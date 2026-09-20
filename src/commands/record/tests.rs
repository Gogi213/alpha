use super::{
    append_gap_row, check_level_step, day_file_path, drain_latest_steps, ensure_gaps_csv,
    gaps_csv_path, read_gap_rows, request_steps_refresh, resolve_suspicion,
    spawn_steps_authority_with_rest, GapKind, GapRow, Recorder,
};

const TICK_E9: i64 = 10_000_000; // 0.01
const STEP_E9: i64 = 1_000_000; // 0.001
const DAY: &str = "2026-09-08";

fn snapshot_update() -> crate::book::Update {
    crate::book::Update {
        is_snapshot: true,
        depth: 50,
        u: 1,
        seq: 1,
        cts_ms: 1_757_800_000_000,
        bids: vec![(150_000_000_000, 2_500_000_000)],
        asks: vec![(150_010_000_000, 3_000_000_000)],
    }
}

fn open_recorder(dir: &tempfile::TempDir) -> Recorder {
    Recorder::open(dir.path(), "SOLUSDT", TICK_E9, STEP_E9, DAY).unwrap()
}

fn snapshot_book() -> crate::book::Book {
    let mut book = crate::book::Book::new(TICK_E9, STEP_E9);
    book.apply(&snapshot_update()).unwrap();
    book
}

/// Требуемый тест шага 0.3 (`PLAN.md`): цена, не кратная сохранённому
/// шагу, даёт ротацию и строку в `gaps.csv`, а не молчаливое продолжение.
#[test]
fn off_tick_price_rotates_and_leaves_a_gap_row_instead_of_continuing_silently() {
    let dir = tempfile::tempdir().unwrap();
    let mut rec = open_recorder(&dir);
    let mut book = snapshot_book();
    rec.on_snapshot(&book, 1_757_800_000_000_000_000, 1_757_800_000_001_000_000)
        .unwrap();

    // Цена 150.005 не кратна тику 0.01 — горячий детектор обязан отказать,
    // а не записать дельту молча.
    let bad = crate::book::Update {
        is_snapshot: false,
        depth: 50,
        u: 2,
        seq: 2,
        cts_ms: 1_757_800_000_020,
        bids: vec![(150_005_000_000, 1_000_000_000)],
        asks: vec![],
    };
    assert!(
        rec.stage_book_update(
            &mut book,
            &bad,
            1_757_800_000_020_000_000,
            1_757_800_000_021_000_000
        )
        .is_err(),
        "цена не на тике обязана отвергаться, а не писаться молча"
    );

    // Авторитетное значение из часового перечитывания instruments-info:
    // биржа уполовинила тик, и 150.005 ему кратен.
    rec.rotate_on_step_change(
        5_000_000,
        STEP_E9,
        "2026-09-08T00:00:20Z",
        "price 150.005 not multiple of tick 0.01",
    )
    .unwrap();

    assert_eq!(rec.tick_e9(), 5_000_000, "новый файл несёт новый шаг цены");
    let gaps = read_gap_rows(&gaps_csv_path(dir.path())).unwrap();
    assert_eq!(gaps.len(), 1, "одна ротация — одна строка в gaps.csv");
    assert_eq!(gaps[0].kind, GapKind::StepChange);
}

/// Парный тест: валидная дельта пишется молча в ХОРОШЕМ смысле — без
/// ротации и без строк в gaps.csv. Без него предыдущий тест не отличил бы
/// «отвергает некратное» от «отвергает всё подряд».
#[test]
fn on_tick_delta_is_staged_without_rotation_and_without_gap_rows() {
    let dir = tempfile::tempdir().unwrap();
    let mut rec = open_recorder(&dir);
    let mut book = snapshot_book();
    rec.on_snapshot(&book, 1_757_800_000_000_000_000, 1_757_800_000_001_000_000)
        .unwrap();

    let delta = crate::book::Update {
        is_snapshot: false,
        depth: 50,
        u: 2,
        seq: 2,
        cts_ms: 1_757_800_000_020,
        bids: vec![(150_000_000_000, 3_000_000_000)],
        asks: vec![],
    };
    let n = rec
        .stage_book_update(
            &mut book,
            &delta,
            1_757_800_000_020_000_000,
            1_757_800_000_021_000_000,
        )
        .unwrap();
    assert_eq!(n, 1, "одна запись на один изменённый уровень (Decision 23)");
    assert_eq!(rec.part(), 1, "ротации не было");
    let gaps = read_gap_rows(&gaps_csv_path(dir.path())).unwrap();
    assert!(
        gaps.is_empty(),
        "нормальная запись не оставляет строк в gaps.csv"
    );
}

