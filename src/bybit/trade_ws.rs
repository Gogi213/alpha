//! RTT по WS trade (шаг 6.4, Decision 24).
//!
//! Тот же цикл из трёх меток `Instant`, что в `probe.rs` (шаг 6.2), но кадрами
//! WS trade (`wss://stream.bybit.com/v5/trade`, `order.create`/`order.cancel`),
//! а не REST `/v5/order/create`. Ордер тот же (`[ASSUMPTION H9]`): post-only
//! минимального размера далеко от середины, снимается немедленно. Подпись та же
//! (`sign.rs`, Decision 12: HMAC-SHA256, ключи только из окружения), статистика
//! та же (`probe.rs`: ближайший ранг, медиана и p95) — заменяется только
//! транспорт, поэтому шаг 6.2 не переоткрывается, а его число остаётся в отчёте
//! справочной колонкой с подписью транспорта.
//!
//! Две метки подтверждения — раздельно: приём ответным кадром trade-сокета и
//! исполнение приватным стримом `order`/`execution` (`wss://.../v5/private`).
//! В G4 идёт та, что соответствует событию, на которое реагирует модель
//! очереди (шаг 6.3 зависит от 6.4, а не от 6.2).
//!
//! Транспорт за синхронным трейтом `WsTradeTransport` — тем же приёмом, что
//! `PrivateRest` в `probe.rs` (там же причина: `async fn` в трейте тянет
//! пару взаимоисключающих предупреждений clippy без `#[allow]` на каждой
//! реализации). Ядро — сборка кадра, подпись, разбор подтверждения, статистика —
//! проверяется на фейковом транспорте без сети и без ключей; живой прогон —
//! отдельным `#[ignore]`-тестом (≥ 1000 живых циклов в песочнице невозможны).

use crate::bybit::conn::{Clock, SystemClock};
use crate::bybit::probe::{self, OrderSide, ProbeParams};
use crate::bybit::sign::Credentials;
use serde::{Deserialize, Serialize};
use std::time::Instant;

/// Публичный адрес WS trade Bybit v5 — факт протокола биржи (как имена
/// топиков в `ws.rs`), а не измеренное число: запрет изобретённых констант —
/// про них, не про адреса.
pub const TRADE_WS_URL: &str = "wss://stream.bybit.com/v5/trade";
/// Приватный стрим, несущий `order`/`execution`, — второй сокет живого цикла.
pub const PRIVATE_WS_URL: &str = "wss://stream.bybit.com/v5/private";
/// Операции trade-сокета, участвующие в замере (cм. Websocket Trade Guideline
/// биржи). `order.amend` в замере не участвует: цикл H9 — выставить и снять.
pub const OP_CREATE: &str = "order.create";
pub const OP_CANCEL: &str = "order.cancel";

/// Подписи транспортных колонок отчёта: done-condition 6.4 требует, чтобы
/// рядом с парой WS стояла та же пара REST-путём 6.2 «отдельной колонкой
/// и с подписью, каким транспортом».
pub const TRANSPORT_WS_ACK: &str = "WS wss://stream.bybit.com/v5/trade order.create (6.4)";
pub const TRANSPORT_WS_EXEC: &str = "WS private order/execution (6.4)";
pub const TRANSPORT_REST: &str = "REST /v5/order/create (6.2)";

/// Категория — та же, что в `probe.rs` (Decision 1, единственная площадка):
/// там она приватна и чужому файлу недоступна, поэтому здесь своя копия
/// с тем же значением, а не параметр.
const CATEGORY: &str = "linear";
/// Post-only существует только у лимитных ордеров — фиксирует протокол Bybit,
/// как и в `probe.rs`.
const ORDER_TYPE: &str = "Limit";
const TIME_IN_FORCE: &str = "PostOnly";
/// Сколько чужих/служебных кадров приватного стрима пропускается в ожидании
/// своего: стрим общий на счёт, туда же падают pong и обновления чужих
/// ордеров. Лимит, а не бесконечность: молча ждать свой кадр вечно на
/// боевом прогоне значит висеть, а не мерить.
const MAX_PRIVATE_SKIPS: usize = 32;

/// Обратное к `bybit::ws::parse_e9`: величина 1e-9 обратно в десятичную строку
/// для `qty`/`price`. Своя копия, а не `probe.rs::format_e9` — та приватна,
/// а `lob.rs` уже зафиксировал этот приём («своя маленькая копия здесь, пин
/// round-trip тестом»); тест ниже держит обе копии на одном значении.
fn format_e9(v: i64) -> String {
    let neg = v < 0;
    let v = v.unsigned_abs();
    let int_part = v / 1_000_000_000;
    let frac_part = v % 1_000_000_000;
    let mut s = if frac_part == 0 {
        int_part.to_string()
    } else {
        let mut frac_str = format!("{frac_part:09}");
        while frac_str.ends_with('0') {
            frac_str.pop();
        }
        format!("{int_part}.{frac_str}")
    };
    if neg {
        s.insert(0, '-');
    }
    s
}

fn side_str(side: OrderSide) -> &'static str {
    match side {
        OrderSide::Buy => "Buy",
        OrderSide::Sell => "Sell",
    }
}

/// Время эпохи в миллисекундах — только для `X-BAPI-TIMESTAMP` подписи.
/// Через `conn::SystemClock`, а не свой `SystemTime::now().expect(...)`:
/// тот же приём, что `commands/lob.rs::wall_clock_ns` (там же причина —
/// часы раньше эпохи обязаны дать метку, а не панику).
fn wall_timestamp_ms() -> i64 {
    SystemClock.now_ns() / 1_000_000
}

