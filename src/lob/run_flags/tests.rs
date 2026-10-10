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
    assert!(!f.fast_hold && !f.attempt_stats && f.sig_cache.is_none());
    assert_eq!(f.event_steps, EventSteps::Off);
    assert!(f.snapshot_lines().is_empty());
}

#[test]
fn one_vs_set_semantics() {
    let f = RunFlags::from_vars(vars(&[
        ("ALPHA_FAST_HOLD", "0"),
        ("ALPHA_HOLD_INDEX", "1"),
        ("ALPHA_ATTEMPT_STATS", ""),
    ]));
    assert!(!f.fast_hold && f.hold_index);
    assert!(f.attempt_stats, "задан любым значением");
}

#[test]
fn numbers_event_steps_and_paths() {
    let f = RunFlags::from_vars(vars(&[
        ("ALPHA_SIG_CACHE", "0"),
        ("ALPHA_TRIM_ROWS", "250"),
        ("ALPHA_EVENT_STEPS", "entry"),
        ("ALPHA_TOUCH_BIN_LEVEL", "x"),
        ("ALPHA_APPROACH_BIN_DIR", "/d"),
    ]));
    assert_eq!(f.sig_cache, None, "0 = выкл.");
    assert_eq!(f.trim_rows, Some(250));
    assert_eq!(f.event_steps, EventSteps::Entry);
    assert_eq!(f.touch_bin_level, None);
    assert_eq!(f.approach_bin_dir, Some(OsString::from("/d")));
    let g = RunFlags::from_vars(vars(&[
        ("ALPHA_SIG_CACHE", "64"),
        ("ALPHA_EVENT_STEPS", "1"),
    ]));
    assert_eq!((g.sig_cache, g.event_steps), (Some(64), EventSteps::All));
}

#[test]
fn snapshot_sorted_by_name_includes_unknown() {
    let f = RunFlags::from_vars(vars(&[
        ("ALPHA_Z_NEW", "v"),
        ("OTHER", "x"),
        ("ALPHA_FAST_HOLD", "1"),
        ("ALPHA_FAST_HOLD", "0"),
    ]));
    assert_eq!(
        f.snapshot_lines(),
        ["env:ALPHA_FAST_HOLD=0", "env:ALPHA_Z_NEW=v"]
    );
}
