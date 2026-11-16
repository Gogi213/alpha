//! Тесты живого пула (16.11): гистерезис состава, раскладка по шардам, пул-файл запуска, живая подписка / отписка на соединении, бюджет подключений
//! (резерв шардам без соединения, потолки на шард; повтор бури Binance 15.11 18:12), раздел health.

use std::collections::{BTreeMap, HashSet};
use std::net::TcpListener;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use super::binance::Binance;
use super::bingx::Bingx;
use super::budget::{Budget, Cap};
use super::bybit::Bybit;
use super::exchange::{Entrance, Exchange, Family, ShardPlan, Turnover};
use super::gate::Gate;
use super::health::{build, HealthCtx, PoolHealth, ProcSampler};
use super::livepool::{kind_of, Layout, LivePool, Op, PoolFile, PoolState};
use super::okx::Okx;
use super::session::StreamSet;
use super::shard::{gap_seed, spawn_shard, GapThreshold, ShardOpts};

const H: i64 = 3_600_000;

fn syms(prefix: &str, n: usize, suffix: &str) -> Vec<String> {
    (0..n).map(|i| format!("{prefix}{i:03}{suffix}")).collect()
}

fn qv_of(pairs: &[(&str, f64)]) -> BTreeMap<String, f64> {
    pairs.iter().map(|(s, v)| ((*s).to_string(), *v)).collect()
}

fn kinds() -> Vec<String> {
    ["book", "trade", "mark"].map(String::from).to_vec()
}

// ---- гистерезис состава ----

#[test]
fn symbol_enters_at_threshold_and_leaves_only_after_a_full_day_below() {
    let mut st = PoolState::new(
        &["AUSDT".into(), "BUSDT".into()],
        0,
        300_000.0,
        0,
        24 * 3600,
    );
    // C впервые выше порога, A на пороге, B упал под порог
    let qv = qv_of(&[
        ("AUSDT", 300_000.0),
        ("BUSDT", 100_000.0),
        ("CUSDT", 301_000.0),
    ]);
    let r = st.review(&qv, H).unwrap();
    assert_eq!(r.add, vec![("CUSDT".to_string(), 301_000.0)]);
    assert!(r.stale.is_empty(), "B под порогом всего час");
    assert_eq!(r.eligible, 2);
    st.admit("CUSDT", H);
    // сутки без одного часа после последнего «выше порога» (B был принят в 0): ещё держится
    let r = st.review(&qv, 24 * H - 1).unwrap();
    assert!(r.stale.is_empty(), "B держится: сутки не прошли");
    // ровно сутки под порогом
    let r = st.review(&qv, 24 * H).unwrap();
    assert_eq!(r.stale, vec![("BUSDT".to_string(), Some(100_000.0))]);
    st.forget("BUSDT");
    assert!(!st.contains("BUSDT") && st.contains("AUSDT") && st.contains("CUSDT"));
}

#[test]
fn returning_above_the_threshold_restarts_the_hold() {
    let mut st = PoolState::new(&["BUSDT".into()], 0, 300_000.0, 0, 24 * 3600);
    let low = qv_of(&[("BUSDT", 100_000.0), ("XUSDT", 1_000_000.0)]);
    let high = qv_of(&[("BUSDT", 400_000.0), ("XUSDT", 1_000_000.0)]);
    assert!(st.review(&low, 20 * H).unwrap().stale.is_empty());
    // вернулся выше порога на 20-м часу: отсчёт суток начинается заново
    st.review(&high, 21 * H).unwrap();
    assert!(st.review(&low, 44 * H).unwrap().stale.is_empty());
    assert_eq!(st.review(&low, 45 * H).unwrap().stale.len(), 1);
}

#[test]
fn symbol_missing_from_the_response_is_held_like_a_symbol_below_the_threshold() {
    let mut st = PoolState::new(&["AUSDT".into(), "GONEUSDT".into()], 0, 300_000.0, 0, 3600);
    let qv = qv_of(&[("AUSDT", 500_000.0)]);
    assert!(st.review(&qv, 1000).unwrap().stale.is_empty());
    let r = st.review(&qv, H).unwrap();
    assert_eq!(r.stale, vec![("GONEUSDT".to_string(), None)]);
}

#[test]
fn truncated_rest_response_is_ignored() {
    let pool: Vec<String> = syms("S", 10, "USDT");
    let mut st = PoolState::new(&pool, 0, 300_000.0, 0, 3600);
    // выдача из 4 контрактов при пуле в 10: обрезанный ответ, пересмотр пропущен (никого не снимаем и не добавляем)
    let qv = qv_of(&[
        ("S000USDT", 1e6),
        ("S001USDT", 1e6),
        ("NEWUSDT", 1e6),
        ("S002USDT", 1e6),
    ]);
    assert!(st.review(&qv, 100 * H).is_none());
}

#[test]
fn top_exclude_is_off_by_default_and_the_biggest_symbol_is_kept() {
    // оборот самого крупного — в пуле (до 16.11 топ-10 Binance и Bybit исключались и выпадали на сутки и дольше)
    for exch in [
        &Binance as &dyn Exchange,
        &Bybit,
        &Okx::default(),
        &Bingx::default(),
        &Gate::default(),
    ] {
        assert_eq!(exch.default_top_exclude(), 0, "{}", exch.name());
    }
    let mut st = PoolState::new(&[], 0, 300_000.0, 0, 3600);
    let qv = qv_of(&[("BARDUSDT", 165_800_000.0), ("XUSDT", 400_000.0)]);
    let r = st.review(&qv, 1).unwrap();
    assert_eq!(r.add.len(), 2, "BARD в пуле");
    let mut st = PoolState::new(&[], 0, 300_000.0, 1, 3600);
    let r = st.review(&qv, 1).unwrap();
    assert_eq!(
        r.add,
        vec![("XUSDT".to_string(), 400_000.0)],
        "--top-exclude 1 по-прежнему работает"
    );
}

// ---- раскладка по шардам ----

