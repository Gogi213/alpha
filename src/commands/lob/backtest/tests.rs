use super::feed::events_from_feed;
use super::plan::EARLY_EXITS_S;
use super::*;
use crate::book::{Side, Update};
use crate::bybit::ws::Event as WsEvent;
use crate::commands::lob::profiles::FillModel;
use crate::feed::{Event as FeedEvent, Feed};
use crate::lob::backtest::{ExecLatency, SIGMA_SHORT};
use crate::lob::levels::LevelRecord;
use crate::lob::strategy::{ExitReason, TradePlan};
use hftbacktest::types::{
    EXCH_ASK_DEPTH_EVENT, EXCH_BID_DEPTH_EVENT, EXCH_BUY_TRADE_EVENT, EXCH_EVENT,
    EXCH_SELL_TRADE_EVENT, LOCAL_ASK_DEPTH_EVENT, LOCAL_BID_DEPTH_EVENT, LOCAL_BUY_TRADE_EVENT,
    LOCAL_EVENT, LOCAL_SELL_TRADE_EVENT,
};

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

    let model = BacktestFillModel::new(
        ExecLatency::uniform(1_000_000),
        ExecLatency::uniform(2_000_000),
        100_000_000,
    );
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
    assert_eq!(model.p95_rtt_ns(), ExecLatency::uniform(2_000_000));
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
        lot: 1.0,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_ticks: 0,
        // Дедлайн по умолчанию — 60 с, как `--deadline-secs` без флага (B3).
        deadline_ns: 60 * 1_000_000_000,
        // Досрочный выход выключен: тесты B2/B3 проверяют геометрию плана.
        early_exit_ns: 0,
        // F5 (В-74): условия «стена снята»/«цена ушла» проверяются своими
        // тестами, здесь — прежний режим входа.
        entry_ttl: EntryTtl::Touch,
        h3_usd: None,
        band_exit_bps: 0.0,
        // F6 (В-73): прежняя форма входа — нога у фронтранера (гейт).
        entry_form: EntryForm::SingleFrontrun,
        // F7/F8: форма выхода — не используется в одиночном backtest.
        exit_form: crate::commands::lob::bounce_grid::ExitForm::None,
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
        stack_next_tick: None,
        traded_first_s: [0; 3],
        flow_1h_lots: 0,
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

fn geometry(stop_mult: f64, take_mult: f64, take_floor_fees: f64) -> BounceForm {
    BounceForm {
        stop: StopForm::Sigma(stop_mult),
        take: TakeForm::Sigma(take_mult),
        take_floor_fees: Some(take_floor_fees),
    }
}

fn base(stop: &str, take: &str) -> BounceForm {
    BounceForm::parse(stop, take, None).unwrap()
}

/// F5 (В-74): режим `entry_ttl` превращается в **потолок** срока жизни входа
/// и два условия снятия — порог В-66 в единицах крейта (`--h3-usd / цена
/// уровня`) и полосу ухода (`--band-exit-bps`). Режим `touch` (гейт «те же
/// круги») оставляет прежний срок (конец касания) и выключает условия нулями.
#[test]
fn bounce_plan_turns_the_entry_ttl_mode_into_the_ceiling_and_the_two_conditions() {
    let tick = 0.01_f64;
    let touch = bounce_touch(1_000, Some(1_005)); // P = 10.00, вход 10.05
    let form = base("pct2", "1to1");
    let shape = |entry_ttl: EntryTtl| PlanShape {
        entry_ttl,
        h3_usd: Some(50.0),
        band_exit_bps: 20.0,
        ..plain_shape()
    };
    let plan_fields = |plan: &TradePlan| match plan {
        TradePlan::Bounce {
            entry_ttl_ns,
            level_floor_qty,
            band_exit_bps,
            ..
        } => (*entry_ttl_ns, *level_floor_qty, *band_exit_bps),
        TradePlan::SpreadHold => panic!("отскок обязан быть Bounce"),
    };

    // Прежний режим: срок — конец касания (1 с), условия выключены.
    let (_, plan) = bounce_plan(&touch, tick, form, None, shape(EntryTtl::Touch)).unwrap();
    let (ttl, floor, band) = plan_fields(&plan);
    assert_eq!(
        ttl,
        1_000 * 1_000_000,
        "touch — прежний срок, конец касания"
    );
    assert_eq!((floor, band), (0.0, 0.0), "условия F5 в touch выключены");

    // Потолок 60 с из сетки В-74; порог В-66 — 50 / 10.00 = 5 единиц крейта.
    let (_, plan) = bounce_plan(&touch, tick, form, None, shape(EntryTtl::Secs(60))).unwrap();
    let (ttl, floor, band) = plan_fields(&plan);
    assert_eq!(ttl, 60 * 1_000_000_000, "потолок — секунды сетки В-74");
    assert!(
        (floor - 5.0).abs() < 1e-9,
        "порог В-66 = --h3-usd / цена уровня: {floor}"
    );
    assert!((band - 20.0).abs() < 1e-9, "полоса — число флага: {band}");

    // `wall` — только условия рынка, потолка-таймера нет.
    let (_, plan) = bounce_plan(&touch, tick, form, None, shape(EntryTtl::Wall)).unwrap();
    let (ttl, _, _) = plan_fields(&plan);
    assert_eq!(ttl, i64::MAX, "wall — без предохранительного потолка");
}

