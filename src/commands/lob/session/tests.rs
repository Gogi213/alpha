use super::*;
// Элементы подмодулей, которые сам `session.rs` не импортирует — нужны только тестам.
use super::resources::resource_sample_period;
use super::sink::SinkFile;
use crate::binlog::Header;
use crate::binlog::Record;
use crate::bybit::rest::BYBIT_MAINNET_URL;
use crate::feed::replay::ReplayFeed;
use hftbacktest::types::{
    LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_EVENT, LOCAL_BID_DEPTH_SNAPSHOT_EVENT,
    LOCAL_BUY_TRADE_EVENT,
};
use std::collections::VecDeque;
use std::fs::File;

const TEST_TICK_E9: i64 = 1_000_000;
const TEST_STEP_E9: i64 = 1_000_000;

fn rec(ev: u64, exch_ts_ns: i64, price_ticks: i64, qty_lots: i64) -> Record {
    Record {
        ev,
        exch_ts_ns,
        local_ts_ns: exch_ts_ns,
        price_ticks,
        qty_lots,
        block: false,
    }
}

/// One resting snapshot (bid + ask, never touched again) followed by
/// `pairs` repetitions of (bid-depth delta, trade) — same shape as the
/// `record.rs::steady_events_allocate_nothing` precedent ("форма и
/// magnitudes не меняются, меняются только монотонные счётчики"), built
/// as a real binlog so the test drives `write_market_event` through
/// `ReplayFeed`, not by calling it directly — that is the shape ТУПИК 1
/// (handoff-04-1) named: "синтетический `ReplayFeed`", not a synthetic
/// `Event` fed straight to the function under test.
fn build_synthetic_binlog(pairs: usize) -> Vec<u8> {
    const PER_FRAME: usize = 20_000;
    // `max_records_per_frame` sizes the reader's decompression-bomb
    // ceiling as `declared_max * MIN_RECORD_LEN` (`binlog::mod::
    // max_frame_payload_bytes`) — a ceiling computed from records at
    // their *minimum* varint size, not their real one. Declaring it
    // equal to `PER_FRAME` made every chunk here reject once real
    // (non-zero-delta) encoding pushed a 20,000-record frame past that
    // tight ceiling (found by bisection: broke between 2,000 and 5,000
    // pairs). `binlog::tests::round_trips_one_million_events` sets this
    // field to 1,000,000 while writing the very same 20,000-record
    // chunks — the same headroom, reused here rather than reinvented.
    let header = Header {
        tick_e9: TEST_TICK_E9,
        step_e9: TEST_STEP_E9,
        max_records_per_frame: 1_000_000,
    };
    let mut w = Writer::create(Vec::new(), header, 0).unwrap();
    w.write_frame(&[
        rec(LOCAL_BID_DEPTH_SNAPSHOT_EVENT, 0, 100, 5),
        rec(LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, 0, 110, 5),
    ])
    .unwrap();

    let mut buf: Vec<Record> = Vec::with_capacity(PER_FRAME);
    for k in 0..pairs {
        let ts = 1_000_000 + (2 * k as i64 + 1) * 1_000;
        buf.push(rec(LOCAL_BID_DEPTH_EVENT, ts, 100, 5));
        buf.push(rec(LOCAL_BUY_TRADE_EVENT, ts + 1_000, 100, 1));
        if buf.len() >= PER_FRAME {
            w.write_frame(&buf).unwrap();
            buf.clear();
        }
    }
    if !buf.is_empty() {
        w.write_frame(&buf).unwrap();
    }
    w.into_inner()
}

const TEST_DAY: &str = "2026-09-12";

fn test_member(symbol: &str) -> PoolMember {
    PoolMember {
        symbol: symbol.to_string(),
        tick_e9: TEST_TICK_E9,
        step_e9: TEST_STEP_E9,
    }
}

fn fresh_state(root: &Path) -> SymbolState {
    open_symbol_state(root, &test_member("SYM"), TEST_DAY).unwrap()
}

fn state_with_writer(symbol: &str, writer: Writer<FrameSink>, part: u32) -> SymbolState {
    SymbolState {
        member: test_member(symbol),
        writer,
        part,
        day_index: crate::commands::record::day_index_of_day_str(TEST_DAY).unwrap(),
        book: Book::new(TEST_TICK_E9, TEST_STEP_E9),
        synced: false,
        has_snapshot: false,
        records_written: 0,
        rotate_retry_after_ns: i64::MIN,
        frames_failed: 0,
        scratch: Vec::with_capacity(128),
        batch: Vec::with_capacity(FRAME_TARGET_RECORDS + 128),
    }
}

/// ТУПИК 1 (handoff-04-1), closed: `write_market_event` used to build a
/// fresh `Vec<Record>` every call, which allocates on its first `push` —
/// one allocation per event on the hot path. Drives `state.scratch`
/// through `ReplayFeed` over 10⁶ synthetic events and sums
/// `alloc_count::measure` **around each `write_market_event` call
/// alone** (after a warmup that lets `Writer`'s internal buffers and
/// `state.scratch` reach steady-state capacity), not around
/// `feed.next_event()` — `ReplayFeed`'s own `FileReplayer`
/// (`bybit::verify.rs`, outside this task's zone) rebuilds its
/// `Update.bids`/`.asks` with `std::mem::take` on every flush, which
/// resets those `Vec`s to zero capacity and reallocates them on the next
/// push; that is a real, separate allocation source this test does not
/// claim to have fixed. What this test proves is exactly what ТУПИК 1
/// named: the function under test, not its synthetic data source.
#[test]
fn million_events_through_replay_feed_allocate_nothing_after_warmup() {
    const WARMUP_EVENTS: usize = 2_000;
    const MEASURED_EVENTS: usize = 1_000_000;
    // +1 snapshot event per 2 records (bid delta + trade); generous
    // margin so the feed never runs dry before `MEASURED_EVENTS` is hit.
    let pairs = (WARMUP_EVENTS + MEASURED_EVENTS) / 2 + 10;
    let bytes = build_synthetic_binlog(pairs);
    let mut feed = ReplayFeed::open(0, &bytes[..]).unwrap();

    let dir = tempfile::tempdir().unwrap();
    let mut state = fresh_state(dir.path());

    let mut next_market_event = || loop {
        match feed
            .next_event()
            .expect("синтетический ReplayFeed обязан покрыть весь замер")
        {
            Event::Market {
                local_ts_ns,
                payload,
                ..
            } => return (local_ts_ns, payload),
            Event::Gap { .. } | Event::Tick { .. } => continue,
        }
    };

    for _ in 0..WARMUP_EVENTS {
        let (local_ts_ns, payload) = next_market_event();
        write_market_event(&mut state, local_ts_ns, payload).unwrap();
    }

    let mut measured_allocations = 0u64;
    for _ in 0..MEASURED_EVENTS {
        let (local_ts_ns, payload) = next_market_event();
        let (_, counts) =
            crate::alloc_count::measure(|| write_market_event(&mut state, local_ts_ns, payload));
        measured_allocations += counts.allocations;
    }
    assert_eq!(
        measured_allocations, 0,
        "write_market_event обязана не аллоцировать после прогрева на 10^6 событий"
    );
}

