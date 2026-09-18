//! Замер RTT полного цикла: тик, отправка, подтверждение (шаг 6.2).
//!
//! Decision 12 и `[ASSUMPTION H9]` (`PLAN.md`): один цикл — реальный
//! post-only ордер минимального размера далеко от середины на боевом счёте,
//! немедленно снятый. HTTP скрыт за трейтом `PrivateRest` (Decision 12
//! требует именно этого: «число в G4 обязано получаться командой из
//! репозитория», а не сторонним скриптом) — подпись (`sign.rs`) и вся
//! логика этого файла проверяются тестами без сети и без ключей; настоящий
//! поход на биржу — единственная не тестируемая здесь часть, `BybitPrivateRest`.
//!
//! «Тик» этого файла — не событие книги: живой стакан читает `bybit::ws`
//! (другой файл этого прохода, с `local_ts` на возврате из `recv`,
//! `[ASSUMPTION H12]`), а сюда вызывающий передаёт уже готовую middle-цену
//! на момент решения. Здесь «тик → отправка» — интервал между тем, как
//! вызывающий узнал середину, и тем, как собранный и подписанный запрос
//! ушёл в `PrivateRest::send`; сборка JSON и HMAC занимает микросекунды и
//! не зависит от сети, поэтому не включена в измеряемый `Cycle::rtt_ns` —
//! иначе она смазывала бы именно сетевую задержку, которая и нужна G4 для
//! модели очереди.
//!
//! Трейт синхронный, а не `async fn`: `reqwest` в `Cargo.toml` собран без
//! фичи `blocking` (менять `Cargo.toml` не в этом проходе), а `async fn` в
//! публичном трейте либо тянет предупреждение `async_fn_in_trait` о
//! недостающем `Send`, либо десахарируется в `impl Future + Send`, который
//! конфликтует с `clippy::manual_async_fn` — два предупреждения, отменяющих
//! друг друга без `#[allow]` на каждой реализации. Синхронный трейт снимает
//! это целиком: настоящая реализация — `BybitPrivateRest` — сама поднимает
//! маленький рантайм и блокируется на нём, а это происходит на человеческом
//! масштабе (одна отправка в секунду на весь прогон), где бюджет аллокаций
//! GC ни при чём — тот считается на пути разбор-и-запись книги.

use crate::bybit::sign::Credentials;
use serde::{Deserialize, Serialize};
use std::time::Duration;

/// Общий HTTP-таймаут шага 0.7 (дефект В-5): 10 секунд — два порядка ниже
/// часовой каденции. Процесс без присмотра не вправе висеть на сокете дольше.
const HTTP_TIMEOUT: Duration = Duration::from_secs(10);

/// Bybit v5, linear perp (Decision 1) — единственная площадка проекта,
/// категория запроса не варьируется и потому не параметр.
const CATEGORY: &str = "linear";
/// Post-only существует только у лимитных ордеров — это фиксирует протокол
/// Bybit, не наш выбор.
const ORDER_TYPE: &str = "Limit";
/// `[ASSUMPTION H9]`: ордер обязан быть мейкером, не улучшающим цену.
/// `PostOnly` — это же и защита: биржа отклонит ордер вместо того, чтобы
/// тихо исполнить его тейкером, если цена всё-таки пересекла книгу.
const TIME_IN_FORCE: &str = "PostOnly";
const CREATE_ORDER_PATH: &str = "/v5/order/create";
const CANCEL_ORDER_PATH: &str = "/v5/order/cancel";

/// Дефолт `recv_window`, задокументированный самой Bybit (не наше число):
/// пять секунд запаса на рассинхрон часов и время в пути. Параметр, не
/// жёсткая константа — хост из `H12` (VPS в регионе входа Bybit) может
/// стабильно укладываться в меньшее окно, а более тесное окно теснее
/// привязывает измерение к реальному разбросу задержки.
pub const DEFAULT_RECV_WINDOW_MS: u32 = 5000;

