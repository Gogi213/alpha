//! Тесты допила `statarb run` (TK-493): фандинг в pair / cross с двумя папками, `--delay-x`, `--rule funding`,
//! столбец `fill_x`, `--mtm-open` и `--cells-out`, `--from-day/--to-day`, неизменность вывода без новых флагов.
//!
//! Замкнутые формулы считаются руками на ряде из одной сделки: X плоская (100), Y = 100 до минуты 5 и 98 с минуты 6,
//! open минуты = close прежней; окно 4, β = 1: сигнал входа в минуте 6 (z = −√3), исполнение Y в минуте 7 по 98, дальше
//! z = −1 и −0,58 (выхода по z нет), с минуты 9 ряд плоский и z не определён — выход только по сроку `max_hold`.

use super::cli::parse_funding;
use super::kernel::{run_pair, BetaMode, LegKind, OpenPos, PairInput, Params, Rule, Sink, Trade};
use super::mtm::{last_valid, value, OpenSnap};
use super::multi::run_pair_multi;
use super::panel::{read_series, write_series, Series, GRID0_MS, MINUTE_MS};
use super::run::{run, RunArgs};
use super::tests::{lns, ou_on, params, run_panels_dir, walk, with_gaps, Lcg, N};
use crate::lob::costs::leg_fee_bps;
use clap::Parser;
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// TK-493 волна 5: `--no-entry-before-from`, `--job-ext-out`, проверки входных файлов.
mod w5;

#[derive(Parser)]
struct W {
    #[command(flatten)]
    a: RunArgs,
}

fn go(args: &[String]) -> anyhow::Result<()> {
    let mut v = vec!["run".to_string()];
    v.extend(args.iter().cloned());
    let w = W::try_parse_from(v).map_err(|e| anyhow::anyhow!("разбор флагов: {e}"))?;
    run(&w.a)
}

fn args(list: &[&str]) -> Vec<String> {
    list.iter().map(|x| (*x).to_string()).collect()
}

fn kv(v: &mut Vec<String>, k: &str, val: &Path) {
    v.push(k.to_string());
    v.push(val.to_str().unwrap().to_string());
}

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() <= 1e-9 * a.abs().max(b.abs()).max(1.0)
}

/// Файл ставок как у биржи: `time_ms,rate,interval_h`, время выплаты в мс unix с дрожанием в мс (минута сетки — `m`).
fn fund_text(ev: &[(i64, f64)]) -> String {
    let mut t = String::from("time_ms,rate,interval_h\n");
    for (m, r) in ev {
        t += &format!("{},{r},8\n", GRID0_MS + m * MINUTE_MS + 3);
    }
    t
}

fn put_fund(dir: &Path, name: &str, ev: &[(i64, f64)]) {
    std::fs::create_dir_all(dir).unwrap();
    let out = File::create(dir.join(format!("{name}.csv.gz"))).unwrap();
    let mut ch = Command::new("gzip")
        .arg("-c")
        .stdin(Stdio::piped())
        .stdout(out)
        .spawn()
        .unwrap();
    ch.stdin
        .take()
        .unwrap()
        .write_all(fund_text(ev).as_bytes())
        .unwrap();
    assert!(ch.wait().unwrap().success());
}

fn put_panel(dir: &Path, name: &str, s: &Series) {
    std::fs::create_dir_all(dir).unwrap();
    let mut f = File::create(dir.join(format!("{name}.sap"))).unwrap();
    write_series(&mut f, s).unwrap();
}

/// Ряд по закрытиям: open минуты = close прежней, `NaN` в close — свечи нет (и open тоже).
fn series_from(cl: &[f64]) -> Series {
    let open = (0..cl.len())
        .map(|t| {
            if cl[t].is_finite() {
                cl[t.saturating_sub(1)]
            } else {
                f64::NAN
            }
        })
        .collect();
    Series {
        first_min: 0,
        open,
        close: cl.to_vec(),
    }
}

fn flat(len: usize) -> Series {
    series_from(&vec![100.0; len])
}

/// Y: 100 до минуты 5, дальше 98.
fn y_closes(len: usize) -> Vec<f64> {
    (0..len).map(|t| if t < 6 { 100.0 } else { 98.0 }).collect()
}

struct Files {
    jobs: PathBuf,
    daily: PathBuf,
    trades: PathBuf,
    cells_out: PathBuf,
}

fn files(root: &Path, tag: &str, jobs: &str) -> Files {
    let f = Files {
        jobs: root.join(format!("jobs-{tag}.csv")),
        daily: root.join(format!("daily-{tag}.csv")),
        trades: root.join(format!("trades-{tag}.csv")),
        cells_out: root.join(format!("cells-{tag}.csv")),
    };
    std::fs::write(&f.jobs, jobs).unwrap();
    f
}

/// Флаги, общие для всех прогонов: задания, выходы, поток; задание 0 пишет сделки.
fn common(f: &Files) -> Vec<String> {
    let mut v = args(&["--trade-jobs", "0", "--min-span", "10", "--threads", "1"]);
    kv(&mut v, "--jobs", &f.jobs);
    kv(&mut v, "--daily-out", &f.daily);
    kv(&mut v, "--trades-out", &f.trades);
    v
}

/// Клетка ряда из одной сделки (окно 4, β = 1, вход 1,5, выход 0,5, срок `max_hold`).
fn hc_cell(label: &str, max_hold: &str) -> Vec<String> {
    args(&[
        "--cell",
        label,
        "--window",
        "4",
        "--min-obs",
        "4",
        "--beta-fixed",
        "1.0",
        "--entry-z",
        "1.5",
        "--exit-z",
        "0.5",
        "--stop-z",
        "inf",
        "--max-hold",
        max_hold,
        "--refresh",
        "0",
    ])
}

fn table(path: &Path) -> Vec<HashMap<String, String>> {
    let txt = std::fs::read_to_string(path).unwrap();
    let mut it = txt.lines();
    let head: Vec<&str> = it.next().unwrap().split(',').collect();
    it.map(|l| {
        head.iter()
            .zip(l.split(','))
            .map(|(h, v)| ((*h).to_string(), v.to_string()))
            .collect()
    })
    .collect()
}

fn num(r: &HashMap<String, String>, k: &str) -> f64 {
    r[k].parse().unwrap()
}

fn header(path: &Path) -> String {
    std::fs::read_to_string(path)
        .unwrap()
        .lines()
        .next()
        .unwrap()
        .to_string()
}

/// Строки файла без заголовка.
fn body(path: &Path) -> Vec<String> {
    std::fs::read_to_string(path)
        .unwrap()
        .lines()
        .skip(1)
        .map(str::to_string)
        .collect()
}

/// Выплаты ноги: (минута сетки, ставка).
type Events = Vec<(i64, f64)>;

/// Ставки ноги Y и ноги X ряда из одной сделки. Позиция Y держится с минуты 7 (исполнение) до 18 (исполнение выхода):
/// выплаты в минутах 5 (до входа), 7 (минута исполнения) и 18 (минута выхода) не начисляются, 10 и 14 — начисляются.
fn hc_events() -> (Events, Events) {
    (
        vec![(5, 9e-4), (7, 7e-4), (10, 1e-4), (14, 2e-4), (18, 5e-4)],
        vec![(5, 3e-4), (7, 3e-4), (10, 5e-5), (14, 5e-5), (18, 3e-4)],
    )
}

