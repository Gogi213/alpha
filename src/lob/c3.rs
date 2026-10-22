//! Г-013 G1 (R4.1 `docs/research/methodology-wall-edge-2026-10-21.md`): контроль C3 «псевдо-стена».
//!
//! Чистая арифметика без ввода-вывода над готовыми рядами суток (посекундные mid и σ240, интервалы «стена включена»
//! из `--wall-log`). Для каждой реальной стены `w` ищется момент `τ'` из ДРУГИХ суток той же монеты: окна
//! `[τ',t'+H]` и `[τ,t+H]` не пересекаются, час `τ'+a_w` в пределах `±tod_window_s` от часа взвода `t`, тот же режим
//! BTC 4ч; псевдо-уровень стоит на `d_w·σ240(τ')` от `mid(τ')` той же стороны; в `[τ',t']` нет реальной стены на
//! уровне ±1 тик и нет стены той же стороны ближе `σ240·√(H/240)` от mid; mid не пересекал уровень; взвод `t'` — первая
//! секунда не раньше `τ'+a_w`, где mid подошёл к уровню на `arm_dist_bps` (не позже `max_wait_s`). Ковариаты пути
//! (`cov_fn`) считаются ПОСЛЕ поиска взвода — они зависят от уровня и `t'` (Судья, TK-353 п.R4.1 п.3). Из подходящих —
//! `k` ближайших по стандартизованным ковариатам (метрика Чебышёва, масштаб = SD по реальным стенам монеты) в пределах
//! `caliper_sd`; ничья — меньший `twin_key(sym, τ', seed)`; без возвращения (окно контроля не пересекает уже занятые).
//! Стены обходятся в порядке `twin_key(sym, id, seed)`. Числа-параметры — аргументы без умолчаний.

// Офлайн-постобработка суток (не горячий путь): индексы — внутри длин рядов, касты — секунды и тики в пределах i64/f64.
#![allow(
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_precision_loss
)]

use crate::book::Side;
use crate::lob::markout::{mid_double_tick, MidSample};
use crate::lob::sigma::SigmaSeries;
use crate::lob::twin::twin_key;

/// Окно σ в секундах (`sigma_bps(ts, 240)`); к горизонту `H` приводится множителем `√(H/240)`.
pub const SIGMA_WINDOW_S: f64 = 240.0;
const DAY_S: i64 = 86_400;
/// Число ковариат подбора (заполняет вызывающий: например, ln σ240, тихо-секунд, мин-расстояние в σ).
pub const NCOV: usize = 3;

/// Реальная стена: взвод `arm_ms`, возраст к взводу `age_ms` (τ = arm − age), `dist_sigma` — расстояние уровня от mid
/// в момент рождения в единицах σ240, `cov` — ковариаты в τ той же функцией, что и у кандидатов.
#[derive(Debug, Clone, Copy)]
pub struct WallRec {
    pub id: u64,
    pub arm_ms: i64,
    pub age_ms: i64,
    pub side: Side,
    pub arm_dist_bps: f64,
    pub dist_sigma: f64,
    pub regime: u8,
    pub cov: [f64; NCOV],
}

/// Интервал «реальная стена включена» (из переходов wall-log): тик, сторона, `[from_ms, to_ms)`.
#[derive(Debug, Clone, Copy)]
pub struct Span {
    pub side: Side,
    pub tick: i64,
    pub from_ms: i64,
    pub to_ms: i64,
}

/// Сутки монеты: `mid`/`sigma`/`regime` по секундам суток (`sigma` в bps, NaN — не определена; `regime` — режим BTC 4ч
/// на ту секунду, сравнивается в `τ'+a_w`, Судья TK-353 п.2), `spans` — стены суток.
pub struct DayCtx<'a> {
    pub day: i32,
    pub start_ms: i64,
    pub regime: &'a [u8],
    pub tick: f64,
    pub mid: &'a [f64],
    pub sigma: &'a [f64],
    pub spans: &'a [Span],
}

#[derive(Debug, Clone, Copy)]
pub struct Params {
    pub horizon_s: i64,
    pub tod_window_s: i64,
    pub caliper_sd: f64,
    pub k: usize,
    pub seed: u64,
    pub max_wait_s: i64,
    pub stride_s: usize,
    /// Пред-отсев кандидата по `ln σ` до прохода пути (точное сужение: выбранные пары те же, меняется лишь разбивка
    /// счётчиков отказов). `false` — прежний порядок проверок (для гейта «байт в байт»).
    pub prefilter: bool,
}

