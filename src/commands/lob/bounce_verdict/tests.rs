use super::DEADLINE_SECS;
use super::*;
use crate::commands::lob::backtest::{StopForm, TakeForm};
use crate::commands::lob::bounce_grid::{grid_forms, GridForm};
use crate::lob::runs::{self, RunKind, RunRow};
use std::path::Path;

/// Сетка синтетики: 3 множителя стопа × 4 тейка × 4 дедлайна = 48 форм — тот
/// же размер, что у сетки В-58, чтобы гейты и DSR считались на тех же числах.
fn test_forms() -> Vec<GridForm> {
    grid_forms(
        &[
            StopForm::Sigma(0.5),
            StopForm::Sigma(1.0),
            StopForm::Sigma(2.0),
        ],
        &[
            TakeForm::Sigma(1.0),
            TakeForm::Sigma(2.0),
            TakeForm::Sigma(3.0),
            TakeForm::Sigma(4.0),
        ],
        Some(1.0),
        &DEADLINE_SECS,
    )
}

/// Синтетический каталог `bounce-grid`: 48 форм × символы × сутки. `net`
/// круга — детерминированная функция (форма, символ, сутки, номер круга);
/// у формы `favoured` все круги положительные (`+bias`), у остальных —
/// вокруг нуля. Сигналов на пару (символ, сутки) — `signals`, кругов —
/// `fills`.
fn write_grid(
    dir: &Path,
    symbols: &[&str],
    days: &[&str],
    signals: u64,
    fills: Option<u64>,
    favoured: &str,
    bias: f64,
) {
    // Кругов на пару (символ, сутки): фиксированно или по-разному на каждый
    // день — как в жизни; одинаковые суточные суммы делают реплики
    // бутстрэпа сумм пропорциональными и дают ровно ноль в квантили.
    let fills_of = |si: usize, di: usize| fills.unwrap_or(10 + 3 * di as u64 + si as u64);
    std::fs::create_dir_all(dir).unwrap();
    let mut forms = String::from(
        "# synthetic grid\nsymbol,day_utc,form,n_signals,n_submitted,n_fills,n_busy,entry_rejected,entry_crossed,sum_net_bps,n_stop,n_take,n_trail,n_deadline,n_early,n_horizon,incomplete,n_skipped,n_residual_flattened,signals_by_hour\n",
    );
    let mut rounds = String::from(
        "# synthetic grid\nsymbol,day_utc,form,signal_index,t0_ns,dir,entry_px,exit_px,qty,net_bps,reason\n",
    );
    for (fi, form) in test_forms().iter().enumerate() {
        for (si, sym) in symbols.iter().enumerate() {
            for (di, day) in days.iter().enumerate() {
                let fills = fills_of(si, di);
                let signals = signals.max(fills);
                let mut sum = 0.0;
                for k in 0..fills {
                    // Псевдослучайный, но воспроизводимый знак и величина.
                    let seed =
                        (fi * 7919 + si * 104_729 + di * 1_299_709 + k as usize * 15_485_863)
                            % 1000;
                    let noise = (seed as f64 / 1000.0 - 0.5) * 4.0; // [-2, 2) bps
                    let net = if form.label == favoured {
                        bias + noise * 0.1
                    } else {
                        noise
                    };
                    sum += net;
                    let reason = if net >= 0.0 { "take" } else { "stop" };
                    rounds.push_str(&format!(
                        "{sym},{day},{},{k},{},{},100.0,100.0,1.0,{net:.6},{reason}\n",
                        form.label,
                        // Круги раз в 10 минут: 60 кругов — десять часов, чтобы
                        // вердикт по часам (В-60) имел кластеры.
                        k * 600 * 1_000_000_000,
                        if k % 2 == 0 { 1 } else { -1 }
                    ));
                }
                let n_take = (0..fills)
                    .filter(|k| {
                        let seed =
                            (fi * 7919 + si * 104_729 + di * 1_299_709 + *k as usize * 15_485_863)
                                % 1000;
                        let noise = (seed as f64 / 1000.0 - 0.5) * 4.0;
                        let net = if form.label == favoured {
                            bias + noise * 0.1
                        } else {
                            noise
                        };
                        net >= 0.0
                    })
                    .count() as u64;
                // Сигналы по часам: круги — по своему t0 (раз в 10 минут),
                // промахи — все в 23-м часе.
                let mut by_hour = [0u64; 24];
                for k in 0..fills {
                    by_hour[((k * 600) / 3600) as usize % 24] += 1;
                }
                by_hour[23] += signals - fills;
                let by_hour = by_hour
                    .iter()
                    .map(u64::to_string)
                    .collect::<Vec<_>>()
                    .join(":");
                forms.push_str(&format!(
                    "{sym},{day},{},{signals},{fills},{fills},{},0,0,{sum:.6},{},{n_take},0,0,0,0,false,0,0,{by_hour}\n",
                    form.label,
                    signals - fills,
                    fills - n_take
                ));
            }
        }
    }
    std::fs::write(dir.join("forms.csv"), forms).unwrap();
    std::fs::write(dir.join("rounds.csv"), rounds).unwrap();
}

