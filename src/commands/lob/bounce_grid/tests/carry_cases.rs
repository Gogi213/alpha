use super::keys::probe_touch;
use super::*;

pub(super) fn day_start_ns_for_test(day: &str) -> i64 {
    chrono::NaiveDate::parse_from_str(day, "%Y-%m-%d")
        .unwrap()
        .and_hms_opt(0, 0, 0)
        .unwrap()
        .and_utc()
        .timestamp_nanos_opt()
        .unwrap()
}

/// Цена стены — крупный тик (100.00, а не 0.99, как у `touch_frames()`):
/// стоп `pct1` от цены входа ~100.01 — это **сто** тиков (шаг 0.01), а не
/// один, так что после входа стоп остаётся далеко ниже текущего рынка (не
/// пересекается им сразу же, как было бы у входа в тике от стены). Порог
/// H3 (`instruments.csv`, `h3_lots=5`) от масштаба цены не зависит.
const WALL_TICK: i64 = 10_000;
const FRONTRUN_TICK: i64 = 10_001;

/// Своя минимальная фикстура (не `touch_frames()`: там касание того же
/// уровня повторяется три раза подряд, что этому тесту не нужно, а
/// геометрия входа в тике от стены оставляла бы стопу `pct1` меньше тика
/// запаса) — один-единственный, полностью финализированный внутри суток D
/// touch, круг которого при этом остаётся **открытым** к концу дня. Метки —
/// настоящие эпоховые (`day_start_ns_for_test(day)`, не относительный нуль):
/// вход — за 40 с до полуночи, а не сразу после старта суток, иначе
/// `--deadline-secs` (60 с от входа) наступил бы раньше, чем перенос успел бы
/// дочитать данные D+1 (сравнение абсолютных нс — так же, как у настоящего
/// бэктеста).
///
/// - снапшот на старте суток: стена `WALL_TICK` (10 лотов, порог H3 — 5),
///   фронтран `FRONTRUN_TICK` перед ней, лучшая цена — фронтран (весь день
///   до входа книга стоит без событий — реплею это ничем не грозит).
/// - `t0 = 23:59:20`: фронтран снят — касание стены СТАРТУЕТ
///   (`frontrun_tick=Some(FRONTRUN_TICK)`, лучшая цена теперь стена).
/// - `t0+100мс`: сделка продавца ровно по цене резерва (`FRONTRUN_TICK`) —
///   исполняет вход (`single@fr`: резерв на фронтране, `trade_could_fill` —
///   сделка на цене резерва или ниже её исполняет; здесь ровно на ней),
///   цену не двигает — рынок остаётся у стены, далеко выше стопа.
/// - `t0+1с` (23:59:21): размер стены падает с 10 до 1 (< порога 5) —
///   уровень умирает, касание ФИНАЛИЗИРУЕТСЯ (`duration_ms=1000` — это и
///   есть `entry_ttl_ns` режима `touch`, вход уже исполнен к этому моменту,
///   тайм-аут ему не грозит) и появляется в `day.touches`.
///
/// Дальше сутки D обрываются: круг открыт (вход исполнен, выход не
/// наступил), а данных для стопа/тейка/дедлайна в этом дне больше нет —
/// ровно случай задачи («вход 21:00, дедлайн 4 ч»).
fn touch_frames_open_at_day_end(day: &str) -> Vec<Vec<crate::binlog::Record>> {
    let day_start_ms = day_start_ns_for_test(day) / 1_000_000;
    let t0 = day_start_ms + 86_360_000; // 23:59:20
    vec![
        snap_frame(
            day_start_ms,
            &[(WALL_TICK, 10), (FRONTRUN_TICK, 10)],
            &[(WALL_TICK + 500, 10)],
        ),
        delta_frame(t0, &[(FRONTRUN_TICK, 0)], &[]),
        trade_frame(t0 + 100, FRONTRUN_TICK, 4),
        delta_frame(t0 + 1_000, &[(WALL_TICK, 1)], &[]),
        // Кадр-заглушка (то же состояние книги) сразу после финализации
        // касания: без него сутки обрываются РОВНО на критической метке
        // времени, и опросу движка (`bot.elapse`) не хватает шага, чтобы
        // это заметить — пробное усечение golden-фикстуры `touch_frames()`
        // (see debug probe) показало ту же чувствительность «плюс один
        // кадр» на границе конца данных.
        delta_frame(t0 + 1_500, &[(WALL_TICK, 1)], &[]),
    ]
}

