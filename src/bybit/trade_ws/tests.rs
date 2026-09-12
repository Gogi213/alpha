use super::*;
use std::cell::Cell;
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
    let ack = parse_ack_frame(&ack_json(OP_CREATE, "ws-1", "abc-1"), OP_CREATE, "ws-1").unwrap();
    assert_eq!(ack.ret_code, 0);
    assert_eq!(ack.order_id, "abc-1");
}

#[test]
fn parse_ack_rejects_a_foreign_op() {
    let err = parse_ack_frame(&ack_json(OP_CANCEL, "ws-1", "x"), OP_CREATE, "ws-1").unwrap_err();
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
    let err = parse_ack_frame(&ack_json(OP_CREATE, "ws-9", "x"), OP_CREATE, "ws-1").unwrap_err();
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

// -----------------------------------------------------------------------
// D-ОРДЕР (таск 15): payload собран и подписан до триггера, триггер
// делает только `send`.
// -----------------------------------------------------------------------

/// `OrderSigner`-фейк, считающий вызовы `sign`: единственный способ
/// доказать критерий приёмки «подпись не вызывается из ветки
/// срабатывания» — не чтением тела функции глазами, а счётчиком.
struct CountingSigner<'a> {
    inner: &'a Credentials,
    calls: Cell<u32>,
}

impl OrderSigner for CountingSigner<'_> {
    fn sign(
        &self,
        timestamp_ms: i64,
        recv_window_ms: u32,
        body: &str,
    ) -> Result<String, CredentialsError> {
        self.calls.set(self.calls.get() + 1);
        self.inner.sign(timestamp_ms, recv_window_ms, body)
    }

    fn api_key(&self) -> &str {
        self.inner.api_key()
    }
}

#[test]
fn build_create_frame_at_price_carries_the_exact_price_given() {
    let params = test_params();
    let own_side_price_e9 = 100_010_000_000; // на своей стороне спреда, не far_price_e9
    let frame = build_create_frame_at_price(
        &test_creds(),
        &params.symbol,
        params.side,
        params.qty_e9,
        own_side_price_e9,
        params.recv_window_ms,
        "r-1",
        TS_MS,
    )
    .unwrap();
    let v: serde_json::Value = serde_json::from_str(&frame.frame).unwrap();
    assert_eq!(v["args"][0]["price"], format_e9(own_side_price_e9));
    assert_eq!(v["args"][0]["qty"], format_e9(params.qty_e9));
}

/// Критерий приёмки таска 15 буквально: подготовка (`ReadyMakerOrder::
/// rebuild`) подписывает; отправка (`send_ready_maker_order`) — нет.
/// Счётчик `sign` не двигается между «после подготовки» и «после
/// отправки», хотя кадр реально уходит транспорту.
#[test]
fn trigger_branch_sends_a_ready_frame_without_resigning_it() {
    let creds = test_creds();
    let signer = CountingSigner {
        inner: &creds,
        calls: Cell::new(0),
    };
    let params = test_params();

    let mut ready = ReadyMakerOrder::new();
    ready
        .rebuild(
            &signer,
            &params.symbol,
            params.side,
            params.qty_e9,
            100_010_000_000,
            params.recv_window_ms,
            "react-1",
            TS_MS,
        )
        .unwrap();
    let calls_after_rebuild = signer.calls.get();
    assert!(calls_after_rebuild >= 1, "подготовка обязана подписывать");

    let mut fake = FakeWs::with_frames(Vec::new());
    let sent_frame = ready.frame_str().to_string();
    send_ready_maker_order(&mut fake, &ready).unwrap();

    assert_eq!(
        signer.calls.get(),
        calls_after_rebuild,
        "триггер не имеет права подписывать повторно"
    );
    assert_eq!(fake.sent, vec![sent_frame]);
}

