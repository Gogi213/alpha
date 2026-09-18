//! Стандартизированная волатильность середины (В-62, 2026-09-18).
//!
//! Геометрия сделки-отскока перестала быть тиками: стоп и тейк — множители
//! `σ_H` — реализованной волатильности середины за окно, равное горизонту
//! сделки `H` (владелец: «переходить с тиков на проценты и держать в уме
//! стандартизированную волатильность»). Одно число на касание и горизонт:
//!
//! `σ_H(t) = sqrt( Σ r_s² )` по секундам `s ∈ (t − H, t]`, где
//! `r_s = 10⁴ × (m_s − m_{s−1}) / m_{s−1}` — доходность середины в bps между
//! соседними секундами, `m_s` — середина книги **на момент** `s × 1000` мс
//! (последний срез не позже границы секунды, как `sample_asof`).
//!
//! Почему так, а не «σ за N минут × √H»: сумма квадратов секундных
//! доходностей за последние `H` секунд — это и есть типичный размах середины
//! за горизонт длиной `H`, без предположения о масштабировании √t и без
//! второго числа `N`. Окно и горизонт — одно и то же значение из сетки
//! дедлайнов В-58 (`DEADLINE_SECS`), новых чисел не появляется.
//!
//! Чтобы не считать `H` срезов на касание (7200 × 100 тыс. касаний), ряд
//! строится один раз на символ: префиксные суммы квадратов доходностей по
//! секундам, `σ` любого окна — две разности за O(1). Окно, выходящее за
//! начало записи (или через её конец), не определено — `None`: сигнала на
//! такое касание у формы нет, и это считается (`n_no_sigma` в `forms.csv`),
//! а не подменяется нулём. Разрыв записи внутри окна (шов покрытия) ряд не
//! видит: середина «как есть» тянется через шов, доходность через него —
//! один скачок; иначе пришлось бы вводить порог длины разрыва — число.

use crate::lob::markout::{mid_double_tick, MidSample};

/// Множитель перевода доли в базисные пункты.
const BPS_SCALE: f64 = 10_000.0;

/// Ряд префиксных сумм квадратов секундных доходностей середины одного
/// символа (одна запись целиком, сутки подряд).
#[derive(Debug, Clone, PartialEq)]
pub struct SigmaSeries {
    /// Первая секунда ряда (unix, с): граница секунды не раньше первого среза.
    first_sec: i64,
    /// `cum_sq[j]` — сумма `r_s²` по `s ∈ (first_sec, first_sec + j]`;
    /// `cum_sq[0] = 0`. Длина — число секунд ряда.
    cum_sq: Vec<f64>,
}

impl SigmaSeries {
    /// Строит ряд по срезам середины, отсортированным по времени (так их
    /// выдаёт реплей; сутки подряд — конкатенация в порядке дней). Пустой
    /// вход — пустой ряд, у которого `σ` не определена нигде.
    pub fn from_mids(mids: &[MidSample]) -> Self {
        debug_assert!(
            mids.windows(2).all(|w| w[0].ts_ms <= w[1].ts_ms),
            "срезы середины обязаны идти по времени"
        );
        let (Some(first), Some(last)) = (mids.first(), mids.last()) else {
            return Self {
                first_sec: 0,
                cum_sq: Vec::new(),
            };
        };
        let first_sec =
            first.ts_ms.div_euclid(1_000) + i64::from(first.ts_ms.rem_euclid(1_000) != 0);
        let last_sec = last.ts_ms.div_euclid(1_000);
        if last_sec < first_sec {
            return Self {
                first_sec: 0,
                cum_sq: Vec::new(),
            };
        }
        let n_secs = usize::try_from(last_sec - first_sec + 1).unwrap_or(0);
        let mut cum_sq = Vec::with_capacity(n_secs);
        let mut cum = 0.0_f64;
        let mut prev_mid2x: Option<i64> = None;
        // Указатель по срезам: для каждой секунды — последний срез не позже
        // её границы. Срезы и секунды монотонны, проход линейный.
        let mut i = 0usize;
        for sec in first_sec..=last_sec {
            let boundary_ms = sec.saturating_mul(1_000);
            while i + 1 < mids.len() && mids[i + 1].ts_ms <= boundary_ms {
                i += 1;
            }
            let mid2x = mid_double_tick(mids[i].bid_tick, mids[i].ask_tick);
            if let Some(prev) = prev_mid2x {
                cum += return_bps(prev, mid2x).powi(2);
            }
            cum_sq.push(cum);
            prev_mid2x = Some(mid2x);
        }
        Self { first_sec, cum_sq }
    }

    /// `σ` в bps за окно `window_s` секунд, кончающееся последней границей
    /// секунды не позже `ts_ms`. `None` — окно не покрыто рядом (начало
    /// записи ближе `window_s`, метка за концом ряда, окно неположительное).
    pub fn sigma_bps(&self, ts_ms: i64, window_s: i64) -> Option<f64> {
        if window_s <= 0 || self.cum_sq.is_empty() {
            return None;
        }
        let end_sec = ts_ms.div_euclid(1_000);
        let start_sec = end_sec.checked_sub(window_s)?;
        if start_sec < self.first_sec {
            return None;
        }
        let end = usize::try_from(end_sec - self.first_sec).ok()?;
        let start = usize::try_from(start_sec - self.first_sec).ok()?;
        let end_cum = *self.cum_sq.get(end)?;
        let start_cum = *self.cum_sq.get(start)?;
        // Префиксные суммы неубывающие; `max(0)` гасит хвост округления.
        Some((end_cum - start_cum).max(0.0).sqrt())
    }

    /// Число секунд в ряду.
    pub fn len_secs(&self) -> usize {
        self.cum_sq.len()
    }
}

/// Прореживание хвоста срезов до «как есть на границу секунды»: предпоследний
/// срез нужен, только если между ним и последним (включая его метку) лежит
/// граница секунды — иначе на любую границу `sample_asof` вернул бы последний,
/// и предпоследний удаляется. Зовётся после каждого кадра (один срез на кадр),
/// поэтому смотреть дальше двух последних не надо: всё раньше уже прорежено.
/// Результат `SigmaSeries::from_mids` на прореженном ряду тот же, что на
/// полном (тест `thinned_samples_give_the_same_series_as_the_full_ones`).
/// Живёт здесь, а не в реплее: это правило ряда `σ`, реплей лишь его зовёт.
pub fn thin_mids_tail_to_second_boundaries(mids: &mut Vec<MidSample>) {
    while mids.len() >= 2 {
        let prev = mids[mids.len() - 2].ts_ms;
        let last = mids[mids.len() - 1].ts_ms;
        // Первая граница секунды не раньше `prev`.
        let boundary =
            prev.div_euclid(1_000) * 1_000 + i64::from(prev.rem_euclid(1_000) != 0) * 1_000;
        if boundary < last {
            break;
        }
        let keep_last = mids[mids.len() - 1];
        mids.pop();
        mids.pop();
        mids.push(keep_last);
    }
}

/// Доходность середины между двумя удвоенными серединами в bps; ноль при
/// неположительной базе (книги без сторон в ряду не бывает — `feed_frames`
/// пишет срез только при обеих сторонах, но арифметика тотальна).
#[allow(clippy::cast_precision_loss)]
fn return_bps(prev2x: i64, next2x: i64) -> f64 {
    if prev2x <= 0 {
        return 0.0;
    }
    (next2x - prev2x) as f64 / prev2x as f64 * BPS_SCALE
}

#[cfg(test)]
mod tests;