/// Таск 24, критерий «батчинг записи»: пишет 2500 событий книги через
/// настоящий файловый `Writer` (`claim_symbol_binlog`, тот же путь, что
/// `run_session`), читает файл обратно `binlog::Reader` и проверяет два
/// свойства разом — (1) кадров на диске на порядки меньше, чем событий
/// (батчинг реально произошёл, а не осталась запись на событие под
/// другим именем переменной), (2) все записи читаются обратно в том же
/// порядке и с тем же содержимым, что построено напрямую из событий —
/// границы кадра не меняют то, что видит читатель (`bybit::verify::
/// FileReplayer` группирует по `(вид, exch_ts_ns)`, не по кадру, тот же
/// принцип проверяется здесь на уровне сырых `Record`).
#[test]
fn write_market_event_batches_many_messages_into_few_frames_and_round_trips() {
    const N: i64 = 2_500;
    let dir = tempfile::tempdir().unwrap();
    let (writer, part) = claim_symbol_binlog(
        dir.path(),
        "SOLUSDT",
        "2026-09-12",
        TEST_TICK_E9,
        TEST_STEP_E9,
    )
    .unwrap();
    assert_eq!(part, 1);
    let mut state = state_with_writer("SOLUSDT", writer, part);

    let mut expected_ticks = Vec::with_capacity(N as usize);
    for i in 0..N {
        // Первое обновление — снапшот: файл, начатый с дельт, нечитаем
        // (`has_snapshot`, тот же контракт, что `Recorder::NoSnapshot`).
        let update = crate::book::Update {
            is_snapshot: i == 0,
            u: i as u64 + 1,
            seq: i as u64 + 1,
            cts_ms: i,
            bids: vec![(TEST_TICK_E9 * (100 + i), TEST_STEP_E9)],
            asks: vec![],
        };
        expected_ticks.push(100 + i);
        write_market_event(&mut state, i * 1_000, crate::bybit::ws::Event::Book(update)).unwrap();
    }
    flush_symbol_batch(&mut state).unwrap();
    state.writer.flush().unwrap();
    assert_eq!(state.records_written, N as u64);

    let path = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-12", 1);
    let bytes = std::fs::read(&path).unwrap();
    let mut reader = crate::binlog::Reader::open(&bytes[..]).unwrap();
    let mut frames = 0u64;
    let mut got_ticks = Vec::with_capacity(N as usize);
    while let Some(frame) = reader.read_frame().unwrap() {
        frames += 1;
        for rec in frame {
            got_ticks.push(rec.price_ticks);
        }
    }
    assert_eq!(
        got_ticks, expected_ticks,
        "порядок и содержимое обязаны совпасть"
    );
    assert!(
        frames <= 5,
        "{N} событий по одному кадру на событие дали бы {N} кадров; батчинг обязан \
         уместить их в единицы кадров (порог {}), получено {frames}",
        crate::commands::record::FRAME_TARGET_RECORDS
    );
    assert!(
        frames >= 2,
        "2500 записей при пороге {} обязаны дать хотя бы два кадра (не один гигантский), \
         получено {frames}",
        crate::commands::record::FRAME_TARGET_RECORDS
    );
}

/// `LatencyHistogram`: перцентиль на известной синтетике — 100 значений
/// `1..=100` (микросекунды в наносекундах), p99 обязан попасть в бин,
/// чья нижняя граница не выше истинного значения (99 000 нс) и не ниже
/// его больше, чем на ширину бина (`RESOLUTION_PCT`).
/// Таск 25: ряд `samples` олвейс-он не растёт без потолка — раз в
/// `RESOURCE_SAMPLE_SECS` (гейт GC `PLAN.md` 6.1: «RSS раз в 30 с»)
/// первый час, дальше раз в `HOURLY_REFRESH_SECS` (существующий часовой
/// период, с которым и переписывается `session.json`). Ожидаемые числа —
/// из таблицы 6.1 и doc `HOURLY_REFRESH_SECS`, не из кода под тестом.
#[test]
fn resource_sample_period_is_30s_in_the_first_hour_then_hourly() {
    assert_eq!(resource_sample_period(0), Duration::from_secs(30));
    assert_eq!(resource_sample_period(3_599), Duration::from_secs(30));
    assert_eq!(resource_sample_period(3_600), Duration::from_secs(3_600));
    assert_eq!(resource_sample_period(86_400), Duration::from_secs(3_600));
}

#[test]
fn latency_histogram_p99_is_within_bin_resolution_of_the_true_value() {
    let mut h = LatencyHistogram::new();
    assert_eq!(h.percentile(99), None, "пустая гистограмма — не число");
    for v in 1..=100i64 {
        h.record(v * 1_000);
    }
    assert_eq!(h.len(), 100);
    let p99 = h
        .percentile(99)
        .expect("100 значений — перцентиль обязан быть числом");
    let true_value = 99_000i64;
    assert!(
        p99 <= true_value,
        "перцентиль — нижняя граница бина, обязан быть не выше истинного значения: \
         {p99} > {true_value}"
    );
    let tolerance = (true_value as f64 * LatencyHistogram::RESOLUTION_PCT / 100.0) as i64 + 1;
    assert!(
        true_value - p99 <= tolerance,
        "отклонение {} превышает разрешение бина {tolerance}",
        true_value - p99
    );
}

