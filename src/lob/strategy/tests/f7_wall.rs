//! Тесты стратегии — F7/R4: выход по стене, пыль позиции.

use super::*;

// -----------------------------------------------------------------------
// F7 (план 2026-09-20, Б-75): выход «съели» (`eat<X>`) и «сняли» (`gone<W>`).
// Сделки в стену видит драйвер (`run_round`) и отдаёт стратегии
// (`observe_wall_trades`) — буфер `bot.last_trades` под его управлением,
// поэтому тесты идут полным драйвером (`drive_bounce`), а не голым `on_event`.
// -----------------------------------------------------------------------

/// План F7: вход 100 у бид-стены 99 размером `level_qty`, потолок входа 2 с,
/// формы выхода — `eat<eat_pct>` и/или `gone<gone_pct>` (нули выключают).
pub(super) fn f7_plan(eat_pct: f64, gone_pct: f64, level_qty: f64) -> TradePlan {
    TradePlan::Bounce {
        entry_px: 100.0,
        // Стоп далеко: тест про формы выхода, а не про стоп.
        stop_px: 90.0,
        take_px: 130.0,
        deadline_ns: 30 * S,
        entry_ttl_ns: 2 * S,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_px: 0.0,
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
        level_qty,
        lot_qty: 1.0,
        exit_eat_pct: eat_pct,
        wall_eat: crate::lob::strategy::WallEatExit::OFF,
        pyramid: crate::lob::strategy::PyramidCfg::OFF,
        exit_gone_pct: gone_pct,
        gone_trail_bps: 0.0,
        gone_stop: crate::lob::strategy::GoneStop::Off,
    }
}

// -----------------------------------------------------------------------
// R4 (ревью 23.09): пыль позиции — сумма f64 частичных исполнений
// (`entry_qty − exit_qty`) сходится не в ровный ноль, а в остаток порядка
// шага округления; сравнение с нулём точно читало его как «позиция
// открыта».
// -----------------------------------------------------------------------

/// `position()` — единое место допуска: остаток меньше половины шага лота
/// плана (здесь `lot_qty = 1.0` у `f7_plan`, половина — 0.5) читается как
/// отсутствие позиции, не как пыль.
#[test]
pub(super) fn position_rounds_dust_below_half_lot_step_down_to_flat() {
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 1.0, 1, f7_plan(0.0, 0.0, 100.0));
    state.entry_qty = 1.0;
    // Остаток 1e-12 лота — то же порядок величины, что и накопленная ошибка
    // f64 у суммы частичных исполнений (ноги лестницы, добор F4).
    state.exit_qty = 1.0 - 1e-12;
    assert_eq!(
        state.position(),
        0.0,
        "остаток 1e-12 лота меньше половины шага лота — позиция закрыта"
    );
}

/// Остаток настоящего лота (не пыль) остаётся видимым — допуск не глотает
/// позицию целиком.
#[test]
pub(super) fn position_keeps_a_real_remainder_above_half_lot_step() {
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 1.0, 1, f7_plan(0.0, 0.0, 100.0));
    state.entry_qty = 1.0;
    state.exit_qty = 0.3;
    assert!(
        (state.position() - 0.7).abs() < 1e-9,
        "0.7 лота — настоящий остаток, не пыль: {}",
        state.position()
    );
}

/// Заявка выхода на пыль (R4): круг с остатком 1e-12 лота после снятия
/// лимитки выхода уходит в `Idle` без новой заявки — `on_exit_pending`
/// раньше видел бы `position() > 0.0` и держал круг в `ExitPending` вечно,
/// повторяя заявку на пыль на каждом событии (биржа отклонила бы её по
/// `minQty`). Заявки в `bot` для `order_id` нет нарочно: `observe_exit` не
/// находит её и не меняет `exit_qty`, оставляя ровно проверяемый остаток.
#[test]
pub(super) fn on_exit_pending_with_dust_left_goes_idle_without_a_new_exit_order() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
    ];
    let mut hbt = prob_backtest(&feed);
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 1.0, 1, f7_plan(0.0, 0.0, 100.0));
    state.entry_qty = 1.0;
    state.exit_qty = 1.0 - 1e-12;
    state.phase = Phase::ExitPending {
        order_id: 999,
        entry_ns: 0,
    };
    let action = on_exit_pending(&mut hbt, &mut state, 0, 999, 0).unwrap();
    assert!(
        matches!(action, Action::Idle),
        "пыль — не позиция, новой заявки выхода нет: {action:?}"
    );
    assert!(
        matches!(state.phase, Phase::Idle),
        "круг освобождён, не завис в ExitPending: {:?}",
        state.phase
    );
}

