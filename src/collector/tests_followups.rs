//! Тесты доработок после пул-фикса (16.11): независимый счётчик «не подписано», нагрузка символов и раскладка по ней, состояние пула между запусками,
//! режим «стоянка всей биржи», тревога бюджета подключений, дозапрос потерянных сделок.

use std::collections::{BTreeMap, HashSet};
use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
use std::sync::mpsc::channel;
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::json;

use super::backfill::{
    work, BackfillStats, GapReq, IdTracker, OutFile, WorkCfg, CONFIRM_MS, MAX_PENDING_PER_SYMBOL,
};
use super::binance::{parse_agg_page, Binance};
use super::budget::{Budget, Cap};
use super::exchange::{next_range_desc, Exchange, RestTrade, TradePage};
use super::health::{build, Eligible, HealthCtx, PoolHealth, ProcSampler};
use super::livepool::{
    spread_plans, Layout, LivePool, Op, PoolState, RateTracker, Rates, SavedState, Weights,
};
use super::metrics::ShardMetrics;
use super::storm::{self, Storm, StormGate};

fn kinds() -> Vec<String> {
    ["book", "trade", "mark"].map(String::from).to_vec()
}

fn names(n: usize) -> Vec<String> {
    (0..n).map(|i| format!("S{i:03}USDT")).collect()
}

// ---- независимый счётчик «не подписано» ----

fn elig(name: &str, since_ms: i64) -> Eligible {
    Eligible {
        lower: name.to_lowercase(),
        name: name.into(),
        qv: 1_000_000.0,
        since_ms,
    }
}

fn book_shard(seen: &[(&str, i64)]) -> Arc<ShardMetrics> {
    let m = ShardMetrics::new("book00", 0, 64, 16);
    for (s, last) in seen {
        m.writer()
            .seen
            .insert(s.as_bytes().into(), (s.to_lowercase(), *last, 5));
    }
    m
}

#[test]
fn not_subscribed_counts_rest_eligible_symbols_without_a_book_frame_in_the_window() {
    let now = 3_600_000;
    let m = book_shard(&[
        ("BTCUSDT", now - 5_000),
        // тихий символ: последний кадр 5 мин назад — в окне 10 мин, не потеря (до этого окно 60 с красило его)
        ("THINUSDT", now - 300_000),
        // молчит 11 мин — не подписан
        ("DEADUSDT", now - 660_000),
    ]);
    let live = PoolHealth {
        last_ok_ms: now - 30_000,
        elig: Arc::new(vec![
            elig("BTCUSDT", 0),
            elig("THINUSDT", 0),
            elig("DEADUSDT", 0),
            // в составе REST, кадров книги нет вовсе — не подписан, оборот в счётчике
            Eligible {
                qv: 5_000_000.0,
                ..elig("NOFRAMEUSDT", 0)
            },
            // добавлен минуту назад: окно ещё не прошло
            elig("NEWUSDT", now - 60_000),
        ]),
        ..PoolHealth::default()
    };
    let ctx = HealthCtx {
        exchange: "binance",
        pool: ["btcusdt", "thinusdt", "deadusdt", "noframeusdt", "newusdt"]
            .map(String::from)
            .into(),
        version: "t".into(),
        live: Some(live),
        book_window_ms: 600_000,
    };
    let v = build(&[m], &ctx, now, &mut ProcSampler::default());
    let s = &v["symbols"];
    assert_eq!(s["book_window_s"], 600);
    assert_eq!(s["rest_checked"], 4, "новый символ ещё не проверяется");
    assert_eq!(s["not_subscribed"], 2);
    assert_eq!(s["not_subscribed_qv_usd"], 6_000_000.0);
    assert_eq!(s["not_subscribed_top"][0][0], "NOFRAMEUSDT");
    assert_eq!(s["not_subscribed_top"][1][0], "DEADUSDT");
    // «с данными за минуту» — прежнее число, на красный не влияет
    assert_eq!(s["with_data_1m"], 1);
}

#[test]
fn not_subscribed_is_not_counted_before_the_first_rest_poll_or_in_the_first_window() {
    let m = book_shard(&[("BTCUSDT", 800_000)]);
    let live = |ok: i64| PoolHealth {
        last_ok_ms: ok,
        elig: Arc::new(vec![elig("BTCUSDT", 0), elig("ETHUSDT", 0)]),
        ..PoolHealth::default()
    };
    let ctx = |l| HealthCtx {
        exchange: "binance",
        pool: HashSet::new(),
        version: "t".into(),
        live: Some(l),
        book_window_ms: 600_000,
    };
    // процесс моложе окна: `started` шарда = 0, now = 5 мин
    let v = build(
        std::slice::from_ref(&m),
        &ctx(live(100_000)),
        300_000,
        &mut ProcSampler::default(),
    );
    assert!(v["symbols"]["not_subscribed"].is_null());
    // опроса REST ещё не было
    let v = build(
        std::slice::from_ref(&m),
        &ctx(live(0)),
        900_000,
        &mut ProcSampler::default(),
    );
    assert!(v["symbols"]["not_subscribed"].is_null());
    // после окна и опроса: ETH без кадров
    let v = build(
        std::slice::from_ref(&m),
        &ctx(live(800_000)),
        900_000,
        &mut ProcSampler::default(),
    );
    assert_eq!(v["symbols"]["not_subscribed"], 1);
}

