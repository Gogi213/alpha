use super::*;

fn vars(kv: &[(&str, &str)]) -> impl Iterator<Item = (OsString, OsString)> {
    kv.iter()
        .map(|(k, v)| (OsString::from(k), OsString::from(v)))
        .collect::<Vec<_>>()
        .into_iter()
}

#[test]
fn empty_env_is_default_all_off() {
    let f = RunFlags::from_vars(vars(&[("PATH", "/bin"), ("HOME", "/h")]));
    assert_eq!(f, RunFlags::default());
    assert!(!f.skip_same && !f.diag && f.sig_cache.is_none());
    assert!(f.snapshot_lines().is_empty());
}

#[test]
fn one_vs_set_semantics() {
    let f = RunFlags::from_vars(vars(&[("ALPHA_SKIP_SAME", "0"), ("ALPHA_DIAG", "")]));
    assert!(!f.skip_same);
    assert!(f.diag, "задан любым значением");
}

#[test]
fn numbers_event_steps_and_paths() {
    let f = RunFlags::from_vars(vars(&[
        ("ALPHA_SIG_CACHE", "0"),
        ("ALPHA_APPROACH_BIN_DIR", "/d"),
    ]));
    assert_eq!(f.sig_cache, None, "0 = выкл.");
    assert_eq!(f.approach_bin_dir, Some(OsString::from("/d")));
    let g = RunFlags::from_vars(vars(&[("ALPHA_SIG_CACHE", "64")]));
    assert_eq!(g.sig_cache, Some(64));
}

#[test]
fn snapshot_sorted_by_name_includes_unknown() {
    let f = RunFlags::from_vars(vars(&[
        ("ALPHA_Z_NEW", "v"),
        ("OTHER", "x"),
        ("ALPHA_SKIP_SAME", "1"),
        ("ALPHA_SKIP_SAME", "0"),
    ]));
    assert_eq!(
        f.snapshot_lines(),
        ["env:ALPHA_SKIP_SAME=0", "env:ALPHA_Z_NEW=v"]
    );
}
