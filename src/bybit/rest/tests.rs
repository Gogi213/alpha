use super::*;
use std::collections::VecDeque;

struct FakeRest {
    responses: VecDeque<Result<String, RestError>>,
    calls: Vec<(String, Vec<(String, String)>)>,
}

impl FakeRest {
    fn with_responses(responses: Vec<Result<String, RestError>>) -> Self {
        Self {
            responses: responses.into(),
            calls: Vec::new(),
        }
    }
}

impl PublicRest for FakeRest {
    fn get(&mut self, path: &str, query: &[(&str, &str)]) -> Result<String, RestError> {
        self.calls.push((
            path.to_string(),
            query
                .iter()
                .map(|&(k, v)| (k.to_string(), v.to_string()))
                .collect(),
        ));
        self.responses
            .pop_front()
            .unwrap_or_else(|| panic!("тест не подготовил столько ответов"))
    }
}

fn instrument_json(symbol: &str, launch_time: Option<&str>) -> String {
    let launch = match launch_time {
        Some(t) => format!(r#""launchTime":"{t}","#),
        None => String::new(),
    };
    format!(
        r#"{{"symbol":"{symbol}","contractType":"LinearPerpetual","status":"Trading",
          "baseCoin":"SOL","quoteCoin":"USDT",{launch}
          "priceFilter":{{"tickSize":"0.01"}},
          "lotSizeFilter":{{"minOrderQty":"0.1","qtyStep":"0.1","minNotionalValue":"5"}}}}"#
    )
}

fn page_json(symbols: &[&str], next_cursor: Option<&str>) -> String {
    let list: Vec<String> = symbols
        .iter()
        .map(|s| instrument_json(s, Some("1700000000000")))
        .collect();
    let cursor_field = match next_cursor {
        Some(c) => format!(r#","nextPageCursor":"{c}""#),
        None => r#","nextPageCursor":"""#.to_string(),
    };
    format!(
        r#"{{"retCode":0,"retMsg":"OK","result":{{"category":"linear","list":[{}]{}}}}}"#,
        list.join(","),
        cursor_field
    )
}

/// Ровно то, что нужно Decision 18 и Decision 22: шаги цены/размера и
/// `minNotionalValue` — целые 1e-9, разобранные `parse_e9` (см. doc
/// модуля), а не `str::parse::<f64>()`. Отдельный тест
/// `tickers_parse_turnover_without_going_through_f64` ниже пином
/// показывает случай, где `f64`-маршрут дал бы другое число — здесь
/// проверяется полнота извлечения полей, не сама разница с `f64`.
///
/// `tickSize`, `minOrderQty` и `qtyStep` — три разных числа, не общие
/// «0.1» на двоих: с одинаковым `minOrderQty`/`qtyStep` (как было раньше)
/// перепутанные местами поля в `parse_instrument` прошли бы этот тест
/// незамеченными — оба присвоения читали бы то же самое число.
#[test]
fn parse_instrument_reads_decision18_and_decision22_fields() {
    let raw = r#"{"symbol":"SOLUSDT","contractType":"LinearPerpetual","status":"Trading",
      "baseCoin":"SOL","quoteCoin":"USDT","launchTime":"1600000000000",
      "priceFilter":{"tickSize":"0.01"},
      "lotSizeFilter":{"minOrderQty":"0.1","qtyStep":"0.001","minNotionalValue":"5.123456789"}}"#;
    let v: Value = serde_json::from_str(raw).unwrap();
    let inst = parse_instrument(&v).unwrap();
    assert_eq!(inst.symbol, "SOLUSDT");
    assert_eq!(inst.base_coin, "SOL");
    assert_eq!(inst.quote_coin, "USDT");
    assert_eq!(inst.contract_type, "LinearPerpetual");
    assert_eq!(inst.launch_time_ms, Some(1_600_000_000_000));
    assert_eq!(inst.tick_e9, 10_000_000);
    assert_eq!(inst.min_order_qty_e9, 100_000_000);
    assert_eq!(inst.qty_step_e9, 1_000_000);
    // Девять знаков дробной части — предел `parse_e9`; f64 столько не
    // держит без потерь, поэтому проверяется на целом, не на приближении.
    assert_eq!(inst.min_notional_value_e9, 5_123_456_789);
}

/// Отсутствие `launchTime` — это `None`, а не молчаливый `0`: инструмент,
/// заведённый до появления поля, не должен читаться как «запущен в 1970».
#[test]
fn missing_launch_time_is_none_not_zero() {
    let raw = r#"{"symbol":"BTCUSDT","contractType":"LinearPerpetual","status":"Trading",
      "baseCoin":"BTC","quoteCoin":"USDT",
      "priceFilter":{"tickSize":"0.1"},
      "lotSizeFilter":{"minOrderQty":"0.001","qtyStep":"0.001"}}"#;
    let v: Value = serde_json::from_str(raw).unwrap();
    let inst = parse_instrument(&v).unwrap();
    assert_eq!(inst.launch_time_ms, None);
}

