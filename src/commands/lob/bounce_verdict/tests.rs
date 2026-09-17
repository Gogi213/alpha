//! Тесты `lob bounce-verdict` (B5, В-58): сетка имён, срез испытаний
//! процедуры, пул кругов по форме и поправка на число испытаний.

use super::*;

use crate::lob::runs::{RunKind, RunRow};

/// Пишет покруговой дамп символа так, как его пишет `lob backtest
/// --trades-out`: шапка-комментарий, затем строки.
fn write_dump(root: &Path, form: &str, symbol: &str, rows: &[(f64, &str)]) {
    let dir = root.join(form);
    std::fs::create_dir_all(&dir).unwrap();
    let mut text = String::from("# lob backtest --touches: тест\n");
    text.push_str("signal_index,dir,entry_px,exit_px,qty,net_bps,reason\n");
    for (i, (net, reason)) in rows.iter().enumerate() {
        text.push_str(&format!(
            "{i},1,100.0000000000,100.0100000000,1.0000000000,{net:.6},{reason}\n"
        ));
    }
    std::fs::write(dir.join(format!("{symbol}.csv")), text).unwrap();
}

fn args(root: &Path, runs_csv: &Path) -> BounceVerdictArgs {
    BounceVerdictArgs {
        trades_dir: root.to_path_buf(),
        runs_csv: runs_csv.to_path_buf(),
        out: root.join("verdict.csv"),
        log_trials: true,
    }
}

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() < 1e-9
}

/// Имя формы читается сеткой В-58, а всё, чего в сетке нет, — отказ:
/// «попробовать ещё одно значение» — это лишнее испытание, а не параметр.
#[test]
fn form_names_are_the_preregistered_grid_and_nothing_else() {
    assert_eq!(parse_form("behind-60-off").unwrap(), ("behind", 60, None));
    assert_eq!(
        parse_form("before-7200-3").unwrap(),
        ("before", 7200, Some(3))
    );
    assert_eq!(parse_form("at-600-1").unwrap(), ("at", 600, Some(1)));

    for bad in [
        "middle-60-off",
        "behind-120-off",
        "behind-60-5",
        "behind-60",
        "behind-60-x",
    ] {
        assert!(parse_form(bad).is_err(), "имя {bad} обязано быть отказом");
    }
}

/// Срез испытаний — строки **этой** процедуры: пилоты плотности и профили
/// касаний лежат в том же журнале, и складывать их с формами отскока значило
/// бы дефлировать Шарп чужим выбором (полное число печатается рядом).
#[test]
fn trial_slice_counts_only_this_procedures_rows() {
    let row = |kind: RunKind, detail: &str| RunRow {
        ts_utc: "2026-09-18T00:00:00Z".to_string(),
        symbol: String::new(),
        kind,
        detail: detail.to_string(),
    };
    let rows = vec![
        row(RunKind::Confirmatory, "bounce_form behind-60-off"),
        row(RunKind::Confirmatory, "bounce_form before-600-1"),
        row(RunKind::Confirmatory, "profile age:[0,10m)"),
        row(RunKind::Pilot, "NEARUSDT пилот"),
        row(RunKind::Prereg, "V-58 bounce base before data"),
        row(RunKind::Amendment, "правка"),
        // Похожий префикс, но не эта процедура: `bounce_formX` — не строка
        // формы, и засчитывать её значило бы менять `N` случайным совпадением.
        row(RunKind::Confirmatory, "bounce_formX behind-60-off"),
    ];
    assert_eq!(count_scoped_trials(&rows, BOUNCE_TRIAL_PREFIX), 2);
    assert_eq!(
        runs::count_trials(&rows),
        5,
        "полное число — пилоты и профили"
    );
}

