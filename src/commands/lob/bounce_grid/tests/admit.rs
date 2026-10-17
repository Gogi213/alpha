use super::carry_cases::day_start_ns_for_test;
use super::keys::probe_touch;
use super::*;

/// TK-049 (admit_soa): `admits` по строкам `AdmitRow` даёт те же ответы, что по записям касаний.
#[test]
fn admit_rows_match_records() {
    use super::sets::{AdmitRow, TouchFilter};
    let mode = crate::lob::levels::H3Mode::Floor { h3_lots: 5 };
    let touches: Vec<TouchRecord> = (0..64_i64)
        .map(|i| TouchRecord {
            side: if i % 3 == 0 {
                crate::book::Side::Bid
            } else {
                crate::book::Side::Ask
            },
            price_tick: 1000 + i,
            size_at_touch: 3 + i % 17,
            size_max_before: 10 + i % 11,
            frontrun_lots: i % 7,
            frontrun_tick: (i % 4 != 0).then_some(990 + i),
            depth_behind_lots: i % 23,
            stack_levels: u32::try_from(i % 5).unwrap(),
            flow_1h_lots: (i % 6) * 40,
            level_birth_ms: -(i % 9) * 1_000,
            ..probe_touch()
        })
        .collect();
    let holds: Vec<bool> = touches
        .iter()
        .map(|t| mode.holds_at_touch(t) == Some(true))
        .collect();
    let rows: Vec<AdmitRow> = touches.iter().map(AdmitRow::of).collect();
    for spec in [
        "a:",
        "b:behind_min=100,stack_min=2",
        "c:frontrun,side=bid,age=3",
        "d:eaten=40,eaten_min=5,usd_min=20",
        "e:flow=2,frontrun_min=3",
    ] {
        let set = FilterSet::parse(spec).unwrap();
        let mut f = TouchFilter::from_set(&set, 0.01, 1.0, &[]);
        let want: Vec<bool> = touches
            .iter()
            .enumerate()
            .map(|(i, t)| f.admits_oracle(mode, i, t))
            .collect();
        f.holds = Some(&holds);
        f.rows = Some(&rows);
        let got: Vec<bool> = touches
            .iter()
            .enumerate()
            .map(|(i, _)| f.admits(i))
            .collect();
        assert_eq!(got, want, "{spec}");
    }
}

/// Г-07 (TK-012): ключи `behind_min=<%>` и `stack_min=<n>` — разбор, граница «равно проходит» в
/// `TouchFilter::admits` (целые лоты), шапка без ключей прежняя, с ключами — только заданные.
#[test]
fn g07_behind_and_stack_keys() {
    let s = FilterSet::parse("g:behind_min=150,stack_min=2").unwrap();
    assert_eq!((s.behind_min_pct, s.stack_min), (Some(150), Some(2)));
    let s = FilterSet::parse("g:behind_min=0").unwrap();
    assert_eq!((s.behind_min_pct, s.stack_min), (Some(0), None));
    for bad in [
        "x:behind_min=-1",
        "x:behind_min=1.5",
        "x:behind_min=a",
        "x:stack_min=0",
        "x:stack_min=-1",
        "x:stack_min=a",
        "x:stack_min=1,stack_min=2",
    ] {
        assert!(FilterSet::parse(bad).is_err(), "{bad}");
    }

    let set = FilterSet::parse("g:behind_min=150,stack_min=2").unwrap();
    let mode = crate::lob::levels::H3Mode::Floor { h3_lots: 1 };
    let f = super::sets::TouchFilter::from_set(&set, 0.01, 1.0, &[]);
    let t = |depth: i64, stack: u32| TouchRecord {
        size_at_touch: 10,
        depth_behind_lots: depth,
        stack_levels: stack,
        ..probe_touch()
    };
    assert!(
        f.admits_oracle(mode, 0, &t(15, 2)),
        "15 × 100 == 150 × 10, стопка 2 == 2 — проходит"
    );
    assert!(!f.admits_oracle(mode, 0, &t(14, 2)), "позади меньше порога");
    assert!(!f.admits_oracle(mode, 0, &t(15, 1)), "стопка меньше порога");
    let none = FilterSet::parse("n:").unwrap();
    let f = super::sets::TouchFilter::from_set(&none, 0.01, 1.0, &[]);
    assert!(
        f.admits_oracle(mode, 0, &t(0, 0)),
        "без ключей фильтр прежний"
    );

    assert_eq!(super::plan::g07_label(&none), "");
    assert_eq!(super::plan::g07_label(&set), " behind_min=150 stack_min=2");
}

