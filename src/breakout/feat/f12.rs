//! TK-611: признаки эпизода f12 — порт `sigfeat_run.Worker.episode` (+ `sigfeat_lib`) в маршруте U0 `retitr_ftab.CandWorker`, побитово.
//!
//! Область (приказ координатора, `docs/archive/tk609-handoff-2026-11-19-v4.md` п. 4): только признаки; метки, контроли (`controls`,
//! `default_rng`), `trade_feats` и пересечения не портируются. Каждая строка `<side>-sigsx.csv` — эпизод из одного сигнала
//! (`ep_no` — номер в порядке `(level, sig_ms)` по монете, `n_sig_ep = 1`, `last_sig_ms = sig_ms`, `ep_id = U0|side|sym|level|ep_no`).
//! Вход: `sigsx`, минутки `<cache>/<SYM>.b42` (+ `BTCUSDT`, `ETHUSDT` для корреляций), сутки сделок (только цена ×1e8 и время).
//! Сутки нужны только сигнальные (и предыдущие, если окно [сигнал − 60 мин, сигнал] их пересекает): `Ticks.span` Python читает ещё
//! +240 мин вперёд ради меток — на признаки это не влияет (сделки строго раньше сигнальной, индекс сигнальной сделки — внутри суток).
//! Равенство с Python — побитовое: суммы numpy — `npcompat` (`np_mean`/`np_sum`), деления — в том же порядке операций, что и у Python.

use super::npcompat::{np_mean, np_sum};
use super::pdcompat::{date_str, parse_date, parse_f64, parse_i64};
use crate::breakout::store::{DayTrades as StoredDay, TickStore, F_PX_INEXACT};
use crate::breakout::tick::ZipSrc;
use anyhow::{bail, Context, Result};
use std::borrow::Cow;
use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, PoisonError};
use std::time::Instant;

const MIN_MS: i64 = 60_000;
const DAY_MS: i64 = 86_400_000;
const SCALE: i64 = 100_000_000;
/// Зона входа E = 0,3 % (e8).
const E_E8: i64 = 300_000;
/// τ = 0,2 % (в 1e-5).
const TAU_PM: i64 = 200;
/// Полоса проторговки 0,5 % (в 1e-5).
const B_PM: i64 = 500;
/// Таймфрейм баров уровня, минут.
const TF: i64 = 60;
const START_DAY: i64 = 14;
/// Сетка суток по умолчанию (`SIGFEAT_DAYS`: у ftab TK-599 — 80, у TK-594/`ep_u0` — 74); длина `.b42` обязана ей соответствовать (`F12Cfg::days`).
pub const DAYS_TOTAL: i64 = 80;
const NAN: f64 = f64::NAN;
const REFS: [&str; 2] = ["BTCUSDT", "ETHUSDT"];

/// Признаки в порядке словаря `out` у Python (`level_features`, `fresh_prior_features`, `sparse_features`, `tick_sparse_features`,
/// `approach_features`, `context_features`, `corr_features`).
pub const NAMES: [&str; 48] = [
    "n_piv",
    "spread_bp",
    "spread_tau",
    "ext_gap_bp",
    "age_h",
    "since_born_h",
    "dist_bp",
    "pierce_ep",
    "pierce_max_bp",
    "touch_ep",
    "since_touch_h",
    "depth_min_bp",
    "depth_mean_bp",
    "approach_dur_min",
    "since_touch_prior_h",
    "touch_prior_none",
    "emp2h",
    "emp6h",
    "flat60",
    "zv2h",
    "zv6h",
    "tpm15",
    "tpm60",
    "gap60_s",
    "mv5",
    "mv15",
    "mv60",
    "maxmv1",
    "maxrng15",
    "cons60",
    "cons120",
    "natr_bp",
    "turn1h",
    "turn24h",
    "c5_btc_7d",
    "c5_btc_24h",
    "c15_btc_7d",
    "c15_btc_24h",
    "c1_btc_6h",
    "btc_mv5",
    "btc_mv15",
    "c5_eth_7d",
    "c5_eth_24h",
    "c15_eth_7d",
    "c15_eth_24h",
    "c1_eth_6h",
    "eth_mv5",
    "eth_mv15",
];
pub const NF: usize = NAMES.len();
/// Столбцы, которые у Python целые (`int64` при отсутствии пропусков); остальные — `float64`.
const INT_FEATS: [&str; 6] = [
    "n_piv",
    "pierce_ep",
    "touch_ep",
    "touch_prior_none",
    "cons60",
    "cons120",
];
/// Служебные столбцы строки до признаков.
pub const META: [&str; 18] = [
    "ep_id",
    "univ",
    "side",
    "sym",
    "level",
    "ep_no",
    "sig_ms",
    "day",
    "half",
    "n_sig_ep",
    "last_sig_ms",
    "lp_e8",
    "natr_e8",
    "dist_e8",
    "emp_coin",
    "hour4",
    "wday",
    "tick_ok",
];

// ───────────────────────────── минутки ─────────────────────────────

/// Кэш минуток `.b42` (`load_b42`): o, h, l, c, qv (e8, qv — e5); `o == 0` — минутка без данных.
pub struct Minutes {
    pub o: Vec<i64>,
    pub h: Vec<i64>,
    pub l: Vec<i64>,
    pub c: Vec<i64>,
    pub qv: Vec<i64>,
}

impl Minutes {
    pub fn n(&self) -> usize {
        self.o.len()
    }

    /// `B42\x01`, `n_min` (u32), затем 5 столбцов `i64`; другая длина — ошибка (как `ValueError` Python).
    pub fn load(path: &Path, n_min: usize) -> Result<Minutes> {
        let buf = std::fs::read(path).with_context(|| path.display().to_string())?;
        if buf.len() < 8 || buf[..4] != *b"B42\x01" {
            bail!("{}: не .b42", path.display());
        }
        let n = u32::from_le_bytes([buf[4], buf[5], buf[6], buf[7]]) as usize;
        if n != n_min || buf.len() < 8 + 5 * n * 8 {
            bail!("{}: .b42 длины {n}, нужна {n_min}", path.display());
        }
        let col = |k: usize| -> Vec<i64> {
            buf[8 + k * n * 8..8 + (k + 1) * n * 8]
                .chunks_exact(8)
                .map(|c| i64::from_le_bytes([c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7]]))
                .collect()
        };
        Ok(Minutes {
            o: col(0),
            h: col(1),
            l: col(2),
            c: col(3),
            qv: col(4),
        })
    }
}

/// `long_frame`: шорт — цены с обратным знаком (`H = −low`, `L = −high`, `C = −close`).
struct Fr<'a> {
    m: &'a Minutes,
    s: i64,
}