/// Прогон одного сигнала полным драйвером: круг закрывается страховкой на
/// конце фида, поэтому причина выхода — последняя в `fill_reason`.
///
/// Бэктест — `build_backtest` (не `seam6_backtest`): только он ставит
/// `last_trades_capacity`, без которого крейт вовсе не пишет ленту
/// (`proc/local.rs`: `trades.capacity() > 0`), и F7 нечего было бы считать.
pub(super) fn f7_exits(plan: TradePlan, feed: &[Event]) -> Vec<ExitReason> {
    f7_run(plan, feed).fill_reason
}

/// Тот же прогон, но наружу отдаётся весь `BounceRun`: агрегаты причин
/// выхода (`ExitTally`) считает драйвер, и F8b проверяет их отдельно от
/// `fill_reason` кругов.
pub(super) fn f7_run(plan: TradePlan, feed: &[Event]) -> crate::lob::backtest::BounceRun {
    let mut hbt = build_backtest(
        feed,
        1.0,
        1.0,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let cfg = DriveConfig {
        tape_log_secs: 0,
        order_qty: 1.0,
        first_order_id: 1,
        queue_model: QueueModelKind::RiskAdverse,
        busy_skip: true,
        hold_skip: false,
    };
    let signal = BounceSignal {
        touch_index: 0,
        t0_ns: S,
        sigma: SIGMA_LONG,
        plan,
        profile: 0,
        qty: None,
    };
    drive_bounce(&mut hbt, 0, &[signal], &cfg).unwrap()
}

/// Шапка фида: книга с бид-стеной 99 (размер `wall`) и вход в 100, который
/// исполняет продажа 6 в бид 100 (очередь впереди 5 — как в шве 6). Сделка
/// стоит **после** `t0` сигнала (1 с): до постановки входа лента к кругу не
/// относится. Потолок входа 2 с снимает остаток к 3 с, и позиция уходит в
/// `Holding` — дальше фид продолжается тем, что передал тест.
pub(super) fn f7_feed_tail(rest: &[Event], wall: f64) -> Vec<Event> {
    let mut feed = vec![
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, true, 99.0, wall),
        depth_at(0, false, 101.0, 5.0),
        // Вход (лимит 100) исполняется: продажа 6 съедает очередь 5 и берёт нас.
        trade_at(S + S / 2, true, 100.0, 6.0),
        // Потолок входа (2 с) истекает — позиция 1.0 уходит в `Holding`.
        depth_at(3 * S, true, 100.0, 0.0),
        depth_at(3 * S, true, 99.0, wall),
    ];
    feed.extend_from_slice(rest);
    feed
}

/// F7 (Б-75): печать в стену `≥ X %` размера стены — выход по рынку
/// (`EatenByTrades`), а не ожидание дедлайна. Одна сделка 6 из стены 10 при
/// `eat50`.
#[test]
pub(super) fn a_print_into_the_wall_exits_by_trades() {
    let feed = f7_feed_tail(
        &[
            // Печать в стену 99: 6 из 10 = 60 % ≥ 50 %.
            trade_at(4 * S, true, 99.0, 6.0),
            // Книга для рыночного выхода и хвост, чтобы ответ дошёл.
            depth_at(5 * S, false, 101.0, 5.0),
            depth_at(6 * S, false, 102.0, 5.0),
        ],
        10.0,
    );
    assert_eq!(
        f7_exits(f7_plan(50.0, 0.0, 10.0), &feed),
        vec![ExitReason::EatenByTrades],
        "печать в стену обязана закрыть позицию по рынку"
    );
}

