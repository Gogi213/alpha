use super::DriverArg;
use super::*;
use crate::commands::lob::backtest::{StopForm, TakeForm};
use crate::commands::lob::bounce_verdict::parse_form;
use crate::commands::lob::test_support::{delta_frame, snap_frame, trade_frame, write_day};
use crate::commands::lob::{H3Args, H3ModeArg};

/// Фикстура `touches/tests.rs` (три касания бида 99 за 6 секунд), сдвинутая
/// на `LEAD_S` секунд «тихой» книги впереди: ряд `σ` (В-62) должен покрывать
/// окно 60 с к первому касанию, иначе ни у одной формы нет сигналов.
const LEAD_S: i64 = 70;

fn touch_frames() -> Vec<Vec<crate::binlog::Record>> {
    let lead = LEAD_S * 1_000;
    let mut frames = vec![snap_frame(
        0,
        &[(98, 10), (99, 10), (100, 10)],
        &[(105, 10)],
    )];
    for s in 1..=LEAD_S {
        // Лёгкое дыхание аска: середина двигается, σ не ноль.
        let ask = if s % 2 == 0 { 105 } else { 106 };
        frames.push(delta_frame(
            s * 1_000,
            &[],
            &[(ask, 10), (105 + 106 - ask, 0)],
        ));
    }
    frames.extend([
        trade_frame(lead + 500, 100, 3),
        delta_frame(lead + 1000, &[(100, 0)], &[]),
        delta_frame(lead + 2000, &[(100, 10)], &[]),
        delta_frame(lead + 3000, &[(100, 0)], &[]),
        delta_frame(lead + 4000, &[(101, 10)], &[]),
        delta_frame(lead + 5000, &[(101, 0)], &[]),
        trade_frame(lead + 5500, 99, 4),
        delta_frame(lead + 6000, &[(99, 1)], &[]),
    ]);
    frames
}

fn fixture_root(dir: &std::path::Path, verified: bool) {
    std::fs::write(
        crate::commands::record::instruments_csv_path(dir),
        "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n\
         SOLUSDT,0.01,0.1,0.1,5,5\n",
    )
    .unwrap();
    write_day(dir, "SOLUSDT", "2026-09-08", &touch_frames());
    std::fs::write(
        dir.join("session.json"),
        "{\"started_utc\":\"2026-09-08T00:00:00Z\",\"start_hour_utc\":0,\"instruments\":[\"SOLUSDT\"]}",
    )
    .unwrap();
    if verified {
        std::fs::write(dir.join("verify-SOLUSDT.status"), "ok").unwrap();
    }
}

fn args(root: &std::path::Path, allow_unverified: bool) -> BounceGridArgs {
    BounceGridArgs {
        root: root.to_path_buf(),
        symbols: vec!["SOLUSDT".to_string()],
        median_rtt_ns: crate::lob::backtest::ExecLatency::uniform(20_000_000),
        p95_rtt_ns: crate::lob::backtest::ExecLatency::uniform(20_000_000),
        queue_model: "risk-adverse".to_string(),
        order_qty_e9: Some(100_000_000),
        order_qty_mult: 1,
        order_qty_from_pool: false,
        order_usd: None,
        post_only: false,
        stop_form: vec!["s1".to_string(), "s2".to_string()],
        take_form: vec!["t1".to_string()],
        take_floor_fees: Some(1.0),
        frontrun_only: false,
        min_age_secs: None,
        min_flow_pct: None,
        side: None,
        touches_from: None,
        sets: Vec::new(),
        regime_from: None,
        deadline_secs: Vec::new(),
        h3: H3Args {
            h3_mode: H3ModeArg::Percentile,
            h3_lots: Some(5),
            h3_usd: None,
            h3_strength_pct: None,
            h3_strength_window_bps: None,
        },
        h3_k: None,
        warmup_ms: Some(0),
        repeat_window_ms: Some(3_600_000),
        days: Vec::new(),
        threads: Some(3),
        driver: DriverArg::Setups,
        out_dir: root.join("grid"),
        allow_unverified,
    }
}

fn read_csv(path: &std::path::Path) -> (Vec<String>, Vec<Vec<String>>) {
    let text = std::fs::read_to_string(path).unwrap();
    read_csv_from(text.as_bytes())
}

/// То же, но из байтов «золотого» файла (`include_str!`).
fn read_csv_from(bytes: &[u8]) -> (Vec<String>, Vec<Vec<String>>) {
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_reader(bytes);
    let header: Vec<String> = r.headers().unwrap().iter().map(str::to_string).collect();
    let rows = r
        .records()
        .map(|rec| rec.unwrap().iter().map(str::to_string).collect())
        .collect();
    (header, rows)
}