impl Fr<'_> {
    fn h(&self, x: usize) -> i64 {
        if self.s > 0 {
            self.m.h[x]
        } else {
            -self.m.l[x]
        }
    }
    fn l(&self, x: usize) -> i64 {
        if self.s > 0 {
            self.m.l[x]
        } else {
            -self.m.h[x]
        }
    }
    fn c(&self, x: usize) -> i64 {
        if self.s > 0 {
            self.m.c[x]
        } else {
            -self.m.c[x]
        }
    }
}

/// `ffill_close`: последнее закрытие с данными (`f64`); до листинга NaN.
pub fn ffill_close(m: &Minutes) -> Vec<f64> {
    let mut last = NAN;
    m.o.iter()
        .zip(&m.c)
        .map(|(&o, &c)| {
            if o != 0 {
                last = c as f64;
            }
            last
        })
        .collect()
}

/// `block_returns`: доходности блоков по `k` минут; блок без данных или сосед без данных — NaN.
pub fn block_returns(m: &Minutes, k: usize) -> Vec<f64> {
    let nb = m.n() / k;
    let cb: Vec<f64> = (0..nb)
        .map(|b| {
            (b * k..(b + 1) * k)
                .rev()
                .find(|&x| m.o[x] != 0)
                .map_or(NAN, |x| m.c[x] as f64)
        })
        .collect();
    let mut r = vec![NAN; nb];
    for (o, w) in r.iter_mut().skip(1).zip(cb.windows(2)) {
        *o = w[1] / w[0] - 1.0;
    }
    r
}

/// `minute_returns`: доходность минуты к предыдущей, если обе с данными.
pub fn minute_returns(m: &Minutes) -> Vec<f64> {
    let mut r = vec![NAN; m.n()];
    for (x, o) in r.iter_mut().enumerate().skip(1) {
        if m.o[x] != 0 && m.o[x - 1] != 0 {
            *o = m.c[x] as f64 / m.c[x - 1] as f64 - 1.0;
        }
    }
    r
}

/// `natr_series(m, 30)` Python (как в движке): NATR 1м/p, e8; −1 — нет значения. Служит счётчику `natr_mismatch`.
pub fn natr_series(m: &Minutes, p: usize) -> Vec<i64> {
    let n = m.n();
    let mut ptr = vec![0i64; n + 1];
    let mut pcnt = vec![0i64; n + 1];
    let mut prevc = 0i64;
    for t in 0..n {
        let (mut tr, mut cnt) = (0, 0);
        if m.o[t] != 0 {
            tr = m.h[t] - m.l[t];
            if prevc != 0 {
                tr = tr.max((m.h[t] - prevc).abs()).max((m.l[t] - prevc).abs());
            }
            cnt = 1;
            prevc = m.c[t];
        }
        ptr[t + 1] = ptr[t] + tr;
        pcnt[t + 1] = pcnt[t] + cnt;
    }
    let mut out = vec![-1i64; n];
    for t in p.saturating_sub(1)..n {
        if m.o[t] == 0 {
            continue;
        }
        let k = pcnt[t + 1] - pcnt[t + 1 - p];
        if k * 2 < p as i64 {
            continue;
        }
        let s = ptr[t + 1] - ptr[t + 1 - p];
        let den = i128::from(k) * i128::from(m.c[t]);
        if den != 0 {
            out[t] = (i128::from(s) * i128::from(SCALE) / den).min(i128::from(i32::MAX)) as i64;
        }
    }
    out
}

// ───────────────────────────── числа ─────────────────────────────

/// Python `int / int`: правильно округлённое частное (одно округление). Для операндов ≤ 2^53 это обычное деление `f64`.
pub fn ratio_f64(num: u128, den: u128) -> f64 {
    const EXACT: u128 = 1 << 53;
    if den == 0 {
        return NAN;
    }
    if num <= EXACT && den <= EXACT {
        return num as f64 / den as f64;
    }
    let bits = |x: u128| 128 - x.leading_zeros() as i64;
    let shift = (64 - (bits(num) - bits(den))).max(0) as u32;
    let n = num << shift;
    let (mut q, r) = (n / den, n % den);
    if r != 0 {
        q |= 1;
    }
    (q as f64) * 2f64.powi(-(shift as i32))
}

/// Расстояние под уровнем ×1e8 (`dist_e8` Python): `(R − P)·SCALE / |R|` с усечением к нулю.
fn dist_e8(r: i64, p: i64) -> i64 {
    let n = (i128::from(r) - i128::from(p)) * i128::from(SCALE);
    let q = n
        .abs()
        .checked_div(i128::from(r.abs()))
        .unwrap_or(i128::MAX);
    (if n >= 0 { q } else { -q }) as i64
}

/// `pearson_pairs`: Пирсон по позициям, где обе доходности не NaN; меньше `min_n` пар — NaN. Суммы — порядок numpy.
pub fn pearson_pairs(x: &[f64], y: &[f64], min_n: usize) -> f64 {
    let (mut a, mut b): (Vec<f64>, Vec<f64>) = x
        .iter()
        .zip(y)
        .filter(|(u, v)| !(u.is_nan() || v.is_nan()))
        .map(|(&u, &v)| (u, v))
        .unzip();
    if a.len() < min_n || a.is_empty() {
        return NAN;
    }
    let (ma, mb) = (np_mean(&a), np_mean(&b));
    a.iter_mut().for_each(|z| *z -= ma);
    b.iter_mut().for_each(|z| *z -= mb);
    let aa: Vec<f64> = a.iter().map(|z| z * z).collect();
    let bb: Vec<f64> = b.iter().map(|z| z * z).collect();
    let ab: Vec<f64> = a.iter().zip(&b).map(|(u, v)| u * v).collect();
    let den = (np_sum(&aa) * np_sum(&bb)).sqrt();
    if den > 0.0 {
        np_sum(&ab) / den
    } else {
        NAN
    }
}

fn window_corr(coin: &[f64], refr: &[f64], b_last: i64, n_blocks: i64, min_n: usize) -> f64 {
    let a = (b_last - n_blocks + 1).max(0) as usize;
    let e = (b_last + 1).max(0) as usize;
    match (coin.get(a..e), refr.get(a..e)) {
        (Some(x), Some(y)) => pearson_pairs(x, y, min_n),
        _ => NAN,
    }
}

/// Число серий `true` подряд, накопленное потоком.
#[derive(Default)]
struct Runs {
    n: i64,
    prev: bool,
}

