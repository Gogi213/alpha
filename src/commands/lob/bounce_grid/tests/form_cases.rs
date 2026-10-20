use super::*;

/// `--deadline-secs`: сетка с 30 мин и 4 ч — формы и шапка несут свой набор,
/// чужое число — отказ.
#[test]
fn deadline_secs_flag_extends_the_grid_to_30min_and_4h() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.stop_form = vec!["pct2".to_string()];
    a.take_form = vec!["1to1".to_string()];
    a.take_floor_fees = None;
    a.deadline_secs = vec![14_400, 60, 1800, 60];
    a.out_dir = dir.path().join("grid-dl");
    let m = run_bounce_grid(&a).unwrap();
    assert_eq!(m.forms, 3, "дубли схлопнуты, порядок по возрастанию");
    let (fh, forms) = read_csv(&m.forms_path);
    let labels: Vec<&str> = forms.iter().map(|r| col(&fh, r, "form")).collect();
    assert_eq!(
        labels,
        ["pct2-1to1-60", "pct2-1to1-1800", "pct2-1to1-14400"]
    );
    let head = std::fs::read_to_string(&m.forms_path).unwrap();
    assert!(head.contains("deadlines=[60, 1800, 14400]"), "{head}");
    a.deadline_secs = vec![21_600, 28_800];
    a.out_dir = dir.path().join("grid-dl-long");
    assert_eq!(run_bounce_grid(&a).unwrap().forms, 2);
    a.deadline_secs = vec![900];
    a.out_dir = dir.path().join("grid-dl-bad");
    assert!(run_bounce_grid(&a).is_err());
}

// -----------------------------------------------------------------------
// F5 (план 2026-09-20, В-74): срок жизни входа — сетка `--entry-ttl-secs`
// плюс условия «стена снята»/«цена ушла», их числа приходят флагами.
// -----------------------------------------------------------------------

/// Ось `--entry-ttl-secs` — декартово произведение форм: у прежнего режима
/// (`touch`) имя прежнее (`form_label`, гейт «те же круги»), у секунд —
/// суффикс `-ttl<значение>`, иначе имена совпали бы и сетка отказала.
#[test]
fn entry_ttl_axis_multiplies_forms_with_distinct_labels() {
    let stops = [StopForm::Pct(2.0)];
    let takes = [TakeForm::OneToOne];
    let base = grid_forms(&stops, &takes, None, &[3600]);
    assert_eq!(base.len(), 1);
    assert_eq!(base[0].label, "pct2-1to1-3600");
    assert_eq!(base[0].entry_ttl, EntryTtl::Touch);

    let multi = grid_forms_with_entry_ttl(
        &stops,
        &takes,
        None,
        &[3600],
        &[EntryTtl::Touch, EntryTtl::Secs(60), EntryTtl::Secs(300)],
        &[ExitForm::None],
    );
    assert_eq!(multi.len(), 3, "одна форма × три значения ttl");
    assert_eq!(multi[0].label, "pct2-1to1-3600");
    assert_eq!(multi[1].label, "pct2-1to1-3600-ttl60");
    assert_eq!(multi[2].label, "pct2-1to1-3600-ttl300");
    let seen: std::collections::BTreeSet<&str> = multi.iter().map(|f| f.label).collect();
    assert_eq!(seen.len(), 3, "имена обязаны различаться: {seen:?}");
    // Прежний режим — прежнее имя: «золото» F3/F4 и вердикт читают его как есть.
    assert_eq!(base[0].label, multi[0].label);
}

