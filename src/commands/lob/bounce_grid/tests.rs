use super::args::{
    ensure_entry_conditions_args, parse_early_exits, parse_entry_ttls, parse_exit_forms, DriverArg,
    SideArg,
};
use super::cache::eaten_pct;
use super::carry::carry_events;
use super::forms::{grid_forms_with_axes, grid_forms_with_early, grid_forms_with_entry_ttl};
use super::outputs::{forms_row, sum_net_bps, FORMS_HEADER, FORMS_HEADER_CARRY_LEN};
use super::sets::{Range, CTX_AXES};
use super::*;
use crate::commands::lob::backtest::{EntryForm, EntryTtl, PoolLot, StopForm, TakeForm};
use crate::commands::lob::bounce_verdict::{parse_form, DEADLINE_SECS};
use crate::commands::lob::test_support::{delta_frame, snap_frame, trade_frame, write_day};
use crate::commands::lob::{H3Args, H3ModeArg};
use crate::lob::backtest::{BounceRun, QueueModelKind};
use crate::lob::levels::TouchRecord;
use crate::lob::strategy::WallStopMode;

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

/// Фикстура F6 (В-73): цена **подходит** к бид-стене 99.00 (тик 9900) за 2 с
/// до касания. Аск стоит далеко (130.00 — 3130 bps от стены, полоса не
/// взведена), на `lead+3500` входит в полосу 750 bps (106.00 против 99.00 —
/// 707 bps), на `lead+4000` кадр стороны бида взводит подход, на `lead+5500`
/// убирается бид 100.00: стена становится лучшей — **касание**, подход
/// снимается касанием. Свип 99.00 на `lead+5000` (за 0,5 с **до** касания)
/// исполняет ноги входа, поставленные на взводе, а касание закрывает круг
/// стопом «в стену».
fn approach_frames() -> Vec<Vec<crate::binlog::Record>> {
    let lead = LEAD_S * 1_000;
    let mut frames = vec![snap_frame(
        0,
        &[(9_800, 10), (9_900, 10), (10_000, 10)],
        &[(13_000, 10)],
    )];
    for s in 1..=LEAD_S {
        // Аск дышит далеко от стены: полоса взвода не задета.
        let ask = if s % 2 == 0 { 13_000 } else { 13_100 };
        frames.push(delta_frame(s * 1_000, &[], &[(ask, 10), (26_100 - ask, 0)]));
    }
    frames.extend([
        // Подход: аск 106.00 — 707 bps от стены 99.00 (полоса 750).
        delta_frame(lead + 3_500, &[], &[(10_600, 10), (13_000, 0), (13_100, 0)]),
        // Кадр стороны бида: чужая цена (106.00) уже известна, стена ещё не
        // лучшая (впереди 100.00) — взвод подхода здесь, а не на касании.
        delta_frame(lead + 4_000, &[(9_800, 20)], &[]),
        // Свип в стену до касания: сделка по 99.00 исполняет ноги входа.
        trade_frame(lead + 5_000, 9_900, 4),
        // Касание: 100.00 снят — стена стала лучшей ценой, подход снят.
        delta_frame(lead + 5_500, &[(10_000, 0)], &[]),
        // Книга стоит: круг закрывается стопом «в стену» (99.00).
        delta_frame(lead + 5_700, &[(9_900, 10)], &[(10_600, 10)]),
        delta_frame(lead + 5_900, &[(9_900, 10)], &[(10_600, 10)]),
        // Второй свип: сигнал **касания** (t0 = lead+5500) ставит ногу 99.01
        // позже и исполняется здесь — на тех же данных видно разницу сигналов.
        trade_frame(lead + 6_000, 9_900, 4),
        delta_frame(lead + 6_200, &[(9_900, 10)], &[(10_600, 10)]),
        // 100.00 вернулся: касание стены заканчивается и попадает в CSV
        // касаний (открытое касание в конце записи не эмитится).
        delta_frame(lead + 6_400, &[(10_000, 10)], &[]),
    ]);
    frames
}

fn fixture_root_approach(dir: &std::path::Path) {
    std::fs::write(
        crate::commands::record::instruments_csv_path(dir),
        "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n\
         SOLUSDT,0.01,0.1,0.1,5,5\n",
    )
    .unwrap();
    write_day(dir, "SOLUSDT", "2026-09-08", &approach_frames());
    std::fs::write(
        dir.join("session.json"),
        "{\"started_utc\":\"2026-09-08T00:00:00Z\",\"start_hour_utc\":0,\"instruments\":[\"SOLUSDT\"]}",
    )
    .unwrap();
    std::fs::write(dir.join("verify-SOLUSDT.status"), "ok").unwrap();
}

