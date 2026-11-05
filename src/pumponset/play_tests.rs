//! Тесты выборки «монеты в игре» (`play.rs`; флаги `run`: `--hv-natr-pct`/`--hv-n`/`--no-reversal`/`--hv-first-phase`):
//! NATR на ручных свечах, откуп на V- и Λ-пути, первая фаза, `hv` по факту у эпизодов и по состоянию у срабатываний,
//! контроли C2a/C2b только из «игровых» минут, неизменность прогона при нейтральных флагах, детерминизм по потокам,
//! отсутствие утечки в будущее, число строк выгрузки против `daycells`, выходы на тех же срабатываниях.

use super::cache::Contract;
use super::control::{self, sign_of, slot_t};
use super::dump::{HEADER, PLAY_COLS};
use super::feat;
use super::params::Params;
use super::play::{
    first_phase_mask, natr_series, reversal_masks, Gate, HvCfg, Play, PlayArgs, PlayCfg,
};
use super::run::{mask_r_with, run, RunArgs, Stage};
use super::run_tests::{
    approx, num, params_json, pump_contract, put, rules_file, table, Row, SEED, T0, XD,
};
use super::types::{Episode, Rule, XdTable};
use clap::Parser;
use std::fs;
use std::path::{Path, PathBuf};

const NAN: f64 = f64::NAN;

// ---------- данные ----------

fn f32s(v: &[f64]) -> Vec<f32> {
    v.iter().map(|&x| x as f32).collect()
}

/// Контракт из рядов close / high / low с нулевой первой минутой (`NaN` — минута без сделок).
fn hand(close: &[f64], high: &[f64], low: &[f64]) -> Contract {
    Contract {
        first_min: 0,
        close: close.to_vec(),
        high: f32s(high),
        low: f32s(low),
        ..Default::default()
    }
}

/// Контракт по `close`; `w(m)` — полуразмах свечи минуты `m` в долях цены (`high = c·(1+w)`, `low = c·(1−w)`):
/// `TR` такой минуты — `200·w` процентов, если гэпа от прошлого close нет.
fn with_w(close: Vec<f64>, w: impl Fn(usize) -> f64) -> Contract {
    let high = close
        .iter()
        .enumerate()
        .map(|(m, &x)| (x * (1.0 + w(m))) as f32)
        .collect();
    let low = close
        .iter()
        .enumerate()
        .map(|(m, &x)| (x * (1.0 - w(m))) as f32)
        .collect();
    Contract {
        first_min: 0,
        close,
        high,
        low,
        ..Default::default()
    }
}

/// Тихая минута (`TR` = 0,02 %) и минута всплеска (`TR` = 40 %).
const QUIET: f64 = 1.0e-4;
const BIG: f64 = 0.2;

/// `w(m)`: всплеск `BIG` на отрезках `[начало, начало + длина)`, иначе `QUIET`.
fn bursts(parts: &[(usize, usize)]) -> impl Fn(usize) -> f64 {
    let parts = parts.to_vec();
    move |m| {
        if parts.iter().any(|&(a, l)| m >= a && m < a + l) {
            BIG
        } else {
            QUIET
        }
    }
}

/// `base` с `high`/`low` по `w(m)` вокруг его `close`; остальное (объёмы, OI, фандинг) — из `base`.
fn lit(base: Contract, w: impl Fn(usize) -> f64) -> Contract {
    let hl = with_w(base.close.clone(), w);
    Contract {
        high: hl.high,
        low: hl.low,
        ..base
    }
}

/// Контракт пампа (`run_tests::pump_contract`, 12 суток) с «всплесками» высоких/низких цен.
fn pump_hl(parts: &[(usize, usize)]) -> Contract {
    lit(pump_contract(17_280, &[T0]), bursts(parts))
}

/// Все значения маски на `[lo, hi)` равны `want`.
fn range_is(mask: &[bool], lo: usize, hi: usize, want: bool) {
    for (m, &x) in mask.iter().enumerate().take(hi).skip(lo) {
        assert_eq!(x, want, "m = {m}");
    }
}

/// Обвал: цена умножается на `exp(−ln_drop)` линейно за 10 минут от минуты `at`; `high`/`low` пересчитывает вызывающий.
fn crashed(mut c: Contract, at: usize, ln_drop: f64) -> Contract {
    for (m, x) in c.close.iter_mut().enumerate() {
        let k = ((m as f64 - at as f64) / 10.0).clamp(0.0, 1.0);
        *x *= (-ln_drop * k).exp();
    }
    c
}

/// V-путь: 100 до минуты 399, обвал на `ln(1,25)` за 10 минут (минуты 400…409, дно 80), дно до 899, отскок на ту же
/// величину за 10 минут (900…909), 100 до конца (1500 минут).
fn v_path() -> Vec<f64> {
    let d = (100.0f64 / 80.0).ln();
    (0..1500usize)
        .map(|m| {
            let lp = if m < 400 {
                0.0
            } else if m < 410 {
                -d * (m - 399) as f64 / 10.0
            } else if m < 900 {
                -d
            } else if m < 910 {
                -d + d * (m - 899) as f64 / 10.0
            } else {
                0.0
            };
            100.0 * lp.exp()
        })
        .collect()
}

/// Детерминированный источник [0, 1) для данных тестов.
struct Lcg(u64);

impl Lcg {
    fn next(&mut self) -> f64 {
        self.0 = self
            .0
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        (self.0 >> 11) as f64 / (1u64 << 53) as f64
    }
}

/// Другие свечи с минуты `from`: цена умножается на `1 + (u − 0,5)·amp`, `high`/`low` расходятся на ±30 % от неё.
fn tamper(c: &mut Contract, from: usize, rng: &mut Lcg, amp: f64) {
    let tail = c
        .close
        .iter_mut()
        .zip(c.high.iter_mut())
        .zip(c.low.iter_mut())
        .skip(from);
    for ((x, h), l) in tail {
        *x *= 1.0 + (rng.next() - 0.5) * amp;
        *h = (*x * 1.3) as f32;
        *l = (*x * 0.7) as f32;
    }
}

fn same_f32(a: f32, b: f32) -> bool {
    a.to_bits() == b.to_bits() || (a.is_nan() && b.is_nan())
}

fn near(got: f32, want: f64) {
    if want.is_nan() {
        assert!(got.is_nan(), "ждали NaN, получили {got}");
    } else {
        assert!(
            (f64::from(got) - want).abs() < 1.0e-4,
            "{got} против {want}"
        );
    }
}

fn cfg_hv(pct: f64, n: usize) -> PlayCfg {
    PlayCfg {
        hv: Some(HvCfg { natr_pct: pct, n }),
        no_reversal: false,
        first_phase: false,
    }
}

// ---------- NATR ----------

