use super::*;
use crate::lob::backtest::fast_depth::FastMarketDepth;
use hftbacktest::backtest::assettype::LinearAsset;
use hftbacktest::backtest::data::Data;
use hftbacktest::backtest::models::{
    CommonFees, ConstantLatency, PowerProbQueueFunc3, ProbQueueModel, TradingValueFeeModel,
};
use hftbacktest::backtest::{Backtest, DataSource, ExchangeKind, L2AssetBuilder};
use hftbacktest::depth::MarketDepth;
use hftbacktest::types::{
    Bot, EXCH_BUY_TRADE_EVENT, EXCH_SELL_TRADE_EVENT, LOCAL_BUY_TRADE_EVENT, LOCAL_SELL_TRADE_EVENT,
};

fn ev(kind: u8, exch_ts: i64, px: f64, qty: f64) -> Event {
    let bits = match kind {
        0 => LOCAL_BID_DEPTH_EVENT | EXCH_BID_DEPTH_EVENT,
        1 => LOCAL_ASK_DEPTH_EVENT | EXCH_ASK_DEPTH_EVENT,
        2 => LOCAL_BUY_TRADE_EVENT | EXCH_BUY_TRADE_EVENT | EXCH_EVENT | LOCAL_EVENT,
        _ => LOCAL_SELL_TRADE_EVENT | EXCH_SELL_TRADE_EVENT | EXCH_EVENT | LOCAL_EVENT,
    };
    Event {
        ev: bits,
        exch_ts,
        local_ts: exch_ts + 700,
        px,
        qty,
        order_id: 0,
        ival: 0,
        fval: 0.0,
    }
}

fn rows() -> Vec<Event> {
    let mut v = vec![ev(0, 1_000, 100.0, 10.0), ev(1, 1_100, 100.1, 10.0)];
    let mut x: u64 = 12345;
    let mut ts = 1_200;
    for _ in 0..400 {
        x = x
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        let r = (x >> 33) as i64;
        ts += 300 + r % 900;
        let step = (r / 7 % 5) as f64 * 0.1;
        let qty = ((r / 3 % 9) as f64) * 2.0;
        match r % 4 {
            0 => v.push(ev(0, ts, 99.8 + step, qty)),
            1 => v.push(ev(1, ts, 100.0 + step, qty)),
            2 => v.push(ev(2, ts, 100.0 + step, 1.0 + (r % 3) as f64)),
            _ => v.push(ev(3, ts, 99.8 + step, 1.0 + (r % 3) as f64)),
        }
    }
    v
}

fn snap_orders(o: &hftbacktest::types::OrderMap) -> Vec<String> {
    let mut ids: Vec<_> = o.keys().copied().collect();
    ids.sort_unstable();
    ids.iter().map(|i| format!("{:?}", o[i])).collect()
}

#[test]
fn one_circle_equals_backtest() {
    let data = rows();
    let lat = ConstantLatency::new(2_000, 2_000);
    let fees = TradingValueFeeModel::new(CommonFees::new(-0.0001, 0.0006));
    let mut bt = Backtest::builder()
        .add_asset(
            L2AssetBuilder::default()
                .data(vec![DataSource::Data(Data::from_data(&data))])
                .latency_model(lat.clone())
                .asset_type(LinearAsset::new(1.0))
                .fee_model(fees.clone())
                .queue_model(ProbQueueModel::new(PowerProbQueueFunc3::new(3.0)))
                .exchange(ExchangeKind::PartialFillExchange)
                .last_trades_capacity(64)
                .depth(|| FastMarketDepth::new(0.1, 1.0))
                .build()
                .unwrap(),
        )
        .build()
        .unwrap();
    let mut eng = SharedEngine::new(
        data.clone(),
        SharedDepth::new_leader(FastMarketDepth::new(0.1, 1.0)),
        SharedDepth::new_leader(FastMarketDepth::new(0.1, 1.0)),
        LinearAsset::new(1.0),
        fees,
        lat,
        ProbQueueModel::new(PowerProbQueueFunc3::new(3.0)),
        64,
    );

    assert_eq!(
        format!("{:?}", bt.elapse(500).unwrap()),
        format!("{:?}", eng.elapse(500).unwrap())
    );
    let mut id = 1u64;
    for step in 0..300u64 {
        let a = bt.wait_next_feed(true, 20_000).unwrap();
        let b = eng.wait_next_feed(true, 20_000).unwrap();
        assert_eq!(format!("{a:?}"), format!("{b:?}"), "step {step}");
        assert_eq!(
            bt.current_timestamp(),
            eng.current_timestamp(),
            "step {step}"
        );
        if matches!(a, ElapseResult::EndOfData) {
            break;
        }
        let (bb, ba) = (bt.depth(0).best_bid(), bt.depth(0).best_ask());
        assert_eq!(
            (bb.to_bits(), ba.to_bits()),
            (
                eng.depth().best_bid().to_bits(),
                eng.depth().best_ask().to_bits()
            ),
            "step {step}"
        );
        if step % 4 == 0 {
            let (px_b, px_s) = (bb, ba);
            if px_b.is_finite() && px_b > 0.0 {
                bt.submit_buy_order(0, id, px_b, 2.0, TimeInForce::GTC, OrdType::Limit, false)
                    .unwrap();
                eng.submit_order(
                    id,
                    Side::Buy,
                    px_b,
                    2.0,
                    TimeInForce::GTC,
                    OrdType::Limit,
                    false,
                )
                .unwrap();
                id += 1;
            }
            if px_s.is_finite() && px_s > 0.0 {
                bt.submit_sell_order(0, id, px_s, 2.0, TimeInForce::GTC, OrdType::Limit, false)
                    .unwrap();
                eng.submit_order(
                    id,
                    Side::Sell,
                    px_s,
                    2.0,
                    TimeInForce::GTC,
                    OrdType::Limit,
                    false,
                )
                .unwrap();
                id += 1;
            }
        }
        if step % 7 == 3 && id > 4 {
            let cid = id - 3;
            let r1 = bt.cancel(0, cid, false);
            let r2 = eng.cancel(cid, false);
            assert_eq!(r1.is_ok(), r2.is_ok());
        }
        assert_eq!(bt.position(0), eng.position(), "step {step}");
        assert_eq!(
            format!("{:?}", bt.state_values(0)),
            format!("{:?}", eng.state_values()),
            "step {step}"
        );
        assert_eq!(
            snap_orders(bt.orders(0)),
            snap_orders(eng.orders()),
            "step {step}"
        );
    }
    assert!(id > 10, "заявок слишком мало");
    assert!(
        bt.position(0) != 0.0 || bt.state_values(0).num_trades > 0,
        "сделок не было — тест пустой"
    );
}
