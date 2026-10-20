//! Тесты стратегии — F4: частичная позиция, пост-онли вход, своя позиция.

use super::*;

// -----------------------------------------------------------------------
// F4 (план 2026-09-20, В-78): частичная позиция, пост-онли вход и своя
// позиция вместо `Bot::position` крейта.
// -----------------------------------------------------------------------

/// План F4: две ноги лестницы (`step` тиков между ними), стоп/тейк формы от
/// планового входа 100, вход живёт `ttl`, пост-онли — параметром.
pub(super) fn f4_plan(
    stop_px: f64,
    take_px: f64,
    post_only: bool,
    ttl_ns: i64,
    step: f64,
) -> TradePlan {
    TradePlan::Bounce {
        entry_px: 100.0,
        stop_px,
        take_px,
        deadline_ns: 20 * S,
        entry_ttl_ns: ttl_ns,
        post_only,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 2,
        grid_step_px: step,
        // F4-лестница `grid_legs × grid_step_px` — прежний вход; лестница
        // формы F6 проверяется отдельным тестом (`EntryLadder`).
        ladder: EntryLadder::NONE,
        early_exit_ns: 0,
        level_floor_qty: 0.0,
        band_exit_bps: 0.0,
        level_px: 99.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 0.0,
        eaten_all_pct: 0.0,
        eaten_half_frac: 0.0,
        level_qty: 0.0,
        lot_qty: 0.1,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        wall_eat: crate::lob::strategy::WallEatExit::OFF,
        pyramid: crate::lob::strategy::PyramidCfg::OFF,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_stop: crate::lob::strategy::GoneStop::Off,
    }
}

/// Бэктест с моделью очереди по объёму (`PartialFillExchange`): только она
/// отдаёт частичное исполнение, ради которого и заведена частичная позиция.
pub(super) fn prob_backtest(feed: &[Event]) -> Backtest<FastMarketDepth> {
    build_backtest(
        feed,
        1.0,
        0.1,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::Prob { n: 3.0 },
    )
}

/// Форк крейта, вторая правка `PartialFillExchange` (F10-fix, 21.09): заявка
/// **не по лоту** (1.04 при лоте 0.1) исполняется сделкой на 1.0 — остаток
/// 0.04 меньше половины лота, крейт ставит `Filled`, но прежнее условие
/// удаления (`filled_qty >= leaves_qty`) ногу в карте биржи оставляло, и
/// следующая сделка по той же цене роняла `elapse` с `InvalidOrderStatus`
/// (прогон F10 на счётной, `a45-bid` D20, «форма #2»). Теперь удаление — по
/// статусу после `fill`: вторая сделка проходит, позиция — исполненное 1.0.
#[test]
pub(super) fn a_sub_lot_remainder_does_not_leave_a_stale_filled_order_on_the_exchange() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        // Продажа 6 в бид 100: очередь 5 съедена, нам исполняется ровно 1.0
        // (лотами), остаток 0.04 — статус `Filled`.
        trade_at(2 * S, true, 100.0, 6.0),
        // Вторая продажа по той же цене — раньше здесь падал весь прогон.
        trade_at(3 * S, true, 100.0, 6.0),
        depth_at(5 * S, true, 100.0, 5.0),
        depth_at(6 * S, false, 101.0, 5.0),
    ];
    let mut hbt = prob_backtest(&feed);
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 1.04, 1, cancel_wait_plan(30 * S));

    let actions = drive(&mut hbt, &mut state);

    assert!(
        actions
            .iter()
            .any(|a| matches!(a, Action::EntrySubmitted { .. })),
        "{actions:?}"
    );
    assert!(
        (state.position() - 1.0).abs() < 1e-9,
        "исполнено ровно 1.0 лотами, остаток 0.04 не исполняем: {}",
        state.position()
    );
}