/// Сделка ряда из одной сделки со сроком 10: вход 6/7, выход 17/18, валовый ноль, комиссия 4 стороны × 3,15.
fn check_hc(r: &HashMap<String, String>, funding_bps: f64) {
    assert_eq!(
        (
            num(r, "dir"),
            num(r, "sig_in"),
            num(r, "fill_in"),
            num(r, "sig_out"),
            num(r, "fill_out")
        ),
        (1.0, 6.0, 7.0, 17.0, 18.0)
    );
    let fee = 4.0 * leg_fee_bps(true);
    assert!(close(num(r, "gross_bps"), 0.0), "{r:?}");
    assert!(close(num(r, "fee_bps"), fee), "{r:?}");
    assert!(close(num(r, "funding_bps"), funding_bps), "{r:?}");
    assert!(close(num(r, "net_bps"), -fee + funding_bps), "{r:?}");
}

const OLD_TRADES_HEADER: &str =
    "cell,job,y,x,dir,sig_in,fill_in,sig_out,fill_out,gross_bps,fee_bps,funding_bps,net_bps";

#[test]
fn funding_is_wired_for_pair_one_dir_and_cross_two_dirs() {
    let d = tempfile::tempdir().unwrap();
    let root = d.path();
    let (ey, ex) = hc_events();
    // pair: одна биржа, ставки обеих ног из одного каталога --funding-y
    put_panel(&root.join("p"), "YYY", &series_from(&y_closes(60)));
    put_panel(&root.join("p"), "XXX", &flat(60));
    put_fund(&root.join("f"), "YYY", &ey);
    put_fund(&root.join("f"), "XXX", &ex);
    let pair = |tag: &str, with_funding: bool| {
        let f = files(root, tag, "y,x,from_day,to_day\nYYY,XXX,0,1\n");
        let mut a = args(&["--kind", "pair"]);
        kv(&mut a, "--panels", &root.join("p"));
        if with_funding {
            kv(&mut a, "--funding-y", &root.join("f"));
        }
        a.extend(common(&f));
        a.extend(hc_cell("T", "10"));
        go(&a).unwrap();
        f
    };
    let f = pair("pair", true);
    assert_eq!(
        header(&f.trades),
        OLD_TRADES_HEADER,
        "без --delay-x заголовок прежний"
    );
    let t = table(&f.trades);
    assert_eq!(t.len(), 1);
    // Y: 10 и 14 → −(1e-4 + 2e-4); X (шорт): +(5e-5 + 5e-5); итого −2 bps
    check_hc(&t[0], -2.0);
    // контроль: без каталога ставок фандинг ноль
    let t0 = table(&pair("pair0", false).trades);
    check_hc(&t0[0], 0.0);

    // cross: две биржи, символы названы по-разному, ставки каждой ноги — из своего каталога
    put_panel(&root.join("a"), "YYYUSDT", &series_from(&y_closes(60)));
    put_panel(&root.join("b"), "XBTUSDTM", &flat(60));
    put_fund(&root.join("fa"), "YYYUSDT", &ey);
    put_fund(&root.join("fb"), "XBTUSDTM", &ex);
    let fc = files(root, "cross", "y,x,from_day,to_day\nYYYUSDT,XBTUSDTM,0,1\n");
    let mut a = args(&["--kind", "cross"]);
    kv(&mut a, "--panels", &root.join("a"));
    kv(&mut a, "--panels-b", &root.join("b"));
    kv(&mut a, "--funding-y", &root.join("fa"));
    kv(&mut a, "--funding-x", &root.join("fb"));
    a.extend(common(&fc));
    a.extend(hc_cell("T", "10"));
    go(&a).unwrap();
    let tc = table(&fc.trades);
    assert_eq!(tc.len(), 1);
    check_hc(&tc[0], -2.0);
    assert_eq!(tc[0]["y"], "YYYUSDT");
    assert_eq!(tc[0]["x"], "XBTUSDTM");
}

#[test]
fn delay_x_adds_fill_x_and_cells_run_as_single_passes() {
    let d = tempfile::tempdir().unwrap();
    let root = d.path();
    put_panel(&root.join("p"), "YYY", &series_from(&y_closes(60)));
    put_panel(&root.join("p"), "XXX", &flat(60));
    // Y держится с 7: выплаты 10, 14, 18 начисляются, 5 / 7 / 21 — нет (до входа, минута входа, минута выхода).
    // X входит в минуте 10 (задержка 3): выплата 10 не начисляется (минута исполнения), 14 и 18 — да.
    put_fund(
        &root.join("f"),
        "YYY",
        &[
            (5, 9e-4),
            (7, 7e-4),
            (10, 1e-4),
            (14, 2e-4),
            (18, 3e-4),
            (21, 9e-4),
        ],
    );
    put_fund(
        &root.join("f"),
        "XXX",
        &[
            (5, 3e-4),
            (7, 3e-4),
            (10, 5e-5),
            (14, 5e-5),
            (18, 5e-5),
            (21, 9e-4),
        ],
    );
    let base = |f: &Files| {
        let mut a = args(&["--kind", "pair", "--delay-x", "3"]);
        kv(&mut a, "--panels", &root.join("p"));
        kv(&mut a, "--funding-y", &root.join("f"));
        a.extend(common(f));
        a
    };
    let f = files(root, "d1", "y,x,from_day,to_day\nYYY,XXX,0,1\n");
    let mut a = base(&f);
    a.extend(hc_cell("T", "10"));
    go(&a).unwrap();
    assert_eq!(
        header(&f.trades),
        "cell,job,y,x,dir,sig_in,fill_in,fill_x,sig_out,fill_out,gross_bps,fee_bps,funding_bps,net_bps"
    );
    let t = table(&f.trades);
    assert_eq!(t.len(), 1);
    let r = &t[0];
    // позиция набрана с входа X (минута 10): срок 10 → сигнал 20, исполнение 21
    assert_eq!(
        (
            num(r, "sig_in"),
            num(r, "fill_in"),
            num(r, "fill_x"),
            num(r, "sig_out"),
            num(r, "fill_out")
        ),
        (6.0, 7.0, 10.0, 20.0, 21.0)
    );
    let fee = 4.0 * leg_fee_bps(true);
    assert!(close(num(r, "funding_bps"), 1e4 * (-6e-4 + 1e-4)), "{r:?}");
    assert!(close(num(r, "net_bps"), -fee - 5.0), "{r:?}");

    // --cells при --delay-x: каждая клетка совпадает с отдельным прогоном (multi.rs такие клетки отклоняет)
    std::fs::write(
        root.join("cells.csv"),
        "cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh\n\
         c0,4,4,1.0,1.5,0.5,inf,10,0\nc1,4,4,1.0,1.5,0.5,inf,12,0\n",
    )
    .unwrap();
    let fm = files(root, "dm", "y,x,from_day,to_day\nYYY,XXX,0,1\n");
    let mut a = base(&fm);
    kv(&mut a, "--cells", &root.join("cells.csv"));
    go(&a).unwrap();
    let (mut want_t, mut want_d) = (Vec::new(), Vec::new());
    for (i, (label, hold)) in [("c0", "10"), ("c1", "12")].into_iter().enumerate() {
        let fs = files(
            root,
            &format!("ds{i}"),
            "y,x,from_day,to_day\nYYY,XXX,0,1\n",
        );
        let mut a = base(&fs);
        a.extend(hc_cell(label, hold));
        go(&a).unwrap();
        want_t.extend(body(&fs.trades));
        want_d.extend(body(&fs.daily));
    }
    assert_eq!(want_t.len(), 2, "тест пуст: сделок {}", want_t.len());
    assert_eq!(body(&fm.trades), want_t);
    assert_eq!(body(&fm.daily), want_d);
}

