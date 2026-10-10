use super::*;
use crate::lob::backtest::{
    build_backtest, drive_bounce, latency_from_rtt, BounceSignal, DriveConfig, ExecLatency,
    QueueModelKind, SIGMA_LONG, SIGMA_SHORT,
};

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() < 1e-9
}

use crate::lob::backtest::fast_depth::FastMarketDepth;
use hftbacktest::backtest::assettype::LinearAsset;
use hftbacktest::backtest::data::Data;
use hftbacktest::backtest::models::{
    CommonFees, ConstantLatency, RiskAdverseQueueModel, TradingValueFeeModel,
};
use hftbacktest::backtest::{Backtest, DataSource, ExchangeKind, L2AssetBuilder};
use hftbacktest::types::{
    ElapseResult, Event, EXCH_ASK_DEPTH_EVENT, EXCH_BID_DEPTH_EVENT, EXCH_BUY_TRADE_EVENT,
    EXCH_EVENT, EXCH_SELL_TRADE_EVENT, LOCAL_ASK_DEPTH_EVENT, LOCAL_BID_DEPTH_EVENT,
    LOCAL_BUY_TRADE_EVENT, LOCAL_EVENT, LOCAL_SELL_TRADE_EVENT,
};

// Синтетический поток и билдер `Backtest` — та же форма, что
// `lob::backtest`'s own test module (шов 6 требует настоящий крейтовый
// `Backtest`, а не второй фейковый `Bot<MD>`); скопировано намеренно —
// это тестовая обвязка, не стратегия, и дублирование здесь не Reinvention
// (`interfaces.md`, правило 5 — про стратегию, не про сборку фикстуры).

fn depth_ev(bid: bool) -> u64 {
    if bid {
        LOCAL_BID_DEPTH_EVENT | EXCH_BID_DEPTH_EVENT
    } else {
        LOCAL_ASK_DEPTH_EVENT | EXCH_ASK_DEPTH_EVENT
    }
}

/// TK-115 Г-112: кольцо ленты считает только сделки против позиции в окне `W`; без включения — 0.
#[cfg(feature = "r2")]
#[test]
fn tape_press_counts_adverse_trades_in_window() {
    let mut state = StrategyState::new(0, SIGMA_LONG, 1.0, 1);
    let trades = [
        trade_at(101 * S, true, 99.0, 30.0), // продажа тейкера против лонга
        trade_at(102 * S, false, 99.0, 900.0), // покупка — не против
        trade_at(105 * S, true, 98.0, 20.0),
    ];
    state.observe_wall_trades(&trades);
    assert_eq!(state.tape_press(106 * S), 0.0, "выключено");
    state.enable_tape(10);
    state.observe_wall_trades(&trades);
    assert_eq!(state.tape_press(106 * S), 50.0);
    // t = 112 с, W = 10: окно [103, 112] — сделка 101 с выпала.
    assert_eq!(state.tape_press(112 * S), 20.0);
}

/// TK-115 Г-116: отмены = убыль уровня нашей стороны минус сделки против на той же цене; рост и сделки не считаются.
#[cfg(feature = "r2")]
#[test]
fn cxl_press_counts_unexplained_depth_drop() {
    use hftbacktest::depth::L2MarketDepth;
    let mut d = FastMarketDepth::new(1.0, 1.0);
    d.update_bid_depth(99.0, 50.0, 0);
    d.update_bid_depth(98.0, 40.0, 0);
    d.update_ask_depth(101.0, 10.0, 0);
    let plan = f4_plan(96.0, 103.0, false, 60 * S, 1.0);
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 1.0, 1, plan);
    state.enable_tape(10);
    state.observe_cancels(&d, &[], 100 * S); // первый снимок — отмен нет
    d.update_bid_depth(99.0, 30.0, 0); // -20: 5 съела сделка, 15 отмена
    d.update_bid_depth(98.0, 60.0, 0); // рост — не отмена
    state.observe_cancels(&d, &[trade_at(101 * S, true, 99.0, 5.0)], 101 * S);
    assert!(close(state.cxl_press(101 * S), 15.0));
    d.update_bid_depth(98.0, 10.0, 0); // -50 без сделок
    state.observe_cancels(&d, &[], 103 * S);
    assert!(close(state.cxl_press(103 * S), 65.0));
    assert!(close(state.cxl_press(120 * S), 0.0), "окно W=10 прошло");
}

fn trade_ev(sell: bool) -> u64 {
    if sell {
        LOCAL_SELL_TRADE_EVENT | EXCH_SELL_TRADE_EVENT
    } else {
        LOCAL_BUY_TRADE_EVENT | EXCH_BUY_TRADE_EVENT
    }
}

pub(super) fn depth_at(exch_ts: i64, bid: bool, px: f64, qty: f64) -> Event {
    Event {
        ev: depth_ev(bid),
        exch_ts,
        local_ts: exch_ts + 500,
        px,
        qty,
        order_id: 0,
        ival: 0,
        fval: 0.0,
    }
}

pub(super) fn trade_at(exch_ts: i64, sell: bool, px: f64, qty: f64) -> Event {
    Event {
        ev: trade_ev(sell) | EXCH_EVENT | LOCAL_EVENT,
        exch_ts,
        local_ts: exch_ts + 500,
        px,
        qty,
        order_id: 0,
        ival: 0,
        fval: 0.0,
    }
}

pub(super) fn seam6_backtest(feed: &[Event]) -> Backtest<FastMarketDepth> {
    let (entry, response) = latency_from_rtt(1_000_000);
    Backtest::builder()
        .add_asset(
            L2AssetBuilder::default()
                .data(vec![DataSource::Data(Data::from_data(feed))])
                .latency_model(ConstantLatency::new(entry, response))
                .asset_type(LinearAsset::new(1.0))
                .fee_model(TradingValueFeeModel::new(CommonFees::new(0.0002, 0.00055)))
                .queue_model(RiskAdverseQueueModel::new())
                .exchange(ExchangeKind::NoPartialFillExchange)
                .depth(|| FastMarketDepth::new(1.0, 1.0))
                .build()
                .unwrap(),
        )
        .build()
        .unwrap()
}

pub(super) const S: i64 = 1_000_000_000;