/// Разбор `--entry-ttl-secs` (F5): пусто — `touch`, числа — только из сетки
/// замера В-74; условия F5 без `--h3-usd`/`--band-exit-bps` — отказ, а не
/// молчаливый пропуск (умолчаний в коде нет).
#[test]
fn entry_ttl_values_come_from_the_measured_grid_and_need_their_numbers() {
    assert_eq!(parse_entry_ttls(&[]).unwrap(), vec![EntryTtl::Touch]);
    assert_eq!(
        parse_entry_ttls(&[
            "300".to_string(),
            "60".to_string(),
            "touch".to_string(),
            "wall".to_string(),
        ])
        .unwrap(),
        vec![
            EntryTtl::Touch,
            EntryTtl::Wall,
            EntryTtl::Secs(60),
            EntryTtl::Secs(300)
        ],
        "порядок детерминирован, как у дедлайнов"
    );
    assert!(
        parse_entry_ttls(&["45".to_string()]).is_err(),
        "чужое число — отказ, а не расширение сетки"
    );
    assert!(parse_entry_ttls(&["soon".to_string()]).is_err());

    assert!(ensure_entry_conditions_args(true, None, Some(2.0)).is_err());
    assert!(ensure_entry_conditions_args(true, Some(10_000.0), None).is_err());
    assert!(ensure_entry_conditions_args(true, Some(10_000.0), Some(2.0)).is_ok());
    // Режим `touch` — прежний: спутников не требует.
    assert!(ensure_entry_conditions_args(false, None, None).is_ok());
}

/// Прогон фикстуры с осью `--entry-ttl-secs` (F5): формы умножаются, шапка
/// несёт режим и полосу, `forms.csv` — колонки снятий по причинам.
#[test]
fn grid_with_entry_ttl_axis_writes_the_header_and_cancel_columns() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    // Порог В-66 в деньгах даёт `level_floor_qty = --h3-usd / цена`.
    a.h3 = H3Args {
        h3_mode: H3ModeArg::Notional,
        h3_lots: None,
        h3_usd: Some(0.002),
        h3_strength_pct: None,
        h3_strength_window_bps: None,
    };
    a.entry_ttl_secs = vec!["60".to_string(), "300".to_string()];
    a.band_exit_bps = Some(20.0);
    a.out_dir = dir.path().join("grid-f5");
    let m = run_bounce_grid(&a).unwrap();
    // 2 стопа × 1 тейк × 4 дедлайна × 2 значения ttl.
    assert_eq!(m.forms, 16);

    let head = std::fs::read_to_string(&m.forms_path).unwrap();
    assert!(head.contains(" entry_ttl=60+300 "), "{head}");
    assert!(head.contains(" band_exit_bps=20 "), "{head}");

    let (fh, forms) = read_csv(&m.forms_path);
    assert_eq!(forms.len(), 16, "строка на форму: {forms:?}");
    for name in [
        "n_entry_cancelled_ttl",
        "n_entry_cancelled_wall_dead",
        "n_entry_cancelled_price_left",
    ] {
        assert!(fh.iter().any(|h| h == name), "нет колонки {name}: {fh:?}");
    }
    let labels: std::collections::BTreeSet<&str> = forms
        .iter()
        .map(|r| col(&fh, r, "form"))
        .collect::<std::collections::BTreeSet<_>>();
    assert_eq!(labels.len(), 16, "имена форм различаются");
    assert!(labels.iter().any(|l| l.ends_with("-ttl60")));
    assert!(labels.iter().any(|l| l.ends_with("-ttl300")));
    for r in &forms {
        let cancelled: u64 = [
            "n_entry_cancelled_ttl",
            "n_entry_cancelled_wall_dead",
            "n_entry_cancelled_price_left",
        ]
        .iter()
        .map(|name| col(&fh, r, name).parse::<u64>().unwrap())
        .sum();
        let signals: u64 = col(&fh, r, "n_signals").parse().unwrap();
        assert!(cancelled <= signals, "снятий больше сигналов: {r:?}");
    }
}

