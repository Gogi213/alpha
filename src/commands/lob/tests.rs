use std::path::{Path, PathBuf};

use super::parts::day_of_binlog_name;
use super::*;
use crate::commands::record::instruments_csv_path;
use crate::lob::levels::{H3Mode, LevelsConfig};

/// Критерий приёмки таска 17: `--h3-lots` вместе с `--h3-mode floor` —
/// громкая ошибка (`resolve_h3_mode` — общий шов пяти подкоманд), не
/// молчаливый игнор значения, которое `floor` не читает.
#[test]
fn resolve_h3_mode_rejects_h3_lots_with_floor() {
    let dir = tempfile::tempdir().unwrap();
    let err = resolve_h3_mode(dir.path(), "SOLUSDT", H3ModeArg::Floor, Some(5)).unwrap_err();
    assert!(
        err.to_string().contains("h3-lots") || err.to_string().contains("floor"),
        "сообщение обязано назвать конфликт --h3-lots/--h3-mode floor: {err}"
    );
}

/// Критерий приёмки таска 18 (В-30/D05): `--h3-k` вместе с
/// `--h3-mode percentile` — громкая ошибка, `k` параметризует только
/// относительный порог `floor`.
#[test]
fn resolve_h3_mode_with_k_rejects_h3_k_with_percentile() {
    let dir = tempfile::tempdir().unwrap();
    let err = resolve_h3_mode_with_k(
        dir.path(),
        "SOLUSDT",
        H3ModeArg::Percentile,
        Some(5),
        Some(2.0),
    )
    .unwrap_err();
    assert!(
        err.to_string().contains("h3-k") || err.to_string().contains("percentile"),
        "сообщение обязано назвать конфликт --h3-k/--h3-mode percentile: {err}"
    );
}

/// `--h3-k` с `floor` считает `h3_lots = floor(k × median_trade_lots)`
/// из колонки `instruments.csv`, не колонку `h3_lots` (та пустая в этой
/// фикстуре — читатель обязан её не тронуть).
#[test]
fn resolve_h3_mode_with_k_computes_floor_from_median_trade_lots() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        instruments_csv_path(dir.path()),
        "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots,k,median_trade_lots\n\
         SOLUSDT,0.01,0.1,0.1,5,,,7\n",
    )
    .unwrap();
    let mode =
        resolve_h3_mode_with_k(dir.path(), "SOLUSDT", H3ModeArg::Floor, None, Some(1.5)).unwrap();
    assert_eq!(mode, H3Mode::Floor { h3_lots: 10 }, "floor(1.5*7) = 10");
}

#[test]
fn median_trade_lots_for_symbol_reads_the_column() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        instruments_csv_path(dir.path()),
        "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots,k,median_trade_lots\n\
         SOLUSDT,0.01,0.1,0.1,5,10,2.0,7\n",
    )
    .unwrap();
    assert_eq!(
        median_trade_lots_for_symbol(&instruments_csv_path(dir.path()), "SOLUSDT").unwrap(),
        7
    );
}

#[test]
fn median_trade_lots_for_symbol_errors_without_the_column() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        instruments_csv_path(dir.path()),
        "symbol,tick_size,min_order_qty,qty_step,min_notional_value\nSOLUSDT,0.01,0.1,0.1,5\n",
    )
    .unwrap();
    let err =
        median_trade_lots_for_symbol(&instruments_csv_path(dir.path()), "SOLUSDT").unwrap_err();
    assert!(
        err.to_string().contains("median_trade_lots"),
        "сообщение обязано назвать недостающую колонку: {err}"
    );
}

