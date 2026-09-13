//! Тесты `lob binlog-stats`: счётчики считаются по факту содержимого файла.

use super::*;
use crate::binlog::{Header, Writer};

fn record(ev: u64, order_id: u64, ival: i64, fval: f64, local_eq_exch: bool) -> Record {
    Record {
        ev,
        exch_ts_ns: 1_000,
        local_ts_ns: if local_eq_exch { 1_000 } else { 1_500 },
        price_ticks: 100,
        qty_lots: 1,
        order_id,
        ival,
        fval,
    }
}

#[test]
fn stats_count_frames_records_event_types_and_live_fields() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("X-2026-09-13.binlog");
    let header = Header {
        tick_e9: 10_000_000,
        step_e9: 100_000_000,
        max_records_per_frame: 16,
    };
    {
        let file = std::fs::File::create(&path).unwrap();
        let mut writer = Writer::create(file, header, 3).unwrap();
        writer
            .write_frame(&[
                record(0x1, 0, 0, 0.0, true),
                record(0x1, 7, 0, 0.0, false),
                record(0x2, 0, 3, 1.5, true),
            ])
            .unwrap();
        writer.write_frame(&[record(0x2, 0, 0, 0.0, true)]).unwrap();
        writer.flush().unwrap();
    }

    let stats = run_binlog_stats(&BinlogStatsArgs { path: path.clone() }).unwrap();
    assert_eq!(stats.records, 4);
    assert_eq!(stats.frames, 2);
    assert_eq!(stats.min_records_in_frame, 1);
    assert_eq!(stats.max_records_in_frame, 3);
    assert_eq!(stats.by_ev, vec![(0x1, 2), (0x2, 2)]);
    assert_eq!(stats.nonzero_order_id, 1);
    assert_eq!(stats.nonzero_ival, 1);
    assert_eq!(stats.nonzero_fval, 1);
    assert_eq!(
        stats.local_ts_differs, 1,
        "одна запись с собственной меткой"
    );
    assert!(stats.bytes_on_disk > 0);

    let lines = summary_lines(&path, &stats);
    assert!(lines[0].contains("записей 4"), "{}", lines[0]);
    assert!(lines[1].contains("order_id 25.0 %"), "{}", lines[1]);
}
