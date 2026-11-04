//! Тесты `stats` и `verdict` (TK-492, П-34 §5–§8): математика против наивной реализации и малых примеров,
//! отбор K и ничьи, вердикты на трёх заготовленных сценариях, детерминизм по числу потоков.

use super::control::{stream, SplitMix64};
use super::stats::{bootstrap, draw_weights, holm, interval, quantile_floor, Sparse};
use super::verdict::{
    lift_verdict, share_ok, verdict, LiftIn, Stage, VParams, VShare, Verdict, VerdictArgs,
};
use crate::lob::costs::{leg_fee_bps, ROUNDTRIP_FEES_BPS};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

const PARAMS: &str = include_str!("../../docs/research/P-34-params.json");

fn close(a: &[f64], b: &[f64]) -> bool {
    a.len() == b.len() && a.iter().zip(b).all(|(x, y)| (x - y).abs() < 1e-12)
}

fn approx(a: f64, b: f64) -> bool {
    (a - b).abs() < 1e-9
}

// ---------- stats: квантиль, интервал, Холм ----------

#[test]
fn quantile_floor_small_examples() {
    let s = [1.0, 2.0, 3.0, 4.0, 5.0];
    assert_eq!(quantile_floor(&s, 0.0), 1.0);
    assert_eq!(quantile_floor(&s, 0.5), 3.0);
    assert_eq!(quantile_floor(&s, 0.999), 4.0);
    assert_eq!(quantile_floor(&s, 1.0), 5.0);
    assert_eq!(quantile_floor(&s, 7.0), 5.0);
    assert_eq!(quantile_floor(&s, -1.0), 1.0);
    assert!(quantile_floor(&[], 0.5).is_nan());
    assert_eq!(quantile_floor(&[9.0], 0.975), 9.0);
    // граница целого индекса: 0,975·40 и 0,025·40 не должны уйти на единицу вниз из-за двоичной ошибки q
    let v: Vec<f64> = (0..=40).map(f64::from).collect();
    assert_eq!(quantile_floor(&v, 0.975), 39.0);
    assert_eq!(quantile_floor(&v, 0.025), 1.0);
}

#[test]
fn interval_percentiles_and_p() {
    let reps: Vec<f64> = (1..=100).map(f64::from).collect();
    let iv = interval(&reps, 0.95, 50.0);
    assert_eq!((iv.lo, iv.hi, iv.valid), (3.0, 97.0, 100));
    assert!(approx(iv.p, 51.0 / 101.0));
    // B = 1: интервал вырожден, p = (1 + #≤нуль)/2
    let one = interval(&[5.0], 0.95, 1.0);
    assert_eq!((one.lo, one.hi, one.valid), (5.0, 5.0, 1));
    assert!(approx(one.p, 0.5));
    assert!(approx(interval(&[0.5], 0.95, 1.0).p, 1.0));
    // NaN: не входит в границы, но входит в счёт p как «не отвергла нуль»
    let nan = interval(&[f64::NAN, 2.0, 3.0], 0.95, 1.0);
    assert_eq!(nan.valid, 2);
    assert!(approx(nan.p, 0.5));
    let all = interval(&[f64::NAN; 4], 0.95, 1.0);
    assert!(all.lo.is_nan() && all.hi.is_nan());
    assert_eq!(all.valid, 0);
    assert!(approx(all.p, 1.0));
    // +inf (база нулевая) сортируется последним и не ломает нижнюю границу
    let inf = interval(&[f64::INFINITY, 2.0, 3.0, f64::INFINITY], 0.95, 1.0);
    assert_eq!(inf.lo, 2.0);
    assert!(inf.hi.is_infinite());
}

#[test]
fn holm_step_down_examples() {
    assert!(close(&holm(&[0.01, 0.04, 0.03], 3), &[0.03, 0.06, 0.06]));
    // m больше числа p: недостающие p = 1, множители (m − j)
    assert!(close(&holm(&[0.01, 0.04, 0.03], 5), &[0.05, 0.12, 0.12]));
    // монотонность скорректированных p по порядку возрастания p
    assert!(close(&holm(&[0.02, 0.021], 2), &[0.04, 0.04]));
    // потолок 1
    assert!(close(&holm(&[0.6, 0.7], 2), &[1.0, 1.0]));
    // ничья p — по порядку входа
    assert!(close(&holm(&[0.01, 0.01], 2), &[0.02, 0.02]));
    // NaN считается 1; m меньше числа p поднимается до числа p
    assert!(close(&holm(&[f64::NAN, 0.001], 1), &[1.0, 0.002]));
    assert!(holm(&[], 60).is_empty());
    // протокол: m = 60, одно p = 1/3001 → 60/3001
    assert!(approx(holm(&[1.0 / 3001.0], 60)[0], 60.0 / 3001.0));
}

// ---------- stats: бутстрэп ----------

#[test]
fn weights_are_multinomial_and_deterministic() {
    let mut w1 = vec![0u32; 25];
    draw_weights(34, 7, &mut w1);
    let mut w2 = vec![0u32; 25];
    draw_weights(34, 7, &mut w2);
    assert_eq!(w1, w2);
    assert_eq!(w1.iter().sum::<u32>(), 25);
    let mut w3 = vec![0u32; 25];
    draw_weights(34, 8, &mut w3);
    assert_ne!(w1, w3);
    let mut w4 = vec![0u32; 25];
    draw_weights(35, 7, &mut w4);
    assert_ne!(w1, w4);
}

