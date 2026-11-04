//! Эталон `build_cube_naive` (TK-492): тот же куб, что `feat::build_cube`, но «по определению» П-34 §3 и допущениям
//! шапки `feat.rs` — без быстрых приёмов: признак `x` каждого слота пересчитывается прямо из минут и слотов OI,
//! окно нормы собирается заново на каждом слоте, сортируется целиком (медиана), затем сортируются расстояния
//! (MAD). Только для сверки в тестах; боевой путь — `feat::build_cube`.

use super::cache::{Contract, SLOT_MIN};
use super::params::Params;
use super::types::{
    Cube, F_FUND, F_GLOBAL, F_OI_PCT, F_OI_USD, F_TAKER, F_TOP_COUNT, F_TOP_SUM, F_TRADES, F_TURN,
    F_VR, NF,
};

/// Минут в сутках.
const DAY_MIN: i64 = 24 * 60;

struct Naive<'a> {
    c: &'a Contract,
    cf: Vec<f64>,
    first: i64,
    last: i64,
    slot0: i64,
    lag: i64,
}

fn median(sorted: &[f64]) -> f64 {
    let m = sorted.len();
    if m % 2 == 1 {
        sorted[m / 2]
    } else {
        (sorted[m / 2 - 1] + sorted[m / 2]) / 2.0
    }
}

impl Naive<'_> {
    fn close(&self, m: i64) -> f64 {
        if m < self.first || m > self.last {
            f64::NAN
        } else {
            self.cf[(m - self.first) as usize]
        }
    }

    /// `ln(c[m]/c[m−1])`; `NaN`, если одного из close нет.
    fn ret(&self, m: i64) -> f64 {
        (self.close(m) / self.close(m - 1)).ln()
    }

    /// Ст. откл. (по 1/N) `nmin` минутных доходностей `(m_end − nmin, m_end]`; `NaN`, если хоть одна не определена.
    fn std_win(&self, m_end: i64, nmin: i64) -> f64 {
        let v: Vec<f64> = (m_end - nmin + 1..=m_end).map(|m| self.ret(m)).collect();
        if v.iter().any(|x| x.is_nan()) {
            return f64::NAN;
        }
        let n = nmin as f64;
        let mean = v.iter().sum::<f64>() / n;
        let var = v.iter().map(|x| (x - mean) * (x - mean)).sum::<f64>() / n;
        var.sqrt()
    }

    fn oi(&self, col: usize, u: i64) -> f64 {
        let j = u - self.c.oi_first_slot;
        if j < 0 || j as usize >= self.c.oi[col].len() {
            f64::NAN
        } else {
            self.c.oi[col][j as usize]
        }
    }

    /// Сумма колонки за минуты `(t − w, t]`; `NaN`, если окно выходит за ряд.
    fn sum(&self, col: &[f32], t: i64, w: i64) -> f64 {
        if t - w + 1 < self.first || t > self.last {
            return f64::NAN;
        }
        let mut s = 0.0;
        for m in t - w + 1..=t {
            s += f64::from(col[(m - self.first) as usize]);
        }
        s
    }

    /// Последняя ставка с минутой выплаты `≤ t` (при равных минутах — последняя в списке).
    fn fund(&self, t: i64) -> f64 {
        let mut best: Option<(i64, f64)> = None;
        for &(m, r) in &self.c.funding {
            if m <= t && best.is_none_or(|(bm, _)| m >= bm) {
                best = Some((m, r));
            }
        }
        best.map_or(f64::NAN, |(_, r)| r)
    }

    /// Сырое значение признака `f` окна `w` минут в слоте с индексом `i` области куба.
    fn raw(&self, p: &Params, f: usize, w: i64, i: usize) -> f64 {
        let s = self.slot0 + i as i64;
        let t = s * SLOT_MIN;
        let k = w / SLOT_MIN;
        let u = s - self.lag;
        let v = match f {
            F_OI_PCT => {
                let (a, b) = (self.oi(0, u), self.oi(0, u - k));
                if b > 0.0 {
                    (a - b) / b
                } else {
                    f64::NAN
                }
            }
            F_OI_USD => self.oi(1, u) - self.oi(1, u - k),
            F_TURN => self.sum(&self.c.qvol, t, w),
            F_TRADES => self.sum(&self.c.trades, t, w),
            F_TAKER => self.oi(5, u),
            F_TOP_COUNT => self.oi(2, u),
            F_TOP_SUM => self.oi(3, u),
            F_GLOBAL => self.oi(4, u),
            F_VR => {
                let hist_min = i64::from(p.features.norm.history_days) * DAY_MIN;
                let (sw, sh) = (self.std_win(t, w), self.std_win(t - w, hist_min));
                if sh > 0.0 {
                    sw / sh
                } else {
                    f64::NAN
                }
            }
            F_FUND => self.fund(t),
            _ => f64::NAN,
        };
        if v.is_finite() {
            v
        } else {
            f64::NAN
        }
    }
}

