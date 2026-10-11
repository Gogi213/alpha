//! Тесты стратегии — F5/F6/R3: срок жизни входа, лестница входа, доли лота.

use super::*;

// -----------------------------------------------------------------------
// F5 (план 2026-09-20, В-74): срок жизни входа — «пока стена жива и цена в
// полосе», потолок — предохранительный. Условия проверяются на настоящем
// `Backtest` крейта (шов 6): книга управляется покадрово, время — из данных.
// -----------------------------------------------------------------------

/// План F5: вход 100 у бид-стены 99, потолок `ttl_ns`, порог стены `floor`
/// (единицы крейта) и полоса `band` bps. Нули в `floor`/`band` — прежний
/// режим «до конца касания» (условия выключены, гейт «те же круги»).
pub(super) fn f5_plan(ttl_ns: i64, floor: f64, band: f64) -> TradePlan {
    TradePlan::Bounce {
        entry_px: 100.0,
        stop_px: 90.0,
        take_px: 110.0,
        deadline_ns: 60 * S,
        entry_ttl_ns: ttl_ns,
        level_floor_qty: floor,
        band_exit_bps: band,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_px: 0.0,
        // F6: лестницы формы нет — прежний вход `entry_px` (гейт).
        ladder: EntryLadder::NONE,
        early_exit_ns: 0,
        level_px: 99.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 0.0,
        eaten_all_pct: 0.0,
        eaten_half_frac: 0.0,
        level_qty: 0.0,
        lot_qty: 1.0,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        wall_eat: crate::lob::strategy::WallEatExit::OFF,
        pyramid: crate::lob::strategy::PyramidCfg::OFF,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_stop: crate::lob::strategy::GoneStop::Off,
    }
}

/// Прогон с метками времени стороны на каждом вызове `on_event` — для
/// проверки «вход жил ровно потолок» (F5): по меткам видно, когда вход
/// отправлен и когда снят.
pub(super) fn drive_stamped(
    hbt: &mut Backtest<FastMarketDepth>,
    state: &mut StrategyState,
) -> Vec<(i64, Action)> {
    let mut out = Vec::new();
    loop {
        let r = hbt.elapse(100_000_000).unwrap();
        let ts = hbt.current_timestamp();
        out.push((ts, on_event(hbt, state).unwrap()));
        if r == ElapseResult::EndOfData {
            break;
        }
    }
    out
}

/// Первая причина снятия входа в действиях круга.
pub(super) fn timeout_reason(actions: &[Action]) -> Option<EntryCancelReason> {
    actions.iter().find_map(|a| match a {
        Action::EntryTimedOut { reason, .. } => Some(*reason),
        _ => None,
    })
}

/// F5 (В-74): стена снята — размер на цене уровня (99) упал с 10 до 4 без
/// сделок, то есть плотность **убрали** (порог 5). Вход (потолок 30 с) не
/// ждёт потолка: все ноги снимаются сразу, причина `WallDead`.
#[test]
pub(super) fn a_dead_wall_cancels_the_entry_before_the_ceiling() {
    let feed = [
        depth_at(0, true, 99.0, 10.0),
        depth_at(0, false, 101.0, 5.0),
        // Плотность убрали: размер 4 < порога 5, сделок не было (не съедание).
        depth_at(2 * S, true, 99.0, 4.0),
        depth_at(6 * S, false, 102.0, 5.0),
    ];
    let mut hbt = seam6_backtest(&feed);
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 1.0, 1, f5_plan(30 * S, 5.0, 200.0));

    let actions = drive(&mut hbt, &mut state);

    assert_eq!(
        timeout_reason(&actions),
        Some(EntryCancelReason::WallDead),
        "вход обязан сняться по смерти стены, а не по потолку: {actions:?}"
    );
    assert_eq!(hbt.position(0), 0.0, "позиции не было");
}

/// F5 (В-74): цена ушла из полосы — лучший бид (102) выше дальней ноги
/// лестницы (100) на 200 bps при полосе 20; стена (99, размер 10) жива.
/// Вход снимается, причина `PriceLeft`.
#[test]
pub(super) fn a_price_that_left_the_band_cancels_the_entry() {
    let feed = [
        depth_at(0, true, 99.0, 10.0),
        depth_at(0, false, 101.0, 5.0),
        // Цена ушла вверх далеко за ногу 100 — вход больше не исполнится.
        depth_at(2 * S, true, 102.0, 5.0),
        depth_at(2 * S, false, 103.0, 5.0),
        depth_at(6 * S, false, 103.0, 5.0),
    ];
    let mut hbt = seam6_backtest(&feed);
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 1.0, 1, f5_plan(30 * S, 5.0, 20.0));

    let actions = drive(&mut hbt, &mut state);

    assert_eq!(
        timeout_reason(&actions),
        Some(EntryCancelReason::PriceLeft),
        "вход обязан сняться по уходу цены из полосы: {actions:?}"
    );
    assert_eq!(hbt.position(0), 0.0, "позиции не было");
}

