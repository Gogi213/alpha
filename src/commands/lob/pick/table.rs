//! `lob pick` — коммитимая таблица кандидатов и `instruments.csv`
//! (done-condition шага 0.4). Раскладка по колонкам не зависит от сети —
//! строится из уже готового `PoolOutcome` (`super::pool`) и измерений
//! (`super::depth`), поэтому проверена тестом на реальном `csv::Writer`, а
//! не только опосредованно.

use std::collections::HashMap;
use std::path::Path;

use crate::bybit::rest::Instrument;

use super::coverage::eligible_baskets;
use super::depth::{MeasuredCandidate, DEPTH_FLOOR_USD_E9};
use super::order_size::{order_size_22a, order_size_notional_usd_e9};
use super::pool::PoolOutcome;

#[derive(Debug, Clone, serde::Serialize)]
pub struct CandidateRow {
    pub symbol: String,
    pub turnover_24h_usd_e9: i64,
    /// Пусто у членов пула; у остальных — код причины (`EXCLUDED_*`,
    /// `BELOW_TOP10`). Колонка `excluded_reason` шага 0.4: строка на каждое
    /// исключение, потому что признака некриптового актива в API нет.
    pub excluded_reason: String,
    /// Покрытие топ-50 в bps (шаг 0.4, Decision 26а). Пусто у исключённых:
    /// покрытие считается только для пула, остальные строки — про причину,
    /// а не про глубину.
    pub coverage_top50_bps: Option<f64>,
    /// Пригодные корзины расстояния (Decision 26а) метками через `;`
    /// (пусто — ни одна не пригодна либо строка исключённой). Разбор числа
    /// испытаний для DSR — через `count_eligible_trials` по пулу, а не
    /// разбором этой строки.
    pub suitable_baskets: String,
    pub measured: bool,
    pub window_start_utc_ms: Option<i64>,
    pub window_secs: Option<i64>,
    pub events: Option<i64>,
    /// Decision 18(б), ревизия 10: колонки раздельно по стороне — одно
    /// объединённое число пряталось бы за толстой стороной.
    pub median_bid_depth_usd_e9: Option<i64>,
    pub median_ask_depth_usd_e9: Option<i64>,
    /// `true`, только если порог пройден на **обеих** сторонах.
    pub above_depth_floor: Option<bool>,
    /// Размер-22а в базовом активе, 1e9 (Decision 22а, ревизия 17б). `Some` у
    /// членов пула — считается из метаданных инструмента и цены, измерения
    /// глубины не требует; `None` у исключённых (строка — про причину, а не
    /// про размер).
    pub order_size_e9: Option<i64>,
    /// Номинал размера-22а в USD·1e9 — колонка done-condition шага 0.4
    /// («колонка с номиналом размера»): разброс $5–12.42 печатается, не
    /// усредняется; на markout не влияет. `None` — там же, где и размер.
    pub order_size_notional_usd_e9: Option<i64>,
    pub selected_for_pilot: bool,
    pub final_rank: Option<u8>,
}

