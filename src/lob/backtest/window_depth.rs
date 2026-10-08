//! Книга окон сетапов (К3, T-23): та же L2-семантика, что у
//! `HashMapMarketDepth::update_{bid,ask}_depth` крейта, на отсортированных массивах целых тиков.
//!
//! Зачем своя: `SignalWindows::build` проводит книгу через все события суток, а снимок берёт
//! на каждом `t0`. У крейта это `HashMap` (SipHash на каждое событие), линейный обход тиков
//! при поиске новой лучшей цены и снимок «скопировать обе карты и отсортировать» на окно;
//! здесь — двоичный поиск и снимок копией уже отсортированных массивов. Замер и порядок —
//! `docs/findings/backtest-optimization-2026-09-26.md`, раздел T-23.
//!
//! Движок окна и стратегия остаются на книге крейта (условие Судьи b86eed6): отсюда выходит
//! только `DepthSnapshot`, из которого окно собирает `HashMapMarketDepth` как прежде.
//! Снимок обязан быть **тем же** по всем полям, поэтому повторено всё, чем крейт отличается
//! от «чистой» книги (`book::Book`):
//! - тик и лот — `(x / size).round() as i64` в `f64`, как у крейта; хранится исходный `qty`
//!   (не лоты), уровень живёт при `qty_lot > 0`;
//! - пересечение не отбрасывается: уровень, оказавшийся по ту сторону чужой лучшей цены,
//!   остаётся в карте «спрятанным», лучшая цена чужой стороны ищется за ним;
//! - поиск новой лучшей цены ограничен `low_bid_tick`/`high_ask_tick` — нижней/верхней
//!   границей, которую крейт только расширяет и сбрасывает, лишь когда сторона опустела
//!   (спрятанные уровни за сброшенной границей поиску не видны — как у крейта);
//! - `timestamp` книги `update_*_depth` не трогает — в снимке он 0.
//!
//! Целые тики здесь — те же, что крейт получает округлением; B4 (цены в стратегии целыми)
//! переведёт на тики и событие, тогда округление уйдёт из обеих книг сразу.

use hftbacktest::depth::{INVALID_MAX, INVALID_MIN};

use super::DepthSnapshot;

/// Книга окон: обе стороны отсортированы по возрастанию тика (у бидов лучшая — в конце, у
/// асков — в начале); значения — исходные `qty` событий, все `> 0`.
#[derive(Debug, Clone)]
pub struct WindowDepth {
    tick: Quant,
    lot: Quant,
    bids: Side,
    asks: Side,
    best_bid_tick: i64,
    best_ask_tick: i64,
    low_bid_tick: i64,
    high_ask_tick: i64,
}

/// `x.round()` (к ближайшему, половина от нуля) без вызова libm: сборка без SSE4.1 вызывала `trunc` функцией
/// (8 % ЦП, TK-048). `a + 2^52 - 2^52` — ближайшее целое с чётной половиной, точное; ничью, ушедшую вниз,
/// поднимаем (`a - r == 0.5`); от 2^52 число уже целое. Побитно то же, что `f64::round`.
#[inline(always)]
pub(crate) fn round_half_away(x: f64) -> f64 {
    const MAGIC: f64 = 4503599627370496.0;
    let a = x.abs();
    if a >= MAGIC {
        return x;
    }
    let mut r = (a + MAGIC) - MAGIC;
    if a - r >= 0.5 {
        r += 1.0;
    }
    r.copysign(x)
}

/// Самый большой `|px/шаг|`, при котором невязка умножения на обратный шаг заведомо мала: относительная
/// ошибка `px·(1/шаг)` против `px/шаг` ≤ 3·2^-53, при `|m| < 2^40` это ≤ 4·10^-4 ≪ 0,25.
const QUANT_LIM: f64 = 1_099_511_627_776.0;

/// Счёт вызовов `Quant::of` и откатов на деление (суммируется при `Drop` книги; печать — `ALPHA_TICK_STATS=1`).
pub static QUANT_CALLS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
pub static QUANT_SLOW: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// Перевод цены/объёма в целое число тиков/лотов без деления (Э-17, TK-048). Результат тот же, что у
/// `round_half_away(x / step) as i64`: `m = x · fl(1/step)`, `c` — ближайшее целое к `m`; если `|m − c| < 0,25` и
/// `|m| < 2^40`, то `|x/step − c| < 0,5`, значит прежнее округление даёт тот же `c`. Иначе (нецелое, NaN, inf,
/// огромное, `1/step` не нормальное) — прежний путь с делением.
#[derive(Debug)]
pub struct Quant {
    step: f64,
    inv: f64,
    calls: std::cell::Cell<u64>,
    slow: std::cell::Cell<u64>,
}