/// Отсутствие `minNotionalValue` — легитимный `0` (см. doc поля), не ошибка.
#[test]
fn missing_min_notional_value_defaults_to_zero() {
    let raw = r#"{"symbol":"BTCUSDT","contractType":"LinearPerpetual","status":"Trading",
      "baseCoin":"BTC","quoteCoin":"USDT",
      "priceFilter":{"tickSize":"0.1"},
      "lotSizeFilter":{"minOrderQty":"0.001","qtyStep":"0.001"}}"#;
    let v: Value = serde_json::from_str(raw).unwrap();
    let inst = parse_instrument(&v).unwrap();
    assert_eq!(inst.min_notional_value_e9, 0);
}

#[test]
fn missing_required_field_is_an_error_not_a_panic() {
    let raw = r#"{"symbol":"BTCUSDT","contractType":"LinearPerpetual","status":"Trading",
      "baseCoin":"BTC","quoteCoin":"USDT",
      "lotSizeFilter":{"minOrderQty":"0.001","qtyStep":"0.001"}}"#;
    let v: Value = serde_json::from_str(raw).unwrap();
    assert_eq!(
        parse_instrument(&v).unwrap_err(),
        RestError::MissingField("priceFilter")
    );
}

#[test]
fn api_rejection_is_returned_not_panicked() {
    let body = r#"{"retCode":10001,"retMsg":"invalid category","result":{}}"#;
    let err = parse_instruments_info(body).unwrap_err();
    assert_eq!(
        err,
        RestError::Api {
            ret_code: 10001,
            ret_msg: "invalid category".to_string()
        }
    );
}

#[test]
fn malformed_body_is_a_decode_error_not_a_panic() {
    assert!(matches!(
        parse_instruments_info("not json"),
        Err(RestError::Decode(_))
    ));
}

#[test]
fn instruments_info_pagination_follows_cursor_until_it_is_empty() {
    let mut fake = FakeRest::with_responses(vec![
        Ok(page_json(&["AAAUSDT", "BBBUSDT"], Some("cursor-2"))),
        Ok(page_json(&["CCCUSDT"], None)),
    ]);
    let instruments = fetch_all_linear_instruments(&mut fake).unwrap();
    assert_eq!(
        instruments
            .iter()
            .map(|i| i.symbol.clone())
            .collect::<Vec<_>>(),
        vec!["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    );
    assert_eq!(fake.calls.len(), 2);
    assert!(!fake.calls[0].1.iter().any(|(k, _)| k == "cursor"));
    assert!(fake.calls[1]
        .1
        .iter()
        .any(|(k, v)| k == "cursor" && v == "cursor-2"));
}

/// Курсор непустой, но страница пуста — дефектный ответ биржи. Без этой
/// защиты цикл пагинации опрашивал бы один и тот же курсор бесконечно.
#[test]
fn pagination_stops_on_an_empty_page_even_if_a_cursor_is_present() {
    let body = r#"{"retCode":0,"retMsg":"OK","result":{"category":"linear","list":[],"nextPageCursor":"stale"}}"#;
    let mut fake = FakeRest::with_responses(vec![Ok(body.to_string())]);
    let instruments = fetch_all_linear_instruments(&mut fake).unwrap();
    assert!(instruments.is_empty());
    assert_eq!(
        fake.calls.len(),
        1,
        "пустая страница обязана остановить цикл"
    );
}

/// Оборот — та величина, ради которой этот эндпоинт вообще есть
/// (Decision 18); дробная часть длиннее шести знаков не выживает в `f64`,
/// и терцили оборота обязаны сравнивать точные целые.
#[test]
fn tickers_parse_turnover_without_going_through_f64() {
    let body = r#"{"retCode":0,"retMsg":"OK","result":{"category":"linear",
      "list":[{"symbol":"SOLUSDT","turnover24h":"123456789.123456789","lastPrice":"150.25"}]}}"#;
    let tickers = parse_tickers(body).unwrap();
    assert_eq!(tickers.len(), 1);
    assert_eq!(tickers[0].symbol, "SOLUSDT");
    assert_eq!(tickers[0].turnover_24h_usd_e9, 123_456_789_123_456_789);
    assert_eq!(tickers[0].last_price_e9, 150_250_000_000);
    let via_f64 = ("123456789.123456789".parse::<f64>().unwrap() * 1e9).round() as i64;
    assert_ne!(tickers[0].turnover_24h_usd_e9, via_f64);
}

#[test]
fn tickers_missing_last_price_is_an_error_not_a_panic() {
    let body = r#"{"retCode":0,"retMsg":"OK","result":{"category":"linear",
      "list":[{"symbol":"SOLUSDT","turnover24h":"1"}]}}"#;
    assert_eq!(
        parse_tickers(body).unwrap_err(),
        RestError::MissingField("lastPrice")
    );
}

#[test]
fn fetch_linear_tickers_sends_the_linear_category() {
    let body = r#"{"retCode":0,"retMsg":"OK","result":{"list":[]}}"#;
    let mut fake = FakeRest::with_responses(vec![Ok(body.to_string())]);
    fetch_linear_tickers(&mut fake).unwrap();
    assert_eq!(fake.calls[0].0, TICKERS_PATH);
    assert!(fake.calls[0]
        .1
        .contains(&("category".to_string(), "linear".to_string())));
}

