use super::super::depth::DEPTH_FLOOR_USD_E9;
use super::super::pool::EXCLUDED_NON_CRYPTO;
use super::*;

fn e9(dollars: i64) -> i64 {
    dollars * 1_000_000_000
}

// -- format_e9 -------------------------------------------------------

#[test]
fn format_e9_round_trips_through_parse_e9() {
    use crate::bybit::ws::parse_e9;
    for &s in &["150.01", "0.001", "12345.6789", "1", "0.000000001", "0"] {
        let v = parse_e9(s).unwrap();
        assert_eq!(parse_e9(&format_e9(v)).unwrap(), v);
    }
}

// -- write_candidate_table_csv / write_instruments_csv (CSV round-trip) --

/// Зеркало `CandidateRow` для чтения назад: поля и их порядок — те же,
/// чтобы сравнение было прямым свидетельством того, что
/// `write_candidate_table_csv` пишет ровно то, что было передано.
/// `Option<f64>` читается назад как `Option<f64>` тем же `csv`/`serde`:
/// пустая ячейка — `None`, число — `Some`, без отдельных правил.
#[derive(Debug, PartialEq, serde::Deserialize)]
struct CandidateRowOwned {
    symbol: String,
    turnover_24h_usd_e9: i64,
    excluded_reason: String,
    coverage_top50_bps: Option<f64>,
    suitable_baskets: String,
    measured: bool,
    window_start_utc_ms: Option<i64>,
    window_secs: Option<i64>,
    events: Option<i64>,
    median_bid_depth_usd_e9: Option<i64>,
    median_ask_depth_usd_e9: Option<i64>,
    depth_check: String,
    order_size_e9: Option<i64>,
    order_size_notional_usd_e9: Option<i64>,
    selected_for_pilot: bool,
    final_rank: Option<u8>,
}

