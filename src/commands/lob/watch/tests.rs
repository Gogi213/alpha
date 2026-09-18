use super::*;
use crate::binlog::{Header, Record, Writer};
use crate::commands::lob::H3ModeArg;

fn write_session_dir(
    root: &Path,
    session_id: &str,
    symbol: &str,
    started_utc: &str,
    start_hour_utc: u32,
    verified: bool,
    frames: &[Vec<Record>],
) -> PathBuf {
    let dir = root.join(session_id);
    std::fs::create_dir_all(&dir).expect("каталог сессии создаётся");
    let json = format!(
        "{{\"started_utc\":\"{started_utc}\",\"start_hour_utc\":{start_hour_utc},\
         \"instruments\":[\"{symbol}\"]}}"
    );
    std::fs::write(dir.join("session.json"), json).expect("session.json пишется");
    let header = Header {
        tick_e9: super::super::test_support::FIX_TICK_E9,
        step_e9: 1_000_000,
        max_records_per_frame: 4096,
    };
    let mut w = Writer::create(Vec::new(), header, 1).expect("заголовок годен");
    for f in frames {
        w.write_frame(f).expect("кадр пишется");
    }
    w.flush().expect("сброс");
    // Таск 19: `lob session` пишет `<SYMBOL>-<день>.binlog`, не
    // `<SYMBOL>.binlog` — фикстура следует той же раскладке, которую
    // теперь ждёт `session_binlog_for`.
    let day = &started_utc[..10];
    std::fs::write(dir.join(format!("{symbol}-{day}.binlog")), w.into_inner())
        .expect("бинлог пишется");
    if verified {
        std::fs::write(dir.join(format!("verify-{symbol}.status")), "ok")
            .expect("маркер сверки пишется");
    }
    dir
}

/// Один уровень `pulled` (снят на 20% максимума — граница правила
/// 70/20) на биде: подходит под `marginal:outcome=pulled` и
/// `marginal:side=bid` разом.
fn pulled_bid_frames() -> Vec<Vec<Record>> {
    super::super::test_support::three_level_frames()
}

#[test]
fn watch_counts_profile_across_sessions_and_skips_unverified() {
    let dir = tempfile::tempdir().expect("песочница");
    let root = dir.path();
    let frames = pulled_bid_frames();
    // Семь годных суток, одна верифицированная сессия каждая:
    // `three_level_frames` даёт два `pulled` на сессию (бид на 100 и
    // аск на 105 — оба ни разу не торговались, оба закрываются на
    // `finish()`) — G=7, n=14 не хватает n>=100 порога, флаг не встаёт
    // (см. отдельный тест на самом счётчике для случая, где порог
    // достигается).
    for d in 1..=7u32 {
        write_session_dir(
            root,
            &format!("2026-05-{d:02}T020000Z"),
            "SOLUSDT",
            &format!("2026-05-{d:02}T02:00:00Z"),
            2,
            true,
            &frames,
        );
    }
    // Восьмая сессия — отдельные сутки, без сверки. Наблюдения в её
    // бинлоге (n=2) не должны попасть в счёт: сессия выброшена целиком.
    write_session_dir(
        root,
        "2026-05-08T050000Z",
        "SOLUSDT",
        "2026-05-08T05:00:00Z",
        5,
        false,
        &frames,
    );
    let args = WatchArgs {
        root: root.to_path_buf(),
        symbol: "SOLUSDT".to_string(),
        profile: "marginal:outcome=pulled".to_string(),
        h3: H3Args {
            h3_mode: H3ModeArg::Percentile,
            h3_lots: Some(1),
            h3_usd: None,
            h3_strength_pct: None,
            h3_strength_window_bps: None,
        },
        warmup_ms: 0,
        repeat_window_ms: DEFAULT_REPEAT_WINDOW_MS,
        now_utc: Some("2026-06-01T00:00:00Z".to_string()),
    };
    let summary = run_watch(&args).expect("прогон watch");
    assert_eq!(summary.days, 8, "восемь суток, включая негодные");
    assert_eq!(
        summary.n, 14,
        "по два pulled на каждую из семи годных сессий"
    );
    assert_eq!(summary.g, 7);
    assert!(
        summary.flag.is_none(),
        "n=14 не достигает CONFIRM_MIN_N=100 — не готово"
    );
    let rows = crate::lob::watch::read_session_progress_rows(&summary.progress)
        .expect("прогресс читается");
    assert_eq!(rows.len(), 8);
    let junk = rows
        .iter()
        .find(|r| r.day_utc == "2026-05-08")
        .expect("строка на восьмые сутки есть");
    assert!(
        !junk.eligible,
        "непрошедшая сверку единственная сессия — сутки негодны"
    );
    assert_eq!(junk.n, 0);
    let first = rows
        .iter()
        .find(|r| r.day_utc == "2026-05-01")
        .expect("строка на первые сутки есть");
    assert_eq!(first.session_start_hours_utc, "02");
    assert_eq!(first.n, 2, "два pulled на сессию, см. pulled_bid_frames");
}

