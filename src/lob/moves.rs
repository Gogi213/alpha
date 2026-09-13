//! Переезды плотностей (T42, В-46): пары «смерть `pulled` → рождение» и их
//! распределения.
//!
//! **Метка `moved` здесь не вводится.** В-46 требует сначала измерить
//! совместное распределение (Δt, Δp, отношение размеров) и следствия пары, а
//! границы метки назначить по видимой структуре отдельным решением. Этот
//! модуль только измеряет: ни одного порога внутри нет, окно поиска приходит
//! параметром и печатается в шапке артефакта.
//!
//! Что такое пара: смерть с исходом `pulled` на (цена p₁, сторона `s`) и
//! **ближайшее по времени** рождение той же стороны в пределах окна. Цена при
//! этом не ограничивается: Δp — измеряемая величина, а не фильтр (ограничить
//! его значило бы вписать ответ в вопрос). «Спуф» и «переезд» здесь не
//! различаются формой пары — только следствием: дошла ли цена до нового
//! уровня, был ли он коснут, чем умер, куда ушла середина.

use crate::book::Side;
use crate::lob::levels::{classify_outcome, LevelRecord, Outcome, TouchRecord};
use crate::lob::markout::MidSample;

/// Одна пара «смерть → рождение».
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct MovePair {
    pub side: Side,
    /// Δt от смерти до рождения, мс (положительное).
    pub dt_ms: i64,
    /// Δp в тиках со знаком: цена рождения минус цена смерти.
    pub dp_ticks: i64,
    /// Размер нового уровня к старому (`size_max`); `None` при нулевом старом.
    pub size_ratio: Option<f64>,
    /// Нулей в конце цены у старого и нового уровня (круглость, В-44).
    pub zeros_from: u8,
    pub zeros_to: u8,
    /// Сдвиг середины от рождения до +10 с и +60 с, bps.
    pub mid_10s_bps: Option<f64>,
    pub mid_60s_bps: Option<f64>,
    /// Дошла ли цена до нового уровня (есть касание) и чем он умер.
    pub touched: bool,
    pub outcome_to: Option<Outcome>,
}

/// Пары по всем умершим `pulled`-уровням суток. `window_ms` — окно поиска
/// рождения (параметр вызывающего, не константа модуля).
pub fn find_pairs(
    records: &[LevelRecord],
    touches: &[TouchRecord],
    mids: &[MidSample],
    window_ms: i64,
) -> Vec<MovePair> {
    // Рождения, отсортированные по (сторона, время): «ближайшее рождение той
    // же стороны после смерти» — это двоичный поиск, а не перебор.
    let mut births: Vec<(Side, i64, usize)> = records
        .iter()
        .enumerate()
        .map(|(i, r)| (r.side, r.birth_ms, i))
        .collect();
    births.sort_by_key(|&(s, t, _)| (side_rank(s), t));

    // Касания по (сторона, цена) — для «дошла ли цена до нового уровня».
    let mut touch_at: std::collections::BTreeMap<(u8, i64), Vec<i64>> =
        std::collections::BTreeMap::new();
    for t in touches {
        touch_at
            .entry((side_rank(t.side), t.price_tick))
            .or_default()
            .push(t.start_ms);
    }
    for v in touch_at.values_mut() {
        v.sort_unstable();
    }

    let mut out = Vec::new();
    for dead in records {
        if classify_outcome(dead.traded_lots, dead.size_max) != Outcome::Pulled {
            continue;
        }
        let rank = side_rank(dead.side);
        let lo = births.partition_point(|&(s, t, _)| (side_rank(s), t) <= (rank, dead.death_ms));
        let Some(&(b_side, b_ms, b_idx)) = births.get(lo) else {
            continue;
        };
        if b_side != dead.side || b_ms - dead.death_ms > window_ms {
            continue;
        }
        let born = &records[b_idx];
        let touched = touch_at
            .get(&(rank, born.price_tick))
            .is_some_and(|starts| starts.iter().any(|&s| s >= born.birth_ms));
        out.push(MovePair {
            side: dead.side,
            dt_ms: b_ms - dead.death_ms,
            dp_ticks: born.price_tick - dead.price_tick,
            size_ratio: size_ratio(born.size_max, dead.size_max),
            zeros_from: trailing_zeroes(dead.price_tick),
            zeros_to: trailing_zeroes(born.price_tick),
            mid_10s_bps: mid_shift_bps(mids, b_ms, 10_000),
            mid_60s_bps: mid_shift_bps(mids, b_ms, 60_000),
            touched,
            outcome_to: Some(classify_outcome(born.traded_lots, born.size_max)),
        });
    }
    out.sort_by_key(|p| p.dt_ms);
    out
}

/// Квантили p10/p50/p90 линейной интерполяцией по порядковым статистикам
/// (тип 7 — та же конвенция, что у `numpy.percentile`): определение
/// измерения, а не порог. `None` на пустом входе.
pub fn quantiles(values: &[f64]) -> Option<(f64, f64, f64)> {
    if values.is_empty() {
        return None;
    }
    let mut v = values.to_vec();
    v.sort_by(f64::total_cmp);
    Some((
        quantile_sorted(&v, 0.10),
        quantile_sorted(&v, 0.50),
        quantile_sorted(&v, 0.90),
    ))
}

