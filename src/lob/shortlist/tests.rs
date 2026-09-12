use super::*;

fn days(prefix: &str, from: u32, to: u32) -> Vec<String> {
    (from..=to).map(|d| format!("{prefix}{d:02}")).collect()
}

fn ten_coverages_all_wide() -> Vec<InstrumentCoverage> {
    [
        "SOLUSDT",
        "ZECUSDT",
        "XRPUSDT",
        "HYPEUSDT",
        "NEARUSDT",
        "DOGEUSDT",
        "VVVUSDT",
        "PUMPFUNUSDT",
        "IOSTUSDT",
        "USELESSUSDT",
    ]
    .iter()
    .map(|s| InstrumentCoverage {
        symbol: (*s).to_string(),
        coverage_bps: 300.0,
    })
    .collect()
}

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() < 1e-9
}

/// Деление 60/40 по календарю до анализа: десять суток дают 6/4, порядок
/// подачи не влияет, граница — между шестыми и седьмыми сутками.
#[test]
fn split_is_calendar_60_40_before_any_analysis() {
    let mut input = days("2026-05-", 1, 10);
    input.reverse();
    let split = split_calendar(&input).expect("десять суток делятся");
    assert_eq!(split.exploratory.len(), 6);
    assert_eq!(split.confirmatory.len(), 4);
    assert_eq!(split.exploratory[0], "2026-05-01");
    assert_eq!(split.exploratory[5], "2026-05-06");
    assert_eq!(split.confirmatory[0], "2026-05-07");
    assert_eq!(split.confirmatory[3], "2026-05-10");
}

/// Мало суток, дубликаты и мусор — отказ, а не сдвиг границы.
#[test]
fn split_refuses_degenerate_input() {
    assert!(split_calendar(&[]).is_err());
    assert!(split_calendar(&["2026-05-01".to_string()]).is_err());
    let dup = vec!["2026-05-01".to_string(), "2026-05-01".to_string()];
    assert_eq!(
        split_calendar(&dup),
        Err(ShortlistError::DuplicateDay {
            day: "2026-05-01".to_string()
        })
    );
    let bad = vec!["2026-05-01".to_string(), "01.05.2026".to_string()];
    assert!(matches!(
        split_calendar(&bad),
        Err(ShortlistError::BadDay { .. })
    ));
    // Двое суток делятся 1/1: обе половины непусты.
    let two = vec!["2026-05-01".to_string(), "2026-05-02".to_string()];
    let split = split_calendar(&two).expect("двое суток делятся");
    assert_eq!(split.exploratory.len(), 1);
    assert_eq!(split.confirmatory.len(), 1);
}

/// Ticket 21 (R57): файл без файла на диске пишется впервые тем же
/// сплитом 60/40, что `split_calendar`, и несёт обе даты границ.
#[test]
fn load_or_write_window_writes_once_from_split_calendar() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("preregistration.md");
    let ten_days = days("2026-05-", 1, 10);
    let window = load_or_write_window(&path, &ten_days, None).expect("первый прогон пишет файл");
    assert_eq!(window.start, "2026-05-01");
    assert_eq!(window.end, "2026-05-10");
    assert_eq!(window_length_days(&window).unwrap(), 10);

    // Новые сутки под root не двигают уже зафиксированное окно — файл
    // только читается дальше.
    let more_days = days("2026-05-", 1, 20);
    let window2 = load_or_write_window(&path, &more_days, None).expect("второй прогон читает файл");
    assert_eq!(window2, window, "окно зафиксировано первым прогоном");
}

/// Ticket 21 (R57): файл с одной границей без конца (`confirmatory:`
/// отсутствует) требует `--window-end` и дописывает его в файл один
/// раз — второй прогон с другим значением флага его не двигает.
#[test]
fn load_or_write_window_requires_and_writes_window_end_once() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("preregistration.md");
    std::fs::write(&path, "exploratory: 2026-05-01,2026-05-02\n").unwrap();

    assert!(
        load_or_write_window(&path, &[], None).is_err(),
        "без --window-end окно не определено"
    );

    let window = load_or_write_window(&path, &[], Some("2026-05-09"))
        .expect("с --window-end окно определяется и дописывается");
    assert_eq!(window.end, "2026-05-09");
    let text = std::fs::read_to_string(&path).unwrap();
    assert!(text.contains("confirmatory: 2026-05-09"), "{text}");

    // Второй прогон с другим значением флага не двигает уже дописанный
    // конец — тот же приём write-once, что граница целиком.
    let window2 = load_or_write_window(&path, &[], Some("2026-05-20"))
        .expect("второй прогон читает уже дописанный конец");
    assert_eq!(window2.end, "2026-05-09", "конец окна дописан один раз");
}

