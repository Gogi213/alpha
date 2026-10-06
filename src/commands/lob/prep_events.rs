//! `lob prep-events` и чтение подготовленных монето-суток (TK-050, решение CEO 05.10).
//!
//! Подготовленный файл `<имя бинлога>.prep` хранит поток строк счёта (`CompactEvent` после чтения кадров, `Update`
//! и перевода стороны) колонками с дельтами и zstd — кусками по `CHUNK_EVENTS` строк. Файл заменяет декод бинлога
//! и счётный проход (число строк — в заголовке); индекс кусков по времени даёт довеску D+1 читать только начало.
//! Исходный бинлог только читается (В-172). Строки совпадают с прежним путём побитно: проверка при записи.
//!
//! Раскладка: `ALPREP01` | n_events u64 | src_len u64 | src_mtime u64 | sha256 бинлога [32] | n_chunks u32 |
//! таблица кусков (first_local_ts i64, n u32, offset u64, len u32) | куски. Кусок — 5 колонок
//! (вид, Δlocal_ts, Δexch_ms, Δцены по виду, qty), каждая `raw_len u32 | comp_len u32 | zstd`; состояние дельт
//! в начале куска нулевое. Читатель сверяет размер+mtime бинлога (хеш — для проверки вручную, не на горячем пути).

use std::fs::File;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

use clap::Args;
use sha2::{Digest, Sha256};

use crate::lob::backtest::CompactEvent;

use super::backtest::{feed_compact_each, open_replay_feed};

const MAGIC: &[u8; 8] = b"ALPREP01";
const CHUNK_EVENTS: usize = 1 << 20;
pub(super) const NCOL: usize = 5;
const HEADER_FIXED: usize = 8 + 8 + 8 + 8 + 32 + 4;
const ENTRY_LEN: usize = 8 + 4 + 8 + 4;

#[derive(Debug, Args)]
pub struct PrepEventsArgs {
    /// Файлы бинлога или каталоги (из каталога — все `*.binlog`).
    pub paths: Vec<PathBuf>,
    /// Файл со списком путей (по одному на строку), добавляется к `paths`.
    #[arg(long)]
    pub list: Option<PathBuf>,
    /// Каталоги вывода (`<каталог>/<имя бинлога>.prep`); несколько — сутки чередуются по чётности дня.
    #[arg(long = "out-dir", required = true)]
    pub out_dir: Vec<PathBuf>,
    /// Уровень zstd.
    #[arg(long, default_value_t = 19)]
    pub level: i32,
    /// Потоков подготовки.
    #[arg(long, default_value_t = 1)]
    pub jobs: usize,
    /// Не сверять записанный файл со свежим переводом бинлога.
    #[arg(long)]
    pub no_verify: bool,
    /// Переписать и годный файл.
    #[arg(long)]
    pub force: bool,
}

fn put_uv(buf: &mut Vec<u8>, mut v: u64) {
    while v >= 0x80 {
        buf.push((v as u8) | 0x80);
        v >>= 7;
    }
    buf.push(v as u8);
}

fn put_zz(buf: &mut Vec<u8>, v: i64) {
    put_uv(buf, ((v << 1) ^ (v >> 63)) as u64);
}

#[inline(always)]
fn get_uv(buf: &[u8], pos: &mut usize) -> u64 {
    let b = buf[*pos];
    *pos += 1;
    if b < 0x80 {
        return u64::from(b);
    }
    let mut r = u64::from(b & 0x7f);
    let mut sh = 7;
    loop {
        let b = buf[*pos];
        *pos += 1;
        r |= u64::from(b & 0x7f) << sh;
        if b < 0x80 {
            return r;
        }
        sh += 7;
    }
}

#[inline(always)]
fn get_zz(buf: &[u8], pos: &mut usize) -> i64 {
    let v = get_uv(buf, pos);
    ((v >> 1) as i64) ^ -((v & 1) as i64)
}

