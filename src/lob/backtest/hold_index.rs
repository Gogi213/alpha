//! TK-048 К-4а (`ALPHA_HOLD_INDEX`, заготовка): индекс суток для быстрого пути удержания. Один проход по локальным
//! строкам ленты вместо шести (по кругу на форму): изменения лучших `bid/ask`, история объёма по тику и сделки по
//! тику/стороне. Круг читает значения подписи `hold_input_sig` из индекса и прыгает к следующей значимой строке.
//! Здесь — построение и запросы; встраивание в `fast_hold_scan` — следующий шаг. Сейчас индекс не строится, если в
//! суточной ленте есть строки очистки глубины или метки локальной стороны идут не по возрастанию (`None` — путь
//! прежний).

use super::fast_depth::FastMarketDepth;
use super::{round_half_away, DepthSnapshot, Event};
use hftbacktest::depth::{L2MarketDepth, MarketDepth};
use hftbacktest::types::{
    EXCH_BUY_TRADE_EVENT, EXCH_SELL_TRADE_EVENT, LOCAL_ASK_DEPTH_CLEAR_EVENT,
    LOCAL_ASK_DEPTH_EVENT, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_CLEAR_EVENT,
    LOCAL_BID_DEPTH_EVENT, LOCAL_BID_DEPTH_SNAPSHOT_EVENT, LOCAL_DEPTH_CLEAR_EVENT, LOCAL_EVENT,
    LOCAL_TRADE_EVENT,
};

/// Сторона книги / сделки в ключе тика: 0 — bid (покупатели), 1 — ask.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Debug)]
pub enum BookSide {
    Bid = 0,
    Ask = 1,
}

/// Лучшие цены после строки `row` (пишется только при смене любой из двух).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct BestChange {
    pub local_ts: i64,
    pub row: usize,
    pub bid_tick: i64,
    pub ask_tick: i64,
}

/// Обновление объёма тика: `qty` с момента `local_ts`.
#[derive(Clone, Copy, Debug)]
struct QtyAt {
    tick: i64,
    row: usize,
    local_ts: i64,
    qty: f64,
}

/// Сделка локальной стороны на тике: порядок строк ленты сохранён (сумма за шаг считается в этом порядке, как
/// `observe_wall_trades`, — иначе `f64` разойдётся побитно).
#[derive(Clone, Copy, Debug)]
pub struct TradeAt {
    pub tick: i64,
    pub row: usize,
    pub local_ts: i64,
    pub qty: f64,
}

pub struct HoldIdx {
    base: FastMarketDepth,
    best: Vec<BestChange>,
    /// Обновления объёма по сторонам; внутри стороны — по `(tick, row)`.
    qty: [Vec<QtyAt>; 2],
    /// Сделки: [0] — продажи (едят bid), [1] — покупки (едят ask); по `(tick, row)`.
    sells: Vec<TradeAt>,
    buys: Vec<TradeAt>,
    /// Первая строка ленты после последней применённой локальной (конец прохода).
    pub end_row: usize,
}

impl HoldIdx {
    /// Проход по `rows[base_row..]`, `base_book` — локальная книга до `rows[base_row]`. `None` — очистка глубины
    /// в ленте или `local_ts` убывает.
    pub fn build(
        rows: &[Event],
        base_row: usize,
        base_book: &DepthSnapshot,
        tick: f64,
        lot: f64,
    ) -> Option<Self> {
        let mut book = base_book.build(tick, lot);
        let base = base_book.build(tick, lot);
        let mut best = Vec::new();
        let mut qty: [Vec<QtyAt>; 2] = [Vec::new(), Vec::new()];
        let (mut sells, mut buys) = (Vec::new(), Vec::new());
        let mut prev = i64::MIN;
        let mut last = (book.best_bid_tick(), book.best_ask_tick());
        for (i, ev) in rows.iter().enumerate().skip(base_row) {
            if !ev.is(LOCAL_EVENT) {
                continue;
            }
            if ev.local_ts < prev {
                return None;
            }
            prev = ev.local_ts;
            if ev.is(LOCAL_BID_DEPTH_CLEAR_EVENT)
                || ev.is(LOCAL_ASK_DEPTH_CLEAR_EVENT)
                || ev.is(LOCAL_DEPTH_CLEAR_EVENT)
            {
                return None;
            }
            #[allow(clippy::cast_possible_truncation)]
            let t = round_half_away(ev.px / tick) as i64;
            if ev.is(LOCAL_BID_DEPTH_EVENT) || ev.is(LOCAL_BID_DEPTH_SNAPSHOT_EVENT) {
                book.update_bid_depth(ev.px, ev.qty, ev.local_ts);
                qty[0].push(QtyAt {
                    tick: t,
                    row: i,
                    local_ts: ev.local_ts,
                    qty: book.bid_qty_at_tick(t),
                });
            } else if ev.is(LOCAL_ASK_DEPTH_EVENT) || ev.is(LOCAL_ASK_DEPTH_SNAPSHOT_EVENT) {
                book.update_ask_depth(ev.px, ev.qty, ev.local_ts);
                qty[1].push(QtyAt {
                    tick: t,
                    row: i,
                    local_ts: ev.local_ts,
                    qty: book.ask_qty_at_tick(t),
                });
            }
            if ev.is(LOCAL_TRADE_EVENT) {
                let tr = TradeAt {
                    tick: t,
                    row: i,
                    local_ts: ev.local_ts,
                    qty: ev.qty,
                };
                if ev.ev & EXCH_SELL_TRADE_EVENT != 0 {
                    sells.push(tr);
                }
                if ev.ev & EXCH_BUY_TRADE_EVENT != 0 {
                    buys.push(tr);
                }
            }
            let now = (book.best_bid_tick(), book.best_ask_tick());
            if now != last {
                best.push(BestChange {
                    local_ts: ev.local_ts,
                    row: i,
                    bid_tick: now.0,
                    ask_tick: now.1,
                });
                last = now;
            }
        }
        for v in &mut qty {
            v.sort_by_key(|q| (q.tick, q.row));
        }
        sells.sort_by_key(|x| (x.tick, x.row));
        buys.sort_by_key(|x| (x.tick, x.row));
        Some(Self {
            base,
            best,
            qty,
            sells,
            buys,
            end_row: rows.len(),
        })
    }