/// R-C ревью таска 06: маргиналы по каждой оси (повторяемость теперь
/// трёхзначна — 29 на десять инструментов) плюс единственный
/// предрегистрированный крест инструмент × исход × расстояние (150 при
/// полной пригодности) — не полный крест семи осей. Формула и
/// построенная сетка обязаны совпасть.
#[test]
fn nominal_grid_is_29_plus_150_for_ten_instruments() {
    assert_eq!(nominal_grid_size(10), 179);
    let grid = build_profile_grid(&ten_coverages_all_wide()).expect("сетка");
    assert_eq!(
        grid.len(),
        179,
        "маргиналы 29 + крест инструмент×исход×расстояние 150"
    );
    assert_eq!(actual_trials(&grid), 179);
    assert!(grid.contains(&"marginal:side=bid".to_string()));
    assert!(grid.contains(&"marginal:dist=[0,1)".to_string()));
    assert!(grid.contains(&"marginal:repeat=2".to_string()));
    assert!(grid.contains(&"cross:SOLUSDT|pulled|[0,1)".to_string()));
}

/// Decision 26а буквально: непригодная корзина отсутствует, а не ноль.
/// ZEC с покрытием 4 bps видит только корзины до 4 bps.
#[test]
fn ineligible_basket_absent_not_zero() {
    let coverages = vec![InstrumentCoverage {
        symbol: "ZECUSDT".to_string(),
        coverage_bps: 4.0,
    }];
    let grid = build_profile_grid(&coverages).expect("сетка");
    // Маргиналы для одного инструмента: 1 + 19 = 20 (повторяемость трёхзначна).
    // Крест: пригодны [0,1) и [1,2.5) × 3 исхода = 6.
    assert_eq!(grid.len(), 20 + 6, "дальние корзины отсутствуют: {grid:?}");
    for bad in ["[2.5,5)", "[5,10)", "[10,25)"] {
        assert!(
            !grid.contains(&format!("cross:ZECUSDT|pulled|{bad}")),
            "непригодная корзина {bad} обязана отсутствовать, а не быть нулём"
        );
    }
    assert!(
        !grid.contains(&"cross:ZECUSDT|pulled|[10,25)".to_string()),
        "непригодная корзина обязана отсутствовать, а не быть нулём"
    );
    assert!(grid.contains(&"cross:ZECUSDT|pulled|[0,1)".to_string()));
    // Пустой пул и плохое покрытие — отказ.
    assert_eq!(build_profile_grid(&[]), Err(ShortlistError::EmptyPool));
    assert_eq!(
        build_profile_grid(&[InstrumentCoverage {
            symbol: "X".to_string(),
            coverage_bps: f64::NAN,
        }]),
        Err(ShortlistError::BadCoverage {
            symbol: "X".to_string()
        })
    );
}

/// Повторяемость — трёхзначная (`1`, `2`, `>=3`), не двузначная: спека
/// «Профиль» и бриф §3 буквально требуют три корзины. `repeat_count`
/// считает прошлые рождения 0-based (`lob/levels.rs`), поэтому «ровно
/// один раз» — `repeat_count == 0`, не `<= 1`.
#[test]
fn repeat_axis_has_three_buckets() {
    assert_eq!(REPEAT_LABELS, ["1", "2", ">=3"]);
    assert_eq!(repeat_bucket(0), "1");
    assert_eq!(repeat_bucket(1), "2");
    assert_eq!(repeat_bucket(2), ">=3");
    assert_eq!(repeat_bucket(100), ">=3");
}

