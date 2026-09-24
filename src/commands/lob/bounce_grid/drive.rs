//! Драйвер прогона: сигналы формы на сутках (`signals_for`), события суток из
//! частей с кэшем счётчика (`day_events`), правило размера круга по цене
//! касания (`OrderSizing`, R2), параметры суток одной структурой
//! (`DayParams`), очередь готовых результатов форм по порядку (`FormOrder`),
//! прогон всех форм над сутками потоками (`drive_day`) и окна сетапов
//! (`day_windows`). Вынесено из `bounce_grid` при разрезке B3 (ревью 23.09),
//! поведение не менялось.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::Instant;

use hftbacktest::types::Event as HbtEvent;

use crate::book::Side;
use crate::commands::lob::backtest::{
    approach_plan, bounce_plan, count_feed_events, deadline_ns_from_secs, early_exit_ns_from_secs,
    feed_events_into, open_replay_feed, PlanShape, PoolLot,
};
use crate::lob::backtest::{
    drive_bounce, drive_bounce_windowed, drive_bounce_windowed_memo, with_backtest_over, BounceRun,
    BounceSignal, DriveConfig, ExecLatency, QueueModelKind, RoundMemo, SignalWindows,
};
use crate::lob::levels::{H3Mode, TouchRecord};
use crate::lob::sigma::SigmaSeries;

use super::args::{BounceGridArgs, DriverArg};
use super::carry::{cached_event_count, store_event_count};
use super::forms::GridForm;
use super::outputs::FormDayResult;
use super::sets::{Range, TouchContext, TouchFilter, CTX_AXES};

/// Сигналы формы: план базы на каждое касание (`σ_H` за окно дедлайна — только
/// σ-формам); касания, для которых форму не построить, пропускаются и
/// считаются (второе значение). С `--signal approach` `touches` — это **вид**
/// записей подхода как касаний (`cached_approaches`/`touch_view_of_approach`:
/// `start_ms` = `arm_ms`), а план строится `approach_plan` от самой записи
/// подхода: вход ставится на взводе, а не на касании (F6, В-73).
#[allow(clippy::cast_precision_loss)]
fn signals_for(
    touches: &[TouchRecord],
    approaches: Option<&[crate::lob::levels::ApproachRecord]>,
    sigma: &SigmaSeries,
    form: &GridForm,
    p: &DayParams<'_>,
) -> anyhow::Result<(Vec<BounceSignal>, u64)> {
    let deadline_ns = deadline_ns_from_secs(form.deadline_secs)?;
    let early_exit_ns = early_exit_ns_from_secs(form.early_exit_secs)?;
    let mut skipped: u64 = 0;
    if let Some(ctx) = p.ctx {
        anyhow::ensure!(
            ctx.len() == touches.len(),
            "контекст касаний ({}) не совпадает с касаниями ({})",
            ctx.len(),
            touches.len()
        );
    }
    if let Some(ap) = approaches {
        anyhow::ensure!(
            ap.len() == touches.len(),
            "записи подхода ({}) не совпадают с их видом как касаний ({})",
            ap.len(),
            touches.len()
        );
    }
    let filter = TouchFilter::from_day(p);
    let mut signals: Vec<BounceSignal> = touches
        .iter()
        .enumerate()
        .filter_map(|(ti, t)| {
            if !filter.admits(ti, t) {
                skipped += 1;
                return None;
            }
            let sigma_bps = if form.form.needs_sigma() {
                sigma.sigma_bps(t.start_ms, form.deadline_secs)
            } else {
                None
            };
            let shape = PlanShape {
                lot: p.lot,
                post_only: p.post_only,
                trail_bps: 0.0,
                trail_activate_bps: 0.0,
                grid_legs: 1,
                grid_step_ticks: 0,
                // F6 (В-73): форма входа — ось сетки (`--entry-form`):
                // `single@fr` — прежняя нога, лестница — ноги от `from` до `to`
                // bps над стеной.
                entry_form: form.entry_form,
                deadline_ns,
                early_exit_ns,
                // F5 (В-74): режим срока жизни входа — из формы сетки
                // (`--entry-ttl-secs`), а условия «стена снята»/«цена ушла»
                // читают `--h3-usd` и `--band-exit-bps`.
                entry_ttl: form.entry_ttl,
                h3_usd: p.h3_usd,
                band_exit_bps: p.band_exit_bps,
                // F7 (Б-75): форма выхода — ось сетки (`--exit-form`).
                exit_form: form.exit_form,
            };
            let built = match approaches {
                Some(ap) => approach_plan(&ap[ti], p.tick, form.form, sigma_bps, shape),
                None => bounce_plan(t, p.tick, form.form, sigma_bps, shape),
            };
            let Some((dir, plan)) = built else {
                skipped += 1;
                return None;
            };
            Some(BounceSignal {
                t0_ns: t.start_ms.saturating_mul(1_000_000),
                sigma: dir,
                plan,
                profile: 0,
                qty: Some(p.order_qtys[ti]),
            })
        })
        .collect();
    signals.sort_by_key(|s| s.t0_ns);
    Ok((signals, skipped))
}

