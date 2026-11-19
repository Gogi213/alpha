//! Признаки «поток сделок» (16): порт `tk594_feat.flow_features` / `process_coin` (режим `flow`) без изменения правил.
//! Окна — строго раньше `sig_ms`; оценка `q99` крупных сделок — по часовым гистограммам `log10 v` за 7 суток.
//! Все суммы — `np_sum` (порядок numpy), накопления — `np_cumsum`, поиск — `searchsorted` слева/справа.

use super::npcompat::{np_cumsum, np_mean, np_sum, ss_left_f64, ss_left_i64, ss_right_f64};
use super::trades::{DaySrc, DayTrades};
use anyhow::Result;
use std::collections::BTreeMap;
use std::sync::OnceLock;

pub const DAY_MS: i64 = 86_400_000;
pub const HOUR_MS: i64 = 3_600_000;
pub const MIN_MS: i64 = 60_000;
/// Корзин гистограммы `log10 v`: 1e-3 … 1e9, шаг 0,01 декады.
pub const NB: usize = 1200;
const VPIN_TOL: f64 = 1e-12;
pub const FLOW: [&str; 16] = [
    "tfv10", "tfv60", "tfv300", "tfv1800", "tfn10", "tfn60", "tfn300", "tfn1800", "acc_v", "acc_n",
    "big300", "big1800", "tfp300", "lmsz300", "msz_r", "vpin",
];
const WIN_S: [i64; 4] = [10, 60, 300, 1800];
const NAN: f64 = f64::NAN;

/// Границы корзин `10 ** ((j − 300) / 100)`, `j = 0…NB`.
pub fn edges() -> &'static [f64] {
    static E: OnceLock<Vec<f64>> = OnceLock::new();
    E.get_or_init(|| {
        (0..=NB)
            .map(|j| 10.0f64.powf((j as f64 - 300.0) / 100.0))
            .collect()
    })
}

/// Таблица для `hist_bin`: ячейка — порядок double и 8 старших бит мантиссы (ширина ячейки 0,2…0,4 % против шага корзины 2,3 %: граница
/// попадает внутрь ячейки редко, в большинстве случаев сравнений после таблицы нет), значение — `searchsorted(edges, нижняя граница ячейки, "right")`.
const LUT_SHIFT: u32 = 44;
/// Первая ячейка — 2^-10 ≈ 9,8e-4 (< `edges[0]` = 1e-3).
const LUT_E0: u64 = (1023 - 10) << 8;
/// 41 порядок: до 2^31 ≈ 2,1e9 (> `edges[NB]` = 1e9).
const LUT_N: usize = 41 * 256;

fn bin_lut() -> &'static [u16] {
    static L: OnceLock<Vec<u16>> = OnceLock::new();
    L.get_or_init(|| {
        let ed = edges();
        (0..LUT_N as u64)
            .map(|k| ss_right_f64(ed, f64::from_bits((LUT_E0 + k) << LUT_SHIFT)) as u16)
            .collect()
    })
}

#[cfg(test)]
pub(super) fn bin_lut_for_test() -> &'static [u16] {
    bin_lut()
}

/// Корзина гистограммы для `v` (не NaN): `(searchsorted(edges, v, "right") − 1)` в `0…NB−1`, как у numpy; быстрый путь — таблица ячеек, вне её
/// (ноль, отрицательные, < 2^-10, ≥ 2^31, inf) — двоичный поиск.
#[inline]
pub(super) fn hist_bin(lut: &[u16], ed: &[f64], v: f64) -> usize {
    let k = (v.to_bits() >> LUT_SHIFT).wrapping_sub(LUT_E0);
    let r = if k < LUT_N as u64 {
        let mut i = usize::from(lut[k as usize]);
        while i < ed.len() && ed[i] <= v {
            i += 1;
        }
        i
    } else {
        ss_right_f64(ed, v)
    };
    (r as i64 - 1).clamp(0, NB as i64 - 1) as usize
}

/// Эпизод монеты: номер строки входа, время сигнала (мс от 1970), сторона (+1 лонг / −1 шорт), оборот за 24 ч.
#[derive(Debug, Clone, Copy)]
pub struct Ep {
    pub row: usize,
    pub sig: i64,
    pub side: i64,
    pub turn24h: f64,
}

/// Сутки подряд (предыдущие + текущие), как `np.concatenate` двух массивов.
pub(super) struct Win {
    t: Vec<i64>,
    v: Vec<f64>,
    b: Vec<bool>,
}

impl Win {
    pub(super) fn of(parts: &[&DayTrades]) -> Win {
        Win {
            t: parts.iter().flat_map(|p| p.t.iter().copied()).collect(),
            v: parts.iter().flat_map(|p| p.v.iter().copied()).collect(),
            b: parts.iter().flat_map(|p| p.buy.iter().copied()).collect(),
        }
    }
}

