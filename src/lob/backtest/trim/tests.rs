use super::*;
use hftbacktest::types::{
    Event, BUY_EVENT, EXCH_ASK_DEPTH_EVENT, EXCH_BID_DEPTH_EVENT, EXCH_EVENT, LOCAL_EVENT,
    TRADE_EVENT,
};

const TICK: f64 = 0.5;
const LOT: f64 = 0.1;

struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
    fn below(&mut self, n: u64) -> u64 {
        self.next() % n
    }
}

fn tape(seed: u64, n: usize) -> Vec<Event> {
    let mut rng = Rng(seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) | 1);
    let mut mid = 1000i64;
    (0..n)
        .map(|i| {
            if rng.below(50) == 0 {
                mid += rng.below(21) as i64 - 10;
            }
            let ev = if rng.below(10) == 0 {
                LOCAL_EVENT | EXCH_EVENT | TRADE_EVENT | BUY_EVENT
            } else if rng.below(2) == 0 {
                LOCAL_BID_DEPTH_EVENT | EXCH_BID_DEPTH_EVENT
            } else {
                LOCAL_ASK_DEPTH_EVENT | EXCH_ASK_DEPTH_EVENT
            };
            let tick = if rng.below(30) == 0 {
                mid + rng.below(300) as i64 - 150
            } else {
                mid + rng.below(41) as i64 - 20
            };
            let qty = if rng.below(3) == 0 {
                0.0
            } else {
                LOT * (1 + rng.below(40)) as f64
            };
            Event {
                ev,
                exch_ts: i as i64 + 3,
                local_ts: i as i64,
                px: tick as f64 * TICK,
                qty,
                order_id: 0,
                ival: 0,
                fval: 0.0,
            }
        })
        .collect()
}

fn qty_at(levels: &[(i64, f64)], tick: i64) -> f64 {
    levels
        .binary_search_by_key(&tick, |l| l.0)
        .map_or(0.0, |i| levels[i].1)
}

/// Книга по оставленной ленте совпадает с полной по лучшим и по всем тикам в полосе — в каждом состоянии.
#[test]
fn trimmed_book_matches_full_book_inside_the_band() {
    for seed in 1..=12u64 {
        for band in [0i64, 2, 5, 12] {
            let events = tape(seed, 3000);
            let kept = kept_rows(events.as_slice(), TICK, LOT, band);
            assert!(kept.windows(2).all(|w| w[0] < w[1]));
            assert_eq!(
                kept.last().copied(),
                Some(2999),
                "последняя строка остаётся"
            );
            let mut full = WindowDepth::new(TICK, LOT);
            let mut cut = WindowDepth::new(TICK, LOT);
            let mut ki = 0usize;
            for j in 0..=events.len() {
                let (fs, cs) = (full.snapshot(), cut.snapshot());
                assert_eq!(
                    fs.best_bid_tick, cs.best_bid_tick,
                    "seed {seed} band {band} s{j}"
                );
                assert_eq!(
                    fs.best_ask_tick, cs.best_ask_tick,
                    "seed {seed} band {band} s{j}"
                );
                for d in 0..=band {
                    if fs.best_bid_tick != hftbacktest::depth::INVALID_MIN {
                        let t = fs.best_bid_tick - d;
                        assert_eq!(
                            qty_at(&fs.bids, t),
                            qty_at(&cs.bids, t),
                            "bid seed {seed} band {band} s{j} t{t}"
                        );
                    }
                    if fs.best_ask_tick != hftbacktest::depth::INVALID_MAX {
                        let t = fs.best_ask_tick + d;
                        assert_eq!(
                            qty_at(&fs.asks, t),
                            qty_at(&cs.asks, t),
                            "ask seed {seed} band {band} s{j} t{t}"
                        );
                    }
                }
                if j == events.len() {
                    break;
                }
                let ev = &events[j];
                if ev.is(LOCAL_BID_DEPTH_EVENT) {
                    full.update_bid_depth(ev.px, ev.qty);
                } else if ev.is(LOCAL_ASK_DEPTH_EVENT) {
                    full.update_ask_depth(ev.px, ev.qty);
                }
                if ki < kept.len() && kept[ki] as usize == j {
                    if ev.is(LOCAL_BID_DEPTH_EVENT) {
                        cut.update_bid_depth(ev.px, ev.qty);
                    } else if ev.is(LOCAL_ASK_DEPTH_EVENT) {
                        cut.update_ask_depth(ev.px, ev.qty);
                    }
                    ki += 1;
                }
            }
        }
    }
}

#[test]
fn narrow_band_drops_rows_and_wide_band_keeps_all() {
    let events = tape(7, 3000);
    let narrow = kept_rows(events.as_slice(), TICK, LOT, 2);
    assert!(narrow.len() < events.len());
    let wide = kept_rows(events.as_slice(), TICK, LOT, i64::MAX / 4);
    assert_eq!(wide.len(), events.len());
}

#[test]
fn view_maps_rows_and_window_start() {
    let events = tape(3, 500);
    let kept = kept_rows(events.as_slice(), TICK, LOT, 1);
    let view = TrimRows::new(events.as_slice(), &kept);
    assert_eq!(EventRows::len(&view), kept.len());
    for (i, &k) in kept.iter().enumerate() {
        assert_eq!(view.row_local_ts(i), events[k as usize].local_ts);
    }
    let first_ge_100 = kept.iter().position(|&k| k >= 100).unwrap();
    assert_eq!(view.skip_to(100), first_ge_100);
    assert_eq!(view.skip_to(0), 0);
}
