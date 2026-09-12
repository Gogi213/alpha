use super::*;
use std::path::Path;

use crate::commands::lob::test_support::{delta_frame, snap_frame, write_day};
use crate::commands::lob::H3ModeArg;
use crate::lob::runs::{read_run_rows, RunKind};
use crate::lob::touch_axes::{touch_grid_size, AGE_LABELS, TOUCH_AXES};

const SYMBOL: &str = "ZECUSDT";

/// Пул с полом `h3_lots = 5` — как у фикстур `profiles`.
fn write_instruments_csv(root: &Path, symbols: &[(&str, i64)]) {
    let mut text =
        String::from("symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n");
    for (symbol, h3_lots) in symbols {
        text.push_str(&format!("{symbol},0.01,0.1,0.1,5,{h3_lots}\n"));
    }
    std::fs::write(crate::commands::record::instruments_csv_path(root), text).unwrap();
}

/// Одна сессия одних суток: `session.json`, датированный бинлог, маркер
/// сверки по флагу. Раскладка — та, что ждёт `session_parts_for`.
fn write_session_dir(dir: &Path, day: &str, verified: bool, frames: &[Vec<crate::binlog::Record>]) {
    std::fs::create_dir_all(dir).unwrap();
    let json = format!(
        "{{\"started_utc\":\"{day}T02:00:00Z\",\"start_hour_utc\":2,\
         \"instruments\":[\"{SYMBOL}\"]}}"
    );
    std::fs::write(dir.join("session.json"), json).unwrap();
    write_day(dir, SYMBOL, day, frames);
    if verified {
        std::fs::write(dir.join(format!("verify-{SYMBOL}.status")), "ok").unwrap();
    }
}

/// Файл предрегистрации окна `first..last` — тот же формат, что у
/// `profiles`/`shortlist` (`load_or_write_window`).
fn write_preregistration(root: &Path, first: &str, last: &str) -> PathBuf {
    let path = root.join("preregistration.md");
    std::fs::write(
        &path,
        format!("exploratory: {first}\nconfirmatory: {last}\n"),
    )
    .unwrap();
    path
}

/// Четыре касания по 500 мс — обе стороны, оба исхода, все с уровнем,
/// жившим до кадра (В-43); короче секунды, чтобы горизонт 1 с был **вне**
/// касания (В-45 (2): горизонт 100 мс — внутри, `m_100ms` в таблице
/// `none`). Бид 100 снят на 1000 мс → бид 99 стал лучшим (касание 0, база
/// — середина 102 как есть); на 1500 мс 100 родился заново лучшей ценой
/// (не касание) → 99 ушёл с лучшей цены: **отскок**, середина к 2000 мс
/// 102.5 — известный сдвиг `+1 тик / 204` = +49.0196 bps «в сторону
/// отскока». Аск 105 снят на 3000 мс → аск 106 лучший (касание 0), на
/// 3500 мс 105 вернулся → **отскок** аска, середина 103 → 102.5: +48.5437
/// bps. На 5000 мс 100 снят снова → 99 лучший второй раз (индекс 1), на
/// 5500 мс 99 усох до лота — **смерть в касании**, середина не сдвинулась;
/// на 7000 мс 105 снят → 106 лучший второй раз, на 7500 мс усох —
/// **смерть**. Бид 98 и аск 107 касаний не дают.
fn four_touch_frames() -> Vec<Vec<crate::binlog::Record>> {
    vec![
        snap_frame(
            0,
            &[(98, 10), (99, 10), (100, 10)],
            &[(105, 10), (106, 10), (107, 10)],
        ),
        delta_frame(1000, &[(100, 0)], &[]),
        delta_frame(1500, &[(100, 10)], &[]),
        delta_frame(3000, &[], &[(105, 0)]),
        delta_frame(3500, &[], &[(105, 10)]),
        delta_frame(5000, &[(100, 0)], &[]),
        delta_frame(5500, &[(99, 1)], &[]),
        delta_frame(7000, &[], &[(105, 0)]),
        delta_frame(7500, &[], &[(106, 1)]),
    ]
}

