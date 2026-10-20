//! Кэш касаний/подходов (`--touches-from`, F1/F6, В-58/В-73) для суток
//! символа — `cached_touches`/`cached_approaches`; вынесено из `bounce_grid`
//! при разрезке B3 (ревью 23.09), поведение не менялось.

use std::path::Path;

use crate::book::Side;
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
#[derive(Clone)]
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

/// Контроль C (TK-333): `touches-project --shift-secs N` дописывает в файл колонку `ctx_ms` — исходный момент строки
/// (`start_ms`/`arm_ms` = `ctx_ms + N`). σ/режим/btc-lag `bounce-grid` берёт из `start_ms − --ctx-shift-secs`, поэтому
/// флаг обязан совпасть с файлом: файл с `ctx_ms` без флага (или с другим) молча дал бы контекст из сдвинутого `t′`,
/// флаг без такого файла — сдвиг контекста относительно несдвинутого входа. Отказ жёсткий (вне отката кэша на реплей):
/// без `--touches-from` флаг не нулевой — отказ; файл без `ctx_ms` и флаг 0 — прежний путь без чтения строк.
pub(super) fn verify_ctx_shift<'a>(
    touches_from: Option<&Path>,
    approach: bool,
    symbol: &str,
    days: impl Iterator<Item = &'a String>,
    ctx_shift_ms: i64,
) -> anyhow::Result<()> {
    let Some(dir) = touches_from else {
        anyhow::ensure!(
            ctx_shift_ms == 0,
            "--ctx-shift-secs {} без --touches-from: сдвинутый вход берётся только из файла touches-project с колонкой ctx_ms",
            ctx_shift_ms / 1_000
        );
        return Ok(());
    };
    let stem = if approach { "approaches" } else { "touches" };
    let start_col = if approach { "arm_ms" } else { "start_ms" };
    let flat = dir.join(format!("{stem}-{symbol}.csv"));
    let mut flat_done = false;
    for day in days {
        let per_day = dir.join(day).join(format!("{stem}-{symbol}.csv"));
        let file = if per_day.is_file() {
            per_day
        } else if flat.is_file() && !flat_done {
            flat_done = true;
            flat.clone()
        } else {
            continue; // файла нет — скажет загрузчик кэша
        };
        check_ctx_file(&file, start_col, ctx_shift_ms)?;
    }
    Ok(())
}

fn check_ctx_file(file: &Path, start_col: &str, ctx_shift_ms: i64) -> anyhow::Result<()> {
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_path(file)
        .map_err(|e| anyhow::anyhow!("{}: {e}", file.display()))?;
    let header = r.headers()?.clone();
    let Some(c_ctx) = header.iter().position(|h| h == "ctx_ms") else {
        anyhow::ensure!(
            ctx_shift_ms == 0,
            "{}: --ctx-shift-secs {} при файле без колонки ctx_ms (не из `touches-project --shift-secs`)",
            file.display(),
            ctx_shift_ms / 1_000
        );
        return Ok(());
    };
    anyhow::ensure!(
        ctx_shift_ms != 0,
        "{}: колонка ctx_ms (сдвинутый вход) без --ctx-shift-secs — σ/btc4h взялись бы из сдвинутого момента",
        file.display()
    );
    let c_start = header
        .iter()
        .position(|h| h == start_col)
        .ok_or_else(|| anyhow::anyhow!("{}: нет колонки {start_col}", file.display()))?;
    for (i, rec) in r.records().enumerate() {
        let rec = rec?;
        let num = |c: usize, name: &str| -> anyhow::Result<i64> {
            rec.get(c).and_then(|v| v.parse().ok()).ok_or_else(|| {
                anyhow::anyhow!("{}: строка {}: {name} не число", file.display(), i + 2)
            })
        };
        let (start, ctx) = (num(c_start, start_col)?, num(c_ctx, "ctx_ms")?);
        anyhow::ensure!(
            start.checked_sub(ctx) == Some(ctx_shift_ms),
            "{}: строка {}: {start_col} − ctx_ms = {} мс, а --ctx-shift-secs = {} с",
            file.display(),
            i + 2,
            start.saturating_sub(ctx),
            ctx_shift_ms / 1_000
        );
    }
    Ok(())
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
            let rows = crate::commands::lob::touches::read_touches_cached(&per_day)?;
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
                flat_rows = Some(crate::commands::lob::touches::read_touches_cached(&flat)?);
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
            let rows = crate::commands::lob::touches::read_approaches_cached(&per_day)?;
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
                flat_rows = Some(crate::commands::lob::touches::read_approaches_cached(
                    &flat,
                )?);
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
        let mut views: Vec<TouchRecord> = approaches
            .iter()
            .map(crate::commands::lob::backtest::touch_view_of_approach)
            .collect();
        join_swept_lots(dir, symbol, day, &approaches, &mut views);
        out.push(DayTouches {
            day: day.clone(),
            touches: views,
            approaches: Some(approaches),
            // Кэш подходов хода до взвода не несёт (как у F2): контекстные
            // ключи наборов с `--signal approach` отвергает `run_bounce_grid`,
            // но длины рядов обязаны сходиться (`touch_contexts`).
            rets: vec![[None; 3]; n],
        });
    }
    Ok(out)
}

