//! Строка `touches-<SYMBOL>.csv` и `approaches-<SYMBOL>.csv` (W5в/г).
//!
//! Раньше `TOUCHES_COLUMNS` (имена, 61 строка) и массив `row` внутри
//! `run_touches` (значения, тоже 61 выражение) были двумя независимыми
//! литералами, сверенными компилятором только по длине (`[String; N]` и
//! `[&str; N]` с одним и тем же `N`): переставить местами два соседних имени
//! в одном массиве, забыв про значения в другом (или наоборот), компилировалось
//! молча — колонки съезжали по смыслу, а не по счёту. Здесь одна пара «имя →
//! значение» на позицию (`touch_row_pairs`/`approach_row_pairs`): переставить
//! имя без значения уже негде, они одна запись массива. `TOUCHES_COLUMNS`/
//! `APPROACHES_COLUMNS` (заголовок CSV, `super`) остаются отдельными
//! константами; `touches/tests.rs` сверяет с ними порядок имён из пар.
//!
//! Всю арифметику строки (markout, подход, `σ`, экскурсия, ход до касания)
//! эта функция и делает — раньше это было тело цикла `run_touches` (W5в:
//! часть тринадцати задач той функции).

use crate::commands::lob::bounce_verdict::DEADLINE_SECS;
use crate::commands::lob::replay::ReplayDay;
use crate::commands::lob::{side_name, some_or_empty};
use crate::lob::excursion::SecondMids;
use crate::lob::levels::{ApproachRecord, TouchRecord};
use crate::lob::markout::{
    approaches_for_touch, distance_bps_at_birth, long_markouts_for_touch, markouts_for_touch,
    touch_base, within_touch,
};
use crate::lob::sigma::SigmaSeries;

use super::{APPROACHES_COLUMNS, APPROACHES_WIDTH, PRE_TOUCH_MS, TOUCHES_COLUMNS, TOUCHES_WIDTH};

/// Сила «×соседи» в процентах из `strength_e2`; `-1` (соседей нет) — пусто.
/// Обратное — `read::strength_e2`.
fn strength_pct(e2: i64) -> String {
    if e2 < 0 {
        String::new()
    } else {
        format!("{}.{:02}", e2 / 100, e2 % 100)
    }
}

