use super::*;

// -----------------------------------------------------------------
// `sessions_needed_for_profile`, `g0_verdict`, `power_b_gap` — синтетика.
// -----------------------------------------------------------------

#[test]
fn sessions_needed_rounds_up_and_rejects_nonpositive_rate() {
    assert_eq!(sessions_needed_for_profile(2.0, 10.0, 100), Some(5));
    // 21 наблюдение/сессия -> ceil(100/21) = 5, не 4: округление вверх.
    assert_eq!(sessions_needed_for_profile(2.1, 10.0, 100), Some(5));
    assert_eq!(sessions_needed_for_profile(0.0, 10.0, 100), None);
    assert_eq!(sessions_needed_for_profile(-1.0, 10.0, 100), None);
    assert_eq!(sessions_needed_for_profile(1.0, 0.0, 100), None);
}

/// Решение владельца 2026-09-12: хвост боевого пилота, засчитанный в
/// замер, — половина длительности записи, не второй час фиксированно
/// (прежняя `BATTLE_COUNTED_TAIL_MINUTES = 60`). §11 плана называет
/// частный случай двухчасового пилота (120 → 60, ровно второй час);
/// тридцатиминутный пилот владельца (30 → 15) — тот же расчёт, не второе
/// правило.
#[test]
fn battle_counted_tail_minutes_is_half_the_window() {
    assert_eq!(battle_counted_tail_minutes(120.0), 60.0);
    assert_eq!(battle_counted_tail_minutes(30.0), 15.0);
}

/// Шаг 0 таска 09(в): `--hours` не может выразить 30 минут
/// (`hours: u64`). Боевое окно обязано прийти из `session.json` самого
/// каталога — `pilot_minutes = 30` → окно 30 минут, зачётный хвост
/// (`battle_counted_tail_minutes`) — 15.
#[test]
fn battle_window_reads_pilot_minutes_from_session_json() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("session.json"),
        r#"{"started_utc":"2026-09-08T00:00:00Z","start_hour_utc":0,"duration_s":1800,
           "instruments":["SOLUSDT"],"records_total":0,"gaps":0,"clock_samples":0,
           "parse_p99_ns":0,"queue_p99_ns":0,"cpu_pct_avg":0.0,"cpu_pct_max":0.0,
           "rss_bytes_start":0,"rss_bytes_end":0,"out":".","debug":true,
           "pilot":true,"pilot_minutes":30}"#,
    )
    .unwrap();
    let window = resolve_battle_window_minutes(dir.path(), 2);
    assert_eq!(
        window, 30.0,
        "pilot_minutes из session.json обязан победить --hours"
    );
    assert_eq!(battle_counted_tail_minutes(window), 15.0);
}

/// Сессия без `pilot_minutes` (обычный `lob session --minutes`) — окно
/// из `duration_s / 60`, а не из `--hours`.
#[test]
fn battle_window_falls_back_to_duration_s_when_not_a_pilot_session() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("session.json"),
        r#"{"started_utc":"2026-09-08T00:00:00Z","start_hour_utc":0,"duration_s":600,
           "instruments":["SOLUSDT"],"records_total":0,"gaps":0,"clock_samples":0,
           "parse_p99_ns":0,"queue_p99_ns":0,"cpu_pct_avg":0.0,"cpu_pct_max":0.0,
           "rss_bytes_start":0,"rss_bytes_end":0,"out":".","debug":true}"#,
    )
    .unwrap();
    let window = resolve_battle_window_minutes(dir.path(), 2);
    assert_eq!(window, 10.0);
}

/// Нет `session.json` под `root` вовсе (каталог ещё не содержит
/// записи) — `--hours` остаётся запасным путём, как до этой правки.
#[test]
fn battle_window_falls_back_to_hours_when_session_json_is_absent() {
    let dir = tempfile::tempdir().unwrap();
    let window = resolve_battle_window_minutes(dir.path(), 2);
    assert_eq!(window, 120.0);
}

#[test]
fn g0_verdict_reports_sparse_when_median_rate_is_low() {
    let v = g0_verdict(&[50.0, 60.0, 70.0], Some(20.0));
    assert!(v.starts_with("G0 RED (sparse)"), "{v}");
}

