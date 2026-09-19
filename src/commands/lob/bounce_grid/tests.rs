use super::DriverArg;
use super::*;
use crate::commands::lob::backtest::{StopForm, TakeForm};
use crate::commands::lob::bounce_verdict::parse_form;
use crate::commands::lob::test_support::{delta_frame, snap_frame, trade_frame, write_day};
use crate::commands::lob::{H3Args, H3ModeArg};

/// Фикстура `touches/tests.rs` (три касания бида 99 за 6 секунд), сдвинутая
/// на `LEAD_S` секунд «тихой» книги впереди: ряд `σ` (В-62) должен покрывать
/// окно 60 с к первому касанию, иначе ни у одной формы нет сигналов.
const LEAD_S: i64 = 70;

fn touch_frames() -> Vec<Vec<crate::binlog::Record>> {
    let lead = LEAD_S * 1_000;
    let mut frames = vec![snap_frame(
        0,
        &[(98, 10), (99, 10), (100, 10)],
        &[(105, 10)],
    )];
    for s in 1..=LEAD_S {
        // Лёгкое дыхание аска: середина двигается, σ не ноль.
        let ask = if s % 2 == 0 { 105 } else { 106 };
        frames.push(delta_frame(
            s * 1_000,
            &[],
            &[(ask, 10), (105 + 106 - ask, 0)],
        ));
    }
    frames.extend([
        trade_frame(lead + 500, 100, 3),
        delta_frame(lead + 1000, &[(100, 0)], &[]),
        delta_frame(lead + 2000, &[(100, 10)], &[]),
        delta_frame(lead + 3000, &[(100, 0)], &[]),
        delta_frame(lead + 4000, &[(101, 10)], &[]),
        delta_frame(lead + 5000, &[(101, 0)], &[]),
        trade_frame(lead + 5500, 99, 4),
        delta_frame(lead + 6000, &[(99, 1)], &[]),
    ]);
    frames
}

fn fixture_root(dir: &std::path::Path, verified: bool) {
    std::fs::write(
        crate::commands::record::instruments_csv_path(dir),
        "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n\
         SOLUSDT,0.01,0.1,0.1,5,5\n",
    )
    .unwrap();
    write_day(dir, "SOLUSDT", "2026-09-08", &touch_frames());
    std::fs::write(
        dir.join("session.json"),
        "{\"started_utc\":\"2026-09-08T00:00:00Z\",\"start_hour_utc\":0,\"instruments\":[\"SOLUSDT\"]}",
    )
    .unwrap();
    if verified {
        std::fs::write(dir.join("verify-SOLUSDT.status"), "ok").unwrap();
    }
}

fn args(root: &std::path::Path, allow_unverified: bool) -> BounceGridArgs {
    BounceGridArgs {
        root: root.to_path_buf(),
        symbols: vec!["SOLUSDT".to_string()],
        median_rtt_ns: 20_000_000,
        p95_rtt_ns: 20_000_000,
        order_qty_e9: Some(100_000_000),
        order_qty_from_pool: false,
        post_only: false,
        stop_form: vec!["s1".to_string(), "s2".to_string()],
        take_form: vec!["t1".to_string()],
        take_floor_fees: Some(1.0),
        frontrun_only: false,
        min_age_secs: None,
        min_flow_pct: None,
        h3: H3Args {
            h3_mode: H3ModeArg::Percentile,
            h3_lots: Some(5),
            h3_usd: None,
            h3_strength_pct: None,
            h3_strength_window_bps: None,
        },
        h3_k: None,
        warmup_ms: Some(0),
        repeat_window_ms: Some(3_600_000),
        days: Vec::new(),
        threads: Some(3),
        driver: DriverArg::Setups,
        out_dir: root.join("grid"),
        allow_unverified,
    }
}

fn read_csv(path: &std::path::Path) -> (Vec<String>, Vec<Vec<String>>) {
    let text = std::fs::read_to_string(path).unwrap();
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_reader(text.as_bytes());
    let header: Vec<String> = r.headers().unwrap().iter().map(str::to_string).collect();
    let rows = r
        .records()
        .map(|rec| rec.unwrap().iter().map(str::to_string).collect())
        .collect();
    (header, rows)
}

fn col<'a>(header: &[String], row: &'a [String], name: &str) -> &'a str {
    let i = header
        .iter()
        .position(|h| h == name)
        .unwrap_or_else(|| panic!("колонки {name} нет: {header:?}"));
    &row[i]
}