fn journal_with_grid(path: &Path) {
    runs::ensure_runs_csv(path).unwrap();
    let labels: Vec<String> = test_forms().iter().map(|f| f.label.to_string()).collect();
    runs::log_trials(path, "2026-09-18T00:00:00Z", BOUNCE_TRIAL_PREFIX, &labels).unwrap();
}

fn args(grid: &Path, runs_csv: &Path, out: &Path) -> BounceVerdictArgs {
    BounceVerdictArgs {
        grid_dir: grid.to_path_buf(),
        runs_csv: runs_csv.to_path_buf(),
        out: out.to_path_buf(),
        log_trials: false,
    }
}

/// Имена форм — `s<a>-t<b>-<H>` (В-62): разбор возвращает множители и дедлайн,
/// неканоническое или чужое имя — отказ, размер сетки — произведение осей,
/// встреченных в именах.
#[test]
fn form_names_are_the_sigma_grid_and_nothing_else() {
    let labels: Vec<String> = test_forms().iter().map(|f| f.label.to_string()).collect();
    assert_eq!(labels.len(), 48);
    assert_eq!(
        grid_size_from_labels(labels.iter().map(String::as_str)).unwrap(),
        48
    );
    assert_eq!(
        parse_form("s0.5-t1-60").unwrap(),
        ("s0.5".to_string(), "t1".to_string(), 60)
    );
    assert_eq!(
        parse_form("before-1to1-7200").unwrap(),
        ("before".to_string(), "1to1".to_string(), 7200)
    );
    assert!(parse_form("s1-t1-90").is_err(), "дедлайн вне сетки");
    assert!(parse_form("x1-t1-60").is_err(), "стоп не из базы");
    assert!(parse_form("s1-tx-60").is_err(), "тейк не число");
    assert!(parse_form("s1-t1-60-5").is_err(), "не три части");
    assert!(parse_form("s1.0-t1-60").is_err(), "неканоническое имя");
    assert_eq!(form_label("s1.5", "t1", 600), "s1.5-t1-600");
    // Неполное произведение осей — не сетка: 3 формы при осях 2 × 1 × 4.
    assert_ne!(
        grid_size_from_labels(["s1-t1-60", "s2-t1-60", "s1-t1-600"]).unwrap(),
        3
    );
}

/// `N` для DSR — только строки этой процедуры (`bounce_form`), не весь журнал.
#[test]
fn trial_slice_counts_only_this_procedures_rows() {
    let row = |kind: RunKind, detail: &str| RunRow {
        ts_utc: "2026-09-18T00:00:00Z".to_string(),
        symbol: "POOL".to_string(),
        kind,
        detail: detail.to_string(),
    };
    let rows = vec![
        row(RunKind::Confirmatory, "bounce_form:s0.5-t1-60"),
        row(RunKind::Confirmatory, "bounce_form:s1-t1-600"),
        row(RunKind::Confirmatory, "pilot k=1.0"),
        row(RunKind::Prereg, "bounce_form:prereg"),
    ];
    assert_eq!(count_scoped_trials(&rows, BOUNCE_TRIAL_PREFIX), 2);
    assert_eq!(runs::count_trials(&rows), 3);
}