#[test]
fn g0_verdict_reports_edge_red_when_rate_ok_but_net_is_negative() {
    let rates = vec![250.0, 260.0, 270.0, 280.0, 290.0];
    let v = g0_verdict(&rates, Some(-0.5));
    assert!(v.starts_with("G0 RED (edge)"), "{v}");
}

#[test]
fn g0_verdict_passes_when_both_conditions_hold() {
    let rates = vec![250.0, 260.0, 270.0, 280.0, 290.0];
    let v = g0_verdict(&rates, Some(0.5));
    assert!(v.starts_with("G0 pass"), "{v}");
}

/// Дозапрос по ревью таска 09(а), ось Данные: `g0_verdict` обязан судить
/// по `net` (издержки Decision 15 плюс проскальзывание, как в
/// `costs::net_bps`), а не по сырому `m_10s` — план требует «`m_10s` ≥
/// 7.5 bps + проскальзывание», и голое сравнение с 7.5 пропускало бы
/// слагаемое проскальзывания. Здесь `m_bps = 10.0` уже выше 7.5 (старое,
/// ошибочное условие дало бы "pass"), но узкая база относительно
/// широкого выхода (`spread=3`, `mid2x=1000`) даёт проскальзывание
/// 50 bps: `net = 10.0 − 7.5 − 50.0 = −47.5 < 0` — обязан быть "RED
/// (edge)".
#[test]
fn g0_verdict_reds_on_edge_when_m10s_beats_roundtrip_but_net_is_negative_after_slippage() {
    let obs = [Observation {
        m_bps: 10.0,
        spread_ticks_exit: 3,
        mid2x_base: 1_000,
    }];
    let net = mean_net_bps(&obs);
    assert!(
        net.unwrap() < 0.0,
        "проверка фикстуры: net обязан быть отрицателен"
    );
    let rates = vec![250.0, 260.0, 270.0, 280.0, 290.0];
    let v = g0_verdict(&rates, net);
    assert!(v.starts_with("G0 RED (edge)"), "{v}");
}

/// Обратный случай той же фикстуры: тот же `m_bps = 10.0`, но глубокая
/// база относительно узкого выхода (`spread=3`, `mid2x=100_000`) даёт
/// проскальзывание 0.5 bps: `net = 10.0 − 7.5 − 0.5 = 2.0 >= 0` — "pass".
#[test]
fn g0_verdict_passes_on_edge_when_net_after_slippage_is_nonnegative() {
    let obs = [Observation {
        m_bps: 10.0,
        spread_ticks_exit: 3,
        mid2x_base: 100_000,
    }];
    let net = mean_net_bps(&obs);
    assert!(
        net.unwrap() >= 0.0,
        "проверка фикстуры: net обязан быть неотрицателен"
    );
    let rates = vec![250.0, 260.0, 270.0, 280.0, 290.0];
    let v = g0_verdict(&rates, net);
    assert!(v.starts_with("G0 pass"), "{v}");
}

#[test]
fn power_b_gap_is_required_minus_measured() {
    assert!((power_b_gap(1.0, 3.0) - 2.0).abs() < 1e-9);
    assert!(
        power_b_gap(4.0, 3.0) < 0.0,
        "планка достигнута — разрыв отрицателен"
    );
}

// -----------------------------------------------------------------
// `process_instrument` — тот же шов (`test_support::write_day`), что
// `levels`/`markout`/`watch`; без сети, `verify_root` — суточные файлы.
// -----------------------------------------------------------------

fn write_instruments_csv_with_h3_lots(root: &std::path::Path, symbol: &str, h3_lots: i64) {
    std::fs::write(
        crate::commands::record::instruments_csv_path(root),
        format!(
            "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n\
             {symbol},0.01,0.1,0.1,5,{h3_lots}\n"
        ),
    )
    .unwrap();
}

// -----------------------------------------------------------------
// Сетка `k` (таск 18, В-30/D05): `k_grid_for_instrument`,
// `summarize_k_grid`, `choose_k`, `format_k_grid_lines` — синтетика.
// -----------------------------------------------------------------