pub(super) fn encode_chunk(evs: &[CompactEvent], level: i32) -> anyhow::Result<Vec<u8>> {
    let mut c: [Vec<u8>; NCOL] = Default::default();
    let (mut lo, mut ex) = (0i64, 0i64);
    let mut px = [0i64; 4];
    for e in evs {
        let [l, ek, p, q] = e.raw();
        let k = (ek & 3) as usize;
        c[0].push(k as u8);
        put_zz(&mut c[1], l.wrapping_sub(lo));
        put_zz(&mut c[2], (ek >> 2).wrapping_sub(ex));
        put_zz(&mut c[3], p.wrapping_sub(px[k]));
        put_zz(&mut c[4], q);
        lo = l;
        ex = ek >> 2;
        px[k] = p;
    }
    let mut out = Vec::new();
    for col in &c {
        let z = zstd::bulk::compress(col, level)?;
        out.extend_from_slice(&(col.len() as u32).to_le_bytes());
        out.extend_from_slice(&(z.len() as u32).to_le_bytes());
        out.extend_from_slice(&z);
    }
    Ok(out)
}

/// Распаковывает кусок из `data` и дописывает `n` строк в `out`, но не дальше первой строки с
/// `local_ts >= until` (как перевод потока с потолком). `true` — потолок достигнут.
pub(super) fn decode_chunk(
    data: &[u8],
    n: usize,
    until: Option<i64>,
    scratch: &mut [Vec<u8>; NCOL],
    out: &mut Vec<CompactEvent>,
) -> anyhow::Result<bool> {
    let mut at = 0usize;
    for col in scratch.iter_mut() {
        anyhow::ensure!(data.len() >= at + 8, "подготовленный файл: обрыв куска");
        let raw = u32::from_le_bytes(data[at..at + 4].try_into()?) as usize;
        let comp = u32::from_le_bytes(data[at + 4..at + 8].try_into()?) as usize;
        at += 8;
        anyhow::ensure!(
            data.len() >= at + comp,
            "подготовленный файл: обрыв колонки"
        );
        col.resize(raw, 0);
        let got = zstd::bulk::Decompressor::new()?
            .decompress_to_buffer(&data[at..at + comp], col.as_mut_slice())?;
        anyhow::ensure!(got == raw, "подготовленный файл: длина колонки");
        at += comp;
    }
    let mut p = [0usize; NCOL];
    let (mut lo, mut ex) = (0i64, 0i64);
    let mut px = [0i64; 4];
    anyhow::ensure!(
        scratch[0].len() == n,
        "подготовленный файл: число строк куска"
    );
    for i in 0..n {
        let k = scratch[0][i] as usize;
        lo = lo.wrapping_add(get_zz(&scratch[1], &mut p[1]));
        ex = ex.wrapping_add(get_zz(&scratch[2], &mut p[2]));
        px[k] = px[k].wrapping_add(get_zz(&scratch[3], &mut p[3]));
        let q = get_zz(&scratch[4], &mut p[4]);
        if until.is_some_and(|u| lo >= u) {
            return Ok(true);
        }
        out.push(CompactEvent::from_raw([lo, (ex << 2) | k as i64, px[k], q]));
    }
    Ok(false)
}

pub(crate) fn file_stamp(path: &Path) -> Option<(u64, u64)> {
    let meta = std::fs::metadata(path).ok()?;
    let mtime = meta
        .modified()
        .ok()?
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?
        .as_secs();
    Some((meta.len(), mtime))
}

fn prep_name(src: &Path) -> Option<std::ffi::OsString> {
    let mut n = src.file_name()?.to_owned();
    n.push(".prep");
    Some(n)
}

struct ChunkEntry {
    first_local_ts: i64,
    n: u32,
    offset: u64,
    len: u32,
}

/// Открытый подготовленный файл, годный к своему бинлогу.
pub(crate) struct Prepared {
    file: File,
    n_events: usize,
    chunks: Vec<ChunkEntry>,
}

impl Prepared {
    pub(crate) fn n_events(&self) -> usize {
        self.n_events
    }

    /// Верхняя граница числа строк до потолка `until` (куски целиком до куска, где потолок).
    fn upper_bound(&self, until: Option<i64>) -> usize {
        let mut total = 0usize;
        for c in &self.chunks {
            if until.is_some_and(|u| c.first_local_ts >= u) {
                break;
            }
            total += c.n as usize;
        }
        total
    }

    /// Дописывает строки в `out`, не дальше первой с `local_ts >= until`; `true` — упёрлись в потолок.
    fn append_until(
        &mut self,
        until: Option<i64>,
        out: &mut Vec<CompactEvent>,
    ) -> anyhow::Result<bool> {
        let mut scratch: [Vec<u8>; NCOL] = Default::default();
        let mut buf = Vec::new();
        for i in 0..self.chunks.len() {
            let (first, n, off, len) = {
                let c = &self.chunks[i];
                (c.first_local_ts, c.n as usize, c.offset, c.len as usize)
            };
            if until.is_some_and(|u| first >= u) {
                return Ok(true);
            }
            buf.resize(len, 0);
            self.file.seek(SeekFrom::Start(off))?;
            self.file.read_exact(&mut buf)?;
            if decode_chunk(&buf, n, until, &mut scratch, out)? {
                return Ok(true);
            }
        }
        Ok(false)
    }
}