#[test]
fn natr_on_hand_candles() {
    // минуты 2, 3, 6 — без сделок; гэп между close и следующей свечью после пропуска считается от ПОСЛЕДНЕГО close
    let c = hand(
        &[10.0, 11.0, NAN, NAN, 20.0, 22.0, NAN, 21.0, 21.0],
        &[11.0, 12.0, NAN, NAN, 21.0, 23.0, NAN, 22.5, 21.5],
        &[9.0, 10.5, NAN, NAN, 19.0, 21.5, NAN, 20.5, 20.0],
    );
    // TR, % от close минуты:
    let t0 = 2.0 / 10.0 * 100.0; // первая свеча: h − l = 2, прошлого close нет
    let t1 = 2.0 / 11.0 * 100.0; // max(1,5; |12 − 10| = 2; |10,5 − 10| = 0,5) = 2: гэп вверх от close 10
    let t4 = 10.0 / 20.0 * 100.0; // после дыры: max(2; |21 − 11| = 10; |19 − 11| = 8) = 10, close_ff = 11
    let t5 = 3.0 / 22.0 * 100.0; // max(1,5; |23 − 20| = 3; |21,5 − 20| = 1,5) = 3
    let t7 = 2.0 / 21.0 * 100.0; // после дыры (6): прошлый close 22: max(2; 0,5; 1,5) = 2
    let t8 = 1.5 / 21.0 * 100.0; // max(1,5; 0,5; 1) = 1,5
                                 // окно n = 4 (нужно ≥ 2 свечи), включая минуту m: [m − 3, m]
    let want4 = [
        NAN,
        NAN,
        NAN,
        (t0 + t1) / 2.0,
        (t1 + t4) / 2.0,
        (t4 + t5) / 2.0,
        (t4 + t5) / 2.0,
        (t4 + t5 + t7) / 3.0,
        (t5 + t7 + t8) / 3.0,
    ];
    let got = natr_series(&c, 4);
    assert_eq!(got.len(), c.close.len());
    for (g, w) in got.iter().zip(want4) {
        near(*g, w);
    }
    // окно n = 3 (достаточно одной свечи): окно должно целиком лежать в сетке (m ≥ 2)
    let want3 = [
        NAN,
        NAN,
        (t0 + t1) / 2.0,
        t1,
        t4,
        (t4 + t5) / 2.0,
        (t4 + t5) / 2.0,
        (t5 + t7) / 2.0,
        (t7 + t8) / 2.0,
    ];
    for (g, w) in natr_series(&c, 3).iter().zip(want3) {
        near(*g, w);
    }
    // нормировка — на close самой минуты, не прошлой: TR минуты 4 делится на 20, а не на 11
    assert!((f64::from(natr_series(&c, 1)[4]) - t4).abs() < 1.0e-4);
    assert!(f64::from(natr_series(&c, 1)[4]) < 100.0 * 10.0 / 11.0 - 1.0);
}

#[test]
fn natr_needs_half_the_window_in_candles() {
    // свечи только в минутах 0 и 6
    let c = hand(
        &[10.0, NAN, NAN, NAN, NAN, NAN, 12.0],
        &[10.5, NAN, NAN, NAN, NAN, NAN, 12.5],
        &[9.5, NAN, NAN, NAN, NAN, NAN, 11.5],
    );
    let tr0 = 1.0 / 10.0 * 100.0;
    let tr6 = 2.5 / 12.0 * 100.0; // max(1; |12,5 − 10| = 2,5; |11,5 − 10| = 1,5)
                                  // n = 4: нужно 2 свечи, в окне одна → NaN; n = 3 и 2: нужна одна → определено
    let n4 = natr_series(&c, 4);
    assert!(n4[3].is_nan() && n4[6].is_nan());
    let n3 = natr_series(&c, 3);
    near(n3[2], tr0);
    near(n3[6], tr6);
    let n2 = natr_series(&c, 2);
    near(n2[1], tr0);
    near(n2[6], tr6);
    // n = 5 (5 / 2 = 2 целочисленно): [2, 6] — одна свеча; n = 6 (нужно 3), n = 7 (нужно 3): в окне 1–2 свечи
    assert!(natr_series(&c, 5)[6].is_nan() && natr_series(&c, 5)[4].is_nan());
    assert!(natr_series(&c, 6)[5].is_nan() && natr_series(&c, 6)[6].is_nan());
    assert!(natr_series(&c, 7)[6].is_nan());
    // n = 0, окно длиннее ряда, контракт без high/low — всё NaN
    assert!(natr_series(&c, 0).iter().all(|x| x.is_nan()));
    assert!(natr_series(&c, 50).iter().all(|x| x.is_nan()));
    let bare = Contract {
        close: vec![10.0, 11.0, 12.0, 13.0],
        ..Default::default()
    };
    assert!(natr_series(&bare, 2).iter().all(|x| x.is_nan()));
}

#[test]
fn natr_uses_only_data_up_to_the_minute() {
    let base = hand(
        &[10.0, 11.0, NAN, NAN, 20.0, 22.0, NAN, 21.0, 21.0],
        &[11.0, 12.0, NAN, NAN, 21.0, 23.0, NAN, 22.5, 21.5],
        &[9.0, 10.5, NAN, NAN, 19.0, 21.5, NAN, 20.5, 20.0],
    );
    let mut late = base.clone();
    let tail = late
        .close
        .iter_mut()
        .zip(late.high.iter_mut())
        .zip(late.low.iter_mut())
        .enumerate()
        .skip(6);
    for (m, ((x, h), l)) in tail {
        *x = 50.0 + m as f64;
        *h = 80.0;
        *l = 30.0;
    }
    let (a, b) = (natr_series(&base, 4), natr_series(&late, 4));
    for (m, (x, y)) in a.iter().zip(&b).enumerate().take(6) {
        assert!(same_f32(*x, *y), "минута {m}: {x} против {y}");
    }
    assert!(
        !same_f32(a[7], b[7]),
        "правка после m = 6 должна менять поздние значения"
    );
}

// ---------- откуп ----------

#[test]
fn reversal_masks_v_path_and_its_mirror() {
    let cf = v_path();
    let (ol, os) = reversal_masks(&cf, 360, 0.2, 0.2);
    // до обвала окно либо неполное (m < 360), либо плоское — лонг и шорт не исключены
    range_is(&ol, 0, 400, true);
    range_is(&os, 0, 400, true);
    // лонг после дна исключён, пока в окне [m − 360, m] есть цена до обвала (последняя — минута 399 → m ≤ 759)
    range_is(&ol, 410, 760, false);
    // окно ушло за обвал: дно 80 — лонг снова допустим
    range_is(&ol, 770, 900, true);
    // шорт на дне и в падении не исключён (цена не выше минимума окна)
    range_is(&os, 400, 900, true);
    // отскок до 100: ln(c / 80) ≥ 0,2 с минуты 908 → шорт после ралли исключён, пока 80 в окне (m ≤ 1259)
    assert!(os[907] && !os[908] && !os[909]);
    range_is(&os, 910, 1260, false);
    range_is(&os, 1270, 1500, true);
    // лонг в отскоке не исключён: падения от максимума окна нет
    range_is(&ol, 900, 1500, true);
    // зеркало (Λ-путь, c' = 10⁴ / c): шорт «после ралли» исключён ровно там, где лонг «после обвала» на V
    let mirror: Vec<f64> = cf.iter().map(|&x| 1.0e4 / x).collect();
    let (ml, ms) = reversal_masks(&mirror, 360, 0.2, 0.2);
    assert_eq!(ms, ol);
    assert_eq!(ml, os);
}

