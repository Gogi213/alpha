//! Тесты `statarb run` TK-493 волны 5: `--no-entry-before-from` (D5(ii), аудит M3-04), `--job-ext-out` (L1-03), проверки
//! входных файлов и окна (L2-03). Помощники — из `run_tests` (родительский модуль).
//!
//! D2-подобный сценарий: Y и X плоские 3 суток, ставки Y постоянные каждые 8 минут с минуты 3 (30 bps удержания против круга),
//! правило funding (k = 2, 3 выплаты), срок 40 минут, окно 4: задание суток 1…3 греется 4 минуты до границы 1440. Кольцо ставок
//! полно сразу, поэтому без запрета вход на первой минуте (1437) идёт в разогреве и сделка отбрасывается как `pre_window`.

use super::*;

fn d2_case(root: &Path) {
    let n = 3 * 1440;
    put_panel(&root.join("p"), "YYY", &flat(n));
    put_panel(&root.join("p"), "XXX", &flat(n));
    let ev: Vec<(i64, f64)> = (0..n as i64 / 8).map(|j| (3 + 8 * j, 1e-3)).collect();
    put_fund(&root.join("f"), "YYY", &ev);
}

/// Прогон правила funding над `d2_case` со сводкой клеток и счётчиками заданий; `extra` — доп. флаги.
fn d2_run(root: &Path, tag: &str, jobs: &str, extra: &[&str]) -> (Files, PathBuf) {
    let f = files(root, tag, jobs);
    let jext = root.join(format!("jext-{tag}.csv"));
    let mut a = args(&[
        "--kind",
        "pair",
        "--rule",
        "funding",
        "--funding-k",
        "2",
        "--hold-payments",
        "3",
    ]);
    kv(&mut a, "--panels", &root.join("p"));
    kv(&mut a, "--funding-y", &root.join("f"));
    a.extend(common(&f));
    a.extend(hc_cell("D", "40"));
    kv(&mut a, "--cells-out", &f.cells_out);
    kv(&mut a, "--job-ext-out", &jext);
    a.extend(args(extra));
    go(&a).unwrap();
    (f, jext)
}

#[test]
fn no_entry_before_from_opens_d2_at_from_min_and_counts_blocked_signals() {
    let d = tempfile::tempdir().unwrap();
    let root = d.path();
    d2_case(root);
    let jobs = "y,x,from_day,to_day\nYYY,XXX,1,3\n";
    let (off, _) = d2_run(root, "off", jobs, &[]);
    let (on, _) = d2_run(root, "on", jobs, &["--no-entry-before-from"]);
    // без флага: заголовок сводки прежний (21 столбец), первая сделка пары вошла в разогреве (1437 < 1440) и отброшена
    assert!(!header(&off.cells_out).contains("warmup_blocked"));
    assert_eq!(header(&off.cells_out).split(',').count(), 21);
    let c_off = &table(&off.cells_out)[0];
    assert_eq!(num(c_off, "pre_window"), 1.0, "{c_off:?}");
    assert!(num(&table(&off.trades)[0], "fill_in") >= 1440.0);
    // с флагом: столбец дописан; сигналы минут 1436…1438 подавлены (3), сигнал 1439 исполнен ровно на 1440 — отбрасывать нечего
    assert!(header(&on.cells_out).ends_with(",warmup_blocked"));
    let c = &table(&on.cells_out)[0];
    assert_eq!(num(c, "pre_window"), 0.0, "{c:?}");
    assert_eq!(num(c, "warmup_blocked"), 3.0, "{c:?}");
    assert_eq!(num(c, "open_pre_window"), 0.0);
    let t = table(&on.trades);
    let r = &t[0];
    assert_eq!(
        (
            num(r, "dir"),
            num(r, "sig_in"),
            num(r, "fill_in"),
            num(r, "sig_out"),
            num(r, "fill_out")
        ),
        (-1.0, 1439.0, 1440.0, 1480.0, 1481.0)
    );
    // выплаты 1443, 1451, 1459, 1467, 1475 — по 10 bps шорту Y
    assert!(close(num(r, "funding_bps"), 50.0), "{r:?}");
    // сделок в окне с флагом больше: первая сделка пары, потерянная в разогреве, теперь считается
    let in_window_off = num(c_off, "trades") - num(c_off, "pre_window");
    assert!(num(c, "trades") > in_window_off, "{c:?}");
}

