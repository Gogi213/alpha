use super::*;
use crate::commands::lob::test_support::{delta_frame, snap_frame, trade_frame, write_day};
use crate::commands::lob::H3ModeArg;

fn touches_args(root: &std::path::Path) -> TouchesArgs {
    TouchesArgs {
        root: root.to_path_buf(),
        symbol: "SOLUSDT".to_string(),
        h3: H3Args {
            h3_mode: H3ModeArg::Percentile,
            h3_lots: Some(5),
            h3_usd: None,
            h3_strength_pct: None,
            h3_strength_window_bps: None,
        },
        h3_k: None,
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        out: None,
        approach_bps: None,
        approach_min_age_secs: 0,
        moves: None,
        moves_window_ms: None,
        moves_bin_ms: None,
        numbers: None,
        allow_unverified: true,
    }
}

/// Бид 100 родился лучшей ценой — не касание (В-43); продавец бьёт в него
/// 3 лота, на 1000 мс он снят — смерть без касания. Бид 99 стал лучшим
/// (касание 0, фронтран — 10 лотов бида 100 с прошлого кадра, завал — 98 и
/// 99); на 2000 мс бид 100 родился заново лучшей ценой (снова не касание) —
/// 99 ушёл с лучшей цены; на 3000 мс 100 снят — 99 лучший второй раз
/// (индекс 1); на 4000 мс родился 101 — касание 1 у 99 кончилось; на 5000 мс
/// 101 снят — касание 2 у 99, внутри сделка 4 лота, на 6000 мс 99 упал до
/// 1 лота — касание кончилось смертью. Аск 105 и бид 98 касаний не дают.
fn touch_frames() -> Vec<Vec<crate::binlog::Record>> {
    vec![
        snap_frame(0, &[(98, 10), (99, 10), (100, 10)], &[(105, 10)]),
        trade_frame(500, 100, 3),
        delta_frame(1000, &[(100, 0)], &[]),
        delta_frame(2000, &[(100, 10)], &[]),
        delta_frame(3000, &[(100, 0)], &[]),
        delta_frame(4000, &[(101, 10)], &[]),
        delta_frame(5000, &[(101, 0)], &[]),
        trade_frame(5500, 99, 4),
        delta_frame(6000, &[(99, 1)], &[]),
    ]
}

fn read_rows(path: &std::path::Path) -> (Vec<String>, Vec<Vec<String>>) {
    let mut r = csv::Reader::from_path(path).unwrap();
    let header: Vec<String> = r.headers().unwrap().iter().map(str::to_string).collect();
    let rows = r
        .records()
        .map(|rec| rec.unwrap().iter().map(str::to_string).collect())
        .collect();
    (header, rows)
}

fn col<'a>(header: &[String], row: &'a [String], name: &str) -> &'a str {
    let i = header.iter().position(|h| h == name).unwrap();
    &row[i]
}