/// Условия F5 (кроме `touch`) без обязательных чисел — отказ до прогона:
/// порог В-66 в деньгах (`--h3-usd`) и полоса (`--band-exit-bps`) приходят
/// флагами, умолчаний в коде нет.
#[test]
fn entry_ttl_conditions_refuse_without_their_numbers() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);

    // Режим floor: `--h3-usd` не читается вовсе — «стена снята» не проверить.
    let mut a = args(dir.path(), false);
    a.stop_form = vec!["pct2".to_string()];
    a.take_form = vec!["1to1".to_string()];
    a.take_floor_fees = None;
    a.entry_ttl_secs = vec!["60".to_string()];
    a.band_exit_bps = Some(20.0);
    a.out_dir = dir.path().join("grid-f5-no-usd");
    let err = run_bounce_grid(&a).expect_err("без --h3-usd условия F5 не проверить");
    assert!(err.to_string().contains("--h3-usd"), "{err}");

    // Полоса не задана — изобретать число нечем.
    let mut b = args(dir.path(), false);
    b.stop_form = vec!["pct2".to_string()];
    b.take_form = vec!["1to1".to_string()];
    b.take_floor_fees = None;
    b.h3 = H3Args {
        h3_mode: H3ModeArg::Notional,
        h3_lots: None,
        h3_usd: Some(0.002),
        h3_strength_pct: None,
        h3_strength_window_bps: None,
    };
    b.entry_ttl_secs = vec!["60".to_string()];
    b.band_exit_bps = None;
    b.out_dir = dir.path().join("grid-f5-no-band");
    let err = run_bounce_grid(&b).expect_err("без --band-exit-bps условия F5 не проверить");
    assert!(err.to_string().contains("--band-exit-bps"), "{err}");

    // Прежний режим `touch`: ни порога, ни полосы не требует (гейт).
    let mut c = args(dir.path(), false);
    c.stop_form = vec!["pct2".to_string()];
    c.take_form = vec!["1to1".to_string()];
    c.take_floor_fees = None;
    c.entry_ttl_secs = vec!["touch".to_string()];
    c.out_dir = dir.path().join("grid-f5-touch");
    assert!(run_bounce_grid(&c).is_ok());
}

// -----------------------------------------------------------------------
// F7/F8 (план 2026-09-20, Б-75): ось формы выхода `--exit-form`
// (`none`/`eat<X>`/`gone<W>`) — имена форм, колонки артефактов, гейт.
// -----------------------------------------------------------------------

