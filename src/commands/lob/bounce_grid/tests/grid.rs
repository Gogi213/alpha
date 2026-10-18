use super::*;

/// Сетка В-62 — `stop_sigma × take_sigma × DEADLINE_SECS` в этом порядке,
/// имена читаются вердиктом (`parse_form`) и не повторяются.
#[test]
fn forms_are_the_sigma_grid_in_grid_order() {
    let forms = grid_forms(
        &[StopForm::Sigma(1.0), StopForm::Sigma(2.0)],
        &[TakeForm::Sigma(1.0)],
        Some(1.0),
        &DEADLINE_SECS,
    );
    assert_eq!(forms.len(), 8);
    assert_eq!(forms[0].label, "s1-t1-60");
    assert_eq!(forms[3].label, "s1-t1-7200");
    assert_eq!(forms[7].label, "s2-t1-7200");
    let mut seen = std::collections::BTreeSet::new();
    for f in &forms {
        let (stop, take, deadline) = parse_form(f.label).unwrap();
        assert_eq!(deadline as i64, f.deadline_secs);
        assert_eq!(stop, f.form.stop.label());
        assert_eq!(take, f.form.take.label());
        assert!(seen.insert(f.label), "форма {} повторяется", f.label);
    }
    // Позиционные формы базы — те же имена, что читает вердикт.
    let base = grid_forms(
        &[StopForm::Before, StopForm::Pct(1.0)],
        &[TakeForm::OneToOne],
        None,
        &DEADLINE_SECS,
    );
    assert_eq!(base[0].label, "before-1to1-60");
    assert_eq!(base[7].label, "pct1-1to1-7200");
    // S8: свой набор дедлайнов — 30 мин и 4 ч читаются вердиктом, чужое число — нет.
    let ext = grid_forms(
        &[StopForm::Pct(2.0)],
        &[TakeForm::OneToOne],
        None,
        &[1800, 14_400],
    );
    assert_eq!(ext.len(), 2);
    assert_eq!(ext[1].label, "pct2-1to1-14400");
    assert_eq!(parse_form("pct2-1to1-1800").unwrap().2, 1800);
    assert!(parse_form("pct2-1to1-900").is_err());
    assert_eq!(
        crate::commands::lob::bounce_verdict::grid_size_from_labels([
            "pct2-1to1-1800",
            "pct2-1to1-14400",
            "pct1-1to1-1800",
            "pct1-1to1-14400"
        ])
        .unwrap(),
        4
    );
}

/// Одни сутки фикстуры → строка `forms.csv` на форму с одинаковым числом
/// касаний (`n_signals + n_no_sigma`: касания общие для всех форм — S1; у форм
/// с окном `σ` длиннее записи сигналов нет, у 60-секундных — все три),
/// `rounds.csv` с шапкой и колонками `symbol`/`day_utc`/`form` (K2), сводка
/// считает сутки и символ.
#[test]
fn grid_runs_the_fixture_day_and_writes_every_form() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let summary = run_bounce_grid(&args(dir.path(), false)).expect("прогон сетки на фикстуре");
    assert_eq!(summary.forms, 8);
    assert_eq!(summary.symbols_done, 1);
    assert_eq!(summary.symbols_skipped_unverified, 0);
    assert_eq!(summary.symbol_days, 1);

    let (fh, forms) = read_csv(&summary.forms_path);
    assert_eq!(forms.len(), 8, "строка на форму: {forms:?}");
    let labels: std::collections::BTreeSet<&str> =
        forms.iter().map(|r| col(&fh, r, "form")).collect();
    let expected: std::collections::BTreeSet<&str> = grid_forms(
        &[StopForm::Sigma(1.0), StopForm::Sigma(2.0)],
        &[TakeForm::Sigma(1.0)],
        Some(1.0),
        &DEADLINE_SECS,
    )
    .iter()
    .map(|f| f.label)
    .collect();
    assert_eq!(labels, expected);
    for r in &forms {
        let n_signals: u64 = col(&fh, r, "n_signals").parse().unwrap();
        let n_skipped: u64 = col(&fh, r, "n_skipped").parse().unwrap();
        assert_eq!(
            n_signals + n_skipped,
            3,
            "фикстура даёт три касания бида 99: {r:?}"
        );
        let form = col(&fh, r, "form");
        if form.ends_with("-60") {
            assert_eq!(n_signals, 3, "окно 60 с покрыто: {form}");
        } else {
            assert_eq!(n_signals, 0, "окно длиннее записи — сигналов нет: {form}");
        }
    }
    for r in &forms {
        assert_eq!(col(&fh, r, "symbol"), "SOLUSDT");
        assert_eq!(col(&fh, r, "day_utc"), "2026-09-08");
        // Фикстура — 6 с данных: дедлайны 60 с и дольше выходят за конец записи,
        // `incomplete` тут допустим и обязан лишь быть булевым.
        assert!(matches!(col(&fh, r, "incomplete"), "true" | "false"));
    }

    let (rh, rounds) = read_csv(&summary.rounds_path);
    for name in [
        "symbol",
        "day_utc",
        "form",
        "signal_index",
        "t0_ns",
        "net_bps",
        "reason",
        // F4 (В-78): фактическое исполнение круга в строке круга.
        "fill_frac",
        "entry_vwap",
        "legs_filled",
        "legs_rejected",
    ] {
        assert!(rh.iter().any(|h| h == name), "в rounds.csv нет {name}");
    }
    // Значения колонок F4 проверяются там, где у фикстуры есть круг
    // (`risk_adverse_queue_model_keeps_the_old_bytes`: σ-формы этой сетки
    // сигналов не дают — окно σ длиннее записи).
    let fills_from_forms: u64 = forms
        .iter()
        .map(|r| col(&fh, r, "n_fills").parse::<u64>().unwrap())
        .sum();
    assert_eq!(rounds.len() as u64, fills_from_forms);
    assert_eq!(summary.rounds, fills_from_forms);
    // Счётчик ног, не поставленных биржей, в `forms.csv` есть у каждой формы и
    // на фикстуре нулевой (вход пост-онли ни разу не пересёк спред).
    for r in &forms {
        assert_eq!(col(&fh, r, "n_rejected_postonly"), "0", "{r:?}");
    }
    // Умолчание входа — пост-онли (В-72), и оно видно в шапке.
    let head = std::fs::read_to_string(&summary.forms_path).unwrap();
    assert!(
        head.contains(" entry_post_only=true "),
        "шапка обязана нести режим входа: {head}"
    );
    assert!(dir.path().join("grid").join("manifest.txt").exists());
}

