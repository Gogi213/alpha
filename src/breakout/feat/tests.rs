//! Тесты порта признаков TK-609: золотой набор numpy/pandas (`golden_np.json`, делает `tools/p42/np_golden.py` на srv),
//! синтетика по краям `flow`/`deriv`, разбор CSV и вход/выход `alpha-feat`.

use super::deriv::{deriv_features, Metrics, DERIV};
use super::flow::{
    edges, flow_coin, flow_features, q99_edge, vpin_value, Ep, Win, DAY_MS, HOUR_MS, MIN_MS, NB,
};
use super::io::{read_eps, run, Cfg, Mode};
use super::npcompat::{
    np_cumsum, np_mean, np_sum, ss_left_f64, ss_left_i64, ss_right_f64, ss_right_i64,
};
use super::pdcompat::{
    date_str, days_from_civil, parse_date, parse_datetime_ms, parse_f64, xstrtod,
};
use super::trades::{parse_trades_csv, Csv, DaySrc, DayTrades};
use anyhow::Result;
use serde_json::Value;
use std::collections::HashMap;
use std::io::Cursor;

// ───────────────────────── золотой набор: те же данные, что в np_golden.py ─────────────────────────

const GAMMA: u64 = 0x9E37_79B9_7F4A_7C15;
const C1: u64 = 0xBF58_476D_1CE4_E5B9;
const C2: u64 = 0x94D0_49BB_1331_11EB;

/// `(u, 2^e)` splitmix64: `u = (z >> 11)·2⁻⁵³`, `e = (z & 63) − 32`.
fn mix(seed: u64, n: usize) -> Vec<(f64, f64)> {
    (0..n)
        .map(|i| {
            let mut z = seed.wrapping_add((i as u64 + 1).wrapping_mul(GAMMA));
            z = (z ^ (z >> 30)).wrapping_mul(C1);
            z = (z ^ (z >> 27)).wrapping_mul(C2);
            z ^= z >> 31;
            let u = (z >> 11) as f64 * (1.0 / 9_007_199_254_740_992.0);
            let e = (z & 63) as i64 - 32;
            (u, f64::from_bits(((1023 + e) as u64) << 52))
        })
        .collect()
}

fn gen(seed: u64, n: usize) -> Vec<f64> {
    mix(seed, n)
        .into_iter()
        .map(|(u, p)| (u - 0.3) * p)
        .collect()
}

fn gen_pos(seed: u64, n: usize) -> Vec<f64> {
    mix(seed, n)
        .into_iter()
        .map(|(u, p)| (1.0 + u) * p)
        .collect()
}

fn hex(x: f64) -> String {
    format!("{:016x}", x.to_bits())
}

/// FNV-1a64 по байтам слов `u64` (little-endian).
fn fnv(words: impl IntoIterator<Item = u64>) -> String {
    let mut h: u64 = 0xCBF2_9CE4_8422_2325;
    for w in words {
        for k in 0..8 {
            h = (h ^ ((w >> (8 * k)) & 0xFF)).wrapping_mul(0x0000_0100_0000_01B3);
        }
    }
    format!("{h:016x}")
}

fn fnv_f(a: &[f64]) -> String {
    fnv(a.iter().map(|x| x.to_bits()))
}

fn golden() -> Value {
    serde_json::from_str(include_str!("golden_np.json")).expect("golden_np.json")
}

fn rows<'a>(g: &'a Value, key: &str) -> &'a Vec<Value> {
    g[key].as_array().unwrap_or_else(|| panic!("нет {key}"))
}

fn u(v: &Value) -> u64 {
    v.as_u64().expect("u64")
}

fn s(v: &Value) -> &str {
    v.as_str().expect("str")
}

