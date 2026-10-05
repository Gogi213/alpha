//! `lob gaps` — дыры по времени внутри суточных бинлогов (проверка данных TK-032/TK-033).
//!
//! Только чтение. Вход — файлы или каталоги (`*.binlog`, `*.binlog.zst`, v2 и v3), выход — CSV,
//! одна строка на файл (монето-сутки): границы по `exch_ts`/`local_ts` (UTC), покрытие суток,
//! самая длинная тишина и число тишин > 1 мин / > 10 мин / > 1 ч между соседними событиями по
//! `exch_ts`, события по 24 часам UTC. `--sha256` считает хеш файла тем же проходом чтения.

use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use clap::Args;
use sha2::{Digest, Sha256};

use crate::binlog::{is_binlog_file_name, strip_binlog_suffix, Reader};

const NS_PER_SEC: i64 = 1_000_000_000;
const SECS_PER_DAY: i64 = 86_400;
const GAP_1M_S: i64 = 60;
const GAP_10M_S: i64 = 600;
const GAP_1H_S: i64 = 3_600;

/// Аргументы `lob gaps`.
#[derive(Debug, Args)]
pub struct GapsArgs {
    /// Файлы бинлога или каталоги (из каталога берутся все `*.binlog` и `*.binlog.zst`).
    pub paths: Vec<PathBuf>,
    /// Файл со списком путей (по одному на строку), добавляется к `paths`.
    #[arg(long)]
    pub list: Option<PathBuf>,
    /// Посчитать sha256 файла (на диске) тем же проходом чтения.
    #[arg(long, default_value_t = false)]
    pub sha256: bool,
    /// CSV-файл вывода; без флага — stdout.
    #[arg(long)]
    pub out: Option<PathBuf>,
}

/// Итог по одному файлу.
#[derive(Debug, Clone, PartialEq)]
pub struct GapRow {
    pub symbol: String,
    pub day: String,
    pub path: String,
    pub version: u8,
    pub bytes: u64,
    pub records: u64,
    pub first_exch_ns: i64,
    pub last_exch_ns: i64,
    pub first_local_ns: i64,
    pub last_local_ns: i64,
    pub span_pct: f64,
    pub minutes_with_events_pct: f64,
    pub longest_gap_s: f64,
    pub longest_gap_start_ns: i64,
    pub gaps_gt_1m: u64,
    pub gaps_gt_10m: u64,
    pub gaps_gt_1h: u64,
    pub hours: [u64; 24],
    pub sha256: Option<String>,
    pub status: String,
}

struct HashRead<'a, R> {
    inner: R,
    hasher: Option<&'a mut Sha256>,
}

impl<R: Read> Read for HashRead<'_, R> {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        let n = self.inner.read(buf)?;
        if let Some(h) = self.hasher.as_deref_mut() {
            h.update(&buf[..n]);
        }
        Ok(n)
    }
}

/// `<SYMBOL>-<YYYY-MM-DD>[-pN].binlog[.zst]` → (символ, день); нераспознанное — пустые строки.
pub(crate) fn symbol_day_of(path: &Path) -> (String, String) {
    let name = path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    let Some(stem) = strip_binlog_suffix(&name) else {
        return (String::new(), String::new());
    };
    let mut stem = stem;
    if let Some((head, tail)) = stem.rsplit_once("-p") {
        if !tail.is_empty() && tail.bytes().all(|b| b.is_ascii_digit()) {
            stem = head;
        }
    }
    if stem.len() > 11 && stem.is_char_boundary(stem.len() - 10) {
        let (sym, day) = stem.split_at(stem.len() - 10);
        if crate::binlog::parse_calendar_day(day).is_some() && sym.ends_with('-') {
            return (sym[..sym.len() - 1].to_string(), day.to_string());
        }
    }
    (stem.to_string(), String::new())
}

#[derive(Default)]
struct Acc {
    records: u64,
    first_exch: i64,
    last_exch: i64,
    first_local: i64,
    last_local: i64,
    max_exch: i64,
    longest_gap_ns: i64,
    longest_gap_start: i64,
    gaps_1m: u64,
    gaps_10m: u64,
    gaps_1h: u64,
    hours: [u64; 24],
    minutes: Vec<bool>,
}

