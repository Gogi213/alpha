//! Шард: писатель + диск + линии соединений одного набора потоков; переподключение, смена пути без разрыва, смена входа.
//!
//! Линия (`lanes` ≥ 1) — независимое соединение на тот же набор потоков (вторая — тёплый запас на другом входе/пути);
//! дубли линий снимает писатель. У линии одна живая сессия; при испорченном пути (`degraded`) или возрасте `max_age`
//! открывается замена, после приёма (окно проверки пути) — перекрытие `overlap`, затем старая закрывается.
//! Вход линии меняется на запасной: после `FAIL_LIMIT` коротких сессий подряд или при замене испорченной сессии.
//! Остановка: сессии закрываются и пишут `closed`, только потом писатель получает `Shutdown` и закрывает файл.

use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicUsize, Ordering::Relaxed};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde_json::json;

use super::budget::Budget;
use super::events::{now_ms, replay_lists, EventLog};
use super::exchange::{Entrance, Exchange, Family, ShardPlan};
use super::metrics::{us_to_ms, Hist, ShardMetrics, WindowSnap, STATS_PERIOD_S};
use super::path::{PathWatch, PROBE_MAX_REJECTS, PROBE_S, PROBE_SKIP_S};
use super::reactor::ReactorHandle;
use super::ring::{ring, Item, Payload};
use super::session::{self, Outcome, Shared, CONNECT_RTT_MAX_MS, PING_PERIOD, PING_TIMEOUT};
use super::sink::{run_sink, sink_channel};
use super::worker::{spawn_worker, WorkerHandle};
use super::writer::{ShardWriter, WriterCfg};

/// Короткая сессия — не дольше этого; `FAIL_LIMIT` коротких подряд → вход меняется (назначаемое, g022-integrate).
pub const SHORT_SESSION: Duration = Duration::from_secs(60);
pub const FAIL_LIMIT: u32 = 3;
const GAP_TIMEOUT_MS: u64 = 3000;
const GAP_TICK: Duration = Duration::from_millis(500);
/// Пауза перед новой попыткой улучшить путь сессии, принятой без проверки, после неудачной.
const UPGRADE_RETRY: Duration = Duration::from_secs(60);
/// Замена, которая не принимает медленный путь (`never_slow`): после стольких отбраковок подряд пауза, чтобы не съесть бюджет подключений.
pub const STRICT_PAUSE: Duration = Duration::from_secs(10);
const STACK: usize = 1 << 20;

/// Реактор по умолчанию для `ShardOpts::new`: только в сборке проверок с фичей `test-reactor` (весь набор тестов шардов идёт через реактор).
#[cfg(feature = "test-reactor")]
fn default_reactor() -> Option<ReactorHandle> {
    static R: OnceLock<Option<ReactorHandle>> = OnceLock::new();
    R.get_or_init(|| {
        super::reactor::spawn_reactor(super::reactor::ReactorCfg {
            window: Duration::from_millis(1),
            kernel_stamp: false,
        })
        .map(|(h, _)| h)
    })
    .clone()
}

#[cfg(not(feature = "test-reactor"))]
fn default_reactor() -> Option<ReactorHandle> {
    None
}

fn start() -> Instant {
    static START: OnceLock<Instant> = OnceLock::new();
    *START.get_or_init(Instant::now)
}

pub fn mono_ms() -> u64 {
    mono_ms_at(Instant::now())
}

/// Монотонное время в мс от первого обращения, для уже снятого `Instant` (без лишнего чтения часов на кадр).
pub fn mono_ms_at(t: Instant) -> u64 {
    u64::try_from(t.saturating_duration_since(start()).as_millis()).unwrap_or(0)
}