/// События суток крейта из всех частей дня — в `Vec` **точного** размера:
/// сначала части считаются (`count_feed_events`), потом декодируются в
/// буфер с готовой ёмкостью. Рост удвоением держал старый и новый буфер
/// вместе (пик до 3× итога) и ронял сетку на сервере по OOM на сутках в
/// ~20 млн событий (2026-09-18); второй декод дешевле памяти.
pub(super) fn day_events(parts: &[PathBuf]) -> anyhow::Result<Vec<HbtEvent>> {
    let started = Instant::now();
    let mut total = 0usize;
    let mut counted_parts = 0usize;
    for path in parts {
        total += match cached_event_count(path) {
            Some(n) => n,
            None => {
                let mut feed = open_replay_feed(path)?;
                let n = count_feed_events(&mut feed);
                store_event_count(path, n);
                counted_parts += 1;
                n
            }
        };
    }
    let counted = started.elapsed().as_secs_f64();
    let mut events: Vec<HbtEvent> = Vec::with_capacity(total);
    for path in parts {
        let mut feed = open_replay_feed(path)?;
        feed_events_into(&mut feed, &mut events);
    }
    // Кэш числа событий — только ёмкость буфера: разошёлся — буфер просто
    // вырос, круги те же; сайдкары переписываются честным пересчётом.
    if events.len() != total {
        eprintln!(
            "bounce-grid:   события: кэш числа врал ({total} против {}), сайдкары пересчитаны",
            events.len()
        );
        for path in parts {
            let mut feed = open_replay_feed(path)?;
            let n = count_feed_events(&mut feed);
            store_event_count(path, n);
        }
    }
    eprintln!(
        "bounce-grid:   события: счёт {counted:.2}s ({counted_parts} из {} частей считано, остальные из кэша) · декод {:.2}s · {}",
        parts.len(),
        started.elapsed().as_secs_f64() - counted,
        events.len()
    );
    Ok(events)
}

/// Правило размера круга (R2, ревью 23.09): лот считается по цене **каждого
/// касания**, а не одной ценой на символ. До исправления лот брался по цене
/// последнего касания всей записи: сигнал в начале месяца получал размер по
/// цене его конца — заглядывание вперёд, и номинал круга расходился с
/// `--order-usd` ровно на движение цены за запись (у монеты, выросшей вдвое,
/// ранние круги шли на половину заявленного номинала). Поля пула читаются
/// один раз на символ.
#[derive(Debug, Clone, Copy)]
pub(super) enum OrderSizing {
    /// `--order-qty-e9` — лот задан числом, от цены не зависит.
    Fixed(i64),
    /// `--order-qty-from-pool` — `order_size_22a` при цене касания.
    Pool22a(PoolLot),
    /// `--order-usd` — номинал при цене касания, не меньше 22а.
    Usd(PoolLot, f64),
}

impl OrderSizing {
    pub(super) fn from_args(args: &BounceGridArgs, symbol: &str) -> anyhow::Result<Self> {
        if let Some(v) = args.order_qty_e9 {
            return Ok(Self::Fixed(v));
        }
        let lot = PoolLot::read(&args.root.join("instruments.csv"), symbol)?;
        Ok(match args.order_usd {
            Some(usd) => {
                anyhow::ensure!(
                    usd.is_finite() && usd > 0.0,
                    "{symbol}: --order-usd обязан быть положительным числом"
                );
                anyhow::ensure!(
                    lot.qty_step_e9 > 0,
                    "{symbol}: шаг лота в пуле обязан быть положительным"
                );
                Self::Usd(lot, usd)
            }
            None => Self::Pool22a(lot),
        })
    }

    /// Лот в 1e-9 при цене `price_tick` (в тиках книги `tick_e9`). Цена —
    /// уровня (стены) в момент касания, а не будущей заявки входа: она
    /// известна в `t0` (заглядывания нет), а вход стоит в bps от неё — номинал
    /// круга отличается от `--order-usd` на эти bps, не на ход цены за запись.
    fn qty_e9(&self, price_tick: i64, tick_e9: i64) -> i64 {
        let price_e9 = price_tick.saturating_mul(tick_e9);
        match *self {
            Self::Fixed(v) => v,
            Self::Pool22a(lot) => lot.qty_22a_e9(price_e9),
            Self::Usd(lot, usd) => lot.qty_usd_e9(price_e9, usd),
        }
    }

