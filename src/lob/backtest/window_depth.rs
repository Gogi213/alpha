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

/// Книга окон: биды отсортированы по возрастанию тика, аски — по убыванию, так что лучшая цена
/// каждой стороны — в конце массива (вставки и снятия у лучшей цены не сдвигают хвост);
/// значения — исходные `qty` событий, все `> 0`. Снимок отдаёт аски по возрастанию.
#[derive(Debug, Clone)]
pub struct WindowDepth {
    tick_size: f64,
    lot_size: f64,
    bids: Vec<(i64, f64)>,
    asks: Vec<(i64, f64)>,
    best_bid_tick: i64,
    best_ask_tick: i64,
    low_bid_tick: i64,
    high_ask_tick: i64,
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
/// `levels` — по убыванию тика: тики `> start` — префикс, наименьший из них — его последний.
fn depth_above(levels: &[(i64, f64)], start: i64, end: i64) -> i64 {
    let i = levels.partition_point(|l| l.0 > start);
    match i.checked_sub(1).and_then(|j| levels.get(j)) {
        Some(&(t, _)) if t <= end => t,
        _ => INVALID_MAX,
    }
}

/// Ставит `qty` на тик (или снимает уровень при `qty_lot == 0`) — ветки `Entry` крейта.
#[allow(clippy::indexing_slicing)] // индексы доказаны `binary_search_by_key`
fn set_level(levels: &mut Vec<(i64, f64)>, tick: i64, qty: f64, qty_lot: i64, descending: bool) {
    let found = if descending {
        levels.binary_search_by(|l| tick.cmp(&l.0))
    } else {
        levels.binary_search_by_key(&tick, |l| l.0)
    };
    match found {
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

impl WindowDepth {
    pub fn new(tick_size: f64, lot_size: f64) -> Self {
        Self {
            tick_size,
            lot_size,
            bids: Vec::new(),
            asks: Vec::new(),
            best_bid_tick: INVALID_MIN,
            best_ask_tick: INVALID_MAX,
            low_bid_tick: INVALID_MAX,
            high_ask_tick: INVALID_MIN,
        }
    }

    /// `HashMapMarketDepth::update_bid_depth` крейта, строка в строку по смыслу.
    pub fn update_bid_depth(&mut self, price: f64, qty: f64) {
        let price_tick = (price / self.tick_size).round() as i64;
        let qty_lot = (qty / self.lot_size).round() as i64;
        set_level(&mut self.bids, price_tick, qty, qty_lot, false);
        if qty_lot == 0 {
            if price_tick == self.best_bid_tick {
                self.best_bid_tick = depth_below(&self.bids, self.best_bid_tick, self.low_bid_tick);
                if self.best_bid_tick == INVALID_MIN {
                    self.low_bid_tick = INVALID_MAX;
                }
            }
        } else {
            if price_tick > self.best_bid_tick {
                self.best_bid_tick = price_tick;
                if self.best_bid_tick >= self.best_ask_tick {
                    self.best_ask_tick =
                        depth_above(&self.asks, self.best_bid_tick, self.high_ask_tick);
                }
            }
            self.low_bid_tick = self.low_bid_tick.min(price_tick);
        }
    }

    /// `HashMapMarketDepth::update_ask_depth` крейта, строка в строку по смыслу.
    pub fn update_ask_depth(&mut self, price: f64, qty: f64) {
        let price_tick = (price / self.tick_size).round() as i64;
        let qty_lot = (qty / self.lot_size).round() as i64;
        set_level(&mut self.asks, price_tick, qty, qty_lot, true);
        if qty_lot == 0 {
            if price_tick == self.best_ask_tick {
                self.best_ask_tick =
                    depth_above(&self.asks, self.best_ask_tick, self.high_ask_tick);
                if self.best_ask_tick == INVALID_MAX {
                    self.high_ask_tick = INVALID_MIN;
                }
            }
        } else {
            if price_tick < self.best_ask_tick {
                self.best_ask_tick = price_tick;
                if self.best_bid_tick >= self.best_ask_tick {
                    self.best_bid_tick =
                        depth_below(&self.bids, self.best_ask_tick, self.low_bid_tick);
                }
            }
            self.high_ask_tick = self.high_ask_tick.max(price_tick);
        }
    }

    /// Снимок — копия уже отсортированных сторон, без сортировки (`DepthSnapshot::of` для книги
    /// крейта сортирует обе карты на каждое окно).
    pub fn snapshot(&self) -> DepthSnapshot {
        DepthSnapshot {
            bids: self.bids.clone(),
            asks: self.asks.iter().rev().copied().collect(),
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
