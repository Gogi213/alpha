use super::*;

const NOW_MS: i64 = 2_000_000_000_000; // произвольная точка «сейчас» для всех тестов пула

fn e9(dollars: i64) -> i64 {
    dollars * 1_000_000_000
}

fn meta(symbol: &str, base: &str, turnover: i64) -> CandidateMeta {
    meta_px(symbol, base, turnover, 10_000_000, e9(100))
}

fn meta_px(
    symbol: &str,
    base: &str,
    turnover: i64,
    tick_e9: i64,
    last_price_e9: i64,
) -> CandidateMeta {
    CandidateMeta {
        symbol: symbol.to_string(),
        base_coin: base.to_string(),
        quote_coin: "USDT".to_string(),
        contract_type: "LinearPerpetual".to_string(),
        launch_time_ms: Some(0), // «родился на эпохе» — всегда старше 30 суток в тестах
        turnover_24h_usd_e9: turnover,
        tick_e9,
        last_price_e9,
        // SOL-подобный лот: 0.1 при шаге 0.1, чек $5 — минимальный лот
        // ($10 при цене 100) чек покрывает, размер-22а равен ему же.
        min_order_qty_e9: 100_000_000,
        qty_step_e9: 100_000_000,
        min_notional_value_e9: e9(5),
    }
}

fn measured(
    symbol: &str,
    depth: i64,
    turnover: i64,
    events: i64,
    window_secs: i64,
) -> MeasuredCandidate {
    MeasuredCandidate {
        symbol: symbol.to_string(),
        window_start_utc_ms: 0,
        window_secs,
        events,
        median_bid_depth_usd_e9: depth,
        median_ask_depth_usd_e9: depth,
        reported_turnover_usd_e9: turnover,
        median_trade_lots: None,
    }
}