/// Хвост-довесок: сутки D+1 корня переноса, с абсолютными эпоховыми метками
/// начала `next_day` (не относительными нулём, как у `touch_frames_open_
/// at_day_end`: довесок сравнивается с настоящей полуночью — `day_start_ns`).
/// Открывается собственным снапшотом, как часть-переподключение (A4) — тем
/// же приёмом, что уже сшивает части одних суток (`day_events`). Книга
/// дальше стоит без движения — выход круга здесь не от цены, а от дедлайна
/// (`--deadline-secs 60` от входа `23:59:20` суток D — ровно 00:00:20 суток
/// D+1, `20_000`/`20_500` мс после полуночи ниже): без ЕЩЁ ОДНОГО кадра
/// после точки дедлайна опрос движка (`bot.elapse`) не успевает её
/// заметить — та же чувствительность «плюс один кадр», что и у
/// `touch_frames_open_at_day_end`.
fn carry_tail_frames(next_day: &str) -> Vec<Vec<crate::binlog::Record>> {
    let start_ms = day_start_ns_for_test(next_day) / 1_000_000;
    vec![
        // Снапшот — то же состояние книги, в котором сутки D оставили её
        // (стена — огрызок в 1 лот после смерти уровня, фронтран давно снят).
        snap_frame(start_ms, &[(WALL_TICK, 1)], &[(WALL_TICK + 500, 10)]),
        delta_frame(start_ms + 20_000, &[(WALL_TICK, 1)], &[]),
        delta_frame(start_ms + 20_500, &[(WALL_TICK, 1)], &[]),
    ]
}

fn write_carry_root(dir: &std::path::Path, next_day: &str) {
    write_day(dir, "SOLUSDT", next_day, &carry_tail_frames(next_day));
    std::fs::write(dir.join("session.json"), "{\"start_hour_utc\":0}").unwrap();
}

/// Без `--carry-root`: сутки D обрываются сразу после касания — круг не
/// находит выхода в данных этого дня, `incomplete=true`, кругов в
/// `rounds.csv` нет.
#[test]
fn without_carry_root_a_round_still_open_at_day_end_stays_incomplete() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &touch_frames_open_at_day_end("2026-09-08"),
    );

    let mut a = args(dir.path(), false);
    a.stop_form = vec!["pct1".to_string()];
    a.take_form = vec!["1to1".to_string()];
    a.take_floor_fees = None;
    a.h3 = H3Args {
        h3_mode: H3ModeArg::Floor,
        h3_lots: None,
        h3_usd: None,
        h3_strength_pct: None,
        h3_strength_window_bps: None,
    };
    a.warmup_ms = None;
    a.repeat_window_ms = None;
    a.no_post_only = true;
    a.entry_ttl_secs = vec!["touch".to_string()];
    a.entry_form = vec!["single@fr".to_string()];
    a.deadline_secs = vec![60];
    a.out_dir = dir.path().join("grid-no-carry");
    let m = run_bounce_grid(&a).unwrap();
    let (fh, forms) = read_csv(&m.forms_path);
    assert_eq!(forms.len(), 1, "одна форма сетки");
    assert_eq!(col(&fh, &forms[0], "n_submitted"), "1", "вход отправлен");
    assert_eq!(
        col(&fh, &forms[0], "incomplete"),
        "true",
        "без --carry-root круг не дочитан к концу данных суток D"
    );
    // R6: без --carry-root колонок переноса нет вовсе — шапка прежняя, 33
    // колонки (гейт «те же байты», `gate-g10.sh`).
    assert_eq!(
        fh.len(),
        FORMS_HEADER.len() - FORMS_HEADER_CARRY_LEN,
        "без --carry-root шапка прежняя, 33 колонки"
    );
    assert!(
        !fh.iter()
            .any(|h| h == "n_carried" || h == "carry_unverified"),
        "колонок переноса без флага нет: {fh:?}"
    );
    let (_, rounds) = read_csv(&m.rounds_path);
    assert!(rounds.is_empty(), "круг без выхода не идёт в rounds.csv");
}

