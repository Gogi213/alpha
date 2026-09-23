use super::*;
use crate::lob::strategy::EntryLadder;

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() < 1e-9
}

/// Предрегистрация буквально: вход мейкером живёт 2 с, выход ровно
/// на t₀ + 10 с. Числа — константы, а не параметры вызова.
#[test]
fn decision20_timing_is_frozen_in_constants() {
    assert_eq!(ENTRY_TTL_NS, 2_000_000_000);
    assert_eq!(HOLD_NS, 10_000_000_000);
    const {
        assert!(HOLD_NS > ENTRY_TTL_NS, "выход обязан быть позже снятия");
    }
}

/// Сторона по σ (Decision 14): бид — шорт, аск — лонг. Зеркальная пара
/// при зеркальных книгах даёт зеркальные цены входа.
#[test]
fn side_follows_sigma_and_entry_rests_on_own_side() {
    assert_eq!(entry_side(SIGMA_SHORT), Some(HbtSide::Sell));
    assert_eq!(entry_side(SIGMA_LONG), Some(HbtSide::Buy));
    assert_eq!(entry_side(0), None);
    // Шорт — на лучший аск, лонг — на лучший бид (Decision 19).
    assert_eq!(entry_price(HbtSide::Sell, 100.0, 101.0), Some(101.0));
    assert_eq!(entry_price(HbtSide::Buy, 100.0, 101.0), Some(100.0));
    // Зеркало: перемена сторон книги меняет цены местами.
    assert_eq!(
        entry_price(HbtSide::Sell, 100.0, 101.0),
        entry_price(HbtSide::Buy, 101.0, 100.0)
    );
    assert_eq!(entry_price(HbtSide::Buy, f64::NAN, 101.0), None);
}

/// Выход — тейкер пересекающим лимитом: лонг по биду, шорт по аску.
#[test]
fn exit_takes_on_own_side_book() {
    assert_eq!(exit_price(HbtSide::Buy, 100.0, 101.0), Some(100.0));
    assert_eq!(exit_price(HbtSide::Sell, 100.0, 101.0), Some(101.0));
    assert_eq!(exit_price(HbtSide::Buy, 100.0, f64::NAN), None);
}

/// `Fill` синтетики: полное исполнение (F3), поэтому `entry_vwap == entry_px`
/// и `fill_frac == 1.0` — прежние числа формулы не меняются.
fn fill_at(dir: i8, entry_px: f64, exit_px: f64) -> Fill {
    Fill {
        dir,
        entry_px,
        exit_px,
        qty: 1.0,
        entry_taker: false,
        exit_taker: true,
        entry_vwap: entry_px,
        fill_frac: 1.0,
        legs_filled: 1,
        legs_rejected: 0,
        fill_by_cross: false,
    }
}

/// Формула круга буквально: направленный возврат минус комиссии по ногам
/// (В-63: вход мейкер 1.26 + выход тейкер 3.15 = 4.41 bps).
/// Лонг 100 → 101: +100 bps валом, 92.5 чистыми.
#[test]
fn roundtrip_formula_is_exact_on_synthetic_fill() {
    let long = fill_at(1, 100.0, 101.0);
    assert!(close(roundtrip_net_bps(&long).unwrap(), 95.59));
    let short = fill_at(-1, 101.0, 100.0);
    // Зеркало в уровнях не симметрично в bps: база другая (101, не 100).
    assert!(close(
        roundtrip_net_bps(&short).unwrap(),
        10_000.0 / 101.0 - 4.41
    ));
    let loss = fill_at(1, 100.0, 99.0);
    assert!(close(roundtrip_net_bps(&loss).unwrap(), -104.41));
    assert_eq!(roundtrip_net_bps(&fill_at(1, 0.0, 101.0)), None);
    assert_eq!(roundtrip_net_bps(&fill_at(0, 100.0, 101.0)), None);
    assert_eq!(roundtrip_net_bps(&fill_at(1, f64::NAN, 101.0)), None);
    // F3: доходность считается по `entry_vwap`, а не по `entry_px` — на
    // частичном входе по другой средней цене число меняется.
    let partial = Fill {
        entry_vwap: 99.0,
        ..fill_at(1, 100.0, 101.0)
    };
    assert!(close(
        roundtrip_net_bps(&partial).unwrap(),
        (101.0 - 99.0) / 99.0 * 10_000.0 - 4.41
    ));
}

/// PnL-кривая как функция от входов: накопление по шагам, без недели.
#[test]
fn pnl_curve_accumulates_per_fill_without_a_week() {
    let fills = [fill_at(1, 100.0, 101.0), fill_at(1, 100.0, 99.0)];
    let curve = pnl_curve_bps(&fills).unwrap();
    assert_eq!(curve.len(), 2);
    assert!(close(curve[0], 95.59));
    assert!(close(curve[1], 95.59 - 104.41));
    assert!(close(mean_net_bps(&fills).unwrap(), (95.59 - 104.41) / 2.0));
    assert_eq!(pnl_curve_bps(&[]), None, "пусто — нет кривой, а не ноль");
    assert_eq!(mean_net_bps(&[]), None);
    let bad = [fills[0], fill_at(1, 0.0, 1.0)];
    assert_eq!(
        pnl_curve_bps(&bad),
        None,
        "битый круг травит кривую, а не выкидывается молча"
    );
}

/// Обязательная колонка пропусков: причины считаются раздельно и
/// печатаются обе, даже нулевые.
#[test]
fn miss_columns_are_split_by_reason() {
    let mut ledger = MissLedger::default();
    ledger.record(MissReason::EntryTimeout);
    ledger.record(MissReason::EntryTimeout);
    ledger.record(MissReason::PositionBusy);
    assert_eq!(ledger.timeout, 2);
    assert_eq!(ledger.busy, 1);
    assert_eq!(ledger.total(), 3);
    let col = ledger.format_column();
    assert!(col.contains("missed_timeout=2"), "колонка обязана: {col}");
    assert!(col.contains("missed_busy=1"), "колонка обязана: {col}");
    assert!(
        MissLedger::default()
            .format_column()
            .contains("missed_timeout=0"),
        "нулевая колонка тоже печатается"
    );
}

/// Сравнение с таблицей профилей: разность реализации и таблицы,
/// отказы — `None`.
#[test]
fn table_comparison_is_realized_minus_table_estimate() {
    let table = Some(TableEstimate {
        net_bps: Some(5.0),
        net_fill_bps: Some(7.0),
        net_fill_not_measured: false,
    });
    let c = compare_with_table(table, Some(92.5));
    assert!(close(c.diff_net_fill_bps.unwrap(), 85.5));
    assert_eq!(compare_with_table(None, Some(1.0)).diff_net_fill_bps, None);
    assert_eq!(
        compare_with_table(
            Some(TableEstimate {
                net_bps: Some(1.0),
                net_fill_bps: Some(1.0),
                net_fill_not_measured: false,
            }),
            None
        )
        .diff_net_fill_bps,
        None
    );
    let line = c.format_line();
    assert!(
        line.contains("vs_table"),
        "строка обязана называться: {line}"
    );
    assert!(line.contains("diff=85.5000"), "разность на месте: {line}");
}

/// Координатор: `net_fill` таблицы `not_measured` — сравнение падает на
/// `net_bps`, а колонка печатает `not_measured` буквально, не `none`.
#[test]
fn not_measured_net_fill_falls_back_to_net_bps_for_the_diff() {
    let table = Some(TableEstimate {
        net_bps: Some(10.0),
        net_fill_bps: None,
        net_fill_not_measured: true,
    });
    let c = compare_with_table(table, Some(92.5));
    assert!(close(c.diff_net_fill_bps.unwrap(), 82.5), "{c:?}");
    assert_eq!(c.table_net_fill_bps, None);
    let line = c.format_line();
    assert!(
        line.contains("table_net_fill=not_measured"),
        "not_measured обязан печататься буквально: {line}"
    );
}

