use super::*;

const MIN: i64 = 60 * 1_000_000_000;

fn mem(k: u32, t_min: i64) -> RetreatMemory {
    RetreatMemory::new(RetreatCfg {
        k_ticks: k,
        half_life_ns: t_min * MIN,
    })
}

#[test]
fn no_stop_means_no_offset() {
    assert_eq!(mem(8, 30).offset_ticks(10 * MIN), 0);
}

#[test]
fn offset_halves_every_half_life_and_floors() {
    let mut m = mem(8, 30);
    m.on_stop(100 * MIN);
    assert_eq!(m.offset_ticks(100 * MIN), 8);
    assert_eq!(m.offset_ticks(100 * MIN + 30 * MIN), 4);
    assert_eq!(m.offset_ticks(100 * MIN + 60 * MIN), 2);
    assert_eq!(m.offset_ticks(100 * MIN + 90 * MIN), 1);
    // пол-периода: 8·2^(−0,5) ≈ 5,66 → 5
    assert_eq!(m.offset_ticks(100 * MIN + 15 * MIN), 5);
    assert_eq!(m.offset_ticks(100 * MIN + 91 * MIN), 0);
}

#[test]
fn repeated_stop_resets_to_k_not_sum() {
    let mut m = mem(4, 15);
    m.on_stop(0);
    m.on_stop(15 * MIN);
    assert_eq!(m.offset_ticks(15 * MIN), 4);
    assert_eq!(m.offset_ticks(30 * MIN), 2);
}

#[test]
fn carry_window_is_where_the_offset_is_still_positive() {
    for (k, t) in [(1, 5), (2, 15), (3, 30), (8, 240)] {
        let cfg = RetreatCfg {
            k_ticks: k,
            half_life_ns: t * MIN,
        };
        let w = carry_window_ns(cfg);
        let mut m = RetreatMemory::new(cfg);
        m.on_stop(0);
        assert_eq!(m.offset_ticks(w + 1), 0, "k={k} T={t}");
        if k > 1 {
            assert!(m.offset_ticks(w / 2) > 0, "k={k} T={t}");
        }
    }
}
