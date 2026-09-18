use super::*;
use crate::commands::lob::H3ModeArg;

fn write_instruments_csv(root: &Path, symbols: &[&str]) {
    let mut text =
        String::from("symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n");
    for symbol in symbols {
        text.push_str(&format!("{symbol},0.01,0.1,0.1,5,5\n"));
    }
    std::fs::write(instruments_csv_path(root), text).unwrap();
}

fn write_candidates_csv(path: &Path, rows: &[(&str, f64)]) {
    let mut text = String::from("symbol,coverage_top50_bps\n");
    for (symbol, coverage) in rows {
        text.push_str(&format!("{symbol},{coverage}\n"));
    }
    std::fs::write(path, text).unwrap();
}

fn write_session_dir(root: &Path, session_id: &str, symbol: &str, started_utc: &str) {
    let dir = root.join(session_id);
    std::fs::create_dir_all(&dir).unwrap();
    let json = format!(
        "{{\"started_utc\":\"{started_utc}\",\"start_hour_utc\":2,\"instruments\":[\"{symbol}\"]}}"
    );
    std::fs::write(dir.join("session.json"), json).unwrap();
    let header = crate::binlog::Header {
        tick_e9: super::super::test_support::FIX_TICK_E9,
        step_e9: 1_000_000,
        max_records_per_frame: 4096,
    };
    let frames = super::super::test_support::three_level_frames();
    let mut w = crate::binlog::Writer::create(Vec::new(), header, 1).unwrap();
    for f in &frames {
        w.write_frame(f).unwrap();
    }
    w.flush().unwrap();
    // Таск 19: `lob session` пишет `<SYMBOL>-<день>.binlog`, не
    // `<SYMBOL>.binlog` — эта фикстура течёт через настоящий
    // `profiles::run_profiles_with_fill_model` (`run_profiles_over`),
    // так что раскладка обязана совпасть с тем, что ждёт
    // `session_binlog_for`.
    let day = &started_utc[..10];
    std::fs::write(dir.join(format!("{symbol}-{day}.binlog")), w.into_inner()).unwrap();
    std::fs::write(dir.join(format!("verify-{symbol}.status")), "ok").unwrap();
}

/// Таск 23: каталог с частями за двое суток стоит под обеими датами
/// календаря (до таска — только под `started_utc` своего `session.json`,
/// то есть под последней сессией), а во времянку линкуется один раз.
#[test]
fn group_by_day_lists_a_two_day_directory_under_both_days_and_links_it_once() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    // Каталог за 01 мая по `session.json`, но с частью и за 02 мая рядом.
    write_session_dir(root, "collect", "SOLUSDT", "2026-05-01T02:00:00Z");
    std::fs::write(
        root.join("collect").join("SOLUSDT-2026-05-02.binlog"),
        std::fs::read(root.join("collect").join("SOLUSDT-2026-05-01.binlog")).unwrap(),
    )
    .unwrap();
    write_session_dir(root, "single", "SOLUSDT", "2026-05-03T02:00:00Z");
    let dirs = session_dirs(root).unwrap();
    let by_day = group_by_day(&dirs);
    let view: Vec<(String, Vec<String>)> = by_day
        .iter()
        .map(|(d, ds)| {
            (
                d.clone(),
                ds.iter()
                    .map(|p| p.file_name().unwrap().to_string_lossy().into_owned())
                    .collect(),
            )
        })
        .collect();
    assert_eq!(
        view,
        vec![
            ("2026-05-01".to_string(), vec!["collect".to_string()]),
            ("2026-05-02".to_string(), vec!["collect".to_string()]),
            ("2026-05-03".to_string(), vec!["single".to_string()]),
        ]
    );

    let scratch = tempfile::tempdir().unwrap();
    let instruments = root.join("instruments.csv");
    std::fs::write(&instruments, "symbol\nSOLUSDT\n").unwrap();
    build_filtered_root(
        scratch.path(),
        &instruments,
        &by_day,
        &["2026-05-01".to_string(), "2026-05-02".to_string()],
    )
    .expect("каталог под двумя сутками линкуется один раз, не падает на повторе");
    assert!(scratch
        .path()
        .join("collect")
        .join("session.json")
        .is_file());
    assert!(
        !scratch.path().join("single").exists(),
        "03 мая не запрашивали"
    );
}

