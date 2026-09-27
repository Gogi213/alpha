//! Книга движка бэктеста с быстрым хешем (Э-05, T-38): та же `HashMapMarketDepth` крейта `hftbacktest 0.9.4`
//! (`depth/hashmapmarketdepth.rs`, MIT) — поля, поиск лучшей цены перебором тиков (`depth_below`/`depth_above`),
//! обновления и снимок строка в строку, — но отображение «тик → объём» хешируется умножением (Fx), а не SipHash:
//! профиль 03.08 (`docs/efficiency-register.md` Э-05) — `hash_one` + `sip::write` ≈ 15 % счёта сетки. Алгоритм от
//! порядка обхода отображения не зависит (снимок сортируется), поэтому итог байт в байт тот же, что у книги
//! крейта. L3 (заявки по номерам) движку сетки не нужен и не реализован.

use std::collections::hash_map::Entry;
use std::collections::HashMap;
use std::hash::{BuildHasherDefault, Hasher};

use hftbacktest::backtest::data::Data;
use hftbacktest::depth::{ApplySnapshot, L2MarketDepth, MarketDepth, INVALID_MAX, INVALID_MIN};
use hftbacktest::types::{
    Event, Side, BUY_EVENT, DEPTH_SNAPSHOT_EVENT, EXCH_EVENT, LOCAL_EVENT, SELL_EVENT,
};

/// Хеш тика умножением (FxHash rustc): ключ — одно целое, стойкость к подбору не нужна.
#[derive(Default, Clone, Copy)]
pub struct TickHasher(u64);

const FX_K: u64 = 0x51_7c_c1_b7_27_22_0a_95;

impl Hasher for TickHasher {
    #[inline]
    fn write(&mut self, bytes: &[u8]) {
        for &b in bytes {
            self.0 = (self.0.rotate_left(5) ^ u64::from(b)).wrapping_mul(FX_K);
        }
    }

    #[inline]
    #[allow(clippy::cast_sign_loss)]
    fn write_i64(&mut self, v: i64) {
        self.0 = (self.0.rotate_left(5) ^ v as u64).wrapping_mul(FX_K);
    }

    #[inline]
    fn finish(&self) -> u64 {
        self.0
    }
}

/// Отображение «тик → объём» книги.
pub type TickMap = HashMap<i64, f64, BuildHasherDefault<TickHasher>>;

/// Книга L2 движка — копия `HashMapMarketDepth` крейта с `TickMap` (см. модуль).
pub struct FastMarketDepth {
    pub tick_size: f64,
    pub lot_size: f64,
    pub timestamp: i64,
    pub ask_depth: TickMap,
    pub bid_depth: TickMap,
    pub best_bid_tick: i64,
    pub best_ask_tick: i64,
    pub low_bid_tick: i64,
    pub high_ask_tick: i64,
}

#[inline(always)]
fn depth_below(depth: &TickMap, start: i64, end: i64) -> i64 {
    for t in (end..start).rev() {
        if *depth.get(&t).unwrap_or(&0f64) > 0f64 {
            return t;
        }
    }
    INVALID_MIN
}

#[inline(always)]
fn depth_above(depth: &TickMap, start: i64, end: i64) -> i64 {
    for t in (start + 1)..(end + 1) {
        if *depth.get(&t).unwrap_or(&0f64) > 0f64 {
            return t;
        }
    }
    INVALID_MAX
}

impl FastMarketDepth {
    pub fn new(tick_size: f64, lot_size: f64) -> Self {
        Self {
            tick_size,
            lot_size,
            timestamp: 0,
            ask_depth: TickMap::default(),
            bid_depth: TickMap::default(),
            best_bid_tick: INVALID_MIN,
            best_ask_tick: INVALID_MAX,
            low_bid_tick: INVALID_MAX,
            high_ask_tick: INVALID_MIN,
        }
    }
}