/// Трёхзначный флаг входа (В-72): `--post-only` включает, `--no-post-only`
/// выключает (режим гейта «те же круги»), **умолчание — включено**; заданные
/// оба — выигрывает последний (`clap` `overrides_with`). Проверяется разбором
/// командной строки, а не полем структуры: решает именно `clap`.
#[test]
fn post_only_defaults_to_on_and_the_pair_resolves_last_wins() {
    #[derive(clap::Parser)]
    struct Cli {
        #[command(flatten)]
        grid: BounceGridArgs,
    }
    use clap::Parser as _;
    let parse = |extra: &[&str]| -> bool {
        let mut argv = vec![
            "t",
            "--root",
            ".",
            "--median-rtt-ns",
            "1000000",
            "--p95-rtt-ns",
            "1000000",
            "--queue-model",
            "risk-adverse",
            "--order-qty-e9",
            "100000000",
            "--stop-form",
            "pct1",
            "--take-form",
            "1to1",
            "--h3-mode",
            "floor",
            "--out-dir",
            "o",
        ];
        argv.extend_from_slice(extra);
        Cli::try_parse_from(argv).unwrap().grid.entry_post_only()
    };
    assert!(parse(&[]), "умолчание — пост-онли (В-72)");
    assert!(parse(&["--post-only"]), "--post-only включает");
    assert!(!parse(&["--no-post-only"]), "--no-post-only — режим гейта");
    assert!(
        !parse(&["--post-only", "--no-post-only"]),
        "заданные оба — выигрывает последний"
    );
    assert!(parse(&["--no-post-only", "--post-only"]));
}

/// K1: без маркера `verify-<SYMBOL>.status == ok` символ пропускается и
/// артефакт остаётся пустым; `--allow-unverified` снимает требование.
#[test]
fn unverified_symbol_is_skipped_unless_allowed() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), false);
    let summary = run_bounce_grid(&args(dir.path(), false)).unwrap();
    assert_eq!(summary.symbols_skipped_unverified, 1);
    assert_eq!(summary.symbols_done, 0);
    let (_, forms) = read_csv(&summary.forms_path);
    assert!(forms.is_empty(), "без маркера ни одной строки");

    let summary = run_bounce_grid(&args(dir.path(), true)).unwrap();
    assert_eq!(summary.symbols_skipped_unverified, 0);
    assert_eq!(summary.symbols_done, 1);
}

/// K1 по суткам (В-181): `--verdict-csv` заменяет маркер символа; сутки не «пускаем» и сутки без строки — отказ.
#[test]
fn verdict_csv_gates_days_instead_of_marker() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), false);
    let csv = dir.path().join("verdict.csv");
    let header = "sym,day,month,verdict,reason
";
    let run = |body: &str| {
        std::fs::write(&csv, format!("{header}{body}")).unwrap();
        let mut a = args(dir.path(), false);
        a.verdict_csv = Some(csv.clone());
        run_bounce_grid(&a).unwrap()
    };
    let s = run("SOLUSDT,2026-09-08,2026-09,пускаем,
");
    assert_eq!(
        (s.symbols_done, s.days_refused_verdict),
        (1, 0),
        "пускаем — счёт без маркера"
    );
    let s = run("SOLUSDT,2026-09-08,2026-09,не пускаем,журнал потерь записи
");
    assert_eq!(
        (
            s.symbols_done,
            s.symbols_skipped_unverified,
            s.days_refused_verdict
        ),
        (0, 1, 1)
    );
    let s = run("ETHUSDT,2026-09-08,2026-09,пускаем,
");
    assert_eq!(
        (s.symbols_done, s.days_refused_verdict),
        (0, 1),
        "нет строки вердикта — отказ"
    );
}

/// В-172: части записи с разной сеткой (тик, лот) — отказ, не тихий счёт на сетке первой части;
/// та же сетка во второй части — не отказ.
#[test]
fn mixed_grid_parts_are_refused() {
    const DAY_NS: i64 = 86_400_000_000_000;
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    // Метки вторых суток — после первых (реплей отдаёт среднюю цену по времени; `from_mids` в debug это проверяет).
    let mut next_day = touch_frames();
    for r in next_day.iter_mut().flatten() {
        r.exch_ts_ns += DAY_NS;
        r.local_ts_ns += DAY_NS;
    }
    write_day(dir.path(), "SOLUSDT", "2026-09-09", &next_day);
    run_bounce_grid(&args(dir.path(), false)).expect("одна сетка — счёт идёт");

    let path = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-09", 1);
    let (tick_e9, step_e9) = read_tick_step(&path).unwrap();
    let header = crate::binlog::Header {
        tick_e9: tick_e9 * 10,
        step_e9,
        max_records_per_frame: 4096,
    };
    let mut w = crate::binlog::Writer::create(Vec::new(), header, 1).unwrap();
    for f in &next_day {
        w.write_frame(f).unwrap();
    }
    w.flush().unwrap();
    std::fs::write(&path, w.into_inner()).unwrap();
    let err = run_bounce_grid(&args(dir.path(), false)).unwrap_err();
    assert!(format!("{err:#}").contains("смешанные сетки"), "{err:#}");
}

/// Лот задаётся ровно одним способом — как у `lob backtest`.
#[test]
fn lot_must_come_from_exactly_one_source() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.order_qty_e9 = None;
    assert!(run_bounce_grid(&a).is_err());
    a.order_qty_from_pool = true;
    a.order_qty_e9 = Some(1);
    assert!(run_bounce_grid(&a).is_err());
    a.order_qty_e9 = None;
    a.order_usd = Some(100.0);
    assert!(run_bounce_grid(&a).is_err(), "два источника лота — отказ");
    // Лот под номинал: фикстура — цена ~0.99 $, шаг 0.1 → $100 = 1010 шагов = 101.0.
    a.order_qty_from_pool = false;
    a.out_dir = dir.path().join("grid-usd-lot");
    let m = run_bounce_grid(&a).unwrap();
    let head = std::fs::read_to_string(&m.forms_path).unwrap();
    assert!(head.contains("lot=usd:100x1"), "{head}");
    let (rh, rounds) = read_csv(&m.rounds_path);
    if let Some(r) = rounds.first() {
        let qty: f64 = col(&rh, r, "qty").parse().unwrap();
        assert!((qty - 101.0).abs() < 1e-9, "qty {qty}");
    }
}

/// Ось стороны (этап 1): фикстура — три касания бида 99, так что `--side ask`
/// выбивает все три в `n_skipped` у каждой формы, `--side bid` ничего не
/// меняет против прогона без флага; сторона — в шапке `forms.csv`.
#[test]
fn side_axis_keeps_only_touches_of_that_side() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);

    let mut a = args(dir.path(), false);
    a.side = Some(SideArg::Ask);
    a.out_dir = dir.path().join("grid-ask");
    let summary = run_bounce_grid(&a).unwrap();
    let (fh, forms) = read_csv(&summary.forms_path);
    assert_eq!(forms.len(), 8);
    for r in &forms {
        assert_eq!(
            col(&fh, r, "n_signals"),
            "0",
            "аск-стен в фикстуре нет: {r:?}"
        );
        assert_eq!(
            col(&fh, r, "n_skipped"),
            "3",
            "все три касания бида выбыли: {r:?}"
        );
    }
    let head = std::fs::read_to_string(&summary.forms_path).unwrap();
    assert!(head.contains(" side=ask "), "сторона в шапке: {head}");

    let mut a = args(dir.path(), false);
    a.side = Some(SideArg::Bid);
    a.out_dir = dir.path().join("grid-bid");
    let summary = run_bounce_grid(&a).unwrap();
    let (fh, forms) = read_csv(&summary.forms_path);
    for r in &forms {
        let form = col(&fh, r, "form");
        let want = if form.ends_with("-60") { "3" } else { "0" };
        assert_eq!(col(&fh, r, "n_signals"), want, "бид пропускает всё: {form}");
    }
    let head = std::fs::read_to_string(&summary.forms_path).unwrap();
    assert!(head.contains(" side=bid "), "сторона в шапке: {head}");
    let both = std::fs::read_to_string(
        run_bounce_grid(&args(dir.path(), false))
            .unwrap()
            .forms_path,
    )
    .unwrap();
    assert!(
        both.contains(" side=both "),
        "без флага — обе стороны: {both}"
    );
}