fn args(root: &std::path::Path, allow_unverified: bool) -> BounceGridArgs {
    BounceGridArgs {
        root: root.to_path_buf(),
        symbols: vec!["SOLUSDT".to_string()],
        median_rtt_ns: crate::lob::backtest::ExecLatency::uniform(20_000_000),
        p95_rtt_ns: crate::lob::backtest::ExecLatency::uniform(20_000_000),
        queue_model: QueueModelKind::RiskAdverse,
        order_qty_e9: Some(100_000_000),
        order_qty_mult: 1,
        order_qty_from_pool: false,
        order_usd: None,
        // Оба флага сняты — умолчание команды: вход пост-онли (В-72).
        post_only: false,
        no_post_only: false,
        stop_form: vec!["s1".to_string(), "s2".to_string()],
        take_form: vec!["t1".to_string()],
        take_floor_fees: Some(1.0),
        // F5 (В-74): по умолчанию — прежний режим `touch`, полоса не задана.
        entry_ttl_secs: Vec::new(),
        band_exit_bps: None,
        frontrun_only: false,
        min_age_secs: None,
        min_flow_pct: None,
        side: None,
        touches_from: None,
        // F6 (В-73): прежний сигнал (касание) и прежний вход (`single@fr`) —
        // на них стоит гейт «те же круги»; подход и лестница — своими тестами.
        signal: SignalArg::Touch,
        entry_form: Vec::new(),
        sets: Vec::new(),
        cells: None,
        sigma_from: None,
        hold_step: "poll".to_string(),
        exit_group: "off".to_string(),
        p08_cols: false,
        r1_cols: false,
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
        round_memo: "on".to_string(),
        busy_skip: "on".to_string(),
        // К3: сверка окон с книгой крейта на всех сутках тестовых сеток.
        windows_check: true,
        events: "compact".to_string(),
        out_dir: root.join("grid"),
        allow_unverified,
        verdict_csv: None,
        // F7/F8: форма выхода — по умолчанию `none` (гейт).
        exit_form: Vec::new(),
        btc_minutes: Vec::new(),
        early_exit_secs: Vec::new(),
        carry_age: false,
        touches_cache_only: false,
        carry_root: None,
        extra_runs: None,
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
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    write_day(dir.path(), "SOLUSDT", "2026-09-09", &touch_frames());
    run_bounce_grid(&args(dir.path(), false)).expect("одна сетка — счёт идёт");

    let path = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-09", 1);
    let (tick_e9, step_e9) = read_tick_step(&path).unwrap();
    let header = crate::binlog::Header {
        tick_e9: tick_e9 * 10,
        step_e9,
        max_records_per_frame: 4096,
    };
    let mut w = crate::binlog::Writer::create(Vec::new(), header, 1).unwrap();
    for f in &touch_frames() {
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
            usd_min: None,
            behind_min_pct: None,
            stack_min: None,
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

/// T-35 (Г-85): ключ `frontrun_min=<лоты>` — порог фронтрана вместо «да/нет»; порог 1 = `frontrun`, огромный —
/// ни одного сигнала; без ключа шапка прежняя.
#[test]
fn frontrun_min_key_filters_by_frontrun_lots_at_touch() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid-frmin");
    a.sets = vec![
        "fr:frontrun".to_string(),
        "fm1:frontrun_min=1".to_string(),
        "fmbig:frontrun_min=1000000000".to_string(),
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
    // фронтран есть ⇔ впереди уровня ≥ 1 лота: порог 1 — те же касания, что «да/нет» `frontrun`
    assert_eq!(
        body(&by_name("fm1").rounds_path),
        body(&by_name("fr").rounds_path)
    );
    let (fh, big) = read_csv(&by_name("fmbig").forms_path);
    for r in &big {
        assert_eq!(col(&fh, r, "n_signals"), "0", "{r:?}");
    }
    let head = std::fs::read_to_string(&by_name("fm1").forms_path).unwrap();
    assert!(
        head.contains(" frontrun_only=false frontrun_min=1 "),
        "{head}"
    );
    let head = std::fs::read_to_string(&by_name("fr").forms_path).unwrap();
    assert!(!head.contains("frontrun_min"), "без ключа шапка прежняя");
    for bad in ["x:frontrun_min=0", "x:frontrun_min=-1", "x:frontrun_min=a"] {
        assert!(FilterSet::parse(bad).is_err(), "{bad}");
    }
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
        depth_behind_lots: 0,
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

/// T4 (П-02, Г-86): ключ `eaten_min=<%>` — зеркало `eaten=<%>` (там «не
/// больше», здесь «не меньше»). Фикстура `touch_frames()` — размер тика 99
/// не падает **до** ни одного из трёх касаний (`size_max_before ==
/// size_at_touch`), поэтому `eaten_pct == 0` у всех: порог 0 не выбивает
/// ничего (байты те же), любой порог выше нуля выбивает все три.
#[test]
fn eaten_min_key_filters_by_wall_erosion_at_touch() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid-eaten-min");
    a.sets = vec![
        "all:".to_string(),
        "e0:eaten_min=0".to_string(),
        "e1:eaten_min=1".to_string(),
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
    assert_eq!(body(&by("e0").rounds_path), body(&by("all").rounds_path));
    let (fh, e1) = read_csv(&by("e1").forms_path);
    assert!(e1
        .iter()
        .all(|r| col(&fh, r, "n_signals") == "0" && col(&fh, r, "n_skipped") == "3"));
    let head = std::fs::read_to_string(&by("e1").forms_path).unwrap();
    assert!(head.contains(" eaten_min=Some(1.0) "), "{head}");
}

/// `--signal approach` не определяет `eaten_min=` (T4) так же, как `eaten=`
/// — размер на взводе и есть старт, истории размера нет.
#[test]
fn eaten_min_key_is_rejected_with_approach_signal() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    // Не-σ формы и снятые умолчания трекера — требования `--touches-from`
    // (проверяются раньше нашего ключа, см. `resolve_plan`); сам кэш не
    // читается до этого отказа, поэтому подойдёт любой существующий каталог.
    a.stop_form = vec!["behind".to_string()];
    a.take_form = vec!["1to1".to_string()];
    a.take_floor_fees = None;
    a.warmup_ms = None;
    a.repeat_window_ms = None;
    a.signal = SignalArg::Approach;
    a.touches_from = Some(dir.path().to_path_buf());
    a.sets = vec!["x:eaten_min=1".to_string()];
    let err = run_bounce_grid(&a).unwrap_err().to_string();
    assert!(err.contains("eaten_min"), "{err}");
}

/// T4 (П-02, Г-86): `--entry-form market` — вход пересекает спред
/// (`entry_crossed`), но не отклоняется как пост-онли (В-72 не действует на
/// рыночный вход: `post_only` снят самой формой) и исполняется.
#[test]
fn entry_form_market_crosses_the_spread_instead_of_being_rejected_postonly() {
    // Свой, тесный спред: `touch_frames()`/`fixture_root()` держат аск в
    // 600–700 bps от стены (99 против 105/106 — простор для теста подхода и
    // ряда σ), а рыночному входу нужен реалистичный спред, который его
    // запас (`MARKET_CROSS_MARGIN_BPS = 500`) пересечёт: аск в 3 тиках от
    // стены (99 → 102, ≈ 300 bps).
    fn tight_spread_frames() -> Vec<Vec<crate::binlog::Record>> {
        vec![
            snap_frame(0, &[(98, 10), (99, 10), (100, 10)], &[(102, 10)]),
            delta_frame(1000, &[(100, 0)], &[]), // 100 снят — 99 лучший: касание.
            delta_frame(2000, &[(100, 10)], &[]), // конец касания.
        ]
    }
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        crate::commands::record::instruments_csv_path(dir.path()),
        "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n\
         SOLUSDT,0.01,0.1,0.1,5,5\n",
    )
    .unwrap();
    write_day(dir.path(), "SOLUSDT", "2026-09-08", &tight_spread_frames());
    std::fs::write(
        dir.path().join("session.json"),
        "{\"started_utc\":\"2026-09-08T00:00:00Z\",\"start_hour_utc\":0,\"instruments\":[\"SOLUSDT\"]}",
    )
    .unwrap();
    std::fs::write(dir.path().join("verify-SOLUSDT.status"), "ok").unwrap();

    let mut a = args(dir.path(), false);
    a.stop_form = vec!["behind".to_string()];
    a.take_form = vec!["1to1".to_string()];
    a.take_floor_fees = None;
    a.entry_form = vec!["market".to_string()];
    a.out_dir = dir.path().join("grid-market");
    let m = run_bounce_grid(&a).unwrap();
    // Фикстура — три кадра (снимок, снятие 100, возврат 100): достаточно
    // событий, чтобы сигнал построился и заявка ушла и пересекла спред, но
    // не достаточно, чтобы обычный дедлайн (мин. 60 с) успел разрешить
    // круг целиком (`incomplete=true`, ожидаемо на такой короткой записи —
    // не предмет этого теста). Проверяем сам механизм T4: заявка
    // действительно **пересекла** спред (`entry_crossed`) и не была
    // отклонена как пост-онли (`n_rejected_postonly`) — это и отличает
    // `market` от `single@fr`/`ladder…`, которые на этой же фикстуре и с тем
    // же `--post-only` (умолчание команды) были бы отклонены `Expired`.
    let (fh, rows) = read_csv(&m.forms_path);
    assert!(!rows.is_empty());
    for r in &rows {
        assert_eq!(
            col(&fh, r, "n_rejected_postonly"),
            "0",
            "рыночный вход не пост-онли — GTX его бы отклонил как Expired"
        );
        assert!(
            col(&fh, r, "entry_crossed").parse::<u32>().unwrap() >= 1,
            "рыночный вход обязан пересечь спред: {r:?}"
        );
    }
}

/// Гейт F3/F4: `--queue-model risk-adverse --no-post-only` — прежний движок
/// и прежний вход (обычный лимит `GTC`), и круг (`rounds.csv`) и числа форм
/// (`forms.csv`) обязаны совпасть с прогоном до правок. «Золото» — снятый до
/// правки вывод фикстуры (`golden/rounds.csv`, `golden/forms.csv`):
/// сравниваются все поля по именам, поэтому добавленные правками колонки
/// (`n_fill_by_cross` у F3; `fill_frac`/`entry_vwap`/`legs_filled`/
/// `legs_rejected` у F4) и поле шапки (`queue=…`, `entry_post_only=…`) гейт не
/// обманывают, а любое расхождение прежнего поля — валит. Пост-онли новое
/// умолчание (В-72), поэтому гейт гоняется именно с `--no-post-only`.
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
    a.no_post_only = true;
    // F5 (В-74): гейт «те же круги» — с явным прежним режимом входа
    // (`touch`), условия рынка выключены.
    a.entry_ttl_secs = vec!["touch".to_string()];
    // F6 (В-73): та же явная прежняя форма входа — одиночная нога у
    // фронтранера; имя формы остаётся трёхпольным (гейт).
    a.entry_form = vec!["single@fr".to_string()];
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
        head.contains(" entry_post_only=false "),
        "режим входа в шапке (гейт гоняется с --no-post-only): {head}"
    );
    assert!(
        head.contains(" entry_ttl=touch ") && head.contains(" band_exit_bps=0 "),
        "прежний режим срока жизни входа в шапке (гейт F5): {head}"
    );
    assert!(
        head.contains(" signal=touch ") && head.contains(" entry_forms=single@fr "),
        "прежний сигнал и прежняя форма входа в шапке (гейт F6): {head}"
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
        assert_eq!(
            col(&fh, r, "n_rejected_postonly"),
            "0",
            "вход не пересекал спред — отвергнутых ног нет: {r:?}"
        );
    }
    same_fields(&m.rounds_path, include_str!("golden/rounds.csv"), &[]);
    // Колонки F4 осмысленны на круге фикстуры: вход исполнился целиком одной
    // ногой, поэтому доля — единица, средняя равна цене входа, ног — одна, а
    // не поставленных биржей — ноль (В-78, В-72).
    let (rh, rounds) = read_csv(&m.rounds_path);
    assert!(!rounds.is_empty(), "фикстура обязана давать круги");
    for r in &rounds {
        assert_eq!(col(&rh, r, "fill_frac"), "1.000000", "{r:?}");
        assert_eq!(
            col(&rh, r, "entry_vwap"),
            col(&rh, r, "entry_px"),
            "полное исполнение: средняя равна цене входа — {r:?}"
        );
        assert_eq!(col(&rh, r, "legs_filled"), "1", "{r:?}");
        assert_eq!(col(&rh, r, "legs_rejected"), "0", "{r:?}");
    }

    // Разбор флага: без `--queue-model` команда не запускается вовсе
    // (умолчания в коде нет), `prob:<n>` собирает свой движок.
    assert!(QueueModelKind::parse("").is_err());
    let mut b = args(dir.path(), false);
    b.queue_model = QueueModelKind::Prob { n: 3.0 };
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
        parse_entry_ttls(&["120".to_string()]).is_err(),
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
        super::super::bounce_verdict::parse_form_fields("pct2-1to1-3600-eat50")
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
        entry_cancelled_cancel_timeout: entry_cancel_timeout,
        exit_cancel_timeout,
        orphan_fills: 0,
        spread_at_entry: Vec::new(),
        submitted_signal: Vec::new(),
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
    assert_eq!(earlies, vec![None, Some(2)]);
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
    assert_eq!(both[2].early_exit_secs, Some(2));
    assert_eq!(both[0].early_exit_secs, None);

    assert!(parse_early_exits(&[]).unwrap() == vec![None]);
    assert!(
        parse_early_exits(&["5".to_string()]).is_err(),
        "5 с нет в наборе В-58"
    );
    assert!(parse_early_exits(&["x".to_string()]).is_err());
}

