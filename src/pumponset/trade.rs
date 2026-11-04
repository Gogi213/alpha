//! Сделка «встать рано» без стратегии (П-34 §7.5): вход по `close_ff` минуты `t_f + 1`, выход по `close_ff` минуты
//! `t_f + H`, фандинг за удержание по реальным ставкам файла. Издержки входа/выхода здесь НЕ вычитаются — их
//! берёт `verdict` из `crate::lob::costs`. Значение после `t_f` на факт срабатывания не влияет: сюда приходит
//! уже найденное срабатывание.
//!
//! Допущения (протокол не называет):
//! 1. Фандинг считается в bps номинала входа: `rate · 1e4` за каждую выплату с минутой в `(t_f + 1, t_f + H]`
//!    (выплата в минуту входа позицию ещё не застаёт; в минуту выхода — застаёт). Лонг платит `rate`, шорт получает
//!    (`sign · rate`): положительное значение — издержка стороны.
//! 2. Нет данных — `None` (в `nret` не входит): цены входа/выхода нет (до первой свечи, за концом ряда), знак 0,
//!    `H = 0` либо у контракта вообще нет ставок фандинга (иначе net без фандинга выглядел бы лучше правды).
//!    Окно удержания без выплат при непустом файле — ноль, это не «нет данных».

#![allow(
    // индексы ограничены проверенными длинами; бюджет линтов (TK-211) не растим
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_precision_loss
)]

/// Перевод доли в bps (единица измерения, не параметр).
const BPS: f64 = 1.0e4;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Trade {
    /// `sign · ln(c[t_f + H] / c[t_f + 1]) · 1e4`.
    pub gross_bps: f64,
    /// Фандинг, уплаченный позицией стороны `sign` за удержание, bps номинала (положительное — издержка).
    pub fund_bps: f64,
}

/// Сделка срабатывания: `cf` — `close_ff` (индекс 0 ↔ минута `first_min`), `funding` — выплаты `(минута, ставка)`
/// по возрастанию минуты, `t_f` — минута срабатывания, `h` — удержание в минутах, `sign` — сторона (±1).
pub fn round_trip(
    cf: &[f64],
    first_min: i64,
    funding: &[(i64, f64)],
    t_f: i64,
    h: u32,
    sign: i8,
) -> Option<Trade> {
    if sign == 0 || h == 0 || funding.is_empty() {
        return None;
    }
    let (m_in, m_out) = (t_f + 1, t_f + i64::from(h));
    let (b, e) = (m_in - first_min, m_out - first_min);
    if b < 0 || e < 0 || e as usize >= cf.len() {
        return None;
    }
    let (c_in, c_out) = (cf[b as usize], cf[e as usize]);
    if !(c_in.is_finite() && c_in > 0.0 && c_out.is_finite() && c_out > 0.0) {
        return None;
    }
    let side = f64::from(sign);
    let lo = funding.partition_point(|&(m, _)| m <= m_in);
    let hi = funding.partition_point(|&(m, _)| m <= m_out);
    let mut rate_sum = 0.0;
    for &(_, r) in &funding[lo..hi] {
        rate_sum += r;
    }
    Some(Trade {
        gross_bps: side * (c_out / c_in).ln() * BPS,
        fund_bps: side * rate_sum * BPS,
    })
}