impl Runs {
    fn push(&mut self, v: bool) {
        if v && !self.prev {
            self.n += 1;
        }
        self.prev = v;
    }
}

// ───────────────────────────── признаки ─────────────────────────────

/// Значения признаков по порядку `NAMES`.
struct Vals {
    v: [f64; NF],
    n: usize,
}

impl Vals {
    fn push(&mut self, x: f64) {
        if let Some(c) = self.v.get_mut(self.n) {
            *c = x;
        }
        self.n += 1;
    }
}

/// Строка `sigsx` (`SIGSX_COLS`): сигнал кандидата потока.
#[derive(Debug, Clone)]
pub struct Sig {
    pub level: i64,
    pub sig_ms: i64,
    pub dist_e8: i64,
    pub level_px_e8: i64,
    pub born: i64,
    pub first_bar: i64,
    pub natr_e8: i64,
    /// `(бар, цена)` членов версии уровня.
    pub members: Vec<(i64, i64)>,
}

/// Минутные ряды монеты и производные (`Worker.process`).
pub struct CoinCtx {
    pub m: Minutes,
    cf: Vec<f64>,
    first_valid: usize,
    r5: Vec<f64>,
    r15: Vec<f64>,
    r1: Vec<f64>,
    natr: Vec<i64>,
    emp_coin: f64,
}

impl CoinCtx {
    pub fn new(m: Minutes) -> CoinCtx {
        let first_valid = m.o.iter().position(|&o| o != 0).unwrap_or(m.n());
        let emp_coin = if first_valid < m.n() {
            let from = first_valid.max((START_DAY * 1440) as usize).min(m.n());
            let tail = &m.o[from..];
            tail.iter().filter(|&&o| o == 0).count() as f64 / tail.len() as f64
        } else {
            NAN
        };
        CoinCtx {
            cf: ffill_close(&m),
            first_valid,
            r5: block_returns(&m, 5),
            r15: block_returns(&m, 15),
            r1: minute_returns(&m),
            natr: natr_series(&m, 30),
            emp_coin,
            m,
        }
    }
}

