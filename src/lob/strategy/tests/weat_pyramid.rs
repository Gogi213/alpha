//! Тесты стратегии — TK-014/R2-A: съедание стены после входа, пирамида.

use super::*;

// -----------------------------------------------------------------------
// TK-014: выход `weat<X>s<W>{m|l|a}<Y>` — съедание стены после входа за окно `W`,
// причина раздельно по ходу BTC (`docs/findings/tk014-design-2026-09-28.md`).
// -----------------------------------------------------------------------

pub(super) fn weat_plan(pct: f64, secs: u32, mode: WallEatMode, btc_bps: f64) -> TradePlan {
    let mut p = f7_plan(0.0, 0.0, 100.0);
    if let TradePlan::Bounce { wall_eat, .. } = &mut p {
        *wall_eat = WallEatExit {
            pct,
            secs,
            mode,
            btc_bps,
            btc: None,
        };
    }
    p
}

/// Без формы `weat*` кольца нет — прежний путь; с формой — кольцо ёмкостью `W`.
#[test]
pub(super) fn wall_ring_is_allocated_only_for_weat_plans() {
    let off = StrategyState::with_plan(0, SIGMA_LONG, 1.0, 1, f7_plan(50.0, 0.0, 100.0));
    assert!(off.wall_ring.is_none());
    let on = StrategyState::with_plan(
        0,
        SIGMA_LONG,
        1.0,
        1,
        weat_plan(30.0, 10, WallEatMode::Any, 5.0),
    );
    assert_eq!(on.wall_ring.as_ref().map(|r| r.len()), Some(10));
    let mut forked = off.clone();
    forked.set_plan(weat_plan(30.0, 7, WallEatMode::Any, 5.0));
    assert_eq!(forked.wall_ring.as_ref().map(|r| r.len()), Some(7));
}

/// Окно `[max(вход, t − W), t]`: сделки до входа, не по цене стены и не той стороны не
/// считаются; сделки и максимум размера старше `W` секунд выпадают.
#[test]
pub(super) fn weat_window_counts_only_post_entry_wall_trades_within_w() {
    let mut state = StrategyState::with_plan(
        0,
        SIGMA_LONG,
        1.0,
        1,
        weat_plan(50.0, 10, WallEatMode::Any, 5.0),
    );
    state.phase = Phase::Holding { entry_ns: 100 * S };
    let trades = [
        trade_at(95 * S, true, 99.0, 500.0),   // до входа
        trade_at(101 * S, true, 99.0, 30.0),   // в стену
        trade_at(101 * S, true, 98.0, 700.0),  // не на цене стены
        trade_at(102 * S, false, 99.0, 900.0), // покупатель — стену лонга не ест
        trade_at(105 * S, true, 99.0, 30.0),   // в стену
    ];
    state.observe_wall_trades(&trades);
    state.wall_bucket(101).unwrap().max_qty = 100.0;
    assert_eq!(state.wall_window(100 * S, 106 * S), (60.0, 100.0));
    // t = 112 с, W = 10: окно [103, 112] — сделка 101 с и максимум 101 с выпали.
    assert_eq!(state.wall_window(100 * S, 112 * S), (30.0, 0.0));
    // Вход позже: окно не раньше секунды входа.
    assert_eq!(state.wall_window(104 * S, 106 * S), (30.0, 0.0));
}

/// Ход BTC: последняя закрытая минута против `ceil(W/60)` минут раньше; пропуск — ближайшая
/// более ранняя; данных нет — `None`.
#[test]
pub(super) fn btc_move_uses_last_closed_minute_and_earlier_on_gaps() {
    const M: i64 = 60_000;
    let rows = vec![
        (7 * M, 100.0),
        (9 * M, 99.0),
        (6 * M, 101.0),
        (9 * M, 55.0),
        (10 * M, 1.0),
    ];
    let b = BtcMinutes::from_rows(rows);
    let t = (10 * M + 30_000) * 1_000_000; // 10:30 — минута 10 ещё не закрыта
                                           // W = 60: минута 9 (99) против минуты 8 — её нет, берётся 7 (100): −100 bps.
    let mv = b.move_bps(t, 60).unwrap();
    assert!((mv - (-100.0)).abs() < 1e-9, "{mv}");
    // W = 120: k = 2 — минута 7 (100) тоже.
    assert!((b.move_bps(t, 120).unwrap() - (-100.0)).abs() < 1e-9);
    // W = 180: k = 3 — минута 6 (101).
    assert!((b.move_bps(t, 180).unwrap() - (99.0 / 101.0 - 1.0) * 10_000.0).abs() < 1e-9);
    // Раньше ряда — нет хода.
    assert!(b.move_bps(t, 3600).is_none());
    assert_eq!(b.span_ms(), Some((6 * M, 10 * M)));
}