/// Заголовок WS-запроса: те же четыре поля, что REST-заголовки `probe.rs`
/// (`X-BAPI-API-KEY`, `TIMESTAMP`, `RECV-WINDOW`, `SIGN`). Подпись считается
/// той же формулой v5 (`sign.rs`) по компактному JSON аргументов — допущение
/// этого файла, проверяемое живым `#[ignore]`-тестом: биржа отвечает
/// ненулевым `retCode` на неверную подпись, а не молчанием.
#[derive(Debug, Clone, Serialize)]
struct WsHeader {
    #[serde(rename = "X-BAPI-API-KEY")]
    api_key: String,
    #[serde(rename = "X-BAPI-TIMESTAMP")]
    timestamp: String,
    #[serde(rename = "X-BAPI-RECV-WINDOW")]
    recv_window: String,
    #[serde(rename = "X-BAPI-SIGN")]
    sign: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct CreateArgs {
    category: &'static str,
    symbol: String,
    side: &'static str,
    order_type: &'static str,
    qty: String,
    price: String,
    time_in_force: &'static str,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct CancelArgs {
    category: &'static str,
    symbol: String,
    order_id: String,
}

#[derive(Debug, Clone, Serialize)]
struct WsRequest<A: Serialize> {
    #[serde(rename = "reqId")]
    req_id: String,
    header: WsHeader,
    op: &'static str,
    args: Vec<A>,
}

/// Один подписанный кадр, готовый к отправке транспортом.
///
/// Собственный `Debug`, не `derive`: кадр буквально содержит `api_key`
/// в JSON заголовка, и автогенерация напечатала бы его в любой лог или
/// панику. Тот же приём, что `SignedRequest` в `probe.rs`, и по той же
/// причине (Decision 12) — явная редакция здесь не зависит от содержимого
/// кадра. Содержимое доступно через поле `frame` тем, кому оно нужно
/// (транспорт, тесты), но не через `{:?}`.
#[derive(Clone)]
pub struct WsSignedFrame {
    pub op: &'static str,
    pub req_id: String,
    pub frame: String,
}

impl std::fmt::Debug for WsSignedFrame {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("WsSignedFrame")
            .field("op", &self.op)
            .field("req_id", &self.req_id)
            .field("frame", &"<redacted>")
            .finish()
    }
}

/// Кадр `order.create`: post-only минимального размера далеко от середины.
/// Цена — `probe::far_price_e9` (та же функция, что в 6.2: ордер тот же),
/// размер — `params.qty_e9` (минимальный лот, Decision 22).
pub fn build_create_frame(
    creds: &Credentials,
    params: &ProbeParams,
    mid_price_e9: i64,
    req_id: &str,
    timestamp_ms: i64,
) -> Result<WsSignedFrame, TradeWsError> {
    let price_e9 = probe::far_price_e9(
        mid_price_e9,
        params.tick_e9,
        params.ticks_from_mid,
        params.side,
    );
    let args = CreateArgs {
        category: CATEGORY,
        symbol: params.symbol.clone(),
        side: side_str(params.side),
        order_type: ORDER_TYPE,
        qty: format_e9(params.qty_e9),
        price: format_e9(price_e9),
        time_in_force: TIME_IN_FORCE,
    };
    let args_json =
        serde_json::to_string(&args).map_err(|e| TradeWsError::Decode(e.to_string()))?;
    let signature_hex = creds
        .sign(timestamp_ms, params.recv_window_ms, &args_json)
        .map_err(TradeWsError::Credentials)?;
    let header = WsHeader {
        api_key: creds.api_key().to_string(),
        timestamp: timestamp_ms.to_string(),
        recv_window: params.recv_window_ms.to_string(),
        sign: signature_hex,
    };
    let frame = serde_json::to_string(&WsRequest {
        req_id: req_id.to_string(),
        header,
        op: OP_CREATE,
        args: vec![args],
    })
    .map_err(|e| TradeWsError::Decode(e.to_string()))?;
    Ok(WsSignedFrame {
        op: OP_CREATE,
        req_id: req_id.to_string(),
        frame,
    })
}

/// Кадр `order.cancel` для немедленного снятия (`[ASSUMPTION H9]`).
pub fn build_cancel_frame(
    creds: &Credentials,
    params: &ProbeParams,
    order_id: &str,
    req_id: &str,
    timestamp_ms: i64,
) -> Result<WsSignedFrame, TradeWsError> {
    let args = CancelArgs {
        category: CATEGORY,
        symbol: params.symbol.clone(),
        order_id: order_id.to_string(),
    };
    let args_json =
        serde_json::to_string(&args).map_err(|e| TradeWsError::Decode(e.to_string()))?;
    let signature_hex = creds
        .sign(timestamp_ms, params.recv_window_ms, &args_json)
        .map_err(TradeWsError::Credentials)?;
    let header = WsHeader {
        api_key: creds.api_key().to_string(),
        timestamp: timestamp_ms.to_string(),
        recv_window: params.recv_window_ms.to_string(),
        sign: signature_hex,
    };
    let frame = serde_json::to_string(&WsRequest {
        req_id: req_id.to_string(),
        header,
        op: OP_CANCEL,
        args: vec![args],
    })
    .map_err(|e| TradeWsError::Decode(e.to_string()))?;
    Ok(WsSignedFrame {
        op: OP_CANCEL,
        req_id: req_id.to_string(),
        frame,
    })
}

/// Ответный кадр trade-сокета: `{"op","reqId","retCode","retMsg","data":...}`.
/// `ret_code` — `Option`, а не `i32`: отсутствующий код обязан быть ошибкой
/// разбора, а не успехом (дефолтный `0` превратил бы битый кадр в успешный).
#[derive(Debug, Deserialize)]
struct AckEnvelope {
    #[serde(default)]
    op: Option<String>,
    #[serde(default, rename = "reqId")]
    req_id: Option<String>,
    #[serde(default, rename = "retCode")]
    ret_code: Option<i32>,
    #[serde(default, rename = "retMsg")]
    ret_msg: Option<String>,
    #[serde(default)]
    data: Option<AckData>,
}