impl Clone for Quant {
    fn clone(&self) -> Self {
        Self::new(self.step)
    }
}

impl Drop for Quant {
    fn drop(&mut self) {
        QUANT_CALLS.fetch_add(self.calls.get(), std::sync::atomic::Ordering::Relaxed);
        QUANT_SLOW.fetch_add(self.slow.get(), std::sync::atomic::Ordering::Relaxed);
    }
}

#[allow(clippy::cast_possible_truncation, clippy::cast_precision_loss)]
impl Quant {
    pub fn new(step: f64) -> Self {
        let inv = 1.0 / step;
        Self {
            step,
            inv: if inv.is_normal() { inv } else { f64::NAN },
            calls: std::cell::Cell::new(0),
            slow: std::cell::Cell::new(0),
        }
    }

    #[inline(always)]
    pub fn of(&self, x: f64) -> i64 {
        self.calls.set(self.calls.get() + 1);
        let m = x * self.inv;
        if m.abs() < QUANT_LIM {
            let c = (m + 0.5f64.copysign(m)) as i64;
            if (m - c as f64).abs() < 0.25 {
                return c;
            }
        }
        self.of_slow(x)
    }

    #[cold]
    #[inline(never)]
    fn of_slow(&self, x: f64) -> i64 {
        self.slow.set(self.slow.get() + 1);
        round_half_away(x / self.step) as i64
    }
}

/// Наибольший тик в `[end, start)` или `INVALID_MIN` — `depth_below` крейта: он обходит
/// тики от `start - 1` вниз до `end` и берёт первый с объёмом `> 0`, а в карте других нет.
fn depth_below(levels: &[(i64, f64)], start: i64, end: i64) -> i64 {
    let i = levels.partition_point(|l| l.0 < start);
    match i.checked_sub(1).and_then(|j| levels.get(j)) {
        Some(&(t, _)) if t >= end => t,
        _ => INVALID_MIN,
    }
}

/// Наименьший тик в `(start, end]` или `INVALID_MAX` — `depth_above` крейта.
fn depth_above(levels: &[(i64, f64)], start: i64, end: i64) -> i64 {
    let i = levels.partition_point(|l| l.0 <= start);
    match levels.get(i) {
        Some(&(t, _)) if t <= end => t,
        _ => INVALID_MAX,
    }
}

/// Ставит `qty` на тик (или снимает уровень при `qty_lot == 0`) — ветки `Entry` крейта.
#[allow(clippy::indexing_slicing)] // индексы доказаны `binary_search_by_key`
fn set_level(levels: &mut Vec<(i64, f64)>, tick: i64, qty: f64, qty_lot: i64) {
    match levels.binary_search_by_key(&tick, |l| l.0) {
        Ok(i) => {
            if qty_lot > 0 {
                levels[i].1 = qty;
            } else {
                levels.remove(i);
            }
        }
        Err(i) => {
            if qty_lot > 0 {
                levels.insert(i, (tick, qty));
            }
        }
    }
}

/// Самый широкий диапазон тиков, который держит прямая адресация; шире — сортированный массив.
const DENSE_SPAN_MAX: i128 = 1 << 20;

/// Прямая адресация: `qty[тик - base]` и битовая маска занятых уровней (`base` кратно 64).
#[derive(Debug, Clone)]
struct Dense {
    base: i64,
    qty: Vec<f64>,
    mask: Vec<u64>,
}

impl Dense {
    fn to_vec(&self) -> Vec<(i64, f64)> {
        let n: u32 = self.mask.iter().map(|w| w.count_ones()).sum();
        let mut out = Vec::with_capacity(n as usize);
        for (w, &word) in self.mask.iter().enumerate() {
            let mut m = word;
            while m != 0 {
                let idx = w * 64 + m.trailing_zeros() as usize;
                m &= m - 1;
                if let Some(&q) = self.qty.get(idx) {
                    out.push((self.base + idx as i64, q));
                }
            }
        }
        out
    }

