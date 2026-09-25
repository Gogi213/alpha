use super::*;
use crate::commands::lob::replay_symbol;
use crate::commands::lob::test_support::{
    snap_frame, touch_frames, trade_frame_flags, write_day, write_day_part,
};
use crate::lob::levels::{H3Mode, LevelsConfig};

fn trades_args(root: &std::path::Path) -> TradesArgs {
    TradesArgs {
        root: root.to_path_buf(),
        symbol: "SOLUSDT".to_string(),
        days: Vec::new(),
        out: None,
        allow_unverified: true,
    }
}

fn read_rows(path: &std::path::Path) -> (Vec<String>, Vec<Vec<String>>) {
    let mut r = csv::Reader::from_path(path).unwrap();
    let header: Vec<String> = r.headers().unwrap().iter().map(str::to_string).collect();
    let rows = r
        .records()
        .map(|rec| rec.unwrap().iter().map(str::to_string).collect())
        .collect();
    (header, rows)
}

fn col<'a>(header: &[String], row: &'a [String], name: &str) -> &'a str {
    let i = header.iter().position(|h| h == name).unwrap();
    &row[i]
}

/// Критерий приёмки T1: `lob trades` на фикстуре с обеими сторонами
/// агрессора и обоими флагами пишет `trades-<SYMBOL>.csv` с колонками
/// `TRADES_COLUMNS`, по строке на сделку, в порядке записи файла.
#[test]
fn trades_fixture_writes_side_price_and_flags() {
    let dir = tempfile::tempdir().unwrap();
    let frames = vec![
        snap_frame(0, &[(98, 10)], &[(105, 10)]),
        trade_frame_flags(500, 100, 3, false, false, false), // sell, без флагов
        trade_frame_flags(1000, 105, 2, true, false, false), // buy, без флагов
        trade_frame_flags(1500, 98, 7, false, true, false),  // sell, block
        trade_frame_flags(2000, 105, 1, true, false, true),  // buy, rpi
    ];
    write_day(dir.path(), "SOLUSDT", "2026-09-08", &frames);

    let summary = run_trades(&trades_args(dir.path())).unwrap();
    assert_eq!(summary.days, 1);
    assert_eq!(summary.trades, 4);
    assert_eq!(summary.buy, 2);
    assert_eq!(summary.sell, 2);
    assert_eq!(
        summary.out,
        dir.path().join("trades-SOLUSDT.csv"),
        "имя артефакта — trades-<SYMBOL>.csv"
    );

    let (header, rows) = read_rows(&summary.out);
    assert_eq!(header, TRADES_COLUMNS.map(str::to_string).to_vec());
    assert_eq!(rows.len(), 4);
    for r in &rows {
        assert_eq!(r.len(), TRADES_COLUMNS.len());
    }

    let r0 = &rows[0];
    assert_eq!(col(&header, r0, "day_utc"), "2026-09-08");
    assert_eq!(col(&header, r0, "exch_ts_ms"), "500");
    assert_eq!(
        col(&header, r0, "local_ts_ms"),
        "500",
        "0.5 мс до целой — усечение вниз"
    );
    assert_eq!(col(&header, r0, "side"), "sell");
    assert_eq!(col(&header, r0, "price_tick"), "100");
    assert_eq!(
        col(&header, r0, "price_usd"),
        "1",
        "тик 0.01 × 100 = 1.00 — без хвоста нулей"
    );
    assert_eq!(col(&header, r0, "qty_lots"), "3");
    assert_eq!(col(&header, r0, "block"), "false");
    assert_eq!(col(&header, r0, "rpi"), "false");

    let r1 = &rows[1];
    assert_eq!(col(&header, r1, "side"), "buy");
    assert_eq!(col(&header, r1, "price_tick"), "105");
    assert_eq!(col(&header, r1, "price_usd"), "1.05");
    assert_eq!(col(&header, r1, "block"), "false");
    assert_eq!(col(&header, r1, "rpi"), "false");

    let r2 = &rows[2];
    assert_eq!(col(&header, r2, "side"), "sell");
    assert_eq!(col(&header, r2, "price_tick"), "98");
    assert_eq!(col(&header, r2, "price_usd"), "0.98");
    assert_eq!(col(&header, r2, "block"), "true");
    assert_eq!(col(&header, r2, "rpi"), "false");

    let r3 = &rows[3];
    assert_eq!(col(&header, r3, "side"), "buy");
    assert_eq!(col(&header, r3, "block"), "false");
    assert_eq!(col(&header, r3, "rpi"), "true");
}

/// Пустые сутки (в бинлоге только книга, без единой сделки) — файл с одним
/// заголовком, не ошибка.
#[test]
fn trades_empty_day_writes_header_only() {
    let dir = tempfile::tempdir().unwrap();
    write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &[snap_frame(0, &[(98, 10)], &[(105, 10)])],
    );

    let summary = run_trades(&trades_args(dir.path())).unwrap();
    assert_eq!(summary.days, 1);
    assert_eq!(summary.trades, 0);
    assert_eq!(summary.buy, 0);
    assert_eq!(summary.sell, 0);

    let (header, rows) = read_rows(&summary.out);
    assert_eq!(header, TRADES_COLUMNS.map(str::to_string).to_vec());
    assert!(rows.is_empty());
}

