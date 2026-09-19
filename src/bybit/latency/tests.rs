//! Ядро `lob latency` на фейковых транспортах: без сети и без ключей.

use super::*;
use std::cell::RefCell;
use std::collections::VecDeque;
use std::rc::Rc;

// --- размер -----------------------------------------------------------------

const F: Filters = Filters {
    tick_e9: 10_000,            // 0.00001
    qty_step_e9: 1_000_000_000, // 1
    min_qty_e9: 1_000_000_000,
    min_notional_e9: 5_000_000_000, // 5 USDT
};

#[test]
fn qty_covers_notional_rounded_up_to_step() {
    // DOGE по 0.2: $10 → 50 монет ровно.
    let qty = qty_for_notional(10_000_000_000, 200_000_000, F).unwrap();
    assert_eq!(qty, 50_000_000_000);
    // по 0.21: 47.6 → 48.
    let qty = qty_for_notional(10_000_000_000, 210_000_000, F).unwrap();
    assert_eq!(qty, 48_000_000_000);
    assert!(notional_e9(qty, 210_000_000) >= 10_000_000_000);
}

#[test]
fn qty_respects_min_qty_and_min_notional() {
    // Дорогая монета: $10 / $40 000 → 0.00025, но minOrderQty 0.001.
    let f = Filters {
        tick_e9: 100_000_000,
        qty_step_e9: 1_000_000,
        min_qty_e9: 1_000_000,
        min_notional_e9: 5_000_000_000,
    };
    assert_eq!(
        qty_for_notional(10_000_000_000, 40_000_000_000_000, f).unwrap(),
        1_000_000
    );
    // Номинал ниже minNotional: $1 при minNotional $5 → qty на $5.
    assert_eq!(
        qty_for_notional(1_000_000_000, 200_000_000, F).unwrap(),
        25_000_000_000
    );
}

#[test]
fn qty_rejects_bad_price() {
    assert!(matches!(
        qty_for_notional(10, 0, F),
        Err(LatencyError::Size(_))
    ));
}

#[test]
fn align_to_tick_moves_away_from_book() {
    assert_eq!(align_to_tick(123_456, 1_000, OrderSide::Buy), 123_000);
    assert_eq!(align_to_tick(123_456, 1_000, OrderSide::Sell), 124_000);
    assert_eq!(align_to_tick(123_000, 1_000, OrderSide::Sell), 123_000);
}

#[test]
fn format_e9_matches_probe_copy() {
    assert_eq!(format_e9(50_000_000_000), "50");
    assert_eq!(format_e9(123_450_000), "0.12345");
    assert_eq!(format_e9(0), "0");
}

// --- тела и разбор ----------------------------------------------------------

#[test]
fn bodies_serialize_per_bybit_v5() {
    let b = post_only_body(
        "DOGEUSDT",
        OrderSide::Buy,
        50_000_000_000,
        199_000_000,
        "lat0r",
    );
    let j = serde_json::to_value(&b).unwrap();
    assert_eq!(j["orderType"], "Limit");
    assert_eq!(j["timeInForce"], "PostOnly");
    assert_eq!(j["price"], "0.199");
    assert_eq!(j["qty"], "50");
    assert_eq!(j["orderLinkId"], "lat0r");
    assert!(j.get("reduceOnly").is_none(), "false не сериализуется");
    let m = market_body("DOGEUSDT", OrderSide::Sell, 50_000_000_000, "lat0trx", true);
    let j = serde_json::to_value(&m).unwrap();
    assert_eq!(j["orderType"], "Market");
    assert_eq!(j["timeInForce"], "IOC");
    assert!(j.get("price").is_none());
    assert_eq!(j["reduceOnly"], true);
}

#[test]
fn ws_frame_has_req_id_header_and_args() {
    let b = market_body("DOGEUSDT", OrderSide::Buy, 1_000_000_000, "l", false);
    let f = ws_frame("t0", OP_CREATE, &b, 1_700_000_000_000, 5000).unwrap();
    let v: serde_json::Value = serde_json::from_str(&f).unwrap();
    assert_eq!(v["reqId"], "t0");
    assert_eq!(v["op"], "order.create");
    assert_eq!(v["header"]["X-BAPI-TIMESTAMP"], "1700000000000");
    assert_eq!(v["header"]["X-BAPI-RECV-WINDOW"], "5000");
    assert_eq!(v["args"][0]["symbol"], "DOGEUSDT");
    assert_eq!(ws_frame_req_id(&f).as_deref(), Some("t0"));
}