/// Гейт G4 буквально: положителен при медиане и остаётся положительным
/// при p95 — проход; иначе (включая отсутствие данных) красный.
#[test]
fn g4_passes_only_when_positive_at_both_latencies() {
    assert!(decide_g4(Some(1.0), Some(0.5)).is_pass());
    assert!(!decide_g4(Some(1.0), Some(-0.5)).is_pass());
    assert!(!decide_g4(Some(-1.0), Some(1.0)).is_pass());
    assert!(!decide_g4(Some(0.0), Some(1.0)).is_pass());
    assert!(!decide_g4(None, Some(1.0)).is_pass());
    assert!(!decide_g4(Some(1.0), None).is_pass());
    assert!(!decide_g4(Some(f64::NAN), Some(1.0)).is_pass());
    assert_eq!(decide_g4(Some(0.1), Some(0.1)), G4Verdict::Pass);
    assert_eq!(decide_g4(Some(0.1), Some(0.0)), G4Verdict::Red);
}

/// RTT 6.4 → задержка: вся измеренная RTT на входном плече, ответ — ноль.
/// Момент подтверждения совпадает с измеренным, очередь — консервативно.
#[test]
fn rtt_maps_wholly_onto_entry_leg() {
    assert_eq!(latency_from_rtt(1_500_000), (1_500_000, 0));
    assert_eq!(latency_from_rtt(0), (0, 0));
    assert_eq!(latency_from_rtt(-5), (0, 0));
}

/// В-68: задержка по типу запроса — снятие, рыночный, лимитка — так, как крейт
/// сам помечает запросы (`req == Canceled`, `order_type == Market`).
#[test]
fn measured_latency_picks_place_cancel_taker_by_request_kind() {
    let lat = ExecLatency {
        place_ns: 4_200_000,
        cancel_ns: 3_980_000,
        taker_ns: 5_650_000,
    };
    let mut model = MeasuredLatency(lat);
    let mut order = Order::new(
        1,
        100,
        1.0,
        1.0,
        HbtSide::Buy,
        OrdType::Limit,
        TimeInForce::GTC,
    );
    order.req = Status::New;
    assert_eq!(model.entry(0, &order), 4_200_000);
    assert_eq!(model.response(0, &order), 0);
    order.req = Status::Canceled;
    assert_eq!(model.entry(0, &order), 3_980_000);
    let mut market = Order::new(
        2,
        100,
        1.0,
        1.0,
        HbtSide::Buy,
        OrdType::Market,
        TimeInForce::IOC,
    );
    market.req = Status::New;
    assert_eq!(model.entry(0, &market), 5_650_000);
    // Одно число — прежняя форма В-37: всем одинаково.
    let mut uni = MeasuredLatency(ExecLatency::uniform(20_000_000));
    assert_eq!(uni.entry(0, &order), 20_000_000);
    assert_eq!(uni.entry(0, &market), 20_000_000);
}

#[test]
fn exec_latency_parses_single_number_and_triple_and_prints_back() {
    let uni: ExecLatency = "20000000".parse().unwrap();
    assert_eq!(uni, ExecLatency::uniform(20_000_000));
    assert!(uni.is_uniform());
    assert_eq!(uni.to_string(), "20000000");
    assert_eq!(uni.provenance(), "assumed(В-37)");
    let tri: ExecLatency = "taker=5650000, place=4200000,cancel=3980000"
        .parse()
        .unwrap();
    assert_eq!(
        tri,
        ExecLatency {
            place_ns: 4_200_000,
            cancel_ns: 3_980_000,
            taker_ns: 5_650_000
        }
    );
    assert_eq!(
        tri.to_string(),
        "place=4200000,cancel=3980000,taker=5650000"
    );
    assert_eq!(tri.provenance(), "measured(lob latency, В-68)");
    assert_eq!(tri.to_string().parse::<ExecLatency>().unwrap(), tri);
    assert!(
        "place=1,cancel=2".parse::<ExecLatency>().is_err(),
        "без taker — отказ"
    );
    assert!("place=1,cancel=2,taker=x".parse::<ExecLatency>().is_err());
    assert!("foo=1,cancel=2,taker=3".parse::<ExecLatency>().is_err());
}

/// Done-condition читается в строках отчёта профиля: заполнения, обе
/// колонки пропусков, `fill`/`net_fill`, сравнение с таблицей, G4.
#[test]
fn profile_report_carries_every_done_item() {
    let median = ProfileRun {
        signals: 3,
        fills: vec![fill_at(1, 100.0, 101.0)],
        misses: MissLedger {
            timeout: 1,
            busy: 1,
        },
        observations: vec![
            FillObservation {
                day_cluster: 0,
                net_bps: 92.5,
                filled: true,
            },
            FillObservation {
                day_cluster: 0,
                net_bps: 0.0,
                filled: false,
            },
            FillObservation {
                day_cluster: 0,
                net_bps: 0.0,
                filled: false,
            },
        ],
        incomplete: false,
    };
    let p95 = ProfileRun {
        signals: 3,
        fills: vec![],
        misses: MissLedger {
            timeout: 3,
            busy: 0,
        },
        observations: vec![
            FillObservation {
                day_cluster: 0,
                net_bps: 0.0,
                filled: false,
            };
            3
        ],
        incomplete: false,
    };
    let table = Some(TableEstimate {
        net_bps: Some(5.0),
        net_fill_bps: Some(10.0),
        net_fill_not_measured: false,
    });
    let report = build_profile_report("cross:SOL|eaten|near".to_string(), median, p95, table);
    let text = report.summary_lines().join("\n");
    assert!(text.contains("profile=cross:SOL|eaten|near"), "{text}");
    assert!(text.contains("fills=1"), "{text}");
    assert!(text.contains("missed_timeout=1"), "{text}");
    assert!(text.contains("missed_busy=1"), "{text}");
    assert!(text.contains("vs_table"), "{text}");
    assert!(text.contains("pnl_bps"), "{text}");
    assert!(text.contains("fill="), "{text}");
    assert!(text.contains("net_fill="), "{text}");
    assert!(
        !report.g4.is_pass(),
        "p95 без заполнений — не может быть Pass: {text}"
    );
    assert!(text.contains("G4 RED"), "{text}");
}

// -----------------------------------------------------------------------
// Очередь RiskAdverseQueueModel на фикстуре крейта: позиция двигается
// только сделками на той же цене (самое консервативное допущение).
// -----------------------------------------------------------------------

/// Очередь встаёт за видимым объёмом, сделка на чужой цене её не двигает,
/// сделка на своей — двигает, исполнение наступает при съеденной очереди.
#[test]
fn risk_adverse_queue_moves_only_on_same_price_trades() {
    use hftbacktest::backtest::models::QueueModel;
    use hftbacktest::depth::L2MarketDepth;
    use hftbacktest::types::{OrdType, TimeInForce};

    let mut depth = HashMapMarketDepth::new(1.0, 1.0);
    depth.update_bid_depth(100.0, 5.0, 0);
    let qm = RiskAdverseQueueModel::new();
    let mut order = hftbacktest::types::Order::new(
        7,
        100,
        1.0,
        1.0,
        HbtSide::Buy,
        OrdType::Limit,
        TimeInForce::GTX,
    );
    qm.new_order(&mut order, &depth);
    // Чужая цена: глубина сменилась — очередь лишь подрезается минимумом,
    // фронт тот же.
    qm.depth(&mut order, 9.0, 7.0, &depth);
    assert_eq!(qm.is_filled(&mut order, &depth), 0.0);
    // Своя цена, мало: фронт 5 − 3 = 2 — исполнения нет.
    qm.trade(&mut order, 3.0, &depth);
    assert_eq!(qm.is_filled(&mut order, &depth), 0.0);
    // Своя цена, добивка: фронт 2 − 3 = −1 — исполнен весь лот.
    qm.trade(&mut order, 3.0, &depth);
    assert_eq!(qm.is_filled(&mut order, &depth), 1.0);
}

