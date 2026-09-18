use super::*;

fn mid(ts_ms: i64, bid: i64, ask: i64) -> MidSample {
    MidSample {
        ts_ms,
        bid_tick: bid,
        ask_tick: ask,
    }
}

/// Середина по секундам 1..=6: 100, 103, 99, 101, 97, 104 (тики; ×2 в ряду).
/// База 100 (×2 = 200). Окно 3 с после секунды 1 покрывает секунды 2..=4:
/// максимум 103 (+300 bps), минимум 99 (−100 bps). Бид: против 100, за 300;
/// аск: наоборот.
#[test]
fn excursion_is_the_extreme_move_each_way_within_the_window() {
    let mids = vec![
        mid(1_000, 100, 100),
        mid(2_000, 103, 103),
        mid(3_000, 99, 99),
        mid(4_000, 101, 101),
        mid(5_000, 97, 97),
        mid(6_000, 104, 104),
    ];
    let s = SecondMids::from_mids(&mids);
    assert_eq!(s.len_secs(), 6);
    assert_eq!(s.min_max_after(1_000, 3), Some((198, 206)));
    let bid = s.excursion(Side::Bid, 200, 1_000, 3).unwrap();
    assert!((bid.adverse_bps - 100.0).abs() < 1e-9, "{bid:?}");
    assert!((bid.favour_bps - 300.0).abs() < 1e-9, "{bid:?}");
    let ask = s.excursion(Side::Ask, 200, 1_000, 3).unwrap();
    assert!((ask.adverse_bps - 300.0).abs() < 1e-9, "{ask:?}");
    assert!((ask.favour_bps - 100.0).abs() < 1e-9, "{ask:?}");
    // Окно 5 с: секунды 2..=6 — минимум 97, максимум 104.
    assert_eq!(s.min_max_after(1_000, 5), Some((194, 208)));
    // Окно 6 с требует секунду 7 — её нет.
    assert_eq!(s.min_max_after(1_000, 6), None);
}

/// Середина, не ушедшая против, даёт ход против ровно 0, не отрицательный.
#[test]
fn a_move_only_one_way_gives_zero_on_the_other_side() {
    let mids: Vec<MidSample> = (0..5)
        .map(|k| mid(1_000 + k * 1_000, 100 + k, 100 + k))
        .collect();
    let s = SecondMids::from_mids(&mids);
    let e = s.excursion(Side::Bid, 200, 1_000, 4).unwrap();
    assert_eq!(e.adverse_bps, 0.0);
    assert!((e.favour_bps - 400.0).abs() < 1e-9, "{e:?}");
    assert_eq!(s.excursion(Side::Bid, 0, 1_000, 4), None, "база ≤ 0");
    assert_eq!(SecondMids::from_mids(&[]).min_max_after(0, 1), None);
}

/// Разреженная таблица против прямого перебора на псевдослучайном ряду —
/// все отрезки длиной 1..=64 на 300 секундах.
#[test]
fn sparse_table_matches_a_direct_scan() {
    let mut state: u64 = 0x2545_F491_4F6C_DD1D;
    let mut v: Vec<i64> = Vec::new();
    let mut x: i64 = 1_000;
    for _ in 0..300 {
        state = state
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1);
        x += ((state >> 33) as i64 % 21) - 10;
        v.push(x);
    }
    let mids: Vec<MidSample> = v
        .iter()
        .enumerate()
        .map(|(i, &m)| mid(i as i64 * 1_000, m, m))
        .collect();
    let s = SecondMids::from_mids(&mids);
    for from in 0..v.len() {
        for len in 1..=64usize {
            let to = from + len - 1;
            if to >= v.len() {
                break;
            }
            let got = s.min_max_after((from as i64 - 1) * 1_000, len as i64);
            let want_min = *v[from..=to].iter().min().unwrap() * 2;
            let want_max = *v[from..=to].iter().max().unwrap() * 2;
            assert_eq!(got, Some((want_min, want_max)), "from={from} len={len}");
        }
    }
}
