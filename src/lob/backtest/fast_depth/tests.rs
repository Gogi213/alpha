use hftbacktest::depth::{
    ApplySnapshot, HashMapMarketDepth, L2MarketDepth, MarketDepth, INVALID_MAX, INVALID_MIN,
};
use hftbacktest::types::Side;

use super::FastMarketDepth;

/// Детерминированный генератор (LCG) — поток обновлений без внешних крейтов.
struct Lcg(u64);

impl Lcg {
    fn next(&mut self) -> u64 {
        self.0 = self
            .0
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        self.0 >> 33
    }
}

fn same(a: &FastMarketDepth, b: &HashMapMarketDepth, ctx: &str) {
    assert_eq!(a.best_bid_tick, b.best_bid_tick, "{ctx}: best_bid_tick");
    assert_eq!(a.best_ask_tick, b.best_ask_tick, "{ctx}: best_ask_tick");
    assert_eq!(a.low_bid_tick, b.low_bid_tick, "{ctx}: low_bid_tick");
    assert_eq!(a.high_ask_tick, b.high_ask_tick, "{ctx}: high_ask_tick");
    assert_eq!(
        a.best_bid().to_bits(),
        b.best_bid().to_bits(),
        "{ctx}: best_bid"
    );
    assert_eq!(
        a.best_ask().to_bits(),
        b.best_ask().to_bits(),
        "{ctx}: best_ask"
    );
    assert_eq!(
        a.best_bid_qty().to_bits(),
        b.best_bid_qty().to_bits(),
        "{ctx}"
    );
    assert_eq!(
        a.best_ask_qty().to_bits(),
        b.best_ask_qty().to_bits(),
        "{ctx}"
    );
    let mut ab: Vec<(i64, u64)> = a.bid_depth.iter().map(|(t, q)| (*t, q.to_bits())).collect();
    let mut bb: Vec<(i64, u64)> = b.bid_depth.iter().map(|(t, q)| (*t, q.to_bits())).collect();
    let mut aa: Vec<(i64, u64)> = a.ask_depth.iter().map(|(t, q)| (*t, q.to_bits())).collect();
    let mut ba: Vec<(i64, u64)> = b.ask_depth.iter().map(|(t, q)| (*t, q.to_bits())).collect();
    ab.sort_unstable();
    bb.sort_unstable();
    aa.sort_unstable();
    ba.sort_unstable();
    assert_eq!(ab, bb, "{ctx}: бид");
    assert_eq!(aa, ba, "{ctx}: аск");
}

/// Э-05: книга с быстрым хешем ведёт себя как `HashMapMarketDepth` крейта поле в поле — на потоке из
/// 200 000 случайных обновлений обеих сторон (снятия уровней, перекрёст, дыры в тиках, лот ниже шага),
/// с очистками стороны и снимком; возвращаемые кортежи обновлений — те же.
#[test]
#[allow(clippy::cast_precision_loss, clippy::cast_possible_wrap)]
fn fast_depth_matches_the_crate_book_field_for_field() {
    let (tick, lot) = (0.5, 0.1);
    let mut a = FastMarketDepth::new(tick, lot);
    let mut b = HashMapMarketDepth::new(tick, lot);
    let mut rng = Lcg(7);
    for i in 0..200_000u32 {
        let mid = 2_000 + (rng.next() % 40) as i64;
        let off = (rng.next() % 120) as i64 - 20;
        let bid = rng.next().is_multiple_of(2);
        let tick_px = if bid { mid - off } else { mid + off };
        let qty = match rng.next() % 5 {
            0 => 0.0,
            1 => 0.04,
            _ => (rng.next() % 50) as f64 * 0.1,
        };
        let px = tick_px as f64 * tick;
        let ts = i64::from(i);
        if bid {
            assert_eq!(
                a.update_bid_depth(px, qty, ts),
                b.update_bid_depth(px, qty, ts),
                "шаг {i}"
            );
        } else {
            assert_eq!(
                a.update_ask_depth(px, qty, ts),
                b.update_ask_depth(px, qty, ts),
                "шаг {i}"
            );
        }
        if i % 25_000 == 24_999 {
            let side = if rng.next().is_multiple_of(2) {
                Side::Buy
            } else {
                Side::Sell
            };
            let upto = (mid as f64) * tick;
            a.clear_depth(side, upto);
            b.clear_depth(side, upto);
        }
        same(&a, &b, &format!("шаг {i}"));
    }
    let (sa, sb) = (a.snapshot(), b.snapshot());
    assert_eq!(sa.len(), sb.len());
    for (x, y) in sa.iter().zip(&sb) {
        assert_eq!(
            (x.ev, x.px.to_bits(), x.qty.to_bits()),
            (y.ev, y.px.to_bits(), y.qty.to_bits())
        );
    }
    a.clear_depth(Side::None, f64::NAN);
    b.clear_depth(Side::None, f64::NAN);
    same(&a, &b, "очистка");
    assert_eq!(
        (a.best_bid_tick, a.best_ask_tick),
        (INVALID_MIN, INVALID_MAX)
    );
}