/// Собирает полную таблицу: строка на каждый рассмотренный символ — сначала
/// пул, затем исключённые — внутри каждой группы по убыванию оборота.
/// Порядок групп фиксирован, а не по общему обороту: BTC/ETH с максимальным
/// оборотом иначе вставали бы первыми и читались как «первые», хотя они
/// исключены; пул — первые строки, потому что он и есть результат шага.
///
/// Каст ранга точен: выбранных ≤ размера пула (≤ 10 по Decision 25), `u8`
/// хватает с запасом в двадцать пять раз.
#[allow(clippy::cast_possible_truncation)]
pub fn build_candidate_table(
    outcome: &PoolOutcome,
    measured: &[MeasuredCandidate],
    selected: &[MeasuredCandidate],
) -> Vec<CandidateRow> {
    let measured_by_symbol: HashMap<&str, &MeasuredCandidate> =
        measured.iter().map(|m| (m.symbol.as_str(), m)).collect();
    let selected_rank: HashMap<&str, u8> = selected
        .iter()
        .enumerate()
        .map(|(i, m)| (m.symbol.as_str(), i as u8 + 1))
        .collect();
    let measured_row = |symbol: &str,
                        turnover_24h_usd_e9: i64,
                        excluded_reason: &str,
                        coverage_top50_bps: Option<f64>,
                        suitable_baskets: String,
                        order_size_e9: Option<i64>,
                        order_size_notional_usd_e9: Option<i64>| {
        let m = measured_by_symbol.get(symbol).copied();
        CandidateRow {
            symbol: symbol.to_string(),
            turnover_24h_usd_e9,
            excluded_reason: excluded_reason.to_string(),
            coverage_top50_bps,
            suitable_baskets,
            order_size_e9,
            order_size_notional_usd_e9,
            measured: m.is_some(),
            window_start_utc_ms: m.map(|m| m.window_start_utc_ms),
            window_secs: m.map(|m| m.window_secs),
            events: m.map(|m| m.events),
            median_bid_depth_usd_e9: m.map(|m| m.median_bid_depth_usd_e9),
            median_ask_depth_usd_e9: m.map(|m| m.median_ask_depth_usd_e9),
            above_depth_floor: m.map(|m| {
                m.median_bid_depth_usd_e9 >= DEPTH_FLOOR_USD_E9
                    && m.median_ask_depth_usd_e9 >= DEPTH_FLOOR_USD_E9
            }),
            selected_for_pilot: selected_rank.contains_key(symbol),
            final_rank: selected_rank.get(symbol).copied(),
        }
    };

    let mut rows = Vec::with_capacity(outcome.pool.len() + outcome.excluded.len());
    for c in &outcome.pool {
        let suitable = eligible_baskets(c.coverage_top50_bps).join(";");
        // Размер-22а на инструмент: из статики контракта и цены тикера, без
        // измерения — поэтому колонка заполнена и у неизмеренных членов пула.
        let order_size_e9 = order_size_22a(
            c.min_order_qty_e9,
            c.qty_step_e9,
            c.min_notional_value_e9,
            c.last_price_e9,
        );
        let order_size_notional_usd_e9 = order_size_notional_usd_e9(order_size_e9, c.last_price_e9);
        rows.push(measured_row(
            &c.symbol,
            c.turnover_24h_usd_e9,
            "",
            c.coverage_top50_bps,
            suitable,
            Some(order_size_e9),
            Some(order_size_notional_usd_e9),
        ));
    }
    for e in &outcome.excluded {
        rows.push(measured_row(
            &e.symbol,
            e.turnover_24h_usd_e9,
            e.excluded_reason,
            None,
            String::new(),
            None,
            None,
        ));
    }
    rows
}

pub fn write_candidate_table_csv(path: &Path, rows: &[CandidateRow]) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut w = csv::Writer::from_path(path)?;
    for row in rows {
        w.serialize(row)?;
    }
    w.flush()?;
    Ok(())
}

/// Обратное к `bybit::ws::parse_e9`: величина 1e-9 обратно в десятичную
/// строку — для человекочитаемого `instruments.csv`. Не переиспользует
/// `probe.rs::format_e9` (тот же приём, там же причина: разная точка
/// использования в чужом файле) — своя маленькая копия здесь, а тест
/// `format_e9_round_trips_through_parse_e9` ниже пином проверяет, что оба
/// файла всё равно говорят об одной и той же величине через общий `parse_e9`.
fn format_e9(v: i64) -> String {
    let neg = v < 0;
    let v = v.unsigned_abs();
    let int_part = v / 1_000_000_000;
    let frac_part = v % 1_000_000_000;
    let mut s = if frac_part == 0 {
        int_part.to_string()
    } else {
        let mut frac_str = format!("{frac_part:09}");
        while frac_str.ends_with('0') {
            frac_str.pop();
        }
        format!("{int_part}.{frac_str}")
    };
    if neg {
        s.insert(0, '-');
    }
    s
}