/// Минимум циклов на распределение — done-condition шага 6.2 (`PLAN.md`:
/// «распределение RTT по ≥ 1000 циклов»). Меньше — и 95-й перцентиль
/// опирается на единицы точек в хвосте (5% от `n`), а не на статистически
/// осмысленное число.
pub const MIN_CYCLES: usize = 1000;

/// Сторона размещаемого ордера.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OrderSide {
    Buy,
    Sell,
}

impl OrderSide {
    fn as_bybit_str(self) -> &'static str {
        match self {
            OrderSide::Buy => "Buy",
            OrderSide::Sell => "Sell",
        }
    }
}

/// Параметры одного прогона зонда. Ничего не захардкожено сверх того, что
/// задано самим Decision 1 (`linear`) и H9 (post-only, `PostOnly`): размер,
/// смещение и окно приходят с биржи или явно от вызывающего.
#[derive(Debug, Clone)]
pub struct ProbeParams {
    pub symbol: String,
    pub side: OrderSide,
    /// `lotSizeFilter.minOrderQty` инструмента (Decision 22) — минимальный
    /// лот биржи. Читается вызывающим с REST `instruments-info` (другой
    /// файл, `rest.rs`) на момент запуска, не хардкодится: у разных
    /// символов разный минимальный лот, и он может смениться биржей.
    pub qty_e9: i64,
    pub tick_e9: i64,
    /// Смещение цены от середины в тиках — параметр, не константа, см.
    /// `far_price_e9`.
    pub ticks_from_mid: i64,
    pub recv_window_ms: u32,
}

/// Цена post-only ордера «далеко от середины» (`[ASSUMPTION H9]`).
///
/// `ticks_from_mid` — параметр, а не константа: «далеко» обязано быть
/// больше текущего спреда символа, иначе post-only либо отклонится биржей
/// (пересёк книгу), либо исполнится как тейкер — оба исхода портят именно
/// то измерение, ради которого ставится ордер. Спред отличается от символа
/// к символу и от часа к часу на порядок, поэтому зашитое число было бы
/// либо избыточным, либо недостаточным уже на соседнем инструменте или
/// через час на этом же — решение о величине смещения принимает вызывающий,
/// глядя на текущую живую книгу, а не код этого файла.
pub fn far_price_e9(mid_price_e9: i64, tick_e9: i64, ticks_from_mid: i64, side: OrderSide) -> i64 {
    assert!(
        ticks_from_mid > 0,
        "смещение обязано уводить цену прочь от книги, а не в неё или на неё"
    );
    match side {
        OrderSide::Buy => mid_price_e9 - ticks_from_mid * tick_e9,
        OrderSide::Sell => mid_price_e9 + ticks_from_mid * tick_e9,
    }
}