/// С `--carry-root`: те же сутки D дочитывают выход по данным D+1 из
/// отдельного корня записи — круг закрыт (`incomplete=false`), помечен
/// перенесённым (`n_carried=1`), день круга в `rounds.csv` остаётся D, а
/// `exit_ns` — уже после настоящей полуночи D+1. Выход здесь — дедлайн
/// (60 с от входа `23:59:20` = 00:00:20 суток D+1): цена в фикстуре не
/// двигается, так что дочитывание видно от чистого наличия данных, не от
/// конкретной причины выхода.
#[test]
fn carry_root_finishes_a_round_still_open_at_midnight() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &touch_frames_open_at_day_end("2026-09-08"),
    );

    let carry_dir = tempfile::tempdir().unwrap();
    write_carry_root(carry_dir.path(), "2026-09-09");
    std::fs::write(carry_dir.path().join("verify-SOLUSDT.status"), "ok").unwrap();

    let mut a = args(dir.path(), false);
    a.stop_form = vec!["pct1".to_string()];
    a.take_form = vec!["1to1".to_string()];
    a.take_floor_fees = None;
    a.h3 = H3Args {
        h3_mode: H3ModeArg::Floor,
        h3_lots: None,
        h3_usd: None,
        h3_strength_pct: None,
        h3_strength_window_bps: None,
    };
    a.warmup_ms = None;
    a.repeat_window_ms = None;
    a.no_post_only = true;
    a.entry_ttl_secs = vec!["touch".to_string()];
    a.entry_form = vec!["single@fr".to_string()];
    a.deadline_secs = vec![60];
    a.carry_root = Some(carry_dir.path().to_path_buf());
    a.out_dir = dir.path().join("grid-carry");
    let m = run_bounce_grid(&a).unwrap();
    let (fh, forms) = read_csv(&m.forms_path);
    assert_eq!(forms.len(), 1, "одна форма сетки");
    assert_eq!(
        fh.len(),
        FORMS_HEADER.len(),
        "с --carry-root шапка полная, 35 колонок"
    );
    assert_eq!(
        col(&fh, &forms[0], "incomplete"),
        "false",
        "с --carry-root круг дочитывает выход по суткам D+1"
    );
    assert_eq!(col(&fh, &forms[0], "n_fills"), "1");
    assert_eq!(col(&fh, &forms[0], "n_carried"), "1");
    assert_eq!(
        col(&fh, &forms[0], "carry_unverified"),
        "false",
        "маркер сверки в корне довеска есть и он `ok`"
    );
    let (rh, rounds) = read_csv(&m.rounds_path);
    assert_eq!(rounds.len(), 1);
    assert_eq!(
        col(&rh, &rounds[0], "day_utc"),
        "2026-09-08",
        "день круга остаётся сутками D, а не D+1"
    );
    let exit_ns: i64 = col(&rh, &rounds[0], "exit_ns").parse().unwrap();
    assert!(
        exit_ns >= day_start_ns_for_test("2026-09-09"),
        "выход исполнился уже после настоящей полуночи D+1: {exit_ns}"
    );

    // T-31 (условие Судьи 3): круг, перенесённый через полночь, при `--busy-skip off` — тот же круг, и след
    // несёт его `idle_ns`/`exit_ns` после полуночи; занятость у движка — на символ-сутки D, D+1 её не наследует.
    a.busy_skip = "off".to_string();
    a.out_dir = dir.path().join("grid-carry-off");
    let m_off = run_bounce_grid(&a).unwrap();
    let (_, rounds_off) = read_csv(&m_off.rounds_path);
    assert_eq!(rounds_off, rounds, "перенесённый круг при `off` — тот же");
    let (sh, sig) = read_csv(&m_off.rounds_path.with_file_name("signals.csv"));
    assert_eq!(sig.len(), 1);
    assert_eq!(col(&sh, &sig[0], "step"), "filled");
    assert_eq!(col(&sh, &sig[0], "exit_ns"), exit_ns.to_string());
    let idle: i64 = col(&sh, &sig[0], "idle_ns").parse().unwrap();
    assert!(
        idle >= exit_ns,
        "форма свободна не раньше выхода: {idle} < {exit_ns}"
    );
}