/// Режимы m/l/a × BTC ниже/выше −Y: причина раздельная, режим отсекает свой случай.
#[test]
pub(super) fn weat_mode_splits_market_and_local_by_btc_threshold() {
    let w = |mode| WallEatExit {
        pct: 30.0,
        secs: 60,
        mode,
        btc_bps: 10.0,
        btc: None,
    };
    use WallEatMode::{Any, Local, Market};
    assert_eq!(
        wall_eat_reason_for(w(Market), -10.0),
        Some(ExitReason::WallEatBtc)
    );
    assert_eq!(wall_eat_reason_for(w(Market), -9.9), None);
    assert_eq!(wall_eat_reason_for(w(Local), -10.0), None);
    assert_eq!(
        wall_eat_reason_for(w(Local), 5.0),
        Some(ExitReason::WallEatLocal)
    );
    assert_eq!(
        wall_eat_reason_for(w(Any), -30.0),
        Some(ExitReason::WallEatBtc)
    );
    assert_eq!(
        wall_eat_reason_for(w(Any), -9.9),
        Some(ExitReason::WallEatLocal)
    );
}

// -----------------------------------------------------------------------
// R2-A (TK-065, Г-94): доливка `pyeat<N>` — `pyramid_step` / `pyramid_release` напрямую.
// -----------------------------------------------------------------------

pub(super) fn pyr_plan(parts: u8) -> TradePlan {
    let mut plan = f7_plan(0.0, 0.0, 30.0);
    if let TradePlan::Bounce { pyramid, .. } = &mut plan {
        *pyramid = PyramidCfg {
            eat_parts: parts,
            ..PyramidCfg::OFF
        };
    }
    plan
}

/// Состояние в удержании: позиция 9, стена на входе 30, съедено `eaten`; книга готова.
pub(super) fn pyr_state(parts: u8, eaten: f64) -> (Backtest<FastMarketDepth>, StrategyState) {
    let feed = [
        depth_at(0, true, 99.0, 30.0),
        depth_at(0, false, 101.0, 5.0),
        depth_at(S, false, 102.0, 5.0),
    ];
    let mut hbt = seam6_backtest(&feed);
    hbt.elapse(100_000_000).unwrap();
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 9.0, 1, pyr_plan(parts));
    state.entry_qty = 9.0;
    state.entry_notional = 9.0 * 100.0;
    state.level_qty_at_entry = 30.0;
    state.eaten_qty = eaten;
    (hbt, state)
}

#[test]
#[cfg(feature = "r2")]
pub(super) fn level_shift_follows_the_base_entry_not_the_adds_but_breakeven_follows_all_orders() {
    // База 9 @ 100, добавка 3 @ 96: общая средняя 99, база 100; тик 0,5.
    let (_hbt, mut state) = pyr_state(3, 11.0);
    state.adds_done = 1;
    state.add_base_qty = 9.0;
    state.add_base_notional = 900.0;
    state.entry_qty = 12.0;
    state.entry_notional = 900.0 + 3.0 * 96.0;
    let (base, all) = (state.base_entry_vwap(), state.entry_vwap());
    assert!(
        close(level_shift(base, 100.0, 0.5), 0.0),
        "стоп/тейк на месте"
    );
    assert!(
        close(level_shift(all, 100.0, 0.5), -1.0),
        "общая средняя сдвинулась на 2 тика — безубыток считается от неё"
    );
    state.adds_done = 0;
    assert!(
        close(level_shift(state.base_entry_vwap(), 100.0, 0.5), -1.0),
        "без добавок база = общая средняя"
    );
}

