//! TK-607: однократное заполнение склада суточных сделок по каталогам zip (`alpha breakout store-fill`) со сверкой на лету.
//!
//! Задание — пара `(монета, дата)` из каталогов `<МОНЕТА>/<МОНЕТА>-aggTrades-<дата>.zip` (повторяющиеся пары разных каталогов
//! сводятся к одной; разные исходные файлы под одной парой — конфликт, он попадает в отчёт). Для каждого задания: `unzip -p`
//! один раз в память, разбор движка (`parse_trades_full`), запись в склад, чтение записанного файла обратно и три сверки:
//! (1) разобранное движком = прочитанному со склада (сделки, количество, масштаб, флаги, счётчики); (2) независимый разбор CSV
//! стандартной библиотекой (`str::parse::<f64>`) = цена и количество со склада побитово (при нулевых флагах точности), порядок,
//! `off`, `bm` — как `window_quantity_matches_csv`; (3) число строк. Уже лежащие файлы пропускаются (`verify_hits` — проверяются
//! так же). Различие — файл склада удаляется, задание попадает в отчёт. Запись прекращается при свободном месте < `min_free_gb`
//! (задание тогда считается ошибкой записи, прогон останавливается).
//!
//! Начало сетки (`P42_T0`) должно быть не позже самой ранней даты заданий: файлы склада от него не зависят (`off` — от полуночи
//! даты файла), он нужен только для номера суток.

use super::data;
use super::store::{DayTrades, SrcMeta, StoreOpts, TickStore, F_PX_INEXACT, F_QTY_BAD, F_QTY_F64};
use super::tick::{parse_trades_full, LoadStat, DAY_MS};
use anyhow::{bail, Context, Result};
use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::fs;
use std::io::Cursor;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::Instant;

pub struct FillOpts {
    /// Каталоги zip `<МОНЕТА>/<МОНЕТА>-aggTrades-<дата>.zip`.
    pub roots: Vec<PathBuf>,
    pub store: StoreOpts,
    pub threads: usize,
    /// Проверять и уже лежащие файлы склада (разбор zip и сверка); иначе они пропускаются.
    pub verify_hits: bool,
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
    verified_hits: AtomicU64,
    skipped_hits: AtomicU64,
    rows: AtomicU64,
    csv_checked: AtomicU64,
    flag_px: AtomicU64,
    flag_qty: AtomicU64,
    unsorted_files: AtomicU64,
    bad_rows: AtomicU64,
    out_of_day: AtomicU64,
    mismatches: AtomicU64,
    errors: AtomicU64,
}

fn add(c: &AtomicU64, v: u64) {
    c.fetch_add(v, Ordering::Relaxed);
}