/// В-62: вход — от **первого фронтранера**; стоп — `a × σ_H` bps от входа в
/// целых тиках вверх, но не ближе тика за плотностью; тейк — `b × σ_H` bps
/// от входа, но не ниже `k × 4.41` bps круга комиссий (В-63). Числа: P = 10.00, вход 10.05
/// (тик 1005), σ = 100 bps: стоп 1×σ = 100 bps × 1005 / 10⁴ = 10.05 тика →
/// 11 тиков → 9.94 (дальше пола 9.99 — берётся он же, 9.94); тейк 2×σ =
/// 200 bps → 20.1 → 21 тиков → 10.26.
#[test]
fn bounce_plan_puts_stop_and_take_at_sigma_multiples_from_the_entry() {
    let tick = 0.01_f64;
    let touch = bounce_touch(1_000, Some(1_005));
    let (_, plan) = bounce_plan(
        &touch,
        tick,
        geometry(1.0, 2.0, 1.0),
        Some(100.0),
        plain_shape(),
    )
    .unwrap();
    let (entry, stop, take) = plan_prices(&plan);
    assert!((entry - 10.05).abs() < 1e-9, "вход от фронтранера: {entry}");
    assert!(
        (stop - 9.94).abs() < 1e-9,
        "стоп 1×σ = 11 тиков ниже входа: {stop}"
    );
    assert!(
        (take - 10.26).abs() < 1e-9,
        "тейк 2×σ = 21 тик выше входа: {take}"
    );
}

/// В-62, полы: при `σ` малой (или нулевой) стоп встаёт на тик **за**
/// плотностью (`P − 1`), а не на входе, тейк — на `k × 4.41` bps: форма не
/// вырождается в «стоп на цене входа» сетки В-58.
#[test]
fn bounce_plan_floors_keep_the_form_non_degenerate() {
    let tick = 0.01_f64;
    let touch = bounce_touch(1_000, Some(1_005));
    // σ = 0: стоп 0 тиков → пол 9.99; тейк max(0, 3 × 4.41 = 13.23 bps) →
    // 13.23 bps × 1005 / 10⁴ = 1.33 тика → 2 тика → 10.07.
    let (_, plan) = bounce_plan(
        &touch,
        tick,
        geometry(1.0, 1.0, 3.0),
        Some(0.0),
        plain_shape(),
    )
    .unwrap();
    let (_, stop, take) = plan_prices(&plan);
    assert!(
        (stop - 9.99).abs() < 1e-9,
        "стоп на тике за плотностью: {stop}"
    );
    assert!(
        (take - 10.07).abs() < 1e-9,
        "тейк на полу 3 × 4.41 bps: {take}"
    );
    // Стоп по σ ближе пола (5 bps → 5.025 тика → 6 тиков → 9.99): совпадает с
    // полом; 2 bps → 2.01 → 3 тика → 10.02 — ближе пола, берётся пол 9.99.
    let (_, plan) = bounce_plan(
        &touch,
        tick,
        geometry(1.0, 1.0, 1.0),
        Some(2.0),
        plain_shape(),
    )
    .unwrap();
    let (_, stop, _) = plan_prices(&plan);
    assert!(
        (stop - 9.99).abs() < 1e-9,
        "пол дальше σ-стопа — берётся пол: {stop}"
    );
}

