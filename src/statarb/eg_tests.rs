use super::eg::*;

fn lcg(seed: u64) -> impl FnMut() -> f64 {
    let mut st = seed;
    move || {
        st = st
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        ((st >> 11) as f64 / (1u64 << 53) as f64) * 2.0 - 1.0
    }
}

/// ln-цены: x — случайное блуждание вокруг 8,0; y = 1,7 + 0,8·x + шум(AR(1), ρ) либо независимое блуждание.
fn pair(t: usize, rho: f64, coint: bool, seed: u64) -> (Vec<f64>, Vec<f64>) {
    let mut u = lcg(seed);
    let (mut x, mut e, mut y2) = (8.0f64, 0.0f64, 6.0f64);
    let (mut xs, mut ys) = (Vec::new(), Vec::new());
    for k in 0..t {
        x += 1e-3 * u();
        e = rho * e + 1e-3 * u();
        y2 += 1e-3 * u();
        xs.push(if k % 11 == 5 || (400..520).contains(&k) {
            f64::NAN
        } else {
            x
        });
        let y = if coint { 1.7 + 0.8 * x + e } else { y2 };
        ys.push(if k % 13 == 7 { f64::NAN } else { y });
    }
    (xs, ys)
}

fn close(a: f64, b: f64, tol: f64) -> bool {
    (a - b).abs() <= tol * (1.0 + b.abs())
}

#[test]
fn sums_match_naive_with_gaps() {
    let (x, y) = pair(3000, 0.9, true, 7);
    let s = EgSums::from_series(&x, &y).test(100).unwrap();
    let n = eg_naive(&x, &y, 100).unwrap();
    assert_eq!(s.n, n.n);
    assert!(close(s.beta, n.beta, 1e-9), "{} {}", s.beta, n.beta);
    assert!(close(s.alpha, n.alpha, 1e-9), "{} {}", s.alpha, n.alpha);
    assert!(close(s.gamma, n.gamma, 1e-6), "{} {}", s.gamma, n.gamma);
    assert!(close(s.t_adf, n.t_adf, 1e-6), "{} {}", s.t_adf, n.t_adf);
}

#[test]
fn blocks_sum_to_window() {
    // окно = сумма блоков: суммы блоков по границам, общим для лага (пара k−1,k не разрывается — каждый блок
    // начинается с собственного «k−1»), итог равен прямому проходу по окну с теми же парами отсчётов
    let (x, y) = pair(3000, 0.9, true, 11);
    let mut w = EgSums::default();
    for (lo, hi) in [(0usize, 1000usize), (1000, 2000), (2000, 3000)] {
        let mut b = EgSums::default();
        for k in lo.max(1)..hi {
            let (a, a1, c, c1) = (x[k], x[k - 1], y[k], y[k - 1]);
            if a.is_finite() && a1.is_finite() && c.is_finite() && c1.is_finite() {
                b.add(a, a1, c, c1);
            }
        }
        w.merge(&b);
    }
    let d = EgSums::from_series(&x, &y);
    assert_eq!(w.n, d.n);
    assert!(close(w.xy, d.xy, 1e-12) && close(w.x1y, d.x1y, 1e-12) && close(w.yy1, d.yy1, 1e-12));
}

#[test]
fn cointegrated_pair_rejects_unit_root_independent_does_not() {
    let (xc, yc) = pair(20000, 0.9, true, 3);
    let (xi, yi) = pair(20000, 0.9, false, 3);
    let c = EgSums::from_series(&xc, &yc).test(100).unwrap();
    let i = EgSums::from_series(&xi, &yi).test(100).unwrap();
    assert!(c.t_adf < -5.0, "связанная пара: t {}", c.t_adf);
    assert!(i.t_adf > -3.5, "независимая пара: t {}", i.t_adf);
    assert!((c.beta - 0.8).abs() < 0.05, "β {}", c.beta);
}

#[test]
fn too_few_observations_is_none() {
    let (x, y) = pair(80, 0.9, true, 1);
    assert!(EgSums::from_series(&x, &y).test(100).is_none());
    assert!(eg_naive(&x, &y, 100).is_none());
}