/// П-17 (TK-279): `repeat_min` (Г-136/137) и `swept_min` (Г-135) — разбор, отказы, целочисленный допуск, шапка;
/// `repeat_min=0` пропускает всё, но ключ в идентичности набора (контроль Г-136).
#[test]
fn p17_repeat_and_swept_keys() {
    let s = FilterSet::parse("g:repeat_min=3,swept_min=250").unwrap();
    assert_eq!((s.repeat_min, s.swept_min_bp), (Some(3), Some(250)));
    for bad in [
        "x:repeat_min=-1",
        "x:repeat_min=1.5",
        "x:swept_min=-1",
        "x:swept_min=a",
        "x:repeat_min=1,repeat_min=2",
    ] {
        assert!(FilterSet::parse(bad).is_err(), "{bad}");
    }
    let mode = crate::lob::levels::H3Mode::Floor { h3_lots: 1 };
    let f = super::sets::TouchFilter::from_set(&s, 0.01, 1.0, &[]);
    let t = |rep: u32, swept: i64| TouchRecord {
        size_at_touch: 40,
        repeat_count: rep,
        swept_lots: swept,
        ..probe_touch()
    };
    assert!(f.admits_oracle(mode, 0, &t(3, 1)), "3 ≥ 3; 1×10⁴ == 250×40");
    assert!(!f.admits_oracle(mode, 0, &t(2, 1)), "repeat меньше");
    assert!(!f.admits_oracle(mode, 0, &t(3, 0)), "swept меньше");
    let ctl = FilterSet::parse("c:repeat_min=0").unwrap();
    let f = super::sets::TouchFilter::from_set(&ctl, 0.01, 1.0, &[]);
    assert!(
        f.admits_oracle(mode, 0, &t(0, 0)),
        "контроль k=0 пропускает всё"
    );
    assert_eq!(super::plan::g07_label(&ctl), " repeat_min=0");
    assert_eq!(super::plan::g07_label(&s), " repeat_min=3 swept_min=250");
    let r = FilterSet::parse("r:size_risk=5").unwrap();
    assert_eq!(r.size_risk, Some(5.0));
    assert_eq!(super::plan::g07_label(&r), " size_risk=5");
    for bad in ["x:size_risk=0", "x:size_risk=-1", "x:size_risk=a"] {
        assert!(FilterSet::parse(bad).is_err(), "{bad}");
    }
    let g = FilterSet::parse("g:entry_gate=cxl30q0.5").unwrap();
    assert_eq!(g.entry_gate, Some((30, 0.5)));
    assert_eq!(super::plan::g07_label(&g), " entry_gate=cxl30q0.5");
    // Проба порогов: недостижимый порог, своя идентичность.
    let p = FilterSet::parse("p:entry_gate=cxl45qprobe").unwrap();
    assert_eq!(p.entry_gate, Some((45, f64::INFINITY)));
    assert_eq!(super::plan::g07_label(&p), " entry_gate=cxl45qprobe");
    for bad in [
        "x:entry_gate=cxl0q1",
        "x:entry_gate=cxl30q0",
        "x:entry_gate=cxl30",
        "x:entry_gate=tape30q1",
        "x:entry_gate=cxl30qp",
    ] {
        assert!(FilterSet::parse(bad).is_err(), "{bad}");
    }
    let none = FilterSet::parse("n:").unwrap();
    assert_eq!(super::plan::g07_label(&none), "");
}

/// Г-07 сквозь сетку: `behind_min=0` не выбивает ничего (байты те же, шапка с ключом), огромный
/// `stack_min` выбивает всё; набор без ключей — шапка без `behind_min`/`stack_min`.
#[test]
fn g07_keys_through_grid() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid-g07");
    a.sets = vec![
        "all:".to_string(),
        "b0:behind_min=0".to_string(),
        "sbig:stack_min=1000000".to_string(),
    ];
    let m = run_bounce_grid(&a).unwrap();
    let by = |n: &str| m.sets.iter().find(|s| s.name == n).unwrap().clone();
    let body = |p: &std::path::Path| -> String {
        std::fs::read_to_string(p)
            .unwrap()
            .lines()
            .filter(|l| !l.starts_with('#'))
            .collect::<Vec<_>>()
            .join(
                "
",
            )
    };
    assert_eq!(body(&by("b0").rounds_path), body(&by("all").rounds_path));
    let (fh, big) = read_csv(&by("sbig").forms_path);
    assert!(big.iter().all(|r| col(&fh, r, "n_signals") == "0"));
    let head = std::fs::read_to_string(&by("b0").forms_path).unwrap();
    assert!(head.contains(" usd_min=None behind_min=0 ctx="), "{head}");
    let head = std::fs::read_to_string(&by("all").forms_path).unwrap();
    assert!(head.contains(" usd_min=None ctx="), "{head}");
    assert!(
        !head.contains("behind_min") && !head.contains("stack_min"),
        "{head}"
    );
}

/// TK-012 (`--p08-cols`): без флага `signals.csv` — прежние 11 колонок; с флагом — те же байты в
/// первых 11 и шесть колонок `ArmP08` подхода в конце; кэш без колонок П-08 — отказ; флаг без
/// `--busy-skip off` — отказ.
#[test]
fn p08_cols_in_signals_csv() {
    use crate::commands::lob::touches::{read_approaches_csv, run_touches, TouchesArgs};
    let dir = tempfile::tempdir().unwrap();
    fixture_root_approach(dir.path());
    let h3 = || H3Args {
        h3_mode: H3ModeArg::Floor,
        h3_lots: None,
        h3_usd: None,
        h3_strength_pct: None,
        h3_strength_window_bps: None,
    };
    let cache = dir.path().join("approaches");
    let summary = run_touches(&TouchesArgs {
        root: dir.path().to_path_buf(),
        symbol: "SOLUSDT".to_string(),
        h3: h3(),
        h3_k: None,
        warmup_ms: crate::commands::lob::DEFAULT_WARMUP_MS,
        repeat_window_ms: crate::commands::lob::DEFAULT_REPEAT_WINDOW_MS,
        out: Some(cache.join("2026-09-08").join("touches-SOLUSDT.csv")),
        approach_bps: vec![750],
        approach_min_age_secs: 0,
        moves: None,
        moves_window_ms: None,
        moves_bin_ms: None,
        numbers: None,
        allow_unverified: false,
        carry_age: false,
        emit_day: None,
        levels_out: None,
        minute_flow: None,
        r1_cols: false,
        wall_log: false,
    })
    .unwrap();
    let ap_path = summary.approaches_out[0].clone();
    let ap = read_approaches_csv(&ap_path).unwrap();
    assert_eq!(ap.len(), 1);
    let p = ap[0].approach.p08.expect("новый кэш несёт колонки П-08");

    let grid = |name: &str, p08: bool, busy_off: bool| {
        let mut a = args(dir.path(), false);
        a.signal = SignalArg::Approach;
        a.entry_form = vec!["ladder3x2..10".to_string()];
        a.stop_form = vec!["at".to_string()];
        a.take_form = vec!["1to1".to_string()];
        a.take_floor_fees = None;
        a.deadline_secs = vec![60];
        a.h3 = h3();
        a.warmup_ms = None;
        a.repeat_window_ms = None;
        a.touches_from = Some(cache.clone());
        if busy_off {
            a.busy_skip = "off".to_string();
        }
        a.p08_cols = p08;
        a.out_dir = dir.path().join(name);
        run_bounce_grid(&a)
    };
    let off = grid("g-off", false, true).unwrap();
    let on = grid("g-on", true, true).unwrap();
    let sig_off = std::fs::read_to_string(off.rounds_path.with_file_name("signals.csv")).unwrap();
    let sig_on = std::fs::read_to_string(on.rounds_path.with_file_name("signals.csv")).unwrap();
    let (h_off, r_off) = read_csv_from(sig_off.as_bytes());
    let (h_on, r_on) = read_csv_from(sig_on.as_bytes());
    assert_eq!(h_off.len(), 11, "без флага шапка прежняя: {h_off:?}");
    assert_eq!(h_on[..11], h_off[..]);
    assert_eq!(
        h_on[11..],
        [
            "traded_lots_at_arm",
            "size_max_at_arm",
            "size_monotonic_at_arm",
            "eat_60s_lots",
            "size_max_60s_lots",
            "depth_behind50_lots_at_arm"
        ]
    );
    assert!(!r_off.is_empty());
    assert_eq!(r_on.len(), r_off.len());
    for (a, b) in r_on.iter().zip(&r_off) {
        assert_eq!(a[..11], b[..]);
        assert_eq!(a[11..], super::outputs::p08_cells(&p));
    }

    // Старый кэш: те же строки без колонок П-08 — без флага счёт тот же байт в байт, с флагом отказ.
    let text = std::fs::read_to_string(&ap_path).unwrap();
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_reader(text.as_bytes());
    let head = r.headers().unwrap().clone();
    let keep: Vec<usize> = (0..head.len() - 6).collect();
    let mut w = csv::Writer::from_writer(Vec::new());
    w.write_record(keep.iter().map(|&i| &head[i])).unwrap();
    for rec in r.records() {
        let rec = rec.unwrap();
        w.write_record(keep.iter().map(|&i| &rec[i])).unwrap();
    }
    std::fs::write(&ap_path, w.into_inner().unwrap()).unwrap();
    assert!(read_approaches_csv(&ap_path).unwrap()[0]
        .approach
        .p08
        .is_none());
    let old = grid("g-old", false, true).unwrap();
    let body = |p: &std::path::Path| {
        std::fs::read_to_string(p)
            .unwrap()
            .lines()
            .filter(|l| !l.starts_with('#'))
            .collect::<Vec<_>>()
            .join("\n")
    };
    assert_eq!(
        body(&old.rounds_path.with_file_name("signals.csv")),
        body(&off.rounds_path.with_file_name("signals.csv"))
    );
    let err = grid("g-old-p08", true, true).unwrap_err().to_string();
    assert!(err.contains("--p08-cols"), "{err}");
    let err = grid("g-busy-on", true, false).unwrap_err().to_string();
    assert!(err.contains("--busy-skip off"), "{err}");
}