// -----------------------------------------------------------------------
// Перенос круга через полночь (`--carry-root`): круг, ещё открытый на конце
// суток D, дочитывает выход по данным D+1 вместо `incomplete`.
// -----------------------------------------------------------------------

fn day_start_ns_for_test(day: &str) -> i64 {
    chrono::NaiveDate::parse_from_str(day, "%Y-%m-%d")
        .unwrap()
        .and_hms_opt(0, 0, 0)
        .unwrap()
        .and_utc()
        .timestamp_nanos_opt()
        .unwrap()
}

/// Цена стены — крупный тик (100.00, а не 0.99, как у `touch_frames()`):
/// стоп `pct1` от цены входа ~100.01 — это **сто** тиков (шаг 0.01), а не
/// один, так что после входа стоп остаётся далеко ниже текущего рынка (не
/// пересекается им сразу же, как было бы у входа в тике от стены). Порог
/// H3 (`instruments.csv`, `h3_lots=5`) от масштаба цены не зависит.
const WALL_TICK: i64 = 10_000;
const FRONTRUN_TICK: i64 = 10_001;

/// Своя минимальная фикстура (не `touch_frames()`: там касание того же
/// уровня повторяется три раза подряд, что этому тесту не нужно, а
/// геометрия входа в тике от стены оставляла бы стопу `pct1` меньше тика
/// запаса) — один-единственный, полностью финализированный внутри суток D
/// touch, круг которого при этом остаётся **открытым** к концу дня. Метки —
/// настоящие эпоховые (`day_start_ns_for_test(day)`, не относительный нуль):
/// вход — за 40 с до полуночи, а не сразу после старта суток, иначе
/// `--deadline-secs` (60 с от входа) наступил бы раньше, чем перенос успел бы
/// дочитать данные D+1 (сравнение абсолютных нс — так же, как у настоящего
/// бэктеста).
///
/// - снапшот на старте суток: стена `WALL_TICK` (10 лотов, порог H3 — 5),
///   фронтран `FRONTRUN_TICK` перед ней, лучшая цена — фронтран (весь день
///   до входа книга стоит без событий — реплею это ничем не грозит).
/// - `t0 = 23:59:20`: фронтран снят — касание стены СТАРТУЕТ
///   (`frontrun_tick=Some(FRONTRUN_TICK)`, лучшая цена теперь стена).
/// - `t0+100мс`: сделка продавца ровно по цене резерва (`FRONTRUN_TICK`) —
///   исполняет вход (`single@fr`: резерв на фронтране, `trade_could_fill` —
///   сделка на цене резерва или ниже её исполняет; здесь ровно на ней),
///   цену не двигает — рынок остаётся у стены, далеко выше стопа.
/// - `t0+1с` (23:59:21): размер стены падает с 10 до 1 (< порога 5) —
///   уровень умирает, касание ФИНАЛИЗИРУЕТСЯ (`duration_ms=1000` — это и
///   есть `entry_ttl_ns` режима `touch`, вход уже исполнен к этому моменту,
///   тайм-аут ему не грозит) и появляется в `day.touches`.
///
/// Дальше сутки D обрываются: круг открыт (вход исполнен, выход не
/// наступил), а данных для стопа/тейка/дедлайна в этом дне больше нет —
/// ровно случай задачи («вход 21:00, дедлайн 4 ч»).
fn touch_frames_open_at_day_end(day: &str) -> Vec<Vec<crate::binlog::Record>> {
    let day_start_ms = day_start_ns_for_test(day) / 1_000_000;
    let t0 = day_start_ms + 86_360_000; // 23:59:20
    vec![
        snap_frame(
            day_start_ms,
            &[(WALL_TICK, 10), (FRONTRUN_TICK, 10)],
            &[(WALL_TICK + 500, 10)],
        ),
        delta_frame(t0, &[(FRONTRUN_TICK, 0)], &[]),
        trade_frame(t0 + 100, FRONTRUN_TICK, 4),
        delta_frame(t0 + 1_000, &[(WALL_TICK, 1)], &[]),
        // Кадр-заглушка (то же состояние книги) сразу после финализации
        // касания: без него сутки обрываются РОВНО на критической метке
        // времени, и опросу движка (`bot.elapse`) не хватает шага, чтобы
        // это заметить — пробное усечение golden-фикстуры `touch_frames()`
        // (see debug probe) показало ту же чувствительность «плюс один
        // кадр» на границе конца данных.
        delta_frame(t0 + 1_500, &[(WALL_TICK, 1)], &[]),
    ]
}