/// Кэш касаний (`--touches-from`): круги и строки форм побайтово те же, что
/// из реплея книги — на фикстуре с базовыми формами (без σ) и порогом `floor`
/// (без прогрева: кэш идёт с умолчаниями трекера, как ночной `lob touches`).
/// Сам кэш — то, что пишет `lob touches`, в раскладке ночного H3
/// (`<dir>/<сутки>/`). Символ без суток в кэше идёт реплеем и считается
/// отдельно; σ-формы с кэшем — отказ.
#[test]
fn touches_cache_gives_byte_identical_rounds() {
    use crate::commands::lob::touches::{run_touches, TouchesArgs};
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let h3 = || H3Args {
        h3_mode: H3ModeArg::Floor,
        h3_lots: None,
        h3_usd: None,
        h3_strength_pct: None,
        h3_strength_window_bps: None,
    };
    let base = |out: &str| {
        let mut a = args(dir.path(), false);
        a.stop_form = vec!["pct1".to_string(), "behind".to_string()];
        a.take_form = vec!["1to1".to_string()];
        a.take_floor_fees = None;
        a.h3 = h3();
        a.warmup_ms = None;
        a.repeat_window_ms = None;
        a.out_dir = dir.path().join(out);
        a
    };
    let replayed = run_bounce_grid(&base("grid-replay")).unwrap();
    assert!(replayed.rounds > 0, "фикстура обязана давать круги");

    let cache = dir.path().join("touches");
    run_touches(&TouchesArgs {
        root: dir.path().to_path_buf(),
        symbol: "SOLUSDT".to_string(),
        h3: h3(),
        h3_k: None,
        warmup_ms: crate::commands::lob::DEFAULT_WARMUP_MS,
        repeat_window_ms: crate::commands::lob::DEFAULT_REPEAT_WINDOW_MS,
        out: Some(cache.join("2026-09-08").join("touches-SOLUSDT.csv")),
        approach_bps: Vec::new(),
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
        twin_seeds: Vec::new(),
    })
    .unwrap();
    let mut a = base("grid-cache");
    a.touches_from = Some(cache.clone());
    let cached = run_bounce_grid(&a).unwrap();
    assert_eq!(cached.symbols_from_cache, 1);
    assert_eq!(cached.rounds, replayed.rounds);
    let body = |p: &std::path::Path| -> String {
        std::fs::read_to_string(p)
            .unwrap()
            .lines()
            .filter(|l| !l.starts_with('#'))
            .collect::<Vec<_>>()
            .join("\n")
    };
    assert_eq!(body(&cached.rounds_path), body(&replayed.rounds_path));
    assert_eq!(body(&cached.forms_path), body(&replayed.forms_path));
    let head = std::fs::read_to_string(&cached.forms_path).unwrap();
    assert!(
        head.contains(" touches=csv("),
        "источник касаний в шапке: {head}"
    );

    // Суток в кэше нет — реплей, не отказ.
    let mut a = base("grid-nocache");
    a.touches_from = Some(dir.path().join("grid-replay"));
    let s = run_bounce_grid(&a).unwrap();
    assert_eq!(s.symbols_from_cache, 0);
    assert_eq!(s.rounds, replayed.rounds);

    // `--touches-cache-only` (22.09): суток в кэше нет — символ пропущен, реплея нет (иначе монета
    // с неполным кэшем уходила в реплей всех суток корня и роняла ночь деки по памяти).
    let mut a = base("grid-cache-only-miss");
    a.touches_from = Some(dir.path().join("grid-replay"));
    a.touches_cache_only = true;
    let s = run_bounce_grid(&a).unwrap();
    assert_eq!(s.symbols_from_cache, 0);
    assert_eq!(s.symbols_without_touches, 1, "символ без кэша пропущен");
    assert_eq!(s.rounds, 0, "реплея нет — кругов нет");
    // Кэш есть — те же круги, что без флага.
    let mut a = base("grid-cache-only-hit");
    a.touches_from = Some(cache.clone());
    a.touches_cache_only = true;
    let s = run_bounce_grid(&a).unwrap();
    assert_eq!(s.symbols_from_cache, 1);
    assert_eq!(body(&s.rounds_path), body(&replayed.rounds_path));

    // σ-формы с кэшем — отказ; явный прогрев с кэшем — отказ.
    let mut a = args(dir.path(), false);
    a.h3 = h3();
    a.warmup_ms = None;
    a.repeat_window_ms = None;
    a.touches_from = Some(cache.clone());
    assert!(run_bounce_grid(&a).is_err(), "σ-формы из кэша не считаются");
    let mut a = base("grid-warmup");
    a.warmup_ms = Some(0);
    a.touches_from = Some(cache);
    assert!(run_bounce_grid(&a).is_err(), "прогрев с кэшем — отказ");
}

