//! Тесты выгрузки срабатываний (`dump.rs`): известные ответы по каждой группе столбцов (время, круглые числа,
//! волатильность, контекст, исходы, база), отсутствие утечки в будущее, детерминизм по потокам, число строк против
//! `daycells`, сверка каждого столбца с прямым (наивным) расчётом на случайных контрактах.

use super::cache::{write_contract, Contract, OI_COLS};
use super::control::{self, sign_of, slot_t, TriggerRow};
use super::dump::{
    ign_header, nearest_level_bps, time_cols, Collector, IgnTail, Shared, DEC, FEATURE_COLS, HALF,
    HEADER, M125,
};
use super::episodes::find_episodes;
use super::feat;
use super::params::Params;
use super::play::PlayArgs;
use super::run::{run, RunArgs, Stage};
use super::types::{Cube, Episode, Rule, XdTable, NF};
use crate::statarb::panel::{GRID0_MS, MINUTE_MS};
use chrono::{DateTime, Datelike, NaiveDate, Timelike};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

const SEED: u64 = 7;
/// Порог хода `X_D` сценария пампа: `90·ε + 87,5·R` (см. `pump_contract`).
const XD: f64 = 0.0884;
/// Дрейф цены за минуту (ln) и добавка хода за минуту на 90-минутном участке пампа.
const EPS: f64 = 1.0e-5;
const R: f64 = 1.0e-3;
const T0: usize = 15_000;

// ---------- данные ----------

const TPL: &str = r#"{
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

