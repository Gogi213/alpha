//! Экскурсия середины после касания (замер для `a`/`b` геометрии В-62,
//! владелец 2026-09-18: «А — надо выяснить»).
//!
//! Для касания и горизонта `H` (дедлайн сетки, `DEADLINE_SECS`) считаются два
//! числа в bps от базы касания (`markout::touch_base` — середина как есть на
//! `start_ms`), по секундным срезам середины за `(t₀, t₀ + H]`:
//!
//! - **ход против** (`adverse`) — самый большой сдвиг середины **в сторону
//!   плотности** (для бида — вниз), ≥ 0; это то, что должен пережить стоп;
//! - **ход за** (`favour`) — самый большой сдвиг **в сторону отскока**, ≥ 0;
//!   это то, что мог взять тейк.
//!
//! Оба — по серединам «как есть на границу секунды», тем же рядом, что `σ_H`
//! (`lob::sigma`): деление `adverse / σ_H` и `favour / σ_H` даёт множители
//! `a` и `b` в тех же единицах, что у плана. Секундная сетка режет
//! внутрисекундные пики — консервативно для обоих чисел, зато замер на
//! 100 тыс. касаний × 7200 с делается за O(1) на пару (разреженная таблица
//! минимума/максимума), а не за O(H × срезов).
//!
//! Окно, выходящее за конец ряда, — `None` (нет данных), не «сколько успели».

use crate::book::Side;
use crate::lob::markout::{mid_double_tick, MidSample};

/// Секундный ряд середины с разреженными таблицами минимума и максимума.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SecondMids {
    /// Первая секунда ряда (unix, с) — как у `SigmaSeries`.
    first_sec: i64,
    /// Удвоенная середина на границу каждой секунды.
    mid2x: Vec<i64>,
    /// `min_table[k][i]` — минимум `mid2x[i .. i + 2^k]`; `max_table` — максимум.
    min_table: Vec<Vec<i64>>,
    max_table: Vec<Vec<i64>>,
}

/// Экскурсия касания на одном горизонте, bps от базы, оба числа ≥ 0.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Excursion {
    pub adverse_bps: f64,
    pub favour_bps: f64,
}

impl SecondMids {
    /// Строит ряд по срезам, отсортированным по времени (та же сетка секунд,
    /// что у `SigmaSeries::from_mids`: первая граница не раньше первого среза,
    /// последняя — не позже последнего).
    pub fn from_mids(mids: &[MidSample]) -> Self {
        let (Some(first), Some(last)) = (mids.first(), mids.last()) else {
            return Self::empty();
        };
        let first_sec =
            first.ts_ms.div_euclid(1_000) + i64::from(first.ts_ms.rem_euclid(1_000) != 0);
        let last_sec = last.ts_ms.div_euclid(1_000);
        if last_sec < first_sec {
            return Self::empty();
        }
        let n = usize::try_from(last_sec - first_sec + 1).unwrap_or(0);
        let mut mid2x = Vec::with_capacity(n);
        let mut i = 0usize;
        for sec in first_sec..=last_sec {
            let boundary_ms = sec.saturating_mul(1_000);
            while i + 1 < mids.len() && mids[i + 1].ts_ms <= boundary_ms {
                i += 1;
            }
            mid2x.push(mid_double_tick(mids[i].bid_tick, mids[i].ask_tick));
        }
        let (min_table, max_table) = sparse_tables(&mid2x);
        Self {
            first_sec,
            mid2x,
            min_table,
            max_table,
        }
    }

    fn empty() -> Self {
        Self {
            first_sec: 0,
            mid2x: Vec::new(),
            min_table: Vec::new(),
            max_table: Vec::new(),
        }
    }

    /// Число секунд в ряду.
    pub fn len_secs(&self) -> usize {
        self.mid2x.len()
    }

    /// Минимум и максимум середины по секундам `(t₀, t₀ + window_s]`, где
    /// `t₀ = floor(start_ms / 1000)`; `None`, если окно не покрыто рядом.
    pub fn min_max_after(&self, start_ms: i64, window_s: i64) -> Option<(i64, i64)> {
        if window_s <= 0 || self.mid2x.is_empty() {
            return None;
        }
        let t0 = start_ms.div_euclid(1_000);
        let from_sec = t0.checked_add(1)?;
        let to_sec = t0.checked_add(window_s)?;
        if from_sec < self.first_sec {
            return None;
        }
        let from = usize::try_from(from_sec - self.first_sec).ok()?;
        let to = usize::try_from(to_sec - self.first_sec).ok()?;
        if to >= self.mid2x.len() {
            return None;
        }
        Some(range_min_max(&self.min_table, &self.max_table, from, to))
    }

    /// Экскурсия касания стороны `side` с базой `base2x` (удвоенная середина
    /// на `start_ms`, `markout::touch_base`) за `window_s` после `start_ms`.
    /// Знак: для бида «против» — вниз, «за» — вверх; для аска наоборот. Оба
    /// числа обрезаны нулём снизу: середина, не ушедшая против, даёт ход
    /// против 0, а не отрицательный.
    #[allow(clippy::cast_precision_loss)]
    pub fn excursion(
        &self,
        side: Side,
        base2x: i64,
        start_ms: i64,
        window_s: i64,
    ) -> Option<Excursion> {
        if base2x <= 0 {
            return None;
        }
        let (lo, hi) = self.min_max_after(start_ms, window_s)?;
        let to_bps = |v: i64| (v - base2x) as f64 / base2x as f64 * 10_000.0;
        let (up, down) = (to_bps(hi).max(0.0), (-to_bps(lo)).max(0.0));
        Some(match side {
            Side::Bid => Excursion {
                adverse_bps: down,
                favour_bps: up,
            },
            Side::Ask => Excursion {
                adverse_bps: up,
                favour_bps: down,
            },
        })
    }
}

/// Разреженные таблицы минимума и максимума: `table[k][i]` покрывает
/// `[i, i + 2^k)`. Память `n × log₂ n` на таблицу — для 172 800 секунд двух
/// суток ≈ 25 МБ каждая, строится один раз на символ.
fn sparse_tables(v: &[i64]) -> (Vec<Vec<i64>>, Vec<Vec<i64>>) {
    if v.is_empty() {
        return (Vec::new(), Vec::new());
    }
    let mut mins = vec![v.to_vec()];
    let mut maxs = vec![v.to_vec()];
    let mut k = 1usize;
    while (1usize << k) <= v.len() {
        let half = 1usize << (k - 1);
        let width = 1usize << k;
        let prev_min = &mins[k - 1];
        let prev_max = &maxs[k - 1];
        let n = v.len() - width + 1;
        let mut lvl_min = Vec::with_capacity(n);
        let mut lvl_max = Vec::with_capacity(n);
        for i in 0..n {
            lvl_min.push(prev_min[i].min(prev_min[i + half]));
            lvl_max.push(prev_max[i].max(prev_max[i + half]));
        }
        mins.push(lvl_min);
        maxs.push(lvl_max);
        k += 1;
    }
    (mins, maxs)
}

/// Минимум и максимум на отрезке `[from, to]` (включительно) за O(1).
fn range_min_max(mins: &[Vec<i64>], maxs: &[Vec<i64>], from: usize, to: usize) -> (i64, i64) {
    debug_assert!(from <= to);
    let len = to - from + 1;
    let k = usize::BITS - 1 - len.leading_zeros();
    let k = k as usize;
    let j = to + 1 - (1usize << k);
    (mins[k][from].min(mins[k][j]), maxs[k][from].max(maxs[k][j]))
}

#[cfg(test)]
mod tests;
