//! REST Bybit v5: instruments-info, orderbook-снапшот, serverTime.
//!
//! Только публичные эндпоинты `market/*` — подписи не требуется (в отличие от
//! `probe.rs`, который ходит в приватный `order/*`). HTTP спрятан за трейтом
//! `PublicRest` тем же приёмом, что `probe.rs::PrivateRest`: сборка запроса,
//! разбор JSON и вся арифметика фильтров и терцилей (`commands/lob.rs`)
//! проверяются тестами без сети; `get` реального транспорта — единственная не
//! тестируемая здесь часть.
//!
//! Decision 18 (`PLAN.md`) ранжирует пул по обороту за 24 часа, а
//! `instruments-info` его не несёт (там только статические свойства контракта
//! и фильтры цены/размера) — оборот отдаёт отдельный эндпоинт `market/tickers`.
//! Без него ранжирование, которое требует Decision 18, нечем было бы построить,
//! поэтому этот файл несёт три эндпоинта, а не два: `instruments-info`,
//! `tickers` и `orderbook`.
//!
//! Все десятичные строки биржи (цена, размер, оборот) разбираются в целые
//! 1e-9 функцией `bybit::ws::parse_e9` — той же, что использует разбор
//! живого потока (`ws.rs`), а не отдельной копией и не через `f64`
//! (`ARCHITECTURE.md` A1): цена вроде `0.000001` или оборот с шестизначной
//! дробной частью не переживают округление `f64`, а `minNotionalValue`
//! (Decision 22) и терцили оборота (Decision 18) сравниваются точно.

use std::time::Duration;

use serde_json::Value;

use crate::bybit::ws::parse_e9;

/// Bybit v5 REST, боевой хост. Не единственная константа файла нарочно:
/// тесты и `lob pick --base-url` (тестовая сеть Bybit) подставляют другой.
pub const BYBIT_MAINNET_URL: &str = "https://api.bybit.com";

const INSTRUMENTS_INFO_PATH: &str = "/v5/market/instruments-info";
const TICKERS_PATH: &str = "/v5/market/tickers";
const ORDERBOOK_PATH: &str = "/v5/market/orderbook";

/// Категория Decision 1: единственная площадка проекта — linear-перпы Bybit.
pub const CATEGORY_LINEAR: &str = "linear";

/// Верхняя граница `limit` у `instruments-info` — задокументированный Bybit v5
/// потолок страницы (не измеренное число, а константа протокола, как топики
/// в `ws.rs`); больше страница просто не отдаст, дальше нужен `nextPageCursor`.
const INSTRUMENTS_PAGE_LIMIT: u32 = 1000;

/// Общий HTTP-таймаут шага 0.7 (дефект В-5): 10 секунд — два порядка ниже
/// часовой каденции авторитета шагов. Процесс без присмотра не вправе
/// висеть на сокете дольше.
const HTTP_TIMEOUT: Duration = Duration::from_secs(10);

/// Глубина REST-снапшота книги для будущего `lob verify` (шаг 0.6, не этот
/// проход): план сверяет топ-50 живой книги, а этот снапшот обязан заведомо
/// накрывать топ-50 целиком, а не совпадать с ним по границе случайно.
///
/// `1000` — задокументированный Bybit потолок `limit` у `GET
/// /v5/market/orderbook` для `category=linear` (и `inverse`): репозиторий
/// `bybit-exchange/docs`, файл `docs/v5/market/orderbook.mdx`, строка
/// параметра `limit` — ``"linear"&"inverse"`: [`1`, `1000`]. Default: `25`.``
/// (проверено запросом к живому файлу репозитория, не по памяти). Это не то
/// же число, что тиры глубины `orderbook.<depth>.<symbol>` **WS**-топика из
/// Decision 3 `PLAN.md` (`1/50/200/1000`) — совпадение с одним из них
/// (`200`) в более ранней версии этой константы было именно такой путаницей
/// двух разных протоколов, не числом из документации REST-эндпоинта. Число
/// не из `PLAN.md` (план не называет его вовсе, только WS-тиры), поэтому
/// источник здесь — документированный потолок биржи, тем же приёмом, что
/// `INSTRUMENTS_PAGE_LIMIT` выше, а не константа из решения плана.
pub const ORDERBOOK_SNAPSHOT_LIMIT: u32 = 1000;

