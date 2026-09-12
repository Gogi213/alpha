use super::*;

/// Возраст: границы В-44 — `[0, 10 мин)`, `[10 мин, 1 ч)`, `[1 ч, ∞)`,
/// нижняя включительно, верхняя исключительно.
#[test]
fn age_buckets_follow_the_ten_minute_and_one_hour_bounds() {
    assert_eq!(age_bucket(0), Some("[0,10m)"));
    assert_eq!(age_bucket(599_999), Some("[0,10m)"));
    assert_eq!(
        age_bucket(600_000),
        Some("[10m,1h)"),
        "10 мин — включительно"
    );
    assert_eq!(age_bucket(3_599_999), Some("[10m,1h)"));
    assert_eq!(
        age_bucket(3_600_000),
        Some("[1h,inf)"),
        "1 ч — включительно"
    );
    assert_eq!(age_bucket(i64::MAX - 1), Some("[1h,inf)"));
    assert_eq!(age_bucket(-1), None, "касание раньше рождения — никуда");
    assert_eq!(AGE_LABELS.len(), AGE_BOUNDS_MS.len());
}

/// Фронтран: ровно ноль — своя корзина; половина размера — граница
/// включительно вверх (D 03:57: лям и 500 тысяч фронтрана).
#[test]
fn frontrun_buckets_split_zero_and_half_of_the_size() {
    assert_eq!(frontrun_share(0, 10), Some(0.0));
    assert_eq!(frontrun_share(5, 10), Some(0.5));
    assert_eq!(frontrun_share(3, 0), None, "размера нет — доли нет");
    assert_eq!(frontrun_share(0, 0), None);

    assert_eq!(frontrun_bucket(0.0), Some("0"));
    assert_eq!(frontrun_bucket(f64::EPSILON), Some("(0,0.5)"));
    assert_eq!(frontrun_bucket(0.499_999), Some("(0,0.5)"));
    assert_eq!(
        frontrun_bucket(0.5),
        Some("[0.5,inf)"),
        "половина — включительно"
    );
    assert_eq!(frontrun_bucket(7.0), Some("[0.5,inf)"));
    assert_eq!(frontrun_bucket(-0.1), None);
    assert_eq!(frontrun_bucket(f64::NAN), None);
    assert_eq!(
        frontrun_bucket(frontrun_share(4, 10).unwrap()),
        Some("(0,0.5)")
    );
    assert_eq!(
        frontrun_bucket(frontrun_share(10, 10).unwrap()),
        Some("[0.5,inf)")
    );
}

/// Круглость: 0 / 1 / 2+ — тотально, включая потолок `ROUND_ZEROS_CAP`.
#[test]
fn round_buckets_are_zero_one_and_two_or_more() {
    assert_eq!(round_bucket(0), "0");
    assert_eq!(round_bucket(1), "1");
    assert_eq!(round_bucket(2), ">=2");
    assert_eq!(round_bucket(crate::lob::levels::ROUND_ZEROS_CAP), ">=2");
    assert_eq!(round_bucket(u8::MAX), ">=2");
}

/// Номер касания: `touch_index` 0 → «1-е», 1–2 → «2–3-е», ≥ 3 → «4-е+»
/// (S 12:50: два-три раза максимум, четвёртый — риск).
#[test]
fn touch_index_buckets_are_first_second_to_third_and_fourth_plus() {
    assert_eq!(touch_index_bucket(0), "1");
    assert_eq!(touch_index_bucket(1), "2-3");
    assert_eq!(touch_index_bucket(2), "2-3");
    assert_eq!(touch_index_bucket(3), ">=4", "четвёртое касание — индекс 3");
    assert_eq!(touch_index_bucket(u32::MAX), ">=4");
}

