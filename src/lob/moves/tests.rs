//! Тесты измерения переездов (T42): пара находится с её Δt/Δp/отношением,
//! ближайшее рождение выигрывает, «спуф» отличается следствием, а не формой
//! пары; квантили и гистограмма считаются по объявленным определениям.

use super::{by_dt_bins, find_pairs, histogram, quantiles, MovePair};
use crate::book::Side;
use crate::lob::levels::{DeathKind, LevelRecord, Outcome, TouchRecord};
use crate::lob::markout::MidSample;

fn rec(
    side: Side,
    price_tick: i64,
    birth_ms: i64,
    death_ms: i64,
    size_max: i64,
    traded_lots: i64,
) -> LevelRecord {
    LevelRecord {
        side,
        price_tick,
        birth_ms,
        death_ms,
        lifetime_ms: death_ms - birth_ms,
        size_max,
        time_to_max_ms: 0,
        size_monotonic: true,
        repeat_count: 0,
        repriced: false,
        death: DeathKind::BelowFraction,
        traded_lots,
        rpi_lots: 0,
    }
}

fn mid(ts_ms: i64, bid_tick: i64, ask_tick: i64) -> MidSample {
    MidSample {
        ts_ms,
        bid_tick,
        ask_tick,
    }
}

fn touch(side: Side, price_tick: i64, start_ms: i64) -> TouchRecord {
    TouchRecord {
        side,
        price_tick,
        touch_index: 0,
        start_ms,
        end_ms: start_ms + 100,
        duration_ms: 100,
        level_birth_ms: start_ms - 1000,
        size_at_touch: 100,
        size_max_before: 100,
        traded_during: 0,
        frontrun_lots: 0,
        frontrun_tick: None,
        swept_lots: 0,
        round_zeros: 0,
        ended_by_death: false,
        stack_levels: 1,
    }
}

/// Переехавшая плотность: `pulled` на цене 10000 через 300 мс рождается на
/// 10005 тем же размером — одна пара с Δt=300, Δp=+5, отношением 1.
#[test]
fn a_moved_density_gives_one_pair_with_its_dt_dp_and_size_ratio() {
    let records = vec![
        rec(Side::Bid, 10_000, 1_000, 5_000, 100, 10), // сняли (10 % ≤ 20 %)
        rec(Side::Bid, 10_005, 5_300, 9_000, 100, 5),
    ];
    let pairs = find_pairs(&records, &[], &[], 1_000);
    assert_eq!(pairs.len(), 1);
    let p: MovePair = pairs[0];
    assert_eq!(p.side, Side::Bid);
    assert_eq!(p.dt_ms, 300);
    assert_eq!(p.dp_ticks, 5);
    assert_eq!(p.size_ratio, Some(1.0));
    assert_eq!(p.zeros_from, 4);
    assert!(!p.touched, "касаний не подавали — цена до уровня не дошла");
}

/// Из двух рождений в окне берётся ближайшее по времени; рождение за окном
/// парой не считается.
#[test]
fn the_nearest_birth_wins_and_a_birth_outside_the_window_is_not_a_pair() {
    let records = vec![
        rec(Side::Ask, 20_000, 1_000, 2_000, 100, 0),
        rec(Side::Ask, 20_100, 2_200, 3_000, 100, 0), // ближайшее (+200 мс)
        rec(Side::Ask, 20_900, 9_000, 9_500, 100, 0), // за окном
    ];
    let pairs = find_pairs(&records, &[], &[], 1_000);
    assert_eq!(pairs.len(), 1);
    assert_eq!(pairs[0].dt_ms, 200);
    assert_eq!(pairs[0].dp_ticks, 100);

    // Окно уже расстояния до второго рождения — пары нет вовсе.
    let none = find_pairs(&records, &[], &[], 100);
    assert!(none.is_empty());
}

/// Спуф отличается от переезда **следствием**: та же форма пары, но цена до
/// нового уровня не дошла, касания нет, а середина ушла от него.
#[test]
fn a_spoof_differs_by_what_happened_next_not_by_the_shape_of_the_pair() {
    let records = vec![
        rec(Side::Bid, 10_000, 1_000, 5_000, 100, 0),
        rec(Side::Bid, 9_900, 5_100, 20_000, 100, 0),
    ];
    // Середина уходит вниз от нового уровня: 10 с — минус, через 60 с — ещё ниже.
    let mids = vec![
        mid(5_000, 10_050, 10_060),
        mid(15_100, 10_000, 10_010),
        mid(65_100, 9_800, 9_810),
    ];
    let pairs = find_pairs(&records, &[], &mids, 1_000);
    assert_eq!(pairs.len(), 1);
    assert!(!pairs[0].touched);
    assert!(pairs[0].mid_10s_bps.unwrap() < 0.0);
    assert!(pairs[0].mid_60s_bps.unwrap() < pairs[0].mid_10s_bps.unwrap());
}

