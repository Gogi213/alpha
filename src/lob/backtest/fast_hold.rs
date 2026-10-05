//! TK-049 (`ALPHA_FAST_HOLD`, заготовка): быстрый путь удержания без живых заявок. Пока у круга нет заявок, движок
//! нужен только книге: строки применяются к одной плоской локальной книге (`HoldTracker`), а в момент, когда
//! решению понадобился движок, он строится заново из двух снимков (`FastHandoff`): локальная книга на `t` и
//! биржевая (локальная + строки, уже дошедшие до биржи, но не до локальной стороны; их биржевые флаги в новом
//! движке сняты, чтобы сторона не применила строку дважды).

use super::fast_book::{SeriesDepth, TapeBook, STRIDE};
use super::*;
use hftbacktest::depth::MarketDepth;
use hftbacktest::types::{Side, EXCH_EVENT, LOCAL_EVENT};
use hftbacktest::types::{
    EXCH_ASK_DEPTH_CLEAR_EVENT, EXCH_ASK_DEPTH_EVENT, EXCH_ASK_DEPTH_SNAPSHOT_EVENT,
    EXCH_BID_DEPTH_CLEAR_EVENT, EXCH_BID_DEPTH_EVENT, EXCH_BID_DEPTH_SNAPSHOT_EVENT,
    EXCH_DEPTH_CLEAR_EVENT, LOCAL_ASK_DEPTH_CLEAR_EVENT, LOCAL_ASK_DEPTH_EVENT,
    LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_CLEAR_EVENT, LOCAL_BID_DEPTH_EVENT,
    LOCAL_BID_DEPTH_SNAPSHOT_EVENT, LOCAL_DEPTH_CLEAR_EVENT, LOCAL_TRADE_EVENT,
};

/// Строка локальной стороны: те же ветки, что `Local::process` крейта (глубина; сделки — в `trades`).
pub(super) fn apply_local(book: &mut FastMarketDepth, ev: &Event) {
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
    /// Общая книга окна (`ALPHA_FAST_BOOK`): строки применяет она, один раз на окно; `book` подтягивается до
    /// `lcur` только перед чтением целиком (`sync_book`), `wcur` — до какой строки он уже дошёл.
    tape: Option<*mut TapeBook<'static>>,
    wcur: usize,
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
            tape: None,
            wcur: base,
            trades: Vec::new(),
        }
    }

    /// Трекер на общей книге окна: `tape` построена над тем же срезом `rows` с базой 0; рабочая книга берётся
    /// из неё на строке `base`.
    ///
    /// # Safety
    /// `tape` жив дольше трекера и не имеет других ссылок на время его работы.
    pub unsafe fn with_tape(rows: &'a [Event], base: usize, tape: *mut TapeBook<'static>) -> Self {
        // SAFETY: вызывающий держит `tape` живой и без других ссылок на время вызова.
        let book = unsafe { &mut *tape }.book_at(base);
        let mut t = Self::new(rows, base, book);
        t.tape = Some(tape);
        t
    }

    /// Подтянуть рабочую книгу до курсора (без общей книги — ничего: она и так на курсоре).
    pub fn sync_book(&mut self) {
        let Some(tp) = self.tape else { return };
        if self.wcur == self.lcur {
            return;
        }
        if self.lcur - self.wcur <= STRIDE / 2 {
            for ev in &self.rows[self.wcur..self.lcur] {
                if ev.is(LOCAL_EVENT) {
                    apply_local(&mut self.book, ev);
                }
            }
        } else {
            // SAFETY: см. `with_tape`.
            self.book = unsafe { &mut *tp }.book_at(self.lcur);
        }
        self.wcur = self.lcur;
    }

    /// Подпись входов удержания на курсоре: по ряду общей книги, если он достоверен, иначе по рабочей книге.
    pub fn input_sig(&mut self, state: &StrategyState, now: i64) -> Option<[u64; 5]> {
        if let Some(tp) = self.tape {
            // SAFETY: см. `with_tape`; ряд уже выращен `advance_to`.
            let tape: &TapeBook<'static> = unsafe { &*tp };
            if let Some(sd) = SeriesDepth::new(tape, self.lcur) {
                return state.hold_input_sig(&sd, now);
            }
        }
        self.sync_book();
        state.hold_input_sig(&self.book, now)
    }

    pub fn book(&self) -> &FastMarketDepth {
        &self.book
    }

    /// Книга на курсоре из ряда общей книги (без apply строк); `None` — ленты нет или ряд недостоверен.
    fn series(&self) -> Option<SeriesDepth<'static, 'static>> {
        let tp = self.tape?;
        // SAFETY: см. `with_tape`; ряд уже выращен `advance_to`, лента на время шага не меняется.
        SeriesDepth::new(unsafe { &*tp }, self.lcur)
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
                if self.tape.is_none() {
                    apply_local(&mut self.book, ev);
                }
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
        if let Some(tp) = self.tape {
            // SAFETY: см. `with_tape`.
            unsafe { &mut *tp }.grow_to(self.lcur);
        }
    }

    pub fn take_trades(&mut self) -> std::vec::Drain<'_, Event> {
        self.trades.drain(..)
    }

    /// Состояние для нового движка на `t`; `None` — случай, где биржевую книгу по локальной не восстановить
    /// (строка без пары флагов до курсора или биржа отстаёт от локальной стороны) — круг идёт полным путём.
    pub fn handoff(&mut self, t: i64, tick_size: f64, lot_size: f64) -> Option<FastHandoff> {
        if !self.clean || self.max_exch_ts > t {
            return None;
        }
        self.sync_book();
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
/// Движок читает `rows` по сырому указателю: срез должен жить дольше движка.
pub fn build_backtest_from_handoff(
    h: &FastHandoff,
    rows: &[Event],
    tick_size: f64,
    lot_size: f64,
    exec_latency: ExecLatency,
    queue_model: QueueModelKind,
) -> Backtest<FastMarketDepth> {
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
        // SAFETY: см. doc функции: `rows` переживает движок, крейт только читает.
        sources.push(DataSource::Data(unsafe { borrowed_data(tail) }));
    }
    // Крейт зовёт фабрику книги дважды: сначала локальная сторона, затем биржевая (`L2AssetBuilder::build`).
    let calls = std::cell::Cell::new(0u8);
    let local = h.local.clone();
    let exch = h.exch.clone();
    build_backtest_from(
        sources,
        exec_latency,
        move || {
            let n = calls.get();
            calls.set(n + 1);
            if n == 0 { &local } else { &exch }.build(tick_size, lot_size)
        },
        queue_model,
    )
}