/// `(оборот, оборот покупок, записей, покупок)` по срезу `[lo, hi)`.
fn win_stats(v: &[f64], b: &[bool], lo: usize, hi: usize) -> (f64, f64, i64, i64) {
    if hi <= lo {
        return (0.0, 0.0, 0, 0);
    }
    let (vs, bs) = (&v[lo..hi], &b[lo..hi]);
    let vb: Vec<f64> = vs
        .iter()
        .zip(bs)
        .filter(|(_, &m)| m)
        .map(|(&x, _)| x)
        .collect();
    (
        np_sum(vs),
        np_sum(&vb),
        (hi - lo) as i64,
        bs.iter().filter(|&&m| m).count() as i64,
    )
}

/// 50 корзин равного оборота на записях `[lo, hi)`; граница — запись, на которой накопленный оборот впервые ≥ k/50·(1 − 1e-12).
pub fn vpin_value(v: &[f64], b: &[bool], lo: usize, hi: usize) -> f64 {
    let vv = &v[lo..hi];
    let n = vv.len();
    if n == 0 {
        return NAN;
    }
    let cum = np_cumsum(vv);
    let total = cum[n - 1];
    if total.partial_cmp(&0.0) != Some(std::cmp::Ordering::Greater) {
        return NAN;
    }
    let masked: Vec<f64> = vv
        .iter()
        .zip(&b[lo..hi])
        .map(|(&x, &m)| if m { x } else { 0.0 })
        .collect();
    let cb = np_cumsum(&masked);
    let mut pv = Vec::with_capacity(n + 1);
    pv.push(0.0);
    pv.extend_from_slice(&cum);
    let mut pb = Vec::with_capacity(n + 1);
    pb.push(0.0);
    pb.extend_from_slice(&cb);
    let mut e = [0usize; 51];
    for (k, ek) in e.iter_mut().enumerate().take(50).skip(1) {
        *ek = ss_left_f64(&cum, k as f64 * total / 50.0 * (1.0 - VPIN_TOL)) + 1;
    }
    e[50] = n;
    let mut r = Vec::with_capacity(50);
    for k in 0..50 {
        let tot = pv[e[k + 1]] - pv[e[k]];
        let bu = pb[e[k + 1]] - pb[e[k]];
        if tot > 0.0 {
            r.push((2.0 * bu - tot).abs() / tot);
        }
    }
    if r.len() < 10 {
        return NAN;
    }
    np_mean(&r)
}

/// Нижняя граница наименьшего интервала, для которого доля записей с `log10 v` ≥ границы ≤ 1 %; `inf` — таких нет; `None` — нет записей.
pub fn q99_edge(hist: &[i64]) -> Option<f64> {
    let total: i64 = hist.iter().sum();
    if total == 0 {
        return None;
    }
    let lim = 0.01 * total as f64;
    let mut ge = vec![0i64; hist.len()];
    let mut acc = 0i64;
    for j in (0..hist.len()).rev() {
        acc += hist[j];
        ge[j] = acc;
    }
    Some(match ge.iter().position(|&g| g as f64 <= lim) {
        Some(j) => edges()[j],
        None => f64::INFINITY,
    })
}

pub(super) fn flow_features(w: &Win, sig: i64, s: i64, q99: Option<f64>, has24: bool) -> [f64; 16] {
    let mut f = [NAN; 16];
    let i_hi = ss_left_i64(&w.t, sig);
    let ilo = |ms: i64| ss_left_i64(&w.t, sig - ms);
    let sf = s as f64;
    for (k, &win) in WIN_S.iter().enumerate() {
        let (vt, vb, n, nb) = win_stats(&w.v, &w.b, ilo(win * 1000), i_hi);
        if n > 0 {
            f[k] = if vt > 0.0 {
                sf * (2.0 * vb - vt) / vt
            } else {
                NAN
            };
            f[4 + k] = sf * (2.0 * nb as f64 - n as f64) / n as f64;
        }
    }
    let i60 = ilo(60_000);
    let (v1, _, n1, _) = win_stats(&w.v, &w.b, i60, i_hi);
    let (vp, _, np_, _) = win_stats(&w.v, &w.b, ilo(31 * MIN_MS), i60);
    if vp > 0.0 {
        f[8] = (v1 / 60.0) / (vp / 1800.0);
    }
    if np_ > 0 {
        f[9] = (n1 as f64 / 60.0) / (np_ as f64 / 1800.0);
    }
    let i5 = ilo(300_000);
    let (v5, vb5, n5, _) = win_stats(&w.v, &w.b, i5, i_hi);
    if let Some(q) = q99 {
        for (w_s, k) in [(300i64, 10usize), (1800, 11)] {
            let lo = ilo(w_s * 1000);
            let seg = if lo < i_hi { &w.v[lo..i_hi] } else { &w.v[..0] };
            let vt = if seg.is_empty() { 0.0 } else { np_sum(seg) };
            if vt > 0.0 {
                let big: Vec<f64> = seg.iter().copied().filter(|&x| x >= q).collect();
                f[k] = np_sum(&big) / vt;
            }
        }
    }
    if has24 {
        let i24 = ilo(DAY_MS);
        let v24 = if i_hi > i24 {
            np_sum(&w.v[i24..i_hi])
        } else {
            0.0
        };
        if v24 > 0.0 {
            f[12] = sf * (2.0 * vb5 - v5) / (v24 / 288.0);
        }
        f[15] = vpin_value(&w.v, &w.b, i24, i_hi);
    }
    if n5 > 0 && v5 > 0.0 {
        f[13] = (v5 / n5 as f64).log10();
        let (vq, _, nq, _) = win_stats(&w.v, &w.b, ilo(35 * MIN_MS), ilo(5 * MIN_MS));
        if nq > 0 && vq > 0.0 {
            f[14] = (v5 / n5 as f64) / (vq / nq as f64);
        }
    }
    f
}

