use super::*;
use crate::lob::backtest::{
    build_backtest, drive_bounce, latency_from_rtt, BounceSignal, DriveConfig, ExecLatency,
    QueueModelKind, SIGMA_LONG,
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
        early_exit_ns: 0,
        level_px: 99.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 0.0,
        eaten_all_pct: 0.0,
        eaten_half_frac: 0.0,
        level_qty: 0.0,
        lot_qty: 0.1,
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
