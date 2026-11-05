//! Тесты событий воспламенения (`ignitions.rs`): ручные NATR-ряды (событие только после `L_quiet` минут тишины, второе
//! воспламенение внутри `hv`-отрезка — не событие, повторное после тишины — событие), округление `t_f` вверх до 5,
//! решение по данным `≤ m` и признаки по данным `≤ t_f` (утечка), вселенная по обороту `d − 1`, период стадии, столбцы
//! против выгрузки срабатываний (одна функция записи), детерминизм по потокам, `sign = +1` и знаковый `r_w_bps`.

use super::cache::Contract;
use super::control::{yyyymm, TriggerRow};
use super::dump::{
    header_core, ign_header, Collector, IgnTail, Shared, FEATURE_COLS, HEADER, IGN_TAIL_COLS,
    PLAY_COLS,
};
use super::feat;
use super::ignitions::{
    find_events, ignitions, tf_of, Event, IgnitionsArgs, IGN_RULE, PREV_MAX_MIN, QUIET_CAP_MIN,
};
use super::params::Params;
use super::play::{HvCfg, Play, PlayCfg};
use super::run::Stage;
use super::run_tests::{
    approx, num, params_json, pump_contract, put, random_contract, table, xd_file, Row, T0, XD,
};
use super::types::Rule;
use clap::Parser;
use std::fs;
use std::path::{Path, PathBuf};

// ---------- данные ----------

/// Полуразмах свечи в долях цены: тихая минута (`TR` = 0,02 %), средняя (0,5 %, ниже порога 1 %) и всплеск (40 %).
const QUIET: f64 = 1.0e-4;
const MEDIUM: f64 = 0.0025;
const BIG: f64 = 0.2;
/// Окно NATR тестов и `L_quiet` (`onset.L_pre_min` файла параметров тестов).
const N_NATR: usize = 5;
const L_QUIET: i64 = 360;

/// Всплеск `(начало, длина, полуразмах)`.
type Burst = (usize, usize, f64);

fn singles(ms: &[usize]) -> Vec<Burst> {
    ms.iter().map(|&m| (m, 1, BIG)).collect()
}

fn half_range(bursts: &[Burst], m: usize) -> f64 {
    bursts
        .iter()
        .find(|b| (b.0..b.0 + b.1).contains(&m))
        .map_or(QUIET, |b| b.2)
}

/// `base` с `high`/`low` вокруг его `close`: `c·(1 ± w(m))`; `TR` минуты без гэпа — `200·w` процентов.
fn with_hl(base: Contract, bursts: &[Burst]) -> Contract {
    let hl = |sign: f64| -> Vec<f32> {
        base.close
            .iter()
            .enumerate()
            .map(|(m, &x)| (x * (1.0 + sign * half_range(bursts, m))) as f32)
            .collect()
    };
    let (high, low) = (hl(1.0), hl(-1.0));
    Contract { high, low, ..base }
}

/// Плоская цена 100 и всплески: NATR (окно `N_NATR`) читается вручную.
fn spiky(first_min: i64, len: usize, bursts: &[Burst]) -> Contract {
    with_hl(
        Contract {
            first_min,
            close: vec![100.0; len],
            ..Default::default()
        },
        bursts,
    )
}

fn hv_cfg() -> PlayCfg {
    PlayCfg {
        hv: Some(HvCfg {
            natr_pct: 1.0,
            n: N_NATR,
        }),
        no_reversal: false,
        first_phase: false,
        onset: false,
    }
}

fn events_of(c: &Contract) -> Vec<Event> {
    let cf = c.close_ff();
    let pl = Play::build(c, &cf, hv_cfg(), L_QUIET, [f64::NAN; 2]);
    find_events(&pl, c.first_min, c.close.len())
}

fn ms_of(ev: &[Event]) -> Vec<i64> {
    ev.iter().map(|e| e.m).collect()
}

// ---------- события по ручным рядам ----------

