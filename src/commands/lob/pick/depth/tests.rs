use super::*;

fn e9(dollars: i64) -> i64 {
    dollars * 1_000_000_000
}

// -- median_depth_per_level_usd_e9 --------------------------------------

#[test]
fn median_of_odd_length_is_the_middle_value() {
    let levels = vec![e9(10), e9(30), e9(20)];
    assert_eq!(median_depth_per_level_usd_e9(&levels), Some(e9(20)));
}

#[test]
fn median_of_even_length_averages_the_two_middle_values() {
    let levels = vec![e9(10), e9(20), e9(30), e9(40)];
    assert_eq!(median_depth_per_level_usd_e9(&levels), Some(e9(25)));
}

/// Все уровни одной глубины — книга без формы, вырожденный, но реальный
/// случай (например, все 50 уровней у минимального лота). Медиана обязана
/// вернуть ровно эту глубину, не среднее с искажением от `a + (b-a)/2`.
#[test]
fn median_of_all_equal_values_returns_that_value() {
    let levels = vec![e9(7); 50];
    assert_eq!(median_depth_per_level_usd_e9(&levels), Some(e9(7)));
}

#[test]
fn median_of_empty_input_is_none_not_a_panic() {
    assert_eq!(median_depth_per_level_usd_e9(&[]), None);
}

#[test]
fn median_of_a_single_level_is_that_level() {
    assert_eq!(median_depth_per_level_usd_e9(&[e9(42)]), Some(e9(42)));
}

/// Требуемый тест: медиана и сумма расходятся, и порог обязан применяться
/// к медиане. Один толстый уровень ($50000) плюс 49 тонких ($10 каждый):
/// сумма перескакивает порог $2000 с большим запасом, а медиана — нет,
/// потому что реальная ликвидность у 49 из 50 уровней ничтожна.
#[test]
fn median_disagrees_with_sum_and_the_floor_must_use_the_median() {
    let mut levels = vec![e9(10); 49];
    levels.push(e9(50_000));
    let sum: i64 = levels.iter().sum();
    let median = median_depth_per_level_usd_e9(&levels).unwrap();

    assert_eq!(sum, e9(10) * 49 + e9(50_000));
    assert!(sum >= DEPTH_FLOOR_USD_E9, "сумма прошла бы порог");
    assert_eq!(
        median,
        e9(10),
        "медиана — типичный, а не выдающийся уровень"
    );
    assert!(
        median < DEPTH_FLOOR_USD_E9,
        "медиана обязана провалить порог"
    );
}

/// Крупные значения — без переполнения при усреднении двух средних:
/// `a + (b - a) / 2`, а не `(a + b) / 2`.
#[test]
fn median_does_not_overflow_on_the_largest_i64_values() {
    let levels = vec![i64::MAX - 2, i64::MAX];
    assert_eq!(median_depth_per_level_usd_e9(&levels), Some(i64::MAX - 1));
    let levels_eq = vec![i64::MAX, i64::MAX];
    assert_eq!(median_depth_per_level_usd_e9(&levels_eq), Some(i64::MAX));
}

// -- time_weighted_median_{bid,ask}_depth_usd_e9 -------------------------

#[test]
fn time_weighted_median_weighs_by_duration_to_next_sample() {
    // Один уровень: глубина 10 держится 1с, потом 30 держится 3с — среднее
    // взвешенное (10*1 + 30*3)/4 = 25, не простое среднее (10+30)/2=20.
    // Один уровень на снимок — медиана снимка равна ему самому, порядок
    // операций (Изменение 2) здесь ничего не меняет.
    let samples = vec![
        DepthSample {
            at_ns: 0,
            bid_notional_usd_e9: vec![e9(10)],
            ask_notional_usd_e9: vec![],
        },
        DepthSample {
            at_ns: 1_000_000_000,
            bid_notional_usd_e9: vec![e9(30)],
            ask_notional_usd_e9: vec![],
        },
    ];
    let window_end_ns = 4_000_000_000;
    assert_eq!(
        time_weighted_median_bid_depth_usd_e9(&samples, window_end_ns),
        Some(e9(25))
    );
}