/// Результат одной монеты: строки таблицы и число пропущенных суток файлов сделок.
pub struct CoinFlow {
    pub rows: Vec<(usize, [f64; 16])>,
    pub missing_trade_days: u64,
    /// Сутки окна, где время в файле убывало (записи пересортированы).
    pub unsorted_days: u64,
}

/// `process_coin` режима `flow`: сутки от `dmin − 8` (гистограммы `q99`) до `dmax`; массивы — предыдущие и текущие сутки.
pub fn flow_coin(src: &dyn DaySrc, sym: &str, eps: &[Ep]) -> Result<CoinFlow> {
    let days: Vec<i64> = eps.iter().map(|e| e.sig.div_euclid(DAY_MS)).collect();
    let dmin = *days.iter().min().unwrap_or(&0);
    let dmax = *days.iter().max().unwrap_or(&0);
    let h0 = (dmin - 8) * 24;
    let nrows = ((dmax - (dmin - 8) + 1) * 24) as usize;
    let mut hist = vec![0i32; nrows * NB];
    let mut by_day: BTreeMap<i64, Vec<usize>> = BTreeMap::new();
    for (i, &d) in days.iter().enumerate() {
        by_day.entry(d).or_default().push(i);
    }
    let (ed, lut) = (edges(), bin_lut());
    let mut out = CoinFlow {
        rows: Vec::with_capacity(eps.len()),
        missing_trade_days: 0,
        unsorted_days: 0,
    };
    let mut prev: Option<DayTrades> = None;
    let mut first_seen = false;
    let mut coin_first: Option<i64> = None;
    for d in (dmin - 8)..=dmax {
        let cur = src.day(sym, d)?;
        match &cur {
            Some(c) => {
                if !first_seen {
                    first_seen = true;
                    coin_first = c.t.first().copied();
                }
                out.unsorted_days += u64::from(c.unsorted);
                let base = ((d * 24 - h0) as usize) * NB;
                for (&t, &v) in c.t.iter().zip(&c.v) {
                    let hr = (t.div_euclid(HOUR_MS) - d * 24).clamp(0, 23) as usize;
                    // NaN: `searchsorted` numpy кладёт его в конец → последняя корзина
                    let ix = if v.is_nan() {
                        NB - 1
                    } else {
                        hist_bin(lut, ed, v)
                    };
                    hist[base + hr * NB + ix] += 1;
                }
            }
            None => {
                if first_seen && d >= dmin - 1 {
                    out.missing_trade_days += 1;
                }
            }
        }
        if let Some(idx) = by_day.get(&d) {
            let parts: Vec<&DayTrades> = [prev.as_ref(), cur.as_ref()]
                .into_iter()
                .flatten()
                .collect();
            if !parts.is_empty() {
                let w = Win::of(&parts);
                for &e in idx {
                    let ep = &eps[e];
                    let h_end = ep.sig.div_euclid(HOUR_MS);
                    let lo = (h_end - 168 - h0).max(0) as usize;
                    let hi = ((h_end - h0).max(0) as usize).min(nrows);
                    let mut hh = vec![0i64; NB];
                    let mut n_hours = 0;
                    for r in lo..hi.max(lo) {
                        let row = &hist[r * NB..(r + 1) * NB];
                        let mut s = 0i64;
                        for (a, &c) in hh.iter_mut().zip(row) {
                            *a += i64::from(c);
                            s += i64::from(c);
                        }
                        if s > 0 {
                            n_hours += 1;
                        }
                    }
                    let q99 = if n_hours >= 24 { q99_edge(&hh) } else { None };
                    let has24 = coin_first.is_some_and(|c| c <= ep.sig - DAY_MS);
                    out.rows
                        .push((ep.row, flow_features(&w, ep.sig, ep.side, q99, has24)));
                }
            }
        }
        prev = cur;
    }
    Ok(out)
}