fn write_instruments_csv_with_median_trade_lots(
    root: &std::path::Path,
    symbol: &str,
    median_trade_lots: i64,
) {
    std::fs::write(
        crate::commands::record::instruments_csv_path(root),
        format!(
            "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots,k,median_trade_lots\n\
             {symbol},0.01,0.1,0.1,5,,,{median_trade_lots}\n"
        ),
    )
    .unwrap();
}

/// Критерий приёмки таска 18: ставка (число рождённых уровней) не
/// растёт с `k` — фикстура `three_level_frames` рождает уровни только
/// при размере строго больше порога, максимум размера в ней — 10 лотов,
/// так что при `median_trade_lots=1` пороги `K_GRID` дают `[2,5,10,20,
/// 50]`: рождения есть на `k=2,5` (порог < 10) и ровно ноль на `k>=10`
/// (порог рождения строгий — 10 не рождает уровень с максимумом 10).
#[test]
fn k_grid_for_instrument_rate_is_non_increasing_in_k() {
    let dir = tempfile::tempdir().unwrap();
    super::super::test_support::write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &super::super::test_support::three_level_frames(),
    );
    write_instruments_csv_with_median_trade_lots(dir.path(), "SOLUSDT", 1);
    let rows = k_grid_for_instrument(dir.path(), "SOLUSDT", 3_600_000, 5.0, None)
        .expect("сетка обязана посчитаться на фикстуре");
    assert_eq!(rows.len(), K_GRID.len());
    for pair in rows.windows(2) {
        assert!(
            pair[0].levels >= pair[1].levels,
            "ставка обязана не расти с k: {rows:?}"
        );
    }
    assert!(rows[0].levels > 0, "на малом k уровни обязаны рождаться");
    assert!(
        rows.last().unwrap().levels == 0,
        "на k=50 (порог 50 > максимума фикстуры 10) уровни не рождаются: {rows:?}"
    );
}

#[test]
fn choose_k_picks_the_smallest_k_that_passes_both_gates() {
    let summary = vec![
        KGridSummaryRow {
            k: 2.0,
            median_rate_per_hour: 500.0,
            median_eaten_share: 0.01,
            g0_pass: true,
            g1_pass: false,
        },
        KGridSummaryRow {
            k: 5.0,
            median_rate_per_hour: 300.0,
            median_eaten_share: 0.06,
            g0_pass: true,
            g1_pass: true,
        },
        KGridSummaryRow {
            k: 10.0,
            median_rate_per_hour: 100.0,
            median_eaten_share: 0.08,
            g0_pass: false,
            g1_pass: true,
        },
    ];
    assert_eq!(
        choose_k(&summary),
        Some(5.0),
        "k=2 проваливает G1, k=5 — первый, прошедший оба гейта разом"
    );
}

/// Критерий приёмки таска 18: нет `k`, прошедшего оба гейта разом —
/// `choose_k` обязан вернуть `None` («не определим», красный по
/// построению, не про рынок).
#[test]
fn choose_k_returns_none_when_no_k_passes_both_gates() {
    let summary = vec![
        KGridSummaryRow {
            k: 2.0,
            median_rate_per_hour: 500.0,
            median_eaten_share: 0.01,
            g0_pass: true,
            g1_pass: false,
        },
        KGridSummaryRow {
            k: 50.0,
            median_rate_per_hour: 50.0,
            median_eaten_share: 0.09,
            g0_pass: false,
            g1_pass: true,
        },
    ];
    assert_eq!(choose_k(&summary), None);
}

#[test]
fn format_k_grid_lines_names_the_chosen_k_or_says_not_determined() {
    let passing = vec![KGridSummaryRow {
        k: 5.0,
        median_rate_per_hour: 300.0,
        median_eaten_share: 0.06,
        g0_pass: true,
        g1_pass: true,
    }];
    let chosen = format_k_grid_lines(&[], &passing, false);
    assert!(chosen.iter().any(|l| l.contains("выбран=5")), "{chosen:?}");

    let failing = vec![KGridSummaryRow {
        k: 5.0,
        median_rate_per_hour: 300.0,
        median_eaten_share: 0.01,
        g0_pass: true,
        g1_pass: false,
    }];
    let none = format_k_grid_lines(&[], &failing, true);
    assert!(
        none.iter()
            .any(|l| l.contains("не определим") && l.contains("[debug]")),
        "{none:?}"
    );
}