#[test]
fn event_needs_quiet_and_repeat_inside_hv_is_not_an_event() {
    // всплеск в минуте s даёт hv_state в [s, s + 4] (окно NATR = 5 свечей)
    let bursts = [
        (500, 1, BIG),
        (514, 1, BIG), // 9 минут после конца hv (504): не событие
        (879, 1, BIG), // 518 + 360 + 1: первое возможное — событие
        (1300, 1, MEDIUM),
        (1500, 1, BIG),  // повтор после тишины — событие
        (2000, 40, BIG), // длинный отрезок: событие только в его начале
        (2060, 1, BIG),  // внутри 360 минут от конца отрезка (2043): не событие
    ];
    let ev = events_of(&spiky(0, 3000, &bursts));
    assert_eq!(ms_of(&ev), vec![500, 879, 1500, 2000]);
    assert_eq!(
        ev.iter().map(|e| e.t_f).collect::<Vec<_>>(),
        vec![500, 880, 1500, 2000]
    );
    // минут тишины перед событием: подряд без hv_state (до первого события — вся сетка до него)
    assert_eq!(
        ev.iter().map(|e| e.quiet_min).collect::<Vec<_>>(),
        vec![500, 360, 616, 495]
    );
    // максимум NATR за 360 минут до события: тихо — 0,02 %; средняя минута 1300 — (4·0,02 + 0,5) / 5 = 0,116 %
    for (e, want) in ev.iter().zip([0.02, 0.02, 0.116, 0.02]) {
        approx(f64::from(e.natr_prev_max), want, 1.0e-3);
    }
}

#[test]
fn quiet_boundary_is_exactly_l_quiet_minutes() {
    // всплеск в 500 → последняя минута hv_state — 504; событие возможно с 504 + 360 + 1 = 865
    let second = |s: usize| ms_of(&events_of(&spiky(0, 1200, &[(500, 1, BIG), (s, 1, BIG)])));
    assert_eq!(second(864), vec![500]);
    assert_eq!(second(865), vec![500, 865]);
}

#[test]
fn t_f_is_m_rounded_up_to_the_five_minute_grid() {
    for m in 0..2000i64 {
        let t = tf_of(m);
        assert_eq!(t, (m + 4) / 5 * 5, "m {m}");
        assert!(t >= m && t - m <= 4 && t % 5 == 0, "m {m}: t_f {t}");
    }
    for (m, want) in [
        (500, 500),
        (501, 505),
        (502, 505),
        (503, 505),
        (504, 505),
        (505, 505),
        (506, 510),
    ] {
        assert_eq!(tf_of(m), want, "m {m}");
    }
    // сетка 5 мин — от GRID0_MS, а не от первой минуты контракта: first_min = 7, индексы 500, 1003, 1498 → m = 507, 1010, 1505
    let ev = events_of(&spiky(
        7,
        3000,
        &[(500, 1, BIG), (1003, 1, BIG), (1498, 1, BIG)],
    ));
    assert_eq!(
        ev.iter().map(|e| (e.m, e.t_f)).collect::<Vec<_>>(),
        vec![(507, 510), (1010, 1010), (1505, 1505)]
    );
}

#[test]
fn event_decision_uses_only_data_up_to_m() {
    let bursts = [(500, 1, BIG), (879, 1, BIG), (1500, 1, BIG)];
    let base = events_of(&spiky(0, 3000, &bursts));
    assert_eq!(ms_of(&base), vec![500, 879, 1500]);
    // всё после минуты 879 заменено другим: события до неё и сама 879 — те же поля
    let mut c = spiky(0, 3000, &bursts);
    for (m, (h, l)) in c
        .high
        .iter_mut()
        .zip(c.low.iter_mut())
        .enumerate()
        .skip(880)
    {
        let w = if m % 7 == 0 { BIG } else { MEDIUM };
        *h = (100.0 * (1.0 + w)) as f32;
        *l = (100.0 * (1.0 - w)) as f32;
    }
    let after = events_of(&c);
    let upto = |v: &[Event]| -> Vec<Event> { v.iter().filter(|e| e.m <= 879).copied().collect() };
    assert_eq!(upto(&base).len(), 2);
    assert_eq!(upto(&base), upto(&after));
    assert_ne!(base, after, "поздняя часть должна была измениться");
}

#[test]
fn constants_are_the_spec_numbers_and_the_pseudo_rule_is_outside_the_families() {
    assert_eq!(QUIET_CAP_MIN, 1440);
    assert_eq!(PREV_MAX_MIN, 360);
    assert_eq!(IGN_RULE.fam, 11);
    assert_eq!((IGN_RULE.wi, IGN_RULE.ti), (0, 0));
    assert_eq!(usize::from(IGN_RULE.fam), feat::FAMILIES.len());
}

// ---------- столбцы ----------

fn rows_from(head: &str, body: &str) -> Vec<Row> {
    let cols: Vec<&str> = head.split(',').collect();
    body.lines()
        .filter(|l| !l.is_empty())
        .map(|l| {
            let f: Vec<&str> = l.split(',').collect();
            assert_eq!(f.len(), cols.len(), "число полей строки: {l}");
            cols.iter()
                .zip(f)
                .map(|(h, v)| ((*h).to_string(), v.to_string()))
                .collect()
        })
        .collect()
}

