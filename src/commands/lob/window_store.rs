//! Хранилище окон ALWIN01 (TK-049): на монето-сутки — только строки внутри окон подходов (интервалы исходных
//! номеров строк), снимки книги на каждый `t0` и ключ данных. Прогон читает файл вместо бинлога/ALPREP и считает
//! круги по `SparseRows` (индексы сжаты, окно находит строку по исходному номеру — `EventRows::skip_to`).
//!
//! Раскладка: `ALWIN01\0` | key_len u32 | key | h_max_ns i64 | n_rows u64 | tick f64 | lot f64 | n_ranges u32 |
//! n_windows u32 | интервалы (lo u64, hi u64) | n_events u64 | ev_len u32 | события (5 колонок ALPREP, один кусок) |
//! win_raw u32 | win_len u32 | окна (zstd): t0 i64, start u64, ts, best_bid, best_ask, low_bid, high_ask i64,
//! nb u32, na u32, затем пары (тик i64, объём f64 битами).
//! Ключ (`key`) — строка, которую собирает подготовка (sha256 бинлога, версия D20, tick/lot); читатель при
//! несовпадении отказывает с обоими значениями — тихого обхода нет.

use std::path::Path;

use crate::lob::backtest::{CompactEvent, DepthSnapshot, EventRows, SignalWindow, SignalWindows};
use hftbacktest::types::Event;

use super::prep_events::{decode_chunk, encode_chunk, NCOL};

const MAGIC: &[u8; 8] = b"ALWIN01\0";

/// Содержимое файла суток.
pub struct WindowFile {
    pub key: String,
    /// Дальше этого горизонта (нс после `t0`) лента в интервалах не гарантирована.
    pub h_max_ns: i64,
    /// Строк в полных сутках (для сверки с эталоном).
    pub n_rows: u64,
    pub ranges: Vec<(u64, u64)>,
    pub events: Vec<CompactEvent>,
    /// Исходный номер каждой сохранённой строки.
    pub orig: Vec<u32>,
    pub windows: SignalWindows,
}

/// Вид на разреженную ленту: строки окон и их исходные номера.
pub struct SparseRows<'a> {
    pub events: &'a [CompactEvent],
    pub orig: &'a [u32],
}

impl EventRows for SparseRows<'_> {
    fn len(&self) -> usize {
        self.events.len()
    }
    fn row(&self, i: usize) -> Event {
        self.events[i].expand()
    }
    fn row_local_ts(&self, i: usize) -> i64 {
        self.events[i].local_ts()
    }
    fn row_exch_ts(&self, i: usize) -> i64 {
        self.events[i].exch_ts()
    }
    fn skip_to(&self, orig_start: usize) -> usize {
        self.orig.partition_point(|&k| (k as usize) < orig_start)
    }
}

fn put_u32(b: &mut Vec<u8>, v: u32) {
    b.extend_from_slice(&v.to_le_bytes());
}
fn put_u64(b: &mut Vec<u8>, v: u64) {
    b.extend_from_slice(&v.to_le_bytes());
}
fn put_i64(b: &mut Vec<u8>, v: i64) {
    b.extend_from_slice(&v.to_le_bytes());
}
fn put_f64(b: &mut Vec<u8>, v: f64) {
    b.extend_from_slice(&v.to_bits().to_le_bytes());
}

struct Cur<'a> {
    b: &'a [u8],
    at: usize,
}

impl<'a> Cur<'a> {
    fn take(&mut self, n: usize) -> anyhow::Result<&'a [u8]> {
        anyhow::ensure!(self.b.len() >= self.at + n, "ALWIN: обрыв файла");
        let s = &self.b[self.at..self.at + n];
        self.at += n;
        Ok(s)
    }
    fn u32(&mut self) -> anyhow::Result<u32> {
        Ok(u32::from_le_bytes(self.take(4)?.try_into()?))
    }
    fn u64(&mut self) -> anyhow::Result<u64> {
        Ok(u64::from_le_bytes(self.take(8)?.try_into()?))
    }
    fn i64(&mut self) -> anyhow::Result<i64> {
        Ok(i64::from_le_bytes(self.take(8)?.try_into()?))
    }
    fn f64(&mut self) -> anyhow::Result<f64> {
        Ok(f64::from_bits(self.u64()?))
    }
}

fn encode_windows(w: &SignalWindows) -> Vec<u8> {
    let mut b = Vec::new();
    for x in w.windows() {
        let d = &x.depth;
        put_i64(&mut b, x.t0_ns);
        put_u64(&mut b, x.start as u64);
        for v in [
            d.timestamp,
            d.best_bid_tick,
            d.best_ask_tick,
            d.low_bid_tick,
            d.high_ask_tick,
        ] {
            put_i64(&mut b, v);
        }
        put_u32(&mut b, d.bids.len() as u32);
        put_u32(&mut b, d.asks.len() as u32);
        for &(t, q) in d.bids.iter().chain(&d.asks) {
            put_i64(&mut b, t);
            put_f64(&mut b, q);
        }
    }
    b
}

