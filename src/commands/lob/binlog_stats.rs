//! `lob binlog-stats` — что лежит в бинлоге и за что платятся байты.
//!
//! Задача (владелец 2026-09-13: «оптимизировать коллектор сильно: формат
//! файлов, формат записи, тип записи, скорость, объём») начинается с замера:
//! формат нельзя чинить, не зная, какие поля вообще несут информацию, сколько
//! записей на кадр и какие типы событий доминируют по числу.
//!
//! Команда **только читает** файл и печатает счётчики: ни порогов, ни
//! вердиктов. Скорость декодирования не меряется здесь намеренно — её меряет
//! `lob react`/`--times` на живом пути, а не разовый проход по диску.

use std::path::{Path, PathBuf};

use clap::Args;

use crate::binlog::{Reader, Record};

/// Аргументы `lob binlog-stats`.
#[derive(Debug, Args)]
pub struct BinlogStatsArgs {
    /// Файл бинлога (`<SYMBOL>-<день>.binlog`).
    #[arg(long)]
    pub path: PathBuf,
}

/// Итог разбора — для печати диспетчером и для тестов.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct BinlogStats {
    pub records: u64,
    pub frames: u64,
    pub bytes_on_disk: u64,
    /// Записей по коду типа события (`ev`), отсортировано по убыванию числа.
    pub by_ev: Vec<(u64, u64)>,
    /// Живые поля: сколько записей несут ненулевое значение.
    pub nonzero_order_id: u64,
    pub nonzero_ival: u64,
    pub nonzero_fval: u64,
    /// `local_ts != exch_ts` — сколько записей несут собственную метку приёма.
    pub local_ts_differs: u64,
    pub min_records_in_frame: u64,
    pub max_records_in_frame: u64,
}

/// Разбирает файл целиком и считает счётчики. `Err` — только ввод-вывод и
/// порча формата: счётчики на испорченном файле не печатаются частично.
pub fn run_binlog_stats(args: &BinlogStatsArgs) -> anyhow::Result<BinlogStats> {
    let file = std::fs::File::open(&args.path)?;
    let bytes_on_disk = file.metadata()?.len();
    let mut reader = Reader::open(file)?;
    let mut stats = BinlogStats {
        bytes_on_disk,
        min_records_in_frame: u64::MAX,
        ..Default::default()
    };
    let mut by_ev: std::collections::BTreeMap<u64, u64> = std::collections::BTreeMap::new();
    while let Some(frame) = reader.read_frame()? {
        stats.frames += 1;
        let n = frame.len() as u64;
        stats.records += n;
        stats.min_records_in_frame = stats.min_records_in_frame.min(n);
        stats.max_records_in_frame = stats.max_records_in_frame.max(n);
        for r in &frame {
            *by_ev.entry(r.ev).or_insert(0) += 1;
            count_fields(&mut stats, r);
        }
    }
    if stats.frames == 0 {
        stats.min_records_in_frame = 0;
    }
    let mut by_ev: Vec<(u64, u64)> = by_ev.into_iter().collect();
    by_ev.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    stats.by_ev = by_ev;
    Ok(stats)
}

fn count_fields(stats: &mut BinlogStats, r: &Record) {
    if r.order_id != 0 {
        stats.nonzero_order_id += 1;
    }
    if r.ival != 0 {
        stats.nonzero_ival += 1;
    }
    if r.fval != 0.0 {
        stats.nonzero_fval += 1;
    }
    if r.local_ts_ns != r.exch_ts_ns {
        stats.local_ts_differs += 1;
    }
}

/// Строки отчёта — одна на факт, чтобы диспетчер не считал сам.
pub fn summary_lines(path: &Path, s: &BinlogStats) -> Vec<String> {
    let per_record = if s.records > 0 {
        s.bytes_on_disk as f64 / s.records as f64
    } else {
        0.0
    };
    let frame_avg = if s.frames > 0 {
        s.records as f64 / s.frames as f64
    } else {
        0.0
    };
    let share = |v: u64| {
        if s.records > 0 {
            100.0 * v as f64 / s.records as f64
        } else {
            0.0
        }
    };
    let mut lines = vec![
        format!(
            "binlog-stats: {} · {:.1} МБ · записей {} · кадров {} ({:.0} записей на кадр, от {} до {}) · {:.2} Б/запись",
            path.display(),
            s.bytes_on_disk as f64 / 1e6,
            s.records,
            s.frames,
            frame_avg,
            s.min_records_in_frame,
            s.max_records_in_frame,
            per_record
        ),
        format!(
            "binlog-stats: живые поля — order_id {:.1} % · ival {:.1} % · fval {:.1} % · local_ts ≠ exch_ts {:.1} %",
            share(s.nonzero_order_id),
            share(s.nonzero_ival),
            share(s.nonzero_fval),
            share(s.local_ts_differs)
        ),
    ];
    for (ev, n) in s.by_ev.iter().take(8) {
        lines.push(format!(
            "binlog-stats: ev={ev:#x} — {n} записей ({:.1} %)",
            share(*n)
        ));
    }
    lines
}

#[cfg(test)]
mod tests;
