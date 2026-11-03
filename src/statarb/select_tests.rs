use super::egmat::{eg_blocks, PackedEg, CH};
use super::select::*;

fn lcg(seed: u64) -> impl FnMut() -> f64 {
    let mut st = seed;
    move || {
        st = st
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        ((st >> 11) as f64 / (1u64 << 53) as f64) * 2.0 - 1.0
    }
}

fn rel(a: f64, b: f64) -> f64 {
    (a - b).abs() / b.abs()
}

#[test]
fn norm_cdf_matches_published_tail_values() {
    // опубликованные значения Q(z) = Φ(−z)
    for (z, q) in [
        (1.644_853_626_951_472_7, 0.05),
        (2.0, 2.275_013_194_817_92e-2),
        (3.0, 1.349_898_031_630_09e-3),
        (4.0, 3.167_124_183_311_99e-5),
        (5.0, 2.866_515_718_791_94e-7),
        (6.0, 9.865_876_450_377e-10),
    ] {
        assert!(rel(norm_cdf(-z), q) < 1e-9, "z={z}: {}", norm_cdf(-z));
    }
    assert!(rel(norm_cdf(0.0), 0.5) < 1e-15);
    assert!((norm_cdf(1.3) + norm_cdf(-1.3) - 1.0).abs() < 1e-15);
    // стык ряда и цепной дроби
    assert!(rel(norm_cdf(-2.999_999), norm_cdf(-3.000_001)) < 1e-5);
}

#[test]
fn eg_pvalue_hits_published_critical_values_and_is_continuous() {
    // MacKinnon, N = 2, константа: асимптотические критические значения 1 / 5 / 10 %
    for (tau, p) in [(-3.9001, 0.01), (-3.3377, 0.05), (-3.0462, 0.10)] {
        assert!(
            (eg_pvalue(tau) - p).abs() < 1.5e-3,
            "tau={tau}: {}",
            eg_pvalue(tau)
        );
    }
    assert_eq!(eg_pvalue(1.0), 1.0);
    assert_eq!(eg_pvalue(-19.0), 0.0);
    assert_eq!(eg_pvalue(f64::NAN), 1.0);
    // стык веток при τ* = −2.62 и монотонность
    assert!((eg_pvalue(-2.62) - eg_pvalue(-2.620_001)).abs() < 5e-3);
    let mut prev = 0.0;
    for k in 0..400 {
        let p = eg_pvalue(-18.0 + 0.045 * f64::from(k));
        assert!(p >= prev - 1e-12, "k={k}");
        prev = p;
    }
}

/// 6 символов: 0 и 1 коинтегрированы (`y = 1.5·x + AR(½)`), остальные — независимые блуждания.
fn panel() -> Vec<Vec<f64>> {
    let (s, t) = (6, 6000);
    let mut u = lcg(7);
    let mut x = vec![0f64; t];
    for k in 1..t {
        x[k] = x[k - 1] + 2e-3 * u();
    }
    let mut e = 0.0;
    let y: Vec<f64> = (0..t)
        .map(|k| {
            e = 0.5 * e + 1e-3 * u();
            1.5 * x[k] + e
        })
        .collect();
    let mut rows = vec![x, y];
    for i in 2..s {
        let mut w = vec![3.0 + i as f64; t];
        for k in 1..t {
            w[k] = w[k - 1] + 2e-3 * u();
        }
        rows.push(w);
    }
    rows
}

fn blocks_of(rows: &[Vec<f64>]) -> super::egmat::EgBlocks {
    let refs: Vec<&[f64]> = rows.iter().map(|v| v.as_slice()).collect();
    let p = PackedEg::new(&refs).unwrap();
    // 6000 отсчётов = 47 кусков по CH; по 8 кусков в блоке — 6 блоков
    assert_eq!(6000usize.div_ceil(CH), 47);
    eg_blocks(&p, 8, 2)
}

#[test]
fn selects_only_the_cointegrated_pair_with_its_beta() {
    let eb = blocks_of(&panel());
    assert_eq!(eb.blocks, 6);
    let sp = SelectParams {
        lo: 0,
        hi: 6,
        min_obs: 100,
        alpha: 0.05,
    };
    let sel = select_pairs(&eb, &sp);
    assert_eq!(sel.len(), 1, "{sel:?}");
    let c = sel[0];
    assert_eq!((c.i, c.j), (0, 1));
    assert!(c.p < 1e-3 && c.t_first < -5.0 && c.t_second < -5.0, "{c:?}");
    // нога x = 0 или x = 1: β = 1.5 либо 1/1.5
    let want = if c.swapped { 1.0 / 1.5 } else { 1.5 };
    assert!(rel(c.beta, want) < 0.05, "{c:?}");
}

#[test]
fn short_window_or_independent_walks_select_nothing() {
    let eb = blocks_of(&panel());
    let mk = |lo, hi, alpha| SelectParams {
        lo,
        hi,
        min_obs: 100,
        alpha,
    };
    // окно из одного блока не делится на две половины
    assert!(select_pairs(&eb, &mk(2, 3, 0.05)).is_empty());
    // без коинтегрированной пары — только независимые блуждания
    let mut rows = panel();
    rows.remove(1);
    let noise = blocks_of(&rows);
    let sel = select_pairs(&noise, &mk(0, 6, 0.05));
    assert!(sel.is_empty(), "{sel:?}");
    // окно из четырёх блоков: половины по два блока, пара находится и там
    let sel = select_pairs(&eb, &mk(1, 5, 0.05));
    assert_eq!(sel.len(), 1, "{sel:?}");
}
