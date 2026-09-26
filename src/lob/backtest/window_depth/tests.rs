use super::*;
use crate::lob::backtest::SignalWindows;
use hftbacktest::depth::{HashMapMarketDepth, L2MarketDepth};
use hftbacktest::types::{
    Event, BUY_EVENT, EXCH_ASK_DEPTH_EVENT, EXCH_BID_DEPTH_EVENT, EXCH_EVENT,
    LOCAL_ASK_DEPTH_EVENT, LOCAL_BID_DEPTH_EVENT, LOCAL_EVENT, TRADE_EVENT,
};

/// Детерминированный xorshift64* — случайные ленты без зависимости `rand`.
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

const TICK: f64 = 0.5;
const LOT: f64 = 0.1;

/// Одна строка стакана: цена вне сетки до ±0,45 тика (округление крейта), объём — ноль,
/// меньше половины лота (уровень снимается, хотя `qty != 0`), около лота или несколько лотов;
/// изредка — прыжок далеко от середины (растягивает `low_bid_tick`/`high_ask_tick`).
fn random_level(rng: &mut Rng) -> (bool, f64, f64) {
    let bid = rng.below(2) == 0;
    let tick = if rng.below(40) == 0 {
        1000 + rng.below(400) as i64 - 200
    } else {
        1000 + rng.below(24) as i64 - 12
    };
    let jitter = (rng.below(91) as f64 - 45.0) / 100.0;
    let px = (tick as f64 + jitter) * TICK;
    let qty = match rng.below(6) {
        0 | 1 => 0.0,
        2 => LOT * 0.3,
        3 => LOT * 0.7,
        _ => LOT * (1 + rng.below(50)) as f64,
    };
    (bid, px, qty)
}

fn crate_snapshot(d: &HashMapMarketDepth) -> DepthSnapshot {
    DepthSnapshot::of(d)
}

/// После **каждой** строки своя книга равна книге крейта по всем полям снимка: пересечения,
/// спрятанные уровни, снятие лучшего с поиском за ним, устаревшие границы поиска, объём меньше
/// полулота, цены вне сетки.
#[test]
fn every_update_matches_the_crate_book() {
    for seed in 1..=40u64 {
        let mut rng = Rng(seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) | 1);
        let mut own = WindowDepth::new(TICK, LOT);
        let mut reference = HashMapMarketDepth::new(TICK, LOT);
        for step in 0..3000 {
            let (bid, px, qty) = random_level(&mut rng);
            if bid {
                own.update_bid_depth(px, qty);
                reference.update_bid_depth(px, qty, step);
            } else {
                own.update_ask_depth(px, qty);
                reference.update_ask_depth(px, qty, step);
            }
            assert_eq!(
                own.snapshot(),
                crate_snapshot(&reference),
                "seed {seed}, шаг {step}"
            );
        }
    }
}

/// Лента нарочно проходит состояния, которые отличают книгу крейта от «чистой»: пересечение
/// прячет уровень, сторона пустеет при спрятанном уровне (граница сброшена — поиск его не видит).
#[test]
fn hidden_levels_and_reset_bounds_follow_the_crate() {
    let rows: &[(bool, f64, f64)] = &[
        (true, 10.0, 1.0),
        (false, 11.0, 1.0),
        (false, 12.0, 1.0),
        (true, 11.5, 1.0),  // бид выше аска 11 — аск 11 спрятан, лучший аск 12
        (false, 12.0, 0.0), // лучший аск снят: поиск выше 11,5 — пусто, граница сброшена
        (false, 13.0, 1.0),
        (true, 11.5, 0.0), // лучший бид снят: следующий — 10
        (false, 13.0, 0.0),
        (true, 10.0, 0.0),
        (false, 11.0, 0.04), // меньше полулота — снятие спрятанного аска
    ];
    let mut own = WindowDepth::new(0.5, 0.1);
    let mut reference = HashMapMarketDepth::new(0.5, 0.1);
    for (i, &(bid, px, qty)) in rows.iter().enumerate() {
        if bid {
            own.update_bid_depth(px, qty);
            reference.update_bid_depth(px, qty, 0);
        } else {
            own.update_ask_depth(px, qty);
            reference.update_ask_depth(px, qty, 0);
        }
        assert_eq!(own.snapshot(), crate_snapshot(&reference), "строка {i}");
    }
}

