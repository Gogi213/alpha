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
        },
        h3_k: None,
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        out: None,
    }
}

/// Бид 100 — лучшая цена с рождения (касание 0 с кадра 0), продавец бьёт в
/// него 3 лота, на 1000 мс он снят — касание кончилось смертью; бид 99
/// стал лучшим (касание 0, фронтран — 10 лотов бида 100 с прошлого кадра,
/// завал — 98 и 99); на 2000 мс бид 100 родился заново лучшей ценой — 99
/// ушёл с лучшей цены; на 3000 мс 100 снят — 99 лучший второй раз (индекс 1);
/// на 4000 мс родился 101 — касание 1 у 99 кончилось. Аск 105 и биды 98/101
/// касаний до конца записи не закончили — строк не дают.
fn touch_frames() -> Vec<Vec<crate::binlog::Record>> {
    vec![
        snap_frame(0, &[(98, 10), (99, 10), (100, 10)], &[(105, 10)]),
        trade_frame(500, 100, 3),
        delta_frame(1000, &[(100, 0)], &[]),
        delta_frame(2000, &[(100, 10)], &[]),
        delta_frame(3000, &[(100, 0)], &[]),
        delta_frame(4000, &[(101, 10)], &[]),
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
    assert_eq!(summary.touches, 4);
    assert_eq!(
        summary.out,
        dir.path().join("touches-SOLUSDT.csv"),
        "имя артефакта — touches-<SYMBOL>.csv"
    );
    let (header, rows) = read_rows(&summary.out);
    assert_eq!(header, TOUCHES_COLUMNS.map(str::to_string).to_vec());
    assert_eq!(rows.len(), 4);

    let find = |tick: &str, idx: &str| {
        rows.iter()
            .find(|r| {
                col(&header, r, "price_tick") == tick && col(&header, r, "touch_index") == idx
            })
            .unwrap_or_else(|| panic!("нет строки тик {tick} касание {idx}"))
    };
    let t100 = find("100", "0");
    assert_eq!(col(&header, t100, "day_utc"), "2026-09-08");
    assert_eq!(col(&header, t100, "side"), "bid");
    assert_eq!(col(&header, t100, "start_ms"), "0");
    assert_eq!(col(&header, t100, "end_ms"), "1000");
    assert_eq!(col(&header, t100, "duration_ms"), "1000");
    assert_eq!(col(&header, t100, "age_ms"), "0");
    assert_eq!(col(&header, t100, "traded_during"), "3");
    assert_eq!(col(&header, t100, "frontrun_lots"), "0");
    assert_eq!(col(&header, t100, "round_zeros"), "2");
    assert_eq!(col(&header, t100, "ended_by_death"), "true");
    assert_eq!(
        col(&header, t100, "m_1s"),
        "",
        "касание с первого кадра: среза до него нет — пусто, не ноль"
    );

    let t99 = find("99", "0");
    assert_eq!(col(&header, t99, "start_ms"), "1000");
    assert_eq!(col(&header, t99, "end_ms"), "2000");
    assert_eq!(col(&header, t99, "age_ms"), "1000");
    assert_eq!(col(&header, t99, "size_at_touch"), "10");
    assert_eq!(col(&header, t99, "size_max_before"), "10");
    assert_eq!(col(&header, t99, "frontrun_lots"), "10");
    assert_eq!(col(&header, t99, "stack_levels"), "2");
    assert_eq!(col(&header, t99, "ended_by_death"), "false");
    // Середина 205/2 → 204/2 за секунду после касания: бид-касание, цена
    // пошла вниз — markout «в сторону отскока» отрицательный.
    let m_1s: f64 = col(&header, t99, "m_1s").parse().unwrap();
    assert!(m_1s < 0.0, "m_1s = {m_1s}");
    // Расстояние 99 до середины 102.5 при рождении — |99 − 102.5| / 102.5.
    let dist: f64 = col(&header, t99, "dist_bps").parse().unwrap();
    assert!((dist - 341.463_414).abs() < 1e-3, "dist_bps = {dist}");
    assert_eq!(
        col(&header, t99, "approach_1s"),
        "",
        "до касания секунды записи нет"
    );

    let t99_second = find("99", "1");
    assert_eq!(col(&header, t99_second, "start_ms"), "3000");
    assert_eq!(col(&header, t99_second, "end_ms"), "4000");
    assert_eq!(col(&header, t99_second, "frontrun_lots"), "10");
    // Середина за секунду до касания шла вверх (204 → 205 удвоенная): от
    // бида — знак «к уровню» отрицательный.
    let ap_1s: f64 = col(&header, t99_second, "approach_1s").parse().unwrap();
    assert!(ap_1s < 0.0, "approach_1s = {ap_1s}");

    // Бид 100 родился дважды, оба раза лучшей ценой: два касания с
    // индексом 0 — у каждого рождения свой счёт, объём через разрыв не
    // переносится (`traded_during` второго — ноль).
    let rows_100: Vec<&Vec<String>> = rows
        .iter()
        .filter(|r| col(&header, r, "price_tick") == "100")
        .collect();
    assert_eq!(rows_100.len(), 2);
    let reborn = rows_100[1];
    assert_eq!(col(&header, reborn, "touch_index"), "0");
    assert_eq!(col(&header, reborn, "start_ms"), "2000");
    assert_eq!(col(&header, reborn, "end_ms"), "3000");
    assert_eq!(col(&header, reborn, "traded_during"), "0");
    assert_eq!(col(&header, reborn, "ended_by_death"), "true");
}