/// Критерий приёмки таска 18: сетка `k` пилота кормится одним
/// декодированием бинлога, не пятью — `replay_symbol_over_configs` на
/// нескольких порогах разом обязан дать те же числа, что отдельные
/// вызовы `replay_symbol` на каждом пороге по отдельности, и разный
/// порог обязан дать разный (не больший при большем пороге) счёт
/// уровней на этой фикстуре.
#[test]
fn replay_symbol_over_configs_matches_single_config_replay_per_threshold() {
    let dir = tempfile::tempdir().unwrap();
    test_support::write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &test_support::three_level_frames(),
    );
    let cfg_low = LevelsConfig {
        mode: H3Mode::Floor { h3_lots: 1 },
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let cfg_high = LevelsConfig {
        mode: H3Mode::Floor { h3_lots: 9 },
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let multi = replay_symbol_over_configs(dir.path(), "SOLUSDT", &[cfg_low, cfg_high]).unwrap();
    let single_low = replay_symbol(dir.path(), "SOLUSDT", cfg_low).unwrap();
    let single_high = replay_symbol(dir.path(), "SOLUSDT", cfg_high).unwrap();
    assert_eq!(multi.len(), 2);
    assert_eq!(
        multi[0].days[0].records.len(),
        single_low.days[0].records.len()
    );
    assert_eq!(
        multi[1].days[0].records.len(),
        single_high.days[0].records.len()
    );
    assert!(
        multi[1].days[0].records.len() <= multi[0].days[0].records.len(),
        "выше порог — не больше рождений: {} vs {}",
        multi[1].days[0].records.len(),
        multi[0].days[0].records.len()
    );
}

/// Таск 19, критерий 4: каталог старого формата (до таска 19 `lob
/// session` писала `<SYMBOL>.binlog` без даты) обязан провалиться с
/// явным сообщением про переименование, не с общим «нет суточных
/// файлов» — иначе владелец ищет разгадку не там.
#[test]
fn undated_symbol_binlog_fails_with_an_explicit_rename_message_not_silent_no_files() {
    let dir = tempfile::tempdir().unwrap();
    // Старая раскладка `lob session` (до таска 19): файл без даты.
    std::fs::write(dir.path().join("SOLUSDT.binlog"), b"stub").unwrap();
    let cfg = LevelsConfig {
        mode: H3Mode::Floor { h3_lots: 1 },
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let err = match replay_symbol(dir.path(), "SOLUSDT", cfg) {
        Ok(_) => panic!("файл без даты обязан провалить реплей"),
        Err(e) => e,
    };
    let msg = err.to_string();
    assert!(
        msg.contains("SOLUSDT.binlog") && msg.contains("переименуйте"),
        "сообщение обязано назвать файл и посоветовать переименование: {msg}"
    );
}

/// Тот же каталог без вообще никакого файла символа — сообщение
/// остаётся прежним, общим «нет суточных файлов» (регресс-тест против
/// того, чтобы находка старого формата подменила собой пустой каталог).
#[test]
fn missing_symbol_files_still_get_the_generic_no_daily_files_message() {
    let dir = tempfile::tempdir().unwrap();
    let cfg = LevelsConfig {
        mode: H3Mode::Floor { h3_lots: 1 },
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let err = match replay_symbol(dir.path(), "SOLUSDT", cfg) {
        Ok(_) => panic!("пустой каталог обязан провалить реплей"),
        Err(e) => e,
    };
    let msg = err.to_string();
    assert!(
        msg.contains("нет суточных файлов") && !msg.contains("переименуйте"),
        "без файла вовсе сообщение обязано остаться общим: {msg}"
    );
}

// -----------------------------------------------------------------
// `session_binlog_for` — резолвер одной сессии (таск 19, часть 2):
// `profiles.rs`/`watch.rs`/`backtest.rs`/`bybit::verify` замыкаются на
// него вместо литерала `<SYMBOL>.binlog` или своей копии поиска.
// -----------------------------------------------------------------

#[test]
fn session_binlog_for_finds_the_single_dated_file() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("SOLUSDT-2026-09-08.binlog"), b"stub").unwrap();
    let paths = session_binlog_for(dir.path(), "SOLUSDT").unwrap();
    assert_eq!(paths, vec![dir.path().join("SOLUSDT-2026-09-08.binlog")]);
}

#[test]
fn session_binlog_for_reports_the_undated_legacy_file_by_name_with_a_rename_hint() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("SOLUSDT.binlog"), b"stub").unwrap();
    let err = session_binlog_for(dir.path(), "SOLUSDT").unwrap_err();
    let msg = err.to_string();
    assert!(
        msg.contains("SOLUSDT.binlog") && msg.contains("переименуйте"),
        "сообщение обязано назвать файл и посоветовать переименование: {msg}"
    );
}

#[test]
fn session_binlog_for_reports_nothing_found_when_the_symbol_never_appears() {
    let dir = tempfile::tempdir().unwrap();
    let err = session_binlog_for(dir.path(), "SOLUSDT").unwrap_err();
    let msg = err.to_string();
    assert!(
        msg.contains("SOLUSDT") && !msg.contains("переименуйте"),
        "без файла вовсе сообщение не обязано упоминать переименование: {msg}"
    );
}

