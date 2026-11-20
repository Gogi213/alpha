//! Тесты `tsize` (TK-639): границы окон 1 / 6 / 12 / 24 ч, переход через сутки, отсутствующие сутки, согласие с `tcount` по `n60` / `n24`, прогон по CSV на синтетике.

use super::tcount::counts_at;
use super::trades::{DaySrc, DayTrades};
use super::tsize::{run, sizes_at, DaySlices, TsCfg};
use anyhow::Result;
use std::collections::BTreeMap;

const MIN: i64 = 60_000;
const HOUR: i64 = 60 * MIN;
const DAY: i64 = 86_400_000;
/// 2026-09-01, сутки от 1970.
const D0: i64 = 20_697;

/// Оборот записи в тестах — её порядковый номер в сутках + 1 (разные, чтобы суммы различали состав окна).
fn vs(n: usize) -> Vec<f64> {
    (1..=n).map(|x| x as f64).collect()
}

#[test]
fn windows_are_half_open_at_signal_minute_start() {
    // сигнал в 12:30:45.500; окна кончаются в 12:30:00.000
    let end = D0 * DAY + 12 * HOUR + 30 * MIN;
    let sig = end + 45_500;
    let cur = [
        end - 60 * MIN - 1, // раньше окна 1 ч, внутри 6 ч
        end - 60 * MIN,     // ровно начало окна 1 ч
        end - 1,            // последняя мс перед минутой сигнала
        end,                // начало минуты сигнала — вне всех окон
        sig + 1000,         // после сигнала — вне
    ];
    let v = vs(cur.len());
    let days: Vec<DaySlices> = vec![(D0, Some((&cur, &v)))];
    let s = sizes_at(&days, sig);
    // 1 ч: записи 2 и 3 (оборот 2 + 3); 6 ч / 12 ч: записи 1…3 (1 + 2 + 3)
    assert_eq!(s.n, [2, 3, 3, 3]);
    assert_eq!(s.v, [5.0, 6.0, 6.0, 6.0]);
    assert_eq!(s.m, [false; 4]);
}

#[test]
fn six_and_twelve_hour_windows_cross_midnight() {
    // сигнал в 04:00:10 — окно 6 ч начинается в 22:00 предыдущих суток, окно 12 ч — в 16:00, окно 1 ч целиком в текущих сутках
    let end = D0 * DAY + 4 * HOUR;
    let sig = end + 10_000;
    let prev = [
        end - 24 * HOUR - 1, // раньше окна 24 ч
        end - 24 * HOUR,     // ровно начало окна 24 ч
        end - 12 * HOUR - 1, // раньше окна 12 ч, внутри 24 ч
        end - 12 * HOUR,     // начало окна 12 ч
        end - 6 * HOUR - 1,  // раньше окна 6 ч
        end - 6 * HOUR,      // начало окна 6 ч
        D0 * DAY - 1,        // последняя мс предыдущих суток
    ];
    let cur = [D0 * DAY, end - 60 * MIN, end - 1];
    let (pv, cv) = (vs(prev.len()), vs(cur.len()));
    let days: Vec<DaySlices> = vec![(D0 - 1, Some((&prev, &pv))), (D0, Some((&cur, &cv)))];
    let s = sizes_at(&days, sig);
    assert_eq!(s.n, [2, 5, 7, 9]);
    // оборот: 1 ч — текущие 2, 3; 6 ч — предыдущие 6, 7 и все текущие 1…3; 12 ч — предыдущие 4…7 и текущие; 24 ч — предыдущие 2…7 и текущие
    assert_eq!(
        s.v,
        [
            5.0,
            6.0 + 7.0 + 6.0,
            4.0 + 5.0 + 6.0 + 7.0 + 6.0,
            2.0 + 3.0 + 4.0 + 5.0 + 6.0 + 7.0 + 6.0
        ]
    );
    assert_eq!(s.m, [false; 4]);
}

#[test]
fn missing_day_flags_only_windows_that_touch_it() {
    let cur = [D0 * DAY + 13 * HOUR];
    let cv = vs(1);
    // сигнал в 14:30: 1 ч и 6 ч целиком в текущих сутках, 12 ч начинается в 02:30 (тоже текущие), 24 ч задевает предыдущие
    let sig = D0 * DAY + 14 * HOUR + 30 * MIN + 1;
    let days: Vec<DaySlices> = vec![(D0 - 1, None), (D0, Some((&cur, &cv)))];
    let s = sizes_at(&days, sig);
    assert_eq!(s.n, [0, 1, 1, 1]); // запись в 13:00 — раньше окна 1 ч [13:30, 14:30)
    assert_eq!(s.m, [false, false, false, true]);
    // сигнал в 05:10: 6 ч, 12 ч и 24 ч задевают предыдущие сутки, 1 ч — нет
    let sig = D0 * DAY + 5 * HOUR + 10 * MIN;
    let s = sizes_at(&days, sig);
    assert_eq!(s.m, [false, true, true, true]);
    // сигнал в 00:20: предыдущие сутки нужны и окну 1 ч
    let sig = D0 * DAY + 20 * MIN;
    assert_eq!(sizes_at(&days, sig).m, [true; 4]);
    // сигнал ровно в 00:00:00.300: окна кончаются на 00:00:00.000, текущие сутки не задеты
    let prev = [D0 * DAY - 2 * HOUR];
    let pv = vs(1);
    let days: Vec<DaySlices> = vec![(D0 - 1, Some((&prev, &pv))), (D0, None)];
    let s = sizes_at(&days, D0 * DAY + 300);
    assert_eq!(s.n, [0, 1, 1, 1]);
    assert_eq!(s.m, [false; 4]);
}

