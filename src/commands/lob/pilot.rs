//! `lob pilot` — один код, два режима (таск 09, история 41).
//!
//! **`--debug`** (R78, D-ОТЛАДКА): весь пул из `instruments.csv` разом,
//! ≤ 5 минут, гейт **G-DEBUG**. Цепочка — `lob session` (сеть, весь пул) →
//! `lob verify` на каждый инструмент, с маркером сверки `verify-<SYMBOL>.status`
//! (интерфейс таска 07, `crate::lob::watch`, пишет этот файл) → `lob levels`
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
//! на синтетике — ниже; посчитанное «за второй час» окно (§11: «считается
//! второй час») этой правкой **не фильтруется** — вся выборка под `--root`
//! идёт в замер целиком; хвостовой фильтр по времени — открытый пункт для
//! того, кто включит боевой прогон (см. `BATTLE_COUNTED_TAIL_MINUTES`).
//!
//! # Раскладка `lob session` против `replay_symbol`
//!
//! `lob session --root <dir>` пишет `<dir>/<SYMBOL>.binlog` — один файл без
//! суток (таск 04). `replay_symbol`/`levels`/`markout`/`bybit::verify::
//! run_verify` читают `<SYMBOL>-<день>[-pN].binlog` (таск 01). Мост —
//! `stage_session_for_replay`: копия (не перезапись) файлов сессии под
//! именем с сутками в отдельный подкаталог `replay/`, плюс копия
//! `instruments.csv` пула (нужна режиму `floor`). Оригинал сессии остаётся
//! нетронутым — маркер сверки пишется рядом с ним, как того требует
//! `interfaces.md` («Из таска 07»), а не рядом с копией.

use std::path::{Path, PathBuf};

use clap::Args;

use crate::bybit::rest::BYBIT_MAINNET_URL;
use crate::bybit::verify::{run_verify, VerifyArgs};
use crate::lob::costs::{
    mean_net_bps, net_fill_interval, FillObservation, Observation, MAKER_FEE_BPS,
    ROUNDTRIP_FEES_BPS, TAKER_FEE_BPS,
};
use crate::lob::final_metrics::sharpe_ratio;
use crate::lob::levels::{H3Mode, LevelsConfig, Outcome};
use crate::lob::markout::{base_before, markouts_for_level, MidSample, HORIZONS_MS};
use crate::lob::runs::log_pilot_run;
use crate::lob::shortlist::CONFIRM_MIN_N;
use crate::stats::{count_f64, count_f64_u64, count_u64, BOOTSTRAP_REPLICATIONS, GATE_ALPHA};

use super::levels::{resolve_h3_mode, run_levels, H3ModeArg, LevelsArgs};
use super::markout::{run_markout, MarkoutArgs};
use super::session::{run_session, SessionArgs};
use super::{replay_symbol, DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS, G0_MIN_PULLED};

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
    /// Часы боевого пилота (§11: два, считается второй) — только боевой
    /// путь; используется для ставки в час при печати.
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

fn is_positive_finite(x: f64) -> bool {
    x.is_finite() && x > 0.0
}

/// Число сессий, нужное профилю, чтобы набрать `CONFIRM_MIN_N` (100)
/// наблюдений при измеренной ставке (история 41, `PLAN.md` §2.4): `ceil(n_min
/// / (rate_per_min · session_minutes))`. `None` — ставка или длина сессии
/// неположительны (профиль на этой ставке не считается, а не делится на
/// ноль).
pub fn sessions_needed_for_profile(
    rate_per_min: f64,
    session_minutes: f64,
    n_min: u64,
) -> Option<u64> {
    if !is_positive_finite(rate_per_min) || !is_positive_finite(session_minutes) {
        return None;
    }
    let per_session = rate_per_min * session_minutes;
    if per_session <= 0.0 {
        return None;
    }
    #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
    let sessions = (count_f64_u64(n_min) / per_session).ceil() as u64;
    Some(sessions.max(1))
}

/// «Второй час» §11 — сколько минут с хвоста боевого прогона считаются в
/// замер. Плановое решение (`PLAN.md` §11, «два часа, считается второй»),
/// не изобретённое число: но фильтр по времени в `process_instrument`
/// этой правкой **не подключён** — открытый пункт, см. doc модуля и
/// `CONCERNS` таска 09.
pub const BATTLE_COUNTED_TAIL_MINUTES: u64 = 60;

// ---------------------------------------------------------------------------
// Общий замер одного инструмента: сверка, оба режима `H3`, markout — тем же
// кодом, что `verify`/`levels`/`markout` (шаги 0.6, 1.1, 1.2, 2.1).
// ---------------------------------------------------------------------------