// ---- нагрузка и раскладка ----

fn rates_of(kind: &str, pairs: &[(&str, f64)]) -> Rates {
    let mut r = Rates::default();
    for (s, v) in pairs {
        r.set(kind, &s.to_lowercase(), *v);
    }
    r
}

#[test]
fn rate_tracker_takes_the_increment_between_polls_and_smooths_it() {
    let mut t = RateTracker::default();
    t.observe(0, [("book", "btcusdt", 1_000)]);
    assert!(t.rates().is_empty(), "первое наблюдение — только отметка");
    t.observe(10_000, [("book", "btcusdt", 2_000)]);
    assert!(t.rates().is_empty(), "чаще 30 с — нового значения нет");
    t.observe(60_000, [("book", "btcusdt", 7_000)]);
    assert_eq!(t.rates().get("book", "btcusdt"), Some(100.0));
    t.observe(120_000, [("book", "btcusdt", 7_000 + 60 * 300)]);
    // скользящее среднее: 0,5 × 300 + 0,5 × 100
    assert_eq!(t.rates().get("book", "btcusdt"), Some(200.0));
}

#[test]
fn weights_fall_back_to_turnover_times_measured_rate_per_dollar() {
    let rates = rates_of("book", &[("AUSDT", 100.0), ("BUSDT", 300.0)]);
    let qv: BTreeMap<String, f64> = [("AUSDT", 1e6), ("BUSDT", 3e6), ("CUSDT", 2e6)]
        .map(|(s, v)| (s.to_string(), v))
        .into();
    let w = Weights::new(rates, &qv);
    assert_eq!(w.weight("book", "AUSDT"), 100.0);
    assert!(
        (w.weight("book", "CUSDT") - 200.0).abs() < 1e-9,
        "оборот × 1e-4 кадров/с на $"
    );
    assert!(w.measured("book") && !w.measured("trade"));
    // без измерений — условные единицы: оборот в млн $, порядок сохраняется
    let w = Weights::new(Rates::default(), &qv);
    assert!(w.weight("book", "BUSDT") > w.weight("book", "AUSDT"));
    assert!(!w.measured("book"));
}

fn two_hot_pool() -> (Vec<String>, Weights) {
    let mut pool = vec!["AAAUSDT".to_string(), "AABUSDT".to_string()];
    pool.extend(names(30));
    pool.sort();
    let mut r = rates_of("book", &[("AAAUSDT", 1_500.0), ("AABUSDT", 1_400.0)]);
    for s in names(30) {
        r.set("book", &s.to_lowercase(), 5.0);
    }
    (pool, Weights::new(r, &BTreeMap::new()))
}

#[test]
fn spread_plans_put_the_heaviest_symbols_on_different_shards_and_keep_every_stream() {
    let exch = Binance;
    let (pool, w) = two_hot_pool();
    let plans = exch.plan(&pool, &kinds(), 20);
    let out = spread_plans(&exch, &plans, &pool, &kinds(), 20, &w);
    assert_eq!(out.len(), plans.len());
    let at = |sym: &str| {
        out.iter()
            .position(|p| p.streams.iter().any(|s| s == &format!("{sym}@bookTicker")))
            .unwrap()
    };
    assert_ne!(at("aaausdt"), at("aabusdt"), "алфавит клал их рядом");
    let all = |ps: &[super::exchange::ShardPlan]| {
        let mut v: Vec<String> = ps.iter().flat_map(|p| p.streams.clone()).collect();
        v.sort();
        v
    };
    assert_eq!(
        all(&out),
        all(&plans),
        "ни один поток не потерян и не удвоен"
    );
    for (a, b) in out.iter().zip(&plans) {
        assert_eq!((&a.name, &a.endpoint), (&b.name, &b.endpoint));
        assert!(a.streams.len() <= 20, "{}: {}", a.name, a.streams.len());
    }
    // mark — один поток на все символы, пул его не трогает
    assert_eq!(
        out.iter().find(|p| p.name == "mark00"),
        plans.iter().find(|p| p.name == "mark00")
    );
}

