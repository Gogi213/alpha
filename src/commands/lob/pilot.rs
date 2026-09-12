//! `lob pilot` — один код, два режима (таск 09, история 41).
//!
//! **`--debug`** (R78, D-ОТЛАДКА): весь пул из `instruments.csv` разом,
//! ≤ 5 минут, гейт **G-DEBUG**. Цепочка — `lob session` (сеть, весь пул) →
//! `lob verify` на каждый инструмент, с маркером сверки `verify-<SYMBOL>.status`
//! (интерфейс таска 07, `crate::lob::watch`; пишет его общая
//! `commands::lob::verify::verify_and_mark`, таск 26) → `lob levels`
//! в обоих режимах `H3` → `lob markout`. Результат несёт `debug` и не пишет
//! `runs.csv` — не данные ни для вердикта, ни для предрегистрации (история 13).
//!
//! **Боевой** (без `--debug`): два часа по §11 задачи, тем же кодом
//! (`process_instrument` ниже) — печатает ставку `H3` в обоих режимах, доли
//! исходов, `m` на 10 с с интервалом, Шарп против `lob power`, и число сессий
//! на профиль из ставки (`sessions_needed_for_profile`). **Не запускается
//! этим тасков против сети** — план (`docs/plan/PLAN.md`, D-ОТЛАДКА)
//! возвращает боевые окна только после гейта G-DEBUG, а `k` для `--h3-k`
//! ещё не назначен владельцем (`interfaces.md`, «Из таска 08»). Код и тесты
//! на синтетике — ниже; «за второй час» (§11) теперь фильтр, не открытый
//! пункт: `process_instrument` считает ставку/доли/`m`/`net` только по
//! хвосту в `battle_counted_tail_minutes(window_minutes)` минут от конца
//! выборки — половина длительности записи, не второй час фиксированно
//! (решение владельца 2026-09-12: пилот бывает короче двух часов) —
//! `counted_tail_cutoff_ms` ниже. Реплей самого символа (шаги `verify` →
//! `levels`×2 → `markout`) при этом не сводится к одному проходу:
//! `verify_and_mark`/`run_levels`/`run_markout` (`commands/lob/verify.rs`,
//! `commands/lob/levels.rs`, `commands/lob/markout.rs` — вне зоны этого
//! таска) возвращают только сводку (`VerifySummary`/`LevelsSummary`/
//! `MarkoutSummary`), не `Vec<LevelRecord>`/`Vec<MidSample>` — свести 4–5
//! проходов в один значило бы менять их публичные подписи, что запрещено
//! границами таска (см. CONCERNS).
//!
//! # `--debug`: полная цепочка G-DEBUG (часть 09(б))
//!
//! После `markout` для каждого символа — `lob profiles --allow-unverified`
//! один раз на весь пул (`run_profiles_and_backtest_chain`), затем `lob
//! backtest --debug` на каждый символ: сигналы — уровни из уже написанного
//! `levels-floor-<SYMBOL>.csv` (весь профиль — один `profile_id =
//! debug:<SYMBOL>`, разметка по семи осям — таск 12/16, не эта команда), RTT
//! — `probe-<SYMBOL>.csv`/`clock.csv` сессии, если есть, иначе флаги
//! `--median-rtt-ns`/`--p95-rtt-ns` без умолчания; лот —
//! `pick::order_size_22a` от полей `instruments.csv` пула и последней цены
//! из того же `levels-floor` файла (`resolve_backtest_rtt_ns`,
//! `compute_order_qty_e9`). Ни профили (`--allow-unverified`), ни бэктест
//! (нет `--runs-out` в его аргументах) не пишут `runs.csv` — тест
//! `debug_chain_after_process_instrument_backtests_and_never_writes_runs_csv`
//! ниже проверяет это на синтетике (долг ревью 09(а)).
//!
//! # Раскладка `lob session` — таск 19
//!
//! `lob session --root <dir>` пишет `<dir>/<SYMBOL>-<день>.binlog` (таск 19:
//! то же имя, часть 1, что `commands::record::day_file_path`) —
//! `replay_symbol`/`levels`/`markout`/`verify::verify_and_mark` находят
//! его напрямую префиксным поиском, без переименования: `process_instrument`
//! ниже зовёт их с `verify_root = marker_dir = session_dir`, отдельного
//! подкаталога `replay/` для этой пары больше нет (мост
//! `stage_session_for_replay` снят).
//!
//! `lob backtest --session-root` (`backtest.rs::run_backtest`) и обход
//! сессий у `profiles.rs`/`watch.rs` тоже находят `<SYMBOL>-<день>.binlog`
//! напрямую — все четыре читателя замкнуты на один резолвер,
//! `super::session_binlog_for` (таск 19, часть 2); отдельного алиаса без
//! даты для них больше не требуется, `alias_dated_binlogs_for_legacy_readers`
//! снята. `copy_pool_instruments_csv` ниже остаётся: копия
//! `instruments.csv` пула в `session_dir` нужна режиму `floor`
//! (`resolve_h3_mode`) и подсчёту лота (`compute_order_qty_e9`) — про имя
//! бинлога она не знает и не заменяет собой резолвер.

