//! Черновой офлайн-замер: даёт ли колоночная раскладка тех же записей меньше байт,
//! чем формат v3, при том же zstd-1.
//!
//! Только чтение: открывает существующий бинлог, читает кадры, для каждого кадра
//! строит три варианта одного и того же набора записей и печатает суммы:
//!
//! * `M1`  — базовый v3 (`binlog::encode_frame_payload_v3`) + zstd-1; рядом —
//!   фактический размер тела кадра на диске как проверка кодировщика;
//! * `M1c` — колоночно одним блоком: поток заголовков групп, затем вся колонка цен,
//!   затем вся колонка размеров; один вызов zstd-1 на кадр;
//! * `M1d` — то же, но каждая колонка сжимается отдельно (как делают колоночные
//!   форматы): три вызова zstd-1 на кадр, сумма размеров.
//!
//! Round-trip обязателен: колоночный буфер декодируется обратно и сверяется с
//! исходными записями — иначе «экономия» может оказаться потерей данных.
//!
//! Запуск: `cargo run --release --example columnar_probe -- <файл.binlog> [кадров]`

#![allow(
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_possible_wrap
)]

use std::env;
use std::fs::File;

use alpha::binlog::{encode_frame_payload_v3, Reader, Record, LEN_PREFIX};

fn put_uvarint(buf: &mut Vec<u8>, mut v: u64) {
    while v >= 0x80 {
        buf.push((v as u8) | 0x80);
        v >>= 7;
    }
    buf.push(v as u8);
}

fn put_zigzag(buf: &mut Vec<u8>, v: i64) {
    put_uvarint(buf, ((v << 1) ^ (v >> 63)) as u64);
}

struct Cursor<'a> {
    buf: &'a [u8],
    pos: usize,
}

impl<'a> Cursor<'a> {
    fn new(buf: &'a [u8]) -> Self {
        Self { buf, pos: 0 }
    }

    fn uvarint(&mut self) -> u64 {
        let mut out = 0u64;
        let mut shift = 0u32;
        loop {
            let b = self.buf[self.pos];
            self.pos += 1;
            out |= u64::from(b & 0x7f) << shift;
            if b & 0x80 == 0 {
                return out;
            }
            shift += 7;
        }
    }

    fn zigzag(&mut self) -> i64 {
        let u = self.uvarint();
        ((u >> 1) as i64) ^ -((u & 1) as i64)
    }
}

fn same_message(a: &Record, b: &Record) -> bool {
    a.ev == b.ev
        && a.exch_ts_ns == b.exch_ts_ns
        && a.local_ts_ns == b.local_ts_ns
        && a.block == b.block
}

/// Колоночное тело кадра: заголовки групп — отдельным потоком, значения — двумя
/// колонками. Семантика та же, что у v3: эпоха одна на кадр, дельты меток от
/// эпохи, дельты цены и размера — от предыдущей записи кадра.
fn encode_columnar(
    records: &[Record],
    groups: &mut Vec<u8>,
    prices: &mut Vec<u8>,
    qtys: &mut Vec<u8>,
) {
    let epoch_ns = records[0].exch_ts_ns;
    groups.extend_from_slice(&epoch_ns.to_le_bytes());
    let (mut prev_price, mut prev_qty) = (0i64, 0i64);
    let mut i = 0;
    while i < records.len() {
        let head = records[i];
        let mut end = i + 1;
        while end < records.len() && same_message(&head, &records[end]) {
            end += 1;
        }
        put_uvarint(groups, head.ev);
        put_zigzag(groups, head.exch_ts_ns.wrapping_sub(epoch_ns));
        put_zigzag(groups, head.local_ts_ns.wrapping_sub(epoch_ns));
        put_uvarint(groups, u64::from(head.block));
        put_uvarint(groups, (end - i) as u64);
        for r in &records[i..end] {
            put_zigzag(prices, r.price_ticks.wrapping_sub(prev_price));
            prev_price = r.price_ticks;
            put_zigzag(qtys, r.qty_lots.wrapping_sub(prev_qty));
            prev_qty = r.qty_lots;
        }
        i = end;
    }
}