/// Хвост-довесок: сутки D+1 корня переноса, с абсолютными эпоховыми метками
/// начала `next_day` (не относительными нулём, как у `touch_frames_open_
/// at_day_end`: довесок сравнивается с настоящей полуночью — `day_start_ns`).
/// Открывается собственным снапшотом, как часть-переподключение (A4) — тем
/// же приёмом, что уже сшивает части одних суток (`day_events`). Книга
/// дальше стоит без движения — выход круга здесь не от цены, а от дедлайна
/// (`--deadline-secs 60` от входа `23:59:20` суток D — ровно 00:00:20 суток
/// D+1, `20_000`/`20_500` мс после полуночи ниже): без ЕЩЁ ОДНОГО кадра
/// после точки дедлайна опрос движка (`bot.elapse`) не успевает её
/// заметить — та же чувствительность «плюс один кадр», что и у
/// `touch_frames_open_at_day_end`.
fn carry_tail_frames(next_day: &str) -> Vec<Vec<crate::binlog::Record>> {
    let start_ms = day_start_ns_for_test(next_day) / 1_000_000;
    vec![
        // Снапшот — то же состояние книги, в котором сутки D оставили её
        // (стена — огрызок в 1 лот после смерти уровня, фронтран давно снят).
        snap_frame(start_ms, &[(WALL_TICK, 1)], &[(WALL_TICK + 500, 10)]),
        delta_frame(start_ms + 20_000, &[(WALL_TICK, 1)], &[]),
        delta_frame(start_ms + 20_500, &[(WALL_TICK, 1)], &[]),
    ]
}

