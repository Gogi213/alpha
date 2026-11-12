//! Тесты ядра коллектора: формат (круг без потерь), разбор кадров обеих бирж (кадры живых замеров), пул и раскладка, гистограммы,
//! свежесть, бюджет, путь, писатель (дубли, копии сырых кадров, смена суток, закрытие), сеть и шард на локальном WebSocket.

use std::collections::HashSet;
use std::net::TcpListener;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
use std::sync::Arc;
use std::time::{Duration, Instant};

use bytes::Bytes;
use serde_json::json;

use super::binance::{self, Binance};
use super::budget::Budget;
use super::bybit::{self, Bybit};
use super::codec::{Book, Decoded, Decoder, Encoder, Mark, Rec, SymEnc, Trade};
use super::exchange::{Entrance, Exchange, Family, Parsed};
use super::fresh::{Fresh, KindFresh, LATE_MS};
use super::health::{build, HealthCtx, ProcSampler};
use super::metrics::{Hist, Rolling, ShardMetrics};
use super::path::{path_verdict, percentile, PathProbe, PathWatch};
use super::pool::{balanced_chunks, rank_pool};
use super::ring::{ring, Item, Payload};
use super::scan::{each_elem, parse_i64, parse_object, single_pair, Val};
use super::shard::{spawn_shard, ShardOpts};
use super::sink::{run_sink, sink_channel};
use super::writer::{day_string, ShardWriter, WriterCfg};

const NS_PER_DAY: i64 = 86_400 * 1_000_000_000;

// ---- кадры живых замеров ----

const BN_BOOK: &str = r#"{"stream":"avaxusdt@bookTicker","data":{"e":"bookTicker","u":12081389761850,"s":"AVAXUSDT","ps":"AVAXUSDT","b":"8.6790","B":"42","a":"8.6800","A":"285","T":1794418695170,"E":1794418695171,"st":1}}"#;
const BN_TRADE: &str = r#"{"stream":"aiotusdt@aggTrade","data":{"e":"aggTrade","E":1794418694042,"a":92150638,"s":"AIOTUSDT","p":"0.1742400","q":"410","nq":"410","f":180333636,"l":180333637,"T":1794418693917,"m":true,"st":1}}"#;
const BN_MARK: &str = r#"{"stream":"!markPrice@arr@1s","data":[{"e":"markPriceUpdate","E":1794418695000,"s":"AVAXUSDT","p":"8.68001","ap":"8.68002","P":"8.67","st":1,"i":"8.6801","r":"0.00010000","T":1794441600000},{"e":"markPriceUpdate","E":1794418695000,"s":"BTCUSDT","p":"74601.07","ap":"74601.50","P":"74600.00","st":1,"i":"74634.68","r":"0.00006514","T":1794441600000}]}"#;

const BB_BOOK: &str = r#"{"topic":"orderbook.1.BTCUSDT","ts":1794433022399,"type":"snapshot","data":{"s":"BTCUSDT","b":[["74600","8.454"]],"a":[["74600.1","7.416"]],"u":174478070,"seq":843868169089},"cts":1794433022398}"#;
const BB_SNAP: &str = r#"{"topic":"tickers.BTCUSDT","type":"snapshot","data":{"symbol":"BTCUSDT","lastPrice":"74600.00","markPrice":"74601.07","indexPrice":"74634.68","fundingRate":"0.00006514","nextFundingTime":"1794441600000","bid1Price":"74600.00","openInterest":"69022.136"},"cs":1,"ts":1794433021421}"#;

fn bb_fill(side: &str, v: &str, id: &str, bt: &str) -> String {
    format!(
        r#"{{"T":1794433203183,"s":"XRPUSDT","S":"{side}","v":"{v}","p":"1.0634","L":"PlusTick","i":"{id}","BT":{bt},"RPI":false,"seq":272596956733}}"#
    )
}

fn bb_trade(fills: &[String]) -> String {
    format!(
        r#"{{"topic":"publicTrade.XRPUSDT","type":"snapshot","ts":1794433203184,"data":[{}]}}"#,
        fills.join(",")
    )
}

fn bb_delta(data: &str, ts: i64) -> String {
    format!(r#"{{"topic":"tickers.BTCUSDT","type":"delta","data":{data},"cs":2,"ts":{ts}}}"#)
}

/// Разбор кадра → записи в виде декодированных строк формата (кодировщик → декодер): сравниваем с тем, что попадёт в файл.
fn parse_all(exch: &dyn Exchange, frames: &[&str]) -> Vec<(Parsed, Vec<Decoded>)> {
    let mut parser = exch.new_parser();
    let (mut enc, mut st) = (
        Encoder::new(),
        std::collections::HashMap::<Vec<u8>, SymEnc>::new(),
    );
    let (mut out, mut dec) = (Vec::new(), Decoder::new());
    for (i, f) in frames.iter().enumerate() {
        let mut text = Vec::new();
        let rx = 1_794_433_300_000_000_000 + i64::try_from(i).unwrap() * 1000;
        let parsed = parser.parse(f.as_bytes(), &mut |rec: &Rec<'_>| {
            enc.record(
                &mut text,
                rx,
                st.entry(rec.sym().to_vec()).or_default(),
                rec,
            )
        });
        let recs = String::from_utf8(text)
            .unwrap()
            .lines()
            .map(|l| dec.line(l).unwrap())
            .collect();
        out.push((parsed, recs));
    }
    out
}

// ---- формат ----

#[test]
fn codec_roundtrip_exact() {
    let mut enc = Encoder::new();
    let mut st = std::collections::HashMap::<Vec<u8>, SymEnc>::new();
    let mut out = Vec::new();
    let b1 = Rec::Book(Book {
        sym: b"BTCUSDT",
        e: 1_000_000_000_001,
        t: 1_000_000_000_000,
        u: 500,
        id: 500,
        bid: b"74600",
        bsz: b"8.454",
        ask: b"74600.10",
        asz: b"7.416",
    });
    let b2 = Rec::Book(Book {
        sym: b"BTCUSDT",
        e: 1_000_000_000_010,
        t: 1_000_000_000_010,
        u: 503,
        id: 503,
        bid: b"74600",
        bsz: b"1",
        ask: b"74600.10",
        asz: b"2",
    });
    let t1 = Rec::Trade(Trade {
        sym: b"BTCUSDT",
        e: 1_000_000_000_005,
        t: 1_000_000_000_004,
        a: 10,
        id: 10,
        p: b"74600.1",
        q: b"0.5",
        f: 100,
        l: 102,
        m: true,
        nq: b"0.5",
    });
    let t2 = Rec::Trade(Trade {
        sym: b"BTCUSDT",
        e: 1_000_000_000_006,
        t: 1_000_000_000_006,
        a: 11,
        id: 11,
        p: b"74600.2",
        q: b"0.7",
        f: 103,
        l: 103,
        m: false,
        nq: b"0.4",
    });
    let m1 = Rec::Mark(Mark {
        sym: b"BTCUSDT",
        e: 1_000_000_001_000,
        p: b"74601.07",
        ap: b"",
        pp: b"",
        i: b"74634.68",
        r: b"0.0001",
        t: 1_000_000_500_000,
    });
    for (i, r) in [b1, t1, b2, t2, m1].iter().enumerate() {
        enc.record(
            &mut out,
            1_000_000_002_000_000_000 + i64::try_from(i).unwrap() * 250,
            st.entry(r.sym().to_vec()).or_default(),
            r,
        );
    }
    enc.ping(&mut out, 1_000_000_002_000_001_000, 305_000);
    enc.control(
        &mut out,
        1_000_000_002_000_002_000,
        "connected",
        "7 wss://x/y",
    );
    enc.raw(
        &mut out,
        1_000_000_002_000_003_000,
        b"{\"result\":null,\"id\":1}",
    );
    let text = String::from_utf8(out).unwrap();
    let mut dec = Decoder::new();
    let got: Vec<Decoded> = text.lines().map(|l| dec.line(l).unwrap()).collect();
    assert_eq!(got.len(), 8);
    assert_eq!(
        got[0],
        Decoded::Book {
            rx_ns: 1_000_000_002_000_000_000,
            e: 1_000_000_000_001,
            t: 1_000_000_000_000,
            sym: "BTCUSDT".into(),
            u: 500,
            bid: "74600".into(),
            bsz: "8.454".into(),
            ask: "74600.10".into(),
            asz: "7.416".into()
        }
    );
    assert!(
        matches!(&got[1], Decoded::Trade { a: 10, f: 100, l: 102, m: true, nq, .. } if nq == "0.5")
    );
    assert!(matches!(&got[2], Decoded::Book { u: 503, .. }));
    assert!(
        matches!(&got[3], Decoded::Trade { a: 11, f: 103, l: 103, m: false, q, nq, .. } if q == "0.7" && nq == "0.4")
    );
    assert!(matches!(&got[4], Decoded::Mark { t: 1_000_000_500_000, r, .. } if r == "0.0001"));
    assert_eq!(
        got[5],
        Decoded::Ping {
            rx_ns: 1_000_000_002_000_001_000,
            rtt_us: 305_000
        }
    );
    assert_eq!(
        got[6],
        Decoded::Control {
            rx_ns: 1_000_000_002_000_002_000,
            what: "connected".into(),
            detail: "7 wss://x/y".into()
        }
    );
    assert_eq!(
        got[7],
        Decoded::Raw {
            rx_ns: 1_000_000_002_000_003_000,
            frame: "{\"result\":null,\"id\":1}".into()
        }
    );
    // `dns` первой записи — абсолютное время, дальше разности ≥ 0
    assert!(text.starts_with("b\t1000000002000000000\t"));
    // тот же вход, что у Python-ядра: строка книги побайтно
    assert!(text
        .lines()
        .next()
        .unwrap()
        .ends_with("\tBTCUSDT\t500\t74600\t8.454\t74600.10\t7.416"));
}

