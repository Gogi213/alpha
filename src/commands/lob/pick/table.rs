//! `lob pick` — коммитимая таблица кандидатов и `instruments.csv`
//! (done-condition шага 0.4). Раскладка по колонкам не зависит от сети —
//! строится из уже готового `PoolOutcome` (`super::pool`) и измерений
//! (`super::depth`), поэтому проверена тестом на реальном `csv::Writer`, а
//! не только опосредованно.

use std::collections::HashMap;
use std::path::Path;

use crate::bybit::rest::Instrument;

use super::coverage::eligible_baskets;
use super::depth::{depth_check, DepthCheck, MeasuredCandidate};
use super::h3::H3FloorInfo;
use super::order_size::{order_size_22a, order_size_notional_usd_e9};
use super::pool::{PoolCandidate, PoolOutcome};

#[derive(Debug, Clone, serde::Serialize)]
pub struct CandidateRow {
    pub symbol: String,
    pub turnover_24h_usd_e9: i64,
    /// Пусто у членов пула; у остальных — код причины: три исключения по
    /// правилам §2 (`EXCLUDED_*`) либо срез ранга (`RANK_BEYOND_POOL`).
    /// Колонка `excluded_reason` шага 0.4: строка на каждое исключение,
    /// потому что признака некриптового актива в API нет. Глубина сюда не
    /// попадает никогда (таск 27) — у неё своя `depth_check`.
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
    /// Вердикт часового замера: `ok` | `below_floor` | `not_measured`
    /// (`super::depth::DepthCheck`), пусто у строк вне пула — их никто не
    /// мерил. **Строка отчёта, не критерий отбора** (BUSINESS-TASK §9):
    /// `below_floor` не убирает инструмент из пула и не гасит
    /// `selected_for_pilot`. До таска 27 здесь стояла `above_depth_floor`,
    /// и её `false` означал исключение из пула — ровно то противоречие
    /// задаче, которое назвал аудит 2026-09-12 (`SETTLED.md` В-35).
    pub depth_check: String,
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
/// **Отобран пилоту — весь пул, и только он** (таск 27, BUSINESS-TASK §2
/// «первые десять оставшихся»): `selected_for_pilot` и `final_rank` идут от
/// членства в `outcome.pool` и от ранга по обороту внутри него, а не от
/// замера глубины. Замер даёт только колонку `depth_check`. Раньше здесь
/// был третий аргумент `selected` — выжившие порога глубины, — и он резал
/// пул до восьми (аудит 2026-09-12, `SETTLED.md` В-35).
///
/// Каст ранга точен: пул ≤ `POOL_SIZE` (10 по Decision 25), `u8` хватает
/// с запасом в двадцать пять раз.
#[allow(clippy::cast_possible_truncation)]
pub fn build_candidate_table(
    outcome: &PoolOutcome,
    measured: &[MeasuredCandidate],
) -> Vec<CandidateRow> {
    let measured_by_symbol: HashMap<&str, &MeasuredCandidate> =
        measured.iter().map(|m| (m.symbol.as_str(), m)).collect();
    let measured_row = |symbol: &str,
                        turnover_24h_usd_e9: i64,
                        excluded_reason: &str,
                        coverage_top50_bps: Option<f64>,
                        suitable_baskets: String,
                        order_size_e9: Option<i64>,
                        order_size_notional_usd_e9: Option<i64>,
                        pool_rank: Option<u8>| {
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
            // Строка вне пула не мерилась и метки не получает — пусто, а не
            // `not_measured`: «не мерили, потому что исключён» и «мерили и не
            // домерили» — разные факты.
            depth_check: match pool_rank {
                Some(_) => depth_check(m).as_str().to_string(),
                None => String::new(),
            },
            selected_for_pilot: pool_rank.is_some(),
            final_rank: pool_rank,
        }
    };

    let mut rows = Vec::with_capacity(outcome.pool.len() + outcome.excluded.len());
    for (i, c) in outcome.pool.iter().enumerate() {
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
            Some(i as u8 + 1),
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
            None,
        ));
    }
    rows
}

/// `debug_label` — предупреждение отладочного окна (`super::h3::
/// debug_window_warning`), первой строкой файла как комментарий `#`:
/// `None` на боевом окне, ничего не пишет (критерий приёмки таска 08—
/// коммитимая таблица боевого прогона без метки debug).
pub fn write_candidate_table_csv(
    path: &Path,
    rows: &[CandidateRow],
    debug_label: Option<&str>,
) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut file = std::fs::File::create(path)?;
    if let Some(msg) = debug_label {
        use std::io::Write as _;
        writeln!(file, "# {msg}")?;
    }
    let mut w = csv::Writer::from_writer(file);
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