/// Таск 24, критерий «аллокаций на сообщение после прогрева на пути
/// разбор → книга → запись — измерено `alloc_count`-гейтом»: 10⁶
/// сообщений в формате площадки (дельты 0–5 уровней на сторону, лента
/// 1–10 сделок каждое десятое сообщение, снапшот 50+50 раз в 100 000)
/// через `ws::parse_message_into` (буфер вызывающего, как в
/// `bybit::conn`), `Book::apply` (книга `conn.rs`) и `write_market_event`
/// — те же три шага, что проходит живой кадр до диска. Текст сообщения
/// строится **до** замера (в бою его выделяет `tungstenite` —
/// «транспорт ≤ 1 на кадр, принято», `PLAN.md` 6.1), замер — только три
/// шага. Ожидаемые числа названы заранее, не подсмотрены: книжное
/// сообщение — по одному `Vec<(i64, i64)>` на **непустую** сторону
/// (`book::Update` владеет `bids`/`asks`, doc модуля `ws.rs`), лента —
/// ноль, снапшот — восемь (две стороны × (первая ёмкость + три роста
/// 8→16→32→64, `ws::LEVELS_INITIAL_CAPACITY`)). Средние по видам
/// печатаются в stderr — их кладёт в таблицу
/// `docs/findings/collector-2026-09-12.md`.
#[test]
fn parse_book_write_path_allocations_per_message_after_warmup() {
    use crate::book::Book;
    use crate::bybit::ws::{parse_message_into, Event as WsEvent};

    const WARMUP: usize = 2_000;
    const MEASURED: usize = 1_000_000;
    const SNAPSHOT_EVERY: usize = 100_000;
    // Тик 0.01 — как у цен фикстуры с двумя знаками: 50 уровней через
    // 0.01 = 50 тиков, внутри кольца книги (`book::CAPACITY` = 128).
    const TICK_E9: i64 = 10_000_000;
    const STEP_E9: i64 = 1_000_000;
    const SNAPSHOT: usize = 0;
    const DELTA: usize = 1;
    const TRADES: usize = 2;

    struct Rng(u64);
    impl Rng {
        fn next(&mut self) -> u64 {
            self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
            let mut z = self.0;
            z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
            z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
            z ^ (z >> 31)
        }
        fn below(&mut self, n: u64) -> u64 {
            self.next() % n
        }
    }
    fn levels(rng: &mut Rng, start_cents: u64, n: usize, ascending: bool) -> String {
        let mut out = String::from("[");
        for i in 0..n {
            if i > 0 {
                out.push(',');
            }
            let cents = if ascending {
                start_cents + i as u64
            } else {
                start_cents - i as u64
            };
            let milli = 1 + rng.below(50_000);
            out.push_str(&format!(
                "[\"{}.{:02}\",\"{}.{:03}\"]",
                cents / 100,
                cents % 100,
                milli / 1000,
                milli % 1000
            ));
        }
        out.push(']');
        out
    }
    fn book_msg(kind: &str, bids: &str, asks: &str, u: u64) -> String {
        format!(
            "{{\"topic\":\"orderbook.50.SOLUSDT\",\"type\":\"{kind}\",\"ts\":{},\
             \"data\":{{\"s\":\"SOLUSDT\",\"b\":{bids},\"a\":{asks},\"u\":{u},\
             \"seq\":{}}},\"cts\":{}}}",
            1_757_600_000_000u64 + u,
            u * 7,
            1_757_599_999_999u64 + u
        )
    }
    fn trades_msg(rng: &mut Rng, u: u64) -> String {
        let n = 1 + rng.below(10) as usize;
        let mut items = String::new();
        for i in 0..n {
            if i > 0 {
                items.push(',');
            }
            let side = if rng.below(2) == 0 { "Buy" } else { "Sell" };
            let milli = 1 + rng.below(50_000);
            let cents = 15_000 + rng.below(10);
            let id = rng.next();
            items.push_str(&format!(
                "{{\"T\":{},\"s\":\"SOLUSDT\",\"S\":\"{side}\",\"v\":\"{}.{:03}\",\
                 \"p\":\"{}.{:02}\",\"L\":\"PlusTick\",\"i\":\"{id:016x}\",\"BT\":false}}",
                1_757_600_000_000u64 + u,
                milli / 1000,
                milli % 1000,
                cents / 100,
                cents % 100
            ));
        }
        format!(
            "{{\"topic\":\"publicTrade.SOLUSDT\",\"type\":\"snapshot\",\"ts\":{},\
             \"data\":[{items}]}}",
            1_757_600_000_000u64 + u
        )
    }

    let dir = tempfile::tempdir().unwrap();
    let mut state = fresh_state(dir.path());
    let mut book = Book::new(TICK_E9, STEP_E9);
    let mut events: Vec<WsEvent> = Vec::new();
    let mut rng = Rng(0x5EED_2026_0912);
    let mut u: u64 = 0;

    let mut allocs_by_kind = [0u64; 3];
    let mut max_by_kind = [0u64; 3];
    let mut n_by_kind = [0u64; 3];
    for i in 0..(WARMUP + MEASURED) {
        let (kind, raw, expected_book_allocs) = if i % SNAPSHOT_EVERY == 0 {
            u += 1;
            let bids = levels(&mut rng, 15_000, 50, false);
            let asks = levels(&mut rng, 15_001, 50, true);
            (SNAPSHOT, book_msg("snapshot", &bids, &asks, u), Some(8))
        } else if i % 10 == 9 {
            (TRADES, trades_msg(&mut rng, u), None)
        } else {
            u += 1;
            let nb = rng.below(6) as usize;
            let na = rng.below(6) as usize;
            let bid_start = 15_000 - rng.below(20);
            let ask_start = 15_001 + rng.below(20);
            let bids = levels(&mut rng, bid_start, nb, false);
            let asks = levels(&mut rng, ask_start, na, true);
            let non_empty_sides = u64::from(nb > 0) + u64::from(na > 0);
            (
                DELTA,
                book_msg("delta", &bids, &asks, u),
                Some(non_empty_sides),
            )
        };
        let (_, counts) = crate::alloc_count::measure(|| {
            parse_message_into(&raw, &mut events).expect("фикстура обязана разбираться");
            for ev in events.drain(..) {
                if let WsEvent::Book(update) = &ev {
                    book.apply(update)
                        .expect("фикстура — валидная последовательность u");
                }
                write_market_event(&mut state, 1, ev).unwrap();
            }
        });
        if i < WARMUP {
            continue;
        }
        let allocs = counts.allocations;
        n_by_kind[kind] += 1;
        allocs_by_kind[kind] += allocs;
        max_by_kind[kind] = max_by_kind[kind].max(allocs);
        if let Some(expected) = expected_book_allocs {
            assert_eq!(
                allocs, expected,
                "сообщение #{i}: книжное сообщение обязано аллоцировать ровно по одному \
                 Vec на непустую сторону (снапшот — 8), получено {allocs}: {raw}"
            );
        }
    }
    assert_eq!(
        max_by_kind[TRADES], 0,
        "лента сделок после прогрева не аллоцирует — сделки идут прямо в буфер вызывающего"
    );
    for (name, kind) in [("snapshot", SNAPSHOT), ("delta", DELTA), ("trades", TRADES)] {
        eprintln!(
            "alloc gate: {name}: n={} allocs_per_msg_avg={:.3} max={}",
            n_by_kind[kind],
            allocs_by_kind[kind] as f64 / n_by_kind[kind].max(1) as f64,
            max_by_kind[kind]
        );
    }
    flush_symbol_batch(&mut state).unwrap();
    state.writer.flush().unwrap();
    assert!(state.records_written > 0);
}

fn args_with_minutes(minutes: u64) -> SessionArgs {
    SessionArgs {
        pool_instruments: Some(PathBuf::from("does/not/exist/instruments.csv")),
        all_instruments: false,
        root: PathBuf::from("does/not/exist/root"),
        minutes: Some(minutes),
        pilot_minutes: None,
        always_on: false,
        base_url: BYBIT_MAINNET_URL.to_string(),
        ntp_addr: "pool.ntp.org:123".to_string(),
    }
}

fn args_with_pilot_minutes(pilot_minutes: u32) -> SessionArgs {
    SessionArgs {
        pool_instruments: Some(PathBuf::from("does/not/exist/instruments.csv")),
        all_instruments: false,
        root: PathBuf::from("does/not/exist/root"),
        minutes: None,
        pilot_minutes: Some(pilot_minutes),
        always_on: false,
        base_url: BYBIT_MAINNET_URL.to_string(),
        ntp_addr: "pool.ntp.org:123".to_string(),
    }
}

/// История 7 / R38 дословно: «от пяти до пятнадцати минут». Значение вне
/// `MIN_MINUTES..=MAX_MINUTES` обязано провалиться на самой первой строке
/// `run_session` — до `create_dir_all`/`load_pool` — иначе сообщение об
/// ошибке пришло бы от отсутствующего `instruments.csv`, а не от границы
/// `--minutes`, и звало бы сеть/диск раньше проверки диапазона.
#[test]
fn run_session_rejects_minutes_outside_five_to_fifteen_before_touching_disk() {
    for bad in [0, MIN_MINUTES - 1, MAX_MINUTES + 1, 60] {
        let err = run_session(&args_with_minutes(bad)).expect_err("вне 5..=15 обязана быть ошибка");
        let msg = err.to_string();
        assert!(
            msg.contains("--minutes") && msg.contains(&bad.to_string()),
            "сообщение обязано называть флаг и полученное значение {bad}: {msg}"
        );
    }
}

/// Граничные значения (5 и 15) обязаны пройти проверку диапазона и
/// провалиться дальше, на отсутствующем `instruments.csv` — не на
/// сообщении про `--minutes`. Так тест ловит и «отклоняет legit
/// значения», и «ошибка не о границе» одним прогоном.
#[test]
fn run_session_accepts_boundary_minutes_and_proceeds_past_validation() {
    for ok in [MIN_MINUTES, MAX_MINUTES] {
        let err = run_session(&args_with_minutes(ok)).expect_err("несуществующий пул — ошибка");
        let msg = err.to_string();
        assert!(
            !msg.contains("--minutes"),
            "{ok} — валидная граница, ошибка обязана быть не про --minutes: {msg}"
        );
    }
}