/// В-62, аск зеркально: уровень 10.00 (аск), фронтранер 9.95; стоп выше входа,
/// не ближе `P + 1`; тейк ниже входа.
#[test]
fn bounce_plan_mirrors_the_geometry_for_the_ask() {
    let tick = 0.01_f64;
    let mut touch = bounce_touch(1_000, Some(995));
    touch.side = Side::Ask;
    let (dir, plan) = bounce_plan(
        &touch,
        tick,
        geometry(1.0, 2.0, 1.0),
        Some(100.0),
        plain_shape(),
    )
    .unwrap();
    assert_eq!(dir, SIGMA_SHORT);
    let (entry, stop, take) = plan_prices(&plan);
    assert!((entry - 9.95).abs() < 1e-9, "{entry}");
    // 100 bps × 995 / 10⁴ = 9.95 тика → 10 тиков → 10.05; пол P + 1 = 10.01 —
    // σ-стоп дальше, берётся он.
    assert!((stop - 10.05).abs() < 1e-9, "{stop}");
    // 200 bps → 19.9 → 20 тиков → 9.75.
    assert!((take - 9.75).abs() < 1e-9, "{take}");
}

/// Фронтрана впереди не было — вход прежний (`P + 1` тик), геометрия та же.
#[test]
fn bounce_plan_without_frontrun_enters_one_tick_before_the_level() {
    let tick = 0.01_f64;
    let touch = bounce_touch(1_000, None);
    let (_, plan) = bounce_plan(
        &touch,
        tick,
        geometry(1.0, 1.0, 1.0),
        Some(50.0),
        plain_shape(),
    )
    .unwrap();
    let (entry, stop, take) = plan_prices(&plan);
    assert!((entry - 10.01).abs() < 1e-9, "вход P+1: {entry}");
    // 50 bps × 1001 / 10⁴ = 5.005 → 6 тиков → 9.95 (дальше пола 9.99).
    assert!((stop - 9.95).abs() < 1e-9, "{stop}");
    // max(50, 4.41) = 50 bps → 6 тиков → 10.07.
    assert!((take - 10.07).abs() < 1e-9, "{take}");
}

/// Формы базы (В-65) на бид-уровне `P = 10.00` с фронтранером `10.05`:
/// `before` → стоп `P+1` = 10.01, тейк 1:1 от входа 10.09; `at` → 10.00 /
/// 10.10; `behind` → 9.99 / 10.11; `midfr` → середина между 10.05 и 10.00 =
/// 10.02 (два тика от уровня, целая часть) / 10.08; `pct1` → 1 % от входа =
/// 10.05 тика → 11 тиков → 9.94 / 10.16; `stack2` — нужна вторая плотность.
#[test]
fn base_stop_forms_are_positional_from_the_level_and_the_entry() {
    let tick = 0.01_f64;
    let touch = bounce_touch(1_000, Some(1_005));
    let cases = [
        ("before", 10.01, 10.09),
        ("at", 10.00, 10.10),
        ("behind", 9.99, 10.11),
        ("midfr", 10.02, 10.08),
        ("pct1", 9.94, 10.16),
    ];
    for (stop, want_stop, want_take) in cases {
        let (_, plan) = bounce_plan(&touch, tick, base(stop, "1to1"), None, plain_shape())
            .unwrap_or_else(|| panic!("{stop}: форма обязана строиться"));
        let (entry, got_stop, got_take) = plan_prices(&plan);
        assert!(
            (entry - 10.05).abs() < 1e-9,
            "{stop}: вход от фронтрана: {entry}"
        );
        assert!(
            (got_stop - want_stop).abs() < 1e-9,
            "{stop}: стоп {got_stop} вместо {want_stop}"
        );
        assert!(
            (got_take - want_take).abs() < 1e-9,
            "{stop}: тейк {got_take} вместо {want_take}"
        );
    }
    // Вторая плотность завала на 9.95: стоп за ней — 9.94, тейк 1:1 — 10.16.
    let mut stacked = touch;
    stacked.stack_next_tick = Some(995);
    let (_, plan) =
        bounce_plan(&stacked, tick, base("stack2", "1to1"), None, plain_shape()).unwrap();
    let (_, got_stop, got_take) = plan_prices(&plan);
    assert!((got_stop - 9.94).abs() < 1e-9, "{got_stop}");
    assert!((got_take - 10.16).abs() < 1e-9, "{got_take}");
    assert!(
        bounce_plan(&touch, tick, base("stack2", "1to1"), None, plain_shape()).is_none(),
        "без второй плотности форма stack2 не строится"
    );
}