fn col<'a>(header: &[String], row: &'a [String], name: &str) -> &'a str {
    let i = header
        .iter()
        .position(|h| h == name)
        .unwrap_or_else(|| panic!("колонки {name} нет: {header:?}"));
    &row[i]
}

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
    ] {
        assert!(rh.iter().any(|h| h == name), "в rounds.csv нет {name}");
    }
    let fills_from_forms: u64 = forms
        .iter()
        .map(|r| col(&fh, r, "n_fills").parse::<u64>().unwrap())
        .sum();
    assert_eq!(rounds.len() as u64, fills_from_forms);
    assert_eq!(summary.rounds, fills_from_forms);
    assert!(dir.path().join("grid").join("manifest.txt").exists());
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
        approach_bps: None,
        approach_min_age_secs: 0,
        moves: None,
        moves_window_ms: None,
        moves_bin_ms: None,
        numbers: None,
        allow_unverified: false,
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
            min_flow_pct: Some(10.0),
            side: None,
            eaten_max_pct: None,
            usd_min: None,
            ctx: [Range::default(); CTX_AXES.len()],
        }
    );
}

/// Ключ `eaten=<%>` (S1 плана по сторонам): `eaten=100` ничего не выбивает —
/// байты те же, что без ключа; `eaten=-1` выбивает все касания в
/// `n_skipped`; фикстура: у первого касания стена целая (`eaten` 0), у
/// следующих — частично съедена, `eaten=0` оставляет только целые.
#[test]
fn eaten_key_filters_by_wall_state_at_touch() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid-eaten");
    a.sets = vec![
        "all:".to_string(),
        "e100:eaten=100".to_string(),
        "e0:eaten=0".to_string(),
        "none:eaten=-1".to_string(),
    ];
    let multi = run_bounce_grid(&a).unwrap();
    let by_name = |n: &str| multi.sets.iter().find(|s| s.name == n).unwrap().clone();
    let body = |p: &std::path::Path| -> String {
        std::fs::read_to_string(p)
            .unwrap()
            .lines()
            .filter(|l| !l.starts_with('#'))
            .collect::<Vec<_>>()
            .join("\n")
    };
    assert_eq!(
        body(&by_name("e100").rounds_path),
        body(&by_name("all").rounds_path)
    );
    assert_eq!(
        body(&by_name("e100").forms_path),
        body(&by_name("all").forms_path)
    );
    let (fh, none) = read_csv(&by_name("none").forms_path);
    for r in &none {
        assert_eq!(col(&fh, r, "n_signals"), "0");
        assert_eq!(col(&fh, r, "n_skipped"), "3");
    }
    let (fh, e0) = read_csv(&by_name("e0").forms_path);
    let (_, all) = read_csv(&by_name("all").forms_path);
    for (r0, ra) in e0.iter().zip(&all) {
        let s0: u64 = col(&fh, r0, "n_signals").parse().unwrap();
        let sa: u64 = col(&fh, ra, "n_signals").parse().unwrap();
        assert!(s0 <= sa, "eaten=0 — подмножество: {r0:?}");
        let k0: u64 = col(&fh, r0, "n_skipped").parse().unwrap();
        assert_eq!(s0 + k0, 3);
    }
    let head = std::fs::read_to_string(&by_name("e0").forms_path).unwrap();
    assert!(head.contains(" eaten_max=Some(0.0) "), "{head}");
    assert_eq!(
        eaten_pct(&TouchRecord {
            size_at_touch: 3,
            size_max_before: 12,
            ..probe_touch()
        }),
        75.0
    );
    assert_eq!(
        eaten_pct(&TouchRecord {
            size_at_touch: 5,
            size_max_before: 0,
            ..probe_touch()
        }),
        0.0
    );
}

fn probe_touch() -> TouchRecord {
    TouchRecord {
        side: Side::Bid,
        price_tick: 1,
        touch_index: 0,
        start_ms: 0,
        end_ms: 0,
        duration_ms: 0,
        level_birth_ms: 0,
        size_at_touch: 0,
        size_max_before: 0,
        traded_during: 0,
        frontrun_lots: 0,
        frontrun_tick: None,
        swept_lots: 0,
        round_zeros: 0,
        ended_by_death: false,
        stack_levels: 0,
        stack_next_tick: None,
        traded_first_s: [0; 3],
        flow_1h_lots: 0,
        strength_e2: [-1; 3],
        strength_held_e2: [-1; 4],
        repeat_count: 0,
    }
}

