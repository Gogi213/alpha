//! Одно WS-соединение: бюджет → connect с RTT → TLS/upgrade → подписка → чтение кадров до закрытия. Один поток ОС на соединение.
//!
//! Горячий путь кадра: возврат из чтения → метка приёма (нс UTC) → счётчики (атомики) → кадр в очередь писателя шарда.
//! Разбор, дубли, формат, диск — не здесь: поток приёма не ждёт ничего, кроме сокета (и места в очереди — это обратное
//! давление, оно считается). Пинг, сторож pong, окно проверки пути, замер Recv-Q — на тиках таймаута чтения (200 мс).
//! Не решает, когда менять путь и переподключаться, — это шард.

use std::collections::{HashSet, VecDeque};
use std::io::ErrorKind;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering::Relaxed};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use bytes::Bytes;
use serde_json::json;
use tungstenite::{Error as WsError, Message, WebSocket};

use super::budget::{Budget, Cap};
use super::events::{now_ms, EventLog};
use super::exchange::{Entrance, Exchange, ShardPlan};
use super::fresh::GRACE_S;
use super::gzip::Gunzip;
use super::metrics::ShardMetrics;
use super::net::{self, Conn};
use super::path::{window_verdict, PathProbe, PathWatch, Window};
use super::reactor::ReactorHandle;
use super::ring::{Item, Payload, RingTx};
use super::storm;

/// Абсолютный порог отбраковки пути по RTT рукопожатия, назначаемое (TK-527, замер: два маршрута, выбираются портом источника;
/// быстрый ≤ ~293 мс — чисто, медленный ≥ ~347 мс — лаг 1–14 с и ретрансмиты; у Python такой же порог 300 мс → 0 поздних).
/// Путь ≥ порога отбраковывается новым подключением (другой порт источника), пока в бюджете есть запас (`Budget::can_reject`).
pub const CONNECT_RTT_MAX_MS: f64 = 300.0;
pub const PING_PERIOD: Duration = Duration::from_secs(5);
/// Pong позже этого срока — событие `slow_pong` (диагностика стоянок пути короче `PING_TIMEOUT`), назначаемое.
const SLOW_PONG: Duration = Duration::from_secs(1);
/// Порог «таймаут» страницы здоровья (нет pong), назначаемое.
pub const PING_TIMEOUT: Duration = Duration::from_secs(10);
/// Линия молчит столько, пока и весь шард в паузе (`gap`), — она испорчена, не дожидаясь `PING_TIMEOUT` (TK-582: обнаружение 10–14 с → 4–7 с), назначаемое.
pub const SILENT_LINE: Duration = Duration::from_secs(4);
/// ...и наш пинг без pong уже столько: живая линия отвечает за RTT (≈ 0,25 с), тихий рынок сам линию не портит, назначаемое.
pub const SILENT_PONG: Duration = Duration::from_secs(2);
/// Данные свежие, если последний кадр линии моложе этого срока; при `--no-pong-only-degrade` нет pong при свежих данных линию не портит (журнал `timeout` и счётчики health остаются).
pub const FRESH_DATA: Duration = Duration::from_secs(2);
/// Тик чтения: период, с которым поток просыпается без кадров (пинг, остановка, проверка пути).
pub const READ_TICK: Duration = Duration::from_millis(200);
const WRITE_TIMEOUT: Duration = Duration::from_secs(10);
/// Каждый N-й кадр (с первого) идёт в выборку лага для сторожа пути — как `LAG_SAMPLE` в Python `wscore/session.py`.
const LAG_SAMPLE: u64 = 64;
const RECVQ_EVERY: u64 = 16;
const BUSY_EVERY: u64 = 4;
/// Сколько событий `bad_frame` писать на сессию (кадр, не разжавшийся как gzip, идёт дальше «как пришёл»; поток таких кадров журнал не затопит).
const BAD_FRAME_EVENTS: u32 = 5;

/// Живой набор потоков шарда: меняется без перезапуска (пересмотр пула, `livepool`). Версия растёт с каждым изменением; сессия сверяет её на тике и
/// досылает разницу (подписка / отписка) на своём соединении, новая сессия подписывается на актуальный набор.
pub struct StreamSet {
    inner: Mutex<Vec<String>>,
    version: AtomicU64,
}

impl StreamSet {
    pub fn new(streams: Vec<String>) -> Self {
        Self {
            inner: Mutex::new(streams),
            version: AtomicU64::new(0),
        }
    }

    pub fn version(&self) -> u64 {
        self.version.load(Relaxed)
    }

    /// Версия и набор вместе (под одним замком: версия описывает ровно этот набор).
    pub fn snapshot(&self) -> (u64, Vec<String>) {
        let g = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        (self.version.load(Relaxed), g.clone())
    }

