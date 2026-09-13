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
//! на горизонте), не ноль. `frontrun_lots` — за секунду до касания,
//! `swept_lots` — на последнем кадре до него (В-45); `within_touch_<h>` —
//! горизонт не длиннее касания (`markout::within_touch`): `m_<h>` там ≈ 0
//! по построению, читателю средних такую ячейку надо пропустить.

use std::path::PathBuf;

use clap::Args;

use crate::lob::levels::LevelsConfig;
use crate::lob::markout::{
    approaches_for_touch, distance_bps_at_birth, markouts_for_touch, within_touch, APPROACH_MS,
    HORIZONS_MS,
};

use super::{
    outcome_name, replay_symbol, resolve_h3_mode_with_k, side_name, some_or_empty, H3Args,
    DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS,
};
use crate::lob::moves::{find_pairs, histogram, quantiles};

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
    /// Заодно измерить **переезды плотностей** (T42, В-46) и записать пары
    /// «смерть → рождение» в этот CSV. Метка `moved` не ставится: измеряется
    /// распределение, границы назначаются отдельным решением.
    #[arg(long)]
    pub moves: Option<PathBuf>,
    /// Окно поиска рождения для `--moves`, мс. Без умолчания: это параметр
    /// измерения, а не константа кода (В-46), и он печатается в шапке.
    #[arg(long)]
    pub moves_window_ms: Option<i64>,
    /// Шаг гистограммы `dt_ms` для `--moves`, мс (без него гистограмма не
    /// печатается: шаг — параметр, а не назначенное число).
    #[arg(long)]
    pub moves_bin_ms: Option<f64>,
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
const TOUCHES_WIDTH: usize = 28;

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
    "swept_lots",
    "round_zeros",
    "ended_by_death",
    "stack_levels",
    "dist_bps",
    "m_100ms",
    "m_1s",
    "m_10s",
    "m_60s",
    "within_touch_100ms",
    "within_touch_1s",
    "within_touch_10s",
    "within_touch_60s",
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
            let inside = within_touch(t.duration_ms);
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
                t.swept_lots.to_string(),
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
                inside[0].to_string(),
                inside[1].to_string(),
                inside[2].to_string(),
                inside[3].to_string(),
                some_or_empty(ap[0]),
                some_or_empty(ap[1]),
            ];
            w.write_record(row)?;
            n += 1;
        }
    }
    w.flush()?;

    // Переезды плотностей (T42, В-46) — отдельным файлом: окно поиска
    // приходит флагом и печатается, метка `moved` не ставится.
    if let Some(path) = &args.moves {
        let Some(window_ms) = args.moves_window_ms else {
            anyhow::bail!("--moves требует --moves-window-ms: окно поиска — параметр измерения, не константа (В-46)");
        };
        anyhow::ensure!(window_ms > 0, "--moves-window-ms обязан быть положителен");
        // Сутки склеиваются в один поток: пара через полночь UTC — такая же
        // пара, как внутри суток (критерий приёмки T42).
        let mut records = Vec::new();
        let mut touches_all = Vec::new();
        let mut mids = Vec::new();
        for day in &replay.days {
            records.extend(day.records.iter().copied());
            touches_all.extend(day.touches.iter().copied());
            mids.extend(day.mids.iter().copied());
        }
        mids.sort_by_key(|s| s.ts_ms);
        let pairs = find_pairs(&records, &touches_all, &mids, window_ms);
        if let Some(parent) = path.parent() {
            if !parent.as_os_str().is_empty() {
                std::fs::create_dir_all(parent)?;
            }
        }
        let mut mw = csv::Writer::from_path(path)?;
        mw.write_record([
            "side",
            "dt_ms",
            "dp_ticks",
            "size_ratio",
            "zeros_from",
            "zeros_to",
            "mid_10s_bps",
            "mid_60s_bps",
            "touched",
            "outcome_to",
        ])?;
        for p in &pairs {
            mw.write_record([
                side_name(p.side).to_string(),
                p.dt_ms.to_string(),
                p.dp_ticks.to_string(),
                some_or_empty(p.size_ratio),
                p.zeros_from.to_string(),
                p.zeros_to.to_string(),
                some_or_empty(p.mid_10s_bps),
                some_or_empty(p.mid_60s_bps),
                p.touched.to_string(),
                p.outcome_to.map(outcome_name).unwrap_or("").to_string(),
            ])?;
        }
        mw.flush()?;
        println!(
            "moves: {} {} пар за {} мс окна (сняли → родилось; рядом {:.1} %), ноль-Δp {:.1} %, коснуты {:.1} % → {}",
            args.symbol,
            pairs.len(),
            window_ms,
            100.0 * pairs.iter().filter(|p| p.dp_ticks == 0).count() as f64
                / pairs.len().max(1) as f64,
            100.0
                * pairs
                    .iter()
                    .filter(|p| p.dp_ticks.abs() <= 1)
                    .count() as f64
                / pairs.len().max(1) as f64,
            100.0 * pairs.iter().filter(|p| p.touched).count() as f64 / pairs.len().max(1) as f64,
            path.display()
        );
        let dts: Vec<f64> = pairs.iter().map(|p| p.dt_ms as f64).collect();
        let dps: Vec<f64> = pairs
            .iter()
            .map(|p| p.dp_ticks.unsigned_abs() as f64)
            .collect();
        let ratios: Vec<f64> = pairs.iter().filter_map(|p| p.size_ratio).collect();
        if let Some((a, b, c)) = quantiles(&dts) {
            println!("moves: Δt, мс — p10 {a:.0} · p50 {b:.0} · p90 {c:.0}");
        }
        if let Some((a, b, c)) = quantiles(&dps) {
            println!("moves: |Δp|, тиков — p10 {a:.0} · p50 {b:.0} · p90 {c:.0}");
        }
        if let Some((a, b, c)) = quantiles(&ratios) {
            println!("moves: размер новый/старый — p10 {a:.2} · p50 {b:.2} · p90 {c:.2}");
        }
        if let Some(width) = args.moves_bin_ms {
            let h = histogram(&dts, width);
            let text: Vec<String> = h.iter().map(|(x, n)| format!("{x:.0}мс:{n}")).collect();
            println!(
                "moves: Δt гистограмма шагом {width:.0} мс — {}",
                text.join(" ")
            );
        }
    }

    Ok(TouchesSummary {
        days: replay.days.len(),
        touches: n,
        out,
    })
}

#[cfg(test)]
mod tests;