// Числа и приведения — как в крейте, строка в строку (гейт «байт в байт»).
#[allow(clippy::cast_possible_truncation, clippy::cast_precision_loss)]
impl L2MarketDepth for FastMarketDepth {
    fn update_bid_depth(
        &mut self,
        price: f64,
        qty: f64,
        timestamp: i64,
    ) -> (i64, i64, i64, f64, f64, i64) {
        let price_tick = (price / self.tick_size).round() as i64;
        let qty_lot = (qty / self.lot_size).round() as i64;
        let prev_best_bid_tick = self.best_bid_tick;
        let prev_qty;
        match self.bid_depth.entry(price_tick) {
            Entry::Occupied(mut entry) => {
                prev_qty = *entry.get();
                if qty_lot > 0 {
                    *entry.get_mut() = qty;
                } else {
                    entry.remove();
                }
            }
            Entry::Vacant(entry) => {
                prev_qty = 0f64;
                if qty_lot > 0 {
                    entry.insert(qty);
                }
            }
        }

        if qty_lot == 0 {
            if price_tick == self.best_bid_tick {
                self.best_bid_tick =
                    depth_below(&self.bid_depth, self.best_bid_tick, self.low_bid_tick);
                if self.best_bid_tick == INVALID_MIN {
                    self.low_bid_tick = INVALID_MAX;
                }
            }
        } else {
            if price_tick > self.best_bid_tick {
                self.best_bid_tick = price_tick;
                if self.best_bid_tick >= self.best_ask_tick {
                    self.best_ask_tick =
                        depth_above(&self.ask_depth, self.best_bid_tick, self.high_ask_tick);
                }
            }
            self.low_bid_tick = self.low_bid_tick.min(price_tick);
        }
        (
            price_tick,
            prev_best_bid_tick,
            self.best_bid_tick,
            prev_qty,
            qty,
            timestamp,
        )
    }

    fn update_ask_depth(
        &mut self,
        price: f64,
        qty: f64,
        timestamp: i64,
    ) -> (i64, i64, i64, f64, f64, i64) {
        let price_tick = (price / self.tick_size).round() as i64;
        let qty_lot = (qty / self.lot_size).round() as i64;
        let prev_best_ask_tick = self.best_ask_tick;
        let prev_qty;
        match self.ask_depth.entry(price_tick) {
            Entry::Occupied(mut entry) => {
                prev_qty = *entry.get();
                if qty_lot > 0 {
                    *entry.get_mut() = qty;
                } else {
                    entry.remove();
                }
            }
            Entry::Vacant(entry) => {
                prev_qty = 0f64;
                if qty_lot > 0 {
                    entry.insert(qty);
                }
            }
        }

        if qty_lot == 0 {
            if price_tick == self.best_ask_tick {
                self.best_ask_tick =
                    depth_above(&self.ask_depth, self.best_ask_tick, self.high_ask_tick);
                if self.best_ask_tick == INVALID_MAX {
                    self.high_ask_tick = INVALID_MIN;
                }
            }
        } else {
            if price_tick < self.best_ask_tick {
                self.best_ask_tick = price_tick;
                if self.best_bid_tick >= self.best_ask_tick {
                    self.best_bid_tick =
                        depth_below(&self.bid_depth, self.best_ask_tick, self.low_bid_tick);
                }
            }
            self.high_ask_tick = self.high_ask_tick.max(price_tick);
        }
        (
            price_tick,
            prev_best_ask_tick,
            self.best_ask_tick,
            prev_qty,
            qty,
            timestamp,
        )
    }

    fn clear_depth(&mut self, side: Side, clear_upto_price: f64) {
        match side {
            Side::Buy => {
                if clear_upto_price.is_finite() {
                    let clear_upto = (clear_upto_price / self.tick_size).round() as i64;
                    if self.best_bid_tick != INVALID_MIN {
                        for t in clear_upto..(self.best_bid_tick + 1) {
                            if self.bid_depth.contains_key(&t) {
                                self.bid_depth.remove(&t);
                            }
                        }
                    }
                    self.best_bid_tick =
                        depth_below(&self.bid_depth, clear_upto - 1, self.low_bid_tick);
                } else {
                    self.bid_depth.clear();
                    self.best_bid_tick = INVALID_MIN;
                }
                if self.best_bid_tick == INVALID_MIN {
                    self.low_bid_tick = INVALID_MAX;
                }
            }
            Side::Sell => {
                if clear_upto_price.is_finite() {
                    let clear_upto = (clear_upto_price / self.tick_size).round() as i64;
                    if self.best_ask_tick != INVALID_MAX {
                        for t in self.best_ask_tick..(clear_upto + 1) {
                            if self.ask_depth.contains_key(&t) {
                                self.ask_depth.remove(&t);
                            }
                        }
                    }
                    self.best_ask_tick =
                        depth_above(&self.ask_depth, clear_upto + 1, self.high_ask_tick);
                } else {
                    self.ask_depth.clear();
                    self.best_ask_tick = INVALID_MAX;
                }
                if self.best_ask_tick == INVALID_MAX {
                    self.high_ask_tick = INVALID_MIN;
                }
            }
            Side::None => {
                self.bid_depth.clear();
                self.ask_depth.clear();
                self.best_bid_tick = INVALID_MIN;
                self.best_ask_tick = INVALID_MAX;
                self.low_bid_tick = INVALID_MAX;
                self.high_ask_tick = INVALID_MIN;
            }
            Side::Unsupported => {
                unreachable!();
            }
        }
    }
}

