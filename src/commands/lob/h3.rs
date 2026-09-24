//! Режим `H3` и общие флаги CLI: `--h3-mode`/`--h3-lots` (`H3Args`), тройка
//! исполнения (`ExecutionArgs`), чтение пола `floor` и `median_trade_lots` из
//! `instruments.csv`. Отдельно от `mod.rs`: общее для `levels`, `markout`,
//! `watch`, `pilot`, `profiles`, `shortlist` — не CLI-диспетчер и не реплей.

use std::path::Path;

use crate::commands::record::instruments_csv_path;
use crate::lob::levels::H3Mode;

use super::pick;

/// Прогрев трекера по умолчанию, мс: 60 минут (`[ASSUMPTION H3]`).
pub const DEFAULT_WARMUP_MS: i64 = 3_600_000;
/// Скользящее окно `repeat_count` по умолчанию, мс: час (шаг 1.1).
pub const DEFAULT_REPEAT_WINDOW_MS: i64 = 3_600_000;
/// Минимум зачтённых `pulled`-уровней гейта G0 (шаг 3.1).
pub const G0_MIN_PULLED: u64 = 200;

// ---------------------------------------------------------------------------
// Режим `H3`: флаг без умолчания (план D-H3) + чтение пола `floor` из
// `instruments.csv`. Общее для `levels`, `markout`, `pilot`, `watch`,
// `profiles`, `shortlist` (переехало из `levels.rs` — таск 17, общий код
// нескольких подкоманд не может лежать в файле одной из них).
// ---------------------------------------------------------------------------

/// Режим порога `H3`, флагом CLI. Явный выбор — умолчания нет: какой режим
/// входит в предрегистрацию, решает двухчасовой пилот, не эта команда.
#[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
pub enum H3ModeArg {
    /// Пол в лотах из `instruments.csv`, без прогрева — режим отладки.
    Floor,
    /// 99-й перцентиль по скользящему часу, прогрев 60 мин — прежнее
    /// определение; порог измерен заранее и приходит через `--h3-lots`.
    Percentile,
    /// Абсолютный пол «от N» в деньгах: `--h3-usd` (владелец 2026-09-18, В-61).
    Notional,
    /// Относительная сила «×соседи»: `--h3-strength-pct` и
    /// `--h3-strength-window-bps` (В-61).
    Strength,
    /// И то и другое: все три флага (В-61).
    Both,
}

/// Флаги режима `H3` (`--h3-mode`, `--h3-lots`), `#[command(flatten)]` в
/// `levels`/`markout`/`watch`/`profiles`/`shortlist` (таск 17: те же два
/// флага дословно определялись в пяти файлах). `lob pilot` не флаттенит
/// это: он гоняет обе ветки `H3` внутри одного прогона, а не выбирает одну
/// флагом CLI.
#[derive(Debug, Clone, Copy, clap::Args)]
pub struct H3Args {
    /// Режим порога H3: `floor` | `percentile`, без умолчания (план D-H3).
    #[arg(long)]
    pub h3_mode: H3ModeArg,
    /// Порог рождения H3 в лотах: только режим `percentile` — заранее
    /// измеренный 99-й перцентиль. `floor` берёт число из `instruments.csv`
    /// и этот флаг вместе с `floor` — ошибка (`resolve_h3_mode`), не игнор.
    #[arg(long)]
    pub h3_lots: Option<i64>,
    /// Денежный порог плотности, USD: режимы `notional` и `both` (В-61).
    #[arg(long)]
    pub h3_usd: Option<f64>,
    /// Минимальная сила уровня в процентах от среднего соседа: `strength`, `both`.
    #[arg(long)]
    pub h3_strength_pct: Option<f64>,
    /// Окно соседей, bps от цены уровня: `strength`, `both`.
    #[arg(long)]
    pub h3_strength_window_bps: Option<f64>,
}

