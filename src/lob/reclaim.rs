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

/// Потоковый автомат Г-88: сделки подаются по одной (тот же код в бэктесте и live, без аллокаций). Состояние — один
/// флаг «закол был». `feed` отдаёт `Some(t0)` ровно на сделке возврата; окно `[t_arm, t_arm + ttl]` проверяется здесь.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ReclaimWatch {
    long: bool,
    level_tick: i64,
    depth: i64,
    pause_ns: i64,
    window: (i64, i64),
    poked: bool,
}

impl ReclaimWatch {
    /// `long` — сторона сделки (стена-бид, закол вниз). `window` — `[t_arm, t_arm + entry_ttl]`, нс.
    pub fn new(
        long: bool,
        level_tick: i64,
        depth_bps: u32,
        pause_ns: i64,
        window: (i64, i64),
    ) -> Self {
        Self {
            long,
            level_tick,
            depth: depth_ticks(level_tick, depth_bps),
            pause_ns,
            window,
            poked: false,
        }
    }

    /// Окно кончилось к моменту `now_ns`: сигнала уже не будет (вход не ставится).
    pub fn expired(&self, now_ns: i64) -> bool {
        now_ns > self.window.1
    }

    /// Сделка ленты; `Some(t0)` — вход в `t0` (возврат + пауза). Сделки вне окна игнорируются.
    pub fn feed(&mut self, t: TradeTick) -> Option<i64> {
        if t.ts_ns < self.window.0 || t.ts_ns > self.window.1 {
            return None;
        }
        let (beyond, back) = if self.long {
            (
                t.tick <= self.level_tick - self.depth,
                t.tick >= self.level_tick,
            )
        } else {
            (
                t.tick >= self.level_tick + self.depth,
                t.tick <= self.level_tick,
            )
        };
        if self.poked && back {
            return Some(t.ts_ns.saturating_add(self.pause_ns));
        }
        self.poked |= beyond;
        None
    }
}

/// Момент входа `t0` (нс) по готовой ленте или `None` — свёртка `ReclaimWatch` (для разборов и тестов).
pub fn reclaim_t0(
    trades: &[TradeTick],
    long: bool,
    level_tick: i64,
    depth_bps: u32,
    pause_ns: i64,
    window: (i64, i64),
) -> Option<i64> {
    let mut w = ReclaimWatch::new(long, level_tick, depth_bps, pause_ns, window);
    trades.iter().find_map(|&t| w.feed(t))
}

#[cfg(test)]
mod tests;
