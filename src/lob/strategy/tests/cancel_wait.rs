//! Тесты стратегии — F8b: потолок ожидания отмены, сироты, гонки.

use super::*;

// -----------------------------------------------------------------------
// F8b (аудит этапа F 21.09, В5/Р2/Р3/Р4; решение владельца В-79): потолок
// ожидания подтверждения отмены `CANCEL_WAIT_NS` — один механизм на вход
// (`CancelPending`) и на лимитку выхода (`ExitCancelPending`), плюс счёт
// причин F7 в агрегате `ExitTally`.
// -----------------------------------------------------------------------

/// Идентификатор лимитки выхода в тестах потолка: ставится руками, потому что
/// сценарий — «отмена летит, а биржа не отвечает», и доводить до него
/// естественный круг значило бы проверять крейт, а не предохранитель.
pub(super) const EXIT_ID: u64 = 7;

/// Задержки тестов потолка: постановка 1 мс, **снятие 5 с** — так отмена
/// остаётся нерешённой и видно, что делает предохранитель (`CANCEL_WAIT_NS`
/// = 1 с); рыночный — 1 мс.
pub(super) fn slow_cancel_latency() -> ExecLatency {
    ExecLatency {
        place_ns: 1_000_000,
        cancel_ns: 5_000_000_000,
        taker_ns: 1_000_000,
    }
}

/// План тестов потолка: вход 100 у бид-стены 99, стоп/тейк далеко, дедлайн
/// 30 с, срок жизни входа — `ttl_ns`. Условия F5 выключены: тест про отмену,
/// а не про «стена снята».
pub(super) fn cancel_wait_plan(ttl_ns: i64) -> TradePlan {
    TradePlan::Bounce {
        entry_px: 100.0,
        stop_px: 90.0,
        take_px: 110.0,
        deadline_ns: 30 * S,
        entry_ttl_ns: ttl_ns,
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
        level_qty: 0.0,
        lot_qty: 1.0,
        exit_eat_pct: 0.0,
        wall_eat: crate::lob::strategy::WallEatExit::OFF,
        pyramid: crate::lob::strategy::PyramidCfg::OFF,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_stop: crate::lob::strategy::GoneStop::Off,
    }
}

/// F8b (В5): подтверждение отмены **входа** не приходит — круг освобождается
/// потолком, а не висит «занятым» до конца записи. Причина названа
/// `CancelTimeout` (видна в `forms.csv`), позиции нет, фаза снова `Idle`.
#[test]
pub(super) fn a_cancel_the_exchange_never_confirms_is_released_by_the_ceiling() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        // События после потолка: отмена (5 с) всё ещё летит — без
        // предохранителя фаза ждала бы её здесь и на всей записи дальше.
        depth_at(2 * S, true, 100.0, 5.0),
        depth_at(3 * S, true, 100.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        1.0,
        slow_cancel_latency(),
        QueueModelKind::RiskAdverse,
    );
    // Срок жизни входа 0.1 с: снятие уходит на первом же шаге после него и
    // подтверждения не получает.
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 1.0, 1, cancel_wait_plan(S / 10));

    // Тот же цикл, что `drive`, плюс наблюдение сирот по шагам: после потолка
    // и до подтверждения отмены нога обязана числиться сиротой.
    let mut actions = Vec::new();
    let mut orphan_seen = false;
    loop {
        let r = hbt.elapse(100_000_000).unwrap();
        actions.push(on_event(&mut hbt, &mut state).unwrap());
        orphan_seen |= state.has_orphans();
        if r == ElapseResult::EndOfData {
            break;
        }
    }

    let timeouts = actions
        .iter()
        .filter(|a| {
            matches!(
                a,
                Action::EntryTimedOut {
                    reason: EntryCancelReason::CancelTimeout,
                    ..
                }
            )
        })
        .count();
    assert_eq!(
        timeouts, 1,
        "потолок обязан освободить круг ровно раз и назвать причину: {actions:?}"
    );
    // Дальше драйвер продолжает запись и стратегия перевооружается (второй
    // `EntrySubmitted` в списке) — это норма: проверяется, что первый круг
    // освобождён потолком, а не остался висеть «занятым».
    assert_eq!(state.position(), 0.0, "позиции не было");
    // F8c (К1): заявка после потолка — сирота, её снятие повторяется, пока
    // биржа не подтвердит; подтверждение (5 с) крейт доигрывает после конца
    // данных — к концу записи сирота отпущена, исполнений у неё нет.
    assert!(
        orphan_seen,
        "после потолка и до подтверждения нога — сирота"
    );
    assert!(!state.has_orphans(), "после подтверждения отмены сирот нет");
    assert_eq!(state.orphan_fills(), 0, "сирота не исполнялась");
}

