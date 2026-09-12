//! `lob touch-profiles` — таблица профилей касаний (таск 37, R90, В-44):
//! `docs/findings/touch-profiles-<дата>.csv`, строка на каждый id сетки
//! `lob::touch_axes::touch_profile_grid` — маргиналы девяти осей касания
//! В-44 плюс один крест исход × возраст, на каждый инструмент пула и на
//! пул целиком (`pool`). Таблица **полная**: профили с `n = 0` остаются
//! строками (`none` в числах), как у сетки смертей (`lob profiles`).
//!
//! # Что это и чего здесь нет
//!
//! Это отбор кандидатов: markout касания «в сторону отскока» на четырёх
//! горизонтах `HORIZONS_MS` от среза как есть на `start_ms` (В-43,
//! `markout::markouts_for_touch`) с бутстрап-интервалом. `net`/`net_fill`
//! здесь **нет** — они у сделки-отскока в бэктесте (таск 38, В-44
//! «Сделка-отскок»): markout отбирает, бэктест выносит вердикт (задача,
//! «Зачем бэктест»). RTT в markout не входит — `rtt=assumed(...)` в шапке
//! не печатается.
//!
//! # Вход — тот же каталог сессий, что у `lob profiles`
//!
//! `--root` — либо каталог с `instruments.csv` пула и подкаталогом на
//! каждую сессию (`lob session`, раскладка `profiles`), либо сам каталог
//! сессии (в нём `session.json`; раскладка `dashboard`/`touches` —
//! `data/always-on/<ts>`, `data/session-debug/<id>`): тогда сессия одна,
//! пул — из его же `instruments.csv`. Сутки и части — `session_parts_for`/
//! `session_days_in_dir` (таск 23), реплей — `replay_symbol` (тот же код,
//! что `levels`/`markout`/`touches`/`dashboard`: трекер общий на части
//! суток, чистый на каждые сутки, касания — `ReplayDay.touches`). Маркер
//! `verify-<SYMBOL>.status == ok` обязателен для сессии (fail-closed, как у
//! `profiles`); `--allow-unverified` снимает требование для отладочных
//! данных и обязан нести метку `debug` в шапке — такой прогон данными не
//! является и `runs.csv` не пишет.
//!
//! # Оси, корзины, кластер
//!
//! Корзины — только `lob::touch_axes` (В-44; сторона/размер/длительность —
//! те же корзины, что у смертей: `profiles::size_bucket`/`lifetime_bucket`).
//! Размер — `size_at_touch × H3` (усохший ниже `H3` уровень ни в одну
//! корзину размера не входит, В-43: фильтр в анализе), фронтран — доля
//! `frontrun_lots / size_at_touch`, подход — за 1 с (`APPROACH_MS[0]`,
//! `approaches_for_touch`), возраст — `age_ms()`; касание без среза для
//! подхода в оси подхода отсутствует, в остальных — есть. Интервал `m` —
//! тот же `costs::net_fill_interval` с `filled = true` на каждом
//! наблюдении, что у `profiles` (`interfaces.md`, «Из таска 03»: второй
//! ресэмплер не писать): кластер — сутки, веса Уэбба, `alpha`/`replications`/
//! `seed` — `stats::GATE_ALPHA`/`BOOTSTRAP_REPLICATIONS`/
//! `profiles::BOOTSTRAP_SEED`, напечатаны в шапке. Верхняя граница — та же
//! функция на наблюдениях с обратным знаком (квантиль с линейной
//! интерполяцией симметричен: нижняя граница `−m` есть минус верхняя `m`),
//! не второй бутстрап и не второе число.
//!
//! # Число испытаний
//!
//! Длина сетки (`touch_grid_size`: `(26 + 6) × (инструментов + 1)`) идёт в
//! `runs.csv` по строке на id (`runs::log_touch_profile_trials`) — тот же
//! учёт, что у сетки смертей, чтобы DSR в шортлисте видел и эти испытания
//! (В-44). Журнал, не перезапись: повторный прогон — вторая партия строк.
//!
//! # Окно
//!
//! `--preregistration` — тот же файл и та же граница, что у `lob profiles`/
//! `shortlist` (`shortlist::load_or_write_window`, R57): сутки вне окна в
//! таблицу не входят (фильтр по суткам части после реплея — `replay_symbol`
//! читает каталог целиком, отдельного реплея по суткам у него нет), шапка
//! печатает `format_window_line`. Без файла окно **не предрегистрировано**
//! — шапка печатает это словами вместе с фактическим диапазоном суток и
//! числом сессий, не тихое «все сессии».

