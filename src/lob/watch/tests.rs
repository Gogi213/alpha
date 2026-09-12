use super::*;

fn tally(violations: u64, basis: u64) -> DayTally {
    DayTally {
        symbol: "TST".to_string(),
        day_utc: "2026-01-01".to_string(),
        verify_test1_violations: violations,
        verify_basis_points: basis,
    }
}

#[test]
fn tally_day_builds_from_verify_counts_and_feeds_eligibility() {
    let t = tally_day("TST", "2026-01-01", 0, 50_000);
    assert_eq!(t.symbol, "TST");
    assert_eq!(t.day_utc, "2026-01-01");
    assert!(day_eligible(&t));
}

#[test]
fn eligibility_encodes_the_test1_threshold() {
    assert!(day_eligible(&tally(0, 50_000)), "чистые сутки годны");
    assert!(
        !day_eligible(&tally(1, 10_000)),
        "ровно 0.01% — уже не годно, граница строгая"
    );
    assert!(day_eligible(&tally(1, 10_001)), "ниже порога — годно");
    assert!(
        !day_eligible(&tally(0, 0)),
        "проверок не было — годности нет"
    );
    assert!(!day_eligible(&tally(10, 10_000)), "провал verify целиком");
    assert!(day_eligible(&tally(5, 100_000)), "0.005% — годно");
}

#[test]
fn confirmatory_without_flag_is_err() {
    let dir = tempfile::tempdir().expect("песочница");
    let path = ready_flag_path(dir.path());
    assert!(
        require_ready_flag(&path).is_err(),
        "подтверждающий прогон без флага обязан завершиться ненулевым кодом"
    );
    let flag = ReadyFlag {
        symbol: "TST".to_string(),
        n: 108,
        g: 12,
        ready_at_utc: "2026-02-01T00:00:00Z".to_string(),
        days: vec!["2026-01-03".to_string()],
    };
    write_ready_flag(&path, &flag).expect("флаг пишется раз");
    assert_eq!(require_ready_flag(&path).expect("с флагом — пропуск"), flag);
    assert!(
        write_ready_flag(&path, &flag).is_err(),
        "второй флаг — отказ"
    );
}

/// Граница модулей в духе шага 1.1: счётчик не знает про транспорт,
/// часы и дробные числа. Проверка — грепом по собственному исходнику.
/// Дробных чисел здесь нет сознательно: доли считаются целыми
/// (порог verify — перекрёстным умножением, доля разрывов — в ppm).
///
/// Запрещённые фрагменты собраны из частей: литерал целиком триггерил бы
/// эту же проверку сам на себя.
#[test]
fn module_stays_detached_from_transport_clocks_and_approx_numbers() {
    const SRC: &str = include_str!("../watch.rs");
    let banned = [
        concat!("by", "bit"),
        concat!("tok", "io"),
        concat!("Inst", "ant"),
        concat!("System", "Time"),
        concat!("f", "64"),
        concat!("std::", "time"),
    ];
    for b in banned {
        assert!(!SRC.contains(b), "исходник тянет запрещённое: {b}");
    }
}

// -------------------------------------------------------------------
// Таск 07 — сессии и вердиктный профиль вместо ячейки C1/C2.
// -------------------------------------------------------------------

fn session(id: &str, day: &str, hour: u32, verified: bool, n: u64) -> SessionTally {
    SessionTally {
        session_id: id.to_string(),
        day_utc: day.to_string(),
        start_hour_utc: hour,
        symbol: "SOLUSDT".to_string(),
        verified,
        n,
    }
}

fn day_of(sessions: Vec<SessionTally>) -> SessionDay {
    SessionDay {
        day_utc: sessions[0].day_utc.clone(),
        symbol: sessions[0].symbol.clone(),
        sessions,
    }
}