#[test]
fn range_search_matches_the_crate_bounds() {
    let levels = [(3, 1.0), (5, 1.0), (9, 1.0)];
    assert_eq!(depth_below(&levels, 9, 0), 5, "start не включается");
    assert_eq!(depth_below(&levels, 5, 3), 3, "end включается");
    assert_eq!(depth_below(&levels, 5, 4), INVALID_MIN);
    assert_eq!(depth_below(&levels, 3, INVALID_MAX), INVALID_MIN);
    assert_eq!(depth_below(&[], 3, 0), INVALID_MIN);
    assert_eq!(depth_above(&levels, 3, 9), 5, "start не включается");
    assert_eq!(depth_above(&levels, 5, 9), 9, "end включается");
    assert_eq!(depth_above(&levels, 5, 8), INVALID_MAX);
    assert_eq!(depth_above(&levels, 3, INVALID_MIN), INVALID_MAX);
    assert_eq!(depth_above(&[], 3, 9), INVALID_MAX);
}

/// Окна сутками случайной ленты: своя книга и эталон крейта дают те же окна (старт среза и
/// снимок по всем полям); часы коллектора и биржи расходятся в обе стороны, сделки в ленте
/// книгу не трогают.
#[test]
fn signal_windows_match_the_crate_path() {
    for seed in 1..=10u64 {
        let mut rng = Rng(seed.wrapping_mul(0xD1B5_4A32_D192_ED03) | 1);
        let mut events = Vec::new();
        let mut ts = 1_000_000i64;
        for _ in 0..20_000 {
            ts += rng.below(3_000) as i64;
            let skew = rng.below(2_001) as i64 - 1_000;
            let (bid, px, qty) = random_level(&mut rng);
            let ev = if rng.below(10) == 0 {
                EXCH_EVENT | LOCAL_EVENT | TRADE_EVENT | BUY_EVENT
            } else if bid {
                LOCAL_BID_DEPTH_EVENT | EXCH_BID_DEPTH_EVENT
            } else {
                LOCAL_ASK_DEPTH_EVENT | EXCH_ASK_DEPTH_EVENT
            };
            events.push(Event {
                ev,
                exch_ts: ts + skew,
                local_ts: ts,
                px,
                qty,
                order_id: 0,
                ival: 0,
                fval: 0.0,
            });
        }
        let t0s: Vec<i64> = (0..300)
            .map(|_| 1_000_000 + rng.below(31_000_000) as i64)
            .collect();
        let own = SignalWindows::build(&events, &t0s, TICK, LOT);
        let reference = SignalWindows::build_crate(&events, &t0s, TICK, LOT);
        assert!(own.len() > 250, "окна есть: {}", own.len());
        assert_eq!(own.first_mismatch(&reference), None, "seed {seed}");
    }
}

#[test]
fn first_mismatch_names_the_field() {
    let events = vec![Event {
        ev: LOCAL_BID_DEPTH_EVENT | EXCH_BID_DEPTH_EVENT,
        exch_ts: 10,
        local_ts: 10,
        px: 5.0,
        qty: 1.0,
        order_id: 0,
        ival: 0,
        fval: 0.0,
    }];
    let a = SignalWindows::build(&events, &[20], TICK, LOT);
    let b = SignalWindows::build(&events, &[20], TICK, LOT * 30.0);
    assert_eq!(a.first_mismatch(&a), None);
    assert_eq!(a.first_mismatch(&b), Some((20, "bids")));
    let none = SignalWindows::build(&events, &[], TICK, LOT);
    assert_eq!(a.first_mismatch(&none), Some((0, "число окон")));
}