use std::collections::{BTreeMap, BTreeSet};
use std::io::Write as _;
use std::path::{Path, PathBuf};

use clap::Args;

use crate::lob::costs::{net_fill_interval, FillObservation};
use crate::lob::levels::{H3Mode, LevelsConfig, TouchRecord};
use crate::lob::markout::{approaches_for_touch, markouts_for_touch, MidSample, HORIZONS_MS};
use crate::lob::runs::log_touch_profile_trials;
use crate::lob::shortlist::{load_or_write_window, PreregisteredWindow};
use crate::lob::touch_axes::{
    age_bucket, approach_bucket, frontrun_bucket, frontrun_share, round_bucket, touch_cross_id,
    touch_index_bucket, touch_marginal_id, touch_outcome, touch_profile_grid, POOL_SCOPE,
};
use crate::stats::{count_f64_u64, BOOTSTRAP_REPLICATIONS, GATE_ALPHA};

use super::profiles::{
    distinct_session_days, format_window_line, h3_mode_label, hour_utc_of_ms, lifetime_bucket,
    read_pool_symbols, read_verify_marker, session_dirs, size_bucket, BOOTSTRAP_SEED,
};
use super::{
    replay_symbol, resolve_h3_mode_with_k, session_days_in_dir, session_parts_for, side_name,
    H3Args, DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS,
};

// ---------------------------------------------------------------------------
// CLI.
// ---------------------------------------------------------------------------

/// Аргументы `lob touch-profiles`. `--h3-mode` обязателен (тот же выбор,
/// что у `levels`/`touches`/`profiles`); `--h3-k` — пересчёт пола на лету
/// (таск 18), только с `floor`.
#[derive(Debug, Args)]
pub struct TouchProfilesArgs {
    /// Корень: `instruments.csv` пула плюс подкаталог на каждую сессию, либо
    /// сам каталог сессии (с `session.json`).
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Режим `H3`: `--h3-mode`, `--h3-lots` (общие для нескольких подкоманд).
    #[command(flatten)]
    pub h3: H3Args,
    /// Множитель относительного порога `floor` (таск 18, В-30/D05):
    /// `h3_lots = floor(k × median_trade_lots)` из `instruments.csv`.
    #[arg(long)]
    pub h3_k: Option<f64>,
    /// Прогрев в мс: только режим `percentile`; `floor` не читает.
    #[arg(long, default_value_t = DEFAULT_WARMUP_MS)]
    pub warmup_ms: i64,
    /// Скользящее окно `repeat_count` в мс — печатается в шапке.
    #[arg(long, default_value_t = DEFAULT_REPEAT_WINDOW_MS)]
    pub repeat_window_ms: i64,
    /// Отладочный вход без маркеров сверки: метка `debug` в шапке,
    /// `runs.csv` не пишется.
    #[arg(long, default_value_t = false)]
    pub allow_unverified: bool,
    /// Куда писать (по умолчанию `docs/findings/touch-profiles-<дата>.csv`).
    #[arg(long)]
    pub out: Option<PathBuf>,
    /// Момент для имени файла и журнала UTC (по умолчанию — сейчас).
    #[arg(long)]
    pub now_utc: Option<String>,
    /// Журнал испытаний — только боевой режим.
    #[arg(long, default_value = "docs/plan/runs.csv")]
    pub runs_out: PathBuf,
    /// Файл-предрегистрация окна «сейчас» — тот же, что у `lob profiles`/
    /// `shortlist` (R57). Без него окно не предрегистрировано — шапка
    /// печатает это словами и фактический диапазон суток.
    #[arg(long)]
    pub preregistration: Option<PathBuf>,
    /// Конец окна для файла предрегистрации с одной границей (как у
    /// `lob profiles --window-end`).
    #[arg(long)]
    pub window_end: Option<String>,
}

/// Итог `lob touch-profiles` для печати диспетчером: строк таблицы,
/// касаний прочитано, строк испытаний дописано в `runs.csv` (0 в отладке).
#[derive(Debug)]
pub struct TouchProfilesSummary {
    pub rows: usize,
    pub touches: usize,
    pub trials: usize,
    pub out: PathBuf,
    pub debug: bool,
}

// ---------------------------------------------------------------------------
// Накопитель профиля касаний.
// ---------------------------------------------------------------------------

/// Наблюдения одного профиля: счётчики исходов, `m` по горизонтам как
/// `FillObservation` с `filled = true` (вход `net_fill_interval`, doc
/// модуля), сутки-кластеры и часы UTC начала касаний.
#[derive(Default)]
struct TouchAgg {
    n: u64,
    bounced: u64,
    horizons: [Vec<FillObservation>; 4],
    days: BTreeSet<i64>,
    hours: BTreeSet<u32>,
}