/// Таск 22, критерий 1: `--pilot-minutes` вне
/// `MIN_PILOT_MINUTES..=MAX_PILOT_MINUTES` — ошибка до сети/диска, тем
/// же приёмом, что `--minutes` уже проверяется.
#[test]
fn run_session_rejects_pilot_minutes_outside_range_before_touching_disk() {
    for bad in [0, MIN_PILOT_MINUTES - 1, MAX_PILOT_MINUTES + 1, 1000] {
        let err = run_session(&args_with_pilot_minutes(bad))
            .expect_err("вне диапазона обязана быть ошибка");
        let msg = err.to_string();
        assert!(
            msg.contains("--pilot-minutes") && msg.contains(&bad.to_string()),
            "сообщение обязано называть флаг и полученное значение {bad}: {msg}"
        );
    }
}

/// Граничные значения пилота (16 и 360) обязаны пройти проверку
/// диапазона и провалиться дальше, на отсутствующем `instruments.csv`.
#[test]
fn run_session_accepts_boundary_pilot_minutes_and_proceeds_past_validation() {
    for ok in [MIN_PILOT_MINUTES, MAX_PILOT_MINUTES] {
        let err =
            run_session(&args_with_pilot_minutes(ok)).expect_err("несуществующий пул — ошибка");
        let msg = err.to_string();
        assert!(
            !msg.contains("--pilot-minutes"),
            "{ok} — валидная граница, ошибка обязана быть не про --pilot-minutes: {msg}"
        );
    }
}

/// `resolve_duration`: ни `--minutes`, ни `--pilot-minutes` — вторая
/// половина условия «ровно один обязателен», которую `clap
/// conflicts_with` не выражает (он только запрещает оба разом).
#[test]
fn resolve_duration_requires_exactly_one_of_minutes_or_pilot_minutes() {
    let args = SessionArgs {
        pool_instruments: Some(PathBuf::from("does/not/exist/instruments.csv")),
        all_instruments: false,
        root: PathBuf::from("does/not/exist/root"),
        minutes: None,
        pilot_minutes: None,
        always_on: false,
        base_url: BYBIT_MAINNET_URL.to_string(),
        ntp_addr: "pool.ntp.org:123".to_string(),
    };
    let err = resolve_duration(&args).expect_err("ни один флаг не задан — обязана быть ошибка");
    let msg = err.to_string();
    assert!(
        msg.contains("--minutes") && msg.contains("--pilot-minutes"),
        "сообщение обязано назвать оба флага: {msg}"
    );
}

/// `--minutes`/`--pilot-minutes` — `clap conflicts_with`: заданы оба
/// разом на самом парсере, не на уже собранной структуре
/// (`resolve_duration` эту комбинацию в принципе не видит).
#[test]
fn cli_rejects_minutes_and_pilot_minutes_given_together() {
    #[derive(Debug, clap::Parser)]
    struct TestCli {
        #[command(subcommand)]
        cmd: super::super::LobCommand,
    }
    use clap::Parser as _;
    let err = TestCli::try_parse_from([
        "t",
        "session",
        "--pool-instruments",
        "instruments.csv",
        "--root",
        "root",
        "--minutes",
        "10",
        "--pilot-minutes",
        "30",
    ])
    .expect_err("--minutes и --pilot-minutes разом обязаны конфликтовать");
    assert_eq!(
        err.kind(),
        clap::error::ErrorKind::ArgumentConflict,
        "{err}"
    );
}

/// `session.json.debug` — чистая функция от длительности (таск 17,
/// пункт 5б): `true`, пока сессия короче часа (`CLAUDE.md`: тестовый
/// прогон — фаза отладки, результат не данные), `false` начиная ровно с
/// часа. `MAX_MINUTES = 15` не даёт этой ветке случиться через сегодняшний
/// CLI — тест бьёт по самой функции, не по `run_session`.
#[test]
fn is_debug_session_true_under_an_hour_false_at_and_past_it() {
    assert!(is_debug_session(0));
    assert!(is_debug_session(3_599));
    assert!(!is_debug_session(3_600));
    assert!(!is_debug_session(3_601));
}

// -----------------------------------------------------------------
// Таск 19: `lob session` пишет `<SYMBOL>-<день>.binlog`, не
// `<SYMBOL>.binlog` — то имя, которое читают `verify`/`levels`/
// `markout` (слепая приёмка G4).
// -----------------------------------------------------------------

/// `claim_symbol_binlog` первой сессии суток обязана давать часть 1 —
/// ровно то же имя, что и прежняя жёсткая `session_binlog_path`
/// (`day_file_path(.., 1)`), до таска 22 единственный случай.
#[test]
fn claim_symbol_binlog_gives_part_one_on_a_fresh_day() {
    let dir = tempfile::tempdir().unwrap();
    let (_writer, part) = claim_symbol_binlog(
        dir.path(),
        "SOLUSDT",
        "2025-09-13",
        TEST_TICK_E9,
        TEST_STEP_E9,
    )
    .unwrap();
    assert_eq!(part, 1);
    assert!(
        crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2025-09-13", 1).is_file()
    );
}

// -----------------------------------------------------------------
// Таск 22: несколько сессий в те же сутки — `claim_symbol_binlog`
// обязана отдать следующий свободный номер, не затирая прежний файл;
// `session_binlog_for` (`mod.rs`) обязана прочитать обе части подряд.
// -----------------------------------------------------------------

/// Критерий приёмки 2 дословно: «две сессии подряд в один каталог дают
/// два файла и обе читаются». Читает содержимое обоих файлов (не только
/// их наличие) через `session_binlog_for`, чтобы отличить «часть 2
/// существует» от «часть 2 несёт свои собственные, а не чужие данные».
#[test]
fn two_sessions_in_a_row_into_one_directory_produce_two_parts_both_readable() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let day = "2026-09-08";

    let (mut w1, part1) =
        claim_symbol_binlog(root, "SOLUSDT", day, TEST_TICK_E9, TEST_STEP_E9).unwrap();
    w1.write_frame(&[rec(LOCAL_BID_DEPTH_SNAPSHOT_EVENT, 0, 100, 1)])
        .unwrap();
    w1.flush().unwrap();

    let (mut w2, part2) =
        claim_symbol_binlog(root, "SOLUSDT", day, TEST_TICK_E9, TEST_STEP_E9).unwrap();
    w2.write_frame(&[rec(LOCAL_BID_DEPTH_SNAPSHOT_EVENT, 0, 200, 2)])
        .unwrap();
    w2.flush().unwrap();

    assert_eq!(part1, 1, "первая сессия суток — часть 1, не затёрта");
    assert_eq!(part2, 2, "вторая сессия суток — новая часть, не часть 1");

    let paths = super::super::session_binlog_for(root, "SOLUSDT").unwrap();
    assert_eq!(paths.len(), 2, "обе части присутствуют");
    assert_eq!(
        paths[0],
        crate::commands::record::day_file_path(root, "SOLUSDT", day, 1)
    );
    assert_eq!(
        paths[1],
        crate::commands::record::day_file_path(root, "SOLUSDT", day, 2)
    );

    // "обе читаются": часть 1 несёт price_ticks=100, часть 2 — 200, в
    // том порядке, в котором `session_binlog_for` их вернула.
    for (path, expected_tick) in [(&paths[0], 100i64), (&paths[1], 200i64)] {
        let data = std::fs::read(path).unwrap();
        let mut reader = crate::binlog::Reader::open(&data[..]).unwrap();
        let frame = reader
            .read_frame()
            .unwrap()
            .expect("часть обязана нести свой кадр");
        assert_eq!(frame[0].price_ticks, expected_tick);
    }
}

