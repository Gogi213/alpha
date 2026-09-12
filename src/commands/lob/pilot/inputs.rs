//! Чтение входов пилота с диска: пул из `instruments.csv`, его копия в
//! каталог сессии, сигналы бэктеста из `levels-floor-*.csv`, лот по
//! Decision 22а и RTT из `probe-*.csv`/`clock.csv`. Отдельно от `pilot.rs`:
//! только разбор файлов — ни реплея, ни вердикта.

use std::path::Path;

use crate::bybit::probe::percentile_ns;
use crate::bybit::ws::parse_e9;
use crate::commands::lob::pick::order_size_22a;

/// Символ из `instruments.csv`: колонка `symbol` (единственный читатель —
/// `pick::instruments_csv_reader`, терпит метку `debug` первой строкой).
#[derive(Debug, serde::Deserialize)]
struct PoolSymbolRow {
    symbol: String,
}

pub(super) fn read_pool_symbols(instruments_csv: &Path) -> anyhow::Result<Vec<String>> {
    let mut r =
        crate::commands::lob::pick::instruments_csv_reader(instruments_csv).map_err(|e| {
            anyhow::anyhow!(
                "{}: {e} — lob pilot читает пул из instruments.csv",
                instruments_csv.display()
            )
        })?;
    let mut out = Vec::new();
    for row in r.deserialize::<PoolSymbolRow>() {
        out.push(row?.symbol);
    }
    anyhow::ensure!(!out.is_empty(), "{}: пул пуст", instruments_csv.display());
    Ok(out)
}

/// Копия `instruments.csv` пула в `session_dir` — нужна режиму `floor`
/// (`resolve_h3_mode`) и подсчёту лота (`compute_order_qty_e9` в цепочке
/// ниже), которые читают его рядом с бинлогом символа, а не берут
/// `--pool-instruments` напрямую. Про имя бинлога не знает: раньше (до
/// таска 19, часть 2) эта же функция ещё и клала алиас `<SYMBOL>.binlog`
/// без даты для `backtest.rs`/`profiles.rs`/`watch.rs` —
/// `alias_dated_binlogs_for_legacy_readers` снята, все три находят
/// `<SYMBOL>-<день>.binlog` напрямую через `super::session_binlog_for`.
pub(super) fn copy_pool_instruments_csv(
    session_dir: &Path,
    pool_instruments: &Path,
) -> anyhow::Result<()> {
    std::fs::copy(pool_instruments, session_dir.join("instruments.csv"))
        .map_err(|e| anyhow::anyhow!("копия instruments.csv в {}: {e}", session_dir.display()))?;
    Ok(())
}

/// Строка `levels-floor-<SYMBOL>.csv`, только нужные здесь колонки по
/// имени: сторона — уже строка `bid`/`ask` (`side_name`, `levels.rs`), тот
/// же алфавит, что ждёт `lob backtest --signals-csv`.
#[derive(Debug, serde::Deserialize)]
struct LevelsRowForSignals {
    side: String,
    birth_ms: i64,
    price_tick: i64,
}

/// Сигналы `lob backtest` (`profile_id,side,birth_ms`) из уже написанного
/// `levels-floor-<SYMBOL>.csv` — один `profile_id` на весь символ
/// (`debug:<symbol>`): разметка по семи осям — таск 12/16, не эта команда,
/// а цель здесь — доказать, что цепочка `profiles → backtest` собирается и
/// проходит, не назначить вердиктный профиль. Возвращает `price_tick`
/// последней строки (нужен для `order_size_22a` — «последняя цена» без
/// второго реплея) или `None`, если строк не было (нечего торговать —
/// вызывающий обязан пропустить бэктест этого символа, не считать дефектом).
pub(super) fn write_signals_csv_from_levels(
    levels_csv: &Path,
    signals_csv: &Path,
    profile_id: &str,
) -> anyhow::Result<Option<i64>> {
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_path(levels_csv)
        .map_err(|e| anyhow::anyhow!("{}: {e}", levels_csv.display()))?;
    let mut w = csv::Writer::from_path(signals_csv)
        .map_err(|e| anyhow::anyhow!("{}: {e}", signals_csv.display()))?;
    w.write_record(["profile_id", "side", "birth_ms"])?;
    let mut last_price_tick = None;
    for row in r.deserialize::<LevelsRowForSignals>() {
        let row = row.map_err(|e| anyhow::anyhow!("{}: {e}", levels_csv.display()))?;
        w.write_record([profile_id, &row.side, &row.birth_ms.to_string()])?;
        last_price_tick = Some(row.price_tick);
    }
    w.flush()?;
    Ok(last_price_tick)
}

