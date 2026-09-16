use super::*;
use std::collections::VecDeque;

struct ScriptedFeed {
    events: VecDeque<Event>,
}

impl Feed for ScriptedFeed {
    fn next_event(&mut self) -> Option<Event> {
        self.events.pop_front()
    }
}

/// `u` — счётчик последовательности `Book::apply` (не связан с `Feed`,
/// который в этом файле его не видит): снапшот несёт `u=1`, каждая
/// следующая дельта — `u` на единицу больше предыдущей, иначе
/// `Book::apply` честно откажет `SequenceGap`, и тест не увидит ничего.
///
/// Дельта, а не снапшот: старый уровень каждой стороны обязан быть
/// явно обнулён (`(old, 0)`), иначе он остаётся в книге навсегда и при
/// движении цены вверх старый аск оказывается ниже нового бида —
/// `Crossed`, а не смена тика (реальный Bybit-делта устроен так же: не
/// названный явно уровень не исчезает сам). Когда `old == new`, пара
/// `(px, 0)` затем `(px, qty)` — недействующий ноль, тик остаётся тем же.
fn book_event(
    local_ts_ns: i64,
    parse_ns: Option<i64>,
    u: u64,
    old_bid_e9: i64,
    new_bid_e9: i64,
    old_ask_e9: i64,
    new_ask_e9: i64,
) -> Event {
    use crate::book::Update;
    Event::Market {
        symbol: 0,
        local_ts_ns,
        parse_latency_ns: parse_ns,
        payload: crate::bybit::ws::Event::Book(Update {
            is_snapshot: false,
            depth: 50,
            u,
            cts_ms: local_ts_ns / 1_000_000,
            seq: 0,
            bids: vec![(old_bid_e9, 0), (new_bid_e9, 1_000_000_000)],
            asks: vec![(old_ask_e9, 0), (new_ask_e9, 1_000_000_000)],
        }),
    }
}

fn snapshot_event(local_ts_ns: i64, bid_e9: i64, ask_e9: i64) -> Event {
    use crate::book::Update;
    Event::Market {
        symbol: 0,
        local_ts_ns,
        parse_latency_ns: Some(1_000),
        payload: crate::bybit::ws::Event::Book(Update {
            is_snapshot: true,
            depth: 50,
            u: 1,
            cts_ms: local_ts_ns / 1_000_000,
            seq: 0,
            bids: vec![(bid_e9, 1_000_000_000)],
            asks: vec![(ask_e9, 1_000_000_000)],
        }),
    }
}

fn test_creds() -> Credentials {
    Credentials::for_test("test-key", "test-secret")
}

fn cfg() -> ReactCoreConfig {
    ReactCoreConfig {
        tick_e9: 10_000_000,
        step_e9: 1_000_000,
        side: OrderSide::Buy,
        qty_e9: 1_000_000,
        recv_window_ms: 5_000,
    }
}

/// Снапшот сам по себе не двигает лучший тик (первое наблюдение), а
/// каждое следующее реальное изменение цены — срабатывание: три смены
/// подряд после снапшота обязаны дать три образца, не четыре и не два.
#[test]
fn each_top_of_book_change_after_the_first_is_a_trigger() {
    let px = |n: i64| n * 10_000_000;
    let events = VecDeque::from(vec![
        snapshot_event(0, px(100), px(101)),
        book_event(
            1_000_000,
            Some(150_000),
            2,
            px(100),
            px(100),
            px(101),
            px(101),
        ), // тот же тик — не триггер
        book_event(
            2_000_000,
            Some(160_000),
            3,
            px(100),
            px(101),
            px(101),
            px(102),
        ), // сдвиг — триггер 1
        book_event(
            3_000_000,
            Some(170_000),
            4,
            px(101),
            px(102),
            px(102),
            px(103),
        ), // сдвиг — триггер 2
    ]);
    let mut feed = ScriptedFeed { events };
    let clock = MonotonicClock::start();
    let creds = test_creds();
    let (samples, processed) =
        run_react_over_feed(&mut feed, &cfg(), &creds, "SOLUSDT", &clock, || false);

    assert_eq!(processed, 4, "все четыре события обязаны быть прочитаны");
    // «Книга»/«разбор» — на КАЖДОМ событии (ремонт таска 15 по ревью):
    // все четыре события дают образец, а не только два срабатывания.
    assert_eq!(
        samples.len(),
        4,
        "книга/разбор считаются на каждом событии: {samples:?}"
    );
    for s in &samples {
        assert!(s.book_ns >= 0, "{s:?}");
        if let Some(t) = s.trigger {
            assert!(
                t.trigger_ns >= 0 && t.order_ns >= 0 && t.full_ns >= 0,
                "{t:?}"
            );
        }
    }
    let triggers: Vec<_> = samples.iter().filter(|s| s.trigger.is_some()).collect();
    assert_eq!(
        triggers.len(),
        2,
        "срабатывания только на реальную смену тика: {samples:?}"
    );
    assert_eq!(
        triggers[0].parse_ns,
        Some(160_000),
        "срабатывание 1 — кадр в 2с"
    );
    assert_eq!(
        triggers[1].parse_ns,
        Some(170_000),
        "срабатывание 2 — кадр в 3с"
    );
}