/// Таск 22, критерий 3: несколько сессий в те же сутки (`-p2`, `-p3`, …)
/// — не ошибка «путаница каталогов» (старое поведение таска 19), а список
/// частей в порядке записи. `-p2` голой лексикографией сортировался бы
/// раньше файла без суффикса (`-` < `.` в ASCII) — тест ловит именно
/// инверсию, а не только «оба файла присутствуют».
#[test]
fn session_binlog_for_orders_same_day_parts_by_part_number_not_lexicographically() {
    let dir = tempfile::tempdir().unwrap();
    let p1 = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-08", 1);
    let p2 = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-08", 2);
    let p3 = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-08", 3);
    // Порядок записи на диск — намеренно не по возрастанию имени, чтобы
    // тест не мог случайно совпасть с порядком `std::fs::read_dir`.
    std::fs::write(&p2, b"stub").unwrap();
    std::fs::write(&p3, b"stub").unwrap();
    std::fs::write(&p1, b"stub").unwrap();
    let paths = session_binlog_for(dir.path(), "SOLUSDT").unwrap();
    assert_eq!(
        paths,
        vec![p1, p2, p3],
        "части одних суток — по возрастанию номера"
    );
}

/// Таск 22: каталог теперь может нести несколько суток (владелец гоняет
/// `lob session` в тот же `--root` день за днём) — сортировка сперва по
/// дате, потом по части внутри даты.
#[test]
fn session_binlog_for_orders_multiple_days_by_date_then_part() {
    let dir = tempfile::tempdir().unwrap();
    let day1_p1 = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-08", 1);
    let day1_p2 = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-08", 2);
    let day2_p1 = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-09", 1);
    std::fs::write(&day2_p1, b"stub").unwrap();
    std::fs::write(&day1_p2, b"stub").unwrap();
    std::fs::write(&day1_p1, b"stub").unwrap();
    let paths = session_binlog_for(dir.path(), "SOLUSDT").unwrap();
    assert_eq!(paths, vec![day1_p1, day1_p2, day2_p1]);
}

// -----------------------------------------------------------------
// `session_parts_for` / `group_parts_by_day` / `session_days_in_dir` —
// сутки и час старта на уровне части, не каталога (таск 23).
// -----------------------------------------------------------------

fn write_session_json(dir: &Path, start_hour_utc: u32, parts: &[(&str, u32, &str)]) {
    let files: Vec<String> = parts
        .iter()
        .map(|(symbol, part, started)| {
            format!("{{\"symbol\":\"{symbol}\",\"part\":{part},\"started_utc\":\"{started}\"}}")
        })
        .collect();
    let json = format!(
        "{{\"started_utc\":\"2026-09-09T14:00:00Z\",\"start_hour_utc\":{start_hour_utc},             \"instruments\":[\"SOLUSDT\"],\"binlog_files\":[{}]}}",
        files.join(",")
    );
    std::fs::write(dir.join("session.json"), json).unwrap();
}

/// Каталог с частями за двое суток: верхний `started_utc`/`start_hour_utc`
/// — от последней сессии (D+1, 14 ч), но каждая часть получает свои
/// сутки из имени файла и свой час из `binlog_files`.
#[test]
fn session_parts_for_attributes_day_and_hour_per_part_not_per_directory() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let d1p1 = crate::commands::record::day_file_path(root, "SOLUSDT", "2026-09-08", 1);
    let d1p2 = crate::commands::record::day_file_path(root, "SOLUSDT", "2026-09-08", 2);
    let d2p1 = crate::commands::record::day_file_path(root, "SOLUSDT", "2026-09-09", 1);
    for p in [&d2p1, &d1p2, &d1p1] {
        std::fs::write(p, b"stub").unwrap();
    }
    write_session_json(
        root,
        14,
        &[
            ("SOLUSDT", 1, "2026-09-08T02:00:00Z"),
            ("SOLUSDT", 2, "2026-09-08T11:30:00Z"),
            ("SOLUSDT", 1, "2026-09-09T14:00:00Z"),
        ],
    );
    let parts = session_parts_for(root, "SOLUSDT").unwrap();
    let view: Vec<(&str, u32, u32)> = parts
        .iter()
        .map(|p| (p.day_utc.as_str(), p.part, p.start_hour_utc))
        .collect();
    // Часть 1 встречается дважды (D и D+1) — запись `binlog_files`
    // подбирается по тройке (symbol, part, сутки `started_utc`), не по
    // паре: у части 1 суток D+1 час 14, не 02.
    assert_eq!(
        view,
        vec![
            ("2026-09-08", 1, 2),
            ("2026-09-08", 2, 11),
            ("2026-09-09", 1, 14)
        ]
    );
    assert_eq!(
        parts.iter().map(|p| p.path.clone()).collect::<Vec<_>>(),
        vec![d1p1, d1p2, d2p1],
        "порядок тот же, что у session_binlog_for"
    );
}

