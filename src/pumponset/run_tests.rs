//! Тесты слоя `run` (trade, agg, run): синтетика с известным ответом, быстрое == наивное, детерминизм по потокам,
//! отсутствие утечек (будущее, тест в фите), вселенная.

use super::agg::{q_index, strata_of, NOT_OBS, NO_LAG};
use super::cache::{write_contract, Contract};
use super::control;
use super::episodes::XdAcc;
use super::episodes_ref::{ff_ln, moves_naive, xd_from_values};
use super::feat;
use super::params::Params;
use super::play::PlayArgs;
use super::run::{
    clip_contract, day_of_min, period, run, slot_range, Fast, RunArgs, Stage, Universe,
};
use super::trade::{round_trip, Funding, Skip};
use super::types::{Cube, Episode, Rule, XdTable, FLAG_MARKET, FLAG_OUTLIER, FLAG_WICK, NF};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

pub(super) const SEED: u64 = 7;
/// Порог хода `X_D` сценария пампа: `90·ε + 87,5·R` (см. `pump_contract`).
pub(super) const XD: f64 = 0.0884;
/// Дрейф цены за минуту (ln) и добавка хода за минуту на 90-минутном участке пампа.
pub(super) const EPS: f64 = 1.0e-5;
pub(super) const R: f64 = 1.0e-3;
pub(super) const T0: usize = 15_000;

// ---------- данные ----------

pub(super) const TPL: &str = r#"{
 "universe":{"min_quote_turnover_usd_per_day":1000,"turnover_lag_days":1},
 "grid_min":{"value":5},
 "episodes":{"D_min":@DS@,"quantile_X_D":0.99,
  "E4_wick":{"return_share":0.5,"within_min":15},"outlier_tail_candles_lt":3,
  "market_stratum":{"ref_symbol":"BTCUSDT","min_overlap_share":0.5}},
 "features":{"windows_min":[5,15],"norm":{"history_days":1,"mad_factor":1.4826},
  "funding_z_history_days":1,"theta":[2,3]},
 "onset":{"L_pre_min":360},
 "controls":{"C2a":{"moments_per_first_trigger":10},
  "C2b":{"moments_per_episode":10,"exclude_same_sign_episode_window_min":[-360,1440]},
  "sigma_1m_window_days":1},
 "label_forward":{"D_min":[90],"H_min":[90,180]},
 "split":{"fit":["@FIT@","@FIT@"],"test":["@TEST@","@TEST@"]}
}"#;

pub(super) fn params_json(fit: &str, test: &str, ds: &str) -> String {
    TPL.replace("@FIT@", fit)
        .replace("@TEST@", test)
        .replace("@DS@", ds)
}

fn params_of(ds: &str) -> Params {
    Params::from_json(&params_json("2019-12", "2020-01", ds)).unwrap()
}

/// xorshift64: детерминированный источник данных тестов.
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
    fn unit(&mut self) -> f64 {
        (self.step() >> 11) as f64 / (1u64 << 53) as f64
    }
    fn below(&mut self, n: u64) -> u64 {
        self.step() % n
    }
}

/// ln цены сценария: дрейф `ε` и 90-минутные участки пампов `R` с началами `pumps` (минуты).
pub(super) fn lnp(t: usize, pumps: &[usize]) -> f64 {
    EPS * t as f64
        + R * pumps
            .iter()
            .map(|&p| (t as f64 - p as f64).clamp(0.0, 90.0))
            .sum::<f64>()
}

/// Контракт с пампами: цена `100·exp(lnp)`, OI-заливка за 55 минут до цены (рост OI на 10 в слот 40 слотов, первое
/// срабатывание F1 в `T − 55`), всплеск оборота в минуту `T` на 60 минут, фандинг `1e-4` в минуты `100 + 480·k`.
pub(super) fn pump_contract(n: usize, pumps: &[usize]) -> Contract {
    let slots = n / 5;
    let close: Vec<f64> = (0..n).map(|t| 100.0 * lnp(t, pumps).exp()).collect();
    let mut qvol = vec![10.0f32; n];
    for &p in pumps {
        for q in qvol.iter_mut().skip(p).take(60) {
            *q = 1000.0;
        }
    }
    let oi0: Vec<f64> = (0..slots)
        .map(|s| {
            1000.0
                + pumps
                    .iter()
                    .map(|&p| {
                        let so = (p - 60) / 5;
                        10.0 * (s as f64 - so as f64 + 1.0).clamp(0.0, 40.0)
                    })
                    .sum::<f64>()
        })
        .collect();
    Contract {
        first_min: 0,
        close,
        vol: vec![1.0; n],
        qvol,
        trades: vec![5.0; n],
        high: Vec::new(),
        low: Vec::new(),
        oi_first_slot: 0,
        oi: [
            oi0,
            vec![1.0e5; slots],
            vec![1.0; slots],
            vec![1.0; slots],
            vec![1.0; slots],
            vec![1.0; slots],
        ],
        funding: (0..n as i64)
            .step_by(480)
            .map(|m| (100 + m, 1.0e-4))
            .collect(),
    }
}

pub(super) fn put(dir: &Path, sym: &str, c: &Contract) {
    let mut f = std::io::BufWriter::new(fs::File::create(dir.join(format!("{sym}.pmc"))).unwrap());
    write_contract(&mut f, c).unwrap();
}

pub(super) fn xd_file(dir: &Path, x: f64) -> PathBuf {
    let p = dir.join("xd-in.csv");
    fs::write(
        &p,
        format!("di,sign,x_d,n_windows\n0,1,{x},100\n0,-1,{x},100\n"),
    )
    .unwrap();
    p
}

pub(super) fn rules_file(dir: &Path, rules: &[(u8, u8, u8)]) -> PathBuf {
    let items: Vec<String> = rules
        .iter()
        .map(|(f, w, t)| format!("{{\"fam\":{f},\"wi\":{w},\"ti\":{t},\"rank_lo\":1.5}}"))
        .collect();
    let p = dir.join("k.json");
    fs::write(&p, format!("[{}]", items.join(","))).unwrap();
    p
}

fn args(
    cache: &Path,
    params: &Path,
    out: &Path,
    stage: Stage,
    rules: Option<PathBuf>,
    xd: Option<PathBuf>,
    threads: usize,
) -> RunArgs {
    RunArgs {
        cache: cache.to_path_buf(),
        params: params.to_path_buf(),
        out: out.to_path_buf(),
        stage,
        rules,
        seed: SEED,
        oi_lag: 1,
        threads,
        xd,
        limit: None,
        exits: false,
        exits_units: super::exits::UnitsArgs::default(),
        dump_triggers: false,
        dump_features: false,
        play: PlayArgs::default(),
    }
}

pub(super) type Row = BTreeMap<String, String>;

pub(super) fn table(path: &Path) -> Vec<Row> {
    let text = fs::read_to_string(path).unwrap();
    let mut lines = text.lines();
    let head: Vec<&str> = lines.next().unwrap().split(',').collect();
    lines
        .filter(|l| !l.is_empty())
        .map(|l| {
            head.iter()
                .zip(l.split(','))
                .map(|(h, v)| ((*h).to_string(), v.to_string()))
                .collect()
        })
        .collect()
}

fn pick<'a>(rows: &'a [Row], conds: &[(&str, String)]) -> Vec<&'a Row> {
    rows.iter()
        .filter(|r| conds.iter().all(|(k, v)| r.get(*k) == Some(v)))
        .collect()
}

pub(super) fn num(r: &Row, col: &str) -> f64 {
    r[col].parse().unwrap()
}

pub(super) fn approx(a: f64, b: f64, tol: f64) {
    assert!((a - b).abs() <= tol, "{a} против {b} (допуск {tol})");
}

// ---------- (а) один заливочный сценарий ----------

