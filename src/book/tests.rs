use super::*;

const TICK: i64 = 100_000; // 0.0001
const STEP: i64 = 1_000_000; // 0.001

fn px(v: f64) -> i64 {
    (v * 1e9).round() as i64
}

fn snapshot(u: u64, bids: &[(f64, f64)], asks: &[(f64, f64)]) -> Update {
    Update {
        is_snapshot: true,
        u,
        seq: u,
        cts_ms: 1_000,
        bids: bids.iter().map(|&(p, q)| (px(p), px(q))).collect(),
        asks: asks.iter().map(|&(p, q)| (px(p), px(q))).collect(),
    }
}

fn delta(u: u64, bids: &[(f64, f64)], asks: &[(f64, f64)]) -> Update {
    Update {
        is_snapshot: false,
        u,
        seq: u,
        cts_ms: 1_000 + u as i64,
        bids: bids.iter().map(|&(p, q)| (px(p), px(q))).collect(),
        asks: asks.iter().map(|&(p, q)| (px(p), px(q))).collect(),
    }
}

fn book() -> Book {
    Book::new(TICK, STEP)
}

#[test]
fn snapshot_then_delta_applies() {
    let mut b = book();
    b.apply(&snapshot(
        10,
        &[(1.0000, 5.0), (0.9999, 3.0)],
        &[(1.0001, 4.0)],
    ))
    .unwrap();
    assert_eq!(b.depth(Side::Bid), 2);
    assert_eq!(b.depth(Side::Ask), 1);
    assert_eq!(b.best_bid_tick_opt(), Some(px(1.0000) / TICK));
    assert_eq!(b.best_ask_tick_opt(), Some(px(1.0001) / TICK));

    b.apply(&delta(11, &[(1.0000, 7.0)], &[])).unwrap();
    assert_eq!(b.qty_lots_at(Side::Bid, px(1.0000) / TICK), px(7.0) / STEP);
}

/// `size = 0` удаляет уровень. Без этого снятая плотность осталась бы в книге
/// стоять вечно и попала бы в разметку как живая.
#[test]
fn zero_size_removes_the_level() {
    let mut b = book();
    b.apply(&snapshot(
        10,
        &[(1.0000, 5.0), (0.9999, 3.0)],
        &[(1.0001, 4.0)],
    ))
    .unwrap();
    b.apply(&delta(11, &[(0.9999, 0.0)], &[])).unwrap();

    assert_eq!(b.depth(Side::Bid), 1);
    assert_eq!(b.qty_lots_at(Side::Bid, px(0.9999) / TICK), 0);
}

/// Удаление цены, которой в книге нет, не должно её создавать.
#[test]
fn zero_size_on_absent_level_is_a_no_op() {
    let mut b = book();
    b.apply(&snapshot(10, &[(1.0000, 5.0)], &[(1.0001, 4.0)]))
        .unwrap();
    b.apply(&delta(11, &[(0.5000, 0.0)], &[])).unwrap();
    assert_eq!(b.depth(Side::Bid), 1);
}

/// Пропуск `u` обязан форсировать ресинк, а не молча продолжить: пропущенное
/// сообщение — это уровни, которых в нашей книге либо нет, либо уже не должно быть.
#[test]
fn gap_in_u_is_reported() {
    let mut b = book();
    b.apply(&snapshot(10, &[(1.0000, 5.0)], &[(1.0001, 4.0)]))
        .unwrap();
    let err = b.apply(&delta(13, &[(1.0000, 6.0)], &[])).unwrap_err();
    assert_eq!(
        err,
        ApplyError::SequenceGap {
            expected: 11,
            got: 13
        }
    );
    // Книга не тронута: отказ должен быть атомарным на уровне решения,
    // иначе после разрыва в ней смесь двух состояний.
    assert_eq!(b.last_u(), Some(10));
    assert_eq!(b.qty_lots_at(Side::Bid, px(1.0000) / TICK), px(5.0) / STEP);
}

#[test]
fn repeated_u_is_a_gap_too() {
    let mut b = book();
    b.apply(&snapshot(10, &[(1.0000, 5.0)], &[(1.0001, 4.0)]))
        .unwrap();
    b.apply(&delta(11, &[(1.0000, 6.0)], &[])).unwrap();
    let err = b.apply(&delta(11, &[(1.0000, 7.0)], &[])).unwrap_err();
    assert!(matches!(err, ApplyError::SequenceGap { .. }));
}

#[test]
fn delta_before_any_snapshot_is_rejected() {
    let mut b = book();
    let err = b.apply(&delta(5, &[(1.0000, 5.0)], &[])).unwrap_err();
    assert_eq!(
        err,
        ApplyError::SequenceGap {
            expected: 0,
            got: 5
        }
    );
    assert!(!b.is_synced());
}

