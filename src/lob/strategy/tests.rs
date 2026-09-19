use super::*;
use crate::lob::backtest::{latency_from_rtt, SIGMA_LONG};

use hftbacktest::backtest::assettype::LinearAsset;
use hftbacktest::backtest::data::Data;
use hftbacktest::backtest::models::{
    CommonFees, ConstantLatency, RiskAdverseQueueModel, TradingValueFeeModel,
};
use hftbacktest::backtest::{Backtest, DataSource, ExchangeKind, L2AssetBuilder};
use hftbacktest::depth::HashMapMarketDepth;
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

fn trade_ev(sell: bool) -> u64 {
    if sell {
        LOCAL_SELL_TRADE_EVENT | EXCH_SELL_TRADE_EVENT
    } else {
        LOCAL_BUY_TRADE_EVENT | EXCH_BUY_TRADE_EVENT
    }
}

fn depth_at(exch_ts: i64, bid: bool, px: f64, qty: f64) -> Event {
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

fn trade_at(exch_ts: i64, sell: bool, px: f64, qty: f64) -> Event {
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

fn seam6_backtest(feed: &[Event]) -> Backtest<HashMapMarketDepth> {
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
                .depth(|| HashMapMarketDepth::new(1.0, 1.0))
                .build()
                .unwrap(),
        )
        .build()
        .unwrap()
}

const S: i64 = 1_000_000_000;

/// Прогоняет `on_event` в цикле `elapse(шаг) -> on_event` до конца
/// синтетического фида — тот самый "прогон на `Backtest` крейта через
/// шов 6", который требует критерий приёмки. `on_event` сама не зовёт
/// `elapse` (doc модуля) — это работа вызывающего, здесь тестового цикла.
fn drive(hbt: &mut Backtest<HashMapMarketDepth>, state: &mut StrategyState) -> Vec<Action> {
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
        early_exit_ns: 0,
        level_px: 99.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 0.0,
        eaten_all_pct: 0.0,
        eaten_half_frac: 0.0,
        level_qty: 0.0,
        lot_qty: 1.0,
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
        early_exit_ns: 0,
        level_px: 99.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 50.0,
        eaten_all_pct: 80.0,
        eaten_half_frac: 0.5,
        level_qty: 10.0,
        lot_qty: 1.0,
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
        early_exit_ns: 0,
        level_px: 99.0,
        tick_px: 1.0,
        take_frac: 0.5,
        eaten_half_pct: 0.0,
        eaten_all_pct: 0.0,
        eaten_half_frac: 0.0,
        level_qty: 0.0,
        lot_qty: 1.0,
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
        early_exit_ns: 0,
        level_px: 99.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 50.0,
        eaten_all_pct: 80.0,
        eaten_half_frac: 0.5,
        level_qty: 10.0,
        lot_qty: 1.0,
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
