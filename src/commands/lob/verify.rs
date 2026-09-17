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
//! (`bybit::verify::verify_file`), а вердикт — по сумме частей символа и
//! строкам `gaps.csv` того же каталога (V4, 2026-09-17): разрыв `u` в файле
//! не виден (поля `u` в бинлоге нет), поэтому единственный его след —
//! журнал потерь записи. Целостность (`gaps`, инварианты книги) ровно ноль,
//! доля нарушений теста 3 меньше 0.1 % сделок (план §11, В-56) — вердикт
//! `VerifyStatus::of_with_gap_rows`.

use std::path::{Path, PathBuf};

use crate::bybit::verify::{verify_file, VerifyArgs, VerifySummary};
use crate::commands::record::{gaps_csv_path, read_gap_rows};

/// Вердикт маркера сверки — то единственное слово, что лежит в
/// `verify-<SYMBOL>.status` и что печатает `lob verify`.
///
/// «Прошла сверку целиком» (`interfaces.md`, doc `watch.rs`) — по тому,
/// что файловый режим вообще может проверить (сверка по `u` — только
/// живой поток, здесь её нет): разрывов нет, инварианты книги целы
/// (тест 2) и доля сделок по цене, которой книга не держала ни мгновения,
/// меньше 0.1 % (тест 3, план §11, В-56).
/// `trades_out_of_range`/`trades_indeterminate` — отдельные, не булевы
/// метрики, в вердикт не входят (та же трактовка, что `day_tallies` в
/// `mod.rs`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum VerifyStatus {
    Ok,
    Fail,
}

/// Порог доли нарушений теста 3 — **0.1 % сделок** (план §11, В-56).
///
/// Нарушение теста 3 («цена ни разу не держалась») — свойство
/// сэмплированного фида: `.50` публикуется пачками ~20 мс, внутри окна
/// заявки появляются и исчезают (замер: `docs/findings/hft-underground-2026-09-16.md`,
/// §12d/§12e).
/// Поэтому поштучный ноль тут — не критерий целостности, а недостижимое
/// на рынке требование; доля и есть порог плана. Знаменатель: доля
/// меньше `1 / VIOLATION_SHARE_DENOM`.
pub(crate) const VIOLATION_SHARE_DENOM: u64 = 1000;

impl VerifyStatus {
    /// Вердикт: целостность — ровно ноль (`sequence_gaps`, инварианты книги),
    /// тест 3 — **доля** нарушений меньше 0.1 % сделок (план §11, В-56).
    ///
    /// Сравнение целочисленное (`violations * 1000 < trades`) — без `f64` и
    /// без округления: ровно 0.1 % это уже `fail`. Сделок нет — нарушать
    /// нечего, тест 3 пройден.
    pub(crate) fn of(summary: &VerifySummary) -> Self {
        let integrity_clean = summary.sequence_gaps == 0 && summary.invariant_violations == 0;
        let trades_clean = summary.trades_total == 0
            || summary.trades_violations * VIOLATION_SHARE_DENOM < summary.trades_total;
        if integrity_clean && trades_clean {
            Self::Ok
        } else {
            Self::Fail
        }
    }

