//! `lob markout` — CLI-обёртка markout на четырёх горизонтах (шаг 2.1).
//! Сам расчёт — `crate::lob::markout`; реплей суточных файлов — общий с
//! `levels`/`watch`/`pilot` код в `mod.rs`; подтверждающий режим переиспользует
//! `crate::lob::markup::run_confirmatory` и общий предикат годности суток.

use std::path::{Path, PathBuf};

use clap::Args;

use crate::lob::levels::LevelsConfig;
use crate::lob::markout::{markouts_for_level, HORIZONS_MS};
use crate::lob::markup::{run_confirmatory, ConfirmatoryDay};
use crate::lob::watch::{
    ready_flag_path, require_ready_flag, require_session_ready_flag, session_ready_flag_path,
};

use super::{
    day_tallies, outcome_name, replay_symbol, side_name, some_or_empty, ReplayDay,
    DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS,
};
use super::{resolve_h3_mode, H3Args};

// ---------------------------------------------------------------------------
// `lob markout` (шаг 2.1, Decision 14).
// ---------------------------------------------------------------------------

/// Аргументы `lob markout`: `m` на четырёх горизонтах по Decision 14.
/// С `--confirmatory` читает ровно сутки из `ready.flag` и без флага
/// отказывается стартовать ненулевым кодом (Decision 21): это механическая
/// защита от optional stopping, а не обещание не подглядывать.
#[derive(Debug, Args)]
pub struct MarkoutArgs {
    /// Корень записи: суточные файлы `<SYMBOL>-*.binlog`.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Символ, например `SOLUSDT`.
    #[arg(long)]
    pub symbol: String,
    /// Режим `H3`: `--h3-mode`, `--h3-lots` (общие для нескольких подкоманд,
    /// тот же выбор, что у `levels`).
    #[command(flatten)]
    pub h3: H3Args,
    /// Прогрев в мс.
    #[arg(long, default_value_t = DEFAULT_WARMUP_MS)]
    pub warmup_ms: i64,
    /// Скользящее окно `repeat_count` в мс.
    #[arg(long, default_value_t = DEFAULT_REPEAT_WINDOW_MS)]
    pub repeat_window_ms: i64,
    /// Куда писать markout (по умолчанию `<root>/markout-<symbol>.csv`).
    #[arg(long)]
    pub out: Option<PathBuf>,
    /// Подтверждающий режим: сутки ровно из флага, без флага — отказ.
    #[arg(long, default_value_t = false)]
    pub confirmatory: bool,
    /// Путь к `ready.flag` (по умолчанию `<root>/ready.flag`).
    #[arg(long)]
    pub flag: Option<PathBuf>,
    /// Медиана `lifetime_ms` популяции C1 по первым суткам (Decision 16):
    /// нужна только в `--confirmatory` для счётчиков суток.
    #[arg(long)]
    pub median_lifetime_ms: Option<i64>,
    /// Вердиктный профиль (таск 07, `crate::lob::watch`/`shortlist::build_profile_grid`):
    /// только для `--confirmatory` — без него читать `ready-<symbol>-<profile>.flag`
    /// (`SessionReadyFlag`) не по чему, и команда отказывается стартовать.
    #[arg(long)]
    pub profile: Option<String>,
    /// Замороженный шорт-лист (таск 12, `lob shortlist --freeze-out`): только
    /// для `--confirmatory` — без него или с профилем не из списка команда
    /// отказывается стартовать (Decision 27/28: заморозка коммитом обязана
    /// предшествовать первому чтению подтверждающей, механически, не по
    /// договорённости).
    #[arg(long)]
    pub shortlist: Option<PathBuf>,
}

/// Итог `lob markout` для печати диспетчером.
pub struct MarkoutSummary {
    pub days: usize,
    pub levels: usize,
    pub out: PathBuf,
    pub horizon_line: String,
}

