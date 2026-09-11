//! `lob pick` — отбор пула из десяти инструментов и часовой замер глубины
//! (шаг 0.4, Decision 25). Этот файл держит только CLI (`PickArgs`), сборку
//! итога (`PickReport`) и точку входа (`run_pick`/`run_pick_async`); сама
//! логика правила разложена по соседним файлам, каждый — своя
//! ответственность, ни один не переносит ввод-вывод в другой:
//!
//! - `pool` — правило пула Decision 25: `build_pool`, три исключения,
//!   `NON_CRYPTO_BASES`, `CandidateMeta`/`join_candidate_meta`.
//! - `coverage` — покрытие топ-50 книги в bps и пригодность корзин
//!   расстояния Decision 26а (`coverage_top50_bps`, `eligible_baskets`,
//!   `count_eligible_trials`).
//! - `depth` — модель измеренной глубины и порог отбора: медиана по уровням
//!   снимка, время-взвешенное усреднение по стороне, `MeasuredCandidate`,
//!   `survivors_above_depth_floor`.
//! - `measure` — тонкая сетевая оболочка часового замера (`measure_one_symbol`,
//!   `measure_prefiltered`): собирает вход для `depth`, сама не решает ничего.
//! - `order_size` — размер ордера по формуле биржи, Decision 22а (`order_size_22a`).
//! - `table` — коммитимая таблица кандидатов и `instruments.csv`
//!   (`build_candidate_table`, `write_candidate_table_csv`, `write_instruments_csv`).
//!
//! Разрез механический (таск 01, дозапрос): поведение, публичные имена и CLI
//! не менялись, только расположение — каждый файл ниже потолка в 900 строк,
//! тесты уехали со своими функциями и остались на публичных интерфейсах.
//! Чистые функции (`pool`, `coverage`, `depth`, `order_size`, `table`) не
//! делают ввода-вывода вообще и проверены тестами исчерпывающе, в том числе
//! на вырожденных входах; сетевая оболочка (`measure`, и `run_pick` здесь)
//! часовой замер не покрыта тестами без сети по той же причине, по которой
//! её нельзя устроить в CI, — и это не пробел, а прямое следствие того, что
//! вся логика уже вынесена наружу.

use std::collections::HashMap;
use std::path::PathBuf;

use clap::Args;

use crate::bybit::rest::{
    fetch_all_linear_instruments, fetch_linear_tickers, BybitPublicRest, Instrument,
    BYBIT_MAINNET_URL,
};

mod coverage;
mod depth;
mod h3;
mod measure;
mod order_size;
mod pool;
mod table;

pub use coverage::{
    book_already_costs, count_eligible_trials, coverage_top50_bps, eligible_baskets,
    DISTANCE_BASKETS,
};
pub use depth::{
    median_depth_per_level_usd_e9, survivors_above_depth_floor, DepthSample, MeasuredCandidate,
    PickError, DEPTH_FLOOR_USD_E9,
};
pub use depth::{time_weighted_median_ask_depth_usd_e9, time_weighted_median_bid_depth_usd_e9};
pub use h3::{debug_window_warning, h3_lots_floor, H3FloorInfo};
pub use measure::{measure_prefiltered, MEASUREMENT_WINDOW_SECS};
pub use order_size::order_size_22a;
pub use pool::{
    base_coins_considered_until_pool_complete, build_pool, is_listed_long_enough,
    join_candidate_meta, CandidateMeta, ExcludedCandidate, PoolCandidate, PoolOutcome, BELOW_TOP10,
    EXCLUDED_BTC_ETH, EXCLUDED_NON_CRYPTO, EXCLUDED_TOO_YOUNG, MIN_LISTED_DAYS, NON_CRYPTO_BASES,
    POOL_SIZE,
};
pub use table::{
    build_candidate_table, instruments_csv_reader, instruments_for_pool, write_candidate_table_csv,
    write_instruments_csv, write_instruments_csv_with_h3, CandidateRow,
};

use measure::wall_clock_ms;

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