fn layout_for(exch: &dyn Exchange, pool: &[String]) -> (Layout, Vec<ShardPlan>) {
    let plans = exch.plan(pool, &kinds(), 0);
    (Layout::new(exch, &plans, pool, &kinds(), 0), plans)
}

#[test]
fn new_binance_symbol_goes_to_the_emptiest_shards_of_each_kind_and_mark_is_pool_free() {
    let pool = syms("S", 100, "USDT");
    let (mut lay, plans) = layout_for(&Binance, &pool);
    // книга 100 / 32 → 4 шарда по 25; сделки 100 / 64 → 2 по 50; mark00 один
    let names: Vec<&str> = plans.iter().map(|p| p.name.as_str()).collect();
    assert_eq!(
        names,
        ["book00", "book01", "book02", "book03", "trade00", "trade01", "mark00"]
    );
    assert_eq!(lay.symbols(), 100);
    let ops = lay.add_symbol(&Binance, "NEWUSDT");
    assert_eq!(
        ops,
        vec![
            Op::Append {
                slot: 0,
                streams: vec!["newusdt@bookTicker".into()]
            },
            Op::Append {
                slot: 4,
                streams: vec!["newusdt@aggTrade".into()]
            },
        ],
        "книга и сделки; mark не зависит от пула"
    );
    assert!(lay.contains("NEWUSDT"));
    // второй новый — в следующий по загрузке шард книги
    let ops = lay.add_symbol(&Binance, "NEW2USDT");
    assert!(matches!(&ops[0], Op::Append { slot: 1, .. }), "{ops:?}");
}

#[test]
fn full_shards_spawn_a_new_one_and_later_symbols_fill_it() {
    let pool = syms("S", 100, "USDT");
    let (mut lay, _) = layout_for(&Binance, &pool);
    let mut spawned = None;
    for i in 0..40 {
        let ops = lay.add_symbol(&Binance, &format!("N{i:02}USDT"));
        if let Some(Op::Spawn { slot, plan }) = ops.iter().find(|o| matches!(o, Op::Spawn { .. })) {
            spawned = Some((i, *slot, plan.clone()));
            break;
        }
    }
    // в четырёх шардах книги свободно по 7 мест: 28 символов влезают, 29-й открывает book04 (индекс 7: после 4 + 2 + mark00)
    let (i, slot, plan) = spawned.expect("шард открыт");
    assert_eq!(i, 28);
    assert_eq!(
        (slot, plan.name.as_str(), plan.endpoint.as_str()),
        (7, "book04", "public")
    );
    assert_eq!(plan.streams, vec!["n28usdt@bookTicker".to_string()]);
    // следующий символ — в наименее загруженный: это новый шард (1 поток)
    let ops = lay.add_symbol(&Binance, "N29USDT");
    assert!(matches!(&ops[0], Op::Append { slot: 7, .. }), "{ops:?}");
    assert_eq!(lay.slots().get(7).map(|s| s.streams), Some(2));
}

#[test]
fn removing_a_symbol_unsubscribes_its_streams_but_never_empties_a_shard() {
    let pool = syms("S", 100, "USDT");
    let (mut lay, _) = layout_for(&Binance, &pool);
    lay.add_symbol(&Binance, "NEWUSDT");
    let ops = lay.remove_symbol("NEWUSDT").expect("можно снять");
    assert_eq!(
        ops,
        vec![
            Op::Remove {
                slot: 0,
                streams: vec!["newusdt@bookTicker".into()]
            },
            Op::Remove {
                slot: 4,
                streams: vec!["newusdt@aggTrade".into()]
            },
        ]
    );
    assert!(!lay.contains("NEWUSDT"));
    // единственный символ шардов: снять нельзя (шард без потоков биржа закрывает), символ остаётся
    let pool = vec!["ONLYUSDT".to_string()];
    let (mut lay, _) = layout_for(&Bybit, &pool);
    assert!(lay.remove_symbol("ONLYUSDT").is_none());
    assert!(lay.contains("ONLYUSDT"));
}

#[test]
fn okx_symbol_keeps_its_three_mark_channels_in_one_shard() {
    let pool: Vec<String> = syms("C", 30, "-USDT-SWAP");
    let (mut lay, plans) = layout_for(&Okx::default(), &pool);
    assert!(plans.iter().any(|p| p.name == "mark00"));
    let ops = lay.add_symbol(&Okx::default(), "NEW-USDT-SWAP");
    let mark: Vec<&Op> = ops
        .iter()
        .filter(|o| matches!(o, Op::Append { streams, .. } if streams.iter().any(|s| s.starts_with("mark-price:"))))
        .collect();
    assert_eq!(mark.len(), 1);
    if let Op::Append { streams, .. } = mark[0] {
        assert_eq!(
            streams,
            &[
                "mark-price:NEW-USDT-SWAP",
                "index-tickers:NEW-USDT",
                "funding-rate:NEW-USDT-SWAP"
            ]
        );
    }
}