/// Слепая приёмка G4: каталог, размеченный так, как `run_session`
/// раскладывает файлы начиная с этого таска (`<SYMBOL>-<день>.binlog`,
/// день часть 1 — та же `day_file_path`, что доказал предыдущий тест),
/// читается `verify`/`levels` без ручного переименования. Фикстура
/// собрана публичной `test_support::write_day` (тот же шов, что
/// `pilot.rs::process_instrument` уже использует для этой пары команд),
/// не вызовом `run_session` целиком — та сама открывает `LiveFeed`
/// (живой сокет), офлайн-тестам сеть недоступна (`CLAUDE.md`).
#[test]
fn directory_named_like_a_fresh_session_reads_through_verify_and_levels_untouched() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let day = "2026-09-08";

    super::super::test_support::write_day(
        root,
        "SOLUSDT",
        day,
        &super::super::test_support::three_level_frames(),
    );
    std::fs::write(
        crate::commands::record::instruments_csv_path(root),
        "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n\
         SOLUSDT,0.01,0.1,0.1,5,5\n",
    )
    .unwrap();

    let vs = crate::bybit::verify::run_verify(&crate::bybit::verify::VerifyArgs {
        symbol: "SOLUSDT".to_string(),
        root: root.to_path_buf(),
    })
    .expect("verify обязан найти суточный файл сессии без переименования");
    assert_eq!(vs.files, 1, "ровно один суточный файл сессии");

    let ls = super::super::levels::run_levels(&super::super::levels::LevelsArgs {
        root: root.to_path_buf(),
        symbol: "SOLUSDT".to_string(),
        h3: super::super::H3Args {
            h3_mode: super::super::H3ModeArg::Floor,
            h3_lots: None,
        },
        h3_k: None,
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        out: Some(root.join("levels-SOLUSDT.csv")),
    })
    .expect("levels обязан найти суточный файл сессии без переименования");
    assert!(
        ls.levels > 0,
        "фикстура three_level_frames обязана дать хотя бы один уровень"
    );
}
// -----------------------------------------------------------------
// Таск 25: олвейс-он — ядро над сценарным `Feed` (A5: вызывающий не
// различает источник), без сети.
// -----------------------------------------------------------------

enum Step {
    Ev(Event),
    Probe(Box<dyn FnMut()>),
}

/// Сценарный `Feed`: события по списку; `Probe` выполняется между
/// событиями — так тест смотрит на диск **во время** прогона, а не
/// после финального сброса, и отличает «кадр лёг по тику» от «кадр лёг
/// при закрытии».
struct ScriptedFeed(VecDeque<Step>);

impl Feed for ScriptedFeed {
    fn next_event(&mut self) -> Option<Event> {
        loop {
            match self.0.pop_front()? {
                Step::Ev(e) => return Some(e),
                Step::Probe(mut f) => f(),
            }
        }
    }
}

fn book_event(symbol: u16, local_ts_ns: i64, cts_ms: i64, is_snapshot: bool, u: u64) -> Event {
    Event::Market {
        symbol,
        local_ts_ns,
        parse_latency_ns: None,
        payload: crate::bybit::ws::Event::Book(crate::book::Update {
            is_snapshot,
            u,
            seq: u,
            cts_ms,
            bids: vec![(100 * TEST_TICK_E9, 5 * TEST_STEP_E9)],
            asks: vec![(110 * TEST_TICK_E9, 7 * TEST_STEP_E9)],
        }),
    }
}

fn frames_on_disk(path: &Path) -> Vec<Vec<Record>> {
    let data = std::fs::read(path).unwrap();
    let mut reader = crate::binlog::Reader::open(&data[..]).unwrap();
    let mut out = Vec::new();
    while let Some(frame) = reader.read_frame().unwrap() {
        out.push(frame);
    }
    out
}

fn always_on_ctx(root: &Path, started_ns: i64) -> SessionCtx {
    SessionCtx::open(
        root,
        &[test_member("SYM")],
        SessionPlan::AlwaysOn,
        started_ns,
    )
    .unwrap()
}

/// 2026-09-12T12:00:00Z в наносекундах — сутки `TEST_DAY`.
const NOON_NS: i64 = 1_789_214_400 * 1_000_000_000;

/// Критерий «тихий инструмент даёт кадр на диске не позже окна» и
/// «сброс — по таймеру рантайма»: снапшот, одна дельта, потом только тик
/// — проба между тиком и концом прогона обязана увидеть дельту кадром
/// на диске (2 кадра: снапшот, дельта), хотя финального сброса ещё не
/// было. До таска 25 дельта ждала часового таймера или конца сессии.
#[test]
fn silent_instrument_frame_reaches_disk_on_the_tick_not_at_shutdown() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let path = crate::commands::record::day_file_path(&root, "SYM", TEST_DAY, 1);
    let seen = std::sync::Arc::new(std::sync::Mutex::new(0usize));
    let seen_probe = seen.clone();
    let probe_path = path.clone();
    let window_ns = FRAME_LOSS_WINDOW_SECS as i64 * 1_000_000_000;
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(book_event(0, NOON_NS, NOON_NS / 1_000_000, true, 1)),
        Step::Ev(book_event(
            0,
            NOON_NS + 1,
            NOON_NS / 1_000_000 + 1,
            false,
            2,
        )),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + window_ns,
        }),
        Step::Probe(Box::new(move || {
            *seen_probe.lock().unwrap() = frames_on_disk(&probe_path).len();
        })),
    ]));
    run_session_loop(&mut feed, &mut ctx).unwrap();
    assert_eq!(
        *seen.lock().unwrap(),
        2,
        "после тика на диске обязаны лежать оба кадра — снапшот и дельта"
    );
}

/// Критерий «ноль событий → session.json на диске не позже окна» плюс
/// остановка сигналом-заменителем: `None` от `Feed` (то, во что
/// `StopHandle::stop` превращает Ctrl+C) — финальный `session.json` с
/// `closed = true`, `always_on = true`; до него — периодический с
/// `closed = false`, уже читаемый.
#[test]
fn zero_events_still_writes_session_json_and_stop_closes_it() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let mid = std::sync::Arc::new(std::sync::Mutex::new(None::<SessionSummary>));
    let mid_probe = mid.clone();
    let probe_root = root.clone();
    let hour_ns = HOURLY_REFRESH_SECS as i64 * 1_000_000_000;
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + hour_ns,
        }),
        Step::Probe(Box::new(move || {
            let text = std::fs::read_to_string(probe_root.join("session.json")).unwrap();
            *mid_probe.lock().unwrap() = Some(serde_json::from_str(&text).unwrap());
        })),
    ]));
    // Финальная запись — дело шва, не теста: `None` от `Feed` обязан
    // закрыть `session.json` сам.
    let final_summary = run_session_loop(&mut feed, &mut ctx).unwrap();
    let mid = mid
        .lock()
        .unwrap()
        .take()
        .expect("часовой тик обязан переписать session.json");
    assert!(
        mid.always_on && !mid.closed,
        "периодическая запись — не финальная"
    );
    assert!(
        final_summary.closed,
        "после None от Feed — финальная запись"
    );
    assert_eq!(final_summary.records_total, 0);
    let on_disk: SessionSummary =
        serde_json::from_str(&std::fs::read_to_string(root.join("session.json")).unwrap()).unwrap();
    assert!(on_disk.closed && on_disk.always_on);
}