#[test]
fn header_is_the_dump_header_without_the_base_plus_the_ignition_tail() {
    let core: Vec<&str> = header_core().split(',').collect();
    let dump_cols: Vec<&str> = HEADER.split(',').collect();
    assert_eq!(&dump_cols[..core.len()], &core[..]);
    assert_eq!(dump_cols.len() - core.len(), 3);
    assert!(dump_cols[core.len()..]
        .iter()
        .all(|c| c.starts_with("b_gross_")));
    let h = ign_header();
    let cols: Vec<&str> = h.split(',').collect();
    let n_feat = FEATURE_COLS.split(',').count();
    let n_tail = IGN_TAIL_COLS.split(',').count();
    assert_eq!((n_feat, n_tail), (23, 3));
    assert_eq!(cols.len(), core.len() + 1 + n_feat + n_tail);
    assert_eq!(&cols[..core.len()], &core[..]);
    assert_eq!(cols[core.len()], "natr_pct");
    assert_eq!(
        &cols[core.len() + 1..core.len() + 1 + n_feat],
        &FEATURE_COLS.split(',').collect::<Vec<_>>()[..]
    );
    assert_eq!(
        &cols[cols.len() - n_tail..],
        &["lead_m_since_quiet", "natr_prev_max_6h", "m_minus_tf"]
    );
    assert!(!cols.iter().any(|c| c.starts_with("b_gross_")));
    for must in [
        "sym",
        "fam",
        "wi",
        "ti",
        "sign",
        "t_f",
        "ep_running",
        "mfe_price",
        "gross_1440",
    ] {
        assert!(cols.contains(&must), "нет столбца {must}");
    }
}

#[test]
fn ignition_row_has_the_same_columns_as_the_dump_row_on_the_same_point() {
    let p = Params::from_json(&params_json("2020-01", "2020-02", "[90]")).unwrap();
    let c = with_hl(pump_contract(17_280, &[T0]), &singles(&[3000, 9000]));
    let cf = c.close_ff();
    let cube = feat::build_cube(&c, &p, 1);
    let pl = Play::build(&c, &cf, hv_cfg(), L_QUIET, [f64::NAN; 2]);
    let sh = Shared::default();
    let (t_f, fw) = (9000i64, 1usize);
    let new = || {
        Collector::new(&sh, "AAAUSDT", &c, &cf, &cube)
            .with_play(Some(&pl))
            .with_features(Some(1))
    };

    // срабатывание правила (0, окно fw, 0), сторона +1, `ep_running` = 1
    let mut d = new();
    d.add(
        &TriggerRow {
            rule: Rule {
                fam: 0,
                wi: fw as u8,
                ti: 0,
            },
            t_f,
            month: yyyymm(t_f),
            sign: 1,
            label: Vec::new(),
            c2a_hit: Vec::new(),
            c2a_n: Vec::new(),
            c2a_drawn: 0,
            in_episode_running: true,
        },
        &[],
    );
    let bytes = d.finish();
    let dump_rows = rows_from(
        &format!("{HEADER},{PLAY_COLS},{FEATURE_COLS}"),
        std::str::from_utf8(&bytes).unwrap(),
    );

    // событие в той же точке, окно признаков то же
    let mut g = new();
    g.add_ignition(
        IGN_RULE,
        t_f,
        fw,
        true,
        IgnTail {
            quiet_min: 7,
            natr_prev_max: 0.5,
            m_minus_tf: 2,
        },
    );
    let bytes = g.finish();
    let ign_rows = rows_from(&ign_header(), std::str::from_utf8(&bytes).unwrap());

    assert_eq!((dump_rows.len(), ign_rows.len()), (1, 1));
    let (a, b) = (&dump_rows[0], &ign_rows[0]);
    let same = header_core()
        .split(',')
        .chain(["natr_pct"])
        .chain(FEATURE_COLS.split(','));
    let mut n_same = 0;
    for col in same {
        if ["fam", "wi", "ti"].contains(&col) {
            continue;
        }
        assert_eq!(a[col], b[col], "{col}");
        n_same += 1;
    }
    assert!(n_same > 60, "сверено столбцов: {n_same}");
    // ключ события, хвост и знак
    assert_eq!(
        (b["fam"].as_str(), b["wi"].as_str(), b["ti"].as_str()),
        ("11", "0", "0")
    );
    assert_eq!(b["sign"], "1");
    assert_eq!(b["lead_m_since_quiet"], "7");
    assert_eq!(b["natr_prev_max_6h"], "0.5");
    assert_eq!(b["m_minus_tf"], "2");
    assert_eq!(b["ep_running"], "1");
    // NATR в t_f: всплеск в минуте 9000 — (4·0,02 + 40) / 5 ≈ 8,016 %
    approx(num(b, "natr_pct"), 8.016, 0.2);
}