impl Acc {
    fn new() -> Self {
        Self {
            minutes: vec![false; 1440],
            ..Default::default()
        }
    }

    fn push(&mut self, exch: i64, local: i64) {
        if self.records == 0 {
            self.first_exch = exch;
            self.first_local = local;
            self.max_exch = exch;
        } else if exch > self.max_exch {
            let gap = exch - self.max_exch;
            if gap > self.longest_gap_ns {
                self.longest_gap_ns = gap;
                self.longest_gap_start = self.max_exch;
            }
            if gap > GAP_1M_S * NS_PER_SEC {
                self.gaps_1m += 1;
            }
            if gap > GAP_10M_S * NS_PER_SEC {
                self.gaps_10m += 1;
            }
            if gap > GAP_1H_S * NS_PER_SEC {
                self.gaps_1h += 1;
            }
            self.max_exch = exch;
        }
        self.records += 1;
        self.last_exch = exch;
        self.last_local = local;
        let sod = (exch / NS_PER_SEC).rem_euclid(SECS_PER_DAY) as usize;
        self.hours[sod / 3600] += 1;
        self.minutes[sod / 60] = true;
    }
}

/// Один файл → строка. Ошибка чтения не роняет прогон: она уходит в `status`.
pub fn gaps_for_file(path: &Path, with_sha: bool) -> GapRow {
    let (symbol, day) = symbol_day_of(path);
    let mut row = GapRow {
        symbol,
        day,
        path: path.display().to_string(),
        version: 0,
        bytes: 0,
        records: 0,
        first_exch_ns: 0,
        last_exch_ns: 0,
        first_local_ns: 0,
        last_local_ns: 0,
        span_pct: 0.0,
        minutes_with_events_pct: 0.0,
        longest_gap_s: 0.0,
        longest_gap_start_ns: 0,
        gaps_gt_1m: 0,
        gaps_gt_10m: 0,
        gaps_gt_1h: 0,
        hours: [0; 24],
        sha256: None,
        status: "ok".to_string(),
    };
    let mut hasher = Sha256::new();
    let mut acc = Acc::new();
    let res: anyhow::Result<()> = (|| {
        let file = std::fs::File::open(path)?;
        row.bytes = file.metadata()?.len();
        let mut src = HashRead {
            inner: file,
            hasher: with_sha.then_some(&mut hasher),
        };
        {
            let mut reader = Reader::open(&mut src)?;
            row.version = reader.version();
            while let Some(frame) = reader.read_frame_soft()? {
                for r in &frame {
                    acc.push(r.exch_ts_ns, r.local_ts_ns);
                }
            }
            if reader.truncated_tail() {
                row.status = "truncated_tail".to_string();
            }
        }
        if with_sha {
            std::io::copy(&mut src, &mut std::io::sink())?;
        }
        Ok(())
    })();
    if let Err(e) = res {
        row.status = format!("error: {e}").replace(['\n', '\r', ','], " ");
    }
    if with_sha && !row.status.starts_with("error") {
        row.sha256 = Some(hex(&hasher.finalize()));
    }
    row.records = acc.records;
    if acc.records > 0 {
        row.first_exch_ns = acc.first_exch;
        row.last_exch_ns = acc.last_exch;
        row.first_local_ns = acc.first_local;
        row.last_local_ns = acc.last_local;
        row.span_pct =
            (acc.max_exch - acc.first_exch) as f64 / NS_PER_SEC as f64 / SECS_PER_DAY as f64
                * 100.0;
        row.minutes_with_events_pct =
            acc.minutes.iter().filter(|m| **m).count() as f64 / 1440.0 * 100.0;
        row.longest_gap_s = acc.longest_gap_ns as f64 / NS_PER_SEC as f64;
        row.longest_gap_start_ns = acc.longest_gap_start;
        row.gaps_gt_1m = acc.gaps_1m;
        row.gaps_gt_10m = acc.gaps_10m;
        row.gaps_gt_1h = acc.gaps_1h;
        row.hours = acc.hours;
        if row.day.is_empty() {
            row.day = utc(acc.first_exch)[..10].to_string();
        }
    }
    row
}