#[test]
fn golden_sum_mean() {
    let g = golden();
    let mut bad = Vec::new();
    for r in rows(&g, "sum") {
        let (seed, n, off) = (u(&r[0]), u(&r[1]) as usize, u(&r[2]) as usize);
        let a = gen(seed, off + n);
        if hex(np_sum(&a[off..])) != s(&r[3]) {
            bad.push(format!("sum n={n} off={off}"));
        }
    }
    for r in rows(&g, "mean") {
        let (seed, n, off) = (u(&r[0]), u(&r[1]) as usize, u(&r[2]) as usize);
        let a = gen(seed, off + n);
        if hex(np_mean(&a[off..])) != s(&r[3]) {
            bad.push(format!("mean n={n} off={off}"));
        }
    }
    for r in rows(&g, "masked_sum") {
        let (seed, n) = (u(&r[0]), u(&r[1]) as usize);
        let a = gen(seed, n);
        let m = mix(seed + 99, n);
        let sel: Vec<f64> = a
            .iter()
            .zip(&m)
            .filter(|(_, (uu, _))| *uu < 0.5)
            .map(|(&x, _)| x)
            .collect();
        if hex(np_sum(&sel)) != s(&r[2]) {
            bad.push(format!("masked n={n}"));
        }
    }
    assert!(
        bad.is_empty(),
        "{} расхождений, первые: {:?}",
        bad.len(),
        &bad[..bad.len().min(10)]
    );
}

#[test]
fn golden_cumsum_log() {
    let g = golden();
    for r in rows(&g, "cumsum") {
        let (seed, n) = (u(&r[0]), u(&r[1]) as usize);
        assert_eq!(fnv_f(&np_cumsum(&gen(seed, n))), s(&r[2]), "cumsum n={n}");
    }
    for r in rows(&g, "log") {
        let (seed, n) = (u(&r[0]), u(&r[1]) as usize);
        let v: Vec<f64> = gen_pos(seed, n).iter().map(|x| x.ln()).collect();
        assert_eq!(fnv_f(&v), s(&r[2]), "ln n={n}");
    }
    for r in rows(&g, "log10") {
        let (seed, n) = (u(&r[0]), u(&r[1]) as usize);
        let v: Vec<f64> = gen_pos(seed, n).iter().map(|x| x.log10()).collect();
        assert_eq!(fnv_f(&v), s(&r[2]), "log10 n={n}");
    }
    let p = gen_pos(1234, 5000);
    let l: Vec<f64> = p.iter().map(|x| x.ln()).collect();
    let l10: Vec<f64> = p.iter().map(|x| x.log10()).collect();
    assert_eq!(fnv_f(&l), s(&g["math_log"]));
    assert_eq!(fnv_f(&l10), s(&g["math_log10"]));
}

#[test]
fn golden_edges_search() {
    let g = golden();
    assert_eq!(fnv_f(edges()), s(&g["edges"]));
    for r in rows(&g, "searchsorted") {
        let (seed, na, nx, side) = (u(&r[0]), u(&r[1]) as usize, u(&r[2]) as usize, s(&r[3]));
        let mut a = gen(seed, na);
        a.sort_by(f64::total_cmp);
        let mut x = gen(seed + 1, nx);
        let k = na.min(nx) / 2;
        x[..k].copy_from_slice(&a[..k]);
        let res: Vec<u64> = x
            .iter()
            .map(|&q| {
                (if side == "left" {
                    ss_left_f64(&a, q)
                } else {
                    ss_right_f64(&a, q)
                }) as u64
            })
            .collect();
        assert_eq!(fnv(res), s(&r[4]), "searchsorted na={na} nx={nx} {side}");
    }
    let ed = edges();
    let mut v: Vec<f64> = gen(555, 200_000).iter().map(|x| x.abs()).collect();
    v.extend_from_slice(ed);
    v.extend(ed.iter().map(|e| e * (1.0 + 2f64.powi(-52))));
    v.extend(ed.iter().map(|e| e * (1.0 - 2f64.powi(-53))));
    let ix: Vec<u64> = v
        .iter()
        .map(|&x| (ss_right_f64(ed, x) as i64 - 1).clamp(0, NB as i64 - 1) as u64)
        .collect();
    assert_eq!(fnv(ix), s(&g["hist_index"]));
}