/// Три ячейки на 12 суток с целыми значениями (точное сравнение), часть суток без записей.
fn sample_sparse() -> (Sparse<2>, Vec<Vec<[f64; 2]>>) {
    let ndays = 12usize;
    let mut sp = Sparse::<2>::new();
    let mut dense = Vec::new();
    let mut g = SplitMix64::new(7);
    for _ in 0..3 {
        let mut row = vec![[0.0; 2]; ndays];
        for (d, cell) in row.iter_mut().enumerate() {
            if g.below(3) == 0 {
                continue;
            }
            let v = [g.below(10) as f64 + 1.0, g.below(5) as f64];
            sp.push(d as u32, v);
            *cell = v;
        }
        sp.end_cell();
        dense.push(row);
    }
    (sp, dense)
}

#[test]
fn bootstrap_matches_naive_resampling() {
    let (sp, dense) = sample_sparse();
    let (ndays, b, seed) = (12usize, 50usize, 34u64);
    let rep = bootstrap::<2, 1, _>(&sp, ndays, b, seed, 3, |s, out| out[0] = s[1] / s[0]).unwrap();
    assert_eq!(rep.b(), b);
    for r in 0..b {
        // наивно: те же розыгрыши суток списком, статистика по строкам выбранных суток
        let mut rng = stream(seed, r as u64);
        let draws: Vec<usize> = (0..ndays)
            .map(|_| rng.below(ndays as u64) as usize)
            .collect();
        for (c, cell) in dense.iter().enumerate() {
            let mut s = [0.0; 2];
            for &d in &draws {
                s[0] += cell[d][0];
                s[1] += cell[d][1];
            }
            let want = s[1] / s[0];
            let got = rep.get(c, 0)[r];
            if want.is_nan() {
                assert!(got.is_nan());
            } else {
                assert_eq!(got.to_bits(), want.to_bits(), "ячейка {c} реплика {r}");
            }
        }
    }
}

#[test]
fn bootstrap_is_independent_of_thread_count() {
    let (sp, _) = sample_sparse();
    let run = |threads: usize| {
        bootstrap::<2, 2, _>(&sp, 12, 40, 34, threads, |s, out| {
            out[0] = s[1] / s[0];
            out[1] = s[0] - s[1];
        })
        .unwrap()
    };
    let base: Vec<u64> = run(1).raw().iter().map(|x| x.to_bits()).collect();
    for t in [2usize, 3, 4, 7, 64] {
        let got: Vec<u64> = run(t).raw().iter().map(|x| x.to_bits()).collect();
        assert_eq!(got, base, "потоков {t}");
    }
}

#[test]
fn bootstrap_constant_days_give_degenerate_interval() {
    let mut sp = Sparse::<2>::new();
    for d in 0..9u32 {
        sp.push(d, [2.0, 6.0]);
    }
    sp.end_cell();
    sp.end_cell(); // пустая ячейка
    let rep = bootstrap::<2, 1, _>(&sp, 9, 200, 34, 2, |s, out| out[0] = s[1] / s[0]).unwrap();
    assert!(rep.get(0, 0).iter().all(|&x| x == 3.0));
    let iv = interval(rep.get(0, 0), 0.95, 1.0);
    assert_eq!((iv.lo, iv.hi, iv.valid), (3.0, 3.0, 200));
    assert!(approx(iv.p, 1.0 / 201.0));
    // пустая ячейка: статистика не определена во всех репликах
    let empty = interval(rep.get(1, 0), 0.95, 1.0);
    assert_eq!(empty.valid, 0);
    assert!(approx(empty.p, 1.0));
}

#[test]
fn bootstrap_two_clusters_known_distribution() {
    // сутки 0: попадание (1 из 1), сутки 1: промах (0 из 1); при 2 розыгрышах доля попаданий ∈ {0, ½, 1}
    // с вероятностями ¼, ½, ¼
    let mut sp = Sparse::<2>::new();
    sp.push(0, [1.0, 1.0]);
    sp.push(1, [1.0, 0.0]);
    sp.end_cell();
    let b = 4000usize;
    let rep = bootstrap::<2, 1, _>(&sp, 2, b, 34, 1, |s, out| out[0] = s[1] / s[0]).unwrap();
    let v = rep.get(0, 0);
    assert!(v.iter().all(|&x| x == 0.0 || x == 0.5 || x == 1.0));
    let freq = |t: f64| v.iter().filter(|&&x| x == t).count() as f64 / b as f64;
    assert!((freq(0.0) - 0.25).abs() < 0.05);
    assert!((freq(0.5) - 0.5).abs() < 0.05);
    assert!((freq(1.0) - 0.25).abs() < 0.05);
}

#[test]
fn bootstrap_single_replica_and_guards() {
    let mut sp = Sparse::<2>::new();
    sp.push(0, [1.0, 1.0]);
    sp.push(1, [3.0, 0.0]);
    sp.end_cell();
    let rep = bootstrap::<2, 1, _>(&sp, 2, 1, 34, 4, |s, out| out[0] = s[1] / s[0]).unwrap();
    assert_eq!(rep.get(0, 0).len(), 1);
    assert!(!rep.get(0, 0)[0].is_nan());
    // индекс суток вне числа суток — отказ
    assert!(bootstrap::<2, 1, _>(&sp, 1, 5, 34, 1, |s, out| out[0] = s[0]).is_err());
    // нет ячеек / нет реплик — пустой результат без паники
    let none = Sparse::<2>::new();
    assert_eq!(
        bootstrap::<2, 1, _>(&none, 3, 5, 34, 2, |s, out| out[0] = s[0])
            .unwrap()
            .raw()
            .len(),
        0
    );
}

// ---------- verdict: правило §5 ----------

fn li(fit_lo: f64, test_lo: f64, test_hi: f64, p: f64, gt: usize, total: usize) -> LiftIn {
    LiftIn {
        fit_lo,
        test_lo,
        test_hi,
        p_holm: p,
        months_gt1: gt,
        months_total: total,
    }
}