/// Ключи контекста (S4): без них байты те же (набор `all`); граница по ходу
/// монеты на фикстуре (6 с записи — хода нет) выбивает всё; ключ режима без
/// `--regime-from` — отказ; с режимом на минуту касания — фильтр по значению
/// из файла суток (пул +50 bps за 4 ч: `pool4h_min=40` пропускает всё,
/// `pool4h_min=60` — ничего); парсер границ; `Range::holds`.
#[test]
fn context_keys_filter_by_pre_touch_return_and_regime() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let plain = run_bounce_grid(&args(dir.path(), false)).unwrap();

    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid-ctx");
    a.sets = vec!["all:".to_string(), "r:ret1h_min=-100000".to_string()];
    let multi = run_bounce_grid(&a).unwrap();
    let by_name =
        |m: &BounceGridSummary, n: &str| m.sets.iter().find(|s| s.name == n).unwrap().clone();
    let body = |p: &std::path::Path| -> String {
        std::fs::read_to_string(p)
            .unwrap()
            .lines()
            .filter(|l| !l.starts_with('#'))
            .collect::<Vec<_>>()
            .join("\n")
    };
    assert_eq!(
        body(&by_name(&multi, "all").rounds_path),
        body(&plain.rounds_path)
    );
    let (fh, r) = read_csv(&by_name(&multi, "r").forms_path);
    for row in &r {
        assert_eq!(
            col(&fh, row, "n_signals"),
            "0",
            "хода до касания на фикстуре нет"
        );
        assert_eq!(col(&fh, row, "n_skipped"), "3");
    }
    let head = std::fs::read_to_string(&by_name(&multi, "r").forms_path).unwrap();
    assert!(head.contains(" ctx=ret1h_min=-100000 "), "{head}");

    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid-noregime");
    a.sets = vec!["p:pool4h_min=0".to_string()];
    assert!(
        run_bounce_grid(&a).is_err(),
        "ключ режима без --regime-from — отказ"
    );

    // Режим суток: одна строка на минуту фикстуры (касания в первой минуте
    // суток 2026-09-08), пул +50 bps за 4 ч, биток пусто.
    let regime = dir.path().join("regime");
    std::fs::create_dir_all(&regime).unwrap();
    // Касания фикстуры — на 70–76 с записи: минуты 0, 60 000 и 120 000 мс
    // покрывают их с запасом.
    let rows: String = [0i64, 60_000, 120_000]
        .iter()
        .map(|m| format!("{m},10.0,50.0,5,,,,\n"))
        .collect();
    std::fs::write(
        regime.join("2026-09-08.csv"),
        format!(
            "minute_ms,pool_ret_1h_bps,pool_ret_4h_bps,n_coins,btc_ret_1h_bps,btc_ret_4h_bps,eth_ret_1h_bps,eth_ret_4h_bps\n{rows}"
        ),
    )
    .unwrap();
    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid-regime");
    a.regime_from = Some(regime);
    a.sets = vec![
        "p40:pool4h_min=40".to_string(),
        "p60:pool4h_min=60".to_string(),
        "b:btc1h_max=0".to_string(),
    ];
    let m = run_bounce_grid(&a).unwrap();
    assert_eq!(
        body(&by_name(&m, "p40").rounds_path),
        body(&plain.rounds_path),
        "режим держит — те же круги"
    );
    let (fh, p60) = read_csv(&by_name(&m, "p60").forms_path);
    assert!(
        p60.iter().all(|r| col(&fh, r, "n_signals") == "0"),
        "пул ниже границы — нет сигналов"
    );
    let (fh, b) = read_csv(&by_name(&m, "b").forms_path);
    assert!(
        b.iter().all(|r| col(&fh, r, "n_signals") == "0"),
        "битка на минуту нет — выбывает"
    );

    let set = FilterSet::parse("x:ret4h_max=-5.5,pool1h_min=1,btc4h_min=-1,btc4h_max=1").unwrap();
    assert_eq!(
        set.ctx[2],
        Range {
            min: None,
            max: Some(-5.5)
        }
    );
    assert_eq!(
        set.ctx[3],
        Range {
            min: Some(1.0),
            max: None
        }
    );
    assert_eq!(
        set.ctx[6],
        Range {
            min: Some(-1.0),
            max: Some(1.0)
        }
    );
    assert_eq!(
        set.ctx_label(),
        "ret4h_max=-5.5,pool1h_min=1,btc4h_min=-1,btc4h_max=1"
    );
    let r = Range {
        min: Some(0.0),
        max: Some(10.0),
    };
    assert!(r.holds(Some(0.0)) && r.holds(Some(10.0)) && !r.holds(Some(10.1)) && !r.holds(None));
    assert!(Range::default().holds(None));
}