// -----------------------------------------------------------------------
// Сквозной прогон драйвера на синтетике крейта: фид собирается руками,
// неделя не нужна. Мотор — `strategy::on_event`, не копия экономики.
// -----------------------------------------------------------------------

use hftbacktest::types::{
    EXCH_ASK_DEPTH_EVENT, EXCH_BID_DEPTH_EVENT, EXCH_BUY_TRADE_EVENT, EXCH_EVENT,
    EXCH_SELL_TRADE_EVENT, LOCAL_ASK_DEPTH_EVENT, LOCAL_BID_DEPTH_EVENT, LOCAL_BUY_TRADE_EVENT,
    LOCAL_EVENT, LOCAL_SELL_TRADE_EVENT,
};

/// Флаг глубины, видимый обеим сторонам бэктеста: локальной (по local_ts)
/// и биржевой (по exch_ts). Экспорт пишет только LOCAL-флаги, здесь оба —
/// иначе одна из сторон фид не увидит.
fn depth_ev(bid: bool) -> u64 {
    if bid {
        LOCAL_BID_DEPTH_EVENT | EXCH_BID_DEPTH_EVENT
    } else {
        LOCAL_ASK_DEPTH_EVENT | EXCH_ASK_DEPTH_EVENT
    }
}

fn trade_ev(sell: bool) -> u64 {
    if sell {
        LOCAL_SELL_TRADE_EVENT | EXCH_SELL_TRADE_EVENT
    } else {
        LOCAL_BUY_TRADE_EVENT | EXCH_BUY_TRADE_EVENT
    }
}

fn depth_at(exch_ts: i64, bid: bool, px: f64, qty: f64) -> Event {
    Event {
        ev: depth_ev(bid),
        exch_ts,
        local_ts: exch_ts + 500,
        px,
        qty,
        order_id: 0,
        ival: 0,
        fval: 0.0,
    }
}

fn trade_at(exch_ts: i64, sell: bool, px: f64, qty: f64) -> Event {
    Event {
        ev: trade_ev(sell) | EXCH_EVENT | LOCAL_EVENT,
        exch_ts,
        local_ts: exch_ts + 500,
        px,
        qty,
        order_id: 0,
        ival: 0,
        fval: 0.0,
    }
}

/// Конфиг прогона по умолчанию для тестов: прежний движок (`risk-adverse`),
/// размер 1 лот. Частичное исполнение тесты включают своим `DriveConfig` с
/// `QueueModelKind::Prob`.
fn drive_cfg() -> DriveConfig {
    DriveConfig {
        order_qty: 1.0,
        first_order_id: 1,
        queue_model: QueueModelKind::RiskAdverse,
    }
}

/// Секунда в наносекундах для читаемости сценариев.
const S: i64 = 1_000_000_000;

/// Лонг по σ=+1: вход мейкером на бид 100, сделки съедают очередь за 2 с,
/// книга уходит вверх с живым спредом, выход тейкером по 101. Итог:
/// fills=1, обе колонки пропусков на месте, позиция плоская.
#[test]
fn driver_closes_a_maker_round_trip_on_synthetic_feed() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        trade_at(S + S / 2, true, 100.0, 3.0),
        trade_at(S + 4 * S / 5, true, 100.0, 3.0),
        // Книга обязана держать спред: залоченная (бид ≥ аск) глубина
        // крейта прячет пересечённую сторону, и выхода не будет.
        depth_at(10 * S + 9 * S / 10, true, 101.0, 5.0),
        depth_at(10 * S + 9 * S / 10, false, 102.0, 5.0),
        depth_at(30 * S, false, 103.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        1.0,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let rep = drive_profile(
        &mut hbt,
        0,
        &[Signal {
            t0_ns: S,
            sigma: SIGMA_LONG,
        }],
        &drive_cfg(),
    )
    .unwrap();
    assert!(!rep.incomplete, "фид длиннее выхода с запасом");
    assert_eq!(rep.signals, 1);
    assert_eq!(rep.fills.len(), 1, "вход исполнился за 2 с");
    assert_eq!(rep.misses, MissLedger::default());
    let fill = rep.fills[0];
    assert_eq!(fill.dir, 1);
    assert!(close(fill.entry_px, 100.0));
    assert!(close(fill.exit_px, 101.0));
    // В-63: вход отстоял в книге — мейкер по флагу крейта; комиссии по ногам.
    assert!(!fill.entry_taker, "вход лимитом обязан быть мейкерским");
    assert!(close(
        rep.mean_net_bps().unwrap(),
        100.0 - leg_fee_bps(fill.entry_taker) - leg_fee_bps(fill.exit_taker)
    ));
    assert_eq!(hbt.position(0), 0.0, "позиция плоская после выхода");
    assert_eq!(rep.observations.len(), 1);
    assert!(rep.observations[0].filled);
}

/// `with_backtest_over` (сетка форм без копии событий) даёт тот же круг, что
/// копирующий `build_backtest`, и несколько потоков могут гнать свои
/// `Backtest` над одним `&[Event]` одновременно — буфер только читается.
#[test]
fn shared_buffer_backtest_matches_copying_one_and_survives_threads() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        trade_at(S + S / 2, true, 100.0, 3.0),
        trade_at(S + 4 * S / 5, true, 100.0, 3.0),
        depth_at(10 * S + 9 * S / 10, true, 101.0, 5.0),
        depth_at(10 * S + 9 * S / 10, false, 102.0, 5.0),
        depth_at(30 * S, false, 103.0, 5.0),
    ];
    let signals = [Signal {
        t0_ns: S,
        sigma: SIGMA_LONG,
    }];
    let mut copied = build_backtest(
        &feed,
        1.0,
        1.0,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let want = drive_profile(&mut copied, 0, &signals, &drive_cfg()).unwrap();
    let got = with_backtest_over(
        &feed,
        1.0,
        1.0,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
        |bt| drive_profile(bt, 0, &signals, &drive_cfg()).unwrap(),
    );
    assert_eq!(got.fills, want.fills, "общий буфер — те же сделки");
    assert_eq!(got.misses, want.misses);
    assert_eq!(got.observations.len(), want.observations.len());

    let feed_ref: &[Event] = &feed;
    let per_thread: Vec<_> = std::thread::scope(|scope| {
        let handles: Vec<_> = (0..4)
            .map(|_| {
                scope.spawn(move || {
                    with_backtest_over(
                        feed_ref,
                        1.0,
                        1.0,
                        ExecLatency::uniform(1_000_000),
                        QueueModelKind::RiskAdverse,
                        |bt| drive_profile(bt, 0, &signals, &drive_cfg()).unwrap().fills,
                    )
                })
            })
            .collect();
        handles.into_iter().map(|h| h.join().unwrap()).collect()
    });
    for fills in per_thread {
        assert_eq!(
            fills, want.fills,
            "потоки над одним буфером не мешают друг другу"
        );
    }
    assert_eq!(feed[2].px, 100.0, "буфер после прогонов не тронут");
}