fn write_carry_root(dir: &std::path::Path, next_day: &str) {
    write_day(dir, "SOLUSDT", next_day, &carry_tail_frames(next_day));
    std::fs::write(dir.join("session.json"), "{\"start_hour_utc\":0}").unwrap();
}

/// Без `--carry-root`: сутки D обрываются сразу после касания — круг не
/// находит выхода в данных этого дня, `incomplete=true`, кругов в
/// `rounds.csv` нет.
#[test]
fn without_carry_root_a_round_still_open_at_day_end_stays_incomplete() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &touch_frames_open_at_day_end("2026-09-08"),
    );

    let mut a = args(dir.path(), false);
    a.stop_form = vec!["pct1".to_string()];
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
    a.no_post_only = true;
    a.entry_ttl_secs = vec!["touch".to_string()];
    a.entry_form = vec!["single@fr".to_string()];
    a.deadline_secs = vec![60];
    a.out_dir = dir.path().join("grid-no-carry");
    let m = run_bounce_grid(&a).unwrap();
    let (fh, forms) = read_csv(&m.forms_path);
    assert_eq!(forms.len(), 1, "одна форма сетки");
    assert_eq!(col(&fh, &forms[0], "n_submitted"), "1", "вход отправлен");
    assert_eq!(
        col(&fh, &forms[0], "incomplete"),
        "true",
        "без --carry-root круг не дочитан к концу данных суток D"
    );
    // R6: без --carry-root колонок переноса нет вовсе — шапка прежняя, 33
    // колонки (гейт «те же байты», `gate-g10.sh`).
    assert_eq!(
        fh.len(),
        FORMS_HEADER.len() - FORMS_HEADER_CARRY_LEN,
        "без --carry-root шапка прежняя, 33 колонки"
    );
    assert!(
        !fh.iter()
            .any(|h| h == "n_carried" || h == "carry_unverified"),
        "колонок переноса без флага нет: {fh:?}"
    );
    let (_, rounds) = read_csv(&m.rounds_path);
    assert!(rounds.is_empty(), "круг без выхода не идёт в rounds.csv");
}

/// С `--carry-root`: те же сутки D дочитывают выход по данным D+1 из
/// отдельного корня записи — круг закрыт (`incomplete=false`), помечен
/// перенесённым (`n_carried=1`), день круга в `rounds.csv` остаётся D, а
/// `exit_ns` — уже после настоящей полуночи D+1. Выход здесь — дедлайн
/// (60 с от входа `23:59:20` = 00:00:20 суток D+1): цена в фикстуре не
/// двигается, так что дочитывание видно от чистого наличия данных, не от
/// конкретной причины выхода.
#[test]
fn carry_root_finishes_a_round_still_open_at_midnight() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    write_day(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        &touch_frames_open_at_day_end("2026-09-08"),
    );

    let carry_dir = tempfile::tempdir().unwrap();
    write_carry_root(carry_dir.path(), "2026-09-09");
    std::fs::write(carry_dir.path().join("verify-SOLUSDT.status"), "ok").unwrap();

    let mut a = args(dir.path(), false);
    a.stop_form = vec!["pct1".to_string()];
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
    a.no_post_only = true;
    a.entry_ttl_secs = vec!["touch".to_string()];
    a.entry_form = vec!["single@fr".to_string()];
    a.deadline_secs = vec![60];
    a.carry_root = Some(carry_dir.path().to_path_buf());
    a.out_dir = dir.path().join("grid-carry");
    let m = run_bounce_grid(&a).unwrap();
    let (fh, forms) = read_csv(&m.forms_path);
    assert_eq!(forms.len(), 1, "одна форма сетки");
    assert_eq!(
        fh.len(),
        FORMS_HEADER.len(),
        "с --carry-root шапка полная, 35 колонок"
    );
    assert_eq!(
        col(&fh, &forms[0], "incomplete"),
        "false",
        "с --carry-root круг дочитывает выход по суткам D+1"
    );
    assert_eq!(col(&fh, &forms[0], "n_fills"), "1");
    assert_eq!(col(&fh, &forms[0], "n_carried"), "1");
    assert_eq!(
        col(&fh, &forms[0], "carry_unverified"),
        "false",
        "маркер сверки в корне довеска есть и он `ok`"
    );
    let (rh, rounds) = read_csv(&m.rounds_path);
    assert_eq!(rounds.len(), 1);
    assert_eq!(
        col(&rh, &rounds[0], "day_utc"),
        "2026-09-08",
        "день круга остаётся сутками D, а не D+1"
    );
    let exit_ns: i64 = col(&rh, &rounds[0], "exit_ns").parse().unwrap();
    assert!(
        exit_ns >= day_start_ns_for_test("2026-09-09"),
        "выход исполнился уже после настоящей полуночи D+1: {exit_ns}"
    );

    // T-31 (условие Судьи 3): круг, перенесённый через полночь, при `--busy-skip off` — тот же круг, и след
    // несёт его `idle_ns`/`exit_ns` после полуночи; занятость у движка — на символ-сутки D, D+1 её не наследует.
    a.busy_skip = "off".to_string();
    a.out_dir = dir.path().join("grid-carry-off");
    let m_off = run_bounce_grid(&a).unwrap();
    let (_, rounds_off) = read_csv(&m_off.rounds_path);
    assert_eq!(rounds_off, rounds, "перенесённый круг при `off` — тот же");
    let (sh, sig) = read_csv(&m_off.rounds_path.with_file_name("signals.csv"));
    assert_eq!(sig.len(), 1);
    assert_eq!(col(&sh, &sig[0], "step"), "filled");
    assert_eq!(col(&sh, &sig[0], "exit_ns"), exit_ns.to_string());
    let idle: i64 = col(&sh, &sig[0], "idle_ns").parse().unwrap();
    assert!(
        idle >= exit_ns,
        "форма свободна не раньше выхода: {idle} < {exit_ns}"
    );
}

