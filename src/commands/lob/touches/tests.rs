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