/// Полный синтетический прогон: 48 форм × 2 символа × 8 суток, у одной формы
/// все круги +5 bps. Вердикт: лучшая — она, нижняя граница интервала > 0,
/// суток с кругами 8 ≥ G_MIN, кругов ≥ 100, DSR при 48 испытаниях, PBO и
/// CPCV посчитаны, итог — «зелёный» (точка ≥ 3 bps на сигнал при 100 %
/// исполнении).
#[test]
fn verdict_uses_day_clustered_interval_gates_and_selection_metrics() {
    let dir = tempfile::tempdir().unwrap();
    let grid = dir.path().join("grid");
    let days = [
        "2026-09-10",
        "2026-09-11",
        "2026-09-12",
        "2026-09-13",
        "2026-09-14",
        "2026-09-15",
        "2026-09-16",
        "2026-09-17",
    ];
    write_grid(
        &grid,
        &["AAAUSDT", "BBBUSDT"],
        &days,
        10,
        None,
        "s1-t2-600",
        5.0,
    );
    let runs_csv = dir.path().join("runs.csv");
    journal_with_grid(&runs_csv);
    let out = dir.path().join("verdict.csv");
    let s = run_bounce_verdict(&args(&grid, &runs_csv, &out)).expect("вердикт на синтетике");

    assert_eq!(s.forms, 48);
    assert_eq!(s.symbols, 2);
    assert_eq!(s.days, 8);
    assert_eq!(s.trials, 48);
    assert_eq!(s.best_form, "s1-t2-600");
    let expected_fills: u64 = (0..2usize)
        .flat_map(|si| (0..8usize).map(move |di| 10 + 3 * di as u64 + si as u64))
        .sum();
    assert_eq!(
        s.best_n_fills, expected_fills,
        "сумма кругов по символам и суткам"
    );
    assert_eq!(s.best_days, 8);
    let point = s.best_point_bps.expect("точка интервала");
    let lower = s.best_lower_bps.expect("нижняя граница");
    assert!(point > 4.0 && point < 6.0, "точка ≈ 5 bps: {point}");
    assert!(
        lower > 0.0 && lower < point,
        "нижняя граница между 0 и точкой: {lower}"
    );
    assert!(s.dsr.is_some(), "DSR при 48 испытаниях");
    assert!(s.pbo.is_some(), "PBO по матрице 48×8");
    assert!(s.cpcv.is_some(), "CPCV по матрице 48×8");
    assert_eq!(s.verdict, Verdict::Green);

    let text = std::fs::read_to_string(&out).unwrap();
    assert!(text.contains("ИТОГ: зелёный"), "{text}");
    assert!(
        text.contains("без деления выборки"),
        "оговорка владельца в шапке"
    );
    let data_rows = text.lines().filter(|l| !l.starts_with('#')).count();
    assert_eq!(data_rows, 49, "шапка + 48 форм");
}

/// В-60 (владелец 2026-09-18: «одного дня минимум хватает»): при суток
/// меньше `G_MIN` кластер интервала — час UTC; трое суток по 60 кругов раз в
/// 10 минут дают десятки часовых кластеров, и вердикт выносится (форма с
/// преимуществом +5 bps), а не «мало данных».
#[test]
fn few_days_get_a_verdict_through_hour_clusters() {
    let dir = tempfile::tempdir().unwrap();
    let grid = dir.path().join("grid");
    write_grid(
        &grid,
        &["AAAUSDT"],
        &["2026-09-10", "2026-09-11", "2026-09-12"],
        60,
        Some(60),
        "s1-t2-600",
        5.0,
    );
    let runs_csv = dir.path().join("runs.csv");
    journal_with_grid(&runs_csv);
    let out = dir.path().join("v.csv");
    let s = run_bounce_verdict(&args(&grid, &runs_csv, &out)).unwrap();
    assert_eq!(s.best_n_fills, 180);
    assert_eq!(s.best_days, 3);
    assert_ne!(s.verdict, Verdict::NotEnoughData, "{s:?}");
    let text = std::fs::read_to_string(&out).unwrap();
    assert!(text.contains("cluster_unit"), "{text}");
    assert!(text.contains(",hour,"), "кластер — час: {text}");
    // Мало кругов — по-прежнему «мало данных», часы этого не отменяют.
    let grid2 = dir.path().join("grid2");
    write_grid(
        &grid2,
        &["AAAUSDT"],
        &["2026-09-10"],
        10,
        Some(3),
        "s1-t2-600",
        5.0,
    );
    let s2 = run_bounce_verdict(&args(&grid2, &runs_csv, &dir.path().join("v2.csv"))).unwrap();
    assert_eq!(s2.verdict, Verdict::NotEnoughData);
}

