//! Файлы записи в каталоге: имена `<SYMBOL>-<день>[-pN].binlog` и их архивы
//! `<SYMBOL>-<день>[-pN].binlog.zst` (T46), их порядок, резолвер бинлогов
//! сессии (`session_binlog_for`) и части с сутками/часом старта
//! (`session_parts_for`). Отдельно от `mod.rs`: это раскладка каталога
//! на диске, её делят `verify`, `backtest`, `profiles`, `watch`, `shortlist`.
//!
//! Суточный файл и его архив — **одни и те же сутки** для всех читателей:
//! `binlog::Reader` открывает контейнер тем же кодом, поэтому резолверы
//! возвращают оба имени в одном списке, а порядок внутри суток считается по
//! номеру части, а не по расширению.

use std::path::{Path, PathBuf};

use crate::binlog::{
    is_binlog_file_name, strip_binlog_suffix, BINLOG_ARCHIVE_SUFFIX, BINLOG_SUFFIX,
};

use super::session;

/// Кадр живого файла записи (A4, 2026-09-17).
///
/// Запись кладёт кадр не одним `write`: читатель, заставший момент между
/// префиксом длины и телом, получал `ShortRead` — и вся команда падала на
/// живом корне, хотя виноват не файл, а то, что коллектор пишет в него прямо
/// сейчас (`COMMANDS.md`: `verify`/`levels`/`markout` читают живой каталог).
/// Здесь обрезанный **хвостовой** кадр — это конец прочитанного плюс одна
/// строка stderr; порча (`Corrupt`, `MissingSnapshot`, `TruncatedHeader`)
/// остаётся отказом: она про обрыв хвоста не говорит.
pub(crate) fn read_frame_soft<R: std::io::Read>(
    reader: &mut crate::binlog::Reader<R>,
    path: &Path,
) -> anyhow::Result<Option<Vec<crate::binlog::Record>>> {
    let frame = reader
        .read_frame_soft()
        .map_err(|e| anyhow::anyhow!("кадр {}: {e:?}", path.display()))?;
    if frame.is_none() && reader.truncated_tail() {
        eprintln!(
            "{}: хвостовой кадр обрезан — файл дописывается, читаю прочитанное",
            path.display()
        );
    }
    Ok(frame)
}

/// Сутки из имени файла `<SYMBOL>-<день>[-pN].binlog[.zst]`: первые 10 знаков
/// остатка. Формат проверяет позже `watch` (`BadDay`), здесь только нарезка.
pub(crate) fn day_of_filename(prefix: &str, name: &str) -> Option<String> {
    let rest = strip_binlog_suffix(name.strip_prefix(prefix)?)?;
    if rest.len() < 10 {
        return None;
    }
    Some(rest[..10].to_string())
}

/// Хронологический ключ файла: день, затем часть суток. Правило —
/// `binlog::binlog_file_order_key` (одно на все слои: `commands::lob`,
/// `lob::export`, `bybit::verify`); обёртка осталась, потому что этим именем
/// её зовут `verify.rs` и тесты.
pub(crate) fn file_order_key(prefix: &str, name: &str) -> (String, u32) {
    crate::binlog::binlog_file_order_key(prefix, name)
}

/// Все `<SYMBOL>-*.binlog` и их архивы `<SYMBOL>-*.binlog.zst` каталога,
/// хронологически (день, потом часть) — общий шаг `replay_symbol_over_configs`
/// (запись `lob record`, много суток) и `session_binlog_for` (запись
/// `lob session`, с таска 22 — тоже может нести несколько суток и несколько
/// частей на сутки: владелец пишет одну-две сессии в сутки в тот же `--root`,
/// не одну на весь каталог). Пустой список не ошибка здесь — у обоих
/// вызывающих свой текст на пустоту (общий на «нет файлов вовсе», разный на
/// подсказку про старый недатированный формат).
///
/// Архив (T46) попадает в тот же список: для `Reader` это те же сутки, и
/// разделять их значило бы требовать от каждой читающей команды знания о
/// том, что файл побывал в архиве.
pub(crate) fn list_symbol_binlogs(dir: &Path, symbol: &str) -> anyhow::Result<Vec<PathBuf>> {
    let prefix = format!("{symbol}-");
    let entries = std::fs::read_dir(dir)
        .map_err(|e| anyhow::anyhow!("каталог {} не читается: {e}", dir.display()))?;
    let mut files: Vec<PathBuf> = Vec::new();
    for e in entries {
        let e = e.map_err(|e| anyhow::anyhow!("запись каталога: {e}"))?;
        let name = e.file_name().to_string_lossy().into_owned();
        if name.starts_with(&prefix) && is_binlog_file_name(&name) {
            files.push(e.path());
        }
    }
    // Хронологический порядок и склейка «оригинал + его архив одной части» —
    // одним вызовом: обе работы идут по одному ключу
    // (`binlog::dedupe_same_day_part`), и раздельные сортировки разошлись бы
    // на первом же изменении правила.
    crate::binlog::dedupe_same_day_part(&prefix, &mut files);
    Ok(files)
}

