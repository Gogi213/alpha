//! `lob cold ensure` (КТ-5, TK-150): подготовка двоичного кэша суток в `<cold-root>/<ключ>/`.
//!
//! Ключ = версия формата кодеков + версия алгоритма подготовки + хеш параметров + сутки; идентификатора сборки в нём
//! нет (В-213), поэтому пересборка кэш не обнуляет. Повтор с теми же входами ничего не пишет: годные `.abin` читаются.

use std::path::{Path, PathBuf};

use super::bincache::bin_path;
use super::{abin, tbin};

/// Версия алгоритма подготовки подходов/касаний; растёт, когда меняется их расчёт (старый кэш уходит из ключа).
const ALGO_VERSION: u32 = 1;

#[derive(Debug, clap::Args)]
pub struct EnsureArgs {
    /// Каталог с `touches-<SYM>.csv` / `approaches-<SYM>.csv`: `<dir>/<сутки>/…` или плоско.
    #[arg(long)]
    pub touches_from: PathBuf,
    /// Сутки `YYYY-MM-DD`.
    #[arg(long)]
    pub day: String,
    /// Параметры расчёта подходов/касаний одной строкой (входят в ключ).
    #[arg(long)]
    pub params: String,
    /// Корень холодного кэша.
    #[arg(long, default_value = "/data/cold")]
    pub cold_root: PathBuf,
}

#[derive(Debug, PartialEq, Eq)]
pub struct EnsureSummary {
    pub dir: PathBuf,
    pub files: usize,
    pub written: usize,
}

fn fnv1a64(s: &str) -> u64 {
    s.bytes().fold(14_695_981_039_346_656_037, |h, b| {
        (h ^ u64::from(b)).wrapping_mul(1_099_511_628_211)
    })
}

/// Ключ каталога кэша: формат · алгоритм · параметры · сутки.
pub fn cold_key(params: &str, day: &str) -> String {
    let fmt = |m: &[u8; 4]| String::from_utf8_lossy(m).to_lowercase();
    format!(
        "{}-{}-a{ALGO_VERSION}-{:016x}-{day}",
        fmt(abin::MAGIC),
        fmt(tbin::MAGIC),
        fnv1a64(params)
    )
}

fn csvs(dir: &Path) -> anyhow::Result<Vec<PathBuf>> {
    let mut v: Vec<PathBuf> = std::fs::read_dir(dir)?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| {
            let n = p.file_name().and_then(|n| n.to_str()).unwrap_or("");
            n.ends_with(".csv") && (n.starts_with("touches-") || n.starts_with("approaches-"))
        })
        .collect();
    v.sort();
    Ok(v)
}

/// Кэширует все `touches-*`/`approaches-*` суток; нет ни одного файла — ошибка (fail-closed).
pub fn run_ensure(a: &EnsureArgs) -> anyhow::Result<EnsureSummary> {
    let dir = a.cold_root.join(cold_key(&a.params, &a.day));
    let src = Some(a.touches_from.join(&a.day))
        .filter(|d| d.is_dir())
        .unwrap_or_else(|| a.touches_from.clone());
    let files = csvs(&src)?;
    anyhow::ensure!(
        !files.is_empty(),
        "cold ensure: в {} нет touches-*/approaches-*.csv",
        src.display()
    );
    // `bin_path` читает каталог кэша из окружения; команда однопоточна на этом шаге.
    std::env::set_var("ALPHA_APPROACH_BIN_DIR", &dir);
    let mut written = 0;
    for csv in &files {
        let bin = bin_path(csv).expect("каталог кэша задан");
        let before = std::fs::metadata(&bin).and_then(|m| m.modified()).ok();
        let rows = if csv
            .file_name()
            .and_then(|n| n.to_str())
            .is_some_and(|n| n.starts_with("touches-"))
        {
            tbin::read_touches_cached(csv)?.len()
        } else {
            abin::read_approaches_cached(csv)?.len()
        };
        let after = std::fs::metadata(&bin).and_then(|m| m.modified()).ok();
        // Файл без строк (одна шапка) кодеки не кэшируют: читать там нечего.
        anyhow::ensure!(
            after.is_some() || rows == 0,
            "cold ensure: кэш не записан для {}",
            csv.display()
        );
        written += usize::from(before != after);
    }
    Ok(EnsureSummary {
        dir,
        files: files.len(),
        written,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn key_has_no_build_id_and_depends_on_params_and_day() {
        let k = cold_key("h3=10000", "2026-01-01");
        assert!(k.starts_with("abn2-tbn1-a1-") && k.ends_with("-2026-01-01"));
        assert_eq!(k, cold_key("h3=10000", "2026-01-01"));
        assert_ne!(k, cold_key("h3=20000", "2026-01-01"));
        assert_ne!(k, cold_key("h3=10000", "2026-01-02"));
    }

    #[test]
    fn empty_day_dir_is_error() {
        let d = tempfile::tempdir().unwrap();
        let a = EnsureArgs {
            touches_from: d.path().to_path_buf(),
            day: "2026-01-01".into(),
            params: "p".into(),
            cold_root: d.path().join("cold"),
        };
        assert!(run_ensure(&a).is_err());
    }
}