/// Граница пригодности включительно: hi == coverage наблюдаемо.
#[test]
fn eligibility_boundary_is_inclusive() {
    assert!(distance_bucket_eligible(10.0, 5.0, 10.0));
    assert!(!distance_bucket_eligible(9.99, 5.0, 10.0));
    assert!(!distance_bucket_eligible(f64::NAN, 0.0, 1.0));
    assert!(!distance_bucket_eligible(300.0, 2.5, 2.5));
}

/// Шорт-лист только на разведочной: порог n>=100, сортировка, дубликаты
/// сняты; красивый только на подтверждающей сюда не входит по построению
/// (вход — лишь разведочные счётчики).
#[test]
fn shortlist_comes_from_exploratory_only() {
    let expl = vec![
        ExplProfile {
            id: "cross:A|pulled|[0,1)".to_string(),
            n: 150,
            g: 9,
        },
        ExplProfile {
            id: "cross:B|eaten|[1,2.5)".to_string(),
            n: 99,
            g: 20,
        },
        ExplProfile {
            id: "cross:A|pulled|[0,1)".to_string(),
            n: 150,
            g: 9,
        },
    ];
    let list = select_shortlist(&expl);
    assert_eq!(list, vec!["cross:A|pulled|[0,1)".to_string()]);
}

/// Обращение к подтверждающей до заморозки — отказ.
#[test]
fn confirmatory_before_freeze_is_refusal() {
    let conf = vec![ConfProfile {
        id: "x".to_string(),
        n: 150,
        g: 12,
        net_fill: Some(5.0),
        net_fill_lower: Some(1.0),
        observed_sharpe: None,
        order_size_usd: None,
    }];
    assert_eq!(
        confirmatory_table(None, &conf, 178),
        Err(ShortlistError::NotFrozen)
    );
    assert_eq!(require_frozen(None), Err(ShortlistError::NotFrozen));
}

/// Профиль вне шорт-листа на подтверждающей — отказ (механическая
/// заморозка: эквивалент ненулевого кода команды).
#[test]
fn confirmatory_outside_shortlist_is_refusal() {
    let frozen = freeze_shortlist(&["a".to_string()], "commit-1", 178);
    assert!(require_member(&frozen, "a").is_ok());
    assert_eq!(
        require_member(&frozen, "b"),
        Err(ShortlistError::OutOfShortlist {
            id: "b".to_string()
        })
    );
    let conf = vec![ConfProfile {
        id: "b".to_string(),
        n: 200,
        g: 12,
        net_fill: Some(5.0),
        net_fill_lower: Some(1.0),
        observed_sharpe: None,
        order_size_usd: None,
    }];
    assert_eq!(
        confirmatory_table(Some(&frozen), &conf, 178),
        Err(ShortlistError::OutOfShortlist {
            id: "b".to_string()
        })
    );
}

/// Профиль, красивый на разведочной и пустой на подтверждающей,
/// печатается как неподтверждённый, а не исчезает.
#[test]
fn beautiful_exploratory_empty_confirmatory_stays_unconfirmed() {
    let expl = vec![ExplProfile {
        id: "cross:SOLUSDT|pulled|[0,1)".to_string(),
        n: 500,
        g: 15,
    }];
    let ids = select_shortlist(&expl);
    let frozen = freeze_shortlist(&ids, "commit-beautiful", 178);
    // На подтверждающей профиля нет вовсе: подача пуста.
    let rows = confirmatory_table(Some(&frozen), &[], 178).expect("подтв");
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].id, "cross:SOLUSDT|pulled|[0,1)");
    assert_eq!(rows[0].n, 0);
    assert_eq!(rows[0].status, ConfirmStatus::InsufficientData);
    assert!(!rows[0].is_confirmed());
    let line = rows[0].format_line();
    assert!(line.contains("cross:SOLUSDT|pulled|[0,1)"), "{line}");
    // Вердикт по такой таблице — красный по нехватке мощности (R68):
    // измерения не было вовсе, а не «идея проверена и не работает».
    assert_eq!(
        decide_verdict(&rows),
        ShortlistVerdict::RedInsufficientPower
    );
}