#[derive(Debug, Args)]
pub struct PickArgs {
    /// REST-хост Bybit v5. Параметр, не константа — тесты и `testnet`
    /// подставляют другой (см. `bybit::rest::BybitPublicRest`).
    #[arg(long, default_value = BYBIT_MAINNET_URL)]
    pub base_url: String,
    /// Куда писать рабочий `instruments.csv` (Decision 7: рядом с записью,
    /// не в git) — то, что читают `lob record`/`lob levels --h3-mode floor`
    /// в этой же сессии.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Куда писать коммитимую таблицу кандидатов (done-condition шага 0.4).
    #[arg(long, default_value = "docs/plan/candidates.csv")]
    pub candidates_out: PathBuf,
    /// Куда писать коммитимую заморозку `instruments.csv` (критерий приёмки
    /// таска 08: «`candidates.csv` и `instruments.csv` закоммичены») —
    /// отдельно от `--root`, тот не в git (Decision 7).
    #[arg(long, default_value = "instruments.csv")]
    pub instruments_out: PathBuf,
    /// Длительность окна замера глубины и ленты сделок в секундах.
    /// Без умолчания (поправка оркестратора к таску 08, фаза отладки —
    /// `PLAN.md` D-ОТЛАДКА): боевой отбор требует `MEASUREMENT_WINDOW_SECS`
    /// (3600 с) — любое другое значение годится только для отладки конвейера
    /// и помечается `debug` в stderr и в шапке коммитимых CSV
    /// (`h3::debug_window_warning`), а не для заморозки пула.
    #[arg(long)]
    pub window_secs: u64,
    /// Множитель `k` формулы пола `H3` (план D-H3): `h3_lots = floor(k ×
    /// median_trade_lots)`. Без умолчания — ни `PLAN.md`, ни спека числа не
    /// называют, назначается владельцем до данных (изобретённое число
    /// запрещено, `interfaces.md`).
    #[arg(long)]
    pub h3_k: f64,
}

/// Итог `lob pick`: полная таблица (все промежуточные колонки) и подмножество
/// пула, прошедшее порог глубины (см. doc `survivors_above_depth_floor`);
/// ошибка возможна и после сети, не только до неё.
pub struct PickReport {
    pub table: Vec<CandidateRow>,
    pub selected: Vec<MeasuredCandidate>,
}

/// Точка входа `lob pick`. Синхронная сигнатура по образцу
/// `probe.rs`/`rest.rs`: свой рантайм внутри, `block_on` наружу — вызывающему
/// (будущему `main.rs`) не нужно становиться асинхронным ради одной команды.
/// Многопоточный рантайм (не `current_thread`, как у `BybitPublicRest`):
/// `measure_prefiltered` держит до `POOL_SIZE` живых сокетов
/// одновременно, и разнести их по нескольким потокам дешевле, чем гонять
/// такое количество на одном.
pub fn run_pick(args: &PickArgs) -> anyhow::Result<PickReport> {
    let runtime = tokio::runtime::Runtime::new()?;
    runtime.block_on(run_pick_async(args))
}

