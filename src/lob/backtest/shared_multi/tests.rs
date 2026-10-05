use super::*;
use crate::lob::backtest::fast_depth::FastMarketDepth;
use crate::lob::backtest::shared_engine::tests::{rows, snap_orders};
use crate::lob::backtest::shared_engine::SharedEngine;
use hftbacktest::backtest::assettype::LinearAsset;
use hftbacktest::backtest::models::{
    CommonFees, ConstantLatency, PowerProbQueueFunc3, ProbQueueModel, TradingValueFeeModel,
};
use hftbacktest::backtest::Backtest;
use hftbacktest::depth::MarketDepth;
use hftbacktest::types::Bot as _;

type AT = LinearAsset;
type LM = ConstantLatency;
type QM = ProbQueueModel<PowerProbQueueFunc3, SharedDepth>;
type FM = TradingValueFeeModel<CommonFees>;

trait Drv {
    fn ts(&self) -> i64;
    fn bbo(&self) -> (f64, f64);
    fn pos(&self) -> f64;
    fn sv(&self) -> String;
    fn ords(&self) -> Vec<String>;
    fn elapse(&mut self, d: i64) -> ElapseResult;
    fn wnf(&mut self, inc: bool, t: i64) -> ElapseResult;
    fn submit(&mut self, id: u64, side: Side, px: f64, qty: f64, wait: bool) -> ElapseResult;
    fn cancel(&mut self, id: u64) -> bool;
}

impl Drv for SharedEngine<AT, LM, QM, FM> {
    fn ts(&self) -> i64 {
        self.current_timestamp()
    }
    fn bbo(&self) -> (f64, f64) {
        (self.depth().best_bid(), self.depth().best_ask())
    }
    fn pos(&self) -> f64 {
        self.position()
    }
    fn sv(&self) -> String {
        format!("{:?}", self.state_values())
    }
    fn ords(&self) -> Vec<String> {
        snap_orders(self.orders())
    }
    fn elapse(&mut self, d: i64) -> ElapseResult {
        SharedEngine::elapse(self, d).unwrap()
    }
    fn wnf(&mut self, inc: bool, t: i64) -> ElapseResult {
        self.wait_next_feed(inc, t).unwrap()
    }
    fn submit(&mut self, id: u64, side: Side, px: f64, qty: f64, wait: bool) -> ElapseResult {
        self.submit_order(id, side, px, qty, TimeInForce::GTC, OrdType::Limit, wait)
            .unwrap()
    }
    fn cancel(&mut self, id: u64) -> bool {
        SharedEngine::cancel(self, id, false).is_ok()
    }
}

impl Drv for CircleCtx<AT, LM, QM, FM> {
    fn ts(&self) -> i64 {
        self.current_timestamp()
    }
    fn bbo(&self) -> (f64, f64) {
        (self.depth().best_bid(), self.depth().best_ask())
    }
    fn pos(&self) -> f64 {
        self.position()
    }
    fn sv(&self) -> String {
        format!("{:?}", self.state_values())
    }
    fn ords(&self) -> Vec<String> {
        snap_orders(self.orders())
    }
    fn elapse(&mut self, d: i64) -> ElapseResult {
        CircleCtx::elapse(self, d).unwrap()
    }
    fn wnf(&mut self, inc: bool, t: i64) -> ElapseResult {
        self.wait_next_feed(inc, t).unwrap()
    }
    fn submit(&mut self, id: u64, side: Side, px: f64, qty: f64, wait: bool) -> ElapseResult {
        self.submit_order(id, side, px, qty, TimeInForce::GTC, OrdType::Limit, wait)
            .unwrap()
    }
    fn cancel(&mut self, id: u64) -> bool {
        CircleCtx::cancel(self, id, false).is_ok()
    }
}

#[derive(Clone, Copy)]
struct P {
    every: u64,
    qty: f64,
    timeout: i64,
    inc: bool,
    wait_every: u64,
}

