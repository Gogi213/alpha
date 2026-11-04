//! Тесты куба признаков (TK-492): сверка с эталоном `feat_ref`, ручные примеры, граничные случаи,
//! отсутствие заглядывания вперёд, правила `fires` / `first_triggers`.

use super::cache::{Contract, SLOT_MIN};
use super::feat::{
    build_cube, fires, first_triggers, med_mad, rolling_med_mad, rules, window_sums, zval,
    FAMILIES, FAMILY_NAMES,
};
use super::feat_ref::build_cube_naive;
use super::params::Params;
use super::types::{
    Cube, Rule, F_FUND, F_GLOBAL, F_OI_PCT, F_TAKER, F_TOP_COUNT, F_TOP_SUM, F_TRADES, F_TURN,
    F_VR, NF,
};

/// Параметры протокола (реальный `P-34-params.json`) с малыми историями, чтобы эталон считался быстро:
/// норма 1 сутки, фандинг и σ — 2 суток; окна и пороги зафиксированы явно.
fn params(l_pre_min: u32) -> Params {
    let mut v: serde_json::Value =
        serde_json::from_str(include_str!("../../docs/research/P-34-params.json")).unwrap();
    v["features"]["windows_min"] = serde_json::json!([5, 15, 30, 60]);
    v["features"]["theta"] = serde_json::json!([2, 3, 4]);
    v["features"]["norm"]["history_days"] = serde_json::json!(1);
    v["features"]["funding_z_history_days"] = serde_json::json!(2);
    v["controls"]["sigma_1m_window_days"] = serde_json::json!(2);
    v["onset"]["L_pre_min"] = serde_json::json!(l_pre_min);
    Params::from_json(&v.to_string()).unwrap()
}

/// Детерминированный xorshift64.
struct Rng(u64);
impl Rng {
    fn step(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }
    /// Равномерно на [0, 1).
    fn u(&mut self) -> f64 {
        (self.step() >> 11) as f64 / (1u64 << 53) as f64
    }
}

/// Синтетический контракт: случайный ход с режимами волатильности, дыры свечей (3 % и длинная), плоский
/// участок (нулевые доходности), квантованные колонки OI (ничьи), колонка почти без изменений (MAD = 0),
/// дыры OI (3 %, прогон 30 слотов, хвост без OI), OI начинается раньше свечей, часовой фандинг.
fn synth(seed: u64, first_min: i64, minutes: usize) -> Contract {
    let mut g = Rng(seed);
    let mut c = Contract {
        first_min,
        ..Default::default()
    };
    let (mut lnp, mut sig) = (2.0f64, 1e-3);
    for m in 0..minutes {
        if m.is_multiple_of(700) {
            sig = 3e-4 * (1.0 + 8.0 * g.u());
        }
        if !(2000..2400).contains(&m) {
            lnp += sig * (g.u() - 0.5) * 2.0;
        }
        if (1000..1100).contains(&m) || g.u() < 0.03 {
            c.close.push(f64::NAN);
            c.vol.push(0.0);
            c.qvol.push(0.0);
            c.trades.push(0.0);
        } else {
            let px = lnp.exp();
            let q = 5e3 * (1.0 + 20.0 * g.u() * g.u());
            c.close.push(px);
            c.vol.push((q / px) as f32);
            c.qvol.push(q as f32);
            c.trades.push((q / 100.0).round() as f32);
        }
    }
    let slot0 = (first_min + SLOT_MIN - 1).div_euclid(SLOT_MIN);
    c.oi_first_slot = slot0 - 40;
    let mut oi = 1e6f64;
    for s in 0..minutes / 5 + 40 - 60 {
        oi *= 1.0 + 0.02 * (g.u() - 0.5);
        let gap = (200..230).contains(&s) || g.u() < 0.03;
        let vals = [
            oi,
            oi * 3.0 * (1.0 + 0.2 * g.u()),
            ((0.8 + 0.4 * g.u()) * 100.0).round() / 100.0,
            ((1.0 + 0.5 * (g.u() - 0.5)) * 50.0).round() / 50.0,
            if g.u() < 0.05 { 1.1 } else { 1.0 },
            ((0.5 + 1.5 * g.u() * g.u()) * 1000.0).round() / 1000.0,
        ];
        for (k, v) in vals.iter().enumerate() {
            let hole = gap || (k == 2 && g.u() < 0.02);
            c.oi[k].push(if hole { f64::NAN } else { *v });
        }
    }
    let mut m = (slot0 + 150) * SLOT_MIN;
    while m < first_min + minutes as i64 {
        let rate = if g.u() < 0.4 {
            1e-4
        } else {
            1e-4 + 6e-4 * (g.u() - 0.5)
        };
        c.funding.push((m, rate));
        m += 60;
    }
    c
}