#[derive(Clone, Debug)]
pub struct ShardOpts {
    pub root: PathBuf,
    pub run_id: i64,
    pub flush: Duration,
    pub block_max: usize,
    pub ring_cap: usize,
    pub sink_cap: usize,
    /// Живое соединение живёт не дольше: новое открывается заранее (биржа рвёт сутки), старое закрывается после перекрытия.
    pub max_age: Duration,
    /// На перекрытии оба соединения несут одни кадры в одного писателя (дубли снимает он).
    pub overlap: Duration,
    /// Сколько ждать приёма новой сессии (окно проверки пути), затем перекрытие.
    pub switch_wait: Duration,
    pub tls: bool,
    pub port: u16,
    /// Окно проверки нового пути: (пропуск, конец), секунды.
    pub probe_window: (f64, f64),
    /// Период пинга и срок ожидания pong.
    pub ping: (Duration, Duration),
    /// Порог отбраковки пути по RTT рукопожатия, мс.
    pub rtt_max_ms: f64,
    /// Замена испорченного (или принятого медленным) пути никогда не принимает медленный путь «как есть»: старая сессия остаётся, пока не найден быстрый.
    pub never_slow: bool,
    /// Линия не заменяется, пока её партнёр по шарду здоров (его сессия открыта, не испорчена и не медленная); возрастная пересменка не откладывается.
    pub lazy: bool,
    /// Шард без соединения проверяет путь, как Python (`Shared::cold_probe`).
    pub cold_probe: bool,
    /// Пауза строгой замены после каждых `PROBE_MAX_REJECTS` отбраковок подряд.
    pub strict_pause: Duration,
    /// Рабочий процесса, общий для шардов (`runner`); `None` — шард заводит своего (проверки).
    pub worker: Option<WorkerHandle>,
    /// Период пробуждений рабочего (collector-perf: кадры не будят писателя, он просыпается по таймеру).
    pub worker_tick: Duration,
    /// Реактор процесса: готовые соединения читает он; `None` — поток на соединение.
    pub reactor: Option<ReactorHandle>,
    /// В `rx` файла — ядерная метка приёма (реактор): писатель сортирует пачку по метке, дубль несёт минимум метки по линиям.
    pub kernel_stamp: bool,
}

impl ShardOpts {
    pub fn new(root: PathBuf, run_id: i64) -> Self {
        Self {
            root,
            run_id,
            flush: Duration::from_millis(100),
            block_max: 256 * 1024,
            ring_cap: 1 << 15,
            sink_cap: 64,
            max_age: Duration::from_secs(23 * 3600),
            overlap: Duration::from_secs(20),
            switch_wait: Duration::from_secs(120),
            tls: true,
            port: 443,
            probe_window: (PROBE_SKIP_S, PROBE_S),
            ping: (PING_PERIOD, PING_TIMEOUT),
            rtt_max_ms: CONNECT_RTT_MAX_MS,
            never_slow: false,
            lazy: false,
            cold_probe: false,
            strict_pause: STRICT_PAUSE,
            worker: None,
            worker_tick: Duration::from_micros(1000),
            reactor: default_reactor(),
            kernel_stamp: false,
        }
    }
}

/// Входы линии и счёт коротких сессий.
struct Lane {
    k: usize,
    ents: Vec<Entrance>,
    idx: AtomicUsize,
    fails: AtomicU32,
    /// Сдвиг возраста первой сессии: линии стареют вразнобой (k/число линий · max_age) — не обе замены в один час.
    stagger: Duration,
    /// Здоровье линий шарда (по индексу линии): сессия открыта, не испорчена и не медленная. Нужно ленивой замене (`ShardOpts::lazy`).
    health: Arc<Vec<AtomicBool>>,
}

impl Lane {
    /// Здорова ли хоть одна другая линия шарда.
    fn partner_healthy(&self) -> bool {
        self.health
            .iter()
            .enumerate()
            .any(|(j, h)| j != self.k && h.load(Relaxed))
    }
}

impl Lane {
    fn current(&self) -> Entrance {
        self.ents
            .get(self.idx.load(Relaxed) % self.ents.len().max(1))
            .cloned()
            .unwrap_or_else(|| Entrance::new("", Family::Any))
    }

    /// Следующий вход по кругу; событие и счётчик. Один вход — менять некуда.
    fn switch(&self, sh: &Shared, reason: &str, seq: u32) {
        if self.ents.len() < 2 {
            return;
        }
        let from = self.current();
        let fails = self.fails.swap(0, Relaxed);
        self.idx
            .store((self.idx.load(Relaxed) + 1) % self.ents.len(), Relaxed);
        let to = self.current();
        sh.metrics.lists().entrance_switches.push_back(now_ms());
        sh.events.emit("entrance_switch", json!({"line": self.k, "reason": reason, "session": seq, "from": from.host, "to": to.host, "fails": fails}));
    }

    /// Итог закрытой сессии: короткая — счёт, длинная — сброс; на пределе — смена входа.
    fn note_end(&self, sh: &Shared, seq: u32, ran: Duration) {
        if ran > SHORT_SESSION {
            self.fails.store(0, Relaxed);
        } else if self.fails.fetch_add(1, Relaxed) + 1 >= FAIL_LIMIT {
            self.switch(sh, "connect_fail", seq);
        }
    }
}

