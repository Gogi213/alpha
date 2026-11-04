//! Тесты `control.rs` (TK-492): быстрое == наивное (`control_ref.rs`) на синтетике, детерминизм по зерну,
//! независимость от порядка контрактов/правил/потоков, граничные случаи. Признаки/эпизоды не нужны:
//! куб и эпизоды собраны вручную, срабатывания заданы функцией от (зерно, правило, слот).

use super::control::{
    c2a_with, c2b_with, check_label_d, fnv1a, hit, label, lags_with, mix64, pick, rule_tag,
    seed_for, yyyymm, C2bRow, Input, Label, Labels, SplitMix64, TriggerRow,
};
use super::control_ref::{c2a_ref, c2b_ref, label_ref, lags_ref};
use super::params::Params;
use super::types::{Cube, Episode, Rule, XdTable, NF};
use std::collections::{BTreeMap, BTreeSet};

// ---------- данные ----------

/// Усечённая копия секций `docs/research/P-34-params.json`; `label_forward` подставляется тестом.
const PARAMS: &str = r#"{
 "grid_min":{"value":5},
 "episodes":{"D_min":[90,180,360,720,1440],"quantile_X_D":0.99,
  "E4_wick":{"return_share":0.5,"within_min":15},"outlier_tail_candles_lt":3,
  "market_stratum":{"min_overlap_share":0.5}},
 "features":{"windows_min":[5,15],"norm":{"history_days":7,"mad_factor":1.4826},
  "funding_z_history_days":30,"theta":[2,3,4]},
 "onset":{"L_pre_min":360},
 "controls":{"C2a":{"moments_per_first_trigger":10},
  "C2b":{"moments_per_episode":10,"exclude_same_sign_episode_window_min":[-360,1440]},
  "sigma_1m_window_days":7},
 "label_forward":{"D_min":@LD@,"H_min":@HS@},
 "split":{"fit":["2025-01","2025-12"],"test":["2026-01","2026-10"]}
}"#;

fn params(ld: &str, hs: &str) -> Params {
    Params::from_json(&PARAMS.replace("@LD@", ld).replace("@HS@", hs)).unwrap()
}

fn params_std() -> Params {
    params("[90,180,360]", "[90,360,1440]")
}

/// `X_D` по `episodes.D_min` [90, 180, 360, 720, 1440]: вверх и вниз.
fn xd_table() -> XdTable {
    XdTable {
        x: vec![
            [0.012, 0.012],
            [0.017, 0.015],
            [0.022, 0.021],
            [0.033, 0.030],
            [0.045, 0.042],
        ],
    }
}

/// xorshift64: детерминированный источник для данных тестов (не тот генератор, что проверяется).
struct Xs(u64);

impl Xs {
    fn step(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }

    fn below(&mut self, n: u64) -> u64 {
        self.step() % n
    }

    fn unit(&mut self) -> f64 {
        (self.step() >> 11) as f64 / (1u64 << 53) as f64
    }
}

/// `close_ff` случайного блуждания: префикс `NaN`, в 30 % минут цена не меняется (как перенос вперёд).
fn walk(rng: &mut Xs, len: usize, nan_prefix: usize, step: f64) -> Vec<f64> {
    let mut v = vec![f64::NAN; nan_prefix.min(len)];
    let mut x = 0.0f64;
    while v.len() < len {
        if rng.below(10) >= 3 {
            x += step * (rng.unit() - 0.5);
        }
        v.push(100.0 * x.exp());
    }
    v
}

fn rules6() -> Vec<Rule> {
    [
        (0, 0, 0),
        (0, 1, 1),
        (3, 0, 2),
        (8, 1, 0),
        (9, 1, 0),
        (10, 0, 1),
    ]
    .iter()
    .map(|&(fam, wi, ti)| Rule { fam, wi, ti })
    .collect()
}

/// Срабатывание правила в слоте: функция от (зерно, правило, слот), примерно 1 из 120.
fn fires_at(seed: u64, rule: Rule, i: usize) -> bool {
    mix64(seed ^ rule_tag(9, rule) ^ mix64(i as u64)) % 120 == 0
}

/// Первые срабатывания: без срабатывания в предыдущие 72 слота (`L_pre/5`).
fn firsts(seed: u64, rule: Rule, n: usize) -> Vec<usize> {
    let mut out = Vec::new();
    let mut last: Option<usize> = None;
    for i in 0..n {
        if fires_at(seed, rule, i) {
            if last.is_none_or(|l| l + 72 < i) {
                out.push(i);
            }
            last = Some(i);
        }
    }
    out
}

fn ep(sign: i8, t0: i64, t_end: i64) -> Episode {
    Episode {
        di: 0,
        sign,
        t0,
        t_end,
        mv: 0.1,
        flags: 0,
    }
}

