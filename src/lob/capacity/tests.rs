use super::*;
use crate::book::Update;

/// Книга с тиком 1 и шагом лота 1: цена в тиках и лоты совпадают с e9-единицами,
/// так проще читать тесты.
fn book() -> Book {
    Book::new(1, 1)
}

fn update(
    is_snapshot: bool,
    u: u64,
    cts_ms: i64,
    bids: &[(i64, i64)],
    asks: &[(i64, i64)],
) -> Update {
    Update {
        is_snapshot,
        depth: 50,
        u,
        seq: u,
        cts_ms,
        bids: bids.to_vec(),
        asks: asks.to_vec(),
    }
}

fn apply(tr: &mut CapacityTracker, book: &mut Book, up: &Update) {
    tr.before_update(up.cts_ms, book);
    book.apply(up).expect("обновление применимо");
    tr.after_update(up.cts_ms);
}

fn sell(tick: i64, lots: i64, exch_ms: i64) -> TradeHit {
    TradeHit {
        tick,
        lots,
        aggressor_is_buy: false,
        block: false,
        rpi: false,
        exch_ms,
    }
}

fn buy(tick: i64, lots: i64, exch_ms: i64) -> TradeHit {
    TradeHit {
        aggressor_is_buy: true,
        ..sell(tick, lots, exch_ms)
    }
}

/// Бид-стена на 10 000: полоса 70 bps = 70 тиков вверх (10 000 × 70 / 10⁴).
fn bid_target() -> Target {
    Target {
        side: Side::Bid,
        price_tick: 10_000,
        start_ms: 100_000,
        end_ms: 130_000,
    }
}

#[test]
fn band_ticks_floors_bps_to_whole_ticks() {
    assert_eq!(band_ticks(10_000, 70), 70);
    // 12 345 × 70 / 10 000 = 86.4 → 86 тиков.
    assert_eq!(band_ticks(12_345, 70), 86);
    // Мелкая цена в тиках: полоса уже одного тика — только сама стена.
    assert_eq!(band_ticks(100, 70), 0);
    assert_eq!(band_ticks(0, 70), 0);
    assert_eq!(band_ticks(10_000, 0), 0);
}

#[test]
fn rejects_bad_windows_and_band() {
    let t = [bid_target()];
    assert!(CapacityTracker::new(&[1_000, 1_000], 70, &t).is_err());
    assert!(CapacityTracker::new(&[0], 70, &t).is_err());
    assert!(CapacityTracker::new(&[1_000], 0, &t).is_err());
    let bad = Target {
        end_ms: 99_000,
        ..bid_target()
    };
    assert!(CapacityTracker::new(&[1_000], 70, &[bad]).is_err());
    // Порядок окон в аргументе — любой, внутри — по возрастанию.
    let tr = CapacityTracker::new(&[60_000, 1_000], 70, &t).expect("годные окна");
    assert_eq!(tr.pre_ms(), &[1_000, 60_000]);
}

