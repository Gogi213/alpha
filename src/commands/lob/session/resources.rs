//! Фоновые замеры сессии вне горячего пути: `clock.csv` (NTP/serverTime,
//! свой ОС-поток, A9), ряд CPU/RSS `session.json.samples` по расписанию
//! `resource_sample_period`, `sample_resources` по ОС и час старта UTC.
//! Отдельно — сеть и вызовы ОС, которым в цикле событий не место.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crate::bybit::clock::{append_row, sample, BybitServerTimeSource, ClockRow, UdpNtpSource};
use crate::bybit::conn::{Clock, SystemClock};
use crate::commands::record::{ts_utc_of_ns, HOURLY_REFRESH_SECS};

use super::ResourceSample;

/// Дописывает один замер в `clock.csv` — best-effort: провал замера не
/// роняет сессию, только не даёт строки. Сеть (NTP, REST) — **не** в
/// событийном цикле (A9, запрет 4): зовётся из `spawn_clock_sampler` (свой
/// ОС-поток, раз в `HOURLY_REFRESH_SECS`) и один раз после цикла.
pub(super) fn take_clock_sample(
    ntp_addr: &str,
    base_url: &str,
    idx: u64,
    clock_csv: &Path,
) -> bool {
    let mut ntp = match UdpNtpSource::connect(ntp_addr, Duration::from_secs(2)) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("session: clock — NTP {ntp_addr} недоступен: {e:?}");
            return false;
        }
    };
    let mut bybit = match BybitServerTimeSource::new(base_url.to_string()) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("session: clock — Bybit serverTime недоступен: {e:?}");
            return false;
        }
    };
    let row: ClockRow = sample(idx, &SystemClock, &mut ntp, &mut bybit);
    match append_row(clock_csv, &row) {
        Ok(()) => true,
        Err(e) => {
            eprintln!("session: clock.csv не дописался: {e:?}");
            false
        }
    }
}

/// Часовой замер `clock.csv` в своём ОС-потоке (таск 25): первый — сразу,
/// дальше раз в `HOURLY_REFRESH_SECS` (тот же часовой таймер, что у
/// `lob record` — «`clock.csv` (шаг 0.5)», doc константы). Счётчик удачных
/// строк — наружу атомиком; поток не присоединяется, живёт до конца
/// процесса, как сэмплер ресурсов.
pub(super) fn spawn_clock_sampler(
    ntp_addr: String,
    base_url: String,
    clock_csv: PathBuf,
    count: Arc<AtomicU64>,
) {
    std::thread::spawn(move || loop {
        let idx = count.load(Ordering::Relaxed);
        if take_clock_sample(&ntp_addr, &base_url, idx, &clock_csv) {
            count.fetch_add(1, Ordering::Relaxed);
        }
        std::thread::sleep(Duration::from_secs(HOURLY_REFRESH_SECS));
    });
}

/// Час старта UTC (история 8, `R41`) — колонка запись о сессии обязана
/// нести. `chrono` уже в зависимостях (`interfaces.md`, §1).
pub(super) fn hour_utc_of_ns(ts_ns: i64) -> u32 {
    let secs = ts_ns.div_euclid(1_000_000_000);
    let nanos = ts_ns.rem_euclid(1_000_000_000) as u32;
    chrono::DateTime::from_timestamp(secs, nanos)
        .map(|dt| chrono::Timelike::hour(&dt))
        .unwrap_or(0)
}

/// Период замера CPU/RSS в первый час — гейт GC `PLAN.md` 6.1 дословно:
/// «RSS раз в 30 с в течение прогона, плоский». Тот же шаг, которым
/// измерены все прогоны до сих пор (таск 20, пилот, таск 24) — ряды
/// сравнимы между собой.
pub(super) const RESOURCE_SAMPLE_SECS: u64 = 30;

