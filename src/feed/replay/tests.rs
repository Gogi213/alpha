//! Тесты `feed::replay` — вынесены из `replay.rs` (C4 аудита 2026-09-17:
//! инлайн-модуль `mod tests` внутри исходника против конвенции «тесты модуля —
//! в `<модуль>/tests.rs`»; греп-тест горячего пути остался в `replay.rs`, как
//! у `live.rs`/`strategy.rs`/`react.rs`).

use super::*;
use crate::binlog::{Header, Writer};
use hftbacktest::types::LOCAL_BID_DEPTH_SNAPSHOT_EVENT;

fn header() -> Header {
    Header {
        tick_e9: 1_000_000_000,
        step_e9: 1_000_000_000,
        max_records_per_frame: 100,
    }
}

/// Шов: `Record` -> `Event`. Снапшот из одной записи затем сделка на
/// той же цене — `ReplayFeed` обязан отдать сначала `Book` со снапшотом,
/// потом `Trade` с ценой/размером/стороной, восстановленными из бит,
/// а не что-то одно или в другом порядке.
#[test]
fn replay_feed_yields_snapshot_then_trade_in_order() {
    let mut buf = Vec::new();
    let mut w = Writer::create(&mut buf, header(), 0).unwrap();
    w.write_frame(&[
        Record {
            ev: LOCAL_BID_DEPTH_SNAPSHOT_EVENT,
            exch_ts_ns: 1_000_000_000,
            local_ts_ns: 1_000_000_500,
            price_ticks: 100,
            qty_lots: 5,
            block: false,
            rpi: false,
        },
        Record {
            ev: LOCAL_BUY_TRADE_EVENT,
            exch_ts_ns: 2_000_000_000,
            local_ts_ns: 2_000_000_500,
            price_ticks: 101,
            qty_lots: 3,
            block: false,
            rpi: false,
        },
    ])
    .unwrap();
    w.flush().unwrap();
    drop(w);

    let mut feed = ReplayFeed::open(7, &buf[..]).unwrap();

    let first = feed.next_event().expect("снапшот книги");
    match first {
        Event::Market {
            symbol,
            payload: crate::bybit::ws::Event::Book(up),
            ..
        } => {
            assert_eq!(symbol, 7);
            assert!(up.is_snapshot);
            assert_eq!(up.bids, vec![(100_000_000_000, 5_000_000_000)]);
        }
        other => panic!("ожидали Book-снапшот, получили {other:?}"),
    }

    let second = feed.next_event().expect("сделка");
    match second {
        Event::Market {
            symbol,
            payload: crate::bybit::ws::Event::Trade(t),
            ..
        } => {
            assert_eq!(symbol, 7);
            assert_eq!(t.price_e9, 101_000_000_000);
            assert_eq!(t.qty_e9, 3_000_000_000);
            assert!(t.aggressor_is_buy);
        }
        other => panic!("ожидали Trade, получили {other:?}"),
    }

    assert_eq!(feed.next_event(), None, "два события — и конец файла");
}
