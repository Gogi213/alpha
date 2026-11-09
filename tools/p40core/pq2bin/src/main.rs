//! TK-500: сутки parquet CHFT (24 часовых файла) -> плоские бинарники <outdir>/<ЧЧ>.bin одним процессом (замена 24 запусков to_bin.py / to_bin_trades.py).
//! Запуск: pq2bin <book|trades> <root> <файл> <outdir> [потоков=4]; вход <root>/<ЧЧ>/<файл>, часа нет — пропуск.
//! book — 33 Б LE: event_time i64, final_update_id i64 (null=-1), price f64, qty f64, flags u8 (бит0 снимок, бит1 бид); порядок строк как в файле.
//! trades — 25 Б LE: event_time i64, price f64, qty f64, flags u8 (бит0 is_buyer_maker); устойчивая сортировка по event_time.
//! bybit <zip> <outdir> — сутки Bybit ob200 (zip, jsonl: ts мс, type snapshot|delta, data.b/a [цена,кол-во] строками, u) -> 24 часовых книжных .bin (час = (ts − начало суток UTC)/1 ч);
//!   снимок: fu=-1 и бит0, дельта: fu=u; количество абсолютное, как в CHFT. Сообщение вне 24 ч суток отбрасывается (счётчик в stderr).
//! gate <dir> <контракт> <ГГГГММДД> <outdir> [множитель=1] [потоков=4] — сутки Gate futures (<dir>/<контракт>-<ГГГГММДД><ЧЧ>.csv.gz: ts с, событие, цена, размер со знаком, id, флаг):
//!   set = уровень снимка (размер>0 бид, <0 аск); make: L+=размер, take: L-=размер (гипотеза B из G0); строка книги = абсолютное |L|·множитель, знак L = сторона;
//!   fu = номер группы (одна метка ts = одно событие), event_time = round(ts·1000). Каждый час независим (в начале часа — снимок).
//! TK-515: l1 book <root> <файл> <outdir> [потоков=4] | l1 bybit <zip> <outdir> | l1 gate <dir> <контракт> <ГГГГММДД> <outdir> [множитель=1] [потоков=4] —
//!   те же разборщики, но выход — L1-файл `<ЧЧ>.l1.bin.zst` (zstd, уровень `P40_ZSTD`, умолч. 3; при `P40_L1_RAW=1` ещё и несжатый `<ЧЧ>.l1.bin`), формат и правила — `l1.rs`.
mod l1;

use arrow_array::{cast::AsArray, types::Int64Type, Array, RecordBatch};
use l1::L1Enc;
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use parquet::arrow::ProjectionMask;
use serde::Deserialize;
use std::collections::HashMap;
use std::io::{BufRead, BufReader};
use std::sync::atomic::{AtomicUsize, Ordering};

#[derive(Deserialize)]
struct BybitData<'a> {
    #[serde(borrow)]
    b: Vec<(&'a str, &'a str)>,
    #[serde(borrow)]
    a: Vec<(&'a str, &'a str)>,
    u: Option<i64>,
}

#[derive(Deserialize)]
struct BybitMsg<'a> {
    ts: i64,
    #[serde(rename = "type")]
    ty: &'a str,
    #[serde(borrow)]
    data: BybitData<'a>,
}

fn push_row(out: &mut Vec<u8>, et: i64, fu: i64, px: f64, qt: f64, fl: u8) {
    out.extend_from_slice(&et.to_le_bytes());
    out.extend_from_slice(&fu.to_le_bytes());
    out.extend_from_slice(&px.to_le_bytes());
    out.extend_from_slice(&qt.to_le_bytes());
    out.push(fl);
}

/// Выход L1-режима: `<outdir>/<ЧЧ>.l1.bin.zst` (zstd, уровень `P40_ZSTD`, умолч. 3) и при `P40_L1_RAW=1` — несжатый `<ЧЧ>.l1.bin`.
struct L1Out {
    zstd_level: i32,
    raw: bool,
}