/// Запись до таска 22: `session.json` без `binlog_files` — час берётся
/// из `start_hour_utc` каталога, сутки по-прежнему из имени файла.
#[test]
fn session_parts_for_falls_back_to_directory_hour_without_binlog_files() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    std::fs::write(root.join("SOLUSDT-2026-09-08.binlog"), b"stub").unwrap();
    std::fs::write(
        root.join("session.json"),
        "{\"started_utc\":\"2026-09-08T05:00:00Z\",\"start_hour_utc\":5,\"instruments\":[\"SOLUSDT\"]}",
    )
    .unwrap();
    let parts = session_parts_for(root, "SOLUSDT").unwrap();
    assert_eq!(parts.len(), 1);
    assert_eq!(parts[0].day_utc, "2026-09-08");
    assert_eq!(parts[0].start_hour_utc, 5);
    assert_eq!(parts[0].part, 1);
}

#[test]
fn session_parts_for_requires_session_json() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("SOLUSDT-2026-09-08.binlog"), b"stub").unwrap();
    let err = session_parts_for(dir.path(), "SOLUSDT").unwrap_err();
    assert!(
        err.to_string().contains("session.json"),
        "без session.json — не сессия: {err}"
    );
}

#[test]
fn group_parts_by_day_keeps_order_and_splits_only_on_day_change() {
    let part = |day: &str, n: u32| SessionPart {
        path: PathBuf::from(format!("SOLUSDT-{day}-p{n}.binlog")),
        part: n,
        day_utc: day.to_string(),
        start_hour_utc: 0,
    };
    let groups = group_parts_by_day(vec![
        part("2026-09-08", 1),
        part("2026-09-08", 2),
        part("2026-09-09", 1),
    ]);
    let view: Vec<(&str, Vec<u32>)> = groups
        .iter()
        .map(|(d, ps)| (d.as_str(), ps.iter().map(|p| p.part).collect()))
        .collect();
    assert_eq!(
        view,
        vec![("2026-09-08", vec![1, 2]), ("2026-09-09", vec![1])]
    );
}

#[test]
fn session_days_in_dir_unions_dated_parts_of_all_symbols_and_ignores_the_rest() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    std::fs::write(root.join("session.json"), "{}").unwrap();
    for name in [
        "SOLUSDT-2026-09-08.binlog",
        "SOLUSDT-2026-09-08-p2.binlog",
        "NEARUSDT-2026-09-10.binlog",
        "ZECUSDT.binlog", // старый недатированный формат
        "gaps.csv",
        "BAD-2026-9-8.binlog", // не YYYY-MM-DD
    ] {
        std::fs::write(root.join(name), b"stub").unwrap();
    }
    let days: Vec<String> = session_days_in_dir(root).into_iter().collect();
    assert_eq!(days, vec!["2026-09-08", "2026-09-10"]);
}

#[test]
fn session_days_in_dir_is_empty_without_session_json() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("SOLUSDT-2026-09-08.binlog"), b"stub").unwrap();
    assert!(session_days_in_dir(dir.path()).is_empty());
}

#[test]
fn day_of_binlog_name_parses_dated_names_only() {
    assert_eq!(
        day_of_binlog_name("SOLUSDT-2026-09-08.binlog"),
        Some("2026-09-08")
    );
    assert_eq!(
        day_of_binlog_name("SOLUSDT-2026-09-08-p12.binlog"),
        Some("2026-09-08")
    );
    assert_eq!(day_of_binlog_name("SOLUSDT.binlog"), None);
    assert_eq!(day_of_binlog_name("2026-09-08.binlog"), None, "без символа");
    assert_eq!(day_of_binlog_name("SOLUSDT-2026-09-08.csv"), None);
}