/// Ряд с открытой на конце позицией: Y падает до 97 в минуте 40 и пропадает с 41 (контракт замолчал), X плоская.
fn open_case(root: &Path) {
    let mut cy = y_closes(60);
    cy[40] = 97.0;
    for v in &mut cy[41..] {
        *v = f64::NAN;
    }
    put_panel(&root.join("p"), "YYY", &series_from(&cy));
    put_panel(&root.join("p"), "XXX", &flat(60));
    // выплаты Y: 10 (держим) и 45 (в дыре Y: цена для выплаты — последний open); файла X нет — нога без фандинга
    put_fund(&root.join("f"), "YYY", &[(10, 1e-4), (45, 2e-4)]);
}

fn open_run(root: &Path, tag: &str, mtm: bool) -> Files {
    let f = files(root, tag, "y,x,from_day,to_day\nYYY,XXX,0,1\n");
    let mut a = args(&["--kind", "pair"]);
    kv(&mut a, "--panels", &root.join("p"));
    kv(&mut a, "--funding-y", &root.join("f"));
    a.extend(common(&f));
    a.extend(hc_cell("T", "0"));
    kv(&mut a, "--cells-out", &f.cells_out);
    if mtm {
        a.push("--mtm-open".to_string());
        kv(&mut a, "--open-out", &root.join(format!("open-{tag}.csv")));
    }
    go(&a).unwrap();
    f
}

#[test]
fn open_position_is_marked_to_last_valid_close() {
    let d = tempfile::tempdir().unwrap();
    let root = d.path();
    open_case(root);
    let f = open_run(root, "m1", true);
    let rows = table(&f.cells_out);
    let c = &rows[0];
    assert_eq!(
        (num(c, "trades"), num(c, "entries"), num(c, "open_at_end")),
        (0.0, 1.0, 1.0)
    );
    assert_eq!((num(c, "open_pre_window"), num(c, "mtm_n")), (0.0, 1.0));
    // Y: лонг 1/98 от 98 до последнего close 97 (минута 40); X: шорт 1/100 от 100, close 100 до конца окна (минута 59)
    let gross = 1e4 * (97.0 - 98.0) / 98.0;
    let fee = leg_fee_bps(true) * (3.0 + 97.0 / 98.0); // Y: (1/98)(98 + 97)·f; X: 2·f
    let funding = -3.0; // −(1e-4 + 2e-4): обе выплаты Y при позиции, вторая — в дыре
    assert!(close(num(c, "mtm_gross_bps"), gross), "{c:?}");
    assert!(close(num(c, "mtm_fee_bps"), fee), "{c:?}");
    assert!(close(num(c, "mtm_funding_bps"), funding), "{c:?}");
    assert!(close(num(c, "mtm_net_bps"), gross - fee + funding), "{c:?}");
    assert_eq!(num(c, "mtm_stale_max_min"), 19.0); // конец окна 59 − последний close Y в 40
                                                   // --open-out: та же позиция построчно — вход и оценка (для ручной сверки)
    let open = table(&root.join("open-m1.csv"));
    assert_eq!(open.len(), 1);
    let o = &open[0];
    assert_eq!(
        (
            num(o, "job"),
            num(o, "dir"),
            num(o, "sig_in"),
            num(o, "fill_in")
        ),
        (0.0, 1.0, 6.0, 7.0)
    );
    assert_eq!((o["y"].as_str(), o["x"].as_str()), ("YYY", "XXX"));
    assert_eq!(num(o, "fill_x"), 7.0);
    assert!(close(num(o, "net_bps"), num(c, "mtm_net_bps")), "{o:?}");
    assert_eq!(num(o, "stale_min"), 19.0);
    // оценка не попадает ни в сутки, ни в сделки
    assert_eq!(body(&f.daily).len(), 0);
    assert_eq!(body(&f.trades).len(), 0);

    // без --mtm-open: те же файлы сделок, счётчики те же, оценки нет (nan, не ноль)
    let g = open_run(root, "m0", false);
    assert_eq!(body(&g.daily), body(&f.daily));
    assert_eq!(body(&g.trades), body(&f.trades));
    let rows0 = table(&g.cells_out);
    let c0 = &rows0[0];
    for k in [
        "mtm_n",
        "mtm_net_bps",
        "mtm_gross_bps",
        "mtm_fee_bps",
        "mtm_funding_bps",
        "mtm_stale_max_min",
    ] {
        assert_eq!(c0[k], "nan", "{k}");
    }
    for k in [
        "trades",
        "entries",
        "open_at_end",
        "gap_minutes",
        "minutes_in_pos",
    ] {
        assert_eq!(c0[k], c[k], "{k}");
    }
}

#[test]
fn open_position_with_pending_x_leg_values_only_y() {
    // Ряд оборван на минуте 10; Y входит в 7, задержка 5 → нога X ждёт минуты 12 и не успевает: открыта одна нога Y.
    let d = tempfile::tempdir().unwrap();
    let root = d.path();
    put_panel(&root.join("p"), "YYY", &series_from(&y_closes(11)));
    put_panel(&root.join("p"), "XXX", &flat(11));
    put_fund(&root.join("f"), "YYY", &[(9, 3e-4)]);
    put_fund(&root.join("f"), "XXX", &[(9, 5e-4)]); // нога X не вошла: её выплата не начисляется
    let f = files(root, "pend", "y,x,from_day,to_day\nYYY,XXX,0,1\n");
    let mut a = args(&["--kind", "pair", "--delay-x", "5", "--mtm-open"]);
    kv(&mut a, "--open-out", &root.join("open-pend.csv"));
    kv(&mut a, "--panels", &root.join("p"));
    kv(&mut a, "--funding-y", &root.join("f"));
    a.extend(common(&f));
    a.extend(hc_cell("T", "0"));
    kv(&mut a, "--cells-out", &f.cells_out);
    go(&a).unwrap();
    let rows = table(&f.cells_out);
    let c = &rows[0];
    assert_eq!(
        (
            num(c, "entries"),
            num(c, "open_at_end"),
            num(c, "x_deferred")
        ),
        (1.0, 1.0, 0.0)
    );
    // Y: 98 → close 98 (минута 10), валовый ноль; комиссия двух сторон одной ноги; фандинг −3
    let fee = 2.0 * leg_fee_bps(true);
    assert!(close(num(c, "mtm_gross_bps"), 0.0), "{c:?}");
    assert!(close(num(c, "mtm_fee_bps"), fee), "{c:?}");
    assert!(close(num(c, "mtm_funding_bps"), -3.0), "{c:?}");
    assert!(close(num(c, "mtm_net_bps"), -fee - 3.0), "{c:?}");
    assert_eq!(num(c, "mtm_stale_max_min"), 0.0);
    // нога X не вошла: в --open-out fill_x пуст
    let open = table(&root.join("open-pend.csv"));
    assert_eq!(open.len(), 1);
    assert_eq!(
        (open[0]["fill_in"].as_str(), open[0]["fill_x"].as_str()),
        ("7", "")
    );
}

/// Выплаты ноги: раз в час `amp·sin(j/4)` — знак и величина меняются, вход и выход по правилу funding идут по уровню.
fn swinging(n: usize, amp: f64) -> Vec<(i64, f64)> {
    (0..n as i64 / 60)
        .map(|j| (j * 60, amp * (j as f64 / 4.0).sin()))
        .collect()
}

