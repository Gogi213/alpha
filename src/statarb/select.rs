//! Отбор пар формирования (P-32 §6.1, TK-486): тест Энгла–Грейнджера на первой и на второй половине окна
//! формирования — оба прохода; порог `p` — по Холму на число кандидатов формирования. Суммы пары по половинам и по
//! всему окну берутся из блоков `EgBlocks` (сумма блоков, без пересчёта «пара × окно»); отобранные пары торгуются все,
//! `N` не назначается.
//!
//! Выбор метода, не назначенные числа: пара проходит, если оба прохода значимы, её `p` = больший из двух
//! (пересечение гипотез); тест несимметричен по ногам, поэтому каждая пара проверяется в обоих направлениях и в семью
//! Холма входят оба (`m = s·(s−1)` проверок); пара берётся в направлении с меньшим `p`. Неисполнимая проверка
//! (мало наблюдений, нулевая дисперсия) считается `p = 1`, но остаётся в `m`. Уровень `alpha` и `min_obs` — параметры
//! прогона. `p` — приближение МакКиннона (1994) для остатка регрессии с константой на `N = 2` рядах, как в
//! `statsmodels.tsa.adfvalues.mackinnonp(…, "c", N=2)`.

use super::corr::pair_idx;
use super::egmat::EgBlocks;

/// Φ(x) с относительной точностью в левом хвосте: ряд для `erf` при `|x| < 3`, цепная дробь Лежандра дальше
/// (в хвосте `p ~ 1e-6…1e-10` на 45 тыс. пар `Φ` через `1 + erf` терял бы все знаки).
pub fn norm_cdf(x: f64) -> f64 {
    if x.is_nan() {
        return f64::NAN;
    }
    if x > 0.0 {
        return 1.0 - norm_cdf(-x);
    }
    let z = -x;
    let phi = (-0.5 * z * z).exp() / (2.0 * std::f64::consts::PI).sqrt();
    if z < 3.0 {
        // Φ(−z) = ½ − φ(0)·Σ (−1)ⁿ z^(2n+1) / (2ⁿ n! (2n+1))
        let (mut term, mut sum) = (z, z);
        for n in 1..200 {
            term *= -z * z / (2.0 * f64::from(n));
            let add = term / f64::from(2 * n + 1);
            sum += add;
            if add.abs() < 1e-18 {
                break;
            }
        }
        0.5 - sum / (2.0 * std::f64::consts::PI).sqrt()
    } else {
        // Q(z) = φ(z) / (z + 1/(z + 2/(z + 3/(z + …)))) — считается с конца
        let mut f = z;
        for m in (1..=120).rev() {
            f = z + f64::from(m) / f;
        }
        phi / f
    }
}

/// Приближённое `p`-значение МакКиннона (1994) теста Энгла–Грейнджера, `N = 2` ряда, регрессия с константой:
/// `p = Φ(c₀ + c₁τ + c₂τ² + c₃τ³)`; ниже `τ*` коэффициенты левого хвоста. Границы — как в `statsmodels`.
pub fn eg_pvalue(tau: f64) -> f64 {
    const TAU_MAX: f64 = 0.92;
    const TAU_MIN: f64 = -18.86;
    const TAU_STAR: f64 = -2.62;
    const SMALL: [f64; 3] = [2.92, 1.5012, 3.9796e-2];
    const LARGE: [f64; 4] = [2.1945, 6.4695e-1, -2.9198e-1, -4.2377e-2];
    if tau.is_nan() || tau > TAU_MAX {
        return 1.0;
    }
    if tau < TAU_MIN {
        return 0.0;
    }
    let z = if tau <= TAU_STAR {
        SMALL[0] + tau * (SMALL[1] + tau * SMALL[2])
    } else {
        LARGE[0] + tau * (LARGE[1] + tau * (LARGE[2] + tau * LARGE[3]))
    };
    norm_cdf(z)
}

pub struct SelectParams {
    /// Блоки окна формирования `lo..hi`; половины — `lo..mid` и `mid..hi`, `mid = lo + (hi − lo)/2`.
    pub lo: usize,
    pub hi: usize,
    pub min_obs: u32,
    /// Уровень Холма по семье `m = s·(s−1)` проверок.
    pub alpha: f64,
}

/// Отобранная пара: `swapped = false` — нога `x` = `i`, `y` = `j`; `true` — `x` = `j`, `y` = `i`
/// (регрессия `y = α + β·x` по всему окну).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SelPair {
    pub i: usize,
    pub j: usize,
    pub swapped: bool,
    pub beta: f64,
    pub t_first: f64,
    pub t_second: f64,
    pub p: f64,
}

/// Пары, прошедшие оба прохода и Холм; порядок — по возрастанию `p`, при равенстве по `(i, j)`.
pub fn select_pairs(eb: &EgBlocks, sp: &SelectParams) -> Vec<SelPair> {
    let s = eb.s;
    let hi = sp.hi.min(eb.blocks);
    if hi < sp.lo + 2 {
        return Vec::new();
    }
    let mid = sp.lo + (hi - sp.lo) / 2;
    let m = s * s.saturating_sub(1);
    let mut cand: Vec<SelPair> = Vec::new();
    for i in 0..s {
        for j in (i + 1)..s {
            let (h1, h2) = (
                eb.pair_window(i, j, sp.lo, mid),
                eb.pair_window(i, j, mid, hi),
            );
            for swapped in [false, true] {
                let (a, b) = if swapped {
                    (h1.swapped(), h2.swapped())
                } else {
                    (h1, h2)
                };
                let (Some(r1), Some(r2)) = (a.test(sp.min_obs), b.test(sp.min_obs)) else {
                    continue;
                };
                let p = eg_pvalue(r1.t_adf).max(eg_pvalue(r2.t_adf));
                if p <= sp.alpha {
                    cand.push(SelPair {
                        i,
                        j,
                        swapped,
                        beta: f64::NAN,
                        t_first: r1.t_adf,
                        t_second: r2.t_adf,
                        p,
                    });
                }
            }
        }
    }
    cand.sort_by(|a, b| {
        a.p.total_cmp(&b.p)
            .then(a.i.cmp(&b.i))
            .then(a.j.cmp(&b.j))
            .then(a.swapped.cmp(&b.swapped))
    });
    let mut seen = vec![false; s * s.saturating_sub(1) / 2];
    let mut out = Vec::new();
    for (k, mut c) in cand.into_iter().enumerate() {
        if c.p > sp.alpha / (m - k) as f64 {
            break;
        }
        let slot = &mut seen[pair_idx(s, c.i, c.j)];
        if *slot {
            continue;
        }
        let full = eb.pair_window(c.i, c.j, sp.lo, hi);
        let full = if c.swapped { full.swapped() } else { full };
        let Some(r) = full.test(sp.min_obs) else {
            continue;
        };
        *slot = true;
        c.beta = r.beta;
        out.push(c);
    }
    out
}