/// Равенство с допуском `tol` (относительно max(1, |a|, |b|)); `NaN` равен `NaN`.
fn same(a: f32, b: f32, tol: f32) -> bool {
    (a.is_nan() && b.is_nan())
        || a == b
        || (a.is_finite() && b.is_finite() && (a - b).abs() <= tol * a.abs().max(b.abs()).max(1.0))
}

fn all_same(a: &[f32], b: &[f32]) -> bool {
    a.len() == b.len() && a.iter().zip(b).all(|(&x, &y)| same(x, y, 0.0))
}

fn eq64(a: f64, b: f64) -> bool {
    (a.is_nan() && b.is_nan()) || a == b
}

/// Куб `a` совпал с кубом `b`: размеры, `r` и z (кроме F9) побитно, F9 и σ — до округления f32 (суммирование
/// σ в быстром пути и в эталоне идёт в разном порядке, различие ~1e-15 до приведения к f32).
fn assert_cubes(a: &Cube, b: &Cube) {
    assert_eq!((a.slot0, a.n, &a.windows), (b.slot0, b.n, &b.windows));
    assert_eq!(
        (a.z.len(), a.r.len(), a.sigma.len()),
        (b.z.len(), b.r.len(), b.sigma.len())
    );
    let nw = a.windows.len();
    for f in 0..NF {
        let tol = if f == F_VR { 1e-6 } else { 0.0 };
        for wi in 0..nw {
            for i in 0..a.n {
                let (x, y) = (a.zat(f, wi, i), b.zat(f, wi, i));
                assert!(same(x, y, tol), "z f={f} wi={wi} i={i}: {x} vs {y}");
            }
        }
    }
    for wi in 0..nw {
        for i in 0..a.n {
            let (x, y) = (a.rat(wi, i), b.rat(wi, i));
            assert!(same(x, y, 0.0), "r wi={wi} i={i}: {x} vs {y}");
        }
    }
    for (i, (&x, &y)) in a.sigma.iter().zip(&b.sigma).enumerate() {
        assert!(same(x, y, 1e-6), "sigma i={i}: {x} vs {y}");
    }
}

/// Куб `a` (усечённый контракт) равен началу куба `b` побитно — по всем признакам.
fn assert_prefix(a: &Cube, b: &Cube) {
    assert_eq!((a.slot0, &a.windows), (b.slot0, &b.windows));
    assert!(a.n <= b.n);
    let nw = a.windows.len();
    let mut finite = 0;
    for f in 0..NF {
        for wi in 0..nw {
            for i in 0..a.n {
                let (x, y) = (a.zat(f, wi, i), b.zat(f, wi, i));
                assert!(same(x, y, 0.0), "z f={f} wi={wi} i={i}: {x} vs {y}");
                finite += usize::from(x.is_finite());
            }
        }
    }
    for wi in 0..nw {
        for i in 0..a.n {
            assert!(same(a.rat(wi, i), b.rat(wi, i), 0.0), "r wi={wi} i={i}");
        }
    }
    for (i, (&x, &y)) in a.sigma.iter().zip(&b.sigma).enumerate() {
        assert!(same(x, y, 0.0), "sigma i={i}: {x} vs {y}");
    }
    assert!(finite > 1000, "тест не должен быть пустым: {finite}");
}

fn finite_count(c: &Cube, f: usize) -> usize {
    (0..c.windows.len())
        .map(|wi| (0..c.n).filter(|&i| c.zat(f, wi, i).is_finite()).count())
        .sum()
}

