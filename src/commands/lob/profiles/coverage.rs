//! Пул и покрытие: символы пула из `instruments.csv` корня и
//! `coverage_top50_bps` на каждый из них из `candidates.csv` — вход
//! `shortlist::build_profile_grid`. Два чтения CSV, отдельные от реплея
//! и накопления.

use std::collections::HashMap;
use std::path::Path;

use crate::commands::lob::pick::instruments_csv_reader;
use crate::lob::shortlist::InstrumentCoverage;

// ---------------------------------------------------------------------------
// Пул и покрытие.
// ---------------------------------------------------------------------------

#[derive(Debug, serde::Deserialize)]
struct PoolSymbolRow {
    symbol: String,
}

/// Символы пула из `instruments.csv` корня (таск 08) — единственный читатель
/// `pick::instruments_csv_reader`, терпит метку `debug` первой строкой.
pub(super) fn read_pool_symbols(instruments_csv: &Path) -> anyhow::Result<Vec<String>> {
    let mut r = instruments_csv_reader(instruments_csv).map_err(|e| {
        anyhow::anyhow!(
            "{}: {e} — lob profiles читает пул из instruments.csv",
            instruments_csv.display()
        )
    })?;
    let mut out = Vec::new();
    for row in r.deserialize::<PoolSymbolRow>() {
        out.push(row?.symbol);
    }
    anyhow::ensure!(!out.is_empty(), "{}: пул пуст", instruments_csv.display());
    out.sort();
    Ok(out)
}

#[derive(Debug, serde::Deserialize)]
struct CoverageRow {
    symbol: String,
    coverage_top50_bps: Option<f64>,
}

/// Покрытие топ-50 на символ пула из `candidates.csv` (таск 08): единственное
/// место, где это число персистентно (`instruments.csv` корня его не несёт —
/// `interfaces.md`, «Из таска 08»). Терпит метку `debug` первой строкой тем
/// же приёмом, что `pick::instruments_csv_reader`. Символ пула без строки
/// покрытия — громкая ошибка: сетку профилей без него строить нечестно
/// (изобретённое покрытие запрещено §9).
pub(super) fn read_coverage(
    candidates_csv: &Path,
    pool: &[String],
) -> anyhow::Result<Vec<InstrumentCoverage>> {
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_path(candidates_csv)
        .map_err(|e| anyhow::anyhow!("{}: {e}", candidates_csv.display()))?;
    let mut by_symbol: HashMap<String, f64> = HashMap::new();
    for row in r.deserialize::<CoverageRow>() {
        let row = row?;
        if let Some(c) = row.coverage_top50_bps {
            by_symbol.insert(row.symbol, c);
        }
    }
    pool.iter()
        .map(|s| {
            by_symbol
                .get(s)
                .copied()
                .map(|coverage_bps| InstrumentCoverage {
                    symbol: s.clone(),
                    coverage_bps,
                })
                .ok_or_else(|| {
                    anyhow::anyhow!(
                        "{}: нет покрытия top50 для {s} — lob pick обязан измерить пул до lob profiles",
                        candidates_csv.display()
                    )
                })
        })
        .collect()
}