#[allow(clippy::cast_precision_loss)]
impl MarketDepth for FastMarketDepth {
    #[inline(always)]
    fn best_bid(&self) -> f64 {
        if self.best_bid_tick == INVALID_MIN {
            f64::NAN
        } else {
            self.best_bid_tick as f64 * self.tick_size
        }
    }

    #[inline(always)]
    fn best_ask(&self) -> f64 {
        if self.best_ask_tick == INVALID_MAX {
            f64::NAN
        } else {
            self.best_ask_tick as f64 * self.tick_size
        }
    }

    #[inline(always)]
    fn best_bid_tick(&self) -> i64 {
        self.best_bid_tick
    }

    #[inline(always)]
    fn best_ask_tick(&self) -> i64 {
        self.best_ask_tick
    }

    #[inline(always)]
    fn best_bid_qty(&self) -> f64 {
        *self.bid_depth.get(&self.best_bid_tick).unwrap_or(&0.0)
    }

    #[inline(always)]
    fn best_ask_qty(&self) -> f64 {
        *self.ask_depth.get(&self.best_ask_tick).unwrap_or(&0.0)
    }

    #[inline(always)]
    fn tick_size(&self) -> f64 {
        self.tick_size
    }

    #[inline(always)]
    fn lot_size(&self) -> f64 {
        self.lot_size
    }

    #[inline(always)]
    fn bid_qty_at_tick(&self, price_tick: i64) -> f64 {
        *self.bid_depth.get(&price_tick).unwrap_or(&0.0)
    }

    #[inline(always)]
    fn ask_qty_at_tick(&self, price_tick: i64) -> f64 {
        *self.ask_depth.get(&price_tick).unwrap_or(&0.0)
    }
}

#[allow(clippy::cast_possible_truncation, clippy::cast_precision_loss)]
impl ApplySnapshot for FastMarketDepth {
    fn apply_snapshot(&mut self, data: &Data<Event>) {
        self.best_bid_tick = INVALID_MIN;
        self.best_ask_tick = INVALID_MAX;
        self.low_bid_tick = INVALID_MAX;
        self.high_ask_tick = INVALID_MIN;
        self.bid_depth.clear();
        self.ask_depth.clear();
        for row_num in 0..data.len() {
            let price = data[row_num].px;
            let qty = data[row_num].qty;

            let price_tick = (price / self.tick_size).round() as i64;
            if data[row_num].ev & BUY_EVENT == BUY_EVENT {
                self.best_bid_tick = self.best_bid_tick.max(price_tick);
                self.low_bid_tick = self.low_bid_tick.min(price_tick);
                *self.bid_depth.entry(price_tick).or_insert(0f64) = qty;
            } else if data[row_num].ev & SELL_EVENT == SELL_EVENT {
                self.best_ask_tick = self.best_ask_tick.min(price_tick);
                self.high_ask_tick = self.high_ask_tick.max(price_tick);
                *self.ask_depth.entry(price_tick).or_insert(0f64) = qty;
            }
        }
    }

    fn snapshot(&self) -> Vec<Event> {
        let mut events = Vec::new();
        let mut bid_depth = self
            .bid_depth
            .iter()
            .filter(|&(&px_tick, _)| px_tick <= self.best_bid_tick)
            .map(|(&px_tick, &qty)| (px_tick, qty))
            .collect::<Vec<_>>();
        bid_depth.sort_by(|a, b| b.0.cmp(&a.0));
        for (px_tick, qty) in bid_depth {
            events.push(Event {
                ev: EXCH_EVENT | LOCAL_EVENT | BUY_EVENT | DEPTH_SNAPSHOT_EVENT,
                exch_ts: 0,
                local_ts: 0,
                px: px_tick as f64 * self.tick_size,
                qty,
                order_id: 0,
                ival: 0,
                fval: 0.0,
            });
        }
        let mut ask_depth = self
            .ask_depth
            .iter()
            .filter(|&(&px_tick, _)| px_tick >= self.best_ask_tick)
            .map(|(&px_tick, &qty)| (px_tick, qty))
            .collect::<Vec<_>>();
        ask_depth.sort_by(|a, b| a.0.cmp(&b.0));
        for (px_tick, qty) in ask_depth {
            events.push(Event {
                ev: EXCH_EVENT | LOCAL_EVENT | SELL_EVENT | DEPTH_SNAPSHOT_EVENT,
                exch_ts: 0,
                local_ts: 0,
                px: px_tick as f64 * self.tick_size,
                qty,
                order_id: 0,
                ival: 0,
                fval: 0.0,
            });
        }
        events
    }
}

#[cfg(test)]
mod tests;