fn sleep_stop(sh: &Shared, d: Duration) {
    let end = Instant::now() + d;
    while !sh.stop.load(Relaxed) {
        let left = end.saturating_duration_since(Instant::now());
        if left.is_zero() {
            return;
        }
        thread::sleep(left.min(Duration::from_millis(100)));
    }
}

/// Одна сессия с переподключением при сбое; отбракованный путь — новое соединение сразу (до `PROBE_MAX_REJECTS` подряд).
fn session_forever(
    sh: &Arc<Shared>,
    lane: &Lane,
    seq: u32,
    accepted: Option<&Arc<AtomicBool>>,
    cancel: &Arc<AtomicBool>,
    strict: Option<Duration>,
) {
    let mut backoff = 1.0f64;
    let mut rejects = 0u32;
    loop {
        if cancel.load(Relaxed) || sh.stop.load(Relaxed) {
            return;
        }
        let t0 = Instant::now();
        let ent = lane.current();
        match session::run(
            sh,
            seq,
            &ent,
            strict.is_some() || rejects < PROBE_MAX_REJECTS,
            accepted,
            cancel,
            u8::try_from(lane.k).unwrap_or(u8::MAX),
        ) {
            Outcome::PathRejected => {
                rejects += 1;
                sleep_stop(sh, Duration::from_millis(500));
                if let Some(pause) = strict.filter(|_| rejects.is_multiple_of(PROBE_MAX_REJECTS)) {
                    sleep_stop(sh, pause);
                }
                continue;
            }
            Outcome::Closed => {
                if cancel.load(Relaxed) || sh.stop.load(Relaxed) {
                    return;
                }
                rejects = 0;
                sh.events.emit("closed", json!({"session": seq}));
            }
            Outcome::Failed(e) => {
                if sh.stop.load(Relaxed) {
                    return;
                }
                sh.events.emit("error", json!({"session": seq, "err": e.chars().take(200).collect::<String>(), "entrance": ent.host}));
            }
        }
        sh.metrics.lists().reconnects.push_back(now_ms());
        let ran = t0.elapsed();
        lane.note_end(sh, seq, ran);
        backoff = if ran > SHORT_SESSION {
            1.0
        } else {
            (backoff * 2.0).min(30.0)
        };
        sleep_stop(sh, Duration::from_secs_f64(backoff));
    }
}

struct SessionThread {
    seq: u32,
    cancel: Arc<AtomicBool>,
    born: Instant,
    join: JoinHandle<()>,
}

fn spawn_session(
    sh: &Arc<Shared>,
    lane: &Arc<Lane>,
    seq: u32,
    accepted: Option<Arc<AtomicBool>>,
    strict: Option<Duration>,
) -> Option<SessionThread> {
    let cancel = Arc::new(AtomicBool::new(false));
    let (shc, lanec, cc) = (Arc::clone(sh), Arc::clone(lane), Arc::clone(&cancel));
    let join = thread::Builder::new()
        .name(format!("rx-{}-{seq}", sh.plan.name))
        .stack_size(STACK)
        .spawn(move || {
            shc.metrics.register_thread();
            session_forever(&shc, &lanec, seq, accepted.as_ref(), &cc, strict);
        })
        .ok()?;
    Some(SessionThread {
        seq,
        cancel,
        born: Instant::now(),
        join,
    })
}

fn finish(s: SessionThread) {
    s.cancel.store(true, Relaxed);
    let _ = s.join.join();
}