/// Расписание замеров ряда `samples` по прошедшему времени прогона (таск
/// 25, олвейс-он): первый час — раз в `RESOURCE_SAMPLE_SECS` (гейт 6.1,
/// окно, в котором и живут все 5-минутные замеры), дальше — раз в
/// `HOURLY_REFRESH_SECS`, вместе с переписыванием `session.json`. Без
/// прореживания ряд рос бы без потолка — 2 880 замеров в сутки, ≈ 130 Б
/// каждый в JSON и ≈ 100 Б в памяти: сотни КБ в сутки в файле, который
/// переписывается целиком каждый час, и медленный рост RSS у процесса,
/// чей гейт — «RSS плоский» (тот же довод, которым таск 24 заменил `Vec`
/// задержек гистограммой). Между двумя часовыми записями `session.json`
/// 30-секундный ряд до диска и так не доходил — часовая точка и есть
/// разрешение артефакта после первого часа; потолок — 120 + 24 замера в
/// сутки. Оба периода существующие, новых чисел нет.
pub(super) fn resource_sample_period(elapsed_s: u64) -> Duration {
    if elapsed_s < HOURLY_REFRESH_SECS {
        Duration::from_secs(RESOURCE_SAMPLE_SECS)
    } else {
        Duration::from_secs(HOURLY_REFRESH_SECS)
    }
}

/// Замер CPU (% одного ядра — критерий GC "< 5% ядра суммарно",
/// `PLAN.md`, раздел GC) и RSS по расписанию `resource_sample_period` — в
/// ряд `session.json.samples` (таск 25: не stderr — «одна строка в час», не
/// строка на замер). `cpu_pct` — среднее между двумя соседними замерами
/// ряда (30 с в первый час, час дальше). Фоновый ОС-поток — приём
/// `bybit::verify_sidecar` ("поток откреплён", `JoinHandle` наружу не идёт:
/// остановка вместе с процессом, а не по сигналу); не в горячем пути, туда
/// попадает только сон и один вызов ОС. Без сторонней зависимости
/// (`sysinfo` не в `Cargo.toml`, добавлять нельзя — `interfaces.md`): то,
/// что уже даёт ОС — `Get-Process` на Windows, `/proc/self/{stat,status}`
/// на Linux.
pub(super) fn spawn_resource_sampler(out: Arc<Mutex<Vec<ResourceSample>>>) {
    let pid = std::process::id();
    std::thread::spawn(move || {
        let started = std::time::Instant::now();
        let mut prev: Option<(std::time::Instant, f64)> = None;
        loop {
            std::thread::sleep(resource_sample_period(started.elapsed().as_secs()));
            let Some((cpu_seconds, rss_bytes)) = sample_resources(pid) else {
                eprintln!("session: замер CPU/RSS недоступен на этой ОС");
                return;
            };
            let now = std::time::Instant::now();
            let cpu_pct = prev.and_then(|(prev_at, prev_cpu)| {
                let wall_s = now.duration_since(prev_at).as_secs_f64();
                (wall_s > 0.0).then(|| (cpu_seconds - prev_cpu).max(0.0) / wall_s * 100.0)
            });
            prev = Some((now, cpu_seconds));
            if let Ok(mut v) = out.lock() {
                v.push(ResourceSample {
                    ts_utc: ts_utc_of_ns(SystemClock.now_ns()),
                    rss_bytes,
                    cpu_pct,
                });
            }
        }
    });
}

/// Кумулятивное время CPU в секундах (пользователь+система с момента
/// старта процесса) и RSS в байтах — пара, из которой `spawn_resource_
/// sampler` считает `%` делением дельты первого на дельту секунд между
/// замерами (то самое "измеримое — измеряется", а не готовый процент из
/// стороннего крейта).
#[cfg(target_os = "windows")]
pub(super) fn sample_resources(pid: u32) -> Option<(f64, u64)> {
    let script = format!(
        "(Get-Process -Id {pid} | Select-Object -Property \
         @{{n='cpu';e={{$_.TotalProcessorTime.TotalSeconds}}}}, \
         @{{n='rss';e={{$_.WorkingSet64}}}} | ConvertTo-Json -Compress)"
    );
    let out = std::process::Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-Command", &script])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&out.stdout);
    let v: serde_json::Value = serde_json::from_str(text.trim()).ok()?;
    let cpu = v.get("cpu")?.as_f64()?;
    let rss = v.get("rss")?.as_u64()?;
    Some((cpu, rss))
}