/// Поля `instruments.csv` пула, нужные `order_size_22a` (Decision 22): лот и
/// шаг лота площадки, минимальный чек. Читает по имени колонки через
/// `pick::instruments_csv_reader` — единственный терпимый к `#`-баннеру
/// ридер этого файла (`interfaces.md`, «Из таска 08»).
fn read_lot_fields(instruments_csv: &Path, symbol: &str) -> anyhow::Result<(i64, i64, i64)> {
    #[derive(Debug, serde::Deserialize)]
    struct Row {
        symbol: String,
        min_order_qty: String,
        qty_step: String,
        min_notional_value: String,
    }
    let mut r = crate::commands::lob::pick::instruments_csv_reader(instruments_csv)
        .map_err(|e| anyhow::anyhow!("{}: {e}", instruments_csv.display()))?;
    for row in r.deserialize::<Row>() {
        let row = row.map_err(|e| anyhow::anyhow!("{}: {e}", instruments_csv.display()))?;
        if row.symbol != symbol {
            continue;
        }
        let min_order_qty_e9 = parse_e9(&row.min_order_qty)
            .ok_or_else(|| anyhow::anyhow!("{symbol}: min_order_qty не разобрался"))?;
        let qty_step_e9 = parse_e9(&row.qty_step)
            .ok_or_else(|| anyhow::anyhow!("{symbol}: qty_step не разобрался"))?;
        let min_notional_value_e9 = parse_e9(&row.min_notional_value)
            .ok_or_else(|| anyhow::anyhow!("{symbol}: min_notional_value не разобрался"))?;
        return Ok((min_order_qty_e9, qty_step_e9, min_notional_value_e9));
    }
    anyhow::bail!("{symbol}: нет в {}", instruments_csv.display())
}

/// Лот `lob backtest --order-qty-e9` (Decision 22а): `order_size_22a` от
/// полей `instruments.csv` пула и последней цены из `levels-floor` (в
/// тиках — `tick_e9` от `load_steps_for_symbol`, то же измерение, что
/// `record.rs` использует для шагов записи, не второй реплей ради цены).
pub(super) fn compute_order_qty_e9(
    instruments_csv: &Path,
    symbol: &str,
    last_price_tick: i64,
) -> anyhow::Result<i64> {
    let (tick_e9, _step_e9) =
        crate::commands::record::load_steps_for_symbol(instruments_csv, symbol)
            .map_err(|e| anyhow::anyhow!("{symbol}: шаги: {e:?}"))?;
    let (min_order_qty_e9, qty_step_e9, min_notional_value_e9) =
        read_lot_fields(instruments_csv, symbol)?;
    let last_price_e9 = last_price_tick.saturating_mul(tick_e9);
    Ok(order_size_22a(
        min_order_qty_e9,
        qty_step_e9,
        min_notional_value_e9,
        last_price_e9,
    ))
}

/// Круги RTT `probe-<SYMBOL>.csv` (`lob probe`) — колонка `rtt_ns` по имени.
#[derive(Debug, serde::Deserialize)]
struct ProbeCycleRow {
    rtt_ns: i64,
}

fn read_probe_rtts(path: &Path) -> Option<Vec<i64>> {
    let mut r = csv::Reader::from_path(path).ok()?;
    let vals: Vec<i64> = r
        .deserialize::<ProbeCycleRow>()
        .filter_map(Result::ok)
        .map(|row| row.rtt_ns)
        .collect();
    if vals.is_empty() {
        None
    } else {
        Some(vals)
    }
}

/// `clock.csv` (`bybit::clock`, `lob session`), колонка `bybit_rtt_ns`:
/// REST round-trip той же сессии, запасной источник, когда авторизованный
/// `lob probe` не гонялся (нужны ключи). Фильтр над общим `read_rows`
/// (таск 17 — не второй парсер того же файла), строки без замера (`None`,
/// сеть не ответила) отсутствуют в выдаче, а не превращаются в `0`.
fn read_clock_bybit_rtts(path: &Path) -> Option<Vec<i64>> {
    let rows = crate::bybit::clock::read_rows(path).ok()?;
    let vals: Vec<i64> = rows
        .into_iter()
        .filter_map(|row| row.bybit_rtt_ns)
        .collect();
    if vals.is_empty() {
        None
    } else {
        Some(vals)
    }
}

/// RTT `lob backtest --median-rtt-ns/--p95-rtt-ns` (D-RTT): `probe-<symbol>.
/// csv` сессии, если есть, иначе `clock.csv` той же сессии, иначе явные
/// флаги без умолчания — измеренное всегда предпочтено назначенному (§9
/// плана). Отказ без всех трёх — громкий, не молчаливая подстановка.
pub(super) fn resolve_backtest_rtt_ns(
    session_dir: &Path,
    symbol: &str,
    explicit_median_rtt_ns: Option<i64>,
    explicit_p95_rtt_ns: Option<i64>,
) -> anyhow::Result<(i64, i64, &'static str)> {
    if let Some(rtts) = read_probe_rtts(&session_dir.join(format!("probe-{symbol}.csv"))) {
        return Ok((percentile_ns(&rtts, 50), percentile_ns(&rtts, 95), "probe"));
    }
    if let Some(rtts) = read_clock_bybit_rtts(&session_dir.join("clock.csv")) {
        return Ok((percentile_ns(&rtts, 50), percentile_ns(&rtts, 95), "clock"));
    }
    match (explicit_median_rtt_ns, explicit_p95_rtt_ns) {
        (Some(m), Some(p)) => Ok((m, p, "flag")),
        _ => anyhow::bail!(
            "{symbol}: RTT не измерена (нет probe-{symbol}.csv/clock.csv в {}) — передайте --median-rtt-ns/--p95-rtt-ns",
            session_dir.display()
        ),
    }
}
