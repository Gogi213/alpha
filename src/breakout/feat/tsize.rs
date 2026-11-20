//! TK-639: число сделок и оборот монеты перед сигналом в окнах 1 / 6 / 12 / 24 ч — для среднего размера сделки (оборот / число сделок) и числа сделок за 6 и 12 ч.
//! Окна — как у `tcount` (TK-638) и ключей пула `tick`: закрытые минуты строго раньше минуты сигнала `t = ⌊sig_ms / 60 000⌋`, `[(t − W)·60 000, t·60 000)`, `W` = 60 / 360 / 720 / 1 440 мин.
//!
//! Вход: CSV с заголовком, столбцы `sym` и `sig_ms` (как у `tcount`). Выход: `sym,sig_ms,n60,n360,n720,n1440,v60,v360,v720,v1440,m60,m360,m720,m1440` в порядке входа:
//! `n` — число записей aggTrades, `v` — оборот `Σ price·quantity` (`DaySrc`: `v` записи — одно умножение `f64`, суммы по записям окна в порядке времени), `m` = 1, если файла суток, который окно задевает,
//! нет (счёт идёт по тому, что есть; решение «пусто проходит» принимает вызывающий код). Источник суток — `DaySrc` (zip или склад TK-607 на чтение). P&L не читает.

use super::pdcompat::parse_date;
use super::tcount::{read_rows, Row};
use super::trades::{DaySrc, DayTrades};
use anyhow::{bail, Context, Result};
use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Mutex, PoisonError};
use std::time::Instant;

const MIN_MS: i64 = 60_000;
const DAY_MS: i64 = 86_400_000;
/// Окна, мин: 1 ч, 6 ч, 12 ч, 24 ч.
pub const WINDOWS_MIN: [i64; 4] = [60, 360, 720, 1_440];

pub struct TsCfg {
    pub eps: PathBuf,
    pub out: PathBuf,
    pub base_date: String,
    pub threads: usize,
    pub coins: Option<Vec<String>>,
}

/// Одна строка результата: по окнам `WINDOWS_MIN`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Sizes {
    pub n: [i64; 4],
    pub v: [f64; 4],
    pub m: [bool; 4],
}

/// Сутки: `(абсолютные сутки, (времена, обороты записей) | None — файла нет)`, по возрастанию суток.
pub type DaySlices<'a> = (i64, Option<(&'a [i64], &'a [f64])>);

/// Счёты одного сигнала по суткам `days`; `sig_abs` — время сигнала, мс от 1970: окна кончаются на начале минуты сигнала.
pub fn sizes_at(days: &[DaySlices], sig_abs: i64) -> Sizes {
    let end = sig_abs.div_euclid(MIN_MS) * MIN_MS;
    let mut s = Sizes {
        n: [0; 4],
        v: [0.0; 4],
        m: [false; 4],
    };
    for &(d, day) in days {
        let day_lo = d * DAY_MS;
        let day_hi = day_lo + DAY_MS;
        for (k, w) in WINDOWS_MIN.iter().enumerate() {
            let lo = end - w * MIN_MS;
            // сутки нужны окну, если пересекают `[lo, end)`
            if day_hi <= lo || day_lo >= end {
                continue;
            }
            match day {
                Some((t, v)) => {
                    let a = t.partition_point(|&x| x < lo);
                    let b = t.partition_point(|&x| x < end);
                    if b > a {
                        s.n[k] += (b - a) as i64;
                        s.v[k] += v[a..b].iter().sum::<f64>();
                    }
                }
                None => s.m[k] = true,
            }
        }
    }
    s
}

#[derive(Debug, Clone)]
pub struct TsSummary {
    pub rows: usize,
    pub coins: usize,
    pub seconds: f64,
    pub miss_rows: usize,
}

impl TsSummary {
    pub fn json(&self) -> String {
        format!(
            "{{\"rows\":{},\"coins\":{},\"seconds\":{:.1},\"miss_rows\":{}}}",
            self.rows, self.coins, self.seconds, self.miss_rows
        )
    }
}

/// Сутки монеты для строк `idx`: сутки сигнала и предыдущие (окно 24 ч не выходит дальше предыдущих суток), по одному разу на монету.
fn coin_sizes(
    src: &dyn DaySrc,
    sym: &str,
    base_ms: i64,
    rows: &[Row],
    idx: &[usize],
) -> Result<Vec<(usize, Sizes)>> {
    let mut need: BTreeMap<i64, Option<DayTrades>> = BTreeMap::new();
    for &i in idx {
        let d = (base_ms + rows[i].sig_ms).div_euclid(DAY_MS);
        for k in [d - 1, d] {
            need.entry(k).or_insert(None);
        }
    }
    for (d, slot) in need.iter_mut() {
        *slot = src.day(sym, *d)?;
    }
    let days: Vec<DaySlices> = need
        .iter()
        .map(|(d, x)| (*d, x.as_ref().map(|x| (x.t.as_slice(), x.v.as_slice()))))
        .collect();
    Ok(idx
        .iter()
        .map(|&i| (i, sizes_at(&days, base_ms + rows[i].sig_ms)))
        .collect())
}

pub fn run(cfg: &TsCfg, src: &dyn DaySrc) -> Result<TsSummary> {
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
    let res: Mutex<Vec<(usize, Sizes)>> = Mutex::new(Vec::with_capacity(rows.len()));
    let err: Mutex<Option<anyhow::Error>> = Mutex::new(None);
    std::thread::scope(|sc| {
        for _ in 0..cfg.threads.max(1) {
            sc.spawn(|| loop {
                let j = next.fetch_add(1, Ordering::SeqCst);
                let Some((sym, idx)) = jobs.get(j) else {
                    break;
                };
                match coin_sizes(src, sym, base_ms, &rows, idx)
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
    let mut text =
        String::from("sym,sig_ms,n60,n360,n720,n1440,v60,v360,v720,v1440,m60,m360,m720,m1440\n");
    let mut miss_rows = 0;
    for (i, s) in &done {
        miss_rows += usize::from(s.m.iter().any(|&x| x));
        let _ = writeln!(
            text,
            "{},{},{},{},{},{},{:.4},{:.4},{:.4},{:.4},{},{},{},{}",
            rows[*i].sym,
            rows[*i].sig_ms,
            s.n[0],
            s.n[1],
            s.n[2],
            s.n[3],
            s.v[0],
            s.v[1],
            s.v[2],
            s.v[3],
            u8::from(s.m[0]),
            u8::from(s.m[1]),
            u8::from(s.m[2]),
            u8::from(s.m[3])
        );
    }
    std::fs::write(&cfg.out, text).with_context(|| cfg.out.display().to_string())?;
    Ok(TsSummary {
        rows: done.len(),
        coins: jobs.len(),
        seconds: t0.elapsed().as_secs_f64(),
        miss_rows,
    })
}