fn quantile_sorted(v: &[f64], q: f64) -> f64 {
    if v.len() == 1 {
        return v[0];
    }
    let pos = q * (v.len() - 1) as f64;
    let lo = pos.floor() as usize;
    let hi = pos.ceil() as usize;
    if lo == hi {
        v[lo]
    } else {
        v[lo] + (v[hi] - v[lo]) * (pos - lo as f64)
    }
}

/// Гистограмма от минимума с шагом `width` (шаг — параметр вызывающего):
/// `(левая граница корзины, сколько значений)`, пустые корзины опущены.
pub fn histogram(values: &[f64], width: f64) -> Vec<(f64, u64)> {
    if values.is_empty() || !width.is_finite() || width <= 0.0 {
        return Vec::new();
    }
    let min = values.iter().copied().fold(f64::INFINITY, f64::min);
    let base = (min / width).floor() * width;
    let mut bins: std::collections::BTreeMap<i64, u64> = std::collections::BTreeMap::new();
    for v in values {
        let k = ((v - base) / width).floor() as i64;
        *bins.entry(k).or_insert(0) += 1;
    }
    bins.into_iter()
        .map(|(k, n)| (base + k as f64 * width, n))
        .collect()
}

fn side_rank(s: Side) -> u8 {
    match s {
        Side::Bid => 0,
        Side::Ask => 1,
    }
}

fn size_ratio(new: i64, old: i64) -> Option<f64> {
    if old == 0 {
        None
    } else {
        Some(new as f64 / old as f64)
    }
}

/// Нулей в конце целого тика: у `12300` — два, у `12345` — ноль.
fn trailing_zeroes(tick: i64) -> u8 {
    let mut t = tick.abs();
    let mut n = 0u8;
    while t > 0 && t % 10 == 0 {
        n = n.saturating_add(1);
        t /= 10;
    }
    n
}

/// Сдвиг середины от метки `from_ms` до `from_ms + horizon_ms`, bps. `None`,
/// если среза на `from_ms` или на горизонте нет: отсутствие данных не ноль.
fn mid_shift_bps(mids: &[MidSample], from_ms: i64, horizon_ms: i64) -> Option<f64> {
    let base = mid_asof(mids, from_ms)?;
    let later = mid_asof(mids, from_ms.saturating_add(horizon_ms))?;
    if base <= 0.0 {
        return None;
    }
    Some((later - base) / base * 10_000.0)
}

/// Середина (в тиках) на последнем срезе с меткой `≤ ts_ms`.
fn mid_asof(mids: &[MidSample], ts_ms: i64) -> Option<f64> {
    let i = mids.partition_point(|s| s.ts_ms <= ts_ms);
    let s = mids.get(i.checked_sub(1)?)?;
    Some((s.bid_tick as f64 + s.ask_tick as f64) / 2.0)
}

/// Свод по корзинам Δt: сколько пар, медиана |Δp|, медиана отношения размеров,
/// доли нулевого Δp и коснутых. Второго набора границ не заводится — корзины
/// идут по тому же шагу `dt_bin_ms`, что гистограмма, и он приходит от
/// вызывающего (в коде модуля порогов нет).
///
/// Зачем: маргиналы вырождены первым бакетом Δt (все пары «в том же кадре»),
/// поэтому вопрос «есть ли кластер настоящих переездов» решается только
/// совместно — сравнением корзин Δt по остальным признакам.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct MoveBin {
    pub dt_from_ms: i64,
    pub n: u64,
    pub dp_abs_median: Option<f64>,
    pub ratio_median: Option<f64>,
    pub zero_dp_share: f64,
    pub touched_share: f64,
}

/// Свод пар по корзинам Δt шириной `dt_bin_ms` (неположительный шаг — пустой
/// результат: шаг задаёт вызывающий).
pub fn by_dt_bins(pairs: &[MovePair], dt_bin_ms: i64) -> Vec<MoveBin> {
    if pairs.is_empty() || dt_bin_ms <= 0 {
        return Vec::new();
    }
    let mut groups: std::collections::BTreeMap<i64, Vec<&MovePair>> =
        std::collections::BTreeMap::new();
    for p in pairs {
        groups
            .entry(p.dt_ms / dt_bin_ms * dt_bin_ms)
            .or_default()
            .push(p);
    }
    groups
        .into_iter()
        .map(|(from, ps)| {
            let n = ps.len() as f64;
            let dps: Vec<f64> = ps
                .iter()
                .map(|p| p.dp_ticks.unsigned_abs() as f64)
                .collect();
            let ratios: Vec<f64> = ps.iter().filter_map(|p| p.size_ratio).collect();
            MoveBin {
                dt_from_ms: from,
                n: ps.len() as u64,
                dp_abs_median: quantiles(&dps).map(|(_, m, _)| m),
                ratio_median: quantiles(&ratios).map(|(_, m, _)| m),
                zero_dp_share: ps.iter().filter(|p| p.dp_ticks == 0).count() as f64 / n,
                touched_share: ps.iter().filter(|p| p.touched).count() as f64 / n,
            }
        })
        .collect()
}

#[cfg(test)]
mod tests;
