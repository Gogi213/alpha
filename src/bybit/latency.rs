//! Замер задержек торгового пути на живом счёте (2026-09-19, задача владельца:
//! «измерить RTT, скорость постановки лимитки, скорость снятия, скорость
//! исполнения тейкера — на 10 баксов всё»).
//!
//! Один цикл — пять ступеней, каждая даёт свои точки (`Sample`):
//! 1. `rest_time` — `GET /v5/market/time` без подписи: сеть + HTTP, база отсчёта;
//! 2. `ws_ping` — `ping`→`pong` приватного стрима: сеть по уже открытому сокету;
//! 3. лимитка post-only далеко от середины (`probe::far_price_e9`, как в 6.2):
//!    `place_ack` (отправка → ответ транспорта) и `place_new` (отправка →
//!    кадр `order` со статусом `New` в приватном стриме);
//! 4. её снятие: `cancel_ack`, `cancel_done` (→ кадр `Cancelled`);
//! 5. тейкер: рыночный ордер на заданный номинал (`taker_ack`, `taker_exec` —
//!    → кадр `execution`, `taker_filled` — → кадр `order` `Filled`), и сразу
//!    обратный `reduceOnly` рыночный — позиция плоская к концу ступени, обе
//!    ноги дают по точке.
//!
//! Ступени 3–5 идут двумя транспортами — REST (`/v5/order/*`) и WS trade
//! (`order.create`/`order.cancel`), колонка `via`. Приватный стрим читает
//! отдельный поток и штампует **приём** каждого кадра `Instant`'ом (иначе кадр,
//! пришедший пока мы ждём HTTP-ответ, получил бы метку позже истины).
//!
//! Метки — `Instant` (монотонные), не настенные часы: тот же довод, что у
//! `probe::Cycle`. Цена и размер — целые 1e-9 (A1), никаких `f64`.
//! Ключи — только `BYBIT_API_KEY`/`BYBIT_API_SECRET` (Decision 12); этот файл
//! их не видит вовсе — подпись живёт в реализациях транспортов
//! (`commands/lob/latency.rs`), а ядро проверяется фейками без сети.
//!
//! Это не горячий путь: одна отправка в секунду, аллокации разрешены.

use std::collections::VecDeque;
use std::time::{Duration, Instant};

use serde::Serialize;

use crate::bybit::probe::{far_price_e9, percentile_ns, OrderSide};
use crate::bybit::rest::parse_orderbook_snapshot;

pub const TIME_PATH: &str = "/v5/market/time";
pub const ORDERBOOK_PATH: &str = "/v5/market/orderbook";
pub const INSTRUMENTS_PATH: &str = "/v5/market/instruments-info";
pub const CREATE_PATH: &str = "/v5/order/create";
pub const CANCEL_PATH: &str = "/v5/order/cancel";
pub const CANCEL_ALL_PATH: &str = "/v5/order/cancel-all";
pub const POSITION_LIST_PATH: &str = "/v5/position/list";
pub const OP_CREATE: &str = "order.create";
pub const OP_CANCEL: &str = "order.cancel";
const CATEGORY: &str = "linear";

/// Адреса трёх контуров Bybit v5 — факты протокола биржи, не числа.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Endpoints {
    pub rest: &'static str,
    pub ws_trade: &'static str,
    pub ws_private: &'static str,
}

pub const MAINNET: Endpoints = Endpoints {
    rest: "https://api.bybit.com",
    ws_trade: "wss://stream.bybit.com/v5/trade",
    ws_private: "wss://stream.bybit.com/v5/private",
};
pub const TESTNET: Endpoints = Endpoints {
    rest: "https://api-testnet.bybit.com",
    ws_trade: "wss://stream-testnet.bybit.com/v5/trade",
    ws_private: "wss://stream-testnet.bybit.com/v5/private",
};
/// Demo Trading — счёт с виртуальными деньгами на основной площадке
/// (`api-demo`); WS trade у демо-контура биржей не задокументирован — если
/// сокет не откроется, ступени `via=ws` пропускаются с записью в `errors`.
pub const DEMO: Endpoints = Endpoints {
    rest: "https://api-demo.bybit.com",
    ws_trade: "wss://stream-demo.bybit.com/v5/trade",
    ws_private: "wss://stream-demo.bybit.com/v5/private",
};