#[test]
fn orderbook_snapshot_parses_bids_and_asks_as_exact_integers() {
    let body = r#"{"retCode":0,"retMsg":"OK","result":{"s":"SOLUSDT",
      "b":[["150.00","2.5"],["149.99","1.0"]],"a":[["150.01","3.0"]],
      "ts":1700000000000,"u":42,"seq":807370000000}}"#;
    let snap = parse_orderbook_snapshot(body).unwrap();
    assert_eq!(snap.symbol, "SOLUSDT");
    assert_eq!(snap.u, 42);
    assert_eq!(snap.seq, 807_370_000_000);
    assert_eq!(snap.ts_ms, 1_700_000_000_000);
    assert_eq!(
        snap.bids,
        vec![
            (150_000_000_000, 2_500_000_000),
            (149_990_000_000, 1_000_000_000)
        ]
    );
    assert_eq!(snap.asks, vec![(150_010_000_000, 3_000_000_000)]);
}

#[test]
fn fetch_orderbook_snapshot_passes_symbol_and_limit() {
    let body = r#"{"retCode":0,"retMsg":"OK","result":{"s":"SOLUSDT","b":[],"a":[],"ts":1,"u":1,"seq":2}}"#;
    let mut fake = FakeRest::with_responses(vec![Ok(body.to_string())]);
    fetch_orderbook_snapshot(&mut fake, "SOLUSDT", ORDERBOOK_SNAPSHOT_LIMIT).unwrap();
    assert_eq!(fake.calls[0].0, ORDERBOOK_PATH);
    assert!(fake.calls[0]
        .1
        .contains(&("symbol".to_string(), "SOLUSDT".to_string())));
    // Значение читается из константы, а не задублировано литералом:
    // тест не должен молча разойтись с `ORDERBOOK_SNAPSHOT_LIMIT`, если
    // её когда-нибудь изменят вслед за документацией Bybit.
    assert!(fake.calls[0]
        .1
        .contains(&("limit".to_string(), ORDERBOOK_SNAPSHOT_LIMIT.to_string())));
}

#[test]
fn orderbook_snapshot_missing_u_is_an_error_not_a_panic() {
    let body =
        r#"{"retCode":0,"retMsg":"OK","result":{"s":"SOLUSDT","b":[],"a":[],"ts":1,"seq":2}}"#;
    assert_eq!(
        parse_orderbook_snapshot(body).unwrap_err(),
        RestError::MissingField("u")
    );
}

#[test]
fn orderbook_snapshot_missing_seq_is_an_error_not_a_panic() {
    let body = r#"{"retCode":0,"retMsg":"OK","result":{"s":"SOLUSDT","b":[],"a":[],"ts":1,"u":1}}"#;
    assert_eq!(
        parse_orderbook_snapshot(body).unwrap_err(),
        RestError::MissingField("seq")
    );
}

/// Транспортная ошибка обязана дойти до вызывающего как есть, а не
/// потеряться где-то в разборе — `fetch_*` не глотает `Err` фейка.
#[test]
fn transport_error_propagates_from_fetch_functions() {
    let mut fake = FakeRest::with_responses(vec![Err(RestError::Transport(
        "connection reset".to_string(),
    ))]);
    assert_eq!(
        fetch_all_linear_instruments(&mut fake).unwrap_err(),
        RestError::Transport("connection reset".to_string())
    );
}

/// Шаг 0.7 (дефект В-1): настоящий `BybitPublicRest::get`, вызванный
/// изнутри tokio-рантайма, обязан вернуть `Err(Transport)`, а не
/// запаниковать «Cannot start a runtime from within a runtime».
/// Закрытый порт даёт быстрый отказ без сети и без долгого таймаута.
#[tokio::test]
async fn real_get_inside_runtime_returns_transport_error_not_panic() {
    let mut rest = BybitPublicRest::new("http://127.0.0.1:9").expect("клиент обязан создаться");
    let err = rest.get("/v5/market/time", &[]).unwrap_err();
    assert!(
        matches!(err, RestError::Transport(_)),
        "ожидался Transport, получен: {err:?}"
    );
}

/// Шаг 0.7 (дефект В-5): HTTP-клиенты создаются с таймаутом.
/// Проверка — грепом по исходникам, тем же приёмом, что граница
/// модулей в `lob/levels.rs`. Литерал собран из частей, чтобы сам
/// тест не давал ложное срабатывание.
#[test]
fn http_clients_are_created_with_a_timeout() {
    let needle = concat!(".", "timeout(");
    assert!(
        include_str!("../rest.rs").contains(needle),
        "rest.rs: клиент без таймаута"
    );
    assert!(
        include_str!("../probe.rs").contains(needle),
        "probe.rs: клиент без таймаута"
    );
    assert!(
        include_str!("../clock.rs").contains(needle),
        "clock.rs: клиент без таймаута"
    );
}
