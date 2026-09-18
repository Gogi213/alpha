use super::*;
use crate::book::Update;
use crate::lob::strategy::ExitReason;

struct VecFeed(std::vec::IntoIter<FeedEvent>);
impl Feed for VecFeed {
    fn next_event(&mut self) -> Option<FeedEvent> {
        self.0.next()
    }
}

fn book_ev(local_ts_ns: i64, up: Update) -> FeedEvent {
    FeedEvent::Market {
        symbol: 0,
        local_ts_ns,
        parse_latency_ns: None,
        payload: WsEvent::Book(up),
    }
}

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() < 1e-9
}

// -----------------------------------------------------------------------
// `BacktestFillModel` (таск 16) на синтетическом потоке `hftbacktest`
// напрямую (`prime_from_events`) — тот же приём фикстур, что
// `lob::backtest::tests` (сборка `Event` руками, без файла бинлога): это
// тестовый шов, названный doc `prime_from_events`, не второй разбор
// формата.
// -----------------------------------------------------------------------

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

fn depth_at(exch_ts: i64, bid: bool, px: f64, qty: f64) -> HbtEvent {
    HbtEvent {
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

fn trade_at(exch_ts: i64, sell: bool, px: f64, qty: f64) -> HbtEvent {
    HbtEvent {
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

fn ask_level(price_tick: i64, birth_ms: i64) -> LevelRecord {
    LevelRecord {
        side: Side::Ask,
        price_tick,
        birth_ms,
        death_ms: birth_ms + 1,
        lifetime_ms: 1,
        size_max: 1,
        time_to_max_ms: 0,
        size_monotonic: true,
        repeat_count: 0,
        repriced: false,
        death: crate::lob::levels::DeathKind::BelowFraction,
        traded_lots: 0,
        rpi_lots: 0,
    }
}

/// Секунда в наносекундах — тот же приём читаемости, что
/// `lob::backtest::tests::S`.
const S: i64 = 1_000_000_000;

/// Критерий приёмки таска 16: синтетический поток с известными
/// исполнениями — вход А исполняется за 2 с (сделки съедают очередь),
/// вход Б не встречает сделок и снимается по таймауту. Оба сигнала гонит
/// **один** прогон `prime_from_events` (не по одному на уровень —
/// BLOCKERS таска 13), `filled` читает готовый кэш.
#[test]
fn backtest_fill_model_matches_known_executions_on_a_synthetic_feed() {
    let feed = [
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 101.0, 5.0),
        // Вход А (born t=S, аск-уровень → long): сделки на бид съедают
        // очередь мейкера за 2 с (тот же сценарий, что
        // `lob::backtest::tests::driver_closes_a_maker_round_trip_on_
        // synthetic_feed`).
        trade_at(S + S / 2, true, 100.0, 3.0),
        trade_at(S + 4 * S / 5, true, 100.0, 3.0),
        depth_at(10 * S + 9 * S / 10, true, 101.0, 5.0),
        depth_at(10 * S + 9 * S / 10, false, 102.0, 5.0),
        // Вход Б (born t=20*S): книга валидна, но между рождением и
        // t+2с сделок нет — обязан снятся по таймауту.
        depth_at(30 * S, false, 103.0, 5.0),
    ];
    let records = [ask_level(100, 1000), ask_level(200, 20_000)];

    let model = BacktestFillModel::new(1_000_000, 2_000_000, 100_000_000);
    model.prime_from_events("SOLUSDT", &feed, 1.0, 1.0, &records);

    assert_eq!(
        model.filled("SOLUSDT", &records[0], &[]),
        Some(true),
        "вход А обязан исполниться — очередь съедена за 2 с"
    );
    assert_eq!(
        model.filled("SOLUSDT", &records[1], &[]),
        Some(false),
        "вход Б обязан не исполниться — сделок не было"
    );
    // Уровень, которого не было в сессии, — не измерен, не ложный ноль.
    let unseen = ask_level(300, 999_999);
    assert_eq!(model.filled("SOLUSDT", &unseen, &[]), None);
    // Тот же уровень другого символа — отдельный ключ, не измерен.
    assert_eq!(model.filled("ETHUSDT", &records[0], &[]), None);
    assert_eq!(model.label(), "backtest");
    assert_eq!(model.p95_rtt_ns(), 2_000_000);
}

/// Снапшот, потерявший уровень против предыдущего снапшота, обязан
/// явно его обнулить — иначе `HashMapMarketDepth` крейта унаследует
/// цену, которой в книге уже нет (`book::Book::apply` делает то же самое
/// перед применением снапшота, это тот же случай на другом типе).
#[test]
fn snapshot_clears_stale_levels_and_deltas_upsert() {
    let snap1 = Update {
        is_snapshot: true,
        depth: 50,
        u: 1,
        seq: 1,
        cts_ms: 1000,
        bids: vec![
            (100_000_000_000, 5_000_000_000),
            (99_000_000_000, 2_000_000_000),
        ],
        asks: vec![(101_000_000_000, 3_000_000_000)],
    };
    let snap2 = Update {
        is_snapshot: true,
        depth: 50,
        u: 2,
        seq: 2,
        cts_ms: 2000,
        bids: vec![(100_000_000_000, 4_000_000_000)],
        asks: vec![(101_000_000_000, 3_000_000_000)],
    };
    let events = vec![book_ev(1_500, snap1), book_ev(2_500, snap2)];
    let mut feed = VecFeed(events.into_iter());
    let out = events_from_feed(&mut feed);

    assert_eq!(
        out.iter().filter(|e| e.exch_ts == 1_000_000_000).count(),
        3,
        "первый снапшот — только апсерты, стейла ещё нет"
    );
    let cleared = out
        .iter()
        .find(|e| e.exch_ts == 2_000_000_000 && close(e.px, 99.0));
    assert!(
        cleared.is_some(),
        "пропавший из второго снапшота уровень (99.0) обязан обнулиться"
    );
    assert_eq!(cleared.unwrap().qty, 0.0);
    let kept = out
        .iter()
        .rfind(|e| e.exch_ts == 2_000_000_000 && close(e.px, 100.0))
        .unwrap();
    assert_eq!(kept.qty, 4.0, "выживший уровень апсерчен новым размером");
}

/// Дельта с нулевым размером снимает уровень так же, как отсутствие
/// его в снапшоте — оба пути дают одно и то же событие цепочки крейта.
#[test]
fn zero_qty_delta_removes_a_level() {
    let snap = Update {
        is_snapshot: true,
        depth: 50,
        u: 1,
        seq: 1,
        cts_ms: 1000,
        bids: vec![(100_000_000_000, 5_000_000_000)],
        asks: vec![],
    };
    let delta = Update {
        is_snapshot: false,
        depth: 50,
        u: 2,
        seq: 2,
        cts_ms: 1500,
        bids: vec![(100_000_000_000, 0)],
        asks: vec![],
    };
    let events = vec![book_ev(1_000, snap), book_ev(1_200, delta)];
    let mut feed = VecFeed(events.into_iter());
    let out = events_from_feed(&mut feed);
    assert_eq!(out.len(), 2);
    assert_eq!(out[1].qty, 0.0);
    assert!(close(out[1].px, 100.0));
}

/// Реальная шапка `docs/findings/profiles-<дата>.csv` (таск 10,
/// `commands::lob::profiles::HEADER`/`write_row`), записанная литералом:
/// комментарий `#`, 26 колонок, `not_measured` там, где `fill_model=none`
/// не мерил `fill`/`net_fill`. Не старая схема `shortlist::ProfileRow`,
/// которую этот читатель больше не понимает (ревью: не соединялась).
const PROFILES_TASK10_FIXTURE: &str = concat!(
    "# lob profiles: h3_mode=floor warmup_ms=3600000 repeat_window_ms=3600000",
    " alpha=0.005 replications=9999 seed=0 fill_model=none debug\n",
    "profile_id,n,eaten_share,pulled_share,mixed_share,",
    "m_100ms,m_100ms_lower,raw_100ms,unreachable_100ms,",
    "m_1000ms,m_1000ms_lower,raw_1000ms,unreachable_1000ms,",
    "m_10000ms,m_10000ms_lower,raw_10000ms,unreachable_10000ms,",
    "m_60000ms,m_60000ms_lower,raw_60000ms,unreachable_60000ms,",
    "net_bps,fill,net_fill,net_fill_lower,session_start_hours_utc\n",
    "smoke:bid,150,0.3000,0.6000,0.1000,",
    "1.0000,0.5000,1.0000,not_measured,",
    "2.0000,1.0000,2.0000,not_measured,",
    "3.0000,1.0000,3.0000,not_measured,",
    "4.0000,1.0000,4.0000,not_measured,",
    "10.0000,not_measured,not_measured,not_measured,2\n",
    "smoke:ask,120,0.2000,0.7000,0.1000,",
    "1.0000,0.5000,1.0000,not_measured,",
    "2.0000,1.0000,2.0000,not_measured,",
    "3.0000,1.0000,3.0000,not_measured,",
    "4.0000,1.0000,4.0000,not_measured,",
    "5.0000,0.4000,3.0000,1.5000,3\n",
);

/// Таблица профилей читается по пути-параметру на фикстуре формата
/// таска 10, не на боевом файле. `smoke:bid` — колонка `net_fill`
/// `not_measured` (`fill_model=none`): читатель обязан пометить это
/// явно, а не молчать как `None`. `smoke:ask` — обычная измеренная
/// строка, для контраста. Отсутствующий профиль и путь — пусто, не
/// паника.
#[test]
fn read_table_parses_task10_format_and_flags_not_measured() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("profiles-fixture.csv");
    std::fs::write(&path, PROFILES_TASK10_FIXTURE).unwrap();

    let table = read_table(Some(&path)).unwrap();

    let bid = table.get("smoke:bid").expect("профиль обязан найтись");
    assert_eq!(bid.net_bps, Some(10.0));
    assert_eq!(bid.net_fill_bps, None, "not_measured — не число");
    assert!(bid.net_fill_not_measured, "литерал обязан распознаться");

    let ask = table.get("smoke:ask").expect("профиль обязан найтись");
    assert_eq!(ask.net_bps, Some(5.0));
    assert_eq!(ask.net_fill_bps, Some(3.0));
    assert!(!ask.net_fill_not_measured, "измеренная строка — не флаг");

    assert!(!table.contains_key("smoke:missing"), "чужого профиля нет");
    assert!(
        read_table(None).unwrap().is_empty(),
        "без пути — пустая таблица, не паника"
    );
}

/// Сквозная проверка сравнения на фикстуре таска 10: `net_fill`
/// `not_measured` обязан упасть сравнением на `net_bps` (координатор), а
/// не молча дать `None` разности.
#[test]
fn compare_with_table_falls_back_to_net_bps_on_the_task10_fixture() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("profiles-fixture.csv");
    std::fs::write(&path, PROFILES_TASK10_FIXTURE).unwrap();
    let table = read_table(Some(&path)).unwrap();

    let comparison =
        crate::lob::backtest::compare_with_table(table.get("smoke:bid").copied(), Some(92.5));
    assert!(
        (comparison.diff_net_fill_bps.unwrap() - 82.5).abs() < 1e-9,
        "{comparison:?}"
    );
    assert!(comparison.format_line().contains("not_measured"));
}

/// Форма сделки «как в T38»: без пост-онли, трейла и лестницы — тесты B2
/// проверяют геометрию входа/стопа/тейка, а не оси T38.
fn plain_shape() -> PlanShape {
    PlanShape {
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_ticks: 0,
        // Дедлайн по умолчанию — 60 с, как `--deadline-secs` без флага (B3).
        deadline_ns: 60 * 1_000_000_000,
        // Досрочный выход выключен: тесты B2/B3 проверяют геометрию плана.
        early_exit_ns: 0,
    }
}

/// Касание для проверки плана B2: бид, цена уровня `P`, при желании — цена
/// фронтрана перед ним. Остальные поля записаны так, чтобы тест читался:
/// касание длиной 1 с, размер 10 лотов.
fn bounce_touch(price_tick: i64, frontrun_tick: Option<i64>) -> TouchRecord {
    TouchRecord {
        side: Side::Bid,
        price_tick,
        touch_index: 0,
        start_ms: 1_000,
        end_ms: 2_000,
        duration_ms: 1_000,
        level_birth_ms: 0,
        size_at_touch: 10,
        size_max_before: 10,
        traded_during: 0,
        frontrun_lots: if frontrun_tick.is_some() { 5 } else { 0 },
        frontrun_tick,
        swept_lots: 0,
        round_zeros: 2,
        ended_by_death: false,
        stack_levels: 1,
        strength_e2: [-1, -1, -1],
        strength_held_e2: [-1, -1, -1, -1],
        repeat_count: 0,
    }
}

/// Разбирает план сделки-отскока в тройку (вход, стоп, тейк) — иначе тест
/// утонул бы в сопоставлении с образцом.
fn plan_prices(plan: &TradePlan) -> (f64, f64, f64) {
    match plan {
        TradePlan::Bounce {
            entry_px,
            stop_px,
            take_px,
            ..
        } => (*entry_px, *stop_px, *take_px),
        TradePlan::SpreadHold => panic!("отскок обязан быть Bounce"),
    }
}

/// B2 (В-58): вход — от **первого фронтранера**, тейк — 1:1 **от входа**, а
/// стоп — одной из трёх предрегистрированных форм (`before`/`at`/`behind` =
/// `P+1`/`P`/`P−1` по цене уровня). Три формы обязаны давать **разные** планы:
/// иначе вариация стопа ничего не вариирует, и предрегистрация В-58 пуста.
#[test]
fn bounce_plan_enters_at_the_frontrun_and_stops_in_three_forms() {
    let tick = 0.01_f64;
    // P = 10.00, первый фронтранер — 10.05 (тик 1005 перед плотностью бида).
    let touch = bounce_touch(1_000, Some(1_005));

    let mut plans = Vec::new();
    for mode in [StopModeArg::Before, StopModeArg::At, StopModeArg::Behind] {
        let (_, plan) = bounce_plan(&touch, tick, mode, plain_shape());
        plans.push((mode, plan_prices(&plan)));
    }

    // Вход один и тот же во всех трёх — от фронтрана, а не от уровня.
    for (mode, (entry, _, _)) in &plans {
        assert!(
            (entry - 10.05).abs() < 1e-9,
            "{mode:?}: вход обязан быть ценой фронтранера, получено {entry}"
        );
    }
    // Стоп: before P+1, at P, behind P−1; тейк — ровно на столько же выше
    // входа (1:1 от входа, а не от цены уровня).
    let expected = [
        (StopModeArg::Before, 10.01_f64, 10.09_f64),
        (StopModeArg::At, 10.00, 10.10),
        (StopModeArg::Behind, 9.99, 10.11),
    ];
    for (mode, stop, take) in expected {
        let (_, (entry, got_stop, got_take)) = plans
            .iter()
            .find(|(m, _)| *m == mode)
            .unwrap_or_else(|| panic!("{mode:?} потерян"));
        assert!(
            (got_stop - stop).abs() < 1e-9,
            "{mode:?}: стоп {got_stop} вместо {stop}"
        );
        assert!(
            (got_take - take).abs() < 1e-9,
            "{mode:?}: тейк {got_take} вместо {take} (1:1 от входа {entry})"
        );
    }
}

/// B2: фронтрана впереди не было — вход прежний (`P + 1` тик), и с формой
/// стопа T38 (`behind`) это ровно прежняя сделка: стоп `P−1`, тейк `P+3`.
#[test]
fn bounce_plan_without_frontrun_keeps_the_old_entry_and_one_to_one() {
    let tick = 0.01_f64;
    let touch = bounce_touch(1_000, None);
    let (_, plan) = bounce_plan(&touch, tick, StopModeArg::Behind, plain_shape());
    let (entry, stop, take) = plan_prices(&plan);
    assert!((entry - 10.01).abs() < 1e-9, "вход P+1: {entry}");
    assert!((stop - 9.99).abs() < 1e-9, "стоп P−1: {stop}");
    assert!(
        (take - 10.03).abs() < 1e-9,
        "тейк P+3 при таком входе: {take}"
    );
}

/// B3/B4: сетки В-58 зафиксированы **до** данных — значение дедлайна или
/// досрочного выхода вне них это отказ, а не параметр. Проверяется функцией,
/// а не живым прогоном: на боевом корне та же ошибка стоит минут счёта до
/// диагностики (находка R5 аудита 2026-09-17 — отказа не покрывал тест).
#[test]
fn deadline_and_early_exit_values_outside_the_preregistered_grid_are_refused() {
    // Дедлайн: сетка {60, 600, 3600, 7200} с.
    assert_eq!(deadline_ns_from_secs(60).unwrap(), 60 * 1_000_000_000);
    assert_eq!(deadline_ns_from_secs(7_200).unwrap(), 7_200 * 1_000_000_000);
    let err = deadline_ns_from_secs(120).unwrap_err().to_string();
    assert!(
        err.contains("не из предрегистрированной сетки В-58") && err.contains("120"),
        "отказ обязан называть значение и сетку: {err}"
    );
    // Досрочный выход: выключен либо {1, 2, 3} с — четвёртый вариант той же оси.
    assert_eq!(
        early_exit_ns_from_secs(None).unwrap(),
        0,
        "без флага досрочный выход выключен, а не «ноль секунд»"
    );
    for x in EARLY_EXITS_S {
        assert_eq!(early_exit_ns_from_secs(Some(x)).unwrap(), x * 1_000_000_000);
    }
    for bad in [0, 4, 60] {
        let err = early_exit_ns_from_secs(Some(bad)).unwrap_err().to_string();
        assert!(
            err.contains("не из предрегистрированного набора В-58"),
            "{bad}: {err}"
        );
    }
}

/// B4: `--early-exit-secs` доезжает до плана вместе с ценой уровня и шагом
/// цены — без них стратегия не может решить «уровень ещё держит»: тика она не
/// знает, а цена уровня не выводится из входа (вход бывает от фронтранера).
#[test]
fn bounce_plan_carries_the_early_exit_the_level_and_the_tick() {
    let tick = 0.01_f64;
    let touch = bounce_touch(1_000, Some(1_005));
    let shape = PlanShape {
        early_exit_ns: 2 * 1_000_000_000,
        ..plain_shape()
    };
    let (_, plan) = bounce_plan(&touch, tick, StopModeArg::Behind, shape);
    let TradePlan::Bounce {
        early_exit_ns,
        level_px,
        tick_px,
        ..
    } = plan
    else {
        panic!("отскок обязан быть Bounce");
    };
    assert_eq!(early_exit_ns, 2_000_000_000, "X из флага — в план");
    assert!(
        (level_px - 10.0).abs() < 1e-9,
        "уровень касания P = 10.00, а не цена входа: {level_px}"
    );
    assert!((tick_px - 0.01).abs() < 1e-12, "шаг цены: {tick_px}");
}

// -----------------------------------------------------------------------
// B5 (В-58): покруговой дамп `--trades-out` — вход прогона-вердикта.
// -----------------------------------------------------------------------

fn fill(dir: i8, entry_px: f64, exit_px: f64) -> crate::lob::backtest::Fill {
    crate::lob::backtest::Fill {
        dir,
        entry_px,
        exit_px,
        qty: 1.0,
    }
}

fn bounce_run(fills: Vec<crate::lob::backtest::Fill>, reasons: Vec<ExitReason>) -> BounceRun {
    let n = fills.len();
    BounceRun {
        profile: 0,
        signals: n as u64,
        fills,
        fill_signal: (0..n).collect(),
        fill_reason: reasons,
        fill_exit_ns: Vec::new(),
        exits: Default::default(),
        entry_rejected: 0,
        entry_crossed: 0,
        spread_at_entry: Vec::new(),
        submitted_signal: (0..n).collect(),
        busy_signal: Vec::new(),
        busy_wait_ns_max: 0,
        round_ns_max: 0,
        misses: Default::default(),
        observations: Vec::new(),
        incomplete: false,
        residual_flattened: 0,
    }
}

/// Дамп — строка на круг: обе ноги, причина выхода словами и `net_bps` той же
/// арифметикой `roundtrip_net_bps`, что и кривая PnL (второй расчёт разошёлся
/// бы с вердиктом). Круг без числа пишется литералом `not_measured`:
/// потребитель (`lob bounce-verdict`) на нём отказывает, а не выбрасывает
/// наблюдение молча.
#[test]
fn trades_dump_writes_one_row_per_circle_with_its_reason() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("nested").join("ZECUSDT.csv");
    let run = bounce_run(
        vec![fill(1, 100.0, 101.0), fill(-1, 0.0, 101.0)],
        vec![ExitReason::Take, ExitReason::Stop],
    );
    write_trades_csv(&path, "# lob backtest: тест", &run).unwrap();
    let text = std::fs::read_to_string(&path).unwrap();
    let lines: Vec<&str> = text.lines().collect();
    assert_eq!(lines[0], "# lob backtest: тест");
    assert_eq!(
        lines[1],
        "signal_index,dir,entry_px,exit_px,qty,net_bps,reason"
    );
    // 1 % хода минус круг мейкер-тейкер 7.5 bps (Decision 19) = 92.5 bps.
    assert!(
        lines[2].ends_with(",92.500000,take"),
        "первый круг: {}",
        lines[2]
    );
    assert!(
        lines[3].ends_with(",not_measured,stop"),
        "круг с нулевым входом: {}",
        lines[3]
    );
}