#[test]
fn queue_is_last_frame_not_later_than_placement() {
    let t = bid_target();
    let mut tr = CapacityTracker::new(&[1_000, 60_000], 70, &[t]).expect("трекер");
    let mut book = book();
    // 40 с до касания: на 10 001 стоит 5, на 10 002 — 7; лучший аск 10 050.
    apply(
        &mut tr,
        &mut book,
        &update(
            true,
            1,
            60_000,
            &[(10_000, 100), (10_001, 5), (10_002, 7)],
            &[(10_050, 3)],
        ),
    );
    // 99 000 — ровно момент снимка `pre = 1 с`: обновление ровно в момент
    // ещё входит в снимок (последний кадр «не позже»).
    apply(
        &mut tr,
        &mut book,
        &update(false, 2, 99_000, &[(10_001, 9)], &[]),
    );
    // 99 500 — уже позже момента снимка `pre = 1 с`, но раньше `t0`.
    apply(
        &mut tr,
        &mut book,
        &update(false, 3, 99_500, &[(10_001, 11)], &[]),
    );
    // Кадр старта касания: очередь на 10 001 снята, лучший аск подошёл.
    apply(
        &mut tr,
        &mut book,
        &update(
            false,
            4,
            100_000,
            &[(10_001, 0), (10_002, 0)],
            &[(10_001, 2), (10_050, 0)],
        ),
    );
    let out = tr.finish(&book);
    assert_eq!(out.len(), 1);
    let c = &out[0];
    assert_eq!(c.band, 70);
    // Слот 0 — pre 1 с: кадр 99 000 (9 лотов), не 99 500 (11).
    assert_eq!(c.queue(1, 0), 9);
    assert_eq!(c.queue(2, 0), 7);
    // Слот 1 — pre 60 с (момент 40 000): кадров не позже нет → «неизвестно».
    assert_eq!(c.queue(1, 1), -1);
    assert_eq!(c.best(1), BestAt::UNKNOWN);
    // Слот 2 — t0: очередь снята, стена стоит.
    assert_eq!(c.queue(0, 2), 100);
    assert_eq!(c.queue(1, 2), 0);
    assert_eq!(
        c.best(2),
        BestAt {
            ours: 10_000,
            opposite: 10_001
        }
    );
    // Лучший бид слота 0 — 10 002 (стоит выше стены), не сама стена.
    assert_eq!(
        c.best(0),
        BestAt {
            ours: 10_002,
            opposite: 10_050
        }
    );
    assert_eq!(c.tick_at(70), 10_070);
}

#[test]
fn sold_splits_by_window_side_and_band() {
    let t = bid_target();
    let mut tr = CapacityTracker::new(&[1_000, 60_000], 70, &[t]).expect("трекер");
    let mut book = book();
    apply(
        &mut tr,
        &mut book,
        &update(true, 1, 30_000, &[(10_000, 100)], &[(10_100, 1)]),
    );
    // До окна 60 с — не считается.
    tr.observe_trade(sell(10_001, 3, 39_999));
    // В окне 60 с, вне окна 1 с.
    tr.observe_trade(sell(10_001, 5, 50_000));
    // В окне 1 с (и в 60 с тоже).
    tr.observe_trade(sell(10_001, 7, 99_500));
    // Касание: против нас на стене и на 10 002.
    tr.observe_trade(sell(10_000, 20, 100_000));
    tr.observe_trade(sell(10_002, 4, 120_000));
    // Агрессор-покупатель, блочная, RPI, нулевая — не наши сделки.
    tr.observe_trade(buy(10_002, 40, 120_000));
    tr.observe_trade(TradeHit {
        block: true,
        ..sell(10_002, 40, 120_000)
    });
    tr.observe_trade(TradeHit {
        rpi: true,
        ..sell(10_002, 40, 120_000)
    });
    tr.observe_trade(sell(10_002, 0, 120_000));
    // Вне полосы (71 тик).
    tr.observe_trade(sell(10_071, 40, 120_000));
    // Ровно на конце касания — входит; после конца — нет, даже если кадр книги
    // за окном ещё не приходил.
    tr.observe_trade(sell(10_070, 6, 130_000));
    tr.observe_trade(sell(10_002, 40, 130_001));
    // Метка сделки внутри окна после кадра книги за окном (часы книги и ленты
    // разные) — сделка не теряется.
    tr.after_update(131_000);
    tr.observe_trade(sell(10_002, 1, 129_999));
    let out = tr.finish(&book);
    let c = &out[0];
    assert_eq!(c.sold(1, 0), 7);
    assert_eq!(c.sold(1, 1), 12);
    assert_eq!(c.sold(1, 2), 0);
    assert_eq!(c.sold(0, 2), 20);
    assert_eq!(c.sold(2, 2), 5);
    assert_eq!(c.sold(70, 2), 6);
    assert_eq!(c.sold(2, 0), 0);
}