/// Без сделок очередь не двигается: вход снимается через 2 с и считается
/// пропуском именно по таймауту, а не по занятости.
#[test]
fn driver_counts_an_unfilled_entry_as_timeout_miss() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        depth_at(15 * S, false, 102.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        1.0,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let rep = drive_profile(
        &mut hbt,
        0,
        &[Signal {
            t0_ns: S,
            sigma: SIGMA_LONG,
        }],
        &drive_cfg(),
    )
    .unwrap();
    assert!(!rep.incomplete);
    assert_eq!(rep.fills.len(), 0);
    assert_eq!(rep.misses.timeout, 1);
    assert_eq!(rep.misses.busy, 0);
    assert_eq!(rep.mean_net_bps(), None);
    assert_eq!(hbt.position(0), 0.0);
    assert_eq!(rep.observations.len(), 1);
    assert!(!rep.observations[0].filled);
}

/// Сигнал при открытой позиции — пропуск по занятости, а не второй вход:
/// одна позиция за раз, в очередь не ставится.
#[test]
fn driver_counts_a_signal_inside_position_as_busy_miss() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        trade_at(S + S / 2, true, 100.0, 3.0),
        trade_at(S + 4 * S / 5, true, 100.0, 3.0),
        depth_at(10 * S + 9 * S / 10, true, 101.0, 5.0),
        depth_at(10 * S + 9 * S / 10, false, 102.0, 5.0),
        depth_at(30 * S, false, 103.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        1.0,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let rep = drive_profile(
        &mut hbt,
        0,
        &[
            Signal {
                t0_ns: S,
                sigma: SIGMA_LONG,
            },
            Signal {
                t0_ns: 5 * S,
                sigma: SIGMA_LONG,
            },
        ],
        &drive_cfg(),
    )
    .unwrap();
    assert!(!rep.incomplete);
    assert_eq!(rep.fills.len(), 1, "второй вход не ставился");
    assert_eq!(rep.misses.timeout, 0);
    assert_eq!(rep.misses.busy, 1);
    assert_eq!(hbt.position(0), 0.0);
    assert_eq!(rep.observations.len(), 2);
}

/// Граница ядра как у издержек и экспорта: чистому ядру стратегии нечего
/// делать в транспорте, стеночных часах и площадке. Имена заметаются
/// склейкой, чтобы сам тест запрет не триггерил.
#[test]
fn module_stays_detached_from_transport_and_clocks() {
    const SRC: &str = include_str!("../backtest.rs");
    let banned = [
        concat!("tok", "io"),
        concat!("Inst", "ant"),
        concat!("System", "Time"),
        concat!("std::", "time"),
        concat!("byb", "it::"),
        concat!("reqw", "est"),
        concat!("tungst", "enite"),
        concat!("elapse", "_bt"),
        concat!("crate::", "feed"),
    ];
    for b in banned {
        assert!(!SRC.contains(b), "исходник тянет запрещённое: {b}");
    }
}