/// Критерий приёмки таска 35: `lob touches` на фикстуре пишет
/// `touches-<SYMBOL>.csv` с ожидаемыми колонками, по строке на кончившееся
/// касание, с признаками из записи и производными.
#[test]
fn touches_fixture_writes_touch_rows_with_expected_columns() {
    let dir = tempfile::tempdir().unwrap();
    write_day(dir.path(), "SOLUSDT", "2026-09-08", &touch_frames());
    let summary = run_touches(&touches_args(dir.path())).unwrap();
    assert_eq!(summary.days, 1);
    assert_eq!(summary.touches, 3);
    assert_eq!(
        summary.out,
        dir.path().join("touches-SOLUSDT.csv"),
        "имя артефакта — touches-<SYMBOL>.csv"
    );
    let (header, rows) = read_rows(&summary.out);
    assert_eq!(header, TOUCHES_COLUMNS.map(str::to_string).to_vec());
    assert_eq!(rows.len(), 3);
    assert!(
        rows.iter().all(|r| col(&header, r, "price_tick") == "99"),
        "родившиеся лучшей ценой 100/101/105 касаний не дают (В-43)"
    );
    for r in &rows {
        assert_eq!(r.len(), TOUCHES_COLUMNS.len());
    }

    let find = |idx: &str| {
        rows.iter()
            .find(|r| col(&header, r, "touch_index") == idx)
            .unwrap_or_else(|| panic!("нет строки касания {idx}"))
    };
    let t0 = find("0");
    assert_eq!(col(&header, t0, "day_utc"), "2026-09-08");
    assert_eq!(col(&header, t0, "side"), "bid");
    assert_eq!(col(&header, t0, "start_ms"), "1000");
    assert_eq!(col(&header, t0, "end_ms"), "2000");
    assert_eq!(col(&header, t0, "duration_ms"), "1000");
    assert_eq!(col(&header, t0, "birth_ms"), "0");
    assert_eq!(col(&header, t0, "age_ms"), "1000");
    assert_eq!(col(&header, t0, "size_at_touch"), "10");
    assert_eq!(col(&header, t0, "size_max_before"), "10");
    assert_eq!(col(&header, t0, "traded_during"), "0");
    // Кадры раз в секунду: фронтран за секунду до касания (В-45) — те же 10
    // лотов бида 100, что и сметённые последним шагом.
    assert_eq!(col(&header, t0, "frontrun_lots"), "10");
    assert_eq!(col(&header, t0, "swept_lots"), "10");
    assert_eq!(col(&header, t0, "round_zeros"), "0");
    assert_eq!(col(&header, t0, "ended_by_death"), "false");
    assert_eq!(
        col(&header, t0, "stack_levels"),
        "1",
        "окно 25 bps от 99 тиков — 0 тиков: только сам уровень (В-45)"
    );
    // Касание длилось 1000 мс: горизонты 100 мс и 1 с — внутри касания
    // (граница включительна), 10 с и 60 с — нет (В-45 (2)).
    assert_eq!(col(&header, t0, "within_touch_100ms"), "true");
    assert_eq!(col(&header, t0, "within_touch_1s"), "true");
    assert_eq!(col(&header, t0, "within_touch_10s"), "false");
    assert_eq!(col(&header, t0, "within_touch_60s"), "false");
    // Расстояние 99 до середины 102.5 при рождении — |99 − 102.5| / 102.5.
    let dist: f64 = col(&header, t0, "dist_bps").parse().unwrap();
    assert!((dist - 341.463_414).abs() < 1e-3, "dist_bps = {dist}");
    // База — срез как есть на 1000 мс (середина уже шагнула на 99: 204/2);
    // через 100 мс сдвига нет — ноль, не −1 тик; через 1 с середина 205/2 —
    // выше: бид-касание, «в сторону отскока» — плюс.
    assert_eq!(col(&header, t0, "m_100ms"), "0.000000");
    let m_1s: f64 = col(&header, t0, "m_1s").parse().unwrap();
    assert!(m_1s > 0.0, "m_1s = {m_1s}");
    // Подход: за секунду до касания середина падала на бид (205 → 204) — плюс.
    let ap_1s: f64 = col(&header, t0, "approach_1s").parse().unwrap();
    assert!(ap_1s > 0.0, "approach_1s = {ap_1s}");
    assert_eq!(
        col(&header, t0, "approach_10s"),
        "",
        "десяти секунд до касания в записи нет"
    );

    let t1 = find("1");
    assert_eq!(col(&header, t1, "start_ms"), "3000");
    assert_eq!(col(&header, t1, "end_ms"), "4000");
    assert_eq!(col(&header, t1, "frontrun_lots"), "10");
    assert_eq!(col(&header, t1, "ended_by_death"), "false");

    // Касание 2 кончилось смертью: сделка внутри — 4 лота, фронтран — 10
    // лотов бида 101 с кадра 4000.
    let t2 = find("2");
    assert_eq!(col(&header, t2, "start_ms"), "5000");
    assert_eq!(col(&header, t2, "end_ms"), "6000");
    assert_eq!(col(&header, t2, "traded_during"), "4");
    assert_eq!(col(&header, t2, "frontrun_lots"), "10");
    assert_eq!(col(&header, t2, "ended_by_death"), "true");
}

