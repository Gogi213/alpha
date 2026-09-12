use super::*;
use crate::commands::lob::H3ModeArg;
use crate::commands::record::instruments_csv_path;

fn levels_args(root: &std::path::Path) -> LevelsArgs {
    LevelsArgs {
        root: root.to_path_buf(),
        symbol: "SOLUSDT".to_string(),
        h3: H3Args {
            h3_mode: H3ModeArg::Percentile,
            h3_lots: Some(5),
        },
        h3_k: None,
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        out: None,
    }
}

/// `instruments.csv` минимальный, но с настоящими колонками формата
/// (план D-H3: `h3_lots` пишет отдельный шаг, здесь — его фикстура).
fn write_instruments_csv_with_h3_lots(root: &std::path::Path, symbol: &str, h3_lots: i64) {
    std::fs::write(
        instruments_csv_path(root),
        format!(
            "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n\
             {symbol},0.01,0.1,0.1,5,{h3_lots}\n"
        ),
    )
    .unwrap();
}

#[test]
fn levels_fixture_writes_classified_levels() {
    let dir = tempfile::tempdir().unwrap();
    super::super::test_support::write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &super::super::test_support::three_level_frames(),
    );
    let summary = run_levels(&levels_args(dir.path())).unwrap();
    assert_eq!(summary.levels, 4);
    let text = std::fs::read_to_string(&summary.out).unwrap();
    for col in [
        "lifetime_ms",
        "size_max",
        "time_to_max_ms",
        "size_monotonic",
        "repeat_count",
        "repriced",
    ] {
        assert!(text.contains(col), "нет колонки {col}");
    }
    for class in ["eaten", "mixed", "pulled"] {
        assert!(text.contains(class), "нет класса {class}");
    }
}

/// Критерий приёмки таска 02: прогон короче окна `repeat_count` печатает
/// предупреждение `debug` в шапку CSV, а не молча. Фикстура — 2 секунды,
/// окно по умолчанию — час: заведомо короче.
#[test]
fn short_run_marks_csv_header_with_debug_warning() {
    let dir = tempfile::tempdir().unwrap();
    super::super::test_support::write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &super::super::test_support::three_level_frames(),
    );
    let summary = run_levels(&levels_args(dir.path())).unwrap();
    let text = std::fs::read_to_string(&summary.out).unwrap();
    let first_line = text.lines().next().unwrap();
    assert!(
        first_line.starts_with('#') && first_line.contains("debug"),
        "шапка CSV обязана нести предупреждение debug, получено: {first_line:?}"
    );
}

/// Режим `floor` (план D-H3): порог берётся из `instruments.csv`, а не
/// из `--h3-lots` — то же число, тот же результат, что у `percentile`
/// с явным порогом на этой фикстуре.
#[test]
fn floor_mode_reads_h3_lots_from_instruments_csv() {
    let dir = tempfile::tempdir().unwrap();
    super::super::test_support::write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &super::super::test_support::three_level_frames(),
    );
    write_instruments_csv_with_h3_lots(dir.path(), "SOLUSDT", 5);
    let mut args = levels_args(dir.path());
    args.h3.h3_mode = H3ModeArg::Floor;
    args.h3.h3_lots = None;
    let summary = run_levels(&args).unwrap();
    assert_eq!(
        summary.levels, 4,
        "floor с h3_lots=5 из instruments.csv обязан дать тот же результат, что percentile с --h3-lots 5"
    );
}

/// `instruments.csv` с колонкой `median_trade_lots`, без готового
/// `h3_lots` — фикстура `--h3-k` (таск 18, В-30/D05).
fn write_instruments_csv_with_median_trade_lots(
    root: &std::path::Path,
    symbol: &str,
    median_trade_lots: i64,
) {
    std::fs::write(
        instruments_csv_path(root),
        format!(
            "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots,k,median_trade_lots\n\
             {symbol},0.01,0.1,0.1,5,,,{median_trade_lots}\n"
        ),
    )
    .unwrap();
}