#[test]
fn golden_xstrtod_pandas() {
    let g = golden();
    let mut bad = Vec::new();
    for r in rows(&g, "xstrtod") {
        let got = xstrtod(s(&r[0]).as_bytes()).map(hex);
        if got.as_deref() != Some(s(&r[1])) {
            bad.push(format!("{:?}: {:?} вместо {}", s(&r[0]), got, s(&r[1])));
        }
    }
    assert!(
        bad.is_empty(),
        "{} расхождений, первые: {:?}",
        bad.len(),
        &bad[..bad.len().min(10)]
    );
}

// ───────────────────────── мелкие примитивы ─────────────────────────

#[test]
fn np_primitives_small() {
    assert_eq!(np_sum(&[]).to_bits(), 0.0f64.to_bits());
    assert_eq!(np_cumsum(&[1.0, 2.0, 3.5]), vec![1.0, 3.0, 6.5]);
    assert_eq!(
        np_cumsum(&[-0.0]).first().map(|x| x.to_bits()),
        Some((-0.0f64).to_bits())
    );
    let a = [1i64, 3, 3, 5];
    assert_eq!((ss_left_i64(&a, 3), ss_right_i64(&a, 3)), (1, 3));
    assert_eq!((ss_left_i64(&a, 0), ss_right_i64(&a, 9)), (0, 4));
}

#[test]
fn dates() {
    assert_eq!(parse_date("2026-09-01").ok(), Some(20_697));
    assert_eq!(date_str(20_697), "2026-09-01");
    for d in 0..30_000 {
        assert_eq!(parse_date(&date_str(d)).ok(), Some(d), "день {d}");
    }
    assert_eq!(days_from_civil(1970, 1, 1), 0);
    assert_eq!(
        parse_datetime_ms(b"1970-01-02 00:00:05").ok(),
        Some(86_405_000)
    );
    assert!(parse_datetime_ms(b"2026-9-1 00:00:00").is_err());
    assert!(parse_f64(b"abc").is_err());
    assert!(parse_f64(b"").is_ok_and(f64::is_nan));
    assert_eq!(
        parse_f64(b"13052463.0000000000000000").ok(),
        Some(13_052_463.0)
    );
}

// ───────────────────────── разбор CSV ─────────────────────────

#[test]
fn trades_csv_sort_and_bools() {
    let csv =
        "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n\
               1,2.5,4,1,1,1000,true\n\
               2,1.0,2,2,2,999,False\n\
               \n\
               3,10,1,3,3,1001,TRUE\n\
               4,3,3,4,4,1000,false\r\n";
    let d = parse_trades_csv(Cursor::new(csv)).expect("разбор");
    assert_eq!(d.t, vec![999, 1000, 1000, 1001]);
    // устойчивая сортировка: при равном времени порядок файла (запись 1, затем 4)
    assert_eq!(d.v, vec![2.0, 10.0, 9.0, 10.0]);
    assert_eq!(d.buy, vec![true, false, true, false]);
    assert!(d.unsorted);
    assert_eq!(
        parse_trades_csv(Cursor::new("")).expect("пусто"),
        DayTrades::default()
    );
    let head_only =
        "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n";
    assert!(parse_trades_csv(Cursor::new(head_only))
        .expect("шапка")
        .t
        .is_empty());
    assert!(parse_trades_csv(Cursor::new("a,b\n1,2\n")).is_err());
}

#[test]
fn csv_columns_by_name() {
    let c = Csv::from_bytes(b"z,b,a\r\n1,2,3\r\n4,5,6\r\n\r\n");
    let cols = c.columns(&["a", "b"]).expect("столбцы");
    assert_eq!(cols[0], vec![b"3".as_slice(), b"6".as_slice()]);
    assert_eq!(cols[1], vec![b"2".as_slice(), b"5".as_slice()]);
    assert!(c.columns(&["q"]).is_err());
    assert!(
        Csv::from_bytes(b"").columns(&["a"]).is_err(),
        "пустой файл — как EmptyDataError pandas"
    );
}