/// В-131: σ-лестница на подходе — σ на взводе из `--sigma-from` (`sigma-<SYMBOL>.csv`, окно по последней
/// закрытой минуте до `arm_ms`): ноги σ·(a…b) bps, нераздельные; строки нет — сигнала нет (`n_no_sigma`,
/// `n_skipped`). Без `--sigma-from` σ-форма — отказ, `--sigma-from` без σ-формы — отказ.
#[test]
fn sigma_ladder_reads_entry_sigma_from_the_side_table() {
    use crate::commands::lob::touches::{run_touches, TouchesArgs};
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
        twin_seeds: Vec::new(),
    })
    .unwrap();
    assert_eq!(summary.approaches, 1);
    let arm_ms = LEAD_S * 1_000 + 4_000;
    let end_ms = arm_ms.div_euclid(60_000) * 60_000;
    let sigma_dir = dir.path().join("sigma");
    std::fs::create_dir_all(&sigma_dir).unwrap();
    let grid = |entry: &str, sigma: Option<&std::path::Path>, out: &str| {
        let mut a = args(dir.path(), false);
        a.signal = SignalArg::Approach;
        a.entry_form = vec![entry.to_string()];
        a.stop_form = vec!["at".to_string()];
        a.take_form = vec!["1to1".to_string()];
        a.take_floor_fees = None;
        a.deadline_secs = vec![60];
        a.h3 = h3();
        a.warmup_ms = None;
        a.repeat_window_ms = None;
        a.touches_from = Some(cache.clone());
        a.sigma_from = sigma.map(std::path::Path::to_path_buf);
        a.out_dir = dir.path().join(out);
        run_bounce_grid(&a)
    };
    // Стена 99.00 (9 900 тиков по 0.01): 1 тик ≈ 1,01 bps. σ = 100 bps, `ladder3x0.02..0.1s` — полоса
    // 2…10 bps, те же ноги 99.02/99.06/99.10, что у `ladder3x2..10` соседнего теста.
    std::fs::write(
        sigma_dir.join("sigma-SOLUSDT.csv"),
        format!(
            "window_end_ms,sigma_bps
{},55
{end_ms},100
",
            end_ms - 60_000
        ),
    )
    .unwrap();
    let m = grid("ladder3x0.02..0.1s", Some(&sigma_dir), "grid-sigma").unwrap();
    assert_eq!((m.n_no_sigma, m.n_sigma_signals), (0, 1));
    assert!(m.rounds > 0, "σ есть — круг на свипе в стену");
    let (rh, rounds) = read_csv(&m.rounds_path);
    for r in &rounds {
        let vwap: f64 = col(&rh, r, "entry_vwap").parse().unwrap();
        assert!((vwap - 99.0596).abs() < 1e-6, "entry_vwap {vwap}");
        assert_eq!(col(&rh, r, "legs_filled"), "3", "{r:?}");
    }
    let head = std::fs::read_to_string(&m.forms_path).unwrap();
    assert!(
        head.contains(&format!(
            " entry_forms=ladder3x0.02..0.1s sigma_from={} ",
            sigma_dir.display()
        )),
        "{head}"
    );
    // Строки на минуту взвода нет — сигнала нет, он в `n_no_sigma` и `n_skipped` формы.
    std::fs::write(
        sigma_dir.join("sigma-SOLUSDT.csv"),
        format!(
            "window_end_ms,sigma_bps
{},55
",
            end_ms - 60_000
        ),
    )
    .unwrap();
    let m = grid("ladder3x0.02..0.1s", Some(&sigma_dir), "grid-no-sigma").unwrap();
    assert_eq!((m.n_no_sigma, m.n_sigma_signals, m.rounds), (1, 1, 0));
    let (fh, forms) = read_csv(&m.forms_path);
    assert_eq!(col(&fh, &forms[0], "n_signals"), "0");
    assert_eq!(col(&fh, &forms[0], "n_skipped"), "1");
    // Отказы флага.
    assert!(grid("ladder3x0.02..0.1s", None, "grid-x1").is_err());
    assert!(grid("ladder3x2..10", Some(&sigma_dir), "grid-x2").is_err());
    let missing = dir.path().join("nosigma");
    std::fs::create_dir_all(&missing).unwrap();
    assert!(
        grid("ladder3x0.02..0.1s", Some(&missing), "grid-x3").is_err(),
        "нет таблицы монеты"
    );
}