/// Тройка флагов `BacktestFillModel` (`--median-rtt-ns`, `--p95-rtt-ns`,
/// `--order-qty-e9`) — `#[command(flatten)]` в `profiles`/`shortlist`
/// (таск 17: тройка дословно определялась в обоих файлах). Все три
/// опциональны и включают модель исполнения только вместе
/// (`resolve_fill_model`); не заданы — `NoFillModel`, как раньше.
///
/// `lob backtest` **не** флаттенит эту структуру: там та же тройка —
/// обязательные флаги без умолчания (нет `NoFillModel`, бэктест не бывает
/// без RTT/лота), а `Option<i64>` здесь убрал бы `--median-rtt-ns` и соседей
/// из строки `Usage` как обязательные — заметная перемена `--help`, которую
/// критерий приёмки таска 17 запрещает. Общая структура для настоящего
/// разного контракта CLI (обязательно/опционально) была бы либо тем же
/// изменением поведения, либо второй структурой с другим именем — то есть
/// не тем упрощением, которого просит пункт 2.
#[derive(Debug, Clone, Copy, clap::Args)]
pub struct ExecutionArgs {
    /// Медианная замеренная RTT исполнения, нс — параметр `BacktestFillModel`
    /// (таск 16), тот же смысл и источник, что `lob backtest
    /// --median-rtt-ns` (D-RTT: `lob probe`/`clock.csv`). Обязана быть задана
    /// вместе с `--p95-rtt-ns`/`--order-qty-e9` — все три сразу включают
    /// модель исполнения поверх `lob::backtest` (`resolve_fill_model`); без
    /// всех трёх (умолчание — не задан ни один) команда остаётся на
    /// `NoFillModel`, как раньше. Задавать только часть тройки — ошибка:
    /// изобретать недостающее число запрещено (§9).
    #[arg(long)]
    pub median_rtt_ns: Option<i64>,
    /// 95-й перцентиль той же замеренной RTT — см. `median_rtt_ns`.
    #[arg(long)]
    pub p95_rtt_ns: Option<i64>,
    /// Размер круга в 1e-9 лотов — минимальный лот площадки (Decision 22,
    /// тот же смысл, что `lob backtest --order-qty-e9`) — см. `median_rtt_ns`.
    #[arg(long)]
    pub order_qty_e9: Option<i64>,
}

/// Сырое значение колонки `column` первой строки `symbol` в уже открытом
/// `instruments.csv` — общий проход, которым ищут строку символа порог `H3`
/// (`h3_lots_for_symbol`, `median_trade_lots_for_symbol` ниже) и подпись
/// дашборда (`h3_k_for_symbol`, `dashboard.rs`; W9 ревью 23.09). Позиции
/// колонок ищутся по имени в заголовке — порядок столбцов файла не зафиксирован.
/// `Ok(None)` — колонки `column` или `symbol` нет в заголовке либо строки
/// символа нет; `Err` — строка файла не читается (кривой CSV после ручной
/// правки пула): вызывающий печатает «файл повреждён», а не вводящее в
/// заблуждение «нет строки» (замечание проверки W9). Скан обрывается на
/// первой кривой строке, как и раньше, — не пропуском.
pub(crate) fn instruments_symbol_field(
    reader: &mut csv::Reader<std::fs::File>,
    headers: &csv::StringRecord,
    symbol: &str,
    column: &str,
) -> Result<Option<String>, csv::Error> {
    let (Some(col_idx), Some(sym_idx)) = (
        headers.iter().position(|h| h == column),
        headers.iter().position(|h| h == "symbol"),
    ) else {
        return Ok(None);
    };
    for row in reader.records() {
        let row = row?;
        if row.get(sym_idx) == Some(symbol) {
            return Ok(row.get(col_idx).map(str::to_string));
        }
    }
    Ok(None)
}

/// Пол `H3` символа из `instruments.csv`: нет файла, нет колонки, нет
/// символа или значение не положительное — понятная ошибка с ненулевым
/// кодом выхода, а не молчаливый ноль (критерий приёмки таска 02).
fn h3_lots_for_symbol(instruments_csv: &Path, symbol: &str) -> anyhow::Result<i64> {
    // Единственный читатель `instruments.csv` (дозапрос по ревью таска 08,
    // ось Craft) — терпит метку `debug` первой строкой, голый
    // `csv::Reader::from_path` читал бы её как заголовок вместо настоящего.
    let mut r = pick::instruments_csv_reader(instruments_csv).map_err(|e| {
        anyhow::anyhow!(
            "{}: {e} — режиму floor нужен instruments.csv с колонкой h3_lots \
             (пишет отдельный шаг сборки пула)",
            instruments_csv.display()
        )
    })?;
    let headers = r.headers()?.clone();
    anyhow::ensure!(
        headers.iter().any(|h| h == "h3_lots"),
        "{}: нет колонки h3_lots (пишет отдельный шаг сборки пула)",
        instruments_csv.display()
    );
    let Some(raw) = instruments_symbol_field(&mut r, &headers, symbol, "h3_lots")
        .map_err(|e| anyhow::anyhow!("{}: строка не читается ({e})", instruments_csv.display()))?
    else {
        anyhow::bail!(
            "{symbol}: нет строки в {} (режим floor)",
            instruments_csv.display()
        );
    };
    let raw = raw.trim();
    let v: i64 = raw
        .parse()
        .map_err(|_| anyhow::anyhow!("{symbol}: h3_lots {raw:?} в instruments.csv не целое"))?;
    anyhow::ensure!(
        v > 0,
        "{symbol}: h3_lots обязан быть положителен, получено {v}"
    );
    Ok(v)
}