impl L1Out {
    fn from_env() -> Self {
        L1Out {
            zstd_level: std::env::var("P40_ZSTD").ok().map_or(3, |v| v.parse().unwrap()),
            raw: std::env::var("P40_L1_RAW").is_ok_and(|v| v == "1"),
        }
    }

    /// Пишет файл часа и очищает буфер; пустой час (L1 не появился) не пишется. Возвращает, записан ли файл.
    fn write(&self, outdir: &str, h: usize, buf: &mut Vec<u8>) -> bool {
        if buf.is_empty() {
            return false;
        }
        if self.raw {
            std::fs::write(format!("{outdir}/{h:02}.l1.bin"), &buf).unwrap();
        }
        std::fs::write(format!("{outdir}/{h:02}.l1.bin.zst"), zstd::bulk::compress(buf, self.zstd_level).unwrap()).unwrap();
        buf.clear();
        true
    }
}

/// Сутки Bybit: для каждого сообщения внутри суток — `f(час, сообщение)`; возвращает число сообщений вне суток.
fn bybit_scan(zip_path: &str, mut f: impl FnMut(usize, &BybitMsg)) -> usize {
    let mut za = zip::ZipArchive::new(std::fs::File::open(zip_path).unwrap()).unwrap();
    let mut rd = BufReader::with_capacity(1 << 20, za.by_index(0).unwrap());
    let (mut line, mut day0, mut dropped) = (String::new(), None::<i64>, 0usize);
    while {
        line.clear();
        rd.read_line(&mut line).unwrap() > 0
    } {
        if line.trim().is_empty() {
            continue;
        }
        let m: BybitMsg = serde_json::from_str(&line).unwrap();
        let d0 = *day0.get_or_insert(m.ts - m.ts.rem_euclid(86_400_000));
        let h = (m.ts - d0).div_euclid(3_600_000);
        if !(0..24).contains(&h) {
            dropped += 1;
            continue;
        }
        f(h as usize, &m);
    }
    dropped
}

/// Строки книги одного сообщения Bybit: сначала бид (сторона 2), потом аск; снимок fu=-1 и бит0, дельта fu=u.
fn bybit_rows(m: &BybitMsg, mut emit: impl FnMut(i64, i64, f64, f64, u8)) {
    let snap = m.ty == "snapshot";
    let fu = if snap { -1 } else { m.data.u.unwrap() };
    for (side, lv) in [(2u8, &m.data.b), (0u8, &m.data.a)] {
        for (p, q) in lv {
            emit(m.ts, fu, f64_of(p), f64_of(q), snap as u8 | side);
        }
    }
}

fn bybit(zip_path: &str, outdir: &str) {
    let mut hours: Vec<Vec<u8>> = (0..24).map(|_| Vec::new()).collect();
    let dropped = bybit_scan(zip_path, |h, m| {
        let out = &mut hours[h];
        bybit_rows(m, |et, fu, px, qt, fl| push_row(out, et, fu, px, qt, fl));
    });
    std::fs::create_dir_all(outdir).unwrap();
    let mut n = 0;
    for (h, b) in hours.iter().enumerate() {
        if !b.is_empty() {
            std::fs::write(format!("{outdir}/{h:02}.bin"), b).unwrap();
            n += 1;
        }
    }
    eprintln!("pq2bin bybit: часов {n}, сообщений вне суток {dropped}");
}