/// Перенос ограничен окном времени: событие довеска далеко за окном (здесь —
/// намеренно за пределами `--deadline-secs 60` + запас RTT) не читается —
/// `carry_events` останавливает декод на первом событии `local_ts_ns ≥
/// until_ns`, не декодируя сутки D+1 целиком (тот же приём, что бережёт
/// память у `day_events`).
#[test]
fn carry_events_stops_decoding_past_the_carry_window() {
    let next_day = "2026-09-09";
    let start_ms = day_start_ns_for_test(next_day) / 1_000_000;
    let dir = tempfile::tempdir().unwrap();
    write_day(
        dir.path(),
        "SOLUSDT",
        next_day,
        &[
            snap_frame(start_ms, &[(99, 10)], &[(105, 10)]),
            // В окне (сразу после полуночи).
            delta_frame(start_ms + 1_000, &[(99, 9)], &[]),
            // Далеко за окном — читаться не должно.
            delta_frame(start_ms + 3_600_000_000, &[(99, 1)], &[]),
        ],
    );
    let path = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", next_day, 1);
    let until_ns = day_start_ns_for_test(next_day) + 60_000_000_000;
    let events = carry_events(&[path], until_ns).unwrap();
    assert!(!events.is_empty(), "событие в окне обязано быть прочитано");
    assert!(
        events.iter().all(|e| e.local_ts() < until_ns),
        "событие за окном попало в результат: {:?}",
        events.iter().map(|e| e.local_ts()).collect::<Vec<_>>()
    );
}

/// Р10 (T-22): довесок дописывается прямо в буфер суток — те же события в том же
/// порядке, что отдельный `carry_events`, а ёмкость буфера растёт ровно на довесок,
/// без амортизированного удвоения (иначе крупные сутки не влезают в память).
#[test]
fn append_carry_events_grows_day_buffer_exactly() {
    let next_day = "2026-09-09";
    let start_ms = day_start_ns_for_test(next_day) / 1_000_000;
    let dir = tempfile::tempdir().unwrap();
    write_day(
        dir.path(),
        "SOLUSDT",
        next_day,
        &[
            snap_frame(start_ms, &[(99, 10), (98, 5)], &[(105, 10)]),
            delta_frame(start_ms + 1_000, &[(99, 9)], &[]),
            delta_frame(start_ms + 2_000, &[], &[(105, 7)]),
            delta_frame(start_ms + 3_600_000_000, &[(99, 1)], &[]),
        ],
    );
    let path = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", next_day, 1);
    let until_ns = day_start_ns_for_test(next_day) + 60_000_000_000;
    let alone = carry_events(std::slice::from_ref(&path), until_ns).unwrap();
    assert!(!alone.is_empty(), "довесок в окне обязан быть прочитан");
    // Буфер «суток» ровно по размеру, как у `day_events`.
    let day: Vec<crate::lob::backtest::CompactEvent> = alone.iter().take(1).copied().collect();
    let mut events: Vec<crate::lob::backtest::CompactEvent> = Vec::with_capacity(day.len());
    events.extend_from_slice(&day);
    assert_eq!(events.capacity(), events.len());
    let n = super::carry::append_carry_events(&[path], until_ns, &mut events).unwrap();
    assert_eq!(n, alone.len());
    assert_eq!(events.len(), day.len() + alone.len());
    assert_eq!(
        events.capacity(),
        events.len(),
        "ёмкость — ровно сутки + довесок, без удвоения"
    );
    assert_eq!(
        &events[day.len()..],
        &alone[..],
        "те же события в том же порядке"
    );
}

