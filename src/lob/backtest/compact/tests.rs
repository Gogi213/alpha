use super::*;

/// То, что строил перевод потока до Р6 (`feed.rs`), — эталон развёртки.
fn legacy(kind: EventKind, exch_ms: i64, local_ts: i64, px_e9: i64, qty_e9: i64) -> Event {
    Event {
        ev: kind.ev_bits(),
        exch_ts: exch_ms.saturating_mul(1_000_000),
        local_ts,
        px: px_e9 as f64 / 1e9,
        qty: qty_e9 as f64 / 1e9,
        order_id: 0,
        ival: 0,
        fval: 0.0,
    }
}

fn same_bits(a: &Event, b: &Event) -> bool {
    a.ev == b.ev
        && a.exch_ts == b.exch_ts
        && a.local_ts == b.local_ts
        && a.px.to_bits() == b.px.to_bits()
        && a.qty.to_bits() == b.qty.to_bits()
        && a.order_id == b.order_id
        && a.ival == b.ival
        && a.fval.to_bits() == b.fval.to_bits()
}

/// Р6: развёртка компактного события — побитно тот же `Event`, что строил перевод потока, на всех
/// видах и на краях: отрицательные и огромные метки биржи (насыщение), нулевой и отрицательный объём.
#[test]
fn expand_is_bit_exact_with_legacy_translation() {
    let kinds = [
        EventKind::BidDepth,
        EventKind::AskDepth,
        EventKind::BuyTrade,
        EventKind::SellTrade,
    ];
    let exch = [
        0i64,
        1,
        1_758_844_800_123,
        -5,
        -1_758_844_800_123,
        EXCH_MS_LIMIT,
        -EXCH_MS_LIMIT,
        i64::MAX,
        i64::MIN,
        9_223_372_036_854,
        9_223_372_036_855,
    ];
    let prices = [0i64, 1, 123_456_789_000, 99_999_999_999, i64::MAX, -7];
    let sizes = [0i64, 1, 1_000_000_000, 250_000_000, i64::MAX];
    for &k in &kinds {
        for &e in &exch {
            for &p in &prices {
                for &q in &sizes {
                    let c = CompactEvent::new(k, e, 1_758_844_800_123_456_789, p, q);
                    assert_eq!(c.kind(), k);
                    let got = c.expand();
                    let want = legacy(k, e, 1_758_844_800_123_456_789, p, q);
                    assert!(same_bits(&got, &want), "k={k:?} exch_ms={e} px={p} qty={q}");
                }
            }
        }
    }
    assert_eq!(std::mem::size_of::<CompactEvent>(), 32);
}

/// `expand_until` — те же строки, что поштучный цикл `row` до `until`; на всех границах (пусто, всё, середина).
#[test]
fn expand_until_matches_row_loop() {
    let rows: Vec<CompactEvent> = (0..40_i64)
        .map(|i| {
            let kind = EventKind::from_bits(i);
            CompactEvent::new(
                kind,
                i / 3,
                1_000 + i * 10,
                1_234_567_891 + i,
                5_000_000 + i * 7,
            )
        })
        .collect();
    for start in [0, 1, 17, 39] {
        for until in [0, 999, 1_000, 1_005, 1_170, 1_389, 1_390, i64::MAX] {
            let mut want = Vec::new();
            let mut i = start;
            while i < rows.len() && rows[i].local_ts() <= until {
                want.push(rows[i].expand());
                i += 1;
            }
            let mut got = vec![legacy(EventKind::BidDepth, 0, 0, 0, 0); 3];
            let end = rows.expand_until(start, until, &mut got);
            assert_eq!(end, i, "start={start} until={until}");
            assert_eq!(got.len(), want.len());
            assert!(got.iter().zip(&want).all(|(a, b)| same_bits(a, b)));
        }
    }
}