#[test]
fn cube_equals_naive_lag0() {
    let p = params(360);
    let c = synth(7, 1_000_003, 4600);
    let fast = build_cube(&c, &p, 0);
    assert_cubes(&fast, &build_cube_naive(&c, &p, 0));
    for f in 0..NF {
        assert!(finite_count(&fast, f) > 50, "признак {f} пуст");
    }
    // колонка с MAD = 0 даёт бесконечные z, а не NaN
    let infs = (0..fast.windows.len())
        .flat_map(|wi| (0..fast.n).map(move |i| (wi, i)))
        .filter(|&(wi, i)| fast.zat(F_GLOBAL, wi, i).is_infinite())
        .count();
    assert!(infs > 0);
}

#[test]
fn cube_equals_naive_short_series() {
    // 700 минут = 140 слотов: истории для z нет, но r, σ-NaN, размеры и раскладка совпадают с эталоном
    let p = params(360);
    let c = synth(3, 12_345, 700);
    let fast = build_cube(&c, &p, 0);
    assert_cubes(&fast, &build_cube_naive(&c, &p, 0));
    assert!(fast.z.iter().all(|v| v.is_nan()));
    assert!(fast.r.iter().any(|v| v.is_finite()));
}

#[test]
fn cube_equals_naive_lag1_aligned_start() {
    let p = params(360);
    let c = synth(11, 500_000, 4300);
    assert_cubes(&build_cube(&c, &p, 1), &build_cube_naive(&c, &p, 1));
}

#[test]
fn oi_lag_is_a_shift_of_the_oi_slot() {
    // метка OI «с лагом 1» на контракте = метка без лага на контракте, где OI сдвинут на слот вперёд
    let p = params(360);
    let c = synth(5, 77_777, 3800);
    let mut shifted = c.clone();
    shifted.oi_first_slot += 1;
    let (a, b) = (build_cube(&c, &p, 1), build_cube(&shifted, &p, 0));
    assert_cubes(&a, &b);
    // и лаг действительно что-то меняет
    let z0 = build_cube(&c, &p, 0);
    assert!(!all_same(&a.z, &z0.z) && all_same(&a.r, &z0.r) && all_same(&a.sigma, &z0.sigma));
}

#[test]
fn unsorted_funding_is_sorted_inside() {
    let p = params(360);
    let c = synth(13, 9_001, 3600);
    let mut d = c.clone();
    d.funding.reverse();
    assert_cubes(&build_cube(&c, &p, 0), &build_cube(&d, &p, 0));
}

fn cut(c: &Contract, last_min: i64) -> Contract {
    let keep = (last_min - c.first_min + 1) as usize;
    let oi_keep = (last_min.div_euclid(SLOT_MIN) - c.oi_first_slot + 1).max(0) as usize;
    let mut d = c.clone();
    d.close.truncate(keep);
    d.vol.truncate(keep);
    d.qvol.truncate(keep);
    d.trades.truncate(keep);
    for col in &mut d.oi {
        col.truncate(oi_keep);
    }
    d.funding.retain(|&(m, _)| m <= last_min);
    d
}

/// Контракт, у которого всё после `last_min` заменено мусором.
fn corrupt_future(c: &Contract, last_min: i64) -> Contract {
    let keep = (last_min - c.first_min + 1) as usize;
    let oi_keep = (last_min.div_euclid(SLOT_MIN) - c.oi_first_slot + 1).max(0) as usize;
    let mut d = c.clone();
    for j in keep..d.close.len() {
        d.close[j] = 1e3 + (j % 7) as f64;
        d.vol[j] = 9.0;
        d.qvol[j] = 1e7;
        d.trades[j] = 1e5;
    }
    for col in &mut d.oi {
        for v in col.iter_mut().skip(oi_keep) {
            *v = 1e9;
        }
    }
    for e in &mut d.funding {
        if e.0 > last_min {
            e.1 = 0.5;
        }
    }
    d
}