/// Линия: живая сессия и смена без разрыва (возраст `max_age` или путь испортился).
fn lane_main(sh: &Arc<Shared>, lane: &Arc<Lane>, opts: &ShardOpts, seq0: u32) {
    let mut seq = seq0;
    let Some(mut cur) = spawn_session(sh, lane, seq, None, None) else {
        return;
    };
    cur.born = Instant::now()
        .checked_sub(lane.stagger)
        .unwrap_or_else(Instant::now);
    let mut upgrade_retry = Instant::now();
    let mut deferred: Option<u32> = None;
    while !sh.stop.load(Relaxed) {
        thread::sleep(Duration::from_millis(250));
        let (bad, slow_raw, open) = {
            let w = sh.watch();
            (
                w.is_degraded(cur.seq),
                w.is_slow(cur.seq),
                w.is_open(cur.seq),
            )
        };
        if let Some(h) = lane.health.get(lane.k) {
            h.store(open && !bad && !slow_raw, Relaxed);
        }
        let old = cur.born.elapsed() >= opts.max_age || sh.watch().is_retiring(cur.seq);
        // сессия принята без проверки пути, потому что шард был без соединения (холодный старт): путь меняется на проверенный позже, без смены входа
        let slow = !bad && !old && Instant::now() >= upgrade_retry && slow_raw;
        if !(bad || slow || old) {
            continue;
        }
        // ленивая замена: пока партнёр по шарду здоров, поток идёт по нему, а эта линия не тратит подключения (возрастная пересменка не откладывается)
        if opts.lazy && !old && lane.partner_healthy() {
            if deferred != Some(cur.seq) {
                deferred = Some(cur.seq);
                sh.events.emit(
                    "replace_deferred",
                    json!({"line": lane.k, "session": cur.seq, "degraded": bad}),
                );
            }
            continue;
        }
        // замена, не принимающая медленный путь: для испорченного и медленного пути, не для возрастной пересменки (её старую сессию биржа всё равно рвёт)
        let strict = (opts.never_slow && !old).then_some(opts.strict_pause);
        seq = seq.wrapping_add(1);
        let prev_idx = lane.idx.load(Relaxed);
        if bad {
            lane.switch(sh, "late_delivery", seq);
        }
        let accepted = Arc::new(AtomicBool::new(false));
        let Some(nxt) = spawn_session(sh, lane, seq, Some(Arc::clone(&accepted)), strict) else {
            lane.idx.store(prev_idx, Relaxed);
            continue;
        };
        let t0 = Instant::now();
        // строгая замена испорченного пути ждёт быстрый путь сколько нужно: старая сессия остаётся (медленная лучше разрыва)
        let patient = strict.is_some() && bad;
        while !accepted.load(Relaxed)
            && !sh.stop.load(Relaxed)
            && (patient || t0.elapsed() < opts.switch_wait)
        {
            thread::sleep(Duration::from_millis(100));
        }
        if !accepted.load(Relaxed) && !sh.stop.load(Relaxed) {
            if slow {
                // улучшение пути необязательно: прежняя сессия несёт данные, её не рвём (разрыв был бы хуже медленного пути), новая попытка позже
                sh.events.emit(
                    "upgrade_timeout",
                    json!({"session": seq, "kept_session": cur.seq}),
                );
                finish(nxt);
                upgrade_retry = Instant::now() + UPGRADE_RETRY;
                continue;
            }
            // как в Python (`Shard.lane`): событие `switch_timeout`, старая сессия всё равно закрывается после перекрытия, а замена продолжает подключаться
            sh.events.emit(
                "switch_timeout",
                json!({"session": seq, "old_session": cur.seq}),
            );
        }
        sleep_stop(sh, opts.overlap);
        let old_seq = cur.seq;
        finish(cur);
        sh.events.emit(
            match (bad, slow) {
                (true, _) => "path_switch",
                (false, true) => "path_upgrade",
                _ => "rotated",
            },
            json!({"old_session": old_seq, "new_session": seq}),
        );
        if bad {
            sh.metrics.lists().path_switches.push_back(now_ms());
        }
        cur = nxt;
    }
    finish(cur);
}

pub struct ShardHandle {
    pub name: String,
    pub metrics: Arc<ShardMetrics>,
    join: JoinHandle<()>,
}

impl ShardHandle {
    pub fn join(self) {
        let _ = self.join.join();
    }
}

