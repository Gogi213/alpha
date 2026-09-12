use super::*;
use crate::commands::lob::test_support::{snap_frame, three_level_frames, write_day_part};

/// Критерий приёмки таска 26: каталог из фикстуры → `lob verify` →
/// `verify-<S>.status` ровно `ok` (без перевода строки — читатель
/// `profiles.rs` сравнивает `trim() == "ok"`, `watch.rs` — так же).
/// Ожидание — из фикстуры: `three_level_frames` не пересекает книгу
/// и торгует только по удерживаемым тикам (doc `test_support`).
#[test]
fn verify_writes_ok_marker_for_a_clean_session_directory() {
    let dir = tempfile::tempdir().unwrap();
    write_day_part(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        1,
        &three_level_frames(),
    );

    let report =
        verify_and_mark(dir.path(), dir.path(), "SOLUSDT").expect("фикстура обязана сверяться");

    assert_eq!(report.status, VerifyStatus::Ok);
    assert_eq!(report.parts.len(), 1);
    assert_eq!(report.parts[0].day_utc, "2026-09-08");
    assert_eq!(report.total.files, 1);
    let marker = std::fs::read_to_string(dir.path().join("verify-SOLUSDT.status")).unwrap();
    assert_eq!(marker, "ok");
}

/// Критерий приёмки таска 26: части за несколько суток — сверка по
/// всем частям символа (одна запись отчёта на часть с её сутками), и
/// одна битая часть (пересечённая книга в снапшоте: бид 105 ≥ аск 100 —
/// инвариант шага 0.6) роняет маркер всего каталога в `fail`.
#[test]
fn verify_reports_every_part_with_its_day_and_a_crossed_part_fails_the_marker() {
    let dir = tempfile::tempdir().unwrap();
    write_day_part(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        1,
        &three_level_frames(),
    );
    write_day_part(
        dir.path(),
        "SOLUSDT",
        "2026-09-09",
        1,
        &[snap_frame(0, &[(105, 10)], &[(100, 10)])],
    );

    let report = verify_and_mark(dir.path(), dir.path(), "SOLUSDT")
        .expect("битая часть — вердикт, не ошибка чтения");

    let days: Vec<&str> = report.parts.iter().map(|p| p.day_utc.as_str()).collect();
    assert_eq!(
        days,
        ["2026-09-08", "2026-09-09"],
        "по части на сутки, по порядку"
    );
    assert_eq!(VerifyStatus::of(&report.parts[0].summary), VerifyStatus::Ok);
    assert_eq!(
        VerifyStatus::of(&report.parts[1].summary),
        VerifyStatus::Fail
    );
    assert_eq!(report.total.files, 2);
    assert_eq!(report.status, VerifyStatus::Fail);
    let marker = std::fs::read_to_string(dir.path().join("verify-SOLUSDT.status")).unwrap();
    assert_eq!(marker, "fail");
}

/// Значение колонки `name` в строке `profile_id == id` таблицы
/// `profiles-<дата>.csv` (шапка `#` пропускается).
fn profiles_cell(text: &str, id: &str, name: &str) -> String {
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_reader(text.as_bytes());
    let idx = r
        .headers()
        .unwrap()
        .iter()
        .position(|h| h == name)
        .unwrap_or_else(|| panic!("колонки {name} нет"));
    r.records()
        .map(|row| row.unwrap())
        .find(|row| row.get(0) == Some(id))
        .unwrap_or_else(|| panic!("строки {id} нет:\n{text}"))
        .get(idx)
        .unwrap()
        .to_string()
}

/// Критерий приёмки таска 26 (владелец: «с того что заколлектилось
/// уже делать анализы»): сутки, собранные без `lob pilot`, до маркера
/// для `lob profiles` без `--allow-unverified` не существуют
/// (fail-closed, R44: `n = 0` у маргинала инструмента), а после
/// `lob verify --root <session_dir>` — читаются: `sessions=1` в шапке
/// окна и `n > 0` у того же маргинала (фикстура `three_level_frames`
/// даёт записи уровней при `h3_lots = 5`, см. `pilot.rs::
/// process_instrument_reports_rates_shares_and_verify_marker`).
#[test]
fn verify_marker_lets_profiles_read_the_day_without_allow_unverified() {
    use crate::commands::lob::profiles::{run_profiles, ProfilesArgs};
    use crate::commands::lob::{ExecutionArgs, H3Args, H3ModeArg};

    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    std::fs::write(
        crate::commands::record::instruments_csv_path(root),
        "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n\
         SOLUSDT,0.01,0.1,0.1,5,5\n",
    )
    .unwrap();
    let candidates_csv = root.join("candidates.csv");
    std::fs::write(&candidates_csv, "symbol,coverage_top50_bps\nSOLUSDT,300\n").unwrap();
    let session_dir = root.join("collect");
    std::fs::create_dir_all(&session_dir).unwrap();
    std::fs::write(
        session_dir.join("session.json"),
        "{\"started_utc\":\"2026-05-01T02:00:00Z\",\"start_hour_utc\":2,\
         \"instruments\":[\"SOLUSDT\"]}",
    )
    .unwrap();
    write_day_part(
        &session_dir,
        "SOLUSDT",
        "2026-05-01",
        1,
        &three_level_frames(),
    );
    let preregistration = root.join("preregistration.md");
    std::fs::write(
        &preregistration,
        "exploratory: 2000-01-01\nconfirmatory: 2099-12-31\n",
    )
    .unwrap();
    let args = |out: &str| ProfilesArgs {
        root: root.to_path_buf(),
        candidates_csv: candidates_csv.clone(),
        h3: H3Args {
            h3_mode: H3ModeArg::Floor,
            h3_lots: None,
        },
        warmup_ms: 0,
        repeat_window_ms: crate::commands::lob::DEFAULT_REPEAT_WINDOW_MS,
        allow_unverified: false,
        out: Some(root.join(out)),
        now_utc: Some("2026-06-20T00:00:00Z".to_string()),
        runs_out: root.join("runs.csv"),
        execution: ExecutionArgs {
            median_rtt_ns: None,
            p95_rtt_ns: None,
            order_qty_e9: None,
        },
        preregistration: Some(preregistration.clone()),
        window_end: None,
    };
    const ID: &str = "marginal:instrument=SOLUSDT";

    let before = args("before.csv");
    run_profiles(&before).expect("прогон без маркера");
    let before_text = std::fs::read_to_string(before.out.unwrap()).unwrap();
    assert_eq!(
        profiles_cell(&before_text, ID, "n"),
        "0",
        "без маркера сессия не читается (fail-closed):\n{before_text}"
    );

    print_summary(&VerifyArgs {
        symbol: "SOLUSDT".to_string(),
        root: session_dir.clone(),
    })
    .expect("lob verify на каталоге сессии");
    assert_eq!(
        std::fs::read_to_string(session_dir.join("verify-SOLUSDT.status")).unwrap(),
        "ok"
    );

    let after = args("after.csv");
    run_profiles(&after).expect("прогон с маркером от lob verify");
    let after_text = std::fs::read_to_string(after.out.unwrap()).unwrap();
    let window_line = after_text
        .lines()
        .find(|l| l.starts_with("# window:"))
        .unwrap_or_else(|| panic!("нет строки окна:\n{after_text}"));
    assert!(
        window_line.contains("sessions=1 "),
        "сутки сессии внутри окна: {window_line}"
    );
    let n: u64 = profiles_cell(&after_text, ID, "n").parse().unwrap();
    assert!(
        n > 0,
        "после маркера сутки читаются, n = {n}:\n{after_text}"
    );
}
