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

use crate::commands::lob::backtest::{
    approach_plan, approach_plan_sched, bounce_plan, bounce_plan_sched, count_feed_events,
    deadline_ns_from_secs, early_exit_ns_from_ms, open_replay_feed, replay_compact_into_until,
    BounceForm, EntryForm, PlanShape, PoolLot, StopForm,
};
use crate::lob::backtest::{
    drive_bounce, drive_bounce_windowed, precompute_exit_group, with_backtest_over, BounceRun,
    BounceSignal, CompactEvent, DriveConfig, ExecLatency, QueueModelKind, RoundMemo, SignalWindows,
};
use crate::lob::levels::TouchRecord;
use crate::lob::sigma::SigmaSeries;
use crate::lob::step_schedule::StepSchedule;

use super::args::{BounceGridArgs, DriverArg};
use super::carry::{cached_event_count, store_event_count};
use super::entry_sigma::EntrySigma;
use super::forms::GridForm;
use super::outputs::FormDayResult;
use super::sets::{FilterSpec, TouchFilter};

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
    let early_exit_ns = early_exit_ns_from_ms(form.early_exit_ms)?;
    let mut skipped: u64 = 0;
    // Г-101 (П-17): режим бывает только у лестницы входа / стопа `pct`|`s`; иначе множитель молча не сработал бы.
    anyhow::ensure!(
        p.filter.ladder_regime.is_none() || scale_ladder(form.entry_form, 1.0).is_some(),
        "{}: lreg_* (Г-101) требует форму входа ladder…",
        form.label
    );
    anyhow::ensure!(
        p.filter.stop_regime.is_none() || scale_stop(form.form.stop, 1.0).is_some(),
        "{}: sreg_* (Г-101) требует стоп pct<x> или s<a>",
        form.label
    );
    if let Some(ctx) = p.filter.ctx {
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
    let admitted = p.admitted.map(|cell| {
        cell.get_or_init(|| {
            touches
                .iter()
                .enumerate()
                .filter(|&(ti, _)| filter.admits(ti))
                .map(|(ti, _)| u32::try_from(ti).expect("касаний меньше 2^32"))
                .collect()
        })
    });
    if let Some(ids) = admitted {
        skipped = (touches.len() - ids.len()) as u64;
    }
    let candidates: Box<dyn Iterator<Item = (usize, &TouchRecord)> + '_> = match admitted {
        Some(ids) => Box::new(ids.iter().map(|&ti| (ti as usize, &touches[ti as usize]))),
        None => Box::new(touches.iter().enumerate()),
    };
    let mut signals: Vec<BounceSignal> = candidates
        .filter_map(|(ti, t)| {
            if (admitted.is_none() && !filter.admits(ti))
                || !filter.admits_r1(approaches.map(|ap| &ap[ti]))
            {
                skipped += 1;
                return None;
            }
            // Г-101: «сильная» половина (btc4h ≤ T) — шаг лестницы и/или стоп ×f; режим не задан — прежняя форма.
            let entry_form = p
                .filter
                .regime_factor(p.filter.ladder_regime, ti)
                .and_then(|f| scale_ladder(form.entry_form, f))
                .unwrap_or(form.entry_form);
            let bform = match p
                .filter
                .regime_factor(p.filter.stop_regime, ti)
                .and_then(|f| scale_stop(form.form.stop, f))
            {
                Some(stop) => BounceForm { stop, ..form.form },
                None => form.form,
            };
            let sigma_bps = if bform.needs_sigma() {
                sigma.sigma_bps(t.start_ms, form.deadline_secs)
            } else {
                None
            };
            let shape = PlanShape {
                lot: p.lot,
                level_lot: None,
                post_only: p.post_only,
                trail_bps: 0.0,
                trail_activate_bps: 0.0,
                grid_legs: 1,
                grid_step_ticks: 0,
                // F6 (В-73): форма входа — ось сетки (`--entry-form`):
                // `single@fr` — прежняя нога, лестница — ноги от `from` до `to`
                // bps над стеной.
                entry_form,
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
                // В-131: σ на взводе — только σ-лестнице (`t.start_ms` у подхода — `arm_ms`).
                entry_sigma_bps: match entry_form {
                    EntryForm::LadderSigma { .. } => p.entry_sigma.and_then(|s| s.at(t.start_ms)),
                    _ => None,
                },
            };
            // Г-98: лот ниже min-лота (`size_risk`) — NaN из `day_risk_qtys`: сделка пропущена и посчитана.
            if p.order_qtys.get(ti).is_some_and(|q| q.is_nan()) {
                skipped += 1;
                return None;
            }
            let built = match (p.step_schedule, approaches) {
                (Some(sc), Some(ap)) => approach_plan_sched(
                    &ap[ti],
                    p.grid_e9.0,
                    p.grid_e9.1,
                    sc,
                    bform,
                    sigma_bps,
                    shape,
                ),
                (Some(sc), None) => {
                    bounce_plan_sched(t, p.grid_e9.0, p.grid_e9.1, sc, bform, sigma_bps, shape)
                }
                (None, Some(ap)) => approach_plan(&ap[ti], p.tick, bform, sigma_bps, shape),
                (None, None) => bounce_plan(t, p.tick, bform, sigma_bps, shape),
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

/// Г-101: шаг лестницы входа ×`f` — дальняя нога уходит на `from + f·(to − from)`; не лестница — `None`.
fn scale_ladder(e: EntryForm, f: f64) -> Option<EntryForm> {
    match e {
        EntryForm::Ladder {
            legs,
            from_bps,
            to_bps,
            wall_weight,
        } => Some(EntryForm::Ladder {
            legs,
            from_bps,
            to_bps: from_bps + f * (to_bps - from_bps),
            wall_weight,
        }),
        EntryForm::LadderSigma {
            legs,
            from_sigma,
            to_sigma,
            wall_weight,
        } => Some(EntryForm::LadderSigma {
            legs,
            from_sigma,
            to_sigma: from_sigma + f * (to_sigma - from_sigma),
            wall_weight,
        }),
        _ => None,
    }
}

/// Г-101: ширина стопа ×`f` (`pct<x>` → `pct<x·f>`, `s<a>` → `s<a·f>`); прочие формы стопа — `None`.
fn scale_stop(s: StopForm, f: f64) -> Option<StopForm> {
    match s {
        StopForm::Pct(x) => Some(StopForm::Pct(x * f)),
        StopForm::Sigma(a) => Some(StopForm::Sigma(a * f)),
        _ => None,
    }
}

/// Сигналы форм, посчитанные в `exit_groups`, — главному проходу суток (TK-050, потолок `SIG_CACHE_CAP` сигналов на сутки): `signals_for` второй раз не зовётся. Сверх потолка форма не
/// кладётся — считается заново, как раньше (результат тот же).
type SigSlot = Option<(Vec<BounceSignal>, u64)>;

/// Потолок сигналов на сутки (бывший `ALPHA_SIG_CACHE=3000000` боевого набора).
const SIG_CACHE_CAP: usize = 3_000_000;

struct SigCache {
    slots: Mutex<Vec<SigSlot>>,
    left: AtomicUsize,
}

impl SigCache {
    fn new(forms: usize) -> Self {
        Self {
            slots: Mutex::new((0..forms).map(|_| None).collect()),
            left: AtomicUsize::new(SIG_CACHE_CAP),
        }
    }

    fn put(&self, i: usize, signals: Vec<BounceSignal>, skipped: u64) {
        let n = signals.len();
        if self
            .left
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |l| l.checked_sub(n))
            .is_ok()
        {
            if let Ok(mut g) = self.slots.lock() {
                g[i] = Some((signals, skipped));
            }
        }
    }

    fn take(&self, i: usize) -> Option<(Vec<BounceSignal>, u64)> {
        self.slots.lock().ok()?[i].take()
    }
}

/// Группы форм с общим входом (Э-08): равные `entry_form` и `entry_ttl`. У `retreat<k>t<T>` `k`/`T` — часть
/// `entry_form`, поэтому варианты с разной памятью отступа в одну группу не попадают (вход группы — из её первой формы).
pub(super) fn entry_groups(forms: &[GridForm]) -> Vec<Vec<usize>> {
    let mut groups: Vec<Vec<usize>> = Vec::new();
    for (i, f) in forms.iter().enumerate() {
        match groups.iter_mut().find(|g| {
            let h = &forms[g[0]];
            h.entry_form == f.entry_form
                && h.entry_ttl == f.entry_ttl
                && !matches!(f.entry_form, EntryForm::Reclaim { .. })
        }) {
            Some(g) => g.push(i),
            None => groups.push(vec![i]),
        }
    }
    groups
}

/// Предсчёт групп выходов (Э-08) для форм суток: группы — по `entry_form` и `entry_ttl`, из двух и больше
/// форм; сигналы — те же, что у формы (`signals_for`). Печатает число групповых кругов.
#[allow(clippy::too_many_arguments)]
fn exit_groups(
    events: DayRows<'_>,
    windows: &SignalWindows,
    touches: &[TouchRecord],
    approaches: Option<&[crate::lob::levels::ApproachRecord]>,
    forms: &[GridForm],
    p: &DayParams<'_>,
    memos: &[Mutex<RoundMemo>],
    cache: Option<&SigCache>,
) -> anyhow::Result<()> {
    let groups = entry_groups(forms);
    let cfg = DriveConfig {
        tape_log_secs: p.tape_log_secs,
        order_qty: 0.0,
        first_order_id: 1,
        queue_model: p.queue_model,
        busy_skip: p.busy_skip,
        hold_skip: p.hold_skip,
    };
    let mut rounds: u64 = 0;
    for g in groups.into_iter().filter(|g| {
        g.len() > 1
            || g.iter()
                .any(|&i| matches!(forms[i].exit_form, super::forms::ExitForm::Wall2 { .. }))
    }) {
        let mut sigs: Vec<Vec<BounceSignal>> = Vec::with_capacity(g.len());
        let mut skips: Vec<u64> = Vec::with_capacity(g.len());
        for &i in &g {
            let (sg, sk) = signals_for(touches, approaches, p.sigma, &forms[i], p)?;
            sigs.push(sg);
            skips.push(sk);
        }
        let mut guards: Vec<_> = g
            .iter()
            .map(|&i| {
                memos[p.form_ids[i]]
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner)
            })
            .collect();
        let mut refs: Vec<&mut RoundMemo> = guards.iter_mut().map(|m| &mut **m).collect();
        let sig_refs: Vec<&[BounceSignal]> = sigs.iter().map(Vec::as_slice).collect();
        rounds += match events {
            DayRows::Compact(c) => {
                precompute_exit_group(c, windows, &sig_refs, &cfg, p.rtt_ns, &mut refs, p.walls)
            }
            DayRows::Wide(e) => {
                precompute_exit_group(e, windows, &sig_refs, &cfg, p.rtt_ns, &mut refs, p.walls)
            }
        }
        .map_err(|e| anyhow::anyhow!("группа выходов: {e}"))?;
        drop(refs);
        drop(guards);
        if let Some(c) = cache {
            for ((&i, sg), sk) in g.iter().zip(sigs).zip(skips) {
                c.put(i, sg, sk);
            }
        }
    }
    eprintln!("bounce-grid:   группы выходов: групповых кругов {rounds}");
    if crate::lob::run_flags::RunFlags::from_env().diag {
        let d: Vec<u64> = crate::lob::backtest::EXIT_GROUP_DIAG
            .iter()
            .map(|a| a.load(std::sync::atomic::Ordering::Relaxed))
            .collect();
        eprintln!(
            "bounce-grid:   группы выходов (разбор, нарастающим итогом): сигналов {} в памяти {} частей {} частей≥2 {} без круга {}",
            d[0], d[1], d[2], d[3], d[4]
        );
    }
    Ok(())
}

/// События суток крейта из всех частей дня — в `Vec` **точного** размера:
/// сначала части считаются (`count_feed_events`), потом декодируются в
/// буфер с готовой ёмкостью. Рост удвоением держал старый и новый буфер
/// вместе (пик до 3× итога) и ронял сетку на сервере по OOM на сутках в
/// ~20 млн событий (2026-09-18); второй декод дешевле памяти.
pub(super) fn day_events(
    parts: &[PathBuf],
    symbol: &str,
    day: &str,
) -> anyhow::Result<Vec<CompactEvent>> {
    let started = Instant::now();
    let e2e_count = super::e2e::Mark::now();
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
    super::e2e::stage(
        symbol,
        day,
        "ev_count",
        e2e_count,
        serde_json::json!({ "parts": parts.len(), "counted_parts": counted_parts }),
    );
    let e2e_decode = super::e2e::Mark::now();
    // Р6: сутки — компактными событиями (32 Б вместо 64); круги разворачивают свой кусок сами.
    let mut events: Vec<CompactEvent> = Vec::with_capacity(total);
    for path in parts {
        let mut feed = open_replay_feed(path)?;
        replay_compact_into_until(&mut feed, None, &mut events);
    }
    // Кэш числа событий — только ёмкость буфера: разошёлся — буфер просто
    // вырос, круги те же; сайдкары переписываются честным пересчётом.
    super::e2e::stage(
        symbol,
        day,
        "ev_decode",
        e2e_decode,
        serde_json::json!({ "n_events": events.len() }),
    );
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

    /// М-04 (TK-206): размер первого подхода — номинал × `k` до округления вниз до шага лота (как у
    /// `qty_usd_e9`), не округление вверх. `None` — ниже min-лота (22а) или нуль шагов: клетка не определена
    /// (spec-m04 §3), вызывающий отказывает, молча не поднимает.
    pub(super) fn qty_first_e9(
        &self,
        price_tick: i64,
        tick_e9: i64,
        mult: u32,
        k: f64,
        step_e9: i64,
    ) -> Option<i64> {
        let price_e9 = price_tick.saturating_mul(tick_e9);
        let m = f64::from(mult.max(1)) * k;
        #[allow(clippy::cast_precision_loss)]
        let (raw, step, min) = match *self {
            Self::Fixed(v) => (v as f64 * m, step_e9, step_e9),
            Self::Pool22a(lot) => (
                lot.qty_22a_e9(price_e9) as f64 * m,
                lot.qty_step_e9,
                lot.qty_22a_e9(price_e9),
            ),
            Self::Usd(lot, usd) if price_e9 > 0 => (
                usd * 1e18 / price_e9 as f64 * f64::from(mult.max(1)) * k,
                lot.qty_step_e9,
                lot.qty_22a_e9(price_e9),
            ),
            Self::Usd(lot, _) => (0.0, lot.qty_step_e9, lot.qty_22a_e9(price_e9)),
        };
        if step <= 0 || !raw.is_finite() {
            return None;
        }
        #[allow(clippy::cast_possible_truncation, clippy::cast_precision_loss)]
        let q = ((raw / step as f64 + 1e-9).floor() as i64).saturating_mul(step);
        (q > 0 && q >= min.saturating_mul(i64::from(mult.max(1)))).then_some(q)
    }

    /// Г-98 (П-17, `size_risk=<R$>`): лот под риск — `min(base, R·10⁴ ÷ span_bps)` (в монете по цене уровня), вниз до шага
    /// `step_e9`; `span_bps < 1` или риск не жмёт — `base` как есть (= B1 байт в байт). `None` — ниже min-лота (22а):
    /// сделка пропускается и считается, вверх не поднимаем.
    pub(super) fn qty_risk_e9(
        &self,
        price_e9: i64,
        mult: u32,
        base_e9: i64,
        risk_usd: f64,
        span_bps: i64,
        step_e9: i64,
    ) -> Option<i64> {
        if span_bps < 1 {
            return Some(base_e9);
        }
        #[allow(clippy::cast_precision_loss)]
        let risk_raw = risk_usd * 1e4 / span_bps as f64 * 1e18 / price_e9.max(1) as f64;
        #[allow(clippy::cast_precision_loss)]
        if !risk_raw.is_finite() || risk_raw >= base_e9 as f64 {
            return Some(base_e9);
        }
        let (step, min) = match *self {
            Self::Fixed(_) => (step_e9, step_e9),
            Self::Pool22a(lot) | Self::Usd(lot, _) => (lot.qty_step_e9, lot.qty_22a_e9(price_e9)),
        };
        let step = if step_e9 > 0 { step_e9 } else { step };
        if step <= 0 {
            return None;
        }
        #[allow(clippy::cast_possible_truncation, clippy::cast_precision_loss)]
        let q = ((risk_raw / step as f64 + 1e-9).floor() as i64).saturating_mul(step);
        (q > 0 && q >= min.saturating_mul(i64::from(mult.max(1)))).then_some(q)
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
    /// Индексы касаний, прошедших фильтр набора (`ALPHA_ADMIT_CACHE=1`): фильтр не зависит от формы,
    /// считается один раз на сутки-набор; `None` — фильтр на каждую форму, как раньше.
    pub(super) admitted: Option<&'a std::sync::OnceLock<Vec<u32>>>,
    /// Порог уровня в момент касания по касаниям суток, один раз на все наборы (`ALPHA_HOLDS_MEMO=1`); `None` — как раньше.
    pub(super) holds: Option<&'a [bool]>,
    /// Строки скаляров касаний суток для `TouchFilter::admits` (`ALPHA_ADMIT_SOA=1` вместе с `HOLDS_MEMO`); `None` — как раньше.
    pub(super) rows: Option<&'a [super::sets::AdmitRow]>,
    /// Номер каждой формы `forms` в общем списке прогона — индекс памяти кругов (`--cells`, T-38: у набора
    /// своё подмножество форм; без флага — `0..forms.len()`).
    pub(super) form_ids: &'a [usize],
    pub(super) tick: f64,
    pub(super) lot: f64,
    /// Сетка данных `(tick_e9, step_e9)` и расписание действующих шагов суток (v4, В-172); `None` —
    /// шаг один, план прежний.
    pub(super) grid_e9: (i64, i64),
    pub(super) step_schedule: Option<&'a StepSchedule>,
    pub(super) rtt_ns: ExecLatency,
    /// Журнал стен суток (TK-115, e65) — для форм `wall2*`; `None`, когда таких форм нет.
    pub(super) walls: Option<&'a [crate::lob::levels::WallEvent]>,
    /// Модель очереди/исполнения суток (`--queue-model`, F3) — одна на процесс.
    pub(super) queue_model: QueueModelKind,
    /// Пропуск «позиция занята» (`--busy-skip`, T-31): `false` — каждый сигнал свой круг, след в `trace`.
    pub(super) busy_skip: bool,
    /// Пропуск пустых шагов удержания (`--hold-step skip`, Э-04б): итог тот же, быстрее.
    pub(super) hold_skip: bool,
    /// Окно журнала ленты (`--tape-log W`, TK-115 Г-112), секунды; 0 — выключено. Идёт в `DriveConfig`.
    pub(super) tape_log_secs: u32,
    /// Группы выходов (`--exit-group on`, Э-08): предсчёт кругов группы в память форм.
    pub(super) exit_group: bool,
    /// Размер круга на каждое касание суток (тот же порядок, что `touches`):
    /// лот по цене **этого** касания (R2, `OrderSizing`).
    pub(super) order_qtys: &'a [f64],
    pub(super) threads: usize,
    /// Вход пост-онли (В-72): у плана `post_only`, у сетки умолчание —
    /// включён (`BounceGridArgs::entry_post_only`).
    pub(super) post_only: bool,
    /// Номинал порога В-66 в долларах (`--h3-usd`): из него план считает
    /// `level_floor_qty` — порог «стена снята» (F5, В-74). `None` — условия
    /// F5 выключены (режим `touch`), иначе `run_bounce_grid` отказал бы.
    pub(super) h3_usd: Option<f64>,
    /// Полоса ухода цены, bps (F5, В-74, `--band-exit-bps`); `0` — условие
    /// выключено (режим `touch`).
    pub(super) band_exit_bps: f64,
    /// σ монеты на взводе для σ-лестницы (В-131, `--sigma-from`); `None` — флага нет.
    pub(super) entry_sigma: Option<&'a EntrySigma>,
    /// Фильтры набора в момент касания (возраст, сила «×поток», сторона, съедание, номинал, R1, контекст).
    pub(super) filter: FilterSpec<'a>,
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

/// Результат формы `i` — в очередь по порядку форм; готовый префикс уходит в `sink`.
fn deposit(
    order: &Mutex<FormOrder<'_>>,
    i: usize,
    run: BounceRun,
    signals: Vec<BounceSignal>,
    skipped: u64,
) -> anyhow::Result<()> {
    let mut o = order
        .lock()
        .map_err(|_| anyhow::anyhow!("результаты форм: мьютекс"))?;
    o.pending.insert(i, (run, signals, skipped));
    loop {
        let form = o.next_form;
        let Some((run, signals, skipped)) = o.pending.remove(&form) else {
            return Ok(());
        };
        let res = (o.sink)(FormDayResult { form, run, skipped }, &signals);
        o.next_form += 1;
        o.done += 1;
        res?;
    }
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
    events: DayRows<'_>,
    windows: Option<&SignalWindows>,
    touches: &[TouchRecord],
    approaches: Option<&[crate::lob::levels::ApproachRecord]>,
    forms: &[GridForm],
    p: DayParams<'_>,
    sink: &mut (dyn FnMut(FormDayResult, &[BounceSignal]) -> anyhow::Result<()> + Send),
) -> anyhow::Result<usize> {
    // `--driver full` (эталон гейта) держит сутки 64-байтными событиями крейта — одна полная
    // развёртка на сутки, общая для форм; путь по окнам (`setups`) разворачивает только круги (Р6).
    let expanded: Vec<HbtEvent> = match (windows, events) {
        (None, DayRows::Compact(c)) => c.iter().map(CompactEvent::expand).collect(),
        _ => Vec::new(),
    };
    let full: &[HbtEvent] = match (windows, events) {
        (None, DayRows::Wide(e)) => e,
        _ => &expanded,
    };
    // Э-08: формы с одним входом (`entry_form`, `entry_ttl`) — круги группы заранее, одним движком на
    // сигнал, в память каждой формы; дальше формы идут прежним путём и берут круги из памяти.
    let sig_cache = Some(SigCache::new(forms.len()));
    if p.exit_group && !p.busy_skip {
        if let (Some(w), Some(ms)) = (windows, p.memos) {
            exit_groups(
                events,
                w,
                touches,
                approaches,
                forms,
                &p,
                ms,
                sig_cache.as_ref(),
            )?;
        }
    }
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
                    tape_log_secs: p.tape_log_secs,
                    // R2: размер несёт каждый сигнал (`BounceSignal::qty`,
                    // `signals_for`); размер прогона сетке не нужен.
                    order_qty: 0.0,
                    first_order_id: 1,
                    queue_model: p.queue_model,
                    busy_skip: p.busy_skip,
                    hold_skip: p.hold_skip,
                };
                let sigs = match sig_cache.as_ref().and_then(|c| c.take(i)) {
                    Some(hit) => Ok(hit),
                    None => signals_for(touches, approaches, p.sigma, &forms[i], &p),
                };
                let step = sigs.and_then(|(signals, skipped)| {
                    let driven = match windows {
                        Some(w) => match p.memos {
                            // Память формы берёт один поток за раз: форма в наборе одна.
                            Some(ms) => {
                                let mut m = ms[p.form_ids[i]]
                                    .lock()
                                    .unwrap_or_else(std::sync::PoisonError::into_inner);
                                match events {
                                    DayRows::Compact(c) => drive_bounce_windowed(
                                        c,
                                        w,
                                        &signals,
                                        &cfg,
                                        p.rtt_ns,
                                        Some(&mut m),
                                    ),
                                    DayRows::Wide(e) => drive_bounce_windowed(
                                        e,
                                        w,
                                        &signals,
                                        &cfg,
                                        p.rtt_ns,
                                        Some(&mut m),
                                    ),
                                }
                            }
                            None => match events {
                                DayRows::Compact(c) => {
                                    drive_bounce_windowed(c, w, &signals, &cfg, p.rtt_ns, None)
                                }
                                DayRows::Wide(e) => {
                                    drive_bounce_windowed(e, w, &signals, &cfg, p.rtt_ns, None)
                                }
                            },
                        },
                        None => {
                            with_backtest_over(full, p.tick, p.lot, p.rtt_ns, p.queue_model, |bt| {
                                drive_bounce(bt, 0, &signals, &cfg)
                            })
                        }
                    };
                    driven
                        .map(|run| (run, signals, skipped))
                        .map_err(|e| anyhow::anyhow!("форма #{i}: {e}"))
                });
                let flushed = step
                    .and_then(|(run, signals, skipped)| deposit(&order, i, run, signals, skipped));
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

/// События суток в том виде, в каком их держит прогон (`--events`): компактные строки Р6
/// (круг разворачивается в свой буфер) или 64-байтные строки крейта (круг берёт срез без копии).
#[derive(Clone, Copy)]
pub(super) enum DayRows<'a> {
    Compact(&'a [CompactEvent]),
    Wide(&'a [HbtEvent]),
}

/// Окна сетапов суток (`--driver setups`): снимок книги на каждый `t0`
/// касания — один раз на сутки, общий для всех форм **и наборов** (`--set`):
/// касания те же, фильтры наборов только выбирают из них сигналы.
pub(super) fn day_windows(
    events: DayRows<'_>,
    t0s: &[i64],
    driver: DriverArg,
    tick: f64,
    lot: f64,
    check: bool,
) -> anyhow::Result<Option<SignalWindows>> {
    match driver {
        DriverArg::Full => Ok(None),
        DriverArg::Setups => {
            let started = Instant::now();
            let w = match events {
                DayRows::Compact(c) => SignalWindows::build(c, t0s, tick, lot),
                DayRows::Wide(e) => SignalWindows::build(e, t0s, tick, lot),
            };
            eprintln!(
                "bounce-grid:   окна: снимков {} · уровней всего {} (в среднем {:.0} на снимок) · {:.2}s",
                w.len(),
                w.levels_total(),
                w.levels_total() as f64 / w.len().max(1) as f64,
                started.elapsed().as_secs_f64()
            );
            if check {
                let started = Instant::now();
                let reference = match events {
                    DayRows::Compact(c) => SignalWindows::build_crate(c, t0s, tick, lot),
                    DayRows::Wide(e) => SignalWindows::build_crate(e, t0s, tick, lot),
                };
                if let Some((t0, field)) = w.first_mismatch(&reference) {
                    anyhow::bail!("окна: снимок на t0={t0} расходится с книгой крейта: {field}");
                }
                eprintln!(
                    "bounce-grid:   окна: сверка с книгой крейта — {} снимков равны · {:.2}s",
                    reference.len(),
                    started.elapsed().as_secs_f64()
                );
            }
            Ok(Some(w))
        }
    }
}