/// Обратимость CSV → `TouchRecord` (`read_touches_csv`): записи трекера из
/// реплея фикстуры и записи, прочитанные из `touches-SOLUSDT.csv`, равны
/// поле в поле, сутки — из `day_utc`. На этом стоит `lob bounce-grid
/// --touches-from` (касания из ночного H3 вместо реплея книги).
#[test]
fn touches_csv_reads_back_the_same_records() {
    let dir = tempfile::tempdir().unwrap();
    write_day(dir.path(), "SOLUSDT", "2026-09-08", &touch_frames());
    let args = touches_args(dir.path());
    let summary = run_touches(&args).unwrap();
    let rows = read_touches_csv(&summary.out).unwrap();
    assert_eq!(rows.len(), 3);

    let cfg = LevelsConfig {
        mode: crate::lob::levels::H3Mode::Percentile { h3_lots: 5 },
        warmup_ms: args.warmup_ms,
        repeat_window_ms: args.repeat_window_ms,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let replay = replay_symbol(dir.path(), "SOLUSDT", cfg).unwrap();
    let expected: Vec<TouchRow> = replay
        .days
        .iter()
        .flat_map(|d| {
            d.touches.iter().map(move |t| TouchRow {
                day: d.day.clone(),
                touch: *t,
                ret_bps: Some(std::array::from_fn(|k| {
                    pre_touch_return_bps_csv(&d.mids, t.start_ms, PRE_TOUCH_MS[k])
                })),
            })
        })
        .collect();
    assert_eq!(rows, expected, "CSV обязан читаться в те же записи");
    // Пустые поля читаются как «нет»: у фикстуры нет соседей в окнах силы.
    assert!(rows.iter().all(|r| r.touch.strength_held_e2[3] == -1));
}

/// S2 плана по сторонам: три колонки хода до касания в конце шапки; на
/// фикстуре (6 с записи) все три пусты — окна 10 мин и длиннее упираются в
/// начало; на ряду, где окно есть, ход считается от среза за окно до базы
/// касания, знак абсолютный. Рядом с касаниями — минутный ряд `mids1m-*.csv`
/// с минутами подряд и закрытием минуты (фикстура — 6 с, одна минута).
#[test]
fn touches_write_pre_touch_returns_and_minute_mids() {
    let dir = tempfile::tempdir().unwrap();
    write_day(dir.path(), "SOLUSDT", "2026-09-08", &touch_frames());
    let summary = run_touches(&touches_args(dir.path())).unwrap();
    let (header, rows) = read_rows(&summary.out);
    assert_eq!(
        &header[header.len() - 3..],
        ["ret_10m_bps", "ret_1h_bps", "ret_4h_bps"]
    );
    for r in &rows {
        for c in ["ret_10m_bps", "ret_1h_bps", "ret_4h_bps"] {
            assert_eq!(col(&header, r, c), "", "окно длиннее записи — пусто");
        }
    }
    let mids = [
        crate::lob::markout::MidSample {
            ts_ms: 0,
            bid_tick: 99,
            ask_tick: 101,
        },
        crate::lob::markout::MidSample {
            ts_ms: 500_000,
            bid_tick: 104,
            ask_tick: 106,
        },
        crate::lob::markout::MidSample {
            ts_ms: 700_000,
            bid_tick: 109,
            ask_tick: 111,
        },
    ];
    // База касания на 700_000 — 110 (2x = 220); срез за 10 мин (≤ 100_000) — первый, 100 (2x = 200).
    let r = pre_touch_return_bps(&mids, 700_000, PRE_TOUCH_MS[0]).unwrap();
    assert!((r - 1000.0).abs() < 1e-9, "+10 % = +1000 bps, получено {r}");
    assert_eq!(pre_touch_return_bps(&mids, 700_000, PRE_TOUCH_MS[1]), None);

    let m = mids1m_path(&summary.out, "SOLUSDT");
    assert_eq!(m, dir.path().join("mids1m-SOLUSDT.csv"));
    let (mh, mrows) = read_rows(&m);
    assert_eq!(mh, ["minute_ms", "mid2x"]);
    assert!(!mrows.is_empty(), "минимум одна минута: {mrows:?}");
    let minutes: Vec<i64> = mrows.iter().map(|r| r[0].parse().unwrap()).collect();
    assert!(
        minutes.windows(2).all(|w| w[1] == w[0] + 60_000),
        "минуты подряд: {minutes:?}"
    );
    assert!(mrows.iter().all(|r| r[1].parse::<i64>().unwrap() > 0));
}

/// F1 этапа F: `--approach-bps` пишет `approaches-<SYMBOL>.csv` рядом с
/// касаниями, а `read_approaches_csv` читает те же записи поле в поле.
/// Полоса 700 bps — от фикстуры: аск 105 против бид-стены 99 это 606 bps;
/// взвод на кадре 2000 (стена не лучшая: вернулся бид 100), снятие касанием
/// на 3000 (100 снят — стена стала лучшей). Повторного взвода нет: после
/// снятия касанием цена за `2 × D` не уходила.
#[test]
fn approaches_csv_reads_back_the_same_records() {
    let dir = tempfile::tempdir().unwrap();
    write_day(dir.path(), "SOLUSDT", "2026-09-08", &touch_frames());
    let mut args = touches_args(dir.path());
    args.approach_bps = Some(700);
    let summary = run_touches(&args).unwrap();
    assert_eq!(summary.approaches, 1);
    let path = summary.approaches_out.clone().expect("файл подходов");
    assert_eq!(path, dir.path().join("approaches-SOLUSDT.csv"));
    let (header, rows) = read_rows(&path);
    assert_eq!(header, APPROACHES_COLUMNS.map(str::to_string).to_vec());
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].len(), APPROACHES_COLUMNS.len());
    let r = &rows[0];
    assert_eq!(col(&header, r, "day_utc"), "2026-09-08");
    assert_eq!(col(&header, r, "side"), "bid");
    assert_eq!(col(&header, r, "price_tick"), "99");
    assert_eq!(col(&header, r, "approach_index"), "0");
    assert_eq!(col(&header, r, "arm_ms"), "2000");
    assert_eq!(col(&header, r, "age_ms"), "2000");
    assert_eq!(col(&header, r, "arm_dist_bps"), "606");
    assert_eq!(col(&header, r, "best_opp_tick"), "105");
    assert_eq!(col(&header, r, "best_own_tick"), "100");
    assert_eq!(col(&header, r, "disarm_ms"), "3000");
    assert_eq!(col(&header, r, "duration_ms"), "1000");
    assert_eq!(col(&header, r, "touch_start_ms"), "3000");
    assert_eq!(col(&header, r, "disarm_reason"), "touch");
    // Обратимость: CSV читается в те же записи, что отдаёт трекер.
    let read = read_approaches_csv(&path).unwrap();
    let cfg = LevelsConfig {
        mode: crate::lob::levels::H3Mode::Percentile { h3_lots: 5 },
        warmup_ms: args.warmup_ms,
        repeat_window_ms: args.repeat_window_ms,
        approach_bps: Some(700),
        approach_min_age_ms: 0,
    };
    let replay = replay_symbol(dir.path(), "SOLUSDT", cfg).unwrap();
    let expected: Vec<ApproachRow> = replay
        .days
        .iter()
        .flat_map(|d| {
            d.approaches.iter().map(move |a| ApproachRow {
                day: d.day.clone(),
                approach: *a,
            })
        })
        .collect();
    assert_eq!(read, expected, "CSV обязан читаться в те же записи");
}