/// Критерий приёмки таска 18: выбранные режим и `k` заполняют
/// `stage2_preregistration_skeleton` числами, остальные поля остаются
/// плейсхолдером.
#[test]
fn stage2_preregistration_skeleton_fills_h3_mode_and_k_when_selected() {
    let text = stage2_preregistration_skeleton(Some(("floor", 5.0)));
    assert!(text.contains("h3_mode: floor"), "{text}");
    assert!(text.contains("h3_k: 5"), "{text}");
    assert!(
        text.contains("repeat_axis_bounds: <"),
        "остальные поля обязаны остаться плейсхолдером: {text}"
    );
}

#[test]
fn stage2_preregistration_skeleton_keeps_placeholders_when_not_selected() {
    let text = stage2_preregistration_skeleton(None);
    assert!(text.contains("h3_mode: <floor|percentile"), "{text}");
    assert!(text.contains("h3_k: <k для --h3-k"), "{text}");
}

#[test]
fn process_instrument_reports_rates_shares_and_verify_marker() {
    let dir = tempfile::tempdir().unwrap();
    super::super::test_support::write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &super::super::test_support::three_level_frames(),
    );
    write_instruments_csv_with_h3_lots(dir.path(), "SOLUSDT", 5);
    let marker_dir = tempfile::tempdir().unwrap();
    let m = process_instrument(
        dir.path(),
        marker_dir.path(),
        "SOLUSDT",
        0,
        3_600_000,
        5.0,
        None,
    )
    .expect("цепочка обязана пройти на фикстуре");
    assert_eq!(
        m.levels_floor, 4,
        "три уровня фикстуры дают 4 записи (одна reprice)"
    );
    assert!(m.levels_per_min_floor > 0.0);
    assert!((m.share_eaten + m.share_pulled + m.share_mixed - 1.0).abs() < 1e-9);
    assert!(
        m.verify_ok,
        "фикстура без разрывов и нарушений — сверка обязана пройти"
    );
    let marker = std::fs::read_to_string(marker_dir.path().join("verify-SOLUSDT.status")).unwrap();
    assert_eq!(marker, "ok");
}

#[test]
fn process_instrument_names_the_failing_step_on_missing_pool_file() {
    let dir = tempfile::tempdir().unwrap();
    super::super::test_support::write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &super::super::test_support::three_level_frames(),
    );
    // Без instruments.csv режим floor обязан назвать шаг levels_floor,
    // не упасть где попало.
    let marker_dir = tempfile::tempdir().unwrap();
    let err = process_instrument(
        dir.path(),
        marker_dir.path(),
        "SOLUSDT",
        0,
        3_600_000,
        5.0,
        None,
    )
    .unwrap_err();
    assert_eq!(err.step, "levels_floor");
}

/// Регресс на фильтр «второй час» (§11, `counted_tail_cutoff_ms`): тот
/// же фикстурный реплей, но с хвостом настолько узким, что от последнего
/// среза середины (ts=2000, `three_level_frames`) в него попадают только
/// уровни, рождённые практически на самом хвосте — строго меньше, чем
/// без фильтра (`None` выше даёт `levels_floor=4`). Доказывает, что
/// `Some(tail)` действительно режет выборку, а не только меняет знаменатель.
#[test]
fn process_instrument_counted_tail_filters_out_early_births() {
    let dir = tempfile::tempdir().unwrap();
    super::super::test_support::write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &super::super::test_support::three_level_frames(),
    );
    write_instruments_csv_with_h3_lots(dir.path(), "SOLUSDT", 5);
    let marker_dir = tempfile::tempdir().unwrap();

    let unfiltered = process_instrument(
        dir.path(),
        marker_dir.path(),
        "SOLUSDT",
        0,
        3_600_000,
        5.0,
        None,
    )
    .expect("цепочка обязана пройти без фильтра");
    assert_eq!(unfiltered.levels_floor, 4);

    // Хвост в 0.0001 минуты (6мс) от ts=2000 — cutoff=1994: почти все
    // записи фикстуры (рождённые на ts<=1000) обязаны выпасть.
    let filtered = process_instrument(
        dir.path(),
        marker_dir.path(),
        "SOLUSDT",
        0,
        3_600_000,
        5.0,
        Some(0.0001),
    )
    .expect("цепочка обязана пройти с фильтром");
    assert!(
        filtered.levels_floor < unfiltered.levels_floor,
        "хвостовой фильтр обязан уменьшить счёт: {} vs {}",
        filtered.levels_floor,
        unfiltered.levels_floor
    );
}

