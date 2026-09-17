//! `gaps.csv`: причина строки, сама строка, шапка и три операции над файлом
//! (создать с шапкой, дописать, прочитать). Отдельно от рекордера: тот же
//! файл пишет `lob session`, а `Recorder` — лишь один из его писателей.

use std::fs::OpenOptions;
use std::path::Path;

use super::errors::RecordError;

// ---------------------------------------------------------------------------
// gaps.csv: шапка всегда, строки только на разрывах.
// ---------------------------------------------------------------------------

/// Причина строки в `gaps.csv`. Сериализуется snake_case и читается назад тем
/// же именем — переименование варианта без `serde(rename)` молча разойдётся
/// со старыми файлами, поэтому имена зафиксированы тестом `gap_row_round_trips`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GapKind {
    /// Смена `tickSize`/`qtyStep`: файл закрыт, открыт следующий, заголовок
    /// нового несёт новые шаги.
    StepChange,
    /// Разрыв `u`: событие пропущено, книга ждёт снапшота.
    SequenceGap,
    /// Пересечение книги или немасштабное нарушение инварианта.
    BookInvariant,
    /// Кадр транспорта не разобрался: событие потеряно до книги.
    ParseError,
    /// Кадр не записался на диск (таск 25): потерян целый батч — до
    /// `FRAME_TARGET_RECORDS` записей, молчать нельзя.
    WriteFailed,
    /// `connect()` не удался: сокет не открылся, подписки не было (K1,
    /// 2026-09-17). Отдельно от `SequenceGap`, которым отмечается разрыв уже
    /// работавшего сокета: тут шва в данных нет вовсе — данные не начинались,
    /// и по `reconnects` такой отказ раньше не был виден никак.
    ConnectFailed,
}

/// Одна строка `gaps.csv`. Колонки: момент (UTC, RFC 3339), символ, причина,
/// человекочитаемая деталь (какое поле, какие значения, какие пороги).
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct GapRow {
    pub ts_utc: String,
    pub symbol: String,
    pub kind: GapKind,
    pub detail: String,
}

/// Шапка `gaps.csv` — те же имена и в том же порядке, что поля `GapRow`.
/// Ручная запись, а не `serialize` пустой строки: `csv` пишет шапку только на
/// первом `serialize`, а файл с нулём строк обязан шапку уже нести
/// (done-condition 0.3: «`gaps.csv` с нулём строк» — это шапка без данных,
/// а не отсутствующий файл). Дрейф имён ловит `gap_row_round_trips`.
const GAPS_HEADER: [&str; 4] = ["ts_utc", "symbol", "kind", "detail"];

/// Создаёт `gaps.csv` с шапкой, если его нет или он пуст. Существующий
/// непустой файл не трогает — часовой замер не имеет права терять уже
/// записанные разрывы (тот же приём, что `clock::append_row`).
pub fn ensure_gaps_csv(path: &Path) -> Result<(), RecordError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let needs_header = std::fs::metadata(path)
        .map(|m| m.len() == 0)
        .unwrap_or(true);
    if needs_header {
        // `truncate(false)` явно: файл здесь либо отсутствует, либо нулевой
        // длины (проверено выше) — усекать нечего, и это зафиксировано
        // вызовом, а не умолчанием `OpenOptions`.
        let file = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(false)
            .open(path)?;
        let mut w = csv::WriterBuilder::new()
            .has_headers(false)
            .from_writer(file);
        w.write_record(GAPS_HEADER)?;
        w.flush()?;
    }
    Ok(())
}

/// Дописывает строку. Шапка пишется тем же вызовом, если файла не было, —
/// вызывающему не нужно помнить про `ensure_gaps_csv` отдельно.
pub fn append_gap_row(path: &Path, row: &GapRow) -> Result<(), RecordError> {
    ensure_gaps_csv(path)?;
    let file = OpenOptions::new().create(true).append(true).open(path)?;
    let mut w = csv::WriterBuilder::new()
        .has_headers(false)
        .from_writer(file);
    w.serialize(row)?;
    w.flush()?;
    Ok(())
}

/// Читает все строки. Пустого файла (ноль байт, без шапки) здесь быть не
/// должно после `ensure_gaps_csv`, но если он подсунут напрямую — это ноль
/// строк, а не ошибка разбора: отсутствие данных не есть повреждённые данные.
pub fn read_gap_rows(path: &Path) -> Result<Vec<GapRow>, RecordError> {
    if std::fs::metadata(path)
        .map(|m| m.len() == 0)
        .unwrap_or(true)
    {
        return Ok(Vec::new());
    }
    let mut r = csv::Reader::from_path(path)?;
    r.deserialize::<GapRow>()
        .collect::<Result<Vec<_>, _>>()
        .map_err(RecordError::from)
}