fn base_args(root: &Path, candidates_csv: PathBuf) -> ShortlistArgs {
    ShortlistArgs {
        root: root.to_path_buf(),
        candidates_csv,
        h3: H3Args {
            h3_mode: H3ModeArg::Floor,
            h3_lots: None,
            h3_usd: None,
            h3_strength_pct: None,
            h3_strength_window_bps: None,
        },
        warmup_ms: 0,
        repeat_window_ms: super::super::DEFAULT_REPEAT_WINDOW_MS,
        allow_unverified: false,
        out: Some(root.join("shortlist.md")),
        now_utc: Some("2026-05-10T00:00:00Z".to_string()),
        runs_out: root.join("runs.csv"),
        preregistration: root.join("preregistration.md"),
        freeze_out: root.join("shortlist-frozen.txt"),
        freeze_commit: Some("deadbeef".to_string()),
        execution: ExecutionArgs {
            median_rtt_ns: None,
            p95_rtt_ns: None,
            order_qty_e9: None,
        },
    }
}

/// Механизм отладки (п.5 таска): один период, `runs.csv` не трогается,
/// подтверждающая пропущена, отчёт помечен `debug`.
#[test]
fn debug_run_skips_split_and_does_not_touch_runs_csv() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_instruments_csv(root, &["SOLUSDT"]);
    let candidates_csv = root.join("candidates.csv");
    write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);
    write_session_dir(
        root,
        "2026-05-01T020000Z",
        "SOLUSDT",
        "2026-05-01T02:00:00Z",
    );

    let mut args = base_args(root, candidates_csv);
    args.allow_unverified = true;
    args.out = Some(root.join("shortlist-debug.md"));

    let summary = run_shortlist(&args).expect("отладочный прогон");
    assert!(summary.debug);
    assert!(summary.verdict.is_none());
    let text = std::fs::read_to_string(&summary.out).unwrap();
    assert!(text.contains("debug"), "{text}");
    assert!(text.contains("confirmatory: пропущена"), "{text}");
    assert!(!args.runs_out.exists(), "отладка не пишет runs.csv");
    assert!(
        !args.preregistration.exists(),
        "отладка не пишет предрегистрацию"
    );
}

/// Механизм п.1–2: граница пишется один раз до анализа и переиспользуется
/// — третьи сутки, добавленные после первой записи, не двигают уже
/// зафиксированную границу. Механизм п.4: заморозка пишет файл, который
/// читает `require_shortlist_member`, и требует `--freeze-commit`.
#[test]
fn production_run_freezes_boundary_once_and_writes_frozen_list() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_instruments_csv(root, &["SOLUSDT"]);
    let candidates_csv = root.join("candidates.csv");
    write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);
    write_session_dir(
        root,
        "2026-05-01T020000Z",
        "SOLUSDT",
        "2026-05-01T02:00:00Z",
    );
    write_session_dir(
        root,
        "2026-05-02T020000Z",
        "SOLUSDT",
        "2026-05-02T02:00:00Z",
    );

    let args = base_args(root, candidates_csv.clone());
    let summary = run_shortlist(&args).expect("первый боевой прогон");
    assert!(!summary.debug);
    assert!(summary.verdict.is_some());
    assert!(args.runs_out.exists(), "боевой режим пишет runs.csv");
    let rows_after_first = crate::lob::runs::read_run_rows(&args.runs_out)
        .unwrap()
        .len();
    assert_eq!(
        rows_after_first, summary.trials,
        "по строке журнала на каждый id сетки"
    );

    // Таск 29: суток меньше, чем блоков перебора, — шапка обязана
    // сказать, чего именно не хватило, а не напечатать немой `none`.
    let text = std::fs::read_to_string(&summary.out).unwrap();
    assert!(text.contains("pbo=n/a (days=2 < 8)"), "{text}");

    let boundary_text = std::fs::read_to_string(&args.preregistration).unwrap();
    assert!(
        boundary_text.contains("exploratory: 2026-05-01"),
        "{boundary_text}"
    );
    assert!(
        boundary_text.contains("confirmatory: 2026-05-02"),
        "{boundary_text}"
    );

    assert!(args.freeze_out.exists(), "заморозка пишет файл списка");
    let frozen_text = std::fs::read_to_string(&args.freeze_out).unwrap();
    assert!(
        frozen_text.contains("freeze_commit: deadbeef"),
        "{frozen_text}"
    );

    // Третьи сутки не должны сдвинуть уже зафиксированную границу.
    write_session_dir(
        root,
        "2026-05-03T020000Z",
        "SOLUSDT",
        "2026-05-03T02:00:00Z",
    );
    let summary2 = run_shortlist(&args).expect("второй прогон после новых суток");
    let boundary_text2 = std::fs::read_to_string(&args.preregistration).unwrap();
    assert_eq!(
        boundary_text, boundary_text2,
        "граница зафиксирована — новые сутки её не двигают"
    );
    let rows_after_second = crate::lob::runs::read_run_rows(&args.runs_out)
        .unwrap()
        .len();
    assert_eq!(
        rows_after_second,
        2 * summary.trials,
        "второй боевой прогон — ещё по строке на id сетки (append, не перезапись)"
    );
    // `trials` теперь идёт из фактических строк `runs.csv` (R47), а не
    // из номинала сетки: журнал копится через прогоны (append, не
    // перезапись), поэтому второй прогон честно видит вдвое больше
    // испытаний, чем первый — то самое «поправка по фактическому N»,
    // которое требует ticket 13, а не постоянное число от состава пула.
    assert_eq!(summary2.trials, 2 * summary.trials);
}

