use super::*;

#[test]
fn decimal_strings_become_exact_integers() {
    assert_eq!(parse_e9("1"), Some(1_000_000_000));
    assert_eq!(parse_e9("0.1"), Some(100_000_000));
    assert_eq!(parse_e9("1.000000001"), Some(1_000_000_001));
    // Больше девяти знаков — лишнее отбрасывается, а не округляется вверх.
    assert_eq!(parse_e9("1.0000000019"), Some(1_000_000_001));
    assert_eq!(parse_e9("12345.6789"), Some(12_345_678_900_000));
    assert_eq!(parse_e9(""), None);
    assert_eq!(parse_e9("abc"), None);
    assert_eq!(parse_e9("1.2.3"), None);
}

/// Тот же случай, ради которого книга целочисленная, но на входе: строка
/// `0.3` обязана дать ровно то же целое, что и `0.1 + 0.2` через строку.
#[test]
fn parsing_does_not_go_through_f64() {
    let via_str = parse_e9("0.3").unwrap();
    assert_eq!(via_str, 300_000_000);
    let via_f64 = ((0.1_f64 + 0.2_f64) * 1e9).round() as i64;
    assert_eq!(via_str, via_f64, "здесь они совпадают");
    // А здесь f64 уже врёт, и именно поэтому разбор идёт по строке.
    let big = "123456789.123456789";
    assert_eq!(parse_e9(big), Some(123_456_789_123_456_789));
    let f = (big.parse::<f64>().unwrap() * 1e9).round() as i64;
    assert_ne!(parse_e9(big).unwrap(), f);
}

#[test]
fn orderbook_snapshot_parses() {
    let raw = r#"{"topic":"orderbook.50.SOLUSDT","type":"snapshot","ts":1700000000000,
      "data":{"s":"SOLUSDT","b":[["150.00","2.5"],["149.99","1.0"]],
      "a":[["150.01","3.0"]],"u":42,"seq":7},"cts":1699999999999}"#;
    let evs = parse_message(raw).unwrap();
    assert_eq!(evs.len(), 1);
    match &evs[0] {
        Event::Book(u) => {
            assert!(u.is_snapshot);
            assert_eq!(u.u, 42);
            assert_eq!(u.seq, 7);
            assert_eq!(u.cts_ms, 1699999999999);
            assert_eq!(u.bids.len(), 2);
            assert_eq!(u.bids[0], (150_000_000_000, 2_500_000_000));
            assert_eq!(u.asks[0], (150_010_000_000, 3_000_000_000));
        }
        other => panic!("ожидалось обновление книги, получено {other:?}"),
    }
}

#[test]
fn orderbook_delta_with_zero_size_parses() {
    let raw = r#"{"topic":"orderbook.50.SOLUSDT","type":"delta","ts":1,
      "data":{"s":"SOLUSDT","b":[["149.99","0"]],"a":[],"u":43,"seq":8},"cts":2}"#;
    let evs = parse_message(raw).unwrap();
    match &evs[0] {
        Event::Book(u) => {
            assert!(!u.is_snapshot);
            assert_eq!(u.u, 43);
            assert_eq!(u.seq, 8);
            assert_eq!(u.bids, vec![(149_990_000_000, 0)]);
            assert!(u.asks.is_empty());
        }
        other => panic!("ожидалось обновление книги, получено {other:?}"),
    }
}

/// Лента несёт сторону агрессора и флаг блочной сделки, и оба нужны разметке.
#[test]
fn public_trade_carries_aggressor_side_and_block_flag() {
    let raw = r#"{"topic":"publicTrade.SOLUSDT","type":"snapshot","ts":1,
      "data":[{"T":1700000000123,"s":"SOLUSDT","S":"Sell","v":"1.5","p":"150.00",
               "L":"MinusTick","i":"abc","BT":false},
              {"T":1700000000124,"s":"SOLUSDT","S":"Buy","v":"2.0","p":"150.01",
               "L":"PlusTick","i":"def","BT":true}]}"#;
    let evs = parse_message(raw).unwrap();
    assert_eq!(evs.len(), 2);
    match evs[0] {
        Event::Trade(t) => {
            assert_eq!(t.exch_ms, 1700000000123);
            assert_eq!(t.price_e9, 150_000_000_000);
            assert_eq!(t.qty_e9, 1_500_000_000);
            assert!(!t.aggressor_is_buy);
            assert!(!t.block);
        }
        _ => panic!("ожидалась сделка"),
    }
    match evs[1] {
        Event::Trade(t) => {
            assert!(t.aggressor_is_buy);
            assert!(t.block, "блочная сделка обязана быть помечена");
        }
        _ => panic!("ожидалась сделка"),
    }
}