/// Счёт довеска кэшируется сайдкаром по окну: второй вызов даёт те же события, чужое окно — промах.
#[test]
fn append_carry_events_count_sidecar_roundtrip() {
    let next_day = "2026-09-09";
    let start_ms = day_start_ns_for_test(next_day) / 1_000_000;
    let dir = tempfile::tempdir().unwrap();
    write_day(
        dir.path(),
        "SOLUSDT",
        next_day,
        &[
            snap_frame(start_ms, &[(99, 10)], &[(105, 10)]),
            delta_frame(start_ms + 1_000, &[(99, 9)], &[]),
            delta_frame(start_ms + 3_600_000_000, &[(99, 1)], &[]),
        ],
    );
    let path = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", next_day, 1);
    let until_ns = day_start_ns_for_test(next_day) + 60_000_000_000;
    assert!(super::carry::cached_carry_count(&path, until_ns).is_none());
    let mut a = Vec::new();
    let na =
        super::carry::append_carry_events(std::slice::from_ref(&path), until_ns, &mut a).unwrap();
    let cached = super::carry::cached_carry_count(&path, until_ns).expect("сайдкар записан");
    assert_eq!(cached.0, na);
    assert!(super::carry::cached_carry_count(&path, until_ns + 1).is_none());
    let mut b = Vec::new();
    let nb =
        super::carry::append_carry_events(std::slice::from_ref(&path), until_ns, &mut b).unwrap();
    assert_eq!((na, &a), (nb, &b));
}

/// R2 (ревью 23.09): лот `--order-usd` — по цене **каждого** касания, а не
/// одной ценой на символ (прежде — последнего касания всей записи, то есть
/// заглядывание вперёд). $100 при цене 10.00 — 10 монет, при 20.00 — 5; шаг
/// лота 0.1. Явный лот (`--order-qty-e9`) от цены не зависит; множитель E7
/// умножает оба.
#[test]
fn order_usd_sizes_each_touch_at_its_own_price() {
    let lot = PoolLot {
        min_order_qty_e9: 100_000_000,
        qty_step_e9: 100_000_000,
        min_notional_value_e9: 5_000_000_000,
    };
    let tick_e9 = 10_000_000; // 0.01
    let mut cheap = probe_touch();
    cheap.price_tick = 1_000; // 10.00
    let mut dear = probe_touch();
    dear.price_tick = 2_000; // 20.00
    let touches = [cheap, dear];

    let usd = OrderSizing::Usd(lot, 100.0).touch_qtys(&touches, tick_e9, 1);
    assert_eq!(usd.len(), 2);
    assert!((usd[0] - 10.0).abs() < 1e-9, "$100 / 10.00: {}", usd[0]);
    assert!((usd[1] - 5.0).abs() < 1e-9, "$100 / 20.00: {}", usd[1]);

    let doubled = OrderSizing::Usd(lot, 100.0).touch_qtys(&touches, tick_e9, 2);
    assert!((doubled[0] - 20.0).abs() < 1e-9 && (doubled[1] - 10.0).abs() < 1e-9);

    let fixed = OrderSizing::Fixed(300_000_000).touch_qtys(&touches, tick_e9, 1);
    assert!(fixed.iter().all(|q| (q - 0.3).abs() < 1e-12), "{fixed:?}");

    // 22а: минимальный чек $5 при 10.00 — 0.5, при 20.00 — 0.3 (вверх до шага).
    let pool = OrderSizing::Pool22a(lot).touch_qtys(&touches, tick_e9, 1);
    assert!(
        (pool[0] - 0.5).abs() < 1e-9 && (pool[1] - 0.3).abs() < 1e-9,
        "{pool:?}"
    );
}

