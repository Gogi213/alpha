//! Книга движка бэктеста с быстрым хешем (Э-05, T-38): та же `HashMapMarketDepth` крейта `hftbacktest 0.9.4`
//! (`depth/hashmapmarketdepth.rs`, MIT) — поля, поиск лучшей цены перебором тиков (`depth_below`/`depth_above`),
//! обновления и снимок строка в строку, — но отображение «тик → объём» хешируется умножением (Fx), а не SipHash:
//! профиль 03.08 (`docs/efficiency-register.md` Э-05) — `hash_one` + `sip::write` ≈ 15 % счёта сетки. Алгоритм от
//! порядка обхода отображения не зависит (снимок сортируется), поэтому итог байт в байт тот же, что у книги
//! крейта. L3 (заявки по номерам) движку сетки не нужен и не реализован.

use std::collections::HashMap;
use std::hash::{BuildHasherDefault, Hasher};

use super::levels::Levels;
use super::window_depth::round_half_away;
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
    pub ask_depth: Levels,
    pub bid_depth: Levels,
    pub best_bid_tick: i64,
    pub best_ask_tick: i64,
    pub low_bid_tick: i64,
    pub high_ask_tick: i64,
}

impl FastMarketDepth {
    pub fn new(tick_size: f64, lot_size: f64) -> Self {
        Self {
            tick_size,
            lot_size,
            timestamp: 0,
            ask_depth: Levels::default(),
            bid_depth: Levels::default(),
            best_bid_tick: INVALID_MIN,
            best_ask_tick: INVALID_MAX,
            low_bid_tick: INVALID_MAX,
            high_ask_tick: INVALID_MIN,
        }
    }
}

/// Строки глубины по удалению от своей лучшей цены в тиках: ≤3 / ≤10 / ≤30 / дальше — замер TK-049, на счёт не влияет.
pub static DEPTH_ROW_BANDS: [std::sync::atomic::AtomicU64; 4] =
    [const { std::sync::atomic::AtomicU64::new(0) }; 4];

/// Классы обновлений глубины (замер TK-049, включается `ALPHA_ATTEMPT_STATS`): 0 — сдвигает лучшую цену своей стороны,
/// 1 — на лучшей цене без сдвига, 2 — на тике стены (наблюдаемый стратегией уровень), 3 — 1..3 тика от лучшей,
/// 4 — остальное; 5 — независимый счёт: любые строки в пределах ±3 тика от тика стены.
pub static DEPTH_ROW_CLASS: [std::sync::atomic::AtomicU64; 6] =
    [const { std::sync::atomic::AtomicU64::new(0) }; 6];
pub static BAND_STATS_ON: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
/// `ALPHA_BAND_COUNT_OFF=1`: не считать `DEPTH_ROW_BANDS` на каждое обновление (атомарный `fetch_add` на горячем пути);
/// строка печатается только под `ALPHA_ATTEMPT_STATS`, там счёт включён всегда.
pub static BAND_COUNT_OFF: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

thread_local! {
    static WATCH_TICK: std::cell::Cell<i64> = const { std::cell::Cell::new(i64::MIN) };
}

pub fn set_watch_tick(tick: i64) {
    if BAND_STATS_ON.load(std::sync::atomic::Ordering::Relaxed) {
        WATCH_TICK.with(|w| w.set(tick));
    }
}

/// Удаление цены своей заявки от лучшей цены той же стороны в тиках (замер TK-049, `ALPHA_ATTEMPT_STATS`):
/// [вход/выход][<0 пересекает / 0 / 1..3 / 4..10 / >10].
pub static ORDER_DIST: [[std::sync::atomic::AtomicU64; 5]; 2] =
    [const { [const { std::sync::atomic::AtomicU64::new(0) }; 5] }; 2];

pub fn note_order_dist<MD: hftbacktest::depth::MarketDepth>(
    exit: bool,
    buy: bool,
    px: f64,
    depth: &MD,
) {
    if !BAND_STATS_ON.load(std::sync::atomic::Ordering::Relaxed) {
        return;
    }
    #[allow(clippy::cast_possible_truncation)]
    let t = (px / depth.tick_size()).round() as i64;
    let d = if buy {
        depth.best_bid_tick() - t
    } else {
        t - depth.best_ask_tick()
    };
    let k = match d {
        i64::MIN..=-1 => 0,
        0 => 1,
        1..=3 => 2,
        4..=10 => 3,
        _ => 4,
    };
    ORDER_DIST[usize::from(exit)][k].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
}

fn note_band(price_tick: i64, best_tick: i64, moves_best: bool) {
    let stats = BAND_STATS_ON.load(std::sync::atomic::Ordering::Relaxed);
    if !stats && BAND_COUNT_OFF.load(std::sync::atomic::Ordering::Relaxed) {
        return;
    }
    let d = (price_tick - best_tick).unsigned_abs();
    let k = usize::from(d > 3) + usize::from(d > 10) + usize::from(d > 30);
    DEPTH_ROW_BANDS[k].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    if !stats {
        return;
    }
    let w = WATCH_TICK.with(std::cell::Cell::get);
    let c = if moves_best {
        0
    } else if d == 0 {
        1
    } else if price_tick == w {
        2
    } else if d <= 3 {
        3
    } else {
        4
    };
    DEPTH_ROW_CLASS[c].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    if w != i64::MIN && (price_tick - w).abs() <= 3 {
        DEPTH_ROW_CLASS[5].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    }
}

