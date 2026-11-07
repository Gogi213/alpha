//! Тесты эпизодов (TK-492): быстрая реализация против наивного эталона, питона v11 (`tk435-windows.py`) и numpy.

use super::cache::Contract;
use super::episodes::*;
use super::episodes_ref::*;
use super::params::Params;
use super::types::*;

const TPL: &str = r#"{
 "protocol":"P-34 r4",
 "grid_min":{"value":5},
 "episodes":{"D_min":@D@,"quantile_X_D":@Q@,
  "E4_wick":{"return_share":0.5,"within_min":15},"outlier_tail_candles_lt":3,
  "market_stratum":{"ref_symbol":"BTCUSDT","same_sign":true,"same_D":true,"min_overlap_share":0.5}},
 "features":{"windows_min":[5,15,30,60],"norm":{"history_days":7,"mad_factor":1.4826},
  "funding_z_history_days":30,"theta":[2,3,4]},
 "onset":{"L_pre_min":360},
 "controls":{"C2a":{"moments_per_first_trigger":10},
  "C2b":{"moments_per_episode":10,"exclude_same_sign_episode_window_min":[-360,1440]},
  "sigma_1m_window_days":7},
 "label_forward":{"D_min":[90,180],"H_min":[90,360,1440]},
 "split":{"fit":["2025-01","2025-12"],"test":["2026-01","2026-10"]}
}"#;

fn params(d: &str, q: &str) -> Params {
    Params::from_json(&TPL.replace("@D@", d).replace("@Q@", q)).unwrap()
}

/// xorshift64: детерминированный источник для синтетики.
struct Rng(u64);

impl Rng {
    fn bits(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }
    fn unit(&mut self) -> f64 {
        (self.bits() >> 11) as f64 / (1u64 << 53) as f64
    }
    fn below(&mut self, n: u64) -> u64 {
        self.bits() % n
    }
}

/// Случайное блуждание в ln с вставленными пампами/дампами, одиночными выбросами и дырами (NaN-close).
fn synth(seed: u64, n: usize, first_min: i64, holes: bool) -> Contract {
    let mut r = Rng(seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) | 1);
    let mut lp = vec![0.0f64; n];
    let mut x = 0.0;
    for v in lp.iter_mut() {
        x += (r.unit() - 0.5) * 0.004;
        *v = x;
    }
    if n > 0 {
        for _ in 0..1 + r.below(8) {
            let s = r.below(n as u64) as usize;
            let len = 90 + r.below(1500) as usize;
            let tot = r.unit() - 0.5;
            for (k, v) in lp.iter_mut().enumerate().skip(s) {
                *v += tot * (((k - s) as f64) / len as f64).min(1.0);
            }
        }
    }
    let mut close: Vec<f64> = lp.iter().map(|v| 10.0 * v.exp()).collect();
    if n > 0 {
        for _ in 0..r.below(4) {
            let k = r.below(n as u64) as usize;
            close[k] *= 0.6 + r.unit();
        }
        if holes {
            for _ in 0..1 + r.below(6) {
                let s = r.below(n as u64) as usize;
                let len = 1 + r.below(40) as usize;
                for v in close.iter_mut().skip(s).take(len) {
                    *v = f64::NAN;
                }
            }
            let lead = r.below(60) as usize;
            for v in close.iter_mut().take(lead) {
                *v = f64::NAN;
            }
        }
    }
    Contract {
        first_min,
        close,
        ..Default::default()
    }
}

fn fit_all(c: &Contract) -> [i64; 2] {
    [c.first_min, c.first_min + c.close.len() as i64]
}

fn same(a: f64, b: f64) -> bool {
    a.to_bits() == b.to_bits() || (a.is_nan() && b.is_nan())
}

fn same_table(a: &XdTable, b: &XdTable) -> bool {
    a.x.len() == b.x.len()
        && a.x
            .iter()
            .zip(&b.x)
            .all(|(p, q)| same(p[0], q[0]) && same(p[1], q[1]))
}