#[test]
fn share_ok_boundaries() {
    let two_thirds = VShare { num: 2, den: 3 };
    assert!(share_ok(2, 3, &two_thirds));
    assert!(share_ok(3, 3, &two_thirds));
    assert!(!share_ok(1, 3, &two_thirds));
    assert!(share_ok(7, 10, &two_thirds));
    assert!(!share_ok(6, 10, &two_thirds));
    assert!(!share_ok(0, 0, &two_thirds));
}

#[test]
fn lift_verdict_rule_table() {
    let vp = VParams::parse(PARAMS).unwrap();
    let v = |i: LiftIn| lift_verdict(&vp, &i);
    assert_eq!(v(li(1.5, 1.2, 3.0, 0.01, 2, 3)), Verdict::Confirmed);
    // не хватает 2/3 месяцев
    assert_eq!(v(li(1.5, 1.2, 3.0, 0.01, 1, 3)), Verdict::Indistinct);
    // p-Холм ровно на пороге — не «меньше»
    assert_eq!(v(li(1.5, 1.2, 3.0, 0.05, 3, 3)), Verdict::Indistinct);
    // фит: нижняя граница не выше 1
    assert_eq!(v(li(1.0, 1.2, 3.0, 0.01, 3, 3)), Verdict::Indistinct);
    // тест: нижняя граница не выше 1
    assert_eq!(v(li(1.5, 1.0, 3.0, 0.01, 3, 3)), Verdict::Indistinct);
    // оспорено: верхняя граница в тесте ниже 1
    assert_eq!(v(li(1.5, 0.2, 0.9, 1.0, 0, 3)), Verdict::Contested);
    assert_eq!(v(li(f64::NAN, 0.2, 0.9, 1.0, 0, 3)), Verdict::Contested);
    // верхняя граница ровно 1 — не «ниже»
    assert_eq!(v(li(1.5, 0.2, 1.0, 1.0, 0, 3)), Verdict::Indistinct);
    // неопределённое всё — не различимо
    let nan = f64::NAN;
    assert_eq!(v(li(nan, nan, nan, nan, 0, 0)), Verdict::Indistinct);
}

#[test]
fn params_with_unsupported_sections_are_rejected() {
    let edit = |f: &dyn Fn(&mut serde_json::Value)| -> String {
        let mut v: serde_json::Value = serde_json::from_str(PARAMS).unwrap();
        f(&mut v);
        v.to_string()
    };
    assert!(VParams::parse(PARAMS).is_ok());
    assert!(VParams::parse(&edit(
        &|v| v["verdict"]["cluster"] = serde_json::json!("symbol_day")
    ))
    .is_err());
    assert!(VParams::parse(&edit(
        &|v| v["verdict"]["rank"]["H_min"] = serde_json::json!(7)
    ))
    .is_err());
    assert!(VParams::parse(&edit(
        &|v| v["verdict"]["bootstrap_B"] = serde_json::json!(0)
    ))
    .is_err());
    assert!(VParams::parse(&edit(&|v| v["costs"]["main"] = serde_json::json!("0"))).is_err());
}

// ---------- verdict: сценарии на файлах ----------

const DC_HEAD: &str = "fam,wi,ti,sign,hi,day,month,n,hit,bn,bhit,nret,gross_bps,fund_bps\n";

/// Строка `daycells`: ключ (fam, wi, ti, sign, hi), сутки, месяц, счётчики [n, hit, bn, bhit, nret], gross, fund.
fn dc(
    key: (u8, u8, u8, i8, u8),
    day: i64,
    month: i32,
    c: [u64; 5],
    gross: f64,
    fund: f64,
) -> String {
    format!(
        "{},{},{},{},{},{},{},{},{},{},{},{},{},{}\n",
        key.0, key.1, key.2, key.3, key.4, day, month, c[0], c[1], c[2], c[3], c[4], gross, fund
    )
}

struct FitRule {
    key: (u8, u8, u8),
    signs: &'static [i8],
    hit: u64,
    /// Нечётные сутки без попаданий: интервал невырожденный.
    alt: bool,
}

/// Фит: n = 10 срабатываний в сутки, база C2a 10 из 100 (0,1) → lift = hit (для hit из 10).
fn fit_rules() -> Vec<FitRule> {
    let both: &'static [i8] = &[-1, 1];
    let r = |key, signs, hit, alt| FitRule {
        key,
        signs,
        hit,
        alt,
    };
    vec![
        r((0, 1, 2), both, 3, false),
        r((0, 1, 0), both, 3, false),
        r((0, 0, 0), both, 3, false),
        r((1, 0, 0), both, 2, false),
        r((2, 0, 0), both, 2, false),
        r((3, 0, 0), both, 1, false),
        r((9, 2, 1), both, 4, false),
        r((4, 0, 0), &[1], 5, false),
        r((5, 0, 0), both, 3, true),
    ]
}

fn gen_fit(rules: &[FitRule]) -> String {
    let mut s = String::from(DC_HEAD);
    for r in rules {
        for &sign in r.signs {
            for hi in 0..3u8 {
                for d in 0..24i64 {
                    let month = 202501 + (d / 12) as i32;
                    let hit = if r.alt && d % 2 == 1 { 0 } else { r.hit };
                    let key = (r.key.0, r.key.1, r.key.2, sign, hi);
                    s.push_str(&dc(
                        key,
                        20000 + d,
                        month,
                        [10, hit, 100, 10, 10],
                        1000.0,
                        0.0,
                    ));
                }
            }
        }
    }
    s
}

