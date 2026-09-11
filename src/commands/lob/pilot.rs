//! `lob pilot` — CLI-обёртка пилотной цепочки 1.1 -> 1.2 -> 2.1 и гейта G0
//! (шаг 3.1). Реплей — общий с `levels`/`markout`/`watch` код в `mod.rs`;
//! издержки — `crate::lob::costs`; журнал прогонов — `crate::lob::runs`.

use std::path::PathBuf;

use clap::Args;

use crate::lob::costs::{
    mean_net_bps, Observation, MAKER_FEE_BPS, ROUNDTRIP_FEES_BPS, TAKER_FEE_BPS,
};
use crate::lob::levels::LevelsConfig;
use crate::lob::markout::{base_before, markouts_for_level, MidSample, HORIZONS_MS};
use crate::lob::runs::log_pilot_run;
use crate::lob::watch::is_c1;

use super::levels::{resolve_h3_mode, H3ModeArg};
use super::{replay_symbol, DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS, G0_MIN_PULLED};

// ---------------------------------------------------------------------------
// `lob pilot` (шаг 3.1) и гейт G0.
// ---------------------------------------------------------------------------

/// Аргументы `lob pilot`: цепочка 1.1 → 1.2 → 2.1 тем же кодом, что
/// подтверждающий прогон, сверка комиссий с H4, замер байт на запись,
/// вердикт G0 и строка в `runs.csv`. CPU и RSS — только живой замер
/// (шаг 3.1): на фикстуре печатаются как `live-only`, в вердикт не входят.
#[derive(Debug, Args)]
pub struct PilotArgs {
    /// Корень записи: суточные файлы кандидата.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Кандидат, например `SOLUSDT` — один вызов на каждого кандидата
    /// пула (см. `PickReport`/`runs.rs`), не на двух назначенных.
    #[arg(long)]
    pub symbol: String,
    /// Режим порога H3: `floor` | `percentile`, без умолчания (план D-H3,
    /// тот же выбор, что у `levels`).
    #[arg(long)]
    pub h3_mode: H3ModeArg,
    /// Порог рождения H3 в лотах: только режим `percentile` (тот же смысл,
    /// что у `levels`); `floor` берёт число из `instruments.csv`.
    #[arg(long)]
    pub h3_lots: Option<i64>,
    /// Прогрев в мс.
    #[arg(long, default_value_t = DEFAULT_WARMUP_MS)]
    pub warmup_ms: i64,
    /// Скользящее окно `repeat_count` в мс.
    #[arg(long, default_value_t = DEFAULT_REPEAT_WINDOW_MS)]
    pub repeat_window_ms: i64,
    /// Комиссия мейкера в bps: обязана совпасть с H4, иначе стоп.
    #[arg(long, default_value_t = MAKER_FEE_BPS)]
    pub maker_fee_bps: f64,
    /// Комиссия тейкера в bps: обязана совпасть с H4, иначе стоп.
    #[arg(long, default_value_t = TAKER_FEE_BPS)]
    pub taker_fee_bps: f64,
    /// Журнал прогонов (канонический путь шага 7.1).
    #[arg(long, default_value = "docs/plan/runs.csv")]
    pub runs_out: PathBuf,
    /// Момент строки журнала UTC (по умолчанию — сейчас).
    #[arg(long)]
    pub now_utc: Option<String>,
}

/// Итог `lob pilot` для печати диспетчером.
pub struct PilotSummary {
    pub symbol: String,
    pub n_pulled: u64,
    pub mean_m_10s_bps: f64,
    pub mean_net_bps: f64,
    pub verdict: String,
    pub runs_out: PathBuf,
}

