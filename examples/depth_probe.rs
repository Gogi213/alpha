//! Черновой офлайн-замер: как далеко от середины лежат записываемые уровни.
//!
//! Читает настоящий бинлог, восстанавливает книгу по тем же флагам, которыми её
//! писал коллектор, и раскладывает записи изменений по расстоянию до середины
//! (bps). Отвечает на вопрос «пишем ли мы лишнее»: подписка даёт `orderbook.50`,
//! то есть 50 уровней на сторону, а разметка уровней работает с окном 25 bps.
//!
//! Запуск: `cargo run --release --example depth_probe -- <файл.binlog>`

#![allow(clippy::indexing_slicing, clippy::cast_precision_loss)]

use std::collections::BTreeMap;
use std::env;
use std::fs::File;

use alpha::binlog::Reader;
use hftbacktest::types::{
    LOCAL_ASK_DEPTH_EVENT, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_EVENT,
    LOCAL_BID_DEPTH_SNAPSHOT_EVENT, LOCAL_BUY_TRADE_EVENT, LOCAL_SELL_TRADE_EVENT,
};

const BOUNDS: [f64; 7] = [1.0, 2.5, 5.0, 10.0, 25.0, 50.0, 100.0];
const LABELS: [&str; 8] = [
    "0–1 bps",
    "1–2.5",
    "2.5–5",
    "5–10",
    "10–25",
    "25–50",
    "50–100",
    "100+ bps",
];

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = env::args()
        .nth(1)
        .expect("usage: depth_probe <file.binlog>");
    let mut reader = Reader::open(File::open(&path)?)?;
    let header = reader.header();

    let mut bids: BTreeMap<i64, i64> = BTreeMap::new();
    let mut asks: BTreeMap<i64, i64> = BTreeMap::new();
    let mut buckets = [0u64; 8];
    let (mut book, mut trades, mut other, mut no_book) = (0u64, 0u64, 0u64, 0u64);
    let mut in_snapshot = false;
    let mut frames = 0u64;
    let mut peak_levels = 0usize;
    let mut samples: Vec<(f64, usize)> = Vec::new();

    while let Some(frame) = reader.read_frame()? {
        frames += 1;
        for rec in &frame {
            match rec.ev {
                LOCAL_BUY_TRADE_EVENT | LOCAL_SELL_TRADE_EVENT => {
                    trades += 1;
                    continue;
                }
                LOCAL_BID_DEPTH_EVENT
                | LOCAL_BID_DEPTH_SNAPSHOT_EVENT
                | LOCAL_ASK_DEPTH_EVENT
                | LOCAL_ASK_DEPTH_SNAPSHOT_EVENT => {}
                _ => {
                    other += 1;
                    continue;
                }
            }
            let is_bid = matches!(
                rec.ev,
                LOCAL_BID_DEPTH_EVENT | LOCAL_BID_DEPTH_SNAPSHOT_EVENT
            );
            let is_snapshot = matches!(
                rec.ev,
                LOCAL_BID_DEPTH_SNAPSHOT_EVENT | LOCAL_ASK_DEPTH_SNAPSHOT_EVENT
            );
            if is_snapshot && !in_snapshot {
                bids.clear();
                asks.clear();
                in_snapshot = true;
            } else if !is_snapshot {
                in_snapshot = false;
            }
            let side = if is_bid { &mut bids } else { &mut asks };
            if rec.qty_lots == 0 {
                side.remove(&rec.price_ticks);
            } else {
                side.insert(rec.price_ticks, rec.qty_lots);
            }
            book += 1;

            let (Some((&best_bid, _)), Some((&best_ask, _))) =
                (bids.iter().next_back(), asks.iter().next())
            else {
                no_book += 1;
                continue;
            };
            let mid = (best_bid as f64 + best_ask as f64) / 2.0;
            if mid <= 0.0 {
                no_book += 1;
                continue;
            }
            let dist_bps = ((rec.price_ticks as f64 - mid) / mid).abs() * 1e4;
            let idx = BOUNDS
                .iter()
                .position(|b| dist_bps < *b)
                .unwrap_or(BOUNDS.len());
            buckets[idx] += 1;
            peak_levels = peak_levels.max(bids.len() + asks.len());
        }
        if frames.is_multiple_of(50) {
            if let (Some((&bb, _)), Some((&ba, _))) = (bids.iter().next_back(), asks.iter().next())
            {
                let mid = (bb as f64 + ba as f64) / 2.0;
                if mid > 0.0 {
                    let deepest = bids
                        .keys()
                        .chain(asks.keys())
                        .map(|p| ((*p as f64 - mid) / mid).abs() * 1e4)
                        .fold(0.0f64, f64::max);
                    samples.push((deepest, bids.len() + asks.len()));
                }
            }
        }
    }

    let counted: u64 = buckets.iter().sum();
    println!("file: {path}");
    println!(
        "frames={frames}  записей изменения книги={book}  сделок={trades}  прочих={other}  без книги={no_book}"
    );
    println!(
        "тик={} шаг={}  максимум уровней в книге={peak_levels}",
        header.tick_e9, header.step_e9
    );
    if !samples.is_empty() {
        let mut extents: Vec<f64> = samples.iter().map(|s| s.0).collect();
        extents.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let median = extents[extents.len() / 2];
        let p90 = extents[(extents.len() as f64 * 0.9) as usize];
        let levels_median = {
            let mut lv: Vec<usize> = samples.iter().map(|s| s.1).collect();
            lv.sort_unstable();
            lv[lv.len() / 2]
        };
        println!(
            "глубина книги ({} замеров): медиана {median:.1} bps, p90 {p90:.1} bps, максимум {:.1} bps; уровней медиана {levels_median}",
            samples.len(),
            extents[extents.len() - 1]
        );
    }
    println!(
        "{:<10} {:>12} {:>8} {:>10}",
        "расстояние", "записей", "доля", "накопл."
    );
    let mut cum = 0u64;
    for (i, label) in LABELS.iter().enumerate() {
        cum += buckets[i];
        println!(
            "{label:<10} {:>12} {:>7.2}% {:>9.2}%",
            buckets[i],
            100.0 * buckets[i] as f64 / counted.max(1) as f64,
            100.0 * cum as f64 / counted.max(1) as f64
        );
    }
    for cut in [25.0, 50.0, 100.0] {
        let keep: u64 = buckets
            .iter()
            .enumerate()
            .filter(|(i, _)| BOUNDS.get(*i).copied().unwrap_or(f64::INFINITY) <= cut)
            .map(|(_, v)| *v)
            .sum();
        println!(
            "если писать только ближе {cut:.0} bps — осталось бы {:.2} % записей книги",
            100.0 * keep as f64 / counted.max(1) as f64
        );
    }
    Ok(())
}
