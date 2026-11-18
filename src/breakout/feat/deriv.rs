//! Признаки «производные метрики» (14): порт `tk594_feat.deriv_features`, `load_metrics`, `load_funding`, `load_premium`.
//! Строка `metrics` — только на отметке `T* = ⌊(sig − 10 мин)/5 мин⌋·5 мин`; фандинг — последний расчёт не позже `sig`; базис — закрытие
//! минутной премии предыдущей минуты. Разбор чисел — как `pandas.read_csv` (`pdcompat`).

use super::flow::{Ep, DAY_MS, HOUR_MS, MIN_MS};
use super::npcompat::ss_right_i64;
use super::pdcompat::{date_str, parse_datetime_ms, parse_f64, parse_i64};
use super::trades::Csv;
use anyhow::Result;
use std::collections::BTreeMap;
use std::path::PathBuf;

pub const DERIV: [&str; 14] = [
    "oi_c1",
    "oi_c4",
    "oi_c24",
    "loi_turn",
    "ls_tc",
    "ls_ts",
    "ls_a",
    "dls_tc",
    "dls_ts",
    "dls_a",
    "fund_last",
    "fund_chg24",
    "fund_int",
    "basis",
];
const NAN: f64 = f64::NAN;
const METRIC_COLS: [&str; 5] = [
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
];

/// Каталоги источников производных (как `TK594_METRICS`, `TK594_PREMIUM`, `TK594_FUNDING_CSV`, `TK594_FUNDING_M`).
#[derive(Debug, Clone)]
pub struct DerivDirs {
    pub metrics: PathBuf,
    pub premium: PathBuf,
    pub funding_csv: PathBuf,
    pub funding_m: PathBuf,
}

/// Строки `metrics` монеты на сетке 5 мин от `d0·DAY_MS`: столбец → массив с NaN; `first` — самое раннее `create_time` монеты.
pub(super) struct Metrics {
    pub(super) cols: [Vec<f64>; 5],
    pub(super) first: Option<i64>,
}

fn load_metrics(dirs: &DerivDirs, sym: &str, d0: i64, d1: i64) -> Result<Metrics> {
    let n = ((d1 - d0 + 1) * 288) as usize;
    let mut m = Metrics {
        cols: std::array::from_fn(|_| vec![NAN; n]),
        first: None,
    };
    for d in d0..=d1 {
        let ds = date_str(d);
        let path = dirs
            .metrics
            .join(sym)
            .join(format!("{sym}-metrics-{ds}.zip"));
        if !path.exists() {
            continue;
        }
        let csv = Csv::from_zip(&path, &format!("{sym}-metrics-{ds}.csv"))?;
        let mut names = vec!["create_time"];
        names.extend(METRIC_COLS);
        let cols = csv.columns(&names)?;
        let ct: Vec<i64> = cols[0]
            .iter()
            .map(|b| parse_datetime_ms(b))
            .collect::<Result<_>>()?;
        if let Some(&mn) = ct.iter().min() {
            if m.first.is_none_or(|f| mn < f) {
                m.first = Some(mn);
            }
        }
        let vals: Vec<Vec<f64>> = (0..5)
            .map(|c| {
                cols[c + 1]
                    .iter()
                    .map(|b| parse_f64(b))
                    .collect::<Result<_>>()
            })
            .collect::<Result<_>>()?;
        for (i, &t) in ct.iter().enumerate() {
            let rel = t - d0 * DAY_MS;
            let k = rel.div_euclid(300_000);
            if k >= 0 && (k as usize) < n && rel.rem_euclid(300_000) == 0 {
                for (c, v) in vals.iter().enumerate() {
                    m.cols[c][k as usize] = v[i];
                }
            }
        }
    }
    Ok(m)
}

/// `(время мс, ставка)` расчётов фандинга: месячные архивы `fundingRate` (по имени файла), затем `funding.csv` (перекрывает); сортировка по времени.
fn load_funding(dirs: &DerivDirs, sym: &str) -> Result<(Vec<i64>, Vec<f64>)> {
    let mut rows: BTreeMap<i64, f64> = BTreeMap::new();
    let mdir = dirs.funding_m.join(sym);
    if mdir.is_dir() {
        let mut names: Vec<String> = std::fs::read_dir(&mdir)?
            .filter_map(|e| e.ok().and_then(|e| e.file_name().into_string().ok()))
            .collect();
        names.sort();
        for fnm in names.iter().filter(|n| n.ends_with(".zip")) {
            let csv = Csv::from_zip(&mdir.join(fnm), &format!("{}.csv", &fnm[..fnm.len() - 4]))?;
            let c = csv.columns(&["calc_time", "last_funding_rate"])?;
            for (t, r) in c[0].iter().zip(&c[1]) {
                rows.insert(parse_i64(t)?, parse_f64(r)?);
            }
        }
    }
    let p = dirs.funding_csv.join(sym).join("funding.csv");
    if p.exists() {
        let csv = Csv::from_file(&p)?;
        let c = csv.columns(&["funding_time", "funding_rate"])?;
        for (t, r) in c[0].iter().zip(&c[1]) {
            rows.insert(parse_i64(t)?, parse_f64(r)?);
        }
    }
    Ok((
        rows.keys().copied().collect(),
        rows.values().copied().collect(),
    ))
}