pub fn endpoints_for(net: &str) -> Option<Endpoints> {
    match net {
        "mainnet" => Some(MAINNET),
        "testnet" => Some(TESTNET),
        "demo" => Some(DEMO),
        _ => None,
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LatencyError {
    Transport(String),
    Decode(String),
    Rejected { ret_code: i64, ret_msg: String },
    Timeout(&'static str),
    Size(String),
}

impl std::fmt::Display for LatencyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LatencyError::Transport(s) => write!(f, "транспорт: {s}"),
            LatencyError::Decode(s) => write!(f, "разбор: {s}"),
            LatencyError::Rejected { ret_code, ret_msg } => {
                write!(f, "биржа отказала: retCode={ret_code} retMsg={ret_msg}")
            }
            LatencyError::Timeout(what) => write!(f, "таймаут: {what}"),
            LatencyError::Size(s) => write!(f, "размер: {s}"),
        }
    }
}

// ---------------------------------------------------------------------------
// Транспорты за трейтами: подпись — внутри реализаций, ядро ключей не видит.
// ---------------------------------------------------------------------------

pub trait Rest {
    /// `query` — уже собранная строка `a=b&c=d` (пустая — без `?`).
    fn get_public(&mut self, path: &str, query: &str) -> Result<String, LatencyError>;
    fn get_signed(&mut self, path: &str, query: &str) -> Result<String, LatencyError>;
    fn post_signed(&mut self, path: &str, body: &str) -> Result<String, LatencyError>;
}

pub trait TradeWs {
    fn send(&mut self, frame: &str) -> Result<(), LatencyError>;
    fn recv(&mut self, timeout: Duration) -> Result<String, LatencyError>;
}

/// Один разобранный кадр приватного стрима.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PrivateFrame {
    pub topic: Option<String>,
    pub op: Option<String>,
    pub ret_msg: Option<String>,
    pub rows: Vec<PrivateRow>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct PrivateRow {
    pub order_id: String,
    pub order_link_id: String,
    pub order_status: String,
    pub exec_type: String,
}

pub fn parse_private_frame(raw: &str) -> PrivateFrame {
    let v: serde_json::Value = match serde_json::from_str(raw) {
        Ok(v) => v,
        Err(_) => {
            return PrivateFrame {
                topic: None,
                op: None,
                ret_msg: None,
                rows: Vec::new(),
            }
        }
    };
    let s = |x: &serde_json::Value| x.as_str().map(str::to_string);
    let rows = v["data"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|r| PrivateRow {
                    order_id: r["orderId"].as_str().unwrap_or("").to_string(),
                    order_link_id: r["orderLinkId"].as_str().unwrap_or("").to_string(),
                    order_status: r["orderStatus"].as_str().unwrap_or("").to_string(),
                    exec_type: r["execType"].as_str().unwrap_or("").to_string(),
                })
                .collect()
        })
        .unwrap_or_default();
    PrivateFrame {
        topic: s(&v["topic"]),
        op: s(&v["op"]),
        ret_msg: s(&v["ret_msg"]),
        rows,
    }
}

impl PrivateFrame {
    pub fn is_pong(&self) -> bool {
        self.op.as_deref() == Some("pong") || self.ret_msg.as_deref() == Some("pong")
    }
    pub fn has_order(&self, link: &str, status: &str) -> bool {
        self.topic.as_deref() == Some("order")
            && self
                .rows
                .iter()
                .any(|r| r.order_link_id == link && r.order_status == status)
    }
    pub fn has_trade_exec(&self, link: &str) -> bool {
        self.topic.as_deref() == Some("execution")
            && self
                .rows
                .iter()
                .any(|r| r.order_link_id == link && r.exec_type == "Trade")
    }
}

/// Источник штампованных кадров приватного стрима: реализация читает сокет в
/// своём потоке и отдаёт `(момент приёма, сырой кадр)`; ядро держит буфер
/// несовпавших кадров (`Feed`), чтобы кадр `order Filled`, пришедший раньше
/// `execution`, не потерялся, пока ждём второй. `None` — таймаут без кадра.
pub trait PrivateSource {
    fn send(&mut self, frame: &str) -> Result<(), LatencyError>;
    fn recv_timeout(
        &mut self,
        timeout: Duration,
    ) -> Result<Option<(Instant, String)>, LatencyError>;
}

/// Сколько несовпавших кадров держать: стрим общий на счёт, туда падают и
/// чужие ордера; буфер ограничен, чтобы прогон на час не рос без предела.
const FEED_BUFFER: usize = 1024;

pub struct Feed<S: PrivateSource> {
    src: S,
    buf: VecDeque<(Instant, PrivateFrame)>,
}