/// Запуск шарда: потоки писателя, диска, линий и надзора. `entrances[k]` — входы линии k по порядку.
pub fn spawn_shard(
    exch: &Arc<dyn Exchange>,
    plan: &ShardPlan,
    entrances: &[Vec<Entrance>],
    opts: &ShardOpts,
    budget: &Arc<Budget>,
    stop: &Arc<AtomicBool>,
) -> Option<ShardHandle> {
    let metrics = ShardMetrics::new(&plan.name, now_ms(), opts.ring_cap, opts.sink_cap);
    // счётчики health за сутки возвращаются из журнала прежних запусков: рестарт их не обнуляет
    *metrics.lists() = replay_lists(&opts.root, &plan.name, now_ms());
    let (rtx, rrx) = ring(&metrics.ring);
    let (stx, srx) = sink_channel(&metrics.sink_q);
    let cfg = WriterCfg {
        root: opts.root.clone(),
        shard: plan.name.clone(),
        run_id: opts.run_id,
        flush: opts.flush,
        block_max: opts.block_max,
        clock: session::wall_ns,
    };
    let mut writer = ShardWriter::new(cfg, Arc::clone(exch), Arc::clone(&metrics), stx, rrx);
    writer.set_sort_batch(opts.kernel_stamp);
    let sh = Arc::new(Shared {
        exch: Arc::clone(exch),
        plan: plan.clone(),
        metrics: Arc::clone(&metrics),
        ring: rtx.clone(),
        watch: Mutex::new(PathWatch::default()),
        events: EventLog::open(&opts.root, &plan.name),
        budget: Arc::clone(budget),
        stop: Arc::clone(stop),
        tls: opts.tls,
        port: opts.port,
        probe_window: opts.probe_window,
        ping: opts.ping,
        rtt_max_ms: opts.rtt_max_ms,
        cold_probe: opts.cold_probe,
        reactor: opts.reactor.clone(),
    });
    let health: Arc<Vec<AtomicBool>> = Arc::new(
        (0..entrances.len())
            .map(|_| AtomicBool::new(false))
            .collect(),
    );
    let lanes: Vec<Arc<Lane>> = entrances
        .iter()
        .enumerate()
        .map(|(k, ents)| {
            Arc::new(Lane {
                k,
                ents: ents.clone(),
                idx: AtomicUsize::new(0),
                fails: AtomicU32::new(0),
                stagger: opts.max_age.mul_f64(
                    f64::from(u32::try_from(k).unwrap_or(0))
                        / f64::from(u32::try_from(entrances.len().max(1)).unwrap_or(1)),
                ),
                health: Arc::clone(&health),
            })
        })
        .collect();
    let (name, opts) = (plan.name.clone(), opts.clone());
    let m2 = Arc::clone(&metrics);
    let (worker, own_worker) = match opts.worker.clone() {
        Some(w) => (w, None),
        None => {
            let (w, j) = spawn_worker(opts.worker_tick);
            (w, Some(j))
        }
    };
    let join = thread::Builder::new()
        .name(format!("sh-{name}"))
        .stack_size(STACK)
        .spawn(move || {
            let sink_m = Arc::clone(&sh.metrics);
            let sink_depth = Arc::clone(&sh.metrics.sink_q);
            let m_sk = Arc::clone(&sh.metrics);
            let sink = thread::Builder::new()
                .name(format!("sk-{}", sh.plan.name))
                .spawn(move || {
                    m_sk.register_thread();
                    run_sink(&srx, &sink_depth, &sink_m);
                })
                .ok();
            let wr_done = worker.add(writer);
            let base = u32::try_from(opts.run_id.rem_euclid(i64::from(u32::MAX) / 2)).unwrap_or(0);
            let lane_threads: Vec<JoinHandle<()>> = lanes
                .iter()
                .enumerate()
                .filter_map(|(k, lane)| {
                    let (shc, lc, oc) = (Arc::clone(&sh), Arc::clone(lane), opts.clone());
                    let seq0 = base.wrapping_add(u32::try_from(k).unwrap_or(0) * 1_000_000);
                    thread::Builder::new()
                        .name(format!("ln-{}-{k}", sh.plan.name))
                        .stack_size(STACK)
                        .spawn(move || lane_main(&shc, &lc, &oc, seq0))
                        .ok()
                })
                .collect();
            sh.metrics.register_thread();
            sh.events
                .emit("started", json!({"version": super::VERSION}));
            supervise(&sh, &opts);
            for t in lane_threads {
                let _ = t.join();
            }
            sh.events.emit("stopped", serde_json::Value::Null);
            // все сессии закрыты и написали `closed`: писатель дочитывает очередь, закрывает файл суток
            let _ = rtx.push(Item {
                rx_ns: session::wall_ns(),
                sender: 0,
                lane: 0,
                settled: true,
                payload: Payload::Shutdown,
            });
            let _ = wr_done.recv();
            if let Some(j) = own_worker {
                worker.quit();
                let _ = j.join();
            }
            if let Some(s) = sink {
                let _ = s.join();
            }
        })
        .ok()?;
    Some(ShardHandle {
        name,
        metrics: m2,
        join,
    })
}

/// Пауза данных закончилась: длительность — в поминутный ряд (`gap_spans`) и в журнал (`gap_end`).
fn end_gap(sh: &Arc<Shared>, start_ms: i64, total_ms: u64) {
    let total = i64::try_from(total_ms).unwrap_or(i64::MAX);
    sh.metrics.lists().gap_spans.push_back((start_ms, total));
    sh.events.emit(
        "gap_end",
        json!({"gap_total_ms": total, "gap_start_ms": start_ms}),
    );
}

