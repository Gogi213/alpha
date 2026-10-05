//! TK-049 (`ALPHA_FAST_BOOK`, заготовка): общая книга символ-суток. Круги одного окна читают один и тот же суффикс
//! ленты и каждый сам применяет строки к плоской книге (`HoldTracker`); здесь локальные строки применяются один раз
//! вперёд, а книга на любом курсоре восстанавливается от ближайшей контрольной точки назад (≤ `STRIDE` строк).

use super::fast_depth::TickHasher;
use super::fast_hold::apply_local;
use super::*;
use hftbacktest::depth::{L2MarketDepth, MarketDepth, INVALID_MAX, INVALID_MIN};
use hftbacktest::types::{
    LOCAL_ASK_DEPTH_CLEAR_EVENT, LOCAL_ASK_DEPTH_EVENT, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT,
    LOCAL_BID_DEPTH_CLEAR_EVENT, LOCAL_BID_DEPTH_EVENT, LOCAL_BID_DEPTH_SNAPSHOT_EVENT,
    LOCAL_DEPTH_CLEAR_EVENT, LOCAL_EVENT,
};
use std::collections::HashMap;
use std::hash::BuildHasherDefault;

/// Изменения уровня одной стороны по тику: `(смещение строки от base, объём после строки)`, смещения растут.
type Changes = HashMap<i64, Vec<(u32, f64)>, BuildHasherDefault<TickHasher>>;

/// Строк ленты между контрольными точками полной книги.
pub const STRIDE: usize = 4096;

pub struct TapeBook<'a> {
    rows: &'a [Event],
    base: usize,
    tick: f64,
    lot: f64,
    cur: usize,
    book: FastMarketDepth,
    /// `(best_bid_tick, best_ask_tick)` после строки `base + i`.
    bbo: Vec<(i64, i64)>,
    /// Книга перед строкой `base + k * STRIDE`.
    points: Vec<DepthSnapshot>,
    start: DepthSnapshot,
    bid_changes: Changes,
    ask_changes: Changes,
    /// Смещение первой строки clear: ряд изменений уровней после неё недостоверен (`SeriesDepth` отказывает).
    broken: Option<usize>,
}

impl<'a> TapeBook<'a> {
    /// `start` — локальная книга перед `rows[base]` (как у `HoldTracker::new`).
    pub fn new(rows: &'a [Event], base: usize, start: &DepthSnapshot, tick: f64, lot: f64) -> Self {
        Self {
            rows,
            base,
            tick,
            lot,
            cur: base,
            book: start.build(tick, lot),
            bbo: Vec::new(),
            points: Vec::new(),
            start: start.clone(),
            bid_changes: Changes::default(),
            ask_changes: Changes::default(),
            broken: None,
        }
    }

    pub fn base(&self) -> usize {
        self.base
    }

    /// Дорастить ряд и точки до курсора `upto` (строка `upto` не применяется).
    pub fn grow_to(&mut self, upto: usize) {
        let upto = upto.min(self.rows.len());
        while self.cur < upto {
            let i = self.cur - self.base;
            if i.is_multiple_of(STRIDE) {
                self.points.push(DepthSnapshot::of(&self.book));
            }
            let ev = &self.rows[self.cur];
            if ev.is(LOCAL_EVENT) {
                let off = i as u32;
                if ev.is(LOCAL_BID_DEPTH_CLEAR_EVENT)
                    || ev.is(LOCAL_ASK_DEPTH_CLEAR_EVENT)
                    || ev.is(LOCAL_DEPTH_CLEAR_EVENT)
                {
                    self.broken.get_or_insert(i);
                    apply_local(&mut self.book, ev);
                } else if ev.is(LOCAL_BID_DEPTH_EVENT) || ev.is(LOCAL_BID_DEPTH_SNAPSHOT_EVENT) {
                    let (t, ..) = self.book.update_bid_depth(ev.px, ev.qty, ev.local_ts);
                    let q = self.book.bid_depth.get(t);
                    self.bid_changes.entry(t).or_default().push((off, q));
                } else if ev.is(LOCAL_ASK_DEPTH_EVENT) || ev.is(LOCAL_ASK_DEPTH_SNAPSHOT_EVENT) {
                    let (t, ..) = self.book.update_ask_depth(ev.px, ev.qty, ev.local_ts);
                    let q = self.book.ask_depth.get(t);
                    self.ask_changes.entry(t).or_default().push((off, q));
                }
            }
            self.bbo
                .push((self.book.best_bid_tick, self.book.best_ask_tick));
            self.cur += 1;
        }
    }