/// Резолвер бинлогов сессии (таск 19, находка G4; список частей — таск 22).
/// До таска 22 каталог `lob session` нёс не больше одного файла на символ
/// (ровно один прогон). С этого таска `lob session --root` можно вызывать
/// несколько раз в те же сутки (В-32, «одна-две сессии в сутки») и часть
/// суток больше не единственная: `session_binlog_path`/`claim_part` отдают
/// следующий свободный номер вместо `part = 1` жёстко, ничего не затирая, а
/// эта функция возвращает **все** файлы символа в каталоге по порядку записи
/// — день, потом часть внутри дня (`list_symbol_binlogs`, тот же ключ
/// `file_order_key`, что уже сортирует много-частевую запись `lob record`
/// выше) — читатели проигрывают их подряд как один поток.
///
/// - Один и больше датированных файлов — это и есть бинлоги сессии, в
///   хронологическом порядке (архивы `*.binlog.zst` — в том же списке, T46).
/// - Ни одного, но есть файл старого формата `<symbol>.binlog` (или его
///   архив `<symbol>.binlog.zst`) без даты — явная ошибка с именем файла и
///   советом переименовать (не тихое «нет файлов», см.
///   `replay_symbol_over_configs`/`bybit::verify::run_verify` выше — тот же
///   приём).
/// - Ни одного и старого формата тоже нет — общая ошибка «нет бинлога».
pub(crate) fn session_binlog_for(dir: &Path, symbol: &str) -> anyhow::Result<Vec<PathBuf>> {
    let files = list_symbol_binlogs(dir, symbol)?;
    if files.is_empty() {
        for suffix in [BINLOG_SUFFIX, BINLOG_ARCHIVE_SUFFIX] {
            let name = format!("{symbol}{suffix}");
            let undated = dir.join(&name);
            if undated.is_file() {
                anyhow::bail!(
                    "файл `{name}` без даты — запись старого формата, переименуйте в \
                     `{symbol}-<дата>{suffix}` ({} в {})",
                    undated.display(),
                    dir.display()
                );
            }
        }
        anyhow::bail!("нет бинлога для `{symbol}` в {}", dir.display());
    }
    Ok(files)
}

/// Одна часть записи сессии с её **собственными** сутками и часом старта
/// (таск 23). До него `profiles`/`watch` брали `day_utc` и `start_hour_utc`
/// из верхнего `session.json` каталога — а его перезаписывает последняя
/// сессия в этот `--root`, и каталог с частями за D и D+1 отдавал всё как
/// D+1: сутки переставали быть кластером, окно «сейчас» фильтровало по
/// завышенному дню. Здесь сутки — из имени файла части
/// (`<SYMBOL>-<день>[-pN].binlog`, то же имя выбирает `record::claim_part`
/// по моменту старта сессии), час — из `session.json.binlog_files` своей
/// части (запись с теми же символом, номером и сутками `started_utc`,
/// таск 22), а если часть там не перечислена (запись до таска 22) — из
/// `start_hour_utc` каталога, как раньше.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct SessionPart {
    pub path: PathBuf,
    pub part: u32,
    /// Календарные сутки старта части, UTC, `YYYY-MM-DD`.
    pub day_utc: String,
    pub start_hour_utc: u32,
}

/// Подмножество `session.json`, нужное атрибуции частей: час старта
/// каталога (запасной путь) и перечень частей с их `started_utc`.
/// Старый формат без `binlog_files` — пустой перечень, не ошибка.
#[derive(Debug, serde::Deserialize)]
struct SessionPartsPeek {
    start_hour_utc: u32,
    #[serde(default)]
    binlog_files: Vec<session::BinlogPart>,
}

fn hour_of_started_utc(started_utc: &str) -> Option<u32> {
    started_utc.get(11..13)?.parse().ok()
}