/// Без `--freeze-commit` боевой прогон обязан отказать: изобретать
/// коммит самому запрещено (§9).
#[test]
fn production_run_requires_freeze_commit() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_instruments_csv(root, &["SOLUSDT"]);
    let candidates_csv = root.join("candidates.csv");
    write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);
    write_session_dir(
        root,
        "2026-05-01T020000Z",
        "SOLUSDT",
        "2026-05-01T02:00:00Z",
    );
    write_session_dir(
        root,
        "2026-05-02T020000Z",
        "SOLUSDT",
        "2026-05-02T02:00:00Z",
    );
    let mut args = base_args(root, candidates_csv);
    args.freeze_commit = None;
    assert!(run_shortlist(&args).is_err());
}

/// Механизм заморозки как проверяет `lob markout --confirmatory`:
/// профиль вне списка — отказ; профиль из списка — проходит; без файла —
/// отказ (заморозка обязана предшествовать).
#[test]
fn require_shortlist_member_gates_by_frozen_file() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("shortlist-frozen.txt");
    assert!(require_shortlist_member(&path, "marginal:side=bid").is_err());
    std::fs::write(
        &path,
        "# freeze_commit: abc\nmarginal:side=bid\ncross:SOLUSDT|pulled|[0,1)\n",
    )
    .unwrap();
    assert!(require_shortlist_member(&path, "marginal:side=bid").is_ok());
    assert!(require_shortlist_member(&path, "marginal:side=ask").is_err());
}

/// Таск 29 насквозь: боевой прогон на восьми сутках строит матрицу
/// «испытания × периоды» и печатает **числом** PBO (суток ровно столько
/// же, сколько блоков полного перебора) — заглушки `None` тасков 13/16
/// в шапке больше нет, а форма матрицы и правило пустой ячейки в шапке
/// названы. CPCV на том же ряду: суток больше четырёх, отказать «по
/// нехватке суток» ему нечем.
#[test]
fn production_run_prints_pbo_number_and_matrix_shape_on_eight_days() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_instruments_csv(root, &["SOLUSDT"]);
    let candidates_csv = root.join("candidates.csv");
    write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);
    for d in 1..=8 {
        write_session_dir(
            root,
            &format!("2026-05-{d:02}T020000Z"),
            "SOLUSDT",
            &format!("2026-05-{d:02}T02:00:00Z"),
        );
    }
    let args = base_args(root, candidates_csv);
    let summary = run_shortlist(&args).expect("боевой прогон на восьми сутках");
    let text = std::fs::read_to_string(&summary.out).unwrap();
    assert!(
        text.contains("pbo_matrix: rows=8 days=8 excluded_nan_rows=27 cell=net_bps"),
        "форма матрицы, снятые по NaN строки и колонка ячейки — в шапке: {text}"
    );
    // Фикстура кладёт один и тот же бинлог во все восемь суток, поэтому
    // у дошедших до оценщиков испытаний `net` по суткам постоянен: OOS
    // без дисперсии — Sharpe не считается ни на одной складке, и отказ
    // обязан назвать именно это, а не «мало суток».
    assert!(
        text.contains("cpcv_oos_sharpe=n/a (матрица 8×8: ни одна складка не посчиталась)"),
        "{text}"
    );
    let line = text
        .lines()
        .find(|l| l.starts_with("dsr_target:"))
        .unwrap_or_default();
    let pbo: f64 = line
        .split("pbo=")
        .nth(1)
        .and_then(|t| t.split_whitespace().next())
        .and_then(|t| t.parse().ok())
        .unwrap_or_else(|| panic!("восемь суток — PBO обязан быть числом: {line}"));
    assert!(
        (0.0..=1.0).contains(&pbo),
        "PBO — вероятность, не что попало: {line}"
    );
    assert!(text.contains("pbo_gate: none"), "{text}");
}

