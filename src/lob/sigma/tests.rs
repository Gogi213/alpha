use super::*;

fn mid(ts_ms: i64, bid: i64, ask: i64) -> MidSample {
    MidSample {
        ts_ms,
        bid_tick: bid,
        ask_tick: ask,
    }
}

/// Середина 100.0 → 101.0 → 101.0 → 99.0 (тики ×2: 200, 202, 202, 198) по
/// секундам 1..4: доходности 100 bps, 0, −198.0 bps; σ за окно 3 с к
/// четвёртой секунде — sqrt(100² + 0 + 198.02²).
#[test]
fn sigma_is_root_of_summed_squared_one_second_returns() {
    let mids = vec![
        mid(1_000, 100, 100),
        mid(2_000, 101, 101),
        mid(3_000, 101, 101),
        mid(4_000, 99, 99),
    ];
    let s = SigmaSeries::from_mids(&mids);
    assert_eq!(s.len_secs(), 4);
    let r3 = (198.0 - 202.0) / 202.0 * 10_000.0;
    let expect = (100.0_f64.powi(2) + r3 * r3).sqrt();
    let got = s.sigma_bps(4_000, 3).unwrap();
    assert!((got - expect).abs() < 1e-9, "{got} vs {expect}");
    // Окно из одной секунды к четвёртой — только последняя доходность.
    let got1 = s.sigma_bps(4_999, 1).unwrap();
    assert!((got1 - r3.abs()).abs() < 1e-9, "{got1}");
}

/// Середина берётся «на момент» границы секунды: срез внутри секунды не
/// сдвигает границу, и последний срез не позже границы — тот, что считается.
#[test]
fn per_second_mid_is_as_of_the_second_boundary() {
    let mids = vec![
        mid(1_000, 100, 100),
        mid(1_400, 110, 110), // внутри второй секунды — виден на границе 2 000
        mid(2_000, 120, 120), // ровно на границе — он и берётся
        mid(2_900, 200, 200), // виден только на границе 3 000
        mid(3_000, 130, 130),
    ];
    let s = SigmaSeries::from_mids(&mids);
    // Секунда 2: 120 (не 110 и не 200); секунда 3: 130.
    let r2: f64 = (240.0 - 200.0) / 200.0 * 10_000.0;
    let r3: f64 = (260.0 - 240.0) / 240.0 * 10_000.0;
    let got = s.sigma_bps(3_000, 2).unwrap();
    assert!((got - (r2 * r2 + r3 * r3).sqrt()).abs() < 1e-9, "{got}");
}

/// Окно, упирающееся в начало записи, не определено — `None`, не ноль; за
/// концом ряда — тоже; неположительное окно — тоже.
#[test]
fn window_outside_the_series_is_undefined() {
    let mids: Vec<MidSample> = (0..10).map(|k| mid(1_000 + k * 1_000, 100, 100)).collect();
    let s = SigmaSeries::from_mids(&mids);
    assert_eq!(s.len_secs(), 10);
    // Ряд: секунды 1..=10. Окно 9 с к секунде 10 покрыто (начало = 1).
    assert_eq!(s.sigma_bps(10_000, 9), Some(0.0));
    // Окно 10 с к секунде 10 требует секунду 0 — её нет.
    assert_eq!(s.sigma_bps(10_000, 10), None);
    // Метка позже конца ряда.
    assert_eq!(s.sigma_bps(11_000, 1), None);
    assert_eq!(s.sigma_bps(10_000, 0), None);
    assert_eq!(SigmaSeries::from_mids(&[]).sigma_bps(0, 1), None);
}

/// Первый срез не на границе секунды: ряд начинается со следующей границы,
/// и на ней действует этот же срез (он не позже границы).
#[test]
fn series_starts_at_the_first_boundary_after_the_first_sample() {
    let mids = vec![
        mid(1_250, 100, 100),
        mid(2_500, 100, 102),
        mid(3_000, 100, 102),
    ];
    let s = SigmaSeries::from_mids(&mids);
    // Границы 2 000 (срез 1 250) и 3 000 (срез 3 000): две секунды.
    assert_eq!(s.len_secs(), 2);
    let r = (202.0 - 200.0) / 200.0 * 10_000.0;
    let got = s.sigma_bps(3_000, 1).unwrap();
    assert!((got - r).abs() < 1e-9, "{got}");
    assert_eq!(s.sigma_bps(2_000, 1), None);
}

/// Разрыв срезов внутри окна не роняет ряд: середина тянется через него,
/// скачок — одна доходность.
#[test]
fn gap_in_samples_carries_the_mid_forward() {
    let mids = vec![mid(1_000, 100, 100), mid(5_000, 110, 110)];
    let s = SigmaSeries::from_mids(&mids);
    assert_eq!(s.len_secs(), 5);
    let r = (220.0 - 200.0) / 200.0 * 10_000.0;
    let got = s.sigma_bps(5_000, 4).unwrap();
    assert!((got - r).abs() < 1e-9, "{got}");
    assert_eq!(s.sigma_bps(4_000, 3), Some(0.0));
}

/// Прореживание реплея (`thin_mids_tail_to_second_boundaries`,
/// `MidsKeep::PerSecond`) оставляет ровно те срезы, на которые смотрит ряд:
/// `SigmaSeries` на прореженном ряду равен ряду на полном. Срезы — по
/// нескольку на секунду, с точными границами и с пропусками секунд.
#[test]
fn thinned_samples_give_the_same_series_as_the_full_ones() {
    let mut full: Vec<MidSample> = Vec::new();
    let mut state: u64 = 0x9E37_79B9_7F4A_7C15;
    let mut ts: i64 = 250;
    let mut bid: i64 = 10_000;
    for _ in 0..5_000 {
        state = state
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1);
        let r = (state >> 33) as i64;
        // Шаг 0…599 мс: по нескольку срезов на секунду, изредка секунда
        // пустая; каждое 7-е — ровно на границу.
        ts += r % 600;
        if r % 7 == 0 {
            ts = ts.div_euclid(1_000) * 1_000 + 1_000;
        }
        bid += (r % 5) - 2;
        full.push(mid(ts, bid, bid + 3));
    }
    let mut thinned: Vec<MidSample> = Vec::new();
    for m in &full {
        thinned.push(*m);
        thin_mids_tail_to_second_boundaries(&mut thinned);
    }
    assert!(
        thinned.len() < full.len() / 2,
        "{} из {}",
        thinned.len(),
        full.len()
    );
    assert_eq!(
        SigmaSeries::from_mids(&thinned),
        SigmaSeries::from_mids(&full)
    );
}
