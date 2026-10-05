//! Тесты `lob validate` (срез 1, время): синтетика с дырой, откатом и скачком задержки.

use super::*;

const T0: i64 = 1_790_000_000 * NS_PER_SEC - (1_790_000_000 % 86_400) * NS_PER_SEC;

fn push(acc: &mut TimeAcc, s: i64, lat_ms: i64) {
    acc.push(
        T0 + s * NS_PER_SEC,
        T0 + s * NS_PER_SEC + lat_ms * NS_PER_MS,
    );
}

#[test]
fn silent_minutes_gap_backward_and_latency() {
    let mut acc = TimeAcc::default();
    // 00:00–00:09:50 — событие каждые 10 с (задержка 5 мс), дыра до 00:35, затем ещё 10 мин
    for i in 0..60 {
        push(&mut acc, i * 10, 5);
    }
    push(&mut acc, 2_100, 5);
    // откат на 3 с и задержка 300 мс
    push(&mut acc, 2_100 - 3, 300);
    for i in 1..60 {
        push(&mut acc, 2_100 + i * 10, 5);
    }
    let hours = acc.hour_lines("ABCUSDT", "2026-09-26", "ok");
    assert_eq!(hours.len(), 1);
    let header = TimeAcc::hours_csv_header();
    assert!(
        !header.contains(['\n', ' ']),
        "заголовок hours.csv — одна строка без пробелов"
    );
    let f: Vec<&str> = hours[0].split(',').collect();
    assert_eq!(f[3], "121"); // events
    assert_eq!(f[4], "24"); // silent minutes: 10..=33
    assert_eq!(f[5], "1510.000"); // longest gap, с (от 590 до 2100)
    assert_eq!((f[6], f[7]), ("1", "1")); // >1м, >10м
    assert_eq!(f[8], "1"); // backward steps
    assert_eq!(f[9], "3000.000"); // max_backward_ms
    assert_eq!(f[13], "5.000"); // lat_min
    assert_eq!(f[14], "300.000"); // lat_max
    let mins = acc.minute_lines("ABCUSDT", "2026-09-26", 0.01);
    assert_eq!(mins.iter().filter(|l| l.contains(",silent")).count(), 24);
    assert!(mins.iter().any(|l| l.contains("ts_backward")));
}

#[test]
fn rate_anomaly_needs_busy_coin() {
    let mut busy = TimeAcc::default();
    for m in 0..60 {
        if m == 30 {
            continue;
        }
        for k in 0..50 {
            push(&mut busy, m * 60 + k, 1);
        }
    }
    assert!(busy.silent_is_anomaly(0.01));
    let mut quiet = TimeAcc::default();
    for m in 0..60 {
        if m % 2 == 0 {
            push(&mut quiet, m * 60, 1);
        }
    }
    assert!(!quiet.silent_is_anomaly(0.01));
}

/// Срез 2: книга и сделки считаются тем же кодом, что `verify --keep-going`; здесь — раскладка по
/// часам и минутам и равенство итогов.
#[test]
fn book_counts_match_keep_going_and_land_in_the_right_minute() {
    use crate::bybit::verify::verify_file_keep_going;
    use crate::commands::lob::test_support::{delta_frame, snap_frame, write_day_part};
    let dir = tempfile::tempdir().unwrap();
    write_day_part(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        1,
        &[
            snap_frame(0, &[(98, 10), (100, 10)], &[(105, 10)]),
            delta_frame(1000, &[(106, 5)], &[]),
            delta_frame(2000, &[(106, 0)], &[]),
            delta_frame(3000, &[(99, 4)], &[]),
            snap_frame(4000, &[(98, 10)], &[(105, 10)]),
            delta_frame(5000, &[(97, 1)], &[]),
        ],
    );
    let path = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-08", 1);
    let (_, kg) = verify_file_keep_going(&path).unwrap();
    let v = validate_file(&path, 0.01, None);
    assert_eq!(v.status, "ok");
    let h: Vec<&str> = v.hours[0].split(',').collect();
    let col = |name: &str| {
        let i = TimeAcc::hours_csv_header()
            .replace("\\n", "")
            .split(',')
            .position(|c| c.trim() == name)
            .unwrap();
        h[i].to_string()
    };
    assert_eq!(col("book_updates"), kg.updates.to_string());
    assert_eq!(col("book_broken_updates"), kg.broken_updates.to_string());
    assert_eq!(col("book_broken_ms"), kg.broken_ms.to_string());
    assert_eq!(col("crossed"), kg.counts[0].to_string());
    assert!(v.minutes.iter().any(|l| l.contains("book_broken")));
}

