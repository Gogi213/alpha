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
    json!({"p50": h.percentile(0.5).map(us_to_ms), "p99": h.percentile(0.99).map(us_to_ms), "window_s": WINDOW_S})
}

pub struct HealthCtx {
    pub exchange: &'static str,
    /// Пул в нижнем регистре (для «символов с данными»).
    pub pool: HashSet<String>,
    pub version: String,
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
    let mut lag_in = Hist::new();
    let mut lag_in_kind = [Hist::new(), Hist::new(), Hist::new()];
    let mut lag_out = Hist::new();
    let mut queue_lag = Hist::new();
    let mut fresh = [KindFresh::default(); 4];
    let mut fresh_seen = [false; 4];
    let (mut pings, mut connections) = (Vec::new(), 0i64);
    let (mut to, mut gp, mut rc, mut es, mut ps) =
        ([0u64; 2], [0u64; 2], [0u64; 2], [0u64; 2], [0u64; 2]);
    let (mut last_frame, mut last_write, mut started) = (0i64, 0i64, i64::MAX);
    let mut totals = [0u64; 6];
    let (mut sink_errors, mut files_closed) = (0u64, 0u64);
    let (mut depth_max, mut sink_depth_max, mut ring_cap, mut sink_cap) =
        (0i64, 0i64, 0usize, 0usize);
    let (mut full_total, mut blocked_ms_total, mut full_win) = (0u64, 0u64, 0u64);
    let (mut recvq_max, mut recvq_s, mut recvq_b, mut busy_max, mut handoff_max, mut write_max) =
        (0u64, 0u64, 0u64, 0u64, 0u64, 0u64);
    let mut per_shard = Vec::new();
    for m in shards {
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
                    (&mut gp, &l.gaps),
                    (&mut rc, &l.reconnects),
                    (&mut es, &l.entrance_switches),
                    (&mut ps, &l.path_switches),
                ] {
                    if let Some(a) = acc.get_mut(i) {
                        *a += c(dq);
                    }
                }
            }
            pings.extend(l.pings.iter().map(|p| p.1));
            l.trim(now_ms);
        }
        let shard_lag = {
            let mut w = m.writer();
            for (low, last) in w.seen.values() {
                if now_ms - last <= WINDOW_S * 1000 && ctx.pool.contains(low) {
                    with_data.insert(low.clone());
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
            sl
        };
        {
            let s = m.sink();
            lag_out.merge(&s.lag_out.snapshot(now_ms));
            last_write = last_write.max(s.last_write_ms);
            sink_errors += s.errors;
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
        per_shard.push(json!({
            "shard": m.name, "connected": m.connected.load(std::sync::atomic::Ordering::Relaxed), "msgs_10s": w.frames,
            "lag_in_p99_ms": shard_lag.percentile(0.99).map(us_to_ms), "ring_depth_max": w.ring_depth_max, "recvq_max": w.recvq_max, "busy_max_us": w.busy_max_us,
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
    let [gp1, gp24] = gp;
    let [rc1, rc24] = rc;
    let [es1, es24] = es;
    let [ps1, ps24] = ps;
    let [t_rec, t_dup, t_raw, t_rawc, t_rows, t_clamp] = totals;
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
        "symbols": {"subscribed": ctx.pool.len(), "with_data_1m": with_data.len()},
        "ws": {
            "connections": connections, "ping_ms": ping,
            "timeouts_1h": to1, "timeouts_24h": to24, "gaps_1h": gp1, "gaps_24h": gp24, "reconnects_1h": rc1, "reconnects_24h": rc24,
            "entrance_switches_1h": es1, "entrance_switches_24h": es24, "path_switches_1h": ps1, "path_switches_24h": ps24,
        },
        "fresh": {"late_ms": LATE_MS, "kinds": kinds},
        "lag_in_ms": pct_json(&lag_in),
        "lag_in_by_kind_ms": {"book": pct_json(lag_book), "trade": pct_json(lag_trade), "mark": pct_json(lag_mark)},
        "lag_out_ms": pct_json(&lag_out),
        "last_frame_ms": last_frame, "last_write_ms": last_write,
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
            "sink_errors": sink_errors, "files_closed": files_closed,
        },
        "shards": per_shard,
    })
}