/// Причин меньше, чем кругов, — отказ: сдвинутые причины приписали бы кругу
/// чужой выход, а это хуже отсутствующего файла.
#[test]
fn trades_dump_refuses_a_run_whose_reasons_do_not_match_its_circles() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("ZECUSDT.csv");
    let run = bounce_run(
        vec![fill(1, 100.0, 101.0)],
        vec![ExitReason::Take, ExitReason::Stop],
    );
    assert!(write_trades_csv(&path, "# тест", &run).is_err());
    assert!(!path.exists(), "отказ не оставляет половины файла");
}

// -----------------------------------------------------------------------
// Лот круга: явный флаг или `order_size_22a` от пула и цены касания.
// -----------------------------------------------------------------------

/// Пул сессии + цена последнего касания дают тот же `order_size_22a`, что
/// `lob pick` (Decision 22а): минимум площадки поднимается до чека $5, а при
/// нулевом чеке остаётся минимальным лотом.
#[test]
fn pool_order_qty_is_order_size_22a_from_the_pool_and_the_touch_price() {
    let dir = tempfile::tempdir().unwrap();
    let pool = dir.path().join("instruments.csv");
    std::fs::write(
        &pool,
        "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n\
         SOLUSDT,0.01,0.1,0.1,5,9\n\
         AAAUSDT,0.01,0.1,0.1,0,9\n",
    )
    .unwrap();
    // Тик 0.01 в 1e-9, цена касания 100 тиков = $1.00. Чек $5 при шаге 0.1
    // даёт 5.0 лотов = 5e9 e-9.
    let tick_e9 = 10_000_000;
    assert_eq!(
        pool_order_qty(&pool, "SOLUSDT", 100, tick_e9).unwrap(),
        5_000_000_000
    );
    assert_eq!(
        pool_order_qty(&pool, "AAAUSDT", 100, tick_e9).unwrap(),
        100_000_000,
        "нулевой чек — ответ минимальный лот"
    );
    let err = pool_order_qty(&pool, "NOPEUSDT", 100, tick_e9)
        .unwrap_err()
        .to_string();
    assert!(err.contains("нет в"), "отказ обязан назвать пул: {err}");
}