/// Требуемый тест: `write_candidate_table_csv` — единственное место, где
/// строится файл, названный в done-condition шага 0.4 («таблица
/// кандидатов ... закоммичена»), и до этого теста ни один тест файла не
/// доходил до настоящего `csv::Writer`. Четыре строки — по одной на
/// каждую стадию воронки Decision 25, от «член пула, отобран пилоту» до
/// «исключён по пункту 2»:
/// - `SOLUSDT` — пул (`excluded_reason` пусто), покрытие и корзины
///   посчитаны, измерен, прошёл порог, отобран, `final_rank = 1`;
/// - `NEARUSDT` — пул, измерен, глубина НИЖЕ порога, и всё равно отобран
///   (`depth_check = below_floor`, `selected_for_pilot = true`) — таск 27,
///   BUSINESS-TASK §9: глубина не критерий отбора;
/// - `MID2USDT` — пул, но остался БЕЗ измерения
///   (`measured = false`, `depth_check = not_measured`) — реальный, не
///   синтетический случай: `measure_prefiltered` обошла все десять
///   символов, но не для всех в `measured` попал результат (соединение
///   оборвалось, символ не набрал ни одного события за час и т. п.);
///   отбора он тоже не теряет;
/// - `AAPLUSDT` — исключён по пункту 2 (`excluded_reason` непусто,
///   покрытие и глубины — `None`/пусто): та ветка, что молча ломается
///   первой, если формат столбцов когда-нибудь разойдётся со структурой.
///
/// Правило на будущее для этого теста: у каждой пары полей одного типа
/// должна быть хотя бы одна строка, где их значения различаются — иначе
/// обмен местами такой пары для теста не отличим от отсутствия ошибки.
/// Здесь: `median_bid_depth_usd_e9` и `median_ask_depth_usd_e9` различны
/// внутри каждой измеренной строки, `order_size_e9` и
/// `order_size_notional_usd_e9` различны внутри каждой строки пула, а
/// `excluded_reason` пуста у пула и непуста у исключённой — иначе
/// перепутанные колонки прошли бы тест.
#[test]
fn candidate_table_csv_round_trips_measured_and_unmeasured_rows() {
    let rows = vec![
        CandidateRow {
            symbol: "SOLUSDT".to_string(),
            turnover_24h_usd_e9: e9(1_000_000),
            excluded_reason: String::new(),
            coverage_top50_bps: Some(40.0),
            suitable_baskets: "0-1;1-2.5;2.5-5;5-10;10-25".to_string(),
            measured: true,
            window_start_utc_ms: Some(1_700_000_000_000),
            window_secs: Some(3600),
            events: Some(12_345),
            median_bid_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 + e9(1)),
            median_ask_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 + e9(2)),
            depth_check: "ok".to_string(),
            order_size_e9: Some(100_000_000),
            order_size_notional_usd_e9: Some(15_000_000_000),
            selected_for_pilot: true,
            final_rank: Some(1),
        },
        CandidateRow {
            symbol: "NEARUSDT".to_string(),
            turnover_24h_usd_e9: e9(900_000),
            excluded_reason: String::new(),
            coverage_top50_bps: Some(200.2),
            suitable_baskets: "0-1;1-2.5;2.5-5;5-10;10-25".to_string(),
            measured: true,
            window_start_utc_ms: Some(1_700_000_003_600_000),
            window_secs: Some(3600),
            events: Some(999),
            median_bid_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 - e9(3)),
            median_ask_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 - e9(4)),
            depth_check: "below_floor".to_string(),
            order_size_e9: Some(500_000_000),
            order_size_notional_usd_e9: Some(7_500_000_000),
            selected_for_pilot: true,
            final_rank: Some(2),
        },
        CandidateRow {
            symbol: "MID2USDT".to_string(),
            turnover_24h_usd_e9: e9(500_000),
            excluded_reason: String::new(),
            coverage_top50_bps: Some(50.0),
            suitable_baskets: "0-1;1-2.5;2.5-5;5-10;10-25".to_string(),
            measured: false,
            window_start_utc_ms: None,
            window_secs: None,
            events: None,
            median_bid_depth_usd_e9: None,
            median_ask_depth_usd_e9: None,
            depth_check: "not_measured".to_string(),
            // Размер из метаданных и цены, измерения не требует — поэтому
            // заполнен и у неизмеренного члена пула, в отличие от глубин.
            order_size_e9: Some(1_000_000_000),
            order_size_notional_usd_e9: Some(5_000_000_000),
            selected_for_pilot: true,
            final_rank: Some(3),
        },
        CandidateRow {
            symbol: "AAPLUSDT".to_string(),
            turnover_24h_usd_e9: e9(800_000),
            excluded_reason: EXCLUDED_NON_CRYPTO.to_string(),
            coverage_top50_bps: None,
            suitable_baskets: String::new(),
            measured: false,
            window_start_utc_ms: None,
            window_secs: None,
            events: None,
            median_bid_depth_usd_e9: None,
            median_ask_depth_usd_e9: None,
            depth_check: String::new(),
            order_size_e9: None,
            order_size_notional_usd_e9: None,
            selected_for_pilot: false,
            final_rank: None,
        },
    ];

    let tmp = tempfile::NamedTempFile::new().unwrap();
    write_candidate_table_csv(tmp.path(), &rows, None).unwrap();

    let mut reader = csv::Reader::from_path(tmp.path()).unwrap();
    let read_back: Vec<CandidateRowOwned> = reader.deserialize().collect::<Result<_, _>>().unwrap();

    assert_eq!(
        read_back,
        vec![
            CandidateRowOwned {
                symbol: "SOLUSDT".to_string(),
                turnover_24h_usd_e9: e9(1_000_000),
                excluded_reason: String::new(),
                coverage_top50_bps: Some(40.0),
                suitable_baskets: "0-1;1-2.5;2.5-5;5-10;10-25".to_string(),
                measured: true,
                window_start_utc_ms: Some(1_700_000_000_000),
                window_secs: Some(3600),
                events: Some(12_345),
                median_bid_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 + e9(1)),
                median_ask_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 + e9(2)),
                depth_check: "ok".to_string(),
                order_size_e9: Some(100_000_000),
                order_size_notional_usd_e9: Some(15_000_000_000),
                selected_for_pilot: true,
                final_rank: Some(1),
            },
            CandidateRowOwned {
                symbol: "NEARUSDT".to_string(),
                turnover_24h_usd_e9: e9(900_000),
                excluded_reason: String::new(),
                coverage_top50_bps: Some(200.2),
                suitable_baskets: "0-1;1-2.5;2.5-5;5-10;10-25".to_string(),
                measured: true,
                window_start_utc_ms: Some(1_700_000_003_600_000),
                window_secs: Some(3600),
                events: Some(999),
                median_bid_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 - e9(3)),
                median_ask_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 - e9(4)),
                depth_check: "below_floor".to_string(),
                order_size_e9: Some(500_000_000),
                order_size_notional_usd_e9: Some(7_500_000_000),
                selected_for_pilot: true,
                final_rank: Some(2),
            },
            CandidateRowOwned {
                symbol: "MID2USDT".to_string(),
                turnover_24h_usd_e9: e9(500_000),
                excluded_reason: String::new(),
                coverage_top50_bps: Some(50.0),
                suitable_baskets: "0-1;1-2.5;2.5-5;5-10;10-25".to_string(),
                measured: false,
                window_start_utc_ms: None,
                window_secs: None,
                events: None,
                median_bid_depth_usd_e9: None,
                median_ask_depth_usd_e9: None,
                depth_check: "not_measured".to_string(),
                order_size_e9: Some(1_000_000_000),
                order_size_notional_usd_e9: Some(5_000_000_000),
                selected_for_pilot: true,
                final_rank: Some(3),
            },
            CandidateRowOwned {
                symbol: "AAPLUSDT".to_string(),
                turnover_24h_usd_e9: e9(800_000),
                excluded_reason: EXCLUDED_NON_CRYPTO.to_string(),
                coverage_top50_bps: None,
                suitable_baskets: String::new(),
                measured: false,
                window_start_utc_ms: None,
                window_secs: None,
                events: None,
                median_bid_depth_usd_e9: None,
                median_ask_depth_usd_e9: None,
                depth_check: String::new(),
                order_size_e9: None,
                order_size_notional_usd_e9: None,
                selected_for_pilot: false,
                final_rank: None,
            },
        ],
        "неизмеренный кандидат обязан вернуться как None на всех Option-полях \
         замера, а не как 0, false или пустая строка, принятая за None; \
         `excluded_reason` обязана читаться назад раздельно (пусто у пула, \
         код у исключённой); размер-22а при этом заполнен и у неизмеренного \
         члена пула (считается без замера) и пуст только у исключённой; \
         `depth_check` — три метки у пула и пусто у исключённой, и ни одна \
         из них не гасит `selected_for_pilot` (таск 27, §9)"
    );
}