fn base_args(root: &Path, out: PathBuf) -> TouchProfilesArgs {
    TouchProfilesArgs {
        root: root.to_path_buf(),
        h3: H3Args {
            h3_mode: H3ModeArg::Floor,
            h3_lots: None,
        },
        h3_k: None,
        warmup_ms: 0,
        repeat_window_ms: crate::commands::lob::DEFAULT_REPEAT_WINDOW_MS,
        allow_unverified: false,
        out: Some(out),
        now_utc: Some("2026-09-13T00:00:00Z".to_string()),
        runs_out: root.join("runs.csv"),
        preregistration: None,
        window_end: None,
    }
}

/// Боевые аргументы: файл предрегистрации окна `first..last`.
fn battle_args(root: &Path, out: PathBuf, first: &str, last: &str) -> TouchProfilesArgs {
    let mut args = base_args(root, out);
    args.preregistration = Some(write_preregistration(root, first, last));
    args
}

type Table = (Vec<String>, std::collections::BTreeMap<String, Vec<String>>);

/// Строки таблицы без комментариев шапки: id → колонки по имени.
fn read_table(path: &Path) -> Table {
    let text = std::fs::read_to_string(path).unwrap();
    let body: String = text
        .lines()
        .filter(|l| !l.starts_with('#'))
        .map(|l| format!("{l}\n"))
        .collect();
    let mut r = csv::Reader::from_reader(body.as_bytes());
    let header: Vec<String> = r.headers().unwrap().iter().map(str::to_string).collect();
    let rows = r
        .records()
        .map(|rec| {
            let cells: Vec<String> = rec.unwrap().iter().map(str::to_string).collect();
            (cells[0].clone(), cells)
        })
        .collect();
    (header, rows)
}

fn cell<'a>(header: &[String], row: &'a [String], name: &str) -> &'a str {
    let i = header
        .iter()
        .position(|h| h == name)
        .unwrap_or_else(|| panic!("нет колонки {name}"));
    &row[i]
}

fn header_lines(path: &Path) -> Vec<String> {
    std::fs::read_to_string(path)
        .unwrap()
        .lines()
        .filter(|l| l.starts_with('#'))
        .map(str::to_string)
        .collect()
}

/// Id строк таблицы с `n > 0` — то, что обязано попасть в журнал.
fn ids_with_touches(table: &Table) -> Vec<String> {
    let (header, rows) = table;
    rows.iter()
        .filter(|(_, row)| cell(header, row, "n") != "0")
        .map(|(id, _)| id.clone())
        .collect()
}

/// Корень с тремя сессиями: двое суток со сверкой и третьи без маркера.
fn three_day_root(root: &Path) {
    write_instruments_csv(root, &[(SYMBOL, 5)]);
    write_session_dir(&root.join("s1"), "2026-09-01", true, &four_touch_frames());
    write_session_dir(&root.join("s2"), "2026-09-02", true, &four_touch_frames());
    write_session_dir(&root.join("s3"), "2026-09-03", false, &four_touch_frames());
}

/// Корень с семью сверенными сутками — `G_MIN` кластеров для интервала.
fn seven_day_root(root: &Path) {
    write_instruments_csv(root, &[(SYMBOL, 5)]);
    for d in 1..=7 {
        write_session_dir(
            &root.join(format!("s{d}")),
            &format!("2026-09-0{d}"),
            true,
            &four_touch_frames(),
        );
    }
}