/// F8c (К1): сирота **отпускается**, когда биржа подтвердила отмену — партия
/// не живёт вечно, и исполнений у неё нет. Запись длиннее задержки снятия
/// (5 с), поэтому подтверждение успевает дойти.
#[test]
pub(super) fn an_orphan_is_released_once_the_exchange_confirms_the_cancel() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        depth_at(2 * S, true, 100.0, 5.0),
        depth_at(6 * S, true, 100.0, 5.0),
        depth_at(7 * S, true, 100.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        1.0,
        slow_cancel_latency(),
        QueueModelKind::RiskAdverse,
    );
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 1.0, 1, cancel_wait_plan(S / 10));

    let _ = drive(&mut hbt, &mut state);

    assert!(
        !state.has_orphans(),
        "отмена подтверждена на 5 с — сирот больше нет: {:?}",
        state.phase
    );
    assert_eq!(state.orphan_fills(), 0);
    assert_eq!(state.orphan_overflow(), 0);
}

/// F8c (К1): сирота **входа** исполнилась, пока её снятие летело (продажа 6
/// съела очередь на 1.5 с; отмена, отправленная на 0.2 с, доедет до биржи на
/// 1.7 с — потолок 1 с сработал на 1.2 с) — лишняя позиция гасится по рынку и
/// считается. Инвариант, ради которого сироты заведены: позиция на бирже
/// равна позиции в учёте стратегии, а не расходится на исполненную сироту.
/// Задержка снятия здесь 1.5 с, а не 5 с: шина заявок крейта — очередь без
/// обгона, и всё, что отправлено после медленной отмены, ждёт за ней;
/// с 5 с гашение не успело бы дойти до биржи до конца записи.
#[test]
pub(super) fn a_filled_entry_orphan_is_flattened_and_counted() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        trade_at(S + S / 2, true, 100.0, 6.0),
        depth_at(2 * S, true, 100.0, 5.0),
        depth_at(3 * S, true, 100.0, 5.0),
        // Хвост, чтобы гашение по рынку (IOC) успело дойти до биржи и назад.
        depth_at(4 * S, true, 100.0, 5.0),
        depth_at(5 * S, true, 100.0, 5.0),
    ];
    let lat = ExecLatency {
        place_ns: 1_000_000,
        cancel_ns: 1_500_000_000,
        taker_ns: 1_000_000,
    };
    let mut hbt = build_backtest(&feed, 1.0, 1.0, lat, QueueModelKind::RiskAdverse);
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 1.0, 1, cancel_wait_plan(S / 10));

    let actions = drive(&mut hbt, &mut state);

    assert_eq!(
        state.orphan_fills(),
        1,
        "исполнение сироты обязано быть замечено и посчитано: {actions:?}"
    );
    assert!(
        (hbt.position(0) - state.position()).abs() < 1e-9,
        "позиция биржи {} обязана совпасть с учётом стратегии {}: сирота погашена",
        hbt.position(0),
        state.position()
    );
}