/// Тот же дефект форка, путь (б) — заявка **по лоту** (0.3 = три лота по 0.1),
/// но `0.1 + 0.1 + 0.1 ≠ 0.3` в плавающей точке: после двух исполнений по лоту
/// остаток `0.10000000000000003`, третье исполнение `0.1` не проходит прежнее
/// сравнение `filled_qty >= leaves_qty` на `3e-17`, статус уже `Filled` — нога
/// оставалась в карте биржи, четвёртая сделка роняла прогон. Это и есть путь,
/// на котором упал F10 на живых сутках при лотах от пула.
#[test]
pub(super) fn a_lot_aligned_order_with_a_float_remainder_is_removed_when_filled() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        // Очередь 5 съедена и исполнен первый лот; дальше по лоту за сделку.
        trade_at(2 * S, true, 100.0, 5.1),
        trade_at(3 * S, true, 100.0, 0.1),
        trade_at(4 * S, true, 100.0, 0.1),
        // Заявка `Filled` с остатком 3e-17 — раньше здесь падал `elapse`.
        trade_at(5 * S, true, 100.0, 0.1),
        depth_at(6 * S, true, 100.0, 5.0),
        depth_at(7 * S, false, 101.0, 5.0),
    ];
    let mut hbt = prob_backtest(&feed);
    // Шаг лота плана — тот же, что у биржи модели (`prob_backtest`, 0.1): порог
    // пыли позиции (R4) — половина шага плана, и при плане с лотом 1.0 позиция
    // 0.3 читалась бы пылью.
    let mut plan = cancel_wait_plan(30 * S);
    if let TradePlan::Bounce { lot_qty, .. } = &mut plan {
        *lot_qty = 0.1;
    }
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 0.3, 1, plan);

    let _ = drive(&mut hbt, &mut state);

    assert!(
        (state.position() - 0.3).abs() < 1e-9,
        "три лота исполнены: {}",
        state.position()
    );
}

/// F4 (В-78): исполнилась **половина одной ноги** из двух — позиция равна
/// этой половине (0.5 при ноге 1.0), и выход идёт на неё: заявка выхода несёт
/// 0.5, а стоп сдвинут к средней исполненного (101 против плановой 100 →
/// стоп 97 при плановом 96, и бид 97 его и выбивает).
#[test]
pub(super) fn a_partial_leg_sets_the_position_and_the_exit_is_sized_on_it() {
    let feed = [
        // Книга стоит ниже лестницы: у ног нет чужой очереди впереди, и вход
        // решает объём сделок, а не глубина стакана.
        depth_at(0, true, 98.0, 5.0),
        depth_at(0, false, 110.0, 5.0),
        // Продажа 0.5 ровно в дальнюю ногу (101): очередь впереди пуста,
        // нога исполняется наполовину; ближняя (100) не тронута.
        trade_at(2 * S, true, 101.0, 0.5),
        // Вход живёт до срока (5 с), поэтому круг выходит в `Holding` только
        // после снятия входа, а стоп формы 96 сдвинут средней (101) на +1.
        depth_at(10 * S, true, 98.0, 0.0),
        depth_at(10 * S, true, 97.0, 5.0),
        depth_at(10 * S, false, 110.0, 5.0),
        // Хвост: ответу на выход нужно событие после срабатывания.
        depth_at(12 * S, false, 110.0, 5.0),
    ];
    let mut hbt = prob_backtest(&feed);
    let mut state = StrategyState::with_plan(
        0,
        SIGMA_LONG,
        2.0,
        1,
        f4_plan(96.0, 104.0, false, 5 * S, 1.0),
    );

    let actions = drive(&mut hbt, &mut state);

    let (order_id, price, reason) = actions
        .iter()
        .find_map(|a| match a {
            Action::ExitSubmitted {
                order_id,
                price,
                reason,
                ..
            } => Some((*order_id, *price, *reason)),
            _ => None,
        })
        .expect("позиция из половины ноги обязана закрыться");
    assert_eq!(reason, ExitReason::Stop, "{actions:?}");
    assert!(
        close(price, 97.0),
        "стоп — от средней исполненного (101 = 100 + 1 тик): {price}"
    );
    let exit = hbt.orders(0).get(&order_id).expect("заявка выхода в учёте");
    assert!(
        close(exit.qty, 0.5),
        "размер выхода — своя позиция (половина ноги 1.0): {}",
        exit.qty
    );
    assert_eq!(exit.status, Status::Filled, "выход исполнился целиком");
}