/// Тест (60 суток во всех 10 месяцах `split.test` 2026-01…2026-10, по 6 суток): X (0,0,0) — lift 3, net +;
/// Y (1,0,0) — lift 0,5, net −; Z (2,0,0) — lift ≈ 1 с разбросом по суткам. Плюс строка X вне периода теста и
/// строка правила вне K.
fn gen_test() -> String {
    let mut s = String::from(DC_HEAD);
    for d in 0..60i64 {
        let month = 202601 + (d / 6) as i32;
        for sign in [-1i8, 1] {
            for hi in 0..3u8 {
                let day = 30000 + d;
                s.push_str(&dc(
                    (0, 0, 0, sign, hi),
                    day,
                    month,
                    [10, 3, 100, 10, 10],
                    1000.0,
                    0.0,
                ));
                s.push_str(&dc(
                    (1, 0, 0, sign, hi),
                    day,
                    month,
                    [10, 1, 100, 20, 10],
                    -500.0,
                    0.0,
                ));
                let (h, g) = if d % 2 == 0 {
                    (6, 1000.0)
                } else {
                    (0, -1000.0)
                };
                s.push_str(&dc(
                    (2, 0, 0, sign, hi),
                    day,
                    month,
                    [10, h, 100, 30, 10],
                    g,
                    0.0,
                ));
            }
        }
    }
    s.push_str(&dc(
        (0, 0, 0, 1, 1),
        30100,
        202611,
        [10, 3, 100, 10, 10],
        1000.0,
        0.0,
    ));
    s.push_str(&dc(
        (5, 0, 0, 1, 1),
        30000,
        202601,
        [10, 3, 100, 10, 10],
        1000.0,
        0.0,
    ));
    s
}

const LAGS_HEAD: &str =
    "kind,stratum,fam,wi,ti,sign,di,month,n_ep,n_fired,n_lt0,n_le0,n_le30,n_le60,p10,p50,p90\n";
const FIRST_HEAD: &str = "kind,stratum,wi,ti,sign,di,fam,n_ep,n_first\n";
const PROG_HEAD: &str = "stratum,fam,wi,ti,sign,di,month,n_fired,done_bps_p10,done_bps_p50,done_bps_p90,left_share_p10,left_share_p50,left_share_p90,to_peak_bps_p10,to_peak_bps_p50,to_peak_bps_p90\n";

fn gen_lags() -> String {
    [
        LAGS_HEAD,
        "ep,all,0,0,0,1,0,202601,10,8,2,4,6,7,-30,10,120\n",
        "ep,all,0,0,0,1,0,202602,10,6,1,2,3,4,-10,20,200\n",
        "c2b,all,0,0,0,1,0,202601,10,5,0,1,2,3,0,30,100\n",
        "ep,market,0,0,0,1,0,202601,4,3,1,1,2,3,-5,5,50\n",
        "ep,all,7,0,0,1,0,202601,10,8,2,4,6,7,-30,10,120\n",
    ]
    .concat()
}

fn gen_first() -> String {
    [
        FIRST_HEAD,
        "ep,all,0,0,1,0,0,20,10\n",
        "ep,all,0,0,1,0,1,20,6\n",
        "c2b,all,0,0,1,0,0,20,4\n",
        "c2b,all,0,0,1,0,1,20,6\n",
    ]
    .concat()
}

fn gen_progress() -> String {
    [
        PROG_HEAD,
        "all,0,0,0,1,0,202601,10,50,100,150,0.2,0.5,0.8,100,300,600\n",
        "all,0,0,0,1,0,202602,30,80,200,400,0.1,0.25,0.9,200,500,900\n",
        "market,0,0,0,1,0,202601,5,1,2,3,0.1,0.2,0.3,1,2,3\n",
        "all,7,0,0,1,0,202601,5,1,2,3,0.1,0.2,0.3,1,2,3\n",
    ]
    .concat()
}

fn params_file(dir: &Path, edit: impl FnOnce(&mut serde_json::Value)) -> PathBuf {
    let mut v: serde_json::Value = serde_json::from_str(PARAMS).unwrap();
    edit(&mut v);
    let p = dir.join("params.json");
    fs::write(&p, serde_json::to_string_pretty(&v).unwrap()).unwrap();
    p
}

fn run_verdict(
    stage: Stage,
    input: &Path,
    params: &Path,
    out: &Path,
    k: Option<&Path>,
    threads: usize,
) -> anyhow::Result<()> {
    verdict(VerdictArgs {
        stage,
        input: input.to_path_buf(),
        in_fit: None,
        params: params.to_path_buf(),
        out: out.to_path_buf(),
        k: k.map(Path::to_path_buf),
        threads,
    })
}

fn table(path: &Path) -> Vec<HashMap<String, String>> {
    let mut rd = csv::Reader::from_path(path).unwrap();
    let head: Vec<String> = rd.headers().unwrap().iter().map(String::from).collect();
    rd.records()
        .map(|r| {
            let r = r.unwrap();
            head.iter()
                .cloned()
                .zip(r.iter().map(String::from))
                .collect()
        })
        .collect()
}

fn num(row: &HashMap<String, String>, col: &str) -> f64 {
    row[col].parse::<f64>().unwrap()
}

/// Каталог входа со всеми таблицами `run` для обеих стадий.
fn inputs(dir: &Path, with_progress: bool) -> PathBuf {
    let inp = dir.join("in");
    fs::create_dir_all(&inp).unwrap();
    fs::write(inp.join("daycells-fit.csv"), gen_fit(&fit_rules())).unwrap();
    fs::write(inp.join("daycells-test.csv"), gen_test()).unwrap();
    fs::write(inp.join("lags-test.csv"), gen_lags()).unwrap();
    fs::write(inp.join("first-test.csv"), gen_first()).unwrap();
    if with_progress {
        fs::write(inp.join("progress-test.csv"), gen_progress()).unwrap();
    }
    inp
}