/// Шаг цепочки, на котором замер инструмента остановился.
#[derive(Debug, Clone)]
pub struct StepFailure {
    pub step: &'static str,
    pub message: String,
}

impl std::fmt::Display for StepFailure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "шаг {}: {}", self.step, self.message)
    }
}

/// Итог замера одного инструмента: ставка `H3` в обоих режимах, доли
/// исходов, `m` на 10 с с интервалом, Шарп, и сырые наблюдения `net` —
/// для агрегата по пулу (`mean_net_bps` в `PilotSummary`).
#[derive(Debug)]
pub struct InstrumentMetrics {
    pub symbol: String,
    pub verify_ok: bool,
    pub verify_summary_line: String,
    pub levels_floor: usize,
    pub levels_percentile: usize,
    pub levels_per_min_floor: f64,
    pub levels_per_min_percentile: f64,
    pub share_eaten: f64,
    pub share_pulled: f64,
    pub share_mixed: f64,
    pub pulled_count: usize,
    pub m10s_n: usize,
    pub m10s_mean_bps: Option<f64>,
    pub m10s_lower_bps: Option<f64>,
    pub sharpe: Option<f64>,
    pub net_observations: Vec<Observation>,
}

/// Замеряет один инструмент цепочкой сверка → `H3` `floor` → `H3`
/// `percentile` → markout → доли исходов и `m` на 10 с из своего же реплея
/// (`replay_symbol`, тот же код, что `levels`/`markout`/`watch`/`mod.rs`).
///
/// `percentile` без владельческого измерения порога переиспользует то же
/// число, что `floor` (`h3_lots` из `instruments.csv`), а не изобретает
/// новое (правило 1 `interfaces.md`): на прогоне короче часа прогрева
/// результат всё равно `levels=0` независимо от конкретного порога (таск 02,
/// разведка `data/recon5`), так что выбор числа здесь на итог не влияет.
///
/// `verify_root` — каталог с `<SYMBOL>-<день>.binlog` (раскладка
/// `replay_symbol`); `marker_dir` — куда лечь `verify-<SYMBOL>.status`
/// (интерфейс таска 07): в `--debug` это исходный каталог сессии, в
/// боевом пути — тот же `verify_root`.
pub fn process_instrument(
    verify_root: &Path,
    marker_dir: &Path,
    symbol: &str,
    warmup_ms: i64,
    repeat_window_ms: i64,
    window_minutes: f64,
) -> Result<InstrumentMetrics, StepFailure> {
    debug_assert_eq!(
        HORIZONS_MS[2], 10_000,
        "индекс горизонта 10 с обязан указывать на 10 000 мс"
    );
    let step = |name: &'static str| {
        move |e: anyhow::Error| StepFailure {
            step: name,
            message: e.to_string(),
        }
    };

    let vs = run_verify(&VerifyArgs {
        symbol: symbol.to_string(),
        root: verify_root.to_path_buf(),
    })
    .map_err(step("verify"))?;
    // «Прошла сверку целиком» (`interfaces.md`, доку `watch.rs`) — по тому,
    // что файловый режим вообще может проверить (сверка по `u` — только
    // живой поток, здесь её нет): инварианты книги (тест 2) и то, что цена
    // сделки хоть раз держалась (тест 3). `trades_out_of_range`/
    // `trades_indeterminate` — отдельные, не булевы метрики, в вердикт
    // маркера не входят (та же трактовка, что `day_tallies` в `mod.rs`).
    let verify_ok =
        vs.sequence_gaps == 0 && vs.invariant_violations == 0 && vs.trades_violations == 0;
    let marker_path = marker_dir.join(format!("verify-{symbol}.status"));
    std::fs::write(&marker_path, if verify_ok { "ok" } else { "fail" }).map_err(|e| {
        StepFailure {
            step: "verify",
            message: format!("маркер {}: {e}", marker_path.display()),
        }
    })?;
    let verify_summary_line = format!(
        "files={} updates={} gaps={} invariants={} trades={} out_of_range={} violations={} indeterminate={}",
        vs.files,
        vs.updates_applied,
        vs.sequence_gaps,
        vs.invariant_violations,
        vs.trades_total,
        vs.trades_out_of_range,
        vs.trades_violations,
        vs.trades_indeterminate,
    );

    let floor_h3_lots = match resolve_h3_mode(verify_root, symbol, H3ModeArg::Floor, None)
        .map_err(step("levels_floor"))?
    {
        H3Mode::Floor { h3_lots } => h3_lots,
        H3Mode::Percentile { .. } => unreachable!("H3ModeArg::Floor всегда даёт H3Mode::Floor"),
    };

    let floor_summary = run_levels(&LevelsArgs {
        root: verify_root.to_path_buf(),
        symbol: symbol.to_string(),
        h3_mode: H3ModeArg::Floor,
        h3_lots: None,
        warmup_ms,
        repeat_window_ms,
        out: Some(verify_root.join(format!("levels-floor-{symbol}.csv"))),
    })
    .map_err(step("levels_floor"))?;

    let percentile_summary = run_levels(&LevelsArgs {
        root: verify_root.to_path_buf(),
        symbol: symbol.to_string(),
        h3_mode: H3ModeArg::Percentile,
        h3_lots: Some(floor_h3_lots),
        warmup_ms,
        repeat_window_ms,
        out: Some(verify_root.join(format!("levels-percentile-{symbol}.csv"))),
    })
    .map_err(step("levels_percentile"))?;

    // Markout считается тем же кодом, что подтверждающий прогон
    // (`run_markout`, разведочный режим — `--confirmatory` требует флагов
    // готовности, которых у пилота ещё нет); артефакт на диске — то, что
    // «шапка каждого артефакта несёт debug» проверяет по `levels-*.csv`
    // (см. CONCERNS: `markout.rs` такой шапки не пишет вовсе, вне зоны).
    run_markout(&MarkoutArgs {
        root: verify_root.to_path_buf(),
        symbol: symbol.to_string(),
        h3_mode: H3ModeArg::Floor,
        h3_lots: None,
        warmup_ms,
        repeat_window_ms,
        out: Some(verify_root.join(format!("markout-{symbol}.csv"))),
        confirmatory: false,
        flag: None,
        median_lifetime_ms: None,
        profile: None,
    })
    .map_err(step("markout"))?;

    let cfg = LevelsConfig {
        mode: H3Mode::Floor {
            h3_lots: floor_h3_lots,
        },
        warmup_ms,
        repeat_window_ms,
    };
    let replay = replay_symbol(verify_root, symbol, cfg).map_err(step("markout"))?;

    let mut eaten = 0usize;
    let mut pulled = 0usize;
    let mut mixed = 0usize;
    let mut m10s: Vec<f64> = Vec::new();
    let mut net_observations: Vec<Observation> = Vec::new();
    for day in &replay.days {
        for rec in &day.records {
            match rec.outcome() {
                Outcome::Eaten => eaten += 1,
                Outcome::Pulled => pulled += 1,
                Outcome::Mixed => mixed += 1,
            }
            let Some(m) = markouts_for_level(rec, &day.mids)[2] else {
                continue;
            };
            m10s.push(m);
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
            if let Some(x) = exit {
                net_observations.push(Observation {
                    m_bps: m,
                    spread_ticks_exit: x.ask_tick - x.bid_tick,
                    mid2x_base: base2x,
                });
            }
        }
    }
    let total = eaten + pulled + mixed;
    let share_of = |n: usize| {
        if total > 0 {
            count_f64(n) / count_f64(total)
        } else {
            0.0
        }
    };

    // Интервал на `m` на 10 с — тот же совместный бутстрап, что `net_fill`
    // (`costs::net_fill_interval`), с `filled = true` на каждом наблюдении:
    // вторая серия вырождается в константу 1, и совместный ресэмплинг пары
    // сводится к обычному Уэббовскому интервалу среднего одной серии —
    // расширения `disjoint_contrast`, а не второй бутстрап (`interfaces.md`,
    // «Из таска 03»: «второй ресэмплер не писать»). Сутки — кластер, тот
    // же смысл, что везде в `stats`/`cells`.
    let interval_obs: Vec<FillObservation> = replay
        .days
        .iter()
        .enumerate()
        .flat_map(|(day_idx, day)| {
            day.records.iter().filter_map(move |rec| {
                markouts_for_level(rec, &day.mids)[2].map(|m| FillObservation {
                    day_cluster: day_idx as i64,
                    net_bps: m,
                    filled: true,
                })
            })
        })
        .collect();
    let interval = net_fill_interval(&interval_obs, GATE_ALPHA, BOOTSTRAP_REPLICATIONS, 0);

    let window_minutes = if window_minutes > 0.0 {
        window_minutes
    } else {
        f64::INFINITY
    };
    Ok(InstrumentMetrics {
        symbol: symbol.to_string(),
        verify_ok,
        verify_summary_line,
        levels_floor: floor_summary.levels,
        levels_percentile: percentile_summary.levels,
        levels_per_min_floor: count_f64(floor_summary.levels) / window_minutes,
        levels_per_min_percentile: count_f64(percentile_summary.levels) / window_minutes,
        share_eaten: share_of(eaten),
        share_pulled: share_of(pulled),
        share_mixed: share_of(mixed),
        pulled_count: pulled,
        m10s_n: m10s.len(),
        m10s_mean_bps: interval.map(|i| i.point_bps),
        m10s_lower_bps: interval.map(|i| i.lower_bps),
        sharpe: sharpe_ratio(&m10s),
        net_observations,
    })
}