/// Прогоняет `on_event` в цикле `elapse(шаг) -> on_event` до конца
/// синтетического фида — тот самый "прогон на `Backtest` крейта через
/// шов 6", который требует критерий приёмки. `on_event` сама не зовёт
/// `elapse` (doc модуля) — это работа вызывающего, здесь тестового цикла.
fn drive(hbt: &mut Backtest<FastMarketDepth>, state: &mut StrategyState) -> Vec<Action> {
    let mut actions = Vec::new();
    loop {
        let r = hbt.elapse(100_000_000).unwrap();
        actions.push(on_event(hbt, state).unwrap());
        if r == ElapseResult::EndOfData {
            break;
        }
    }
    actions
}

/// Круг целиком через настоящий крейтовый `Backtest`: вход мейкером на
/// бид, очередь съедена сделками за 2 с, выход тейкером ровно на
/// `t₀ + HOLD_NS`. Доказывает шов 6 — `on_event<MD, B: Bot<MD>>` работает
/// без единой правки на реализации `Bot<MD>` из `hftbacktest`, не на
/// собственном фейке.
#[test]
fn on_event_drives_a_full_maker_round_trip_through_the_crates_backtest() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        trade_at(S + S / 2, true, 100.0, 3.0),
        trade_at(S + 4 * S / 5, true, 100.0, 3.0),
        // Спред жив и после горизонта выхода — иначе выйти не на чем.
        depth_at(HOLD_NS + S + 9 * S / 10, true, 101.0, 5.0),
        depth_at(HOLD_NS + S + 9 * S / 10, false, 102.0, 5.0),
        // Хвост с запасом, чтобы ответ на выход успел дойти.
        depth_at(HOLD_NS + 3 * S, false, 103.0, 5.0),
    ];
    let mut hbt = seam6_backtest(&feed);
    let mut state = StrategyState::new(0, SIGMA_LONG, 1.0, 1);

    let actions = drive(&mut hbt, &mut state);

    assert!(
        actions
            .iter()
            .any(|a| matches!(a, Action::EntrySubmitted { .. })),
        "вход обязан был отправиться: {actions:?}"
    );
    assert!(
        actions
            .iter()
            .any(|a| matches!(a, Action::ExitSubmitted { .. })),
        "выход обязан был отправиться: {actions:?}"
    );
    let entry_at = actions
        .iter()
        .position(|a| matches!(a, Action::EntrySubmitted { .. }))
        .expect("проверено выше");
    let exit_at = actions
        .iter()
        .position(|a| matches!(a, Action::ExitSubmitted { .. }))
        .expect("проверено выше");
    assert!(
        entry_at < exit_at,
        "выход обязан идти после входа: {actions:?}"
    );
    assert_eq!(
        hbt.position(0),
        0.0,
        "позиция плоская после выхода — доказательство, что круг прошёл через настоящий Bot<MD>"
    );
    // Круг сам перевооружается на следующий вход, как только книга снова
    // валидна (`Phase::Idle`, D-СТРАТЕГИЯ: система готова ловить сигнал
    // на каждом валидном тике) — состояние после первого полного круга
    // поэтому не обязано быть `Idle`, это уже второй круг в работе. Само
    // перевооружение видно по `EntrySubmitted` после первого `ExitSubmitted`.
    assert!(
        actions[exit_at + 1..]
            .iter()
            .any(|a| matches!(a, Action::EntrySubmitted { .. })),
        "после выхода круг обязан перевооружиться на новый вход: {actions:?}"
    );
}

/// Без сделок очередь не двигается: вход снимается через `ENTRY_TTL_NS`
/// и `on_event` сообщает об этом явным действием, а не молчанием.
#[test]
fn on_event_times_out_an_entry_that_never_fills() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        depth_at(10 * S, false, 102.0, 5.0),
    ];
    let mut hbt = seam6_backtest(&feed);
    let mut state = StrategyState::new(0, SIGMA_LONG, 1.0, 1);

    let actions = drive(&mut hbt, &mut state);

    assert!(
        actions
            .iter()
            .any(|a| matches!(a, Action::EntryTimedOut { .. })),
        "неисполнившийся вход обязан истечь явным действием: {actions:?}"
    );
    assert_eq!(hbt.position(0), 0.0, "позиции не было и нет");
}

/// Запрет 1 `interfaces.md`: ноль аллокаций на событие после прогрева.
/// Считает через `alloc_count` (тот же приём, что `commands::lob::
/// session::tests::million_events_through_replay_feed_allocate_nothing_
/// after_warmup`) на 10⁶ вызовов `on_event` в самой частой ветке живого
/// потока — «книга ещё не готова/сигнала нет» (`Phase::Idle`, книга
/// невалидна): она же и есть подавляющее большинство вызовов на живом
/// потоке, где настоящий сигнал редок. Submit/cancel по настоящему
/// сигналу вызывает учёт ордеров крейта (`hftbacktest::backtest::
/// Backtest`), который не в этой зоне и не заявлен здесь как zero-alloc
/// — измеряется только код этого файла, а не внутренности крейта.
#[test]
fn on_event_allocates_nothing_per_call_while_the_book_is_not_ready() {
    const WARMUP: usize = 2_000;
    const MEASURED: usize = 1_000_000;

    // Фид без единого обновления книги (только сделка — крейт не даёт
    // построить `Backtest` на буквально пустом фиде): `depth()` остаётся
    // `INVALID_MIN`/`INVALID_MAX` на всех вызовах, `on_event` доходит до
    // `best_prices`, видит неполную книгу и уходит в `Idle`, не трогая
    // `submit_*`/`cancel`.
    let mut hbt = seam6_backtest(&[trade_at(0, true, 100.0, 1.0)]);
    let mut state = StrategyState::new(0, SIGMA_LONG, 1.0, 1);

    for _ in 0..WARMUP {
        let _ = on_event(&mut hbt, &mut state);
    }

    let mut measured_allocations = 0u64;
    for _ in 0..MEASURED {
        let (action, counts) =
            crate::alloc_count::measure(|| on_event(&mut hbt, &mut state).unwrap());
        assert_eq!(action, Action::Idle, "без книги решение обязано быть Idle");
        measured_allocations += counts.allocations;
    }
    assert_eq!(
        measured_allocations, 0,
        "on_event аллоцировал на пути без книги — запрет 1 interfaces.md"
    );
}