/// F7 (Б-75): медленное съедание копится — две сделки по 3 из стены 10 дают
/// те же 60 % и тот же выход. Это проверка накопления, а не одного принта.
#[test]
pub(super) fn slow_eating_accumulates_to_the_eat_threshold() {
    let feed = f7_feed_tail(
        &[
            trade_at(4 * S, true, 99.0, 3.0),
            depth_at(4 * S + 500_000_000, false, 101.0, 5.0),
            trade_at(5 * S, true, 99.0, 3.0),
            depth_at(6 * S, false, 101.0, 5.0),
            depth_at(7 * S, false, 102.0, 5.0),
        ],
        10.0,
    );
    assert_eq!(
        f7_exits(f7_plan(50.0, 0.0, 10.0), &feed),
        vec![ExitReason::EatenByTrades],
        "3 + 3 = 60 % — порог накоплен, как одной печатью"
    );
}

/// F7 (Б-75): стена **снята** без сделок — размер 10 → 3 при `gone50` (ниже
/// половины) и съеденном нуле: выход `WallGone`.
#[test]
pub(super) fn a_wall_that_shrinks_without_trades_exits_as_gone() {
    let feed = f7_feed_tail(
        &[
            // Размер стены убрали: 3 < 5 (половина от 10), сделок не было.
            depth_at(4 * S, true, 99.0, 3.0),
            depth_at(5 * S, false, 101.0, 5.0),
            depth_at(6 * S, false, 102.0, 5.0),
        ],
        10.0,
    );
    assert_eq!(
        f7_exits(f7_plan(0.0, 50.0, 10.0), &feed),
        vec![ExitReason::WallGone],
        "падение без сделок — это снятие, а не съедание"
    );
}

/// План F7 с трейлом после снятия: `gone<gone_pct>tr<trail_bps / 100>`.
pub(super) fn f7_plan_gone_trail(gone_pct: f64, trail_bps: f64, level_qty: f64) -> TradePlan {
    let mut plan = f7_plan(0.0, gone_pct, level_qty);
    if let TradePlan::Bounce { gone_trail_bps, .. } = &mut plan {
        *gone_trail_bps = trail_bps;
    }
    plan
}

/// Рост книги после снятия: аск 101 уходит на 104, бид поднимается до 103 — пик после снятия.
pub(super) fn rally_to_103(ts: i64) -> [Event; 3] {
    [
        depth_at(ts, false, 101.0, 0.0),
        depth_at(ts, false, 104.0, 5.0),
        depth_at(ts, true, 103.0, 5.0),
    ]
}

/// Трейл после снятия (владелец 23.09: «сразу выход по снятию — глупость, но снятие — уже риск,
/// нужна защита»): стена снята — позиция **не** закрывается; цена выросла до 103 и откатилась
/// к 99 — 400 bps от входа ≥ трейла 100 bps — выход по рынку с причиной «сняли».
#[test]
pub(super) fn a_removed_wall_arms_a_trail_instead_of_exiting() {
    let mut rest = vec![depth_at(4 * S, true, 99.0, 3.0)];
    rest.extend_from_slice(&rally_to_103(5 * S));
    rest.extend_from_slice(&[
        depth_at(6 * S, false, 105.0, 5.0),
        depth_at(7 * S, true, 103.0, 0.0),
        depth_at(8 * S, false, 106.0, 5.0),
        depth_at(9 * S, false, 107.0, 5.0),
    ]);
    let feed = f7_feed_tail(&rest, 10.0);
    assert_eq!(
        f7_exits(f7_plan_gone_trail(50.0, 100.0, 10.0), &feed),
        vec![ExitReason::WallGone],
        "откат от пика после снятия обязан закрыть позицию трейлом"
    );
}

/// Снятие без отката: трейл взведён, но цена после снятия только растёт — позиция живёт дальше,
/// выхода «сняли» нет (прежняя форма `gone50` закрыла бы её сразу на снятии).
#[test]
pub(super) fn a_removed_wall_without_a_pullback_keeps_the_position() {
    let mut rest = vec![depth_at(4 * S, true, 99.0, 3.0)];
    rest.extend_from_slice(&rally_to_103(5 * S));
    rest.extend_from_slice(&[
        depth_at(6 * S, false, 105.0, 5.0),
        depth_at(7 * S, false, 106.0, 5.0),
    ]);
    let feed = f7_feed_tail(&rest, 10.0);
    let reasons = f7_exits(f7_plan_gone_trail(50.0, 100.0, 10.0), &feed);
    assert!(
        !reasons.contains(&ExitReason::WallGone),
        "снятие без отката не закрывает позицию: {reasons:?}"
    );
    assert_eq!(
        f7_exits(f7_plan(0.0, 50.0, 10.0), &feed),
        vec![ExitReason::WallGone],
        "контроль: без трейла та же лента закрывает позицию на снятии"
    );
}

