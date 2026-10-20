//! Тесты стратегии — R1: знак прибыли трейл-тейка.

use super::*;

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
pub(super) fn trail_plan(
    stop_px: f64,
    take_px: f64,
    trail_activate_bps: f64,
    trail_bps: f64,
) -> TradePlan {
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
pub(super) fn trail_plan_long(trail_activate_bps: f64, trail_bps: f64) -> TradePlan {
    trail_plan(50.0, 200.0, trail_activate_bps, trail_bps)
}

/// То же для шорта: стоп выше входа, тейк ниже — зеркально лонгу.
pub(super) fn trail_plan_short(trail_activate_bps: f64, trail_bps: f64) -> TradePlan {
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
pub(super) fn trail_feed_tail(rest: &[Event], sigma: i8) -> Vec<Event> {
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

pub(super) fn trail_exits(plan: TradePlan, feed: &[Event], sigma: i8) -> Vec<ExitReason> {
    let mut hbt = build_backtest(
        feed,
        1.0,
        1.0,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let cfg = DriveConfig {
        tape_log_secs: 0,
        tape_log_ws: crate::lob::backtest::NO_TAPE_WS,
        order_qty: 1.0,
        first_order_id: 1,
        queue_model: QueueModelKind::RiskAdverse,
        busy_skip: true,
        hold_skip: false,
    };
    let signal = BounceSignal {
        touch_index: 0,
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
pub(super) fn a_long_that_never_recovers_above_entry_does_not_arm_the_trail() {
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
pub(super) fn a_short_that_never_recovers_below_entry_does_not_arm_the_trail() {
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
pub(super) fn a_long_pullback_from_a_genuine_gain_still_fires_the_trail() {
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
pub(super) fn a_short_pullback_from_a_genuine_gain_still_fires_the_trail() {
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
