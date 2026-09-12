//! Сетка `k` пилота (таск 18, `SETTLED.md` В-30, `manifest.md` D05): `K_GRID`,
//! ставка и доля `eaten` по инструменту за один реплей, сводка по медианному
//! инструменту, выбор `k` и печать таблицы. Отдельно от `pilot.rs`: процедура
//! выбора `k` — самостоятельная методология, её читают без остальной цепочки.

use std::path::Path;

use crate::commands::lob::pick::h3_lots_floor;
use crate::commands::lob::{
    median_trade_lots_for_symbol, replay_symbol_over_configs, G0_MIN_PULLED,
};
use crate::lob::levels::{H3Mode, LevelsConfig, Outcome};
use crate::stats::{count_f64, count_f64_u64};

use super::metrics::{counted_tail_cutoff_ms, median};

// ---------------------------------------------------------------------------
// Сетка `k` (таск 18, `SETTLED.md` В-30, `manifest.md` D05): владелец на
// вопрос о числе `k` — «не знаю надо методологию определения динамического
// порога сформулировать», методологию сформулировал оркестратор и записал
// как В-30. `k` не назначается рукой: двухчасовой пилот считает ставку
// уровней в минуту и долю `eaten` по предрегистрированной сетке и берёт
// наименьшее `k`, при котором медианный инструмент проходит G0 (`PLAN.md`
// §6: ≥ 200 уровней за зачтённый час) и G1 (§6: `eaten` ≥ 5%). Один реплей
// на инструмент, не пять: `k_grid_for_instrument` читает
// `median_trade_lots` один раз и кормит все пять порогов
// `super::replay_symbol_over_configs` разом — та функция декодирует бинлог
// один раз и раздаёт кадры пяти трекерам (`interfaces.md`, «Из таска 18»).
// ---------------------------------------------------------------------------

/// Предрегистрированная сетка `k` (`SETTLED.md` В-30, `manifest.md` D05):
/// «k выбирает двухчасовой пилот ... по предрегистрированной сетке k ∈ {2,
/// 5, 10, 20, 50}». Не изобретённое число: сетка зафиксирована методологией
/// до всякого прогона, владелец явно отказался назначать `k` рукой.
pub const K_GRID: [f64; 5] = [2.0, 5.0, 10.0, 20.0, 50.0];

/// Доля `eaten` гейта G1 (`PLAN.md` §6: «`eaten` и `pulled` обе ≥ 5%») —
/// переиспользует уже закоммиченный гейт `lob::markup::G1_MIN_SHARE_NUM`/
/// `_DEN` (1/20), а не второй литерал `0.05`: изобретённое число запрещено,
/// а этот порог уже назначен и живёт в одном месте (`markup.rs`, гейт G1
/// исхода уровня — то же число из того же пункта плана, не совпадение).
fn g1_min_eaten_share() -> f64 {
    count_f64_u64(crate::lob::markup::G1_MIN_SHARE_NUM)
        / count_f64_u64(crate::lob::markup::G1_MIN_SHARE_DEN)
}

/// Одна строка сетки `k` одного инструмента: число уровней, ставка в
/// минуту и доля `eaten` при пороге `h3_lots = floor(k × median_trade_lots)`.
#[derive(Debug, Clone, Copy)]
pub struct KGridInstrumentRow {
    pub k: f64,
    pub levels: usize,
    pub rate_per_min: f64,
    pub eaten_share: f64,
}

/// Сетка `k` одного инструмента, один реплей бинлога (критерий приёмки
/// таска 18: «за один реплей на инструмент», не пять): `median_trade_lots`
/// читается один раз из `instruments.csv` (`super::
/// median_trade_lots_for_symbol`), пороги `h3_lots` для всех пяти `k` из
/// `K_GRID` собираются в пять `LevelsConfig::Floor`, и `super::
/// replay_symbol_over_configs` кормит все пять трекеров кадрами одного
/// декодирования. Хвостовой фильтр («второй час» §11) — тот же приём, что
/// `process_instrument`/`counted_tail_cutoff_ms`: `None` — вся выборка
/// (`--debug`), `Some(t)` — только хвост в `t` минут (боевой путь).
pub(super) fn k_grid_for_instrument(
    root: &Path,
    symbol: &str,
    repeat_window_ms: i64,
    window_minutes: f64,
    counted_tail_minutes: Option<f64>,
) -> anyhow::Result<Vec<KGridInstrumentRow>> {
    let instruments_csv = crate::commands::record::instruments_csv_path(root);
    let median = median_trade_lots_for_symbol(&instruments_csv, symbol)?;
    let cfgs: Vec<LevelsConfig> = K_GRID
        .iter()
        .map(|&k| {
            let h3_lots = h3_lots_floor(Some(median), k)
                .ok_or_else(|| anyhow::anyhow!("{symbol}: h3_lots_floor(k={k}) не посчитался"))?;
            anyhow::ensure!(
                h3_lots > 0,
                "{symbol}: h3_lots(k={k}) обязан быть положителен (медиана={median}), получено {h3_lots}"
            );
            Ok(LevelsConfig {
                mode: H3Mode::Floor { h3_lots },
                warmup_ms: 0,
                repeat_window_ms,
            })
        })
        .collect::<anyhow::Result<Vec<_>>>()?;

    let replay_per_k = replay_symbol_over_configs(root, symbol, &cfgs)?;

    let global_max_ts_ms = replay_per_k[0]
        .days
        .iter()
        .filter_map(|d| d.mids.last().map(|s| s.ts_ms))
        .max();
    let cutoff_ms = counted_tail_cutoff_ms(global_max_ts_ms, counted_tail_minutes);
    let effective_minutes = match (cutoff_ms, counted_tail_minutes) {
        (Some(_), Some(t)) => t,
        _ => window_minutes,
    };
    let effective_minutes = if effective_minutes > 0.0 {
        effective_minutes
    } else {
        f64::INFINITY
    };

    let mut rows = Vec::with_capacity(K_GRID.len());
    for (i, &k) in K_GRID.iter().enumerate() {
        let mut levels = 0usize;
        let mut eaten = 0usize;
        for day in &replay_per_k[i].days {
            for rec in &day.records {
                if cutoff_ms.is_some_and(|c| rec.birth_ms < c) {
                    continue;
                }
                levels += 1;
                if rec.outcome() == Outcome::Eaten {
                    eaten += 1;
                }
            }
        }
        let eaten_share = if levels > 0 {
            count_f64(eaten) / count_f64(levels)
        } else {
            0.0
        };
        rows.push(KGridInstrumentRow {
            k,
            levels,
            rate_per_min: count_f64(levels) / effective_minutes,
            eaten_share,
        });
    }
    Ok(rows)
}

