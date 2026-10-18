use super::*;

// Цена ≈ 100 000 тиков: 1 bps ≈ 10 тиков. Бид-стена на 99 950, чужая (аск) 100 000, своя лучшая 99 999 → 5 bps,
// корзина — тики 99 941…99 950 (разрыв 50…59).
const ASK: i64 = 100_000;
const OWN: i64 = 99_999;
const WALL: i64 = 99_950;

fn free(_: i64) -> bool {
    false
}

#[test]
fn bid_basket_is_same_integer_bps_without_wall() {
    for key in 0..200u64 {
        let t = pick_twin(Side::Bid, WALL, OWN, ASK, key, free).unwrap();
        assert_eq!(t.candidates, 9);
        assert!((99_941..=99_949).contains(&t.tick), "{}", t.tick);
        assert_eq!(gap_bps(ASK - t.tick, t.tick), gap_bps(ASK - WALL, WALL));
    }
}

#[test]
fn same_key_same_twin_different_keys_spread() {
    let a = pick_twin(Side::Bid, WALL, OWN, ASK, 7, free).unwrap();
    assert_eq!(a, pick_twin(Side::Bid, WALL, OWN, ASK, 7, free).unwrap());
    let seen: std::collections::BTreeSet<i64> = (0..200u64)
        .map(|k| pick_twin(Side::Bid, WALL, OWN, ASK, k, free).unwrap().tick)
        .collect();
    assert!(seen.len() > 5);
}

#[test]
fn dense_ticks_are_excluded_and_all_dense_is_none() {
    let dense = |p: i64| p >= 99_945;
    for key in 0..50u64 {
        let t = pick_twin(Side::Bid, WALL, OWN, ASK, key, dense).unwrap();
        assert!(t.tick < 99_945);
        assert_eq!(t.candidates, 4);
    }
    assert_eq!(pick_twin(Side::Bid, WALL, OWN, ASK, 1, |_| true), None);
}

#[test]
fn twin_stays_behind_own_best() {
    // лучшая своя 99 948: корзина обрезается сверху, стена 99 941 (разрыв 59, те же 5 bps)
    let t = pick_twin(Side::Bid, 99_941, 99_948, ASK, 3, free).unwrap();
    assert_eq!(t.candidates, 6);
    assert!(t.tick < 99_948 && t.tick != 99_941);
}

#[test]
fn coarse_tick_has_empty_basket() {
    assert_eq!(pick_twin(Side::Bid, 95, 99, 100, 5, free), None);
}

#[test]
fn ask_side_mirrors() {
    let bid = 100_000;
    let wall = 100_050;
    let own = 100_001;
    let d = gap_bps(wall - bid, wall);
    for key in 0..100u64 {
        let t = pick_twin(Side::Ask, wall, own, bid, key, free).unwrap();
        assert_ne!(t.tick, wall);
        assert!(t.tick > own);
        assert_eq!(gap_bps(t.tick - bid, t.tick), d);
    }
}

#[test]
fn key_depends_on_symbol_time_and_seed() {
    let k = twin_key("BTCUSDT", 1_000, 1);
    assert_eq!(k, twin_key("BTCUSDT", 1_000, 1));
    assert_ne!(k, twin_key("ETHUSDT", 1_000, 1));
    assert_ne!(k, twin_key("BTCUSDT", 1_001, 1));
    assert_ne!(k, twin_key("BTCUSDT", 1_000, 2));
}