/// Горячий детектор — чистая функция: ноль и отрицательные значения тоже
/// обязаны проверяться тем же `%`, а не особым путём.
#[test]
fn step_detector_accepts_multiples_and_rejects_everything_else() {
    assert!(check_level_step(150_000_000_000, 1_000_000_000, TICK_E9, STEP_E9).is_ok());
    // Удаление уровня (размер 0) кратно любому шагу — оно обязано проходить.
    assert!(check_level_step(150_000_000_000, 0, TICK_E9, STEP_E9).is_ok());
    let v = check_level_step(150_005_000_000, 1_000_000_000, TICK_E9, STEP_E9).unwrap_err();
    assert_eq!(v.price_e9, 150_005_000_000);
    assert_eq!(v.tick_e9, TICK_E9);
    let v = check_level_step(150_000_000_000, 1_500_000, TICK_E9, STEP_E9).unwrap_err();
    assert_eq!(v.qty_e9, 1_500_000);
}

/// Имя части 1 — каноническое, без суффикса; следующие — с `-pN`.
#[test]
fn day_file_names_are_canonical_for_part_one_and_suffixed_after() {
    let root = std::path::Path::new("data/bybit");
    assert_eq!(
        day_file_path(root, "SOLUSDT", DAY, 1),
        root.join("SOLUSDT-2026-09-08.binlog")
    );
    assert_eq!(
        day_file_path(root, "SOLUSDT", DAY, 2),
        root.join("SOLUSDT-2026-09-08-p2.binlog")
    );
}

/// `gaps.csv` создаётся с шапкой и нулём строк; повторный вызов ничего не
/// дописывает; строка дописывается и читается назад теми же значениями
/// (это же фиксирует имена колонок и `GapKind`, от которых зависит чтение
/// старых файлов).
#[test]
fn gap_row_round_trips_and_header_is_stable() {
    let dir = tempfile::tempdir().unwrap();
    let path = gaps_csv_path(dir.path());
    ensure_gaps_csv(&path).unwrap();
    ensure_gaps_csv(&path).unwrap();
    let header = std::fs::read_to_string(&path).unwrap();
    assert_eq!(
        header.trim_end(),
        "ts_utc,symbol,kind,detail",
        "шапка gaps.csv — контракт, не оформление"
    );
    assert!(
        read_gap_rows(&path).unwrap().is_empty(),
        "шапка без данных — ноль строк"
    );

    let row = GapRow {
        ts_utc: "2026-09-08T00:00:20Z".to_string(),
        symbol: "SOLUSDT".to_string(),
        kind: GapKind::StepChange,
        detail: "tick 0.01 -> 0.005".to_string(),
    };
    append_gap_row(&path, &row).unwrap();
    assert_eq!(read_gap_rows(&path).unwrap(), vec![row.clone()]);

    // A8.3: имя нового класса зафиксировано строкой файла — его читают
    // `verify` (шов покрытия) и глазами оператор, а `serde(rename_all)`
    // меняет имена молча.
    let refused = GapRow {
        kind: GapKind::SubscribeFailed,
        detail: "подписка не состоялась: orderbook.50.ZZZFAKEUSDT".to_string(),
        ..row.clone()
    };
    append_gap_row(&path, &refused).unwrap();
    let text = std::fs::read_to_string(&path).unwrap();
    assert!(
        text.contains(",subscribe_failed,"),
        "класс строки — `subscribe_failed`: {text}"
    );
    assert_eq!(read_gap_rows(&path).unwrap(), vec![row, refused]);
}