/// Г-114 `halfstop`: стоп закрывает ровно половину позиции (0.5 → 0.25, шаг лота 0), защёлка взведена,
/// заявка выхода помечена частичной; без флага тот же фид закрывает всё (см. соседний тест F4).
#[test]
#[cfg(feature = "r2")]
pub(super) fn halfstop_closes_half_of_the_position_on_the_stop_and_latches() {
    let feed = [
        depth_at(0, true, 98.0, 5.0),
        depth_at(0, false, 110.0, 5.0),
        trade_at(2 * S, true, 101.0, 0.5),
        depth_at(10 * S, true, 98.0, 0.0),
        depth_at(10 * S, true, 97.0, 5.0),
        depth_at(10 * S, false, 110.0, 5.0),
        depth_at(12 * S, false, 110.0, 5.0),
    ];
    let mut hbt = prob_backtest(&feed);
    let mut plan = f4_plan(96.0, 104.0, false, 5 * S, 1.0);
    if let TradePlan::Bounce {
        pyramid, lot_qty, ..
    } = &mut plan
    {
        pyramid.half_stop = true;
        *lot_qty = 0.0;
    }
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 2.0, 1, plan);
    let actions = drive(&mut hbt, &mut state);
    let (order_id, reason, partial) = actions
        .iter()
        .find_map(|a| match a {
            Action::ExitSubmitted {
                order_id,
                reason,
                partial,
                ..
            } => Some((*order_id, *reason, *partial)),
            _ => None,
        })
        .expect("стоп обязан сработать");
    assert_eq!(reason, ExitReason::Stop, "{actions:?}");
    assert!(partial, "выход частичный");
    assert!(state.stop_half_done, "защёлка взведена");
    let exit = hbt.orders(0).get(&order_id).expect("заявка выхода в учёте");
    assert!(close(exit.qty, 0.25), "половина от 0.5: {}", exit.qty);
}

/// Г-114 `halflevel`: первая сделка ленты ниже `level_px` (99) после входа закрывает половину по рынку
/// (0.5 → 0.25), защёлка взведена; без такой сделки половины нет.
#[cfg(feature = "r2")]
pub(super) fn halflevel_exit(trade_px: Option<f64>) -> Option<(bool, f64, bool)> {
    let mut feed = vec![
        depth_at(0, true, 98.0, 5.0),
        depth_at(0, false, 110.0, 5.0),
        trade_at(2 * S, true, 101.0, 0.5),
    ];
    if let Some(px) = trade_px {
        feed.push(trade_at(8 * S, true, px, 0.1));
    }
    feed.push(depth_at(12 * S, false, 110.0, 5.0));
    feed.push(depth_at(14 * S, false, 110.0, 5.0));
    let mut hbt = prob_backtest(&feed);
    let mut plan = f4_plan(90.0, 120.0, false, 5 * S, 1.0);
    if let TradePlan::Bounce {
        pyramid, lot_qty, ..
    } = &mut plan
    {
        pyramid.half_level = true;
        *lot_qty = 0.0;
    }
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 2.0, 1, plan);
    let mut actions = Vec::new();
    loop {
        let r = hbt.elapse(100_000_000).unwrap();
        state.observe_wall_trades(hbt.last_trades(0));
        hbt.clear_last_trades(Some(0));
        actions.push(on_event(&mut hbt, &mut state).unwrap());
        if r == ElapseResult::EndOfData {
            break;
        }
    }
    let (order_id, reason, partial) = actions.iter().find_map(|a| match a {
        Action::ExitSubmitted {
            order_id,
            reason,
            partial,
            ..
        } => Some((*order_id, *reason, *partial)),
        _ => None,
    })?;
    assert_eq!(reason, ExitReason::Stop);
    let qty = hbt.orders(0).get(&order_id).expect("заявка выхода").qty;
    Some((partial, qty, state.stop_half_done))
}