/// Ключ `usd_min=<$>`: номинал стены при касании (цена × размер) ≥ порога;
/// фикстура — бид 99 × тик 0.01 × 10 лотов × шаг 0.1 ≈ $0.99: порог 0 не
/// выбивает (байты те же), порог 1 выбивает всё.
#[test]
fn usd_min_key_filters_by_wall_notional_at_touch() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid-usd");
    a.sets = vec![
        "all:".to_string(),
        "u0:usd_min=0".to_string(),
        "u1:usd_min=1".to_string(),
    ];
    let m = run_bounce_grid(&a).unwrap();
    let by = |n: &str| m.sets.iter().find(|s| s.name == n).unwrap().clone();
    let body = |p: &std::path::Path| -> String {
        std::fs::read_to_string(p)
            .unwrap()
            .lines()
            .filter(|l| !l.starts_with('#'))
            .collect::<Vec<_>>()
            .join("\n")
    };
    assert_eq!(body(&by("u0").rounds_path), body(&by("all").rounds_path));
    let (fh, u1) = read_csv(&by("u1").forms_path);
    assert!(u1
        .iter()
        .all(|r| col(&fh, r, "n_signals") == "0" && col(&fh, r, "n_skipped") == "3"));
    let head = std::fs::read_to_string(&by("u1").forms_path).unwrap();
    assert!(head.contains(" usd_min=Some(1.0) "), "{head}");
}

/// Гейт F3: `--queue-model risk-adverse` — прежний движок, и круг
/// (`rounds.csv`) и числа форм (`forms.csv`) обязаны совпасть с прогоном до
/// правки. «Золото» — снятый до правки вывод фикстуры
/// (`golden/rounds.csv`, `golden/forms.csv`): сравниваются все поля по
/// именам, поэтому добавленные правкой колонки (`n_fill_by_cross`) и поле
/// шапки (`queue=…`) гейт не обманывают, а любое расхождение прежнего поля —
/// валит.
#[test]
fn risk_adverse_queue_model_keeps_the_old_bytes() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
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
    a.out_dir = dir.path().join("grid-gate");
    let m = run_bounce_grid(&a).unwrap();
    assert!(m.rounds > 0, "фикстура обязана давать круги");

    // Поля «золота» по именам: прямой байтовый диф невозможен — шапка несёт
    // `queue=…`, а у `forms.csv` правка F3 добавила колонку.
    let same_fields = |got: &std::path::Path, golden: &str, skip: &[&str]| {
        let (gh, grows) = read_csv_from(golden.as_bytes());
        let (h, rows) = read_csv(got);
        assert_eq!(rows.len(), grows.len(), "{got:?}: число строк");
        for (r, g) in rows.iter().zip(&grows) {
            for (k, name) in gh.iter().enumerate() {
                if skip.contains(&name.as_str()) {
                    continue;
                }
                assert_eq!(
                    col(&h, r, name),
                    g[k],
                    "{got:?}: колонка {name} разошлась (строка {r:?})"
                );
            }
        }
        h
    };

    let head = std::fs::read_to_string(&m.forms_path).unwrap();
    assert!(
        head.contains(" queue=risk-adverse "),
        "модель очереди в шапке: {head}"
    );
    assert!(
        head.contains("queue=risk-adverse")
            && head.contains("paths=1:сделки-на-нашей-цене-частично"),
        "три пути исполнения крейта в шапке: {head}"
    );
    let fh = same_fields(
        &m.forms_path,
        include_str!("golden/forms.csv"),
        &["n_fill_by_cross"],
    );
    let (_, forms) = read_csv(&m.forms_path);
    assert_eq!(forms.len(), 8, "строка на форму");
    for r in &forms {
        assert_eq!(
            col(&fh, r, "n_fill_by_cross"),
            "0",
            "прежний движок исполняет целыми заявками: {r:?}"
        );
    }
    same_fields(&m.rounds_path, include_str!("golden/rounds.csv"), &[]);

    // Разбор флага: без `--queue-model` команда не запускается вовсе
    // (умолчания в коде нет), `prob:<n>` собирает свой движок.
    assert!(QueueModelKind::parse("").is_err());
    let mut b = args(dir.path(), false);
    b.queue_model = "prob:3".to_string();
    b.out_dir = dir.path().join("grid-prob");
    let p = run_bounce_grid(&b).unwrap();
    assert_eq!(p.forms, 8, "формы те же, движок другой");
    let head = std::fs::read_to_string(&p.forms_path).unwrap();
    assert!(head.contains(" queue=prob:3 "), "{head}");
    let (ph, prows) = read_csv(&p.forms_path);
    assert_eq!(prows.len(), 8);
    for r in &prows {
        // Колонка есть у обеих моделей; значение — счётчик пути (3).
        assert!(
            col(&ph, r, "n_fill_by_cross").parse::<u64>().is_ok(),
            "счётчик пути (3) обязан быть числом: {r:?}"
        );
    }
}

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
    a.deadline_secs = vec![900];
    a.out_dir = dir.path().join("grid-dl-bad");
    assert!(run_bounce_grid(&a).is_err());
}
