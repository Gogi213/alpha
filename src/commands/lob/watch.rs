//! `lob watch` — CLI-обёртка счётчика n/G для C2 (шаг 4.1, Decision 21).
//! Сам счётчик и предикат годности суток — `crate::lob::watch`; реплей и
//! сбор `DayTally` — общий с `levels`/`markout`/`pilot` код в `mod.rs`.

use std::path::PathBuf;

use clap::Args;

use crate::lob::levels::LevelsConfig;
use crate::lob::watch::{progress_csv_path, WatchState};

use super::{day_tallies, replay_symbol, DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS};

// ---------------------------------------------------------------------------
// `lob watch` (шаг 4.1, Decision 21).
// ---------------------------------------------------------------------------

/// Аргументы `lob watch`: считает `n` и `G` для C2, пишет `progress.csv` и
/// выставляет `ready.flag`. Markout не вычисляет ни в каком виде: видит
/// только счётчики через `tally_day`/`WatchState`.
#[derive(Debug, Args)]
pub struct WatchArgs {
    /// Корень записи: суточные файлы, `progress.csv`, `ready.flag`.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Символ подтверждающей записи — один вызов на каждого кандидата
    /// пула (см. `PickReport`/`runs.rs`), счётчик заводится на каждого,
    /// не только на одного назначенного из двух.
    #[arg(long)]
    pub symbol: String,
    /// Порог рождения H3 в лотах (тот же, что у `levels`).
    #[arg(long)]
    pub h3_lots: i64,
    /// Прогрев в мс.
    #[arg(long, default_value_t = DEFAULT_WARMUP_MS)]
    pub warmup_ms: i64,
    /// Скользящее окно `repeat_count` в мс.
    #[arg(long, default_value_t = DEFAULT_REPEAT_WINDOW_MS)]
    pub repeat_window_ms: i64,
    /// Медиана `lifetime_ms` популяции C1 по первым суткам (Decision 16).
    #[arg(long)]
    pub median_lifetime_ms: i64,
    /// Момент выставления флага строкой UTC (по умолчанию — сейчас).
    #[arg(long)]
    pub now_utc: Option<String>,
}

/// Итог `lob watch` для печати диспетчером.
pub struct WatchSummary {
    pub days: usize,
    pub n_c2: u64,
    pub g_c2: u64,
    /// Сутки выборки через запятую, если флаг выставлен этим прогоном.
    pub flag: Option<String>,
    pub progress: PathBuf,
}

/// Суточный шаг целиком тем же кодом, что живая запись: принять сутки,
/// переписать прогресс, при срабатывании триггера выставить флаг — ровно
/// один раз (`WatchState::observe_day`).
pub fn run_watch(args: &WatchArgs) -> anyhow::Result<WatchSummary> {
    let cfg = LevelsConfig {
        h3_lots: args.h3_lots,
        warmup_ms: args.warmup_ms,
        repeat_window_ms: args.repeat_window_ms,
    };
    let replay = replay_symbol(&args.root, &args.symbol, cfg)?;
    let tallies = day_tallies(
        &args.root,
        &args.symbol,
        &replay.days,
        args.median_lifetime_ms,
    )?;
    let now = args
        .now_utc
        .clone()
        .unwrap_or_else(|| chrono::Utc::now().to_rfc3339());
    let mut state = WatchState::new(&args.symbol, args.median_lifetime_ms);
    let mut flag: Option<String> = None;
    for tally in &tallies {
        let outcome = state
            .observe_day(&args.root, tally.clone(), &now)
            .map_err(|e| anyhow::anyhow!("{e}"))?;
        if let Some(f) = outcome.flag {
            flag = Some(f.days.join(","));
        }
    }
    Ok(WatchSummary {
        days: tallies.len(),
        n_c2: state.n_c2_total(),
        g_c2: state.g_c2(),
        flag,
        progress: progress_csv_path(&args.root),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Двенадцать суток по 12 рождений на одной цене внутри часа: повторы
    /// 0..11, C2 (повтор >= 2) — 9 зачтённых в сутки, итого n=108, G=12.
    fn watch_day_frames() -> Vec<Vec<crate::binlog::Record>> {
        let mut frames = vec![super::super::test_support::snap_frame(
            0,
            &[(100, 10)],
            &[(101, 10)],
        )];
        for i in 1..=11i64 {
            frames.push(super::super::test_support::delta_frame(
                i * 180_000 - 60_000,
                &[(100, 1)],
                &[(101, 10)],
            ));
            frames.push(super::super::test_support::delta_frame(
                i * 180_000,
                &[(100, 10)],
                &[(101, 10)],
            ));
        }
        frames
    }

    #[test]
    fn watch_fixture_writes_progress_and_flag() {
        let dir = tempfile::tempdir().unwrap();
        let frames = watch_day_frames();
        for d in 0..12 {
            let day = format!("2026-09-{:02}", 8 + d);
            super::super::test_support::write_day(dir.path(), "SOLUSDT", &day, &frames);
            crate::bybit::verify_sidecar::append_verify_row(
                &crate::bybit::verify_sidecar::verify_csv_path(dir.path()),
                &crate::bybit::verify_sidecar::VerifyRow {
                    ts_utc: format!("{day}T00:05:00Z"),
                    symbol: "SOLUSDT".to_string(),
                    snapshot_seq: Some(1),
                    book_seq: Some(1),
                    mismatches: Some(0),
                    verdict: crate::bybit::verify_sidecar::VerifyVerdict::Ok,
                },
            )
            .unwrap();
        }
        let args = WatchArgs {
            root: dir.path().to_path_buf(),
            symbol: "SOLUSDT".to_string(),
            h3_lots: 5,
            warmup_ms: 0,
            repeat_window_ms: 3_600_000,
            median_lifetime_ms: 3_600_000,
            now_utc: Some("2026-09-20T00:00:00Z".to_string()),
        };
        let summary = run_watch(&args).unwrap();
        assert_eq!(summary.days, 12);
        assert_eq!(summary.n_c2, 108);
        assert_eq!(summary.g_c2, 12);
        assert!(summary.flag.is_some());
        assert!(dir.path().join("ready.flag").exists());
        let flag = crate::lob::watch::require_ready_flag(&dir.path().join("ready.flag")).unwrap();
        assert_eq!(flag.n_c2, 108);
        assert_eq!(flag.g, 12);
        let progress = std::fs::read_to_string(&summary.progress).unwrap();
        assert_eq!(progress.lines().count(), 13, "шапка плюс строка на сутки");
    }
}