/// F8c (К1): сирота **выхода** — лимитка тейка, чью отмену биржа не
/// подтвердила за потолок, — исполнилась позже: это наш же выход, он
/// зачитывается в позицию (а не откупается обратно), круг закрывается.
#[test]
pub(super) fn a_filled_exit_orphan_is_accounted_as_our_exit() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        depth_at(1_200_000_000, true, 100.0, 5.0),
        // Покупка по 111 выше стоящей продажи 110 — крейт исполняет её
        // целиком; потолок (1 с) уже сработал, лимитка — сирота.
        trade_at(1_500_000_000, false, 111.0, 1.0),
        depth_at(2 * S, false, 111.0, 5.0),
        depth_at(6 * S, false, 111.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        1.0,
        slow_cancel_latency(),
        QueueModelKind::RiskAdverse,
    );
    let mut state = racing_exit_state(&mut hbt);

    let actions = drive(&mut hbt, &mut state);

    assert_eq!(state.exit_cancel_timeouts(), 1, "{actions:?}");
    assert_eq!(
        state.orphan_fills(),
        1,
        "исполнение сироты выхода посчитано"
    );
    assert!(
        state.position() <= 0.0,
        "позиция закрыта исполнением сироты: {:?}",
        actions
    );
    // Круг закрыт без заявки на ноль: следующий `ExitSubmitted` после
    // потолка не отправлялся (стратегия дальше перевооружается новым входом —
    // это норма записи, не этого круга).
    assert!(
        !actions
            .iter()
            .any(|a| matches!(a, Action::ExitSubmitted { .. })),
        "выход на нулевую позицию не ставится: {actions:?}"
    );
    // Позицию биржи здесь не сверяем: `racing_exit_state` собирает позицию
    // полями, у крейта входа не было (его −1 — артефакт харнесса).
}

/// F8b (В5, вторая половина — сама причина зависания): нога, чей запрос
/// **постановки** летел в момент потолка срока жизни, в тот шаг не снимается
/// (`cancel_resting` трогает только стоящие ноги), и без повтора осталась бы
/// в рынке навсегда. Повтор снимает её, когда она станет `New`, — отмена
/// подтверждается, причина `Ttl`, предохранитель не при чём.
#[test]
pub(super) fn an_entry_leg_whose_place_was_in_flight_is_cancelled_by_the_retry() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        depth_at(S, true, 100.0, 5.0),
        depth_at(2 * S, true, 100.0, 5.0),
    ];
    // Постановка 0.5 с — на 0.1 с (потолок входа) заявка ещё не у биржи;
    // снятие 1 мс — отмена подтверждается сразу, как только нога встала.
    let lat = ExecLatency {
        place_ns: 500_000_000,
        cancel_ns: 1_000_000,
        taker_ns: 1_000_000,
    };
    let mut hbt = build_backtest(&feed, 1.0, 1.0, lat, QueueModelKind::RiskAdverse);
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 1.0, 1, cancel_wait_plan(S / 10));

    let actions = drive(&mut hbt, &mut state);

    assert!(
        actions.iter().any(|a| matches!(
            a,
            Action::EntryTimedOut {
                reason: EntryCancelReason::Ttl,
                ..
            }
        )),
        "повтор снятия обязан закрыть вход по сроку жизни: {actions:?}"
    );
    assert!(
        !actions.iter().any(|a| matches!(
            a,
            Action::EntryTimedOut {
                reason: EntryCancelReason::CancelTimeout,
                ..
            }
        )),
        "отмена подтвердилась — предохранитель не при чём: {actions:?}"
    );
}

/// Готовит состояние «отмена лимитки выхода летит» на настоящем крейте:
/// позиция 1.0 открыта по 100, лимитка тейка 110 стоит в рынке (`EXIT_ID`),
/// фаза — `ExitCancelPending`. Хедж-состояние собирается полями: ветку
/// естественного круга (`Holding` → частичный тейк → рыночная причина)
/// проверяет Б1-тест, а здесь проверяется сам предохранитель.
pub(super) fn racing_exit_state(hbt: &mut Backtest<FastMarketDepth>) -> StrategyState {
    hbt.submit_sell_order(
        0,
        EXIT_ID,
        110.0,
        1.0,
        TimeInForce::GTC,
        OrdType::Limit,
        false,
    )
    .unwrap();
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 1.0, 1, cancel_wait_plan(S / 10));
    state.entry_qty = 1.0;
    state.entry_notional = 100.0;
    state.phase = Phase::ExitCancelPending {
        order_id: EXIT_ID,
        entry_ns: 0,
        cancel_sent_ns: 0,
    };
    state
}