/// Сделки, посчитанные ядром напрямую по тем же панелям и ставкам (минуты ставок — через `parse_funding`).
fn direct(dir: &Path, fa: &[(i64, f64)], fb: &[(i64, f64)], p: &Params) -> Vec<Trade> {
    let rd = |n: &str| {
        let f = File::open(dir.join(format!("{n}.sap"))).unwrap();
        read_series(&mut BufReader::new(f)).unwrap()
    };
    let (sy, sx) = (rd("AAA"), rd("BBB"));
    assert_eq!((sy.first_min, sy.len()), (sx.first_min, sx.len()));
    let (ly, lx) = (sy.ln_close(), sx.ln_close());
    let fy = parse_funding(&fund_text(fa)).unwrap();
    let fx = parse_funding(&fund_text(fb)).unwrap();
    let fxs: [&[(i64, f64)]; 1] = [&fx];
    let inp = PairInput {
        first_min: sy.first_min,
        ly: &ly,
        lx: &lx,
        oy: &sy.open,
        ox: &[&sx.open],
        wx: &[1.0],
        fund_y: &fy,
        fund_x: &fxs,
    };
    let mut tr: Vec<Trade> = Vec::new();
    run_pair(&inp, p, &mut tr);
    tr
}

#[test]
fn run_equals_direct_kernel_for_delay_and_funding_rule() {
    let d = run_panels_dir();
    let root = d.path();
    let n = 7 * 1440;
    let (fa, fb) = (swinging(n, 6e-4), swinging(n, 2e-4));
    put_fund(&root.join("f"), "AAA", &fa);
    put_fund(&root.join("f"), "BBB", &fb);
    let base = Params {
        window: 240,
        min_obs: 120,
        beta: BetaMode::Fixed(1.0),
        entry_z: 2.0,
        exit_z: 0.2,
        stop_z: f64::INFINITY,
        max_hold: 3000,
        refresh: 0,
        y_kind: LegKind::Perp,
        x_kind: LegKind::Perp,
        taker_in: true,
        taker_out: true,
        delay_x: 0,
        rearm: false,
        entry_from: None,
        rule: Rule::Z,
    };
    let fund_rule = Rule::Funding {
        k: 2,
        hold_payments: 5,
    };
    // (флаги режима, параметры ядра): правило funding по `--funding-k` без `--rule` (как в bench), с `--rule funding`
    // и задержкой, правило z с задержкой и выплатами D2
    let cases: [(&[&str], Params); 3] = [
        (
            &["--funding-k", "2", "--hold-payments", "5"],
            Params {
                rule: fund_rule,
                ..base
            },
        ),
        (
            &[
                "--rule",
                "funding",
                "--funding-k",
                "2",
                "--hold-payments",
                "5",
                "--delay-x",
                "3",
            ],
            Params {
                rule: fund_rule,
                delay_x: 3,
                ..base
            },
        ),
        (
            &["--rule", "z", "--delay-x", "2"],
            Params { delay_x: 2, ..base },
        ),
    ];
    for (i, (flags, p)) in cases.iter().enumerate() {
        let f = files(root, &format!("k{i}"), "y,x,from_day,to_day\nAAA,BBB,0,7\n");
        let mut a = args(&["--kind", "pair"]);
        kv(&mut a, "--panels", root);
        kv(&mut a, "--funding-y", &root.join("f"));
        a.extend(common(&f));
        a.extend(args(&[
            "--cell",
            "K",
            "--window",
            "240",
            "--min-obs",
            "120",
            "--beta-fixed",
            "1.0",
            "--entry-z",
            "2.0",
            "--exit-z",
            "0.2",
            "--stop-z",
            "inf",
            "--max-hold",
            "3000",
            "--refresh",
            "0",
        ]));
        a.extend(args(flags));
        go(&a).unwrap();
        let want = direct(root, &fa, &fb, p);
        let got = table(&f.trades);
        assert!(
            want.len() >= 3,
            "случай {i}: тест пуст, сделок {}",
            want.len()
        );
        assert_eq!(got.len(), want.len(), "случай {i}: число сделок");
        assert!(
            want.iter().any(|t| t.funding_bps != 0.0),
            "случай {i}: фандинг ноль"
        );
        for (r, t) in got.iter().zip(&want) {
            assert_eq!(
                (
                    num(r, "dir"),
                    num(r, "sig_in"),
                    num(r, "fill_in"),
                    num(r, "sig_out"),
                    num(r, "fill_out")
                ),
                (
                    f64::from(t.dir),
                    t.sig_in as f64,
                    t.fill_in as f64,
                    t.sig_out as f64,
                    t.fill_out as f64
                ),
                "случай {i}"
            );
            if p.delay_x > 0 {
                assert_eq!(num(r, "fill_x"), t.fill_x as f64, "случай {i}");
            } else {
                assert!(!r.contains_key("fill_x"));
            }
            assert_eq!(num(r, "funding_bps"), t.funding_bps, "случай {i}");
            assert_eq!(num(r, "net_bps"), t.net_bps, "случай {i}");
        }
    }
}

#[test]
fn new_flags_off_keep_old_bytes_and_cells_out_matches_single_runs() {
    let d = run_panels_dir();
    let root = d.path();
    let jobs =
        "y,x,from_day,to_day\nAAA,BBB,2,3\nAAA,BBB,2,4\nAAA,BBB,2,5\nAAA,BBB,2,6\nBBB,AAA,3,5\n";
    // метка, window, min_obs, beta_fixed, entry_z, exit_z, stop_z, max_hold, refresh
    let cells = [
        ("c0", "240", "120", "", "2.0", "0.2", "inf", "720", "0"),
        ("c1", "240", "120", "", "1.5", "0.5", "4.0", "0", "0"),
        ("c2", "360", "180", "", "2.0", "0.0", "inf", "240", "97"),
        ("c3", "240", "120", "1.0", "2.5", "0.2", "3.5", "480", "0"),
        ("c4", "360", "180", "", "2.5", "0.3", "5.0", "0", "97"),
    ];
    let mut csv =
        String::from("cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh\n");
    for c in &cells {
        csv += &format!(
            "{},{},{},{},{},{},{},{},{}\n",
            c.0, c.1, c.2, c.3, c.4, c.5, c.6, c.7, c.8
        );
    }
    std::fs::write(root.join("cells.csv"), csv).unwrap();
    let single =
        |f: &Files, c: &(&str, &str, &str, &str, &str, &str, &str, &str, &str), more: &[&str]| {
            let mut a = args(&["--kind", "pair"]);
            kv(&mut a, "--panels", root);
            a.extend(common(f));
            a.extend(args(&[
                "--cell",
                c.0,
                "--window",
                c.1,
                "--min-obs",
                c.2,
                "--entry-z",
                c.4,
                "--exit-z",
                c.5,
                "--stop-z",
                c.6,
                "--max-hold",
                c.7,
                "--refresh",
                c.8,
            ]));
            if !c.3.is_empty() {
                a.extend(args(&["--beta-fixed", c.3]));
            }
            a.extend(args(more));
            go(&a).unwrap();
        };
    // 1. явные значения по умолчанию и включённая оценка не меняют ни суточный csv, ни сделки
    let f0 = files(root, "b0", jobs);
    single(&f0, &cells[0], &[]);
    let f1 = files(root, "b1", jobs);
    single(&f1, &cells[0], &["--delay-x", "0", "--rule", "z"]);
    let f2 = files(root, "b2", jobs);
    let mut more = args(&["--mtm-open", "--delay-x", "0", "--rule", "z"]);
    kv(&mut more, "--cells-out", &f2.cells_out);
    let more_ref: Vec<&str> = more.iter().map(String::as_str).collect();
    single(&f2, &cells[0], &more_ref);
    for f in [&f1, &f2] {
        assert_eq!(
            std::fs::read(&f.daily).unwrap(),
            std::fs::read(&f0.daily).unwrap()
        );
        assert_eq!(
            std::fs::read(&f.trades).unwrap(),
            std::fs::read(&f0.trades).unwrap()
        );
    }
    assert_eq!(header(&f0.trades), OLD_TRADES_HEADER);
    assert!(!f0.cells_out.exists(), "--cells-out без флага не пишется");

    // 2. --cells-out многоклеточного прохода = склейка сводок отдельных прогонов (счётчики и оценка открытых позиций)
    let fm = files(root, "bm", jobs);
    let mut a = args(&["--kind", "pair", "--mtm-open"]);
    kv(&mut a, "--panels", root);
    a.extend(common(&fm));
    kv(&mut a, "--cells", &root.join("cells.csv"));
    kv(&mut a, "--cells-out", &fm.cells_out);
    go(&a).unwrap();
    let mut want = Vec::new();
    for (i, c) in cells.iter().enumerate() {
        let fs = files(root, &format!("bs{i}"), jobs);
        let mut more = args(&["--mtm-open"]);
        kv(&mut more, "--cells-out", &fs.cells_out);
        let more_ref: Vec<&str> = more.iter().map(String::as_str).collect();
        single(&fs, c, &more_ref);
        want.extend(body(&fs.cells_out));
    }
    assert_eq!(header(&fm.cells_out), header(&f2.cells_out));
    assert_eq!(body(&fm.cells_out), want);
    let rows = table(&fm.cells_out);
    let open: f64 = rows.iter().map(|r| num(r, "open_at_end")).sum();
    let marked: f64 = rows
        .iter()
        .map(|r| num(r, "mtm_n") + num(r, "open_pre_window"))
        .sum();
    assert!(open > 0.0, "тест пуст: открытых позиций на концах окон нет");
    assert_eq!(
        open, marked,
        "каждая открытая позиция оценена или вошла в разогреве"
    );
    for r in &rows {
        // вход = сделка закрытая + открытая на конце окна
        assert_eq!(
            num(r, "entries"),
            num(r, "trades") + num(r, "open_at_end"),
            "{r:?}"
        );
    }
}