#[test]
fn single_pump_scenario_matches_hand_calculation() {
    let dir = tempfile::tempdir().unwrap();
    let (cache, out) = (dir.path().join("cache"), dir.path().join("out"));
    fs::create_dir_all(&cache).unwrap();
    let c = pump_contract(17_280, &[T0]);
    put(&cache, "ABCUSDT", &c);
    let pj = dir.path().join("params.json");
    let text = params_json("2019-12", "2020-01", "[90]");
    fs::write(&pj, &text).unwrap();
    let rules = [(0u8, 0u8, 0u8), (0, 1, 1), (1, 0, 0), (9, 0, 0)];
    run(args(
        &cache,
        &pj,
        &out,
        Stage::Test,
        Some(rules_file(dir.path(), &rules)),
        Some(xd_file(dir.path(), XD)),
        2,
    ))
    .unwrap();

    // эталон C2a / C2b: тот же куб и зерно, прямой вызов ядра контролей
    let p = Params::from_json(&text).unwrap();
    let cube = feat::build_cube(&c, &p, 1);
    let cf = c.close_ff();
    let eps = [Episode {
        di: 0,
        sign: 1,
        t0: T0 as i64,
        t_end: T0 as i64 + 90,
        mv: 0.0909,
        flags: 0,
    }];
    let inp = control::Input {
        sym: "ABCUSDT",
        cube: &cube,
        cf: &cf,
        first_min: 0,
        eps: &eps,
    };
    let xd = XdTable { x: vec![[XD, XD]] };
    let rl: Vec<Rule> = rules
        .iter()
        .map(|&(fam, wi, ti)| Rule { fam, wi, ti })
        .collect();
    let c2a = control::c2a(&inp, &rl, &xd, &p, SEED).unwrap();
    let c2b = control::c2b(&inp, &rl, &p, 90, SEED).unwrap();

    // эпизод: ровно один, чистый (не фитиль, не выброс), без рынка
    let ep = table(&out.join("episodes-test.csv"));
    assert_eq!(ep.len(), 1, "эпизоды: {ep:?}");
    assert_eq!(ep[0]["sym"], "ABCUSDT");
    assert_eq!(
        (ep[0]["t0"].as_str(), ep[0]["t_end"].as_str()),
        ("15000", "15090")
    );
    assert_eq!(
        (ep[0]["sign"].as_str(), ep[0]["flags"].as_str()),
        ("1", "0")
    );
    approx(num(&ep[0], "mv"), 90.0 * (EPS + R), 1e-9);

    let dc = table(&out.join("daycells-test.csv"));
    let lags = table(&out.join("lags-test.csv"));
    let prog = table(&out.join("progress-test.csv"));
    let first = table(&out.join("first-test.csv"));
    let c2a_of = |rule: Rule| {
        let v: Vec<_> = c2a.iter().filter(|r| r.rule == rule).collect();
        assert_eq!(v.len(), 1, "первые срабатывания {rule:?}: {v:?}");
        v[0]
    };
    for (&(fam, wi, ti), &rule) in rules.iter().zip(&rl) {
        let t_f: i64 = if fam == 0 { 14_945 } else { 15_000 };
        let want_hit = fam != 0;
        let tr = c2a_of(rule);
        assert_eq!((tr.t_f, tr.sign), (t_f, 1), "{rule:?}");
        for (hi, h) in [(0usize, 90i64), (1, 180)] {
            let rows = pick(
                &dc,
                &[
                    ("fam", fam.to_string()),
                    ("wi", wi.to_string()),
                    ("ti", ti.to_string()),
                    ("sign", "1".to_string()),
                    ("hi", hi.to_string()),
                ],
            );
            assert_eq!(rows.len(), 1, "{rule:?} hi {hi}: {rows:?}");
            let r = rows[0];
            assert_eq!(r["day"], day_of_min(t_f).to_string());
            assert_eq!(r["month"], "202001");
            assert_eq!(r["n"], "1", "{rule:?}");
            assert_eq!(r["hit"], if want_hit { "1" } else { "0" }, "{rule:?}");
            assert_eq!(r["nret"], "1");
            let (a, b) = (
                lnp((t_f + 1) as usize, &[T0]),
                lnp((t_f + h) as usize, &[T0]),
            );
            // результат сделки — простая доходность: `exp(Δln) − 1`, не `Δln` (допущение 4 `trade.rs`)
            approx(num(r, "gross_bps"), ((b - a).exp() - 1.0) * 1.0e4, 1e-6);
            let pays = (0..40_000i64)
                .map(|k| 100 + 480 * k)
                .filter(|&m| m > t_f + 1 && m <= t_f + h)
                .count();
            approx(num(r, "fund_bps"), pays as f64, 1e-9);
            // база C2a — тот же расчёт ядра контролей
            assert_eq!(r["bn"], tr.c2a_n[hi].to_string(), "{rule:?}");
            assert_eq!(r["bhit"], tr.c2a_hit[hi].to_string(), "{rule:?}");
        }
        // лаги эпизода
        let lag = t_f - T0 as i64;
        for stratum in ["all", "nonmarket"] {
            let rows = pick(
                &lags,
                &[
                    ("kind", "ep".to_string()),
                    ("stratum", stratum.to_string()),
                    ("fam", fam.to_string()),
                    ("wi", wi.to_string()),
                    ("ti", ti.to_string()),
                ],
            );
            assert_eq!(rows.len(), 1, "{rule:?} {stratum}");
            let r = rows[0];
            assert_eq!(
                (r["sign"].as_str(), r["di"].as_str(), r["month"].as_str()),
                ("1", "0", "202001")
            );
            assert_eq!((r["n_ep"].as_str(), r["n_fired"].as_str()), ("1", "1"));
            let b = |x: bool| if x { "1" } else { "0" };
            assert_eq!(r["n_lt0"], b(lag < 0));
            assert_eq!(r["n_le0"], b(lag <= 0));
            assert_eq!(r["n_le30"], b(lag <= 30));
            assert_eq!(r["n_le60"], b(lag <= 60));
            for q in ["p10", "p50", "p90"] {
                assert_eq!(r[q], lag.to_string());
            }
        }
        assert!(pick(
            &lags,
            &[
                ("kind", "ep".to_string()),
                ("stratum", "market".to_string())
            ]
        )
        .is_empty());
        assert!(pick(
            &lags,
            &[("kind", "ep".to_string()), ("stratum", "wick".to_string())]
        )
        .is_empty());
        // нулевая линия C2b против прямого вызова ядра (l_post = медиана единственной фазы = 90)
        let refs: Vec<_> = c2b.iter().filter(|r| r.rule == rule).collect();
        let rows = pick(
            &lags,
            &[
                ("kind", "c2b".to_string()),
                ("stratum", "all".to_string()),
                ("fam", fam.to_string()),
                ("wi", wi.to_string()),
                ("ti", ti.to_string()),
            ],
        );
        assert_eq!(rows.len(), 1, "c2b {rule:?}");
        let r = rows[0];
        let lags_ref: Vec<i64> = refs.iter().filter_map(|x| x.lag).collect();
        assert!(!refs.is_empty(), "в эталоне нет псевдо-моментов");
        assert_eq!(r["n_ep"], refs.len().to_string(), "c2b {rule:?}");
        assert_eq!(r["n_fired"], lags_ref.len().to_string(), "c2b {rule:?}");
        assert_eq!(
            r["n_lt0"],
            lags_ref.iter().filter(|&&l| l < 0).count().to_string()
        );
        assert_eq!(
            r["n_le0"],
            lags_ref.iter().filter(|&&l| l <= 0).count().to_string()
        );
        assert_eq!(
            r["n_le30"],
            lags_ref.iter().filter(|&&l| l <= 30).count().to_string()
        );
        assert_eq!(
            r["n_le60"],
            lags_ref.iter().filter(|&&l| l <= 60).count().to_string()
        );
        if !lags_ref.is_empty() {
            let mut s = lags_ref.clone();
            s.sort_unstable();
            for (q, pct) in [("p10", 10), ("p50", 50), ("p90", 90)] {
                assert_eq!(
                    r[q],
                    s[q_index(s.len(), pct)].to_string(),
                    "c2b {rule:?} {q}"
                );
            }
        }
    }

    // прогресс хода (F1 w5 θ2: вход за 54 минуты до t0)
    let t_in = 14_946usize;
    let (c0, ce, cb) = (lnp(T0, &[T0]), lnp(T0 + 90, &[T0]), lnp(t_in, &[T0]));
    let rows = pick(
        &prog,
        &[
            ("stratum", "all".to_string()),
            ("fam", "0".to_string()),
            ("wi", "0".to_string()),
            ("ti", "0".to_string()),
        ],
    );
    assert_eq!(rows.len(), 1, "{prog:?}");
    let r = rows[0];
    assert_eq!(r["n_fired"], "1");
    for q in ["p10", "p50", "p90"] {
        approx(num(r, &format!("done_bps_{q}")), (cb - c0) * 1.0e4, 1e-3);
        approx(
            num(r, &format!("left_share_{q}")),
            (ce - cb) / (ce - c0),
            1e-5,
        );
        approx(num(r, &format!("to_peak_bps_{q}")), (ce - cb) * 1.0e4, 1e-2);
    }
    // «какое семейство первым»: F1 (лаг −55) раньше F3 (лаг 0)
    for (fam, want) in [(0, "1"), (1, "0")] {
        let rows = pick(
            &first,
            &[
                ("kind", "ep".to_string()),
                ("stratum", "all".to_string()),
                ("wi", "0".to_string()),
                ("ti", "0".to_string()),
                ("fam", fam.to_string()),
            ],
        );
        assert_eq!(rows.len(), 1);
        assert_eq!(
            (rows[0]["n_ep"].as_str(), rows[0]["n_first"].as_str()),
            ("1", want)
        );
    }
    // порог из файла, без пересчёта
    let xd_out = table(&out.join("xd.csv"));
    assert_eq!(xd_out.len(), 2);
    approx(num(&xd_out[0], "x_d"), XD, 1e-12);
    // мета
    let meta: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out.join("run-meta.json")).unwrap()).unwrap();
    assert_eq!(meta["stage"], "test");
    assert_eq!(meta["contracts"], 1);
    assert_eq!(meta["episodes"], 1);
    assert_eq!(
        meta["symbol_days"], 11,
        "сутки 1..11 из 12 (у суток 0 нет предыдущих)"
    );
    assert_eq!(meta["oi_lag_slots"], 1);
    assert_eq!(meta["seed"], SEED);
    assert_eq!(meta["btc_missing"], true);
    assert_eq!(meta["params_sha256"].as_str().unwrap().len(), 64);
    // фандинг покрывает все окна: 4 правила × 2 горизонта; признаки всех правил в окне эпизода определены
    assert_eq!(meta["funding_coverage"]["trades"], 8);
    assert_eq!(meta["funding_coverage"]["trades_uncovered"], 0);
    assert_eq!(meta["funding_coverage"]["contracts_with_uncovered"], 0);
    assert_eq!(meta["lag_observability"]["ep"]["all"]["pairs"], 4);
    assert_eq!(meta["lag_observability"]["ep"]["all"]["unobserved"], 0);
}