    /// `(best_bid_tick, best_ask_tick)` локальной книги после строки `row` (уже выращенной).
    pub fn bbo_after(&self, row: usize) -> Option<(i64, i64)> {
        self.bbo.get(row.checked_sub(self.base)?).copied()
    }

    /// Локальная книга перед строкой `cursor` — то же, что `HoldTracker::book()` при `cursor() == cursor`.
    pub fn book_at(&mut self, cursor: usize) -> FastMarketDepth {
        self.grow_to(cursor);
        if cursor == self.cur {
            return self.book_from_live();
        }
        let k = (cursor - self.base) / STRIDE;
        let mut book = self.points[k].build(self.tick, self.lot);
        for ev in &self.rows[self.base + k * STRIDE..cursor] {
            if ev.is(LOCAL_EVENT) {
                apply_local(&mut book, ev);
            }
        }
        book
    }

    fn book_from_live(&self) -> FastMarketDepth {
        DepthSnapshot::of(&self.book).build(self.tick, self.lot)
    }
}

fn level_at(changes: &Changes, start: &[(i64, f64)], tick: i64, applied: usize) -> f64 {
    if let Some(list) = changes.get(&tick) {
        let n = list.partition_point(|&(off, _)| (off as usize) < applied);
        if n > 0 {
            return list[n - 1].1;
        }
    }
    match start.binary_search_by_key(&tick, |&(t, _)| t) {
        Ok(i) => start[i].1,
        Err(_) => 0.0,
    }
}

/// Лёгкий `MarketDepth` поверх `TapeBook`: книга перед строкой `cursor` без плоской книги и без apply строк.
pub struct SeriesDepth<'b, 'a> {
    tb: &'b TapeBook<'a>,
    cursor: usize,
}

impl<'b, 'a> SeriesDepth<'b, 'a> {
    /// `None`, если ряд до `cursor` не выращен или в нём были clear-строки.
    pub fn new(tb: &'b TapeBook<'a>, cursor: usize) -> Option<Self> {
        let applied = cursor.checked_sub(tb.base)?;
        if cursor > tb.cur || tb.broken.is_some_and(|b| applied > b) {
            return None;
        }
        Some(Self { tb, cursor })
    }

    fn applied(&self) -> usize {
        self.cursor - self.tb.base
    }
}

#[allow(clippy::cast_precision_loss)]
impl MarketDepth for SeriesDepth<'_, '_> {
    fn best_bid(&self) -> f64 {
        match self.best_bid_tick() {
            INVALID_MIN => f64::NAN,
            t => t as f64 * self.tb.tick,
        }
    }

    fn best_ask(&self) -> f64 {
        match self.best_ask_tick() {
            INVALID_MAX => f64::NAN,
            t => t as f64 * self.tb.tick,
        }
    }

    fn best_bid_tick(&self) -> i64 {
        match self.applied() {
            0 => self.tb.start.best_bid_tick,
            n => self.tb.bbo[n - 1].0,
        }
    }

    fn best_ask_tick(&self) -> i64 {
        match self.applied() {
            0 => self.tb.start.best_ask_tick,
            n => self.tb.bbo[n - 1].1,
        }
    }

    fn best_bid_qty(&self) -> f64 {
        self.bid_qty_at_tick(self.best_bid_tick())
    }

    fn best_ask_qty(&self) -> f64 {
        self.ask_qty_at_tick(self.best_ask_tick())
    }

    fn tick_size(&self) -> f64 {
        self.tb.tick
    }

    fn lot_size(&self) -> f64 {
        self.tb.lot
    }

    fn bid_qty_at_tick(&self, price_tick: i64) -> f64 {
        level_at(
            &self.tb.bid_changes,
            &self.tb.start.bids,
            price_tick,
            self.applied(),
        )
    }

    fn ask_qty_at_tick(&self, price_tick: i64) -> f64 {
        level_at(
            &self.tb.ask_changes,
            &self.tb.start.asks,
            price_tick,
            self.applied(),
        )
    }
}

#[cfg(test)]
mod tests;
