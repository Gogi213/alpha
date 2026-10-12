//! Г-88 (В-227 D, прочтение Б): вход после «закола» — цена на секунды прошла за уровень и вернулась.
//!
//! Чистый детектор по сделкам ленты (В-172) внутри окна взвода `[t_arm, t_arm + entry_ttl]`. Книга не читается:
//! исчезновение стены при заколе сигнал не гасит (иначе клетки пусты по построению, разбор
//! `reviews/P-02-2026-09-26-g88-undefined.md` п.1). Лонг: закол — сделка по цене ≤ `P − D`, возврат — первая сделка
//! ≥ `P` после закола; шорт зеркально. `D` в bps от `P`, переводится в целые тики с округлением вверх (закол не мельче
//! `D`). `t0` входа = возврат + пауза; пауза 0 — вход на сделке возврата. Закола или возврата в окне нет — сигнала нет.

/// Одна сделка ленты: метка (нс) и цена в целых тиках.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TradeTick {
    pub ts_ns: i64,
    pub tick: i64,
}

/// Глубина закола в тиках: `ceil(level_tick · depth_bps / 10 000)`, не меньше 1 тика при `depth_bps > 0`.
pub fn depth_ticks(level_tick: i64, depth_bps: u32) -> i64 {
    if depth_bps == 0 {
        return 0;
    }
    let num = i128::from(level_tick) * i128::from(depth_bps);
    let t = i64::try_from((num + 9_999) / 10_000).unwrap_or(i64::MAX);
    t.max(1)
}

/// Момент входа `t0` (нс) или `None`. `long` — сторона сделки (стена-бид, закол вниз). `trades` — по времени.
pub fn reclaim_t0(
    trades: &[TradeTick],
    long: bool,
    level_tick: i64,
    depth_bps: u32,
    pause_ns: i64,
    window: (i64, i64),
) -> Option<i64> {
    let d = depth_ticks(level_tick, depth_bps);
    let mut poked = false;
    for t in trades {
        if t.ts_ns < window.0 {
            continue;
        }
        if t.ts_ns > window.1 {
            break;
        }
        let (beyond, back) = if long {
            (t.tick <= level_tick - d, t.tick >= level_tick)
        } else {
            (t.tick >= level_tick + d, t.tick <= level_tick)
        };
        if poked && back {
            return Some(t.ts_ns.saturating_add(pause_ns));
        }
        poked |= beyond;
    }
    None
}

#[cfg(test)]
mod tests;
