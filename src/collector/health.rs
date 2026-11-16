//! Здоровье коллектора: метрики шардов → `health.json` (схема 2 = схема 1 Python-ядра + разделы `backpressure`, `process`, `totals`, `shards`).
//!
//! Поля схемы 1 — те же имена и смысл (страница здоровья читает их без правок): `symbols`, `ws`, `fresh`, `lag_in_ms`, `lag_out_ms`,
//! `last_frame_ms`, `last_write_ms`. Обратное давление — `backpressure`: глубина очереди приёма и диска, Recv-Q ядра, занятость потока
//! приёма, ожидание кадра в очереди писателя, самая долгая запись на диск. Запись — атомарная (tmp + rename).

use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::sync::Arc;
use std::time::Instant;

use serde_json::{json, Value};

use super::fresh::{KindFresh, KIND_NAMES, LATE_MS};
use super::metrics::{us_to_ms, Hist, ShardMetrics, WINDOW_S};
use super::path::percentile;

const HOUR_MS: i64 = 3_600_000;
const DAY_MS: i64 = 24 * HOUR_MS;

pub fn write_atomic(path: &Path, v: &Value) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let mut tmp = path.as_os_str().to_owned();
    tmp.push(".tmp");
    std::fs::write(&tmp, v.to_string())?;
    std::fs::rename(&tmp, path)
}

/// Процесс: CPU по классам потоков (rx — приём, wr — писатель, sk — диск, sh/ln — надзор), самый горячий поток, память.
#[derive(Default)]
pub struct ProcSampler {
    last: Option<(Instant, f64, ThreadMap)>,
}

/// tid → (имя, CPU потока в секундах, ожидание CPU потока в секундах)
type ThreadMap = HashMap<u32, (String, f64, f64)>;

#[derive(Default)]
struct ProcSnap {
    cores: f64,
    /// Сколько ядер-секунд в секунду потоки процесса простояли в очереди на CPU (ожидание планировщика): признак перегрузки машины.
    sched_wait: f64,
    by_class: HashMap<String, f64>,
    hot: Option<(String, f64)>,
    rss_mb: f64,
    threads: usize,
}

#[cfg(target_os = "linux")]
fn read_proc() -> Option<(f64, ThreadMap, f64)> {
    const TICKS: f64 = 100.0; // CLK_TCK Linux
    fn parse(stat: &str) -> Option<(String, f64)> {
        let (a, b) = (stat.find('(')?, stat.rfind(')')?);
        let comm = stat.get(a + 1..b)?.to_string();
        let f: Vec<&str> = stat.get(b + 2..)?.split_whitespace().collect();
        let (u, s) = (
            f.get(11)?.parse::<f64>().ok()?,
            f.get(12)?.parse::<f64>().ok()?,
        );
        Some((comm, (u + s) / TICKS))
    }
    let total = parse(&std::fs::read_to_string("/proc/self/stat").ok()?)?.1;
    let mut threads = ThreadMap::new();
    for e in std::fs::read_dir("/proc/self/task").ok()?.flatten() {
        let tid = e.file_name().to_string_lossy().parse::<u32>().ok()?;
        if let Some((name, cpu)) = std::fs::read_to_string(e.path().join("stat"))
            .ok()
            .and_then(|s| parse(&s))
        {
            // schedstat: «время на CPU, нс; время в очереди на CPU, нс; число слотов»
            let delay = std::fs::read_to_string(e.path().join("schedstat"))
                .ok()
                .and_then(|s| s.split_whitespace().nth(1)?.parse::<f64>().ok())
                .unwrap_or(0.0);
            threads.insert(tid, (name, cpu, delay / 1e9));
        }
    }
    let rss_pages = std::fs::read_to_string("/proc/self/statm")
        .ok()?
        .split_whitespace()
        .nth(1)?
        .parse::<f64>()
        .ok()?;
    Some((total, threads, rss_pages * 4096.0 / 1_048_576.0))
}

