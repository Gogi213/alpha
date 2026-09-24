//! Запись CSV: сводка бэктеста (`BACKTEST_HEADER`/`BacktestRow`/
//! `write_backtest_csv`), покруговой дамп `--trades-out`
//! (`write_trades_csv`), причина выхода словами (`exit_reason_label`, тот же
//! словарь, что `lob bounce-verdict`, используется и `lob bounce-grid`) и
//! кривая PnL (`PNL_HEADER`/`PnlRow`/`write_pnl_csv`). Вынесено из
//! `backtest` при разрезке B3 (ревью 23.09), поведение не менялось.

use std::io::Write as _;
use std::path::Path;

use crate::lob::backtest::{pnl_curve_bps, roundtrip_net_bps, BacktestReport, BounceRun};

const BACKTEST_HEADER: [&str; 15] = [
    "profile_id",
    "rtt",
    "signals",
    "fills",
    "missed_timeout",
    "missed_busy",
    "incomplete",
    "mean_net_bps",
    "fill",
    "net_fill",
    "net_fill_lower",
    "table_net_bps",
    "table_net_fill_bps",
    "diff_net_fill_bps",
    "g4",
];

#[derive(Debug, Clone, serde::Serialize)]
struct BacktestRow {
    profile_id: String,
    rtt: &'static str,
    signals: u64,
    fills: usize,
    missed_timeout: u64,
    missed_busy: u64,
    incomplete: bool,
    mean_net_bps: Option<f64>,
    fill: Option<f64>,
    net_fill: Option<f64>,
    net_fill_lower: Option<f64>,
    table_net_bps: Option<f64>,
    table_net_fill_bps: Option<f64>,
    diff_net_fill_bps: Option<f64>,
    g4: String,
}

pub(super) fn write_backtest_csv(
    path: &Path,
    report: &BacktestReport,
    header: &str,
) -> anyhow::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut file = std::fs::File::create(path)?;
    writeln!(file, "{header}")?;
    let mut w = csv::WriterBuilder::new()
        .has_headers(false)
        .from_writer(file);
    w.write_record(BACKTEST_HEADER)?;
    for p in &report.profiles {
        for (rtt, run) in [("median", &p.median), ("p95", &p.p95)] {
            w.serialize(BacktestRow {
                profile_id: p.profile_id.clone(),
                rtt,
                signals: run.signals,
                fills: run.n_fills(),
                missed_timeout: run.misses.timeout,
                missed_busy: run.misses.busy,
                incomplete: run.incomplete,
                mean_net_bps: run.mean_net_bps(),
                fill: run.fill_rate(),
                net_fill: run.net_fill_bps(),
                net_fill_lower: run.net_fill_interval().map(|iv| iv.lower_bps),
                table_net_bps: p.comparison.table_net_bps,
                table_net_fill_bps: p.comparison.table_net_fill_bps,
                diff_net_fill_bps: p.comparison.diff_net_fill_bps,
                g4: p.g4.to_string(),
            })?;
        }
    }
    w.flush()?;
    Ok(())
}

const PNL_HEADER: [&str; 4] = ["profile_id", "rtt", "step_index", "cum_net_bps"];

#[derive(Debug, Clone, serde::Serialize)]
struct PnlRow {
    profile_id: String,
    rtt: &'static str,
    step_index: usize,
    cum_net_bps: f64,
}

/// Покруговой дамп прогона `--touches` (`--trades-out`): одна строка на
/// закрытый круг профиля «все». Порядок строк — порядок исполнения (тот же,
/// что у кривой PnL), поэтому ряд пригоден и для Шарпа, и для кумулятивной
/// суммы. `net_bps` не посчитался — литерал `not_measured`, не пустая строка
/// и не ноль: «нет числа» и «ноль» — разные вещи (то же правило, что у
/// `TableEstimate`), а потребитель (`lob bounce-verdict`) на литерале
/// отказывает, а не молча выкидывает круг.
pub(super) fn write_trades_csv(path: &Path, header: &str, run: &BounceRun) -> anyhow::Result<()> {
    anyhow::ensure!(
        run.fill_reason.len() == run.fills.len() && run.fill_signal.len() == run.fills.len(),
        "кругов {}, причин выхода {}, сигналов {}: дамп не пишется из несогласованного прогона",
        run.fills.len(),
        run.fill_reason.len(),
        run.fill_signal.len()
    );
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut file = std::fs::File::create(path)?;
    writeln!(file, "{header}")?;
    let mut w = csv::WriterBuilder::new()
        .has_headers(false)
        .from_writer(file);
    w.write_record([
        "signal_index",
        "dir",
        "entry_px",
        "exit_px",
        "qty",
        "net_bps",
        "reason",
    ])?;
    for (i, fill) in run.fills.iter().enumerate() {
        let net = match roundtrip_net_bps(fill) {
            Some(v) => format!("{v:.6}"),
            None => "not_measured".to_string(),
        };
        w.write_record([
            run.fill_signal[i].to_string(),
            fill.dir.to_string(),
            format!("{:.10}", fill.entry_px),
            format!("{:.10}", fill.exit_px),
            format!("{:.10}", fill.qty),
            net,
            exit_reason_label(run.fill_reason[i]).to_string(),
        ])?;
    }
    w.flush()?;
    Ok(())
}

/// Причина выхода словами — тот же словарь, что `lob bounce-verdict` и
/// `EXIT_REASONS`: одна форма имени на писателя и читателя, чтобы отчёт не
/// собирался из двух разных написаний одной причины.
pub(crate) fn exit_reason_label(reason: crate::lob::strategy::ExitReason) -> &'static str {
    use crate::lob::strategy::ExitReason;
    match reason {
        ExitReason::Stop => "stop",
        ExitReason::Take => "take",
        ExitReason::Trail => "trail",
        ExitReason::Deadline => "deadline",
        ExitReason::Early => "early",
        ExitReason::Horizon => "horizon",
        ExitReason::Eaten => "eaten",
        ExitReason::EatenByTrades => "eaten_by_trades",
        ExitReason::WallGone => "wall_gone",
    }
}

pub(super) fn write_pnl_csv(
    path: &Path,
    report: &BacktestReport,
    header: &str,
) -> anyhow::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut file = std::fs::File::create(path)?;
    writeln!(file, "{header}")?;
    let mut w = csv::WriterBuilder::new()
        .has_headers(false)
        .from_writer(file);
    // Заголовок пишется всегда, даже когда ни один профиль не дал ни
    // одного заполнения — координатор: «pnl.csv всегда с заголовком».
    w.write_record(PNL_HEADER)?;
    for p in &report.profiles {
        for (rtt, run) in [("median", &p.median), ("p95", &p.p95)] {
            if let Some(curve) = pnl_curve_bps(&run.fills) {
                for (step_index, cum_net_bps) in curve.into_iter().enumerate() {
                    w.serialize(PnlRow {
                        profile_id: p.profile_id.clone(),
                        rtt,
                        step_index,
                        cum_net_bps,
                    })?;
                }
            }
        }
    }
    w.flush()?;
    Ok(())
}