/// Лестница входа (решение владельца 2026-09-13, переписано F4/В-78):
/// исполнившаяся нога остальные **не снимает** — вход копит позицию, пока жив
/// (до `entry_ttl_ns`), и цена входа берётся с исполненных, а не с первой.
/// Здесь дальняя нога (104) исполняется на 2 с, а на 10 с — вторая продажа по
/// 101, которая добирает остальные (приоритет цены по 102..104, очередь по
/// 101): вход набран целиком (`legs_filled = 4`), и только тогда круг уходит
/// в `Holding`. Прежний код снял бы ноги 101..103 при первом исполнении, и
/// набралось бы 0.25 вместо 1.0 — это и есть проверка накопления.
#[test]
fn ladder_entry_accumulates_legs_until_the_entry_is_over() {
    // Стена на бид 100, аск 105: покупки по 101..104 стоят в стороне от
    // рынка. Продажа-агрессор ровно по 104 исполняет **дальнюю** ногу;
    // следующая, по 101, закрывает всю лестницу. Средняя исполненного —
    // 102.5, поэтому стоп (99) и тейк (103) формы сдвинуты на неё: тейк
    // 106, и бид доходит до 106.
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
        trade_at(2 * S, true, 104.0, 5.0),
        trade_at(10 * S, true, 101.0, 5.0),
        depth_at(30 * S, true, 106.0, 5.0),
        depth_at(30 * S, false, 107.0, 5.0),
        // Хвост: ответу на выход нужно событие после срабатывания тейка.
        depth_at(32 * S, false, 107.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        1.0,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let plan = TradePlan::Bounce {
        entry_px: 101.0,
        // Стоп далеко (90): средняя исполненного (102.5) сдвигает и стоп —
        // форма «1:1» поставила бы его выше текущего бида (100) и выбила бы
        // круг сразу, а тест про накопление позиции, не про геометрию стопа.
        stop_px: 90.0,
        take_px: 103.0,
        deadline_ns: 60 * S,
        entry_ttl_ns: 20 * S,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 4,
        grid_step_px: 1.0,
        // F6: лестница формы (`EntryLadder`) — не этот тест; вход прежний.
        ladder: EntryLadder::NONE,
        early_exit_ns: 0,
        level_floor_qty: 0.0,
        band_exit_bps: 0.0,
        level_px: 100.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 0.0,
        eaten_all_pct: 0.0,
        eaten_half_frac: 0.0,
        level_qty: 0.0,
        lot_qty: 1.0,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
    };
    let run = drive_bounce(
        &mut hbt,
        0,
        &[BounceSignal {
            t0_ns: S,
            sigma: SIGMA_LONG,
            plan,
            profile: 0,
        }],
        &drive_cfg(),
    )
    .unwrap();

    assert!(
        !run.incomplete,
        "круг обязан закрыться: fills={} misses={:?} pos={} orders={:?}",
        run.fills.len(),
        run.misses,
        hbt.position(0),
        hbt.orders(0)
            .values()
            .map(|o| (o.order_id, o.status))
            .collect::<Vec<_>>()
    );
    assert_eq!(run.fills.len(), 1, "ровно один круг");
    let fill = run.fills[0];
    // В-78: позиция — сумма исполненных ног (0.25 × 4), а не одна нога, и
    // средняя цена — средневзвешенная по ним (102.5). Прежний код снял бы
    // ноги 101..103 при первом исполнении, и здесь стояло бы 0.25.
    assert_eq!(fill.legs_filled, 4, "накоплены все четыре ноги лестницы");
    assert!(
        close(fill.qty, 1.0),
        "позиция круга — весь вход: {}",
        fill.qty
    );
    assert!(
        close(fill.entry_vwap, 102.5),
        "средняя исполненного входа (101+102+103+104)/4: {}",
        fill.entry_vwap
    );
    assert!(
        close(fill.entry_px, 102.5),
        "простая средняя по исполненным ногам: {}",
        fill.entry_px
    );
    assert_eq!(
        run.fill_reason,
        vec![crate::lob::strategy::ExitReason::Take],
        "выход — тейк, сдвинутый на среднюю (103 + 2.5 → 106)"
    );
    assert!(
        close(fill.exit_px, 106.0),
        "выход по тейку от средней цены: {}",
        fill.exit_px
    );
    assert_eq!(run.fill_signal, vec![0]);
    assert_eq!(hbt.position(0), 0.0, "позиция плоская после выхода");
    let live = hbt
        .orders(0)
        .values()
        .filter(|o| o.status == Status::New || o.status == Status::PartiallyFilled)
        .count();
    assert_eq!(live, 0, "к концу круга живых ног нет");
}

/// B4 (В-58 п. 5): досрочный выход «по прилипанию». Вход исполняется на 2 с,
/// уровень `P = 100` остаётся лучшим бидом, и через `X = 1 с` после входа
/// сделка выходит **по рынку с причиной `Early`** — не стопом, не тейком и не
/// дедлайном (все три здесь недостижимы: стоп 99, тейк 103, дедлайн 60 с).
#[test]
fn early_exit_leaves_a_level_that_sticks_for_x_seconds() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
        // Продажа-агрессор ровно в наш лимит 101 — вход исполнен на 2 с.
        trade_at(2 * S, true, 101.0, 5.0),
        // Уровень держится: бид всё те же 100.
        depth_at(20 * S, true, 100.0, 5.0),
        depth_at(20 * S, false, 105.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        1.0,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let plan = TradePlan::Bounce {
        entry_px: 101.0,
        stop_px: 99.0,
        take_px: 103.0,
        deadline_ns: 60 * S,
        entry_ttl_ns: 20 * S,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_px: 0.0,
        // F6: лестница формы (`EntryLadder`) — не этот тест; вход прежний.
        ladder: EntryLadder::NONE,
        early_exit_ns: S,
        level_floor_qty: 0.0,
        band_exit_bps: 0.0,
        level_px: 100.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 0.0,
        eaten_all_pct: 0.0,
        eaten_half_frac: 0.0,
        level_qty: 0.0,
        lot_qty: 1.0,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
    };
    let run = drive_bounce(
        &mut hbt,
        0,
        &[BounceSignal {
            t0_ns: S,
            sigma: SIGMA_LONG,
            plan,
            profile: 0,
        }],
        &drive_cfg(),
    )
    .unwrap();

    assert!(
        !run.incomplete,
        "круг обязан закрыться: fills={} misses={:?}",
        run.fills.len(),
        run.misses
    );
    assert_eq!(run.fills.len(), 1, "ровно один круг");
    assert_eq!(
        run.fill_reason,
        vec![crate::lob::strategy::ExitReason::Early],
        "причина выхода — прилипание"
    );
    assert_eq!(run.exits.early, 1);
    assert_eq!(run.exits.stop, 0, "стоп 99 не достигнут");
    assert_eq!(run.exits.take, 0, "тейк 103 не достигнут");
    assert_eq!(run.exits.deadline, 0, "дедлайн 60 с не наступил");
    // Выход по рынку: исполнение по лучшему биду 100, а не по тейку 103.
    assert!(
        close(run.fills[0].exit_px, 100.0),
        "выход по рынку на 100, а не по тейку: {}",
        run.fills[0].exit_px
    );
}

/// B4, отрицательный контроль: цена ушла с уровня вверх (`102` против уровня
/// `100` — больше половины тика), значит касание разрешилось, и «прилипания»
/// нет: `Early` не выдаётся, даже когда `X` истёк.
#[test]
fn early_exit_does_not_fire_once_the_price_left_the_level() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
        trade_at(2 * S, true, 101.0, 5.0),
        // Цена ушла с уровня: лучший бид 102.
        depth_at(3 * S, true, 102.0, 5.0),
        depth_at(20 * S, true, 102.0, 5.0),
        depth_at(20 * S, false, 105.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        1.0,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let plan = TradePlan::Bounce {
        entry_px: 101.0,
        stop_px: 99.0,
        take_px: 103.0,
        deadline_ns: 60 * S,
        entry_ttl_ns: 20 * S,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_px: 0.0,
        // F6: лестница формы (`EntryLadder`) — не этот тест; вход прежний.
        ladder: EntryLadder::NONE,
        early_exit_ns: S,
        level_floor_qty: 0.0,
        band_exit_bps: 0.0,
        level_px: 100.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 0.0,
        eaten_all_pct: 0.0,
        eaten_half_frac: 0.0,
        level_qty: 0.0,
        lot_qty: 1.0,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
    };
    let run = drive_bounce(
        &mut hbt,
        0,
        &[BounceSignal {
            t0_ns: S,
            sigma: SIGMA_LONG,
            plan,
            profile: 0,
        }],
        &drive_cfg(),
    )
    .unwrap();

    assert!(
        !run.fill_reason
            .iter()
            .any(|r| matches!(r, crate::lob::strategy::ExitReason::Early)),
        "прилипания нет: цена с уровня ушла — {run:?}"
    );
    assert_eq!(run.exits.early, 0);
}

/// Прогон по сетапам (`drive_bounce_windowed`) даёт тот же `BounceRun`, что
/// сплошной `drive_bounce`: круги, занятые сигналы, время выхода — всё поле в
/// поле. Второй сигнал приходит внутри первого круга — «занято» у обоих.
/// Лестница F4 стоит **без шага** (`grid_step_px = 0`): ноги по одной цене
/// набираются целиком первым же агрессором, средняя равна плановой, и числа
/// стопа/тейка остаются прежними — тест про драйвер, не про геометрию.
#[test]
fn windowed_driver_matches_the_continuous_one_on_a_synthetic_day() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
        // Агрессор-продавец по 101 закрывает все четыре ноги входа.
        trade_at(2 * S, true, 101.0, 5.0),
        depth_at(3 * S, true, 103.0, 5.0),
        trade_at(4 * S, false, 103.0, 5.0),
        depth_at(30 * S, true, 103.0, 5.0),
        depth_at(30 * S, false, 104.0, 5.0),
        // Второй круг: книга вернулась, тот же вход и тот же тейк.
        depth_at(60 * S, true, 100.0, 5.0),
        depth_at(60 * S, false, 105.0, 5.0),
        trade_at(62 * S, true, 101.0, 5.0),
        depth_at(63 * S, true, 103.0, 5.0),
        trade_at(64 * S, false, 103.0, 5.0),
        depth_at(90 * S, true, 103.0, 5.0),
        depth_at(90 * S, false, 104.0, 5.0),
        depth_at(200 * S, false, 104.0, 5.0),
    ];
    let plan = TradePlan::Bounce {
        entry_px: 101.0,
        stop_px: 99.0,
        take_px: 103.0,
        deadline_ns: 60 * S,
        entry_ttl_ns: 20 * S,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 4,
        grid_step_px: 0.0,
        // F6: лестница формы (`EntryLadder`) — не этот тест; вход прежний.
        ladder: EntryLadder::NONE,
        early_exit_ns: 0,
        level_floor_qty: 0.0,
        band_exit_bps: 0.0,
        level_px: 100.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 0.0,
        eaten_all_pct: 0.0,
        eaten_half_frac: 0.0,
        level_qty: 0.0,
        lot_qty: 1.0,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
    };
    let signal = |t0_ns: i64| BounceSignal {
        t0_ns,
        sigma: SIGMA_LONG,
        plan,
        profile: 0,
    };
    let signals = [signal(S), signal(3 * S), signal(61 * S)];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        1.0,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let full = drive_bounce(&mut hbt, 0, &signals, &drive_cfg()).unwrap();
    let windows = SignalWindows::build(&feed, &[S, 3 * S, 61 * S], 1.0, 1.0);
    assert_eq!(windows.len(), 3);
    let win = drive_bounce_windowed(
        &feed,
        &windows,
        &signals,
        &drive_cfg(),
        ExecLatency::uniform(1_000_000),
    )
    .unwrap();
    assert_eq!(win, full, "окна обязаны дать тот же прогон, что сплошной");
    assert_eq!(full.fills.len(), 2, "{full:?}");
    assert_eq!(full.misses.busy, 1, "второй сигнал пришёл внутри круга");
    assert_eq!(full.fill_exit_ns.len(), 2);
    assert!(full.fill_exit_ns[0] < full.fill_exit_ns[1]);

    // G10: память кругов. «Набор A» (все три сигнала) заполняет память, «набор B» (без первого
    // сигнала — у него другая занятость и другая нумерация заявок) берёт круги из неё; итог B
    // обязан совпасть с его же прогоном без памяти, а память — сработать.
    let mut memo = RoundMemo::default();
    let a = drive_bounce_windowed_memo(
        &feed,
        &windows,
        &signals,
        &drive_cfg(),
        ExecLatency::uniform(1_000_000),
        &mut memo,
    )
    .unwrap();
    assert_eq!(a, win, "с пустой памятью прогон тот же");
    let subset = [signal(3 * S), signal(61 * S)];
    let plain_b = drive_bounce_windowed(
        &feed,
        &windows,
        &subset,
        &drive_cfg(),
        ExecLatency::uniform(1_000_000),
    )
    .unwrap();
    let memo_b = drive_bounce_windowed_memo(
        &feed,
        &windows,
        &subset,
        &drive_cfg(),
        ExecLatency::uniform(1_000_000),
        &mut memo,
    )
    .unwrap();
    assert_eq!(
        memo_b, plain_b,
        "круги из памяти обязаны дать тот же прогон"
    );
    let (hits, _) = memo.stats();
    assert!(
        hits >= 1,
        "второй набор обязан взять хотя бы круг 61 с из памяти: {hits}"
    );
}

