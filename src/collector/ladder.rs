//! Лестница нагрузки Binance на Rust: одно соединение, быстрый путь (RTT рукопожатия ≤ 300 мс), окно, n потоков одного вида — тот же метод, что
//! у `tools/collector/ladder.py` (TK-531), чтобы ступени сравнивались ячейка в ячейку: адреса `/ws` (книга) и `/market/ws` (сделки, mark), подписка пачками
//! по 50 с паузой 0,25 с, первые 5 с после подписки не считаются, лаг = приём − E, тишина = секунды окна без кадров, CPU = процесс целиком.
//! Принимает тем же кодом, что коллектор (`net::dial`, `net::ws_connect`, tungstenite, rustls), без очереди и писателя: меряется приём соединения.

// Диагностический инструмент (не горячий путь): счётчики и времена в f64 для печати.
#![allow(
    clippy::cast_precision_loss,
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    clippy::too_many_lines
)]

use std::path::PathBuf;
use std::time::{Duration, Instant};

use clap::Args;
use serde_json::{json, Value};
use tungstenite::{Error as WsError, Message};

use super::events::now_ms;
use super::exchange::{lag_ms, Entrance, Family};
use super::net;
use super::session::{wall_ns, CONNECT_RTT_MAX_MS};

const HOST: &str = "fstream.binance.com";
const SUB_BATCH: usize = 50;
const SUB_PAUSE: Duration = Duration::from_millis(250);
const SKIP_S: u64 = 5;
const DIAL_TRIES: usize = 12;
const READ_TICK: Duration = Duration::from_millis(100);

#[derive(Debug, Args)]
pub struct LadderArgs {
    /// Вид потока: book (`bookTicker`, `/ws`), agg (`aggTrade`, `/market/ws`), mark (`markPrice@1s`, `/market/ws`).
    #[arg(long)]
    pub kind: String,
    /// Потоков на соединение (символов одного вида).
    #[arg(long)]
    pub n: usize,
    /// top — самые активные по обороту, mid — со 150-й позиции.
    #[arg(long, default_value = "top")]
    pub group: String,
    /// Окно, с (после подписки; первые 5 с не считаются).
    #[arg(long, default_value_t = 180)]
    pub dur: u64,
    /// JSON-массив символов по убыванию `quoteVolume24h` (как их ранжирует `ladder.py`).
    #[arg(long)]
    pub symbols_file: PathBuf,
    #[arg(long)]
    pub out: PathBuf,
}

/// `xs[min(n-1, int(q·n))]` отсортированного — как `pct` в `ladder.py`.
fn pct(xs: &[f64], q: f64) -> Option<f64> {
    (!xs.is_empty()).then(|| xs[((xs.len() as f64 * q) as usize).min(xs.len() - 1)])
}

/// CPU процесса (utime + stime), секунды.
fn proc_cpu_s() -> f64 {
    let Ok(stat) = std::fs::read_to_string("/proc/self/stat") else {
        return 0.0;
    };
    let Some(rest) = stat.rfind(')').and_then(|i| stat.get(i + 2..)) else {
        return 0.0;
    };
    let f: Vec<&str> = rest.split_whitespace().collect();
    let at = |i: usize| f.get(i).and_then(|x| x.parse::<f64>().ok()).unwrap_or(0.0);
    (at(11) + at(12)) / 100.0
}

/// Время биржи `E` кадра (мс): число после `"E":`.
fn exchange_time(text: &str) -> Option<i64> {
    let i = text.find("\"E\":")? + 4;
    let digits: String = text
        .get(i..)?
        .chars()
        .take_while(char::is_ascii_digit)
        .collect();
    digits.parse().ok()
}

fn round1(x: f64) -> f64 {
    (x * 10.0).round() / 10.0
}