#[test]
fn lob_help_lists_all_subcommands() {
    #[derive(clap::Parser)]
    struct TestCli {
        #[command(subcommand)]
        cmd: LobCommand,
    }
    use clap::CommandFactory;
    let mut top = TestCli::command();
    top.build();
    let mut sorted: Vec<String> = top
        .get_subcommands()
        .map(|s| s.get_name().to_string())
        .filter(|n| n != "help")
        .collect();
    sorted.sort();
    let expected = [
        "archive",
        "backtest",
        "binlog-stats",
        "bounce-grid",
        "bounce-verdict",
        "clock",
        "dashboard",
        "export",
        "fee-rate",
        "fill-capacity",
        "latency",
        "levels",
        "markout",
        "pick",
        "pilot",
        "power",
        "probe",
        "profiles",
        "react",
        "record",
        "session",
        "shortlist",
        "touch-profiles",
        "touches",
        "verify",
        "watch",
    ];
    assert_eq!(
        sorted,
        expected.iter().map(ToString::to_string).collect::<Vec<_>>()
    );
}

/// В-61: полный резолвер требует свои флаги и отвергает чужие; прежние режимы
/// через него — те же, что через `resolve_h3_mode_with_k`.
#[test]
fn resolve_h3_mode_full_requires_and_forbids_the_right_flags() {
    let dir = tempfile::tempdir().unwrap();
    let base = H3Args {
        h3_mode: H3ModeArg::Notional,
        h3_lots: None,
        h3_usd: None,
        h3_strength_pct: None,
        h3_strength_window_bps: None,
    };
    let err = resolve_h3_mode_full(dir.path(), "SOLUSDT", &base, None, 10_000_000, 100_000_000)
        .unwrap_err();
    assert!(err.to_string().contains("--h3-usd"), "{err}");
    let ok = resolve_h3_mode_full(
        dir.path(),
        "SOLUSDT",
        &H3Args {
            h3_usd: Some(50_000.0),
            ..base
        },
        None,
        10_000_000,
        100_000_000,
    )
    .unwrap();
    assert_eq!(
        ok,
        H3Mode::Notional {
            min_usd_e9: 50_000 * 1_000_000_000,
            tick_e9: 10_000_000,
            step_e9: 100_000_000
        }
    );
    let err = resolve_h3_mode_full(
        dir.path(),
        "SOLUSDT",
        &H3Args {
            h3_usd: Some(50_000.0),
            h3_lots: Some(5),
            ..base
        },
        None,
        10_000_000,
        100_000_000,
    )
    .unwrap_err();
    assert!(err.to_string().contains("--h3-lots"), "{err}");
    let err = resolve_h3_mode_full(
        dir.path(),
        "SOLUSDT",
        &H3Args {
            h3_mode: H3ModeArg::Both,
            h3_usd: Some(50_000.0),
            h3_strength_pct: Some(300.0),
            ..base
        },
        None,
        10_000_000,
        100_000_000,
    )
    .unwrap_err();
    assert!(
        err.to_string().contains("--h3-strength-window-bps"),
        "{err}"
    );
    let ok = resolve_h3_mode_full(
        dir.path(),
        "SOLUSDT",
        &H3Args {
            h3_mode: H3ModeArg::Strength,
            h3_strength_pct: Some(300.0),
            h3_strength_window_bps: Some(20.0),
            ..base
        },
        None,
        10_000_000,
        100_000_000,
    )
    .unwrap();
    assert_eq!(
        ok,
        H3Mode::Strength {
            pct_e2: 30_000,
            window_bps_e2: 2_000
        }
    );
    // Прежний режим через полный резолвер не принимает денежные флаги.
    let err = resolve_h3_mode_full(
        dir.path(),
        "SOLUSDT",
        &H3Args {
            h3_mode: H3ModeArg::Floor,
            h3_usd: Some(1.0),
            ..base
        },
        None,
        10_000_000,
        100_000_000,
    )
    .unwrap_err();
    assert!(err.to_string().contains("--h3-usd"), "{err}");
}

/// Перенос возраста (аудит дизайна 22.09 Т3) — только через смежную полночь: пропущенные сутки
/// значат, что уровень никто не видел.
#[test]
fn carry_age_needs_adjacent_calendar_days() {
    use super::replay::is_next_day;
    assert!(is_next_day("2026-09-21", "2026-09-22"));
    assert!(is_next_day("2026-09-30", "2026-10-01"));
    assert!(!is_next_day("2026-09-21", "2026-09-23"));
    assert!(!is_next_day("2026-09-21", "2026-09-21"));
    assert!(!is_next_day("x", "2026-09-22"));
}
