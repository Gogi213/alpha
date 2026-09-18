use super::*;
use crate::commands::lob::H3ModeArg;

#[test]
fn markout_fixture_writes_four_horizons() {
    let dir = tempfile::tempdir().unwrap();
    let mut frames = super::super::test_support::three_level_frames();
    // Хвост середин до 72 с теми же размерами: ни рождений, ни смертей,
    // но будущие срезы для всех горизонтов есть.
    for k in 1..=7 {
        frames.push(super::super::test_support::delta_frame(
            2000 + k * 10_000,
            &[(96, 1), (98, 1), (99, 1), (100, 1)],
            &[(105, 10)],
        ));
    }
    super::super::test_support::write_day(dir.path(), "SOLUSDT", "2026-09-08", &frames);
    let args = MarkoutArgs {
        root: dir.path().to_path_buf(),
        symbol: "SOLUSDT".to_string(),
        h3: H3Args {
            h3_mode: H3ModeArg::Percentile,
            h3_lots: Some(5),
            h3_usd: None,
            h3_strength_pct: None,
            h3_strength_window_bps: None,
        },
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        out: None,
        confirmatory: false,
        flag: None,
        median_lifetime_ms: None,
        profile: None,
        shortlist: None,
    };
    let summary = run_markout(&args).unwrap();
    assert_eq!(summary.levels, 4);
    let mut reader = csv::Reader::from_path(&summary.out).unwrap();
    let headers = reader.headers().unwrap().clone();
    let col = |name: &str| {
        headers
            .iter()
            .position(|h| h == name)
            .unwrap_or_else(|| panic!("нет колонки {name}"))
    };
    let (m10, tick_col) = (col("m_10s"), col("price_tick"));
    for name in ["m_100ms", "m_1s", "m_10s", "m_60s"] {
        col(name);
    }
    let mut seen_10s = false;
    for rec in reader.records() {
        let rec = rec.unwrap();
        if &rec[tick_col] == "98" {
            let v: f64 = rec[m10].parse().expect("у уровня 98 обязан быть m на 10 с");
            assert!(v.is_finite());
            seen_10s = true;
        }
    }
    assert!(seen_10s, "строка уровня 98 не найдена");
}

#[test]
fn markout_confirmatory_without_flag_is_err() {
    let dir = tempfile::tempdir().unwrap();
    let args = MarkoutArgs {
        root: dir.path().to_path_buf(),
        symbol: "SOLUSDT".to_string(),
        h3: H3Args {
            h3_mode: H3ModeArg::Percentile,
            h3_lots: Some(5),
            h3_usd: None,
            h3_strength_pct: None,
            h3_strength_window_bps: None,
        },
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        out: None,
        confirmatory: true,
        flag: None,
        median_lifetime_ms: Some(60_000),
        profile: Some("marginal:outcome=pulled".to_string()),
        shortlist: None,
    };
    assert!(run_markout(&args).is_err());
    assert!(super::super::dispatch(super::super::LobCommand::Markout(args)).is_err());
}

/// Таск 07: `--confirmatory` обязан требовать сессионный флаг готовности
/// (`ready-<symbol>-<profile>.flag`) отдельно от старого `ready.flag` —
/// без него отказ ненулевым кодом до всякого markout, даже если старый
/// флаг и все данные в порядке; с обоими флагами прогон обязан пройти.
#[test]
fn markout_confirmatory_requires_session_ready_flag() {
    let dir = tempfile::tempdir().unwrap();
    let mut frames = super::super::test_support::three_level_frames();
    for k in 1..=7 {
        frames.push(super::super::test_support::delta_frame(
            2000 + k * 10_000,
            &[(96, 1), (98, 1), (99, 1), (100, 1)],
            &[(105, 10)],
        ));
    }
    super::super::test_support::write_day(dir.path(), "SOLUSDT", "2026-09-08", &frames);
    crate::bybit::verify_sidecar::append_verify_row(
        &crate::bybit::verify_sidecar::verify_csv_path(dir.path()),
        &crate::bybit::verify_sidecar::VerifyRow {
            ts_utc: "2026-09-08T00:05:00Z".to_string(),
            symbol: "SOLUSDT".to_string(),
            snapshot_seq: Some(1),
            book_seq: Some(1),
            mismatches: Some(0),
            verdict: crate::bybit::verify_sidecar::VerifyVerdict::Ok,
        },
    )
    .unwrap();
    crate::lob::watch::write_ready_flag(
        &dir.path().join("ready.flag"),
        &crate::lob::watch::ReadyFlag {
            symbol: "SOLUSDT".to_string(),
            n: 100,
            g: 1,
            ready_at_utc: "2026-09-09T00:00:00Z".to_string(),
            days: vec!["2026-09-08".to_string()],
        },
    )
    .unwrap();
    let args = MarkoutArgs {
        root: dir.path().to_path_buf(),
        symbol: "SOLUSDT".to_string(),
        h3: H3Args {
            h3_mode: H3ModeArg::Percentile,
            h3_lots: Some(5),
            h3_usd: None,
            h3_strength_pct: None,
            h3_strength_window_bps: None,
        },
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        out: None,
        confirmatory: true,
        flag: None,
        median_lifetime_ms: Some(60_000),
        profile: Some("marginal:outcome=pulled".to_string()),
        shortlist: Some(dir.path().join("shortlist-frozen.txt")),
    };
    // Старый флаг и данные в порядке, сессионного флага ещё нет.
    // `MarkoutSummary` не несёт `Debug` (вне зоны этой правки — не
    // добавляю), поэтому без `expect_err`: явный match.
    match run_markout(&args) {
        Err(e) => assert!(
            format!("{e}").contains("сессионный флаг"),
            "сообщение обязано называть причину: {e}"
        ),
        Ok(_) => panic!("без сессионного флага обязан быть отказ"),
    }

    crate::lob::watch::write_session_ready_flag(
        &crate::lob::watch::session_ready_flag_path(
            dir.path(),
            "SOLUSDT",
            "marginal:outcome=pulled",
        ),
        &crate::lob::watch::SessionReadyFlag {
            symbol: "SOLUSDT".to_string(),
            profile_id: "marginal:outcome=pulled".to_string(),
            n: 100,
            g: 7,
            ready_at_utc: "2026-09-09T00:00:00Z".to_string(),
            days: vec!["2026-09-08".to_string()],
        },
    )
    .unwrap();
    // Без замороженного шорт-листа (файл ещё не существует) — всё ещё
    // отказ, теперь по заморозке, не по сессионному флагу.
    match run_markout(&args) {
        Err(e) => assert!(
            format!("{e}").contains("заморозка"),
            "сообщение обязано называть причину: {e}"
        ),
        Ok(_) => panic!("без файла шорт-листа обязан быть отказ"),
    }
    std::fs::write(
        args.shortlist.as_ref().unwrap(),
        "marginal:outcome=pulled\n",
    )
    .unwrap();
    run_markout(&args).expect("с обоими флагами и шорт-листом --confirmatory обязан пройти");
}