#[test]
fn leverage_names_like_tk435_regex() {
    // Регэксп ищется без привязки слева: имя оканчивается на UP/DOWN/BULL/BEAR или `<цифры>L|S`, затем
    // необязательно USDT/USD/_USDT/_USD; берётся часть до первого `-`.
    for s in [
        "3LUSDT",
        "3SUSDT",
        "10LUSDT",
        "BTCUPUSDT",
        "BTCDOWNUSDT",
        "BTCUP",
        "BTCDOWN",
        "BULLUSDT",
        "BEARUSDT",
        "ETHBULL",
        "ETHBEAR_USDT",
        "3L_USD",
        "BTCUP-USDT",
        "SETUPUSDT",
    ] {
        assert!(is_leverage(s), "{s} — плечо");
    }
    for s in [
        "1000PEPEUSDT",
        "SOLUSDT",
        "BTCUSDT",
        "1000SHIBUSDT",
        "10000SATSUSDT",
        "SPELLUSDT",
        "1INCHUSDT",
        "ETHUSDC",
        "USDT",
        "LUSDT",
        "SUSDT",
        "XRP-3L",
        "BTC-USDT",
        "",
    ] {
        assert!(!is_leverage(s), "{s} — не плечо");
    }
}

#[test]
fn fast_equals_naive_on_random_walks() {
    let p = params("[90,180,360,720,1440]", "0.97");
    let cases = [
        (1, 0, false),
        (2, 1, false),
        (3, 89, false),
        (4, 90, false),
        (5, 91, true),
        (6, 100, true),
        (7, 250, false),
        (8, 1500, true),
        (9, 3000, false),
        (10, 3000, true),
        (11, 4500, true),
        (12, 4500, false),
    ];
    let mut finder = Finder::default();
    for (seed, n, holes) in cases {
        let c = synth(seed, n, 1_234_567 + seed as i64, holes);
        let xd = xd_naive(&[&c], &p, fit_all(&c));
        let want = find_episodes_naive(&c, &p, &xd, "ABCUSDT");
        assert_eq!(
            find_episodes(&c, &p, &xd, "ABCUSDT"),
            want,
            "seed {seed} n {n}"
        );
        // Finder с чужими остатками в буферах даёт то же.
        assert_eq!(
            finder.find(&c, &p, &xd, "ABCUSDT"),
            want,
            "Finder seed {seed} n {n}"
        );
        if n >= 250 {
            assert!(
                !want.is_empty(),
                "вырожденный тест: нет эпизодов (seed {seed})"
            );
        }
        for w in want.windows(2) {
            assert!(
                (w[0].t0, w[0].di, w[0].sign) < (w[1].t0, w[1].di, w[1].sign),
                "порядок (t0, di, sign), seed {seed}"
            );
        }
        // Плечевое имя: пусто в обеих.
        assert!(find_episodes(&c, &p, &xd, "3LUSDT").is_empty());
        assert!(find_episodes_naive(&c, &p, &xd, "3LUSDT").is_empty());
    }
}

#[test]
fn fast_equals_naive_with_ties_and_degenerate_thresholds() {
    let p = params("[90,180]", "0.5");
    // Пила: много равных close — ничьи по минимуму/максимуму окна и по значениям хода.
    let saw: Vec<f64> = (0..900).map(|k| 100.0 + ((k * 7) % 5) as f64).collect();
    let c = Contract {
        first_min: 50,
        close: saw,
        ..Default::default()
    };
    for x in [0.0, 0.01, 0.03, 1.0, f64::INFINITY] {
        let xd = XdTable { x: vec![[x, x]; 2] };
        let got = find_episodes(&c, &p, &xd, "AUSDT");
        assert_eq!(got, find_episodes_naive(&c, &p, &xd, "AUSDT"), "X = {x}");
        if x == 0.0 {
            assert!(!got.is_empty());
        }
        if x.is_infinite() {
            assert!(got.is_empty());
        }
    }
    // NaN-порог (нет данных фита) — эпизодов нет.
    let xd = XdTable {
        x: vec![[f64::NAN; 2]; 2],
    };
    assert!(find_episodes(&c, &p, &xd, "AUSDT").is_empty());
    // Пустой и совсем короткий контракт.
    for n in [0usize, 1, 90] {
        let c = Contract {
            first_min: 7,
            close: vec![5.0; n],
            ..Default::default()
        };
        let xd = XdTable {
            x: vec![[0.0; 2]; 2],
        };
        assert!(find_episodes(&c, &p, &xd, "AUSDT").is_empty(), "n = {n}");
    }
    // Все close — NaN.
    let c = Contract {
        first_min: 7,
        close: vec![f64::NAN; 500],
        ..Default::default()
    };
    let xd = XdTable {
        x: vec![[0.0; 2]; 2],
    };
    assert!(find_episodes(&c, &p, &xd, "AUSDT").is_empty());
}