/// `swept_lots` касания — в вид подхода, по ключу (сторона, цена, `touch_start_ms` = `start_ms` касания) (Г-135, П-17).
/// Читает только `swept_min=` и только вместе с `postsweep<R>` (вход после касания; на лестнице B1 — отказ
/// `signals_for`): на взводе величина неизвестна. Подход без касания или без файла касаний — 0.
fn join_swept_lots(
    dir: &Path,
    symbol: &str,
    day: &String,
    approaches: &[crate::lob::levels::ApproachRecord],
    views: &mut [TouchRecord],
) {
    if !approaches.iter().any(|a| a.touch_start_ms.is_some()) {
        return;
    }
    let Ok(days) = cached_touches(dir, symbol, std::iter::once(day), false) else {
        return;
    };
    let swept: std::collections::HashMap<(bool, i64, i64), i64> = days
        .iter()
        .flat_map(|d| d.touches.iter())
        .map(|t| {
            (
                (t.side == Side::Bid, t.price_tick, t.start_ms),
                t.swept_lots,
            )
        })
        .collect();
    for (a, v) in approaches.iter().zip(views.iter_mut()) {
        if let Some(ts) = a.touch_start_ms {
            v.swept_lots = swept
                .get(&(a.side == Side::Bid, a.price_tick, ts))
                .copied()
                .unwrap_or(0);
        }
    }
}

/// Журнал стен суток (TK-115, e65) из кэша `--touches-from`: `<dir>/<сутки>/walls-<SYMBOL>.csv`, иначе общий
/// `<dir>/walls-<SYMBOL>.csv` (строки этих суток). Файла нет или в нём нет колонок — `Err`: форма `wall2*` без
/// журнала считалась бы молча как B1. Порядок — по времени (устойчиво).
pub(crate) fn cached_walls(
    dir: &Path,
    symbol: &str,
    day: &str,
) -> anyhow::Result<Vec<crate::lob::levels::WallEvent>> {
    use crate::book::Side;
    let per_day = dir.join(day).join(format!("walls-{symbol}.csv"));
    let flat = dir.join(format!("walls-{symbol}.csv"));
    let path = if per_day.is_file() {
        per_day
    } else if flat.is_file() {
        flat
    } else {
        anyhow::bail!(
            "{symbol} {day}: журнала стен walls-{symbol}.csv нет в {} (форма wall2* требует `lob touches --wall-log`)",
            dir.display()
        );
    };
    let mut rd = csv::Reader::from_path(&path)?;
    let mut out = Vec::new();
    for rec in rd.records() {
        let rec = rec?;
        let f = |i: usize| {
            rec.get(i)
                .ok_or_else(|| anyhow::anyhow!("{}: мало колонок", path.display()))
        };
        if f(0)? != day {
            continue;
        }
        out.push(crate::lob::levels::WallEvent {
            ts_ms: f(1)?.parse()?,
            side: match f(2)? {
                "bid" => Side::Bid,
                "ask" => Side::Ask,
                s => anyhow::bail!("{}: сторона {s:?}", path.display()),
            },
            price_tick: f(3)?.parse()?,
            wall: f(4)? == "1",
        });
    }
    out.sort_by_key(|e| e.ts_ms);
    Ok(out)
}