/// Ошибки REST-клиента. Транспорт и отклонение биржей различены: вызывающий
/// код (в первую очередь `lob pick`) обязан реагировать по-разному — сетевая
/// ошибка это кандидат на повтор, `retCode != 0` от биржи — нет.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RestError {
    Transport(String),
    Decode(String),
    Api { ret_code: i32, ret_msg: String },
    MissingField(&'static str),
    BadNumber(&'static str),
}

impl std::fmt::Display for RestError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RestError::Transport(e) => write!(f, "транспорт: {e}"),
            RestError::Decode(e) => write!(f, "разбор JSON: {e}"),
            RestError::Api { ret_code, ret_msg } => {
                write!(f, "биржа отклонила запрос: retCode={ret_code}, {ret_msg}")
            }
            RestError::MissingField(field) => write!(f, "нет обязательного поля {field}"),
            RestError::BadNumber(field) => write!(f, "поле {field} не разобралось как число"),
        }
    }
}

/// `std::error::Error`, а не только `Display`: `commands/lob.rs::run_pick`
/// пробрасывает эту ошибку через `?` в `anyhow::Result`, а `anyhow` требует
/// именно этот трейт для автоматической конвертации.
impl std::error::Error for RestError {}

/// Один инструмент из `instruments-info` — только поля, которые использует
/// Decision 18 (пул, дедуп, `H6`) и Decision 22а (размер как наименьшее
/// допустимое количество: `minOrderQty`, `qtyStep`, `minNotionalValue`).
/// Цена и размер — целые 1e-9 (см. doc модуля).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Instrument {
    pub symbol: String,
    pub base_coin: String,
    pub quote_coin: String,
    pub contract_type: String,
    pub status: String,
    /// `None`, если Bybit не прислал `launchTime` вообще. Такое бывает у
    /// инструментов, заведённых до появления поля в API — отсутствие не
    /// равно нулю миллисекунд эпохи, и молчаливая подстановка `0` читалась бы
    /// как «запущен в 1970» вместо честного «неизвестно» (`commands/lob.rs`
    /// трактует `None` отдельно, а не как псевдо-дату).
    pub launch_time_ms: Option<i64>,
    pub tick_e9: i64,
    pub min_order_qty_e9: i64,
    pub qty_step_e9: i64,
    /// `lotSizeFilter.minNotionalValue`. Отсутствие поля — это `0`, а не
    /// ошибка: у части старых инструментов Bybit его не публикует, и это
    /// означает «дополнительного минимума нет», а не «неизвестно» — в отличие
    /// от `launchTime`, ноль здесь корректный по смыслу дефолт, а не выдумка.
    pub min_notional_value_e9: i64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstrumentsPage {
    pub instruments: Vec<Instrument>,
    pub next_cursor: Option<String>,
}

/// Один тикер `market/tickers`: оборот — ради ранжирования Decision 18,
/// последняя цена — вход формулы размера Decision 22а (`minNotionalValue` в
/// `instruments-info` задан в валюте котировки, а `minOrderQty` — в базовом
/// активе, и без текущей цены их не сравнить вовсе).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Ticker {
    pub symbol: String,
    pub turnover_24h_usd_e9: i64,
    pub last_price_e9: i64,
}

/// REST-снапшот книги — вход будущего `lob verify` (шаг 0.6): книга,
/// проигранная от синтетического снапшота, сверяется с этим снапшотом
/// по `seq` (сквозной счётчик WS и REST; `u` в двух каналах — два разных
/// счётчика и для выравнивания не годится).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OrderbookSnapshot {
    pub symbol: String,
    pub u: u64,
    /// Сквозной `seq` — ключ выравнивания сверки.
    pub seq: u64,
    pub ts_ms: i64,
    pub bids: Vec<(i64, i64)>,
    pub asks: Vec<(i64, i64)>,
}

