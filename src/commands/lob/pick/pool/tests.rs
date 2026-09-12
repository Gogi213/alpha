use super::*;

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

fn ticker(symbol: &str, turnover: i64, last_price: i64) -> Ticker {
    Ticker {
        symbol: symbol.to_string(),
        turnover_24h_usd_e9: turnover,
        last_price_e9: last_price,
    }
}

const NOW_MS: i64 = 2_000_000_000_000; // произвольная точка «сейчас» для всех тестов пула

fn e9(dollars: i64) -> i64 {
    dollars * 1_000_000_000
}

// -- join_candidate_meta --------------------------------------------

fn no_data_instrument() -> Instrument {
    Instrument {
        symbol: "NODATAUSDT".to_string(),
        base_coin: "NODATA".to_string(),
        quote_coin: "USDT".to_string(),
        contract_type: "LinearPerpetual".to_string(),
        status: "Trading".to_string(),
        launch_time_ms: Some(0),
        tick_e9: 1,
        min_order_qty_e9: 1,
        qty_step_e9: 1,
        min_notional_value_e9: 0,
    }
}

#[test]
fn join_drops_symbols_without_a_ticker() {
    let instruments = vec![no_data_instrument()];
    let out = join_candidate_meta(&instruments, &[]);
    assert!(
        out.is_empty(),
        "без оборота ранжировать нечем — символ выпадает"
    );
}

/// Требуемый тест: символ выпадает именно из-за отсутствия тикера, а не
/// потому что `join_candidate_meta` теряет всё подряд. Без символа, у
/// которого тикер ЕСТЬ, тест выше не отличил бы «фильтрует по тикеру» от
/// «возвращает пустой список независимо от входа».
#[test]
fn join_keeps_the_symbol_that_has_a_ticker_and_drops_the_one_that_does_not() {
    let mut has_ticker = no_data_instrument();
    has_ticker.symbol = "HASDATAUSDT".to_string();
    has_ticker.base_coin = "HASDATA".to_string();
    has_ticker.tick_e9 = 10_000_000;
    let instruments = vec![has_ticker, no_data_instrument()];
    let tickers = vec![ticker("HASDATAUSDT", e9(1_000), e9(100))];

    let out = join_candidate_meta(&instruments, &tickers);

    assert_eq!(
        out.iter().map(|c| c.symbol.as_str()).collect::<Vec<_>>(),
        vec!["HASDATAUSDT"],
        "с тикером — остаётся, без тикера — выпадает, оба в одном вызове"
    );
}

#[test]
fn join_carries_turnover_tick_and_price_for_coverage() {
    let mut inst = no_data_instrument();
    inst.symbol = "SOLUSDT".to_string();
    inst.base_coin = "SOL".to_string();
    inst.tick_e9 = 10_000_000;
    let tickers = vec![ticker("SOLUSDT", e9(1_000), e9(150))];

    let out = join_candidate_meta(&[inst], &tickers);

    assert_eq!(out.len(), 1);
    assert_eq!(out[0].turnover_24h_usd_e9, e9(1_000));
    assert_eq!(out[0].tick_e9, 10_000_000);
    assert_eq!(out[0].last_price_e9, e9(150));
}

// -- is_listed_long_enough -------------------------------------------

#[test]
fn exactly_thirty_days_old_is_long_enough() {
    let launch = NOW_MS - MIN_LISTED_DAYS * MS_PER_DAY;
    assert!(is_listed_long_enough(Some(launch), NOW_MS));
}

#[test]
fn one_millisecond_short_of_thirty_days_is_not_long_enough() {
    let launch = NOW_MS - MIN_LISTED_DAYS * MS_PER_DAY + 1;
    assert!(!is_listed_long_enough(Some(launch), NOW_MS));
}

#[test]
fn missing_launch_time_is_treated_as_long_listed() {
    assert!(is_listed_long_enough(None, NOW_MS));
}

// -- build_pool: область и три исключения Decision 25 ------------------

#[test]
fn pool_excludes_non_usdt_quote_silently() {
    let mut c = meta("BTCUSD", "BTC", e9(1_000_000));
    c.quote_coin = "USD".to_string();
    // Область запроса, не исключение Decision 25: строки в таблице нет.
    let outcome = build_pool(&[c], NOW_MS);
    assert!(outcome.pool.is_empty());
    assert!(outcome.excluded.is_empty());
}

#[test]
fn pool_excludes_non_linear_perpetual_contract_type_silently() {
    let mut c = meta("SOLUSDT", "SOL", e9(1_000_000));
    c.contract_type = "LinearFutures".to_string();
    let outcome = build_pool(&[c], NOW_MS);
    assert!(outcome.pool.is_empty());
    assert!(outcome.excluded.is_empty());
}

