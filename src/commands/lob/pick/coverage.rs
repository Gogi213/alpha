//! `lob pick` — покрытие топ-50 книги в bps (шаг 0.4) и пригодность корзин
//! расстояния Decision 26а. Обе величины — чистые функции от метаданных
//! инструмента (шаг цены, последняя цена), считаются на стадии построения
//! пула (`super::pool::build_pool`) и переиспользуются при сборке
//! коммитимой таблицы (`super::table`).

use super::pool::PoolCandidate;

/// Покрытие топ-50 в bps по шагу 0.4: `50 × tickSize / цена × 10⁴`.
/// Ширина одной стороны книги в базисных пунктах — та величина, внутрь
/// которой Decision 26а требует целиком укладывать корзину расстояния.
pub fn coverage_top50_bps(tick_e9: i64, last_price_e9: i64) -> Option<f64> {
    if tick_e9 <= 0 || last_price_e9 <= 0 {
        return None;
    }
    // Масштабы e9 (~1e4–1e14) точны в f64; формула — та же, что в отчёте.
    #[allow(clippy::cast_precision_loss)]
    let (tick, price) = (tick_e9 as f64, last_price_e9 as f64);
    Some(50.0 * tick / price * 1e4)
}

/// Корзины расстояния Decision 26 (bps): метка и верхняя граница.
/// Границы назначены планом до данных и не пересматриваются.
pub const DISTANCE_BASKETS: &[(&str, f64)] = &[
    ("0-1", 1.0),
    ("1-2.5", 2.5),
    ("2.5-5", 5.0),
    ("5-10", 10.0),
    ("10-25", 25.0),
];

/// Пригодные корзины расстояния для инструмента (Decision 26а): корзина
/// пригодна, только если целиком попадает внутрь его покрытия топ-50, то
/// есть её верхняя граница не выходит за покрытие. Непригодная корзина не
/// печатается ни строкой таблицы, ни нулём — её там нет, а не «нет сигнала»,
/// поэтому пустое покрытие (`None`) даёт пустой список, а не все корзины.
/// Равенство границы покрытию — пригодна: корзина `[a,b)` при покрытии ровно
/// `b` вся наблюдается.
pub fn eligible_baskets(coverage_bps: Option<f64>) -> Vec<&'static str> {
    match coverage_bps {
        Some(c) => DISTANCE_BASKETS
            .iter()
            .filter(|(_, upper)| *upper <= c)
            .map(|(label, _)| *label)
            .collect(),
        None => Vec::new(),
    }
}

/// Число испытаний для DSR (Decision 26а): количество пригодных пар
/// (инструмент, корзина), а не номинальный крест. Считается по тем же
/// покрытиям, что лежат в пуле, — не разбором строк таблицы.
pub fn count_eligible_trials(pool: &[PoolCandidate]) -> usize {
    pool.iter()
        .map(|c| eligible_baskets(c.coverage_top50_bps).len())
        .sum()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn e9(dollars: i64) -> i64 {
        dollars * 1_000_000_000
    }

    // -- coverage_top50_bps (шаг 0.4) ----------------------------------------

    fn assert_bps_close(got: Option<f64>, expected: f64) {
        let got = got.expect("покрытие обязано посчитаться на положительных входе");
        assert!(
            (got - expected).abs() < 1e-9,
            "покрытие {got} bps далеко от ожидаемых {expected} bps"
        );
    }

    #[test]
    fn coverage_follows_the_plan_formula() {
        // 50 × 0.01 / 125 × 10⁴ = 40 bps ровно.
        assert_bps_close(coverage_top50_bps(10_000_000, 125_000_000_000), 40.0);
    }

    #[test]
    fn coverage_is_none_without_a_positive_tick_or_price() {
        assert_eq!(coverage_top50_bps(0, e9(100)), None);
        assert_eq!(coverage_top50_bps(10_000_000, 0), None);
        assert_eq!(coverage_top50_bps(-1, e9(100)), None);
    }

    /// Дефолтный `meta()` (тик 0.01, цена 100) даёт покрытие ровно 50 bps —
    /// пин, на который опирается сквозной тест таблицы ниже.
    #[test]
    fn default_meta_coverage_covers_the_whole_basket_grid() {
        assert_bps_close(coverage_top50_bps(10_000_000, e9(100)), 50.0);
    }

    // -- eligible_baskets / count_eligible_trials (Decision 26а) ---------------

    #[test]
    fn narrow_coverage_admits_only_the_near_baskets() {
        // Замер 2026-09-10 из Decision 26а: покрытие ZECUSDT — 4.0 bps.
        assert_eq!(eligible_baskets(Some(4.0)), vec!["0-1", "1-2.5"]);
    }

    #[test]
    fn wide_coverage_admits_the_whole_grid() {
        // Замер 2026-09-10 из Decision 26а: покрытие NEARUSDT — 200.2 bps.
        assert_eq!(
            eligible_baskets(Some(200.2)),
            vec!["0-1", "1-2.5", "2.5-5", "5-10", "10-25"]
        );
    }

    #[test]
    fn sub_tick_coverage_admits_no_basket() {
        // BTCUSDT из живого прогона ревизии 17а: топ-50 покрывает 0.6 bps —
        // даже ближайшая корзина [0,1) там не существует целиком.
        assert!(eligible_baskets(Some(0.6)).is_empty());
    }

    #[test]
    fn basket_on_the_exact_boundary_is_eligible() {
        // Корзина [a,b) при покрытии ровно b вся наблюдается.
        assert_eq!(eligible_baskets(Some(1.0)), vec!["0-1"]);
        assert_eq!(
            eligible_baskets(Some(25.0)),
            vec!["0-1", "1-2.5", "2.5-5", "5-10", "10-25"]
        );
        assert_eq!(
            eligible_baskets(Some(24.999)),
            vec!["0-1", "1-2.5", "2.5-5", "5-10"]
        );
    }

    #[test]
    fn unknown_coverage_admits_no_basket_rather_than_all() {
        // Непригодная корзина не печатается вовсе — ни строкой, ни нулём, —
        // поэтому неизвестное покрытие даёт пустой список, а не полный:
        // полный раздувал бы поправку DSR несуществующими испытаниями.
        assert!(eligible_baskets(None).is_empty());
    }

    fn pool_member(symbol: &str, coverage_bps: Option<f64>) -> PoolCandidate {
        PoolCandidate {
            symbol: symbol.to_string(),
            turnover_24h_usd_e9: e9(1_000),
            tick_e9: 10_000_000,
            last_price_e9: e9(100),
            coverage_top50_bps: coverage_bps,
            min_order_qty_e9: 100_000_000,
            qty_step_e9: 100_000_000,
            min_notional_value_e9: e9(5),
        }
    }

    #[test]
    fn eligible_trials_count_sums_suitable_pairs_not_the_nominal_cross() {
        // ZEC-подобный (2 корзины) + NEAR-подобный (5 корзин) = 7 испытаний,
        // а не номинальный крест.
        let pool = vec![
            pool_member("ZECUSDT", Some(4.0)),
            pool_member("NEARUSDT", Some(200.2)),
        ];
        assert_eq!(count_eligible_trials(&pool), 7);
    }

    #[test]
    fn eligible_trials_count_ignores_members_without_coverage() {
        let pool = vec![
            pool_member("KNOWNUSDT", Some(5.0)),
            pool_member("UNKNOWNUSDT", None),
        ];
        assert_eq!(count_eligible_trials(&pool), 3);
    }
}