#[test]
fn every_adapter_lays_out_and_unsubscribes_what_it_subscribed() {
    let cases: Vec<(Box<dyn Exchange>, Vec<String>)> = vec![
        (Box::new(Binance), syms("S", 20, "USDT")),
        (Box::new(Bybit), syms("S", 20, "USDT")),
        (Box::new(Okx::default()), syms("S", 20, "-USDT-SWAP")),
        (Box::new(Bingx::default()), syms("S", 20, "-USDT")),
        (Box::new(Gate::default()), syms("S", 20, "_USDT")),
    ];
    for (exch, pool) in cases {
        let (mut lay, plans) = layout_for(exch.as_ref(), &pool);
        // исходный набор раскладки = набор планов
        let total: usize = plans.iter().map(|p| p.streams.len()).sum();
        assert_eq!(
            lay.slots().iter().map(|s| s.streams).sum::<usize>(),
            total,
            "{}",
            exch.name()
        );
        let new = pool[0].replace("S000", "NEWNEW");
        let add = lay.add_symbol(exch.as_ref(), &new);
        let mut added: Vec<String> = Vec::new();
        for op in &add {
            match op {
                Op::Append { streams, .. } => added.extend(streams.clone()),
                Op::Spawn { plan, .. } => added.extend(plan.streams.clone()),
                Op::Remove { .. } => panic!("при добавлении снятия нет"),
            }
        }
        assert!(!added.is_empty(), "{}", exch.name());
        // отписка — те же потоки, что подписка, обратным сообщением
        let (sub, unsub) = (
            exch.subscribe_messages(&added).join("\n"),
            exch.unsubscribe_messages(&added).join("\n"),
        );
        assert_ne!(sub, unsub, "{}", exch.name());
        let m: Value =
            serde_json::from_str(exch.unsubscribe_messages(&added).first().unwrap()).unwrap();
        let word = ["method", "op", "reqType", "event"]
            .iter()
            .find_map(|k| m.get(*k).and_then(Value::as_str))
            .unwrap_or_default()
            .to_lowercase();
        assert!(word.contains("unsub"), "{}: {m}", exch.name());
        let rm = lay
            .remove_symbol(&new)
            .expect("добавленный символ снимается");
        let mut removed: Vec<String> = Vec::new();
        for op in rm {
            if let Op::Remove { streams, .. } = op {
                removed.extend(streams);
            }
        }
        let (a, r): (HashSet<_>, HashSet<_>) =
            (added.into_iter().collect(), removed.into_iter().collect());
        assert_eq!(a, r, "{}: снято ровно то, что добавлено", exch.name());
    }
}

#[test]
fn kind_is_the_shard_name_without_the_index() {
    assert_eq!(kind_of("book07"), "book");
    assert_eq!(kind_of("mark00"), "mark");
    assert_eq!(kind_of("trade12"), "trade");
}

// ---- набор потоков ----

#[test]
fn stream_set_versions_only_real_changes() {
    let s = StreamSet::new(vec!["a".into(), "b".into()]);
    assert_eq!(s.version(), 0);
    assert!(
        !s.apply(&["a".into()], &["zzz".into()]),
        "уже есть / нечего снимать"
    );
    assert_eq!(s.version(), 0);
    assert!(s.apply(&["c".into()], &["a".into()]));
    assert_eq!(s.version(), 1);
    let (v, now) = s.snapshot();
    assert_eq!((v, now), (1, vec!["b".to_string(), "c".to_string()]));
    assert_eq!(s.len(), 2);
}

// ---- пул-файл запуска ----

fn read_json(p: &Path) -> Value {
    serde_json::from_str(&std::fs::read_to_string(p).unwrap()).unwrap()
}

#[test]
fn live_pool_logs_every_change_with_a_timestamp_in_the_run_file() {
    let dir = tempfile::tempdir().unwrap();
    let pool = syms("S", 40, "USDT");
    let exch: Arc<dyn Exchange> = Arc::new(Binance);
    let plans = exch.plan(&pool, &kinds(), 0);
    let base = json!({"ts_ms": 1_000, "min_quote_volume": 300_000.0, "top_excluded": [], "pool": pool,
                      "meta": {}, "qv": {"S000USDT": 1.0}});
    let run_id = 1_794_000_000;
    let file = PoolFile::create(dir.path(), run_id, base, 300, 86_400).unwrap();
    let per_run = file.path().to_path_buf();
    let mut lp = LivePool::new(
        Arc::clone(&exch),
        PoolState::new(&pool, 1_000, 300_000.0, 0, 24 * 3600),
        Layout::new(exch.as_ref(), &plans, &pool, &kinds(), 0),
        Some(file),
        300,
        86_400,
    );
    let day = super::writer::day_string(run_id.div_euclid(86_400));
    assert_eq!(
        per_run,
        dir.path().join(format!("pool-{day}-{run_id}.json"))
    );
    assert!(
        dir.path().join(format!("pool-{day}.json")).exists(),
        "прежнее имя — для читателей по имени"
    );
    // свежий REST: все старые на месте (оборот выше порога), один новый
    let mut qv: BTreeMap<String, f64> = pool.iter().map(|s| (s.clone(), 1e6)).collect();
    qv.insert("BARDUSDT".into(), 165_800_000.0);
    let t = Turnover {
        qv: qv.clone(),
        meta: BTreeMap::new(),
    };
    let a = lp.apply(&t, 10 * 60_000);
    assert_eq!(a.changes.len(), 1);
    assert!(a.changes[0].add && a.changes[0].symbol == "BARDUSDT");
    assert_eq!(a.ops.len(), 2, "книга и сделки");
    let h = lp.health();
    assert_eq!(
        (h.pool, h.eligible, h.not_subscribed, h.missing_at_poll),
        (41, 41, 0, 1)
    );
    assert!((h.missing_at_poll_qv_usd - 165_800_000.0).abs() < 1.0);
    let doc = read_json(&per_run);
    assert_eq!(
        doc["pool"].as_array().unwrap().len(),
        40,
        "начальный состав не меняется"
    );
    assert_eq!(doc["pool_now"].as_array().unwrap().len(), 41);
    assert_eq!(doc["changes"][0]["op"], "add");
    assert_eq!(doc["changes"][0]["symbol"], "BARDUSDT");
    assert_eq!(doc["changes"][0]["ts_ms"], 600_000);
    assert_eq!(doc["updated_ms"], 600_000);
    assert_eq!(doc["run_id"], run_id);
    assert_eq!(
        read_json(&dir.path().join(format!("pool-{day}.json"))),
        doc,
        "прежнее имя — тот же документ"
    );
    // S000 ушёл под порог: до суток держится, через сутки снимается; журнал получает remove
    qv.insert("S000USDT".into(), 1_000.0);
    let t = Turnover {
        qv: qv.clone(),
        meta: BTreeMap::new(),
    };
    assert!(
        lp.apply(&t, 10 * 60_000 + 23 * H).changes.is_empty(),
        "23 ч под порогом — ещё держится"
    );
    let a = lp.apply(&t, 10 * 60_000 + 25 * H);
    assert_eq!(a.changes.len(), 1);
    assert!(!a.changes[0].add && a.changes[0].symbol == "S000USDT");
    assert!(a.ops.iter().all(|o| matches!(o, Op::Remove { .. })));
    let doc = read_json(&per_run);
    assert_eq!(doc["changes"].as_array().unwrap().len(), 2);
    assert_eq!(doc["changes"][1]["op"], "remove");
    assert_eq!(doc["pool_now"].as_array().unwrap().len(), 40);
}