#[test]
fn ask_wall_mirrors_direction_and_aggressor() {
    let t = Target {
        side: Side::Ask,
        price_tick: 10_000,
        start_ms: 100_000,
        end_ms: 110_000,
    };
    let mut tr = CapacityTracker::new(&[1_000], 70, &[t]).expect("трекер");
    let mut book = book();
    apply(
        &mut tr,
        &mut book,
        &update(
            true,
            1,
            90_000,
            &[(9_900, 1)],
            &[(10_000, 50), (9_999, 8), (9_930, 2)],
        ),
    );
    // Против аска нас ест покупатель; продавец — нет.
    tr.observe_trade(buy(9_999, 5, 105_000));
    tr.observe_trade(sell(9_999, 50, 105_000));
    tr.observe_trade(buy(9_930, 3, 105_000));
    tr.observe_trade(buy(10_001, 9, 105_000));
    let out = tr.finish(&book);
    let c = &out[0];
    assert_eq!(c.tick_at(1), 9_999);
    assert_eq!(c.tick_at(70), 9_930);
    assert_eq!(c.queue(1, 0), 8);
    assert_eq!(c.queue(70, 0), 2);
    assert_eq!(c.queue(0, 0), 50);
    assert_eq!(c.sold(1, 1), 5);
    assert_eq!(c.sold(70, 1), 3);
    assert_eq!(
        c.best(0),
        BestAt {
            ours: 9_930,
            opposite: 9_900
        }
    );
}

#[test]
fn overlapping_targets_each_get_their_own_trades() {
    let a = bid_target();
    let b = Target {
        price_tick: 20_000,
        start_ms: 110_000,
        end_ms: 120_000,
        ..a
    };
    let c = Target {
        start_ms: 200_000,
        end_ms: 201_000,
        ..a
    };
    // Порядок целей — как подали, не по времени.
    let mut tr = CapacityTracker::new(&[1_000], 70, &[c, a, b]).expect("трекер");
    let mut book = book();
    apply(
        &mut tr,
        &mut book,
        &update(true, 1, 90_000, &[(10_000, 1), (20_000, 1)], &[(30_000, 1)]),
    );
    tr.observe_trade(sell(10_001, 2, 105_000));
    tr.observe_trade(sell(20_001, 3, 115_000));
    // Полоса стены 20 000 — 140 тиков, 20 001 в неё входит; 10 001 в полосу
    // стены 20 000 не входит (ниже стены).
    tr.observe_trade(sell(10_001, 100, 115_000));
    tr.observe_trade(sell(10_001, 4, 200_500));
    let out = tr.finish(&book);
    assert_eq!(out.len(), 3);
    assert_eq!(out[0].target, c);
    assert_eq!(out[1].target, a);
    assert_eq!(out[2].target, b);
    assert_eq!(out[1].sold(1, 1), 102);
    assert_eq!(out[2].band, 140);
    assert_eq!(out[2].sold(1, 1), 3);
    assert_eq!(out[0].sold(1, 1), 4);
}

#[test]
fn snapshots_after_last_frame_take_the_last_book() {
    let t = bid_target();
    let mut tr = CapacityTracker::new(&[1_000], 70, &[t]).expect("трекер");
    let mut book = book();
    // Единственный кадр задолго до касания: и `pre`, и `t0` читают его.
    apply(
        &mut tr,
        &mut book,
        &update(
            true,
            1,
            10_000,
            &[(10_000, 42), (10_003, 6)],
            &[(10_010, 1)],
        ),
    );
    let out = tr.finish(&book);
    assert_eq!(out[0].queue(0, 0), 42);
    assert_eq!(out[0].queue(0, 1), 42);
    assert_eq!(out[0].queue(3, 1), 6);
    assert_eq!(out[0].queue(4, 1), 0);
}

#[test]
fn no_frames_at_all_leaves_unknown() {
    let t = bid_target();
    let tr = CapacityTracker::new(&[1_000], 70, &[t]).expect("трекер");
    let book = book();
    let out = tr.finish(&book);
    assert_eq!(out[0].queue(0, 0), -1);
    assert_eq!(out[0].queue(0, 1), -1);
    assert_eq!(out[0].best(1), BestAt::UNKNOWN);
}