/// Снимок книги воспроизводит `HashMapMarketDepth` крейта поле в поле,
/// включая перекрещённые «спрятанные» уровни и границы поиска лучшей цены.
#[test]
fn depth_snapshot_rebuilds_the_crate_book_field_by_field() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        depth_at(S, true, 99.0, 2.0),
        depth_at(S, false, 103.0, 1.0),
        // Бид залезает на аск: крейт прячет аск 101, лучший аск уходит на 103.
        depth_at(2 * S, true, 101.0, 4.0),
        depth_at(3 * S, true, 100.0, 0.0),
    ];
    let windows = SignalWindows::build(&feed, &[10 * S], 1.0, 1.0);
    let w = windows.window_at(10 * S).unwrap();
    assert_eq!(w.start, feed.len(), "все строки до t0 — в снимке");
    let mut want = HashMapMarketDepth::new(1.0, 1.0);
    for ev in &feed {
        if ev.is(LOCAL_BID_DEPTH_EVENT) {
            want.update_bid_depth(ev.px, ev.qty, ev.local_ts);
        } else {
            want.update_ask_depth(ev.px, ev.qty, ev.local_ts);
        }
    }
    let got = w.depth.build(1.0, 1.0);
    assert_eq!(got.bid_depth, want.bid_depth);
    assert_eq!(got.ask_depth, want.ask_depth);
    assert_eq!(got.best_bid_tick, want.best_bid_tick);
    assert_eq!(got.best_ask_tick, want.best_ask_tick);
    assert_eq!(got.low_bid_tick, want.low_bid_tick);
    assert_eq!(got.high_ask_tick, want.high_ask_tick);
    assert_eq!(got.best_ask(), 103.0, "спрятанный аск 101 не всплыл");
    assert!(windows.window_at(5 * S).is_none());
    // Строка с биржевой меткой за t0 в снимок не входит, даже если локальная
    // метка уже прошла: биржевая сторона доберёт её из среза сама.
    let late = [
        depth_at(0, true, 100.0, 5.0),
        Event {
            exch_ts: 5 * S,
            local_ts: S,
            ..depth_at(0, true, 101.0, 1.0)
        },
        depth_at(2 * S, true, 102.0, 1.0),
    ];
    let w = SignalWindows::build(&late, &[3 * S], 1.0, 1.0);
    let w = w.window_at(3 * S).unwrap();
    assert_eq!(w.start, 1, "срез начинается со строки с exch_ts > t0");
    assert_eq!(w.depth.bids, vec![(100, 5.0)]);
}

/// Микрозамер фиксированной цены окна (сборка `Backtest` + две книги из
/// снимка + якорь), без событий в срезе. Не гейт — число для решения, что
/// оптимизировать; гонять `cargo test --release -- --ignored bench_window`.
#[test]
#[ignore]
fn bench_window_fixed_cost() {
    for levels in [50usize, 200, 1000] {
        let mut feed = Vec::new();
        for i in 0..levels {
            feed.push(depth_at(0, true, 1000.0 - i as f64, 1.0));
            feed.push(depth_at(0, false, 1001.0 + i as f64, 1.0));
        }
        feed.push(depth_at(10 * S, true, 1000.0, 2.0));
        let windows = SignalWindows::build(&feed, &[5 * S], 1.0, 1.0);
        let w = windows.window_at(5 * S).unwrap();
        let n = 20_000;
        let t = std::time::Instant::now();
        let mut acc = 0i64;
        for _ in 0..n {
            acc += with_backtest_over_window(
                &w.depth,
                5 * S,
                &feed[w.start..],
                1.0,
                1.0,
                ExecLatency::uniform(1_000_000),
                QueueModelKind::RiskAdverse,
                |bt| {
                    bt.elapse(0).unwrap();
                    bt.current_timestamp()
                },
            );
        }
        let per = t.elapsed().as_secs_f64() * 1e6 / n as f64;
        eprintln!("bench_window: levels={levels} per_window={per:.1}us (acc={acc})");
    }
}

/// Микрозамер цены **круга** в окне: вход исполняется через 1 с, тейк через 3 с
/// — около 300 шагов опроса по 10 мс. Отсюда цена одного `elapse` крейта.
/// Гонять `cargo test --release -- --ignored bench_round`.
#[test]
#[ignore]
fn bench_round_cost_in_a_window() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
        trade_at(2 * S, true, 104.0, 5.0),
        depth_at(3 * S, true, 103.0, 5.0),
        trade_at(4 * S, false, 103.0, 5.0),
        depth_at(30 * S, true, 103.0, 5.0),
        depth_at(30 * S, false, 104.0, 5.0),
        depth_at(200 * S, false, 104.0, 5.0),
    ];
    let plan = TradePlan::Bounce {
        entry_px: 101.0,
        stop_px: 99.0,
        take_px: 103.0,
        deadline_ns: 60 * S,
        entry_ttl_ns: 20 * S,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_px: 1.0,
        // F6: лестница формы (`EntryLadder`) — не этот тест; вход прежний.
        ladder: EntryLadder::NONE,
        early_exit_ns: 0,
        level_floor_qty: 0.0,
        band_exit_bps: 0.0,
        level_px: 100.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 0.0,
        eaten_all_pct: 0.0,
        eaten_half_frac: 0.0,
        level_qty: 0.0,
        lot_qty: 1.0,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
    };
    let signals = [BounceSignal {
        t0_ns: S,
        sigma: SIGMA_LONG,
        plan,
        profile: 0,
    }];
    let windows = SignalWindows::build(&feed, &[S], 1.0, 1.0);
    let n = 2_000;
    let t = std::time::Instant::now();
    let mut exit_ns = 0;
    for _ in 0..n {
        let run = drive_bounce_windowed(
            &feed,
            &windows,
            &signals,
            &drive_cfg(),
            ExecLatency::uniform(1_000_000),
        )
        .unwrap();
        exit_ns = run.fill_exit_ns.first().copied().unwrap_or(0);
    }
    let per = t.elapsed().as_secs_f64() * 1e6 / n as f64;
    let polls = (exit_ns - S) / ON_EVENT_POLL_STEP_NS;
    eprintln!(
        "bench_round: per_window={per:.1}us polls~{polls} per_poll~{:.2}us (exit at {:.3}s)",
        (per - 20.0) / polls.max(1) as f64,
        exit_ns as f64 / 1e9
    );
}

