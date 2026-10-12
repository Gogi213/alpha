//! Обработчики фаз: удержание, ожидание выхода и отмен, ожидание входа.

use super::*;

/// Одна функция стратегии (A6, D-СТРАТЕГИЯ). Вызывается один раз на событие
/// потока — какое именно событие произошло, эта функция не спрашивает: она
/// смотрит на `bot.current_timestamp()`/`bot.depth()`/`bot.orders()` в
/// момент вызова, что и есть контракт `Bot<MD>` что в `Backtest`, что в
/// `LiveBot` (`current_timestamp` — время внутри данных или локальные часы
/// соответственно, `ARCHITECTURE.md` A6). Позиция круга — **своя**
/// (`StrategyState::position`, по исполненному в ордерах крейта), а не
/// `bot.position`: крейт обновляет позицию только на `Filled` и частичного
/// исполнения не видит (F3/В-78).
///
/// Не аллоцирует (`ARCHITECTURE.md`, запрет 1): никаких `Vec`/`String` на
/// своём пути, только копируемые `Phase`/`Action` и вызовы `Bot<MD>`.
/// Не вызывает часы напрямую (запрет 2 `interfaces.md`) — время только через
/// `bot.current_timestamp()`. Не делает REST и не строит/подписывает ордер
/// после решения (запреты 4, 5) — `submit_*_order` крейта не имеет отношения
/// к `bybit::sign`, это ответственность `Bot<MD>` (бэктест — симуляция,
/// живой — коннектор фазы 2, вне этого прохода).
/// Позиция открыта: решение выхода (`decide_exit`) и подача (`submit_exit`).
pub(super) fn on_holding<MD, B>(
    bot: &mut B,
    state: &mut StrategyState,
    now: i64,
    entry_ns: i64,
) -> Result<Action, B::Error>
where
    MD: MarketDepth,
    B: Bot<MD>,
{
    let Some((bid_tick, ask_tick)) = best_ticks(bot.depth(state.asset_no)) else {
        // Без книги выйти нельзя — круг остаётся Holding к следующему
        // событию, а не теряется молча.
        return Ok(Action::Idle);
    };
    let Some(entry_side) = entry_side(state.sigma) else {
        return Ok(Action::Idle);
    };
    let exit_side = match entry_side {
        HbtSide::Buy => HbtSide::Sell,
        _ => HbtSide::Buy,
    };
    // Что и в каком порядке решает выход — `decide_exit`; как
    // подаётся заявка — `submit_exit`.
    let quotes = Quotes {
        bid_tick,
        ask_tick,
        step: bot.depth(state.asset_no).tick_size(),
        entry_side,
    };
    // R2 (TK-065): четыре вызова `pyramid_*` на каждом событии удержания при выключенной доливке — только
    // накладные (PGO: +14 % инструкций d15); при `!pyramid_on()` все четыре — пустые (`adds_done == 0`,
    // `eat_parts == 0`, `reinst_trigger` ставится только при `reinstall_n/newwall_k > 0`).
    let pyr = state.pyramid_on();
    if pyr {
        state.pyramid_account(bot);
    }
    match decide_exit(bot, state, entry_ns, now, quotes, true) {
        Some(decision) => {
            if pyr {
                state.pyramid_release(bot)?;
            }
            submit_exit(bot, state, entry_ns, exit_side, decision)
        }
        None => {
            if pyr {
                state.pyramid_step(bot, quotes)?;
                state.pyramid_reinstall_step(bot, quotes)?;
            }
            Ok(Action::Idle)
        }
    }
}