use std::path::PathBuf;

use clap::Args;

use crate::bybit::rest::BYBIT_MAINNET_URL;
use crate::lob::costs::{mean_net_bps, Observation, MAKER_FEE_BPS, TAKER_FEE_BPS};
use crate::lob::final_metrics::sharpe_ratio;
use crate::lob::runs::log_pilot_run;
use crate::lob::shortlist::CONFIRM_MIN_N;
use crate::stats::{count_f64, count_u64};

use super::session::{run_session, SessionArgs};
use super::{DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS};

mod chain;
mod gates;
mod inputs;
mod k_grid;
mod metrics;

pub use gates::{
    g0_verdict, power_b_gap, sessions_needed_for_profile, stage2_preregistration_skeleton,
};
pub use k_grid::{choose_k, summarize_k_grid, KGridInstrumentRow, KGridSummaryRow, K_GRID};
pub use metrics::{
    battle_counted_tail_minutes, process_instrument, InstrumentMetrics, StepFailure,
};

use chain::{run_profiles_and_backtest_chain, write_debug_summary_csv};
use inputs::{copy_pool_instruments_csv, read_pool_symbols};
use k_grid::{format_k_grid_lines, k_grid_for_instrument};
use metrics::{format_instrument_line, format_opt, format_opt_bps, resolve_battle_window_minutes};

// Только для `tests.rs` (`use super::*`): имена, которые сам модуль после
// разбиения не зовёт, а тесты — зовут.
#[cfg(test)]
use super::{H3Args, H3ModeArg};
#[cfg(test)]
use chain::DEBUG_CHAIN_RUNS_SENTINEL;
#[cfg(test)]
use inputs::resolve_backtest_rtt_ns;

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