/// Разбор `--exit-form` (F7): `none` — прежний выход; `eat<X>`/`gone<W>` —
/// проценты в (0, 100]; чужое имя или число вне диапазона — отказ, а не
/// молчаливый `none` (иначе испытание шло бы не под тем именем).
#[test]
fn exit_forms_parse_and_refuse_unknown_or_out_of_range_values() {
    assert_eq!(ExitForm::parse("none").unwrap(), ExitForm::None);
    assert_eq!(
        ExitForm::parse("eat50").unwrap(),
        ExitForm::Eat { pct: 50.0 }
    );
    assert_eq!(
        ExitForm::parse("gone20").unwrap(),
        ExitForm::Gone { pct: 20.0 }
    );
    // Трейл после снятия (владелец 23.09): имя несёт и порог снятия, и откат.
    let gt = ExitForm::parse("gone90tr0.5").unwrap();
    assert_eq!(
        gt,
        ExitForm::GoneTrail {
            pct: 90.0,
            trail_pct: 0.5
        }
    );
    assert_eq!(gt.label(), "gone90tr0.5");
    // Безубыток после снятия (владелец 23.09): мягкий и жёсткий.
    assert_eq!(
        ExitForm::parse("gone90be").unwrap(),
        ExitForm::GoneBe {
            pct: 90.0,
            hard: false
        }
    );
    assert_eq!(
        ExitForm::parse("gone90bex").unwrap(),
        ExitForm::GoneBe {
            pct: 90.0,
            hard: true
        }
    );
    assert!(ExitForm::parse("gone90bey").is_err());
    // Стоп на уровень стены после снятия (владелец 26.09): буфер в bps, три режима.
    for (spec, mode, buffer_bps) in [
        ("gone20wall0", WallStopMode::Soft, 0.0),
        ("gone20wallx0", WallStopMode::Hard, 0.0),
        ("gone20wallk0", WallStopMode::Keep, 0.0),
        ("gone90wall5", WallStopMode::Soft, 5.0),
        ("gone90wallx2.5", WallStopMode::Hard, 2.5),
        ("gone90wallk2.5", WallStopMode::Keep, 2.5),
    ] {
        let form = ExitForm::parse(spec).unwrap();
        assert_eq!(
            form,
            ExitForm::GoneWall {
                pct: if spec.starts_with("gone20") {
                    20.0
                } else {
                    90.0
                },
                mode,
                buffer_bps
            },
            "{spec}"
        );
        assert_eq!(form.label(), spec);
    }
    for bad in [
        "gone20wall",
        "gone20wallx",
        "gone20wall-1",
        "gone20wall10000",
        "gone20wally5",
        "gone20wallkx5",
        "gone20wallk",
        "gone0wall5",
        "gone20wall05",
        "gone20wall5.0",
    ] {
        assert!(
            ExitForm::parse(bad).is_err(),
            "{bad:?} — не форма стопа на стену, обязан быть отказ"
        );
    }
    for bad in [
        "eat",
        "eat0",
        "eat101",
        "gone0",
        "gone-5",
        "goneabc",
        "eaten50",
        "",
        "gone90tr0",
        "gone90tr",
        "gone0tr1",
        "gone90tr0.50",
    ] {
        assert!(
            ExitForm::parse(bad).is_err(),
            "{bad:?} — не форма выхода, обязан быть отказ"
        );
    }
    // Имя формы — число как есть: дробный порог не округляется в имени
    // (иначе `eat33.7` считалось бы под именем `eat34`, ревью 21.09).
    assert_eq!(ExitForm::parse("eat33.7").unwrap().label(), "eat33.7");
    assert_eq!(ExitForm::parse("gone12.5").unwrap().label(), "gone12.5");
    assert_eq!(ExitForm::parse("eat50").unwrap().label(), "eat50");
    // Пустой флаг — прежний выход (`none`), как у остальных осей сетки.
    assert_eq!(parse_exit_forms(&[]).unwrap(), vec![ExitForm::None]);
    assert_eq!(
        parse_exit_forms(&["eat50".to_string(), "eat50".to_string()]).unwrap(),
        vec![ExitForm::Eat { pct: 50.0 }],
        "повтор флага свёрнут — иначе имена форм совпали бы"
    );
}

/// Ось `--exit-form` — декартово произведение форм: у прежнего выхода (`none`)
/// имя прежнее (`form_label`, гейт «те же круги»), у остальных — хвостовое
/// поле `-eat<X>`/`-gone<W>`, иначе имена совпали бы и сетка отказала.
#[test]
fn exit_form_axis_multiplies_forms_with_distinct_labels() {
    let stops = [StopForm::Pct(2.0)];
    let takes = [TakeForm::OneToOne];
    let base = grid_forms(&stops, &takes, None, &[3600]);
    assert_eq!(base.len(), 1);
    assert_eq!(base[0].label, "pct2-1to1-3600");
    assert_eq!(base[0].exit_form, ExitForm::None);

    let multi = grid_forms_with_axes(
        &stops,
        &takes,
        None,
        &[3600],
        &[EntryTtl::Touch],
        &[EntryForm::SingleFrontrun],
        &[
            ExitForm::None,
            ExitForm::Eat { pct: 50.0 },
            ExitForm::Gone { pct: 20.0 },
        ],
    );
    assert_eq!(multi.len(), 3, "одна форма × три формы выхода");
    assert_eq!(multi[0].label, "pct2-1to1-3600");
    assert_eq!(multi[1].label, "pct2-1to1-3600-eat50");
    assert_eq!(multi[2].label, "pct2-1to1-3600-gone20");
    let seen: std::collections::BTreeSet<&str> = multi.iter().map(|f| f.label).collect();
    assert_eq!(seen.len(), 3, "имена обязаны различаться: {seen:?}");
    // Прежний выход — прежнее имя: гейт «те же круги» и вердикт читают его как есть.
    assert_eq!(base[0].label, multi[0].label);
    // Имена читаются вердиктом: хвостовое поле выхода разбирается.
    assert_eq!(
        super::super::super::bounce_verdict::parse_form_fields("pct2-1to1-3600-eat50")
            .unwrap()
            .exit,
        Some("eat50".to_string())
    );
}

