//! Сайдкары счёта событий (`.events`, `.carry-events`): запись целиком и отказ от усечённой строки (С-23, С-24).
use super::carry::{cached_carry_count, cached_event_count, store_carry_count, store_event_count};
use crate::commands::lob::touches::stamp;
use std::path::PathBuf;

fn part_file(dir: &std::path::Path) -> PathBuf {
    let p = dir.join("2026-09-09.part");
    std::fs::write(&p, b"binlog bytes").unwrap();
    p
}

fn sidecar(part: &std::path::Path, suffix: &str) -> PathBuf {
    let mut s = part.as_os_str().to_owned();
    s.push(suffix);
    PathBuf::from(s)
}

/// Запись → чтение даёт то же число; штамп в сайдкаре — наносекунды, как у `.abin`; временных файлов не остаётся.
#[test]
fn event_sidecar_roundtrip_uses_ns_stamp_and_leaves_no_tmp() {
    let dir = tempfile::tempdir().unwrap();
    let part = part_file(dir.path());
    store_event_count(&part, 78);
    assert_eq!(cached_event_count(&part), Some(78));
    let (len, mtime_ns) = stamp(&part).unwrap();
    let text = std::fs::read_to_string(sidecar(&part, ".events")).unwrap();
    assert_eq!(text, format!("{len} {mtime_ns} 78\n"));
    let names: Vec<_> = std::fs::read_dir(dir.path())
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    assert_eq!(names.len(), 2, "часть и сайдкар, без .tmp: {names:?}");
}

/// Усечённая запись («78» → «7») не читается как валидная: промах и пересчёт, не n=7.
#[test]
fn truncated_event_sidecar_is_a_miss() {
    let dir = tempfile::tempdir().unwrap();
    let part = part_file(dir.path());
    let (len, mtime_ns) = stamp(&part).unwrap();
    let side = sidecar(&part, ".events");
    std::fs::write(&side, format!("{len} {mtime_ns} 7")).unwrap();
    assert_eq!(
        cached_event_count(&part),
        None,
        "без перевода строки — не годна"
    );
    std::fs::write(&side, format!("{len} {mtime_ns} 7\n")).unwrap();
    assert_eq!(cached_event_count(&part), Some(7), "целая строка читается");
    std::fs::write(&side, format!("{len} {mtime_ns} 7 9\n")).unwrap();
    assert_eq!(cached_event_count(&part), None, "лишнее поле — не годна");
}

#[test]
fn truncated_carry_sidecar_is_a_miss() {
    let dir = tempfile::tempdir().unwrap();
    let part = part_file(dir.path());
    store_carry_count(&part, 1_000, (78, true));
    assert_eq!(cached_carry_count(&part, 1_000), Some((78, true)));
    let (len, mtime_ns) = stamp(&part).unwrap();
    let side = sidecar(&part, ".carry-events");
    std::fs::write(&side, format!("{len} {mtime_ns} 1000 7")).unwrap();
    assert_eq!(cached_carry_count(&part, 1_000), None);
    std::fs::write(&side, format!("{len} {mtime_ns} 1000 78 1")).unwrap();
    assert_eq!(
        cached_carry_count(&part, 1_000),
        None,
        "без перевода строки"
    );
}

/// Файл изменился (иной размер) — сайдкар устарел.
#[test]
fn sidecar_of_a_changed_file_is_a_miss() {
    let dir = tempfile::tempdir().unwrap();
    let part = part_file(dir.path());
    store_event_count(&part, 5);
    std::fs::write(&part, b"binlog bytes, longer now").unwrap();
    assert_eq!(cached_event_count(&part), None);
}

/// С-06: снимок `ALPHA_*` — только эти переменные, по имени, значение как есть (в т.ч. `0`).
#[test]
fn env_flag_snapshot_lists_only_alpha_vars_sorted() {
    use std::ffi::OsString;
    let vars = [
        ("PATH", "/bin"),
        ("ALPHA_TICK_STATS", "0"),
        ("ALPHA_ATTEMPT_STATS", ""),
        ("MY_ALPHA_X", "1"),
    ]
    .map(|(k, v)| (OsString::from(k), OsString::from(v)));
    assert_eq!(
        super::plan::env_flag_lines(vars.into_iter()),
        ["env:ALPHA_ATTEMPT_STATS=", "env:ALPHA_TICK_STATS=0"]
    );
}