/// Таск 29, известный ответ, посчитанный руками, а не кодом: испытание
/// с постоянным `net > 0` на всех восьми сутках даёт нулевую дисперсию
/// и, значит, `+∞` на любом наборе блоков — оно выигрывает in-sample
/// каждый из 35 сплитов `C(8,4)/2` и на дополнении тоже `+∞`, то есть
/// строго лучше обоих конечных соперников. Его относительный ранг —
/// `(2 + 0.5) / 3 = 0.83`, ниже медианы он не опускается ни разу, и
/// PBO обязан быть ровно нулём. Суток меньше числа блоков — не ноль, а
/// названная причина.
#[test]
fn pbo_of_matrix_is_zero_when_one_trial_is_steadily_positive() {
    let ids = vec!["A".to_string(), "B".to_string(), "C".to_string()];
    let steady = vec![1.0; 8];
    let rising: Vec<f64> = (1..=8).map(f64::from).collect();
    let zigzag = vec![8.0, -7.0, 6.0, -5.0, 4.0, -3.0, 2.0, -1.0];
    let m = TrialDayMatrix {
        days: (1..=8).map(|d| format!("2026-05-{d:02}")).collect(),
        ids: ids.clone(),
        rows: vec![steady, rising, zigzag.clone()],
    };
    let (pbo, why) = pbo_of_matrix(&m);
    assert_eq!(pbo, Some(0.0), "{why:?}");
    assert!(why.is_none());

    let short = TrialDayMatrix {
        days: (1..=4).map(|d| format!("2026-05-{d:02}")).collect(),
        ids,
        rows: vec![vec![1.0; 4], vec![1.0, 2.0, 3.0, 4.0], zigzag[..4].to_vec()],
    };
    let (pbo, why) = pbo_of_matrix(&short);
    assert!(pbo.is_none());
    assert_eq!(why.as_deref(), Some("days=4 < 8"));
}

/// Таск 29: CPCV проверяет **процедуру отбора** (§6.4 п. 4) и
/// отказывает с названной целиком причиной. Известный ответ выведен
/// руками: правило берёт в обеих складках строку `[1,2,1,2,…]`
/// (среднее 1.5 против нуля), её OOS-половина — `[1,2,1,2]`, среднее
/// 1.5 при отклонении 0.5, то есть Sharpe ровно 3.0 в каждой складке,
/// и среднее по складкам — тоже ровно 3.0.
#[test]
fn cpcv_of_matrix_names_why_it_refuses_and_scores_the_selection() {
    let ids = vec!["A".to_string(), "B".to_string()];
    let short = TrialDayMatrix {
        days: (1..=3).map(|d| format!("2026-05-{d:02}")).collect(),
        ids: ids.clone(),
        rows: vec![vec![1.0, 2.0, 1.0], vec![0.0, 0.0, 0.0]],
    };
    assert_eq!(
        cpcv_of_matrix(&short),
        (None, Some("days=3 < 4".to_string()))
    );

    let alone = TrialDayMatrix {
        days: (1..=8).map(|d| format!("2026-05-{d:02}")).collect(),
        ids: vec!["A".to_string()],
        rows: vec![vec![1.0, 2.0, 1.0, 2.0, 1.0, 2.0, 1.0, 2.0]],
    };
    assert_eq!(
        cpcv_of_matrix(&alone),
        (None, Some("rows=1 < 2 после исключения NaN".to_string())),
        "отбирать не из чего — это не «нулевой эффект»"
    );

    let scored = TrialDayMatrix {
        days: (1..=8).map(|d| format!("2026-05-{d:02}")).collect(),
        ids,
        rows: vec![vec![1.0, 2.0, 1.0, 2.0, 1.0, 2.0, 1.0, 2.0], vec![0.0; 8]],
    };
    let (v, why) = cpcv_of_matrix(&scored);
    assert_eq!(why, None);
    assert!(
        v.is_some_and(|x| (x - 3.0).abs() < 1e-12),
        "ровно 3.0 по обеим складкам: {v:?}"
    );
}

