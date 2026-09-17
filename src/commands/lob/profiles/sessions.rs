//! Каталог сессий под `--root`: подкаталоги, маркер сверки
//! `verify-<SYMBOL>.status` и реплей частей одних суток в записи уровней и
//! срезы середины. Ввод-вывод и книга — здесь; оси и накопление его не
//! видят.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use crate::binlog::Reader;
use crate::book::Book;
use crate::bybit::verify::FileReplayer;
use crate::commands::lob::{feed_frames, session_days_in_dir, trade_hit_from_record, SessionPart};
use crate::lob::levels::{LevelRecord, LevelTracker, LevelsConfig};
use crate::lob::markout::MidSample;

// ---------------------------------------------------------------------------
// Каталог сессий: session.json, маркер сверки, реплей одного файла в записи
// уровней и срезы середины. Раскладка и приёмы — те же, что
// `commands::lob::watch` (частный модуль, не переиспользуется напрямую: его
// функции — не `pub`, а зона этого таска не трогает файл watch.rs).
// ---------------------------------------------------------------------------

pub(crate) fn read_verify_marker(path: &Path) -> bool {
    std::fs::read_to_string(path)
        .map(|s| s.trim() == "ok")
        .unwrap_or(false)
}

pub(crate) fn session_dirs(root: &Path) -> anyhow::Result<Vec<PathBuf>> {
    let entries = std::fs::read_dir(root)
        .map_err(|e| anyhow::anyhow!("корень {} не читается: {e}", root.display()))?;
    let mut dirs: Vec<PathBuf> = Vec::new();
    for e in entries {
        let e = e.map_err(|e| anyhow::anyhow!("запись каталога: {e}"))?;
        let path = e.path();
        if path.is_dir() {
            dirs.push(path);
        }
    }
    dirs.sort();
    Ok(dirs)
}

/// Различные сутки среди подкаталогов, по возрастанию — вход
/// `load_or_write_window` для первой записи файла предрегистрации (тот же
/// вход, что `split_calendar` уже берёт у `commands::lob::shortlist`).
/// С таска 23 сутки — по датированным частям каталога
/// (`super::session_days_in_dir`), не по `started_utc` его `session.json`:
/// каталог с частями за D и D+1 даёт обе даты.
pub(crate) fn distinct_session_days(dirs: &[PathBuf]) -> Vec<String> {
    let mut days: BTreeSet<String> = BTreeSet::new();
    for dir in dirs {
        days.extend(session_days_in_dir(dir));
    }
    days.into_iter().collect()
}

/// Реплеит части одних суток одной сессии (`super::group_parts_by_day`,
/// таск 23) в записи уровней и срезы середины: кормит книгу и трекер общим
/// `super::feed_frames`, сделки — общим `super::trade_hit_from_record`.
/// Трекер общий на части суток (таск 22, «части читаются подряд как один
/// поток») и чистый на каждые сутки — между сутками разрыв записи есть
/// всегда, и сутки — единица кластера, а не продолжение потока. Книга и
/// `FileReplayer` заводятся заново на каждый файл — тем же приёмом, что
/// `mod.rs::replay_symbol_over_configs` уже применяет к частям суток `lob
/// record` (каждый файл несёт собственный снапшот в начале, продолжать
/// старую книгу через границу файла было бы чтением чужого состояния).
///
/// Возвращает записи **по частям** (в порядке `parts`) — каждая часть несёт
/// свой час старта, и `accumulate_level` получает его от своей части, не от
/// каталога; срезы середины — общие на сутки, как и трекер.
pub(super) fn replay_one_session_day(
    parts: &[SessionPart],
    cfg: LevelsConfig,
) -> anyhow::Result<(Vec<Vec<LevelRecord>>, Vec<MidSample>)> {
    let mut tracker = LevelTracker::new(cfg);
    let mut per_part = Vec::with_capacity(parts.len());
    let mut mids = Vec::new();
    for part in parts {
        let mut records = Vec::new();
        replay_binlog_file_into(&part.path, &mut tracker, &mut records, &mut mids)?;
        per_part.push(records);
    }
    Ok((per_part, mids))
}

/// Один файл-часть через общий трекер; книга и `FileReplayer` — свои.
fn replay_binlog_file_into(
    path: &Path,
    tracker: &mut LevelTracker,
    records: &mut Vec<LevelRecord>,
    mids: &mut Vec<MidSample>,
) -> anyhow::Result<()> {
    let data = std::fs::read(path)
        .map_err(|e| anyhow::anyhow!("файл {} не читается: {e}", path.display()))?;
    let mut reader = Reader::open(&data[..])
        .map_err(|e| anyhow::anyhow!("заголовок {}: {e:?}", path.display()))?;
    let header = reader.header();
    let mut book = Book::new(header.tick_e9, header.step_e9);
    let mut replayer = FileReplayer::new();
    let mut ups = Vec::new();
    let mut tps = Vec::new();
    'frames: loop {
        let frame = super::super::parts::read_frame_soft(&mut reader, path)?;
        let Some(frame_records) = frame else { break };
        for rec in &frame_records {
            ups.clear();
            tps.clear();
            replayer.push_frame(
                std::slice::from_ref(rec),
                header.tick_e9,
                header.step_e9,
                &mut ups,
                &mut tps,
            );
            let hit = trade_hit_from_record(rec);
            for up in &ups {
                if book.apply(up).is_err() {
                    break 'frames;
                }
                feed_frames(&book, tracker, up.cts_ms, records, mids);
            }
            if let Some(h) = hit {
                tracker.observe_trade(h);
            }
        }
    }
    let mut tail = Vec::new();
    replayer.finish(&mut tail);
    for up in &tail {
        if book.apply(up).is_err() {
            break;
        }
        feed_frames(&book, tracker, up.cts_ms, records, mids);
    }
    Ok(())
}