/// Опорные ряды BTC/ETH для корреляций (`Worker.ref`).
pub struct Refs {
    pub refs: Vec<(&'static str, CoinCtx)>,
}

impl Refs {
    pub fn load(cache: &Path, n_min: usize) -> Result<Refs> {
        let mut refs = Vec::new();
        for name in REFS {
            let m = Minutes::load(&cache.join(format!("{name}.b42")), n_min)?;
            refs.push((name, CoinCtx::new(m)));
        }
        Ok(Refs { refs })
    }
}

fn runs_and_touch(fr: &Fr, a: i64, t: i64, r: i64, bars: &[i64]) -> (i64, f64, i64, Option<i64>) {
    let m = fr.m;
    let band = r - r.abs() * TAU_PM / 100_000;
    let (mut pierce, mut touch) = (Runs::default(), Runs::default());
    let (mut pmax, mut last_touch): (Option<i64>, Option<i64>) = (None, None);
    let (mut hour, mut hour_excl) = (i64::MIN, false);
    for x in a.max(0)..t.min(m.n() as i64) {
        if x.div_euclid(TF) != hour {
            hour = x.div_euclid(TF);
            hour_excl = bars.contains(&hour);
        }
        let xu = x as usize;
        let ok = m.o[xu] != 0 && !hour_excl;
        let h = fr.h(xu);
        let (p, tc) = (ok && h >= r, ok && h >= band);
        pierce.push(p);
        touch.push(tc);
        if p {
            pmax = Some(pmax.map_or(h, |v: i64| v.max(h)));
        }
        if tc {
            last_touch = Some(x);
        }
    }
    let pierce_max_bp = pmax.map_or(0.0, |h| ((h - r) as f64 / r.abs() as f64 * 1e4).max(0.0));
    (pierce.n, pierce_max_bp, touch.n, last_touch)
}

/// `level_features`: качество уровня на минуте сигнала `t`; окна — закрытые минуты ≤ t−1.
#[allow(clippy::too_many_arguments)]
fn level_features(
    fr: &Fr,
    t: i64,
    r: i64,
    members: &[(i64, i64)],
    born: i64,
    first_bar: i64,
    dist: i64,
    out: &mut Vals,
) {
    let m = fr.m;
    let s = fr.s;
    let px: Vec<f64> = members.iter().map(|x| x.1 as f64).collect();
    let mean = np_mean(&px);
    let bars: Vec<i64> = members.iter().map(|x| x.0).collect();
    let pmax = px.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let pmin = px.iter().copied().fold(f64::INFINITY, f64::min);
    let spread_bp = (pmax - pmin) / mean * 1e4;
    let lp_real = r.abs();
    let lpx = if s > 0 { pmax } else { pmin };
    let lpr = lp_real as f64;
    out.push(members.len() as f64);
    out.push(spread_bp);
    out.push(spread_bp / (TAU_PM as f64 / 10.0));
    out.push(if s > 0 {
        (lpx - lpr) / lpr * 1e4
    } else {
        (lpr - lpx) / lpr * 1e4
    });
    out.push((t - first_bar * TF) as f64 / 60.0);
    out.push((t - born) as f64 / 60.0);
    out.push(dist as f64 / 1e4);
    let (pierce_ep, pierce_max, touch_ep, last_touch) =
        runs_and_touch(fr, first_bar * TF, t, r, &bars);
    out.push(pierce_ep as f64);
    out.push(pierce_max);
    out.push(touch_ep as f64);
    out.push(match last_touch {
        Some(x) => (t - x) as f64 / 60.0,
        None => {
            let mb = bars.iter().copied().max().unwrap_or(0);
            (t - (mb + 1) * TF) as f64 / 60.0
        }
    });
    // глубина отскоков между соседними членами
    let mut ms = members.to_vec();
    ms.sort_unstable();
    let mut depths: Vec<f64> = Vec::new();
    for (i, &(b, p_)) in ms.iter().enumerate() {
        let lo = (b + 1) * TF;
        let hi = ms.get(i + 1).map_or(born, |x| x.0 * TF).min(t);
        if hi <= lo {
            continue;
        }
        let (mut any, mut lmin, mut hmax) = (false, i64::MAX, i64::MIN);
        for x in lo..hi {
            let xu = x as usize;
            if m.o[xu] != 0 {
                any = true;
                lmin = lmin.min(m.l[xu]);
                hmax = hmax.max(m.h[xu]);
            }
        }
        if !any {
            continue;
        }
        depths.push(if s > 0 {
            (p_ - lmin) as f64 / p_ as f64 * 1e4
        } else {
            (hmax - p_) as f64 / p_ as f64 * 1e4
        });
    }
    if depths.is_empty() {
        out.push(NAN);
        out.push(NAN);
    } else {
        out.push(depths.iter().copied().fold(f64::INFINITY, f64::min));
        out.push(np_mean(&depths));
    }
}

/// `fresh_prior_features`: последнее касание ДО начала текущего подхода.
fn fresh_prior_features(fr: &Fr, t: i64, r: i64, bars: &[i64], first_bar: i64, out: &mut Vals) {
    let m = fr.m;
    // `abs(R)·(E_E8+1)/SCALE` — частное Python (одно округление), не `f64`-деление округлённого числителя
    let p_lo = r as f64 - ratio_f64(r.unsigned_abs() as u128 * (E_E8 + 1) as u128, SCALE as u128);
    let lo = (first_bar * TF).max(0);
    let band = r - r.abs() * TAU_PM / 100_000;
    // a0 — минута после последней «ухода» (low ≤ p_lo или high ≥ R) среди минут с данными
    let a0 = (lo..t)
        .rev()
        .find(|&x| {
            let xu = x as usize;
            m.o[xu] != 0 && ((fr.l(xu) as f64) <= p_lo || fr.h(xu) >= r)
        })
        .map_or(lo, |x| x + 1);
    out.push((t - a0) as f64);
    let touch = (lo..a0.min(t)).rev().find(|&x| {
        let xu = x as usize;
        m.o[xu] != 0 && !bars.contains(&x.div_euclid(TF)) && fr.h(xu) >= band
    });
    match touch {
        Some(x) => {
            out.push((t - x) as f64 / 60.0);
            out.push(0.0);
        }
        None => {
            let mb = bars.iter().copied().max().unwrap_or(0);
            out.push((t - (mb + 1) * TF) as f64 / 60.0);
            out.push(1.0);
        }
    }
}

/// `sparse_features`: пустые минутки (`emp*`, `zv*`) и плоские (`flat60`).
fn sparse_features(m: &Minutes, first_valid: i64, t: i64, out: &mut Vals) {
    // emp: (n − #{o ≠ 0}) / n; zv: #{qv == 0} / n; окно — не короче 30 минут от первой минутки с данными
    let frac = |w: i64, pred: &dyn Fn(usize) -> bool, invert: bool| {
        let a = (t - w).max(first_valid);
        let n = t - a;
        if n < 30 {
            return NAN;
        }
        let k = (a..t).filter(|&x| pred(x as usize)).count() as i64;
        (if invert { n - k } else { k }) as f64 / n as f64
    };
    let nz = |x: usize| m.o[x] != 0;
    let zq = |x: usize| m.qv[x] == 0;
    out.push(frac(120, &nz, true));
    out.push(frac(360, &nz, true));
    let flat = (t - 60..t)
        .filter(|&x| m.o[x as usize] != 0 && m.h[x as usize] == m.l[x as usize])
        .count();
    out.push(flat as f64 / 60.0);
    out.push(frac(120, &zq, false));
    out.push(frac(360, &zq, false));
}

/// `tick_sparse_features`: `pre` — времена сделок строго раньше сигнальной в окне [сигнал − 60 мин, сигнал).
fn tick_sparse_features(pre: &[i64], sig: i64, out: &mut Vals) {
    let t15 = pre.iter().filter(|&&x| x >= sig - 15 * MIN_MS).count();
    let t60 = pre.len();
    let gap = match (pre.first(), pre.last()) {
        (Some(&f), Some(&l)) => {
            let mut mx = f - (sig - 60 * MIN_MS);
            for w in pre.windows(2) {
                mx = mx.max(w[1] - w[0]);
            }
            mx.max(sig - l) as f64 / 1000.0
        }
        _ => 3600.0,
    };
    out.push(t15 as f64 / 15.0);
    out.push(t60 as f64 / 60.0);
    out.push(gap);
}

/// `approach_features`: ход цены к сигналу, диапазоны, консолидация под уровнем.
fn approach_features(fr: &Fr, cf: &[f64], t: i64, r: i64, natr: f64, out: &mut Vals) {
    let m = fr.m;
    let sf = fr.s as f64;
    let c1 = cf[(t - 1) as usize];
    for n in [5i64, 15, 60] {
        let c0 = cf[(t - 1 - n) as usize];
        out.push(if c1.is_nan() || c0.is_nan() {
            NAN
        } else {
            sf * (c1 - c0) / c1 / natr
        });
    }
    let w60 = (t - 60) as usize..t as usize;
    let mut best: Option<f64> = None;
    for x in w60.clone().filter(|&x| m.o[x] != 0) {
        let (cc, oo) = (m.c[x] as f64, m.o[x] as f64);
        let v = sf * (cc - oo) / cc;
        best = Some(best.map_or(v, |b| if v > b { v } else { b }));
    }
    out.push(best.map_or(NAN, |b| b / natr));
    let mut best: Option<f64> = None;
    for x in ((t - 15) as usize..t as usize).filter(|&x| m.o[x] != 0) {
        let v = (m.h[x] - m.l[x]) as f64 / m.c[x] as f64;
        best = Some(best.map_or(v, |b| if v > b { v } else { b }));
    }
    out.push(best.map_or(NAN, |b| b / natr));
    let band = r.abs() * B_PM / 100_000;
    for w in [60i64, 120] {
        let k = ((t - w) as usize..t as usize)
            .filter(|&x| {
                let cl = fr.c(x);
                m.o[x] != 0 && cl < r && r - cl <= band
            })
            .count();
        out.push(k as f64);
    }
}

/// `context_features`.
fn context_features(m: &Minutes, t: i64, natr_e8: i64, out: &mut Vals) {
    let sum = |a: usize, b: usize| m.qv[a..b].iter().fold(0i64, |s, &x| s.wrapping_add(x));
    out.push(natr_e8 as f64 / 1e4);
    out.push(sum((t - 60) as usize, t as usize) as f64 / 1e5);
    out.push(if t >= 1440 {
        sum((t - 1440) as usize, t as usize) as f64 / 1e5
    } else {
        NAN
    });
}

/// `corr_features`: корреляции с BTC/ETH (блоки 5 и 15 минут, минутки) и их движение за 5/15 минут.
fn corr_features(c: &CoinCtx, refs: &Refs, sym: &str, s: i64, t: i64, out: &mut Vals) {
    let (b5, b15) = (t / 5 - 1, t / 15 - 1);
    for (name, rf) in &refs.refs {
        if sym == *name {
            for _ in 0..5 {
                out.push(NAN);
            }
        } else {
            out.push(window_corr(&c.r5, &rf.r5, b5, 2016, 700));
            out.push(window_corr(&c.r5, &rf.r5, b5, 288, 100));
            out.push(window_corr(&c.r15, &rf.r15, b15, 672, 250));
            out.push(window_corr(&c.r15, &rf.r15, b15, 96, 40));
            let (a, e) = ((t - 360) as usize, t as usize);
            out.push(match (c.r1.get(a..e), rf.r1.get(a..e)) {
                (Some(x), Some(y)) => pearson_pairs(x, y, 120),
                _ => NAN,
            });
        }
        for n in [5i64, 15] {
            out.push(
                (rf.cf[(t - 1) as usize] / rf.cf[(t - 1 - n) as usize] - 1.0) * 1e4 * s as f64,
            );
        }
    }
}

/// Признаки одного эпизода; `pre` — времена сделок окна строго раньше сигнальной.
pub fn episode_features(
    c: &CoinCtx,
    refs: &Refs,
    sym: &str,
    s: i64,
    sig: &Sig,
    pre: &[i64],
    sig_abs: i64,
) -> [f64; NF] {
    let t = sig.sig_ms.div_euclid(MIN_MS);
    let r = s * sig.level_px_e8;
    let fr = Fr { m: &c.m, s };
    let mut out = Vals { v: [NAN; NF], n: 0 };
    let bars: Vec<i64> = sig.members.iter().map(|x| x.0).collect();
    level_features(
        &fr,
        t,
        r,
        &sig.members,
        sig.born,
        sig.first_bar,
        sig.dist_e8,
        &mut out,
    );
    fresh_prior_features(&fr, t, r, &bars, sig.first_bar, &mut out);
    sparse_features(&c.m, c.first_valid as i64, t, &mut out);
    tick_sparse_features(pre, sig_abs, &mut out);
    approach_features(
        &fr,
        &c.cf,
        t,
        r,
        sig.natr_e8 as f64 / SCALE as f64,
        &mut out,
    );
    context_features(&c.m, t, sig.natr_e8, &mut out);
    corr_features(c, refs, sym, s, t, &mut out);
    debug_assert_eq!(out.n, NF);
    out.v
}

// ───────────────────────────── сделки ─────────────────────────────

/// Сутки сделок: время, мс от 1970 (`transact_time`), и цена ×1e8 (`rint(price·1e8)`); порядок — как в файле, при убывании времени —
/// устойчивая сортировка (Python: `np.argsort(kind="stable")`).
#[derive(Debug, Default, Clone)]
pub struct PxDay {
    pub t: Vec<i64>,
    pub p: Vec<i64>,
    pub unsorted: bool,
}

pub trait PxSrc: Sync {
    fn day(&self, sym: &str, abs_day: i64) -> Result<Option<PxDay>>;
}

/// `<корень>/<SYM>/<SYM>-aggTrades-<день>.zip` (внешний `unzip -p`), корни по порядку, берётся первый существующий файл.
pub struct ZipPxSrc {
    pub roots: Vec<PathBuf>,
}

impl PxSrc for ZipPxSrc {
    fn day(&self, sym: &str, abs_day: i64) -> Result<Option<PxDay>> {
        let name = format!("{sym}-aggTrades-{}", date_str(abs_day));
        for root in &self.roots {
            let path = root.join(sym).join(format!("{name}.zip"));
            if !path.exists() {
                continue;
            }
            let mut child = Command::new("unzip")
                .arg("-p")
                .arg(&path)
                .arg(format!("{name}.csv"))
                .stdout(Stdio::piped())
                .stderr(Stdio::null())
                .spawn()
                .with_context(|| format!("unzip {}", path.display()))?;
            let out = child.stdout.take().context("unzip: нет stdout")?;
            let parsed = parse_px_csv(BufReader::with_capacity(1 << 20, out));
            let status = child.wait()?;
            let d = parsed.with_context(|| path.display().to_string())?;
            if !status.success() {
                bail!("unzip {}: {status}", path.display());
            }
            return Ok(Some(d));
        }
        Ok(None)
    }
}

/// Склад TK-607 для суток цены: `ZipSrc::read_day` (чтение склада; в режиме «только чтение» — `cap_gb = 0` — промах разбирает zip без записи).
/// Склад хранит `rint(price·1e8)` (разбор десятичной записи) и смещение времени в сутках; для признаков f12 берутся сутки без `F_PX_INEXACT`,
/// без строк вне суток и без плохих строк — иначе прежний `ZipPxSrc` (Python не отсеивает строки вне суток). Флаги количества цену не касаются.
/// `verify` — каждые сутки склада сверяются с `ZipPxSrc` побитово и при расхождении отдаётся его результат.
pub struct StorePxSrc {
    srcs: Vec<ZipSrc>,
    exact: ZipPxSrc,
    day0: i64,
    verify: bool,
    pub store_days: AtomicU64,
    pub fallback: AtomicU64,
    pub mismatch: AtomicU64,
}

impl StorePxSrc {
    /// `first_abs_day` — первые запрошенные сутки: сетка движка (`P42_T0`) ставится на них, номер суток склада — от неё.
    pub fn new(
        roots: &[PathBuf],
        store: &Arc<TickStore>,
        first_abs_day: i64,
        verify: bool,
    ) -> Result<StorePxSrc> {
        std::env::set_var("P42_T0", date_str(first_abs_day));
        let t0 = crate::breakout::data::t0_ms();
        if t0 != first_abs_day * DAY_MS {
            bail!(
                "сетка склада уже зафиксирована на {t0} мс, нужно {}",
                first_abs_day * DAY_MS
            );
        }
        Ok(StorePxSrc {
            srcs: roots
                .iter()
                .map(|r| ZipSrc::new(r.clone(), 0).with_store(Arc::clone(store)))
                .collect(),
            exact: ZipPxSrc {
                roots: roots.to_vec(),
            },
            day0: first_abs_day,
            verify,
            store_days: AtomicU64::new(0),
            fallback: AtomicU64::new(0),
            mismatch: AtomicU64::new(0),
        })
    }
}

/// Сутки склада → время и цена ×1e8; `None` — сутки с отступлениями (см. `StorePxSrc`).
fn px_from_store(d: &StoredDay, abs_day: i64) -> Option<PxDay> {
    if d.flags & F_PX_INEXACT != 0 || d.stat.out_of_day != 0 || d.stat.bad_rows != 0 {
        return None;
    }
    let base = abs_day * DAY_MS;
    Some(PxDay {
        t: d.ticks.iter().map(|k| base + i64::from(k.off)).collect(),
        p: d.ticks.iter().map(|k| k.px).collect(),
        unsorted: d.stat.unsorted_files > 0,
    })
}

fn same_px(a: &PxDay, b: &PxDay) -> bool {
    a.t == b.t && a.p == b.p && a.unsorted == b.unsorted
}

impl PxSrc for StorePxSrc {
    fn day(&self, sym: &str, abs_day: i64) -> Result<Option<PxDay>> {
        let idx = abs_day - self.day0;
        if idx < 0 {
            return self.exact.day(sym, abs_day);
        }
        for z in &self.srcs {
            let Some(stored) = z.read_day(sym, idx as usize)? else {
                continue;
            };
            let Some(got) = px_from_store(&stored, abs_day) else {
                self.fallback.fetch_add(1, Ordering::Relaxed);
                return self.exact.day(sym, abs_day);
            };
            if self.verify {
                let want = self.exact.day(sym, abs_day)?;
                if !want.as_ref().is_some_and(|w| same_px(w, &got)) {
                    if self.mismatch.fetch_add(1, Ordering::Relaxed) < 5 {
                        eprintln!("склад ≠ zip: {sym} {}", date_str(abs_day));
                    }
                    return Ok(want);
                }
            }
            self.store_days.fetch_add(1, Ordering::Relaxed);
            return Ok(Some(got));
        }
        Ok(None)
    }
}

fn trim_eol(l: &[u8]) -> &[u8] {
    let mut e = l.len();
    while e > 0 && (l[e - 1] == b'\n' || l[e - 1] == b'\r') {
        e -= 1;
    }
    &l[..e]
}

/// CSV суток (`price`, `transact_time` по именам) → время и `rint(price·1e8)` (`Ticks._read`).
pub fn parse_px_csv(mut rd: impl BufRead) -> Result<PxDay> {
    let mut line = Vec::with_capacity(256);
    if rd.read_until(b'\n', &mut line)? == 0 {
        return Ok(PxDay::default());
    }
    let head: Vec<&[u8]> = trim_eol(&line).split(|&c| c == b',').collect();
    let col = |n: &str| {
        head.iter()
            .position(|h| *h == n.as_bytes())
            .with_context(|| format!("нет столбца {n}"))
    };
    let (ip, it) = (col("price")?, col("transact_time")?);
    let need = ip.max(it) + 1;
    let mut d = PxDay::default();
    loop {
        line.clear();
        if rd.read_until(b'\n', &mut line)? == 0 {
            break;
        }
        let l = trim_eol(&line);
        if l.is_empty() {
            continue;
        }
        let (mut p, mut t, mut cnt) = (&l[..0], &l[..0], 0);
        for (i, f) in l.split(|&c| c == b',').enumerate() {
            if i == ip {
                p = f;
            } else if i == it {
                t = f;
            }
            cnt = i + 1;
            if i + 1 >= need {
                break;
            }
        }
        if cnt < need {
            bail!("строка с {cnt} полями: {:?}", String::from_utf8_lossy(l));
        }
        d.t.push(parse_i64(t)?);
        d.p.push(px_e8(parse_f64(p)?)?);
    }
    if d.t.len() > 1 && d.t.windows(2).any(|w| w[1] < w[0]) {
        let mut ord: Vec<usize> = (0..d.t.len()).collect();
        ord.sort_by_key(|&i| d.t[i]);
        d.t = ord.iter().map(|&i| d.t[i]).collect();
        d.p = ord.iter().map(|&i| d.p[i]).collect();
        d.unsorted = true;
    }
    Ok(d)
}

/// `np.rint(price * 1e8).astype(np.int64)`.
fn px_e8(price: f64) -> Result<i64> {
    let x = price * 1e8;
    if !x.is_finite() || x.abs() >= 9.2e18 {
        bail!("цена вне диапазона: {price}");
    }
    Ok(x.round_ties_even() as i64)
}

/// Мелкий кэш суток монеты (по возрастанию дня сигналов хватает трёх).
struct DayCache<'a> {
    src: &'a dyn PxSrc,
    sym: &'a str,
    base_day: i64,
    days: i64,
    slots: Vec<(i64, Option<Arc<PxDay>>)>,
    loaded: u64,
    unsorted: u64,
    irregular: u64,
}