fn floor_h3() -> H3Args {
    H3Args {
        h3_mode: H3ModeArg::Floor,
        h3_lots: None,
        h3_usd: None,
        h3_strength_pct: None,
        h3_strength_window_bps: None,
    }
}

/// Кэш подходов `fixture_root_approach` (одна запись, полоса 750) без колонок R1;
/// возвращает путь к суточному файлу подходов.
fn approach_cache(dir: &std::path::Path) -> std::path::PathBuf {
    use crate::commands::lob::touches::{run_touches, TouchesArgs};
    fixture_root_approach(dir);
    let out = dir
        .join("approaches")
        .join("2026-09-08")
        .join("touches-SOLUSDT.csv");
    let summary = run_touches(&TouchesArgs {
        root: dir.to_path_buf(),
        symbol: "SOLUSDT".to_string(),
        h3: floor_h3(),
        h3_k: None,
        warmup_ms: crate::commands::lob::DEFAULT_WARMUP_MS,
        repeat_window_ms: crate::commands::lob::DEFAULT_REPEAT_WINDOW_MS,
        out: Some(out),
        approach_bps: vec![750],
        approach_min_age_secs: 0,
        moves: None,
        moves_window_ms: None,
        moves_bin_ms: None,
        numbers: None,
        allow_unverified: false,
        carry_age: false,
        emit_day: None,
        levels_out: None,
        minute_flow: None,
        r1_cols: false,
        wall_log: false,
    })
    .unwrap();
    assert_eq!(summary.approaches, 1, "фикстура взводит ровно один подход");
    summary.approaches_out[0].clone()
}

/// Сетка по подходам (лестница, стоп «в стену», тейк 1:1) на кэше `dir/approaches`.
fn approach_grid(
    dir: &std::path::Path,
    name: &str,
    sets: &[&str],
    r1_cols: bool,
    p08_cols: bool,
    busy_off: bool,
) -> anyhow::Result<BounceGridSummary> {
    let mut a = args(dir, false);
    a.signal = SignalArg::Approach;
    a.entry_form = vec!["ladder3x2..10".to_string()];
    a.stop_form = vec!["at".to_string()];
    a.take_form = vec!["1to1".to_string()];
    a.take_floor_fees = None;
    a.deadline_secs = vec![60];
    a.h3 = floor_h3();
    a.warmup_ms = None;
    a.repeat_window_ms = None;
    a.touches_from = Some(dir.join("approaches"));
    if busy_off {
        a.busy_skip = "off".to_string();
    }
    a.sets = sets.iter().map(|s| s.to_string()).collect();
    a.r1_cols = r1_cols;
    a.p08_cols = p08_cols;
    a.out_dir = dir.join(name);
    run_bounce_grid(&a)
}

fn approach_probe() -> crate::lob::levels::ApproachRecord {
    crate::lob::levels::ApproachRecord {
        side: crate::book::Side::Bid,
        price_tick: 1000,
        approach_index: 0,
        arm_ms: 0,
        arm_dist_bps: 10,
        level_birth_ms: 0,
        size_at_arm: 100,
        best_own_tick: 999,
        best_opp_tick: 1010,
        flow_1h_lots: 0,
        strength_e2: [-1, -1, -1],
        depth_behind_lots: 0,
        stack_levels_at_arm: 0,
        stack_span_bps_at_arm: 0,
        repeat_count_at_arm: 0,
        frontrun_lots_at_arm: 0,
        p08: None,
        r1: None,
        touch_start_ms: None,
        disarm_ms: 100,
        disarm_reason: crate::lob::levels::ApproachEnd::PriceLeft,
    }
}