/// Транспорт публичного REST за трейтом — по образцу `probe.rs::PrivateRest`:
/// сборка запроса и весь разбор проверяются тестами на фейковой реализации,
/// без сети. `query` без кодирования: URL-кодирование — дело реализации
/// (`reqwest::RequestBuilder::query` делает это само).
pub trait PublicRest {
    fn get(&mut self, path: &str, query: &[(&str, &str)]) -> Result<String, RestError>;
}

/// Настоящий транспорт. `reqwest` в `Cargo.toml` собран без фичи `blocking`,
/// поэтому каждый вызов поднимает эфемерный однопоточный рантайм и делает
/// `block_on` на нём: цена моста ничтожна на масштабе «несколько запросов
/// на весь `lob pick`» и «раз в час» авторитета шагов, а `#[tokio::main]`
/// ради синхронного по природе трейта не нужен. Рантайм в полях НЕ хранится
/// нарочно (шаг 0.7, вторая паника В-1): дроп `Runtime` изнутри чужого
/// рантайма паникует «Cannot drop a runtime», и структура с полем-рантаймом
/// роняла бы любой `#[tokio::test]`, где она создана.
pub struct BybitPublicRest {
    client: reqwest::Client,
    base_url: String,
}

impl BybitPublicRest {
    pub fn new(base_url: impl Into<String>) -> Result<Self, RestError> {
        let client = reqwest::Client::builder()
            .timeout(HTTP_TIMEOUT)
            .build()
            .map_err(|e| RestError::Transport(e.to_string()))?;
        Ok(Self {
            client,
            base_url: base_url.into(),
        })
    }
}

impl PublicRest for BybitPublicRest {
    fn get(&mut self, path: &str, query: &[(&str, &str)]) -> Result<String, RestError> {
        let client = self.client.clone(); // reqwest::Client — Arc внутри, клон дёшев
        let url = format!("{}{}", self.base_url, path);
        // Собственные String: `async move` не может занять `query` по ссылке
        // на время жизни короче, чем сам future, который живёт до конца
        // `block_on` — заимствование входного среза туда не дотягивается.
        let pairs: Vec<(String, String)> = query
            .iter()
            .map(|&(k, v)| (k.to_string(), v.to_string()))
            .collect();
        // Шаг 0.7 (дефект В-1): `block_on` изнутри чужого рантайма паникует
        // «Cannot start a runtime from within a runtime». Если вызваны из
        // async-контекста — уйти на эфемерный ОС-поток со своим рантаймом;
        // все отказы (спавн, паника потока, транспорт) маппятся в
        // `RestError::Transport`. Паники нет никогда.
        if tokio::runtime::Handle::try_current().is_ok() {
            let spawn = std::thread::Builder::new()
                .name("bybit-rest-get".to_string())
                .spawn(move || {
                    let rt = tokio::runtime::Builder::new_current_thread()
                        .enable_all()
                        .build()
                        .map_err(|e| RestError::Transport(e.to_string()))?;
                    rt.block_on(async move {
                        let resp = client
                            .get(url)
                            .query(&pairs)
                            .send()
                            .await
                            .map_err(|e| RestError::Transport(e.to_string()))?;
                        resp.text()
                            .await
                            .map_err(|e| RestError::Transport(e.to_string()))
                    })
                });
            match spawn {
                Ok(join) => match join.join() {
                    Ok(res) => res,
                    Err(_) => Err(RestError::Transport(
                        "ОС-поток запроса запаниковал".to_string(),
                    )),
                },
                Err(e) => Err(RestError::Transport(format!("спавн ОС-потока: {e}"))),
            }
        } else {
            let rt = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .map_err(|e| RestError::Transport(e.to_string()))?;
            rt.block_on(async move {
                let resp = client
                    .get(url)
                    .query(&pairs)
                    .send()
                    .await
                    .map_err(|e| RestError::Transport(e.to_string()))?;
                resp.text()
                    .await
                    .map_err(|e| RestError::Transport(e.to_string()))
            })
        }
    }
}

fn str_field<'a>(v: &'a Value, key: &'static str) -> Result<&'a str, RestError> {
    v.get(key)
        .and_then(|x| x.as_str())
        .ok_or(RestError::MissingField(key))
}