#[test]
fn reversal_uses_the_threshold_of_its_own_direction() {
    let cf = v_path();
    // обвал ln(1,25) ≈ 0,223: вниз порог 0,5 его не берёт, вверх порог 0,2 отскок берёт
    let (ol, os) = reversal_masks(&cf, 360, 0.2, 0.5);
    assert!(ol.iter().all(|&x| x), "порог вниз 0,5 выше обвала");
    assert!(!os[1000], "порог вверх 0,2 ниже отскока");
    let (ol, os) = reversal_masks(&cf, 360, 0.5, 0.2);
    assert!(!ol[500], "порог вниз 0,2 ниже обвала");
    assert!(os.iter().all(|&x| x), "порог вверх 0,5 выше отскока");
}

#[test]
fn reversal_without_a_full_window_is_not_evidence() {
    // первые 200 минут close нет; цена 100 до минуты 249, со 250-й — 80 (ступенька вниз −20 %)
    let mut cf = vec![NAN; 200];
    cf.extend(vec![100.0; 50]);
    cf.extend(vec![80.0; 700]);
    let (ol, _) = reversal_masks(&cf, 360, 0.2, 0.2);
    // окно [m − 360, m] целиком в данных с m = 560 (первый close — минута 200): раньше — нет свидетельства, лонг допустим
    range_is(&ol, 0, 560, true);
    // m = 560…609: в окне цена 100 (минуты ≤ 249) → исключён; с m = 610 окно начинается с минуты 250 → допустим
    range_is(&ol, 560, 610, false);
    assert!(ol[610], "окно [250, 610] уже без цены до обвала");
    // нет ни одного close — все допустимы
    let (a, b) = reversal_masks(&[NAN; 10], 5, 0.2, 0.2);
    assert!(a.iter().chain(&b).all(|&x| x));
}

// ---------- первая фаза ----------

fn mask_of(len: usize, on: &[usize]) -> Vec<bool> {
    let mut v = vec![false; len];
    for &i in on {
        v[i] = true;
    }
    v
}

#[test]
fn first_phase_is_the_transition_minute_after_a_quiet_window() {
    // L_pre = 5: первая фаза в m, если hv есть в m и не было в [m − 5, m − 1]
    let hv = mask_of(40, &[10, 11, 12, 18, 20, 30]);
    let want = mask_of(40, &[10, 18, 30]);
    assert_eq!(first_phase_mask(&hv, 5), want);
    // граница: последняя hv-минута 12; в 17 окно [12, 16] её ещё содержит → не первая; в 18 окно [13, 17] уже чистое
    let hv = mask_of(30, &[12, 17, 23]);
    assert_eq!(first_phase_mask(&hv, 5), mask_of(30, &[12, 23]));
    // перехода нет (hv всё время) — только самая первая минута
    let always = vec![true; 20];
    assert_eq!(first_phase_mask(&always, 5), mask_of(20, &[0]));
    // hv нет нигде
    assert_eq!(first_phase_mask(&[false; 8], 3), vec![false; 8]);
}

// ---------- маски контракта ----------

/// Контракт с тремя всплесками на постоянной цене 100: `hv` (NATR_30 ≥ 1) — [407, 471], [907, 971], [1107, 1171].
fn three_bursts() -> Contract {
    with_w(vec![100.0; 1500], |m| {
        if (400..450).contains(&m) || (900..950).contains(&m) || (1100..1150).contains(&m) {
            0.02
        } else {
            QUIET
        }
    })
}

#[test]
fn play_masks_on_three_bursts() {
    let c = three_bursts();
    let cf = c.close_ff();
    let pl = Play::build(&c, &cf, cfg_hv(1.0, 30), 360, [NAN; 2]);
    // NATR: 7 минут всплеска в окне (4 % каждая) — ниже порога, 8 — выше
    assert!((f64::from(pl.natr_at(406)) - (7.0 * 4.0 + 23.0 * 0.02) / 30.0).abs() < 1.0e-3);
    assert!((f64::from(pl.natr_at(407)) - (8.0 * 4.0 + 22.0 * 0.02) / 30.0).abs() < 1.0e-3);
    let hv: Vec<i64> = (0..1500).filter(|&m| pl.hv_at(m)).collect();
    let want: Vec<i64> = (407..=471).chain(907..=971).chain(1107..=1171).collect();
    assert_eq!(hv, want);
    // первая фаза — только минута входа в игру после 360 тихих минут: 407 и 907; 1107 — hv был 971 (< 360 назад)
    let first: Vec<i64> = (0..1500).filter(|&m| pl.first_at(m)).collect();
    assert_eq!(first, vec![407, 907]);
    // hv «по факту» в отрезке [a, b]
    assert!(!pl.hv_any(0, 406));
    assert!(pl.hv_any(0, 407));
    assert!(pl.hv_any(471, 471));
    assert!(!pl.hv_any(472, 906));
    assert!(pl.hv_any(472, 907));
    assert!(!pl.hv_any(1172, 1499));
    assert!(pl.hv_any(300, 1000));
    // вне сетки и пустой/перевёрнутый отрезок
    assert!(!pl.hv_any(-100, -1));
    assert!(!pl.hv_any(1499, 5000) && !pl.hv_any(2000, 3000));
    assert!(!pl.hv_any(500, 400));
    assert!(!pl.hv_at(-5) && !pl.hv_at(1500));
    // откуп не проверяется — ok всегда истина; NATR без hv — NaN
    assert!(pl.ok_at(500, 1) && pl.ok_at(500, -1));
    let only_rev = PlayCfg {
        hv: None,
        no_reversal: true,
        first_phase: false,
    };
    let q = Play::build(&c, &cf, only_rev, 360, [0.2, 0.2]);
    assert!(q.natr_at(407).is_nan());
    assert!(
        q.hv_at(5) && q.first_at(5) && q.hv_any(0, 0),
        "без hv условия нет"
    );
}