fn format_opt_bps(v: Option<f64>) -> String {
    v.map(|x| format!("{x:.3}bps"))
        .unwrap_or_else(|| "n/a".to_string())
}

fn format_opt(v: Option<f64>) -> String {
    v.map(|x| format!("{x:.3}"))
        .unwrap_or_else(|| "n/a".to_string())
}

/// Строка "по инструменту", общая для `--debug` и боевого пути.
fn format_instrument_line(m: &InstrumentMetrics) -> String {
    format!(
        "symbol={} rate_floor={:.3}/min rate_percentile={:.3}/min levels_floor={} levels_percentile={} eaten={:.3} pulled={:.3} mixed={:.3} m10s(n={}, mean={}, lower={}) sharpe={} verify={}",
        m.symbol,
        m.levels_per_min_floor,
        m.levels_per_min_percentile,
        m.levels_floor,
        m.levels_percentile,
        m.share_eaten,
        m.share_pulled,
        m.share_mixed,
        m.m10s_n,
        format_opt_bps(m.m10s_mean_bps),
        format_opt_bps(m.m10s_lower_bps),
        format_opt(m.sharpe),
        if m.verify_ok { "ok" } else { "fail" },
    )
}

// ---------------------------------------------------------------------------
// `--debug`: весь пул, сеть, ≤ 5 минут (R78).
// ---------------------------------------------------------------------------