/// F6 (В-73): `--signal approach` берёт сигнал из записи подхода F1
/// (`approaches-<SYMBOL>.csv`): `t0` круга — **взвод** (`arm_ms`, за 1,5 с до
/// касания), а не касание, вход ставится заранее, а срок жизни входа — по F5
/// (`touch` — до снятия подхода). Лестница формы ставит ноги на целых тиках
/// (99.02/99.06/99.10), свип в стену исполняет их, касание закрывает круг
/// стопом «в плотность». `--signal touch` на тех же данных — другой `t0`
/// (момент касания).
#[test]
fn approach_signal_arms_on_the_f1_record_and_fills_the_ladder() {
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
    // Кэш F1: касания и записи подхода — тем же прогоном `lob touches`.
    let cache = dir.path().join("approaches");
    let touches_out = cache.join("2026-09-08").join("touches-SOLUSDT.csv");
    let summary = run_touches(&TouchesArgs {
        root: dir.path().to_path_buf(),
        symbol: "SOLUSDT".to_string(),
        h3: h3(),
        h3_k: None,
        warmup_ms: crate::commands::lob::DEFAULT_WARMUP_MS,
        repeat_window_ms: crate::commands::lob::DEFAULT_REPEAT_WINDOW_MS,
        out: Some(touches_out.clone()),
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
        twin_seeds: Vec::new(),
    })
    .unwrap();
    assert_eq!(summary.approaches, 1, "фикстура взводит ровно один подход");
    let approaches = read_approaches_csv(&summary.approaches_out[0]).unwrap();
    assert_eq!(approaches.len(), 1);
    let arm_ms = LEAD_S * 1_000 + 4_000;
    let a = &approaches[0].approach;
    assert_eq!(
        a.arm_ms, arm_ms,
        "взвод — кадр стороны стены с чужой ценой в полосе"
    );
    assert_eq!(a.disarm_ms, LEAD_S * 1_000 + 5_500, "снят касанием");
    assert_eq!(a.disarm_reason, crate::lob::levels::ApproachEnd::Touch);
    assert_eq!(a.price_tick, 9_900, "стена — цена уровня");

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
    a.out_dir = dir.path().join("grid-approach");
    let m = run_bounce_grid(&a).unwrap();
    assert_eq!(m.symbols_from_cache, 1);
    assert!(m.rounds > 0, "подход обязан дать круг на свипе в стену");

    let (fh, forms) = read_csv(&m.forms_path);
    assert_eq!(forms.len(), 1);
    assert_eq!(col(&fh, &forms[0], "form"), "ladder3x2..10-at-1to1-60");
    let (rh, rounds) = read_csv(&m.rounds_path);
    assert_eq!(rounds.len() as u64, m.rounds);
    for r in &rounds {
        assert_eq!(
            col(&rh, r, "t0_ns"),
            (arm_ms * 1_000_000).to_string(),
            "t0 — взвод подхода, а не касание: {r:?}"
        );
        // Ноги — целые тики 99.02/99.06/99.10 и целые шаги лота (R3): 0.1 при
        // шаге записи 0.001 — 100 шагов, по трети — 33, остаток шаг — первой
        // (на равных долях — первая по счёту): 0.034/0.033/0.033, средняя
        // (0.034×99.02 + 0.033×99.06 + 0.033×99.10) / 0.1 = 99.0596, а не
        // 99.06 ровных долей.
        let vwap: f64 = col(&rh, r, "entry_vwap").parse().unwrap();
        assert!((vwap - 99.0596).abs() < 1e-6, "entry_vwap {vwap}");
        assert_eq!(col(&rh, r, "legs_filled"), "3", "три ноги лестницы: {r:?}");
        assert_eq!(col(&rh, r, "legs_rejected"), "0", "{r:?}");
        assert_eq!(col(&rh, r, "fill_frac"), "1.000000", "{r:?}");
    }
    let head = std::fs::read_to_string(&m.forms_path).unwrap();
    assert!(head.contains(" signal=approach "), "{head}");
    assert!(head.contains(" entry_forms=ladder3x2..10 "), "{head}");

    // Сигнал касания на тех же данных — другое `t0` (момент касания, +2 с):
    // доказывает, что круг выше взят со взвода, а не с касания.
    let mut t = args(dir.path(), false);
    t.stop_form = vec!["at".to_string()];
    t.take_form = vec!["1to1".to_string()];
    t.take_floor_fees = None;
    t.deadline_secs = vec![60];
    t.h3 = h3();
    t.warmup_ms = None;
    t.repeat_window_ms = None;
    t.touches_from = Some(cache.clone());
    t.out_dir = dir.path().join("grid-touch");
    let touch_run = run_bounce_grid(&t).unwrap();
    assert!(touch_run.rounds > 0, "касание тоже даёт круг");
    let (trh, touch_rounds) = read_csv(&touch_run.rounds_path);
    assert_eq!(
        col(&trh, &touch_rounds[0], "t0_ns"),
        ((LEAD_S * 1_000 + 5_500) * 1_000_000).to_string(),
        "сигнал касания — его собственный момент"
    );

    // Условия F6: без кэша подходов сигнал не построить, а ключ `eaten=`
    // (история размера) у подхода не определён — отказ, не пустой прогон.
    let non_sigma = |out: &str| {
        let mut a = args(dir.path(), false);
        a.stop_form = vec!["at".to_string()];
        a.take_form = vec!["1to1".to_string()];
        a.take_floor_fees = None;
        a.deadline_secs = vec![60];
        a.h3 = h3();
        a.warmup_ms = None;
        a.repeat_window_ms = None;
        a.out_dir = dir.path().join(out);
        a
    };
    let mut no_cache = non_sigma("grid-no-cache");
    no_cache.signal = SignalArg::Approach;
    let err = run_bounce_grid(&no_cache).unwrap_err().to_string();
    assert!(err.contains("кэш подходов"), "{err}");
    let mut eaten = non_sigma("grid-eaten");
    eaten.signal = SignalArg::Approach;
    eaten.touches_from = Some(cache.clone());
    eaten.sets = vec!["e:eaten=50".to_string()];
    let err = run_bounce_grid(&eaten).unwrap_err().to_string();
    assert!(err.contains("eaten"), "{err}");

    // F10b: ход **монеты** до взвода (`ret*`) кэш подходов не несёт — отказ;
    // режим (`pool*`/`btc*`) читается по минуте взвода из `--regime-from`, и
    // H1 «после просадки BTC» (`btc4h_max=0`) на подходах считается: набор с
    // BTC «в минусе» даёт те же круги, что прогон без фильтра, «в плюсе» — ноль.
    let mut ret = non_sigma("grid-ret");
    ret.signal = SignalArg::Approach;
    ret.touches_from = Some(cache.clone());
    ret.sets = vec!["r:ret1h_min=0".to_string()];
    let err = run_bounce_grid(&ret).unwrap_err().to_string();
    assert!(err.contains("ret*"), "{err}");

    let regime = dir.path().join("regime");
    std::fs::create_dir_all(&regime).unwrap();
    let arm_minute = arm_ms - arm_ms.rem_euclid(60_000);
    let rows: String = [arm_minute - 60_000, arm_minute, arm_minute + 60_000]
        .iter()
        .map(|m| {
            format!(
                "{m},10.0,50.0,5,-20.0,-50.0,,
"
            )
        })
        .collect();
    std::fs::write(
        regime.join("2026-09-08.csv"),
        format!(
            "minute_ms,pool_ret_1h_bps,pool_ret_4h_bps,n_coins,btc_ret_1h_bps,btc_ret_4h_bps,eth_ret_1h_bps,eth_ret_4h_bps
{rows}"
        ),
    )
    .unwrap();
    let mut b = non_sigma("grid-btc");
    b.signal = SignalArg::Approach;
    b.entry_form = vec!["ladder3x2..10".to_string()];
    b.touches_from = Some(cache);
    b.regime_from = Some(regime);
    b.sets = vec!["neg:btc4h_max=0".to_string(), "pos:btc4h_min=0".to_string()];
    let mb = run_bounce_grid(&b).unwrap();
    let by_name = |n: &str| mb.sets.iter().find(|s| s.name == n).unwrap().clone();
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
    assert_eq!(
        body(&by_name("neg").rounds_path),
        body(&m.rounds_path),
        "BTC −50 bps/4 ч на минуте взвода проходит `btc4h_max=0` — круги те же"
    );
    let (_, pos_rounds) = read_csv(&by_name("pos").rounds_path);
    assert!(
        pos_rounds.is_empty(),
        "`btc4h_min=0` при BTC в минусе — ни одного круга: {pos_rounds:?}"
    );
}