#[test]
fn no_lookahead_truncated_and_corrupted_future() {
    let p = params(360);
    let c = synth(21, 400_001, 4600);
    let last = c.first_min + 3700;
    for lag in [0, 1] {
        let full = build_cube(&c, &p, lag);
        let part = build_cube(&cut(&c, last), &p, lag);
        let bad = build_cube(&corrupt_future(&c, last), &p, lag);
        assert!(part.n < full.n);
        assert_prefix(&part, &full);
        assert_prefix(&part, &bad);
    }
}

#[test]
fn edge_empty_one_slot_and_short_history() {
    let p = params(30);
    let flat = |first_min: i64, close: Vec<f64>| {
        let n = close.len();
        Contract {
            first_min,
            close,
            vol: vec![1.0; n],
            qvol: vec![1.0; n],
            trades: vec![1.0; n],
            ..Default::default()
        }
    };
    let check = |c: &Contract| {
        let (fast, naive) = (build_cube(c, &p, 0), build_cube_naive(c, &p, 0));
        assert_cubes(&fast, &naive);
        fast
    };
    // пустой контракт
    let e = check(&Contract::default());
    assert_eq!((e.n, e.z.len(), e.r.len(), e.sigma.len()), (0, 0, 0, 0));
    assert_eq!(e.windows, vec![5, 15, 30, 60]);
    // две минуты — ни одной минуты сетки 5·s
    assert_eq!(check(&flat(3, vec![1.0, 2.0])).n, 0);
    // три минуты 3, 4, 5 — один слот (s = 1, t = 5): окон нет, всё NaN
    let one = check(&flat(3, vec![1.0, 2.0, 3.0]));
    assert_eq!((one.n, one.slot0), (1, 1));
    assert!(one
        .z
        .iter()
        .chain(&one.r)
        .chain(&one.sigma)
        .all(|v| v.is_nan()));
    // r = ln(c[t]/c[t−w]); окно 5 в слоте t = 5 есть, окно 15 — нет
    let two = check(&flat(0, (1..=6u32).map(f64::from).collect()));
    assert_eq!((two.n, two.slot0), (2, 0));
    assert!(two.rat(0, 0).is_nan() && two.rat(1, 1).is_nan());
    assert_eq!(two.rat(0, 1), 6.0f64.ln() as f32);
    // нет первого close: доходность по NaN не определена, дальше close тянется вперёд
    let nan0 = check(&flat(
        0,
        vec![f64::NAN, 1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 2.0, 2.0, 4.0],
    ));
    assert_eq!(nan0.n, 3);
    assert!(nan0.rat(0, 1).is_nan());
    assert_eq!(nan0.rat(0, 2), 2.0f64.ln() as f32);
    // окно 60 длиннее истории (50 минут): r и z пусты, окно 30 — уже есть с t = 30
    let short = check(&flat(0, (1..=50u32).map(f64::from).collect()));
    assert_eq!(short.n, 10);
    assert!((0..10).all(|i| short.rat(3, i).is_nan()));
    assert!(short.rat(2, 5).is_nan() && short.rat(2, 6).is_finite());
    assert!(short.z.iter().all(|v| v.is_nan()));
}

#[test]
#[should_panic(expected = "grid_min")]
fn window_not_multiple_of_slot_is_rejected() {
    let mut p = params(30);
    p.features.windows_min = vec![7];
    build_cube(&synth(1, 0, 200), &p, 0);
}

/// Контракт без OI и фандинга: постоянная цена, `qvol` и `trades` заданы функциями индекса минуты.
fn flat_flows(
    minutes: usize,
    trades: impl Fn(usize) -> f32,
    qvol: impl Fn(usize) -> f32,
) -> Contract {
    Contract {
        first_min: 0,
        close: vec![1.0; minutes],
        vol: vec![1.0; minutes],
        qvol: (0..minutes).map(qvol).collect(),
        trades: (0..minutes).map(trades).collect(),
        ..Default::default()
    }
}

/// Номер слота (блока), которому принадлежит минута: минуты (5(s−1), 5s].
fn block_of(m: usize) -> usize {
    m.div_ceil(5)
}