/// Аргументы `lob pilot`. Общие поля идут в обоих режимах; `--debug`
/// переключает на весь пул из `--pool-instruments` (сеть, `lob session`),
/// без флага — боевой путь на уже записанных данных под `--root`.
#[derive(Debug, Args)]
pub struct PilotArgs {
    /// Отладочный режим (R78): весь пул, ≤ 5 минут, `debug`-артефакты,
    /// без `runs.csv`. Без флага — боевой путь (история 41).
    #[arg(long, default_value_t = false)]
    pub debug: bool,
    /// `instruments.csv` пула (`lob pick`) — обязателен в обоих режимах:
    /// список кандидатов и колонка `h3_lots` для режима `floor`.
    #[arg(long)]
    pub pool_instruments: Option<PathBuf>,
    /// Корень: в `--debug` — куда положить `session/`, `replay/` и
    /// сводный артефакт пилота; в боевом — уже записанные суточные файлы
    /// пула (тот же `--root`, что был у `lob session`/`lob record`).
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Длина сессии в минутах — только `--debug`. Без умолчания: бюджет
    /// всей команды — 5 минут разом (сессия плюс сверка плюс разметка),
    /// сколько из них отдать сессии — решает вызывающий (CLAUDE.md: живой
    /// прогон ≤ 5 минут), не эта команда.
    #[arg(long)]
    pub minutes: Option<u64>,
    /// REST-хост Bybit v5 для замера часов сессии — только `--debug`.
    #[arg(long, default_value = BYBIT_MAINNET_URL)]
    pub base_url: String,
    /// NTP-эталон для того же замера — только `--debug`.
    #[arg(long, default_value = "pool.ntp.org:123")]
    pub ntp_addr: String,
    /// Запасная длина боевого окна в часах — только когда в `--root` ещё
    /// нет `session.json` (`resolve_battle_window_minutes`). Обычный боевой
    /// путь читает длину из самой сессии — `session.json.pilot_minutes`
    /// (`--pilot-minutes` у `lob session`, например 30 — не выражается
    /// целыми часами) или `duration_s / 60`; этот флаг её не перекрывает.
    #[arg(long, default_value_t = 2)]
    pub hours: u64,
    /// Прогрев в мс: только режим `percentile` внутри `H3` (`floor` не
    /// читает) — тот же смысл, что у `levels`/`markout`.
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
    /// Журнал прогонов (канонический путь шага 7.1) — боевой путь пишет
    /// строку на инструмент; `--debug` этот файл не трогает вовсе.
    #[arg(long, default_value = "docs/plan/runs.csv")]
    pub runs_out: PathBuf,
    /// Момент строки журнала/отчёта UTC (по умолчанию — сейчас).
    #[arg(long)]
    pub now_utc: Option<String>,
    /// Полная таблица кандидатов (`lob pick`, таск 08) — источник
    /// `coverage_top50_bps` для шага `lob profiles` внутри `--debug`-цепочки
    /// (`interfaces.md`, «Из таска 10»). Без флага — тот же путь по
    /// умолчанию, что у `lob profiles` само́й.
    #[arg(long, default_value = "docs/plan/candidates.csv")]
    pub candidates_csv: PathBuf,
    /// Медианная RTT исполнения для шага `lob backtest` внутри
    /// `--debug`-цепочки — только запасной путь, когда в каталоге сессии нет
    /// ни `probe-<symbol>.csv` (`lob probe`), ни `clock.csv`
    /// (`resolve_backtest_rtt_ns`). Без умолчания: изобретённое число
    /// запрещено (§9 плана).
    #[arg(long)]
    pub median_rtt_ns: Option<i64>,
    /// 95-й перцентиль той же запасной RTT — см. `median_rtt_ns`.
    #[arg(long)]
    pub p95_rtt_ns: Option<i64>,
}

/// Итог `lob pilot` для печати диспетчером (`commands/lob/mod.rs::dispatch`,
/// вне зоны таска 09 — форма сохранена, чтобы дозапрос дошёл без правки
/// чужого файла). В `--debug` поля несут агрегат по всему пулу, не по
/// одному символу: `symbol` — список через запятую, `n_pulled` — сумма
/// `pulled` по пулу, `runs_out` — путь сводного артефакта пилота (не
/// `runs.csv`: `--debug` его не пишет).
#[derive(Debug)]
pub struct PilotSummary {
    pub symbol: String,
    pub n_pulled: u64,
    pub mean_m_10s_bps: f64,
    pub mean_net_bps: f64,
    pub verdict: String,
    pub runs_out: PathBuf,
}

// ---------------------------------------------------------------------------
// `--debug`: весь пул, сеть, ≤ 5 минут (R78).
// ---------------------------------------------------------------------------