/// Одна строка сводки `k -> rate/eaten/G0/G1` по медианному инструменту
/// пула (критерий приёмки таска 18).
#[derive(Debug, Clone, Copy)]
pub struct KGridSummaryRow {
    pub k: f64,
    pub median_rate_per_hour: f64,
    pub median_eaten_share: f64,
    pub g0_pass: bool,
    pub g1_pass: bool,
}

/// Сводка по пулу: медиана ставки (в час) и доли `eaten` на каждый `k`
/// сетки — тот же приём «медианный инструмент», что `g0_verdict` уже
/// использует для ставки `H3`. G0/G1 — гейты §6 плана.
pub fn summarize_k_grid(
    per_instrument: &[(String, Vec<KGridInstrumentRow>)],
) -> Vec<KGridSummaryRow> {
    let g1_threshold = g1_min_eaten_share();
    (0..K_GRID.len())
        .map(|i| {
            let mut rates: Vec<f64> = per_instrument
                .iter()
                .map(|(_, rows)| rows[i].rate_per_min * 60.0)
                .collect();
            let mut eatens: Vec<f64> = per_instrument
                .iter()
                .map(|(_, rows)| rows[i].eaten_share)
                .collect();
            rates.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
            eatens.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
            let median_rate = median(&rates).unwrap_or(0.0);
            let median_eaten = median(&eatens).unwrap_or(0.0);
            KGridSummaryRow {
                k: K_GRID[i],
                median_rate_per_hour: median_rate,
                median_eaten_share: median_eaten,
                g0_pass: median_rate >= count_f64_u64(G0_MIN_PULLED),
                g1_pass: median_eaten >= g1_threshold,
            }
        })
        .collect()
}

/// Выбор `k` (таск 18, В-30/D05): наименьшее `k` сетки (сетка уже по
/// возрастанию), при котором медианный инструмент проходит оба гейта
/// разом. Нет такого — `None`: печатается как «не определим», отдельно от
/// красного про рынок (критерий приёмки).
pub fn choose_k(summary: &[KGridSummaryRow]) -> Option<f64> {
    summary.iter().find(|r| r.g0_pass && r.g1_pass).map(|r| r.k)
}

/// Печать таблицы `k -> rate/eaten/G0/G1` — по инструментам и по
/// медианному инструменту (критерий приёмки таска 18): `debug` — метка
/// `[debug]` на каждой строке, потому что ставка в `--debug` —
/// экстраполяция `rate/min × 60` на прогоне короче часа, не измеренный час
/// (боевой путь мерит настоящий зачтённый час/хвост — без метки).
pub(super) fn format_k_grid_lines(
    per_instrument: &[(String, Vec<KGridInstrumentRow>)],
    summary: &[KGridSummaryRow],
    debug: bool,
) -> Vec<String> {
    let debug_suffix = if debug { " [debug]" } else { "" };
    let mut lines = Vec::new();
    for (symbol, rows) in per_instrument {
        for row in rows {
            lines.push(format!(
                "pilot k-grid: {symbol} k={:.1} levels={} rate={:.3}/min eaten_share={:.3}{debug_suffix}",
                row.k, row.levels, row.rate_per_min, row.eaten_share
            ));
        }
    }
    for row in summary {
        lines.push(format!(
            "pilot k-grid: median k={:.1} rate_per_hour={:.1} eaten_share={:.3} G0={} G1={}{debug_suffix}",
            row.k,
            row.median_rate_per_hour,
            row.median_eaten_share,
            if row.g0_pass { "pass" } else { "fail" },
            if row.g1_pass { "pass" } else { "fail" },
        ));
    }
    match choose_k(summary) {
        Some(k) => lines.push(format!(
            "pilot k-grid: k выбран={k:.1} (h3 floor, G0 и G1 пройдены на медианном инструменте){debug_suffix}"
        )),
        None => lines.push(format!(
            "pilot k-grid: k: не определим (порог не задан по данным){debug_suffix}"
        )),
    }
    lines
}