impl DayCache<'_> {
    fn get(&mut self, abs_day: i64) -> Result<Option<Arc<PxDay>>> {
        let rel = abs_day - self.base_day;
        if rel < 0 || rel >= self.days {
            return Ok(None);
        }
        if let Some((_, d)) = self.slots.iter().find(|(k, _)| *k == abs_day) {
            return Ok(d.clone());
        }
        let d = self.src.day(self.sym, abs_day)?.map(Arc::new);
        if let Some(x) = &d {
            self.loaded += 1;
            self.unsorted += u64::from(x.unsorted);
            let (lo, hi) = (abs_day * DAY_MS, (abs_day + 1) * DAY_MS);
            if x.t.first().is_some_and(|&f| f < lo) || x.t.last().is_some_and(|&l| l >= hi) {
                self.irregular += 1;
            }
        }
        self.slots.push((abs_day, d.clone()));
        if self.slots.len() > 3 {
            self.slots.remove(0);
        }
        Ok(d)
    }
}

/// Сделки окна [сигнал − 60 мин, сигнал]: срезы по суткам, склеенные в порядке суток (`Ticks.span`).
fn window<'a>(
    cur: Option<&'a PxDay>,
    prev: Option<&'a PxDay>,
    a0: i64,
    sig: i64,
) -> (Cow<'a, [i64]>, Cow<'a, [i64]>) {
    let cut = |d: &'a PxDay| {
        let i0 = d.t.partition_point(|&x| x < a0);
        let i1 = d.t.partition_point(|&x| x <= sig);
        (&d.t[i0..i1.max(i0)], &d.p[i0..i1.max(i0)])
    };
    match (prev, cur) {
        (Some(pd), Some(cd)) => {
            let ((pt, pp), (ct, cp)) = (cut(pd), cut(cd));
            (Cow::Owned([pt, ct].concat()), Cow::Owned([pp, cp].concat()))
        }
        (Some(d), None) | (None, Some(d)) => {
            let (t, p) = cut(d);
            (Cow::Borrowed(t), Cow::Borrowed(p))
        }
        (None, None) => (Cow::Borrowed(&[]), Cow::Borrowed(&[])),
    }
}

