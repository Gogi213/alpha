//! Одно WS-соединение: бюджет → connect с RTT → TLS/upgrade → подписка → чтение кадров до закрытия. Один поток ОС на соединение.
//!
//! Горячий путь кадра: возврат из чтения → метка приёма (нс UTC) → счётчики (атомики) → кадр в очередь писателя шарда.
//! Разбор, дубли, формат, диск — не здесь: поток приёма не ждёт ничего, кроме сокета (и места в очереди — это обратное
//! давление, оно считается). Пинг, сторож pong, окно проверки пути, замер Recv-Q — на тиках таймаута чтения (200 мс).
//! Не решает, когда менять путь и переподключаться, — это шард.

use std::io::ErrorKind;
use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use bytes::Bytes;
use serde_json::json;
use tungstenite::{Error as WsError, Message};

use super::budget::Budget;
use super::events::{now_ms, EventLog};
use super::exchange::{Entrance, Exchange, ShardPlan};
use super::fresh::GRACE_S;
use super::metrics::ShardMetrics;
use super::net::{self, Conn};
use super::path::{PathProbe, PathWatch};
use super::ring::{Item, Payload, RingTx};

/// Назначаемое (Судье): замер 12.11 (30 соед. srv→Токио): RTT ≤ 276 мс — лаг p99 ≤ 1,5 с, ≥ 320 мс — p99 ≥ 1,9 с.
pub const CONNECT_RTT_MAX_MS: f64 = 300.0;
pub const PING_PERIOD: Duration = Duration::from_secs(5);
/// Порог «таймаут» страницы здоровья (нет pong), назначаемое.
pub const PING_TIMEOUT: Duration = Duration::from_secs(10);
/// Тик чтения: период, с которым поток просыпается без кадров (пинг, остановка, проверка пути).
pub const READ_TICK: Duration = Duration::from_millis(200);
const WRITE_TIMEOUT: Duration = Duration::from_secs(10);
/// Каждый N-й кадр идёт в выборку лага для сторожа пути; плюс не реже раза в `SAMPLE_EVERY_MS`.
const LAG_SAMPLE: u64 = 32;
const SAMPLE_EVERY: Duration = Duration::from_millis(250);
const RECVQ_EVERY: u64 = 16;
const HANDOFF_EVERY: u64 = 8;

/// Общее состояние шарда для его сессий.
pub struct Shared {
    pub exch: Arc<dyn Exchange>,
    pub plan: ShardPlan,
    pub metrics: Arc<ShardMetrics>,
    pub ring: RingTx,
    pub watch: Mutex<PathWatch>,
    pub events: EventLog,
    pub budget: Arc<Budget>,
    pub stop: Arc<AtomicBool>,
    /// `false` — открытый `ws://` (локальные проверки и синтетический источник).
    pub tls: bool,
    pub port: u16,
    /// Окно проверки нового пути: (пропуск, конец), секунды от начала чтения.
    pub probe_window: (f64, f64),
}

impl Shared {
    pub fn watch(&self) -> MutexGuard<'_, PathWatch> {
        self.watch
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    fn url(&self, host: &str) -> String {
        let u = if self.port == 443 {
            self.exch.url(&self.plan.endpoint, host)
        } else {
            self.exch
                .url(&self.plan.endpoint, &format!("{host}:{}", self.port))
        };
        if self.tls {
            u
        } else {
            u.replacen("wss://", "ws://", 1)
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum Outcome {
    /// Путь отбракован (RTT рукопожатия или окно проверки лага): соединение закрыто до приёма в «живые».
    PathRejected,
    /// Закрыто нами (остановка, замена) или биржей по протоколу.
    Closed,
    /// Сбой сети/протокола: шард переподключит.
    Failed(String),
}

fn ctl(sh: &Shared, seq: u32, what: &'static str, detail: String) {
    let _ = sh.ring.push(Item {
        rx_ns: wall_ns(),
        sender: seq,
        settled: true,
        payload: Payload::Control { what, detail },
    });
}

pub fn wall_ns() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_nanos()).unwrap_or(i64::MAX))
}

fn tcp_json(s: &std::net::TcpStream) -> serde_json::Value {
    match net::tcp_state(s) {
        Some(t) => json!({
            "retransmits": t.retransmits, "backoff": t.backoff, "rto_ms": t.rto_ms, "unacked": t.unacked, "lost": t.lost,
            "retrans": t.retrans, "last_data_recv_ms": t.last_data_recv_ms, "rtt_ms": t.rtt_ms, "total_retrans": t.total_retrans,
        }),
        None => serde_json::Value::Null,
    }
}