#[test]
fn contract_meta_of_a_new_symbol_reaches_the_pool_file() {
    let dir = tempfile::tempdir().unwrap();
    let pool = syms("C", 5, "-USDT-SWAP");
    let exch: Arc<dyn Exchange> = Arc::new(Okx::default());
    let plans = exch.plan(&pool, &kinds(), 0);
    let base =
        json!({"ts_ms": 1, "pool": pool, "meta": {"C000-USDT-SWAP": {"ctVal": "1"}}, "qv": {}});
    let file = PoolFile::create(dir.path(), 1_794_000_000, base, 300, 86_400).unwrap();
    let per_run = file.path().to_path_buf();
    let mut lp = LivePool::new(
        Arc::clone(&exch),
        PoolState::new(&pool, 1, 300_000.0, 0, 3600),
        Layout::new(exch.as_ref(), &plans, &pool, &kinds(), 0),
        Some(file),
        300,
        86_400,
    );
    let mut qv: BTreeMap<String, f64> = pool.iter().map(|s| (s.clone(), 1e6)).collect();
    qv.insert("NEW-USDT-SWAP".into(), 2e6);
    let meta = BTreeMap::from([("NEW-USDT-SWAP".to_string(), json!({"ctVal": "0.01"}))]);
    lp.apply(&Turnover { qv, meta }, 5000);
    let doc = read_json(&per_run);
    assert_eq!(doc["meta"]["NEW-USDT-SWAP"]["ctVal"], "0.01");
    assert_eq!(doc["meta"]["C000-USDT-SWAP"]["ctVal"], "1");
    assert_eq!(doc["qv"]["NEW-USDT-SWAP"], 2e6);
}

#[test]
fn rest_failure_is_counted_and_keeps_the_pool() {
    let dir = tempfile::tempdir().unwrap();
    let pool = syms("S", 10, "USDT");
    let exch: Arc<dyn Exchange> = Arc::new(Bybit);
    let plans = exch.plan(&pool, &kinds(), 0);
    let mut lp = LivePool::new(
        Arc::clone(&exch),
        PoolState::new(&pool, 0, 300_000.0, 0, 3600),
        Layout::new(exch.as_ref(), &plans, &pool, &kinds(), 0),
        Some(
            PoolFile::create(
                dir.path(),
                1_794_000_000,
                json!({"pool": pool}),
                300,
                86_400,
            )
            .unwrap(),
        ),
        300,
        86_400,
    );
    lp.failed(1000, "HTTP 403");
    let h = lp.health();
    assert_eq!((h.errors, h.last_ok_ms, h.last_try_ms), (1, 0, 1000));
    assert_eq!(h.last_error.as_deref(), Some("HTTP 403"));
    // обрезанный ответ — тоже сбой, состав цел
    let a = lp.apply(
        &Turnover {
            qv: qv_of(&[("S000USDT", 1e6)]),
            meta: BTreeMap::new(),
        },
        2000,
    );
    assert!(a.ops.is_empty() && a.changes.is_empty());
    assert_eq!(lp.health().errors, 2);
    assert_eq!(lp.state.len(), 10);
}

// ---- живая подписка и отписка на соединении ----

/// Локальный WS-сервер: принимает соединения и записывает все текстовые сообщения клиента (подписки и отписки).
fn recording_server(
    listener: TcpListener,
    stop: Arc<AtomicBool>,
    log: Arc<Mutex<Vec<String>>>,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        listener.set_nonblocking(true).unwrap();
        let mut conns = Vec::new();
        while !stop.load(Relaxed) {
            match listener.accept() {
                Ok((s, _)) => {
                    s.set_nonblocking(false).unwrap();
                    let (stop, log) = (Arc::clone(&stop), Arc::clone(&log));
                    conns.push(std::thread::spawn(move || {
                        let Ok(mut ws) = tungstenite::accept(s) else {
                            return;
                        };
                        ws.get_ref()
                            .set_read_timeout(Some(Duration::from_millis(20)))
                            .unwrap();
                        while !stop.load(Relaxed) {
                            match ws.read() {
                                Ok(tungstenite::Message::Text(t)) => {
                                    log.lock().unwrap().push(t.to_string())
                                }
                                Ok(tungstenite::Message::Close(_)) => return,
                                Ok(_) => {}
                                Err(tungstenite::Error::Io(e))
                                    if matches!(
                                        e.kind(),
                                        std::io::ErrorKind::WouldBlock
                                            | std::io::ErrorKind::TimedOut
                                    ) => {}
                                Err(_) => return,
                            }
                        }
                    }));
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(5));
                }
                Err(_) => break,
            }
        }
        for c in conns {
            let _ = c.join();
        }
    })
}