// ---------- (б) детерминизм по потокам ----------

pub(super) fn random_contract(seed: u64, days: usize) -> Contract {
    let n = days * 1440;
    let slots = n / 5;
    let mut r = Xs(seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) | 1);
    let mut lp = vec![0.0f64; n];
    let mut x = 0.0;
    for v in lp.iter_mut() {
        x += (r.unit() - 0.5) * 0.0016;
        *v = x;
    }
    for _ in 0..8 {
        let s = 1500 + r.below((n - 2200) as u64) as usize;
        let len = 90 + r.below(250) as usize;
        let tot = (r.unit() - 0.5) * 0.30;
        for (k, v) in lp.iter_mut().enumerate().skip(s) {
            *v += tot * (((k - s) as f64) / len as f64).min(1.0);
        }
    }
    let close: Vec<f64> = lp.iter().map(|v| 10.0 * v.exp()).collect();
    let mut oi0 = vec![1000.0f64; slots];
    for s in 1..slots {
        oi0[s] = (oi0[s - 1] * (1.0 + (r.unit() - 0.5).powi(3) * 0.16)).max(1.0);
    }
    for _ in 0..3 {
        let s = r.below((slots - 40) as u64) as usize;
        for v in oi0.iter_mut().skip(s).take(1 + r.below(30) as usize) {
            *v = f64::NAN;
        }
    }
    let col = |r: &mut Xs| (0..slots).map(|_| 1.0 + r.unit()).collect::<Vec<f64>>();
    let oi1: Vec<f64> = oi0.iter().map(|v| v * 3.0).collect();
    let (oi2, oi3, oi4, oi5) = (col(&mut r), col(&mut r), col(&mut r), col(&mut r));
    Contract {
        first_min: 0,
        close,
        vol: vec![1.0; n],
        qvol: (0..n).map(|_| 5.0 + 5.0 * r.unit() as f32).collect(),
        trades: (0..n)
            .map(|_| (3.0 + 3.0 * r.unit()).round() as f32)
            .collect(),
        high: Vec::new(),
        low: Vec::new(),
        oi_first_slot: 0,
        oi: [oi0, oi1, oi2, oi3, oi4, oi5],
        funding: (0..n as i64)
            .step_by(480)
            .map(|m| (m, (r.unit() - 0.5) * 2.0e-4))
            .collect(),
    }
}

const FILES: [&str; 6] = [
    "daycells-fit.csv",
    "lags-fit.csv",
    "first-fit.csv",
    "progress-fit.csv",
    "episodes-fit.csv",
    "xd.csv",
];

#[test]
fn run_is_byte_identical_for_any_thread_count() {
    let dir = tempfile::tempdir().unwrap();
    let cache = dir.path().join("cache");
    fs::create_dir_all(&cache).unwrap();
    for (i, sym) in ["AAAUSDT", "BBBUSDT", "BTCUSDT", "CCCUSDT", "DDDUSDT"]
        .iter()
        .enumerate()
    {
        put(&cache, sym, &random_contract(11 + i as u64, 14));
    }
    // левередж-токен и контракт без OI — пропускаются
    put(&cache, "BTCUPUSDT", &random_contract(99, 14));
    let mut no_oi = random_contract(98, 14);
    no_oi.oi = Default::default();
    put(&cache, "NOOIUSDT", &no_oi);
    let pj = dir.path().join("params.json");
    fs::write(&pj, params_json("2020-01", "2020-02", "[90,180]")).unwrap();

    let outs: Vec<PathBuf> = [1usize, 4, 3]
        .iter()
        .map(|&th| {
            let out = dir.path().join(format!("out{th}"));
            run(args(&cache, &pj, &out, Stage::Fit, None, None, th)).unwrap();
            out
        })
        .collect();
    for f in FILES {
        let a = fs::read(outs[0].join(f)).unwrap();
        assert!(
            a.iter().filter(|&&b| b == b'\n').count() >= 2,
            "{f}: пустая таблица"
        );
        for o in &outs[1..] {
            assert_eq!(
                a,
                fs::read(o.join(f)).unwrap(),
                "{f}: потоки 1 против другого"
            );
        }
    }
    let lags = table(&outs[0].join("lags-fit.csv"));
    assert!(!pick(&lags, &[("kind", "ep".to_string())]).is_empty());
    assert!(
        !pick(&lags, &[("kind", "c2b".to_string())]).is_empty(),
        "нет строк нулевой линии"
    );
    let meta: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(outs[0].join("run-meta.json")).unwrap()).unwrap();
    assert_eq!(meta["contracts"], 5);
    assert_eq!(meta["skipped"]["leverage"], 1);
    assert_eq!(meta["skipped"]["no_oi"], 1);
    assert_eq!(meta["btc_missing"], false);
    assert_eq!(meta["xd_source"], "computed");
    // отчёт о вселенной не зависит от числа потоков и сходится со счётчиком основного прохода
    let uni = &meta["universe"];
    assert_eq!(uni["contracts_eligible"], 5);
    assert_eq!(uni["episodes_member"], meta["episodes"]);
    assert!(uni["episodes_period"].as_u64() >= uni["episodes_member"].as_u64());
    for o in &outs[1..] {
        let m: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(o.join("run-meta.json")).unwrap()).unwrap();
        assert_eq!(
            m["universe"], meta["universe"],
            "universe: потоки 1 против другого"
        );
        for key in ["lag_observability", "funding_coverage"] {
            assert_eq!(m[key], meta[key], "{key}: потоки 1 против другого");
        }
    }

    // --limit: первые N по алфавиту, число пишется в мета
    let out = dir.path().join("out-limit");
    let mut a = args(&cache, &pj, &out, Stage::Fit, None, None, 2);
    a.limit = Some(2);
    run(a).unwrap();
    let meta: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out.join("run-meta.json")).unwrap()).unwrap();
    assert_eq!(
        (meta["limit"].as_u64(), meta["files"].as_u64()),
        (Some(2), Some(2))
    );
    assert_eq!(meta["contracts"], 2);
    assert_eq!(meta["btc_missing"], false, "BTCUSDT читается и вне --limit");
}

// ---------- (в) сделка с фандингом ----------

