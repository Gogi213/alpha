//! Шаги `run_symbol` (КТ-6b, С-29): подготовка символа и разбор суток вынесены из тела команды
//! функциями; вывод и порядок вычислений те же, что были в теле.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use super::args::{BounceGridArgs, SignalArg};
use super::cache::DayTouches;
use super::drive::{day_windows, DayRows, OrderSizing};
use super::sets::FilterSet;
use super::SharedEvents;
use crate::commands::lob::{replay_symbol_touches_and_second_mids, session_parts_for};
use crate::lob::backtest::{RoundMemo, SignalWindows};
use crate::lob::levels::LevelsConfig;
use crate::lob::run_flags::RunFlags;
use crate::lob::sigma::SigmaSeries;
use crate::lob::step_schedule::StepSchedule;

/// Части записи символа во всей записи (`--carry-root`); нет их — перенос выключен для символа.
pub(super) fn carry_parts_by_day(
    args: &BounceGridArgs,
    symbol: &str,
) -> BTreeMap<String, Vec<PathBuf>> {
    match &args.carry_root {
        Some(dir) => match session_parts_for(dir, symbol) {
            Ok(parts) => {
                let mut m: BTreeMap<String, Vec<PathBuf>> = BTreeMap::new();
                for p in parts {
                    m.entry(p.day_utc.clone()).or_default().push(p.path.clone());
                }
                m
            }
            Err(e) => {
                eprintln!(
                    "bounce-grid: {symbol} — --carry-root {}: {e}, перенос выключен для символа",
                    dir.display()
                );
                BTreeMap::new()
            }
        },
        None => BTreeMap::new(),
    }
}

/// `--touches-cache-only`: отбрасывает сутки без кэша касаний; `false` — суток не осталось, символ пропущен.
pub(super) fn prune_days_without_cache(
    args: &BounceGridArgs,
    symbol: &str,
    parts_by_day: &mut BTreeMap<String, Vec<PathBuf>>,
) -> bool {
    if args.touches_cache_only && args.signal != SignalArg::Approach {
        if let Some(dir) = args.touches_from.as_deref() {
            if !dir.join(format!("touches-{symbol}.csv")).is_file() {
                let before = parts_by_day.len();
                parts_by_day.retain(|day, _| {
                    dir.join(day)
                        .join(format!("touches-{symbol}.csv"))
                        .is_file()
                });
                if parts_by_day.is_empty() {
                    eprintln!(
                            "bounce-grid: {symbol} — в кэше касаний нет ни одних суток корня, символ пропущен (--touches-cache-only)"
                        );
                    return false;
                }
                if parts_by_day.len() < before {
                    eprintln!(
                            "bounce-grid: {symbol} — суток без кэша касаний {} из {before}, пропущены (--touches-cache-only)",
                            before - parts_by_day.len()
                        );
                }
            }
        }
    }
    true
}