/// Перенос ограничен окном времени: событие довеска далеко за окном (здесь —
/// намеренно за пределами `--deadline-secs 60` + запас RTT) не читается —
/// `carry_events` останавливает декод на первом событии `local_ts_ns ≥
/// until_ns`, не декодируя сутки D+1 целиком (тот же приём, что бережёт
/// память у `day_events`).
#[test]
fn carry_events_stops_decoding_past_the_carry_window() {
    let next_day = "2026-09-09";
    let start_ms = day_start_ns_for_test(next_day) / 1_000_000;
    let dir = tempfile::tempdir().unwrap();
    write_day(
        dir.path(),
        "SOLUSDT",
        next_day,
        &[
            snap_frame(start_ms, &[(99, 10)], &[(105, 10)]),
            // В окне (сразу после полуночи).
            delta_frame(start_ms + 1_000, &[(99, 9)], &[]),
            // Далеко за окном — читаться не должно.
            delta_frame(start_ms + 3_600_000_000, &[(99, 1)], &[]),
        ],
    );
    let path = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", next_day, 1);
    let until_ns = day_start_ns_for_test(next_day) + 60_000_000_000;
    let events = carry_events(&[path], until_ns).unwrap();
    assert!(!events.is_empty(), "событие в окне обязано быть прочитано");
    assert!(
        events.iter().all(|e| e.local_ts() < until_ns),
        "событие за окном попало в результат: {:?}",
        events.iter().map(|e| e.local_ts()).collect::<Vec<_>>()
    );
}

/// Р10 (T-22): довесок дописывается прямо в буфер суток — те же события в том же
/// порядке, что отдельный `carry_events`, а ёмкость буфера растёт ровно на довесок,
/// без амортизированного удвоения (иначе крупные сутки не влезают в память).
#[test]
fn append_carry_events_grows_day_buffer_exactly() {
    let next_day = "2026-09-09";
    let start_ms = day_start_ns_for_test(next_day) / 1_000_000;
    let dir = tempfile::tempdir().unwrap();
    write_day(
        dir.path(),
        "SOLUSDT",
        next_day,
        &[
            snap_frame(start_ms, &[(99, 10), (98, 5)], &[(105, 10)]),
            delta_frame(start_ms + 1_000, &[(99, 9)], &[]),
            delta_frame(start_ms + 2_000, &[], &[(105, 7)]),
            delta_frame(start_ms + 3_600_000_000, &[(99, 1)], &[]),
        ],
    );
    let path = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", next_day, 1);
    let until_ns = day_start_ns_for_test(next_day) + 60_000_000_000;
    let alone = carry_events(std::slice::from_ref(&path), until_ns).unwrap();
    assert!(!alone.is_empty(), "довесок в окне обязан быть прочитан");
    // Буфер «суток» ровно по размеру, как у `day_events`.
    let day: Vec<crate::lob::backtest::CompactEvent> = alone.iter().take(1).copied().collect();
    let mut events: Vec<crate::lob::backtest::CompactEvent> = Vec::with_capacity(day.len());
    events.extend_from_slice(&day);
    assert_eq!(events.capacity(), events.len());
    let n = super::carry::append_carry_events(&[path], until_ns, &mut events).unwrap();
    assert_eq!(n, alone.len());
    assert_eq!(events.len(), day.len() + alone.len());
    assert_eq!(
        events.capacity(),
        events.len(),
        "ёмкость — ровно сутки + довесок, без удвоения"
    );
    assert_eq!(
        &events[day.len()..],
        &alone[..],
        "те же события в том же порядке"
    );
}

/// Счёт довеска кэшируется сайдкаром по окну: второй вызов даёт те же события, чужое окно — промах.
#[test]
fn append_carry_events_count_sidecar_roundtrip() {
    let next_day = "2026-09-09";
    let start_ms = day_start_ns_for_test(next_day) / 1_000_000;
    let dir = tempfile::tempdir().unwrap();
    write_day(
        dir.path(),
        "SOLUSDT",
        next_day,
        &[
            snap_frame(start_ms, &[(99, 10)], &[(105, 10)]),
            delta_frame(start_ms + 1_000, &[(99, 9)], &[]),
            delta_frame(start_ms + 3_600_000_000, &[(99, 1)], &[]),
        ],
    );
    let path = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", next_day, 1);
    let until_ns = day_start_ns_for_test(next_day) + 60_000_000_000;
    assert!(super::carry::cached_carry_count(&path, until_ns).is_none());
    let mut a = Vec::new();
    let na =
        super::carry::append_carry_events(std::slice::from_ref(&path), until_ns, &mut a).unwrap();
    let cached = super::carry::cached_carry_count(&path, until_ns).expect("сайдкар записан");
    assert_eq!(cached.0, na);
    assert!(super::carry::cached_carry_count(&path, until_ns + 1).is_none());
    let mut b = Vec::new();
    let nb =
        super::carry::append_carry_events(std::slice::from_ref(&path), until_ns, &mut b).unwrap();
    assert_eq!((na, &a), (nb, &b));
}