fn run_pilot_debug(args: &PilotArgs) -> anyhow::Result<PilotSummary> {
    let pool_instruments = args.pool_instruments.as_ref().ok_or_else(|| {
        anyhow::anyhow!("--debug требует --pool-instruments (instruments.csv последнего lob pick)")
    })?;
    let minutes = args.minutes.ok_or_else(|| {
        anyhow::anyhow!(
            "--debug требует --minutes: бюджет всей команды — 5 минут разом \
             (сессия плюс сверка плюс разметка), длину сессии называет вызывающий"
        )
    })?;
    std::fs::create_dir_all(&args.root)?;
    let session_dir = args.root.join("session");

    let session_summary = run_session(&SessionArgs {
        pool_instruments: Some(pool_instruments.clone()),
        all_instruments: false,
        root: session_dir.clone(),
        minutes: Some(minutes),
        pilot_minutes: None,
        always_on: false,
        base_url: args.base_url.clone(),
        ntp_addr: args.ntp_addr.clone(),
    })?;
    copy_pool_instruments_csv(&session_dir, pool_instruments)?;

    let mut reports: Vec<(String, Result<InstrumentMetrics, StepFailure>)> = Vec::new();
    for symbol in &session_summary.instruments {
        let outcome = process_instrument(
            &session_dir,
            &session_dir,
            symbol,
            args.warmup_ms,
            args.repeat_window_ms,
            minutes as f64,
            None,
        );
        reports.push((symbol.clone(), outcome));
    }

    let mut all_net_obs: Vec<Observation> = Vec::new();
    let mut all_m10s_means: Vec<f64> = Vec::new();
    let mut total_pulled = 0usize;
    let mut first_defect: Option<String> = None;
    let mut lines = Vec::new();
    for (symbol, outcome) in &reports {
        match outcome {
            Ok(m) => {
                lines.push(format!("pilot debug: {}", format_instrument_line(m)));
                if !m.verify_ok && first_defect.is_none() {
                    first_defect = Some(format!(
                        "{symbol}: verify — сверка не прошла целиком ({})",
                        m.verify_summary_line
                    ));
                }
                total_pulled += m.pulled_count;
                all_net_obs.extend(m.net_observations.iter().copied());
                if let Some(mean) = m.m10s_mean_bps {
                    all_m10s_means.push(mean);
                }
            }
            Err(f) => {
                lines.push(format!("pilot debug: {symbol} — упал на {f}"));
                if first_defect.is_none() {
                    first_defect = Some(format!("{symbol}: {f}"));
                }
            }
        }
    }
    for line in &lines {
        println!("{line}");
    }

    // Сетка k (таск 18, В-30/D05): один реплей на инструмент
    // (`replay_symbol_over_configs`, не пять), ставка/eaten по каждому k
    // из `K_GRID`; в `--debug` окно короче часа — ставка экстраполирована
    // (метка `[debug]` в печати), выбор здесь не является боевым вердиктом.
    let mut k_grid_per_instrument: Vec<(String, Vec<KGridInstrumentRow>)> = Vec::new();
    for symbol in &session_summary.instruments {
        match k_grid_for_instrument(
            &session_dir,
            symbol,
            args.repeat_window_ms,
            minutes as f64,
            None,
        ) {
            Ok(rows) => k_grid_per_instrument.push((symbol.clone(), rows)),
            Err(e) => println!("pilot k-grid: {symbol} — упал: {e}"),
        }
    }
    if !k_grid_per_instrument.is_empty() {
        let k_grid_summary = summarize_k_grid(&k_grid_per_instrument);
        for line in format_k_grid_lines(&k_grid_per_instrument, &k_grid_summary, true) {
            println!("{line}");
        }
    }

    // Хвост цепочки G-DEBUG: profiles по всему пулу, затем backtest на
    // сигналах каждого символа (шаг 09(б)) — первый дефект тут учитывается
    // только если per-instrument цепочка выше уже прошла без дефекта (тот
    // же принцип «первый дефект побеждает», что и в цикле над `reports`).
    let now = args
        .now_utc
        .clone()
        .unwrap_or_else(|| chrono::Utc::now().to_rfc3339());
    let (chain_lines, chain_defect) = run_profiles_and_backtest_chain(
        &args.root,
        &session_summary.instruments,
        args.warmup_ms,
        args.repeat_window_ms,
        &args.candidates_csv,
        (args.median_rtt_ns, args.p95_rtt_ns),
        &now,
    );
    for line in &chain_lines {
        println!("{line}");
    }
    if first_defect.is_none() {
        first_defect = chain_defect;
    }

    let verdict = match &first_defect {
        None => format!(
            "pilot debug: G-DEBUG-цепочка session -> verify -> levels -> markout -> profiles -> backtest прошла без дефекта на {}/{} инструментах",
            reports.iter().filter(|(_, o)| o.is_ok()).count(),
            reports.len()
        ),
        Some(defect) => format!(
            "pilot debug: цепочка упала на шаге {defect} (остальные шаги/инструменты обработаны независимо, см. строки выше)"
        ),
    };
    println!("{verdict}");

    let summary_out = args.root.join("pilot-debug-summary.csv");
    write_debug_summary_csv(&summary_out, &reports)?;

    let mean_m10s = if all_m10s_means.is_empty() {
        0.0
    } else {
        all_m10s_means.iter().sum::<f64>() / count_f64(all_m10s_means.len())
    };
    Ok(PilotSummary {
        symbol: session_summary.instruments.join(","),
        n_pulled: count_u64(total_pulled),
        mean_m_10s_bps: mean_m10s,
        mean_net_bps: mean_net_bps(&all_net_obs).unwrap_or(0.0),
        verdict,
        runs_out: summary_out,
    })
}