#[test]
fn time_weighted_median_of_empty_samples_is_none_not_a_panic() {
    assert_eq!(time_weighted_median_bid_depth_usd_e9(&[], 1_000), None);
    assert_eq!(time_weighted_median_ask_depth_usd_e9(&[], 1_000), None);
}

/// Требуемый тест (Изменение 1): толстый бид не должен просочиться в
/// медиану аска через общее хранилище — раньше `DepthSample` нёс обе
/// стороны в одном `Vec` (`level_notional_usd_e9`), собранном
/// `Iterator::chain`, и наблюдение с сотней толстых бидов подняло бы
/// объединённую медиану выше порога, даже если аск тонок или пуст.
#[test]
fn time_weighted_median_does_not_pool_the_two_sides() {
    let samples = vec![DepthSample {
        at_ns: 0,
        bid_notional_usd_e9: vec![DEPTH_FLOOR_USD_E9 * 100; 50], // толстый бид
        ask_notional_usd_e9: vec![e9(1); 50],                    // тонкий аск
    }];
    let window_end_ns = 1_000_000_000;
    assert_eq!(
        time_weighted_median_bid_depth_usd_e9(&samples, window_end_ns),
        Some(DEPTH_FLOOR_USD_E9 * 100)
    );
    assert_eq!(
        time_weighted_median_ask_depth_usd_e9(&samples, window_end_ns),
        Some(e9(1)),
        "медиана аска обязана считаться по уровням аска, не смешиваться с бидом"
    );
}

/// Требуемый тест (Изменение 2, Decision 18в ревизии 10): порядок
/// операций — сначала медиана по уровням КАЖДОГО снимка, затем
/// взвешивание этих скаляров по времени; не наоборот. Числа подобраны
/// так, что два порядка дают разный ответ на одних и тех же данных: A
/// (3 уровня, короче) и B (2 уровня) получают равный вес по времени
/// (1с каждый, window_end = 2с).
///
/// Правильный порядок: median(A=[10,20,30]) = 20, median(B=[1000,2000])
/// = 1000 + (2000-1000)/2 = 1500; взвешенное среднее по равным весам —
/// (20 + 1500) / 2 = 760.
///
/// Обратный порядок (сначала взвесить по времени каждую ПОЗИЦИЮ уровня
/// за оба снимка — с недостающей позицией B[2], учтённой как 0, — и
/// только потом взять медиану по позициям) даёт другое число: позиции
/// [505, 1010, 15], медиана 505. Это и есть старое поведение, которое
/// ревизия 10 отвергла — `assert_ne!` ниже утверждает, что реализация
/// не должна давать этот ответ.
#[test]
fn time_weighted_median_computes_per_snapshot_median_before_time_weighting() {
    let samples = vec![
        DepthSample {
            at_ns: 0,
            bid_notional_usd_e9: vec![e9(10), e9(20), e9(30)],
            ask_notional_usd_e9: vec![],
        },
        DepthSample {
            at_ns: 1_000_000_000,
            bid_notional_usd_e9: vec![e9(1000), e9(2000)],
            ask_notional_usd_e9: vec![],
        },
    ];
    let window_end_ns = 2_000_000_000;

    assert_eq!(
        time_weighted_median_bid_depth_usd_e9(&samples, window_end_ns),
        Some(e9(760)),
        "медиана каждого снимка обязана считаться первой, до взвешивания по времени"
    );
    assert_ne!(
        time_weighted_median_bid_depth_usd_e9(&samples, window_end_ns),
        Some(e9(505)),
        "505 — ответ обратного (отвергнутого ревизией 10) порядка операций"
    );
}

