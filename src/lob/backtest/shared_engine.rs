//! Общий движок кругов (TK-049, шаг б, часть 2б): стенд на ОДИН круг. Одна лента строк, две общие книги
//! (биржа и локальная) обновляются здесь один раз на строку, круг (`Local` + `PartialFillExchange` на ведомых
//! видах книг) получает кортеж и сделки. `goto` повторяет `Backtest::goto` крейта (те же четыре ключа
//! `LocalData < LocalOrder < ExchData < ExchOrder`, тот же порядок обновления слотов), поэтому результат
//! обязан совпасть с `Backtest` байт в байт; несколько кругов и сопрограммы — следующий шаг.

use super::shared_depth::SharedDepth;
use hftbacktest::backtest::assettype::AssetType;
use hftbacktest::backtest::models::{FeeModel, LatencyModel, QueueModel};
use hftbacktest::backtest::order::order_bus;
use hftbacktest::backtest::proc::{Local, LocalProcessor, PartialFillExchange, Processor};
use hftbacktest::backtest::state::State;
use hftbacktest::backtest::BacktestError;
use hftbacktest::depth::L2MarketDepth;
use hftbacktest::types::{
    ElapseResult, Event, OrdType, OrderId, Side, TimeInForce, WaitOrderResponse,
    EXCH_ASK_DEPTH_CLEAR_EVENT, EXCH_ASK_DEPTH_EVENT, EXCH_ASK_DEPTH_SNAPSHOT_EVENT,
    EXCH_BID_DEPTH_CLEAR_EVENT, EXCH_BID_DEPTH_EVENT, EXCH_BID_DEPTH_SNAPSHOT_EVENT,
    EXCH_BUY_TRADE_EVENT, EXCH_DEPTH_CLEAR_EVENT, EXCH_EVENT, EXCH_SELL_TRADE_EVENT,
    LOCAL_ASK_DEPTH_CLEAR_EVENT, LOCAL_ASK_DEPTH_EVENT, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT,
    LOCAL_BID_DEPTH_CLEAR_EVENT, LOCAL_BID_DEPTH_EVENT, LOCAL_BID_DEPTH_SNAPSHOT_EVENT,
    LOCAL_DEPTH_CLEAR_EVENT, LOCAL_EVENT, UNTIL_END_OF_DATA,
};

const LOCAL_DATA: usize = 0;
const LOCAL_ORDER: usize = 1;
const EXCH_DATA: usize = 2;
const EXCH_ORDER: usize = 3;

pub struct SharedEngine<AT, LM, QM, FM>
where
    AT: AssetType,
    LM: LatencyModel,
    QM: QueueModel<SharedDepth>,
    FM: FeeModel,
{
    rows: Vec<Event>,
    local_row: Option<usize>,
    exch_row: Option<usize>,
    local_book: SharedDepth,
    exch_book: SharedDepth,
    local: Local<AT, LM, SharedDepth, FM>,
    exch: PartialFillExchange<AT, LM, QM, SharedDepth, FM>,
    evs: [i64; 4],
    cur_ts: i64,
}

