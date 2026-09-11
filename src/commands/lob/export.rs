//! Печать итога `lob export` поверх `crate::lob::export`.
//!
//! Сам экспорт в `npy` для крейта `hftbacktest` (шаг 6.1, Decision 17)
//! реализован в `crate::lob::export` — не здесь.

use crate::lob::export::{run_export, ExportArgs};

pub(super) fn print_summary(args: &ExportArgs) -> anyhow::Result<()> {
    let summary = run_export(args)?;
    println!(
        "export: files={} events={} defective={} out={}",
        summary.files,
        summary.events,
        summary.defective,
        summary.out.display()
    );
    Ok(())
}