// ---------- прогон ----------

struct Set {
    cache: PathBuf,
    params: PathBuf,
    xd: PathBuf,
}

/// Каталоги кэша, `params.json` (фит — январь 2020, тест — февраль 2020) и `xd.csv` (`X_D` пампа `run_tests`).
fn setup(dir: &Path, ds: &str) -> Set {
    let cache = dir.join("cache");
    fs::create_dir_all(&cache).unwrap();
    let params = dir.join("params.json");
    fs::write(&params, params_json("2020-01", "2020-02", ds)).unwrap();
    Set {
        cache,
        params,
        xd: xd_file(dir, XD),
    }
}

fn args(set: &Set, out: &Path, stage: Stage, threads: usize, xd: bool) -> IgnitionsArgs {
    IgnitionsArgs {
        cache: set.cache.clone(),
        params: set.params.clone(),
        out: out.to_path_buf(),
        stage,
        hv_natr_pct: 1.0,
        hv_n: N_NATR,
        oi_lag: 1,
        threads,
        seed: None,
        xd: xd.then(|| set.xd.clone()),
        // окно 15 мин (индекс 1 в `features.windows_min` тестов [5, 15])
        sign_window: Some(1),
        limit: None,
    }
}

fn go(dir: &Path, set: &Set, stage: Stage, threads: usize, name: &str) -> PathBuf {
    let out = dir.join(name);
    ignitions(args(set, &out, stage, threads, true)).unwrap();
    out
}

fn meta(out: &Path, stage: &str) -> serde_json::Value {
    let text = fs::read_to_string(out.join(format!("ignitions-meta-{stage}.json"))).unwrap();
    serde_json::from_str(&text).unwrap()
}

fn keys_of(rows: &[Row]) -> Vec<(String, i64)> {
    rows.iter()
        .map(|r| (r["sym"].clone(), r["t_f"].parse().unwrap()))
        .collect()
}

fn keyed(list: &[(&str, i64)]) -> Vec<(String, i64)> {
    list.iter().map(|&(s, t)| (s.to_string(), t)).collect()
}

/// Всплески контрактов набора: AAA — 57 600 минут (40 суток, граница фита 44 640), BBB — 14 суток.
const AAA_MS: [usize; 9] = [
    3000, 3100, 5000, 9000, 44_000, 44_639, 45_100, 57_000, 57_599,
];
const BBB_MS: [usize; 4] = [3000, 9000, 9400, 15_000];

/// AAA и BBB с событиями, левередж-токен и контракт без OI (пропускаются). В AAA сутки 2 (минуты 2880…4319) без оборота:
/// сутки 3 не во вселенной (оборот `d − 1` меньше порога) — всплеск 5000 вне вселенной.
fn big_set(dir: &Path) -> Set {
    let set = setup(dir, "[90]");
    let mut a = pump_contract(57_600, &[T0]);
    for q in a.qvol.iter_mut().take(4320).skip(2880) {
        *q = 0.0;
    }
    put(&set.cache, "AAAUSDT", &with_hl(a, &singles(&AAA_MS)));
    put(
        &set.cache,
        "BBBUSDT",
        &with_hl(random_contract(21, 14), &singles(&BBB_MS)),
    );
    put(
        &set.cache,
        "ETHUPUSDT",
        &with_hl(random_contract(22, 14), &singles(&BBB_MS)),
    );
    let mut no_oi = with_hl(random_contract(23, 14), &singles(&BBB_MS));
    no_oi.oi = Default::default();
    put(&set.cache, "NOOIUSDT", &no_oi);
    set
}