/// Таск 29, ремонт по ревью: испытание с пропущенными сутками снимается
/// **до** расчёта. Иначе `final_metrics::pbo` даёт ему Sharpe ровно
/// `0.0` — и профиль без данных встаёт выше любого убыточного, участвуя
/// в IS-выборе. Известный ответ прежний (стабильно прибыльное
/// испытание даёт PBO ноль), а строка с дырой в него не входит; когда
/// после снятия остаётся одна строка, отказ называет именно это.
#[test]
fn matrix_excludes_trials_with_missing_days_before_scoring() {
    let mut gap = vec![100.0; 8];
    gap[3] = f64::NAN;
    let m = TrialDayMatrix {
        days: (1..=8).map(|d| format!("2026-05-{d:02}")).collect(),
        ids: vec!["gap".to_string(), "A".to_string(), "B".to_string()],
        rows: vec![gap, vec![1.0; 8], (1..=8).map(f64::from).collect()],
    };
    let (scored, excluded) = m.without_nan_rows();
    assert_eq!(excluded, 1);
    assert_eq!(scored.ids, vec!["A".to_string(), "B".to_string()]);
    assert_eq!(pbo_of_matrix(&scored), (Some(0.0), None));
    assert!(
        scored
            .header_line(excluded)
            .contains("pbo_matrix: rows=2 days=8 excluded_nan_rows=1 cell=net_bps"),
        "{}",
        scored.header_line(excluded)
    );

    let mut only_gaps = m;
    only_gaps.ids.truncate(2);
    only_gaps.rows.truncate(2);
    let (scored, excluded) = only_gaps.without_nan_rows();
    assert_eq!(excluded, 1);
    assert_eq!(
        pbo_of_matrix(&scored),
        (None, Some("rows=1 < 2 после исключения NaN".to_string()))
    );
}

/// Таск 29, ремонт по ревью: ориентация матрицы — строка на испытание,
/// столбец на сутки. Известный ответ по построению фикстуры: в первые
/// сутки записан только SOLUSDT, во вторые — только HYPEUSDT, поэтому
/// маргинал по инструменту обязан быть пуст (`NaN`) ровно в чужом
/// столбце. Транспонированная матрица это провалит.
#[test]
fn build_trial_day_matrix_puts_trials_in_rows_and_days_in_columns() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_instruments_csv(root, &["SOLUSDT", "HYPEUSDT"]);
    let candidates_csv = root.join("candidates.csv");
    write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0), ("HYPEUSDT", 300.0)]);
    write_session_dir(
        root,
        "2026-05-01T020000Z",
        "SOLUSDT",
        "2026-05-01T02:00:00Z",
    );
    write_session_dir(
        root,
        "2026-05-02T020000Z",
        "HYPEUSDT",
        "2026-05-02T02:00:00Z",
    );
    let args = base_args(root, candidates_csv.clone());
    let pool = vec!["HYPEUSDT".to_string(), "SOLUSDT".to_string()];
    let grid = build_profile_grid(&read_coverage(&candidates_csv, &pool).unwrap()).unwrap();
    let by_day = group_by_day(&session_dirs(root).unwrap());
    let days = vec!["2026-05-01".to_string(), "2026-05-02".to_string()];
    let m = build_trial_day_matrix(&args, &instruments_csv_path(root), &by_day, &days, &grid)
        .expect("матрица по двум суткам");
    assert_eq!(m.days, days);
    assert_eq!(m.ids, grid);
    assert!(m.rows.iter().all(|r| r.len() == 2), "столбец на сутки");
    let sol = m
        .row("marginal:instrument=SOLUSDT")
        .expect("сетка несёт маргинал по инструменту");
    let hype = m
        .row("marginal:instrument=HYPEUSDT")
        .expect("сетка несёт маргинал по инструменту");
    assert!(
        sol.get(1).copied().is_some_and(f64::is_nan),
        "во вторые сутки SOLUSDT не записан: {sol:?}"
    );
    assert!(
        hype.first().copied().is_some_and(f64::is_nan),
        "в первые сутки HYPEUSDT не записан: {hype:?}"
    );
}

/// Граница модулей: чистая логика этого файла не тянет запрещённое —
/// грепом по собственному исходнику (тот же приём, что `shortlist.rs`).
#[test]
fn module_avoids_forbidden_hot_path_primitives_outside_cli_glue() {
    const SRC: &str = include_str!("../shortlist.rs");
    let banned = concat!("Inst", "ant::now");
    assert!(
        !SRC.contains(banned),
        "исходник тянет запрещённое: {banned}"
    );
}