/// Закрытие минутной премии на сетке минут от `d0·DAY_MS` (NaN — нет строки).
fn load_premium(dirs: &DerivDirs, sym: &str, d0: i64, d1: i64) -> Result<Vec<f64>> {
    let n = ((d1 - d0 + 1) * 1440) as usize;
    let mut out = vec![NAN; n];
    for d in d0..=d1 {
        let ds = date_str(d);
        let path = dirs.premium.join(sym).join(format!("{sym}-1m-{ds}.zip"));
        if !path.exists() {
            continue;
        }
        let csv = Csv::from_zip(&path, &format!("{sym}-1m-{ds}.csv"))?;
        let c = csv.columns(&["open_time", "close"])?;
        for (t, v) in c[0].iter().zip(&c[1]) {
            let k = (parse_i64(t)? - d0 * DAY_MS).div_euclid(MIN_MS);
            let x = parse_f64(v)?;
            if k >= 0 && (k as usize) < n {
                out[k as usize] = x;
            }
        }
    }
    Ok(out)
}

#[allow(clippy::too_many_arguments)]
pub(super) fn deriv_features(
    sig: i64,
    turn24h: f64,
    m: &Metrics,
    d0: i64,
    ft: &[i64],
    fr: &[f64],
    prem: &[f64],
) -> [f64; 14] {
    let mut f = [NAN; 14];
    let ts = (sig - 10 * MIN_MS).div_euclid(300_000) * 300_000;
    let g = (ts - d0 * DAY_MS).div_euclid(300_000);
    if m.first.is_some_and(|fr0| fr0 <= ts - DAY_MS) {
        let at = |c: usize, back: i64| -> f64 {
            let i = g - back;
            if i >= 0 && (i as usize) < m.cols[c].len() {
                m.cols[c][i as usize]
            } else {
                NAN
            }
        };
        let oi = at(0, 0);
        for (h, k) in [(12i64, 0usize), (48, 1), (288, 2)] {
            let o2 = at(0, h);
            if !oi.is_nan() && o2 > 0.0 {
                f[k] = (oi / o2 - 1.0) * 1e4;
            }
        }
        let oiv = at(1, 0);
        if oiv > 0.0 && turn24h > 0.0 {
            f[3] = (oiv / turn24h).log10();
        }
        for (c, k1, k2) in [(2usize, 4usize, 7usize), (3, 5, 8), (4, 6, 9)] {
            let (x, x1) = (at(c, 0), at(c, 12));
            if x > 0.0 {
                f[k1] = x.ln();
                if x1 > 0.0 {
                    f[k2] = x.ln() - x1.ln();
                }
            }
        }
    }
    let i = ss_right_i64(ft, sig) as i64 - 1;
    if i >= 0 {
        let iu = i as usize;
        f[10] = fr[iu] * 1e4;
        let j = ss_right_i64(ft, sig - DAY_MS) as i64 - 1;
        if j >= 0 {
            f[11] = (fr[iu] - fr[j as usize]) * 1e4;
        }
        if iu >= 1 {
            f[12] = ((ft[iu] - ft[iu - 1]) as f64 / HOUR_MS as f64).round_ties_even();
        }
    }
    let k = (sig.div_euclid(MIN_MS) - 1) - (d0 * DAY_MS).div_euclid(MIN_MS);
    if k >= 0 && (k as usize) < prem.len() {
        let p = prem[k as usize];
        if !p.is_nan() {
            f[13] = p * 1e4;
        }
    }
    f
}

/// Результат одной монеты.
pub struct CoinDeriv {
    pub rows: Vec<(usize, [f64; 14])>,
}

/// `process_coin` режима `deriv`: строки `metrics` на сетке с `d0 = dmin − 2` (24 ч назад от `T*` ранних эпизодов суток).
pub fn deriv_coin(dirs: &DerivDirs, sym: &str, eps: &[Ep]) -> Result<CoinDeriv> {
    let dmin = eps
        .iter()
        .map(|e| e.sig.div_euclid(DAY_MS))
        .min()
        .unwrap_or(0);
    let dmax = eps
        .iter()
        .map(|e| e.sig.div_euclid(DAY_MS))
        .max()
        .unwrap_or(0);
    let d0 = dmin - 2;
    let m = load_metrics(dirs, sym, d0, dmax)?;
    let (ft, fr) = load_funding(dirs, sym)?;
    let prem = load_premium(dirs, sym, d0, dmax)?;
    let rows = eps
        .iter()
        .map(|e| {
            (
                e.row,
                deriv_features(e.sig, e.turn24h, &m, d0, &ft, &fr, &prem),
            )
        })
        .collect();
    Ok(CoinDeriv { rows })
}