/// Пары «имя колонки → значение» одной строки касания, в порядке
/// `TOUCHES_COLUMNS`. Вся арифметика (markout на четырёх горизонтах, `σ`,
/// экскурсия, подход, длинные горизонты, ход до касания) — здесь же, один
/// раз на касание.
fn touch_row_pairs(
    day: &ReplayDay,
    t: &TouchRecord,
    sigma_series: &SigmaSeries,
    second_mids: &SecondMids,
) -> [(&'static str, String); TOUCHES_WIDTH] {
    let sigma: [Option<f64>; DEADLINE_SECS.len()] =
        std::array::from_fn(|k| sigma_series.sigma_bps(t.start_ms, DEADLINE_SECS[k] as i64));
    let excursion: [Option<crate::lob::excursion::Excursion>; DEADLINE_SECS.len()] =
        std::array::from_fn(|k| {
            let (_, base2x) = touch_base(&day.mids, t.start_ms)?;
            second_mids.excursion(t.side, base2x, t.start_ms, DEADLINE_SECS[k] as i64)
        });
    let ms = markouts_for_touch(t, &day.mids);
    let inside = within_touch(t.duration_ms);
    let ap = approaches_for_touch(t, &day.mids);
    let long = long_markouts_for_touch(t, &day.mids);
    let pre: [Option<f64>; PRE_TOUCH_MS.len()] = std::array::from_fn(|k| {
        super::pre_touch_return_bps(&day.mids, t.start_ms, PRE_TOUCH_MS[k])
    });
    [
        ("day_utc", day.day.clone()),
        ("side", side_name(t.side).to_string()),
        ("price_tick", t.price_tick.to_string()),
        ("touch_index", t.touch_index.to_string()),
        ("start_ms", t.start_ms.to_string()),
        ("end_ms", t.end_ms.to_string()),
        ("duration_ms", t.duration_ms.to_string()),
        ("birth_ms", t.level_birth_ms.to_string()),
        ("age_ms", t.age_ms().to_string()),
        ("size_at_touch", t.size_at_touch.to_string()),
        ("size_max_before", t.size_max_before.to_string()),
        ("traded_during", t.traded_during.to_string()),
        ("frontrun_lots", t.frontrun_lots.to_string()),
        (
            "frontrun_tick",
            t.frontrun_tick.map_or(String::new(), |v| v.to_string()),
        ),
        ("swept_lots", t.swept_lots.to_string()),
        ("round_zeros", t.round_zeros.to_string()),
        ("ended_by_death", t.ended_by_death.to_string()),
        ("stack_levels", t.stack_levels.to_string()),
        (
            "dist_bps",
            some_or_empty(distance_bps_at_birth(
                &day.mids,
                t.level_birth_ms,
                t.price_tick,
            )),
        ),
        ("m_100ms", some_or_empty(ms[0])),
        ("m_1s", some_or_empty(ms[1])),
        ("m_10s", some_or_empty(ms[2])),
        ("m_60s", some_or_empty(ms[3])),
        ("within_touch_100ms", inside[0].to_string()),
        ("within_touch_1s", inside[1].to_string()),
        ("within_touch_10s", inside[2].to_string()),
        ("within_touch_60s", inside[3].to_string()),
        ("approach_1s", some_or_empty(ap[0])),
        ("approach_10s", some_or_empty(ap[1])),
        ("m_10m", some_or_empty(long[0])),
        ("m_1h", some_or_empty(long[1])),
        ("m_2h", some_or_empty(long[2])),
        ("strength_w10_pct", strength_pct(t.strength_e2[0])),
        ("strength_w20_pct", strength_pct(t.strength_e2[1])),
        ("strength_w50_pct", strength_pct(t.strength_e2[2])),
        ("strength_held_1s_pct", strength_pct(t.strength_held_e2[0])),
        ("strength_held_5s_pct", strength_pct(t.strength_held_e2[1])),
        ("strength_held_15s_pct", strength_pct(t.strength_held_e2[2])),
        ("strength_held_60s_pct", strength_pct(t.strength_held_e2[3])),
        ("repeat_count", t.repeat_count.to_string()),
        ("sigma_60s_bps", some_or_empty(sigma[0])),
        ("sigma_600s_bps", some_or_empty(sigma[1])),
        ("sigma_3600s_bps", some_or_empty(sigma[2])),
        ("sigma_7200s_bps", some_or_empty(sigma[3])),
        (
            "adverse_60s_bps",
            some_or_empty(excursion[0].map(|e| e.adverse_bps)),
        ),
        (
            "adverse_600s_bps",
            some_or_empty(excursion[1].map(|e| e.adverse_bps)),
        ),
        (
            "adverse_3600s_bps",
            some_or_empty(excursion[2].map(|e| e.adverse_bps)),
        ),
        (
            "adverse_7200s_bps",
            some_or_empty(excursion[3].map(|e| e.adverse_bps)),
        ),
        (
            "favour_60s_bps",
            some_or_empty(excursion[0].map(|e| e.favour_bps)),
        ),
        (
            "favour_600s_bps",
            some_or_empty(excursion[1].map(|e| e.favour_bps)),
        ),
        (
            "favour_3600s_bps",
            some_or_empty(excursion[2].map(|e| e.favour_bps)),
        ),
        (
            "favour_7200s_bps",
            some_or_empty(excursion[3].map(|e| e.favour_bps)),
        ),
        (
            "stack_next_tick",
            t.stack_next_tick.map_or(String::new(), |v| v.to_string()),
        ),
        ("traded_1s", t.traded_first_s[0].to_string()),
        ("traded_2s", t.traded_first_s[1].to_string()),
        ("traded_3s", t.traded_first_s[2].to_string()),
        ("flow_1h_lots", t.flow_1h_lots.to_string()),
        (
            "strength_flow_pct",
            if t.flow_1h_lots > 0 {
                format!(
                    "{:.4}",
                    t.size_at_touch as f64 / t.flow_1h_lots as f64 * 100.0
                )
            } else {
                String::new()
            },
        ),
        ("ret_10m_bps", some_or_empty(pre[0])),
        ("ret_1h_bps", some_or_empty(pre[1])),
        ("ret_4h_bps", some_or_empty(pre[2])),
    ]
}

/// Строка `touches-<SYMBOL>.csv`, значения без имён, в порядке
/// `TOUCHES_COLUMNS` — для `csv::Writer::write_record`.
pub(super) fn touch_row(
    day: &ReplayDay,
    t: &TouchRecord,
    sigma_series: &SigmaSeries,
    second_mids: &SecondMids,
) -> [String; TOUCHES_WIDTH] {
    let pairs = touch_row_pairs(day, t, sigma_series, second_mids);
    debug_assert_eq!(
        pairs.each_ref().map(|(name, _)| *name),
        TOUCHES_COLUMNS,
        "имя колонки и её значение обязаны стоять на одной позиции"
    );
    pairs.map(|(_, v)| v)
}

/// Имена колонок из пар — только для теста, что порядок совпадает с
/// `TOUCHES_COLUMNS` независимо от `debug_assert` (который не выполняется
/// в `--release`, см. `run_touches`, W5б).
#[cfg(test)]
pub(super) fn touch_row_pair_names(
    day: &ReplayDay,
    t: &TouchRecord,
    sigma_series: &SigmaSeries,
    second_mids: &SecondMids,
) -> [&'static str; TOUCHES_WIDTH] {
    touch_row_pairs(day, t, sigma_series, second_mids).map(|(name, _)| name)
}

/// Пары «имя колонки → значение» одной строки подхода, в порядке
/// `APPROACHES_COLUMNS`.
fn approach_row_pairs(day: &str, a: &ApproachRecord) -> [(&'static str, String); APPROACHES_WIDTH] {
    [
        ("day_utc", day.to_string()),
        ("side", side_name(a.side).to_string()),
        ("price_tick", a.price_tick.to_string()),
        ("approach_index", a.approach_index.to_string()),
        ("arm_ms", a.arm_ms.to_string()),
        ("age_ms", a.age_ms().to_string()),
        ("arm_dist_bps", a.arm_dist_bps.to_string()),
        ("birth_ms", a.level_birth_ms.to_string()),
        ("size_at_arm", a.size_at_arm.to_string()),
        ("best_own_tick", a.best_own_tick.to_string()),
        ("best_opp_tick", a.best_opp_tick.to_string()),
        ("flow_1h_lots", a.flow_1h_lots.to_string()),
        ("strength_w10_pct", strength_pct(a.strength_e2[0])),
        ("strength_w20_pct", strength_pct(a.strength_e2[1])),
        ("strength_w50_pct", strength_pct(a.strength_e2[2])),
        (
            "touch_start_ms",
            a.touch_start_ms.map_or(String::new(), |v| v.to_string()),
        ),
        ("disarm_ms", a.disarm_ms.to_string()),
        ("duration_ms", a.duration_ms().to_string()),
        ("disarm_reason", a.disarm_reason.name().to_string()),
    ]
}

/// Строка `approaches-<SYMBOL>.csv`, значения без имён, в порядке
/// `APPROACHES_COLUMNS`.
pub(super) fn approach_row(day: &str, a: &ApproachRecord) -> [String; APPROACHES_WIDTH] {
    let pairs = approach_row_pairs(day, a);
    debug_assert_eq!(
        pairs.each_ref().map(|(name, _)| *name),
        APPROACHES_COLUMNS,
        "имя колонки и её значение обязаны стоять на одной позиции"
    );
    pairs.map(|(_, v)| v)
}

/// Имена колонок из пар — только для теста (см. `touch_row_pair_names`).
#[cfg(test)]
pub(super) fn approach_row_pair_names(
    day: &str,
    a: &ApproachRecord,
) -> [&'static str; APPROACHES_WIDTH] {
    approach_row_pairs(day, a).map(|(name, _)| name)
}
