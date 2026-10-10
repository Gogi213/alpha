//! Писатель CSV уровней суток реплея — для `lob touches --levels-out` (T-14).
//! (Команда `lob levels` срезана в TK-168, КТ-11; тег `archive/research-layer-0925`.)

use std::path::Path;

use super::{death_name, outcome_name, side_name};

/// Длина суток от первого до последнего среза середины — прокси на длину
/// прогона, которым мерялось окно `repeat_count`. Без срезов (файл вообще
/// без обновлений книги) длина ноль — сутки короче любого положительного окна.
fn day_span_ms(day: &super::ReplayDay) -> i64 {
    match (day.mids.first(), day.mids.last()) {
        (Some(a), Some(b)) => b.ts_ms - a.ts_ms,
        _ => 0,
    }
}

/// Запись уровней суток реплея в CSV — общий писатель `lob levels` и `lob touches
/// --levels-out` (T-14, план «один проход» T-05): оба пути пишут одни байты, включая
/// строку `# debug` прогона короче окна `repeat_count` (`repeat_window_ms` — окно того
/// реплея, чьи записи пишутся). Возвращает число строк уровней.
pub(crate) fn write_levels_csv(
    out: &Path,
    days: &[super::ReplayDay],
    repeat_window_ms: i64,
) -> anyhow::Result<usize> {
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let short_span_ms = days.iter().map(day_span_ms).min();
    let debug_warning = short_span_ms
        .filter(|&span| span < repeat_window_ms)
        .map(|span| {
            format!(
                "lob levels: debug — прогон короче окна repeat_count ({span} мс < {} мс), \
             repeat_count занижен, данными не является",
                repeat_window_ms
            )
        });
    if let Some(msg) = &debug_warning {
        eprintln!("{msg}");
    }

    let mut file = std::fs::File::create(out)?;
    if let Some(msg) = &debug_warning {
        use std::io::Write as _;
        writeln!(file, "# {msg}")?;
    }
    let mut w = csv::Writer::from_writer(file);
    w.write_record([
        "day_utc",
        "side",
        "price_tick",
        "birth_ms",
        "death_ms",
        "lifetime_ms",
        "size_max",
        "time_to_max_ms",
        "size_monotonic",
        "repeat_count",
        "repriced",
        "death",
        "traded_lots",
        "rpi_lots",
        "outcome",
    ])?;
    let mut n = 0usize;
    for day in days {
        for r in &day.records {
            w.write_record([
                day.day.clone(),
                side_name(r.side).to_string(),
                r.price_tick.to_string(),
                r.birth_ms.to_string(),
                r.death_ms.to_string(),
                r.lifetime_ms.to_string(),
                r.size_max.to_string(),
                r.time_to_max_ms.to_string(),
                r.size_monotonic.to_string(),
                r.repeat_count.to_string(),
                r.repriced.to_string(),
                death_name(r.death).to_string(),
                r.traded_lots.to_string(),
                r.rpi_lots.to_string(),
                outcome_name(r.outcome()).to_string(),
            ])?;
            n += 1;
        }
    }
    w.flush()?;
    Ok(n)
}