/// Ротация по суткам UTC: событие биржи следующих суток открывает новую
/// часть (`claim_part_with`, часть 1 новых суток), первым кадром —
/// синтетический снапшот книги (обе стороны), затем сама дельта;
/// `binlog_files` получает вторую часть с `started_utc` новых суток;
/// `session_binlog_for` читает обе части по порядку.
#[test]
fn always_on_rotates_at_utc_midnight_with_a_synthetic_snapshot_first() {
    use hftbacktest::types::{LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_SNAPSHOT_EVENT};
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let next_day_ns = NOON_NS + 12 * 3600 * 1_000_000_000 + 1_000_000;
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(book_event(0, NOON_NS, NOON_NS / 1_000_000, true, 1)),
        Step::Ev(book_event(
            0,
            NOON_NS + 1,
            NOON_NS / 1_000_000 + 1,
            false,
            2,
        )),
        Step::Ev(book_event(
            0,
            next_day_ns,
            next_day_ns / 1_000_000,
            false,
            3,
        )),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx).unwrap();

    let paths = super::super::session_binlog_for(&root, "SYM").unwrap();
    assert_eq!(paths.len(), 2, "две части: сутки D и D+1");
    assert_eq!(
        paths[1],
        crate::commands::record::day_file_path(&root, "SYM", "2026-09-13", 1)
    );
    let frames = frames_on_disk(&paths[1]);
    assert!(frames.len() >= 2, "снапшот и дельта: {}", frames.len());
    let first = &frames[0];
    assert_eq!(
        first.len(),
        2,
        "синтетический снапшот — по записи на уровень"
    );
    assert_eq!(first[0].ev, LOCAL_BID_DEPTH_SNAPSHOT_EVENT);
    assert_eq!(first[0].price_ticks, 100);
    assert_eq!(first[1].ev, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT);
    assert_eq!(first[1].price_ticks, 110);
    assert_eq!(summary.binlog_files.len(), 2);
    assert!(summary.binlog_files[1]
        .started_utc
        .starts_with("2026-09-13"));
    assert_eq!(summary.binlog_files[1].part, 1);
    let parts = super::super::session_parts_for(&root, "SYM").unwrap();
    assert_eq!(parts[1].day_utc, "2026-09-13");
    assert_eq!(parts[1].start_hour_utc, 0);
}

/// Ротация только вперёд: после ухода в сутки D+1 опоздавшее событие
/// суток D (сделка с `T` раньше `cts` принятой дельты) пишется в
/// текущую часть D+1 — новой части `-p2` суток D не появляется,
/// `binlog_files` не растёт, запись лежит в файле D+1 со своей меткой.
#[test]
fn late_event_of_the_previous_day_stays_in_the_current_part() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let next_day_ns = NOON_NS + 12 * 3600 * 1_000_000_000 + 1_000_000;
    let late_ms = NOON_NS / 1_000_000 + 2;
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(book_event(0, NOON_NS, NOON_NS / 1_000_000, true, 1)),
        Step::Ev(book_event(
            0,
            next_day_ns,
            next_day_ns / 1_000_000,
            false,
            2,
        )),
        Step::Ev(book_event(0, next_day_ns + 1, late_ms, false, 3)),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx).unwrap();
    assert_eq!(summary.binlog_files.len(), 2, "D и D+1, без -p2 для D");
    let paths = super::super::session_binlog_for(&root, "SYM").unwrap();
    assert_eq!(paths.len(), 2);
    let frames = frames_on_disk(&paths[1]);
    let last = frames.last().unwrap();
    assert_eq!(
        last.last().unwrap().exch_ts_ns,
        late_ms * 1_000_000,
        "опоздавшее событие — в части D+1"
    );
    assert!(
        !crate::commands::record::day_file_path(&root, "SYM", TEST_DAY, 2).exists(),
        "части -p2 прежних суток быть не должно"
    );
}

/// Файл под приёмником, роняющий N-й `write_all` посреди кадра
/// (половина байт на диск, потом ошибка) — то, что делает диск при
/// нехватке места или отвале тома.
struct HalfFailingFile {
    inner: File,
    writes: u32,
    fail_on: u32,
}

impl std::io::Write for HalfFailingFile {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.writes += 1;
        if self.writes == self.fail_on {
            self.inner.write_all(&buf[..buf.len() / 2])?;
            return Err(std::io::Error::other("диск: нет места"));
        }
        self.inner.write(buf)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        self.inner.flush()
    }
}

impl SinkFile for HalfFailingFile {
    fn truncate_to(&mut self, len: u64) -> std::io::Result<()> {
        self.inner.truncate_to(len)
    }
}

/// Ревью таска 25: упавшая запись кадра не оставляет обрывка на диске.
/// Снапшот (кадр 1), дельта → тик (кадр 2 падает на половине), дельта →
/// тик (кадр 3): `frames_failed = 1`, ровно одна строка `write_failed`
/// в `gaps.csv`, а файл читается `Reader` целиком до конца — два
/// кадра, снапшот и третий, без `ShortRead`.
#[test]
fn failed_frame_write_truncates_to_frame_boundary_and_the_part_stays_readable() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let sink_dir = tempfile::tempdir().unwrap();
    // Заголовок копится в буфере приёмника до первого сброса и уходит
    // одним `write_all` со снапшотом (№1); дельты — №2 (падает), №3.
    let (writer, part) = claim_part_with(
        sink_dir.path(),
        "SYM",
        TEST_DAY,
        1,
        TEST_TICK_E9,
        TEST_STEP_E9,
        |file| {
            FrameSink::over(Box::new(HalfFailingFile {
                inner: file,
                writes: 0,
                fail_on: 2,
            }))
        },
    )
    .unwrap();
    ctx.states[0].writer = writer;
    ctx.states[0].part = part;
    let ms = NOON_NS / 1_000_000;
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(book_event(0, NOON_NS, ms, true, 1)),
        Step::Ev(book_event(0, NOON_NS + 1, ms + 1, false, 2)),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + 10_000_000_000,
        }),
        Step::Ev(book_event(0, NOON_NS + 2, ms + 2, false, 3)),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + 20_000_000_000,
        }),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx).unwrap();
    assert_eq!(summary.frames_failed, 1);
    let gaps = std::fs::read_to_string(gaps_csv_path(&root)).unwrap();
    assert_eq!(
        gaps.matches("write_failed").count(),
        1,
        "одна строка write_failed: {gaps}"
    );
    let path = crate::commands::record::day_file_path(sink_dir.path(), "SYM", TEST_DAY, 1);
    let frames = frames_on_disk(&path);
    assert_eq!(
        frames.len(),
        2,
        "снапшот и третий кадр, обрывка второго нет"
    );
    assert_eq!(frames[1][0].exch_ts_ns, (ms + 2) * 1_000_000);
    assert_eq!(
        std::fs::metadata(&path).unwrap().len(),
        summary.bytes_written,
        "файл кончается ровно на границе последнего целого кадра"
    );
}

/// Таск 28. Разрыв мультиплексированного сокета доходит до каждого его
/// инструмента: строк `gaps.csv` — по инструменту, а `reconnects` — один
/// на сокет (копии приходят `DisconnectedSameSocket`). Кадр с
/// неразрешённым топиком считается отдельно (`unrouted`) и строки
/// `gaps.csv` не получает — у неё колонка `symbol` обязательна, а чей
/// это кадр, неизвестно.
#[test]
fn socket_close_gives_a_row_per_instrument_one_reconnect_and_unrouted_is_counted_apart() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let gap = |symbol, kind| Event::Gap {
        symbol,
        local_ts_ns: NOON_NS,
        kind,
        detail: "разрыв".to_string(),
    };
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(gap(0, FeedGapKind::Disconnected)),
        Step::Ev(gap(0, FeedGapKind::DisconnectedSameSocket)),
        Step::Ev(gap(0, FeedGapKind::Unrouted)),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + 20_000_000_000,
        }),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx).unwrap();
    assert_eq!(
        (summary.reconnects, summary.gaps, summary.unrouted),
        (1, 2, 1),
        "один разрыв сокета, две строки gaps.csv, один неразрешённый кадр"
    );
    let gaps = std::fs::read_to_string(gaps_csv_path(&root)).unwrap();
    assert_eq!(
        gaps.lines().count(),
        3,
        "заголовок и две строки разрыва, неразрешённого кадра там нет: {gaps}"
    );
}