#[test]
fn session_day_eligible_requires_at_least_one_verified_session() {
    let empty = SessionDay {
        day_utc: "2026-03-01".to_string(),
        symbol: "SOLUSDT".to_string(),
        sessions: Vec::new(),
    };
    assert!(!session_day_eligible(&empty), "суток без сессий вовсе нет");

    let lone_bad = day_of(vec![session("s1", "2026-03-01", 2, false, 50)]);
    assert!(
        !session_day_eligible(&lone_bad),
        "единственная сессия без сверки — сутки не кластер"
    );
    assert_eq!(lone_bad.n(), 0, "не прошедшая сверку сессия не даёт n");

    let lone_good = day_of(vec![session("s1", "2026-03-01", 2, true, 0)]);
    assert!(
        session_day_eligible(&lone_good),
        "верифицированная сессия годна, даже если наблюдений нет"
    );

    let mixed = day_of(vec![
        session("s1", "2026-03-01", 2, false, 999),
        session("s2", "2026-03-01", 14, true, 10),
    ]);
    assert!(
        session_day_eligible(&mixed),
        "вторая сессия суток верифицирована — сутки годны"
    );
    assert_eq!(
        mixed.n(),
        10,
        "непрошедшая сверку сессия выброшена целиком, её n не подмешалось"
    );
    assert_eq!(mixed.start_hours_utc(), vec![2, 14]);
}

#[test]
fn push_session_rejects_foreign_symbol_bad_day_and_duplicate_session() {
    let mut sample = WatchSample::new("SOLUSDT", "marginal:outcome=pulled");
    let foreign = SessionTally {
        symbol: "NEARUSDT".to_string(),
        ..session("s1", "2026-03-01", 2, true, 10)
    };
    assert!(
        sample.push_session(foreign).is_err(),
        "чужой символ отвергается"
    );
    let bad_day = SessionTally {
        day_utc: "01.03.2026".to_string(),
        ..session("s1", "2026-03-01", 2, true, 10)
    };
    assert!(
        sample.push_session(bad_day).is_err(),
        "формат суток строгий"
    );
    sample
        .push_session(session("s1", "2026-03-01", 2, true, 10))
        .expect("первая сессия");
    assert!(
        sample
            .push_session(session("s1", "2026-03-02", 5, true, 10))
            .is_err(),
        "тот же session_id — дубликат, даже на другие сутки"
    );
    assert_eq!(sample.days_len(), 1);
}

/// Критерий приёмки таска 07: предикат годности суток и счётчик `n` у
/// `lob watch` — тот же код, что применяет гейт. Пересчёт из
/// `progress_rows()` (то, что видит человек/следующий таск в CSV) тем
/// же путём, что и `is_due()`/флаг, обязан дать те же числа — иначе
/// прогресс и гейт могли бы разойтись.
#[test]
fn session_gate_and_progress_share_one_predicate_and_counter() {
    let dir = tempfile::tempdir().expect("песочница");
    let root = dir.path();
    let mut sample = WatchSample::new("SOLUSDT", "marginal:outcome=pulled");
    let now = "2026-04-01T00:00:00Z";
    let mut fired = None;
    // Семь годных суток по одной верифицированной сессии, 15
    // наблюдений каждая: G=7 (порог), n=105 (>= CONFIRM_MIN_N=100).
    for d in 1..=7u32 {
        let day = format!("2026-03-{d:02}");
        let tally = session(&format!("s{d}"), &day, 2, true, 15);
        let outcome = sample.observe_session(root, tally, now).expect("сессия");
        if let Some(f) = outcome.flag {
            fired = Some(f);
        }
    }
    // Восьмые сутки: единственная сессия, сверку не прошла — выброшена
    // целиком, несмотря на большой n, и флаг уже стоит.
    let junk = session("junk", "2026-03-08", 5, false, 999);
    let outcome = sample.observe_session(root, junk, now).expect("сессия");
    assert!(
        outcome.flag.is_none(),
        "флаг уже выставлен седьмыми сутками, повторно не выставляется"
    );

    let rows = sample.progress_rows();
    assert_eq!(rows.len(), 8, "восемь суток, включая негодные");
    let recomputed_n: u64 = rows.iter().filter(|r| r.eligible).map(|r| r.n).sum();
    let recomputed_g = rows.iter().filter(|r| r.eligible && r.n >= 1).count();
    assert_eq!(
        recomputed_n,
        sample.n_total(),
        "n из progress.csv обязан совпасть с n гейта"
    );
    assert_eq!(
        recomputed_g,
        sample.g(),
        "G из progress.csv обязан совпасть с G гейта"
    );
    assert_eq!(sample.n_total(), 105, "n не меняется от негодных суток");
    assert_eq!(sample.g(), 7, "G не меняется от негодных суток");
    assert!(
        !sample.is_due(),
        "флаг уже стоит — is_due больше не взводится повторно"
    );
    let flag = fired.expect("седьмые годные сутки обязаны выставить флаг");
    assert_eq!(flag.symbol, "SOLUSDT");
    assert_eq!(flag.profile_id, "marginal:outcome=pulled");
    assert_eq!(flag.n, recomputed_n);
    assert_eq!(flag.g, recomputed_g as u64);
    assert_eq!(flag.days.len(), 7);

    let junk_row = rows
        .iter()
        .find(|r| r.day_utc == "2026-03-08")
        .expect("строка на восьмые сутки есть");
    assert!(
        !junk_row.eligible,
        "единственная непрошедшая сверку сессия — сутки негодны"
    );
    assert_eq!(junk_row.n, 0);
    assert_eq!(junk_row.session_start_hours_utc, "05");

    let first_row = rows
        .iter()
        .find(|r| r.day_utc == "2026-03-01")
        .expect("строка на первые сутки есть");
    assert_eq!(first_row.session_start_hours_utc, "02");
    assert_eq!(first_row.n, 15);
    assert!(first_row.eligible);

    // Флаг лежит на диске под именем, несущим и символ, и профиль.
    let flag_path = session_ready_flag_path(root, "SOLUSDT", "marginal:outcome=pulled");
    assert!(flag_path.exists());
    let back = require_session_ready_flag(&flag_path).expect("флаг читается");
    assert_eq!(back, flag);
}