/// Обратное к `bybit::ws::parse_e9`: величина в 1e-9 обратно в десятичную
/// строку, которую API Bybit принимает для `qty`/`price`. Не переиспользует
/// `ws.rs` (другой файл этого прохода) для инверсии — направления разные, а
/// `parse_e9` там приватного формата не задаёт; тест
/// `format_e9_round_trips_through_ws_parse_e9` ниже проверяет, что оба
/// файла всё равно говорят об одной и той же величине.
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

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct CreateOrderBody {
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
struct CancelOrderBody {
    category: &'static str,
    symbol: String,
    order_id: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct OrderResult {
    /// `#[serde(default)]`: на отклонении Bybit шлёт `"result":{}` — тот же
    /// объект, но без ключа `orderId` вовсе, а не с пустой строкой в нём.
    /// Без дефолта разбор такого ответа падал бы здесь, до того как код
    /// вообще посмотрел на `ret_code`, — ровно тот дефект, который эта
    /// структура ловит.
    #[serde(default)]
    order_id: String,
}

/// Общий конверт ответа v5 для `order/create` и `order/cancel`: у обоих
/// одна форма, `retCode`/`retMsg`/`result.orderId` (лишние поля вроде
/// `retExtInfo`/`time` серде молча игнорирует).
///
/// `result` — `Option`, а не обязательное поле: при ненулевом `retCode`
/// Bybit присылает `"result":{}` — без `orderId` вообще, а не пустую
/// строку в нём. Обязательное поле роняло бы разбор `serde`-ошибкой на
/// каждом отклонении ордера ещё до того, как код вообще посмотрел на
/// `ret_code`, — и вызывающий вместо `OrderRejected`/`CancelFailed`
/// (единственных ошибок, которые этот модуль обещает отличать) видел бы
/// неотличимый от битого JSON `Decode`.
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RestEnvelope {
    ret_code: i32,
    ret_msg: String,
    #[serde(default)]
    result: Option<OrderResult>,
}

impl RestEnvelope {
    /// `order_id` нужен только на успешном ответе, и оба места вызова
    /// проверяют `ret_code` раньше. Но «на успехе Bybit всегда кладёт
    /// `orderId`» — утверждение про поведение чужого сервера, а не локальный
    /// инвариант, и держать на нём `expect` нельзя: замер идёт тысячей циклов
    /// на боевом счёте, и один кривой ответ уронил бы весь прогон вместо того,
    /// чтобы стоить одну точку. До правки этого разбора такой ответ давал
    /// `Decode`, то есть обычную ошибку, — паника здесь была бы регрессией.
    fn require_order_id(self) -> Result<String, ProbeError> {
        // Пустая строка отвергается наравне с отсутствием поля, и это не
        // придирка: `#[serde(default)]` на `order_id` превращает `"result":{}`
        // в `Some(OrderResult { order_id: "" })`, то есть проверка одного лишь
        // `Option` пропускала бы такой ответ как успешный. Дальше пустой
        // идентификатор ушёл бы в запрос отмены на боевом счёте — это хуже,
        // чем ошибка разбора: ордер остался бы висеть, а зонд считал бы, что
        // снял его.
        match self.result.map(|r| r.order_id) {
            Some(id) if !id.is_empty() => Ok(id),
            _ => Err(ProbeError::MissingOrderId {
                ret_code: self.ret_code,
            }),
        }
    }
}

/// Заголовки уже подписанного запроса. `signature_hex` — необратимый дайджест
/// (свойство HMAC), не секрет; `api_key` не криптографический секрет тоже
/// (открытый по протоколу заголовок), но Decision 12 (`PLAN.md`) требует,
/// чтобы величина, идентифицирующая счёт, не попадала в файлы и отчёты
/// вообще — а прежний `derive(Debug)` печатал `api_key` как есть в любом
/// логе или панике на этой структуре. Ручной `Debug` ниже — тот же приём,
/// что у `Credentials` в `sign.rs`, и по той же причине: автогенерация не
/// умеет знать, какое поле нельзя печатать.
#[derive(Clone)]
pub struct RequestHeaders {
    pub api_key: String,
    pub timestamp_ms: i64,
    pub recv_window_ms: u32,
    pub signature_hex: String,
}

impl std::fmt::Debug for RequestHeaders {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("RequestHeaders")
            .field("api_key", &"<redacted>")
            .field("timestamp_ms", &self.timestamp_ms)
            .field("recv_window_ms", &self.recv_window_ms)
            .field("signature_hex", &self.signature_hex)
            .finish()
    }
}

/// Один запрос, готовый к отправке транспортом.
///
/// Собственный `Debug`, не `derive`: `derive` печатал бы `headers` через её
/// же `Debug` (и потому уже не потёк бы `api_key` благодаря редакции выше),
/// но полагаться на то, что вложенное поле когда-нибудь случайно останется
/// прозрачным, — это и есть способ, которым такая утечка возвращается;
/// явная редакция здесь не зависит от того, что делает соседний тип.
#[derive(Clone)]
pub struct SignedRequest {
    pub path: &'static str,
    pub headers: RequestHeaders,
    pub body: String,
}

impl std::fmt::Debug for SignedRequest {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SignedRequest")
            .field("path", &self.path)
            .field("headers", &self.headers)
            .field("body", &self.body)
            .finish()
    }
}