/// 2026-09-18: гонка отмены и исполнения. Вход истёк, отмена ушла (RTT в
/// пути), а заявка успела исполниться — позиция открыта. Раньше стратегия
/// сразу становилась `Idle`, позиция без выхода висела навсегда (на ZEC за
/// 20 ч круги шли только первые ~27 минут, дальше все сигналы «заняты»).
/// Теперь: `CancelPending` → позиция есть → `Holding` → выход по плану.
#[test]
fn a_fill_that_races_the_cancel_becomes_a_holding_not_an_idle() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        // Вход ушёл на шаге 0.1 с; TTL 2 с истекает на шаге 2.1 с, отмена летит
        // 0.5 мс (RTT 1 мс); продажа в 100 объёмом больше очереди исполняет
        // заявку раньше, чем отмена доходит до биржи.
        trade_at(2 * S + S / 10 + 100_000, true, 100.0, 10.0),
        depth_at(2 * S + S / 10 + 100_000, true, 100.0, 5.0),
        // Дедлайн 5 с от входа → рыночный выход по биду 100.
        depth_at(9 * S, true, 100.0, 5.0),
        depth_at(9 * S, false, 101.0, 5.0),
        depth_at(12 * S, false, 102.0, 5.0),
    ];
    let mut hbt = seam6_backtest(&feed);
    let plan = TradePlan::Bounce {
        entry_px: 100.0,
        stop_px: 90.0,
        take_px: 110.0,
        deadline_ns: 5 * S,
        entry_ttl_ns: 2 * S,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_px: 0.0,
        // F6: лестницы формы нет — прежний вход `entry_px` (гейт).
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
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        wall_eat: crate::lob::strategy::WallEatExit::OFF,
        pyramid: crate::lob::strategy::PyramidCfg::OFF,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_stop: crate::lob::strategy::GoneStop::Off,
    };
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 1.0, 1, plan);

    let actions = drive(&mut hbt, &mut state);

    let exit_at = actions
        .iter()
        .position(|a| {
            matches!(
                a,
                Action::ExitSubmitted {
                    reason: ExitReason::Deadline,
                    ..
                }
            )
        })
        .unwrap_or_else(|| panic!("позиция из гонки обязана закрыться по плану: {actions:?}"));
    assert!(
        !actions[..exit_at]
            .iter()
            .any(|a| matches!(a, Action::EntryTimedOut { .. })),
        "заявка исполнилась в гонке — это не тайм-аут: {actions:?}"
    );
    // После выхода стратегия перевооружается; второй вход честно истекает —
    // это уже не гонка, а обычный тайм-аут.
    assert_eq!(hbt.position(0), 0.0, "позиция плоская, не зависла");
}

/// E7 (владелец 19.09: «позиция одна за раз, но может быть дробной»), пороги
/// съедания чужих ботов 50/80 %: плотность на 99 (размер 10) съедается до 4
/// (60 %) — по рынку уходит **половина** позиции, круг продолжается; затем до
/// 1 (90 %) — уходит остаток. Две ноги выхода, обе `Eaten`, первая `partial`.
#[test]
fn eaten_thresholds_close_half_then_the_rest_in_two_market_legs() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, true, 99.0, 10.0),
        depth_at(0, false, 101.0, 5.0),
        trade_at(S + S / 2, true, 100.0, 3.0),
        trade_at(S + 4 * S / 5, true, 100.0, 3.0),
        // Плотность съедена на 60 % → половина позиции по рынку.
        depth_at(4 * S, true, 99.0, 4.0),
        // Съедена на 90 % → остаток по рынку.
        depth_at(6 * S, true, 99.0, 1.0),
        depth_at(12 * S, false, 102.0, 5.0),
    ];
    let mut hbt = seam6_backtest(&feed);
    let plan = TradePlan::Bounce {
        entry_px: 100.0,
        stop_px: 90.0,
        take_px: 110.0,
        deadline_ns: 20 * S,
        entry_ttl_ns: 5 * S,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_px: 0.0,
        // F6: лестницы формы нет — прежний вход `entry_px` (гейт).
        ladder: EntryLadder::NONE,
        early_exit_ns: 0,
        level_floor_qty: 0.0,
        band_exit_bps: 0.0,
        level_px: 99.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 50.0,
        eaten_all_pct: 80.0,
        eaten_half_frac: 0.5,
        level_qty: 10.0,
        lot_qty: 1.0,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        wall_eat: crate::lob::strategy::WallEatExit::OFF,
        pyramid: crate::lob::strategy::PyramidCfg::OFF,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_stop: crate::lob::strategy::GoneStop::Off,
    };
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 2.0, 1, plan);

    let actions = drive(&mut hbt, &mut state);

    let exits: Vec<(ExitReason, bool)> = actions
        .iter()
        .filter_map(|a| match a {
            Action::ExitSubmitted {
                reason, partial, ..
            } => Some((*reason, *partial)),
            _ => None,
        })
        .collect();
    assert_eq!(
        exits,
        vec![(ExitReason::Eaten, true), (ExitReason::Eaten, false)],
        "две ноги съедания: половина, потом остаток: {actions:?}"
    );
    assert_eq!(hbt.position(0), 0.0, "позиция плоская после второй ноги");
}

/// E7 «половина на середине хода» (Z 1:07:37): тейк 1:1 закрывает половину
/// лимитом, остаток **не** закрывается на 1:1 второй раз — бежит до дедлайна.
#[test]
fn half_take_closes_half_and_the_remainder_runs_to_the_deadline() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        trade_at(S + S / 2, true, 100.0, 3.0),
        trade_at(S + 4 * S / 5, true, 100.0, 3.0),
        // Цена дошла до тейка 102 и стоит там до конца.
        depth_at(4 * S, true, 102.0, 5.0),
        depth_at(4 * S, false, 103.0, 5.0),
        depth_at(15 * S, true, 102.0, 5.0),
        depth_at(15 * S, false, 103.0, 5.0),
    ];
    let mut hbt = seam6_backtest(&feed);
    let plan = TradePlan::Bounce {
        entry_px: 100.0,
        stop_px: 98.0,
        take_px: 102.0,
        deadline_ns: 8 * S,
        entry_ttl_ns: 5 * S,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_px: 0.0,
        // F6: лестницы формы нет — прежний вход `entry_px` (гейт).
        ladder: EntryLadder::NONE,
        early_exit_ns: 0,
        level_floor_qty: 0.0,
        band_exit_bps: 0.0,
        level_px: 99.0,
        tick_px: 1.0,
        take_frac: 0.5,
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
    };
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 2.0, 1, plan);

    let actions = drive(&mut hbt, &mut state);

    let exits: Vec<(ExitReason, bool)> = actions
        .iter()
        .filter_map(|a| match a {
            Action::ExitSubmitted {
                reason, partial, ..
            } => Some((*reason, *partial)),
            _ => None,
        })
        .collect();
    assert_eq!(
        exits,
        vec![(ExitReason::Take, true), (ExitReason::Deadline, false)],
        "половина тейком, остаток дедлайном, без второго тейка: {actions:?}"
    );
    assert_eq!(hbt.position(0), 0.0);
}