/// Кэш числа событий части (`<бинлог>.events`): первый прогон пишет сайдкар,
/// второй читает его и даёт те же круги; испорченный сайдкар (чужое число)
/// не меняет результата и переписывается честным счётом.
#[test]
fn event_count_sidecar_is_written_read_and_self_healing() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let first = run_bounce_grid(&args(dir.path(), false)).unwrap();
    let sidecars: Vec<PathBuf> = std::fs::read_dir(dir.path())
        .unwrap()
        .map(|e| e.unwrap().path())
        .filter(|p| p.to_string_lossy().ends_with(".events"))
        .collect();
    assert_eq!(sidecars.len(), 1, "сайдкар на часть: {sidecars:?}");
    let text = std::fs::read_to_string(&sidecars[0]).unwrap();
    let n: usize = text.split_whitespace().nth(2).unwrap().parse().unwrap();
    assert!(n > 0);

    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid2");
    let second = run_bounce_grid(&a).unwrap();
    assert_eq!(second.rounds, first.rounds);

    // Ложное число с верным штампом: буфер растёт, круги те же, сайдкар починен.
    let stamp: Vec<&str> = text.split_whitespace().collect();
    std::fs::write(&sidecars[0], format!("{} {} 1\n", stamp[0], stamp[1])).unwrap();
    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid3");
    let third = run_bounce_grid(&a).unwrap();
    assert_eq!(third.rounds, first.rounds);
    let healed = std::fs::read_to_string(&sidecars[0]).unwrap();
    assert_eq!(healed, text, "сайдкар переписан честным счётом");
    // Файл части не найден резолвером как бинлог: сессия по-прежнему одна часть.
    assert_eq!(third.symbol_days, 1);
}

/// Наборы фильтров одним процессом (`--set`): артефакты набора байт в байт те
/// же, что у отдельной сетки с теми же флагами (окна и события суток общие,
/// фильтры только выбирают сигналы); без `--set` — прежние пути и байты.
/// Набор `bid` на фикстуре (три касания бида) равен сетке без фильтров, набор
/// `ask` пуст; флаги фильтров вместе с `--set` — отказ, кривой набор — отказ.
#[test]
fn filter_sets_match_separate_grids_byte_for_byte() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let plain = run_bounce_grid(&args(dir.path(), false)).unwrap();
    assert_eq!(plain.sets.len(), 1);
    assert_eq!(plain.sets[0].name, "");
    assert_eq!(plain.sets[0].forms_path, plain.forms_path);

    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid-sets");
    a.sets = vec![
        "all:".to_string(),
        "bid:side=bid".to_string(),
        "ask:side=ask,age=0".to_string(),
    ];
    let multi = run_bounce_grid(&a).unwrap();
    assert_eq!(multi.sets.len(), 3);
    assert_eq!(
        multi.rounds,
        plain.rounds * 2,
        "all и bid дают одни круги, ask — ноль"
    );
    let body = |p: &std::path::Path| -> String {
        std::fs::read_to_string(p)
            .unwrap()
            .lines()
            .filter(|l| !l.starts_with('#'))
            .collect::<Vec<_>>()
            .join("\n")
    };
    let by_name = |n: &str| multi.sets.iter().find(|s| s.name == n).unwrap().clone();
    assert_eq!(
        by_name("all").rounds_path,
        dir.path().join("grid-sets").join("all").join("rounds.csv")
    );
    assert_eq!(body(&by_name("all").rounds_path), body(&plain.rounds_path));
    assert_eq!(body(&by_name("all").forms_path), body(&plain.forms_path));
    assert_eq!(body(&by_name("bid").rounds_path), body(&plain.rounds_path));
    assert_eq!(body(&by_name("bid").forms_path), body(&plain.forms_path));
    let (fh, ask_forms) = read_csv(&by_name("ask").forms_path);
    assert_eq!(ask_forms.len(), 8);
    for r in &ask_forms {
        assert_eq!(col(&fh, r, "n_signals"), "0");
        assert_eq!(col(&fh, r, "n_skipped"), "3");
    }
    let head = std::fs::read_to_string(&by_name("ask").forms_path).unwrap();
    assert!(
        head.contains(" side=ask ") && head.contains(" set=ask"),
        "{head}"
    );
    assert!(dir.path().join("grid-sets").join("manifest.txt").exists());
    assert!(dir
        .path()
        .join("grid-sets")
        .join("bid")
        .join("manifest.txt")
        .exists());
    // С-06: снимок `ALPHA_*` — в манифесте и набора, и общего
    for m in [
        dir.path().join("grid-sets").join("manifest.txt"),
        dir.path()
            .join("grid-sets")
            .join("bid")
            .join("manifest.txt"),
    ] {
        assert!(
            std::fs::read_to_string(&m).unwrap().contains(
                "
env_count="
            ),
            "{m:?}"
        );
    }

    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid-bad");
    a.sets = vec!["x:side=bid".to_string()];
    a.side = Some(SideArg::Ask);
    assert!(
        run_bounce_grid(&a).is_err(),
        "флаг фильтра вместе с --set — отказ"
    );
    for bad in [
        "nocolon",
        "a b:",
        "x:side=up",
        "x:age=1,age=2",
        "x:zzz=1",
        "x:eaten=abc",
        "x:eaten=nan",
        "x:usd_min=-1",
        "x:usd_min=abc",
        "x:ret1h_min=abc",
        "x:ret9h_min=1",
        "x:pool4h_mid=1",
        "x:ret1h_min=1,ret1h_min=2",
        "..:",
    ] {
        assert!(FilterSet::parse(bad).is_err(), "{bad}");
    }
    let ok = FilterSet::parse("a15-s10:age=900,flow=10,frontrun").unwrap();
    assert_eq!(
        ok,
        FilterSet {
            name: "a15-s10".to_string(),
            frontrun_only: true,
            min_age_secs: Some(900),
            max_age_secs: None,
            min_flow_pct: Some(10.0),
            side: None,
            eaten_max_pct: None,
            eaten_min_pct: None,
            frontrun_min_lots: None,
            lot_first: None,
            size_risk: None,
            entry_gate: None,
            usd_min: None,
            behind_min_pct: None,
            stack_min: None,
            repeat_min: None,
            swept_min_bp: None,
            ladder_regime: None,
            stop_regime: None,
            r1: Vec::new(),
            ctx: [Range::default(); CTX_AXES.len()],
        }
    );
}