#[test]
fn from_day_to_day_cut_job_windows() {
    let d = run_panels_dir();
    let root = d.path();
    let go_with = |tag: &str, jobs: &str, more: &[&str]| -> Files {
        let f = files(root, tag, jobs);
        let mut a = args(&["--kind", "pair"]);
        kv(&mut a, "--panels", root);
        a.extend(common(&f));
        a.extend(args(&[
            "--cell",
            "T",
            "--window",
            "240",
            "--min-obs",
            "120",
            "--entry-z",
            "2.0",
            "--exit-z",
            "0.2",
            "--stop-z",
            "inf",
            "--max-hold",
            "720",
            "--refresh",
            "0",
        ]));
        a.extend(args(more));
        kv(&mut a, "--cells-out", &f.cells_out);
        go(&a).unwrap();
        f
    };
    let hdr = "y,x,from_day,to_day\n";
    // окно задания обрезается до [2, 6) — как задание с этим окном в файле
    let cut = go_with(
        "c1",
        &format!("{hdr}AAA,BBB,0,7\n"),
        &["--from-day", "2", "--to-day", "6"],
    );
    let own = go_with("c2", &format!("{hdr}AAA,BBB,2,6\n"), &[]);
    assert!(!body(&own.trades).is_empty(), "тест пуст: сделок нет");
    assert_eq!(body(&cut.daily), body(&own.daily));
    assert_eq!(body(&cut.trades), body(&own.trades));
    // обрезка не расширяет окно: задание 3–5 остаётся 3–5
    let narrow = go_with(
        "c3",
        &format!("{hdr}AAA,BBB,3,5\n"),
        &["--from-day", "2", "--to-day", "6"],
    );
    let narrow_own = go_with("c4", &format!("{hdr}AAA,BBB,3,5\n"), &[]);
    assert_eq!(body(&narrow.trades), body(&narrow_own.trades));
    // два сегмента вокруг «дыры» трое суток 3–4: сделки не пересекают границу сегмента
    let seg1 = go_with("c5", &format!("{hdr}AAA,BBB,0,7\n"), &["--to-day", "3"]);
    let seg2 = go_with("c6", &format!("{hdr}AAA,BBB,0,7\n"), &["--from-day", "4"]);
    let (t1, t2) = (table(&seg1.trades), table(&seg2.trades));
    assert!(
        !t1.is_empty() && !t2.is_empty(),
        "тест пуст: {} / {}",
        t1.len(),
        t2.len()
    );
    assert!(t1.iter().all(|r| num(r, "fill_out") < 3.0 * 1440.0));
    assert!(t2.iter().all(|r| num(r, "fill_in") >= 4.0 * 1440.0));
    // пустое окно после обрезки: задание считается «нет спана», номера заданий не сдвигаются
    let empty = go_with(
        "c7",
        &format!("{hdr}AAA,BBB,0,2\nAAA,BBB,2,6\n"),
        &["--from-day", "2", "--trade-jobs", "1"],
    );
    let rows = table(&empty.cells_out);
    let c = &rows[0];
    assert_eq!(
        (num(c, "jobs"), num(c, "done"), num(c, "no_span")),
        (2.0, 1.0, 1.0)
    );
    let tr = table(&empty.trades);
    assert!(!tr.is_empty() && tr.iter().all(|r| num(r, "job") == 1.0));
}

#[test]
fn bad_flag_combinations_are_rejected() {
    let d = tempfile::tempdir().unwrap();
    let root = d.path();
    let f = files(root, "bad", "y,x,from_day,to_day\nA,B,0,1\n");
    let fail = |kind: &str, more: &[&str], want: &str| {
        let mut a = args(&["--kind", kind]);
        kv(&mut a, "--panels", root);
        a.extend(common(&f));
        a.extend(hc_cell("T", "10"));
        a.extend(args(more));
        let e = go(&a).unwrap_err().to_string();
        assert!(e.contains(want), "{kind} {more:?}: {e}");
    };
    let dir = root.to_str().unwrap();
    fail("pair", &["--funding-x", dir], "--funding-x не нужен");
    fail("basket", &["--funding-x", dir], "--funding-x не нужен");
    fail(
        "funding-ps",
        &["--panels-b", dir, "--funding-y", dir, "--funding-x", dir],
        "спот без фандинга",
    );
    fail(
        "cross",
        &["--panels-b", dir, "--funding-y", dir],
        "у обеих ног",
    );
    fail("pair", &["--rule", "funding"], "--funding-k ≥ 1");
    fail("pair", &["--funding-k", "2"], "--hold-payments ≥ 1");
    fail(
        "pair",
        &[
            "--rule",
            "funding",
            "--funding-k",
            "2",
            "--hold-payments",
            "5",
        ],
        "требует --funding-y",
    );
    fail(
        "pair",
        &["--rule", "z", "--funding-k", "2"],
        "относятся к правилу funding",
    );
    fail("pair", &["--mtm-open"], "--cells-out");
    fail("pair", &["--open-out", dir], "--open-out требует");
    fail("pair", &["--from-day", "5", "--to-day", "3"], "--to-day 3");
}

/// Приёмник с позицией на конце данных.
#[derive(Default)]
struct Rec {
    trades: Vec<Trade>,
    open: Option<OpenSnap>,
}

impl Sink for Rec {
    fn trade(&mut self, t: &Trade) {
        self.trades.push(*t);
    }