/// Критерий приёмки таска 23 для `watch`: один каталог с частями за
/// двое суток (верхний `session.json` — от последней сессии, 02 мая
/// 14 ч) — две строки прогресса, `G = 2`, час старта каждой — свой.
/// До таска 23 обе части шли за 02 мая: одни сутки, `G = 1`.
#[test]
fn watch_attributes_days_and_hours_per_part_inside_one_directory() {
    let dir = tempfile::tempdir().expect("песочница");
    let root = dir.path();
    let frames = pulled_bid_frames();
    let session = root.join("collect");
    std::fs::create_dir_all(&session).unwrap();
    for day in ["2026-05-01", "2026-05-02"] {
        let header = Header {
            tick_e9: super::super::test_support::FIX_TICK_E9,
            step_e9: 1_000_000,
            max_records_per_frame: 4096,
        };
        let mut w = Writer::create(Vec::new(), header, 1).unwrap();
        for f in &frames {
            w.write_frame(f).unwrap();
        }
        w.flush().unwrap();
        let path = crate::commands::record::day_file_path(&session, "SOLUSDT", day, 1);
        std::fs::write(path, w.into_inner()).unwrap();
    }
    std::fs::write(
        session.join("session.json"),
        "{\"started_utc\":\"2026-05-02T14:00:00Z\",\"start_hour_utc\":14,\
         \"instruments\":[\"SOLUSDT\"],\"binlog_files\":[\
         {\"symbol\":\"SOLUSDT\",\"part\":1,\"started_utc\":\"2026-05-01T02:00:00Z\"},\
         {\"symbol\":\"SOLUSDT\",\"part\":1,\"started_utc\":\"2026-05-02T14:00:00Z\"}]}",
    )
    .unwrap();
    std::fs::write(session.join("verify-SOLUSDT.status"), "ok").unwrap();

    let args = WatchArgs {
        root: root.to_path_buf(),
        symbol: "SOLUSDT".to_string(),
        profile: "marginal:outcome=pulled".to_string(),
        h3: H3Args {
            h3_mode: H3ModeArg::Percentile,
            h3_lots: Some(1),
            h3_usd: None,
            h3_strength_pct: None,
            h3_strength_window_bps: None,
        },
        warmup_ms: 0,
        repeat_window_ms: DEFAULT_REPEAT_WINDOW_MS,
        now_utc: Some("2026-06-01T00:00:00Z".to_string()),
    };
    let summary = run_watch(&args).expect("прогон watch");
    assert_eq!(summary.days, 2, "двое суток из одного каталога");
    assert_eq!(summary.g, 2);
    assert_eq!(summary.n, 4, "по два pulled на каждую часть");
    let rows = crate::lob::watch::read_session_progress_rows(&summary.progress)
        .expect("прогресс читается");
    let hours: Vec<(String, String)> = rows
        .iter()
        .map(|r| (r.day_utc.clone(), r.session_start_hours_utc.clone()))
        .collect();
    assert_eq!(
        hours,
        vec![
            ("2026-05-01".to_string(), "02".to_string()),
            ("2026-05-02".to_string(), "14".to_string()),
        ],
        "сутки и час — из части, не из верхнего session.json"
    );
}