// -----------------------------------------------------------------
// `copy_pool_instruments_csv` — чистая файловая операция, без сети
// (таск 19, часть 2: замена `alias_dated_binlogs_for_legacy_readers` —
// алиас без даты снят, `backtest.rs`/`profiles.rs`/`watch.rs` находят
// `<SYMBOL>-<день>.binlog` напрямую через `super::session_binlog_for`).
// -----------------------------------------------------------------

#[test]
fn copy_pool_instruments_csv_copies_the_pool_table_and_leaves_the_dated_binlog_untouched() {
    let session_dir = tempfile::tempdir().unwrap();
    let pool_csv = tempfile::NamedTempFile::new().unwrap();
    std::fs::write(pool_csv.path(), "symbol,h3_lots\nSOLUSDT,5\n").unwrap();
    // Настоящий файл сессии — таск 19: `lob session` пишет дату
    // (`day_file_path(.., 1)`, та же функция, что и таск 19 в `session.rs`).
    let real =
        crate::commands::record::day_file_path(session_dir.path(), "SOLUSDT", "2026-09-08", 1);
    std::fs::write(&real, b"stub").unwrap();
    copy_pool_instruments_csv(session_dir.path(), pool_csv.path()).unwrap();
    assert!(session_dir.path().join("instruments.csv").is_file());
    // Файл с датой остаётся на месте и без пары без даты рядом —
    // алиаса больше нет.
    assert!(session_dir
        .path()
        .join("SOLUSDT-2026-09-08.binlog")
        .is_file());
    assert!(!session_dir.path().join("SOLUSDT.binlog").exists());
}

// -----------------------------------------------------------------
// Боевой путь — синтетика, без сети (executor.md: не запускать против
// живой биржи; таск 09 запускает вживую только `--debug`).
// -----------------------------------------------------------------

fn write_pool_instruments_csv(root: &std::path::Path, symbol: &str, h3_lots: i64) {
    std::fs::write(
        crate::commands::record::instruments_csv_path(root),
        format!(
            "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n\
             {symbol},0.01,0.1,0.1,5,{h3_lots}\n"
        ),
    )
    .unwrap();
}

fn battle_args(root: &std::path::Path) -> PilotArgs {
    PilotArgs {
        debug: false,
        pool_instruments: Some(crate::commands::record::instruments_csv_path(root)),
        root: root.to_path_buf(),
        minutes: None,
        base_url: BYBIT_MAINNET_URL.to_string(),
        ntp_addr: "pool.ntp.org:123".to_string(),
        hours: 2,
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        maker_fee_bps: MAKER_FEE_BPS,
        taker_fee_bps: TAKER_FEE_BPS,
        runs_out: root.join("runs.csv"),
        now_utc: Some("2026-09-08T00:00:00Z".to_string()),
        candidates_csv: PathBuf::from("docs/plan/candidates.csv"),
        median_rtt_ns: None,
        p95_rtt_ns: None,
    }
}