/// Робастный z слота `i` по ряду `xs`: норма — значения `xs[i−k−hist+1..=i−k]` без `NaN`; окно должно целиком
/// лежать в данных ряда (начинаться не раньше `v0` — первого определённого значения). `MAD = 0`: `x = med` → 0,
/// иначе `±∞` (допущения 2 и 10 шапки `feat.rs`).
fn z_naive(xs: &[f64], v0: Option<usize>, i: usize, k: usize, hist: usize, factor: f64) -> f64 {
    let end = i as i64 - k as i64;
    let start = end - hist as i64 + 1;
    let Some(v0) = v0 else {
        return f64::NAN;
    };
    if start < v0 as i64 || xs[i].is_nan() {
        return f64::NAN;
    }
    let mut v: Vec<f64> = xs[start as usize..=end as usize]
        .iter()
        .copied()
        .filter(|x| !x.is_nan())
        .collect();
    if v.is_empty() {
        return f64::NAN;
    }
    v.sort_by(f64::total_cmp);
    let med = median(&v);
    let mut d: Vec<f64> = v.iter().map(|x| (x - med).abs()).collect();
    d.sort_by(f64::total_cmp);
    let mad = median(&d);
    let dev = xs[i] - med;
    if mad == 0.0 {
        if dev == 0.0 {
            0.0
        } else {
            f64::INFINITY.copysign(dev)
        }
    } else {
        dev / (factor * mad)
    }
}

/// Эталонный куб: те же размеры, раскладка и значения (до округления f32 и ошибок суммирования σ), что у
/// `feat::build_cube`.
pub fn build_cube_naive(c: &Contract, p: &Params, oi_lag_slots: u32) -> Cube {
    let ws = &p.features.windows_min;
    let nw = ws.len();
    let slot0 = (c.first_min + SLOT_MIN - 1).div_euclid(SLOT_MIN);
    let last = c.first_min + c.close.len() as i64 - 1;
    let n = if c.close.is_empty() || last.div_euclid(SLOT_MIN) < slot0 {
        0
    } else {
        (last.div_euclid(SLOT_MIN) - slot0 + 1) as usize
    };
    let mut cube = Cube {
        slot0,
        n,
        windows: ws.clone(),
        z: vec![f32::NAN; NF * nw * n],
        r: vec![f32::NAN; nw * n],
        sigma: vec![f32::NAN; n],
    };
    let nv = Naive {
        c,
        cf: c.close_ff(),
        first: c.first_min,
        last,
        slot0,
        lag: i64::from(oi_lag_slots),
    };
    let per_day = (DAY_MIN / SLOT_MIN) as usize;
    let hist = p.features.norm.history_days as usize * per_day;
    let hist_f = p.features.funding_z_history_days as usize * per_day;
    let sigma_min = i64::from(p.controls.sigma_1m_window_days) * DAY_MIN;
    let factor = p.features.norm.mad_factor;
    let minute = |i: usize| (slot0 + i as i64) * SLOT_MIN;
    for (i, s) in cube.sigma.iter_mut().enumerate() {
        *s = nv.std_win(minute(i), sigma_min) as f32;
    }
    for (wi, &w) in ws.iter().enumerate() {
        for (i, ri) in cube.r[wi * n..(wi + 1) * n].iter_mut().enumerate() {
            let t = minute(i);
            *ri = (nv.close(t) / nv.close(t - i64::from(w))).ln() as f32;
        }
    }
    for f in 0..NF {
        let len = if f == F_FUND { hist_f } else { hist };
        for (wi, &w) in ws.iter().enumerate() {
            let xs: Vec<f64> = (0..n).map(|i| nv.raw(p, f, i64::from(w), i)).collect();
            let k = (i64::from(w) / SLOT_MIN) as usize;
            let v0 = xs.iter().position(|x| !x.is_nan());
            let o = (f * nw + wi) * n;
            for (i, zi) in cube.z[o..o + n].iter_mut().enumerate() {
                *zi = z_naive(&xs, v0, i, k, len, factor) as f32;
            }
        }
    }
    cube
}