#[test]
fn watch_leaves_the_forbidden_horizon_metric_uncomputed() {
    // Критерий приёмки таска 07 — грепом по собственному исходнику и по
    // ядру счётчика: ни один из двух файлов не тянет запрещённую метрику
    // движения середины по имени (Decision 16 гейта 2.1). Литерал собран
    // из частей: спелись целиком — проверка сработала бы сама на себя.
    let forbidden = concat!("mark", "out");
    const SRC: &str = include_str!("../watch.rs");
    assert!(
        !SRC.contains(forbidden),
        "lob watch не вычисляет запрещённую метрику ни в каком виде"
    );
    // Только раздел таска 07 (старый C1/C2-код выше в том же файле
    // упоминает эту метрику в прозе как свой собственный отказ от неё —
    // не относится к новому счётчику и не должно триггерить проверку).
    const CORE_SRC: &str = include_str!("../../../lob/watch.rs");
    let core_new_section = CORE_SRC
        .split_once("pub struct SessionTally")
        .expect("таск 07 добавил SessionTally в lob/watch.rs")
        .1;
    assert!(
        !core_new_section.contains(forbidden),
        "счётчик n/G не вычисляет запрещённую метрику ни в каком виде"
    );
}

#[test]
fn profile_matcher_rejects_distance_based_profiles_with_a_clear_error() {
    assert!(ProfileMatcher::parse("cross:SOLUSDT|pulled|[0,1)", "SOLUSDT").is_err());
    assert!(ProfileMatcher::parse("marginal:size=[1,2)", "SOLUSDT").is_err());
    assert!(ProfileMatcher::parse("marginal:life=[0,1s)", "SOLUSDT").is_err());
    assert!(ProfileMatcher::parse("marginal:dist=[0,1)", "SOLUSDT").is_err());
    assert!(ProfileMatcher::parse("marginal:side=bid", "SOLUSDT").is_ok());
    assert!(ProfileMatcher::parse("marginal:instrument=SOLUSDT", "SOLUSDT").is_ok());
    assert!(
        ProfileMatcher::parse("marginal:instrument=NEARUSDT", "SOLUSDT").is_err(),
        "инструмент профиля обязан совпасть с --symbol"
    );
}

#[test]
fn session_dirs_skip_entries_without_session_json_or_symbol_binlog() {
    let dir = tempfile::tempdir().expect("песочница");
    let root = dir.path();
    std::fs::create_dir_all(root.join("not-a-session")).unwrap();
    std::fs::write(root.join("stray-file.txt"), "мусор").unwrap();
    write_session_dir(
        root,
        "2026-05-01T020000Z",
        "SOLUSDT",
        "2026-05-01T02:00:00Z",
        2,
        true,
        &pulled_bid_frames(),
    );
    let args = WatchArgs {
        root: root.to_path_buf(),
        symbol: "NEARUSDT".to_string(),
        profile: "marginal:side=bid".to_string(),
        h3: H3Args {
            h3_mode: H3ModeArg::Percentile,
            h3_lots: Some(1),
            h3_usd: None,
            h3_strength_pct: None,
            h3_strength_window_bps: None,
        },
        warmup_ms: 0,
        repeat_window_ms: DEFAULT_REPEAT_WINDOW_MS,
        now_utc: Some("2026-06-01T00:00:00Z".to_string()),
    };
    // NEARUSDT не писался ни в одной сессии — ни у одного каталога нет
    // NEARUSDT.binlog, значит суток вовсе нет, а не ошибка.
    let summary = run_watch(&args).expect("прогон watch без сессий символа — не ошибка");
    assert_eq!(summary.days, 0);
    assert_eq!(summary.n, 0);
}