#[test]
fn gate_order_is_volatility_then_reversal_then_first_phase() {
    // V-путь, один всплеск в минуте 600 (hv [600, 629]); обвал на минутах 400…409, окно откупа [m − 360, m]
    let cf = v_path();
    let c = with_w(cf.clone(), bursts(&[(600, 1)]));
    let cfg = PlayCfg {
        hv: Some(HvCfg {
            natr_pct: 1.0,
            n: 30,
        }),
        no_reversal: true,
        first_phase: true,
    };
    let pl = Play::build(&c, &cf, cfg, 360, [0.2, 0.2]);
    assert_eq!(
        pl.gate(599, 1),
        Gate::Hv,
        "hv ложь раньше откупа (откуп тоже ложь)"
    );
    assert_eq!(pl.gate(600, 1), Gate::Reversal, "лонг после обвала");
    assert_eq!(
        pl.gate(600, -1),
        Gate::Keep,
        "шорт: hv, откупа нет, минута перехода"
    );
    assert_eq!(
        pl.gate(605, -1),
        Gate::FirstPhase,
        "hv уже идёт — не первая фаза"
    );
    assert_eq!(pl.gate(605, 1), Gate::Reversal, "откуп раньше первой фазы");
    assert_eq!(pl.gate(600, 0), Gate::Keep, "знак 0 откупом не режется");
    assert_eq!(pl.gate(1000, 1), Gate::Hv);
    assert_eq!(pl.gate(-5, 1), Gate::Hv, "вне сетки hv ложь");
    assert!(pl.moment_ok(600, -1) && !pl.moment_ok(605, -1));
    // без флага первой фазы шорт в 605 допустим
    let no_first = PlayCfg {
        first_phase: false,
        ..cfg
    };
    let q = Play::build(&c, &cf, no_first, 360, [0.2, 0.2]);
    assert_eq!(q.gate(605, -1), Gate::Keep);
    // ok_at: данные откупа — те же маски; знак 0 и вне сетки — истина
    assert!(!pl.ok_at(700, 1) && pl.ok_at(700, -1) && pl.ok_at(700, 0) && pl.ok_at(-3, 1));
}

#[test]
fn play_masks_do_not_peek_into_the_future() {
    let w = |m: usize| {
        if (600..1200).contains(&m) {
            0.02
        } else {
            QUIET
        }
    };
    let cf = v_path();
    let base = with_w(cf.clone(), w);
    // после минуты 1000 данные другие: цена, high, low
    let cut = 1000usize;
    let mut late = base.clone();
    tamper(&mut late, cut, &mut Lcg(5), 0.4);
    let cfg = PlayCfg {
        hv: Some(HvCfg {
            natr_pct: 1.0,
            n: 30,
        }),
        no_reversal: true,
        first_phase: true,
    };
    let a = Play::build(&base, &base.close_ff(), cfg, 360, [0.2, 0.2]);
    let b = Play::build(&late, &late.close_ff(), cfg, 360, [0.2, 0.2]);
    for m in 0..cut as i64 {
        assert!(same_f32(a.natr_at(m), b.natr_at(m)), "natr, m = {m}");
        assert_eq!(a.hv_at(m), b.hv_at(m), "hv, m = {m}");
        assert_eq!(a.first_at(m), b.first_at(m), "first, m = {m}");
        for s in [1i8, -1] {
            assert_eq!(a.ok_at(m, s), b.ok_at(m, s), "ok, m = {m}, знак {s}");
            assert_eq!(a.gate(m, s), b.gate(m, s), "gate, m = {m}, знак {s}");
        }
    }
    assert!(
        (cut as i64..1500).any(|m| !same_f32(a.natr_at(m), b.natr_at(m))),
        "правка данных после cut должна менять поздние значения"
    );
}

// ---------- флаги ----------

#[derive(Parser)]
struct W {
    #[command(flatten)]
    a: PlayArgs,
}

#[test]
fn flags_parse_and_validate() {
    let all = W::try_parse_from([
        "t",
        "--hv-natr-pct",
        "1",
        "--hv-n",
        "30",
        "--no-reversal",
        "--hv-first-phase",
    ])
    .unwrap()
    .a;
    let cfg = all.cfg().unwrap().unwrap();
    assert_eq!(
        cfg.hv,
        Some(HvCfg {
            natr_pct: 1.0,
            n: 30
        })
    );
    assert!(cfg.no_reversal && cfg.first_phase);
    // пары и зависимости на уровне разбора аргументов
    assert!(W::try_parse_from(["t", "--hv-natr-pct", "1"]).is_err());
    assert!(W::try_parse_from(["t", "--hv-n", "30"]).is_err());
    assert!(W::try_parse_from(["t", "--hv-first-phase"]).is_err());
    // без флагов — фильтра нет; только откуп — есть, без hv
    assert_eq!(W::try_parse_from(["t"]).unwrap().a.cfg().unwrap(), None);
    let rev = W::try_parse_from(["t", "--no-reversal"]).unwrap().a;
    let cfg = rev.cfg().unwrap().unwrap();
    assert!(cfg.hv.is_none() && cfg.no_reversal && !cfg.first_phase);
    // те же проверки в cfg() (RunArgs собирают и без clap)
    let a = |p: Option<f64>, n: Option<usize>, fp: bool| PlayArgs {
        hv_natr_pct: p,
        hv_n: n,
        no_reversal: false,
        hv_first_phase: fp,
    };
    assert!(a(Some(1.0), None, false).cfg().is_err());
    assert!(a(None, Some(30), false).cfg().is_err());
    assert!(a(None, None, true).cfg().is_err());
    assert!(a(Some(0.0), Some(30), false).cfg().is_err());
    assert!(a(Some(f64::NAN), Some(30), false).cfg().is_err());
    assert!(a(Some(-1.0), Some(30), false).cfg().is_err());
    assert!(a(Some(1.0), Some(0), false).cfg().is_err());
    assert!(a(Some(1.0), Some(30), true).cfg().unwrap().is_some());
}

#[test]
fn reversal_without_d360_in_params_is_an_error_before_any_work() {
    let dir = tempfile::tempdir().unwrap();
    let pj = dir.path().join("params.json");
    fs::write(&pj, params_json("2019-12", "2020-01", "[90]")).unwrap();
    let e = run(RunArgs {
        cache: dir.path().join("нет-такого-каталога"),
        params: pj,
        out: dir.path().join("out"),
        stage: Stage::Test,
        rules: None,
        seed: SEED,
        oi_lag: 1,
        threads: 1,
        xd: None,
        limit: None,
        exits: false,
        dump_triggers: false,
        play: PlayArgs {
            no_reversal: true,
            ..Default::default()
        },
    })
    .unwrap_err();
    assert!(format!("{e:#}").contains("360"), "{e:#}");
}

#[test]
fn old_cache_file_gives_a_clear_error_in_run() {
    let dir = tempfile::tempdir().unwrap();
    let cache = dir.path().join("cache");
    fs::create_dir_all(&cache).unwrap();
    let mut old = b"PMC1".to_vec();
    old.extend(vec![0u8; 36]);
    fs::write(cache.join("ABCUSDT.pmc"), old).unwrap();
    let pj = dir.path().join("params.json");
    fs::write(&pj, params_json("2019-12", "2020-01", "[90]")).unwrap();
    let e = run(RunArgs {
        cache,
        params: pj,
        out: dir.path().join("out"),
        stage: Stage::Test,
        rules: Some(rules_file(dir.path(), &[(0, 0, 0)])),
        seed: SEED,
        oi_lag: 1,
        threads: 1,
        xd: Some(super::run_tests::xd_file(dir.path(), XD)),
        limit: None,
        exits: false,
        dump_triggers: false,
        play: PlayArgs::default(),
    })
    .unwrap_err();
    let s = format!("{e:#}");
    assert!(
        s.contains("старого формата") && s.contains("пересоберите"),
        "{s}"
    );
}

// ---------- прогоны ----------

