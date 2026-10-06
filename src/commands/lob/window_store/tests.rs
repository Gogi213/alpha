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
    let ta = ts_after_of(&full, &ranges);
    write_window_file(&path, "k1", 5_000_000_000, 1000, &ranges, &ta, &kept, &w, 3).unwrap();
    let f = read_window_file(&path, "k1").unwrap();
    assert_eq!(f.events, kept);
    assert_eq!(f.ranges, ranges);
    assert_eq!(f.n_rows, 1000);
    assert_eq!(f.h_max_ns, 5_000_000_000);
    assert!(f.windows.first_mismatch(&w).is_none());
    let sp = SparseRows {
        events: &f.events,
        orig: &f.orig,
        ends: &f.ends(),
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
    write_window_file(&path, "old", 1, 0, &[], &[], &[], &w, 3).unwrap();
    let err = read_window_file(&path, "new").err().unwrap().to_string();
    assert!(err.contains("old") && err.contains("new"), "{err}");
}

#[test]
fn bad_ranges_are_refused() {
    let w = SignalWindows::from_parts(0.01, 0.001, Vec::new());
    let path = tmp("c.alwin");
    let ev = rows(4);
    assert!(write_window_file(&path, "k", 1, 10, &[(5, 7), (6, 8)], &[0, 0], &ev, &w, 3).is_err());
    assert!(write_window_file(&path, "k", 1, 10, &[(0, 3)], &[0], &ev, &w, 3).is_err());
}

#[test]
fn build_sparse_matches_full_rows_within_horizon() {
    let full = rows(2000);
    let t0s: Vec<i64> = [50usize, 60, 700, 1500]
        .iter()
        .map(|&i| full[i].local_ts())
        .collect();
    let h = 30_000_000i64;
    let (ranges, kept, w) = build_sparse(&full, &t0s, h, 0, 0.01, 0.001);
    let whole = SignalWindows::build(full.as_slice(), &t0s, 0.01, 0.001);
    assert!(w.first_mismatch(&whole).is_none());
    assert!(ranges.windows(2).all(|p| p[0].1 <= p[1].0));
    let orig: Vec<u32> = ranges
        .iter()
        .flat_map(|&(a, b)| (a..b).map(|i| i as u32))
        .collect();
    let ta = ts_after_of(&full, &ranges);
    let mut at = 0usize;
    let ends: Vec<(usize, i64)> = ranges
        .iter()
        .zip(&ta)
        .map(|(&(a, b), &t)| {
            at += (b - a) as usize;
            (at, t)
        })
        .collect();
    let sp = SparseRows {
        events: &kept,
        orig: &orig,
        ends: &ends,
    };
    let (mut a, mut b) = (Vec::new(), Vec::new());
    for win in w.windows() {
        let until = win.t0_ns + h;
        let e = sp.expand_until(sp.skip_to(win.start), until, &mut a);
        let f = full.as_slice().expand_until(win.start, until, &mut b);
        assert_eq!(a, b);
        assert_eq!(orig[e - 1] as usize, f - 1);
    }
}

#[test]
fn build_sparse_back_margin_covers_lookback() {
    let full = rows(2000);
    let t0s = vec![full[700].local_ts()];
    let back = 20_000_000i64;
    let (ranges, kept, w) = build_sparse(&full, &t0s, 10_000_000, back, 0.01, 0.001);
    let (lo, _) = ranges[0];
    let need = full.partition_point(|e| e.local_ts() < t0s[0] - back) as u64;
    assert_eq!(lo, need);
    assert!(lo < w.windows()[0].start as u64);
    assert_eq!(
        kept.len() as u64,
        ranges.iter().map(|r| r.1 - r.0).sum::<u64>()
    );
}

#[test]
#[should_panic(expected = "за горизонтом хранилища")]
fn expand_past_interval_end_is_refused() {
    let full = rows(2000);
    let t0s = vec![full[700].local_ts()];
    let h = 30_000_000i64;
    let (ranges, kept, w) = build_sparse(&full, &t0s, h, 0, 0.01, 0.001);
    let ta = ts_after_of(&full, &ranges);
    let orig: Vec<u32> = ranges
        .iter()
        .flat_map(|&(a, b)| (a..b).map(|i| i as u32))
        .collect();
    let ends = vec![(kept.len(), ta[0])];
    let sp = SparseRows {
        events: &kept,
        orig: &orig,
        ends: &ends,
    };
    let mut out = Vec::new();
    let start = sp.skip_to(w.windows()[0].start);
    sp.expand_until(start, w.windows()[0].t0_ns + h + 5_000_000, &mut out);
}