/// Без фронтрана вход `P+1`: `before` и `midfr` вырождаются (стоп совпал бы
/// со входом) — `None`, а `at`/`behind`/`pct` строятся; σ-формам без σ — `None`.
#[test]
fn base_forms_that_need_a_frontrun_skip_touches_without_one() {
    let tick = 0.01_f64;
    let touch = bounce_touch(1_000, None);
    assert!(bounce_plan(&touch, tick, base("before", "1to1"), None, plain_shape()).is_none());
    assert!(bounce_plan(&touch, tick, base("midfr", "1to1"), None, plain_shape()).is_none());
    let (_, plan) = bounce_plan(&touch, tick, base("at", "1to1"), None, plain_shape()).unwrap();
    let (entry, stop, take) = plan_prices(&plan);
    assert!(
        (entry - 10.01).abs() < 1e-9 && (stop - 10.00).abs() < 1e-9 && (take - 10.02).abs() < 1e-9
    );
    assert!(bounce_plan(&touch, tick, base("behind", "1to1"), None, plain_shape()).is_some());
    assert!(bounce_plan(&touch, tick, base("pct0.5", "1to1"), None, plain_shape()).is_some());
    assert!(
        bounce_plan(&touch, tick, geometry(1.0, 1.0, 1.0), None, plain_shape()).is_none(),
        "σ-форма без σ не строится"
    );
}

/// Аск зеркально для позиционных форм: уровень 10.00 (аск), фронтранер 9.95;
/// `before` → стоп 9.99, тейк 9.91; `pct1` → 9.95 × 1 % = 9.95 тика → 10 тиков
/// → стоп 10.05, тейк 9.85.
#[test]
fn base_forms_mirror_for_the_ask() {
    let tick = 0.01_f64;
    let mut touch = bounce_touch(1_000, Some(995));
    touch.side = Side::Ask;
    let (dir, plan) =
        bounce_plan(&touch, tick, base("before", "1to1"), None, plain_shape()).unwrap();
    assert_eq!(dir, SIGMA_SHORT);
    let (entry, stop, take) = plan_prices(&plan);
    assert!(
        (entry - 9.95).abs() < 1e-9 && (stop - 9.99).abs() < 1e-9 && (take - 9.91).abs() < 1e-9,
        "{entry} {stop} {take}"
    );
    let (_, plan) = bounce_plan(&touch, tick, base("pct1", "1to1"), None, plain_shape()).unwrap();
    let (_, stop, take) = plan_prices(&plan);
    assert!(
        (stop - 10.05).abs() < 1e-9 && (take - 9.85).abs() < 1e-9,
        "{stop} {take}"
    );
}

