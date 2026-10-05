//! Многокруговой движок (TK-049, шаг в): одна лента и две общие книги на все круги; круг (`Local` +
//! `PartialFillExchange` на ведомых видах книг) — сопрограмма с блокирующим API, как у `SharedEngine`.
//! Глобальный цикл идёт по ключам (метка, вид 0..4, номер круга): лента LocalData=0 / ExchData=2, заказы круга
//! LocalOrder=1 / ExchOrder=3, пробуждение круга (стратегия) = 4, то есть после всех событий метки — как
//! `ev_ts > timestamp` в `Backtest::goto`. Каждый круг обязан дать то же, что одиночный `SharedEngine`.
//! Ошибка обработки события круга прерывает весь прогон (`run` возвращает её).

use std::cmp::Reverse;
use std::collections::BinaryHeap;

use corosensei::stack::DefaultStack;
use corosensei::{Coroutine, CoroutineResult, Yielder};

use super::sched::CIRCLE_STACK_BYTES;
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

const K_LOCAL_DATA: u8 = 0;
const K_LOCAL_ORDER: u8 = 1;
const K_EXCH_DATA: u8 = 2;
const K_EXCH_ORDER: u8 = 3;
const K_WAKE: u8 = 4;

type Co<R> = Coroutine<(), (), R, DefaultStack>;
type Key = Reverse<(i64, u8, u32, u32)>;

struct Req {
    birth: Option<i64>,
    wnf: bool,
    wait: WaitOrderResponse,
    bound: i64,
}

#[derive(PartialEq, Eq, Clone, Copy)]
enum Cs {
    Unborn,
    Running,
    Waiting,
    Done,
}

struct Circle<AT, LM, QM, FM>
where
    AT: AssetType,
    LM: LatencyModel,
    QM: QueueModel<SharedDepth>,
    FM: FeeModel,
{
    local: Local<AT, LM, SharedDepth, FM>,
    exch: PartialFillExchange<AT, LM, QM, SharedDepth, FM>,
    cur_ts: i64,
    first_ts: i64,
    req: Option<Req>,
    out: ElapseResult,
    cs: Cs,
    wnf: bool,
    wait: WaitOrderResponse,
    bound: i64,
    result: ElapseResult,
    eod: bool,
    slot_ts: [i64; 3],
    slot_ver: [u32; 3],
    /// Срок ожидания круга «до следующей ленты» (`i64::MAX` — нет); живёт вне кучи, см. `dl_min`.
    dl: i64,
    start: Option<i64>,
    data_end: Option<i64>,
    mk: MkParts<AT, LM, QM, FM>,
    born_ok: bool,
    fresh: bool,
}

type MkParts<AT, LM, QM, FM> = Box<
    dyn Fn() -> (
        Local<AT, LM, SharedDepth, FM>,
        PartialFillExchange<AT, LM, QM, SharedDepth, FM>,
    ),
>;

impl<AT, LM, QM, FM> Circle<AT, LM, QM, FM>
where
    AT: AssetType,
    LM: LatencyModel,
    QM: QueueModel<SharedDepth>,
    FM: FeeModel,
{
    fn live(&self) -> bool {
        matches!(self.cs, Cs::Running | Cs::Waiting)
    }
}

const ST_N: usize = 20;
const ST_NAMES: [&str; ST_N] = [
    "rows_local",
    "rows_exch",
    "deliv_local",
    "deliv_exch",
    "births",
    "mk_calls",
    "pop_lo",
    "pop_eo",
    "pop_birth",
    "pop_wake",
    "stale_pops",
    "wnf_wakes",
    "ns_local",
    "ns_exch",
    "ns_resume",
    "upd_local",
    "upd_exch",
    "heap_push",
    "heap_max",
    "wnfq_pop",
];

const S_LO: usize = 0;
const S_EO: usize = 1;
const S_WAKE: usize = 2;
const SLOT_KIND: [u8; 3] = [K_LOCAL_ORDER, K_EXCH_ORDER, K_WAKE];

/// Рука круга: блокирующий API поверх сопрограммы. Живёт только внутри тела круга.
pub struct CircleCtx<AT, LM, QM, FM>
where
    AT: AssetType,
    LM: LatencyModel,
    QM: QueueModel<SharedDepth>,
    FM: FeeModel,
{
    c: *mut Circle<AT, LM, QM, FM>,
    y: *const Yielder<(), ()>,
    depth: SharedDepth,
}