/// План F7 с безубытком после снятия: `gone<gone_pct>be` (`mode = 1`) или `…bex` (`mode = 2`).
pub(super) fn f7_plan_gone_be(gone_pct: f64, mode: u8, level_qty: f64) -> TradePlan {
    f7_plan_gone_stop(gone_pct, GoneStop::Breakeven { hard: mode == 2 }, level_qty)
}

/// План F7 с переносом стопа после снятия (`gone_stop`).
pub(super) fn f7_plan_gone_stop(gone_pct: f64, stop: GoneStop, level_qty: f64) -> TradePlan {
    let mut plan = f7_plan(0.0, gone_pct, level_qty);
    if let TradePlan::Bounce { gone_stop, .. } = &mut plan {
        *gone_stop = stop;
    }
    plan
}

/// Безубыток после снятия, мягкий (владелец 23.09: «снятие — стоп в ноль»): на снятии позиция хуже
/// безубытка — выхода нет; цена поднялась выше входа — стоп переехал в безубыток; откат к 99 —
/// выход «сняли» (а не стоп 90 и не дедлайн).
#[test]
pub(super) fn a_removed_wall_moves_the_stop_to_breakeven_once_in_profit() {
    let mut rest = vec![depth_at(4 * S, true, 99.0, 3.0)];
    rest.extend_from_slice(&rally_to_103(5 * S));
    rest.extend_from_slice(&[
        depth_at(6 * S, false, 105.0, 5.0),
        depth_at(7 * S, true, 103.0, 0.0),
        depth_at(8 * S, false, 106.0, 5.0),
        depth_at(9 * S, false, 107.0, 5.0),
    ]);
    let feed = f7_feed_tail(&rest, 10.0);
    assert_eq!(
        f7_exits(f7_plan_gone_be(50.0, 1, 10.0), &feed),
        vec![ExitReason::WallGone],
        "после снятия и роста откат к входу закрывает позицию в безубытке"
    );
    // Без снятия та же лента позицию не закрывает: стоп 90 далеко, откат к 99 — не выход.
    let mut calm_rest = vec![depth_at(4 * S, true, 99.0, 10.0)];
    calm_rest.extend_from_slice(&rally_to_103(5 * S));
    calm_rest.extend_from_slice(&[
        depth_at(7 * S, true, 103.0, 0.0),
        depth_at(8 * S, false, 106.0, 5.0),
    ]);
    let calm = f7_feed_tail(&calm_rest, 10.0);
    assert!(
        !f7_exits(f7_plan_gone_be(50.0, 1, 10.0), &calm).contains(&ExitReason::WallGone),
        "без снятия безубыток не взводится"
    );
}

/// Безубыток после снятия, жёсткий: на снятии позиция хуже безубытка (бид 99 при входе 100) —
/// выход по рынку сразу, причина «сняли».
#[test]
pub(super) fn a_removed_wall_below_breakeven_exits_at_once_in_hard_mode() {
    let feed = f7_feed_tail(
        &[
            depth_at(4 * S, true, 99.0, 3.0),
            depth_at(5 * S, false, 101.0, 5.0),
            depth_at(6 * S, false, 102.0, 5.0),
        ],
        10.0,
    );
    assert_eq!(
        f7_exits(f7_plan_gone_be(50.0, 2, 10.0), &feed),
        vec![ExitReason::WallGone],
        "жёсткий режим закрывает позицию хуже безубытка на снятии"
    );
    assert!(
        !f7_exits(f7_plan_gone_be(50.0, 1, 10.0), &feed).contains(&ExitReason::WallGone),
        "мягкий режим на той же ленте держит позицию"
    );
}