fn decimal_field(v: &Value, key: &'static str) -> Result<i64, RestError> {
    parse_e9(str_field(v, key)?).ok_or(RestError::BadNumber(key))
}

/// Общий конверт ответа v5: `retCode`/`retMsg`/`result`. Ненулевой `retCode`
/// возвращается ошибкой раньше, чем вызывающий код увидит `result` вообще —
/// то же решение, что `probe.rs::RestEnvelope`, и по той же причине: биржа
/// не обязана класть в `result` то, что ждёт разбор на отклонении.
fn parse_envelope(body: &str) -> Result<Value, RestError> {
    let v: Value = serde_json::from_str(body).map_err(|e| RestError::Decode(e.to_string()))?;
    // Коды биржи — маленькие целые (0, пятизначные); усечение невозможно
    // на любом диапазоне, который Bybit способен вернуть.
    #[allow(clippy::cast_possible_truncation)]
    let ret_code = v
        .get("retCode")
        .and_then(|x| x.as_i64())
        .ok_or(RestError::MissingField("retCode"))? as i32;
    if ret_code != 0 {
        let ret_msg = v
            .get("retMsg")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string();
        return Err(RestError::Api { ret_code, ret_msg });
    }
    v.get("result")
        .cloned()
        .ok_or(RestError::MissingField("result"))
}

fn parse_instrument(v: &Value) -> Result<Instrument, RestError> {
    let symbol = str_field(v, "symbol")?.to_string();
    let base_coin = str_field(v, "baseCoin")?.to_string();
    let quote_coin = str_field(v, "quoteCoin")?.to_string();
    let contract_type = str_field(v, "contractType")?.to_string();
    let status = str_field(v, "status")?.to_string();
    // `launchTime` — необязательное поле у части инструментов (см. doc
    // `Instrument::launch_time_ms`), поэтому здесь не `str_field`/`?`, а мягкий
    // разбор: отсутствие или нечисловая строка — `None`, не ошибка всего ответа.
    let launch_time_ms = v
        .get("launchTime")
        .and_then(|x| x.as_str())
        .and_then(|s| s.parse::<i64>().ok());

    let price_filter = v
        .get("priceFilter")
        .ok_or(RestError::MissingField("priceFilter"))?;
    let tick_e9 = decimal_field(price_filter, "tickSize")?;

    let lot_filter = v
        .get("lotSizeFilter")
        .ok_or(RestError::MissingField("lotSizeFilter"))?;
    let min_order_qty_e9 = decimal_field(lot_filter, "minOrderQty")?;
    let qty_step_e9 = decimal_field(lot_filter, "qtyStep")?;
    // Отсутствие `minNotionalValue` — легитимный `0`, см. doc поля.
    let min_notional_value_e9 = lot_filter
        .get("minNotionalValue")
        .and_then(|x| x.as_str())
        .and_then(parse_e9)
        .unwrap_or(0);

    Ok(Instrument {
        symbol,
        base_coin,
        quote_coin,
        contract_type,
        status,
        launch_time_ms,
        tick_e9,
        min_order_qty_e9,
        qty_step_e9,
        min_notional_value_e9,
    })
}

/// Разбирает страницу `instruments-info`. Пустая строка курсора — это Bybit'ов
/// способ сказать «страниц больше нет»; `None`, а не `Some("")`, иначе цикл
/// пагинации (`fetch_all_linear_instruments`) продолжал бы ходить за пустыми
/// страницами вечно.
pub fn parse_instruments_info(body: &str) -> Result<InstrumentsPage, RestError> {
    let result = parse_envelope(body)?;
    let list = result
        .get("list")
        .and_then(|x| x.as_array())
        .ok_or(RestError::MissingField("list"))?;
    let instruments = list
        .iter()
        .map(parse_instrument)
        .collect::<Result<Vec<_>, _>>()?;
    let next_cursor = result
        .get("nextPageCursor")
        .and_then(|x| x.as_str())
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string());
    Ok(InstrumentsPage {
        instruments,
        next_cursor,
    })
}