fn script<D: Drv>(d: &mut D, p: P) -> Vec<String> {
    let mut tr = vec![format!("{:?}", d.elapse(500))];
    let mut id = 1u64;
    for step in 0..300u64 {
        let r = d.wnf(p.inc, p.timeout);
        let (bb, ba) = d.bbo();
        tr.push(format!(
            "{step} {r:?} {} {} {} {} {} {:?}",
            d.ts(),
            bb.to_bits(),
            ba.to_bits(),
            d.pos(),
            d.sv(),
            d.ords()
        ));
        if matches!(r, ElapseResult::EndOfData) {
            break;
        }
        if step % p.every == 0 {
            let wait = p.wait_every > 0 && step % p.wait_every == 0;
            if bb.is_finite() && bb > 0.0 {
                tr.push(format!("{:?}", d.submit(id, Side::Buy, bb, p.qty, wait)));
                id += 1;
            }
            if ba.is_finite() && ba > 0.0 {
                tr.push(format!("{:?}", d.submit(id, Side::Sell, ba, p.qty, false)));
                id += 1;
            }
        }
        if step % 7 == 3 && id > 4 {
            tr.push(format!("cancel {}", d.cancel(id - 3)));
        }
        if step % 13 == 5 {
            tr.push(format!("{:?} {}", d.elapse(1_500), d.ts()));
        }
    }
    tr.push(format!("final {} {}", d.pos(), d.sv()));
    tr
}

fn fees() -> FM {
    TradingValueFeeModel::new(CommonFees::new(-0.0001, 0.0006))
}

fn qm() -> QM {
    ProbQueueModel::new(PowerProbQueueFunc3::new(3.0))
}

fn books() -> (SharedDepth, SharedDepth) {
    (
        SharedDepth::new_leader(FastMarketDepth::new(0.1, 1.0)),
        SharedDepth::new_leader(FastMarketDepth::new(0.1, 1.0)),
    )
}

#[test]
fn three_circles_equal_three_solo() {
    let ps = [
        P {
            every: 4,
            qty: 2.0,
            timeout: 20_000,
            inc: true,
            wait_every: 0,
        },
        P {
            every: 3,
            qty: 5.0,
            timeout: 5_000,
            inc: false,
            wait_every: 8,
        },
        P {
            every: 5,
            qty: 1.0,
            timeout: 60_000,
            inc: true,
            wait_every: 10,
        },
    ];
    let data = rows();
    let lat = ConstantLatency::new(2_000, 2_000);
    let solo: Vec<Vec<String>> = ps
        .iter()
        .map(|&p| {
            let (lb, eb) = books();
            let mut e = SharedEngine::new(
                data.clone(),
                lb,
                eb,
                LinearAsset::new(1.0),
                fees(),
                lat.clone(),
                qm(),
                64,
            );
            script(&mut e, p)
        })
        .collect();
    let (lb, eb) = books();
    let mut m: MultiEngine<AT, LM, QM, FM, Vec<String>> = MultiEngine::new(data, lb, eb);
    for &p in &ps {
        m.add_circle(
            LinearAsset::new(1.0),
            fees(),
            lat.clone(),
            qm(),
            64,
            move |mut c| script(&mut c, p),
        )
        .unwrap();
    }
    let mut res = m.run().unwrap();
    res.sort_by_key(|(i, _)| *i);
    assert_eq!(res.len(), 3);
    for (i, (_, tr)) in res.iter().enumerate() {
        assert!(tr.len() > 100, "круг {i}: трасса короткая");
        assert_eq!(tr.len(), solo[i].len(), "круг {i}: длина");
        for (k, (a, b)) in tr.iter().zip(&solo[i]).enumerate() {
            assert_eq!(a, b, "круг {i}, строка {k}");
        }
    }
}

type LM2 = crate::lob::backtest::MeasuredLatency;
type QM2 = ProbQueueModel<hftbacktest::backtest::models::PowerProbQueueFunc, SharedDepth>;

impl Drv for Backtest<FastMarketDepth> {
    fn ts(&self) -> i64 {
        self.current_timestamp()
    }
    fn bbo(&self) -> (f64, f64) {
        (self.depth(0).best_bid(), self.depth(0).best_ask())
    }
    fn pos(&self) -> f64 {
        self.position(0)
    }
    fn sv(&self) -> String {
        format!("{:?}", self.state_values(0))
    }
    fn ords(&self) -> Vec<String> {
        snap_orders(self.orders(0))
    }
    fn elapse(&mut self, d: i64) -> ElapseResult {
        hftbacktest::types::Bot::elapse(self, d).unwrap()
    }
    fn wnf(&mut self, inc: bool, t: i64) -> ElapseResult {
        self.wait_next_feed(inc, t).unwrap()
    }
    fn submit(&mut self, id: u64, side: Side, px: f64, qty: f64, wait: bool) -> ElapseResult {
        let r = if side == Side::Buy {
            self.submit_buy_order(0, id, px, qty, TimeInForce::GTC, OrdType::Limit, wait)
        } else {
            self.submit_sell_order(0, id, px, qty, TimeInForce::GTC, OrdType::Limit, wait)
        };
        r.unwrap()
    }
    fn cancel(&mut self, id: u64) -> bool {
        hftbacktest::types::Bot::cancel(self, 0, id, false).is_ok()
    }
}

