use super::DriverArg;
use super::*;
use crate::commands::lob::bounce_verdict::parse_form;
use crate::commands::lob::test_support::{delta_frame, snap_frame, trade_frame, write_day};
use crate::commands::lob::{H3Args, H3ModeArg};

/// Та же фикстура, что у `touches/tests.rs`: три касания бида 99 за 6 секунд.
fn touch_frames() -> Vec<Vec<crate::binlog::Record>> {
    vec![
        snap_frame(0, &[(98, 10), (99, 10), (100, 10)], &[(105, 10)]),
        trade_frame(500, 100, 3),
        delta_frame(1000, &[(100, 0)], &[]),
        delta_frame(2000, &[(100, 10)], &[]),
        delta_frame(3000, &[(100, 0)], &[]),
        delta_frame(4000, &[(101, 10)], &[]),
        delta_frame(5000, &[(101, 0)], &[]),
        trade_frame(5500, 99, 4),
        delta_frame(6000, &[(99, 1)], &[]),
    ]
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
        h3: H3Args {
            h3_mode: H3ModeArg::Percentile,
            h3_lots: Some(5),
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

/// Сетка — ровно 48 форм В-58, в порядке стоп × дедлайн × ранний, имена
/// читаются вердиктом (`parse_form`) и не повторяются.
#[test]
fn forms_are_the_48_of_v58_in_grid_order() {
    let forms = grid_forms();
    assert_eq!(forms.len(), 48);
    assert_eq!(forms[0].label, "before-60-off");
    assert_eq!(forms[47].label, "behind-7200-3");
    let mut seen = std::collections::BTreeSet::new();
    for f in &forms {
        let (stop, deadline, early) = parse_form(f.label).unwrap();
        assert_eq!(deadline as i64, f.deadline_secs);
        assert_eq!(early.map(|s| s as i64), f.early_exit_secs);
        assert_eq!(
            match f.stop {
                StopModeArg::Before => "before",
                StopModeArg::At => "at",
                StopModeArg::Behind => "behind",
            },
            stop
        );
        assert!(seen.insert(f.label), "форма {} повторяется", f.label);
    }
}

/// Одни сутки фикстуры → 48 строк `forms.csv` с одинаковым числом сигналов
/// (касания общие для всех форм — S1), `rounds.csv` с шапкой и колонками
/// `symbol`/`day_utc`/`form` (K2), сводка считает сутки и символ.
#[test]
fn grid_runs_the_fixture_day_and_writes_48_forms() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let summary = run_bounce_grid(&args(dir.path(), false)).expect("прогон сетки на фикстуре");
    assert_eq!(summary.forms, 48);
    assert_eq!(summary.symbols_done, 1);
    assert_eq!(summary.symbols_skipped_unverified, 0);
    assert_eq!(summary.symbol_days, 1);

    let (fh, forms) = read_csv(&summary.forms_path);
    assert_eq!(forms.len(), 48, "строка на форму: {forms:?}");
    let labels: std::collections::BTreeSet<&str> =
        forms.iter().map(|r| col(&fh, r, "form")).collect();
    let expected: std::collections::BTreeSet<&str> = grid_forms().iter().map(|f| f.label).collect();
    assert_eq!(labels, expected);
    let n_signals: std::collections::BTreeSet<&str> =
        forms.iter().map(|r| col(&fh, r, "n_signals")).collect();
    assert_eq!(
        n_signals.len(),
        1,
        "сигналы одни на все формы: {n_signals:?}"
    );
    let n: usize = n_signals.iter().next().unwrap().parse().unwrap();
    assert_eq!(n, 3, "фикстура даёт три касания бида 99");
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