    /// Расширяет диапазон до `tick`; `false`, если он вышел бы за `DENSE_SPAN_MAX`.
    fn cover(&mut self, tick: i64) -> bool {
        let len = self.qty.len() as i128;
        let (t, base) = (i128::from(tick), i128::from(self.base));
        if len == 0 {
            let Ok(nb) = i64::try_from(t.div_euclid(64) * 64) else {
                return false;
            };
            self.base = nb;
            self.qty.resize(64, 0.0);
            self.mask.resize(1, 0);
            return true;
        }
        if t >= base && t < base + len {
            return true;
        }
        if t >= base {
            let new_len = ((t - base) / 64 + 1) * 64;
            let new_len = new_len.max(len + len / 2 / 64 * 64);
            if new_len > DENSE_SPAN_MAX {
                return false;
            }
            self.qty.resize(new_len as usize, 0.0);
            self.mask.resize(new_len as usize / 64, 0);
            return true;
        }
        let need = (base - t + 63) / 64 * 64;
        let add = need.max(len / 2 / 64 * 64);
        if len + add > DENSE_SPAN_MAX {
            return false;
        }
        let Ok(nb) = i64::try_from(base - add) else {
            return false;
        };
        let add = add as usize;
        let mut qty = vec![0.0; add];
        qty.extend_from_slice(&self.qty);
        let mut mask = vec![0u64; add / 64];
        mask.extend_from_slice(&self.mask);
        self.base = nb;
        self.qty = qty;
        self.mask = mask;
        true
    }

    #[allow(clippy::indexing_slicing)] // индекс внутри `qty`/`mask` по `cover`/проверке диапазона
    fn set(&mut self, tick: i64, qty: f64, live: bool) -> bool {
        if live {
            if !self.cover(tick) {
                return false;
            }
            let idx = (i128::from(tick) - i128::from(self.base)) as usize;
            self.qty[idx] = qty;
            self.mask[idx / 64] |= 1 << (idx % 64);
        } else {
            let off = i128::from(tick) - i128::from(self.base);
            if off >= 0 && off < self.qty.len() as i128 {
                let idx = off as usize;
                self.mask[idx / 64] &= !(1u64 << (idx % 64));
            }
        }
        true
    }

    /// Как `depth_below`: наибольший занятый тик в `[end, start)`.
    #[allow(clippy::indexing_slicing)] // `w` ограничен длиной маски
    fn below(&self, start: i64, end: i64) -> i64 {
        let top = i128::from(start) - 1 - i128::from(self.base);
        if top < 0 || self.qty.is_empty() {
            return INVALID_MIN;
        }
        let top = top.min(self.qty.len() as i128 - 1) as usize;
        let mut w = top / 64;
        let mut m = self.mask[w] & (u64::MAX >> (63 - top % 64));
        loop {
            if m != 0 {
                let t = i128::from(self.base) + (w * 64 + 63 - m.leading_zeros() as usize) as i128;
                return if t >= i128::from(end) {
                    t as i64
                } else {
                    INVALID_MIN
                };
            }
            if w == 0 {
                return INVALID_MIN;
            }
            w -= 1;
            m = self.mask[w];
        }
    }

    /// Как `depth_above`: наименьший занятый тик в `(start, end]`.
    #[allow(clippy::indexing_slicing)] // `w` ограничен длиной маски
    fn above(&self, start: i64, end: i64) -> i64 {
        let from = i128::from(start) + 1 - i128::from(self.base);
        let len = self.qty.len() as i128;
        if len == 0 || from >= len {
            return INVALID_MAX;
        }
        let from = from.max(0) as usize;
        let mut w = from / 64;
        let mut m = self.mask[w] & (u64::MAX << (from % 64));
        loop {
            if m != 0 {
                let t = i128::from(self.base) + (w * 64 + m.trailing_zeros() as usize) as i128;
                return if t <= i128::from(end) {
                    t as i64
                } else {
                    INVALID_MAX
                };
            }
            w += 1;
            if w >= self.mask.len() {
                return INVALID_MAX;
            }
            m = self.mask[w];
        }
    }
}

/// Одна сторона книги: прямая адресация, а при диапазоне шире `DENSE_SPAN_MAX` — сортированный массив.
#[derive(Debug, Clone)]
enum Side {
    Dense(Dense),
    Sorted(Vec<(i64, f64)>),
}

impl Side {
    fn new() -> Self {
        Side::Dense(Dense {
            base: 0,
            qty: Vec::new(),
            mask: Vec::new(),
        })
    }