/// R2 (ревью 23.09): лот `--order-usd` — по цене **каждого** касания, а не
/// одной ценой на символ (прежде — последнего касания всей записи, то есть
/// заглядывание вперёд). $100 при цене 10.00 — 10 монет, при 20.00 — 5; шаг
/// лота 0.1. Явный лот (`--order-qty-e9`) от цены не зависит; множитель E7
/// умножает оба.
#[test]
fn order_usd_sizes_each_touch_at_its_own_price() {
    let lot = PoolLot {
        min_order_qty_e9: 100_000_000,
        qty_step_e9: 100_000_000,
        min_notional_value_e9: 5_000_000_000,
    };
    let tick_e9 = 10_000_000; // 0.01
    let mut cheap = probe_touch();
    cheap.price_tick = 1_000; // 10.00
    let mut dear = probe_touch();
    dear.price_tick = 2_000; // 20.00
    let touches = [cheap, dear];

    let usd = OrderSizing::Usd(lot, 100.0).touch_qtys(&touches, tick_e9, 1);
    assert_eq!(usd.len(), 2);
    assert!((usd[0] - 10.0).abs() < 1e-9, "$100 / 10.00: {}", usd[0]);
    assert!((usd[1] - 5.0).abs() < 1e-9, "$100 / 20.00: {}", usd[1]);

    let doubled = OrderSizing::Usd(lot, 100.0).touch_qtys(&touches, tick_e9, 2);
    assert!((doubled[0] - 20.0).abs() < 1e-9 && (doubled[1] - 10.0).abs() < 1e-9);

    let fixed = OrderSizing::Fixed(300_000_000).touch_qtys(&touches, tick_e9, 1);
    assert!(fixed.iter().all(|q| (q - 0.3).abs() < 1e-12), "{fixed:?}");

    // 22а: минимальный чек $5 при 10.00 — 0.5, при 20.00 — 0.3 (вверх до шага).
    let pool = OrderSizing::Pool22a(lot).touch_qtys(&touches, tick_e9, 1);
    assert!(
        (pool[0] - 0.5).abs() < 1e-9 && (pool[1] - 0.3).abs() < 1e-9,
        "{pool:?}"
    );
}

/// R3 (ревью 23.09, замечание проверки): явный лот `--order-qty-e9`, не
/// кратный шагу записи, — отказ, а не молчаливое округление ног лестницы
/// (0.0015 при шаге 0.001 дал бы 2 шага — +33 % к размеру круга).
#[test]
fn fixed_order_qty_off_the_lot_step_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.order_qty_e9 = Some(1_500_000);
    a.out_dir = dir.path().join("grid-off-lot");
    let err = run_bounce_grid(&a).unwrap_err().to_string();
    assert!(err.contains("не кратен шагу лота"), "{err}");
}

/// Замечание проверки R2: явный нулевой лот — отказ, а не круг без размера
/// (`0 % шаг == 0` проходил проверку кратности).
#[test]
fn zero_fixed_order_qty_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.order_qty_e9 = Some(0);
    a.out_dir = dir.path().join("grid-zero-lot");
    let err = run_bounce_grid(&a).unwrap_err().to_string();
    assert!(err.contains("не положителен"), "{err}");
}

/// E26: промежуточные оси битка `btc2h`/`btc3h` — ключи набора, колонки режима
/// `btc_ret_2h_bps`/`btc_ret_3h_bps`; прежний файл режима без них читается (оси —
/// `None`), но если оси нужны наборам — отказ, а не молчаливый пустой набор.
#[test]
fn btc_mid_axes_parse_and_regime_columns() {
    let set = FilterSet::parse("x:btc2h_max=-30,btc3h_min=-40").unwrap();
    assert!(set.uses_regime() && set.uses_btc_mid());
    assert_eq!(set.ctx[7].max, Some(-30.0));
    assert_eq!(set.ctx[8].min, Some(-40.0));
    assert_eq!(set.ctx_label(), "btc2h_max=-30,btc3h_min=-40");
    assert!(!FilterSet::parse("y:btc4h_max=0").unwrap().uses_btc_mid());

    let dir = tempfile::tempdir().unwrap();
    let old = "minute_ms,pool_ret_1h_bps,pool_ret_4h_bps,n_coins,btc_ret_1h_bps,btc_ret_4h_bps,eth_ret_1h_bps,eth_ret_4h_bps\n\
               60000,1,2,5,-10,-40,,\n";
    std::fs::write(dir.path().join("2026-09-08.csv"), old).unwrap();
    let day = read_regime_day(dir.path(), "2026-09-08", false).unwrap();
    assert_eq!(
        day[&60_000],
        [Some(1.0), Some(2.0), Some(-10.0), Some(-40.0), None, None]
    );
    let err = read_regime_day(dir.path(), "2026-09-08", true)
        .unwrap_err()
        .to_string();
    assert!(err.contains("btc_ret_2h_bps"), "{err}");

    let new = "minute_ms,pool_ret_1h_bps,pool_ret_4h_bps,n_coins,btc_ret_1h_bps,btc_ret_4h_bps,eth_ret_1h_bps,eth_ret_4h_bps,btc_ret_2h_bps,btc_ret_3h_bps\n\
               60000,1,2,5,-10,-40,,,-20,-30\n";
    std::fs::write(dir.path().join("2026-09-09.csv"), new).unwrap();
    let day = read_regime_day(dir.path(), "2026-09-09", true).unwrap();
    assert_eq!(
        day[&60_000],
        [
            Some(1.0),
            Some(2.0),
            Some(-10.0),
            Some(-40.0),
            Some(-20.0),
            Some(-30.0)
        ]
    );
}