/// Раньше короткий снимок дополнялся нулём на недостающих ПОЗИЦИЯХ
/// уровня, потому что порядок операций был обратным (см. предыдущий
/// тест). В правильном порядке (Decision 18в) у каждого снимка — своя
/// медиана по своим же уровням, дополнять нечем: снимок короче на один
/// уровень просто даёт медиану по тому, что в нём есть, не паникует и
/// не голосует «нулевым» уровнем, которого на бирже не было.
#[test]
fn time_weighted_median_computes_each_snapshot_independently_without_padding() {
    let samples = vec![
        DepthSample {
            at_ns: 0,
            bid_notional_usd_e9: vec![e9(10), e9(20)],
            ask_notional_usd_e9: vec![],
        },
        DepthSample {
            at_ns: 1,
            bid_notional_usd_e9: vec![e9(25)], // короче на один уровень
            ask_notional_usd_e9: vec![],
        },
    ];
    // median(A=[10,20]) = 10 + (20-10)/2 = 15; median(B=[25]) = 25.
    // Равные веса (window_end=2, dtA=dtB=1): (15+25)/2 = 20.
    assert_eq!(
        time_weighted_median_bid_depth_usd_e9(&samples, 2),
        Some(e9(20))
    );
}

// -- depth_check и его вход (таск 27) ------------------------------------

fn measured_sided(
    symbol: &str,
    bid_depth: i64,
    ask_depth: i64,
    turnover: i64,
    events: i64,
    window_secs: i64,
) -> MeasuredCandidate {
    MeasuredCandidate {
        symbol: symbol.to_string(),
        window_start_utc_ms: 0,
        window_secs,
        events,
        median_bid_depth_usd_e9: bid_depth,
        median_ask_depth_usd_e9: ask_depth,
        reported_turnover_usd_e9: turnover,
        median_trade_lots: None,
    }
}

// -- depth_check (таск 27, §9: проверка, а не критерий отбора) -----------

/// Критерий приёмки таска 27 (BUSINESS-TASK §9: «час живой глубины
/// остаётся как проверка, а не как критерий отбора»): глубина ниже
/// порога — метка отчёта, и у неё ровно три состояния. Замер есть и
/// обе стороны не ниже порога — `ok`; замер есть, любая сторона ниже —
/// `below_floor` (толстая сторона не прикрывает тонкую, Decision 18б);
/// замера нет вовсе — `not_measured`, а не `below_floor`: оборвавшееся
/// соединение и тонкая книга — разные факты, и слитые в один они
/// сделали бы отчёт неотличимым от прежнего отсева.
#[test]
fn depth_check_labels_the_three_states_separately() {
    let at_floor = measured_sided(
        "AT_FLOOR",
        DEPTH_FLOOR_USD_E9,
        DEPTH_FLOOR_USD_E9,
        0,
        1,
        3600,
    );
    let thin_ask = measured_sided(
        "THIN_ASK",
        DEPTH_FLOOR_USD_E9 * 10,
        DEPTH_FLOOR_USD_E9 - 1,
        0,
        1,
        3600,
    );
    let thin_bid = measured_sided(
        "THIN_BID",
        DEPTH_FLOOR_USD_E9 - 1,
        DEPTH_FLOOR_USD_E9 * 10,
        0,
        1,
        3600,
    );

    assert_eq!(depth_check(Some(&at_floor)), DepthCheck::Ok);
    assert_eq!(depth_check(Some(&thin_ask)), DepthCheck::BelowFloor);
    assert_eq!(depth_check(Some(&thin_bid)), DepthCheck::BelowFloor);
    assert_eq!(depth_check(None), DepthCheck::NotMeasured);

    // Метки — то, что уезжает колонкой `depth_check` в оба CSV.
    assert_eq!(DepthCheck::Ok.as_str(), "ok");
    assert_eq!(DepthCheck::BelowFloor.as_str(), "below_floor");
    assert_eq!(DepthCheck::NotMeasured.as_str(), "not_measured");
}