/// Критерий приёмки таска 08: окно короче боевого несёт предупреждение
/// `debug` первой строкой CSV (та же метка, что и в stderr) — и обычный
/// `csv::Reader` без настройки `.comment` эту строку читает как заголовок,
/// потому и нужен `ReaderBuilder::comment` на стороне читателя (сделано в
/// `h3_lots_for_symbol`/`load_steps_for_symbol`), что этот тест и
/// проверяет: данные читаются обратно **сквозь** метку, а не вместо неё.
#[test]
fn candidate_table_csv_carries_the_debug_label_as_a_leading_comment() {
    let rows = vec![CandidateRow {
        symbol: "SOLUSDT".to_string(),
        turnover_24h_usd_e9: e9(1),
        excluded_reason: String::new(),
        coverage_top50_bps: Some(50.0),
        suitable_baskets: "0-1".to_string(),
        measured: true,
        window_start_utc_ms: Some(1),
        window_secs: Some(300),
        events: Some(1),
        median_bid_depth_usd_e9: Some(1),
        median_ask_depth_usd_e9: Some(1),
        depth_check: "ok".to_string(),
        order_size_e9: Some(1),
        order_size_notional_usd_e9: Some(1),
        selected_for_pilot: true,
        final_rank: Some(1),
    }];
    let tmp = tempfile::NamedTempFile::new().unwrap();
    write_candidate_table_csv(
        tmp.path(),
        &rows,
        Some("debug: окно 300 с — результат не годится для отбора, только для отладки"),
    )
    .unwrap();

    let content = std::fs::read_to_string(tmp.path()).unwrap();
    let first_line = content.lines().next().unwrap();
    assert!(
        first_line.starts_with('#') && first_line.contains("debug"),
        "первая строка обязана нести метку debug, получено: {first_line:?}"
    );

    let mut reader = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_path(tmp.path())
        .unwrap();
    let read_back: Vec<CandidateRowOwned> = reader.deserialize().collect::<Result<_, _>>().unwrap();
    assert_eq!(read_back.len(), 1);
    assert_eq!(read_back[0].symbol, "SOLUSDT");
}