/// То же в замыкании (тесты): движок умирает до выхода.
pub fn with_backtest_from_handoff<R>(
    h: &FastHandoff,
    rows: &[Event],
    tick_size: f64,
    lot_size: f64,
    exec_latency: ExecLatency,
    queue_model: QueueModelKind,
    f: impl FnOnce(&mut Backtest<FastMarketDepth>) -> R,
) -> R {
    let mut bt =
        build_backtest_from_handoff(h, rows, tick_size, lot_size, exec_latency, queue_model);
    let out = f(&mut bt);
    drop(bt);
    out
}

/// Локальный курсор ленты на `t` (первая строка после `start` с локальной меткой позже `t`): так же, как
/// `HoldTracker::advance_to`, но без книги; `None`, если метки локальной стороны идут не по возрастанию
/// (курсор по времени ненадёжен — быстрый путь не включается).
pub fn local_cursor_at(rows: &[Event], start: usize, t: i64) -> Option<usize> {
    let mut prev = i64::MIN;
    let mut max_exch = i64::MIN;
    let mut i = start;
    while let Some(ev) = rows.get(i) {
        if ev.is(LOCAL_EVENT) {
            if ev.local_ts < prev {
                return None;
            }
            prev = ev.local_ts;
            if ev.local_ts > t {
                break;
            }
        }
        if ev.is(EXCH_EVENT) {
            max_exch = max_exch.max(ev.exch_ts);
        }
        i += 1;
    }
    // Строка до курсора, не дошедшая до биржи к `t`: биржевую книгу по локальной не восстановить.
    (max_exch <= t).then_some(i)
}

/// Книга для решения в скане: ряд общей книги на курсоре (без apply) либо плоская рабочая книга трекера.
pub enum HoldDepth {
    Series(SeriesDepth<'static, 'static>),
    Flat(*const FastMarketDepth),
}