fn wait_for(log: &Arc<Mutex<Vec<String>>>, what: &str, secs: u64) -> bool {
    let t0 = Instant::now();
    while t0.elapsed() < Duration::from_secs(secs) {
        if log.lock().unwrap().iter().any(|m| m.contains(what)) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    false
}

#[test]
fn live_stream_changes_reach_the_open_connection_as_subscribe_and_unsubscribe() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let (stop_src, log) = (
        Arc::new(AtomicBool::new(false)),
        Arc::new(Mutex::new(Vec::new())),
    );
    let src = recording_server(listener, Arc::clone(&stop_src), Arc::clone(&log));
    let dir = tempfile::tempdir().unwrap();
    let plan = ShardPlan {
        name: "book00".into(),
        endpoint: "public".into(),
        streams: vec!["btcusdt@bookTicker".into()],
    };
    let mut opts = ShardOpts::new(dir.path().to_path_buf(), 1_794_000_000);
    (opts.tls, opts.port, opts.flush) = (false, port, Duration::from_millis(30));
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
    assert!(
        wait_for(&log, "btcusdt@bookTicker", 15),
        "начальная подписка"
    );
    // новый символ докладывается на то же соединение, без переподключения
    h.streams.apply(&["ethusdt@bookTicker".to_string()], &[]);
    assert!(
        wait_for(&log, "ethusdt@bookTicker", 10),
        "SUBSCRIBE нового символа"
    );
    // ушедший снимается отпиской
    h.streams.apply(&[], &["btcusdt@bookTicker".to_string()]);
    assert!(wait_for(&log, "UNSUBSCRIBE", 10), "UNSUBSCRIBE ушедшего");
    std::thread::sleep(Duration::from_millis(300));
    stop.store(true, Relaxed);
    h.join();
    stop_src.store(true, Relaxed);
    src.join().unwrap();
    let msgs = log.lock().unwrap().clone();
    let sub_eth = msgs
        .iter()
        .filter(|m| m.contains("\"SUBSCRIBE\"") && m.contains("ethusdt@bookTicker"))
        .count();
    let unsub_btc = msgs
        .iter()
        .filter(|m| m.contains("\"UNSUBSCRIBE\"") && m.contains("btcusdt@bookTicker"))
        .count();
    assert_eq!((sub_eth, unsub_btc), (1, 1), "{msgs:?}");
    let ev = std::fs::read_to_string(dir.path().join("events-book00.jsonl")).unwrap();
    assert_eq!(ev.matches("\"ev\":\"pool_delta\"").count(), 2, "{ev}");
    assert_eq!(
        ev.matches("\"ev\":\"connected\"").count(),
        1,
        "переподключения не было"
    );
}

#[test]
fn a_replacement_session_subscribes_to_the_current_set_not_the_initial_plan() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let (stop_src, log) = (
        Arc::new(AtomicBool::new(false)),
        Arc::new(Mutex::new(Vec::new())),
    );
    let src = recording_server(listener, Arc::clone(&stop_src), Arc::clone(&log));
    let dir = tempfile::tempdir().unwrap();
    let plan = ShardPlan {
        name: "book00".into(),
        endpoint: "public".into(),
        streams: vec!["btcusdt@bookTicker".into()],
    };
    let mut opts = ShardOpts::new(dir.path().to_path_buf(), 1_794_000_000);
    (opts.tls, opts.port, opts.flush) = (false, port, Duration::from_millis(30));
    // линия живёт 1,5 с и открывает замену: она подключается уже после смены набора
    (opts.max_age, opts.overlap, opts.switch_wait) = (
        Duration::from_millis(1500),
        Duration::from_millis(300),
        Duration::from_secs(2),
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
    assert!(wait_for(&log, "btcusdt@bookTicker", 15));
    h.streams.apply(
        &["ethusdt@bookTicker".to_string()],
        &["btcusdt@bookTicker".to_string()],
    );
    let subs = |what: &str| {
        log.lock()
            .unwrap()
            .iter()
            .filter(|m| m.contains("\"SUBSCRIBE\"") && m.contains(what))
            .count()
    };
    // eth: дельта на старом соединении + подписка замены; btc: только начальная подписка
    let t0 = Instant::now();
    while subs("ethusdt@bookTicker") < 2 && t0.elapsed() < Duration::from_secs(20) {
        std::thread::sleep(Duration::from_millis(50));
    }
    let (eth, btc) = (subs("ethusdt@bookTicker"), subs("btcusdt@bookTicker"));
    stop.store(true, Relaxed);
    h.join();
    stop_src.store(true, Relaxed);
    src.join().unwrap();
    assert!(eth >= 2, "eth: дельта и замена, было {eth}");
    assert_eq!(btc, 1, "замена не подписывается на снятый btc");
}

// ---- бюджет подключений: резерв и потолки; повтор бури Binance 15.11 18:12 ----

#[test]
fn cap_allows_max_events_per_window_and_reports_the_wait() {
    let c = Cap::new(3, Duration::from_secs(300));
    let t0 = Instant::now();
    for k in 0..3 {
        assert!(c.take(t0 + Duration::from_secs(k * 10)));
    }
    assert!(
        !c.take(t0 + Duration::from_secs(30)),
        "четвёртая за окно — отказ"
    );
    assert_eq!(
        c.wait(t0 + Duration::from_secs(30)),
        Duration::from_secs(270)
    );
    assert!(c.take(t0 + Duration::from_secs(300)), "первая ушла из окна");
    // отбраковка уже случилась: note записывает без отказа, ждать следующей приходится
    let n = Cap::new(2, Duration::from_secs(60));
    n.note(t0);
    assert_eq!(n.wait(t0), Duration::ZERO);
    n.note(t0 + Duration::from_secs(5));
    assert_eq!(n.wait(t0 + Duration::from_secs(5)), Duration::from_secs(55));
}

#[test]
fn urgent_reserve_keeps_places_for_shards_without_a_connection() {
    let b = Budget::new(4, Duration::from_secs(300));
    b.set_urgent_reserve(2);
    let now = Instant::now();
    assert!(b.try_take_keeping(now, b.urgent_reserve()).is_none());
    assert!(b.try_take_keeping(now, b.urgent_reserve()).is_none());
    // обычным остаётся 4 − 2 = 2 места: третье подключение ждёт, а шард без соединения берёт сразу
    assert!(b.try_take_keeping(now, b.urgent_reserve()).is_some());
    assert!(b.try_take_keeping(now, 0).is_none());
    assert!(b.try_take_keeping(now, 0).is_none());
    assert!(b.try_take_keeping(now, 0).is_some(), "лимит 4 исчерпан");
}