/// `BT` не обязан присутствовать — по документации это не всегда
/// отдаваемое поле; отсутствие обязано читаться как «не блочная», не
/// как ошибка разбора.
#[test]
fn public_trade_without_bt_field_defaults_to_not_block() {
    let raw = r#"{"topic":"publicTrade.SOLUSDT","type":"snapshot","ts":1,
      "data":[{"T":1,"s":"SOLUSDT","S":"Buy","v":"1.0","p":"1.0"}]}"#;
    let evs = parse_message(raw).unwrap();
    match evs[0] {
        Event::Trade(t) => assert!(!t.block),
        _ => panic!("ожидалась сделка"),
    }
}

#[test]
fn service_messages_are_ignored_not_failed() {
    assert_eq!(
        parse_message(r#"{"success":true,"op":"subscribe"}"#).unwrap(),
        vec![Event::Other]
    );
    assert_eq!(
        parse_message(r#"{"op":"pong","args":["1"]}"#).unwrap(),
        vec![Event::Other]
    );
}

/// Топик, не совпадающий ни с одним известным префиксом, но валидный
/// JSON — обязан молча стать `Other`, а не ошибкой: неизвестный топик не
/// то же самое, что сломанное сообщение.
/// Таск 28: маршрут мультиплексированного сокета — символ из топика,
/// и его отдаёт тот же разбор, что уже прочитал сообщение. Ожидаемые
/// значения — из имён топиков протокола, не из кода под тестом.
#[test]
fn parse_returns_the_topic_symbol_for_both_book_and_trade() {
    let mut out = Vec::new();
    let book = r#"{"topic":"orderbook.50.SOLUSDT","type":"delta","ts":1,"data":{"b":[],"a":[],"u":2,"seq":2}}"#;
    assert_eq!(parse_message_into(book, &mut out).unwrap(), Some("SOLUSDT"));
    let trade = r#"{"topic":"publicTrade.PUMPFUNUSDT","type":"snapshot","ts":1,"data":[{"T":1,"s":"PUMPFUNUSDT","S":"Buy","v":"1.0","p":"1.0","i":"x","BT":false}]}"#;
    assert_eq!(
        parse_message_into(trade, &mut out).unwrap(),
        Some("PUMPFUNUSDT")
    );
    // Не компактное сообщение идёт фолбэком — символ обязан дойти и там.
    let spaced = r#"{ "topic" : "orderbook.50.XRPUSDT", "type": "delta", "ts": 1, "data": {"b":[],"a":[],"u":2,"seq":2}}"#;
    assert_eq!(
        parse_message_into(spaced, &mut out).unwrap(),
        Some("XRPUSDT")
    );
    // Служебное сообщение символа не несёт — и не обязано.
    let pong = r#"{"op":"pong","success":true}"#;
    assert_eq!(parse_message_into(pong, &mut out).unwrap(), None);
}

#[test]
fn unknown_topic_is_other_not_an_error() {
    let raw = r#"{"topic":"kline.1.SOLUSDT","data":{"whatever":1}}"#;
    assert_eq!(parse_message(raw).unwrap(), vec![Event::Other]);
}

#[test]
fn malformed_input_is_an_error_not_a_panic() {
    assert_eq!(
        parse_message("not json at all").unwrap_err(),
        ParseError::NotJson
    );
    let no_u = r#"{"topic":"orderbook.50.X","type":"delta","ts":1,"data":{"b":[],"a":[],"seq":8}}"#;
    assert_eq!(
        parse_message(no_u).unwrap_err(),
        ParseError::MissingField("u")
    );
    let no_seq =
        r#"{"topic":"orderbook.50.X","type":"delta","ts":1,"data":{"b":[],"a":[],"u":43}}"#;
    assert_eq!(
        parse_message(no_seq).unwrap_err(),
        ParseError::MissingField("seq")
    );
}

/// Ни `data.cts`, ни верхний `cts` не заданы — разбор обязан упасть на
/// `ts`, а не потерять запись молча (та же цепочка, что была раньше:
/// `data.cts` → верхний `cts` → верхний `ts`).
#[test]
fn missing_cts_falls_back_to_top_level_ts() {
    let raw = r#"{"topic":"orderbook.50.X","type":"delta","ts":777,
      "data":{"b":[],"a":[],"u":1,"seq":1}}"#;
    let evs = parse_message(raw).unwrap();
    match &evs[0] {
        Event::Book(u) => assert_eq!(u.cts_ms, 777),
        other => panic!("ожидалось обновление книги, получено {other:?}"),
    }
}

/// Порядок полей внутри объекта — не часть контракта: `data` перед
/// `topic`, `seq`/`u` в обратном порядке относительно всех остальных
/// фикстур этого файла обязаны разобраться так же, как и обычный порядок.
#[test]
fn field_order_inside_objects_does_not_matter() {
    let raw = r#"{"data":{"seq":9,"a":[],"u":44,"b":[["1.0","2.0"]]},"cts":5,"ts":1,
      "type":"delta","topic":"orderbook.50.X"}"#;
    let evs = parse_message(raw).unwrap();
    match &evs[0] {
        Event::Book(u) => {
            assert_eq!(u.u, 44);
            assert_eq!(u.seq, 9);
            assert_eq!(u.cts_ms, 5);
            assert_eq!(u.bids, vec![(1_000_000_000, 2_000_000_000)]);
        }
        other => panic!("ожидалось обновление книги, получено {other:?}"),
    }
}