// ───────────────────────────── монета и запуск ─────────────────────────────

/// Строка таблицы: служебные столбцы и признаки (`None` — сигнальная сделка не найдена, как у `CandWorker`).
pub struct Row {
    pub ep_no: usize,
    pub level: i64,
    pub sig_ms: i64,
    pub day: i64,
    pub lp_e8: i64,
    pub natr_e8: i64,
    pub dist_e8: i64,
    pub emp_coin: f64,
    pub feats: Option<[f64; NF]>,
}

#[derive(Debug, Default, Clone)]
pub struct CoinStat {
    pub locate_fail: u64,
    pub natr_mismatch: u64,
    pub days_loaded: u64,
    pub unsorted_days: u64,
    /// Сутки с временем вне своих суток: допущение «срез до сигнала ≡ срез до +240 мин» для них не проверяется.
    pub irregular_days: u64,
}

/// `Worker.process` + `CandWorker.do_universe` для одной монеты: `sigs` — сигналы монеты в порядке `(level, sig_ms)`.
#[allow(clippy::too_many_arguments)]
pub fn coin_rows(
    src: &dyn PxSrc,
    refs: &Refs,
    ctx: &CoinCtx,
    sym: &str,
    s: i64,
    base_ms: i64,
    days: i64,
    sigs: &[Sig],
) -> Result<(Vec<Row>, CoinStat)> {
    let base_day = base_ms.div_euclid(DAY_MS);
    let mut cache = DayCache {
        src,
        sym,
        base_day,
        days,
        slots: Vec::new(),
        loaded: 0,
        unsorted: 0,
        irregular: 0,
    };
    let mut st = CoinStat::default();
    let mut order: Vec<usize> = (0..sigs.len()).collect();
    order.sort_by_key(|&i| sigs[i].sig_ms.div_euclid(DAY_MS)); // устойчиво: внутри суток — порядок потока
    let mut rows: Vec<Option<Row>> = (0..sigs.len()).map(|_| None).collect();
    for i in order {
        let sg = &sigs[i];
        let t = sg.sig_ms.div_euclid(MIN_MS);
        // ниже 360 минут окна Python уходят в отрицательные индексы numpy (мусор) — порт отказывает, а не воспроизводит его
        if t < 360 || t as usize >= ctx.m.n() {
            bail!("{sym}: минута сигнала {t} вне диапазона 360…{}", ctx.m.n());
        }
        if sg.natr_e8 != ctx.natr[(t - 1) as usize] {
            st.natr_mismatch += 1;
        }
        let day = sg.sig_ms.div_euclid(DAY_MS);
        let r = s * sg.level_px_e8;
        let sig_abs = base_ms + sg.sig_ms;
        let a0 = sig_abs - 60 * MIN_MS;
        let cur = cache.get(base_day + day)?;
        let prev = if a0 < (base_day + day) * DAY_MS {
            cache.get(base_day + day - 1)?
        } else {
            None
        };
        let (tt, pp) = window(cur.as_deref(), prev.as_deref(), a0, sig_abs);
        let i0 = tt.partition_point(|&x| x < sig_abs);
        let i1 = tt.partition_point(|&x| x <= sig_abs);
        let k = (i0..i1).find(|&j| dist_e8(r, s * pp[j]) == sg.dist_e8);
        let mut row = Row {
            ep_no: 0,
            level: sg.level,
            sig_ms: sg.sig_ms,
            day,
            lp_e8: r.abs(),
            natr_e8: sg.natr_e8,
            dist_e8: sg.dist_e8,
            emp_coin: ctx.emp_coin,
            feats: None,
        };
        match k {
            Some(k) => {
                row.feats = Some(episode_features(ctx, refs, sym, s, sg, &tt[..k], sig_abs));
            }
            None => st.locate_fail += 1,
        }
        if let Some(slot) = rows.get_mut(i) {
            *slot = Some(row);
        }
    }
    st.days_loaded = cache.loaded;
    st.unsorted_days = cache.unsorted;
    st.irregular_days = cache.irregular;
    let mut out: Vec<Row> = rows.into_iter().flatten().collect();
    for (n, r) in out.iter_mut().enumerate() {
        r.ep_no = n;
    }
    Ok((out, st))
}