struct Fx {
    cube: Cube,
    cf: Vec<f64>,
    first_min: i64,
    eps: Vec<Episode>,
}

/// Куб `n` слотов с `NaN` в z (5 %), r (знак ±, нули и `NaN`), σ (первые 100 слотов `NaN`); цены кончаются
/// за 400 минут до последнего слота (хвост без меток); эпизоды: `n_eps` случайных + 4 у краёв куба.
fn fixture(seed: u64, slot0: i64, n: usize, n_eps: usize) -> Fx {
    let mut rng = Xs(seed | 1);
    let nw = 2;
    let mut z = vec![0f32; NF * nw * n];
    for v in &mut z {
        *v = if rng.below(20) == 0 {
            f32::NAN
        } else {
            (rng.unit() * 10.0 - 5.0) as f32
        };
    }
    let mut r = vec![0f32; nw * n];
    for v in &mut r {
        *v = match rng.below(20) {
            0 => f32::NAN,
            1 => 0.0,
            _ => (rng.unit() * 0.04 - 0.02) as f32,
        };
    }
    let sigma: Vec<f32> = (0..n)
        .map(|i| {
            if i < 100 {
                f32::NAN
            } else {
                (0.001 + 0.004 * rng.unit()) as f32
            }
        })
        .collect();
    let cube = Cube {
        slot0,
        n,
        windows: vec![5, 15],
        z,
        r,
        sigma,
    };
    let first_min = 5 * slot0 - 50;
    let cf = walk(&mut rng, 5 * n + 50 - 400, 7, 0.004);
    let (t_a, t_b) = (5 * slot0, 5 * (slot0 + n as i64 - 1));
    let mut eps: Vec<Episode> = (0..n_eps)
        .map(|_| {
            let t0 = t_a + 200 + rng.below((t_b - t_a - 400) as u64) as i64;
            let sign = if rng.below(2) == 0 { 1 } else { -1 };
            ep(sign, t0, t0 + 60 + rng.below(1_500) as i64)
        })
        .collect();
    eps.push(ep(1, t_a + 10, t_a + 400));
    eps.push(ep(-1, t_b - 5, t_b + 300));
    eps.push(ep(1, t_a - 1_000, t_a - 800));
    eps.push(ep(-1, t_b + 1_000, t_b + 1_200));
    Fx {
        cube,
        cf,
        first_min,
        eps,
    }
}

fn inp<'a>(fx: &'a Fx, sym: &'a str) -> Input<'a> {
    Input {
        sym,
        cube: &fx.cube,
        cf: &fx.cf,
        first_min: fx.first_min,
        eps: &fx.eps,
    }
}

fn run_c2a(fx: &Fx, sym: &str, seed: u64, rules: &[Rule]) -> Vec<TriggerRow> {
    let (p, xd) = (params_std(), xd_table());
    let mut out = Vec::new();
    c2a_with(
        &inp(fx, sym),
        rules,
        &xd,
        &p,
        seed,
        |r| firsts(11, r, fx.cube.n),
        |row| out.push(row),
    )
    .unwrap();
    out
}

fn small_cube(slot0: i64, n: usize) -> Cube {
    let nw = 2;
    Cube {
        slot0,
        n,
        windows: vec![5, 15],
        z: vec![0.0; NF * nw * n],
        r: vec![0.01; nw * n],
        sigma: vec![0.003; n],
    }
}

/// Цена растёт на 0,1 % в минуту: любое окно метки с `D ≥ 90` даёт попадание вверх.
fn growth(len: usize) -> Vec<f64> {
    (0..len).map(|k| 100.0 * (0.001 * k as f64).exp()).collect()
}

// ---------- генератор, зёрна, месяц ----------

#[test]
fn splitmix_and_fnv_match_reference_vectors() {
    let mut r = SplitMix64::new(0);
    for want in [
        0xE220_A839_7B1D_CDAF_u64,
        0x6E78_9E6A_A1B9_65F4,
        0x06C4_5D18_8009_454F,
        0xF88B_B8A8_724C_81EC,
    ] {
        assert_eq!(r.next_u64(), want);
    }
    let mut r = SplitMix64::new(1_234_567);
    for want in [
        0x599E_D017_FB08_FC85_u64,
        0x2C73_F084_5854_0FA5,
        0x883E_BCE5_A3F2_7C77,
        0x3FBE_F740_E917_7B3F,
    ] {
        assert_eq!(r.next_u64(), want);
    }
    assert_eq!(mix64(5), SplitMix64::new(5).next_u64());
    assert_eq!(fnv1a(""), 0xCBF2_9CE4_8422_2325);
    assert_eq!(fnv1a("a"), 0xAF63_DC4C_8601_EC8C);
    assert_eq!(fnv1a("foobar"), 0x8594_4171_F739_67E8);
}