/// Медиана размера сделки символа из `instruments.csv` — вход
/// `pick::h3_lots_floor` для `--h3-k`/сетки `pilot::K_GRID` (таск 18,
/// В-30/D05). Нет файла, нет колонки, нет строки символа или значение не
/// положительное — понятная ошибка (тот же приём, что `h3_lots_for_symbol`),
/// не молчаливый ноль.
pub(crate) fn median_trade_lots_for_symbol(
    instruments_csv: &Path,
    symbol: &str,
) -> anyhow::Result<i64> {
    let mut r = pick::instruments_csv_reader(instruments_csv).map_err(|e| {
        anyhow::anyhow!(
            "{}: {e} — --h3-k нужен instruments.csv с колонкой median_trade_lots (пишет lob pick)",
            instruments_csv.display()
        )
    })?;
    let headers = r.headers()?.clone();
    anyhow::ensure!(
        headers.iter().any(|h| h == "median_trade_lots"),
        "{}: нет колонки median_trade_lots (пишет lob pick)",
        instruments_csv.display()
    );
    let Some(raw) = instruments_symbol_field(&mut r, &headers, symbol, "median_trade_lots")
        .map_err(|e| anyhow::anyhow!("{}: строка не читается ({e})", instruments_csv.display()))?
    else {
        anyhow::bail!(
            "{symbol}: нет строки в {} (median_trade_lots)",
            instruments_csv.display()
        );
    };
    let raw = raw.trim();
    anyhow::ensure!(
        !raw.is_empty(),
        "{symbol}: median_trade_lots не измерена в {} (окно lob pick не поймало сделок)",
        instruments_csv.display()
    );
    let v: i64 = raw.parse().map_err(|_| {
        anyhow::anyhow!("{symbol}: median_trade_lots {raw:?} в instruments.csv не целое")
    })?;
    anyhow::ensure!(
        v > 0,
        "{symbol}: median_trade_lots обязана быть положительна, получено {v}"
    );
    Ok(v)
}

/// Режим `H3` из флага: `floor` читает пол из `instruments.csv` корня
/// записи, `percentile` берёт заранее измеренный порог из `--h3-lots`
/// (обязателен в этом режиме — измерение вне этой команды). `--h3-lots`
/// вместе с `floor` — громкая ошибка (таск 17, критерий приёмки), а не
/// молчаливый игнор значения, которое `floor` не читает.
pub fn resolve_h3_mode(
    root: &Path,
    symbol: &str,
    mode: H3ModeArg,
    h3_lots: Option<i64>,
) -> anyhow::Result<H3Mode> {
    resolve_h3_mode_with_k(root, symbol, mode, h3_lots, None)
}

/// Как `resolve_h3_mode`, плюс относительный порог `--h3-k` (таск 18,
/// В-30/D05): `k` действует только вместе с `floor` — порог `h3_lots =
/// floor(k × median_trade_lots)` (`pick::h3_lots_floor`), медиана — из той
/// же строки `instruments.csv`, что несёт колонку `h3_lots`. Без `--h3-k` —
/// прежнее поведение (`resolve_h3_mode` делегирует сюда с `h3_k = None`,
/// колонка `h3_lots` целиком). `--h3-k` вместе с `percentile` — громкая
/// ошибка: `k` параметризует только относительный порог `floor`, у
/// `percentile` свой заранее измеренный перцентиль (критерий приёмки
/// таска 18). Единственный вызывающий сегодня — `lob levels`; сетка
/// `pilot::K_GRID` зовёт `pick::h3_lots_floor`/`median_trade_lots_for_symbol`
/// напрямую, минуя эту обёртку (ей нужно пять порогов за один реплей, не
/// один).
pub fn resolve_h3_mode_with_k(
    root: &Path,
    symbol: &str,
    mode: H3ModeArg,
    h3_lots: Option<i64>,
    h3_k: Option<f64>,
) -> anyhow::Result<H3Mode> {
    match mode {
        H3ModeArg::Floor => {
            anyhow::ensure!(
                h3_lots.is_none(),
                "--h3-lots несовместим с --h3-mode floor: порог берётся из instruments.csv"
            );
            let instruments_csv = instruments_csv_path(root);
            let h3_lots = match h3_k {
                Some(k) => {
                    let median = median_trade_lots_for_symbol(&instruments_csv, symbol)?;
                    pick::h3_lots_floor(Some(median), k).ok_or_else(|| {
                        anyhow::anyhow!("{symbol}: h3_lots_floor(k={k}) не посчитался")
                    })?
                }
                None => h3_lots_for_symbol(&instruments_csv, symbol)?,
            };
            anyhow::ensure!(
                h3_lots > 0,
                "{symbol}: h3_lots (k={h3_k:?}) обязан быть положителен, получено {h3_lots}"
            );
            Ok(H3Mode::Floor { h3_lots })
        }
        H3ModeArg::Percentile => {
            anyhow::ensure!(
                h3_k.is_none(),
                "--h3-k несовместим с --h3-mode percentile: k параметризует только floor (В-30/D05)"
            );
            let h3_lots = h3_lots.ok_or_else(|| {
                anyhow::anyhow!(
                    "--h3-lots обязателен в режиме percentile: порог измеряется заранее"
                )
            })?;
            Ok(H3Mode::Percentile { h3_lots })
        }
        H3ModeArg::Notional | H3ModeArg::Strength | H3ModeArg::Both => anyhow::bail!(
            "режим H3 {mode:?} требует тик и шаг лота записи — его знает `resolve_h3_mode_full` \
             (bounce-grid); в этой команде доступны только floor|percentile"
        ),
    }
}