impl<AT, LM, QM, FM> SharedEngine<AT, LM, QM, FM>
where
    AT: AssetType + Clone,
    LM: LatencyModel + Clone,
    QM: QueueModel<SharedDepth>,
    FM: FeeModel + Clone,
{
    #[allow(clippy::too_many_arguments)]
    /// `local_book`/`exch_book` — ведущие общие книги (пустые, с шагом цены и лота потока).
    pub fn new(
        rows: Vec<Event>,
        local_book: SharedDepth,
        exch_book: SharedDepth,
        asset_type: AT,
        fee_model: FM,
        order_latency: LM,
        queue_model: QM,
        last_trades_cap: usize,
    ) -> Self {
        let (order_e2l, order_l2e) = order_bus(order_latency);
        let local = Local::new(
            local_book.follower(),
            State::new(asset_type.clone(), fee_model.clone()),
            last_trades_cap,
            order_l2e,
        );
        let exch = PartialFillExchange::new(
            exch_book.follower(),
            State::new(asset_type, fee_model),
            queue_model,
            order_e2l,
        );
        Self {
            rows,
            local_row: None,
            exch_row: None,
            local_book,
            exch_book,
            local,
            exch,
            evs: [i64::MAX; 4],
            cur_ts: i64::MAX,
        }
    }

    fn advance_local(&mut self) -> Option<i64> {
        let start = self.local_row.map_or(0, |r| r + 1);
        for rn in start..self.rows.len() {
            let e = &self.rows[rn];
            if e.is(LOCAL_EVENT) {
                self.local_row = Some(rn);
                return Some(e.local_ts);
            }
        }
        self.local_row = Some(self.rows.len());
        None
    }

    fn advance_exch(&mut self) -> Option<i64> {
        let start = self.exch_row.map_or(0, |r| r + 1);
        for rn in start..self.rows.len() {
            let e = &self.rows[rn];
            if e.is(EXCH_EVENT) {
                self.exch_row = Some(rn);
                return Some(e.exch_ts);
            }
        }
        self.exch_row = Some(self.rows.len());
        None
    }

    fn init(&mut self) -> bool {
        if self.cur_ts != i64::MAX {
            return true;
        }
        self.evs[LOCAL_DATA] = self.advance_local().unwrap_or(i64::MAX);
        self.evs[EXCH_DATA] = self.advance_exch().unwrap_or(i64::MAX);
        match self.next_ev() {
            Some((ts, _)) => {
                self.cur_ts = ts;
                true
            }
            None => false,
        }
    }

    fn next_ev(&self) -> Option<(i64, usize)> {
        let mut k = 0;
        let mut ts = self.evs[0];
        for (i, &t) in self.evs.iter().enumerate().skip(1) {
            if t < ts {
                ts = t;
                k = i;
            }
        }
        (ts != i64::MAX).then_some((ts, k))
    }

    /// Строка ленты для локальной стороны: общая книга обновляется один раз, круг — только сделки/задержка.
    fn process_local(&mut self, ev: Event) {
        if ev.is(LOCAL_BID_DEPTH_CLEAR_EVENT) {
            self.local_book.clear_depth(Side::Buy, ev.px);
        } else if ev.is(LOCAL_ASK_DEPTH_CLEAR_EVENT) {
            self.local_book.clear_depth(Side::Sell, ev.px);
        } else if ev.is(LOCAL_DEPTH_CLEAR_EVENT) {
            self.local_book.clear_depth(Side::None, 0.0);
        } else if ev.is(LOCAL_BID_DEPTH_EVENT) || ev.is(LOCAL_BID_DEPTH_SNAPSHOT_EVENT) {
            self.local_book.update_bid_depth(ev.px, ev.qty, ev.local_ts);
        } else if ev.is(LOCAL_ASK_DEPTH_EVENT) || ev.is(LOCAL_ASK_DEPTH_SNAPSHOT_EVENT) {
            self.local_book.update_ask_depth(ev.px, ev.qty, ev.local_ts);
        }
        self.local.apply_feed(&ev);
    }

    fn process_exch(&mut self, ev: Event) -> Result<(), BacktestError> {
        if ev.is(EXCH_BID_DEPTH_CLEAR_EVENT) {
            self.exch_book.clear_depth(Side::Buy, ev.px);
        } else if ev.is(EXCH_ASK_DEPTH_CLEAR_EVENT) {
            self.exch_book.clear_depth(Side::Sell, ev.px);
        } else if ev.is(EXCH_DEPTH_CLEAR_EVENT) {
            self.exch_book.clear_depth(Side::None, 0.0);
        } else if ev.is(EXCH_BID_DEPTH_EVENT) || ev.is(EXCH_BID_DEPTH_SNAPSHOT_EVENT) {
            let (t, pb, b, pq, nq, ts) = self.exch_book.update_bid_depth(ev.px, ev.qty, ev.exch_ts);
            self.exch.apply_bid_delta(t, pb, b, pq, nq, ts)?;
        } else if ev.is(EXCH_ASK_DEPTH_EVENT) || ev.is(EXCH_ASK_DEPTH_SNAPSHOT_EVENT) {
            let (t, pb, b, pq, nq, ts) = self.exch_book.update_ask_depth(ev.px, ev.qty, ev.exch_ts);
            self.exch.apply_ask_delta(t, pb, b, pq, nq, ts)?;
        } else if ev.is(EXCH_BUY_TRADE_EVENT) {
            self.exch.apply_buy_trade(&ev)?;
        } else if ev.is(EXCH_SELL_TRADE_EVENT) {
            self.exch.apply_sell_trade(&ev)?;
        }
        Ok(())
    }

    fn goto<const WAIT_NEXT_FEED: bool>(
        &mut self,
        timestamp: i64,
        wait: WaitOrderResponse,
    ) -> Result<ElapseResult, BacktestError> {
        let mut result = ElapseResult::Ok;
        let mut timestamp = timestamp;
        self.evs[EXCH_ORDER] = self.local.earliest_send_order_timestamp();
        self.evs[LOCAL_ORDER] = self.local.earliest_recv_order_timestamp();
        loop {
            let Some((ev_ts, kind)) = self.next_ev() else {
                return Ok(ElapseResult::EndOfData);
            };
            if ev_ts > timestamp {
                self.cur_ts = timestamp;
                return Ok(result);
            }
            match kind {
                LOCAL_DATA => {
                    let row = self.local_row.expect("строка локальной стороны");
                    let ev = self.rows[row].clone();
                    self.process_local(ev);
                    self.evs[LOCAL_DATA] = self.advance_local().unwrap_or(i64::MAX);
                    if WAIT_NEXT_FEED {
                        timestamp = ev_ts;
                        result = ElapseResult::MarketFeed;
                    }
                }
                LOCAL_ORDER => {
                    let wait_id = match wait {
                        WaitOrderResponse::Specified { order_id, .. } => Some(order_id),
                        _ => None,
                    };
                    if self.local.process_recv_order(ev_ts, wait_id)?
                        || wait == WaitOrderResponse::Any
                    {
                        timestamp = ev_ts;
                        if WAIT_NEXT_FEED {
                            result = ElapseResult::OrderResponse;
                        }
                    }
                    self.evs[LOCAL_ORDER] = self.local.earliest_recv_order_timestamp();
                }
                EXCH_DATA => {
                    let row = self.exch_row.expect("строка биржевой стороны");
                    let ev = self.rows[row].clone();
                    self.process_exch(ev)?;
                    self.evs[EXCH_DATA] = self.advance_exch().unwrap_or(i64::MAX);
                    self.evs[LOCAL_ORDER] = self.exch.earliest_send_order_timestamp();
                }
                _ => {
                    let _ = self.exch.process_recv_order(ev_ts, None)?;
                    self.evs[EXCH_ORDER] = self.exch.earliest_recv_order_timestamp();
                    self.evs[LOCAL_ORDER] = self.exch.earliest_send_order_timestamp();
                }
            }
        }
    }

    pub fn current_timestamp(&self) -> i64 {
        self.cur_ts
    }

    pub fn depth(&self) -> &SharedDepth {
        self.local.depth()
    }

    pub fn position(&self) -> f64 {
        self.local.position()
    }

    pub fn state_values(&self) -> &hftbacktest::types::StateValues {
        self.local.state_values()
    }

    pub fn orders(&self) -> &hftbacktest::types::OrderMap {
        self.local.orders()
    }

    pub fn elapse(&mut self, duration: i64) -> Result<ElapseResult, BacktestError> {
        if !self.init() {
            return Ok(ElapseResult::EndOfData);
        }
        self.goto::<false>(self.cur_ts + duration, WaitOrderResponse::None)
    }

    pub fn wait_next_feed(
        &mut self,
        include_order_resp: bool,
        timeout: i64,
    ) -> Result<ElapseResult, BacktestError> {
        if !self.init() {
            return Ok(ElapseResult::EndOfData);
        }
        let w = if include_order_resp {
            WaitOrderResponse::Any
        } else {
            WaitOrderResponse::None
        };
        self.goto::<true>(self.cur_ts + timeout, w)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn submit_order(
        &mut self,
        order_id: OrderId,
        side: Side,
        price: f64,
        qty: f64,
        time_in_force: TimeInForce,
        order_type: OrdType,
        wait: bool,
    ) -> Result<ElapseResult, BacktestError> {
        self.local.submit_order(
            order_id,
            side,
            price,
            qty,
            order_type,
            time_in_force,
            self.cur_ts,
        )?;
        if wait {
            return self.goto::<false>(
                UNTIL_END_OF_DATA,
                WaitOrderResponse::Specified {
                    asset_no: 0,
                    order_id,
                },
            );
        }
        Ok(ElapseResult::Ok)
    }

    pub fn cancel(&mut self, order_id: OrderId, wait: bool) -> Result<ElapseResult, BacktestError> {
        self.local.cancel(order_id, self.cur_ts)?;
        if wait {
            return self.goto::<false>(
                UNTIL_END_OF_DATA,
                WaitOrderResponse::Specified {
                    asset_no: 0,
                    order_id,
                },
            );
        }
        Ok(ElapseResult::Ok)
    }
}

#[cfg(test)]
pub(crate) mod tests;