/// Первое срабатывание использует ордер, подготовленный на предыдущем
/// обновлении (снапшоте) — то есть уже ПЕРВАЯ смена тика после снапшота
/// даёт образец, а не вторая (иначе «готов заранее» не выполняется).
#[test]
fn the_very_first_change_after_the_snapshot_is_already_a_trigger() {
    let px = |n: i64| n * 10_000_000;
    let events = VecDeque::from(vec![
        snapshot_event(0, px(100), px(101)),
        book_event(
            1_000_000,
            Some(150_000),
            2,
            px(100),
            px(101),
            px(101),
            px(102),
        ),
    ]);
    let mut feed = ScriptedFeed { events };
    let clock = MonotonicClock::start();
    let creds = test_creds();
    let (samples, _) = run_react_over_feed(&mut feed, &cfg(), &creds, "SOLUSDT", &clock, || false);
    // Оба события дают образец «книга»/«разбор»; срабатывание — только
    // второе (первая реальная смена тика после снапшота).
    assert_eq!(
        samples.len(),
        2,
        "книга/разбор на каждом событии: {samples:?}"
    );
    let triggers: Vec<_> = samples.iter().filter(|s| s.trigger.is_some()).collect();
    assert_eq!(triggers.len(), 1, "готовность уже на снапшоте: {samples:?}");
    assert_eq!(triggers[0].parse_ns, Some(150_000));
}

/// `should_stop` останавливает чтение немедленно — ядро не эксплуатирует
/// весь `Feed`, если вызывающий решил хватит (живой дедлайн в
/// `run_react`).
#[test]
fn should_stop_ends_the_loop_without_draining_the_feed() {
    let px = |n: i64| n * 10_000_000;
    let events = VecDeque::from(vec![
        snapshot_event(0, px(100), px(101)),
        book_event(1_000_000, Some(1), 2, px(100), px(101), px(101), px(102)),
        book_event(2_000_000, Some(1), 3, px(101), px(102), px(102), px(103)),
    ]);
    let mut feed = ScriptedFeed { events };
    let clock = MonotonicClock::start();
    let creds = test_creds();
    let mut calls = 0;
    let (_samples, processed) =
        run_react_over_feed(&mut feed, &cfg(), &creds, "SOLUSDT", &clock, || {
            calls += 1;
            calls >= 1
        });
    assert_eq!(processed, 1, "остановка после первого события: {processed}");
}

/// Фейк-подписант без единой аллокации — тот же приём, что
/// `bybit::trade_ws::tests::ZeroAllocSigner`, своя копия здесь: та
/// приватна тестовому модулю другого файла (шов «`Transport`/подпись —
/// фейк», не обязательно один экземпляр на весь репозиторий).
struct ZeroAllocSigner {
    api_key: String,
}

impl OrderSigner for ZeroAllocSigner {
    fn sign(
        &self,
        _timestamp_ms: i64,
        _recv_window_ms: u32,
        _body: &str,
    ) -> Result<String, crate::bybit::sign::CredentialsError> {
        unreachable!("тест зовёт только sign_into")
    }
    fn api_key(&self) -> &str {
        &self.api_key
    }
    fn sign_into(
        &self,
        _timestamp_ms: i64,
        _recv_window_ms: u32,
        _body: &str,
        out: &mut [u8; 64],
    ) -> Result<(), crate::bybit::sign::CredentialsError> {
        out.fill(b'a');
        Ok(())
    }
}