/// Стоп на уровень стены после снятия (T-24, владелец 26.09: «не безубыток а стоп на то место где
/// была плотность»): `gone<W>wall<B>`, `hard` — `…wallx<B>`.
pub(super) fn wall_stop(hard: bool, buffer_bps: f64) -> GoneStop {
    let mode = if hard {
        WallStopMode::Hard
    } else {
        WallStopMode::Soft
    };
    GoneStop::Wall { mode, buffer_bps }
}

/// `gone<W>wallk<B>` — снятие хуже нового стопа оставляет стоп плана до конца сделки.
pub(super) fn wall_keep(buffer_bps: f64) -> GoneStop {
    GoneStop::Wall {
        mode: WallStopMode::Keep,
        buffer_bps,
    }
}

/// `f7_feed_tail` и секунда хвоста: далёкий аск 110 (лучшие цены не меняет) — драйвер решает выход
/// на шаге после события, и последнему событию ленты нужен следующий шаг.
pub(super) fn wall_feed(rest: &[Event], wall: f64) -> Vec<Event> {
    let last = rest.last().map_or(3 * S, |e| e.exch_ts);
    let mut rest = rest.to_vec();
    rest.push(depth_at(last + S, false, 110.0, 5.0));
    f7_feed_tail(&rest, wall)
}

/// Бид возвращается на 100 (выше стены 99), стена 99 снята 10 → 3 на 5 с, бид 100 уходит на 6 с —
/// лучший бид 99 = стена.
pub(super) fn wall_removed_then_bid_falls_to_99() -> Vec<Event> {
    wall_feed(
        &[
            depth_at(4 * S, true, 100.0, 5.0),
            depth_at(5 * S, true, 99.0, 3.0),
            depth_at(6 * S, true, 100.0, 0.0),
        ],
        10.0,
    )
}

/// Переезд: на снятии бид 100 выше стены 99 — стоп переезжает с 90 на 99; бид падает к 99 —
/// выход «сняли» по цене стены. Без формы и с безубытком (100 плюс комиссии выше бида 100) та же
/// лента позицию держит.
#[test]
pub(super) fn a_removed_wall_moves_the_stop_to_the_wall_level() {
    let feed = wall_removed_then_bid_falls_to_99();
    let run = f7_run(f7_plan_gone_stop(50.0, wall_stop(false, 0.0), 10.0), &feed);
    assert_eq!(run.fill_reason, vec![ExitReason::WallGone]);
    assert!(
        run.fills[0].exit_px >= 99.0 - 1e-9,
        "выход по стопу на стене 99: {}",
        run.fills[0].exit_px
    );
    assert!(
        !f7_exits(f7_plan(0.0, 0.0, 10.0), &feed).contains(&ExitReason::WallGone),
        "контроль: без формы стоп 90 — выхода на 99 нет"
    );
    assert!(
        !f7_exits(f7_plan_gone_be(50.0, 1, 10.0), &feed).contains(&ExitReason::WallGone),
        "контроль: безубыток на той же ленте не взводится"
    );
}

/// Буфер: стоп в 100 bps ниже стены (98,01) — бид 99 его не задевает, выход только на 98.
#[test]
pub(super) fn the_wall_stop_buffer_sits_below_the_wall() {
    let mut rest = vec![
        depth_at(4 * S, true, 100.0, 5.0),
        depth_at(5 * S, true, 99.0, 3.0),
        depth_at(6 * S, true, 100.0, 0.0),
        depth_at(7 * S, true, 98.0, 5.0),
    ];
    rest.push(depth_at(8 * S, true, 99.0, 0.0));
    let feed = wall_feed(&rest, 10.0);
    let at_wall = f7_run(f7_plan_gone_stop(50.0, wall_stop(false, 0.0), 10.0), &feed);
    let buffered = f7_run(
        f7_plan_gone_stop(50.0, wall_stop(false, 100.0), 10.0),
        &feed,
    );
    assert_eq!(at_wall.fill_reason, vec![ExitReason::WallGone]);
    assert_eq!(buffered.fill_reason, vec![ExitReason::WallGone]);
    assert!(
        at_wall.fill_exit_ns[0] < 7 * S,
        "без буфера — выход на бид 99 (6 с): {}",
        at_wall.fill_exit_ns[0]
    );
    assert!(
        buffered.fill_exit_ns[0] >= 8 * S,
        "с буфером 100 bps бид 99 не выход, выход — на 98 (8 с): {}",
        buffered.fill_exit_ns[0]
    );
}