    /// Размер круга на каждое касание суток — по цене этого касания, с
    /// множителем `--order-qty-mult` (E7).
    pub(super) fn touch_qtys(&self, touches: &[TouchRecord], tick_e9: i64, mult: u32) -> Vec<f64> {
        touches
            .iter()
            .map(|t| {
                #[allow(clippy::cast_precision_loss)]
                let q = self
                    .qty_e9(t.price_tick, tick_e9)
                    .saturating_mul(i64::from(mult.max(1))) as f64
                    / 1e9;
                q
            })
            .collect()
    }
}

/// Параметры прогона суток одной структурой (clippy держит предел семи аргументов).
#[derive(Debug, Clone, Copy)]
pub(super) struct DayParams<'a> {
    /// Память кругов по форме (G10, `--round-memo`); `None` — счёт с нуля.
    pub(super) memos: Option<&'a [Mutex<RoundMemo>]>,
    pub(super) tick: f64,
    pub(super) lot: f64,
    pub(super) rtt_ns: ExecLatency,
    /// Модель очереди/исполнения суток (`--queue-model`, F3) — одна на процесс.
    pub(super) queue_model: QueueModelKind,
    /// Размер круга на каждое касание суток (тот же порядок, что `touches`):
    /// лот по цене **этого** касания (R2, `OrderSizing`).
    pub(super) order_qtys: &'a [f64],
    pub(super) threads: usize,
    /// Вход пост-онли (В-72): у плана `post_only`, у сетки умолчание —
    /// включён (`BounceGridArgs::entry_post_only`).
    pub(super) post_only: bool,
    /// E3: входить только от фронтрана (`--frontrun-only`).
    pub(super) frontrun_only: bool,
    /// Порог уровня — проверяется и **в момент касания** (`H3Mode::holds_at_touch`).
    pub(super) mode: H3Mode,
    /// Номинал порога В-66 в долларах (`--h3-usd`): из него план считает
    /// `level_floor_qty` — порог «стена снята» (F5, В-74). `None` — условия
    /// F5 выключены (режим `touch`), иначе `run_bounce_grid` отказал бы.
    pub(super) h3_usd: Option<f64>,
    /// Полоса ухода цены, bps (F5, В-74, `--band-exit-bps`); `0` — условие
    /// выключено (режим `touch`).
    pub(super) band_exit_bps: f64,
    /// Фильтры базы в момент касания: возраст плотности и сила «×поток».
    pub(super) min_age_ms: Option<i64>,
    pub(super) min_flow_pct: Option<f64>,
    /// Ось стороны (`--side`): `None` — обе стороны.
    pub(super) side: Option<Side>,
    /// Доля съедания стены к касанию не больше этого процента (`eaten=`).
    pub(super) eaten_max_pct: Option<f64>,
    /// Номинал стены при касании ≥ (`usd_min=`), доллары.
    pub(super) usd_min: Option<f64>,
    /// Контекст касаний суток (тот же порядок, что `touches`) — только когда
    /// у набора есть ключи контекста; границы — `ctx_ranges`.
    pub(super) ctx: Option<&'a [TouchContext]>,
    pub(super) ctx_ranges: [Range; CTX_AXES.len()],
    /// Ряд `σ` символа (все сутки записи подряд).
    pub(super) sigma: &'a SigmaSeries,
}

/// Готовые результаты форм уходят в `sink` **по порядку форм**: форма,
/// закончившая раньше соседей с меньшим индексом, ждёт в буфере (не больше
/// числа потоков), чтобы дамп не зависел от числа потоков.
struct FormOrder<'a> {
    pending: BTreeMap<usize, (BounceRun, Vec<BounceSignal>, u64)>,
    next_form: usize,
    done: usize,
    sink: &'a mut (dyn FnMut(FormDayResult, &[BounceSignal]) -> anyhow::Result<()> + Send),
}

