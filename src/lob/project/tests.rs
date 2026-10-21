use super::*;

#[test]
fn identity_when_same_market() {
    for p in [1, 7, 4_999_999, 8_999_999_999_999] {
        for side in [Side::Bid, Side::Ask] {
            assert_eq!(
                project_tick(p, side, 10_000_000, 10_000_000, 61_234.5, 61_234.5),
                Some(p)
            );
        }
    }
}

#[test]
fn keeps_distance_in_bps_and_rounds_passively() {
    // Стена 0,5 % ниже спот-mid 100 → на фьючерсе mid 99,9 (базис −10 bps):
    // цена 99,9·0,995 = 99,4005 → bid 99,40 (вниз), ask 99,41 (вверх).
    let tick = 10_000_000; // 0,01
    let bid = project_tick(9_950, Side::Bid, tick, tick, 100.0, 99.9).unwrap();
    let ask = project_tick(9_950, Side::Ask, tick, tick, 100.0, 99.9).unwrap();
    assert_eq!((bid, ask), (9_940, 9_941));
}

#[test]
fn converts_between_tick_sizes() {
    // Спот-тик 0,0001, фьюч-тик 0,001: 0,5230 → 523 тика фьючерса.
    assert_eq!(
        project_tick(5_230, Side::Bid, 100_000, 1_000_000, 0.55, 0.55),
        Some(523)
    );
}

#[test]
fn rejects_bad_mid_and_ticks() {
    let t = 10_000_000;
    for (s, f) in [
        (0.0, 1.0),
        (1.0, 0.0),
        (f64::NAN, 1.0),
        (1.0, f64::INFINITY),
        (-1.0, 1.0),
    ] {
        assert_eq!(project_tick(100, Side::Bid, t, t, s, f), None);
    }
    assert_eq!(project_tick(100, Side::Bid, 0, t, 1.0, 1.0), None);
    assert_eq!(project_tick(100, Side::Bid, t, -1, 1.0, 1.0), None);
}
