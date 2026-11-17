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
use super::exchange::{next_range_desc, Exchange, PageReq, RestError, RestTrade, TradePage};
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
        // книга молчит 11 мин, но сделки по символу после неё шли — книга обязана была прийти: не подписан
        ("DEADUSDT", now - 660_000),
        // книга молчит 20 мин, сделок нет — тихий символ (акции и индексы при закрытом рынке): не красит
        ("QUIETUSDT", now - 1_200_000),
        // сделка была до последнего кадра книги — книга опередила её, молчание без новых сделок
        ("OLDTRADEUSDT", now - 700_000),
    ]);
    let tr = ShardMetrics::new("trade00", 0, 64, 16);
    for (sym, last) in [
        ("DEADUSDT", now - 5_000),
        ("OLDTRADEUSDT", now - 800_000),
        ("BTCUSDT", now - 1_000),
    ] {
        tr.writer()
            .seen
            .insert(sym.as_bytes().into(), (sym.to_lowercase(), last, 5));
    }
    let live = PoolHealth {
        last_ok_ms: now - 30_000,
        elig: Arc::new(vec![
            elig("BTCUSDT", 0),
            elig("THINUSDT", 0),
            elig("DEADUSDT", 0),
            elig("QUIETUSDT", 0),
            elig("OLDTRADEUSDT", 0),
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
    let v = build(&[m, tr], &ctx, now, &mut ProcSampler::default());
    let s = &v["symbols"];
    assert_eq!(s["book_window_s"], 600);
    assert_eq!(s["rest_checked"], 6, "новый символ ещё не проверяется");
    assert_eq!(s["not_subscribed"], 2);
    assert_eq!(s["not_subscribed_qv_usd"], 6_000_000.0);
    assert_eq!(s["not_subscribed_top"][0][0], "NOFRAMEUSDT");
    assert_eq!(s["not_subscribed_top"][1][0], "DEADUSDT");
    // «с данными за минуту» — прежнее число (книга или сделки за минуту), на красный не влияет
    assert_eq!(s["with_data_1m"], 2);
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

/// Первый запуск без `pool-state.json` (нет измеренной нагрузки): раскладка — прежняя, не по обороту; вид с измерением раскладывается, без измерения — прежний.
#[test]
fn spread_plans_keep_the_plain_layout_for_kinds_without_measured_load() {
    let exch = Binance;
    let (pool, _) = two_hot_pool();
    let qv: BTreeMap<String, f64> = pool
        .iter()
        .enumerate()
        .map(|(i, s)| (s.clone(), 1e6 * f64::from(u32::try_from(i + 1).unwrap())))
        .collect();
    let plans = exch.plan(&pool, &kinds(), 20);
    // оборот есть, измерений нет: прежняя раскладка (алфавитная), а не по обороту
    let w = Weights::new(Rates::default(), &qv);
    assert!(w.informative() && !w.measured("book"));
    assert_eq!(spread_plans(&exch, &plans, &pool, &kinds(), 20, &w), plans);
    // измерена только книга: сделки остаются прежними, книга — по нагрузке
    let (_, w) = two_hot_pool();
    let out = spread_plans(&exch, &plans, &pool, &kinds(), 20, &w);
    let streams = |ps: &[super::exchange::ShardPlan], p: &str| {
        ps.iter()
            .filter(|s| s.name.starts_with(p))
            .map(|s| s.streams.clone())
            .collect::<Vec<_>>()
    };
    assert_eq!(streams(&out, "trade"), streams(&plans, "trade"));
    assert_ne!(streams(&out, "book"), streams(&plans, "book"));
}

/// Разовый прогон на srv: оценка нагрузки шардов BingX (`Layout::loads()` по измеренным весам `pool-state.json`) при трёх раскладках — прежней (алфавит, до 16.11 07:02), по обороту
/// (первый запуск `3835be7b` без состояния) и по измеренной нагрузке (следующий запуск). Запуск (srv, процесс не нужен):
/// `BX_ROOT=/data/collect/bingx/data BX_POOL=pool-2026-11-16-1794812546.json cargo test --release bingx_layout_loads -- --ignored --nocapture`
#[test]
#[ignore = "прогон на srv по данным BingX: env BX_ROOT, BX_POOL"]
fn bingx_layout_loads_before_after_report() {
    use super::bingx::Bingx;
    let (Ok(root), Ok(pool_file)) = (std::env::var("BX_ROOT"), std::env::var("BX_POOL")) else {
        return;
    };
    let root = std::path::PathBuf::from(root);
    let pf: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(root.join(pool_file)).unwrap()).unwrap();
    let pool: Vec<String> = pf["pool"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    let qv: BTreeMap<String, f64> = pf["qv"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(k, v)| (k.clone(), v.as_f64().unwrap()))
        .collect();
    let saved = SavedState::load(&root).unwrap();
    let exch = Bingx::default();
    let kinds = kinds();
    let measured = Weights::new(saved.rates.clone(), &qv);
    // раскладка первого запуска `3835be7b`: без измерений вес символа — оборот × 1e-6 у всех видов; сейчас вид без измерений остаётся прежним, поэтому тот же вес подаётся как «измеренный»
    let mut as_rates = Rates::default();
    for kind in &kinds {
        for (sym, v) in &qv {
            as_rates.set(kind, &sym.to_lowercase(), v * 1e-6);
        }
    }
    let turnover = Weights::new(as_rates, &qv);
    let plain = exch.plan(&pool, &kinds, 0);
    let by_turnover = spread_plans(&exch, &plain, &pool, &kinds, 0, &turnover);
    let by_rates = spread_plans(&exch, &plain, &pool, &kinds, 0, &measured);
    println!(
        "пул {} символов; измерено: книга {} сделки {} mark {}",
        pool.len(),
        measured.measured("book"),
        measured.measured("trade"),
        measured.measured("mark")
    );
    for (title, plans) in [
        ("алфавит (до 07:02)", &plain),
        (
            "по обороту: так раскладывал первый запуск 3835be7b",
            &by_turnover,
        ),
        ("по измеренной нагрузке", &by_rates),
    ] {
        let mut layout = Layout::new(&exch, plans, &pool, &kinds, 0);
        layout.set_weights(measured.clone());
        let loads = layout.loads();
        println!("== {title}");
        for kind in ["book", "trade", "mark"] {
            let rows: Vec<String> = layout
                .slots()
                .iter()
                .zip(&loads)
                .filter(|(s, _)| s.kind == kind)
                .map(|(s, l)| format!("{} {} пот. {:.1}", s.name, s.streams, l))
                .collect();
            let ls: Vec<f64> = layout
                .slots()
                .iter()
                .zip(&loads)
                .filter(|(s, _)| s.kind == kind)
                .map(|(_, l)| *l)
                .collect();
            let (mx, mn) = (
                ls.iter().copied().fold(f64::MIN, f64::max),
                ls.iter().copied().fold(f64::MAX, f64::min),
            );
            println!("  {kind}: {} | макс/мин {:.2}", rows.join("; "), mx / mn);
        }
    }
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
        StormGate::Probe,
        "пробная"
    );
    assert_eq!(storm::gate(Some(&s), &probe, now), StormGate::Hold);
    assert_eq!(
        storm::gate(Some(&s), &probe, now + Duration::from_secs(10)),
        StormGate::Hold
    );
}

/// Проверка 16.11 (пробная замена): потолок замен не должен сжигать пробную замену режима — иначе шард без замены ещё 300 с, а потолок её так и не пропустил.
#[test]
fn the_replace_cap_does_not_burn_the_storm_probe() {
    use storm::{admit, Admit};
    let now = Instant::now();
    let s = Storm::default();
    s.evaluate(5, 6, 0);
    let probe = Cap::new(1, storm::PROBE_PERIOD);
    let replace = Cap::new(1, Duration::from_secs(300));
    assert!(replace.take(now), "потолок замен исчерпан");
    // потолок отказал: пробная возвращена и достаётся следующей попытке
    assert_eq!(admit(false, Some(&s), &probe, &replace, now), Admit::Capped);
    assert_eq!(admit(false, Some(&s), &probe, &replace, now), Admit::Capped);
    // место в потолке освободилось: проходит пробная замена, следующая — держится
    let later = now + Duration::from_secs(301);
    assert_eq!(
        admit(false, Some(&s), &probe, &replace, later),
        Admit::Go(StormGate::Probe)
    );
    assert_eq!(admit(false, Some(&s), &probe, &replace, later), Admit::Hold);
    // возрастная пересменка идёт всегда и ничего не занимает
    let (probe, replace) = (
        Cap::new(1, storm::PROBE_PERIOD),
        Cap::new(1, Duration::from_secs(300)),
    );
    assert_eq!(
        admit(true, Some(&s), &probe, &replace, later),
        Admit::Go(StormGate::Open)
    );
    assert!(probe.take(later) && replace.take(later));
    // режим выключен: пробная не нужна, потолок решает сам
    let off = Storm::default();
    let (probe, replace) = (
        Cap::new(1, storm::PROBE_PERIOD),
        Cap::new(1, Duration::from_secs(300)),
    );
    assert_eq!(
        admit(false, Some(&off), &probe, &replace, later),
        Admit::Go(StormGate::Open)
    );
    assert_eq!(
        admit(false, Some(&off), &probe, &replace, later),
        Admit::Capped
    );
    assert!(probe.take(later), "пробная не тронута");
}

/// Шард испорчен, когда стоят данные (пауза) или ни одна его линия не несёт поток; испорченная линия при здоровом партнёре шард не портит (Б1: иначе режим держал сам себя).
#[test]
fn a_shard_is_impaired_only_with_a_data_pause_or_no_carrying_line() {
    let m = ShardMetrics::new("book00", 0, 64, 16);
    assert!(
        storm::impaired(&m),
        "нет ни одной линии — шард без соединения"
    );
    m.lane_carry[0].store(true, Relaxed);
    m.lane_carry[1].store(true, Relaxed);
    assert!(!storm::impaired(&m));
    // вторая линия испорчена или закрыта, первая несёт: шард не испорчен
    m.lane_carry[1].store(false, Relaxed);
    assert!(!storm::impaired(&m));
    // обе не несут
    m.lane_carry[0].store(false, Relaxed);
    assert!(storm::impaired(&m));
    // пауза данных портит и при несущих линиях
    m.lane_carry[0].store(true, Relaxed);
    m.in_gap.store(true, Relaxed);
    assert!(storm::impaired(&m));
}

/// TK-582 (остановки Binance 16.11 21:18–22:21): шард, у которого ни одна линия не несёт поток, тёмный даже при открытых соединениях; режим «стоянка» и потолок замен
/// такой шард не держат (у них держать нечем: старая линия мертва), потолки при этом не занимаются; со светлой линией решает прежний `admit`.
#[test]
fn a_dark_shard_is_admitted_through_the_storm_hold_and_the_replace_cap() {
    use storm::{admit_dark, Admit};
    let now = Instant::now();
    let s = Storm::default();
    s.evaluate(5, 6, 0);
    let probe = Cap::new(1, storm::PROBE_PERIOD);
    let replace = Cap::new(1, Duration::from_secs(300));
    assert!(
        probe.take(now) && replace.take(now),
        "пробная и потолок замен исчерпаны"
    );
    assert_eq!(
        admit_dark(false, false, Some(&s), &probe, &replace, now),
        Admit::Hold,
        "светлый шард: режим держит старую линию"
    );
    assert_eq!(
        admit_dark(true, false, Some(&s), &probe, &replace, now),
        Admit::Go(StormGate::Open),
        "тёмный шард идёт через режим и потолок"
    );
    assert!(
        probe.wait(now) > Duration::ZERO && replace.wait(now) > Duration::ZERO,
        "тёмная замена ничего не заняла в потолках"
    );
}

#[test]
fn a_shard_with_open_connections_but_no_carrying_line_is_dark() {
    let m = ShardMetrics::new("book00", 0, 64, 16);
    assert!(storm::dark(&m), "без соединений");
    m.connected.store(2, Relaxed);
    assert!(storm::dark(&m), "соединения открыты, обе линии испорчены");
    m.lane_carry[1].store(true, Relaxed);
    assert!(!storm::dark(&m), "одна линия несёт — шард светлый");
    m.lane_carry[1].store(false, Relaxed);
    m.in_gap.store(true, Relaxed);
    assert!(
        storm::dark(&m),
        "пауза данных сама по себе не делает шард светлым"
    );
}

/// Ожидание потолка отбраковок не держит тёмный шард: сон кончается, как только ни одна линия не несёт поток; у светлого шарда ждёт весь срок.
#[test]
fn the_reject_wait_ends_when_the_shard_turns_dark_and_runs_in_full_otherwise() {
    use super::shard::sleep_unless_dark;
    use std::sync::atomic::AtomicBool;
    let stop = AtomicBool::new(false);
    let m = ShardMetrics::new("book00", 0, 64, 16);
    let t0 = Instant::now();
    sleep_unless_dark(&stop, &m, Duration::from_secs(30));
    assert!(t0.elapsed() < Duration::from_secs(1), "тёмный шард не ждёт");
    m.lane_carry[0].store(true, Relaxed);
    let t1 = Instant::now();
    sleep_unless_dark(&stop, &m, Duration::from_millis(300));
    assert!(
        t1.elapsed() >= Duration::from_millis(290),
        "светлый шард ждёт весь срок"
    );
    stop.store(true, Relaxed);
    let t2 = Instant::now();
    sleep_unless_dark(&stop, &m, Duration::from_secs(30));
    assert!(
        t2.elapsed() < Duration::from_secs(1),
        "остановка прерывает сон"
    );
}

/// Воспроизведение Б1 (проверка 16.11, прогон по событиям 14–16.11, Gate 15.11 15:28): у каждого из 9 шардов одна линия испорчена (`ping_degraded`), вторая здорова. Режим держит замены
/// испорченных линий; по прежнему правилу («испорчена хоть одна линия») все 9 шардов остаются «испорченными», пока линии не закрыты, — режим не выключается. По новому правилу шард
/// испорчен, только если у него нет здоровой линии: режим выключается через минуту, хотя испорченные линии не заменены.
#[test]
fn storm_does_not_hold_itself_when_each_shard_keeps_one_healthy_line() {
    let shards: Vec<_> = (0..9)
        .map(|i| ShardMetrics::new(&format!("book{i:02}"), 0, 64, 16))
        .collect();
    // линия 0 испорчена (открыта, не несёт), линия 1 здорова (несёт)
    for m in &shards {
        m.lane_carry[0].store(false, Relaxed);
        m.lane_carry[1].store(true, Relaxed);
    }
    // прежнее правило: любая испорченная открытая линия портит шард
    let old_count = shards.len();
    let new_count = shards.iter().filter(|m| storm::impaired(m)).count();
    assert_eq!(
        new_count, 0,
        "по новому правилу ни одного: партнёр несёт поток"
    );
    // всплеск: у всех шардов обе линии не несли — режим включён; затем здоровая линия вернулась, испорченные не заменены
    let s = Storm::default();
    s.evaluate(9, 9, 0);
    assert!(s.is_on());
    s.evaluate(new_count, 9, 5_000);
    assert!(s.is_on(), "минуты ещё не прошло");
    s.evaluate(new_count, 9, 70_000);
    assert!(!s.is_on(), "режим выключился");
    // по прежнему правилу число испорченных не падает, пока линии не закрыты, — режим держал бы сам себя
    let s_old = Storm::default();
    s_old.evaluate(9, 9, 0);
    s_old.evaluate(old_count, 9, 70_000);
    assert!(s_old.is_on());
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

/// P3: у секундных правил (OKX и BingX 2/с, Gate 5/с) заполнение и короткое ожидание — норма; тревога — подключение ждёт места ≥ 30 с. У окон от 60 с — как прежде.
#[test]
fn budget_alarm_for_a_one_second_rule_needs_a_wait_of_thirty_seconds() {
    use super::budget::BudgetSnap;
    let snap =
        |window_s: u64, used: usize, limit: usize, waiting: usize, max_wait_s: u64| BudgetSnap {
            limit,
            window_s,
            used,
            urgent_reserve: 0,
            waiting,
            max_wait_s,
            waits_total: 1,
            wait_s_total: 0,
        };
    // OKX: два подключения в одну секунду — 100 % лимита, третье ждёт 5 с (старт процесса) — не тревога
    assert!(!snap(1, 2, 2, 0, 0).alarm());
    assert!(!snap(1, 2, 2, 1, 5).alarm());
    assert!(
        !snap(1, 5, 5, 3, 29).alarm(),
        "Gate: 29 с ожидания — ещё нет"
    );
    assert!(snap(1, 2, 2, 1, 30).alarm());
    // Binance и Bybit (5 мин): 90 % или ожидание — как раньше
    assert!(snap(300, 252, 280, 0, 0).alarm());
    assert!(!snap(300, 251, 280, 0, 0).alarm());
    assert!(snap(300, 10, 280, 1, 0).alarm());
    // живой счётчик ожидания: секунды самого давнего ожидающего
    let b = Arc::new(Budget::new(1, Duration::from_secs(300)));
    assert!(b.try_take(Instant::now()).is_none());
    let stop = Arc::new(AtomicBool::new(false));
    let (b2, stop2) = (Arc::clone(&b), Arc::clone(&stop));
    let h = std::thread::spawn(move || b2.acquire(&[&stop2], &|| false));
    std::thread::sleep(Duration::from_millis(150));
    let later = Instant::now() + Duration::from_secs(31);
    assert!(b.snapshot(later).max_wait_s >= 30);
    stop.store(true, Relaxed);
    assert!(h.join().unwrap().is_none());
    assert_eq!(b.snapshot(Instant::now()).max_wait_s, 0);
}

// ---- пропуски номеров сделок ----

#[test]
fn reordered_ids_are_not_a_gap_but_a_missing_range_is_confirmed_after_the_window() {
    let mut t = IdTracker::default();
    t.on_span(0, b"BTCUSDT", 100, 100, 0, 0);
    t.on_span(0, b"BTCUSDT", 103, 103, 10, 10);
    assert_eq!(t.pending(), 1);
    // 101 и 102 пришли позже (другая линия): пропуск закрыт по частям
    t.on_span(0, b"BTCUSDT", 102, 102, 20, 20);
    t.on_span(0, b"BTCUSDT", 101, 101, 30, 30);
    assert_eq!(t.pending(), 0);
    assert_eq!(t.reordered, 2);
    assert!(t.scan(CONFIRM_MS * 10, "trade00").is_empty());
    // настоящий пропуск 106..108
    t.on_span(0, b"BTCUSDT", 104, 105, 1_000, 1_000);
    t.on_span(0, b"BTCUSDT", 109, 110, 2_000, 2_000);
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
            found_ms: 2_000,
            // последняя сделка перед пропуском (номер 105) принята в 1 000, первая после (109) — в 2 000: минута потери — по первому
            gap_ms: 1_000,
            t_before_ms: 1_000,
            t_after_ms: 2_000,
        }]
    );
    assert_eq!(g[0].ids(), 3);
    assert_eq!(t.pending(), 0);
    // раз в секунду, не чаще
    t.on_span(0, b"BTCUSDT", 120, 120, 3_000, 3_000);
    assert!(t.scan(2_000 + CONFIRM_MS + 10, "trade00").is_empty());
}