#[test]
fn battle_pilot_writes_one_runs_row_per_instrument_and_prints_g0_and_power_b() {
    let dir = tempfile::tempdir().unwrap();
    super::super::test_support::write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &super::super::test_support::three_level_frames(),
    );
    write_pool_instruments_csv(dir.path(), "SOLUSDT", 5);
    let summary =
        run_pilot(&battle_args(dir.path())).expect("боевой путь обязан пройти на фикстуре");
    assert_eq!(summary.symbol, "SOLUSDT");
    let rows = crate::lob::runs::read_run_rows(&dir.path().join("runs.csv")).unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].symbol, "SOLUSDT");
    assert!(summary.verdict.contains("G0"), "{}", summary.verdict);
    assert!(summary.verdict.contains("G-POWER-B"), "{}", summary.verdict);
}

#[test]
fn pilot_rejects_changed_fees_in_either_mode() {
    let dir = tempfile::tempdir().unwrap();
    let mut args = battle_args(dir.path());
    args.taker_fee_bps = 6.0;
    assert!(run_pilot(&args).is_err());
}

#[test]
fn debug_without_pool_instruments_errors_before_touching_the_network() {
    let dir = tempfile::tempdir().unwrap();
    let mut args = battle_args(dir.path());
    args.debug = true;
    args.pool_instruments = None;
    let err = run_pilot(&args).unwrap_err();
    assert!(err.to_string().contains("pool-instruments"), "{err}");
}

#[test]
fn debug_without_minutes_errors_before_touching_the_network() {
    let dir = tempfile::tempdir().unwrap();
    let mut args = battle_args(dir.path());
    args.debug = true;
    args.pool_instruments = Some(crate::commands::record::instruments_csv_path(dir.path()));
    args.minutes = None;
    let err = run_pilot(&args).unwrap_err();
    assert!(err.to_string().contains("minutes"), "{err}");
}

// -----------------------------------------------------------------
// `run_profiles_and_backtest_chain` — хвост G-DEBUG после markout (09(б)).
// Не сетевая: строит ровно ту раскладку `session/` (таск 19: один
// каталог, без `replay/`), которую `run_pilot_debug` готовит на
// настоящей сессии, синтетикой.
// -----------------------------------------------------------------

#[test]
fn debug_chain_backtests_each_symbol_and_never_writes_a_runs_csv() {
    let root = tempfile::tempdir().unwrap();
    let pilot_root = root.path();
    let session_dir = pilot_root.join("session");
    std::fs::create_dir_all(&session_dir).unwrap();

    // Раскладка `lob session` начиная с таска 19: `<SYMBOL>-<день>.binlog`.
    super::super::test_support::write_day(
        &session_dir,
        "SOLUSDT",
        "2026-09-08",
        &super::super::test_support::three_level_frames(),
    );
    write_instruments_csv_with_h3_lots(&session_dir, "SOLUSDT", 5);

    // `process_instrument` пишет levels-floor/-percentile/markout и
    // маркер сверки прямо в `session_dir` — verify_root == marker_dir,
    // мост `stage_session_for_replay` снят (таск 19).
    let m = process_instrument(
        &session_dir,
        &session_dir,
        "SOLUSDT",
        0,
        3_600_000,
        5.0,
        None,
    )
    .expect("цепочка verify->levels->markout обязана пройти на фикстуре");
    assert!(
        m.levels_floor > 0,
        "фикстура обязана дать хотя бы один уровень"
    );

    // Таск 19, часть 2: `backtest.rs::run_backtest` находит
    // `SOLUSDT-2026-09-08.binlog` напрямую через `super::
    // session_binlog_for` — алиас без даты больше не нужен здесь.
    std::fs::write(
        session_dir.join("session.json"),
        r#"{"started_utc":"2026-09-08T00:00:00Z","start_hour_utc":0,"duration_s":300,"instruments":["SOLUSDT"],"records_total":0,"gaps":0,"clock_samples":0,"parse_p99_ns":0,"out":"."}"#,
    )
    .unwrap();

    let candidates_csv = pilot_root.join("candidates.csv");
    std::fs::write(&candidates_csv, "symbol,coverage_top50_bps\nSOLUSDT,50.0\n").unwrap();

    let (lines, defect) = run_profiles_and_backtest_chain(
        pilot_root,
        &["SOLUSDT".to_string()],
        0,
        3_600_000,
        &candidates_csv,
        (Some(1_000_000), Some(2_000_000)),
        "2026-09-08T00:00:00Z",
    );
    assert!(
        defect.is_none(),
        "цепочка обязана пройти: {defect:?} / {lines:?}"
    );
    assert!(
        lines.iter().any(|l| l.contains("backtest")),
        "обязана быть строка про backtest: {lines:?}"
    );
    assert!(
        pilot_root.join("profiles-debug.csv").is_file(),
        "profiles обязан написать артефакт"
    );
    assert!(
        !pilot_root.join(DEBUG_CHAIN_RUNS_SENTINEL).exists(),
        "profiles внутри debug-цепочки не обязан писать runs.csv (allow_unverified=true)"
    );
    assert!(
        !pilot_root.join("runs.csv").exists(),
        "debug-цепочка не обязана писать runs.csv нигде"
    );

    // Таск 19, критерий приёмки: `session -> verify -> levels ->
    // profiles -> watch -> backtest` на каталоге, который писала
    // `lob session`, без ручных шагов. `verify`/`levels` — уже выше
    // через `process_instrument`; `profiles`/`backtest` — уже выше
    // через `run_profiles_and_backtest_chain`; `watch` — здесь, на том
    // же `pilot_root`/`session_dir`, тем же резолвером
    // `super::session_binlog_for`, что и остальные трое.
    let watch_summary = super::super::watch::run_watch(&super::super::watch::WatchArgs {
        root: pilot_root.to_path_buf(),
        symbol: "SOLUSDT".to_string(),
        profile: "marginal:instrument=SOLUSDT".to_string(),
        h3: H3Args {
            h3_mode: H3ModeArg::Floor,
            h3_lots: None,
            h3_usd: None,
            h3_strength_pct: None,
            h3_strength_window_bps: None,
        },
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        now_utc: Some("2026-09-08T00:00:00Z".to_string()),
    })
    .expect("watch обязан найти сессию без ручного переименования бинлога");
    assert_eq!(watch_summary.days, 1, "ровно одни сутки в фикстуре");
    assert!(
        watch_summary.n > 0,
        "фикстура обязана дать хотя бы одно наблюдение профиля"
    );
}