/// Требуемый тест: `write_instruments_csv` — то, что done-condition шага
/// 0.4 называет «`instruments.csv` непуст». Круглый путь через
/// `format_e9` (не `parse_e9`, отдельная копия — см. её doc) проверяется
/// здесь на реальном файле, а не только опосредованно через
/// `format_e9_round_trips_through_parse_e9`.
///
/// Изменение 5: `tick_size`, `min_order_qty`, `qty_step` и
/// `min_notional_value` — четыре РАЗНЫХ числа, попарно не равных. Раньше
/// `min_order_qty_e9` и `qty_step_e9` были одним и тем же значением
/// (0.1 == 0.1): перепутанные местами колонки `min_order_qty`/`qty_step`
/// в `write_instruments_csv` (или в `InstrumentRow`) прошли бы тест
/// незамеченными, потому что оба столбца читались бы как «0.1» в любом
/// порядке.
#[test]
fn instruments_csv_round_trips_and_is_nonempty() {
    let instruments = vec![Instrument {
        symbol: "SOLUSDT".to_string(),
        base_coin: "SOL".to_string(),
        quote_coin: "USDT".to_string(),
        contract_type: "LinearPerpetual".to_string(),
        status: "Trading".to_string(),
        launch_time_ms: Some(1_600_000_000_000),
        tick_e9: 10_000_000,
        min_order_qty_e9: 500_000_000,
        qty_step_e9: 250_000_000,
        min_notional_value_e9: 5_000_000_000,
    }];

    let tmp = tempfile::NamedTempFile::new().unwrap();
    write_instruments_csv(tmp.path(), &instruments).unwrap();

    let content = std::fs::read_to_string(tmp.path()).unwrap();
    assert!(
        !content.trim().is_empty(),
        "instruments.csv обязан быть непуст (done-condition шага 0.4)"
    );

    let mut reader = csv::Reader::from_path(tmp.path()).unwrap();
    let read_back: Vec<InstrumentRow> = reader.deserialize().collect::<Result<_, _>>().unwrap();
    assert_eq!(
        read_back,
        vec![InstrumentRow {
            symbol: "SOLUSDT".to_string(),
            tick_size: "0.01".to_string(),
            min_order_qty: "0.5".to_string(),
            qty_step: "0.25".to_string(),
            min_notional_value: "5".to_string(),
        }]
    );
}

// -- write_instruments_csv_with_h3 (план D-H3, таск 08) ------------------

fn instrument(symbol: &str) -> Instrument {
    Instrument {
        symbol: symbol.to_string(),
        base_coin: symbol.trim_end_matches("USDT").to_string(),
        quote_coin: "USDT".to_string(),
        contract_type: "LinearPerpetual".to_string(),
        status: "Trading".to_string(),
        launch_time_ms: Some(1_600_000_000_000),
        tick_e9: 10_000_000,
        min_order_qty_e9: 100_000_000,
        qty_step_e9: 100_000_000,
        min_notional_value_e9: 5_000_000_000,
    }
}

/// Критерий приёмки таска 08: `h3_lots`/`k`/`median_trade_lots` — колонки
/// `instruments.csv`, заполненные у измеренного символа и пустые (`None`,
/// не 0) у неизмеренного — таск 02 (`h3_lots_for_symbol`) ищет строку по
/// имени символа, а не по позиции, поэтому наличие пустой строки другого
/// символа не должно быть отличимо от отсутствия измерения для него.
#[test]
fn instruments_csv_with_h3_fills_measured_symbols_and_leaves_others_empty() {
    let instruments = vec![instrument("SOLUSDT"), instrument("UNMEASUREDUSDT")];
    let mut h3_by_symbol = HashMap::new();
    h3_by_symbol.insert(
        "SOLUSDT".to_string(),
        H3FloorInfo {
            k: 1.5,
            median_trade_lots: 7,
            h3_lots: 10,
            window_start_utc_ms: 1_700_000_000_000,
            window_secs: 3600,
        },
    );

    let tmp = tempfile::NamedTempFile::new().unwrap();
    let mut depth_by_symbol = HashMap::new();
    depth_by_symbol.insert("SOLUSDT".to_string(), DepthCheck::BelowFloor);
    write_instruments_csv_with_h3(
        tmp.path(),
        &instruments,
        &h3_by_symbol,
        &depth_by_symbol,
        None,
    )
    .unwrap();

    let mut reader = csv::Reader::from_path(tmp.path()).unwrap();
    let read_back: Vec<InstrumentRowWithH3> =
        reader.deserialize().collect::<Result<_, _>>().unwrap();
    let sol = read_back.iter().find(|r| r.symbol == "SOLUSDT").unwrap();
    assert_eq!(sol.h3_lots, Some(10));
    assert_eq!(sol.k, Some(1.5));
    assert_eq!(sol.median_trade_lots, Some(7));
    assert_eq!(sol.window_secs, Some(3600));

    let other = read_back
        .iter()
        .find(|r| r.symbol == "UNMEASUREDUSDT")
        .unwrap();
    assert_eq!(other.h3_lots, None);
    assert_eq!(other.k, None);
    assert_eq!(other.median_trade_lots, None);
}

