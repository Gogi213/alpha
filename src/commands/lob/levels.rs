//! `lob levels` — CLI-обёртка разметки уровней (шаги 1.1, 1.2). Сам трекер —
//! `crate::lob::levels`; реплей суточных файлов в него — общий с `markout`,
//! `watch`, `pilot` код `super::replay_symbol` (мод. `mod.rs`). Режим `H3`
//! (план D-H3, таск 02) — `H3ModeArg`/`resolve_h3_mode`/`h3_lots_for_symbol`
//! — переехал в `super` (`mod.rs`, таск 17 — общий код нескольких подкоманд
//! не может лежать в файле одной из них); эта команда просто зовёт его.
//!
//! # `debug` в шапке ниже пяти минут
//! Прогон короче окна `repeat_count` метит первую строку CSV предупреждением
//! — критерий приёмки таска 02 (`short_run_marks_csv_header_with_debug_warning`).

use std::path::PathBuf;

use clap::Args;

use crate::lob::levels::LevelsConfig;

use super::{
    death_name, outcome_name, replay_symbol, resolve_h3_mode_with_k, side_name, H3Args,
    DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS,
};

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
    /// Режим `H3`: `--h3-mode`, `--h3-lots` (общие для нескольких подкоманд).
    #[command(flatten)]
    pub h3: H3Args,
    /// Множитель относительного порога `floor` (таск 18, В-30/D05):
    /// `h3_lots = floor(k × median_trade_lots)` из `instruments.csv`
    /// (`lob pick`), а не готовая колонка `h3_lots`. Только `--h3-mode
    /// floor`; вместе с `percentile` — ошибка (`resolve_h3_mode_with_k`).
    /// Не поле `H3Args`: та структура флаттенится в `markout`/`watch`/
    /// `profiles`/`shortlist` (таск 17), а добавление обязательного (по
    /// смыслу) поля туда потребовало бы правки всех точек `H3Args {...}` в
    /// тех файлах — вне зоны и границ таска 18 («не трогать»).
    #[arg(long)]
    pub h3_k: Option<f64>,
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
    let mode = resolve_h3_mode_with_k(
        &args.root,
        &args.symbol,
        args.h3.h3_mode,
        args.h3.h3_lots,
        args.h3_k,
    )?;
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
        "rpi_lots",
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
                r.rpi_lots.to_string(),
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
mod tests;