#[test]
fn fit_stage_writes_the_events_of_the_universe_and_counts_the_rest() {
    let dir = tempfile::tempdir().unwrap();
    let set = big_set(dir.path());
    let out = go(dir.path(), &set, Stage::Fit, 2, "fit");
    let path = out.join("ignitions-fit.csv");
    let text = fs::read_to_string(&path).unwrap();
    assert_eq!(text.lines().next().unwrap(), ign_header());
    let rows = table(&path);

    // порядок — контракт по алфавиту, затем t_f; 3100 — не событие (внутри 360 минут), 5000 — вне вселенной, 44639 — нет
    // цены входа c[t_f + 1] (t_f = 44 640 — конец фита), 45100 и далее — тест
    assert_eq!(
        keys_of(&rows),
        keyed(&[
            ("AAAUSDT", 3000),
            ("AAAUSDT", 9000),
            ("AAAUSDT", 44_000),
            ("BBBUSDT", 3000),
            ("BBBUSDT", 9000),
            ("BBBUSDT", 9400),
            ("BBBUSDT", 15_000),
        ])
    );
    for r in &rows {
        let key = (r["fam"].as_str(), r["wi"].as_str(), r["ti"].as_str());
        assert_eq!((key, r["sign"].as_str()), (("11", "0", "0"), "1"));
        assert_eq!(r["m_minus_tf"], "0");
        assert_eq!(r["month"], "202001");
        approx(num(r, "natr_pct"), 8.016, 0.2);
        assert!(
            num(r, "natr_prev_max_6h") < 0.5,
            "{:?}",
            r["natr_prev_max_6h"]
        );
        // минут тишины: 1440 (предел) везде, кроме BBB 9400 — после всплеска 9000 (hv до 9004): 9400 − 9004 − 1
        let want = if r["sym"] == "BBBUSDT" && r["t_f"] == "9400" {
            395.0
        } else {
            1440.0
        };
        approx(num(r, "lead_m_since_quiet"), want, 0.0);
        // исходы: у AAA 44 000 путь 1440 минут за концом фита — mfe NaN, короткие исходы есть; у остальных всё определено
        if r["sym"] == "AAAUSDT" && r["t_f"] == "44000" {
            assert!(num(r, "mfe_bps").is_nan() && num(r, "gross_1440").is_nan());
            assert!(num(r, "gross_5").is_finite() && num(r, "gross_60").is_finite());
        } else {
            assert!(num(r, "mfe_bps").is_finite() && num(r, "gross_1440").is_finite());
        }
        // признаки направления есть (куб построен); дыры OI случайных контрактов уровень не отменяют только у AAA
        assert!(num(r, "r_w_bps").is_finite());
        if r["sym"] == "AAAUSDT" {
            assert!(num(r, "oi_value").is_finite());
        }
    }

    let m = meta(&out, "fit");
    assert_eq!(m["stage"], "fit");
    assert_eq!(m["contracts"], 2);
    assert_eq!(m["contracts_with_events"], 2);
    assert_eq!(m["skipped"]["leverage"], 1);
    assert_eq!(m["skipped"]["no_oi"], 1);
    assert_eq!(m["events_in_stage"], 9);
    assert_eq!(m["events_in_universe"], 8);
    assert_eq!(m["events_out_of_universe"], 1);
    assert_eq!(m["events_dropped_incomplete_path"], 1);
    assert_eq!(m["events"], 7);
    assert_eq!(m["events_kept_path_shorter_1440"], 1);
    assert_eq!(m["events_by_month"], serde_json::json!({"202001": 7}));
    assert_eq!(m["sign"], 1);
    assert_eq!(m["sign_window_index"], 1);
    assert_eq!(m["sign_window_min"], 15);
    assert_eq!(m["rule"], serde_json::json!({"fam": 11, "wi": 0, "ti": 0}));
    assert_eq!(m["xd_source"], "file");
    assert!(m["secs_by_phase"]["cube_episodes"].is_number());
}

#[test]
fn test_stage_takes_events_by_the_minute_m_and_does_not_clip_the_contract() {
    let dir = tempfile::tempdir().unwrap();
    let set = big_set(dir.path());
    let out = go(dir.path(), &set, Stage::Test, 2, "test");
    let rows = table(&out.join("ignitions-test.csv"));
    // 44 639 — последняя минута фита (событие фита), 57 599 — последняя минута данных: цены входа нет
    assert_eq!(
        keys_of(&rows),
        keyed(&[("AAAUSDT", 45_100), ("AAAUSDT", 57_000)])
    );
    // 45 100: путь 1440 минут целиком в данных; 57 000 — нет (mfe NaN, исходы до конца данных есть)
    assert!(num(&rows[0], "mfe_bps").is_finite() && num(&rows[0], "gross_1440").is_finite());
    assert!(num(&rows[1], "mfe_bps").is_nan() && num(&rows[1], "gross_1440").is_nan());
    assert!(num(&rows[1], "gross_60").is_finite());
    let m = meta(&out, "test");
    assert_eq!(m["contracts"], 2);
    assert_eq!(m["contracts_with_events"], 1);
    assert_eq!(m["events_in_stage"], 3);
    assert_eq!(m["events_in_universe"], 3);
    assert_eq!(m["events_out_of_universe"], 0);
    assert_eq!(m["events_dropped_incomplete_path"], 1);
    assert_eq!(m["events"], 2);
    assert_eq!(m["events_kept_path_shorter_1440"], 1);
    assert_eq!(m["events_by_month"], serde_json::json!({"202002": 2}));
}