/// Собирает весь путь Decision 25 от сырых метаданных до финальных двух
/// на небольшой, но не тривиальной синтетической вселенной — то самое
/// «структурировано так, что правило — чистая функция», проверенное
/// сквозным прогоном, а не только по стадиям порознь. Вселенная повторяет
/// иллюстрацию BUSINESS-TASK §2: из сырого топа выбывают BTC/ETH по имени,
/// акция/золото/ETF по пункту 2 и молодой контракт по пункту 3.
#[test]
fn full_pipeline_from_synthetic_universe_matches_hand_computed_result() {
    let mut candidates = vec![
        meta("BTCUSDT", "BTC", e9(20_000_000)),
        meta("ETHUSDT", "ETH", e9(19_000_000)),
        meta("AAPLUSDT", "AAPL", e9(18_000_000)),
        meta("XAUUSDT", "XAU", e9(17_000_000)),
        meta("SOXLUSDT", "SOXL", e9(16_000_000)),
        meta("SNDKUSDT", "SNDK", e9(15_000_000)),
    ];
    let mut young = meta("PONSUSDT", "PONS", e9(14_000_000));
    young.launch_time_ms = Some(NOW_MS - 9 * 24 * 60 * 60 * 1_000);
    candidates.push(young);
    for i in 0..10 {
        candidates.push(meta(
            &format!("CRYPTO{i}USDT"),
            &format!("CRYPTO{i}"),
            e9(1_000_000 - i * 10_000),
        ));
    }

    let outcome = build_pool(&candidates, NOW_MS);
    assert_eq!(
        outcome.pool.len(),
        POOL_SIZE,
        "семь исключённых добраны снизу — пул всё равно десятка"
    );
    assert!(
        outcome.pool.iter().all(|c| c.symbol.starts_with("CRYPTO")),
        "в пуле только прошедшая крипта"
    );
    // Покрытие посчитано уже на стадии пула: дефолтный meta() даёт 50 bps.
    assert!(
        outcome
            .pool
            .iter()
            .all(|c| c.coverage_top50_bps == Some(50.0)),
        "тик 0.01 при цене 100 — это 50 bps у каждого"
    );
    assert_eq!(count_eligible_trials(&outcome.pool), 10 * 5);

    // Синтетический замер: CRYPTO0 — глубокий и активный, CRYPTO1 —
    // глубокий, но реже торгуется, остальные — ниже порога.
    let measured: Vec<MeasuredCandidate> = outcome
        .pool
        .iter()
        .map(|c| {
            let depth = if c.symbol == "CRYPTO0USDT" || c.symbol == "CRYPTO1USDT" {
                DEPTH_FLOOR_USD_E9 + e9(1)
            } else {
                DEPTH_FLOOR_USD_E9 - e9(1)
            };
            let events = if c.symbol == "CRYPTO0USDT" { 500 } else { 100 };
            measured(&c.symbol, depth, c.turnover_24h_usd_e9, events, 3600)
        })
        .collect();

    let table = build_candidate_table(&outcome, &measured);
    assert_eq!(
        table.len(),
        outcome.pool.len() + outcome.excluded.len(),
        "таблица несёт пул и все исключения, не только выживших"
    );
    // Таск 27 (§2 «первые десять оставшихся», §9 «час живой глубины —
    // проверка, а не критерий отбора»): отобраны ровно десять членов
    // пула, хотя порог глубины прошли только двое.
    assert_eq!(
        table
            .iter()
            .filter(|r| r.selected_for_pilot)
            .map(|r| r.symbol.clone())
            .collect::<Vec<_>>(),
        outcome
            .pool
            .iter()
            .map(|c| c.symbol.clone())
            .collect::<Vec<_>>(),
        "отобран весь пул, в порядке ранга по обороту"
    );
    assert_eq!(
        table.iter().filter(|r| r.selected_for_pilot).count(),
        POOL_SIZE
    );
    // Пул — первые строки.
    assert!(table[0].excluded_reason.is_empty());
    assert_eq!(table[0].symbol, outcome.pool[0].symbol);
    // Исключения — со своими причинами.
    let reasons: Vec<(&str, &str)> = table
        .iter()
        .filter(|r| !r.excluded_reason.is_empty())
        .map(|r| (r.symbol.as_str(), r.excluded_reason.as_str()))
        .collect();
    assert!(reasons.contains(&("BTCUSDT", EXCLUDED_BTC_ETH)));
    assert!(reasons.contains(&("ETHUSDT", EXCLUDED_BTC_ETH)));
    assert!(reasons.contains(&("AAPLUSDT", EXCLUDED_NON_CRYPTO)));
    assert!(reasons.contains(&("XAUUSDT", EXCLUDED_NON_CRYPTO)));
    assert!(reasons.contains(&("SOXLUSDT", EXCLUDED_NON_CRYPTO)));
    assert!(reasons.contains(&("SNDKUSDT", EXCLUDED_NON_CRYPTO)));
    assert!(reasons.contains(&("PONSUSDT", EXCLUDED_TOO_YOUNG)));
    // Строка пула несёт покрытие, корзины и ранг финалиста.
    let c0_row = table.iter().find(|r| r.symbol == "CRYPTO0USDT").unwrap();
    assert!(c0_row.excluded_reason.is_empty());
    assert_eq!(c0_row.coverage_top50_bps, Some(50.0));
    assert_eq!(c0_row.suitable_baskets, "0-1;1-2.5;2.5-5;5-10;10-25");
    assert!(c0_row.measured);
    assert_eq!(c0_row.depth_check, "ok");
    assert!(c0_row.selected_for_pilot);
    assert_eq!(c0_row.final_rank, Some(1));
    // Тонкая книга — метка, а не выбытие: CRYPTO2 ниже порога, но в пуле.
    let c2_row = table.iter().find(|r| r.symbol == "CRYPTO2USDT").unwrap();
    assert_eq!(c2_row.depth_check, "below_floor");
    assert!(c2_row.selected_for_pilot);
    assert_eq!(c2_row.final_rank, Some(3));
    assert!(c2_row.excluded_reason.is_empty());
    // Размер-22а на дефолтном meta(): лот 0.1 при цене 100 — $10, чек $5
    // покрыт одним шагом, размер равен лоту, номинал $10.
    assert_eq!(c0_row.order_size_e9, Some(100_000_000));
    assert_eq!(c0_row.order_size_notional_usd_e9, Some(10_000_000_000));
    // Строка исключённой: причина есть, покрытия, замера и размера нет.
    let btc_row = table.iter().find(|r| r.symbol == "BTCUSDT").unwrap();
    assert_eq!(btc_row.excluded_reason, EXCLUDED_BTC_ETH);
    assert_eq!(btc_row.coverage_top50_bps, None);
    assert!(btc_row.suitable_baskets.is_empty());
    assert!(!btc_row.measured);
    assert!(!btc_row.selected_for_pilot);
    assert_eq!(btc_row.order_size_e9, None);
    assert_eq!(btc_row.order_size_notional_usd_e9, None);
    assert!(
        btc_row.depth_check.is_empty(),
        "исключённого никто не мерил — метки глубины у него нет вовсе"
    );
}