/// Статусы §7 плюс поправка DSR (R47, критерий приёмки таска 13): зачёт
/// по n/G, затем знак низа интервала, и только затем — DSR по
/// фактическому `total_trials`. `required_sharpe_for_dsr(1, 100,
/// DSR_TARGET)` — при одном испытании поправки на отбор почти нет
/// (перепроверено в `final_metrics.rs`), это и есть «почти
/// недефлированный» порог сравнения.
#[test]
fn confirm_status_reads_counts_then_lower_bound_then_dsr() {
    let required_at_one_trial = required_sharpe_for_dsr(1, 100, DSR_TARGET).unwrap();
    assert_eq!(
        decide_profile(100, 12, Some(0.1), Some(required_at_one_trial + 0.01), 1),
        ConfirmStatus::Confirmed
    );
    assert_eq!(
        decide_profile(99, 12, Some(5.0), Some(10.0), 1),
        ConfirmStatus::InsufficientData
    );
    assert_eq!(
        decide_profile(100, 6, Some(5.0), Some(10.0), 1),
        ConfirmStatus::InsufficientData
    );
    // Низ интервала неположителен — необходимое условие не выполнено,
    // Шарп даже не сравнивается.
    assert_eq!(
        decide_profile(100, 12, Some(0.0), Some(10.0), 1),
        ConfirmStatus::Unconfirmed
    );
    assert_eq!(
        decide_profile(100, 12, None, Some(10.0), 1),
        ConfirmStatus::Unconfirmed
    );
    assert_eq!(
        decide_profile(100, 12, Some(f64::NAN), Some(10.0), 1),
        ConfirmStatus::Unconfirmed
    );
    // Низ положителен, но Шарп не измерен (модель исполнения не
    // подключена) — подтвердить нечем.
    assert_eq!(
        decide_profile(100, 12, Some(0.1), None, 1),
        ConfirmStatus::Unconfirmed
    );
    // Низ положителен, Шарп измерен, но ниже требуемого при этом N.
    assert_eq!(
        decide_profile(100, 12, Some(0.1), Some(required_at_one_trial - 0.01), 1),
        ConfirmStatus::Unconfirmed
    );
}

/// Критерий приёмки таска 13 буквально: «нижняя граница выше нуля» сама
/// по себе больше не вердикт — порог обязан двигаться фактическим `N`.
/// Тот же наблюдаемый Шарп, которого хватает при одном испытании,
/// обязан перестать подтверждать профиль при 179 испытаниях.
#[test]
fn confirm_status_requires_dsr_correction_not_just_positive_lower_bound() {
    let sr = required_sharpe_for_dsr(1, 100, DSR_TARGET).unwrap() + 0.01;
    assert_eq!(
        decide_profile(100, 12, Some(0.1), Some(sr), 1),
        ConfirmStatus::Confirmed,
        "при одном испытании этого Шарпа обязано хватать"
    );
    assert_eq!(
        decide_profile(100, 12, Some(0.1), Some(sr), 179),
        ConfirmStatus::Unconfirmed,
        "тот же Шарп при 179 испытаниях обязан не пройти — порог сдвинулся DSR"
    );
}

/// Ровно `G_MIN` (7, таск 01: `CONFIRM_MIN_G` сведён к этой константе,
/// объявленной в `stats::G_MIN`) обязано зачитываться, а не отказывать —
/// граница включает минимум. Прежний отдельный порог этого модуля (12)
/// отказал бы ровно на семи годных сутках.
#[test]
fn confirm_status_accepts_exactly_g_min_good_days() {
    assert_eq!(G_MIN, 7);
    let required = required_sharpe_for_dsr(1, 100, DSR_TARGET).unwrap();
    assert_eq!(
        decide_profile(100, G_MIN as u64, Some(0.1), Some(required + 0.01), 1),
        ConfirmStatus::Confirmed
    );
}