#[test]
fn a_new_symbol_goes_to_the_lightest_shard_and_a_shard_over_the_cap_is_not_filled() {
    let exch = Binance;
    let (pool, w) = two_hot_pool();
    // алфавитный план: AAA и AAB — в book00 (16 потоков), остальные — в book01; место есть в обоих (предел 20)
    let plans = exch.plan(&pool, &kinds(), 20);
    let mut layout = Layout::new(&exch, &plans, &pool, &kinds(), 20);
    layout.set_weights(w);
    let loads = layout.loads();
    assert!(loads[0] > 2_900.0 && loads[1] < 100.0, "{loads:?}");
    let book_slot = |ops: &[Op]| {
        ops.iter().find_map(|o| match o {
            Op::Append { slot, streams } if streams[0].ends_with("@bookTicker") => Some(*slot),
            _ => None,
        })
    };
    // лёгкий новый символ — в наименее нагруженный шард, а не в тот, где AAA и AAB
    let ops = layout.add_symbol(&exch, "ZZZUSDT");
    assert_eq!(layout.slots()[book_slot(&ops).unwrap()].name, "book01");
    // все шарды книги выше потолка Binance (1 800 кадров/с) с новым тяжёлым символом — открывается новый шард, занятый не набивается
    let mut r = rates_of(
        "book",
        &[
            ("AAAUSDT", 1_500.0),
            ("AABUSDT", 1_400.0),
            ("YYYUSDT", 1_000.0),
        ],
    );
    for s in names(30) {
        r.set("book", &s.to_lowercase(), 100.0);
    }
    layout.set_weights(Weights::new(r, &BTreeMap::new()));
    let ops = layout.add_symbol(&exch, "YYYUSDT");
    assert!(
        ops.iter()
            .any(|o| matches!(o, Op::Spawn { plan, .. } if plan.name == "book02")),
        "{ops:?}"
    );
}

#[test]
fn without_weights_placement_is_the_old_least_streams_rule() {
    let exch = Binance;
    let pool = names(30);
    let plans = exch.plan(&pool, &kinds(), 16);
    let mut layout = Layout::new(&exch, &plans, &pool, &kinds(), 16);
    let ops = layout.add_symbol(&exch, "NEWUSDT");
    let slots: Vec<usize> = ops
        .iter()
        .filter_map(|o| match o {
            Op::Append { slot, .. } => Some(*slot),
            _ => None,
        })
        .collect();
    assert_eq!(slots.len(), 2, "книга и сделки");
    assert_eq!(layout.slots()[slots[0]].streams, 16);
}

// ---- состояние пула между запусками ----

#[test]
fn saved_state_round_trips_and_keeps_only_symbols_still_inside_the_hold() {
    let dir = tempfile::tempdir().unwrap();
    let marks: BTreeMap<String, i64> = [
        ("AUSDT", 1_000),
        ("BUSDT", 50_000_000),
        ("OLDUSDT", 100),
        ("GONEUSDT", 49_000_000),
    ]
    .map(|(s, t)| (s.to_string(), t))
    .into();
    SavedState::save(
        dir.path(),
        60_000_000,
        86_400,
        &marks,
        &rates_of("book", &[("AUSDT", 12.5)]),
    )
    .unwrap();
    let st = SavedState::load(dir.path()).unwrap();
    assert_eq!(st.marks, marks);
    assert_eq!(st.rates.get("book", "ausdt"), Some(12.5));
    let tradable: BTreeMap<String, f64> = [("BUSDT", 1.0), ("OLDUSDT", 1.0), ("AUSDT", 1.0)]
        .map(|(s, v)| (s.to_string(), v))
        .into();
    // сейчас 60 000 с; выдержка 24 ч = 86 400 с: BUSDT и GONEUSDT внутри, OLDUSDT (метка 0,1 с) — тоже внутри; AUSDT в пуле; GONEUSDT не торгуется
    let held = st.held(&["AUSDT".to_string()], 60_000_000, 86_400, &tradable);
    assert_eq!(
        held.keys().cloned().collect::<Vec<_>>(),
        vec!["BUSDT".to_string(), "OLDUSDT".to_string()]
    );
    // выдержка истекла для старой метки
    let held = st.held(&[], 200_000_000, 86_400, &tradable);
    assert!(held.is_empty());
    assert!(SavedState::load(&dir.path().join("nope")).is_none());
}

#[test]
fn restart_seeded_with_a_held_symbol_does_not_shorten_its_hold() {
    let held: BTreeMap<String, i64> = [("HELDUSDT".to_string(), 1_000)].into();
    let pool = vec!["AUSDT".to_string(), "HELDUSDT".to_string()];
    let mut st = PoolState::seeded(&pool, 50_000_000, 300_000.0, 0, 86_400, &held);
    assert_eq!(st.marks().get("AUSDT"), Some(&50_000_000));
    assert_eq!(
        st.marks().get("HELDUSDT"),
        Some(&1_000),
        "метка прежнего запуска, не момент старта"
    );
    // оборот HELD под порогом, с метки прошло > 24 ч: символ снимается сразу, а не ещё через сутки
    let qv: BTreeMap<String, f64> = [("AUSDT", 1e6), ("HELDUSDT", 1e3)]
        .map(|(s, v)| (s.to_string(), v))
        .into();
    let r = st.review(&qv, 87_000_000).unwrap();
    assert_eq!(r.stale.len(), 1);
    assert_eq!(r.stale[0].0, "HELDUSDT");
}