/// Запрет 1 горячего пути (дозапрос ревью таска 15): 10⁶ событий через
/// `process_event` (шаг цикла `run_react_over_feed`) на одном и том же
/// `ReactCoreState` — цена своей стороны спреда меняется каждое
/// `PRICE_CHANGE_EVERY`-е событие, то есть `rebuild` реально вызывается
/// десятки тысяч раз внутри измеряемого окна, не только на прогреве. И
/// путь «разбор → книга → триггер» на неизменной цене, и сам `rebuild`
/// на изменившейся — обязаны не аллоцировать после прогрева.
#[test]
fn process_event_allocates_nothing_per_event_after_warmup() {
    const WARMUP: usize = 2_000;
    const MEASURED: usize = 1_000_000;
    const PRICE_CHANGE_EVERY: i64 = 97;
    // Цикл через небольшой набор уровней — цифры числа не растут в
    // течение теста, иначе рост ёмкости строковых буферов внутри
    // измеряемого окна был бы артефактом сценария, а не находкой.
    const LEVEL_PERIOD: i64 = 5;

    let px = |n: i64| n * 10_000_000;
    let make_event = |i: usize| -> Event {
        let level = 100 + (i as i64 / PRICE_CHANGE_EVERY) % LEVEL_PERIOD;
        if i == 0 {
            return snapshot_event(0, px(level), px(level + 1));
        }
        let prev_level = 100 + ((i as i64 - 1) / PRICE_CHANGE_EVERY) % LEVEL_PERIOD;
        book_event(
            i as i64 * 1_000,
            Some(1_000),
            (i + 1) as u64,
            px(prev_level),
            px(level),
            px(prev_level + 1),
            px(level + 1),
        )
    };

    let signer = ZeroAllocSigner {
        api_key: "test-key".to_string(),
    };
    let cfg = cfg();
    let clock = MonotonicClock::start();
    let mut state = ReactCoreState::new(cfg.tick_e9, cfg.step_e9);
    // Ёмкость с запасом заранее, ровно на все проходы обоих циклов:
    // `process_event` теперь пишет образец на КАЖДОЕ событие (не только
    // на срабатывание, ремонт таска 15 по ревью), значит `samples`
    // растёт на единицу каждый вызов — `WARMUP + MEASURED` целиком,
    // иначе рост `Vec` реаллоцировал бы внутри измеряемого окна и
    // проверял бы рост буфера, а не `process_event` (та же ловушка,
    // что `ReadyMakerOrder`'s буферы решают через `with_capacity`).
    let mut samples = Vec::with_capacity(WARMUP + MEASURED);

    for i in 0..WARMUP {
        process_event(
            &mut state,
            &cfg,
            &signer,
            "SOLUSDT",
            &clock,
            make_event(i),
            &mut samples,
        );
    }

    let mut total_allocations = 0u64;
    for i in WARMUP..(WARMUP + MEASURED) {
        let event = make_event(i);
        let (_, counts) = crate::alloc_count::measure(|| {
            process_event(
                &mut state,
                &cfg,
                &signer,
                "SOLUSDT",
                &clock,
                event,
                &mut samples,
            )
        });
        total_allocations += counts.allocations;
    }
    let trigger_count = samples.iter().filter(|s| s.trigger.is_some()).count();
    assert!(
        trigger_count > MIN_TRIGGERS_FOR_GATE as usize,
        "сценарий обязан дать много срабатываний внутри измеряемого окна: {trigger_count}"
    );
    assert_eq!(
        samples.len(),
        WARMUP + MEASURED,
        "книга/разбор — на каждом событии, не только на срабатывании"
    );
    assert_eq!(
        total_allocations, 0,
        "process_event аллоцировал после прогрева — запрет 1 interfaces.md"
    );
}