/// Заявка выхода в рынке: учёт исполненного, возврат остатка в план, снятие
/// зависшей лимитки по рыночной причине (аудит 21.09, Б1).
pub(super) fn on_exit_pending<MD, B>(
    bot: &mut B,
    state: &mut StrategyState,
    now: i64,
    order_id: u64,
    entry_ns: i64,
) -> Result<Action, B::Error>
where
    MD: MarketDepth,
    B: Bot<MD>,
{
    state.observe_exit(bot, order_id);
    if state.position() <= 0.0 {
        if state.chase_start_ns != 0
            && state.chase_wait_ns < 0
            && bot
                .orders(state.asset_no)
                .get(&order_id)
                .is_some_and(|o| o.order_type == OrdType::Limit)
        {
            state.chase_wait_ns = now.saturating_sub(state.chase_start_ns);
        }
        state.phase = Phase::Idle;
        return Ok(Action::Idle);
    }
    // Заявка выхода решена (исполнена, снята или отвергнута) и больше
    // не стоит в рынке: остаток позиции ведём по плану дальше —
    // прежними часами круга (дедлайн считается от входа, а не от
    // выхода). Так закрывается и частичный выход E7, и выход, который
    // модель очереди по объёму отдала не целиком (F4, В-78): раньше
    // такой остаток ждал заявку, которой уже нет, до конца записи.
    let (resolved, resting_maker) = match bot.orders(state.asset_no).get(&order_id) {
        Some(o) => {
            let open = matches!(
                o.status,
                Status::None | Status::New | Status::PartiallyFilled
            );
            (
                o.req == Status::None && !open,
                o.req == Status::None && o.order_type == OrdType::Limit && open,
            )
        }
        None => (true, false),
    };
    if resolved {
        state.phase = Phase::Holding { entry_ns };
        return Ok(Action::Idle);
    }
    // Аудит 21.09, Б1: лимитка тейка стоит в рынке (не исполнена или
    // исполнена частично — под моделью очереди по объёму это норма),
    // а остаток позиции при этом **не ведётся**: стоп, дедлайн, трейл
    // и съедание для него не проверялись, и круг ждал до конца записи
    // (`EndOfData`), теряя все дальнейшие сигналы суток. Теперь
    // остаток под стоящей лимиткой решается теми же рыночными
    // причинами: сработала — снимаем лимитку, а после подтверждения
    // отмены остаток закрывается тейкером через `Holding` (условие
    // стопа/дедлайна держится и на следующем событии).
    if resting_maker {
        let (Some((bid_tick, ask_tick)), Some(entry_side)) = (
            best_ticks(bot.depth(state.asset_no)),
            entry_side(state.sigma),
        ) else {
            return Ok(Action::Idle);
        };
        let quotes = Quotes {
            bid_tick,
            ask_tick,
            step: bot.depth(state.asset_no).tick_size(),
            entry_side,
        };
        if let Some(decision) = decide_exit(bot, state, entry_ns, now, quotes, false) {
            // Г-133: погоня переставляет лимитку вслед за ценой (снять, затем `Holding` ставит новую).
            #[allow(clippy::float_cmp)] // цена цели переставлена или нет: копия, не вычисление
            let reprice = !decision.taker
                && decision.reason == ExitReason::Deadline
                && decision.px != state.chase_px;
            if decision.taker || reprice {
                bot.cancel(state.asset_no, order_id, false)?;
                state.phase = Phase::ExitCancelPending {
                    order_id,
                    entry_ns,
                    cancel_sent_ns: now,
                };
            }
        }
    }
    Ok(Action::Idle)
}

/// Отмена лимитки выхода летит: ждём, пока заявка перестанет быть открытой.
/// `cancel_sent_ns` — часы отправки отмены; по ним работает потолок
/// `CANCEL_WAIT_NS` (F8b, В5/Р2). Заявка, чей запрос постановки ещё летел в
/// момент рыночной причины, снимается повторно на следующем шаге — иначе она
/// осталась бы в рынке, а фаза ждала бы её вечно.
pub(super) fn on_exit_cancel_pending<MD, B>(
    bot: &mut B,
    state: &mut StrategyState,
    now: i64,
    order_id: u64,
    entry_ns: i64,
    cancel_sent_ns: i64,
) -> Result<Action, B::Error>
where
    MD: MarketDepth,
    B: Bot<MD>,
{
    state.cancel_open(bot, order_id)?;
    // Отмена лимитки выхода летит; исполнение могло её обогнать
    // (та же гонка, что у входа, 2026-09-18) — считаем исполненное и
    // ждём, пока заявка перестанет быть открытой.
    state.observe_exit(bot, order_id);
    if state.position() <= 0.0 {
        state.phase = Phase::Idle;
        return Ok(Action::Idle);
    }
    let open = match bot.orders(state.asset_no).get(&order_id) {
        Some(o) => {
            o.req != Status::None
                || matches!(
                    o.status,
                    Status::None | Status::New | Status::PartiallyFilled
                )
        }
        None => false,
    };
    if !open {
        state.phase = Phase::Holding { entry_ns };
        return Ok(Action::Idle);
    }
    // F8b (В5/Р2): подтверждение отмены не пришло за потолок — остаток позиции
    // ведёт план (`Holding`), а не ждёт биржу до конца записи. Заявка выхода
    // могла остаться в рынке: предохранитель назван `CancelTimeout`, но
    // короче круга он не притворяется — позиция остаётся под управлением
    // стратегии, а не бросается.
    if now.saturating_sub(cancel_sent_ns) >= CANCEL_WAIT_NS {
        state.exit_cancel_timeouts = state.exit_cancel_timeouts.saturating_add(1);
        state.adopt_orphans(order_id, 1, OrphanKind::Exit, state.exit_accounted);
        state.phase = Phase::Holding { entry_ns };
    }
    Ok(Action::Idle)
}

