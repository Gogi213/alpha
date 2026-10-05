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

/// Заглушка бота для быстрого пути: часы и локальная книга из `HoldTracker`, заявок нет. Любая заявка/снятие/
/// модификация только поднимает `need_engine` и ничего не делает — вызывающий отбрасывает результат шага и
/// повторяет его на настоящем движке (`FastHandoff`).
pub struct FastBot<'a> {
    pub tracker: HoldTracker<'a>,
    now: i64,
    orders: hftbacktest::types::OrderMap,
    values: hftbacktest::types::StateValues,
    pub need_engine: bool,
}

impl<'a> FastBot<'a> {
    pub fn new(tracker: HoldTracker<'a>, now: i64) -> Self {
        Self {
            tracker,
            now,
            orders: Default::default(),
            values: hftbacktest::types::StateValues::default(),
            need_engine: false,
        }
    }

    fn deny(&mut self) -> Result<hftbacktest::types::ElapseResult, BacktestError> {
        self.need_engine = true;
        Ok(hftbacktest::types::ElapseResult::Ok)
    }
}

impl hftbacktest::types::Bot<FastMarketDepth> for FastBot<'_> {
    type Error = BacktestError;

    fn current_timestamp(&self) -> i64 {
        self.now
    }
    fn num_assets(&self) -> usize {
        1
    }
    fn position(&self, _: usize) -> f64 {
        0.0
    }
    fn state_values(&self, _: usize) -> &hftbacktest::types::StateValues {
        &self.values
    }
    fn depth(&self, _: usize) -> &FastMarketDepth {
        self.tracker.book()
    }
    fn last_trades(&self, _: usize) -> &[Event] {
        &self.tracker.trades
    }
    fn clear_last_trades(&mut self, _: Option<usize>) {
        self.tracker.trades.clear();
    }
    fn orders(&self, _: usize) -> &hftbacktest::types::OrderMap {
        &self.orders
    }
    fn submit_buy_order(
        &mut self,
        _: usize,
        _: u64,
        _: f64,
        _: f64,
        _: hftbacktest::types::TimeInForce,
        _: hftbacktest::types::OrdType,
        _: bool,
    ) -> Result<hftbacktest::types::ElapseResult, Self::Error> {
        self.deny()
    }
    fn submit_sell_order(
        &mut self,
        _: usize,
        _: u64,
        _: f64,
        _: f64,
        _: hftbacktest::types::TimeInForce,
        _: hftbacktest::types::OrdType,
        _: bool,
    ) -> Result<hftbacktest::types::ElapseResult, Self::Error> {
        self.deny()
    }
    fn submit_order(
        &mut self,
        _: usize,
        _: hftbacktest::types::OrderRequest,
        _: bool,
    ) -> Result<hftbacktest::types::ElapseResult, Self::Error> {
        self.deny()
    }
    fn modify(
        &mut self,
        _: usize,
        _: u64,
        _: f64,
        _: f64,
        _: bool,
    ) -> Result<hftbacktest::types::ElapseResult, Self::Error> {
        self.deny()
    }
    fn cancel(
        &mut self,
        _: usize,
        _: u64,
        _: bool,
    ) -> Result<hftbacktest::types::ElapseResult, Self::Error> {
        self.deny()
    }
    fn clear_inactive_orders(&mut self, _: Option<usize>) {}
    fn wait_order_response(
        &mut self,
        _: usize,
        _: u64,
        _: i64,
    ) -> Result<hftbacktest::types::ElapseResult, Self::Error> {
        self.deny()
    }
    fn wait_next_feed(
        &mut self,
        _: bool,
        _: i64,
    ) -> Result<hftbacktest::types::ElapseResult, Self::Error> {
        self.deny()
    }
    fn elapse(&mut self, duration: i64) -> Result<hftbacktest::types::ElapseResult, Self::Error> {
        self.now += duration;
        self.tracker.advance_to(self.now);
        Ok(hftbacktest::types::ElapseResult::Ok)
    }
    fn elapse_bt(
        &mut self,
        duration: i64,
    ) -> Result<hftbacktest::types::ElapseResult, Self::Error> {
        self.elapse(duration)
    }
    fn close(&mut self) -> Result<(), Self::Error> {
        Ok(())
    }
    fn feed_latency(&self, _: usize) -> Option<(i64, i64)> {
        None
    }
    fn order_latency(&self, _: usize) -> Option<(i64, i64, i64)> {
        None
    }
}