/// Экранированная кавычка внутри значения поля, которое разбор не
/// читает вовсе (`i`, идентификатор сделки), не обязана ломать поиск
/// нужных полей — экранирование внутри JSON-строк остаётся заботой
/// `serde_json`, а не подстрочного пика темы.
#[test]
fn escaped_quote_in_an_unread_field_does_not_break_parsing() {
    let raw = r#"{"topic":"publicTrade.SOLUSDT","type":"snapshot","ts":1,
      "data":[{"T":1,"s":"SOLUSDT","S":"Buy","v":"1.0","p":"1.0",
               "i":"trade-\"quoted\"-id","BT":false}]}"#;
    let evs = parse_message(raw).unwrap();
    match evs[0] {
        Event::Trade(t) => assert_eq!(t.price_e9, 1_000_000_000),
        _ => panic!("ожидалась сделка"),
    }
}

/// Ревью таска 24 (а): валидный, но некомпактный JSON (пробелы после
/// двоеточий — быстрый путь не совпадает) обязан разобраться фолбэком
/// как книга/лента, а не уйти в `Other`/`NotJson`.
#[test]
fn non_compact_json_falls_back_to_typed_topic_and_parses() {
    let book = r#"{"topic": "orderbook.50.X", "type": "delta", "ts": 1,
      "data": {"b": [], "a": [["1.5", "2"]], "u": 43, "seq": 8}, "cts": 9}"#;
    match &parse_message(book).unwrap()[0] {
        Event::Book(u) => {
            assert_eq!(u.u, 43);
            assert_eq!(u.asks, vec![(1_500_000_000, 2_000_000_000)]);
        }
        other => panic!("ожидалась книга, получено {other:?}"),
    }
    let trade = r#"{ "topic" : "publicTrade.X", "type": "snapshot", "ts": 1,
      "data": [{"T": 5, "s": "X", "S": "Sell", "v": "1.0", "p": "2.0"}] }"#;
    match parse_message(trade).unwrap()[0] {
        Event::Trade(t) => {
            assert_eq!(t.exch_ms, 5);
            assert!(!t.aggressor_is_buy);
        }
        _ => panic!("ожидалась сделка"),
    }
}

/// Ревью таска 24 (а): поле перед `topic`, чьё содержимое похоже на
/// `"topic":"orderbook.` — строковое (с экранированием) и вложенный
/// ключ `topic` внутри объекта — не обязано подменить настоящий
/// верхний `topic`: сообщение — лента, и разобраться обязано лентой.
#[test]
fn lookalike_topic_before_the_real_one_does_not_fool_the_dispatch() {
    let string_field = r#"{"note":"\"topic\":\"orderbook.50.X\"","topic":"publicTrade.X",
      "type":"snapshot","ts":1,"data":[{"T":7,"s":"X","S":"Buy","v":"1.0","p":"3.0"}]}"#;
    let nested_key = r#"{"meta":{"topic":"orderbook.50.X"},"topic":"publicTrade.X",
      "type":"snapshot","ts":1,"data":[{"T":7,"s":"X","S":"Buy","v":"1.0","p":"3.0"}]}"#;
    for raw in [string_field, nested_key] {
        match parse_message(raw).unwrap()[0] {
            Event::Trade(t) => assert_eq!(t.exch_ms, 7),
            _ => panic!("ожидалась сделка: {raw}"),
        }
    }
}

