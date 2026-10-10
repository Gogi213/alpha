//! Запрет 1 `interfaces.md` (С-04, ревью 10.10): ноль аллокаций на событие после прогрева — по фазам круга.
//! Idle меряет `tests::on_event_allocates_nothing_per_call_while_the_book_is_not_ready`; здесь — фазы, где
//! стратегия ждёт с открытым ордером или позицией. Переходы (submit/cancel уходят в учёт ордеров крейта, он не
//! в этой зоне) не считаются: меряются вызовы, начатые и кончившиеся в одной фазе.
use super::tests::{depth_at, seam6_backtest, trade_at};
use super::*;
use crate::lob::backtest::SIGMA_LONG;
use hftbacktest::types::ElapseResult;

/// События в каждой измеряемой фазе — не меньше (запрет 1: 10⁵ на фазу).
const PER_PHASE: usize = 100_000;
/// Шаг ленты = шаг `elapse`: на вызов `on_event` приходится одно событие.
const STEP: i64 = 100_000_000;

fn plan() -> TradePlan {
    TradePlan::Bounce {
        entry_px: 100.0,
        stop_px: 90.0,
        take_px: 110.0,
        deadline_ns: i64::MAX / 4,
        entry_ttl_ns: i64::MAX / 4,
        post_only: false,
        trail_bps: 0.0,
        trail_activate_bps: 0.0,
        grid_legs: 1,
        grid_step_px: 0.0,
        ladder: EntryLadder::NONE,
        early_exit_ns: 0,
        level_floor_qty: 0.0,
        band_exit_bps: 0.0,
        level_px: 99.0,
        tick_px: 1.0,
        take_frac: 1.0,
        eaten_half_pct: 0.0,
        eaten_all_pct: 0.0,
        eaten_half_frac: 0.0,
        level_qty: 0.0,
        lot_qty: 1.0,
        exit_eat_pct: 0.0,
        wall_eat: crate::lob::strategy::WallEatExit::OFF,
        pyramid: crate::lob::strategy::PyramidCfg::OFF,
        exit_gone_pct: 0.0,
        gone_trail_bps: 0.0,
        gone_stop: crate::lob::strategy::GoneStop::Off,
    }
}

fn phase_name(p: &Phase) -> &'static str {
    match p {
        Phase::Idle => "Idle",
        Phase::EntryPending { .. } => "EntryPending",
        Phase::Holding { .. } => "Holding",
        Phase::ExitPending { .. } => "ExitPending",
        Phase::ExitCancelPending { .. } => "ExitCancelPending",
        Phase::CancelPending { .. } => "CancelPending",
    }
}

/// Гоняет `on_event` по ленте и меряет вызовы, начатые и кончившиеся в одной из фаз `names` (вызовы и аллокации).
fn measure_phases(feed: &[Event], names: [&str; 2]) -> ([usize; 2], [u64; 2]) {
    let mut hbt = seam6_backtest(feed);
    let mut state = StrategyState::with_plan(0, SIGMA_LONG, 1.0, 1, plan());
    let mut calls = [0usize; 2];
    let mut allocs = [0u64; 2];
    loop {
        let r = hbt.elapse(STEP).unwrap();
        let before = phase_name(&state.phase);
        let (res, counts) = crate::alloc_count::measure(|| on_event(&mut hbt, &mut state));
        res.unwrap();
        if before == phase_name(&state.phase) {
            if let Some(i) = names.iter().position(|n| *n == before) {
                calls[i] += 1;
                allocs[i] += counts.allocations;
            }
        }
        if r == ElapseResult::EndOfData {
            break;
        }
    }
    (calls, allocs)
}

fn assert_phases_clean(names: [&str; 2], calls: [usize; 2], allocs: [u64; 2]) {
    for ((name, c), a) in names.iter().zip(calls).zip(allocs) {
        assert!(
            c >= PER_PHASE,
            "{name} измерен на {c} вызовах < {PER_PHASE}"
        );
        assert_eq!(a, 0, "{name} аллоцировал — запрет 1 interfaces.md");
    }
}

/// Лента: стакан, вход стоит за огромной очередью (не исполняется) `PER_PHASE + 5_000` событий, затем сделка
/// крупнее очереди исполняет вход, и столько же событий позиция держится в коридоре стопа и тейка.
#[test]
fn on_event_allocates_nothing_while_entry_pending_and_while_holding() {
    let n = PER_PHASE as i64 + 5_000;
    let mut feed = vec![
        depth_at(0, true, 100.0, 1e9),
        depth_at(0, false, 101.0, 5.0),
    ];
    // Шум на стороне, не затрагивающей ни вход, ни коридор выхода: меняется размер бида на 99.
    for k in 1..=n {
        feed.push(depth_at(k * STEP, true, 99.0, 5.0 + (k % 2) as f64));
    }
    let fill_at = (n + 1) * STEP;
    feed.push(trade_at(fill_at, true, 100.0, 2e9));
    feed.push(depth_at(fill_at, true, 100.0, 5.0));
    for k in 1..=n {
        feed.push(depth_at(
            fill_at + k * STEP,
            true,
            99.0,
            5.0 + (k % 2) as f64,
        ));
    }
    let names = ["EntryPending", "Holding"];
    let (calls, allocs) = measure_phases(&feed, names);
    assert_phases_clean(names, calls, allocs);
}

/// Лента: вход исполнен; бид доходит до тейка 110 и отступает раньше прихода заявки на биржу (+1,2 мс от
/// шага стратегии: время бэктеста сдвинуто на 1 мс) — лимитка тейка встаёт в рынок и не исполняется (сделок
/// нет): `ExitPending` держится `PER_PHASE + 5_000` событий. Вторая фаза — `Holding` до тейка. Ask = 120: бид
/// 110 не пересекает книгу (иначе обновление не видно).
#[test]
fn on_event_allocates_nothing_while_exit_is_resting() {
    let n = PER_PHASE as i64 + 5_000;
    let mut feed = vec![
        depth_at(0, true, 100.0, 5.0),
        depth_at(0, false, 120.0, 5.0),
        trade_at(5 * STEP, true, 100.0, 2e9),
    ];
    for k in 2..=n {
        feed.push(depth_at(k * STEP, true, 99.0, 5.0 + (k % 2) as f64));
    }
    let touch = (n + 1) * STEP;
    feed.push(depth_at(touch, true, 110.0, 5.0));
    feed.push(depth_at(touch + 1_200_000, true, 110.0, 0.0));
    for k in 1..=n {
        feed.push(depth_at(touch + k * STEP, true, 99.0, 5.0 + (k % 2) as f64));
    }
    let names = ["Holding", "ExitPending"];
    let (calls, allocs) = measure_phases(&feed, names);
    assert_phases_clean(names, calls, allocs);
}