async fn run_pick_async(args: &PickArgs) -> anyhow::Result<PickReport> {
    // Поправка оркестратора к таску 08 (фаза отладки, `PLAN.md` D-ОТЛАДКА):
    // окно короче боевого не годится для заморозки пула — метка идёт и в
    // stderr, и первой строкой (`#`) в оба коммитимых CSV ниже.
    let debug_label = debug_window_warning(args.window_secs);
    if let Some(msg) = &debug_label {
        eprintln!("pick: {msg}");
    }

    let mut rest = BybitPublicRest::new(args.base_url.clone())?;
    let instruments = fetch_all_linear_instruments(&mut rest)?;
    let tickers = fetch_linear_tickers(&mut rest)?;
    // Инструментов ~863 из REST-ответа сюда не идёт вовсе (дозапрос по
    // ревью таска 08, ось Манифест, R33 «пул фиксируется»): единственная
    // запись `instruments.csv` — ниже, после отбора, и несёт только пул.
    // Полный список остаётся в `docs/plan/candidates.csv`.

    let meta = join_candidate_meta(&instruments, &tickers);
    let now_ms = wall_clock_ms();

    // История 2 спеки (R28–R31): базовые активы ровно тех кандидатов,
    // которых правило увидело по пути к десятому выжившему — не всех 863.
    println!(
        "pick: базовые активы кандидатов до десятого выжившего: {}",
        base_coins_considered_until_pool_complete(&meta, now_ms).join(",")
    );

    let outcome = build_pool(&meta, now_ms);
    // Единственный источник N для DSR (Decision 26а, В-23): число пригодных
    // пар (инструмент, корзина) по пулу — таск 09/13 берут это число, не
    // номинальный крест.
    println!(
        "pick: пригодных пар (инструмент, корзина) = {}",
        count_eligible_trials(&outcome.pool)
    );
    // Decision 26б: факт печатается строкой, инструмент не исключается.
    for c in &outcome.pool {
        if book_already_costs(c.coverage_top50_bps) {
            println!(
                "pick: {} — вся видимая книга топ-50 уже дешевле круговых издержек \
                 ({:.3} bps < {} bps), инструмент не исключается",
                c.symbol,
                c.coverage_top50_bps.unwrap_or_default(),
                crate::lob::costs::ROUNDTRIP_FEES_BPS
            );
        }
    }

    let instruments_by_symbol: HashMap<String, &Instrument> =
        instruments.iter().map(|i| (i.symbol.clone(), i)).collect();
    // Замеряются все десять пула, а не предфильтрованное подмножество:
    // час живого стакана — протокол шага 0.4, сохранённый ревизией 17.
    // Тем же окном и тем же соединением копится лента `publicTrade` — вход
    // медианы размера сделки пола H3 (план D-H3, таск 08).
    let measured =
        measure_prefiltered(&outcome.pool, &instruments_by_symbol, args.window_secs).await;
    // Диагностика на пути отказа: какие медианы намерялись, по каждому
    // кандидату — иначе следующий провал снова виден только как «ни один».
    // Печать в stderr, не в таблицу: таблица пишется только на успехе.
    let selected = match survivors_above_depth_floor(&measured) {
        Ok(sel) => sel,
        Err(e) => {
            let mut rows: Vec<&MeasuredCandidate> = measured.iter().collect();
            rows.sort_by(|a, b| a.symbol.cmp(&b.symbol));
            for m in rows {
                eprintln!(
                    "pick measured: {} events={} bid_med={} ask_med={}",
                    m.symbol, m.events, m.median_bid_depth_usd_e9, m.median_ask_depth_usd_e9
                );
            }
            return Err(e.into());
        }
    };

    // Пол H3 на измеренный инструмент: floor(k × медиана размера сделки),
    // колонки instruments.csv (критерий приёмки таска 08). Символ без
    // медианы (окно не поймало ни одной неблочной сделки) остаётся без
    // h3_lots — не 0.
    let h3_by_symbol: HashMap<String, H3FloorInfo> = measured
        .iter()
        .filter_map(|m| {
            let median_trade_lots = m.median_trade_lots?;
            let h3_lots = h3_lots_floor(Some(median_trade_lots), args.h3_k)?;
            Some((
                m.symbol.clone(),
                H3FloorInfo {
                    k: args.h3_k,
                    median_trade_lots,
                    h3_lots,
                    window_start_utc_ms: m.window_start_utc_ms,
                    window_secs: m.window_secs,
                },
            ))
        })
        .collect();
    // Только пул: `session.rs::load_pool` подписывает `lob session` на
    // каждую строку этого файла, а `power.rs::pool_size` считает по числу
    // строк `N` для DSR — вся вселенная REST здесь не годится ни для того,
    // ни для другого (дозапрос по ревью таска 08, ось Манифест).
    let pool_instruments = instruments_for_pool(&instruments, &selected);
    write_instruments_csv_with_h3(
        &args.root.join("instruments.csv"),
        &pool_instruments,
        &h3_by_symbol,
        debug_label.as_deref(),
    )?;
    write_instruments_csv_with_h3(
        &args.instruments_out,
        &pool_instruments,
        &h3_by_symbol,
        debug_label.as_deref(),
    )?;

    // Decision 22а, done-condition шага 0.4: размер считается на инструмент
    // (`order_size_22a` внутри `build_candidate_table` ниже) и всегда
    // допустим по построению — путём отказа он не является, прежняя проверка
    // Decision 22 с ошибкой `MinNotionalNotSatisfied` отменена ревизией 17б.
    let table = build_candidate_table(&outcome, &measured, &selected);
    write_candidate_table_csv(&args.candidates_out, &table, debug_label.as_deref())?;

    Ok(PickReport { table, selected })
}

// ---------------------------------------------------------------------------
// Сквозной тест: весь путь Decision 25 от сырых метаданных до финальных
// строк таблицы на синтетической вселенной. Единственный тест, который
// пересекает границы всех подмодулей разом (`pool` → `depth` →
// `table`/`order_size`), поэтому он живёт здесь, а не в одном из них.
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
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

        let selected = survivors_above_depth_floor(&measured).unwrap();
        assert_eq!(
            selected
                .iter()
                .map(|c| c.symbol.clone())
                .collect::<Vec<_>>(),
            vec!["CRYPTO0USDT", "CRYPTO1USDT"]
        );

        let table = build_candidate_table(&outcome, &measured, &selected);
        assert_eq!(
            table.len(),
            outcome.pool.len() + outcome.excluded.len(),
            "таблица несёт пул и все исключения, не только выживших"
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
        assert_eq!(c0_row.above_depth_floor, Some(true));
        assert!(c0_row.selected_for_pilot);
        assert_eq!(c0_row.final_rank, Some(1));
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
    }
}
