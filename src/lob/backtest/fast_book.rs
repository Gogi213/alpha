//! TK-049 (`ALPHA_FAST_BOOK`, заготовка): общая книга символ-суток. Круги одного окна читают один и тот же суффикс
//! ленты и каждый сам применяет строки к плоской книге (`HoldTracker`); здесь локальные строки применяются один раз
//! вперёд, а книга на любом курсоре восстанавливается от ближайшей контрольной точки назад (≤ `STRIDE` строк).

use super::fast_hold::apply_local;
use super::*;
use hftbacktest::types::LOCAL_EVENT;

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
            if i % STRIDE == 0 {
                self.points.push(DepthSnapshot::of(&self.book));
            }
            let ev = &self.rows[self.cur];
            if ev.is(LOCAL_EVENT) {
                apply_local(&mut self.book, ev);
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

#[cfg(test)]
mod tests;
