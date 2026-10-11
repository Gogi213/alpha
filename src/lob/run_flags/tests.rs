use super::*;

fn vars(kv: &[(&str, &str)]) -> impl Iterator<Item = (OsString, OsString)> {
    kv.iter()
        .map(|(k, v)| (OsString::from(k), OsString::from(v)))
        .collect::<Vec<_>>()
        .into_iter()
}

#[test]
fn empty_env_is_default() {
    let f = RunFlags::from_vars(vars(&[("PATH", "/bin"), ("HOME", "/h")]));
    assert_eq!(f, RunFlags::default());
    assert!(!f.diag && f.approach_bin_dir.is_none());
    assert!(f.snapshot_lines().is_empty());
}

#[test]
fn set_vs_number_and_paths() {
    let f = RunFlags::from_vars(vars(&[
        ("ALPHA_DIAG", ""),
        ("ALPHA_APPROACH_BIN_LEVEL", "5"),
        ("ALPHA_APPROACH_BIN_DIR", "/d"),
    ]));
    assert!(f.diag, "задан любым значением");
    assert_eq!(f.approach_bin_level, Some(5));
    assert_eq!(f.approach_bin_dir, Some(OsString::from("/d")));
}

#[test]
fn snapshot_sorted_by_name_includes_unknown() {
    let f = RunFlags::from_vars(vars(&[
        ("ALPHA_Z_NEW", "v"),
        ("OTHER", "x"),
        ("ALPHA_E2E", "1"),
        ("ALPHA_E2E", "0"),
    ]));
    assert_eq!(f.snapshot_lines(), ["env:ALPHA_E2E=0", "env:ALPHA_Z_NEW=v"]);
}