    /// Вердикт по сводке **и** журналу потерь (V4, 2026-09-17).
    ///
    /// `sequence_gaps` файлового режима всегда ноль — в бинлоге нет `u`, —
    /// поэтому разрыв, пережитый записью, виден только строкой `gaps.csv`.
    /// Одна такая строка по символу — это шов в данных: вердикт `fail`, чем бы
    /// ни была чиста сводка; иначе символ с ресинками проходил `ok` и все
    /// читатели (fail-closed) брали сутки как проверенные.
    pub(crate) fn of_with_gap_rows(summary: &VerifySummary, gap_rows: usize) -> Self {
        if gap_rows > 0 {
            Self::Fail
        } else {
            Self::of(summary)
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
    /// Строк `gaps.csv` этого символа в каталоге (V4, 2026-09-17) — та
    /// единственная часть вердикта, которой в бинлоге нет.
    pub gap_rows: usize,
}

/// Путь маркера сверки символа в каталоге — единственное место, где это
/// имя собирается для записи (читатели держат своё, по контракту таска 07).
pub(crate) fn marker_path(dir: &Path, symbol: &str) -> PathBuf {
    dir.join(format!("verify-{symbol}.status"))
}

/// Строк `gaps.csv` этого символа в каталоге записи (V4, 2026-09-17).
///
/// `VerifySummary::sequence_gaps` в файловом режиме всегда ноль — в бинлоге
/// нет поля `u`, и по файлу разрыв неотличим от честной дельты. Настоящие
/// разрывы видны **только** в `gaps.csv`, который запись ведёт рядом. Нет
/// файла — ноль строк: каталог из одних бинлогов (например
/// `/opt/alpha/verify/<день>` у ночной сверки) журнала потерь не несёт, и
/// выдумывать за него нечего.
fn gap_rows_for(root: &Path, symbol: &str) -> usize {
    read_gap_rows(&gaps_csv_path(root))
        .map(|rows| rows.iter().filter(|r| r.symbol == symbol).count())
        .unwrap_or(0)
}

/// Сверяет все части символа в `root` (`super::session_binlog_for`) и
/// пишет маркер `verify-<SYMBOL>.status` в `marker_dir` — **единственная**
/// функция записи маркера. Вердикт — по сумме частей **и** строкам
/// `gaps.csv` каталога (V4): одна битая часть или один разрыв `u`,
/// пережитый записью, роняют каталог в `fail` (fail-closed).
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
        total.violations_block += summary.violations_block;
        total.violations_rpi += summary.violations_rpi;
        total.violations_inside_spread += summary.violations_inside_spread;
        total.violations_adjacent += summary.violations_adjacent;
        total.violations_far += summary.violations_far;
        total.violations_no_side += summary.violations_no_side;
        total.violations_stale_20ms += summary.violations_stale_20ms;
        total.violations_stale_100ms += summary.violations_stale_100ms;
        parts.push(PartVerify {
            path,
            day_utc,
            part,
            summary,
        });
    }
    let gap_rows = gap_rows_for(root, symbol);
    let status = VerifyStatus::of_with_gap_rows(&total, gap_rows);
    let marker = marker_path(marker_dir, symbol);
    std::fs::write(&marker, status.as_str())
        .map_err(|e| anyhow::anyhow!("маркер {}: {e}", marker.display()))?;
    Ok(VerifyReport {
        parts,
        total,
        status,
        marker,
        gap_rows,
    })
}

/// Одна строка сводки — та же для stdout `lob verify` и для
/// `pilot::InstrumentMetrics::verify_summary_line`.
pub(crate) fn format_summary(s: &VerifySummary) -> String {
    format!(
        "files={} updates={} gaps={} invariants={} trades={} out_of_range={} violations={} \
         (block={} rpi={} in_spread={} adj1={} far={} no_side={} stale20={} stale100={}) \
         indeterminate={}",
        s.files,
        s.updates_applied,
        s.sequence_gaps,
        s.invariant_violations,
        s.trades_total,
        s.trades_out_of_range,
        s.trades_violations,
        s.violations_block,
        s.violations_rpi,
        s.violations_inside_spread,
        s.violations_adjacent,
        s.violations_far,
        s.violations_no_side,
        s.violations_stale_20ms,
        s.violations_stale_100ms,
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
        // `gaps_csv` — сколько строк журнала потерь пришлось на символ (V4):
        // вердикт рубится и по ним, поэтому число печатается рядом с ним, а не
        // ищется читателем в `gaps.csv` отдельно.
        "verify: status={} gaps_csv={} marker={}",
        report.status.as_str(),
        report.gap_rows,
        report.marker.display()
    );
    Ok(())
}

#[cfg(test)]
mod tests;