#[test]
#[cfg(feature = "r2")]
pub(super) fn fresh_entry_is_one_part_floored_to_the_lot_and_adds_are_q0_over_n() {
    let mut plan = pyr_plan(3);
    if let TradePlan::Bounce {
        pyramid, lot_qty, ..
    } = &mut plan
    {
        pyramid.fresh = true;
        *lot_qty = 1.0;
    }
    assert!(close(fresh_entry_qty(plan, 10.0), 3.0), "10/3 вниз до лота");
    assert!(close(fresh_entry_qty(plan, 2.0), 1.0), "не меньше лота");
    let off = pyr_plan(3);
    assert!(
        close(fresh_entry_qty(off, 10.0), 10.0),
        "без fresh — как есть"
    );
    let (mut hbt, mut state) = pyr_state(3, 11.0);
    state.plan = plan;
    state.entry_qty = 3.0;
    state.entry_notional = 300.0;
    state
        .pyramid_step(&mut hbt, 99.0, 101.0, HbtSide::Buy)
        .unwrap();
    let id = state.add_order_ids()[0];
    let o = hbt.orders(0).get(&id).expect("добавка в рынке");
    assert!(close(o.qty, 3.0), "Q0/N = 9/3, а не base/N = 1: {}", o.qty);
}

pub(super) fn reinst_state(n: u8, u3: u8) -> (Backtest<FastMarketDepth>, StrategyState) {
    let (hbt, mut state) = pyr_state(0, 0.0);
    if let TradePlan::Bounce {
        pyramid, lot_qty, ..
    } = &mut state.plan
    {
        pyramid.reinstall_n = n;
        pyramid.reinstall_u3 = u3;
        *lot_qty = 1.0;
    }
    (hbt, state)
}

pub(super) fn wall_now(qty: f64) -> WallNow {
    WallNow {
        ok: true,
        qty,
        eaten_pct: 0.0,
    }
}

#[test]
pub(super) fn reinstall_counts_returns_only_after_a_removal_and_adds_one_per_return() {
    let (mut hbt, mut state) = reinst_state(3, 1);
    state.observe_reinstall(wall_now(50.0), 10.0);
    assert_eq!(state.reinstalls, 0, "стена не снималась — возврата нет");
    state.observe_reinstall(wall_now(2.0), 10.0);
    state.observe_reinstall(wall_now(3.0), 10.0);
    assert_eq!(state.reinstalls, 0, "снята и не вернулась");
    state.observe_reinstall(wall_now(40.0), 10.0);
    assert_eq!(state.reinstalls, 1);
    state
        .pyramid_reinstall_step(&mut hbt, 99.0, 101.0, HbtSide::Buy)
        .unwrap();
    assert_eq!(state.add_order_ids().len(), 1);
    let id = state.add_order_ids()[0];
    let o = hbt.orders(0).get(&id).expect("добавка в рынке");
    assert!(close(o.qty, 3.0), "u = 1/3 · Q0 = 3: {}", o.qty);
    // Второй возврат при висящей добавке: слот занят, adds_done не растёт, возврат в счёт N входит.
    state.observe_reinstall(wall_now(1.0), 10.0);
    state.observe_reinstall(wall_now(40.0), 10.0);
    assert_eq!(state.reinstalls, 2);
    state
        .pyramid_reinstall_step(&mut hbt, 99.0, 101.0, HbtSide::Buy)
        .unwrap();
    assert_eq!(
        state.add_order_ids().len(),
        1,
        "висящая добавка занимает слот"
    );
}

#[test]
pub(super) fn reinstall_moves_the_stop_to_breakeven_only_after_n_returns_and_when_price_is_there() {
    let (_hbt, mut state) = reinst_state(1, 1);
    assert!(
        close(state.reinstall_stop(HbtSide::Buy, 100.0, 105.0, 98.0), 98.0),
        "до N-го возврата стоп прежний"
    );
    state.observe_reinstall(wall_now(1.0), 10.0);
    state.observe_reinstall(wall_now(40.0), 10.0);
    let be = 100.0 * (1.0 + crate::lob::costs::ROUNDTRIP_FEES_BPS / 10_000.0);
    assert!(
        close(state.reinstall_stop(HbtSide::Buy, 100.0, 99.0, 98.0), 98.0),
        "цена хуже безубытка — прежний стоп (мягкий режим)"
    );
    assert!(
        close(
            state.reinstall_stop(HbtSide::Buy, 100.0, be + 0.5, 98.0),
            be
        ),
        "у безубытка — переезд"
    );
    assert!(
        close(state.reinstall_stop(HbtSide::Buy, 100.0, 99.0, 98.0), be),
        "защёлка: стоп остаётся в безубытке"
    );
}