/// Несколько частей одних суток (`-pN`, таск 22) читаются подряд одним
/// потоком, в порядке частей — как остальные читатели `session_binlog_for`.
#[test]
fn trades_multiple_parts_read_in_chronological_order() {
    let dir = tempfile::tempdir().unwrap();
    write_day_part(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        1,
        &[
            snap_frame(0, &[(98, 10)], &[(105, 10)]),
            trade_frame_flags(500, 98, 2, false, false, false),
        ],
    );
    write_day_part(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        2,
        &[
            snap_frame(0, &[(98, 8)], &[(105, 10)]),
            trade_frame_flags(1500, 98, 5, false, false, false),
        ],
    );

    let summary = run_trades(&trades_args(dir.path())).unwrap();
    assert_eq!(summary.days, 1, "обе части — одни сутки");
    assert_eq!(summary.trades, 2);

    let (header, rows) = read_rows(&summary.out);
    assert_eq!(rows.len(), 2);
    assert_eq!(
        col(&header, &rows[0], "qty_lots"),
        "2",
        "часть 1 читается первой"
    );
    assert_eq!(
        col(&header, &rows[1], "qty_lots"),
        "5",
        "часть 2 читается второй"
    );
}

/// `--day` сужает выдачу до запрошенных суток; `TradesSummary.days` считает
/// только их.
#[test]
fn trades_day_filter_narrows_to_requested_day() {
    let dir = tempfile::tempdir().unwrap();
    write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &[
            snap_frame(0, &[(98, 10)], &[(105, 10)]),
            trade_frame_flags(500, 100, 1, false, false, false),
        ],
    );
    write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-09",
        &[
            snap_frame(0, &[(98, 10)], &[(105, 10)]),
            trade_frame_flags(500, 100, 9, false, false, false),
        ],
    );

    let mut args = trades_args(dir.path());
    args.days = vec!["2026-09-09".to_string()];
    let summary = run_trades(&args).unwrap();
    assert_eq!(summary.days, 1);
    assert_eq!(summary.trades, 1);

    let (header, rows) = read_rows(&summary.out);
    assert_eq!(rows.len(), 1);
    assert_eq!(col(&header, &rows[0], "day_utc"), "2026-09-09");
    assert_eq!(col(&header, &rows[0], "qty_lots"), "9");
}

/// Сверка T1: сумма `qty_lots` из ленты за окно касания 2 фикстуры
/// `touch_frames` (тик 99, продавец-агрессор) обязана совпасть с
/// `traded_during`, который трекер уровней (`replay_symbol`) посчитал
/// независимо на тех же байтах — расхождение не проходит тест (план T1:
/// «расхождение сверх округления не проходит тест»).
#[test]
fn trades_sum_matches_touch_traded_during_independently() {
    let dir = tempfile::tempdir().unwrap();
    write_day(dir.path(), "SOLUSDT", "2026-09-08", &touch_frames());

    let cfg = LevelsConfig {
        mode: H3Mode::Percentile { h3_lots: 5 },
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let replay = replay_symbol(dir.path(), "SOLUSDT", cfg).unwrap();
    let touch2 = replay.days[0]
        .touches
        .iter()
        .find(|t| t.touch_index == 2)
        .expect("касание 2 есть в фикстуре touch_frames");
    assert_eq!(touch2.price_tick, 99);
    assert_eq!(
        touch2.traded_during, 4,
        "фикстура touch_frames: сверка держится на этом числе (touches/tests.rs)"
    );

    let summary = run_trades(&trades_args(dir.path())).unwrap();
    let (header, rows) = read_rows(&summary.out);
    let from_ledger: i64 = rows
        .iter()
        .filter(|r| {
            col(&header, r, "side") == "sell"
                && col(&header, r, "price_tick") == "99"
                && col(&header, r, "exch_ts_ms").parse::<i64>().unwrap() >= touch2.start_ms
                && col(&header, r, "exch_ts_ms").parse::<i64>().unwrap() < touch2.end_ms
        })
        .map(|r| col(&header, r, "qty_lots").parse::<i64>().unwrap())
        .sum();
    assert_eq!(
        from_ledger, touch2.traded_during,
        "сумма qty_lots из ленты обязана совпасть с traded_during, посчитанным трекером уровней"
    );
}

/// Порядок имён в строке совпадает с `TRADES_COLUMNS` независимо от профиля
/// сборки (см. `row::trade_row_pair_names`, тот же приём, что `touches`).
#[test]
fn trade_row_column_names_match_header_order() {
    let rec = crate::binlog::Record {
        ev: LOCAL_BUY_TRADE_EVENT,
        exch_ts_ns: 1_000_000_000,
        local_ts_ns: 1_000_500_000,
        price_ticks: 42,
        qty_lots: 7,
        block: false,
        rpi: false,
    };
    assert_eq!(
        row::trade_row_pair_names("2026-09-08", &rec, 10_000_000),
        TRADES_COLUMNS
    );
}