    pub fn len(&self) -> usize {
        self.inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Добавить `add` (уже имеющиеся не дублируются) и снять `remove`; версия растёт, если набор изменился.
    pub fn apply(&self, add: &[String], remove: &[String]) -> bool {
        let mut g = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let before = g.len();
        let gone: HashSet<&str> = remove.iter().map(String::as_str).collect();
        g.retain(|s| !gone.contains(s.as_str()));
        let mut changed = g.len() != before;
        let have: HashSet<String> = g.iter().cloned().collect();
        for a in add {
            if !have.contains(a) {
                g.push(a.clone());
                changed = true;
            }
        }
        if changed {
            self.version.fetch_add(1, Relaxed);
        }
        changed
    }
}

/// Общее состояние шарда для его сессий.
pub struct Shared {
    pub exch: Arc<dyn Exchange>,
    pub plan: ShardPlan,
    /// Актуальный набор потоков шарда (`plan.streams` — только начальный).
    pub streams: Arc<StreamSet>,
    /// Потолки на шард: замены пути (`shard::REPLACE_CAP`) и отбракованные пути (`shard::REJECT_CAP`) за скользящее окно.
    pub replace_cap: Cap,
    pub reject_cap: Cap,
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
    /// Период пинга и срок ожидания pong.
    pub ping: (Duration, Duration),
    /// `false` (`--no-pong-only-degrade`): нет pong на НАШ пинг при свежих данных (последний кадр моложе `FRESH_DATA`, 2 с) не портит линию (событие `timeout` пишется, счётчики health растут, пинг
    /// продолжается); портят линию молчание (нет ни pong, ни данных за срок), данные старше `FRESH_DATA` и лаг доставки. Наш пинг — не требование биржи, а наш способ следить за путём.
    pub pong_only_degrades: bool,
    /// `--no-ping`: наш протокольный пинг не шлётся вовсе (опыт «не наш ли пинг провоцирует закрытия»; без пинга сторож пути не видит нет-pong, только лаг и обрывы чтения).
    pub no_ping: bool,
    /// Порог отбраковки пути по RTT рукопожатия, мс (`CONNECT_RTT_MAX_MS`; в проверках занижается).
    pub rtt_max_ms: f64,
    /// Шард без соединения проверяет путь, как Python: отбраковка по RTT и окно лага до приёма (по умолчанию — принимает сразу, даже медленный).
    pub cold_probe: bool,
    /// Реактор процесса: готовые соединения читает он (один поток на все сокеты), а не поток сессии; `None` — поток на соединение.
    pub reactor: Option<ReactorHandle>,
    /// Режим «стоянка всей биржи» процесса (`None` — не оценивается) и потолок «одна пробная замена на шард» в нём.
    pub storm: Option<Arc<super::storm::Storm>>,
    pub storm_probe: Cap,
    /// Проверка паузы шарда сделок по REST (только шарды сделок биржи с такой проверкой); `None` — пауза не проверяется.
    pub pause_check: Option<super::pausecheck::PauseCheck>,
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
        lane: 0,
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
            "last_data_sent_ms": t.last_data_sent_ms, "last_ack_recv_ms": t.last_ack_recv_ms, "recvq": net::recv_q(s),
        }),
        None => serde_json::Value::Null,
    }
}