#[derive(Debug, Deserialize, Default)]
struct AckData {
    #[serde(default, rename = "orderId")]
    order_id: String,
}

/// Разобранное подтверждение приёма: код/сообщение как есть, `order_id`
/// пустой строкой при отсутствии. Проверяет форму и принадлежность
/// (`op`, `reqId`); смысл кода решает вызывающий: на создании ненулевой код —
/// `OrderRejected`, на отмене — `CancelFailed` (тот же контракт, что пара
/// `OrderRejected`/`CancelFailed` в `probe.rs`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WsAck {
    pub ret_code: i32,
    pub ret_msg: String,
    pub order_id: String,
}

pub fn parse_ack_frame(
    raw: &str,
    expected_op: &str,
    expected_req_id: &str,
) -> Result<WsAck, TradeWsError> {
    let env: AckEnvelope =
        serde_json::from_str(raw).map_err(|e| TradeWsError::Decode(e.to_string()))?;
    let op = env
        .op
        .ok_or_else(|| TradeWsError::Decode("кадр без op".to_string()))?;
    if op != expected_op {
        return Err(TradeWsError::UnexpectedOp {
            expected: expected_op.to_string(),
            got: op,
        });
    }
    let req_id = env
        .req_id
        .ok_or_else(|| TradeWsError::Decode("кадр без reqId".to_string()))?;
    if req_id != expected_req_id {
        return Err(TradeWsError::ReqIdMismatch {
            expected: expected_req_id.to_string(),
            got: req_id,
        });
    }
    let ret_code = env
        .ret_code
        .ok_or_else(|| TradeWsError::Decode("кадр без retCode".to_string()))?;
    Ok(WsAck {
        ret_code,
        ret_msg: env.ret_msg.unwrap_or_default(),
        order_id: env.data.map(|d| d.order_id).unwrap_or_default(),
    })
}

/// Кадр приватного стрима: `{"topic":"order"|"execution","data":[{...}]}`.
/// Возвращает первый непустой `orderId`, `None` — для всего чужого
/// (подписки, pong, чужие ордера, битый JSON): вызывающий пропускает такие
/// кадры в ожидании своего, а не падает на них.
pub fn private_frame_order_id(raw: &str) -> Option<String> {
    #[derive(Debug, Deserialize)]
    struct PrivateEnvelope {
        #[serde(default)]
        topic: Option<String>,
        #[serde(default)]
        data: Option<Vec<PrivateRow>>,
    }
    #[derive(Debug, Deserialize)]
    struct PrivateRow {
        #[serde(default, rename = "orderId")]
        order_id: String,
    }
    let env: PrivateEnvelope = serde_json::from_str(raw).ok()?;
    let topic = env.topic?;
    let ours = topic == "order"
        || topic.starts_with("order.")
        || topic == "execution"
        || topic.starts_with("execution.");
    if !ours {
        return None;
    }
    env.data?
        .into_iter()
        .find_map(|r| (!r.order_id.is_empty()).then_some(r.order_id))
}

/// Транспорт WS trade за трейтом: отправка подписанного кадра и чтение
/// следующего сырого кадра. Живой цикл держит два сокета (trade + private),
/// но обе стороны говорят строками, поэтому трейт один — какая строка
/// откуда, решает реализация, а ядро ниже видит только порядок.
pub trait WsTradeTransport {
    fn send(&mut self, frame: String) -> Result<(), TradeWsError>;
    fn recv(&mut self) -> Result<String, TradeWsError>;
}

/// Ошибки зонда WS. Как и в `probe.rs`, ни один вариант не может содержать
/// секрет: в кадры уходит только HMAC-дайджест, а транспорт несёт лишь текст
/// ошибки сокета.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TradeWsError {
    Credentials(crate::bybit::sign::CredentialsError),
    TooFewCycles {
        requested: usize,
        minimum: usize,
    },
    Transport(String),
    Decode(String),
    UnexpectedOp {
        expected: String,
        got: String,
    },
    ReqIdMismatch {
        expected: String,
        got: String,
    },
    OrderRejected {
        ret_code: i32,
        ret_msg: String,
    },
    MissingOrderId {
        ret_code: i32,
    },
    CancelFailed {
        order_id: String,
        ret_code: i32,
        ret_msg: String,
    },
}

/// Четыре метки одного цикла — `Instant`, не настенные часы (тот же фикс,
/// что `Cycle` в `probe.rs`: скачок NTP между метками не вправе попасть
/// в выборку G4 и тем более дать отрицательный отсчёт).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WsCycle {
    pub tick_observed: Instant,
    pub order_sent: Instant,
    pub ack_received: Instant,
    pub exec_received: Instant,
}

impl WsCycle {
    /// RTT приёма: отправка кадра → ответный кадр trade-сокета.
    /// Касты точные: RTT — миллисекунды против 292 лет диапазона `i64` в нс.
    #[allow(clippy::cast_possible_truncation)]
    pub fn rtt_ack_ns(&self) -> i64 {
        self.ack_received.duration_since(self.order_sent).as_nanos() as i64
    }
    /// RTT исполнения: отправка кадра → подтверждение приватным стримом
    /// `order`/`execution`. Та же база, другая третья метка — обе печатаются
    /// раздельно, в G4 идёт та, что соответствует модели очереди.
    #[allow(clippy::cast_possible_truncation)]
    pub fn rtt_exec_ns(&self) -> i64 {
        self.exec_received
            .duration_since(self.order_sent)
            .as_nanos() as i64
    }
}

