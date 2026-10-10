//! С-13: таблица приоритетов выходов — каждая пара правил и полнота таблицы.

use super::*;

fn hit(i: usize) -> (ExitAt, ExitReason, f64) {
    (ExitAt::Taker(i as f64), ExitReason::Stop, 1.0 + i as f64)
}

#[test]
fn table_lists_every_rule_once() {
    let mut seen = [false; EXIT_PRIORITY.len()];
    for r in EXIT_PRIORITY {
        assert!(!seen[r as usize], "{r:?} дважды");
        seen[r as usize] = true;
    }
    assert!(seen.iter().all(|&s| s));
}

#[test]
fn higher_rule_wins_every_pair() {
    for (i, &hi) in EXIT_PRIORITY.iter().enumerate() {
        for &lo in &EXIT_PRIORITY[i + 1..] {
            let mut hits = ExitHits::NONE;
            hits.set(lo, true, hit(1));
            hits.set(hi, true, hit(0));
            assert_eq!(pick_exit(&hits), Some((hi, hit(0))), "{hi:?} над {lo:?}");
        }
    }
}

#[test]
fn single_rule_and_nothing() {
    assert_eq!(pick_exit(&ExitHits::NONE), None);
    for &r in &EXIT_PRIORITY {
        let mut hits = ExitHits::NONE;
        hits.set(r, true, hit(3));
        hits.set(r, false, hit(4));
        assert_eq!(pick_exit(&hits), Some((r, hit(3))));
    }
}

#[test]
fn documented_order() {
    use ExitRule::*;
    let pos = |r: ExitRule| EXIT_PRIORITY.iter().position(|&x| x == r).unwrap();
    assert!(pos(Stop) < pos(Take) && pos(EatenAll) < pos(Trail) && pos(Trail) < pos(Take));
    assert!(pos(Take) < pos(EatenHalf) && pos(Early) < pos(Deadline) && pos(GoneExit) < pos(Early));
}