/// Переподключение и ресинк — числом в `session.json` и строкой в
/// `gaps.csv` каждый: без них сутки записи нечем оценить.
#[test]
fn reconnects_and_resyncs_are_counted_and_logged() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let gap = |kind, detail: &str| Event::Gap {
        symbol: 0,
        local_ts_ns: NOON_NS,
        kind,
        detail: detail.to_string(),
    };
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(gap(FeedGapKind::Disconnected, "транспорт переподключился")),
        Step::Ev(gap(FeedGapKind::SequenceGap, "разрыв u")),
        Step::Ev(gap(FeedGapKind::BookInvariant, "книга нарушена")),
        Step::Ev(gap(FeedGapKind::ParseFailed, "кадр не разобрался")),
    ]));
    run_session_loop(&mut feed, &mut ctx).unwrap();
    let summary = ctx.write_session_json(true).unwrap();
    assert_eq!(summary.reconnects, 1);
    assert_eq!(summary.resyncs, 2);
    assert_eq!(summary.gaps, 4);
    let rows = crate::commands::record::read_gap_rows(&gaps_csv_path(&root)).unwrap();
    assert_eq!(rows.len(), 4, "строка на каждый разрыв");
    assert_eq!(rows[0].kind, GapKind::SequenceGap);
    assert_eq!(rows[1].kind, GapKind::SequenceGap);
    assert_eq!(rows[2].kind, GapKind::BookInvariant);
    assert_eq!(rows[3].kind, GapKind::ParseError);
}

/// `--always-on` взаимоисключающий с `--minutes`/`--pilot-minutes` на
/// самом парсере; `resolve_duration` даёт `AlwaysOn` без дедлайна.
#[test]
fn always_on_conflicts_with_timed_flags_and_resolves_without_deadline() {
    #[derive(Debug, clap::Parser)]
    struct TestCli {
        #[command(subcommand)]
        cmd: super::super::LobCommand,
    }
    use clap::Parser as _;
    for other in [["--minutes", "10"], ["--pilot-minutes", "30"]] {
        let err = TestCli::try_parse_from([
            "t",
            "session",
            "--pool-instruments",
            "instruments.csv",
            "--root",
            "root",
            "--always-on",
            other[0],
            other[1],
        ])
        .expect_err("--always-on с таймированным флагом обязан конфликтовать");
        assert_eq!(
            err.kind(),
            clap::error::ErrorKind::ArgumentConflict,
            "{err}"
        );
    }
    let args = SessionArgs {
        pool_instruments: Some(PathBuf::from("does/not/exist/instruments.csv")),
        all_instruments: false,
        root: PathBuf::from("does/not/exist/root"),
        minutes: None,
        pilot_minutes: None,
        always_on: true,
        base_url: BYBIT_MAINNET_URL.to_string(),
        ntp_addr: "pool.ntp.org:123".to_string(),
    };
    assert_eq!(resolve_duration(&args).unwrap(), SessionPlan::AlwaysOn);
}

// -----------------------------------------------------------------------
// Таск 34: пул на ходу — `<root>/instruments.csv` живой файл.
// -----------------------------------------------------------------------

/// Сценарный `Feed` без расширения: то же, что реплей, — источник
/// статичен, `add` отвечает `StaticSource`. Тесты выше про пул не знают,
/// им это и нужно.
impl DynamicPool for ScriptedFeed {
    fn add(
        &mut self,
        _members: Vec<PoolMember>,
    ) -> Result<Vec<u16>, crate::feed::live::LayoutError> {
        Err(crate::feed::live::LayoutError::StaticSource)
    }
}

/// Сценарный `Feed`, который умеет расти: `add` продолжает нумерацию с
/// `pool_len` (как `LiveFeed`) и запоминает, что добавляли — тест
/// проверяет, что партия ушла в источник ровно один раз.
struct GrowingFeed {
    steps: ScriptedFeed,
    pool_len: usize,
    added: std::sync::Arc<std::sync::Mutex<Vec<String>>>,
}

impl Feed for GrowingFeed {
    fn next_event(&mut self) -> Option<Event> {
        self.steps.next_event()
    }
}

impl DynamicPool for GrowingFeed {
    fn add(
        &mut self,
        members: Vec<PoolMember>,
    ) -> Result<Vec<u16>, crate::feed::live::LayoutError> {
        let mut out = Vec::with_capacity(members.len());
        for m in members {
            out.push(u16::try_from(self.pool_len).unwrap());
            self.pool_len += 1;
            self.added.lock().unwrap().push(m.symbol);
        }
        Ok(out)
    }
}

const POOL_CSV_HEADER: &str = "symbol,tick_size,min_order_qty,qty_step\n";

/// Дописывает строку в `<root>/instruments.csv` (создаёт с заголовком,
/// если файла нет). Короткая пауза перед записью — чтобы `mtime` заведомо
/// отличался от предыдущей записи на любой файловой системе.
fn append_pool_row(root: &Path, row: &str) {
    std::thread::sleep(Duration::from_millis(20));
    let path = root.join("instruments.csv");
    let mut text = std::fs::read_to_string(&path).unwrap_or_else(|_| POOL_CSV_HEADER.to_string());
    text.push_str(row);
    text.push('\n');
    std::fs::write(&path, text).unwrap();
}

fn session_json_on_disk(root: &Path) -> SessionSummary {
    serde_json::from_str(&std::fs::read_to_string(root.join("session.json")).unwrap()).unwrap()
}