#[test]
fn below_is_in_range_and_unbiased() {
    let mut r = SplitMix64::new(3);
    assert_eq!(r.below(0), 0);
    assert_eq!(r.below(1), 0);
    let mut cnt = [0u32; 7];
    for _ in 0..70_000 {
        let x = r.below(7);
        assert!(x < 7);
        cnt[x as usize] += 1;
    }
    for c in cnt {
        assert!((9_000..11_000).contains(&c), "{cnt:?}");
    }
}

#[test]
fn seed_for_depends_on_every_argument_and_nothing_else() {
    let s = seed_for(1, "BTCUSDT", 2);
    assert_eq!(s, seed_for(1, "BTCUSDT", 2));
    assert_ne!(s, seed_for(2, "BTCUSDT", 2));
    assert_ne!(s, seed_for(1, "ETHUSDT", 2));
    assert_ne!(s, seed_for(1, "BTCUSDT", 3));
    assert_ne!(seed_for(1, "AB", 0), seed_for(1, "BA", 0));
    assert_ne!(seed_for(1, "", 0), seed_for(1, "", 1));
}

#[test]
fn pick_is_distinct_complete_and_uniform() {
    let mut rng = SplitMix64::new(9);
    let mut out = Vec::new();
    pick(&mut rng, 5, 10, &mut out);
    assert_eq!(out, [0u32, 1, 2, 3, 4]);
    pick(&mut rng, 0, 10, &mut out);
    assert!(out.is_empty());
    pick(&mut rng, 5, 5, &mut out);
    assert_eq!(out, [0u32, 1, 2, 3, 4]);
    let mut cnt = [0u32; 10];
    for _ in 0..20_000 {
        pick(&mut rng, 10, 3, &mut out);
        assert_eq!(out.len(), 3);
        let mut s = out.clone();
        s.sort_unstable();
        s.dedup();
        assert_eq!(s.len(), 3, "повтор в {out:?}");
        for &x in &out {
            cnt[x as usize] += 1;
        }
    }
    for c in cnt {
        assert!((5_400..6_600).contains(&c), "{cnt:?}");
    }
}

#[test]
fn yyyymm_matches_the_calendar() {
    let day = 1_440;
    assert_eq!(yyyymm(0), 202001);
    assert_eq!(yyyymm(31 * day - 1), 202001);
    assert_eq!(yyyymm(31 * day), 202002);
    assert_eq!(yyyymm((31 + 29) * day - 1), 202002, "2020 високосный");
    assert_eq!(yyyymm((31 + 29) * day), 202003);
    assert_eq!(yyyymm(1_827 * day), 202501);
    assert_eq!(yyyymm(1_827 * day - 1), 202412);
    assert_eq!(yyyymm(-1), 201912);
}

// ---------- метка вперёд ----------

#[test]
fn label_follows_the_definition_on_a_hand_made_series() {
    let p = params("[90,180]", "[90,360]");
    let xd = xd_table();
    let off = 1_000i64;
    let mut cf = vec![100.0f64; 400];
    for v in &mut cf[120..] {
        *v = 105.0;
    }
    for v in &mut cf[300..] {
        *v = 98.0;
    }
    let labs = Labels::new(&cf, off, &xd, &p).unwrap();
    let cases = [
        // подъём на 120-й минуте вне окна D = 90, но внутри D = 180 (H = 360)
        (9, 1, 90, Label::Miss),
        (9, 1, 360, Label::Hit),
        (9, -1, 90, Label::Miss),
        (9, -1, 360, Label::Miss),
        // ход начался до t_f и идёт/кончился: в попадание не входит
        (130, 1, 90, Label::Miss),
        (130, 1, 360, Label::Miss),
        (130, -1, 90, Label::Miss),
        (130, -1, 360, Label::Hit),
        // край данных: окно наибольшего D ≤ H не умещается — нет данных
        (219, -1, 360, Label::Hit),
        (219, -1, 90, Label::Hit),
        (220, -1, 360, Label::NoData),
        (220, -1, 90, Label::Hit),
        (309, -1, 90, Label::Miss),
        (310, -1, 90, Label::NoData),
        // нет знака, до начала данных
        (9, 0, 90, Label::NoData),
        (-2, 1, 90, Label::NoData),
    ];
    for (t, sign, h, want) in cases {
        let hi = usize::from(h == 360);
        assert_eq!(
            label(&cf, off, t + off, sign, &xd, &p, h),
            want,
            "label {t} {sign} {h}"
        );
        assert_eq!(
            label_ref(&cf, off, t + off, sign, &xd, &p, h),
            want,
            "ref {t} {sign} {h}"
        );
        assert_eq!(labs.get(t + off, sign, hi), want, "batch {t} {sign} {h}");
        assert_eq!(hit(&cf, off, t + off, sign, &xd, &p, h), want == Label::Hit);
    }
}

