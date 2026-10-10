//! Общее, что осталось от исследовательского слоя 09.2026 (TK-168, КТ-11): корзина
//! размера, маркер сверки, трейт модели исполнения, правило отбора CPCV.

use std::path::{Path, PathBuf};

use crate::lob::levels::LevelRecord;
use crate::lob::markout::MidSample;
use crate::lob::shortlist::SIZE_LABELS;

/// Кратности порога `H3`: `[1,2)`, `[2,4)`, `[4,∞)` (`shortlist::SIZE_LABELS`).
const SIZE_BOUNDS: [(f64, f64); 3] = [(1.0, 2.0), (2.0, 4.0), (4.0, f64::INFINITY)];

pub(crate) fn size_bucket(ratio: f64) -> Option<&'static str> {
    SIZE_BOUNDS
        .iter()
        .zip(SIZE_LABELS.iter())
        .find(|((lo, hi), _)| ratio >= *lo && ratio < *hi)
        .map(|(_, label)| *label)
}

pub(crate) fn read_verify_marker(path: &Path) -> bool {
    std::fs::read_to_string(path)
        .map(|s| s.trim() == "ok")
        .unwrap_or(false)
}

/// Модель исполнения входа за отведённые 2 с (R06): `Some(bool)` — модель
/// судит, `None` — не имеет мнения. Реализация — `BacktestFillModel`.
pub trait FillModel {
    /// `_binlog_paths` — все части сессии в порядке записи; модель с книжным
    /// потоком прогоняет их подряд. Умолчание — пустая операция.
    fn prime_session(&self, _symbol: &str, _binlog_paths: &[PathBuf], _records: &[LevelRecord]) {}

    fn filled(&self, symbol: &str, rec: &LevelRecord, mids: &[MidSample]) -> Option<bool>;
    fn label(&self) -> &'static str;
}

/// Правило отбора, которое проверяет CPCV, — то же, которым шорт-лист
/// выбирает профиль вердикта: лучший по среднему `net` на предъявленных
/// сутках. Печатается в шапке рядом с числом, чтобы «процедура отбора» в
/// отчёте была названа, а не подразумевалась.
pub(crate) const CPCV_SELECTION_RULE: &str = "лучший по среднему net на IS-сутках";

/// Само правило: индекс лучшей строки матрицы по среднему `net` на
/// поданных периодах. `Confirmed` шорт-листа добавляет к тому же
/// сравнению пороги `n`/`G`, которых суточная матрица не несёт по
/// построению (в ней только `net`), — это единственное расхождение, и оно
/// сужает не выбор, а множество, из которого он делается.
pub(crate) fn select_best_mean_net(trials: &[Vec<f64>], is_periods: &[usize]) -> Option<usize> {
    let mut best: Option<(usize, f64)> = None;
    for (i, row) in trials.iter().enumerate() {
        let vals: Vec<f64> = is_periods
            .iter()
            .filter_map(|&j| row.get(j).copied())
            .filter(|x| x.is_finite())
            .collect();
        if vals.is_empty() {
            continue;
        }
        let mean = vals.iter().sum::<f64>() / vals.len() as f64;
        if best.is_none_or(|(_, b)| mean > b) {
            best = Some((i, mean));
        }
    }
    best.map(|(i, _)| i)
}