#[cfg(target_os = "linux")]
pub(super) fn sample_resources(_pid: u32) -> Option<(f64, u64)> {
    // 100 Гц — стандартная частота `USER_HZ` ядра Linux на x86/x86_64
    // (`sysconf(_SC_CLK_TCK)` в подавляющем большинстве сборок); это факт
    // ABI платформы, а не изобретённое число `interfaces.md` — доставать
    // настоящее значение потребовало бы `libc`, которого нет в
    // `Cargo.toml` (закрытый список зависимостей).
    const CLK_TCK: f64 = 100.0;
    let stat = std::fs::read_to_string("/proc/self/stat").ok()?;
    // `comm` (второе поле, в скобках) может содержать пробелы — считаем от
    // последней закрывающей скобки, не от фиксированного индекса.
    let after_comm = stat.rsplit_once(')')?.1;
    let fields: Vec<&str> = after_comm.split_whitespace().collect();
    // Поля `stat(5)` 1-based; после `)` индекс 0 — поле 3 (`state`), значит
    // `utime`/`stime` (поля 14/15) — индексы 11/12 здесь.
    let utime: f64 = fields.get(11)?.parse().ok()?;
    let stime: f64 = fields.get(12)?.parse().ok()?;
    let cpu_seconds = (utime + stime) / CLK_TCK;

    let status = std::fs::read_to_string("/proc/self/status").ok()?;
    let rss_kb: u64 = status
        .lines()
        .find_map(|l| l.strip_prefix("VmRSS:"))
        .and_then(|rest| rest.trim().split_whitespace().next())
        .and_then(|kb| kb.parse().ok())?;
    Some((cpu_seconds, rss_kb.saturating_mul(1024)))
}

#[cfg(not(any(target_os = "windows", target_os = "linux")))]
pub(super) fn sample_resources(_pid: u32) -> Option<(f64, u64)> {
    None
}

/// Свободное место файловой системы, на которой лежит `path`, в байтах
/// (A1, 2026-09-17). Сторож диска: без него полный диск гасит запись всех
/// монет сразу, и журнал потерь (`gaps.csv`) тоже перестаёт писаться —
/// отказ виден только по остановке роста бинлогов в дашборде (аудит V2/V6).
///
/// `statvfs` — вызов ОС, поэтому меряется на тике сессии (раз в
/// `record::FRAME_LOSS_WINDOW_SECS`), а не в событийном пути; `None` — замер
/// недоступен (не-Unix или путь не отвечает), и это не ошибка сессии.
#[cfg(unix)]
pub(super) fn disk_free_bytes(path: &Path) -> Option<u64> {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    let c_path = CString::new(path.as_os_str().as_bytes()).ok()?;
    // SAFETY: `statvfs` заполняет всю структуру и не хранит указатель;
    // `c_path` живёт до конца вызова, строка NUL-терминирована.
    let mut st: libc::statvfs = unsafe { std::mem::zeroed() };
    let rc = unsafe { libc::statvfs(c_path.as_ptr(), &mut st) };
    if rc != 0 {
        return None;
    }
    let block = st.f_frsize.max(st.f_bsize) as u64;
    Some((st.f_bavail as u64).saturating_mul(block))
}

/// Замер диска на не-Unix: тихий `None` — на рабочей машине (Windows) сторож
/// не нужен, и тесты гоняются там же (значение приходит параметром).
#[cfg(not(unix))]
pub(super) fn disk_free_bytes(_path: &Path) -> Option<u64> {
    None
}

/// Место ниже порога? Чистая функция от замера и порога — чтобы решение
/// сторожа было тестируемо там, где `statvfs` не запускается (и чтобы порог
/// менялся одним числом из `--disk-warn-gib`). Недоступный замер (`None`)
/// тревогой не считается: неизвестность — не полный диск.
pub(super) fn disk_low(free_bytes: Option<u64>, warn_bytes: u64) -> bool {
    free_bytes.is_some_and(|free| free < warn_bytes)
}
