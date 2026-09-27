//! `--minute-flow` (TK-012, П-08 §12, Г-140): минутный ряд `add`/`cancel`/`trade`
//! по всей видимой книге обеих сторон — `crate::lob::minute_flow::MinuteFlow`.
//!
//! Отдельный проход по тем же суточным файлам тем же порядком, что
//! `replay::replay_symbol_over_configs_keep` (книга с нуля на каждый файл,
//! разрыв последовательности останавливает файл): общий реплей касаний не
//! трогается, без флага байты всех прежних выходов те же. Снапшот посреди файла
//! и начало файла — база без потока.

use std::path::{Path, PathBuf};

use crate::binlog::Reader;
use crate::book::{Book, Side};
use crate::bybit::verify::FileReplayer;
use crate::lob::minute_flow::{MinuteFlow, MinuteRow};

use super::super::parts::{day_of_filename, list_symbol_binlogs, read_frame_soft};
use super::super::replay::trade_hit_from_record;

/// Колонки файла минутного ряда.
pub const MINUTE_FLOW_COLUMNS: [&str; 5] = [
    "minute_ms",
    "symbol",
    "add_lots",
    "cancel_lots",
    "trade_lots",
];

/// Путь файла: каталог → `<dir>/minute-flow-<SYMBOL>.csv` (как
/// `touches-<SYMBOL>.csv`/`mids1m-<SYMBOL>.csv`: один файл на символ, сутки
/// подряд), иначе — сам путь.
pub fn minute_flow_path(arg: &Path, symbol: &str) -> PathBuf {
    if arg.is_dir() {
        arg.join(format!("minute-flow-{symbol}.csv"))
    } else {
        arg.to_path_buf()
    }
}

/// Проход по суточным файлам символа и запись ряда. `emit_day` — только
/// файлы этих суток (как `--emit-day` у касаний). Возвращает путь и число строк.
pub fn write_minute_flow(
    root: &Path,
    symbol: &str,
    emit_day: Option<&str>,
    arg: &Path,
) -> anyhow::Result<(PathBuf, usize)> {
    let prefix = format!("{symbol}-");
    let files = list_symbol_binlogs(root, symbol)?;
    let mut mf = MinuteFlow::new();
    let mut rows: Vec<MinuteRow> = Vec::new();
    for path in &files {
        let name = path
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        let day = day_of_filename(&prefix, &name)
            .ok_or_else(|| anyhow::anyhow!("имя {name} не разбирается как сутки"))?;
        if emit_day.is_some_and(|d| d != day) {
            continue;
        }
        let data = std::fs::read(path)
            .map_err(|e| anyhow::anyhow!("файл {} не читается: {e}", path.display()))?;
        let mut reader = Reader::open(&data[..])
            .map_err(|e| anyhow::anyhow!("заголовок {}: {e:?}", path.display()))?;
        let header = reader.header();
        let mut book = Book::new(header.tick_e9, header.step_e9);
        let mut replayer = FileReplayer::new();
        let mut ups = Vec::new();
        let mut tps = Vec::new();
        let mut file_ok = true;
        mf.reset_book();
        while let Some(frame_records) = read_frame_soft(&mut reader, path)? {
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
                for up in &ups {
                    if book.apply(up).is_err() {
                        file_ok = false;
                        break;
                    }
                    mf.observe_frame(
                        up.cts_ms,
                        book.levels(Side::Bid),
                        book.levels(Side::Ask),
                        up.is_snapshot,
                        &mut rows,
                    );
                }
                if !file_ok {
                    break;
                }
                if let Some(h) = trade_hit_from_record(rec) {
                    mf.observe_trade(h, &mut rows);
                }
            }
            if !file_ok {
                break;
            }
        }
        if file_ok {
            let mut tail = Vec::new();
            replayer.finish(&mut tail);
            for up in &tail {
                if book.apply(up).is_err() {
                    break;
                }
                mf.observe_frame(
                    up.cts_ms,
                    book.levels(Side::Bid),
                    book.levels(Side::Ask),
                    up.is_snapshot,
                    &mut rows,
                );
            }
        }
    }
    mf.finish(&mut rows);
    if mf.pending_overflow > 0 {
        eprintln!(
            "{symbol}: --minute-flow: {} сделок не уместились в копилку между кадрами \
             (падение ушло в cancel)",
            mf.pending_overflow
        );
    }
    let out = minute_flow_path(arg, symbol);
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut w = csv::Writer::from_path(&out)?;
    w.write_record(MINUTE_FLOW_COLUMNS)?;
    for r in &rows {
        w.write_record([
            r.minute_ms.to_string(),
            symbol.to_string(),
            r.add_lots.to_string(),
            r.cancel_lots.to_string(),
            r.trade_lots.to_string(),
        ])?;
    }
    w.flush()?;
    Ok((out, rows.len()))
}