/// TK-025: ключи `r1_<колонка>_min|_max` — разбор (обе границы любой колонки, границы `i64`, порядок
/// колонок вместо порядка записи), отказы (неизвестная колонка с перечнем, `R1_UNDEF`-порог, `min > max`,
/// повтор, не целое), условие `admits_r1` («равно проходит», `R1_UNDEF` и запись без R1 — нет) и шапка набора.
#[test]
fn r1_keys_parse_bounds_and_refuse() {
    use super::sets::{R1Bound, TouchFilter};
    use crate::lob::r1::{ArmR1, FLOW_N, R1_UNDEF};
    let names: Vec<&str> = ArmR1::names().collect();
    let at = |n: &str| names.iter().position(|x| *x == n).unwrap();

    let s =
        FilterSet::parse("g:r1_obi1_bp_min=500,r1_obi1_bp_max=900,r1_tape_press_lots_30s_min=-5")
            .unwrap();
    assert!(s.uses_r1());
    assert_eq!(
        s.r1,
        vec![
            R1Bound {
                col: at("tape_press_lots_30s"),
                min: Some(-5),
                max: None
            },
            R1Bound {
                col: at("obi1_bp"),
                min: Some(500),
                max: Some(900)
            },
        ],
        "границы — по порядку колонок, не по порядку записи"
    );
    assert!(!FilterSet::parse("n:").unwrap().uses_r1());

    for n in &names {
        for b in ["min", "max"] {
            let s = FilterSet::parse(&format!("x:r1_{n}_{b}=1")).unwrap();
            assert_eq!(s.r1.len(), 1, "{n} {b}");
        }
    }
    FilterSet::parse(&format!("x:r1_obi1_bp_min={}", i64::MAX)).unwrap();
    FilterSet::parse(&format!("x:r1_obi1_bp_max={}", R1_UNDEF + 1)).unwrap();

    for bad in [
        format!("x:r1_obi1_bp_min={R1_UNDEF}"),
        format!("x:r1_obi1_bp_max={R1_UNDEF}"),
        "x:r1_obi1_bp_min=5,r1_obi1_bp_max=4".to_string(),
        "x:r1_obi1_bp_max=4,r1_obi1_bp_min=5".to_string(),
        "x:r1_obi1_bp_min=1,r1_obi1_bp_min=2".to_string(),
        "x:r1_obi1_bp=1".to_string(),
        "x:r1_obi1_bp_mid=1".to_string(),
        "x:r1_obi1_bp_min=1.5".to_string(),
        "x:r1_obi1_bp_min=".to_string(),
        "x:r1_obi1_bp_min=a".to_string(),
        "x:r1_obi1_bp_min".to_string(),
        "x:r1_min=1".to_string(),
        "x:r1__min=1".to_string(),
        "x:r1_=1".to_string(),
    ] {
        assert!(FilterSet::parse(&bad).is_err(), "{bad}");
    }
    let err = FilterSet::parse("x:r1_nope_min=1").unwrap_err().to_string();
    assert!(err.contains("nope"), "{err}");
    for n in &names {
        assert!(err.contains(n), "в тексте отказа нет колонки {n}: {err}");
    }

    // Условие на записи подхода: [500, 900] включительно; поток и уровень — обе группы колонок.
    let set = FilterSet::parse("g:r1_obi1_bp_min=500,r1_obi1_bp_max=900,r1_cancel_60m_lots_min=7")
        .unwrap();
    let f = TouchFilter::from_set(&set, 0.01, 1.0, &[]);
    let rec = |obi: i64, cancel: i64| {
        let mut r1 = ArmR1::undefined();
        r1.flow[at("obi1_bp")] = obi;
        r1.level[at("cancel_60m_lots") - FLOW_N] = cancel;
        crate::lob::levels::ApproachRecord {
            r1: Some(Box::new(r1)),
            ..approach_probe()
        }
    };
    assert!(f.admits_r1(Some(&rec(500, 7))), "границы включительны");
    assert!(f.admits_r1(Some(&rec(900, 100))));
    assert!(!f.admits_r1(Some(&rec(499, 7))));
    assert!(!f.admits_r1(Some(&rec(901, 7))));
    assert!(!f.admits_r1(Some(&rec(700, 6))), "колонка уровня");
    assert!(
        !f.admits_r1(Some(&rec(R1_UNDEF, 7))),
        "не определено — не проходит"
    );
    assert!(!f.admits_r1(Some(&rec(700, R1_UNDEF))));
    assert!(
        !f.admits_r1(Some(&approach_probe())),
        "нет записи R1 — не проходит"
    );
    assert!(!f.admits_r1(None));
    // Минимум ниже любого значения не пускает R1_UNDEF и в этом случае.
    let low = FilterSet::parse("l:r1_obi1_bp_min=-1000000000000").unwrap();
    let f = TouchFilter::from_set(&low, 0.01, 1.0, &[]);
    assert!(f.admits_r1(Some(&rec(0, 0))));
    assert!(!f.admits_r1(Some(&rec(R1_UNDEF, 0))));
    let none = FilterSet::parse("n:").unwrap();
    let f = TouchFilter::from_set(&none, 0.01, 1.0, &[]);
    assert!(f.admits_r1(None), "без ключей фильтр прежний");
    assert!(f.admits_r1(Some(&approach_probe())));

    // Шапка набора: только заданные ключи, порядок канонический (идентичность не зависит от записи).
    let a = FilterSet::parse("a:r1_obi1_bp_max=900,r1_obi1_bp_min=500,r1_cancel_60m_lots_min=7")
        .unwrap();
    let b = FilterSet::parse("b:r1_cancel_60m_lots_min=7,r1_obi1_bp_min=500,r1_obi1_bp_max=900")
        .unwrap();
    assert_eq!(
        super::plan::r1_label(&a),
        " r1_obi1_bp_min=500 r1_obi1_bp_max=900 r1_cancel_60m_lots_min=7"
    );
    assert_eq!(super::plan::r1_label(&a), super::plan::r1_label(&b));
    assert_eq!(super::plan::r1_label(&none), "");
}