#[test]
fn live_pool_saves_state_after_each_poll_and_exposes_the_eligible_list() {
    let dir = tempfile::tempdir().unwrap();
    let exch: Arc<dyn Exchange> = Arc::new(Binance);
    let pool = names(5);
    let plans = exch.plan(&pool, &kinds(), 0);
    let mut lp = LivePool::new(
        Arc::clone(&exch),
        PoolState::new(&pool, 1_000, 300_000.0, 0, 86_400),
        Layout::new(exch.as_ref(), &plans, &pool, &kinds(), 0),
        None,
        300,
        86_400,
    )
    .with_state(dir.path().to_path_buf(), Rates::default());
    lp.observe(0, [("book", "s000usdt", 0)]);
    lp.observe(60_000, [("book", "s000usdt", 6_000)]);
    let qv: BTreeMap<String, f64> = pool.iter().map(|s| (s.clone(), 1e6)).collect();
    let _ = lp.apply(
        &super::exchange::Turnover {
            qv,
            meta: BTreeMap::new(),
        },
        60_000,
    );
    let st = SavedState::load(dir.path()).unwrap();
    assert_eq!(st.marks.len(), 5);
    assert_eq!(st.rates.get("book", "s000usdt"), Some(100.0));
    let h = lp.health();
    assert_eq!(h.elig.len(), 5);
    assert_eq!(h.rates_tracked, 1);
}

// ---- стоянка всей биржи ----

#[test]
fn storm_turns_on_for_a_majority_of_shards_and_off_a_minute_after_it_ends() {
    let s = Storm::default();
    s.evaluate(3, 6, 0);
    assert!(!s.is_on(), "ровно половина — не большинство");
    s.evaluate(4, 6, 1_000);
    assert!(s.is_on());
    s.evaluate(1, 6, 30_000);
    assert!(s.is_on(), "минуты ещё не прошло");
    s.evaluate(1, 6, 70_000);
    assert!(!s.is_on());
    assert_eq!(s.json(80_000)["episodes"], 1);
    // меньше четырёх шардов — режим не включается
    let s = Storm::default();
    s.evaluate(3, 3, 0);
    assert!(!s.is_on());
}

#[test]
fn in_a_storm_a_shard_gets_one_probe_replacement_and_then_holds_its_old_line() {
    let probe = Cap::new(1, storm::PROBE_PERIOD);
    let s = Storm::default();
    let now = Instant::now();
    assert_eq!(
        storm::gate(Some(&s), &probe, now),
        StormGate::Open,
        "режим выключен"
    );
    assert_eq!(storm::gate(None, &probe, now), StormGate::Open);
    s.evaluate(5, 6, 0);
    assert_eq!(
        storm::gate(Some(&s), &probe, now),
        StormGate::Open,
        "пробная"
    );
    assert_eq!(storm::gate(Some(&s), &probe, now), StormGate::Hold);
    assert_eq!(
        storm::gate(Some(&s), &probe, now + Duration::from_secs(10)),
        StormGate::Hold
    );
    // шард с испорченной линией или паузой данных — испорчен
    let m = ShardMetrics::new("book00", 0, 64, 16);
    assert!(!storm::impaired(&m));
    m.lane_bad[1].store(true, Relaxed);
    assert!(storm::impaired(&m));
    m.lane_bad[1].store(false, Relaxed);
    m.in_gap.store(true, Relaxed);
    assert!(storm::impaired(&m));
}

#[test]
fn budget_alarm_fires_at_ninety_percent_or_when_a_connection_waits() {
    let b = Budget::new(10, Duration::from_secs(300));
    let now = Instant::now();
    for _ in 0..8 {
        assert!(b.try_take(now).is_none());
    }
    let s = b.snapshot(now);
    assert_eq!((s.limit, s.used, s.waiting), (10, 8, 0));
    assert!(!s.alarm());
    assert!(b.try_take(now).is_none());
    assert!(b.snapshot(now).alarm(), "9 из 10");
    assert_eq!(b.snapshot(now).json()["free"], 1);
    // ожидание считается: место занято до конца окна, подключение ждёт
    let b = Arc::new(Budget::new(1, Duration::from_secs(300)));
    assert!(b.try_take(now).is_none());
    let stop = Arc::new(AtomicBool::new(false));
    let (b2, stop2) = (Arc::clone(&b), Arc::clone(&stop));
    let h = std::thread::spawn(move || b2.acquire(&[&stop2], &|| false));
    std::thread::sleep(Duration::from_millis(150));
    let snap = b.snapshot(Instant::now());
    assert_eq!((snap.waiting, snap.waits_total), (1, 1));
    assert!(snap.alarm());
    stop.store(true, Relaxed);
    assert!(h.join().unwrap().is_none());
    assert_eq!(b.snapshot(Instant::now()).waiting, 0);
}

// ---- пропуски номеров сделок ----

