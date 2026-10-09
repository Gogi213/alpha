//! TK-049 (`ALPHA_FAST_HOLD`, заготовка): быстрый путь удержания без живых заявок. Пока у круга нет заявок, движок
//! нужен только книге: строки применяются к одной плоской локальной книге (`HoldTracker`), а в момент, когда
//! решению понадобился движок, он строится заново из двух снимков (`FastHandoff`): локальная книга на `t` и
//! биржевая (локальная + строки, уже дошедшие до биржи, но не до локальной стороны; их биржевые флаги в новом
//! движке сняты, чтобы сторона не применила строку дважды).

use super::hold_index::{BookSide, HoldIdx};
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
        Some(finish_handoff(
            self.rows,
            self.lcur,
            DepthSnapshot::of(&self.book),
            t,
            tick_size,
            lot_size,
        ))
    }
}

/// Хвост `handoff`: биржевая книга и `middle` из локальной книги `local` на курсоре `lcur` (общий для `HoldTracker`
/// и `HoldIdx`).
pub(super) fn finish_handoff(
    rows: &[Event],
    lcur: usize,
    local: DepthSnapshot,
    t: i64,
    tick_size: f64,
    lot_size: f64,
) -> FastHandoff {
    let mut ecur = lcur;
    while let Some(ev) = rows.get(ecur) {
        if ev.is(EXCH_EVENT) && ev.exch_ts > t {
            break;
        }
        ecur += 1;
    }
    let mut exch_book = local.build(tick_size, lot_size);
    let mut middle = Vec::new();
    for ev in &rows[lcur..ecur] {
        apply_exch(&mut exch_book, ev);
        if ev.is(LOCAL_EVENT) {
            let mut e = ev.clone();
            e.ev &= !EXCH_EVENT;
            middle.push(e);
        }
    }
    FastHandoff {
        t_ns: t,
        local,
        exch: DepthSnapshot::of(&exch_book),
        middle,
        tail_start: ecur,
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
    let now = bot.current_timestamp();
    let ne = bot.tracker.next_event_ts(now)?;
    bot.elapse(step_duration(now, ne, wakeup, cap)).ok()?;
    Some(())
}

/// Длина шага удержания: 10 мс или прыжок к ближайшему узлу сетки не раньше `ne` (метка ближайшего значимого
/// события) в пределах `wakeup` и `cap`.
fn step_duration(now: i64, ne: i64, wakeup: Option<i64>, cap: i64) -> i64 {
    let step = ON_EVENT_POLL_STEP_NS;
    let Some(th) = wakeup else {
        return step;
    };
    let k_wake = th.saturating_sub(now).div_euclid(step)
        + i64::from(th.saturating_sub(now).rem_euclid(step) != 0);
    let k_cap = (cap.saturating_sub(now) - 1).div_euclid(step);
    let k = k_wake.min(k_cap);
    if k <= 1 {
        return step;
    }
    let target = now.saturating_add(k.saturating_mul(step));
    if ne <= target {
        let ke = (ne - now).div_euclid(step) + i64::from((ne - now).rem_euclid(step) != 0);
        now.saturating_add(ke.max(1).saturating_mul(step)) - now
    } else {
        target - now
    }
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
        let sig_before = sig;
        sig.stat_step(true, || state.hold_input_sig(bot.depth(0), now));
        let skip = skip_on && sig.skip(state.hold_input_sig(bot.depth(0), now));
        let mut before = None;
        let action = if skip {
            Action::Idle
        } else {
            before = Some(state.clone());
            match on_event(bot, state) {
                Ok(a) => a,
                Err(_) => Action::Idle,
            }
        };
        if bot.need_engine {
            return FastResume {
                state: before.unwrap_or_else(|| state.clone()),
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

/// Групповой аналог `fast_hold_scan` (Э-08 + TK-048): К вариантов одной группы ведут удержание на одной плоской
/// книге, шаг — по минимуму `hold_wakeup` (все `decided`), как у главного цикла `run_round_group`. Любое
/// расхождение с плоской книгой (заявка, `Idle`, не-`Idle` действие) откатывает ВСЕ варианты к началу шага и
/// отдаёт шаг движку (`true` = `post_step`). `false` — вышли на границе шага (нет `hold_wakeup`, конец ленты).
pub(super) fn fast_hold_scan_group(
    bot: &mut FastBot,
    states: &mut [StrategyState],
    cap: i64,
    decided: &mut [bool],
    stable: &mut [bool],
    sigs: &mut [SigMemo],
    skip_on: bool,
) -> bool {
    use hftbacktest::types::Bot;
    loop {
        let now = bot.current_timestamp();
        let mut th: Option<i64> = None;
        let mut all_decided = true;
        for (i, s) in states.iter().enumerate() {
            match (s.is_holding(), s.hold_wakeup_ns(now)) {
                (true, Some(t)) => th = Some(th.map_or(t, |x: i64| x.min(t))),
                _ => return false,
            }
            all_decided &= decided[i];
        }
        let wakeup = if all_decided { th } else { None };
        if fast_step(bot, wakeup, cap).is_none() {
            return false;
        }
        let now = bot.current_timestamp();
        for s in states.iter_mut() {
            s.observe_wall_trades(bot.last_trades(0));
        }
        bot.clear_last_trades(Some(0));
        let sigs_before: Vec<SigMemo> = sigs.to_vec();
        let decided_before: Vec<bool> = decided.to_vec();
        let stable_before: Vec<bool> = stable.to_vec();
        let mut before: Vec<Option<StrategyState>> = Vec::with_capacity(states.len());
        let mut redo = false;
        for i in 0..states.len() {
            let held_before = states[i].hold_wakeup_ns(now).is_some();
            let mark_before = states[i].phase_mark();
            sigs[i].stat_step(true, || states[i].hold_input_sig(bot.depth(0), now));
            let skip = skip_on && sigs[i].skip(states[i].hold_input_sig(bot.depth(0), now));
            let action = if skip {
                before.push(None);
                Action::Idle
            } else {
                before.push(Some(states[i].clone()));
                match on_event(bot, &mut states[i]) {
                    Ok(a) => a,
                    Err(_) => Action::Idle,
                }
            };
            if bot.need_engine || !matches!(action, Action::Idle) || states[i].is_idle() {
                redo = true;
                break;
            }
            decided[i] = held_before && states[i].hold_wakeup_ns(now).is_some();
            stable[i] = states[i].phase_mark() == mark_before;
        }
        if redo {
            for (s, b) in states.iter_mut().zip(before) {
                if let Some(b) = b {
                    *s = b;
                }
            }
            sigs.copy_from_slice(&sigs_before);
            decided.copy_from_slice(&decided_before);
            stable.copy_from_slice(&stable_before);
            return true;
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

#[cfg(test)]
thread_local! {
    pub(super) static FORCE_ON: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// Кругов, где движок заменён быстрым путём / где `handoff` не удался и круг пошёл прежним путём / строк ленты,
/// пройденных плоской книгой (процесс; на итог счёта не влияет).
pub static FAST_ROUNDS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
pub static FAST_FALLBACKS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
pub static FAST_ROWS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

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
    /// Лента суток для общего индекса (`None` — индекс выключен).
    shared: Option<SharedRaw>,
}

/// Лента суток для индекса, общего для всех кругов и окон суток (`HoldIdx` в нумерации ленты `all`).
#[derive(Clone, Copy)]
pub struct SharedDay<'a> {
    /// Строки суток: события в памяти целиком либо развёрнутый вид от базы индекса (`day_rows`).
    pub all: &'a [Event],
    /// Строка `all`, с которой строится индекс (самое раннее окно суток), и книга перед ней.
    pub base_row: usize,
    pub base_depth: &'a DepthSnapshot,
    /// Строка `all`, с которой начинается срез круга `rows`, и книга окна перед ней (сверка `_CHECK`).
    pub off: usize,
    pub win_depth: &'a DepthSnapshot,
}

#[derive(Clone, Copy)]
struct SharedRaw {
    all_ptr: *const Event,
    all_len: usize,
    base_row: usize,
    base_depth: *const DepthSnapshot,
    off: usize,
    win_depth: *const DepthSnapshot,
}

type DayKey = (usize, usize, usize, i64, i64);
type DayCache = Option<(DayKey, std::rc::Rc<Vec<Event>>)>;

thread_local! {
    /// Развёрнутый вид суток для буферного пути: ((адрес ленты, длина, строка базы), строки от базы).
    static DAY_CACHE: std::cell::RefCell<DayCache> = const { std::cell::RefCell::new(None) };
}

/// Строки суток от базы индекса, развёрнутые один раз на символо-сутки (кэш потока; ключ — `key`). Срез живёт до
/// следующего вызова с другим ключом — то есть дольше круга, который его читает.
pub fn day_rows<'a>(key: DayKey, fill: impl FnOnce() -> Vec<Event>) -> &'a [Event] {
    DAY_CACHE.with(|c| {
        let mut c = c.borrow_mut();
        if !c.as_ref().is_some_and(|(k, _)| *k == key) {
            let t0 = std::time::Instant::now();
            *c = Some((key, std::rc::Rc::new(fill())));
            idx_add(6, t0);
        }
        let v: &Vec<Event> = &c.as_ref().expect("только что заполнен").1;
        // SAFETY: вектор держит `Rc` в кэше потока до замены по другому ключу; замена идёт на следующих сутках,
        // когда круг, читающий срез, уже закончен.
        unsafe { &*std::ptr::from_ref::<[Event]>(v.as_slice()) }
    })
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
    shared: Option<SharedDay<'_>>,
    tick: f64,
    lot: f64,
    latency: ExecLatency,
    queue_model: QueueModelKind,
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
            shared: shared.map(|d| SharedRaw {
                all_ptr: d.all.as_ptr(),
                all_len: d.all.len(),
                base_row: d.base_row,
                base_depth: std::ptr::from_ref(d.base_depth),
                off: d.off,
                win_depth: std::ptr::from_ref(d.win_depth),
            }),
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
    let t_snap = std::time::Instant::now();
    let snap = DepthSnapshot::of(bt.depth(asset_no));
    idx_add(2, t_snap);
    let state_start = state.clone();
    if skip_on && hold_index_on() {
        let shared = ctx.shared;
        if shared.is_none() {
            IDX_TIMING[5].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        }
        // SAFETY: лента суток и книги окон живут дольше шага круга (как и срез `rows`).
        let day = shared.map(|s| unsafe {
            (
                std::slice::from_raw_parts(s.all_ptr, s.all_len),
                s,
                &*s.base_depth,
                &*s.win_depth,
            )
        });
        let built = day.and_then(|(all, s, base_depth, win_depth)| {
            let idx = idx_for(all, s.base_row, base_depth, ctx.tick, ctx.lot)?;
            if hold_index_check() {
                assert!(
                    idx.book_before(all, s.off, ctx.tick, ctx.lot) == *win_depth,
                    "ALPHA_HOLD_INDEX_CHECK: книга индекса на строке круга {} расходится с книгой окна",
                    s.off
                );
            }
            Some((all, s.off, idx))
        });
        if let Some((all, off, idx)) = built {
            let mut ib = IdxBot::new(&idx, all, ctx.tick, ctx.lot, t);
            if off + rows.len() < all.len() {
                ib.limit = rows.last().map_or(i64::MAX, |e| e.local_ts);
            }
            let t_scan = std::time::Instant::now();
            let r = fast_hold_scan_idx(&mut ib, state, cap, decided_in_hold, stable, sig);
            idx_add(3, t_scan);
            let t2 = ib.now;
            let t_hand = std::time::Instant::now();
            // Срез круга короче ленты суток (буфер): индекс видит строки за его концом, плоская книга — нет.
            // Дошли до конца буфера — круг пойдёт движком, как при отказе `handoff`.
            let past_buf =
                off + rows.len() < all.len() && rows.last().is_none_or(|e| t2 >= e.local_ts);
            let handoff = if past_buf || (ib.hit_limit && t2 == t) {
                None
            } else {
                idx.handoff(all, rows, off, cur, t2, ctx.tick, ctx.lot)
            };
            if let Some(h) = handoff {
                if hold_index_check() {
                    check_handoff(rows, cur, &snap, t2, &h, ctx.tick, ctx.lot);
                }
                *bt = build_backtest_from_handoff(
                    &h,
                    rows,
                    ctx.tick,
                    ctx.lot,
                    ctx.latency,
                    ctx.queue_model,
                );
                let _ = bt.elapse(0);
                idx_add(4, t_hand);
                FAST_ROUNDS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                IDX_ROUNDS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                return FastOutcome::Swapped(Box::new(r));
            }
            *state = state_start;
            FAST_FALLBACKS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            return FastOutcome::NotApplied;
        }
    }
    let book = snap.build(ctx.tick, ctx.lot);
    let mut fb = FastBot::new(HoldTracker::new(rows, cur, book), t);
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
    FastOutcome::Swapped(Box::new(r))
}

/// Быстрый путь удержания для `run_round_group`: все К вариантов на плоской книге, затем замена движка. `None` —
/// быстрого пути не было (состояния не тронуты); `Some(post_step)` — движок заменён, состояния продвинуты.
#[allow(clippy::too_many_arguments)]
pub(super) fn try_fast_hold_group<B, MD>(
    bot: &mut B,
    asset_no: usize,
    states: &mut [StrategyState],
    cap: i64,
    decided: &mut [bool],
    stable: &mut [bool],
    sigs: &mut [SigMemo],
    skip_on: bool,
) -> Option<bool>
where
    B: Bot<MD>,
    MD: MarketDepth,
{
    let ctx = FAST_CTX.with(|c| c.get())?;
    let raw = std::ptr::from_mut::<B>(bot);
    if raw as *mut () as usize != ctx.engine {
        return None;
    }
    // SAFETY: см. `try_fast_hold` — адрес совпал с движком окна, `&mut` единственный.
    let bt = unsafe { &mut *raw.cast::<Backtest<FastMarketDepth>>() };
    // SAFETY: срез окна живёт дольше этого вызова.
    let rows = unsafe { std::slice::from_raw_parts(ctx.ptr, ctx.len) };
    let t = bt.current_timestamp();
    let cur = local_cursor_at(rows, 0, t)?;
    let book = DepthSnapshot::of(bt.depth(asset_no)).build(ctx.tick, ctx.lot);
    let states_start = states.to_vec();
    let (decided_start, stable_start, sigs_start) =
        (decided.to_vec(), stable.to_vec(), sigs.to_vec());
    let mut fb = FastBot::new(HoldTracker::new(rows, cur, book), t);
    let post_step = fast_hold_scan_group(&mut fb, states, cap, decided, stable, sigs, skip_on);
    let t2 = fb.current_timestamp();
    let Some(h) = fb.tracker.handoff(t2, ctx.tick, ctx.lot) else {
        states.clone_from_slice(&states_start);
        decided.copy_from_slice(&decided_start);
        stable.copy_from_slice(&stable_start);
        sigs.copy_from_slice(&sigs_start);
        FAST_FALLBACKS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        return None;
    };
    let used = fb.tracker.cursor().saturating_sub(cur);
    *bt = build_backtest_from_handoff(&h, rows, ctx.tick, ctx.lot, ctx.latency, ctx.queue_model);
    let _ = bt.elapse(0);
    FAST_ROUNDS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    FAST_ROWS.fetch_add(used as u64, std::sync::atomic::Ordering::Relaxed);
    Some(post_step)
}

// ---- TK-048 К-4: индекс удержания (`ALPHA_HOLD_INDEX=1`) -------------------------------------------------------

/// `ALPHA_HOLD_INDEX=1` — быстрый путь берёт значения подписи из индекса окна (`HoldIdx`), а не из плоской книги;
/// нужен `ALPHA_FAST_HOLD=1` и `ALPHA_SKIP_SAME=1`. Умолчание — выкл.
pub fn hold_index_on() -> bool {
    #[cfg(test)]
    if FORCE_IDX.with(std::cell::Cell::get) {
        return true;
    }
    static ON: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *ON.get_or_init(|| std::env::var_os("ALPHA_HOLD_INDEX").is_some_and(|v| v == "1"))
}

/// `ALPHA_HOLD_INDEX_CHECK=1` — на каждом выходе из индексного пути строить `handoff` ещё и плоской книгой и
/// сверять (паника при расхождении). Только отладка (d15).
fn hold_index_check() -> bool {
    static ON: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *ON.get_or_init(|| std::env::var_os("ALPHA_HOLD_INDEX_CHECK").is_some_and(|v| v == "1"))
}

#[cfg(test)]
thread_local! {
    pub(super) static FORCE_IDX: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// Кругов, прошедших индексным путём (процесс; на итог счёта не влияет).
pub static IDX_ROUNDS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
/// Диагностика индексного пути (процесс): [построений, мкс на построения, мкс на снимок книги, мкс на скан,
/// мкс на handoff + пересборку движка, кругов без индекса суток, мкс на развёртку суток]; на итог счёта не влияет.
pub static IDX_TIMING: [std::sync::atomic::AtomicU64; 7] =
    [const { std::sync::atomic::AtomicU64::new(0) }; 7];

fn idx_add(i: usize, t0: std::time::Instant) {
    IDX_TIMING[i].fetch_add(
        u64::try_from(t0.elapsed().as_micros()).unwrap_or(u64::MAX),
        std::sync::atomic::Ordering::Relaxed,
    );
}

type IdxCache = Option<(
    (usize, usize, i64, i64, usize),
    Option<std::rc::Rc<HoldIdx>>,
)>;

thread_local! {
    /// Индекс последнего окна: ((адрес ленты, длина, `local_ts` первой и последней строк), индекс).
    static IDX_CACHE: std::cell::RefCell<IdxCache> = const { std::cell::RefCell::new(None) };
}

/// Книга по индексу на метке `t`: ровно то, что читают `hold_input_sig` и `on_event` в удержании.
pub struct IdxDepth<'a> {
    idx: &'a HoldIdx,
    t: i64,
    tick_size: f64,
    lot_size: f64,
}

impl hftbacktest::depth::MarketDepth for IdxDepth<'_> {
    fn best_bid(&self) -> f64 {
        let t = self.best_bid_tick();
        if t == hftbacktest::depth::INVALID_MIN {
            f64::NAN
        } else {
            t as f64 * self.tick_size
        }
    }
    fn best_ask(&self) -> f64 {
        let t = self.best_ask_tick();
        if t == hftbacktest::depth::INVALID_MAX {
            f64::NAN
        } else {
            t as f64 * self.tick_size
        }
    }
    fn best_bid_tick(&self) -> i64 {
        self.idx.best_at(self.t).0
    }
    fn best_ask_tick(&self) -> i64 {
        self.idx.best_at(self.t).1
    }
    fn best_bid_qty(&self) -> f64 {
        self.bid_qty_at_tick(self.best_bid_tick())
    }
    fn best_ask_qty(&self) -> f64 {
        self.ask_qty_at_tick(self.best_ask_tick())
    }
    fn tick_size(&self) -> f64 {
        self.tick_size
    }
    fn lot_size(&self) -> f64 {
        self.lot_size
    }
    fn bid_qty_at_tick(&self, price_tick: i64) -> f64 {
        self.idx.qty_at(BookSide::Bid, price_tick, self.t)
    }
    fn ask_qty_at_tick(&self, price_tick: i64) -> f64 {
        self.idx.qty_at(BookSide::Ask, price_tick, self.t)
    }
}

/// Заглушка бота индексного пути: как `FastBot`, но книга и сделки — из индекса.
pub struct IdxBot<'a> {
    depth: IdxDepth<'a>,
    rows: &'a [Event],
    now: i64,
    trades: Vec<Event>,
    orders: hftbacktest::types::OrderMap,
    values: hftbacktest::types::StateValues,
    pub need_engine: bool,
    /// Метка последней строки буфера круга, если буфер короче ленты суток (иначе `i64::MAX`): шаг за неё скан не делает.
    limit: i64,
    /// Скан остановился у `limit` (а не по своему условию выхода).
    hit_limit: bool,
}

impl<'a> IdxBot<'a> {
    fn new(idx: &'a HoldIdx, rows: &'a [Event], tick: f64, lot: f64, now: i64) -> Self {
        Self {
            depth: IdxDepth {
                idx,
                t: now,
                tick_size: tick,
                lot_size: lot,
            },
            rows,
            now,
            trades: Vec::new(),
            orders: Default::default(),
            values: hftbacktest::types::StateValues::default(),
            need_engine: false,
            limit: i64::MAX,
            hit_limit: false,
        }
    }

    fn deny(&mut self) -> Result<hftbacktest::types::ElapseResult, BacktestError> {
        self.need_engine = true;
        Ok(hftbacktest::types::ElapseResult::Ok)
    }
}

impl<'a> hftbacktest::types::Bot<IdxDepth<'a>> for IdxBot<'a> {
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
    fn depth(&self, _: usize) -> &IdxDepth<'a> {
        &self.depth
    }
    fn last_trades(&self, _: usize) -> &[Event] {
        &self.trades
    }
    fn clear_last_trades(&mut self, _: Option<usize>) {
        self.trades.clear();
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
        let prev = self.now;
        self.now += duration;
        self.depth.t = self.now;
        self.depth
            .idx
            .trade_events(self.rows, prev, self.now, &mut self.trades);
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

/// `fast_hold_scan` на индексе: тот же цикл, но когда подпись уже повторилась (`reps ≥ 1` — следующий вызов на тех же
/// входах будет пропущен), шаг прыгает к ближайшей строке, меняющей вход подписи, а не к любой строке ленты. Узлы
/// сетки между ними пропустил бы `SigMemo`, так что состояние то же. Только одиночный круг.
pub(super) fn fast_hold_scan_idx<'a>(
    bot: &mut IdxBot<'a>,
    state: &mut StrategyState,
    cap: i64,
    mut decided_in_hold: bool,
    mut stable: bool,
    mut sig: SigMemo,
) -> FastResume {
    use hftbacktest::types::Bot;
    let idx = bot.depth.idx;
    loop {
        let now = bot.current_timestamp();
        let wake = state.hold_wakeup_ns(now);
        let out = |state: &StrategyState, decided_in_hold, stable, sig| FastResume {
            state: state.clone(),
            decided_in_hold,
            stable,
            sig,
            post_step: false,
        };
        if wake.is_none() || !state.is_holding() {
            return out(state, decided_in_hold, stable, sig);
        }
        let wakeup = if decided_in_hold { wake } else { None };
        let Some(any) = idx.next_row_ts(now) else {
            return out(state, decided_in_hold, stable, sig);
        };
        let ne = match state.hold_watch() {
            Some((long, lt)) if sig.sig.is_some() && sig.reps >= 1 => {
                idx.next_sig_ts(long, lt, now).unwrap_or(i64::MAX)
            }
            _ => any,
        };
        let dur = step_duration(now, ne, wakeup, cap);
        if now.saturating_add(dur) >= bot.limit {
            // Шаг ушёл бы за буфер круга: дальше всё равно движок (handoff невозможен за концом буфера).
            bot.hit_limit = true;
            return out(state, decided_in_hold, stable, sig);
        }
        if bot.elapse(dur).is_err() {
            return out(state, decided_in_hold, stable, sig);
        }
        let now = bot.current_timestamp();
        state.observe_wall_trades(bot.last_trades(0));
        bot.clear_last_trades(Some(0));
        let held_before = state.hold_wakeup_ns(now).is_some();
        let mark_before = state.phase_mark();
        let sig_before = sig;
        let skip = sig.skip(state.hold_input_sig(bot.depth(0), now));
        let mut before = None;
        let action = if skip {
            Action::Idle
        } else {
            before = Some(state.clone());
            match on_event(bot, state) {
                Ok(a) => a,
                Err(_) => Action::Idle,
            }
        };
        if bot.need_engine {
            return FastResume {
                state: before.unwrap_or_else(|| state.clone()),
                decided_in_hold,
                stable,
                sig: sig_before,
                post_step: true,
            };
        }
        if !matches!(action, Action::Idle) {
            sig.reset();
        }
        decided_in_hold = held_before && state.hold_wakeup_ns(now).is_some();
        stable = state.phase_mark() == mark_before && matches!(action, Action::Idle);
        if state.is_idle() || !matches!(action, Action::Idle) {
            return out(state, decided_in_hold, stable, sig);
        }
    }
}

/// Индекс суток: один на ленту `all`, строится от самого раннего окна `first` с его книгой; общий для всех кругов и
/// окон суток (ключ — адрес, длина и метки краёв ленты; неудача построения тоже запоминается). `None` — индекс
/// невозможен (очистка глубины, `local_ts` убывает).
fn idx_for(
    all: &[Event],
    base_row: usize,
    base_depth: &DepthSnapshot,
    tick: f64,
    lot: f64,
) -> Option<std::rc::Rc<HoldIdx>> {
    let key = (
        all.as_ptr() as usize,
        all.len(),
        all.first().map_or(0, |e| e.local_ts),
        all.last().map_or(0, |e| e.local_ts),
        base_row,
    );
    if let Some(i) = IDX_CACHE.with(|c| {
        c.borrow()
            .as_ref()
            .filter(|(k, _)| *k == key)
            .map(|e| e.1.clone())
    }) {
        return i;
    }
    let t_build = std::time::Instant::now();
    let idx = HoldIdx::build(all, base_row, base_depth, tick, lot).map(std::rc::Rc::new);
    IDX_TIMING[0].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    idx_add(1, t_build);
    IDX_CACHE.with(|c| *c.borrow_mut() = Some((key, idx.clone())));
    idx
}

/// Сверка `handoff` индекса с плоской книгой (`ALPHA_HOLD_INDEX_CHECK=1`).
fn check_handoff(
    rows: &[Event],
    cur: usize,
    snap: &DepthSnapshot,
    t: i64,
    h: &FastHandoff,
    tick: f64,
    lot: f64,
) {
    let mut tr = HoldTracker::new(rows, cur, snap.build(tick, lot));
    tr.advance_to(t);
    let w = tr
        .handoff(t, tick, lot)
        .expect("ALPHA_HOLD_INDEX_CHECK: плоская книга не отдала handoff");
    assert!(
        w.local == h.local && w.exch == h.exch && w.tail_start == h.tail_start,
        "ALPHA_HOLD_INDEX_CHECK: handoff индекса расходится с плоской книгой на t={t}, cur={cur}"
    );
    assert_eq!(
        w.middle.len(),
        h.middle.len(),
        "ALPHA_HOLD_INDEX_CHECK: middle, t={t}"
    );
}