impl<S: PrivateSource> Feed<S> {
    pub fn new(src: S) -> Self {
        Self {
            src,
            buf: VecDeque::new(),
        }
    }
    pub fn send(&mut self, frame: &str) -> Result<(), LatencyError> {
        self.src.send(frame)
    }
    /// Первый кадр, для которого `pred` истинен: сначала буфер, потом сокет до
    /// `timeout`. Несовпавшие — в буфер.
    pub fn wait(
        &mut self,
        timeout: Duration,
        what: &'static str,
        mut pred: impl FnMut(&PrivateFrame) -> bool,
    ) -> Result<Instant, LatencyError> {
        if let Some(i) = self.buf.iter().position(|(_, f)| pred(f)) {
            let (at, _) = self.buf.remove(i).expect("позиция из iter().position");
            return Ok(at);
        }
        let deadline = Instant::now() + timeout;
        loop {
            let now = Instant::now();
            if now >= deadline {
                return Err(LatencyError::Timeout(what));
            }
            match self.src.recv_timeout(deadline - now)? {
                None => return Err(LatencyError::Timeout(what)),
                Some((at, raw)) => {
                    let frame = parse_private_frame(&raw);
                    if pred(&frame) {
                        return Ok(at);
                    }
                    if self.buf.len() >= FEED_BUFFER {
                        self.buf.pop_front();
                    }
                    self.buf.push_back((at, frame));
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Тела запросов — одно место для REST-тела и `args` кадра WS trade.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OrderBody {
    pub category: &'static str,
    pub symbol: String,
    pub side: &'static str,
    pub order_type: &'static str,
    pub qty: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub price: Option<String>,
    pub time_in_force: &'static str,
    pub order_link_id: String,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub reduce_only: bool,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CancelBody {
    pub category: &'static str,
    pub symbol: String,
    pub order_id: String,
}

fn side_str(side: OrderSide) -> &'static str {
    match side {
        OrderSide::Buy => "Buy",
        OrderSide::Sell => "Sell",
    }
}

pub fn opposite(side: OrderSide) -> OrderSide {
    match side {
        OrderSide::Buy => OrderSide::Sell,
        OrderSide::Sell => OrderSide::Buy,
    }
}

/// 1e-9 → десятичная строка (`bybit::ws::parse_e9` наоборот); своя копия, как
/// в `trade_ws.rs`, приколота тестом к тому же значению.
pub fn format_e9(v: i64) -> String {
    let neg = v < 0;
    let v = v.unsigned_abs();
    let int_part = v / 1_000_000_000;
    let frac_part = v % 1_000_000_000;
    let mut s = if frac_part == 0 {
        int_part.to_string()
    } else {
        let mut frac = format!("{frac_part:09}");
        while frac.ends_with('0') {
            frac.pop();
        }
        format!("{int_part}.{frac}")
    };
    if neg {
        s.insert(0, '-');
    }
    s
}

pub fn post_only_body(
    symbol: &str,
    side: OrderSide,
    qty_e9: i64,
    price_e9: i64,
    link: &str,
) -> OrderBody {
    OrderBody {
        category: CATEGORY,
        symbol: symbol.to_string(),
        side: side_str(side),
        order_type: "Limit",
        qty: format_e9(qty_e9),
        price: Some(format_e9(price_e9)),
        time_in_force: "PostOnly",
        order_link_id: link.to_string(),
        reduce_only: false,
    }
}

pub fn market_body(
    symbol: &str,
    side: OrderSide,
    qty_e9: i64,
    link: &str,
    reduce_only: bool,
) -> OrderBody {
    OrderBody {
        category: CATEGORY,
        symbol: symbol.to_string(),
        side: side_str(side),
        order_type: "Market",
        qty: format_e9(qty_e9),
        price: None,
        time_in_force: "IOC",
        order_link_id: link.to_string(),
        reduce_only,
    }
}

/// Кадр WS trade по документации биржи: `reqId`, `header` с меткой времени и
/// окном (аутентификация — один раз при подключении, `auth`), `op`, `args`.
pub fn ws_frame<A: Serialize>(
    req_id: &str,
    op: &'static str,
    args: &A,
    timestamp_ms: i64,
    recv_window_ms: u32,
) -> Result<String, LatencyError> {
    let frame = serde_json::json!({
        "reqId": req_id,
        "header": {
            "X-BAPI-TIMESTAMP": timestamp_ms.to_string(),
            "X-BAPI-RECV-WINDOW": recv_window_ms.to_string(),
        },
        "op": op,
        "args": [args],
    });
    serde_json::to_string(&frame).map_err(|e| LatencyError::Decode(e.to_string()))
}

/// Ответ биржи на REST-ордер или кадр WS trade: `retCode` и `orderId`
/// (`result.orderId` у REST, `data.orderId` у WS).
pub fn parse_order_ack(body: &str) -> Result<String, LatencyError> {
    let v: serde_json::Value =
        serde_json::from_str(body).map_err(|e| LatencyError::Decode(e.to_string()))?;
    let ret_code = v["retCode"].as_i64().unwrap_or(-1);
    if ret_code != 0 {
        return Err(LatencyError::Rejected {
            ret_code,
            ret_msg: v["retMsg"].as_str().unwrap_or("").to_string(),
        });
    }
    let id = v["result"]["orderId"]
        .as_str()
        .or_else(|| v["data"]["orderId"].as_str())
        .unwrap_or("");
    if id.is_empty() {
        return Err(LatencyError::Decode("нет orderId в ответе".to_string()));
    }
    Ok(id.to_string())
}

pub fn ws_frame_req_id(raw: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(raw).ok()?;
    v["reqId"].as_str().map(str::to_string)
}

/// Проверяет только `retCode` (ответы `cancel`, `cancel-all`).
pub fn check_ret_code(body: &str) -> Result<(), LatencyError> {
    let v: serde_json::Value =
        serde_json::from_str(body).map_err(|e| LatencyError::Decode(e.to_string()))?;
    let ret_code = v["retCode"].as_i64().unwrap_or(-1);
    if ret_code != 0 {
        return Err(LatencyError::Rejected {
            ret_code,
            ret_msg: v["retMsg"].as_str().unwrap_or("").to_string(),
        });
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Размер: номинал → qty по шагу лота и минимумам инструмента.
// ---------------------------------------------------------------------------

/// Фильтры инструмента из `instruments-info` (`priceFilter.tickSize`,
/// `lotSizeFilter.{qtyStep,minOrderQty,minNotionalValue}`), 1e-9.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Filters {
    pub tick_e9: i64,
    pub qty_step_e9: i64,
    pub min_qty_e9: i64,
    pub min_notional_e9: i64,
}

pub fn parse_filters(body: &str, symbol: &str) -> Result<Filters, LatencyError> {
    let page = crate::bybit::rest::parse_instruments_info(body)
        .map_err(|e| LatencyError::Decode(format!("{e:?}")))?;
    let inst = page
        .instruments
        .into_iter()
        .find(|i| i.symbol == symbol)
        .ok_or_else(|| LatencyError::Decode(format!("{symbol} нет в instruments-info")))?;
    Ok(Filters {
        tick_e9: inst.tick_e9,
        qty_step_e9: inst.qty_step_e9,
        min_qty_e9: inst.min_order_qty_e9,
        min_notional_e9: inst.min_notional_value_e9,
    })
}

fn ceil_div_i128(a: i128, b: i128) -> i128 {
    (a + b - 1) / b
}

/// Наименьшее qty (1e-9), кратное шагу, не ниже `minOrderQty`, с номиналом
/// `qty × price ≥ max(notional, minNotionalValue)`. Целочисленно (A1).
pub fn qty_for_notional(notional_e9: i64, price_e9: i64, f: Filters) -> Result<i64, LatencyError> {
    if price_e9 <= 0 || f.qty_step_e9 <= 0 {
        return Err(LatencyError::Size(format!(
            "price_e9={price_e9} qty_step_e9={}",
            f.qty_step_e9
        )));
    }
    let target = i128::from(notional_e9.max(f.min_notional_e9));
    // qty = target / price в 1e-9: (target_e9 × 1e9) / price_e9, вверх.
    let raw = ceil_div_i128(target * 1_000_000_000, i128::from(price_e9));
    let step = i128::from(f.qty_step_e9);
    let mut qty = ceil_div_i128(raw, step) * step;
    let min_qty = i128::from(f.min_qty_e9);
    if qty < min_qty {
        qty = ceil_div_i128(min_qty, step) * step;
    }
    i64::try_from(qty).map_err(|_| LatencyError::Size("qty не влезает в i64".to_string()))
}

/// Номинал ордера (1e-9 USDT) при данной цене — для отчёта.
pub fn notional_e9(qty_e9: i64, price_e9: i64) -> i64 {
    let n = i128::from(qty_e9) * i128::from(price_e9) / 1_000_000_000;
    i64::try_from(n).unwrap_or(i64::MAX)
}

/// Цена, кратная тику, в сторону «прочь от книги»: покупка — вниз, продажа —
/// вверх. Середина `(bid + ask) / 2` при нечётном числе тиков между ними не
/// кратна тику, а биржа такую цену отвергает.
pub fn align_to_tick(price_e9: i64, tick_e9: i64, side: OrderSide) -> i64 {
    if tick_e9 <= 0 {
        return price_e9;
    }
    let floored = price_e9.div_euclid(tick_e9) * tick_e9;
    match side {
        OrderSide::Buy => floored,
        OrderSide::Sell if floored == price_e9 => price_e9,
        OrderSide::Sell => floored + tick_e9,
    }
}

pub fn mid_from_orderbook(body: &str) -> Result<i64, LatencyError> {
    let snap =
        parse_orderbook_snapshot(body).map_err(|e| LatencyError::Decode(format!("{e:?}")))?;
    let bid = snap.bids.first().map(|l| l.0);
    let ask = snap.asks.first().map(|l| l.0);
    match (bid, ask) {
        (Some(b), Some(a)) if b > 0 && a > 0 => Ok((b + a) / 2),
        _ => Err(LatencyError::Decode("пустая сторона книги".to_string())),
    }
}

/// `size` и сторона открытой позиции по символу из `/v5/position/list`
/// (`result.list[]` с `size ≠ 0`); `None` — плоско.
pub fn parse_position(body: &str, symbol: &str) -> Result<Option<(OrderSide, i64)>, LatencyError> {
    check_ret_code(body)?;
    let v: serde_json::Value =
        serde_json::from_str(body).map_err(|e| LatencyError::Decode(e.to_string()))?;
    let list = v["result"]["list"].as_array().cloned().unwrap_or_default();
    for row in list {
        if row["symbol"].as_str() != Some(symbol) {
            continue;
        }
        let size = row["size"]
            .as_str()
            .and_then(crate::bybit::ws::parse_e9)
            .unwrap_or(0);
        if size == 0 {
            continue;
        }
        let side = match row["side"].as_str() {
            Some("Buy") => OrderSide::Buy,
            Some("Sell") => OrderSide::Sell,
            other => return Err(LatencyError::Decode(format!("сторона позиции {other:?}"))),
        };
        return Ok(Some((side, size)));
    }
    Ok(None)
}

// ---------------------------------------------------------------------------
// Точки, сводка.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Sample {
    pub cycle: usize,
    pub stage: &'static str,
    pub via: &'static str,
    pub ns: i64,
}

pub const VIA_REST: &str = "rest";
pub const VIA_WS: &str = "ws";
pub const VIA_PRIVATE: &str = "private";

/// Порядок ступеней в отчёте — порядок цикла.
pub const STAGES: [&str; 10] = [
    "rest_time",
    "ws_ping",
    "place_ack",
    "place_new",
    "cancel_ack",
    "cancel_done",
    "taker_ack",
    "taker_exec",
    "taker_filled",
    "cycle_total",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StageSummary {
    pub stage: &'static str,
    pub via: &'static str,
    pub n: usize,
    pub median_ns: i64,
    pub p95_ns: i64,
    pub p99_ns: i64,
    pub max_ns: i64,
}

pub fn summarize(samples: &[Sample]) -> Vec<StageSummary> {
    let mut out = Vec::new();
    for stage in STAGES {
        for via in [VIA_REST, VIA_WS, VIA_PRIVATE] {
            let xs: Vec<i64> = samples
                .iter()
                .filter(|s| s.stage == stage && s.via == via)
                .map(|s| s.ns)
                .collect();
            if xs.is_empty() {
                continue;
            }
            out.push(StageSummary {
                stage,
                via,
                n: xs.len(),
                median_ns: percentile_ns(&xs, 50),
                p95_ns: percentile_ns(&xs, 95),
                p99_ns: percentile_ns(&xs, 99),
                max_ns: xs.iter().copied().max().unwrap_or(0),
            });
        }
    }
    out
}

/// Таблица сводки в миллисекундах — только для печати; сами точки в CSV
/// остаются целыми наносекундами.
pub fn format_summary(rows: &[StageSummary]) -> String {
    let mut s = String::from("stage         via      n   median_ms   p95_ms   p99_ms   max_ms\n");
    for r in rows {
        s.push_str(&format!(
            "{:<13} {:<7} {:>4} {:>10.2} {:>8.2} {:>8.2} {:>8.2}\n",
            r.stage,
            r.via,
            r.n,
            r.median_ns as f64 / 1e6,
            r.p95_ns as f64 / 1e6,
            r.p99_ns as f64 / 1e6,
            r.max_ns as f64 / 1e6,
        ));
    }
    s
}

// ---------------------------------------------------------------------------
// Цикл.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct Plan {
    pub symbol: String,
    pub side: OrderSide,
    pub qty_e9: i64,
    pub filters: Filters,
    pub ticks_from_mid: i64,
    pub recv_window_ms: u32,
    /// Ожидание кадра приватного стрима и ответа WS trade.
    pub wait: Duration,
    pub taker: bool,
}

/// Настенное время для `header` WS trade — из `probe`, чтобы не заводить
/// вторую копию.
fn wall_ms() -> Result<i64, LatencyError> {
    crate::bybit::probe::wall_clock_timestamp_ms()
        .map_err(|e| LatencyError::Transport(format!("{e:?}")))
}

pub struct Bench<'a, R: Rest, T: TradeWs, S: PrivateSource> {
    pub rest: &'a mut R,
    pub trade: Option<&'a mut T>,
    pub feed: &'a mut Feed<S>,
    pub plan: &'a Plan,
    pub samples: Vec<Sample>,
    pub errors: Vec<String>,
}

#[allow(clippy::cast_possible_truncation)]
fn ns(from: Instant, to: Instant) -> i64 {
    to.duration_since(from).as_nanos() as i64
}

impl<'a, R: Rest, T: TradeWs, S: PrivateSource> Bench<'a, R, T, S> {
    pub fn new(
        rest: &'a mut R,
        trade: Option<&'a mut T>,
        feed: &'a mut Feed<S>,
        plan: &'a Plan,
    ) -> Self {
        Self {
            rest,
            trade,
            feed,
            plan,
            samples: Vec::new(),
            errors: Vec::new(),
        }
    }

    fn push(&mut self, cycle: usize, stage: &'static str, via: &'static str, ns: i64) {
        self.samples.push(Sample {
            cycle,
            stage,
            via,
            ns,
        });
    }

    fn note(&mut self, cycle: usize, what: &str, e: &LatencyError) {
        self.errors.push(format!("cycle {cycle} {what}: {e}"));
    }

    fn mid(&mut self) -> Result<i64, LatencyError> {
        let q = format!("category={CATEGORY}&symbol={}&limit=1", self.plan.symbol);
        let body = self.rest.get_public(ORDERBOOK_PATH, &q)?;
        mid_from_orderbook(&body)
    }

    /// Один цикл целиком; ошибки ступеней не прерывают цикл — записываются,
    /// ступень пропускается. Возвращает, была ли ошибка в тейкере: тогда
    /// вызывающий обязан выровнять позицию (`flatten`).
    pub fn run_cycle(&mut self, cycle: usize) -> bool {
        let t_cycle = Instant::now();
        // 1. REST time.
        let t0 = Instant::now();
        match self.rest.get_public(TIME_PATH, "") {
            Ok(_) => {
                let t1 = Instant::now();
                self.push(cycle, "rest_time", VIA_REST, ns(t0, t1));
            }
            Err(e) => self.note(cycle, "rest_time", &e),
        }
        // 2. WS ping на приватном стриме.
        let t0 = Instant::now();
        let wait = self.plan.wait;
        match self
            .feed
            .send(r#"{"op":"ping"}"#)
            .and_then(|()| self.feed.wait(wait, "pong", PrivateFrame::is_pong))
        {
            Ok(at) => self.push(cycle, "ws_ping", VIA_PRIVATE, ns(t0, at)),
            Err(e) => self.note(cycle, "ws_ping", &e),
        }
        // 3–4. Лимитка и снятие: REST, затем WS trade.
        let mid = match self.mid() {
            Ok(m) => m,
            Err(e) => {
                self.note(cycle, "mid", &e);
                return false;
            }
        };
        let tick = self.plan.filters.tick_e9;
        let price = align_to_tick(
            far_price_e9(mid, tick, self.plan.ticks_from_mid, self.plan.side),
            tick,
            self.plan.side,
        );
        if let Err(e) = self.limit_rest(cycle, price) {
            self.note(cycle, "limit rest", &e);
        }
        if self.trade.is_some() {
            if let Err(e) = self.limit_ws(cycle, price) {
                self.note(cycle, "limit ws", &e);
            }
        }
        // 5. Тейкер: две ноги REST, две ноги WS.
        let mut taker_failed = false;
        if self.plan.taker {
            if let Err(e) = self.taker_rest(cycle) {
                self.note(cycle, "taker rest", &e);
                taker_failed = true;
            }
            if self.trade.is_some() && !taker_failed {
                if let Err(e) = self.taker_ws(cycle) {
                    self.note(cycle, "taker ws", &e);
                    taker_failed = true;
                }
            }
        }
        let t_end = Instant::now();
        self.push(cycle, "cycle_total", VIA_REST, ns(t_cycle, t_end));
        taker_failed
    }

    fn limit_rest(&mut self, cycle: usize, price_e9: i64) -> Result<(), LatencyError> {
        let link = format!("lat{cycle}r");
        let body = post_only_body(
            &self.plan.symbol,
            self.plan.side,
            self.plan.qty_e9,
            price_e9,
            &link,
        );
        let json = serde_json::to_string(&body).map_err(|e| LatencyError::Decode(e.to_string()))?;
        let t0 = Instant::now();
        let resp = self.rest.post_signed(CREATE_PATH, &json)?;
        let t1 = Instant::now();
        let order_id = parse_order_ack(&resp)?;
        self.push(cycle, "place_ack", VIA_REST, ns(t0, t1));
        let wait = self.plan.wait;
        let at = self
            .feed
            .wait(wait, "order New (rest)", |f| f.has_order(&link, "New"))?;
        self.push(cycle, "place_new", VIA_REST, ns(t0, at));
        let cancel = CancelBody {
            category: CATEGORY,
            symbol: self.plan.symbol.clone(),
            order_id,
        };
        let json =
            serde_json::to_string(&cancel).map_err(|e| LatencyError::Decode(e.to_string()))?;
        let t0 = Instant::now();
        let resp = self.rest.post_signed(CANCEL_PATH, &json)?;
        let t1 = Instant::now();
        check_ret_code(&resp)?;
        self.push(cycle, "cancel_ack", VIA_REST, ns(t0, t1));
        let at = self.feed.wait(wait, "order Cancelled (rest)", |f| {
            f.has_order(&link, "Cancelled")
        })?;
        self.push(cycle, "cancel_done", VIA_REST, ns(t0, at));
        Ok(())
    }

    /// Отправка кадра и ожидание ответа с тем же `reqId` (чужие кадры —
    /// pong и т. п. — пропускаются).
    fn ws_roundtrip(
        &mut self,
        req_id: &str,
        frame: &str,
    ) -> Result<(Instant, Instant, String), LatencyError> {
        let wait = self.plan.wait;
        let trade = self
            .trade
            .as_deref_mut()
            .ok_or_else(|| LatencyError::Transport("нет WS trade".to_string()))?;
        let t0 = Instant::now();
        trade.send(frame)?;
        let deadline = t0 + wait;
        loop {
            let now = Instant::now();
            if now >= deadline {
                return Err(LatencyError::Timeout("ответ WS trade"));
            }
            let raw = trade.recv(deadline - now)?;
            let t1 = Instant::now();
            if ws_frame_req_id(&raw).as_deref() == Some(req_id) {
                return Ok((t0, t1, raw));
            }
        }
    }

    fn limit_ws(&mut self, cycle: usize, price_e9: i64) -> Result<(), LatencyError> {
        let link = format!("lat{cycle}w");
        let body = post_only_body(
            &self.plan.symbol,
            self.plan.side,
            self.plan.qty_e9,
            price_e9,
            &link,
        );
        let req = format!("c{cycle}");
        let frame = ws_frame(&req, OP_CREATE, &body, wall_ms()?, self.plan.recv_window_ms)?;
        let (t0, t1, raw) = self.ws_roundtrip(&req, &frame)?;
        let order_id = parse_order_ack(&raw)?;
        self.push(cycle, "place_ack", VIA_WS, ns(t0, t1));
        let wait = self.plan.wait;
        let at = self
            .feed
            .wait(wait, "order New (ws)", |f| f.has_order(&link, "New"))?;
        self.push(cycle, "place_new", VIA_WS, ns(t0, at));
        let cancel = CancelBody {
            category: CATEGORY,
            symbol: self.plan.symbol.clone(),
            order_id,
        };
        let req = format!("x{cycle}");
        let frame = ws_frame(
            &req,
            OP_CANCEL,
            &cancel,
            wall_ms()?,
            self.plan.recv_window_ms,
        )?;
        let (t0, t1, raw) = self.ws_roundtrip(&req, &frame)?;
        check_ret_code(&raw)?;
        self.push(cycle, "cancel_ack", VIA_WS, ns(t0, t1));
        let at = self.feed.wait(wait, "order Cancelled (ws)", |f| {
            f.has_order(&link, "Cancelled")
        })?;
        self.push(cycle, "cancel_done", VIA_WS, ns(t0, at));
        Ok(())
    }

    fn taker_leg_rest(
        &mut self,
        cycle: usize,
        side: OrderSide,
        link: &str,
        reduce_only: bool,
    ) -> Result<(), LatencyError> {
        let body = market_body(&self.plan.symbol, side, self.plan.qty_e9, link, reduce_only);
        let json = serde_json::to_string(&body).map_err(|e| LatencyError::Decode(e.to_string()))?;
        let t0 = Instant::now();
        let resp = self.rest.post_signed(CREATE_PATH, &json)?;
        let t1 = Instant::now();
        parse_order_ack(&resp)?;
        self.push(cycle, "taker_ack", VIA_REST, ns(t0, t1));
        self.taker_confirm(cycle, VIA_REST, link, t0)
    }

    fn taker_confirm(
        &mut self,
        cycle: usize,
        via: &'static str,
        link: &str,
        t0: Instant,
    ) -> Result<(), LatencyError> {
        let wait = self.plan.wait;
        let at = self
            .feed
            .wait(wait, "execution Trade", |f| f.has_trade_exec(link))?;
        self.push(cycle, "taker_exec", via, ns(t0, at));
        let at = self
            .feed
            .wait(wait, "order Filled", |f| f.has_order(link, "Filled"))?;
        self.push(cycle, "taker_filled", via, ns(t0, at));
        Ok(())
    }

    fn taker_rest(&mut self, cycle: usize) -> Result<(), LatencyError> {
        let side = self.plan.side;
        self.taker_leg_rest(cycle, side, &format!("lat{cycle}tr"), false)?;
        self.taker_leg_rest(cycle, opposite(side), &format!("lat{cycle}trx"), true)
    }

    fn taker_leg_ws(
        &mut self,
        cycle: usize,
        side: OrderSide,
        link: &str,
        reduce_only: bool,
        req: &str,
    ) -> Result<(), LatencyError> {
        let body = market_body(&self.plan.symbol, side, self.plan.qty_e9, link, reduce_only);
        let frame = ws_frame(req, OP_CREATE, &body, wall_ms()?, self.plan.recv_window_ms)?;
        let (t0, t1, raw) = self.ws_roundtrip(req, &frame)?;
        parse_order_ack(&raw)?;
        self.push(cycle, "taker_ack", VIA_WS, ns(t0, t1));
        self.taker_confirm(cycle, VIA_WS, link, t0)
    }

    fn taker_ws(&mut self, cycle: usize) -> Result<(), LatencyError> {
        let side = self.plan.side;
        self.taker_leg_ws(
            cycle,
            side,
            &format!("lat{cycle}tw"),
            false,
            &format!("t{cycle}"),
        )?;
        self.taker_leg_ws(
            cycle,
            opposite(side),
            &format!("lat{cycle}twx"),
            true,
            &format!("u{cycle}"),
        )
    }

    /// Страховка: снять все ордера символа и закрыть остаток позиции рынком.
    /// Зовётся после цикла с ошибкой тейкера и в конце прогона. Возвращает
    /// размер закрытого остатка (1e-9), `None` — было плоско.
    pub fn flatten(&mut self) -> Result<Option<i64>, LatencyError> {
        let cancel_all =
            serde_json::json!({"category": CATEGORY, "symbol": self.plan.symbol}).to_string();
        let resp = self.rest.post_signed(CANCEL_ALL_PATH, &cancel_all)?;
        check_ret_code(&resp)?;
        let q = format!("category={CATEGORY}&symbol={}", self.plan.symbol);
        let body = self.rest.get_signed(POSITION_LIST_PATH, &q)?;
        match parse_position(&body, &self.plan.symbol)? {
            None => Ok(None),
            Some((side, size_e9)) => {
                let body = market_body(&self.plan.symbol, opposite(side), size_e9, "latflat", true);
                let json = serde_json::to_string(&body)
                    .map_err(|e| LatencyError::Decode(e.to_string()))?;
                let resp = self.rest.post_signed(CREATE_PATH, &json)?;
                parse_order_ack(&resp)?;
                Ok(Some(size_e9))
            }
        }
    }
}

#[cfg(test)]
mod tests;
