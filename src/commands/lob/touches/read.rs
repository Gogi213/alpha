//! Чтение `touches-<SYMBOL>.csv` и `approaches-<SYMBOL>.csv` обратно в
//! записи трекера (W5в) — обратное к `super::row`. Вынесено из
//! `commands/lob/touches.rs` вместе с общим помощником полей CSV, который
//! раньше был продублирован слово в слово в `TouchCols::parse` и
//! `ApproachCols::parse` (W5в).

use crate::book::Side;
use crate::lob::levels::{
    ApproachEnd, ApproachRecord, TouchRecord, REACTION_WINDOWS_S, STRENGTH_HELD_WINDOWS_S,
    STRENGTH_WINDOWS_BPS,
};

use super::PRE_TOUCH_MS;

/// Поле строки как есть — общий помощник (было продублировано в
/// `TouchCols::parse` и `ApproachCols::parse`).
fn csv_field(rec: &csv::StringRecord, i: usize) -> anyhow::Result<&str> {
    rec.get(i)
        .ok_or_else(|| anyhow::anyhow!("короткая строка: нет поля {i}"))
}

/// Поле строки как целое.
fn csv_int(rec: &csv::StringRecord, i: usize) -> anyhow::Result<i64> {
    let s = csv_field(rec, i)?;
    s.parse::<i64>()
        .map_err(|e| anyhow::anyhow!("{s:?} в поле {i}: {e}"))
}

/// Поле строки как целое или пусто.
fn csv_opt_int(rec: &csv::StringRecord, i: usize) -> anyhow::Result<Option<i64>> {
    Ok(if csv_field(rec, i)?.is_empty() {
        None
    } else {
        Some(csv_int(rec, i)?)
    })
}

/// Индекс колонки по имени — общий помощник (было продублировано локальным
/// замыканием `idx` в `read_touches_csv` и `read_approaches_csv`). Колонки
/// ищутся по именам, не по позициям: порядок — дело писателя.
fn column_index(
    header: &csv::StringRecord,
    path: &std::path::Path,
    name: &str,
) -> anyhow::Result<usize> {
    header
        .iter()
        .position(|h| h == name)
        .ok_or_else(|| anyhow::anyhow!("{}: нет колонки {name}", path.display()))
}

/// Касание, прочитанное из `touches-<SYMBOL>.csv`: сутки строки и запись
/// трекера как есть.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct TouchRow {
    pub day: String,
    pub touch: TouchRecord,
    /// Ход до касания `ret_10m/1h/4h_bps` (S2) — `None`, если в файле этих
    /// колонок нет (кэш до 20.09); пустая клетка — `Some([.., None, ..])`.
    pub ret_bps: Option<[Option<f64>; PRE_TOUCH_MS.len()]>,
}

/// Обратное к строке `run_touches`: `TouchRecord` из CSV касаний — те же
/// поля, что пишет трекер, побайтово (целые как есть, `strength_*_pct` —
/// «проценты × 100» обратно в `e2`, пусто → `-1`; `frontrun_tick`/
/// `stack_next_tick` пусто → `None`). Производные колонки (`m_*`, `sigma_*`,
/// `adverse_*`…) не читаются: `σ` в CSV — суточный ряд с шестью знаками, а не
/// ряд записи, поэтому σ-формы из кэша не считаются (`bounce_grid`). Смысл:
/// `lob bounce-grid --touches-from` читает касания ночного H3 вместо реплея
/// книги (83 % времени сетки, замер 19.09) — трекер в обоих суточный, так что
/// касания те же; ворота — побайтово те же `rounds.csv`/`forms.csv`.
pub(crate) fn read_touches_csv(path: &std::path::Path) -> anyhow::Result<Vec<TouchRow>> {
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_path(path)
        .map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?;
    let header = r.headers()?.clone();
    let idx = |name: &str| column_index(&header, path, name);
    let cols = TouchCols {
        day: idx("day_utc")?,
        side: idx("side")?,
        price_tick: idx("price_tick")?,
        touch_index: idx("touch_index")?,
        start_ms: idx("start_ms")?,
        end_ms: idx("end_ms")?,
        duration_ms: idx("duration_ms")?,
        birth_ms: idx("birth_ms")?,
        size_at_touch: idx("size_at_touch")?,
        size_max_before: idx("size_max_before")?,
        traded_during: idx("traded_during")?,
        frontrun_lots: idx("frontrun_lots")?,
        frontrun_tick: idx("frontrun_tick")?,
        swept_lots: idx("swept_lots")?,
        round_zeros: idx("round_zeros")?,
        ended_by_death: idx("ended_by_death")?,
        stack_levels: idx("stack_levels")?,
        strength: [
            idx("strength_w10_pct")?,
            idx("strength_w20_pct")?,
            idx("strength_w50_pct")?,
        ],
        strength_held: [
            idx("strength_held_1s_pct")?,
            idx("strength_held_5s_pct")?,
            idx("strength_held_15s_pct")?,
            idx("strength_held_60s_pct")?,
        ],
        repeat_count: idx("repeat_count")?,
        stack_next_tick: idx("stack_next_tick")?,
        traded_first: [idx("traded_1s")?, idx("traded_2s")?, idx("traded_3s")?],
        flow_1h_lots: idx("flow_1h_lots")?,
        ret: {
            let cols = [idx("ret_10m_bps"), idx("ret_1h_bps"), idx("ret_4h_bps")];
            if cols.iter().all(Result::is_ok) {
                Some(cols.map(|c| c.expect("проверено")))
            } else {
                None
            }
        },
    };
    let mut out = Vec::new();
    for (i, rec) in r.records().enumerate() {
        let rec = rec?;
        out.push(
            cols.parse(&rec)
                .map_err(|e| anyhow::anyhow!("{}: строка {}: {e}", path.display(), i + 2))?,
        );
    }
    Ok(out)
}

