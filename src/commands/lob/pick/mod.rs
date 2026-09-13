//! `lob pick` — отбор пула (`--top`, по умолчанию десять, В-35) и часовой замер глубины
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
//! - `depth` — модель измеренной глубины и её **проверка**: медиана по
//!   уровням снимка, время-взвешенное усреднение по стороне,
//!   `MeasuredCandidate`, `depth_check`. Отбора здесь нет с таска 27:
//!   глубина не решает состав пула (BUSINESS-TASK §9, `SETTLED.md` В-35).
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
    depth_check, median_depth_per_level_usd_e9, DepthCheck, DepthSample, MeasuredCandidate,
    DEPTH_FLOOR_USD_E9,
};
pub use depth::{time_weighted_median_ask_depth_usd_e9, time_weighted_median_bid_depth_usd_e9};
pub use h3::{debug_window_warning, h3_lots_floor, H3FloorInfo};
pub use measure::{measure_prefiltered, MEASUREMENT_WINDOW_SECS};
pub use order_size::order_size_22a;
pub use pool::{
    base_coins_considered_until_pool_complete, build_pool, is_listed_long_enough,
    join_candidate_meta, CandidateMeta, ExcludedCandidate, PoolCandidate, PoolOutcome,
    EXCLUDED_BTC_ETH, EXCLUDED_NON_CRYPTO, EXCLUDED_TOO_YOUNG, MIN_LISTED_DAYS, NON_CRYPTO_BASES,
    POOL_SIZE, RANK_BEYOND_POOL,
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
    /// Сколько инструментов берёт пул: первые `--top` прошедших три исключения
    /// по обороту за 24 ч. Умолчание — `POOL_SIZE` (десять, Decision 25/В-35);
    /// владелец 2026-09-13: «раскатка на топ 50» (В-50) — размер пула стал
    /// **параметром**, а не константой кода. Число строк `instruments.csv`
    /// равно `--top`, и каждая строка — свой инструмент замера (своё
    /// соединение на окно глубины).
    #[arg(long, default_value_t = POOL_SIZE)]
    pub top: usize,
}

/// Итог `lob pick`: полная таблица (все промежуточные колонки) и члены пула,
/// по которым час замера что-то вернул, в порядке ранга пула.
///
/// Таск 27: `selected` больше не «прошедшие порог глубины» — состав пула
/// решают только исключения §2 и ранг по обороту, а глубина едет колонкой
/// `depth_check`. Здесь остаются лишь **измеренные** члены пула, потому что
/// у неизмеренного нет чисел глубины для печати; кто в пуле на самом деле —
/// говорит `table` (`selected_for_pilot` ровно у `--top`).
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
    if args.top == 0 {
        anyhow::bail!(
            "--top 0: пул не может быть пустым — нечего записывать и не на что подписываться"
        );
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
    // которых правило увидело по пути к N-му выжившему — не всех 863.
    println!(
        "pick: базовые активы кандидатов до {}-го выжившего: {}",
        args.top,
        base_coins_considered_until_pool_complete(&meta, now_ms, args.top).join(",")
    );

    let outcome = build_pool(&meta, now_ms, args.top);
    println!("pick: пул — топ {} по обороту за 24 ч", outcome.pool.len());
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
    // Замеряются все члены пула, а не предфильтрованное подмножество:
    // час живого стакана — протокол шага 0.4, сохранённый ревизией 17.
    // Тем же окном и тем же соединением копится лента `publicTrade` — вход
    // медианы размера сделки пола H3 (план D-H3, таск 08).
    let measured =
        measure_prefiltered(&outcome.pool, &instruments_by_symbol, args.window_secs).await;
    let measured_by_symbol: HashMap<&str, &MeasuredCandidate> =
        measured.iter().map(|m| (m.symbol.as_str(), m)).collect();

    // Час живой глубины — проверка, а не критерий отбора (BUSINESS-TASK §9,
    // `SETTLED.md` В-35): вердикт печатается строкой на каждый инструмент
    // пула и колонкой `depth_check` в обоих CSV; из пула никто не выбывает.
    // Это ровно то место, где до таска 27 стоял отсев, оставлявший восемь
    // из десяти и выбрасывавший `ZECUSDT`, которого §3 защищает поимённо.
    let depth_by_symbol: HashMap<String, DepthCheck> = outcome
        .pool
        .iter()
        .map(|c| {
            (
                c.symbol.clone(),
                depth_check(measured_by_symbol.get(c.symbol.as_str()).copied()),
            )
        })
        .collect();
    for (rank, c) in outcome.pool.iter().enumerate() {
        let check = depth_by_symbol
            .get(&c.symbol)
            .copied()
            .unwrap_or(DepthCheck::NotMeasured);
        let m = measured_by_symbol.get(c.symbol.as_str()).copied();
        println!(
            "pick pool: {} {} depth_check={} bid_med={} ask_med={} events={}",
            rank + 1,
            c.symbol,
            check.as_str(),
            m.map_or_else(
                || "-".to_string(),
                |m| m.median_bid_depth_usd_e9.to_string()
            ),
            m.map_or_else(
                || "-".to_string(),
                |m| m.median_ask_depth_usd_e9.to_string()
            ),
            m.map_or_else(|| "-".to_string(), |m| m.events.to_string()),
        );
        if check.is_warning() {
            println!(
                "pick: {} — глубина {} против порога {} USD на уровень; \
                 инструмент остаётся в пуле, это строка отчёта, а не критерий отбора (§9)",
                c.symbol,
                check.as_str(),
                DEPTH_FLOOR_USD_E9 / 1_000_000_000,
            );
        }
    }
    // Порядок пула — ранг по обороту (`build_pool`); в `selected` едут те его
    // члены, по которым час что-то намерил.
    let selected: Vec<MeasuredCandidate> = outcome
        .pool
        .iter()
        .filter_map(|c| {
            measured_by_symbol
                .get(c.symbol.as_str())
                .map(|m| (*m).clone())
        })
        .collect();

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
    let pool_instruments = instruments_for_pool(&instruments, &outcome.pool);
    write_instruments_csv_with_h3(
        &args.root.join("instruments.csv"),
        &pool_instruments,
        &h3_by_symbol,
        &depth_by_symbol,
        debug_label.as_deref(),
    )?;
    write_instruments_csv_with_h3(
        &args.instruments_out,
        &pool_instruments,
        &h3_by_symbol,
        &depth_by_symbol,
        debug_label.as_deref(),
    )?;

    // Decision 22а, done-condition шага 0.4: размер считается на инструмент
    // (`order_size_22a` внутри `build_candidate_table` ниже) и всегда
    // допустим по построению — путём отказа он не является, прежняя проверка
    // Decision 22 с ошибкой `MinNotionalNotSatisfied` отменена ревизией 17б.
    let table = build_candidate_table(&outcome, &measured);
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
mod tests;
