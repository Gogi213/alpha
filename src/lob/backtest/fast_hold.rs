//! TK-049 (`ALPHA_FAST_HOLD`, заготовка): быстрый путь удержания без живых заявок. Пока у круга нет заявок, движок
//! нужен только книге: строки применяются к одной плоской локальной книге (`HoldTracker`), а в момент, когда
//! решению понадобился движок, он строится заново из двух снимков (`FastHandoff`): локальная книга на `t` и
//! биржевая (локальная + строки, уже дошедшие до биржи, но не до локальной стороны; их биржевые флаги в новом
//! движке сняты, чтобы сторона не применила строку дважды).

use super::*;
use hftbacktest::types::{Side, EXCH_EVENT, LOCAL_EVENT};
use hftbacktest::types::{
    EXCH_ASK_DEPTH_CLEAR_EVENT, EXCH_ASK_DEPTH_EVENT, EXCH_ASK_DEPTH_SNAPSHOT_EVENT,
    EXCH_BID_DEPTH_CLEAR_EVENT, EXCH_BID_DEPTH_EVENT, EXCH_BID_DEPTH_SNAPSHOT_EVENT,
    EXCH_DEPTH_CLEAR_EVENT, LOCAL_ASK_DEPTH_CLEAR_EVENT, LOCAL_ASK_DEPTH_EVENT,
    LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_CLEAR_EVENT, LOCAL_BID_DEPTH_EVENT,
    LOCAL_BID_DEPTH_SNAPSHOT_EVENT, LOCAL_DEPTH_CLEAR_EVENT, LOCAL_TRADE_EVENT,
};

/// Строка локальной стороны: те же ветки, что `Local::process` крейта (глубина; сделки — в `trades`).
fn apply_local(book: &mut FastMarketDepth, ev: &Event) {
    if ev.is(LOCAL_BID_DEPTH_CLEAR_EVENT) {
        book.clear_depth(Side::Buy, ev.px);
    } else if ev.is(LOCAL_ASK_DEPTH_CLEAR_EVENT) {
        book.clear_depth(Side::Sell, ev.px);
    } else if ev.is(LOCAL_DEPTH_CLEAR_EVENT) {
        book.clear_depth(Side::None, 0.0);
    } else if ev.is(LOCAL_BID_DEPTH_EVENT) || ev.is(LOCAL_BID_DEPTH_SNAPSHOT_EVENT) {
        book.update_bid_depth(ev.px, ev.qty, ev.local_ts);
    } else if ev.is(LOCAL_ASK_DEPTH_EVENT) || ev.is(LOCAL_ASK_DEPTH_SNAPSHOT_EVENT) {
        book.update_ask_depth(ev.px, ev.qty, ev.local_ts);
    }
}

/// Строка биржевой стороны без заявок: те же ветки глубины, что `PartialFillExchange::process` (сделки без заявок
/// книгу не меняют).
fn apply_exch(book: &mut FastMarketDepth, ev: &Event) {
    if ev.is(EXCH_BID_DEPTH_CLEAR_EVENT) {
        book.clear_depth(Side::Buy, ev.px);
    } else if ev.is(EXCH_ASK_DEPTH_CLEAR_EVENT) {
        book.clear_depth(Side::Sell, ev.px);
    } else if ev.is(EXCH_DEPTH_CLEAR_EVENT) {
        book.clear_depth(Side::None, 0.0);
    } else if ev.is(EXCH_BID_DEPTH_EVENT) || ev.is(EXCH_BID_DEPTH_SNAPSHOT_EVENT) {
        book.update_bid_depth(ev.px, ev.qty, ev.exch_ts);
    } else if ev.is(EXCH_ASK_DEPTH_EVENT) || ev.is(EXCH_ASK_DEPTH_SNAPSHOT_EVENT) {
        book.update_ask_depth(ev.px, ev.qty, ev.exch_ts);
    }
}

/// Всё, что нужно, чтобы поднять движок на момент `t` посреди круга.
pub struct FastHandoff {
    pub t_ns: i64,
    pub local: DepthSnapshot,
    pub exch: DepthSnapshot,
    /// Строки между курсорами сторон: уже применены биржей, локальной стороне ещё предстоят (флаг биржи снят).
    pub middle: Vec<Event>,
    /// Индекс первой строки хвоста ленты (общий для обеих сторон, берётся как есть).
    pub tail_start: usize,
}

pub struct HoldTracker<'a> {
    rows: &'a [Event],
    base: usize,
    lcur: usize,
    clean: bool,
    max_exch_ts: i64,
    book: FastMarketDepth,
    /// Локальные сделки, пришедшие с прошлого `take_trades`.
    pub trades: Vec<Event>,
}