/// М-04 (TK-206, вердикт Судьи): k применяется к номиналу до округления вниз до шага лота — размер всегда кратен
/// шагу; ниже min-лота — `None` (клетка не определена), не округление вверх. Шаг 0.001, min 0.001, $5 мин-чек.
#[test]
fn lot_first_scales_nominal_floor_to_step_and_refuses_below_min() {
    let lot = PoolLot {
        min_order_qty_e9: 1_000_000,
        qty_step_e9: 1_000_000,
        min_notional_value_e9: 5_000_000_000,
    };
    let tick_e9 = 10_000_000; // 0.01
    let mut t = probe_touch();
    t.price_tick = 1_000; // 10.00 → $100 = 10 монет
    let usd = OrderSizing::Usd(lot, 100.0);
    let q = |k: f64| usd.qty_first_e9(t.price_tick, tick_e9, 1, k, 1_000_000);
    assert_eq!(q(1.5), Some(15_000_000_000));
    assert_eq!(q(0.375), Some(3_750_000_000));
    // $1 → 0.1 монеты × 0.375 = 0.0375 → 37 шагов (0.037) < 22а (0.5)
    let small = OrderSizing::Usd(lot, 1.0);
    assert_eq!(
        small.qty_first_e9(t.price_tick, tick_e9, 1, 0.375, 1_000_000),
        None,
        "$0.375 ниже min-чека 22а ($5) — не определена, не поднимается до min"
    );
    let big = OrderSizing::Usd(lot, 5.1); // 0.51 монеты, k = 0.375 → 0.19125 → 0.191 < 22а (0.5)
    assert_eq!(
        big.qty_first_e9(t.price_tick, tick_e9, 1, 0.375, 1_000_000),
        None
    );
    // Pool22a: 0.5 × 1.5 = 0.75; × 0.5 = 0.25 < 0.5 → не определена
    let pool = OrderSizing::Pool22a(lot);
    assert_eq!(
        pool.qty_first_e9(t.price_tick, tick_e9, 1, 1.5, 1_000_000),
        Some(750_000_000)
    );
    assert_eq!(
        pool.qty_first_e9(t.price_tick, tick_e9, 1, 0.5, 1_000_000),
        None
    );
    // Fixed 0.3, шаг записи 0.1: × 1.5 = 0.45 → 0.4 (вниз, кратно шагу)
    assert_eq!(
        OrderSizing::Fixed(300_000_000).qty_first_e9(t.price_tick, tick_e9, 1, 1.5, 100_000_000),
        Some(400_000_000)
    );
}

/// М-04 (TK-206, вердикт Судьи 09:20): `lot_first=k` меняет размер только у `touch_index == 0`; остальные касания —
/// байт в байт как без ключа (`first_k = None`); k = 1 — сутки как без ключа.
#[test]
fn lot_first_scales_only_first_touch_of_the_level() {
    use super::super::steps::day_order_qtys;
    let lot = PoolLot {
        min_order_qty_e9: 1_000_000,
        qty_step_e9: 1_000_000,
        min_notional_value_e9: 5_000_000_000,
    };
    let tick_e9 = 10_000_000; // 0.01
    let mut touches = Vec::new();
    for (i, idx) in [0_u32, 1, 2, 0, 3].into_iter().enumerate() {
        let mut t = probe_touch();
        t.price_tick = 1_000 + 100 * i64::try_from(i).unwrap();
        t.touch_index = idx;
        touches.push(t);
    }
    let day = super::super::cache::DayTouches {
        day: "2026-01-01".to_string(),
        touches,
        approaches: None,
        rets: Vec::new(),
    };
    let sizing = OrderSizing::Usd(lot, 100.0);
    let grid = (tick_e9, 1_000_000);
    let base = day_order_qtys(&sizing, &day, None, grid, 1, None).unwrap();
    let one = day_order_qtys(&sizing, &day, None, grid, 1, Some(1.0)).unwrap();
    assert_eq!(base, one, "k = 1 — как без ключа");
    let k2 = day_order_qtys(&sizing, &day, None, grid, 1, Some(2.0)).unwrap();
    let k3 = day_order_qtys(&sizing, &day, None, grid, 1, Some(0.375)).unwrap();
    for (i, t) in day.touches.iter().enumerate() {
        if t.touch_index == 0 {
            assert!(
                (k2[i] - 2.0 * base[i]).abs() < 1e-9,
                "{i}: {} vs {}",
                k2[i],
                base[i]
            );
            // кратно шагу 0.001, вниз от 0.375 номинала
            let steps = k3[i] / 0.001;
            assert!((steps - steps.round()).abs() < 1e-6, "{i}: {}", k3[i]);
            assert!(k3[i] <= 0.375 * base[i] + 1e-12 && k3[i] > 0.375 * base[i] - 0.001);
        } else {
            assert_eq!(k2[i].to_bits(), base[i].to_bits(), "{i}: touch_index≠0");
            assert_eq!(k3[i].to_bits(), base[i].to_bits(), "{i}: touch_index≠0");
        }
    }
}