/// Один цикл: тик → подпись и отправка `order.create` → ответный кадр →
/// приватное подтверждение → немедленная отмена. Отмена входит в цикл, но
/// не в измеряемые RTT, и обязана состояться до возврата (висящий ордер на
/// боевом счёте опаснее потерянной точки — тот же контракт, что
/// `ProbeError::CancelFailed` в `probe.rs`).
pub fn run_ws_cycle<T: WsTradeTransport>(
    transport: &mut T,
    creds: &Credentials,
    params: &ProbeParams,
    mid_price_e9: i64,
    req_id: &str,
) -> Result<WsCycle, TradeWsError> {
    let tick_observed = Instant::now();

    let create = build_create_frame(creds, params, mid_price_e9, req_id, wall_timestamp_ms())?;
    let order_sent = Instant::now();
    transport.send(create.frame)?;
    let ack_raw = transport.recv()?;
    let ack_received = Instant::now();

    let ack = parse_ack_frame(&ack_raw, OP_CREATE, req_id)?;
    if ack.ret_code != 0 {
        return Err(TradeWsError::OrderRejected {
            ret_code: ack.ret_code,
            ret_msg: ack.ret_msg,
        });
    }
    if ack.order_id.is_empty() {
        return Err(TradeWsError::MissingOrderId {
            ret_code: ack.ret_code,
        });
    }
    let order_id = ack.order_id;

    let mut exec_received = None;
    for _ in 0..=MAX_PRIVATE_SKIPS {
        let raw = transport.recv()?;
        let at = Instant::now();
        if private_frame_order_id(&raw).as_deref() == Some(order_id.as_str()) {
            exec_received = Some(at);
            break;
        }
    }
    let exec_received = exec_received.ok_or_else(|| {
        TradeWsError::Transport("приватный стрим не подтвердил ордер".to_string())
    })?;

    let cancel_req_id = format!("{req_id}-cancel");
    let cancel = build_cancel_frame(
        creds,
        params,
        &order_id,
        &cancel_req_id,
        wall_timestamp_ms(),
    )?;
    transport.send(cancel.frame)?;
    let cancel_raw = transport.recv()?;
    let cancel_ack = parse_ack_frame(&cancel_raw, OP_CANCEL, &cancel_req_id)?;
    if cancel_ack.ret_code != 0 {
        return Err(TradeWsError::CancelFailed {
            order_id,
            ret_code: cancel_ack.ret_code,
            ret_msg: cancel_ack.ret_msg,
        });
    }

    Ok(WsCycle {
        tick_observed,
        order_sent,
        ack_received,
        exec_received,
    })
}

/// Прогоняет `n_cycles` циклов подряд. `reqId` — счётчик `ws-000000…`:
/// биржевое эхо обязано совпасть с запросом, а детерминированный ряд делает
/// фейковые ответы сценарного транспорта проверяемыми без регулярок.
pub fn run_ws_cycles<T: WsTradeTransport>(
    transport: &mut T,
    creds: &Credentials,
    params: &ProbeParams,
    mut mid_price_e9: impl FnMut() -> i64,
    n_cycles: usize,
) -> Result<Vec<WsCycle>, TradeWsError> {
    let mut cycles = Vec::with_capacity(n_cycles);
    for i in 0..n_cycles {
        let mid = mid_price_e9();
        cycles.push(run_ws_cycle(
            transport,
            creds,
            params,
            mid,
            &format!("ws-{i:06}"),
        )?);
    }
    Ok(cycles)
}

/// Сводка распределения RTT одного прогона: медиана и p95 каждой из двух
/// меток раздельно — done-condition шага 6.4.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WsRttSummary {
    pub n: usize,
    pub ack_median_ns: i64,
    pub ack_p95_ns: i64,
    pub exec_median_ns: i64,
    pub exec_p95_ns: i64,
}

/// Перцентили — `probe::percentile_ns` (ближайший ранг, всегда реально
/// наблюдённое значение, не интерполяция): та же конвенция, что в 6.2,
/// иначе две колонки отчёта были бы несравнимы между собой.
pub fn summarize_ws(cycles: &[WsCycle]) -> WsRttSummary {
    let ack: Vec<i64> = cycles.iter().map(WsCycle::rtt_ack_ns).collect();
    let exec: Vec<i64> = cycles.iter().map(WsCycle::rtt_exec_ns).collect();
    WsRttSummary {
        n: cycles.len(),
        ack_median_ns: probe::percentile_ns(&ack, 50),
        ack_p95_ns: probe::percentile_ns(&ack, 95),
        exec_median_ns: probe::percentile_ns(&exec, 50),
        exec_p95_ns: probe::percentile_ns(&exec, 95),
    }
}

/// Одна колонка отчёта с подписью транспорта.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LabeledSummary {
    pub transport: &'static str,
    pub n: usize,
    pub median_ns: i64,
    pub p95_ns: i64,
}

/// Три колонки рядом: две WS-метки и справочная REST-пара из 6.2.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SideBySide {
    pub ws_ack: LabeledSummary,
    pub ws_exec: LabeledSummary,
    pub rest: LabeledSummary,
}

/// Та же пара (медиана/p95), померенная REST-путём 6.2, — отдельной колонкой
/// и с подписью транспорта. Входом в G4 она быть перестала (ревизия 11),
/// остаётся справочной.
pub fn compare_with_rest(ws: &WsRttSummary, rest: &probe::RttSummary) -> SideBySide {
    SideBySide {
        ws_ack: LabeledSummary {
            transport: TRANSPORT_WS_ACK,
            n: ws.n,
            median_ns: ws.ack_median_ns,
            p95_ns: ws.ack_p95_ns,
        },
        ws_exec: LabeledSummary {
            transport: TRANSPORT_WS_EXEC,
            n: ws.n,
            median_ns: ws.exec_median_ns,
            p95_ns: ws.exec_p95_ns,
        },
        rest: LabeledSummary {
            transport: TRANSPORT_REST,
            n: rest.n,
            median_ns: rest.median_ns,
            p95_ns: rest.p95_ns,
        },
    }
}

