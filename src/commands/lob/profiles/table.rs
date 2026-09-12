//! Запись `profiles-<дата>.csv`: строка `window: …` шапки, метка режима
//! `H3`, шапка колонок (`HEADER`), строка на профиль (`write_row`) и путь
//! артефакта по умолчанию. Только форматирование посчитанного — сами числа
//! копит `accumulate`.

use std::path::PathBuf;

use crate::commands::lob::H3ModeArg;
use crate::lob::costs::{
    fill_rate, format_fill_column, mean_net_bps, net_fill_bps, net_fill_interval,
};
use crate::lob::final_metrics::sharpe_ratio;
use crate::lob::shortlist::{window_length_days, PreregisteredWindow};
use crate::stats::{count_f64, count_f64_u64, BOOTSTRAP_REPLICATIONS, GATE_ALPHA};

use super::accumulate::{HorizonAgg, ProfileAgg};
use super::BOOTSTRAP_SEED;

/// Литерал колонок `unreachable_<horizon>` (см. doc модуля: нет живого
/// отчёта G-LAT — нет числа).
const NOT_MEASURED: &str = "not_measured";

/// Строка `window: …` шапки (ticket 21, R57) — общий формат для
/// `profiles-<дата>.csv` и `shortlist-<дата>.md`
/// (`commands::lob::shortlist::run_shortlist` печатает её той же функцией):
/// `debug (all sessions)` в отладке, иначе границы плюс длина в сутках
/// (из границ, не из числа сессий), число сессий внутри/вне окна и сама
/// пара разведочная/подтверждающая, из которой окно выведено.
pub(crate) fn format_window_line(
    window: &Option<PreregisteredWindow>,
    sessions_in_window: usize,
    sessions_outside_window: usize,
) -> anyhow::Result<String> {
    let Some(w) = window else {
        return Ok("window: debug (all sessions)".to_string());
    };
    let days = window_length_days(w).map_err(|e| anyhow::anyhow!("{e}"))?;
    let expl_start = w.exploratory.first().cloned().unwrap_or_default();
    let expl_end = w.exploratory.last().cloned().unwrap_or_default();
    let conf_start = w.confirmatory.first().cloned().unwrap_or_default();
    let conf_end = w.confirmatory.last().cloned().unwrap_or_default();
    Ok(format!(
        "window: {}..{} days={days} sessions={sessions_in_window} \
         sessions_outside_window={sessions_outside_window} exploratory={expl_start}..{expl_end} \
         confirmatory={conf_start}..{conf_end}",
        w.start, w.end
    ))
}

pub(super) fn h3_mode_label(mode: H3ModeArg) -> &'static str {
    match mode {
        H3ModeArg::Floor => "floor",
        H3ModeArg::Percentile => "percentile",
    }
}

fn fmt_opt(v: Option<f64>) -> String {
    match v {
        Some(x) => format!("{x:.6}"),
        None => "none".to_string(),
    }
}

fn mean(vals: &[f64]) -> Option<f64> {
    if vals.is_empty() {
        return None;
    }
    Some(vals.iter().sum::<f64>() / count_f64(vals.len()))
}

struct HorizonColumns {
    m: Option<f64>,
    m_lower: Option<f64>,
    raw: Option<f64>,
}

fn horizon_columns(agg: &HorizonAgg) -> HorizonColumns {
    let interval = net_fill_interval(
        &agg.m_obs,
        GATE_ALPHA,
        BOOTSTRAP_REPLICATIONS,
        BOOTSTRAP_SEED,
    );
    HorizonColumns {
        m: interval.map(|i| i.point_bps),
        m_lower: interval.map(|i| i.lower_bps),
        raw: mean(&agg.raw_vals),
    }
}

/// Шапка `profiles-<дата>.csv`: 5 общих колонок плюс 4 на каждый из четырёх
/// горизонтов (`m`, нижняя граница, сырое движение, флаг `unreachable`)
/// плюс деньги плюс час старта сессий плюс `g` (ремонт по ревью таска 12,
/// открытый пункт (1) для таска 13). `g` — новая, дописанная в конец колонка:
/// `commands::lob::shortlist::read_profile_table` матчит по имени, а не по
/// позиции (доктрока там же), так что дописывание в конец не ломает читателя.
///
/// `level_hours_utc` (таск 30, В-36) — часы UTC рождения уровней, то есть
/// **ось** «час» (§6а брифа) и вход `hour_dependence_test`. Стоит рядом с
/// `session_start_hours_utc`, которая осталась и в осях не участвует: это
/// часы старта частей записи, под олвейс-оном — константа
/// (`ProfileAgg::hours`). Вставка в середину, а не в конец, безопасна по той
/// же причине, что и дописывание: оба читателя таблицы
/// (`shortlist::read_profile_table`, `backtest::read_table`) матчат колонки
/// по имени и лишние пропускают (их доктроки говорят это прямо), ни ту, ни
/// другую колонку часа не читают, — а `observed_sharpe` остаётся последней,
/// как её и проверяют тесты таска 16.
pub(super) const HEADER: [&str; 29] = [
    "profile_id",
    "n",
    "eaten_share",
    "pulled_share",
    "mixed_share",
    "m_100ms",
    "m_100ms_lower",
    "raw_100ms",
    "unreachable_100ms",
    "m_1000ms",
    "m_1000ms_lower",
    "raw_1000ms",
    "unreachable_1000ms",
    "m_10000ms",
    "m_10000ms_lower",
    "raw_10000ms",
    "unreachable_10000ms",
    "m_60000ms",
    "m_60000ms_lower",
    "raw_60000ms",
    "unreachable_60000ms",
    "net_bps",
    "fill",
    "net_fill",
    "net_fill_lower",
    "session_start_hours_utc",
    "level_hours_utc",
    "g",
    "observed_sharpe",
];