/// Отладочное окно несёт ту же метку `#`-комментарием первой строкой, что
/// и `write_candidate_table_csv` — читатель со включённым `.comment`
/// (`h3_lots_for_symbol`, `load_steps_for_symbol`) обязан пройти сквозь
/// неё к настоящему заголовку.
#[test]
fn instruments_csv_with_h3_carries_the_debug_label_as_a_leading_comment() {
    let instruments = vec![instrument("SOLUSDT")];
    let tmp = tempfile::NamedTempFile::new().unwrap();
    write_instruments_csv_with_h3(
        tmp.path(),
        &instruments,
        &HashMap::new(),
        &HashMap::new(),
        Some("debug: окно 300 с — результат не годится для отбора, только для отладки"),
    )
    .unwrap();

    let content = std::fs::read_to_string(tmp.path()).unwrap();
    assert!(content.lines().next().unwrap().starts_with("# debug"));

    let mut reader = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_path(tmp.path())
        .unwrap();
    let read_back: Vec<InstrumentRowWithH3> =
        reader.deserialize().collect::<Result<_, _>>().unwrap();
    assert_eq!(read_back.len(), 1);
    assert_eq!(read_back[0].symbol, "SOLUSDT");
}

// -- instruments_csv_reader (дозапрос по ревью таска 08, ось Craft) ------

/// Требуемый тест: единственный читатель `instruments.csv` обязан пройти
/// сквозь баннер `debug` к настоящему заголовку — это ровно то, на чём
/// падали `session.rs::load_pool` и `power.rs::pool_size` до перевода
/// на эту функцию (голый `csv::Reader::from_path` читал баннер как
/// заголовок).
#[test]
fn instruments_csv_reader_skips_the_leading_debug_banner() {
    let tmp = tempfile::NamedTempFile::new().unwrap();
    std::fs::write(
        tmp.path(),
        "# debug: окно 300 с — результат не годится для отбора, только для отладки\n\
         symbol,tick_size,min_order_qty,qty_step,min_notional_value\n\
         SOLUSDT,0.01,0.1,0.1,5\n",
    )
    .unwrap();

    let mut r = instruments_csv_reader(tmp.path()).unwrap();
    let headers = r.headers().unwrap().clone();
    assert_eq!(headers.get(0), Some("symbol"), "заголовок, не баннер");
    let rows: Vec<InstrumentRow> = r.deserialize().collect::<Result<_, _>>().unwrap();
    assert_eq!(
        rows,
        vec![InstrumentRow {
            symbol: "SOLUSDT".to_string(),
            tick_size: "0.01".to_string(),
            min_order_qty: "0.1".to_string(),
            qty_step: "0.1".to_string(),
            min_notional_value: "5".to_string(),
        }]
    );
}

/// Без баннера читатель ведёт себя как обычный `csv::Reader` — терпимость
/// к `#` не требует, чтобы файл его нёс.
#[test]
fn instruments_csv_reader_works_without_a_banner_too() {
    let tmp = tempfile::NamedTempFile::new().unwrap();
    std::fs::write(
        tmp.path(),
        "symbol,tick_size,min_order_qty,qty_step,min_notional_value\n\
         SOLUSDT,0.01,0.1,0.1,5\n",
    )
    .unwrap();

    let mut r = instruments_csv_reader(tmp.path()).unwrap();
    let rows: Vec<InstrumentRow> = r.deserialize().collect::<Result<_, _>>().unwrap();
    assert_eq!(rows.len(), 1);
}