/// Сессия до закрытия. `probe` — путь проверяется (RTT и окно лага); `accepted` — флаг «путь принят» для шарда.
pub fn run(
    sh: &Shared,
    seq: u32,
    ent: &Entrance,
    probe: bool,
    accepted: Option<&AtomicBool>,
    cancel: &AtomicBool,
) -> Outcome {
    let Some(waited) = sh.budget.acquire(&sh.stop) else {
        return Outcome::Closed;
    };
    if waited >= Duration::from_secs(1) {
        sh.events.emit(
            "connect_wait",
            json!({"session": seq, "waited_s": (waited.as_secs_f64() * 10.0).round() / 10.0}),
        );
    }
    let (tcp, rtt, ip) = match net::dial(ent, sh.port) {
        Ok(x) => x,
        Err(e) => return Outcome::Failed(format!("connect {}: {e}", ent.host)),
    };
    if probe && rtt > CONNECT_RTT_MAX_MS {
        sh.events.emit(
            "path_rejected_rtt",
            json!({"session": seq, "rtt_ms": rtt.round(), "entrance": ent.host}),
        );
        return Outcome::PathRejected;
    }
    let url = sh.url(&ent.host);
    let (mut ws, _) = match net::ws_connect(tcp, &url, &ent.host, sh.tls) {
        Ok(x) => x,
        Err(e) => return Outcome::Failed(e),
    };
    let tcp_ok = {
        let t = ws.get_ref().tcp();
        t.set_read_timeout(Some(READ_TICK))
            .and(t.set_write_timeout(Some(WRITE_TIMEOUT)))
            .is_ok()
    };
    if !tcp_ok {
        return Outcome::Failed("таймауты сокета".to_string());
    }
    sh.events.emit("connected", json!({"session": seq, "url": url, "rtt_ms": rtt.round(), "ip": ip.to_string(), "entrance": ent.host}));
    sh.metrics.connected.fetch_add(1, Relaxed);
    sh.watch().open(seq);
    ctl(sh, seq, "connected", format!("{seq} {url}"));
    let out = body(sh, &mut ws, seq, probe, accepted, cancel);
    if !matches!(out, Outcome::Failed(_)) || !sh.stop.load(Relaxed) {
        let _ = ws.close(None);
    }
    sh.metrics.connected.fetch_sub(1, Relaxed);
    sh.watch().close(seq);
    ctl(sh, seq, "closed", seq.to_string());
    out
}

fn body(
    sh: &Shared,
    ws: &mut tungstenite::WebSocket<Conn>,
    seq: u32,
    probe: bool,
    accepted: Option<&AtomicBool>,
    cancel: &AtomicBool,
) -> Outcome {
    for msg in sh.exch.subscribe_messages(&sh.plan.streams) {
        if let Err(e) = ws.send(Message::text(msg)) {
            return Outcome::Failed(format!("подписка: {e}"));
        }
        std::thread::sleep(sh.exch.sub_period());
    }
    sh.events.emit(
        "subscribed",
        json!({"session": seq, "streams": sh.plan.streams.len()}),
    );
    ctl(
        sh,
        seq,
        "subscribed",
        format!("{seq} {}", sh.plan.streams.len()),
    );
    if !probe {
        if let Some(a) = accepted {
            a.store(true, Relaxed);
        }
    }
    read_loop(sh, ws, seq, probe, accepted, cancel)
}