/// Символ из `instruments.csv`: колонка `symbol` (единственный читатель —
/// `pick::instruments_csv_reader`, терпит метку `debug` первой строкой).
#[derive(Debug, serde::Deserialize)]
struct PoolSymbolRow {
    symbol: String,
}

fn read_pool_symbols(instruments_csv: &Path) -> anyhow::Result<Vec<String>> {
    let mut r = super::pick::instruments_csv_reader(instruments_csv).map_err(|e| {
        anyhow::anyhow!(
            "{}: {e} — lob pilot читает пул из instruments.csv",
            instruments_csv.display()
        )
    })?;
    let mut out = Vec::new();
    for row in r.deserialize::<PoolSymbolRow>() {
        out.push(row?.symbol);
    }
    anyhow::ensure!(!out.is_empty(), "{}: пул пуст", instruments_csv.display());
    Ok(out)
}

/// Мост между раскладкой `lob session` (`<SYMBOL>.binlog`, без суток) и
/// раскладкой `replay_symbol`/`verify`/`levels`/`markout`
/// (`<SYMBOL>-<день>.binlog`): копия каждого файла пула плюс
/// `instruments.csv` (нужен режиму `floor`). Оригиналы сессии не трогает.
fn stage_session_for_replay(
    session_dir: &Path,
    replay_dir: &Path,
    pool_instruments: &Path,
    day: &str,
    symbols: &[String],
) -> anyhow::Result<()> {
    std::fs::create_dir_all(replay_dir)?;
    std::fs::copy(pool_instruments, replay_dir.join("instruments.csv"))
        .map_err(|e| anyhow::anyhow!("копия instruments.csv в {}: {e}", replay_dir.display()))?;
    for symbol in symbols {
        let src = session_dir.join(format!("{symbol}.binlog"));
        let dst = replay_dir.join(format!("{symbol}-{day}.binlog"));
        std::fs::copy(&src, &dst)
            .map_err(|e| anyhow::anyhow!("копия {} -> {}: {e}", src.display(), dst.display()))?;
    }
    Ok(())
}

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
    let replay_dir = args.root.join("replay");

    let session_summary = run_session(&SessionArgs {
        pool_instruments: pool_instruments.clone(),
        root: session_dir.clone(),
        minutes,
        base_url: args.base_url.clone(),
        ntp_addr: args.ntp_addr.clone(),
    })?;
    let day = session_summary
        .started_utc
        .get(..10)
        .unwrap_or("1970-01-01")
        .to_string();
    stage_session_for_replay(
        &session_dir,
        &replay_dir,
        pool_instruments,
        &day,
        &session_summary.instruments,
    )?;

    let mut reports: Vec<(String, Result<InstrumentMetrics, StepFailure>)> = Vec::new();
    for symbol in &session_summary.instruments {
        let outcome = process_instrument(
            &replay_dir,
            &session_dir,
            symbol,
            args.warmup_ms,
            args.repeat_window_ms,
            minutes as f64,
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
    let verdict = match &first_defect {
        None => format!(
            "pilot debug: G-DEBUG-цепочка session -> verify -> levels -> markout прошла без дефекта на {}/{} инструментах",
            reports.iter().filter(|(_, o)| o.is_ok()).count(),
            reports.len()
        ),
        Some(defect) => format!(
            "pilot debug: цепочка упала — {defect} (остальные инструменты обработаны независимо, см. строки выше)"
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

/// Сводный артефакт `--debug`: шапка несёт `debug` первой строкой (тот же
/// приём, что `levels.rs::debug_warning` — терпимый к `#` `csv::Reader`, не
/// изобретённый второй формат). Это единственный артефакт, который пилот
/// пишет сам, поэтому только он несёт метку явно; `markout-<SYMBOL>.csv`
/// такой шапки не пишет (см. doc `process_instrument`, CONCERNS таска 09).
fn write_debug_summary_csv(
    out: &Path,
    reports: &[(String, Result<InstrumentMetrics, StepFailure>)],
) -> anyhow::Result<()> {
    use std::io::Write as _;
    let mut file = std::fs::File::create(out)?;
    writeln!(
        file,
        "# debug: пилот ≤5 минут — результат не данные, ни для вердикта, ни для предрегистрации"
    )?;
    let mut w = csv::Writer::from_writer(file);
    w.write_record([
        "symbol",
        "step_failed",
        "verify_ok",
        "levels_floor",
        "levels_percentile",
        "rate_floor_per_min",
        "rate_percentile_per_min",
        "share_eaten",
        "share_pulled",
        "share_mixed",
        "m10s_n",
        "m10s_mean_bps",
        "m10s_lower_bps",
        "sharpe",
    ])?;
    for (symbol, outcome) in reports {
        match outcome {
            Ok(m) => {
                w.write_record([
                    symbol.clone(),
                    String::new(),
                    m.verify_ok.to_string(),
                    m.levels_floor.to_string(),
                    m.levels_percentile.to_string(),
                    format!("{:.6}", m.levels_per_min_floor),
                    format!("{:.6}", m.levels_per_min_percentile),
                    format!("{:.6}", m.share_eaten),
                    format!("{:.6}", m.share_pulled),
                    format!("{:.6}", m.share_mixed),
                    m.m10s_n.to_string(),
                    m.m10s_mean_bps
                        .map(|v| format!("{v:.6}"))
                        .unwrap_or_default(),
                    m.m10s_lower_bps
                        .map(|v| format!("{v:.6}"))
                        .unwrap_or_default(),
                    m.sharpe.map(|v| format!("{v:.6}")).unwrap_or_default(),
                ])?;
            }
            Err(f) => {
                w.write_record([
                    symbol.clone(),
                    format!("{}: {}", f.step, f.message),
                    String::new(),
                    String::new(),
                    String::new(),
                    String::new(),
                    String::new(),
                    String::new(),
                    String::new(),
                    String::new(),
                    String::new(),
                    String::new(),
                    String::new(),
                    String::new(),
                ])?;
            }
        }
    }
    w.flush()?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Боевой путь (§11): код и тесты на синтетике — не запускается этим таском
// (G-DEBUG не пройден, `k` не назначен владельцем; `interfaces.md`).
// ---------------------------------------------------------------------------

fn median(sorted: &[f64]) -> Option<f64> {
    if sorted.is_empty() {
        return None;
    }
    let mid = sorted.len() / 2;
    if sorted.len() % 2 == 1 {
        Some(sorted[mid])
    } else {
        Some((sorted[mid - 1] + sorted[mid]) / 2.0)
    }
}

/// Гейт G0 дословно (`PLAN.md` §6): (1) медианный инструмент ≥
/// `G0_MIN_PULLED` (200 — значение гейта; имя константы унаследовано от
/// отменённого дизайна «pulled», её место — `mod.rs`, не в зоне этого
/// таска) уровней за зачтённый час; (2) `m` на 10 с на объединённой
/// выборке ≥ круговых издержек **плюс проскальзывание**. Издержки и
/// проскальзывание по Decision 15 уже посчитаны на каждое наблюдение
/// (`costs::net_bps`, потребитель — `net_observations`/`mean_net_bps` в
/// `process_instrument`), поэтому условие (2) — `pooled_net_mean_bps >= 0`,
/// а не сырое `m10s >= ROUNDTRIP_FEES_BPS`: последнее пропускало бы
/// слагаемое проскальзывания (дозапрос по ревью таска 09(а), ось Данные) —
/// на тонком инструменте с широким спредом высокий `m` легко проигрывает
/// своему же проскальзыванию. Передавать сюда **`net`**, не `m`.
/// Продление до 6 часов один раз — решение вызывающего по этому
/// результату, не эта функция.
pub fn g0_verdict(
    levels_per_hour_by_instrument: &[f64],
    pooled_net_mean_bps: Option<f64>,
) -> String {
    let mut sorted = levels_per_hour_by_instrument.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let med = median(&sorted);
    let rate_pass = med.is_some_and(|m| m >= count_f64_u64(G0_MIN_PULLED));
    let edge_pass = pooled_net_mean_bps.is_some_and(|net| net >= 0.0);
    if !rate_pass {
        return format!(
            "G0 RED (sparse): медианная ставка {} < {G0_MIN_PULLED}/ч — продление до 6 часов один раз",
            med.map(|m| format!("{m:.1}/ч")).unwrap_or_else(|| "n/a".to_string())
        );
    }
    if !edge_pass {
        return format!(
            "G0 RED (edge): объединённый net (m_10s − {ROUNDTRIP_FEES_BPS:.1}bps − проскальзывание) {} < 0",
            format_opt_bps(pooled_net_mean_bps)
        );
    }
    format!(
        "G0 pass: медианная ставка {:.1}/ч >= {G0_MIN_PULLED}, объединённый net {} >= 0",
        med.unwrap_or(0.0),
        format_opt_bps(pooled_net_mean_bps)
    )
}

/// Разрыв G-POWER-B (история 40): требуемый Шарп (`lob power`) минус
/// замеренный. Положительное число — разрыв, ноль или отрицательное —
/// планка достигнута.
pub fn power_b_gap(measured_sharpe: f64, required_sharpe: f64) -> f64 {
    required_sharpe - measured_sharpe
}

fn run_pilot_battle(args: &PilotArgs) -> anyhow::Result<PilotSummary> {
    let pool_instruments = args
        .pool_instruments
        .as_ref()
        .ok_or_else(|| anyhow::anyhow!("боевой пилот требует --pool-instruments"))?;
    let symbols = read_pool_symbols(pool_instruments)?;
    let window_minutes = count_f64_u64(args.hours.saturating_mul(60));
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
mod tests {
    use super::*;

    // -----------------------------------------------------------------
    // `sessions_needed_for_profile`, `g0_verdict`, `power_b_gap` — синтетика.
    // -----------------------------------------------------------------

    #[test]
    fn sessions_needed_rounds_up_and_rejects_nonpositive_rate() {
        assert_eq!(sessions_needed_for_profile(2.0, 10.0, 100), Some(5));
        // 21 наблюдение/сессия -> ceil(100/21) = 5, не 4: округление вверх.
        assert_eq!(sessions_needed_for_profile(2.1, 10.0, 100), Some(5));
        assert_eq!(sessions_needed_for_profile(0.0, 10.0, 100), None);
        assert_eq!(sessions_needed_for_profile(-1.0, 10.0, 100), None);
        assert_eq!(sessions_needed_for_profile(1.0, 0.0, 100), None);
    }

    #[test]
    fn g0_verdict_reports_sparse_when_median_rate_is_low() {
        let v = g0_verdict(&[50.0, 60.0, 70.0], Some(20.0));
        assert!(v.starts_with("G0 RED (sparse)"), "{v}");
    }

    #[test]
    fn g0_verdict_reports_edge_red_when_rate_ok_but_net_is_negative() {
        let rates = vec![250.0, 260.0, 270.0, 280.0, 290.0];
        let v = g0_verdict(&rates, Some(-0.5));
        assert!(v.starts_with("G0 RED (edge)"), "{v}");
    }

    #[test]
    fn g0_verdict_passes_when_both_conditions_hold() {
        let rates = vec![250.0, 260.0, 270.0, 280.0, 290.0];
        let v = g0_verdict(&rates, Some(0.5));
        assert!(v.starts_with("G0 pass"), "{v}");
    }

    /// Дозапрос по ревью таска 09(а), ось Данные: `g0_verdict` обязан судить
    /// по `net` (издержки Decision 15 плюс проскальзывание, как в
    /// `costs::net_bps`), а не по сырому `m_10s` — план требует «`m_10s` ≥
    /// 7.5 bps + проскальзывание», и голое сравнение с 7.5 пропускало бы
    /// слагаемое проскальзывания. Здесь `m_bps = 10.0` уже выше 7.5 (старое,
    /// ошибочное условие дало бы "pass"), но узкая база относительно
    /// широкого выхода (`spread=3`, `mid2x=1000`) даёт проскальзывание
    /// 50 bps: `net = 10.0 − 7.5 − 50.0 = −47.5 < 0` — обязан быть "RED
    /// (edge)".
    #[test]
    fn g0_verdict_reds_on_edge_when_m10s_beats_roundtrip_but_net_is_negative_after_slippage() {
        let obs = [Observation {
            m_bps: 10.0,
            spread_ticks_exit: 3,
            mid2x_base: 1_000,
        }];
        let net = mean_net_bps(&obs);
        assert!(
            net.unwrap() < 0.0,
            "проверка фикстуры: net обязан быть отрицателен"
        );
        let rates = vec![250.0, 260.0, 270.0, 280.0, 290.0];
        let v = g0_verdict(&rates, net);
        assert!(v.starts_with("G0 RED (edge)"), "{v}");
    }

    /// Обратный случай той же фикстуры: тот же `m_bps = 10.0`, но глубокая
    /// база относительно узкого выхода (`spread=3`, `mid2x=100_000`) даёт
    /// проскальзывание 0.5 bps: `net = 10.0 − 7.5 − 0.5 = 2.0 >= 0` — "pass".
    #[test]
    fn g0_verdict_passes_on_edge_when_net_after_slippage_is_nonnegative() {
        let obs = [Observation {
            m_bps: 10.0,
            spread_ticks_exit: 3,
            mid2x_base: 100_000,
        }];
        let net = mean_net_bps(&obs);
        assert!(
            net.unwrap() >= 0.0,
            "проверка фикстуры: net обязан быть неотрицателен"
        );
        let rates = vec![250.0, 260.0, 270.0, 280.0, 290.0];
        let v = g0_verdict(&rates, net);
        assert!(v.starts_with("G0 pass"), "{v}");
    }

    #[test]
    fn power_b_gap_is_required_minus_measured() {
        assert!((power_b_gap(1.0, 3.0) - 2.0).abs() < 1e-9);
        assert!(
            power_b_gap(4.0, 3.0) < 0.0,
            "планка достигнута — разрыв отрицателен"
        );
    }

    // -----------------------------------------------------------------
    // `process_instrument` — тот же шов (`test_support::write_day`), что
    // `levels`/`markout`/`watch`; без сети, `verify_root` — суточные файлы.
    // -----------------------------------------------------------------

    fn write_instruments_csv_with_h3_lots(root: &std::path::Path, symbol: &str, h3_lots: i64) {
        std::fs::write(
            crate::commands::record::instruments_csv_path(root),
            format!(
                "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n\
                 {symbol},0.01,0.1,0.1,5,{h3_lots}\n"
            ),
        )
        .unwrap();
    }

    #[test]
    fn process_instrument_reports_rates_shares_and_verify_marker() {
        let dir = tempfile::tempdir().unwrap();
        super::super::test_support::write_day(
            dir.path(),
            "SOLUSDT",
            "2026-09-08",
            &super::super::test_support::three_level_frames(),
        );
        write_instruments_csv_with_h3_lots(dir.path(), "SOLUSDT", 5);
        let marker_dir = tempfile::tempdir().unwrap();
        let m = process_instrument(dir.path(), marker_dir.path(), "SOLUSDT", 0, 3_600_000, 5.0)
            .expect("цепочка обязана пройти на фикстуре");
        assert_eq!(
            m.levels_floor, 4,
            "три уровня фикстуры дают 4 записи (одна reprice)"
        );
        assert!(m.levels_per_min_floor > 0.0);
        assert!((m.share_eaten + m.share_pulled + m.share_mixed - 1.0).abs() < 1e-9);
        assert!(
            m.verify_ok,
            "фикстура без разрывов и нарушений — сверка обязана пройти"
        );
        let marker =
            std::fs::read_to_string(marker_dir.path().join("verify-SOLUSDT.status")).unwrap();
        assert_eq!(marker, "ok");
    }

    #[test]
    fn process_instrument_names_the_failing_step_on_missing_pool_file() {
        let dir = tempfile::tempdir().unwrap();
        super::super::test_support::write_day(
            dir.path(),
            "SOLUSDT",
            "2026-09-08",
            &super::super::test_support::three_level_frames(),
        );
        // Без instruments.csv режим floor обязан назвать шаг levels_floor,
        // не упасть где попало.
        let marker_dir = tempfile::tempdir().unwrap();
        let err = process_instrument(dir.path(), marker_dir.path(), "SOLUSDT", 0, 3_600_000, 5.0)
            .unwrap_err();
        assert_eq!(err.step, "levels_floor");
    }

    // -----------------------------------------------------------------
    // `stage_session_for_replay` — чистая файловая операция, без сети.
    // -----------------------------------------------------------------

    #[test]
    fn stage_session_for_replay_copies_binlogs_and_instruments_csv_with_day_suffix() {
        let session_dir = tempfile::tempdir().unwrap();
        let replay_dir = tempfile::tempdir().unwrap();
        let pool_csv = tempfile::NamedTempFile::new().unwrap();
        std::fs::write(pool_csv.path(), "symbol,h3_lots\nSOLUSDT,5\n").unwrap();
        std::fs::write(session_dir.path().join("SOLUSDT.binlog"), b"stub").unwrap();
        stage_session_for_replay(
            session_dir.path(),
            replay_dir.path(),
            pool_csv.path(),
            "2026-09-08",
            &["SOLUSDT".to_string()],
        )
        .unwrap();
        assert!(replay_dir
            .path()
            .join("SOLUSDT-2026-09-08.binlog")
            .is_file());
        assert!(replay_dir.path().join("instruments.csv").is_file());
        // Оригинал сессии остаётся нетронутым.
        assert!(session_dir.path().join("SOLUSDT.binlog").is_file());
    }

    // -----------------------------------------------------------------
    // Боевой путь — синтетика, без сети (executor.md: не запускать против
    // живой биржи; таск 09 запускает вживую только `--debug`).
    // -----------------------------------------------------------------

    fn write_pool_instruments_csv(root: &std::path::Path, symbol: &str, h3_lots: i64) {
        std::fs::write(
            crate::commands::record::instruments_csv_path(root),
            format!(
                "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n\
                 {symbol},0.01,0.1,0.1,5,{h3_lots}\n"
            ),
        )
        .unwrap();
    }

    fn battle_args(root: &std::path::Path) -> PilotArgs {
        PilotArgs {
            debug: false,
            pool_instruments: Some(crate::commands::record::instruments_csv_path(root)),
            root: root.to_path_buf(),
            minutes: None,
            base_url: BYBIT_MAINNET_URL.to_string(),
            ntp_addr: "pool.ntp.org:123".to_string(),
            hours: 2,
            warmup_ms: 0,
            repeat_window_ms: 3_600_000,
            maker_fee_bps: MAKER_FEE_BPS,
            taker_fee_bps: TAKER_FEE_BPS,
            runs_out: root.join("runs.csv"),
            now_utc: Some("2026-09-08T00:00:00Z".to_string()),
        }
    }

    #[test]
    fn battle_pilot_writes_one_runs_row_per_instrument_and_prints_g0_and_power_b() {
        let dir = tempfile::tempdir().unwrap();
        super::super::test_support::write_day(
            dir.path(),
            "SOLUSDT",
            "2026-09-08",
            &super::super::test_support::three_level_frames(),
        );
        write_pool_instruments_csv(dir.path(), "SOLUSDT", 5);
        let summary =
            run_pilot(&battle_args(dir.path())).expect("боевой путь обязан пройти на фикстуре");
        assert_eq!(summary.symbol, "SOLUSDT");
        let rows = crate::lob::runs::read_run_rows(&dir.path().join("runs.csv")).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].symbol, "SOLUSDT");
        assert!(summary.verdict.contains("G0"), "{}", summary.verdict);
        assert!(summary.verdict.contains("G-POWER-B"), "{}", summary.verdict);
    }

    #[test]
    fn pilot_rejects_changed_fees_in_either_mode() {
        let dir = tempfile::tempdir().unwrap();
        let mut args = battle_args(dir.path());
        args.taker_fee_bps = 6.0;
        assert!(run_pilot(&args).is_err());
    }

    #[test]
    fn debug_without_pool_instruments_errors_before_touching_the_network() {
        let dir = tempfile::tempdir().unwrap();
        let mut args = battle_args(dir.path());
        args.debug = true;
        args.pool_instruments = None;
        let err = run_pilot(&args).unwrap_err();
        assert!(err.to_string().contains("pool-instruments"), "{err}");
    }

    #[test]
    fn debug_without_minutes_errors_before_touching_the_network() {
        let dir = tempfile::tempdir().unwrap();
        let mut args = battle_args(dir.path());
        args.debug = true;
        args.pool_instruments = Some(crate::commands::record::instruments_csv_path(dir.path()));
        args.minutes = None;
        let err = run_pilot(&args).unwrap_err();
        assert!(err.to_string().contains("minutes"), "{err}");
    }
}