/// `u = 1` посреди потока — рестарт сервиса Bybit. Книга перезаписывается
/// целиком; уровни, пришедшие до рестарта, не выживают.
#[test]
fn u_equals_one_overwrites_the_whole_book() {
    let mut b = book();
    b.apply(&snapshot(
        10,
        &[(1.0000, 5.0), (0.9999, 3.0)],
        &[(1.0001, 4.0)],
    ))
    .unwrap();

    let mut restart = delta(1, &[(0.5000, 2.0)], &[(0.5001, 2.0)]);
    restart.is_snapshot = false; // именно дельтой, как это и приходит
    b.apply(&restart).unwrap();

    assert_eq!(b.depth(Side::Bid), 1);
    assert_eq!(b.depth(Side::Ask), 1);
    assert_eq!(b.qty_lots_at(Side::Bid, px(1.0000) / TICK), 0);
    assert_eq!(b.best_bid_tick_opt(), Some(px(0.5000) / TICK));
    assert_eq!(b.last_u(), Some(1));
}

/// Смена шага цены посреди записи обязана быть видна сразу. Без этой проверки
/// дельты в тиках тихо поехали бы по масштабу в данных, которые не восстановить.
#[test]
fn price_off_tick_is_rejected() {
    let mut b = book();
    let bad = Update {
        is_snapshot: true,
        u: 10,
        seq: 10,
        cts_ms: 1,
        bids: vec![(px(1.00005), px(1.0))],
        asks: vec![],
    };
    let err = b.apply(&bad).unwrap_err();
    assert!(matches!(err, ApplyError::PriceNotOnTick { .. }));
}

#[test]
fn qty_off_step_is_rejected() {
    let mut b = book();
    let bad = Update {
        is_snapshot: true,
        u: 10,
        seq: 10,
        cts_ms: 1,
        bids: vec![(px(1.0000), px(0.0005))],
        asks: vec![],
    };
    let err = b.apply(&bad).unwrap_err();
    assert!(matches!(err, ApplyError::QtyNotOnStep { .. }));
}

#[test]
fn crossed_book_is_rejected() {
    let mut b = book();
    let bad = snapshot(10, &[(1.0002, 5.0)], &[(1.0001, 4.0)]);
    let err = b.apply(&bad).unwrap_err();
    assert!(matches!(err, ApplyError::Crossed { .. }));
}

#[test]
fn levels_iterate_from_the_best_price_inward() {
    let mut b = book();
    b.apply(&snapshot(
        10,
        &[(0.9998, 1.0), (1.0000, 5.0), (0.9999, 3.0)],
        &[(1.0002, 2.0), (1.0001, 4.0)],
    ))
    .unwrap();

    let bids: Vec<i64> = b.levels(Side::Bid).map(|(t, _)| t).collect();
    assert_eq!(
        bids,
        vec![px(1.0000) / TICK, px(0.9999) / TICK, px(0.9998) / TICK]
    );

    let asks: Vec<i64> = b.levels(Side::Ask).map(|(t, _)| t).collect();
    assert_eq!(asks, vec![px(1.0001) / TICK, px(1.0002) / TICK]);
}

/// A7: логика уровней пишется generic над `MarketDepth` крейта, и эта книга
/// его удовлетворяет. Тест вызывает трейт через обобщённую функцию — если бы
/// реализация была адаптером, здесь бы понадобилась вторая.
#[test]
fn book_satisfies_the_crate_market_depth_trait() {
    use hftbacktest::depth::{MarketDepth, INVALID_MAX, INVALID_MIN};

    fn spread<MD: MarketDepth>(d: &MD) -> f64 {
        d.best_ask() - d.best_bid()
    }
    fn best_bid_tick<MD: MarketDepth>(d: &MD) -> i64 {
        d.best_bid_tick()
    }

    let mut b = book();
    // Пустая книга обязана отдавать сентинелы крейта, а не ноль: ноль —
    // это цена, и стратегия приняла бы его за настоящую.
    assert_eq!(best_bid_tick(&b), INVALID_MIN);
    assert_eq!(MarketDepth::best_ask_tick(&b), INVALID_MAX);

    b.apply(&snapshot(10, &[(1.0000, 5.0)], &[(1.0001, 4.0)]))
        .unwrap();

    assert!((spread(&b) - 0.0001).abs() < 1e-9);
    assert_eq!(best_bid_tick(&b), px(1.0000) / TICK);
    assert!((MarketDepth::tick_size(&b) - 0.0001).abs() < 1e-12);
    assert!((MarketDepth::lot_size(&b) - 0.001).abs() < 1e-12);
    assert!((MarketDepth::best_bid_qty(&b) - 5.0).abs() < 1e-9);
    assert!((b.bid_qty_at_tick(px(1.0000) / TICK) - 5.0).abs() < 1e-9);
    assert_eq!(b.ask_qty_at_tick(px(0.5) / TICK), 0.0);
}

/// Целые тики вместо `f64` существуют ровно ради этого случая: цена, которую
/// сложение с плавающей точкой сдвинуло бы на последнем бите, обязана попасть
/// в тот же уровень, а не породить соседний.
#[test]
fn prices_that_break_f64_land_on_one_level() {
    let mut b = book();
    b.apply(&snapshot(10, &[(0.3, 1.0)], &[(0.4, 1.0)]))
        .unwrap();

    let sum = 0.1_f64 + 0.2_f64; // != 0.3 в f64
    assert_ne!(sum, 0.3_f64);

    let tick_from_sum = px(sum) / TICK;
    let tick_from_literal = px(0.3) / TICK;
    assert_eq!(tick_from_sum, tick_from_literal);
    assert_eq!(b.qty_lots_at(Side::Bid, tick_from_sum), px(1.0) / STEP);
}