fn sign_request(
    creds: &Credentials,
    path: &'static str,
    recv_window_ms: u32,
    body: String,
) -> Result<SignedRequest, ProbeError> {
    let timestamp_ms = wall_clock_timestamp_ms()?;
    let signature_hex = creds
        .sign(timestamp_ms, recv_window_ms, &body)
        .map_err(ProbeError::Credentials)?;
    Ok(SignedRequest {
        path,
        headers: RequestHeaders {
            api_key: creds.api_key().to_string(),
            timestamp_ms,
            recv_window_ms,
            signature_hex,
        },
        body,
    })
}

/// Транспорт приватного REST, отделённый от `reqwest`: цикл зонда — подпись
/// уже вложена вызывающим в `req.headers`, трейт про HMAC вообще не знает —
/// проверяется тестами на фейковой реализации, без сети и без ключей.
pub trait PrivateRest {
    /// `req.path` без хоста, `req.body` — уже сериализованное JSON-тело.
    /// Возвращает тело ответа как есть; разбор — на вызывающем.
    fn send(&mut self, req: SignedRequest) -> Result<String, ProbeError>;
}

/// Настоящий транспорт. `reqwest` в `Cargo.toml` собран без фичи
/// `blocking` (см. doc модуля), поэтому здесь свой рантайм на один поток и
/// `block_on` на каждый вызов — цена этого моста ничтожна на масштабе
/// «одна отправка в секунду», а поднимать `#[tokio::main]` ради синхронного
/// по своей природе трейта незачем.
pub struct BybitPrivateRest {
    client: reqwest::Client,
    base_url: String,
    runtime: tokio::runtime::Runtime,
}

impl BybitPrivateRest {
    pub fn new(base_url: impl Into<String>) -> Result<Self, ProbeError> {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|e| ProbeError::Transport(e.to_string()))?;
        let client = reqwest::Client::builder()
            .timeout(HTTP_TIMEOUT)
            .build()
            .map_err(|e| ProbeError::Transport(e.to_string()))?;
        Ok(Self {
            client,
            base_url: base_url.into(),
            runtime,
        })
    }
}

impl PrivateRest for BybitPrivateRest {
    fn send(&mut self, req: SignedRequest) -> Result<String, ProbeError> {
        let client = self.client.clone(); // reqwest::Client — Arc внутри, клон дёшев
        let url = format!("{}{}", self.base_url, req.path);
        let headers = req.headers;
        let body = req.body;
        // `async move` не занимает ничего из `self` кроме уже склонированного
        // `client`: если бы блок ссылался на `self.client` напрямую, заём
        // конфликтовал бы с `&mut self.runtime` ниже.
        self.runtime.block_on(async move {
            let resp = client
                .post(url)
                .header("X-BAPI-API-KEY", headers.api_key)
                .header("X-BAPI-TIMESTAMP", headers.timestamp_ms.to_string())
                .header("X-BAPI-RECV-WINDOW", headers.recv_window_ms.to_string())
                .header("X-BAPI-SIGN", headers.signature_hex)
                .header("Content-Type", "application/json")
                .body(body)
                .send()
                .await
                .map_err(|e| ProbeError::Transport(e.to_string()))?;
            resp.text()
                .await
                .map_err(|e| ProbeError::Transport(e.to_string()))
        })
    }
}