#[test]
#[cfg(feature = "r2")]
pub(super) fn halflevel_closes_half_on_the_first_trade_below_the_level() {
    let (partial, qty, latched) = halflevel_exit(Some(98.5)).expect("половина обязана выйти");
    assert!(partial && latched, "частичный выход и защёлка");
    assert!(close(qty, 0.25), "половина от 0.5: {qty}");
    assert!(
        halflevel_exit(Some(99.0)).is_none(),
        "сделка на цене уровня — не ниже"
    );
    assert!(
        halflevel_exit(None).is_none(),
        "нет сделки за уровнем — нет выхода"
    );
}

/// Г-117 `tslx` (точки F1 П-14): γ = G/8, T = Q/16 — те же цены, что у `tsl` при совпадающих γ и T; прежние поля не тронуты.
#[test]
pub(super) fn scheduled_take_x_matches_the_old_encoding_on_shared_points() {
    let mut old = PyramidCfg::OFF;
    old.sched_g10 = 10;
    old.sched_t4 = 2;
    let mut new = PyramidCfg::OFF;
    new.sched_g8 = 8;
    new.sched_t16 = 8;
    let (entry, take, tick, dl) = (100.0, 101.0, 0.01, 1000 * S);
    for t in [0, 100 * S, 250 * S, 500 * S, 900 * S] {
        for side in [HbtSide::Buy, HbtSide::Sell] {
            assert_eq!(
                scheduled_take(side, entry, take, tick, old, t, dl),
                scheduled_take(side, entry, take, tick, new, t, dl),
                "t = {t}"
            );
        }
    }
}

/// Г-117 `tsl`: тейк лонга ползёт от `take_px` к безубытку (вход + круг комиссий), до `T` — степенью `γ`, после `T` — пол;
/// шорт зеркально; тейк ниже пола — без изменений.
#[test]
pub(super) fn scheduled_take_slides_to_breakeven_and_mirrors_for_shorts() {
    let mut cfg = PyramidCfg::OFF;
    cfg.sched_g10 = 10;
    cfg.sched_t4 = 2;
    let fees = crate::lob::costs::ROUNDTRIP_FEES_BPS / 10_000.0;
    let (entry, take, tick, dl) = (100.0, 101.0, 0.01, 1000 * S);
    let at = |side, t| scheduled_take(side, entry, take, tick, cfg, t, dl);
    assert!(close(at(HbtSide::Buy, 0), 101.0), "t = 0 — исходный тейк");
    let floor = entry * (1.0 + fees);
    assert!(
        (at(HbtSide::Buy, 500 * S) - floor).abs() <= tick,
        "t = T — пол"
    );
    assert!(
        (at(HbtSide::Buy, 900 * S) - floor).abs() <= tick,
        "после T — пол"
    );
    let mid = at(HbtSide::Buy, 250 * S);
    assert!(
        mid > floor && mid < 101.0 && mid >= (101.0 + floor) / 2.0 - tick,
        "середина: {mid}"
    );
    let short = scheduled_take(HbtSide::Sell, entry, 99.0, tick, cfg, 500 * S, dl);
    assert!(
        (short - entry * (1.0 - fees)).abs() <= tick,
        "шорт зеркально: {short}"
    );
    assert!(
        close(
            scheduled_take(HbtSide::Buy, entry, 100.0, tick, cfg, 0, dl),
            100.0
        ),
        "тейк не выше пола"
    );
}

