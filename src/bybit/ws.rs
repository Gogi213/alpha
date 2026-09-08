//! Разбор публичных WS-сообщений Bybit v5 и склейка их с книгой.
//!
//! Здесь только разбор и правила потока. Сокет, переподключение и запись живут
//! выше, в `commands/`: так эта часть тестируется без сети и без времени.
//!
//! Поля подтверждены по документации v5:
//! - `orderbook.<depth>.<symbol>` несёт `ts`, `cts`, `u`, `seq`, `type` и массивы
//!   `b`/`a` из пар `[цена, размер]`. Размер `0` удаляет уровень, `u = 1` означает
//!   рестарт сервиса и требует перезаписи книги.
//! - `publicTrade.<symbol>` несёт `T` (время исполнения), `S` (сторона агрессора),
//!   `v`, `p`, `i`, `BT` (блочная сделка) и `seq`. **Поля `cts` у него нет** —
//!   ключ склейки с книгой это `T` против `orderbook.cts`, оба времени матчинга.

use crate::book::Update;

/// Разобранное событие публичного потока.
#[derive(Debug, Clone, PartialEq)]
pub enum Event {
    /// Обновление стакана. `Update` уже в целых 1e-9.
    Book(Update),
    /// Одна сделка ленты.
    Trade(Trade),
    /// Ответ на подписку, pong и прочее, что нас не касается.
    Other,
}

/// Сделка. Сторона — это сторона **агрессора**, и именно она решает, съел ли
/// поток уровень: бид-уровень потребляют продавцы.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Trade {
    /// Время исполнения на матчинге, миллисекунды. Сравнимо с `orderbook.cts`.
    pub exch_ms: i64,
    pub price_e9: i64,
    pub qty_e9: i64,
    pub aggressor_is_buy: bool,
    /// Блочная сделка. Такие не потребляют видимую ликвидность стакана, и
    /// засчитанные в объём они превращают снятие уровня в исполнение.
    pub block: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParseError {
    NotJson,
    MissingField(&'static str),
    BadNumber(&'static str),
}

/// Разбирает десятичную строку в целое 1e-9 без промежуточного `f64`.
///
/// Через `f64` этого делать нельзя: цена вроде `0.00000001` и большие количества
/// не переживают округление, а вся книга стоит на точном сравнении тиков.
pub fn parse_e9(s: &str) -> Option<i64> {
    let s = s.trim();
    if s.is_empty() {
        return None;
    }
    let (neg, body) = match s.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, s.strip_prefix('+').unwrap_or(s)),
    };
    let (int_part, frac_part) = match body.split_once('.') {
        Some((i, f)) => (i, f),
        None => (body, ""),
    };
    if int_part.is_empty() && frac_part.is_empty() {
        return None;
    }
    if !int_part.bytes().all(|b| b.is_ascii_digit())
        || !frac_part.bytes().all(|b| b.is_ascii_digit())
    {
        return None;
    }
    let mut v: i64 = 0;
    for b in int_part.bytes() {
        v = v.checked_mul(10)?.checked_add((b - b'0') as i64)?;
    }
    // Ровно девять знаков после точки: лишние отбрасываются, недостающие дополняются.
    let mut scale = 9;
    for b in frac_part.bytes() {
        if scale == 0 {
            break;
        }
        v = v.checked_mul(10)?.checked_add((b - b'0') as i64)?;
        scale -= 1;
    }
    for _ in 0..scale {
        v = v.checked_mul(10)?;
    }
    Some(if neg { -v } else { v })
}

fn levels(v: &serde_json::Value, key: &'static str) -> Result<Vec<(i64, i64)>, ParseError> {
    let arr = match v.get(key) {
        Some(serde_json::Value::Array(a)) => a,
        None => return Ok(Vec::new()),
        Some(_) => return Err(ParseError::MissingField(key)),
    };
    let mut out = Vec::with_capacity(arr.len());
    for pair in arr {
        let p = pair
            .get(0)
            .and_then(|x| x.as_str())
            .ok_or(ParseError::MissingField("level price"))?;
        let q = pair
            .get(1)
            .and_then(|x| x.as_str())
            .ok_or(ParseError::MissingField("level size"))?;
        let price = parse_e9(p).ok_or(ParseError::BadNumber("level price"))?;
        let qty = parse_e9(q).ok_or(ParseError::BadNumber("level size"))?;
        out.push((price, qty));
    }
    Ok(out)
}