/// TK-025 сквозь сетку: значения R1 кладёт в кэш сам тест (проверяются провода — чтение, фильтр набора,
/// `signals.csv`, а не счёт признаков). Ключи пускают/режут сигналы по границам, `R1_UNDEF` не проходит
/// даже при сколь угодно низком минимуме; `--r1-cols` дописывает 61 колонку (FLOW_N + LEVEL_N) после колонок П-08; кэш без
/// колонок R1 и ключи/флаг — отказ с текстом, а без них счёт тот же байт в байт; флаг без `--signal
/// approach` / `--busy-skip off` и ключи без `--signal approach` — отказ.
#[test]
fn r1_cols_and_keys_through_grid() {
    use crate::commands::lob::touches::{r1_cells, read_approaches_csv};
    use crate::lob::r1::{ArmR1, FLOW_N, LEVEL_N};
    let dir = tempfile::tempdir().unwrap();
    let ap_path = approach_cache(dir.path());
    let old_text = std::fs::read_to_string(&ap_path).unwrap();
    let p08 = read_approaches_csv(&ap_path).unwrap()[0]
        .approach
        .p08
        .expect("новый кэш несёт колонки П-08");

    // Тот же файл подходов, но с колонками R1: obi1_bp = 500, cancel_60m_lots = 7, остальное не определено.
    let names: Vec<&str> = ArmR1::names().collect();
    let at = |n: &str| names.iter().position(|x| *x == n).unwrap();
    let mut r1 = ArmR1::undefined();
    r1.flow[at("obi1_bp")] = 500;
    r1.level[at("cancel_60m_lots") - FLOW_N] = 7;
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_reader(old_text.as_bytes());
    let head = r.headers().unwrap().clone();
    let mut w = csv::Writer::from_writer(Vec::new());
    w.write_record(head.iter().chain(names.iter().copied()))
        .unwrap();
    for rec in r.records() {
        let rec = rec.unwrap();
        w.write_record(rec.iter().map(str::to_string).chain(r1_cells(Some(&r1))))
            .unwrap();
    }
    let r1_text = w.into_inner().unwrap();
    std::fs::write(&ap_path, &r1_text).unwrap();
    assert_eq!(
        read_approaches_csv(&ap_path).unwrap()[0].approach.r1,
        Some(Box::new(r1))
    );

    let body = |p: &std::path::Path| -> String {
        std::fs::read_to_string(p)
            .unwrap()
            .lines()
            .filter(|l| !l.starts_with('#'))
            .collect::<Vec<_>>()
            .join("\n")
    };

    // Ключи наборов.
    let m = approach_grid(
        dir.path(),
        "g-keys",
        &[
            "all:",
            "ge:r1_obi1_bp_min=500",
            "eq:r1_obi1_bp_min=500,r1_obi1_bp_max=500",
            "above:r1_obi1_bp_min=501",
            "below:r1_obi1_bp_max=499",
            "undef:r1_tape_press_lots_30s_min=-1000000000",
            "lvl:r1_cancel_60m_lots_min=7,r1_cancel_60m_lots_max=7",
            "lvlhi:r1_cancel_60m_lots_min=8",
        ],
        false,
        false,
        false,
    )
    .unwrap();
    let by = |n: &str| m.sets.iter().find(|s| s.name == n).unwrap().clone();
    let n_signals = |n: &str| -> u64 {
        let (h, rows) = read_csv(&by(n).forms_path);
        rows.iter()
            .map(|r| col(&h, r, "n_signals").parse::<u64>().unwrap())
            .sum()
    };
    let all = n_signals("all");
    assert!(all > 0, "фикстура даёт сигнал подхода");
    for same in ["ge", "eq", "lvl"] {
        assert_eq!(n_signals(same), all, "{same}: граница равно проходит");
    }
    for none in ["above", "below", "undef", "lvlhi"] {
        assert_eq!(n_signals(none), 0, "{none}");
    }
    assert_eq!(body(&by("ge").rounds_path), body(&by("all").rounds_path));
    let head = std::fs::read_to_string(&by("eq").forms_path).unwrap();
    assert!(
        head.contains(" usd_min=None r1_obi1_bp_min=500 r1_obi1_bp_max=500 ctx="),
        "{head}"
    );
    let head = std::fs::read_to_string(&by("all").forms_path).unwrap();
    assert!(!head.contains("r1_"), "без ключей шапка прежняя: {head}");

    // `--r1-cols`: те же 11 колонок, затем (с --p08-cols — после шести колонок П-08) 61 колонка R1 (FLOW_N + LEVEL_N).
    let signals = |m: &BounceGridSummary, name: &str| {
        let p = m
            .sets
            .iter()
            .find(|s| s.name == name)
            .unwrap()
            .rounds_path
            .with_file_name("signals.csv");
        read_csv(&p)
    };
    let off = approach_grid(dir.path(), "g-off", &["all:"], false, false, true).unwrap();
    let on = approach_grid(
        dir.path(),
        "g-on",
        &["all:", "ge:r1_obi1_bp_min=500", "hi:r1_obi1_bp_min=501"],
        true,
        false,
        true,
    )
    .unwrap();
    let both = approach_grid(dir.path(), "g-both", &["all:"], true, true, true).unwrap();
    let (h_off, r_off) = signals(&off, "all");
    let (h_on, r_on) = signals(&on, "all");
    let (h_both, r_both) = signals(&both, "all");
    assert_eq!(h_off.len(), 11, "без флага шапка прежняя: {h_off:?}");
    assert_eq!(h_on.len(), 11 + FLOW_N + LEVEL_N);
    assert_eq!(h_on[..11], h_off[..]);
    let want: Vec<String> = names.iter().map(|s| s.to_string()).collect();
    assert_eq!(
        h_on[11..],
        want[..],
        "61 колонка R1 в порядке ArmR1::names()"
    );
    assert_eq!(h_both.len(), 11 + 6 + FLOW_N + LEVEL_N);
    assert_eq!(h_both[..11], h_off[..]);
    assert_eq!(h_both[17..], want[..], "R1 — после колонок П-08");
    assert!(!r_off.is_empty());
    assert_eq!(r_on.len(), r_off.len());
    assert_eq!(r_both.len(), r_off.len());
    for ((a, b), c) in r_on.iter().zip(&r_off).zip(&r_both) {
        assert_eq!(a[..11], b[..]);
        assert_eq!(a[11..], r1_cells(Some(&r1)));
        assert_eq!(c[..11], b[..]);
        assert_eq!(c[11..17], super::outputs::p08_cells(&p08));
        assert_eq!(c[17..], r1_cells(Some(&r1)));
    }
    assert_eq!(
        signals(&on, "ge").1,
        r_on,
        "ключ, пускающий запись, сигналы не режет"
    );
    assert!(
        signals(&on, "hi").1.is_empty(),
        "ключ выше значения — ни одной строки"
    );

    // Отказы флага и ключей по плану сетки.
    let err = approach_grid(dir.path(), "g-e1", &["all:"], true, false, false)
        .unwrap_err()
        .to_string();
    assert!(err.contains("--busy-skip off"), "{err}");
    let mut a = args(dir.path(), false);
    a.r1_cols = true;
    let err = run_bounce_grid(&a).unwrap_err().to_string();
    assert!(
        err.contains("--r1-cols") && err.contains("--signal approach"),
        "{err}"
    );
    let mut a = args(dir.path(), false);
    a.sets = vec!["k:r1_obi1_bp_min=1".to_string()];
    let err = run_bounce_grid(&a).unwrap_err().to_string();
    assert!(
        err.contains("r1_") && err.contains("--signal approach"),
        "{err}"
    );

    // Кэш без колонок R1: флаг и ключи — отказ с текстом, без них счёт прежний.
    std::fs::write(&ap_path, &old_text).unwrap();
    let err = approach_grid(
        dir.path(),
        "g-old-key",
        &["k:r1_obi1_bp_min=1"],
        false,
        false,
        false,
    )
    .unwrap_err()
    .to_string();
    assert!(err.contains("колонками R1"), "{err}");
    let err = approach_grid(dir.path(), "g-old-flag", &["all:"], true, false, true)
        .unwrap_err()
        .to_string();
    assert!(err.contains("колонками R1"), "{err}");
    let old = approach_grid(dir.path(), "g-old", &["all:"], false, false, false).unwrap();
    assert_eq!(
        body(&old.sets[0].rounds_path),
        body(&by("all").rounds_path),
        "кэш без R1 и без ключей — тот же счёт, что на кэше с R1"
    );

    // Наполовину записанные колонки R1 — отказ чтения, а не «кэш без R1».
    let (header, raw) = {
        std::fs::write(&ap_path, &r1_text).unwrap();
        let mut rd = csv::Reader::from_path(&ap_path).unwrap();
        let h: Vec<String> = rd.headers().unwrap().iter().map(str::to_string).collect();
        let rows: Vec<Vec<String>> = rd
            .records()
            .map(|r| r.unwrap().iter().map(str::to_string).collect())
            .collect();
        (h, rows)
    };
    let mut w = csv::Writer::from_path(&ap_path).unwrap();
    w.write_record(&header[..header.len() - 1]).unwrap();
    for row in &raw {
        w.write_record(&row[..row.len() - 1]).unwrap();
    }
    w.flush().unwrap();
    drop(w);
    // Нечитаемый кэш подходов сетка и так пропускает со счётчиком (причина — в stderr): символ не считается.
    let skipped = approach_grid(dir.path(), "g-partial", &["all:"], false, false, false).unwrap();
    assert_eq!(skipped.symbols_without_touches, 1);
    assert_eq!(skipped.symbols_done, 0);
}