/// Части сессии символа в каталоге — те же файлы и в том же порядке, что
/// `session_binlog_for`, но с сутками и часом старта на каждую часть.
/// Каталог без разборного `session.json` — ошибка (не сессия), как и
/// отсутствие файлов символа; читатели многих каталогов подряд пропускают
/// такой каталог молча — тот же приём, что у `session_binlog_for`.
pub(crate) fn session_parts_for(dir: &Path, symbol: &str) -> anyhow::Result<Vec<SessionPart>> {
    let meta_path = dir.join("session.json");
    let text = std::fs::read_to_string(&meta_path)
        .map_err(|e| anyhow::anyhow!("{} не читается: {e}", meta_path.display()))?;
    let meta: SessionPartsPeek = serde_json::from_str(&text)
        .map_err(|e| anyhow::anyhow!("{} не разбирается: {e}", meta_path.display()))?;
    let prefix = format!("{symbol}-");
    session_binlog_for(dir, symbol)?
        .into_iter()
        .map(|path| {
            let name = path
                .file_name()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default();
            let (day_utc, part) = file_order_key(&prefix, &name);
            let start_hour_utc = meta
                .binlog_files
                .iter()
                .find(|b| {
                    b.symbol == symbol && b.part == part && b.started_utc.starts_with(&day_utc)
                })
                .and_then(|b| hour_of_started_utc(&b.started_utc))
                .unwrap_or(meta.start_hour_utc);
            Ok(SessionPart {
                path,
                part,
                day_utc,
                start_hour_utc,
            })
        })
        .collect()
}

/// Части, сгруппированные по суткам в хронологическом порядке — единица
/// реплея для `profiles`/`watch` (таск 23): трекер уровней общий на части
/// одних суток (таск 22, «части читаются подряд как один поток») и чистый
/// на каждые новые сутки — между сутками разрыв записи есть всегда.
pub(crate) fn group_parts_by_day(parts: Vec<SessionPart>) -> Vec<(String, Vec<SessionPart>)> {
    let mut out: Vec<(String, Vec<SessionPart>)> = Vec::new();
    for part in parts {
        match out.last_mut() {
            Some((day, group)) if *day == part.day_utc => group.push(part),
            _ => out.push((part.day_utc.clone(), vec![part])),
        }
    }
    out
}

/// Сутки, представленные в каталоге сессии хотя бы одной датированной
/// частью любого символа — по возрастанию. Каталог без `session.json` — не
/// сессия, пустое множество; недатированный `<SYMBOL>.binlog` старого
/// формата суток не даёт. Это вход окна «сейчас» (`profiles`: перечень
/// суток для предрегистрации и счёт сессий внутри/снаружи) и календаря
/// `shortlist` — оба до таска 23 читали одни сутки `started_utc` каталога.
pub(crate) fn session_days_in_dir(dir: &Path) -> std::collections::BTreeSet<String> {
    let mut days = std::collections::BTreeSet::new();
    if !dir.join("session.json").is_file() {
        return days;
    }
    let Ok(entries) = std::fs::read_dir(dir) else {
        return days;
    };
    for e in entries.flatten() {
        let name = e.file_name().to_string_lossy().into_owned();
        if let Some(day) = day_of_binlog_name(&name) {
            days.insert(day.to_string());
        }
    }
    days
}

/// Символ из имени `<SYMBOL>-<день>[-pN].binlog[.zst]` без знания символа:
/// тем же разбором, что `day_of_binlog_name` (хвост после необязательного
/// `-pN` кончается на `-YYYY-MM-DD`), только возвращается голова. Нужен
/// `lob archive`: маркер сверки (`verify-<SYMBOL>.status`, таск 26) лежит по
/// символу, а команда получает путь к файлу. `None` — имя не по раскладке
/// записи (старый недатированный формат, чужой файл).
pub(crate) fn symbol_of_binlog_name(name: &str) -> Option<&str> {
    let stem = strip_binlog_suffix(name)?;
    let stem = match stem.rsplit_once("-p") {
        Some((head, num)) if num.parse::<u32>().is_ok() => head,
        _ => stem,
    };
    let split = stem.len().checked_sub(11)?;
    let head = stem.get(..split)?;
    let day = stem.get(split..)?.strip_prefix('-')?;
    (!head.is_empty() && crate::lob::watch::day_format_ok(day)).then_some(head)
}

/// Сутки из имени `<SYMBOL>-<день>[-pN].binlog[.zst]` без знания символа:
/// хвост после необязательного `-pN` обязан кончаться на `-YYYY-MM-DD`.
/// Иначе — `None` (старый недатированный формат, чужой файл). Архив суток
/// даёт те же сутки, что и оригинал: `session_days_in_dir` не должен видеть
/// в каталоге «лишние» сутки от того, что часть уже упакована (T46).
pub(crate) fn day_of_binlog_name(name: &str) -> Option<&str> {
    let stem = strip_binlog_suffix(name)?;
    let stem = match stem.rsplit_once("-p") {
        Some((head, num)) if num.parse::<u32>().is_ok() => head,
        _ => stem,
    };
    let split = stem.len().checked_sub(11)?;
    let head = stem.get(..split)?;
    let day = stem.get(split..)?.strip_prefix('-')?;
    (!head.is_empty() && crate::lob::watch::day_format_ok(day)).then_some(day)
}