struct Opt {
    ds: &'static str,
    xs: Vec<f64>,
    rules: Vec<(u8, u8, u8)>,
    threads: usize,
    dump: bool,
    exits: bool,
}

impl Opt {
    /// D = [90], порог хода из `run_tests`, правила F1 (срабатывает за 55 минут до пампа) и F3 (в минуту пампа).
    fn base() -> Opt {
        Opt {
            ds: "[90]",
            xs: vec![XD],
            rules: vec![(0, 0, 0), (1, 0, 0)],
            threads: 2,
            dump: true,
            exits: false,
        }
    }

    /// D = [90, 360] и порог `X_360` (откуп).
    fn with_360(x360: f64) -> Opt {
        Opt {
            ds: "[90,360]",
            xs: vec![XD, x360],
            ..Opt::base()
        }
    }
}

fn caches(dir: &Path, items: &[(&str, &Contract)]) -> PathBuf {
    let cache = dir.join("cache");
    fs::create_dir_all(&cache).unwrap();
    for (sym, c) in items {
        put(&cache, sym, c);
    }
    cache
}

fn xd_multi(dir: &Path, xs: &[f64]) -> PathBuf {
    let mut s = String::from("di,sign,x_d,n_windows\n");
    for (di, x) in xs.iter().enumerate() {
        s += &format!("{di},1,{x},100\n{di},-1,{x},100\n");
    }
    let p = dir.join("xd-play.csv");
    fs::write(&p, s).unwrap();
    p
}

fn go(dir: &Path, tag: &str, cache: &Path, play: PlayArgs, o: &Opt) -> PathBuf {
    let out = dir.join(format!("out-{tag}"));
    let pj = dir.join(format!("params-{tag}.json"));
    fs::write(&pj, params_json("2019-12", "2020-01", o.ds)).unwrap();
    run(RunArgs {
        cache: cache.to_path_buf(),
        params: pj,
        out: out.clone(),
        stage: Stage::Test,
        rules: Some(rules_file(dir, &o.rules)),
        seed: SEED,
        oi_lag: 1,
        threads: o.threads,
        xd: Some(xd_multi(dir, &o.xs)),
        limit: None,
        exits: o.exits,
        dump_triggers: o.dump,
        play,
    })
    .unwrap();
    out
}

fn hv_args(first_phase: bool, no_reversal: bool) -> PlayArgs {
    PlayArgs {
        hv_natr_pct: Some(1.0),
        hv_n: Some(30),
        no_reversal,
        hv_first_phase: first_phase,
    }
}

fn trig(out: &Path) -> Vec<Row> {
    table(&out.join("triggers-test.csv"))
}

fn jmeta(out: &Path) -> serde_json::Value {
    serde_json::from_str(&fs::read_to_string(out.join("run-meta.json")).unwrap()).unwrap()
}

fn one(dir: &Path, tag: &str, c: &Contract, play: PlayArgs, o: &Opt) -> PathBuf {
    let cache = caches(dir, &[("ABCUSDT", c)]);
    go(dir, tag, &cache, play, o)
}

#[test]
fn trigger_in_the_first_minute_of_a_burst_is_kept_and_reported() {
    // один всплеск в минуте 15000 (= T0): hv [15000, 15029]; F1 срабатывает в 14945 (hv нет), F3 — в 15000 (первая минута hv)
    let dir = tempfile::tempdir().unwrap();
    let c = pump_hl(&[(15_000, 1)]);
    let out = one(dir.path(), "a", &c, hv_args(true, false), &Opt::base());
    let rows = trig(&out);
    assert_eq!(rows.len(), 1, "{rows:?}");
    let r = &rows[0];
    assert_eq!(
        (r["fam"].as_str(), r["t_f"].as_str(), r["sign"].as_str()),
        ("1", "15000", "1")
    );
    // колонки выборки: NATR_30 в t_f, откуп не проверялся — 1, первая фаза — 1
    approx(num(r, "natr_pct"), (40.0 + 29.0 * 0.02) / 30.0, 1.0e-2);
    assert_eq!(
        (
            r["ok_long"].as_str(),
            r["ok_short"].as_str(),
            r["first_phase"].as_str()
        ),
        ("1", "1", "1")
    );
    let m = jmeta(&out);
    let sf = &m["sample_filter"];
    assert_eq!(sf["hv_natr_pct"], 1.0);
    assert_eq!(sf["hv_n"], 30);
    assert_eq!(sf["no_reversal"], false);
    assert_eq!(sf["first_phase"], true);
    assert_eq!(sf["contracts_eligible"], 1);
    assert_eq!(sf["contracts_with_hv"], 1);
    // минут во вселенной: сутки 1…11 (первые без предшественника не входят), с hv — 30
    approx(
        sf["hv_minutes_share"].as_f64().unwrap(),
        30.0 / 15_840.0,
        1.0e-12,
    );
    assert_eq!(sf["episodes_before_filter"], 1);
    assert_eq!(sf["episodes_dropped_hv"], 0);
    assert_eq!(sf["episodes_dropped_reversal"], 0);
    assert_eq!(sf["triggers_before"], 2);
    assert_eq!(sf["triggers_dropped_hv"], 1);
    assert_eq!(sf["triggers_dropped_reversal"], 0);
    assert_eq!(sf["triggers_dropped_first_phase"], 0);
    // состав монет
    let coins = table(&out.join("sample-coins-test.csv"));
    assert_eq!(coins.len(), 1);
    assert_eq!(
        (
            coins[0]["sym"].as_str(),
            coins[0]["minutes_in_universe"].as_str(),
            coins[0]["minutes_hv"].as_str(),
            coins[0]["triggers_kept"].as_str(),
            coins[0]["episodes_kept"].as_str()
        ),
        ("ABCUSDT", "15840", "30", "1", "1")
    );
    assert_eq!(table(&out.join("episodes-test.csv")).len(), 1);
}

#[test]
fn first_phase_drops_a_trigger_inside_a_running_burst() {
    // всплеск на минутах [14995, 15025): hv идёт уже 5 минут к срабатыванию F3 в 15000 — это не первая фаза
    let dir = tempfile::tempdir().unwrap();
    let c = pump_hl(&[(14_995, 30)]);
    let cache = caches(dir.path(), &[("ABCUSDT", &c)]);
    let out = go(dir.path(), "fp", &cache, hv_args(true, false), &Opt::base());
    assert!(trig(&out).is_empty());
    let m = jmeta(&out);
    let sf = &m["sample_filter"];
    assert_eq!(sf["triggers_before"], 2);
    assert_eq!(sf["triggers_dropped_hv"], 1, "F1 в 14945: hv ещё нет");
    assert_eq!(sf["triggers_dropped_first_phase"], 1, "F3 в 15000");
    assert_eq!(sf["episodes_before_filter"], 1);
    assert_eq!(sf["episodes_dropped_hv"], 0, "hv по факту в [15000, 15090]");
    // без флага первой фазы F3 остаётся, а колонка first_phase честно показывает 0
    let out2 = go(
        dir.path(),
        "nofp",
        &cache,
        hv_args(false, false),
        &Opt::base(),
    );
    let rows = trig(&out2);
    assert_eq!(rows.len(), 1);
    assert_eq!(
        (
            rows[0]["fam"].as_str(),
            rows[0]["t_f"].as_str(),
            rows[0]["first_phase"].as_str()
        ),
        ("1", "15000", "0")
    );
    assert_eq!(
        jmeta(&out2)["sample_filter"]["triggers_dropped_first_phase"],
        0
    );
}