#[test]
fn job_ext_out_has_a_row_per_cell_and_job_and_agrees_with_cells_out() {
    let d = tempfile::tempdir().unwrap();
    let root = d.path();
    d2_case(root);
    // задание 0 посчитано, 1 — нет панели X, 2 — окно за концом данных (нет общего спана)
    let jobs = "y,x,from_day,to_day\nYYY,XXX,1,3\nYYY,ZZZ,1,3\nYYY,XXX,5,6\n";
    let (f, jext) = d2_run(root, "je", jobs, &["--no-entry-before-from", "--mtm-open"]);
    assert_eq!(
        header(&jext),
        "cell,job,y,x,status,eval_minutes,z_def_minutes,minutes_in_pos,pos_z_nan_minutes,noentry_window_minutes,entries,trades,\
pre_window,pre_window_net_bps,open_at_end,open_pre_window,warmup_blocked,mtm_n,mtm_net_bps,mtm_gross_bps,mtm_fee_bps,mtm_funding_bps,mtm_stale_min"
    );
    let rows = table(&jext);
    assert_eq!(rows.len(), 3);
    let st: Vec<&str> = rows.iter().map(|r| r["status"].as_str()).collect();
    assert_eq!(st, ["done", "missing", "no_span"]);
    let c = &table(&f.cells_out)[0];
    let r = &rows[0];
    assert_eq!(
        (r["cell"].as_str(), r["y"].as_str(), r["x"].as_str()),
        ("D", "YYY", "XXX")
    );
    for k in [
        "entries",
        "trades",
        "pre_window",
        "open_at_end",
        "open_pre_window",
        "warmup_blocked",
        "mtm_n",
        "mtm_net_bps",
        "mtm_gross_bps",
        "mtm_fee_bps",
        "mtm_funding_bps",
    ] {
        assert!(close(num(r, k), num(c, k)), "{k}: {} против {}", r[k], c[k]);
    }
    // позиция открыта на конце окна и оценена; счётчики окна z и позиции — по заданию
    assert_eq!(num(r, "mtm_n"), 1.0);
    assert_eq!(num(r, "pre_window"), 0.0);
    assert_eq!(num(r, "warmup_blocked"), 3.0);
    assert!(num(r, "eval_minutes") > 0.0 && num(r, "minutes_in_pos") > 0.0);
    // нет панели и нет спана: нули, оценки нет
    for r in &rows[1..] {
        for k in [
            "eval_minutes",
            "entries",
            "trades",
            "pre_window",
            "warmup_blocked",
            "mtm_n",
        ] {
            assert_eq!(num(r, k), 0.0, "{k} в строке {r:?}");
        }
    }
    // без --mtm-open столбцы оценки — nan; без запрета разогрев не подавлен, а отброшен (pre_window)
    let (_, j2) = d2_run(root, "je2", jobs, &[]);
    let r2 = &table(&j2)[0];
    assert_eq!(r2["mtm_net_bps"], "nan");
    assert_eq!(num(r2, "warmup_blocked"), 0.0);
    assert!(num(r2, "pre_window") >= 1.0);
    // потеря видна не только числом, но и суммой: отброшенная первая сделка (вход 1437, выход 1478) — 4 стороны × 3,15 комиссии
    // и 5 выплат (1443…1475 → 50 bps) минус ничего: net = 50 − 12,6; число отброшенных сделок умножает её
    assert!(
        close(
            num(r2, "pre_window_net_bps"),
            num(r2, "pre_window") * (50.0 - 4.0 * 3.15)
        ),
        "{r2:?}"
    );
    assert_eq!(num(r, "pre_window_net_bps"), 0.0);
}

#[test]
fn input_files_without_header_and_bad_window_or_delay_are_rejected() {
    let d = tempfile::tempdir().unwrap();
    let root = d.path();
    put_panel(&root.join("p"), "YYY", &flat(60));
    put_panel(&root.join("p"), "XXX", &flat(60));
    let run_bad = |tag: &str, jobs: &str, cell: Vec<String>, extra: &[&str]| -> String {
        let f = files(root, tag, jobs);
        let mut a = args(&["--kind", "pair"]);
        kv(&mut a, "--panels", &root.join("p"));
        a.extend(common(&f));
        a.extend(cell);
        a.extend(args(extra));
        go(&a).unwrap_err().to_string()
    };
    // файл заданий без заголовка: первая строка данных больше не теряется молча
    let e = run_bad("h1", "YYY,XXX,0,1\n", hc_cell("T", "10"), &[]);
    assert!(e.contains("заголовком"), "{e}");
    // файл клеток без заголовка
    std::fs::write(
        root.join("cells-nohdr.csv"),
        "c0,4,4,1.0,1.5,0.5,inf,10,0\n",
    )
    .unwrap();
    let f = files(root, "h2", "y,x,from_day,to_day\nYYY,XXX,0,1\n");
    let mut a = args(&["--kind", "pair"]);
    kv(&mut a, "--panels", &root.join("p"));
    a.extend(common(&f));
    kv(&mut a, "--cells", &root.join("cells-nohdr.csv"));
    let e = go(&a).unwrap_err().to_string();
    assert!(e.contains("заголовком"), "{e}");
    // окно 0 — ошибка, а не деление на ноль; delay_x = usize::MAX зарезервирован
    let jobs = "y,x,from_day,to_day\nYYY,XXX,0,1\n";
    let mut zero = hc_cell("T", "10");
    zero[3] = "0".to_string();
    let e = run_bad("h3", jobs, zero, &[]);
    assert!(e.contains("window"), "{e}");
    let e = run_bad(
        "h4",
        jobs,
        hc_cell("T", "10"),
        &["--delay-x", "18446744073709551615"],
    );
    assert!(e.contains("зарезервирован"), "{e}");
    // файл ставок: без заголовка — ошибка, пустой файл — нет ставок
    assert!(parse_funding("1,0.0001,8\n").is_err());
    assert!(parse_funding("").unwrap().is_empty());
    assert_eq!(parse_funding("time_ms,rate,interval_h\n").unwrap().len(), 0);
}