/// `measured` — есть ли активная модель исполнения (`FillModel::label() !=
/// "none"`): `false` печатает `not_measured` в `fill`/`net_fill`/
/// `net_fill_lower` буквально, независимо от того, пуст ли `agg.fill_obs`
/// (он и обязан быть пуст под `NoFillModel` — см. doc `ProfileAgg`), а не
/// молчаливый `none`, который читатель мог бы принять за измеренный ноль.
pub(super) fn write_row(
    w: &mut csv::Writer<std::fs::File>,
    id: &str,
    agg: &ProfileAgg,
    measured: bool,
) -> anyhow::Result<()> {
    let n = agg.eaten + agg.pulled + agg.mixed;
    let (eaten_share, pulled_share, mixed_share) = if n > 0 {
        let nf = count_f64_u64(n);
        (
            Some(count_f64_u64(agg.eaten) / nf),
            Some(count_f64_u64(agg.pulled) / nf),
            Some(count_f64_u64(agg.mixed) / nf),
        )
    } else {
        (None, None, None)
    };
    let hc: Vec<HorizonColumns> = agg.horizons.iter().map(horizon_columns).collect();
    let net = mean_net_bps(&agg.net_obs);
    let (fill_col, net_fill_col, net_fill_lower_col) = if measured {
        let fill = fill_rate(&agg.fill_obs);
        let net_fill_point = net_fill_bps(&agg.fill_obs);
        let net_fill_lower = net_fill_interval(
            &agg.fill_obs,
            GATE_ALPHA,
            BOOTSTRAP_REPLICATIONS,
            BOOTSTRAP_SEED,
        )
        .map(|i| i.lower_bps);
        (
            format_fill_column(fill),
            fmt_opt(net_fill_point),
            fmt_opt(net_fill_lower),
        )
    } else {
        (
            NOT_MEASURED.to_string(),
            NOT_MEASURED.to_string(),
            NOT_MEASURED.to_string(),
        )
    };
    let hours: Vec<String> = agg.hours.iter().map(ToString::to_string).collect();
    let level_hours: Vec<String> = agg.level_hours.iter().map(ToString::to_string).collect();
    // `observed_sharpe` (таск 16) — Шарп круговых net **исполнившихся**
    // входов (`fill_obs` фильтрован по `filled == true`), в единицах на
    // наблюдение — то же самое множество наблюдений, из которого уже
    // считаются `fill`/`net_fill`/`net_fill_lower` (doc `FillModel`, «из тех
    // же наблюдений», ticket 16). `not_measured` без активной модели
    // (`measured == false`, `fill_obs` тогда и так пуст); `none` — модель
    // активна, но исполнений меньше двух (`sharpe_ratio` требует дисперсию).
    let observed_sharpe_col = if measured {
        let filled_net: Vec<f64> = agg
            .fill_obs
            .iter()
            .filter(|o| o.filled)
            .map(|o| o.net_bps)
            .collect();
        fmt_opt(sharpe_ratio(&filled_net))
    } else {
        NOT_MEASURED.to_string()
    };

    let mut record = vec![
        id.to_string(),
        n.to_string(),
        fmt_opt(eaten_share),
        fmt_opt(pulled_share),
        fmt_opt(mixed_share),
    ];
    for h in &hc {
        record.push(fmt_opt(h.m));
        record.push(fmt_opt(h.m_lower));
        record.push(fmt_opt(h.raw));
        record.push(NOT_MEASURED.to_string());
    }
    record.push(fmt_opt(net));
    record.push(fill_col);
    record.push(net_fill_col);
    record.push(net_fill_lower_col);
    record.push(hours.join(","));
    record.push(level_hours.join(","));
    record.push(agg.days.len().to_string());
    record.push(observed_sharpe_col);
    w.write_record(record)?;
    Ok(())
}

pub(super) fn default_out_path(now: &str) -> PathBuf {
    let date = now.get(..10).unwrap_or("1970-01-01");
    PathBuf::from(format!("docs/findings/profiles-{date}.csv"))
}
