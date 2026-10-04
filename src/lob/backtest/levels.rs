//! Одна сторона книги движка: плоский массив объёмов по тикам вместо хеш-отображения (TK-049).
//! Уровень есть, пока объём `> 0` (уровень с нулевым лотом в книгу не попадает — см. `fast_depth`), поэтому
//! отдельная маска занятости не нужна. Диапазон тиков шире `DENSE_SPAN_MAX` — прежнее хеш-отображение.
//! Все операции дают те же значения, что `TickMap`: порядок обхода нигде не используется.

use std::fmt;

use hftbacktest::depth::{INVALID_MAX, INVALID_MIN};

use super::fast_depth::TickMap;

/// Самый широкий диапазон тиков в плоском массиве (2^18 · 8 Б = 2 МБ на сторону).
const DENSE_SPAN_MAX: i64 = 1 << 18;

enum Repr {
    Dense { base: i64, qty: Vec<f64> },
    Map(TickMap),
}

pub struct Levels(Repr);

impl Default for Levels {
    fn default() -> Self {
        Self(Repr::Dense {
            base: 0,
            qty: Vec::new(),
        })
    }
}

impl Levels {
    /// Объём на тике; 0, если уровня нет.
    #[inline(always)]
    pub fn get(&self, tick: i64) -> f64 {
        match &self.0 {
            Repr::Dense { base, qty } => {
                let off = tick.wrapping_sub(*base) as u64;
                qty.get(usize::try_from(off).unwrap_or(usize::MAX))
                    .copied()
                    .unwrap_or(0.0)
            }
            Repr::Map(m) => m.get(&tick).copied().unwrap_or(0.0),
        }
    }

    /// Ставит объём (`qty > 0`) или снимает уровень (`qty == 0`); возвращает прежний объём (0, если не было).
    #[inline]
    pub fn replace(&mut self, tick: i64, qty: f64) -> f64 {
        if let Repr::Dense { base, qty: v } = &mut self.0 {
            let off = tick.wrapping_sub(*base) as u64;
            if let Some(slot) = v.get_mut(usize::try_from(off).unwrap_or(usize::MAX)) {
                return std::mem::replace(slot, qty);
            }
            if qty <= 0.0 {
                return 0.0;
            }
            if self.cover(tick) {
                return self.replace(tick, qty);
            }
        }
        self.replace_map(tick, qty)
    }

    fn replace_map(&mut self, tick: i64, qty: f64) -> f64 {
        if let Repr::Dense { .. } = self.0 {
            let mut m = TickMap::default();
            m.extend(self.iter());
            self.0 = Repr::Map(m);
        }
        let Repr::Map(m) = &mut self.0 else {
            unreachable!()
        };
        if qty > 0.0 {
            m.insert(tick, qty).unwrap_or(0.0)
        } else {
            m.remove(&tick).unwrap_or(0.0)
        }
    }

    /// Расширяет массив до `tick`; `false` — диапазон вышел бы за `DENSE_SPAN_MAX`.
    fn cover(&mut self, tick: i64) -> bool {
        let Repr::Dense { base, qty } = &mut self.0 else {
            return false;
        };
        let len = qty.len() as i64;
        if len == 0 {
            let Some(b) = tick.checked_sub(32) else {
                return false;
            };
            qty.resize(64, 0.0);
            *base = b;
            return true;
        }
        if tick >= *base {
            let need = tick - *base + 1;
            if need > DENSE_SPAN_MAX {
                return false;
            }
            let new_len = need.max(len + len / 2).min(DENSE_SPAN_MAX);
            qty.resize(new_len as usize, 0.0);
        } else {
            let need = *base - tick + len;
            if need > DENSE_SPAN_MAX {
                return false;
            }
            let add = (*base - tick).max(len / 2).min(DENSE_SPAN_MAX - len);
            let mut grown = vec![0.0; add as usize];
            grown.extend_from_slice(qty);
            *qty = grown;
            *base -= add;
        }
        true
    }

    /// Наибольший занятый тик в `[end, start)`, как `depth_below` книги крейта; `INVALID_MIN`, если такого нет.
    #[inline(always)]
    pub fn below(&self, start: i64, end: i64) -> i64 {
        match &self.0 {
            Repr::Dense { base, qty } => {
                let hi = start.min(base.saturating_add(qty.len() as i64));
                let lo = end.max(*base);
                let mut t = hi;
                while t > lo {
                    t -= 1;
                    if qty[(t - base) as usize] > 0.0 {
                        return t;
                    }
                }
                INVALID_MIN
            }
            Repr::Map(m) => {
                for t in (end..start).rev() {
                    if *m.get(&t).unwrap_or(&0f64) > 0f64 {
                        return t;
                    }
                }
                INVALID_MIN
            }
        }
    }

    /// Наименьший занятый тик в `(start, end]`, как `depth_above` книги крейта; `INVALID_MAX`, если такого нет.
    #[inline(always)]
    pub fn above(&self, start: i64, end: i64) -> i64 {
        match &self.0 {
            Repr::Dense { base, qty } => {
                let top = base.saturating_add(qty.len() as i64);
                let mut t = start.saturating_add(1).max(*base);
                let stop = end.saturating_add(1).min(top);
                while t < stop {
                    if qty[(t - base) as usize] > 0.0 {
                        return t;
                    }
                    t += 1;
                }
                INVALID_MAX
            }
            Repr::Map(m) => {
                for t in (start + 1)..(end + 1) {
                    if *m.get(&t).unwrap_or(&0f64) > 0f64 {
                        return t;
                    }
                }
                INVALID_MAX
            }
        }
    }

    pub fn clear(&mut self) {
        match &mut self.0 {
            Repr::Dense { qty, .. } => qty.fill(0.0),
            Repr::Map(m) => m.clear(),
        }
    }

    /// Занятые уровни `(тик, объём)` в произвольном порядке.
    pub fn iter(&self) -> Box<dyn Iterator<Item = (i64, f64)> + '_> {
        match &self.0 {
            Repr::Dense { base, qty } => Box::new(
                qty.iter()
                    .enumerate()
                    .filter(|(_, q)| **q > 0.0)
                    .map(move |(i, q)| (base + i as i64, *q)),
            ),
            Repr::Map(m) => Box::new(m.iter().map(|(t, q)| (*t, *q))),
        }
    }

    pub fn extend(&mut self, levels: impl IntoIterator<Item = (i64, f64)>) {
        for (t, q) in levels {
            self.replace(t, q);
        }
    }
}

impl fmt::Debug for Levels {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let mut v: Vec<(i64, f64)> = self.iter().collect();
        v.sort_unstable_by_key(|(t, _)| *t);
        f.debug_list().entries(v).finish()
    }
}

impl PartialEq for Levels {
    fn eq(&self, other: &Self) -> bool {
        let sorted = |l: &Self| {
            let mut v: Vec<(i64, u64)> = l.iter().map(|(t, q)| (t, q.to_bits())).collect();
            v.sort_unstable();
            v
        };
        sorted(self) == sorted(other)
    }
}