/// Строка `instruments.csv` с полом `H3` (план D-H3, таск 08): те же пять
/// колонок, что и `InstrumentRow`, плюс `h3_lots`/`k`/`median_trade_lots` и
/// время окна замера. Пустые `Option` — символ вне измеренного пула: колонка
/// существует (`h3_lots_for_symbol`, таск 02, ищет её по имени), значения
/// нет — не 0, тем же приёмом, что и у прочих замеров этого шага.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
struct InstrumentRowWithH3 {
    symbol: String,
    tick_size: String,
    min_order_qty: String,
    qty_step: String,
    min_notional_value: String,
    h3_lots: Option<i64>,
    k: Option<f64>,
    median_trade_lots: Option<i64>,
    window_start_utc_ms: Option<i64>,
    window_secs: Option<i64>,
    /// Вердикт часового замера глубины: `ok` | `below_floor` |
    /// `not_measured` (таск 27). Строка отчёта, не критерий: инструмент с
    /// `below_floor` стоит в этом файле наравне с остальными — иначе
    /// `session.rs::load_pool` не подписался бы на него, а задача (§2, §3)
    /// требует ровно обратного.
    depth_check: String,
}

/// `instruments.csv` с полом `H3` — коммитимый вывод `lob pick` (критерий
/// приёмки таска 08): пишется для **всего** прошедшего REST пула, как и
/// `write_instruments_csv`, но с `h3_lots`/`k`/`median_trade_lots` там, где
/// символ измерен (`h3_by_symbol`). `debug_label` — та же метка отладочного
/// окна первой строкой (`#`), что и в `write_candidate_table_csv`.
/// Подмножество инструментов ровно с теми символами, что вошли в **пул**
/// (`super::pool::build_pool` — первые десять оставшихся по обороту после
/// трёх исключений §2), а не вся вселенная REST. Дозапрос по ревью таска 08
/// (ось Манифест, R33 «пул фиксируется»): `instruments.csv` корня обязан
/// нести только пул — иначе `session.rs::load_pool` подписал бы `lob
/// session` на всю вселенную инструментов, а `power.rs::pool_size` посчитал
/// бы `N` для DSR по её размеру, а не по пулу.
///
/// Таск 27: вход — пул, а не выжившие порога глубины. Порядок — ранг пула
/// по обороту (порядок `build_pool`), не порядок REST-ответа и не порядок
/// по измеренной глубине: глубина ничего не решает (BUSINESS-TASK §9).
/// Символ без записи в `instruments` (сеть не вернула метаданные)
/// пропускается молча — та же причина, что `join_candidate_meta` уже
/// отбрасывает символ без тикера.
pub fn instruments_for_pool(instruments: &[Instrument], pool: &[PoolCandidate]) -> Vec<Instrument> {
    let by_symbol: HashMap<&str, &Instrument> =
        instruments.iter().map(|i| (i.symbol.as_str(), i)).collect();
    pool.iter()
        .filter_map(|c| by_symbol.get(c.symbol.as_str()).map(|i| (*i).clone()))
        .collect()
}

/// Единственный читатель `instruments.csv` во всём дереве (дозапрос по
/// ревью таска 08, ось Craft): окно короче боевого пишет метку `debug`
/// первой строкой (`# ...`, `write_candidate_table_csv`/этот файл), и
/// голый `csv::Reader::from_path` читает её как заголовок вместо
/// настоящего — `session.rs::load_pool` и `power.rs::pool_size` падали
/// ровно на этом. Все читатели `instruments.csv` (`levels::
/// h3_lots_for_symbol`, `record::load_steps_for_symbol`,
/// `session::load_pool`, `power::pool_size`) обязаны идти через эту
/// функцию, а не заводить свой `csv::Reader::from_path` — второй
/// нетерпимый к `#` ридер воспроизвёл бы тот же дефект под другим именем.
pub fn instruments_csv_reader(path: &Path) -> csv::Result<csv::Reader<std::fs::File>> {
    csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_path(path)
}

/// `depth_by_symbol` — вердикт часового замера на символ (таск 27): символа
/// нет в карте — `not_measured`. Карта, а не поле `H3FloorInfo`: пол `H3`
/// есть только у символа с лентой сделок, а метка глубины обязана быть у
/// каждой строки файла.
pub fn write_instruments_csv_with_h3(
    path: &Path,
    instruments: &[Instrument],
    h3_by_symbol: &HashMap<String, H3FloorInfo>,
    depth_by_symbol: &HashMap<String, DepthCheck>,
    debug_label: Option<&str>,
) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut file = std::fs::File::create(path)?;
    if let Some(msg) = debug_label {
        use std::io::Write as _;
        writeln!(file, "# {msg}")?;
    }
    let mut w = csv::Writer::from_writer(file);
    for inst in instruments {
        let h3 = h3_by_symbol.get(&inst.symbol);
        w.serialize(InstrumentRowWithH3 {
            symbol: inst.symbol.clone(),
            tick_size: format_e9(inst.tick_e9),
            min_order_qty: format_e9(inst.min_order_qty_e9),
            qty_step: format_e9(inst.qty_step_e9),
            min_notional_value: format_e9(inst.min_notional_value_e9),
            h3_lots: h3.map(|h| h.h3_lots),
            k: h3.map(|h| h.k),
            median_trade_lots: h3.map(|h| h.median_trade_lots),
            window_start_utc_ms: h3.map(|h| h.window_start_utc_ms),
            window_secs: h3.map(|h| h.window_secs),
            depth_check: depth_by_symbol
                .get(&inst.symbol)
                .copied()
                .unwrap_or(DepthCheck::NotMeasured)
                .as_str()
                .to_string(),
        })?;
    }
    w.flush()?;
    Ok(())
}

#[cfg(test)]
mod tests;