/// Вердикт собирает круги всех символов формы в один ряд, считает доли причин
/// и берёт `DSR` лучшей формы тем же срезом пробных Шарпов, что и
/// `final_metrics::dsr_from_returns`.
#[test]
fn verdict_pools_circles_per_form_and_deflates_by_journal_trials() {
    let dir = tempfile::tempdir().unwrap();
    let runs_csv = dir.path().join("runs.csv");
    let a = [(1.0, "stop"), (2.0, "take"), (3.0, "take")];
    let a2 = [(-1.0, "stop"), (0.5, "deadline")];
    let b = [(0.5, "take"), (0.5, "early"), (2.0, "take"), (-3.0, "stop")];
    write_dump(dir.path(), "behind-60-off", "ZECUSDT", &a);
    write_dump(dir.path(), "behind-60-off", "SOLUSDT", &a2);
    write_dump(dir.path(), "before-600-1", "ZECUSDT", &b);

    let summary = run_bounce_verdict(&args(dir.path(), &runs_csv)).unwrap();
    assert_eq!(summary.forms, 2);
    assert_eq!(summary.trials, 2, "две формы — два испытания процедуры");
    assert_eq!(summary.best_form, "behind-60-off");
    assert!(close(
        summary.best_net_fill_bps.unwrap(),
        (1.0 + 2.0 + 3.0 - 1.0 + 0.5) / 5.0
    ));

    // DSR — тот же вызов, что у вердикта профилей: ряд лучшей формы и срез
    // пробных Шарпов всех форм сетки.
    let best_returns = vec![1.0, 2.0, 3.0, -1.0, 0.5];
    let trial_sharpes = vec![
        final_metrics::sharpe_ratio(&best_returns).unwrap(),
        final_metrics::sharpe_ratio(&[0.5, 0.5, 2.0, -3.0]).unwrap(),
    ];
    let expected = final_metrics::dsr_from_returns(&best_returns, &trial_sharpes);
    assert!(expected.is_some(), "срез Шарпов обязан считаться");
    assert!(close(summary.dsr.unwrap(), expected.unwrap()));
    // Число испытаний журнала — это `N` поправки: 8 старых строк в боевом
    // журнале не мешают, потому что срез считается по префиксу.
    assert_eq!(summary.journal_trials, 2);
    assert_eq!(
        summary.required_sharpe,
        final_metrics::required_sharpe_for_dsr(2, 5, DSR_TARGET)
    );

    let text = std::fs::read_to_string(&summary.out).unwrap();
    let lines: Vec<&str> = text.lines().collect();
    assert!(
        lines[0].starts_with("# lob bounce-verdict:"),
        "шапка: {}",
        lines[0]
    );
    let header: Vec<&str> = lines[1].split(',').collect();
    assert!(header.contains(&"net_fill_bps") && header.contains(&"share_stop"));
    // Порядок строк — по имени формы (`before-…` раньше `behind-…`), поэтому
    // строку берём по имени, а не по номеру: нумерация — не часть контракта.
    let row = |form: &str| -> Vec<String> {
        lines
            .iter()
            .skip(2)
            .map(|l| l.split(',').map(str::to_string).collect::<Vec<_>>())
            .find(|r| r[0] == form)
            .unwrap_or_else(|| panic!("строки формы {form} нет в {}", summary.out.display()))
    };
    let col = |name: &str| header.iter().position(|h| *h == name).unwrap();
    let row_a = row("behind-60-off");
    assert_eq!(row_a[1], "behind");
    assert_eq!(row_a[2], "60");
    assert_eq!(row_a[3], "off");
    assert_eq!(row_a[4], "5", "круги обоих символов — один ряд формы");
    assert_eq!(row_a[col("n_stop")], "2");
    assert_eq!(row_a[col("n_take")], "2");
    assert_eq!(row_a[col("n_timeout")], "1");
    assert!(close(row_a[col("share_stop")].parse().unwrap(), 2.0 / 5.0));
    let row_b = row("before-600-1");
    assert_eq!(row_b[col("n_early")], "1");
    assert_eq!(row_b[col("early_exit_secs")], "1");
    assert_eq!(row_b[4], "4");
}

/// Повторный прогон без `--log-trials` не дописывает журнал, но обязан найти
/// в нём испытания сетки: пустой срез — отказ, а не поправка по нулю.
#[test]
fn verdict_refuses_a_journal_that_does_not_know_the_grid() {
    let dir = tempfile::tempdir().unwrap();
    let runs_csv = dir.path().join("runs.csv");
    write_dump(
        dir.path(),
        "behind-60-off",
        "ZECUSDT",
        &[(1.0, "take"), (-1.0, "stop")],
    );
    write_dump(
        dir.path(),
        "before-600-1",
        "ZECUSDT",
        &[(2.0, "take"), (-1.0, "stop")],
    );

    let mut a = args(dir.path(), &runs_csv);
    a.log_trials = false;
    let err = run_bounce_verdict(&a).unwrap_err().to_string();
    assert!(
        err.contains("--log-trials"),
        "отказ обязан назвать выход: {err}"
    );
}

/// Форма без Шарпа (нулевая дисперсия ряда) — отказ: молча выпав из среза,
/// она занизила бы `N`.
#[test]
fn verdict_refuses_a_form_without_a_sharpe() {
    let dir = tempfile::tempdir().unwrap();
    let runs_csv = dir.path().join("runs.csv");
    write_dump(
        dir.path(),
        "behind-60-off",
        "ZECUSDT",
        &[(1.0, "take"), (1.0, "stop")],
    );
    write_dump(
        dir.path(),
        "before-600-1",
        "ZECUSDT",
        &[(2.0, "take"), (-1.0, "stop")],
    );
    let err = run_bounce_verdict(&args(dir.path(), &runs_csv))
        .unwrap_err()
        .to_string();
    assert!(
        err.contains("Шарп не считается"),
        "отказ обязан назвать форму: {err}"
    );
}

/// Круг без числа (`not_measured`) — отказ, а не тихо выброшенное наблюдение:
/// ряд, из которого убрали неудачные круги, дефлируется не по тому Шарпу.
#[test]
fn verdict_refuses_a_circle_that_was_not_measured() {
    let dir = tempfile::tempdir().unwrap();
    let runs_csv = dir.path().join("runs.csv");
    let form = dir.path().join("behind-60-off");
    std::fs::create_dir_all(&form).unwrap();
    std::fs::write(
        form.join("ZECUSDT.csv"),
        "# тест\nsignal_index,dir,entry_px,exit_px,qty,net_bps,reason\n\
         0,1,100.0,101.0,1.0,1.000000,take\n\
         1,1,0.0,101.0,1.0,not_measured,stop\n",
    )
    .unwrap();
    write_dump(
        dir.path(),
        "before-600-1",
        "ZECUSDT",
        &[(2.0, "take"), (-1.0, "stop")],
    );
    let err = run_bounce_verdict(&args(dir.path(), &runs_csv))
        .unwrap_err()
        .to_string();
    assert!(
        err.contains("not_measured"),
        "отказ обязан назвать литерал: {err}"
    );
}

/// Каталог без форм — отказ: поправка на число испытаний считается по сетке.
#[test]
fn verdict_refuses_an_empty_grid() {
    let dir = tempfile::tempdir().unwrap();
    let runs_csv = dir.path().join("runs.csv");
    let err = run_bounce_verdict(&args(dir.path(), &runs_csv))
        .unwrap_err()
        .to_string();
    assert!(
        err.contains("форм 0"),
        "отказ обязан назвать число форм: {err}"
    );
}