/// F5 (В-74): стена жива и цена в полосе — вход стоит ровно потолок
/// `entry_ttl_ns` и снимается им, причина `Ttl`. Метки времени доказывают,
/// что снятие не раньше потолка (плюс шаг опроса 100 мс и RTT отмены).
#[test]
pub(super) fn the_ceiling_cancels_an_entry_that_lived_its_full_ttl() {
    let feed = [
        depth_at(0, true, 99.0, 10.0),
        depth_at(0, false, 101.0, 5.0),
        // Тихая книга: ни стена не умирает, ни цена не уходит.
        depth_at(10 * S, false, 101.0, 5.0),
    ];
    let mut hbt = seam6_backtest(&feed);
    let ttl = 3 * S;
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 1.0, 1, f5_plan(ttl, 5.0, 200.0));

    let stamped = drive_stamped(&mut hbt, &mut state);
    let entry_ts = stamped
        .iter()
        .find_map(|(ts, a)| matches!(a, Action::EntrySubmitted { .. }).then_some(*ts))
        .expect("вход обязан отправиться");
    let timeout_ts = stamped
        .iter()
        .find_map(|(ts, a)| matches!(a, Action::EntryTimedOut { .. }).then_some(*ts))
        .expect("вход обязан сняться потолком");
    assert_eq!(
        timeout_reason(&stamped.iter().map(|(_, a)| *a).collect::<Vec<_>>()),
        Some(EntryCancelReason::Ttl)
    );
    let lived = timeout_ts - entry_ts;
    assert!(
        lived >= ttl,
        "потолок снимает не раньше срока: вход жил {lived} нс при потолке {ttl}"
    );
    assert!(
        lived <= ttl + 300_000_000,
        "вход жил заметно дольше потолка: {lived} нс при потолке {ttl}"
    );
    assert_eq!(hbt.position(0), 0.0, "позиции не было");
}

/// F5 (В-74), гейт «те же круги»: прежний режим входа (`touch`,
/// `level_floor_qty`/`band_exit_bps` = 0) не читает ни смерть стены, ни уход
/// цены — вход снимается только потолком `entry_ttl_ns`, как было.
#[test]
pub(super) fn the_touch_mode_ignores_the_wall_and_the_band() {
    let feed = [
        depth_at(0, true, 99.0, 10.0),
        depth_at(0, false, 101.0, 5.0),
        depth_at(2 * S, true, 99.0, 4.0),
        depth_at(3 * S, true, 102.0, 5.0),
        depth_at(3 * S, false, 103.0, 5.0),
        depth_at(8 * S, false, 103.0, 5.0),
    ];
    let mut hbt = seam6_backtest(&feed);
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 1.0, 1, f5_plan(5 * S, 0.0, 0.0));

    let actions = drive(&mut hbt, &mut state);

    assert_eq!(
        timeout_reason(&actions),
        Some(EntryCancelReason::Ttl),
        "прежний режим снимается только потолком: {actions:?}"
    );
    assert!(
        !actions.iter().any(|a| matches!(
            a,
            Action::EntryTimedOut {
                reason: EntryCancelReason::WallDead | EntryCancelReason::PriceLeft,
                ..
            }
        )),
        "в режиме touch условия F5 выключены: {actions:?}"
    );
    assert_eq!(hbt.position(0), 0.0, "позиции не было");
}

// -----------------------------------------------------------------------
// F6 (план 2026-09-20, §3): лестница входа как форма — ноги заданы целыми
// тиками и долями (вес к стене), нога, пересекшая спред, биржей не ставится.
// -----------------------------------------------------------------------

