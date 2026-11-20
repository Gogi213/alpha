//! TK-638: число сделок монеты перед сигналом — `n60` и `n24` в тех же окнах, что ключи пула `tick` (`t1h`, `t24`): закрытые минуты строго раньше
//! минуты сигнала `t = ⌊sig_ms / 60 000⌋` — `[(t − 60)·60 000, t·60 000)` и `[(t − 1 440)·60 000, t·60 000)` (`tick::candidates`: `pq[t] − pq[t − N]`).
//!
//! Вход: CSV с заголовком, столбцы `sym` и `sig_ms` (мс от `--base-date`, как у таблиц признаков П-42; другие столбцы игнорируются).
//! Выход: `sym,sig_ms,n60,n24,m60,m24` в порядке входа; `m60` / `m24` = 1, если для окна 60 мин / 24 ч нет файла суток, которые окно задевает:
//! счёт идёт по тому, что есть, решение «пусто / не пусто» принимает вызывающий код по флагам.
//! Считаются записи aggTrades (`transact_time`); источник суток — `PxSrc` f12 (zip или склад TK-607 на чтение).
//! P&L не читает.

use super::f12::PxSrc;
use super::pdcompat::{parse_date, parse_i64};
use anyhow::{bail, Context, Result};
use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Mutex, PoisonError};
use std::time::Instant;

const MIN_MS: i64 = 60_000;
const DAY_MS: i64 = 86_400_000;
const H24_MS: i64 = DAY_MS;

pub struct TcCfg {
    pub eps: PathBuf,
    pub out: PathBuf,
    pub base_date: String,
    pub threads: usize,
    pub coins: Option<Vec<String>>,
}

/// Одна строка результата.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Counts {
    pub n60: i64,
    pub n24: i64,
    pub m60: bool,
    pub m24: bool,
}

/// Число сделок со временем в `[lo, hi)` в отсортированном массиве.
pub fn count_range(t: &[i64], lo: i64, hi: i64) -> i64 {
    let a = t.partition_point(|&x| x < lo);
    let b = t.partition_point(|&x| x < hi);
    (b.max(a) - a) as i64
}

/// Счёты одного сигнала по суткам `days` (`(абсолютные сутки, времена | None — файла нет)`, по возрастанию суток); `sig_abs` — время сигнала, мс
/// от 1970: окна кончаются на начале минуты сигнала.
pub fn counts_at(days: &[(i64, Option<&[i64]>)], sig_abs: i64) -> Counts {
    let end = sig_abs.div_euclid(MIN_MS) * MIN_MS;
    let (mut n60, mut n24, mut m60, mut m24) = (0, 0, false, false);
    for &(d, t) in days {
        let day_lo = d * DAY_MS;
        let day_hi = day_lo + DAY_MS;
        // сутки нужны окну 24 ч, если пересекают `[end − 24 ч, end)`; окну 60 мин — если пересекают `[end − 60 мин, end)`
        let need24 = day_hi > end - H24_MS && day_lo < end;
        let need60 = day_hi > end - 60 * MIN_MS && day_lo < end;
        if !need24 && !need60 {
            continue;
        }
        match t {
            Some(t) => {
                n24 += count_range(t, end - H24_MS, end);
                n60 += count_range(t, end - 60 * MIN_MS, end);
            }
            None => {
                m24 |= need24;
                m60 |= need60;
            }
        }
    }
    Counts { n60, n24, m60, m24 }
}

struct Row {
    sym: String,
    sig_ms: i64,
}

fn read_rows(path: &PathBuf, coins: Option<&[String]>) -> Result<Vec<Row>> {
    let mut rd = csv::ReaderBuilder::new()
        .has_headers(true)
        .flexible(true)
        .from_path(path)
        .with_context(|| path.display().to_string())?;
    let head = rd.headers()?.clone();
    let col = |n: &str| {
        head.iter()
            .position(|h| h == n)
            .with_context(|| format!("{}: нет столбца {n}", path.display()))
    };
    let (is, it) = (col("sym")?, col("sig_ms")?);
    let mut out = Vec::new();
    for (ln, rec) in rd.records().enumerate() {
        let rec = rec?;
        let sym = rec.get(is).unwrap_or("").trim();
        if coins.is_some_and(|c| !c.iter().any(|x| x == sym)) {
            continue;
        }
        let sig_ms = parse_i64(rec.get(it).unwrap_or("").trim().as_bytes())
            .with_context(|| format!("{}: строка {}", path.display(), ln + 2))?;
        out.push(Row {
            sym: sym.to_string(),
            sig_ms,
        });
    }
    Ok(out)
}