/// «Уже ниже»: стена 99 снята целиком, лучший бид 98 — позиция хуже нового стопа. Жёсткий режим
/// выходит сразу, мягкий держит стоп 90; вернувшаяся к 100 цена взводит перенос и в мягком, и
/// откат к 98 тогда закрывает позицию «сняли».
#[test]
pub(super) fn a_wall_removed_below_the_new_stop_exits_in_hard_mode_and_waits_in_soft() {
    let below = [
        depth_at(4 * S, true, 98.0, 5.0),
        depth_at(5 * S, true, 99.0, 0.0),
    ];
    let feed = wall_feed(&below, 10.0);
    let hard = f7_run(f7_plan_gone_stop(50.0, wall_stop(true, 0.0), 10.0), &feed);
    assert_eq!(hard.fill_reason, vec![ExitReason::WallGone]);
    assert!(
        hard.fill_exit_ns[0] < 6 * S,
        "жёсткий — выход на снятии: {}",
        hard.fill_exit_ns[0]
    );
    assert!(
        !f7_exits(f7_plan_gone_stop(50.0, wall_stop(false, 0.0), 10.0), &feed)
            .contains(&ExitReason::WallGone),
        "мягкий — стоп остаётся 90, позиция держится"
    );
    let mut back = below.to_vec();
    back.extend_from_slice(&[
        depth_at(6 * S, true, 100.0, 5.0),
        depth_at(7 * S, true, 100.0, 0.0),
    ]);
    let feed = wall_feed(&back, 10.0);
    let soft = f7_run(f7_plan_gone_stop(50.0, wall_stop(false, 0.0), 10.0), &feed);
    assert_eq!(
        soft.fill_reason,
        vec![ExitReason::WallGone],
        "мягкий — цена вернулась выше стены, стоп переехал на 99, откат к 98 его выбил"
    );
    assert!(soft.fill_exit_ns[0] >= 7 * S);
    // `wallk` (владелец 26.09: «оба варианта мягкого режима прогнать»): на той же ленте снятие
    // застало позицию хуже стены — стоп 90 до конца сделки; возврат к 100 и откат к 98 не выход.
    let keep = f7_exits(f7_plan_gone_stop(50.0, wall_keep(0.0), 10.0), &feed);
    assert!(
        !keep.contains(&ExitReason::WallGone),
        "wallk — стена больше не учитывается: {keep:?}"
    );
}

/// `wallk` не отличается от мягкого, когда снятие застало позицию у стены или лучше: перенос на
/// 99 сразу, выход «сняли» на откате к 99.
#[test]
pub(super) fn wall_keep_moves_the_stop_when_the_removal_finds_the_position_above_the_wall() {
    let feed = wall_removed_then_bid_falls_to_99();
    assert_eq!(
        f7_exits(f7_plan_gone_stop(50.0, wall_keep(0.0), 10.0), &feed),
        vec![ExitReason::WallGone]
    );
}

/// Стоп не опускается: цель 2000 bps ниже стены (79,2) хуже стопа плана 90 — стоп остаётся 90,
/// падение к 85 закрывает позицию обычным стопом, не «сняли».
#[test]
pub(super) fn the_wall_stop_never_lowers_the_plan_stop() {
    let feed = wall_feed(
        &[
            depth_at(4 * S, true, 100.0, 5.0),
            depth_at(5 * S, true, 99.0, 3.0),
            depth_at(6 * S, true, 100.0, 0.0),
            depth_at(7 * S, true, 85.0, 5.0),
            depth_at(8 * S, true, 99.0, 0.0),
        ],
        10.0,
    );
    assert_eq!(
        f7_exits(
            f7_plan_gone_stop(50.0, wall_stop(false, 2000.0), 10.0),
            &feed
        ),
        vec![ExitReason::Stop]
    );
}

