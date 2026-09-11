//! `lob pick` — пол `H3` на инструмент (план D-H3, таск 08) и метка
//! отладочного окна (поправка оркестратора к таску 08). Обе функции чистые:
//! ни сети, ни файлов, проверяются без запуска чего-либо живого.
//!
//! `h3_lots = floor(k × median_trade_lots)` — `k` назначен параметром CLI
//! без умолчания (`PickArgs::h3_k`): ни `PLAN.md` (D-H3), ни спека числа не
//! называют, а изобретённое число запрещено (`interfaces.md`). Медиана
//! приходит от `super::measure` замером ленты `publicTrade` тем же окном,
//! что и глубина.

use super::measure::MEASUREMENT_WINDOW_SECS;

/// Готовый пол `H3` инструмента вместе с тем, из чего он посчитан —
/// коммитимая строка `instruments.csv` (критерий приёмки таска 08: `k` и
/// медиана — колонки, не только итоговое число).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct H3FloorInfo {
    pub k: f64,
    pub median_trade_lots: i64,
    pub h3_lots: i64,
    pub window_start_utc_ms: i64,
    pub window_secs: i64,
}

/// `h3_lots` из медианы размера сделки в лотах и множителя `k`:
/// `floor(k × median)`, целое (A1). `None` — медианы нет (окно не поймало ни
/// одной неблочной сделки) — не 0: подстановка нуля превратила бы «данных
/// нет» в «порог нулевой», ровно та ошибка, которую `median_depth_per_level
/// _usd_e9` (`super::depth`) уже избегает тем же приёмом для глубины.
#[allow(clippy::cast_possible_truncation, clippy::cast_precision_loss)]
pub fn h3_lots_floor(median_trade_lots: Option<i64>, k: f64) -> Option<i64> {
    let median = median_trade_lots?;
    Some((k * median as f64).floor() as i64)
}

/// Предупреждение отладочного окна (поправка оркестратора к таску 08):
/// боевым отбор считается только на `MEASUREMENT_WINDOW_SECS` (3600 с) —
/// любое другое окно не годится для заморозки пула, только для отладки
/// конвейера. Один и тот же текст идёт и в stderr, и первой строкой (`#`) в
/// коммитимые CSV — оператор обязан видеть одно и то же сообщение в обоих
/// местах. `None` на боевом окне — критерий приёмки: «stderr без
/// предупреждения об отладке» на 3600 с.
pub fn debug_window_warning(window_secs: u64) -> Option<String> {
    if window_secs == MEASUREMENT_WINDOW_SECS {
        None
    } else {
        Some(format!(
            "debug: окно {window_secs} с — результат не годится для отбора, только для отладки"
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // -- h3_lots_floor --------------------------------------------------

    #[test]
    fn h3_floor_rounds_the_product_down() {
        // k=1.5, медиана=7 лотов → 10.5 → пол 10, не 11.
        assert_eq!(h3_lots_floor(Some(7), 1.5), Some(10));
    }

    #[test]
    fn h3_floor_on_integer_k_is_exact() {
        assert_eq!(h3_lots_floor(Some(4), 2.0), Some(8));
    }

    #[test]
    fn h3_floor_is_none_without_a_median() {
        assert_eq!(h3_lots_floor(None, 2.0), None);
    }

    // -- debug_window_warning --------------------------------------------

    #[test]
    fn battle_window_has_no_debug_warning() {
        assert_eq!(debug_window_warning(MEASUREMENT_WINDOW_SECS), None);
    }

    #[test]
    fn short_window_warns_and_names_the_second_count() {
        let msg = debug_window_warning(300).expect("короче боевого — обязано быть предупреждение");
        assert!(msg.starts_with("debug:"), "получено: {msg:?}");
        assert!(
            msg.contains("300"),
            "сообщение обязано называть окно: {msg:?}"
        );
    }

    #[test]
    fn longer_than_battle_window_still_warns() {
        // Длиннее боевого — тоже не 3600, тоже не отбор: правило по
        // равенству константе, а не по «короче».
        assert!(debug_window_warning(MEASUREMENT_WINDOW_SECS + 1).is_some());
    }
}
