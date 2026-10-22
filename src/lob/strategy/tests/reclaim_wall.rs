//! Тесты стратегии — Г-88 (reclaim): сигнал по сделкам, не по книге.

#[cfg(feature = "r2")]
use super::*;

/// Г-88, прочтение Б: стена на уровне снята при заколе (глубина 99 → 0), но возврат по сделкам всё равно даёт сигнал —
/// заявка ставится, круг исполняется; без закола (ни одной сделки ≤ 98) входа нет. Только с `r2` (`eff()` иначе OFF).
#[cfg(feature = "r2")]
#[test]
pub(super) fn reclaim_places_the_entry_after_return_even_if_the_wall_vanished_at_the_poke() {
    let mut plan = f7_plan(0.0, 0.0, 10.0);
    if let TradePlan::Bounce { pyramid, .. } = &mut plan {
        pyramid.reclaim_depth_bps = 100;
        pyramid.reclaim_pause_ms = 0;
    }
    let head = |poke: bool| {
        let mut feed = vec![
            depth_at(0, true, 100.0, 5.0),
            depth_at(0, true, 99.0, 10.0),
            depth_at(0, false, 101.0, 5.0),
            // Стена снята при заколе.
            depth_at(S + S / 5, true, 99.0, 0.0),
        ];
        if poke {
            feed.push(trade_at(S + 2 * S / 5, true, 98.0, 1.0));
        }
        feed.push(trade_at(S + 3 * S / 5, true, 99.0, 1.0));
        feed.push(trade_at(S + 4 * S / 5, true, 100.0, 6.0));
        feed.push(depth_at(40 * S, true, 100.0, 0.0));
        feed
    };
    let with = f7_run(plan, &head(true));
    assert_eq!(
        with.fills.len(),
        1,
        "закол и возврат при снятой стене: вход есть {with:?}"
    );
    let without = f7_run(plan, &head(false));
    assert!(
        without.fills.is_empty(),
        "закола нет — входа нет {without:?}"
    );
}

/// Г-135 (`postsweep<R>`): вход ставится на первой сделке, отошедшей на `R` от минимума сделок после взвода; без отхода
/// и при отходе позже 30 с входа нет. Книга сигнал не гасит (стена снята при свипе). Только с `r2`.
#[cfg(feature = "r2")]
#[test]
pub(super) fn postsweep_places_the_entry_on_the_rise_within_the_window_only() {
    let mut plan = f7_plan(0.0, 0.0, 10.0);
    if let TradePlan::Bounce {
        pyramid,
        entry_ttl_ns,
        ..
    } = &mut plan
    {
        pyramid.postsweep_bps = 100;
        *entry_ttl_ns = 60 * S; // окно postsweep — min(TTL, 30 с)
    }
    let run = |sweep_s: i64, rise_s: i64, rise_px: f64| {
        let feed = vec![
            depth_at(0, true, 100.0, 5.0),
            depth_at(0, true, 99.0, 10.0),
            depth_at(0, false, 101.0, 5.0),
            depth_at(S + S / 5, true, 99.0, 0.0),
            trade_at(sweep_s * S, true, 98.0, 1.0),
            trade_at(rise_s * S, true, rise_px, 1.0),
            trade_at(rise_s * S + S / 5, true, 100.0, 6.0),
            depth_at(80 * S, true, 100.0, 0.0),
        ];
        f7_run(plan, &feed)
    };
    assert_eq!(run(2, 4, 99.0).fills.len(), 1, "отход в окне: вход есть");
    assert!(run(2, 4, 98.0).fills.is_empty(), "отхода нет: входа нет");
    assert!(
        run(2, 40, 99.0).fills.is_empty(),
        "отход после 30 с: входа нет"
    );
}