/// Разбор `members`: `бар:цена|бар:цена|…`.
fn parse_members(b: &str) -> Result<Vec<(i64, i64)>> {
    b.split('|')
        .map(|x| {
            let (a, p) = x
                .split_once(':')
                .with_context(|| format!("член уровня без «:»: {x:?}"))?;
            Ok((parse_i64(a.as_bytes())?, parse_i64(p.as_bytes())?))
        })
        .collect()
}

/// `<side>-sigsx.csv` (`read_sigsx`): строки по монетам, внутри монеты — порядок `(level, sig_ms)`; столбцы по позиции
/// (`sym, level, sig_ms, dist_e8, level_px_e8, born, end, first_bar, natr_e8, members`; в заголовке бинаря `natr_e8` нет).
pub fn read_sigsx(path: &Path, coins: Option<&[String]>) -> Result<BTreeMap<String, Vec<Sig>>> {
    let mut rd = csv::ReaderBuilder::new()
        .has_headers(true)
        .flexible(true) // в заголовке бинаря прогона `natr_e8` нет — 9 имён при 10 полях строк
        .from_path(path)
        .with_context(|| path.display().to_string())?;
    let mut by: BTreeMap<String, Vec<Sig>> = BTreeMap::new();
    for (ln, rec) in rd.records().enumerate() {
        let rec = rec?;
        let ctx = || format!("{}: строка {}", path.display(), ln + 2);
        if rec.len() < 10 {
            bail!("{}: {} полей вместо 10", ctx(), rec.len());
        }
        let f = |i: usize| rec.get(i).unwrap_or("").trim();
        let sym = f(0);
        if coins.is_some_and(|c| !c.iter().any(|x| x == sym)) {
            continue;
        }
        let int = |i: usize| parse_i64(f(i).as_bytes()).with_context(ctx);
        by.entry(sym.to_string()).or_default().push(Sig {
            level: int(1)?,
            sig_ms: int(2)?,
            dist_e8: int(3)?,
            level_px_e8: int(4)?,
            born: int(5)?,
            first_bar: int(7)?,
            natr_e8: int(8)?,
            members: parse_members(f(9)).with_context(ctx)?,
        });
    }
    for v in by.values_mut() {
        v.sort_by_key(|x| (x.level, x.sig_ms));
    }
    Ok(by)
}

pub struct F12Cfg {
    pub sigsx: PathBuf,
    /// `long` / `short`.
    pub side: String,
    pub cache: PathBuf,
    pub trades: Vec<PathBuf>,
    pub out: PathBuf,
    pub base_date: String,
    pub threads: usize,
    pub coins: Option<Vec<String>>,
    /// Суток в сетке минуток (`SIGFEAT_DAYS` Python): `N_MIN = days · 1440` — длина `.b42`, область `emp_coin`.
    pub days: i64,
}