impl HoldDepth {
    fn flat(&self) -> Option<&FastMarketDepth> {
        match self {
            // SAFETY: указатель ставит `FastBot::refresh_view` на книгу трекера того же бота перед каждым решением.
            Self::Flat(p) => Some(unsafe { &**p }),
            Self::Series(_) => None,
        }
    }
}

macro_rules! hd {
    ($self:ident, $m:ident $(, $a:expr)*) => {
        match $self {
            HoldDepth::Series(s) => s.$m($($a),*),
            HoldDepth::Flat(_) => $self.flat().unwrap().$m($($a),*),
        }
    };
}

impl MarketDepth for HoldDepth {
    fn best_bid(&self) -> f64 {
        hd!(self, best_bid)
    }
    fn best_ask(&self) -> f64 {
        hd!(self, best_ask)
    }
    fn best_bid_tick(&self) -> i64 {
        hd!(self, best_bid_tick)
    }
    fn best_ask_tick(&self) -> i64 {
        hd!(self, best_ask_tick)
    }
    fn best_bid_qty(&self) -> f64 {
        hd!(self, best_bid_qty)
    }
    fn best_ask_qty(&self) -> f64 {
        hd!(self, best_ask_qty)
    }
    fn tick_size(&self) -> f64 {
        hd!(self, tick_size)
    }
    fn lot_size(&self) -> f64 {
        hd!(self, lot_size)
    }
    fn bid_qty_at_tick(&self, t: i64) -> f64 {
        hd!(self, bid_qty_at_tick, t)
    }
    fn ask_qty_at_tick(&self, t: i64) -> f64 {
        hd!(self, ask_qty_at_tick, t)
    }
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
    view: HoldDepth,
}

impl<'a> FastBot<'a> {
    pub fn new(tracker: HoldTracker<'a>, now: i64) -> Self {
        Self {
            tracker,
            now,
            orders: Default::default(),
            values: hftbacktest::types::StateValues::default(),
            need_engine: false,
            view: HoldDepth::Flat(std::ptr::null()),
        }
    }

    /// Книга для следующего решения: ряд ленты, если достоверен (без apply), иначе подтянуть рабочую книгу.
    fn refresh_view(&mut self) {
        self.view = if let Some(sd) = self.tracker.series() {
            HoldDepth::Series(sd)
        } else {
            self.tracker.sync_book();
            HoldDepth::Flat(std::ptr::from_ref(self.tracker.book()))
        };
    }

    fn deny(&mut self) -> Result<hftbacktest::types::ElapseResult, BacktestError> {
        self.need_engine = true;
        Ok(hftbacktest::types::ElapseResult::Ok)
    }
}

impl hftbacktest::types::Bot<HoldDepth> for FastBot<'_> {
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
    fn depth(&self, _: usize) -> &HoldDepth {
        &self.view
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
pub(super) struct FastResume {
    pub state: StrategyState,
    pub decided_in_hold: bool,
    pub stable: bool,
    pub sig: SigMemo,
    /// `true`: часы стоят на точке сетки, шаг уже сделан, осталось решение (`on_event`) — первый проход цикла
    /// пропускает шаг.
    pub post_step: bool,
    /// Почему вышли из скана (счётчики FAST_EXIT_*): 0 нет таймерного пробуждения/не Holding, 1 конец ленты,
    /// 2 нужна заявка (`need_engine`), 3 решение не Idle, 4 круг закончен.
    pub reason: usize,
}

/// Удержание без живых заявок на плоской книге (только `ALPHA_SKIP_NOSIGNAL`-режим с `hold_skip`, без
/// `ev_steps`). Возврат — когда движок нужен: решение просит заявку, `hold_wakeup_ns == None` (сироты,
/// `wall_ring`), конец ленты, круг закончился.
pub(super) fn fast_hold_scan(
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
                reason: 0,
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
                reason: 1,
            };
        }
        let now = bot.current_timestamp();
        state.observe_wall_trades(bot.last_trades(0));
        bot.clear_last_trades(Some(0));
        let held_before = state.hold_wakeup_ns(now).is_some();
        let mark_before = state.phase_mark();
        let before = state.clone();
        let sig_before = sig;
        let skip = skip_on && sig.skip(bot.tracker.input_sig(state, now));
        if skip {
            FAST_SKIPS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        }
        let action = if skip {
            Action::Idle
        } else {
            bot.refresh_view();
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
                reason: 2,
            };
        }
        if skip_on && !matches!(action, Action::Idle) {
            sig.reset();
        }
        decided_in_hold = held_before && state.hold_wakeup_ns(now).is_some();
        stable = state.phase_mark() == mark_before && matches!(action, Action::Idle);
        FAST_STEPS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        if state.is_idle() || !matches!(action, Action::Idle) {
            return FastResume {
                state: state.clone(),
                decided_in_hold,
                stable,
                sig,
                post_step: false,
                reason: if state.is_idle() { 4 } else { 3 },
            };
        }
    }
}

