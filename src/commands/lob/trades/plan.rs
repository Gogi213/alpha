//! Разбор и ранняя валидация аргументов `lob trades` (T1, П-02): маркер
//! сверки (К1, как у `touches`), список бинлогов символа в хронологическом
//! порядке с их сутками (резолвер — тот же `session_binlog_for`, что у
//! `touches`; сутки — из имени файла тем же разбором, что использует реплей,
//! `parts::day_of_filename`) и шаг цены заголовка — до единого байта чтения
//! кадров.

use std::path::PathBuf;

use super::TradesArgs;

/// Файлы символа, отфильтрованные по `--day` (пусто — все сутки), парой с
/// их сутками, и шаг цены заголовка (общий на сессию — читается один раз с
/// первого файла, до фильтра, как у `touches::plan`).
pub(super) struct ResolvedPlan {
    pub files: Vec<(PathBuf, String)>,
    pub tick_e9: i64,
}

pub(super) fn resolve(args: &TradesArgs) -> anyhow::Result<ResolvedPlan> {
    crate::commands::lob::require_verified(&args.root, &args.symbol, args.allow_unverified)?;
    let paths = crate::commands::lob::session_binlog_for(&args.root, &args.symbol)?;
    anyhow::ensure!(
        !paths.is_empty(),
        "{}: нет суточных файлов {}",
        args.root.display(),
        args.symbol
    );
    let (tick_e9, _step_e9) = crate::commands::lob::backtest::read_tick_step(&paths[0])?;

    let prefix = format!("{}-", args.symbol);
    let mut files = Vec::with_capacity(paths.len());
    for path in paths {
        let name = path
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        let day = crate::commands::lob::parts::day_of_filename(&prefix, &name)
            .ok_or_else(|| anyhow::anyhow!("имя {name} не разбирается как сутки"))?;
        if args.days.is_empty() || args.days.iter().any(|d| d == &day) {
            files.push((path, day));
        }
    }
    Ok(ResolvedPlan { files, tick_e9 })
}