impl TouchAgg {
    fn observe(&mut self, t: &TouchRecord, m: [Option<f64>; 4], day_id: i64, hour: u32) {
        self.n += 1;
        if !t.ended_by_death {
            self.bounced += 1;
        }
        self.days.insert(day_id);
        self.hours.insert(hour);
        for (obs, m_h) in self.horizons.iter_mut().zip(m) {
            if let Some(v) = m_h {
                obs.push(FillObservation {
                    day_cluster: day_id,
                    net_bps: v,
                    filled: true,
                });
            }
        }
    }
}

/// Корзины одного касания по девяти осям — считаются один раз и
/// раскладываются по двум областям (`pool` и инструмент). `None` — касание
/// в этой оси отсутствует (размер ниже `H3`, нет среза для подхода).
struct TouchLabels {
    side: &'static str,
    size: Option<&'static str>,
    age: Option<&'static str>,
    frontrun: Option<&'static str>,
    round: &'static str,
    index: &'static str,
    approach: Option<&'static str>,
    duration: Option<&'static str>,
    outcome: &'static str,
}

/// `size_at_touch / H3` — тот же расчёт, что у `dashboard::size_ratio`;
/// без положительного порога доли нет (ни одна корзина размера).
#[allow(clippy::cast_precision_loss)]
fn size_ratio(lots: i64, h3_lots: i64) -> Option<f64> {
    (h3_lots > 0).then(|| lots as f64 / h3_lots as f64)
}

fn touch_labels(t: &TouchRecord, mids: &[MidSample], h3_lots: i64) -> TouchLabels {
    TouchLabels {
        side: side_name(t.side),
        size: size_ratio(t.size_at_touch, h3_lots).and_then(size_bucket),
        age: age_bucket(t.age_ms()),
        frontrun: frontrun_share(t.frontrun_lots, t.size_at_touch).and_then(frontrun_bucket),
        round: round_bucket(t.round_zeros),
        index: touch_index_bucket(t.touch_index),
        approach: approaches_for_touch(t, mids)[0].and_then(approach_bucket),
        duration: lifetime_bucket(t.duration_ms),
        outcome: touch_outcome(t.ended_by_death),
    }
}

/// Id сетки, в которые входит касание в области `scope`: маргинал каждой
/// оси, где корзина определена, плюс клетка креста исход × возраст.
/// Порядок осей — как в `touch_axes::TOUCH_AXES`.
fn touch_ids(scope: &str, l: &TouchLabels) -> Vec<String> {
    let mut ids = vec![touch_marginal_id(scope, "side", l.side)];
    if let Some(s) = l.size {
        ids.push(touch_marginal_id(scope, "size", s));
    }
    if let Some(a) = l.age {
        ids.push(touch_marginal_id(scope, "age", a));
    }
    if let Some(f) = l.frontrun {
        ids.push(touch_marginal_id(scope, "frontrun", f));
    }
    ids.push(touch_marginal_id(scope, "round", l.round));
    ids.push(touch_marginal_id(scope, "index", l.index));
    if let Some(a) = l.approach {
        ids.push(touch_marginal_id(scope, "approach", a));
    }
    if let Some(d) = l.duration {
        ids.push(touch_marginal_id(scope, "duration", d));
    }
    ids.push(touch_marginal_id(scope, "outcome", l.outcome));
    if let Some(a) = l.age {
        ids.push(touch_cross_id(scope, l.outcome, a));
    }
    ids
}

// ---------------------------------------------------------------------------
// Каталог: сессии под корнем или сам корень.
// ---------------------------------------------------------------------------

/// Сессии для чтения: корень с `session.json` — одна сессия, он сам;
/// иначе — его подкаталоги (`profiles::session_dirs`), без `session.json`
/// они отсеются на `session_parts_for` молча, как у `profiles`.
fn session_roots(root: &Path) -> anyhow::Result<Vec<PathBuf>> {
    if root.join("session.json").is_file() {
        return Ok(vec![root.to_path_buf()]);
    }
    session_dirs(root)
}

/// Сутки → целый кластер, один на весь прогон (тот же день у разных
/// инструментов — один и тот же кластер, иначе строки `pool` считали бы
/// одни сутки дважды).
fn day_id(index: &mut BTreeMap<String, i64>, day_utc: &str) -> i64 {
    if let Some(&id) = index.get(day_utc) {
        return id;
    }
    let id = i64::try_from(index.len()).expect("число суток в памяти умещается в i64");
    index.insert(day_utc.to_string(), id);
    id
}