/// Ремонт таска 15 по ревью: отрицательная стадия — дефект прогона
/// (метки recv/разбор и метки книги/триггера/send пришли не из одного
/// `Clock`), не число для печати. На прежнем коде (без
/// `check_no_negative_durations`) `finish_report` тихо построила бы
/// отчёт с `book_ns = -49_100` — этот тест обязан был бы упасть на нём:
/// `is_ok()` был бы `true`, а не `false`.
#[test]
fn a_negative_stage_fails_the_report_instead_of_printing_it() {
    let mut samples: Vec<ReactSample> = (0..MIN_TRIGGERS_FOR_GATE)
        .map(|_| ReactSample {
            parse_ns: Some(50_000),
            book_ns: 10_000,
            trigger: Some(TriggerLatency {
                trigger_ns: 5_000,
                order_ns: 5_000,
                full_ns: 1_000_000,
            }),
        })
        .collect();
    // Ровно симптом живого прогона `data/react-debug/20260911T110318Z/`:
    // «книга» отрицательна, когда метки идут не из одного домена часов.
    samples[3].book_ns = -49_100;
    let args = ReactArgs {
        symbol: "SOLUSDT".to_string(),
        tick_e9: 10_000_000,
        step_e9: 1_000_000,
        side: "buy".to_string(),
        qty_e9: 1_000_000,
        recv_window_ms: 5_000,
        minutes: 5,
        root: std::env::temp_dir(),
        out: Some(std::env::temp_dir().join("react-test-negative.csv")),
        host_id: None,
        probe_csv: None,
    };
    let result = finish_report(&args, samples, MIN_TRIGGERS_FOR_GATE);
    let err = result.expect_err("отрицательная стадия обязана провалить сборку отчёта");
    let message = format!("{err}");
    assert!(
        message.contains("FAIL: clock domain"),
        "сообщение обязано называть дефект честно, не число: {message}"
    );
    assert!(message.contains("книга"), "{message}");
}

/// Пустая выборка не печатает выдуманных чисел (правило 1
/// `interfaces.md`) — `summarize_stage`/`format_report` честно говорят
/// «нет данных», гейт G-LAT не объявляется на выборке меньше 1000.
#[test]
fn a_short_run_reports_debug_and_declares_no_gate() {
    let args = ReactArgs {
        symbol: "SOLUSDT".to_string(),
        tick_e9: 10_000_000,
        step_e9: 1_000_000,
        side: "buy".to_string(),
        qty_e9: 1_000_000,
        recv_window_ms: 5_000,
        minutes: 5,
        root: std::env::temp_dir(),
        out: Some(std::env::temp_dir().join("react-test-empty.csv")),
        host_id: Some("test-host".to_string()),
        probe_csv: None,
    };
    let rep = finish_report(&args, Vec::new(), 0).unwrap();
    assert!(!rep.gated);
    assert_eq!(rep.g_lat_pass, None);
    assert!(rep.horizons.is_empty());
    let printed = format_report(&rep);
    assert!(printed.contains("debug"));
    assert!(printed.contains("не объявлен"));
}

/// Гейт объявляется только на ≥ 1000 срабатываниях, и p99 полного пути
/// решает PASS/FAIL буквально по `G_LAT_BUDGET_NS`.
#[test]
fn gate_declared_and_passes_when_p99_is_under_budget_with_enough_triggers() {
    let samples: Vec<ReactSample> = (0..MIN_TRIGGERS_FOR_GATE)
        .map(|_| ReactSample {
            parse_ns: Some(50_000),
            book_ns: 10_000,
            trigger: Some(TriggerLatency {
                trigger_ns: 5_000,
                order_ns: 5_000,
                full_ns: 1_000_000, // 1мс — под бюджетом 5мс
            }),
        })
        .collect();
    let args = ReactArgs {
        symbol: "SOLUSDT".to_string(),
        tick_e9: 10_000_000,
        step_e9: 1_000_000,
        side: "buy".to_string(),
        qty_e9: 1_000_000,
        recv_window_ms: 5_000,
        minutes: 5,
        root: std::env::temp_dir(),
        out: Some(std::env::temp_dir().join("react-test-full.csv")),
        host_id: None,
        probe_csv: None,
    };
    let rep = finish_report(&args, samples, MIN_TRIGGERS_FOR_GATE).unwrap();
    assert!(rep.gated);
    assert_eq!(rep.g_lat_pass, Some(true));
    // 1мс — < 20% от каждого из четырёх горизонтов (100мс..60с) — все достижимы.
    assert!(
        rep.horizons.iter().all(|h| h.reachable),
        "{:?}",
        rep.horizons
    );
    assert!(rep.host_id == "unknown-host" || !rep.host_id.is_empty());
}