/// Касание нового уровня после рождения отмечается, и исход берётся у самого
/// нового уровня.
#[test]
fn a_touch_after_the_birth_marks_the_pair_and_carries_the_outcome() {
    let records = vec![
        rec(Side::Bid, 10_000, 1_000, 5_000, 100, 0),
        // Новый уровень проели: 90 лотов против максимума 100 — это `eaten`.
        rec(Side::Bid, 10_002, 5_100, 8_000, 100, 90),
    ];
    let touches = vec![touch(Side::Bid, 10_002, 6_000)];
    let pairs = find_pairs(&records, &touches, &[], 1_000);
    assert_eq!(pairs.len(), 1);
    assert!(pairs[0].touched);
    assert_eq!(pairs[0].outcome_to, Some(Outcome::Eaten));
    assert_eq!(pairs[0].dp_ticks, 2);
}

/// Квантили — тип 7: на 1..10 p50 = 5.5, p10 = 1.9, p90 = 9.1.
#[test]
fn quantiles_follow_the_declared_type_seven_definition() {
    let v: Vec<f64> = (1..=10).map(|x| x as f64).collect();
    let (p10, p50, p90) = quantiles(&v).unwrap();
    assert!((p10 - 1.9).abs() < 1e-9, "p10={p10}");
    assert!((p50 - 5.5).abs() < 1e-9, "p50={p50}");
    assert!((p90 - 9.1).abs() < 1e-9, "p90={p90}");
    assert_eq!(quantiles(&[]), None);
    assert_eq!(quantiles(&[3.0]), Some((3.0, 3.0, 3.0)));
}

/// Гистограмма начинается от минимума и считает значения по корзинам шага,
/// который задал вызывающий; пустые корзины не выдумываются.
#[test]
fn histogram_starts_at_the_minimum_and_uses_the_given_width() {
    let v = vec![300.0, 350.0, 900.0, 1_050.0];
    let h = histogram(&v, 500.0);
    assert_eq!(h, vec![(0.0, 2), (500.0, 1), (1_000.0, 1)]);
    assert!(histogram(&[], 500.0).is_empty());
    assert!(histogram(&v, 0.0).is_empty(), "нулевой шаг — не шаг");
}

/// Свод по корзинам Δt: пары «в том же кадре» отделяются от более поздних, и
/// видно, чем они отличаются — нулевым Δp и отсутствием касания против
/// настоящего сдвига цены.
#[test]
fn dt_bins_separate_the_same_frame_churn_from_later_pairs() {
    let mk = |dt_ms: i64, dp_ticks: i64, ratio: f64, touched: bool| MovePair {
        side: Side::Bid,
        dt_ms,
        dp_ticks,
        size_ratio: Some(ratio),
        zeros_from: 0,
        zeros_to: 0,
        mid_10s_bps: None,
        mid_60s_bps: None,
        touched,
        outcome_to: None,
    };
    let pairs = vec![
        mk(0, 0, 0.1, false),
        mk(0, 0, 12.0, false),
        mk(600, 25, 1.0, true),
        mk(900, 30, 0.9, true),
    ];
    let bins = by_dt_bins(&pairs, 500);
    assert_eq!(bins.len(), 2);
    assert_eq!(bins[0].dt_from_ms, 0);
    assert_eq!(bins[0].n, 2);
    assert_eq!(bins[0].dp_abs_median, Some(0.0));
    assert!((bins[0].zero_dp_share - 1.0).abs() < 1e-9);
    assert!((bins[0].touched_share - 0.0).abs() < 1e-9);
    assert_eq!(bins[1].dt_from_ms, 500);
    assert_eq!(bins[1].n, 2);
    assert_eq!(bins[1].dp_abs_median, Some(27.5));
    assert!((bins[1].touched_share - 1.0).abs() < 1e-9);
    assert!(by_dt_bins(&pairs, 0).is_empty(), "нулевой шаг — не шаг");
}
