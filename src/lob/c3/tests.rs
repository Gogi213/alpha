use super::*;

const DAY_MS: i64 = 86_400_000;
const SOD: i64 = 36_000;

struct Day {
    regime: Vec<u8>,
    mid: Vec<f64>,
    sigma: Vec<f64>,
    spans: Vec<Span>,
}

// Плоские сутки: mid 100, σ240 = 5 bps. Бид-стена с dist_sigma 2 → уровень 99,9 (тик 0,01 → 9990), mid на 10 bps выше.
fn flat() -> Day {
    flat_r(0)
}

fn flat_r(r: u8) -> Day {
    Day {
        regime: vec![r; DAY_S as usize],
        mid: vec![100.0; DAY_S as usize],
        sigma: vec![5.0; DAY_S as usize],
        spans: Vec::new(),
    }
}

fn ctx(d: &Day, idx: i32) -> DayCtx<'_> {
    DayCtx {
        day: idx,
        start_ms: i64::from(idx) * DAY_MS,
        regime: &d.regime,
        tick: 0.01,
        mid: &d.mid,
        sigma: &d.sigma,
        spans: &d.spans,
    }
}

fn wall(id: u64, sod_s: i64, cov0: f64) -> WallRec {
    WallRec {
        id,
        arm_ms: sod_s * 1000,
        age_ms: 10_000,
        side: Side::Bid,
        arm_dist_bps: 12.0,
        dist_sigma: 2.0,
        regime: 0,
        cov: [cov0, 0.0, 0.0],
    }
}

fn params(k: usize, tod: i64) -> Params {
    Params {
        horizon_s: 60,
        tod_window_s: tod,
        caliper_sd: 0.5,
        k,
        seed: 1,
        max_wait_s: 10,
        stride_s: 1,
    }
}

fn zero_cov(_: &CovIn) -> [f64; NCOV] {
    [0.0; NCOV]
}

#[test]
fn matches_other_day_with_pseudo_level_and_arm() {
    let (d0, d1) = (flat(), flat());
    let days = [ctx(&d0, 0), ctx(&d1, 1)];
    let (c, st) = select_controls("X", &[wall(1, SOD, 0.0)], &days, &params(1, 5), zero_cov);
    assert_eq!(c.len(), 1);
    assert!(st.unmatched.is_empty());
    assert_eq!(c[0].day, 1);
    assert_eq!(c[0].level_tick, 9990);
    assert_eq!(c[0].arm_ms - c[0].tau_ms, 10_000);
    assert!((c[0].tau_ms - (DAY_MS + (SOD - 10) * 1000)).abs() <= 5_000);
    assert_eq!(st.rejects[Reject::SameDay as usize], 1);
}

#[test]
fn same_day_and_other_regime_are_excluded() {
    let (d0, d1) = (flat(), flat_r(1));
    let (c, st) = select_controls(
        "X",
        &[wall(1, SOD, 0.0)],
        &[ctx(&d0, 0)],
        &params(1, 5),
        zero_cov,
    );
    assert!(c.is_empty());
    assert_eq!(st.unmatched, vec![0]);
    assert_eq!(st.rejects[Reject::SameDay as usize], 1);
    let (c, st) = select_controls(
        "X",
        &[wall(1, SOD, 0.0)],
        &[ctx(&d1, 1)],
        &params(1, 5),
        zero_cov,
    );
    assert!(c.is_empty());
    assert_eq!(st.rejects[Reject::Regime as usize], 11);
}

#[test]
fn real_wall_in_band_rejects_candidate() {
    let mut d1 = flat();
    d1.spans.push(Span {
        side: Side::Bid,
        tick: 9991,
        from_ms: DAY_MS,
        to_ms: 2 * DAY_MS,
    });
    let (c, st) = select_controls(
        "X",
        &[wall(1, SOD, 0.0)],
        &[ctx(&d1, 1)],
        &params(1, 5),
        zero_cov,
    );
    assert!(c.is_empty());
    assert_eq!(st.rejects[Reject::WallInBand as usize], 11);
}

#[test]
fn nearer_same_side_wall_rejects_candidate() {
    // σ_H = 5·√(60/240) = 2,5 bps; тик 9998 (99,98) — на 2 bps от mid и вне ±1 тика уровня 9990.
    let mut d1 = flat();
    d1.spans.push(Span {
        side: Side::Bid,
        tick: 9998,
        from_ms: DAY_MS,
        to_ms: 2 * DAY_MS,
    });
    let (c, st) = select_controls(
        "X",
        &[wall(1, SOD, 0.0)],
        &[ctx(&d1, 1)],
        &params(1, 5),
        zero_cov,
    );
    assert!(c.is_empty());
    assert_eq!(st.rejects[Reject::NearerWall as usize], 11);
}

#[test]
fn crossing_before_arm_is_rejected() {
    let mut d1 = flat();
    d1.mid[(SOD - 4) as usize] = 99.0;
    let day = DAY_S as usize;
    assert_eq!(d1.mid.len(), day);
    let (c, st) = select_controls(
        "X",
        &[wall(1, SOD, 0.0)],
        &[ctx(&d1, 1)],
        &params(1, 5),
        zero_cov,
    );
    assert!(st.rejects[Reject::Crossed as usize] >= 1);
    assert_eq!(c.len(), 1);
}

