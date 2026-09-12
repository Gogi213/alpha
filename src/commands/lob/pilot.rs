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

use std::path::{Path, PathBuf};

use clap::Args;

use crate::bybit::probe::percentile_ns;
use crate::bybit::rest::BYBIT_MAINNET_URL;
use crate::bybit::ws::parse_e9;
use crate::lob::costs::{
    mean_net_bps, net_fill_interval, observation_at, FillObservation, Observation, MAKER_FEE_BPS,
    ROUNDTRIP_FEES_BPS, TAKER_FEE_BPS,
};
use crate::lob::final_metrics::sharpe_ratio;
use crate::lob::levels::{H3Mode, LevelsConfig, Outcome};
use crate::lob::markout::{markouts_for_level, HORIZONS_MS};
use crate::lob::runs::log_pilot_run;
use crate::lob::shortlist::CONFIRM_MIN_N;
use crate::stats::{count_f64, count_f64_u64, count_u64, BOOTSTRAP_REPLICATIONS, GATE_ALPHA};

use super::backtest::{run_backtest, BacktestArgs};
use super::levels::{run_levels, LevelsArgs};
use super::markout::{run_markout, MarkoutArgs};
use super::pick::{h3_lots_floor, order_size_22a};
use super::profiles::{run_profiles, ProfilesArgs};
use super::session::{run_session, SessionArgs, SessionSummary};
use super::{
    median_trade_lots_for_symbol, replay_symbol, replay_symbol_over_configs,
    DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS, G0_MIN_PULLED,
};
use super::{resolve_h3_mode, ExecutionArgs, H3Args, H3ModeArg};

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

fn is_positive_finite(x: f64) -> bool {
    x.is_finite() && x > 0.0
}

/// «Второй час» §11, дословно: последняя метка реплея минус хвост в
/// `tail_minutes` минут. `None` при отсутствующем реплее (пустая выборка —
/// фильтр не определён, не паника) или отсутствующем/неположительном
/// `tail_minutes` (режим `--debug`, где хвост не запрашивается) — в обоих
/// случаях вызывающий обязан считать выборку целиком, не отрезанной.
fn counted_tail_cutoff_ms(max_ts_ms: Option<i64>, tail_minutes: Option<f64>) -> Option<i64> {
    let max_ts_ms = max_ts_ms?;
    let tail_minutes = tail_minutes?;
    if !is_positive_finite(tail_minutes) {
        return None;
    }
    #[allow(clippy::cast_possible_truncation)]
    let tail_ms = (tail_minutes * 60_000.0) as i64;
    Some(max_ts_ms.saturating_sub(tail_ms))
}

/// Строка `levels-*.csv` (`commands::lob::levels::run_levels`), только то,
/// что здесь нужно — колонка `birth_ms` по имени, остальные игнорируются
/// (тот же приём, что `PoolSymbolRow` ниже).
#[derive(Debug, serde::Deserialize)]
struct BirthMsRow {
    birth_ms: i64,
}

