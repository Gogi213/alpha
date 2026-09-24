//! Разбор и ранняя валидация аргументов `lob touches` (W5а/в): режим `H3`,
//! полосы подхода, конфигурация трекера — одной функцией, до единого байта
//! реплея. Раньше проверки `--moves` (окно поиска задано и положительно) и
//! `--numbers` (режим несёт единый порог `H3`) читались только в глубине
//! `run_touches`, ПОСЛЕ полного реплея и записи `touches-<SYMBOL>.csv`:
//! отказ на пустом `--moves-window-ms` приходил уже после того, как реплей
//! и запись касаний отработали впустую. Здесь — до `resolve` возвращает
//! ошибку, до `run_touches` начинает реплей.

use crate::lob::levels::{H3Mode, LevelsConfig};

use super::TouchesArgs;

/// Результат разбора аргументов: полосы подхода и конфигурация трекера, уже
/// проверенные и готовые к реплею.
pub(super) struct ResolvedPlan {
    /// Полосы `--approach-bps`: первая — «привычная» (`approaches-<symbol>.csv`),
    /// каждая следующая — со своим суффиксом `-D<d>`. Пустой список — записей
    /// подхода нет вовсе.
    pub bands: Vec<i64>,
    pub cfg: LevelsConfig,
    /// Порог в лотах — только для осей «×H3» у `--numbers`; `None` у режимов
    /// В-61 (`notional`/`strength`/`both`), у них единого порога нет.
    pub h3_lots: Option<i64>,
}

/// Разбирает и проверяет аргументы `lob touches`, читает заголовок бинлога
/// и режим `H3`. Ошибка отсюда — до единого байта реплея (W5а).
pub(super) fn resolve(args: &TouchesArgs) -> anyhow::Result<ResolvedPlan> {
    crate::commands::lob::require_verified(&args.root, &args.symbol, args.allow_unverified)?;
    // Порог уровня — любой из режимов В-61 (`notional`/`strength`/`both`) или
    // прежние `floor`/`percentile` (E2 базы, В-64: оси «возраст» и «стек»
    // осмысленны только когда уровнем считается сильная плотность, а не любое
    // место стакана при `k = 1.0`); тик и шаг лота — из заголовка бинлога.
    let paths = crate::commands::lob::session_binlog_for(&args.root, &args.symbol)?;
    anyhow::ensure!(
        !paths.is_empty(),
        "{}: нет суточных файлов {}",
        args.root.display(),
        args.symbol
    );
    let (tick_e9, step_e9) = crate::commands::lob::backtest::read_tick_step(&paths[0])?;
    let mode = crate::commands::lob::resolve_h3_mode_full(
        &args.root,
        &args.symbol,
        &args.h3,
        args.h3_k,
        tick_e9,
        step_e9,
    )?;
    // Полосы подхода: первая — «привычная» (`approaches-<symbol>.csv`), каждая
    // следующая — со своим суффиксом `-D<d>` (момент взвода у полос разный, см.
    // doc флага `TouchesArgs::approach_bps`). Пустой список — записей подхода
    // нет вовсе.
    anyhow::ensure!(
        args.approach_bps.iter().all(|&d| d > 0),
        "--approach-bps обязан быть положителен"
    );
    let bands = args.approach_bps.clone();
    {
        let mut sorted = bands.clone();
        sorted.sort_unstable();
        anyhow::ensure!(
            sorted.windows(2).all(|w| w[0] != w[1]),
            "--approach-bps: полосы повторяются: {:?}",
            args.approach_bps
        );
    }
    let cfg = LevelsConfig {
        mode,
        warmup_ms: args.warmup_ms,
        repeat_window_ms: args.repeat_window_ms,
        approach_bps: bands.first().copied(),
        approach_min_age_ms: args.approach_min_age_secs.saturating_mul(1_000),
    };
    anyhow::ensure!(
        !args.carry_age || !matches!(mode, H3Mode::Percentile { .. }),
        "--carry-age: режим percentile с прогревом перенос возраста не принимает (прогрев — про порог)"
    );
    if bands.is_empty() {
        anyhow::ensure!(
            args.approach_min_age_secs == 0,
            "--approach-min-age-secs задан без --approach-bps: пола взвода нет, записи подхода тоже"
        );
    }
    // Порог в лотах — только для чисел практиков `--numbers` (оси «×H3»): у
    // режимов В-61 единого порога нет, и `--numbers` с ними — отказ.
    let h3_lots = mode.single_h3_lots();

    // W5а: обе проверки ниже раньше читались глубоко внутри `run_touches` —
    // `--moves` только после того, как реплей и запись `touches-<SYMBOL>.csv`
    // уже отработали, `--numbers` — в самом конце функции. Здесь — до реплея.
    if args.numbers.is_some() && h3_lots.is_none() {
        anyhow::bail!(
            "--numbers: оси «×H3» не определены у режимов notional/strength/both — только floor/percentile"
        );
    }
    if args.moves.is_some() {
        let window_ms = args.moves_window_ms.ok_or_else(|| {
            anyhow::anyhow!(
                "--moves требует --moves-window-ms: окно поиска — параметр измерения, не константа (В-46)"
            )
        })?;
        anyhow::ensure!(window_ms > 0, "--moves-window-ms обязан быть положителен");
    }

    Ok(ResolvedPlan {
        bands,
        cfg,
        h3_lots,
    })
}
