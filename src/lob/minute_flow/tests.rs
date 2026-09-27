use super::*;

const T0: i64 = 1_700_000_040_000; // кратно минуте

fn trade(ts: i64, tick: i64, lots: i64, buy: bool) -> TradeHit {
    TradeHit {
        tick,
        lots,
        aggressor_is_buy: buy,
        block: false,
        rpi: false,
        exch_ms: ts,
    }
}

fn frame(
    mf: &mut MinuteFlow,
    ts: i64,
    bids: &[(i64, i64)],
    asks: &[(i64, i64)],
    out: &mut Vec<MinuteRow>,
) {
    mf.observe_frame(ts, bids.iter().copied(), asks.iter().copied(), false, out);
}

fn one_minute(f: impl FnOnce(&mut MinuteFlow, &mut Vec<MinuteRow>)) -> MinuteRow {
    let mut mf = MinuteFlow::new();
    let mut out = Vec::new();
    f(&mut mf, &mut out);
    mf.finish(&mut out);
    assert_eq!(out.len(), 1, "{out:?}");
    out[0]
}

#[test]
fn minute_flow_first_frame_is_baseline() {
    let r = one_minute(|mf, out| frame(mf, T0, &[(100, 5), (99, 7)], &[(101, 3)], out));
    assert_eq!(
        (r.minute_ms, r.add_lots, r.cancel_lots, r.trade_lots),
        (T0, 0, 0, 0)
    );
}

#[test]
fn minute_flow_add_and_cancel_same_price() {
    let r = one_minute(|mf, out| {
        frame(mf, T0, &[(100, 5), (99, 7)], &[(101, 3), (102, 4)], out);
        frame(mf, T0 + 1, &[(100, 8), (99, 2)], &[(101, 3), (102, 1)], out);
    });
    assert_eq!((r.add_lots, r.cancel_lots), (3, 5 + 3));
}

#[test]
fn minute_flow_decrease_first_goes_to_trades() {
    let r = one_minute(|mf, out| {
        frame(mf, T0, &[(100, 5), (99, 7)], &[(101, 10), (102, 4)], out);
        // покупатель съел 4 на аске 101, падение 6 — отмена 2
        mf.observe_trade(trade(T0 + 1, 101, 4, true), out);
        // продавец съел 9 на биде 100: больше падения — отмена 0
        mf.observe_trade(trade(T0 + 1, 100, 9, false), out);
        frame(mf, T0 + 2, &[(100, 0), (99, 7)], &[(101, 4), (102, 4)], out);
    });
    // бид 100 с размером 0 — в книге его нет, но проверим и «исчезновение»
    assert_eq!(r.trade_lots, 13);
    assert_eq!(r.cancel_lots, 2);
    assert_eq!(r.add_lots, 0);
}

#[test]
fn minute_flow_disappeared_inside_window_is_decrease() {
    let r = one_minute(|mf, out| {
        frame(
            mf,
            T0,
            &[(100, 5), (99, 7), (98, 1)],
            &[(101, 3), (103, 2)],
            out,
        );
        mf.observe_trade(trade(T0 + 1, 100, 2, false), out);
        // бид 100 исчез (выше лучшего текущего — внутри окна), бид 99 исчез в середине
        frame(mf, T0 + 2, &[(98, 1), (97, 6)], &[(101, 3), (103, 2)], out);
    });
    // 100: 5 − 2 сделки = 3; 99: 7; 97 глубже прошлого худшего 98 — не add
    assert_eq!((r.add_lots, r.cancel_lots, r.trade_lots), (0, 10, 2));
}

#[test]
fn minute_flow_left_window_is_not_cancel() {
    let r = one_minute(|mf, out| {
        frame(mf, T0, &[(100, 5), (99, 7), (98, 4)], &[(101, 3)], out);
        // новый лучший бид 101 (add 2), 98 вышел за окно (глубже 99 — не отмена)
        frame(mf, T0 + 1, &[(101, 2), (100, 5), (99, 7)], &[(102, 3)], out);
    });
    // аск: 101 исчез внутри окна (101 ≤ 102) — отмена 3; 102 новый, глубже прошлого худшего 101 — не add
    assert_eq!((r.add_lots, r.cancel_lots), (2, 3));
}

#[test]
fn minute_flow_new_price_inside_window_is_add() {
    let r = one_minute(|mf, out| {
        frame(mf, T0, &[(100, 5), (97, 7)], &[(101, 3), (105, 1)], out);
        frame(
            mf,
            T0 + 1,
            &[(100, 5), (99, 4), (97, 7)],
            &[(101, 3), (103, 6), (105, 1)],
            out,
        );
    });
    assert_eq!((r.add_lots, r.cancel_lots), (10, 0));
}

#[test]
fn minute_flow_trades_between_frames_reset_each_frame() {
    let r = one_minute(|mf, out| {
        frame(mf, T0, &[(100, 10)], &[(101, 3)], out);
        mf.observe_trade(trade(T0 + 1, 100, 4, false), out);
        frame(mf, T0 + 2, &[(100, 10)], &[(101, 3)], out);
        // сделка уже «сгорела» на прошлом кадре: падение 4 — отмена
        frame(mf, T0 + 3, &[(100, 6)], &[(101, 3)], out);
    });
    assert_eq!((r.cancel_lots, r.trade_lots), (4, 4));
}

#[test]
fn minute_flow_rpi_block_excluded() {
    let r = one_minute(|mf, out| {
        frame(mf, T0, &[(100, 10)], &[(101, 3)], out);
        let mut t = trade(T0 + 1, 100, 4, false);
        t.rpi = true;
        mf.observe_trade(t, out);
        let mut t = trade(T0 + 1, 100, 5, false);
        t.block = true;
        mf.observe_trade(t, out);
        frame(mf, T0 + 2, &[(100, 6)], &[(101, 3)], out);
    });
    assert_eq!((r.cancel_lots, r.trade_lots), (4, 0));
}

#[test]
fn minute_flow_minutes_and_reset() {
    let mut mf = MinuteFlow::new();
    let mut out = Vec::new();
    frame(&mut mf, T0, &[(100, 10)], &[(101, 3)], &mut out);
    // минута без кадра — строки нет, сделка в ней не теряет разбор, но строки не даёт
    mf.observe_trade(trade(T0 + 60_000, 100, 1, false), &mut out);
    frame(&mut mf, T0 + 120_000, &[(100, 12)], &[(101, 3)], &mut out);
    // снапшот — база без потока
    mf.observe_frame(T0 + 120_001, [(100, 1)], [(101, 50)], true, &mut out);
    mf.reset_book();
    frame(&mut mf, T0 + 120_002, &[(100, 40)], &[(101, 50)], &mut out);
    mf.finish(&mut out);
    let got: Vec<(i64, i64, i64, i64)> = out
        .iter()
        .map(|r| (r.minute_ms, r.add_lots, r.cancel_lots, r.trade_lots))
        .collect();
    assert_eq!(got, vec![(T0, 0, 0, 0), (T0 + 120_000, 2, 0, 0)]);
}