/// Вердикт выносит подтверждающая: красный без подтверждённых (про
/// рынок — измерение состоялось), тонкий зелёный ниже H6, полный
/// зелёный не ниже H6.
#[test]
fn verdict_comes_from_confirmatory_only() {
    assert!(close(GREEN_NET_BPS, 3.0));
    let red = vec![ConfirmRow {
        id: "a".to_string(),
        n: 150,
        g: 12,
        net_fill: Some(-1.0),
        net_fill_lower: Some(-2.0),
        status: ConfirmStatus::Unconfirmed,
        order_size_usd: None,
    }];
    assert_eq!(decide_verdict(&red), ShortlistVerdict::RedNoEdge);
    assert_eq!(decide_verdict(&[]), ShortlistVerdict::RedInsufficientPower);
    let thin = vec![ConfirmRow {
        id: "a".to_string(),
        n: 150,
        g: 12,
        net_fill: Some(2.9),
        net_fill_lower: Some(0.5),
        status: ConfirmStatus::Confirmed,
        order_size_usd: Some(10_000.0),
    }];
    assert_eq!(decide_verdict(&thin), ShortlistVerdict::GreenThin);
    assert!(close(thin[0].net_fill_usd().unwrap(), 2.9));
    assert!(close(thin[0].green_threshold_usd().unwrap(), 3.0));
    let green = vec![ConfirmRow {
        id: "a".to_string(),
        n: 150,
        g: 12,
        net_fill: Some(3.0),
        net_fill_lower: Some(0.5),
        status: ConfirmStatus::Confirmed,
        order_size_usd: None,
    }];
    assert_eq!(decide_verdict(&green), ShortlistVerdict::Green);
    assert_eq!(
        green[0].net_fill_usd(),
        None,
        "без размера ордера доллары не считаются"
    );
}

/// Все посчитанные профили идут в runs.csv, и число строк равно
/// фактическому числу испытаний; DSR-вход проверяется на равенство.
#[test]
fn runs_csv_holds_every_counted_profile() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("runs.csv");
    let grid = build_profile_grid(&ten_coverages_all_wide()).expect("сетка");
    log_profile_trials(&path, "2026-05-20T00:00:00Z", &grid).expect("журнал");
    assert_eq!(trials_from_runs_csv(&path), Some(grid.len()));
    assert!(require_dsr_trials(grid.len(), grid.len()).is_ok());
    assert_eq!(
        require_dsr_trials(grid.len() - 28, grid.len()),
        Err(ShortlistError::TrialsMismatch {
            expected: grid.len(),
            got: grid.len() - 28
        })
    );
    // Урезанная сетка (непригодные исключены) даёт меньше строк.
    let narrow = vec![InstrumentCoverage {
        symbol: "ZECUSDT".to_string(),
        coverage_bps: 4.0,
    }];
    let grid2 = build_profile_grid(&narrow).expect("узкая сетка");
    let path2 = dir.path().join("runs2.csv");
    log_profile_trials(&path2, "2026-05-20T00:00:00Z", &grid2).expect("журнал");
    assert_eq!(trials_from_runs_csv(&path2), Some(grid2.len()));
    assert!(grid2.len() < grid.len());
}

/// Обязательный тест ticket 06: число испытаний, которое печатает модуль
/// (`total_trials`: сетка плюс тесты на час), совпадает с числом строк
/// `runs.csv` на общей фикстуре — и профили, и тесты на час идут в один
/// журнал одной и той же строкой-испытанием.
#[test]
fn module_trial_count_matches_runs_csv_row_count() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("runs.csv");
    let narrow = vec![InstrumentCoverage {
        symbol: "ZECUSDT".to_string(),
        coverage_bps: 4.0,
    }];
    let grid = build_profile_grid(&narrow).expect("сетка");
    log_profile_trials(&path, "2026-05-20T00:00:00Z", &grid).expect("журнал профилей");
    let hour_test_ids = ["marginal:instrument=ZECUSDT", "cross:ZECUSDT|..."];
    for id in hour_test_ids {
        log_hour_test(&path, "2026-05-20T00:05:00Z", id, 0.5).expect("журнал часа");
    }
    let printed = total_trials(grid.len(), hour_test_ids.len());
    assert_eq!(trials_from_runs_csv(&path), Some(printed));
}