/// Разбирает одно текстовое сообщение публичного потока.
pub fn parse_message(raw: &str) -> Result<Vec<Event>, ParseError> {
    let v: serde_json::Value = serde_json::from_str(raw).map_err(|_| ParseError::NotJson)?;

    let topic = match v.get("topic").and_then(|t| t.as_str()) {
        Some(t) => t,
        None => return Ok(vec![Event::Other]),
    };

    if topic.starts_with("orderbook.") {
        let data = v.get("data").ok_or(ParseError::MissingField("data"))?;
        let u = data
            .get("u")
            .and_then(|x| x.as_u64())
            .ok_or(ParseError::MissingField("u"))?;
        // `seq` — сквозной счётчик WS и REST (в отличие от `u`, у которого
        // в двух каналах два разных счётчика). В контроле потока не участвует.
        let seq = data
            .get("seq")
            .and_then(|x| x.as_u64())
            .ok_or(ParseError::MissingField("seq"))?;
        // `cts` — время матчинга. У некоторых сообщений его нет; тогда берём `ts`,
        // время формирования, и это ухудшение точности, а не эквивалент.
        let cts_ms = data
            .get("cts")
            .and_then(|x| x.as_i64())
            .or_else(|| v.get("cts").and_then(|x| x.as_i64()))
            .or_else(|| v.get("ts").and_then(|x| x.as_i64()))
            .ok_or(ParseError::MissingField("cts"))?;
        let is_snapshot = v.get("type").and_then(|x| x.as_str()) == Some("snapshot");
        return Ok(vec![Event::Book(Update {
            is_snapshot,
            u,
            seq,
            cts_ms,
            bids: levels(data, "b")?,
            asks: levels(data, "a")?,
        })]);
    }

    if topic.starts_with("publicTrade") {
        let arr = match v.get("data") {
            Some(serde_json::Value::Array(a)) => a,
            _ => return Err(ParseError::MissingField("data")),
        };
        let mut out = Vec::with_capacity(arr.len());
        for t in arr {
            let exch_ms = t
                .get("T")
                .and_then(|x| x.as_i64())
                .ok_or(ParseError::MissingField("T"))?;
            let price_e9 = t
                .get("p")
                .and_then(|x| x.as_str())
                .and_then(parse_e9)
                .ok_or(ParseError::BadNumber("p"))?;
            let qty_e9 = t
                .get("v")
                .and_then(|x| x.as_str())
                .and_then(parse_e9)
                .ok_or(ParseError::BadNumber("v"))?;
            let side = t
                .get("S")
                .and_then(|x| x.as_str())
                .ok_or(ParseError::MissingField("S"))?;
            out.push(Event::Trade(Trade {
                exch_ms,
                price_e9,
                qty_e9,
                aggressor_is_buy: side.eq_ignore_ascii_case("buy"),
                block: t.get("BT").and_then(|x| x.as_bool()).unwrap_or(false),
            }));
        }
        return Ok(out);
    }

    Ok(vec![Event::Other])
}

/// Сообщения подписки. Отдельными функциями, чтобы они были в тестах, а не
/// в строке посреди сетевого кода.
pub fn sub_orderbook(depth: u32, symbol: &str) -> String {
    format!(
        r#"{{"op":"subscribe","args":["orderbook.{depth}.{symbol}"]}}"#,
        depth = depth,
        symbol = symbol
    )
}

pub fn sub_trades(symbol: &str) -> String {
    format!(
        r#"{{"op":"subscribe","args":["publicTrade.{symbol}"]}}"#,
        symbol = symbol
    )
}

#[cfg(test)]
mod tests {
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

    #[test]
    fn malformed_input_is_an_error_not_a_panic() {
        assert_eq!(parse_message("not json").unwrap_err(), ParseError::NotJson);
        let no_u =
            r#"{"topic":"orderbook.50.X","type":"delta","ts":1,"data":{"b":[],"a":[],"seq":8}}"#;
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

    #[test]
    fn subscription_messages_are_exact() {
        assert_eq!(
            sub_orderbook(50, "SOLUSDT"),
            r#"{"op":"subscribe","args":["orderbook.50.SOLUSDT"]}"#
        );
        assert_eq!(
            sub_trades("SOLUSDT"),
            r#"{"op":"subscribe","args":["publicTrade.SOLUSDT"]}"#
        );
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
}