/// П-19 (TK-291): `--touch-col` дописывает в конец `rounds.csv` столбец `touch_index`; прежние столбцы и строки — байт в
/// байт как без флага (гейт Судьи); без флага шапка прежняя, столбца нет.
#[test]
fn touch_col_appends_column_and_keeps_old_columns() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid-base");
    run_bounce_grid(&a).unwrap();
    a.touch_col = true;
    a.out_dir = dir.path().join("grid-touch");
    run_bounce_grid(&a).unwrap();
    let base = std::fs::read_to_string(dir.path().join("grid-base/rounds.csv")).unwrap();
    let with = std::fs::read_to_string(dir.path().join("grid-touch/rounds.csv")).unwrap();
    let (bl, wl): (Vec<&str>, Vec<&str>) = (base.lines().collect(), with.lines().collect());
    assert_eq!(bl.len(), wl.len());
    assert!(bl.len() > 2, "в фикстуре должны быть круги");
    assert!(!bl[1].contains("touch_index"));
    assert!(wl[1].ends_with(",touch_index"), "{}", wl[1]);
    for (b, w) in bl.iter().zip(&wl).skip(2) {
        let cut = w.rfind(',').unwrap();
        assert_eq!(*b, &w[..cut], "старые столбцы не тронуты");
        assert!(w[cut + 1..].parse::<u32>().is_ok(), "{w}");
    }
}

/// R3 (ревью 23.09, замечание проверки): явный лот `--order-qty-e9`, не
/// кратный шагу записи, — отказ, а не молчаливое округление ног лестницы
/// (0.0015 при шаге 0.001 дал бы 2 шага — +33 % к размеру круга).
#[test]
fn fixed_order_qty_off_the_lot_step_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.order_qty_e9 = Some(1_500_000);
    a.out_dir = dir.path().join("grid-off-lot");
    let err = run_bounce_grid(&a).unwrap_err().to_string();
    assert!(err.contains("не кратен шагу лота"), "{err}");
}

/// Замечание проверки R2: явный нулевой лот — отказ, а не круг без размера
/// (`0 % шаг == 0` проходил проверку кратности).
#[test]
fn zero_fixed_order_qty_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.order_qty_e9 = Some(0);
    a.out_dir = dir.path().join("grid-zero-lot");
    let err = run_bounce_grid(&a).unwrap_err().to_string();
    assert!(err.contains("не положителен"), "{err}");
}

/// E26: промежуточные оси битка `btc2h`/`btc3h` — ключи набора, колонки режима
/// `btc_ret_2h_bps`/`btc_ret_3h_bps`; прежний файл режима без них читается (оси —
/// `None`), но если оси нужны наборам — отказ, а не молчаливый пустой набор.
#[test]
fn btc_mid_axes_parse_and_regime_columns() {
    let set = FilterSet::parse("x:btc2h_max=-30,btc3h_min=-40").unwrap();
    assert!(set.uses_regime() && set.uses_btc_mid());
    assert_eq!(set.ctx[7].max, Some(-30.0));
    assert_eq!(set.ctx[8].min, Some(-40.0));
    assert_eq!(set.ctx_label(), "btc2h_max=-30,btc3h_min=-40");
    assert!(!FilterSet::parse("y:btc4h_max=0").unwrap().uses_btc_mid());

    let dir = tempfile::tempdir().unwrap();
    let old = "minute_ms,pool_ret_1h_bps,pool_ret_4h_bps,n_coins,btc_ret_1h_bps,btc_ret_4h_bps,eth_ret_1h_bps,eth_ret_4h_bps\n\
               60000,1,2,5,-10,-40,,\n";
    std::fs::write(dir.path().join("2026-09-08.csv"), old).unwrap();
    let day = read_regime_day(dir.path(), "2026-09-08", false, false).unwrap();
    assert_eq!(
        day[&60_000],
        [
            Some(1.0),
            Some(2.0),
            Some(-10.0),
            Some(-40.0),
            None,
            None,
            None,
            None,
            None,
            None
        ]
    );
    let err = read_regime_day(dir.path(), "2026-09-08", true, false)
        .unwrap_err()
        .to_string();
    assert!(err.contains("btc_ret_2h_bps"), "{err}");

    let new = "minute_ms,pool_ret_1h_bps,pool_ret_4h_bps,n_coins,btc_ret_1h_bps,btc_ret_4h_bps,eth_ret_1h_bps,eth_ret_4h_bps,btc_ret_2h_bps,btc_ret_3h_bps\n\
               60000,1,2,5,-10,-40,,,-20,-30\n";
    std::fs::write(dir.path().join("2026-09-09.csv"), new).unwrap();
    let day = read_regime_day(dir.path(), "2026-09-09", true, false).unwrap();
    assert_eq!(
        day[&60_000],
        [
            Some(1.0),
            Some(2.0),
            Some(-10.0),
            Some(-40.0),
            Some(-20.0),
            Some(-30.0),
            None,
            None,
            None,
            None
        ]
    );

    // Г-10 (TK-189): длинные оси — ключи набора; файл режима без их колонок читается, но при
    // заданной оси — отказ.
    let set = FilterSet::parse("z:btc6h_max=-50,btc24h_min=-90").unwrap();
    assert!(set.uses_regime() && set.uses_btc_long() && !set.uses_btc_mid());
    assert_eq!(set.ctx[9].max, Some(-50.0));
    assert_eq!(set.ctx[12].min, Some(-90.0));
    assert_eq!(set.ctx_label(), "btc6h_max=-50,btc24h_min=-90");
    let err = read_regime_day(dir.path(), "2026-09-09", true, true)
        .unwrap_err()
        .to_string();
    assert!(err.contains("btc_ret_6h_bps"), "{err}");
    let long = "minute_ms,pool_ret_1h_bps,pool_ret_4h_bps,n_coins,btc_ret_1h_bps,btc_ret_4h_bps,eth_ret_1h_bps,eth_ret_4h_bps,btc_ret_2h_bps,btc_ret_3h_bps,eth_ret_2h_bps,eth_ret_3h_bps,btc_ret_6h_bps,btc_ret_8h_bps,btc_ret_12h_bps,btc_ret_24h_bps\n\
                60000,1,2,5,-10,-40,,,-20,-30,,,-60,-70,-80,-90\n";
    std::fs::write(dir.path().join("2026-09-10.csv"), long).unwrap();
    let day = read_regime_day(dir.path(), "2026-09-10", true, true).unwrap();
    assert_eq!(
        day[&60_000][6..],
        [Some(-60.0), Some(-70.0), Some(-80.0), Some(-90.0)]
    );
}