fn golden_series(n: usize) -> Vec<f64> {
    let mut st: u64 = 0x9E37_79B9_7F4A_7C15;
    let mut p: i64 = 0;
    (0..n)
        .map(|k| {
            st = st
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1_442_695_040_888_963_407);
            p += ((st >> 33) % 7) as i64 - 3;
            if (500..700).contains(&k) {
                p += 3;
            }
            if (1500..1650).contains(&k) {
                p -= 3;
            }
            let spike = if k == 2300 { 700 } else { 0 };
            (3000 + p + spike) as f64
        })
        .collect()
}

#[test]
fn golden_events_from_python_tk435() {
    // Ожидание — функции `moves`/`starts`/жадный отбор/E4 из `tools/compute/tk435-windows.py` на том же ряду
    // (генератор — `golden_series`), порог 8 % арифметического хода (ln(1,08) вверх, −ln(0,92) вниз; ближайший ход
    // к порогу в 7,7·10⁻⁵ — гарантия от погрешностей). Кортеж: (индекс D, знак, старт, пик, флаги); длина серии
    // концов с ходом ≥ X < 3 — только у пиков-выбросов одной свечи.
    let p = params("[90,180,360,720]", "0.99");
    let first = 777;
    let c = Contract {
        first_min: first,
        close: golden_series(3000),
        ..Default::default()
    };
    let xd = XdTable {
        x: vec![[0.0769610411361284, 0.08338160893905101]; 4],
    };
    let spike_up = FLAG_WICK | FLAG_OUTLIER;
    let want: [(u8, i8, i64, i64, u8); 15] = [
        (0, 1, 526, 616, 0),
        (0, 1, 2210, 2300, spike_up),
        (0, -1, 2300, 2390, FLAG_OUTLIER),
        (1, 1, 512, 692, 0),
        (1, 1, 2202, 2300, spike_up),
        (1, -1, 1483, 1650, 0),
        (1, -1, 2300, 2390, 0),
        (2, 1, 495, 805, 0),
        (2, 1, 1944, 2300, spike_up),
        (2, -1, 1290, 1650, 0),
        (2, -1, 2300, 2390, 0),
        (3, 1, 495, 1179, 0),
        (3, 1, 1650, 2300, spike_up),
        (3, -1, 1257, 1650, 0),
        (3, -1, 2300, 2998, 0),
    ];
    let got = find_episodes(&c, &p, &xd, "GOLDUSDT");
    let mut have: Vec<_> = got
        .iter()
        .map(|e| (e.di, e.sign, e.t0 - first, e.t_end - first, e.flags))
        .collect();
    have.sort();
    let mut want = want.to_vec();
    want.sort();
    assert_eq!(have, want);
    assert_eq!(got, find_episodes_naive(&c, &p, &xd, "GOLDUSDT"));
    // Ход — ln-разность close на концах (вверх: пик − старт, вниз: старт − пик).
    for e in &got {
        let (i, j) = ((e.t0 - first) as usize, (e.t_end - first) as usize);
        let want_mv = if e.sign > 0 {
            c.close[j].ln() - c.close[i].ln()
        } else {
            c.close[i].ln() - c.close[j].ln()
        };
        assert_eq!(e.mv.to_bits(), want_mv.to_bits(), "{e:?}");
    }
    // Порядок вывода — (t0, di, sign).
    assert!(got
        .windows(2)
        .all(|w| (w[0].t0, w[0].di, w[0].sign) < (w[1].t0, w[1].di, w[1].sign)));
}