#[test]
fn hand_ramp_checks_the_norm_window_end() {
    // trades минуты = номер блока → x(s, w) — арифметическая прогрессия с шагом a = 5k; окно нормы из 288 слотов,
    // кончающееся в s − k: z = a(k + 143,5)/(f·72a) = (k + 143,5)/(72f), не зависит от слота и от a
    let p = params(30);
    let c = flat_flows(2000, |m| block_of(m) as f32, |_| 1.0);
    let cube = build_cube(&c, &p, 0);
    assert_eq!(cube.n, 400);
    let f = p.features.norm.mad_factor;
    for (wi, k) in [(0usize, 1.0f64), (1, 3.0), (2, 6.0), (3, 12.0)] {
        let want = ((k + 143.5) / (72.0 * f)) as f32;
        for i in [350, 399] {
            let got = cube.zat(F_TRADES, wi, i);
            assert!(
                (got - want).abs() <= 1e-6 * want.abs(),
                "wi={wi} i={i}: {got} vs {want}"
            );
        }
    }
    assert_cubes(&cube, &build_cube_naive(&c, &p, 0));
}

#[test]
fn hand_periodic_ties_and_infinite_z_on_zero_mad() {
    // trades блока = 10·(1 + s mod 6): окно из 288 слотов = 48 полных периодов, медиана 35, MAD 15
    let p = params(30);
    let mut c = flat_flows(
        2000,
        |m| 2.0 * (1 + block_of(m) % 6) as f32,
        |m| if (1991..=1995).contains(&m) { 7.0 } else { 1.0 },
    );
    c.funding = vec![(0, 1e-4)];
    let cube = build_cube(&c, &p, 0);
    let f = p.features.norm.mad_factor;
    for (i, x) in [(305, 60.0), (300, 10.0), (302, 30.0)] {
        assert_eq!(
            cube.zat(F_TRADES, 0, i),
            ((x - 35.0) / (f * 15.0)) as f32,
            "i={i}"
        );
    }
    // оборот постоянен (MAD = 0): z = 0, а на всплеске в последнем блоке — +∞, и правило срабатывает
    assert_eq!(cube.zat(F_TURN, 0, 398), 0.0);
    assert_eq!(cube.zat(F_TURN, 0, 399), f32::INFINITY);
    assert_eq!(cube.zat(F_TURN, 1, 399), f32::INFINITY);
    let spike = Rule {
        fam: 1,
        wi: 0,
        ti: 2,
    };
    assert!(fires(&cube, spike, &p, 399));
    assert!(!fires(&cube, spike, &p, 398));
    // фандинг один раз начислен в минуту 0: ставка постоянна, но история меньше 2 суток — NaN
    assert!((0..cube.n).all(|i| cube.zat(F_FUND, 0, i).is_nan()));
    assert_cubes(&cube, &build_cube_naive(&c, &p, 0));
}

#[test]
fn sigma_is_population_std_of_minute_returns() {
    // доходности ±a поочерёдно: ст. откл. по 1/N = a при чётном числе минут окна
    let p = params(30);
    let a = 1e-3f64;
    let minutes = 3000;
    let c = Contract {
        first_min: 0,
        close: (0..minutes)
            .map(|m| if m % 2 == 1 { a.exp() } else { 1.0 })
            .collect(),
        vol: vec![1.0; minutes],
        qvol: vec![1.0; minutes],
        trades: vec![1.0; minutes],
        ..Default::default()
    };
    let cube = build_cube(&c, &p, 0);
    assert_eq!(cube.n, 600);
    // окно 2 суток = 2880 минут: первый полный слот t = 2880 (слот 576), раньше — NaN
    assert!(cube.sigma[..576].iter().all(|s| s.is_nan()));
    for &s in &cube.sigma[576..] {
        assert!(((f64::from(s) - a) / a).abs() < 1e-6, "{s}");
    }
    assert_cubes(&cube, &build_cube_naive(&c, &p, 0));
}