fn k_file(dir: &Path, rules: &[(u8, u8, u8)]) -> PathBuf {
    let items: Vec<serde_json::Value> = rules
        .iter()
        .map(|r| serde_json::json!({"fam": r.0, "wi": r.1, "ti": r.2}))
        .collect();
    let p = dir.join("k-test.json");
    fs::write(&p, serde_json::to_string(&items).unwrap()).unwrap();
    p
}

#[test]
fn fit_selects_k_with_family_best_and_ties() {
    let d = tempfile::tempdir().unwrap();
    let inp = inputs(d.path(), false);
    let params = params_file(d.path(), |v| v["verdict"]["K"] = serde_json::json!(3));
    let out = d.path().join("out");
    run_verdict(Stage::Fit, &inp, &params, &out, None, 2).unwrap();

    let k: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out.join("k.json")).unwrap()).unwrap();
    let arr = k.as_array().unwrap();
    let got: Vec<(u64, u64, u64)> = arr
        .iter()
        .map(|o| {
            (
                o["fam"].as_u64().unwrap(),
                o["wi"].as_u64().unwrap(),
                o["ti"].as_u64().unwrap(),
            )
        })
        .collect();
    // S_oi (lift 4) > F1 (lift 3: ничья трёх правил → меньший θ, затем меньший w) > F3 (lift 2) и F4 (тоже 2,
    // ничья между семействами → меньший индекс)
    assert_eq!(got, vec![(9, 2, 1), (0, 0, 0), (1, 0, 0)]);
    let ranks: Vec<f64> = arr.iter().map(|o| o["rank_lo"].as_f64().unwrap()).collect();
    assert!(approx(ranks[0], 4.0) && approx(ranks[1], 3.0) && approx(ranks[2], 2.0));
    assert_eq!(arr[0]["pos"].as_u64(), Some(1));

    // ранги всех правил: правило без данных во втором знаке — «без ранга»
    let rank = table(&out.join("rank-fit.csv"));
    let by = |fam: &str, wi: &str, ti: &str| {
        rank.iter()
            .find(|r| r["fam"] == fam && r["wi"] == wi && r["ti"] == ti)
            .unwrap()
    };
    assert_eq!(by("4", "0", "0")["status"], "no_data");
    assert_eq!(by("9", "2", "1")["status"], "ranked");
    assert_eq!(by("9", "2", "1")["k_pos"], "1");
    assert_eq!(by("0", "0", "0")["family_best"], "1");
    assert_eq!(by("0", "1", "0")["family_best"], "0");
    // невырожденное правило: нижняя граница строго ниже точки, точка = 1,5
    let alt = by("5", "0", "0");
    assert!(approx(num(alt, "lift_minus"), 1.5));
    assert!(num(alt, "rank_lo") < 1.5 && num(alt, "rank_lo") > 0.0);

    // все ячейки: 8 правил × 2 знака × 3 H + правило без второго знака 3
    let lifts = fs::read_to_string(out.join("lift-fit.csv")).unwrap();
    assert_eq!(lifts.lines().count(), 1 + 8 * 6 + 3);
    let line = fs::read_to_string(out.join("journal-line.txt")).unwrap();
    assert!(line.contains("K=3") && line.contains("sha256") && line.contains("2025-01"));
}

#[test]
fn fit_rejects_months_outside_fit_period() {
    let d = tempfile::tempdir().unwrap();
    let inp = d.path().join("in");
    fs::create_dir_all(&inp).unwrap();
    let mut s = gen_fit(&fit_rules());
    s.push_str(&dc(
        (0, 0, 0, 1, 1),
        30000,
        202601,
        [10, 3, 100, 10, 10],
        1000.0,
        0.0,
    ));
    fs::write(inp.join("daycells-fit.csv"), s).unwrap();
    let params = params_file(d.path(), |_| {});
    let err = run_verdict(Stage::Fit, &inp, &params, &d.path().join("out"), None, 1)
        .unwrap_err()
        .to_string();
    assert!(err.contains("вне split"), "{err}");
}

#[test]
fn fit_is_byte_identical_for_one_and_four_threads() {
    let d = tempfile::tempdir().unwrap();
    let inp = inputs(d.path(), false);
    let params = params_file(d.path(), |_| {});
    let (o1, o4) = (d.path().join("o1"), d.path().join("o4"));
    run_verdict(Stage::Fit, &inp, &params, &o1, None, 1).unwrap();
    run_verdict(Stage::Fit, &inp, &params, &o4, None, 4).unwrap();
    for f in ["lift-fit.csv", "rank-fit.csv", "k.json", "journal-line.txt"] {
        assert_eq!(
            fs::read(o1.join(f)).unwrap(),
            fs::read(o4.join(f)).unwrap(),
            "{f}"
        );
    }
}

