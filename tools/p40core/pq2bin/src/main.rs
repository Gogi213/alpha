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
//!   те же разборщики, но выход — L1-файл `<ЧЧ>.l1.bin.zst` (zstd, уровень `P40_ZSTD`, умолч. 2; при `P40_L1_RAW=1` ещё и несжатый `<ЧЧ>.l1.bin`), формат и правила — `l1.rs`.
mod bybit;
mod gz;
mod l1;
mod num;

use arrow_array::{cast::AsArray, types::{Int32Type, Int64Type}, Array, ArrayRef, GenericStringArray, PrimitiveArray, RecordBatch, StringViewArray};
use arrow_schema::{DataType, Field, Schema};
use l1::L1Enc;
use num::{parse_f64, parse_f64_b};
use parquet::arrow::arrow_reader::{ArrowReaderMetadata, ArrowReaderOptions, ParquetRecordBatchReaderBuilder};
use parquet::arrow::ProjectionMask;
use parquet::basic::{Encoding, PageType};
use std::collections::hash_map::Entry;
use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

fn push_row(out: &mut Vec<u8>, et: i64, fu: i64, px: f64, qt: f64, fl: u8) {
    out.extend_from_slice(&et.to_le_bytes());
    out.extend_from_slice(&fu.to_le_bytes());
    out.extend_from_slice(&px.to_le_bytes());
    out.extend_from_slice(&qt.to_le_bytes());
    out.push(fl);
}

/// Выход L1-режима: `<outdir>/<ЧЧ>.l1.bin.zst` (zstd, уровень `P40_ZSTD`, умолч. 2) и при `P40_L1_RAW=1` — несжатый `<ЧЧ>.l1.bin`.
struct L1Out {
    zstd_level: i32,
    raw: bool,
}

