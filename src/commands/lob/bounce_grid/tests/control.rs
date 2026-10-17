use crate::book::Side;
use crate::commands::lob::bounce_grid::cache::DayTouches;
use crate::commands::lob::bounce_grid::control::{apply, ControlStats, LevelControl};
use crate::lob::levels::{ApproachEnd, ApproachRecord};

fn ap(arm_ms: i64) -> ApproachRecord {
    ApproachRecord {
        side: Side::Bid,
        price_tick: 1000,
        approach_index: 0,
        arm_ms,
        arm_dist_bps: 10,
        level_birth_ms: 0,
        size_at_arm: 100,
        best_own_tick: 999,
        best_opp_tick: 1010,
        flow_1h_lots: 0,
        strength_e2: [-1, -1, -1],
        depth_behind_lots: 0,
        stack_levels_at_arm: 0,
        stack_span_bps_at_arm: 0,
        frontrun_lots_at_arm: 0,
        p08: None,
        r1: None,
        touch_start_ms: None,
        disarm_ms: arm_ms + 100,
        disarm_reason: ApproachEnd::PriceLeft,
    }
}

fn day_of(arms: &[i64]) -> DayTouches {
    let aps: Vec<ApproachRecord> = arms.iter().map(|&a| ap(a)).collect();
    DayTouches {
        day: "2026-03-01".to_string(),
        touches: aps
            .iter()
            .map(crate::commands::lob::backtest::touch_view_of_approach)
            .collect(),
        approaches: Some(aps),
        rets: vec![[None; 3]; arms.len()],
    }
}

/// TK-274 (Г-03): разбор режима; `random` подменяет цену на двойника и отбрасывает подход без двойника, `paired`
/// оставляет настоящие стены на тех же подходах; чужое семя в файле игнорируется; счётчики и расстояние в тиках.
#[test]
fn level_control_random_and_paired_share_the_same_approaches() {
    assert_eq!(LevelControl::parse("random:7"), Ok(LevelControl::Random(7)));
    assert_eq!(LevelControl::parse("paired:0"), Ok(LevelControl::Paired(0)));
    assert!(LevelControl::parse("random").is_err());
    assert!(LevelControl::parse("other:1").is_err());
    assert!(LevelControl::parse("random:x").is_err());

    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("twins-BTCUSDT.csv"),
        "day_utc,side,price_tick,arm_ms,seed,twin_tick,candidates\n\
         2026-03-01,bid,1000,0,7,1003,4\n\
         2026-03-01,bid,1000,5,7,,0\n\
         2026-03-01,bid,1000,0,8,990,2\n",
    )
    .unwrap();
    for (c, price) in [
        (LevelControl::Random(7), 1003),
        (LevelControl::RandomNoWall(7), 1003),
        (LevelControl::Paired(7), 1000),
    ] {
        let mut day = day_of(&[0, 5]);
        let mut st = ControlStats::default();
        apply(c, dir.path(), "BTCUSDT", &mut day, &mut st).unwrap();
        let aps = day.approaches.as_ref().unwrap();
        assert_eq!((aps.len(), day.touches.len(), day.rets.len()), (1, 1, 1));
        assert_eq!(aps[0].price_tick, price);
        assert_eq!(aps[0].size_at_arm, 100, "поля стены прежние");
        assert_eq!((st.approaches, st.built, st.not_found), (2, 1, 1));
        let row = st.csv_row("BTCUSDT", c);
        assert_eq!(row[6], "3");
        assert_eq!(row[8], "1.0000");
        assert_eq!(row[9], "0.0000");
    }

    let mut day = day_of(&[0, 9]);
    let err = apply(
        LevelControl::Random(7),
        dir.path(),
        "BTCUSDT",
        &mut day,
        &mut ControlStats::default(),
    )
    .unwrap_err();
    assert!(err.to_string().contains("нет записи двойника"), "{err}");
}

/// TK-274 (Г-03, Судья 17.10): `random:<seed>:nowall` — двойник без правил стены. Отмена по гибели стены держится на
/// пороге В-66 (`--h3-usd` → `level_floor_qty`): у `nowall` порога нет, у остальных режимов и без контроля — как задан.
#[test]
fn level_control_nowall_drops_only_the_wall_floor() {
    assert_eq!(
        LevelControl::parse("random:3:nowall"),
        Ok(LevelControl::RandomNoWall(3))
    );
    assert!(LevelControl::parse("paired:3:nowall").is_err());
    assert!(LevelControl::parse("random:3:other").is_err());
    assert!(LevelControl::parse("random:3:nowall:x").is_err());
    let floor = |c| LevelControl::wall_floor_usd(c, Some(10_000.0));
    assert_eq!(floor(None), Some(10_000.0));
    assert_eq!(floor(Some(LevelControl::Random(3))), Some(10_000.0));
    assert_eq!(floor(Some(LevelControl::Paired(3))), Some(10_000.0));
    assert_eq!(floor(Some(LevelControl::RandomNoWall(3))), None);
    assert_eq!(LevelControl::RandomNoWall(3).name(), "random-nowall");
}