pub(crate) fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn utc(ns: i64) -> String {
    chrono::DateTime::from_timestamp(ns.div_euclid(NS_PER_SEC), ns.rem_euclid(NS_PER_SEC) as u32)
        .map(|d| d.format("%Y-%m-%dT%H:%M:%S%.3fZ").to_string())
        .unwrap_or_default()
}

fn utc_or_empty(ns: i64, has: bool) -> String {
    if has {
        utc(ns)
    } else {
        String::new()
    }
}

pub fn csv_header() -> String {
    let mut h = String::from(
        "symbol,day,path,version,bytes,records,first_exch_utc,last_exch_utc,first_local_utc,\
         last_local_utc,span_pct,minutes_with_events_pct,longest_gap_s,longest_gap_start_utc,\
         gaps_gt_1m,gaps_gt_10m,gaps_gt_1h",
    );
    for i in 0..24 {
        h.push_str(&format!(",h{i:02}"));
    }
    h.push_str(",sha256,status");
    h
}

pub fn csv_line(r: &GapRow) -> String {
    let has = r.records > 0;
    let mut s = format!(
        "{},{},\"{}\",{},{},{},{},{},{},{},{:.4},{:.4},{:.3},{},{},{},{}",
        r.symbol,
        r.day,
        r.path.replace('"', "\"\""),
        r.version,
        r.bytes,
        r.records,
        utc_or_empty(r.first_exch_ns, has),
        utc_or_empty(r.last_exch_ns, has),
        utc_or_empty(r.first_local_ns, has),
        utc_or_empty(r.last_local_ns, has),
        r.span_pct,
        r.minutes_with_events_pct,
        r.longest_gap_s,
        utc_or_empty(r.longest_gap_start_ns, has && r.longest_gap_s > 0.0),
        r.gaps_gt_1m,
        r.gaps_gt_10m,
        r.gaps_gt_1h,
    );
    for h in r.hours {
        s.push_str(&format!(",{h}"));
    }
    s.push_str(&format!(
        ",{},{}",
        r.sha256.as_deref().unwrap_or(""),
        r.status
    ));
    s
}

pub(crate) fn collect_paths(args: &GapsArgs) -> anyhow::Result<Vec<PathBuf>> {
    let mut out = Vec::new();
    let mut inputs = args.paths.clone();
    if let Some(list) = &args.list {
        for line in std::fs::read_to_string(list)?.lines() {
            let line = line.trim();
            if !line.is_empty() {
                inputs.push(PathBuf::from(line));
            }
        }
    }
    for p in inputs {
        if p.is_dir() {
            let mut files: Vec<PathBuf> = std::fs::read_dir(&p)?
                .filter_map(|e| e.ok())
                .filter(|e| is_binlog_file_name(&e.file_name().to_string_lossy()))
                .map(|e| e.path())
                .collect();
            files.sort();
            out.extend(files);
        } else {
            out.push(p);
        }
    }
    Ok(out)
}

/// Прогон: печатает CSV построчно (строка уходит сразу, как посчитана файл), возвращает строки.
pub fn run_gaps(args: &GapsArgs) -> anyhow::Result<Vec<GapRow>> {
    let paths = collect_paths(args)?;
    if paths.is_empty() {
        anyhow::bail!("lob gaps: не задано ни одного файла (paths / --list)");
    }
    let mut sink: Box<dyn Write> = match &args.out {
        Some(p) => Box::new(std::io::BufWriter::new(std::fs::File::create(p)?)),
        None => Box::new(std::io::stdout().lock()),
    };
    writeln!(sink, "{}", csv_header())?;
    let mut rows = Vec::with_capacity(paths.len());
    for p in &paths {
        let row = gaps_for_file(p, args.sha256);
        writeln!(sink, "{}", csv_line(&row))?;
        sink.flush()?;
        rows.push(row);
    }
    Ok(rows)
}

#[cfg(test)]
mod tests;