// ---------------------------------------------------------------------------
// Запуск.
// ---------------------------------------------------------------------------

/// Точка входа `lob touch-profiles`: пул из `instruments.csv`, сетка
/// `touch_profile_grid`, реплей каждой сверенной сессии каждого символа,
/// полная таблица, строки испытаний в `runs.csv` (боевой режим).
pub fn run_touch_profiles(args: &TouchProfilesArgs) -> anyhow::Result<TouchProfilesSummary> {
    debug_assert_eq!(
        HORIZONS_MS,
        [100, 1_000, 10_000, 60_000],
        "порядок колонок m_* обязан совпадать с горизонтами"
    );
    let instruments_csv = crate::commands::record::instruments_csv_path(&args.root);
    let pool = read_pool_symbols(&instruments_csv)?;
    let grid = touch_profile_grid(&pool);
    let mut profiles: BTreeMap<String, TouchAgg> = grid
        .iter()
        .cloned()
        .map(|id| (id, TouchAgg::default()))
        .collect();

    let dirs = session_roots(&args.root)?;
    let window: Option<PreregisteredWindow> = match &args.preregistration {
        Some(path) => {
            let days_now = distinct_session_days(&dirs);
            Some(
                load_or_write_window(path, &days_now, args.window_end.as_deref())
                    .map_err(|e| anyhow::anyhow!("{e}"))?,
            )
        }
        None => None,
    };
    // Счёт сессий по парам (каталог, сутки), как у `profiles` (таск 23).
    let mut sessions_in_window = 0usize;
    let mut sessions_outside_window = 0usize;
    let mut days_present: BTreeSet<String> = BTreeSet::new();
    for dir in &dirs {
        for day in session_days_in_dir(dir) {
            match &window {
                Some(w) if !w.contains_day(&day) => sessions_outside_window += 1,
                _ => {
                    sessions_in_window += 1;
                    days_present.insert(day);
                }
            }
        }
    }

    let mut day_index: BTreeMap<String, i64> = BTreeMap::new();
    let mut touches_total = 0usize;
    for symbol in &pool {
        let mode = resolve_h3_mode_with_k(
            &args.root,
            symbol,
            args.h3.h3_mode,
            args.h3.h3_lots,
            args.h3_k,
        )?;
        let h3_lots = match mode {
            H3Mode::Floor { h3_lots } | H3Mode::Percentile { h3_lots } => h3_lots,
        };
        let cfg = LevelsConfig {
            mode,
            warmup_ms: args.warmup_ms,
            repeat_window_ms: args.repeat_window_ms,
        };
        for dir in &dirs {
            // Не сессия этого символа (нет `session.json` или файлов) —
            // мягкий пропуск на обходе многих каталогов, как у `profiles`.
            if session_parts_for(dir, symbol).is_err() {
                continue;
            }
            let verified = read_verify_marker(&dir.join(format!("verify-{symbol}.status")));
            if !verified && !args.allow_unverified {
                continue;
            }
            let replay = replay_symbol(dir, symbol, cfg)?;
            for day in &replay.days {
                if let Some(w) = &window {
                    if !w.contains_day(&day.day) {
                        continue;
                    }
                }
                let cluster = day_id(&mut day_index, &day.day);
                for t in &day.touches {
                    touches_total += 1;
                    let labels = touch_labels(t, &day.mids, h3_lots);
                    let m = markouts_for_touch(t, &day.mids);
                    let hour = hour_utc_of_ms(t.start_ms);
                    for scope in [POOL_SCOPE, symbol.as_str()] {
                        for id in touch_ids(scope, &labels) {
                            profiles
                                .get_mut(&id)
                                .expect("id касания всегда есть в сетке touch_profile_grid")
                                .observe(t, m, cluster, hour);
                        }
                    }
                }
            }
        }
    }

    let now = args
        .now_utc
        .clone()
        .unwrap_or_else(|| chrono::Utc::now().to_rfc3339());
    let out = args.out.clone().unwrap_or_else(|| default_out_path(&now));
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut file = std::fs::File::create(&out)?;
    let debug_suffix = if args.allow_unverified { " debug" } else { "" };
    let h3_k = args.h3_k.map(|k| format!(" h3_k={k}")).unwrap_or_default();
    writeln!(
        file,
        "# lob touch-profiles: h3_mode={}{h3_k} warmup_ms={} repeat_window_ms={} alpha={GATE_ALPHA} replications={BOOTSTRAP_REPLICATIONS} seed={BOOTSTRAP_SEED}{debug_suffix}",
        h3_mode_label(args.h3.h3_mode),
        args.warmup_ms,
        args.repeat_window_ms,
    )?;
    let window_line = match &window {
        Some(_) => format_window_line(&window, sessions_in_window, sessions_outside_window)?,
        None => match (days_present.first(), days_present.last()) {
            (Some(first), Some(last)) => format!(
                "window: not preregistered {first}..{last} days={} sessions={sessions_in_window}",
                days_present.len()
            ),
            _ => "window: not preregistered (no sessions)".to_string(),
        },
    };
    writeln!(file, "# {window_line}")?;
    let mut w = csv::WriterBuilder::new()
        .has_headers(false)
        .from_writer(file);
    w.write_record(HEADER)?;
    for (id, agg) in &profiles {
        write_row(&mut w, id, agg)?;
    }
    w.flush()?;

    let trials = if args.allow_unverified {
        0
    } else {
        log_touch_profile_trials(&args.runs_out, &now, &grid)?;
        grid.len()
    };

    Ok(TouchProfilesSummary {
        rows: profiles.len(),
        touches: touches_total,
        trials,
        out,
        debug: args.allow_unverified,
    })
}