/// Известная величина, посчитанная вручную: `value = 10 × hour_utc` на
/// семи сутках (`hour_utc = 1..7`) даёт кросс-произведение
/// `10 × (hour − 4)²` по каждым суткам — `[90, 40, 10, 0, 10, 40, 90]`,
/// строго неотрицательное и явно ненулевое в среднем. Сильная линейная
/// связь обязана быть отвергнута на альфе гейта.
#[test]
fn hour_dependence_test_rejects_strong_known_trend() {
    let obs: Vec<HourDayObservation> = (1..=7)
        .map(|d| HourDayObservation {
            day: d,
            hour_utc: d as f64,
            value: 10.0 * d as f64,
        })
        .collect();
    let p = hour_dependence_test(&obs, 999, 7).expect("семь суток — минимум набран");
    assert!(
        p <= GATE_ALPHA,
        "сильный линейный тренд обязан пройти альфу гейта: p={p}"
    );
}

/// Известная величина: `value = (hour − 4)²` — чётная функция от
/// отклонения часа, само отклонение часа — нечётная функция, поэтому
/// сумма их произведений по семи суткам равна нулю ровно по симметрии
/// (`Σ [-15,0,3,0,-3,0,15] = 0`), не по случайности данных. Наблюдаемая
/// при этом варьируется (не вырождена), так что тест обязан вернуть
/// число, а не отказ, и это число не должно проходить альфу.
#[test]
fn hour_dependence_test_does_not_reject_symmetric_no_trend() {
    let obs: Vec<HourDayObservation> = (1..=7)
        .map(|d| {
            let dev = d as f64 - 4.0;
            HourDayObservation {
                day: d,
                hour_utc: d as f64,
                value: dev * dev,
            }
        })
        .collect();
    let p = hour_dependence_test(&obs, 999, 11).expect("наблюдаемая варьируется");
    assert!(
        p > GATE_ALPHA,
        "кросс-произведение по симметрии в среднем нулевое: p={p} обязан быть больше альфы"
    );
}

/// Наблюдаемая, тождественно равная по всем суткам, вырождает
/// кросс-произведение в ноль на каждой сутки — знаменатель теста ноль,
/// и это методический отказ (как `BootstrapError::DegenerateVariance` у
/// гейта G2), а не подделанное число.
#[test]
fn hour_dependence_test_refuses_on_degenerate_variance() {
    let obs: Vec<HourDayObservation> = (1..=7)
        .map(|d| HourDayObservation {
            day: d,
            hour_utc: d as f64,
            value: 5.0,
        })
        .collect();
    assert_eq!(
        hour_dependence_test(&obs, 999, 1),
        Err(HourTestError::DegenerateVariance { days: 7 })
    );
}

/// Меньше `G_MIN` суток — тот же методический отказ, что у гейта G2, а
/// не заниженное число.
#[test]
fn hour_dependence_test_refuses_below_g_min_days() {
    let obs: Vec<HourDayObservation> = (1..=6)
        .map(|d| HourDayObservation {
            day: d,
            hour_utc: d as f64,
            value: 10.0 * d as f64,
        })
        .collect();
    assert_eq!(
        hour_dependence_test(&obs, 999, 1),
        Err(HourTestError::TooFewDays {
            days: 6,
            minimum: G_MIN
        })
    );
    assert_eq!(
        hour_dependence_test(&[], 999, 1),
        Err(HourTestError::TooFewDays {
            days: 0,
            minimum: G_MIN
        })
    );
}

/// Формат журнала: тест на час пишется `RunKind::Confirmatory` — тем же
/// видом строки, что и профиль, — и это испытание считается в
/// `count_trials`.
#[test]
fn hour_test_log_line_counts_as_trial() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("runs.csv");
    log_hour_test(&path, "2026-05-20T00:00:00Z", "marginal:side=bid", 0.12).expect("журнал");
    let rows = crate::lob::runs::read_run_rows(&path).expect("чтение");
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].kind, RunKind::Confirmatory);
    assert!(rows[0].detail.contains("hour_test"));
    assert!(rows[0].detail.contains("marginal:side=bid"));
    assert_eq!(crate::lob::runs::count_trials(&rows), 1);
}