// ───────────────────────── признаки потока ─────────────────────────

struct Mem(HashMap<i64, DayTrades>);

impl DaySrc for Mem {
    fn day(&self, _sym: &str, abs_day: i64) -> Result<Option<DayTrades>> {
        Ok(self.0.get(&abs_day).cloned())
    }
}

fn day_of(rows: &[(i64, f64, bool)]) -> DayTrades {
    DayTrades {
        t: rows.iter().map(|r| r.0).collect(),
        v: rows.iter().map(|r| r.1).collect(),
        buy: rows.iter().map(|r| r.2).collect(),
        unsorted: false,
    }
}

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() <= 1e-9 * b.abs().max(1.0)
}

#[test]
fn flow_edges_no_history() {
    let d = 20_700;
    let sig = d * DAY_MS + 12 * HOUR_MS;
    let src = Mem(HashMap::from([(
        d,
        day_of(&[
            (sig - 900_000, 6.0, false),
            (sig - 600_000, 6.0, true),
            (sig - 5_000, 10.0, true),
            (sig - 3_000, 20.0, true),
            (sig - 1_000, 10.0, false),
            (sig, 99.0, true), // запись в момент сигнала — вне окон (строго раньше)
        ]),
    )]));
    let eps = [
        Ep {
            row: 0,
            sig,
            side: 1,
            turn24h: f64::NAN,
        },
        Ep {
            row: 1,
            sig,
            side: -1,
            turn24h: f64::NAN,
        },
    ];
    let r = flow_coin(&src, "X", &eps).expect("flow");
    assert_eq!(r.missing_trade_days, 0);
    let f = r.rows[0].1;
    let g = r.rows[1].1;
    assert!(close(f[0], 0.5) && close(f[1], 0.5) && close(f[2], 0.5)); // tfv10/60/300
    assert!(close(f[3], 20.0 / 52.0)); // tfv1800
    assert!(close(f[4], 1.0 / 3.0) && close(f[7], 0.2)); // tfn10, tfn1800
    assert!(close(f[8], (40.0 / 60.0) / (12.0 / 1800.0)));
    assert!(close(f[9], (3.0 / 60.0) / (2.0 / 1800.0)));
    assert!(
        f[10].is_nan() && f[11].is_nan(),
        "big без 24 часов гистограммы"
    );
    assert!(
        f[12].is_nan() && f[15].is_nan(),
        "tfp300 и vpin без суток истории монеты"
    );
    assert!(close(f[13], (40.0f64 / 3.0).log10()));
    assert!(close(f[14], (40.0 / 3.0) / (12.0 / 2.0)));
    assert!(
        close(g[0], -0.5) && close(g[4], -1.0 / 3.0),
        "шорт меняет знак"
    );
    assert!(close(g[8], f[8]), "ускорение от стороны не зависит");
}

#[test]
fn flow_empty_windows_and_zero_volume() {
    let d = 20_700;
    let sig = d * DAY_MS + 12 * HOUR_MS;
    // оборот нулевой: tfv = NaN при N > 0, tfn считается
    let src = Mem(HashMap::from([(
        d,
        day_of(&[(sig - 4_000, 0.0, true), (sig - 3_000, 0.0, false)]),
    )]));
    let f = flow_coin(
        &src,
        "X",
        &[Ep {
            row: 0,
            sig,
            side: 1,
            turn24h: f64::NAN,
        }],
    )
    .expect("flow")
    .rows[0]
        .1;
    assert!(f[0].is_nan() && f[4] == 0.0);
    assert!(f[8].is_nan() && f[13].is_nan() && f[14].is_nan());
    // пустое окно 10 с: всё NaN, окно 30 мин видит запись
    let src = Mem(HashMap::from([(d, day_of(&[(sig - 600_000, 5.0, true)]))]));
    let f = flow_coin(
        &src,
        "X",
        &[Ep {
            row: 0,
            sig,
            side: 1,
            turn24h: f64::NAN,
        }],
    )
    .expect("flow")
    .rows[0]
        .1;
    assert!(f[0].is_nan() && f[4].is_nan() && f[2].is_nan());
    assert!(close(f[3], 1.0) && close(f[7], 1.0));
    // файлов суток нет вовсе: строки не создаются — в таблице остаются NaN (io заполняет ими всё, что не посчитано)
    let f = flow_coin(
        &Mem(HashMap::new()),
        "X",
        &[Ep {
            row: 0,
            sig,
            side: 1,
            turn24h: f64::NAN,
        }],
    )
    .expect("flow");
    assert!(f.rows.is_empty());
}