#[test]
fn episode_without_volatility_is_dropped_and_in_the_middle_is_kept() {
    let dir = tempfile::tempdir().unwrap();
    // нигде нет всплеска: эпизод [15000, 15090] и оба срабатывания отсеяны
    let calm = pump_hl(&[]);
    let cache = caches(dir.path(), &[("ABCUSDT", &calm)]);
    let out = go(
        dir.path(),
        "calm",
        &cache,
        hv_args(false, false),
        &Opt::base(),
    );
    assert!(table(&out.join("episodes-test.csv")).is_empty());
    assert!(trig(&out).is_empty());
    let m = jmeta(&out);
    let sf = &m["sample_filter"];
    assert_eq!(sf["episodes_before_filter"], 1);
    assert_eq!(sf["episodes_dropped_hv"], 1);
    assert_eq!(sf["triggers_before"], 2);
    assert_eq!(sf["triggers_dropped_hv"], 2);
    assert_eq!(sf["contracts_with_hv"], 0);
    approx(sf["hv_minutes_share"].as_f64().unwrap(), 0.0, 0.0);
    let coins = table(&out.join("sample-coins-test.csv"));
    assert_eq!(
        (
            coins[0]["minutes_hv"].as_str(),
            coins[0]["triggers_kept"].as_str(),
            coins[0]["episodes_kept"].as_str()
        ),
        ("0", "0", "0")
    );

    // всплеск ровно в середине эпизода: эпизод сохранён (hv по факту), срабатывания в 14945 и 15000 — нет (hv по состоянию в t_f)
    let mid = pump_hl(&[(15_040, 1)]);
    let dir2 = tempfile::tempdir().unwrap();
    let cache2 = caches(dir2.path(), &[("ABCUSDT", &mid)]);
    let out2 = go(
        dir2.path(),
        "mid",
        &cache2,
        hv_args(false, false),
        &Opt::base(),
    );
    let eps = table(&out2.join("episodes-test.csv"));
    assert_eq!(eps.len(), 1);
    assert_eq!(eps[0]["t0"], "15000");
    assert!(trig(&out2).is_empty());
    let m2 = jmeta(&out2);
    let sf2 = &m2["sample_filter"];
    assert_eq!(sf2["episodes_dropped_hv"], 0);
    assert_eq!(sf2["triggers_dropped_hv"], 2);
    // эпизод до всплеска (всплеск после конца эпизода) отсеян: hv по факту только внутри [t0, t_end]
    let after = pump_hl(&[(15_200, 1)]);
    let dir3 = tempfile::tempdir().unwrap();
    let cache3 = caches(dir3.path(), &[("ABCUSDT", &after)]);
    let out3 = go(
        dir3.path(),
        "after",
        &cache3,
        hv_args(false, false),
        &Opt::base(),
    );
    assert!(table(&out3.join("episodes-test.csv")).is_empty());
    assert_eq!(jmeta(&out3)["sample_filter"]["episodes_dropped_hv"], 1);
}

#[test]
fn long_after_a_crash_is_excluded_and_a_later_one_is_kept() {
    // обвал −25 % за 10 минут от минуты 14600; F1 срабатывает в 14945 (6 часов ещё не прошло), F3 — в 15000 (окно [14640, 15000] чистое)
    let dir = tempfile::tempdir().unwrap();
    let c = crashed(pump_contract(17_280, &[T0]), 14_600, 0.25);
    let cache = caches(dir.path(), &[("ABCUSDT", &c)]);
    let rev = PlayArgs {
        no_reversal: true,
        ..Default::default()
    };
    let o = Opt::with_360(0.2);
    let out = go(dir.path(), "rev", &cache, rev, &o);
    let rows = trig(&out);
    assert_eq!(rows.len(), 1, "{rows:?}");
    assert_eq!(
        (
            rows[0]["fam"].as_str(),
            rows[0]["t_f"].as_str(),
            rows[0]["sign"].as_str()
        ),
        ("1", "15000", "1")
    );
    assert!(
        rows[0]["natr_pct"].parse::<f64>().unwrap().is_nan(),
        "hv не задан — NaN"
    );
    assert_eq!(
        (
            rows[0]["ok_long"].as_str(),
            rows[0]["ok_short"].as_str(),
            rows[0]["first_phase"].as_str()
        ),
        ("1", "1", "1")
    );
    let m = jmeta(&out);
    let sf = &m["sample_filter"];
    assert_eq!(sf["triggers_before"], 2);
    assert_eq!(sf["triggers_dropped_reversal"], 1);
    assert_eq!(sf["triggers_dropped_hv"], 0);
    assert!(sf["hv_natr_pct"].is_null() && sf["contracts_with_hv"].is_null());
    assert_eq!(sf["reversal_d_min"], 360);
    approx(sf["x360_down"].as_f64().unwrap(), 0.2, 1.0e-12);
    // контроль: без флагов оба срабатывания на месте
    let out0 = go(dir.path(), "norev", &cache, PlayArgs::default(), &o);
    assert_eq!(trig(&out0).len(), 2);
    assert!(jmeta(&out0).get("sample_filter").is_none());
    assert!(!out0.join("sample-coins-test.csv").exists());
}

// ---------- без флагов и при нейтральных флагах ----------

fn tables_equal(a: &Path, b: &Path, names: &[&str]) {
    for n in names {
        let (x, y) = (fs::read(a.join(n)).unwrap(), fs::read(b.join(n)).unwrap());
        assert!(x == y, "{n}: файлы различаются");
    }
}

const TABLES: [&str; 8] = [
    "daycells-test.csv",
    "lags-test.csv",
    "first-test.csv",
    "progress-test.csv",
    "episodes-test.csv",
    "xd.csv",
    "exits-test.csv",
    "exits-grid.csv",
];

/// Набор контрактов прогонов: пампы со всплесками, один с обвалом и один тихий.
fn mixed() -> Vec<(&'static str, Contract)> {
    let crash = lit(
        crashed(pump_contract(17_280, &[T0]), 14_600, 0.25),
        bursts(&[(15_000, 1)]),
    );
    vec![
        ("AAAUSDT", pump_hl(&[(15_000, 1)])),
        ("BBBUSDT", pump_hl(&[(14_900, 130)])),
        ("CCCUSDT", pump_hl(&[])),
        ("DDDUSDT", crash),
    ]
}