#[test]
fn acquire_blocks_a_covered_shard_at_the_reserve_but_lets_an_uncovered_one_through() {
    let b = Arc::new(Budget::new(4, Duration::from_secs(300)));
    b.set_urgent_reserve(2);
    let stop = AtomicBool::new(false);
    for _ in 0..2 {
        assert!(b.acquire(&[&stop], &|| false).is_some());
    }
    std::thread::scope(|sc| {
        let (b2, stop2) = (Arc::clone(&b), &stop);
        let blocked = sc.spawn(move || b2.acquire(&[stop2], &|| false));
        std::thread::sleep(Duration::from_millis(400));
        assert!(
            !blocked.is_finished(),
            "покрытый шард ждёт: остались только места резерва"
        );
        let t0 = Instant::now();
        assert!(
            b.acquire(&[&stop], &|| true).is_some(),
            "шард без соединения берёт из резерва"
        );
        assert!(t0.elapsed() < Duration::from_secs(1));
        stop.store(true, Relaxed);
        assert!(blocked.join().unwrap().is_none());
    });
}

/// Повтор бури Binance 15.11 18:12 на виртуальном времени (модель на допущении: каждый из 25 шардов открывает путь на замену раз в 17 с — так, чтобы за 3 мин вышло
/// ≈ 261 подключения, как в журнале; в 18:12:18 у 8 шардов обрываются обе линии). Прежний порядок — без резерва и потолков: бюджет 280 из 300 с исчерпан,
/// оборвавшиеся шарды ждут, пока окно освободит места (123–139 с в журнале). Новый — резерв шардам без соединения и потолок отбраковок на шард.
fn storm(reserve: usize, capped: bool) -> (usize, f64, usize) {
    const STORM_S: u64 = 180;
    let budget = Budget::new(280, Duration::from_secs(300));
    budget.set_urgent_reserve(reserve);
    let base = Instant::now();
    let at = |ms: u64| base + Duration::from_millis(ms);
    let caps: Vec<Cap> = (0..25)
        .map(|_| Cap::new(6, Duration::from_secs(300)))
        .collect();
    let mut next_ms: Vec<u64> = (0..25).map(|i| i * 700).collect();
    let mut used_by_storm = 0usize;
    let mut dead_wait_ms: Vec<Option<u64>> = vec![None; 16];
    for step in 0..=(330_000 / 100) {
        let ms = step * 100;
        let now = at(ms);
        for (i, nx) in next_ms.iter_mut().enumerate() {
            if ms < *nx {
                continue;
            }
            // шард в буре: каждая попытка заканчивается отбраковкой пути; потолок отбраковок откладывает следующую
            if let Some(c) = capped.then(|| caps.get(i)).flatten() {
                let w = c.wait(now);
                if !w.is_zero() {
                    *nx = ms + u64::try_from(w.as_millis()).unwrap();
                    continue;
                }
            }
            if budget
                .try_take_keeping(now, budget.urgent_reserve())
                .is_none()
            {
                used_by_storm += 1;
                if let Some(c) = caps.get(i) {
                    c.note(now);
                }
                *nx = ms + 17_000;
            } else {
                *nx = ms + 500;
            }
            if ms > STORM_S * 1000 + 2_000 {
                *nx = u64::MAX;
            }
        }
        // 8 шардов × 2 линии теряют соединения; каждая линия берёт место без остатка
        if ms >= STORM_S * 1000 {
            for w in &mut dead_wait_ms {
                if w.is_none() && budget.try_take_keeping(now, 0).is_none() {
                    *w = Some(ms - STORM_S * 1000);
                }
            }
        }
        if dead_wait_ms.iter().all(Option::is_some) {
            break;
        }
    }
    let max_wait = dead_wait_ms
        .iter()
        .flatten()
        .copied()
        .max()
        .unwrap_or(u64::MAX) as f64
        / 1000.0;
    let connected = dead_wait_ms.iter().flatten().count();
    (used_by_storm, max_wait, connected)
}

#[test]
fn binance_storm_1218_starves_dead_shards_without_the_reserve_and_not_with_it() {
    let (used_old, wait_old, ok_old) = storm(0, false);
    // как в журнале: за 3 мин бури ≈ 261 подключение из 280, оборвавшиеся шарды ждут больше двух минут
    assert!(
        (240..=280).contains(&used_old),
        "буря съедает бюджет: {used_old}"
    );
    assert_eq!(ok_old, 16);
    assert!(
        wait_old >= 100.0,
        "без резерва оборвавшиеся линии ждут {wait_old} с"
    );
    // новый порядок: резерв 2 × 27 шардов и потолок отбраковок 6 на шард за 5 мин
    let (used_new, wait_new, ok_new) = storm(54, true);
    let (used_res, wait_res, ok_res) = storm(54, false);
    eprintln!(
        "storm 18:12 (модель): без резерва и потолков — бюджет занят бурей {used_old}/280, оборвавшиеся линии ждут {wait_old:.1} с; резерв 54 + потолок отбраковок 6 — {used_new}/280, ожидание {wait_new:.1} с; только резерв — {used_res}/280, ожидание {wait_res:.1} с"
    );
    assert_eq!(ok_new, 16);
    assert!(
        wait_new <= 0.2,
        "с резервом линии подключаются сразу: {wait_new} с"
    );
    assert!(
        used_new <= 150,
        "потолок отбраковок: 25 шардов × 6 = 150 за окно, было {used_new}"
    );
    assert!(used_new < used_old);
    // резерв без потолка тоже спасает оборвавшиеся шарды (потолок — второй рубеж, не единственный)
    assert!(ok_res == 16 && wait_res <= 0.2, "{wait_res}");
}