/// Критерии таска 34: между двумя тиками в `instruments.csv` дописана
/// строка → появился `<SYM>-<день>.binlog` с заголовком из строки (tick
/// 0.5, step 0.001 — не тестовые `TEST_TICK_E9`/`TEST_STEP_E9`, чтобы
/// видеть, что шаги пришли из файла), `session.json.instruments` и
/// `binlog_files` выросли на один; событие нового символа с индексом 1
/// легло в **его** файл, а не в файл первого (индекс = позиция в `states`);
/// повторная запись той же строки ничего не дублирует; битая строка —
/// только stderr, состав прежний; в источник партия ушла один раз.
#[test]
fn a_row_appended_to_instruments_csv_between_ticks_joins_the_recording_once() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    // Файл с уже пишущимся символом лежит до старта — как после `cp
    // instruments.csv <root>/` из `CLAUDE.md`; его повтор ничего не даёт.
    append_pool_row(&root, "SYM,0.001,1,0.001");
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let window_ns = FRAME_LOSS_WINDOW_SECS as i64 * 1_000_000_000;
    let added = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    let after_first = std::sync::Arc::new(std::sync::Mutex::new(None::<SessionSummary>));
    let after_first_probe = after_first.clone();
    let after_first_root = root.clone();
    let (r1, r2, r3) = (root.clone(), root.clone(), root.clone());
    let new_path = crate::commands::record::day_file_path(&root, "NEWUSDT", TEST_DAY, 1);
    let sym_path = crate::commands::record::day_file_path(&root, "SYM", TEST_DAY, 1);
    let new_probe = new_path.clone();
    let mut feed = GrowingFeed {
        pool_len: 1,
        added: added.clone(),
        steps: ScriptedFeed(VecDeque::from(vec![
            Step::Ev(Event::Tick {
                local_ts_ns: NOON_NS + window_ns,
            }),
            Step::Probe(Box::new(move || {
                assert!(
                    !new_probe.exists(),
                    "до строки в instruments.csv файла нового символа быть не должно"
                );
                append_pool_row(&r1, "NEWUSDT,0.5,1,0.001");
            })),
            Step::Ev(Event::Tick {
                local_ts_ns: NOON_NS + 2 * window_ns,
            }),
            Step::Probe(Box::new(move || {
                *after_first_probe.lock().unwrap() = Some(session_json_on_disk(&after_first_root));
                // Та же строка ещё раз — дубликата быть не должно.
                append_pool_row(&r2, "NEWUSDT,0.5,1,0.001");
            })),
            // Снапшот нового символа под индексом 1 — обязан лечь в его файл.
            Step::Ev(book_event(
                1,
                NOON_NS + 2 * window_ns + 1,
                NOON_NS / 1_000_000,
                true,
                1,
            )),
            Step::Ev(Event::Tick {
                local_ts_ns: NOON_NS + 3 * window_ns,
            }),
            Step::Probe(Box::new(move || {
                // Битая строка: tick_size не число — только stderr.
                append_pool_row(&r3, "BADUSDT,not-a-number,1,0.001");
            })),
            Step::Ev(Event::Tick {
                local_ts_ns: NOON_NS + 4 * window_ns,
            }),
        ])),
    };
    let summary = run_session_loop(&mut feed, &mut ctx).unwrap();

    let after_first = after_first
        .lock()
        .unwrap()
        .take()
        .expect("после тика с новой строкой session.json обязан быть переписан");
    assert_eq!(
        after_first.instruments,
        vec!["SYM".to_string(), "NEWUSDT".to_string()],
        "новый символ — в session.json.instruments сразу после тика"
    );
    assert_eq!(
        after_first
            .binlog_files
            .iter()
            .map(|p| (p.symbol.as_str(), p.part))
            .collect::<Vec<_>>(),
        vec![("SYM", 1), ("NEWUSDT", 1)],
        "binlog_files вырос на часть нового символа"
    );

    let header = crate::binlog::Reader::open(&std::fs::read(&new_path).unwrap()[..])
        .unwrap()
        .header();
    assert_eq!(
        (header.tick_e9, header.step_e9),
        (500_000_000, 1_000_000),
        "tick/step заголовка — из строки instruments.csv, не из первого символа"
    );
    assert_eq!(
        frames_on_disk(&new_path).len(),
        1,
        "снапшот с индексом 1 обязан лечь в файл нового символа"
    );
    // У первого символа событий не было: файл либо ещё без заголовка
    // (буфер писателя не сброшен — кадров не было), либо без кадров.
    let sym_bytes = std::fs::read(&sym_path).unwrap();
    assert!(
        sym_bytes.is_empty() || frames_on_disk(&sym_path).is_empty(),
        "в файл первого символа ничего чужого не попало"
    );

    assert_eq!(
        *added.lock().unwrap(),
        vec!["NEWUSDT".to_string()],
        "в источник партия уходит ровно один раз: повтор строки и битая строка не добавляют"
    );
    assert_eq!(
        summary.instruments,
        vec!["SYM".to_string(), "NEWUSDT".to_string()],
        "битая строка не меняет состав, дубликата нет"
    );
    assert_eq!(summary.binlog_files.len(), 2);
    assert!(
        !crate::commands::record::day_file_path(&root, "BADUSDT", TEST_DAY, 1).exists(),
        "битой строке файла не открывали"
    );
}

/// Источник, который не растёт (реплей, `StaticSource`): строка в файле
/// — stderr, файл части убран, состав прежний; цикл не падает.
#[test]
fn a_static_feed_refuses_the_batch_and_the_recording_stays_as_it_was() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let window_ns = FRAME_LOSS_WINDOW_SECS as i64 * 1_000_000_000;
    let r1 = root.clone();
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Probe(Box::new(move || {
            append_pool_row(&r1, "NEWUSDT,0.5,1,0.001")
        })),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + window_ns,
        }),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx).unwrap();
    assert_eq!(summary.instruments, vec!["SYM".to_string()]);
    assert!(
        !crate::commands::record::day_file_path(&root, "NEWUSDT", TEST_DAY, 1).exists(),
        "источник отказал — файл части не должен остаться"
    );
}

/// Семь запретов после добавления: событие рынка нового символа идёт тем
/// же `write_market_event`, что и у стартовых, — ноль аллокаций после
/// прогрева (тот же гейт `alloc_count`, что у сессии; аллокации — только в
/// момент добавления).
#[test]
fn events_of_an_added_symbol_allocate_nothing_after_warmup() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    let mut feed = GrowingFeed {
        pool_len: 1,
        added: std::sync::Arc::new(std::sync::Mutex::new(Vec::new())),
        steps: ScriptedFeed(VecDeque::new()),
    };
    append_pool_row(&root, "NEWUSDT,0.001,1,0.001");
    ctx.check_pool_file(&mut feed, NOON_NS);
    assert_eq!(ctx.states.len(), 2, "символ добавлен");
    let mut u = 1u64;
    let mut delta = || {
        u += 1;
        crate::bybit::ws::Event::Book(crate::book::Update {
            is_snapshot: false,
            u,
            seq: u,
            cts_ms: NOON_NS / 1_000_000,
            bids: vec![(100 * TEST_TICK_E9, 5 * TEST_STEP_E9)],
            asks: vec![],
        })
    };
    // Снапшот и прогрев — до замера.
    let snapshot = crate::bybit::ws::Event::Book(crate::book::Update {
        is_snapshot: true,
        u: 1,
        seq: 1,
        cts_ms: NOON_NS / 1_000_000,
        bids: vec![(100 * TEST_TICK_E9, 5 * TEST_STEP_E9)],
        asks: vec![(110 * TEST_TICK_E9, 7 * TEST_STEP_E9)],
    });
    write_market_event(&mut ctx.states[1], NOON_NS, snapshot).unwrap();
    for _ in 0..2 * FRAME_TARGET_RECORDS {
        let payload = delta();
        write_market_event(&mut ctx.states[1], NOON_NS, payload).unwrap();
    }
    let mut allocations = 0u64;
    for _ in 0..10_000 {
        let payload = delta();
        let (_, counts) = crate::alloc_count::measure(|| {
            write_market_event(&mut ctx.states[1], NOON_NS, payload)
        });
        allocations += counts.allocations;
    }
    assert_eq!(
        allocations, 0,
        "после добавления — ноль аллокаций на событие рынка"
    );
}

/// В-41: `<root>/stop` останавливает сессию на ближайшем тике штатно —
/// `closed = true`, события после него не читаются; старый `stop` на старте
/// удаляется и новую сессию не останавливает.
#[test]
fn a_stop_file_ends_the_session_on_the_next_tick_and_is_cleared_on_open() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    std::fs::write(root.join("stop"), "").unwrap();
    let mut ctx = always_on_ctx(&root, NOON_NS);
    assert!(
        !root.join("stop").exists(),
        "старый stop обязан быть удалён на старте"
    );
    let window_ns = FRAME_LOSS_WINDOW_SECS as i64 * 1_000_000_000;
    let stop_root = root.clone();
    let consumed = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let consumed_probe = consumed.clone();
    let mut feed = ScriptedFeed(VecDeque::from(vec![
        Step::Ev(book_event(0, NOON_NS, NOON_NS / 1_000_000, true, 1)),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + window_ns,
        }),
        Step::Probe(Box::new(move || {
            std::fs::write(stop_root.join("stop"), "").unwrap();
        })),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + 2 * window_ns,
        }),
        Step::Probe(Box::new(move || {
            consumed_probe.store(true, std::sync::atomic::Ordering::SeqCst);
        })),
        Step::Ev(Event::Tick {
            local_ts_ns: NOON_NS + 3 * window_ns,
        }),
    ]));
    let summary = run_session_loop(&mut feed, &mut ctx).unwrap();
    assert!(summary.closed, "stop обязан дать штатное закрытие");
    assert!(
        !consumed.load(std::sync::atomic::Ordering::SeqCst),
        "после stop цикл не должен читать следующие события"
    );
}
