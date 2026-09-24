//! Путь сравнения с таблицей профилей (таск 10): CSV сигналов
//! (`SignalRow`/`read_signals`, Decision 14) и таблица `docs/findings/
//! profiles-<дата>.csv` для сравнения `net_fill` (`ProfilesTableRow`/
//! `parse_measured_bps`/`read_table`, `NOT_MEASURED`). Вынесено из
//! `backtest` при разрезке B3 (ревью 23.09), поведение не менялось.

use std::collections::BTreeMap;
use std::path::Path;

use crate::lob::backtest::{Signal, TableEstimate, SIGMA_LONG, SIGMA_SHORT};

// ---------------------------------------------------------------------------
// CSV сигналов и таблицы профилей.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, serde::Deserialize)]
struct SignalRow {
    profile_id: String,
    side: String,
    birth_ms: i64,
}

pub(super) fn read_signals(path: &Path) -> anyhow::Result<BTreeMap<String, Vec<Signal>>> {
    let mut r = csv::Reader::from_path(path)
        .map_err(|e| anyhow::anyhow!("сигналы {} не читаются: {e}", path.display()))?;
    let mut out: BTreeMap<String, Vec<Signal>> = BTreeMap::new();
    for row in r.deserialize::<SignalRow>() {
        let row = row.map_err(|e| anyhow::anyhow!("строка сигналов {}: {e}", path.display()))?;
        let sigma = match row.side.as_str() {
            "bid" => SIGMA_SHORT,
            "ask" => SIGMA_LONG,
            other => anyhow::bail!("сторона сигнала обязана быть bid|ask, получено {other}"),
        };
        out.entry(row.profile_id).or_default().push(Signal {
            t0_ns: row.birth_ms.saturating_mul(1_000_000),
            sigma,
        });
    }
    Ok(out)
}

/// Литерал таска 10 (`profiles.rs::NOT_MEASURED`): колонка измерялась, но
/// источник явно не считал `fill`/`net_fill` (`fill_model=none`) — отличать
/// от `none` (нет данных даже при активной модели).
const NOT_MEASURED: &str = "not_measured";

/// Строка `docs/findings/profiles-<дата>.csv` (таск 10, `commands::lob::
/// profiles`), только нужные этому файлу колонки — `csv`+`serde` матчит по
/// имени заголовка, лишние 22 колонки (`n`, доли исходов, горизонты,
/// `session_start_hours_utc`, …) не мешают. Числовые колонки читаются
/// строкой: `none`/`not_measured` — не `f64`, а два разных отсутствия.
#[derive(Debug, Clone, serde::Deserialize)]
struct ProfilesTableRow {
    profile_id: String,
    net_bps: String,
    net_fill: String,
}

/// `none` → нет числа; `not_measured` → тоже нет числа, но по другой причине
/// (см. `ProfilesTableRow`); иначе — разобранное значение.
fn parse_measured_bps(raw: &str) -> Option<f64> {
    match raw {
        "none" | NOT_MEASURED => None,
        s => s.parse::<f64>().ok(),
    }
}

/// Формат таска 10: шапка — комментарий `# lob profiles: …`, которую
/// `.comment(Some(b'#'))` пропускает целиком (`pick::table::
/// instruments_csv_reader` — тот же приём); эта функция не разбирает её
/// ключи (`fill_model=…`) — то, что нужно, уже несёт каждая строка своим
/// литералом `not_measured`.
pub(super) fn read_table(path: Option<&Path>) -> anyhow::Result<BTreeMap<String, TableEstimate>> {
    let Some(path) = path else {
        return Ok(BTreeMap::new());
    };
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_path(path)
        .map_err(|e| anyhow::anyhow!("таблица профилей {} не читается: {e}", path.display()))?;
    let mut out = BTreeMap::new();
    for row in r.deserialize::<ProfilesTableRow>() {
        let row =
            row.map_err(|e| anyhow::anyhow!("строка таблицы профилей {}: {e}", path.display()))?;
        out.insert(
            row.profile_id,
            TableEstimate {
                net_bps: parse_measured_bps(&row.net_bps),
                net_fill_bps: parse_measured_bps(&row.net_fill),
                net_fill_not_measured: row.net_fill == NOT_MEASURED,
            },
        );
    }
    Ok(out)
}