fn bump_price(k: usize, end: f64) -> f64 {
    match k {
        0..=299 => 100.0 + (300 - k) as f64 * 0.001,
        300..=420 => 100.0 + (k - 300) as f64 * 0.25,
        421..=428 => 130.0 - (130.0 - end) * ((k - 420) as f64 / 8.0),
        _ => end,
    }
}

#[test]
fn wick_flag_boundary_is_half_of_move_within_15_min() {
    // Ход 100 → 130 к минуте 420; половина хода = 115. Возврат до 114 за ≤ 15 мин — фитиль, до 116 — нет.
    let p = params("[360]", "0.99");
    let xd = XdTable {
        x: vec![[0.18, f64::INFINITY]],
    };
    for (end, flags) in [(114.0, FLAG_WICK), (116.0, 0)] {
        let c = Contract {
            first_min: 40,
            close: (0..800).map(|k| bump_price(k, end)).collect(),
            ..Default::default()
        };
        let got = find_episodes(&c, &p, &xd, "BUMPUSDT");
        assert_eq!(got.len(), 1, "end {end}: {got:?}");
        let e = got[0];
        assert_eq!((e.di, e.sign, e.t0, e.t_end), (0, 1, 40 + 300, 40 + 420));
        assert_eq!(e.flags, flags, "end {end}");
        assert_eq!(got, find_episodes_naive(&c, &p, &xd, "BUMPUSDT"));
    }
}

#[test]
fn outlier_flag_boundary_is_run_shorter_than_three_candles() {
    // Пик держится 1, 2, 3, 4 свечи подряд: серия концов с ходом ≥ X короче 3 — выброс.
    let p = params("[360]", "0.99");
    let xd = XdTable {
        x: vec![[0.18, f64::INFINITY]],
    };
    for (width, outlier) in [(1usize, true), (2, true), (3, false), (4, false)] {
        let close: Vec<f64> = (0..800)
            .map(|k| {
                if (420..420 + width).contains(&k) {
                    130.0
                } else {
                    100.0 + (300.0 - k as f64).abs() * 0.001
                }
            })
            .collect();
        let c = Contract {
            first_min: 0,
            close,
            ..Default::default()
        };
        let got = find_episodes(&c, &p, &xd, "SPKUSDT");
        assert_eq!(got.len(), 1, "width {width}: {got:?}");
        let want_flags = FLAG_WICK | if outlier { FLAG_OUTLIER } else { 0 };
        assert_eq!(got[0].flags, want_flags, "width {width}");
        assert_eq!((got[0].t0, got[0].t_end), (300, 420));
        assert_eq!(got, find_episodes_naive(&c, &p, &xd, "SPKUSDT"));
    }
}