impl Drv for CircleCtx<AT, LM2, QM2, FM> {
    fn ts(&self) -> i64 {
        self.current_timestamp()
    }
    fn bbo(&self) -> (f64, f64) {
        (self.depth().best_bid(), self.depth().best_ask())
    }
    fn pos(&self) -> f64 {
        self.position()
    }
    fn sv(&self) -> String {
        format!("{:?}", self.state_values())
    }
    fn ords(&self) -> Vec<String> {
        snap_orders(self.orders())
    }
    fn elapse(&mut self, d: i64) -> ElapseResult {
        CircleCtx::elapse(self, d).unwrap()
    }
    fn wnf(&mut self, inc: bool, t: i64) -> ElapseResult {
        self.wait_next_feed(inc, t).unwrap()
    }
    fn submit(&mut self, id: u64, side: Side, px: f64, qty: f64, wait: bool) -> ElapseResult {
        self.submit_order(id, side, px, qty, TimeInForce::GTC, OrdType::Limit, wait)
            .unwrap()
    }
    fn cancel(&mut self, id: u64) -> bool {
        CircleCtx::cancel(self, id, false).is_ok()
    }
}

#[test]
fn born_circles_equal_windows() {
    use crate::lob::backtest::{
        with_backtest_over_window, ExecLatency, QueueModelKind, SignalWindows,
    };
    use crate::lob::costs::{MAKER_FEE_BPS, TAKER_FEE_BPS};
    let data = rows();
    let lat = ExecLatency::uniform(2_000);
    let t0s = [
        data[40].exch_ts,
        data[100].exch_ts + 300,
        data[100].local_ts,
        data[180].exch_ts + 650,
        data[260].exch_ts + 1,
    ];
    let ps = [
        P {
            every: 4,
            qty: 2.0,
            timeout: 20_000,
            inc: true,
            wait_every: 0,
        },
        P {
            every: 3,
            qty: 5.0,
            timeout: 5_000,
            inc: false,
            wait_every: 8,
        },
    ];
    let wins = SignalWindows::build_crate(&data, &t0s, 0.1, 1.0);
    let mut refs: Vec<Vec<String>> = Vec::new();
    for (i, &t0) in t0s.iter().enumerate() {
        let w = wins.window_at(t0).unwrap();
        let p = ps[i % 2];
        refs.push(with_backtest_over_window(
            &w.depth,
            t0,
            &data[w.start..],
            0.1,
            1.0,
            lat,
            QueueModelKind::Prob { n: 3.0 },
            |bt| script(bt, p),
        ));
    }
    let (lb, eb) = books();
    let mut m: MultiEngine<AT, LM2, QM2, FM, Vec<String>> = MultiEngine::new(data, lb, eb);
    for (i, &t0) in t0s.iter().enumerate() {
        let p = ps[i % 2];
        m.add_circle_at(
            t0,
            LinearAsset::new(1.0),
            TradingValueFeeModel::new(CommonFees::new(
                MAKER_FEE_BPS / 10_000.0,
                TAKER_FEE_BPS / 10_000.0,
            )),
            crate::lob::backtest::MeasuredLatency(lat),
            ProbQueueModel::new(hftbacktest::backtest::models::PowerProbQueueFunc::new(3.0)),
            64,
            move |mut c| script(&mut c, p),
        )
        .unwrap();
    }
    let mut res = m.run().unwrap();
    res.sort_by_key(|(i, _)| *i);
    assert_eq!(res.len(), t0s.len());
    for (i, (_, tr)) in res.iter().enumerate() {
        assert!(tr.len() > 30, "круг {i}: трасса короткая ({})", tr.len());
        assert_eq!(tr.len(), refs[i].len(), "круг {i}: длина");
        for (k, (a, b)) in tr.iter().zip(&refs[i]).enumerate() {
            assert_eq!(a, b, "круг {i}, строка {k}");
        }
    }
}