/// Ревью T37 (BLOCKING): боевой режим без `--preregistration` — отказ, как у
/// `profiles`; ни таблицы, ни журнала.
#[test]
fn battle_mode_without_preregistration_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    three_day_root(root);
    let out = root.join("touch-profiles.csv");
    let args = base_args(root, out.clone());
    let err = run_touch_profiles(&args).expect_err("без предрегистрации боевой прогон запрещён");
    assert!(
        err.to_string()
            .contains("--preregistration обязателен без --allow-unverified"),
        "{err}"
    );
    assert!(!out.exists(), "таблица не пишется");
    assert!(!args.runs_out.exists(), "журнал не пишется");
}

/// Критерий приёмки 1: фикстура двух суток с касаниями обеих сторон и
/// обоих исходов → строка на каждый id сетки (`touch_grid_size`), `n` по
/// корзинам сходится с числом касаний, тотальные оси суммируются в него;
/// сутки без `verify ok` не читаются (`n_days = 2`, не 3). Ревью T37: в
/// `runs.csv` — только корзины с `n > 0` (второй инструмент пула без
/// сессии не даёт ни строки — В-18), шапка печатает их число; при
/// `n_days < G_MIN` границы интервала — `none`; колонка `touch_hours_utc`.
#[test]
fn two_verified_days_fill_marginals_and_cross_and_the_unverified_day_is_not_read() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    three_day_root(root);
    // Второй инструмент в пуле без единой сессии: строки в таблице есть,
    // испытаний — нет.
    write_instruments_csv(root, &[(SYMBOL, 5), ("XRPUSDT", 5)]);
    let out = root.join("touch-profiles.csv");
    let args = battle_args(root, out.clone(), "2026-09-01", "2026-09-03");
    let summary = run_touch_profiles(&args).unwrap();
    assert_eq!(summary.rows, touch_grid_size(2), "32 × (2 + 1) строки");
    assert_eq!(summary.touches, 8, "4 касания × 2 сверенных суток");
    assert!(!summary.debug);

    let table = read_table(&out);
    let (header, rows) = &table;
    assert_eq!(*header, HEADER.map(str::to_string).to_vec());
    assert!(header.contains(&"touch_hours_utc".to_string()));
    assert!(!header.contains(&"level_hours_utc".to_string()));
    assert_eq!(rows.len(), touch_grid_size(2));
    let n = |id: &str| -> u64 {
        cell(
            header,
            rows.get(id).unwrap_or_else(|| panic!("нет строки {id}")),
            "n",
        )
        .parse()
        .unwrap()
    };
    for scope in ["pool", SYMBOL] {
        assert_eq!(n(&touch_marginal_id(scope, "side", "bid")), 4, "{scope}");
        assert_eq!(n(&touch_marginal_id(scope, "side", "ask")), 4, "{scope}");
        assert_eq!(n(&touch_marginal_id(scope, "outcome", "bounced")), 4);
        assert_eq!(n(&touch_marginal_id(scope, "outcome", "eaten")), 4);
        assert_eq!(n(&touch_marginal_id(scope, "age", "[0,10m)")), 8);
        assert_eq!(
            n(&touch_marginal_id(scope, "index", "1")),
            4,
            "первые касания"
        );
        assert_eq!(
            n(&touch_marginal_id(scope, "index", "2-3")),
            4,
            "вторые касания"
        );
        assert_eq!(
            n(&touch_marginal_id(scope, "frontrun", "[0.5,inf)")),
            8,
            "10 лотов на 10 (кадры реже секунды: за секунду до касания то же)"
        );
        assert_eq!(
            n(&touch_marginal_id(scope, "size", "[2,4)")),
            8,
            "10 лотов / пол 5"
        );
        assert_eq!(
            n(&touch_marginal_id(scope, "duration", "[0,1s)")),
            8,
            "касания по 500 мс"
        );
        assert_eq!(n(&touch_marginal_id(scope, "round", "0")), 8);
        assert_eq!(n(&touch_cross_id(scope, "bounced", "[0,10m)")), 4);
        assert_eq!(n(&touch_cross_id(scope, "eaten", "[0,10m)")), 4);
        assert_eq!(n(&touch_cross_id(scope, "bounced", "[10m,1h)")), 0);
        // Тотальные оси (сторона, круглость, номер, исход, возраст) и крест
        // дают в сумме все касания; частичные — не больше.
        for (axis, labels) in TOUCH_AXES {
            let total: u64 = labels
                .iter()
                .map(|l| n(&touch_marginal_id(scope, axis, l)))
                .sum();
            match axis {
                "side" | "round" | "index" | "outcome" | "age" => {
                    assert_eq!(total, 8, "ось {axis} тотальна");
                }
                _ => assert!(total <= 8, "ось {axis}: {total}"),
            }
        }
        let cross_total: u64 = ["bounced", "eaten"]
            .iter()
            .flat_map(|o| AGE_LABELS.iter().map(move |a| touch_cross_id(scope, o, a)))
            .map(|id| n(&id))
            .sum();
        assert_eq!(cross_total, 8);
    }
    for (axis, labels) in TOUCH_AXES {
        for l in labels.iter() {
            assert_eq!(
                n(&touch_marginal_id("XRPUSDT", axis, l)),
                0,
                "инструмент без сессии — нули по всей области"
            );
        }
    }
    let bid = rows.get(&touch_marginal_id("pool", "side", "bid")).unwrap();
    assert_eq!(cell(header, bid, "share_bounced"), "0.500000");
    assert_eq!(
        cell(header, bid, "n_days"),
        "2",
        "третьи сутки без verify ok не читаются"
    );
    assert_eq!(
        cell(header, bid, "touch_hours_utc"),
        "0",
        "касания — в час 0 UTC (метки от эпохи)"
    );
    // Двое суток < G_MIN: точка есть, границ нет — число на двух кластерах
    // было бы арифметикой весов, не данными.
    let m: f64 = cell(header, bid, "m_1000ms").parse().unwrap();
    assert!((m - 10_000.0 / 204.0 / 2.0).abs() < 1e-3, "m_1000ms = {m}");
    assert_eq!(cell(header, bid, "m_1000ms_lower"), "none");
    assert_eq!(cell(header, bid, "m_1000ms_upper"), "none");
    assert_eq!(cell(header, bid, "m_10000ms_lower"), "none");
    let empty = rows
        .get(&touch_cross_id("pool", "bounced", "[1h,inf)"))
        .unwrap();
    assert_eq!(cell(header, empty, "n"), "0");
    assert_eq!(cell(header, empty, "share_bounced"), "none");
    assert_eq!(cell(header, empty, "m_1000ms"), "none");
    assert_eq!(cell(header, empty, "n_days"), "0");

    // Журнал испытаний: только корзины с наблюдениями, все подтверждающие,
    // повтор — вторая партия.
    let with_touches = ids_with_touches(&table);
    assert!(
        with_touches.len() < touch_grid_size(2),
        "часть сетки пуста по построению фикстуры"
    );
    assert!(with_touches.iter().all(|id| !id.starts_with("XRPUSDT:")));
    assert_eq!(summary.trials, with_touches.len());
    let lines = header_lines(&out);
    assert!(
        lines[0].starts_with("# lob touch-profiles: h3_mode=floor warmup_ms=0 repeat_window_ms="),
        "{}",
        lines[0]
    );
    assert!(
        lines[0].contains(" alpha=0.005 replications=9999 seed=0 bounds=lower/upper one-sided alpha each, none below g_min=7 days"),
        "{}",
        lines[0]
    );
    assert!(
        lines[0].ends_with(&format!(" trials={}", with_touches.len())),
        "{}",
        lines[0]
    );
    assert!(!lines[0].contains("debug"));
    assert!(
        !lines.iter().any(|l| l.contains("rtt=")),
        "RTT в markout не входит"
    );
    assert!(
        lines[1].starts_with(
            "# window: 2026-09-01..2026-09-03 days=3 sessions=3 sessions_outside_window=0"
        ),
        "{}",
        lines[1]
    );
    let runs = read_run_rows(&args.runs_out).unwrap();
    assert_eq!(runs.len(), with_touches.len());
    assert!(runs.iter().all(|r| r.kind == RunKind::Confirmatory
        && r.detail.starts_with("touch_profile ")
        && r.ts_utc == "2026-09-13T00:00:00Z"));
    let ids: Vec<String> = runs
        .iter()
        .map(|r| r.detail.trim_start_matches("touch_profile ").to_string())
        .collect();
    assert_eq!(ids, with_touches);
    run_touch_profiles(&args).unwrap();
    assert_eq!(
        read_run_rows(&args.runs_out).unwrap().len(),
        2 * with_touches.len(),
        "журнал, не перезапись"
    );
}