#[test]
fn test_stage_three_scenarios() {
    let d = tempfile::tempdir().unwrap();
    let inp = inputs(d.path(), false);
    let params = params_file(d.path(), |_| {});
    let k = k_file(d.path(), &[(0, 0, 0), (1, 0, 0), (2, 0, 0)]);
    let out = d.path().join("out");
    run_verdict(Stage::Test, &inp, &params, &out, Some(&k), 2).unwrap();

    let vt = table(&out.join("verdict-test.csv"));
    assert_eq!(vt.len(), 18);
    let verdicts = |fam: &str| -> Vec<String> {
        vt.iter()
            .filter(|r| r["fam"] == fam)
            .map(|r| r["verdict"].clone())
            .collect()
    };
    assert_eq!(verdicts("0"), vec!["подтверждено"; 6]);
    assert_eq!(verdicts("1"), vec!["оспорено"; 6]);
    assert_eq!(verdicts("2"), vec!["не различимо"; 6]);

    // известные числа сценария X: точность 0,3 против базы 0,1, lift 3 без разброса, p = 1/(B+1), 3 из 3 месяцев
    let x = vt
        .iter()
        .find(|r| r["fam"] == "0" && r["sign"] == "1" && r["hi"] == "1")
        .unwrap();
    assert_eq!(x["h_min"], "360");
    assert!(approx(num(x, "precision"), 0.3) && approx(num(x, "base"), 0.1));
    assert!(approx(num(x, "lift"), 3.0));
    assert!(approx(num(x, "lift_lo"), 3.0) && approx(num(x, "lift_hi"), 3.0));
    assert!((num(x, "p") - 1.0 / 3001.0).abs() < 1e-6);
    assert!(num(x, "p_holm") < 0.05 && num(x, "p_holm") >= num(x, "p"));
    assert!(approx(num(x, "fit_lift_lo"), 3.0));
    assert_eq!(
        (x["months_gt1"].as_str(), x["months_total"].as_str()),
        ("10", "10")
    );
    // сценарий Z: интервал невырожденный и накрывает 1
    let z = vt
        .iter()
        .find(|r| r["fam"] == "2" && r["sign"] == "1" && r["hi"] == "1")
        .unwrap();
    assert!(num(z, "lift_lo") < 1.0 && num(z, "lift_hi") > 1.0);
    assert!(approx(num(z, "lift"), 1.0));

    // net: gross 100 bps на сделку, фандинга нет; основной вариант вычитает 2·leg_fee_bps(true)
    let nt = table(&out.join("net-test.csv"));
    let fee = 2.0 * leg_fee_bps(true);
    let xn = nt
        .iter()
        .find(|r| r["fam"] == "0" && r["sign"] == "1" && r["hi"] == "1")
        .unwrap();
    assert_eq!(xn["main"], "1");
    assert!(approx(num(xn, "fee_main"), fee) && approx(num(xn, "fee_second"), ROUNDTRIP_FEES_BPS));
    assert!(approx(num(xn, "net_main"), 100.0 - fee));
    assert!(approx(num(xn, "net_second"), 100.0 - ROUNDTRIP_FEES_BPS));
    assert!(approx(num(xn, "net_main_lo"), 100.0 - fee));
    assert!(approx(num(xn, "breakeven_slip_main"), 100.0 - fee));
    assert!(num(xn, "p_holm") < 0.05);
    // описательные H: без Холма
    let xd = nt
        .iter()
        .find(|r| r["fam"] == "0" && r["sign"] == "1" && r["hi"] == "0")
        .unwrap();
    assert_eq!((xd["main"].as_str(), xd["p_holm"].as_str()), ("0", ""));
    // Y: net отрицательный
    let yn = nt
        .iter()
        .find(|r| r["fam"] == "1" && r["sign"] == "1" && r["hi"] == "1")
        .unwrap();
    assert!(approx(num(yn, "net_main"), -50.0 - fee));

    // итог §7.6: X проходит все условия в обоих знаках, Y и Z — нет, с названием, что не сошлось
    let early = table(&out.join("early-test.csv"));
    assert_eq!(early.len(), 6);
    for r in &early {
        match r["fam"].as_str() {
            "0" => assert_eq!(r["early"], "можно"),
            _ => {
                assert_eq!(r["early"], "не доказано");
                assert!(r["fail"].contains("lift не подтверждён"), "{}", r["fail"]);
            }
        }
    }
    let rep = fs::read_to_string(out.join("report-tables.md")).unwrap();
    assert_eq!(rep.matches("встать рано можно —").count(), 2, "{rep}");
    assert!(rep.contains("вне периода теста отброшено: 1"));
    assert!(
        rep.contains("вне замороженных правил: 1 (не использованы)"),
        "{rep}"
    );
    assert!(rep.contains("не считалась"));
    assert!(rep.contains(
        "202601, 202602, 202603, 202604, 202605, 202606, 202607, 202608, 202609, 202610"
    ));
}

#[test]
fn test_stage_reports_not_proven_with_reason() {
    let d = tempfile::tempdir().unwrap();
    let inp = inputs(d.path(), false);
    let params = params_file(d.path(), |_| {});
    let k = k_file(d.path(), &[(1, 0, 0), (2, 0, 0)]);
    let out = d.path().join("out");
    run_verdict(Stage::Test, &inp, &params, &out, Some(&k), 1).unwrap();
    let rep = fs::read_to_string(out.join("report-tables.md")).unwrap();
    assert_eq!(rep.matches(": не доказано").count(), 2, "{rep}");
    assert!(rep.contains("lift не подтверждён (§5)"));
    // правило X вне K: его 360 строк + строка правила 5 не используются
    assert!(rep.contains("вне замороженных правил: 361 (не использованы)"));
    // правила X в K нет: его строки не используются
    assert!(!rep.contains("встать рано можно —"));
    let vt = table(&out.join("verdict-test.csv"));
    assert_eq!(vt.len(), 12);
}

#[test]
fn test_stage_is_byte_identical_for_one_and_four_threads() {
    let d = tempfile::tempdir().unwrap();
    let inp = inputs(d.path(), true);
    let params = params_file(d.path(), |_| {});
    let k = k_file(d.path(), &[(0, 0, 0), (1, 0, 0), (2, 0, 0)]);
    let (o1, o4) = (d.path().join("o1"), d.path().join("o4"));
    run_verdict(Stage::Test, &inp, &params, &o1, Some(&k), 1).unwrap();
    run_verdict(Stage::Test, &inp, &params, &o4, Some(&k), 4).unwrap();
    for f in [
        "verdict-test.csv",
        "net-test.csv",
        "early-test.csv",
        "recall-test.csv",
        "first-diff-test.csv",
        "progress-summary-test.csv",
        "report-tables.md",
    ] {
        assert_eq!(
            fs::read(o1.join(f)).unwrap(),
            fs::read(o4.join(f)).unwrap(),
            "{f}"
        );
    }
}