#[test]
fn trade_gross_and_funding_both_signs() {
    // минуты 100..699: цена = номер минуты
    let cf: Vec<f64> = (0..600).map(|i| 100.0 + i as f64).collect();
    // выплаты каждые 50 минут с 100-й по 700-ю; наибольший интервал файла — 50
    let mut rates = vec![
        (100i64, 5.0e-4),
        (150, 1.0e-4),
        (200, -2.0e-4),
        (250, 3.0e-4),
        (300, 1.0e-4),
    ];
    rates.extend((350..=700).step_by(50).map(|m| (m, 1.0e-4)));
    let fund = Funding::new(&rates);
    // вход c[141] = 141, выход c[240] = 240; выплаты в (141, 240]: 150 (+1e-4) и 200 (−2e-4)
    let long = round_trip(&cf, 100, &fund, 140, 100, 1).unwrap();
    approx(long.gross_bps, (240.0f64 / 141.0 - 1.0) * 1.0e4, 1e-9);
    approx(long.fund_bps, -1.0, 1e-9); // лонг получил 1 bps
    let short = round_trip(&cf, 100, &fund, 140, 100, -1).unwrap();
    approx(short.gross_bps, -(240.0f64 / 141.0 - 1.0) * 1.0e4, 1e-9);
    // шорт на той же паре цен — ровно минус лонг; по ln-формуле был бы другой модуль
    assert_eq!(short.gross_bps, -long.gross_bps);
    assert!((long.gross_bps - (240.0f64 / 141.0).ln() * 1.0e4).abs() > 500.0);
    approx(short.fund_bps, 1.0, 1e-9); // шорт заплатил 1 bps

    // граница: выплата в минуту входа не входит, в минуту выхода входит
    let a = round_trip(&cf, 100, &fund, 149, 100, 1).unwrap(); // (150, 249] → только 200
    approx(a.fund_bps, -2.0, 1e-9);
    let b = round_trip(&cf, 100, &fund, 149, 101, 1).unwrap(); // (150, 250] → 200 и 250
    approx(b.fund_bps, -2.0 + 3.0, 1e-9);
    // окно без выплат внутри покрытого файла — ноль, сделка есть: (202, 241], следующая выплата в 250
    let z = round_trip(&cf, 100, &fund, 201, 40, 1).unwrap();
    approx(z.fund_bps, 0.0, 1e-12);
    // нет данных: выход за рядом, вход до ряда, знак 0, H = 0, пустой фандинг, дыра в цене входа
    let nodata = Err(Skip::NoData);
    assert_eq!(round_trip(&cf, 100, &fund, 140, 600, 1), nodata);
    assert_eq!(round_trip(&cf, 100, &fund, 90, 20, 1), nodata);
    assert_eq!(round_trip(&cf, 100, &fund, 140, 100, 0), nodata);
    assert_eq!(round_trip(&cf, 100, &fund, 140, 0, 1), nodata);
    assert_eq!(
        round_trip(&cf, 100, &Funding::new(&[]), 140, 100, 1),
        nodata
    );
    let mut hole = cf.clone();
    hole[41] = f64::NAN;
    assert_eq!(round_trip(&hole, 100, &fund, 140, 100, 1), nodata);
    // последняя минута ряда (699) — выход допустим, следующая — нет
    assert!(round_trip(&cf, 100, &fund, 100, 599, 1).is_ok());
    assert_eq!(round_trip(&cf, 100, &fund, 100, 600, 1), nodata);

    // покрытие фандингом, конец файла: последняя ставка 300, интервал 50 — выход до 350 включительно покрыт
    let ended = Funding::new(&rates[..5]);
    let edge = round_trip(&cf, 100, &ended, 301, 49, 1).unwrap(); // m_out = 350: 300 ≥ 350 − 50
    approx(edge.fund_bps, 0.0, 1e-12);
    // m_out = 351: между 300 и 351 обязательно была выплата, а в файле её нет
    assert_eq!(round_trip(&cf, 100, &ended, 301, 50, 1), Err(Skip::Funding));
    // ... и тем более сделка целиком после файла
    assert_eq!(
        round_trip(&cf, 100, &ended, 500, 100, -1),
        Err(Skip::Funding)
    );
    // начало файла: первая ставка 150 позже входа 141 — выплаты до неё неизвестны; вход в 150 — покрыто
    let late = Funding::new(&rates[1..]);
    assert_eq!(round_trip(&cf, 100, &late, 140, 100, 1), Err(Skip::Funding));
    let from_first = round_trip(&cf, 100, &late, 149, 100, 1).unwrap();
    approx(from_first.fund_bps, -2.0, 1e-9);
    // нет цены — «нет данных», даже если фандинг не покрыт (причина — цена)
    assert_eq!(round_trip(&cf, 100, &ended, 90, 20, 1), nodata);
    // наибольший интервал измеряется по файлу: ставки 100, 160, 400 → 240; выход до 640 включительно покрыт
    let sparse_rates = [(100i64, 1.0e-4), (160, 1.0e-4), (400, 1.0e-4)];
    let sparse = Funding::new(&sparse_rates);
    assert!(round_trip(&cf, 100, &sparse, 399, 240, 1).is_ok()); // вход 400, выход 639
    assert!(round_trip(&cf, 100, &sparse, 399, 241, 1).is_ok()); // выход 640 = 400 + 240

    // выход 641 — уже за пределом
    assert_eq!(
        round_trip(&cf, 100, &sparse, 399, 242, 1),
        Err(Skip::Funding)
    );
    // одна ставка: интервал файла неизвестен (0); окно до первой ставки не покрыто
    let one_rate = [(300i64, 1.0e-4)];
    let one = Funding::new(&one_rate);
    assert_eq!(round_trip(&cf, 100, &one, 290, 5, 1), Err(Skip::Funding));
}

// ---------- (г) утечки ----------

#[test]
fn changing_data_after_trigger_does_not_change_trigger() {
    let a = pump_contract(17_280, &[T0]);
    let m_t = 14_990usize; // данные после этой минуты меняются
    let mut b = a.clone();
    for t in m_t + 1..b.close.len() {
        b.close[t] *= 1.5;
        b.qvol[t] *= 100.0;
        b.trades[t] *= 9.0;
    }
    for s in m_t / 5 + 1..b.oi[0].len() {
        b.oi[0][s] += 777.0;
        b.oi[5][s] *= 4.0;
    }
    for f in &mut b.funding {
        if f.0 > m_t as i64 {
            f.1 *= 9.0;
        }
    }
    let p = params_of("[90]");
    let rules = feat::rules(&p);
    let (cube_a, cube_b) = (feat::build_cube(&a, &p, 1), feat::build_cube(&b, &p, 1));
    let last = m_t / 5; // слоты ≤ 2998 целиком по данным ≤ m_t
    let (nw, n) = (cube_a.windows.len(), cube_a.n);
    for f in 0..NF {
        for wi in 0..nw {
            for i in 0..=last {
                let (x, y) = (cube_a.zat(f, wi, i), cube_b.zat(f, wi, i));
                assert!(
                    x.to_bits() == y.to_bits() || (x.is_nan() && y.is_nan()),
                    "z f{f} w{wi} слот {i}"
                );
            }
        }
    }
    let mut fa = Fast::new();
    let mut fb = Fast::new();
    let quiet = (p.onset.l_pre_min / 5) as usize;
    fa.build(&cube_a, &p, &rules, quiet, &|_| true);
    fb.build(&cube_b, &p, &rules, quiet, &|_| true);
    assert_eq!(fa.nr, rules.len());
    assert_eq!(cube_b.n, n);
    for (ri, r) in rules.iter().enumerate() {
        let pa: Vec<usize> = fa.trig[ri].iter().copied().filter(|&i| i <= last).collect();
        let pb: Vec<usize> = fb.trig[ri].iter().copied().filter(|&i| i <= last).collect();
        assert_eq!(pa, pb, "срабатывания {r:?} до изменения данных");
    }
    // сам факт заливки (F1, w 5, θ 2) в слоте 2989 присутствует в обоих
    let ri = rules
        .iter()
        .position(|r| {
            *r == Rule {
                fam: 0,
                wi: 0,
                ti: 0,
            }
        })
        .unwrap();
    assert!(fa.trig[ri].contains(&2989) && fb.trig[ri].contains(&2989));

    // на уровне прогона: число меток n по правилу F1 не зависит от цен после t_f
    let dir = tempfile::tempdir().unwrap();
    let mut counts = Vec::new();
    for (name, c) in [("a", &a), ("b", &b)] {
        let cache = dir.path().join(format!("cache-{name}"));
        fs::create_dir_all(&cache).unwrap();
        put(&cache, "ABCUSDT", c);
        let pj = dir.path().join("params.json");
        fs::write(&pj, params_json("2019-12", "2020-01", "[90]")).unwrap();
        let out = dir.path().join(format!("out-{name}"));
        run(args(
            &cache,
            &pj,
            &out,
            Stage::Test,
            Some(rules_file(dir.path(), &[(0, 0, 0)])),
            Some(xd_file(dir.path(), XD)),
            1,
        ))
        .unwrap();
        let dc = table(&out.join("daycells-test.csv"));
        counts.push(
            dc.iter()
                .map(|r| (r["hi"].clone(), r["day"].clone(), r["n"].clone()))
                .collect::<Vec<_>>(),
        );
    }
    assert!(!counts[0].is_empty());
    assert_eq!(counts[0], counts[1]);
}

// ---------- (д) вселенная ----------

/// Контракт с дрейфом цены и тремя одиночными скачками OI на 3, 4, 5-е сутки (по минуте 300 суток); оборот 3-х суток
/// ниже порога.
fn universe_contract() -> Contract {
    let n = 8 * 1440;
    let slots = n / 5;
    let mut qvol = vec![10.0f32; n];
    for q in qvol.iter_mut().skip(3 * 1440).take(1440) {
        *q = 0.1;
    }
    let oi0: Vec<f64> = (0..slots)
        .map(|s| {
            let jumps = [924usize, 1212, 1500].iter().filter(|&&j| s >= j).count();
            1000.0 + 10.0 * jumps as f64
        })
        .collect();
    Contract {
        first_min: 0,
        close: (0..n).map(|t| 100.0 * (EPS * t as f64).exp()).collect(),
        vol: vec![1.0; n],
        qvol,
        trades: vec![5.0; n],
        high: Vec::new(),
        low: Vec::new(),
        oi_first_slot: 0,
        oi: [
            oi0,
            vec![1.0e5; slots],
            vec![1.0; slots],
            vec![1.0; slots],
            vec![1.0; slots],
            vec![1.0; slots],
        ],
        funding: (0..n as i64)
            .step_by(480)
            .map(|m| (100 + m, 1.0e-4))
            .collect(),
    }
}