/// TK-014: `weat<X>s<W>{m|l|a}<Y>` — разбор, канонические имена и отказы.
#[test]
fn wall_eat_exit_form_parses_canonically() {
    use super::forms::WallEatMode;
    let f = ExitForm::parse("weat30s60m10").unwrap();
    assert_eq!(
        f,
        ExitForm::WallEat {
            pct: 30.0,
            secs: 60,
            mode: WallEatMode::Market,
            btc_bps: 10.0,
            btc: None,
        }
    );
    assert_eq!(f.label(), "weat30s60m10");
    assert!(f.needs_btc());
    for ok in ["weat33.5s3600l0", "weat100s1a2.5"] {
        assert_eq!(ExitForm::parse(ok).unwrap().label(), ok);
    }
    assert!(matches!(
        ExitForm::parse("weat50s10l5").unwrap(),
        ExitForm::WallEat {
            mode: WallEatMode::Local,
            ..
        }
    ));
    assert!(!ExitForm::parse("eat30").unwrap().needs_btc());
    for bad in [
        "weat",
        "weat0s60m10",
        "weat101s60m10",
        "weat30s0m10",
        "weat30s3601m10",
        "weat30s60x10",
        "weat30s60m",
        "weat30s60m-1",
        "weat30.0s60m10",
        "weat30s60m10.0",
        "weat30s060m10",
    ] {
        assert!(ExitForm::parse(bad).is_err(), "{bad} должен отказать");
    }
}

/// TK-014: форма `weat*` без `--btc-minutes` — отказ до счёта.
#[test]
fn wall_eat_exit_form_requires_btc_minutes() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.exit_form = vec!["weat30s60m10".to_string()];
    let err = run_bounce_grid(&a).unwrap_err().to_string();
    assert!(err.contains("--btc-minutes"), "{err}");
}

/// TK-014: ряд BTC сливается из нескольких файлов; не покрывший сутки (час до, дедлайн после)
/// или без `--day` — отказ до счёта.
#[test]
fn btc_minutes_merge_and_coverage_check() {
    use super::plan::load_btc_minutes;
    let dir = tempfile::tempdir().unwrap();
    let day0 = day_start_ns_for_test("2026-09-15") / 1_000_000;
    let (a, b) = (dir.path().join("a.csv"), dir.path().join("b.csv"));
    let mut sa = String::from("minute_ms,open,high,low,close,volume\n");
    let mut sb = String::new();
    // a: с часа до суток до полудня; b: с полудня до конца суток + 2 ч.
    let mut m = day0 - 2 * 3_600_000;
    while m <= day0 + 26 * 3_600_000 {
        let line = format!("{m},1,1,1,100,1\n");
        if m < day0 + 12 * 3_600_000 {
            sa.push_str(&line);
        } else {
            sb.push_str(&line);
        }
        m += 60_000;
    }
    std::fs::write(&a, sa).unwrap();
    std::fs::write(&b, sb).unwrap();
    let days = vec!["2026-09-15".to_string()];
    assert!(load_btc_minutes(&[a.clone(), b.clone()], &days, 3600).is_ok());
    assert!(
        load_btc_minutes(std::slice::from_ref(&a), &days, 3600).is_err(),
        "конец суток не покрыт"
    );
    assert!(
        load_btc_minutes(&[a.clone(), b.clone()], &days, 3 * 3600).is_err(),
        "дедлайн после суток не покрыт"
    );
    assert!(
        load_btc_minutes(&[a, b], &[], 3600).is_err(),
        "без --day — отказ"
    );
}