/// Ошибки зонда. Ни один вариант не может содержать секрет: секрет не идёт
/// ни в тело, ни в URL, ни в заголовки как есть — только его необратимый
/// HMAC-дайджест, а транспортные ошибки несут лишь текст `reqwest`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProbeError {
    Credentials(crate::bybit::sign::CredentialsError),
    TooFewCycles {
        requested: usize,
        minimum: usize,
    },
    Transport(String),
    Decode(String),
    /// Биржа отклонила создание ордера. Отмена в этом случае не
    /// вызывается: без `order_id` снимать нечего.
    OrderRejected {
        ret_code: i32,
        ret_msg: String,
    },
    /// Ответ пришёл успешным, но `orderId` в нём нет. Отдельный вариант,
    /// а не паника: утверждение «на успехе поле есть всегда» описывает
    /// чужой сервер, и цена ошибки в нём — одна точка измерения, а не
    /// упавший прогон на тысячу циклов.
    MissingOrderId {
        ret_code: i32,
    },
    /// Ордер встал, но снять его не получилось. Висящий ордер на боевом
    /// счёте (H9) опаснее одной потерянной точки измерения, поэтому это
    /// тоже ошибка всего цикла, а не лог и продолжение.
    CancelFailed {
        order_id: String,
        ret_code: i32,
        ret_msg: String,
    },
    /// Системные часы раньше эпохи Unix: мёртвая RTC или несконфигурированная
    /// VM. Подпись требует настенное время, выдумывать его из нуля значило бы
    /// слать бирже заведомо неверный timestamp (см. `wall_clock_timestamp_ms`).
    WallClockBeforeEpoch,
}

/// Время эпохи в миллисекундах — **только** для `X-BAPI-TIMESTAMP`: подпись
/// v5 сверяется биржей против её же представления о UTC (`recv_window`
/// защищает именно от рассинхрона с этим временем, H12). Это ровно та
/// потребность, ради которой годится настенное время и не годится
/// `Instant` — `Instant` ничего не говорит о том, «какой сейчас час по UTC»,
/// а подписи нужно именно это. Не переиспользуется циклом ниже — см. его
/// комментарий: там та же функция была бы уже дефектом, а не удобством.
/// Каст точен до 2262 года (миллисекунды эпохи ~1.7e12 против `i64::MAX`).
#[allow(clippy::cast_possible_truncation)]
pub(crate) fn wall_clock_timestamp_ms() -> Result<i64, ProbeError> {
    Ok(std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|_| ProbeError::WallClockBeforeEpoch)?
        .as_millis() as i64)
}

/// Три метки одного цикла — `Instant`, не показания настенных часов.
///
/// Раньше здесь стояли наносекунды `SystemTime` (A1 требует целые, но не
/// требует именно эту величину), и разность двух таких меток выдавалась за
/// RTT. Дефект: NTP на хосте (H12) вправе подвести часы скачком между
/// `order_sent` и `ack_received`, и тогда `ack - sent` — это знак и величина
/// той правки, а не время в сети, причём отличить одно от другого по самому
/// числу нельзя — оно просто попадает в выборку G4 как есть, вплоть до
/// отрицательного. `Instant` — монотонные часы платформы (гарантия stdlib),
/// поэтому разность двух `Instant` одного процесса не может стать
/// отрицательной и не зависит от коррекции NTP вообще.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Cycle {
    pub tick_observed: std::time::Instant,
    pub order_sent: std::time::Instant,
    pub ack_received: std::time::Instant,
}

impl Cycle {
    /// RTT одного цикла — величина, которая в итоге перцентилируется:
    /// интервал от отправки до подтверждения, без времени на сборку и
    /// подпись запроса (см. doc модуля).
    ///
    /// `Instant::duration_since` у стабильной stdlib не паникует и не уходит
    /// в отрицательное на непорядке аргументов (насыщается нулём) — здесь
    /// это не подстраховка от бага, а следствие самого выбора `Instant`
    /// (см. doc `Cycle`): порядок `order_sent ≤ ack_received` и так гарантирован
    /// однопоточным вызовом `run_cycle` ниже. Каст точен: RTT — миллисекунды,
    /// `i64` наносекунд хватает на 292 года.
    #[allow(clippy::cast_possible_truncation)]
    pub fn rtt_ns(&self) -> i64 {
        self.ack_received.duration_since(self.order_sent).as_nanos() as i64
    }
}