    fn open_end(&mut self, pos: &OpenPos) {
        self.open = Some(OpenSnap::of(pos));
    }
}

#[test]
fn open_end_hook_is_the_same_in_single_and_multi_passes() {
    let mut r = Lcg(11);
    let x = walk(N, &mut r, 1e-3, 4.0);
    let y = ou_on(&x, 0.8, &mut r, 2e-3);
    let (ox, cx) = with_gaps(&x, &mut r, None);
    let (oy, cy) = with_gaps(&y, &mut r, None);
    let (lx, ly) = (lns(&cx), lns(&cy));
    let base = params(BetaMode::Ols, LegKind::Perp, LegKind::Perp);
    let cells: Vec<Params> = [(1.5, 0.0), (2.0, 0.2), (2.5, 0.5)]
        .into_iter()
        .map(|(entry_z, exit_z)| Params {
            entry_z,
            exit_z,
            stop_z: f64::INFINITY,
            max_hold: 0,
            ..base
        })
        .collect();
    let mut opened = 0;
    // ряд обрезается в разных местах: на части обрезок позиция остаётся открытой
    for cut in (2000..N).step_by(131) {
        let oxs: [&[f64]; 1] = [&ox[..cut]];
        let inp = PairInput {
            first_min: 0,
            ly: &ly[..cut],
            lx: &lx[..cut],
            oy: &oy[..cut],
            ox: &oxs,
            wx: &[1.0],
            fund_y: &[],
            fund_x: &[],
        };
        let mut multi: Vec<Rec> = cells.iter().map(|_| Rec::default()).collect();
        let cs = run_pair_multi(&inp, &cells, &mut multi).unwrap();
        for ((p, m), c) in cells.iter().zip(&multi).zip(&cs) {
            let mut one = Rec::default();
            let c1 = run_pair(&inp, p, &mut one);
            assert_eq!(*c, c1, "счётчики, обрезка {cut}");
            assert_eq!(m.trades, one.trades, "сделки, обрезка {cut}");
            assert_eq!(m.open, one.open, "открытая позиция, обрезка {cut}");
            assert_eq!(c1.open_at_end == 1, one.open.is_some());
            opened += u64::from(one.open.is_some());
        }
    }
    assert!(
        opened > 0,
        "тест пуст: позиции на конце нет ни в одной обрезке"
    );
}

#[test]
fn mtm_values_each_basket_leg_at_its_own_last_close() {
    // Корзина из двух компонент: Y лонг, X — две ноги с весами ½; вход комиссии taker, выход maker (params).
    // Закрытия ряда (минута = 10 + индекс, вход в минуте 12): Y последняя цена в индексе 5, X0 — в 4, X1 — в 3.
    let p = params(BetaMode::Fixed(1.0), LegKind::Perp, LegKind::Perp);
    let o = OpenSnap {
        dir: 1,
        sig_in: 11,
        fill_in: 12,
        fill_x: Some(12),
        qty: vec![1.0 / 100.0, -0.5 / 50.0, -0.5 / 20.0],
        pin: vec![100.0, 50.0, 20.0],
        fund: 1e-4,
    };
    let nan = f64::NAN;
    let cy = [100.0, 100.0, 100.0, 101.0, 102.0, 103.0, nan, nan];
    let cx0 = [50.0, 50.0, 50.0, 52.0, 55.0, nan, nan, nan];
    let cx1 = [20.0, 20.0, 20.0, 19.0, nan, nan, nan, nan];
    let m = value(&p, &o, 10, &cy, &[&cx0, &cx1]);
    let gross = 1e4 * (0.01 * (103.0 - 100.0) - 0.01 * (55.0 - 50.0) - 0.025 * (19.0 - 20.0));
    let (fi, fo) = (leg_fee_bps(true), leg_fee_bps(false));
    let fee = 0.01 * (100.0 * fi + 103.0 * fo)
        + 0.01 * (50.0 * fi + 55.0 * fo)
        + 0.025 * (20.0 * fi + 19.0 * fo);
    assert!(close(m.gross_bps, gross), "{m:?}");
    assert!(close(m.fee_bps, fee), "{m:?}");
    assert!(close(m.funding_bps, 1.0), "{m:?}");
    assert!(close(m.net_bps, gross - fee + 1.0), "{m:?}");
    assert_eq!(m.stale_min, 7 - 3); // конец окна — индекс 7, самая «старая» нога X1 — индекс 3
}

#[test]
fn last_valid_close_search() {
    let c = [1.0, f64::NAN, 3.0, f64::NAN, f64::NAN];
    assert_eq!(last_valid(&c, 0), Some((2, 3.0)));
    assert_eq!(last_valid(&c, 2), Some((2, 3.0)));
    assert_eq!(last_valid(&c, 3), None);
    assert_eq!(last_valid(&c, 9), None);
}

// ---- TK-493 волна 3: --rearm, --trades-ext, --ext-out, --job-day-out (аудит F-01, F-02, F-03, P-02, M-07) ----
// Сценарии замкнутые (ряд Y, X плоская 100, окно 24, β = 1, пересчёт окна каждую минуту), ожидания — независимая модель
// правила (см. `fix_tests`): те же числа, что в проверках ядра, но через `statarb run` и файлы вывода.

use super::fix_tests::{hole_case, two_events, EVENT};

/// Клетка окна 24 с β = 1: пороги и срок параметрами, `min_obs` = окну.
fn z_cell(label: &str, entry: &str, exit: &str, stop: &str, hold: &str) -> Vec<String> {
    args(&[
        "--cell",
        label,
        "--window",
        "24",
        "--min-obs",
        "24",
        "--beta-fixed",
        "1.0",
        "--entry-z",
        entry,
        "--exit-z",
        exit,
        "--stop-z",
        stop,
        "--max-hold",
        hold,
        "--refresh",
        "1",
    ])
}

/// Прогон `pair` над панелями `root/<panels>`: задания, клетка (`--cell …` или `--cells …`), доп. флаги.
fn run_with(
    root: &Path,
    tag: &str,
    panels: &str,
    jobs: &str,
    cell: &[String],
    extra: &[String],
) -> Files {
    let f = files(root, tag, jobs);
    let mut a = args(&["--kind", "pair"]);
    kv(&mut a, "--panels", &root.join(panels));
    a.extend(common(&f));
    a.extend(cell.iter().cloned());
    a.extend(extra.iter().cloned());
    go(&a).unwrap();
    f
}

/// Новые флаги вывода: `--trades-ext`, `--ext-out`, `--job-day-out`.
fn flag_files(root: &Path, tag: &str) -> (Vec<String>, PathBuf, PathBuf) {
    let (ext, jd) = (
        root.join(format!("ext-{tag}.csv")),
        root.join(format!("jd-{tag}.csv")),
    );
    let mut v = args(&["--trades-ext"]);
    kv(&mut v, "--ext-out", &ext);
    kv(&mut v, "--job-day-out", &jd);
    (v, ext, jd)
}

const EXT_HEADER: &str = "cell,jobs_done,eval_minutes,z_def_minutes,z_def_share,minutes_in_pos,pos_z_nan_minutes,\
pos_z_nan_share,noentry_window_minutes,rearm_blocked,trades_n,exit_rule,exit_stop,exit_timeout,open_end,net_y_sum_bps,\
net_gross_sum_bps,net_y_mean_bps,net_gross_mean_bps,notional_mean,beta_neg_share,beta_min,beta_p01,beta_p05,beta_p25,\
beta_p50,beta_p75,beta_p95,beta_p99,beta_max";