/// Раздатчик: 1 читатель + N обработчиков дают те же строки в том же порядке, что один поток.
#[test]
fn threaded_dispatch_equals_sequential() {
    use crate::commands::lob::test_support::{delta_frame, snap_frame, write_day_part};
    let dir = tempfile::tempdir().unwrap();
    let mut paths = Vec::new();
    for (k, sym) in ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT", "EEEUSDT"]
        .iter()
        .enumerate()
    {
        write_day_part(
            dir.path(),
            sym,
            "2026-09-08",
            1,
            &[
                snap_frame(0, &[(98, 10), (100, 10)], &[(105, 10)]),
                delta_frame(1000 + k as i64, &[(106, 5)], &[]),
                delta_frame(61_000, &[(99, 4)], &[]),
            ],
        );
        paths.push(crate::commands::record::day_file_path(
            dir.path(),
            sym,
            "2026-09-08",
            1,
        ));
    }
    let collect = |threads: usize| {
        let mut out = Vec::new();
        process_all(&paths, 0.01, threads, 2, false, |i, v| {
            out.push((i, v.hours.clone(), v.minutes.clone(), v.status.clone()));
            Ok(())
        })
        .unwrap();
        out
    };
    let seq = collect(1);
    assert_eq!(seq.len(), 5);
    assert_eq!(seq, collect(4));
}

/// C1–C3: дубль дельты, снимок посреди потока — чистый и с дрейфом.
#[test]
fn duplicate_group_and_snapshot_drift() {
    use crate::commands::lob::test_support::{delta_frame, snap_frame, write_day_part};
    let dir = tempfile::tempdir().unwrap();
    write_day_part(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        1,
        &[
            snap_frame(0, &[(98, 10), (100, 10)], &[(105, 10)]),
            delta_frame(1000, &[(99, 4)], &[]),
            delta_frame(1500, &[(99, 4)], &[]),
            snap_frame(2000, &[(98, 10), (99, 4), (100, 10)], &[(105, 10)]),
            snap_frame(3000, &[(98, 10), (100, 7)], &[(105, 10)]),
        ],
    );
    let path = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-08", 1);
    let v = validate_file(&path, 0.01, None);
    assert_eq!(v.status, "ok");
    let h: Vec<&str> = v.hours[0].split(',').collect();
    let col = |name: &str| {
        let i = TimeAcc::hours_csv_header()
            .split(',')
            .position(|c| c.trim() == name)
            .unwrap();
        h[i].to_string()
    };
    assert_eq!(col("snapshots"), "3");
    assert_eq!(col("dup_groups"), "1");
    // чистый снимок (2000) — без дрейфа; снимок 3000 (глубина 2): тики 98, 99 и 100 расходятся → 3
    assert_eq!(col("snapshot_drift"), "1");
    assert_eq!(col("snapshot_drift_levels"), "3");
    assert!(v.minutes.iter().any(|l| l.contains("snap_drift")));
}

/// D1: книга с конца дня D сверяется с первым снимком D+1 (цепочка одной монеты).
#[test]
fn carry_compares_end_of_day_with_next_first_snapshot() {
    use crate::commands::lob::test_support::{delta_frame, snap_frame, write_day_part};
    let dir = tempfile::tempdir().unwrap();
    write_day_part(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        1,
        &[
            snap_frame(0, &[(98, 10), (100, 10)], &[(105, 10)]),
            delta_frame(1000, &[(99, 4)], &[]),
        ],
    );
    write_day_part(
        dir.path(),
        "SOLUSDT",
        "2026-09-09",
        1,
        &[snap_frame(
            86_400_000,
            &[(98, 10), (99, 4), (100, 7)],
            &[(105, 10)],
        )],
    );
    let paths: Vec<_> = ["2026-09-08", "2026-09-09"]
        .iter()
        .map(|d| crate::commands::record::day_file_path(dir.path(), "SOLUSDT", d, 1))
        .collect();
    for threads in [1, 2] {
        let mut got = Vec::new();
        process_all(&paths, 0.01, threads, 2, true, |i, v| {
            got.push((i, v.carry.as_ref().map(|c| (c.first_kind, c.mismatch))));
            Ok(())
        })
        .unwrap();
        assert_eq!(got[0], (0, None));
        assert_eq!(got[1], (1, Some(("snapshot", Some(1)))));
    }
}