/// Причины отбраковки кандидата в порядке проверки (счётчики — в `Stats`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Reject {
    SameDay,
    Regime,
    NoSigma,
    Caliper,
    Crossed,
    NoArm,
    Overlap,
    Reused,
    WallInBand,
    NearerWall,
}
pub const N_REJECT: usize = 10;

#[derive(Debug, Clone, Copy)]
pub struct Control {
    pub wall: usize,
    pub day: i32,
    pub tau_ms: i64,
    pub arm_ms: i64,
    pub level_tick: i64,
    pub dist: f64,
}

#[derive(Debug, Clone, Default)]
pub struct Stats {
    pub rejects: [u64; N_REJECT],
    pub candidates: u64,
    pub unmatched: Vec<usize>,
}

/// SD ковариат по реальным стенам монеты (масштаб метрики); нулевая SD → ось не участвует.
pub fn pooled_sd(walls: &[WallRec]) -> [f64; NCOV] {
    let mut sd = [0.0; NCOV];
    let n = walls.len() as f64;
    if walls.len() < 2 {
        return sd;
    }
    for (j, s) in sd.iter_mut().enumerate() {
        let m = walls.iter().map(|w| w.cov[j]).sum::<f64>() / n;
        *s = (walls.iter().map(|w| (w.cov[j] - m).powi(2)).sum::<f64>() / (n - 1.0)).sqrt();
    }
    sd
}

/// Стандартизованная разность средних (SMD) двух выборок: `(mean_a − mean_b) / sqrt((var_a + var_b)/2)`.
pub fn smd(a: &[f64], b: &[f64]) -> f64 {
    if a.len() < 2 || b.len() < 2 {
        return f64::NAN;
    }
    let mean = |x: &[f64]| x.iter().sum::<f64>() / x.len() as f64;
    let var =
        |x: &[f64], m: f64| x.iter().map(|v| (v - m).powi(2)).sum::<f64>() / (x.len() as f64 - 1.0);
    let (ma, mb) = (mean(a), mean(b));
    let pooled = ((var(a, ma) + var(b, mb)) / 2.0).sqrt();
    if pooled == 0.0 {
        return 0.0;
    }
    (ma - mb) / pooled
}

/// «Нулевая синтетика» G1 §8: для каждой пары «стена/контроль» детерминированно (seed) — поменять ли роли местами.
pub fn swap_flags(n: usize, seed: u64) -> Vec<bool> {
    (0..n)
        .map(|i| twin_key("null-permute", i as i64, seed) & 1 == 1)
        .collect()
}

/// Посекундные ряды суток для `DayCtx`: `mid[s]` — середина (цена) как есть на границу секунды `start_ms + s·1000`
/// (последний срез не позже границы; NaN, пока срезов не было), `sigma[s]` — σ240 в bps на ту же границу (NaN —
/// не определена). `mids` отсортированы по времени (так их выдаёт реплей; можно с хвостом прошлых суток для σ).
pub fn second_series(
    mids: &[MidSample],
    sigma: &SigmaSeries,
    start_ms: i64,
    secs: usize,
    tick: f64,
) -> (Vec<f64>, Vec<f64>) {
    let mut mid = Vec::with_capacity(secs);
    let mut sig = Vec::with_capacity(secs);
    let mut i = 0usize;
    let mut cur = f64::NAN;
    for s in 0..secs {
        let ts = start_ms + s as i64 * 1000;
        while i < mids.len() && mids[i].ts_ms <= ts {
            cur = mid_double_tick(mids[i].bid_tick, mids[i].ask_tick) as f64 / 2.0 * tick;
            i += 1;
        }
        mid.push(cur);
        sig.push(
            sigma
                .sigma_bps(ts, SIGMA_WINDOW_S as i64)
                .unwrap_or(f64::NAN),
        );
    }
    (mid, sig)
}

/// σ-окно `window` секунд по посекундным mid (bps): `sqrt(Σ r²)`, `r_s = 10⁴·(m_s − m_{s−1})/m_{s−1}` по секундам
/// `(s − window, s]` — то же определение, что `lob::sigma`, но от готового посекундного ряда (`sync_basis::Market`).
/// `NaN` — окно выходит за начало суток или в нём есть секунда без mid.
pub fn sigma_from_second_mids(mid: &[f64], window: usize) -> Vec<f64> {
    let n = mid.len();
    let mut pref = vec![0.0f64; n + 1];
    let mut bad = vec![0u32; n + 1];
    for s in 1..n {
        let r = 1e4 * (mid[s] - mid[s - 1]) / mid[s - 1];
        let ok = r.is_finite();
        pref[s + 1] = pref[s] + if ok { r * r } else { 0.0 };
        bad[s + 1] = bad[s] + u32::from(!ok);
    }
    (0..n)
        .map(|s| {
            if s < window || bad[s + 1] != bad[s + 1 - window] {
                f64::NAN
            } else {
                (pref[s + 1] - pref[s + 1 - window]).sqrt()
            }
        })
        .collect()
}

