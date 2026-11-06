//! Тесты TK-493 волны 5: запрет входа до `from_min` задания (`Params::entry_from`, `statarb run --no-entry-before-from`, D5(ii),
//! аудит M3-04), счётчики по заданиям (`--job-ext-out`, L1-03), проверки входных файлов (L2-03), покрытие задержки с `rearm` (L1-05).
//!
//! Ожидаемые числа посчитаны руками (модель правила выписана заново, без кода ядра): ряды плоские (100), ставки Y постоянные,
//! поэтому сигнал входа правила funding держится каждую минуту с момента, когда кольцо из `k` выплат полно.

use super::kernel::{
    run_pair, BetaMode, Counters, ExitReason, LegKind, PairInput, Params, Rule, Trade,
};
use super::multi::run_pair_multi;
use super::reference;
use super::tests::{both, lns, ou_on, params, walk, Lcg, N};

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() <= 1e-9 * a.abs().max(b.abs()).max(1.0)
}

type Key = (i8, i64, i64, i64, i64, ExitReason);

fn keys(tr: &[Trade]) -> Vec<Key> {
    tr.iter()
        .map(|t| (t.dir, t.sig_in, t.fill_in, t.sig_out, t.fill_out, t.reason))
        .collect()
}

/// Ядро и референс: те же сделки (минуты, причина, фандинг) и счётчики; возвращает сделки и счётчики ядра.
fn kernel_vs_reference(inp: &PairInput, p: &Params) -> (Vec<Trade>, Counters) {
    inp.check().unwrap();
    let mut k: Vec<Trade> = Vec::new();
    let ck = run_pair(inp, p, &mut k);
    let mut r: Vec<Trade> = Vec::new();
    let cr = reference::run_pair(inp, p, &mut r);
    assert_eq!(keys(&k), keys(&r), "ядро против референса");
    for (a, b) in k.iter().zip(&r) {
        assert!(
            close(a.funding_bps, b.funding_bps),
            "фандинг ядра и референса"
        );
        assert!(close(a.net_bps, b.net_bps), "net ядра и референса");
    }
    let (mut a, mut b) = (ck, cr);
    a.sum_net_bps = 0.0;
    b.sum_net_bps = 0.0;
    assert_eq!(a, b, "счётчики: ядро против референса");
    (k, ck)
}

/// Постоянные ставки Y каждые 8 минут с минуты 3: `(m, rate)`.
fn payments(n: usize, rate: f64) -> Vec<(i64, f64)> {
    (0..n as i64 / 8).map(|j| (3 + 8 * j, rate)).collect()
}

/// Правило funding, k = 2 выплаты, удержание 3 выплаты (30 bps против круга ≈ 8,8 bps), срок 40 минут: вход как только кольцо полно.
fn funding_params(entry_from: Option<i64>) -> Params {
    Params {
        rule: Rule::Funding {
            k: 2,
            hold_payments: 3,
        },
        max_hold: 40,
        entry_from,
        ..params(BetaMode::Fixed(1.0), LegKind::Perp, LegKind::Perp)
    }
}

#[test]
fn funding_rule_no_entry_before_from_opens_exactly_at_from() {
    let n = 400;
    let cy = vec![100.0; n];
    let ly = lns(&cy);
    let fund = payments(n, 1e-3);
    let inp = PairInput {
        first_min: 0,
        ly: &ly,
        lx: &ly,
        oy: &cy,
        ox: &[&cy],
        wx: &[1.0],
        fund_y: &fund,
        fund_x: &[],
    };
    // без запрета: кольцо (k = 2) полно на выплате минуты 11 → сигнал 11, исполнение 12 — в разогреве
    let (t0, c0) = kernel_vs_reference(&inp, &funding_params(None));
    assert_eq!((t0[0].sig_in, t0[0].fill_in, t0[0].dir), (11, 12, -1));
    assert_eq!(c0.warmup_blocked, 0);
    // с запретом до минуты 100: сигналы 11…98 подавлены (88 минут), сигнал на закрытии минуты 99 исполняется ровно на 100
    let from = 100;
    let (tg, cg) = kernel_vs_reference(&inp, &funding_params(Some(from)));
    assert!(tg.iter().all(|t| t.fill_in >= from), "вход в разогреве");
    assert_eq!((tg[0].sig_in, tg[0].fill_in), (from - 1, from));
    assert_eq!(cg.warmup_blocked, (from - 1 - 11) as u64);
    // срок 40 от входа: сигнал выхода 140, исполнение 141; выплаты 107, 115, 123, 131, 139 — 5 × 10 bps в пользу шорта Y
    assert_eq!((tg[0].sig_out, tg[0].fill_out), (from + 40, from + 41));
    assert!(close(tg[0].funding_bps, 50.0), "{}", tg[0].funding_bps);
    assert_eq!(tg[0].reason, ExitReason::Timeout);
    // без запрета сделки шли бы через границу (цикл 42 минуты: 12…53, 54…95, 96…137): в окне `run` такая сделка отбрасывалась бы как pre_window
    assert!(t0.iter().any(|t| t.fill_in < from && t.fill_out > from));
}

#[test]
fn entry_from_at_or_before_grid_start_is_a_noop() {
    let n = 400;
    let cy = vec![100.0; n];
    let ly = lns(&cy);
    let fund = payments(n, 1e-3);
    let inp = PairInput {
        first_min: 50,
        ly: &ly,
        lx: &ly,
        oy: &cy,
        ox: &[&cy],
        wx: &[1.0],
        fund_y: &fund,
        fund_x: &[],
    };
    let (base_t, base_c) = kernel_vs_reference(&inp, &funding_params(None));
    // раньше сетки, ровно в начале сетки и на минуте 1 (сигнал на t = 0 исполняется на t + 1 = 1 — не раньше)
    for from in [Some(-1000), Some(0), Some(50), Some(51)] {
        let (t, c) = kernel_vs_reference(&inp, &funding_params(from));
        assert_eq!(t, base_t, "entry_from {from:?}");
        assert_eq!(c, base_c, "entry_from {from:?}");
        assert_eq!(c.warmup_blocked, 0);
    }
}

