//! Печать итога `lob verify` поверх `crate::bybit::verify`.
//!
//! Сама сверка (шаг 0.6: инварианты и сделки в диапазоне книги) реализована
//! в `crate::bybit::verify` — не здесь. Сверка с REST по `u` — только живой
//! поток (у файла нет `u`), см. doc `bybit::verify`.

use crate::bybit::verify::{run_verify, VerifyArgs};

pub(super) fn print_summary(args: &VerifyArgs) -> anyhow::Result<()> {
    let summary = run_verify(args)?;
    println!(
        "verify: files={} updates={} gaps={} invariants={} trades={} out_of_range={} violations={} indeterminate={}",
        summary.files,
        summary.updates_applied,
        summary.sequence_gaps,
        summary.invariant_violations,
        summary.trades_total,
        summary.trades_out_of_range,
        summary.trades_violations,
        summary.trades_indeterminate,
    );
    Ok(())
}