// ---------------------------------------------------------------------------
// Таблица.
// ---------------------------------------------------------------------------

/// Шапка `touch-profiles-<дата>.csv`: id, число касаний, доля отскоков,
/// по три колонки на горизонт (`m`, нижняя и верхняя границы), годные
/// сутки, часы UTC начала касаний.
pub(crate) const HEADER: [&str; 17] = [
    "profile_id",
    "n",
    "share_bounced",
    "m_100ms",
    "m_100ms_lower",
    "m_100ms_upper",
    "m_1000ms",
    "m_1000ms_lower",
    "m_1000ms_upper",
    "m_10000ms",
    "m_10000ms_lower",
    "m_10000ms_upper",
    "m_60000ms",
    "m_60000ms_lower",
    "m_60000ms_upper",
    "n_days",
    "level_hours_utc",
];

fn fmt_opt(v: Option<f64>) -> String {
    match v {
        Some(x) => format!("{x:.6}"),
        None => "none".to_string(),
    }
}

/// `m` с границами на одном горизонте: точка и нижняя — `net_fill_interval`
/// как у `profiles`; верхняя — та же функция на наблюдениях с обратным
/// знаком, взятая с минусом (doc модуля, `negate` без `-0.0`). `None` —
/// наблюдений на горизонте нет.
fn horizon_columns(obs: &[FillObservation]) -> (Option<f64>, Option<f64>, Option<f64>) {
    let lower_side = net_fill_interval(obs, GATE_ALPHA, BOOTSTRAP_REPLICATIONS, BOOTSTRAP_SEED);
    let negated: Vec<FillObservation> = obs
        .iter()
        .map(|o| FillObservation {
            net_bps: -o.net_bps,
            ..*o
        })
        .collect();
    let upper_side =
        net_fill_interval(&negated, GATE_ALPHA, BOOTSTRAP_REPLICATIONS, BOOTSTRAP_SEED);
    (
        lower_side.map(|i| i.point_bps),
        lower_side.map(|i| i.lower_bps),
        upper_side.map(|i| negate(i.lower_bps)),
    )
}

/// Минус без `-0.0`: нулевой сдвиг печатается как `0.000000`, а не
/// `-0.000000` (`-0.0 == 0.0`, ветка ловит оба нуля).
fn negate(x: f64) -> f64 {
    if x == 0.0 {
        0.0
    } else {
        -x
    }
}

fn write_row(w: &mut csv::Writer<std::fs::File>, id: &str, agg: &TouchAgg) -> anyhow::Result<()> {
    let share_bounced = (agg.n > 0).then(|| count_f64_u64(agg.bounced) / count_f64_u64(agg.n));
    let mut record = vec![id.to_string(), agg.n.to_string(), fmt_opt(share_bounced)];
    for obs in &agg.horizons {
        let (m, lower, upper) = horizon_columns(obs);
        record.push(fmt_opt(m));
        record.push(fmt_opt(lower));
        record.push(fmt_opt(upper));
    }
    record.push(agg.days.len().to_string());
    let hours: Vec<String> = agg.hours.iter().map(ToString::to_string).collect();
    record.push(hours.join(","));
    debug_assert_eq!(record.len(), HEADER.len());
    w.write_record(record)?;
    Ok(())
}

fn default_out_path(now: &str) -> PathBuf {
    let date = now.get(..10).unwrap_or("1970-01-01");
    PathBuf::from(format!("docs/findings/touch-profiles-{date}.csv"))
}

#[cfg(test)]
mod tests;