/// Ряд без дыр: close = exp(ln), open = прежний close.
fn clean(ln: &[f64]) -> (Vec<f64>, Vec<f64>) {
    let c: Vec<f64> = ln.iter().map(|v| v.exp()).collect();
    let o: Vec<f64> = (0..c.len()).map(|t| c[t.saturating_sub(1)]).collect();
    (o, c)
}

#[test]
fn z_rule_with_min_obs_equal_window_is_unchanged_by_the_gate() {
    let mut r = Lcg(11);
    let x = walk(N, &mut r, 1e-3, 4.0);
    let y = ou_on(&x, 0.8, &mut r, 2e-3);
    let (ox, cx) = clean(&x);
    let (oy, cy) = clean(&y);
    let (lx, ly) = (lns(&cx), lns(&cy));
    let inp = PairInput {
        first_min: 777,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &[&ox],
        wx: &[1.0],
        fund_y: &[],
        fund_x: &[],
    };
    // min_obs = W: первый сигнал на t = W − 1, исполнение ровно на from_min = first_min + W — запрет ничего не меняет
    let p = Params {
        min_obs: 240,
        ..params(BetaMode::Ols, LegKind::Perp, LegKind::Perp)
    };
    let (t_off, c_off) = both(&inp, &p);
    let gated = Params {
        entry_from: Some(777 + 240),
        ..p
    };
    let (t_on, c_on) = both(&inp, &gated);
    assert_eq!(t_off, t_on);
    assert_eq!(c_off, c_on);
    assert_eq!(c_on.warmup_blocked, 0);
}

#[test]
fn z_rule_gate_blocks_early_entries_and_agrees_in_kernel_reference_and_multi() {
    let mut r = Lcg(12);
    let x = walk(N, &mut r, 1e-3, 4.0);
    let y = ou_on(&x, 0.8, &mut r, 2e-3);
    let (ox, cx) = clean(&x);
    let (oy, cy) = clean(&y);
    let (lx, ly) = (lns(&cx), lns(&cy));
    let first = 777;
    let inp = PairInput {
        first_min: first,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &[&ox],
        wx: &[1.0],
        fund_y: &[],
        fund_x: &[],
    };
    let from = first + 3000;
    let base = Params {
        stop_z: 3.5,
        ..params(BetaMode::Ols, LegKind::Perp, LegKind::Perp)
    };
    let mut off: Vec<Trade> = Vec::new();
    run_pair(&inp, &base, &mut off);
    assert!(
        off.iter().any(|t| t.fill_in < from) && off.iter().any(|t| t.fill_in >= from),
        "тест пуст: сделок до границы {}",
        off.iter().filter(|t| t.fill_in < from).count()
    );
    // клетки группы (общие window / min_obs / beta / refresh): разные пороги, без и с rearm
    let cells: Vec<Params> = [(2.0, false), (1.5, false), (2.5, true), (2.0, true)]
        .into_iter()
        .map(|(entry_z, rearm)| Params {
            entry_z,
            rearm,
            entry_from: Some(from),
            ..base
        })
        .collect();
    let mut sinks: Vec<Vec<Trade>> = cells.iter().map(|_| Vec::new()).collect();
    let cm = run_pair_multi(&inp, &cells, &mut sinks).unwrap();
    for (i, p) in cells.iter().enumerate() {
        let (tk, ck) = both(&inp, p); // ядро против референса
        assert!(
            tk.iter().all(|t| t.fill_in >= from),
            "клетка {i}: вход в разогреве"
        );
        assert!(ck.warmup_blocked > 0, "клетка {i}: подавлено 0");
        assert_eq!(
            tk, sinks[i],
            "клетка {i}: ядро против многоклеточного прохода"
        );
        assert_eq!(
            ck, cm[i],
            "клетка {i}: счётчики ядра и многоклеточного прохода"
        );
    }
    // первая сделка запрещённого прогона не раньше границы (сигнал допустим на закрытии минуты from − 1)
    let (t_on, _) = both(&inp, &cells[0]);
    assert!(t_on[0].fill_in >= from);
}

#[test]
fn delay_x_with_rearm_agrees_with_reference() {
    // L1-05: комбинация задержки ноги X и rearm (клетки C1 с --delay-x 1 --rearm) раньше не была покрыта сверкой с референсом
    let mut r = Lcg(13);
    let x = walk(N, &mut r, 1e-3, 4.0);
    let y = ou_on(&x, 0.8, &mut r, 2e-3);
    let (ox, cx) = super::tests::with_gaps(&x, &mut r, None);
    let (oy, cy) = super::tests::with_gaps(&y, &mut r, Some((3000, 3040)));
    let (lx, ly) = (lns(&cx), lns(&cy));
    let inp = PairInput {
        first_min: 0,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &[&ox],
        wx: &[1.0],
        fund_y: &[],
        fund_x: &[],
    };
    for delay_x in [1, 2] {
        let p = Params {
            delay_x,
            rearm: true,
            stop_z: 3.5,
            max_hold: 120,
            min_obs: 228,
            ..params(BetaMode::Ols, LegKind::Perp, LegKind::Perp)
        };
        let (t, c) = both(&inp, &p);
        assert!(
            c.rearm_blocked > 0,
            "delay_x {delay_x}: rearm не сработал, тест пуст"
        );
        assert!(t.iter().any(|x| x.fill_x > x.fill_in));
    }
}