#[derive(Debug, Clone)]
pub struct TcSummary {
    pub rows: usize,
    pub coins: usize,
    pub seconds: f64,
    pub miss_rows: usize,
}

impl TcSummary {
    pub fn json(&self) -> String {
        format!(
            "{{\"rows\":{},\"coins\":{},\"seconds\":{:.1},\"miss_rows\":{}}}",
            self.rows, self.coins, self.seconds, self.miss_rows
        )
    }
}

/// Сутки монеты для строк `idx`: сутки сигнала и предыдущие (окно 24 ч не выходит дальше предыдущих суток), по одному разу на монету.
fn coin_counts(
    src: &dyn PxSrc,
    sym: &str,
    base_ms: i64,
    rows: &[Row],
    idx: &[usize],
) -> Result<Vec<(usize, Counts)>> {
    let mut need: BTreeMap<i64, Option<Vec<i64>>> = BTreeMap::new();
    for &i in idx {
        let d = (base_ms + rows[i].sig_ms).div_euclid(DAY_MS);
        for k in [d - 1, d] {
            need.entry(k).or_insert(None);
        }
    }
    for (d, slot) in need.iter_mut() {
        *slot = src.day(sym, *d)?.map(|x| x.t);
    }
    let days: Vec<(i64, Option<&[i64]>)> = need.iter().map(|(d, t)| (*d, t.as_deref())).collect();
    Ok(idx
        .iter()
        .map(|&i| (i, counts_at(&days, base_ms + rows[i].sig_ms)))
        .collect())
}

pub fn run(cfg: &TcCfg, src: &dyn PxSrc) -> Result<TcSummary> {
    let t0 = Instant::now();
    let base_ms = parse_date(&cfg.base_date)? * DAY_MS;
    let rows = read_rows(&cfg.eps, cfg.coins.as_deref())?;
    if rows.is_empty() {
        bail!("пустой вход: {}", cfg.eps.display());
    }
    let mut by: BTreeMap<&str, Vec<usize>> = BTreeMap::new();
    for (i, r) in rows.iter().enumerate() {
        by.entry(r.sym.as_str()).or_default().push(i);
    }
    let jobs: Vec<(&str, &Vec<usize>)> = by.iter().map(|(k, v)| (*k, v)).collect();
    let next = AtomicUsize::new(0);
    let res: Mutex<Vec<(usize, Counts)>> = Mutex::new(Vec::with_capacity(rows.len()));
    let err: Mutex<Option<anyhow::Error>> = Mutex::new(None);
    std::thread::scope(|sc| {
        for _ in 0..cfg.threads.max(1) {
            sc.spawn(|| loop {
                let j = next.fetch_add(1, Ordering::SeqCst);
                let Some((sym, idx)) = jobs.get(j) else {
                    break;
                };
                match coin_counts(src, sym, base_ms, &rows, idx)
                    .with_context(|| format!("монета {sym}"))
                {
                    Ok(v) => res.lock().unwrap_or_else(PoisonError::into_inner).extend(v),
                    Err(e) => {
                        *err.lock().unwrap_or_else(PoisonError::into_inner) = Some(e);
                        break;
                    }
                }
            });
        }
    });
    if let Some(e) = err.into_inner().unwrap_or_else(PoisonError::into_inner) {
        return Err(e);
    }
    let mut done = res.into_inner().unwrap_or_else(PoisonError::into_inner);
    done.sort_by_key(|(i, _)| *i);
    let mut text = String::from("sym,sig_ms,n60,n24,m60,m24\n");
    let mut miss_rows = 0;
    for (i, c) in &done {
        miss_rows += usize::from(c.m60 || c.m24);
        let _ = writeln!(
            text,
            "{},{},{},{},{},{}",
            rows[*i].sym,
            rows[*i].sig_ms,
            c.n60,
            c.n24,
            u8::from(c.m60),
            u8::from(c.m24)
        );
    }
    std::fs::write(&cfg.out, text).with_context(|| cfg.out.display().to_string())?;
    Ok(TcSummary {
        rows: done.len(),
        coins: jobs.len(),
        seconds: t0.elapsed().as_secs_f64(),
        miss_rows,
    })
}