#[test]
fn set_agemax_parses_into_the_upper_age_bound() {
    let set = FilterSet::parse("g87:age=2700,agemax=3600").unwrap();
    assert_eq!(set.min_age_secs, Some(2700));
    assert_eq!(set.max_age_secs, Some(3600));
    assert!(FilterSet::parse("g87:agemax=x").is_err());
    assert_eq!(FilterSet::parse("g87:age=2700").unwrap().max_age_secs, None);
}

/// TK-115 (Г-114): доля `f` — суффикс `f1`/`f3`; половина — прежнее имя без суффикса; чужой суффикс — отказ.
#[cfg(feature = "r2")]
#[test]
fn half_forms_take_quarter_fraction_suffix() {
    assert_eq!(
        ExitForm::parse("halfstop").unwrap(),
        ExitForm::HalfStop { q8: 4 }
    );
    assert_eq!(
        ExitForm::parse("halfstopf1").unwrap(),
        ExitForm::HalfStop { q8: 2 }
    );
    assert_eq!(
        ExitForm::parse("halflevelf3").unwrap(),
        ExitForm::HalfLevel { q8: 6 }
    );
    assert_eq!(ExitForm::HalfLevel { q8: 6 }.label(), "halflevelf3");
    assert!(ExitForm::parse("halfstopf2").is_err());
    assert!(ExitForm::parse("halfstopf4").is_err());
}

/// TK-115 (Г-106): `nostop<X2>` — X2 ∈ {1, 2, 4}; другое — отказ.
#[cfg(feature = "r2")]
#[test]
fn nostop_form_parses_only_the_grid_multipliers() {
    assert_eq!(
        ExitForm::parse("nostop2").unwrap(),
        ExitForm::NoStop { x8: 8 }
    );
    assert_eq!(ExitForm::NoStop { x8: 16 }.label(), "nostop4");
    // F1 TK-189: новые точки — свои имена, прежние побайтно.
    assert_eq!(
        ExitForm::parse("nostopx1").unwrap(),
        ExitForm::NoStop { x8: 1 }
    );
    assert_eq!(ExitForm::parse("nostopx64").unwrap().label(), "nostopx64");
    assert!(ExitForm::parse("nostopx8").is_err());
    assert!(ExitForm::parse("nostop3").is_err());
    assert_eq!(
        ExitForm::parse("halfstopg3").unwrap(),
        ExitForm::HalfStop { q8: 3 }
    );
    assert_eq!(
        ExitForm::parse("halflevelg7").unwrap().label(),
        "halflevelg7"
    );
    assert!(ExitForm::parse("halfstopg4").is_err());
    assert_eq!(
        ExitForm::parse("pyre10w2").unwrap(),
        ExitForm::PyrReinstall { n: 10, u12: 2 }
    );
    assert_eq!(
        ExitForm::parse("pyre3u2").unwrap(),
        ExitForm::PyrReinstall { n: 3, u12: 8 }
    );
    assert_eq!(ExitForm::parse("pynw5w10").unwrap().label(), "pynw5w10");
    assert!(ExitForm::parse("pynw5w8").is_err());
    assert_eq!(
        ExitForm::parse("pyfresh12").unwrap(),
        ExitForm::PyrFresh { parts: 12 }
    );
    assert_eq!(ExitForm::parse("conv16a20").unwrap().label(), "conv16a20");
    assert!(ExitForm::parse("nostop3").is_err());
    assert!(ExitForm::parse("nostop").is_err());
}

/// TK-115 (e65): `wall2`/`wall2x` — единственные имена формы цели за второй стеной.
#[cfg(feature = "r2")]
#[test]
fn wall2_form_parses_both_offsets() {
    assert_eq!(ExitForm::parse("wall2").unwrap(), ExitForm::Wall2 { k: -1 });
    assert_eq!(ExitForm::Wall2 { k: 1 }.label(), "wall2x");
    assert!(ExitForm::parse("wall3").is_err());
    // TK-189: ось k ∈ −3..+3, имена канонические, коды 1/2 прежние
    for (name, k, code) in [
        ("wall2m3", -3, 6),
        ("wall2m2", -2, 4),
        ("wall2", -1, 1),
        ("wall20", 0, 3),
        ("wall2x", 1, 2),
        ("wall2p2", 2, 5),
        ("wall2p3", 3, 7),
    ] {
        let f = ExitForm::parse(name).unwrap();
        assert_eq!(f, ExitForm::Wall2 { k });
        assert_eq!(f.label(), name);
        assert_eq!(crate::lob::strategy::wall2_code(k), code);
        assert_eq!(crate::lob::strategy::WALL2_K[usize::from(code) - 1], k);
    }
    for bad in [
        "wall2m4", "wall2p0", "wall2m0", "wall2p1", "wall2m1", "wall2k1", "wall2p",
    ] {
        assert!(ExitForm::parse(bad).is_err(), "{bad}");
    }
}

/// TK-115 (e65): журнал стен суток читается из суточного файла кэша; нет файла — отказ, не пустой журнал.
#[test]
fn wall_log_reads_day_file_and_refuses_when_missing() {
    let dir = tempfile::tempdir().unwrap();
    let day = dir.path().join("2026-01-05");
    std::fs::create_dir_all(&day).unwrap();
    std::fs::write(
        day.join("walls-BTCUSDT.csv"),
        "day_utc,ts_ms,side,price_tick,wall\n2026-01-05,200,ask,101,0\n2026-01-05,100,ask,101,1\n2026-01-06,50,bid,90,1\n",
    )
    .unwrap();
    let ev = super::cache::cached_walls(dir.path(), "BTCUSDT", "2026-01-05").unwrap();
    assert_eq!(ev.iter().map(|e| e.ts_ms).collect::<Vec<_>>(), [100, 200]);
    assert!(ev[0].wall && !ev[1].wall);
    assert!(super::cache::cached_walls(dir.path(), "ETHUSDT", "2026-01-05").is_err());
}