struct Header {
    n_events: usize,
    src_len: u64,
    src_mtime: u64,
    chunks: Vec<ChunkEntry>,
}

fn read_header(file: &mut File) -> anyhow::Result<Header> {
    let mut fixed = [0u8; HEADER_FIXED];
    file.read_exact(&mut fixed)?;
    anyhow::ensure!(&fixed[..8] == MAGIC, "не подготовленный файл (магия)");
    let u64_at = |o: usize| u64::from_le_bytes(fixed[o..o + 8].try_into().unwrap());
    let n_events = u64_at(8) as usize;
    let (src_len, src_mtime) = (u64_at(16), u64_at(24));
    let n_chunks = u32::from_le_bytes(fixed[64..68].try_into().unwrap()) as usize;
    let mut table = vec![0u8; n_chunks * ENTRY_LEN];
    file.read_exact(&mut table)?;
    let chunks = table
        .chunks_exact(ENTRY_LEN)
        .map(|e| ChunkEntry {
            first_local_ts: i64::from_le_bytes(e[0..8].try_into().unwrap()),
            n: u32::from_le_bytes(e[8..12].try_into().unwrap()),
            offset: u64::from_le_bytes(e[12..20].try_into().unwrap()),
            len: u32::from_le_bytes(e[20..24].try_into().unwrap()),
        })
        .collect();
    Ok(Header {
        n_events,
        src_len,
        src_mtime,
        chunks,
    })
}

/// Подготовленный файл для бинлога `src` в одном из `dirs`: есть, читается и размер+mtime бинлога те же.
pub(crate) fn open_prepared(dirs: &[PathBuf], src: &Path) -> Option<Prepared> {
    let name = prep_name(src)?;
    let (len, mtime) = file_stamp(src)?;
    for d in dirs {
        let Ok(mut file) = File::open(d.join(&name)) else {
            continue;
        };
        let Ok(h) = read_header(&mut file) else {
            continue;
        };
        if h.src_len == len && h.src_mtime == mtime {
            return Some(Prepared {
                file,
                n_events: h.n_events,
                chunks: h.chunks,
            });
        }
    }
    None
}

/// Все части годны как подготовленные — иначе `None` (прежний путь целиком, без смешения).
fn open_all(dirs: &[PathBuf], parts: &[PathBuf]) -> Option<Vec<Prepared>> {
    if dirs.is_empty() {
        return None;
    }
    parts.iter().map(|p| open_prepared(dirs, p)).collect()
}

/// События суток из подготовленных файлов — `Vec` точного размера (число строк — из заголовков).
pub(crate) fn prepared_day_events(
    dirs: &[PathBuf],
    parts: &[PathBuf],
) -> anyhow::Result<Option<Vec<CompactEvent>>> {
    let Some(mut all) = open_all(dirs, parts) else {
        return Ok(None);
    };
    let total: usize = all.iter().map(Prepared::n_events).sum();
    let mut events = Vec::with_capacity(total);
    for p in &mut all {
        p.append_until(None, &mut events)?;
    }
    anyhow::ensure!(
        events.len() == total,
        "подготовленные сутки: число строк не сошлось с заголовком"
    );
    Ok(Some(events))
}

/// Довесок D+1 из подготовленных файлов: дописывает строки до потолка; `None` — файлы не годны (прежний путь).
pub(crate) fn prepared_carry_events(
    dirs: &[PathBuf],
    parts: &[PathBuf],
    until_ns: i64,
    events: &mut Vec<CompactEvent>,
) -> anyhow::Result<Option<usize>> {
    let Some(mut all) = open_all(dirs, parts) else {
        return Ok(None);
    };
    let before = events.len();
    let mut reserved = false;
    for p in &mut all {
        if !reserved {
            events.reserve_exact(p.upper_bound(Some(until_ns)));
            reserved = true;
        }
        if p.append_until(Some(until_ns), events)? {
            break;
        }
    }
    Ok(Some(events.len() - before))
}