/// Обратное чтение колоночного тела — доказательство, что оно не теряет данные.
fn decode_columnar(groups: &[u8], prices: &[u8], qtys: &[u8]) -> Option<Vec<Record>> {
    let mut g = Cursor::new(groups);
    let mut p = Cursor::new(prices);
    let mut q = Cursor::new(qtys);
    if groups.len() < 8 {
        return None;
    }
    let epoch_ns = i64::from_le_bytes(groups[0..8].try_into().ok()?);
    g.pos = 8;
    let mut out = Vec::new();
    let (mut prev_price, mut prev_qty) = (0i64, 0i64);
    while g.pos < groups.len() {
        let ev = g.uvarint();
        let exch_ts_ns = epoch_ns.wrapping_add(g.zigzag());
        let local_ts_ns = epoch_ns.wrapping_add(g.zigzag());
        let attrs = g.uvarint();
        let block = attrs & 1 != 0;
        let rpi = attrs & 2 != 0;
        let count = g.uvarint() as usize;
        for _ in 0..count {
            prev_price = prev_price.wrapping_add(p.zigzag());
            prev_qty = prev_qty.wrapping_add(q.zigzag());
            out.push(Record {
                ev,
                exch_ts_ns,
                local_ts_ns,
                price_ticks: prev_price,
                qty_lots: prev_qty,
                block,
                rpi,
            });
        }
    }
    Some(out)
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = env::args().skip(1);
    let path = args
        .next()
        .expect("usage: columnar_probe <file.binlog> [frames]");
    let max_frames: usize = args.next().map_or(usize::MAX, |v| v.parse().unwrap());

    let file_bytes = std::fs::metadata(&path)?.len() as usize;
    let mut reader = Reader::open(File::open(&path)?)?;

    let (mut frames, mut records) = (0usize, 0usize);
    let (mut disk_payload, mut v3_zstd, mut col_one, mut col_sep) =
        (0usize, 0usize, 0usize, 0usize);
    let (mut groups_raw, mut prices_raw, mut qtys_raw) = (0usize, 0usize, 0usize);
    let mut mismatches = 0usize;

    let mut v3_buf: Vec<u8> = Vec::with_capacity(64 * 1024);
    let mut groups: Vec<u8> = Vec::with_capacity(4 * 1024);
    let mut prices: Vec<u8> = Vec::with_capacity(16 * 1024);
    let mut qtys: Vec<u8> = Vec::with_capacity(16 * 1024);
    let mut blob: Vec<u8> = Vec::with_capacity(48 * 1024);

    while frames < max_frames {
        let Some(frame) = reader.read_frame()? else {
            break;
        };
        if frame.is_empty() {
            continue;
        }
        frames += 1;
        records += frame.len();
        disk_payload += reader.last_frame_bytes() - LEN_PREFIX;

        v3_buf.clear();
        encode_frame_payload_v3(&frame, &mut v3_buf);
        v3_zstd += zstd::bulk::compress(&v3_buf, 1)?.len();

        groups.clear();
        prices.clear();
        qtys.clear();
        encode_columnar(&frame, &mut groups, &mut prices, &mut qtys);
        groups_raw += groups.len();
        prices_raw += prices.len();
        qtys_raw += qtys.len();

        blob.clear();
        blob.extend_from_slice(&groups);
        blob.extend_from_slice(&prices);
        blob.extend_from_slice(&qtys);
        col_one += zstd::bulk::compress(&blob, 1)?.len();

        col_sep += zstd::bulk::compress(&groups, 1)?.len()
            + zstd::bulk::compress(&prices, 1)?.len()
            + zstd::bulk::compress(&qtys, 1)?.len();

        match decode_columnar(&groups, &prices, &qtys) {
            Some(back) if back == frame => {}
            _ => mismatches += 1,
        }
    }

    let per = |bytes: usize| bytes as f64 / records.max(1) as f64;
    let pct = |bytes: usize| 100.0 * bytes as f64 / v3_zstd.max(1) as f64;

    println!("file: {path} ({:.2} МБ)", file_bytes as f64 / 1e6);
    println!("frames={frames} records={records} round-trip расхождений={mismatches}");
    println!(
        "M1  v3+zstd1            : {:.2} МБ, {:.3} Б/запись  (тело кадров на диске {:.2} МБ)",
        v3_zstd as f64 / 1e6,
        per(v3_zstd),
        disk_payload as f64 / 1e6
    );
    println!(
        "M1c колоночно, 1 блок   : {:.2} МБ, {:.3} Б/запись, {:.1} % от v3",
        col_one as f64 / 1e6,
        per(col_one),
        pct(col_one)
    );
    println!(
        "M1d колоночно, 3 блока  : {:.2} МБ, {:.3} Б/запись, {:.1} % от v3",
        col_sep as f64 / 1e6,
        per(col_sep),
        pct(col_sep)
    );
    println!(
        "сырые байты до zstd: заголовки {:.1} %, цены {:.1} %, размеры {:.1} %",
        100.0 * groups_raw as f64 / (groups_raw + prices_raw + qtys_raw) as f64,
        100.0 * prices_raw as f64 / (groups_raw + prices_raw + qtys_raw) as f64,
        100.0 * qtys_raw as f64 / (groups_raw + prices_raw + qtys_raw) as f64
    );
    Ok(())
}