/// Пункт 1 Decision 25: BTC и ETH — по имени. Единственное именное
/// исключение, прямое указание владельца («там всё выедено»).
#[test]
fn pool_excludes_btc_and_eth_by_name_with_reason() {
    let candidates = vec![
        meta("BTCUSDT", "BTC", e9(10_000_000)),
        meta("ETHUSDT", "ETH", e9(9_000_000)),
        meta("SOLUSDT", "SOL", e9(1_000)),
    ];
    let outcome = build_pool(&candidates, NOW_MS);
    assert_eq!(
        outcome
            .pool
            .iter()
            .map(|c| c.symbol.as_str())
            .collect::<Vec<_>>(),
        vec!["SOLUSDT"]
    );
    let mut reasons: Vec<(&str, &str)> = outcome
        .excluded
        .iter()
        .map(|e| (e.symbol.as_str(), e.excluded_reason))
        .collect();
    reasons.sort();
    assert_eq!(
        reasons,
        vec![("BTCUSDT", EXCLUDED_BTC_ETH), ("ETHUSDT", EXCLUDED_BTC_ETH),]
    );
}

/// Пункт 2 Decision 25: некриптовый базовый актив. Ревизия 17 доказала,
/// что признака в API нет (`AAPLUSDT` — `LinearPerpetual`/`Trading`, как
/// `SOLUSDT`; `SETTLED.md` ПЛАН-2 неверен), поэтому решение — списком
/// `NON_CRYPTO_BASES` здесь, и каждая строка несёт причину.
///
/// Исчерпывающая деструктуризация `CandidateMeta` без `..`: компилятор
/// требует назвать здесь буквально каждое поле структуры. Если у неё
/// когда-нибудь появится новое поле, эта строка перестанет собираться,
/// пока кто-то не впишет его сюда осознанно, — красный результат
/// гарантирован значением типа, а не удачным выбором тестовых данных.
#[test]
fn pool_excludes_non_crypto_bases_with_reason() {
    let c = meta("AAPLUSDT", "AAPL", e9(1_000_000));

    let CandidateMeta {
        symbol,
        base_coin,
        quote_coin,
        contract_type,
        launch_time_ms,
        turnover_24h_usd_e9,
        tick_e9,
        last_price_e9,
        min_order_qty_e9,
        qty_step_e9,
        min_notional_value_e9,
    } = c.clone();
    assert_eq!(symbol, "AAPLUSDT");
    assert_eq!(base_coin, "AAPL");
    assert_eq!(quote_coin, "USDT");
    assert_eq!(contract_type, "LinearPerpetual");
    assert_eq!(launch_time_ms, Some(0));
    assert_eq!(turnover_24h_usd_e9, e9(1_000_000));
    assert_eq!(tick_e9, 10_000_000);
    assert_eq!(last_price_e9, e9(100));
    assert_eq!(min_order_qty_e9, 100_000_000);
    assert_eq!(qty_step_e9, 100_000_000);
    assert_eq!(min_notional_value_e9, e9(5));

    let outcome = build_pool(&[c], NOW_MS);
    assert!(
        outcome.pool.is_empty(),
        "акция обязана выбыть по пункту 2, а не остаться в пуле"
    );
    assert_eq!(outcome.excluded.len(), 1);
    assert_eq!(outcome.excluded[0].symbol, "AAPLUSDT");
    assert_eq!(outcome.excluded[0].excluded_reason, EXCLUDED_NON_CRYPTO);
}

/// Тот же пункт 2 на золоте, ETF и второй акции из замера 2026-09-10,
/// записанного в Decision 25: `XAUUSDT`, `SOXLUSDT`, `SNDKUSDT`.
#[test]
fn pool_excludes_gold_etf_and_stock_from_the_recorded_measurement() {
    let candidates = vec![
        meta("XAUUSDT", "XAU", e9(8_000_000)),
        meta("SOXLUSDT", "SOXL", e9(7_000_000)),
        meta("SNDKUSDT", "SNDK", e9(6_000_000)),
        meta("TSLAUSDT", "TSLA", e9(5_000_000)),
        meta("SOLUSDT", "SOL", e9(1_000)),
    ];
    let outcome = build_pool(&candidates, NOW_MS);
    assert_eq!(
        outcome
            .pool
            .iter()
            .map(|c| c.symbol.as_str())
            .collect::<Vec<_>>(),
        vec!["SOLUSDT"]
    );
    assert!(
        outcome
            .excluded
            .iter()
            .all(|e| e.excluded_reason == EXCLUDED_NON_CRYPTO),
        "все четыре — пункт 2: {outcome:?}"
    );
}