impl<AT, LM, QM, FM> CircleCtx<AT, LM, QM, FM>
where
    AT: AssetType,
    LM: LatencyModel,
    QM: QueueModel<SharedDepth>,
    FM: FeeModel,
{
    // Круг лежит в Box движка и трогается только пока его сопрограмма работает (движок — когда она
    // приостановлена); потоки не участвуют.
    fn c(&self) -> &Circle<AT, LM, QM, FM> {
        unsafe { &*self.c }
    }

    fn cm(&mut self) -> &mut Circle<AT, LM, QM, FM> {
        unsafe { &mut *self.c }
    }

    pub fn current_timestamp(&self) -> i64 {
        self.c().cur_ts
    }

    pub fn depth(&self) -> &SharedDepth {
        &self.depth
    }

    /// Конец данных: меньшая из меток последней строки ленты (как `data_end` окна).
    pub fn data_end(&self) -> Option<i64> {
        self.c().data_end
    }

    pub fn position(&self) -> f64 {
        self.c().local.position()
    }

    pub fn state_values(&self) -> &hftbacktest::types::StateValues {
        self.c().local.state_values()
    }

    pub fn orders(&self) -> &hftbacktest::types::OrderMap {
        self.c().local.orders()
    }

    fn block(&mut self, wnf: bool, wait: WaitOrderResponse, bound: i64) -> ElapseResult {
        self.cm().req = Some(Req {
            birth: None,
            wnf,
            wait,
            bound,
        });
        unsafe { (*self.y).suspend(()) };
        let c = self.cm();
        c.out
    }

    /// Новый круг на `t0` (`t0 >=` часов круга): прежние заявки и книги круга отбрасываются, как у нового
    /// окна сигнала. `false` — строк с метками `> t0` в ленте нет (окна нет), круг прежний.
    pub fn rebirth(&mut self, t0: i64) -> bool {
        self.cm().req = Some(Req {
            birth: Some(t0),
            wnf: false,
            wait: WaitOrderResponse::None,
            bound: t0,
        });
        unsafe { (*self.y).suspend(()) };
        self.c().born_ok
    }

    fn init(&mut self) -> bool {
        let c = self.cm();
        if c.cur_ts != i64::MAX {
            return true;
        }
        if c.first_ts == i64::MAX {
            return false;
        }
        c.cur_ts = c.first_ts;
        true
    }

    pub fn elapse(&mut self, duration: i64) -> Result<ElapseResult, BacktestError> {
        if !self.init() {
            return Ok(ElapseResult::EndOfData);
        }
        let b = self.c().cur_ts + duration;
        Ok(self.block(false, WaitOrderResponse::None, b))
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
        let b = self.c().cur_ts + timeout;
        Ok(self.block(true, w, b))
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
        let now = self.c().cur_ts;
        self.cm()
            .local
            .submit_order(order_id, side, price, qty, order_type, time_in_force, now)?;
        Ok(self.after_order(order_id, wait))
    }

    fn after_order(&mut self, order_id: OrderId, wait: bool) -> ElapseResult {
        if wait {
            return self.block(
                false,
                WaitOrderResponse::Specified {
                    asset_no: 0,
                    order_id,
                },
                UNTIL_END_OF_DATA,
            );
        }
        ElapseResult::Ok
    }

    pub fn cancel(&mut self, order_id: OrderId, wait: bool) -> Result<ElapseResult, BacktestError> {
        let now = self.c().cur_ts;
        self.cm().local.cancel(order_id, now)?;
        Ok(self.after_order(order_id, wait))
    }
}

