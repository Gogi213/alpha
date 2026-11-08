//! TK-500: сутки parquet CHFT (24 часовых файла) -> плоские бинарники <outdir>/<ЧЧ>.bin одним процессом (замена 24 запусков to_bin.py / to_bin_trades.py).
//! Запуск: pq2bin <book|trades> <root> <файл> <outdir> [потоков=4]; вход <root>/<ЧЧ>/<файл>, часа нет — пропуск.
//! book — 33 Б LE: event_time i64, final_update_id i64 (null=-1), price f64, qty f64, flags u8 (бит0 снимок, бит1 бид); порядок строк как в файле.
//! trades — 25 Б LE: event_time i64, price f64, qty f64, flags u8 (бит0 is_buyer_maker); устойчивая сортировка по event_time.
use arrow_array::{cast::AsArray, types::Int64Type, Array, RecordBatch};
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use parquet::arrow::ProjectionMask;
use std::sync::atomic::{AtomicUsize, Ordering};

fn read(path: &str, names: &[&str]) -> Vec<RecordBatch> {
    let b = ParquetRecordBatchReaderBuilder::try_new(std::fs::File::open(path).unwrap()).unwrap();
    let idx: Vec<usize> = names.iter().map(|n| b.parquet_schema().columns().iter().position(|c| c.name() == *n).unwrap()).collect();
    let mask = ProjectionMask::roots(b.parquet_schema(), idx);
    b.with_projection(mask).with_batch_size(65536).build().unwrap().map(|r| r.unwrap()).collect()
}

fn f64_of(s: &str) -> f64 {
    s.parse().unwrap()
}

fn book(path: &str) -> Vec<u8> {
    let mut out = Vec::new();
    for rb in read(path, &["event_time", "event_type", "final_update_id", "side", "price", "quantity"]) {
        let et = rb.column_by_name("event_time").unwrap().as_primitive::<Int64Type>();
        let fu = rb.column_by_name("final_update_id").unwrap().as_primitive::<Int64Type>();
        let ev = rb.column_by_name("event_type").unwrap().as_string::<i32>();
        let sd = rb.column_by_name("side").unwrap().as_string::<i32>();
        let px = rb.column_by_name("price").unwrap().as_string::<i32>();
        let qt = rb.column_by_name("quantity").unwrap().as_string::<i32>();
        out.reserve(rb.num_rows() * 33);
        for i in 0..rb.num_rows() {
            let f = if fu.is_null(i) { -1 } else { fu.value(i) };
            let fl = (ev.value(i) == "snapshot") as u8 | (((sd.value(i) == "bid") as u8) << 1);
            out.extend_from_slice(&et.value(i).to_le_bytes());
            out.extend_from_slice(&f.to_le_bytes());
            out.extend_from_slice(&f64_of(px.value(i)).to_le_bytes());
            out.extend_from_slice(&f64_of(qt.value(i)).to_le_bytes());
            out.push(fl);
        }
    }
    out
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

fn main() {
    let a: Vec<String> = std::env::args().collect();
    if a.len() < 5 {
        eprintln!("pq2bin <book|trades> <root> <файл> <outdir> [потоков=4]");
        std::process::exit(2);
    }
    let is_book = match a[1].as_str() {
        "book" => true,
        "trades" => false,
        _ => panic!("режим book|trades"),
    };
    let nth: usize = a.get(5).map_or(4, |s| s.parse().unwrap());
    std::fs::create_dir_all(&a[4]).unwrap();
    let next = AtomicUsize::new(0);
    let done = AtomicUsize::new(0);
    std::thread::scope(|s| {
        for _ in 0..nth {
            s.spawn(|| loop {
                let h = next.fetch_add(1, Ordering::Relaxed);
                if h >= 24 {
                    break;
                }
                let p = format!("{}/{h:02}/{}", a[2], a[3]);
                if !std::path::Path::new(&p).exists() {
                    continue;
                }
                let b = if is_book { book(&p) } else { trades(&p) };
                std::fs::write(format!("{}/{h:02}.bin", a[4]), b).unwrap();
                done.fetch_add(1, Ordering::Relaxed);
            });
        }
    });
    eprintln!("pq2bin {}: часов {}", a[1], done.load(Ordering::Relaxed));
}