#[test]
fn contract_below_turnover_yesterday_gives_no_rows_today() {
    let dir = tempfile::tempdir().unwrap();
    let (cache, out) = (dir.path().join("cache"), dir.path().join("out"));
    fs::create_dir_all(&cache).unwrap();
    let c = universe_contract();
    put(&cache, "ABCUSDT", &c);
    let pj = dir.path().join("params.json");
    fs::write(&pj, params_json("2020-01", "2020-02", "[90]")).unwrap();
    run(args(
        &cache,
        &pj,
        &out,
        Stage::Fit,
        Some(rules_file(dir.path(), &[(0, 0, 0)])),
        Some(xd_file(dir.path(), XD)),
        1,
    ))
    .unwrap();
    let dc = table(&out.join("daycells-fit.csv"));
    let d0 = day_of_min(0);
    let days: Vec<i64> = dc
        .iter()
        .filter(|r| r["hi"] == "0")
        .map(|r| r["day"].parse().unwrap())
        .collect();
    // триггеры на 3-и, 4-е, 5-е сутки (слоты 925, 1213, 1501); 4-е сутки — после суток 3 с оборотом 144 < 1000
    assert_eq!(days, vec![d0 + 3, d0 + 5], "{dc:?}");
    let meta: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out.join("run-meta.json")).unwrap()).unwrap();
    assert_eq!(meta["symbol_days"], 6, "сутки 1, 2, 3, 5, 6, 7");
    // сама вселенная: порог включительно, лаг в сутки
    let u = Universe::build(&c, 1000.0, 1);
    let ok: Vec<bool> = (0..8).map(|d| u.day_ok(d0 + d)).collect();
    assert_eq!(ok, vec![false, true, true, true, false, true, true, true]);
    assert!(!u.day_ok(d0 - 1) && !u.day_ok(d0 + 8));
    let mut edge = pump_contract(3 * 1440, &[]);
    edge.qvol = vec![0.0; 3 * 1440];
    for q in edge.qvol.iter_mut().take(400) {
        *q = 2.5; // сутки 0: ровно 1000
    }
    for q in edge.qvol.iter_mut().skip(1440).take(399) {
        *q = 2.5; // сутки 1: 997,5
    }
    let u = Universe::build(&edge, 1000.0, 1);
    assert!(!u.day_ok(d0) && u.day_ok(d0 + 1) && !u.day_ok(d0 + 2));
    let u2 = Universe::build(&edge, 1000.0, 2);
    assert!(!u2.day_ok(d0 + 1) && u2.day_ok(d0 + 2));
    assert_eq!(u.days_in(0, 3 * 1440), 1);
}

/// Отчёт о вселенной (П-34 §1): снятые контракты и эпизоды, которые правило `d − 1` теряет и добавляет.
#[test]
fn universe_report_counts_delisted_and_lag_losses() {
    let dir = tempfile::tempdir().unwrap();
    let (cache, out) = (dir.path().join("cache"), dir.path().join("out"));
    fs::create_dir_all(&cache).unwrap();
    let n = 17_280;
    let set_day = |c: &mut Contract, day: usize, q: f32| {
        for x in c.qvol.iter_mut().skip(day * 1440).take(1440) {
            *x = q;
        }
    };
    // памп в 10-е сутки; оборот всех суток выше порога (14 400 за сутки против 1000)
    put(&cache, "AAAUSDT", &pump_contract(n, &[T0]));
    // оборот 9-х суток ниже порога, 10-х выше (из-за самого пампа): по d − 1 эпизод теряется
    let mut lost = pump_contract(n, &[T0]);
    set_day(&mut lost, 9, 0.1);
    put(&cache, "BBBUSDT", &lost);
    // оборот 10-х суток ниже порога (всплеск пампа убран), 9-х выше: по d − 1 эпизод добавляется
    let mut gained = pump_contract(n, &[T0]);
    set_day(&mut gained, 10, 0.1);
    put(&cache, "CCCUSDT", &gained);
    // снят в конце 9-х суток, до пампа: эпизодов нет
    put(&cache, "DDDUSDT", &pump_contract(9 * 1440, &[]));
    // левередж-токен: пропущен, в подходящие и в счёт эпизодов не входит
    put(&cache, "BTCUPUSDT", &pump_contract(n, &[T0]));
    let pj = dir.path().join("params.json");
    fs::write(&pj, params_json("2019-12", "2020-01", "[90]")).unwrap();
    run(args(
        &cache,
        &pj,
        &out,
        Stage::Test,
        Some(rules_file(dir.path(), &[(0, 0, 0)])),
        Some(xd_file(dir.path(), XD)),
        3,
    ))
    .unwrap();
    let meta: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out.join("run-meta.json")).unwrap()).unwrap();
    let u = &meta["universe"];
    assert_eq!(u["turnover_lag_days"], 1);
    assert_eq!(u["min_quote_turnover_usd_per_day"].as_f64(), Some(1000.0));
    assert_eq!(u["data_end_day"], "2020-01-12");
    assert_eq!(u["contracts_eligible"], 4);
    assert_eq!(u["contracts_delisted"], 1, "{u}");
    assert_eq!(u["episodes_period"], 3, "{u}");
    assert_eq!(u["episodes_member"], 2, "AAA и CCC");
    assert_eq!(u["episodes_member_same_day"], 2, "AAA и BBB");
    assert_eq!(u["episodes_lost_by_lag"], 1, "BBB");
    assert_eq!(u["episodes_gained_by_lag"], 1, "CCC");
    assert_eq!(meta["episodes"], u["episodes_member"]);
    assert_eq!(meta["skipped"]["leverage"], 1);
    let ep = table(&out.join("episodes-test.csv"));
    let mut syms: Vec<&str> = ep.iter().map(|r| r["sym"].as_str()).collect();
    syms.sort_unstable();
    assert_eq!(syms, ["AAAUSDT", "CCCUSDT"], "в таблице только члены");
}

// ---------- (е) стадия fit не читает тест ----------

#[test]
fn fit_stage_ignores_test_period_minutes() {
    let dir = tempfile::tempdir().unwrap();
    let n = 41 * 1440;
    let feb = 31 * 1440;
    let a = pump_contract(n, &[T0, 47_000]);
    let mut b = a.clone();
    for t in feb..n {
        b.close[t] *= 3.0;
        b.qvol[t] *= 0.001;
        b.trades[t] *= 7.0;
    }
    for s in feb / 5..n / 5 {
        b.oi[0][s] += 5000.0;
        b.oi[2][s] *= 2.0;
    }
    for f in &mut b.funding {
        if f.0 >= feb as i64 {
            f.1 *= 5.0;
        }
    }
    let pj = dir.path().join("params.json");
    fs::write(&pj, params_json("2020-01", "2020-02", "[90]")).unwrap();
    let rules = rules_file(dir.path(), &[(0, 0, 0), (1, 0, 0), (9, 0, 0), (0, 1, 1)]);
    let xd = xd_file(dir.path(), XD);
    let mut outs = Vec::new();
    for (name, c) in [("a", &a), ("b", &b)] {
        let cache = dir.path().join(format!("cache-{name}"));
        fs::create_dir_all(&cache).unwrap();
        put(&cache, "ABCUSDT", c);
        let fit = dir.path().join(format!("fit-{name}"));
        run(args(
            &cache,
            &pj,
            &fit,
            Stage::Fit,
            Some(rules.clone()),
            Some(xd.clone()),
            2,
        ))
        .unwrap();
        let test = dir.path().join(format!("test-{name}"));
        run(args(
            &cache,
            &pj,
            &test,
            Stage::Test,
            Some(rules.clone()),
            Some(xd.clone()),
            2,
        ))
        .unwrap();
        outs.push((fit, test));
    }
    for f in FILES {
        assert_eq!(
            fs::read(outs[0].0.join(f)).unwrap(),
            fs::read(outs[1].0.join(f)).unwrap(),
            "{f}: фит изменился от данных теста"
        );
    }
    let fit_eps = table(&outs[0].0.join("episodes-fit.csv"));
    assert_eq!(fit_eps.len(), 1, "в фите один памп: {fit_eps:?}");
    // контроль чувствительности: таблицы теста от тех же правок меняются
    assert_ne!(
        fs::read(outs[0].1.join("daycells-test.csv")).unwrap(),
        fs::read(outs[1].1.join("daycells-test.csv")).unwrap()
    );
    let test_eps = table(&outs[0].1.join("episodes-test.csv"));
    assert!(test_eps
        .iter()
        .all(|r| r["t0"].parse::<i64>().unwrap() >= feb as i64));
}

#[test]
fn clip_keeps_only_minutes_before_end() {
    let mut c = pump_contract(2 * 1440, &[]);
    clip_contract(&mut c, 1440 + 7);
    assert_eq!(c.close.len(), 1447);
    assert_eq!(
        (c.vol.len(), c.qvol.len(), c.trades.len()),
        (1447, 1447, 1447)
    );
    assert_eq!(c.oi[0].len(), (1447 / 5) as usize);
    assert!(c.funding.iter().all(|f| f.0 < 1447));
    let mut early = pump_contract(100, &[]);
    clip_contract(&mut early, -5);
    assert!(early.close.is_empty() && early.oi[0].is_empty() && early.funding.is_empty());
}

// ---------- быстрые правила и лаги против ядра ----------