impl<AT, LM, QM, FM> hftbacktest::types::Bot<SharedDepth> for CircleCtx<AT, LM, QM, FM>
where
    AT: AssetType,
    LM: LatencyModel,
    QM: QueueModel<SharedDepth>,
    FM: FeeModel,
{
    type Error = BacktestError;

    fn current_timestamp(&self) -> i64 {
        self.c().cur_ts
    }

    fn num_assets(&self) -> usize {
        1
    }

    fn position(&self, _asset_no: usize) -> f64 {
        self.c().local.position()
    }

    fn state_values(&self, _asset_no: usize) -> &hftbacktest::types::StateValues {
        self.c().local.state_values()
    }

    fn depth(&self, _asset_no: usize) -> &SharedDepth {
        &self.depth
    }

    fn last_trades(&self, _asset_no: usize) -> &[Event] {
        self.c().local.last_trades()
    }

    fn clear_last_trades(&mut self, _asset_no: Option<usize>) {
        self.cm().local.clear_last_trades();
    }

    fn orders(&self, _asset_no: usize) -> &hftbacktest::types::OrderMap {
        self.c().local.orders()
    }

    fn submit_buy_order(
        &mut self,
        _asset_no: usize,
        order_id: OrderId,
        price: f64,
        qty: f64,
        time_in_force: TimeInForce,
        order_type: OrdType,
        wait: bool,
    ) -> Result<ElapseResult, BacktestError> {
        CircleCtx::submit_order(
            self,
            order_id,
            Side::Buy,
            price,
            qty,
            time_in_force,
            order_type,
            wait,
        )
    }

    fn submit_sell_order(
        &mut self,
        _asset_no: usize,
        order_id: OrderId,
        price: f64,
        qty: f64,
        time_in_force: TimeInForce,
        order_type: OrdType,
        wait: bool,
    ) -> Result<ElapseResult, BacktestError> {
        CircleCtx::submit_order(
            self,
            order_id,
            Side::Sell,
            price,
            qty,
            time_in_force,
            order_type,
            wait,
        )
    }

    // Как в `Backtest::submit_order` крейта: сторона всегда Sell.
    fn submit_order(
        &mut self,
        _asset_no: usize,
        order: hftbacktest::types::OrderRequest,
        wait: bool,
    ) -> Result<ElapseResult, BacktestError> {
        CircleCtx::submit_order(
            self,
            order.order_id,
            Side::Sell,
            order.price,
            order.qty,
            order.time_in_force,
            order.order_type,
            wait,
        )
    }

    fn modify(
        &mut self,
        _asset_no: usize,
        order_id: OrderId,
        price: f64,
        qty: f64,
        wait: bool,
    ) -> Result<ElapseResult, BacktestError> {
        let now = self.c().cur_ts;
        self.cm().local.modify(order_id, price, qty, now)?;
        Ok(self.after_order(order_id, wait))
    }

    fn cancel(
        &mut self,
        _asset_no: usize,
        order_id: OrderId,
        wait: bool,
    ) -> Result<ElapseResult, BacktestError> {
        CircleCtx::cancel(self, order_id, wait)
    }

    fn clear_inactive_orders(&mut self, _asset_no: Option<usize>) {
        self.cm().local.clear_inactive_orders();
    }

    fn wait_order_response(
        &mut self,
        _asset_no: usize,
        order_id: OrderId,
        timeout: i64,
    ) -> Result<ElapseResult, BacktestError> {
        let b = self.c().cur_ts + timeout;
        Ok(self.block(
            false,
            WaitOrderResponse::Specified {
                asset_no: 0,
                order_id,
            },
            b,
        ))
    }

    fn wait_next_feed(
        &mut self,
        include_order_resp: bool,
        timeout: i64,
    ) -> Result<ElapseResult, BacktestError> {
        CircleCtx::wait_next_feed(self, include_order_resp, timeout)
    }

    fn elapse(&mut self, duration: i64) -> Result<ElapseResult, BacktestError> {
        CircleCtx::elapse(self, duration)
    }

    fn elapse_bt(&mut self, duration: i64) -> Result<ElapseResult, BacktestError> {
        CircleCtx::elapse(self, duration)
    }

    fn close(&mut self) -> Result<(), BacktestError> {
        Ok(())
    }

    fn feed_latency(&self, _asset_no: usize) -> Option<(i64, i64)> {
        self.c().local.feed_latency()
    }

    fn order_latency(&self, _asset_no: usize) -> Option<(i64, i64, i64)> {
        self.c().local.order_latency()
    }
}

pub struct MultiEngine<AT, LM, QM, FM, R>
where
    AT: AssetType,
    LM: LatencyModel,
    QM: QueueModel<SharedDepth>,
    FM: FeeModel,
{
    rows: Vec<Event>,
    local_row: Option<usize>,
    exch_row: Option<usize>,
    ev_ld: i64,
    ev_ed: i64,
    first_ts: i64,
    local_book: SharedDepth,
    exch_book: SharedDepth,
    circles: Vec<Box<Circle<AT, LM, QM, FM>>>,
    cos: Vec<Option<Co<R>>>,
    heap: BinaryHeap<Key>,
    /// Пробуждения «по следующей ленте» одной метки `wnf_ts`, по возрастанию номера круга, мимо кучи (с головой `wnf_head`).
    wnf_q: Vec<(u32, u32)>,
    wnf_head: usize,
    wnf_ts: i64,
    /// Наименьший срок `(ts, id)` среди кругов с `dl != MAX`; при `dl_dirty` пересчитывается сканом в `drop_stale`.
    dl_min: (i64, u32),
    dl_dirty: bool,
    /// По номеру круга: 0 — не ждёт, 1 — ждёт (`Cs::Waiting`), 3 — ждёт ленту (`wnf`); плотный массив для циклов по строке.
    wflag: Vec<u8>,
    results: Vec<(u32, R)>,
    m_cursor: usize,
    st: [u64; ST_N],
    timed: bool,
}

