use super::eg::EgSums;
use super::egmat::*;

fn lcg(seed: u64) -> impl FnMut() -> f64 {
    let mut st = seed;
    move || {
        st = st
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        ((st >> 11) as f64 / (1u64 << 53) as f64) * 2.0 - 1.0
    }
}

/// `s` символов по `t` отсчётов: блуждания разного уровня, у части пар общий фактор, пропуски и длинная дыра.
fn rows(s: usize, t: usize) -> Vec<Vec<f64>> {
    let mut u = lcg(99);
    let mut f = vec![0f64; t];
    for k in 1..t {
        f[k] = f[k - 1] + 1e-3 * u();
    }
    (0..s)
        .map(|i| {
            let mut e = 0.0;
            (0..t)
                .map(|k| {
                    e = 0.9 * e + 1e-3 * u();
                    let lvl = 3.0 + 1.7 * i as f64;
                    if (i == 1 && (300..700).contains(&k)) || u() > 0.93 {
                        f64::NAN
                    } else if i % 2 == 0 {
                        lvl + 0.8 * f[k] + e
                    } else {
                        lvl + 1e-3 * (k as f64).sqrt() * u() + e
                    }
                })
                .collect()
        })
        .collect()
}

fn close(a: f64, b: f64, tol: f64) -> bool {
    (a - b).abs() <= tol * (1.0 + b.abs())
}

fn same(a: &EgSums, b: &EgSums) -> bool {
    [
        (a.n, b.n),
        (a.x, b.x),
        (a.x1, b.x1),
        (a.y, b.y),
        (a.y1, b.y1),
        (a.xx, b.xx),
        (a.x1x1, b.x1x1),
        (a.xx1, b.xx1),
        (a.yy, b.yy),
        (a.y1y1, b.y1y1),
        (a.yy1, b.yy1),
        (a.xy, b.xy),
        (a.x1y1, b.x1y1),
        (a.xy1, b.xy1),
        (a.x1y, b.x1y),
    ]
    .iter()
    .all(|&(p, q)| close(p, q, 1e-9))
}

/// Центровка на символ — как внутри `PackedEg`: суммы эталона берутся по тем же сдвинутым рядам.
fn centered(v: &[f64]) -> Vec<f64> {
    let f: Vec<f64> = v.iter().copied().filter(|a| a.is_finite()).collect();
    let m = f.iter().sum::<f64>() / f.len() as f64;
    v.iter().map(|a| a - m).collect()
}

#[test]
fn blocks_match_direct_sums_every_pair_and_window() {
    let (s, t) = (7, 3000);
    let r = rows(s, t);
    let c: Vec<Vec<f64>> = r.iter().map(|v| centered(v)).collect();
    let refs: Vec<&[f64]> = r.iter().map(|v| v.as_slice()).collect();
    let p = PackedEg::new(&refs).unwrap();
    let bc = 5; // блок = 5 кусков = 640 отсчётов, всего 5 блоков (последний неполный)
    let b = eg_blocks(&p, bc, 3);
    assert_eq!(b.blocks, t.div_ceil(CH).div_ceil(bc));
    let bo = bc * CH;
    for (lo, hi) in [(0usize, b.blocks), (1, 3), (2, 5), (0, 2)] {
        for i in 0..s {
            for j in (i + 1)..s {
                let got = b.pair_window(i, j, lo, hi);
                // окно блоков = отсчёты k ∈ [lo·bo, hi·bo): ряд берём с k−1 для первого отсчёта
                let (a, z) = (lo * bo, (hi * bo).min(t));
                let from = a.saturating_sub(1);
                let want = EgSums::from_series(&c[i][from..z], &c[j][from..z]);
                assert!(
                    same(&got, &want),
                    "пара {i},{j} окно {lo}..{hi}: {got:?} {want:?}"
                );
            }
        }
    }
}

#[test]
fn swapped_sums_give_reverse_regression() {
    let r = rows(4, 2000);
    let c: Vec<Vec<f64>> = r.iter().map(|v| centered(v)).collect();
    let a = EgSums::from_series(&c[0], &c[2]);
    let b = EgSums::from_series(&c[2], &c[0]);
    assert!(same(&a.swapped(), &b));
    let (t1, t2) = (a.swapped().test(100).unwrap(), b.test(100).unwrap());
    assert!(close(t1.t_adf, t2.t_adf, 1e-9) && close(t1.beta, t2.beta, 1e-9));
}

#[test]
fn threads_do_not_change_sums() {
    let r = rows(9, 1500);
    let refs: Vec<&[f64]> = r.iter().map(|v| v.as_slice()).collect();
    let p = PackedEg::new(&refs).unwrap();
    let (a, b) = (eg_blocks(&p, 4, 1), eg_blocks(&p, 4, 5));
    for i in 0..9 {
        for j in (i + 1)..9 {
            assert_eq!(a.pair_window(i, j, 0, 99), b.pair_window(i, j, 0, 99));
        }
    }
}