/// Пункт 3 Decision 25: возраст < 30 суток (правило Decision 18).
#[test]
fn pool_excludes_recently_listed_instruments_with_reason() {
    let mut c = meta("PONSUSDT", "PONS", e9(1_000_000));
    c.launch_time_ms = Some(NOW_MS - (MIN_LISTED_DAYS - 1) * MS_PER_DAY);
    let outcome = build_pool(&[c], NOW_MS);
    assert!(outcome.pool.is_empty());
    assert_eq!(outcome.excluded.len(), 1);
    assert_eq!(outcome.excluded[0].excluded_reason, EXCLUDED_TOO_YOUNG);
}

/// Приоритет причин — в порядке пунктов плана: имя, затем базовый актив,
/// затем возраст. У символа одна строка и одна причина.
#[test]
fn exclusion_reason_priority_is_name_then_base_then_age() {
    // Молодой BTC: пункты 1 и 3 сразу — побеждает имя.
    let mut young_btc = meta("BTCUSDT", "BTC", e9(1_000_000));
    young_btc.launch_time_ms = Some(NOW_MS);
    // Молодая акция: пункты 2 и 3 сразу — побеждает базовый актив.
    let mut young_stock = meta("AAPLUSDT", "AAPL", e9(900_000));
    young_stock.launch_time_ms = Some(NOW_MS);
    let outcome = build_pool(&[young_btc, young_stock], NOW_MS);
    assert!(outcome.pool.is_empty());
    let mut reasons: Vec<(&str, &str)> = outcome
        .excluded
        .iter()
        .map(|e| (e.symbol.as_str(), e.excluded_reason))
        .collect();
    reasons.sort();
    assert_eq!(
        reasons,
        vec![
            ("AAPLUSDT", EXCLUDED_NON_CRYPTO),
            ("BTCUSDT", EXCLUDED_BTC_ETH),
        ]
    );
}

/// Ядро Decision 25: берутся первые десять **оставшихся**, а не «первая
/// десятка минус выбывшие». Три исключения сидят внутри сырого топ-10 —
/// пул всё равно из десяти, недобор снизу добрасывается из-за черты.
#[test]
fn pool_takes_first_ten_remaining_not_ten_minus_excluded() {
    // Сырой топ-13: BTC, ETH и AAPL внутри первой десятки.
    let mut candidates = vec![
        meta("BTCUSDT", "BTC", e9(13_000)),
        meta("ETHUSDT", "ETH", e9(12_000)),
        meta("AAPLUSDT", "AAPL", e9(11_000)),
    ];
    for i in 0..10 {
        candidates.push(meta(
            &format!("CRYPTO{i}USDT"),
            &format!("CRYPTO{i}"),
            e9(10_000 - i * 100),
        ));
    }
    let outcome = build_pool(&candidates, NOW_MS);
    assert_eq!(
        outcome.pool.len(),
        POOL_SIZE,
        "пул обязан остаться десяткой, хотя трое из сырого топ-10 выбыли"
    );
    assert!(
        outcome
            .pool
            .iter()
            .all(|c| !["BTCUSDT", "ETHUSDT", "AAPLUSDT"].contains(&c.symbol.as_str())),
        "исключённые не могут быть в пуле"
    );
    // Хвост сырого топ-13 (CRYPTO7-9) добрался в пул именно потому, что
    // отбор идёт по оставшимся, а не вычитанием из первой десятки.
    for tail in ["CRYPTO7USDT", "CRYPTO8USDT", "CRYPTO9USDT"] {
        assert!(
            outcome.pool.iter().any(|c| c.symbol == tail),
            "{tail} обязан войти в пул добором снизу"
        );
    }
    // Пул ранжирован по убыванию оборота.
    let turnovers: Vec<i64> = outcome.pool.iter().map(|c| c.turnover_24h_usd_e9).collect();
    let mut sorted = turnovers.clone();
    sorted.sort_by(|a, b| b.cmp(a));
    assert_eq!(turnovers, sorted);
}

/// Прошедшие фильтр, но не вошедшие в десятку, получают строку с причиной
/// среза — иначе «первые десять» непроверяемы по таблице.
#[test]
fn passing_candidates_below_top_ten_get_a_cutoff_row() {
    let mut candidates = Vec::new();
    for i in 0..12 {
        candidates.push(meta(
            &format!("C{i:02}USDT"),
            &format!("C{i:02}"),
            e9(12_000 - i * 100),
        ));
    }
    let outcome = build_pool(&candidates, NOW_MS);
    assert_eq!(outcome.pool.len(), 10);
    assert_eq!(outcome.excluded.len(), 2);
    assert!(
        outcome
            .excluded
            .iter()
            .all(|e| e.excluded_reason == RANK_BEYOND_POOL),
        "оба — срез ранжирования: {outcome:?}"
    );
    assert_eq!(outcome.excluded[0].symbol, "C10USDT");
    assert_eq!(outcome.excluded[1].symbol, "C11USDT");
}