/// Среднее доступных значений горизонта одной строкой: отсутствие данных —
/// `n/a`, а не ноль (нет будущего — не нулевой сдвиг).
fn horizon_stat(name: &str, values: &[f64]) -> String {
    if values.is_empty() {
        return format!("{name}: n/a");
    }
    let mean = values.iter().sum::<f64>() / crate::stats::count_f64(values.len());
    format!("{name}: n={} mean={mean:.3}bps", values.len())
}

fn write_markout_csv(out: &Path, days: &[ReplayDay]) -> anyhow::Result<(usize, [Vec<f64>; 4])> {
    debug_assert_eq!(
        HORIZONS_MS,
        [100, 1_000, 10_000, 60_000],
        "порядок колонок обязан совпадать с горизонтами"
    );
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut w = csv::Writer::from_path(out)?;
    w.write_record([
        "day_utc",
        "side",
        "price_tick",
        "birth_ms",
        "death_ms",
        "outcome",
        "m_100ms",
        "m_1s",
        "m_10s",
        "m_60s",
    ])?;
    let mut n = 0usize;
    let mut per_horizon: [Vec<f64>; 4] = [Vec::new(), Vec::new(), Vec::new(), Vec::new()];
    for day in days {
        for r in &day.records {
            let ms = markouts_for_level(r, &day.mids);
            for (slot, v) in per_horizon.iter_mut().zip(ms) {
                if let Some(v) = v {
                    slot.push(v);
                }
            }
            w.write_record([
                day.day.clone(),
                side_name(r.side).to_string(),
                r.price_tick.to_string(),
                r.birth_ms.to_string(),
                r.death_ms.to_string(),
                outcome_name(r.outcome()).to_string(),
                some_or_empty(ms[0]),
                some_or_empty(ms[1]),
                some_or_empty(ms[2]),
                some_or_empty(ms[3]),
            ])?;
            n += 1;
        }
    }
    w.flush()?;
    Ok((n, per_horizon))
}

fn horizon_line(per_horizon: &[Vec<f64>; 4]) -> String {
    format!(
        "{}; {}; {}; {}",
        horizon_stat("m_100ms", &per_horizon[0]),
        horizon_stat("m_1s", &per_horizon[1]),
        horizon_stat("m_10s", &per_horizon[2]),
        horizon_stat("m_60s", &per_horizon[3]),
    )
}