/// Дробный выход при лоте, не делящемся пополам: `qty = 1`, шаг лота 1 —
/// половина округляется до нуля, выходим целиком одной ногой (не `partial`).
#[test]
fn a_fraction_below_one_lot_exits_whole() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, true, 99.0, 10.0),
        depth_at(0, false, 101.0, 5.0),
        trade_at(S + S / 2, true, 100.0, 3.0),
        trade_at(S + 4 * S / 5, true, 100.0, 3.0),
        depth_at(4 * S, true, 99.0, 4.0),
        depth_at(12 * S, false, 102.0, 5.0),
    ];
    let mut hbt = seam6_backtest(&feed);
    let plan = TradePlan::Bounce {
        entry_px: 100.0,
        stop_px: 90.0,
        take_px: 110.0,
        deadline_ns: 20 * S,
        entry_ttl_ns: 5 * S,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_px: 0.0,
        // F6: лестницы формы нет — прежний вход `entry_px` (гейт).
        ladder: EntryLadder::NONE,
        early_exit_ns: 0,
        level_floor_qty: 0.0,
        band_exit_bps: 0.0,
        level_px: 99.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 50.0,
        eaten_all_pct: 80.0,
        eaten_half_frac: 0.5,
        level_qty: 10.0,
        lot_qty: 1.0,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        wall_eat: crate::lob::strategy::WallEatExit::OFF,
        pyramid: crate::lob::strategy::PyramidCfg::OFF,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_stop: crate::lob::strategy::GoneStop::Off,
    };
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 1.0, 1, plan);
    let actions = drive(&mut hbt, &mut state);
    let exits: Vec<(ExitReason, bool)> = actions
        .iter()
        .filter_map(|a| match a {
            Action::ExitSubmitted {
                reason, partial, ..
            } => Some((*reason, *partial)),
            _ => None,
        })
        .collect();
    assert_eq!(exits, vec![(ExitReason::Eaten, false)]);
    assert_eq!(hbt.position(0), 0.0);
}

// -----------------------------------------------------------------------
// F4 (план 2026-09-20, В-78): частичная позиция, пост-онли вход и своя
// позиция вместо `Bot::position` крейта.
// -----------------------------------------------------------------------