/// Критерий приёмки таска 12, буквально: `lob markout --confirmatory` на
/// профиле вне замороженного шорт-листа завершается ненулевым кодом, даже
/// когда сессионный флаг готовности и все данные в порядке. Заморозка —
/// механизм, а не обещание.
#[test]
fn markout_confirmatory_rejects_profile_outside_frozen_shortlist() {
    let dir = tempfile::tempdir().unwrap();
    let mut frames = super::super::test_support::three_level_frames();
    for k in 1..=7 {
        frames.push(super::super::test_support::delta_frame(
            2000 + k * 10_000,
            &[(96, 1), (98, 1), (99, 1), (100, 1)],
            &[(105, 10)],
        ));
    }
    super::super::test_support::write_day(dir.path(), "SOLUSDT", "2026-09-08", &frames);
    crate::bybit::verify_sidecar::append_verify_row(
        &crate::bybit::verify_sidecar::verify_csv_path(dir.path()),
        &crate::bybit::verify_sidecar::VerifyRow {
            ts_utc: "2026-09-08T00:05:00Z".to_string(),
            symbol: "SOLUSDT".to_string(),
            snapshot_seq: Some(1),
            book_seq: Some(1),
            mismatches: Some(0),
            verdict: crate::bybit::verify_sidecar::VerifyVerdict::Ok,
        },
    )
    .unwrap();
    crate::lob::watch::write_ready_flag(
        &dir.path().join("ready.flag"),
        &crate::lob::watch::ReadyFlag {
            symbol: "SOLUSDT".to_string(),
            n: 100,
            g: 1,
            ready_at_utc: "2026-09-09T00:00:00Z".to_string(),
            days: vec!["2026-09-08".to_string()],
        },
    )
    .unwrap();
    crate::lob::watch::write_session_ready_flag(
        &crate::lob::watch::session_ready_flag_path(
            dir.path(),
            "SOLUSDT",
            "marginal:outcome=pulled",
        ),
        &crate::lob::watch::SessionReadyFlag {
            symbol: "SOLUSDT".to_string(),
            profile_id: "marginal:outcome=pulled".to_string(),
            n: 100,
            g: 7,
            ready_at_utc: "2026-09-09T00:00:00Z".to_string(),
            days: vec!["2026-09-08".to_string()],
        },
    )
    .unwrap();
    let shortlist_path = dir.path().join("shortlist-frozen.txt");
    // Заморожен другой профиль — не тот, что запрашивает --profile.
    std::fs::write(&shortlist_path, "marginal:side=bid\n").unwrap();
    let args = MarkoutArgs {
        root: dir.path().to_path_buf(),
        symbol: "SOLUSDT".to_string(),
        h3: H3Args {
            h3_mode: H3ModeArg::Percentile,
            h3_lots: Some(5),
            h3_usd: None,
            h3_strength_pct: None,
            h3_strength_window_bps: None,
        },
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        out: None,
        confirmatory: true,
        flag: None,
        median_lifetime_ms: Some(60_000),
        profile: Some("marginal:outcome=pulled".to_string()),
        shortlist: Some(shortlist_path.clone()),
    };
    match run_markout(&args) {
        Err(e) => assert!(
            format!("{e}").contains("шорт-лист"),
            "сообщение обязано называть причину: {e}"
        ),
        Ok(_) => panic!("профиль вне шорт-листа обязан отказать"),
    }
    assert!(
        super::super::dispatch(super::super::LobCommand::Markout(args)).is_err(),
        "код выхода команды обязан быть ненулевым"
    );

    // Тот же профиль, добавленный в список, — обязан пройти.
    std::fs::write(
        &shortlist_path,
        "marginal:side=bid\nmarginal:outcome=pulled\n",
    )
    .unwrap();
    let args_ok = MarkoutArgs {
        root: dir.path().to_path_buf(),
        symbol: "SOLUSDT".to_string(),
        h3: H3Args {
            h3_mode: H3ModeArg::Percentile,
            h3_lots: Some(5),
            h3_usd: None,
            h3_strength_pct: None,
            h3_strength_window_bps: None,
        },
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        out: None,
        confirmatory: true,
        flag: None,
        median_lifetime_ms: Some(60_000),
        profile: Some("marginal:outcome=pulled".to_string()),
        shortlist: Some(shortlist_path),
    };
    run_markout(&args_ok).expect("профиль из шорт-листа обязан пройти");
}