/// План с лестницей формы: две ноги — 96 (вес 2/3) и 101 (1/3) при аске 100,
/// вход живёт `ttl`, пост-онли.
pub(super) fn ladder_plan(ttl_ns: i64) -> TradePlan {
    let mut ladder = EntryLadder::NONE;
    assert!(ladder.push(96, 2.0 / 3.0));
    assert!(ladder.push(101, 1.0 / 3.0));
    TradePlan::Bounce {
        entry_px: 98.0,
        stop_px: 90.0,
        take_px: 110.0,
        deadline_ns: 30 * S,
        entry_ttl_ns: ttl_ns,
        post_only: true,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_px: 0.0,
        ladder,
        early_exit_ns: 0,
        level_floor_qty: 0.0,
        band_exit_bps: 0.0,
        level_px: 95.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 0.0,
        eaten_all_pct: 0.0,
        eaten_half_frac: 0.0,
        level_qty: 0.0,
        lot_qty: 1.0,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        wall_eat: crate::lob::strategy::WallEatExit::OFF,
        pyramid: crate::lob::strategy::PyramidCfg::OFF,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_stop: crate::lob::strategy::GoneStop::Off,
    }
}

/// F6 (В-73): ноги лестницы ставятся по **своим** тикам и долям — нижняя
/// нога (к стене) весит вдвое, а нога, цена которой перекрыла лучший аск
/// (101 при аске 100), пост-онли `GTX` не ставится: биржа отвечает `Expired`,
/// и в позиции остаётся исполненная нижняя нога.
#[test]
pub(super) fn ladder_legs_use_their_own_ticks_and_weights_and_a_crossing_leg_is_rejected() {
    let feed = [
        // Книга стоит **ниже** лестницы: у ноги 96 нет чужой очереди впереди,
        // и вход решает объём сделок, а не глубина стакана (приём F4).
        depth_at(0, true, 95.0, 5.0),
        // Аск 100: нога 101 пересекает спред (пост-онли её не поставит).
        depth_at(0, false, 100.0, 5.0),
        // Продажа 5.0 ровно в ногу 96 (нога 2.0, очередь впереди пуста).
        trade_at(S, true, 96.0, 5.0),
        depth_at(4 * S, true, 95.0, 5.0),
        depth_at(4 * S, false, 100.0, 5.0),
    ];
    let mut hbt = seam6_backtest(&feed);
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 3.0, 1, ladder_plan(20 * S));

    let actions = drive(&mut hbt, &mut state);

    assert!(
        actions
            .iter()
            .any(|a| matches!(a, Action::EntrySubmitted { .. })),
        "вход обязан быть поставлен: {actions:?}"
    );
    let lower = hbt.orders(0).get(&1).expect("нижняя нога в учёте");
    assert_eq!(lower.price_tick, 96, "нога плана — свой целый тик");
    assert!(
        close(lower.qty, 2.0),
        "доля нижней ноги 2/3 от 3.0: {}",
        lower.qty
    );
    assert_eq!(lower.status, Status::Filled, "сделка по 96 исполняет ногу");
    let crossing = hbt.orders(0).get(&2).expect("вторая нога в учёте");
    assert_eq!(crossing.price_tick, 101, "нога плана — свой целый тик");
    assert!(
        close(crossing.qty, 1.0),
        "доля второй ноги 1/3 от 3.0: {}",
        crossing.qty
    );
    assert!(
        matches!(crossing.status, Status::Expired | Status::Rejected),
        "нога выше лучшего аска не ставится: {:?}",
        crossing.status
    );
    assert!(
        close(hbt.position(0), 2.0),
        "позиция — исполненная нога: {}",
        hbt.position(0)
    );
}

// -----------------------------------------------------------------------
// R3 (ревью 23.09): ноги лестницы входа — целыми шагами лота. `state.qty *
// ladder.frac[j]` (или `qty / legs` у прежнего входа) даёт дробный лот
// почти всегда — Bybit его не примет. `ladder_leg_qtys` — единственное
// место округления, тесты на нём прямые (без бэктеста).
// -----------------------------------------------------------------------

/// Доли 0.5/0.25/0.25 при `qty` = 7 шагов лота: вниз до целого, остаток —
/// на самую тяжёлую (первую) ногу. `5+1+1=7`, ни один лот не потерян и не
/// придуман.
#[test]
pub(super) fn ladder_leg_qtys_rounds_shares_to_whole_lot_steps_with_remainder_on_the_heaviest_leg()
{
    let fracs = [0.5, 0.25, 0.25, 0.0, 0.0, 0.0, 0.0, 0.0];
    let out = ladder_leg_qtys(7.0, 1.0, &fracs, 3);
    assert_eq!(&out[..3], &[5.0, 1.0, 1.0], "{out:?}");
    assert!(
        (out[..3].iter().sum::<f64>() - 7.0).abs() < 1e-9,
        "сумма ног равна qty: {out:?}"
    );
}

