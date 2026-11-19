//! TK-607: однократное заполнение склада суточных сделок по каталогам zip (`alpha breakout store-fill`).
//!
//! Задание — пара `(монета, дата)` из каталогов `<МОНЕТА>/<МОНЕТА>-aggTrades-<дата>.zip` (повторяющиеся пары разных каталогов
//! сводятся к одной; разные исходные файлы под одной парой — конфликт, он попадает в отчёт). На задание: zip читается в память,
//! разжимается в процессе (libdeflate; размеры и CRC32 берутся из центрального каталога zip, CRC сверяется), один разбор движка
//! (`parse_trades_full`), запись в склад. Целостность — CRC zip и число строк в заголовке склада (после записи файл проверяется
//! по заголовку и длине, без разбора блоков). Иное устройство zip (zip64, несколько записей, шифрование) — внешний `unzip -p`.
//! Уже лежащие файлы (тот же размер и mtime zip, длина сходится с заголовком) пропускаются по заголовку. Запись прекращается
//! при свободном месте < `min_free_gb` (задание тогда считается ошибкой записи, прогон останавливается).
//!
//! Начало сетки (`P42_T0`) должно быть не позже самой ранней даты заданий: файлы склада от него не зависят (`off` — от полуночи
//! даты файла), он нужен только для номера суток.

use super::data;
use super::store::{SrcMeta, StoreOpts, TickStore, F_PX_INEXACT, F_QTY_BAD, F_QTY_F64};
use super::tick::{parse_trades_full, LoadStat};
use anyhow::{bail, Context, Result};
use libdeflater::{Crc, Decompressor};
use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::fs;
use std::io::Cursor;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub struct FillOpts {
    /// Каталоги zip `<МОНЕТА>/<МОНЕТА>-aggTrades-<дата>.zip`.
    pub roots: Vec<PathBuf>,
    pub store: StoreOpts,
    pub threads: usize,
    /// Каталог отчёта: `report.txt`, `errors.txt`.
    pub report: PathBuf,
    /// Только первые `limit` заданий (проверка на малом).
    pub limit: Option<usize>,
}

struct Job {
    name: String,
    date: String,
    day: usize,
    zip: PathBuf,
}

#[derive(Default)]
struct Stats {
    done: AtomicU64,
    written: AtomicU64,
    skipped: AtomicU64,
    rows: AtomicU64,
    flag_px: AtomicU64,
    flag_qty: AtomicU64,
    unsorted_files: AtomicU64,
    bad_rows: AtomicU64,
    out_of_day: AtomicU64,
    unzip_fallback: AtomicU64,
    errors: AtomicU64,
    /// Время по стадиям, нс, сумма по потокам: чтение zip и разжатие / разбор / запись.
    ns_inflate: AtomicU64,
    ns_parse: AtomicU64,
    ns_write: AtomicU64,
}

fn add(c: &AtomicU64, v: u64) {
    c.fetch_add(v, Ordering::Relaxed);
}

fn add_time(c: &AtomicU64, t: Instant) {
    add(c, u64::try_from(t.elapsed().as_nanos()).unwrap_or(u64::MAX));
}

fn get(c: &AtomicU64) -> u64 {
    c.load(Ordering::Relaxed)
}

fn secs(ns: u64) -> f64 {
    Duration::from_nanos(ns).as_secs_f64()
}