#[test]
fn flow_with_24h_history() {
    let d = 20_700;
    let sig = d * DAY_MS + 12 * HOUR_MS;
    // сутки d−1: по сделке в каждый час (24 заполненных часа → q99 есть), v = 10; сутки d: окно перед сигналом
    let prev: Vec<(i64, f64, bool)> = (0..24)
        .map(|h| ((d - 1) * DAY_MS + h * HOUR_MS + 1_000, 10.0, h % 2 == 0))
        .collect();
    let cur = [
        (d * DAY_MS + 10 * HOUR_MS, 10.0, true),
        (sig - 200_000, 10.0, true),
        (sig - 100_000, 30.0, false),
        (sig - 1_000, 10.0, true),
    ];
    let src = Mem(HashMap::from([(d - 1, day_of(&prev)), (d, day_of(&cur))]));
    let r = flow_coin(
        &src,
        "X",
        &[Ep {
            row: 0,
            sig,
            side: 1,
            turn24h: f64::NAN,
        }],
    )
    .expect("flow");
    let f = r.rows[0].1;
    // порог q99: все записи в одной корзине (v = 10 и 30 разные) — главное, что он вычислен и big300 конечен и в [0, 1]
    assert!(f[10].is_finite() && (0.0..=1.0).contains(&f[10]));
    assert!(f[11].is_finite() && (0.0..=1.0).contains(&f[11]));
    // tfp300: окно 5 мин = 3 записи (buys 10+10, sell 30): Vb5 = 20, V5 = 50; V24 — записи за сутки до сигнала
    let v24: f64 = prev
        .iter()
        .filter(|r| r.0 >= sig - DAY_MS)
        .map(|r| r.1)
        .sum::<f64>()
        + 10.0
        + 10.0
        + 30.0
        + 10.0;
    assert!(
        close(f[12], (2.0 * 20.0 - 50.0) / (v24 / 288.0)),
        "tfp300 {} vs {}",
        f[12],
        (2.0 * 20.0 - 50.0) / (v24 / 288.0)
    );
    assert!(f[15].is_finite() && (0.0..=1.0).contains(&f[15]));
}

#[test]
fn missing_days_counted_after_first_seen() {
    let d = 20_700;
    let sig = d * DAY_MS + 12 * HOUR_MS;
    // сутки d−9…d−6 есть (первый — d−8), d−5…d−2 нет, d−1 нет, d есть: пропуски считаются с dmin−1 = d−1
    let src = Mem(HashMap::from([
        (d - 8, day_of(&[((d - 8) * DAY_MS + 5, 1.0, true)])),
        (d, day_of(&[(sig - 5_000, 1.0, true)])),
    ]));
    let r = flow_coin(
        &src,
        "X",
        &[Ep {
            row: 0,
            sig,
            side: 1,
            turn24h: f64::NAN,
        }],
    )
    .expect("flow");
    assert_eq!(
        r.missing_trade_days, 1,
        "считается только сутки d−1 (d ≥ dmin−1)"
    );
}