#[cfg(not(target_os = "linux"))]
fn read_proc() -> Option<(f64, ThreadMap, f64)> {
    None
}

impl ProcSampler {
    fn sample(&mut self) -> ProcSnap {
        let Some((total, threads, rss_mb)) = read_proc() else {
            return ProcSnap::default();
        };
        let now = Instant::now();
        let mut snap = ProcSnap {
            rss_mb,
            threads: threads.len(),
            ..ProcSnap::default()
        };
        if let Some((t0, total0, prev)) = &self.last {
            let dt = now.duration_since(*t0).as_secs_f64().max(1e-3);
            snap.cores = (total - total0) / dt;
            for (tid, (name, cpu, delay)) in &threads {
                // поток появился между замерами: считаем с нуля; ушедшие потоки в сумму не входят
                let (cpu0, delay0) = prev.get(tid).map_or((0.0, 0.0), |p| (p.1, p.2));
                snap.sched_wait += (delay - delay0) / dt;
                let d = (cpu - cpu0) / dt;
                let class = name.split('-').next().unwrap_or(name).to_string();
                *snap.by_class.entry(class).or_insert(0.0) += d;
                if snap.hot.as_ref().is_none_or(|h| d > h.1) {
                    snap.hot = Some((name.clone(), d));
                }
            }
        }
        self.last = Some((now, total, threads));
        snap
    }
}

fn r3(x: f64) -> f64 {
    (x * 1000.0).round() / 1000.0
}

fn pct_json(h: &Hist) -> Value {
    json!({"p50": h.percentile(0.5).map(us_to_ms), "p99": h.percentile(0.99).map(us_to_ms), "p99_9": h.percentile(0.999).map(us_to_ms), "window_s": WINDOW_S})
}

pub struct HealthCtx {
    pub exchange: &'static str,
    /// Пул в нижнем регистре (для «символов с данными»).
    pub pool: HashSet<String>,
    pub version: String,
    /// Пересмотр пула без перезапуска (`livepool`): сверка REST-оборота с подпиской; `None` — пул фиксирован на старте.
    pub live: Option<PoolHealth>,
    /// Окно «есть запись книги», мс: символ из состава REST без кадра книги за это время — «не подписан» (`DEFAULT_BOOK_WINDOW_S`).
    pub book_window_ms: i64,
}

/// Окно по умолчанию для независимого счётчика «не подписано», с: самый тихий символ Binance за 70 мин (PREDICTUSDT) молчал по книге и сделкам до 218 с, поэтому окно 60 с
/// («с данными за минуту») красило биржу без потери данных; 10 мин держат паузы тихих символов (назначаемое, Судье).
pub const DEFAULT_BOOK_WINDOW_S: u64 = 600;

/// Символ из состава REST (оборот выше порога): с какого момента он должен писать книгу (старт процесса или добавление пересмотром).
#[derive(Clone, Debug, PartialEq)]
pub struct Eligible {
    pub lower: String,
    pub name: String,
    pub qv: f64,
    pub since_ms: i64,
}

/// Итог независимой сверки: символы состава REST без записи книги за окно.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct NoBook {
    pub checked: usize,
    pub missing: usize,
    pub qv_usd: f64,
    pub top: Vec<(String, f64)>,
}