#[test]
fn zval_zero_mad_follows_ieee() {
    assert_eq!(zval(3.0, 1.0, 0.5, 2.0), 2.0);
    assert_eq!(zval(1.0, 1.0, 0.0, 1.4826), 0.0);
    assert_eq!(zval(2.0, 1.0, 0.0, 1.4826), f64::INFINITY);
    assert_eq!(zval(-2.0, 1.0, 0.0, 1.4826), f64::NEG_INFINITY);
    assert!(zval(f64::NAN, 1.0, 1.0, 1.4826).is_nan());
    assert!(zval(1.0, f64::NAN, 1.0, 1.4826).is_nan());
    assert!(zval(1.0, 1.0, f64::NAN, 1.4826).is_nan());
}

fn mid(v: &[f64]) -> f64 {
    let m = v.len();
    if m % 2 == 1 {
        v[m / 2]
    } else {
        (v[m / 2 - 1] + v[m / 2]) / 2.0
    }
}

/// Медиана и MAD сортировкой по определению.
fn direct_med_mad(v: &[f64]) -> (f64, f64) {
    let mut s = v.to_vec();
    s.sort_by(f64::total_cmp);
    let med = mid(&s);
    let mut d: Vec<f64> = s.iter().map(|x| (x - med).abs()).collect();
    d.sort_by(f64::total_cmp);
    (med, mid(&d))
}

#[test]
fn med_mad_matches_sorting() {
    let mut g = Rng(99);
    for m in 1..=40 {
        for round in 0..30 {
            let ties = round % 2 == 0;
            let mut v: Vec<f64> = (0..m)
                .map(|_| {
                    if ties {
                        (g.u() * 5.0).floor() / 2.0
                    } else {
                        g.u()
                    }
                })
                .collect();
            v.sort_by(f64::total_cmp);
            assert_eq!(med_mad(&v), direct_med_mad(&v), "m={m} {v:?}");
        }
    }
}

#[test]
fn rolling_med_mad_matches_sorting_with_holes_and_ties() {
    let mut g = Rng(5);
    for (len, ties) in [
        (1usize, true),
        (2, false),
        (3, true),
        (7, true),
        (16, false),
        (31, true),
        (400, true),
    ] {
        let x: Vec<f64> = (0..300)
            .map(|_| {
                let u = g.u();
                if u < 0.2 {
                    f64::NAN
                } else if ties {
                    (u * 12.0).floor()
                } else {
                    u
                }
            })
            .collect();
        let (mut med, mut mad) = (vec![0.0; x.len()], vec![0.0; x.len()]);
        rolling_med_mad(&x, len, &mut med, &mut mad);
        for (j, (&m1, &d1)) in med.iter().zip(&mad).enumerate() {
            let v0 = x.iter().position(|v| !v.is_nan());
            let win: Vec<f64> = if v0.is_none_or(|v| j + 1 < v + len) {
                Vec::new()
            } else {
                x[j + 1 - len..=j]
                    .iter()
                    .copied()
                    .filter(|v| !v.is_nan())
                    .collect()
            };
            let (m2, d2) = if win.is_empty() {
                (f64::NAN, f64::NAN)
            } else {
                direct_med_mad(&win)
            };
            assert!(eq64(m1, m2) && eq64(d1, d2), "len={len} j={j}");
        }
    }
}

#[test]
fn window_sums_match_direct_sums() {
    let mut g = Rng(3);
    let b: Vec<f64> = (0..97).map(|_| g.u()).collect();
    for w in [1usize, 2, 5, 13, 48, 96, 97] {
        let s = window_sums(&b, w);
        for (j, &sj) in s.iter().enumerate() {
            if j + 1 < w {
                assert!(sj.is_nan(), "w={w} j={j}");
            } else {
                let d: f64 = b[j + 1 - w..=j].iter().sum();
                assert!((sj - d).abs() < 1e-12, "w={w} j={j}: {sj} vs {d}");
            }
        }
    }
    assert!(window_sums(&b, 98).iter().all(|v| v.is_nan()));
    assert!(window_sums(&b, 0).iter().all(|v| v.is_nan()));
}

#[test]
fn rules_order_and_count() {
    let p = params(30);
    let rs = rules(&p);
    assert_eq!(rs.len(), 11 * 4 * 3);
    let r = |fam, wi, ti| Rule { fam, wi, ti };
    assert_eq!(
        (rs[0], rs[1], rs[2], rs[3]),
        (r(0, 0, 0), r(0, 0, 1), r(0, 0, 2), r(0, 1, 0))
    );
    assert_eq!((rs[12], rs[131]), (r(1, 0, 0), r(10, 3, 2)));
    assert!(rs.windows(2).all(|w| w[0] < w[1]), "порядок fam, wi, ti");
}