/// Критерий приёмки таска 27, дословно: «кандидат с глубиной ниже порога
/// в первой десятке по рангу — в пуле с `depth_check=below_floor`».
/// Фикстура повторяет `ZECUSDT` из BUSINESS-TASK §3 — инструмент с очень
/// узкой книгой (вся видимая глубина дешевле круговых издержек), который
/// задача защищает поимённо: «Инструмент не исключается … и это
/// печатается строкой отчёта». До таска 27 порог `DEPTH_FLOOR_USD_E9`
/// выбрасывал его из пула, оставляя восемь вместо десяти (аудит
/// 2026-09-12, `SETTLED.md` В-35).
///
/// Проверяется весь путь, а не только `build_pool`: пул → таблица →
/// подмножество `instruments.csv`. Тонкий инструмент обязан выжить во
/// всех трёх, потому что до таска 27 он выпадал именно на третьем.
#[test]
fn a_thin_book_inside_the_top_ten_stays_in_the_pool_with_below_floor() {
    // Второй по обороту — ZEC-подобный: книга тоньше порога на обеих
    // сторонах. Первый и остальные восемь — толще.
    let mut candidates = vec![meta("SOLUSDT", "SOL", e9(10_000_000))];
    candidates.push(meta("ZECUSDT", "ZEC", e9(9_000_000)));
    for i in 0..8 {
        candidates.push(meta(
            &format!("CRYPTO{i}USDT"),
            &format!("CRYPTO{i}"),
            e9(1_000_000 - i * 10_000),
        ));
    }
    // Одиннадцатый — прошёл все правила, но не влез по рангу.
    candidates.push(meta("SPILLUSDT", "SPILL", e9(1)));

    let outcome = build_pool(&candidates, NOW_MS);
    assert_eq!(outcome.pool.len(), POOL_SIZE);

    let measured: Vec<MeasuredCandidate> = outcome
        .pool
        .iter()
        .map(|c| {
            let depth = if c.symbol == "ZECUSDT" {
                DEPTH_FLOOR_USD_E9 - e9(1)
            } else {
                DEPTH_FLOOR_USD_E9 + e9(1)
            };
            measured(&c.symbol, depth, c.turnover_24h_usd_e9, 100, 3600)
        })
        .collect();

    let table = build_candidate_table(&outcome, &measured);
    let zec = table.iter().find(|r| r.symbol == "ZECUSDT").unwrap();
    assert!(
        zec.excluded_reason.is_empty(),
        "тонкая книга — не исключение по правилу"
    );
    assert_eq!(zec.depth_check, "below_floor");
    assert!(zec.selected_for_pilot, "§3: инструмент не исключается");
    assert_eq!(zec.final_rank, Some(2), "ранг — по обороту, не по глубине");
    assert_eq!(table.iter().filter(|r| r.selected_for_pilot).count(), 10);

    // Не влезший по рангу — своим кодом, не одним из трёх правил.
    let spill = table.iter().find(|r| r.symbol == "SPILLUSDT").unwrap();
    assert_eq!(spill.excluded_reason, RANK_BEYOND_POOL);
    assert!(!spill.selected_for_pilot);

    // `instruments.csv` (то, на что подпишется `lob session`) несёт
    // все десять, включая тонкого.
    let instruments: Vec<Instrument> = outcome
        .pool
        .iter()
        .map(|c| Instrument {
            symbol: c.symbol.clone(),
            base_coin: c.symbol.trim_end_matches("USDT").to_string(),
            quote_coin: "USDT".to_string(),
            contract_type: "LinearPerpetual".to_string(),
            status: "Trading".to_string(),
            launch_time_ms: Some(0),
            tick_e9: c.tick_e9,
            min_order_qty_e9: c.min_order_qty_e9,
            qty_step_e9: c.qty_step_e9,
            min_notional_value_e9: c.min_notional_value_e9,
        })
        .collect();
    let pool_instruments = instruments_for_pool(&instruments, &outcome.pool);
    assert_eq!(pool_instruments.len(), POOL_SIZE);
    assert!(pool_instruments.iter().any(|i| i.symbol == "ZECUSDT"));
}