#[test]
fn test_stage_recall_first_and_progress_tables() {
    let d = tempfile::tempdir().unwrap();
    let inp = inputs(d.path(), true);
    let params = params_file(d.path(), |_| {});
    let k = k_file(d.path(), &[(0, 0, 0), (1, 0, 0), (2, 0, 0)]);
    let out = d.path().join("out");
    run_verdict(Stage::Test, &inp, &params, &out, Some(&k), 1).unwrap();

    // полнота: суммы по месяцам, правило вне K (fam 7) отсутствует
    let rc = table(&out.join("recall-test.csv"));
    assert!(rc.iter().all(|r| r["fam"] != "7"));
    let ep = rc
        .iter()
        .find(|r| r["kind"] == "ep" && r["stratum"] == "all" && r["fam"] == "0")
        .unwrap();
    assert_eq!(
        (
            ep["n_ep"].as_str(),
            ep["n_fired"].as_str(),
            ep["n_lt0"].as_str()
        ),
        ("20", "14", "3")
    );
    assert!(approx(num(ep, "recall0"), 0.3));
    assert!(approx(num(ep, "recall30"), 0.45));
    assert!(approx(num(ep, "recall60"), 0.55));
    assert!(approx(num(ep, "share_lt0_of_ep"), 0.15));
    assert!((num(ep, "share_lt0_of_fired") - 3.0 / 14.0).abs() < 1e-6);
    let c2b = rc
        .iter()
        .find(|r| r["kind"] == "c2b" && r["stratum"] == "all" && r["fam"] == "0")
        .unwrap();
    assert!(approx(num(c2b, "recall0"), 0.1));
    assert_eq!(
        rc.iter()
            .filter(|r| r["kind"] == "ep" && r["stratum"] == "market")
            .count(),
        1
    );

    // какой признак первым: разность долей эпизодов и C2b
    let fd = table(&out.join("first-diff-test.csv"));
    let f0 = fd.iter().find(|r| r["fam"] == "0").unwrap();
    assert!(approx(num(f0, "share_ep"), 0.5) && approx(num(f0, "share_c2b"), 0.2));
    assert!(approx(num(f0, "diff"), 0.3));
    let f1 = fd.iter().find(|r| r["fam"] == "1").unwrap();
    assert!(approx(num(f1, "diff"), 0.0));

    // сколько хода: взвешенное по срабатываниям среднее помесячных медиан
    let pg = table(&out.join("progress-summary-test.csv"));
    assert_eq!(pg.len(), 1);
    assert_eq!(
        (pg[0]["n_fired"].as_str(), pg[0]["months"].as_str()),
        ("40", "2")
    );
    assert!(approx(num(&pg[0], "done_bps_p50_wmean"), 175.0));
    assert!(approx(num(&pg[0], "left_share_p50_wmean"), 0.3125));
    assert!(approx(num(&pg[0], "to_peak_bps_p50_wmean"), 450.0));
}

#[test]
fn test_stage_input_errors() {
    let d = tempfile::tempdir().unwrap();
    let inp = inputs(d.path(), false);
    let params = params_file(d.path(), |_| {});
    let out = d.path().join("out");
    // нет --k
    assert!(run_verdict(Stage::Test, &inp, &params, &out, None, 1).is_err());
    // K длиннее verdict.K
    let many: Vec<(u8, u8, u8)> = (0..11).map(|f| (f, 0, 0)).collect();
    let k = k_file(d.path(), &many);
    assert!(run_verdict(Stage::Test, &inp, &params, &out, Some(&k), 1).is_err());
    // правило вне параметров (семейство 11)
    let k = k_file(d.path(), &[(11, 0, 0)]);
    assert!(run_verdict(Stage::Test, &inp, &params, &out, Some(&k), 1).is_err());
    // месяц теста раньше начала периода теста
    let mut bad = gen_test();
    bad.push_str(&dc(
        (0, 0, 0, 1, 1),
        30200,
        202512,
        [10, 3, 100, 10, 10],
        1000.0,
        0.0,
    ));
    fs::write(inp.join("daycells-test.csv"), bad).unwrap();
    let k = k_file(d.path(), &[(0, 0, 0)]);
    assert!(run_verdict(Stage::Test, &inp, &params, &out, Some(&k), 1).is_err());
    // повтор строки (ячейка, сутки)
    let mut dup = gen_test();
    dup.push_str(&dc(
        (0, 0, 0, 1, 1),
        30000,
        202601,
        [10, 3, 100, 10, 10],
        1000.0,
        0.0,
    ));
    fs::write(inp.join("daycells-test.csv"), dup).unwrap();
    assert!(run_verdict(Stage::Test, &inp, &params, &out, Some(&k), 1).is_err());
    // неверный заголовок
    fs::write(inp.join("daycells-test.csv"), "a,b\n1,2\n").unwrap();
    assert!(run_verdict(Stage::Test, &inp, &params, &out, Some(&k), 1).is_err());
}

/// `gen_test` без строк позже месяца `last` (`ггггмм`): в файле только первые месяцы периода теста.
fn gen_test_until(last: i32) -> String {
    let mut out = String::from(DC_HEAD);
    for l in gen_test().lines().skip(1) {
        let month: i32 = l.split(',').nth(6).unwrap().parse().unwrap();
        if month <= last {
            out.push_str(l);
            out.push('\n');
        }
    }
    out
}