#[test]
fn output_is_byte_identical_for_any_thread_count_and_x_d_is_computed_from_the_fit() {
    let dir = tempfile::tempdir().unwrap();
    let set = setup(dir.path(), "[90,180]");
    // пять контрактов (в том числе BTCUSDT — эталон `btc_*`), всплески не кратны 5 минутам
    for (i, sym) in ["AAAUSDT", "BBBUSDT", "BTCUSDT", "CCCUSDT", "DDDUSDT"]
        .iter()
        .enumerate()
    {
        let ms = [2003 + 700 * i, 6001 + 311 * i, 12_000 + 97 * i];
        put(
            &set.cache,
            sym,
            &with_hl(random_contract(11 + i as u64, 14), &singles(&ms)),
        );
    }
    put(
        &set.cache,
        "BTCUPUSDT",
        &with_hl(random_contract(99, 14), &singles(&[3000])),
    );
    let mut no_oi = with_hl(random_contract(98, 14), &singles(&[3000]));
    no_oi.oi = Default::default();
    put(&set.cache, "NOOIUSDT", &no_oi);

    let outs: Vec<PathBuf> = [1usize, 4, 3]
        .iter()
        .map(|&th| {
            let out = dir.path().join(format!("out{th}"));
            let mut a = args(&set, &out, Stage::Fit, th, false);
            a.seed = Some(7);
            ignitions(a).unwrap();
            out
        })
        .collect();
    let first = fs::read(outs[0].join("ignitions-fit.csv")).unwrap();
    let rows = table(&outs[0].join("ignitions-fit.csv"));
    assert_eq!(rows.len(), 15, "по три события у пяти контрактов");
    for o in &outs[1..] {
        assert_eq!(
            first,
            fs::read(o.join("ignitions-fit.csv")).unwrap(),
            "потоки 1 против другого"
        );
    }
    // не кратные 5 минуты: t_f = m, округлённое вверх; BTCUSDT — эталон, btc_prev60 определён
    for r in &rows {
        let t_f: i64 = r["t_f"].parse().unwrap();
        let d: i64 = r["m_minus_tf"].parse().unwrap();
        assert!(t_f % 5 == 0 && (0..5).contains(&d), "{t_f} {d}");
        assert!(num(r, "btc_prev60_bps").is_finite());
    }
    assert!(rows.iter().any(|r| r["m_minus_tf"] != "0"));
    let m0 = meta(&outs[0], "fit");
    assert_eq!(m0["xd_source"], "computed");
    assert_eq!(m0["seed"], 7);
    assert_eq!(m0["contracts"], 5);
    assert_eq!(m0["skipped"]["leverage"], 1);
    assert_eq!(m0["skipped"]["no_oi"], 1);
    for o in &outs[1..] {
        let m = meta(o, "fit");
        for key in [
            "contracts",
            "contracts_with_events",
            "events_in_stage",
            "events_in_universe",
            "events_out_of_universe",
            "events_dropped_incomplete_path",
            "events",
            "events_kept_path_shorter_1440",
            "events_by_month",
            "skipped",
        ] {
            assert_eq!(m[key], m0[key], "{key}");
        }
    }
}

#[test]
fn ep_running_marks_events_inside_a_same_sign_episode() {
    let dir = tempfile::tempdir().unwrap();
    let set = setup(dir.path(), "[90]");
    // эпизод пампа T0 = 15 000 … 15 090 (`run_tests`): всплеск 15 030 — внутри, 3000 и 16 000 — вне
    put(
        &set.cache,
        "ABCUSDT",
        &with_hl(
            pump_contract(17_280, &[T0]),
            &singles(&[3000, 15_030, 16_000]),
        ),
    );
    let out = go(dir.path(), &set, Stage::Fit, 1, "o");
    let rows = table(&out.join("ignitions-fit.csv"));
    let got: Vec<(String, String)> = rows
        .iter()
        .map(|r| (r["t_f"].clone(), r["ep_running"].clone()))
        .collect();
    let want: Vec<(String, String)> = [("3000", "0"), ("15030", "1"), ("16000", "0")]
        .iter()
        .map(|&(a, b)| (a.to_string(), b.to_string()))
        .collect();
    assert_eq!(got, want);
}