fn list_jobs(roots: &[PathBuf]) -> Result<(Vec<(String, String, PathBuf)>, Vec<String>)> {
    let mut map: BTreeMap<(String, String), PathBuf> = BTreeMap::new();
    let mut conflicts = Vec::new();
    for root in roots {
        let rd = fs::read_dir(root).with_context(|| format!("каталог {}", root.display()))?;
        for coin in rd.flatten() {
            let name = coin.file_name().to_string_lossy().into_owned();
            let Ok(files) = fs::read_dir(coin.path()) else {
                continue;
            };
            let prefix = format!("{name}-aggTrades-");
            for f in files.flatten() {
                let fname = f.file_name().to_string_lossy().into_owned();
                let Some(date) = fname
                    .strip_prefix(&prefix)
                    .and_then(|r| r.strip_suffix(".zip"))
                else {
                    continue;
                };
                let key = (name.clone(), date.to_string());
                match map.get(&key) {
                    None => {
                        map.insert(key, f.path());
                    }
                    Some(first) => {
                        let m = |p: &Path| fs::metadata(p).ok().and_then(|m| SrcMeta::of(&m));
                        if m(first) != m(&f.path()) {
                            conflicts.push(format!(
                                "{} {}: {} != {}",
                                key.0,
                                key.1,
                                first.display(),
                                f.path().display()
                            ));
                        }
                    }
                }
            }
        }
    }
    Ok((
        map.into_iter().map(|((n, d), p)| (n, d, p)).collect(),
        conflicts,
    ))
}

fn le16(b: &[u8], o: usize) -> Option<usize> {
    Some(usize::from(u16::from_le_bytes(
        b.get(o..o + 2)?.try_into().ok()?,
    )))
}

fn le32(b: &[u8], o: usize) -> Option<u32> {
    Some(u32::from_le_bytes(b.get(o..o + 4)?.try_into().ok()?))
}

/// Разжатие zip с одной записью (метод 0 или 8) в процессе. Размеры и CRC32 — из центрального каталога; CRC сверяется.
/// `Ok(None)` — устройство zip не то (несколько записей, zip64, шифрование, иной метод): вызывающий берёт `unzip -p`.
/// `Err` — zip повреждён (границы, размер, CRC).
fn inflate_zip(z: &[u8], dec: &mut Decompressor) -> Result<Option<Vec<u8>>, String> {
    const TRUNC: &str = "обрезан центральный каталог";
    let Some(eocd) = (z.len().saturating_sub(22 + 65_535)..=z.len().saturating_sub(22))
        .rev()
        .find(|&i| le32(z, i) == Some(0x0605_4b50))
    else {
        return Err("нет конца центрального каталога".into());
    };
    let (Some(entries), Some(cd_off)) = (le16(z, eocd + 10), le32(z, eocd + 16)) else {
        return Err(TRUNC.into());
    };
    if entries != 1 || cd_off == u32::MAX {
        return Ok(None);
    }
    let cd = cd_off as usize;
    if le32(z, cd) != Some(0x0201_4b50) {
        return Err("центральный каталог: сигнатура".into());
    }
    let (Some(flags), Some(method)) = (le16(z, cd + 8), le16(z, cd + 10)) else {
        return Err(TRUNC.into());
    };
    let (Some(crc), Some(csize), Some(usize_), Some(local)) = (
        le32(z, cd + 16),
        le32(z, cd + 20),
        le32(z, cd + 24),
        le32(z, cd + 42),
    ) else {
        return Err(TRUNC.into());
    };
    if flags & 1 != 0 || ![0, 8].contains(&method) || [csize, usize_, local].contains(&u32::MAX) {
        return Ok(None);
    }
    let lh = local as usize;
    if le32(z, lh) != Some(0x0403_4b50) {
        return Err("локальный заголовок: сигнатура".into());
    }
    let (Some(n), Some(e)) = (le16(z, lh + 26), le16(z, lh + 28)) else {
        return Err("обрезан локальный заголовок".into());
    };
    let start = lh + 30 + n + e;
    let Some(body) = z.get(start..start + csize as usize) else {
        return Err("данные за границей файла".into());
    };
    let out = if method == 0 {
        body.to_vec()
    } else {
        let mut out = vec![0u8; usize_ as usize];
        let got = dec
            .deflate_decompress(body, &mut out)
            .map_err(|e| format!("разжатие: {e:?}"))?;
        if got != out.len() {
            return Err(format!("размер {got} != {} по каталогу", out.len()));
        }
        out
    };
    if out.len() != usize_ as usize {
        return Err("размер без сжатия не сходится с каталогом".into());
    }
    let mut c = Crc::new();
    c.update(&out);
    if c.sum() != crc {
        return Err("CRC32 не сходится".into());
    }
    Ok(Some(out))
}