/// Два символа при суток < G_MIN: круги обоих символов в один час, сигналы —
/// по ячейкам; инвариант «кругов не больше сигналов» сравнивает суммы по
/// суткам, а не круги суток с сигналами одной ячейки (ложный отказ 19.09).
#[test]
fn hour_clusters_sum_signals_across_symbols() {
    let dir = tempfile::tempdir().unwrap();
    let grid = dir.path().join("grid");
    write_grid(
        &grid,
        &["AAAUSDT", "BBBUSDT"],
        &["2026-09-10", "2026-09-11"],
        60,
        Some(60),
        "s1-t2-600",
        5.0,
    );
    let runs_csv = dir.path().join("runs.csv");
    journal_with_grid(&runs_csv);
    let s = run_bounce_verdict(&args(&grid, &runs_csv, &dir.path().join("v.csv")))
        .expect("круги двух символов в одном часе — не отказ");
    assert_eq!(s.best_n_fills, 240);
}

/// Форма без преимущества — интервал не отделяется от нуля: «красный».
#[test]
fn no_edge_is_red() {
    let dir = tempfile::tempdir().unwrap();
    let grid = dir.path().join("grid");
    let days = [
        "2026-09-10",
        "2026-09-11",
        "2026-09-12",
        "2026-09-13",
        "2026-09-14",
        "2026-09-15",
        "2026-09-16",
        "2026-09-17",
    ];
    write_grid(&grid, &["AAAUSDT", "BBBUSDT"], &days, 10, None, "none", 0.0);
    let runs_csv = dir.path().join("runs.csv");
    journal_with_grid(&runs_csv);
    let s = run_bounce_verdict(&args(&grid, &runs_csv, &dir.path().join("v.csv"))).unwrap();
    assert_eq!(s.verdict, Verdict::Red);
}

/// K4: неполная сетка (47 форм) или разное число сигналов между формами — отказ.
#[test]
fn partial_grid_or_uneven_signals_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let grid = dir.path().join("grid");
    write_grid(
        &grid,
        &["AAAUSDT"],
        &["2026-09-10", "2026-09-11"],
        5,
        Some(3),
        "none",
        0.0,
    );
    let runs_csv = dir.path().join("runs.csv");
    journal_with_grid(&runs_csv);

    let forms_path = grid.join("forms.csv");
    let full = std::fs::read_to_string(&forms_path).unwrap();
    let without_one: String = full
        .lines()
        .filter(|l| !l.contains(",s2-t4-7200,"))
        .map(|l| format!("{l}\n"))
        .collect();
    std::fs::write(&forms_path, &without_one).unwrap();
    let rounds_path = grid.join("rounds.csv");
    let rounds_full = std::fs::read_to_string(&rounds_path).unwrap();
    let rounds_without: String = rounds_full
        .lines()
        .filter(|l| !l.contains(",s2-t4-7200,"))
        .map(|l| format!("{l}\n"))
        .collect();
    std::fs::write(&rounds_path, &rounds_without).unwrap();
    let err = run_bounce_verdict(&args(&grid, &runs_csv, &dir.path().join("v.csv")))
        .unwrap_err()
        .to_string();
    assert!(err.contains("48"), "{err}");

    std::fs::write(&forms_path, full.replacen(",5,3,3,2,", ",6,3,3,3,", 1)).unwrap();
    std::fs::write(&rounds_path, &rounds_full).unwrap();
    let err = run_bounce_verdict(&args(&grid, &runs_csv, &dir.path().join("v.csv")))
        .unwrap_err()
        .to_string();
    assert!(err.contains("сигналов"), "{err}");
}