pub(super) fn newwall_state(
    feed: &[Event],
    k: u8,
    u3: u8,
) -> (Backtest<FastMarketDepth>, StrategyState) {
    let mut hbt = seam6_backtest(feed);
    hbt.elapse(100_000_000).unwrap();
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 9.0, 1, pyr_plan(0));
    state.entry_qty = 9.0;
    state.entry_notional = 9.0 * 100.0;
    state.level_qty_at_entry = 30.0;
    if let TradePlan::Bounce { pyramid, .. } = &mut state.plan {
        pyramid.newwall_k = k;
        pyramid.newwall_u3 = u3;
    }
    (hbt, state)
}

pub(super) fn observe_nw(hbt: &Backtest<FastMarketDepth>, state: &mut StrategyState, bid: f64) {
    state.observe_newwall(
        hbt.depth(0),
        HbtSide::Buy,
        90.0,
        99.0,
        1.0,
        10.0,
        bid,
        101.0,
    );
}

#[test]
pub(super) fn newwall_triggers_only_on_a_big_level_born_after_the_first_hold_event_between_stop_and_market(
) {
    let feed = [
        depth_at(0, true, 99.0, 30.0),
        depth_at(0, true, 94.0, 20.0),
        depth_at(0, false, 101.0, 5.0),
        depth_at(2 * S, true, 95.0, 20.0),
        depth_at(3 * S, true, 85.0, 50.0),
    ];
    let (mut hbt, mut state) = newwall_state(&feed, 2, 3);
    observe_nw(&hbt, &mut state, 99.0);
    assert!(!state.reinst_trigger, "первое событие — только снимок");
    hbt.elapse(S).unwrap();
    observe_nw(&hbt, &mut state, 99.0);
    assert!(!state.reinst_trigger, "стена на 94 была к снимку");
    hbt.elapse(2 * S).unwrap();
    observe_nw(&hbt, &mut state, 100.5);
    assert!(!state.reinst_trigger, "позиция не в убытке — триггера нет");
    observe_nw(&hbt, &mut state, 99.0);
    assert!(
        state.reinst_trigger,
        "новая стена на 95 в убыточной позиции"
    );
    state.reinst_trigger = false;
    observe_nw(&hbt, &mut state, 99.0);
    assert!(!state.reinst_trigger, "одна стена — один триггер");
    hbt.elapse(2 * S).unwrap();
    observe_nw(&hbt, &mut state, 99.0);
    assert!(!state.reinst_trigger, "стена за стопом (85) — не триггер");
}

#[test]
pub(super) fn newwall_add_is_u_times_q0_and_capped_by_k() {
    let feed = [
        depth_at(0, true, 99.0, 30.0),
        depth_at(0, false, 101.0, 5.0),
        depth_at(S, true, 95.0, 20.0),
        depth_at(2 * S, true, 96.0, 20.0),
    ];
    let (mut hbt, mut state) = newwall_state(&feed, 1, 1);
    observe_nw(&hbt, &mut state, 99.0);
    hbt.elapse(S).unwrap();
    observe_nw(&hbt, &mut state, 99.0);
    state
        .pyramid_reinstall_step(&mut hbt, 99.0, 101.0, HbtSide::Buy)
        .unwrap();
    assert_eq!(state.add_order_ids().len(), 1);
    let id = state.add_order_ids()[0];
    let o = hbt.orders(0).get(&id).expect("добавка в рынке");
    assert!(close(o.qty, 3.0), "u = 1/3 · Q0 = 3: {}", o.qty);
    hbt.elapse(S).unwrap();
    observe_nw(&hbt, &mut state, 99.0);
    state
        .pyramid_reinstall_step(&mut hbt, 99.0, 101.0, HbtSide::Buy)
        .unwrap();
    assert_eq!(state.add_order_ids().len(), 1, "K = 1 исчерпано");
}

#[test]
pub(super) fn pyramid_off_never_adds() {
    let (mut hbt, mut state) = pyr_state(0, 25.0);
    state
        .pyramid_step(&mut hbt, 99.0, 101.0, HbtSide::Buy)
        .unwrap();
    assert!(state.add_order_ids().is_empty());
}