/// Считает строки `levels-*.csv` с `birth_ms >= cutoff_ms` (или все, если
/// `cutoff_ms` — `None`) — пересчёт ставки по хвосту без второго реплея
/// бинлога: файл уже написан `run_levels` тем же счётом.
fn count_rows_with_birth_after(csv_path: &Path, cutoff_ms: Option<i64>) -> anyhow::Result<usize> {
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_path(csv_path)
        .map_err(|e| anyhow::anyhow!("{}: {e}", csv_path.display()))?;
    let mut n = 0usize;
    for row in r.deserialize::<BirthMsRow>() {
        let row = row.map_err(|e| anyhow::anyhow!("{}: {e}", csv_path.display()))?;
        if cutoff_ms.is_none_or(|c| row.birth_ms >= c) {
            n += 1;
        }
    }
    Ok(n)
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

/// Сколько минут с хвоста боевого прогона считаются в замер: половина
/// длительности записи (решение владельца 2026-09-12, заменяет прежнюю
/// константу `BATTLE_COUNTED_TAIL_MINUTES = 60`). §11 плана называет частный
/// случай — «два часа, считается второй» — эта функция его обобщает:
/// `120.0 → 60.0` (ровно второй час, как в §11), `30.0 → 15.0` (текущий
/// пилот владельца — 30 минут, не два часа). `run_pilot_battle` передаёт
/// результат в `process_instrument`, который через `counted_tail_cutoff_ms`
/// режет ставку/доли исходов/`m`/`net` по хвосту в эти минуты от последнего
/// среза середины реплея — первая половина (прогрев) в замер не входит.
pub fn battle_counted_tail_minutes(window_minutes: f64) -> f64 {
    window_minutes / 2.0
}

/// Длина боевого окна для `process_instrument`/`k_grid_for_instrument`:
/// каталог сессии знает свою собственную длину, `--hours` не может её
/// перекрыть и не должен. Источник — `session.json` под `root` (тот же
/// файл, что пишет `lob session`): `pilot_minutes`, если сессия шла через
/// `--pilot-minutes` (таск 22, В-33 — «30 минут» владельца, не выражается
/// в целых часах), иначе `duration_s / 60` для обычной сессии сбора.
/// `--hours` остаётся **только** запасным путём — когда `session.json`
/// отсутствует или не разбирается (каталог ещё не содержит записи, либо
/// старый формат до таска 04): тогда окно назначает вызывающий флагом, как
/// до этой правки.
fn resolve_battle_window_minutes(root: &Path, hours: u64) -> f64 {
    std::fs::read_to_string(root.join("session.json"))
        .ok()
        .and_then(|s| serde_json::from_str::<SessionSummary>(&s).ok())
        .map(|summary| {
            summary
                .pilot_minutes
                .map(f64::from)
                .unwrap_or_else(|| count_f64_u64(summary.duration_s) / 60.0)
        })
        .unwrap_or_else(|| count_f64_u64(hours.saturating_mul(60)))
}

/// Путь `runs_out`, которым `run_profiles_and_backtest_chain` зовёт `lob
/// profiles` внутри `--debug`-цепочки: `allow_unverified: true` гарантирует,
/// что `run_profiles` его не тронет (`profiles.rs`: «отладочный режим файл
/// не трогает») — имя нарочно недвусмысленное, регресс-тест
/// `debug_chain_after_process_instrument_backtests_and_never_writes_runs_csv`
/// проверяет, что файл с этим именем не появляется (долг ревью 09(а)).
const DEBUG_CHAIN_RUNS_SENTINEL: &str = "profiles-runs-should-never-exist.csv";

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
///
/// `counted_tail_minutes` — половина §11: `None` (режим `--debug`) не
/// фильтрует ничего, вся выборка под `window_minutes` целиком; `Some(t)`
/// (боевой путь, `t = battle_counted_tail_minutes(window_minutes)`) режет
/// ставку `H3` в обоих режимах, доли исходов, `m`/`net`/Шарп по хвосту в `t`
/// минут от последнего среза середины реплея (`counted_tail_cutoff_ms`) —
/// первая половина (прогрев) не входит в замер, только она определяет
/// знаменатель ставки вместо `window_minutes`.
pub fn process_instrument(
    verify_root: &Path,
    marker_dir: &Path,
    symbol: &str,
    warmup_ms: i64,
    repeat_window_ms: i64,
    window_minutes: f64,
    counted_tail_minutes: Option<f64>,
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

    // Сверка и маркер `verify-<SYMBOL>.status` — тем же кодом, что
    // `lob verify` (таск 26: `super::verify::verify_and_mark` — единственная
    // функция записи маркера; вердикт — `VerifyStatus::of`, там же).
    let report =
        super::verify::verify_and_mark(verify_root, marker_dir, symbol).map_err(step("verify"))?;
    let verify_ok = report.status.is_ok();
    let verify_summary_line = super::verify::format_summary(&report.total);

    let floor_h3_lots = match resolve_h3_mode(verify_root, symbol, H3ModeArg::Floor, None)
        .map_err(step("levels_floor"))?
    {
        H3Mode::Floor { h3_lots } => h3_lots,
        H3Mode::Percentile { .. } => unreachable!("H3ModeArg::Floor всегда даёт H3Mode::Floor"),
    };

    let floor_summary = run_levels(&LevelsArgs {
        root: verify_root.to_path_buf(),
        symbol: symbol.to_string(),
        h3: H3Args {
            h3_mode: H3ModeArg::Floor,
            h3_lots: None,
        },
        h3_k: None,
        warmup_ms,
        repeat_window_ms,
        out: Some(verify_root.join(format!("levels-floor-{symbol}.csv"))),
    })
    .map_err(step("levels_floor"))?;

    let percentile_summary = run_levels(&LevelsArgs {
        root: verify_root.to_path_buf(),
        symbol: symbol.to_string(),
        h3: H3Args {
            h3_mode: H3ModeArg::Percentile,
            h3_lots: Some(floor_h3_lots),
        },
        h3_k: None,
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
        h3: H3Args {
            h3_mode: H3ModeArg::Floor,
            h3_lots: None,
        },
        warmup_ms,
        repeat_window_ms,
        out: Some(verify_root.join(format!("markout-{symbol}.csv"))),
        confirmatory: false,
        flag: None,
        median_lifetime_ms: None,
        profile: None,
        shortlist: None,
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

    // «Второй час» (§11): хвост в `counted_tail_minutes` от последнего среза
    // середины реплея — `None`/пустой реплей не фильтрует (см. doc
    // `counted_tail_cutoff_ms`).
    let global_max_ts_ms = replay
        .days
        .iter()
        .filter_map(|d| d.mids.last().map(|s| s.ts_ms))
        .max();
    let cutoff_ms = counted_tail_cutoff_ms(global_max_ts_ms, counted_tail_minutes);
    let effective_minutes = match (cutoff_ms, counted_tail_minutes) {
        (Some(_), Some(t)) => t,
        _ => window_minutes,
    };

    let mut eaten = 0usize;
    let mut pulled = 0usize;
    let mut mixed = 0usize;
    let mut m10s: Vec<f64> = Vec::new();
    let mut net_observations: Vec<Observation> = Vec::new();
    for day in &replay.days {
        for rec in &day.records {
            if cutoff_ms.is_some_and(|c| rec.birth_ms < c) {
                continue;
            }
            match rec.outcome() {
                Outcome::Eaten => eaten += 1,
                Outcome::Pulled => pulled += 1,
                Outcome::Mixed => mixed += 1,
            }
            let Some(m) = markouts_for_level(rec, &day.mids)[2] else {
                continue;
            };
            m10s.push(m);
            // Наблюдение `net` — одна конструкция на всех читателей
            // (`costs::observation_at`, таск 33), не своя петля по срезам.
            if let Some(obs) = observation_at(rec, &day.mids, HORIZONS_MS[2]) {
                net_observations.push(obs);
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
                if cutoff_ms.is_some_and(|c| rec.birth_ms < c) {
                    return None;
                }
                markouts_for_level(rec, &day.mids)[2].map(|m| FillObservation {
                    day_cluster: day_idx as i64,
                    net_bps: m,
                    filled: true,
                })
            })
        })
        .collect();
    let interval = net_fill_interval(&interval_obs, GATE_ALPHA, BOOTSTRAP_REPLICATIONS, 0);

    let effective_minutes = if effective_minutes > 0.0 {
        effective_minutes
    } else {
        f64::INFINITY
    };
    // Ставка `H3` по хвосту: без фильтра — итог `run_levels` целиком (как
    // раньше); с фильтром — пересчёт по уже написанному `levels-*.csv`
    // (`birth_ms`), а не второй реплей бинлога (`count_rows_with_birth_after`
    // читает файл, который `run_levels` только что сам написал).
    let levels_floor_count = match cutoff_ms {
        Some(c) => count_rows_with_birth_after(&floor_summary.out, Some(c))
            .map_err(step("levels_floor"))?,
        None => floor_summary.levels,
    };
    let levels_percentile_count = match cutoff_ms {
        Some(c) => count_rows_with_birth_after(&percentile_summary.out, Some(c))
            .map_err(step("levels_percentile"))?,
        None => percentile_summary.levels,
    };
    Ok(InstrumentMetrics {
        symbol: symbol.to_string(),
        verify_ok,
        verify_summary_line,
        levels_floor: levels_floor_count,
        levels_percentile: levels_percentile_count,
        levels_per_min_floor: count_f64(levels_floor_count) / effective_minutes,
        levels_per_min_percentile: count_f64(levels_percentile_count) / effective_minutes,
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
// Сетка `k` (таск 18, `SETTLED.md` В-30, `manifest.md` D05): владелец на
// вопрос о числе `k` — «не знаю надо методологию определения динамического
// порога сформулировать», методологию сформулировал оркестратор и записал
// как В-30. `k` не назначается рукой: двухчасовой пилот считает ставку
// уровней в минуту и долю `eaten` по предрегистрированной сетке и берёт
// наименьшее `k`, при котором медианный инструмент проходит G0 (`PLAN.md`
// §6: ≥ 200 уровней за зачтённый час) и G1 (§6: `eaten` ≥ 5%). Один реплей
// на инструмент, не пять: `k_grid_for_instrument` читает
// `median_trade_lots` один раз и кормит все пять порогов
// `super::replay_symbol_over_configs` разом — та функция декодирует бинлог
// один раз и раздаёт кадры пяти трекерам (`interfaces.md`, «Из таска 18»).
// ---------------------------------------------------------------------------

/// Предрегистрированная сетка `k` (`SETTLED.md` В-30, `manifest.md` D05):
/// «k выбирает двухчасовой пилот ... по предрегистрированной сетке k ∈ {2,
/// 5, 10, 20, 50}». Не изобретённое число: сетка зафиксирована методологией
/// до всякого прогона, владелец явно отказался назначать `k` рукой.
pub const K_GRID: [f64; 5] = [2.0, 5.0, 10.0, 20.0, 50.0];

/// Доля `eaten` гейта G1 (`PLAN.md` §6: «`eaten` и `pulled` обе ≥ 5%») —
/// переиспользует уже закоммиченный гейт `lob::markup::G1_MIN_SHARE_NUM`/
/// `_DEN` (1/20), а не второй литерал `0.05`: изобретённое число запрещено,
/// а этот порог уже назначен и живёт в одном месте (`markup.rs`, гейт G1
/// исхода уровня — то же число из того же пункта плана, не совпадение).
fn g1_min_eaten_share() -> f64 {
    count_f64_u64(crate::lob::markup::G1_MIN_SHARE_NUM)
        / count_f64_u64(crate::lob::markup::G1_MIN_SHARE_DEN)
}

/// Одна строка сетки `k` одного инструмента: число уровней, ставка в
/// минуту и доля `eaten` при пороге `h3_lots = floor(k × median_trade_lots)`.
#[derive(Debug, Clone, Copy)]
pub struct KGridInstrumentRow {
    pub k: f64,
    pub levels: usize,
    pub rate_per_min: f64,
    pub eaten_share: f64,
}

/// Сетка `k` одного инструмента, один реплей бинлога (критерий приёмки
/// таска 18: «за один реплей на инструмент», не пять): `median_trade_lots`
/// читается один раз из `instruments.csv` (`super::
/// median_trade_lots_for_symbol`), пороги `h3_lots` для всех пяти `k` из
/// `K_GRID` собираются в пять `LevelsConfig::Floor`, и `super::
/// replay_symbol_over_configs` кормит все пять трекеров кадрами одного
/// декодирования. Хвостовой фильтр («второй час» §11) — тот же приём, что
/// `process_instrument`/`counted_tail_cutoff_ms`: `None` — вся выборка
/// (`--debug`), `Some(t)` — только хвост в `t` минут (боевой путь).
fn k_grid_for_instrument(
    root: &Path,
    symbol: &str,
    repeat_window_ms: i64,
    window_minutes: f64,
    counted_tail_minutes: Option<f64>,
) -> anyhow::Result<Vec<KGridInstrumentRow>> {
    let instruments_csv = crate::commands::record::instruments_csv_path(root);
    let median = median_trade_lots_for_symbol(&instruments_csv, symbol)?;
    let cfgs: Vec<LevelsConfig> = K_GRID
        .iter()
        .map(|&k| {
            let h3_lots = h3_lots_floor(Some(median), k)
                .ok_or_else(|| anyhow::anyhow!("{symbol}: h3_lots_floor(k={k}) не посчитался"))?;
            anyhow::ensure!(
                h3_lots > 0,
                "{symbol}: h3_lots(k={k}) обязан быть положителен (медиана={median}), получено {h3_lots}"
            );
            Ok(LevelsConfig {
                mode: H3Mode::Floor { h3_lots },
                warmup_ms: 0,
                repeat_window_ms,
            })
        })
        .collect::<anyhow::Result<Vec<_>>>()?;

    let replay_per_k = replay_symbol_over_configs(root, symbol, &cfgs)?;

    let global_max_ts_ms = replay_per_k[0]
        .days
        .iter()
        .filter_map(|d| d.mids.last().map(|s| s.ts_ms))
        .max();
    let cutoff_ms = counted_tail_cutoff_ms(global_max_ts_ms, counted_tail_minutes);
    let effective_minutes = match (cutoff_ms, counted_tail_minutes) {
        (Some(_), Some(t)) => t,
        _ => window_minutes,
    };
    let effective_minutes = if effective_minutes > 0.0 {
        effective_minutes
    } else {
        f64::INFINITY
    };

    let mut rows = Vec::with_capacity(K_GRID.len());
    for (i, &k) in K_GRID.iter().enumerate() {
        let mut levels = 0usize;
        let mut eaten = 0usize;
        for day in &replay_per_k[i].days {
            for rec in &day.records {
                if cutoff_ms.is_some_and(|c| rec.birth_ms < c) {
                    continue;
                }
                levels += 1;
                if rec.outcome() == Outcome::Eaten {
                    eaten += 1;
                }
            }
        }
        let eaten_share = if levels > 0 {
            count_f64(eaten) / count_f64(levels)
        } else {
            0.0
        };
        rows.push(KGridInstrumentRow {
            k,
            levels,
            rate_per_min: count_f64(levels) / effective_minutes,
            eaten_share,
        });
    }
    Ok(rows)
}

/// Одна строка сводки `k -> rate/eaten/G0/G1` по медианному инструменту
/// пула (критерий приёмки таска 18).
#[derive(Debug, Clone, Copy)]
pub struct KGridSummaryRow {
    pub k: f64,
    pub median_rate_per_hour: f64,
    pub median_eaten_share: f64,
    pub g0_pass: bool,
    pub g1_pass: bool,
}

/// Сводка по пулу: медиана ставки (в час) и доли `eaten` на каждый `k`
/// сетки — тот же приём «медианный инструмент», что `g0_verdict` уже
/// использует для ставки `H3`. G0/G1 — гейты §6 плана.
pub fn summarize_k_grid(
    per_instrument: &[(String, Vec<KGridInstrumentRow>)],
) -> Vec<KGridSummaryRow> {
    let g1_threshold = g1_min_eaten_share();
    (0..K_GRID.len())
        .map(|i| {
            let mut rates: Vec<f64> = per_instrument
                .iter()
                .map(|(_, rows)| rows[i].rate_per_min * 60.0)
                .collect();
            let mut eatens: Vec<f64> = per_instrument
                .iter()
                .map(|(_, rows)| rows[i].eaten_share)
                .collect();
            rates.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
            eatens.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
            let median_rate = median(&rates).unwrap_or(0.0);
            let median_eaten = median(&eatens).unwrap_or(0.0);
            KGridSummaryRow {
                k: K_GRID[i],
                median_rate_per_hour: median_rate,
                median_eaten_share: median_eaten,
                g0_pass: median_rate >= count_f64_u64(G0_MIN_PULLED),
                g1_pass: median_eaten >= g1_threshold,
            }
        })
        .collect()
}

/// Выбор `k` (таск 18, В-30/D05): наименьшее `k` сетки (сетка уже по
/// возрастанию), при котором медианный инструмент проходит оба гейта
/// разом. Нет такого — `None`: печатается как «не определим», отдельно от
/// красного про рынок (критерий приёмки).
pub fn choose_k(summary: &[KGridSummaryRow]) -> Option<f64> {
    summary.iter().find(|r| r.g0_pass && r.g1_pass).map(|r| r.k)
}

/// Печать таблицы `k -> rate/eaten/G0/G1` — по инструментам и по
/// медианному инструменту (критерий приёмки таска 18): `debug` — метка
/// `[debug]` на каждой строке, потому что ставка в `--debug` —
/// экстраполяция `rate/min × 60` на прогоне короче часа, не измеренный час
/// (боевой путь мерит настоящий зачтённый час/хвост — без метки).
fn format_k_grid_lines(
    per_instrument: &[(String, Vec<KGridInstrumentRow>)],
    summary: &[KGridSummaryRow],
    debug: bool,
) -> Vec<String> {
    let debug_suffix = if debug { " [debug]" } else { "" };
    let mut lines = Vec::new();
    for (symbol, rows) in per_instrument {
        for row in rows {
            lines.push(format!(
                "pilot k-grid: {symbol} k={:.1} levels={} rate={:.3}/min eaten_share={:.3}{debug_suffix}",
                row.k, row.levels, row.rate_per_min, row.eaten_share
            ));
        }
    }
    for row in summary {
        lines.push(format!(
            "pilot k-grid: median k={:.1} rate_per_hour={:.1} eaten_share={:.3} G0={} G1={}{debug_suffix}",
            row.k,
            row.median_rate_per_hour,
            row.median_eaten_share,
            if row.g0_pass { "pass" } else { "fail" },
            if row.g1_pass { "pass" } else { "fail" },
        ));
    }
    match choose_k(summary) {
        Some(k) => lines.push(format!(
            "pilot k-grid: k выбран={k:.1} (h3 floor, G0 и G1 пройдены на медианном инструменте){debug_suffix}"
        )),
        None => lines.push(format!(
            "pilot k-grid: k: не определим (порог не задан по данным){debug_suffix}"
        )),
    }
    lines
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

/// Копия `instruments.csv` пула в `session_dir` — нужна режиму `floor`
/// (`resolve_h3_mode`) и подсчёту лота (`compute_order_qty_e9` в цепочке
/// ниже), которые читают его рядом с бинлогом символа, а не берут
/// `--pool-instruments` напрямую. Про имя бинлога не знает: раньше (до
/// таска 19, часть 2) эта же функция ещё и клала алиас `<SYMBOL>.binlog`
/// без даты для `backtest.rs`/`profiles.rs`/`watch.rs` —
/// `alias_dated_binlogs_for_legacy_readers` снята, все три находят
/// `<SYMBOL>-<день>.binlog` напрямую через `super::session_binlog_for`.
fn copy_pool_instruments_csv(session_dir: &Path, pool_instruments: &Path) -> anyhow::Result<()> {
    std::fs::copy(pool_instruments, session_dir.join("instruments.csv"))
        .map_err(|e| anyhow::anyhow!("копия instruments.csv в {}: {e}", session_dir.display()))?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Хвост `--debug`-цепочки (09(б)): `lob profiles --allow-unverified` по
// всему пулу, затем `lob backtest --debug` на сигналах каждого символа —
// сигналы из уже написанного `levels-floor-<SYMBOL>.csv` (`run_levels`),
// RTT из `probe-<SYMBOL>.csv`/`clock.csv` сессии или флагов, лот —
// `order_size_22a` от `instruments.csv` пула. Не сетевая: работает на уже
// готовых каталогах — тестируется на синтетике той же фикстурой, что
// `process_instrument`.
// ---------------------------------------------------------------------------

/// Строка `levels-floor-<SYMBOL>.csv`, только нужные здесь колонки по
/// имени: сторона — уже строка `bid`/`ask` (`side_name`, `levels.rs`), тот
/// же алфавит, что ждёт `lob backtest --signals-csv`.
#[derive(Debug, serde::Deserialize)]
struct LevelsRowForSignals {
    side: String,
    birth_ms: i64,
    price_tick: i64,
}

/// Сигналы `lob backtest` (`profile_id,side,birth_ms`) из уже написанного
/// `levels-floor-<SYMBOL>.csv` — один `profile_id` на весь символ
/// (`debug:<symbol>`): разметка по семи осям — таск 12/16, не эта команда,
/// а цель здесь — доказать, что цепочка `profiles → backtest` собирается и
/// проходит, не назначить вердиктный профиль. Возвращает `price_tick`
/// последней строки (нужен для `order_size_22a` — «последняя цена» без
/// второго реплея) или `None`, если строк не было (нечего торговать —
/// вызывающий обязан пропустить бэктест этого символа, не считать дефектом).
fn write_signals_csv_from_levels(
    levels_csv: &Path,
    signals_csv: &Path,
    profile_id: &str,
) -> anyhow::Result<Option<i64>> {
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_path(levels_csv)
        .map_err(|e| anyhow::anyhow!("{}: {e}", levels_csv.display()))?;
    let mut w = csv::Writer::from_path(signals_csv)
        .map_err(|e| anyhow::anyhow!("{}: {e}", signals_csv.display()))?;
    w.write_record(["profile_id", "side", "birth_ms"])?;
    let mut last_price_tick = None;
    for row in r.deserialize::<LevelsRowForSignals>() {
        let row = row.map_err(|e| anyhow::anyhow!("{}: {e}", levels_csv.display()))?;
        w.write_record([profile_id, &row.side, &row.birth_ms.to_string()])?;
        last_price_tick = Some(row.price_tick);
    }
    w.flush()?;
    Ok(last_price_tick)
}

/// Поля `instruments.csv` пула, нужные `order_size_22a` (Decision 22): лот и
/// шаг лота площадки, минимальный чек. Читает по имени колонки через
/// `pick::instruments_csv_reader` — единственный терпимый к `#`-баннеру
/// ридер этого файла (`interfaces.md`, «Из таска 08»).
fn read_lot_fields(instruments_csv: &Path, symbol: &str) -> anyhow::Result<(i64, i64, i64)> {
    #[derive(Debug, serde::Deserialize)]
    struct Row {
        symbol: String,
        min_order_qty: String,
        qty_step: String,
        min_notional_value: String,
    }
    let mut r = super::pick::instruments_csv_reader(instruments_csv)
        .map_err(|e| anyhow::anyhow!("{}: {e}", instruments_csv.display()))?;
    for row in r.deserialize::<Row>() {
        let row = row.map_err(|e| anyhow::anyhow!("{}: {e}", instruments_csv.display()))?;
        if row.symbol != symbol {
            continue;
        }
        let min_order_qty_e9 = parse_e9(&row.min_order_qty)
            .ok_or_else(|| anyhow::anyhow!("{symbol}: min_order_qty не разобрался"))?;
        let qty_step_e9 = parse_e9(&row.qty_step)
            .ok_or_else(|| anyhow::anyhow!("{symbol}: qty_step не разобрался"))?;
        let min_notional_value_e9 = parse_e9(&row.min_notional_value)
            .ok_or_else(|| anyhow::anyhow!("{symbol}: min_notional_value не разобрался"))?;
        return Ok((min_order_qty_e9, qty_step_e9, min_notional_value_e9));
    }
    anyhow::bail!("{symbol}: нет в {}", instruments_csv.display())
}

/// Лот `lob backtest --order-qty-e9` (Decision 22а): `order_size_22a` от
/// полей `instruments.csv` пула и последней цены из `levels-floor` (в
/// тиках — `tick_e9` от `load_steps_for_symbol`, то же измерение, что
/// `record.rs` использует для шагов записи, не второй реплей ради цены).
fn compute_order_qty_e9(
    instruments_csv: &Path,
    symbol: &str,
    last_price_tick: i64,
) -> anyhow::Result<i64> {
    let (tick_e9, _step_e9) =
        crate::commands::record::load_steps_for_symbol(instruments_csv, symbol)
            .map_err(|e| anyhow::anyhow!("{symbol}: шаги: {e:?}"))?;
    let (min_order_qty_e9, qty_step_e9, min_notional_value_e9) =
        read_lot_fields(instruments_csv, symbol)?;
    let last_price_e9 = last_price_tick.saturating_mul(tick_e9);
    Ok(order_size_22a(
        min_order_qty_e9,
        qty_step_e9,
        min_notional_value_e9,
        last_price_e9,
    ))
}

/// Круги RTT `probe-<SYMBOL>.csv` (`lob probe`) — колонка `rtt_ns` по имени.
#[derive(Debug, serde::Deserialize)]
struct ProbeCycleRow {
    rtt_ns: i64,
}

fn read_probe_rtts(path: &Path) -> Option<Vec<i64>> {
    let mut r = csv::Reader::from_path(path).ok()?;
    let vals: Vec<i64> = r
        .deserialize::<ProbeCycleRow>()
        .filter_map(Result::ok)
        .map(|row| row.rtt_ns)
        .collect();
    if vals.is_empty() {
        None
    } else {
        Some(vals)
    }
}

/// `clock.csv` (`bybit::clock`, `lob session`), колонка `bybit_rtt_ns`:
/// REST round-trip той же сессии, запасной источник, когда авторизованный
/// `lob probe` не гонялся (нужны ключи). Фильтр над общим `read_rows`
/// (таск 17 — не второй парсер того же файла), строки без замера (`None`,
/// сеть не ответила) отсутствуют в выдаче, а не превращаются в `0`.
fn read_clock_bybit_rtts(path: &Path) -> Option<Vec<i64>> {
    let rows = crate::bybit::clock::read_rows(path).ok()?;
    let vals: Vec<i64> = rows
        .into_iter()
        .filter_map(|row| row.bybit_rtt_ns)
        .collect();
    if vals.is_empty() {
        None
    } else {
        Some(vals)
    }
}

/// RTT `lob backtest --median-rtt-ns/--p95-rtt-ns` (D-RTT): `probe-<symbol>.
/// csv` сессии, если есть, иначе `clock.csv` той же сессии, иначе явные
/// флаги без умолчания — измеренное всегда предпочтено назначенному (§9
/// плана). Отказ без всех трёх — громкий, не молчаливая подстановка.
fn resolve_backtest_rtt_ns(
    session_dir: &Path,
    symbol: &str,
    explicit_median_rtt_ns: Option<i64>,
    explicit_p95_rtt_ns: Option<i64>,
) -> anyhow::Result<(i64, i64, &'static str)> {
    if let Some(rtts) = read_probe_rtts(&session_dir.join(format!("probe-{symbol}.csv"))) {
        return Ok((percentile_ns(&rtts, 50), percentile_ns(&rtts, 95), "probe"));
    }
    if let Some(rtts) = read_clock_bybit_rtts(&session_dir.join("clock.csv")) {
        return Ok((percentile_ns(&rtts, 50), percentile_ns(&rtts, 95), "clock"));
    }
    match (explicit_median_rtt_ns, explicit_p95_rtt_ns) {
        (Some(m), Some(p)) => Ok((m, p, "flag")),
        _ => anyhow::bail!(
            "{symbol}: RTT не измерена (нет probe-{symbol}.csv/clock.csv в {}) — передайте --median-rtt-ns/--p95-rtt-ns",
            session_dir.display()
        ),
    }
}

/// Хвост цепочки G-DEBUG после `verify -> levels -> markout` (шаг 09(б)):
/// `lob profiles --allow-unverified` по всему пулу, затем `lob backtest
/// --debug` на сигналах каждого символа. Принимает уже готовый `pilot_root`
/// (`session/` с настоящими `<SYMBOL>-<день>.binlog` и копией
/// `instruments.csv` внутри — из `copy_pool_instruments_csv`) — не сетевая,
/// тестируется на синтетике. Возвращает напечатанные строки и первый
/// найденный дефект (тем же протоколом, что цикл `process_instrument` в
/// `run_pilot_debug`).
fn run_profiles_and_backtest_chain(
    pilot_root: &Path,
    symbols: &[String],
    warmup_ms: i64,
    repeat_window_ms: i64,
    candidates_csv: &Path,
    explicit_rtt_ns: (Option<i64>, Option<i64>),
    now: &str,
) -> (Vec<String>, Option<String>) {
    let (explicit_median_rtt_ns, explicit_p95_rtt_ns) = explicit_rtt_ns;
    let mut lines = Vec::new();
    let mut first_defect: Option<String> = None;
    let session_dir = pilot_root.join("session");

    if let Err(e) = std::fs::copy(
        session_dir.join("instruments.csv"),
        pilot_root.join("instruments.csv"),
    ) {
        let msg = format!("instruments.csv для profiles: {e}");
        lines.push(format!("pilot debug: profiles — упал: {msg}"));
        return (lines, Some(format!("profiles: {msg}")));
    }

    let profiles_out = pilot_root.join("profiles-debug.csv");
    // Тройка RTT/лота площадки (таск 16, `resolve_fill_model`) здесь
    // намеренно не задаётся: она одна на весь пул, а лот (`order_size_22a`)
    // у каждого символа свой (см. `compute_order_qty_e9` ниже, для шага
    // `backtest`) — подставлять единый лот на пул значило бы изобретать
    // число для символов, для которых он не измерен. `profiles` остаётся на
    // `NoFillModel` (`fill_model=none`), а вердикт по исполнению даёт
    // отдельный шаг `backtest` ниже, per-symbol.
    let profiles_result = run_profiles(&ProfilesArgs {
        root: pilot_root.to_path_buf(),
        candidates_csv: candidates_csv.to_path_buf(),
        h3: H3Args {
            h3_mode: H3ModeArg::Floor,
            h3_lots: None,
        },
        warmup_ms,
        repeat_window_ms,
        allow_unverified: true,
        out: Some(profiles_out.clone()),
        now_utc: Some(now.to_string()),
        runs_out: pilot_root.join(DEBUG_CHAIN_RUNS_SENTINEL),
        execution: ExecutionArgs {
            median_rtt_ns: None,
            p95_rtt_ns: None,
            order_qty_e9: None,
        },
        // Окно «сейчас» (ticket 21, R57): не нужно здесь — `allow_unverified:
        // true` выше уже снимает требование окна (doc `profiles.rs`); поля
        // добавлены только чтобы этот литерал остался исчерпывающим после
        // добавления `--preregistration`/`--window-end` в `ProfilesArgs`.
        preregistration: None,
        window_end: None,
    });
    match &profiles_result {
        Ok(s) => lines.push(format!(
            "pilot debug: profiles rows={} out={}",
            s.rows,
            s.out.display()
        )),
        Err(e) => {
            lines.push(format!("pilot debug: profiles — упал: {e}"));
            first_defect.get_or_insert_with(|| format!("profiles: {e}"));
        }
    }
    let profiles_csv_for_comparison = profiles_result.is_ok().then(|| profiles_out.clone());

    for symbol in symbols {
        let levels_csv = session_dir.join(format!("levels-floor-{symbol}.csv"));
        let signals_csv = session_dir.join(format!("signals-{symbol}.csv"));
        let profile_id = format!("debug:{symbol}");
        let last_price_tick =
            match write_signals_csv_from_levels(&levels_csv, &signals_csv, &profile_id) {
                Ok(v) => v,
                Err(e) => {
                    lines.push(format!("pilot debug: {symbol} backtest — сигналы: {e}"));
                    first_defect.get_or_insert_with(|| format!("backtest:{symbol}: сигналы: {e}"));
                    continue;
                }
            };
        let Some(last_price_tick) = last_price_tick else {
            lines.push(format!(
                "pilot debug: {symbol} backtest — пропущен, нет сигналов (0 уровней floor)"
            ));
            continue;
        };
        let instruments_csv = session_dir.join("instruments.csv");
        let order_qty_e9 = match compute_order_qty_e9(&instruments_csv, symbol, last_price_tick) {
            Ok(v) => v,
            Err(e) => {
                lines.push(format!("pilot debug: {symbol} backtest — лот: {e}"));
                first_defect.get_or_insert_with(|| format!("backtest:{symbol}: лот: {e}"));
                continue;
            }
        };
        let (median_rtt_ns, p95_rtt_ns, rtt_source) = match resolve_backtest_rtt_ns(
            &session_dir,
            symbol,
            explicit_median_rtt_ns,
            explicit_p95_rtt_ns,
        ) {
            Ok(v) => v,
            Err(e) => {
                lines.push(format!("pilot debug: {symbol} backtest — RTT: {e}"));
                first_defect.get_or_insert_with(|| format!("backtest:{symbol}: RTT: {e}"));
                continue;
            }
        };
        let bt = run_backtest(&BacktestArgs {
            session_root: session_dir.clone(),
            symbol: symbol.clone(),
            signals_csv,
            median_rtt_ns,
            p95_rtt_ns,
            order_qty_e9,
            profiles_csv: profiles_csv_for_comparison.clone(),
            out: Some(session_dir.join(format!("backtest-{symbol}.csv"))),
            pnl_out: Some(session_dir.join(format!("backtest-{symbol}-pnl.csv"))),
            debug: true,
        });
        match bt {
            Ok(s) => lines.push(format!(
                "pilot debug: {symbol} backtest rtt_source={rtt_source} median_rtt_ns={median_rtt_ns} p95_rtt_ns={p95_rtt_ns} order_qty_e9={order_qty_e9} profiles={} pass={} red={}",
                s.profiles, s.pass, s.red
            )),
            Err(e) => {
                lines.push(format!("pilot debug: {symbol} backtest — упал: {e}"));
                first_defect.get_or_insert_with(|| format!("backtest:{symbol}: {e}"));
            }
        }
    }
    (lines, first_defect)
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

/// Каркас предрегистрации, этап 2 (`SETTLED.md` В-29, `PLAN.md` §9): что
/// войдёт в файл, который владелец коммитит **после** двухчасового боевого
/// пилота и **до** первой сессии сбора — режим `H3` и его `k`, границы оси
/// повторяемости, глубина креста профилей, число сессий на профиль,
/// правило остановки в сессиях, хост и его RTT. Печатает только **форму** —
/// имена полей и откуда каждое берётся, ни одного числа, за одним
/// исключением (таск 18, В-30/D05): `selected_h3` — пара `(режим, k)`,
/// которую даёт сетка `K_GRID` этого же боевого пилота (`choose_k`), а не
/// изобретённое число; `None` — сетка не выбрала ни один `k` («не
/// определим» — красный по построению), и строки остаются плейсхолдером,
/// как раньше. Остальные поля числа не получают: их источник (`lob probe`,
/// границы повторяемости, …) этот таск не считает — см. `CONCERNS`.
pub fn stage2_preregistration_skeleton(selected_h3: Option<(&str, f64)>) -> String {
    let h3_mode_line = match selected_h3 {
        Some((mode, _)) => {
            format!("  h3_mode: {mode} — наименьшее k сетки K_GRID (В-30/D05), прошедшее G0 и G1 на этом пилоте")
        }
        None => "  h3_mode: <floor|percentile — какой дал заявленный G0/G-POWER-B на этом пилоте>"
            .to_string(),
    };
    let h3_k_line = match selected_h3 {
        Some((_, k)) => {
            format!("  h3_k: {k} — наименьшее k сетки K_GRID (В-30/D05), прошедшее G0 и G1 на этом пилоте")
        }
        None => {
            "  h3_k: <k для --h3-k — сравнение ставок floor/percentile на этом пилоте>".to_string()
        }
    };
    [
        "предрегистрация, этап 2 (В-29) — коммитом после боевого пилота, до первой сессии сбора:"
            .to_string(),
        h3_mode_line,
        h3_k_line,
        "  repeat_axis_bounds: <границы корзин повторяемости 1 / 2 / >=3 — по распределению repeat_count>".to_string(),
        "  profile_grid_depth: <глубина креста профилей — из ставки уровней в минуту>".to_string(),
        "  sessions_per_profile: <sessions_needed_for_profile(ставка, длина сессии, CONFIRM_MIN_N)>".to_string(),
        "  stopping_rule_sessions: <правило остановки сбора в сессиях>".to_string(),
        "  host_id: <хост, на котором мерялась RTT>".to_string(),
        "  rtt_median_ns / rtt_p95_ns: <lob probe / clock.csv этого пилота>".to_string(),
        "  committed_after_pilot_run: <путь и момент боевого пилота, который дал эти числа>".to_string(),
    ]
    .join("\n")
}

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
