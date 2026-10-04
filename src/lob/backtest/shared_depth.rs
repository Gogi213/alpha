//! Общая книга кругов (TK-049): одна `FastMarketDepth` на всех, круги читают её через ссылку. Обновляет книгу
//! только ведущий (главный проход общего движка); у ведомого `update_*`/`clear_depth` — ошибка программы, потому что
//! круг получает уже посчитанный кортеж (`PartialFillExchange::apply_*_delta`) и сам книгу не трогает.
//! Однопоточно (`Rc`): круги — сопрограммы одного потока (`sched`).

use std::cell::RefCell;
use std::rc::Rc;

use super::fast_depth::FastMarketDepth;
use hftbacktest::backtest::data::Data;
use hftbacktest::depth::{ApplySnapshot, L2MarketDepth, MarketDepth};
use hftbacktest::types::{Event, Side};

#[derive(Clone)]
pub struct SharedDepth {
    book: Rc<RefCell<FastMarketDepth>>,
    leader: bool,
}

impl SharedDepth {
    pub fn new_leader(book: FastMarketDepth) -> Self {
        Self {
            book: Rc::new(RefCell::new(book)),
            leader: true,
        }
    }

    /// Ведомый вид той же книги: читает, обновлять не может.
    pub fn follower(&self) -> Self {
        Self {
            book: Rc::clone(&self.book),
            leader: false,
        }
    }

    #[inline(always)]
    fn leader_mut(&self) -> std::cell::RefMut<'_, FastMarketDepth> {
        assert!(self.leader, "общую книгу обновляет только ведущий");
        self.book.borrow_mut()
    }
}

impl MarketDepth for SharedDepth {
    fn best_bid(&self) -> f64 {
        self.book.borrow().best_bid()
    }
    fn best_ask(&self) -> f64 {
        self.book.borrow().best_ask()
    }
    fn best_bid_tick(&self) -> i64 {
        self.book.borrow().best_bid_tick()
    }
    fn best_ask_tick(&self) -> i64 {
        self.book.borrow().best_ask_tick()
    }
    fn best_bid_qty(&self) -> f64 {
        self.book.borrow().best_bid_qty()
    }
    fn best_ask_qty(&self) -> f64 {
        self.book.borrow().best_ask_qty()
    }
    fn tick_size(&self) -> f64 {
        self.book.borrow().tick_size()
    }
    fn lot_size(&self) -> f64 {
        self.book.borrow().lot_size()
    }
    fn bid_qty_at_tick(&self, price_tick: i64) -> f64 {
        self.book.borrow().bid_qty_at_tick(price_tick)
    }
    fn ask_qty_at_tick(&self, price_tick: i64) -> f64 {
        self.book.borrow().ask_qty_at_tick(price_tick)
    }
}

impl L2MarketDepth for SharedDepth {
    fn update_bid_depth(
        &mut self,
        price: f64,
        qty: f64,
        ts: i64,
    ) -> (i64, i64, i64, f64, f64, i64) {
        self.leader_mut().update_bid_depth(price, qty, ts)
    }
    fn update_ask_depth(
        &mut self,
        price: f64,
        qty: f64,
        ts: i64,
    ) -> (i64, i64, i64, f64, f64, i64) {
        self.leader_mut().update_ask_depth(price, qty, ts)
    }
    fn clear_depth(&mut self, side: Side, clear_upto_price: f64) {
        self.leader_mut().clear_depth(side, clear_upto_price);
    }
}

impl ApplySnapshot for SharedDepth {
    fn apply_snapshot(&mut self, data: &Data<Event>) {
        self.leader_mut().apply_snapshot(data);
    }
    fn snapshot(&self) -> Vec<Event> {
        self.book.borrow().snapshot()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn follower_reads_what_leader_wrote() {
        let mut lead = SharedDepth::new_leader(FastMarketDepth::new(0.1, 1.0));
        let f1 = lead.follower();
        let f2 = lead.follower();
        let d = lead.update_bid_depth(100.0, 5.0, 1);
        assert_eq!(d.0, 1000);
        lead.update_ask_depth(100.2, 3.0, 2);
        for f in [&f1, &f2] {
            assert_eq!(f.best_bid_tick(), 1000);
            assert_eq!(f.best_ask_tick(), 1002);
            assert_eq!(f.best_bid_qty(), 5.0);
            assert_eq!(f.ask_qty_at_tick(1002), 3.0);
        }
        lead.update_bid_depth(100.1, 7.0, 3);
        assert_eq!(f2.best_bid_tick(), 1001);
    }

    #[test]
    #[should_panic(expected = "только ведущий")]
    fn follower_cannot_update() {
        let lead = SharedDepth::new_leader(FastMarketDepth::new(0.1, 1.0));
        let mut f = lead.follower();
        f.update_bid_depth(100.0, 1.0, 1);
    }
}
