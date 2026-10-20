use super::*;

const SAME: (i64, i64) = (100_000_000, 100_000_000);

#[test]
fn self_projection_keeps_raw_string() {
    // Равные mid и шаг: число не меняется, исходная строка возвращается как есть.
    let r = project_field("123450", Side::Bid, (50.5, 50.5), SAME).unwrap();
    assert_eq!(r.as_deref(), Some("123450"));
    let r = project_field("123450", Side::Ask, (50.5, 50.5), SAME).unwrap();
    assert_eq!(r.as_deref(), Some("123450"));
}

#[test]
fn empty_stays_empty() {
    assert_eq!(
        project_field("", Side::Bid, (1.0, 2.0), SAME)
            .unwrap()
            .as_deref(),
        Some("")
    );
}

#[test]
fn basis_shifts_and_rounds_passively() {
    // Фьючерс дороже спота на 1 %: 10 000 тиков → 10 100 ровно; чуть выше — bid вниз, ask вверх.
    let up = project_field("10000", Side::Bid, (100.0, 101.0), SAME).unwrap();
    assert_eq!(up.as_deref(), Some("10100"));
    let bid = project_field("10001", Side::Bid, (100.0, 101.0), SAME).unwrap();
    let ask = project_field("10001", Side::Ask, (100.0, 101.0), SAME).unwrap();
    assert_eq!(bid.as_deref(), Some("10101"));
    assert_eq!(ask.as_deref(), Some("10102"));
}

#[test]
fn unprojectable_is_none_and_garbage_is_error() {
    assert_eq!(
        project_field("10", Side::Bid, (0.0, 1.0), SAME).unwrap(),
        None
    );
    assert!(project_field("abc", Side::Bid, (1.0, 1.0), SAME).is_err());
}

#[test]
fn lots_equal_steps_keep_raw_string() {
    assert_eq!(convert_lots("00123", (10, 10)).unwrap(), "00123");
    assert_eq!(convert_lots("", (10, 1000)).unwrap(), "");
}

#[test]
fn lots_spot_to_fut_by_quantity() {
    // ADA: лот спота 0,01, лот фьючерса 1 → 370 лотов спота = 3,7 монеты = 4 лота фьючерса (до ближайшего).
    let (s, f) = (10_000_000, 1_000_000_000);
    assert_eq!(convert_lots("370", (s, f)).unwrap(), "4");
    assert_eq!(convert_lots("349", (s, f)).unwrap(), "3");
    assert_eq!(convert_lots("350", (s, f)).unwrap(), "4");
    assert_eq!(convert_lots("0", (s, f)).unwrap(), "0");
    // Лот фьючерса мельче — количество растёт кратно.
    assert_eq!(convert_lots("7", (f, s)).unwrap(), "700");
}

#[test]
fn lots_overflow_and_bad_steps_are_errors() {
    assert!(convert_lots("9223372036854775807", (1_000_000_000, 1)).is_err());
    assert!(convert_lots("5", (0, 1)).is_err());
    assert!(convert_lots("x", (1, 2)).is_err());
}

#[test]
fn approach_lot_columns_cover_lots_not_ticks_or_times() {
    use crate::commands::lob::touches::APPROACHES_COLUMNS;
    let lots: Vec<&str> = APPROACHES_COLUMNS
        .iter()
        .copied()
        .filter(|n| approach_lot_column(n))
        .collect();
    assert_eq!(
        lots,
        [
            "size_at_arm",
            "flow_1h_lots",
            "depth_behind_lots",
            "frontrun_lots_at_arm",
            "traded_lots_at_arm",
            "size_max_at_arm",
            "eat_60s_lots",
            "size_max_60s_lots",
            "depth_behind50_lots_at_arm",
        ]
    );
    for n in [
        "price_tick",
        "best_own_tick",
        "best_opp_tick",
        "arm_ms",
        "arm_dist_bps",
    ] {
        assert!(!approach_lot_column(n), "{n}");
    }
}

#[test]
fn approach_lot_columns_take_r1_lots() {
    for n in crate::lob::r1::ArmR1::names() {
        let lots = n.contains("lots") || n.starts_with("trade_size_");
        assert_eq!(approach_lot_column(n), lots, "{n}");
    }
}

#[test]
fn shift_time_columns_exist_in_both_headers() {
    use crate::commands::lob::touches::{APPROACHES_COLUMNS, TOUCHES_COLUMNS};
    for n in time_columns(false) {
        assert!(TOUCHES_COLUMNS.contains(n), "{n}");
    }
    for n in time_columns(true) {
        assert!(APPROACHES_COLUMNS.contains(n), "{n}");
    }
    // Длительности и возраст при сдвиге не трогаются.
    for n in ["duration_ms", "age_ms"] {
        assert!(!time_columns(false).contains(&n) && !time_columns(true).contains(&n));
    }
}

#[test]
fn shift_time_adds_and_keeps_empty() {
    assert_eq!(shift_time("1000", 3_600_000).unwrap(), "3601000");
    assert_eq!(shift_time("1000", -7_200_000).unwrap(), "-7199000");
    assert_eq!(shift_time("", 5).unwrap(), "");
    assert!(shift_time("x", 5).is_err());
    assert!(shift_time("9223372036854775807", 1).is_err());
}

#[test]
fn day_start_is_utc_midnight() {
    assert_eq!(day_start_ms("2026-01-05").unwrap(), 1_767_571_200_000);
    assert!(day_start_ms("05.01.2026").is_err());
}

#[test]
fn shifted_price_uses_spot_mid_at_t_and_fut_mid_at_t_prime() {
    // d = (S(t) − P)/S(t); фьюч-цена = F(t′)·(1−d) = P·F(t′)/S(t): S(t′) сокращается.
    // S(t) = 100, стена 99 (d = 1 %), F(t′) = 105 → 103,95 → 10 395 тиков (шаги равны), bid вниз.
    let r = project_field("9900", Side::Bid, (100.0, 105.0), SAME).unwrap();
    assert_eq!(r.as_deref(), Some("10395"));
}