// ---------------------------------------------------------------------------
// Боевой путь (§11): код и тесты на синтетике — не запускается этим таском
// (G-DEBUG не пройден, `k` не назначен владельцем; `interfaces.md`).
// ---------------------------------------------------------------------------

fn run_pilot_battle(args: &PilotArgs) -> anyhow::Result<PilotSummary> {
    let pool_instruments = args
        .pool_instruments
        .as_ref()
        .ok_or_else(|| anyhow::anyhow!("боевой пилот требует --pool-instruments"))?;
    let symbols = read_pool_symbols(pool_instruments)?;
    let window_minutes = resolve_battle_window_minutes(&args.root, args.hours);
    let now = args
        .now_utc
        .clone()
        .unwrap_or_else(|| chrono::Utc::now().to_rfc3339());

    let mut all_net_obs: Vec<Observation> = Vec::new();
    let mut levels_per_hour_floor: Vec<f64> = Vec::new();
    let mut all_m10s_means: Vec<f64> = Vec::new();
    let mut total_pulled = 0usize;
    for symbol in &symbols {
        let m = process_instrument(
            &args.root,
            &args.root,
            symbol,
            args.warmup_ms,
            args.repeat_window_ms,
            window_minutes,
            Some(battle_counted_tail_minutes(window_minutes)),
        )
        .map_err(|f| anyhow::anyhow!("{symbol}: {f}"))?;
        println!("pilot battle: {}", format_instrument_line(&m));
        let per_hour_floor = m.levels_per_min_floor * 60.0;
        levels_per_hour_floor.push(per_hour_floor);
        total_pulled += m.pulled_count;
        all_net_obs.extend(m.net_observations.iter().copied());
        if let Some(mean) = m.m10s_mean_bps {
            all_m10s_means.push(mean);
        }
        let sessions = sessions_needed_for_profile(
            m.levels_per_min_floor * m.share_pulled,
            15.0,
            CONFIRM_MIN_N,
        );
        println!(
            "pilot battle: {symbol} marginal:outcome=pulled — сессий по 15 мин на профиль: {}",
            sessions
                .map(|n| n.to_string())
                .unwrap_or_else(|| "n/a (ставка ноль)".to_string())
        );
        log_pilot_run(
            &args.runs_out,
            symbol,
            &now,
            &format!(
                "battle rate_floor_per_hour={per_hour_floor:.3} eaten={:.3} pulled={:.3} mixed={:.3} m10s_n={} m10s_mean={} sharpe={} sessions_pulled_profile={}",
                m.share_eaten,
                m.share_pulled,
                m.share_mixed,
                m.m10s_n,
                format_opt_bps(m.m10s_mean_bps),
                format_opt(m.sharpe),
                sessions.map(|n| n.to_string()).unwrap_or_else(|| "n/a".to_string()),
            ),
        )
        .map_err(|e| anyhow::anyhow!("runs.csv: {e}"))?;
    }

    let pooled_m10s = if all_m10s_means.is_empty() {
        None
    } else {
        Some(all_m10s_means.iter().sum::<f64>() / count_f64(all_m10s_means.len()))
    };
    // G0 «edge» сравнивает `net` (издержки Decision 15 и проскальзывание уже
    // вычтены на каждое наблюдение — `process_instrument`/`net_observations`),
    // не сырой `m_10s`: см. doc `g0_verdict` (дозапрос по ревью таска 09(а)).
    let pooled_net = mean_net_bps(&all_net_obs);
    let g0 = g0_verdict(&levels_per_hour_floor, pooled_net);
    println!("pilot battle: {g0}");

    let power_summary =
        crate::commands::lob::power::run_power(&crate::commands::lob::power::PowerArgs {
            root: args.root.clone(),
            hour_tests: 0,
        })?;
    let measured_sharpe = pooled_m10s
        .and_then(|_| sharpe_ratio(&all_m10s_means))
        .unwrap_or(0.0);
    let gap = power_b_gap(measured_sharpe, power_summary.required_sharpe);
    println!(
        "pilot battle: G-POWER-B measured_sharpe={measured_sharpe:.4} required_sharpe={:.4} gap={gap:.4}",
        power_summary.required_sharpe
    );

    // Сетка k (таск 18, В-30/D05): один реплей на инструмент
    // (`replay_symbol_over_configs`), хвост «второй час» §11 — тот же
    // фильтр, что `process_instrument` уже применяет к остальным числам
    // боевого пути. Выбор здесь — вход предрегистрации этапа 2, не второй
    // вердикт G0/G1 (тот уже напечатан выше, по базовому floor-порогу пула).
    let mut k_grid_per_instrument: Vec<(String, Vec<KGridInstrumentRow>)> = Vec::new();
    for symbol in &symbols {
        match k_grid_for_instrument(
            &args.root,
            symbol,
            args.repeat_window_ms,
            window_minutes,
            Some(battle_counted_tail_minutes(window_minutes)),
        ) {
            Ok(rows) => k_grid_per_instrument.push((symbol.clone(), rows)),
            Err(e) => println!("pilot k-grid: {symbol} — упал: {e}"),
        }
    }
    let chosen_k = if k_grid_per_instrument.is_empty() {
        None
    } else {
        let k_grid_summary = summarize_k_grid(&k_grid_per_instrument);
        for line in format_k_grid_lines(&k_grid_per_instrument, &k_grid_summary, false) {
            println!("{line}");
        }
        choose_k(&k_grid_summary)
    };

    for line in stage2_preregistration_skeleton(chosen_k.map(|k| ("floor", k))).lines() {
        println!("pilot battle: {line}");
    }

    let verdict = format!("{g0}; G-POWER-B gap={gap:.4}");
    Ok(PilotSummary {
        symbol: symbols.join(","),
        n_pulled: count_u64(total_pulled),
        mean_m_10s_bps: pooled_m10s.unwrap_or(0.0),
        mean_net_bps: pooled_net.unwrap_or(0.0),
        verdict,
        runs_out: args.runs_out.clone(),
    })
}

// ---------------------------------------------------------------------------
// Точка входа.
// ---------------------------------------------------------------------------

pub fn run_pilot(args: &PilotArgs) -> anyhow::Result<PilotSummary> {
    if args.maker_fee_bps != MAKER_FEE_BPS || args.taker_fee_bps != TAKER_FEE_BPS {
        anyhow::bail!(
            "комиссии сменились (мейкер {} тейкер {} против H4 {MAKER_FEE_BPS}/{TAKER_FEE_BPS}): предположение H4 требует перепроверки, пилот остановлен",
            args.maker_fee_bps,
            args.taker_fee_bps
        );
    }
    if args.debug {
        run_pilot_debug(args)
    } else {
        run_pilot_battle(args)
    }
}

#[cfg(test)]
mod tests;