/// План F4: две ноги лестницы (`step` тиков между ними), стоп/тейк формы от
/// планового входа 100, вход живёт `ttl`, пост-онли — параметром.
fn f4_plan(stop_px: f64, take_px: f64, post_only: bool, ttl_ns: i64, step: f64) -> TradePlan {
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
fn prob_backtest(feed: &[Event]) -> Backtest<FastMarketDepth> {
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
fn a_sub_lot_remainder_does_not_leave_a_stale_filled_order_on_the_exchange() {
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
fn a_lot_aligned_order_with_a_float_remainder_is_removed_when_filled() {
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
fn a_partial_leg_sets_the_position_and_the_exit_is_sized_on_it() {
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
fn halfstop_closes_half_of_the_position_on_the_stop_and_latches() {
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
fn halflevel_exit(trade_px: Option<f64>) -> Option<(bool, f64, bool)> {
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
fn halflevel_closes_half_on_the_first_trade_below_the_level() {
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

/// Г-117 `tsl`: тейк лонга ползёт от `take_px` к безубытку (вход + круг комиссий), до `T` — степенью `γ`, после `T` — пол;
/// шорт зеркально; тейк ниже пола — без изменений.
#[test]
fn scheduled_take_slides_to_breakeven_and_mirrors_for_shorts() {
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
fn converge_exit_reason(a_bps: u32) -> Option<ExitReason> {
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
fn converge_exits_after_the_price_left_the_wall_by_a_and_came_back() {
    assert_eq!(converge_exit_reason(50), Some(ExitReason::Converge));
    assert_ne!(
        converge_exit_reason(500),
        Some(ExitReason::Converge),
        "Dmax < A — правило не активно"
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
fn the_second_leg_recomputes_the_average_and_the_exit_covers_the_whole_position() {
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
fn a_post_only_entry_that_crosses_the_spread_is_not_placed_and_is_not_busy() {
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
        order_qty: 1.0,
        first_order_id: 1,
        queue_model: QueueModelKind::Prob { n: 3.0 },
        busy_skip: true,
        hold_skip: false,
    };
    let signal = |t0_ns: i64| BounceSignal {
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

// -----------------------------------------------------------------------
// F5 (план 2026-09-20, В-74): срок жизни входа — «пока стена жива и цена в
// полосе», потолок — предохранительный. Условия проверяются на настоящем
// `Backtest` крейта (шов 6): книга управляется покадрово, время — из данных.
// -----------------------------------------------------------------------

/// План F5: вход 100 у бид-стены 99, потолок `ttl_ns`, порог стены `floor`
/// (единицы крейта) и полоса `band` bps. Нули в `floor`/`band` — прежний
/// режим «до конца касания» (условия выключены, гейт «те же круги»).
fn f5_plan(ttl_ns: i64, floor: f64, band: f64) -> TradePlan {
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
fn drive_stamped(
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
fn timeout_reason(actions: &[Action]) -> Option<EntryCancelReason> {
    actions.iter().find_map(|a| match a {
        Action::EntryTimedOut { reason, .. } => Some(*reason),
        _ => None,
    })
}

/// F5 (В-74): стена снята — размер на цене уровня (99) упал с 10 до 4 без
/// сделок, то есть плотность **убрали** (порог 5). Вход (потолок 30 с) не
/// ждёт потолка: все ноги снимаются сразу, причина `WallDead`.
#[test]
fn a_dead_wall_cancels_the_entry_before_the_ceiling() {
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
fn a_price_that_left_the_band_cancels_the_entry() {
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
fn the_ceiling_cancels_an_entry_that_lived_its_full_ttl() {
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
fn the_touch_mode_ignores_the_wall_and_the_band() {
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
fn ladder_plan(ttl_ns: i64) -> TradePlan {
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
fn ladder_legs_use_their_own_ticks_and_weights_and_a_crossing_leg_is_rejected() {
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
fn ladder_leg_qtys_rounds_shares_to_whole_lot_steps_with_remainder_on_the_heaviest_leg() {
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
fn ladder_leg_qtys_of_a_single_step_gives_the_whole_step_to_one_leg() {
    let fracs = [0.5, 0.25, 0.25, 0.0, 0.0, 0.0, 0.0, 0.0];
    let out = ladder_leg_qtys(1.0, 1.0, &fracs, 3);
    assert_eq!(&out[..3], &[1.0, 0.0, 0.0], "{out:?}");
}

/// Сумма ног всегда равна `qty` — на сетке произвольных долей и шага лота
/// (не только на круглых числах предыдущих тестов): `total_steps` целых
/// шагов растаскиваются без остатка, откуда бы доля ни пришла.
#[test]
fn ladder_leg_qtys_always_sums_to_qty() {
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
fn ladder_leg_qtys_without_a_known_lot_step_falls_back_to_plain_fractions() {
    let fracs = [0.5, 0.25, 0.25, 0.0, 0.0, 0.0, 0.0, 0.0];
    let out = ladder_leg_qtys(7.0, 0.0, &fracs, 3);
    assert_eq!(&out[..3], &[3.5, 1.75, 1.75], "{out:?}");
}

/// Интеграционно (F6, через `on_idle`/`drive`): `qty` — ровно один шаг лота
/// при долях 0.5/0.25/0.25 — ставится **одна** заявка (на самую тяжёлую
/// ногу), не три. Прежний код звал `submit_*_order` на все три доли, и
/// биржа получала два ордера меньше `qty_step` (`minQty`, отказ).
#[test]
fn on_idle_submits_a_single_order_when_only_one_leg_gets_a_whole_lot_step() {
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
fn a_partially_filled_take_is_cancelled_and_the_rest_is_stopped_out() {
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

// -----------------------------------------------------------------------
// F7 (план 2026-09-20, Б-75): выход «съели» (`eat<X>`) и «сняли» (`gone<W>`).
// Сделки в стену видит драйвер (`run_round`) и отдаёт стратегии
// (`observe_wall_trades`) — буфер `bot.last_trades` под его управлением,
// поэтому тесты идут полным драйвером (`drive_bounce`), а не голым `on_event`.
// -----------------------------------------------------------------------

/// План F7: вход 100 у бид-стены 99 размером `level_qty`, потолок входа 2 с,
/// формы выхода — `eat<eat_pct>` и/или `gone<gone_pct>` (нули выключают).
fn f7_plan(eat_pct: f64, gone_pct: f64, level_qty: f64) -> TradePlan {
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
fn position_rounds_dust_below_half_lot_step_down_to_flat() {
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
fn position_keeps_a_real_remainder_above_half_lot_step() {
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
fn on_exit_pending_with_dust_left_goes_idle_without_a_new_exit_order() {
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
fn f7_exits(plan: TradePlan, feed: &[Event]) -> Vec<ExitReason> {
    f7_run(plan, feed).fill_reason
}

/// Тот же прогон, но наружу отдаётся весь `BounceRun`: агрегаты причин
/// выхода (`ExitTally`) считает драйвер, и F8b проверяет их отдельно от
/// `fill_reason` кругов.
fn f7_run(plan: TradePlan, feed: &[Event]) -> crate::lob::backtest::BounceRun {
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
fn f7_feed_tail(rest: &[Event], wall: f64) -> Vec<Event> {
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
fn a_print_into_the_wall_exits_by_trades() {
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
fn slow_eating_accumulates_to_the_eat_threshold() {
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
fn a_wall_that_shrinks_without_trades_exits_as_gone() {
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
fn f7_plan_gone_trail(gone_pct: f64, trail_bps: f64, level_qty: f64) -> TradePlan {
    let mut plan = f7_plan(0.0, gone_pct, level_qty);
    if let TradePlan::Bounce { gone_trail_bps, .. } = &mut plan {
        *gone_trail_bps = trail_bps;
    }
    plan
}

/// Рост книги после снятия: аск 101 уходит на 104, бид поднимается до 103 — пик после снятия.
fn rally_to_103(ts: i64) -> [Event; 3] {
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
fn a_removed_wall_arms_a_trail_instead_of_exiting() {
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
fn a_removed_wall_without_a_pullback_keeps_the_position() {
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
fn f7_plan_gone_be(gone_pct: f64, mode: u8, level_qty: f64) -> TradePlan {
    f7_plan_gone_stop(gone_pct, GoneStop::Breakeven { hard: mode == 2 }, level_qty)
}

/// План F7 с переносом стопа после снятия (`gone_stop`).
fn f7_plan_gone_stop(gone_pct: f64, stop: GoneStop, level_qty: f64) -> TradePlan {
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
fn a_removed_wall_moves_the_stop_to_breakeven_once_in_profit() {
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
fn a_removed_wall_below_breakeven_exits_at_once_in_hard_mode() {
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
fn wall_stop(hard: bool, buffer_bps: f64) -> GoneStop {
    let mode = if hard {
        WallStopMode::Hard
    } else {
        WallStopMode::Soft
    };
    GoneStop::Wall { mode, buffer_bps }
}

/// `gone<W>wallk<B>` — снятие хуже нового стопа оставляет стоп плана до конца сделки.
fn wall_keep(buffer_bps: f64) -> GoneStop {
    GoneStop::Wall {
        mode: WallStopMode::Keep,
        buffer_bps,
    }
}

/// `f7_feed_tail` и секунда хвоста: далёкий аск 110 (лучшие цены не меняет) — драйвер решает выход
/// на шаге после события, и последнему событию ленты нужен следующий шаг.
fn wall_feed(rest: &[Event], wall: f64) -> Vec<Event> {
    let last = rest.last().map_or(3 * S, |e| e.exch_ts);
    let mut rest = rest.to_vec();
    rest.push(depth_at(last + S, false, 110.0, 5.0));
    f7_feed_tail(&rest, wall)
}

/// Бид возвращается на 100 (выше стены 99), стена 99 снята 10 → 3 на 5 с, бид 100 уходит на 6 с —
/// лучший бид 99 = стена.
fn wall_removed_then_bid_falls_to_99() -> Vec<Event> {
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
fn a_removed_wall_moves_the_stop_to_the_wall_level() {
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
fn the_wall_stop_buffer_sits_below_the_wall() {
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
fn a_wall_removed_below_the_new_stop_exits_in_hard_mode_and_waits_in_soft() {
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
fn wall_keep_moves_the_stop_when_the_removal_finds_the_position_above_the_wall() {
    let feed = wall_removed_then_bid_falls_to_99();
    assert_eq!(
        f7_exits(f7_plan_gone_stop(50.0, wall_keep(0.0), 10.0), &feed),
        vec![ExitReason::WallGone]
    );
}

/// Стоп не опускается: цель 2000 bps ниже стены (79,2) хуже стопа плана 90 — стоп остаётся 90,
/// падение к 85 закрывает позицию обычным стопом, не «сняли».
#[test]
fn the_wall_stop_never_lowers_the_plan_stop() {
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
fn a_short_wall_stop_moves_down_to_the_wall_plus_buffer() {
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
fn a_wall_that_shrank_by_trades_is_not_gone() {
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
fn the_none_exit_form_ignores_the_wall_trades() {
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

// -----------------------------------------------------------------------
// R1 (владелец 23.09, разбор прокида трейла): прибыль трейл-тейка обязана
// быть знаковой по направлению сделки, а не по модулю (`gain_bps`,
// `give_back_bps` в `decide_exit`). Стены и формы `eat`/`gone` тут ни при
// чём — план тот же F7-каркас, но с выключенными их порогами и включённым
// базовым трейлом (`trail_bps`/`trail_activate_bps`), у которого до этой
// правки не было прогона через настоящий выход вовсе.
// -----------------------------------------------------------------------

/// План только с трейл-тейком на удержании (не «после снятия»): стоп и тейк
/// далеко от входа (`0` и удвоенная сторона не подходят — знак стороны
/// заранее не известен вызывающему, поэтому оба берутся параметром), формы
/// `eat`/`gone` выключены — единственное, что может закрыть круг раньше
/// дедлайна (30 с от входа), это сам трейл.
fn trail_plan(stop_px: f64, take_px: f64, trail_activate_bps: f64, trail_bps: f64) -> TradePlan {
    TradePlan::Bounce {
        entry_px: 100.0,
        stop_px,
        take_px,
        deadline_ns: 30 * S,
        entry_ttl_ns: 2 * S,
        post_only: false,
        trail_bps,
        trail_activate_bps,
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
        level_qty: 10.0,
        lot_qty: 1.0,
        exit_eat_pct: 0.0,
        wall_eat: crate::lob::strategy::WallEatExit::OFF,
        pyramid: crate::lob::strategy::PyramidCfg::OFF,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_stop: crate::lob::strategy::GoneStop::Off,
    }
}

/// Стоп и тейк вынесены далеко на обе стороны — этот тест про трейл, не про них.
fn trail_plan_long(trail_activate_bps: f64, trail_bps: f64) -> TradePlan {
    trail_plan(50.0, 200.0, trail_activate_bps, trail_bps)
}

/// То же для шорта: стоп выше входа, тейк ниже — зеркально лонгу.
fn trail_plan_short(trail_activate_bps: f64, trail_bps: f64) -> TradePlan {
    trail_plan(150.0, 20.0, trail_activate_bps, trail_bps)
}

/// Шапка фида для тестов трейла на удержании — тот же приём, что `f7_feed_tail`
/// (вход лимитом в 100 исполняется сделкой на 1.5 с, очередь 5 съедена сделкой 6),
/// но по обе стороны книги (у шорта вход стоит в аске, его сторону задаёт `sigma`)
/// и с одним отличием от `f7_feed_tail`, важным именно здесь.
///
/// Уровень входа (100) снимается сразу за сделкой (`S + S/2 + 1`), а не через
/// потолок `entry_ttl_ns` (2 с, к 3 с), как у `f7_feed_tail`: круг уходит в
/// `Holding` сразу по исполнению (замерено: `enter_holding` на 1.501 с при этой
/// латентности, до какого-либо потолка), и `decide_exit` зовётся первым же тактом
/// опроса после этого (1.511 с). Если к этому такту уровень 100 ещё жив,
/// `best_favourable` сперва садится на цену **входа** (favourable = 100 = entry_px)
/// и там и остаётся (дальше цена только хуже) — `gain_bps` выходит ровно в ноль что
/// по знаковой версии, что по старой `.abs()`, трейл не взводится в обоих случаях
/// по СЛУЧАЙНО совпадающей причине, и тест перестаёт отличать одно от другого
/// (R1, независимая проверка 24.09: `a_long_that_never_recovers_above_entry_
/// does_not_arm_the_trail`/`a_short_..._does_not_arm_the_trail` не падали на
/// откаченной `.abs()`-версии). Сняв уровень входа до перехода в `Holding`,
/// первым тактом `decide_exit` видит уже цену рядом со входом (бид 99 / аск 101 —
/// они стоят с нуля и не трогаются вплоть до испытания), и `best_favourable`
/// фиксируется на ней: у знаковой версии `gain_bps` от неё честно отрицателен
/// (просадка), у `.abs()` — ложно положителен и достаёт до порога активации —
/// ровно та развилка, которую и должен ловить тест.
fn trail_feed_tail(rest: &[Event], sigma: i8) -> Vec<Event> {
    let mut feed = if sigma == SIGMA_LONG {
        vec![
            depth_at(0, true, 100.0, 5.0),
            depth_at(0, true, 99.0, 5.0),
            depth_at(0, false, 101.0, 5.0),
            trade_at(S + S / 2, true, 100.0, 6.0),
            depth_at(S + S / 2 + 1, true, 100.0, 0.0),
        ]
    } else {
        vec![
            depth_at(0, false, 100.0, 5.0),
            depth_at(0, false, 101.0, 5.0),
            depth_at(0, true, 99.0, 5.0),
            trade_at(S + S / 2, false, 100.0, 6.0),
            depth_at(S + S / 2 + 1, false, 100.0, 0.0),
        ]
    };
    feed.extend_from_slice(rest);
    feed
}

fn trail_exits(plan: TradePlan, feed: &[Event], sigma: i8) -> Vec<ExitReason> {
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
        t0_ns: S,
        sigma,
        plan,
        profile: 0,
        qty: None,
    };
    drive_bounce(&mut hbt, 0, &[signal], &cfg)
        .unwrap()
        .fill_reason
}

/// R1: лонг, чья лучшая цена после входа всё время ниже входа (просадка на 5 % — больше порога
/// активации 1 %, и без возврата) — трейл не имеет права взвестись. До правки `gain_bps` брался
/// по модулю, читал эту просадку как «прибыль ≥ порога» и закрывал круг `Trail` в минус, как
/// только откат от неё (тоже по модулю) дорастал до `trail_bps`; здесь — дедлайн.
#[test]
fn a_long_that_never_recovers_above_entry_does_not_arm_the_trail() {
    let feed = trail_feed_tail(
        &[
            // Старый бид 99 обязан быть снят явно: лучшая цена бида — максимум
            // по непустым уровням, и не снятый уровень остался бы «лучшим».
            depth_at(4 * S, true, 99.0, 0.0),
            depth_at(4 * S, true, 95.0, 5.0),
            // Хвост далеко за дедлайном (30 с от входа) — чтобы ответ на выход дошёл.
            depth_at(40 * S, false, 102.0, 5.0),
        ],
        SIGMA_LONG,
    );
    assert_eq!(
        trail_exits(trail_plan_long(100.0, 50.0), &feed, SIGMA_LONG),
        vec![ExitReason::Deadline],
        "просадка без возврата выше входа не должна взводить трейл"
    );
}

/// R1, зеркально: шорт, чья лучшая цена после входа всё время выше входа (цена выросла на 5 % —
/// убыток шорта — и не вернулась) — трейл не взводится, круг доживает до дедлайна.
#[test]
fn a_short_that_never_recovers_below_entry_does_not_arm_the_trail() {
    let feed = trail_feed_tail(
        &[
            // Старый аск 101 обязан быть снят явно: лучшая цена аска — минимум
            // по непустым уровням, и не снятый уровень остался бы «лучшим».
            depth_at(4 * S, false, 101.0, 0.0),
            depth_at(4 * S, false, 105.0, 5.0),
            depth_at(40 * S, true, 99.0, 5.0),
        ],
        SIGMA_SHORT,
    );
    assert_eq!(
        trail_exits(trail_plan_short(100.0, 50.0), &feed, SIGMA_SHORT),
        vec![ExitReason::Deadline],
        "рост цены против шорта без возврата ниже входа не должен взводить трейл"
    );
}

/// Контроль «как раньше»: лонг ушёл в настоящий плюс (3 % — выше порога активации 1 %) и
/// откатился на 2 % (выше отката 0.5 %) — трейл срабатывает, как и до правки R1 (знак не меняет
/// исход для честной прибыли, `.abs()` тут был не нужен, но и не мешал).
#[test]
fn a_long_pullback_from_a_genuine_gain_still_fires_the_trail() {
    let feed = trail_feed_tail(
        &[
            // Пик 103: бид растёт — максимум сам возьмёт его, старый 99 не мешает;
            // аск 101 обязан быть снят явно (иначе минимум аска остался бы на 101,
            // ниже нового бида — пересечённая книга).
            depth_at(4 * S, true, 103.0, 5.0),
            depth_at(4 * S, false, 101.0, 0.0),
            depth_at(4 * S, false, 104.0, 5.0),
            // Откат к 101: старый бид 103 обязан быть снят явно (максимум).
            depth_at(5 * S, true, 103.0, 0.0),
            depth_at(5 * S, true, 101.0, 5.0),
            depth_at(5 * S, false, 102.0, 5.0),
            depth_at(6 * S, false, 103.0, 5.0),
        ],
        SIGMA_LONG,
    );
    assert_eq!(
        trail_exits(trail_plan_long(100.0, 50.0), &feed, SIGMA_LONG),
        vec![ExitReason::Trail],
        "настоящая прибыль с откатом обязана закрыть круг трейлом"
    );
}

/// То же для шорта: цена упала на 3 % (прибыль шорта, выше порога активации 1 %) и откатилась
/// вверх на 2 % (выше отката 0.5 %) — трейл срабатывает.
#[test]
fn a_short_pullback_from_a_genuine_gain_still_fires_the_trail() {
    let feed = trail_feed_tail(
        &[
            // Пик 97: аск падает — минимум сам возьмёт его, старый 101 не мешает;
            // бид 99 обязан быть снят явно (иначе максимум бида остался бы на 99,
            // выше нового аска — пересечённая книга).
            depth_at(4 * S, true, 99.0, 0.0),
            depth_at(4 * S, true, 96.0, 5.0),
            depth_at(4 * S, false, 97.0, 5.0),
            // Откат к 99: старый аск 97 обязан быть снят явно (минимум).
            depth_at(5 * S, false, 97.0, 0.0),
            depth_at(5 * S, false, 99.0, 5.0),
            depth_at(5 * S, true, 98.0, 5.0),
            depth_at(6 * S, true, 97.0, 5.0),
        ],
        SIGMA_SHORT,
    );
    assert_eq!(
        trail_exits(trail_plan_short(100.0, 50.0), &feed, SIGMA_SHORT),
        vec![ExitReason::Trail],
        "настоящая прибыль (падение цены) с откатом обязана закрыть круг трейлом"
    );
}

// -----------------------------------------------------------------------
// F8b (аудит этапа F 21.09, В5/Р2/Р3/Р4; решение владельца В-79): потолок
// ожидания подтверждения отмены `CANCEL_WAIT_NS` — один механизм на вход
// (`CancelPending`) и на лимитку выхода (`ExitCancelPending`), плюс счёт
// причин F7 в агрегате `ExitTally`.
// -----------------------------------------------------------------------

/// Идентификатор лимитки выхода в тестах потолка: ставится руками, потому что
/// сценарий — «отмена летит, а биржа не отвечает», и доводить до него
/// естественный круг значило бы проверять крейт, а не предохранитель.
const EXIT_ID: u64 = 7;

/// Задержки тестов потолка: постановка 1 мс, **снятие 5 с** — так отмена
/// остаётся нерешённой и видно, что делает предохранитель (`CANCEL_WAIT_NS`
/// = 1 с); рыночный — 1 мс.
fn slow_cancel_latency() -> ExecLatency {
    ExecLatency {
        place_ns: 1_000_000,
        cancel_ns: 5_000_000_000,
        taker_ns: 1_000_000,
    }
}

/// План тестов потолка: вход 100 у бид-стены 99, стоп/тейк далеко, дедлайн
/// 30 с, срок жизни входа — `ttl_ns`. Условия F5 выключены: тест про отмену,
/// а не про «стена снята».
fn cancel_wait_plan(ttl_ns: i64) -> TradePlan {
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
fn a_cancel_the_exchange_never_confirms_is_released_by_the_ceiling() {
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
fn an_orphan_is_released_once_the_exchange_confirms_the_cancel() {
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
fn a_filled_entry_orphan_is_flattened_and_counted() {
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
fn a_filled_exit_orphan_is_accounted_as_our_exit() {
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
fn an_entry_leg_whose_place_was_in_flight_is_cancelled_by_the_retry() {
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
fn racing_exit_state(hbt: &mut Backtest<FastMarketDepth>) -> StrategyState {
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
fn entry_ladder_fills_to_capacity_and_refuses_the_extra_leg() {
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
fn entry_ladder_averages_the_legs_by_their_weights() {
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
fn an_exit_fill_that_races_the_cancel_closes_the_round() {
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
fn the_exit_cancel_ceiling_returns_the_round_to_the_plan() {
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
fn the_driver_counts_the_entry_cancel_ceiling() {
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
fn an_orphan_survives_the_round_boundary_in_the_driver() {
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
fn the_driver_counts_the_exit_reasons_into_the_tally() {
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
fn an_entry_orphan_does_not_flatten_what_the_plan_already_holds() {
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
fn holding_decision_reads_time_only_at_the_known_thresholds() {
    const SRC: &str = include_str!("../strategy.rs");
    let uses = |name: &str| -> Vec<String> {
        let start = SRC.find(&format!("\nfn {name}<")).expect("функция есть");
        let end = SRC[start + 5..]
            .find("\nfn ")
            .map_or(SRC.len(), |e| start + 5 + e);
        SRC[start..end]
            .lines()
            .filter(|l| {
                l.split(|c: char| !(c.is_alphanumeric() || c == '_'))
                    .any(|w| w == "now")
            })
            .map(|l| l.trim().to_string())
            .collect()
    };
    assert_eq!(
        uses("decide_exit"),
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
            "} else if now.saturating_sub(entry_ns) >= deadline_ns {",
            // Г-133 `chase<мс>`: пропуск шагов при форме выключен (`pyramid_on` включает `chase_ms > 0`).
            "state.chase_start_ns = now;",
            "if now.saturating_sub(state.chase_start_ns)",
        ]
    );
    assert_eq!(
        uses("on_holding"),
        [
            "now: i64,",
            "match decide_exit(bot, state, entry_ns, now, quotes, true) {",
        ]
    );
}

// -----------------------------------------------------------------------
// TK-014: выход `weat<X>s<W>{m|l|a}<Y>` — съедание стены после входа за окно `W`,
// причина раздельно по ходу BTC (`docs/findings/tk014-design-2026-09-28.md`).
// -----------------------------------------------------------------------

fn weat_plan(pct: f64, secs: u32, mode: WallEatMode, btc_bps: f64) -> TradePlan {
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
fn wall_ring_is_allocated_only_for_weat_plans() {
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
fn weat_window_counts_only_post_entry_wall_trades_within_w() {
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
fn btc_move_uses_last_closed_minute_and_earlier_on_gaps() {
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
fn weat_mode_splits_market_and_local_by_btc_threshold() {
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

fn pyr_plan(parts: u8) -> TradePlan {
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
fn pyr_state(parts: u8, eaten: f64) -> (Backtest<FastMarketDepth>, StrategyState) {
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
fn level_shift_follows_the_base_entry_not_the_adds_but_breakeven_follows_all_orders() {
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
fn fresh_entry_is_one_part_floored_to_the_lot_and_adds_are_q0_over_n() {
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

fn reinst_state(n: u8, u3: u8) -> (Backtest<FastMarketDepth>, StrategyState) {
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

fn wall_now(qty: f64) -> WallNow {
    WallNow {
        ok: true,
        qty,
        eaten_pct: 0.0,
    }
}

#[test]
fn reinstall_counts_returns_only_after_a_removal_and_adds_one_per_return() {
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
fn reinstall_moves_the_stop_to_breakeven_only_after_n_returns_and_when_price_is_there() {
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

fn newwall_state(feed: &[Event], k: u8, u3: u8) -> (Backtest<FastMarketDepth>, StrategyState) {
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

fn observe_nw(hbt: &Backtest<FastMarketDepth>, state: &mut StrategyState, bid: f64) {
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
fn newwall_triggers_only_on_a_big_level_born_after_the_first_hold_event_between_stop_and_market() {
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
fn newwall_add_is_u_times_q0_and_capped_by_k() {
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
fn pyramid_off_never_adds() {
    let (mut hbt, mut state) = pyr_state(0, 25.0);
    state
        .pyramid_step(&mut hbt, 99.0, 101.0, HbtSide::Buy)
        .unwrap();
    assert!(state.add_order_ids().is_empty());
}

#[test]
fn pyramid_adds_one_third_per_third_eaten_and_never_recharges_a_share() {
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
fn pyramid_one_add_per_call_when_two_thirds_jump_in_one_frame() {
    let (mut hbt, mut state) = pyr_state(3, 25.0);
    state
        .pyramid_step(&mut hbt, 99.0, 101.0, HbtSide::Buy)
        .unwrap();
    assert_eq!(state.add_order_ids().len(), 1, "за вызов — одна добавка");
}

#[test]
fn pyramid_does_not_add_when_the_wall_is_eaten_whole() {
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
fn pyramid_release_cancels_adds_and_blocks_new_ones() {
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
fn still_at_level_tells_adjacent_ticks_apart_for_tick_multiples() {
    for tick in [1.0, 0.1, 0.01, 0.001, 0.0001, 0.00001, 0.5, 0.05] {
        for k in [1_i64, 7, 123, 4_567, 99_999, 1_234_567] {
            let px = |n: i64| n as f64 * tick;
            let level = px(k);
            for side in [HbtSide::Buy, HbtSide::Sell] {
                let at = |n: i64| {
                    let (bid, ask) = if side == HbtSide::Buy {
                        (px(n), px(n + 1))
                    } else {
                        (px(n - 1), px(n))
                    };
                    still_at_level(side, bid, ask, level, tick)
                };
                assert!(at(k), "свой тик не узнан: шаг {tick}, k {k}, {side:?}");
                assert!(!at(k + 1), "тик выше принят: шаг {tick}, k {k}, {side:?}");
                assert!(!at(k - 1), "тик ниже принят: шаг {tick}, k {k}, {side:?}");
            }
        }
    }
}