/// `qty` = 1 шаг лота: доля каждой ноги (0.5/0.25/0.25 от одного шага) вниз
/// до целого — ноль, весь шаг идёт на самую тяжёлую ногу; остальные две
/// нулевые (в `on_idle` — не ставятся, «одна нога»).
#[test]
pub(super) fn ladder_leg_qtys_of_a_single_step_gives_the_whole_step_to_one_leg() {
    let fracs = [0.5, 0.25, 0.25, 0.0, 0.0, 0.0, 0.0, 0.0];
    let out = ladder_leg_qtys(1.0, 1.0, &fracs, 3);
    assert_eq!(&out[..3], &[1.0, 0.0, 0.0], "{out:?}");
}

/// Сумма ног всегда равна `qty` — на сетке произвольных долей и шага лота
/// (не только на круглых числах предыдущих тестов): `total_steps` целых
/// шагов растаскиваются без остатка, откуда бы доля ни пришла.
#[test]
pub(super) fn ladder_leg_qtys_always_sums_to_qty() {
    let cases: [(f64, f64, [f64; 3]); 4] = [
        (10.0, 1.0, [0.34, 0.33, 0.33]),
        (23.0, 0.5, [1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0]),
        (5.0, 0.1, [0.2, 0.3, 0.5]),
        (0.9, 0.3, [0.5, 0.25, 0.25]),
    ];
    for (qty, lot, fracs3) in cases {
        let fracs = [fracs3[0], fracs3[1], fracs3[2], 0.0, 0.0, 0.0, 0.0, 0.0];
        let out = ladder_leg_qtys(qty, lot, &fracs, 3);
        let sum: f64 = out[..3].iter().sum();
        assert!(
            (sum - qty).abs() < 1e-9,
            "qty={qty} lot={lot} fracs={fracs3:?}: сумма ног {sum}, ожидали {qty}"
        );
        for q in &out[..3] {
            assert!(
                *q == 0.0 || (*q / lot - (*q / lot).round()).abs() < 1e-6,
                "нога {q} — не целое число шагов лота {lot}"
            );
        }
    }
}

/// Шаг лота неизвестен (`lot_qty = 0.0`, план без данных пула) — округление
/// выключено, доли остаются как раньше (`qty × frac`), гейт «те же круги»
/// формы без пула не задет.
#[test]
pub(super) fn ladder_leg_qtys_without_a_known_lot_step_falls_back_to_plain_fractions() {
    let fracs = [0.5, 0.25, 0.25, 0.0, 0.0, 0.0, 0.0, 0.0];
    let out = ladder_leg_qtys(7.0, 0.0, &fracs, 3);
    assert_eq!(&out[..3], &[3.5, 1.75, 1.75], "{out:?}");
}

/// Интеграционно (F6, через `on_idle`/`drive`): `qty` — ровно один шаг лота
/// при долях 0.5/0.25/0.25 — ставится **одна** заявка (на самую тяжёлую
/// ногу), не три. Прежний код звал `submit_*_order` на все три доли, и
/// биржа получала два ордера меньше `qty_step` (`minQty`, отказ).
#[test]
pub(super) fn on_idle_submits_a_single_order_when_only_one_leg_gets_a_whole_lot_step() {
    let mut ladder = EntryLadder::NONE;
    assert!(ladder.push(96, 0.5));
    assert!(ladder.push(97, 0.25));
    assert!(ladder.push(98, 0.25));
    let plan = TradePlan::Bounce {
        entry_px: 98.0,
        stop_px: 90.0,
        take_px: 110.0,
        deadline_ns: 30 * S,
        entry_ttl_ns: 20 * S,
        post_only: true,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_px: 0.0,
        ladder,
        early_exit_ns: 0,
        level_floor_qty: 0.0,
        band_exit_bps: 0.0,
        level_px: 95.0,
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
    };
    let feed = [depth_at(0, true, 95.0, 5.0), depth_at(0, false, 100.0, 5.0)];
    let mut hbt = seam6_backtest(&feed);
    // `qty = 1.0` — ровно один шаг лота (`lot_qty = 1.0`).
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 1.0, 1, plan);

    let actions = drive(&mut hbt, &mut state);

    assert!(
        actions
            .iter()
            .any(|a| matches!(a, Action::EntrySubmitted { .. })),
        "вход обязан быть поставлен: {actions:?}"
    );
    let submitted = (1..=3).filter(|id| hbt.orders(0).get(id).is_some()).count();
    assert_eq!(
        submitted,
        1,
        "одна нога на весь шаг лота, не три дробных: {:?}",
        (1..=3)
            .filter_map(|id| hbt.orders(0).get(&id).map(|o| (id, o.qty)))
            .collect::<Vec<_>>()
    );
    let lower = hbt.orders(0).get(&1).expect("самая тяжёлая нога стоит");
    assert!(
        close(lower.qty, 1.0),
        "весь шаг лота на одну ногу: {}",
        lower.qty
    );
}