#[test]
fn labels_batch_single_and_naive_agree() {
    let xd = xd_table();
    let mut seen = [0usize; 3];
    for (ld, hs) in [
        ("[90,180,360,720,1440]", "[90,360,1440]"),
        ("[90,180]", "[90,180,720]"),
        ("[360,90]", "[90,360,1440]"),
    ] {
        let p = params(ld, hs);
        let mut rng = Xs(0x1234_5678_9ABC_DEF1);
        let first_min = 12_345i64;
        let cf = walk(&mut rng, 7_000, 7, 0.004);
        let labs = Labels::new(&cf, first_min, &xd, &p).unwrap();
        let end = first_min + cf.len() as i64;
        let mut ts: Vec<i64> = (first_min - 3..first_min + 40).collect();
        ts.extend((first_min + 40..end - 1_500).step_by(11));
        ts.extend((end - 1_500..end + 3).step_by(3));
        for d in [90, 180, 360, 720, 1_440] {
            ts.extend(end - 1 - d - 2..=end - 1 - d + 2);
        }
        for &t in &ts {
            for sign in [-1i8, 0, 1] {
                for (hi, &h) in p.label_forward.h_min.iter().enumerate() {
                    let want = label_ref(&cf, first_min, t, sign, &xd, &p, h);
                    assert_eq!(labs.get(t, sign, hi), want, "batch t={t} sign={sign} h={h}");
                    assert_eq!(
                        label(&cf, first_min, t, sign, &xd, &p, h),
                        want,
                        "single t={t} sign={sign} h={h}"
                    );
                    assert_eq!(hit(&cf, first_min, t, sign, &xd, &p, h), want == Label::Hit);
                    seen[match want {
                        Label::Hit => 0,
                        Label::Miss => 1,
                        Label::NoData => 2,
                    }] += 1;
                }
            }
        }
    }
    assert!(
        seen.iter().all(|&c| c > 100),
        "нужны все три исхода: {seen:?}"
    );
}

#[test]
fn labels_reject_bad_input_and_check_label_d_names_the_problem() {
    let (p, xd) = (params_std(), xd_table());
    let mut cf = walk(&mut Xs(5), 500, 0, 0.004);
    cf[100] = f64::NAN;
    assert!(
        Labels::new(&cf, 0, &xd, &p).is_err(),
        "NaN после первого close"
    );
    cf[100] = 0.0;
    assert!(Labels::new(&cf, 0, &xd, &p).is_err(), "нулевая цена");
    let cf = walk(&mut Xs(5), 500, 0, 0.004);
    let zero = XdTable {
        x: vec![[0.012, 0.012], [0.0, 0.015], [0.022, 0.021]],
    };
    assert!(Labels::new(&cf, 0, &zero, &p).is_err(), "X_D = 0");
    let short = XdTable {
        x: vec![[0.012, 0.012]],
    };
    assert!(Labels::new(&cf, 0, &short, &p).is_err(), "нет строки X_D");
    // цен нет вовсе или ряд пуст — не ошибка, меток нет
    let nan = vec![f64::NAN; 300];
    assert_eq!(
        Labels::new(&nan, 0, &xd, &p).unwrap().get(10, 1, 0),
        Label::NoData
    );
    assert!(Labels::new(&[], 0, &xd, &p).is_ok());
    assert_eq!(label(&[], 0, 5, 1, &xd, &p, 90), Label::NoData);

    assert!(check_label_d(&p).is_ok());
    let e = check_label_d(&params("[90,100]", "[90]"))
        .unwrap_err()
        .to_string();
    assert!(e.contains("100"), "{e}");
    let e = check_label_d(&params("[90]", "[60]"))
        .unwrap_err()
        .to_string();
    assert!(e.contains("60"), "{e}");
}

// ---------- лаги ----------

#[test]
fn lags_fast_equals_naive_and_stay_in_the_window() {
    let fx = fixture(7, 7_728, 2_500, 6);
    let (p, rules) = (params_std(), rules6());
    let fires = |r: Rule, i: usize| fires_at(99, r, i);
    let (mut found_neg, mut found_pos, mut none) = (0, 0, 0);
    for over in [None, Some(0), Some(150), Some(-30), Some(5_000)] {
        let fast = lags_with(&fx.cube, &fx.eps, &rules, &p, over, fires);
        let naive = lags_ref(&fx.cube, &fx.eps, &rules, &p, over, fires);
        assert_eq!(fast, naive, "override {over:?}");
        assert_eq!(fast.len(), fx.eps.len() * rules.len());
        for row in &fast {
            let e = &fx.eps[row.episode as usize];
            let l_post = over.unwrap_or(e.t_end - e.t0);
            match row.lag {
                Some(l) => {
                    assert!((-360..=l_post).contains(&l), "lag {l} вне окна");
                    assert_eq!((e.t0 + l).rem_euclid(5), 0, "срабатывание не на слоте");
                    if l < 0 {
                        found_neg += 1;
                    } else {
                        found_pos += 1;
                    }
                }
                None => none += 1,
            }
        }
    }
    assert!(
        found_neg > 0 && found_pos > 0 && none > 0,
        "{found_neg} {found_pos} {none}"
    );
}