/// Полный резолвер режима `H3` (В-61): к `floor`/`percentile` добавляет
/// `notional` (`--h3-usd`), `strength` (`--h3-strength-pct`,
/// `--h3-strength-window-bps`) и `both`. Тик и шаг лота — из заголовка
/// бинлога (`read_tick_step`), не из `instruments.csv`. Лишние флаги —
/// отказ, не игнор.
pub fn resolve_h3_mode_full(
    root: &Path,
    symbol: &str,
    h3: &H3Args,
    h3_k: Option<f64>,
    tick_e9: i64,
    step_e9: i64,
) -> anyhow::Result<H3Mode> {
    let usd = || -> anyhow::Result<i64> {
        let usd = h3
            .h3_usd
            .ok_or_else(|| anyhow::anyhow!("--h3-usd обязателен в режиме {:?}", h3.h3_mode))?;
        anyhow::ensure!(
            usd.is_finite() && usd > 0.0,
            "--h3-usd обязан быть положителен"
        );
        Ok((usd * 1e9).round() as i64)
    };
    let strength = || -> anyhow::Result<(i64, i64)> {
        let pct = h3.h3_strength_pct.ok_or_else(|| {
            anyhow::anyhow!("--h3-strength-pct обязателен в режиме {:?}", h3.h3_mode)
        })?;
        let w = h3.h3_strength_window_bps.ok_or_else(|| {
            anyhow::anyhow!(
                "--h3-strength-window-bps обязателен в режиме {:?}",
                h3.h3_mode
            )
        })?;
        anyhow::ensure!(
            pct.is_finite() && pct > 0.0,
            "--h3-strength-pct обязан быть положителен"
        );
        anyhow::ensure!(
            w.is_finite() && w > 0.0,
            "--h3-strength-window-bps обязан быть положителен"
        );
        Ok(((pct * 100.0).round() as i64, (w * 100.0).round() as i64))
    };
    let no_legacy = || -> anyhow::Result<()> {
        anyhow::ensure!(
            h3.h3_lots.is_none() && h3_k.is_none(),
            "--h3-lots и --h3-k относятся к floor/percentile, режим {:?} их не читает",
            h3.h3_mode
        );
        Ok(())
    };
    anyhow::ensure!(
        tick_e9 > 0 && step_e9 > 0,
        "{symbol}: тик и шаг лота обязаны быть положительны"
    );
    match h3.h3_mode {
        H3ModeArg::Floor | H3ModeArg::Percentile => {
            anyhow::ensure!(
                h3.h3_usd.is_none()
                    && h3.h3_strength_pct.is_none()
                    && h3.h3_strength_window_bps.is_none(),
                "--h3-usd/--h3-strength-* относятся к notional/strength/both, режим {:?} их не читает",
                h3.h3_mode
            );
            resolve_h3_mode_with_k(root, symbol, h3.h3_mode, h3.h3_lots, h3_k)
        }
        H3ModeArg::Notional => {
            no_legacy()?;
            anyhow::ensure!(
                h3.h3_strength_pct.is_none() && h3.h3_strength_window_bps.is_none(),
                "--h3-strength-* относятся к strength/both"
            );
            Ok(H3Mode::Notional {
                min_usd_e9: usd()?,
                tick_e9,
                step_e9,
            })
        }
        H3ModeArg::Strength => {
            no_legacy()?;
            anyhow::ensure!(h3.h3_usd.is_none(), "--h3-usd относится к notional/both");
            let (pct_e2, window_bps_e2) = strength()?;
            Ok(H3Mode::Strength {
                pct_e2,
                window_bps_e2,
            })
        }
        H3ModeArg::Both => {
            no_legacy()?;
            let (pct_e2, window_bps_e2) = strength()?;
            Ok(H3Mode::Both {
                min_usd_e9: usd()?,
                tick_e9,
                step_e9,
                pct_e2,
                window_bps_e2,
            })
        }
    }
}