/// Дата считается по UTC, а не по локальному часовому поясу хоста:
/// якоря на эпохе и на отрицательной метке, плюс живая дата из других
/// тестов этого файла (1_757_800_000 секунд — целое число суток).
#[test]
fn day_string_of_ns_uses_utc_not_the_hosts_timezone() {
    assert_eq!(super::day_string_of_ns(0).unwrap(), "1970-01-01");
    assert_eq!(
        super::day_string_of_ns(86_400_000_000_000 - 1).unwrap(),
        "1970-01-01"
    );
    assert_eq!(
        super::day_string_of_ns(86_400_000_000_000).unwrap(),
        "1970-01-02"
    );
    assert_eq!(super::day_string_of_ns(-1).unwrap(), "1969-12-31");
    assert_eq!(
        super::day_string_of_ns(1_757_800_000_000_000_000).unwrap(),
        "2025-09-13"
    );
}

/// Полночь UTC закрывает сутки и открывает новые тем же заголовком, без
/// строки в gaps.csv; новые сутки читаются с первого байта: заголовок +
/// снапшот первым кадром (Decision 7: сутки самодостаточны).
#[test]
fn utc_midnight_rotates_to_a_new_self_sufficient_file() {
    let dir = tempfile::tempdir().unwrap();
    let mut rec = open_recorder(&dir);
    let book = snapshot_book();
    rec.on_snapshot(&book, 1_757_800_000_000_000_000, 1_757_800_000_001_000_000)
        .unwrap();
    rec.flush().unwrap();
    let day_one_file = rec.current_file();

    assert!(!rec.ensure_day(DAY).unwrap(), "те же сутки — не ротация");
    assert_eq!(rec.current_file(), day_one_file);

    assert!(
        rec.ensure_day("2025-09-14").unwrap(),
        "следующие сутки UTC — ротация"
    );
    assert_eq!(rec.day(), "2025-09-14");
    // Снапшот новых суток пишется в новый файл первым кадром.
    rec.on_snapshot(&book, 1_757_808_000_000_000_000, 1_757_808_000_001_000_000)
        .unwrap();
    rec.flush().unwrap();

    let gaps = read_gap_rows(&gaps_csv_path(dir.path())).unwrap();
    assert!(
        gaps.is_empty(),
        "полуночная ротация — не разрыв, строк в gaps.csv нет"
    );

    // Новые сутки читаются с первого байта без старых: заголовок несёт
    // шаги, первый кадр — снапшот тех же уровней.
    let file = std::fs::File::open(rec.current_file()).unwrap();
    let mut reader = crate::binlog::Reader::open(file).unwrap();
    assert_eq!(reader.header().tick_e9, TICK_E9);
    assert_eq!(reader.header().step_e9, STEP_E9);
    assert_eq!(
        reader.header().max_records_per_frame,
        super::MAX_RECORDS_PER_FRAME
    );
    let frame = reader.read_frame().unwrap().expect("кадр 0 — снапшот");
    assert_eq!(frame.len(), 2, "два уровня снапшота");
    assert!(
        frame.iter().all(
            |r| r.ev == hftbacktest::types::LOCAL_BID_DEPTH_SNAPSHOT_EVENT
                || r.ev == hftbacktest::types::LOCAL_ASK_DEPTH_SNAPSHOT_EVENT
        ),
        "кадр 0 — только снапшотные флаги"
    );

    // Старые сутки целы: заголовок и снапшот на месте.
    let old = std::fs::File::open(day_one_file).unwrap();
    let mut old_reader = crate::binlog::Reader::open(old).unwrap();
    assert_eq!(old_reader.header().tick_e9, TICK_E9);
    assert!(old_reader.read_frame().unwrap().is_some());
}