fn random_cube(seed: u64, n: usize) -> Cube {
    let mut r = Xs(seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) | 1);
    let nw = 2;
    let mut z = vec![0.0f32; NF * nw * n];
    for wi in 0..nw {
        for i in 0..n {
            let big = r.unit() < 0.03;
            for f in 0..NF {
                let v = if big && r.unit() < 0.85 {
                    match r.below(12) {
                        0 => f32::INFINITY,
                        1 => f32::NEG_INFINITY,
                        _ => ((r.unit() - 0.5) * 12.0) as f32,
                    }
                } else if r.unit() < 0.01 {
                    f32::NAN
                } else {
                    ((r.unit() - 0.5) * 2.0) as f32
                };
                z[(f * nw + wi) * n + i] = v;
            }
        }
    }
    Cube {
        slot0: 3,
        n,
        windows: vec![5, 15],
        z,
        r: vec![0.001; nw * n],
        sigma: vec![0.001; n],
    }
}

#[test]
fn fast_triggers_and_next_fire_equal_feat() {
    let p = params_of("[90]");
    let rules = feat::rules(&p);
    let quiet = (p.onset.l_pre_min / 5) as usize;
    for seed in 1..=4u64 {
        let cube = random_cube(seed, 2000);
        let mut fast = Fast::new();
        let keep = |t: i64| t % 3 != 0;
        fast.build(&cube, &p, &rules, quiet, &keep);
        let mut any = 0;
        for (ri, &rule) in rules.iter().enumerate() {
            let want: Vec<usize> = feat::first_triggers(&cube, rule, &p)
                .into_iter()
                .filter(|&i| keep(5 * (cube.slot0 + i as i64)))
                .collect();
            any += want.len();
            assert_eq!(fast.trig[ri], want, "seed {seed} {rule:?}");
            // следующее срабатывание с каждого слота
            let mut next = u32::MAX;
            for i in (0..=cube.n).rev() {
                if i < cube.n && feat::fires(&cube, rule, &p, i) {
                    next = i as u32;
                }
                assert_eq!(
                    fast.nxt[i * rules.len() + ri],
                    next,
                    "seed {seed} {rule:?} слот {i}"
                );
            }
        }
        assert!(any > 50, "сценарий слишком вялый: {any} срабатываний");
    }
}

#[test]
fn fast_lags_equal_core_lags() {
    let p = params_of("[90]");
    let rules = feat::rules(&p);
    let quiet = (p.onset.l_pre_min / 5) as usize;
    let cube = random_cube(9, 2000);
    let mut fast = Fast::new();
    fast.build(&cube, &p, &rules, quiet, &|_| true);
    let mut r = Xs(5);
    // эпизоды в том числе у краёв куба и за ним
    let eps: Vec<Episode> = (0..60)
        .map(|k| {
            let t0 = if k < 6 {
                [-200, 0, 4, 9_990, 9_900, 20_000][k]
            } else {
                r.below(10_200) as i64 - 100
            };
            let len = 90 + r.below(1350) as i64;
            Episode {
                di: 0,
                sign: 1,
                t0,
                t_end: t0 + len,
                mv: 0.1,
                flags: 0,
            }
        })
        .collect();
    let fires = |rule: Rule, i: usize| feat::fires(&cube, rule, &p, i);
    for lp in [None, Some(90i64), Some(700)] {
        let want = control::lags_with(&cube, &eps, &rules, &p, lp, fires);
        let mut buf = vec![NO_LAG; rules.len()];
        let l_pre = i64::from(p.onset.l_pre_min);
        for (ei, e) in eps.iter().enumerate() {
            let hi = e.t0 + lp.unwrap_or(e.t_end - e.t0);
            fast.fill_lags(cube.slot0, e.t0, e.t0 - l_pre, hi, &mut buf);
            for (ri, &rule) in rules.iter().enumerate() {
                let row = &want[ei * rules.len() + ri];
                assert_eq!(row.rule, rule);
                let got = (buf[ri] != NO_LAG).then(|| i64::from(buf[ri]));
                assert_eq!(
                    got, row.lag,
                    "эпизод {ei} {e:?} правило {rule:?} l_post {lp:?}"
                );
            }
        }
    }
    assert_eq!(slot_range(3, 10, 0, 100), Some((0, 9)));
    assert_eq!(slot_range(3, 10, 16, 16), None); // внутри слота 3 минуты 15..19, первый слот ≥ 16 — 4
    assert_eq!(slot_range(3, 10, 15, 16), Some((0, 0)));
    assert_eq!(slot_range(3, 10, 100, 120), None);
}

// ---------- маска вселенной в X_D против наивного счёта ----------

fn walk_contract(seed: u64, n: usize, first_min: i64) -> Contract {
    let mut r = Xs(seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) | 1);
    let mut x = 0.0f64;
    let mut close = Vec::with_capacity(n);
    for _ in 0..n {
        x += (r.unit() - 0.5) * 0.006;
        close.push(10.0 * x.exp());
    }
    Contract {
        first_min,
        close,
        ..Default::default()
    }
}

#[test]
fn xd_masked_equals_naive_masked() {
    type Mask = fn(i64) -> bool;
    let p = params_of("[90,180]");
    let cs: Vec<Contract> = (0..4u64)
        .map(|s| walk_contract(40 + s, 5_000 + 700 * s as usize, 1_000 + 333 * s as i64))
        .collect();
    let fit = [2_000i64, 7_500];
    let masks: [(&str, Mask); 3] = [
        ("все", |_| true),
        ("каждые вторые сутки", |m| {
            day_of_min(m) % 2 == 0
        }),
        ("ничего", |_| false),
    ];
    for (name, mask) in masks {
        // эталон: все ходы концов в фите и маске, сортировка, квантиль
        let nd = p.episodes.d_min.len();
        let mut vals: Vec<[Vec<f64>; 2]> = vec![Default::default(); nd];
        for c in &cs {
            let (_, lc) = ff_ln(c);
            for (di, &d) in p.episodes.d_min.iter().enumerate() {
                for (j, m) in moves_naive(&lc, d as usize).into_iter().enumerate() {
                    let minute = c.first_min + j as i64;
                    if (fit[0]..fit[1]).contains(&minute) && mask(minute) && !m.up.is_nan() {
                        vals[di][0].push(m.up);
                        vals[di][1].push(m.dn);
                    }
                }
            }
        }
        for v in &mut vals {
            for s in v.iter_mut() {
                s.sort_by(f64::total_cmp);
            }
        }
        let want = xd_from_values(&vals, p.episodes.quantile_x_d);
        for chunk in [1usize, 3] {
            let mut master = XdAcc::new(&p);
            for part in cs.chunks(chunk) {
                let mut a = XdAcc::new(&p);
                for c in part {
                    a.add_masked(c, &p, fit, mask);
                }
                master.merge(a);
            }
            master.begin_pass2();
            for part in cs.chunks(chunk) {
                let mut w = master.pass2_worker();
                for c in part {
                    w.add_masked(c, &p, fit, mask);
                }
                master.merge(w);
            }
            let got = master.finish().unwrap();
            for (g, w) in got.x.iter().zip(&want.x) {
                for k in 0..2 {
                    assert!(
                        g[k].to_bits() == w[k].to_bits() || (g[k].is_nan() && w[k].is_nan()),
                        "{name} chunk {chunk}: {got:?} против {want:?}"
                    );
                }
            }
        }
    }
    // без маски add == add_masked(|_| true)
    let (mut a, mut b) = (XdAcc::new(&p), XdAcc::new(&p));
    for c in &cs {
        a.add(c, &p, fit);
        b.add_masked(c, &p, fit, |_| true);
    }
    assert_eq!(a.counts(), b.counts());
}

// ---------- мелочи ----------

#[test]
fn strata_quantiles_and_periods() {
    assert_eq!(strata_of(0), ([0, 2], 2));
    assert_eq!(strata_of(FLAG_MARKET), ([0, 1], 2));
    assert_eq!(strata_of(FLAG_OUTLIER), ([3, 0], 1));
    assert_eq!(strata_of(FLAG_OUTLIER | FLAG_MARKET), ([3, 0], 1));
    assert_eq!(
        strata_of(FLAG_WICK | FLAG_OUTLIER | FLAG_MARKET),
        ([4, 0], 1)
    );
    // нижний ранг floor(q·(n−1))
    assert_eq!(
        (
            q_index(1, 90),
            q_index(2, 50),
            q_index(11, 10),
            q_index(11, 90)
        ),
        (0, 0, 1, 9)
    );
    assert_eq!(q_index(10, 90), 8);
    // период: месяцы включительно, начало и конец в минутах от GRID0
    let (pm, months) = period(&["2020-01".to_string(), "2020-02".to_string()]).unwrap();
    assert_eq!(pm, [0, 60 * 1440]);
    assert_eq!(months, vec![202001, 202002]);
    let (pm, months) = period(&["2019-12".to_string(), "2020-01".to_string()]).unwrap();
    assert_eq!(pm, [-31 * 1440, 31 * 1440]);
    assert_eq!(months, vec![201912, 202001]);
    assert!(period(&["2020-03".to_string(), "2020-01".to_string()]).is_err());
    assert!(period(&["2020-13".to_string(), "2020-14".to_string()]).is_err());
    assert!(period(&["2020".to_string(), "2020".to_string()]).is_err());
    assert_eq!(day_of_min(0), 18_262);
    assert_eq!(day_of_min(-1), 18_261);
}

