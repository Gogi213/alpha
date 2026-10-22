//! Г-88 (В-227 D, прочтение Б): вход после «закола» — цена на секунды прошла за уровень и вернулась.
//!
//! Чистый детектор по сделкам ленты (В-172) внутри окна взвода `[t_arm, t_arm + entry_ttl]`. Книга не читается:
//! исчезновение стены при заколе сигнал не гасит (иначе клетки пусты по построению, разбор
//! `reviews/P-02-2026-09-26-g88-undefined.md` п.1). Лонг: закол — сделка по цене ≤ `P − D`, возврат — первая сделка
//! ≥ `P` после закола; шорт зеркально. `D` в bps от `P`, переводится в целые тики с округлением вверх (закол не мельче
//! `D`). `t0` входа = возврат + пауза; пауза 0 — вход на сделке возврата. Закола или возврата в окне нет — сигнала нет.
//!
//! Г-135 (`postsweep<R>`, П-17): тот же автомат в режиме «отход от экстремума» — после взвода цена ушла за уровень и
//! отошла на `R` bps обратно: лонг — сделка ≥ минимум окна + `R`; шорт зеркально. Минимум берётся по сделкам окна,
//! включая саму сделку, `t0` — метка сделки отхода (пауза 0). Стена не читается.

/// Одна сделка ленты: метка (нс) и цена в целых тиках.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TradeTick {
    pub ts_ns: i64,
    pub tick: i64,
}

/// Г-135: окно `postsweep` от взвода, нс — назначено П-17 (§5 п. 4), не измерено; не длиннее TTL входа.
pub const POSTSWEEP_WINDOW_NS: i64 = 30_000_000_000;

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
    /// Г-135: отход от экстремума окна в тиках; `0` — режим Г-88 (закол и возврат).
    rise: i64,
    /// Г-135: экстремум сделок окна (лонг — минимум, шорт — максимум), `None` до первой сделки.
    extreme: Option<i64>,
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
            rise: 0,
            extreme: None,
        }
    }

    /// Г-135: отход на `rise_bps` от экстремума сделок окна (в тиках — как `depth_ticks`, не меньше 1).
    /// `long` — сторона сделки (стена-бид, свип вниз, вход на отходе вверх).
    pub fn postsweep(long: bool, level_tick: i64, rise_bps: u32, window: (i64, i64)) -> Self {
        Self {
            rise: depth_ticks(level_tick, rise_bps),
            ..Self::new(long, level_tick, 0, 0, window)
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
        if self.rise > 0 {
            let ext = match self.extreme {
                Some(e) if self.long => e.min(t.tick),
                Some(e) => e.max(t.tick),
                None => t.tick,
            };
            self.extreme = Some(ext);
            let off = if self.long {
                t.tick - ext
            } else {
                ext - t.tick
            };
            return (off >= self.rise).then_some(t.ts_ns);
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

/// Момент входа `t0` (нс) Г-135 по готовой ленте или `None` — свёртка `ReclaimWatch::postsweep` (разборы и тесты).
pub fn postsweep_t0(
    trades: &[TradeTick],
    long: bool,
    level_tick: i64,
    rise_bps: u32,
    window: (i64, i64),
) -> Option<i64> {
    let mut w = ReclaimWatch::postsweep(long, level_tick, rise_bps, window);
    trades.iter().find_map(|&t| w.feed(t))
}

#[cfg(test)]
mod tests;