#[test]
pub(super) fn pyramid_adds_one_third_per_third_eaten_and_never_recharges_a_share() {
    let (mut hbt, mut state) = pyr_state(3, 11.0);
    state
        .pyramid_step(&mut hbt, 99.0, 101.0, HbtSide::Buy)
        .unwrap();
    assert_eq!(
        state.add_order_ids().len(),
        1,
        "съедено 11/30 ≥ 1/3 — первая добавка"
    );
    let id = state.add_order_ids()[0];
    let o = hbt.orders(0).get(&id).expect("добавка в рынке");
    assert!(close(o.qty, 3.0), "Q0/N = 9/3: {}", o.qty);
    assert!(
        close(o.price_tick as f64 * o.tick_size, 99.0),
        "по лучшей цене нашей стороны"
    );
    // Та же доля повторно — добавки нет.
    state
        .pyramid_step(&mut hbt, 99.0, 101.0, HbtSide::Buy)
        .unwrap();
    assert_eq!(state.add_order_ids().len(), 1, "доля 1/3 не перезаряжается");
    // Съедено 2/3 — вторая; третьей не бывает (j < N).
    state.eaten_qty = 25.0;
    state
        .pyramid_step(&mut hbt, 99.0, 101.0, HbtSide::Buy)
        .unwrap();
    assert_eq!(state.add_order_ids().len(), 2);
    state
        .pyramid_step(&mut hbt, 99.0, 101.0, HbtSide::Buy)
        .unwrap();
    assert_eq!(state.add_order_ids().len(), 2, "добавок не больше N−1");
}

#[test]
pub(super) fn pyramid_one_add_per_call_when_two_thirds_jump_in_one_frame() {
    let (mut hbt, mut state) = pyr_state(3, 25.0);
    state
        .pyramid_step(&mut hbt, 99.0, 101.0, HbtSide::Buy)
        .unwrap();
    assert_eq!(state.add_order_ids().len(), 1, "за вызов — одна добавка");
}

#[test]
pub(super) fn pyramid_does_not_add_when_the_wall_is_eaten_whole() {
    let (mut hbt, mut state) = pyr_state(3, 30.0);
    state
        .pyramid_step(&mut hbt, 99.0, 101.0, HbtSide::Buy)
        .unwrap();
    assert!(
        state.add_order_ids().is_empty(),
        "стена съедена целиком — это выход, не вход"
    );
}

#[test]
pub(super) fn pyramid_release_cancels_adds_and_blocks_new_ones() {
    let (mut hbt, mut state) = pyr_state(3, 11.0);
    state
        .pyramid_step(&mut hbt, 99.0, 101.0, HbtSide::Buy)
        .unwrap();
    hbt.elapse(10_000_000).unwrap();
    state.pyramid_release(&mut hbt).unwrap();
    assert!(state.adds_released);
    state.eaten_qty = 25.0;
    state
        .pyramid_step(&mut hbt, 99.0, 101.0, HbtSide::Buy)
        .unwrap();
    assert_eq!(
        state.add_order_ids().len(),
        1,
        "после решения выхода добавок нет"
    );
}

/// С-11 (ревью 10.10): цены на границе `MarketDepth` — `f64`, собранный как `тики × шаг`; `still_at_level` обязан
/// различать соседние тики и узнавать свой на любых реальных шагах (допуск — полтика, не больше).
#[test]
pub(super) fn still_at_level_tells_adjacent_ticks_apart_for_tick_multiples() {
    for tick in [1.0, 0.1, 0.01, 0.001, 0.0001, 0.00001, 0.5, 0.05] {
        for k in [1_i64, 7, 123, 4_567, 99_999, 1_234_567] {
            let px = |n: i64| n as f64 * tick;
            let level = px(k);
            for side in [HbtSide::Buy, HbtSide::Sell] {
                let at = |n: i64| {
                    let (bid_tick, ask_tick) = if side == HbtSide::Buy {
                        (n, n + 1)
                    } else {
                        (n - 1, n)
                    };
                    let q = Quotes {
                        bid_tick,
                        ask_tick,
                        step: tick,
                        entry_side: side,
                    };
                    still_at_level(q, level, tick)
                };
                assert!(at(k), "свой тик не узнан: шаг {tick}, k {k}, {side:?}");
                assert!(!at(k + 1), "тик выше принят: шаг {tick}, k {k}, {side:?}");
                assert!(!at(k - 1), "тик ниже принят: шаг {tick}, k {k}, {side:?}");
            }
        }
    }
}