#[test]
fn codec_raw_replaces_newline_only() {
    let mut out = Vec::new();
    Encoder::new().raw(&mut out, 5, b"a\nb\tc");
    assert_eq!(out, b"r\t5\ta b\tc\n");
}

#[test]
fn encoder_reset_makes_next_record_absolute() {
    let mut enc = Encoder::new();
    let mut st = SymEnc::default();
    let r = Rec::Book(Book {
        sym: b"X",
        e: 10,
        t: 10,
        u: 7,
        id: 7,
        bid: b"1",
        bsz: b"1",
        ask: b"2",
        asz: b"2",
    });
    let mut a = Vec::new();
    enc.record(&mut a, 1_000, &mut st, &r);
    enc.reset();
    st = SymEnc::default();
    let mut b = Vec::new();
    enc.record(&mut b, 1_000, &mut st, &r);
    assert_eq!(a, b);
}

// ---- сканер ----

#[test]
fn scan_object_and_values() {
    let o = parse_object(
        br#" {"a":"x","b":12,"c":-3,"d":true,"e":null,"f":[1,[2],"]"],"g":{"h":"}"} } "#,
    )
    .unwrap();
    assert_eq!(o.len(), 7);
    assert_eq!(o.get(b"a"), Some(Val::Str(b"x")));
    assert_eq!(o.get(b"b").and_then(Val::int), Some(12));
    assert_eq!(o.get(b"c").and_then(Val::int), Some(-3));
    assert_eq!(o.get(b"d").and_then(Val::boolean), Some(true));
    assert_eq!(o.get(b"e"), Some(Val::Null));
    assert_eq!(o.get(b"f").and_then(Val::raw), Some(&br#"[1,[2],"]"]"#[..]));
    assert_eq!(o.get(b"g").and_then(Val::raw), Some(&br#"{"h":"}"}"#[..]));
    assert_eq!(parse_i64(b"1.5"), None);
    assert_eq!(
        parse_i64(b"1234567890123456789"),
        None,
        "19 цифр — не разбираем"
    );
    assert_eq!(
        each_elem(br#"[ "a", 1, {"x":[1,2]}, [3] ]"#, |_| true),
        Some(4)
    );
    assert_eq!(each_elem(b"[]", |_| true), Some(0));
    assert_eq!(
        single_pair(br#"[["74600","8.4"]]"#),
        Some((&b"74600"[..], &b"8.4"[..]))
    );
    assert_eq!(single_pair(b"[]"), None);
    assert_eq!(single_pair(br#"[["1","2"],["3","4"]]"#), None);
    assert_eq!(single_pair(br#"[["1"]]"#), None);
}

#[test]
fn scan_rejects_bad_input_without_panic() {
    for bad in [
        &b""[..],
        b"{",
        b"{\"a\"",
        b"{\"a\":}",
        b"{\"a\":1,}",
        b"[1]",
        b"{\"a\":\"\\n\"}",
        b"{\"a\":tru}",
        b"{\"a\":1} x",
        b"\xff\xfe",
    ] {
        assert!(
            parse_object(bad).is_none(),
            "{:?}",
            String::from_utf8_lossy(bad)
        );
    }
    // детерминированный «шум»: срезы и порча настоящих кадров не роняют разбор
    let mut seed = 0x9e37_79b9_7f4a_7c15u64;
    let mut next = || {
        seed ^= seed << 13;
        seed ^= seed >> 7;
        seed ^= seed << 17;
        seed
    };
    for frame in [BN_BOOK, BN_TRADE, BN_MARK, BB_BOOK, BB_SNAP] {
        let b = frame.as_bytes();
        for cut in 0..b.len() {
            let _ = parse_object(&b[..cut]);
            for ex in [&Binance as &dyn Exchange, &Bybit] {
                let mut p = ex.new_parser();
                let _ = p.parse(&b[..cut], &mut |_| {});
                let _ = ex.lag_ms(&b[..cut], 1);
                let _ = ex.symbol(&b[..cut]);
            }
        }
        for _ in 0..300 {
            let mut c = b.to_vec();
            let i = usize::try_from(next() % u64::try_from(c.len()).unwrap()).unwrap();
            c[i] = u8::try_from(next() % 256).unwrap();
            let _ = parse_object(&c);
            for ex in [&Binance as &dyn Exchange, &Bybit] {
                let mut p = ex.new_parser();
                let _ = p.parse(&c, &mut |_| {});
            }
        }
    }
}

// ---- Binance ----

#[test]
fn binance_parses_book_trade_mark_exactly() {
    let r = parse_all(&Binance, &[BN_BOOK, BN_TRADE, BN_MARK]);
    assert_eq!(r[0].0, Parsed::Recs(1));
    assert_eq!(
        r[0].1[0],
        Decoded::Book {
            rx_ns: 1_794_433_300_000_000_000,
            e: 1_794_418_695_171,
            t: 1_794_418_695_170,
            sym: "AVAXUSDT".into(),
            u: 12_081_389_761_850,
            bid: "8.6790".into(),
            bsz: "42".into(),
            ask: "8.6800".into(),
            asz: "285".into()
        }
    );
    assert_eq!(r[1].0, Parsed::Recs(1));
    assert!(
        matches!(&r[1].1[0], Decoded::Trade { a: 92_150_638, f: 180_333_636, l: 180_333_637, m: true, p, q, nq, .. } if p == "0.1742400" && q == "410" && nq == "410")
    );
    assert_eq!(r[2].0, Parsed::Recs(2));
    assert!(
        matches!(&r[2].1[1], Decoded::Mark { sym, p, ap, pp, i, r, t: 1_794_441_600_000, .. } if sym == "BTCUSDT" && p == "74601.07" && ap == "74601.50" && pp == "74600.00" && i == "74634.68" && r == "0.00006514")
    );
}

#[test]
fn binance_unexpected_frames_stay_raw() {
    let new_field = BN_BOOK.replace(r#""st":1"#, r#""st":1,"zz":5"#);
    let bad_st = BN_BOOK.replace(r#""st":1"#, r#""st":2"#);
    let ps_diff = BN_BOOK.replace(r#""ps":"AVAXUSDT""#, r#""ps":"OTHER""#);
    let esc = BN_BOOK.replace("8.6790", "8.\\u0036790");
    let partial_mark = BN_MARK.replace(r#""i":"74634.68""#, r#""i2":"74634.68""#);
    let empty_arr = r#"{"stream":"!markPrice@arr@1s","data":[]}"#;
    let ack = r#"{"result":null,"id":1}"#;
    let frames = [
        new_field.as_str(),
        bad_st.as_str(),
        ps_diff.as_str(),
        esc.as_str(),
        partial_mark.as_str(),
        empty_arr,
        ack,
        "not json",
        "",
    ];
    for (f, (parsed, recs)) in frames.iter().zip(parse_all(&Binance, &frames)) {
        assert_eq!(parsed, Parsed::Raw, "{f}");
        assert!(recs.is_empty(), "{f}: частично разобранный кадр не пишется");
    }
}

#[test]
fn binance_lag_and_symbol() {
    let rx = 1_794_418_695_271_000_000;
    assert_eq!(Binance.lag_ms(BN_BOOK.as_bytes(), rx), Some(100.0));
    assert_eq!(
        Binance.lag_ms(BN_MARK.as_bytes(), 1_794_418_695_250_000_000),
        Some(250.0)
    );
    assert_eq!(Binance.lag_ms(br#"{"result":null,"id":1}"#, rx), None);
    assert_eq!(Binance.symbol(BN_BOOK.as_bytes()), Some(&b"avaxusdt"[..]));
    assert_eq!(Binance.symbol(BN_MARK.as_bytes()), None);
}

#[test]
fn binance_plan_layout_a_and_subscribe() {
    let pool: Vec<String> = (0..509).map(|i| format!("S{i:03}USDT")).collect();
    let kinds: Vec<String> = ["book", "trade", "mark"].map(String::from).to_vec();
    let plan = Binance.plan(&pool, &kinds, 0);
    let sizes = |k: &str| {
        plan.iter()
            .filter(|p| p.name.starts_with(k))
            .map(|p| p.streams.len())
            .collect::<Vec<_>>()
    };
    assert_eq!(
        (
            sizes("book").len(),
            *sizes("book").iter().max().unwrap(),
            sizes("book").iter().sum::<usize>()
        ),
        (16, 32, 509)
    );
    assert_eq!(
        (
            sizes("trade").len(),
            *sizes("trade").iter().max().unwrap(),
            sizes("trade").iter().sum::<usize>()
        ),
        (8, 64, 509)
    );
    assert_eq!(sizes("mark"), vec![1]);
    assert_eq!(
        2 * plan.len(),
        50,
        "два линии = 50 соединений, как в боевом Python"
    );
    assert_eq!(plan[0].streams[0], "s000usdt@bookTicker");
    assert_eq!(
        (plan[0].endpoint.as_str(), plan[16].endpoint.as_str()),
        ("public", "market")
    );
    assert_eq!(
        Binance.url("public", "fstream.binance.com"),
        "wss://fstream.binance.com/public/stream"
    );
    let msgs = Binance.subscribe_messages(
        &plan[16]
            .streams
            .iter()
            .chain(&plan[17].streams)
            .cloned()
            .collect::<Vec<_>>(),
    );
    let v: Vec<serde_json::Value> = msgs
        .iter()
        .map(|m| serde_json::from_str(m).unwrap())
        .collect();
    assert_eq!(
        (
            v[0]["params"].as_array().unwrap().len(),
            v[1]["params"].as_array().unwrap().len()
        ),
        (100, 28)
    );
    assert_eq!(v[0]["method"], "SUBSCRIBE");
    assert_eq!(v[1]["id"], 101);
    let explicit = Binance.plan(&pool, &kinds[..2], 100);
    assert!(explicit.iter().all(|p| p.streams.len() <= 100));
}

#[test]
fn pool_rules_binance_and_bybit() {
    let names: Vec<String> = (0..15).map(|i| format!("A{i:02}USDT")).collect();
    let info = json!({"symbols": names.iter().map(|n| json!({"symbol": n, "status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT"})).chain([
        json!({"symbol": "QTRUSDT", "status": "TRADING", "contractType": "CURRENT_QUARTER", "quoteAsset": "USDT"}),
        json!({"symbol": "BUSDUSD", "status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USD"}),
        json!({"symbol": "SETUSDT", "status": "SETTLING", "contractType": "PERPETUAL", "quoteAsset": "USDT"}),
        json!({"symbol": "NOTICKUSDT", "status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT"}),
    ]).collect::<Vec<_>>()});
    let mut tk: Vec<serde_json::Value> = names
        .iter()
        .enumerate()
        .map(|(i, n)| json!({"symbol": n, "quoteVolume": (1_000_000 * (20 - i)).to_string()}))
        .collect();
    tk.extend(
        ["QTRUSDT", "BUSDUSD", "SETUSDT"].map(|n| json!({"symbol": n, "quoteVolume": "9e9"})),
    );
    tk[14] = json!({"symbol": names[14], "quoteVolume": "300000"});
    let (pool, top) = binance::select_pool(&info, &json!(tk), 300_000.0, 10);
    assert_eq!(top, names[..10].to_vec());
    let mut want: Vec<String> = names[10..].to_vec();
    want.sort();
    assert_eq!(
        pool, want,
        "ровно порог входит; квартальные, USD, расчётные и без тикера — нет"
    );
    tk[14] = json!({"symbol": names[14], "quoteVolume": "299999.99"});
    assert!(!binance::select_pool(&info, &json!(tk), 300_000.0, 10)
        .0
        .contains(&names[14]));

    let ins: Vec<serde_json::Value> = names.iter().map(|n| json!({"symbol": n, "status": "Trading", "contractType": "LinearPerpetual", "quoteCoin": "USDT"})).chain([json!({"symbol": "FUTUSDT", "status": "Trading", "contractType": "LinearFutures", "quoteCoin": "USDT"})]).collect();
    let bt: Vec<serde_json::Value> = names
        .iter()
        .enumerate()
        .map(|(i, n)| json!({"symbol": n, "turnover24h": (1_000_000 * (20 - i)).to_string()}))
        .chain([json!({"symbol": "FUTUSDT", "turnover24h": "9e9"})])
        .collect();
    let (bpool, btop) = bybit::select_pool(&ins, &json!(bt), 300_000.0, 10);
    assert_eq!((btop, bpool.len()), (names[..10].to_vec(), 5));
    let qv: std::collections::BTreeMap<String, f64> = [
        ("B".to_string(), 5.0),
        ("A".to_string(), 5.0),
        ("C".to_string(), 1.0),
    ]
    .into();
    assert_eq!(
        rank_pool(&qv, 2.0, 1),
        (vec!["B".to_string()], vec!["A".to_string()]),
        "при равном обороте топ — по имени"
    );
}

#[test]
fn balanced_chunks_are_even() {
    let v: Vec<u32> = (0..408).collect();
    let c = balanced_chunks(&v, 80);
    assert_eq!(
        (
            c.len(),
            c.iter()
                .map(Vec::len)
                .collect::<HashSet<_>>()
                .into_iter()
                .collect::<Vec<_>>()
        ),
        (6, vec![68])
    );
    assert!(balanced_chunks::<u32>(&[], 80).is_empty());
    assert_eq!(balanced_chunks(&v[..3], 80), vec![vec![0, 1, 2]]);
}

// ---- Bybit ----

#[test]
fn bybit_book_and_lag() {
    let r = parse_all(&Bybit, &[BB_BOOK]);
    assert_eq!(
        r[0].1[0],
        Decoded::Book {
            rx_ns: 1_794_433_300_000_000_000,
            e: 1_794_433_022_399,
            t: 1_794_433_022_398,
            sym: "BTCUSDT".into(),
            u: 843_868_169_089,
            bid: "74600".into(),
            bsz: "8.454".into(),
            ask: "74600.1".into(),
            asz: "7.416".into()
        }
    );
    assert_eq!(
        Bybit.lag_ms(BB_BOOK.as_bytes(), 1_794_433_022_499_000_000),
        Some(100.0)
    );
    assert_eq!(Bybit.symbol(BB_BOOK.as_bytes()), Some(&b"BTCUSDT"[..]));
    let others = [
        BB_BOOK.replace("\"snapshot\"", "\"delta\""),
        BB_BOOK.replace(r#""b":[["74600","8.454"]]"#, r#""b":[]"#),
        BB_BOOK.replace("\"seq\"", "\"newfield\":1,\"seq\""),
        r#"{"success":true,"ret_msg":"","conn_id":"x","req_id":"","op":"subscribe"}"#.to_string(),
        r#"{"success":true,"ret_msg":"pong","conn_id":"x","req_id":"","op":"ping"}"#.to_string(),
    ];
    let refs: Vec<&str> = others.iter().map(String::as_str).collect();
    assert!(parse_all(&Bybit, &refs)
        .iter()
        .all(|(p, r)| *p == Parsed::Raw && r.is_empty()));
    assert_eq!(Bybit.lag_ms(br#"{"success":true}"#, 1), None);
}

#[test]
fn bybit_trades_batch_keys_and_raw_cases() {
    let frame = bb_trade(&[
        bb_fill("Sell", "5.2", "id-a", "false"),
        bb_fill("Sell", "675.8", "id-b", "false"),
        bb_fill("Buy", "4.8", "id-c", "false"),
    ]);
    let r = parse_all(&Bybit, &[&frame]);
    assert_eq!(r[0].0, Parsed::Recs(3));
    let a: Vec<i64> = r[0]
        .1
        .iter()
        .map(|d| {
            if let Decoded::Trade { a, .. } = d {
                *a
            } else {
                -1
            }
        })
        .collect();
    assert_eq!(
        a,
        vec![
            272_596_956_733_000,
            272_596_956_733_001,
            272_596_956_733_002
        ],
        "пачка с общим seq → строго растущий ключ"
    );
    assert!(
        matches!(&r[0].1[0], Decoded::Trade { m: true, f, l, p, q, nq, .. } if *f == 272_596_956_733_000 && f == l && p == "1.0634" && q == "5.2" && nq == "5.2")
    );
    assert!(
        matches!(&r[0].1[2], Decoded::Trade { m: false, .. }),
        "тейкер Buy → покупатель не мейкер"
    );
    for bad in [
        bb_trade(&[bb_fill("Sell", "1", "i", "true")]),
        bb_trade(&[bb_fill("Hold", "1", "i", "false")]),
        bb_trade(&[]),
        bb_trade(&[
            bb_fill("Sell", "1", "i", "false"),
            bb_fill("Sell", "1", "i", "true"),
        ]),
    ] {
        let (p, recs) = parse_all(&Bybit, &[&bad]).remove(0);
        assert_eq!(
            (p, recs.len()),
            (Parsed::Raw, 0),
            "{bad}: пачка целиком или никак"
        );
    }
    let huge = bb_trade(&vec![bb_fill("Sell", "1", "i", "false"); 1000]);
    assert_eq!(
        parse_all(&Bybit, &[&huge])[0].0,
        Parsed::Raw,
        "больше места в пачке — сырым"
    );
}

#[test]
fn bybit_mark_state_merge_and_skip() {
    let d = |data: &str, ts| bb_delta(data, ts);
    let f0 = d(r#"{"symbol":"BTCUSDT","markPrice":"1"}"#, 1_794_433_021_000);
    let f2 = d(
        r#"{"symbol":"BTCUSDT","ask1Price":"74600.10","ask1Size":"7.377"}"#,
        1_794_433_021_521,
    );
    let f3 = d(
        r#"{"symbol":"BTCUSDT","markPrice":"74602.5"}"#,
        1_794_433_021_621,
    );
    let f4 = d(
        r#"{"symbol":"BTCUSDT","markPrice":"99"}"#,
        1_794_433_021_500,
    );
    let f5 = d(
        r#"{"symbol":"BTCUSDT","indexPrice":"74700"}"#,
        1_794_433_021_700,
    );
    let r = parse_all(&Bybit, &[&f0, BB_SNAP, &f2, &f3, &f4, &f5]);
    assert_eq!(
        (r[0].0, r[0].1.len()),
        (Parsed::Recs(0), 0),
        "дельта до снапшота"
    );
    assert!(
        matches!(&r[1].1[0], Decoded::Mark { e: 1_794_433_021_421, p, i, r, t: 1_794_441_600_000, ap, pp, .. } if p == "74601.07" && i == "74634.68" && r == "0.00006514" && ap.is_empty() && pp.is_empty())
    );
    assert_eq!(r[2].1.len(), 0, "изменились bid/ask — не mark");
    assert!(
        matches!(&r[3].1[0], Decoded::Mark { e: 1_794_433_021_621, p, i, .. } if p == "74602.5" && i == "74634.68"),
        "{:?}",
        r[3]
    );
    assert_eq!(
        r[4].1.len(),
        0,
        "запоздавшая дельта другого пути не портит состояние"
    );
    assert!(matches!(&r[5].1[0], Decoded::Mark { p, i, .. } if p == "74602.5" && i == "74700"));
}

/// Живой замер 12.11 (srv): `tickers` несёт десятки полей — разбор не должен ограничиваться числом полей объекта.
#[test]
fn bybit_tickers_with_many_fields_still_parse() {
    let snap = r#"{"topic":"tickers.ETHUSDT","type":"snapshot","data":{"symbol":"ETHUSDT","tickDirection":"PlusTick","price24hPcnt":"-0.012","lastPrice":"3000.10","prevPrice24h":"3036.5","highPrice24h":"3050","lowPrice24h":"2950","prevPrice1h":"2999","markPrice":"3000.12","indexPrice":"3000.5","openInterest":"1000.1","openInterestValue":"3000000","turnover24h":"5e9","volume24h":"1.6e6","nextFundingTime":"1794441600000","fundingRate":"0.0001","bid1Price":"3000.1","bid1Size":"5","ask1Price":"3000.2","ask1Size":"7","preOpenPrice":"","preQty":"","curPreListingPhase":"","fundingIntervalHour":"8","basisRate":"0","basis":"0","deliveryFeeRate":"","predictedDeliveryPrice":"","deliveryTime":"0"},"cs":9,"ts":1794433021421}"#;
    let delta = r#"{"topic":"tickers.ETHUSDT","type":"delta","data":{"symbol":"ETHUSDT","markPrice":"3001.0","bid1Price":"3000.9","a":{"nested":[1,2]}},"cs":10,"ts":1794433021521}"#;
    let r = parse_all(&Bybit, &[snap, delta]);
    assert!(
        matches!(&r[0].1[0], Decoded::Mark { sym, p, i, r, t: 1_794_441_600_000, .. } if sym == "ETHUSDT" && p == "3000.12" && i == "3000.5" && r == "0.0001"),
        "{:?}",
        r[0]
    );
    assert!(
        matches!(&r[1].1[0], Decoded::Mark { p, i, .. } if p == "3001.0" && i == "3000.5"),
        "{:?}",
        r[1]
    );
}

#[test]
fn bybit_plan_entrances_and_subscribe() {
    let pool: Vec<String> = (0..408).map(|i| format!("S{i:03}USDT")).collect();
    let kinds: Vec<String> = ["book", "trade", "mark"].map(String::from).to_vec();
    let plan = Bybit.plan(&pool, &kinds, 0);
    assert_eq!(plan.len(), 18);
    assert!(plan.iter().all(|p| p.streams.len() == 68));
    assert_eq!(
        (
            plan[0].streams[0].as_str(),
            plan[6].streams[0].as_str(),
            plan[12].streams[0].as_str()
        ),
        (
            "orderbook.1.S000USDT",
            "publicTrade.S000USDT",
            "tickers.S000USDT"
        )
    );
    assert_eq!(
        Bybit.url("linear", "stream.bybit.kz"),
        "wss://stream.bybit.kz/v5/public/linear"
    );
    let m: serde_json::Value =
        serde_json::from_str(&Bybit.subscribe_messages(&plan[0].streams)[0]).unwrap();
    assert_eq!(
        (m["op"].as_str(), m["args"].as_array().map(Vec::len)),
        (Some("subscribe"), Some(68))
    );
    let lines = Bybit.default_entrances(2);
    assert_eq!(
        (lines[0][0].host.as_str(), lines[0][0].family),
        ("stream.bybit.com", Family::V4)
    );
    assert_eq!(
        (lines[1][0].host.as_str(), lines[1][1].host.as_str()),
        ("stream.bybit.kz", "stream.bybit.com"),
        "запасной вход — основной другой линии"
    );
    let e = Entrance::parse("stream.bybit.com@13.226.244.52", Family::Any).unwrap();
    assert_eq!(
        (e.family, e.ip.map(|i| i.to_string())),
        (Family::V4, Some("13.226.244.52".into()))
    );
    assert_eq!(
        Entrance::parse("h@v6", Family::V4).unwrap().family,
        Family::V6
    );
    assert_eq!(Entrance::parse("h", Family::V4).unwrap().family, Family::V4);
    assert!(
        Entrance::parse("@1.2.3.4", Family::Any).is_err()
            && Entrance::parse("h@nope", Family::Any).is_err()
    );
}

// ---- метрики ----

#[test]
fn hist_percentiles_match_exact_within_bucket_width() {
    let mut h = Hist::new();
    let mut xs: Vec<u64> = (0..20_000u64).map(|i| (i * 7919) % 900_000 + 1).collect();
    for &x in &xs {
        h.add(x);
    }
    xs.sort_unstable();
    for p in [0.5, 0.9, 0.99, 0.999] {
        let exact = xs[((xs.len() as f64 * p) as usize).min(xs.len() - 1)] as f64;
        let got = h.percentile(p).unwrap() as f64;
        assert!(
            (got - exact).abs() / exact < 0.04,
            "p{p}: {got} против {exact}"
        );
    }
    assert_eq!(Hist::new().percentile(0.5), None);
    let mut small = Hist::new();
    [3u64, 5, 9].iter().for_each(|&v| small.add(v));
    assert_eq!(small.percentile(0.5), Some(5), "малые значения — точно");
}

#[test]
fn rolling_window_forgets_old_slots() {
    let mut r = Rolling::default();
    r.add(1_000, 100);
    r.add(30_000, 900);
    assert_eq!(r.snapshot(31_000).count(), 2);
    assert_eq!(
        r.snapshot(70_000).count(),
        1,
        "через 60 с первый слот вышел из окна"
    );
    assert_eq!(r.snapshot(200_000).count(), 0);
    r.add(130_000, 5);
    assert_eq!(r.snapshot(131_000).count(), 1, "новый слот");
}

#[test]
fn fresh_counts_late_and_windows() {
    let mut f = Fresh::default();
    let t0 = 1_800_000_000_000i64;
    f.add(0, t0, 100);
    f.add(0, t0 + 1_000, LATE_MS + 1);
    f.add(0, t0 + 61_000, 50);
    assert_eq!(f.fragment(1, t0), None, "вида не было");
    assert_eq!(
        f.fragment(0, t0 + 120_000),
        Some(KindFresh {
            late_1h: 1,
            late_24h: 1,
            max_1h_ms: Some(LATE_MS + 1),
            max_24h_ms: Some(LATE_MS + 1)
        })
    );
    let later = f.fragment(0, t0 + 2 * 3_600_000).unwrap();
    assert_eq!(
        (
            later.late_1h,
            later.late_24h,
            later.max_1h_ms,
            later.max_24h_ms
        ),
        (0, 1, None, Some(LATE_MS + 1)),
        "через 2 ч час пуст, сутки помнят"
    );
    assert_eq!(
        f.fragment(0, t0 + 25 * 3_600_000)
            .map(|k| (k.late_24h, k.max_24h_ms)),
        Some((0, None)),
        "через сутки всё выброшено"
    );
}

#[test]
fn budget_never_exceeds_limit_in_window() {
    let b = Budget::new(3, Duration::from_secs(300));
    let t = Instant::now();
    assert!(b.try_take(t).is_none() && b.try_take(t).is_none() && b.try_take(t).is_none());
    let wait = b
        .try_take(t + Duration::from_secs(10))
        .expect("четвёртое — ждать");
    assert!(wait > Duration::from_secs(289) && wait <= Duration::from_secs(290));
    assert_eq!(b.used(t + Duration::from_secs(10)), 3);
    assert!(
        b.try_take(t + Duration::from_secs(301)).is_none(),
        "окно прошло — место есть"
    );
    let stop = AtomicBool::new(true);
    assert!(
        Budget::new(1, Duration::from_secs(300))
            .acquire(&stop)
            .is_none(),
        "остановка прерывает ожидание"
    );
}

#[test]
fn budget_with_two_windows_obeys_both() {
    // OKX: 3 подключения в секунду наряду с лимитом за 5 минут
    let b = Budget::with_rules(&[(3, Duration::from_secs(1)), (5, Duration::from_secs(300))]);
    let t = Instant::now();
    assert!(b.try_take(t).is_none() && b.try_take(t).is_none() && b.try_take(t).is_none());
    let w = b
        .try_take(t + Duration::from_millis(100))
        .expect("четвёртое в ту же секунду — ждать");
    assert!(w > Duration::from_millis(850) && w <= Duration::from_millis(900));
    assert_eq!(
        b.used(t + Duration::from_millis(100)),
        3,
        "отказ ничего не занимает"
    );
    assert!(b.try_take(t + Duration::from_millis(1_100)).is_none());
    assert!(b.try_take(t + Duration::from_millis(1_100)).is_none());
    // за 5 мин занято 5 из 5: секундное окно свободно, а общее — нет
    assert!(b.try_take(t + Duration::from_secs(10)).is_some());
}

#[test]
fn path_probe_verdict_and_watch() {
    let mut good = vec![120.0, 130.0, 150.0, 140.0, 160.0, 125.0];
    assert!(path_verdict(&mut good));
    let mut slow = vec![2_600.0; 10];
    assert!(!path_verdict(&mut slow));
    let mut few = vec![9_000.0, 9_000.0];
    assert!(path_verdict(&mut few), "мало выборок — не отбраковываем");
    let mut tail = vec![200.0; 100];
    tail[0] = 5_000.0;
    tail[1] = 5_000.0;
    assert!(!path_verdict(&mut tail), "p99 выше порога");
    assert_eq!(percentile(&mut [3.0, 1.0, 2.0], 0.5), Some(2.0));
    let t0 = Instant::now();
    let p = PathProbe::new(t0);
    assert!(
        !p.collecting(t0 + Duration::from_secs(1)) && p.collecting(t0 + Duration::from_secs(5))
    );
    assert!(!p.finished(t0 + Duration::from_secs(14)) && p.finished(t0 + Duration::from_secs(15)));
    let mut w = PathWatch::default();
    w.open(7);
    for window in 0..5 {
        for _ in 0..10 {
            w.sample(7, 3_000.0);
        }
        w.judge();
        assert_eq!(
            w.is_degraded(7),
            window >= 3,
            "два плохих окна подряд после двух пропущенных"
        );
    }
    w.close(7);
    assert!(!w.is_degraded(7));
}

// ---- писатель ----

/// Часы писателя в тестах: сутки 20 000 (приёмы тестов лежат около них), без закрытия суток по часам.
fn test_clock() -> i64 {
    20_000 * NS_PER_DAY + 80_000_000_000
}

struct Rig {
    dir: tempfile::TempDir,
    tx: super::ring::RingTx,
    metrics: Arc<ShardMetrics>,
    threads: Vec<std::thread::JoinHandle<()>>,
}

fn rig(exch: Arc<dyn Exchange>, flush_ms: u64) -> Rig {
    let dir = tempfile::tempdir().unwrap();
    let metrics = ShardMetrics::new("book00", 0, 1024, 16);
    let (tx, rx) = ring(&metrics.ring);
    let (stx, srx) = sink_channel(&metrics.sink_q);
    let cfg = WriterCfg {
        root: dir.path().to_path_buf(),
        shard: "book00".into(),
        run_id: 1_794_000_000,
        flush: Duration::from_millis(flush_ms),
        block_max: 64 * 1024,
        clock: test_clock,
    };
    let w = ShardWriter::new(cfg, exch, Arc::clone(&metrics), stx, rx);
    let (m2, d2) = (Arc::clone(&metrics), Arc::clone(&metrics.sink_q));
    let threads = vec![
        std::thread::spawn(move || w.run()),
        std::thread::spawn(move || run_sink(&srx, &d2, &m2)),
    ];
    Rig {
        dir,
        tx,
        metrics,
        threads,
    }
}

impl Rig {
    fn frame(&self, rx_ns: i64, sender: u32, f: &str) {
        self.tx
            .push(Item {
                rx_ns,
                sender,
                settled: true,
                payload: Payload::Frame(Bytes::from(f.to_string())),
            })
            .unwrap();
    }

    fn control(&self, rx_ns: i64, sender: u32, what: &'static str, detail: &str) {
        self.tx
            .push(Item {
                rx_ns,
                sender,
                settled: true,
                payload: Payload::Control {
                    what,
                    detail: detail.into(),
                },
            })
            .unwrap();
    }

    fn finish(self) -> (tempfile::TempDir, Arc<ShardMetrics>) {
        self.tx
            .push(Item {
                rx_ns: 0,
                sender: 0,
                settled: true,
                payload: Payload::Shutdown,
            })
            .unwrap();
        for t in self.threads {
            t.join().unwrap();
        }
        (self.dir, self.metrics)
    }
}

/// Все файлы суток корня: (путь, строки, число из `.ok`), по имени.
fn read_days(root: &Path) -> Vec<(String, Vec<Decoded>, Option<u64>)> {
    let mut days: Vec<_> = std::fs::read_dir(root)
        .unwrap()
        .flatten()
        .filter(|e| e.path().is_dir())
        .map(|e| e.path())
        .collect();
    days.sort();
    let mut out = Vec::new();
    for d in days {
        for f in std::fs::read_dir(&d).unwrap().flatten() {
            let p = f.path();
            if p.extension().is_some_and(|e| e == "zst") {
                let text =
                    String::from_utf8(zstd::decode_all(std::fs::File::open(&p).unwrap()).unwrap())
                        .unwrap();
                let mut dec = Decoder::new();
                let rows = text.lines().map(|l| dec.line(l).unwrap()).collect();
                let ok = std::fs::read_to_string(format!("{}.ok", p.display()))
                    .ok()
                    .map(|s| s.trim().parse().unwrap());
                out.push((
                    d.file_name().unwrap().to_string_lossy().into_owned(),
                    rows,
                    ok,
                ));
            }
        }
    }
    out
}

/// Есть ли в корне хоть один `.ok` (закрытый файл суток) — без чтения незакрытых кадров zstd.
fn any_ok(root: &Path) -> bool {
    std::fs::read_dir(root)
        .unwrap()
        .flatten()
        .filter(|e| e.path().is_dir())
        .any(|d| {
            std::fs::read_dir(d.path())
                .unwrap()
                .flatten()
                .any(|f| f.path().extension().is_some_and(|x| x == "ok"))
        })
}

fn bn_book(u: i64, e: i64) -> String {
    format!(
        r#"{{"stream":"btcusdt@bookTicker","data":{{"e":"bookTicker","u":{u},"s":"BTCUSDT","ps":"BTCUSDT","b":"1.0","B":"2","a":"1.1","A":"3","T":{e},"E":{e},"st":1}}}}"#
    )
}

#[test]
fn writer_dedups_by_key_across_senders_and_symbols() {
    let r = rig(Arc::new(Binance), 20);
    let t = 20_000 * NS_PER_DAY + 1_000_000_000;
    let ms = |m: i64| t + m * 1_000_000;
    r.control(t, 1, "connected", "1 wss://x");
    r.frame(ms(1), 1, &bn_book(10, 1_000));
    r.frame(ms(2), 2, &bn_book(10, 1_000)); // та же запись с другой линии
    r.frame(ms(3), 2, &bn_book(9, 999)); // старее записанного, но ещё не виденное — новая запись, пишется (порядок — по приходу)
    r.frame(ms(4), 1, &bn_book(11, 1_003));
    r.frame(ms(5), 1, &BN_TRADE.replace("92150638", "5"));
    r.frame(ms(6), 2, &BN_TRADE.replace("92150638", "5")); // дубль сделки
    r.frame(ms(7), 1, &BN_TRADE.replace("92150638", "6"));
    r.control(ms(8), 1, "closed", "1");
    let (dir, m) = r.finish();
    let days = read_days(dir.path());
    assert_eq!(days.len(), 1);
    let (_, rows, ok) = &days[0];
    let kinds: Vec<String> = rows
        .iter()
        .map(|d| match d {
            Decoded::Book { u, .. } => format!("b{u}"),
            Decoded::Trade { a, .. } => format!("t{a}"),
            Decoded::Control { what, .. } => what.clone(),
            other => format!("{other:?}"),
        })
        .collect();
    assert_eq!(
        kinds,
        ["connected", "b10", "b9", "b11", "t5", "t6", "closed"]
    );
    assert_eq!(
        *ok,
        Some(7),
        ".ok = число строк; `closed` до закрытия файла"
    );
    let w = m.writer();
    assert_eq!((w.records, w.dups, w.rows), (5, 2, 7));
    assert_eq!(m.sink().files_closed, 1);
}

#[test]
fn writer_raw_copies_between_senders_only_with_exchange_time() {
    let r = rig(Arc::new(Binance), 20);
    let t = 20_000 * NS_PER_DAY + 5_000_000_000;
    // кадр с временем биржи, не разобранный точно (лишний ключ): писать один раз на двух линиях
    let odd = BN_BOOK.replace(r#""st":1"#, r#""st":1,"zz":5"#);
    r.frame(t, 1, &odd);
    r.frame(t + 1_000_000, 2, &odd); // копия с другой линии → снять
    r.frame(t + 2_000_000, 1, &odd); // та же линия второй раз → настоящая вторая доставка
    r.frame(t + 3_000_000, 2, &odd); // копия второй доставки → снять
                                     // служебные кадры без времени — пишутся все (ответ подписки у двух линий)
    r.frame(t + 4_000_000, 1, r#"{"result":null,"id":1}"#);
    r.frame(t + 5_000_000, 2, r#"{"result":null,"id":1}"#);
    let (dir, m) = r.finish();
    let rows = &read_days(dir.path())[0].1;
    let raws = rows
        .iter()
        .filter(|d| matches!(d, Decoded::Raw { .. }))
        .count();
    assert_eq!(raws, 4, "2 доставки кадра с E + 2 служебных");
    let w = m.writer();
    assert_eq!((w.raw, w.raw_copies), (4, 2));
}

#[test]
fn writer_rotates_days_resets_encoder_and_keeps_keys() {
    let r = rig(Arc::new(Binance), 20);
    let d1 = 20_000 * NS_PER_DAY;
    r.frame(d1 + NS_PER_DAY - 2_000_000, 1, &bn_book(10, 1_000));
    r.frame(d1 + NS_PER_DAY - 1_000_000, 1, &bn_book(11, 1_001));
    r.frame(d1 + NS_PER_DAY + 1_000_000, 1, &bn_book(11, 1_001)); // дубль через границу суток: ключ помним
    r.frame(d1 + NS_PER_DAY + 2_000_000, 1, &bn_book(12, 1_002));
    let (dir, _) = r.finish();
    let days = read_days(dir.path());
    assert_eq!(
        days.iter().map(|d| d.0.clone()).collect::<Vec<_>>(),
        [day_string(20_000), day_string(20_001)]
    );
    assert_eq!((days[0].1.len(), days[0].2), (2, Some(2)));
    assert_eq!((days[1].1.len(), days[1].2), (1, Some(1)));
    // в новом файле первая запись абсолютная: u = 12 целиком, приём — абсолютный
    assert!(
        matches!(&days[1].1[0], Decoded::Book { u: 12, rx_ns, .. } if *rx_ns == d1 + NS_PER_DAY + 2_000_000)
    );
    let raw = zstd::decode_all(
        std::fs::File::open(
            std::fs::read_dir(dir.path().join(day_string(20_001)))
                .unwrap()
                .flatten()
                .find(|e| e.path().extension().is_some_and(|x| x == "zst"))
                .unwrap()
                .path(),
        )
        .unwrap(),
    )
    .unwrap();
    assert!(String::from_utf8(raw)
        .unwrap()
        .starts_with(&format!("b\t{}\t", d1 + NS_PER_DAY + 2_000_000)));
}

#[test]
fn writer_closes_ended_day_by_clock_without_new_records() {
    use std::sync::atomic::AtomicI64;
    static NOW: AtomicI64 = AtomicI64::new(0);
    fn clock() -> i64 {
        NOW.load(Relaxed)
    }
    let day = 20_000 * NS_PER_DAY;
    NOW.store(day + 10_000_000_000, Relaxed);
    let dir = tempfile::tempdir().unwrap();
    let metrics = ShardMetrics::new("book00", 0, 64, 16);
    let (tx, rx) = ring(&metrics.ring);
    let (stx, srx) = sink_channel(&metrics.sink_q);
    let cfg = WriterCfg {
        root: dir.path().to_path_buf(),
        shard: "book00".into(),
        run_id: 1,
        flush: Duration::from_millis(20),
        block_max: 4096,
        clock,
    };
    let w = ShardWriter::new(cfg, Arc::new(Binance), Arc::clone(&metrics), stx, rx);
    let (m2, d2) = (Arc::clone(&metrics), Arc::clone(&metrics.sink_q));
    let threads = [
        std::thread::spawn(move || w.run()),
        std::thread::spawn(move || run_sink(&srx, &d2, &m2)),
    ];
    tx.push(Item {
        rx_ns: day + 5_000_000_000,
        sender: 1,
        settled: true,
        payload: Payload::Frame(Bytes::from(bn_book(1, 1_000))),
    })
    .unwrap();
    std::thread::sleep(Duration::from_millis(300));
    assert!(!any_ok(dir.path()), "сутки ещё идут: файл открыт, .ok нет");
    NOW.store(day + NS_PER_DAY + 10_000_000_000, Relaxed); // полночь прошла, записей нового дня нет
    let started = Instant::now();
    while !any_ok(dir.path()) {
        assert!(
            started.elapsed() < Duration::from_secs(5),
            "файл ушедших суток не закрыт по часам"
        );
        std::thread::sleep(Duration::from_millis(50));
    }
    tx.push(Item {
        rx_ns: 0,
        sender: 0,
        settled: true,
        payload: Payload::Shutdown,
    })
    .unwrap();
    for t in threads {
        t.join().unwrap();
    }
    let days = read_days(dir.path());
    assert_eq!((days.len(), days[0].1.len(), days[0].2), (1, 1, Some(1)));
}

#[test]
fn writer_clamps_rx_to_keep_dns_non_negative() {
    let r = rig(Arc::new(Binance), 20);
    let t = 20_000 * NS_PER_DAY + 9_000_000_000;
    r.frame(t + 5_000, 1, &bn_book(1, 1_000));
    r.frame(t, 2, &bn_book(2, 1_001)); // пришёл «раньше» предыдущего в очереди
    let (dir, m) = r.finish();
    let rows = &read_days(dir.path())[0].1;
    assert!(
        matches!((&rows[0], &rows[1]), (Decoded::Book { rx_ns: a, .. }, Decoded::Book { rx_ns: b, .. }) if b >= a)
    );
    assert_eq!(m.writer().clamped, 1);
}

#[test]
fn writer_flush_makes_data_visible_before_close() {
    let r = rig(Arc::new(Binance), 30);
    let t = 20_000 * NS_PER_DAY + 3_000_000_000;
    r.frame(t, 1, &bn_book(1, 1_000));
    let started = Instant::now();
    let path = loop {
        let found = std::fs::read_dir(r.dir.path())
            .ok()
            .and_then(|d| d.flatten().next())
            .and_then(|d| std::fs::read_dir(d.path()).ok())
            .and_then(|d| {
                d.flatten()
                    .find(|f| f.path().extension().is_some_and(|x| x == "zst"))
            });
        if let Some(f) = found {
            if f.metadata().unwrap().len() > 0 {
                break f.path();
            }
        }
        assert!(
            started.elapsed() < Duration::from_secs(3),
            "запись не дошла до диска за 3 с при flush 30 мс"
        );
        std::thread::sleep(Duration::from_millis(10));
    };
    let mut dec = zstd::stream::read::Decoder::new(std::fs::File::open(path).unwrap()).unwrap();
    let mut buf = vec![0u8; 4096];
    let n = std::io::Read::read(&mut dec, &mut buf).unwrap();
    assert!(
        String::from_utf8_lossy(&buf[..n]).starts_with("b\t"),
        "открытый файл читается до закрытия (кадры zstd сброшены)"
    );
    let (_, m) = r.finish();
    assert!(
        m.sink().lag_out.snapshot(super::events::now_ms()).count() >= 1,
        "лаг «приём → диск» посчитан"
    );
}

// ---- сеть, сессия, шард на локальном WebSocket ----

/// Локальный источник: на каждое соединение читает подписку и шлёт кадры книги Binance с растущим u, пока клиент не закроет.
fn spawn_source(listener: TcpListener, per_conn_frames: usize) -> std::thread::JoinHandle<usize> {
    std::thread::spawn(move || {
        let mut served = 0;
        listener.set_nonblocking(false).unwrap();
        listener.set_ttl(64).unwrap();
        while served < 1 {
            let Ok((s, _)) = listener.accept() else { break };
            let mut ws = tungstenite::accept(s).unwrap();
            let sub = ws.read().unwrap();
            assert!(
                sub.to_text().unwrap().contains("SUBSCRIBE"),
                "клиент подписался"
            );
            for i in 0..per_conn_frames {
                let e = super::events::now_ms();
                let u = i64::try_from(i).unwrap() + 1;
                ws.send(tungstenite::Message::text(bn_book(u, e))).unwrap();
                std::thread::sleep(Duration::from_millis(2));
            }
            ws.send(tungstenite::Message::text(r#"{"result":null,"id":1}"#))
                .unwrap();
            while ws.read().is_ok() {}
            served += 1;
        }
        served
    })
}

fn health_keys_ok(v: &serde_json::Value) {
    for k in [
        "schema",
        "exchange",
        "ts_ms",
        "started_ms",
        "symbols",
        "ws",
        "fresh",
        "lag_in_ms",
        "lag_out_ms",
        "last_frame_ms",
        "last_write_ms",
        "backpressure",
        "process",
        "totals",
        "shards",
    ] {
        assert!(v.get(k).is_some(), "в health нет {k}");
    }
    for k in [
        "connections",
        "ping_ms",
        "timeouts_1h",
        "timeouts_24h",
        "gaps_1h",
        "gaps_24h",
        "reconnects_1h",
        "reconnects_24h",
        "entrance_switches_1h",
        "entrance_switches_24h",
    ] {
        assert!(v["ws"].get(k).is_some(), "в health.ws нет {k}");
    }
    for k in ["ring", "sink", "recv_q", "reader", "queue_lag_ms"] {
        assert!(
            v["backpressure"].get(k).is_some(),
            "в health.backpressure нет {k}"
        );
    }
}

#[test]
fn shard_end_to_end_on_local_websocket() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let source = spawn_source(listener, 300);
    let dir = tempfile::tempdir().unwrap();
    let exch: Arc<dyn Exchange> = Arc::new(Binance);
    let plan = super::exchange::ShardPlan {
        name: "book00".into(),
        endpoint: "public".into(),
        streams: vec!["btcusdt@bookTicker".into()],
    };
    let mut opts = ShardOpts::new(dir.path().to_path_buf(), 1_794_000_000);
    (opts.tls, opts.port, opts.flush) = (false, port, Duration::from_millis(30));
    let stop = Arc::new(AtomicBool::new(false));
    let budget = Arc::new(Budget::new(10, Duration::from_secs(300)));
    let ent = vec![vec![Entrance::new("127.0.0.1", Family::V4)]];
    let h = spawn_shard(&exch, &plan, &ent, &opts, &budget, &stop).unwrap();
    let metrics = Arc::clone(&h.metrics);
    let t0 = Instant::now();
    while metrics.writer().records < 300 && t0.elapsed() < Duration::from_secs(10) {
        std::thread::sleep(Duration::from_millis(50));
    }
    assert!(
        metrics.writer().records >= 300,
        "300 кадров дошли до писателя: {}",
        metrics.writer().records
    );
    let ctx = HealthCtx {
        exchange: "binance",
        pool: ["btcusdt".to_string()].into(),
        version: "test".into(),
    };
    let health = build(
        &[Arc::clone(&metrics)],
        &ctx,
        super::events::now_ms(),
        &mut ProcSampler::default(),
    );
    health_keys_ok(&health);
    assert_eq!(health["ws"]["connections"], 1);
    assert_eq!(health["symbols"]["with_data_1m"], 1);
    stop.store(true, Relaxed);
    h.join();
    assert_eq!(source.join().unwrap(), 1);
    let days = read_days(dir.path());
    assert_eq!(days.len(), 1);
    let (_, rows, ok) = &days[0];
    assert_eq!(*ok, Some(rows.len() as u64), ".ok совпадает с числом строк");
    assert!(matches!(rows.first(), Some(Decoded::Control { what, .. }) if what == "connected"));
    assert!(
        matches!(rows.last(), Some(Decoded::Control { what, .. }) if what == "closed"),
        "closed записан ДО закрытия файла: {:?}",
        rows.last()
    );
    let us: Vec<i64> = rows
        .iter()
        .filter_map(|d| {
            if let Decoded::Book { u, .. } = d {
                Some(*u)
            } else {
                None
            }
        })
        .collect();
    assert_eq!(
        us,
        (1..=300).collect::<Vec<i64>>(),
        "последовательность u без пропусков и дублей"
    );
    assert_eq!(
        rows.iter()
            .filter(|d| matches!(d, Decoded::Raw { .. }))
            .count(),
        1,
        "ответ подписки — сырым"
    );
    let events = std::fs::read_to_string(dir.path().join("events-book00.jsonl")).unwrap();
    for ev in ["connected", "subscribed", "stopped"] {
        assert!(
            events.contains(&format!("\"ev\":\"{ev}\"")),
            "нет события {ev}"
        );
    }
    let stats = std::fs::read_to_string(dir.path().join("stats-book00.jsonl")).unwrap_or_default();
    assert!(stats.is_empty() || stats.contains("ring_depth_max"));
}

/// Источник «биржа»: один общий счётчик событий (u растёт каждые 2 мс), каждое соединение шлёт все события с момента подключения —
/// два соединения несут одни и те же u (как две линии или перекрытие замены). Принимает соединения, пока не поставлен `stop`.
fn spawn_tape(
    listener: TcpListener,
    head: Arc<std::sync::atomic::AtomicI64>,
    stop: Arc<AtomicBool>,
) -> std::thread::JoinHandle<()> {
    let h2 = Arc::clone(&head);
    let s2 = Arc::clone(&stop);
    std::thread::spawn(move || {
        while !s2.load(Relaxed) {
            h2.fetch_add(1, Relaxed);
            std::thread::sleep(Duration::from_millis(2));
        }
    });
    std::thread::spawn(move || {
        listener.set_nonblocking(true).unwrap();
        let mut conns = Vec::new();
        while !stop.load(Relaxed) {
            match listener.accept() {
                Ok((s, _)) => {
                    s.set_nonblocking(false).unwrap();
                    let (head, stop) = (Arc::clone(&head), Arc::clone(&stop));
                    conns.push(std::thread::spawn(move || {
                        let Ok(mut ws) = tungstenite::accept(s) else {
                            return;
                        };
                        if ws.read().is_err() {
                            return;
                        }
                        ws.get_ref()
                            .set_read_timeout(Some(Duration::from_millis(1)))
                            .unwrap();
                        let mut sent = head.load(Relaxed);
                        while !stop.load(Relaxed) {
                            while sent < head.load(Relaxed) {
                                sent += 1;
                                if ws
                                    .send(tungstenite::Message::text(bn_book(
                                        sent,
                                        super::events::now_ms(),
                                    )))
                                    .is_err()
                                {
                                    return;
                                }
                            }
                            match ws.read() {
                                Ok(tungstenite::Message::Close(_))
                                | Err(
                                    tungstenite::Error::ConnectionClosed
                                    | tungstenite::Error::AlreadyClosed,
                                ) => return,
                                Err(tungstenite::Error::Io(e))
                                    if matches!(
                                        e.kind(),
                                        std::io::ErrorKind::WouldBlock
                                            | std::io::ErrorKind::TimedOut
                                    ) => {}
                                Err(_) => return,
                                Ok(_) => {}
                            }
                        }
                    }));
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(5))
                }
                Err(_) => break,
            }
        }
        for c in conns {
            let _ = c.join();
        }
    })
}

fn book_shard(dir: &Path, port: u16, probe: (f64, f64)) -> (ShardOpts, super::exchange::ShardPlan) {
    let plan = super::exchange::ShardPlan {
        name: "book00".into(),
        endpoint: "public".into(),
        streams: vec!["btcusdt@bookTicker".into()],
    };
    let mut opts = ShardOpts::new(dir.to_path_buf(), 1_794_000_000);
    (opts.tls, opts.port, opts.flush, opts.probe_window) =
        (false, port, Duration::from_millis(30), probe);
    (opts, plan)
}

/// u книги по файлам суток: строго подряд (+1), без дублей и пропусков.
fn assert_u_contiguous(root: &Path) -> usize {
    let days = read_days(root);
    let us: Vec<i64> = days
        .iter()
        .flat_map(|d| d.1.iter())
        .filter_map(|d| {
            if let Decoded::Book { u, .. } = d {
                Some(*u)
            } else {
                None
            }
        })
        .collect();
    assert!(us.len() > 50, "записей мало: {}", us.len());
    for w in us.windows(2) {
        assert_eq!(w[1], w[0] + 1, "u подряд: {} → {}", w[0], w[1]);
    }
    us.len()
}

fn count_events(dir: &Path, name: &str) -> usize {
    std::fs::read_to_string(dir.join("events-book00.jsonl"))
        .unwrap_or_default()
        .matches(&format!("\"ev\":\"{name}\""))
        .count()
}

#[test]
fn replacement_session_overlaps_without_gap_or_duplicates() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let (head, stop_src) = (
        Arc::new(std::sync::atomic::AtomicI64::new(0)),
        Arc::new(AtomicBool::new(false)),
    );
    let src = spawn_tape(listener, head, Arc::clone(&stop_src));
    let dir = tempfile::tempdir().unwrap();
    let (mut opts, plan) = book_shard(dir.path(), port, (0.2, 0.5));
    (opts.max_age, opts.overlap, opts.switch_wait) = (
        Duration::from_millis(1500),
        Duration::from_millis(800),
        Duration::from_secs(5),
    );
    let exch: Arc<dyn Exchange> = Arc::new(Binance);
    let stop = Arc::new(AtomicBool::new(false));
    let budget = Arc::new(Budget::new(50, Duration::from_secs(300)));
    let h = spawn_shard(
        &exch,
        &plan,
        &[vec![Entrance::new("127.0.0.1", Family::V4)]],
        &opts,
        &budget,
        &stop,
    )
    .unwrap();
    let metrics = Arc::clone(&h.metrics);
    let t0 = Instant::now();
    while count_events(dir.path(), "rotated") < 2 && t0.elapsed() < Duration::from_secs(30) {
        std::thread::sleep(Duration::from_millis(100));
    }
    assert!(
        count_events(dir.path(), "rotated") >= 2,
        "замена по возрасту сработала дважды"
    );
    stop.store(true, Relaxed);
    h.join();
    stop_src.store(true, Relaxed);
    src.join().unwrap();
    let n = assert_u_contiguous(dir.path());
    let w = metrics.writer();
    assert!(
        w.dups > 0,
        "на перекрытии оба соединения несли одни u — дубли снял писатель ({} записей, {} дублей)",
        n,
        w.dups
    );
    assert!(
        count_events(dir.path(), "connected") >= 3,
        "подключений не меньше трёх (первая + две замены)"
    );
}

#[test]
fn two_lanes_deliver_one_gapless_stream() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let (head, stop_src) = (
        Arc::new(std::sync::atomic::AtomicI64::new(0)),
        Arc::new(AtomicBool::new(false)),
    );
    let src = spawn_tape(listener, head, Arc::clone(&stop_src));
    let dir = tempfile::tempdir().unwrap();
    let (opts, plan) = book_shard(dir.path(), port, (4.0, 15.0));
    let exch: Arc<dyn Exchange> = Arc::new(Binance);
    let stop = Arc::new(AtomicBool::new(false));
    let budget = Arc::new(Budget::new(50, Duration::from_secs(300)));
    let lane = vec![Entrance::new("127.0.0.1", Family::V4)];
    let h = spawn_shard(&exch, &plan, &[lane.clone(), lane], &opts, &budget, &stop).unwrap();
    let metrics = Arc::clone(&h.metrics);
    let t0 = Instant::now();
    while metrics.writer().records < 400 && t0.elapsed() < Duration::from_secs(15) {
        std::thread::sleep(Duration::from_millis(50));
    }
    stop.store(true, Relaxed);
    h.join();
    stop_src.store(true, Relaxed);
    src.join().unwrap();
    let n = assert_u_contiguous(dir.path());
    let w = metrics.writer();
    assert!(
        w.dups as usize > n / 3,
        "вторая линия принесла те же записи: дублей {} при {} записях",
        w.dups,
        n
    );
    assert_eq!(metrics.connected.load(Relaxed), 0, "все соединения закрыты");
}

#[test]
fn entrance_switches_to_spare_after_three_short_sessions() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let (head, stop_src) = (
        Arc::new(std::sync::atomic::AtomicI64::new(0)),
        Arc::new(AtomicBool::new(false)),
    );
    let src = spawn_tape(listener, head, Arc::clone(&stop_src));
    let dir = tempfile::tempdir().unwrap();
    let (opts, plan) = book_shard(dir.path(), port, (4.0, 15.0));
    let exch: Arc<dyn Exchange> = Arc::new(Binance);
    let stop = Arc::new(AtomicBool::new(false));
    let budget = Arc::new(Budget::new(50, Duration::from_secs(300)));
    // основной вход — адрес, где никто не слушает (отказ в соединении), запасной — рабочий источник
    let dead = Entrance {
        host: "127.0.0.1".into(),
        ip: Some("127.0.0.2".parse().unwrap()),
        family: Family::V4,
    };
    let h = spawn_shard(
        &exch,
        &plan,
        &[vec![dead, Entrance::new("127.0.0.1", Family::V4)]],
        &opts,
        &budget,
        &stop,
    )
    .unwrap();
    let metrics = Arc::clone(&h.metrics);
    let t0 = Instant::now();
    while metrics.writer().records < 100 && t0.elapsed() < Duration::from_secs(30) {
        std::thread::sleep(Duration::from_millis(100));
    }
    stop.store(true, Relaxed);
    h.join();
    stop_src.store(true, Relaxed);
    src.join().unwrap();
    let ev = std::fs::read_to_string(dir.path().join("events-book00.jsonl")).unwrap();
    assert_eq!(
        count_events(dir.path(), "error"),
        3,
        "три неудачи на основном входе"
    );
    assert!(
        ev.contains("\"ev\":\"entrance_switch\"")
            && ev.contains("\"reason\":\"connect_fail\"")
            && ev.contains("\"fails\":3"),
        "{ev}"
    );
    assert!(
        metrics.writer().records >= 100,
        "данные пошли через запасной вход"
    );
    assert_eq!(metrics.lists().entrance_switches.len(), 1);
}

const BB_BOOK_KZ: &str = r#"{"topic":"orderbook.1.BTCUSDT","ts":1794433022405,"type":"snapshot","data":{"s":"BTCUSDT","b":[["74600","8.454"]],"a":[["74600.1","7.416"]],"u":858576,"seq":843868169089},"cts":1794433022398}"#;

/// Замер 12.11 (захват кадров двух входов): `u` у входов Bybit — свои счётчики (79 205 427 против 858 576 для одного и того же состояния
/// книги), одинаков сквозной `seq`. Дубль — по `seq`, не по `u`: иначе вторая линия молча отбрасывается целиком.
#[test]
fn bybit_entrances_with_different_u_but_same_seq_are_one_record() {
    let r = rig(Arc::new(Bybit), 20);
    let t = 20_000 * NS_PER_DAY + 2_000_000_000;
    let next = BB_BOOK
        .replace("843868169089", "843868169100")
        .replace("1794433022399", "1794433022420");
    let next_kz = BB_BOOK_KZ
        .replace("843868169089", "843868169100")
        .replace("1794433022405", "1794433022430");
    r.frame(t, 1, BB_BOOK); // com
    r.frame(t + 1_000_000, 2, BB_BOOK_KZ); // kz: то же состояние, другой u
    r.frame(t + 2_000_000, 2, &next_kz); // kz впереди com: следующее состояние первым пришло по kz
    r.frame(t + 3_000_000, 1, &next);
    r.frame(t + 4_000_000, 1, BB_BOOK); // повтор кадра той же сессией (книга не менялась)
    let (dir, m) = r.finish();
    let rows = &read_days(dir.path())[0].1;
    let us: Vec<i64> = rows
        .iter()
        .filter_map(|d| {
            if let Decoded::Book { u, .. } = d {
                Some(*u)
            } else {
                None
            }
        })
        .collect();
    assert_eq!(
        us,
        vec![843_868_169_089, 843_868_169_100],
        "по одной записи на состояние книги, ключ — seq"
    );
    assert_eq!(m.writer().dups, 3);
}

/// Нарезка сделок на пачки у входов разная (`a = seq·1000 + номер` у одной сделки различается): личность сделки — её UUID.
#[test]
fn bybit_trade_identity_is_uuid_not_synthetic_key() {
    let r = rig(Arc::new(Bybit), 20);
    let t = 20_000 * NS_PER_DAY + 3_000_000_000;
    let fill = |id: &str| bb_fill("Sell", "5.2", id, "false");
    let a = bb_trade(&[fill("u-1"), fill("u-2")]); // com: пачка из двух
    let b = bb_trade(&[fill("u-2")]).replace("272596956733", "272596956734"); // kz: вторая сделка отдельной пачкой с другим seq
    r.frame(t, 1, &a);
    r.frame(t + 1_000_000, 2, &b);
    r.frame(
        t + 2_000_000,
        2,
        &bb_trade(&[fill("u-3")]).replace("272596956733", "272596956735"),
    );
    let (dir, m) = r.finish();
    let rows = &read_days(dir.path())[0].1;
    let n = rows
        .iter()
        .filter(|d| matches!(d, Decoded::Trade { .. }))
        .count();
    assert_eq!(
        n, 3,
        "u-1, u-2, u-3 по одному разу, хотя ключ a у u-2 разный"
    );
    assert_eq!(m.writer().dups, 1);
}

#[test]
fn writer_seen_window_forgets_after_two_minutes() {
    let r = rig(Arc::new(Binance), 20);
    let t = 20_000 * NS_PER_DAY + 4_000_000_000;
    r.frame(t, 1, &bn_book(10, 1_000));
    r.frame(t + 100_000_000_000, 2, &bn_book(10, 1_000)); // через 100 с — ещё дубль
    r.frame(t + 130_000_000_000, 2, &bn_book(10, 1_000)); // через 130 с — личность забыта: пишется
    let (dir, m) = r.finish();
    let rows = &read_days(dir.path())[0].1;
    assert_eq!(rows.len(), 2);
    assert_eq!(m.writer().dups, 1);
}

/// Линия ушла на запасной вход, потом пробует вернуться на основной (мёртвый): замена не принимает путь — поток не рвётся, линия остаётся на запасном.
#[test]
fn failed_return_to_primary_keeps_the_stream() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let (head, stop_src) = (
        Arc::new(std::sync::atomic::AtomicI64::new(0)),
        Arc::new(AtomicBool::new(false)),
    );
    let src = spawn_tape(listener, head, Arc::clone(&stop_src));
    let dir = tempfile::tempdir().unwrap();
    let (mut opts, plan) = book_shard(dir.path(), port, (4.0, 15.0));
    (opts.return_after, opts.switch_wait) = (Duration::from_secs(2), Duration::from_secs(3));
    let exch: Arc<dyn Exchange> = Arc::new(Binance);
    let stop = Arc::new(AtomicBool::new(false));
    let budget = Arc::new(Budget::new(100, Duration::from_secs(300)));
    let dead = Entrance {
        host: "127.0.0.1".into(),
        ip: Some("127.0.0.2".parse().unwrap()),
        family: Family::V4,
    };
    let h = spawn_shard(
        &exch,
        &plan,
        &[vec![dead, Entrance::new("127.0.0.1", Family::V4)]],
        &opts,
        &budget,
        &stop,
    )
    .unwrap();
    let metrics = Arc::clone(&h.metrics);
    let t0 = Instant::now();
    while (count_events(dir.path(), "switch_failed") < 1 || metrics.writer().records < 100)
        && t0.elapsed() < Duration::from_secs(60)
    {
        std::thread::sleep(Duration::from_millis(100));
    }
    // ещё немного данных после неудачной замены — поток по-прежнему идёт через запасной вход
    let before = metrics.writer().records;
    std::thread::sleep(Duration::from_millis(500));
    let after = metrics.writer().records;
    stop.store(true, Relaxed);
    h.join();
    stop_src.store(true, Relaxed);
    src.join().unwrap();
    assert!(count_events(dir.path(), "switch_failed") >= 1);
    assert!(count_events(dir.path(), "entrance_return") >= 1);
    assert!(after > before, "поток идёт после неудачной замены");
    assert_u_contiguous(dir.path());
}