/// D-HOR буквально: `p99` больше 20% длины горизонта — `unreachable`, и
/// это не валит гейт G-LAT (тот смотрит только на общий бюджет 5мс).
#[test]
fn a_horizon_shorter_than_five_times_p99_is_marked_unreachable() {
    // p99 = 30мс: 20% от 100мс = 20мс -> недостижим; 20% от 1с = 200мс -> достижим.
    let samples: Vec<ReactSample> = (0..MIN_TRIGGERS_FOR_GATE)
        .map(|_| ReactSample {
            parse_ns: Some(50_000),
            book_ns: 10_000,
            trigger: Some(TriggerLatency {
                trigger_ns: 5_000,
                order_ns: 5_000,
                full_ns: 30_000_000,
            }),
        })
        .collect();
    let args = ReactArgs {
        symbol: "SOLUSDT".to_string(),
        tick_e9: 10_000_000,
        step_e9: 1_000_000,
        side: "buy".to_string(),
        qty_e9: 1_000_000,
        recv_window_ms: 5_000,
        minutes: 5,
        root: std::env::temp_dir(),
        out: Some(std::env::temp_dir().join("react-test-hor.csv")),
        host_id: None,
        probe_csv: None,
    };
    let rep = finish_report(&args, samples, MIN_TRIGGERS_FOR_GATE).unwrap();
    let h100 = rep.horizons.iter().find(|h| h.horizon_ms == 100).unwrap();
    let h1000 = rep.horizons.iter().find(|h| h.horizon_ms == 1_000).unwrap();
    assert!(!h100.reachable, "{h100:?}");
    assert!(h1000.reachable, "{h1000:?}");
}

/// Дозапрос ревью (BLOCKING, ось Предрегистрация): «весь путь» — только
/// срабатывания. 5000 событий книги, из которых только 10 —
/// срабатывания: гейт не объявляется (10 < 1000), несмотря на то что
/// событий книги куда больше тысячи, и `full_path.n` обязан быть 10, не
/// 5000 — смешение серий занизило бы p99 и объявило бы гейт там, где
/// план требует ≥ 1000 срабатываний, а не событий.
#[test]
fn full_path_series_counts_only_triggers_not_all_book_events() {
    let mut samples: Vec<ReactSample> = (0..4_990)
        .map(|_| ReactSample {
            parse_ns: Some(20_000),
            book_ns: 40_000,
            trigger: None,
        })
        .collect();
    samples.extend((0..10).map(|_| ReactSample {
        parse_ns: Some(20_000),
        book_ns: 40_000,
        trigger: Some(TriggerLatency {
            trigger_ns: 300,
            order_ns: 100,
            full_ns: 90_000,
        }),
    }));
    let args = ReactArgs {
        symbol: "SOLUSDT".to_string(),
        tick_e9: 10_000_000,
        step_e9: 1_000_000,
        side: "buy".to_string(),
        qty_e9: 1_000_000,
        recv_window_ms: 5_000,
        minutes: 5,
        root: std::env::temp_dir(),
        out: Some(std::env::temp_dir().join("react-test-mixing.csv")),
        host_id: None,
        probe_csv: None,
    };
    let rep = finish_report(&args, samples, 5_000).unwrap();
    assert!(
        !rep.gated,
        "10 срабатываний < 1000 — гейт не объявляется несмотря на 5000 событий книги"
    );
    assert_eq!(
        rep.full_path.unwrap().n,
        10,
        "«весь путь» — только срабатывания, событие без срабатывания в эту серию не входит"
    );
    assert_eq!(rep.g_lat_pass, None);
    assert!(
        rep.horizons.is_empty(),
        "горизонт не объявляется без объявленного гейта: {:?}",
        rep.horizons
    );
    // Информационная строка «recv → книга» — по всем 5000 событиям, под
    // своим именем, не смешана с «весь путь».
    assert_eq!(rep.recv_to_book.unwrap().n, 5_000);
    let printed = format_report(&rep);
    assert!(printed.contains("triggers=10 < 1000"), "{printed}");
    assert!(printed.contains("horizon: не объявлен"), "{printed}");
}

/// `--minutes` за пределами `1..=MAX_MINUTES` отклоняется до сети (нет
/// ключей, нет сокета — просто разбор аргументов и граница).
#[test]
fn minutes_outside_the_debug_ceiling_is_rejected_before_touching_the_network() {
    let args = ReactArgs {
        symbol: "SOLUSDT".to_string(),
        tick_e9: 10_000_000,
        step_e9: 1_000_000,
        side: "buy".to_string(),
        qty_e9: 1_000_000,
        recv_window_ms: 5_000,
        minutes: MAX_MINUTES + 1,
        root: std::env::temp_dir(),
        out: None,
        host_id: None,
        probe_csv: None,
    };
    assert!(run_react(&args).is_err());
}