/// Микрозамер круга при плотной ленте (обновление далёкого уровня каждые 4 мс,
/// как ~250 событий/с у ZEC): вход лестницей исполняется через 1 с, тейк
/// через 3 с. Отсюда цена события внутри круга против цены шага опроса.
/// Гонять `cargo test --release -- --ignored bench_dense`.
#[test]
#[ignore]
fn bench_dense_round_in_a_window() {
    let mut feed = vec![
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
    ];
    let mut t = 4_000_000i64;
    let mut i = 0i64;
    while t < 30 * S {
        feed.push(depth_at(
            t,
            true,
            80.0 - (i % 20) as f64,
            1.0 + (i % 3) as f64,
        ));
        t += 4_000_000;
        i += 1;
    }
    feed.push(trade_at(2 * S, true, 104.0, 5.0));
    feed.push(depth_at(3 * S, true, 103.0, 5.0));
    feed.push(trade_at(4 * S, false, 103.0, 5.0));
    feed.push(depth_at(30 * S, false, 104.0, 5.0));
    feed.push(depth_at(200 * S, false, 104.0, 5.0));
    feed.sort_by_key(|e| e.local_ts);
    let plan = TradePlan::Bounce {
        entry_px: 101.0,
        stop_px: 99.0,
        take_px: 103.0,
        deadline_ns: 60 * S,
        entry_ttl_ns: 20 * S,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 4,
        grid_step_px: 1.0,
        // F6: лестница формы (`EntryLadder`) — не этот тест; вход прежний.
        ladder: EntryLadder::NONE,
        early_exit_ns: 0,
        level_floor_qty: 0.0,
        band_exit_bps: 0.0,
        level_px: 100.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 0.0,
        eaten_all_pct: 0.0,
        eaten_half_frac: 0.0,
        level_qty: 0.0,
        lot_qty: 1.0,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
    };
    let signals = [BounceSignal {
        t0_ns: S,
        sigma: SIGMA_LONG,
        plan,
        profile: 0,
    }];
    let windows = SignalWindows::build(&feed, &[S], 1.0, 1.0);
    let n = 2_000;
    let t = std::time::Instant::now();
    let mut exit_ns = 0;
    for _ in 0..n {
        let run = drive_bounce_windowed(
            &feed,
            &windows,
            &signals,
            &drive_cfg(),
            ExecLatency::uniform(1_000_000),
        )
        .unwrap();
        exit_ns = run.fill_exit_ns.first().copied().unwrap_or(0);
    }
    let per = t.elapsed().as_secs_f64() * 1e6 / n as f64;
    let polls = (exit_ns - S) / ON_EVENT_POLL_STEP_NS;
    let events = (exit_ns - S) / 4_000_000;
    eprintln!(
        "bench_dense: per_window={per:.1}us polls~{polls} events~{events} → {:.2}us per event+poll pair-ish (exit at {:.3}s)",
        (per - 20.0) / events.max(1) as f64,
        exit_ns as f64 / 1e9
    );
}

/// E7: круг с двумя ногами выхода в драйвере — одна `Fill` на круг, цена
/// выхода средневзвешенная по ногам, в сводке `eaten = 1`, `partial = 1`.
#[test]
fn a_two_leg_exit_is_one_fill_with_a_weighted_exit_price() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, true, 99.0, 10.0),
        depth_at(0, false, 105.0, 5.0),
        // Вход 101 исполняет продажа-агрессор на 2 с.
        trade_at(2 * S, true, 101.0, 5.0),
        // Плотность 100 (5 → 2, 60 %) — половина по рынку в бид 100.
        depth_at(4 * S, true, 100.0, 2.0),
        // Плотность на 100 снята целиком (100 % ≥ 80), лучший бид — 99 (стоял
        // с начала): остаток по рынку в бид 99.
        depth_at(6 * S, true, 98.0, 10.0),
        depth_at(6 * S, true, 100.0, 0.0),
        depth_at(30 * S, true, 98.0, 10.0),
        depth_at(30 * S, false, 105.0, 5.0),
    ];
    let mut hbt = build_backtest(
        &feed,
        1.0,
        1.0,
        ExecLatency::uniform(1_000_000),
        QueueModelKind::RiskAdverse,
    );
    let plan = TradePlan::Bounce {
        entry_px: 101.0,
        stop_px: 90.0,
        take_px: 110.0,
        deadline_ns: 60 * S,
        entry_ttl_ns: 20 * S,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_px: 0.0,
        // F6: лестница формы (`EntryLadder`) — не этот тест; вход прежний.
        ladder: EntryLadder::NONE,
        early_exit_ns: 0,
        level_floor_qty: 0.0,
        band_exit_bps: 0.0,
        level_px: 100.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 50.0,
        eaten_all_pct: 80.0,
        eaten_half_frac: 0.5,
        level_qty: 5.0,
        lot_qty: 1.0,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
    };
    let run = drive_bounce(
        &mut hbt,
        0,
        &[BounceSignal {
            t0_ns: S,
            sigma: SIGMA_LONG,
            plan,
            profile: 0,
        }],
        &DriveConfig {
            order_qty: 2.0,
            first_order_id: 1,
            queue_model: QueueModelKind::RiskAdverse,
        },
    )
    .unwrap();
    assert!(!run.incomplete, "{:?}", run.misses);
    assert_eq!(run.fills.len(), 1, "две ноги — один круг");
    let fill = run.fills[0];
    assert!(close(fill.entry_px, 101.0), "{}", fill.entry_px);
    // F3: полное исполнение одной ноги — `entry_vwap` равен прежней
    // `entry_px`, `qty` — плановому размеру, `fill_frac` — единице.
    assert!(close(fill.entry_vwap, 101.0), "{}", fill.entry_vwap);
    assert!(close(fill.qty, 2.0), "{}", fill.qty);
    assert!(close(fill.fill_frac, 1.0), "{}", fill.fill_frac);
    assert_eq!(fill.legs_filled, 1);
    assert!(!fill.fill_by_cross, "вход исполнен сделкой, а не крестом");
    // Половина по 100, половина по 99 → 99.5.
    assert!(
        close(fill.exit_px, 99.5),
        "средневзвешенная цена выхода: {}",
        fill.exit_px
    );
    assert!(fill.exit_taker);
    assert_eq!(run.exits.eaten, 1);
    assert_eq!(run.exits.partial, 1);
    assert_eq!(run.fill_reason, vec![ExitReason::Eaten]);
    assert_eq!(hbt.position(0), 0.0);
}

// -----------------------------------------------------------------------
// F3 (план 2026-09-20): модель очереди по объёму, частичное исполнение и
// три пути исполнения крейта.
// -----------------------------------------------------------------------

/// План F3: вход лестницей по `entry_px` (`legs` ног с шагом `step`, каждая
/// следующая дальше от плотности), тейк `take_px`, стоп далеко, дедлайн 60 с,
/// срок жизни входа 20 с.
fn f3_plan(entry_px: f64, take_px: f64, legs: u8, step: f64) -> TradePlan {
    TradePlan::Bounce {
        entry_px,
        stop_px: 90.0,
        take_px,
        deadline_ns: 60 * S,
        entry_ttl_ns: 20 * S,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: legs,
        grid_step_px: step,
        // F6: лестница формы (`EntryLadder`) — не этот тест; вход прежний.
        ladder: EntryLadder::NONE,
        early_exit_ns: 0,
        level_floor_qty: 0.0,
        band_exit_bps: 0.0,
        level_px: 100.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 0.0,
        eaten_all_pct: 0.0,
        eaten_half_frac: 0.0,
        level_qty: 0.0,
        lot_qty: 0.1,
        // F7 (Б-75): форма выхода — не используется в тестах гейта.
        exit_eat_pct: 0.0,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
    }
}

#[test]
fn queue_model_kind_parses_and_labels() {
    assert_eq!(
        QueueModelKind::parse("risk-adverse"),
        Ok(QueueModelKind::RiskAdverse)
    );
    assert_eq!(
        QueueModelKind::parse(" risk-adverse "),
        Ok(QueueModelKind::RiskAdverse)
    );
    assert_eq!(
        QueueModelKind::parse("prob:3.0"),
        Ok(QueueModelKind::Prob { n: 3.0 })
    );
    assert_eq!(
        QueueModelKind::label(QueueModelKind::RiskAdverse),
        "risk-adverse"
    );
    assert_eq!(
        QueueModelKind::label(QueueModelKind::Prob { n: 1.5 }),
        "prob:1.5"
    );
    for bad in [
        "",
        "prob",
        "prob:",
        "prob:abc",
        "prob:nan",
        "prob:inf",
        "prob:0",
        "prob:-1",
        "risk-averse",
    ] {
        assert!(
            QueueModelKind::parse(bad).is_err(),
            "{bad:?} обязан быть отказом"
        );
    }
}