#[test]
fn families_follow_protocol() {
    let singles: Vec<usize> = FAMILIES[..9]
        .iter()
        .map(|f| {
            assert_eq!(f.len(), 1);
            f[0]
        })
        .collect();
    // F1, F3, F4, F5, F6, F7, F8, F9, F10 — без F2
    assert_eq!(
        singles,
        [
            F_OI_PCT,
            F_TURN,
            F_TRADES,
            F_TAKER,
            F_TOP_COUNT,
            F_TOP_SUM,
            F_GLOBAL,
            F_VR,
            F_FUND
        ]
    );
    assert_eq!(FAMILIES[9], [F_OI_PCT, F_TURN]);
    assert_eq!(FAMILIES[10], [F_OI_PCT, F_TURN, F_TRADES]);
    assert_eq!(FAMILY_NAMES.len(), FAMILIES.len());
}

/// Куб из `NaN` (4 окна) для ручных примеров.
fn hand_cube(n: usize) -> Cube {
    Cube {
        slot0: 100,
        n,
        windows: vec![5, 15, 30, 60],
        z: vec![f32::NAN; NF * 4 * n],
        r: vec![0.0; 4 * n],
        sigma: vec![1.0; n],
    }
}

fn setz(c: &mut Cube, f: usize, wi: usize, i: usize, v: f32) {
    let o = (f * c.windows.len() + wi) * c.n + i;
    c.z[o] = v;
}

#[test]
fn fires_signs_thresholds_composites_and_nan() {
    let p = params(30);
    let r = |fam, wi, ti| Rule { fam, wi, ti };
    let mut c = hand_cube(9);
    // вверх: F1 (fam 0) срабатывает только на положительном z
    setz(&mut c, F_OI_PCT, 0, 0, 2.5);
    setz(&mut c, F_OI_PCT, 0, 1, -5.0);
    assert!(fires(&c, r(0, 0, 0), &p, 0) && !fires(&c, r(0, 0, 1), &p, 0));
    assert!(!fires(&c, r(0, 0, 0), &p, 1), "минус F1 — не сигнал");
    assert!(!fires(&c, r(0, 1, 0), &p, 0), "другое окно");
    // в обе стороны: F5 (fam 3) по |z|
    setz(&mut c, F_TAKER, 0, 2, -3.5);
    setz(&mut c, F_TAKER, 0, 3, 3.5);
    for i in [2, 3] {
        assert!(fires(&c, r(3, 0, 0), &p, i) && fires(&c, r(3, 0, 1), &p, i));
        assert!(!fires(&c, r(3, 0, 2), &p, i));
    }
    // S_oi (fam 9): F1 & F3 в одном окне, граница z = θ включительно
    setz(&mut c, F_OI_PCT, 0, 4, 3.0);
    setz(&mut c, F_TURN, 0, 4, 1.9);
    setz(&mut c, F_OI_PCT, 0, 5, 3.0);
    setz(&mut c, F_TURN, 0, 5, 2.0);
    assert!(!fires(&c, r(9, 0, 0), &p, 4));
    assert!(fires(&c, r(9, 0, 0), &p, 5) && !fires(&c, r(9, 0, 1), &p, 5));
    // S_all (fam 10): F1 & F3 & F4
    for (f, v) in [(F_OI_PCT, 5.0), (F_TURN, 5.0), (F_TRADES, 3.9)] {
        setz(&mut c, f, 0, 6, v);
    }
    assert!(fires(&c, r(10, 0, 0), &p, 6) && fires(&c, r(10, 0, 1), &p, 6));
    assert!(!fires(&c, r(10, 0, 2), &p, 6));
    // слот целиком NaN: ни одно правило не срабатывает; слот вне куба — тоже
    assert!(rules(&p).iter().all(|&q| !fires(&c, q, &p, 7)));
    assert!(rules(&p).iter().all(|&q| !fires(&c, q, &p, 9)));
    // F10 (fam 8) в обе стороны, F9 (fam 7) только вверх
    setz(&mut c, F_FUND, 2, 8, -4.5);
    setz(&mut c, F_VR, 2, 8, -9.0);
    assert!(fires(&c, r(8, 2, 2), &p, 8));
    assert!(!fires(&c, r(7, 2, 0), &p, 8));
}