/// L1 суток Bybit: потоково, состояние книги и L1 переходят между часами (каждый час файла начинается снимком текущего L1).
fn bybit_l1(zip_path: &str, outdir: &str) {
    std::fs::create_dir_all(outdir).unwrap();
    let cfg = L1Out::from_env();
    let (mut enc, mut out) = (L1Enc::new(), Vec::new());
    let (mut cur, mut files, mut back) = (None::<usize>, 0usize, 0usize);
    let dropped = bybit_scan(zip_path, |h, m| {
        // сообщение с часом раньше текущего (инверсия ts на границе часа) остаётся в текущем часу
        let h = match cur {
            Some(c) if h < c => {
                back += 1;
                c
            }
            _ => h,
        };
        if cur != Some(h) {
            if let Some(c) = cur {
                enc.end_hour(&mut out);
                files += cfg.write(outdir, c, &mut out) as usize;
            }
            cur = Some(h);
        }
        bybit_rows(m, |et, fu, px, qt, fl| enc.row(&mut out, et, fu, px, qt, fl));
    });
    if let Some(c) = cur {
        enc.end_hour(&mut out);
        files += cfg.write(outdir, c, &mut out) as usize;
    }
    eprintln!("pq2bin l1 bybit: часов {files}, сообщений вне суток {dropped}, с часом раньше текущего {back}");
}

/// Строки часа Gate futures (<dir>/<контракт>-<ГГГГММДД><ЧЧ>.csv.gz): `emit(et, fu, price, qty, flags)`.
fn gate_rows(path: &str, mult: f64, mut emit: impl FnMut(i64, i64, f64, f64, u8)) {
    let rd = BufReader::with_capacity(1 << 20, flate2::read::MultiGzDecoder::new(std::fs::File::open(path).unwrap()));
    let mut lv: HashMap<u64, f64> = HashMap::new();
    let (mut grp, mut last_ts) = (-1i64, f64::NAN);
    for l in rd.lines() {
        let l = l.unwrap();
        let mut f = l.split(',');
        let ts: f64 = f.next().unwrap().parse().unwrap();
        let ev = f.next().unwrap();
        let px: f64 = f.next().unwrap().parse().unwrap();
        let sz: f64 = f.next().unwrap().parse().unwrap();
        let et = (ts * 1000.0).round() as i64;
        let key = px.to_bits();
        if ev == "set" {
            lv.insert(key, sz);
            emit(et, -1, px, sz.abs() * mult, 1 | (((sz > 0.0) as u8) << 1));
            continue;
        }
        if ts != last_ts {
            grp += 1;
            last_ts = ts;
        }
        let old = lv.get(&key).copied().unwrap_or(0.0);
        let mut new = if ev == "make" { old + sz } else { old - sz };
        if new.abs() < 1e-12 {
            new = 0.0;
        }
        if old != 0.0 && new != 0.0 && (old > 0.0) != (new > 0.0) {
            emit(et, grp, px, 0.0, ((old > 0.0) as u8) << 1);
        }
        if new == 0.0 {
            lv.remove(&key);
            if old != 0.0 {
                emit(et, grp, px, 0.0, ((old > 0.0) as u8) << 1);
            }
        } else {
            lv.insert(key, new);
            emit(et, grp, px, new.abs() * mult, ((new > 0.0) as u8) << 1);
        }
    }
}

fn gate_hour(path: &str, mult: f64) -> Vec<u8> {
    let mut out = Vec::new();
    gate_rows(path, mult, |et, fu, px, qt, fl| push_row(&mut out, et, fu, px, qt, fl));
    out
}

fn gate_l1_hour(path: &str, mult: f64, cfg: &L1Out, outdir: &str, h: usize) -> bool {
    let (mut enc, mut out) = (L1Enc::new(), Vec::new());
    gate_rows(path, mult, |et, fu, px, qt, fl| enc.row(&mut out, et, fu, px, qt, fl));
    enc.end_hour(&mut out);
    cfg.write(outdir, h, &mut out)
}