/// Путь (1), модель по объёму: заявка исполняется **частично**. Лестница из
/// двух ног по 1 лоту: продажа 0.1 в дальнюю ногу (99, очередь впереди пуста)
/// даёт 0.1 исполнения, а нога 100 исполняется целиком приоритетом цены —
/// позиция возникает, круг закрывается. `qty` несёт реальное исполнение
/// (1.1 из 2.0), `fill_frac` < 1, `entry_vwap` — средневзвешенную цену.
/// Средняя (99.9) сдвигает тейк формы (100) на тик вверх — 101, и бид доходит
/// до него (F4: стоп и тейк считаются от средней цены исполненного, В-78).
#[test]
fn partial_fill_records_real_qty_and_fill_frac() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
        // Продажа 0.1 ровно по 99: впереди в дальней ноге пусто → 0.1
        // исполнения; ближняя нога 100 исполняется целиком (сделка ниже неё).
        trade_at(2 * S, true, 99.0, 0.1),
        // Вход живёт до срока (20 с) — нога 99 стоит недоисполненной, — и
        // круг уходит в `Holding` только после снятия входа. Средняя (99.9 → тик
        // 100) сдвигает тейк формы на тик вверх: 101, и бид доходит до него.
        depth_at(32 * S, true, 101.0, 5.0),
        depth_at(32 * S, false, 105.0, 5.0),
        // Хвост, чтобы ответ на выход успел дойти.
        depth_at(40 * S, false, 105.0, 5.0),
    ];
    let cfg = DriveConfig {
        order_qty: 2.0,
        first_order_id: 1,
        queue_model: QueueModelKind::Prob { n: 3.0 },
    };
    let mut hbt = build_backtest(
        &feed,
        1.0,
        0.1,
        ExecLatency::uniform(1_000_000),
        cfg.queue_model,
    );
    let run = drive_bounce(
        &mut hbt,
        0,
        &[BounceSignal {
            t0_ns: S,
            sigma: SIGMA_LONG,
            plan: f3_plan(99.0, 100.0, 2, 1.0),
            profile: 0,
        }],
        &cfg,
    )
    .unwrap();

    assert!(!run.incomplete, "круг обязан закрыться: {:?}", run.misses);
    assert_eq!(run.fills.len(), 1, "ровно один круг");
    let fill = run.fills[0];
    assert!(
        close(fill.qty, 1.1),
        "исполнено 1.1 из 2.0 (0.1 + 1.0): {}",
        fill.qty
    );
    assert!(
        close(fill.fill_frac, 1.1 / 2.0),
        "доля исполнения круга: {}",
        fill.fill_frac
    );
    assert_eq!(fill.legs_filled, 2, "обе ноги дали исполнение");
    assert!(
        close(fill.entry_vwap, 109.9 / 1.1),
        "средневзвешенная цена входа 0.1×99 + 1.0×100: {}",
        fill.entry_vwap
    );
    assert!(
        close(fill.entry_px, 99.5),
        "простая средняя по ногам остаётся прежней: {}",
        fill.entry_px
    );
    assert!(
        !fill.fill_by_cross,
        "исполнение пришло сделкой на нашей стороне цены — путь (1)/(2), не крест"
    );
    // `Bot::position` крейта здесь **не ноль** — и это ожидаемо: `Local`
    // обновляет позицию только на `Filled`, а частично исполненная нога 99
    // (0.1, снята недоисполненной) в неё не попала (находка F3). Круг вышел на
    // свою позицию 1.1, локальная позиция ушла в −0.1 — тот самый мнимый
    // остаток, из-за которого страховка остатка в драйвере считает теперь
    // **свою** позицию стратегии (F4, В-78).
    assert!(
        close(hbt.position(0), -0.1),
        "локальная позиция крейта не видит частичного исполнения: {}",
        hbt.position(0)
    );
}

/// Путь (3): лучший аск опустился до нашей цены **без сделки** — крейт
/// исполняет весь остаток (`on_best_ask_update`), и круг помечен
/// `fill_by_cross`. Сделки в буфере нет, поэтому детектор видит именно крест.
#[test]
fn fill_by_cross_is_flagged_when_no_trade_could_fill() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
        // Аск 105 снят, аск 102 встал прямо на нашу цену — сделок нет.
        depth_at(2 * S, false, 105.0, 0.0),
        depth_at(2 * S, false, 102.0, 5.0),
        depth_at(30 * S, true, 100.0, 5.0),
        depth_at(30 * S, false, 102.0, 5.0),
    ];
    let cfg = DriveConfig {
        order_qty: 1.0,
        first_order_id: 1,
        queue_model: QueueModelKind::Prob { n: 3.0 },
    };
    let mut hbt = build_backtest(
        &feed,
        1.0,
        0.1,
        ExecLatency::uniform(1_000_000),
        cfg.queue_model,
    );
    let run = drive_bounce(
        &mut hbt,
        0,
        &[BounceSignal {
            t0_ns: S,
            sigma: SIGMA_LONG,
            plan: f3_plan(102.0, 100.0, 1, 0.0),
            profile: 0,
        }],
        &cfg,
    )
    .unwrap();

    assert!(!run.incomplete, "{:?}", run.misses);
    assert_eq!(run.fills.len(), 1);
    let fill = run.fills[0];
    assert!(
        fill.fill_by_cross,
        "исполнение обновлением лучшей цены — путь (3)"
    );
    assert!(
        close(fill.fill_frac, 1.0),
        "крейт отдаёт весь остаток: {}",
        fill.fill_frac
    );
    assert!(!fill.entry_taker, "исполнение лимитной заявки — мейкерское");
    assert_eq!(fill.legs_filled, 1);
}

/// Путь (2): сделка **ниже** нашей цены (в стену) исполняет весь остаток по
/// приоритету цены; сделка в буфере есть, поэтому путь (3) не отмечается —
/// это и отличает счётчик `n_fill_by_cross` от «исполнений без сделки на
/// нашей цене».
#[test]
fn trade_below_our_price_fills_by_priority_and_is_not_a_cross() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
        // Продажа-агрессор ниже нашего лимита 102 — заявка исполняется целиком
        // приоритетом цены.
        trade_at(2 * S, true, 101.0, 7.0),
        depth_at(30 * S, true, 100.0, 5.0),
        depth_at(30 * S, false, 105.0, 5.0),
    ];
    let cfg = DriveConfig {
        order_qty: 3.0,
        first_order_id: 1,
        queue_model: QueueModelKind::Prob { n: 3.0 },
    };
    let mut hbt = build_backtest(
        &feed,
        1.0,
        0.1,
        ExecLatency::uniform(1_000_000),
        cfg.queue_model,
    );
    let run = drive_bounce(
        &mut hbt,
        0,
        &[BounceSignal {
            t0_ns: S,
            sigma: SIGMA_LONG,
            plan: f3_plan(102.0, 100.0, 1, 0.0),
            profile: 0,
        }],
        &cfg,
    )
    .unwrap();

    assert!(!run.incomplete, "{:?}", run.misses);
    assert_eq!(run.fills.len(), 1);
    let fill = run.fills[0];
    assert!(
        close(fill.fill_frac, 1.0),
        "приоритет цены — весь остаток: {}",
        fill.fill_frac
    );
    assert!(
        !fill.fill_by_cross,
        "сделка в буфере была — это путь (2), не (3)"
    );
}