/// Журнал, не знающий про сетку, — отказ: DSR без `N` не дефлирует.
#[test]
fn verdict_refuses_a_journal_that_does_not_know_the_grid() {
    let dir = tempfile::tempdir().unwrap();
    let grid = dir.path().join("grid");
    write_grid(
        &grid,
        &["AAAUSDT"],
        &["2026-09-10", "2026-09-11"],
        5,
        Some(3),
        "none",
        0.0,
    );
    let runs_csv = dir.path().join("runs.csv");
    runs::ensure_runs_csv(&runs_csv).unwrap();
    let err = run_bounce_verdict(&args(&grid, &runs_csv, &dir.path().join("v.csv")))
        .unwrap_err()
        .to_string();
    assert!(err.contains("--log-trials"), "{err}");

    let mut a = args(&grid, &runs_csv, &dir.path().join("v.csv"));
    a.log_trials = true;
    let s = run_bounce_verdict(&a).expect("с --log-trials журнал дописан");
    assert_eq!(s.trials, 48);
}

/// Круг без измеренного `net` — отказ, а не ноль.
#[test]
fn verdict_refuses_a_circle_that_was_not_measured() {
    let dir = tempfile::tempdir().unwrap();
    let grid = dir.path().join("grid");
    write_grid(
        &grid,
        &["AAAUSDT"],
        &["2026-09-10", "2026-09-11"],
        5,
        Some(3),
        "none",
        0.0,
    );
    let rounds_path = grid.join("rounds.csv");
    let text = std::fs::read_to_string(&rounds_path).unwrap();
    // Первая строка данных: поле `net_bps` (индекс 9) заменяется на пометку.
    let mut lines: Vec<String> = text.lines().map(str::to_string).collect();
    let first = lines
        .iter()
        .position(|l| !l.starts_with('#') && !l.starts_with("symbol,"))
        .unwrap();
    let mut fields: Vec<&str> = lines[first].split(',').collect();
    fields[9] = "not_measured";
    lines[first] = fields.join(",");
    std::fs::write(
        &rounds_path,
        lines.join(
            "
",
        ) + "
",
    )
    .unwrap();
    let runs_csv = dir.path().join("runs.csv");
    journal_with_grid(&runs_csv);
    let err = run_bounce_verdict(&args(&grid, &runs_csv, &dir.path().join("v.csv")))
        .unwrap_err()
        .to_string();
    assert!(err.contains("не измерен") || err.contains("net"), "{err}");
}

/// F6 (В-73) + F5 (В-74): имя формы несёт вход первым полем
/// (`<вход>-<стоп>-<тейк>-<H>`) и необязательные хвостовые `-ttl<режим>`
/// (срок жизни входа) и `-<выход>` (F7). Прежние трёхпольные имена читаются
/// как `single@fr-…`, а полнота сетки считается по **всем** осям, включая
/// вход и срок жизни, — иначе артефакты F5/F6 вердиктом не прочитать.
#[test]
fn form_names_carry_the_entry_the_ttl_and_the_exit() {
    assert_eq!(
        form_label_with_entry("single@fr", "pct2", "1to1", 3600),
        "pct2-1to1-3600",
        "у прежнего входа поле в имени не пишется (гейт «те же круги»)"
    );
    assert_eq!(
        form_label_with_entry("ladder3x2..10", "pct2", "1to1", 3600),
        "ladder3x2..10-pct2-1to1-3600"
    );
    // Четыре поля: вход, стоп, тейк, дедлайн — плюс срок жизни входа (F5).
    let parts = parse_form_fields("ladder3x2..10-pct2-1to1-3600-ttl60").unwrap();
    assert_eq!(parts.entry, "ladder3x2..10");
    assert_eq!(parts.stop, "pct2");
    assert_eq!(parts.take, "1to1");
    assert_eq!(parts.deadline_secs, 3600);
    assert_eq!(parts.ttl.as_deref(), Some("60"));
    assert_eq!(parts.exit, None);
    // Пятипольное имя из плана: `<вход>-<стоп>-<тейк>-<H>-<выход>`.
    let parts = parse_form_fields("ladder3x2..10-pct2-1to1-3600-eat50").unwrap();
    assert_eq!(parts.entry, "ladder3x2..10");
    assert_eq!(parts.exit.as_deref(), Some("eat50"));
    assert_eq!(parts.ttl, None);
    // Хвостовые поля — в любом порядке и вместе (`-ttlwall` — режим F5).
    let parts = parse_form_fields("ladder3x2..10-pct2-1to1-3600-ttlwall-gone20").unwrap();
    assert_eq!(parts.ttl.as_deref(), Some("wall"));
    assert_eq!(parts.exit.as_deref(), Some("gone20"));
    // Прежнее трёхпольное имя + ttl: вход неявный, вердикт читает тройку.
    let parts = parse_form_fields("pct2-1to1-3600-ttl1800").unwrap();
    assert_eq!(parts.entry, "single@fr");
    assert_eq!(
        parse_form("pct2-1to1-3600-ttl1800").unwrap(),
        ("pct2".to_string(), "1to1".to_string(), 3600)
    );
    // Отказы: чужой срок жизни, прежний режим под суффиксом, чужой выход,
    // произвольный хвост, нога-лестница из одной ноги.
    assert!(
        parse_form("pct2-1to1-3600-ttl90").is_err(),
        "ttl не из сетки В-74"
    );
    assert!(
        parse_form("pct2-1to1-3600-ttltouch").is_err(),
        "прежний режим суффикса не несёт"
    );
    assert!(
        parse_form("pct2-1to1-3600-eat").is_err(),
        "порог съедания не число"
    );
    assert!(
        parse_form("pct2-1to1-3600-5").is_err(),
        "хвост не из форм выхода"
    );
    assert!(
        parse_form("ladder1x2..10-pct2-1to1-3600").is_err(),
        "одна нога — не лестница"
    );
    // Полнота: оси — вход × стоп × тейк × дедлайн × ttl (у выхода — своя).
    assert_eq!(
        grid_size_from_labels([
            "pct2-1to1-3600",
            "ladder3x2..10-pct2-1to1-3600",
            "pct2-1to1-3600-ttl60",
            "ladder3x2..10-pct2-1to1-3600-ttl60",
        ])
        .unwrap(),
        4
    );
    assert_ne!(
        grid_size_from_labels([
            "pct2-1to1-3600",
            "ladder3x2..10-pct2-1to1-3600",
            "pct2-1to1-3600-ttl60"
        ])
        .unwrap(),
        3,
        "неполная ось срока жизни — не сетка"
    );
}