/// Сверка пула с биржей (`livepool::LivePool::health`): что REST считает торгуемым выше порога против того, что подписано.
#[derive(Clone, Debug, Default)]
pub struct PoolHealth {
    pub refresh_s: u64,
    pub hold_s: u64,
    pub last_try_ms: i64,
    pub last_ok_ms: i64,
    pub errors: u64,
    pub last_error: Option<String>,
    /// Контрактов в ответе REST и из них с оборотом ≥ порога.
    pub rest_symbols: usize,
    pub eligible: usize,
    /// Выше порога по последнему REST, но нет в раскладке по шардам сейчас (после применения пересмотра обычно 0: сбой REST или раскладки). Раскладка — наша
    /// структура, а не биржа: подписка считалась «пустой по построению»; «не подписано» для страницы — независимая сверка `no_book`.
    pub not_in_layout: usize,
    pub not_in_layout_qv_usd: f64,
    /// Состав REST с метками, с которых символ обязан писать книгу (для `no_book`).
    pub elig: Arc<Vec<Eligible>>,
    /// Выше порога, но не в подписке на момент опроса (до применения): сколько символов состав не успел поймать за период.
    pub missing_at_poll: usize,
    pub missing_at_poll_qv_usd: f64,
    pub missing_top: Vec<(String, f64)>,
    pub added_total: u64,
    pub removed_total: u64,
    pub spawned_shards: u64,
    pub remove_blocked: u64,
    pub pool: usize,
    /// Нагрузка: символов с измеренной нагрузкой, оценка самого тяжёлого шарда (кадров/с по сумме символов), его имя и шарды выше потолка биржи (`Exchange::shard_rate_cap`).
    pub rates_tracked: usize,
    pub max_shard_load: f64,
    pub max_shard: String,
    pub over_cap: Vec<String>,
}

impl PoolHealth {
    fn json(&self, now_ms: i64) -> Value {
        json!({
            "refresh_s": self.refresh_s, "hold_s": self.hold_s, "pool": self.pool, "rest_symbols": self.rest_symbols, "rest_eligible": self.eligible,
            "last_ok_ms": self.last_ok_ms, "last_try_ms": self.last_try_ms, "rest_age_s": (self.last_ok_ms > 0).then(|| (now_ms - self.last_ok_ms).max(0) / 1000),
            "errors": self.errors, "last_error": self.last_error,
            "not_in_layout": self.not_in_layout, "not_in_layout_qv_usd": self.not_in_layout_qv_usd.round(),
            "missing_at_poll": self.missing_at_poll, "missing_at_poll_qv_usd": self.missing_at_poll_qv_usd.round(),
            "missing_top": self.missing_top.iter().map(|(s, v)| json!([s, v.round()])).collect::<Vec<_>>(),
            "added_total": self.added_total, "removed_total": self.removed_total, "spawned_shards": self.spawned_shards, "remove_blocked": self.remove_blocked,
            "rates_tracked": self.rates_tracked, "max_shard_load": self.max_shard_load.round(), "max_shard": self.max_shard, "over_cap": self.over_cap,
        })
    }
}

/// Сделка символа, записанная не раньше чем через столько мс после его последнего кадра книги, при молчащей книге означает «книга не пришла»: сделка на лучшей цене меняет её размер, кадр книги
/// следует за сделкой за миллисекунды (запас — порядок записи двух шардов).
const TRADE_AFTER_BOOK_MS: i64 = 1_000;

impl PoolHealth {
    /// Независимый счётчик «не подписано»: символы состава REST (выше порога) без кадра книги за окно `window_ms` по кадрам книжных шардов, а не по раскладке, и при этом
    /// либо без единого кадра книги с момента, когда символ обязан писать (`since_ms`), либо с записанной после его последнего кадра книги сделкой по этому символу. Тихий символ без сделок (акции и индексы Gate
    /// при закрытом рынке молчат по книге до 778 с — проверка 16.11) не считается: молчание книги без сделок — не пропавшая подписка. Символ считается с момента `since_ms` + окно
    /// (только что добавленному дают то же окно на первую запись). `book_last` и `trade_last` — мс последнего кадра символа (нижний регистр) в книжных и в шардах сделок. `None` — опроса REST ещё не было.
    pub fn no_book(
        &self,
        book_last: &HashMap<String, i64>,
        trade_last: &HashMap<String, i64>,
        now_ms: i64,
        window_ms: i64,
    ) -> Option<NoBook> {
        if self.last_ok_ms <= 0 {
            return None;
        }
        let mut r = NoBook::default();
        let mut miss: Vec<(String, f64)> = Vec::new();
        for e in self.elig.iter() {
            if now_ms - e.since_ms < window_ms {
                continue;
            }
            r.checked += 1;
            // кадр книги, пришедший, когда символ уже обязан был писать
            let book = book_last
                .get(&e.lower)
                .copied()
                .filter(|t| *t >= e.since_ms);
            if book.is_some_and(|t| now_ms - t <= window_ms) {
                continue;
            }
            let dead = match book {
                None => true,
                Some(b) => trade_last
                    .get(&e.lower)
                    .is_some_and(|t| *t >= b + TRADE_AFTER_BOOK_MS),
            };
            if dead {
                r.missing += 1;
                r.qv_usd += e.qv;
                miss.push((e.name.clone(), e.qv));
            }
        }
        miss.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
        miss.truncate(10);
        r.top = miss;
        Some(r)
    }
}