/// Шорт (аск-стена 101, вход 100, стоп 110): снятие при аске 99 — стоп переезжает **вниз** на
/// 101 · (1 + 1 %) = 102,01; при аске 103 (хуже нового стопа) жёсткий режим выходит, мягкий держит
/// 110. Трейл после снятия и `gone<W>` без продолжения при переносе молчат.
#[test]
pub(super) fn a_short_wall_stop_moves_down_to_the_wall_plus_buffer() {
    let removed = WallNow {
        ok: true,
        qty: 3.0,
        eaten_pct: 70.0,
    };
    let guard = |hard: bool, ask: f64| {
        let mut state = StrategyState::with_plan(0, SIGMA_SHORT, 1.0, 1, f7_plan(0.0, 50.0, 10.0));
        state.level_qty_at_entry = 10.0;
        let form = GoneForm {
            pct: 50.0,
            trail_bps: 0.0,
            stop: wall_stop(hard, 100.0),
        };
        state.observe_gone(form, removed, ask, HbtSide::Sell, 100.0, 110.0, 101.0)
    };
    let soft = guard(false, 99.0);
    assert!(close(soft.stop_px, 101.0 * 1.01), "{}", soft.stop_px);
    assert!(!soft.exit && !soft.trail_hit && !soft.stop_hard_exit);
    let hard_ok = guard(true, 99.0);
    assert!(close(hard_ok.stop_px, 101.0 * 1.01) && !hard_ok.stop_hard_exit);
    let soft_worse = guard(false, 103.0);
    assert!(close(soft_worse.stop_px, 110.0) && !soft_worse.stop_hard_exit);
    let hard_worse = guard(true, 103.0);
    assert!(close(hard_worse.stop_px, 110.0) && hard_worse.stop_hard_exit);
    // `wallk`: снятие при аске 103 (хуже 102,01) — перенос отменён; аск 99 позже его не взводит.
    let mut state = StrategyState::with_plan(0, SIGMA_SHORT, 1.0, 1, f7_plan(0.0, 50.0, 10.0));
    state.level_qty_at_entry = 10.0;
    let form = GoneForm {
        pct: 50.0,
        trail_bps: 0.0,
        stop: wall_keep(100.0),
    };
    for ask in [103.0, 99.0] {
        let g = state.observe_gone(form, removed, ask, HbtSide::Sell, 100.0, 110.0, 101.0);
        assert!(
            close(g.stop_px, 110.0) && !g.stop_hard_exit,
            "аск {ask}: {g:?}"
        );
    }
}

/// F7 (Б-75): падение размера **сделками** не даёт `WallGone` — стена 10 → 3,
/// но из семи съеденных лотов шесть прошли лентой (≥ половины падения).
/// Форма `gone` молчит, круг закрывается дедлайном.
#[test]
pub(super) fn a_wall_that_shrank_by_trades_is_not_gone() {
    let feed = f7_feed_tail(
        &[
            trade_at(4 * S, true, 99.0, 6.0),
            depth_at(4 * S + 500_000_000, true, 99.0, 3.0),
            depth_at(5 * S, false, 101.0, 5.0),
            // Дедлайн 30 с от входа (~1 с) — закрываем круг по нему.
            depth_at(32 * S, false, 102.0, 5.0),
        ],
        10.0,
    );
    assert_eq!(
        f7_exits(f7_plan(0.0, 50.0, 10.0), &feed),
        vec![ExitReason::Deadline],
        "стена ушла сделками — `gone` не срабатывает, круг живёт до дедлайна"
    );
}

/// F7 (Б-75), гейт «те же круги»: при выключенных формах выхода (`none`,
/// оба порога нули) лента в стену ничего не решает — круг закрывается
/// дедлайном, как до F7.
#[test]
pub(super) fn the_none_exit_form_ignores_the_wall_trades() {
    let feed = f7_feed_tail(
        &[
            trade_at(4 * S, true, 99.0, 9.0),
            depth_at(5 * S, false, 101.0, 5.0),
            depth_at(32 * S, false, 102.0, 5.0),
        ],
        10.0,
    );
    assert_eq!(
        f7_exits(f7_plan(0.0, 0.0, 10.0), &feed),
        vec![ExitReason::Deadline],
        "форма `none` не читает ни съедание, ни снятие"
    );
}