/// Пилот тем же кодом, что подтверждающий прогон: реплей → `is_c1` (шаг 1.2
/// поверх 1.1) → markout на 10 с (шаг 2.1) → издержки Decision 15 на каждом
/// наблюдении отдельно. Гейт G0 дословно: ≥200 зачтённых `pulled`-уровней
/// **и** средний `m` на 10 с не ниже полных круговых издержек — 7.5 bps
/// комиссий плюс проскальзывание. Сравнение идёт через средний net
/// (`mean_net_bps >= 0`): среднее разностей равно разности средних на тех же
/// наблюдениях, а строгость к отсутствующим данным наследуется вызовом.
pub fn run_pilot(args: &PilotArgs) -> anyhow::Result<PilotSummary> {
    if args.maker_fee_bps != MAKER_FEE_BPS || args.taker_fee_bps != TAKER_FEE_BPS {
        anyhow::bail!(
            "комиссии сменились (мейкер {} тейкер {} против H4 {MAKER_FEE_BPS}/{TAKER_FEE_BPS}): предположение H4 требует перепроверки, пилот остановлен",
            args.maker_fee_bps,
            args.taker_fee_bps
        );
    }
    debug_assert_eq!(
        HORIZONS_MS[2], 10_000,
        "индекс горизонта 10 с обязан указывать на 10 000 мс"
    );
    let mode = resolve_h3_mode(&args.root, &args.symbol, args.h3_mode, args.h3_lots)?;
    let cfg = LevelsConfig {
        mode,
        warmup_ms: args.warmup_ms,
        repeat_window_ms: args.repeat_window_ms,
    };
    let replay = replay_symbol(&args.root, &args.symbol, cfg)?;
    let mut observations: Vec<Observation> = Vec::new();
    let mut m_sum = 0.0f64;
    for day in &replay.days {
        for rec in &day.records {
            if !is_c1(rec) {
                continue;
            }
            let Some(m) = markouts_for_level(rec, &day.mids)[2] else {
                continue;
            };
            let Some((base_ts, base2x)) = base_before(&day.mids, rec.death_ms) else {
                continue;
            };
            let target = base_ts.saturating_add(HORIZONS_MS[2]);
            let mut exit: Option<MidSample> = None;
            for s in &day.mids {
                if s.ts_ms <= target {
                    exit = Some(*s);
                } else {
                    break;
                }
            }
            let Some(x) = exit else { continue };
            observations.push(Observation {
                m_bps: m,
                spread_ticks_exit: x.ask_tick - x.bid_tick,
                mid2x_base: base2x,
            });
            m_sum += m;
        }
    }
    let n_pulled = crate::stats::count_u64(observations.len());
    let mean_m = if n_pulled > 0 {
        m_sum / crate::stats::count_f64_u64(n_pulled)
    } else {
        0.0
    };
    let mean_net = mean_net_bps(&observations);
    let verdict = if n_pulled < G0_MIN_PULLED {
        format!("G0 RED (sparse): зачтено {n_pulled} pulled при минимуме {G0_MIN_PULLED}")
    } else {
        match mean_net {
            Some(v) if v.is_finite() && v >= 0.0 => {
                format!("G0 pass: {n_pulled} pulled, средний net {v:.3}bps >= 0")
            }
            _ => "G0 RED: средний net ниже полных круговых издержек".to_string(),
        }
    };
    let mean_net_print = mean_net.unwrap_or(0.0);
    let bytes_per_record = if replay.records > 0 {
        crate::stats::count_f64_u64(replay.bytes) / crate::stats::count_f64_u64(replay.records)
    } else {
        0.0
    };
    let now = args
        .now_utc
        .clone()
        .unwrap_or_else(|| chrono::Utc::now().to_rfc3339());
    let detail = format!(
        "n_pulled={n_pulled} mean_m_10s_bps={mean_m:.3} mean_net_bps={mean_net_print:.3} fees_bps={ROUNDTRIP_FEES_BPS} verdict={verdict} bytes_per_record={bytes_per_record:.1} cpu=rss=live-only"
    );
    log_pilot_run(&args.runs_out, &args.symbol, &now, &detail)
        .map_err(|e| anyhow::anyhow!("runs.csv: {e}"))?;
    Ok(PilotSummary {
        symbol: args.symbol.clone(),
        n_pulled,
        mean_m_10s_bps: mean_m,
        mean_net_bps: mean_net_print,
        verdict,
        runs_out: args.runs_out.clone(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lob::runs::{read_run_rows, RunKind};

    /// Скользящая лесенка: лучший бид падает 2 тика/с, спред 2 тика.
    /// Кадры 1..=129 хоронят по 2 `pulled`-бида; ушедшие уровни несут
    /// size 0 (как пишет рекордер), иначе книга пересеклась бы. У смертей
    /// до 119 с есть будущее на 10 с — 240 зачтённых при нисходящем тренде.
    fn pilot_frames() -> Vec<Vec<crate::binlog::Record>> {
        let mut frames = Vec::new();
        for k in 0..130i64 {
            let best = 1000 - 2 * k;
            let mut bids: Vec<(i64, i64)> = (0..10).map(|j| (best - j, 10)).collect();
            let mut asks = vec![(best + 2, 10)];
            if k > 0 {
                // Ушедшие наверх тики явно удаляются нулевым размером.
                bids.push((best + 2, 0));
                bids.push((best + 1, 0));
                asks.push((best + 4, 0));
            }
            let ts = k * 1000;
            frames.push(if k == 0 {
                super::super::test_support::snap_frame(ts, &bids, &asks)
            } else {
                super::super::test_support::delta_frame(ts, &bids, &asks)
            });
        }
        frames
    }

    fn pilot_args(root: &std::path::Path) -> PilotArgs {
        PilotArgs {
            root: root.to_path_buf(),
            symbol: "SOLUSDT".to_string(),
            h3_mode: H3ModeArg::Percentile,
            h3_lots: Some(5),
            warmup_ms: 0,
            repeat_window_ms: 3_600_000,
            maker_fee_bps: 2.0,
            taker_fee_bps: 5.5,
            runs_out: root.join("runs.csv"),
            now_utc: Some("2026-09-08T00:00:00Z".to_string()),
        }
    }

    #[test]
    fn pilot_fixture_writes_verdict_and_runs_row() {
        let dir = tempfile::tempdir().unwrap();
        super::super::test_support::write_day(dir.path(), "SOLUSDT", "2026-09-08", &pilot_frames());
        let summary = run_pilot(&pilot_args(dir.path())).unwrap();
        assert_eq!(summary.n_pulled, 258);
        assert!(
            summary.verdict.starts_with("G0 pass"),
            "{}",
            summary.verdict
        );
        let rows = read_run_rows(&dir.path().join("runs.csv")).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].kind, RunKind::Pilot);
        assert_eq!(rows[0].symbol, "SOLUSDT");
        assert!(rows[0].detail.contains("G0 pass"));
    }

    #[test]
    fn pilot_rejects_changed_fees() {
        let dir = tempfile::tempdir().unwrap();
        let mut args = pilot_args(dir.path());
        args.taker_fee_bps = 6.0;
        assert!(run_pilot(&args).is_err());
    }
}