/// Прогон фикстуры с осью `--exit-form` (F7/F8): формы умножаются, шапка
/// несёт ось выхода, `forms.csv` — колонки причин F7 и средняя доля входа.
#[test]
fn grid_with_exit_form_axis_writes_the_exit_columns_and_header() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.stop_form = vec!["pct2".to_string()];
    a.take_form = vec!["1to1".to_string()];
    a.take_floor_fees = None;
    a.deadline_secs = vec![3600];
    a.exit_form = vec!["eat50".to_string(), "gone20".to_string()];
    a.out_dir = dir.path().join("grid-f8");
    let m = run_bounce_grid(&a).unwrap();
    assert_eq!(m.forms, 2, "одна базовая форма × две формы выхода");

    let head = std::fs::read_to_string(&m.forms_path).unwrap();
    assert!(head.contains(" exit_forms=eat50+gone20 "), "{head}");

    let (fh, forms) = read_csv(&m.forms_path);
    for name in ["n_eaten_by_trades", "n_wall_gone", "mean_fill_frac"] {
        assert!(fh.iter().any(|h| h == name), "нет колонки {name}: {fh:?}");
    }
    let labels: std::collections::BTreeSet<&str> =
        forms.iter().map(|r| col(&fh, r, "form")).collect();
    assert_eq!(
        labels,
        ["pct2-1to1-3600-eat50", "pct2-1to1-3600-gone20"]
            .into_iter()
            .collect::<std::collections::BTreeSet<_>>(),
        "имя формы несёт поле выхода"
    );
    // Средняя доля входа — число в [0, 1], а не пустая колонка.
    for r in &forms {
        let mean: f64 = col(&fh, r, "mean_fill_frac").parse().unwrap();
        assert!((0.0..=1.0).contains(&mean), "доля вне [0,1]: {r:?}");
    }

    // Прежний выход (`none`) — колонки те же, имена прежние (гейт).
    let mut b = args(dir.path(), false);
    b.stop_form = vec!["pct2".to_string()];
    b.take_form = vec!["1to1".to_string()];
    b.take_floor_fees = None;
    b.deadline_secs = vec![3600];
    b.exit_form = vec!["none".to_string()];
    b.out_dir = dir.path().join("grid-f8-none");
    let m2 = run_bounce_grid(&b).unwrap();
    let (fh2, forms2) = read_csv(&m2.forms_path);
    assert_eq!(col(&fh2, &forms2[0], "form"), "pct2-1to1-3600");
    let head2 = std::fs::read_to_string(&m2.forms_path).unwrap();
    assert!(head2.contains(" exit_forms=none "), "{head2}");
}

/// Гейт «те же круги» (F7/F8): пустая ось выхода печатает `exit_forms=none`,
/// а формы остаются трёхпольными — прежний прогон воспроизводится байт в байт
/// по именам и значениям прежних колонок.
#[test]
fn empty_exit_form_axis_keeps_the_previous_names() {
    let stops = [StopForm::Pct(2.0)];
    let takes = [TakeForm::OneToOne];
    let a = grid_forms_with_axes(
        &stops,
        &takes,
        None,
        &[3600],
        &[EntryTtl::Touch],
        &[EntryForm::SingleFrontrun],
        &parse_exit_forms(&[]).unwrap(),
    );
    let b = grid_forms(&stops, &takes, None, &[3600]);
    assert_eq!(a.len(), b.len());
    for (x, y) in a.iter().zip(&b) {
        assert_eq!(x.label, y.label);
        assert_eq!(x.exit_form, ExitForm::None);
    }
}