#[test]
fn run_rejects_bad_setup() {
    let dir = tempfile::tempdir().unwrap();
    let cache = dir.path().join("cache");
    fs::create_dir_all(&cache).unwrap();
    put(&cache, "ABCUSDT", &pump_contract(3 * 1440, &[]));
    let pj = dir.path().join("params.json");
    fs::write(&pj, params_json("2020-01", "2020-02", "[90]")).unwrap();
    let out = dir.path().join("out");
    // test без --rules
    let e = run(args(
        &cache,
        &pj,
        &out,
        Stage::Test,
        None,
        Some(xd_file(dir.path(), XD)),
        1,
    ))
    .unwrap_err();
    assert!(format!("{e:#}").contains("--rules"), "{e:#}");
    // нет секции universe
    let no_uni =
        params_json("2020-01", "2020-02", "[90]").replace("\"universe\"", "\"universe_x\"");
    let pj2 = dir.path().join("p2.json");
    fs::write(&pj2, no_uni).unwrap();
    let e = run(args(
        &cache,
        &pj2,
        &out,
        Stage::Fit,
        None,
        Some(xd_file(dir.path(), XD)),
        1,
    ))
    .unwrap_err();
    assert!(format!("{e:#}").contains("universe"), "{e:#}");
    // правило вне сетки
    let bad = rules_file(dir.path(), &[(0, 5, 0)]);
    let e = run(args(
        &cache,
        &pj,
        &out,
        Stage::Fit,
        Some(bad),
        Some(xd_file(dir.path(), XD)),
        1,
    ))
    .unwrap_err();
    assert!(format!("{e:#}").contains("вне сетки"), "{e:#}");
    // пустой каталог кэша
    let empty = dir.path().join("empty");
    fs::create_dir_all(&empty).unwrap();
    assert!(run(args(&empty, &pj, &out, Stage::Fit, None, None, 1)).is_err());
}

// ---------- (ж) наблюдаемость правил, рыночный эталон, покрытие фандинга ----------

/// Наивная наблюдаемость: в слотах окна минут `[lo, hi]` есть слот, где z всех признаков правила не `NaN`.
fn observed_naive(cube: &Cube, r: Rule, lo: i64, hi: i64) -> bool {
    slot_range(cube.slot0, cube.n, lo, hi).is_some_and(|(a, b)| {
        (a..=b).any(|i| {
            feat::FAMILIES[r.fam as usize]
                .iter()
                .all(|&f| !cube.zat(f, r.wi as usize, i).is_nan())
        })
    })
}

/// Итог `observability_case` по правилам (в порядке `rules`), страта `all`.
struct Obs {
    n_eps: usize,
    ep_n: Vec<u64>,
    ep_fired: Vec<u64>,
    c2b_total: Vec<u64>,
    c2b_n: Vec<u64>,
}

/// Прогон `test` на одном контракте и сверка `n_ep` / `n_fired` таблицы лагов (страта `all`) и блока
/// `lag_observability` с наивным счётом по кубу. Эпизоды берутся из `episodes-test.csv` (в сценариях нет эпизодов
/// вне вселенной, поэтому список равен списку прогона).
fn observability_case(c: &Contract, text: &str, rules: &[(u8, u8, u8)]) -> Obs {
    let dir = tempfile::tempdir().unwrap();
    let (cache, out) = (dir.path().join("cache"), dir.path().join("out"));
    fs::create_dir_all(&cache).unwrap();
    put(&cache, "ABCUSDT", c);
    let pj = dir.path().join("params.json");
    fs::write(&pj, text).unwrap();
    run(args(
        &cache,
        &pj,
        &out,
        Stage::Test,
        Some(rules_file(dir.path(), rules)),
        Some(xd_file(dir.path(), XD)),
        2,
    ))
    .unwrap();

    let p = Params::from_json(text).unwrap();
    let cube = feat::build_cube(c, &p, 1);
    let cf = c.close_ff();
    let eps: Vec<Episode> = table(&out.join("episodes-test.csv"))
        .iter()
        .map(|r| Episode {
            di: r["di"].parse().unwrap(),
            sign: r["sign"].parse().unwrap(),
            t0: r["t0"].parse().unwrap(),
            t_end: r["t_end"].parse().unwrap(),
            mv: num(r, "mv"),
            flags: r["flags"].parse().unwrap(),
        })
        .collect();
    let rl: Vec<Rule> = rules
        .iter()
        .map(|&(fam, wi, ti)| Rule { fam, wi, ti })
        .collect();
    let l_pre = i64::from(p.onset.l_pre_min);
    let mut lens: Vec<i64> = eps.iter().map(|e| e.t_end - e.t0).collect();
    lens.sort_unstable();
    let l_post = lens[(lens.len() - 1) / 2];
    let inp = control::Input {
        sym: "ABCUSDT",
        cube: &cube,
        cf: &cf,
        first_min: 0,
        eps: &eps,
    };
    let c2b = control::c2b(&inp, &rl, &p, l_post, SEED).unwrap();
    let univ = Universe::build(c, 1000.0, 1);
    let lags = table(&out.join("lags-test.csv"));
    let sum = |kind: &str, r: Rule, col: &str| -> u64 {
        pick(
            &lags,
            &[
                ("kind", kind.to_string()),
                ("stratum", "all".to_string()),
                ("fam", r.fam.to_string()),
                ("wi", r.wi.to_string()),
                ("ti", r.ti.to_string()),
            ],
        )
        .iter()
        .map(|row| row[col].parse::<u64>().unwrap())
        .sum()
    };
    let mut o = Obs {
        n_eps: eps.len(),
        ep_n: Vec::new(),
        ep_fired: Vec::new(),
        c2b_total: Vec::new(),
        c2b_n: Vec::new(),
    };
    for &r in &rl {
        let want_ep = eps
            .iter()
            .filter(|e| observed_naive(&cube, r, e.t0 - l_pre, e.t_end))
            .count() as u64;
        let moments: Vec<_> = c2b
            .iter()
            .filter(|x| x.rule == r && univ.min_ok(x.t_pseudo))
            .collect();
        let seen: Vec<_> = moments
            .iter()
            .filter(|x| observed_naive(&cube, r, x.t_pseudo - l_pre, x.t_pseudo + l_post))
            .collect();
        assert_eq!(sum("ep", r, "n_ep"), want_ep, "{r:?}: n_ep эпизодов");
        assert_eq!(
            sum("c2b", r, "n_ep"),
            seen.len() as u64,
            "{r:?}: n_ep моментов C2b"
        );
        assert_eq!(
            sum("c2b", r, "n_fired"),
            seen.iter().filter(|x| x.lag.is_some()).count() as u64,
            "{r:?}: n_fired моментов C2b"
        );
        o.ep_n.push(want_ep);
        o.ep_fired.push(sum("ep", r, "n_fired"));
        o.c2b_total.push(moments.len() as u64);
        o.c2b_n.push(seen.len() as u64);
    }
    // блок run-meta: число пар и ненаблюдаемых по kind и страте
    let meta: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out.join("run-meta.json")).unwrap()).unwrap();
    let ob = &meta["lag_observability"];
    let pairs_ep = (eps.len() * rl.len()) as u64;
    assert_eq!(ob["ep"]["all"]["pairs"].as_u64(), Some(pairs_ep));
    assert_eq!(
        ob["ep"]["all"]["unobserved"].as_u64(),
        Some(pairs_ep - o.ep_n.iter().sum::<u64>())
    );
    let pairs_c2b: u64 = o.c2b_total.iter().sum();
    assert_eq!(ob["c2b"]["all"]["pairs"].as_u64(), Some(pairs_c2b));
    assert_eq!(
        ob["c2b"]["all"]["unobserved"].as_u64(),
        Some(pairs_c2b - o.c2b_n.iter().sum::<u64>())
    );
    assert_eq!(ob["ep"]["market"]["pairs"].as_u64(), Some(0));
    o
}

/// Эпизод на 3-и сутки жизни контракта: нормы за 3 суток там ещё нет — признаки F1/F3 в окне не определены, а у
/// фандинга (норма — сутки) определены. Такая пара не растёт в `n_ep` и не даёт `n_fired`.
#[test]
fn episode_before_norm_exists_is_not_observable() {
    let text = params_json("2019-12", "2020-01", "[90]")
        .replace("\"history_days\":1", "\"history_days\":3");
    let c = pump_contract(17_280, &[3_000, T0]);
    let rules = [(0u8, 0u8, 0u8), (1, 0, 0), (8, 0, 0)];
    let o = observability_case(&c, &text, &rules);
    assert_eq!(o.n_eps, 2);
    // F1, F3: только второй эпизод; F10 (фандинг): оба, но z = 0 — не срабатывает
    assert_eq!(o.ep_n, vec![1, 1, 2]);
    assert_eq!(o.ep_fired, vec![1, 1, 0]);
}