fn unzip_p(zip: &Path) -> Result<Vec<u8>, String> {
    let out = Command::new("unzip")
        .arg("-p")
        .arg(zip)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
        .map_err(|e| format!("unzip: {e}"))?;
    if !out.status.success() {
        return Err(format!("unzip: {}", out.status));
    }
    Ok(out.stdout)
}

/// Одно задание. `Err` — описание ошибки задания.
fn one(
    store: &TickStore,
    job: &Job,
    st: &Stats,
    abort: &AtomicBool,
    dec: &mut Decompressor,
) -> Result<(), String> {
    let md = fs::metadata(&job.zip).map_err(|e| format!("metadata: {e}"))?;
    let meta = SrcMeta::of(&md).ok_or("mtime zip")?;
    if let Some(n) = store.valid_rows(&job.name, &job.date, &meta) {
        add(&st.skipped, 1);
        add(&st.rows, n);
        return Ok(());
    }
    let t = Instant::now();
    let z = fs::read(&job.zip).map_err(|e| format!("чтение zip: {e}"))?;
    let csv = match inflate_zip(&z, dec)? {
        Some(c) => c,
        None => {
            add(&st.unzip_fallback, 1);
            unzip_p(&job.zip)?
        }
    };
    drop(z);
    add_time(&st.ns_inflate, t);
    let t = Instant::now();
    let mut d = parse_trades_full(Cursor::new(&csv[..]), job.day, LoadStat::default())
        .map_err(|e| format!("разбор: {e:#}"))?;
    d.stat.files = 1;
    drop(csv);
    add_time(&st.ns_parse, t);
    let t = Instant::now();
    store.write(&job.name, &job.date, &meta, &d);
    let ok = store.valid_rows(&job.name, &job.date, &meta) == Some(d.ticks.len() as u64);
    add_time(&st.ns_write, t);
    if !ok {
        if store.is_read_only() {
            abort.store(true, Ordering::Relaxed);
        }
        return Err("файл склада не записан (потолок/место/ошибка записи)".into());
    }
    add(&st.written, 1);
    add(&st.rows, d.ticks.len() as u64);
    add(&st.flag_px, u64::from(d.flags & F_PX_INEXACT != 0));
    add(
        &st.flag_qty,
        u64::from(d.flags & (F_QTY_BAD | F_QTY_F64) != 0),
    );
    add(&st.unsorted_files, d.stat.unsorted_files);
    add(&st.bad_rows, d.stat.bad_rows);
    add(&st.out_of_day, d.stat.out_of_day);
    Ok(())
}