// -----------------------------------------------------------------------
// F8b (аудит этапа F 21.09, Р4/В5/Р2): счётчики форм адресуются **по имени
// колонки** — позиционная запись `forms.csv` перепутанные поля не ловит.
// -----------------------------------------------------------------------

/// Круг с заданными агрегатами F7/F8b и без кругов: тесту нужна только
/// строка `forms.csv`, а не данные.
fn run_with_exit_counters(
    eaten_by_trades: u64,
    wall_gone: u64,
    entry_cancel_timeout: u64,
    exit_cancel_timeout: u64,
) -> BounceRun {
    BounceRun {
        profile: 0,
        signals: 1,
        fills: Vec::new(),
        fill_signal: Vec::new(),
        fill_reason: Vec::new(),
        fill_exit_ns: Vec::new(),
        exits: crate::lob::backtest::ExitTally {
            eaten_by_trades,
            wall_gone,
            ..Default::default()
        },
        entry_rejected: 0,
        rejected_postonly: 0,
        entry_crossed: 0,
        entry_cancelled_ttl: 0,
        entry_cancelled_wall_dead: 0,
        entry_cancelled_price_left: 0,
        entry_cancelled_cxl_gate: 0,
        entry_cancelled_cancel_timeout: entry_cancel_timeout,
        exit_cancel_timeout,
        orphan_fills: 0,
        gate_measures: Vec::new(),
        spread_at_entry: Vec::new(),
        submitted_signal: Vec::new(),
        retreat_window_entries: 0,
        busy_signal: Vec::new(),
        busy_wait_ns_max: 0,
        round_ns_max: 0,
        misses: Default::default(),
        observations: Vec::new(),
        incomplete: false,
        residual_flattened: 0,
        trace: Vec::new(),
    }
}

/// F8b (Р4): строка `forms.csv` несёт каждый счётчик в **своей** колонке —
/// «съели» и «сняли» не путаются местами, а счётчики потолка отмены не
/// попадают в чужие. Числа шапки и строки совпадают по длине и порядку.
#[test]
fn forms_row_puts_every_counter_into_its_own_column() {
    let run = run_with_exit_counters(3, 7, 11, 13);
    let row = forms_row(
        "SOLUSDT",
        "2026-09-08",
        "pct2-1to1-60-eat50",
        &[],
        &run,
        0,
        0.5,
        None,
        false,
        true,
    );
    assert_eq!(
        row.len(),
        FORMS_HEADER.len(),
        "полей в строке — как в шапке"
    );
    let at = |name: &str| -> &str {
        let i = FORMS_HEADER
            .iter()
            .position(|h| *h == name)
            .unwrap_or_else(|| panic!("нет колонки {name}"));
        &row[i]
    };
    assert_eq!(at("n_eaten_by_trades"), "3", "«съели» — своя колонка");
    assert_eq!(at("n_wall_gone"), "7", "«сняли» — своя колонка");
    assert_eq!(at("n_entry_cancelled_cancel_timeout"), "11");
    assert_eq!(at("n_exit_cancel_timeout"), "13");
    assert_eq!(at("n_orphan_fills"), "0", "F8c: сирот в фикстуре нет");
    assert_eq!(at("mean_fill_frac"), "0.500000");
    assert_eq!(at("form"), "pct2-1to1-60-eat50");
    assert_eq!(at("n_carried"), "0", "без --carry-root переноса нет");
    assert_eq!(at("carry_unverified"), "false");
}

/// R6: без `--carry-root` (`with_carry=false`) строка/шапка `forms.csv` —
/// прежние 33 колонки, `n_carried`/`carry_unverified` не пишутся вовсе (не
/// «0»/«false» в лишней колонке) — иначе гейт «те же байты» (`gate-g10.sh`,
/// md5 `forms.csv`) видит расхождение без единого включённого флага.
#[test]
fn forms_row_omits_carry_columns_without_the_flag() {
    let run = run_with_exit_counters(0, 0, 0, 0);
    let row = forms_row(
        "SOLUSDT",
        "2026-09-08",
        "pct2-1to1-60-eat50",
        &[],
        &run,
        0,
        0.5,
        None,
        false,
        false,
    );
    assert_eq!(
        row.len(),
        FORMS_HEADER.len() - FORMS_HEADER_CARRY_LEN,
        "без --carry-root — 33 колонки, не 35"
    );
    assert_eq!(
        row.last().map(String::as_str),
        Some("0"),
        "последняя колонка — n_orphan_fills, не carry_unverified"
    );
}