#[derive(Debug, Clone)]
pub struct Summary {
    pub episodes: usize,
    pub coins: usize,
    pub seconds: f64,
    pub stat: CoinStat,
}

impl Summary {
    pub fn json(&self) -> String {
        let s = &self.stat;
        format!(
            "{{\"episodes\":{},\"coins\":{},\"seconds\":{:.1},\"locate_fail\":{},\"natr_mismatch\":{},\"days_loaded\":{},\"unsorted_days\":{},\"irregular_days\":{}}}",
            self.episodes, self.coins, self.seconds, s.locate_fail, s.natr_mismatch, s.days_loaded, s.unsorted_days, s.irregular_days
        )
    }
}

fn push_f64(out: &mut String, x: f64) {
    if !x.is_nan() {
        let _ = write!(out, "{x:?}");
    }
}

/// `lturn24 = log10(turn24h.clip(lower=1))`.
fn lturn24(turn24h: f64) -> f64 {
    if turn24h.is_nan() {
        NAN
    } else {
        turn24h.max(1.0).log10()
    }
}

/// Заголовок CSV.
pub fn header() -> String {
    let mut v: Vec<&str> = META.to_vec();
    v.extend(NAMES);
    v.push("lturn24");
    v.join(",")
}

/// Строка CSV: float — кратчайшая круговая запись (`{:?}`), NaN — пусто; у `CandWorker` без сигнальной сделки заполнены только
/// `ep_id, univ, side, sym, level, ep_no, sig_ms, day, tick_ok`.
pub fn csv_row(sym: &str, side: &str, r: &Row) -> String {
    let mut s = String::with_capacity(512);
    let _ = write!(
        s,
        "U0|{side}|{sym}|{}|{},U0,{side},{sym},{},{},{},{},",
        r.level, r.ep_no, r.level, r.ep_no, r.sig_ms, r.day
    );
    match &r.feats {
        None => {
            // half, n_sig_ep, last_sig_ms, lp_e8, natr_e8, dist_e8, emp_coin, hour4, wday — пусто; tick_ok = False
            s.push_str(",,,,,,,,,False");
            s.push_str(&",".repeat(NF + 1));
        }
        Some(f) => {
            let half = if r.day < START_DAY + 30 {
                "A"
            } else if r.day < START_DAY + 60 {
                "B"
            } else {
                "H"
            };
            let _ = write!(
                s,
                "{half},1,{},{},{},{},",
                r.sig_ms, r.lp_e8, r.natr_e8, r.dist_e8
            );
            push_f64(&mut s, r.emp_coin);
            let _ = write!(
                s,
                ",{},{},True",
                (r.sig_ms % DAY_MS) / (4 * 3_600_000),
                (r.day + 1) % 7
            );
            for (i, &x) in f.iter().enumerate() {
                s.push(',');
                if INT_FEATS.contains(&NAMES[i]) && x.is_finite() {
                    let _ = write!(s, "{}", x as i64);
                } else {
                    push_f64(&mut s, x);
                }
            }
            s.push(',');
            let turn = NAMES
                .iter()
                .position(|&n| n == "turn24h")
                .map_or(NAN, |i| f[i]);
            push_f64(&mut s, lturn24(turn));
        }
    }
    s
}

pub fn run(cfg: &F12Cfg, src: &dyn PxSrc) -> Result<Summary> {
    let t0 = Instant::now();
    let s = match cfg.side.as_str() {
        "long" => 1,
        "short" => -1,
        o => bail!("--side: long|short, получено {o}"),
    };
    let base_ms = parse_date(&cfg.base_date)? * DAY_MS;
    let n_min = (cfg.days * 1440) as usize;
    let by = read_sigsx(&cfg.sigsx, cfg.coins.as_deref())?;
    if by.is_empty() {
        bail!("пустой вход: {}", cfg.sigsx.display());
    }
    let refs = Refs::load(&cfg.cache, n_min)?;
    let mut jobs: Vec<(&String, &Vec<Sig>)> = by.iter().collect();
    jobs.sort_by_key(|(_, v)| std::cmp::Reverse(v.len()));
    eprintln!(
        "эпизодов {}; монет {}",
        jobs.iter().map(|j| j.1.len()).sum::<usize>(),
        jobs.len()
    );
    let next = AtomicUsize::new(0);
    let finished = AtomicUsize::new(0);
    type Done = (usize, Result<(Vec<Row>, CoinStat)>);
    let results: Mutex<Vec<Done>> = Mutex::new(Vec::with_capacity(jobs.len()));
    std::thread::scope(|sc| {
        for _ in 0..cfg.threads.max(1) {
            sc.spawn(|| loop {
                let j = next.fetch_add(1, Ordering::SeqCst);
                let Some((sym, sigs)) = jobs.get(j) else {
                    break;
                };
                let r = (|| {
                    let m = Minutes::load(&cfg.cache.join(format!("{sym}.b42")), n_min)?;
                    let ctx = CoinCtx::new(m);
                    coin_rows(src, &refs, &ctx, sym, s, base_ms, cfg.days, sigs)
                })()
                .with_context(|| format!("монета {sym}"));
                results
                    .lock()
                    .unwrap_or_else(PoisonError::into_inner)
                    .push((j, r));
                let n = finished.fetch_add(1, Ordering::SeqCst) + 1;
                if n.is_multiple_of(20) {
                    eprintln!(
                        "{n}/{} монет, {:.0} с",
                        jobs.len(),
                        t0.elapsed().as_secs_f64()
                    );
                }
            });
        }
    });
    let mut done = results.into_inner().unwrap_or_else(PoisonError::into_inner);
    done.sort_by(|a, b| jobs[a.0].0.cmp(jobs[b.0].0));
    let mut text = header();
    text.push('\n');
    let mut total = CoinStat::default();
    let mut episodes = 0;
    for (j, r) in done {
        let (rows, st) = r?;
        total.locate_fail += st.locate_fail;
        total.natr_mismatch += st.natr_mismatch;
        total.days_loaded += st.days_loaded;
        total.unsorted_days += st.unsorted_days;
        total.irregular_days += st.irregular_days;
        episodes += rows.len();
        for row in &rows {
            text.push_str(&csv_row(jobs[j].0, &cfg.side, row));
            text.push('\n');
        }
    }
    std::fs::write(&cfg.out, text).with_context(|| cfg.out.display().to_string())?;
    Ok(Summary {
        episodes,
        coins: jobs.len(),
        seconds: t0.elapsed().as_secs_f64(),
        stat: total,
    })
}