/// Индексы колонок `touches-*.csv`, нужных `TouchRecord` (по именам, не по
/// позициям: порядок колонок — дело писателя).
struct TouchCols {
    day: usize,
    side: usize,
    price_tick: usize,
    touch_index: usize,
    start_ms: usize,
    end_ms: usize,
    duration_ms: usize,
    birth_ms: usize,
    size_at_touch: usize,
    size_max_before: usize,
    traded_during: usize,
    frontrun_lots: usize,
    frontrun_tick: usize,
    swept_lots: usize,
    round_zeros: usize,
    ended_by_death: usize,
    stack_levels: usize,
    strength: [usize; STRENGTH_WINDOWS_BPS.len()],
    strength_held: [usize; STRENGTH_HELD_WINDOWS_S.len()],
    repeat_count: usize,
    stack_next_tick: usize,
    traded_first: [usize; REACTION_WINDOWS_S.len()],
    flow_1h_lots: usize,
    ret: Option<[usize; PRE_TOUCH_MS.len()]>,
}

impl TouchCols {
    fn parse(&self, rec: &csv::StringRecord) -> anyhow::Result<TouchRow> {
        let touch = TouchRecord {
            side: match csv_field(rec, self.side)? {
                "bid" => Side::Bid,
                "ask" => Side::Ask,
                other => anyhow::bail!("сторона {other:?}"),
            },
            price_tick: csv_int(rec, self.price_tick)?,
            touch_index: u32::try_from(csv_int(rec, self.touch_index)?)?,
            start_ms: csv_int(rec, self.start_ms)?,
            end_ms: csv_int(rec, self.end_ms)?,
            duration_ms: csv_int(rec, self.duration_ms)?,
            level_birth_ms: csv_int(rec, self.birth_ms)?,
            size_at_touch: csv_int(rec, self.size_at_touch)?,
            size_max_before: csv_int(rec, self.size_max_before)?,
            traded_during: csv_int(rec, self.traded_during)?,
            frontrun_lots: csv_int(rec, self.frontrun_lots)?,
            frontrun_tick: csv_opt_int(rec, self.frontrun_tick)?,
            swept_lots: csv_int(rec, self.swept_lots)?,
            round_zeros: u8::try_from(csv_int(rec, self.round_zeros)?)?,
            ended_by_death: match csv_field(rec, self.ended_by_death)? {
                "true" => true,
                "false" => false,
                other => anyhow::bail!("ended_by_death {other:?}"),
            },
            stack_levels: u32::try_from(csv_int(rec, self.stack_levels)?)?,
            stack_next_tick: csv_opt_int(rec, self.stack_next_tick)?,
            traded_first_s: [
                csv_int(rec, self.traded_first[0])?,
                csv_int(rec, self.traded_first[1])?,
                csv_int(rec, self.traded_first[2])?,
            ],
            flow_1h_lots: csv_int(rec, self.flow_1h_lots)?,
            strength_e2: [
                strength_e2(csv_field(rec, self.strength[0])?)?,
                strength_e2(csv_field(rec, self.strength[1])?)?,
                strength_e2(csv_field(rec, self.strength[2])?)?,
            ],
            strength_held_e2: [
                strength_e2(csv_field(rec, self.strength_held[0])?)?,
                strength_e2(csv_field(rec, self.strength_held[1])?)?,
                strength_e2(csv_field(rec, self.strength_held[2])?)?,
                strength_e2(csv_field(rec, self.strength_held[3])?)?,
            ],
            repeat_count: u32::try_from(csv_int(rec, self.repeat_count)?)?,
        };
        let ret_bps = match self.ret {
            None => None,
            Some(cols) => {
                let mut out = [None; PRE_TOUCH_MS.len()];
                for (k, c) in cols.iter().enumerate() {
                    let v = csv_field(rec, *c)?;
                    out[k] = if v.is_empty() {
                        None
                    } else {
                        Some(
                            v.parse::<f64>()
                                .map_err(|e| anyhow::anyhow!("{v:?} в поле {c}: {e}"))?,
                        )
                    };
                }
                Some(out)
            }
        };
        Ok(TouchRow {
            day: csv_field(rec, self.day)?.to_string(),
            touch,
            ret_bps,
        })
    }
}