#[test]
fn order_ack_rest_and_ws_shapes() {
    assert_eq!(
        parse_order_ack(r#"{"retCode":0,"retMsg":"OK","result":{"orderId":"abc"}}"#).unwrap(),
        "abc"
    );
    assert_eq!(
        parse_order_ack(
            r#"{"reqId":"t0","retCode":0,"retMsg":"OK","op":"order.create","data":{"orderId":"xyz"}}"#
        )
        .unwrap(),
        "xyz"
    );
    assert!(matches!(
        parse_order_ack(r#"{"retCode":10001,"retMsg":"params error"}"#),
        Err(LatencyError::Rejected {
            ret_code: 10001,
            ..
        })
    ));
    assert!(matches!(
        parse_order_ack(r#"{"retCode":0,"result":{}}"#),
        Err(LatencyError::Decode(_))
    ));
}

#[test]
fn private_frame_parse_and_predicates() {
    let f = parse_private_frame(
        r#"{"topic":"order","data":[{"orderId":"1","orderLinkId":"lat0r","orderStatus":"New"}]}"#,
    );
    assert!(f.has_order("lat0r", "New"));
    assert!(!f.has_order("lat0r", "Cancelled"));
    assert!(!f.has_trade_exec("lat0r"));
    let e = parse_private_frame(
        r#"{"topic":"execution","data":[{"orderId":"1","orderLinkId":"lat0tr","execType":"Trade"}]}"#,
    );
    assert!(e.has_trade_exec("lat0tr"));
    assert!(parse_private_frame(r#"{"op":"pong","args":["1"]}"#).is_pong());
    assert!(parse_private_frame(r#"{"ret_msg":"pong","op":"ping"}"#).is_pong());
    assert!(!parse_private_frame("garbage").is_pong());
}

#[test]
fn position_parse() {
    let body =
        r#"{"retCode":0,"result":{"list":[{"symbol":"DOGEUSDT","side":"Buy","size":"50"}]}}"#;
    assert_eq!(
        parse_position(body, "DOGEUSDT").unwrap(),
        Some((OrderSide::Buy, 50_000_000_000))
    );
    let flat = r#"{"retCode":0,"result":{"list":[{"symbol":"DOGEUSDT","side":"","size":"0"}]}}"#;
    assert_eq!(parse_position(flat, "DOGEUSDT").unwrap(), None);
}

#[test]
fn endpoints_by_name() {
    assert_eq!(endpoints_for("mainnet"), Some(MAINNET));
    assert_eq!(
        endpoints_for("testnet").unwrap().rest,
        "https://api-testnet.bybit.com"
    );
    assert_eq!(
        endpoints_for("demo").unwrap().rest,
        "https://api-demo.bybit.com"
    );
    assert_eq!(endpoints_for("prod"), None);
}

// --- фейки транспортов ------------------------------------------------------

type Shared = Rc<RefCell<VecDeque<String>>>;

/// REST-фейк: любой ордер принят, `orderId` = `orderLinkId`; отправленные
/// тела запоминаются; каждый принятый ордер рождает кадры приватного стрима
/// в общей очереди (`feed`), как сделала бы биржа.
struct FakeRest {
    posts: Vec<(String, String)>,
    feed: Shared,
    position: Option<(OrderSide, i64)>,
}

fn order_frames(link: &str, market: bool) -> Vec<String> {
    if market {
        vec![
            format!(
                r#"{{"topic":"order","data":[{{"orderId":"{link}","orderLinkId":"{link}","orderStatus":"Filled"}}]}}"#
            ),
            format!(
                r#"{{"topic":"execution","data":[{{"orderId":"{link}","orderLinkId":"{link}","execType":"Trade"}}]}}"#
            ),
        ]
    } else {
        vec![format!(
            r#"{{"topic":"order","data":[{{"orderId":"{link}","orderLinkId":"{link}","orderStatus":"New"}}]}}"#
        )]
    }
}

fn cancelled_frame(id: &str) -> String {
    format!(
        r#"{{"topic":"order","data":[{{"orderId":"{id}","orderLinkId":"{id}","orderStatus":"Cancelled"}}]}}"#
    )
}

const BOOK: &str = r#"{"retCode":0,"result":{"s":"DOGEUSDT","u":1,"seq":1,"ts":1,"b":[["0.19999","100"]],"a":[["0.20001","100"]]}}"#;

impl Rest for FakeRest {
    fn get_public(&mut self, path: &str, _query: &str) -> Result<String, LatencyError> {
        Ok(match path {
            TIME_PATH => r#"{"retCode":0,"result":{"timeSecond":"1"}}"#.to_string(),
            ORDERBOOK_PATH => BOOK.to_string(),
            other => return Err(LatencyError::Decode(other.to_string())),
        })
    }
    fn get_signed(&mut self, path: &str, _query: &str) -> Result<String, LatencyError> {
        assert_eq!(path, POSITION_LIST_PATH);
        Ok(match self.position {
            None => r#"{"retCode":0,"result":{"list":[]}}"#.to_string(),
            Some((side, size)) => format!(
                r#"{{"retCode":0,"result":{{"list":[{{"symbol":"DOGEUSDT","side":"{}","size":"{}"}}]}}}}"#,
                match side {
                    OrderSide::Buy => "Buy",
                    OrderSide::Sell => "Sell",
                },
                format_e9(size)
            ),
        })
    }
    fn post_signed(&mut self, path: &str, body: &str) -> Result<String, LatencyError> {
        self.posts.push((path.to_string(), body.to_string()));
        let v: serde_json::Value = serde_json::from_str(body).unwrap();
        match path {
            CREATE_PATH => {
                let link = v["orderLinkId"].as_str().unwrap().to_string();
                let market = v["orderType"] == "Market";
                for f in order_frames(&link, market) {
                    self.feed.borrow_mut().push_back(f);
                }
                Ok(format!(
                    r#"{{"retCode":0,"result":{{"orderId":"{link}"}}}}"#
                ))
            }
            CANCEL_PATH => {
                let id = v["orderId"].as_str().unwrap();
                self.feed.borrow_mut().push_back(cancelled_frame(id));
                Ok(r#"{"retCode":0}"#.to_string())
            }
            CANCEL_ALL_PATH => Ok(r#"{"retCode":0}"#.to_string()),
            other => Err(LatencyError::Decode(other.to_string())),
        }
    }
}

struct FakeTrade {
    feed: Shared,
    replies: VecDeque<String>,
    sent: Vec<String>,
}

impl TradeWs for FakeTrade {
    fn send(&mut self, frame: &str) -> Result<(), LatencyError> {
        self.sent.push(frame.to_string());
        let v: serde_json::Value = serde_json::from_str(frame).unwrap();
        let req = v["reqId"].as_str().unwrap();
        let args = &v["args"][0];
        if v["op"] == OP_CREATE {
            let link = args["orderLinkId"].as_str().unwrap().to_string();
            let market = args["orderType"] == "Market";
            for f in order_frames(&link, market) {
                self.feed.borrow_mut().push_back(f);
            }
            // Чужой кадр перед своим — ядро обязано его пропустить.
            self.replies.push_back(r#"{"op":"pong"}"#.to_string());
            self.replies.push_back(format!(
                r#"{{"reqId":"{req}","retCode":0,"retMsg":"OK","op":"order.create","data":{{"orderId":"{link}"}}}}"#
            ));
        } else {
            let id = args["orderId"].as_str().unwrap();
            self.feed.borrow_mut().push_back(cancelled_frame(id));
            self.replies.push_back(format!(
                r#"{{"reqId":"{req}","retCode":0,"op":"order.cancel"}}"#
            ));
        }
        Ok(())
    }
    fn recv(&mut self, _timeout: Duration) -> Result<String, LatencyError> {
        self.replies
            .pop_front()
            .ok_or(LatencyError::Timeout("fake trade"))
    }
}

struct FakePrivate {
    feed: Shared,
}

impl PrivateSource for FakePrivate {
    fn send(&mut self, frame: &str) -> Result<(), LatencyError> {
        if frame.contains("ping") {
            self.feed
                .borrow_mut()
                .push_back(r#"{"op":"pong","args":["1"]}"#.to_string());
        }
        Ok(())
    }
    fn recv_timeout(
        &mut self,
        _timeout: Duration,
    ) -> Result<Option<(Instant, String)>, LatencyError> {
        Ok(self
            .feed
            .borrow_mut()
            .pop_front()
            .map(|s| (Instant::now(), s)))
    }
}

fn plan(taker: bool) -> Plan {
    Plan {
        symbol: "DOGEUSDT".to_string(),
        side: OrderSide::Buy,
        qty_e9: 50_000_000_000,
        filters: F,
        ticks_from_mid: 100,
        recv_window_ms: 5000,
        wait: Duration::from_millis(10),
        taker,
    }
}

fn count(samples: &[Sample], stage: &str, via: &str) -> usize {
    samples
        .iter()
        .filter(|s| s.stage == stage && s.via == via)
        .count()
}

fn shared() -> Shared {
    Rc::new(RefCell::new(VecDeque::new()))
}

#[test]
fn full_cycle_yields_every_stage_on_both_transports() {
    let feed = shared();
    let mut rest = FakeRest {
        posts: Vec::new(),
        feed: feed.clone(),
        position: None,
    };
    let mut trade = FakeTrade {
        feed: feed.clone(),
        replies: VecDeque::new(),
        sent: Vec::new(),
    };
    let mut pf = Feed::new(FakePrivate { feed: feed.clone() });
    let p = plan(true);
    let mut b = Bench::new(&mut rest, Some(&mut trade), &mut pf, &p);
    for c in 0..3 {
        assert!(!b.run_cycle(c), "тейкер не падал");
    }
    assert!(b.errors.is_empty(), "{:?}", b.errors);
    let s = b.samples.clone();
    assert_eq!(count(&s, "rest_time", VIA_REST), 3);
    assert_eq!(count(&s, "ws_ping", VIA_PRIVATE), 3);
    for via in [VIA_REST, VIA_WS] {
        assert_eq!(count(&s, "place_ack", via), 3, "{via}");
        assert_eq!(count(&s, "place_new", via), 3, "{via}");
        assert_eq!(count(&s, "cancel_ack", via), 3, "{via}");
        assert_eq!(count(&s, "cancel_done", via), 3, "{via}");
        // Две ноги тейкера на цикл.
        assert_eq!(count(&s, "taker_ack", via), 6, "{via}");
        assert_eq!(count(&s, "taker_exec", via), 6, "{via}");
        assert_eq!(count(&s, "taker_filled", via), 6, "{via}");
    }
    assert_eq!(count(&s, "cycle_total", VIA_REST), 3);
    drop(b);
    // Вторая нога тейкера — reduceOnly противоположной стороной.
    let legs: Vec<&(String, String)> = rest
        .posts
        .iter()
        .filter(|(_, b)| b.contains("\"Market\""))
        .collect();
    assert_eq!(legs.len(), 6);
    assert!(legs[0].1.contains("\"side\":\"Buy\"") && !legs[0].1.contains("reduceOnly"));
    assert!(legs[1].1.contains("\"side\":\"Sell\"") && legs[1].1.contains("\"reduceOnly\":true"));
    assert_eq!(trade.sent.len(), 3 * (2 + 2));
    // Цена post-only кратна тику и ниже середины (покупка).
    let limit: serde_json::Value = serde_json::from_str(&rest.posts[0].1).unwrap();
    let price = crate::bybit::ws::parse_e9(limit["price"].as_str().unwrap()).unwrap();
    assert_eq!(price % F.tick_e9, 0);
    assert!(price < 200_000_000);
    // Сводка содержит все ступени.
    let rows = summarize(&s);
    assert!(rows
        .iter()
        .any(|r| r.stage == "taker_exec" && r.via == VIA_WS && r.n == 6));
    let text = format_summary(&rows);
    assert!(text.contains("taker_filled  ws"));
}

#[test]
fn skip_taker_and_no_ws_trade() {
    let feed = shared();
    let mut rest = FakeRest {
        posts: Vec::new(),
        feed: feed.clone(),
        position: None,
    };
    let mut pf = Feed::new(FakePrivate { feed: feed.clone() });
    let p = plan(false);
    let mut b: Bench<'_, FakeRest, FakeTrade, FakePrivate> =
        Bench::new(&mut rest, None, &mut pf, &p);
    assert!(!b.run_cycle(0));
    assert!(b.errors.is_empty(), "{:?}", b.errors);
    assert_eq!(count(&b.samples, "place_ack", VIA_REST), 1);
    assert_eq!(count(&b.samples, "place_ack", VIA_WS), 0);
    assert_eq!(count(&b.samples, "taker_ack", VIA_REST), 0);
    drop(b);
    assert!(rest.posts.iter().all(|(_, b)| !b.contains("Market")));
}

#[test]
fn feed_keeps_unmatched_frames_for_later_waits() {
    // `order Filled` приходит раньше `execution`: ожидание exec первым не
    // теряет Filled.
    let feed = Rc::new(RefCell::new(VecDeque::from(vec![
        r#"{"topic":"order","data":[{"orderLinkId":"x","orderStatus":"Filled"}]}"#.to_string(),
        r#"{"topic":"execution","data":[{"orderLinkId":"x","execType":"Trade"}]}"#.to_string(),
    ])));
    let mut pf = Feed::new(FakePrivate { feed });
    let w = Duration::from_millis(10);
    pf.wait(w, "exec", |f| f.has_trade_exec("x")).unwrap();
    pf.wait(w, "filled", |f| f.has_order("x", "Filled"))
        .unwrap();
    assert!(matches!(
        pf.wait(w, "nothing", |_| true),
        Err(LatencyError::Timeout("nothing"))
    ));
}

#[test]
fn flatten_closes_open_position_with_reduce_only_market() {
    let feed = shared();
    let mut rest = FakeRest {
        posts: Vec::new(),
        feed: feed.clone(),
        position: Some((OrderSide::Buy, 50_000_000_000)),
    };
    let mut pf = Feed::new(FakePrivate { feed: feed.clone() });
    let p = plan(true);
    let mut b: Bench<'_, FakeRest, FakeTrade, FakePrivate> =
        Bench::new(&mut rest, None, &mut pf, &p);
    assert_eq!(b.flatten().unwrap(), Some(50_000_000_000));
    drop(b);
    assert_eq!(rest.posts[0].0, CANCEL_ALL_PATH);
    let close: serde_json::Value = serde_json::from_str(&rest.posts[1].1).unwrap();
    assert_eq!(close["side"], "Sell");
    assert_eq!(close["reduceOnly"], true);
    assert_eq!(close["qty"], "50");
    rest.position = None;
    let mut b: Bench<'_, FakeRest, FakeTrade, FakePrivate> =
        Bench::new(&mut rest, None, &mut pf, &p);
    assert_eq!(b.flatten().unwrap(), None);
}

#[test]
fn rejected_order_is_noted_not_fatal() {
    struct Rejecting;
    impl Rest for Rejecting {
        fn get_public(&mut self, path: &str, _q: &str) -> Result<String, LatencyError> {
            Ok(if path == TIME_PATH {
                r#"{"retCode":0}"#.to_string()
            } else {
                BOOK.to_string()
            })
        }
        fn get_signed(&mut self, _p: &str, _q: &str) -> Result<String, LatencyError> {
            unreachable!()
        }
        fn post_signed(&mut self, _p: &str, _b: &str) -> Result<String, LatencyError> {
            Ok(r#"{"retCode":110007,"retMsg":"ab not enough for new order"}"#.to_string())
        }
    }
    let mut rest = Rejecting;
    let mut pf = Feed::new(FakePrivate { feed: shared() });
    let p = plan(true);
    let mut b: Bench<'_, Rejecting, FakeTrade, FakePrivate> =
        Bench::new(&mut rest, None, &mut pf, &p);
    assert!(b.run_cycle(0), "ошибка тейкера обязана вернуть true");
    assert_eq!(b.errors.len(), 2, "{:?}", b.errors);
    assert!(b.errors[0].contains("110007"));
    assert_eq!(count(&b.samples, "place_ack", VIA_REST), 0);
    assert_eq!(count(&b.samples, "cycle_total", VIA_REST), 1);
}
