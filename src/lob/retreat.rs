//! Г-102 (В-227 D): память отступа входа после стопа — «расширять требуемый отступ для нового входа, плавно снижать».
//!
//! Чистая арифметика без книги и часов: после стопа отступ входа растёт на `k` тиков и затухает как
//! `floor(k · 2^(−Δt/T))` до целого тика, `Δt` — от закрытия по стопу. Повторный стоп ставит состояние в `k`
//! (не сумму: потолок суммы источник не задаёт). Состояние живёт на (вариант, инструмент) в пределах суток:
//! один драйвер — одна монета одного варианта, перенос через полночь не делается (решение Судьи 13.10);
//! вместо этого `carry_window_ns` даёт окно суток, где сброс мог изменить отступ (счётчик в отчёт счёта).

/// Параметры формы: расширение `k` тиков (1…8) и полураспад `T` (нс).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RetreatCfg {
    pub k_ticks: u32,
    pub half_life_ns: i64,
}

/// Состояние одного (вариант, инструмент) за сутки.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RetreatMemory {
    cfg: RetreatCfg,
    /// Момент последнего закрытия по стопу, нс; `None` — стопа не было.
    last_stop_ns: Option<i64>,
}

impl RetreatMemory {
    pub const fn new(cfg: RetreatCfg) -> Self {
        Self {
            cfg,
            last_stop_ns: None,
        }
    }

    /// Закрытие по стопу в `now_ns`: состояние снова `k`.
    pub fn on_stop(&mut self, now_ns: i64) {
        self.last_stop_ns = Some(now_ns);
    }

    /// Окно суток этой памяти — `carry_window_ns(cfg)`.
    pub fn carry_window_ns(&self) -> i64 {
        carry_window_ns(self.cfg)
    }

    /// Добавка к отступу входа в `now_ns`, целых тиков: `floor(k · 2^(−Δt/T))`.
    #[allow(
        clippy::cast_precision_loss,
        clippy::cast_possible_truncation,
        clippy::cast_sign_loss
    )]
    pub fn offset_ticks(&self, now_ns: i64) -> u32 {
        let Some(stop_ns) = self.last_stop_ns else {
            return 0;
        };
        if self.cfg.half_life_ns <= 0 {
            return 0;
        }
        let dt = now_ns.saturating_sub(stop_ns).max(0);
        let x = dt as f64 / self.cfg.half_life_ns as f64;
        let v = f64::from(self.cfg.k_ticks) * (-x).exp2();
        v.floor() as u32
    }
}

/// Окно от начала суток, нс, где сброс состояния на полуночи мог изменить отступ: после `T · log2(k)` добавка от любого
/// стопа до полуночи уже 0. Входы суток в `[0, окно)` на этой монете — верхняя граница входов, затронутых сбросом.
#[allow(clippy::cast_precision_loss, clippy::cast_possible_truncation)]
pub fn carry_window_ns(cfg: RetreatCfg) -> i64 {
    if cfg.k_ticks <= 1 || cfg.half_life_ns <= 0 {
        return 0;
    }
    (cfg.half_life_ns as f64 * f64::from(cfg.k_ticks).log2()).ceil() as i64
}

#[cfg(test)]
mod tests;