/// Сетка В-62 — `stop_sigma × take_sigma × DEADLINE_SECS` в этом порядке,
/// имена читаются вердиктом (`parse_form`) и не повторяются.
#[test]
fn forms_are_the_sigma_grid_in_grid_order() {
    let forms = grid_forms(
        &[StopForm::Sigma(1.0), StopForm::Sigma(2.0)],
        &[TakeForm::Sigma(1.0)],
        Some(1.0),
    );
    assert_eq!(forms.len(), 8);
    assert_eq!(forms[0].label, "s1-t1-60");
    assert_eq!(forms[3].label, "s1-t1-7200");
    assert_eq!(forms[7].label, "s2-t1-7200");
    let mut seen = std::collections::BTreeSet::new();
    for f in &forms {
        let (stop, take, deadline) = parse_form(f.label).unwrap();
        assert_eq!(deadline as i64, f.deadline_secs);
        assert_eq!(stop, f.form.stop.label());
        assert_eq!(take, f.form.take.label());
        assert!(seen.insert(f.label), "форма {} повторяется", f.label);
    }
    // Позиционные формы базы — те же имена, что читает вердикт.
    let base = grid_forms(
        &[StopForm::Before, StopForm::Pct(1.0)],
        &[TakeForm::OneToOne],
        None,
    );
    assert_eq!(base[0].label, "before-1to1-60");
    assert_eq!(base[7].label, "pct1-1to1-7200");
}

/// Одни сутки фикстуры → строка `forms.csv` на форму с одинаковым числом
/// касаний (`n_signals + n_no_sigma`: касания общие для всех форм — S1; у форм
/// с окном `σ` длиннее записи сигналов нет, у 60-секундных — все три),
/// `rounds.csv` с шапкой и колонками `symbol`/`day_utc`/`form` (K2), сводка
/// считает сутки и символ.
#[test]
fn grid_runs_the_fixture_day_and_writes_every_form() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let summary = run_bounce_grid(&args(dir.path(), false)).expect("прогон сетки на фикстуре");
    assert_eq!(summary.forms, 8);
    assert_eq!(summary.symbols_done, 1);
    assert_eq!(summary.symbols_skipped_unverified, 0);
    assert_eq!(summary.symbol_days, 1);

    let (fh, forms) = read_csv(&summary.forms_path);
    assert_eq!(forms.len(), 8, "строка на форму: {forms:?}");
    let labels: std::collections::BTreeSet<&str> =
        forms.iter().map(|r| col(&fh, r, "form")).collect();
    let expected: std::collections::BTreeSet<&str> = grid_forms(
        &[StopForm::Sigma(1.0), StopForm::Sigma(2.0)],
        &[TakeForm::Sigma(1.0)],
        Some(1.0),
    )
    .iter()
    .map(|f| f.label)
    .collect();
    assert_eq!(labels, expected);
    for r in &forms {
        let n_signals: u64 = col(&fh, r, "n_signals").parse().unwrap();
        let n_skipped: u64 = col(&fh, r, "n_skipped").parse().unwrap();
        assert_eq!(
            n_signals + n_skipped,
            3,
            "фикстура даёт три касания бида 99: {r:?}"
        );
        let form = col(&fh, r, "form");
        if form.ends_with("-60") {
            assert_eq!(n_signals, 3, "окно 60 с покрыто: {form}");
        } else {
            assert_eq!(n_signals, 0, "окно длиннее записи — сигналов нет: {form}");
        }
    }
    for r in &forms {
        assert_eq!(col(&fh, r, "symbol"), "SOLUSDT");
        assert_eq!(col(&fh, r, "day_utc"), "2026-09-08");
        // Фикстура — 6 с данных: дедлайны 60 с и дольше выходят за конец записи,
        // `incomplete` тут допустим и обязан лишь быть булевым.
        assert!(matches!(col(&fh, r, "incomplete"), "true" | "false"));
    }

    let (rh, rounds) = read_csv(&summary.rounds_path);
    for name in [
        "symbol",
        "day_utc",
        "form",
        "signal_index",
        "t0_ns",
        "net_bps",
        "reason",
    ] {
        assert!(rh.iter().any(|h| h == name), "в rounds.csv нет {name}");
    }
    let fills_from_forms: u64 = forms
        .iter()
        .map(|r| col(&fh, r, "n_fills").parse::<u64>().unwrap())
        .sum();
    assert_eq!(rounds.len() as u64, fills_from_forms);
    assert_eq!(summary.rounds, fills_from_forms);
    assert!(dir.path().join("grid").join("manifest.txt").exists());
}

/// K1: без маркера `verify-<SYMBOL>.status == ok` символ пропускается и
/// артефакт остаётся пустым; `--allow-unverified` снимает требование.
#[test]
fn unverified_symbol_is_skipped_unless_allowed() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), false);
    let summary = run_bounce_grid(&args(dir.path(), false)).unwrap();
    assert_eq!(summary.symbols_skipped_unverified, 1);
    assert_eq!(summary.symbols_done, 0);
    let (_, forms) = read_csv(&summary.forms_path);
    assert!(forms.is_empty(), "без маркера ни одной строки");

    let summary = run_bounce_grid(&args(dir.path(), true)).unwrap();
    assert_eq!(summary.symbols_skipped_unverified, 0);
    assert_eq!(summary.symbols_done, 1);
}

/// Лот задаётся ровно одним способом — как у `lob backtest`.
#[test]
fn lot_must_come_from_exactly_one_source() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.order_qty_e9 = None;
    assert!(run_bounce_grid(&a).is_err());
    a.order_qty_from_pool = true;
    a.order_qty_e9 = Some(1);
    assert!(run_bounce_grid(&a).is_err());
}
