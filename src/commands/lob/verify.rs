//! `lob verify` — сверка записи символа по частям и маркер сверки сессии.
//!
//! Сама сверка (шаг 0.6: инварианты и сделки в диапазоне книги) реализована
//! в `crate::bybit::verify` — не здесь. Сверка с REST по `u` — только живой
//! поток (у файла нет `u`), см. doc `bybit::verify`; её артефакт `verify.csv`
//! пишет сайдкар записи (`bybit::verify_sidecar`), не эта команда.
//!
//! Таск 26: маркер сверки `<dir>/verify-<SYMBOL>.status` (`ok`/`fail`) —
//! то, что читают `lob profiles`/`lob watch` (fail-closed, R44), — пишет
//! **одна** функция `verify_and_mark` ниже; `lob verify` зовёт её напрямую,
//! `pilot::process_instrument` — её же, не свою копию. Части символа
//! (`<SYMBOL>-<день>[-pN].binlog`, таски 19/22) находит общий резолвер
//! `super::session_binlog_for`; сверка идёт по каждой части отдельно
//! (`bybit::verify::verify_file`), и маркер `ok` только когда чиста каждая.

use std::path::{Path, PathBuf};

use crate::bybit::verify::{verify_file, VerifyArgs, VerifySummary};

/// Вердикт маркера сверки — то единственное слово, что лежит в
/// `verify-<SYMBOL>.status` и что печатает `lob verify`.
///
/// «Прошла сверку целиком» (`interfaces.md`, doc `watch.rs`) — по тому,
/// что файловый режим вообще может проверить (сверка по `u` — только
/// живой поток, здесь её нет): разрывов нет, инварианты книги целы
/// (тест 2) и цена сделки хоть раз держалась (тест 3).
/// `trades_out_of_range`/`trades_indeterminate` — отдельные, не булевы
/// метрики, в вердикт не входят (та же трактовка, что `day_tallies` в
/// `mod.rs`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum VerifyStatus {
    Ok,
    Fail,
}

impl VerifyStatus {
    pub(crate) fn of(summary: &VerifySummary) -> Self {
        if summary.sequence_gaps == 0
            && summary.invariant_violations == 0
            && summary.trades_violations == 0
        {
            Self::Ok
        } else {
            Self::Fail
        }
    }

    /// Содержимое файла-маркера, ровно одно слово без перевода строки —
    /// `profiles.rs::read_verify_marker`/`watch.rs` сравнивают с `ok`.
    pub(crate) fn as_str(self) -> &'static str {
        match self {
            Self::Ok => "ok",
            Self::Fail => "fail",
        }
    }

    pub(crate) fn is_ok(self) -> bool {
        matches!(self, Self::Ok)
    }
}

/// Сводка сверки одной части записи символа.
#[derive(Debug, Clone)]
pub(crate) struct PartVerify {
    pub path: PathBuf,
    /// Сутки части из имени файла, `YYYY-MM-DD`.
    pub day_utc: String,
    pub part: u32,
    pub summary: VerifySummary,
}

/// Итог `verify_and_mark`: по части, суммарно, вердикт и куда лёг маркер.
#[derive(Debug, Clone)]
pub(crate) struct VerifyReport {
    pub parts: Vec<PartVerify>,
    pub total: VerifySummary,
    pub status: VerifyStatus,
    pub marker: PathBuf,
}

/// Путь маркера сверки символа в каталоге — единственное место, где это
/// имя собирается для записи (читатели держат своё, по контракту таска 07).
pub(crate) fn marker_path(dir: &Path, symbol: &str) -> PathBuf {
    dir.join(format!("verify-{symbol}.status"))
}

/// Сверяет все части символа в `root` (`super::session_binlog_for`) и
/// пишет маркер `verify-<SYMBOL>.status` в `marker_dir` — **единственная**
/// функция записи маркера. Вердикт — `VerifyStatus::of` по сумме частей:
/// одна битая часть роняет весь каталог в `fail` (fail-closed).
pub(crate) fn verify_and_mark(
    root: &Path,
    marker_dir: &Path,
    symbol: &str,
) -> anyhow::Result<VerifyReport> {
    let prefix = format!("{symbol}-");
    let mut parts = Vec::new();
    let mut total = VerifySummary::default();
    for path in super::session_binlog_for(root, symbol)? {
        let name = path
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        let (day_utc, part) = super::file_order_key(&prefix, &name);
        let summary = verify_file(&path)?;
        total.files += summary.files;
        total.updates_applied += summary.updates_applied;
        total.sequence_gaps += summary.sequence_gaps;
        total.invariant_violations += summary.invariant_violations;
        total.trades_total += summary.trades_total;
        total.trades_out_of_range += summary.trades_out_of_range;
        total.trades_violations += summary.trades_violations;
        total.trades_indeterminate += summary.trades_indeterminate;
        parts.push(PartVerify {
            path,
            day_utc,
            part,
            summary,
        });
    }
    let status = VerifyStatus::of(&total);
    let marker = marker_path(marker_dir, symbol);
    std::fs::write(&marker, status.as_str())
        .map_err(|e| anyhow::anyhow!("маркер {}: {e}", marker.display()))?;
    Ok(VerifyReport {
        parts,
        total,
        status,
        marker,
    })
}

/// Одна строка сводки — та же для stdout `lob verify` и для
/// `pilot::InstrumentMetrics::verify_summary_line`.
pub(crate) fn format_summary(s: &VerifySummary) -> String {
    format!(
        "files={} updates={} gaps={} invariants={} trades={} out_of_range={} violations={} indeterminate={}",
        s.files,
        s.updates_applied,
        s.sequence_gaps,
        s.invariant_violations,
        s.trades_total,
        s.trades_out_of_range,
        s.trades_violations,
        s.trades_indeterminate,
    )
}

/// `lob verify --symbol S --root DIR`: строка на часть с сутками, сумма,
/// вердикт и путь маркера. Маркер ложится в тот же `--root`.
pub(super) fn print_summary(args: &VerifyArgs) -> anyhow::Result<()> {
    let report = verify_and_mark(&args.root, &args.root, &args.symbol)?;
    for p in &report.parts {
        let name = p
            .path
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        println!(
            "verify: part={name} day={} part_no={} {}",
            p.day_utc,
            p.part,
            format_summary(&p.summary)
        );
    }
    println!("verify: {}", format_summary(&report.total));
    println!(
        "verify: status={} marker={}",
        report.status.as_str(),
        report.marker.display()
    );
    Ok(())
}

#[cfg(test)]
mod tests;