fn gate(dir: &str, contract: &str, day: &str, outdir: &str, mult: f64, nth: usize, l1: bool) {
    std::fs::create_dir_all(outdir).unwrap();
    let cfg = L1Out::from_env();
    let next = AtomicUsize::new(0);
    let done = AtomicUsize::new(0);
    std::thread::scope(|s| {
        for _ in 0..nth {
            s.spawn(|| loop {
                let h = next.fetch_add(1, Ordering::Relaxed);
                if h >= 24 {
                    break;
                }
                let p = format!("{dir}/{contract}-{day}{h:02}.csv.gz");
                if !std::path::Path::new(&p).exists() {
                    continue;
                }
                if l1 {
                    if !gate_l1_hour(&p, mult, &cfg, outdir, h) {
                        continue;
                    }
                } else {
                    std::fs::write(format!("{outdir}/{h:02}.bin"), gate_hour(&p, mult)).unwrap();
                }
                done.fetch_add(1, Ordering::Relaxed);
            });
        }
    });
    eprintln!("pq2bin {}gate: часов {}", if l1 { "l1 " } else { "" }, done.load(Ordering::Relaxed));
}

fn read_iter(path: &str, names: &[&str]) -> impl Iterator<Item = RecordBatch> {
    let b = ParquetRecordBatchReaderBuilder::try_new(std::fs::File::open(path).unwrap()).unwrap();
    let idx: Vec<usize> = names.iter().map(|n| b.parquet_schema().columns().iter().position(|c| c.name() == *n).unwrap()).collect();
    let mask = ProjectionMask::roots(b.parquet_schema(), idx);
    b.with_projection(mask).with_batch_size(65536).build().unwrap().map(|r| r.unwrap())
}

fn read(path: &str, names: &[&str]) -> Vec<RecordBatch> {
    read_iter(path, names).collect()
}

fn f64_of(s: &str) -> f64 {
    s.parse().unwrap()
}

/// Строки книги часа CHFT parquet: `emit(et, fu, price, qty, flags)`, порядок как в файле.
fn book_rows(path: &str, mut emit: impl FnMut(i64, i64, f64, f64, u8)) {
    for rb in read_iter(path, &["event_time", "event_type", "final_update_id", "side", "price", "quantity"]) {
        let et = rb.column_by_name("event_time").unwrap().as_primitive::<Int64Type>();
        let fu = rb.column_by_name("final_update_id").unwrap().as_primitive::<Int64Type>();
        let ev = rb.column_by_name("event_type").unwrap().as_string::<i32>();
        let sd = rb.column_by_name("side").unwrap().as_string::<i32>();
        let px = rb.column_by_name("price").unwrap().as_string::<i32>();
        let qt = rb.column_by_name("quantity").unwrap().as_string::<i32>();
        for i in 0..rb.num_rows() {
            let f = if fu.is_null(i) { -1 } else { fu.value(i) };
            let fl = (ev.value(i) == "snapshot") as u8 | (((sd.value(i) == "bid") as u8) << 1);
            emit(et.value(i), f, f64_of(px.value(i)), f64_of(qt.value(i)), fl);
        }
    }
}

fn book(path: &str) -> Vec<u8> {
    let mut out = Vec::new();
    book_rows(path, |et, fu, px, qt, fl| push_row(&mut out, et, fu, px, qt, fl));
    out
}

fn book_l1(path: &str, cfg: &L1Out, outdir: &str, h: usize) -> bool {
    let (mut enc, mut out) = (L1Enc::new(), Vec::new());
    book_rows(path, |et, fu, px, qt, fl| enc.row(&mut out, et, fu, px, qt, fl));
    enc.end_hour(&mut out);
    cfg.write(outdir, h, &mut out)
}

fn trades(path: &str) -> Vec<u8> {
    let mut rows: Vec<(i64, f64, f64, u8)> = Vec::new();
    for rb in read(path, &["event_time", "price", "quantity", "is_buyer_maker"]) {
        let et = rb.column_by_name("event_time").unwrap().as_primitive::<Int64Type>();
        let px = rb.column_by_name("price").unwrap().as_string::<i32>();
        let qt = rb.column_by_name("quantity").unwrap().as_string::<i32>();
        let bm = rb.column_by_name("is_buyer_maker").unwrap().as_boolean();
        for i in 0..rb.num_rows() {
            rows.push((et.value(i), f64_of(px.value(i)), f64_of(qt.value(i)), bm.value(i) as u8));
        }
    }
    rows.sort_by_key(|r| r.0);
    let mut out = Vec::with_capacity(rows.len() * 25);
    for r in rows {
        out.extend_from_slice(&r.0.to_le_bytes());
        out.extend_from_slice(&r.1.to_le_bytes());
        out.extend_from_slice(&r.2.to_le_bytes());
        out.push(r.3);
    }
    out
}

