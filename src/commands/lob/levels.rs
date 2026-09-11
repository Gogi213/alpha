//! `lob levels` — CLI-обёртка разметки уровней (шаги 1.1, 1.2). Сам трекер —
//! `crate::lob::levels`; реплей суточных файлов в него — общий с `markout`,
//! `watch`, `pilot` код `super::replay_symbol` (мод. `mod.rs`). Режим `H3`
//! (план D-H3, таск 02) собран здесь: `H3ModeArg`, разбор `instruments.csv`
//! для `floor` и `resolve_h3_mode` — `markout`/`pilot`/`watch` переиспользуют
//! их через `super::levels`, чтобы не дублировать чтение файла трижды.

use std::path::{Path, PathBuf};

use clap::Args;

use crate::commands::record::instruments_csv_path;
use crate::lob::levels::{H3Mode, LevelsConfig};

use super::{
    death_name, outcome_name, replay_symbol, side_name, DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS,
};

// ---------------------------------------------------------------------------
// Режим `H3`: флаг без умолчания (план D-H3) + чтение пола `floor` из
// `instruments.csv`. Общее для `levels`, `markout`, `pilot`, `watch`.
// ---------------------------------------------------------------------------

/// Режим порога `H3`, флагом CLI. Явный выбор — умолчания нет: какой режим
/// входит в предрегистрацию, решает двухчасовой пилот, не эта команда.
#[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
pub enum H3ModeArg {
    /// Пол в лотах из `instruments.csv`, без прогрева — режим отладки.
    Floor,
    /// 99-й перцентиль по скользящему часу, прогрев 60 мин — прежнее
    /// определение; порог измерен заранее и приходит через `--h3-lots`.
    Percentile,
}

/// Строка `instruments.csv`, нужная режиму `floor`: символ и колонка
/// `h3_lots` (пишет отдельный шаг сборки пула, план D-H3). `h3_lots` читается
/// строкой — у большинства символов колонка сейчас пуста, парсинг в целое
/// откладывается до найденной строки нужного символа.
#[derive(Debug, serde::Deserialize)]
struct H3FloorRow {
    symbol: String,
    h3_lots: String,
}

/// Пол `H3` символа из `instruments.csv`: нет файла, нет колонки, нет
/// символа или значение не положительное — понятная ошибка с ненулевым
/// кодом выхода, а не молчаливый ноль (критерий приёмки таска 02).
fn h3_lots_for_symbol(instruments_csv: &Path, symbol: &str) -> anyhow::Result<i64> {
    let mut r = csv::Reader::from_path(instruments_csv).map_err(|e| {
        anyhow::anyhow!(
            "{}: {e} — режиму floor нужен instruments.csv с колонкой h3_lots \
             (пишет отдельный шаг сборки пула)",
            instruments_csv.display()
        )
    })?;
    let headers = r.headers()?.clone();
    anyhow::ensure!(
        headers.iter().any(|h| h == "h3_lots"),
        "{}: нет колонки h3_lots (пишет отдельный шаг сборки пула)",
        instruments_csv.display()
    );
    for row in r.deserialize::<H3FloorRow>() {
        let row = row?;
        if row.symbol != symbol {
            continue;
        }
        let raw = row.h3_lots.trim();
        let v: i64 = raw
            .parse()
            .map_err(|_| anyhow::anyhow!("{symbol}: h3_lots {raw:?} в instruments.csv не целое"))?;
        anyhow::ensure!(
            v > 0,
            "{symbol}: h3_lots обязан быть положителен, получено {v}"
        );
        return Ok(v);
    }
    anyhow::bail!(
        "{symbol}: нет строки в {} (режим floor)",
        instruments_csv.display()
    );
}

/// Режим `H3` из флага: `floor` читает пол из `instruments.csv` корня
/// записи, `percentile` берёт заранее измеренный порог из `--h3-lots`
/// (обязателен в этом режиме — измерение вне этой команды).
pub fn resolve_h3_mode(
    root: &Path,
    symbol: &str,
    mode: H3ModeArg,
    h3_lots: Option<i64>,
) -> anyhow::Result<H3Mode> {
    match mode {
        H3ModeArg::Floor => Ok(H3Mode::Floor {
            h3_lots: h3_lots_for_symbol(&instruments_csv_path(root), symbol)?,
        }),
        H3ModeArg::Percentile => {
            let h3_lots = h3_lots.ok_or_else(|| {
                anyhow::anyhow!(
                    "--h3-lots обязателен в режиме percentile: порог измеряется заранее"
                )
            })?;
            Ok(H3Mode::Percentile { h3_lots })
        }
    }
}

// ---------------------------------------------------------------------------
// `lob levels` (шаги 1.1, 1.2).
// ---------------------------------------------------------------------------