// Числа и приведения — как в крейте, строка в строку (гейт «байт в байт»).
#[allow(clippy::cast_possible_truncation, clippy::cast_precision_loss)]
impl L2MarketDepth for FastMarketDepth {
    #[inline(always)]
    fn update_bid_depth(
        &mut self,
        price: f64,
        qty: f64,
        timestamp: i64,
    ) -> (i64, i64, i64, f64, f64, i64) {
        let price_tick = round_half_away(price / self.tick_size) as i64;
        let qty_lot = round_half_away(qty / self.lot_size) as i64;
        let prev_best_bid_tick = self.best_bid_tick;
        note_band(
            price_tick,
            prev_best_bid_tick,
            if qty_lot > 0 {
                price_tick > prev_best_bid_tick
            } else {
                price_tick == prev_best_bid_tick
            },
        );
        let prev_qty = self
            .bid_depth
            .replace(price_tick, if qty_lot > 0 { qty } else { 0.0 });

        if qty_lot == 0 {
            if price_tick == self.best_bid_tick {
                self.best_bid_tick = self.bid_depth.below(self.best_bid_tick, self.low_bid_tick);
                if self.best_bid_tick == INVALID_MIN {
                    self.low_bid_tick = INVALID_MAX;
                }
            }
        } else {
            if price_tick > self.best_bid_tick {
                self.best_bid_tick = price_tick;
                if self.best_bid_tick >= self.best_ask_tick {
                    self.best_ask_tick =
                        self.ask_depth.above(self.best_bid_tick, self.high_ask_tick);
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

    #[inline(always)]
    fn update_ask_depth(
        &mut self,
        price: f64,
        qty: f64,
        timestamp: i64,
    ) -> (i64, i64, i64, f64, f64, i64) {
        let price_tick = round_half_away(price / self.tick_size) as i64;
        let qty_lot = round_half_away(qty / self.lot_size) as i64;
        let prev_best_ask_tick = self.best_ask_tick;
        note_band(
            price_tick,
            prev_best_ask_tick,
            if qty_lot > 0 {
                price_tick < prev_best_ask_tick
            } else {
                price_tick == prev_best_ask_tick
            },
        );
        let prev_qty = self
            .ask_depth
            .replace(price_tick, if qty_lot > 0 { qty } else { 0.0 });

        if qty_lot == 0 {
            if price_tick == self.best_ask_tick {
                self.best_ask_tick = self.ask_depth.above(self.best_ask_tick, self.high_ask_tick);
                if self.best_ask_tick == INVALID_MAX {
                    self.high_ask_tick = INVALID_MIN;
                }
            }
        } else {
            if price_tick < self.best_ask_tick {
                self.best_ask_tick = price_tick;
                if self.best_bid_tick >= self.best_ask_tick {
                    self.best_bid_tick =
                        self.bid_depth.below(self.best_ask_tick, self.low_bid_tick);
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
                            self.bid_depth.replace(t, 0.0);
                        }
                    }
                    self.best_bid_tick = self.bid_depth.below(clear_upto - 1, self.low_bid_tick);
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
                            self.ask_depth.replace(t, 0.0);
                        }
                    }
                    self.best_ask_tick = self.ask_depth.above(clear_upto + 1, self.high_ask_tick);
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
        self.bid_depth.get(self.best_bid_tick)
    }

    #[inline(always)]
    fn best_ask_qty(&self) -> f64 {
        self.ask_depth.get(self.best_ask_tick)
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
        self.bid_depth.get(price_tick)
    }

    #[inline(always)]
    fn ask_qty_at_tick(&self, price_tick: i64) -> f64 {
        self.ask_depth.get(price_tick)
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

            let price_tick = round_half_away(price / self.tick_size) as i64;
            if data[row_num].ev & BUY_EVENT == BUY_EVENT {
                self.best_bid_tick = self.best_bid_tick.max(price_tick);
                self.low_bid_tick = self.low_bid_tick.min(price_tick);
                self.bid_depth.replace(price_tick, qty);
            } else if data[row_num].ev & SELL_EVENT == SELL_EVENT {
                self.best_ask_tick = self.best_ask_tick.min(price_tick);
                self.high_ask_tick = self.high_ask_tick.max(price_tick);
                self.ask_depth.replace(price_tick, qty);
            }
        }
    }

    fn snapshot(&self) -> Vec<Event> {
        let mut events = Vec::new();
        let mut bid_depth = self
            .bid_depth
            .iter()
            .filter(|&(px_tick, _)| px_tick <= self.best_bid_tick)
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
            .filter(|&(px_tick, _)| px_tick >= self.best_ask_tick)
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