#[test]
fn vpin_hand_cases() {
    let n = 100;
    let v = vec![1.0; n];
    let alt: Vec<bool> = (0..n).map(|i| i % 2 == 0).collect();
    assert_eq!(
        vpin_value(&v, &alt, 0, n),
        0.0,
        "чередование: каждая корзина (2 записи) уравновешена"
    );
    assert_eq!(vpin_value(&v, &vec![true; n], 0, n), 1.0, "одни покупки");
    assert!(vpin_value(&v, &alt, 0, 0).is_nan());
    assert!(
        vpin_value(&vec![0.0; n], &alt, 0, n).is_nan(),
        "нулевой оборот"
    );
    assert!(
        vpin_value(&v[..5], &alt[..5], 0, 5).is_nan(),
        "меньше 10 непустых корзин"
    );
    // срез не с нуля: те же 100 записей внутри большего массива
    let big = [1.0f64; 130];
    let altb: Vec<bool> = (0..130).map(|i| i % 2 == 0).collect();
    assert_eq!(vpin_value(&big, &altb, 20, 120), 0.0);
}

#[test]
fn q99_edge_cases() {
    assert_eq!(q99_edge(&[0i64; NB]), None);
    let mut h = [0i64; NB];
    h[500] = 100;
    assert_eq!(q99_edge(&h), Some(edges()[501]));
    let mut h = [0i64; NB];
    h[NB - 1] = 100;
    assert_eq!(q99_edge(&h), Some(f64::INFINITY));
    let mut h = [0i64; NB];
    h[500] = 99;
    h[900] = 1; // ровно 1 % выше: ge[501..=900] = 1 ≤ 0.01·100 → граница на 501
    assert_eq!(q99_edge(&h), Some(edges()[501]));
}

#[test]
fn flow_features_direct() {
    let t: Vec<i64> = (0..4).map(|i| 1_000 + i * 1000).collect();
    let d = DayTrades {
        t,
        v: vec![1.0, 2.0, 3.0, 4.0],
        buy: vec![true, true, false, true],
        unsorted: false,
    };
    let w = Win::of(&[&d]);
    let f = flow_features(&w, 5_000, 1, None, false);
    // окно 10 с: все 4 записи, Vt = 10, Vb = 7 → (14 − 10)/10
    assert!(close(f[0], 0.4));
    assert!(close(f[4], (2.0 * 3.0 - 4.0) / 4.0));
}

// ───────────────────────── производные ─────────────────────────

fn metrics(ndays: i64, first: Option<i64>) -> Metrics {
    Metrics {
        cols: std::array::from_fn(|_| vec![f64::NAN; (ndays * 288) as usize]),
        first,
    }
}