#[test]
fn sign_is_plus_one_and_the_move_direction_is_in_r_w_and_prior_returns() {
    let dir = tempfile::tempdir().unwrap();
    let set = setup(dir.path(), "[90]");
    // цена падает на 5 bps за минуту ровно: до события и после; сторона события всё равно +1
    let mut base = pump_contract(17_280, &[T0]);
    base.close = (0..17_280)
        .map(|t| 100.0 * (-0.0005 * f64::from(t)).exp())
        .collect();
    put(
        &set.cache,
        "FALLUSDT",
        &with_hl(base, &singles(&[3000, 9000])),
    );
    let out = go(dir.path(), &set, Stage::Fit, 1, "o");
    let rows = table(&out.join("ignitions-fit.csv"));
    assert_eq!(rows.len(), 2);
    for r in &rows {
        assert_eq!(r["sign"], "1");
        // окно признаков — 15 минут (индекс 1); знак импульса — в r_w_bps и в доходностях до t_f, на sign не умножен
        approx(num(r, "r_w_bps"), -0.0005 * 15.0 * 1.0e4, 1.0e-6);
        approx(num(r, "ret_prev60_bps"), -0.0005 * 60.0 * 1.0e4, 1.0e-6);
        approx(num(r, "ret_prev360_bps"), -0.0005 * 360.0 * 1.0e4, 1.0e-6);
        // исходы — абсолютная доходность вверх: падение даёт минус, mfe — вход (0), mae — конец пути
        approx(num(r, "gross_60"), -0.0005 * 60.0 * 1.0e4, 1.0e-6);
        approx(num(r, "mfe_bps"), 0.0, 1.0e-6);
        approx(num(r, "mae_bps"), -0.0005 * 1440.0 * 1.0e4, 1.0e-6);
        assert_eq!(r["mfe_min"], "0");
        assert_eq!(r["mae_min"], "1440");
    }
}

/// Столбцы, зависящие от данных после `t_f` (исходы, контекст исхода) или от эпизодов, найденных задним числом.
fn is_future(col: &str) -> bool {
    col.starts_with("gross_")
        || col.starts_with("mfe_")
        || col.starts_with("mae_")
        || col.starts_with("pk_d_")
        || col == "btc_next360_bps"
        || col == "ep_running"
}

/// Всё после минуты `m` (цены, объёмы, все колонки OI, ставки) заменено другими числами; до `m` — прежнее.
fn scramble_after(c: &mut Contract, m: usize) {
    for t in m + 1..c.close.len() {
        c.close[t] *= 1.0 + 0.05 * (t % 11) as f64;
        c.qvol[t] *= 1.0 + (t % 5) as f32;
        c.trades[t] *= 3.0;
    }
    for col in &mut c.oi {
        for (s, v) in col.iter_mut().enumerate().skip(m / 5 + 1) {
            *v = if s % 17 == 0 {
                f64::NAN
            } else {
                *v * (1.0 + 0.1 * (s % 3) as f64)
            };
        }
    }
    for f in &mut c.funding {
        if f.0 > m as i64 {
            f.1 *= -5.0;
        }
    }
}

fn row_at(rows: &[Row], t_f: i64) -> &Row {
    rows.iter()
        .find(|r| r["t_f"] == t_f.to_string())
        .unwrap_or_else(|| panic!("нет строки t_f {t_f}"))
}

/// Круглые числа цены входа `c[t_f + 1]` (расстояние от неё до уровня) — часть определения столбцов, как в `triggers`.
const ENTRY_COLS: [&str; 3] = ["d_dec", "d_half", "d_125"];