/// Дозапрос ревью таска 15 буквально: «подписывает только при смене
/// цены, триггер — никогда». Вызывающий (`commands/lob/react.rs`) зовёт
/// `rebuild` только внутри `if changed` — здесь это выражено явно: цикл
/// ниже зовёт `rebuild` дважды (цена реально другая оба раза), счётчик
/// подписи равен двум, не трём и не нулю; срабатывание в конце не
/// добавляет ни одной.
#[test]
fn rebuild_signs_only_when_called_send_never_signs() {
    let creds = test_creds();
    let signer = CountingSigner {
        inner: &creds,
        calls: Cell::new(0),
    };
    let params = test_params();
    let mut ready = ReadyMakerOrder::new();

    for (i, price_e9) in [100_000_000_000i64, 100_010_000_000]
        .into_iter()
        .enumerate()
    {
        ready
            .rebuild(
                &signer,
                &params.symbol,
                params.side,
                params.qty_e9,
                price_e9,
                params.recv_window_ms,
                &format!("react-{i}"),
                TS_MS,
            )
            .unwrap();
    }
    assert_eq!(
        signer.calls.get(),
        2,
        "подпись — по разу на каждый вызов rebuild, не более"
    );

    let mut fake = FakeWs::with_frames(Vec::new());
    send_ready_maker_order(&mut fake, &ready).unwrap();
    assert_eq!(
        signer.calls.get(),
        2,
        "срабатывание не добавило ни одной подписи"
    );
}

#[test]
fn format_e9_into_matches_format_e9() {
    for &v in &[
        150_010_000_000i64,
        1_000_000,
        12_345_678_900_000,
        1,
        -150_010_000_000,
        0,
    ] {
        let mut buf = String::new();
        format_e9_into(&mut buf, v);
        assert_eq!(buf, format_e9(v), "расхождение на {v}");
    }
}

/// Фейк-подписант, пишущий фиксированный hex прямо в буфер — ни одной
/// аллокации (дозапрос ревью: тест `alloc_count` на `rebuild` обязан
/// доказать нулевые аллокации после первой сборки, а сквозь реальный
/// `Credentials` этого не проверить — `sign.rs` не в зоне этого таска
/// и его собственная аллокация остаётся его делом).
struct ZeroAllocSigner {
    api_key: String,
}

impl OrderSigner for ZeroAllocSigner {
    fn sign(
        &self,
        _timestamp_ms: i64,
        _recv_window_ms: u32,
        _body: &str,
    ) -> Result<String, CredentialsError> {
        unreachable!("тест зовёт только sign_into")
    }
    fn api_key(&self) -> &str {
        &self.api_key
    }
    fn sign_into(
        &self,
        _timestamp_ms: i64,
        _recv_window_ms: u32,
        _body: &str,
        out: &mut [u8; 64],
    ) -> Result<(), CredentialsError> {
        out.fill(b'a');
        Ok(())
    }
}

/// Запрет 1 (дозапрос ревью): после первой сборки `rebuild` не
/// аллоцирует — ни в JSON-теле, ни в подписи, ни в итоговом кадре.
/// Первый вызов (прогрев) годится на рост ёмкости буферов; 10⁶
/// последующих — только на том же наборе цен (реалистичный размер
/// строк не растёт), поэтому ёмкость не тронута ни разу.
#[test]
fn rebuild_allocates_nothing_after_the_first_call() {
    const WARMUP: usize = 1;
    const MEASURED: usize = 1_000_000;

    let signer = ZeroAllocSigner {
        api_key: "test-key".to_string(),
    };
    let params = test_params();
    let mut ready = ReadyMakerOrder::new();
    let prices = [100_000_000_000i64, 100_010_000_000, 99_990_000_000];

    for i in 0..WARMUP {
        ready
            .rebuild(
                &signer,
                &params.symbol,
                params.side,
                params.qty_e9,
                prices[i % prices.len()],
                params.recv_window_ms,
                "react-warmup",
                TS_MS,
            )
            .unwrap();
    }

    let mut total_allocations = 0u64;
    for i in 0..MEASURED {
        let (_, counts) = crate::alloc_count::measure(|| {
            ready
                .rebuild(
                    &signer,
                    &params.symbol,
                    params.side,
                    params.qty_e9,
                    prices[i % prices.len()],
                    params.recv_window_ms,
                    "react-fixed",
                    TS_MS,
                )
                .unwrap()
        });
        total_allocations += counts.allocations;
    }
    assert_eq!(
        total_allocations, 0,
        "rebuild аллоцировал после прогрева — запрет 1 interfaces.md"
    );
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
        .send(tokio_tungstenite::tungstenite::Message::Text(
            create.frame.into(),
        ))
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
        .send(tokio_tungstenite::tungstenite::Message::Text(
            cancel.frame.into(),
        ))
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