/// (dir, sig_in, fill_in, sig_out, fill_out, причина выхода).
type Tr = (i64, i64, i64, i64, i64, String);

/// Сделки из `--trades-out` со столбцами `--trades-ext`.
fn trade_keys(path: &Path) -> Vec<Tr> {
    table(path)
        .iter()
        .map(|r| {
            (
                num(r, "dir") as i64,
                num(r, "sig_in") as i64,
                num(r, "fill_in") as i64,
                num(r, "sig_out") as i64,
                num(r, "fill_out") as i64,
                r["exit_reason"].clone(),
            )
        })
        .collect()
}

fn owned(v: &[(i64, i64, i64, i64, i64, &str)]) -> Vec<Tr> {
    v.iter()
        .map(|t| (t.0, t.1, t.2, t.3, t.4, t.5.to_string()))
        .collect()
}

/// Строка сделки без двух последних столбцов (`exit_reason,beta_in`).
fn without_ext(line: &str) -> String {
    let f: Vec<&str> = line.split(',').collect();
    f[..f.len() - 2].join(",")
}

#[test]
fn rearm_reason_and_ext_out_on_closed_scenario() {
    let d = tempfile::tempdir().unwrap();
    let root = d.path();
    put_panel(&root.join("p"), "AAA", &series_from(&two_events()));
    put_panel(&root.join("p"), "XXX", &flat(100));
    let jobs = "y,x,from_day,to_day\nAAA,XXX,0,1\n";
    let cell = z_cell("cA", "2.0", "0.2", "3.5", "0");
    let base = run_with(root, "base", "p", jobs, &cell, &[]);
    let (fl, ext0, jd0) = flag_files(root, "off");
    let off = run_with(root, "off", "p", jobs, &cell, &fl);

    // новые флаги не трогают старые выводы: суточный csv побайтно, сделки — те же столбцы, дописаны два
    assert_eq!(body(&off.daily), body(&base.daily));
    assert_eq!(header(&base.trades), OLD_TRADES_HEADER);
    assert_eq!(
        header(&off.trades),
        format!("{OLD_TRADES_HEADER},exit_reason,beta_in")
    );
    let stripped: Vec<String> = body(&off.trades).iter().map(|l| without_ext(l)).collect();
    assert_eq!(stripped, body(&base.trades));

    // без rearm: стоп закрывает и тут же открывает заново (три сделки на событие), причины — по модели
    let want_off = owned(&[
        (1, 30, 31, 31, 32, "stop"),
        (1, 32, 33, 33, 34, "stop"),
        (1, 34, 35, 41, 42, "rule"),
        (1, 68, 69, 69, 70, "stop"),
        (1, 70, 71, 71, 72, "stop"),
        (1, 72, 73, 79, 80, "rule"),
    ]);
    assert_eq!(trade_keys(&off.trades), want_off);
    assert!(table(&off.trades).iter().all(|r| num(r, "beta_in") == 1.0));
    assert_eq!(header(&ext0), EXT_HEADER);
    let e = &table(&ext0)[0];
    assert_eq!(e["cell"], "cA");
    assert_eq!(
        (
            num(e, "trades_n"),
            num(e, "exit_rule"),
            num(e, "exit_stop"),
            num(e, "exit_timeout"),
            num(e, "open_end")
        ),
        (6.0, 2.0, 4.0, 0.0, 0.0)
    );
    assert_eq!(
        (
            num(e, "eval_minutes"),
            num(e, "z_def_minutes"),
            num(e, "minutes_in_pos")
        ),
        (63.0, 54.0, 18.0)
    );
    assert!(close(num(e, "z_def_share"), 54.0 / 63.0));
    assert_eq!(
        (
            num(e, "pos_z_nan_minutes"),
            num(e, "noentry_window_minutes"),
            num(e, "rearm_blocked")
        ),
        (0.0, 0.0, 0.0)
    );
    // F-03: β = 1 у всех сделок, валовый номинал 1 + |β|·Σw = 2: bps валового номинала вдвое меньше bps номинала Y
    let net: f64 = table(&off.trades).iter().map(|r| num(r, "net_bps")).sum();
    assert!(close(num(e, "net_y_sum_bps"), net), "{e:?}");
    assert!(close(num(e, "net_gross_sum_bps"), net / 2.0), "{e:?}");
    assert!(close(num(e, "net_y_mean_bps"), net / 6.0));
    assert!(close(num(e, "net_gross_mean_bps"), net / 12.0));
    assert!(close(num(e, "notional_mean"), 2.0));
    assert_eq!(num(e, "beta_neg_share"), 0.0);
    for k in [
        "beta_min", "beta_p01", "beta_p05", "beta_p25", "beta_p50", "beta_p75", "beta_p95",
        "beta_p99", "beta_max",
    ] {
        assert_eq!(num(e, k), 1.0, "{k}");
    }
    // сутки заданий: одна строка (клетка, задание 0, сутки 0), шесть сделок, сутки активны; суммы = суточный csv
    let jd = table(&jd0);
    assert_eq!(jd.len(), 1);
    assert_eq!(
        (
            jd[0]["cell"].as_str(),
            jd[0]["y"].as_str(),
            jd[0]["x"].as_str()
        ),
        ("cA", "AAA", "XXX")
    );
    assert_eq!(
        (
            num(&jd[0], "job"),
            num(&jd[0], "day"),
            num(&jd[0], "trades"),
            num(&jd[0], "active")
        ),
        (0.0, 0.0, 6.0, 1.0)
    );
    let day = &table(&off.daily)[0];
    for k in ["net_bps", "gross_bps", "fee_bps", "funding_bps"] {
        assert_eq!(num(&jd[0], k), num(day, k), "{k}");
    }

    // с rearm: вход в сторону стопа закрыт, пока |z| ≥ 2; на событие одна сделка
    let (mut fl_on, ext1, jd1) = flag_files(root, "on");
    fl_on.push("--rearm".to_string());
    let on = run_with(root, "on", "p", jobs, &cell, &fl_on);
    let want_on = owned(&[(1, 30, 31, 31, 32, "stop"), (1, 68, 69, 69, 70, "stop")]);
    assert_eq!(trade_keys(&on.trades), want_on);
    let e = &table(&ext1)[0];
    assert_eq!(
        (
            num(e, "trades_n"),
            num(e, "exit_stop"),
            num(e, "rearm_blocked"),
            num(e, "minutes_in_pos")
        ),
        (2.0, 2.0, 10.0, 2.0)
    );
    assert_eq!(table(&jd1)[0]["trades"], "2");
    // один --rearm, без остальных новых флагов: те же две сделки, заголовок сделок прежний
    let only = run_with(root, "only", "p", jobs, &cell, &args(&["--rearm"]));
    assert_eq!(body(&only.daily), body(&on.daily));
    assert_eq!(header(&only.trades), OLD_TRADES_HEADER);
    assert_eq!(body(&only.trades).len(), 2);
}