/// Критерий приёмки таска 18: `--h3-mode floor --h3-k <f64>` считает
/// порог как `floor(k × median_trade_lots)` из `instruments.csv` на
/// лету, не колонку `h3_lots` (пустая в этой фикстуре) — тот же
/// результат, что `floor_mode_reads_h3_lots_from_instruments_csv` даёт
/// готовой колонкой `h3_lots=5` (здесь `k=1.0 × median=5 → 5`).
#[test]
fn floor_mode_with_h3_k_computes_threshold_from_median_trade_lots() {
    let dir = tempfile::tempdir().unwrap();
    super::super::test_support::write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &super::super::test_support::three_level_frames(),
    );
    write_instruments_csv_with_median_trade_lots(dir.path(), "SOLUSDT", 5);
    let mut args = levels_args(dir.path());
    args.h3.h3_mode = H3ModeArg::Floor;
    args.h3.h3_lots = None;
    args.h3_k = Some(1.0);
    let summary = run_levels(&args).unwrap();
    assert_eq!(
        summary.levels, 4,
        "k=1.0 × median=5 обязан дать тот же порог 5, что готовая колонка h3_lots=5"
    );
}

/// Критерий приёмки таска 18: `--h3-k` вместе с `--h3-mode percentile`
/// — громкая ошибка, `k` параметризует только относительный порог
/// `floor` (В-30/D05).
#[test]
fn h3_k_with_percentile_mode_errors() {
    let dir = tempfile::tempdir().unwrap();
    super::super::test_support::write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &super::super::test_support::three_level_frames(),
    );
    let mut args = levels_args(dir.path());
    args.h3.h3_mode = H3ModeArg::Percentile;
    args.h3.h3_lots = Some(5);
    args.h3_k = Some(2.0);
    let err = run_levels(&args).unwrap_err();
    assert!(
        err.to_string().contains("h3-k") || err.to_string().contains("percentile"),
        "сообщение обязано указать на конфликт --h3-k/--h3-mode percentile: {err}"
    );
}

/// Критерий приёмки: без `instruments.csv` режим `floor` — ненулевой код
/// (здесь — `Err`, диспетчер превращает его в код выхода), а не молчаливый
/// ноль уровней.
#[test]
fn floor_mode_without_instruments_csv_errors() {
    let dir = tempfile::tempdir().unwrap();
    super::super::test_support::write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &super::super::test_support::three_level_frames(),
    );
    let mut args = levels_args(dir.path());
    args.h3.h3_mode = H3ModeArg::Floor;
    args.h3.h3_lots = None;
    let err = run_levels(&args).unwrap_err();
    assert!(
        err.to_string().contains("h3_lots") || err.to_string().contains("instruments.csv"),
        "сообщение обязано указывать на отсутствие instruments.csv/h3_lots: {err}"
    );
}

/// Колонка `h3_lots` есть в схеме файла (пишет отдельный шаг), но не в
/// этой фикстуре — та же понятная ошибка, а не паника на разборе CSV.
#[test]
fn floor_mode_without_h3_lots_column_errors() {
    let dir = tempfile::tempdir().unwrap();
    super::super::test_support::write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &super::super::test_support::three_level_frames(),
    );
    std::fs::write(
        instruments_csv_path(dir.path()),
        "symbol,tick_size,min_order_qty,qty_step,min_notional_value\nSOLUSDT,0.01,0.1,0.1,5\n",
    )
    .unwrap();
    let mut args = levels_args(dir.path());
    args.h3.h3_mode = H3ModeArg::Floor;
    args.h3.h3_lots = None;
    let err = run_levels(&args).unwrap_err();
    assert!(
        err.to_string().contains("h3_lots"),
        "сообщение обязано назвать недостающую колонку: {err}"
    );
}

/// Критерий приёмки: режим `percentile` без `--h3-lots` — ненулевой код:
/// порог измеряется заранее, а не подставляется правдоподобным нулём.
#[test]
fn percentile_mode_without_h3_lots_errors() {
    let dir = tempfile::tempdir().unwrap();
    super::super::test_support::write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &super::super::test_support::three_level_frames(),
    );
    let mut args = levels_args(dir.path());
    args.h3.h3_lots = None;
    let err = run_levels(&args).unwrap_err();
    assert!(
        err.to_string().contains("h3-lots") || err.to_string().contains("percentile"),
        "сообщение обязано указать на недостающий --h3-lots: {err}"
    );
}