/// F8b (аудит 21.09, С-список): колонки причин выхода читаются **по
/// наличию**. Артефакт, снятый на середине внедрения F7 (есть
/// `n_eaten_by_trades`, нет `n_wall_gone` и прочих), обязан читаться, а
/// отсутствующая причина — считаться нулём, не отказом: иначе уже снятые
/// сетки перестали бы считаться вердиктом.
#[test]
fn exit_columns_are_read_when_only_some_of_them_are_present() {
    let dir = tempfile::tempdir().unwrap();
    let forms = "# synthetic partial F7\n\
        symbol,day_utc,form,n_signals,n_skipped,n_fills,sum_net_bps,n_eaten_by_trades\n\
        SOLUSDT,2026-09-08,s1-t1-60,5,0,2,1.000000,2\n";
    std::fs::write(dir.path().join("forms.csv"), forms).unwrap();
    let rounds = "# synthetic partial F7\n\
        symbol,day_utc,form,signal_index,t0_ns,dir,entry_px,exit_px,qty,net_bps,reason\n\
        SOLUSDT,2026-09-08,s1-t1-60,0,0,1,100.0,101.0,1.0,0.500000,eaten_by_trades\n\
        SOLUSDT,2026-09-08,s1-t1-60,1,600000000000,1,100.0,101.0,1.0,0.500000,eaten_by_trades\n";
    std::fs::write(dir.path().join("rounds.csv"), rounds).unwrap();

    let grid = read_grid(dir.path()).expect("часть колонок причин — не отказ");
    let cell = grid.cells.values().next().expect("строка сетки");
    let at = |name: &str| {
        EXIT_REASONS
            .iter()
            .position(|r| *r == name)
            .unwrap_or_else(|| panic!("нет причины {name}"))
    };
    assert_eq!(
        cell.exits[at("eaten_by_trades")],
        2,
        "присутствующая колонка читается целиком"
    );
    assert_eq!(
        cell.exits[at("wall_gone")],
        0,
        "отсутствующая колонка — ноль, а не отказ"
    );
    assert_eq!(
        cell.exits.iter().sum::<u64>(),
        2,
        "чужих причин из отсутствующих колонок не выдумано"
    );
}