/// Разведочный markout: все сутки корня, флаг не требуется и не читается.
fn run_markout_exploratory(args: &MarkoutArgs) -> anyhow::Result<MarkoutSummary> {
    let mode = resolve_h3_mode(&args.root, &args.symbol, args.h3.h3_mode, args.h3.h3_lots)?;
    let cfg = LevelsConfig {
        mode,
        warmup_ms: args.warmup_ms,
        repeat_window_ms: args.repeat_window_ms,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let replay = replay_symbol(&args.root, &args.symbol, cfg)?;
    let out = args
        .out
        .clone()
        .unwrap_or_else(|| args.root.join(format!("markout-{}.csv", args.symbol)));
    let (n, per_horizon) = write_markout_csv(&out, &replay.days)?;
    Ok(MarkoutSummary {
        days: replay.days.len(),
        levels: n,
        out,
        horizon_line: horizon_line(&per_horizon),
    })
}

/// Подтверждающий markout: первым делом флаг (без него — отказ до всякого
/// markout), дальше ровно сутки из списка флага через общий
/// `markup::run_confirmatory`. Счётчики суток — тем же `tally_day`, что
/// `watch`: предикат годности один на весь документ.
fn run_markout_confirmatory(args: &MarkoutArgs) -> anyhow::Result<MarkoutSummary> {
    // Таск 07: подтверждающий прогон обязан требовать сессионный флаг
    // готовности (`WatchSample`/`SessionReadyFlag` — счётчик n/G на
    // сессиях), не только старый `ready.flag` модели C1/C2 ниже — иначе
    // честная остановка сессионного счётчика этот вход вообще не охраняет.
    // Первым делом, до всякого markout и до старого флага.
    let profile = args.profile.clone().ok_or_else(|| {
        anyhow::anyhow!("--confirmatory требует --profile (таск 07: сессионный флаг готовности)")
    })?;
    let session_flag_path = session_ready_flag_path(&args.root, &args.symbol, &profile);
    require_session_ready_flag(&session_flag_path)
        .map_err(|e| anyhow::anyhow!("сессионный флаг готовности: {e}"))?;

    // Таск 12: заморозка шорт-листа — механическая проверка, не обещание.
    // Профиль вне замороженного списка (или список ещё не существует)
    // отказывает здесь же, до всякого markout.
    let shortlist_path = args.shortlist.clone().ok_or_else(|| {
        anyhow::anyhow!("--confirmatory требует --shortlist (таск 12: заморозка коммитом)")
    })?;
    super::shortlist::require_shortlist_member(&shortlist_path, &profile)?;

    let flag_path = args
        .flag
        .clone()
        .unwrap_or_else(|| ready_flag_path(&args.root));
    // Дальше — старый флаг, до всякого markout: без него — отказ, а не
    // пустая выборка (тот же порядок, что `markup::run_confirmatory`).
    let flag = require_ready_flag(&flag_path).map_err(|e| anyhow::anyhow!("{e}"))?;
    // `--median-lifetime-ms` остаётся обязательным флагом (поведение команды
    // не меняется), но само число больше не нужно ни одному вычислению:
    // таск 17 снял счёт по ячейкам исхода-и-истории, для которого эта медиана
    // и заводилась (Decision 16 — снятое). `_` спереди — не забытая проводка.
    let _median = args.median_lifetime_ms.ok_or_else(|| {
        anyhow::anyhow!("--confirmatory требует --median-lifetime-ms (Decision 16)")
    })?;
    let mode = resolve_h3_mode(&args.root, &args.symbol, args.h3.h3_mode, args.h3.h3_lots)?;
    let cfg = LevelsConfig {
        mode,
        warmup_ms: args.warmup_ms,
        repeat_window_ms: args.repeat_window_ms,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let replay = replay_symbol(&args.root, &args.symbol, cfg)?;
    let tallies = day_tallies(&args.root, &args.symbol, &replay.days)?;
    let mut cdays: Vec<ConfirmatoryDay<'_>> = Vec::new();
    for want in &flag.days {
        let day = replay
            .days
            .iter()
            .find(|d| &d.day == want)
            .ok_or_else(|| anyhow::anyhow!("сутки {want} из флага не найдены среди реплея"))?;
        let tally = tallies
            .iter()
            .find(|t| &t.day_utc == want)
            .ok_or_else(|| anyhow::anyhow!("счётчик суток {want} не собрался"))?;
        cdays.push(ConfirmatoryDay {
            tally,
            records: &day.records,
            mids: &day.mids,
        });
    }
    let report = run_confirmatory(&flag_path, &cdays).map_err(|e| anyhow::anyhow!("{e}"))?;
    let out = args.out.clone().unwrap_or_else(|| {
        args.root
            .join(format!("markout-confirmatory-{}.csv", args.symbol))
    });
    let selected: Vec<ReplayDay> = replay
        .days
        .into_iter()
        .filter(|d| flag.days.iter().any(|w| w == &d.day))
        .collect();
    let (n, _) = write_markout_csv(&out, &selected)?;
    Ok(MarkoutSummary {
        days: report.days_used.len(),
        levels: n,
        out,
        horizon_line: report.summary_line(),
    })
}

/// Точка входа `lob markout`: разведочный режим пишет `m` на четырёх
/// горизонтах, подтверждающий — идёт через флаг и вердикт G1.
pub fn run_markout(args: &MarkoutArgs) -> anyhow::Result<MarkoutSummary> {
    if args.confirmatory {
        run_markout_confirmatory(args)
    } else {
        run_markout_exploratory(args)
    }
}

#[cfg(test)]
mod tests;
