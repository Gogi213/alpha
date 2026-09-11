//! Печать итога `lob record` поверх `crate::commands::record`.
//!
//! Сама запись (шаг 0.3, Decision 7/23) реализована в соседнем модуле
//! `crate::commands::record` — не здесь. Имя этого файла совпадает с именем
//! того модуля не случайно: `commands::record` — реализация записи,
//! `commands::lob::record` — её печать из-под `lob record`, две разные вещи
//! под похожими путями (`commands::record::RecordArgs` — аргументы записи,
//! этот файл только форматирует итог для stdout).

use crate::commands::record::{run_record, RecordArgs};

pub(super) fn print_summary(args: &RecordArgs) -> anyhow::Result<()> {
    let summary = run_record(args)?;
    println!(
        "record: {} records={} files={} gaps={} stop={:?}",
        summary.symbol,
        summary.records_total,
        summary.files.len(),
        summary.gaps.len(),
        summary.stop_reason
    );
    Ok(())
}