#[test]
fn lags_edge_cases_empty_lists_and_empty_cube() {
    let fx = fixture(8, 7_728, 600, 2);
    let p = params_std();
    let always = |_: Rule, _: usize| true;
    assert!(lags_with(&fx.cube, &[], &rules6(), &p, None, always).is_empty());
    assert!(lags_with(&fx.cube, &fx.eps, &[], &p, None, always).is_empty());
    // срабатывает везде → лаг — первый слот окна: ceil((t0 − L_pre) / 5)·5 − t0
    let one = ep(1, 5 * 7_728 + 1_000, 5 * 7_728 + 1_100);
    let rows = lags_with(&fx.cube, &[one], &rules6()[..1], &p, None, always);
    assert_eq!(rows[0].lag, Some(-360));
    let odd = ep(1, 5 * 7_728 + 1_001, 5 * 7_728 + 1_100);
    let rows = lags_with(&fx.cube, &[odd], &rules6()[..1], &p, None, always);
    assert_eq!(
        rows[0].lag,
        Some(-356),
        "t0 не на слоте: первый слот окна — следующий кратный 5"
    );
    // окно целиком до куба и целиком после — None
    let before = ep(1, 5 * 7_728 - 2_000, 5 * 7_728 - 1_900);
    let after = ep(1, 5 * (7_728 + 600) + 2_000, 5 * (7_728 + 600) + 2_100);
    let rows = lags_with(&fx.cube, &[before, after], &rules6()[..1], &p, None, always);
    assert!(rows.iter().all(|r| r.lag.is_none()));
    // пустой куб
    let empty = Cube {
        slot0: 0,
        n: 0,
        windows: vec![5, 15],
        z: vec![],
        r: vec![],
        sigma: vec![],
    };
    let rows = lags_with(&empty, &fx.eps, &rules6(), &p, None, always);
    assert_eq!(rows.len(), fx.eps.len() * 6);
    assert!(rows.iter().all(|r| r.lag.is_none()));
    // один эпизод
    let rows = lags_with(&fx.cube, &fx.eps[..1], &rules6(), &p, None, |_, _| false);
    assert_eq!(rows.len(), 6);
    assert!(rows.iter().all(|r| r.episode == 0 && r.lag.is_none()));
}

// ---------- C2a ----------

#[test]
fn c2a_fast_equals_naive_and_rows_are_consistent() {
    let fx = fixture(11, 7_728, 3_000, 6);
    let (p, xd, rules) = (params_std(), xd_table(), rules6());
    let trig = |r: Rule| firsts(11, r, fx.cube.n);
    let i = inp(&fx, "AAAUSDT");
    let mut fast = Vec::new();
    c2a_with(&i, &rules, &xd, &p, 5, trig, |row| fast.push(row)).unwrap();
    let naive = c2a_ref(&i, &rules, &xd, &p, 5, trig).unwrap();
    assert_eq!(fast, naive);
    assert!(fast.len() > 20, "срабатываний мало: {}", fast.len());
    let nh = p.label_forward.h_min.len();
    let (mut run_yes, mut run_no, mut full, mut hits, mut cnt) = (0, 0, 0, 0u32, 0u32);
    let mut labels = [0usize; 3];
    for row in &fast {
        assert_eq!(row.label.len(), nh);
        assert_eq!((row.c2a_hit.len(), row.c2a_n.len()), (nh, nh));
        assert!(matches!(row.month, 202001 | 202002));
        assert_eq!(row.t_f.rem_euclid(5), 0);
        for hi in 0..nh {
            assert!(row.c2a_hit[hi] <= row.c2a_n[hi] && row.c2a_n[hi] <= row.c2a_drawn);
            hits += u32::from(row.c2a_hit[hi]);
            cnt += u32::from(row.c2a_n[hi]);
            labels[match row.label[hi] {
                Label::Hit => 0,
                Label::Miss => 1,
                Label::NoData => 2,
            }] += 1;
        }
        if row.sign == 0 {
            assert!(row.label.iter().all(|&l| l == Label::NoData) && row.c2a_drawn == 0);
        }
        if row.in_episode_running {
            run_yes += 1;
        } else {
            run_no += 1;
        }
        full += usize::from(row.c2a_drawn == 10);
    }
    assert!(run_yes > 0 && run_no > 0, "{run_yes} {run_no}");
    assert!(full > 0 && hits > 0 && hits < cnt, "{full} {hits} {cnt}");
    assert!(labels.iter().all(|&c| c > 0), "{labels:?}");
}