#[test]
fn data_after_t_f_changes_the_outcomes_but_not_the_attributes_and_features() {
    let dir = tempfile::tempdir().unwrap();
    // `edit` правит базовый контракт до наложения high/low
    let run_on = |sub: &str, edit: &dyn Fn(&mut Contract)| -> Vec<Row> {
        let d = dir.path().join(sub);
        fs::create_dir_all(&d).unwrap();
        let set = setup(&d, "[90]");
        let mut c = pump_contract(17_280, &[T0]);
        edit(&mut c);
        put(
            &set.cache,
            "ABCUSDT",
            &with_hl(c, &singles(&[3000, 9000, 12_000])),
        );
        let out = go(&d, &set, Stage::Fit, 1, "o");
        table(&out.join("ignitions-fit.csv"))
    };
    let base = run_on("base", &|_| {});
    assert_eq!(base.len(), 3);
    let t_f = |r: &Row| r["t_f"].parse::<i64>().unwrap();
    let upto =
        |rows: &[Row]| -> Vec<Row> { rows.iter().filter(|r| t_f(r) <= 9000).cloned().collect() };

    // (а) всё после минуты входа t_f + 1 = 9001 заменено (цены, оборот, сделки, OI, фандинг): атрибуты и признаки событий
    // до 9000 включительно прежние, исходы события 9000 и поздняя часть набора — другие
    let cut = run_on("cut", &|c| scramble_after(c, 9001));
    let (b, c) = (upto(&base), upto(&cut));
    assert_eq!((b.len(), c.len()), (2, 2), "события до t_f те же");
    let mut compared = 0;
    for (x, y) in b.iter().zip(&c) {
        for (col, v) in x {
            if !is_future(col) {
                assert_eq!(v, &y[col], "{col} (t_f {})", x["t_f"]);
                compared += 1;
            }
        }
    }
    assert!(compared > 100, "сверено ячеек: {compared}");
    assert_ne!(b[1]["gross_60"], c[1]["gross_60"]);
    let after =
        |rows: &[Row]| -> Vec<Row> { rows.iter().filter(|r| t_f(r) > 9000).cloned().collect() };
    assert_ne!(after(&base), after(&cut));

    // (б) цена входа c[t_f + 1] — в определении круглых чисел: её правка меняет только их (и исходы)
    let entry = run_on("entry", &|c| c.close[9001] *= 1.01);
    let (x, y) = (row_at(&base, 9000), row_at(&entry, 9000));
    for (col, v) in x {
        if !is_future(col) {
            let changed = ENTRY_COLS.contains(&col.as_str());
            assert_eq!(v != &y[col], changed, "{col}: {v} против {}", y[col]);
        }
    }
}

// ---------- аргументы ----------

#[derive(Parser)]
struct W {
    #[command(flatten)]
    a: IgnitionsArgs,
}

#[test]
fn arguments_parse_and_bad_values_are_refused_before_any_work() {
    let ok = [
        "t",
        "--cache",
        "c",
        "--params",
        "p",
        "--out",
        "o",
        "--stage",
        "fit",
        "--hv-natr-pct",
        "1",
        "--hv-n",
        "30",
        "--oi-lag",
        "1",
        "--threads",
        "8",
    ];
    let a = W::try_parse_from(ok).unwrap().a;
    assert_eq!(
        (a.hv_natr_pct, a.hv_n, a.oi_lag, a.threads),
        (1.0, 30, 1, 8)
    );
    assert!(a.seed.is_none() && a.xd.is_none() && a.sign_window.is_none() && a.limit.is_none());
    // порог и окно обязательны, как и сдвиг OI, потоки, стадия
    for skip in [
        "--hv-natr-pct",
        "--hv-n",
        "--oi-lag",
        "--threads",
        "--stage",
    ] {
        let mut v: Vec<&str> = ok.to_vec();
        let i = v.iter().position(|s| *s == skip).unwrap();
        v.remove(i);
        v.remove(i);
        assert!(W::try_parse_from(v).is_err(), "без {skip}");
    }
    let mut v: Vec<&str> = ok.to_vec();
    v.extend([
        "--seed",
        "7",
        "--sign-window",
        "2",
        "--limit",
        "5",
        "--xd",
        "x.csv",
    ]);
    let b = W::try_parse_from(v).unwrap().a;
    assert_eq!(
        (b.seed, b.sign_window, b.limit),
        (Some(7), Some(2), Some(5))
    );

    // значения отвергаются до чтения кэша (каталога кэша нет)
    let dir = tempfile::tempdir().unwrap();
    let set = Set {
        cache: dir.path().join("нет-такого-каталога"),
        params: dir.path().join("params.json"),
        xd: dir.path().join("xd.csv"),
    };
    fs::write(&set.params, params_json("2020-01", "2020-02", "[90]")).unwrap();
    let refused = |edit: &dyn Fn(&mut IgnitionsArgs)| -> String {
        let mut a = args(&set, &dir.path().join("o"), Stage::Fit, 1, false);
        edit(&mut a);
        format!("{:#}", ignitions(a).unwrap_err())
    };
    assert!(refused(&|a| a.hv_natr_pct = 0.0).contains("--hv-natr-pct"));
    assert!(refused(&|a| a.hv_natr_pct = f64::NAN).contains("--hv-natr-pct"));
    assert!(refused(&|a| a.hv_n = 0).contains("--hv-n"));
    assert!(refused(&|a| a.sign_window = Some(2)).contains("--sign-window"));
    // окна 60 мин в тестовых параметрах [5, 15] нет — без --sign-window отказ с названием окна
    assert!(refused(&|a| a.sign_window = None).contains("нет окна 60"));
}
