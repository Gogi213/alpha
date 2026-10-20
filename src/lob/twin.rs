//! Г-03 (П-15 §5, вариант Б, разбор Судьи `docs/research/reviews/tk274-twin-2026-10-16.md`): двойник стены для
//! контроля «случайный уровень».
//!
//! Чистая арифметика на кадре взвода без ввода-вывода. Двойник — тик той же стороны, у которого **целое bps** до чужой
//! лучшей цены то же, что у стены (`arm_dist_bps`, тот же расчёт `gap_bps`), кроме тика стены; тик строго хуже своей
//! лучшей цены (как сама стена в момент взвода) и без плотности в кадре (`is_dense` — тот же порог рождения пула,
//! что у `lob::levels`, не новое число). Выбор из корзины — `hash(symbol, arm_ms, seed)`. Корзина пуста (тик монеты
//! крупнее 1 bps или вся корзина плотная) — `None`, в отчёт «не найдено». Корзина — отрезок подряд идущих тиков:
//! `gap_bps` монотонен при удалении от чужой цены. Ноль аллокаций: два прохода по отрезку.

use crate::book::Side;
use crate::lob::levels::gap_bps;

/// Найденный двойник: тик и размер корзины (для отчёта: расстояние до стены, доля впереди/позади).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Twin {
    pub tick: i64,
    pub candidates: u32,
}

/// Ключ выбора `hash(symbol, arm_ms, seed)`: FNV-1a по имени, затем перемешивание splitmix64 — детерминированно,
/// без зависимости от платформы и порядка запуска.
pub fn twin_key(symbol: &str, arm_ms: i64, seed: u64) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in symbol.bytes() {
        h = (h ^ u64::from(b)).wrapping_mul(0x0000_0100_0000_01b3);
    }
    let mut z = h ^ (arm_ms as u64).rotate_left(21) ^ seed.wrapping_mul(0x9e37_79b9_7f4a_7c15);
    z = (z ^ (z >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    z ^ (z >> 31)
}

/// Двойник стены `wall_tick` на кадре взвода. `best_own_tick` — лучшая цена своей стороны, `best_opp_tick` — чужой;
/// `is_dense(тик)` — есть ли в кадре плотность на тике.
pub fn pick_twin(
    side: Side,
    wall_tick: i64,
    best_own_tick: i64,
    best_opp_tick: i64,
    key: u64,
    is_dense: impl Fn(i64) -> bool,
) -> Option<Twin> {
    let gap_of = |p: i64| match side {
        Side::Bid => best_opp_tick - p,
        Side::Ask => p - best_opp_tick,
    };
    // Тик за лучшей ценой своей стороны (у бида ниже её, у аска выше) и перед чужой ценой.
    let behind_best = |p: i64| match side {
        Side::Bid => p < best_own_tick,
        Side::Ask => p > best_own_tick,
    };
    let d = gap_bps(gap_of(wall_tick), wall_tick);
    let in_group = |p: i64| p > 0 && gap_of(p) >= 1 && behind_best(p) && gap_bps(gap_of(p), p) == d;
    if !in_group(wall_tick) {
        return None;
    }
    let mut lo = wall_tick;
    while in_group(lo - 1) {
        lo -= 1;
    }
    let mut hi = wall_tick;
    while in_group(hi + 1) {
        hi += 1;
    }
    let ok = |p: i64| p != wall_tick && !is_dense(p);
    let n = (lo..=hi).filter(|&p| ok(p)).count() as u32;
    if n == 0 {
        return None;
    }
    let pick = (key % u64::from(n)) as usize;
    let tick = (lo..=hi).filter(|&p| ok(p)).nth(pick)?;
    Some(Twin {
        tick,
        candidates: n,
    })
}

#[cfg(test)]
mod tests;