pub fn run(a: &LadderArgs) -> anyhow::Result<()> {
    let syms: Vec<String> = serde_json::from_str(&std::fs::read_to_string(&a.symbols_file)?)?;
    let start = if a.group == "top" {
        0
    } else {
        150.min(syms.len().saturating_sub(a.n))
    };
    let chosen = syms
        .get(start..(start + a.n).min(syms.len()))
        .unwrap_or(&[]);
    let (path, topic) = match a.kind.as_str() {
        "book" => ("/ws", "@bookTicker"),
        "agg" => ("/market/ws", "@aggTrade"),
        "mark" => ("/market/ws", "@markPrice@1s"),
        other => anyhow::bail!("вид {other}: book|agg|mark"),
    };
    let url = format!("wss://{HOST}{path}");
    let streams: Vec<String> = chosen
        .iter()
        .map(|s| format!("{}{topic}", s.to_lowercase()))
        .collect();
    let mut res = json!({
        "exch": "binance", "impl": "rust", "kind": a.kind, "n": a.n, "group": a.group, "dur": a.dur, "t_start": now_ms() as f64 / 1000.0,
        "streams": streams.len(), "pool": syms.len(), "url": url, "disconnects": 0, "events": [], "ctl": [],
    });
    let (cpu0, w0) = (proc_cpu_s(), Instant::now());
    let ent = Entrance::new(HOST, Family::Any);
    let mut fast = None;
    let mut rtt = 0.0;
    let mut dial_errs: Vec<String> = Vec::new();
    for _ in 0..DIAL_TRIES {
        match net::dial(&ent, 443) {
            Ok((tcp, r, ip)) => {
                rtt = r;
                if r <= CONNECT_RTT_MAX_MS {
                    fast = Some((tcp, ip));
                    break;
                }
            }
            Err(e) => dial_errs.push(format!("dial: {e}")),
        }
    }
    res["rtt_ms"] = json!(rtt.round());
    let Some((tcp, ip)) = fast else {
        res["error"] = json!("no_fast_path");
        res["events"] = json!(dial_errs);
        std::fs::write(&a.out, res.to_string())?;
        return Ok(());
    };
    res["ip"] = json!(ip.to_string());
    let (mut lags, mut cnt, mut nbytes, mut disconnects) = (Vec::<f64>::new(), 0u64, 0usize, 0u32);
    let mut per_sec = vec![0u64; usize::try_from(a.dur + SKIP_S + 2)?];
    let mut ctl: Vec<String> = Vec::new();
    let mut events: Vec<String> = dial_errs;
    let mut tcp_json = Value::Null;
    match net::ws_connect(tcp, &url, HOST, true) {
        Err(e) => {
            disconnects += 1;
            events.push(e);
        }
        Ok((mut ws, _)) => {
            ws.get_ref().tcp().set_read_timeout(Some(READ_TICK))?;
            let mut sub_err = None;
            for (i, chunk) in streams.chunks(SUB_BATCH).enumerate() {
                let msg = json!({"method": "SUBSCRIBE", "params": chunk, "id": i * SUB_BATCH + 1});
                if let Err(e) = ws.send(Message::text(msg.to_string())) {
                    sub_err = Some(format!("подписка: {e}"));
                    break;
                }
                std::thread::sleep(SUB_PAUSE);
            }
            let t0 = Instant::now();
            res["t_sub_done_s"] = json!(round1((t0 - w0).as_secs_f64()));
            let t_end = t0 + Duration::from_secs(a.dur + SKIP_S);
            if let Some(e) = sub_err {
                disconnects += 1;
                events.push(e);
            }
            while disconnects == 0 && Instant::now() < t_end {
                match ws.read() {
                    Ok(Message::Text(t)) => {
                        let (rx, mono) = (wall_ns(), Instant::now());
                        let text: &str = t.as_ref();
                        if text.starts_with("{\"result\"")
                            || text.starts_with("{\"error\"")
                            || text.starts_with("{\"code\"")
                        {
                            if ctl.len() < 20 {
                                ctl.push(text.chars().take(200).collect());
                            }
                            continue;
                        }
                        let el = mono.duration_since(t0).as_secs();
                        if el < SKIP_S {
                            continue;
                        }
                        nbytes += text.len();
                        cnt += 1;
                        if let Some(c) = usize::try_from(el).ok().and_then(|i| per_sec.get_mut(i)) {
                            *c += 1;
                        }
                        if let Some(e) = exchange_time(text) {
                            lags.push(lag_ms(rx, e));
                        }
                    }
                    Ok(Message::Close(_))
                    | Err(WsError::ConnectionClosed | WsError::AlreadyClosed) => {
                        disconnects += 1;
                        events.push("closed".to_string());
                    }
                    Ok(_) => {}
                    Err(WsError::Io(e))
                        if matches!(
                            e.kind(),
                            std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                        ) => {}
                    Err(e) => {
                        disconnects += 1;
                        events.push(format!("{e}").chars().take(200).collect());
                    }
                }
            }
            tcp_json = net::tcp_state(ws.get_ref().tcp()).map_or(Value::Null, |t| {
                json!({"retransmits": t.retransmits, "backoff": t.backoff, "rto_ms": t.rto_ms, "unacked": t.unacked, "lost": t.lost,
                       "retrans": t.retrans, "last_data_recv_ms": t.last_data_recv_ms, "rtt_ms": t.rtt_ms, "total_retrans": t.total_retrans})
            });
            let _ = ws.close(None);
        }
    }
    let dur = a.dur as f64;
    lags.sort_by(f64::total_cmp);
    let silent = (SKIP_S..a.dur + SKIP_S)
        .filter(|s| {
            per_sec
                .get(usize::try_from(*s).unwrap_or(0))
                .is_none_or(|c| *c == 0)
        })
        .count();
    res["disconnects"] = json!(disconnects);
    res["events"] = json!(events);
    res["ctl"] = json!(ctl);
    res["tcp"] = tcp_json;
    res["msgs"] = json!(cnt);
    res["kb"] = json!((nbytes as f64 / 1024.0).round());
    res["msg_s"] = json!(round1(cnt as f64 / dur));
    res["kb_s"] = json!(round1(nbytes as f64 / 1024.0 / dur));
    res["lag_p50"] = json!(pct(&lags, 0.5));
    res["lag_p90"] = json!(pct(&lags, 0.9));
    res["lag_p99"] = json!(pct(&lags, 0.99));
    res["lag_max"] = json!(lags.last());
    res["silent_s"] = json!(silent);
    res["peak_msg_s"] = json!(per_sec.iter().copied().max().unwrap_or(0));
    res["cpu_s"] = json!(((proc_cpu_s() - cpu0) * 100.0).round() / 100.0);
    res["t_end"] = json!(now_ms() as f64 / 1000.0);
    std::fs::write(&a.out, res.to_string())?;
    Ok(())
}