/// Критерий приёмки 2 на `G_MIN` кластерах: известный сдвиг середины после
/// касания — отскок бида 99 с базы 102 (как есть на `start_ms`, В-43) к
/// 102.5 через 1 с: `m_1000ms = +1 тик / 204 × 10⁴ = 49.0196 bps`; интервал
/// по семи суткам-кластерам держит точку внутри; `n_days = 7`. Горизонт
/// 100 мс — внутри касания в 500 мс: `m_100ms` и границы — `none` (В-45
/// (2)), хотя срез есть. Ноль у проеденных печатается `0.000000`, не
/// `-0.000000` — и в верхней границе.
#[test]
fn interval_brackets_the_known_mid_shift_over_seven_day_clusters() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    seven_day_root(root);
    let out = root.join("touch-profiles.csv");
    let args = battle_args(root, out.clone(), "2026-09-01", "2026-09-07");
    let summary = run_touch_profiles(&args).unwrap();
    assert_eq!(summary.touches, 28);
    let (header, rows) = read_table(&out);

    let bid = rows.get(&touch_marginal_id("pool", "side", "bid")).unwrap();
    // Бид: отскок +49.0196 и смерть 0 → среднее 24.5098.
    let m: f64 = cell(&header, bid, "m_1000ms").parse().unwrap();
    assert!((m - 10_000.0 / 204.0 / 2.0).abs() < 1e-3, "m_1000ms = {m}");
    let lower: f64 = cell(&header, bid, "m_1000ms_lower").parse().unwrap();
    let upper: f64 = cell(&header, bid, "m_1000ms_upper").parse().unwrap();
    assert!(lower < m && m < upper, "{lower} < {m} < {upper}");
    assert_eq!(cell(&header, bid, "n_days"), "7");
    assert_eq!(
        cell(&header, bid, "m_100ms"),
        "none",
        "100 мс внутри касания в 500 мс — не наблюдение"
    );
    assert_eq!(cell(&header, bid, "m_100ms_lower"), "none");

    let bounced = rows
        .get(&touch_marginal_id("pool", "outcome", "bounced"))
        .unwrap();
    let m: f64 = cell(&header, bounced, "m_1000ms").parse().unwrap();
    let expected = (10_000.0 / 204.0 + 10_000.0 / 206.0) / 2.0;
    assert!((m - expected).abs() < 1e-3, "бид +49.02, аск +48.54: {m}");

    let eaten = rows
        .get(&touch_marginal_id("pool", "outcome", "eaten"))
        .unwrap();
    assert_eq!(
        cell(&header, eaten, "m_1000ms"),
        "0.000000",
        "усохший уровень остался лучшей ценой"
    );
    assert_eq!(cell(&header, eaten, "m_1000ms_lower"), "0.000000");
    assert_eq!(
        cell(&header, eaten, "m_1000ms_upper"),
        "0.000000",
        "не -0.000000"
    );

    // Детерминизм: тот же файл байт-в-байт при повторе (seed в шапке).
    let first = std::fs::read_to_string(&out).unwrap();
    run_touch_profiles(&args).unwrap();
    assert_eq!(first, std::fs::read_to_string(&out).unwrap());
}

