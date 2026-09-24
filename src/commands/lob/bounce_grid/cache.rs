//! Кэш касаний/подходов (`--touches-from`, F1/F6, В-58/В-73) для суток
//! символа — `cached_touches`/`cached_approaches`; вынесено из `bounce_grid`
//! при разрезке B3 (ревью 23.09), поведение не менялось.

use std::path::Path;

use crate::lob::levels::TouchRecord;

/// Доля съедания стены к касанию, %: `100 × (1 − size_at_touch /
/// size_max_before)`; стена без истории размера (`size_max_before ≤ 0`) —
/// ноль, чтобы ключ `eaten=` её не выбивал (нечего было съесть).
#[allow(clippy::cast_precision_loss)]
pub(super) fn eaten_pct(t: &TouchRecord) -> f64 {
    if t.size_max_before <= 0 {
        return 0.0;
    }
    100.0 * (1.0 - t.size_at_touch as f64 / t.size_max_before as f64)
}

/// Касания одних суток символа — из реплея или из кэша, форме всё равно.
pub(crate) struct DayTouches {
    pub(crate) day: String,
    pub(crate) touches: Vec<TouchRecord>,
    /// Записи подхода (F6, `--signal approach`) — те же сутки и тот же порядок,
    /// что `touches` (это их вид как касания, собранный
    /// `backtest::touch_view_of_approach`); `None` — сигнал по касаниям.
    pub(crate) approaches: Option<Vec<crate::lob::levels::ApproachRecord>>,
    /// Ход до касания за `PRE_TOUCH_MS` на каждое касание (S2), для контекста наборов.
    pub(crate) rets: Vec<[Option<f64>; 3]>,
}

/// Касания символа из кэша `--touches-from` для суток `days` (сутки корня с
/// частями): на сутки — `<dir>/<сутки>/touches-<SYMBOL>.csv`, иначе общий
/// `<dir>/touches-<SYMBOL>.csv`, из которого берутся строки этих суток.
/// Нет файла на какие-то сутки или в суточном файле чужие сутки — `Err`
/// (вызывающий идёт реплеем и пишет причину). Порядок строк — порядок файла,
/// он же порядок выдачи трекера.
pub(crate) fn cached_touches<'a>(
    dir: &Path,
    symbol: &str,
    days: impl Iterator<Item = &'a String>,
    need_ret: bool,
) -> anyhow::Result<Vec<DayTouches>> {
    let flat = dir.join(format!("touches-{symbol}.csv"));
    let mut flat_rows: Option<Vec<crate::commands::lob::touches::TouchRow>> = None;
    let mut out = Vec::new();
    for day in days {
        let per_day = dir.join(day).join(format!("touches-{symbol}.csv"));
        let rows: Vec<crate::commands::lob::touches::TouchRow> = if per_day.is_file() {
            let rows = crate::commands::lob::touches::read_touches_csv(&per_day)?;
            if let Some(bad) = rows.iter().find(|r| r.day != *day) {
                anyhow::bail!(
                    "{}: строка суток {} в файле суток {day}",
                    per_day.display(),
                    bad.day
                );
            }
            rows
        } else if flat.is_file() {
            if flat_rows.is_none() {
                flat_rows = Some(crate::commands::lob::touches::read_touches_csv(&flat)?);
            }
            flat_rows
                .as_ref()
                .expect("только что прочитан")
                .iter()
                .filter(|r| r.day == *day)
                .cloned()
                .collect()
        } else {
            anyhow::bail!("нет {} и нет {}", per_day.display(), flat.display());
        };
        if need_ret {
            if let Some(bad) = rows.iter().find(|r| r.ret_bps.is_none()) {
                anyhow::bail!(
                    "{}: кэш без колонок ret_* (касание {} {}), нужен пересчёт касаний",
                    per_day.display(),
                    bad.day,
                    bad.touch.start_ms
                );
            }
        }
        let rets = rows
            .iter()
            .map(|r| r.ret_bps.unwrap_or([None; 3]))
            .collect();
        out.push(DayTouches {
            day: day.clone(),
            touches: rows.into_iter().map(|r| r.touch).collect(),
            approaches: None,
            rets,
        });
    }
    Ok(out)
}

/// Записи подхода символа из кэша (F1, F6): `<dir>/<сутки>/approaches-<SYMBOL>.csv`,
/// иначе общий `<dir>/approaches-<SYMBOL>.csv`, из которого берутся строки этих
/// суток. Рядом с записями — их **вид как касания**
/// (`backtest::touch_view_of_approach`): фильтры (`TouchFilter`), порог В-66 и
/// окна `SignalWindows` читают касание, а `arm_ms` становится `start_ms` —
/// снимок книги берётся на взводе, как у касания на касании. Нет файла на
/// какие-то сутки или в суточном файле чужие сутки — `Err` (реплея подходов у
/// сетки нет: полосу `D` задаёт прогон `lob touches --approach-bps`).
pub(crate) fn cached_approaches<'a>(
    dir: &Path,
    symbol: &str,
    days: impl Iterator<Item = &'a String>,
) -> anyhow::Result<Vec<DayTouches>> {
    let flat = dir.join(format!("approaches-{symbol}.csv"));
    let mut flat_rows: Option<Vec<crate::commands::lob::touches::ApproachRow>> = None;
    let mut out = Vec::new();
    for day in days {
        let per_day = dir.join(day).join(format!("approaches-{symbol}.csv"));
        let rows: Vec<crate::commands::lob::touches::ApproachRow> = if per_day.is_file() {
            let rows = crate::commands::lob::touches::read_approaches_csv(&per_day)?;
            if let Some(bad) = rows.iter().find(|r| r.day != *day) {
                anyhow::bail!(
                    "{}: строка суток {} в файле суток {day}",
                    per_day.display(),
                    bad.day
                );
            }
            rows
        } else if flat.is_file() {
            if flat_rows.is_none() {
                flat_rows = Some(crate::commands::lob::touches::read_approaches_csv(&flat)?);
            }
            flat_rows
                .as_ref()
                .expect("только что прочитан")
                .iter()
                .filter(|r| r.day == *day)
                .cloned()
                .collect()
        } else {
            anyhow::bail!("нет {} и нет {}", per_day.display(), flat.display());
        };
        let approaches: Vec<crate::lob::levels::ApproachRecord> =
            rows.into_iter().map(|r| r.approach).collect();
        let n = approaches.len();
        out.push(DayTouches {
            day: day.clone(),
            touches: approaches
                .iter()
                .map(crate::commands::lob::backtest::touch_view_of_approach)
                .collect(),
            approaches: Some(approaches),
            // Кэш подходов хода до взвода не несёт (как у F2): контекстные
            // ключи наборов с `--signal approach` отвергает `run_bounce_grid`,
            // но длины рядов обязаны сходиться (`touch_contexts`).
            rets: vec![[None; 3]; n],
        });
    }
    Ok(out)
}