fn params_json(fit: &str, test: &str, ds: &str) -> String {
    TPL.replace("@FIT@", fit)
        .replace("@TEST@", test)
        .replace("@DS@", ds)
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
fn lnp(t: usize, pumps: &[usize]) -> f64 {
    EPS * t as f64
        + R * pumps
            .iter()
            .map(|&p| (t as f64 - p as f64).clamp(0.0, 90.0))
            .sum::<f64>()
}

/// Контракт с пампами: цена `100·exp(lnp)`, OI-заливка за 55 минут до цены (первое срабатывание F1 в `T − 55`),
/// всплеск оборота в минуту `T` на 60 минут, фандинг `1e-4` в минуты `100 + 480·k`.
fn pump_contract(n: usize, pumps: &[usize]) -> Contract {
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

/// Случайное блуждание с рывками, дырами OI и фандингом (как в `run_tests`).
fn random_contract(seed: u64, days: usize) -> Contract {
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

fn put(dir: &Path, sym: &str, c: &Contract) {
    let mut f = std::io::BufWriter::new(fs::File::create(dir.join(format!("{sym}.pmc"))).unwrap());
    write_contract(&mut f, c).unwrap();
}

fn xd_file(dir: &Path, x: f64) -> PathBuf {
    let p = dir.join("xd-in.csv");
    fs::write(
        &p,
        format!("di,sign,x_d,n_windows\n0,1,{x},100\n0,-1,{x},100\n"),
    )
    .unwrap();
    p
}

fn rules_file(dir: &Path, rules: &[(u8, u8, u8)]) -> PathBuf {
    let items: Vec<String> = rules
        .iter()
        .map(|(f, w, t)| format!("{{\"fam\":{f},\"wi\":{w},\"ti\":{t},\"rank_lo\":1.5}}"))
        .collect();
    let p = dir.join("k.json");
    fs::write(&p, format!("[{}]", items.join(","))).unwrap();
    p
}

/// Аргументы прогона без правил, `X_D` и выгрузки (их задаёт вызывающий полями).
fn args(cache: &Path, params: &Path, out: &Path, stage: Stage, threads: usize) -> RunArgs {
    RunArgs {
        cache: cache.to_path_buf(),
        params: params.to_path_buf(),
        out: out.to_path_buf(),
        stage,
        rules: None,
        seed: SEED,
        oi_lag: 1,
        threads,
        xd: None,
        limit: None,
        exits: false,
        exits_units: super::exits::UnitsArgs::default(),
        dump_triggers: false,
        dump_features: false,
        play: PlayArgs::default(),
    }
}

type Row = BTreeMap<String, String>;

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

fn table(path: &Path) -> Vec<Row> {
    let text = fs::read_to_string(path).unwrap();
    let (head, body) = text.split_once('\n').unwrap();
    rows_from(head, body)
}

fn val(r: &Row, col: &str) -> f64 {
    r[col].parse().unwrap()
}

fn same(a: f64, b: f64) -> bool {
    (a.is_nan() && b.is_nan()) || (a - b).abs() <= 1.0e-7 * (1.0 + b.abs())
}

fn expect(r: &Row, col: &str, want: f64) {
    let got = val(r, col);
    assert!(same(got, want), "{col}: {got} против {want}");
}

fn bps_ln(a: f64, b: f64) -> f64 {
    (a / b).ln() * 1.0e4
}

/// Простая доходность `(a/b − 1)·1e4` — результат сделки лонга (допущение 11 шапки `dump.rs`); у шорта со знаком минус.
fn bps_simple(a: f64, b: f64) -> f64 {
    (a / b - 1.0) * 1.0e4
}

// ---------- время ----------

fn tmin(y: i32, mo: u32, d: u32, h: u32, mi: u32) -> i64 {
    NaiveDate::from_ymd_opt(y, mo, d)
        .unwrap()
        .and_hms_opt(h, mi, 0)
        .unwrap()
        .and_utc()
        .timestamp()
        / 60
        - GRID0_MS / MINUTE_MS
}

#[test]
fn time_columns_at_day_and_funding_boundaries() {
    // (минута, час, минута часа, день недели, мин. с начала суток, до отметки, от отметки, месяц)
    let cases = [
        (tmin(2020, 1, 5, 23, 59), 23, 59, 6, 1439, 1, 479, 202001),
        (tmin(2020, 1, 6, 0, 0), 0, 0, 0, 0, 480, 0, 202001),
        (tmin(2020, 1, 6, 7, 59), 7, 59, 0, 479, 1, 479, 202001),
        (tmin(2020, 1, 6, 8, 0), 8, 0, 0, 480, 480, 0, 202001),
        (tmin(2020, 1, 6, 8, 1), 8, 1, 0, 481, 479, 1, 202001),
        (tmin(2020, 1, 6, 15, 59), 15, 59, 0, 959, 1, 479, 202001),
        (tmin(2020, 1, 6, 16, 0), 16, 0, 0, 960, 480, 0, 202001),
        (tmin(2019, 12, 31, 23, 59), 23, 59, 1, 1439, 1, 479, 201912),
        (tmin(2020, 2, 29, 12, 30), 12, 30, 5, 750, 210, 270, 202002),
    ];
    for (t, h, m, dow, since_day, to_fund, since_fund, month) in cases {
        let c = time_cols(t);
        assert_eq!(
            (
                c.hour,
                c.minute,
                c.dow,
                c.since_day,
                c.to_fund,
                c.since_fund,
                c.month
            ),
            (h, m, dow, since_day, to_fund, since_fund, month),
            "t_f {t}"
        );
    }
    // сутки считаются от 1970-01-01: 2020-01-01 — 18262-е
    assert_eq!(time_cols(0).day, 18262);
    assert_eq!(time_cols(-1).day, 18261);

    // сверка с календарём chrono на сетке минут через границы суток, месяцев и года
    let epoch = NaiveDate::from_ymd_opt(1970, 1, 1).unwrap();
    for t in (-40_000i64..800_000).step_by(37) {
        let c = time_cols(t);
        let dt = DateTime::from_timestamp_millis(GRID0_MS + t * MINUTE_MS).unwrap();
        let hm = i64::from(dt.hour() * 60 + dt.minute());
        assert_eq!(c.hour, i64::from(dt.hour()), "t_f {t}");
        assert_eq!(c.minute, i64::from(dt.minute()), "t_f {t}");
        assert_eq!(
            c.dow,
            i64::from(dt.weekday().num_days_from_monday()),
            "t_f {t}"
        );
        assert_eq!(c.month, dt.year() * 100 + dt.month() as i32, "t_f {t}");
        assert_eq!(
            c.day,
            dt.date_naive().signed_duration_since(epoch).num_days(),
            "t_f {t}"
        );
        assert_eq!(c.since_day, hm, "t_f {t}");
        assert_eq!(c.since_fund, hm % 480, "t_f {t}");
        assert_eq!(c.to_fund, 480 - hm % 480, "t_f {t}");
    }
}

// ---------- круглые числа ----------

#[test]
fn round_level_distances_are_signed_and_pick_nearest() {
    // (цена, d_dec, d_half, d_125): положительное — уровень выше цены
    let cases = [
        (
            0.97,
            bps_ln(1.0, 0.97),
            bps_ln(1.0, 0.97),
            bps_ln(1.0, 0.97),
        ),
        (
            1.03,
            bps_ln(1.0, 1.03),
            bps_ln(1.0, 1.03),
            bps_ln(1.0, 1.03),
        ),
        (4.9, bps_ln(10.0, 4.9), bps_ln(5.0, 4.9), bps_ln(5.0, 4.9)),
        (
            0.0099,
            bps_ln(0.01, 0.0099),
            bps_ln(0.01, 0.0099),
            bps_ln(0.01, 0.0099),
        ),
        // наборы уровней различаются: 1,45 — 125 берёт 2 сверху, остальные 1 снизу
        (
            1.45,
            bps_ln(1.0, 1.45),
            bps_ln(1.0, 1.45),
            bps_ln(2.0, 1.45),
        ),
        (3.0, bps_ln(1.0, 3.0), bps_ln(5.0, 3.0), bps_ln(2.0, 3.0)),
        // цена на уровне: 0 там, где уровень есть в наборе
        (5.0, bps_ln(10.0, 5.0), 0.0, 0.0),
        (1000.0, 0.0, 0.0, 0.0),
        (0.001, 0.0, 0.0, 0.0),
        (2.0, bps_ln(1.0, 2.0), bps_ln(1.0, 2.0), 0.0),
        (
            123_456.0,
            bps_ln(1e5, 123_456.0),
            bps_ln(1e5, 123_456.0),
            bps_ln(1e5, 123_456.0),
        ),
        (
            3.45e-5,
            bps_ln(1e-4, 3.45e-5),
            bps_ln(5e-5, 3.45e-5),
            bps_ln(5e-5, 3.45e-5),
        ),
    ];
    for (p, dec, half, m125) in cases {
        for (name, mants, want) in [
            ("dec", &DEC[..], dec),
            ("half", &HALF[..], half),
            ("125", &M125[..], m125),
        ] {
            let got = nearest_level_bps(p, mants);
            assert!(
                same(got, want),
                "цена {p}, набор {name}: {got} против {want}"
            );
        }
    }
    for bad in [0.0, -1.0, f64::NAN, f64::INFINITY] {
        assert!(nearest_level_bps(bad, &DEC).is_nan(), "цена {bad}");
    }
}

// ---------- известные ответы по группам столбцов (Collector на ручных данных) ----------

const FM: i64 = 40;
const NMIN: usize = 4000;
const P0: f64 = 4.9;
const TF: i64 = 2000;

/// Множитель цены на пути после входа (минута `k` от входа `TF + 1`): рост до 1,1 (плато 300…310), спад до 0,9 в
/// минуте 900 и возврат к 1,0 к минуте 1440.
fn shape(k: i64) -> f64 {
    if k < 300 {
        1.0 + 0.1 * k as f64 / 300.0
    } else if k <= 310 {
        1.1
    } else if k < 900 {
        1.1 - 0.2 * (k - 310) as f64 / 590.0
    } else if k == 900 {
        0.9
    } else {
        0.9 + 0.1 * (k - 900) as f64 / 540.0
    }
}

/// Цена минуты `m`: плоская `P0` с одиночными выбросами до входа, путь `shape` после.
fn pf(m: i64) -> f64 {
    if m > TF + 1 {
        return P0 * shape(m - TF - 1);
    }
    let mult = match m {
        1000 => 0.98,
        1500 => 1.05,
        1640 => 0.96,
        1940 => 1.03,
        1990 => 1.02,
        _ => 1.0,
    };
    P0 * mult
}

fn unit_contract() -> Contract {
    let mut qvol = vec![2.0f32; NMIN];
    qvol[(561 - FM) as usize] = 1000.0; // первая минута окна qv1440 при t_f = 2000
    qvol[(560 - FM) as usize] = 7000.0; // минута перед окном
    qvol[(2001 - FM) as usize] = 555.0; // минута входа — после t_f
    Contract {
        first_min: FM,
        close: (0..NMIN as i64).map(|i| pf(FM + i)).collect(),
        vol: vec![1.0; NMIN],
        qvol,
        trades: vec![1.0; NMIN],
        ..Default::default()
    }
}

fn btc_px(m: i64) -> f64 {
    50.0 * (0.0002 * m as f64).exp()
}

fn btc_contract() -> Contract {
    Contract {
        first_min: 30,
        close: (0..4000i64).map(|i| btc_px(30 + i)).collect(),
        vol: vec![1.0; 4000],
        qvol: vec![1.0; 4000],
        trades: vec![1.0; 4000],
        ..Default::default()
    }
}

/// Куб с моментами C2a `(минута, знак)` (знак задаёт `r`) и σ в слоте `t_f = 2000`.
fn unit_cube(moments: &[(i64, f64)]) -> Cube {
    let (n, slot0) = (800usize, 8i64);
    let mut r = vec![f32::NAN; n];
    for &(t, s) in moments {
        r[(t / 5 - slot0) as usize] = (0.1 * s) as f32;
    }
    let mut sigma = vec![f32::NAN; n];
    sigma[(TF / 5 - slot0) as usize] = 0.001;
    Cube {
        slot0,
        n,
        windows: vec![5],
        z: vec![f32::NAN; NF * n],
        r,
        sigma,
    }
}

fn trig(rule: (u8, u8, u8), t_f: i64, sign: i8, running: bool) -> TriggerRow {
    TriggerRow {
        rule: Rule {
            fam: rule.0,
            wi: rule.1,
            ti: rule.2,
        },
        t_f,
        month: control::yyyymm(t_f),
        sign,
        label: Vec::new(),
        c2a_hit: Vec::new(),
        c2a_n: Vec::new(),
        c2a_drawn: 0,
        in_episode_running: running,
    }
}

fn keys(r: &Row) -> (i64, i64, i64, i64, i64) {
    let g = |c: &str| r[c].parse::<i64>().unwrap();
    (g("t_f"), g("fam"), g("wi"), g("ti"), g("sign"))
}

#[test]
fn collector_columns_have_known_answers() {
    let dir = tempfile::tempdir().unwrap();
    put(dir.path(), "BTCUSDT", &btc_contract());
    let btc_path = dir.path().join("BTCUSDT.pmc");
    let sh = Shared::load(&btc_path, None).unwrap();
    let moms = [(1950i64, 1.0f64), (1945, -1.0), (3500, 1.0), (4030, 1.0)];
    let cube = unit_cube(&moms);
    let c = unit_contract();
    let cf = c.close_ff();
    let slots = [382u32, 381, 692, 798, 100];
    let mut col = Collector::new(&sh, "XYZUSDT", &c, &cf, &cube);
    // порядок добавления не совпадает с порядком записи; знак 0 — строки нет
    col.add(&trig((0, 0, 0), 3000, 1, false), &[]);
    col.add(&trig((1, 0, 0), TF, 1, false), &[]);
    col.add(&trig((0, 0, 0), TF, 1, true), &slots);
    col.add(&trig((0, 0, 0), TF, -1, false), &[]);
    col.add(&trig((0, 0, 0), 4000, 1, false), &[]);
    col.add(&trig((0, 0, 0), TF, 0, true), &slots);
    let bytes = col.finish();
    let rows = rows_from(HEADER, std::str::from_utf8(&bytes).unwrap());
    let order: Vec<_> = rows.iter().map(keys).collect();
    assert_eq!(
        order,
        vec![
            (TF, 0, 0, 0, -1),
            (TF, 0, 0, 0, 1),
            (TF, 1, 0, 0, 1),
            (3000, 0, 0, 0, 1),
            (4000, 0, 0, 0, 1)
        ]
    );
    let (dn, up) = (&rows[0], &rows[1]);
    assert_eq!(up["sym"], "XYZUSDT");

    // ключ и время: t_f = 2000 — 2020-01-02 09:20 UTC, четверг
    for (col, want) in [
        ("day", "18263"),
        ("month", "202001"),
        ("hour_utc", "9"),
        ("minute_of_hour", "20"),
        ("dow", "3"),
        ("min_since_day", "560"),
        ("min_to_fund8", "400"),
        ("min_since_fund8", "80"),
    ] {
        assert_eq!(up[col], want, "{col}");
    }

    // круглые числа: вход P0 = 4,9; пик лонга 5,39, дно лонга 4,41 (у шорта наоборот)
    let (hi, lo) = (P0 * 1.1, P0 * 0.9);
    expect(up, "d_dec", bps_ln(10.0, P0));
    expect(up, "d_half", bps_ln(5.0, P0));
    expect(up, "d_125", bps_ln(5.0, P0));
    expect(up, "pk_d_dec", bps_ln(10.0, hi));
    expect(up, "pk_d_half", bps_ln(5.0, hi));
    expect(up, "pk_d_125", bps_ln(5.0, hi));
    expect(dn, "pk_d_dec", bps_ln(10.0, lo));
    expect(dn, "pk_d_half", bps_ln(5.0, lo));
    expect(dn, "pk_d_125", bps_ln(5.0, lo));

    // волатильность по close_ff до t_f включительно
    let sq = |x: f64| x.ln().powi(2);
    expect(up, "rv60_bps", (sq(1.03) + 2.0 * sq(1.02)).sqrt() * 1.0e4);
    let all = [0.98, 1.05, 0.96, 1.03, 1.02];
    expect(
        up,
        "rv1440_bps",
        (2.0 * all.iter().map(|&x| sq(x)).sum::<f64>()).sqrt() * 1.0e4,
    );
    expect(up, "rng1440_bps", bps_ln(1.05, 0.96));
    expect(
        up,
        "sigma_h_bps",
        f64::from(0.001f32) * 60f64.sqrt() * 1.0e4,
    );

    // контекст
    expect(up, "ret_prev60_bps", bps_ln(1.0, 1.03));
    expect(up, "ret_prev360_bps", bps_ln(1.0, 0.96));
    expect(up, "drop360_bps", bps_ln(1.0, 1.03));
    assert_eq!(
        (up["ep_running"].as_str(), dn["ep_running"].as_str()),
        ("1", "0")
    );
    expect(up, "age_days", (TF - FM) as f64 / 1440.0);
    expect(up, "qv1440_usd", 1439.0 * 2.0 + 1000.0);
    expect(up, "btc_prev60_bps", 0.0002 * 60.0 * 1.0e4);
    expect(up, "btc_next360_bps", 0.0002 * 360.0 * 1.0e4);

    // исходы: знаковые ПРОСТЫЕ доходности (не ln), путь, минуты экстремумов (первая из равных на плато 300…310)
    for (side, row) in [(1.0, up), (-1.0, dn)] {
        for h in [5i64, 15, 30, 60, 120, 240, 360, 720, 1440] {
            expect(row, &format!("gross_{h}"), side * (shape(h) - 1.0) * 1.0e4);
        }
    }
    expect(up, "mfe_bps", (1.1f64 - 1.0) * 1.0e4);
    expect(up, "mae_bps", (0.9f64 - 1.0) * 1.0e4);
    assert_eq!(
        (up["mfe_min"].as_str(), up["mae_min"].as_str()),
        ("300", "900")
    );
    expect(up, "mfe_price", hi);
    expect(dn, "mfe_bps", -(0.9f64 - 1.0) * 1.0e4);
    expect(dn, "mae_bps", -(1.1f64 - 1.0) * 1.0e4);
    // шорт на тех же ценах — ровно минус лонг (а не «минус ln»): исходы и потолки зеркальны побитово
    for h in [5i64, 15, 30, 60, 120, 240, 360, 720, 1440] {
        let c = format!("gross_{h}");
        assert_eq!(val(dn, &c), -val(up, &c), "{c}");
    }
    assert_eq!(val(dn, "mfe_bps"), -val(up, "mae_bps"));
    assert_eq!(val(dn, "mae_bps"), -val(up, "mfe_bps"));
    assert_eq!(
        (dn["mfe_min"].as_str(), dn["mae_min"].as_str()),
        ("900", "300")
    );
    expect(dn, "mfe_price", lo);

    // база: среднее по моментам со своими знаками; моменты без цены выхода не входят (1440 — три момента из четырёх)
    let want_base = |h: i64| {
        let v: Vec<f64> = moms
            .iter()
            .filter(|&&(t, _)| t + 1 + h < FM + NMIN as i64)
            .map(|&(t, s)| s * (pf(t + 1 + h) / pf(t + 1) - 1.0) * 1.0e4)
            .collect();
        v.iter().sum::<f64>() / v.len() as f64
    };
    expect(up, "b_gross_60", want_base(60));
    expect(up, "b_gross_360", want_base(360));
    expect(up, "b_gross_1440", want_base(1440));
    assert!(val(up, "b_gross_1440").is_finite());
    // момент (4030) без цены выхода ни на одном горизонте и момент без знака в среднее не вошли
    assert_eq!(
        moms.iter()
            .filter(|&&(t, _)| t + 1 + 60 < FM + NMIN as i64)
            .count(),
        3
    );
    for r in [dn, &rows[2]] {
        for c in ["b_gross_60", "b_gross_360", "b_gross_1440"] {
            assert!(val(r, c).is_nan(), "{c} без моментов");
        }
    }

    // у края данных: исходы без цен — NaN, путь неполный — mfe/mae NaN; BTC и σ без значений — NaN
    let edge = &rows[3];
    let p3 = pf(3001);
    expect(edge, "d_dec", bps_ln(10.0, p3));
    expect(edge, "d_half", bps_ln(5.0, p3));
    for h in [5i64, 15, 30, 60, 120, 240, 360, 720] {
        expect(
            edge,
            &format!("gross_{h}"),
            (pf(3001 + h) / p3 - 1.0) * 1.0e4,
        );
    }
    for c in [
        "gross_1440",
        "mfe_bps",
        "mfe_min",
        "mae_bps",
        "mae_min",
        "mfe_price",
        "pk_d_dec",
        "pk_d_half",
        "pk_d_125",
        "sigma_h_bps",
    ] {
        assert!(val(edge, c).is_nan(), "{c} у края данных");
    }
    expect(edge, "qv1440_usd", 1439.0 * 2.0 + 555.0);
    expect(edge, "btc_next360_bps", 0.0002 * 360.0 * 1.0e4);
    // BTC кончается в минуте 4029: от входа 4001 до 4361 данных нет
    expect(&rows[4], "btc_prev60_bps", 0.0002 * 60.0 * 1.0e4);
    assert!(val(&rows[4], "btc_next360_bps").is_nan());

    // эталон BTC усечён по концу стадии (3300) либо файла нет — btc_* NaN
    let sh_clip = Shared::load(&btc_path, Some(3300)).unwrap();
    let sh_none = Shared::load(&dir.path().join("none.pmc"), None).unwrap();
    for (sh, prev_known) in [(&sh_clip, true), (&sh_none, false)] {
        let mut col = Collector::new(sh, "XYZUSDT", &c, &cf, &cube);
        col.add(&trig((0, 0, 0), 3000, 1, false), &[]);
        let bytes = col.finish();
        let r = rows_from(HEADER, std::str::from_utf8(&bytes).unwrap());
        assert_eq!(r.len(), 1);
        if prev_known {
            expect(&r[0], "btc_prev60_bps", 0.0002 * 60.0 * 1.0e4);
        } else {
            assert!(val(&r[0], "btc_prev60_bps").is_nan());
        }
        assert!(val(&r[0], "btc_next360_bps").is_nan());
    }
    // колонок в заголовке и в строке поровну, `-0` не печатается
    assert_eq!(HEADER.split(',').count(), 49);
    assert!(!std::str::from_utf8(&bytes).unwrap().contains("-0,"));
}

// ---------- сценарий пампа через `run` ----------

/// Прогон стадии `test` на одном контракте, выгрузка срабатываний правил `rules` (только F1).
fn run_dump(c: &Contract, rules: &[(u8, u8, u8)], threads: usize) -> Vec<Row> {
    let dir = tempfile::tempdir().unwrap();
    let (cache, out) = (dir.path().join("cache"), dir.path().join("out"));
    fs::create_dir_all(&cache).unwrap();
    put(&cache, "ABCUSDT", c);
    let pj = dir.path().join("params.json");
    fs::write(&pj, params_json("2019-12", "2020-01", "[90]")).unwrap();
    let mut a = args(&cache, &pj, &out, Stage::Test, threads);
    a.rules = Some(rules_file(dir.path(), rules));
    a.xd = Some(xd_file(dir.path(), XD));
    a.dump_triggers = true;
    // признаки направления включены: проверки утечки ниже сравнивают и их
    a.dump_features = true;
    run(a).unwrap();
    table(&out.join("triggers-test.csv"))
}

fn pick_row(rows: &[Row], rule: (u8, u8, u8), t_f: i64) -> Row {
    rows.iter()
        .find(|r| {
            r["fam"] == rule.0.to_string()
                && r["wi"] == rule.1.to_string()
                && r["ti"] == rule.2.to_string()
                && r["t_f"] == t_f.to_string()
        })
        .unwrap_or_else(|| panic!("нет строки {rule:?} t_f {t_f}"))
        .clone()
}

#[test]
fn pump_scenario_dump_matches_hand_calculation() {
    let pumps = [T0, 16_500];
    let c = pump_contract(17_280, &pumps);
    let rows = run_dump(&c, &[(0, 0, 0), (0, 1, 1)], 2);
    // F1 w5 θ2: по одному первому срабатыванию на заливку (за 55 минут до цены), обе стороны — лонг
    let f1: Vec<_> = rows
        .iter()
        .filter(|r| r["fam"] == "0" && r["wi"] == "0" && r["ti"] == "0")
        .collect();
    assert_eq!(f1.len(), 2, "{f1:?}");
    let (a, b) = (
        pick_row(&rows, (0, 0, 0), 14_945),
        pick_row(&rows, (0, 0, 0), 16_445),
    );
    let cube = feat::build_cube(
        &c,
        &Params::from_json(&params_json("2019-12", "2020-01", "[90]")).unwrap(),
        1,
    );

    // первая заливка: путь полностью в данных
    assert_eq!(
        (
            a["sym"].as_str(),
            a["sign"].as_str(),
            a["day"].as_str(),
            a["month"].as_str()
        ),
        ("ABCUSDT", "1", "18272", "202001")
    );
    for (col, want) in [
        ("hour_utc", "9"),
        ("minute_of_hour", "5"),
        ("dow", "5"),
        ("min_since_day", "545"),
        ("min_to_fund8", "415"),
        ("min_since_fund8", "65"),
        ("ep_running", "0"),
        ("mfe_min", "1440"),
        ("mae_min", "0"),
    ] {
        assert_eq!(a[col], want, "{col}");
    }
    let (e, t_f) = (14_946usize, 14_945usize);
    // вход на дрейфе ниже уровня 100: расстояние до 100 одинаково для всех наборов
    let d_entry = -lnp(e, &pumps) * 1.0e4;
    for col in ["d_dec", "d_half", "d_125"] {
        expect(&a, col, d_entry);
    }
    let d_peak = -lnp(e + 1440, &pumps) * 1.0e4;
    for col in ["pk_d_dec", "pk_d_half", "pk_d_125"] {
        expect(&a, col, d_peak);
    }
    expect(&a, "rv60_bps", 60f64.sqrt() * EPS * 1.0e4);
    expect(&a, "rv1440_bps", 1440f64.sqrt() * EPS * 1.0e4);
    expect(&a, "rng1440_bps", 1439.0 * EPS * 1.0e4);
    expect(
        &a,
        "sigma_h_bps",
        f64::from(cube.sigma[t_f / 5]) * 60f64.sqrt() * 1.0e4,
    );
    expect(&a, "ret_prev60_bps", 60.0 * EPS * 1.0e4);
    expect(&a, "ret_prev360_bps", 360.0 * EPS * 1.0e4);
    expect(&a, "drop360_bps", 0.0);
    expect(&a, "age_days", t_f as f64 / 1440.0);
    expect(&a, "qv1440_usd", 1440.0 * 10.0);
    assert!(val(&a, "btc_prev60_bps").is_nan() && val(&a, "btc_next360_bps").is_nan());
    for h in [5usize, 15, 30, 60, 120, 240, 360, 720, 1440] {
        expect(
            &a,
            &format!("gross_{h}"),
            ((lnp(e + h, &pumps) - lnp(e, &pumps)).exp() - 1.0) * 1.0e4,
        );
    }
    // цена растёт монотонно: пик в конце пути, просадки нет
    expect(
        &a,
        "mfe_bps",
        ((lnp(e + 1440, &pumps) - lnp(e, &pumps)).exp() - 1.0) * 1.0e4,
    );
    expect(&a, "mae_bps", 0.0);
    expect(&a, "mfe_price", c.close[e + 1440]);

    // вторая заливка: путь 1440 минут от входа 16446 уходит за конец данных (17279)
    assert_eq!(b["ep_running"], "0");
    let e2 = 16_446usize;
    for col in ["d_dec", "d_half", "d_125"] {
        expect(&b, col, -lnp(e2, &pumps) * 1.0e4);
    }
    for h in [5usize, 15, 30, 60, 120, 240, 360, 720] {
        expect(
            &b,
            &format!("gross_{h}"),
            ((lnp(e2 + h, &pumps) - lnp(e2, &pumps)).exp() - 1.0) * 1.0e4,
        );
    }
    for col in [
        "gross_1440",
        "mfe_bps",
        "mfe_min",
        "mae_bps",
        "mae_min",
        "mfe_price",
        "pk_d_dec",
        "pk_d_half",
        "pk_d_125",
    ] {
        assert!(val(&b, col).is_nan(), "{col}");
    }
    // окна 1440 минут захватывают конец первой заливки
    let t2 = 16_445usize;
    let rv2: f64 = (t2 - 1439..=t2)
        .map(|j| (lnp(j, &pumps) - lnp(j - 1, &pumps)).powi(2))
        .sum::<f64>()
        .sqrt()
        * 1.0e4;
    expect(&b, "rv1440_bps", rv2);
    expect(
        &b,
        "rng1440_bps",
        (lnp(t2, &pumps) - lnp(t2 - 1439, &pumps)) * 1.0e4,
    );
    expect(&b, "qv1440_usd", 54.0 * 1000.0 + (1440.0 - 54.0) * 10.0);
    // оба правила F1 сработали в те же минуты
    assert_eq!(rows.iter().filter(|r| r["fam"] == "0").count(), 4);
}

// ---------- отсутствие утечки ----------

/// Столбцы, зависящие от данных после `t_f` (исходы, база, контекст исхода) или от эпизодов, найденных задним числом.
fn is_future(col: &str) -> bool {
    col.starts_with("gross_")
        || col.starts_with("mfe_")
        || col.starts_with("mae_")
        || col.starts_with("pk_d_")
        || col.starts_with("b_gross_")
        || col == "btc_next360_bps"
        || col == "ep_running"
}

fn past_equal(x: &Row, y: &Row, skip: &[&str]) {
    for (col, v) in x {
        if is_future(col) || skip.contains(&col.as_str()) {
            continue;
        }
        assert_eq!(v, &y[col], "{col} (t_f {})", x["t_f"]);
    }
}

#[test]
fn data_after_trigger_does_not_change_attributes_and_data_before_does() {
    let rules = [(0u8, 0u8, 0u8), (0, 1, 1)];
    let a = pump_contract(17_280, &[8_000, T0]);
    let base = run_dump(&a, &rules, 1);
    let m_t = 14_990usize; // вход последнего срабатывания (14 946) и всё, что в строках до него, — до этой минуты
    let early = |rows: &[Row]| -> Vec<Row> {
        rows.iter()
            .filter(|r| r["t_f"].parse::<usize>().unwrap() < m_t)
            .cloned()
            .collect()
    };
    let base_early = early(&base);
    assert_eq!(base_early.len(), 4, "две заливки × два правила");

    // (а) всё после m_t меняется: цены, оборот, сделки, OI, фандинг
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
    let after = early(&run_dump(&b, &rules, 1));
    assert_eq!(base_early.len(), after.len());
    for (x, y) in base_early.iter().zip(&after) {
        past_equal(x, y, &[]);
    }
    // исходы при этом меняются (минута 15006 уже за m_t)
    let (x, y) = (
        pick_row(&base, (0, 0, 0), 14_945),
        pick_row(&after, (0, 0, 0), 14_945),
    );
    assert!(
        (val(&x, "gross_60") - val(&y, "gross_60")).abs() > 1000.0,
        "{} против {}",
        x["gross_60"],
        y["gross_60"]
    );

    // (б) данные до t_f = 14945 меняются — меняются и соответствующие атрибуты, ключи и другие строки — нет
    // минута t_f − 60: ret_prev60 и rv60 меняются, окно 360 минут по концам, drop и qv — нет
    let fix = Fixture {
        a: &a,
        base: &base,
        rules: &rules,
    };
    probe(
        &fix,
        &|c: &mut Contract| c.close[14_885] *= 1.01,
        &["ret_prev60_bps", "rv60_bps"],
        &["qv1440_usd", "ret_prev360_bps", "age_days"],
    );
    probe(
        &fix,
        &|c: &mut Contract| c.qvol[14_000] += 5000.0,
        &["qv1440_usd"],
        &[
            "rv60_bps",
            "rv1440_bps",
            "rng1440_bps",
            "ret_prev60_bps",
            "drop360_bps",
        ],
    );
    probe(
        &fix,
        &|c: &mut Contract| c.close[14_500] *= 1.2,
        &["rng1440_bps", "rv1440_bps"],
        &[
            "rv60_bps",
            "ret_prev60_bps",
            "ret_prev360_bps",
            "drop360_bps",
            "qv1440_usd",
        ],
    );
}

/// Исходный контракт, его строки выгрузки и правила прогона для `probe`.
struct Fixture<'a> {
    a: &'a Contract,
    base: &'a [Row],
    rules: &'a [(u8, u8, u8)],
}

/// Прогон с правкой данных до `t_f = 14945`: ключи и число строк те же, `changed` меняются, `same_cols` — нет,
/// строка раньше правки (t_f = 7945) не меняется ни в одном столбце прошлого.
fn probe(f: &Fixture, edit: &dyn Fn(&mut Contract), changed: &[&str], same_cols: &[&str]) {
    let mut c = f.a.clone();
    edit(&mut c);
    let rows = run_dump(&c, f.rules, 1);
    assert_eq!(rows.len(), f.base.len(), "число строк");
    for (x, y) in f.base.iter().zip(&rows) {
        assert_eq!(keys(x), keys(y), "ключ строки");
        if x["t_f"] == "14945" {
            for col in changed {
                assert_ne!(x[*col], y[*col], "{col} должен измениться");
            }
            for col in same_cols {
                assert_eq!(x[*col], y[*col], "{col} не должен меняться");
            }
        } else if x["t_f"] == "7945" {
            past_equal(x, y, &[]);
        }
    }
}

// ---------- c2a_moments_with == c2a_with ----------

#[test]
fn moments_variant_gives_same_rows_and_consistent_moments() {
    let text = params_json("2019-12", "2020-01", "[90]");
    let p = Params::from_json(&text).unwrap();
    let c = pump_contract(17_280, &[T0, 16_500]);
    let (cube, cf) = (feat::build_cube(&c, &p, 1), c.close_ff());
    let inp = control::Input {
        sym: "ABCUSDT",
        cube: &cube,
        cf: &cf,
        first_min: 0,
        eps: &[],
    };
    let xd = XdTable { x: vec![[XD, XD]] };
    let rules = feat::rules(&p);
    let plain = control::c2a(&inp, &rules, &xd, &p, SEED).unwrap();
    assert!(!plain.is_empty());
    let (mut rows, mut moms) = (Vec::new(), Vec::new());
    control::c2a_moments_with(
        &inp,
        &rules,
        &xd,
        &p,
        SEED,
        true,
        |r| feat::first_triggers(&cube, r, &p),
        |row, m| {
            rows.push(row);
            moms.push(m.to_vec());
        },
    )
    .unwrap();
    assert_eq!(plain, rows);
    // флаг выключен — срезы пусты, строки те же
    let mut empty = 0;
    control::c2a_moments_with(
        &inp,
        &rules,
        &xd,
        &p,
        SEED,
        false,
        |r| feat::first_triggers(&cube, r, &p),
        |row, m| {
            assert!(m.is_empty());
            empty += 1;
            assert_eq!(row, plain[empty - 1]);
        },
    )
    .unwrap();
    assert_eq!(empty, plain.len());
    // моменты: столько, сколько выбрано; тот же месяц; метки моментов дают c2a_n / c2a_hit
    let labels = control::Labels::new(&cf, 0, &xd, &p).unwrap();
    let mut with_moments = 0;
    for (row, m) in rows.iter().zip(&moms) {
        assert_eq!(m.len(), usize::from(row.c2a_drawn));
        with_moments += usize::from(!m.is_empty());
        for (hi, _) in p.label_forward.h_min.iter().enumerate() {
            let (mut n, mut hit) = (0u16, 0u16);
            for &j in m {
                let (tj, wi) = (slot_t(&cube, j as usize), row.rule.wi as usize);
                assert_eq!(control::yyyymm(tj), row.month);
                match labels.get(tj, sign_of(cube.rat(wi, j as usize)), hi) {
                    control::Label::Hit => {
                        n += 1;
                        hit += 1;
                    }
                    control::Label::Miss => n += 1,
                    control::Label::NoData => {}
                }
            }
            assert_eq!((n, hit), (row.c2a_n[hi], row.c2a_hit[hi]), "hi {hi}");
        }
    }
    assert!(with_moments > 0);
}

// ---------- случайные контракты: детерминизм, число строк, порядок, сверка столбцов ----------

const SYMS: [&str; 5] = ["AAAUSDT", "BBBUSDT", "BTCUSDT", "CCCUSDT", "DDDUSDT"];
const XD_RAND: f64 = 0.05;
/// Минут в сутках и в контракте из 14 суток (единицы теста).
const DAY: i64 = 1440;
const N_RAND: i64 = 14 * DAY;

fn random_set(dir: &Path) -> (PathBuf, PathBuf) {
    random_set_with(dir, &|_, _| {})
}

/// `random_set`; `edit(индекс контракта, контракт)` правит данные перед записью в кэш.
fn random_set_with(dir: &Path, edit: &dyn Fn(usize, &mut Contract)) -> (PathBuf, PathBuf) {
    let cache = dir.join("cache");
    fs::create_dir_all(&cache).unwrap();
    for (i, sym) in SYMS.iter().enumerate() {
        let mut c = random_contract(11 + i as u64, 14);
        edit(i, &mut c);
        put(&cache, sym, &c);
    }
    let pj = dir.join("params.json");
    fs::write(&pj, params_json("2020-01", "2020-02", "[90]")).unwrap();
    (cache, pj)
}

fn fit_run(dir: &Path, cache: &Path, pj: &Path, name: &str, threads: usize, dump: bool) -> PathBuf {
    fit_run_with(dir, cache, pj, name, threads, (dump, false))
}

/// `fit_run`; `flags` — (`--dump-triggers`, `--dump-features`).
fn fit_run_with(
    dir: &Path,
    cache: &Path,
    pj: &Path,
    name: &str,
    threads: usize,
    flags: (bool, bool),
) -> PathBuf {
    let out = dir.join(name);
    let mut a = args(cache, pj, &out, Stage::Fit, threads);
    a.xd = Some(xd_file(dir, XD_RAND));
    (a.dump_triggers, a.dump_features) = flags;
    run(a).unwrap();
    out
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
fn dump_is_byte_identical_for_any_thread_count_and_leaves_other_tables_alone() {
    let dir = tempfile::tempdir().unwrap();
    let (cache, pj) = random_set(dir.path());
    let outs: Vec<PathBuf> = [1usize, 4, 3]
        .iter()
        .map(|&th| fit_run(dir.path(), &cache, &pj, &format!("out{th}"), th, true))
        .collect();
    let first = fs::read(outs[0].join("triggers-fit.csv")).unwrap();
    assert!(
        first.iter().filter(|&&b| b == b'\n').count() > 50,
        "мало строк выгрузки"
    );
    for o in &outs[1..] {
        assert_eq!(
            first,
            fs::read(o.join("triggers-fit.csv")).unwrap(),
            "triggers-fit.csv: потоки 1 против другого"
        );
    }

    // без флага файла нет, остальные таблицы и счётчики те же байт в байт
    let off = fit_run(dir.path(), &cache, &pj, "off", 2, false);
    assert!(!off.join("triggers-fit.csv").exists());
    for f in FILES {
        assert_eq!(
            fs::read(outs[0].join(f)).unwrap(),
            fs::read(off.join(f)).unwrap(),
            "{f}: с выгрузкой и без"
        );
    }
    let meta = |o: &Path| -> serde_json::Value {
        serde_json::from_str(&fs::read_to_string(o.join("run-meta.json")).unwrap()).unwrap()
    };
    let (m_on, m_off) = (meta(&outs[0]), meta(&off));
    for key in [
        "contracts",
        "episodes",
        "triggers",
        "triggers_no_sign",
        "c2b_moments",
        "universe",
        "lag_observability",
        "funding_coverage",
    ] {
        assert_eq!(m_on[key], m_off[key], "{key}");
    }

    // заголовок, число строк = срабатывания со знаком, порядок (контракт, t_f, fam, wi, ti, sign)
    let text = fs::read_to_string(outs[0].join("triggers-fit.csv")).unwrap();
    assert_eq!(text.lines().next().unwrap(), HEADER);
    let rows = table(&outs[0].join("triggers-fit.csv"));
    let (all, no_sign) = (
        m_on["triggers"].as_u64().unwrap(),
        m_on["triggers_no_sign"].as_u64().unwrap(),
    );
    assert_eq!(rows.len() as u64, all - no_sign);
    let key = |r: &Row| (r["sym"].clone(), keys(r));
    for w in rows.windows(2) {
        assert!(
            key(&w[0]) < key(&w[1]),
            "порядок: {:?} {:?}",
            key(&w[0]),
            key(&w[1])
        );
    }

    // по (правило, сторона): строк с определённой меткой = Σ n по daycells (для обоих H: D метки одно, 90)
    type K = (String, String, String, String);
    let k_of = |r: &Row| -> K {
        (
            r["fam"].clone(),
            r["wi"].clone(),
            r["ti"].clone(),
            r["sign"].clone(),
        )
    };
    let mut want: BTreeMap<K, u64> = BTreeMap::new();
    for r in &rows {
        if keys(r).0 + 1 + 90 <= N_RAND {
            *want.entry(k_of(r)).or_default() += 1;
        }
    }
    assert!(!want.is_empty());
    let dc = table(&outs[0].join("daycells-fit.csv"));
    for hi in ["0", "1"] {
        let mut got: BTreeMap<K, u64> = BTreeMap::new();
        for r in dc.iter().filter(|r| r["hi"] == hi) {
            *got.entry(k_of(r)).or_default() += r["n"].parse::<u64>().unwrap();
        }
        got.retain(|_, v| *v > 0);
        assert_eq!(got, want, "hi {hi}");
    }
    // строк не меньше, чем с определённой меткой (остальные — у края данных)
    assert!(rows.len() as u64 >= want.values().sum::<u64>());
}

/// `(fam, wi, ti, t_f)` → (знак, слоты куба моментов C2a).
type Triggers = BTreeMap<(u8, u8, u8, i64), (i8, Vec<u32>)>;

/// Срабатывания со знаком и моменты C2a по ядру контролей (куб с маской первых суток, как в прогоне).
struct Reference {
    cube: Cube,
    trig: Triggers,
}

fn reference(c: &Contract, sym: &str, p: &Params, xd: &XdTable, rules: &[Rule]) -> Reference {
    let cube = feat::build_cube(c, p, 1);
    let cf = c.close_ff();
    // вселенная прогона не берёт первые сутки данных (нет суток-предшественника оборота)
    let mut masked = cube.clone();
    let (n, nw) = (cube.n, cube.windows.len());
    for i in 0..n {
        if slot_t(&cube, i) < DAY {
            for wi in 0..nw {
                masked.r[wi * n + i] = f32::NAN;
            }
        }
    }
    let inp = control::Input {
        sym,
        cube: &masked,
        cf: &cf,
        first_min: c.first_min,
        eps: &[],
    };
    let mut trig = BTreeMap::new();
    control::c2a_moments_with(
        &inp,
        rules,
        xd,
        p,
        SEED,
        true,
        |r| {
            feat::first_triggers(&cube, r, p)
                .into_iter()
                .filter(|&i| slot_t(&cube, i) >= DAY)
                .collect()
        },
        |row, m| {
            if row.sign != 0 {
                trig.insert(
                    (row.rule.fam, row.rule.wi, row.rule.ti, row.t_f),
                    (row.sign, m.to_vec()),
                );
            }
        },
    )
    .unwrap();
    Reference { cube, trig }
}

fn px(c: &Contract, m: i64) -> f64 {
    usize::try_from(m - c.first_min)
        .ok()
        .and_then(|k| c.close.get(k).copied())
        .unwrap_or(f64::NAN)
}

/// Расстояние со знаком до ближайшего уровня `m·10^k` перебором десяти степеней (независимо от `dump.rs`).
fn brute(p: f64, mants: &[f64]) -> f64 {
    let mut best: Option<f64> = None;
    for k in -8..=8i32 {
        for &m in mants {
            let d = (m * 10f64.powi(k) / p).ln();
            if best.is_none_or(|b| d.abs() < b.abs()) {
                best = Some(d);
            }
        }
    }
    best.unwrap() * 1.0e4
}

struct Fix<'a> {
    c: &'a Contract,
    btc: &'a Contract,
    cube: &'a Cube,
}

/// Ожидаемые значения всех столбцов, кроме `sym`, прямыми циклами по данным контракта (без префиксных сумм и кэшей).
fn expected(
    f: &Fix,
    rule: Rule,
    t_f: i64,
    sign: i8,
    moms: &[u32],
    running: bool,
) -> Vec<(&'static str, f64)> {
    let (c, btc, cube) = (f.c, f.btc, f.cube);
    let (side, e, nan) = (f64::from(sign), t_f + 1, f64::NAN);
    let p = px(c, e);
    let dt = DateTime::from_timestamp_millis(GRID0_MS + t_f * MINUTE_MS).unwrap();
    let hm = i64::from(dt.hour() * 60 + dt.minute());
    let epoch = NaiveDate::from_ymd_opt(1970, 1, 1).unwrap();
    let day = dt.date_naive().signed_duration_since(epoch).num_days();

    let rv = |w: i64| -> f64 {
        (t_f - w + 1..=t_f)
            .map(|j| (px(c, j) / px(c, j - 1)).ln().powi(2))
            .sum::<f64>()
            .sqrt()
            * 1.0e4
    };
    let closes = |n: i64| -> Vec<f64> { (t_f - n + 1..=t_f).map(|j| px(c, j)).collect() };
    let hi_lo = |w: &[f64]| -> Option<(f64, f64)> {
        if w.iter().any(|x| x.is_nan()) {
            return None;
        }
        Some((
            w.iter().copied().fold(f64::NEG_INFINITY, f64::max),
            w.iter().copied().fold(f64::INFINITY, f64::min),
        ))
    };
    let rng = hi_lo(&closes(1440)).map_or(nan, |(mx, mn)| bps_ln(mx, mn));
    let drop = hi_lo(&closes(360)).map_or(nan, |(mx, _)| bps_ln(px(c, t_f), mx));
    let qv: f64 = (t_f - 1439..=t_f)
        .map(|j| {
            usize::try_from(j - c.first_min)
                .ok()
                .and_then(|k| c.qvol.get(k).copied())
                .map_or(nan, f64::from)
        })
        .sum();
    let sigma = f64::from(cube.sigma[(t_f / 5 - cube.slot0) as usize]) * 60f64.sqrt() * 1.0e4;

    // путь от входа: 1441 минута, первая из равных на экстремуме
    let pnl: Vec<f64> = (0..=1440)
        .map(|k| side * bps_simple(px(c, e + k), p))
        .collect();
    let (mut mfe, mut mfe_min, mut mae, mut mae_min, mut mfe_price) = (nan, nan, nan, nan, nan);
    if !pnl.iter().any(|x| x.is_nan()) {
        let (mut hi, mut lo) = (0usize, 0usize);
        for (k, &v) in pnl.iter().enumerate() {
            if v > pnl[hi] {
                hi = k;
            }
            if v < pnl[lo] {
                lo = k;
            }
        }
        (mfe, mfe_min, mae, mae_min) = (pnl[hi], hi as f64, pnl[lo], lo as f64);
        mfe_price = px(c, e + hi as i64);
    }
    let sets: [&[f64]; 3] = [&[1.0], &[1.0, 5.0], &[1.0, 2.0, 5.0]];
    let rd = |x: f64, ms: &[f64]| if x.is_nan() { nan } else { brute(x, ms) };

    let base = |h: i64| -> f64 {
        let v: Vec<f64> = moms
            .iter()
            .filter_map(|&j| {
                let sj = sign_of(cube.rat(rule.wi as usize, j as usize));
                if sj == 0 {
                    return None;
                }
                let tj = slot_t(cube, j as usize);
                let g = f64::from(sj) * bps_simple(px(c, tj + 1 + h), px(c, tj + 1));
                (!g.is_nan()).then_some(g)
            })
            .collect();
        if v.is_empty() {
            nan
        } else {
            v.iter().sum::<f64>() / v.len() as f64
        }
    };

    let mut v: Vec<(&'static str, f64)> = vec![
        ("fam", f64::from(rule.fam)),
        ("wi", f64::from(rule.wi)),
        ("ti", f64::from(rule.ti)),
        ("sign", side),
        ("t_f", t_f as f64),
        ("day", day as f64),
        ("month", f64::from(dt.year() * 100 + dt.month() as i32)),
        ("hour_utc", f64::from(dt.hour())),
        ("minute_of_hour", f64::from(dt.minute())),
        ("dow", f64::from(dt.weekday().num_days_from_monday())),
        ("min_since_day", hm as f64),
        ("min_to_fund8", (480 - hm % 480) as f64),
        ("min_since_fund8", (hm % 480) as f64),
        ("d_dec", rd(p, sets[0])),
        ("d_half", rd(p, sets[1])),
        ("d_125", rd(p, sets[2])),
        ("pk_d_dec", rd(mfe_price, sets[0])),
        ("pk_d_half", rd(mfe_price, sets[1])),
        ("pk_d_125", rd(mfe_price, sets[2])),
        ("rv60_bps", rv(60)),
        ("rv1440_bps", rv(1440)),
        ("rng1440_bps", rng),
        ("sigma_h_bps", sigma),
        ("ret_prev60_bps", bps_ln(px(c, t_f), px(c, t_f - 60))),
        ("ret_prev360_bps", bps_ln(px(c, t_f), px(c, t_f - 360))),
        ("drop360_bps", drop),
        ("ep_running", f64::from(u8::from(running))),
        ("age_days", (t_f - c.first_min) as f64 / 1440.0),
        ("qv1440_usd", qv),
        ("btc_prev60_bps", bps_ln(px(btc, t_f), px(btc, t_f - 60))),
        ("btc_next360_bps", bps_ln(px(btc, e + 360), px(btc, e))),
    ];
    for (name, h) in [
        ("gross_5", 5i64),
        ("gross_15", 15),
        ("gross_30", 30),
        ("gross_60", 60),
        ("gross_120", 120),
        ("gross_240", 240),
        ("gross_360", 360),
        ("gross_720", 720),
        ("gross_1440", 1440),
    ] {
        v.push((name, side * bps_simple(px(c, e + h), p)));
    }
    v.extend([
        ("mfe_bps", mfe),
        ("mfe_min", mfe_min),
        ("mae_bps", mae),
        ("mae_min", mae_min),
        ("mfe_price", mfe_price),
        ("b_gross_60", base(60)),
        ("b_gross_360", base(360)),
        ("b_gross_1440", base(1440)),
    ]);
    v
}

#[test]
fn every_dump_column_matches_direct_calculation_on_random_contracts() {
    let dir = tempfile::tempdir().unwrap();
    let (cache, pj) = random_set(dir.path());
    let out = fit_run(dir.path(), &cache, &pj, "out", 3, true);
    let rows = table(&out.join("triggers-fit.csv"));
    assert!(rows.len() > 100, "мало строк: {}", rows.len());

    let p = Params::from_json(&params_json("2020-01", "2020-02", "[90]")).unwrap();
    let xd = XdTable {
        x: vec![[XD_RAND, XD_RAND]],
    };
    let rules = feat::rules(&p);
    let contracts: Vec<Contract> = (0..SYMS.len())
        .map(|i| random_contract(11 + i as u64, 14))
        .collect();
    let refs: Vec<Reference> = SYMS
        .iter()
        .zip(&contracts)
        .map(|(sym, c)| reference(c, sym, &p, &xd, &rules))
        .collect();
    let eps: Vec<Vec<Episode>> = SYMS
        .iter()
        .zip(&contracts)
        .map(|(sym, c)| find_episodes(c, &p, &xd, sym))
        .collect();
    assert_eq!(SYMS[2], "BTCUSDT");
    let btc = &contracts[2];

    // набор строк = срабатывания со знаком ядра контролей
    let total: usize = refs.iter().map(|r| r.trig.len()).sum();
    assert_eq!(rows.len(), total);
    let (mut longs, mut shorts, mut full_path) = (0, 0, 0);
    for r in &rows {
        let si = SYMS.iter().position(|s| *s == r["sym"]).unwrap();
        let (t_f, fam, wi, ti, sign) = keys(r);
        let rule = Rule {
            fam: fam as u8,
            wi: wi as u8,
            ti: ti as u8,
        };
        let (rsign, moms) = refs[si]
            .trig
            .get(&(rule.fam, rule.wi, rule.ti, t_f))
            .unwrap_or_else(|| panic!("нет срабатывания в эталоне: {} {rule:?} {t_f}", r["sym"]));
        assert_eq!(i64::from(*rsign), sign);
        let running = eps[si]
            .iter()
            .any(|e| i64::from(e.sign) == sign && e.t0 < t_f && t_f <= e.t_end);
        let f = Fix {
            c: &contracts[si],
            btc,
            cube: &refs[si].cube,
        };
        let want = expected(&f, rule, t_f, *rsign, moms, running);
        assert_eq!(want.len() + 1, HEADER.split(',').count(), "все столбцы");
        for (col, w) in &want {
            let got = val(r, col);
            assert!(
                same(got, *w),
                "{} {rule:?} t_f {t_f}: {col} = {got} против {w}",
                r["sym"]
            );
        }
        longs += usize::from(sign > 0);
        shorts += usize::from(sign < 0);
        full_path += usize::from(val(r, "mfe_bps").is_finite());
    }
    assert!(longs > 0 && shorts > 0, "обе стороны: {longs} / {shorts}");
    assert!(full_path > 0, "нет строк с полным путём");
}

// ---------- признаки направления (`--dump-features`) ----------

/// Первый слот и число слотов OI теста признаков: минуты 250…3749 (`t_f` вне диапазона дают `NaN`).
const OI_SLOT0: i64 = 50;
const OI_SLOTS: usize = 700;
/// Окна куба теста: `r_w_bps` и z берутся для окна правила строки.
const FZ_WINDOWS: [u32; 2] = [10, 60];
/// Слот куба теста `t_f = 2000` (`slot0 = 8`): в нём особые z.
const FZ_SLOT_2000: usize = 392;

/// Индекс колонки OI по имени (порядок `cache::OI_COLS`): тест не знает номеров колонок из `dump.rs`.
fn oi_col(name: &str) -> usize {
    OI_COLS.iter().position(|n| *n == name).unwrap()
}

/// Линейный ряд колонки: основание и шаг на слот (у каждой колонки свои — путаница колонок видна).
fn oi_line(name: &str) -> (f64, f64) {
    match name {
        "open_interest" => (1000.0, 2.0),
        "open_interest_value" => (5.0e5, 100.0),
        "count_toptrader_long_short_ratio" => (1.5, 0.01),
        "sum_toptrader_long_short_ratio" => (2.5, 0.02),
        "count_long_short_ratio" => (0.8, 0.005),
        "sum_taker_long_short_vol_ratio" => (1.1, 0.03),
        _ => unreachable!("колонка {name}"),
    }
}

/// Дыры (колонка, слот): при `t_f = 2500` и сдвиге 1 слот `u` = 499, час назад — 487.
const OI_HOLES: [(&str, i64); 4] = [
    ("open_interest_value", 499),
    ("count_toptrader_long_short_ratio", 499),
    ("open_interest", 487),
    ("sum_taker_long_short_vol_ratio", 487),
];
/// Слот с нулевым `open_interest`: при `t_f = 2900` час назад (567) — `ln` не определён.
const OI_ZERO: (&str, i64) = ("open_interest", 567);

/// Ожидаемое значение колонки `name` на слоте `u`: формула; `NaN` — дыра или слот вне ряда; 0 — слот нуля.
fn oi_exp(name: &str, u: i64) -> f64 {
    if !(OI_SLOT0..OI_SLOT0 + OI_SLOTS as i64).contains(&u) || OI_HOLES.contains(&(name, u)) {
        return f64::NAN;
    }
    if (name, u) == OI_ZERO {
        return 0.0;
    }
    let (base, step) = oi_line(name);
    base + step * (u - OI_SLOT0) as f64
}

/// Выплаты фандинга теста (по возрастанию минуты).
const PAYMENTS: [(i64, f64); 5] = [
    (100, 1.0e-4),
    (580, -2.0e-4),
    (1060, 3.0e-4),
    (2000, 5.0e-4),
    (2480, 7.0e-4),
];

/// `(ставка, возраст)` последней выплаты с минутой `≤ t_f`; выплат ещё не было — `NaN`.
fn fund_exp(t_f: i64) -> (f64, f64) {
    PAYMENTS
        .iter()
        .rev()
        .find(|p| p.0 <= t_f)
        .map_or((f64::NAN, f64::NAN), |p| (p.1, (t_f - p.0) as f64))
}

/// `unit_contract` с рядами OI по формулам `oi_exp` и фандингом вперемешку (сборщик сортирует по минуте).
fn feat_contract() -> Contract {
    let mut c = unit_contract();
    c.oi_first_slot = OI_SLOT0;
    for (col, name) in OI_COLS.iter().enumerate() {
        c.oi[col] = (OI_SLOT0..OI_SLOT0 + OI_SLOTS as i64)
            .map(|u| oi_exp(name, u))
            .collect();
    }
    c.funding = [3, 0, 4, 1, 2].iter().map(|&k| PAYMENTS[k]).collect();
    c
}

/// z куба теста: `100·f + 10·wi + 0,25 + 0,001·i`; в слоте `t_f = 2000` окна 0 особые — `NaN`, `+inf`, `-inf`, `-0`.
fn zexp(f: usize, wi: usize, i: usize) -> f32 {
    if wi == 0 && i == FZ_SLOT_2000 {
        match f {
            2 => return f32::NAN,
            3 => return f32::INFINITY,
            4 => return f32::NEG_INFINITY,
            5 => return -0.0,
            _ => {}
        }
    }
    (f * 100 + wi * 10) as f32 + 0.25 + i as f32 * 0.001
}

fn feat_cube() -> Cube {
    let (n, slot0, nw) = (800usize, 8i64, FZ_WINDOWS.len());
    let mut z = vec![0.0f32; NF * nw * n];
    for f in 0..NF {
        for wi in 0..nw {
            for i in 0..n {
                z[(f * nw + wi) * n + i] = zexp(f, wi, i);
            }
        }
    }
    Cube {
        slot0,
        n,
        windows: FZ_WINDOWS.to_vec(),
        z,
        r: vec![f32::NAN; nw * n],
        sigma: vec![f32::NAN; n],
    }
}

fn zcell(r: &Row, col: &str) -> f32 {
    r[col].parse().unwrap()
}

fn z_same(a: f32, b: f32) -> bool {
    a == b || (a.is_nan() && b.is_nan())
}

/// Ожидаемое `doi_60_pct` по рядам `open_interest` слотов `u` и `u − 12`.
fn doi_exp(u: i64) -> f64 {
    let (now, ago) = (oi_exp("open_interest", u), oi_exp("open_interest", u - 12));
    if now > 0.0 && ago > 0.0 {
        (now / ago).ln() * 100.0
    } else {
        f64::NAN
    }
}

/// Строки `(t_f, окно, знак)` теста признаков.
const FZ_ROWS: [(i64, u8, i8); 8] = [
    (2000, 0, -1),
    (2000, 1, -1),
    (1990, 0, 1),
    (3000, 0, 1),
    (50, 0, 1),
    (2500, 0, 1),
    (2900, 0, 1),
    (3800, 0, 1),
];

/// Текст строк `Collector` для `FZ_ROWS`: `lag` — сдвиг OI (`Some`) или признаков нет (`None`).
fn feat_bytes(lag: Option<u32>) -> Vec<u8> {
    let (sh, c, cube) = (Shared::default(), feat_contract(), feat_cube());
    let cf = c.close_ff();
    let mut col = Collector::new(&sh, "XYZUSDT", &c, &cf, &cube).with_features(lag);
    for &(t_f, wi, sign) in FZ_ROWS.iter().rev() {
        col.add(&trig((0, wi, 0), t_f, sign, false), &[]);
    }
    col.finish()
}

fn feat_rows(lag: u32) -> Vec<Row> {
    let bytes = feat_bytes(Some(lag));
    rows_from(
        &format!("{HEADER},{FEATURE_COLS}"),
        std::str::from_utf8(&bytes).unwrap(),
    )
}

fn row_at(rows: &[Row], t_f: i64, wi: u8) -> &Row {
    rows.iter()
        .find(|r| r["t_f"] == t_f.to_string() && r["wi"] == wi.to_string())
        .unwrap_or_else(|| panic!("нет строки t_f {t_f} окно {wi}"))
}

/// Каждый столбец признаков каждой строки теста против формул (сдвиг OI `lag`).
fn check_feature_rows(rows: &[Row], lag: i64) {
    assert_eq!(rows.len(), FZ_ROWS.len());
    for &(t_f, wi, sign) in &FZ_ROWS {
        let r = row_at(rows, t_f, wi);
        assert_eq!(r["sign"], sign.to_string());
        let (i, u) = ((t_f / 5 - 8) as usize, t_f / 5 - lag);
        for f in 0..NF {
            let (got, want) = (zcell(r, &format!("z_f{}", f + 1)), zexp(f, wi.into(), i));
            assert!(
                z_same(got, want),
                "t_f {t_f} окно {wi} z_f{}: {got} против {want}",
                f + 1
            );
        }
        let w = i64::from(FZ_WINDOWS[usize::from(wi)]);
        expect(r, "r_w_bps", bps_ln(pf(t_f), pf(t_f - w)));
        let lv = |name: &str| oi_exp(name, u);
        let dl = |name: &str| oi_exp(name, u) - oi_exp(name, u - 12);
        for (col, name) in [
            ("oi_value", "open_interest_value"),
            ("taker_ratio", "sum_taker_long_short_vol_ratio"),
            ("top_count_ratio", "count_toptrader_long_short_ratio"),
            ("top_sum_ratio", "sum_toptrader_long_short_ratio"),
            ("global_ratio", "count_long_short_ratio"),
        ] {
            expect(r, col, lv(name));
        }
        for (col, name) in [
            ("d_taker_60", "sum_taker_long_short_vol_ratio"),
            ("d_top_count_60", "count_toptrader_long_short_ratio"),
            ("d_top_sum_60", "sum_toptrader_long_short_ratio"),
            ("d_global_60", "count_long_short_ratio"),
        ] {
            expect(r, col, dl(name));
        }
        expect(r, "doi_60_pct", doi_exp(u));
        let (rate, age) = fund_exp(t_f);
        expect(r, "funding_rate", rate);
        expect(r, "funding_age_min", age);
    }
}

#[test]
fn feature_columns_have_known_answers() {
    assert_eq!(FEATURE_COLS.split(',').count(), 23);
    for lag in [0u32, 1, 2] {
        check_feature_rows(&feat_rows(lag), i64::from(lag));
    }
    let rows = feat_rows(1);

    // руками (сдвиг 1): t_f = 2000 — слот OI u = 399, час назад 387
    let r = row_at(&rows, 2000, 0);
    expect(r, "oi_value", 5.0e5 + 100.0 * 349.0);
    expect(r, "taker_ratio", 1.1 + 0.03 * 349.0);
    expect(r, "top_count_ratio", 1.5 + 0.01 * 349.0);
    expect(r, "top_sum_ratio", 2.5 + 0.02 * 349.0);
    expect(r, "global_ratio", 0.8 + 0.005 * 349.0);
    expect(r, "doi_60_pct", (1698.0f64 / 1674.0).ln() * 100.0);
    for (col, want) in [
        ("d_taker_60", 0.36),
        ("d_top_count_60", 0.12),
        ("d_top_sum_60", 0.24),
        ("d_global_60", 0.06),
    ] {
        expect(r, col, want);
    }
    // r_w_bps — знак хода цены, не стороны: сторона −1, ход за 10 минут вниз (цена в t_f − 10 выше на 2 %)
    expect(r, "r_w_bps", bps_ln(P0, P0 * 1.02));
    assert!(val(r, "r_w_bps") < 0.0 && r["sign"] == "-1");
    expect(row_at(&rows, 1990, 0), "r_w_bps", bps_ln(P0 * 1.02, P0));
    expect(row_at(&rows, 2000, 1), "r_w_bps", bps_ln(P0, P0 * 1.03));
    // z окна правила: окно 1 берёт ряд окна 1
    assert!(z_same(
        zcell(row_at(&rows, 2000, 1), "z_f1"),
        zexp(0, 1, FZ_SLOT_2000)
    ));
    assert!(!z_same(
        zcell(row_at(&rows, 2000, 1), "z_f1"),
        zcell(r, "z_f1")
    ));
    // особые z: NaN, ±inf, -0 пишется как 0
    assert_eq!(
        (
            r["z_f3"].as_str(),
            r["z_f4"].as_str(),
            r["z_f5"].as_str(),
            r["z_f6"].as_str()
        ),
        ("NaN", "inf", "-inf", "0")
    );

    // фандинг: выплата ровно в t_f учтена (возраст 0); раньше первой — NaN; после последней — растущий возраст
    expect(r, "funding_rate", 5.0e-4);
    expect(r, "funding_age_min", 0.0);
    expect(row_at(&rows, 1990, 0), "funding_rate", 3.0e-4);
    expect(row_at(&rows, 1990, 0), "funding_age_min", 930.0);
    expect(row_at(&rows, 3000, 0), "funding_rate", 7.0e-4);
    expect(row_at(&rows, 3000, 0), "funding_age_min", 520.0);
    for c in ["funding_rate", "funding_age_min", "oi_value", "doi_60_pct"] {
        assert!(
            val(row_at(&rows, 50, 0), c).is_nan(),
            "{c} до первых данных"
        );
    }

    // дыры: уровень в дыре — NaN, его изменение за час — NaN; соседние колонки не затронуты
    let hole = row_at(&rows, 2500, 0);
    for c in [
        "oi_value",
        "top_count_ratio",
        "d_top_count_60",
        "doi_60_pct",
        "d_taker_60",
    ] {
        assert!(val(hole, c).is_nan(), "{c} в дыре");
    }
    expect(hole, "taker_ratio", 1.1 + 0.03 * 449.0);
    expect(hole, "top_sum_ratio", 2.5 + 0.02 * 449.0);
    expect(hole, "d_top_sum_60", 0.24);
    expect(hole, "d_global_60", 0.06);
    // нулевой OI час назад: ln не определён, остальное считается
    let zero = row_at(&rows, 2900, 0);
    assert!(val(zero, "doi_60_pct").is_nan());
    expect(zero, "oi_value", 5.0e5 + 100.0 * 529.0);
    // слот вне ряда (после последнего): все OI-столбцы NaN, фандинг есть
    let past = row_at(&rows, 3800, 0);
    for c in ["oi_value", "taker_ratio", "doi_60_pct", "d_global_60"] {
        assert!(val(past, c).is_nan(), "{c} за концом ряда");
    }
    expect(past, "funding_age_min", 1320.0);
}

#[test]
fn without_features_the_row_and_schema_stay_and_features_only_append() {
    let (off, on) = (feat_bytes(None), feat_bytes(Some(1)));
    let (off, on) = (
        std::str::from_utf8(&off).unwrap(),
        std::str::from_utf8(&on).unwrap(),
    );
    // прежняя схема читается заголовком `HEADER`, строк столько же
    assert_eq!(rows_from(HEADER, off).len(), FZ_ROWS.len());
    assert_eq!(on.lines().count(), off.lines().count());
    for (a, b) in off.lines().zip(on.lines()) {
        let tail = b.strip_prefix(a).expect("начало строки прежнее");
        assert!(tail.starts_with(','));
        assert_eq!(tail.matches(',').count(), 23, "{tail}");
    }
}

#[test]
fn features_without_dump_triggers_are_refused() {
    use clap::Parser;
    #[derive(clap::Parser)]
    struct FeatCli {
        #[command(flatten)]
        a: RunArgs,
    }
    let base = [
        "t",
        "--cache",
        "c",
        "--params",
        "p",
        "--out",
        "o",
        "--stage",
        "fit",
        "--seed",
        "1",
        "--oi-lag",
        "1",
        "--threads",
        "1",
    ];
    let with = |extra: &[&str]| {
        FeatCli::try_parse_from(base.iter().chain(extra.iter()).copied()).map(|c| c.a)
    };
    assert!(with(&["--dump-features"]).is_err());
    let a = with(&["--dump-triggers", "--dump-features"]).unwrap();
    assert!(a.dump_triggers && a.dump_features);
    let a = with(&["--dump-triggers"]).unwrap();
    assert!(a.dump_triggers && !a.dump_features);
    assert!(!with(&[]).unwrap().dump_features);

    // вызов структурой (без разбора командной строки): отказ до чтения кэша
    let dir = tempfile::tempdir().unwrap();
    let pj = dir.path().join("params.json");
    fs::write(&pj, params_json("2019-12", "2020-01", "[90]")).unwrap();
    let mut a = args(dir.path(), &pj, &dir.path().join("out"), Stage::Test, 1);
    a.dump_features = true;
    let e = run(a).unwrap_err();
    assert!(
        format!("{e:#}").contains("--dump-features требует --dump-triggers"),
        "{e:#}"
    );
}

#[test]
fn feature_dump_is_byte_identical_for_any_thread_count_and_changes_nothing_else() {
    let dir = tempfile::tempdir().unwrap();
    let (cache, pj) = random_set(dir.path());
    let outs: Vec<PathBuf> = [1usize, 4, 3]
        .iter()
        .map(|&th| {
            fit_run_with(
                dir.path(),
                &cache,
                &pj,
                &format!("ft{th}"),
                th,
                (true, true),
            )
        })
        .collect();
    let first = fs::read(outs[0].join("triggers-fit.csv")).unwrap();
    for o in &outs[1..] {
        assert_eq!(
            first,
            fs::read(o.join("triggers-fit.csv")).unwrap(),
            "triggers-fit.csv: потоки 1 против другого"
        );
    }

    // без признаков: заголовок прежний, строки — начало строк с признаками, число строк то же
    let off = fit_run(dir.path(), &cache, &pj, "ft-off", 2, true);
    let (on_text, off_text) = (
        String::from_utf8(first).unwrap(),
        fs::read_to_string(off.join("triggers-fit.csv")).unwrap(),
    );
    let (mut on_lines, mut off_lines) = (on_text.lines(), off_text.lines());
    assert_eq!(off_lines.next().unwrap(), HEADER);
    assert_eq!(on_lines.next().unwrap(), format!("{HEADER},{FEATURE_COLS}"));
    assert_eq!(on_text.lines().count(), off_text.lines().count());
    assert!(off_text.lines().count() > 100, "мало строк выгрузки");
    for (a, b) in off_lines.zip(on_lines) {
        let tail = b.strip_prefix(a).expect("начало строки прежнее");
        assert!(tail.starts_with(','));
        assert_eq!(tail.matches(',').count(), 23);
    }

    // остальные таблицы те же байт в байт; без --dump-triggers файла нет
    let plain = fit_run(dir.path(), &cache, &pj, "ft-plain", 2, false);
    assert!(!plain.join("triggers-fit.csv").exists());
    for f in FILES {
        assert_eq!(
            fs::read(outs[0].join(f)).unwrap(),
            fs::read(plain.join(f)).unwrap(),
            "{f}: с признаками и без"
        );
    }
}

/// Значение колонки OI `name` контракта `c` на слоте `s` прямым чтением ряда (`NaN` — дыра или вне ряда).
fn oi_direct(c: &Contract, name: &str, s: i64) -> f64 {
    let (col, k) = (oi_col(name), s - c.oi_first_slot);
    usize::try_from(k)
        .ok()
        .and_then(|k| c.oi[col].get(k).copied())
        .unwrap_or(f64::NAN)
}

#[test]
fn every_feature_column_matches_direct_calculation_on_random_contracts() {
    let dir = tempfile::tempdir().unwrap();
    let (cache, pj) = random_set(dir.path());
    let out = fit_run_with(dir.path(), &cache, &pj, "out", 3, (true, true));
    let rows = table(&out.join("triggers-fit.csv"));
    assert!(rows.len() > 100, "мало строк: {}", rows.len());

    let p = Params::from_json(&params_json("2020-01", "2020-02", "[90]")).unwrap();
    let contracts: Vec<Contract> = (0..SYMS.len())
        .map(|i| random_contract(11 + i as u64, 14))
        .collect();
    let cubes: Vec<Cube> = contracts
        .iter()
        .map(|c| feat::build_cube(c, &p, 1))
        .collect();
    let (mut finite_z, mut oi_levels) = (0, 0);
    for r in &rows {
        let si = SYMS.iter().position(|s| *s == r["sym"]).unwrap();
        let (c, cube) = (&contracts[si], &cubes[si]);
        let (t_f, _, wi, _, _) = keys(r);
        let i = (t_f / 5 - cube.slot0) as usize;
        for f in 0..NF {
            let (got, want) = (
                zcell(r, &format!("z_f{}", f + 1)),
                cube.zat(f, wi as usize, i),
            );
            assert!(
                z_same(got, want),
                "{} t_f {t_f} z_f{}: {got} против {want}",
                r["sym"],
                f + 1
            );
            finite_z += usize::from(got.is_finite());
        }
        let w = i64::from(p.features.windows_min[wi as usize]);
        expect(r, "r_w_bps", bps_ln(px(c, t_f), px(c, t_f - w)));
        // сдвиг OI прогона — 1 слот
        let u = t_f / 5 - 1;
        let lv = |name: &str| oi_direct(c, name, u);
        let dl = |name: &str| oi_direct(c, name, u) - oi_direct(c, name, u - 12);
        for (col, name) in [
            ("oi_value", "open_interest_value"),
            ("taker_ratio", "sum_taker_long_short_vol_ratio"),
            ("top_count_ratio", "count_toptrader_long_short_ratio"),
            ("top_sum_ratio", "sum_toptrader_long_short_ratio"),
            ("global_ratio", "count_long_short_ratio"),
        ] {
            expect(r, col, lv(name));
        }
        for (col, name) in [
            ("d_taker_60", "sum_taker_long_short_vol_ratio"),
            ("d_top_count_60", "count_toptrader_long_short_ratio"),
            ("d_top_sum_60", "sum_toptrader_long_short_ratio"),
            ("d_global_60", "count_long_short_ratio"),
        ] {
            expect(r, col, dl(name));
        }
        let (now, ago) = (lv("open_interest"), oi_direct(c, "open_interest", u - 12));
        expect(
            r,
            "doi_60_pct",
            if now > 0.0 && ago > 0.0 {
                100.0 * (now / ago).ln()
            } else {
                f64::NAN
            },
        );
        let last = c.funding.iter().rev().find(|f| f.0 <= t_f);
        expect(r, "funding_rate", last.map_or(f64::NAN, |f| f.1));
        expect(
            r,
            "funding_age_min",
            last.map_or(f64::NAN, |f| (t_f - f.0) as f64),
        );
        oi_levels += usize::from(val(r, "oi_value").is_finite());
    }
    assert!(finite_z > rows.len(), "z почти везде NaN: {finite_z}");
    assert!(oi_levels > rows.len() / 2, "уровни OI: {oi_levels}");
}

/// Минута, после которой данные заменяются в проверке утечки: 10-е сутки контрактов из 14.
const LEAK_M: i64 = 10 * DAY;

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

type FeatMap = BTreeMap<(String, (i64, i64, i64, i64, i64)), Vec<String>>;

/// Значения столбцов признаков строк до (`early`) или после минуты `LEAK_M`.
fn feature_cells(rows: &[Row], early: bool) -> FeatMap {
    rows.iter()
        .filter(|r| (keys(r).0 <= LEAK_M) == early)
        .map(|r| {
            let cells: Vec<String> = FEATURE_COLS.split(',').map(|n| r[n].clone()).collect();
            ((r["sym"].clone(), keys(r)), cells)
        })
        .collect()
}

#[test]
fn data_after_t_f_does_not_change_the_feature_columns() {
    let dir = tempfile::tempdir().unwrap();
    let run_on = |sub: &str, edit: &dyn Fn(usize, &mut Contract)| -> Vec<Row> {
        let d = dir.path().join(sub);
        fs::create_dir_all(&d).unwrap();
        let (cache, pj) = random_set_with(&d, edit);
        let out = fit_run_with(&d, &cache, &pj, "out", 2, (true, true));
        table(&out.join("triggers-fit.csv"))
    };
    let base = run_on("base", &|_, _| {});
    let cut = run_on("cut", &|_, c| scramble_after(c, LEAK_M as usize));

    // до LEAK_M: тот же набор строк и те же значения всех 23 столбцов
    let (be, ce) = (feature_cells(&base, true), feature_cells(&cut, true));
    assert!(be.len() > 20, "мало строк до LEAK_M: {}", be.len());
    assert_eq!(be, ce);
    assert!(
        be.values().any(|v| v[..NF].iter().any(|s| s != "NaN")),
        "до LEAK_M нет ни одного определённого z"
    );
    // после: данные другие — признаки поздних строк другие (проверка не пуста)
    assert_ne!(feature_cells(&base, false), feature_cells(&cut, false));
}

// ---------- простая доходность в исходах (допущение 11 шапки `dump.rs`) ----------

#[test]
fn ignition_outcomes_are_simple_long_return_and_short_is_its_exact_negative() {
    let dir = tempfile::tempdir().unwrap();
    put(dir.path(), "BTCUSDT", &btc_contract());
    let sh = Shared::load(&dir.path().join("BTCUSDT.pmc"), None).unwrap();
    let cube = unit_cube(&[]);
    let c = unit_contract();
    let cf = c.close_ff();
    // событие воспламенения: сторона +1 всегда, исходы — доходность вверх (перспектива лонга)
    let mut ig = Collector::new(&sh, "XYZUSDT", &c, &cf, &cube).with_features(Some(1));
    ig.add_ignition(
        Rule {
            fam: 11,
            wi: 0,
            ti: 0,
        },
        TF,
        0,
        false,
        IgnTail {
            quiet_min: 400,
            natr_prev_max: f32::NAN,
            m_minus_tf: 0,
        },
    );
    let bytes = ig.finish();
    let rows = rows_from(&ign_header(), std::str::from_utf8(&bytes).unwrap());
    assert_eq!(rows.len(), 1);
    let ev = &rows[0];
    assert_eq!(ev["sign"], "1");
    // срабатывание шорта на тех же ценах
    let mut tr = Collector::new(&sh, "XYZUSDT", &c, &cf, &cube);
    tr.add(&trig((0, 0, 0), TF, -1, false), &[]);
    let tbytes = tr.finish();
    let trows = rows_from(HEADER, std::str::from_utf8(&tbytes).unwrap());
    assert_eq!(trows.len(), 1);
    let short = &trows[0];
    assert_eq!(short["sign"], "-1");
    for h in [5i64, 15, 30, 60, 120, 240, 360, 720, 1440] {
        let col = format!("gross_{h}");
        // событие: `e^r − 1` для ln-хода `r = ln(c[e+h]/c[e])`, не `r`
        let r = (pf(TF + 1 + h) / pf(TF + 1)).ln();
        expect(ev, &col, (r.exp() - 1.0) * 1.0e4);
        if r.abs() > 0.01 {
            assert!(
                (val(ev, &col) - r * 1.0e4).abs() > 1.0,
                "{col}: простая доходность не должна совпадать с ln-ходом"
            );
        }
        // шорт на тех же ценах — ровно минус лонг
        assert_eq!(val(short, &col), -val(ev, &col), "{col}");
    }
    assert_eq!(val(short, "mfe_bps"), -val(ev, "mae_bps"));
    assert_eq!(val(short, "mae_bps"), -val(ev, "mfe_bps"));
    // контекст остаётся в ln-bps: доходность до t_f не заменена простой
    expect(ev, "ret_prev60_bps", bps_ln(1.0, 1.03));
    expect(ev, "ret_prev360_bps", bps_ln(1.0, 0.96));
}
