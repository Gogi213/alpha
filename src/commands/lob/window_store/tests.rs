use super::*;
use crate::lob::backtest::EventKind;

fn rows(n: usize) -> Vec<CompactEvent> {
    let kinds = [
        EventKind::BidDepth,
        EventKind::AskDepth,
        EventKind::BuyTrade,
        EventKind::SellTrade,
    ];
    (0..n)
        .map(|i| {
            let i = i as i64;
            CompactEvent::new(
                kinds[(i % 4) as usize],
                1_700_000_000_000 + i,
                1_700_000_000_000_000_000 + i * 1_000_000,
                50_000_000_000 + (i % 7) * 10_000_000,
                (i % 13) * 1_000_000,
            )
        })
        .collect()
}

fn tmp(name: &str) -> std::path::PathBuf {
    let d = std::env::temp_dir().join(format!("alwin-test-{}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d.join(name)
}

#[test]
fn round_trip_and_sparse_view_match_full_day() {
    let full = rows(1000);
    let t0s: Vec<i64> = vec![full[100].local_ts(), full[600].local_ts()];
    let w = SignalWindows::build(full.as_slice(), &t0s, 0.01, 0.001);
    let ranges = vec![(100u64, 140u64), (600, 650)];
    let kept: Vec<CompactEvent> = ranges
        .iter()
        .flat_map(|&(a, b)| full[a as usize..b as usize].iter().copied())
        .collect();
    let path = tmp("a.alwin");
    write_window_file(&path, "k1", 5_000_000_000, 1000, &ranges, &kept, &w, 3).unwrap();
    let f = read_window_file(&path, "k1").unwrap();
    assert_eq!(f.events, kept);
    assert_eq!(f.ranges, ranges);
    assert_eq!(f.n_rows, 1000);
    assert_eq!(f.h_max_ns, 5_000_000_000);
    assert!(f.windows.first_mismatch(&w).is_none());
    let sp = SparseRows {
        events: &f.events,
        orig: &f.orig,
    };
    for win in f.windows.windows() {
        let i = sp.skip_to(win.start);
        let want = full.as_slice().skip_to(win.start);
        assert_eq!(sp.row_local_ts(i), full.as_slice().row_local_ts(want));
    }
}

#[test]
fn key_mismatch_is_refused_with_both_values() {
    let w = SignalWindows::from_parts(0.01, 0.001, Vec::new());
    let path = tmp("b.alwin");
    write_window_file(&path, "old", 1, 0, &[], &[], &w, 3).unwrap();
    let err = read_window_file(&path, "new").err().unwrap().to_string();
    assert!(err.contains("old") && err.contains("new"), "{err}");
}

#[test]
fn bad_ranges_are_refused() {
    let w = SignalWindows::from_parts(0.01, 0.001, Vec::new());
    let path = tmp("c.alwin");
    let ev = rows(4);
    assert!(write_window_file(&path, "k", 1, 10, &[(5, 7), (6, 8)], &ev, &w, 3).is_err());
    assert!(write_window_file(&path, "k", 1, 10, &[(0, 3)], &ev, &w, 3).is_err());
}