/// Размеры поднятия `rx` (мкс): число, p50, p99, max.
fn clamp_json(h: &Hist) -> Value {
    json!({"n": h.count(), "p50": h.percentile(0.5), "p99": h.percentile(0.99), "max": h.percentile(1.0)})
}

/// Сводка по шардам на `now_ms`.
#[allow(clippy::too_many_lines)]
pub fn build(
    shards: &[Arc<ShardMetrics>],
    ctx: &HealthCtx,
    now_ms: i64,
    proc: &mut ProcSampler,
) -> Value {
    let (hour, day) = (now_ms - HOUR_MS, now_ms - DAY_MS);
    let mut with_data: HashSet<String> = HashSet::new();
    let mut book_last: HashMap<String, i64> = HashMap::new();
    let mut trade_last: HashMap<String, i64> = HashMap::new();
    let mut lag_in = Hist::new();
    let mut lag_in_kind = [Hist::new(), Hist::new(), Hist::new()];
    let mut lag_out = Hist::new();
    let mut queue_lag = Hist::new();
    let mut clamp = Hist::new();
    let mut clamp_class = [Hist::new(), Hist::new(), Hist::new(), Hist::new()];
    let mut clamp_age = Hist::new();
    let mut fresh = [KindFresh::default(); 4];
    let mut fresh_seen = [false; 4];
    let (mut pings, mut connections) = (Vec::new(), 0i64);
    let (mut to, mut ts, mut gp, mut rc, mut es, mut ps) = (
        [0u64; 2], [0u64; 2], [0u64; 2], [0u64; 2], [0u64; 2], [0u64; 2],
    );
    // паузы шардов книги и mark: дозапрос сделок их не восстанавливает — потеря у бирж с дозапросом; паузы шардов сделок там потерей не считаются
    let mut gp_data = [0u64; 2];
    let (mut last_frame, mut last_write, mut started) = (0i64, 0i64, i64::MAX);
    let mut totals = [0u64; 6];
    let (mut sink_errors, mut files_closed, mut blocks_dropped) = (0u64, 0u64, 0u64);
    let (mut depth_max, mut sink_depth_max, mut ring_cap, mut sink_cap) =
        (0i64, 0i64, 0usize, 0usize);
    let (mut full_total, mut blocked_ms_total, mut full_win) = (0u64, 0u64, 0u64);
    let (mut recvq_max, mut recvq_s, mut recvq_b, mut busy_max, mut handoff_max, mut write_max) =
        (0u64, 0u64, 0u64, 0u64, 0u64, 0u64);
    let mut per_shard = Vec::new();
    for m in shards {
        let is_trade_shard = super::livepool::kind_of(&m.name) == "trade";
        started = started.min(m.started_ms);
        connections += m.connected.load(std::sync::atomic::Ordering::Relaxed);
        last_frame = last_frame.max(m.last_frame_ms.load(std::sync::atomic::Ordering::Relaxed));
        {
            let mut l = m.lists();
            for (i, cut) in [hour, day].into_iter().enumerate() {
                let c = |dq: &std::collections::VecDeque<i64>| {
                    u64::try_from(dq.iter().filter(|&&t| t > cut).count()).unwrap_or(0)
                };
                for (acc, dq) in [
                    (&mut to, &l.timeouts),
                    (&mut ts, &l.timeouts_silent),
                    (&mut gp, &l.gaps),
                    (&mut rc, &l.reconnects),
                    (&mut es, &l.entrance_switches),
                    (&mut ps, &l.path_switches),
                ] {
                    if let Some(a) = acc.get_mut(i) {
                        *a += c(dq);
                    }
                }
                if let (false, Some(a)) = (is_trade_shard, gp_data.get_mut(i)) {
                    *a += c(&l.gaps);
                }
            }
            pings.extend(l.pings.iter().map(|p| p.1));
            l.trim(now_ms);
        }
        let is_book = super::livepool::kind_of(&m.name) == "book";
        let (shard_lag, shard_clamp_p99) = {
            let mut w = m.writer();
            for (low, last, _) in w.seen.values() {
                if now_ms - last <= WINDOW_S * 1000 && ctx.pool.contains(low) {
                    with_data.insert(low.clone());
                }
                let slot = if is_book {
                    Some(&mut book_last)
                } else if is_trade_shard {
                    Some(&mut trade_last)
                } else {
                    None
                };
                if let Some(m) = slot {
                    let e = m.entry(low.clone()).or_insert(0);
                    *e = (*e).max(*last);
                }
            }
            let mut sl = Hist::new();
            for (k, r) in w.lag_in.iter().enumerate() {
                let h = r.snapshot(now_ms);
                if let Some(a) = lag_in_kind.get_mut(k) {
                    a.merge(&h);
                }
                sl.merge(&h);
            }
            lag_in.merge(&sl);
            queue_lag.merge(&w.queue_lag.snapshot(now_ms));
            let shard_clamp = w.clamp_us.snapshot(now_ms);
            clamp.merge(&shard_clamp);
            for (a, r) in clamp_class.iter_mut().zip(&w.clamp_class) {
                a.merge(&r.snapshot(now_ms));
            }
            clamp_age.merge(&w.clamp_age_us.snapshot(now_ms));
            for k in 0..4 {
                if let (Some(f), Some(acc), Some(seen)) = (
                    w.fresh.fragment(k, now_ms),
                    fresh.get_mut(k),
                    fresh_seen.get_mut(k),
                ) {
                    acc.merge(&f);
                    *seen = true;
                }
            }
            for (t, v) in
                totals
                    .iter_mut()
                    .zip([w.records, w.dups, w.raw, w.raw_copies, w.rows, w.clamped])
            {
                *t += v;
            }
            (sl, shard_clamp.percentile(0.99))
        };
        {
            let s = m.sink();
            lag_out.merge(&s.lag_out.snapshot(now_ms));
            last_write = last_write.max(s.last_write_ms);
            sink_errors += s.errors;
            blocks_dropped += s.blocks_dropped;
            files_closed += s.files_closed;
        }
        let w = m
            .window
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone();
        depth_max = depth_max.max(w.ring_depth_max);
        sink_depth_max = sink_depth_max.max(w.sink_depth_max);
        (ring_cap, sink_cap) = (m.ring.cap, m.sink_q.cap);
        full_total += m.ring.full.load(std::sync::atomic::Ordering::Relaxed);
        blocked_ms_total +=
            m.ring.blocked_ns.load(std::sync::atomic::Ordering::Relaxed) / 1_000_000;
        full_win += w.ring_full;
        recvq_max = recvq_max.max(w.recvq_max);
        recvq_s += w.recvq_samples;
        recvq_b += w.recvq_behind;
        busy_max = busy_max.max(w.busy_max_us);
        handoff_max = handoff_max.max(w.handoff_max_us);
        write_max = write_max.max(w.write_max_us);
        let lanes = m.writer().lanes;
        per_shard.push(json!({
            "lanes": {"first": lanes.first, "delivered": lanes.delivered, "unique": lanes.unique},
            // живость линий: здорова ли сессия и сколько секунд линия молчит (null — кадров ещё не было); тихая линия при живом партнёре — потерянная избыточность
            "lane_ok": m.lane_ok.iter().take(2).map(|a| a.load(std::sync::atomic::Ordering::Relaxed)).collect::<Vec<_>>(),
            "lane_silent_s": m.lane_last_ms.iter().take(2).map(|a| { let t = a.load(std::sync::atomic::Ordering::Relaxed); (t > 0).then(|| (now_ms - t).max(0) / 1000) }).collect::<Vec<_>>(),
            "shard": m.name, "connected": m.connected.load(std::sync::atomic::Ordering::Relaxed), "msgs_10s": w.frames,
            "lag_in_p99_ms": shard_lag.percentile(0.99).map(us_to_ms), "rx_clamp_p99_us": shard_clamp_p99, "ring_depth_max": w.ring_depth_max, "recvq_max": w.recvq_max, "busy_max_us": w.busy_max_us,
        }));
    }
    let ping_n = pings.len();
    let ping = json!({"p50": percentile(&mut pings, 0.5).map(|v| (v * 10.0).round() / 10.0), "max": (ping_n > 0).then(|| pings.iter().copied().fold(0.0, f64::max)).map(|v| (v * 10.0).round() / 10.0)});
    let kinds: serde_json::Map<String, Value> = KIND_NAMES
        .iter()
        .enumerate()
        .filter(|(i, _)| fresh_seen.get(*i).copied().unwrap_or(false))
        .map(|(i, name)| {
            let f = fresh.get(i).copied().unwrap_or_default();
            ((*name).to_string(), json!({"late_1h": f.late_1h, "late_24h": f.late_24h, "max_1h_ms": f.max_1h_ms, "max_24h_ms": f.max_24h_ms}))
        })
        .collect();
    let p = proc.sample();
    let [lag_book, lag_trade, lag_mark] = &lag_in_kind;
    let [to1, to24] = to;
    let [ts1, ts24] = ts;
    let [gp1, gp24] = gp;
    let [gpd1, gpd24] = gp_data;
    let [rc1, rc24] = rc;
    let [es1, es24] = es;
    let [ps1, ps24] = ps;
    let [t_rec, t_dup, t_raw, t_rawc, t_rows, t_clamp] = totals;
    let mut symbols = json!({"subscribed": ctx.pool.len(), "with_data_1m": with_data.len()});
    if let (Some(l), Some(o)) = (ctx.live.as_ref(), symbols.as_object_mut()) {
        o.insert("not_in_layout".into(), json!(l.not_in_layout));
        // независимый счётчик: по кадрам книги за окно, не по раскладке; ключ `book_window_s` есть всегда — страница по нему отличает эту сборку от прежней
        o.insert("book_window_s".into(), json!(ctx.book_window_ms / 1000));
        let warm = now_ms - started < ctx.book_window_ms;
        let nb = if warm {
            None
        } else {
            l.no_book(&book_last, &trade_last, now_ms, ctx.book_window_ms)
        };
        o.insert(
            "not_subscribed".into(),
            json!(nb.as_ref().map(|n| n.missing)),
        );
        o.insert(
            "not_subscribed_qv_usd".into(),
            json!(nb.as_ref().map(|n| n.qv_usd.round())),
        );
        o.insert("rest_checked".into(), json!(nb.as_ref().map(|n| n.checked)));
        o.insert(
            "not_subscribed_top".into(),
            json!(nb.map(|n| n
                .top
                .iter()
                .map(|(s, v)| json!([s, v.round()]))
                .collect::<Vec<_>>())),
        );
    }
    let by_class: serde_json::Map<String, Value> = p
        .by_class
        .iter()
        .map(|(k, v)| (k.clone(), json!(r3(*v))))
        .collect();
    #[allow(clippy::cast_precision_loss)]
    let behind_pct = if recvq_s == 0 {
        0.0
    } else {
        recvq_b as f64 * 100.0 / recvq_s as f64
    };
    json!({
        "schema": 2, "exchange": ctx.exchange, "ts_ms": now_ms, "started_ms": if started == i64::MAX { now_ms } else { started },
        "symbols": symbols,
        "ws": {
            "connections": connections, "ping_ms": ping,
            "timeouts_1h": to1, "timeouts_24h": to24,
            // таймаут = ping без pong ≥ 10 с; «канал молчит» — и данных за те же 10 с нет, «pong нет, данные живы» — остальные
            "timeouts_alive_1h": to1.saturating_sub(ts1), "timeouts_alive_24h": to24.saturating_sub(ts24), "timeouts_silent_1h": ts1, "timeouts_silent_24h": ts24,
            "gaps_1h": gp1, "gaps_24h": gp24, "gaps_1h_nontrade": gpd1, "gaps_24h_nontrade": gpd24, "reconnects_1h": rc1, "reconnects_24h": rc24,
            "entrance_switches_1h": es1, "entrance_switches_24h": es24, "path_switches_1h": ps1, "path_switches_24h": ps24,
        },
        "fresh": {"late_ms": LATE_MS, "kinds": kinds},
        "lag_in_ms": pct_json(&lag_in),
        "lag_in_by_kind_ms": {"book": pct_json(lag_book), "trade": pct_json(lag_trade), "mark": pct_json(lag_mark)},
        "lag_out_ms": pct_json(&lag_out),
        "last_frame_ms": last_frame, "last_write_ms": last_write,
        // на сколько мкс `rx` записи поднят до приёма предыдущей записи шарда (`totals.rx_clamped` — сколько записей), 60 с по всем шардам
        "rx_clamp_us": {"n": clamp.count(), "p50": clamp.percentile(0.5), "p99": clamp.percentile(0.99), "p99_9": clamp.percentile(0.999), "max": clamp.percentile(1.0),
            "by_class": {"same_socket": clamp_json(&clamp_class[0]), "same_lane_other_conn": clamp_json(&clamp_class[1]), "other_lane": clamp_json(&clamp_class[2]), "control": clamp_json(&clamp_class[3])},
            "age_at_writer": clamp_json(&clamp_age)},
        "backpressure": {
            "window_s": 10,
            "ring": {"cap": ring_cap, "depth_max": depth_max, "full_events_total": full_total, "full_events_window": full_win, "blocked_ms_total": blocked_ms_total},
            "sink": {"cap": sink_cap, "depth_max": sink_depth_max, "write_max_ms": us_to_ms(write_max)},
            "recv_q": {"max_bytes": recvq_max, "samples": recvq_s, "behind_pct": r3(behind_pct)},
            "reader": {"busy_max_us": busy_max, "handoff_max_us": handoff_max},
            "queue_lag_ms": pct_json(&queue_lag),
        },
        "process": {
            "version": ctx.version, "cpu_cores": r3(p.cores), "sched_wait_cores": r3(p.sched_wait), "cpu_by_class": by_class, "threads": p.threads, "rss_mb": r3(p.rss_mb),
            "hot_thread": p.hot.map(|(n, c)| json!({"name": n, "cores": r3(c)})),
        },
        "totals": {
            "records": t_rec, "dups": t_dup, "raw": t_raw, "raw_copies": t_rawc, "rows": t_rows, "rx_clamped": t_clamp,
            "sink_errors": sink_errors, "sink_blocks_dropped": blocks_dropped, "files_closed": files_closed,
        },
        "shards": per_shard,
        "pool": ctx.live.as_ref().map(|l| l.json(now_ms)),
    })
}