/// Имена форм разбираются и печатаются каноническими; чужие и неканонические — отказ.
#[test]
fn form_names_round_trip_and_reject_strangers() {
    for name in [
        "before", "at", "behind", "midfr", "stack2", "pct0.5", "pct2", "s1", "s1.5",
    ] {
        assert_eq!(StopForm::parse(name).unwrap().label(), name);
    }
    for name in ["1to1", "t1", "t0.5", "tr0.5x0.3", "tr1x0.5"] {
        assert_eq!(TakeForm::parse(name).unwrap().label(), name);
    }
    for bad in ["s1.0", "pct01", "pct0", "pct100", "s-1", "x", "sigma1"] {
        assert!(StopForm::parse(bad).is_err(), "{bad}");
    }
    for bad in ["t1.0", "1:1", "take", "tr0.5", "tr0x0.3", "tr0.5x-1"] {
        assert!(TakeForm::parse(bad).is_err(), "{bad}");
    }
    // Трейл кладёт активацию и откат в план в bps (0.5 % → 50 bps, 0.3 % → 30).
    let touch = bounce_touch(1_000, Some(1_005));
    let (_, plan) =
        bounce_plan(&touch, 0.01, base("at", "tr0.5x0.3"), None, plain_shape()).unwrap();
    let TradePlan::Bounce {
        trail_bps,
        trail_activate_bps,
        ..
    } = plan
    else {
        panic!("отскок обязан быть Bounce");
    };
    assert!((trail_activate_bps - 50.0).abs() < 1e-9 && (trail_bps - 30.0).abs() < 1e-9);
    assert!(
        BounceForm::parse("s1", "t1", None).is_err(),
        "σ-тейк без пола — отказ"
    );
    assert!(BounceForm::parse("before", "1to1", None).is_ok());
}

