use super::*;
use crate::lob::backtest::fast_hold::apply_local;
use hftbacktest::depth::MarketDepth;
use hftbacktest::types::{BUY_EVENT, DEPTH_EVENT, EXCH_EVENT, LOCAL_EVENT, SELL_EVENT};

fn rows(n: usize) -> Vec<Event> {
    let mut s = 12345u64;
    let mut next = move || {
        s = s
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        s >> 33
    };
    (0..n)
        .map(|i| {
            let side = if next() % 2 == 0 {
                BUY_EVENT
            } else {
                SELL_EVENT
            };
            let off = (next() % 40) as i64;
            let tick = if side == BUY_EVENT {
                1000 - off
            } else {
                1001 + off
            };
            let qty = if next() % 4 == 0 {
                0.0
            } else {
                (next() % 90 + 1) as f64
            };
            let flags = if next() % 7 == 0 {
                EXCH_EVENT
            } else {
                EXCH_EVENT | LOCAL_EVENT
            };
            Event {
                ev: flags | side | DEPTH_EVENT,
                exch_ts: i as i64 * 10,
                local_ts: i as i64 * 10 + 3,
                px: tick as f64 * 0.1,
                qty,
                order_id: 0,
                ival: 0,
                fval: 0.0,
            }
        })
        .collect()
}

#[test]
fn book_at_matches_sequential_apply() {
    let tape = rows(3 * STRIDE + 517);
    let start = DepthSnapshot::of(&FastMarketDepth::new(0.1, 1.0));
    let mut tb = TapeBook::new(&tape, 0, &start, 0.1, 1.0);
    let mut reference = FastMarketDepth::new(0.1, 1.0);
    let mut bbo = Vec::new();
    for ev in &tape {
        if ev.is(LOCAL_EVENT) {
            apply_local(&mut reference, ev);
        }
        bbo.push((reference.best_bid_tick, reference.best_ask_tick));
    }
    tb.grow_to(tape.len());
    for (i, b) in bbo.iter().enumerate() {
        assert_eq!(tb.bbo_after(i), Some(*b), "bbo {i}");
    }
    for cursor in [
        0,
        1,
        STRIDE - 1,
        STRIDE,
        STRIDE + 1,
        2 * STRIDE,
        3 * STRIDE + 100,
        tape.len(),
    ] {
        let mut want = FastMarketDepth::new(0.1, 1.0);
        for ev in &tape[..cursor] {
            if ev.is(LOCAL_EVENT) {
                apply_local(&mut want, ev);
            }
        }
        assert_eq!(
            DepthSnapshot::of(&tb.book_at(cursor)),
            DepthSnapshot::of(&want),
            "cursor {cursor}"
        );
    }
}

#[test]
fn series_depth_matches_book() {
    let tape = rows(2 * STRIDE + 300);
    let start = DepthSnapshot::of(&FastMarketDepth::new(0.1, 1.0));
    let mut tb = TapeBook::new(&tape, 0, &start, 0.1, 1.0);
    tb.grow_to(tape.len());
    for cursor in [0, 1, 5, STRIDE, STRIDE + 7, 2 * STRIDE + 299, tape.len()] {
        let want = tb.book_at(cursor);
        let got = SeriesDepth::new(&tb, cursor).expect("ряд без clear");
        assert_eq!(got.best_bid_tick(), want.best_bid_tick(), "bid {cursor}");
        assert_eq!(got.best_ask_tick(), want.best_ask_tick(), "ask {cursor}");
        assert_eq!(got.best_bid().to_bits(), want.best_bid().to_bits());
        assert_eq!(got.best_ask().to_bits(), want.best_ask().to_bits());
        assert_eq!(got.best_bid_qty().to_bits(), want.best_bid_qty().to_bits());
        assert_eq!(got.best_ask_qty().to_bits(), want.best_ask_qty().to_bits());
        for tick in 900..1100 {
            assert_eq!(
                got.bid_qty_at_tick(tick).to_bits(),
                want.bid_qty_at_tick(tick).to_bits(),
                "bid@{tick} {cursor}"
            );
            assert_eq!(
                got.ask_qty_at_tick(tick).to_bits(),
                want.ask_qty_at_tick(tick).to_bits(),
                "ask@{tick} {cursor}"
            );
        }
    }
}