/// Стены популяции (Судья TK-353, 22.10): интервал журнала остаётся, только если внутри него `[from, to]` есть взвод
/// события популяции на том же `(side, tick)`. Пол детектора 10k в полосе — почти любой уровень книги (условие пусто
/// по построению); «реальная стена» для `WallInBand`/`NearerWall` — стена по определению популяции (A_B, S\*).
/// `arms` — `(side, tick, arm_ms)` событий популяции (порядок любой).
#[must_use]
pub fn population_spans(spans: &[Span], arms: &[(Side, i64, i64)]) -> Vec<Span> {
    let mut by_key: Vec<(bool, i64, i64)> = arms
        .iter()
        .map(|&(side, tick, ms)| (side == Side::Ask, tick, ms))
        .collect();
    by_key.sort_unstable();
    spans
        .iter()
        .filter(|sp| {
            let ask = sp.side == Side::Ask;
            let i = by_key.partition_point(|&k| k < (ask, sp.tick, sp.from_ms));
            by_key
                .get(i)
                .is_some_and(|&(a, t, ms)| a == ask && t == sp.tick && ms <= sp.to_ms)
        })
        .copied()
        .collect()
}

fn overlaps(a0: i64, a1: i64, b0: i64, b1: i64) -> bool {
    a0 < b1 && b0 < a1
}

struct Cand {
    day: i32,
    tau_ms: i64,
    arm_ms: i64,
    level_tick: i64,
    dist: f64,
}

/// Вход ковариат кандидата: сутки, секунда рождения `s0`, секунда взвода `s_arm`, псевдо-уровень (цена) и сторона.
pub struct CovIn<'a, 'b> {
    pub day: &'a DayCtx<'b>,
    pub s0: usize,
    pub s_arm: usize,
    pub level: f64,
    pub side: Side,
    pub arm_dist_bps: f64,
}

fn eval<F: Fn(&CovIn) -> [f64; NCOV]>(
    w: &WallRec,
    d: &DayCtx,
    s0: usize,
    p: &Params,
    sd: &[f64; NCOV],
    cov_fn: &F,
    used: &[(i64, i64)],
) -> Result<Cand, Reject> {
    // `used` — непересекающиеся полуинтервалы, отсортированные по началу (концы тоже по возрастанию): пересечение
    // с [tau, end) может дать только последний интервал с началом < end.
    let reused = |tau: i64, end: i64| {
        let i = used.partition_point(|u| u.0 < end);
        i > 0 && used[i - 1].1 > tau
    };
    let sigma0 = d.sigma[s0];
    if !sigma0.is_finite() {
        return Err(Reject::NoSigma);
    }
    let age_s = (w.age_ms / 1000) as usize;
    let s_a = s0 + age_s;
    if d.regime.get(s_a).copied() != Some(w.regime) {
        return Err(Reject::Regime);
    }
    if p.prefilter && sd[0] > 0.0 && (sigma0.ln() - w.cov[0]).abs() / sd[0] > p.caliper_sd {
        return Err(Reject::Caliper);
    }
    let mid0 = d.mid[s0];
    let off = w.dist_sigma * sigma0 / 1e4;
    let level = match w.side {
        Side::Bid => mid0 * (1.0 - off),
        Side::Ask => mid0 * (1.0 + off),
    };
    // Положительное значение — mid по свою сторону уровня (у бида выше, у аска ниже).
    let gap_bps = |m: f64| match w.side {
        Side::Bid => (m - level) / level * 1e4,
        Side::Ask => (level - m) / level * 1e4,
    };
    let s_end = (s_a + p.max_wait_s as usize).min(d.mid.len().saturating_sub(1));
    let mut arm_s = None;
    for s in s0..=s_end {
        let g = gap_bps(d.mid[s]);
        if g <= 0.0 {
            return Err(Reject::Crossed);
        }
        if s >= s_a && g <= w.arm_dist_bps {
            arm_s = Some(s);
            break;
        }
    }
    let Some(arm_s) = arm_s else {
        return Err(Reject::NoArm);
    };
    let cov = cov_fn(&CovIn {
        day: d,
        s0,
        s_arm: arm_s,
        level,
        side: w.side,
        arm_dist_bps: w.arm_dist_bps,
    });
    let mut dist = 0.0f64;
    for j in 0..NCOV {
        if sd[j] > 0.0 {
            dist = dist.max((cov[j] - w.cov[j]).abs() / sd[j]);
        }
    }
    if !dist.is_finite() || dist > p.caliper_sd {
        return Err(Reject::Caliper);
    }
    let tau_ms = d.start_ms + s0 as i64 * 1000;
    let arm_ms = d.start_ms + arm_s as i64 * 1000;
    let end_ms = arm_ms + p.horizon_s * 1000;
    let tau_w = w.arm_ms - w.age_ms;
    if overlaps(tau_ms, end_ms, tau_w, w.arm_ms + p.horizon_s * 1000) {
        return Err(Reject::Overlap);
    }
    if reused(tau_ms, end_ms) {
        return Err(Reject::Reused);
    }
    let level_tick = (level / d.tick).round() as i64;
    let sigma_h = sigma0 * (p.horizon_s as f64 / SIGMA_WINDOW_S).sqrt();
    let mut nearer = false;
    for sp in d.spans {
        if !overlaps(sp.from_ms, sp.to_ms, tau_ms, arm_ms + 1) {
            continue;
        }
        if (sp.tick - level_tick).abs() <= 1 {
            return Err(Reject::WallInBand);
        }
        if sp.side == w.side && ((sp.tick as f64 * d.tick - mid0).abs() / mid0 * 1e4) < sigma_h {
            nearer = true;
        }
    }
    if nearer {
        return Err(Reject::NearerWall);
    }
    Ok(Cand {
        day: d.day,
        tau_ms,
        arm_ms,
        level_tick,
        dist,
    })
}