/// Заголовок суточного файла несёт шаг цены и шаг количества
/// (Decision 7: дельты в тиках без масштаба в том же файле
/// невосстановимы) — читается назад тем же `Reader`, что читает кадры.
#[test]
fn daily_file_header_carries_tick_size_and_qty_step() {
    let dir = tempfile::tempdir().unwrap();
    let mut rec = Recorder::open(dir.path(), "BTCUSDT", 100, 10, DAY).unwrap();
    let mut book = crate::book::Book::new(100, 10);
    book.apply(&crate::book::Update {
        is_snapshot: true,
        depth: 50,
        u: 1,
        seq: 1,
        cts_ms: 0,
        bids: vec![(1_000, 50)],
        asks: vec![(1_100, 60)],
    })
    .unwrap();
    rec.on_snapshot(&book, 0, 1).unwrap();
    rec.flush().unwrap();

    let file = std::fs::File::open(rec.current_file()).unwrap();
    let reader = crate::binlog::Reader::open(file).unwrap();
    assert_eq!(reader.header().tick_e9, 100);
    assert_eq!(reader.header().step_e9, 10);
}

/// Шаги для заголовка берутся из `instruments.csv` шага 0.4 — тем же
/// разбором и в том же масштабе. Все четыре числа инструмента различны:
/// перепутанные колонки `tick_size`/`qty_step`/`min_order_qty` прошли бы
/// тест с совпадающими значениями незамеченными (тот же приём, что
/// `instruments_csv_round_trips_and_is_nonempty` в `lob.rs`).
#[test]
fn steps_come_from_the_right_columns_of_instruments_csv() {
    use crate::bybit::rest::Instrument;
    let dir = tempfile::tempdir().unwrap();
    let path = super::instruments_csv_path(dir.path());
    crate::commands::lob::write_instruments_csv(
        &path,
        &[Instrument {
            symbol: "SOLUSDT".to_string(),
            base_coin: "SOL".to_string(),
            quote_coin: "USDT".to_string(),
            contract_type: "LinearPerpetual".to_string(),
            status: "Trading".to_string(),
            launch_time_ms: Some(1_600_000_000_000),
            tick_e9: 10_000_000,           // 0.01
            min_order_qty_e9: 500_000_000, // 0.5 — не шаг!
            qty_step_e9: 250_000_000,      // 0.25 — не минлот!
            min_notional_value_e9: 5_000_000_000,
        }],
    )
    .unwrap();

    assert_eq!(
        super::load_steps_for_symbol(&path, "SOLUSDT").unwrap(),
        (10_000_000, 250_000_000),
        "tick из tick_size, step из qty_step, а не из соседних колонок"
    );
    assert!(
        super::load_steps_for_symbol(&path, "NOSUCHUSDT").is_err(),
        "чужой символ — ошибка, а не шаги соседа"
    );
}

/// Индекс строки дня совпадает с целочисленным делением меток — иначе
/// горячий путь ротировал бы не на той границе, что именует файлы.
#[test]
fn day_index_matches_ns_division() {
    for (ts_ns, day) in [
        (0i64, "1970-01-01"),
        (86_400_000_000_000 - 1, "1970-01-01"),
        (86_400_000_000_000, "1970-01-02"),
        (-1, "1969-12-31"),
        (1_757_800_000_000_000_000, "2025-09-13"),
    ] {
        assert_eq!(super::day_string_of_ns(ts_ns).unwrap(), day);
        assert_eq!(
            super::day_index_of_day_str(day).unwrap(),
            ts_ns.div_euclid(super::NS_PER_DAY),
            "строка и деление обязаны указывать на одни сутки"
        );
    }
    assert!(super::day_index_of_day_str("13.09.2025").is_err());
}