/// Кэш подходов обязан нести колонки, нужные запросу (T-28, TK-012, TK-025): иначе отказ, не пустые клетки.
pub(super) fn ensure_approach_cache(
    args: &BounceGridArgs,
    sets: &[FilterSet],
    symbol: &str,
    dir: &std::path::Path,
    days: &[DayTouches],
) -> anyhow::Result<()> {
    // T-35: `frontrun_min=` на подходах читает `frontrun_lots_at_arm` (T-28); в старом кэше колонки нет
    // (читается как −1) — отказ, а не молчаливый ноль сигналов.
    anyhow::ensure!(
                !sets.iter().any(|s| s.frontrun_min_lots.is_some())
                    || days.iter().all(|d| d.touches.iter().all(|t| t.frontrun_lots >= 0)),
                "{symbol}: `frontrun_min=` на подходах — нужен кэш с колонкой frontrun_lots_at_arm (T-28, `{}`)",
                dir.display()
            );
    // Г-07: `stack_min=` на подходах читает `stack_levels_at_arm` (те же колонки T-28, что
    // `frontrun_lots_at_arm`); старый кэш без них — отказ, а не молчаливый ноль сигналов.
    anyhow::ensure!(
                !sets.iter().any(|s| s.stack_min.is_some())
                    || days.iter().all(|d| d.touches.iter().all(|t| t.frontrun_lots >= 0)),
                "{symbol}: `stack_min=` на подходах — нужен кэш с колонкой stack_levels_at_arm (T-28, `{}`)",
                dir.display()
            );
    // TK-012: `--p08-cols` читает колонки `ArmP08` кэша подходов; старый кэш без них — отказ,
    // а не пустые клетки.
    anyhow::ensure!(
                !args.p08_cols
                    || days.iter().all(|d| {
                        d.approaches
                            .as_deref()
                            .is_some_and(|ap| ap.iter().all(|a| a.p08.is_some()))
                    }),
                "{symbol}: --p08-cols — нужен кэш подходов с колонками traded_lots_at_arm… (TK-012, `{}`), пересчитайте `lob touches --approach-bps`",
                dir.display()
            );
    // TK-025: колонки R1 — для `--r1-cols` и для ключей `r1_*` наборов; старый кэш без них —
    // отказ, а не пустые клетки и не молчаливый ноль сигналов (читатель даёт `r1 = Some` ровно
    // тогда, когда колонки в файле есть).
    anyhow::ensure!(
                !(args.r1_cols || sets.iter().any(FilterSet::uses_r1))
                    || days.iter().all(|d| {
                        d.approaches
                            .as_deref()
                            .is_some_and(|ap| ap.iter().all(|a| a.r1.is_some()))
                    }),
                "{symbol}: --r1-cols / ключи r1_* — нужен кэш подходов с колонками R1 (TK-025, `{}`), пересчитайте `lob touches --approach-bps --r1-cols`",
                dir.display()
            );
    Ok(())
}

/// Реплей книги вместо кэша: касания, `ret_*` и ряд σ середины.
pub(super) fn replay_days(
    args: &BounceGridArgs,
    symbol: &str,
    cfg_levels: LevelsConfig,
) -> anyhow::Result<(Vec<DayTouches>, SigmaSeries)> {
    let replay =
        replay_symbol_touches_and_second_mids(&args.root, symbol, cfg_levels, args.carry_age)?;
    let mut all = Vec::with_capacity(replay.days.iter().map(|d| d.mids.len()).sum());
    for d in &replay.days {
        all.extend(d.mids.iter().copied());
    }
    let days = replay
        .days
        .into_iter()
        .map(|d| DayTouches {
            rets: d
                .touches
                .iter()
                .map(|t| {
                    std::array::from_fn(|k| {
                        super::super::touches::pre_touch_return_bps_csv(
                            &d.mids,
                            t.start_ms,
                            super::super::touches::PRE_TOUCH_MS[k],
                        )
                    })
                })
                .collect(),
            day: d.day,
            touches: d.touches,
            approaches: None,
        })
        .collect();
    Ok((days, SigmaSeries::from_mids(&all)))
}