/// Перенос круга через полночь (`--carry-root`): `n_carried` считает круги,
/// чей выход (`fill_exit_ns`) уже на данных D+1, не путая их с обычными
/// (граница — ровно вторая полночь фикстуры).
#[test]
fn forms_row_counts_carried_rounds_by_exit_time_past_the_midnight_boundary() {
    let mut run = run_with_exit_counters(0, 0, 0, 0);
    run.fills = vec![
        crate::lob::backtest::Fill {
            dir: 1,
            entry_px: 1.0,
            exit_px: 1.01,
            qty: 0.1,
            entry_taker: false,
            exit_taker: false,
            entry_vwap: 1.0,
            fill_frac: 1.0,
            legs_filled: 1,
            legs_rejected: 0,
            fill_by_cross: false,
            tape_press: 0.0,
            tape_press_ws: [0.0; crate::lob::backtest::TAPE_WS_MAX],
            cxl_press: 0.0,
            chase_wait_ns: -1,
        },
        crate::lob::backtest::Fill {
            dir: 1,
            entry_px: 1.0,
            exit_px: 0.99,
            qty: 0.1,
            entry_taker: false,
            exit_taker: true,
            entry_vwap: 1.0,
            fill_frac: 1.0,
            legs_filled: 1,
            legs_rejected: 0,
            fill_by_cross: false,
            tape_press: 0.0,
            tape_press_ws: [0.0; crate::lob::backtest::TAPE_WS_MAX],
            cxl_press: 0.0,
            chase_wait_ns: -1,
        },
    ];
    run.fill_reason = vec![
        crate::lob::strategy::ExitReason::Take,
        crate::lob::strategy::ExitReason::Stop,
    ];
    run.fill_signal = vec![0, 1];
    // Граница — 100; первый круг закрылся до неё (день D), второй — на ней
    // же и после (день D+1, включая ровно границу — `>=`).
    run.fill_exit_ns = vec![50, 100];
    let row = forms_row(
        "SOLUSDT",
        "2026-09-08",
        "pct2-1to1-60",
        &[],
        &run,
        0,
        1.0,
        Some(100),
        true,
        true,
    );
    let at = |name: &str| -> &str {
        let i = FORMS_HEADER
            .iter()
            .position(|h| *h == name)
            .unwrap_or_else(|| panic!("нет колонки {name}"));
        &row[i]
    };
    assert_eq!(at("n_fills"), "2");
    assert_eq!(at("n_carried"), "1", "только круг с exit_ns ≥ границы");
    assert_eq!(at("carry_unverified"), "true");
}

/// F8b/F8c (К7): сумма `net_bps` пустой формы печатается `0.000000`, а не
/// `-0.000000` — `Iterator::sum` для `f64` стартует с `-0.0` (rustc 1.93),
/// и гейт «те же байты» ловил именно это. Здесь это закреплено юнит-тестом,
/// а не только машинным прогоном.
#[test]
fn the_net_sum_of_an_empty_form_prints_a_positive_zero() {
    let run = run_with_exit_counters(0, 0, 0, 0);
    let s = sum_net_bps(&run);
    assert_eq!(format!("{s:.6}"), "0.000000");
    assert!(
        s.is_sign_positive(),
        "пустая сумма — +0.0, иначе печать даёт знак минус"
    );
}