#[test]
fn resolve_backtest_rtt_ns_prefers_probe_then_clock_then_explicit_flags() {
    let dir = tempfile::tempdir().unwrap();
    let session_dir = dir.path();

    // Ни probe, ни clock, ни флагов — громкая ошибка, не тихая подстановка.
    assert!(resolve_backtest_rtt_ns(session_dir, "SOLUSDT", None, None).is_err());

    // Только явные флаги.
    let (m, p, src) = resolve_backtest_rtt_ns(session_dir, "SOLUSDT", Some(10), Some(20))
        .expect("флаги обязаны сработать без probe/clock");
    assert_eq!((m, p, src), (10, 20, "flag"));

    // `clock.csv` перебивает флаги, если он есть.
    std::fs::write(
        session_dir.join("clock.csv"),
        "sample_index,local_ts_ns,ntp_offset_ns,ntp_rtt_ns,ntp_error,bybit_offset_ns,bybit_rtt_ns,bybit_error\n\
         0,1,0,0,,0,100,\n\
         1,2,0,0,,0,200,\n",
    )
    .unwrap();
    let (m, _p, src) = resolve_backtest_rtt_ns(session_dir, "SOLUSDT", Some(10), Some(20))
        .expect("clock.csv обязан сработать");
    assert_eq!(src, "clock");
    // Перцентиль ближайшего ранга (`percentile_of_sorted`, `bybit/probe.rs`):
    // rank = ceil(50 × 2 / 100) = 1 -> первый элемент отсортированной пары.
    assert_eq!(m, 100);

    // `probe-<symbol>.csv` перебивает и clock.csv, и флаги.
    std::fs::write(
        session_dir.join("probe-SOLUSDT.csv"),
        "cycle,rtt_ns\n0,50\n1,60\n",
    )
    .unwrap();
    let (_m, _p, src) = resolve_backtest_rtt_ns(session_dir, "SOLUSDT", Some(10), Some(20))
        .expect("probe csv обязан сработать");
    assert_eq!(src, "probe");
}