/// Запись подхода, прочитанная из `approaches-<SYMBOL>.csv` (F1): сутки
/// строки и `ApproachRecord` трекера как есть. Читатели — `fill_capacity`
/// (`--targets approaches`, F2) и `bounce_grid` (`--signal approach`, F6).
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ApproachRow {
    pub day: String,
    pub approach: ApproachRecord,
}

/// Обратное к строке `run_touches` для подходов: `ApproachRecord` из CSV —
/// те же поля, что пишет трекер (`strength_*_pct` — «проценты × 100» обратно
/// в `e2`, пусто → `-1`; `touch_start_ms` пусто → `None`). Производные
/// `age_ms`/`duration_ms` не читаются: считаются из полей записи, как у
/// `TouchRecord`. Колонки ищутся по именам, не по позициям: порядок — дело
/// писателя. Обратимость — тестом.
pub(crate) fn read_approaches_csv(path: &std::path::Path) -> anyhow::Result<Vec<ApproachRow>> {
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_path(path)
        .map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?;
    let header = r.headers()?.clone();
    let idx = |name: &str| column_index(&header, path, name);
    let cols = ApproachCols {
        day: idx("day_utc")?,
        side: idx("side")?,
        price_tick: idx("price_tick")?,
        approach_index: idx("approach_index")?,
        arm_ms: idx("arm_ms")?,
        arm_dist_bps: idx("arm_dist_bps")?,
        birth_ms: idx("birth_ms")?,
        size_at_arm: idx("size_at_arm")?,
        best_own_tick: idx("best_own_tick")?,
        best_opp_tick: idx("best_opp_tick")?,
        flow_1h_lots: idx("flow_1h_lots")?,
        strength: [
            idx("strength_w10_pct")?,
            idx("strength_w20_pct")?,
            idx("strength_w50_pct")?,
        ],
        touch_start_ms: idx("touch_start_ms")?,
        disarm_ms: idx("disarm_ms")?,
        disarm_reason: idx("disarm_reason")?,
    };
    let mut out = Vec::new();
    for (i, rec) in r.records().enumerate() {
        let rec = rec?;
        out.push(
            cols.parse(&rec)
                .map_err(|e| anyhow::anyhow!("{}: строка {}: {e}", path.display(), i + 2))?,
        );
    }
    Ok(out)
}

/// Индексы колонок `approaches-*.csv`, нужных `ApproachRecord`.
struct ApproachCols {
    day: usize,
    side: usize,
    price_tick: usize,
    approach_index: usize,
    arm_ms: usize,
    arm_dist_bps: usize,
    birth_ms: usize,
    size_at_arm: usize,
    best_own_tick: usize,
    best_opp_tick: usize,
    flow_1h_lots: usize,
    strength: [usize; STRENGTH_WINDOWS_BPS.len()],
    touch_start_ms: usize,
    disarm_ms: usize,
    disarm_reason: usize,
}

impl ApproachCols {
    fn parse(&self, rec: &csv::StringRecord) -> anyhow::Result<ApproachRow> {
        let approach = ApproachRecord {
            side: match csv_field(rec, self.side)? {
                "bid" => Side::Bid,
                "ask" => Side::Ask,
                other => anyhow::bail!("сторона {other:?}"),
            },
            price_tick: csv_int(rec, self.price_tick)?,
            approach_index: u32::try_from(csv_int(rec, self.approach_index)?)?,
            arm_ms: csv_int(rec, self.arm_ms)?,
            arm_dist_bps: csv_int(rec, self.arm_dist_bps)?,
            level_birth_ms: csv_int(rec, self.birth_ms)?,
            size_at_arm: csv_int(rec, self.size_at_arm)?,
            best_own_tick: csv_int(rec, self.best_own_tick)?,
            best_opp_tick: csv_int(rec, self.best_opp_tick)?,
            flow_1h_lots: csv_int(rec, self.flow_1h_lots)?,
            strength_e2: [
                strength_e2(csv_field(rec, self.strength[0])?)?,
                strength_e2(csv_field(rec, self.strength[1])?)?,
                strength_e2(csv_field(rec, self.strength[2])?)?,
            ],
            touch_start_ms: csv_opt_int(rec, self.touch_start_ms)?,
            disarm_ms: csv_int(rec, self.disarm_ms)?,
            disarm_reason: ApproachEnd::parse(csv_field(rec, self.disarm_reason)?).ok_or_else(
                || anyhow::anyhow!("причина {:?}", csv_field(rec, self.disarm_reason)),
            )?,
        };
        Ok(ApproachRow {
            day: csv_field(rec, self.day)?.to_string(),
            approach,
        })
    }
}

/// Обратное к `strength_pct` (`super::row`): `"123.45"` → `12345`, пусто →
/// `-1`. Ровно две цифры после точки — иначе строка не наша.
fn strength_e2(s: &str) -> anyhow::Result<i64> {
    if s.is_empty() {
        return Ok(-1);
    }
    let (whole, frac) = s
        .split_once('.')
        .ok_or_else(|| anyhow::anyhow!("сила {s:?}: нет точки"))?;
    anyhow::ensure!(frac.len() == 2, "сила {s:?}: не два знака");
    let whole: i64 = whole.parse()?;
    let frac: i64 = frac.parse()?;
    Ok(whole * 100 + frac)
}
