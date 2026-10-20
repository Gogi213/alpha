//! Контроль C (TK-333): `--ctx-shift-secs` против колонки `ctx_ms` файла кэша — `cache::verify_ctx_shift`.

use super::super::cache::verify_ctx_shift;

fn write(dir: &std::path::Path, name: &str, body: &str) {
    std::fs::create_dir_all(dir.join("2026-01-02")).unwrap();
    std::fs::write(dir.join("2026-01-02").join(name), body).unwrap();
}

fn check(dir: Option<&std::path::Path>, approach: bool, shift_ms: i64) -> anyhow::Result<()> {
    let days = vec!["2026-01-02".to_string()];
    verify_ctx_shift(dir, approach, "ADAUSDT", days.iter(), shift_ms)
}

#[test]
fn без_ctx_ms_и_флага_прежний_путь() {
    let dir = tempfile::tempdir().unwrap();
    write(
        dir.path(),
        "touches-ADAUSDT.csv",
        "start_ms,side\n5000,bid\n",
    );
    check(Some(dir.path()), false, 0).unwrap();
    check(None, false, 0).unwrap();
}

#[test]
fn файла_нет_решает_загрузчик() {
    let dir = tempfile::tempdir().unwrap();
    check(Some(dir.path()), true, 0).unwrap();
}

#[test]
fn флаг_без_ctx_ms_отказ() {
    let dir = tempfile::tempdir().unwrap();
    write(
        dir.path(),
        "touches-ADAUSDT.csv",
        "start_ms,side\n5000,bid\n",
    );
    assert!(check(Some(dir.path()), false, 3_600_000)
        .unwrap_err()
        .to_string()
        .contains("без колонки ctx_ms"));
    assert!(check(None, false, 3_600_000).is_err());
}

#[test]
fn ctx_ms_без_флага_отказ() {
    let dir = tempfile::tempdir().unwrap();
    write(
        dir.path(),
        "approaches-ADAUSDT.csv",
        "arm_ms,side,ctx_ms\n3605000,bid,5000\n",
    );
    assert!(check(Some(dir.path()), true, 0)
        .unwrap_err()
        .to_string()
        .contains("без --ctx-shift-secs"));
}

#[test]
fn ctx_ms_совпал_и_расхождение() {
    let dir = tempfile::tempdir().unwrap();
    write(
        dir.path(),
        "approaches-ADAUSDT.csv",
        "arm_ms,side,ctx_ms\n3605000,bid,5000\n3700000,ask,100000\n",
    );
    check(Some(dir.path()), true, 3_600_000).unwrap();
    let e = check(Some(dir.path()), true, -3_600_000)
        .unwrap_err()
        .to_string();
    assert!(e.contains("строка 2") && e.contains("3600000 мс"), "{e}");
    // вторая строка с чужим сдвигом
    write(
        dir.path(),
        "approaches-ADAUSDT.csv",
        "arm_ms,side,ctx_ms\n3605000,bid,5000\n7300000,ask,100000\n",
    );
    let e = check(Some(dir.path()), true, 3_600_000)
        .unwrap_err()
        .to_string();
    assert!(e.contains("строка 3"), "{e}");
}

#[test]
fn общий_файл_с_ctx_ms_проверяется_один_раз() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("touches-ADAUSDT.csv"),
        "start_ms,side,ctx_ms\n7000,bid,3000\n",
    )
    .unwrap();
    let days = vec!["2026-01-02".to_string(), "2026-01-03".to_string()];
    verify_ctx_shift(Some(dir.path()), false, "ADAUSDT", days.iter(), 4_000).unwrap();
    assert!(verify_ctx_shift(Some(dir.path()), false, "ADAUSDT", days.iter(), 0).is_err());
}
