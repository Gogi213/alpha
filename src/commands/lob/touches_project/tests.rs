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