fn day_parity(src: &Path) -> usize {
    let stem = src.file_name().and_then(|n| n.to_str()).unwrap_or("");
    let stem = stem.strip_suffix(".binlog").unwrap_or(stem);
    let day = stem.get(stem.len().saturating_sub(10)..).unwrap_or("");
    chrono::NaiveDate::parse_from_str(day, "%Y-%m-%d")
        .map(|d| chrono::Datelike::num_days_from_ce(&d) as usize)
        .unwrap_or(0)
}

fn sha256_of(path: &Path) -> anyhow::Result<[u8; 32]> {
    let mut f = File::open(path)?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(h.finalize().into())
}

/// Итог подготовки одного файла: (строк, байт бинлога, байт подготовленного, секунд).
struct PrepStat {
    events: u64,
    src_bytes: u64,
    prep_bytes: u64,
    secs: f64,
    skipped: bool,
}

fn prepare_one(
    src: &Path,
    out_dirs: &[PathBuf],
    args: &PrepEventsArgs,
) -> anyhow::Result<PrepStat> {
    let started = std::time::Instant::now();
    let dir = &out_dirs[day_parity(src) % out_dirs.len()];
    let name =
        prep_name(src).ok_or_else(|| anyhow::anyhow!("{}: нет имени файла", src.display()))?;
    let dest = dir.join(&name);
    let (src_len, src_mtime) =
        file_stamp(src).ok_or_else(|| anyhow::anyhow!("{}: не читается", src.display()))?;
    if !args.force && open_prepared(std::slice::from_ref(dir), src).is_some() {
        let prep_bytes = std::fs::metadata(&dest)?.len();
        return Ok(PrepStat {
            events: 0,
            src_bytes: src_len,
            prep_bytes,
            secs: 0.0,
            skipped: true,
        });
    }
    let sha = sha256_of(src)?;
    let mut feed = open_replay_feed(src)?;
    let mut chunks_data: Vec<Vec<u8>> = Vec::new();
    let mut entries: Vec<(i64, u32)> = Vec::new();
    let mut pending: Vec<CompactEvent> = Vec::with_capacity(CHUNK_EVENTS);
    let mut total = 0u64;
    let mut err: Option<anyhow::Error> = None;
    let mut flush = |pending: &mut Vec<CompactEvent>| {
        if pending.is_empty() || err.is_some() {
            pending.clear();
            return;
        }
        match encode_chunk(pending, args.level) {
            Ok(b) => {
                entries.push((pending[0].raw()[0], pending.len() as u32));
                total += pending.len() as u64;
                chunks_data.push(b);
            }
            Err(e) => err = Some(e),
        }
        pending.clear();
    };
    feed_compact_each(&mut feed, &mut |ev| {
        pending.push(ev);
        if pending.len() == CHUNK_EVENTS {
            flush(&mut pending);
        }
    });
    flush(&mut pending);
    if let Some(e) = err {
        return Err(e);
    }
    let table_len = entries.len() * ENTRY_LEN;
    let mut offset = (HEADER_FIXED + table_len) as u64;
    let mut file_bytes =
        Vec::with_capacity(offset as usize + chunks_data.iter().map(Vec::len).sum::<usize>());
    file_bytes.extend_from_slice(MAGIC);
    file_bytes.extend_from_slice(&total.to_le_bytes());
    file_bytes.extend_from_slice(&src_len.to_le_bytes());
    file_bytes.extend_from_slice(&src_mtime.to_le_bytes());
    file_bytes.extend_from_slice(&sha);
    file_bytes.extend_from_slice(&(entries.len() as u32).to_le_bytes());
    for ((first, n), data) in entries.iter().zip(&chunks_data) {
        file_bytes.extend_from_slice(&first.to_le_bytes());
        file_bytes.extend_from_slice(&n.to_le_bytes());
        file_bytes.extend_from_slice(&offset.to_le_bytes());
        file_bytes.extend_from_slice(&(data.len() as u32).to_le_bytes());
        offset += data.len() as u64;
    }
    for d in &chunks_data {
        file_bytes.extend_from_slice(d);
    }
    drop(chunks_data);
    let tmp = dir.join(format!("{}.tmp", name.to_string_lossy()));
    {
        let mut f = File::create(&tmp)?;
        f.write_all(&file_bytes)?;
        f.sync_all()?;
    }
    std::fs::rename(&tmp, &dest)?;
    let prep_bytes = file_bytes.len() as u64;
    drop(file_bytes);
    if !args.no_verify {
        verify(src, &dest)?;
    }
    Ok(PrepStat {
        events: total,
        src_bytes: src_len,
        prep_bytes,
        secs: started.elapsed().as_secs_f64(),
        skipped: false,
    })
}