impl<'a> HoldTracker<'a> {
    /// `book` — локальная книга на момент до `rows[base]` (обе стороны движка в этот момент равны).
    pub fn new(rows: &'a [Event], base: usize, book: FastMarketDepth) -> Self {
        Self {
            rows,
            base,
            lcur: base,
            clean: true,
            max_exch_ts: i64::MIN,
            book,
            trades: Vec::new(),
        }
    }

    pub fn book(&self) -> &FastMarketDepth {
        &self.book
    }

    pub fn cursor(&self) -> usize {
        self.lcur
    }

    pub fn base(&self) -> usize {
        self.base
    }

    /// Применить локальные строки до `t` включительно (как `goto`: префикс по порядку строк, стоп — на первой
    /// локальной строке позже `t`).
    pub fn advance_to(&mut self, t: i64) {
        while let Some(ev) = self.rows.get(self.lcur) {
            if ev.is(LOCAL_EVENT) {
                if ev.local_ts > t {
                    break;
                }
                apply_local(&mut self.book, ev);
                if ev.is(LOCAL_TRADE_EVENT) {
                    self.trades.push(ev.clone());
                }
                if !ev.is(EXCH_EVENT) {
                    self.clean = false;
                }
                self.max_exch_ts = self.max_exch_ts.max(ev.exch_ts);
            } else if ev.is(EXCH_EVENT) {
                self.clean = false;
            }
            self.lcur += 1;
        }
    }

    pub fn take_trades(&mut self) -> std::vec::Drain<'_, Event> {
        self.trades.drain(..)
    }

    /// Состояние для нового движка на `t`; `None` — случай, где биржевую книгу по локальной не восстановить
    /// (строка без пары флагов до курсора или биржа отстаёт от локальной стороны) — круг идёт полным путём.
    pub fn handoff(&self, t: i64, tick_size: f64, lot_size: f64) -> Option<FastHandoff> {
        if !self.clean || self.max_exch_ts > t {
            return None;
        }
        let mut ecur = self.lcur;
        while let Some(ev) = self.rows.get(ecur) {
            if ev.is(EXCH_EVENT) && ev.exch_ts > t {
                break;
            }
            ecur += 1;
        }
        let local = DepthSnapshot::of(&self.book);
        let mut exch_book = local.build(tick_size, lot_size);
        let mut middle = Vec::new();
        for ev in &self.rows[self.lcur..ecur] {
            apply_exch(&mut exch_book, ev);
            if ev.is(LOCAL_EVENT) {
                let mut e = ev.clone();
                e.ev &= !EXCH_EVENT;
                middle.push(e);
            }
        }
        Some(FastHandoff {
            t_ns: t,
            local,
            exch: DepthSnapshot::of(&exch_book),
            middle,
            tail_start: ecur,
        })
    }
}

/// Новый движок на `h.t_ns`: локальная и биржевая книги — свои снимки, лента — `middle` + `rows[tail_start..]`.
pub fn with_backtest_from_handoff<R>(
    h: &FastHandoff,
    rows: &[Event],
    tick_size: f64,
    lot_size: f64,
    exec_latency: ExecLatency,
    queue_model: QueueModelKind,
    f: impl FnOnce(&mut Backtest<FastMarketDepth>) -> R,
) -> R {
    let anchor = [Event {
        ev: LOCAL_BID_DEPTH_EVENT | EXCH_BID_DEPTH_EVENT,
        exch_ts: h.t_ns,
        local_ts: h.t_ns,
        px: 0.0,
        qty: 0.0,
        order_id: 0,
        ival: 0,
        fval: 0.0,
    }];
    let mut sources = vec![DataSource::Data(Data::from_data(&anchor))];
    if !h.middle.is_empty() {
        sources.push(DataSource::Data(Data::from_data(&h.middle)));
    }
    let tail = &rows[h.tail_start.min(rows.len())..];
    if !tail.is_empty() {
        // SAFETY: `rows` жив до конца функции, `Backtest` умирает раньше (`drop(bt)`), крейт только читает.
        sources.push(DataSource::Data(unsafe { borrowed_data(tail) }));
    }
    // Крейт зовёт фабрику книги дважды: сначала локальная сторона, затем биржевая (`L2AssetBuilder::build`).
    let calls = std::cell::Cell::new(0u8);
    let local = h.local.clone();
    let exch = h.exch.clone();
    let mut bt = build_backtest_from(
        sources,
        exec_latency,
        move || {
            let n = calls.get();
            calls.set(n + 1);
            if n == 0 { &local } else { &exch }.build(tick_size, lot_size)
        },
        queue_model,
    );
    let out = f(&mut bt);
    drop(bt);
    out
}
