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

/// Счётчики общей книги: построено лент, обращений к кэшу (попаданий — `TAPE_HITS`), строк, выращенных `grow_to`.
pub static TAPES_BUILT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
pub static TAPE_HITS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
pub static TAPE_ROWS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// Лента окна в кэше суток: книгу держит замок на время круга, ряд растёт один раз для всех форм.
pub struct TapeSlot {
    ident: (usize, usize),
    pub tape: std::sync::Mutex<TapeBook<'static>>,
}

/// Ленты окон одних суток (ключ — начало окна), общие для форм и потоков. Живёт в `SignalWindows` — те же сутки,
/// что и срез строк; лента помнит адрес и длину среза и при несовпадении строится заново.
#[derive(Default)]
pub struct TapeCache {
    slots: std::sync::Mutex<HashMap<usize, std::sync::Arc<TapeSlot>>>,
}

impl Clone for TapeCache {
    fn clone(&self) -> Self {
        Self::default()
    }
}

impl std::fmt::Debug for TapeCache {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("TapeCache")
    }
}

impl TapeCache {
    /// Лента окна `w_start` над `all[w_start..]` (`start` — книга окна перед первой строкой).
    pub fn slot(
        &self,
        all: &[Event],
        w_start: usize,
        start: &DepthSnapshot,
        tick: f64,
        lot: f64,
    ) -> std::sync::Arc<TapeSlot> {
        let ident = (all.as_ptr() as usize, all.len());
        let mut m = self
            .slots
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if let Some(s) = m.get(&w_start).filter(|s| s.ident == ident) {
            TAPE_HITS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            return s.clone();
        }
        // SAFETY: срез строк живёт столько же, сколько сутки, которым принадлежит кэш (он лежит в `SignalWindows`
        // суток); лента читает строки только пока идёт круг этих суток, а чужой срез отсекает `ident`.
        let rows: &'static [Event] = unsafe { &*std::ptr::from_ref(&all[w_start..]) };
        TAPES_BUILT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let s = std::sync::Arc::new(TapeSlot {
            ident,
            tape: std::sync::Mutex::new(TapeBook::new(rows, 0, start, tick, lot)),
        });
        m.insert(w_start, s.clone());
        s
    }
}

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
        TAPE_ROWS.fetch_add(
            upto.saturating_sub(self.cur) as u64,
            std::sync::atomic::Ordering::Relaxed,
        );
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