#[test]
fn no_arm_when_price_never_comes_close() {
    let mut d1 = flat();
    d1.mid.iter_mut().for_each(|m| *m = 100.0);
    let mut w = wall(1, SOD, 0.0);
    w.arm_dist_bps = 5.0;
    let (c, st) = select_controls("X", &[w], &[ctx(&d1, 1)], &params(1, 5), zero_cov);
    assert!(c.is_empty());
    assert_eq!(st.rejects[Reject::NoArm as usize], 11);
}

#[test]
fn no_replacement_across_walls() {
    let (d0, d1) = (flat(), flat());
    let days = [ctx(&d0, 0), ctx(&d1, 1)];
    let walls = [wall(1, SOD, 0.0), wall(2, SOD + 20, 0.0)];
    let (c, st) = select_controls("X", &walls, &days, &params(1, 5), zero_cov);
    assert_eq!(c.len(), 1);
    assert_eq!(st.unmatched.len(), 1);
    assert!(st.rejects[Reject::Reused as usize] > 0);
}

#[test]
fn k_controls_of_one_wall_do_not_overlap_and_run_is_deterministic() {
    let (d0, d1) = (flat(), flat());
    let days = [ctx(&d0, 0), ctx(&d1, 1)];
    let walls = [wall(1, SOD, 0.0)];
    let (c, _) = select_controls("X", &walls, &days, &params(2, 200), zero_cov);
    assert_eq!(c.len(), 2);
    let (a, b) = (&c[0], &c[1]);
    assert!(a.arm_ms + 60_000 <= b.tau_ms || b.arm_ms + 60_000 <= a.tau_ms);
    let (c2, _) = select_controls("X", &walls, &days, &params(2, 200), zero_cov);
    assert_eq!(format!("{c:?}"), format!("{c2:?}"));
}

#[test]
fn caliper_drops_wall_far_in_covariates() {
    let (d0, d1) = (flat(), flat());
    let days = [ctx(&d0, 0), ctx(&d1, 1)];
    // Две стены: cov0 = 0 и 10 (SD ≈ 7,07); контроль всегда cov 0 → вторая стена на 1,41 SD дальше caliper 0,5.
    let walls = [wall(1, 20_000, 0.0), wall(2, 60_000, 10.0)];
    let (c, st) = select_controls("X", &walls, &days, &params(1, 5), zero_cov);
    assert_eq!(c.len(), 1);
    assert_eq!(c[0].wall, 0);
    assert_eq!(st.unmatched, vec![1]);
    assert!(st.rejects[Reject::Caliper as usize] > 0);
}

#[test]
fn smd_and_swap_flags() {
    let a = [1.0, 2.0, 3.0, 4.0];
    assert_eq!(smd(&a, &a), 0.0);
    let b = [11.0, 12.0, 13.0, 14.0];
    assert!(smd(&b, &a) > 5.0);
    assert!(smd(&a[..1], &b).is_nan());
    let f = swap_flags(64, 7);
    assert_eq!(f, swap_flags(64, 7));
    assert!(f.iter().any(|&x| x) && f.iter().any(|&x| !x));
    assert_ne!(f, swap_flags(64, 8));
}

#[test]
fn second_series_is_asof_and_matches_sigma() {
    let ms = |ts, b, a| MidSample {
        ts_ms: ts,
        bid_tick: b,
        ask_tick: a,
    };
    let mids = [
        ms(-500, 9999, 10001),
        ms(2_300, 10_000, 10_002),
        ms(3_000, 10_100, 10_102),
    ];
    let sig = SigmaSeries::from_mids(&mids);
    let (mid, sigma) = second_series(&mids, &sig, 0, 5, 0.01);
    // Срез 2300 позже границы 2000 — сек 2 ещё на первом срезе; срез 3000 ≤ границы 3000 — сек 3 уже на нём.
    let near = |a: f64, b: f64| (a - b).abs() < 1e-9;
    assert!(near(mid[0], 100.0) && near(mid[2], 100.0));
    assert!(near(mid[3], 101.01) && near(mid[4], 101.01));
    assert!(sigma.iter().all(|v| v.is_nan()), "ряд короче окна 240 с");
}

#[test]
fn second_series_nan_before_first_sample() {
    let sig = SigmaSeries::from_mids(&[]);
    let m = [MidSample {
        ts_ms: 2_500,
        bid_tick: 100,
        ask_tick: 102,
    }];
    let (mid, _) = second_series(&m, &sig, 0, 4, 1.0);
    assert!(mid[0].is_nan() && mid[2].is_nan());
    assert!((mid[3] - 101.0).abs() < 1e-9);
}

#[test]
fn covariates_are_computed_after_arm() {
    let (d0, d1) = (flat(), flat());
    let days = [ctx(&d0, 0), ctx(&d1, 1)];
    // Кандидат: взвод через age=10 с после рождения ⇒ ковариата = s_arm − s0 = 10; стена с 10 подходит, с 20 — нет.
    let walls = [wall(1, 20_000, 10.0), wall(2, 60_000, 20.0)];
    let arm_gap = |c: &CovIn| [(c.s_arm - c.s0) as f64, 0.0, 0.0];
    let (c, st) = select_controls("X", &walls, &days, &params(1, 5), arm_gap);
    assert_eq!(c.len(), 1);
    assert_eq!(c[0].wall, 0);
    assert_eq!(st.unmatched, vec![1]);
}