/// Профильный путь лот из пула не считает (касаний у него нет) и без явного
/// флага не запускается: умолчания у размера круга нет.
#[test]
fn order_qty_requires_exactly_one_source() {
    let mut args = minimal_backtest_args();
    args.order_qty_e9 = Some(7);
    args.order_qty_from_pool = true;
    assert!(order_qty_arg(&args).is_err(), "два источника — отказ");
    args.order_qty_e9 = None;
    assert!(
        order_qty_arg(&args).is_err(),
        "лот из пула — только `--touches`"
    );
    args.order_qty_from_pool = false;
    assert!(
        order_qty_arg(&args)
            .unwrap_err()
            .to_string()
            .contains("--order-qty-e9"),
        "без обоих флагов отказ обязан назвать флаг"
    );
    args.order_qty_e9 = Some(7);
    assert_eq!(order_qty_arg(&args).unwrap(), 7);
}

fn minimal_backtest_args() -> BacktestArgs {
    BacktestArgs {
        session_root: std::path::PathBuf::from("data/session"),
        symbol: "SOLUSDT".to_string(),
        signals_csv: None,
        median_rtt_ns: 20_000_000,
        p95_rtt_ns: 20_000_000,
        order_qty_e9: Some(1),
        order_qty_from_pool: false,
        profiles_csv: None,
        out: None,
        pnl_out: None,
        debug: false,
        touches: true,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_ticks: 0,
        stop_mode: StopModeArg::Behind,
        deadline_secs: 60,
        early_exit_secs: None,
        trades_out: None,
        h3: crate::commands::lob::H3Args {
            h3_mode: crate::commands::lob::H3ModeArg::Floor,
            h3_lots: None,
            h3_usd: None,
            h3_strength_pct: None,
            h3_strength_window_bps: None,
        },
        h3_k: None,
        warmup_ms: None,
        repeat_window_ms: None,
        allow_unverified: true,
    }
}