/// `ALPHA_FAST_HOLD=1` — быстрый путь удержания в одиночном драйвере кругов; умолчание — выкл.
pub fn fast_hold_on() -> bool {
    #[cfg(test)]
    if FORCE_ON.with(std::cell::Cell::get) {
        return true;
    }
    static ON: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *ON.get_or_init(|| std::env::var_os("ALPHA_FAST_HOLD").is_some_and(|v| v == "1"))
}

/// `ALPHA_FAST_BOOK=1` (поверх `ALPHA_FAST_HOLD=1`) — общая книга окна для кругов; умолчание — выкл.
pub fn fast_book_on() -> bool {
    #[cfg(test)]
    if FORCE_BOOK.with(std::cell::Cell::get) {
        return true;
    }
    static ON: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *ON.get_or_init(|| std::env::var_os("ALPHA_FAST_BOOK").is_some_and(|v| v == "1"))
}

/// `ALPHA_FAST_BOOK_CHECK=1` — сверять книгу общего ряда на старте круга со снимком движка; несовпадение — счётчик
/// и круг идёт на книге движка.
fn fast_book_check_on() -> bool {
    #[cfg(test)]
    if FORCE_BOOK.with(std::cell::Cell::get) {
        return true;
    }
    static ON: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *ON.get_or_init(|| std::env::var_os("ALPHA_FAST_BOOK_CHECK").is_some_and(|v| v == "1"))
}

/// Кругов быстрого пути, стартовавших на общей книге окна.
pub static FAST_BOOK_ROUNDS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
pub static FAST_BOOK_MISMATCH: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

