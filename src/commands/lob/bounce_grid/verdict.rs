//! K1 по суткам (В-177/В-181): вердикт гейта порчи из `verify-gate-v177-*.csv` (`sym,day,…,verdict,reason`).

use std::collections::BTreeMap;
use std::path::Path;

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