/// Ось «прилипания» (аудит дизайна 22.09, В-85): без флага — те же формы и имена, что у
/// полной сетки F7 (гейт «те же круги»); включённая добавляет хвост `-early<x>` и несёт
/// секунды в форму; числа — только из предрегистрированного набора В-58.
#[test]
fn early_exit_axis_keeps_default_forms_and_names_enabled_ones() {
    let stops = [StopForm::Pct(2.0)];
    let takes = [TakeForm::OneToOne];
    let axes = grid_forms_with_axes(
        &stops,
        &takes,
        None,
        &[3600],
        &[EntryTtl::Touch],
        &[EntryForm::SingleFrontrun],
        &[ExitForm::None, ExitForm::Eat { pct: 50.0 }],
    );
    let off = grid_forms_with_early(
        &stops,
        &takes,
        None,
        &[3600],
        &[EntryTtl::Touch],
        &[EntryForm::SingleFrontrun],
        &[ExitForm::None, ExitForm::Eat { pct: 50.0 }],
        &[None],
    );
    assert_eq!(off, axes, "без оси — формы байт в байт прежние");

    let earlies = parse_early_exits(&["off".to_string(), "2".to_string()]).unwrap();
    assert_eq!(earlies, vec![None, Some(2_000)]);
    let both = grid_forms_with_early(
        &stops,
        &takes,
        None,
        &[3600],
        &[EntryTtl::Touch],
        &[EntryForm::SingleFrontrun],
        &[ExitForm::None, ExitForm::Eat { pct: 50.0 }],
        &earlies,
    );
    let labels: Vec<&str> = both.iter().map(|f| f.label).collect();
    assert_eq!(
        labels,
        vec![
            "pct2-1to1-3600",
            "pct2-1to1-3600-eat50",
            "pct2-1to1-3600-early2",
            "pct2-1to1-3600-eat50-early2",
        ]
    );
    assert_eq!(both[2].early_exit_ms, Some(2_000));
    assert_eq!(both[0].early_exit_ms, None);

    assert!(parse_early_exits(&[]).unwrap() == vec![None]);
    assert!(
        parse_early_exits(&["4".to_string()]).is_err(),
        "4 с нет в наборе В-58 + точек F1 П-14"
    );
    assert!(parse_early_exits(&["x".to_string()]).is_err());
    assert!(parse_early_exits(&["0.6".to_string()]).is_err());
    let half = parse_early_exits(&["0.5".to_string()]).unwrap();
    assert_eq!(half, vec![Some(500)]);
    let hf = grid_forms_with_early(
        &stops,
        &takes,
        None,
        &[3600],
        &[EntryTtl::Touch],
        &[EntryForm::SingleFrontrun],
        &[ExitForm::None],
        &half,
    );
    assert_eq!(hf[0].label, "pct2-1to1-3600-early0.5");
}

// -----------------------------------------------------------------------
// Перенос круга через полночь (`--carry-root`): круг, ещё открытый на конце
// суток D, дочитывает выход по данным D+1 вместо `incomplete`.
// -----------------------------------------------------------------------

/// Г-102: варианты `retreat` с разным `k` или `T` — разные группы входа; одинаковые — одна группа (выходы разные).
#[test]
fn retreat_variants_with_different_memory_never_share_an_entry_group() {
    let r = |k_ticks, half_min| EntryForm::Retreat { k_ticks, half_min };
    let forms = grid_forms_with_axes(
        &[StopForm::Pct(2.0)],
        &[TakeForm::OneToOne],
        None,
        &[3600],
        &[EntryTtl::Touch],
        &[r(2, 5), r(4, 5), r(2, 15)],
        &[ExitForm::None, ExitForm::Eat { pct: 50.0 }],
    );
    assert_eq!(forms.len(), 6);
    let groups = super::super::drive::entry_groups(&forms);
    assert_eq!(groups.len(), 3, "три памяти — три группы: {groups:?}");
    for g in &groups {
        assert_eq!(g.len(), 2, "в группе — два выхода одной памяти");
        let e = forms[g[0]].entry_form;
        assert!(g.iter().all(|&i| forms[i].entry_form == e));
    }
}
