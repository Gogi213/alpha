//! `lob touches` — CLI-обёртка касаний живых уровней (таск 35, В-42).
//! Сам учёт касаний — `crate::lob::levels` (`TouchRecord`,
//! `LevelTracker::observe_frame_with_touches`), markout и подход —
//! `crate::lob::markout` (`markouts_for_touch`, `approaches_for_touch`);
//! реплей суточных файлов — общий с `levels`/`markout` `super::replay_symbol`
//! (`ReplayDay.touches`). Здесь только флаги, CSV и код выхода.
//!
//! Колонки: запись касания как есть (`birth_ms` — рождение уровня, как в
//! `levels-*.csv`) плюс `age_ms` (возраст уровня на момент касания),
//! `dist_bps` (расстояние уровня до середины при его рождении — один расчёт с
//! осью `distance` у `profiles`, `markout::distance_bps_at_birth`), markout на
//! четырёх горизонтах `HORIZONS_MS` от среза как есть на `start_ms` (В-43) со
//! знаком «в сторону отскока», подход за `APPROACH_MS` (1 с, 10 с) со знаком
//! «к уровню». Пустая ячейка — нет данных (нет среза на момент касания или
//! на горизонте), не ноль.

use std::path::PathBuf;

use clap::Args;

use crate::lob::levels::LevelsConfig;
use crate::lob::markout::{
    approaches_for_touch, distance_bps_at_birth, markouts_for_touch, APPROACH_MS, HORIZONS_MS,
};

use super::{
    replay_symbol, resolve_h3_mode_with_k, side_name, some_or_empty, H3Args,
    DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS,
};

/// Аргументы `lob touches`: читает суточные файлы, пишет касания живых
/// уровней с признаками практиков. Режим `H3` — без умолчания, как у
/// `levels`; `--h3-k` — тот же пересчёт пола на лету (таск 18).
#[derive(Debug, Args)]
pub struct TouchesArgs {
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
    /// `h3_lots = floor(k × median_trade_lots)` из `instruments.csv`.
    /// Только `--h3-mode floor`.
    #[arg(long)]
    pub h3_k: Option<f64>,
    /// Прогрев в мс: только режим `percentile`; `floor` не читает.
    #[arg(long, default_value_t = DEFAULT_WARMUP_MS)]
    pub warmup_ms: i64,
    /// Скользящее окно `repeat_count` в мс (нужно трекеру, в касания не пишется).
    #[arg(long, default_value_t = DEFAULT_REPEAT_WINDOW_MS)]
    pub repeat_window_ms: i64,
    /// Куда писать касания (по умолчанию `<root>/touches-<symbol>.csv`).
    #[arg(long)]
    pub out: Option<PathBuf>,
}

/// Итог `lob touches` для печати диспетчером.
#[derive(Debug)]
pub struct TouchesSummary {
    pub days: usize,
    pub touches: usize,
    pub out: PathBuf,
}

/// Ширина строки CSV — один источник арности для заголовка и строки:
/// расхождение не компилируется.
const TOUCHES_WIDTH: usize = 23;

/// Заголовок CSV: запись касания как есть, затем производные. `birth_ms` —
/// как в `levels-*.csv`/`markout-*.csv`, для джойна по (сторона, тик,
/// рождение).
pub(crate) const TOUCHES_COLUMNS: [&str; TOUCHES_WIDTH] = [
    "day_utc",
    "side",
    "price_tick",
    "touch_index",
    "start_ms",
    "end_ms",
    "duration_ms",
    "birth_ms",
    "age_ms",
    "size_at_touch",
    "size_max_before",
    "traded_during",
    "frontrun_lots",
    "round_zeros",
    "ended_by_death",
    "stack_levels",
    "dist_bps",
    "m_100ms",
    "m_1s",
    "m_10s",
    "m_60s",
    "approach_1s",
    "approach_10s",
];

/// Реплей символа тем же `replay_symbol`, что `levels`/`markout`, и запись
/// касаний в CSV. Порядок строк — порядок выдачи трекера внутри суток.
pub fn run_touches(args: &TouchesArgs) -> anyhow::Result<TouchesSummary> {
    debug_assert_eq!(
        HORIZONS_MS,
        [100, 1_000, 10_000, 60_000],
        "порядок колонок m_* обязан совпадать с горизонтами"
    );
    debug_assert_eq!(
        APPROACH_MS,
        [1_000, 10_000],
        "порядок колонок approach_* обязан совпадать с окнами подхода"
    );
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
        .unwrap_or_else(|| args.root.join(format!("touches-{}.csv", args.symbol)));
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut w = csv::Writer::from_path(&out)?;
    w.write_record(TOUCHES_COLUMNS)?;
    let mut n = 0usize;
    for day in &replay.days {
        for t in &day.touches {
            let ms = markouts_for_touch(t, &day.mids);
            let ap = approaches_for_touch(t, &day.mids);
            let row: [String; TOUCHES_WIDTH] = [
                day.day.clone(),
                side_name(t.side).to_string(),
                t.price_tick.to_string(),
                t.touch_index.to_string(),
                t.start_ms.to_string(),
                t.end_ms.to_string(),
                t.duration_ms.to_string(),
                t.level_birth_ms.to_string(),
                t.age_ms().to_string(),
                t.size_at_touch.to_string(),
                t.size_max_before.to_string(),
                t.traded_during.to_string(),
                t.frontrun_lots.to_string(),
                t.round_zeros.to_string(),
                t.ended_by_death.to_string(),
                t.stack_levels.to_string(),
                some_or_empty(distance_bps_at_birth(
                    &day.mids,
                    t.level_birth_ms,
                    t.price_tick,
                )),
                some_or_empty(ms[0]),
                some_or_empty(ms[1]),
                some_or_empty(ms[2]),
                some_or_empty(ms[3]),
                some_or_empty(ap[0]),
                some_or_empty(ap[1]),
            ];
            w.write_record(row)?;
            n += 1;
        }
    }
    w.flush()?;
    Ok(TouchesSummary {
        days: replay.days.len(),
        touches: n,
        out,
    })
}

#[cfg(test)]
mod tests;