#[test]
fn c2a_is_deterministic_and_independent_of_order_threads_and_rule_list() {
    let (fx_a, fx_b) = (fixture(31, 7_728, 3_000, 6), fixture(32, 7_728, 3_000, 6));
    let rules = rules6();
    let a1 = run_c2a(&fx_a, "AAAUSDT", 7, &rules);
    let b1 = run_c2a(&fx_b, "BBBUSDT", 7, &rules);
    // другой порядок обработки контрактов
    let b2 = run_c2a(&fx_b, "BBBUSDT", 7, &rules);
    let a2 = run_c2a(&fx_a, "AAAUSDT", 7, &rules);
    assert_eq!((&a1, &b1), (&a2, &b2));
    // несколько потоков, разный порядок внутри потока
    std::thread::scope(|s| {
        let hs: Vec<_> = (0..4)
            .map(|k| {
                let (fa, fb, rs) = (&fx_a, &fx_b, &rules);
                s.spawn(move || {
                    if k % 2 == 0 {
                        (run_c2a(fa, "AAAUSDT", 7, rs), run_c2a(fb, "BBBUSDT", 7, rs))
                    } else {
                        let b = run_c2a(fb, "BBBUSDT", 7, rs);
                        (run_c2a(fa, "AAAUSDT", 7, rs), b)
                    }
                })
            })
            .collect();
        for h in hs {
            let (a, b) = h.join().unwrap();
            assert_eq!((&a, &b), (&a1, &b1));
        }
    });
    // другое зерно и другой символ — другие моменты
    assert_ne!(a1, run_c2a(&fx_a, "AAAUSDT", 8, &rules));
    assert_ne!(a1, run_c2a(&fx_a, "ZZZUSDT", 7, &rules));
    // порядок и состав списка правил не меняют строк правила
    let by_rule = |v: &[TriggerRow], r: Rule| -> Vec<TriggerRow> {
        v.iter().filter(|x| x.rule == r).cloned().collect()
    };
    let mut rev = rules.clone();
    rev.reverse();
    let a_rev = run_c2a(&fx_a, "AAAUSDT", 7, &rev);
    assert_eq!(a_rev.len(), a1.len());
    for &r in &rules {
        assert_eq!(by_rule(&a1, r), by_rule(&a_rev, r));
    }
    let one = run_c2a(&fx_a, "AAAUSDT", 7, &rules[2..3]);
    assert_eq!(one, by_rule(&a1, rules[2]));
}

#[test]
fn c2a_month_decile_and_candidate_limits() {
    let (p, xd) = (params_std(), xd_table());
    let rule = Rule {
        fam: 0,
        wi: 0,
        ti: 0,
    };
    let first_min = 5 * 8_920 - 10;
    let run = |cube: &Cube, cf: &[f64], trig: Vec<usize>| -> Vec<TriggerRow> {
        let i = Input {
            sym: "X",
            cube,
            cf,
            first_min,
            eps: &[],
        };
        let mut rows = Vec::new();
        c2a_with(&i, &[rule], &xd, &p, 1, |_| trig.clone(), |r| rows.push(r)).unwrap();
        rows
    };

    // (а) граница месяцев: слоты 8920..8927 — январь, 8928..8935 — февраль; по 8 кандидатов (< 10) — берутся все
    let (cube, cf) = (small_cube(8_920, 16), growth(1_000));
    let rows = run(&cube, &cf, vec![2, 10]);
    assert_eq!(rows.len(), 2);
    assert_eq!((rows[0].month, rows[1].month), (202001, 202002));
    for row in &rows {
        assert_eq!(row.sign, 1);
        assert_eq!(row.c2a_drawn, 8);
        assert_eq!(row.c2a_n, [8, 8, 8]);
        assert_eq!(row.c2a_hit, [8, 8, 8]);
        assert_eq!(row.label, [Label::Hit; 3]);
        assert!(!row.in_episode_running);
    }

    // (б) годны только слоты 2 и 5 (у остальных z признака — NaN): кандидатов два; в феврале — нет
    let mut cube = small_cube(8_920, 16);
    for (i, v) in cube.z.iter_mut().take(16).enumerate() {
        if i != 2 && i != 5 {
            *v = f32::NAN;
        }
    }
    let rows = run(&cube, &cf, vec![2, 6, 10]);
    assert_eq!(
        rows.iter().map(|r| r.c2a_drawn).collect::<Vec<_>>(),
        [2, 2, 0]
    );
    assert_eq!(rows[2].c2a_n, [0, 0, 0]);
    assert_eq!(
        rows[2].label,
        [Label::Hit; 3],
        "метка самого срабатывания считается и без контроля"
    );

    // (в) σ слота — NaN: дециля нет, моментов нет; r_w = 0: знака нет, метки NoData
    let mut cube = small_cube(8_920, 16);
    cube.sigma[3] = f32::NAN;
    cube.r[4] = 0.0;
    let rows = run(&cube, &cf, vec![3, 4]);
    assert_eq!((rows[0].c2a_drawn, rows[0].sign), (0, 1));
    assert_eq!(rows[0].c2a_n, [0, 0, 0]);
    assert_eq!((rows[1].c2a_drawn, rows[1].sign), (0, 0));
    assert_eq!(rows[1].label, [Label::NoData; 3]);
    assert!(!rows[1].in_episode_running);

    // (г) данные кончаются через 110 минут: метки H = 90 есть только у слотов 0..=2, H = 360/1440 — ни у одного
    let (cube, cf) = (small_cube(8_920, 16), growth(111));
    let rows = run(&cube, &cf, vec![3]);
    assert_eq!(rows[0].label, [Label::NoData; 3]);
    assert_eq!(rows[0].c2a_drawn, 8);
    assert_eq!(rows[0].c2a_n, [3, 0, 0]);
    assert_eq!(rows[0].c2a_hit, [3, 0, 0]);
}