/// F8b (С11 аудита 21.09): границы `EntryLadder` — единственного места, где
/// ноги лестницы F6 складываются в массив фиксированной длины. Переполнение
/// обязано быть отказом (`false`), а не молчаливым усечением: у движка счёт
/// ног идёт битовой маской, и «ещё пара ног» там уже не считается.
#[test]
pub(super) fn entry_ladder_fills_to_capacity_and_refuses_the_extra_leg() {
    let mut ladder = EntryLadder::NONE;
    assert_eq!(ladder.n, 0, "вход без лестницы — ноль ног");
    assert_eq!(ladder.outer_tick(), None, "дальней ноги нет");
    assert_eq!(
        ladder.weighted_avg_tick(),
        0.0,
        "средняя пустой лестницы — ноль"
    );

    for i in 0..MAX_ENTRY_LEGS {
        assert!(
            ladder.push(100 + i as i64, 1.0 / MAX_ENTRY_LEGS as f64),
            "нога {i} влезает в ёмкость"
        );
    }
    assert_eq!(ladder.n as usize, MAX_ENTRY_LEGS, "ёмкость исчерпана ровно");
    // Девятая нога — отказ, состояние не тронуто.
    assert!(!ladder.push(999, 0.5), "за ёмкостью — отказ, а не усечение");
    assert_eq!(ladder.n as usize, MAX_ENTRY_LEGS);
    assert_eq!(ladder.outer_tick(), Some(100 + MAX_ENTRY_LEGS as i64 - 1));
}

/// F8b (С11): средняя цена лестницы — **по долям**, а не по числу ног: у
/// нижней ноги может быть двойной вес («основной объём к сайзу», F6), и от
/// этой средней форма считает стоп и тейк.
#[test]
pub(super) fn entry_ladder_averages_the_legs_by_their_weights() {
    let mut ladder = EntryLadder::NONE;
    assert!(ladder.push(100, 0.75));
    assert!(ladder.push(200, 0.25));
    assert!(
        (ladder.weighted_avg_tick() - 125.0).abs() < 1e-9,
        "0.75 × 100 + 0.25 × 200 = 125, а не середина 150"
    );
    // Доли формы нормированы: сумма — единица (свойство `ladder_legs`).
    assert!((ladder.frac[0] + ladder.frac[1] - 1.0).abs() < 1e-9);
}

/// F8b (Р3): гонка отмены и исполнения **на выходе** — сделка исполняет
/// лимитку тейка, пока отмена летит (до биржи она доедет через 5 с, потолок —
/// 1 с). Позиция закрыта исполнением: фаза `Idle`, второй выход не
/// отправляется, предохранитель не срабатывает.
#[test]
pub(super) fn an_exit_fill_that_races_the_cancel_closes_the_round() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        // Покупка по 111 — **выше** стоящей продажи 110: крейт исполняет её
        // целиком (`Ordering::Less`), и это исполнение приходит раньше отмены
        // (та доедет до биржи через 5 с, потолок — через 1 с).
        trade_at(500_000_000, false, 111.0, 1.0),
        depth_at(3 * S, false, 111.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        1.0,
        slow_cancel_latency(),
        QueueModelKind::RiskAdverse,
    );
    let mut state = racing_exit_state(&mut hbt);

    let actions = drive(&mut hbt, &mut state);

    assert_eq!(
        state.exit_cancel_timeouts(),
        0,
        "гонку выиграло исполнение — потолок не срабатывает: {actions:?}"
    );
    assert!(
        state.position() <= 0.0,
        "позиция закрыта исполнением лимитки: {:?}",
        actions
    );
}

