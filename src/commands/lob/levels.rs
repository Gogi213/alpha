//! `lob levels` — CLI-обёртка разметки уровней (шаги 1.1, 1.2). Сам трекер —
//! `crate::lob::levels`; реплей суточных файлов в него — общий с `markout`,
//! `watch`, `pilot` код `super::replay_symbol` (мод. `mod.rs`).

use std::path::PathBuf;

use clap::Args;

use crate::lob::levels::LevelsConfig;

use super::{
    death_name, outcome_name, replay_symbol, side_name, DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS,
};

// ---------------------------------------------------------------------------
// `lob levels` (шаги 1.1, 1.2).
// ---------------------------------------------------------------------------

/// Аргументы `lob levels`: читает суточные файлы, пишет уровни с шестью
/// признаками истории и классом исхода. Порог `H3` — параметр без умолчания:
/// выдуманного числа здесь быть не должно, значение предрегистрируется.
#[derive(Debug, Args)]
pub struct LevelsArgs {
    /// Корень записи: суточные файлы `<SYMBOL>-*.binlog`.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Символ, например `SOLUSDT`.
    #[arg(long)]
    pub symbol: String,
    /// Порог рождения H3 в лотах: размер строго больше.
    #[arg(long)]
    pub h3_lots: i64,
    /// Прогрев в мс: рождения раньше него отслеживаются, но не печатаются.
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
pub struct LevelsSummary {
    pub days: usize,
    pub levels: usize,
    pub out: PathBuf,
}

/// Разметка символа реплеем из `replay_symbol` — тем же кодом, что
/// подтверждающий прогон, — и запись уровней с классом в CSV.
pub fn run_levels(args: &LevelsArgs) -> anyhow::Result<LevelsSummary> {
    let cfg = LevelsConfig {
        h3_lots: args.h3_lots,
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
    let mut w = csv::Writer::from_path(&out)?;
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
            h3_lots: 5,
            warmup_ms: 0,
            repeat_window_ms: 3_600_000,
            out: None,
        }
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
}