impl<AT, LM, QM, FM, R> MultiEngine<AT, LM, QM, FM, R>
where
    AT: AssetType + Clone + 'static,
    LM: LatencyModel + Clone + 'static,
    QM: QueueModel<SharedDepth> + 'static,
    FM: FeeModel + Clone + 'static,
    R: 'static,
{
    pub fn new(rows: Vec<Event>, local_book: SharedDepth, exch_book: SharedDepth) -> Self {
        let l = rows
            .iter()
            .find(|e| e.is(LOCAL_EVENT))
            .map_or(i64::MAX, |e| e.local_ts);
        let x = rows
            .iter()
            .find(|e| e.is(EXCH_EVENT))
            .map_or(i64::MAX, |e| e.exch_ts);
        Self {
            rows,
            local_row: None,
            exch_row: None,
            ev_ld: i64::MAX,
            ev_ed: i64::MAX,
            first_ts: l.min(x),
            local_book,
            exch_book,
            circles: Vec::new(),
            cos: Vec::new(),
            heap: BinaryHeap::new(),
            wnf_q: Vec::new(),
            wnf_head: 0,
            wnf_ts: i64::MIN,
            dl_min: (i64::MAX, 0),
            dl_dirty: false,
            wflag: Vec::new(),
            results: Vec::new(),
            m_cursor: 0,
            st: [0; ST_N],
            timed: std::env::var_os("ALPHA_SHARED_STATS").is_some(),
        }
    }

    /// Добавить круг; `body` получает руку круга и возвращает результат круга.
    pub fn add_circle<F>(
        &mut self,
        asset_type: AT,
        fee_model: FM,
        order_latency: LM,
        queue_model: QM,
        last_trades_cap: usize,
        body: F,
    ) -> std::io::Result<u32>
    where
        F: FnOnce(CircleCtx<AT, LM, QM, FM>) -> R + 'static,
    {
        let once = std::cell::RefCell::new(Some(queue_model));
        self.add_circle_inner(
            None,
            asset_type,
            fee_model,
            order_latency,
            Box::new(move || {
                once.borrow_mut()
                    .take()
                    .expect("модель очереди одного круга")
            }),
            last_trades_cap,
            body,
        )
    }

    /// Круг-ячейка: первое же `rebirth(t0)` рождает круг на `t0`, каждое следующее — новый круг (новое окно
    /// сигнала) с моделью очереди из `mk_queue`; ячейки идут по одной ленте независимо.
    #[allow(clippy::too_many_arguments)]
    pub fn add_cell<F>(
        &mut self,
        asset_type: AT,
        fee_model: FM,
        order_latency: LM,
        mk_queue: Box<dyn Fn() -> QM>,
        last_trades_cap: usize,
        body: F,
    ) -> std::io::Result<u32>
    where
        F: FnOnce(CircleCtx<AT, LM, QM, FM>) -> R + 'static,
    {
        self.add_circle_inner(
            None,
            asset_type,
            fee_model,
            order_latency,
            mk_queue,
            last_trades_cap,
            body,
        )
    }

    /// Круг, рождённый на `t0`: до `t0` он ленты не видит, на `t0` (после всех строк с метками `<= t0`)
    /// его часы = `t0`, и он идёт как окно сигнала (`with_backtest_over_window`): строки «через `t0`»
    /// (одна метка `<= t0`, другая нет) его `Local` добирает так же, как окно.
    #[allow(clippy::too_many_arguments)]
    pub fn add_circle_at<F>(
        &mut self,
        t0: i64,
        asset_type: AT,
        fee_model: FM,
        order_latency: LM,
        queue_model: QM,
        last_trades_cap: usize,
        body: F,
    ) -> std::io::Result<u32>
    where
        F: FnOnce(CircleCtx<AT, LM, QM, FM>) -> R + 'static,
    {
        let once = std::cell::RefCell::new(Some(queue_model));
        self.add_circle_inner(
            Some(t0),
            asset_type,
            fee_model,
            order_latency,
            Box::new(move || {
                once.borrow_mut()
                    .take()
                    .expect("модель очереди одного круга")
            }),
            last_trades_cap,
            body,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn add_circle_inner<F>(
        &mut self,
        start: Option<i64>,
        asset_type: AT,
        fee_model: FM,
        order_latency: LM,
        mk_queue: Box<dyn Fn() -> QM>,
        last_trades_cap: usize,
        body: F,
    ) -> std::io::Result<u32>
    where
        F: FnOnce(CircleCtx<AT, LM, QM, FM>) -> R + 'static,
    {
        let local_depth = self.local_book.follower();
        let exch_depth = self.exch_book.follower();
        let ld = local_depth.clone();
        let mk: MkParts<AT, LM, QM, FM> = Box::new(move || {
            let (order_e2l, order_l2e) = order_bus(order_latency.clone());
            let local = Local::new(
                ld.clone(),
                State::new(asset_type.clone(), fee_model.clone()),
                last_trades_cap,
                order_l2e,
            );
            let exch = PartialFillExchange::new(
                exch_depth.clone(),
                State::new(asset_type.clone(), fee_model.clone()),
                mk_queue(),
                order_e2l,
            );
            (local, exch)
        });
        let (local, exch) = mk();
        let mut c = Box::new(Circle {
            local,
            exch,
            cur_ts: i64::MAX,
            first_ts: self.first_ts,
            req: None,
            out: ElapseResult::Ok,
            cs: if start.is_some() {
                Cs::Unborn
            } else {
                Cs::Running
            },
            wnf: false,
            wait: WaitOrderResponse::None,
            bound: i64::MAX,
            result: ElapseResult::Ok,
            eod: false,
            slot_ts: [i64::MAX; 3],
            slot_ver: [0; 3],
            dl: i64::MAX,
            start,
            data_end: self.rows.last().map(|e| e.local_ts.min(e.exch_ts)),
            mk,
            born_ok: true,
            fresh: true,
        });
        let ptr: *mut Circle<AT, LM, QM, FM> = &mut *c;
        let id = self.circles.len() as u32;
        self.circles.push(c);
        self.wflag.push(0);
        let stack = DefaultStack::new(CIRCLE_STACK_BYTES)?;
        let co = Coroutine::with_stack(stack, move |y: &Yielder<(), ()>, ()| {
            body(CircleCtx {
                c: ptr,
                y: y as *const _,
                depth: local_depth,
            })
        });
        self.cos.push(Some(co));
        Ok(id)
    }

    fn advance_local(&mut self) -> i64 {
        let start = self.local_row.map_or(0, |r| r + 1);
        for rn in start..self.rows.len() {
            let e = &self.rows[rn];
            if e.is(LOCAL_EVENT) {
                self.local_row = Some(rn);
                return e.local_ts;
            }
        }
        self.local_row = Some(self.rows.len());
        i64::MAX
    }

    fn advance_exch(&mut self) -> i64 {
        let start = self.exch_row.map_or(0, |r| r + 1);
        for rn in start..self.rows.len() {
            let e = &self.rows[rn];
            if e.is(EXCH_EVENT) {
                self.exch_row = Some(rn);
                return e.exch_ts;
            }
        }
        self.exch_row = Some(self.rows.len());
        i64::MAX
    }

    fn set_slot(&mut self, id: usize, slot: usize, ts: i64) {
        let c = &mut self.circles[id];
        if c.slot_ts[slot] == ts {
            return;
        }
        c.slot_ts[slot] = ts;
        c.slot_ver[slot] += 1;
        if ts != i64::MAX {
            self.st[17] += 1;
            self.st[18] = self.st[18].max(self.heap.len() as u64);
            self.heap.push(Reverse((
                ts,
                SLOT_KIND[slot],
                id as u32,
                c.slot_ver[slot] * 4 + slot as u32,
            )));
        }
    }

    fn dl_set(&mut self, id: usize, ts: i64) {
        self.dl_clear(id);
        if ts != i64::MAX {
            self.circles[id].dl = ts;
            if (ts, id as u32) < self.dl_min {
                self.dl_min = (ts, id as u32);
            }
        }
    }

    fn dl_clear(&mut self, id: usize) {
        let c = &mut self.circles[id];
        if c.dl != i64::MAX {
            c.dl = i64::MAX;
            if self.dl_min.1 == id as u32 {
                self.dl_dirty = true;
            }
        }
    }

    /// То же, что `set_slot(id, S_WAKE, ts)`, но запись — в очередь `wnf_q` (одна метка ts, ключ как у кучи: ts, K_WAKE, id).
    fn set_wake_wnf(&mut self, id: usize, ts: i64) {
        let c = &mut self.circles[id];
        if c.slot_ts[S_WAKE] == ts {
            return;
        }
        c.slot_ts[S_WAKE] = ts;
        c.slot_ver[S_WAKE] += 1;
        let tag = c.slot_ver[S_WAKE] * 4 + S_WAKE as u32;
        if self.wnf_head < self.wnf_q.len() && self.wnf_ts != ts {
            for &(i, t) in &self.wnf_q[self.wnf_head..] {
                self.heap.push(Reverse((self.wnf_ts, K_WAKE, i, t)));
            }
            self.wnf_q.clear();
            self.wnf_head = 0;
        }
        if self.wnf_head >= self.wnf_q.len() {
            self.wnf_q.clear();
            self.wnf_head = 0;
        }
        self.wnf_ts = ts;
        let sorted = self.wnf_q.last().is_none_or(|&(i, _)| i < id as u32);
        self.wnf_q.push((id as u32, tag));
        if !sorted {
            self.wnf_q[self.wnf_head..].sort_unstable();
        }
    }

    fn check_end(&mut self, id: usize, now: i64) {
        let feed_done = self.ev_ld == i64::MAX && self.ev_ed == i64::MAX;
        let c = &self.circles[id];
        if feed_done
            && c.cs == Cs::Waiting
            && !c.eod
            && c.slot_ts[S_LO] == i64::MAX
            && c.slot_ts[S_EO] == i64::MAX
        {
            self.circles[id].eod = true;
            self.dl_clear(id);
            self.set_slot(id, S_WAKE, now);
        }
    }

    /// Круг приостановился с запросом: как начало `goto`.
    fn register(&mut self, id: usize, now: i64) {
        let c = &mut self.circles[id];
        let req = c.req.take().expect("запрос ожидания");
        if let Some(t0) = req.birth {
            assert!(
                t0 >= now,
                "рождение круга в прошлом: t0 {t0} < часы ленты {now}"
            );
            c.cs = Cs::Unborn;
            c.start = Some(t0);
            c.eod = false;
            for s in 0..3 {
                self.set_slot(id, s, i64::MAX);
            }
            self.set_slot(id, S_WAKE, t0);
            return;
        }
        c.cs = Cs::Waiting;
        c.wnf = req.wnf;
        c.wait = req.wait;
        c.bound = req.bound;
        c.result = ElapseResult::Ok;
        c.eod = false;
        self.wflag[id] = if c.wnf { 3 } else { 1 };
        let eo = c.local.earliest_send_order_timestamp();
        let lo = c.local.earliest_recv_order_timestamp();
        self.set_slot(id, S_EO, eo);
        self.set_slot(id, S_LO, lo);
        let b = self.circles[id].bound;
        if self.circles[id].wnf {
            self.set_slot(id, S_WAKE, i64::MAX);
            self.dl_set(id, b);
        } else {
            self.set_slot(id, S_WAKE, b);
        }
        self.check_end(id, now);
    }

    fn resume(&mut self, id: usize, now: i64) {
        self.circles[id].cs = Cs::Running;
        self.wflag[id] = 0;
        self.dl_clear(id);
        let t = self.timed.then(std::time::Instant::now);
        let r = self.cos[id].as_mut().expect("круг жив").resume(());
        if let Some(t) = t {
            self.st[14] += t.elapsed().as_nanos() as u64;
        }
        match r {
            CoroutineResult::Yield(()) => self.register(id, now),
            CoroutineResult::Return(r) => {
                self.circles[id].cs = Cs::Done;
                for s in 0..3 {
                    self.set_slot(id, s, i64::MAX);
                }
                self.results.push((id as u32, r));
            }
        }
    }

    /// Рождение круга на `t0` (все строки с метками `<= t0` ленты уже в общих книгах). Окно сигнала до
    /// первого `elapse` отдаёт `Local` якорь `(t0, t0)` и строки `[m..)` с `local_ts <= t0` (`m` — первая
    /// строка с любой меткой `> t0`); здесь то же самое, строки — те, что лента уже прошла.
    fn birth(&mut self, id: usize, t0: i64) {
        self.st[4] += 1;
        while self.m_cursor < self.rows.len() {
            let e = &self.rows[self.m_cursor];
            if e.local_ts <= t0 && e.exch_ts <= t0 {
                self.m_cursor += 1;
            } else {
                break;
            }
        }
        let end = self.local_row.unwrap_or(0).min(self.rows.len());
        self.set_slot(id, S_WAKE, i64::MAX);
        let no_window = self.m_cursor >= self.rows.len();
        let c = &mut self.circles[id];
        c.cs = Cs::Running;
        if no_window {
            c.born_ok = false;
            self.resume(id, t0);
            return;
        }
        c.born_ok = true;
        if !c.fresh {
            self.st[5] += 1;
            let (local, exch) = (c.mk)();
            c.local = local;
            c.exch = exch;
        }
        c.fresh = false;
        c.cur_ts = t0;
        c.local.apply_feed(&Event {
            ev: LOCAL_BID_DEPTH_EVENT | EXCH_BID_DEPTH_EVENT,
            exch_ts: t0,
            local_ts: t0,
            px: 0.0,
            qty: 0.0,
            order_id: 0,
            ival: 0,
            fval: 0.0,
        });
        for e in &self.rows[self.m_cursor.min(end)..end] {
            if e.is(LOCAL_EVENT) {
                c.local.apply_feed(e);
            }
        }
        self.resume(id, t0);
    }

    fn process_local(&mut self, ev: &Event) {
        if ev.is(LOCAL_BID_DEPTH_CLEAR_EVENT) {
            self.local_book.clear_depth(Side::Buy, ev.px);
        } else if ev.is(LOCAL_ASK_DEPTH_CLEAR_EVENT) {
            self.local_book.clear_depth(Side::Sell, ev.px);
        } else if ev.is(LOCAL_DEPTH_CLEAR_EVENT) {
            self.local_book.clear_depth(Side::None, 0.0);
        } else if ev.is(LOCAL_BID_DEPTH_EVENT) || ev.is(LOCAL_BID_DEPTH_SNAPSHOT_EVENT) {
            self.st[15] += 1;
            self.local_book.update_bid_depth(ev.px, ev.qty, ev.local_ts);
        } else if ev.is(LOCAL_ASK_DEPTH_EVENT) || ev.is(LOCAL_ASK_DEPTH_SNAPSHOT_EVENT) {
            self.st[15] += 1;
            self.local_book.update_ask_depth(ev.px, ev.qty, ev.local_ts);
        }
        self.st[0] += 1;
        for c in self.circles.iter_mut().filter(|c| c.live()) {
            self.st[2] += 1;
            c.local.apply_feed(ev);
        }
    }

    fn process_exch(&mut self, ev: &Event) -> Result<(), BacktestError> {
        self.st[1] += 1;
        self.st[3] += self.circles.iter().filter(|c| c.live()).count() as u64;
        if ev.is(EXCH_BID_DEPTH_CLEAR_EVENT) {
            self.exch_book.clear_depth(Side::Buy, ev.px);
        } else if ev.is(EXCH_ASK_DEPTH_CLEAR_EVENT) {
            self.exch_book.clear_depth(Side::Sell, ev.px);
        } else if ev.is(EXCH_DEPTH_CLEAR_EVENT) {
            self.exch_book.clear_depth(Side::None, 0.0);
        } else if ev.is(EXCH_BID_DEPTH_EVENT) || ev.is(EXCH_BID_DEPTH_SNAPSHOT_EVENT) {
            self.st[16] += 1;
            let (t, pb, b, pq, nq, ts) = self.exch_book.update_bid_depth(ev.px, ev.qty, ev.exch_ts);
            for c in self.circles.iter_mut().filter(|c| c.live()) {
                c.exch.apply_bid_delta(t, pb, b, pq, nq, ts)?;
            }
        } else if ev.is(EXCH_ASK_DEPTH_EVENT) || ev.is(EXCH_ASK_DEPTH_SNAPSHOT_EVENT) {
            self.st[16] += 1;
            let (t, pb, b, pq, nq, ts) = self.exch_book.update_ask_depth(ev.px, ev.qty, ev.exch_ts);
            for c in self.circles.iter_mut().filter(|c| c.live()) {
                c.exch.apply_ask_delta(t, pb, b, pq, nq, ts)?;
            }
        } else if ev.is(EXCH_BUY_TRADE_EVENT) {
            for c in self.circles.iter_mut().filter(|c| c.live()) {
                c.exch.apply_buy_trade(ev)?;
            }
        } else if ev.is(EXCH_SELL_TRADE_EVENT) {
            for c in self.circles.iter_mut().filter(|c| c.live()) {
                c.exch.apply_sell_trade(ev)?;
            }
        }
        Ok(())
    }

    fn drop_stale(&mut self) {
        if self.dl_dirty {
            self.dl_dirty = false;
            self.dl_min = (i64::MAX, 0);
            for (i, c) in self.circles.iter().enumerate() {
                if c.dl != i64::MAX && (c.dl, i as u32) < self.dl_min {
                    self.dl_min = (c.dl, i as u32);
                }
            }
        }
        while let Some(&Reverse((ts, _, id, tag))) = self.heap.peek() {
            let (slot, ver) = ((tag % 4) as usize, tag / 4);
            let c = &self.circles[id as usize];
            if c.slot_ver[slot] == ver
                && c.slot_ts[slot] == ts
                && matches!(c.cs, Cs::Waiting | Cs::Unborn)
            {
                break;
            }
            self.heap.pop();
            self.st[10] += 1;
        }
        while let Some(&(id, tag)) = self.wnf_q.get(self.wnf_head) {
            let (slot, ver) = ((tag % 4) as usize, tag / 4);
            let c = &self.circles[id as usize];
            if c.slot_ver[slot] == ver
                && c.slot_ts[slot] == self.wnf_ts
                && matches!(c.cs, Cs::Waiting | Cs::Unborn)
            {
                break;
            }
            self.wnf_head += 1;
            self.st[10] += 1;
        }
    }

    fn top_key(&self) -> Option<(i64, u8, u32, u32)> {
        let h = self.heap.peek().map(|Reverse(k)| *k);
        let m = match (h, self.wnf_top()) {
            (Some(a), Some(b)) => Some(a.min(b)),
            (a, b) => a.or(b),
        };
        match (m, self.dl_top()) {
            (Some(a), Some(b)) => Some(a.min(b)),
            (a, b) => a.or(b),
        }
    }

    fn pop_key(&mut self) -> Option<(i64, u8, u32, u32)> {
        let k = self.top_key()?;
        if self.dl_top() == Some(k) {
        } else if self.wnf_top() == Some(k) {
            self.st[19] += 1;
            self.wnf_head += 1;
        } else {
            self.heap.pop();
        }
        Some(k)
    }

    fn dl_top(&self) -> Option<(i64, u8, u32, u32)> {
        (self.dl_min.0 != i64::MAX).then_some((self.dl_min.0, K_WAKE, self.dl_min.1, 0))
    }

    /// Ближайшее пробуждение из очереди `wnf_q` (после `drop_stale`).
    fn wnf_top(&self) -> Option<(i64, u8, u32, u32)> {
        self.wnf_q
            .get(self.wnf_head)
            .map(|&(id, tag)| (self.wnf_ts, K_WAKE, id, tag))
    }

    /// Прогон до конца: результаты кругов — в порядке завершения.
    pub fn run(&mut self) -> Result<Vec<(u32, R)>, BacktestError> {
        self.ev_ld = self.advance_local();
        self.ev_ed = self.advance_exch();
        for id in 0..self.circles.len() {
            match self.circles[id].start {
                None => self.resume(id, self.first_ts),
                Some(t0) => self.set_slot(id, S_WAKE, t0),
            }
        }
        loop {
            self.drop_stale();
            let feed_ts = self.ev_ld.min(self.ev_ed);
            let feed_kind = if self.ev_ld <= self.ev_ed {
                K_LOCAL_DATA
            } else {
                K_EXCH_DATA
            };
            let feed_first = feed_ts != i64::MAX
                && match self.top_key() {
                    None => true,
                    Some((ts, kind, _, _)) => (feed_ts, feed_kind) < (ts, kind),
                };
            if feed_first {
                let now = feed_ts;
                if feed_kind == K_LOCAL_DATA {
                    let ev = self.rows[self.local_row.expect("строка")].clone();
                    let t = self.timed.then(std::time::Instant::now);
                    self.process_local(&ev);
                    if let Some(t) = t {
                        self.st[12] += t.elapsed().as_nanos() as u64;
                    }
                    self.ev_ld = self.advance_local();
                    for id in 0..self.circles.len() {
                        if self.wflag[id] == 3 {
                            let c = &mut self.circles[id];
                            c.bound = now;
                            c.result = ElapseResult::MarketFeed;
                            self.st[11] += 1;
                            self.dl_clear(id);
                            self.set_wake_wnf(id, now);
                        }
                    }
                } else {
                    let ev = self.rows[self.exch_row.expect("строка")].clone();
                    let t = self.timed.then(std::time::Instant::now);
                    self.process_exch(&ev)?;
                    if let Some(t) = t {
                        self.st[13] += t.elapsed().as_nanos() as u64;
                    }
                    self.ev_ed = self.advance_exch();
                    for id in 0..self.circles.len() {
                        if self.wflag[id] != 0 {
                            let lo = self.circles[id].exch.earliest_send_order_timestamp();
                            self.set_slot(id, S_LO, lo);
                        }
                    }
                }
                if self.ev_ld == i64::MAX && self.ev_ed == i64::MAX {
                    for id in 0..self.circles.len() {
                        self.check_end(id, now);
                    }
                }
                continue;
            }
            let Some((ts, kind, id, _)) = self.pop_key() else {
                break;
            };
            let id = id as usize;
            match kind {
                K_LOCAL_ORDER => {
                    self.st[6] += 1;
                    let c = &mut self.circles[id];
                    let wait_id = match c.wait {
                        WaitOrderResponse::Specified { order_id, .. } => Some(order_id),
                        _ => None,
                    };
                    if c.local.process_recv_order(ts, wait_id)? || c.wait == WaitOrderResponse::Any
                    {
                        c.bound = ts;
                        if c.wnf {
                            c.result = ElapseResult::OrderResponse;
                        }
                        self.dl_clear(id);
                        self.set_slot(id, S_WAKE, ts);
                    }
                    let lo = self.circles[id].local.earliest_recv_order_timestamp();
                    self.set_slot(id, S_LO, lo);
                    self.check_end(id, ts);
                }
                K_EXCH_ORDER => {
                    self.st[7] += 1;
                    let c = &mut self.circles[id];
                    let _ = c.exch.process_recv_order(ts, None)?;
                    let eo = c.exch.earliest_recv_order_timestamp();
                    let lo = c.exch.earliest_send_order_timestamp();
                    self.set_slot(id, S_EO, eo);
                    self.set_slot(id, S_LO, lo);
                    self.check_end(id, ts);
                }
                _ if self.circles[id].cs == Cs::Unborn => {
                    self.st[8] += 1;
                    self.birth(id, ts);
                }
                _ => {
                    self.st[9] += 1;
                    let c = &mut self.circles[id];
                    if c.eod {
                        c.out = ElapseResult::EndOfData;
                    } else {
                        c.cur_ts = c.bound;
                        c.out = c.result;
                    }
                    self.set_slot(id, S_WAKE, i64::MAX);
                    self.resume(id, ts);
                }
            }
        }
        if std::env::var_os("ALPHA_SHARED_STATS").is_some() {
            let mut line = format!(
                "SHARED_STATS circles={} rows={}",
                self.circles.len(),
                self.rows.len()
            );
            for (n, v) in ST_NAMES.iter().zip(self.st) {
                line.push_str(&format!(" {n}={v}"));
            }
            eprintln!("{line}");
        }
        Ok(std::mem::take(&mut self.results))
    }
}

#[cfg(test)]
mod tests;