/// Таск 29: `pbo`/`cpcv_oos_sharpe` без числа печатаются **с причиной**,
/// а не молчаливым `none` — читатель шапки видит, чего не хватило
/// (суток меньше, чем блоков перебора), правило пустой ячейки матрицы
/// названо там же, и там же сказано, что порога PBO план не назначал.
#[test]
fn header_says_why_pbo_and_cpcv_are_not_numbers() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("shortlist-2026-05-20.md");
    let frozen = freeze_shortlist(&["cross:A|pulled|[0,1)".to_string()], "abc123", 147);
    let rows = vec![ConfirmRow {
        id: "cross:A|pulled|[0,1)".to_string(),
        n: 0,
        g: 0,
        net_fill: None,
        net_fill_lower: None,
        status: ConfirmStatus::InsufficientData,
        order_size_usd: None,
    }];
    let header = VerdictHeader {
        pbo_na: Some("days=1 < 8".to_string()),
        cpcv_na: Some("days=1 < 4".to_string()),
        pbo_matrix: "pbo_matrix: trials=147 days=1 cells_nan=3 (n=0 за сутки → NaN)".to_string(),
        ..VerdictHeader::default()
    };
    write_shortlist_md(
        &path,
        "2026-05-20",
        &frozen,
        &rows,
        ShortlistVerdict::RedInsufficientPower,
        &header,
    )
    .expect("писатель");
    let text = std::fs::read_to_string(&path).unwrap();
    assert!(text.contains("pbo=n/a (days=1 < 8)"), "{text}");
    assert!(text.contains("cpcv_oos_sharpe=n/a (days=1 < 4)"), "{text}");
    assert!(text.contains("pbo_matrix: trials=147 days=1"), "{text}");
    assert!(
        text.contains("pbo_gate: none"),
        "порог PBO не назначен ни задачей, ни планом — это обязано быть сказано: {text}"
    );
}

/// Формат shortlist MD: число испытаний, коммит, отпечаток, порог,
/// таблица и вердикт; неподтверждённый профиль в таблице остаётся.
#[test]
fn shortlist_md_format_carries_trials_threshold_and_verdict() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("shortlist-2026-05-20.md");
    let frozen = freeze_shortlist(
        &[
            "cross:A|pulled|[0,1)".to_string(),
            "cross:B|eaten|[1,2.5)".to_string(),
        ],
        "abc123",
        178,
    );
    let rows = vec![
        ConfirmRow {
            id: "cross:A|pulled|[0,1)".to_string(),
            n: 150,
            g: 12,
            net_fill: Some(4.0),
            net_fill_lower: Some(1.0),
            status: ConfirmStatus::Confirmed,
            order_size_usd: Some(10_000.0),
        },
        ConfirmRow {
            id: "cross:B|eaten|[1,2.5)".to_string(),
            n: 0,
            g: 0,
            net_fill: None,
            net_fill_lower: None,
            status: ConfirmStatus::InsufficientData,
            order_size_usd: None,
        },
    ];
    let header = VerdictHeader {
        value_bps: Some(4.0),
        dsr: Some(0.97),
        pbo: Some(0.2),
        cpcv_oos_sharpe: Some(1.1),
        g: Some(12),
        p_grid_resolution: Some(stats::webb_p_grid_resolution(12)),
        jackknife: crate::lob::final_metrics::jackknife_sensitivity(&[
            ("2026-05-18".to_string(), 3.8),
            ("2026-05-19".to_string(), 4.2),
        ]),
        window: "window: 2026-05-01..2026-05-20 days=20 sessions=12 \
                 sessions_outside_window=0 exploratory=2026-05-01..2026-05-15 \
                 confirmatory=2026-05-16..2026-05-20"
            .to_string(),
        pbo_na: None,
        cpcv_na: None,
        cpcv_selection: Some("лучший по среднему net на IS-сутках".to_string()),
        pbo_matrix: String::new(),
    };
    write_shortlist_md(
        &path,
        "2026-05-20",
        &frozen,
        &rows,
        ShortlistVerdict::Green,
        &header,
    )
    .expect("писатель");
    let text = std::fs::read_to_string(&path).unwrap();
    assert!(text.contains("trials: 178"), "{text}");
    assert!(text.contains("freeze_commit: abc123"), "{text}");
    assert!(
        text.contains(&frozen.fingerprint_hex()),
        "отпечаток: {text}"
    );
    assert!(text.contains("net_fill_lower>0"), "{text}");
    assert!(
        text.contains("outcome: GREEN") && text.contains("gate=G3-в"),
        "исход с гейтом обязан быть в шапке: {text}"
    );
    assert!(
        text.contains(&format!("dsr_target: {DSR_TARGET}")),
        "{text}"
    );
    assert!(text.contains("dsr=0.9700"), "{text}");
    assert!(text.contains("pbo=0.2000"), "{text}");
    assert!(
        text.contains("cpcv_oos_sharpe=1.1000 (selection: лучший по среднему net на IS-сутках)"),
        "число CPCV обязано идти с названным правилом отбора: {text}"
    );
    assert!(text.contains("G: 12"), "{text}");
    assert!(text.contains("jackknife_by_day: min=3.8000"), "{text}");
    assert!(
        text.contains("net_fill_usd") && text.contains("green_threshold_usd"),
        "справочные долларовые колонки обязаны быть в таблице: {text}"
    );
    assert!(
        text.contains(
            "| cross:A|pulled|[0,1) | 150 | 12 | 4.0000 | 1.0000 | confirmed | 4.0000 | 3.0000 |"
        ),
        "доллар на 10000$ ордере при net_fill=4bps: {text}"
    );
    // Ревью: порог в шапке обязан идти из тех же констант, что
    // `decide_profile` (`CONFIRM_MIN_N`/`G_MIN`), не литералом — таск 01
    // снёс отдельный `CONFIRM_MIN_G = 12`, и шапка обязана меняться
    // вместе с `G_MIN`, а не расходиться с проверкой.
    assert!(
        text.contains(&format!("threshold: n>={CONFIRM_MIN_N} G>={G_MIN} ")),
        "порог обязан быть собран из CONFIRM_MIN_N/G_MIN, не литералом: {text}"
    );
    assert!(text.contains("cross:A|pulled|[0,1)"), "{text}");
    assert!(
        text.contains("cross:B|eaten|[1,2.5)"),
        "неподтверждённый обязан остаться: {text}"
    );
    assert!(text.contains("verdict: GREEN"), "{text}");
}

