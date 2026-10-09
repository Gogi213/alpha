//! Пакет признаков R1 (TK-025): колонки кэша подходов на кадре взвода (`arm_ms`) — у всех подходов, любой причины конца.
//!
//! Определения «имя → формула → гипотеза» — `docs/findings/tk025-design-2026-10-02.md`. Здесь —
//! единственное место имён и порядка колонок: заголовок CSV, чтение кэша и общий ключ
//! `--set r1_<колонка>_min|_max` берут их отсюда. Все значения целые; [`R1_UNDEF`] — «не
//! определено» (в CSV пустая клетка, любой порог его отвергает).
//!
//! Колонки делятся на две группы по тому, кто их считает: [`FLOW_NAMES`] — состояние монеты
//! (`r1_flow::R1Flow`: лента, книга, поток заявок), [`LEVEL_NAMES`] — состояние уровня
//! (`levels::LevelTracker`). Сторона стены учтена при счёте: «press» — агрессоры против стены,
//! «with» — за стену, направленные величины (`obi*`, `micro_off_cbps`, `ofi_*`, `best_move_*`)
//! ориентированы «от стены» (плюс = в пользу удержания: у бид-стены вверх, у аск-стены вниз).

/// «Не определено» (окно ещё не прогрето, знаменатель ноль, события не было).
pub const R1_UNDEF: i64 = i64::MIN;

/// Число колонок состояния монеты.
pub const FLOW_N: usize = 42;

/// Колонки состояния монеты, порядок = порядок в CSV и в массиве `ArmR1::flow`.
pub const FLOW_NAMES: [&str; FLOW_N] = [
    "tape_press_lots_15s",
    "tape_with_lots_15s",
    "tape_all_lots_15s",
    "tape_press_n_15s",
    "tape_with_n_15s",
    "tape_all_n_15s",
    "tape_press_lots_30s",
    "tape_with_lots_30s",
    "tape_all_lots_30s",
    "tape_press_n_30s",
    "tape_with_n_30s",
    "tape_all_n_30s",
    "tape_press_lots_60s",
    "tape_with_lots_60s",
    "tape_all_lots_60s",
    "tape_press_n_60s",
    "tape_with_n_60s",
    "tape_all_n_60s",
    "tape_press_60m_lots",
    "tape_with_60m_lots",
    "tape_burst_press_15s_bp",
    "tape_burst_with_15s_bp",
    "tape_burst_press_30s_bp",
    "tape_burst_with_30s_bp",
    "tape_press_avg_30s_e2",
    "tape_with_avg_30s_e2",
    "sign_ac_15s_bp",
    "sign_ac_60s_bp",
    "vpin_bp",
    "trade_size_p50_15m",
    "trade_size_p90_15m",
    "obi1_bp",
    "obi5_bp",
    "obi10_bp",
    "obi50_bp",
    "micro_off_cbps",
    "ofi_10s_lots",
    "ofi_60s_lots",
    "best_flips_15s",
    "best_flips_60s",
    // TK-115 (g82-60s): всплеск окна 60 с — в конце, прежние 40 колонок на своих местах.
    "tape_burst_press_60s_bp",
    "tape_burst_with_60s_bp",
];

/// Число колонок состояния уровня.
pub const LEVEL_N: usize = 21;

/// Колонки состояния уровня, порядок = порядок в CSV и в массиве `ArmR1::level`.
pub const LEVEL_NAMES: [&str; LEVEL_N] = [
    "cancel_1s_lots",
    "cancel_3s_lots",
    "cancel_60m_lots",
    "cancel_life_lots",
    "wall_add_max_lots",
    "wall_add_max_age_ms",
    "front_add_max_lots",
    "front_add_max_age_ms",
    "born_shift_cbps",
    "born_shift_lots",
    "prev_death_gap_ms",
    "prev_death_outcome",
    "size_share15_bp",
    "nz_levels15",
    "best_move_1s_cbps",
    "best_move_10s_cbps",
    "opp_wall_dist_cbps",
    "opp_wall_ratio_bp",
    "frontrun_delta_10s_lots",
    "frontrun_levels",
    "since_far_ms",
];

/// Все колонки R1 одного подхода на кадре взвода (`ApproachRecord::r1`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ArmR1 {
    pub flow: [i64; FLOW_N],
    pub level: [i64; LEVEL_N],
}

impl ArmR1 {
    /// Все колонки «не определено» — заготовка, которую заполняют счётчики.
    pub const fn undefined() -> Self {
        Self {
            flow: [R1_UNDEF; FLOW_N],
            level: [R1_UNDEF; LEVEL_N],
        }
    }

    /// Имена всех колонок по порядку CSV: сначала состояние монеты, затем уровня.
    pub fn names() -> impl Iterator<Item = &'static str> {
        FLOW_NAMES.iter().chain(LEVEL_NAMES.iter()).copied()
    }

    /// Значения всех колонок в порядке [`ArmR1::names`].
    pub fn values(&self) -> impl Iterator<Item = i64> + '_ {
        self.flow.iter().chain(self.level.iter()).copied()
    }

    /// Значение колонки по имени; `None` — такой колонки нет.
    pub fn get(&self, name: &str) -> Option<i64> {
        if let Some(i) = FLOW_NAMES.iter().position(|n| *n == name) {
            return Some(self.flow[i]);
        }
        LEVEL_NAMES
            .iter()
            .position(|n| *n == name)
            .map(|i| self.level[i])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_are_unique_and_counted() {
        let all: Vec<&str> = ArmR1::names().collect();
        assert_eq!(all.len(), FLOW_N + LEVEL_N);
        let mut sorted = all.clone();
        sorted.sort_unstable();
        sorted.dedup();
        assert_eq!(
            sorted.len(),
            all.len(),
            "имена колонок R1 не должны повторяться"
        );
    }

    #[test]
    fn get_finds_both_groups() {
        let mut r = ArmR1::undefined();
        r.flow[0] = 7;
        r.level[LEVEL_N - 1] = 9;
        assert_eq!(r.get("tape_press_lots_15s"), Some(7));
        assert_eq!(r.get("since_far_ms"), Some(9));
        assert_eq!(r.get("нет_такой"), None);
        assert_eq!(r.values().count(), FLOW_N + LEVEL_N);
    }
}