/// Источник кадров книги Binance: каждые 2 мс запись `u` подряд (как `spawn_tape` в tests.rs; здесь свой, чтобы модуль не зависел от приватных помощников).
fn tape_server(
    listener: TcpListener,
    stop: Arc<AtomicBool>,
    paused: Arc<AtomicBool>,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        listener.set_nonblocking(true).unwrap();
        let mut conns = Vec::new();
        while !stop.load(Relaxed) {
            match listener.accept() {
                Ok((s, _)) => {
                    s.set_nonblocking(false).unwrap();
                    let (stop, paused) = (Arc::clone(&stop), Arc::clone(&paused));
                    conns.push(std::thread::spawn(move || {
                        let Ok(mut ws) = tungstenite::accept(s) else { return };
                        ws.get_ref().set_read_timeout(Some(Duration::from_millis(2))).unwrap();
                        let mut u = 0i64;
                        while !stop.load(Relaxed) {
                            if paused.load(Relaxed) {
                                // тишина источника: читаем (pong на наш пинг идёт), кадров не шлём
                                let _ = ws.read();
                                continue;
                            }
                            u += 1;
                            let e = super::events::now_ms();
                            let f = format!(
                                r#"{{"stream":"btcusdt@bookTicker","data":{{"e":"bookTicker","u":{u},"s":"BTCUSDT","ps":"BTCUSDT","b":"1.0","B":"2","a":"1.1","A":"3","T":{e},"E":{e},"st":1}}}}"#
                            );
                            if ws.send(tungstenite::Message::text(f)).is_err() {
                                return;
                            }
                            match ws.read() {
                                Ok(tungstenite::Message::Close(_)) => return,
                                Err(tungstenite::Error::Io(e))
                                    if matches!(e.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) => {}
                                Err(_) => return,
                                Ok(_) => {}
                            }
                        }
                    }));
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(5));
                }
                Err(_) => break,
            }
        }
        for c in conns {
            let _ = c.join();
        }
    })
}

fn events_count(dir: &Path, name: &str) -> usize {
    std::fs::read_to_string(dir.join("events-book00.jsonl"))
        .unwrap_or_default()
        .matches(&format!("\"ev\":\"{name}\""))
        .count()
}

#[test]
fn covered_shard_stops_trying_new_paths_after_the_reject_cap_and_keeps_its_data_flowing() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let stop_src = Arc::new(AtomicBool::new(false));
    let src = tape_server(
        listener,
        Arc::clone(&stop_src),
        Arc::new(AtomicBool::new(false)),
    );
    let dir = tempfile::tempdir().unwrap();
    let plan = ShardPlan {
        name: "book00".into(),
        endpoint: "public".into(),
        streams: vec!["btcusdt@bookTicker".into()],
    };
    let mut opts = ShardOpts::new(dir.path().to_path_buf(), 1_794_000_000);
    (opts.tls, opts.port, opts.flush, opts.probe_window) =
        (false, port, Duration::from_millis(30), (0.2, 0.5));
    opts.rtt_max_ms = 1e-6; // любой путь «медленный»: замена отбраковывается по RTT
    opts.never_slow = true;
    opts.strict_pause = Duration::from_millis(100);
    (opts.reject_cap, opts.cap_window) = (3, Duration::from_secs(120));
    let exch: Arc<dyn Exchange> = Arc::new(Binance);
    let stop = Arc::new(AtomicBool::new(false));
    let budget = Arc::new(Budget::new(500, Duration::from_secs(300)));
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
    while events_count(dir.path(), "path_reject_capped") < 1
        && t0.elapsed() < Duration::from_secs(30)
    {
        std::thread::sleep(Duration::from_millis(50));
    }
    let rejected = events_count(dir.path(), "path_rejected_rtt");
    let (r0, dials0) = (
        metrics.writer().records,
        events_count(dir.path(), "connected"),
    );
    std::thread::sleep(Duration::from_secs(2));
    let (r1, dials1) = (
        metrics.writer().records,
        events_count(dir.path(), "connected"),
    );
    stop.store(true, Relaxed);
    h.join();
    stop_src.store(true, Relaxed);
    src.join().unwrap();
    assert_eq!(rejected, 3, "потолок: три отбраковки за окно, дальше пауза");
    assert_eq!(
        dials1, dials0,
        "за паузу новых подключений нет: бюджет не тратится"
    );
    assert!(r1 > r0, "старая сессия несёт поток");
}

// ---- порог паузы шарда по его истории ----

fn gap_hist(parts: &[(usize, u64)]) -> Vec<u64> {
    parts
        .iter()
        .flat_map(|&(n, v)| std::iter::repeat_n(v, n))
        .collect()
}

#[test]
fn gap_threshold_stays_at_the_floor_for_busy_shards_and_short_history() {
    assert_eq!(GapThreshold::new(Vec::<u64>::new()).ms(), 3000);
    // меньше 60 окон — истории нет, даже если разрывы большие
    assert_eq!(GapThreshold::new(gap_hist(&[(59, 5000)])).ms(), 3000);
    // оживлённый шард: наибольшие разрывы окон 0,3 с
    assert_eq!(GapThreshold::new(gap_hist(&[(300, 300)])).ms(), 3000);
    // 2 × p95 = 2 × 1,2 с < 3 с
    assert_eq!(GapThreshold::new(gap_hist(&[(300, 1200)])).ms(), 3000);
}

#[test]
fn gap_threshold_of_a_thin_shard_is_twice_its_usual_largest_gap() {
    // Bybit trade03 15.11 22 UTC: p50 наибольшего разрыва за 10 с 1,1 с, p99 — 3,4 с (хвост 5 % окон) → порог 6,8 с, пауза 3,3 с — норма шарда
    let g = GapThreshold::new(gap_hist(&[(190, 1100), (10, 3400)]));
    assert_eq!(g.ms(), 6800);
    // настоящая остановка длиннее порога по-прежнему считается
    assert!(12_000 > g.ms());
}

#[test]
fn gap_threshold_ignores_rare_outages_and_caps_the_baseline() {
    // 0,5 % окон — остановка 30 с: p95 приходится на обычный разрыв
    let g = GapThreshold::new(gap_hist(&[(995, 800), (5, 30_000)]));
    assert_eq!(g.ms(), 3000);
    // если остановок 10 % окон, «обычным» они не становятся: база не выше 10 с → порог не выше 20 с
    let g = GapThreshold::new(gap_hist(&[(900, 800), (100, 30_000)]));
    assert_eq!(g.ms(), 20_000);
}

