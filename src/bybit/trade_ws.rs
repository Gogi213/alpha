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
use crate::bybit::sign::{Credentials, CredentialsError};
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

/// То же самое, что `format_e9`, но пишет в переданный буфер вместо
/// возврата новой `String` — единственный способ не аллоцировать на каждую
/// пересборку `ReadyMakerOrder` (дозапрос ревью таска 15, запрет 1). `buf`
/// не очищается здесь — вызывающий решает, когда: `rebuild` ниже чистит
/// перед каждым использованием и держит ёмкость между вызовами.
/// Тест `format_e9_into_matches_format_e9` держит обе копии на одном значении.
fn format_e9_into(buf: &mut String, v: i64) {
    use std::fmt::Write as _;
    let neg = v < 0;
    let uv = v.unsigned_abs();
    let int_part = uv / 1_000_000_000;
    let frac_part = uv % 1_000_000_000;
    if neg {
        buf.push('-');
    }
    let _ = write!(buf, "{int_part}");
    if frac_part != 0 {
        buf.push('.');
        let _ = write!(buf, "{frac_part:09}");
        while buf.ends_with('0') {
            buf.pop();
        }
    }
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

/// Шов D-ОРДЕР (таск 15): абстракция подписи, а не конкретный `Credentials`.
/// Продовая реализация ниже просто делегирует настоящим ключам; тестовая
/// (`commands/lob/react.rs`, этот файл) оборачивает её счётчиком вызовов —
/// единственный способ доказать «подпись не вызывается из ветки
/// срабатывания», как того требует критерий приёмки, без парсинга логов.
pub trait OrderSigner {
    fn sign(
        &self,
        timestamp_ms: i64,
        recv_window_ms: u32,
        body: &str,
    ) -> Result<String, CredentialsError>;
    fn api_key(&self) -> &str;

    /// Пишет hex-подпись (64 ASCII-символа HMAC-SHA256, тот же алфавит, что
    /// `sign`) в `out`, не аллоцируя на стороне вызывающего (дозапрос
    /// ревью таска 15, ось Craft, запрет 1: `ReadyMakerOrder::rebuild` не
    /// вправе аллоцировать после прогрева). Реализация по умолчанию зовёт
    /// `sign` и копирует байты — аллоцирующий запасной путь для реализаций,
    /// которым лень писать буферный HMAC. `Credentials` (ниже) его больше
    /// не наследует: таск 17 закрыл долг из `interfaces.md` («Из ремонта
    /// таска 15») буферной реализацией в `bybit::sign::Credentials::
    /// sign_into` — ноль аллокаций доказан тестом `credentials_sign_into_
    /// allocates_nothing_after_warmup` (`sign.rs`) на настоящих ключах, не
    /// только на тестовом фейке `ZeroAllocSigner` этого файла.
    fn sign_into(
        &self,
        timestamp_ms: i64,
        recv_window_ms: u32,
        body: &str,
        out: &mut [u8; 64],
    ) -> Result<(), CredentialsError> {
        let hex = self.sign(timestamp_ms, recv_window_ms, body)?;
        let bytes = hex.as_bytes();
        let n = bytes.len().min(out.len());
        out[..n].copy_from_slice(&bytes[..n]);
        for b in &mut out[n..] {
            *b = b'0';
        }
        Ok(())
    }
}

impl OrderSigner for Credentials {
    fn sign(
        &self,
        timestamp_ms: i64,
        recv_window_ms: u32,
        body: &str,
    ) -> Result<String, CredentialsError> {
        Credentials::sign(self, timestamp_ms, recv_window_ms, body)
    }

    fn api_key(&self) -> &str {
        Credentials::api_key(self)
    }

    /// Переопределяет реализацию по умолчанию: `Credentials::sign_into`
    /// пишет HMAC прямо в `out` без склейки payload в `String` (`sign.rs`),
    /// так что боевой подписант больше не аллоцирует на каждый вызов —
    /// не только тестовый `ZeroAllocSigner` ниже.
    fn sign_into(
        &self,
        timestamp_ms: i64,
        recv_window_ms: u32,
        body: &str,
        out: &mut [u8; 64],
    ) -> Result<(), CredentialsError> {
        Credentials::sign_into(self, timestamp_ms, recv_window_ms, body, out)
    }
}

/// Кадр `order.create` по явно заданной цене. Тело — общее для двух вызывающих
/// с разными правилами цены: `build_create_frame` (ниже, `far_price_e9` —
/// нарочно прочь от книги, зонд 6.2/6.4) и `refresh_ready_maker_order`
/// (таск 15, D-ОРДЕР — нарочно **на** своей стороне спреда). Разошедшиеся
/// копии тела кадра — тот класс дефекта, что `SETTLED.md` уже ловил в других
/// файлах; здесь одна реализация на оба правила цены.
#[allow(clippy::too_many_arguments)]
pub fn build_create_frame_at_price<S: OrderSigner>(
    signer: &S,
    symbol: &str,
    side: OrderSide,
    qty_e9: i64,
    price_e9: i64,
    recv_window_ms: u32,
    req_id: &str,
    timestamp_ms: i64,
) -> Result<WsSignedFrame, TradeWsError> {
    let args = CreateArgs {
        category: CATEGORY,
        symbol: symbol.to_string(),
        side: side_str(side),
        order_type: ORDER_TYPE,
        qty: format_e9(qty_e9),
        price: format_e9(price_e9),
        time_in_force: TIME_IN_FORCE,
    };
    let args_json =
        serde_json::to_string(&args).map_err(|e| TradeWsError::Decode(e.to_string()))?;
    let signature_hex = signer
        .sign(timestamp_ms, recv_window_ms, &args_json)
        .map_err(TradeWsError::Credentials)?;
    let header = WsHeader {
        api_key: signer.api_key().to_string(),
        timestamp: timestamp_ms.to_string(),
        recv_window: recv_window_ms.to_string(),
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

/// Кадр `order.create`: post-only минимального размера далеко от середины.
/// Цена — `probe::far_price_e9` (та же функция, что в 6.2: ордер тот же),
/// размер — `params.qty_e9` (минимальный лот, Decision 22).
pub fn build_create_frame<S: OrderSigner>(
    signer: &S,
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
    build_create_frame_at_price(
        signer,
        &params.symbol,
        params.side,
        params.qty_e9,
        price_e9,
        params.recv_window_ms,
        req_id,
        timestamp_ms,
    )
}

/// Кадр `order.cancel` для немедленного снятия (`[ASSUMPTION H9]`).
pub fn build_cancel_frame<S: OrderSigner>(
    signer: &S,
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
    let signature_hex = signer
        .sign(timestamp_ms, params.recv_window_ms, &args_json)
        .map_err(TradeWsError::Credentials)?;
    let header = WsHeader {
        api_key: signer.api_key().to_string(),
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

/// Те же три типа кадра, что `CreateArgs`/`WsHeader`/`WsRequest` выше, но
/// с заимствованными полями вместо `String`/`Vec` — единственная форма,
/// которая позволяет `ReadyMakerOrder::rebuild` не аллоцировать ничего,
/// кроме уже выделенных буферов (дозапрос ревью таска 15, запрет 1).
/// `args` — массив `[_; 1]`, не `Vec`: единственный элемент, и массив не
/// аллоцирует, где `Vec` аллоцировал бы на каждую пересборку.
#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "camelCase")]
struct CreateArgsRef<'a> {
    category: &'static str,
    symbol: &'a str,
    side: &'static str,
    order_type: &'static str,
    qty: &'a str,
    price: &'a str,
    time_in_force: &'static str,
}

#[derive(Debug, Clone, Copy, Serialize)]
struct WsHeaderRef<'a> {
    #[serde(rename = "X-BAPI-API-KEY")]
    api_key: &'a str,
    #[serde(rename = "X-BAPI-TIMESTAMP")]
    timestamp: &'a str,
    #[serde(rename = "X-BAPI-RECV-WINDOW")]
    recv_window: &'a str,
    #[serde(rename = "X-BAPI-SIGN")]
    sign: &'a str,
}

#[derive(Debug, Clone, Copy, Serialize)]
struct WsRequestRef<'a> {
    #[serde(rename = "reqId")]
    req_id: &'a str,
    header: WsHeaderRef<'a>,
    op: &'static str,
    args: [CreateArgsRef<'a>; 1],
}

/// Ордер до триггера (D-ОРДЕР, таск 15): payload собран и подписан заранее,
/// под цену **на своей стороне спреда** (не `far_price_e9` — та нарочно
/// уводит цену прочь от книги, здесь наоборот: вход мейкером у своей стороны,
/// цена известна заранее из последнего обновления книги).
///
/// **Запрет 1 (дозапрос ревью).** Каждое поле кадра — либо `&'static str`,
/// либо буфер, который `rebuild` чистит (`.clear()`, ёмкость остаётся) и
/// заполняет заново: `qty_buf`/`price_buf`/`timestamp_buf`/`recv_window_buf`
/// через `format_e9_into`/`write!`, `sig_hex` — фиксированный массив (подпись
/// всегда 64 hex-байта), `args_body_buf`/`frame_buf` — через
/// `serde_json::to_writer` вместо `serde_json::to_string`. Вызывающий
/// (`commands/lob/react.rs`) обязан звать `rebuild` только когда цена своей
/// стороны спреда реально изменилась (`if changed`), не на каждое обновление
/// книги — иначе даже нулевая аллокация внутри не спасает от лишней подписи
/// на каждый тик. `send_ready_maker_order` — только `send` уже готового
/// кадра, не трогает ни один из этих буферов: тест `rebuild_signs_only_
/// when_called_send_never_signs` доказывает счётчиком через `OrderSigner`-
/// фейк, что срабатывание не подписывает.
pub struct ReadyMakerOrder {
    price_e9: i64,
    args_body_buf: Vec<u8>,
    sig_hex: [u8; 64],
    frame_buf: Vec<u8>,
    req_id_buf: String,
    timestamp_buf: String,
    recv_window_buf: String,
    qty_buf: String,
    price_buf: String,
}

impl ReadyMakerOrder {
    /// Ёмкость с запасом: любое реалистичное числовое поле (цена, размер,
    /// таймстамп, счётчик `reqId`) в разы короче — рост буфера после первой
    /// сборки не нужен, а лишний запас дешевле, чем гоняться за границей.
    pub fn new() -> Self {
        Self {
            price_e9: 0,
            args_body_buf: Vec::with_capacity(256),
            sig_hex: [b'0'; 64],
            frame_buf: Vec::with_capacity(512),
            req_id_buf: String::with_capacity(48),
            timestamp_buf: String::with_capacity(24),
            recv_window_buf: String::with_capacity(24),
            qty_buf: String::with_capacity(32),
            price_buf: String::with_capacity(32),
        }
    }

    pub fn price_e9(&self) -> i64 {
        self.price_e9
    }

    /// Итоговый кадр как строка — `serde_json::to_writer` пишет валидный
    /// UTF-8 в `Vec<u8>` по построению, `expect` не паникующий на практике.
    pub fn frame_str(&self) -> &str {
        std::str::from_utf8(&self.frame_buf).expect("serde_json пишет валидный UTF-8")
    }

    /// Пересобирает и переподписывает ордер под текущую цену своей стороны
    /// спреда, целиком в уже выделенных буферах. Вызывающий обязан звать
    /// это только когда цена реально изменилась — см. doc структуры.
    #[allow(clippy::too_many_arguments)]
    pub fn rebuild<S: OrderSigner>(
        &mut self,
        signer: &S,
        symbol: &str,
        side: OrderSide,
        qty_e9: i64,
        price_e9: i64,
        recv_window_ms: u32,
        req_id: &str,
        timestamp_ms: i64,
    ) -> Result<(), TradeWsError> {
        self.price_e9 = price_e9;

        self.qty_buf.clear();
        format_e9_into(&mut self.qty_buf, qty_e9);
        self.price_buf.clear();
        format_e9_into(&mut self.price_buf, price_e9);

        self.args_body_buf.clear();
        serde_json::to_writer(
            &mut self.args_body_buf,
            &CreateArgsRef {
                category: CATEGORY,
                symbol,
                side: side_str(side),
                order_type: ORDER_TYPE,
                qty: &self.qty_buf,
                price: &self.price_buf,
                time_in_force: TIME_IN_FORCE,
            },
        )
        .map_err(|e| TradeWsError::Decode(e.to_string()))?;
        let body_str = std::str::from_utf8(&self.args_body_buf)
            .map_err(|e| TradeWsError::Decode(e.to_string()))?;

        self.timestamp_buf.clear();
        {
            use std::fmt::Write as _;
            let _ = write!(self.timestamp_buf, "{timestamp_ms}");
        }
        self.recv_window_buf.clear();
        {
            use std::fmt::Write as _;
            let _ = write!(self.recv_window_buf, "{recv_window_ms}");
        }

        signer
            .sign_into(timestamp_ms, recv_window_ms, body_str, &mut self.sig_hex)
            .map_err(TradeWsError::Credentials)?;
        let sign_str = std::str::from_utf8(&self.sig_hex).expect("sign_into пишет hex-ASCII");

        self.req_id_buf.clear();
        self.req_id_buf.push_str(req_id);

        self.frame_buf.clear();
        serde_json::to_writer(
            &mut self.frame_buf,
            &WsRequestRef {
                req_id: &self.req_id_buf,
                header: WsHeaderRef {
                    api_key: signer.api_key(),
                    timestamp: &self.timestamp_buf,
                    recv_window: &self.recv_window_buf,
                    sign: sign_str,
                },
                op: OP_CREATE,
                args: [CreateArgsRef {
                    category: CATEGORY,
                    symbol,
                    side: side_str(side),
                    order_type: ORDER_TYPE,
                    qty: &self.qty_buf,
                    price: &self.price_buf,
                    time_in_force: TIME_IN_FORCE,
                }],
            },
        )
        .map_err(|e| TradeWsError::Decode(e.to_string()))?;
        Ok(())
    }
}

impl Default for ReadyMakerOrder {
    fn default() -> Self {
        Self::new()
    }
}

/// Триггер: только `send` уже готового кадра. Не пересобирает payload и не
/// зовёт подпись — тест `rebuild_signs_only_when_called_send_never_signs`
/// ниже доказывает это счётчиком через `OrderSigner`-фейк, а не чтением
/// этого тела глазами. `.to_string()` здесь — вне измеряемого пути: dry-run
/// `lob react` этот кадр вовсе не отправляет (метка вместо `send`,
/// критерий приёмки «ни одного ордера»), функция нужна только тесту и,
/// в будущем, живому исполнению.
pub fn send_ready_maker_order<T: WsTradeTransport>(
    transport: &mut T,
    ready: &ReadyMakerOrder,
) -> Result<(), TradeWsError> {
    transport.send(ready.frame_str().to_string())
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
mod tests;