#[test]
fn quantile_matches_numpy_vectors() {
    // Значения — `numpy.quantile(x, q)` (numpy 2.2.6, method="linear"), биты результата.
    let a = [0.0, 0.0, 0.01, 0.02, 0.05, 0.2, 0.3, 0.31, 0.9, 1.5, 2.0];
    let b = [
        0.244253, 0.72225, 0.466721, 0.011422, 0.027045, 0.666605, 0.0, 0.55385, 0.506394, 0.10246,
        0.027827, 0.021584, 0.016556, 0.088166, 0.128442, 0.169569, 0.986562, 0.49804, 0.24085,
        0.967245, 0.009981, 0.004112, 0.229828,
    ];
    let c = [0.5];
    let d = [0.25, 0.75];
    let cases: [(&[f64], f64, u64); 15] = [
        (&a, 0.99, 0x3fff333333333334),
        (&a, 0.5, 0x3fc999999999999a),
        (&a, 0.995, 0x3fff999999999998),
        (&a, 0.25, 0x3f8eb851eb851eb8),
        (&a, 0.3333333333333333, 0x3f9eb851eb851eb6),
        (&b, 0.99, 0x3fef6f1a1ee51d7c),
        (&b, 0.5, 0x3fc5b46fdeb52c9d),
        (&b, 0.995, 0x3fef80824bca2de2),
        (&b, 0.01, 0x3f4da4ab9b79c391),
        (&b, 0.9, 0x3fe6c180d3cff64d),
        (&c, 0.99, 0x3fe0000000000000),
        (&d, 0.99, 0x3fe7d70a3d70a3d7),
        (&d, 0.5, 0x3fe0000000000000),
        (&d, 0.3, 0x3fd999999999999a),
        (&c, 0.5, 0x3fe0000000000000),
    ];
    for (x, q, bits) in cases {
        let mut s = x.to_vec();
        s.sort_by(f64::total_cmp);
        assert_eq!(quantile_linear(&s, q).to_bits(), bits, "q {q}");
    }
    assert!(quantile_linear(&[], 0.5).is_nan());
}

/// Два прохода `XdAcc` по `chunk` контрактов на накопитель (имитация потоков).
fn acc_two_pass(cs: &[Contract], p: &Params, fit: [i64; 2], chunk: usize) -> XdTable {
    let mut master = XdAcc::new(p);
    for part in cs.chunks(chunk) {
        let mut a = XdAcc::new(p);
        for c in part {
            a.add(c, p, fit);
        }
        master.merge(a).unwrap();
    }
    master.begin_pass2().unwrap();
    for part in cs.chunks(chunk) {
        let mut w = master.pass2_worker();
        for c in part {
            w.add(c, p, fit);
        }
        master.merge(w).unwrap();
    }
    master.finish().unwrap()
}

#[test]
fn xd_acc_equals_naive_bitwise() {
    let mut cs: Vec<Contract> = (0..5u64)
        .map(|s| {
            synth(
                100 + s,
                1200 + 300 * s as usize,
                10_000 + 777 * s as i64,
                s % 2 == 0,
            )
        })
        .collect();
    // Пила — много одинаковых ходов (ничьи внутри корзины и на границе рангов).
    cs.push(Contract {
        first_min: 10_100,
        close: (0..1500).map(|k| 100.0 + ((k * 7) % 5) as f64).collect(),
        ..Default::default()
    });
    cs.push(Contract::default());
    let refs: Vec<&Contract> = cs.iter().collect();
    for fit in [
        [0i64, 1_000_000],
        [10_500, 11_300],
        [11_000, 11_001],
        [11_000, 11_003],
        [12_000, 12_000],
        [20_000, 10_000],
    ] {
        let p0 = params("[90,180,360]", "0.99");
        let vals = xd_values_naive(&refs, &p0, fit);
        for q in [
            "0.99",
            "0.5",
            "0.995",
            "0.01",
            "0.75",
            "0.9",
            "0.3333333333333333",
        ] {
            let p = params("[90,180,360]", q);
            let want = xd_from_values(&vals, p.episodes.quantile_x_d);
            for chunk in [1usize, 2, 7] {
                let got = acc_two_pass(&cs, &p, fit, chunk);
                assert!(
                    same_table(&got, &want),
                    "q {q} fit {fit:?} chunk {chunk}: {got:?} против {want:?}"
                );
            }
        }
    }
}

#[test]
fn xd_acc_counts_and_misuse() {
    let p = params("[90,180]", "0.99");
    let c = synth(77, 1000, 5_000, false);
    let fit = [5_000i64, 5_600];
    let mut acc = XdAcc::new(&p);
    acc.add(&c, &p, fit);
    // Концы j ∈ [90, 600) (окно старта непусто, close есть) — по D = 90 и 180 одинаково, оба знака.
    assert_eq!(acc.counts(), vec![[510, 510], [510, 510]]);
    acc.begin_pass2().unwrap();
    assert_eq!(acc.counts(), vec![[510, 510], [510, 510]]);
    // Проход 2 без значений: счётчики не сошлись — ошибка, а не молчаливый ответ.
    assert!(acc.finish().is_err());
    // finish без прохода 2.
    assert!(XdAcc::new(&p).finish().is_err());
    // Пустой накопитель: NaN-порог.
    let mut e = XdAcc::new(&p);
    e.begin_pass2().unwrap();
    let t = e.finish().unwrap();
    assert!(t.x.iter().all(|r| r[0].is_nan() && r[1].is_nan()));
}