/// Надзор шарда до остановки: пауза данных (gap — только диагностика), строка статистики и сторож пути раз в период.
fn supervise(sh: &Arc<Shared>, opts: &ShardOpts) {
    let stats_path = opts.root.join(format!("stats-{}.jsonl", sh.plan.name));
    let mut in_gap = false;
    // пауза данных: начало (мс) и наибольшее наблюдаемое молчание (мс) — длительность пишется при возврате кадров
    let (mut gap_start, mut gap_peak) = (0i64, 0u64);
    let mut late = std::collections::VecDeque::<f64>::with_capacity(8);
    let mut prev = Instant::now();
    let mut last_stats = Instant::now();
    let mut max_gap = 0u64;
    let mut cpu_prev = (Instant::now(), sh.metrics.cpu_seconds());
    while !sh.stop.load(Relaxed) {
        thread::sleep(GAP_TICK);
        let now = Instant::now();
        let tick_late = (now.duration_since(prev).as_secs_f64() - GAP_TICK.as_secs_f64()).max(0.0);
        prev = now;
        if late.len() == 8 {
            late.pop_front();
        }
        late.push_back(tick_late);
        let m = &sh.metrics;
        let last = m.last_frame_mono_ms.load(Relaxed);
        if last > 0 && m.connected.load(Relaxed) > 0 {
            let gap = mono_ms().saturating_sub(last);
            max_gap = max_gap.max(gap);
            if gap > GAP_TIMEOUT_MS && in_gap {
                gap_peak = gap_peak.max(gap);
            } else if gap > GAP_TIMEOUT_MS {
                in_gap = true;
                gap_start = now_ms() - i64::try_from(gap).unwrap_or(0);
                gap_peak = gap;
                m.lists().gaps.push_back(now_ms());
                let loop_late = late.iter().copied().fold(0.0, f64::max);
                sh.events.emit(
                    "gap",
                    json!({"loop_late_ms": (loop_late * 1000.0).round(), "gap_ms": gap}),
                );
            } else {
                if in_gap {
                    end_gap(sh, gap_start, gap_peak);
                }
                in_gap = false;
            }
        } else {
            if in_gap {
                end_gap(sh, gap_start, gap_peak);
            }
            in_gap = false;
        }
        if last_stats.elapsed() >= Duration::from_secs(STATS_PERIOD_S) {
            last_stats = Instant::now();
            sh.watch().judge();
            let w = m.take_window(now_ms(), max_gap);
            max_gap = 0;
            let cpu = m.cpu_seconds();
            let cpu_frac =
                (cpu - cpu_prev.1).max(0.0) / cpu_prev.0.elapsed().as_secs_f64().max(1e-3);
            cpu_prev = (Instant::now(), cpu);
            if let Ok(mut f) = OpenOptions::new()
                .create(true)
                .append(true)
                .open(&stats_path)
            {
                let _ = writeln!(f, "{}", stats_line(m, &w, cpu_frac));
            }
            m.lists().trim(now_ms());
        }
    }
}

/// Строка статистики окна (формат Python-ядра + поля обратного давления).
fn stats_line(m: &ShardMetrics, w: &WindowSnap, cpu_frac: f64) -> serde_json::Value {
    let now = now_ms();
    let (lag, dups, q_p99, lanes) = {
        let st = m.writer();
        let mut h = Hist::new();
        for r in &st.lag_in {
            h.merge(&r.snapshot(now));
        }
        (
            h,
            st.dups,
            st.queue_lag.snapshot(now).percentile(0.99),
            st.lanes,
        )
    };
    json!({
        "ts_ms": now, "msgs": w.frames, "bytes": w.bytes, "max_gap_ms": w.max_gap_ms,
        "lag_p50_ms": lag.percentile(0.5).map(us_to_ms), "lag_p99_ms": lag.percentile(0.99).map(us_to_ms), "dups": dups, "cpu_frac": (cpu_frac * 1000.0).round() / 1000.0,
        "ring_depth_max": w.ring_depth_max, "ring_full": w.ring_full, "ring_blocked_ms": w.ring_blocked_ms,
        "queue_lag_p99_ms": q_p99.map(us_to_ms), "recvq_max": w.recvq_max, "recvq_behind": w.recvq_behind, "busy_max_us": w.busy_max_us,
        "handoff_max_us": w.handoff_max_us, "sink_depth_max": w.sink_depth_max, "disk_write_max_us": w.write_max_us,
        "lane_first": lanes.first, "lane_delivered": lanes.delivered, "lane_unique": lanes.unique,
    })
}