/// Вход в рынке: накопление позиции по ногам, срок жизни и причины снятия (F4/F5).
pub(super) fn on_entry_pending<MD, B>(
    bot: &mut B,
    state: &mut StrategyState,
    now: i64,
    order_id: u64,
    sent_ns: i64,
    legs: u8,
) -> Result<Action, B::Error>
where
    MD: MarketDepth,
    B: Bot<MD>,
{
    // F4 (В-78): вход копит позицию. Первая исполнившаяся нога
    // остальные **не снимает** — лестница набирает объём, пока жив
    // вход; об исполненном и средней цене судим по ордерам крейта,
    // потому что `bot.position` частичного исполнения не видит.
    let snap = state.observe_entry(bot, order_id, legs);
    if !snap.open {
        // Вход решён: ни одна нога не стоит и запросов в пути нет —
        // всё, что могло исполниться, исполнено; остальное снято или
        // отвергнуто биржей.
        if state.position() > 0.0 {
            state.enter_holding(now);
            return Ok(Action::Idle);
        }
        // Позиции нет: либо вход истёк, либо ни одной ноги не
        // поставила биржа (пост-онли заявка пересекла спред, В-72) —
        // «сигнал без входа». Круг свободен сразу, а не висит
        // «занятым» до конца срока жизни входа.
        state.phase = Phase::Idle;
        return Ok(Action::EntryTimedOut {
            order_id,
            reason: EntryCancelReason::NotPlaced,
        });
    }
    // F5 (В-74): вход живёт, пока стена жива и цена в полосе, — это
    // события рынка, а не таймер. Проверяются **раньше** потолка:
    // если условие и потолок совпали на одном кадре, честнее назвать
    // событие, которое и было поводом снять вход.
    if let Some(reason) = state.entry_cancel_reason(bot) {
        state.cancel_resting(bot, order_id, legs)?;
        state.phase = Phase::CancelPending {
            order_id,
            legs,
            reason,
            cancel_sent_ns: now,
        };
        return Ok(Action::Idle);
    }
    if now.saturating_sub(sent_ns) >= state.plan.entry_ttl_ns() {
        state.cancel_resting(bot, order_id, legs)?;
        state.phase = Phase::CancelPending {
            order_id,
            legs,
            reason: EntryCancelReason::Ttl,
            cancel_sent_ns: now,
        };
        return Ok(Action::Idle);
    }
    Ok(Action::Idle)
}

/// Отмена входа летит: гонка с исполнением (2026-09-18) и переход в `Holding`/`Idle`.
/// `cancel_sent_ns` — часы отправки отмены, по ним работает потолок
/// `CANCEL_WAIT_NS` (F8b, В5/Р2); снятие повторяется на каждом шаге, пока
/// нога, чей запрос постановки был в полёте, не станет снимаемой.
pub(super) fn on_cancel_pending<MD, B>(
    bot: &mut B,
    state: &mut StrategyState,
    now: i64,
    order_id: u64,
    legs: u8,
    reason: EntryCancelReason,
    cancel_sent_ns: i64,
) -> Result<Action, B::Error>
where
    MD: MarketDepth,
    B: Bot<MD>,
{
    // F8b (В5/Р2): повтор снятия. Нога, чей запрос постановки летел в момент
    // снятия входа, тогда пропущена (`cancel_resting` снимает только `New`/
    // `PartiallyFilled` без запроса в полёте) — без повтора она осталась бы в
    // рынке, а фаза «отмена летит» ждала бы её вечно.
    state.cancel_resting(bot, order_id, legs)?;
    // Исполнение обогнало отмену — позиция есть, ведём её по плану.
    // Пока хоть одна нога ещё стоит или снимается, вход не решён:
    // позицию добираем (F4, В-78), а не бросаем на половине.
    let snap = state.observe_entry(bot, order_id, legs);
    if !snap.open {
        if state.position() > 0.0 {
            state.enter_holding(now);
            return Ok(Action::Idle);
        }
        state.phase = Phase::Idle;
        return Ok(Action::EntryTimedOut { order_id, reason });
    }
    // Потолок ожидания: подтверждение отмены не пришло — круг освобождается
    // под тем же правилом, что и при подтверждённой отмене (позиция есть →
    // ведём её, нет → причина называется). Заявка могла остаться в рынке:
    // это предохранитель, и он назван `CancelTimeout`.
    if now.saturating_sub(cancel_sent_ns) >= CANCEL_WAIT_NS {
        // Ноги остаются сиротами (F8c, К1): снятие повторится на каждом
        // событии, исполнение погасится по рынку и будет посчитано.
        // `observe_entry` выше — по тем же ногам: `entry_qty` и есть их
        // исполненное, уже ставшее позицией плана (B1).
        state.adopt_orphans(order_id, legs, OrphanKind::Entry, state.entry_qty);
        if state.position() > 0.0 {
            state.enter_holding(now);
            return Ok(Action::Idle);
        }
        state.phase = Phase::Idle;
        return Ok(Action::EntryTimedOut {
            order_id,
            reason: EntryCancelReason::CancelTimeout,
        });
    }
    Ok(Action::Idle)
}
