//! TK-052 (В-187): сквозной учёт времени монето-суток. `ALPHA_E2E=<файл.jsonl>` — по строке на стадию;
//! без переменной `Mark::now()` отдаёт `None` и ничего не делается. Таймеры только на границах стадий.
use serde_json::{json, Map, Value};
use std::io::Write;
use std::sync::{Mutex, OnceLock};
use std::time::Instant;

static SINK: OnceLock<Option<Mutex<std::fs::File>>> = OnceLock::new();

fn sink() -> Option<&'static Mutex<std::fs::File>> {
    SINK.get_or_init(|| {
        let path = std::env::var_os("ALPHA_E2E")?;
        match std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
        {
            Ok(f) => Some(Mutex::new(f)),
            Err(e) => {
                eprintln!(
                    "bounce-grid: ALPHA_E2E {}: {e}, учёт выключен",
                    path.to_string_lossy()
                );
                None
            }
        }
    })
    .as_ref()
}

pub(super) fn on() -> bool {
    sink().is_some()
}

#[cfg(target_os = "linux")]
fn clock_s(id: i32) -> f64 {
    #[repr(C)]
    struct Timespec {
        sec: i64,
        nsec: i64,
    }
    extern "C" {
        fn clock_gettime(clk: i32, ts: *mut Timespec) -> i32;
    }
    let mut ts = Timespec { sec: 0, nsec: 0 };
    // SAFETY: ts — валидная структура layout timespec на 64-бит Linux; вызов только пишет в неё.
    let rc = unsafe { clock_gettime(id, &mut ts) };
    if rc == 0 {
        ts.sec as f64 + ts.nsec as f64 * 1e-9
    } else {
        0.0
    }
}
#[cfg(not(target_os = "linux"))]
fn clock_s(_id: i32) -> f64 {
    0.0
}

/// (rchar, read_bytes) процесса из `/proc/self/io`; без файла — нули.
fn io_bytes() -> (u64, u64) {
    let Ok(s) = std::fs::read_to_string("/proc/self/io") else {
        return (0, 0);
    };
    let get = |k: &str| {
        s.lines()
            .find_map(|l| l.strip_prefix(k))
            .and_then(|v| v.trim().parse().ok())
            .unwrap_or(0)
    };
    (get("rchar:"), get("read_bytes:"))
}

/// Пик RSS процесса, КиБ (`VmHWM`).
fn rss_peak_kib() -> u64 {
    std::fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|s| {
            s.lines()
                .find_map(|l| l.strip_prefix("VmHWM:"))
                .and_then(|v| v.split_whitespace().next()?.parse().ok())
        })
        .unwrap_or(0)
}

#[derive(Clone, Copy)]
pub(super) struct Mark {
    wall: Instant,
    cpu_proc: f64,
    cpu_thread: f64,
    rchar: u64,
    read_bytes: u64,
}

impl Mark {
    pub(super) fn now() -> Option<Mark> {
        sink()?;
        let (rchar, read_bytes) = io_bytes();
        Some(Mark {
            wall: Instant::now(),
            cpu_proc: clock_s(2),
            cpu_thread: clock_s(3),
            rchar,
            read_bytes,
        })
    }
}

fn emit(v: Value) {
    if let Some(m) = sink() {
        if let Ok(mut f) = m.lock() {
            let _ = writeln!(f, "{v}");
        }
    }
}

/// Строка стадии: стена, ЦП процесса (все потоки), ЦП вызывающего потока, байты чтения с момента `start`.
pub(super) fn stage(symbol: &str, day: &str, name: &str, start: Option<Mark>, extra: Value) {
    let (Some(s), Some(e)) = (start, Mark::now()) else {
        return;
    };
    let mut o = Map::new();
    o.insert("t".into(), json!("stage"));
    o.insert("sym".into(), json!(symbol));
    o.insert("day".into(), json!(day));
    o.insert("stage".into(), json!(name));
    o.insert(
        "wall".into(),
        json!(e.wall.duration_since(s.wall).as_secs_f64()),
    );
    o.insert("cpu".into(), json!(e.cpu_proc - s.cpu_proc));
    o.insert("cpu_thread".into(), json!(e.cpu_thread - s.cpu_thread));
    o.insert("rchar".into(), json!(e.rchar.saturating_sub(s.rchar)));
    o.insert(
        "read_bytes".into(),
        json!(e.read_bytes.saturating_sub(s.read_bytes)),
    );
    if let Value::Object(x) = extra {
        o.extend(x);
    }
    emit(Value::Object(o));
}

/// Строка «сутки»: счётчики и пик RSS.
pub(super) fn day_row(symbol: &str, day: &str, mut extra: Map<String, Value>) {
    if !on() {
        return;
    }
    extra.insert("t".into(), json!("day"));
    extra.insert("sym".into(), json!(symbol));
    extra.insert("day".into(), json!(day));
    extra.insert("rss_peak_kib".into(), json!(rss_peak_kib()));
    emit(Value::Object(extra));
}

/// Строка «запуск» / «итог» процесса.
pub(super) fn run_row(kind: &str, extra: Value) {
    if !on() {
        return;
    }
    let mut o = Map::new();
    o.insert("t".into(), json!(kind));
    o.insert("pid".into(), json!(std::process::id()));
    o.insert("cpu".into(), json!(clock_s(2)));
    o.insert("rss_peak_kib".into(), json!(rss_peak_kib()));
    if let Value::Object(x) = extra {
        o.extend(x);
    }
    emit(Value::Object(o));
}