#[test]
fn c2a_empty_inputs_and_bad_trigger_index() {
    let (p, xd) = (params_std(), xd_table());
    let rule = Rule {
        fam: 0,
        wi: 0,
        ti: 0,
    };
    let (cube, cf) = (small_cube(8_920, 16), growth(1_000));
    let i = Input {
        sym: "X",
        cube: &cube,
        cf: &cf,
        first_min: 5 * 8_920 - 10,
        eps: &[],
    };
    let mut rows = Vec::new();
    c2a_with(&i, &[], &xd, &p, 1, |_| vec![1], |r| rows.push(r)).unwrap();
    c2a_with(&i, &[rule], &xd, &p, 1, |_| vec![], |r| rows.push(r)).unwrap();
    assert!(rows.is_empty());
    // индекс слота за кубом — ошибка, а не тихий пропуск; правило вне куба — тоже
    let e = c2a_with(&i, &[rule], &xd, &p, 1, |_| vec![16], |r| rows.push(r)).unwrap_err();
    assert!(e.to_string().contains("16"), "{e}");
    let wild = Rule {
        fam: 0,
        wi: 5,
        ti: 0,
    };
    assert!(c2a_with(&i, &[wild], &xd, &p, 1, |_| vec![], |r| rows.push(r)).is_err());
    // пустой куб и пустые цены
    let empty = Cube {
        slot0: 0,
        n: 0,
        windows: vec![5, 15],
        z: vec![],
        r: vec![],
        sigma: vec![],
    };
    let i = Input {
        sym: "X",
        cube: &empty,
        cf: &[],
        first_min: 0,
        eps: &[],
    };
    c2a_with(&i, &[rule], &xd, &p, 1, |_| vec![], |r| rows.push(r)).unwrap();
    assert!(c2a_with(&i, &[rule], &xd, &p, 1, |_| vec![0], |r| rows.push(r)).is_err());
    assert!(rows.is_empty());
    // куб с неверной длиной z — ошибка
    let mut broken = small_cube(8_920, 16);
    broken.z.pop();
    let i = Input {
        sym: "X",
        cube: &broken,
        cf: &cf,
        first_min: 5 * 8_920 - 10,
        eps: &[],
    };
    assert!(c2a_with(&i, &[rule], &xd, &p, 1, |_| vec![], |r| rows.push(r)).is_err());
}

// ---------- C2b ----------

