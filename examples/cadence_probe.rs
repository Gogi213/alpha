//! Черновой замер частоты сообщений биржи в бинлоге: интервалы между сообщениями.
//!
//! Нужен, чтобы честно сравнить `orderbook.50` и `orderbook.200`: у глубоких топиков
//! Bybit публикует реже, и «больше уровней» покупается более грубой сеткой времени.
//!
//! Запуск: `cargo run --release --example cadence_probe -- <файл.binlog>`

#![allow(clippy::indexing_slicing, clippy::cast_precision_loss)]

use std::env;
use std::fs::File;

use alpha::binlog::{Reader, Record};

fn same_message(a: &Record, b: &Record) -> bool {
    a.ev == b.ev
        && a.exch_ts_ns == b.exch_ts_ns
        && a.local_ts_ns == b.local_ts_ns
        && a.block == b.block
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = env::args()
        .nth(1)
        .expect("usage: cadence_probe <file.binlog>");
    let mut reader = Reader::open(File::open(&path)?)?;
    let (mut groups, mut records) = (0u64, 0u64);
    let mut gaps_ms: Vec<f64> = Vec::new();
    let mut prev_ts: Option<i64> = None;
    let mut first_ts: Option<i64> = None;
    let mut last_ts = 0i64;

    while let Some(frame) = reader.read_frame()? {
        let mut i = 0;
        while i < frame.len() {
            let head = frame[i];
            let mut end = i + 1;
            while end < frame.len() && same_message(&head, &frame[end]) {
                end += 1;
            }
            groups += 1;
            records += (end - i) as u64;
            if let Some(prev) = prev_ts {
                let gap = (head.exch_ts_ns - prev) as f64 / 1e6;
                if gap >= 0.0 {
                    gaps_ms.push(gap);
                }
            }
            prev_ts = Some(head.exch_ts_ns);
            if first_ts.is_none() {
                first_ts = Some(head.exch_ts_ns);
            }
            last_ts = head.exch_ts_ns;
            i = end;
        }
    }

    gaps_ms.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let pick = |q: f64| gaps_ms[((gaps_ms.len() as f64 - 1.0) * q) as usize];
    let span_s = (last_ts - first_ts.unwrap_or(0)) as f64 / 1e9;
    println!("file: {path}");
    println!(
        "сообщений={groups} записей={records} (на сообщение {:.1})  окно {span_s:.0} с",
        records as f64 / groups.max(1) as f64
    );
    if !gaps_ms.is_empty() {
        println!(
            "интервал между сообщениями, мс: медиана {:.1}, p90 {:.1}, p99 {:.1}, максимум {:.1}; сообщений/с {:.0}",
            pick(0.5),
            pick(0.9),
            pick(0.99),
            pick(1.0),
            groups as f64 / span_s.max(1.0)
        );
    }
    Ok(())
}