/// CLI-поверхность `lob record`: символ обязателен, корень и REST-хост —
/// с дефолтами. Без сети: только разбор аргументов.
#[test]
fn record_cli_parses_symbol_and_defaults() {
    #[derive(clap::Parser)]
    struct TestCli {
        #[command(subcommand)]
        cmd: crate::commands::lob::LobCommand,
    }
    use clap::Parser as _;
    let cli = TestCli::try_parse_from(["t", "record", "--symbol", "SOLUSDT"]).unwrap();
    match cli.cmd {
        crate::commands::lob::LobCommand::Record(args) => {
            assert_eq!(args.symbol, "SOLUSDT");
            assert_eq!(args.root, std::path::PathBuf::from("data/bybit"));
        }
        crate::commands::lob::LobCommand::Pick(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::FeeRate(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::Latency(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::Verify(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::Export(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::Clock(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::Probe(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::Levels(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::Markout(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::Watch(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::Pilot(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::Power(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::Session(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::React(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::Profiles(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::Backtest(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::BounceGrid(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::BounceVerdict(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::FillCapacity(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::Shortlist(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::Dashboard(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::Touches(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::TouchProfiles(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::BinlogStats(_) => {
            panic!("разобралась не та подкоманда")
        }
        crate::commands::lob::LobCommand::Archive(_) => {
            panic!("разобралась не та подкоманда")
        }
    }
}

/// Контракт порядка: живое событие до первого снапшота — громкий
/// `NoSnapshot`, а не запись в файл без кадра 0 и не паника.
#[test]
fn staging_before_the_first_snapshot_is_a_loud_error() {
    let dir = tempfile::tempdir().unwrap();
    let mut rec = open_recorder(&dir);
    let mut book = crate::book::Book::new(TICK_E9, STEP_E9);
    let delta = crate::book::Update {
        is_snapshot: false,
        depth: 50,
        u: 2,
        seq: 2,
        cts_ms: 1_757_800_000_020,
        bids: vec![(150_000_000_000, 1_000_000_000)],
        asks: vec![],
    };
    assert_eq!(
        rec.stage_book_update(
            &mut book,
            &delta,
            1_757_800_000_020_000_000,
            1_757_800_000_021_000_000
        )
        .unwrap_err(),
        super::RecordError::NoSnapshot
    );
    let trade = crate::bybit::ws::Trade {
        exch_ms: 1_757_800_000_020,
        price_e9: 150_000_000_000,
        qty_e9: 1_000_000_000,
        aggressor_is_buy: true,
        block: false,
        rpi: false,
    };
    assert_eq!(
        rec.stage_trade(&trade, 1_757_800_000_021_000_000)
            .unwrap_err(),
        super::RecordError::NoSnapshot
    );
}

/// Разрыв `u` — это `SequenceGap` с числами (для строки gaps.csv), книга и
/// файл не тронуты: пропущенное событие не пишется «как ни в чём не
/// бывало», файл ждёт снапшота.
#[test]
fn sequence_gap_is_reported_loudly_and_stages_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let mut rec = open_recorder(&dir);
    let mut book = snapshot_book();
    rec.on_snapshot(&book, 1_757_800_000_000_000_000, 1_757_800_000_001_000_000)
        .unwrap();
    let before = rec.records_total();

    let skipped = crate::book::Update {
        is_snapshot: false,
        depth: 50,
        u: 4, // ждали 2
        seq: 4,
        cts_ms: 1_757_800_000_040,
        bids: vec![(150_000_000_000, 9_000_000_000)],
        asks: vec![],
    };
    assert_eq!(
        rec.stage_book_update(
            &mut book,
            &skipped,
            1_757_800_000_040_000_000,
            1_757_800_000_041_000_000
        )
        .unwrap_err(),
        super::RecordError::SequenceGap {
            expected: 2,
            got: 4
        }
    );
    rec.log_gap(
        super::GapKind::SequenceGap,
        "2025-09-13T21:46:40Z",
        "ждали u=2, пришло u=4",
    )
    .unwrap();
    assert_eq!(rec.records_total(), before);
    let gaps = read_gap_rows(&gaps_csv_path(dir.path())).unwrap();
    assert_eq!(gaps.len(), 1);
    assert_eq!(gaps[0].kind, super::GapKind::SequenceGap);
}

/// Сделки пишутся по одной записи: сторона агрессора — флагом, блочная —
/// `block = true`, цена/размер — в тиках/лотах. Сырые события пишутся все
/// (Decision 7), фильтр `BT` — дело разметки по `block`, не записи.
#[test]
fn trades_are_staged_with_side_block_flag_and_tick_lot_scale() {
    let dir = tempfile::tempdir().unwrap();
    let mut rec = open_recorder(&dir);
    let book = snapshot_book();
    rec.on_snapshot(&book, 1_757_800_000_000_000_000, 1_757_800_000_001_000_000)
        .unwrap();

    let buy = crate::bybit::ws::Trade {
        exch_ms: 1_757_800_000_100,
        price_e9: 150_000_000_000,
        qty_e9: 2_000_000_000,
        aggressor_is_buy: true,
        block: false,
        rpi: false,
    };
    let block_sell = crate::bybit::ws::Trade {
        exch_ms: 1_757_800_000_101,
        price_e9: 149_990_000_000,
        qty_e9: 5_000_000_000,
        aggressor_is_buy: false,
        block: true,
        rpi: false,
    };
    rec.stage_trade(&buy, 1_757_800_000_101_000_000).unwrap();
    rec.stage_trade(&block_sell, 1_757_800_000_102_000_000)
        .unwrap();
    rec.flush().unwrap();

    let file = std::fs::File::open(rec.current_file()).unwrap();
    let mut reader = crate::binlog::Reader::open(file).unwrap();
    reader.read_frame().unwrap().expect("кадр 0 — снапшот");
    let trades = reader.read_frame().unwrap().expect("кадр 1 — сделки");
    assert_eq!(trades.len(), 2);
    assert_eq!(trades[0].ev, hftbacktest::types::LOCAL_BUY_TRADE_EVENT);
    assert_eq!(trades[0].price_ticks, 150_000_000_000 / TICK_E9);
    assert_eq!(trades[0].qty_lots, 2_000_000_000 / STEP_E9);
    assert!(!trades[0].block);
    assert_eq!(
        trades[0].exch_ts_ns,
        1_757_800_000_100i64.saturating_mul(1_000_000)
    );
    assert_eq!(trades[1].ev, hftbacktest::types::LOCAL_SELL_TRADE_EVENT);
    assert!(trades[1].block, "блочная сделка помечена, но записана");
}

/// Гейт GC для шага 0.3: установившийся поток (те же цены и размеры, что
/// в прогреве) не аллоцирует на событие. Замер — тем же счётчиком на
/// глобальном аллокаторе, что `steady_frames_allocate_nothing` в
/// `src/lob/levels.rs`: от уже разобранных `Update`/`Trade` (граница
/// `ws.rs`) через книгу и батч до кадра на диске.
#[test]
fn steady_events_allocate_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let mut rec = open_recorder(&dir);
    let mut book = snapshot_book();
    rec.on_snapshot(&book, 1_757_800_000_000_000_000, 1_757_800_000_001_000_000)
        .unwrap();

    // Один шаблон дельты и одна сделка на все итерации: меняются только
    // монотонные счётчики (`u`), форма и magnitudes — нет, иначе замер
    // ловил бы рост буферов сжатия, а не аллокатор.
    let mut upd = crate::book::Update {
        is_snapshot: false,
        depth: 50,
        u: 2,
        seq: 2,
        cts_ms: 1_757_800_000_020,
        bids: vec![(150_000_000_000, 3_000_000_000)],
        asks: vec![(150_010_000_000, 4_000_000_000)],
    };
    let trade = crate::bybit::ws::Trade {
        exch_ms: 1_757_800_000_020,
        price_e9: 150_000_000_000,
        qty_e9: 1_000_000_000,
        aggressor_is_buy: true,
        block: false,
        rpi: false,
    };
    // Прогрев вне замера: книга, батч и Writer при рабочей ёмкости.
    for k in 0..2_000u64 {
        upd.u = 2 + k;
        upd.seq = 2 + k;
        rec.stage_book_update(
            &mut book,
            &upd,
            1_757_800_000_020_000_000,
            1_757_800_000_021_000_000,
        )
        .unwrap();
        rec.stage_trade(&trade, 1_757_800_000_021_000_000).unwrap();
    }
    let (_, counts) = crate::alloc_count::measure(|| {
        for k in 0..100_000u64 {
            upd.u = 2_002 + k;
            upd.seq = 2_002 + k;
            rec.stage_book_update(
                &mut book,
                &upd,
                1_757_800_000_020_000_000,
                1_757_800_000_021_000_000,
            )
            .unwrap();
            rec.stage_trade(&trade, 1_757_800_000_021_000_000).unwrap();
        }
    });
    assert_eq!(
        counts.allocations, 0,
        "горячий путь обязан не аллоцировать после прогрева"
    );
}

/// Шаг 0.7: дрен канала авторитета берёт только последнее, пустой канал —
/// `None` (ветка подавления, а не ожидание сети).
#[test]
fn authority_drain_takes_only_the_latest_and_empty_is_none() {
    let (tx, mut rx) = std::sync::mpsc::channel::<(i64, i64)>();
    assert_eq!(drain_latest_steps(&mut rx), None);
    tx.send((10_000_000, 1_000_000)).unwrap();
    tx.send((5_000_000, 1_000_000)).unwrap();
    assert_eq!(drain_latest_steps(&mut rx), Some((5_000_000, 1_000_000)));
    assert_eq!(drain_latest_steps(&mut rx), None);
}

struct FakeStepsRest {
    responses: std::collections::VecDeque<Result<String, crate::bybit::rest::RestError>>,
}

impl crate::bybit::rest::PublicRest for FakeStepsRest {
    fn get(
        &mut self,
        _path: &str,
        _query: &[(&str, &str)],
    ) -> Result<String, crate::bybit::rest::RestError> {
        self.responses
            .pop_front()
            .expect("тест не подготовил столько ответов")
    }
}

fn fake_instruments_body(symbol: &str, tick: &str, step: &str) -> String {
    format!(
        r#"{{"retCode":0,"retMsg":"OK","result":{{"category":"linear","list":[{{"symbol":"{symbol}","contractType":"LinearPerpetual","status":"Trading","baseCoin":"SOL","quoteCoin":"USDT","priceFilter":{{"tickSize":"{tick}"}},"lotSizeFilter":{{"minOrderQty":"0.1","qtyStep":"{step}"}}}}],"nextPageCursor":""}}}}"#
    )
}

fn fake_steps_rest(bodies: Vec<String>) -> FakeStepsRest {
    FakeStepsRest {
        responses: bodies.into_iter().map(Ok).collect(),
    }
}

/// Ремонт 0.7 Р1 (В-6): подозрение будит авторитет, не блокируя цикл.
/// Первая часть — горячий путь не ждёт: `try_send` на полном канале
/// возвращается сразу, а не висит до приёма. Вторая — авторитет после
/// пробуждения делает второй fetch за секунды, а не через час.
#[test]
fn suspicion_wakes_authority_without_blocking_hot_path() {
    let (wake_tx, wake_rx) = std::sync::mpsc::sync_channel::<()>(1);
    let old = fake_instruments_body("SOLUSDT", "0.01", "0.001");
    let new = fake_instruments_body("SOLUSDT", "0.005", "0.001");
    let steps_rx = spawn_steps_authority_with_rest(
        fake_steps_rest(vec![old, new]),
        "SOLUSDT".to_string(),
        wake_rx,
    );
    let first = steps_rx
        .recv_timeout(std::time::Duration::from_secs(5))
        .expect("первый fetch обязан прийти без пробуждения");
    assert_eq!(first, (TICK_E9, STEP_E9));

    let (full_tx, full_rx) = std::sync::mpsc::sync_channel::<()>(1);
    full_tx.try_send(()).unwrap();
    let probe_tx = full_tx.clone();
    let (done_tx, done_rx) = std::sync::mpsc::channel::<()>();
    std::thread::spawn(move || {
        request_steps_refresh(&probe_tx);
        let _ = done_tx.send(());
    });
    done_rx
        .recv_timeout(std::time::Duration::from_secs(2))
        .expect("горячий путь заблокировался на полном канале-будильнике");
    drop(full_rx);

    request_steps_refresh(&wake_tx);
    let second = steps_rx
        .recv_timeout(std::time::Duration::from_secs(5))
        .expect("авторитет обязан проснуться по подозрению, а не через час");
    assert_eq!(second, (5_000_000, STEP_E9));
}

/// Ремонт 0.7 Р2 (В-7): весь стык на фейке — подозрение, ответ авторитета
/// с новыми шагами, ротация файла, строка `step_change` в `gaps.csv`.
#[test]
fn suspicion_with_new_steps_rotates_file_and_leaves_gap_row() {
    let dir = tempfile::tempdir().unwrap();
    let mut rec = open_recorder(&dir);
    let old = fake_instruments_body("SOLUSDT", "0.01", "0.001");
    let new = fake_instruments_body("SOLUSDT", "0.005", "0.001");
    let (wake_tx, wake_rx) = std::sync::mpsc::sync_channel::<()>(1);
    let mut steps_rx = spawn_steps_authority_with_rest(
        fake_steps_rest(vec![old, new]),
        "SOLUSDT".to_string(),
        wake_rx,
    );
    let first = steps_rx
        .recv_timeout(std::time::Duration::from_secs(5))
        .expect("первый fetch");
    assert_eq!(first, (TICK_E9, STEP_E9));
    drain_latest_steps(&mut steps_rx);

    request_steps_refresh(&wake_tx);
    let fresh = steps_rx
        .recv_timeout(std::time::Duration::from_secs(5))
        .expect("свежие шаги после пробуждения");
    assert_eq!(fresh, (5_000_000, STEP_E9));

    let mut logged = false;
    let mut suppressed = 0u64;
    let rotated = resolve_suspicion(
        &mut rec,
        Some(fresh),
        "2026-09-08T00:00:20Z",
        "price 150005000000 не на шаге 10000000",
        &mut logged,
        &mut suppressed,
    )
    .unwrap();
    assert_eq!(rotated, Some((5_000_000, STEP_E9)));
    assert_eq!(rec.tick_e9(), 5_000_000);
    assert_eq!(rec.part(), 2);
    let gaps = read_gap_rows(&gaps_csv_path(dir.path())).unwrap();
    assert_eq!(gaps.len(), 1);
    assert_eq!(gaps[0].kind, GapKind::StepChange);
}

/// Ремонт 0.7 Р2 (В-7), второй случай: авторитет ответил прежними шагами —
/// ротации нет, а строка подавления в `gaps.csv` есть.
#[test]
fn suspicion_with_same_steps_suppresses_without_rotation_but_leaves_gap_row() {
    let dir = tempfile::tempdir().unwrap();
    let mut rec = open_recorder(&dir);
    let old_a = fake_instruments_body("SOLUSDT", "0.01", "0.001");
    let old_b = fake_instruments_body("SOLUSDT", "0.01", "0.001");
    let (wake_tx, wake_rx) = std::sync::mpsc::sync_channel::<()>(1);
    let mut steps_rx = spawn_steps_authority_with_rest(
        fake_steps_rest(vec![old_a, old_b]),
        "SOLUSDT".to_string(),
        wake_rx,
    );
    let first = steps_rx
        .recv_timeout(std::time::Duration::from_secs(5))
        .expect("первый fetch");
    assert_eq!(first, (TICK_E9, STEP_E9));
    drain_latest_steps(&mut steps_rx);

    request_steps_refresh(&wake_tx);
    let fresh = steps_rx
        .recv_timeout(std::time::Duration::from_secs(5))
        .expect("второй fetch после пробуждения");
    assert_eq!(fresh, (TICK_E9, STEP_E9));

    let mut logged = false;
    let mut suppressed = 0u64;
    let rotated = resolve_suspicion(
        &mut rec,
        Some(fresh),
        "2026-09-08T00:00:20Z",
        "price 150005000000 не на шаге 10000000",
        &mut logged,
        &mut suppressed,
    )
    .unwrap();
    assert_eq!(rotated, None);
    assert_eq!(rec.part(), 1, "прежние шаги — не ротация");
    assert_eq!(suppressed, 1);
    let gaps = read_gap_rows(&gaps_csv_path(dir.path())).unwrap();
    assert_eq!(gaps.len(), 1);
    assert_eq!(gaps[0].kind, GapKind::BookInvariant);
}