/// Аргументы `lob levels`: читает суточные файлы, пишет уровни с шестью
/// признаками истории и классом исхода. Режим `H3` — параметр без умолчания:
/// выдуманного числа здесь быть не должно, значение предрегистрируется.
#[derive(Debug, Args)]
pub struct LevelsArgs {
    /// Корень записи: суточные файлы `<SYMBOL>-*.binlog`.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Символ, например `SOLUSDT`.
    #[arg(long)]
    pub symbol: String,
    /// Режим порога H3: `floor` | `percentile`, без умолчания (план D-H3).
    #[arg(long)]
    pub h3_mode: H3ModeArg,
    /// Порог рождения H3 в лотах: только режим `percentile` — заранее
    /// измеренный 99-й перцентиль. `floor` берёт число из `instruments.csv`
    /// и этот флаг игнорирует.
    #[arg(long)]
    pub h3_lots: Option<i64>,
    /// Прогрев в мс: только режим `percentile`; `floor` не читает.
    #[arg(long, default_value_t = DEFAULT_WARMUP_MS)]
    pub warmup_ms: i64,
    /// Скользящее окно `repeat_count` в мс.
    #[arg(long, default_value_t = DEFAULT_REPEAT_WINDOW_MS)]
    pub repeat_window_ms: i64,
    /// Куда писать уровни (по умолчанию `<root>/levels-<symbol>.csv`).
    #[arg(long)]
    pub out: Option<PathBuf>,
}

/// Итог `lob levels` для печати диспетчером.
#[derive(Debug)]
pub struct LevelsSummary {
    pub days: usize,
    pub levels: usize,
    pub out: PathBuf,
}

/// Длина суток от первого до последнего среза середины — прокси на длину
/// прогона, которым мерялось окно `repeat_count`. Без срезов (файл вообще
/// без обновлений книги) длина ноль — сутки короче любого положительного окна.
fn day_span_ms(day: &super::ReplayDay) -> i64 {
    match (day.mids.first(), day.mids.last()) {
        (Some(a), Some(b)) => b.ts_ms - a.ts_ms,
        _ => 0,
    }
}

/// Разметка символа реплеем из `replay_symbol` — тем же кодом, что
/// подтверждающий прогон, — и запись уровней с классом в CSV. Прогон короче
/// окна `repeat_count` печатает предупреждение `debug` в stderr и в шапку
/// CSV: `repeat_count` в таких сутках занижен, данными это не является
/// (критерий приёмки таска 02).
pub fn run_levels(args: &LevelsArgs) -> anyhow::Result<LevelsSummary> {
    let mode = resolve_h3_mode(&args.root, &args.symbol, args.h3_mode, args.h3_lots)?;
    let cfg = LevelsConfig {
        mode,
        warmup_ms: args.warmup_ms,
        repeat_window_ms: args.repeat_window_ms,
    };
    let replay = replay_symbol(&args.root, &args.symbol, cfg)?;
    let out = args
        .out
        .clone()
        .unwrap_or_else(|| args.root.join(format!("levels-{}.csv", args.symbol)));
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let short_span_ms = replay.days.iter().map(day_span_ms).min();
    let debug_warning = short_span_ms
        .filter(|&span| span < args.repeat_window_ms)
        .map(|span| {
            format!(
                "lob levels: debug — прогон короче окна repeat_count ({span} мс < {} мс), \
             repeat_count занижен, данными не является",
                args.repeat_window_ms
            )
        });
    if let Some(msg) = &debug_warning {
        eprintln!("{msg}");
    }

    let mut file = std::fs::File::create(&out)?;
    if let Some(msg) = &debug_warning {
        use std::io::Write as _;
        writeln!(file, "# {msg}")?;
    }
    let mut w = csv::Writer::from_writer(file);
    w.write_record([
        "day_utc",
        "side",
        "price_tick",
        "birth_ms",
        "death_ms",
        "lifetime_ms",
        "size_max",
        "time_to_max_ms",
        "size_monotonic",
        "repeat_count",
        "repriced",
        "death",
        "traded_lots",
        "outcome",
    ])?;
    let mut n = 0usize;
    for day in &replay.days {
        for r in &day.records {
            w.write_record([
                day.day.clone(),
                side_name(r.side).to_string(),
                r.price_tick.to_string(),
                r.birth_ms.to_string(),
                r.death_ms.to_string(),
                r.lifetime_ms.to_string(),
                r.size_max.to_string(),
                r.time_to_max_ms.to_string(),
                r.size_monotonic.to_string(),
                r.repeat_count.to_string(),
                r.repriced.to_string(),
                death_name(r.death).to_string(),
                r.traded_lots.to_string(),
                outcome_name(r.outcome()).to_string(),
            ])?;
            n += 1;
        }
    }
    w.flush()?;
    Ok(LevelsSummary {
        days: replay.days.len(),
        levels: n,
        out,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn levels_args(root: &std::path::Path) -> LevelsArgs {
        LevelsArgs {
            root: root.to_path_buf(),
            symbol: "SOLUSDT".to_string(),
            h3_mode: H3ModeArg::Percentile,
            h3_lots: Some(5),
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
        args.h3_mode = H3ModeArg::Floor;
        args.h3_lots = None;
        let summary = run_levels(&args).unwrap();
        assert_eq!(
            summary.levels, 4,
            "floor с h3_lots=5 из instruments.csv обязан дать тот же результат, что percentile с --h3-lots 5"
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
        args.h3_mode = H3ModeArg::Floor;
        args.h3_lots = None;
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
        args.h3_mode = H3ModeArg::Floor;
        args.h3_lots = None;
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
        args.h3_lots = None;
        let err = run_levels(&args).unwrap_err();
        assert!(
            err.to_string().contains("h3-lots") || err.to_string().contains("percentile"),
            "сообщение обязано указать на недостающий --h3-lots: {err}"
        );
    }
}
