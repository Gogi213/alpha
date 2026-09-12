//! Замер одного инструмента: `process_instrument` (сверка → `H3` в обоих
//! режимах → markout → доли исходов, `m` на 10 с, `net`), хвост «второго часа»
//! §11 и строка печати. Отдельно от `pilot.rs`: тем же кодом идут и `--debug`,
//! и боевой путь, а `verify.rs`/`profiles.rs` ссылаются именно сюда.

use std::path::Path;

use crate::commands::lob::levels::{run_levels, LevelsArgs};
use crate::commands::lob::markout::{run_markout, MarkoutArgs};
use crate::commands::lob::session::SessionSummary;
use crate::commands::lob::{replay_symbol, resolve_h3_mode, H3Args, H3ModeArg};
use crate::lob::costs::{net_fill_interval, observation_at, FillObservation, Observation};
use crate::lob::final_metrics::sharpe_ratio;
use crate::lob::levels::{H3Mode, LevelsConfig, Outcome};
use crate::lob::markout::{markouts_for_level, HORIZONS_MS};
use crate::stats::{count_f64, count_f64_u64, BOOTSTRAP_REPLICATIONS, GATE_ALPHA};

pub(super) fn is_positive_finite(x: f64) -> bool {
    x.is_finite() && x > 0.0
}

/// «Второй час» §11, дословно: последняя метка реплея минус хвост в
/// `tail_minutes` минут. `None` при отсутствующем реплее (пустая выборка —
/// фильтр не определён, не паника) или отсутствующем/неположительном
/// `tail_minutes` (режим `--debug`, где хвост не запрашивается) — в обоих
/// случаях вызывающий обязан считать выборку целиком, не отрезанной.
pub(super) fn counted_tail_cutoff_ms(
    max_ts_ms: Option<i64>,
    tail_minutes: Option<f64>,
) -> Option<i64> {
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
pub(super) fn resolve_battle_window_minutes(root: &Path, hours: u64) -> f64 {
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
    // `lob verify` (таск 26: `crate::commands::lob::verify::verify_and_mark` — единственная
    // функция записи маркера; вердикт — `VerifyStatus::of`, там же).
    let report = crate::commands::lob::verify::verify_and_mark(verify_root, marker_dir, symbol)
        .map_err(step("verify"))?;
    let verify_ok = report.status.is_ok();
    let verify_summary_line = crate::commands::lob::verify::format_summary(&report.total);

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

pub(super) fn format_opt_bps(v: Option<f64>) -> String {
    v.map(|x| format!("{x:.3}bps"))
        .unwrap_or_else(|| "n/a".to_string())
}

pub(super) fn format_opt(v: Option<f64>) -> String {
    v.map(|x| format!("{x:.3}"))
        .unwrap_or_else(|| "n/a".to_string())
}

/// Строка "по инструменту", общая для `--debug` и боевого пути.
pub(super) fn format_instrument_line(m: &InstrumentMetrics) -> String {
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

pub(super) fn median(sorted: &[f64]) -> Option<f64> {
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