#[derive(Clone, Copy, PartialEq)]
enum Mode {
    Book,
    Trades,
    BookL1,
}

/// Сутки CHFT: 24 часовых файла `<root>/<ЧЧ>/<файл>` на `nth` потоках -> `<outdir>/<ЧЧ>.bin` (или `.l1.bin.zst` для `l1 book`).
fn day_par(root: &str, file: &str, outdir: &str, nth: usize, mode: Mode, tag: &str) {
    std::fs::create_dir_all(outdir).unwrap();
    let cfg = L1Out::from_env();
    let next = AtomicUsize::new(0);
    let done = AtomicUsize::new(0);
    std::thread::scope(|s| {
        for _ in 0..nth {
            s.spawn(|| loop {
                let h = next.fetch_add(1, Ordering::Relaxed);
                if h >= 24 {
                    break;
                }
                let p = format!("{root}/{h:02}/{file}");
                if !std::path::Path::new(&p).exists() {
                    continue;
                }
                match mode {
                    Mode::Book => std::fs::write(format!("{outdir}/{h:02}.bin"), book(&p)).unwrap(),
                    Mode::Trades => std::fs::write(format!("{outdir}/{h:02}.bin"), trades(&p)).unwrap(),
                    Mode::BookL1 => {
                        if !book_l1(&p, &cfg, outdir, h) {
                            continue;
                        }
                    }
                }
                done.fetch_add(1, Ordering::Relaxed);
            });
        }
    });
    eprintln!("pq2bin {tag}: часов {}", done.load(Ordering::Relaxed));
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    match a.get(1).map(String::as_str) {
        Some("bybit") if a.len() == 4 => return bybit(&a[2], &a[3]),
        Some("gate") if a.len() >= 6 => {
            let mult = a.get(6).map_or(1.0, |s| s.parse().unwrap());
            return gate(&a[2], &a[3], &a[4], &a[5], mult, a.get(7).map_or(4, |s| s.parse().unwrap()), false);
        }
        Some("l1") => match a.get(2).map(String::as_str) {
            Some("bybit") if a.len() == 5 => return bybit_l1(&a[3], &a[4]),
            Some("gate") if a.len() >= 7 => {
                let mult = a.get(7).map_or(1.0, |s| s.parse().unwrap());
                return gate(&a[3], &a[4], &a[5], &a[6], mult, a.get(8).map_or(4, |s| s.parse().unwrap()), true);
            }
            Some("book") if a.len() >= 6 => {
                return day_par(&a[3], &a[4], &a[5], a.get(6).map_or(4, |s| s.parse().unwrap()), Mode::BookL1, "l1 book");
            }
            _ => {
                eprintln!("pq2bin l1 book <root> <файл> <outdir> [потоков=4] | l1 bybit <zip> <outdir> | l1 gate <dir> <контракт> <ГГГГММДД> <outdir> [множитель] [потоков]");
                std::process::exit(2);
            }
        },
        _ => {}
    }
    if a.len() < 5 {
        eprintln!("pq2bin <book|trades> <root> <файл> <outdir> [потоков=4] | bybit <zip> <outdir> | gate <dir> <контракт> <ГГГГММДД> <outdir> [множитель] [потоков] | l1 <book|bybit|gate> …");
        std::process::exit(2);
    }
    let mode = match a[1].as_str() {
        "book" => Mode::Book,
        "trades" => Mode::Trades,
        _ => panic!("режим book|trades"),
    };
    day_par(&a[2], &a[3], &a[4], a.get(5).map_or(4, |s| s.parse().unwrap()), mode, &a[1]);
}