pub fn parse_tickers(body: &str) -> Result<Vec<Ticker>, RestError> {
    let result = parse_envelope(body)?;
    let list = result
        .get("list")
        .and_then(|x| x.as_array())
        .ok_or(RestError::MissingField("list"))?;
    list.iter()
        .map(|t| {
            Ok(Ticker {
                symbol: str_field(t, "symbol")?.to_string(),
                turnover_24h_usd_e9: decimal_field(t, "turnover24h")?,
                last_price_e9: decimal_field(t, "lastPrice")?,
            })
        })
        .collect()
}

fn levels(v: &Value, key: &'static str) -> Result<Vec<(i64, i64)>, RestError> {
    let arr = v
        .get(key)
        .and_then(|x| x.as_array())
        .ok_or(RestError::MissingField(key))?;
    let mut out = Vec::with_capacity(arr.len());
    for pair in arr {
        let p = pair
            .get(0)
            .and_then(|x| x.as_str())
            .ok_or(RestError::MissingField("level price"))?;
        let q = pair
            .get(1)
            .and_then(|x| x.as_str())
            .ok_or(RestError::MissingField("level size"))?;
        let price = parse_e9(p).ok_or(RestError::BadNumber("level price"))?;
        let qty = parse_e9(q).ok_or(RestError::BadNumber("level size"))?;
        out.push((price, qty));
    }
    Ok(out)
}

pub fn parse_orderbook_snapshot(body: &str) -> Result<OrderbookSnapshot, RestError> {
    let result = parse_envelope(body)?;
    let symbol = str_field(&result, "s")?.to_string();
    let u = result
        .get("u")
        .and_then(|x| x.as_u64())
        .ok_or(RestError::MissingField("u"))?;
    let seq = result
        .get("seq")
        .and_then(|x| x.as_u64())
        .ok_or(RestError::MissingField("seq"))?;
    let ts_ms = result
        .get("ts")
        .and_then(|x| x.as_i64())
        .ok_or(RestError::MissingField("ts"))?;
    Ok(OrderbookSnapshot {
        symbol,
        u,
        seq,
        ts_ms,
        bids: levels(&result, "b")?,
        asks: levels(&result, "a")?,
    })
}

/// Тянет весь пул linear-инструментов, страница за страницей. `got_any`
/// останавливает цикл, даже если биржа вернула курсор при пустой странице —
/// без этой защиты дефектный ответ подвесил бы `lob pick` в бесконечном
/// опросе одного и того же курсора вместо явной (хоть и неполной) ошибки.
pub fn fetch_all_linear_instruments<R: PublicRest>(
    client: &mut R,
) -> Result<Vec<Instrument>, RestError> {
    let mut out = Vec::new();
    let mut cursor: Option<String> = None;
    let limit = INSTRUMENTS_PAGE_LIMIT.to_string();
    loop {
        let mut query: Vec<(&str, &str)> = vec![("category", CATEGORY_LINEAR), ("limit", &limit)];
        if let Some(c) = &cursor {
            query.push(("cursor", c.as_str()));
        }
        let body = client.get(INSTRUMENTS_INFO_PATH, &query)?;
        let page = parse_instruments_info(&body)?;
        let got_any = !page.instruments.is_empty();
        out.extend(page.instruments);
        match page.next_cursor {
            Some(c) if got_any => cursor = Some(c),
            _ => break,
        }
    }
    Ok(out)
}

pub fn fetch_linear_tickers<R: PublicRest>(client: &mut R) -> Result<Vec<Ticker>, RestError> {
    let body = client.get(TICKERS_PATH, &[("category", CATEGORY_LINEAR)])?;
    parse_tickers(&body)
}

pub fn fetch_orderbook_snapshot<R: PublicRest>(
    client: &mut R,
    symbol: &str,
    limit: u32,
) -> Result<OrderbookSnapshot, RestError> {
    let limit_str = limit.to_string();
    let body = client.get(
        ORDERBOOK_PATH,
        &[
            ("category", CATEGORY_LINEAR),
            ("symbol", symbol),
            ("limit", &limit_str),
        ],
    )?;
    parse_orderbook_snapshot(&body)
}

#[cfg(test)]
mod tests;