fn get(c: &AtomicU64) -> u64 {
    c.load(Ordering::Relaxed)
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

/// Независимый от `dec_parts`/`parse_px` разбор CSV стандартной библиотекой против суток со склада.
fn csv_check(csv: &[u8], base: i64, w: &DayTrades) -> Result<(), String> {
    let mut want: Vec<(u32, bool, f64, f64)> = Vec::with_capacity(w.ticks.len());
    let (mut prev, mut unsorted) = (0u32, false);
    let num = |b: &[u8]| {
        std::str::from_utf8(b)
            .ok()
            .and_then(|s| s.parse::<f64>().ok())
    };
    for line in csv.split(|&b| b == b'\n') {
        if !line.first().is_some_and(u8::is_ascii_digit) {
            continue;
        }
        let mut f = line.split(|&b| b == b',');
        let (Some(_), Some(p), Some(q), Some(_), Some(_), Some(t), Some(b)) = (
            f.next(),
            f.next(),
            f.next(),
            f.next(),
            f.next(),
            f.next(),
            f.next(),
        ) else {
            continue;
        };
        let (Some(p), Some(q), Some(ts)) = (
            num(p),
            num(q),
            std::str::from_utf8(t)
                .ok()
                .and_then(|s| s.parse::<i64>().ok()),
        ) else {
            continue;
        };
        let off = ts - base;
        if !(0..DAY_MS).contains(&off) {
            continue;
        }
        let off = off as u32;
        unsorted |= off < prev;
        prev = off;
        want.push((off, b.first() == Some(&b't'), p, q));
    }
    if unsorted {
        want.sort_by_key(|x| x.0);
    }
    if w.ticks.len() != want.len() || w.qty.len() != want.len() {
        return Err(format!(
            "строк {} (склад) против {} (CSV)",
            w.ticks.len(),
            want.len()
        ));
    }
    let (fpx, fq) = (
        w.flags & F_PX_INEXACT != 0,
        w.flags & (F_QTY_BAD | F_QTY_F64) != 0,
    );
    for (i, &(off, bm, p, q)) in want.iter().enumerate() {
        if w.ticks[i].off != off || w.ticks[i].bm != bm {
            return Err(format!("строка {i}: off/bm"));
        }
        if !fq && w.qty_f64(i).to_bits() != q.to_bits() {
            return Err(format!("строка {i}: количество {} != {q}", w.qty_f64(i)));
        }
        if !fpx && w.px_f64(i).to_bits() != p.to_bits() {
            return Err(format!("строка {i}: цена {} != {p}", w.px_f64(i)));
        }
    }
    Ok(())
}

fn same_stat(a: &LoadStat, b: &LoadStat) -> bool {
    (a.rows, a.unsorted_files, a.out_of_day, a.bad_rows)
        == (b.rows, b.unsorted_files, b.out_of_day, b.bad_rows)
}

/// Одно задание. `Err` — описание; различие (а не сбой чтения/записи) дополнительно считается в `mismatches`.
fn one(
    store: &TickStore,
    job: &Job,
    verify_hits: bool,
    st: &Stats,
    abort: &AtomicBool,
) -> Result<(), String> {
    let md = fs::metadata(&job.zip).map_err(|e| format!("metadata: {e}"))?;
    let meta = SrcMeta::of(&md).ok_or("mtime zip")?;
    let hit = store.read_day(&job.name, &job.date, &meta);
    if let (Some(h), false) = (&hit, verify_hits) {
        add(&st.skipped_hits, 1);
        add(&st.rows, h.ticks.len() as u64);
        return Ok(());
    }
    let out = Command::new("unzip")
        .arg("-p")
        .arg(&job.zip)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
        .map_err(|e| format!("unzip: {e}"))?;
    if !out.status.success() {
        return Err(format!("unzip: {}", out.status));
    }
    let mut d = parse_trades_full(Cursor::new(&out.stdout[..]), job.day, LoadStat::default())
        .map_err(|e| format!("разбор: {e:#}"))?;
    d.stat.files = 1;
    let (rd, was_hit) = match hit {
        Some(h) => (h, true),
        None => {
            store.write(&job.name, &job.date, &meta, &d);
            match store.read_day(&job.name, &job.date, &meta) {
                Some(x) => (x, false),
                None => {
                    if store.is_read_only() {
                        abort.store(true, Ordering::Relaxed);
                    }
                    return Err(
                        "записанный файл не читается (запись отклонена: потолок/место/ошибка)"
                            .into(),
                    );
                }
            }
        }
    };
    let base = data::t0_ms() + job.day as i64 * DAY_MS;
    let bad = if d.ticks != rd.ticks
        || d.qty != rd.qty
        || d.qty_dec != rd.qty_dec
        || d.flags != rd.flags
        || !same_stat(&d.stat, &rd.stat)
    {
        Some("разобранное движком != прочитанному со склада".to_string())
    } else {
        csv_check(&out.stdout, base, &rd).err()
    };
    if let Some(e) = bad {
        let _ = fs::remove_file(store.path(&job.name, &job.date));
        add(&st.mismatches, 1);
        return Err(e);
    }
    add(&st.csv_checked, 1);
    add(
        if was_hit {
            &st.verified_hits
        } else {
            &st.written
        },
        1,
    );
    add(&st.rows, rd.ticks.len() as u64);
    add(&st.flag_px, u64::from(rd.flags & F_PX_INEXACT != 0));
    add(
        &st.flag_qty,
        u64::from(rd.flags & (F_QTY_BAD | F_QTY_F64) != 0),
    );
    add(&st.unsorted_files, rd.stat.unsorted_files);
    add(&st.bad_rows, rd.stat.bad_rows);
    add(&st.out_of_day, rd.stat.out_of_day);
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
        "store-fill: заданий {} (конфликтов источников {}), потоков {}, склад {}, свободное место не ниже {} ГБ, проверка лежащих файлов: {}",
        jobs.len(),
        conflicts.len(),
        o.threads,
        o.store.dir.display(),
        o.store.min_free_gb,
        o.verify_hits
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
            sc.spawn(|| loop {
                if abort.load(Ordering::Relaxed) {
                    break;
                }
                let i = next.fetch_add(1, Ordering::Relaxed);
                let Some(job) = jobs.get(i) else {
                    break;
                };
                if let Err(e) = one(&store, job, o.verify_hits, &st, &abort) {
                    add(&st.errors, 1);
                    if let Ok(mut g) = errs.lock() {
                        g.push(format!("{} {}: {e}", job.name, job.date));
                    }
                }
                let n = get(&st.done) + 1;
                add(&st.done, 1);
                if n % 500 == 0 {
                    let el = t_start.elapsed().as_secs_f64();
                    println!(
                        "store-fill: {n}/{total} за {el:.0} с ({:.1} файл/с), записано {}, пропущено {}, строк {}, различий {}, ошибок {}, склад {:.2} ГБ",
                        n as f64 / el.max(1e-9),
                        get(&st.written),
                        get(&st.skipped_hits),
                        get(&st.rows),
                        get(&st.mismatches),
                        get(&st.errors),
                        store.counters().used_bytes as f64 / f64::from(1u32 << 30)
                    );
                }
            });
        }
    });
    let errs = errs.into_inner().unwrap_or_default();
    let aborted = abort.load(Ordering::Relaxed);
    let done = get(&st.done);
    let c = store.counters();
    let mut line = format!(
        "STOREFILL {} заданий={total} обработано={done} записано={} проверено_лежащих={} пропущено_лежащих={} сверено_с_CSV={} строк={} различий={} ошибок={} конфликтов_источников={} флаг_цены={} флаг_количества={} не_по_порядку={} плохих_строк={} вне_суток={} склад_байт={} блок_количества_байт={} за {:.0} с",
        if aborted || done < total as u64 { "ОСТАНОВЛЕНО" } else { "DONE" },
        get(&st.written),
        get(&st.verified_hits),
        get(&st.skipped_hits),
        get(&st.csv_checked),
        get(&st.rows),
        get(&st.mismatches),
        get(&st.errors),
        conflicts.len(),
        get(&st.flag_px),
        get(&st.flag_qty),
        get(&st.unsorted_files),
        get(&st.bad_rows),
        get(&st.out_of_day),
        c.used_bytes,
        c.qty_bytes,
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