#[test]
fn gap_threshold_follows_new_windows_and_forgets_after_a_day() {
    let mut g = GapThreshold::new(gap_hist(&[(59, 3000)]));
    assert_eq!(g.ms(), 3000);
    g.push(3000); // 60-е окно: история есть, p95 = 3 с → порог 6 с
    assert_eq!(g.ms(), 6000);
    for _ in 0..8_640 {
        g.push(200);
    }
    assert_eq!(
        g.ms(),
        3000,
        "через сутки спокойных окон порог вернулся к нижнему"
    );
}

#[test]
fn gap_seed_reads_the_tail_of_stats_and_skips_bad_lines() {
    let dir = tempfile::tempdir().unwrap();
    assert!(
        gap_seed(dir.path(), "trade03").is_empty(),
        "файла нет — истории нет"
    );
    let mut body = String::from("не json\n{\"msgs\": 5}\n");
    for i in 0..5u64 {
        body.push_str(&format!(
            "{{\"ts_ms\": {i}, \"max_gap_ms\": {}}}\n",
            1000 + i
        ));
    }
    std::fs::write(dir.path().join("stats-trade03.jsonl"), body).unwrap();
    assert_eq!(
        gap_seed(dir.path(), "trade03"),
        vec![1000, 1001, 1002, 1003, 1004]
    );
}

/// Шард на локальном источнике; источник молчит `pause` секунд после первых кадров; `seed` — прежняя статистика шарда (`stats-book00.jsonl`). Возвращает события `gap`.
fn gaps_after_pause(seed: &[u64], pause: Duration) -> Vec<Value> {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let (stop_src, paused) = (
        Arc::new(AtomicBool::new(false)),
        Arc::new(AtomicBool::new(false)),
    );
    let src = tape_server(listener, Arc::clone(&stop_src), Arc::clone(&paused));
    let dir = tempfile::tempdir().unwrap();
    let body: String = seed
        .iter()
        .map(|g| format!("{{\"max_gap_ms\": {g}}}\n"))
        .collect();
    std::fs::write(dir.path().join("stats-book00.jsonl"), body).unwrap();
    let plan = ShardPlan {
        name: "book00".into(),
        endpoint: "public".into(),
        streams: vec!["btcusdt@bookTicker".into()],
    };
    let mut opts = ShardOpts::new(dir.path().to_path_buf(), 1_794_000_000);
    (opts.tls, opts.port, opts.flush, opts.probe_window) =
        (false, port, Duration::from_millis(30), (0.2, 0.5));
    let exch: Arc<dyn Exchange> = Arc::new(Binance);
    let stop = Arc::new(AtomicBool::new(false));
    let budget = Arc::new(Budget::new(100, Duration::from_secs(300)));
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
    while metrics.writer().records < 50 && t0.elapsed() < Duration::from_secs(15) {
        std::thread::sleep(Duration::from_millis(20));
    }
    paused.store(true, Relaxed);
    std::thread::sleep(pause);
    paused.store(false, Relaxed);
    std::thread::sleep(Duration::from_millis(1500));
    stop.store(true, Relaxed);
    h.join();
    stop_src.store(true, Relaxed);
    src.join().unwrap();
    std::fs::read_to_string(dir.path().join("events-book00.jsonl"))
        .unwrap_or_default()
        .lines()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
        .filter(|e| e["ev"] == "gap")
        .collect()
}

#[test]
fn a_pause_inside_the_shards_usual_range_is_not_a_gap_but_the_same_pause_on_a_fresh_shard_is() {
    // шард без истории: порог 3 с, пауза 4,5 с — потеря, как раньше
    let fresh = gaps_after_pause(&[], Duration::from_millis(4500));
    assert_eq!(fresh.len(), 1, "{fresh:?}");
    assert_eq!(fresh[0]["thr_ms"], 3000);
    // разреженный шард: порог 6,8 с по его истории — та же пауза не потеря
    let thin = gap_hist(&[(190, 1100), (10, 3400)]);
    let g = gaps_after_pause(&thin, Duration::from_millis(4500));
    assert!(g.is_empty(), "{g:?}");
}

// ---- health ----

#[test]
fn health_reports_what_rest_sees_against_the_subscription() {
    let live = PoolHealth {
        refresh_s: 300,
        hold_s: 86_400,
        last_try_ms: 900_000,
        last_ok_ms: 900_000,
        rest_symbols: 600,
        eligible: 520,
        not_subscribed: 3,
        not_subscribed_qv_usd: 1_234_567.4,
        missing_at_poll: 3,
        missing_at_poll_qv_usd: 1_234_567.4,
        missing_top: vec![("BARDUSDT".into(), 165_800_000.2)],
        added_total: 4,
        removed_total: 1,
        pool: 517,
        ..PoolHealth::default()
    };
    let ctx = HealthCtx {
        exchange: "binance",
        pool: HashSet::new(),
        version: "t".into(),
        live: Some(live),
    };
    let v = build(&[], &ctx, 960_000, &mut ProcSampler::default());
    assert_eq!(v["symbols"]["not_subscribed"], 3);
    assert_eq!(v["symbols"]["not_subscribed_qv_usd"], 1_234_567.0);
    assert_eq!(v["pool"]["rest_eligible"], 520);
    assert_eq!(v["pool"]["rest_age_s"], 60);
    assert_eq!(v["pool"]["missing_top"][0][0], "BARDUSDT");
    assert_eq!(v["pool"]["added_total"], 4);
    // пул фиксирован на старте — раздела нет
    let ctx = HealthCtx {
        exchange: "binance",
        pool: HashSet::new(),
        version: "t".into(),
        live: None,
    };
    let v = build(&[], &ctx, 960_000, &mut ProcSampler::default());
    assert!(v["pool"].is_null() && v["symbols"].get("not_subscribed").is_none());
}