fn cache_of(dir: &Path, items: &[(&'static str, Contract)]) -> PathBuf {
    let refs: Vec<(&str, &Contract)> = items.iter().map(|(s, c)| (*s, c)).collect();
    caches(dir, &refs)
}

#[test]
fn neutral_flags_leave_every_table_byte_for_byte() {
    // hv-порог 1e-9 (везде, где NATR определён), откуп с X_360 = 10 (никогда) — выборка та же, что без флагов
    let dir = tempfile::tempdir().unwrap();
    let cache = cache_of(dir.path(), &mixed());
    let mut o = Opt::with_360(10.0);
    o.exits = true;
    let a = go(dir.path(), "plain", &cache, PlayArgs::default(), &o);
    let neutral = PlayArgs {
        hv_natr_pct: Some(1.0e-9),
        hv_n: Some(30),
        no_reversal: true,
        hv_first_phase: false,
    };
    let b = go(dir.path(), "neutral", &cache, neutral, &o);
    tables_equal(&a, &b, &TABLES);
    // без флагов — прежняя схема выгрузки и ни блока в meta, ни файла состава
    let ta = fs::read_to_string(a.join("triggers-test.csv")).unwrap();
    assert_eq!(ta.lines().next().unwrap(), HEADER);
    assert!(jmeta(&a).get("sample_filter").is_none());
    assert!(!a.join("sample-coins-test.csv").exists());
    // с флагами строки те же, к концу добавлены четыре столбца
    let tb = fs::read_to_string(b.join("triggers-test.csv")).unwrap();
    assert_eq!(tb.lines().next().unwrap(), format!("{HEADER},{PLAY_COLS}"));
    let cut = |l: &str| l.rsplitn(5, ',').last().unwrap().to_string();
    let la: Vec<&str> = ta.lines().collect();
    let lb: Vec<String> = tb.lines().skip(1).map(cut).collect();
    assert_eq!(
        la[1..].to_vec(),
        lb.iter().map(String::as_str).collect::<Vec<_>>()
    );
    assert!(la.len() > 3, "выгрузка не пуста: {} строк", la.len());
}

#[test]
fn filtered_run_is_the_same_for_one_and_four_threads() {
    let dir = tempfile::tempdir().unwrap();
    let cache = cache_of(dir.path(), &mixed());
    let mut o = Opt::with_360(0.2);
    o.exits = true;
    let play = || hv_args(false, true);
    o.threads = 1;
    let a = go(dir.path(), "t1", &cache, play(), &o);
    o.threads = 4;
    let b = go(dir.path(), "t4", &cache, play(), &o);
    let mut names: Vec<&str> = TABLES.to_vec();
    names.push("triggers-test.csv");
    names.push("sample-coins-test.csv");
    tables_equal(&a, &b, &names);
    assert_eq!(jmeta(&a)["sample_filter"], jmeta(&b)["sample_filter"]);
    assert!(trig(&a).len() >= 3, "непустая выборка: {}", trig(&a).len());
}

#[test]
fn dump_exits_and_daycells_see_the_same_triggers() {
    let dir = tempfile::tempdir().unwrap();
    let cache = cache_of(dir.path(), &mixed());
    let mut o = Opt::with_360(0.2);
    o.exits = true;
    let out = go(dir.path(), "same", &cache, hv_args(false, true), &o);
    let rows = trig(&out);
    // AAA: F3; BBB: F1 и F3; DDD: F3 (F1 в 14945 вне hv, обвал для F3 уже вне окна); CCC: ничего
    let mut got: Vec<(String, String, String)> = rows
        .iter()
        .map(|r| (r["sym"].clone(), r["fam"].clone(), r["t_f"].clone()))
        .collect();
    got.sort();
    let want: Vec<(String, String, String)> = [
        ("AAAUSDT", "1", "15000"),
        ("BBBUSDT", "0", "14945"),
        ("BBBUSDT", "1", "15000"),
        ("DDDUSDT", "1", "15000"),
    ]
    .iter()
    .map(|&(a, b, c)| (a.to_string(), b.to_string(), c.to_string()))
    .collect();
    assert_eq!(got, want);
    // Σ n в daycells по (правило, сторона) на H = 90 (hi = 0) равна числу строк выгрузки, метки определены везде
    let dc = table(&out.join("daycells-test.csv"));
    for fam in ["0", "1"] {
        let n_rows = rows.iter().filter(|r| r["fam"] == fam).count();
        let n_dc: f64 = dc
            .iter()
            .filter(|r| r["fam"] == fam && r["sign"] == "1" && r["hi"] == "0")
            .map(|r| num(r, "n"))
            .sum();
        assert_eq!(n_rows as f64, n_dc, "fam {fam}");
    }
    // выходы П-35 — те же срабатывания (по одной сделке на срабатывание с направлением)
    let m = jmeta(&out);
    assert_eq!(
        m["exits"]["triggers"].as_u64().unwrap() as usize,
        rows.len()
    );
    assert_eq!(m["triggers"].as_u64().unwrap() as usize, rows.len());
    let sf = &m["sample_filter"];
    assert_eq!(sf["contracts_eligible"], 4);
    assert_eq!(sf["contracts_with_hv"], 3);
    let coins = table(&out.join("sample-coins-test.csv"));
    let kept: Vec<(String, String)> = coins
        .iter()
        .map(|r| (r["sym"].clone(), r["triggers_kept"].clone()))
        .collect();
    assert_eq!(
        kept,
        vec![
            ("AAAUSDT".to_string(), "1".to_string()),
            ("BBBUSDT".to_string(), "2".to_string()),
            ("CCCUSDT".to_string(), "0".to_string()),
            ("DDDUSDT".to_string(), "1".to_string()),
        ]
    );
}

#[test]
fn changing_data_after_the_day_changes_nothing_before_it() {
    let dir = tempfile::tempdir().unwrap();
    let base = pump_hl(&[(14_900, 130)]);
    // правка с начала суток 11 (минута 15840): цена, high/low, оборот, число сделок, OI
    let cut = 11 * 1440;
    let mut late = base.clone();
    let mut rng = Lcg(77);
    tamper(&mut late, cut, &mut rng, 0.3);
    for (q, t) in late.qvol.iter_mut().zip(late.trades.iter_mut()).skip(cut) {
        *q = (1.0e4 * rng.next()) as f32;
        *t = (100.0 * rng.next()) as f32;
    }
    for v in late.oi[0].iter_mut().skip(cut / 5) {
        *v *= 1.0 + rng.next();
    }
    let ca = caches(dir.path(), &[("ABCUSDT", &base)]);
    let dir_b = tempfile::tempdir().unwrap();
    let cb = caches(dir_b.path(), &[("ABCUSDT", &late)]);
    let o = Opt::with_360(0.2);
    let a = go(dir.path(), "ua", &ca, hv_args(false, true), &o);
    let b = go(dir_b.path(), "ub", &cb, hv_args(false, true), &o);
    let key = |r: &Row| {
        [
            "sym",
            "fam",
            "wi",
            "ti",
            "sign",
            "t_f",
            "natr_pct",
            "ok_long",
            "ok_short",
            "first_phase",
        ]
        .iter()
        .map(|k| r[*k].clone())
        .collect::<Vec<_>>()
    };
    let before = |rows: Vec<Row>| -> Vec<Vec<String>> {
        let mut v: Vec<Vec<String>> = rows
            .iter()
            .filter(|r| r["t_f"].parse::<i64>().unwrap() < cut as i64)
            .map(key)
            .collect();
        v.sort();
        v
    };
    let (ra, rb) = (before(trig(&a)), before(trig(&b)));
    assert_eq!(ra.len(), 2, "оба срабатывания до правки: {ra:?}");
    assert_eq!(ra, rb);
    // и сам состав эпизода до правки не меняется
    let ea: Vec<Row> = table(&a.join("episodes-test.csv"))
        .into_iter()
        .filter(|r| r["t_end"].parse::<i64>().unwrap() < cut as i64)
        .collect();
    let eb: Vec<Row> = table(&b.join("episodes-test.csv"))
        .into_iter()
        .filter(|r| r["t_end"].parse::<i64>().unwrap() < cut as i64)
        .collect();
    assert_eq!(ea, eb);
    assert!(!ea.is_empty());
}

// ---------- контроли из «игровой» популяции ----------

fn test_params(ds: &str) -> Params {
    Params::from_json(&params_json("2019-12", "2020-01", ds)).unwrap()
}

#[test]
fn c2a_moments_are_drawn_from_game_minutes_only() {
    let p = test_params("[90]");
    // всплески: далеко от пампа [5000, 5100) и в минуту пампа → «игровых» слотов хватает на выбор
    let c = pump_hl(&[(5_000, 100), (15_000, 1)]);
    let cf = c.close_ff();
    let play = Play::build(&c, &cf, cfg_hv(1.0, 30), 360, [NAN; 2]);
    let cube = feat::build_cube(&c, &p, 1);
    let eps = [Episode {
        di: 0,
        sign: 1,
        t0: T0 as i64,
        t_end: T0 as i64 + 90,
        mv: 0.0909,
        flags: 0,
    }];
    let xd = XdTable { x: vec![[XD, XD]] };
    let rules = [Rule {
        fam: 1,
        wi: 0,
        ti: 0,
    }];
    let keep = |t: i64| (1440..17_280).contains(&t);
    let collect = |cube: &super::types::Cube| {
        let inp = control::Input {
            sym: "ABCUSDT",
            cube,
            cf: &cf,
            first_min: 0,
            eps: &eps,
        };
        let mut out: Vec<(i64, Vec<u32>)> = Vec::new();
        control::c2a_moments_with(
            &inp,
            &rules,
            &xd,
            &p,
            SEED,
            true,
            |r| feat::first_triggers(cube, r, &p),
            |row, moms| out.push((row.t_f, moms.to_vec())),
        )
        .unwrap();
        out
    };
    let mut masked = cube.clone();
    mask_r_with(&mut masked, &keep, Some(&play));
    let mut plain = cube.clone();
    mask_r_with(&mut plain, &keep, None);
    let (m_rows, p_rows) = (collect(&masked), collect(&plain));
    assert_eq!(m_rows.len(), 1);
    assert_eq!(p_rows.len(), 1);
    let game = |cube: &super::types::Cube, j: u32| {
        let j = j as usize;
        let t = slot_t(cube, j);
        let s = sign_of(cube.rat(0, j));
        s != 0 && play.moment_ok(t, s)
    };
    let moms = &m_rows[0].1;
    assert!(!moms.is_empty(), "в корзине как минимум само срабатывание");
    assert!(
        moms.iter().all(|&j| game(&masked, j)),
        "моменты C2a вне «игровых» минут: {moms:?}"
    );
    // контроль теста: без выборки среди тех же 10 моментов есть не-игровые
    assert!(
        p_rows[0].1.iter().any(|&j| !game(&plain, j)),
        "контроль: без маски должны попадаться негодные моменты"
    );
}

#[test]
fn c2b_candidates_are_restricted_before_the_draw() {
    // случайное блуждание 30 суток (только январь), «тихая» зона [12000, 18000) без hv, остальное — hv
    let n = 30 * 1440;
    let mut rng = Lcg(3);
    let mut x = 0.0f64;
    let close: Vec<f64> = (0..n)
        .map(|_| {
            x += (rng.next() - 0.5) * 0.004;
            100.0 * x.exp()
        })
        .collect();
    let c = with_w(close, |m| {
        if (12_000..18_000).contains(&m) {
            QUIET
        } else {
            0.02
        }
    });
    let mut c = c;
    c.vol = vec![1.0; n];
    c.qvol = vec![10.0; n];
    c.trades = vec![5.0; n];
    let p = test_params("[90]");
    let cube = feat::build_cube(&c, &p, 1);
    let cf = c.close_ff();
    let play = Play::build(&c, &cf, cfg_hv(1.0, 30), 360, [NAN; 2]);
    // десять эпизодов-шортов с разносом больше окна исключения (1800 мин)
    let eps: Vec<Episode> = (0..10)
        .map(|k| Episode {
            di: 0,
            sign: -1,
            t0: 3000 + 4000 * k,
            t_end: 3090 + 4000 * k,
            mv: 0.1,
            flags: 0,
        })
        .collect();
    let inp = control::Input {
        sym: "WALKUSDT",
        cube: &cube,
        cf: &cf,
        first_min: 0,
        eps: &eps,
    };
    let rule = [Rule {
        fam: 0,
        wi: 0,
        ti: 0,
    }];
    let l_post = 90;
    let mut all = Vec::new();
    control::c2b_with(&inp, &rule, &p, l_post, SEED, |_, _| true, |r| all.push(r)).unwrap();
    // нейтральное условие даёт прежние строки без изменений
    let mut neutral = Vec::new();
    control::c2b_with_cand(
        &inp,
        &rule,
        &p,
        l_post,
        SEED,
        |_, _| true,
        |_, _| true,
        |r| neutral.push(r),
    )
    .unwrap();
    assert_eq!(all, neutral);
    assert!(
        all.len() >= 30,
        "пул не пуст: {} псевдо-моментов",
        all.len()
    );
    // условие «игровой» минуты для знака эпизода
    let mut cand_rows = Vec::new();
    control::c2b_with_cand(
        &inp,
        &rule,
        &p,
        l_post,
        SEED,
        |j, sign| play.moment_ok(slot_t(&cube, j), sign),
        |_, _| true,
        |r| cand_rows.push(r),
    )
    .unwrap();
    assert!(!cand_rows.is_empty());
    for r in &cand_rows {
        let sign = eps[r.episode_idx as usize].sign;
        assert!(
            play.moment_ok(r.t_pseudo, sign),
            "псевдо-момент {} вне «игровых» минут",
            r.t_pseudo
        );
    }
    // контроль: без условия среди псевдо-моментов есть минуты тихой зоны
    assert!(
        all.iter().any(|r| !play.moment_ok(r.t_pseudo, -1)),
        "контроль: без условия должны попадаться негодные моменты"
    );
    // условие, не пропускающее ничего, даёт пустой контроль
    let mut none = Vec::new();
    control::c2b_with_cand(
        &inp,
        &rule,
        &p,
        l_post,
        SEED,
        |_, _| false,
        |_, _| true,
        |r| none.push(r),
    )
    .unwrap();
    assert!(none.is_empty());
}
