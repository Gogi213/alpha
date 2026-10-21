use super::*;

fn series(f: impl Fn(usize) -> f64) -> Vec<f64> {
    (0..600).map(f).collect()
}

#[test]
fn pearson_perfect_and_degenerate() {
    let a = [1.0, 2.0, 3.0, 4.0];
    assert!((pearson(&a, &[2.0, 4.0, 6.0, 8.0]).unwrap() - 1.0).abs() < 1e-12);
    assert!((pearson(&a, &[4.0, 3.0, 2.0, 1.0]).unwrap() + 1.0).abs() < 1e-12);
    assert!(pearson(&a, &[5.0; 4]).is_none());
    assert!(pearson(&[1.0, 2.0], &[1.0, 2.0]).is_none());
}

#[test]
fn quantile_picks_lower_rank() {
    let v = [1.0, 2.0, 3.0, 4.0, 5.0];
    assert_eq!(quantile(&v, 0.0), 1.0);
    assert_eq!(quantile(&v, 0.5), 3.0);
    assert_eq!(quantile(&v, 1.0), 5.0);
    assert!(quantile(&[], 0.5).is_nan());
}

#[test]
fn lead_lag_finds_fut_leading_by_two_seconds() {
    // Псевдослучайная цена фьючерса; спот повторяет её с запаздыванием в 2 с.
    let mut x = 12345u64;
    let mut p = 100.0;
    let mut fut = Vec::new();
    for _ in 0..600 {
        x = x
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        p *= 1.0 + ((x >> 40) as f64 / (1u64 << 24) as f64 - 0.5) * 1e-3;
        fut.push(p);
    }
    let spot: Vec<f64> = (0..600usize).map(|i| fut[i.saturating_sub(2)]).collect();
    let best = lead_lag(&spot, &fut)
        .into_iter()
        .filter_map(|(k, c)| c.map(|c| (k, c)))
        .max_by(|a, b| a.1.total_cmp(&b.1))
        .unwrap();
    // r_F[t+k] ↔ r_S[t]: фьючерс опережает, значит максимум при k = −2.
    assert_eq!(best.0, -2);
}

#[test]
fn basis_and_drift() {
    let spot = series(|_| 100.0);
    let fut = series(|i| if i < 300 { 100.1 } else { 100.2 });
    let b = basis_bps(&spot, &fut);
    assert!((b[0] - 10.0).abs() < 1e-9 && (b[599] - 20.0).abs() < 1e-9);
    let d = basis_drift(&b, 100);
    assert_eq!(d.len(), 500);
    // Пары, пересекающие скачок в t = 300, дают +10 bps.
    assert_eq!(d.iter().filter(|x| (**x - 10.0).abs() < 1e-9).count(), 100);
    assert!(basis_drift(&b, 600).is_empty());
}

#[test]
fn forward_fill_and_missing_basis() {
    let f = forward_fill(&[f64::NAN, 1.0, f64::NAN, 2.0]);
    assert!(f[0].is_nan());
    assert_eq!(&f[1..], &[1.0, 1.0, 2.0]);
    let b = basis_bps(&[f64::NAN, 100.0], &[100.0, 100.0]);
    assert!(b[0].is_nan() && b[1] == 0.0);
}

#[test]
fn clock_histogram_median_and_zero_share() {
    let mut m = Market::new();
    m.note_clock(1_000_000_000, 1_000); // ровно нуль
    m.note_clock(1_000_000_000 + 5_000_000, 1_000); // 5 мс
    m.note_clock(1_000_000_000 + 9_000_000, 1_000); // 9 мс
    m.note_clock(1_000_000_000 - 1, 1_000); // отрицательное
    assert_eq!((m.total, m.zero, m.negative), (4, 1, 1));
    assert_eq!(m.clock_quantile(0.5), 5.0);
    assert_eq!(m.clock_quantile(1.0), 9.0);
}

#[test]
fn mid_grid_takes_last_in_second() {
    let mut m = Market::new();
    m.note_mid(86_400_000 + 1_100, 10.0);
    m.note_mid(86_400_000 + 1_900, 11.0);
    assert_eq!(m.base_sec, Some(86_400));
    assert_eq!(m.mid[1], 11.0);
}

#[test]
fn mid_before_or_open_covers_first_second_only() {
    let mut m = Market::new();
    m.note_mid(86_400_000 + 300, 10.0); // первый снимок суток
    m.note_mid(86_400_000 + 1_500, 11.0);
    let fill = forward_fill(&m.mid);
    assert_eq!(m.mid_before(&fill, 86_400_000 + 371), None);
    assert_eq!(m.mid_before_or_open(&fill, 86_400_000 + 371), Some(10.0));
    assert_eq!(m.mid_before_or_open(&fill, 86_400_000 + 1_800), Some(10.0));
    assert_eq!(m.mid_before_or_open(&fill, 86_400_000 + 2_800), Some(11.0));
    assert_eq!(m.mid_before_or_open(&fill, 86_400_000 - 1), None);
}