/// Окна сетапов суток: из кэша символ-суток (`--extra-runs`) или строятся для своих `t0` и кэшированных.
pub(super) fn day_signal_windows(
    mut shared: Option<&mut SharedEvents>,
    args: &BounceGridArgs,
    day: &DayTouches,
    rows: DayRows<'_>,
    carry_key: &super::CarryKey,
    tick: f64,
    lot: f64,
) -> anyhow::Result<Option<Arc<SignalWindows>>> {
    let own_t0s: Vec<i64> = day
        .touches
        .iter()
        .map(|t| t.start_ms.saturating_mul(1_000_000))
        .collect();
    let cache_ok = shared.is_some() && !args.windows_check;
    let cached = if cache_ok {
        shared.as_ref().and_then(|s| {
            s.windows
                .get(carry_key)
                .filter(|e| e.1 == tick.to_bits() && e.2 == lot.to_bits())
                .cloned()
        })
    } else {
        None
    };
    Ok(match cached {
        Some((t0s, _, _, w)) if own_t0s.iter().all(|t| t0s.binary_search(t).is_ok()) => {
            eprintln!(
                "bounce-grid:   окна: из кэша символ-суток ({} снимков)",
                w.len()
            );
            Some(w)
        }
        other => {
            let known: &[i64] = other.as_ref().map_or(&[], |e| e.0.as_slice());
            let pre: &[i64] = shared
                .as_ref()
                .and_then(|s| s.pre_t0.get(&day.day))
                .map_or(&[], Vec::as_slice);
            let mut all: Vec<i64> = own_t0s.iter().chain(known).chain(pre).copied().collect();
            all.sort_unstable();
            all.dedup();
            let w =
                day_windows(rows, &all, args.driver, tick, lot, args.windows_check)?.map(Arc::new);
            if let (true, Some(w), Some(s)) = (cache_ok, w.as_ref(), shared.as_mut()) {
                s.windows.insert(
                    carry_key.clone(),
                    (all, tick.to_bits(), lot.to_bits(), w.clone()),
                );
            }
            w
        }
    })
}

/// Размеры ордеров по касаниям суток; со сменой шага (В-172) — кратно лоту на момент касания.
pub(super) fn day_order_qtys(
    sizing: &OrderSizing,
    day: &DayTouches,
    step_schedule: Option<&StepSchedule>,
    tick_e9: i64,
    qty_mult: u32,
) -> Vec<f64> {
    let mut order_qtys = sizing.touch_qtys(&day.touches, tick_e9, qty_mult);
    if let Some(sc) = step_schedule {
        // В-172: размер ордера кратен лоту, действовавшему в момент касания (вверх — минимум не нарушаем).
        for (q, t) in order_qtys.iter_mut().zip(&day.touches) {
            let lot_e9 = sc.at(t.start_ms.saturating_mul(1_000_000)).1;
            if lot_e9 > 0 {
                #[allow(clippy::cast_possible_truncation, clippy::cast_precision_loss)]
                let q_e9 = (*q * 1e9).round() as i64;
                let steps = (q_e9 + lot_e9 - 1) / lot_e9;
                *q = (steps.saturating_mul(lot_e9)) as f64 / 1e9;
            }
        }
    }
    order_qtys
}