/// Подбор контролей C3 для всех стен монеты. `days` — обучающее окно (сутки с рядами), `cov_fn` — ковариаты в
/// взвод (`CovIn`; та же функция, что дала `WallRec.cov`; `cov[0]` = ln σ240 в рождении — на нём пред-отсев). Возвращает контроли (до `k` на стену) и счётчики.
pub fn select_controls<F: Fn(&CovIn) -> [f64; NCOV]>(
    sym: &str,
    walls: &[WallRec],
    days: &[DayCtx],
    p: &Params,
    cov_fn: F,
) -> (Vec<Control>, Stats) {
    let sd = pooled_sd(walls);
    let mut order: Vec<usize> = (0..walls.len()).collect();
    order.sort_by_key(|&i| (twin_key(sym, walls[i].id as i64, p.seed), i));
    let mut out = Vec::new();
    let mut used: Vec<(i64, i64)> = Vec::new();
    let mut st = Stats::default();
    for &wi in &order {
        let w = &walls[wi];
        let day_start_w = w.arm_ms.div_euclid(DAY_S * 1000) * DAY_S * 1000;
        let sod_arm = (w.arm_ms - day_start_w) / 1000;
        let age_s = w.age_ms / 1000;
        let mut best: Vec<(f64, u64, Cand)> = Vec::new();
        for d in days {
            if d.start_ms == day_start_w {
                st.rejects[Reject::SameDay as usize] += 1;
                continue;
            }
            let centre = sod_arm - age_s;
            let mut off = -p.tod_window_s;
            while off <= p.tod_window_s {
                let s0 = (centre + off).rem_euclid(DAY_S) as usize;
                off += p.stride_s as i64;
                if s0 >= d.mid.len() || s0 >= d.sigma.len() {
                    continue;
                }
                st.candidates += 1;
                match eval(w, d, s0, p, &sd, &cov_fn, &used) {
                    Ok(c) => {
                        let end = c.arm_ms + p.horizon_s * 1000;
                        // Контроли одной стены не пересекаются между собой: худший из пересекающихся уступает.
                        let clash = |b: &(f64, u64, Cand)| {
                            overlaps(c.tau_ms, end, b.2.tau_ms, b.2.arm_ms + p.horizon_s * 1000)
                        };
                        let key = twin_key(sym, c.tau_ms, p.seed);
                        if best.iter().any(|b| clash(b) && (b.0, b.1) <= (c.dist, key)) {
                            continue;
                        }
                        best.retain(|b| !clash(b));
                        best.push((c.dist, key, c));
                        best.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)));
                        best.truncate(p.k);
                    }
                    Err(r) => st.rejects[r as usize] += 1,
                }
            }
        }
        if best.is_empty() {
            st.unmatched.push(wi);
        }
        for (_, _, c) in best {
            let iv = (c.tau_ms, c.arm_ms + p.horizon_s * 1000);
            used.insert(used.partition_point(|u| u.0 < iv.0), iv);
            out.push(Control {
                wall: wi,
                day: c.day,
                tau_ms: c.tau_ms,
                arm_ms: c.arm_ms,
                level_tick: c.level_tick,
                dist: c.dist,
            });
        }
    }
    (out, st)
}

#[cfg(test)]
mod tests;