#[test]
fn c2b_fast_equals_naive_and_respects_constraints() {
    let fx = fixture(21, 7_728, 3_000, 6);
    let (p, rules) = (params_std(), rules6());
    let fires = |r: Rule, i: usize| fires_at(77, r, i);
    let i = inp(&fx, "AAAUSDT");
    let l_post = 150;
    let mut fast: Vec<C2bRow> = Vec::new();
    c2b_with(&i, &rules, &p, l_post, 3, fires, |row| fast.push(row)).unwrap();
    let naive = c2b_ref(&i, &rules, &p, l_post, 3, fires);
    assert_eq!(fast, naive);
    assert!(fast.len() > 50, "строк мало: {}", fast.len());

    let [wa, wb] = p.controls.c2b.exclude_same_sign_episode_window_min;
    let t_last = 5 * (fx.cube.slot0 + fx.cube.n as i64 - 1);
    let mut moments: BTreeMap<u32, BTreeSet<i64>> = BTreeMap::new();
    let mut with_lag = 0;
    for row in &fast {
        let e = &fx.eps[row.episode_idx as usize];
        let t = row.t_pseudo;
        assert_eq!(yyyymm(t), yyyymm(e.t0), "месяц");
        assert_eq!(t.rem_euclid(5), 0);
        assert!(t + l_post <= t_last, "окно лага за кубом");
        let clash = fx
            .eps
            .iter()
            .any(|x| x.sign == e.sign && (t + wa..=t + wb).contains(&x.t0));
        assert!(!clash, "в окне исключения есть эпизод того же знака");
        if let Some(l) = row.lag {
            assert!((-360..=l_post).contains(&l));
            with_lag += 1;
        }
        moments.entry(row.episode_idx).or_default().insert(t);
    }
    assert!(with_lag > 0);
    assert!(moments.values().all(|s| s.len() <= 10));
    // у каждого эпизода моменты общие для всех правил: строк = моментов × правил
    assert_eq!(
        fast.len(),
        moments.values().map(BTreeSet::len).sum::<usize>() * rules.len()
    );
    // другое зерно — другие моменты
    let mut other = Vec::new();
    c2b_with(&i, &rules, &p, l_post, 4, fires, |row| other.push(row)).unwrap();
    assert_ne!(fast, other);
    // порядок обработки контрактов и потоки
    let again = std::thread::scope(|s| {
        let h = s.spawn(|| {
            let mut v = Vec::new();
            c2b_with(&i, &rules, &p, l_post, 3, fires, |row| v.push(row)).unwrap();
            v
        });
        h.join().unwrap()
    });
    assert_eq!(fast, again);
}

#[test]
fn c2b_takes_all_candidates_when_fewer_than_requested_and_handles_empties() {
    let p = params_std();
    let rule = Rule {
        fam: 0,
        wi: 0,
        ti: 0,
    };
    let cube = small_cube(8_000, 400);
    let cf = growth(2_100);
    let never = |_: Rule, _: usize| false;
    // кандидаты: σ-слот «до» (−72 слота) должен лежать в кубе, окно лага — тоже: t' + 1600 ≤ последний слот;
    // это слоты 72..=79 (t' = 40360..=40395); эпизод того же знака (t0 = 41950) в их окно исключения не входит
    let eps = [ep(1, 41_950, 42_100)];
    let i = Input {
        sym: "X",
        cube: &cube,
        cf: &cf,
        first_min: 0,
        eps: &eps,
    };
    let mut rows = Vec::new();
    c2b_with(&i, &[rule], &p, 1_600, 1, never, |r| rows.push(r)).unwrap();
    let got: BTreeSet<i64> = rows.iter().map(|r| r.t_pseudo).collect();
    let want: BTreeSet<i64> = (0..8).map(|m| 40_360 + 5 * m).collect();
    assert_eq!((rows.len(), got), (8, want));
    assert!(rows.iter().all(|r| r.episode_idx == 0 && r.lag.is_none()));
    assert_eq!(rows, c2b_ref(&i, &[rule], &p, 1_600, 1, never));

    // эпизод того же знака рядом исключает кандидатов; другого знака — нет
    let near = [ep(1, 41_950, 42_100), ep(1, 40_700, 40_800)];
    let i2 = Input { eps: &near, ..i };
    let mut rows = Vec::new();
    c2b_with(&i2, &[rule], &p, 1_600, 1, never, |r| rows.push(r)).unwrap();
    assert_eq!(rows.iter().filter(|r| r.episode_idx == 0).count(), 0);
    let other_sign = [ep(1, 41_950, 42_100), ep(-1, 40_700, 40_800)];
    let i3 = Input {
        eps: &other_sign,
        ..i
    };
    let mut rows = Vec::new();
    c2b_with(&i3, &[rule], &p, 1_600, 1, never, |r| rows.push(r)).unwrap();
    assert_eq!(rows.iter().filter(|r| r.episode_idx == 0).count(), 8);

    // пусто: нет эпизодов, нет правил, пустой куб
    let mut rows = Vec::new();
    c2b_with(
        &Input { eps: &[], ..i },
        &[rule],
        &p,
        1_600,
        1,
        never,
        |r| rows.push(r),
    )
    .unwrap();
    c2b_with(&i, &[], &p, 1_600, 1, never, |r| rows.push(r)).unwrap();
    let empty = Cube {
        slot0: 0,
        n: 0,
        windows: vec![5, 15],
        z: vec![],
        r: vec![],
        sigma: vec![],
    };
    c2b_with(
        &Input { cube: &empty, ..i },
        &[rule],
        &p,
        1_600,
        1,
        never,
        |r| rows.push(r),
    )
    .unwrap();
    assert!(rows.is_empty());
}