/// Сессия до закрытия. `probe` — путь проверяется (RTT и окно лага); `accepted` — флаг «путь принят» для шарда.
pub fn run(
    sh: &Arc<Shared>,
    seq: u32,
    ent: &Entrance,
    probe: bool,
    accepted: Option<&Arc<AtomicBool>>,
    cancel: &Arc<AtomicBool>,
    lane: u8,
) -> Outcome {
    // шард без соединения берёт место в бюджете раньше второй линии и замен покрытых шардов
    // и шард, у которого соединения открыты, но ни одно не несёт поток (все линии испорчены): ему путь принимается как есть, как шарду без соединения (TK-582)
    let uncovered = || sh.metrics.connected.load(Relaxed) <= 0 || storm::dark(&sh.metrics);
    let Some(waited) = sh.budget.acquire(&[&sh.stop, cancel], &uncovered) else {
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
    // шард без соединения: путь принимается как есть сразу — данные пишутся, разрыва нет; медленный путь потом заменяется проверенным (`path_upgrade`)
    // тёмный шард принимает первое соединение и при `cold_probe`: проверка RTT с отбраковкой и мгновенным повтором (`sleep_stop_unless_dark` возвращается сразу) — цикл подключений без потолка (проверка 17.11, п. 2)
    let lone = probe && (storm::dark(&sh.metrics) || (!sh.cold_probe && uncovered()));
    let slow_lone = lone && rtt > sh.rtt_max_ms;
    if slow_lone {
        sh.events.emit(
            "path_slow_accepted",
            json!({"session": seq, "rtt_ms": rtt.round(), "limit_ms": sh.rtt_max_ms, "entrance": ent.host, "why": "uncovered"}),
        );
    }
    let probe = probe && !lone;
    if probe && rtt > sh.rtt_max_ms {
        if sh.budget.can_reject(Instant::now()) {
            sh.events.emit(
                "path_rejected_rtt",
                json!({"session": seq, "rtt_ms": rtt.round(), "limit_ms": sh.rtt_max_ms, "entrance": ent.host}),
            );
            return Outcome::PathRejected;
        }
        // бюджет выбран до запаса (весь маршрут медленный): принимаем, сторож лага и замена при деградации остаются
        sh.events.emit(
            "path_slow_accepted",
            json!({"session": seq, "rtt_ms": rtt.round(), "limit_ms": sh.rtt_max_ms, "entrance": ent.host}),
        );
    }
    let url = sh.url(&ent.host);
    let (ws, resp) = match net::ws_connect(tcp, &url, &ent.host, sh.tls, sh.exch.ws_headers()) {
        Ok(x) => x,
        Err(e) => return Outcome::Failed(e),
    };
    let hs = net::handshake_info(&ws, &resp);
    let tcp_ok = {
        let t = ws.get_ref().tcp();
        t.set_read_timeout(Some(READ_TICK))
            .and(t.set_write_timeout(Some(WRITE_TIMEOUT)))
            .is_ok()
    };
    if !tcp_ok {
        return Outcome::Failed("таймауты сокета".to_string());
    }
    sh.events.emit("connected", json!({"session": seq, "url": url, "rtt_ms": rtt.round(), "ip": ip.to_string(), "entrance": ent.host, "hs": hs}));
    sh.metrics.connected.fetch_add(1, Relaxed);
    {
        let mut w = sh.watch();
        w.open(seq);
        if slow_lone {
            w.mark_slow(seq);
        }
    }
    ctl(sh, seq, "connected", format!("{seq} {url}"));
    let out = body(sh, ws, seq, probe, accepted, cancel, lane);
    sh.metrics.connected.fetch_sub(1, Relaxed);
    sh.watch().close(seq);
    ctl(sh, seq, "closed", seq.to_string());
    out
}

fn body(
    sh: &Arc<Shared>,
    ws: WebSocket<Conn>,
    seq: u32,
    probe: bool,
    accepted: Option<&Arc<AtomicBool>>,
    cancel: &Arc<AtomicBool>,
    lane: u8,
) -> Outcome {
    let mut ws = ws;
    if let Some(r) = sh.reactor.as_ref().filter(|r| r.paces_subscription()) {
        // подписка из реактора: см. `Rx::begin_sub`; событие `subscribed` и признак «принят» — по её окончании
        let mut rx = Rx::new(
            sh,
            probe,
            accepted.map(Arc::clone),
            lane,
            sh.streams.snapshot(),
        );
        rx.begin_sub(sh, Instant::now());
        return r.run_conn(sh, ws, seq, rx, Arc::clone(cancel));
    }
    let snap = sh.streams.snapshot();
    for msg in sh.exch.subscribe_messages(&snap.1) {
        if let Err(e) = ws.send(Message::text(msg)) {
            return Outcome::Failed(format!("подписка: {e}"));
        }
        std::thread::sleep(sh.exch.sub_period());
    }
    sh.events.emit(
        "subscribed",
        json!({"session": seq, "streams": snap.1.len()}),
    );
    ctl(sh, seq, "subscribed", format!("{seq} {}", snap.1.len()));
    if !probe {
        if let Some(a) = accepted {
            a.store(true, Relaxed);
        }
    }
    let rx = Rx::new(sh, probe, accepted.map(Arc::clone), lane, snap);
    match sh.reactor.as_ref() {
        Some(r) => r.run_conn(sh, ws, seq, rx, Arc::clone(cancel)),
        None => {
            let out = read_loop(sh, &mut ws, seq, rx, cancel);
            if !matches!(out, Outcome::Failed(_)) || !sh.stop.load(Relaxed) {
                let _ = ws.close(None);
            }
            out
        }
    }
}

/// Кадры принятых (не проверяемых) соединений шарда с начала процесса.
fn trusted_frames(m: &ShardMetrics) -> u64 {
    m.frames
        .load(Relaxed)
        .saturating_sub(m.frames_probing.load(Relaxed))
}

/// Состояние чтения одной сессии: общее для потока приёма (`read_loop`) и реактора. Кадры, метки, пинг/pong, окно проверки пути — те же правила.
pub struct Rx {
    lane: u8,
    settle_at: Instant,
    probe: Option<PathProbe>,
    accepted: Option<Arc<AtomicBool>>,
    n: u64,
    /// Путь сессии ещё проверяется (от создания до приёма): её кадры считаются в `ShardMetrics::frames_probing`. `probe_open` — окно замера лага открыто (после пропуска снапшота подписки),
    /// `probe_own` — кадры сессии с начала окна, `trusted_base` — кадры принятых соединений шарда на начало окна: по ним решается «единственный источник» (`path::sole_source`).
    probing: bool,
    probe_open: bool,
    probe_own: u64,
    trusted_base: u64,
    last_ping: Instant,
    ping_sent: Option<(Instant, [u8; 8])>,
    /// Диагностика пинга: сколько мкс занял `ws.send(Ping)` и ушёл ли он в сокет сразу (`false`) или остался в очереди записи (`true`).
    ping_send_us: u64,
    ping_blocked: bool,
    pings_on: bool,
    ping_no: u64,
    /// Диагностика закрытий (collector-perf, 14.11): время жизни сессии, сердцебиения сервера и наши ответы, наши пинги и pong — попадают в `diag` событий `ws_close`/`ws_end`/`timeout`/`slow_pong`.
    born: Instant,
    srv_pings: u64,
    last_srv_ping: Option<Instant>,
    srv_ping_rx: i64,
    pending_srv_pong: bool,
    pongs_sent: u64,
    last_pong_sent: Option<Instant>,
    pong_lat_max_us: u64,
    pong_late: u64,
    our_pongs: u64,
    last_our_pong: Option<Instant>,
    last_frame: Instant,
    gunzip: Option<Gunzip>,
    bad_gzip: u32,
    /// Запись из `ws.send` не ушла целиком (неблокирующий сокет): нужен `flush`, когда сокет станет записываемым.
    pub need_flush: bool,
    /// Подписка из реактора: сообщения уходят с паузой биржи прямо в его цикле, а соединение читается с первого кадра (иначе кадры первых тем стояли бы в очереди ядра
    /// всю подписку — у BingX 3,7 с — и приходили к писателю с ядерной меткой давностью секунд).
    sub: Option<SubPlan>,
    /// Окно проверки пути (RTT-лаг) начинается с конца подписки, как раньше: проверка пути заводится в `finish_sub`.
    probe_wanted: bool,
    /// Набор потоков, на который сессия подписана (или подписывается), и его версия в `Shared::streams`: разница с живым набором шарда досылается на тике.
    applied_ver: u64,
    applied: Vec<String>,
}

/// Недосланная подписка сессии: сообщения, пауза между ними, срок следующего, число тем.
struct SubPlan {
    msgs: VecDeque<String>,
    period: Duration,
    next: Instant,
    streams: usize,
    /// Дозапись разницы набора на живом соединении (подписано, снято): конец — событие `pool_delta`, окно проверки пути и приём не трогаются.
    delta: Option<(usize, usize)>,
}

impl Rx {
    fn new(
        sh: &Shared,
        probe: bool,
        accepted: Option<Arc<AtomicBool>>,
        lane: u8,
        snap: (u64, Vec<String>),
    ) -> Self {
        let t_loop = Instant::now();
        Self {
            lane,
            settle_at: t_loop + Duration::from_secs_f64(GRACE_S),
            probe: probe
                .then(|| PathProbe::with_window(t_loop, sh.probe_window.0, sh.probe_window.1)),
            accepted,
            n: 0,
            probing: probe,
            probe_open: false,
            probe_own: 0,
            trusted_base: 0,
            last_ping: t_loop,
            ping_sent: None,
            ping_send_us: 0,
            ping_blocked: false,
            pings_on: !sh.no_ping,
            ping_no: 0,
            born: t_loop,
            srv_pings: 0,
            last_srv_ping: None,
            srv_ping_rx: 0,
            pending_srv_pong: false,
            pongs_sent: 0,
            last_pong_sent: None,
            pong_lat_max_us: 0,
            pong_late: 0,
            our_pongs: 0,
            last_our_pong: None,
            last_frame: t_loop,
            gunzip: sh.exch.gzip_frames().then(Gunzip::new),
            bad_gzip: 0,
            need_flush: false,
            sub: None,
            probe_wanted: false,
            applied_ver: snap.0,
            applied: snap.1,
        }
    }

    /// Подписку ведёт реактор (`tick`): сообщения по одному с паузой биржи; проверка пути и «принят» — после последнего.
    fn begin_sub(&mut self, sh: &Shared, now: Instant) {
        let msgs: VecDeque<String> = sh.exch.subscribe_messages(&self.applied).into();
        self.probe_wanted = self.probe.take().is_some();
        self.sub = Some(SubPlan {
            msgs,
            period: sh.exch.sub_period(),
            next: now,
            streams: self.applied.len(),
            delta: None,
        });
    }

    /// Набор потоков шарда изменился (пересмотр пула): отписка от снятых и подписка на новые тем же шагом биржи, на этом соединении.
    fn begin_delta(&mut self, sh: &Shared, now: Instant) {
        let (ver, cur) = sh.streams.snapshot();
        let (add, rem) = {
            let have: HashSet<&str> = self.applied.iter().map(String::as_str).collect();
            let want: HashSet<&str> = cur.iter().map(String::as_str).collect();
            let add: Vec<String> = cur
                .iter()
                .filter(|s| !have.contains(s.as_str()))
                .cloned()
                .collect();
            let rem: Vec<String> = self
                .applied
                .iter()
                .filter(|s| !want.contains(s.as_str()))
                .cloned()
                .collect();
            (add, rem)
        };
        self.applied = cur;
        self.applied_ver = ver;
        let mut msgs: VecDeque<String> = sh.exch.unsubscribe_messages(&rem).into();
        msgs.extend(sh.exch.subscribe_messages(&add));
        if msgs.is_empty() {
            return;
        }
        self.sub = Some(SubPlan {
            msgs,
            period: sh.exch.sub_period(),
            next: now,
            streams: add.len(),
            delta: Some((add.len(), rem.len())),
        });
    }

    /// Срок следующего сообщения подписки (`None` — подписки нет или она кончилась): реактор просыпается к нему.
    pub fn sub_due(&self) -> Option<Instant> {
        self.sub.as_ref().map(|s| s.next)
    }

    /// Следующее сообщение подписки, если срок пришёл; после последнего — событие `subscribed`, часы окна проверки пути и признак «принят».
    fn pump_sub(
        &mut self,
        sh: &Shared,
        ws: &mut WebSocket<Conn>,
        seq: u32,
        now: Instant,
    ) -> Option<Outcome> {
        let sp = self.sub.as_mut()?;
        if now < sp.next {
            return None;
        }
        if let Some(msg) = sp.msgs.pop_front() {
            sp.next = now + sp.period;
            if let Err(e) = self.send(ws, Message::text(msg)) {
                return Some(Outcome::Failed(format!("подписка: {e}")));
            }
        }
        let sp = self.sub.as_ref()?;
        if sp.msgs.is_empty() {
            let streams = sp.streams;
            if let Some((added, removed)) = sp.delta {
                self.sub = None;
                sh.events.emit(
                    "pool_delta",
                    json!({"session": seq, "added": added, "removed": removed, "streams": self.applied.len()}),
                );
                return None;
            }
            self.sub = None;
            sh.events
                .emit("subscribed", json!({"session": seq, "streams": streams}));
            ctl(sh, seq, "subscribed", format!("{seq} {streams}"));
            let t = Instant::now();
            self.settle_at = t + Duration::from_secs_f64(GRACE_S);
            self.last_ping = t;
            self.last_frame = t;
            if self.probe_wanted {
                self.probe_open = false;
                self.probe_own = 0;
                self.probe = Some(PathProbe::with_window(
                    t,
                    sh.probe_window.0,
                    sh.probe_window.1,
                ));
            } else if let Some(a) = self.accepted.as_ref() {
                a.store(true, Relaxed);
            }
        }
        None
    }

    /// Состояние сессии для событий закрытия: возраст, молчание в обе стороны, сердцебиения сервера и наши ответы, наши пинги, недосланная запись.
    pub fn diag_json(&self) -> serde_json::Value {
        let now = Instant::now();
        let ms = |t: Option<Instant>| {
            t.map(|t| {
                u64::try_from(now.saturating_duration_since(t).as_millis()).unwrap_or(u64::MAX)
            })
        };
        json!({
            "age_s": now.saturating_duration_since(self.born).as_secs(),
            "since_frame_ms": ms(Some(self.last_frame)),
            "srv_pings": self.srv_pings, "since_srv_ping_ms": ms(self.last_srv_ping),
            "pongs_sent": self.pongs_sent, "since_pong_sent_ms": ms(self.last_pong_sent), "pong_lat_max_us": self.pong_lat_max_us, "pong_late": self.pong_late,
            "our_pings": self.ping_no, "since_our_ping_ms": ms(Some(self.last_ping)), "our_pongs": self.our_pongs, "since_our_pong_ms": ms(self.last_our_pong),
            "need_flush": self.need_flush,
        })
    }

    /// Задержка ответа на сердцебиение сервера: от ядерной метки приёма запроса до записи ответа, мкс; позднее секунды — счётчик `pong_late`.
    fn note_reply(&mut self, rx: i64) {
        let lat = u64::try_from((wall_ns() - rx).max(0) / 1000).unwrap_or(0);
        self.pongs_sent += 1;
        self.last_pong_sent = Some(Instant::now());
        self.pong_lat_max_us = self.pong_lat_max_us.max(lat);
        if lat >= 1_000_000 {
            self.pong_late += 1;
        }
    }

    /// Отправка в сокет: на неблокирующем сокете «не влезло» (`WouldBlock`) — запись в очереди rustls/tungstenite, дослать `flush`.
    fn send(&mut self, ws: &mut WebSocket<Conn>, msg: Message) -> Result<(), WsError> {
        match ws.send(msg) {
            Err(WsError::Io(e)) if e.kind() == ErrorKind::WouldBlock => {
                self.need_flush = true;
                Ok(())
            }
            r => r,
        }
    }

    /// Дослать недосланное (реактор: сокет стал записываемым или очередной такт). `Err` — сбой записи.
    pub fn flush(&mut self, ws: &mut WebSocket<Conn>) -> Result<(), String> {
        if !self.need_flush {
            return Ok(());
        }
        match ws.flush() {
            Ok(()) => {
                self.need_flush = false;
                Ok(())
            }
            Err(WsError::Io(e)) if e.kind() == ErrorKind::WouldBlock => Ok(()),
            Err(e) => Err(format!("запись: {e}")),
        }
    }

    /// Одно сообщение WS после чтения: `rx` — приём (нс UTC), `t_read` — монотонные часы чтения. `Some` — сессия кончилась.
    pub fn message(
        &mut self,
        sh: &Shared,
        ws: &mut WebSocket<Conn>,
        seq: u32,
        got: Message,
        rx: i64,
        t_read: Instant,
    ) -> Option<Outcome> {
        let m = &sh.metrics;
        if matches!(got, Message::Text(_) | Message::Binary(_)) {
            self.last_frame = t_read;
        }
        match got {
            Message::Text(t) => self.deliver(sh, ws, seq, rx, t_read, Bytes::from(t)),
            Message::Binary(b) => {
                let data = unpack(sh, seq, &mut self.gunzip, &mut self.bad_gzip, b);
                self.deliver(sh, ws, seq, rx, t_read, data)
            }
            Message::Ping(_) => {
                // протокольный ping сервера: pong отправит tungstenite при следующем чтении; задержку берём в `tick` (после дочитывания и досылки)
                self.srv_pings += 1;
                self.last_srv_ping = Some(t_read);
                self.srv_ping_rx = rx;
                self.pending_srv_pong = true;
                None
            }
            Message::Pong(p) => {
                self.our_pongs += 1;
                self.last_our_pong = Some(t_read);
                if let Some((t0, tag)) = self.ping_sent {
                    if p.as_ref() == tag.as_slice() {
                        let rtt = t_read.duration_since(t0);
                        if rtt >= SLOW_PONG {
                            // стоянка пути короче порога `timeout` (10 с): pong вернулся поздно — какая сессия (линия) и что говорит сокет, пока видно по событию;
                            // разбор по стадиям: `send_us`/`send_blocked` — запись пинга на нашей стороне, `k2r_us` — ожидание pong в нашем ядре и очереди чтения (ядерная метка → чтение);
                            // остаток `rtt_ms` — сеть и биржа
                            sh.events.emit("slow_pong", json!({"session": seq, "rtt_ms": rtt.as_millis(), "send_us": self.ping_send_us, "send_blocked": self.ping_blocked,
                                "k2r_us": (wall_ns() - rx).max(0) / 1000, "tcp": tcp_json(ws.get_ref().tcp()), "diag": self.diag_json()}));
                        }
                        m.lists()
                            .pings
                            .push_back((now_ms(), rtt.as_secs_f64() * 1000.0));
                        let _ = sh.ring.push(Item {
                            rx_ns: rx,
                            sender: seq,
                            lane: self.lane,
                            settled: true,
                            payload: Payload::Ping(i64::try_from(rtt.as_micros()).unwrap_or(0)),
                        });
                        self.ping_sent = None;
                    }
                }
                None
            }
            Message::Close(cf) => {
                // кто и с каким кодом закрыл (1000 штатно, 1001 уходит, 1006 обрыв, 1008/1011 …): BingX закрывает обе линии подряд — причина должна быть в журнале
                sh.events.emit("ws_close", json!({"session": seq, "by": "peer", "code": cf.as_ref().map(|c| u16::from(c.code)), "reason": cf.as_ref().map(|c| c.reason.to_string()),
                    "tcp": tcp_json(ws.get_ref().tcp()), "diag": self.diag_json()}));
                Some(Outcome::Closed)
            }
            _ => None,
        }
    }

    /// Конец чтения не по таймауту: журнал `ws_end` (как закончилось: FIN без закрывающего рукопожатия, RST, другая ошибка) вместе с состоянием сокета.
    pub fn report_end(&self, sh: &Shared, ws: &WebSocket<Conn>, seq: u32, e: &WsError) {
        let how = match e {
            WsError::ConnectionClosed | WsError::AlreadyClosed => {
                "closed_after_handshake".to_string()
            }
            WsError::Protocol(tungstenite::error::ProtocolError::ResetWithoutClosingHandshake) => {
                "fin_without_close_frame".to_string()
            }
            WsError::Io(io) if matches!(io.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) => {
                return
            }
            WsError::Io(io) => format!("io:{:?}", io.kind()),
            _ => "error".to_string(),
        };
        sh.events.emit("ws_end", json!({"session": seq, "how": how, "detail": e.to_string(), "tcp": tcp_json(ws.get_ref().tcp()), "diag": self.diag_json()}));
    }

    /// Ошибка чтения: `None` — не ошибка (нет данных на этот такт), иначе итог сессии.
    pub fn read_error(e: &WsError) -> Option<Outcome> {
        match e {
            WsError::Io(e) if matches!(e.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) => {
                None
            }
            WsError::ConnectionClosed | WsError::AlreadyClosed => Some(Outcome::Closed),
            e => Some(Outcome::Failed(format!("чтение: {e}"))),
        }
    }

    /// Тик: пинг, сторож pong, окно проверки пути. `had_frame` — часы `now` уже сняты на чтении кадра. `Some` — сессия кончилась.
    pub fn tick(
        &mut self,
        sh: &Shared,
        ws: &mut WebSocket<Conn>,
        seq: u32,
        now: Instant,
    ) -> Option<Outcome> {
        let m = &sh.metrics;
        if self.pending_srv_pong {
            self.pending_srv_pong = false;
            self.note_reply(self.srv_ping_rx);
        }
        if self.sub.is_none() && sh.streams.version() != self.applied_ver {
            self.begin_delta(sh, now);
        }
        if self.sub.is_some() {
            if let Some(out) = self.pump_sub(sh, ws, seq, now) {
                return Some(out);
            }
        }
        if self.pings_on
            && self.sub.is_none()
            && sh.metrics.in_gap.load(Relaxed)
            && now.duration_since(self.last_frame) >= SILENT_LINE
            && self
                .ping_sent
                .is_some_and(|(t0, _)| now.duration_since(t0) >= SILENT_PONG)
        {
            sh.events.emit("ping_degraded", json!({"session": seq, "why": "silent_in_gap", "since_frame_ms": u64::try_from(now.duration_since(self.last_frame).as_millis()).unwrap_or(u64::MAX)}));
            sh.watch().mark_degraded(seq);
            self.pings_on = false;
            self.ping_sent = None;
        }
        if self.pings_on && self.sub.is_none() {
            match self.ping_sent {
                None if now.duration_since(self.last_ping) >= sh.ping.0 => {
                    self.ping_no += 1;
                    let tag = self.ping_no.to_be_bytes();
                    let t_send = Instant::now();
                    let was_blocked = self.need_flush;
                    if let Err(e) = self.send(ws, Message::Ping(Bytes::copy_from_slice(&tag))) {
                        return Some(Outcome::Failed(format!("ping: {e}")));
                    }
                    self.ping_send_us =
                        u64::try_from(t_send.elapsed().as_micros()).unwrap_or(u64::MAX);
                    self.ping_blocked = self.need_flush || was_blocked;
                    self.ping_sent = Some((now, tag));
                    self.last_ping = now;
                }
                Some((t0, _)) if now.duration_since(t0) >= sh.ping.1 => {
                    // как в Python (`Session.pinger`): нет pong за PING_TIMEOUT — таймаут (счётчик health, событие) и путь испорчен: не рвём, а помечаем,
                    // шард откроет замену, примет её окном проверки пути и закроет эту сессию после перекрытия. `data_alive` делит таймауты на «pong нет, данные живы» (`pong_only`) и «канал молчит» (`silent`)
                    let since_frame = now.duration_since(self.last_frame);
                    let alive = since_frame < sh.ping.1;
                    // «свежие данные»: последний кадр моложе `FRESH_DATA` (решение координатора 14.11 после происшествия Binance 20:50 UTC: у всей биржи разом пропали pong при идущих данных)
                    let fresh = since_frame < FRESH_DATA.min(sh.ping.1);
                    {
                        let mut l = m.lists();
                        l.timeouts.push_back(now_ms());
                        if !alive {
                            l.timeouts_silent.push_back(now_ms());
                        }
                    }
                    sh.events.emit("timeout", json!({"what": "ping", "session": seq, "data_alive": alive, "fresh_data": fresh, "kind": if alive { "pong_only" } else { "silent" }, "tcp": tcp_json(ws.get_ref().tcp()), "diag": self.diag_json()}));
                    if fresh && !sh.pong_only_degrades {
                        // данные свежие (последний кадр моложе `FRESH_DATA`): журнал и счётчик, линию не трогаем, пинг продолжается (следующий — по периоду); тишина и данные старше — как раньше
                        self.ping_sent = None;
                        self.last_ping = now;
                    } else {
                        sh.events.emit("ping_degraded", json!({"session": seq}));
                        sh.watch().mark_degraded(seq);
                        self.pings_on = false;
                        self.ping_sent = None;
                    }
                }
                _ => {}
            }
        }
        if let Some(p) = self.probe.as_mut() {
            if p.finished(now) {
                let clean = p.clean();
                let (cnt, p50, p99) = p.summary();
                // медленный путь на тёмном шарде (данные стоят, ни одна линия не несёт поток) или у единственного источника данных (принятые соединения за окно почти ничего не принесли):
                // соединение с данными, пусть запоздавшими, лучше пустого шарда — принимаем, замена на чистый путь потом по `mark_slow` (TK-582)
                let own = self.probe_own;
                let others = trusted_frames(&sh.metrics).saturating_sub(self.trusted_base);
                let verdict = window_verdict(clean, storm::dark(&sh.metrics), own, others);
                let mut ev = json!({"session": seq, "n": cnt, "p50_ms": p50.map(f64::round), "p99_ms": p99.map(f64::round), "own": own, "others": others});
                let name = match verdict {
                    Window::Ok => "path_ok",
                    Window::SlowAccepted(why) => {
                        if let Some(o) = ev.as_object_mut() {
                            o.insert("why".to_string(), json!(why));
                        }
                        "path_slow_accepted"
                    }
                    Window::Rejected => "path_rejected",
                };
                sh.events.emit(name, ev);
                if verdict == Window::Rejected {
                    return Some(Outcome::PathRejected);
                }
                if matches!(verdict, Window::SlowAccepted(_)) {
                    sh.watch().mark_slow(seq);
                }
                self.probe = None;
                self.probing = false;
                if let Some(a) = self.accepted.as_ref() {
                    a.store(true, Relaxed);
                }
            }
        }
        None
    }

    /// Кадр после разжатия: сердцебиение биржи (`Ping` → `Pong`) отвечается сразу и дальше не идёт; остальное — в очередь писателя. `Some` — сессия кончилась.
    fn deliver(
        &mut self,
        sh: &Shared,
        ws: &mut WebSocket<Conn>,
        seq: u32,
        rx: i64,
        t_read: Instant,
        data: Bytes,
    ) -> Option<Outcome> {
        if let Some(reply) = sh.exch.heartbeat_reply(&data) {
            self.srv_pings += 1;
            self.last_srv_ping = Some(t_read);
            let sent = self.send(ws, Message::text(reply));
            if sent.is_ok() {
                self.note_reply(rx);
            } else if let Err(e) = &sent {
                sh.events.emit("ws_end", json!({"session": seq, "how": "write_error:heartbeat", "detail": e.to_string(), "tcp": tcp_json(ws.get_ref().tcp()), "diag": self.diag_json()}));
            }
            return sent
                .err()
                .map(|e| Outcome::Failed(format!("heartbeat: {e}")));
        }
        if self.frame(sh, ws, seq, rx, t_read, data) {
            None
        } else {
            Some(Outcome::Failed("писатель шарда ушёл".to_string()))
        }
    }

    fn frame(
        &mut self,
        sh: &Shared,
        ws: &WebSocket<Conn>,
        seq: u32,
        rx: i64,
        t_read: Instant,
        data: Bytes,
    ) -> bool {
        let m = &sh.metrics;
        self.n += 1;
        let n = self.n;
        if self.probing {
            m.frames_probing.fetch_add(1, Relaxed);
        }
        m.frames.fetch_add(1, Relaxed);
        m.bytes
            .fetch_add(u64::try_from(data.len()).unwrap_or(0), Relaxed);
        m.last_frame_ms.store(rx.div_euclid(1_000_000), Relaxed);
        if let Some(l) = m.lane_last_ms.get(usize::from(self.lane)) {
            l.store(rx.div_euclid(1_000_000), Relaxed);
        }
        let mono = super::shard::mono_ms_at(t_read);
        let prev = m.last_frame_mono_ms.load(Relaxed);
        if prev > 0 && mono.saturating_sub(prev) >= super::shard::GAP_TIMEOUT_MS {
            // молчание ≥ порога закончилось этим кадром: конец паузы по приёму (`shard::supervise` берёт его как конец)
            m.gap_resume_ms.store(rx.div_euclid(1_000_000), Relaxed);
        }
        m.last_frame_mono_ms.store(mono, Relaxed);
        if n.is_multiple_of(RECVQ_EVERY) {
            if let Some(q) = net::recv_q(ws.get_ref().tcp()) {
                m.recvq_samples.fetch_add(1, Relaxed);
                m.recvq_max.fetch_max(q, Relaxed);
                if q > 0 {
                    m.recvq_behind.fetch_add(1, Relaxed);
                }
            }
        }
        if let Some(p) = self.probe.as_mut() {
            if p.collecting(t_read) {
                if !self.probe_open {
                    self.probe_open = true;
                    self.trusted_base = trusted_frames(m);
                }
                self.probe_own += 1;
                p.add(sh.exch.lag_ms(&data, rx));
            }
        }
        if sh.exch.is_retire_notice(&data) {
            sh.watch().mark_retire(seq);
            sh.events
                .emit("notice", json!({"session": seq, "retire": true}));
        }
        if (n - 1).is_multiple_of(LAG_SAMPLE) {
            if let Some(lag) = sh.exch.lag_ms(&data, rx) {
                sh.watch().sample(seq, lag);
            }
        }
        let settled = t_read >= self.settle_at;
        // занятость потока приёма вне чтения и передача кадра в очередь — по каждому BUSY_EVERY-му кадру (чтение часов дороже самого кадра)
        let sampled = n.is_multiple_of(BUSY_EVERY);
        let t_push = sampled.then(Instant::now);
        let pushed = sh.ring.push(Item {
            rx_ns: rx,
            sender: seq,
            lane: self.lane,
            settled,
            payload: Payload::Frame(data),
        });
        if let Some(tp) = t_push {
            let now = Instant::now();
            let us = |a: Instant, b: Instant| {
                u64::try_from(b.saturating_duration_since(a).as_micros()).unwrap_or(u64::MAX)
            };
            m.busy_max_us.fetch_max(us(t_read, now), Relaxed);
            // передача — только `push` (в том числе ожидание места в полной очереди), отдельно от остальной работы кадра
            m.handoff_max_us.fetch_max(us(tp, now), Relaxed);
        }
        pushed.is_ok()
    }
}

/// Чтение потоком приёма: блокирующий сокет с таймаутом `READ_TICK`; пинг, окно проверки, остановка — на тиках.
fn read_loop(
    sh: &Shared,
    ws: &mut WebSocket<Conn>,
    seq: u32,
    mut rx: Rx,
    cancel: &AtomicBool,
) -> Outcome {
    loop {
        if cancel.load(Relaxed) || sh.stop.load(Relaxed) {
            return Outcome::Closed;
        }
        let got = ws.read();
        let rx_ns = wall_ns();
        let t_read = Instant::now();
        let had_frame = matches!(got, Ok(Message::Text(_) | Message::Binary(_)));
        match got {
            Ok(msg) => {
                if let Some(out) = rx.message(sh, ws, seq, msg, rx_ns, t_read) {
                    return out;
                }
            }
            Err(e) => {
                let out = Rx::read_error(&e);
                if out.is_some() {
                    rx.report_end(sh, ws, seq, &e);
                }
                if let Some(out) = out {
                    return out;
                }
            }
        }
        // после кадра отдельное чтение часов не нужно: тик пинга и окна проверки грубее микросекунд
        let now = if had_frame { t_read } else { Instant::now() };
        if let Some(out) = rx.tick(sh, ws, seq, now) {
            return out;
        }
    }
}

/// Двоичный кадр биржи → текст кадра: gzip-кадры (BingX) разжимаются здесь, до метки лага, разбора и записи. Не разжался (не gzip, обрыв, CRC) —
/// кадр идёт дальше как пришёл (адаптер его не разберёт, писатель запишет сырым) и пишется событие `bad_frame`.
fn unpack(sh: &Shared, seq: u32, gz: &mut Option<Gunzip>, bad: &mut u32, b: Bytes) -> Bytes {
    let Some(g) = gz.as_mut() else { return b };
    if let Some(plain) = g.unpack(&b) {
        return Bytes::copy_from_slice(plain);
    }
    *bad += 1;
    if *bad <= BAD_FRAME_EVENTS {
        sh.events.emit(
            "bad_frame",
            json!({"session": seq, "what": "gzip", "len": b.len(), "n": *bad}),
        );
    }
    b
}
