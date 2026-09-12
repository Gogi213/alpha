use super::*;

/// Регрессия, внесённая правкой разбора ответа: `require_order_id` стал
/// делать `expect` на `Option`, опираясь на утверждение «на успехе Bybit
/// всегда кладёт orderId». Это утверждение про поведение чужого сервера,
/// а не локальный инвариант. До той правки кривой успешный ответ давал
/// `Decode`, обычную ошибку; после — паниковал, а зонд гоняет тысячу циклов
/// на боевом счёте, и одна паника стоила бы всего прогона вместо одной точки.
/// `"result":{}` при `retCode = 0`. `#[serde(default)]` делает из этого
/// `Some` с пустой строкой, поэтому проверка одного `Option` здесь не
/// срабатывает — тест написан до правки и падал именно на этом.
#[test]
fn success_with_empty_result_object_is_an_error_not_an_empty_order_id() {
    let envelope: RestEnvelope =
        serde_json::from_str(r#"{"retCode":0,"retMsg":"OK","result":{}}"#).unwrap();
    assert_eq!(envelope.ret_code, 0);
    match envelope.require_order_id() {
        Err(ProbeError::MissingOrderId { ret_code }) => assert_eq!(ret_code, 0),
        other => panic!("ожидалась ошибка MissingOrderId, получено {other:?}"),
    }
}

/// Тот же ответ, но `result` отсутствует целиком — Bybit так тоже умеет.
#[test]
fn success_without_result_at_all_is_an_error_not_a_panic() {
    let envelope: RestEnvelope = serde_json::from_str(r#"{"retCode":0,"retMsg":"OK"}"#).unwrap();
    assert!(matches!(
        envelope.require_order_id(),
        Err(ProbeError::MissingOrderId { .. })
    ));
}
use std::collections::VecDeque;

struct FakeRest {
    responses: VecDeque<Result<String, ProbeError>>,
    calls: Vec<SignedRequest>,
}

impl FakeRest {
    fn with_responses(responses: Vec<Result<String, ProbeError>>) -> Self {
        Self {
            responses: responses.into(),
            calls: Vec::new(),
        }
    }
}

impl PrivateRest for FakeRest {
    fn send(&mut self, req: SignedRequest) -> Result<String, ProbeError> {
        self.calls.push(req);
        self.responses
            .pop_front()
            .unwrap_or_else(|| panic!("тест не подготовил столько ответов зонду"))
    }
}

fn ack_json(order_id: &str) -> String {
    format!(r#"{{"retCode":0,"retMsg":"OK","result":{{"orderId":"{order_id}"}}}}"#)
}

fn reject_json(ret_code: i32, msg: &str) -> String {
    format!(r#"{{"retCode":{ret_code},"retMsg":"{msg}","result":{{"orderId":""}}}}"#)
}

/// Настоящая форма ответа Bybit v5 на отклонение: `result` — пустой
/// объект, ключа `orderId` в нём нет вообще (не пустая строка в нём, а
/// отсутствующий ключ). `reject_json` выше — фикстура, написанная рукой,
/// а не список наблюдённых с биржи ответов, и она этот случай не ловит.
fn reject_json_empty_result(ret_code: i32, msg: &str) -> String {
    format!(r#"{{"retCode":{ret_code},"retMsg":"{msg}","result":{{}}}}"#)
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
        recv_window_ms: DEFAULT_RECV_WINDOW_MS,
    }
}

const MID_E9: i64 = 100_000_000_000; // 100.0

/// Decision 12: в файлы и отчёты попадают только имена ключей, никогда
/// значения. `api_key` — не криптографический секрет, но величина,
/// идентифицирующая счёт, а `{:?}` этой структуры мог всплыть в логе
/// или панике так же легко, как и `Credentials` (`sign.rs`), — до
/// фикса оба типа здесь просто использовали `derive(Debug)`.
#[test]
fn request_headers_and_signed_request_debug_output_contain_neither_key_nor_secret() {
    let secret = "s3cr3t-do-not-leak-in-probe-9f8a7";
    let api_key = "visible-key-in-probe";
    let creds = Credentials::for_test(api_key, secret);
    let req = sign_request(
        &creds,
        CREATE_ORDER_PATH,
        DEFAULT_RECV_WINDOW_MS,
        "{}".to_string(),
    )
    .unwrap();

    let printed_headers = format!("{:?}", req.headers);
    let printed_request = format!("{req:?}");

    for printed in [&printed_headers, &printed_request] {
        assert!(!printed.contains(api_key), "утёк api_key: {printed}");
        assert!(!printed.contains(secret), "утёк секрет: {printed}");
        assert!(
            printed.contains("<redacted>"),
            "нет метки редакции: {printed}"
        );
    }
}

#[test]
fn far_price_moves_buy_orders_below_mid_by_the_requested_ticks() {
    let price = far_price_e9(MID_E9, 10_000_000, 500, OrderSide::Buy);
    assert_eq!(price, MID_E9 - 500 * 10_000_000);
}

#[test]
fn far_price_moves_sell_orders_above_mid_by_the_requested_ticks() {
    let price = far_price_e9(MID_E9, 10_000_000, 500, OrderSide::Sell);
    assert_eq!(price, MID_E9 + 500 * 10_000_000);
}

#[test]
#[should_panic(expected = "прочь от книги")]
fn far_price_panics_on_a_zero_offset() {
    far_price_e9(MID_E9, 10_000_000, 0, OrderSide::Buy);
}

/// Гвард — `ticks_from_mid > 0`, а не `>= 0`: сторона «ноль» выше уже
/// проверена отдельным тестом, но `> 0` не то же самое, что `>= 0`
/// в обратную сторону — регрессия, сузившая гвард до `>= 0`, ловит
/// ноль как и раньше, но пропускает отрицательное смещение, ничем
/// не отличаясь от него на этом одном тесте. Нужны оба теста разом.
#[test]
#[should_panic(expected = "прочь от книги")]
fn far_price_panics_on_a_negative_offset() {
    far_price_e9(MID_E9, 10_000_000, -500, OrderSide::Buy);
}

#[test]
fn format_e9_trims_trailing_zeros_and_keeps_significant_digits() {
    assert_eq!(format_e9(1_000_000_000), "1");
    assert_eq!(format_e9(1_500_000_000), "1.5");
    assert_eq!(format_e9(1_000_000_001), "1.000000001");
}

/// Инверсия разбора `ws.rs`: то, что мы форматируем для отправки на
/// биржу, обязано разбираться обратно в ту же величину его же
/// парсером — иначе два файла одного проекта расходятся в понимании
/// формата, которое ничем не ловится, кроме такого теста.
#[test]
fn format_e9_round_trips_through_ws_parse_e9() {
    use crate::bybit::ws::parse_e9;
    for &s in &["150.01", "0.001", "12345.6789", "1", "0.000000001"] {
        let v = parse_e9(s).unwrap();
        let formatted = format_e9(v);
        assert_eq!(
            parse_e9(&formatted).unwrap(),
            v,
            "цена туда и обратно обязана остаться той же величиной"
        );
    }
}

#[test]
fn percentile_matches_hand_computed_values_on_a_known_sample() {
    let samples: Vec<i64> = (1..=10).map(|i| i * 10).collect(); // 10..=100
    assert_eq!(percentile_ns(&samples, 1), 10);
    assert_eq!(percentile_ns(&samples, 50), 50);
    assert_eq!(percentile_ns(&samples, 95), 100);
    assert_eq!(percentile_ns(&samples, 100), 100);
}

/// n = 5: ближайший ранг даёт ceil(0.95 × 5) = 5 → максимум выборки, 5.
/// Линейная интерполяция (умолчание NumPy/Excel) дала бы 4.8 — число,
/// которого в выборке не было. Тест фиксирует именно это расхождение.
#[test]
fn p95_of_a_small_sample_uses_nearest_rank_not_linear_interpolation() {
    let samples = [1_i64, 2, 3, 4, 5];
    assert_eq!(percentile_ns(&samples, 95), 5);
}

/// n = 4: ceil(0.5 × 4) = 2 → второй по возрастанию элемент, 2, а не
/// среднее (2+3)/2 = 2.5 — конвенция описана в доке `percentile_ns`.
#[test]
fn median_convention_on_even_length_picks_the_lower_of_the_middle_pair() {
    let samples = [1_i64, 2, 3, 4];
    assert_eq!(percentile_ns(&samples, 50), 2);
}

#[test]
fn percentile_does_not_depend_on_input_order() {
    let sorted = [1_i64, 2, 3, 4, 5, 6, 7, 8, 9, 10];
    let mut shuffled = sorted;
    shuffled.reverse();
    for p in [1_u8, 50, 95, 100] {
        assert_eq!(percentile_ns(&sorted, p), percentile_ns(&shuffled, p));
    }
}

/// `percentile_of_sorted` — тело `percentile_ns` без сортировки,
/// вынесенное ради `summarize` (один `sort_unstable` на медиану и p95
/// разом, см. её doc). Она обязана доверять вызывающему и не
/// пересортировывать: на нарочно неотсортированной выборке «ближайший
/// ранг» без сортировки указывает просто на элемент по индексу, а не на
/// статистический перцентиль — если бы функция сортировала сама, это
/// совпало бы с `percentile_ns` на тех же данных, и вся экономия одного
/// сорта тихо исчезла бы обратно в два.
#[test]
fn percentile_of_sorted_trusts_the_caller_and_does_not_sort_again() {
    let unsorted = [50_i64, 10, 30, 20, 40];
    assert_eq!(
        percentile_of_sorted(&unsorted, 20),
        unsorted[0],
        "ранг считается по позиции в переданном срезе как есть"
    );
    assert_ne!(
        percentile_of_sorted(&unsorted, 20),
        percentile_ns(&unsorted, 20),
        "совпадение здесь значило бы, что функция снова сортирует сама"
    );
}

/// `summarize` строит `rtts` из `Cycle` в порядке их появления в
/// `cycles`, который не обязан совпадать с порядком по величине RTT.
/// Единственный тест на `summarize` выше строит цикл так, что RTT и
/// так растут по порядку, — регрессия «забыли отсортировать перед
/// `percentile_of_sorted`» прошла бы его незамеченной. Здесь порядок
/// внесения нарочно не совпадает с порядком величин.
#[test]
fn summarize_is_correct_when_cycles_are_not_in_rtt_order() {
    let base = std::time::Instant::now();
    let rtts_ms = [5_i64, 1, 4, 2, 3]; // порядок внесения ≠ порядок величин
    let cycles: Vec<Cycle> = rtts_ms
        .iter()
        .map(|&ms| Cycle {
            tick_observed: base,
            order_sent: base,
            ack_received: base + std::time::Duration::from_millis(ms as u64),
        })
        .collect();

    let summary = summarize(&cycles);

    assert_eq!(summary.n, 5);
    assert_eq!(summary.median_ns, 3_000_000);
    assert_eq!(summary.p95_ns, 5_000_000);
}

#[test]
#[should_panic(expected = "пустой выборки")]
fn percentile_panics_on_empty_sample() {
    percentile_ns(&[], 50);
}

#[test]
#[should_panic(expected = "1..=100")]
fn percentile_panics_below_the_valid_percent_range() {
    percentile_ns(&[1, 2, 3], 0);
}

/// Гвард — `(1..=100).contains(&p)`: сторона «ниже диапазона» уже
/// проверена отдельным тестом, но регрессия, сузившая проверку до
/// одностороннего `p >= 1` (потеряв верхнюю границу — например, если
/// `contains` заменили на явное сравнение и забыли половину), ловится
/// только этим тестом, не тем.
#[test]
#[should_panic(expected = "1..=100")]
fn percentile_panics_above_the_valid_percent_range() {
    percentile_ns(&[1, 2, 3], 101);
}

#[test]
fn summarize_computes_median_and_p95_of_rtt_not_of_raw_timestamps() {
    // `Instant` не конструируется из произвольного числа (в этом и
    // смысл фикса — нет обходного пути обратно к «удобным» голым ns),
    // поэтому образцы строятся смещением на `Duration` от одной базы.
    let base = std::time::Instant::now();
    let cycles: Vec<Cycle> = (1..=10i64)
        .map(|i| Cycle {
            tick_observed: base,
            order_sent: base,
            ack_received: base + std::time::Duration::from_millis(i as u64), // rtt = i мс
        })
        .collect();
    let summary = summarize(&cycles);
    assert_eq!(summary.n, 10);
    assert_eq!(summary.median_ns, 5_000_000);
    assert_eq!(summary.p95_ns, 10_000_000);
}

/// До фикса `rtt_ns` было `ack_received_ns - order_sent_ns` на `i64` из
/// `SystemTime` — обычное вычитание, которое молча уходит в отрицательное,
/// если второе показание часов оказалось раньше первого (ровно то, что
/// делает скачок NTP между `order_sent` и `ack_received`, H12). Здесь это
/// воспроизведено без реального шага часов: `ack_received` конструируется
/// раньше `order_sent` напрямую. Старое вычитание дало бы отрицательный
/// `i64` и попало бы в перцентиль как есть; `Instant::duration_since`
/// определён как насыщающийся нулём, а не паникующий и не уходящий в
/// отрицательное, поэтому такой образец не может испортить медиану и p95,
/// которые читает G4.
#[test]
fn rtt_ns_saturates_to_zero_instead_of_going_negative_when_ack_precedes_sent() {
    let base = std::time::Instant::now();
    let earlier = base
        .checked_sub(std::time::Duration::from_millis(5))
        .expect("часы процесса должны были идти уже больше 5мс к моменту теста");
    let cycle = Cycle {
        tick_observed: earlier,
        order_sent: base,
        ack_received: earlier,
    };
    assert_eq!(
        cycle.rtt_ns(),
        0,
        "RTT обязан насыщаться нулём, а не становиться отрицательным числом"
    );
}

#[test]
fn run_cycle_places_then_immediately_cancels_and_returns_ordered_timestamps() {
    let mut fake = FakeRest::with_responses(vec![Ok(ack_json("order-1")), Ok(ack_json("order-1"))]);
    let creds = test_creds();
    let params = test_params();

    let cycle = run_cycle(&mut fake, &creds, &params, MID_E9).unwrap();

    assert_eq!(fake.calls.len(), 2);
    assert_eq!(fake.calls[0].path, CREATE_ORDER_PATH);
    assert_eq!(fake.calls[1].path, CANCEL_ORDER_PATH);
    assert!(fake.calls[1].body.contains("order-1"));
    assert!(cycle.tick_observed <= cycle.order_sent);
    assert!(cycle.order_sent <= cycle.ack_received);
}

#[test]
fn run_cycle_fails_and_skips_cancel_when_order_is_rejected() {
    let mut fake = FakeRest::with_responses(vec![Ok(reject_json(10001, "post only would take"))]);
    let creds = test_creds();
    let params = test_params();

    let err = run_cycle(&mut fake, &creds, &params, MID_E9).unwrap_err();

    assert_eq!(
        fake.calls.len(),
        1,
        "без order_id снимать нечего — отмена не вызывается"
    );
    assert_eq!(
        err,
        ProbeError::OrderRejected {
            ret_code: 10001,
            ret_msg: "post only would take".to_string()
        }
    );
}

/// Без этого теста `OrderResult { order_id: String }` обязательным полем
/// проходил бы разбор фикстуры `reject_json` (там `orderId` пустой
/// строкой, но ключ на месте) и падал бы только на настоящей бирже,
/// где отклонённый ордер приходит с `"result":{}` — ключа нет вовсе.
/// Это и есть дефект: путь `OrderRejected` не проверялся на форме
/// ответа, которую реально шлёт Bybit.
#[test]
fn run_cycle_fails_and_skips_cancel_when_order_is_rejected_with_empty_result_object() {
    let mut fake = FakeRest::with_responses(vec![Ok(reject_json_empty_result(
        10001,
        "post only would take",
    ))]);
    let creds = test_creds();
    let params = test_params();

    let err = run_cycle(&mut fake, &creds, &params, MID_E9).unwrap_err();

    assert_eq!(
        fake.calls.len(),
        1,
        "без order_id снимать нечего — отмена не вызывается"
    );
    assert_eq!(
        err,
        ProbeError::OrderRejected {
            ret_code: 10001,
            ret_msg: "post only would take".to_string()
        }
    );
}

/// Тот же дефект на пути отмены: ордер встал (`result.orderId` есть),
/// но отмена отклонена, и настоящий ответ биржи на отклонение отмены —
/// тоже `"result":{}`, без `orderId`.
#[test]
fn run_cycle_fails_when_cancel_is_rejected_with_empty_result_object() {
    let mut fake = FakeRest::with_responses(vec![
        Ok(ack_json("order-3")),
        Ok(reject_json_empty_result(10002, "order not found")),
    ]);
    let creds = test_creds();
    let params = test_params();

    let err = run_cycle(&mut fake, &creds, &params, MID_E9).unwrap_err();

    assert_eq!(
        err,
        ProbeError::CancelFailed {
            order_id: "order-3".to_string(),
            ret_code: 10002,
            ret_msg: "order not found".to_string(),
        }
    );
}

#[test]
fn run_cycle_fails_when_cancel_is_rejected_even_though_order_was_placed() {
    let mut fake = FakeRest::with_responses(vec![
        Ok(ack_json("order-2")),
        Ok(reject_json(10002, "order not found")),
    ]);
    let creds = test_creds();
    let params = test_params();

    let err = run_cycle(&mut fake, &creds, &params, MID_E9).unwrap_err();

    assert_eq!(
        err,
        ProbeError::CancelFailed {
            order_id: "order-2".to_string(),
            ret_code: 10002,
            ret_msg: "order not found".to_string(),
        }
    );
}

#[test]
fn run_cycles_collects_exactly_the_requested_number_of_cycles() {
    let n = 5;
    let responses = (0..n)
        .flat_map(|i| {
            let order_id = format!("o{i}");
            vec![Ok(ack_json(&order_id)), Ok(ack_json(&order_id))]
        })
        .collect();
    let mut fake = FakeRest::with_responses(responses);
    let creds = test_creds();
    let params = test_params();

    let cycles = run_cycles(&mut fake, &creds, &params, || MID_E9, n).unwrap();

    assert_eq!(cycles.len(), n);
    assert_eq!(fake.calls.len(), 2 * n);
}

#[test]
fn probe_refuses_to_run_with_fewer_than_the_minimum_cycles() {
    let mut fake = FakeRest::with_responses(vec![]);
    let params = test_params();

    let err = probe(&mut fake, &params, || MID_E9, MIN_CYCLES - 1).unwrap_err();

    assert_eq!(
        err,
        ProbeError::TooFewCycles {
            requested: MIN_CYCLES - 1,
            minimum: MIN_CYCLES,
        }
    );
    assert!(
        fake.calls.is_empty(),
        "меньше минимума — сеть не трогается вообще"
    );
}

#[test]
fn probe_refuses_to_run_without_api_key() {
    crate::bybit::sign::with_cleared_env(|| {
        std::env::set_var(crate::bybit::sign::API_SECRET_VAR, "secret");
        let mut fake = FakeRest::with_responses(vec![]);
        let params = test_params();

        let err = probe(&mut fake, &params, || MID_E9, MIN_CYCLES).unwrap_err();

        assert_eq!(
            err,
            ProbeError::Credentials(crate::bybit::sign::CredentialsError::Missing(
                crate::bybit::sign::API_KEY_VAR
            ))
        );
        assert!(fake.calls.is_empty(), "без ключей ни один запрос не уходит");
    });
}

#[test]
fn probe_refuses_to_run_without_api_secret() {
    crate::bybit::sign::with_cleared_env(|| {
        std::env::set_var(crate::bybit::sign::API_KEY_VAR, "key");
        let mut fake = FakeRest::with_responses(vec![]);
        let params = test_params();

        let err = probe(&mut fake, &params, || MID_E9, MIN_CYCLES).unwrap_err();

        assert_eq!(
            err,
            ProbeError::Credentials(crate::bybit::sign::CredentialsError::Missing(
                crate::bybit::sign::API_SECRET_VAR
            ))
        );
        assert!(fake.calls.is_empty(), "без ключей ни один запрос не уходит");
    });
}