impl L1Out {
    fn from_env() -> Self {
        L1Out {
            zstd_level: std::env::var("P40_ZSTD").ok().map_or(2, |v| v.parse().unwrap()),
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

fn bybit(zip_path: &str, outdir: &str) {
    let mut hours: Vec<Vec<u8>> = (0..24).map(|_| Vec::new()).collect();
    let dropped = bybit::scan(zip_path, |h, m| {
        let out = &mut hours[h];
        bybit::rows(m, |et, fu, px, qt, fl| push_row(out, et, fu, px, qt, fl));
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
    let dropped = bybit::scan(zip_path, |h, m| {
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
        bybit::rows(m, |et, fu, px, qt, fl| enc.row(&mut out, et, fu, px, qt, fl));
    });
    if let Some(c) = cur {
        enc.end_hour(&mut out);
        files += cfg.write(outdir, c, &mut out) as usize;
    }
    eprintln!("pq2bin l1 bybit: часов {files}, сообщений вне суток {dropped}, с часом раньше текущего {back}");
}

/// Строки часа Gate futures (<dir>/<контракт>-<ГГГГММДД><ЧЧ>.csv.gz): `emit(et, fu, price, qty, flags)`.
fn gate_rows(path: &str, mult: f64, mut emit: impl FnMut(i64, i64, f64, f64, u8)) {
    let mut rd = std::io::Cursor::new(gz::gunzip_all(&std::fs::read(path).unwrap()));
    let mut lv: HashMap<u64, f64, std::hash::BuildHasherDefault<l1::Fx>> = HashMap::default();
    let (mut grp, mut last_ts) = (-1i64, f64::NAN);
    num::each_line(&mut rd, |l| {
        let mut l = l;
        while let [rest @ .., b'\r'] = l {
            l = rest;
        }
        let mut f = l.split(|&c| c == b',');
        let ts = parse_f64_b(f.next().unwrap());
        let ev = f.next().unwrap();
        let px = parse_f64_b(f.next().unwrap());
        let sz = parse_f64_b(f.next().unwrap());
        let et = (ts * 1000.0).round() as i64;
        let key = px.to_bits();
        if ev == b"set" {
            lv.insert(key, sz);
            emit(et, -1, px, sz.abs() * mult, 1 | (((sz > 0.0) as u8) << 1));
            return;
        }
        if ts != last_ts {
            grp += 1;
            last_ts = ts;
        }
        // один поиск по ключу вместо get + insert/remove
        let slot = lv.entry(key);
        let old = match &slot {
            Entry::Occupied(e) => *e.get(),
            Entry::Vacant(_) => 0.0,
        };
        let mut new = if ev == b"make" { old + sz } else { old - sz };
        if new.abs() < 1e-12 {
            new = 0.0;
        }
        if old != 0.0 && new != 0.0 && (old > 0.0) != (new > 0.0) {
            emit(et, grp, px, 0.0, ((old > 0.0) as u8) << 1);
        }
        if new == 0.0 {
            if let Entry::Occupied(e) = slot {
                e.remove();
            }
            if old != 0.0 {
                emit(et, grp, px, 0.0, ((old > 0.0) as u8) << 1);
            }
        } else {
            match slot {
                Entry::Occupied(mut e) => {
                    e.insert(new);
                }
                Entry::Vacant(v) => {
                    v.insert(new);
                }
            }
            emit(et, grp, px, new.abs() * mult, ((new > 0.0) as u8) << 1);
        }
    });
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

/// Сжатая страница словаря колонки не больше этого размера — колонка читается как Dictionary (граница между price ≤ 6 КБ и quantity ≈ 400 КБ у CHFT).
const DICT_PAGE_MAX: i64 = 64 * 1024;

/// Колонки `dict`, закодированные в файле словарём (первая группа строк: RLE_DICTIONARY/PLAIN_DICTIONARY), читаются как Dictionary(Int32, Utf8):
/// строки значений не раскрываются на каждую строку, разбор — по записи словаря. Простая (plain) колонка словарём не читается: arrow строил бы словарь заново (≈30 % кадров).
fn read_iter(path: &str, names: &[&str], dict: &[&str]) -> impl Iterator<Item = RecordBatch> {
    let b0 = ParquetRecordBatchReaderBuilder::try_new(std::fs::File::open(path).unwrap()).unwrap();
    let idx: Vec<usize> = names.iter().map(|n| b0.parquet_schema().columns().iter().position(|c| c.name() == *n).unwrap()).collect();
    let mask = ProjectionMask::roots(b0.parquet_schema(), idx);
    let encoded: Vec<&str> = dict
        .iter()
        .copied()
        .filter(|n| {
            let Some(i) = b0.parquet_schema().columns().iter().position(|c| c.name() == *n) else { return false };
            if b0.metadata().num_row_groups() == 0 {
                return false;
            }
            let c = b0.metadata().row_group(0).column(i);
            let is_dict = |e: &Encoding| matches!(e, Encoding::RLE_DICTIONARY | Encoding::PLAIN_DICTIONARY);
            // страница словаря у предела писателя (≈1 МиБ) = колонка с большим числом значений: дальше писатель откатывается на plain, и arrow строил бы словарь заново
            // (CHFT: quantity ≈ 400 КБ сжатой страницы словаря, price ≤ 6 КБ, event_type/side < 100 Б)
            if !c.dictionary_page_offset().is_some_and(|d| c.data_page_offset() - d < DICT_PAGE_MAX) {
                return false;
            }
            match c.page_encoding_stats() {
                // есть статистика страниц: словарь, только если все страницы данных словарные (при откате на plain arrow строил бы словарь заново)
                Some(st) => {
                    let data = || st.iter().filter(|s| matches!(s.page_type, PageType::DATA_PAGE | PageType::DATA_PAGE_V2));
                    data().any(|s| is_dict(&s.encoding)) && data().all(|s| is_dict(&s.encoding))
                }
                None => c.encodings().iter().any(is_dict),
            }
        })
        .collect();
    // остальные колонки списка `dict` (plain / откат писателя) — Utf8View: строки ≤ 12 байт лежат в самом представлении, без словаря и без копии (P40_NOVIEW=1 — как раньше)
    let no_view = std::env::var_os("P40_NOVIEW").is_some();
    let view: Vec<&str> = if no_view { Vec::new() } else { dict.iter().copied().filter(|n| !encoded.contains(n) && b0.parquet_schema().columns().iter().any(|c| c.name() == *n)).collect() };
    let dict = &encoded[..];
    let b = if dict.is_empty() && view.is_empty() {
        b0
    } else {
        let fields: Vec<Arc<Field>> = b0
            .schema()
            .fields()
            .iter()
            .map(|f| {
                if dict.contains(&f.name().as_str()) {
                    let t = DataType::Dictionary(Box::new(DataType::Int32), Box::new(DataType::Utf8));
                    Arc::new(Field::new(f.name(), t, f.is_nullable()).with_metadata(f.metadata().clone()))
                } else if view.contains(&f.name().as_str()) {
                    Arc::new(Field::new(f.name(), DataType::Utf8View, f.is_nullable()).with_metadata(f.metadata().clone()))
                } else {
                    f.clone()
                }
            })
            .collect();
        let meta = ArrowReaderMetadata::try_new(b0.metadata().clone(), ArrowReaderOptions::new().with_schema(Arc::new(Schema::new(fields)))).unwrap();
        ParquetRecordBatchReaderBuilder::new_with_metadata(std::fs::File::open(path).unwrap(), meta)
    };
    // P40_BATCH: строк в пачке arrow (умолчание 65536, как раньше); меньше — пачка влезает в кэш L2
    let batch = std::env::var("P40_BATCH").ok().and_then(|v| v.parse::<usize>().ok()).filter(|&v| v > 0).unwrap_or(65536);
    b.with_projection(mask).with_batch_size(batch).build().unwrap().map(|r| r.unwrap())
}

fn read(path: &str, names: &[&str]) -> Vec<RecordBatch> {
    read_iter(path, names, &[]).collect()
}

fn is_snapshot(s: &str) -> u8 {
    (s == "snapshot") as u8
}

fn is_bid(s: &str) -> u8 {
    (s == "bid") as u8
}

/// Строковая колонка батча: Dictionary(Int32, Utf8) — значения словаря разобраны заранее; Utf8 — разбор на строке. Null -> значение `nul`.
enum Col<'a, T> {
    Dict(&'a PrimitiveArray<Int32Type>, Vec<T>),
    Str(&'a GenericStringArray<i32>, fn(&str) -> T),
    View(&'a StringViewArray, fn(&str) -> T),
}

impl<T: Copy> Col<'_, T> {
    fn get(&self, i: usize, nul: T) -> T {
        match self {
            Col::Dict(k, v) => {
                if k.is_null(i) {
                    nul
                } else {
                    v[k.value(i) as usize]
                }
            }
            Col::Str(s, f) => {
                if s.is_null(i) {
                    nul
                } else {
                    f(s.value(i))
                }
            }
            Col::View(s, f) => {
                if s.is_null(i) {
                    nul
                } else {
                    f(s.value(i))
                }
            }
        }
    }

    fn null_count(&self) -> usize {
        match self {
            Col::Dict(k, _) => k.null_count(),
            Col::Str(s, _) => s.null_count(),
            Col::View(s, _) => s.null_count(),
        }
    }
}

fn col<T: Copy>(a: &ArrayRef, f: fn(&str) -> T) -> Col<'_, T> {
    if matches!(a.data_type(), DataType::Dictionary(..)) {
        let d = a.as_dictionary::<Int32Type>();
        Col::Dict(d.keys(), d.values().as_string::<i32>().iter().map(|v| f(v.unwrap_or(""))).collect())
    } else if matches!(a.data_type(), DataType::Utf8View) {
        Col::View(a.as_string_view(), f)
    } else {
        Col::Str(a.as_string::<i32>(), f)
    }
}

/// Строки книги часа CHFT parquet: `emit(et, fu, price, qty, flags)`, порядок как в файле.
fn book_rows(path: &str, mut emit: impl FnMut(i64, i64, f64, f64, u8)) {
    for rb in read_iter(path, &["event_time", "event_type", "final_update_id", "side", "price", "quantity"], &["event_type", "side", "price", "quantity"]) {
        let et = rb.column_by_name("event_time").unwrap().as_primitive::<Int64Type>();
        let fu = rb.column_by_name("final_update_id").unwrap().as_primitive::<Int64Type>();
        // колонка — словарь (значение разбирается по записи словаря) или обычные строки (разбор на каждую строку)
        let (ev, sd) = (col(rb.column_by_name("event_type").unwrap(), is_snapshot), col(rb.column_by_name("side").unwrap(), is_bid));
        let (px, qt) = (col(rb.column_by_name("price").unwrap(), parse_f64), col(rb.column_by_name("quantity").unwrap(), parse_f64));
        assert!(px.null_count() == 0 && qt.null_count() == 0, "price/quantity null");
        for i in 0..rb.num_rows() {
            let f = if fu.is_null(i) { -1 } else { fu.value(i) };
            emit(et.value(i), f, px.get(i, 0.0), qt.get(i, 0.0), ev.get(i, 0) | (sd.get(i, 0) << 1));
        }
    }
}

fn book(path: &str) -> Vec<u8> {
    let mut out = Vec::new();
    book_rows(path, |et, fu, px, qt, fl| push_row(&mut out, et, fu, px, qt, fl));
    out
}

/// Энкодер книги CHFT: `P40_L1_SNAPSEQ=1` — источник шлёт полные снимки подряд (Hyperliquid), каждый заменяет книгу (TK-522).
fn book_enc(enc: L1Enc) -> L1Enc {
    if std::env::var("P40_L1_SNAPSEQ").is_ok_and(|v| v == "1") {
        enc.snap_seq()
    } else {
        enc
    }
}

fn book_l1(path: &str, cfg: &L1Out, outdir: &str, h: usize) -> bool {
    let (mut enc, mut out) = (book_enc(L1Enc::new()), Vec::new());
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
            rows.push((et.value(i), parse_f64(px.value(i)), parse_f64(qt.value(i)), bm.value(i) as u8));
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

/// TK-522 (E-17): источник без снимка в начале часа (Bitget режет файлы по received_time, час начинается с дельты) — один энкодер на сутки:
/// книга и L1 переходят через часы, час начинается снимком текущего L1. Часы идут по порядку (один поток на сутки), `P40_L1_CARRY=1`.
fn day_chain(root: &str, file: &str, outdir: &str, tag: &str) {
    std::fs::create_dir_all(outdir).unwrap();
    let cfg = L1Out::from_env();
    let (mut enc, mut out, mut done) = (book_enc(L1Enc::carry()), Vec::new(), 0usize);
    for h in 0..24 {
        let p = format!("{root}/{h:02}/{file}");
        if !std::path::Path::new(&p).exists() {
            continue;
        }
        book_rows(&p, |et, fu, px, qt, fl| enc.row(&mut out, et, fu, px, qt, fl));
        enc.end_hour(&mut out);
        if cfg.write(outdir, h, &mut out) {
            done += 1;
        }
    }
    eprintln!("pq2bin {tag}: часов {done}");
}

/// Сутки CHFT: 24 часовых файла `<root>/<ЧЧ>/<файл>` на `nth` потоках -> `<outdir>/<ЧЧ>.bin` (или `.l1.bin.zst` для `l1 book`).
fn day_par(root: &str, file: &str, outdir: &str, nth: usize, mode: Mode, tag: &str) {
    std::fs::create_dir_all(outdir).unwrap();
    if matches!(mode, Mode::BookL1) && std::env::var("P40_L1_CARRY").is_ok_and(|v| v == "1") {
        return day_chain(root, file, outdir, tag);
    }
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

/// TK-516 R7: glibc по умолчанию возвращает память ядру на каждом освобождении крупных буферов (trim/brk) и снова обнуляет страницы —
/// на bn/okx это ≈ 7 % циклов в ядре; не отдаём хвост кучи (`P40_MALLOC=0` — прежнее поведение).
#[cfg(all(target_os = "linux", target_env = "gnu"))]
fn tune_malloc() {
    extern "C" {
        fn mallopt(param: i32, value: i32) -> i32;
    }
    if std::env::var("P40_MALLOC").is_ok_and(|v| v == "0") {
        return;
    }
    // SAFETY: mallopt вызывается один раз в начале main до потоков; M_TRIM_THRESHOLD = -1, M_TOP_PAD = -2.
    unsafe {
        mallopt(-1, i32::MAX);
        mallopt(-2, 256 << 20);
    }
}
#[cfg(not(all(target_os = "linux", target_env = "gnu")))]
fn tune_malloc() {}

fn main() {
    tune_malloc();
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