#[test]
fn counts_agree_with_tcount() {
    let end = D0 * DAY + 9 * HOUR + 17 * MIN;
    let sig = end + 123;
    // записи лежат внутри своих суток (`tcount` считает по всему срезу суток, `tsize` — только по суткам, что окно задевает)
    let prev: Vec<i64> = (0..24).map(|k| end - DAY + k * 37 * MIN).collect();
    let cur: Vec<i64> = (0..80).map(|k| D0 * DAY + k * 7 * MIN).collect();
    let (pv, cv) = (vs(prev.len()), vs(cur.len()));
    let sz = sizes_at(
        &[(D0 - 1, Some((&prev, &pv))), (D0, Some((&cur, &cv)))],
        sig,
    );
    let tc = counts_at(&[(D0 - 1, Some(&prev)), (D0, Some(&cur))], sig);
    assert_eq!((sz.n[0], sz.n[3]), (tc.n60, tc.n24));
    assert_eq!((sz.m[0], sz.m[3]), (tc.m60, tc.m24));
}

struct MemSrc {
    days: BTreeMap<(String, i64), (Vec<i64>, Vec<f64>)>,
}

impl DaySrc for MemSrc {
    fn day(&self, sym: &str, abs_day: i64) -> Result<Option<DayTrades>> {
        Ok(self
            .days
            .get(&(sym.to_string(), abs_day))
            .map(|(t, v)| DayTrades {
                t: t.clone(),
                v: v.clone(),
                buy: vec![true; t.len()],
                unsorted: false,
            }))
    }
}

#[test]
fn run_rows_in_input_order() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let eps = dir.path().join("eps.csv");
    let out = dir.path().join("out.csv");
    // sig_ms — от base-date = D0: ВТОРАЯ монета раньше первой в файле, порядок входа сохраняется
    let s1 = DAY + 7 * HOUR + 17 * MIN + 500; // сутки 1, 07:17.5
    let s2 = DAY + 7 * HOUR + 17 * MIN + 600;
    std::fs::write(
        &eps,
        format!("sym,sig_ms,x\nBBB,{s2},1\nAAA,{s1},2\nAAA,{s1},3\n"),
    )?;
    let end = (D0 + 1) * DAY + 7 * HOUR + 17 * MIN;
    let mut days = BTreeMap::new();
    days.insert(
        ("AAA".to_string(), D0 + 1),
        (
            vec![end - 7 * HOUR, end - 30 * MIN, end - 10 * MIN, end + 5],
            vec![10.0, 20.0, 30.0, 40.0],
        ),
    );
    days.insert(
        ("AAA".to_string(), D0),
        (vec![end - DAY - 1, end - DAY + MIN], vec![1.0, 2.0]),
    );
    days.insert(
        ("BBB".to_string(), D0 + 1),
        (vec![end - 61 * MIN, end - 59 * MIN], vec![5.0, 7.0]),
    );
    let cfg = TsCfg {
        eps: eps.clone(),
        out: out.clone(),
        base_date: "2026-09-01".to_string(),
        threads: 2,
        coins: None,
    };
    let sum = run(&cfg, &MemSrc { days })?;
    assert_eq!((sum.rows, sum.coins), (3, 2));
    let text = std::fs::read_to_string(&out)?;
    let lines: Vec<&str> = text.lines().collect();
    assert_eq!(
        lines[0],
        "sym,sig_ms,n60,n360,n720,n1440,v60,v360,v720,v1440,m60,m360,m720,m1440"
    );
    // BBB: 1 ч — запись −59 мин (7), 6 / 12 / 24 ч — обе (5 + 7); предыдущих суток нет → m = 1 у окон 12 ч (начало 19:17 предыдущих суток) и 24 ч, у 6 ч — 0
    assert_eq!(
        lines[1],
        format!("BBB,{s2},1,2,2,2,7.0000,12.0000,12.0000,12.0000,0,0,1,1")
    );
    // AAA: 1 ч и 6 ч — −30, −10 мин (20 + 30; +5 мс — вне); 12 ч — ещё −7 ч (10); 24 ч — ещё запись предыдущих суток end − DAY + 1 мин (2; end − DAY − 1 — раньше окна)
    assert_eq!(
        lines[2],
        format!("AAA,{s1},2,2,3,4,50.0000,50.0000,60.0000,62.0000,0,0,0,0")
    );
    assert_eq!(lines[3], lines[2]);
    assert_eq!(sum.miss_rows, 1);
    Ok(())
}
