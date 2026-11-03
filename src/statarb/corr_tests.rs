use super::corr::*;

fn series(s: usize, t: usize) -> Vec<Vec<f64>> {
    let mut st = 12345u64;
    let mut u = move || {
        st = st
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        ((st >> 11) as f64 / (1u64 << 53) as f64) * 2.0 - 1.0
    };
    let f: Vec<f64> = (0..t).map(|_| u()).collect();
    (0..s)
        .map(|i| {
            (0..t)
                .map(|k| {
                    // у символа 1 длинная дыра, у остальных случайные пропуски
                    if (i == 1 && (300..700).contains(&k)) || u() > 0.9 {
                        f64::NAN
                    } else {
                        1e-3 * (0.5 * f[k] + 0.5 * u())
                    }
                })
                .collect()
        })
        .collect()
}

#[test]
fn gram_equals_naive_pairs_with_gaps() {
    // t не кратно CH: хвост блока дополняется нулями с маской 0
    let rows = series(11, 1000);
    let refs: Vec<&[f64]> = rows.iter().map(|r| r.as_slice()).collect();
    let p = Packed::new(&refs).unwrap();
    for threads in [1, 3] {
        let g = gram(&p, 30, threads);
        for i in 0..11 {
            for j in (i + 1)..11 {
                let (c, n) = naive_pair(&rows[i], &rows[j], 30);
                assert_eq!(g.n[i * 11 + j], n, "n {i},{j}");
                assert!(
                    (c - g.corr[i * 11 + j]).abs() < 2e-4,
                    "corr {i},{j}: {c} против {}",
                    g.corr[i * 11 + j]
                );
            }
        }
        assert!(g.corr[11].is_nan(), "нижний треугольник не заполняется");
    }
}

#[test]
fn min_obs_gives_nan() {
    let rows = series(3, 200);
    let refs: Vec<&[f64]> = rows.iter().map(|r| r.as_slice()).collect();
    let g = gram(&Packed::new(&refs).unwrap(), 10_000, 1);
    assert!(g.corr[1].is_nan() && g.corr[2].is_nan());
}

#[test]
fn rows_of_different_length_rejected() {
    let a = vec![0.0; 10];
    let b = vec![0.0; 11];
    assert!(Packed::new(&[&a, &b]).is_err());
}

#[test]
fn window_of_blocks_equals_direct_gram_of_slice() {
    // блок = 2 куска по CH отсчётов; окно блоков 1..3 = отсчёты [2·CH .. 6·CH) — те же куски, что у прямого расчёта среза
    let rows = series(9, 8 * CH - 100);
    let refs: Vec<&[f64]> = rows.iter().map(|r| r.as_slice()).collect();
    let bm = block_moments(&Packed::new(&refs).unwrap(), 2, 2);
    assert_eq!(bm.blocks, 4);
    let w = bm.window(1, 3, 30);
    let cut: Vec<&[f64]> = rows.iter().map(|r| &r[2 * CH..6 * CH]).collect();
    let d = gram(&Packed::new(&cut).unwrap(), 30, 1);
    for i in 0..9 {
        for j in (i + 1)..9 {
            let k = i * 9 + j;
            assert_eq!(w.n[k], d.n[k], "n {i},{j}");
            assert!((w.corr[k] - d.corr[k]).abs() < 1e-5, "corr {i},{j}");
        }
    }
}

#[test]
fn kernels_are_bit_identical() {
    // нечётное число строк и строка с дырой: хвост пары строк у zip2 идёт одиночным проходом
    let rows = series(13, 2 * CH + 77);
    let refs: Vec<&[f64]> = rows.iter().map(|r| r.as_slice()).collect();
    let p = Packed::new(&refs).unwrap();
    let r = gram_with(&p, 30, 2, Kernel::Ref);
    for k in [Kernel::Zip, Kernel::Zip2] {
        let g = gram_with(&p, 30, 3, k);
        assert_eq!(g.n, r.n, "{k:?}: n");
        let same = g
            .corr
            .iter()
            .zip(&r.corr)
            .all(|(a, b)| a.to_bits() == b.to_bits());
        assert!(same, "{k:?}: corr не байт в байт");
    }
}
