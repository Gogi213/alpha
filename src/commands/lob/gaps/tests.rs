//! Тесты `lob gaps`: синтетический бинлог с известной дырой.

use super::*;
use crate::binlog::{Header, Writer};

const T0: i64 = 1_790_000_000 * NS_PER_SEC - (1_790_000_000 % 86_400) * NS_PER_SEC; // начало суток UTC

fn rec(exch_s: i64) -> Record {
    Record {
        ev: 1,
        exch_ts_ns: T0 + exch_s * NS_PER_SEC,
        local_ts_ns: T0 + exch_s * NS_PER_SEC + 1_000,
        price_ticks: 100,
        qty_lots: 1,
        block: false,
        rpi: false,
    }
}

use crate::binlog::Record;

fn write(path: &Path, secs: &[i64]) {
    let header = Header {
        tick_e9: 1,
        step_e9: 1,
        max_records_per_frame: 64,
    };
    let mut w = Writer::create(std::fs::File::create(path).unwrap(), header, 3).unwrap();
    for chunk in secs.chunks(8) {
        let frame: Vec<Record> = chunk.iter().map(|s| rec(*s)).collect();
        w.write_frame(&frame).unwrap();
    }
    w.flush().unwrap();
}

#[test]
fn known_gap_is_found_and_counted() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("ABCUSDT-2026-09-26.binlog");
    // события каждые 30 с в первые 10 мин, дыра 2 ч 0 мин 30 с... затем ещё 10 мин
    let mut secs: Vec<i64> = (0..=20).map(|i| i * 30).collect();
    let after = 600 + 7_200;
    secs.extend((0..=20).map(|i| after + i * 30));
    write(&path, &secs);
    let row = gaps_for_file(&path, false);
    assert_eq!(row.symbol, "ABCUSDT");
    assert_eq!(row.day, "2026-09-26");
    assert_eq!(row.status, "ok");
    assert_eq!(row.records, 42);
    assert_eq!(row.longest_gap_s, 7_200.0);
    assert_eq!(row.longest_gap_start_ns, T0 + 600 * NS_PER_SEC);
    assert_eq!((row.gaps_gt_1m, row.gaps_gt_10m, row.gaps_gt_1h), (1, 1, 1));
    assert_eq!(row.hours[0], 21);
    assert_eq!(row.hours[2], 21);
    assert_eq!(row.minutes_with_events_pct, 22.0 / 1440.0 * 100.0);
    assert!((row.span_pct - (after + 600) as f64 / 864.0).abs() < 1e-9);
    assert!(csv_line(&row).starts_with("ABCUSDT,2026-09-26,"));
}

#[test]
fn sha256_matches_file_and_part_suffix_day() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("ABCUSDT-2026-09-26-p2.binlog");
    write(&path, &[0, 10, 20]);
    let row = gaps_for_file(&path, true);
    assert_eq!(row.day, "2026-09-26");
    let want = hex(&Sha256::digest(std::fs::read(&path).unwrap()));
    assert_eq!(row.sha256.as_deref(), Some(want.as_str()));
    assert_eq!(row.gaps_gt_1m, 0);
}

#[test]
fn bad_file_is_a_row_not_a_failure() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("XYZ-2026-09-26.binlog");
    std::fs::write(&path, b"garbage").unwrap();
    let row = gaps_for_file(&path, false);
    assert!(row.status.starts_with("error"));
    assert_eq!(row.records, 0);
}