#[test]
fn a_late_record_can_split_a_pending_range_and_symbols_are_independent() {
    let mut t = IdTracker::default();
    t.on_span(1, b"A", 1, 1, 0, 0);
    t.on_span(2, b"B", 50, 50, 0, 0);
    t.on_span(1, b"A", 11, 11, 10, 10); // пропуск 2..=10
    t.on_span(2, b"B", 51, 51, 10, 10);
    t.on_span(1, b"A", 5, 6, 20, 20); // середина: остаются 2..=4 и 7..=10
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
    t.on_span(0, b"X", 1, 1, 0, 0);
    t.on_span(0, b"X", 10_000_000, 10_000_000, 1, 1);
    assert_eq!(
        (t.pending(), t.jumps),
        (0, 1),
        "разрыв нумерации — не потеря"
    );
    let mut id = 10_000_000;
    for _ in 0..(MAX_PENDING_PER_SYMBOL + 3) {
        id += 2;
        t.on_span(0, b"X", id, id, 2, 2);
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

type FetchSync = dyn Fn(&PageReq<'_>) -> Option<Result<TradePage, RestError>> + Sync;

fn run_work(
    reqs: Vec<GapReq>,
    fetch: &FetchSync,
    done: impl Fn(&BackfillStats) -> bool,
) -> (Arc<BackfillStats>, Vec<String>) {
    run_work_w(reqs, fetch, done, None)
}

fn run_work_w(
    reqs: Vec<GapReq>,
    fetch: &FetchSync,
    done: impl Fn(&BackfillStats) -> bool,
    window: Option<i64>,
) -> (Arc<BackfillStats>, Vec<String>) {
    let dir = tempfile::tempdir().unwrap();
    let (tx, rx) = channel();
    let st = Arc::new(BackfillStats::default());
    let stop = Arc::new(AtomicBool::new(false));
    let mut out = OutFile::new(dir.path().to_path_buf(), "binance", 7);
    let cfg = WorkCfg {
        min_call_gap: Duration::from_millis(1),
        retry: vec![Duration::from_millis(30), Duration::from_millis(30)],
        window,
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
        gap_ms: 1_699_999_990_000,
        t_before_ms: 1_699_999_990_000,
        t_after_ms: 1_700_000_000_000,
    }
}

/// Происшествие 16.11 12:50 (BingX, REST отдаёт 1 000 последних сделок): очередь шла по порядку подтверждения, горячие символы (ETH, HYPE) ждали 3–6 мин и теряли номера (4 084 из 46 049).
/// С окном очередь идёт по сроку выхода пропуска из окна: горячий символ (много номеров за короткий пропуск) — раньше спокойного, хотя подтверждён позже; без окна — по порядку, как раньше.
#[test]
fn a_windowed_exchange_fetches_the_gap_that_leaves_the_rest_window_first() {
    use std::sync::atomic::Ordering::Relaxed as R;
    let mk = |sym: &str, lo: i64, hi: i64, gap_s: i64| GapReq {
        symbol: sym.into(),
        lo,
        hi,
        gap_ms: 1_700_000_000_000 - gap_s * 1000,
        ..gap(lo, hi)
    };
    // «CALM»: 100 номеров за 100 с (срок найден + 900 с), «HOT»: 100 номеров за 5 с (+ 45 с), «HUGE»: пропуск не меньше окна — срок уже наступил
    let reqs = || {
        vec![
            mk("CALM", 1_000, 1_099, 100),
            mk("HOT", 2_000, 2_099, 5),
            mk("HUGE", 3_000, 4_199, 100),
        ]
    };
    assert_eq!(reqs()[1].deadline_ms(1000), 1_700_000_000_000 + 45_000);
    assert_eq!(reqs()[0].deadline_ms(1000), 1_700_000_000_000 + 900_000);
    assert_eq!(reqs()[2].deadline_ms(1000), 1_700_000_000_000);
    for (window, want) in [
        (Some(1000), ["HUGE", "HOT", "CALM"]),
        (None, ["CALM", "HOT", "HUGE"]),
    ] {
        let order = Arc::new(std::sync::Mutex::new(Vec::<String>::new()));
        let seen = Arc::clone(&order);
        let fetch = move |q: &PageReq<'_>| {
            seen.lock().unwrap().push(q.symbol.to_string());
            Some(Ok(TradePage::new((q.lo..=q.hi).map(trade).collect(), None)))
        };
        let (st, _) = run_work_w(
            reqs(),
            &fetch,
            |s| s.ids_backfilled.load(R) == 100 + 100 + 1_200,
            window,
        );
        assert_eq!(st.ids_lost.load(R), 0);
        assert_eq!(*order.lock().unwrap(), want, "окно {window:?}");
    }
}

/// Подтверждение пропуска у биржи свой срок (`Exchange::gap_confirm_ms`): BingX — 2 с вместо 10 с; трекер по умолчанию — `CONFIRM_MS`.
#[test]
fn the_id_tracker_confirms_a_gap_after_the_exchange_confirm_time() {
    let mut t = IdTracker::with_confirm(2_000);
    t.on_span(1, b"ETH", 10, 10, 1_000, 1_000);
    t.on_span(1, b"ETH", 14, 14, 2_000, 2_000);
    assert!(
        t.scan(3_999, "trade00").is_empty(),
        "2 с с прихода ещё не прошли"
    );
    let g = t.scan(5_000, "trade00");
    assert_eq!((g.len(), g[0].lo, g[0].hi), (1, 11, 13));
    // тот же пропуск у трекера по умолчанию ждёт `CONFIRM_MS`
    let mut d = IdTracker::default();
    d.on_span(1, b"ETH", 10, 10, 1_000, 1_000);
    d.on_span(1, b"ETH", 14, 14, 2_000, 2_000);
    assert!(d.scan(2_000 + CONFIRM_MS - 1_001, "trade00").is_empty());
    assert_eq!(d.scan(2_000 + CONFIRM_MS, "trade00").len(), 1);
    assert_eq!(super::bingx::Bingx::default().gap_confirm_ms(), 2_000);
    assert_eq!(super::bingx::Bingx::default().trade_window(), Some(1000));
    assert_eq!(Binance.gap_confirm_ms(), CONFIRM_MS);
    assert_eq!(Binance.trade_window(), None);
}

#[test]
fn backfill_writes_rest_trades_to_its_own_marked_file_and_counts_what_is_still_lost() {
    use std::sync::atomic::Ordering::Relaxed as R;
    // у биржи есть номера 10, 11, 13; 12 и 14 нет — потеря
    let fetch = |q: &PageReq<'_>| {
        assert_eq!((q.lo, q.hi), (10, 14));
        Some(Ok(TradePage::new(
            vec![trade(10), trade(11), trade(13)],
            None,
        )))
    };
    let (st, lines) = run_work(vec![gap(10, 14)], &fetch, |s| {
        s.ids_backfilled.load(R) + s.ids_lost.load(R) == 5
    });
    assert_eq!(st.ids_backfilled.load(R), 3);
    assert_eq!(st.ids_lost.load(R), 2);
    assert_eq!(st.trades_written.load(R), 3);
    assert_eq!(st.lost_between(0, i64::MAX / 2), 2);
    // потеря — в минуте пропуска (`gap_ms`), а не в момент итога дозапроса
    assert_eq!(
        st.lost_between(1_699_999_990_000, 1_699_999_990_001),
        2,
        "время пропуска"
    );
    assert_eq!(st.lost_between(1_700_000_000_000, i64::MAX / 2), 0);
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
    let fetch = move |q: &PageReq<'_>| {
        let (lo, hi) = (q.lo, q.hi);
        let n = calls.fetch_add(1, R);
        match n {
            0 => Some(Err(RestError::from("HTTP 500"))),
            1 => Some(Ok(TradePage::new(
                (lo..lo + 3).map(trade).collect(),
                Some((lo + 3, hi)),
            ))),
            _ => Some(Ok(TradePage::new((lo..=hi).map(trade).collect(), None))),
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
    let fetch = |q: &PageReq<'_>| {
        if q.symbol == "NOSUPPORT" {
            None
        } else {
            Some(Err(RestError::from("HTTP 500")))
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
    let book = ShardMetrics::new("book00", 0, 64, 16);
    // паузы шарда сделок в двух минутах: 101 — с потерей номеров, 102 — без (сделки дозапрошены или не терялись); пауза шарда книги в минуте 103
    m.lists().gap_spans.push_back((101 * min + 1_000, 3_500));
    m.lists().gap_spans.push_back((102 * min + 1_000, 4_000));
    book.lists().gap_spans.push_back((103 * min + 1_000, 2_000));
    let stats = Arc::new(BackfillStats::default());
    // время пропуска, не итога дозапроса: потеря в минуте 101 даже если итог пришёл позже
    stats.lost(101 * min + 30_000, 8_081);
    let mut w = super::series::SeriesWriter::open(dir.path(), 100 * min + 5_000, "abc1234");
    w.set_backfill(Arc::clone(&stats));
    w.flush(&[Arc::clone(&m), Arc::clone(&book)], 104 * min + 10_000);
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
        (Some(0), Some(3_500), Some(8_081)),
        "пауза шарда сделок — не потеря, потеря сделок видна по номерам (минута пропуска)"
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
    // Б2: пауза шарда книги дозапросом не восстанавливается — потеря и у бирж с дозапросом
    assert_eq!(
        (
            rows[2]["lost_ms"].as_i64(),
            rows[2]["pause_ms"].as_i64(),
            rows[2]["lost_ids"].as_i64()
        ),
        (Some(2_000), Some(2_000), Some(0))
    );
}

// ---- проверка 16.11: П2 (нет на бирже), П5 (отказ по частоте), страницы по времени ----

fn gate_item(id: i64, t_s: f64) -> serde_json::Value {
    json!({"id": id, "contract": "AKE_USDT", "create_time": t_s, "create_time_ms": t_s, "size": "1", "price": "0.01"})
}

/// Номера Gate не сплошные (16.11: 1 000 сделок на 1 004 номера у AKE_USDT): то, чего нет в странице, покрывающей пропуск с обеих сторон, — «нет на бирже», не потеря.
#[test]
fn ids_missing_inside_a_page_that_covers_the_gap_on_both_sides_are_absent_at_the_exchange_not_lost()
{
    use std::sync::atomic::Ordering::Relaxed as R;
    // у биржи есть 98, 99, 103, 104, 108, 109; пропуск у нас 100..=107
    let raw = json!([
        gate_item(109, 1_000.9),
        gate_item(108, 1_000.8),
        gate_item(104, 1_000.4),
        gate_item(103, 1_000.3),
        gate_item(99, 999.9),
        gate_item(98, 999.8)
    ]);
    let p = super::gate::parse_trades_page(&raw, 100, 107).unwrap();
    assert_eq!(
        p.trades.iter().map(|t| t.id).collect::<Vec<_>>(),
        vec![104, 103]
    );
    assert_eq!(p.covered, Some((100, 107)), "страница шире пропуска");
    assert_eq!(p.rest, None);
    let fetch = {
        let raw = raw.clone();
        move |q: &PageReq<'_>| {
            Some(super::gate::parse_trades_page(&raw, q.lo, q.hi).map_err(RestError::from))
        }
    };
    let (st, lines) = run_work(vec![gap(100, 107)], &fetch, |s| {
        s.ids_backfilled.load(R) + s.ids_absent.load(R) + s.ids_lost.load(R) == 8
    });
    assert_eq!(
        (
            st.ids_backfilled.load(R),
            st.ids_absent.load(R),
            st.ids_lost.load(R)
        ),
        (2, 6, 0)
    );
    let rg = lines.iter().find(|l| l.starts_with("rg\t")).unwrap();
    let f: Vec<&str> = rg.split('\t').collect();
    assert_eq!((f[6], f[7], f[10]), ("2", "absent", "6"), "{rg}");
    let j = st.json(true, 0);
    assert_eq!(
        (j["ids_absent"].as_i64(), j["ids_lost"].as_i64()),
        (Some(6), Some(0))
    );
    // страница не доходит до пропуска сверху (самый новый номер ниже `hi`): непокрытое — потеря
    let short = json!([
        gate_item(104, 1_000.4),
        gate_item(103, 1_000.3),
        gate_item(99, 999.9)
    ]);
    let p = super::gate::parse_trades_page(&short, 100, 107).unwrap();
    assert_eq!(p.covered, Some((100, 104)));
}

/// Gate: страница полная — продолжать ниже самого старого номера, `to` следующего запроса — его время; секунда с > 1 000 сделок выше пропуска не зацикливает запрос.
#[test]
fn gate_time_pages_continue_below_the_oldest_id_and_never_loop_on_a_crowded_second() {
    let full = |top: i64, t0: f64| -> serde_json::Value {
        json!((0..1000)
            .map(|i| gate_item(top - i, t0 - f64::from(i32::try_from(i).unwrap()) / 1000.0))
            .collect::<Vec<_>>())
    };
    // первый запрос: номера 5 000..=4 001 (все выше нижней границы 3 000 и ниже верхней 5 000+)
    let p = super::gate::parse_trades_page(&full(5_000, 2_000.0), 3_000, 5_000).unwrap();
    assert_eq!(p.trades.len(), 1000);
    assert_eq!(p.rest, Some((3_000, 4_000)));
    assert_eq!(p.covered, Some((4_001, 5_000)));
    assert_eq!(
        p.next_to_ms,
        Some(1_999_001),
        "время самого старого номера страницы (4 001), мс"
    );
    // следующая страница: пересечение (та же секунда) отсекается по новой верхней границе 4 000
    let p2 = super::gate::parse_trades_page(&full(4_005, 1_999.0), 3_000, 4_000).unwrap();
    assert_eq!(p2.trades.first().map(|t| t.id), Some(4_000));
    assert_eq!(p2.rest, Some((3_000, 3_005)));
    assert_eq!(p2.covered, Some((3_006, 4_000)));
    // все 1 000 номеров страницы выше `hi`: сдвига вниз нет — конец (иначе запрос зациклился бы)
    let p3 = super::gate::parse_trades_page(&full(9_000, 2_000.0), 3_000, 4_000).unwrap();
    assert_eq!((p3.trades.len(), p3.rest, p3.covered), (0, None, None));
}

#[test]
fn okx_binance_and_bingx_pages_report_what_they_cover() {
    // Binance: `fromId = lo` — всё от `lo` до последнего номера страницы
    let v = json!([
        {"a": 10, "p": "1", "q": "1", "f": 1, "l": 1, "T": 1, "m": true},
        {"a": 12, "p": "1", "q": "1", "f": 2, "l": 2, "T": 2, "m": true}
    ]);
    let p = parse_agg_page(&v, 10, 14).unwrap();
    assert_eq!(
        p.covered,
        Some((10, 12)),
        "11 у биржи нет; 13–14 — не известно"
    );
    // OKX: `after = hi + 1` — от самого старого номера страницы до `hi`
    let okx = json!({"code": "0", "msg": "", "data": [
        {"instId": "X", "tradeId": "21", "px": "1", "sz": "1", "side": "buy", "ts": "1", "source": "0"},
        {"instId": "X", "tradeId": "18", "px": "1", "sz": "1", "side": "buy", "ts": "1", "source": "0"}]});
    let p = super::okx::parse_history_page(&okx, 15, 22).unwrap();
    assert_eq!(
        p.covered,
        Some((18, 22)),
        "сверху до `hi` — у биржи нет; ниже 18 — не известно"
    );
    // BingX: окно последних сделок — только между самым старым и самым новым номером окна
    let bingx = json!({"code": 0, "msg": "", "data": [
        {"time": 3, "isBuyerMaker": false, "price": "1", "qty": "1", "fillId": "30", "ts": 3},
        {"time": 1, "isBuyerMaker": false, "price": "1", "qty": "1", "fillId": "27", "ts": 1}]});
    let p = super::bingx::parse_trades_page(&bingx, 20, 40).unwrap();
    assert_eq!(p.covered, Some((27, 30)));
}

/// П5: 429/418 (OKX `50011`) ставят на паузу весь поток дозапроса: ни этот пропуск, ни другие не запрашиваются, пока не пройдёт срок; попытка задачи не тратится; счётчик в health.
#[test]
fn a_rate_limit_answer_pauses_the_whole_backfill_thread_and_does_not_spend_an_attempt() {
    use std::sync::atomic::Ordering::Relaxed as R;
    let calls = Arc::new(std::sync::Mutex::new(Vec::<(Instant, i64)>::new()));
    let calls2 = Arc::clone(&calls);
    let fetch = move |q: &PageReq<'_>| {
        let mut c = calls2.lock().unwrap();
        c.push((Instant::now(), q.lo));
        if c.len() == 1 {
            return Some(Err(RestError::limited(
                "HTTP 429".to_string(),
                Some(Duration::from_millis(600)),
            )));
        }
        Some(Ok(TradePage::new((q.lo..=q.hi).map(trade).collect(), None)))
    };
    // два пропуска в очереди: второй не должен уйти в REST внутри паузы
    let (st, _) = run_work(vec![gap(10, 12), gap(20, 22)], &fetch, |s| {
        s.ids_backfilled.load(R) == 6
    });
    let c = calls.lock().unwrap();
    assert_eq!(c.len(), 3, "429, затем оба пропуска по разу: {:?}", c.len());
    assert!(
        c[1].0.duration_since(c[0].0) >= Duration::from_millis(600),
        "после 429 запросов нет, пока не прошёл срок: {:?}",
        c[1].0.duration_since(c[0].0)
    );
    assert_eq!((st.rate_limited.load(R), st.ids_lost.load(R)), (1, 0));
    assert_eq!(st.rest_errors.load(R), 1);
    assert_eq!(st.json(true, 0)["rate_limited"], 1);
}

#[test]
fn rate_limit_statuses_and_okx_code_are_recognised() {
    assert!(super::pool::is_rate_limit_status(429) && super::pool::is_rate_limit_status(418));
    assert!(!super::pool::is_rate_limit_status(500) && !super::pool::is_rate_limit_status(403));
    let e =
        super::okx::page_from_response(&json!({"code": "50011", "msg": "Too Many Requests"}), 1, 2)
            .unwrap_err();
    assert_eq!(e.limited, Some(super::exchange::RATE_LIMIT_PAUSE));
    assert!(
        super::okx::page_from_response(&json!({"code": "50001", "msg": "x"}), 1, 2)
            .unwrap_err()
            .limited
            .is_none()
    );
}

// ---- проверка пауз шарда сделок по REST (Bybit, 16.11) и поздние потери в ряду минут (Н1) ----

use super::pausecheck::{self, PauseReq, PauseStats, Verdict};

fn preq(start_ms: i64, end_ms: i64, symbols: usize) -> PauseReq {
    PauseReq {
        shard: "trade01".into(),
        streams: (0..symbols)
            .map(|i| format!("publicTrade.S{i}USDT"))
            .collect(),
        start_ms,
        end_ms,
        end_exact: true,
        lag_p99_ms: Some(LAG_P99),
        emit: Box::new(|_, _| {}),
    }
}

/// p99 лага сделок шарда в проверках: измеренный `trade01` Bybit 16.11 (процесс `3835be7b`, 1 509 106 записей) — 133 мс.
const LAG_P99: i64 = 133;

fn quick() -> pausecheck::WorkCfg {
    pausecheck::WorkCfg {
        min_call_gap: Duration::ZERO,
        retry: Vec::new(),
    }
}

fn run_check(req: &PauseReq, fetch: pausecheck::FetchFn<'_>) -> (pausecheck::Outcome, PauseStats) {
    let st = PauseStats::default();
    let stop = AtomicBool::new(false);
    let mut last = Instant::now();
    let o = pausecheck::check(fetch, &quick(), req, &st, &stop, &mut last);
    (o, st)
}

/// Пауза 4 с, окно проверки `[start, end − p99 лага]` (концы — приём кадров); последняя сделка до паузы и первая после неё (лаг ≈ 0,1 с ≤ p99) в окно не входят.
#[test]
fn a_pause_with_no_exchange_trades_inside_is_market_silence_and_the_edges_do_not_count() {
    let now = super::events::now_ms();
    let (s, e) = (now - 20_000, now - 16_000);
    let req = preq(s, e, 5);
    let fetch = |_: &str| Some(Ok(vec![s - 60_000, s - 100, e - 100, e + 5_000]));
    let (o, st) = run_check(&req, &fetch);
    assert_eq!(o.verdict, Some(Verdict::Quiet));
    assert_eq!((o.checked, o.unknown, o.in_window), (5, 0, 0));
    assert_eq!(o.window, (s, e - LAG_P99));
    assert_eq!(st.rest_requests.load(Relaxed), 5);
    // начало: сделка на мс приёма последнего кадра — в окне, на мс раньше — доставлена до паузы
    let at = |t: i64| move |_: &str| Some(Ok(vec![s - 60_000, t]));
    assert_eq!(run_check(&req, &at(s - 1)).0.verdict, Some(Verdict::Quiet));
    assert_eq!(run_check(&req, &at(s)).0.verdict, Some(Verdict::Loss));
    // конец: окно кончается за p99 лага до приёма первого кадра после паузы
    assert_eq!(
        run_check(&req, &at(e - LAG_P99 + 1)).0.verdict,
        Some(Verdict::Quiet)
    );
    let (o, _) = run_check(&req, &at(e - LAG_P99));
    assert_eq!(
        (o.verdict, o.in_window),
        (Some(Verdict::Loss), 1),
        "сделка биржи внутри паузы — потеря"
    );
}

/// Пять реальных пауз `trade01` Bybit 16.11 (srv, процесс `3835be7b`; `tools/collect_gap/real_pauses.py`): `gap_start_ms` надзора, `start + gap_total_ms`, приём и время биржи последней записи до паузы, то же первой после.
const REAL_PAUSES: [[i64; 6]; 5] = [
    [
        1_794_811_416_603,
        1_794_811_420_113,
        1_794_811_416_591,
        1_794_811_416_485,
        1_794_811_420_268,
        1_794_811_420_161,
    ],
    [
        1_794_812_144_854,
        1_794_812_148_548,
        1_794_812_144_837,
        1_794_812_144_731,
        1_794_812_149_010,
        1_794_812_148_903,
    ],
    [
        1_794_815_156_398,
        1_794_815_159_463,
        1_794_815_156_383,
        1_794_815_156_272,
        1_794_815_159_594,
        1_794_815_159_487,
    ],
    [
        1_794_816_767_437,
        1_794_816_770_737,
        1_794_816_767_424,
        1_794_816_767_318,
        1_794_816_770_920,
        1_794_816_770_809,
    ],
    [
        1_794_818_433_616,
        1_794_818_436_790,
        1_794_818_433_593,
        1_794_818_433_486,
        1_794_818_437_130,
        1_794_818_437_023,
    ],
];

/// Окно по приёму кадров на пяти реальных паузах: крайние доставленные записи (время биржи последней до и первой после паузы) в окно не входят, середина паузы и сделки у самых краёв окна — входят;
/// прежнее окно (тик надзора, по 500 мс с краёв) на тех же паузах кончалось на 0,5–1,0 с раньше и сделку в хвосте паузы пропускало бы.
#[test]
fn the_window_on_five_real_pauses_excludes_the_delivered_edge_trades_and_covers_the_pause() {
    let now = super::events::now_ms();
    for (i, p) in REAL_PAUSES.iter().enumerate() {
        let [tick_s, tick_e, last_rx, last_e, first_rx, first_e] = *p;
        // сдвиг к «сейчас»: сверка отказывает паузам старше `MAX_AGE_MS`; разности — реальные
        let off = now - 20_000 - last_rx;
        let sh = |t: i64| t + off;
        let req = preq(sh(last_rx), sh(first_rx), 3);
        let old = |t: i64| (sh(tick_s) + 500..=sh(tick_e) - 500).contains(&t);
        let older = sh(last_rx) - 60_000;
        // в REST — только краевые доставленные сделки (они же в файле): тишина
        let (o, _) = run_check(&req, &|_| {
            Some(Ok(vec![
                older,
                sh(last_e),
                sh(first_e),
                sh(first_rx) + 5_000,
            ]))
        });
        assert_eq!(
            (o.verdict, o.in_window, o.window),
            (
                Some(Verdict::Quiet),
                0,
                (sh(last_rx), sh(first_rx) - LAG_P99)
            ),
            "пауза {} (по тику {} мс, по приёму {} мс)",
            i + 1,
            tick_e - tick_s,
            first_rx - last_rx
        );
        // сделка в середине паузы — потеря
        let mid = sh(last_rx + (first_rx - last_rx) / 2);
        let (o, _) = run_check(&req, &|_| Some(Ok(vec![older, mid])));
        assert_eq!(
            (o.verdict, o.in_window),
            (Some(Verdict::Loss), 1),
            "пауза {}",
            i + 1
        );
        // сделка в хвосте паузы (после тика, до конца окна) — потеря; прежнее окно её не видело
        let tail = sh(first_rx) - LAG_P99 - 1;
        assert!(!old(tail), "пауза {}: хвост не в прежнем окне", i + 1);
        let (o, _) = run_check(&req, &|_| Some(Ok(vec![older, tail])));
        assert_eq!(
            (o.verdict, o.in_window),
            (Some(Verdict::Loss), 1),
            "пауза {}",
            i + 1
        );
        // на мс позже окна (доставленная после паузы сделка с лагом ≤ p99) — не потеря
        let (o, _) = run_check(&req, &|_| Some(Ok(vec![older, sh(first_rx) - LAG_P99 + 1])));
        assert_eq!(o.verdict, Some(Verdict::Quiet), "пауза {}", i + 1);
        // лаг краевых записей — в пределах p99, иначе таблица не про то
        assert!(first_rx - first_e <= LAG_P99 && last_rx - last_e <= LAG_P99);
    }
}

/// Пауза не длиннее лага — окно пусто, тишина не доказана, запросов нет; лага нет — запасной.
#[test]
fn an_empty_window_is_unverified_and_a_missing_lag_falls_back() {
    let now = super::events::now_ms();
    let (s, e) = (now - 20_000, now - 19_900);
    let (o, st) = run_check(&preq(s, e, 3), &|_| Some(Ok(vec![s - 60_000])));
    assert_eq!(
        (o.verdict, o.why, o.unknown),
        (Some(Verdict::Unverified), "window_empty", 3)
    );
    assert_eq!(st.rest_requests.load(Relaxed), 0);
    let mut req = preq(now - 20_000, now - 16_000, 2);
    req.lag_p99_ms = None;
    let (o, _) = run_check(&req, &|_| Some(Ok(vec![req.start_ms - 60_000])));
    assert_eq!(
        (o.verdict, o.lag_used_ms, o.window.1),
        (
            Some(Verdict::Quiet),
            pausecheck::LAG_FALLBACK_MS,
            req.end_ms - pausecheck::LAG_FALLBACK_MS
        )
    );
}

#[test]
fn a_trade_inside_the_pause_ends_the_check_at_that_symbol() {
    let now = super::events::now_ms();
    let (s, e) = (now - 20_000, now - 16_000);
    let req = preq(s, e, 10);
    let fetch = |sym: &str| {
        Some(Ok(if sym.ends_with("S3USDT") {
            vec![s - 60_000, s + 2_000]
        } else {
            vec![s - 60_000]
        }))
    };
    let (o, st) = run_check(&req, &fetch);
    assert_eq!((o.verdict, o.checked), (Some(Verdict::Loss), 4));
    assert_eq!(
        st.rest_requests.load(Relaxed),
        4,
        "остальные символы не нужны"
    );
}

/// Не доказано — не тишина: сбой REST, окно не покрыто ответом (самая старая сделка ответа позже начала окна), пауза старше `MAX_AGE_MS`.
#[test]
fn an_unproven_pause_is_unverified_and_colors_like_a_loss() {
    let now = super::events::now_ms();
    let (s, e) = (now - 20_000, now - 16_000);
    let req = preq(s, e, 3);
    // окно не покрыто: 1 000 последних сделок символа новее начала окна, внутри окна их нет
    let (o, _) = run_check(&req, &|_| Some(Ok(vec![e + 1_000, e + 2_000])));
    assert_eq!(
        (o.verdict, o.unknown, o.why),
        (Some(Verdict::Unverified), 3, "not_proven")
    );
    // сбой REST (повторов нет): символы неизвестны
    let (o, st) = run_check(&req, &|_| Some(Err(RestError::from("boom"))));
    assert_eq!((o.verdict, o.unknown), (Some(Verdict::Unverified), 3));
    assert_eq!(st.rest_errors.load(Relaxed), 3);
    // нет проверки у биржи
    let (o, _) = run_check(&req, &|_| None);
    assert_eq!(
        (o.verdict, o.why),
        (Some(Verdict::Unverified), "unsupported")
    );
    // слишком старая пауза: REST уже не видит её окна
    let old = preq(
        now - pausecheck::MAX_AGE_MS - 5_000,
        now - pausecheck::MAX_AGE_MS - 1_000,
        3,
    );
    let (o, st) = run_check(&old, &|_| Some(Ok(vec![])));
    assert_eq!((o.verdict, o.why), (Some(Verdict::Unverified), "too_old"));
    assert_eq!(st.rest_requests.load(Relaxed), 0);
    // символ без сделок вовсе — окно покрыто (сделок на бирже нет)
    let (o, _) = run_check(&req, &|_| Some(Ok(vec![])));
    assert_eq!(o.verdict, Some(Verdict::Quiet));
    assert!(Verdict::Loss.is_bad() && Verdict::Unverified.is_bad() && !Verdict::Quiet.is_bad());
}

/// Отказ по частоте ставит на паузу весь поток и не тратит попытку символа; запросы идут не чаще `min_call_gap`.
#[test]
fn a_rate_limit_pauses_the_check_and_the_symbol_is_asked_again() {
    use std::sync::atomic::AtomicUsize;
    let now = super::events::now_ms();
    let (s, e) = (now - 20_000, now - 16_000);
    let req = preq(s, e, 3);
    let calls = AtomicUsize::new(0);
    let fetch = |_: &str| {
        let n = calls.fetch_add(1, Relaxed);
        Some(if n == 1 {
            Err(RestError::limited(
                "429".into(),
                Some(Duration::from_millis(30)),
            ))
        } else {
            Ok(vec![s - 60_000])
        })
    };
    let t0 = Instant::now();
    let (o, st) = run_check(&req, &fetch);
    assert!(t0.elapsed() >= Duration::from_millis(30));
    assert_eq!((o.verdict, o.checked), (Some(Verdict::Quiet), 3));
    assert_eq!(
        (
            st.rest_requests.load(Relaxed),
            st.rate_limited.load(Relaxed)
        ),
        (4, 1),
        "три символа и один повтор после отказа"
    );
    // минимум между запросами
    let cfg = pausecheck::WorkCfg {
        min_call_gap: Duration::from_millis(20),
        retry: Vec::new(),
    };
    let st = PauseStats::default();
    let stop = AtomicBool::new(false);
    let mut last = Instant::now();
    let t0 = Instant::now();
    pausecheck::check(
        &|_| Some(Ok(vec![s - 60_000])),
        &cfg,
        &req,
        &st,
        &stop,
        &mut last,
    );
    assert!(
        t0.elapsed() >= Duration::from_millis(55),
        "3 запроса не чаще 1 за 20 мс"
    );
}

/// Поток: очередь → вердикт в счётчики, журнал и событие шарда.
#[test]
fn the_pause_check_thread_judges_queued_pauses_and_reports_them() {
    use std::sync::atomic::AtomicU64;
    let now = super::events::now_ms();
    let (s, e) = (now - 20_000, now - 16_000);
    let st = Arc::new(PauseStats::default());
    let queued = Arc::new(AtomicU64::new(0));
    let stop = Arc::new(AtomicBool::new(false));
    let (tx, rx) = channel::<PauseReq>();
    let events = Arc::new(std::sync::Mutex::new(
        Vec::<(String, serde_json::Value)>::new(),
    ));
    let (st2, q2, stop2) = (Arc::clone(&st), Arc::clone(&queued), Arc::clone(&stop));
    let j = std::thread::spawn(move || {
        let fetch = |_: &str| Some(Ok(vec![s - 60_000]));
        pausecheck::work(&fetch, &quick(), &rx, &st2, &q2, &stop2);
    });
    let ev = Arc::clone(&events);
    let mut req = preq(s, e, 2);
    req.emit = Box::new(move |name, f| ev.lock().unwrap().push((name.to_string(), f)));
    st.requested.fetch_add(1, Relaxed);
    queued.fetch_add(1, Relaxed);
    tx.send(req).unwrap();
    let t0 = Instant::now();
    while st.pending() > 0 && t0.elapsed() < Duration::from_secs(5) {
        std::thread::sleep(Duration::from_millis(10));
    }
    stop.store(true, Relaxed);
    j.join().unwrap();
    assert_eq!(st.pending(), 0);
    assert_eq!(queued.load(Relaxed), 0);
    let got = events.lock().unwrap();
    assert_eq!(got.len(), 1);
    assert_eq!(got[0].0, "pause_check");
    assert_eq!(got[0].1["verdict"], "quiet");
    assert_eq!(got[0].1["pause_start_ms"], s);
    let j = st.json(now, 0);
    assert_eq!(
        (
            j["quiet_1h"].as_u64(),
            j["loss_1h"].as_u64(),
            j["unverified_1h"].as_u64(),
            j["pending"].as_u64()
        ),
        (Some(1), Some(0), Some(0), Some(0))
    );
    assert_eq!(
        st.bad_ms_between(0, i64::MAX),
        0,
        "тишина рынка в потерю не входит"
    );
    st.judged(s, 4_000, Verdict::Loss);
    st.judged(s + 1, 3_000, Verdict::Unverified);
    assert_eq!((st.bad_ms_between(s, s + 2), st.bad_events()), (7_000, 2));
}

#[test]
fn bybit_recent_trade_answer_is_parsed_and_rate_limit_codes_stop_the_requests() {
    use super::bybit::{parse_recent_trade_times, Bybit};
    let ok = json!({"retCode": 0, "result": {"list": [{"time": "1794812547123", "execId": "a"}, {"time": "1794812546000"}]}});
    assert_eq!(
        parse_recent_trade_times(&ok).unwrap(),
        vec![1_794_812_547_123, 1_794_812_546_000]
    );
    for code in [10006, 10018] {
        let e = parse_recent_trade_times(&json!({"retCode": code})).unwrap_err();
        assert!(e.limited.is_some(), "retCode {code}");
    }
    let e = parse_recent_trade_times(&json!({"retCode": 10001})).unwrap_err();
    assert!(e.limited.is_none());
    assert!(
        parse_recent_trade_times(&json!({"retCode": 0, "result": {"list": [{"x": 1}]}})).is_err()
    );
    assert!(Bybit.pause_check() && Bybit.trade_times("tickers.BTCUSDT").is_none());
    assert!(!Binance.pause_check());
}

fn series_rows(w: &super::series::SeriesWriter) -> Vec<serde_json::Value> {
    std::fs::read_to_string(w.path())
        .unwrap()
        .lines()
        .map(|l| serde_json::from_str::<serde_json::Value>(l).unwrap())
        .filter(|v| v.get("t").is_some())
        .collect()
}

/// Н1 повторной проверки: итог дозапроса приходит после строки минуты — он пишется строкой той же минуты `lost_ids_late` ровно один раз (ни потерь, ни двойного счёта).
#[test]
fn a_loss_that_arrives_after_its_minute_row_is_written_as_a_late_row_once() {
    let dir = tempfile::tempdir().unwrap();
    let min = 60_000i64;
    let m = ShardMetrics::new("trade00", 0, 64, 16);
    let stats = Arc::new(BackfillStats::default());
    let mut w = super::series::SeriesWriter::open(dir.path(), 100 * min + 5_000, "abc1234");
    w.set_backfill(Arc::clone(&stats));
    // потеря минуты 101 пришла до записи её строки — в строке, поздней строки нет
    stats.lost(101 * min + 30_000, 7);
    w.flush(&[Arc::clone(&m)], 103 * min + 10_000);
    assert_eq!(
        series_rows(&w)
            .iter()
            .map(|r| r["lost_ids"].as_i64())
            .collect::<Vec<_>>(),
        vec![Some(7), Some(0)]
    );
    // итоги дозапроса пришли позже: минуты 101 и 102 уже записаны
    stats.lost(101 * min + 40_000, 5);
    stats.lost(102 * min + 1_000, 11);
    w.flush(&[Arc::clone(&m)], 103 * min + 20_000);
    w.flush(&[Arc::clone(&m)], 103 * min + 30_000);
    let late: Vec<(i64, i64)> = series_rows(&w)
        .iter()
        .filter(|r| r.get("lost_ids_late").is_some())
        .map(|r| {
            (
                r["t"].as_i64().unwrap() / 60,
                r["lost_ids_late"].as_i64().unwrap(),
            )
        })
        .collect();
    assert_eq!(
        late,
        vec![(101, 5), (102, 11)],
        "по минуте потери, разница с учтённым"
    );
    // минута, ещё не записанная, берёт потерю в свою строку, поздней строки не будет
    stats.lost(103 * min + 50_000, 3);
    w.flush(&[Arc::clone(&m)], 105 * min + 1_000);
    let rows = series_rows(&w);
    assert_eq!(
        rows.iter()
            .filter(|r| r.get("lost_ids_late").is_some())
            .count(),
        2
    );
    assert_eq!(
        rows.iter()
            .find(|r| r["t"] == 103 * 60 && r.get("lost_ids").is_some())
            .map(|r| r["lost_ids"].as_i64()),
        Some(Some(3))
    );
    // потеря в минуте запуска: строки у минуты нет — поздняя строка её запишет
    stats.lost(100 * min + 50_000, 2);
    w.flush(&[Arc::clone(&m)], 105 * min + 2_000);
    assert!(series_rows(&w)
        .iter()
        .any(|r| r["t"] == 100 * 60 && r["lost_ids_late"] == 2));
}

/// Ряд минут биржи с проверкой REST: паузы шардов сделок с вердиктом «тишина» в `lost_ms` не входят, «потеря» — входит (в строку или поздней строкой `lost_ms_late`), книга и mark — как были.
#[test]
fn series_minutes_count_a_trade_pause_as_loss_only_with_a_bad_rest_verdict() {
    let dir = tempfile::tempdir().unwrap();
    let min = 60_000i64;
    let tr = ShardMetrics::new("trade00", 0, 64, 16);
    let book = ShardMetrics::new("book00", 0, 64, 16);
    tr.lists().gap_spans.push_back((101 * min + 1_000, 3_500));
    tr.lists().gap_spans.push_back((102 * min + 1_000, 4_000));
    book.lists().gap_spans.push_back((101 * min + 2_000, 2_000));
    let ps = Arc::new(PauseStats::default());
    ps.judged(101 * min + 1_000, 3_500, Verdict::Quiet);
    let mut w = super::series::SeriesWriter::open(dir.path(), 100 * min + 5_000, "abc1234");
    w.set_pause_check(Arc::clone(&ps));
    let shards = [Arc::clone(&tr), Arc::clone(&book)];
    w.flush(&shards, 103 * min + 10_000);
    let rows = series_rows(&w);
    assert_eq!(
        (rows[0]["lost_ms"].as_i64(), rows[0]["pause_ms"].as_i64()),
        (Some(2_000), Some(5_500)),
        "минута 101: тишина рынка не потеря, пауза книги — потеря"
    );
    assert_eq!(
        (rows[1]["lost_ms"].as_i64(), rows[1]["pause_ms"].as_i64()),
        (Some(0), Some(4_000))
    );
    assert!(
        rows[0].get("lost_ids").is_none(),
        "у биржи без дозапроса поля нет"
    );
    // вердикт по паузе минуты 102 пришёл после строки минуты — поздняя строка, один раз
    ps.judged(102 * min + 1_000, 4_000, Verdict::Loss);
    w.flush(&shards, 103 * min + 20_000);
    w.flush(&shards, 103 * min + 30_000);
    let late: Vec<(i64, i64)> = series_rows(&w)
        .iter()
        .filter(|r| r.get("lost_ms_late").is_some())
        .map(|r| {
            (
                r["t"].as_i64().unwrap() / 60,
                r["lost_ms_late"].as_i64().unwrap(),
            )
        })
        .collect();
    assert_eq!(late, vec![(102, 4_000)]);
}

/// Происшествие 16.11 12:50 (BingX: 15 пауз по ≈ 125 с, `lost_ms` 0 во всех строках): пауза шарда кончается после записи строки её минуты начала — поздняя строка той же минуты
/// (`lost_ms_late`, `gaps_late`, у бирж с `pause_ms` — и `pause_ms_late`), ровно один раз; пауза, кончившаяся до записи строки, поздней строки не даёт; паузы шардов сделок биржи с дозапросом в `lost_ms_late` не входят.
#[test]
fn a_pause_that_ends_after_its_minute_row_is_written_as_a_late_row_once() {
    let min = 60_000i64;
    // биржа без дозапроса и проверки REST: в `lost_ms` входят все паузы
    let dir = tempfile::tempdir().unwrap();
    let book = ShardMetrics::new("book00", 0, 64, 16);
    let trade = ShardMetrics::new("trade00", 0, 64, 16);
    let shards = [Arc::clone(&book), Arc::clone(&trade)];
    let mut w = super::series::SeriesWriter::open(dir.path(), 100 * min + 5_000, "abc1234");
    // пауза минуты 101 кончилась до записи строки — в строке
    book.lists()
        .gap_spans
        .push_back((101 * min + 10_000, 3_000));
    w.flush(&shards, 103 * min + 10_000);
    let rows = series_rows(&w);
    assert_eq!(rows[0]["lost_ms"], 3_000);
    assert!(rows.iter().all(|r| r.get("lost_ms_late").is_none()));
    // многоминутная пауза минуты 102 кончилась позже (строка минуты 102 записана с lost_ms 0)
    book.lists()
        .gap_spans
        .push_back((102 * min + 9_000, 125_000));
    trade
        .lists()
        .gap_spans
        .push_back((102 * min + 9_000, 120_000));
    w.flush(&shards, 103 * min + 20_000);
    w.flush(&shards, 103 * min + 30_000);
    let late: Vec<serde_json::Value> = series_rows(&w)
        .into_iter()
        .filter(|r| r.get("lost_ms_late").is_some())
        .collect();
    assert_eq!(late.len(), 1, "одна строка на минуту, ровно один раз");
    assert_eq!(
        (
            late[0]["t"].as_i64(),
            late[0]["lost_ms_late"].as_i64(),
            late[0]["gaps_late"].as_i64()
        ),
        (Some(102 * 60), Some(245_000), Some(2))
    );
    assert!(
        late[0].get("pause_ms_late").is_none(),
        "у биржи без `pause_ms` поля нет"
    );
    // пауза в минуте запуска (строки у минуты нет) — тоже поздней строкой
    book.lists()
        .gap_spans
        .push_back((100 * min + 40_000, 7_000));
    w.flush(&shards, 104 * min + 1_000);
    assert!(series_rows(&w)
        .iter()
        .any(|r| r["t"] == 100 * 60 && r["lost_ms_late"] == 7_000 && r["gaps_late"] == 1));

    // биржа с дозапросом: паузы шардов сделок — в `pause_ms_late`, в `lost_ms_late` — только книга и mark
    let dir = tempfile::tempdir().unwrap();
    let book = ShardMetrics::new("book00", 0, 64, 16);
    let trade = ShardMetrics::new("trade00", 0, 64, 16);
    let shards = [Arc::clone(&book), Arc::clone(&trade)];
    let mut w = super::series::SeriesWriter::open(dir.path(), 100 * min + 5_000, "abc1234");
    w.set_backfill(Arc::new(BackfillStats::default()));
    w.flush(&shards, 103 * min + 10_000);
    trade
        .lists()
        .gap_spans
        .push_back((102 * min + 9_000, 125_000));
    w.flush(&shards, 103 * min + 20_000);
    let r = series_rows(&w)
        .into_iter()
        .find(|r| r.get("gaps_late").is_some())
        .unwrap();
    assert_eq!(
        (
            r["lost_ms_late"].as_i64(),
            r["pause_ms_late"].as_i64(),
            r["gaps_late"].as_i64()
        ),
        (Some(0), Some(125_000), Some(1)),
        "пауза шарда сделок не потеря: номера возвращает REST; `lost_ms_late` есть всегда (прежняя страница узнаёт по нему поздние строки)"
    );
    book.lists()
        .gap_spans
        .push_back((102 * min + 11_000, 125_000));
    w.flush(&shards, 103 * min + 30_000);
    let r = series_rows(&w)
        .into_iter()
        .filter(|r| r.get("gaps_late").is_some())
        .nth(1)
        .unwrap();
    assert_eq!(
        (r["lost_ms_late"].as_i64(), r["pause_ms_late"].as_i64()),
        (Some(125_000), Some(125_000))
    );
}

/// Повторная проверка 16.11, п. 7 (Binance `over_cap` [book00] 08:23: оценка 2 068 > потолка 1 800): шард выше потолка новых символов не получает, пачка новых символов открывает один
/// новый шард (не по шарду на символ), а уже подписанные символы не двигаются — раскладка меняется только `add_symbol` / `remove_symbol`, новые веса операций не дают.
#[test]
fn a_shard_over_the_cap_takes_no_new_symbols_a_burst_spawns_one_shard_and_nothing_is_moved() {
    let exch = Binance;
    let (pool, w) = two_hot_pool();
    let plans = exch.plan(&pool, &kinds(), 20);
    let mut layout = Layout::new(&exch, &plans, &pool, &kinds(), 20);
    layout.set_weights(w);
    assert!(layout.loads()[0] > 1_800.0, "book00 выше потолка");
    let (mut spawns, mut appends_to_hot, mut removes) = (0, 0, 0);
    let mut appended_to: Vec<usize> = Vec::new();
    for i in 0..10 {
        for op in layout.add_symbol(&exch, &format!("NEW{i}USDT")) {
            match op {
                Op::Spawn { plan, .. } if plan.name.starts_with("book") => spawns += 1,
                Op::Spawn { .. } => {}
                Op::Append { slot, streams } if streams[0].ends_with("@bookTicker") => {
                    appends_to_hot += usize::from(slot == 0);
                    appended_to.push(slot);
                }
                Op::Remove { .. } => removes += 1,
                Op::Append { .. } => {}
            }
        }
    }
    assert_eq!(appends_to_hot, 0, "book00 новых символов не получает");
    assert_eq!(removes, 0);
    assert_eq!(spawns, 1, "пачка из 10 символов — один новый шард книги");
    // свежие веса (каждый опрос REST) раскладку не трогают: операций нет, символы остаются где были
    let before = layout.loads();
    let slots_before: Vec<usize> = layout.slots().iter().map(|s| s.streams).collect();
    let mut r = rates_of("book", &[("AAAUSDT", 100.0), ("AABUSDT", 100.0)]);
    for s in names(30) {
        r.set("book", &s.to_lowercase(), 400.0);
    }
    layout.set_weights(Weights::new(r, &BTreeMap::new()));
    assert_ne!(layout.loads(), before, "оценки изменились");
    assert_eq!(
        layout.slots().iter().map(|s| s.streams).collect::<Vec<_>>(),
        slots_before
    );
}