/// Один цикл: тик → сборка и подпись ордера → отправка → подтверждение →
/// немедленная отмена (`[ASSUMPTION H9]`). Отмена входит в цикл, но не в
/// измеряемый `Cycle::rtt_ns`, и обязана состояться до возврата — см.
/// `ProbeError::CancelFailed`.
pub fn run_cycle<R: PrivateRest>(
    client: &mut R,
    creds: &Credentials,
    params: &ProbeParams,
    mid_price_e9: i64,
) -> Result<Cycle, ProbeError> {
    let tick_observed = std::time::Instant::now();

    let price_e9 = far_price_e9(
        mid_price_e9,
        params.tick_e9,
        params.ticks_from_mid,
        params.side,
    );
    let create_body = CreateOrderBody {
        category: CATEGORY,
        symbol: params.symbol.clone(),
        side: params.side.as_bybit_str(),
        order_type: ORDER_TYPE,
        qty: format_e9(params.qty_e9),
        price: format_e9(price_e9),
        time_in_force: TIME_IN_FORCE,
    };
    let create_body_json =
        serde_json::to_string(&create_body).map_err(|e| ProbeError::Decode(e.to_string()))?;
    let create_req = sign_request(
        creds,
        CREATE_ORDER_PATH,
        params.recv_window_ms,
        create_body_json,
    )?;

    let order_sent = std::time::Instant::now();
    let create_resp = client.send(create_req)?;
    let ack_received = std::time::Instant::now();

    let envelope: RestEnvelope =
        serde_json::from_str(&create_resp).map_err(|e| ProbeError::Decode(e.to_string()))?;
    if envelope.ret_code != 0 {
        return Err(ProbeError::OrderRejected {
            ret_code: envelope.ret_code,
            ret_msg: envelope.ret_msg,
        });
    }
    let order_id = envelope.require_order_id()?;

    let cancel_body = CancelOrderBody {
        category: CATEGORY,
        symbol: params.symbol.clone(),
        order_id: order_id.clone(),
    };
    let cancel_body_json =
        serde_json::to_string(&cancel_body).map_err(|e| ProbeError::Decode(e.to_string()))?;
    let cancel_req = sign_request(
        creds,
        CANCEL_ORDER_PATH,
        params.recv_window_ms,
        cancel_body_json,
    )?;
    let cancel_resp = client.send(cancel_req)?;
    let cancel_envelope: RestEnvelope =
        serde_json::from_str(&cancel_resp).map_err(|e| ProbeError::Decode(e.to_string()))?;
    if cancel_envelope.ret_code != 0 {
        return Err(ProbeError::CancelFailed {
            order_id,
            ret_code: cancel_envelope.ret_code,
            ret_msg: cancel_envelope.ret_msg,
        });
    }

    Ok(Cycle {
        tick_observed,
        order_sent,
        ack_received,
    })
}

/// Прогоняет `n_cycles` циклов подряд. Останавливается на первой ошибке, а
/// не копит частичный результат: `ProbeError::CancelFailed` значит
/// подвисший ордер на боевом счёте, и продолжать вслепую опаснее, чем
/// потерять прогон целиком — решение о повторных попытках и об аварийной
/// остановке принимает вызывающий (`commands/`, не этот файл), а не скрытая
/// политика ретраев здесь.
pub fn run_cycles<R: PrivateRest>(
    client: &mut R,
    creds: &Credentials,
    params: &ProbeParams,
    mut mid_price_e9: impl FnMut() -> i64,
    n_cycles: usize,
) -> Result<Vec<Cycle>, ProbeError> {
    let mut cycles = Vec::with_capacity(n_cycles);
    for _ in 0..n_cycles {
        let mid = mid_price_e9();
        cycles.push(run_cycle(client, creds, params, mid)?);
    }
    Ok(cycles)
}