#[test]
fn ext_out_counts_holes_in_window_and_in_position() {
    let d = tempfile::tempdir().unwrap();
    let root = d.path();
    put_panel(&root.join("p"), "DDD", &series_from(&hole_case()));
    put_panel(&root.join("p"), "XXX", &flat(100));
    let jobs = "y,x,from_day,to_day\nDDD,XXX,0,1\n";
    let cell = z_cell("cD", "1.5", "0.0", "inf", "0");
    let (fl, ext, jd) = flag_files(root, "d");
    let f = run_with(root, "d", "p", jobs, &cell, &fl);
    // позиция открыта на конце: сделок нет, суточный csv пуст
    assert_eq!(body(&f.daily).len(), 0);
    assert_eq!(body(&f.trades).len(), 0);
    let e = &table(&ext)[0];
    assert_eq!(
        (
            num(e, "eval_minutes"),
            num(e, "z_def_minutes"),
            num(e, "minutes_in_pos")
        ),
        (77.0, 12.0, 35.0)
    );
    assert_eq!(
        (
            num(e, "pos_z_nan_minutes"),
            num(e, "noentry_window_minutes")
        ),
        (24.0, 23.0)
    );
    assert!(close(num(e, "z_def_share"), 12.0 / 77.0));
    assert!(close(num(e, "pos_z_nan_share"), 24.0 / 35.0));
    assert_eq!((num(e, "trades_n"), num(e, "open_end")), (0.0, 1.0));
    for k in [
        "net_y_mean_bps",
        "net_gross_mean_bps",
        "notional_mean",
        "beta_neg_share",
        "beta_min",
        "beta_p50",
        "beta_max",
    ] {
        assert_eq!(e[k], "nan", "{k}");
    }
    // сутки: сделок нет, но z определён (64…69, 94…99) — строка суток есть, активна
    let r = &table(&jd)[0];
    assert_eq!(
        (
            num(r, "day"),
            num(r, "trades"),
            num(r, "active"),
            num(r, "net_bps")
        ),
        (0.0, 0.0, 1.0, 0.0)
    );
}

#[test]
fn job_day_out_sums_to_daily_and_skips_warmup_days() {
    let d = tempfile::tempdir().unwrap();
    let root = d.path();
    // 3 суток: флэт 100, событие на минутах 1430 (за 10 минут до суток 1: разогрев задания 0) и 2910
    let mut cy = vec![100.0; 3 * 1440];
    for b in [1430usize, 2910] {
        cy[b..b + EVENT.len()].copy_from_slice(&EVENT);
    }
    put_panel(&root.join("p"), "EEE", &series_from(&cy));
    put_panel(&root.join("p"), "XXX", &flat(cy.len()));
    // задание 0: сутки 1…3 (окно греется с минуты 1416), 1: нет панели ZZZ, 2: сутки 0…2
    let jobs = "y,x,from_day,to_day\nEEE,XXX,1,3\nEEE,ZZZ,1,3\nEEE,XXX,0,2\n";
    std::fs::write(
        root.join("cells3.csv"),
        "cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh\n\
         c0,24,24,1.0,2.0,0.2,3.5,0,1\nc1,24,24,1.0,2.0,0.2,inf,0,1\n",
    )
    .unwrap();
    let mut cells = args(&[]);
    kv(&mut cells, "--cells", &root.join("cells3.csv"));
    let (mut fl, _ext, jd) = flag_files(root, "j");
    fl.extend(args(&["--trade-jobs", "2"]));
    let f = run_with(root, "j", "p", jobs, &cells, &fl);

    // (клетка, задание, сутки, сделок, активна): у задания 0 нет суток 0 — там z определён в разогреве (минута 1439),
    // но сутки до `from_day` не считаются; у задания 1 (нет панели) строк нет
    let want: [(&str, i64, i64, i64, i64); 8] = [
        ("c0", 0, 1, 0, 1),
        ("c0", 0, 2, 3, 1),
        ("c0", 2, 0, 2, 1),
        ("c0", 2, 1, 1, 1),
        ("c1", 0, 1, 0, 1),
        ("c1", 0, 2, 1, 1),
        ("c1", 2, 0, 0, 1),
        ("c1", 2, 1, 1, 1),
    ];
    let rows = table(&jd);
    let got: Vec<(String, i64, i64, i64, i64)> = rows
        .iter()
        .map(|r| {
            (
                r["cell"].clone(),
                num(r, "job") as i64,
                num(r, "day") as i64,
                num(r, "trades") as i64,
                num(r, "active") as i64,
            )
        })
        .collect();
    let want_owned: Vec<(String, i64, i64, i64, i64)> = want
        .iter()
        .map(|w| (w.0.to_string(), w.1, w.2, w.3, w.4))
        .collect();
    assert_eq!(got, want_owned);
    // сумма по заданиям (в порядке номера, от нуля) равна суточному csv клетки побитно
    let cols = ["net_bps", "gross_bps", "fee_bps", "funding_bps", "trades"];
    let mut sums: std::collections::BTreeMap<(String, i64), [f64; 5]> = Default::default();
    for r in &rows {
        let e = sums
            .entry((r["cell"].clone(), num(r, "day") as i64))
            .or_default();
        for (i, k) in cols.iter().enumerate() {
            e[i] += num(r, k);
        }
    }
    let daily = table(&f.daily);
    let with_trades = sums.values().filter(|v| v[4] > 0.0).count();
    assert_eq!(daily.len(), with_trades);
    for r in &daily {
        let s = sums[&(r["cell"].clone(), num(r, "day") as i64)];
        for (i, k) in cols.iter().enumerate() {
            assert_eq!(s[i], num(r, k), "{} сутки {} {k}", r["cell"], r["day"]);
        }
    }
    // сделки заданий 0 и 2 (--trade-jobs 0 из общих флагов и 2): по клеткам, внутри — по заданию; причины по модели
    let kinds: Vec<(String, i64, String)> = table(&f.trades)
        .iter()
        .map(|r| {
            (
                r["cell"].clone(),
                num(r, "job") as i64,
                r["exit_reason"].clone(),
            )
        })
        .collect();
    let want_kinds: Vec<(String, i64, String)> = [
        ("c0", 0, "stop"),
        ("c0", 0, "stop"),
        ("c0", 0, "rule"),
        ("c0", 2, "stop"),
        ("c0", 2, "stop"),
        ("c0", 2, "rule"),
        ("c1", 0, "rule"),
        ("c1", 2, "rule"),
    ]
    .iter()
    .map(|(a, j, b)| (a.to_string(), *j, b.to_string()))
    .collect();
    assert_eq!(kinds, want_kinds);
}

#[test]
fn new_flags_are_validated() {
    let d = tempfile::tempdir().unwrap();
    let root = d.path();
    put_panel(&root.join("p"), "AAA", &series_from(&two_events()));
    put_panel(&root.join("p"), "XXX", &flat(100));
    let jobs = "y,x,from_day,to_day\nAAA,XXX,0,1\n";
    // --rearm с правилом funding
    let f = files(root, "v1", jobs);
    let mut a = args(&["--kind", "pair"]);
    kv(&mut a, "--panels", &root.join("p"));
    kv(&mut a, "--funding-y", root);
    a.extend(common(&f));
    a.extend(z_cell("c", "2.0", "0.2", "3.5", "0"));
    a.extend(args(&[
        "--rearm",
        "--rule",
        "funding",
        "--funding-k",
        "2",
        "--hold-payments",
        "3",
    ]));
    let e = go(&a).unwrap_err().to_string();
    assert!(e.contains("--rearm"), "{e}");
    // --trades-ext без --trades-out
    let f = files(root, "v2", jobs);
    let mut a = args(&[
        "--kind",
        "pair",
        "--trades-ext",
        "--min-span",
        "10",
        "--threads",
        "1",
    ]);
    kv(&mut a, "--panels", &root.join("p"));
    kv(&mut a, "--jobs", &f.jobs);
    kv(&mut a, "--daily-out", &f.daily);
    a.extend(z_cell("c", "2.0", "0.2", "3.5", "0"));
    let e = go(&a).unwrap_err().to_string();
    assert!(e.contains("--trades-ext"), "{e}");
}