/// Подход за 1 с: `<0` отдельно; дальше первые три границы
/// `DISTANCE_BOUNDS_BPS` — 0, 1, 2.5 — нижняя включительно, верхняя
/// исключительно, последняя открыта вверх.
#[test]
fn approach_buckets_reuse_the_first_three_distance_bounds() {
    assert_eq!(approach_bucket(f64::NEG_INFINITY), Some("<0"));
    assert_eq!(approach_bucket(-0.000_1), Some("<0"));
    assert_eq!(approach_bucket(0.0), Some("[0,1)"), "ноль — не «уходила»");
    assert_eq!(approach_bucket(0.999), Some("[0,1)"));
    assert_eq!(approach_bucket(1.0), Some("[1,2.5)"));
    assert_eq!(approach_bucket(2.499), Some("[1,2.5)"));
    assert_eq!(approach_bucket(2.5), Some("[2.5,inf)"));
    assert_eq!(approach_bucket(f64::INFINITY), Some("[2.5,inf)"));
    assert_eq!(approach_bucket(f64::NAN), None);
    assert_eq!(APPROACH_BOUNDS_BPS[1].1, DISTANCE_BOUNDS_BPS[0].1);
    assert_eq!(APPROACH_BOUNDS_BPS[2].0, DISTANCE_BOUNDS_BPS[1].0);
    assert_eq!(APPROACH_BOUNDS_BPS[2].1, DISTANCE_BOUNDS_BPS[1].1);
    assert_eq!(APPROACH_BOUNDS_BPS[3].0, DISTANCE_BOUNDS_BPS[2].0);
    assert_eq!(APPROACH_LABELS.len(), APPROACH_BOUNDS_BPS.len());
}

/// Исход: смерть в касании — `eaten`, иначе `bounced`; порядок меток —
/// отскок первым.
#[test]
fn touch_outcome_is_bounced_unless_the_level_died() {
    assert_eq!(touch_outcome(false), "bounced");
    assert_eq!(touch_outcome(true), "eaten");
    assert_eq!(TOUCH_OUTCOME_LABELS, ["bounced", "eaten"]);
}

/// Сетка профилей касаний (таск 37): на каждую область — пул и каждый
/// инструмент — по маргиналу на корзину каждой из девяти осей плюс крест
/// исход × возраст; длина — из длин массивов корзин, для десяти
/// инструментов `(26 + 6) × 11 = 352`; порядок детерминирован, повторов нет.
#[test]
fn touch_profile_grid_has_marginals_and_the_outcome_age_cross_per_scope() {
    let symbols = vec!["ZECUSDT".to_string(), "SOLUSDT".to_string()];
    let grid = touch_profile_grid(&symbols);
    assert_eq!(grid.len(), touch_grid_size(2));
    assert_eq!(
        touch_grid_size(0),
        32,
        "26 корзин девяти осей + 6 клеток креста"
    );
    assert_eq!(touch_grid_size(10), 352);
    let mut sorted = grid.clone();
    sorted.sort();
    sorted.dedup();
    assert_eq!(grid, sorted, "по возрастанию, без повторов");
    for scope in ["pool", "SOLUSDT", "ZECUSDT"] {
        for (axis, labels) in TOUCH_AXES {
            for label in labels {
                let id = touch_marginal_id(scope, axis, label);
                assert!(grid.contains(&id), "нет маргинала {id}");
            }
        }
        for outcome in TOUCH_OUTCOME_LABELS {
            for age in AGE_LABELS {
                let id = touch_cross_id(scope, outcome, age);
                assert!(grid.contains(&id), "нет клетки {id}");
            }
        }
    }
    assert_eq!(
        touch_marginal_id("pool", "side", "bid"),
        "pool:marginal:side=bid"
    );
    assert_eq!(
        touch_cross_id("ZECUSDT", "bounced", "[1h,inf)"),
        "ZECUSDT:cross:bounced|[1h,inf)"
    );
    assert_eq!(
        touch_profile_grid(&[]).len(),
        touch_grid_size(0),
        "пустой пул — только область pool"
    );
    let axes: Vec<&str> = TOUCH_AXES.iter().map(|(a, _)| *a).collect();
    assert_eq!(
        axes,
        ["side", "size", "age", "frontrun", "round", "index", "approach", "duration", "outcome"],
        "девять осей В-44 в фиксированном порядке"
    );
}