/// Перцентиль методом «ближайший ранг»: результат — всегда одно из реально
/// наблюдённых значений выборки, не число между ними.
///
/// Почему не линейная интерполяция (перцентиль NumPy/Excel по умолчанию,
/// метод «Type 7»): величины здесь — целые наносекунды (A1, `ARCHITECTURE.md`),
/// и интерполяция между двумя целыми родила бы дробную наносекунду, которая
/// не соответствует ни одному реальному измерению. Гейт G4 сравнивает PnL с
/// «95-м перцентилем RTT» — числом, которое обязано быть точкой, когда-то
/// реально случившейся, а не средним между двумя соседними наблюдениями.
///
/// `p` — целый процент, `1..=100`. `samples` не обязана быть отсортирована.
pub fn percentile_ns(samples: &[i64], p: u8) -> i64 {
    let mut sorted = samples.to_vec();
    sorted.sort_unstable();
    percentile_of_sorted(&sorted, p)
}

/// Тело `percentile_ns` без сортировки — принимает уже отсортированную
/// выборку. Вынесено отдельно, потому что `summarize` спрашивает два
/// перцентиля (медиану и p95) одной и той же выборки: через `percentile_ns`
/// это были бы два независимых `to_vec()` и `sort_unstable()` ради одного и
/// того же порядка — две аллокации и два O(n log n) там, где вопрос один.
///
/// Индекс ниже доказуемо в границах (asserts дают n ≥ 1 и p ∈ 1..=100,
/// откуда rank ∈ 1..=n), проверка через `get` — мёртвый код ради линта.
#[allow(clippy::indexing_slicing)]
fn percentile_of_sorted(sorted: &[i64], p: u8) -> i64 {
    assert!(!sorted.is_empty(), "перцентиль пустой выборки не определён");
    assert!(
        (1..=100).contains(&p),
        "перцентиль задаётся в диапазоне 1..=100"
    );
    let n = sorted.len();
    // rank = ceil(p/100 * n), 1-based; целочисленно (A1): для положительных
    // a, b верно ceil(a/b) = (a + b - 1)/b, что и делает `div_ceil`.
    let rank = (p as usize * n).div_ceil(100);
    sorted[rank - 1]
}

/// Сводка распределения RTT одного прогона — done-condition шага 6.2.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RttSummary {
    pub n: usize,
    pub median_ns: i64,
    pub p95_ns: i64,
}

pub fn summarize(cycles: &[Cycle]) -> RttSummary {
    let mut rtts: Vec<i64> = cycles.iter().map(Cycle::rtt_ns).collect();
    // Один `sort_unstable` на обе оценки — см. doc `percentile_of_sorted`:
    // медиана и p95 задают один и тот же порядок, сортировать его дважды
    // ради двух чисел было бы платой без причины.
    rtts.sort_unstable();
    RttSummary {
        n: rtts.len(),
        median_ns: percentile_of_sorted(&rtts, 50),
        p95_ns: percentile_of_sorted(&rtts, 95),
    }
}

/// Единственная точка входа `lob probe` (Decision 12). Отказывается
/// работать без обеих переменных окружения (Decision 12, проверено раньше
/// первой сетевой попытки) и меньше чем на `MIN_CYCLES` циклов.
pub fn probe<R: PrivateRest>(
    client: &mut R,
    params: &ProbeParams,
    mid_price_e9: impl FnMut() -> i64,
    n_cycles: usize,
) -> Result<RttSummary, ProbeError> {
    if n_cycles < MIN_CYCLES {
        return Err(ProbeError::TooFewCycles {
            requested: n_cycles,
            minimum: MIN_CYCLES,
        });
    }
    let creds = Credentials::from_env().map_err(ProbeError::Credentials)?;
    let cycles = run_cycles(client, &creds, params, mid_price_e9, n_cycles)?;
    Ok(summarize(&cycles))
}

#[cfg(test)]
mod tests;