/// Свежий перевод бинлога против записанного файла — строка в строку; расхождение — ошибка (файл удаляется).
fn verify(src: &Path, dest: &Path) -> anyhow::Result<()> {
    let mut file = File::open(dest)?;
    let h = read_header(&mut file)?;
    let n_events = h.n_events;
    let mut prepared = Prepared {
        file,
        n_events,
        chunks: h.chunks,
    };
    let mut feed = open_replay_feed(src)?;
    let mut back: Vec<CompactEvent> = Vec::new();
    prepared.append_until(None, &mut back)?;
    let mut idx = 0usize;
    let mut bad = false;
    feed_compact_each(&mut feed, &mut |ev| {
        if back.get(idx) != Some(&ev) {
            bad = true;
        }
        idx += 1;
    });
    if bad || idx != back.len() || idx != n_events {
        let _ = std::fs::remove_file(dest);
        anyhow::bail!(
            "{}: подготовленный файл не совпал с переводом бинлога (удалён)",
            src.display()
        );
    }
    Ok(())
}

fn collect_sources(args: &PrepEventsArgs) -> anyhow::Result<Vec<PathBuf>> {
    let mut raw = args.paths.clone();
    if let Some(list) = &args.list {
        for line in std::fs::read_to_string(list)?.lines() {
            let l = line.trim();
            if !l.is_empty() {
                raw.push(PathBuf::from(l));
            }
        }
    }
    let mut out = Vec::new();
    for p in raw {
        if p.is_dir() {
            let mut names: Vec<PathBuf> = std::fs::read_dir(&p)?
                .filter_map(Result::ok)
                .map(|e| e.path())
                .filter(|q| q.extension().is_some_and(|x| x == "binlog"))
                .collect();
            names.sort();
            out.extend(names);
        } else {
            out.push(p);
        }
    }
    Ok(out)
}

pub fn run_prep_events(args: &PrepEventsArgs) -> anyhow::Result<()> {
    let sources = collect_sources(args)?;
    for d in &args.out_dir {
        std::fs::create_dir_all(d)?;
    }
    let next = AtomicUsize::new(0);
    let results: std::sync::Mutex<Vec<(usize, anyhow::Result<PrepStat>)>> =
        std::sync::Mutex::new(Vec::new());
    let started = std::time::Instant::now();
    std::thread::scope(|s| {
        for _ in 0..args.jobs.max(1) {
            s.spawn(|| loop {
                let i = next.fetch_add(1, Ordering::Relaxed);
                let Some(src) = sources.get(i) else {
                    break;
                };
                let r = prepare_one(src, &args.out_dir, args);
                results.lock().unwrap().push((i, r));
            });
        }
    });
    let mut results = results.into_inner().unwrap();
    results.sort_by_key(|(i, _)| *i);
    let (mut ev, mut sb, mut pb, mut cpu, mut fails) = (0u64, 0u64, 0u64, 0f64, 0usize);
    for (i, r) in results {
        match r {
            Ok(st) => {
                println!(
                    "{}: строк {} · бинлог {} Б · подготовлено {} Б ({:.3}) · {:.1} с{}",
                    sources[i].display(),
                    st.events,
                    st.src_bytes,
                    st.prep_bytes,
                    st.prep_bytes as f64 / st.src_bytes.max(1) as f64,
                    st.secs,
                    if st.skipped {
                        " · уже готов"
                    } else {
                        ""
                    }
                );
                ev += st.events;
                sb += st.src_bytes;
                pb += st.prep_bytes;
                cpu += st.secs;
            }
            Err(e) => {
                println!("{}: ОШИБКА {e}", sources[i].display());
                fails += 1;
            }
        }
    }
    println!(
        "итого: файлов {} · строк {ev} · бинлоги {sb} Б · подготовлено {pb} Б ({:.3}) · стена {:.1} с · сумма по потокам {cpu:.1} с · ошибок {fails}",
        sources.len(),
        pb as f64 / sb.max(1) as f64,
        started.elapsed().as_secs_f64()
    );
    anyhow::ensure!(fails == 0, "подготовка: ошибок {fails}");
    Ok(())
}

#[cfg(test)]
mod tests;