#[test]
fn reordered_ids_are_not_a_gap_but_a_missing_range_is_confirmed_after_the_window() {
    let mut t = IdTracker::default();
    t.on_span(0, b"BTCUSDT", 100, 100, 0);
    t.on_span(0, b"BTCUSDT", 103, 103, 10);
    assert_eq!(t.pending(), 1);
    // 101 и 102 пришли позже (другая линия): пропуск закрыт по частям
    t.on_span(0, b"BTCUSDT", 102, 102, 20);
    t.on_span(0, b"BTCUSDT", 101, 101, 30);
    assert_eq!(t.pending(), 0);
    assert_eq!(t.reordered, 2);
    assert!(t.scan(CONFIRM_MS * 10, "trade00").is_empty());
    // настоящий пропуск 106..108
    t.on_span(0, b"BTCUSDT", 104, 105, 1_000);
    t.on_span(0, b"BTCUSDT", 109, 110, 2_000);
    assert!(
        t.scan(2_000 + CONFIRM_MS - 1_001, "trade00").is_empty(),
        "ещё не подтверждён"
    );
    let g = t.scan(2_000 + CONFIRM_MS, "trade00");
    assert_eq!(
        g,
        vec![GapReq {
            shard: "trade00".into(),
            symbol: "BTCUSDT".into(),
            lo: 106,
            hi: 108,
            found_ms: 2_000
        }]
    );
    assert_eq!(g[0].ids(), 3);
    assert_eq!(t.pending(), 0);
    // раз в секунду, не чаще
    t.on_span(0, b"BTCUSDT", 120, 120, 3_000);
    assert!(t.scan(2_000 + CONFIRM_MS + 10, "trade00").is_empty());
}

#[test]
fn a_late_record_can_split_a_pending_range_and_symbols_are_independent() {
    let mut t = IdTracker::default();
    t.on_span(1, b"A", 1, 1, 0);
    t.on_span(2, b"B", 50, 50, 0);
    t.on_span(1, b"A", 11, 11, 10); // пропуск 2..=10
    t.on_span(2, b"B", 51, 51, 10);
    t.on_span(1, b"A", 5, 6, 20); // середина: остаются 2..=4 и 7..=10
    let g = t.scan(10 + CONFIRM_MS + 20, "trade01");
    assert_eq!(
        g.iter().map(|x| (x.lo, x.hi)).collect::<Vec<_>>(),
        vec![(2, 4), (7, 10)]
    );
    assert!(g.iter().all(|x| x.symbol == "A"));
}

#[test]
fn a_numbering_jump_and_a_pending_overflow_are_counted_not_tracked() {
    let mut t = IdTracker::default();
    t.on_span(0, b"X", 1, 1, 0);
    t.on_span(0, b"X", 10_000_000, 10_000_000, 1);
    assert_eq!(
        (t.pending(), t.jumps),
        (0, 1),
        "разрыв нумерации — не потеря"
    );
    let mut id = 10_000_000;
    for _ in 0..(MAX_PENDING_PER_SYMBOL + 3) {
        id += 2;
        t.on_span(0, b"X", id, id, 2);
    }
    assert_eq!(t.pending(), MAX_PENDING_PER_SYMBOL);
    assert_eq!(t.overflow, 3);
}

// ---- разбор страниц REST (образцы настоящих ответов, 16.11) ----

#[test]
fn binance_agg_trades_page_keeps_the_requested_range_and_asks_for_more_only_after_a_full_page() {
    let v = json!([
        {"a": 132586728, "p": "1.0463000", "q": "10", "f": 5, "l": 5, "T": 1794799135976i64, "m": true},
        {"a": 132586729, "p": "1.0464000", "q": "7", "f": 6, "l": 8, "T": 1794799153969i64, "m": false},
        {"a": 132586730, "p": "1.0465000", "q": "3", "f": 9, "l": 9, "T": 1794799259942i64, "m": false}
    ]);
    let p = parse_agg_page(&v, 132586729, 132586729).unwrap();
    assert_eq!(
        p.trades,
        vec![RestTrade {
            id: 132586729,
            t_ms: 1794799153969,
            p: "1.0464000".into(),
            q: "7".into(),
            f: 6,
            l: 8,
            m: false
        }]
    );
    assert_eq!(
        p.rest, None,
        "страница короче максимума — дальше номеров нет"
    );
    // полная страница, диапазон не закрыт — продолжать от следующего номера
    let full: Vec<_> = (0..1000)
        .map(|i| json!({"a": 100 + i, "p": "1", "q": "1", "f": i, "l": i, "T": 1, "m": true}))
        .collect();
    let p = parse_agg_page(&json!(full), 100, 5_000).unwrap();
    assert_eq!((p.trades.len(), p.rest), (1000, Some((1100, 5_000))));
    assert!(parse_agg_page(&json!({"code": -1121, "msg": "Invalid symbol."}), 1, 2).is_err());
}

