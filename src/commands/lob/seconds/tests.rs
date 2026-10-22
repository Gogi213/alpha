use super::*;
use crate::commands::lob::replay_symbol;
use crate::commands::lob::test_support::{delta_frame, snap_frame, trade_frame_flags, write_day};
use crate::lob::levels::{H3Mode, LevelsConfig};
use crate::lob::markout::sample_asof;

const SYM: &str = "SOLUSDT";
const DAY: &str = "1970-01-01";

fn args(root: &std::path::Path) -> SecondsArgs {
    SecondsArgs {
        root: root.to_path_buf(),
        symbol: SYM.to_string(),
        days: Vec::new(),
        out: None,
        allow_unverified: true,
    }
}

fn frames() -> Vec<Vec<crate::binlog::Record>> {
    let b = [(9990, 2000), (9980, 3000)];
    let a = [(10010, 1000), (10020, 4000)];
    vec![
        snap_frame(0, &b, &a),
        delta_frame(100, &[(9990, 2000)], &[]),
        trade_frame_flags(500, 10010, 1000, true, false, false),
        delta_frame(1000, &[(9990, 2000)], &[]),
        delta_frame(1001, &[(9990, 2000)], &[]),
        trade_frame_flags(1500, 9990, 500, false, false, false),
        trade_frame_flags(1600, 9990, 500, false, true, false),
        trade_frame_flags(1700, 9990, 500, false, false, true),
        delta_frame(2500, &[], &[(10010, 1500)]),
        trade_frame_flags(2600, 10010, 1000, true, false, false),
        delta_frame(7200, &[(9990, 2500)], &[]),
    ]
}

fn read(path: &std::path::Path) -> Vec<Vec<i64>> {
    let mut r = csv::Reader::from_path(path).unwrap();
    assert_eq!(
        r.headers().unwrap().iter().collect::<Vec<_>>(),
        SECONDS_COLUMNS
    );
    r.records()
        .map(|x| x.unwrap().iter().map(|v| v.parse().unwrap()).collect())
        .collect()
}

#[test]
fn seconds_gate_mid_gap_flow_and_determinism() {
    let dir = tempfile::tempdir().unwrap();
    write_day(dir.path(), SYM, DAY, &frames());
    let s1 = run_seconds(&args(dir.path())).unwrap();
    let path = s1.out_dir.join(format!("{SYM}-{DAY}.csv"));
    let first = std::fs::read(&path).unwrap();
    run_seconds(&args(dir.path())).unwrap();
    assert_eq!(
        first,
        std::fs::read(&path).unwrap(),
        "гейт (а): два прогона"
    );

    let rows = read(&path);
    assert_eq!(rows.len(), DAY_SECS);
    assert_eq!(
        (s1.trades, s1.dropped),
        (3, 0),
        "блочная и RPI не считаются"
    );

    // гейт (б): mid2 / mid2_100 = sample_asof по mids реплея
    let cfg = LevelsConfig {
        mode: H3Mode::Percentile { h3_lots: 5 },
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let mids = replay_symbol(dir.path(), SYM, cfg).unwrap().days[0]
        .mids
        .clone();
    let want = |t: i64| sample_asof(&mids, t, 0).map_or(0, |s| s.bid_tick + s.ask_tick);
    for (k, r) in rows.iter().enumerate() {
        let k = k as i64;
        assert_eq!(r[0], k);
        assert_eq!(r[1], want((k + 1) * 1000), "mid2 на секунде {k}");
        assert_eq!(r[2], want(k * 1000 + 100), "mid2_100 на секунде {k}");
    }

    // gap: обновления в (0,1000], (1000,2000], (2000,3000], затем пусто до 7200
    assert_eq!(rows[2][12], 0);
    assert!((3..=6).all(|k| rows[k][12] == 1));
    assert_eq!(rows[7][12], 0);

    // гейт (в): Σ(tb+ts) = floor точных сумм (100,1+100,1 → 200; 49,95 → 49)
    let tb: i64 = rows.iter().map(|r| r[5]).sum();
    let ts: i64 = rows.iter().map(|r| r[6]).sum();
    assert_eq!((tb, ts), (200, 49));
    assert_eq!((rows[0][7], rows[1][8], rows[2][7]), (1, 1, 1));

    // OBI и глубина: 499,2 vs 500,9 → −1699 ppm, 1000,1 $ → 1000
    assert_eq!((rows[0][9], rows[0][10], rows[0][11]), (-1699, -1699, 1000));
    assert_eq!(rows[0][3], 20);
}