#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
struct InstrumentRow {
    symbol: String,
    tick_size: String,
    min_order_qty: String,
    qty_step: String,
    min_notional_value: String,
}

/// `instruments.csv` (done-condition шага 0.4: «непуст», и размер-22а обоих
/// финалистов посчитан из этих полей и укладывается в правила биржи,
/// Decision 22а) — пишется
/// для **всего** прошедшего REST пула, не только для выбранных: `lob probe`
/// и `lob record` читают эти же метаданные для любого символа, который
/// когда-либо попадёт в запись, а не только для сегодняшнего победителя.
pub fn write_instruments_csv(path: &Path, instruments: &[Instrument]) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut w = csv::Writer::from_path(path)?;
    for inst in instruments {
        w.serialize(InstrumentRow {
            symbol: inst.symbol.clone(),
            tick_size: format_e9(inst.tick_e9),
            min_order_qty: format_e9(inst.min_order_qty_e9),
            qty_step: format_e9(inst.qty_step_e9),
            min_notional_value: format_e9(inst.min_notional_value_e9),
        })?;
    }
    w.flush()?;
    Ok(())
}

#[cfg(test)]
mod tests {
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
        above_depth_floor: Option<bool>,
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
    /// - `NEARUSDT` — пул, измерен, прошёл порог, но НЕ отобран
    ///   (`selected_for_pilot = false`, `final_rank = None`);
    /// - `MID2USDT` — пул, но остался БЕЗ измерения (`measured = false`) —
    ///   реальный, не синтетический случай: `measure_prefiltered` обошла все
    ///   десять символов, но не для всех в `measured` попал результат
    ///   (соединение оборвалось, символ не набрал ни одного события за час
    ///   и т. п.);
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
                above_depth_floor: Some(true),
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
                median_bid_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 + e9(3)),
                median_ask_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 + e9(4)),
                above_depth_floor: Some(true),
                order_size_e9: Some(500_000_000),
                order_size_notional_usd_e9: Some(7_500_000_000),
                selected_for_pilot: false,
                final_rank: None,
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
                above_depth_floor: None,
                // Размер из метаданных и цены, измерения не требует — поэтому
                // заполнен и у неизмеренного члена пула, в отличие от глубин.
                order_size_e9: Some(1_000_000_000),
                order_size_notional_usd_e9: Some(5_000_000_000),
                selected_for_pilot: false,
                final_rank: None,
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
                above_depth_floor: None,
                order_size_e9: None,
                order_size_notional_usd_e9: None,
                selected_for_pilot: false,
                final_rank: None,
            },
        ];

        let tmp = tempfile::NamedTempFile::new().unwrap();
        write_candidate_table_csv(tmp.path(), &rows).unwrap();

        let mut reader = csv::Reader::from_path(tmp.path()).unwrap();
        let read_back: Vec<CandidateRowOwned> =
            reader.deserialize().collect::<Result<_, _>>().unwrap();

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
                    above_depth_floor: Some(true),
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
                    median_bid_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 + e9(3)),
                    median_ask_depth_usd_e9: Some(DEPTH_FLOOR_USD_E9 + e9(4)),
                    above_depth_floor: Some(true),
                    order_size_e9: Some(500_000_000),
                    order_size_notional_usd_e9: Some(7_500_000_000),
                    selected_for_pilot: false,
                    final_rank: None,
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
                    above_depth_floor: None,
                    order_size_e9: Some(1_000_000_000),
                    order_size_notional_usd_e9: Some(5_000_000_000),
                    selected_for_pilot: false,
                    final_rank: None,
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
                    above_depth_floor: None,
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
             члена пула (считается без замера) и пуст только у исключённой"
        );
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
}