#[test]
fn okx_gate_and_bingx_pages_parse_real_responses() {
    let okx = json!({"code": "0", "msg": "", "data": [
        {"instId": "BTC-USDT-SWAP", "tradeId": "3086834323", "px": "64831.5", "sz": "0.45", "side": "sell", "ts": "1794802151904", "source": "0"},
        {"instId": "BTC-USDT-SWAP", "tradeId": "3086834322", "px": "64831.5", "sz": "2.1", "side": "buy", "ts": "1794802151904", "source": "0"}]});
    let p = super::okx::parse_history_page(&okx, 3086834322, 3086834323).unwrap();
    assert_eq!(p.trades.len(), 2);
    assert!(
        p.trades[0].m && !p.trades[1].m,
        "sell — покупатель мейкер, как в записи ws"
    );
    assert_eq!(
        (p.trades[0].p.as_str(), p.trades[0].q.as_str()),
        ("64831.5", "0.45")
    );
    assert_eq!(p.rest, None);
    assert!(
        super::okx::parse_history_page(&json!({"code": "50011", "msg": "Rate limit"}), 1, 2)
            .is_err()
    );

    let gate = json!([
        {"id": 40019879, "contract": "AKE_USDT", "create_time": 1794802149.414, "create_time_ms": 1794802149.414, "size": "-3", "price": "0.0093552"},
        {"id": 40019878, "contract": "AKE_USDT", "create_time": 1794802145.414, "create_time_ms": 1794802145.414, "size": "2", "price": "0.0093473"}]);
    let p = super::gate::parse_trades_page(&gate, 40019878, 40019879).unwrap();
    assert_eq!(p.trades.len(), 2);
    assert_eq!(
        (p.trades[0].q.as_str(), p.trades[0].m, p.trades[0].t_ms),
        ("3", true, 1794802149414)
    );
    assert_eq!((p.trades[1].q.as_str(), p.trades[1].m), ("2", false));

    let bingx = json!({"code": 0, "msg": "", "data": [
        {"time": 1794802011356i64, "isBuyerMaker": false, "price": "64904.0", "qty": "0.0043", "quoteQty": "279.09", "fillId": "762054344", "ts": 1794802011356i64},
        {"time": 1794802011333i64, "isBuyerMaker": true, "price": "64903.9", "qty": "0.0003", "quoteQty": "19.47", "fillId": "762054343", "ts": 1794802011333i64},
        {"time": 1794802011533i64, "isBuyerMaker": true, "price": "64903.9", "qty": "0.0011", "quoteQty": "71.39", "fillId": "762054345", "ts": 1794802011533i64}]});
    let p = super::bingx::parse_trades_page(&bingx, 762054344, 762054345).unwrap();
    assert_eq!(
        p.trades.iter().map(|t| t.id).collect::<Vec<_>>(),
        vec![762054344, 762054345],
        "по возрастанию номера, диапазон соблюдён"
    );
    assert_eq!(p.rest, None, "дальше назад запросить нельзя");

    // страница «от новых к старым»: продолжать, если полная и самый старый выше нижней границы
    assert_eq!(next_range_desc(100, 100, Some(500), 400), Some((400, 499)));
    assert_eq!(next_range_desc(100, 100, Some(400), 400), None);
    assert_eq!(next_range_desc(99, 100, Some(500), 400), None);
    assert_eq!(next_range_desc(0, 100, None, 400), None);
}

// ---- поток дозапроса ----

fn read_backfill_file(root: &std::path::Path) -> Vec<String> {
    let mut out = Vec::new();
    for day in std::fs::read_dir(root).unwrap().flatten() {
        for f in std::fs::read_dir(day.path()).unwrap().flatten() {
            let name = f.file_name().to_string_lossy().into_owned();
            assert!(
                name.starts_with("backfill-binance-7"),
                "отдельный файл с пометкой: {name}"
            );
            let z = std::fs::read(f.path()).unwrap();
            // кадры дописываются по пачке: читать через границы кадров
            let text = String::from_utf8(zstd::stream::decode_all(&z[..]).unwrap()).unwrap();
            out.extend(text.lines().map(String::from));
        }
    }
    out
}

fn trade(id: i64) -> RestTrade {
    RestTrade {
        id,
        t_ms: 1_700_000_000_000 + id,
        p: "1.5".into(),
        q: "2".into(),
        f: id,
        l: id,
        m: id % 2 == 0,
    }
}

type FetchSync = dyn Fn(&str, i64, i64) -> Option<Result<TradePage, String>> + Sync;

fn run_work(
    reqs: Vec<GapReq>,
    fetch: &FetchSync,
    done: impl Fn(&BackfillStats) -> bool,
) -> (Arc<BackfillStats>, Vec<String>) {
    let dir = tempfile::tempdir().unwrap();
    let (tx, rx) = channel();
    let st = Arc::new(BackfillStats::default());
    let stop = Arc::new(AtomicBool::new(false));
    let mut out = OutFile::new(dir.path().to_path_buf(), "binance", 7);
    let cfg = WorkCfg {
        min_call_gap: Duration::from_millis(1),
        retry: vec![Duration::from_millis(30), Duration::from_millis(30)],
    };
    for r in reqs {
        tx.send(r).unwrap();
    }
    std::thread::scope(|s| {
        let (st2, stop2) = (Arc::clone(&st), Arc::clone(&stop));
        let h = s.spawn(move || work(fetch, &cfg, &rx, &st2, &mut out, &stop2));
        let t0 = Instant::now();
        while !done(st.as_ref()) && t0.elapsed() < Duration::from_secs(10) {
            std::thread::sleep(Duration::from_millis(20));
        }
        stop.store(true, Relaxed);
        h.join().unwrap();
    });
    let lines = read_backfill_file(dir.path());
    (st, lines)
}