/// Все формы над одними сутками: потоки берут формы по счётчику. `Setups`
/// — один проход книги на сутки (`SignalWindows`, общий для форм), дальше у
/// каждой формы движок только внутри кругов; `Full` — у каждой формы свой
/// `Backtest` над всеми событиями суток (эталон гейта).
///
/// Память (сервер, 2026-09-18): сигналы формы строятся **в потоке, когда
/// форма взята** (например, 48 форм × 100 тыс. касаний × 128 Б заранее — 600 МБ), а
/// результат формы отдаётся `sink` сразу и до конца суток не копится
/// (`FormOrder`). Возвращает число форм, отданных в `sink`.
///
/// `approaches` — записи подхода (F6, `--signal approach`): те же сутки и тот
/// же порядок, что `touches` (их вид как касания); `None` — сигнал по касаниям.
pub(super) fn drive_day(
    events: &[HbtEvent],
    windows: Option<&SignalWindows>,
    touches: &[TouchRecord],
    approaches: Option<&[crate::lob::levels::ApproachRecord]>,
    forms: &[GridForm],
    p: DayParams<'_>,
    sink: &mut (dyn FnMut(FormDayResult, &[BounceSignal]) -> anyhow::Result<()> + Send),
) -> anyhow::Result<usize> {
    let next = AtomicUsize::new(0);
    let failure: Mutex<Option<anyhow::Error>> = Mutex::new(None);
    let order = Mutex::new(FormOrder {
        pending: BTreeMap::new(),
        next_form: 0,
        done: 0,
        sink,
    });
    std::thread::scope(|scope| {
        for _ in 0..p.threads.max(1) {
            scope.spawn(|| loop {
                let i = next.fetch_add(1, Ordering::Relaxed);
                if i >= forms.len() {
                    break;
                }
                if failure.lock().map(|f| f.is_some()).unwrap_or(true) {
                    break;
                }
                let cfg = DriveConfig {
                    // R2: размер несёт каждый сигнал (`BounceSignal::qty`,
                    // `signals_for`); размер прогона сетке не нужен.
                    order_qty: 0.0,
                    first_order_id: 1,
                    queue_model: p.queue_model,
                };
                let step = signals_for(touches, approaches, p.sigma, &forms[i], &p).and_then(
                    |(signals, skipped)| {
                        let driven = match windows {
                            Some(w) => match p.memos {
                                // Память формы берёт один поток за раз: форма в наборе одна.
                                Some(ms) => {
                                    let mut m = ms[i]
                                        .lock()
                                        .unwrap_or_else(std::sync::PoisonError::into_inner);
                                    drive_bounce_windowed_memo(
                                        events, w, &signals, &cfg, p.rtt_ns, &mut m,
                                    )
                                }
                                None => drive_bounce_windowed(events, w, &signals, &cfg, p.rtt_ns),
                            },
                            None => with_backtest_over(
                                events,
                                p.tick,
                                p.lot,
                                p.rtt_ns,
                                p.queue_model,
                                |bt| drive_bounce(bt, 0, &signals, &cfg),
                            ),
                        };
                        driven
                            .map(|run| (run, signals, skipped))
                            .map_err(|e| anyhow::anyhow!("форма #{i}: {e}"))
                    },
                );
                let flushed = match step {
                    Ok((run, signals, skipped)) => match order.lock() {
                        Ok(mut o) => {
                            o.pending.insert(i, (run, signals, skipped));
                            let mut res = Ok(());
                            loop {
                                let form = o.next_form;
                                let Some((run, signals, skipped)) = o.pending.remove(&form) else {
                                    break;
                                };
                                res = (o.sink)(FormDayResult { form, run, skipped }, &signals);
                                o.next_form += 1;
                                o.done += 1;
                                if res.is_err() {
                                    break;
                                }
                            }
                            res
                        }
                        Err(_) => Err(anyhow::anyhow!("результаты форм: мьютекс")),
                    },
                    Err(e) => Err(e),
                };
                if let Err(e) = flushed {
                    if let Ok(mut f) = failure.lock() {
                        if f.is_none() {
                            *f = Some(e);
                        }
                    }
                    break;
                }
            });
        }
    });
    if let Some(e) = failure.into_inner().ok().flatten() {
        return Err(e);
    }
    let o = order
        .into_inner()
        .map_err(|_| anyhow::anyhow!("результаты форм: мьютекс"))?;
    anyhow::ensure!(
        o.pending.is_empty(),
        "формы без записи в дамп: {}",
        o.pending.len()
    );
    Ok(o.done)
}

/// Окна сетапов суток (`--driver setups`): снимок книги на каждый `t0`
/// касания — один раз на сутки, общий для всех форм **и наборов** (`--set`):
/// касания те же, фильтры наборов только выбирают из них сигналы.
pub(super) fn day_windows(
    events: &[HbtEvent],
    touches: &[TouchRecord],
    driver: DriverArg,
    tick: f64,
    lot: f64,
) -> Option<SignalWindows> {
    match driver {
        DriverArg::Full => None,
        DriverArg::Setups => {
            let t0s: Vec<i64> = touches
                .iter()
                .map(|t| t.start_ms.saturating_mul(1_000_000))
                .collect();
            let started = Instant::now();
            let w = SignalWindows::build(events, &t0s, tick, lot);
            eprintln!(
                "bounce-grid:   окна: снимков {} · уровней всего {} (в среднем {:.0} на снимок) · {:.2}s",
                w.len(),
                w.levels_total(),
                w.levels_total() as f64 / w.len().max(1) as f64,
                started.elapsed().as_secs_f64()
            );
            Some(w)
        }
    }
}