/// `--cells` (T-38): клетки «форма × набор» списком вместо произведения — строки каждой клетки байт в байт
/// те же, что в прогоне произведением, других форм в наборе нет; кривой файл клеток — отказ.
#[test]
fn cells_match_the_product_byte_for_byte() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let sets = vec!["all:".to_string(), "bid:side=bid".to_string()];
    let mut full = args(dir.path(), false);
    full.out_dir = dir.path().join("cells-full");
    full.sets = sets.clone();
    let product = run_bounce_grid(&full).unwrap();
    let (fh, rows) = read_csv(&product.sets[0].forms_path);
    let labels: Vec<String> = {
        let mut v: Vec<String> = rows
            .iter()
            .map(|r| col(&fh, r, "form").to_string())
            .collect();
        v.dedup();
        v
    };
    assert!(labels.len() >= 6, "{labels:?}");
    let pick = |set: usize, forms: &[&str]| -> (Vec<String>, Vec<String>) {
        let keep = |p: &std::path::Path| -> Vec<String> {
            let (h, rs) = read_csv(p);
            rs.iter()
                .filter(|r| forms.contains(&col(&h, r, "form")))
                .map(|r| r.join(","))
                .collect()
        };
        (
            keep(&product.sets[set].rounds_path),
            keep(&product.sets[set].forms_path),
        )
    };
    let cells = dir.path().join("cells.txt");
    std::fs::write(
        &cells,
        format!(
            "# клетки\n{} all\n\n{} all\n{} bid\n",
            labels[3], labels[0], labels[5]
        ),
    )
    .unwrap();
    let mut c = args(dir.path(), false);
    c.out_dir = dir.path().join("cells-list");
    c.sets = sets.clone();
    c.cells = Some(cells.clone());
    let listed = run_bounce_grid(&c).unwrap();
    assert_eq!(listed.forms, 3, "формы вне файла не считаются");
    for (si, forms) in [
        (0, vec![labels[0].as_str(), labels[3].as_str()]),
        (1, vec![labels[5].as_str()]),
    ] {
        let (want_rounds, want_forms) = pick(si, &forms);
        let (h, rs) = read_csv(&listed.sets[si].rounds_path);
        let got_rounds: Vec<String> = rs.iter().map(|r| r.join(",")).collect();
        let (hf, fs) = read_csv(&listed.sets[si].forms_path);
        let got_forms: Vec<String> = fs.iter().map(|r| r.join(",")).collect();
        assert_eq!(got_rounds, want_rounds, "rounds набора {si}");
        assert_eq!(got_forms, want_forms, "forms набора {si}");
        assert!(fs.iter().all(|r| forms.contains(&col(&hf, r, "form"))));
        let _ = h;
    }
    for bad in [
        "нет-такой-формы all\n".to_string(),
        format!("{} zzz\n", labels[0]),
        format!("{} all\n{} all\n", labels[0], labels[0]),
        format!("{} all\n", labels[0]),
        format!("{} all extra\n", labels[0]),
    ] {
        std::fs::write(&cells, &bad).unwrap();
        c.out_dir = dir.path().join("cells-bad");
        assert!(run_bounce_grid(&c).is_err(), "{bad:?}");
    }
}

/// G10: память кругов (`--round-memo on`, умолчание) против прежнего счёта с нуля (`off`) на тех
/// же наборах — тела `rounds.csv` и `forms.csv` каждого набора побайтово одни и те же.
#[test]
fn round_memo_keeps_every_set_byte_for_byte() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let sets = vec![
        "all:".to_string(),
        "bid:side=bid".to_string(),
        "ask:side=ask,age=0".to_string(),
    ];
    let mut on = args(dir.path(), false);
    on.out_dir = dir.path().join("memo-on");
    on.sets = sets.clone();
    let mut off = args(dir.path(), false);
    off.out_dir = dir.path().join("memo-off");
    off.sets = sets.clone();
    off.round_memo = "off".to_string();
    let a = run_bounce_grid(&on).unwrap();
    let b = run_bounce_grid(&off).unwrap();
    assert_eq!(a.rounds, b.rounds);
    let body = |p: &std::path::Path| -> String {
        std::fs::read_to_string(p)
            .unwrap()
            .lines()
            .filter(|l| !l.starts_with('#'))
            .collect::<Vec<_>>()
            .join("\n")
    };
    for (x, y) in a.sets.iter().zip(b.sets.iter()) {
        assert_eq!(x.name, y.name);
        assert_eq!(
            body(&x.rounds_path),
            body(&y.rounds_path),
            "rounds {}",
            x.name
        );
        assert_eq!(body(&x.forms_path), body(&y.forms_path), "forms {}", x.name);
    }
}