// -- instruments_for_pool (дозапрос по ревью таска 08, ось Манифест) -----

fn pool_candidate(symbol: &str, turnover_24h_usd_e9: i64) -> PoolCandidate {
    PoolCandidate {
        symbol: symbol.to_string(),
        turnover_24h_usd_e9,
        tick_e9: 10_000_000,
        last_price_e9: 100_000_000_000,
        min_order_qty_e9: 100_000_000,
        qty_step_e9: 100_000_000,
        min_notional_value_e9: 5_000_000_000,
        coverage_top50_bps: Some(50.0),
    }
}

/// R33 «пул фиксируется»: в `instruments.csv` едут символы **пула** и в
/// его порядке (ранг по обороту) — не вся вселенная REST и не порядок
/// REST-ответа. Таск 27: тонкая книга больше не выбрасывает символ
/// отсюда — `ZECUSDT` в пуле, значит и в файле, на который подпишется
/// `lob session` (BUSINESS-TASK §2/§3).
#[test]
fn instruments_for_pool_keeps_the_pool_in_rank_order() {
    let instruments = vec![
        instrument("ZECUSDT"),
        instrument("SOLUSDT"),
        instrument("NEARUSDT"),
    ];
    // Пул называет SOLUSDT первым, хотя во входе он второй; NEARUSDT в
    // пул не вошёл вовсе.
    let pool = vec![pool_candidate("SOLUSDT", 10), pool_candidate("ZECUSDT", 9)];

    let selected = instruments_for_pool(&instruments, &pool);
    assert_eq!(
        selected
            .iter()
            .map(|i| i.symbol.as_str())
            .collect::<Vec<_>>(),
        vec!["SOLUSDT", "ZECUSDT"],
        "порядок — ранг пула; NEARUSDT вне пула, ZECUSDT в нём"
    );
}

/// Символ пула, для которого сеть не вернула метаданные инструмента,
/// пропускается молча, а не паникой — тот же приём, что
/// `join_candidate_meta` уже применяет к символу без тикера.
#[test]
fn instruments_for_pool_silently_skips_a_pool_symbol_missing_from_instruments() {
    let instruments = vec![instrument("SOLUSDT")];
    let pool = vec![
        pool_candidate("SOLUSDT", 10),
        pool_candidate("GHOSTUSDT", 9),
    ];
    let selected = instruments_for_pool(&instruments, &pool);
    assert_eq!(selected.len(), 1);
    assert_eq!(selected[0].symbol, "SOLUSDT");
}

/// Критерий приёмки таска 27: `instruments.csv` — тот файл, по которому
/// `lob session` подписывается на пул, — несёт колонку `depth_check`, и
/// инструмент с `below_floor` стоит в нём наравне с прошедшим порог.
/// Символ пула без замера получает `not_measured`, а не пустую ячейку:
/// читателю нужна метка, а не догадка.
#[test]
fn instruments_csv_carries_depth_check_and_keeps_a_below_floor_symbol() {
    let instruments = vec![
        instrument("SOLUSDT"),
        instrument("ZECUSDT"),
        instrument("DARKUSDT"),
    ];
    let mut depth_by_symbol = HashMap::new();
    depth_by_symbol.insert("SOLUSDT".to_string(), DepthCheck::Ok);
    depth_by_symbol.insert("ZECUSDT".to_string(), DepthCheck::BelowFloor);
    // DARKUSDT в карте нет вовсе — час не намерил по нему ничего.

    let tmp = tempfile::NamedTempFile::new().unwrap();
    write_instruments_csv_with_h3(
        tmp.path(),
        &instruments,
        &HashMap::new(),
        &depth_by_symbol,
        None,
    )
    .unwrap();

    let mut reader = csv::Reader::from_path(tmp.path()).unwrap();
    let read_back: Vec<InstrumentRowWithH3> =
        reader.deserialize().collect::<Result<_, _>>().unwrap();
    assert_eq!(
        read_back
            .iter()
            .map(|r| (r.symbol.as_str(), r.depth_check.as_str()))
            .collect::<Vec<_>>(),
        vec![
            ("SOLUSDT", "ok"),
            ("ZECUSDT", "below_floor"),
            ("DARKUSDT", "not_measured"),
        ],
        "все три символа пула обязаны быть в файле, метка — колонкой"
    );
}