/// Заморозка детерминирована: порядок подачи не влияет, дубликаты сняты,
/// отпечаток стабилен.
#[test]
fn freeze_is_deterministic_over_input_order() {
    let a = freeze_shortlist(
        &["b".to_string(), "a".to_string(), "b".to_string()],
        "c",
        178,
    );
    let b = freeze_shortlist(&["a".to_string(), "b".to_string()], "c", 178);
    assert_eq!(a.ids(), &["a".to_string(), "b".to_string()]);
    assert_eq!(a.fingerprint(), b.fingerprint());
    assert_eq!(a.fingerprint_hex().len(), 16);
    assert_eq!(a.trials(), 178);
}

/// Синтетика вместо недель: весь модуль считается на сконструированных
/// входах без файлов недели и без сети — явная печать связки для ревью.
#[test]
fn synthetic_drive_prints_split_and_verdict() {
    let split = split_calendar(&days("2026-06-", 1, 5)).expect("пять суток");
    assert_eq!(split.exploratory.len(), 3);
    assert_eq!(split.confirmatory.len(), 2);
    let frozen = freeze_shortlist(&["p1".to_string()], "synthetic", 25);
    let rows = confirmatory_table(
        Some(&frozen),
        &[ConfProfile {
            id: "p1".to_string(),
            n: 120,
            g: 12,
            net_fill: Some(1.0),
            net_fill_lower: Some(0.2),
            observed_sharpe: Some(5.0),
            order_size_usd: Some(9_926.0),
        }],
        25,
    )
    .expect("подтв");
    let line = format!(
        "split 3/2 {} verdict={}",
        rows[0].format_line(),
        decide_verdict(&rows)
    );
    assert!(line.contains("p1"));
    assert!(line.contains("GREEN-thin"));
}

/// Граница модулей: чистая логика не знает про транспорт и часы.
/// Проверка — грепом по собственному исходнику.
#[test]
fn module_stays_detached_from_transport_and_clocks() {
    const SRC: &str = include_str!("../shortlist.rs");
    let banned = [
        concat!("by", "bit"),
        concat!("tok", "io"),
        concat!("Inst", "ant"),
        concat!("System", "Time"),
        concat!("std::", "time"),
    ];
    for b in banned {
        assert!(!SRC.contains(b), "исходник тянет запрещённое: {b}");
    }
}