#[test]
fn deriv_values_and_edges() {
    let d0 = 100;
    let sig = (d0 + 2) * DAY_MS + 3 * HOUR_MS + 7 * MIN_MS + 123;
    let ts = (sig - 10 * MIN_MS).div_euclid(300_000) * 300_000;
    let g = ((ts - d0 * DAY_MS) / 300_000) as usize;
    let mut m = metrics(3, Some(d0 * DAY_MS));
    m.cols[0][g] = 200.0;
    m.cols[0][g - 12] = 100.0;
    m.cols[0][g - 48] = 160.0;
    m.cols[0][g - 288] = 0.0; // не положительный → oi_c24 пуст
    m.cols[1][g] = 5.0e6;
    m.cols[2][g] = 2.0;
    m.cols[2][g - 12] = 1.0;
    m.cols[3][g] = 0.5;
    m.cols[3][g - 12] = f64::NAN;
    m.cols[4][g] = -1.0;
    let ft = [
        sig - 30 * HOUR_MS,
        sig - 25 * HOUR_MS - 1,
        sig - 17 * HOUR_MS,
        sig - 9 * HOUR_MS,
        sig - 3 * HOUR_MS - 3 * MIN_MS,
    ];
    let fr = [0.0001, 0.0002, 0.0004, -0.0001, 0.0003];
    let mut prem = [f64::NAN; 3 * 1440];
    let k = ((sig / MIN_MS) - 1 - d0 * DAY_MS / MIN_MS) as usize;
    prem[k] = 0.001;
    let f = deriv_features(sig, 1.0e6, &m, d0, &ft, &fr, &prem);
    let idx = |n: &str| DERIV.iter().position(|x| *x == n).expect("имя");
    assert!(close(f[idx("oi_c1")], (200.0 / 100.0 - 1.0) * 1e4));
    assert!(close(f[idx("oi_c4")], (200.0 / 160.0 - 1.0) * 1e4));
    assert!(f[idx("oi_c24")].is_nan());
    assert!(close(f[idx("loi_turn")], (5.0e6f64 / 1.0e6).log10()));
    assert!(
        close(f[idx("ls_tc")], 2.0f64.ln()) && close(f[idx("dls_tc")], 2.0f64.ln() - 1.0f64.ln())
    );
    assert!(
        close(f[idx("ls_ts")], 0.5f64.ln()) && f[idx("dls_ts")].is_nan(),
        "x1 пуст → только уровень"
    );
    assert!(f[idx("ls_a")].is_nan(), "x ≤ 0");
    // фандинг: последний расчёт ≤ sig — индекс 4 (sig − 3 ч 3 мин), день назад — последний ≤ sig − 24 ч → индекс 1
    assert!(close(f[idx("fund_last")], 0.0003 * 1e4));
    assert!(close(f[idx("fund_chg24")], (0.0003 - 0.0002) * 1e4));
    assert_eq!(
        f[idx("fund_int")],
        6.0,
        "5 ч 57 мин между последними расчётами → 6 ч"
    );
    assert!(close(f[idx("basis")], 0.001 * 1e4));
    // монета моложе суток на T*: metrics-блок пуст, фандинг и базис остаются
    let young = metrics(3, Some(ts - DAY_MS + 1));
    let f = deriv_features(sig, 1.0e6, &young, d0, &ft, &fr, &prem);
    assert!(f[..10].iter().all(|x| x.is_nan()));
    assert!(f[idx("fund_last")].is_finite() && f[idx("basis")].is_finite());
    // turn24h NaN → loi_turn пуст, остальное на месте
    let f = deriv_features(sig, f64::NAN, &m, d0, &ft, &fr, &prem);
    assert!(f[idx("loi_turn")].is_nan() && f[idx("oi_c1")].is_finite());
    // нет расчётов фандинга до sig
    let f = deriv_features(sig, 1.0e6, &m, d0, &[sig + 1], &[0.1], &prem);
    assert!(f[10..13].iter().all(|x| x.is_nan()));
}

#[test]
fn deriv_funding_interval_rounding() {
    let d0 = 100;
    let sig = (d0 + 2) * DAY_MS;
    let m = metrics(3, None);
    let prem: Vec<f64> = Vec::new();
    // разность расчётов 2.5 ч → round_ties_even = 2; 3.5 ч → 4; 1.5 ч → 2; 8 ч → 8
    for (gap_ms, want) in [
        (150 * MIN_MS, 2.0),
        (210 * MIN_MS, 4.0),
        (90 * MIN_MS, 2.0),
        (8 * HOUR_MS, 8.0),
    ] {
        let ft = [sig - 2 * DAY_MS, sig - 1_000 - gap_ms, sig - 1_000];
        let f = deriv_features(sig, f64::NAN, &m, d0, &ft, &[0.0, 0.0, 0.0], &prem);
        assert_eq!(f[12], want, "интервал {gap_ms} мс");
    }
    // единственный расчёт: fund_last есть, fund_int пуст
    let f = deriv_features(sig, f64::NAN, &m, d0, &[sig - 5], &[0.01], &prem);
    assert!(close(f[10], 100.0) && f[12].is_nan());
}

// ───────────────────────── вход/выход ─────────────────────────