pub fn run(o: &FillOpts) -> Result<()> {
    let t_start = Instant::now();
    let (mut list, conflicts) = list_jobs(&o.roots)?;
    if let Some(n) = o.limit {
        list.truncate(n);
    }
    let t0 = chrono::NaiveDate::parse_from_str(&data::day_string(0), "%Y-%m-%d")
        .context("P42_T0 не разобран")?;
    let mut jobs = Vec::with_capacity(list.len());
    for (name, date, zip) in list {
        let d = chrono::NaiveDate::parse_from_str(&date, "%Y-%m-%d")
            .with_context(|| format!("дата {date} в имени zip {}", zip.display()))?;
        let Ok(day) = usize::try_from((d - t0).num_days()) else {
            bail!("{name} {date}: раньше начала сетки {t0}; задайте P42_T0 не позже самой ранней даты заданий");
        };
        jobs.push(Job {
            name,
            date,
            day,
            zip,
        });
    }
    fs::create_dir_all(&o.report).context("каталог отчёта")?;
    let store = TickStore::open(&o.store).context("склад")?;
    println!(
        "store-fill: заданий {} (конфликтов источников {}), потоков {}, склад {}, свободное место не ниже {} ГБ",
        jobs.len(),
        conflicts.len(),
        o.threads,
        o.store.dir.display(),
        o.store.min_free_gb
    );
    let (next, st, abort) = (
        AtomicUsize::new(0),
        Stats::default(),
        AtomicBool::new(false),
    );
    let errs: Mutex<Vec<String>> = Mutex::new(Vec::new());
    let total = jobs.len();
    std::thread::scope(|sc| {
        for _ in 0..o.threads.max(1) {
            sc.spawn(|| {
                let mut dec = Decompressor::new();
                loop {
                    if abort.load(Ordering::Relaxed) {
                        break;
                    }
                    let i = next.fetch_add(1, Ordering::Relaxed);
                    let Some(job) = jobs.get(i) else {
                        break;
                    };
                    if let Err(e) = one(&store, job, &st, &abort, &mut dec) {
                        add(&st.errors, 1);
                        if let Ok(mut g) = errs.lock() {
                            g.push(format!("{} {}: {e}", job.name, job.date));
                        }
                    }
                    let n = get(&st.done) + 1;
                    add(&st.done, 1);
                    if n % 2000 == 0 {
                        let el = t_start.elapsed().as_secs_f64();
                        println!(
                            "store-fill: {n}/{total} за {el:.0} с ({:.1} файл/с, {:.2} млн строк/с), записано {}, пропущено {}, строк {}, ошибок {}, склад {:.2} ГБ; время потоков, с: чтение+разжатие {:.0}, разбор {:.0}, запись {:.0}",
                            n as f64 / el.max(1e-9),
                            get(&st.rows) as f64 / el.max(1e-9) / 1e6,
                            get(&st.written),
                            get(&st.skipped),
                            get(&st.rows),
                            get(&st.errors),
                            store.counters().used_bytes as f64 / f64::from(1u32 << 30),
                            secs(get(&st.ns_inflate)),
                            secs(get(&st.ns_parse)),
                            secs(get(&st.ns_write))
                        );
                    }
                }
            });
        }
    });
    let errs = errs.into_inner().unwrap_or_default();
    let aborted = abort.load(Ordering::Relaxed);
    let done = get(&st.done);
    let c = store.counters();
    let mut line = format!(
        "STOREFILL {} заданий={total} обработано={done} записано={} пропущено_лежащих={} строк={} ошибок={} конфликтов_источников={} через_unzip={} флаг_цены={} флаг_количества={} не_по_порядку={} плохих_строк={} вне_суток={} склад_байт={} блок_количества_байт={} время_потоков_с(чтение+разжатие/разбор/запись)={:.0}/{:.0}/{:.0} за {:.0} с",
        if aborted || done < total as u64 { "ОСТАНОВЛЕНО" } else { "DONE" },
        get(&st.written),
        get(&st.skipped),
        get(&st.rows),
        get(&st.errors),
        conflicts.len(),
        get(&st.unzip_fallback),
        get(&st.flag_px),
        get(&st.flag_qty),
        get(&st.unsorted_files),
        get(&st.bad_rows),
        get(&st.out_of_day),
        c.used_bytes,
        c.qty_bytes,
        secs(get(&st.ns_inflate)),
        secs(get(&st.ns_parse)),
        secs(get(&st.ns_write)),
        t_start.elapsed().as_secs_f64()
    );
    let _ = write!(line, "\n{}", store.summary());
    println!("{line}");
    fs::write(o.report.join("report.txt"), format!("{line}\n")).context("report.txt")?;
    let mut e = String::new();
    for x in conflicts
        .iter()
        .map(|c| format!("КОНФЛИКТ {c}"))
        .chain(errs)
    {
        let _ = writeln!(e, "{x}");
    }
    fs::write(o.report.join("errors.txt"), e).context("errors.txt")?;
    if aborted {
        bail!("остановлено: склад не принимает запись (см. report.txt)");
    }
    Ok(())
}