fn decode_windows(raw: &[u8], n: usize) -> anyhow::Result<Vec<SignalWindow>> {
    let mut c = Cur { b: raw, at: 0 };
    let mut out = Vec::with_capacity(n);
    for _ in 0..n {
        let t0_ns = c.i64()?;
        let start = c.u64()? as usize;
        let [timestamp, best_bid_tick, best_ask_tick, low_bid_tick, high_ask_tick] =
            [c.i64()?, c.i64()?, c.i64()?, c.i64()?, c.i64()?];
        let (nb, na) = (c.u32()? as usize, c.u32()? as usize);
        let mut side = |k: usize| -> anyhow::Result<Vec<(i64, f64)>> {
            (0..k).map(|_| Ok((c.i64()?, c.f64()?))).collect()
        };
        let bids = side(nb)?;
        let asks = side(na)?;
        out.push(SignalWindow {
            t0_ns,
            start,
            depth: DepthSnapshot {
                bids,
                asks,
                best_bid_tick,
                best_ask_tick,
                low_bid_tick,
                high_ask_tick,
                timestamp,
            },
        });
    }
    anyhow::ensure!(c.at == raw.len(), "ALWIN: хвост после окон");
    Ok(out)
}

#[allow(clippy::too_many_arguments)]
/// Пишет файл суток атомарно (`.part` → rename). `events` — склейка строк интервалов `ranges` по порядку.
pub fn write_window_file(
    path: &Path,
    key: &str,
    h_max_ns: i64,
    n_rows: u64,
    ranges: &[(u64, u64)],
    events: &[CompactEvent],
    windows: &SignalWindows,
    level: i32,
) -> anyhow::Result<()> {
    let want: u64 = ranges.iter().map(|&(a, b)| b - a).sum();
    anyhow::ensure!(want == events.len() as u64, "ALWIN: строк не по интервалам");
    anyhow::ensure!(
        ranges.windows(2).all(|p| p[0].1 <= p[1].0)
            && ranges.iter().all(|&(a, b)| a < b && b <= n_rows),
        "ALWIN: интервалы не по возрастанию"
    );
    let mut b = Vec::new();
    b.extend_from_slice(MAGIC);
    put_u32(&mut b, key.len() as u32);
    b.extend_from_slice(key.as_bytes());
    put_i64(&mut b, h_max_ns);
    put_u64(&mut b, n_rows);
    put_f64(&mut b, windows.tick_size);
    put_f64(&mut b, windows.lot_size);
    put_u32(&mut b, ranges.len() as u32);
    put_u32(&mut b, windows.len() as u32);
    for &(lo, hi) in ranges {
        put_u64(&mut b, lo);
        put_u64(&mut b, hi);
    }
    let ev = if events.is_empty() {
        Vec::new()
    } else {
        encode_chunk(events, level)?
    };
    put_u64(&mut b, events.len() as u64);
    put_u32(&mut b, ev.len() as u32);
    b.extend_from_slice(&ev);
    let raw = encode_windows(windows);
    let z = zstd::bulk::compress(&raw, level)?;
    put_u32(&mut b, raw.len() as u32);
    put_u32(&mut b, z.len() as u32);
    b.extend_from_slice(&z);
    let part = path.with_extension("alwin.part");
    std::fs::write(&part, &b)?;
    std::fs::rename(&part, path)?;
    Ok(())
}

/// Читает файл суток; ключ не совпал — отказ с обоими значениями.
pub fn read_window_file(path: &Path, expect_key: &str) -> anyhow::Result<WindowFile> {
    let data = std::fs::read(path)?;
    let mut c = Cur { b: &data, at: 0 };
    anyhow::ensure!(
        c.take(8)? == MAGIC,
        "ALWIN: не тот формат: {}",
        path.display()
    );
    let klen = c.u32()? as usize;
    let key = String::from_utf8(c.take(klen)?.to_vec())?;
    anyhow::ensure!(
        key == expect_key,
        "ALWIN: ключ данных не совпал в {}: в файле «{key}», нужен «{expect_key}»",
        path.display()
    );
    let h_max_ns = c.i64()?;
    let n_rows = c.u64()?;
    let (tick, lot) = (c.f64()?, c.f64()?);
    let (n_ranges, n_windows) = (c.u32()? as usize, c.u32()? as usize);
    let ranges: Vec<(u64, u64)> = (0..n_ranges)
        .map(|_| Ok((c.u64()?, c.u64()?)))
        .collect::<anyhow::Result<_>>()?;
    let n_events = c.u64()? as usize;
    let ev_len = c.u32()? as usize;
    let ev_raw = c.take(ev_len)?;
    let mut events = Vec::with_capacity(n_events);
    if n_events > 0 {
        let mut scratch: [Vec<u8>; NCOL] = Default::default();
        decode_chunk(ev_raw, n_events, None, &mut scratch, &mut events)?;
    }
    let (raw_len, z_len) = (c.u32()? as usize, c.u32()? as usize);
    let z = c.take(z_len)?;
    let mut raw = vec![0u8; raw_len];
    let got = zstd::bulk::Decompressor::new()?.decompress_to_buffer(z, raw.as_mut_slice())?;
    anyhow::ensure!(got == raw_len, "ALWIN: длина блока окон");
    anyhow::ensure!(c.at == data.len(), "ALWIN: хвост файла");
    let windows = SignalWindows::from_parts(tick, lot, decode_windows(&raw, n_windows)?);
    let orig: Vec<u32> = ranges
        .iter()
        .flat_map(|&(lo, hi)| (lo..hi).map(|i| i as u32))
        .collect();
    anyhow::ensure!(orig.len() == events.len(), "ALWIN: строк не по интервалам");
    Ok(WindowFile {
        key,
        h_max_ns,
        n_rows,
        ranges,
        events,
        orig,
        windows,
    })
}

#[cfg(test)]
mod tests;