/// Равный оборот — детерминированный тай-брейк по символу, а не порядок
/// входного среза.
#[test]
fn pool_tie_in_turnover_breaks_by_symbol_deterministically() {
    let candidates = vec![
        meta("BETAUSDT", "BETA", e9(500)),
        meta("ALPHAUSDT", "ALPHA", e9(500)),
    ];
    let forward = build_pool(&candidates, NOW_MS);
    let mut backward_input = candidates.clone();
    backward_input.reverse();
    let backward = build_pool(&backward_input, NOW_MS);
    assert_eq!(forward.pool[0].symbol, "ALPHAUSDT");
    assert_eq!(
        forward.pool.iter().map(|c| &c.symbol).collect::<Vec<_>>(),
        backward.pool.iter().map(|c| &c.symbol).collect::<Vec<_>>(),
        "порядок входа не должен влиять на результат"
    );
}

/// Список некриптовых баз содержит ядро замера 2026-09-10 из Decision 25.
/// Ловит молчаливое усыхание списка: пустой `NON_CRYPTO_BASES` неотличим
/// по поведению от отсутствия механизма на вселенных без этих символов.
#[test]
fn non_crypto_list_contains_the_bases_named_in_the_plan() {
    for base in ["AAPL", "XAU", "SOXL", "SNDK"] {
        assert!(
            NON_CRYPTO_BASES.contains(&base),
            "база {base} из замера Decision 25 обязана быть в списке"
        );
    }
}

/// Открытые места спеки: `CLUSDT` (`baseCoin = "CL"`, тикер нефти WTI)
/// исключается по умолчанию, тем же путём, что и прочие некриптовые базы.
#[test]
fn clusdt_is_excluded_as_non_crypto_by_default() {
    assert!(NON_CRYPTO_BASES.contains(&"CL"));
    let candidates = vec![meta("CLUSDT", "CL", e9(1_000_000))];
    let outcome = build_pool(&candidates, NOW_MS);
    assert!(outcome.pool.is_empty());
    assert_eq!(outcome.excluded[0].excluded_reason, EXCLUDED_NON_CRYPTO);
}

// -- base_coins_considered_until_pool_complete (история 2) --------------

/// Тот же сценарий, что и сквозной тест `mod.rs`: семь исключённых по
/// обороту выше десятки CRYPTO*. Правило обязано увидеть ровно
/// семнадцать баз — семь исключённых плюс десять пула — и не дальше:
/// одиннадцатый CRYPTO (ниже десятого по обороту) в списке уже не нужен.
#[test]
fn considered_bases_stop_right_after_the_tenth_survivor() {
    let mut candidates = vec![
        meta("BTCUSDT", "BTC", e9(20_000_000)),
        meta("ETHUSDT", "ETH", e9(19_000_000)),
        meta("AAPLUSDT", "AAPL", e9(18_000_000)),
    ];
    let mut young = meta("PONSUSDT", "PONS", e9(17_000_000));
    young.launch_time_ms = Some(NOW_MS - 9 * 24 * 60 * 60 * 1_000);
    candidates.push(young);
    for i in 0..12 {
        candidates.push(meta(
            &format!("CRYPTO{i}USDT"),
            &format!("CRYPTO{i}"),
            e9(1_000_000 - i * 10_000),
        ));
    }

    let seen = base_coins_considered_until_pool_complete(&candidates, NOW_MS);
    assert_eq!(
        seen,
        vec![
            "BTC", "ETH", "AAPL", "PONS", "CRYPTO0", "CRYPTO1", "CRYPTO2", "CRYPTO3", "CRYPTO4",
            "CRYPTO5", "CRYPTO6", "CRYPTO7", "CRYPTO8", "CRYPTO9"
        ],
        "четыре исключённых по обороту выше пула плюс ровно десять пула — \
         CRYPTO10/CRYPTO11 ниже десятого выжившего и правилу не были нужны"
    );
}

/// Меньше десяти прошедших во всей вселенной — десятого нет, возвращены
/// все рассмотренные, без паники на недостающем индексе.
#[test]
fn considered_bases_returns_everyone_when_pool_never_completes() {
    let candidates = vec![
        meta("ONEUSDT", "ONE", e9(100)),
        meta("BTCUSDT", "BTC", e9(90)),
    ];
    let seen = base_coins_considered_until_pool_complete(&candidates, NOW_MS);
    assert_eq!(seen, vec!["ONE", "BTC"]);
}