/// Ревью таска 24 (б): валидный JSON неверной формы — не `NotJson`.
/// `data` книги массивом, `data` ленты объектом, `topic` числом.
#[test]
fn valid_json_of_the_wrong_shape_is_not_reported_as_not_json() {
    let cases = [
        (
            r#"{"topic":"orderbook.50.X","type":"delta","ts":1,"data":[]}"#,
            "orderbook",
        ),
        (
            r#"{"topic":"publicTrade.X","type":"snapshot","ts":1,"data":{}}"#,
            "publicTrade",
        ),
        (r#"{"a":1,"topic":5}"#, "topic"),
    ];
    for (raw, form) in cases {
        let err = parse_message(raw).unwrap_err();
        assert_ne!(err, ParseError::NotJson, "{raw}");
        assert_eq!(err, ParseError::BadShape(form), "{raw}");
    }
    assert_eq!(
        parse_message(r#"{"topic":"orderbook.50.X","data":{"u":1"#).unwrap_err(),
        ParseError::NotJson,
        "обрыв синтаксиса остаётся NotJson"
    );
}

#[test]
fn subscription_messages_are_exact() {
    assert_eq!(
        sub_orderbook(50, "SOLUSDT"),
        r#"{"op":"subscribe","args":["orderbook.50.SOLUSDT"]}"#
    );
    assert_eq!(
        sub_pool(50, &["SOLUSDT", "XRPUSDT"]),
        r#"{"op":"subscribe","args":["orderbook.50.SOLUSDT","publicTrade.SOLUSDT","orderbook.50.XRPUSDT","publicTrade.XRPUSDT"]}"#
    );
}

/// Таск 28: раскладка соединений считает длину `args` арифметикой
/// (`pool_args_chars`), а на сокет уходит то, что построил `sub_pool` —
/// разойдись они, предел 21 000 проверялся бы не по тому, что реально
/// отправлено. Ожидаемое значение берётся из **отправленного**
/// сообщения: имена топиков вырезаются из готового JSON и суммируются.
#[test]
fn pool_args_chars_matches_the_topics_sub_pool_actually_sends() {
    let symbols = ["SOLUSDT", "PUMPFUNUSDT", "1000000BABYDOGEUSDT"];
    for depth in [1u32, 50, 500] {
        let msg = sub_pool(depth, &symbols);
        let inner = msg
            .split_once('[')
            .and_then(|(_, rest)| rest.rsplit_once(']'))
            .expect("массив args");
        let sent: usize = inner.0.split(',').map(|t| t.trim_matches('"').len()).sum();
        let counted: usize = symbols.iter().map(|s| pool_args_chars(depth, s)).sum();
        assert_eq!(counted, sent, "depth={depth}");
    }
}

/// Сквозной случай: разобранные сообщения применяются к книге и дают
/// ожидаемое состояние. Это же и есть проверка, что разбор и книга говорят
/// на одном языке целых чисел.
#[test]
fn parsed_messages_drive_the_book() {
    use crate::book::{Book, Side};

    let mut b = Book::new(10_000_000, 100_000_000); // тик 0.01, шаг 0.1

    let snap = r#"{"topic":"orderbook.50.SOLUSDT","type":"snapshot","ts":1,
      "data":{"b":[["150.00","2.5"],["149.99","1.0"]],"a":[["150.01","3.0"]],"u":42,"seq":7},"cts":10}"#;
    for ev in parse_message(snap).unwrap() {
        if let Event::Book(u) = ev {
            b.apply(&u).unwrap();
        }
    }
    assert_eq!(b.depth(Side::Bid), 2);

    let del = r#"{"topic":"orderbook.50.SOLUSDT","type":"delta","ts":2,
      "data":{"b":[["149.99","0"]],"a":[],"u":43,"seq":8},"cts":11}"#;
    for ev in parse_message(del).unwrap() {
        if let Event::Book(u) = ev {
            b.apply(&u).unwrap();
        }
    }
    assert_eq!(b.depth(Side::Bid), 1);
    assert_eq!(b.last_cts_ms(), 11);
}