/// Строки stderr по итогам суток: пересчёты горизонта, пропуски удержания, счётчики быстрых путей, память кругов.
pub(super) fn day_diagnostics(
    flags: &RunFlags,
    retries_before: u64,
    skips_before: u64,
    memos: &[Mutex<RoundMemo>],
) {
    // Р6: пересчёты кругов из-за короткого горизонта развёртки — строка только когда были
    // (прежний stderr не меняется); на итог не влияют, только на время.
    let retries = crate::lob::backtest::HORIZON_RETRIES
        .load(std::sync::atomic::Ordering::Relaxed)
        .saturating_sub(retries_before);
    if retries > 0 {
        eprintln!("bounce-grid:   горизонт развёртки: пересчётов кругов {retries}");
        if flags.attempt_stats {
            use crate::lob::backtest::{ATTEMPT_ROWS, ATTEMPT_RUNS, ATTEMPT_USED};
            let g = |a: &[std::sync::atomic::AtomicU64]| {
                a.iter()
                    .map(|x| x.load(std::sync::atomic::Ordering::Relaxed).to_string())
                    .collect::<Vec<_>>()
                    .join("/")
            };
            eprintln!(
                        "bounce-grid:   попытки (0/1/2/3+, нарастающим итогом процесса): прогонов {} строк {}",
                        g(&ATTEMPT_RUNS),
                        g(&ATTEMPT_ROWS)
                    );
            eprintln!(
                "bounce-grid:   дошли по часам (local_ts <= now, 0/1/2/3+): {}",
                g(&ATTEMPT_USED)
            );
            eprintln!(
                        "bounce-grid:   строки движка по состоянию (до входа/живая заявка/позиция без заявок/вне шага, обе стороны): {}",
                        g(&crate::lob::backtest::ROW_CLASSES)
                    );
            eprintln!(
                "bounce-grid:   шаги кругов (без заявок/с заявками/прыжок, нарастающим итогом): {}",
                g(&crate::lob::backtest::STEP_KINDS)
            );
            eprintln!(
                        "bounce-grid:   шаги удержания по подписи (быстрый путь: всего/та же/сменилась/нет; движок: всего/та же/сменилась/нет): {}",
                        g(&crate::lob::backtest::SIG_STATS)
                    );
            eprintln!(
                "bounce-grid:   пропущено вызовов on_event (входы те же): {}",
                crate::lob::backtest::SIG_SKIPS.load(std::sync::atomic::Ordering::Relaxed)
            );
            eprintln!(
                        "bounce-grid:   классы обновлений глубины (сдвиг лучшей/на лучшей/на стене/1..3 от лучшей/прочее/±3 от стены): {}",
                        g(&crate::lob::backtest::fast_depth::DEPTH_ROW_CLASS)
                    );
            for (name, row) in ["вход", "выход-лимит"]
                .iter()
                .zip(&crate::lob::backtest::fast_depth::ORDER_DIST)
            {
                eprintln!(
                            "bounce-grid:   заявки {name} по удалению от лучшей (пересекает/0/1..3/4..10/>10 тиков): {}",
                            g(row)
                        );
            }
            let pr = hftbacktest::prof::snapshot();
            if pr.iter().any(|(c, _)| *c > 0) {
                eprintln!(
                            "bounce-grid:   rdtsc (Мцикл/вызовов; книга биржи, exch.process, local.process, копия строк, окно-движок): {}",
                            pr[..5]
                                .iter()
                                .map(|(c, n)| format!("{}/{n}", c / 1_000_000))
                                .collect::<Vec<_>>()
                                .join(" ")
                        );
            }
            eprintln!(
                "bounce-grid:   строки глубины по удалению от лучшей (≤3/≤10/≤30/дальше тиков): {}",
                g(&crate::lob::backtest::fast_depth::DEPTH_ROW_BANDS)
            );
        }
    }
    // Э-04б: строка только при `--hold-step skip` (прежний stderr не меняется).
    let skips = crate::lob::backtest::HOLD_SKIPS
        .load(std::sync::atomic::Ordering::Relaxed)
        .saturating_sub(skips_before);
    if skips > 0 {
        eprintln!("bounce-grid:   удержание: пропусков пустых шагов {skips}");
    }
    if crate::lob::backtest::fast_hold::fast_hold_on() {
        use crate::lob::backtest::fast_hold::{FAST_FALLBACKS, FAST_ROUNDS, FAST_ROWS};
        use std::sync::atomic::Ordering::Relaxed;
        eprintln!(
                    "bounce-grid:   быстрый путь удержания (с начала процесса): кругов {}, отказов {}, строк ленты {}",
                    FAST_ROUNDS.load(Relaxed),
                    FAST_FALLBACKS.load(Relaxed),
                    FAST_ROWS.load(Relaxed)
                );
    }
    if flags.tick_stats {
        use crate::lob::backtest::{QUANT_CALLS, QUANT_SLOW};
        use std::sync::atomic::Ordering::Relaxed;
        eprintln!(
                    "bounce-grid:   тик/лот без деления (Э-17; по уже закрытым книгам, нарастающим итогом): вызовов {}, откатов на деление {}",
                    QUANT_CALLS.load(Relaxed),
                    QUANT_SLOW.load(Relaxed)
                );
    }
    if !memos.is_empty() {
        let (hits, misses) = memos.iter().fold((0u64, 0u64), |(h, m), x| {
            let (a, b) = x.lock().map(|g| g.stats()).unwrap_or((0, 0));
            (h + a, m + b)
        });
        eprintln!("bounce-grid:   память кругов: из памяти {hits}, посчитано движком {misses}");
    }
}