/// `--events wide` (CEO 26.09): сутки 64-байтными строками крейта против компактных строк Р6 —
/// файлы наборов побайтно те же (с шапками: вид событий в шапку не идёт), и с драйвером `full`.
#[test]
fn wide_events_keep_every_set_byte_for_byte() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let sets = vec!["all:".to_string(), "bid:side=bid".to_string()];
    for driver in [DriverArg::Setups, DriverArg::Full] {
        let run = |events: &str| {
            let mut a = args(dir.path(), false);
            // Те же настройки, что у теста кэша касаний: фикстура даёт круги.
            a.stop_form = vec!["pct1".to_string(), "behind".to_string()];
            a.take_form = vec!["1to1".to_string()];
            a.take_floor_fees = None;
            a.h3 = H3Args {
                h3_mode: H3ModeArg::Floor,
                h3_lots: None,
                h3_usd: None,
                h3_strength_pct: None,
                h3_strength_window_bps: None,
            };
            a.warmup_ms = None;
            a.repeat_window_ms = None;
            a.out_dir = dir.path().join(format!("{events}-{}", driver.label()));
            a.sets = sets.clone();
            a.driver = driver;
            a.windows_check = driver == DriverArg::Setups;
            a.events = events.to_string();
            run_bounce_grid(&a).unwrap()
        };
        let compact = run("compact");
        let wide = run("wide");
        assert!(compact.rounds > 0, "фикстура даёт круги");
        assert_eq!(compact.rounds, wide.rounds);
        for (x, y) in compact.sets.iter().zip(wide.sets.iter()) {
            let read = |p: &std::path::Path| std::fs::read_to_string(p).unwrap();
            assert_eq!(
                read(&x.rounds_path),
                read(&y.rounds_path),
                "rounds {}",
                x.name
            );
            assert_eq!(read(&x.forms_path), read(&y.forms_path), "forms {}", x.name);
        }
    }
}

/// T-31 (`--busy-skip off`): шапка помечена, рядом `signals.csv`; правило «занят при `t0 < idle_ns`
/// прошлого принятого шага, обрыв — конец суток» над `signals.csv` отбирает из `rounds.csv` режима `off`
/// ровно строки прогона `on`, в том же порядке, байт в байт (это и делает `tools/compute/busy-replay.py`).
/// С драйвером `full` режим — отказ.
#[test]
fn busy_skip_off_trace_replays_the_default_rounds_byte_for_byte() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let sets = vec!["all:".to_string(), "bid:side=bid".to_string()];
    let run = |busy: &str, driver: DriverArg| {
        let mut a = args(dir.path(), false);
        a.stop_form = vec!["pct1".to_string(), "behind".to_string()];
        a.take_form = vec!["1to1".to_string()];
        a.take_floor_fees = None;
        a.h3 = H3Args {
            h3_mode: H3ModeArg::Floor,
            h3_lots: None,
            h3_usd: None,
            h3_strength_pct: None,
            h3_strength_window_bps: None,
        };
        a.warmup_ms = None;
        a.repeat_window_ms = None;
        a.out_dir = dir.path().join(format!("busy-{busy}-{}", driver.label()));
        a.sets = sets.clone();
        a.driver = driver;
        a.busy_skip = busy.to_string();
        run_bounce_grid(&a)
    };
    let err = run("off", DriverArg::Full).unwrap_err().to_string();
    assert!(err.contains("--busy-skip off"), "{err}");
    let on = run("on", DriverArg::Setups).unwrap();
    let off = run("off", DriverArg::Setups).unwrap();
    assert!(on.rounds > 0, "фикстура даёт круги");
    assert!(off.rounds >= on.rounds, "{} < {}", off.rounds, on.rounds);
    let lines = |p: &std::path::Path| -> Vec<String> {
        std::fs::read_to_string(p)
            .unwrap()
            .lines()
            .map(str::to_string)
            .collect()
    };
    for (x, y) in on.sets.iter().zip(off.sets.iter()) {
        assert!(!x.rounds_path.with_file_name("signals.csv").exists());
        let sig = lines(&y.rounds_path.with_file_name("signals.csv"));
        assert!(sig[0].contains("busy_skip=off"), "{}", sig[0]);
        assert!(lines(&y.rounds_path)[0].contains("busy_skip=off"));
        assert!(!lines(&x.rounds_path)[0].contains("busy_skip"));
        assert_eq!(
            sig[1],
            "symbol,day_utc,form,signal_index,t0_ns,price_tick,entry_px,step,idle_ns,residual,exit_ns"
        );
        // правило движка над следом — по (символ, сутки, форма), в порядке строк
        let mut kept: std::collections::HashSet<(String, String, String, String)> =
            std::collections::HashSet::new();
        let mut state: std::collections::HashMap<(String, String, String), (i64, bool)> =
            std::collections::HashMap::new();
        for row in &sig[2..] {
            let c: Vec<&str> = row.split(',').collect();
            let key = (c[0].to_string(), c[1].to_string(), c[2].to_string());
            let (idle, stopped) = state.entry(key.clone()).or_insert((i64::MIN, false));
            let t0: i64 = c[4].parse().unwrap();
            if *stopped || t0 < *idle {
                continue;
            }
            kept.insert((key.0, key.1, key.2, c[3].to_string()));
            if c[7] == "end_of_data" || c[7] == "no_window" || c[9] == "ended" {
                *stopped = true;
            } else {
                *idle = c[8].parse().unwrap();
            }
        }
        let off_rows = lines(&y.rounds_path);
        let replay: Vec<&String> = off_rows[2..]
            .iter()
            .filter(|r| {
                let c: Vec<&str> = r.split(',').collect();
                kept.contains(&(
                    c[0].to_string(),
                    c[1].to_string(),
                    c[2].to_string(),
                    c[3].to_string(),
                ))
            })
            .collect();
        let on_rows = lines(&x.rounds_path);
        assert_eq!(
            replay,
            on_rows[2..].iter().collect::<Vec<_>>(),
            "набор {}",
            x.name
        );
    }
}