fn gap(lo: i64, hi: i64) -> GapReq {
    GapReq {
        shard: "trade02".into(),
        symbol: "ETHUSDT".into(),
        lo,
        hi,
        found_ms: 1_700_000_000_000,
    }
}

#[test]
fn backfill_writes_rest_trades_to_its_own_marked_file_and_counts_what_is_still_lost() {
    use std::sync::atomic::Ordering::Relaxed as R;
    // у биржи есть номера 10, 11, 13; 12 и 14 нет — потеря
    let fetch = |_: &str, lo: i64, hi: i64| {
        assert_eq!((lo, hi), (10, 14));
        Some(Ok(TradePage {
            trades: vec![trade(10), trade(11), trade(13)],
            rest: None,
        }))
    };
    let (st, lines) = run_work(vec![gap(10, 14)], &fetch, |s| {
        s.ids_backfilled.load(R) + s.ids_lost.load(R) == 5
    });
    assert_eq!(st.ids_backfilled.load(R), 3);
    assert_eq!(st.ids_lost.load(R), 2);
    assert_eq!(st.trades_written.load(R), 3);
    assert_eq!(st.lost_between(0, i64::MAX / 2), 2);
    assert!(
        lines[0].starts_with("rh\t") && lines[0].contains("not websocket data"),
        "{}",
        lines[0]
    );
    let rt: Vec<&String> = lines.iter().filter(|l| l.starts_with("rt\t")).collect();
    assert_eq!(rt.len(), 3);
    let f: Vec<&str> = rt[0].split('\t').collect();
    assert_eq!(
        &f[2..],
        [
            "ETHUSDT",
            "10",
            "1700000000010",
            "1.5",
            "2",
            "10",
            "10",
            "1",
            "10",
            "14"
        ]
    );
    let rg = lines.iter().find(|l| l.starts_with("rg\t")).unwrap();
    let f: Vec<&str> = rg.split('\t').collect();
    assert_eq!(
        (f[2], f[3], f[4], f[6], f[7], f[9]),
        ("ETHUSDT", "10", "14", "3", "partial", "trade02")
    );
    let j = st.json(true, 0);
    assert_eq!(
        (
            j["ids_backfilled"].as_i64(),
            j["ids_lost"].as_i64(),
            j["open_ids"].as_i64()
        ),
        (Some(3), Some(2), Some(0))
    );
}

#[test]
fn backfill_pages_through_a_range_and_retries_a_rest_error() {
    use std::sync::atomic::Ordering::Relaxed as R;
    let calls = std::sync::atomic::AtomicUsize::new(0);
    let fetch = move |_: &str, lo: i64, hi: i64| {
        let n = calls.fetch_add(1, R);
        match n {
            0 => Some(Err("HTTP 429".to_string())),
            1 => Some(Ok(TradePage {
                trades: (lo..lo + 3).map(trade).collect(),
                rest: Some((lo + 3, hi)),
            })),
            _ => Some(Ok(TradePage {
                trades: (lo..=hi).map(trade).collect(),
                rest: None,
            })),
        }
    };
    let (st, lines) = run_work(vec![gap(100, 105)], &fetch, |s| {
        s.ids_backfilled.load(R) + s.ids_lost.load(R) == 6
    });
    assert_eq!((st.ids_backfilled.load(R), st.ids_lost.load(R)), (6, 0));
    assert_eq!(st.rest_errors.load(R), 1);
    assert_eq!(st.rest_requests.load(R), 3);
    assert!(lines
        .iter()
        .any(|l| l.starts_with("rg\t") && l.contains("\tok\t")));
}

#[test]
fn backfill_gives_up_after_the_retries_and_marks_unsupported_and_too_large_gaps_as_lost() {
    use std::sync::atomic::Ordering::Relaxed as R;
    let fetch = |sym: &str, _: i64, _: i64| {
        if sym == "NOSUPPORT" {
            None
        } else {
            Some(Err("HTTP 500".to_string()))
        }
    };
    let mut nosupport = gap(1, 4);
    nosupport.symbol = "NOSUPPORT".into();
    let reqs = vec![gap(1, 3), nosupport, gap(1, 200_000)];
    let (st, lines) = run_work(reqs, &fetch, |s| s.ids_lost.load(R) == 3 + 4 + 200_000);
    assert_eq!(st.ids_backfilled.load(R), 0);
    assert_eq!(st.ids_lost.load(R), 200_007);
    assert_eq!(st.rest_errors.load(R), 3, "первая попытка и два повтора");
    let status = |s: &str| {
        lines
            .iter()
            .any(|l| l.starts_with("rg\t") && l.contains(&format!("\t{s}\t")))
    };
    assert!(status("failed") && status("unsupported") && status("too_large"));
}

// ---- писатель → запрос дозапроса ----