/// Э-08 (T-38): группы выходов (`--exit-group on`) и пропуск шагов удержания (`--hold-step skip`) дают те же
/// `rounds.csv`, `forms.csv`, `signals.csv` (и тот же порядок строк), что прежний счёт (`off` / `poll`), —
/// на сетке с разными стопами, тейками и дедлайнами при одном входе (условия Судьи 1 и 2 к Э-08).
#[test]
fn exit_groups_and_hold_skip_match_the_plain_run_byte_for_byte() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let run = |group: &str, hold: &str, out: &str| {
        let mut a = args(dir.path(), false);
        a.busy_skip = "off".to_string();
        a.stop_form = vec!["s1".to_string(), "s2".to_string(), "pct1".to_string()];
        a.take_form = vec!["t1".to_string(), "1to1".to_string()];
        a.deadline_secs = vec![60, 600];
        a.exit_group = group.to_string();
        a.hold_step = hold.to_string();
        a.out_dir = dir.path().join(out);
        run_bounce_grid(&a).unwrap()
    };
    let base = run("off", "poll", "plain");
    assert!(base.rounds > 0, "фикстура обязана дать круги");
    for (group, hold, out) in [
        ("on", "poll", "g-poll"),
        ("on", "skip", "g-skip"),
        ("off", "skip", "skip"),
    ] {
        let before =
            crate::lob::backtest::EXIT_GROUP_ROUNDS.load(std::sync::atomic::Ordering::Relaxed);
        let got = run(group, hold, out);
        if group == "on" {
            assert!(
                crate::lob::backtest::EXIT_GROUP_ROUNDS.load(std::sync::atomic::Ordering::Relaxed)
                    > before,
                "группы обязаны сработать"
            );
        }
        for name in ["rounds.csv", "forms.csv", "signals.csv"] {
            let body = |p: &std::path::Path| -> String {
                std::fs::read_to_string(p.parent().unwrap().join(name))
                    .unwrap()
                    .lines()
                    .filter(|l| !l.starts_with('#'))
                    .collect::<Vec<_>>()
                    .join("\n")
            };
            assert_eq!(
                body(&got.rounds_path),
                body(&base.rounds_path),
                "{group}/{hold}: {name}"
            );
        }
    }
}