/// Ошибки порядка проходов — `Err` (раньше `panic!`, E-линт `clippy::panic`), а не падение и не молчаливый ответ.
#[test]
fn xd_acc_phase_misuse_is_err() {
    let p = params("[90,180]", "0.99");
    let mut master = XdAcc::new(&p);
    // Работник до begin_pass2 — накопитель прохода 1: в мастер прохода 1 сливается, в мастер прохода 2 — нет.
    let early = master.pass2_worker();
    master.begin_pass2().unwrap();
    assert!(master.merge(early).is_err());
    // begin_pass2 дважды.
    assert!(master.begin_pass2().is_err());
    // Мастер прохода 1 не принимает накопитель прохода 2.
    let mut m1 = XdAcc::new(&p);
    assert!(m1.merge(master.pass2_worker()).is_err());
    // Разное число D.
    let q = params("[90,180,360]", "0.99");
    assert!(XdAcc::new(&p).merge(XdAcc::new(&q)).is_err());
}

fn ep(di: u8, sign: i8, t0: i64, t_end: i64) -> Episode {
    Episode {
        di,
        sign,
        t0,
        t_end,
        mv: 0.1,
        flags: 0,
    }
}

#[test]
fn market_stratum_overlap_rules() {
    let btc = [
        ep(0, 1, 100, 200),
        ep(1, 1, 1000, 1180),
        ep(0, -1, 500, 600),
        ep(0, 1, 300, 340),
    ];
    let mut eps = [
        ep(0, 1, 150, 250),   // перекрытие 50 из 100 = 0,5: граница включена
        ep(0, 1, 160, 260),   // 40 из 100
        ep(1, 1, 1000, 1180), // целиком
        ep(0, -1, 550, 700),  // 50 из 150
        ep(1, -1, 500, 600),  // другой знак и D
        ep(0, 1, 90, 190),    // 90 из 100
        ep(0, 1, 250, 350),   // BTC-эпизод внутри: 40 из 100
    ];
    eps[0].flags = FLAG_WICK;
    mark_market(&mut eps, &btc, 0.5);
    let m: Vec<bool> = eps.iter().map(|e| e.flags & FLAG_MARKET != 0).collect();
    assert_eq!(m, [true, false, true, false, false, true, false]);
    assert_eq!(
        eps[0].flags,
        FLAG_WICK | FLAG_MARKET,
        "прежние флаги сохранены"
    );
    // Без BTC-эпизодов ничего не помечается.
    let mut none = [ep(0, 1, 100, 200)];
    mark_market(&mut none, &[], 0.5);
    assert_eq!(none[0].flags, 0);
}

#[test]
fn market_stratum_fast_equals_naive_random() {
    let mut r = Rng(0xDEAD_BEEF_1234_5678);
    let mut make = |n: usize| -> Vec<Episode> {
        (0..n)
            .map(|_| {
                let t0 = r.below(5000) as i64;
                ep(
                    r.below(3) as u8,
                    if r.below(2) == 0 { 1 } else { -1 },
                    t0,
                    t0 + 90 + r.below(700) as i64,
                )
            })
            .collect()
    };
    for (ne, nb) in [(0, 5), (50, 0), (300, 40), (300, 400)] {
        let (a, b) = (make(ne), make(nb));
        for share in [0.25, 0.5, 1.0] {
            let (mut x, mut y) = (a.clone(), a.clone());
            mark_market(&mut x, &b, share);
            mark_market_naive(&mut y, &b, share);
            assert_eq!(x, y, "ne {ne} nb {nb} share {share}");
        }
    }
}