/// F8b (Р2): подтверждение отмены лимитки выхода не пришло — потолок
/// возвращает остаток позиции плану (`Holding`), а не оставляет круг в
/// «отмена летит» до конца записи. Срабатывание посчитано
/// (`exit_cancel_timeouts`), и оно одно: дальше круг ведёт `Holding`.
#[test]
pub(super) fn the_exit_cancel_ceiling_returns_the_round_to_the_plan() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        depth_at(1_500_000_000, true, 100.0, 5.0),
        depth_at(2_500_000_000, true, 100.0, 5.0),
        depth_at(2_600_000_000, true, 100.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        1.0,
        slow_cancel_latency(),
        QueueModelKind::RiskAdverse,
    );
    let mut state = racing_exit_state(&mut hbt);

    let actions = drive(&mut hbt, &mut state);

    assert_eq!(
        state.exit_cancel_timeouts(),
        1,
        "потолок обязан сработать ровно раз: {actions:?}"
    );
    assert!(
        matches!(state.phase, Phase::Holding { .. }),
        "остаток позиции ведёт план: {:?}",
        state.phase
    );
    assert!(state.position() > 0.0, "позиция не потеряна");
}

/// F8b/F8c (К5): счётчик потолка отмены **входа** доходит до `BounceRun`
/// через настоящий драйвер `drive_bounce` (`SignalStep` → агрегат), а не
/// только живёт в состоянии: снятие входа не подтверждается 5 с, потолок —
/// 1 с, круг закрыт причиной `CancelTimeout`, и это ровно одна единица в
/// `entry_cancelled_cancel_timeout`; выходной счётчик и сироты — нули.
#[test]
pub(super) fn the_driver_counts_the_entry_cancel_ceiling() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        depth_at(2 * S, true, 100.0, 5.0),
        depth_at(3 * S, true, 100.0, 5.0),
        depth_at(4 * S, true, 100.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        1.0,
        slow_cancel_latency(),
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
        t0_ns: S,
        sigma: SIGMA_LONG,
        plan: cancel_wait_plan(S / 10),
        profile: 0,
        qty: None,
    };
    let run = drive_bounce(&mut hbt, 0, &[signal], &cfg).unwrap();

    assert_eq!(run.signals, 1);
    assert_eq!(
        run.entry_cancelled_cancel_timeout, 1,
        "потолок входа обязан дойти до агрегата ровно единицей"
    );
    assert_eq!(run.exit_cancel_timeout, 0, "выходного потолка не было");
    assert_eq!(run.orphan_fills, 0, "сирота не исполнялась");
    assert!(run.fills.is_empty(), "круга без входа нет");
}

/// F8c (ревью 22.09, блокер 1): сирота **переживает границу круга** в
/// настоящем драйвере `drive_bounce`. Первый сигнал: вход не исполнился,
/// снят по сроку жизни, отмена не подтверждена за потолок → круг закрыт тем
/// же событием (`Idle`), состояние выброшено драйвером. Без переноса нога
/// осталась бы в рынке навсегда; с переносом второй круг наследует её,
/// сделка исполняет сироту, гашение по рынку и счётчик `orphan_fills` — в
/// агрегате дня.
#[test]
pub(super) fn an_orphan_survives_the_round_boundary_in_the_driver() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        depth_at(2 * S, true, 100.0, 5.0),
        // Второй сигнал (t0 = 2.4 с) стартует новый круг: сирота первого
        // унаследована, продажа 6 в бид 100 исполняет её (отмена доедет до
        // биржи только на 2.7 с) — гашение по рынку, счётчик.
        trade_at(2_500_000_000, true, 100.0, 6.0),
        depth_at(3 * S, true, 100.0, 5.0),
        depth_at(4 * S, true, 100.0, 5.0),
        depth_at(5 * S, true, 100.0, 5.0),
    ];
    let lat = ExecLatency {
        place_ns: 1_000_000,
        cancel_ns: 1_500_000_000,
        taker_ns: 1_000_000,
    };
    let mut hbt = build_backtest(&feed, 1.0, 1.0, lat, QueueModelKind::RiskAdverse);
    let cfg = DriveConfig {
        tape_log_secs: 0,
        order_qty: 1.0,
        first_order_id: 1,
        queue_model: QueueModelKind::RiskAdverse,
        busy_skip: true,
        hold_skip: false,
    };
    // Срок жизни входа 0.1 с: снятие на 1.2 с, потолок на 2.2 с — круг закрыт.
    let signals = [
        BounceSignal {
            t0_ns: S,
            sigma: SIGMA_LONG,
            plan: cancel_wait_plan(S / 10),
            profile: 0,
            qty: None,
        },
        BounceSignal {
            t0_ns: 2_400_000_000,
            sigma: SIGMA_LONG,
            plan: cancel_wait_plan(30 * S),
            profile: 0,
            qty: None,
        },
    ];
    let run = drive_bounce(&mut hbt, 0, &signals, &cfg).unwrap();

    assert_eq!(
        run.entry_cancelled_cancel_timeout, 1,
        "первый круг закрыт потолком"
    );
    assert_eq!(
        run.orphan_fills, 1,
        "сирота первого круга исполнилась во втором и посчитана: {:?}",
        run.fill_reason
    );
}