/// Знаменатель доли месяцев (2/3, §5 и §7.6) — все 10 месяцев `split.test` (2026-01…2026-10), а не присутствующие в
/// `daycells-test.csv`: месяц без данных — «lift не > 1» и «net не > 0». Данные первых 6 месяцев с идеальным lift в
/// каждом — 6 из 10 < 2/3, не подтверждено; 7 из 10 — подтверждено (граница включительно).
#[test]
fn month_share_denominator_is_all_test_period_months() {
    for (last, present, ok) in [(202606, 6, false), (202607, 7, true)] {
        let d = tempfile::tempdir().unwrap();
        let inp = inputs(d.path(), false);
        fs::write(inp.join("daycells-test.csv"), gen_test_until(last)).unwrap();
        let params = params_file(d.path(), |_| {});
        let k = k_file(d.path(), &[(0, 0, 0)]);
        let out = d.path().join("out");
        run_verdict(Stage::Test, &inp, &params, &out, Some(&k), 1).unwrap();

        let vt = table(&out.join("verdict-test.csv"));
        assert_eq!(vt.len(), 6);
        for r in &vt {
            assert_eq!(r["months_total"], "10", "знаменатель — период, не файл");
            assert_eq!(r["months_gt1"], present.to_string());
            assert_eq!(r["months_defined"], present.to_string());
            assert_eq!(
                r["verdict"],
                if ok {
                    "подтверждено"
                } else {
                    "не различимо"
                },
                "{present} месяцев из 10"
            );
        }
        let nt = table(&out.join("net-test.csv"));
        assert!(nt.iter().all(|r| r["months_total"] == "10"));
        assert!(nt.iter().all(|r| r["months_pos"] == present.to_string()));
        let early = table(&out.join("early-test.csv"));
        assert_eq!(early.len(), 2);
        for r in &early {
            assert_eq!(r["net_months_ok"], if ok { "1" } else { "0" });
            assert_eq!(
                r["early"],
                if ok {
                    "можно"
                } else {
                    "не доказано"
                }
            );
            assert_eq!(
                r["fail"].contains("net > 0 менее чем в 2/3 месяцев"),
                !ok,
                "{}",
                r["fail"]
            );
        }
        let rep = fs::read_to_string(out.join("report-tables.md")).unwrap();
        assert!(rep.contains("месяцев теста 10 ("), "{rep}");
        assert!(
            rep.contains(&format!("с данными в `daycells-test` {present}")),
            "{rep}"
        );
        assert!(rep.contains(&format!("{present}/10")), "{rep}");
    }
}

#[test]
fn period_months_cover_the_whole_period() {
    let d = tempfile::tempdir().unwrap();
    let params = params_file(d.path(), |v| {
        v["split"]["test"] = serde_json::json!(["2025-11", "2026-02"]);
    });
    let inp = d.path().join("in");
    fs::create_dir_all(&inp).unwrap();
    // единственный месяц с данными — 2026-01; период 2025-11…2026-02 (переход через год) = 4 месяца
    let mut s = String::from(DC_HEAD);
    for day in 0..4i64 {
        for sign in [-1i8, 1] {
            for hi in 0..3u8 {
                s.push_str(&dc(
                    (0, 0, 0, sign, hi),
                    30000 + day,
                    202601,
                    [10, 3, 100, 10, 10],
                    1000.0,
                    0.0,
                ));
            }
        }
    }
    fs::write(inp.join("daycells-test.csv"), s).unwrap();
    fs::write(inp.join("daycells-fit.csv"), gen_fit(&fit_rules())).unwrap();
    fs::write(inp.join("lags-test.csv"), LAGS_HEAD).unwrap();
    fs::write(inp.join("first-test.csv"), FIRST_HEAD).unwrap();
    let k = k_file(d.path(), &[(0, 0, 0)]);
    let out = d.path().join("out");
    run_verdict(Stage::Test, &inp, &params, &out, Some(&k), 1).unwrap();
    let vt = table(&out.join("verdict-test.csv"));
    let cell = vt
        .iter()
        .find(|r| r["sign"] == "1" && r["hi"] == "1")
        .unwrap();
    assert_eq!(
        (cell["months_gt1"].as_str(), cell["months_total"].as_str()),
        ("1", "4")
    );
    let rep = fs::read_to_string(out.join("report-tables.md")).unwrap();
    assert!(rep.contains("202511, 202512, 202601, 202602"), "{rep}");
}

/// «Три крупнейших по разности» (отчёт, §4): строка с неопределённой долей (нет эпизодов или C2b) — `NaN` — не
/// должна вставать первой при сортировке по убыванию (`total_cmp` ставит `NaN` выше всех), её нет в таблице.
#[test]
fn first_table_drops_nan_differences_before_sorting() {
    let d = tempfile::tempdir().unwrap();
    let inp = inputs(d.path(), false);
    // семейство 2 (F4) есть только у эпизодов: доля C2b неопределена
    let first = gen_first() + "ep,all,0,0,1,0,2,20,19\n";
    fs::write(inp.join("first-test.csv"), first).unwrap();
    let params = params_file(d.path(), |_| {});
    let k = k_file(d.path(), &[(0, 0, 0), (1, 0, 0), (2, 0, 0)]);
    let out = d.path().join("out");
    run_verdict(Stage::Test, &inp, &params, &out, Some(&k), 1).unwrap();
    let rep = fs::read_to_string(out.join("report-tables.md")).unwrap();
    let from = rep.find("## 4.").unwrap();
    let section = &rep[from..];
    let section = &section[..section.find("\n## ").map_or(section.len(), |p| p + 1)];
    let rows: Vec<&str> = section
        .lines()
        .filter(|l| l.starts_with("| w5 θ2 |"))
        .collect();
    assert_eq!(rows.len(), 2, "{section}");
    assert!(
        rows[0].contains("| F1 |") && rows[0].contains("0.300"),
        "{section}"
    );
    assert!(rows[1].contains("| F3 |"), "{section}");
    assert!(
        !section.contains("| F4 |") && !section.contains('—'),
        "{section}"
    );
}