#[test]
fn first_triggers_quiet_period_and_nan_gaps() {
    // L_pre = 30 мин = 6 слотов: первое срабатывание, если в [i − 6, i) срабатываний нет
    let p = params(30);
    let rule = Rule {
        fam: 0,
        wi: 0,
        ti: 0,
    };
    let mut c = hand_cube(40);
    for i in 0..40 {
        setz(&mut c, F_OI_PCT, 0, i, 0.5);
    }
    for i in [1, 2, 8, 9, 10, 16, 23, 30, 38] {
        setz(&mut c, F_OI_PCT, 0, i, 2.5);
    }
    for i in 31..38 {
        setz(&mut c, F_OI_PCT, 0, i, f32::NAN);
    }
    // 2 → 8 — пауза ровно 6 слотов, окно [2, 8) содержит срабатывание 2: не первое; 23 и 30 — первые;
    // 38 после семи слотов NaN — первое
    assert_eq!(first_triggers(&c, rule, &p), vec![1, 23, 30, 38]);
    // составное: F1 и F3 вместе в слотах 5 и 6; F1 один в 12 — не срабатывание
    let mut d = hand_cube(20);
    for i in [5, 6] {
        setz(&mut d, F_OI_PCT, 0, i, 4.0);
        setz(&mut d, F_TURN, 0, i, 4.0);
    }
    setz(&mut d, F_OI_PCT, 0, 12, 4.0);
    assert_eq!(
        first_triggers(
            &d,
            Rule {
                fam: 9,
                wi: 0,
                ti: 2
            },
            &p
        ),
        vec![5]
    );
    assert_eq!(first_triggers(&hand_cube(0), rule, &p), Vec::<usize>::new());
}

#[test]
fn first_triggers_match_brute_force_on_synthetic_cube() {
    let p = params(360);
    let cube = build_cube(&synth(33, 250_003, 4600), &p, 0);
    let quiet = (p.onset.l_pre_min / 5) as usize;
    let (mut total, mut gated) = (0, 0);
    for rule in rules(&p) {
        let on: Vec<bool> = (0..cube.n).map(|i| fires(&cube, rule, &p, i)).collect();
        let want: Vec<usize> = (0..cube.n)
            .filter(|&i| on[i] && !on[i.saturating_sub(quiet)..i].iter().any(|&b| b))
            .collect();
        let got = first_triggers(&cube, rule, &p);
        assert_eq!(got, want, "{rule:?}");
        total += got.len();
        gated += on.iter().filter(|&&b| b).count() - got.len();
    }
    assert!(total > 0 && gated > 0, "total={total} gated={gated}");
}

/// Замер скорости боевого размера (В-170): параметры протокола как есть (норма 7 сут, фандинг 30 сут, σ 7 сут),
/// 120 суток минут. Запуск на VPS: `cargo test --release feat_speed -- --ignored --nocapture`.
#[test]
#[ignore = "замер скорости, не проверка"]
fn feat_speed_real_params() {
    let p = Params::from_json(include_str!("../../docs/research/P-34-params.json")).unwrap();
    let c = synth(1, 1_000_000, 120 * 1440);
    let t0 = std::time::Instant::now();
    let cube = build_cube(&c, &p, 0);
    let dt = t0.elapsed();
    let series = NF * cube.windows.len();
    println!(
        "feat_speed: {} слотов, {} рядов z, {:.0} мс, {:.0} нс на слот-ряд",
        cube.n,
        series,
        dt.as_secs_f64() * 1e3,
        dt.as_secs_f64() * 1e9 / (cube.n * series) as f64
    );
    for f in 0..NF {
        assert!(finite_count(&cube, f) > 0, "признак {f} пуст");
    }
}
