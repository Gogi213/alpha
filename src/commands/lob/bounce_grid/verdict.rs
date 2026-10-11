//! K1 по суткам (В-177/В-181): вердикт гейта порчи из `verify-gate-v177-*.csv` (`sym,day,…,verdict,reason`).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use super::args::BounceGridArgs;
use crate::commands::lob::read_verify_marker;

/// Маркер сверки символа в корне записи: `verify-<SYMBOL>.status`.
pub(super) fn marker_path(root: &Path, symbol: &str) -> PathBuf {
    root.join(format!("verify-{symbol}.status"))
}

/// Маркер символа — `ok`.
pub(super) fn marker_ok(root: &Path, symbol: &str) -> bool {
    read_verify_marker(&marker_path(root, symbol))
}

/// Единственный K1-гейт символа (fail-closed): что можно считать.
pub(super) enum K1 {
    /// Маркер `ok` или `--allow-unverified`: все сутки записи.
    Pass,
    /// Маркер не `ok`: символ пропускается (путь — в сообщение).
    Refused(PathBuf),
    /// `--verdict-csv` (В-181): вместо маркера — вердикт гейта на каждые сутки.
    Days(BTreeMap<String, (bool, String)>),
}

pub(super) fn gate(args: &BounceGridArgs, symbol: &str) -> anyhow::Result<K1> {
    if let Some(vp) = args.verdict_csv.as_deref() {
        return read_day_verdicts(vp, symbol).map(K1::Days);
    }
    if args.allow_unverified || marker_ok(&args.root, symbol) {
        return Ok(K1::Pass);
    }
    Ok(K1::Refused(marker_path(&args.root, symbol)))
}

/// Оставляет в `parts_by_day` только сутки с вердиктом «пускаем»; без строки вердикта — отказ с причиной.
/// Возвращает число отброшенных суток.
pub(super) fn retain_allowed<T>(
    parts_by_day: &mut BTreeMap<String, T>,
    verdicts: &BTreeMap<String, (bool, String)>,
    symbol: &str,
    csv: &Path,
) -> usize {
    let before = parts_by_day.len();
    parts_by_day.retain(|day, _| match verdicts.get(day) {
        Some((true, _)) => true,
        Some((false, why)) => {
            eprintln!("bounce-grid: {symbol} {day} — отказ вердикта: {why}");
            false
        }
        None => {
            eprintln!(
                "bounce-grid: {symbol} {day} — отказ: суток нет в вердикте {}",
                csv.display()
            );
            false
        }
    });
    before - parts_by_day.len()
}

/// Сутки символа → (пускаем?, причина). Строки других символов пропускаются.
pub(super) fn read_day_verdicts(
    path: &Path,
    symbol: &str,
) -> anyhow::Result<BTreeMap<String, (bool, String)>> {
    let text = std::fs::read_to_string(path)
        .map_err(|e| anyhow::anyhow!("--verdict-csv {}: {e}", path.display()))?;
    let mut lines = text.lines();
    let header: Vec<&str> = lines.next().unwrap_or("").split(',').collect();
    let col = |name: &str| {
        header.iter().position(|h| *h == name).ok_or_else(|| {
            anyhow::anyhow!("--verdict-csv {}: нет колонки `{name}`", path.display())
        })
    };
    let (i_sym, i_day, i_verdict, i_reason) =
        (col("sym")?, col("day")?, col("verdict")?, col("reason")?);
    let mut out = BTreeMap::new();
    for line in lines {
        let f: Vec<&str> = line.split(',').collect();
        if f.get(i_sym) != Some(&symbol) {
            continue;
        }
        let (Some(day), Some(v), Some(why)) = (f.get(i_day), f.get(i_verdict), f.get(i_reason))
        else {
            anyhow::bail!("--verdict-csv {}: короткая строка `{line}`", path.display());
        };
        out.insert((*day).to_string(), (*v == "пускаем", (*why).to_string()));
    }
    Ok(out)
}