#[test]
fn read_eps_filters_and_nan() {
    let dir = tempfile::tempdir().expect("tmp");
    let p = dir.path().join("e.csv");
    std::fs::write(&p, "ep_id,sym,side,sig_ms,turn24h\na1,AAA,long,1000,5.5\na2,BBB,short,2000,\na3,AAA,short,3000,1e6\n").expect("write");
    let all = read_eps(&p, 10_000, None).expect("read");
    assert_eq!(all.len(), 3);
    assert_eq!(
        (all[0].ep.sig, all[0].ep.side, all[1].ep.side),
        (11_000, 1, -1)
    );
    assert!(all[1].ep.turn24h.is_nan() && all[2].ep.turn24h == 1.0e6);
    let only = read_eps(&p, 0, Some(&["AAA".to_string()])).expect("read");
    assert_eq!(
        (only.len(), only[1].ep.row, only[1].ep_id.as_str()),
        (2, 1, "a3")
    );
    std::fs::write(&p, "ep_id,sym,side,sig_ms,turn24h\na1,AAA,long,x,1\n").expect("write");
    assert!(read_eps(&p, 0, None).is_err());
    std::fs::write(&p, "ep_id,sym\na1,AAA\n").expect("write");
    assert!(read_eps(&p, 0, None).is_err());
}

#[test]
fn run_end_to_end_without_data_files() {
    let dir = tempfile::tempdir().expect("tmp");
    let eps = dir.path().join("e.csv");
    let out = dir.path().join("o.csv");
    std::fs::write(&eps, "ep_id,sym,side,sig_ms,turn24h\na1,AAA,long,1000,5.5\na2,BBB,short,2000,\na3,AAA,short,3000,7\n").expect("write");
    let cfg = |mode| Cfg {
        mode,
        eps: eps.clone(),
        out: out.clone(),
        base_ms: 20_700 * DAY_MS,
        threads: 2,
        coins: None,
        trades: vec![dir.path().join("trades")],
        deriv: super::deriv::DerivDirs {
            metrics: dir.path().join("m"),
            premium: dir.path().join("p"),
            funding_csv: dir.path().join("fc"),
            funding_m: dir.path().join("fm"),
        },
    };
    for mode in [Mode::Flow, Mode::Deriv, Mode::All] {
        let s = run(&cfg(mode)).expect("run");
        assert_eq!(
            (s.episodes, s.coins, s.missing_trade_days, s.unsorted_days),
            (3, 2, 0, 0)
        );
        let text = std::fs::read_to_string(&out).expect("out");
        let lines: Vec<&str> = text.lines().collect();
        assert_eq!(lines.len(), 4);
        assert!(lines[0].starts_with("ep_id,tfv10,") && lines[0].ends_with(",basis"));
        assert_eq!(lines[0].split(',').count(), 31);
        assert!(
            lines[1].starts_with("a1,")
                && lines[2].starts_with("a2,")
                && lines[3].starts_with("a3,"),
            "порядок как на входе"
        );
        assert!(
            lines[1..].iter().all(|l| l[3..].chars().all(|c| c == ',')),
            "без данных все признаки пусты"
        );
    }
    assert!(s_json_has_keys(&run(&cfg(Mode::Flow)).expect("run").json()));
    // фандинг есть, сделок нет: deriv даёт fund_last, flow — пусто
    let fdir = dir.path().join("fc").join("AAA");
    std::fs::create_dir_all(&fdir).expect("mkdir");
    std::fs::write(
        fdir.join("funding.csv"),
        "funding_time,funding_rate\n1000,0.0001\n",
    )
    .expect("write");
    let s = run(&cfg(Mode::Deriv)).expect("run");
    assert_eq!(s.episodes, 3);
    let text = std::fs::read_to_string(&out).expect("out");
    let l1: Vec<&str> = text.lines().nth(1).expect("строка").split(',').collect();
    assert!(
        l1[1..17].iter().all(|x| x.is_empty()),
        "flow пуст в режиме deriv"
    );
    assert_eq!(
        l1[17 + 10],
        "1.0",
        "fund_last = 0.0001·1e4 (до сигнала расчёт был)"
    );
}

fn s_json_has_keys(j: &str) -> bool {
    [
        "episodes",
        "coins",
        "seconds",
        "missing_trade_days",
        "unsorted_days",
    ]
    .iter()
    .all(|k| j.contains(&format!("\"{k}\":")))
}