/// F8b (Р4): причины F7 считает драйвер (`ExitTally`), а не строки кругов —
/// и считает их **раздельно**: «съели» и «сняли» не путаются местами и не
/// остаются нулями (адрес колонки `forms.csv` проверяет `bounce_grid`).
#[test]
pub(super) fn the_driver_counts_the_exit_reasons_into_the_tally() {
    let eaten = f7_run(
        f7_plan(50.0, 0.0, 10.0),
        &f7_feed_tail(
            &[
                trade_at(4 * S, true, 99.0, 6.0),
                depth_at(5 * S, false, 101.0, 5.0),
                depth_at(6 * S, false, 102.0, 5.0),
            ],
            10.0,
        ),
    );
    assert_eq!(eaten.fill_reason, vec![ExitReason::EatenByTrades]);
    assert_eq!(eaten.exits.eaten_by_trades, 1, "«съели» — своя строка");
    assert_eq!(eaten.exits.wall_gone, 0, "«сняли» не при чём");

    let gone = f7_run(
        f7_plan(0.0, 50.0, 10.0),
        &f7_feed_tail(
            &[
                depth_at(4 * S, true, 99.0, 3.0),
                depth_at(5 * S, false, 101.0, 5.0),
                depth_at(6 * S, false, 102.0, 5.0),
            ],
            10.0,
        ),
    );
    assert_eq!(gone.fill_reason, vec![ExitReason::WallGone]);
    assert_eq!(gone.exits.wall_gone, 1, "«сняли» — своя строка");
    assert_eq!(gone.exits.eaten_by_trades, 0, "«съели» не при чём");
}

