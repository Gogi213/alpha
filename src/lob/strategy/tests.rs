use super::*;
use crate::lob::backtest::{
    build_backtest, drive_bounce, latency_from_rtt, BounceSignal, DriveConfig, ExecLatency,
    QueueModelKind, SIGMA_LONG, SIGMA_SHORT,
};

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() < 1e-9
}

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
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_be: 0,
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
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_be: 0,
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
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_be: 0,
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
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_be: 0,
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
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_be: 0,
    }
}

/// Бэктест с моделью очереди по объёму (`PartialFillExchange`): только она
/// отдаёт частичное исполнение, ради которого и заведена частичная позиция.
fn prob_backtest(feed: &[Event]) -> Backtest<HashMapMarketDepth> {
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
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 0.3, 1, cancel_wait_plan(30 * S));

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
        order_qty: 1.0,
        first_order_id: 1,
        queue_model: QueueModelKind::Prob { n: 3.0 },
    };
    let signal = |t0_ns: i64| BounceSignal {
        t0_ns,
        sigma: SIGMA_LONG,
        plan: f4_plan(98.0, 102.0, true, 20 * S, 1.0),
        profile: 0,
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
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_be: 0,
    }
}

/// Прогон с метками времени стороны на каждом вызове `on_event` — для
/// проверки «вход жил ровно потолок» (F5): по меткам видно, когда вход
/// отправлен и когда снят.
fn drive_stamped(
    hbt: &mut Backtest<HashMapMarketDepth>,
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
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_be: 0,
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
        exit_gone_pct: gone_pct,
        gone_trail_bps: 0.0,
        gone_be: 0,
    }
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
        order_qty: 1.0,
        first_order_id: 1,
        queue_model: QueueModelKind::RiskAdverse,
    };
    let signal = BounceSignal {
        t0_ns: S,
        sigma: SIGMA_LONG,
        plan,
        profile: 0,
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
    let mut plan = f7_plan(0.0, gone_pct, level_qty);
    if let TradePlan::Bounce { gone_be, .. } = &mut plan {
        *gone_be = mode;
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
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_be: 0,
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
/// (вход лимитом в 100 исполняется сделкой на 1.5 с, очередь 5 съедена сделкой 6;
/// потолок входа (2 с) переводит круг в `Holding` к 3 с), но по обе стороны книги:
/// у шорта вход стоит в аске, и его сторону задаёт `sigma`.
fn trail_feed_tail(rest: &[Event], sigma: i8) -> Vec<Event> {
    let mut feed = if sigma == SIGMA_LONG {
        vec![
            depth_at(0, true, 100.0, 5.0),
            depth_at(0, true, 99.0, 5.0),
            depth_at(0, false, 101.0, 5.0),
            trade_at(S + S / 2, true, 100.0, 6.0),
            depth_at(3 * S, true, 100.0, 0.0),
        ]
    } else {
        vec![
            depth_at(0, false, 100.0, 5.0),
            depth_at(0, false, 101.0, 5.0),
            depth_at(0, true, 99.0, 5.0),
            trade_at(S + S / 2, false, 100.0, 6.0),
            depth_at(3 * S, false, 100.0, 0.0),
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
        order_qty: 1.0,
        first_order_id: 1,
        queue_model: QueueModelKind::RiskAdverse,
    };
    let signal = BounceSignal {
        t0_ns: S,
        sigma,
        plan,
        profile: 0,
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
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_be: 0,
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
fn racing_exit_state(hbt: &mut Backtest<HashMapMarketDepth>) -> StrategyState {
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
        order_qty: 1.0,
        first_order_id: 1,
        queue_model: QueueModelKind::RiskAdverse,
    };
    let signal = BounceSignal {
        t0_ns: S,
        sigma: SIGMA_LONG,
        plan: cancel_wait_plan(S / 10),
        profile: 0,
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
        order_qty: 1.0,
        first_order_id: 1,
        queue_model: QueueModelKind::RiskAdverse,
    };
    // Срок жизни входа 0.1 с: снятие на 1.2 с, потолок на 2.2 с — круг закрыт.
    let signals = [
        BounceSignal {
            t0_ns: S,
            sigma: SIGMA_LONG,
            plan: cancel_wait_plan(S / 10),
            profile: 0,
        },
        BounceSignal {
            t0_ns: 2_400_000_000,
            sigma: SIGMA_LONG,
            plan: cancel_wait_plan(30 * S),
            profile: 0,
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