#[allow(clippy::too_many_lines)]
fn read_loop(
    sh: &Shared,
    ws: &mut tungstenite::WebSocket<Conn>,
    seq: u32,
    probe: bool,
    accepted: Option<&AtomicBool>,
    cancel: &AtomicBool,
) -> Outcome {
    let m = &sh.metrics;
    let t_loop = Instant::now();
    let settle_at = t_loop + Duration::from_secs_f64(GRACE_S);
    let mut probe =
        probe.then(|| PathProbe::with_window(t_loop, sh.probe_window.0, sh.probe_window.1));
    let mut n: u64 = 0;
    let mut last_ping = t_loop;
    let mut ping_sent: Option<(Instant, [u8; 8])> = None;
    let mut pings_on = true;
    let mut ping_no: u64 = 0;
    let mut last_sample = t_loop;
    loop {
        if cancel.load(Relaxed) || sh.stop.load(Relaxed) {
            return Outcome::Closed;
        }
        let got = ws.read();
        let rx = wall_ns();
        let t_read = Instant::now();
        match got {
            Ok(Message::Text(t)) => {
                if !frame(
                    sh,
                    ws,
                    seq,
                    rx,
                    t_read,
                    settle_at,
                    &mut n,
                    &mut last_sample,
                    &mut probe,
                    Bytes::from(t),
                ) {
                    return Outcome::Failed("писатель шарда ушёл".to_string());
                }
            }
            Ok(Message::Binary(b)) => {
                if !frame(
                    sh,
                    ws,
                    seq,
                    rx,
                    t_read,
                    settle_at,
                    &mut n,
                    &mut last_sample,
                    &mut probe,
                    b,
                ) {
                    return Outcome::Failed("писатель шарда ушёл".to_string());
                }
            }
            Ok(Message::Pong(p)) => {
                if let Some((t0, tag)) = ping_sent {
                    if p.as_ref() == tag.as_slice() {
                        let rtt = t_read.duration_since(t0);
                        m.lists()
                            .pings
                            .push_back((now_ms(), rtt.as_secs_f64() * 1000.0));
                        let _ = sh.ring.push(Item {
                            rx_ns: rx,
                            sender: seq,
                            settled: true,
                            payload: Payload::Ping(i64::try_from(rtt.as_micros()).unwrap_or(0)),
                        });
                        ping_sent = None;
                    }
                }
            }
            Ok(Message::Close(_)) => return Outcome::Closed,
            Ok(_) => {}
            Err(WsError::Io(e))
                if matches!(e.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) => {}
            Err(WsError::ConnectionClosed | WsError::AlreadyClosed) => return Outcome::Closed,
            Err(e) => return Outcome::Failed(format!("чтение: {e}")),
        }
        let now = Instant::now();
        if pings_on {
            match ping_sent {
                None if now.duration_since(last_ping) >= PING_PERIOD => {
                    ping_no += 1;
                    let tag = ping_no.to_be_bytes();
                    if let Err(e) = ws.send(Message::Ping(Bytes::copy_from_slice(&tag))) {
                        return Outcome::Failed(format!("ping: {e}"));
                    }
                    ping_sent = Some((now, tag));
                    last_ping = now;
                }
                Some((t0, _)) if now.duration_since(t0) >= PING_TIMEOUT => {
                    // нет pong: путь испортился — не рвём, а помечаем: шард откроет замену и закроет эту сессию после перекрытия
                    m.lists().timeouts.push_back(now_ms());
                    sh.events.emit("timeout", json!({"what": "ping", "session": seq, "tcp": tcp_json(ws.get_ref().tcp())}));
                    sh.events.emit("ping_degraded", json!({"session": seq}));
                    sh.watch().mark_degraded(seq);
                    pings_on = false;
                    ping_sent = None;
                }
                _ => {}
            }
        }
        if let Some(p) = probe.as_mut() {
            if p.finished(now) {
                let clean = p.clean();
                let (cnt, p50, p99) = p.summary();
                sh.events.emit(if clean { "path_ok" } else { "path_rejected" }, json!({"session": seq, "n": cnt, "p50_ms": p50.map(f64::round), "p99_ms": p99.map(f64::round)}));
                if !clean {
                    return Outcome::PathRejected;
                }
                probe = None;
                if let Some(a) = accepted {
                    a.store(true, Relaxed);
                }
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn frame(
    sh: &Shared,
    ws: &tungstenite::WebSocket<Conn>,
    seq: u32,
    rx: i64,
    t_read: Instant,
    settle_at: Instant,
    n: &mut u64,
    last_sample: &mut Instant,
    probe: &mut Option<PathProbe>,
    data: Bytes,
) -> bool {
    let m = &sh.metrics;
    *n += 1;
    m.frames.fetch_add(1, Relaxed);
    m.bytes
        .fetch_add(u64::try_from(data.len()).unwrap_or(0), Relaxed);
    m.last_frame_ms.store(rx.div_euclid(1_000_000), Relaxed);
    m.last_frame_mono_ms.store(super::shard::mono_ms(), Relaxed);
    if n.is_multiple_of(RECVQ_EVERY) {
        if let Some(q) = net::recv_q(ws.get_ref().tcp()) {
            m.recvq_samples.fetch_add(1, Relaxed);
            m.recvq_max.fetch_max(q, Relaxed);
            if q > 0 {
                m.recvq_behind.fetch_add(1, Relaxed);
            }
        }
    }
    if let Some(p) = probe.as_mut() {
        if p.collecting(t_read) {
            p.add(sh.exch.lag_ms(&data, rx));
        }
    }
    if n.is_multiple_of(LAG_SAMPLE) || t_read.duration_since(*last_sample) >= SAMPLE_EVERY {
        *last_sample = t_read;
        if let Some(lag) = sh.exch.lag_ms(&data, rx) {
            sh.watch().sample(seq, lag);
        }
    }
    let settled = t_read >= settle_at;
    let pushed = sh.ring.push(Item {
        rx_ns: rx,
        sender: seq,
        settled,
        payload: Payload::Frame(data),
    });
    let work_us = u64::try_from(t_read.elapsed().as_micros()).unwrap_or(u64::MAX);
    m.busy_max_us.fetch_max(work_us, Relaxed);
    if n.is_multiple_of(HANDOFF_EVERY) {
        m.handoff_max_us.fetch_max(work_us, Relaxed);
    }
    pushed.is_ok()
}