#[cfg(feature = "r2")]
pub(super) fn converge_exit_reason(a_bps: u32) -> Option<ExitReason> {
    let feed = [
        depth_at(0, true, 98.0, 5.0),
        depth_at(0, false, 110.0, 5.0),
        trade_at(2 * S, true, 101.0, 0.5),
        depth_at(8 * S, true, 100.0, 5.0),
        depth_at(10 * S, true, 100.0, 0.0),
        depth_at(10 * S, true, 99.0, 5.0),
        depth_at(12 * S, false, 110.0, 5.0),
        depth_at(25 * S, false, 110.0, 5.0),
    ];
    let mut hbt = prob_backtest(&feed);
    let mut plan = f4_plan(90.0, 120.0, false, 5 * S, 1.0);
    if let TradePlan::Bounce { pyramid, .. } = &mut plan {
        pyramid.converge_tol1 = 1;
        pyramid.converge_a_bps = a_bps;
    }
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 2.0, 1, plan);
    drive(&mut hbt, &mut state).iter().find_map(|a| match a {
        Action::ExitSubmitted { reason, .. } => Some(*reason),
        _ => None,
    })
}

#[test]
#[cfg(feature = "r2")]
pub(super) fn converge_exits_after_the_price_left_the_wall_by_a_and_came_back() {
    assert_eq!(converge_exit_reason(50), Some(ExitReason::Converge));
    assert_ne!(
        converge_exit_reason(500),
        Some(ExitReason::Converge),
        "Dmax < A — правило не активно"
    );
}

#[cfg(feature = "r2")]
fn levelback_exit_reason(gap_bps: u32, secs: u32) -> Option<ExitReason> {
    let feed = [
        depth_at(0, true, 98.0, 5.0),
        depth_at(0, false, 110.0, 5.0),
        trade_at(2 * S, true, 101.0, 0.5),
        depth_at(8 * S, true, 100.0, 5.0),
        depth_at(10 * S, true, 100.0, 0.0),
        depth_at(10 * S, true, 99.0, 5.0),
        depth_at(12 * S, false, 110.0, 5.0),
        depth_at(25 * S, false, 110.0, 5.0),
    ];
    let mut hbt = prob_backtest(&feed);
    let mut plan = f4_plan(90.0, 120.0, false, 5 * S, 1.0);
    if let TradePlan::Bounce { pyramid, .. } = &mut plan {
        pyramid.levelback_gap_bps = gap_bps;
        pyramid.levelback_secs = secs;
    }
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 2.0, 1, plan);
    drive(&mut hbt, &mut state).iter().find_map(|a| match a {
        Action::ExitSubmitted { reason, .. } => Some(*reason),
        _ => None,
    })
}

#[test]
#[cfg(feature = "r2")]
pub(super) fn levelback_exits_when_the_price_went_under_the_level_by_the_gap_within_the_window() {
    assert_eq!(levelback_exit_reason(50, 60), Some(ExitReason::LevelBack));
    assert_ne!(
        levelback_exit_reason(500, 60),
        Some(ExitReason::LevelBack),
        "зазор больше отката — правило не активно"
    );
    assert_ne!(
        levelback_exit_reason(50, 1),
        Some(ExitReason::LevelBack),
        "откат позже окна — правило не активно"
    );
}