#[cfg(test)]
thread_local! {
    pub(super) static FORCE_BOOK: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
    pub(super) static FORCE_ON: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// Кругов, где движок заменён быстрым путём / где `handoff` не удался и круг пошёл прежним путём / строк ленты,
/// пройденных плоской книгой (процесс; на итог счёта не влияет).
pub static FAST_ROUNDS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
pub static FAST_FALLBACKS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
pub static FAST_ROWS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
/// Шагов решения в скане / из них пропущенных по подписи входов; строки окна после старта круга (всё окно) и
/// строки, пройденные плоской книгой, и число кругов — по причине выхода (`FastResume::reason`).
pub static FAST_STEPS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
pub static FAST_SKIPS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
pub static FAST_WINROWS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
#[allow(clippy::declare_interior_mutable_const)]
const Z: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
pub static FAST_EXIT_N: [std::sync::atomic::AtomicU64; 5] = [Z; 5];
pub static FAST_EXIT_ROWS: [std::sync::atomic::AtomicU64; 5] = [Z; 5];

/// Окно круга для быстрого пути: срез ленты движка (с первой строки после `t0`) и параметры сборки нового движка.
#[derive(Clone, Copy)]
struct FastCtx {
    engine: usize,
    ptr: *const Event,
    len: usize,
    tick: f64,
    lot: f64,
    latency: ExecLatency,
    queue_model: QueueModelKind,
    tape: Option<*mut TapeBook<'static>>,
}

thread_local! {
    static FAST_CTX: std::cell::Cell<Option<FastCtx>> = const { std::cell::Cell::new(None) };
}

/// Выставляет окно круга на время `f` (только при `ALPHA_FAST_HOLD=1`). `rows` — тот самый срез, что читает
/// движок окна (`Backtest<FastMarketDepth>` из `with_backtest_over_window`).
#[allow(clippy::too_many_arguments)]
pub fn with_fast_ctx<R>(
    engine: usize,
    rows: &[Event],
    tick: f64,
    lot: f64,
    latency: ExecLatency,
    queue_model: QueueModelKind,
    tape: Option<&mut TapeBook<'_>>,
    f: impl FnOnce() -> R,
) -> R {
    if !fast_hold_on() {
        return f();
    }
    FAST_CTX.with(|c| {
        c.set(Some(FastCtx {
            engine,
            ptr: rows.as_ptr(),
            len: rows.len(),
            tick,
            lot,
            latency,
            queue_model,
            tape: tape
                .filter(|_| fast_book_on())
                .map(|t| std::ptr::from_mut(t).cast::<TapeBook<'static>>()),
        }))
    });
    let out = f();
    FAST_CTX.with(|c| c.set(None));
    out
}

pub(super) enum FastOutcome {
    /// Быстрого пути не было (нет окна, не `Backtest<FastMarketDepth>`, курсор/handoff невозможны): всё как было.
    NotApplied,
    /// Удержание пройдено на плоской книге, движок в `bot` заменён новым на часах быстрого пути.
    Swapped(Box<FastResume>),
}

/// Быстрый путь удержания для `run_round`: плоская книга до момента, когда нужен движок, затем замена движка.
#[allow(clippy::too_many_arguments)]
pub(super) fn try_fast_hold<B, MD>(
    bot: &mut B,
    asset_no: usize,
    state: &mut StrategyState,
    cap: i64,
    decided_in_hold: bool,
    stable: bool,
    sig: SigMemo,
    skip_on: bool,
) -> FastOutcome
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let Some(ctx) = FAST_CTX.with(|c| c.get()) else {
        return FastOutcome::NotApplied;
    };
    let raw = std::ptr::from_mut::<B>(bot);
    if raw as *mut () as usize != ctx.engine {
        return FastOutcome::NotApplied;
    }
    // SAFETY: адрес совпал с адресом движка окна, выставленным `with_fast_ctx` вокруг шага круга, — значит `B` и
    // есть `Backtest<FastMarketDepth>`, а других ссылок на движок на время вызова нет (`bot` — `&mut`).
    let bt = unsafe { &mut *raw.cast::<Backtest<FastMarketDepth>>() };
    // SAFETY: срез окна живёт дольше этого вызова (его держит `windowed_with` на время шага круга).
    let rows = unsafe { std::slice::from_raw_parts(ctx.ptr, ctx.len) };
    let t = bt.current_timestamp();
    let Some(cur) = local_cursor_at(rows, 0, t) else {
        return FastOutcome::NotApplied;
    };
    let tape = ctx.tape.filter(|&tp| {
        !fast_book_check_on() || {
            // SAFETY: см. `with_fast_ctx` — книга окна жива и свободна на время шага круга.
            let same = DepthSnapshot::of(&unsafe { &mut *tp }.book_at(cur))
                == DepthSnapshot::of(bt.depth(asset_no));
            if !same {
                FAST_BOOK_MISMATCH.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            }
            same
        }
    });
    let state_start = state.clone();
    let tracker = match tape {
        Some(tp) => {
            FAST_BOOK_ROUNDS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            // SAFETY: указатель из FastCtx живёт на время with_fast_ctx и используется только из этого потока.
            unsafe { HoldTracker::with_tape(rows, cur, tp) }
        }
        None => HoldTracker::new(
            rows,
            cur,
            DepthSnapshot::of(bt.depth(asset_no)).build(ctx.tick, ctx.lot),
        ),
    };
    let mut fb = FastBot::new(tracker, t);
    let r = fast_hold_scan(&mut fb, state, cap, decided_in_hold, stable, sig, skip_on);
    let t2 = fb.current_timestamp();
    let Some(h) = fb.tracker.handoff(t2, ctx.tick, ctx.lot) else {
        *state = state_start;
        FAST_FALLBACKS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        return FastOutcome::NotApplied;
    };
    let used = fb.tracker.cursor().saturating_sub(cur);
    *bt = build_backtest_from_handoff(&h, rows, ctx.tick, ctx.lot, ctx.latency, ctx.queue_model);
    let _ = bt.elapse(0);
    FAST_ROUNDS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    FAST_ROWS.fetch_add(used as u64, std::sync::atomic::Ordering::Relaxed);
    FAST_WINROWS.fetch_add(
        (rows.len() - cur) as u64,
        std::sync::atomic::Ordering::Relaxed,
    );
    FAST_EXIT_N[r.reason].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    FAST_EXIT_ROWS[r.reason].fetch_add(used as u64, std::sync::atomic::Ordering::Relaxed);
    FastOutcome::Swapped(Box::new(r))
}