/// TK-115 (Г-133): `chase<мс>` — окно в миллисекундах, ноль и мусор — отказ.
#[cfg(feature = "r2")]
#[test]
fn chase_form_parses_window_in_ms() {
    assert_eq!(
        ExitForm::parse("chase2500").unwrap(),
        ExitForm::Chase { ms: 2500 }
    );
    assert_eq!(ExitForm::Chase { ms: 2500 }.label(), "chase2500");
    assert!(ExitForm::parse("chase0").is_err());
    assert!(ExitForm::parse("chase").is_err());
}

/// TK-115 (Г-112/116): `tape<Q>`/`cxl<Q>` — порог числом ровно как в имени, ноль и мусор — отказ.
#[cfg(feature = "r2")]
#[test]
fn tape_cxl_forms_parse_threshold() {
    assert_eq!(
        ExitForm::parse("tape30q1.5").unwrap(),
        ExitForm::Tape { w: 30, q: 1.5 }
    );
    assert_eq!(
        ExitForm::parse("cxl60q0.25").unwrap(),
        ExitForm::Cxl { w: 60, q: 0.25 }
    );
    assert_eq!(ExitForm::Tape { w: 15, q: 1.5 }.label(), "tape15q1.5");
    assert!(ExitForm::parse("tape30q0").is_err());
    assert!(ExitForm::parse("tape0q1").is_err());
    assert!(ExitForm::parse("tape1.5").is_err());
    assert!(ExitForm::parse("cxl").is_err());
    assert!(ExitForm::parse("tape30q1.50").is_err());
    // Г-112 F1: Q_m ≤ 0 — «мера > 0»: tape<W>qp, имя round-trip.
    let p = ExitForm::parse("tape30qp").unwrap();
    assert_eq!(
        p,
        ExitForm::Tape {
            w: 30,
            q: f64::MIN_POSITIVE
        }
    );
    assert_eq!(p.label(), "tape30qp");
    assert_eq!(ExitForm::parse("cxl5qp").unwrap().label(), "cxl5qp");
    assert!(ExitForm::parse("tape30qpp").is_err());
}

/// П-17 (TK-279, Г-101): ключи режима `lreg_t/lreg_f`, `sreg_t/sreg_f` — парой, внутри допуска B1 (`btc4h` задан);
/// шапка набора несёт порог и множитель, два разных множителя — две идентичности; «сильная» половина — `btc4h ≤ T`.
#[test]
fn p17_regime_keys_pair_and_gate() {
    let s =
        FilterSet::parse("g:btc4h_max=-44.55,lreg_t=-120.5,lreg_f=2500,sreg_t=-90,sreg_f=20000")
            .unwrap();
    assert_eq!(
        s.ladder_regime.map(|r| (r.t_bps, r.f_bp)),
        Some((-120.5, 2500))
    );
    assert_eq!(
        s.stop_regime.map(|r| (r.t_bps, r.f_bp)),
        Some((-90.0, 20000))
    );
    assert_eq!(
        super::plan::g07_label(&s),
        " lreg=-120.5:2500 sreg=-90:20000"
    );
    let s2 = FilterSet::parse("g:btc4h_max=-44.55,lreg_t=-120.5,lreg_f=5000").unwrap();
    assert_ne!(super::plan::g07_label(&s), super::plan::g07_label(&s2));
    for bad in [
        "x:btc4h_max=-44.55,lreg_t=-120",
        "x:btc4h_max=-44.55,sreg_f=5000",
        "x:lreg_t=-120,lreg_f=5000",
        "x:btc4h_max=-44.55,lreg_t=-120,lreg_f=0",
        "x:btc4h_max=-44.55,lreg_t=a,lreg_f=5000",
    ] {
        assert!(FilterSet::parse(bad).is_err(), "{bad}");
    }
    let r = s.ladder_regime;
    let mut axes = [None; crate::commands::lob::bounce_grid::sets::CTX_AXES.len()];
    axes[super::sets::BTC4H_AXIS] = Some(-130.0);
    let ctx = [
        super::sets::TouchContext { axes },
        super::sets::TouchContext {
            axes: [None; crate::commands::lob::bounce_grid::sets::CTX_AXES.len()],
        },
    ];
    let spec = super::sets::FilterSpec::of_set(&s, &ctx);
    assert_eq!(
        spec.regime_factor(r, 0),
        Some(0.25),
        "-130 ≤ -120.5: сильная половина"
    );
    assert_eq!(
        spec.regime_factor(r, 1),
        None,
        "нет btc4h — режим не включается"
    );
    assert_eq!(spec.regime_factor(None, 0), None);
}

/// П-18 (TK-289, ось 6б): `strength_min=<%>` — разбор, отказы, окно ±20 bps (индекс 1), сила не определена (−1) не
/// входит даже при 0, шапка.
#[test]
fn p18_strength_min_key() {
    let s = FilterSet::parse("s:strength_min=317.5").unwrap();
    assert_eq!(s.strength_min_e2, Some(31_750));
    for bad in [
        "x:strength_min=-1",
        "x:strength_min=a",
        "x:strength_min=inf",
        "x:strength_min=1,strength_min=2",
    ] {
        assert!(FilterSet::parse(bad).is_err(), "{bad}");
    }
    let mode = crate::lob::levels::H3Mode::Floor { h3_lots: 1 };
    let f = super::sets::TouchFilter::from_set(&s, 0.01, 1.0, &[]);
    let t = |w20: i64| TouchRecord {
        size_at_touch: 40,
        strength_e2: [99_999, w20, 99_999],
        ..probe_touch()
    };
    assert!(f.admits_oracle(mode, 0, &t(31_750)), "ровно порог — вход");
    assert!(!f.admits_oracle(mode, 0, &t(31_749)), "ниже порога");
    let z = FilterSet::parse("z:strength_min=0").unwrap();
    let f = super::sets::TouchFilter::from_set(&z, 0.01, 1.0, &[]);
    assert!(f.admits_oracle(mode, 0, &t(0)));
    assert!(!f.admits_oracle(mode, 0, &t(-1)), "без соседей — не вход");
    assert_eq!(super::plan::g07_label(&s), " strength_min=317.5");
}