impl HoldTracker<'_> {
    /// Метка ближайшего события ленты для движка: локальная половина первой непринятой строки или биржевая
    /// половина первой строки, ещё не дошедшей до биржи к `now` (биржевая раньше на лаг фида).
    pub fn next_event_ts(&self, now: i64) -> Option<i64> {
        let first = self.rows.get(self.lcur)?;
        let mut next = if first.is(LOCAL_EVENT) {
            first.local_ts
        } else {
            first.exch_ts
        };
        for ev in &self.rows[self.lcur..] {
            if ev.is(EXCH_EVENT) && ev.exch_ts > now {
                next = next.min(ev.exch_ts);
                break;
            }
        }
        Some(next)
    }
}

/// Шаг удержания быстрого пути: то же, что `hold_step`/`elapse(10 мс)` круга, но без движка. `None` — ленты не
/// осталось (конец данных решает движок).
fn fast_step(bot: &mut FastBot, wakeup: Option<i64>, cap: i64) -> Option<()> {
    use hftbacktest::types::Bot;
    let step = ON_EVENT_POLL_STEP_NS;
    let now = bot.current_timestamp();
    let ne = bot.tracker.next_event_ts(now)?;
    let Some(th) = wakeup else {
        bot.elapse(step).ok()?;
        return Some(());
    };
    let k_wake = th.saturating_sub(now).div_euclid(step)
        + i64::from(th.saturating_sub(now).rem_euclid(step) != 0);
    let k_cap = (cap.saturating_sub(now) - 1).div_euclid(step);
    let k = k_wake.min(k_cap);
    if k <= 1 {
        bot.elapse(step).ok()?;
        return Some(());
    }
    let target = now.saturating_add(k.saturating_mul(step));
    if ne <= target {
        let ke = (ne - now).div_euclid(step) + i64::from((ne - now).rem_euclid(step) != 0);
        let g = now.saturating_add(ke.max(1).saturating_mul(step));
        bot.elapse(g - now).ok()?;
    } else {
        bot.elapse(target - now).ok()?;
    }
    Some(())
}

/// С чем движок продолжает круг после выхода из быстрого пути.
pub struct FastResume {
    pub state: StrategyState,
    pub decided_in_hold: bool,
    pub stable: bool,
    pub sig: SigMemo,
    /// `true`: часы стоят на точке сетки, шаг уже сделан, осталось решение (`on_event`) — первый проход цикла
    /// пропускает шаг.
    pub post_step: bool,
}

/// Удержание без живых заявок на плоской книге (только `ALPHA_SKIP_NOSIGNAL`-режим с `hold_skip`, без
/// `ev_steps`). Возврат — когда движок нужен: решение просит заявку, `hold_wakeup_ns == None` (сироты,
/// `wall_ring`), конец ленты, круг закончился.
pub fn fast_hold_scan(
    bot: &mut FastBot,
    state: &mut StrategyState,
    cap: i64,
    mut decided_in_hold: bool,
    mut stable: bool,
    mut sig: SigMemo,
    skip_on: bool,
) -> FastResume {
    use hftbacktest::types::Bot;
    loop {
        let now = bot.current_timestamp();
        let wake = state.hold_wakeup_ns(now);
        if wake.is_none() || !state.is_holding() {
            return FastResume {
                state: state.clone(),
                decided_in_hold,
                stable,
                sig,
                post_step: false,
            };
        }
        let wakeup = if decided_in_hold { wake } else { None };
        if fast_step(bot, wakeup, cap).is_none() {
            return FastResume {
                state: state.clone(),
                decided_in_hold,
                stable,
                sig,
                post_step: false,
            };
        }
        let now = bot.current_timestamp();
        state.observe_wall_trades(bot.last_trades(0));
        bot.clear_last_trades(Some(0));
        let held_before = state.hold_wakeup_ns(now).is_some();
        let mark_before = state.phase_mark();
        let before = state.clone();
        let sig_before = sig;
        let skip = skip_on && sig.skip(state.hold_input_sig(bot.depth(0), now));
        let action = if skip {
            Action::Idle
        } else {
            match on_event(bot, state) {
                Ok(a) => a,
                Err(_) => Action::Idle,
            }
        };
        if bot.need_engine {
            return FastResume {
                state: before,
                decided_in_hold,
                stable,
                sig: sig_before,
                post_step: true,
            };
        }
        if skip_on && !matches!(action, Action::Idle) {
            sig.reset();
        }
        decided_in_hold = held_before && state.hold_wakeup_ns(now).is_some();
        stable = state.phase_mark() == mark_before && matches!(action, Action::Idle);
        if state.is_idle() || !matches!(action, Action::Idle) {
            return FastResume {
                state: state.clone(),
                decided_in_hold,
                stable,
                sig,
                post_step: false,
            };
        }
    }
}
