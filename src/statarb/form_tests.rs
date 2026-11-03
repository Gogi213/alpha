use super::cli::{dispatch, StatarbCommand};
use super::form::*;
use super::panel::{write_series, Series};
use clap::Parser;
use std::fs::{self, File};
use std::path::Path;

fn lcg(seed: u64) -> impl FnMut() -> f64 {
    let mut st = seed;
    move || {
        st = st
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        ((st >> 11) as f64 / (1u64 << 53) as f64) * 2.0 - 1.0
    }
}

fn series(first_min: i64, ln: &[f64]) -> Series {
    let close: Vec<f64> = ln.iter().map(|v| v.exp()).collect();
    Series {
        first_min,
        open: close.clone(),
        close,
    }
}

#[test]
fn window_row_cuts_the_span_and_pads_with_nan() {
    let s = series(10, &[0.0, 0.1, 0.2, 0.3]);
    // окно [8, 16): минуты 10..13 заполнены, остальное NaN
    let r = window_row(&s, 8, 8);
    assert!(r[0].is_nan() && r[1].is_nan() && r[6].is_nan() && r[7].is_nan());
    for (k, want) in [0.0, 0.1, 0.2, 0.3].into_iter().enumerate() {
        assert!((r[2 + k] - want).abs() < 1e-12, "{k}: {}", r[2 + k]);
    }
    // окно внутри спана
    let r = window_row(&s, 11, 2);
    assert!((r[0] - 0.1).abs() < 1e-12 && (r[1] - 0.2).abs() < 1e-12);
}

fn write_universe(dir: &Path, w0: i64, t: usize) {
    // B = 1.5·A + AR(½) — коинтегрирована; C, D, E — независимые блуждания; F — короткий ряд
    let mut u = lcg(11);
    let mut a = vec![5.0f64; t];
    for k in 1..t {
        a[k] = a[k - 1] + 2e-3 * u();
    }
    let mut e = 0.0;
    let b: Vec<f64> = (0..t)
        .map(|k| {
            e = 0.5 * e + 1e-3 * u();
            1.5 * a[k] + e
        })
        .collect();
    let mut all = vec![("A", a), ("B", b)];
    for n in ["C", "D", "E"] {
        let mut w = vec![4.0f64; t];
        for k in 1..t {
            w[k] = w[k - 1] + 2e-3 * u();
        }
        all.push((n, w));
    }
    for (n, ln) in all {
        let mut f = File::create(dir.join(format!("{n}.sap"))).unwrap();
        write_series(&mut f, &series(w0, &ln)).unwrap();
    }
    let mut f = File::create(dir.join("F.sap")).unwrap();
    write_series(&mut f, &series(w0 + t as i64 - 50, &[5.0; 50])).unwrap();
}

#[derive(Parser)]
struct Cli {
    #[command(subcommand)]
    cmd: StatarbCommand,
}

#[test]
fn form_writes_jobs_for_run_with_y_and_x_legs() {
    let dir = std::env::temp_dir().join(format!("tk486-form-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    // окно 4 суток перед сутками 10: минуты 6·1440..10·1440
    let (window_days, from_day) = (4i64, 10i64);
    let t = (window_days * 1440) as usize;
    write_universe(&dir, (from_day - window_days) * 1440, t);
    let jobs = dir.join("jobs.csv");
    let detail = dir.join("detail.csv");
    let cli = Cli::parse_from([
        "x",
        "form",
        "--panels",
        dir.to_str().unwrap(),
        "--from-day",
        "10",
        "--to-day",
        "13",
        "--window-days",
        "4",
        "--block-chunks",
        "9",
        "--min-obs",
        "1000",
        "--alpha",
        "0.05",
        "--threads",
        "2",
        "--jobs-out",
        jobs.to_str().unwrap(),
        "--detail-out",
        detail.to_str().unwrap(),
    ]);
    dispatch(cli.cmd).unwrap();
    let txt = fs::read_to_string(&jobs).unwrap();
    let lines: Vec<&str> = txt.lines().collect();
    assert_eq!(lines[0], "y,x,from_day,to_day");
    // одна пара; короткий символ F отсеян по min_obs; ноги — формат run: 4 поля
    assert_eq!(lines.len(), 2, "{txt}");
    let f: Vec<&str> = lines[1].split(',').collect();
    assert_eq!(f.len(), 4);
    assert!(f[2] == "10" && f[3] == "13");
    let det = fs::read_to_string(&detail).unwrap();
    let d: Vec<&str> = det.lines().nth(1).unwrap().split(',').collect();
    assert_eq!((d[0], d[1]), (f[0], f[1]));
    let beta: f64 = d[2].parse().unwrap();
    // y = B, x = A (β = 1.5) или y = A, x = B (β = 1/1.5)
    let want = if f[0] == "B" { 1.5 } else { 1.0 / 1.5 };
    assert!(matches!((f[0], f[1]), ("B", "A") | ("A", "B")), "{lines:?}");
    assert!((beta - want).abs() / want < 0.05, "{beta} против {want}");
    fs::remove_dir_all(&dir).ok();
}