/// Аудит 21.09, Б1: лимитка тейка стоит в рынке исполненной **частично**
/// (модель очереди по объёму), а цена уходит к стопу. Раньше `ExitPending`
/// ждала эту лимитку до конца записи — без стопа и дедлайна, и круг терял все
/// дальнейшие сигналы суток. Теперь остаток под стоящей лимиткой решается
/// рыночными причинами: лимитка снимается, остаток закрывается тейкером с
/// причиной `Stop`, круг возвращается в `Idle`.
#[test]
pub(super) fn a_partially_filled_take_is_cancelled_and_the_rest_is_stopped_out() {
    let feed = [
        depth_at(0, true, 98.0, 5.0),
        depth_at(0, false, 110.0, 5.0),
        // Дальняя нога (101) исполняется целиком сделкой больше ноги.
        trade_at(2 * S, true, 101.0, 1.5),
        // Вход живёт 5 с → `Holding` с позицией 1.0, средняя 101: стоп 97, тейк 105.
        depth_at(6 * S, true, 98.0, 0.0),
        depth_at(6 * S, true, 99.0, 5.0),
        // Бид дошёл до тейка — стратегия ставит лимитку продажи на 105…
        depth_at(10 * S, true, 105.0, 5.0),
        // …стратегия видит это на ближайшем шаге `drive` (шаги по 100 мс от
        // старта записи; здесь — 10,002 с) и шлёт заявку; пока она летит
        // (1 мс), бид отступает — лимитка **встаёт** в рынок на 105 мейкером,
        // впереди на 105 никого. Момент отступления — между отправкой и
        // приходом на биржу.
        depth_at(10 * S + 2_500_000, true, 105.0, 0.0),
        depth_at(10 * S + 2_500_000, true, 104.0, 5.0),
        // Покупатель берёт с 105 только 0.3 — тейк исполнен частично, 0.7 стоит.
        trade_at(12 * S, false, 105.0, 0.3),
        // Цена уходит к стопу: бид 96 ≤ 97 (99 с шага 6 с тоже снимается).
        depth_at(14 * S, true, 104.0, 0.0),
        depth_at(14 * S, true, 99.0, 0.0),
        depth_at(14 * S, true, 96.0, 5.0),
        // Хвост: ответ на отмену и на рыночный выход.
        depth_at(15 * S, false, 110.0, 5.0),
        depth_at(16 * S, false, 110.0, 5.0),
        depth_at(17 * S, false, 110.0, 5.0),
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

    let exits: Vec<(u64, f64, ExitReason)> = actions
        .iter()
        .filter_map(|a| match a {
            Action::ExitSubmitted {
                order_id,
                price,
                reason,
                ..
            } => Some((*order_id, *price, *reason)),
            _ => None,
        })
        .collect();
    assert_eq!(
        exits.len(),
        2,
        "тейк лимитом, затем стоп по рынку: {actions:?}"
    );
    let (take_id, take_px, take_reason) = exits[0];
    let (stop_id, _, stop_reason) = exits[1];
    assert_eq!(take_reason, ExitReason::Take);
    assert!(close(take_px, 105.0), "тейк от средней 101 + 4: {take_px}");
    assert_eq!(stop_reason, ExitReason::Stop);
    let take = hbt.orders(0).get(&take_id).expect("лимитка тейка в учёте");
    assert!(
        close(take.exec_qty, 0.3),
        "тейк исполнен частично (0.3): {}",
        take.exec_qty
    );
    assert_eq!(
        take.status,
        Status::Canceled,
        "частично исполненная лимитка тейка снята, а не ждёт до конца записи"
    );
    let stop = hbt.orders(0).get(&stop_id).expect("рыночный выход в учёте");
    assert!(
        close(stop.qty, 0.7),
        "остаток после частичного тейка закрыт целиком: {}",
        stop.qty
    );
    assert_eq!(stop.status, Status::Filled);
    // Круг закрыт: позиции нет (стратегия уже свободна и на хвосте фида
    // ставит следующий вход — это ожидаемо для синтетического плана).
    assert!(
        state.position() <= 0.0,
        "круг закрыт, позиции нет: {:?}",
        state
    );
}