/// Эпизод внутри дыры OI: OI-правила (F1, S_oi) его не наблюдают, F3 (оборот) наблюдает; нулевая линия C2b —
/// те же правила, дыра накрывает три четверти ряда, так что часть моментов F1 ненаблюдаема.
#[test]
fn episode_inside_oi_hole_is_not_observable_for_oi_rules() {
    let mut c = pump_contract(17_280, &[6_000, T0]);
    for v in c.oi[0].iter_mut().skip(300).take(2_201) {
        *v = f64::NAN; // слоты 300..=2500: минуты 1500…12500
    }
    let text = params_json("2019-12", "2020-01", "[90]");
    let rules = [(0u8, 0u8, 0u8), (1, 0, 0), (9, 0, 0)];
    let o = observability_case(&c, &text, &rules);
    assert_eq!(o.n_eps, 2);
    assert_eq!(o.ep_n, vec![1, 2, 1], "F1 и S_oi — только эпизод вне дыры");
    assert_eq!(o.ep_fired, vec![1, 2, 1]);
    assert!(
        o.c2b_n[0] < o.c2b_total[0],
        "в дыре есть моменты C2b: {} из {}",
        o.c2b_n[0],
        o.c2b_total[0]
    );
    assert!(o.c2b_n[0] > 0, "и есть наблюдаемые");
}

/// Префиксные суммы `Fast` против наивного обхода слотов окна: длинные провалы у всех признаков и только у части.
#[test]
fn fast_unobserved_marks_equal_naive_scan() {
    let p = params_of("[90]");
    let rules = feat::rules(&p);
    let quiet = (p.onset.l_pre_min / 5) as usize;
    let n = 2000usize;
    let mut cube = random_cube(21, n);
    for wi in 0..2 {
        // слоты 0..400 — провал у всех признаков; 900..1300 — только у F1 и F4 (индексы 0 и 3)
        for f in 0..NF {
            for i in 0..400 {
                cube.z[(f * 2 + wi) * n + i] = f32::NAN;
            }
        }
        for f in [0usize, 3] {
            for i in 900..1300 {
                cube.z[(f * 2 + wi) * n + i] = f32::NAN;
            }
        }
    }
    let mut fast = Fast::new();
    fast.build(&cube, &p, &rules, quiet, &|_| true);
    let mut r = Xs(8);
    let mut seen = [0usize; 2];
    for k in 0..400 {
        let t_lo = r.below(10_600) as i64 - 300;
        let t_hi = t_lo + r.below(if k % 2 == 0 { 40 } else { 900 }) as i64;
        let mut buf = vec![NO_LAG; rules.len()];
        fast.mark_unobserved(cube.slot0, t_lo, t_hi, &mut buf);
        for (ri, &rule) in rules.iter().enumerate() {
            let want = !observed_naive(&cube, rule, t_lo, t_hi);
            assert_eq!(buf[ri] == NOT_OBS, want, "{rule:?} [{t_lo}, {t_hi}]");
            seen[usize::from(want)] += 1;
        }
    }
    assert!(
        seen[0] > 100 && seen[1] > 100,
        "вырожденный сценарий: {seen:?}"
    );
}

/// Эпизоды самого эталона (BTCUSDT) — «рыночные» по определению §2; остальные — по перекрытию с эталоном.
#[test]
fn reference_symbol_episodes_get_market_flag() {
    let dir = tempfile::tempdir().unwrap();
    let (cache, out) = (dir.path().join("cache"), dir.path().join("out"));
    fs::create_dir_all(&cache).unwrap();
    put(&cache, "BTCUSDT", &pump_contract(17_280, &[T0]));
    put(&cache, "AAAUSDT", &pump_contract(17_280, &[T0]));
    put(&cache, "BBBUSDT", &pump_contract(17_280, &[8_000]));
    let pj = dir.path().join("params.json");
    fs::write(&pj, params_json("2019-12", "2020-01", "[90]")).unwrap();
    run(args(
        &cache,
        &pj,
        &out,
        Stage::Test,
        Some(rules_file(dir.path(), &[(0, 0, 0)])),
        Some(xd_file(dir.path(), XD)),
        3,
    ))
    .unwrap();
    let ep = table(&out.join("episodes-test.csv"));
    let flags_of = |sym: &str| -> Vec<String> {
        pick(&ep, &[("sym", sym.to_string())])
            .iter()
            .map(|r| r["flags"].clone())
            .collect()
    };
    assert_eq!(flags_of("BTCUSDT"), vec!["4"], "эталон — рыночный");
    assert_eq!(flags_of("AAAUSDT"), vec!["4"], "совпадает с эталоном");
    assert_eq!(flags_of("BBBUSDT"), vec!["0"], "не пересекается с эталоном");
    let lags = table(&out.join("lags-test.csv"));
    let n_ep = |stratum: &str| -> u64 {
        pick(
            &lags,
            &[
                ("kind", "ep".to_string()),
                ("stratum", stratum.to_string()),
                ("fam", "0".to_string()),
            ],
        )
        .iter()
        .map(|r| r["n_ep"].parse::<u64>().unwrap())
        .sum()
    };
    assert_eq!(
        (n_ep("all"), n_ep("market"), n_ep("nonmarket")),
        (3, 2, 1),
        "{lags:?}"
    );
}

/// Прогон `test` по одному контракту с одним пампом, у которого правится файл фандинга: строки `daycells` и мета.
fn funding_case(edit: impl FnOnce(&mut Contract)) -> (Vec<Row>, serde_json::Value) {
    let dir = tempfile::tempdir().unwrap();
    let (cache, out) = (dir.path().join("cache"), dir.path().join("out"));
    fs::create_dir_all(&cache).unwrap();
    let mut c = pump_contract(17_280, &[T0]);
    edit(&mut c);
    put(&cache, "ABCUSDT", &c);
    let pj = dir.path().join("params.json");
    fs::write(&pj, params_json("2019-12", "2020-01", "[90]")).unwrap();
    let rules = [(0u8, 0u8, 0u8), (0, 1, 1), (1, 0, 0), (9, 0, 0)];
    run(args(
        &cache,
        &pj,
        &out,
        Stage::Test,
        Some(rules_file(dir.path(), &rules)),
        Some(xd_file(dir.path(), XD)),
        2,
    ))
    .unwrap();
    let meta: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out.join("run-meta.json")).unwrap()).unwrap();
    (table(&out.join("daycells-test.csv")), meta)
}

/// Файл фандинга не покрывает окно удержания: сделка в `nret` не входит (фандинг не молчаливый ноль) и считается
/// в `run-meta.json`; метки (`n`, `hit`) от фандинга не зависят.
#[test]
fn trades_without_funding_coverage_are_dropped_and_counted() {
    let nret = |dc: &[Row], fam: &str| -> Vec<String> {
        pick(dc, &[("fam", fam.to_string())])
            .iter()
            .map(|r| r["nret"].clone())
            .collect()
    };
    let counts = |meta: &serde_json::Value| -> [Option<u64>; 4] {
        let fc = &meta["funding_coverage"];
        [
            fc["trades"].as_u64(),
            fc["trades_uncovered"].as_u64(),
            fc["contracts_with_uncovered"].as_u64(),
            fc["trades_uncovered_by_h"]["90"].as_u64(),
        ]
    };

    // полный файл — ничего не теряется: 4 правила × 2 горизонта
    let (dc, meta) = funding_case(|_| {});
    assert_eq!(dc.len(), 8);
    assert!(dc.iter().all(|r| r["nret"] == "1"));
    assert_eq!(counts(&meta), [Some(8), Some(0), Some(0), Some(0)]);

    // файл начинается в минуту 14 980: вход F1 (14 946) раньше первой ставки, вход остальных (15 001) позже
    let (dc, meta) = funding_case(|c| c.funding.retain(|f| f.0 >= 14_800));
    assert_eq!(nret(&dc, "0"), vec!["0"; 4]);
    assert_eq!(nret(&dc, "1"), vec!["1"; 2]);
    assert_eq!(nret(&dc, "9"), vec!["1"; 2]);
    assert!(
        dc.iter().all(|r| r["n"] == "1"),
        "метки от фандинга не зависят"
    );
    assert_eq!(counts(&meta), [Some(4), Some(4), Some(1), Some(2)]);
    assert_eq!(meta["funding_coverage"]["trades_uncovered_by_h"]["180"], 2);

    // файл кончился: последняя ставка 14 500, наибольший интервал 480 — выход (≥ 15 035) не покрыт нигде
    let (dc, meta) = funding_case(|c| c.funding.retain(|f| f.0 <= 14_500));
    assert_eq!(dc.len(), 8);
    assert!(dc.iter().all(|r| r["nret"] == "0" && r["n"] == "1"));
    assert_eq!(counts(&meta), [Some(0), Some(8), Some(1), Some(4)]);
}