/// `--root` — сам каталог сессии (раскладка `data/session-debug/t34`,
/// `data/always-on/<ts>`): читается как одна сессия, пул — из его
/// `instruments.csv`. Отладочный вход: предрегистрации не нужно, шапка
/// печатает «not preregistered» с диапазоном.
#[test]
fn root_that_is_itself_a_session_directory_is_read_as_one_session() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_instruments_csv(root, &[(SYMBOL, 5)]);
    write_session_dir(root, "2026-09-12", false, &four_touch_frames());
    let out = root.join("touch-profiles.csv");
    let mut args = base_args(root, out.clone());
    args.allow_unverified = true;
    let summary = run_touch_profiles(&args).unwrap();
    assert_eq!(summary.touches, 4);
    let (header, rows) = read_table(&out);
    let all = rows
        .get(&touch_marginal_id(SYMBOL, "age", "[0,10m)"))
        .unwrap();
    assert_eq!(cell(&header, all, "n"), "4");
    assert_eq!(cell(&header, all, "n_days"), "1");
    assert_eq!(
        header_lines(&out)[1],
        "# window: not preregistered 2026-09-12..2026-09-12 days=1 sessions=1"
    );
}

/// `--allow-unverified`: сутки без маркера читаются, шапка несёт `debug` и
/// `trials=0`, `runs.csv` не пишется (данными не является); файл
/// предрегистрации, даже если назван, окно не сужает — как у `profiles`.
#[test]
fn allow_unverified_reads_unmarked_days_marks_debug_and_skips_runs_csv() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    three_day_root(root);
    let out = root.join("touch-profiles.csv");
    let mut args = battle_args(root, out.clone(), "2026-09-01", "2026-09-01");
    args.allow_unverified = true;
    let summary = run_touch_profiles(&args).unwrap();
    assert!(summary.debug);
    assert_eq!(summary.touches, 12, "все трое суток, окно не сужается");
    assert_eq!(summary.trials, 0);
    assert!(!args.runs_out.exists(), "отладка журнал не трогает");
    let (header, rows) = read_table(&out);
    let bid = rows.get(&touch_marginal_id("pool", "side", "bid")).unwrap();
    assert_eq!(cell(&header, bid, "n_days"), "3");
    let lines = header_lines(&out);
    assert!(lines[0].ends_with(" trials=0 debug"), "{}", lines[0]);
    assert_eq!(
        lines[1],
        "# window: not preregistered 2026-09-01..2026-09-03 days=3 sessions=3"
    );
}

/// `--preregistration`: окно — тот же файл, что у `profiles`; сутки вне
/// окна в таблицу не входят, шапка печатает `format_window_line`; в
/// журнал идут только корзины с касаниями окна.
#[test]
fn preregistered_window_excludes_days_outside_it() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    three_day_root(root);
    let out = root.join("touch-profiles.csv");
    let args = battle_args(root, out.clone(), "2026-09-01", "2026-09-01");
    let summary = run_touch_profiles(&args).unwrap();
    assert_eq!(summary.touches, 4, "только 2026-09-01");
    let table = read_table(&out);
    let (header, rows) = &table;
    let bid = rows.get(&touch_marginal_id("pool", "side", "bid")).unwrap();
    assert_eq!(cell(header, bid, "n"), "2");
    assert_eq!(cell(header, bid, "n_days"), "1");
    let line = &header_lines(&out)[1];
    assert!(
        line.starts_with(
            "# window: 2026-09-01..2026-09-01 days=1 sessions=1 sessions_outside_window=2"
        ),
        "{line}"
    );
    let runs = read_run_rows(&args.runs_out).unwrap();
    assert_eq!(runs.len(), ids_with_touches(&table).len());
    assert_eq!(summary.trials, runs.len());
}
