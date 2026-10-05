//! Драйвер ячеек на общей ленте (TK-049, шаг г): каждая ячейка — одна форма (цепочка сигналов суток), её
//! круги — окна сигналов, но все ячейки идут по ОДНОЙ ленте и общим книгам (`MultiEngine`). Результат
//! ячейки обязан быть тем же, что у `drive_bounce_windowed` (гейт — тест и `bounce-grid`).
//! Только `Prob` (частичное исполнение): круг движка — `PartialFillExchange`.

use super::shared_depth::SharedDepth;
use super::shared_multi::{CircleCtx, MultiEngine};
use super::*;
use hftbacktest::backtest::models::PowerProbQueueFunc;

type Qm = ProbQueueModel<PowerProbQueueFunc, SharedDepth>;
type Fm = TradingValueFeeModel<CommonFees>;
type Ctx = CircleCtx<LinearAsset, MeasuredLatency, Qm, Fm>;

/// Одна ячейка: сигналы формы суток и настройки прогона.
pub struct SharedCell {
    pub signals: Vec<BounceSignal>,
    pub cfg: DriveConfig,
}

/// Прогнать ячейки по одной ленте суток; результаты — в порядке `cells`.
pub fn drive_cells_shared(
    rows: Vec<Event>,
    tick_size: f64,
    lot_size: f64,
    exec_latency: ExecLatency,
    queue_n: f64,
    cells: Vec<SharedCell>,
) -> Result<Vec<BounceRun>, BacktestError> {
    let mut eng: MultiEngine<
        LinearAsset,
        MeasuredLatency,
        Qm,
        Fm,
        Result<BounceRun, BacktestError>,
    > = MultiEngine::new(
        rows,
        SharedDepth::new_leader(FastMarketDepth::new(tick_size, lot_size)),
        SharedDepth::new_leader(FastMarketDepth::new(tick_size, lot_size)),
    );
    let n = cells.len();
    for cell in cells {
        let fees = TradingValueFeeModel::new(CommonFees::new(
            MAKER_FEE_BPS / 10_000.0,
            TAKER_FEE_BPS / 10_000.0,
        ));
        eng.add_cell(
            LinearAsset::new(1.0),
            fees,
            MeasuredLatency(exec_latency),
            Box::new(move || ProbQueueModel::new(PowerProbQueueFunc::new(queue_n))),
            LAST_TRADES_CAPACITY,
            move |mut ctx: Ctx| {
                drive_bounce_with::<Ctx, SharedDepth, _>(
                    0,
                    &cell.signals,
                    &cell.cfg,
                    None,
                    |sig, _attempt, step| {
                        if !ctx.rebirth(sig.t0_ns) {
                            return Ok(None);
                        }
                        let data_end = ctx.data_end();
                        let s = if ctx.elapse(0)? == ElapseResult::EndOfData {
                            SignalStep::EndOfData
                        } else {
                            step(&mut ctx, data_end)?
                        };
                        Ok(Some((s, true)))
                    },
                )
            },
        )
        .map_err(|_| BacktestError::DataError(std::io::Error::other("стек круга")))?;
    }
    let mut res = eng.run()?;
    res.sort_by_key(|(i, _)| *i);
    let mut out = Vec::with_capacity(n);
    for (_, r) in res {
        out.push(r?);
    }
    Ok(out)
}

/// Окна сигналов клетки в строках ленты: `[строка t0, строка t0 + горизонт круга)` — то, что пройдёт окон-путь
/// (сверху: круг обычно выходит раньше горизонта). Строки упорядочены по `local_ts`, поиск — двоичный.
pub fn window_row_spans<R: EventRows + ?Sized>(
    rows: &R,
    signals: &[BounceSignal],
    out: &mut Vec<(usize, usize)>,
) {
    let lower = |t: i64| -> usize {
        let (mut lo, mut hi) = (0usize, rows.len());
        while lo < hi {
            let mid = (lo + hi) / 2;
            if rows.row_local_ts(mid) <= t {
                lo = mid + 1;
            } else {
                hi = mid;
            }
        }
        lo
    };
    out.extend(signals.iter().map(|s| {
        let until = s.t0_ns.saturating_add(horizon_span_ns(&s.plan));
        (lower(s.t0_ns), lower(until))
    }));
}