/// Печать трёх колонок: каждая строка несёт свою подпись транспорта, числа
/// без подписи не печатаются никогда — иначе REST-число снова прочитают
/// как вход G4.
pub fn format_side_by_side(rep: &SideBySide) -> String {
    format!(
        "{}: n={} median_ns={} p95_ns={}\n{}: n={} median_ns={} p95_ns={}\n{}: n={} median_ns={} p95_ns={}",
        rep.ws_ack.transport,
        rep.ws_ack.n,
        rep.ws_ack.median_ns,
        rep.ws_ack.p95_ns,
        rep.ws_exec.transport,
        rep.ws_exec.n,
        rep.ws_exec.median_ns,
        rep.ws_exec.p95_ns,
        rep.rest.transport,
        rep.rest.n,
        rep.rest.median_ns,
        rep.rest.p95_ns,
    )
}

/// Единственная точка входа замера 6.4. Отказывается работать без обеих
/// переменных окружения (Decision 12) и меньше чем на `MIN_CYCLES` циклов
/// (тот же минимум, что 6.2: done-condition требует ≥ 1000).
pub fn probe_ws<T: WsTradeTransport>(
    transport: &mut T,
    params: &ProbeParams,
    mid_price_e9: impl FnMut() -> i64,
    n_cycles: usize,
) -> Result<WsRttSummary, TradeWsError> {
    if n_cycles < probe::MIN_CYCLES {
        return Err(TradeWsError::TooFewCycles {
            requested: n_cycles,
            minimum: probe::MIN_CYCLES,
        });
    }
    let creds = Credentials::from_env().map_err(TradeWsError::Credentials)?;
    let cycles = run_ws_cycles(transport, &creds, params, mid_price_e9, n_cycles)?;
    Ok(summarize_ws(&cycles))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;

    struct FakeWs {
        inbox: VecDeque<Result<String, TradeWsError>>,
        sent: Vec<String>,
    }

    impl FakeWs {
        fn with_frames(frames: Vec<Result<String, TradeWsError>>) -> Self {
            Self {
                inbox: frames.into(),
                sent: Vec::new(),
            }
        }
    }

    impl WsTradeTransport for FakeWs {
        fn send(&mut self, frame: String) -> Result<(), TradeWsError> {
            self.sent.push(frame);
            Ok(())
        }

        fn recv(&mut self) -> Result<String, TradeWsError> {
            self.inbox
                .pop_front()
                .expect("тест не подготовил столько кадров транспорту")
        }
    }

    fn test_creds() -> Credentials {
        Credentials::for_test("test-key", "test-secret")
    }

    fn test_params() -> ProbeParams {
        ProbeParams {
            symbol: "SOLUSDT".to_string(),
            side: OrderSide::Buy,
            qty_e9: 100_000_000,
            tick_e9: 10_000_000,
            ticks_from_mid: 500,
            recv_window_ms: probe::DEFAULT_RECV_WINDOW_MS,
        }
    }

    const MID_E9: i64 = 100_000_000_000; // 100.0
    const TS_MS: i64 = 1_700_000_000_000;

    fn ack_json(op: &str, req_id: &str, order_id: &str) -> String {
        format!(
            r#"{{"op":"{op}","reqId":"{req_id}","retCode":0,"retMsg":"OK","data":{{"orderId":"{order_id}"}}}}"#
        )
    }

    fn reject_json(op: &str, req_id: &str, ret_code: i32, msg: &str) -> String {
        format!(
            r#"{{"op":"{op}","reqId":"{req_id}","retCode":{ret_code},"retMsg":"{msg}","result":{{}},"data":{{}}}}"#
        )
    }

    fn private_order_json(order_id: &str) -> String {
        format!(r#"{{"topic":"order","data":[{{"orderId":"{order_id}","orderStatus":"New"}}]}}"#)
    }

    fn private_execution_json(order_id: &str) -> String {
        format!(r#"{{"topic":"execution","data":[{{"orderId":"{order_id}","execQty":"0.1"}}]}}"#)
    }

    fn full_cycle_frames(req_id: &str, order_id: &str) -> Vec<Result<String, TradeWsError>> {
        vec![
            Ok(ack_json(OP_CREATE, req_id, order_id)),
            Ok(private_order_json(order_id)),
            Ok(ack_json(OP_CANCEL, &format!("{req_id}-cancel"), order_id)),
        ]
    }

    #[test]
    fn create_frame_carries_post_only_far_price_and_signed_header() {
        let params = test_params();
        let frame = build_create_frame(&test_creds(), &params, MID_E9, "ws-000001", TS_MS).unwrap();

        assert_eq!(frame.op, OP_CREATE);
        let v: serde_json::Value = serde_json::from_str(&frame.frame).unwrap();
        assert_eq!(v["op"], OP_CREATE);
        assert_eq!(v["reqId"], "ws-000001");
        let args = &v["args"][0];
        assert_eq!(args["category"], "linear");
        assert_eq!(args["symbol"], "SOLUSDT");
        assert_eq!(args["side"], "Buy");
        assert_eq!(args["orderType"], "Limit");
        assert_eq!(args["timeInForce"], "PostOnly");
        // Тот же ордер, что в 6.2: цена — far_price_e9, размер — минимальный лот.
        let expected_price = probe::far_price_e9(MID_E9, params.tick_e9, 500, OrderSide::Buy);
        assert_eq!(args["price"], format_e9(expected_price));
        assert_eq!(args["qty"], format_e9(params.qty_e9));
        assert_eq!(v["header"]["X-BAPI-API-KEY"], "test-key");
        assert_eq!(v["header"]["X-BAPI-TIMESTAMP"], TS_MS.to_string());
        assert!(
            v["header"]["X-BAPI-SIGN"]
                .as_str()
                .is_some_and(|s| s.len() == 64),
            "подпись — hex HMAC-SHA256"
        );
    }

    #[test]
    fn cancel_frame_carries_order_id_and_cancel_op() {
        let frame = build_cancel_frame(
            &test_creds(),
            &test_params(),
            "order-7",
            "ws-2-cancel",
            TS_MS,
        )
        .unwrap();
        let v: serde_json::Value = serde_json::from_str(&frame.frame).unwrap();
        assert_eq!(v["op"], OP_CANCEL);
        assert_eq!(v["reqId"], "ws-2-cancel");
        assert_eq!(v["args"][0]["orderId"], "order-7");
        assert_eq!(v["args"][0]["category"], "linear");
    }

    /// Decision 12: ключ из кадра не вправе всплыть в `{:?}` — кадр буквально
    /// содержит `api_key` в JSON заголовка, поэтому `Debug` кадр не печатает.
    #[test]
    fn signed_frame_debug_output_contains_neither_key_nor_secret() {
        let creds = Credentials::for_test("visible-ws-key", "s3cr3t-ws-do-not-leak");
        let frame = build_create_frame(&creds, &test_params(), MID_E9, "ws-1", TS_MS).unwrap();
        let printed = format!("{frame:?}");
        assert!(
            !printed.contains("visible-ws-key"),
            "утёк api_key: {printed}"
        );
        assert!(
            !printed.contains("s3cr3t-ws-do-not-leak"),
            "утёк секрет: {printed}"
        );
        assert!(
            printed.contains("<redacted>"),
            "нет метки редакции: {printed}"
        );
    }

    #[test]
    fn format_e9_round_trips_through_ws_parse_e9() {
        use crate::bybit::ws::parse_e9;
        for &s in &["150.01", "0.001", "12345.6789", "1", "0.000000001"] {
            let v = parse_e9(s).unwrap();
            assert_eq!(parse_e9(&format_e9(v)).unwrap(), v);
        }
    }

    #[test]
    fn parse_ack_accepts_success_and_returns_order_id() {
        let ack =
            parse_ack_frame(&ack_json(OP_CREATE, "ws-1", "abc-1"), OP_CREATE, "ws-1").unwrap();
        assert_eq!(ack.ret_code, 0);
        assert_eq!(ack.order_id, "abc-1");
    }

    #[test]
    fn parse_ack_rejects_a_foreign_op() {
        let err =
            parse_ack_frame(&ack_json(OP_CANCEL, "ws-1", "x"), OP_CREATE, "ws-1").unwrap_err();
        assert_eq!(
            err,
            TradeWsError::UnexpectedOp {
                expected: OP_CREATE.to_string(),
                got: OP_CANCEL.to_string(),
            }
        );
    }

    #[test]
    fn parse_ack_rejects_a_foreign_req_id() {
        let err =
            parse_ack_frame(&ack_json(OP_CREATE, "ws-9", "x"), OP_CREATE, "ws-1").unwrap_err();
        assert_eq!(
            err,
            TradeWsError::ReqIdMismatch {
                expected: "ws-1".to_string(),
                got: "ws-9".to_string(),
            }
        );
    }

    #[test]
    fn parse_ack_without_ret_code_is_decode_not_success() {
        let err = parse_ack_frame(
            r#"{"op":"order.create","reqId":"ws-1","data":{"orderId":"x"}}"#,
            OP_CREATE,
            "ws-1",
        )
        .unwrap_err();
        assert!(matches!(err, TradeWsError::Decode(_)));
    }

    #[test]
    fn parse_ack_carries_reject_code_for_the_caller_to_map() {
        let ack = parse_ack_frame(
            &reject_json(OP_CREATE, "ws-1", 10001, "post only would take"),
            OP_CREATE,
            "ws-1",
        )
        .unwrap();
        assert_eq!(ack.ret_code, 10001);
        assert_eq!(ack.ret_msg, "post only would take");
    }

    #[test]
    fn private_frame_extracts_order_and_execution_topics() {
        assert_eq!(
            private_frame_order_id(&private_order_json("o-1")).as_deref(),
            Some("o-1")
        );
        assert_eq!(
            private_frame_order_id(&private_execution_json("o-2")).as_deref(),
            Some("o-2")
        );
        assert_eq!(
            private_frame_order_id(r#"{"topic":"execution.linear","data":[{"orderId":"o-3"}]}"#)
                .as_deref(),
            Some("o-3")
        );
    }

    #[test]
    fn private_frame_ignores_foreign_frames_instead_of_failing() {
        assert_eq!(
            private_frame_order_id(r#"{"success":true,"op":"subscribe"}"#),
            None
        );
        assert_eq!(private_frame_order_id(r#"{"op":"pong"}"#), None);
        assert_eq!(
            private_frame_order_id(r#"{"topic":"order","data":[]}"#),
            None,
            "пустой data — чужой кадр, а не ошибка"
        );
        assert_eq!(private_frame_order_id("not json"), None);
    }

    #[test]
    fn run_ws_cycle_places_cancels_and_returns_four_ordered_marks() {
        let mut fake = FakeWs::with_frames(full_cycle_frames("ws-000000", "order-1"));
        let cycle = run_ws_cycle(
            &mut fake,
            &test_creds(),
            &test_params(),
            MID_E9,
            "ws-000000",
        )
        .unwrap();

        assert_eq!(fake.sent.len(), 2);
        assert!(fake.sent[0].contains(OP_CREATE));
        assert!(fake.sent[1].contains(OP_CANCEL));
        assert!(fake.sent[1].contains("order-1"));
        assert!(cycle.tick_observed <= cycle.order_sent);
        assert!(cycle.order_sent <= cycle.ack_received);
        assert!(cycle.ack_received <= cycle.exec_received);
        assert!(
            cycle.rtt_exec_ns() >= cycle.rtt_ack_ns(),
            "исполнение не раньше приёма на том же цикле"
        );
    }

    #[test]
    fn run_ws_cycle_skips_foreign_private_frames_until_ours() {
        let mut fake = FakeWs::with_frames(vec![
            Ok(ack_json(OP_CREATE, "ws-000000", "mine")),
            Ok(r#"{"op":"pong"}"#.to_string()),
            Ok(private_order_json(" чужой ".trim())),
            Ok(private_execution_json(" чужая ".trim())),
            Ok(private_execution_json("mine")),
            Ok(ack_json(OP_CANCEL, "ws-000000-cancel", "mine")),
        ]);
        let cycle = run_ws_cycle(
            &mut fake,
            &test_creds(),
            &test_params(),
            MID_E9,
            "ws-000000",
        )
        .unwrap();
        assert!(cycle.exec_received >= cycle.ack_received);
    }

    #[test]
    fn run_ws_cycle_fails_and_skips_cancel_when_order_is_rejected() {
        let mut fake = FakeWs::with_frames(vec![Ok(reject_json(
            OP_CREATE,
            "ws-000000",
            10001,
            "post only would take",
        ))]);
        let err = run_ws_cycle(
            &mut fake,
            &test_creds(),
            &test_params(),
            MID_E9,
            "ws-000000",
        )
        .unwrap_err();
        assert_eq!(
            err,
            TradeWsError::OrderRejected {
                ret_code: 10001,
                ret_msg: "post only would take".to_string(),
            }
        );
        assert_eq!(fake.sent.len(), 1, "без order_id снимать нечего");
    }

    #[test]
    fn run_ws_cycle_reports_missing_order_id_on_success_without_id() {
        let mut fake = FakeWs::with_frames(vec![Ok(reject_json(OP_CREATE, "ws-000000", 0, "OK"))]);
        let err = run_ws_cycle(
            &mut fake,
            &test_creds(),
            &test_params(),
            MID_E9,
            "ws-000000",
        )
        .unwrap_err();
        assert_eq!(err, TradeWsError::MissingOrderId { ret_code: 0 });
        assert_eq!(fake.sent.len(), 1, "без order_id снимать нечего");
    }

    #[test]
    fn run_ws_cycle_maps_cancel_reject_to_cancel_failed() {
        let mut fake = FakeWs::with_frames(vec![
            Ok(ack_json(OP_CREATE, "ws-000000", "order-2")),
            Ok(private_order_json("order-2")),
            Ok(reject_json(
                OP_CANCEL,
                "ws-000000-cancel",
                10002,
                "order not found",
            )),
        ]);
        let err = run_ws_cycle(
            &mut fake,
            &test_creds(),
            &test_params(),
            MID_E9,
            "ws-000000",
        )
        .unwrap_err();
        assert_eq!(
            err,
            TradeWsError::CancelFailed {
                order_id: "order-2".to_string(),
                ret_code: 10002,
                ret_msg: "order not found".to_string(),
            }
        );
    }

    #[test]
    fn summarize_ws_computes_median_and_p95_for_both_marks_separately() {
        // Приём 1..=10 мс, исполнение — приём + 10 мс: сводки обязаны различаться
        // ровно на сдвиг, иначе вторая метка посчитана из первой.
        let base = Instant::now();
        let cycles: Vec<WsCycle> = (1..=10i64)
            .map(|i| WsCycle {
                tick_observed: base,
                order_sent: base,
                ack_received: base + std::time::Duration::from_millis(i as u64),
                exec_received: base + std::time::Duration::from_millis((i + 10) as u64),
            })
            .collect();
        let s = summarize_ws(&cycles);
        assert_eq!(s.n, 10);
        assert_eq!(s.ack_median_ns, 5_000_000);
        assert_eq!(s.ack_p95_ns, 10_000_000);
        assert_eq!(s.exec_median_ns, 15_000_000);
        assert_eq!(s.exec_p95_ns, 20_000_000);
    }

    #[test]
    fn side_by_side_labels_each_column_with_its_transport() {
        let ws = WsRttSummary {
            n: 1000,
            ack_median_ns: 1,
            ack_p95_ns: 2,
            exec_median_ns: 3,
            exec_p95_ns: 4,
        };
        let rest = probe::RttSummary {
            n: 1000,
            median_ns: 5,
            p95_ns: 6,
        };
        let rep = compare_with_rest(&ws, &rest);
        assert!(rep.ws_ack.transport.contains("WS"), "метка приёма — WS");
        assert!(
            rep.ws_exec.transport.contains("WS"),
            "метка исполнения — WS"
        );
        assert!(
            rep.rest.transport.contains("REST"),
            "справочная колонка — REST"
        );
        assert_ne!(rep.ws_ack.transport, rep.rest.transport);
        let printed = format_side_by_side(&rep);
        for needle in [
            rep.ws_ack.transport,
            rep.ws_exec.transport,
            rep.rest.transport,
        ] {
            assert!(printed.contains(needle), "строка без подписи: {printed}");
        }
        assert!(
            printed.contains("median_ns=1"),
            "числа ACK на месте: {printed}"
        );
        assert!(
            printed.contains("median_ns=3"),
            "числа EXEC на месте: {printed}"
        );
        assert!(
            printed.contains("median_ns=5"),
            "числа REST на месте: {printed}"
        );
    }

    #[test]
    fn probe_ws_refuses_to_run_with_fewer_than_the_minimum_cycles() {
        let mut fake = FakeWs::with_frames(vec![]);
        let params = test_params();
        let err = probe_ws(&mut fake, &params, || MID_E9, probe::MIN_CYCLES - 1).unwrap_err();
        assert_eq!(
            err,
            TradeWsError::TooFewCycles {
                requested: probe::MIN_CYCLES - 1,
                minimum: probe::MIN_CYCLES,
            }
        );
        assert!(fake.sent.is_empty(), "меньше минимума — сеть не трогается");
    }

    #[test]
    fn probe_ws_refuses_to_run_without_keys() {
        crate::bybit::sign::with_cleared_env(|| {
            std::env::set_var(crate::bybit::sign::API_SECRET_VAR, "secret");
            let mut fake = FakeWs::with_frames(vec![]);
            let params = test_params();
            let err = probe_ws(&mut fake, &params, || MID_E9, probe::MIN_CYCLES).unwrap_err();
            assert_eq!(
                err,
                TradeWsError::Credentials(crate::bybit::sign::CredentialsError::Missing(
                    crate::bybit::sign::API_KEY_VAR
                ))
            );
            assert!(fake.sent.is_empty(), "без ключей ни один кадр не уходит");
        });
    }

    /// Живой прогон 6.4: один реальный цикл create → ack → cancel → ack по
    /// `TRADE_WS_URL` с боевыми ключами из окружения. Игнорируется в обычном
    /// `cargo test`: ≥ 1000 живых циклов в песочнице невозможны, а каждый цикл
    /// ставит реальный ордер (H9). Запуск вручную с хоста H12:
    /// `cargo test --release trade_ws::tests::live_ws_trade_ack_round_trip -- --ignored --nocapture`.
    /// Полное распределение на ≥ 1000 циклов гонится тем же `probe_ws` поверх
    /// живого транспорта с двумя сокетами (trade + private); здесь — один цикл
    /// на trade-сокете как доказательство тракта «кадр → подпись → разбор».
    #[tokio::test]
    #[ignore = "живая сеть: wss://stream.bybit.com, боевые ключи, реальный ордер H9"]
    async fn live_ws_trade_ack_round_trip() {
        use futures::{SinkExt, StreamExt};

        let (Some(_key), Some(_secret)) = (
            std::env::var(crate::bybit::sign::API_KEY_VAR).ok(),
            std::env::var(crate::bybit::sign::API_SECRET_VAR).ok(),
        ) else {
            eprintln!("skip: нет ключей BYBIT_API_KEY/BYBIT_API_SECRET");
            return;
        };
        let creds = Credentials::from_env().expect("ключи только что проверены выше");
        let params = ProbeParams {
            symbol: "SOLUSDT".to_string(),
            side: OrderSide::Buy,
            qty_e9: 100_000_000,
            tick_e9: 10_000_000,
            ticks_from_mid: 5000,
            recv_window_ms: probe::DEFAULT_RECV_WINDOW_MS,
        };
        // Середина здесь — заглушка ручного прогона: оператор подставляет живую
        // середину из стакана перед запуском (смещение 5000 тиков держит
        // post-only далеко от книги при любом разумном значении).
        let mid_price_e9: i64 = 100_000_000_000;

        let connect = tokio::time::timeout(
            std::time::Duration::from_secs(10),
            tokio_tungstenite::connect_async(TRADE_WS_URL),
        )
        .await
        .expect("коннект к trade-сокету за 10с")
        .expect("рукопожатие WS");
        let (mut stream, _) = connect;

        let create = build_create_frame(
            &creds,
            &params,
            mid_price_e9,
            "live-000001",
            wall_timestamp_ms(),
        )
        .unwrap();
        let sent = Instant::now();
        stream
            .send(tokio_tungstenite::tungstenite::Message::Text(create.frame))
            .await
            .expect("отправка order.create");
        let ack_raw = tokio::time::timeout(std::time::Duration::from_secs(10), stream.next())
            .await
            .expect("ответный кадр за 10с")
            .expect("стрим не закрыт")
            .expect("кадр без WS-ошибки");
        let ack_at = Instant::now();
        let ack_text = ack_raw.into_text().expect("текстовый кадр");
        let ack = parse_ack_frame(&ack_text, OP_CREATE, "live-000001").expect("разбор ack");
        assert!(ack_at >= sent, "метки монотонны");
        assert_eq!(ack.ret_code, 0, "живой create отклонён: {ack:?}");
        assert!(!ack.order_id.is_empty());

        let cancel = build_cancel_frame(
            &creds,
            &params,
            &ack.order_id,
            "live-000001-cancel",
            wall_timestamp_ms(),
        )
        .unwrap();
        stream
            .send(tokio_tungstenite::tungstenite::Message::Text(cancel.frame))
            .await
            .expect("отправка order.cancel");
        let cancel_raw = tokio::time::timeout(std::time::Duration::from_secs(10), stream.next())
            .await
            .expect("ответный кадр отмены за 10с")
            .expect("стрим не закрыт")
            .expect("кадр без WS-ошибки");
        let cancel_ack = parse_ack_frame(
            &cancel_raw.into_text().expect("текстовый кадр"),
            OP_CANCEL,
            "live-000001-cancel",
        )
        .expect("разбор ack отмены");
        assert_eq!(
            cancel_ack.ret_code, 0,
            "живая отмена отклонена: {cancel_ack:?}"
        );
    }
}