fn bn_agg(a: i64) -> String {
    format!(
        r#"{{"stream":"btcusdt@aggTrade","data":{{"e":"aggTrade","E":1794418694042,"a":{a},"s":"BTCUSDT","p":"0.1742400","q":"410","nq":"410","f":{a},"l":{a},"T":1794418693917,"m":true,"st":1}}}}"#
    )
}

#[test]
fn the_writer_requests_a_backfill_only_for_a_trade_id_gap_that_stays_open_after_the_window() {
    use std::sync::atomic::AtomicI64;
    static NOW: AtomicI64 = AtomicI64::new(0);
    fn clock() -> i64 {
        NOW.load(Relaxed)
    }
    const DAY_NS: i64 = 86_400_000_000_000;
    let t0 = 20_000 * DAY_NS + 10_000_000_000;
    NOW.store(t0, Relaxed);
    let dir = tempfile::tempdir().unwrap();
    let metrics = ShardMetrics::new("trade00", 0, 64, 16);
    let (tx, rx) = super::ring::ring(&metrics.ring);
    let (stx, _srx) = super::sink::sink_channel(&metrics.sink_q);
    let cfg = super::writer::WriterCfg {
        root: dir.path().to_path_buf(),
        shard: "trade00".into(),
        run_id: 1,
        flush: Duration::from_millis(20),
        block_max: 4096,
        clock,
    };
    let mut w =
        super::writer::ShardWriter::new(cfg, Arc::new(Binance), Arc::clone(&metrics), stx, rx);
    let (btx, brx) = channel();
    let stats = Arc::new(BackfillStats::default());
    w.set_backfill(super::backfill::Backfill {
        tx: btx,
        stats: Arc::clone(&stats),
        seq: super::exchange::TradeSeq::A,
    });
    let push = |a: i64, at_ns: i64| {
        tx.push(super::ring::Item {
            rx_ns: t0 + at_ns,
            sender: 1,
            lane: 0,
            settled: true,
            payload: super::ring::Payload::Frame(bytes::Bytes::from(bn_agg(a))),
        })
        .unwrap();
    };
    let mut scratch = Vec::new();
    // 1, 2, 6 — пропуск 3..=5; 4 приходит позже с другой линии — остаются 3 и 5
    push(1, 1_000_000);
    push(2, 2_000_000);
    push(6, 3_000_000);
    push(4, 4_000_000);
    w.step(&mut scratch);
    NOW.store(t0 + 10_000_000_000, Relaxed);
    w.step(&mut scratch);
    assert!(brx.try_recv().is_err(), "окно подтверждения не прошло");
    NOW.store(t0 + 40_000_000_000, Relaxed);
    w.step(&mut scratch);
    let got: Vec<GapReq> = brx.try_iter().collect();
    assert_eq!(
        got.iter()
            .map(|g| (g.symbol.as_str(), g.lo, g.hi, g.shard.as_str()))
            .collect::<Vec<_>>(),
        vec![("BTCUSDT", 3, 3, "trade00"), ("BTCUSDT", 5, 5, "trade00")]
    );
    assert_eq!(stats.gaps_confirmed.load(Relaxed), 2);
    assert_eq!(stats.ids_confirmed.load(Relaxed), 2);
    // сделки остаются в файле ws как были, дозапрос в них не пишется
    assert_eq!(metrics.writer().records, 4);
}

#[test]
fn series_minutes_count_loss_only_from_confirmed_unrecovered_ids_when_backfill_is_on() {
    let dir = tempfile::tempdir().unwrap();
    let min = 60_000i64;
    let m = ShardMetrics::new("trade00", 0, 64, 16);
    // паузы в двух минутах: 101 — с потерей номеров, 102 — без (сделки дозапрошены или не терялись)
    m.lists().gap_spans.push_back((101 * min + 1_000, 3_500));
    m.lists().gap_spans.push_back((102 * min + 1_000, 4_000));
    let stats = Arc::new(BackfillStats::default());
    stats.lost(101 * min + 30_000, 8_081);
    let mut w = super::series::SeriesWriter::open(dir.path(), 100 * min + 5_000, "abc1234");
    w.set_backfill(Arc::clone(&stats));
    w.flush(std::slice::from_ref(&m), 104 * min + 10_000);
    let rows: Vec<serde_json::Value> = std::fs::read_to_string(w.path())
        .unwrap()
        .lines()
        .map(|l| serde_json::from_str::<serde_json::Value>(l).unwrap())
        .filter(|v| v.get("t").is_some())
        .collect();
    assert_eq!(rows.len(), 3);
    assert_eq!(
        (
            rows[0]["lost_ms"].as_i64(),
            rows[0]["pause_ms"].as_i64(),
            rows[0]["lost_ids"].as_i64()
        ),
        (Some(3_500), Some(3_500), Some(8_081))
    );
    assert_eq!(
        (
            rows[1]["lost_ms"].as_i64(),
            rows[1]["pause_ms"].as_i64(),
            rows[1]["lost_ids"].as_i64()
        ),
        (Some(0), Some(4_000), Some(0)),
        "пауза без потери номеров — не потеря"
    );
}
