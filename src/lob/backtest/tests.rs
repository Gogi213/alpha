use super::*;

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

/// Формула круга буквально: направленный возврат минус 7.5 bps круга.
/// Лонг 100 → 101: +100 bps валом, 92.5 чистыми.
#[test]
fn roundtrip_formula_is_exact_on_synthetic_fill() {
    let long = Fill {
        dir: 1,
        entry_px: 100.0,
        exit_px: 101.0,
        qty: 1.0,
    };
    assert!(close(roundtrip_net_bps(&long).unwrap(), 92.5));
    let short = Fill {
        dir: -1,
        entry_px: 101.0,
        exit_px: 100.0,
        qty: 1.0,
    };
    // Зеркало в уровнях не симметрично в bps: база другая (101, не 100).
    assert!(close(
        roundtrip_net_bps(&short).unwrap(),
        10_000.0 / 101.0 - 7.5
    ));
    let loss = Fill {
        dir: 1,
        entry_px: 100.0,
        exit_px: 99.0,
        qty: 1.0,
    };
    assert!(close(roundtrip_net_bps(&loss).unwrap(), -107.5));
    assert_eq!(
        roundtrip_net_bps(&Fill {
            dir: 1,
            entry_px: 0.0,
            exit_px: 101.0,
            qty: 1.0
        }),
        None
    );
    assert_eq!(
        roundtrip_net_bps(&Fill {
            dir: 0,
            entry_px: 100.0,
            exit_px: 101.0,
            qty: 1.0
        }),
        None
    );
    assert_eq!(
        roundtrip_net_bps(&Fill {
            dir: 1,
            entry_px: f64::NAN,
            exit_px: 101.0,
            qty: 1.0
        }),
        None
    );
}

/// PnL-кривая как функция от входов: накопление по шагам, без недели.
#[test]
fn pnl_curve_accumulates_per_fill_without_a_week() {
    let fills = [
        Fill {
            dir: 1,
            entry_px: 100.0,
            exit_px: 101.0,
            qty: 1.0,
        },
        Fill {
            dir: 1,
            entry_px: 100.0,
            exit_px: 99.0,
            qty: 1.0,
        },
    ];
    let curve = pnl_curve_bps(&fills).unwrap();
    assert_eq!(curve.len(), 2);
    assert!(close(curve[0], 92.5));
    assert!(close(curve[1], 92.5 - 107.5));
    assert!(close(mean_net_bps(&fills).unwrap(), (92.5 - 107.5) / 2.0));
    assert_eq!(pnl_curve_bps(&[]), None, "пусто — нет кривой, а не ноль");
    assert_eq!(mean_net_bps(&[]), None);
    let bad = [
        fills[0],
        Fill {
            dir: 1,
            entry_px: 0.0,
            exit_px: 1.0,
            qty: 1.0,
        },
    ];
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

/// Done-condition читается в строках отчёта профиля: заполнения, обе
/// колонки пропусков, `fill`/`net_fill`, сравнение с таблицей, G4.
#[test]
fn profile_report_carries_every_done_item() {
    let median = ProfileRun {
        signals: 3,
        fills: vec![Fill {
            dir: 1,
            entry_px: 100.0,
            exit_px: 101.0,
            qty: 1.0,
        }],
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

fn drive_cfg() -> DriveConfig {
    DriveConfig {
        order_qty: 1.0,
        first_order_id: 1,
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
    let mut hbt = build_backtest(&feed, 1.0, 1.0, 1_000_000);
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
    assert!(close(rep.mean_net_bps().unwrap(), 92.5));
    assert_eq!(hbt.position(0), 0.0, "позиция плоская после выхода");
    assert_eq!(rep.observations.len(), 1);
    assert!(rep.observations[0].filled);
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
    let mut hbt = build_backtest(&feed, 1.0, 1.0, 1_000_000);
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
    let mut hbt = build_backtest(&feed, 1.0, 1.0, 1_000_000);
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

/// Лестница входа (решение владельца 2026-09-13): исполняется **не первая**
/// нога, цена входа берётся с исполненной, остальные снимаются, круг
/// закрывается и не помечается `incomplete` — то, из-за отсутствия чего
/// прогон по пулу показывал `incomplete` на каждой строке.
#[test]
fn ladder_entry_fills_the_far_leg_cancels_the_rest_and_closes_the_round() {
    // Стена на бид 100, аск 105: покупки по 101..104 стоят в стороне от
    // рынка. Продажа-агрессор ровно по 104 исполняет **дальнюю** ногу
    // (условие `px >= 104` выполнено только для неё), дальше бид поднимается
    // до 103 — срабатывает тейк-лимит, и его исполняет покупка-агрессор.
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 105.0, 5.0),
        trade_at(2 * S, true, 104.0, 5.0),
        depth_at(3 * S, true, 103.0, 5.0),
        trade_at(4 * S, false, 103.0, 5.0),
        depth_at(30 * S, true, 103.0, 5.0),
        depth_at(30 * S, false, 104.0, 5.0),
    ];
    let mut hbt = build_backtest(&feed, 1.0, 1.0, 1_000_000);
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
    assert_eq!(run.fills.len(), 1, "исполнилась ровно одна нога");
    let fill = run.fills[0];
    // В этом сценарии агрессор заполняет только свой уровень, поэтому
    // «среднее исполненных ног» равно цене единственной исполнившейся —
    // дальней (104). Первая нога (101) не исполняется: это и проверяется.
    assert!(
        close(fill.entry_px, 104.0),
        "цена входа — с исполнившейся (дальней) ноги, а не с первой: {}",
        fill.entry_px
    );
    assert_eq!(run.fill_signal, vec![0]);
    assert_eq!(hbt.position(0), 0.0, "позиция плоская после выхода");
    let live = hbt
        .orders(0)
        .values()
        .filter(|o| o.status == Status::New || o.status == Status::PartiallyFilled)
        .count();
    assert_eq!(live, 0, "неисполненные ноги лестницы обязаны быть сняты");
}