/// Числа владельца проверяются: отрицательный множитель и нулевой пол — отказ.
#[test]
fn sigma_geometry_rejects_negative_multipliers_and_a_zero_floor() {
    assert!(geometry(1.0, 1.0, 1.0).validate().is_ok());
    assert!(
        geometry(0.0, 0.0, 0.5).validate().is_ok(),
        "нулевые множители — полы работают"
    );
    assert!(geometry(-1.0, 1.0, 1.0).validate().is_err());
    assert!(geometry(1.0, f64::NAN, 1.0).validate().is_err());
    assert!(geometry(1.0, 1.0, 0.0).validate().is_err());
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
    let (_, plan) = bounce_plan(&touch, tick, geometry(1.0, 1.0, 1.0), Some(10.0), shape).unwrap();
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
        entry_taker: false,
        exit_taker: true,
        // Полное исполнение (F3): средневзвешенная цена входа равна прежней
        // `entry_px`, доля исполнения — единица.
        entry_vwap: entry_px,
        fill_frac: 1.0,
        legs_filled: 1,
        legs_rejected: 0,
        fill_by_cross: false,
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
        rejected_postonly: 0,
        entry_crossed: 0,
        entry_cancelled_ttl: 0,
        entry_cancelled_wall_dead: 0,
        entry_cancelled_price_left: 0,
        entry_cancelled_cancel_timeout: 0,
        exit_cancel_timeout: 0,
        orphan_fills: 0,
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
    // 1 % хода минус комиссии по ногам (В-63: мейкер 1.26 + тейкер 3.15 = 4.41) = 95.59 bps.
    assert!(
        lines[2].ends_with(",95.590000,take"),
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
        median_rtt_ns: ExecLatency::uniform(20_000_000),
        p95_rtt_ns: ExecLatency::uniform(20_000_000),
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
        stop_form: Some("s1".to_string()),
        take_form: Some("t1".to_string()),
        take_floor_fees: Some(1.0),
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

/// E7: формы `half1to1` и `eat<h>x<a>` — имя ↔ форма, границы порогов, и как
/// они ложатся в план (доля тейка, пороги съедания, размер плотности в
/// единицах крейта = лоты × шаг лота).
#[test]
fn e7_take_forms_parse_and_fill_the_plan() {
    assert_eq!(TakeForm::parse("half1to1").unwrap(), TakeForm::HalfOneToOne);
    assert_eq!(TakeForm::HalfOneToOne.label(), "half1to1");
    let e = TakeForm::parse("eat50x80").unwrap();
    assert_eq!(
        e,
        TakeForm::Eaten {
            half_pct: 50.0,
            all_pct: 80.0
        }
    );
    assert_eq!(e.label(), "eat50x80");
    assert!(
        TakeForm::parse("eat80x50").is_err(),
        "половина обязана быть меньше «всё»"
    );
    assert!(TakeForm::parse("eat50x120").is_err(), "не больше 100 %");
    assert!(TakeForm::parse("eat50").is_err());

    let touch = bounce_touch(1_000, Some(1_005));
    let shape = PlanShape {
        lot: 0.1,
        ..plain_shape()
    };
    let (_, plan) = bounce_plan(&touch, 0.01, base("pct1", "eat50x80"), None, shape).unwrap();
    let TradePlan::Bounce {
        take_frac,
        eaten_half_pct,
        eaten_all_pct,
        eaten_half_frac,
        level_qty,
        lot_qty,
        ..
    } = plan
    else {
        panic!("отскок обязан быть Bounce");
    };
    assert!((take_frac - 1.0).abs() < 1e-12);
    assert!((eaten_half_pct - 50.0).abs() < 1e-12 && (eaten_all_pct - 80.0).abs() < 1e-12);
    assert!((eaten_half_frac - 0.5).abs() < 1e-12);
    // `size_at_touch = 10` лотов × шаг 0.1 = 1.0 в единицах крейта.
    assert!((level_qty - 1.0).abs() < 1e-12, "{level_qty}");
    assert!((lot_qty - 0.1).abs() < 1e-12);

    let (_, plan) = bounce_plan(&touch, 0.01, base("pct1", "half1to1"), None, shape).unwrap();
    let TradePlan::Bounce {
        take_frac,
        eaten_half_pct,
        take_px,
        entry_px,
        stop_px,
        ..
    } = plan
    else {
        panic!("отскок обязан быть Bounce");
    };
    assert!((take_frac - 0.5).abs() < 1e-12 && eaten_half_pct == 0.0);
    // Тейк — те же 1:1 от входа, что у `1to1`.
    assert!((take_px - (entry_px + (entry_px - stop_px))).abs() < 1e-9);
}

/// S8: тейк в процентах от входа `tk<x>` — имя ↔ форма, границы (0, 100),
/// тейк ложится в план на `ceil(x % от входа)` тиков в сторону отскока
/// независимо от стопа (у `pct2-tk0.5` тейк в 4 раза ближе стопа).
#[test]
fn pct_take_form_parses_and_sets_the_take_independent_of_the_stop() {
    assert_eq!(TakeForm::parse("tk0.5").unwrap(), TakeForm::Pct(0.5));
    assert_eq!(TakeForm::Pct(1.0).label(), "tk1");
    assert_eq!(TakeForm::parse("tk1").unwrap(), TakeForm::Pct(1.0));
    assert!(TakeForm::parse("tk0").is_err());
    assert!(TakeForm::parse("tk100").is_err());
    assert!(TakeForm::parse("tk1.0").is_err(), "имя не каноническое");
    // Бид 10 000 без фронтрана: вход P + 1 = 10 001; стоп 2 % — 201 тик вниз.
    let touch = bounce_touch(10_000, None);
    let plan = |take: &str| {
        let (_, plan) = bounce_plan(&touch, 0.01, base("pct2", take), None, plain_shape())
            .expect("план строится");
        plan_prices(&plan)
    };
    let (e1, s1, t1) = plan("1to1");
    let (e2, s2, t2) = plan("tk0.5");
    assert!(
        (e1 - e2).abs() < 1e-9 && (s1 - s2).abs() < 1e-9,
        "вход и стоп общие"
    );
    // 1:1 — столько же тиков вверх, сколько стоп вниз; tk0.5 — ceil(0.5 % × 10 001) = 51 тик.
    assert!((t1 - (e1 + (e1 - s1))).abs() < 1e-9, "1to1: {t1}");
    assert!((t2 - (e2 + 51.0 * 0.01)).abs() < 1e-9, "tk0.5: {t2}");
    assert!(t2 < t1, "тейк в % ближе, чем 1:1 при стопе 2 %");
}

// -----------------------------------------------------------------------
// F6 (план 2026-09-20, §3): форма входа — `single@fr` или лестница
// `ladder<N>x<from>..<to>[w<k>]`, ноги — целыми тиками, совпавшие тики
// складываются, нижняя нога может весить к стене.
// -----------------------------------------------------------------------

/// Разбор имени формы входа: каноническое имя (как у `StopForm`/`TakeForm`),
/// границы полосы и веса, ёмкость ног. Имя — единственный источник чисел
/// `N`/`from`/`to`/`w` (предрегистрация), умолчаний нет.
#[test]
fn entry_form_parses_the_ladder_and_the_single_frontrun() {
    assert_eq!(
        EntryForm::parse("single@fr").unwrap(),
        EntryForm::SingleFrontrun
    );
    assert_eq!(EntryForm::SingleFrontrun.label(), "single@fr");
    assert_eq!(
        EntryForm::parse("ladder3x2..10").unwrap(),
        EntryForm::Ladder {
            legs: 3,
            from_bps: 2.0,
            to_bps: 10.0,
            wall_weight: 1,
        }
    );
    assert_eq!(
        EntryForm::parse("ladder3x2..10").unwrap().label(),
        "ladder3x2..10"
    );
    let weighted = EntryForm::parse("ladder4x0.5..2w2").unwrap();
    assert_eq!(
        weighted,
        EntryForm::Ladder {
            legs: 4,
            from_bps: 0.5,
            to_bps: 2.0,
            wall_weight: 2,
        }
    );
    assert_eq!(weighted.label(), "ladder4x0.5..2w2");
    // Отказы: чужое имя, неканоническая запись, границы, ёмкость.
    assert!(EntryForm::parse("fr").is_err());
    assert!(
        EntryForm::parse("ladder3x2..10w1").is_err(),
        "вес 1 не пишется"
    );
    assert!(
        EntryForm::parse("ladder03x2..10").is_err(),
        "нули в числе ног"
    );
    assert!(
        EntryForm::parse("ladder3x2.0..10").is_err(),
        "неканоническая запись bps"
    );
    assert!(
        EntryForm::parse("ladder1x2..10").is_err(),
        "одна нога — не лестница"
    );
    assert!(EntryForm::parse("ladder3x0..10").is_err(), "from = 0");
    assert!(EntryForm::parse("ladder3x10..2").is_err(), "from ≥ to");
    assert!(
        EntryForm::parse("ladder9x2..10").is_err(),
        "ног больше ёмкости"
    );
    assert!(EntryForm::parse("ladder3x2..10w0").is_err(), "вес 0");
}

/// Ноги лестницы ложатся на **целые тики** (`bps_to_ticks_ceil` — расстояние
/// не меньше заданного), средняя цена плана — по долям, а цена уровня остаётся
/// стеной. Бид 100.00, шаг 0.01: 2/6/10 bps — это 2/6/10 тиков.
#[test]
fn ladder_legs_land_on_whole_ticks_and_the_plan_average_is_weighted() {
    let touch = bounce_touch(10_000, None);
    let build = |spec: &str| {
        let (_, plan) = bounce_plan(
            &touch,
            0.01,
            base("pct2", "1to1"),
            None,
            PlanShape {
                entry_form: EntryForm::parse(spec).unwrap(),
                ..plain_shape()
            },
        )
        .expect("план строится");
        plan
    };
    let TradePlan::Bounce {
        ladder,
        entry_px,
        level_px,
        ..
    } = build("ladder3x2..10")
    else {
        panic!("отскок обязан быть Bounce");
    };
    assert_eq!(ladder.n, 3);
    assert_eq!(&ladder.ticks[..3], &[10_002, 10_006, 10_010]);
    for i in 0..3 {
        assert!(
            (ladder.frac[i] - 1.0 / 3.0).abs() < 1e-12,
            "равные доли: {}",
            ladder.frac[i]
        );
    }
    // Средняя — ближайший тик к взвешенной сумме: 10 006 → 100.06.
    assert!((entry_px - 100.06).abs() < 1e-9, "{entry_px}");
    assert!((level_px - 100.0).abs() < 1e-9, "стена — цена уровня");

    // Вес к стене: нижняя нога ×2 — доли 2/4, 1/4, 1/4, средняя (2×10002 +
    // 10006 + 10010)/4 = 10005 → 100.05.
    let TradePlan::Bounce {
        ladder, entry_px, ..
    } = build("ladder3x2..10w2")
    else {
        panic!("отскок обязан быть Bounce");
    };
    assert_eq!(ladder.n, 3);
    assert!((ladder.frac[0] - 0.5).abs() < 1e-12);
    assert!((ladder.frac[1] - 0.25).abs() < 1e-12 && (ladder.frac[2] - 0.25).abs() < 1e-12);
    assert!((entry_px - 100.05).abs() < 1e-9, "{entry_px}");
}

/// Совпавшие тики складываются в одну ногу с суммарной долей: у дешёвой монеты
/// (10.00, шаг 0.01) 2/6/10 bps — это 0.2/0.6/1.0 тика, все три округляются
/// вверх до первого тика (не меньше заданного), и биржа получила бы три заявки
/// по одной цене — а это одна нога и вся сумма долей.
#[test]
fn ladder_legs_on_the_same_tick_merge_into_one_with_the_summed_share() {
    let touch = bounce_touch(1_000, None);
    let (_, plan) = bounce_plan(
        &touch,
        0.01,
        base("pct2", "1to1"),
        None,
        PlanShape {
            entry_form: EntryForm::parse("ladder3x2..10").unwrap(),
            ..plain_shape()
        },
    )
    .expect("план строится");
    let TradePlan::Bounce { ladder, .. } = plan else {
        panic!("отскок обязан быть Bounce");
    };
    assert_eq!(ladder.n, 1, "совпавшие тики — одна нога");
    assert_eq!(ladder.ticks[0], 1_001);
    assert!((ladder.frac[0] - 1.0).abs() < 1e-12, "{}", ladder.frac[0]);
}

/// F8b (С11 аудита 21.09): граница ёмкости ног. `ladder8x…` — ровно
/// `MAX_ENTRY_LEGS` ног: `ladder_legs` заполняет массив до последнего места
/// (его `push` за ёмкостью — отказ, а не усечение), доли нормированы в
/// единицу, тики идут от стены к рынку, а средняя цена плана — взвешенная.
#[test]
fn ladder_at_capacity_fills_every_leg_and_keeps_the_share_sum() {
    let touch = bounce_touch(10_000, None);
    let (_, plan) = bounce_plan(
        &touch,
        0.01,
        base("pct2", "1to1"),
        None,
        PlanShape {
            entry_form: EntryForm::parse("ladder8x1..8").unwrap(),
            ..plain_shape()
        },
    )
    .expect("план строится");
    let TradePlan::Bounce {
        ladder,
        entry_px,
        tick_px,
        ..
    } = plan
    else {
        panic!("отскок обязан быть Bounce");
    };
    assert_eq!(
        ladder.n as usize,
        crate::lob::strategy::MAX_ENTRY_LEGS,
        "восемь ног — вся ёмкость"
    );
    // Первая нога — тик над стеной (1 bps при цене 100.00).
    assert_eq!(ladder.ticks[0], 10_001, "нога у стены");
    for i in 1..ladder.n as usize {
        assert!(
            ladder.ticks[i] > ladder.ticks[i - 1],
            "ноги идут от стены к рынку: {:?}",
            &ladder.ticks[..ladder.n as usize]
        );
    }
    let sum: f64 = ladder.frac[..ladder.n as usize].iter().sum();
    assert!((sum - 1.0).abs() < 1e-12, "доли нормированы: {sum}");
    let weighted = ladder.weighted_avg_tick() * tick_px;
    // Цена плана — ближайший **тик** к взвешенной сумме (цены живут на сетке
    // тиков), поэтому сравнивается округление, а не сама дробная сумма.
    let nearest = (weighted / tick_px).round() * tick_px;
    assert!(
        (entry_px - nearest).abs() < 1e-9,
        "средняя плана — взвешенная по долям: {entry_px} против {weighted}"
    );
}