/// B1 (ревью 23.09): нога входа исполнилась **частично до** потолка отмены —
/// это исполненное уже позиция плана (`entry_qty`), её ведут стоп и выход.
/// Сирота отвечает только за исполнение после усыновления: гасить по рынку
/// то, что план держит, нельзя (прежде партия заводилась с `accounted = 0`,
/// и первая уборка продавала 0.5 поверх позиции плана — план потом выходил
/// ещё раз, и позиция на бирже уходила в минус).
#[test]
pub(super) fn an_entry_orphan_does_not_flatten_what_the_plan_already_holds() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        // Очередь 5 съедена, нашей ноге 2.0 досталось 0.5 — до срока жизни.
        trade_at(3 * S / 10, true, 100.0, 5.5),
        // После потолка (снятие летит 5 с, потолок 1 с): план уже в `Holding`.
        depth_at(2 * S, true, 100.0, 5.0),
        depth_at(3 * S, true, 100.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        0.1,
        slow_cancel_latency(),
        QueueModelKind::Prob { n: 3.0 },
    );
    let mut plan = cancel_wait_plan(S / 2);
    if let TradePlan::Bounce { lot_qty, .. } = &mut plan {
        *lot_qty = 0.1;
    }
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 2.0, 1, plan);

    // Цикл `drive` с наблюдением сирот по шагам: подтверждение снятия (5 с)
    // крейт доигрывает после конца данных, к концу записи сирота отпущена.
    let mut actions = Vec::new();
    let mut orphan_seen = false;
    loop {
        let r = hbt.elapse(100_000_000).unwrap();
        actions.push(on_event(&mut hbt, &mut state).unwrap());
        orphan_seen |= state.has_orphans();
        if r == ElapseResult::EndOfData {
            break;
        }
    }

    assert!(
        actions
            .iter()
            .any(|a| matches!(a, Action::EntrySubmitted { .. })),
        "{actions:?}"
    );
    assert!(
        (state.position() - 0.5).abs() < 1e-9,
        "позиция плана — исполненные до потолка 0.5: {} ({:?})",
        state.position(),
        state.phase
    );
    assert!(orphan_seen, "нога после потолка — сирота");
    assert_eq!(
        state.orphan_fills(),
        0,
        "исполненное до усыновления — не сироты, гасить нечего: {actions:?}"
    );
}

/// Э-04б (условие Судьи 1): пропуск пустых шагов удержания (`hold_wakeup_ns`) опирается на то, что решение
/// удержания зависит от времени только через досрочный выход и дедлайн. Новое сравнение с `now` в
/// `on_holding` / `decide_exit` ломает этот тест — тогда порог надо добавить в `hold_wakeup_ns`.
#[test]
pub(super) fn holding_decision_reads_time_only_at_the_known_thresholds() {
    let uses = |src: &str, name: &str| -> Vec<String> {
        let start = src
            .find(&format!("\npub(super) fn {name}<"))
            .expect("функция есть");
        let end = src[start + 5..]
            .find("\npub(super) fn ")
            .map_or(src.len(), |e| start + 5 + e);
        src[start..end]
            .lines()
            .filter(|l| {
                l.split(|c: char| !(c.is_alphanumeric() || c == '_'))
                    .any(|w| w == "now")
            })
            .map(|l| l.trim().to_string())
            .collect()
    };
    assert_eq!(
        uses(include_str!("../exit.rs"), "decide_exit"),
        [
            "now: i64,",
            "if !maker_allowed || now.saturating_sub(entry_ns) < HOLD_NS {",
            // Г-117 `tsl`: тейк зависит от времени; пропуск шагов при форме выключен (`pyramid_on`).
            "now.saturating_sub(entry_ns),",
            // TK-014 `weat*`: пропуск шагов при этой форме выключен (`hold_wakeup_ns` → `None`).
            "if let Some(b) = state.wall_bucket(now.div_euclid(1_000_000_000)) {",
            "let (eaten, max_qty) = state.wall_window(entry_ns, now);",
            ".and_then(|b| b.move_bps(now, wall_eat.secs))",
            // Г-112/116 `tape<Q>`/`cxl<Q>`: кольцо включено — пропуск шагов выключен (`tape_on` в `hold_wakeup_ns`).
            "&& state.tape_since_entry(entry_ns, now, pyr.tape_w) / state.level_qty_at_entry",
            "&& state.cxl_since_entry(entry_ns, now, pyr.cxl_w) / state.level_qty_at_entry",
            "&& now.saturating_sub(entry_ns) >= early_exit_ns",
            "now.saturating_sub(entry_ns) >= deadline_ns,",
            // Г-133 `chase<мс>`: пропуск шагов при форме выключен (`pyramid_on` включает `chase_ms > 0`).
            "state.chase_start_ns = now;",
            "if now.saturating_sub(state.chase_start_ns) < i64::from(pyr.chase_ms) * 1_000_000 {",
        ]
    );
    assert_eq!(
        uses(include_str!("../handlers.rs"), "on_holding"),
        [
            "now: i64,",
            "match decide_exit(bot, state, entry_ns, now, quotes, true) {",
        ]
    );
}