    /// Лучшие `(bid, ask)` на метке `t`: состояние после последней локальной строки с `local_ts ≤ t`.
    pub fn best_at(&self, t: i64) -> (i64, i64) {
        let n = self.best.partition_point(|c| c.local_ts <= t);
        match n.checked_sub(1) {
            Some(i) => (self.best[i].bid_tick, self.best[i].ask_tick),
            None => (self.base.best_bid_tick(), self.base.best_ask_tick()),
        }
    }

    /// Следующая смена лучших цен строго после `t` (метка локальной стороны).
    pub fn next_best_change(&self, t: i64) -> Option<i64> {
        self.best
            .get(self.best.partition_point(|c| c.local_ts <= t))
            .map(|c| c.local_ts)
    }

    /// Объём тика на метке `t` (последнее обновление с `local_ts ≤ t`; нет обновлений — объём базовой книги).
    pub fn qty_at(&self, side: BookSide, tick: i64, t: i64) -> f64 {
        let v = &self.qty[side as usize];
        let lo = v.partition_point(|q| q.tick < tick);
        let hi = v.partition_point(|q| q.tick <= tick);
        // Внутри тика строки идут по номеру, а метки не убывают ⇒ `local_ts ≤ t` — префикс.
        let n = v[lo..hi].partition_point(|q| q.local_ts <= t);
        match n.checked_sub(1) {
            Some(i) => v[lo + i].qty,
            None => match side {
                BookSide::Bid => self.base.bid_qty_at_tick(tick),
                BookSide::Ask => self.base.ask_qty_at_tick(tick),
            },
        }
    }

    /// Ближайшая строка с изменением объёма тика строго после `t` (метка локальной стороны).
    pub fn next_qty_change(&self, side: BookSide, tick: i64, t: i64) -> Option<i64> {
        let v = &self.qty[side as usize];
        let lo = v.partition_point(|q| q.tick < tick);
        let hi = v.partition_point(|q| q.tick <= tick);
        let n = v[lo..hi].partition_point(|q| q.local_ts <= t);
        v[lo..hi].get(n).map(|q| q.local_ts)
    }

    /// Сделки на тике (продажи — `want_sell`) с `t0 < local_ts ≤ t1`, в порядке ленты.
    pub fn trades_in(&self, want_sell: bool, tick: i64, t0: i64, t1: i64) -> &[TradeAt] {
        let v = if want_sell { &self.sells } else { &self.buys };
        let lo = v.partition_point(|x| x.tick < tick);
        let hi = v.partition_point(|x| x.tick <= tick);
        let s = &v[lo..hi];
        let a = s.partition_point(|x| x.local_ts <= t0);
        let b = s.partition_point(|x| x.local_ts <= t1);
        &s[a..b]
    }

    /// Ближайшая сделка на тике строго после `t`.
    pub fn next_trade(&self, want_sell: bool, tick: i64, t: i64) -> Option<i64> {
        self.trades_in(want_sell, tick, t, i64::MAX)
            .first()
            .map(|x| x.local_ts)
    }
}