/// F4 (В-78): вторая нога исполняется позже, но **до** снятия входа —
/// позиция набирается целиком (1.0 + 1.0), средняя пересчитывается (101 по
/// двум ногам 100 и 102), и выход идёт на всю позицию: заявка выхода несёт
/// 2.0, стоп сдвинут средней на тик вверх (97 при плановом 96).
///
/// Сделки подобраны так, чтобы не задеть особенность `PartialFillExchange`
/// крейта 0.9.4 (замер F4): нога, **исполненная ровно до нуля** сделкой по
/// своей цене (`filled_qty == leaves_qty`), остаётся в карте биржи (в
/// `filled_orders` её не кладут — там условие `>`), и следующая сделка по той
/// же или меньшей цене пытается исполнить её второй раз — `InvalidOrderStatus`
/// роняет весь прогон. Отсюда сделка `1.5` в дальнюю ногу (больше ноги) и
/// полное исполнение ближней последней по времени.
#[test]
pub(super) fn the_second_leg_recomputes_the_average_and_the_exit_covers_the_whole_position() {
    let feed = [
        depth_at(0, true, 98.0, 5.0),
        depth_at(0, false, 110.0, 5.0),
        // Дальняя нога (102) исполняется целиком — ближняя ещё стоит.
        trade_at(2 * S, true, 102.0, 1.5),
        // Вторая продажа по 100 закрывает ближнюю ногу: позиция 2.0, средняя
        // (100 + 102) / 2 = 101 — вход решён, круг уходит в `Holding`.
        trade_at(4 * S, true, 100.0, 1.0),
        depth_at(10 * S, true, 98.0, 0.0),
        depth_at(10 * S, true, 97.0, 5.0),
        depth_at(10 * S, false, 110.0, 5.0),
        depth_at(12 * S, false, 110.0, 5.0),
    ];
    let mut hbt = prob_backtest(&feed);
    let mut state = StrategyState::with_plan(
        0,
        SIGMA_LONG,
        2.0,
        1,
        f4_plan(96.0, 104.0, false, 30 * S, 2.0),
    );

    let actions = drive(&mut hbt, &mut state);
    let (order_id, price, reason) = actions
        .iter()
        .find_map(|a| match a {
            Action::ExitSubmitted {
                order_id,
                price,
                reason,
                ..
            } => Some((*order_id, *price, *reason)),
            _ => None,
        })
        .expect("набранная позиция обязана закрыться");
    assert_eq!(reason, ExitReason::Stop, "{actions:?}");
    assert!(
        close(price, 97.0),
        "стоп от средней исполненного (101 против плановой 100): {price}"
    );
    let exit = hbt.orders(0).get(&order_id).expect("заявка выхода в учёте");
    assert!(
        close(exit.qty, 2.0),
        "выход на **всю** набранную позицию (1.0 + 1.0): {}",
        exit.qty
    );
    assert_eq!(exit.status, Status::Filled);
}

/// F4/В-72: вход пост-онли, цена пересекает спред — **ни одной** ноги биржа
/// не поставила (`Expired` при `GTX`), круга нет, и это «сигнал без входа»:
/// `EntryTimeout`, а не «занято». Круг освобождается сразу (второй сигнал
/// через 2 с проходит как сигнал, а не как занятый), а счётчик ног, не
/// поставленных биржей, растёт по ногам (2 + 2 = 4).
#[test]
pub(super) fn a_post_only_entry_that_crosses_the_spread_is_not_placed_and_is_not_busy() {
    let feed = [
        // Спред один тик: цена входа (100) равна лучшему аску — пост-онли
        // такую заявку отвергает.
        depth_at(0, true, 99.0, 5.0),
        depth_at(0, false, 100.0, 5.0),
        depth_at(10 * S, false, 100.0, 5.0),
    ];
    let mut hbt = prob_backtest(&feed);
    let cfg = DriveConfig {
        tape_log_secs: 0,
        tape_log_ws: crate::lob::backtest::NO_TAPE_WS,
        order_qty: 1.0,
        first_order_id: 1,
        queue_model: QueueModelKind::Prob { n: 3.0 },
        busy_skip: true,
        hold_skip: false,
    };
    let signal = |t0_ns: i64| BounceSignal {
        touch_index: 0,
        t0_ns,
        sigma: SIGMA_LONG,
        plan: f4_plan(98.0, 102.0, true, 20 * S, 1.0),
        profile: 0,
        qty: None,
    };
    let run = drive_bounce(&mut hbt, 0, &[signal(S), signal(3 * S)], &cfg).unwrap();

    assert!(run.fills.is_empty(), "ни одной сделки: {run:?}");
    assert_eq!(run.rejected_postonly, 4, "две ноги × два сигнала");
    assert_eq!(run.misses.timeout, 2, "оба сигнала — без входа");
    assert_eq!(run.misses.busy, 0, "«занято» тут нет");
    assert!(
        run.busy_signal.is_empty(),
        "сигнал без входа круг не занимает: {:?}",
        run.busy_signal
    );
    assert_eq!(
        run.submitted_signal.len(),
        2,
        "оба сигнала поставлены и оба же отвергнуты"
    );
    assert_eq!(run.entry_rejected, 0, "GTX даёт `Expired`, а не `Rejected`");
    assert!(!run.incomplete, "круг не открывался — расписывать нечего");
}