    fn set(&mut self, tick: i64, qty: f64, qty_lot: i64) {
        match self {
            Side::Dense(d) => {
                if !d.set(tick, qty, qty_lot > 0) {
                    let mut v = d.to_vec();
                    set_level(&mut v, tick, qty, qty_lot);
                    *self = Side::Sorted(v);
                }
            }
            Side::Sorted(v) => set_level(v, tick, qty, qty_lot),
        }
    }

    fn below(&self, start: i64, end: i64) -> i64 {
        match self {
            Side::Dense(d) => d.below(start, end),
            Side::Sorted(v) => depth_below(v, start, end),
        }
    }

    fn above(&self, start: i64, end: i64) -> i64 {
        match self {
            Side::Dense(d) => d.above(start, end),
            Side::Sorted(v) => depth_above(v, start, end),
        }
    }

    fn to_vec(&self) -> Vec<(i64, f64)> {
        match self {
            Side::Dense(d) => d.to_vec(),
            Side::Sorted(v) => v.clone(),
        }
    }
}

impl WindowDepth {
    pub fn new(tick_size: f64, lot_size: f64) -> Self {
        Self {
            tick: Quant::new(tick_size),
            lot: Quant::new(lot_size),
            bids: Side::new(),
            asks: Side::new(),
            best_bid_tick: INVALID_MIN,
            best_ask_tick: INVALID_MAX,
            low_bid_tick: INVALID_MAX,
            high_ask_tick: INVALID_MIN,
        }
    }

    /// `HashMapMarketDepth::update_bid_depth` крейта, строка в строку по смыслу.
    pub fn update_bid_depth(&mut self, price: f64, qty: f64) {
        let price_tick = self.tick.of(price);
        let qty_lot = self.lot.of(qty);
        self.bids.set(price_tick, qty, qty_lot);
        if qty_lot == 0 {
            if price_tick == self.best_bid_tick {
                self.best_bid_tick = self.bids.below(self.best_bid_tick, self.low_bid_tick);
                if self.best_bid_tick == INVALID_MIN {
                    self.low_bid_tick = INVALID_MAX;
                }
            }
        } else {
            if price_tick > self.best_bid_tick {
                self.best_bid_tick = price_tick;
                if self.best_bid_tick >= self.best_ask_tick {
                    self.best_ask_tick = self.asks.above(self.best_bid_tick, self.high_ask_tick);
                }
            }
            self.low_bid_tick = self.low_bid_tick.min(price_tick);
        }
    }

    /// `HashMapMarketDepth::update_ask_depth` крейта, строка в строку по смыслу.
    pub fn update_ask_depth(&mut self, price: f64, qty: f64) {
        let price_tick = self.tick.of(price);
        let qty_lot = self.lot.of(qty);
        self.asks.set(price_tick, qty, qty_lot);
        if qty_lot == 0 {
            if price_tick == self.best_ask_tick {
                self.best_ask_tick = self.asks.above(self.best_ask_tick, self.high_ask_tick);
                if self.best_ask_tick == INVALID_MAX {
                    self.high_ask_tick = INVALID_MIN;
                }
            }
        } else {
            if price_tick < self.best_ask_tick {
                self.best_ask_tick = price_tick;
                if self.best_bid_tick >= self.best_ask_tick {
                    self.best_bid_tick = self.bids.below(self.best_ask_tick, self.low_bid_tick);
                }
            }
            self.high_ask_tick = self.high_ask_tick.max(price_tick);
        }
    }

    /// Лучшие тики (бид, аск) сейчас — `INVALID_MIN`/`INVALID_MAX` у пустой стороны.
    pub fn best_ticks(&self) -> (i64, i64) {
        (self.best_bid_tick, self.best_ask_tick)
    }

    /// Снимок — копия уже отсортированных сторон, без сортировки (`DepthSnapshot::of` для книги
    /// крейта сортирует обе карты на каждое окно).
    pub fn snapshot(&self) -> DepthSnapshot {
        DepthSnapshot {
            bids: self.bids.to_vec(),
            asks: self.asks.to_vec(),
            best_bid_tick: self.best_bid_tick,
            best_ask_tick: self.best_ask_tick,
            low_bid_tick: self.low_bid_tick,
            high_ask_tick: self.high_ask_tick,
            timestamp: 0,
        }
    }
}

#[cfg(test)]
mod tests;
