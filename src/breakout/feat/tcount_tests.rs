//! Тесты `tcount` (TK-638): границы окон, переход через сутки, отсутствующие сутки, прогон по CSV на синтетике (времена в сутках — по возрастанию, как у `PxSrc`).

use super::f12::{PxDay, PxSrc};
use super::tcount::{count_range, counts_at, run, Counts, TcCfg};
use anyhow::Result;
use std::collections::BTreeMap;

const MIN: i64 = 60_000;
const DAY: i64 = 86_400_000;
/// 2026-09-01, сутки от 1970.
const D0: i64 = 20_697;

#[test]
fn count_range_is_half_open() {
    let t = [10, 20, 20, 30, 40];
    assert_eq!(count_range(&t, 20, 40), 3); // 20, 20, 30; 40 — вне
    assert_eq!(count_range(&t, 0, 10), 0);
    assert_eq!(count_range(&t, 10, 11), 1);
    assert_eq!(count_range(&t, 50, 60), 0);
    assert_eq!(count_range(&t, 40, 20), 0); // перевёрнутое окно — пусто
}

#[test]
fn windows_end_at_signal_minute_start() {
    // сигнал в 12:30:45.500; окна — [11:30, 12:30) и [вчера 12:30, 12:30)
    let sig = D0 * DAY + 12 * 60 * MIN + 30 * MIN + 45_500;
    let end = D0 * DAY + 12 * 60 * MIN + 30 * MIN;
    let cur = [
        end - 60 * MIN - 1, // раньше окна 60 мин
        end - 60 * MIN,     // ровно начало окна 60 мин — внутри
        end - 1,            // последняя мс перед минутой сигнала — внутри
        end,                // начало минуты сигнала — вне
        sig - 1,            // в минуте сигнала — вне
        sig + 1000,         // после сигнала — вне
    ];
    let prev = [end - DAY - 1, end - DAY, end - DAY + 5];
    let days: Vec<(i64, Option<&[i64]>)> = vec![(D0 - 1, Some(&prev)), (D0, Some(&cur))];
    let c = counts_at(&days, sig);
    // 60 мин: end − 60 мин и end − 1; 24 ч: те же две, ещё end − 60 мин − 1 (текущие) и end − DAY, end − DAY + 5 (предыдущие)
    assert_eq!(
        c,
        Counts {
            n60: 2,
            n24: 5,
            m60: false,
            m24: false
        }
    );
}

#[test]
fn midnight_crossing_uses_previous_day_for_60_min() {
    // сигнал в 00:20 — окно 60 мин начинается в 23:20 предыдущих суток
    let end = D0 * DAY + 20 * MIN;
    let sig = end + 1234;
    let prev = [
        D0 * DAY - 61 * MIN,
        D0 * DAY - 41 * MIN,
        D0 * DAY - 40 * MIN,
    ];
    let cur = [D0 * DAY + MIN, D0 * DAY + 19 * MIN];
    let days: Vec<(i64, Option<&[i64]>)> = vec![(D0 - 1, Some(&prev)), (D0, Some(&cur))];
    let c = counts_at(&days, sig);
    // окно 60 мин — [23:20, 00:20): запись −40 мин (ровно начало окна) и две текущих; −41 и −61 мин — раньше окна
    assert_eq!(c.n60, 3, "{c:?}");
    assert_eq!(c.n24, 5, "{c:?}"); // все пять внутри 24 ч
    assert!(!c.m60 && !c.m24);
}

#[test]
fn missing_previous_day_flags_both_windows_only_when_needed() {
    let cur = [D0 * DAY + 3 * 60 * MIN + 5 * MIN];
    // сигнал в 03:30: окно 60 мин целиком в текущих сутках, окно 24 ч задевает предыдущие
    let sig = D0 * DAY + 3 * 60 * MIN + 30 * MIN + 1;
    let days: Vec<(i64, Option<&[i64]>)> = vec![(D0 - 1, None), (D0, Some(&cur))];
    let c = counts_at(&days, sig);
    assert_eq!((c.n60, c.n24), (1, 1));
    assert!(!c.m60 && c.m24, "{c:?}");
    // сигнал в 00:10: предыдущие сутки нужны и окну 60 мин
    let sig = D0 * DAY + 10 * MIN;
    let c = counts_at(&days, sig);
    assert!(c.m60 && c.m24, "{c:?}");
    // отсутствуют сутки сигнала
    let days: Vec<(i64, Option<&[i64]>)> = vec![(D0 - 1, Some(&[])), (D0, None)];
    let c = counts_at(&days, D0 * DAY + 5 * 60 * MIN);
    assert!(c.m60 && c.m24, "{c:?}");
}

#[test]
fn signal_at_day_start_ignores_current_day() {
    // сигнал в 00:00:00.300 — окно кончается в 00:00:00.000, текущие сутки не задеты (файла нет — не флаг)
    let prev = [D0 * DAY - 2 * 60 * MIN, D0 * DAY - 30 * MIN, D0 * DAY - MIN];
    let days: Vec<(i64, Option<&[i64]>)> = vec![(D0 - 1, Some(&prev)), (D0, None)];
    let c = counts_at(&days, D0 * DAY + 300);
    assert_eq!((c.n60, c.n24), (2, 3));
    assert!(!c.m60 && !c.m24, "{c:?}");
}

struct MemSrc {
    days: BTreeMap<(String, i64), Vec<i64>>,
}

impl PxSrc for MemSrc {
    fn day(&self, sym: &str, abs_day: i64) -> Result<Option<PxDay>> {
        Ok(self.days.get(&(sym.to_string(), abs_day)).map(|t| PxDay {
            p: vec![1; t.len()],
            t: t.clone(),
            unsorted: false,
        }))
    }
}

#[test]
fn run_counts_rows_in_input_order() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let eps = dir.path().join("eps.csv");
    let out = dir.path().join("out.csv");
    // sig_ms — от base-date = D0: ВТОРАЯ монета раньше первой в файле, порядок входа сохраняется
    let s1 = DAY + 2 * 60 * MIN + 17 * MIN + 500; // сутки 1, 02:17.5
    let s2 = DAY + 2 * 60 * MIN + 17 * MIN + 600;
    std::fs::write(
        &eps,
        format!("sym,sig_ms,x\nBBB,{s2},1\nAAA,{s1},2\nAAA,{s1},3\n"),
    )?;
    let end = (D0 + 1) * DAY + 2 * 60 * MIN + 17 * MIN;
    let mut days = BTreeMap::new();
    days.insert(
        ("AAA".to_string(), D0 + 1),
        vec![end - 30 * MIN, end - 10 * MIN, end + 5],
    );
    days.insert(
        ("AAA".to_string(), D0),
        vec![end - DAY - 1, end - DAY + MIN],
    );
    days.insert(
        ("BBB".to_string(), D0 + 1),
        vec![end - 61 * MIN, end - 59 * MIN],
    );
    let cfg = TcCfg {
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
    assert_eq!(lines[0], "sym,sig_ms,n60,n24,m60,m24");
    // BBB: n60 = 1 (−59 мин), n24 = 2; предыдущих суток нет → m24 = 1 (окно 24 ч задевает), m60 = 0
    assert_eq!(lines[1], format!("BBB,{s2},1,2,0,1"));
    // AAA: n60 = 2 (−30, −10 мин; +5 мс — после начала минуты сигнала, вне), n24 = 2 + 1 (end − DAY + MIN)
    assert_eq!(lines[2], format!("AAA,{s1},2,3,0,0"));
    assert_eq!(lines[3], lines[2]);
    assert_eq!(sum.miss_rows, 1);
    Ok(())
}