/// Э-08 (T-38): группы выходов (`--exit-group on`) и пропуск шагов удержания (`--hold-step skip`) дают те же
/// `rounds.csv`, `forms.csv`, `signals.csv` (и тот же порядок строк), что прежний счёт (`off` / `poll`), —
/// на сетке с разными стопами, тейками и дедлайнами при одном входе (условия Судьи 1 и 2 к Э-08).
#[test]
fn exit_groups_and_hold_skip_match_the_plain_run_byte_for_byte() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let run = |group: &str, hold: &str, out: &str| {
        let mut a = args(dir.path(), false);
        a.busy_skip = "off".to_string();
        a.stop_form = vec!["s1".to_string(), "s2".to_string(), "pct1".to_string()];
        a.take_form = vec!["t1".to_string(), "1to1".to_string()];
        a.deadline_secs = vec![60, 600];
        a.exit_group = group.to_string();
        a.hold_step = hold.to_string();
        a.out_dir = dir.path().join(out);
        run_bounce_grid(&a).unwrap()
    };
    let base = run("off", "poll", "plain");
    assert!(base.rounds > 0, "фикстура обязана дать круги");
    for (group, hold, out) in [
        ("on", "poll", "g-poll"),
        ("on", "skip", "g-skip"),
        ("off", "skip", "skip"),
    ] {
        let before =
            crate::lob::backtest::EXIT_GROUP_ROUNDS.load(std::sync::atomic::Ordering::Relaxed);
        let got = run(group, hold, out);
        if group == "on" {
            assert!(
                crate::lob::backtest::EXIT_GROUP_ROUNDS.load(std::sync::atomic::Ordering::Relaxed)
                    > before,
                "группы обязаны сработать"
            );
        }
        for name in ["rounds.csv", "forms.csv", "signals.csv"] {
            let body = |p: &std::path::Path| -> String {
                std::fs::read_to_string(p.parent().unwrap().join(name))
                    .unwrap()
                    .lines()
                    .filter(|l| !l.starts_with('#'))
                    .collect::<Vec<_>>()
                    .join("\n")
            };
            assert_eq!(
                body(&got.rounds_path),
                body(&base.rounds_path),
                "{group}/{hold}: {name}"
            );
        }
    }
}

/// TK-049 (`ALPHA_ADMIT_SOA`): `admits` по строкам `AdmitRow` даёт те же ответы, что по записям касаний.
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
        let mut f = TouchFilter::from_set(&set, mode, 0.01, 1.0, &[]);
        let want: Vec<bool> = touches
            .iter()
            .enumerate()
            .map(|(i, t)| f.admits(i, t))
            .collect();
        f.holds = Some(&holds);
        f.rows = Some(&rows);
        let got: Vec<bool> = touches
            .iter()
            .enumerate()
            .map(|(i, t)| f.admits(i, t))
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
    let f = super::sets::TouchFilter::from_set(&set, mode, 0.01, 1.0, &[]);
    let t = |depth: i64, stack: u32| TouchRecord {
        size_at_touch: 10,
        depth_behind_lots: depth,
        stack_levels: stack,
        ..probe_touch()
    };
    assert!(
        f.admits(0, &t(15, 2)),
        "15 × 100 == 150 × 10, стопка 2 == 2 — проходит"
    );
    assert!(!f.admits(0, &t(14, 2)), "позади меньше порога");
    assert!(!f.admits(0, &t(15, 1)), "стопка меньше порога");
    let none = FilterSet::parse("n:").unwrap();
    let f = super::sets::TouchFilter::from_set(&none, mode, 0.01, 1.0, &[]);
    assert!(f.admits(0, &t(0, 0)), "без ключей фильтр прежний");

    assert_eq!(super::plan::g07_label(&none), "");
    assert_eq!(super::plan::g07_label(&set), " behind_min=150 stack_min=2");
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
    let mode = crate::lob::levels::H3Mode::Floor { h3_lots: 1 };
    let set = FilterSet::parse("g:r1_obi1_bp_min=500,r1_obi1_bp_max=900,r1_cancel_60m_lots_min=7")
        .unwrap();
    let f = TouchFilter::from_set(&set, mode, 0.01, 1.0, &[]);
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
    let f = TouchFilter::from_set(&low, mode, 0.01, 1.0, &[]);
    assert!(f.admits_r1(Some(&rec(0, 0))));
    assert!(!f.admits_r1(Some(&rec(R1_UNDEF, 0))));
    let none = FilterSet::parse("n:").unwrap();
    let f = TouchFilter::from_set(&none, mode, 0.01, 1.0, &[]);
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
        ExitForm::HalfStop { q4: 2 }
    );
    assert_eq!(
        ExitForm::parse("halfstopf1").unwrap(),
        ExitForm::HalfStop { q4: 1 }
    );
    assert_eq!(
        ExitForm::parse("halflevelf3").unwrap(),
        ExitForm::HalfLevel { q4: 3 }
    );
    assert_eq!(ExitForm::HalfLevel { q4: 3 }.label(), "halflevelf3");
    assert!(ExitForm::parse("halfstopf2").is_err());
    assert!(ExitForm::parse("halfstopf4").is_err());
}

/// TK-115 (Г-106): `nostop<X2>` — X2 ∈ {1, 2, 4}; другое — отказ.
#[cfg(feature = "r2")]
#[test]
fn nostop_form_parses_only_the_grid_multipliers() {
    assert_eq!(
        ExitForm::parse("nostop2").unwrap(),
        ExitForm::NoStop { x2: 2 }
    );
    assert_eq!(ExitForm::NoStop { x2: 4 }.label(), "nostop4");
    assert!(ExitForm::parse("nostop3").is_err());
    assert!(ExitForm::parse("nostop").is_err());
}
