use super::*;

const S: i64 = 1_000_000_000;

fn tr(ts_s: i64, tick: i64) -> TradeTick {
    TradeTick {
        ts_ns: ts_s * S,
        tick,
    }
}

#[test]
fn depth_rounds_up_to_whole_ticks() {
    assert_eq!(depth_ticks(10_000, 5), 5);
    assert_eq!(depth_ticks(10_001, 5), 6);
    assert_eq!(depth_ticks(100, 2), 1);
    assert_eq!(depth_ticks(100, 0), 0);
}

#[test]
fn long_enters_at_the_first_trade_back_at_the_level_plus_pause() {
    let tape = [tr(1, 10_000), tr(2, 9_995), tr(3, 9_998), tr(4, 10_000)];
    let w = (0, 60 * S);
    assert_eq!(reclaim_t0(&tape, true, 10_000, 5, 0, w), Some(4 * S));
    assert_eq!(reclaim_t0(&tape, true, 10_000, 5, 2 * S, w), Some(6 * S));
}

#[test]
fn shallow_poke_or_no_return_gives_no_signal() {
    let w = (0, 60 * S);
    let shallow = [tr(1, 9_997), tr(2, 10_000)];
    assert_eq!(reclaim_t0(&shallow, true, 10_000, 5, 0, w), None);
    let no_return = [tr(1, 9_990), tr(2, 9_995)];
    assert_eq!(reclaim_t0(&no_return, true, 10_000, 5, 0, w), None);
}

#[test]
fn return_outside_the_window_is_not_a_signal() {
    let tape = [tr(1, 9_990), tr(70, 10_000)];
    assert_eq!(reclaim_t0(&tape, true, 10_000, 5, 0, (0, 60 * S)), None);
}

#[test]
fn short_is_the_mirror() {
    let tape = [tr(1, 10_000), tr(2, 10_006), tr(3, 10_000)];
    assert_eq!(
        reclaim_t0(&tape, false, 10_000, 5, 0, (0, 60 * S)),
        Some(3 * S)
    );
}

#[test]
fn the_wall_vanishing_does_not_matter_the_detector_reads_trades_only() {
    // Стена на P снята при заколе (в ленте её не видно) — возврат по сделкам всё равно даёт сигнал.
    let tape = [tr(5, 9_990), tr(9, 10_001)];
    assert_eq!(
        reclaim_t0(&tape, true, 10_000, 5, 0, (0, 60 * S)),
        Some(9 * S)
    );
}