#[test]
fn require_session_ready_flag_without_file_is_err() {
    let dir = tempfile::tempdir().expect("песочница");
    let path = session_ready_flag_path(dir.path(), "SOLUSDT", "marginal:side=bid");
    assert!(
        require_session_ready_flag(&path).is_err(),
        "будущий подтверждающий прогон без флага обязан завершиться ненулевым кодом"
    );
    let flag = SessionReadyFlag {
        symbol: "SOLUSDT".to_string(),
        profile_id: "marginal:side=bid".to_string(),
        n: 108,
        g: 7,
        ready_at_utc: "2026-04-01T00:00:00Z".to_string(),
        days: vec!["2026-03-01".to_string()],
    };
    write_session_ready_flag(&path, &flag).expect("флаг пишется раз");
    assert_eq!(
        require_session_ready_flag(&path).expect("с флагом — пропуск"),
        flag
    );
    assert!(
        write_session_ready_flag(&path, &flag).is_err(),
        "второй флаг — отказ"
    );
}

#[test]
fn session_progress_csv_round_trips_with_header() {
    let dir = tempfile::tempdir().expect("песочница");
    let path = session_progress_csv_path(dir.path(), "SOLUSDT", "cross:SOLUSDT|pulled|[0,1)");
    let rows = vec![SessionProgressRow {
        day_utc: "2026-03-01".to_string(),
        symbol: "SOLUSDT".to_string(),
        profile_id: "cross:SOLUSDT|pulled|[0,1)".to_string(),
        session_start_hours_utc: "02,14".to_string(),
        sessions: 2,
        n: 15,
        eligible: true,
    }];
    write_session_progress_csv(&path, &rows).expect("прогресс пишется");
    let raw = std::fs::read_to_string(&path).expect("прогресс читается");
    let header = raw.lines().next().expect("шапка обязана быть");
    assert_eq!(
        header,
        "day_utc,symbol,profile_id,session_start_hours_utc,sessions,n,eligible"
    );
    assert_eq!(
        read_session_progress_rows(&path).expect("круговой проход"),
        rows
    );
    assert_eq!(
        read_session_progress_rows(&dir.path().join("нет.csv")),
        Ok(Vec::new())
    );
    // Имя файла несёт слаг профиля: `:`/`|`/`,`/`[`/`)` не проезжают как есть.
    assert!(path
        .file_name()
        .unwrap()
        .to_string_lossy()
        .starts_with("progress-SOLUSDT-cross_SOLUSDT_pulled_"));
}
