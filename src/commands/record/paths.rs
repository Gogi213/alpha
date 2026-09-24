//! Раскладка файлов под `--root` и календарь суток UTC: имена суточных
//! файлов и обочин, захват свободного номера части, перевод меток времени в
//! сутки/индекс суток/`ts_utc`. Отдельно: эти же имена и индексы читают
//! `lob session`, `lob verify`, `lob profiles` и `dashboard` без рекордера.

use std::fs::File;
use std::path::{Path, PathBuf};

use crate::binlog::{parse_calendar_day, Header};

use super::errors::RecordError;
use super::{MAX_RECORDS_PER_FRAME, ZSTD_LEVEL};

// ---------------------------------------------------------------------------
// Имена файлов.
// ---------------------------------------------------------------------------

/// Путь суточного файла. Часть 1 — каноническое имя без суффикса; ротации по
/// смене шагов внутри тех же суток получают `-p2`, `-p3`, … — перезаписать
/// часть 1 новым заголовком было бы потерей уже записанных суток.
pub fn day_file_path(root: &Path, symbol: &str, day: &str, part: u32) -> PathBuf {
    if part <= 1 {
        root.join(format!("{symbol}-{day}.binlog"))
    } else {
        root.join(format!("{symbol}-{day}-p{part}.binlog"))
    }
}

/// `gaps.csv` — один на корень, на все символы и сутки: десятки строк, читает
/// человек (Decision 23: CSV только для метаданных-обочин).
pub fn gaps_csv_path(root: &Path) -> PathBuf {
    root.join("gaps.csv")
}

/// `instruments.csv` — пишет `lob pick`, читает запись (шаги для заголовка).
pub fn instruments_csv_path(root: &Path) -> PathBuf {
    root.join("instruments.csv")
}

/// Первая свободная часть суток начиная с `from`: часть 1, если файла нет
/// (обычный случай), иначе `-p2`, `-p3`, … — перезапись занятого имени была
/// бы потерей данных. `pub(crate)`: таск 22 переиспользует её из
/// `commands::lob::session` — второй сессии в те же сутки нужен тот же поиск
/// свободного номера, что уже применяет `lob record`, а не свой расчёт.
pub(crate) fn claim_part(
    root: &Path,
    symbol: &str,
    day: &str,
    from: u32,
    tick_e9: i64,
    step_e9: i64,
) -> Result<(crate::binlog::Writer<File>, u32), RecordError> {
    claim_part_with(root, symbol, day, from, tick_e9, step_e9, |file| file)
}

/// То же, что `claim_part`, но приёмник файла выбирает вызывающий (таск 25):
/// `lob session` заворачивает `File` в свой покадровый буфер, `lob record`
/// пишет в голый `File` — один цикл поиска свободного номера части на
/// обоих, а не две копии.
pub(crate) fn claim_part_with<W: std::io::Write>(
    root: &Path,
    symbol: &str,
    day: &str,
    from: u32,
    tick_e9: i64,
    step_e9: i64,
    wrap: impl FnOnce(File) -> W,
) -> Result<(crate::binlog::Writer<W>, u32), RecordError> {
    let mut part = from.max(1);
    loop {
        let path = day_file_path(root, symbol, day, part);
        if !path.exists() {
            let file = File::create(&path)?;
            let header = Header {
                tick_e9,
                step_e9,
                max_records_per_frame: MAX_RECORDS_PER_FRAME,
            };
            let writer = crate::binlog::Writer::create(wrap(file), header, ZSTD_LEVEL)?;
            return Ok((writer, part));
        }
        part = part.checked_add(1).ok_or(RecordError::TooManyParts {
            day: day.to_string(),
        })?;
    }
}

/// Наносекунд в сутках UTC. Деление целочисленное — горячий путь сравнивает
/// дни этим же делителем без форматирования строк (см. `run_record`).
pub const NS_PER_DAY: i64 = 86_400 * 1_000_000_000;

/// Календарная дата UTC (`YYYY-MM-DD`) метки в наносекундах. Ошибка вместо
/// паники на внедиапазонной метке: метка приходит из сети/часов, а паника
/// гасит недели записи.
pub fn day_string_of_ns(ts_ns: i64) -> Result<String, RecordError> {
    let secs = ts_ns.div_euclid(1_000_000_000);
    // Остаток доказуемо < 1e9 < u32::MAX по построению `rem_euclid`.
    #[allow(clippy::cast_possible_truncation)]
    let nanos = ts_ns.rem_euclid(1_000_000_000) as u32;
    let dt = chrono::DateTime::<chrono::Utc>::from_timestamp(secs, nanos)
        .ok_or(RecordError::BadTimestamp { ts_ns })?;
    Ok(dt.format("%Y-%m-%d").to_string())
}

/// Момент UTC для `ts_utc` в `gaps.csv` (RFC 3339). Тотальная: на
/// внедиапазонной метке пишет сырые наносекунды с префиксом, а не падает, —
/// строка-деталь не имеет права ронять запись разрыва, которую оформляет.
pub fn ts_utc_of_ns(ts_ns: i64) -> String {
    let secs = ts_ns.div_euclid(1_000_000_000);
    // Тот же доказанный остаток, что в `day_string_of_ns` выше.
    #[allow(clippy::cast_possible_truncation)]
    let nanos = ts_ns.rem_euclid(1_000_000_000) as u32;
    chrono::DateTime::<chrono::Utc>::from_timestamp(secs, nanos)
        .map(|dt| dt.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true))
        .unwrap_or_else(|| format!("nanos:{ts_ns}"))
}

/// Индекс суток UTC (целые дни от эпохи) строки `YYYY-MM-DD`. Нужен горячему
/// пути: сравнение индексов — целочисленное деление без форматирования строк
/// на событие; строка форматируется только на ротации.
pub fn day_index_of_day_str(day: &str) -> Result<i64, RecordError> {
    let date = parse_calendar_day(day).ok_or_else(|| RecordError::BadDay {
        day: day.to_string(),
    })?;
    // Дни от эпохи через публичную арифметику дат, а не через приватный
    // счётчик эры: 1970-01-01 даёт ровно 0, что проверяет тест. `expect`
    // здесь невозможен по режиму линтов, поэтому невероятная ветвь
    // (календарь без 1970-01-01) возвращается той же ошибкой, что и кривой
    // вход, — с фиксированной строкой вместо пользовательского ввода.
    let epoch = chrono::NaiveDate::from_ymd_opt(1970, 1, 1).ok_or_else(|| RecordError::BadDay {
        day: "1970-01-01".to_string(),
    })?;
    Ok(date.signed_duration_since(epoch).num_days())
}
