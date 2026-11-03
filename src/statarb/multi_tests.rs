use super::kernel::*;
use super::multi::run_pair_multi;
use super::tests::{funding_events, lns, ou_on, params, walk, with_gaps, Lcg, N};

/// Клетки группы (общие window/min_obs/beta/refresh), различаются порогами, стопом, сроком и комиссиями.
fn grid(base: Params) -> Vec<Params> {
    let mut v = Vec::new();
    for (entry_z, exit_z) in [(1.5, 0.0), (2.0, 0.2), (2.5, 0.5), (3.0, 0.2)] {
        for (stop_z, max_hold) in [(f64::INFINITY, 0), (4.0, 500), (3.5, 120)] {
            for taker_out in [true, false] {
                v.push(Params {
                    entry_z,
                    exit_z,
                    stop_z,
                    max_hold,
                    taker_out,
                    ..base
                });
            }
        }
    }
    v
}

/// Сделки и счётчики каждой клетки многоклеточного прохода равны побитно проходу `run_pair` по клетке.
fn same_as_single(inp: &PairInput, base: Params) -> usize {
    inp.check().unwrap();
    let cells = grid(base);
    let mut sinks: Vec<Vec<Trade>> = cells.iter().map(|_| Vec::new()).collect();
    let cs = run_pair_multi(inp, &cells, &mut sinks).unwrap();
    let mut total = 0;
    for ((p, got), c) in cells.iter().zip(&sinks).zip(&cs) {
        let mut want: Vec<Trade> = Vec::new();
        let cw = run_pair(inp, p, &mut want);
        assert_eq!(*got, want, "сделки клетки {p:?}");
        assert_eq!(*c, cw, "счётчики клетки {p:?}");
        total += want.len();
    }
    total
}

#[test]
fn multi_equals_single_cell_passes() {
    // пара с пропусками и фандингом обеих ног
    let mut r = Lcg(11);
    let x = walk(N, &mut r, 1e-3, 4.0);
    let y = ou_on(&x, 0.8, &mut r, 2e-3);
    let (ox, cx) = with_gaps(&x, &mut r, None);
    let (oy, cy) = with_gaps(&y, &mut r, Some((3000, 3040)));
    let (lx, ly) = (lns(&cx), lns(&cy));
    let fy = funding_events(777, N, 480, 3, 1.0);
    let fx = funding_events(777, N, 480, 3, -1.0);
    let fxs: [&[(i64, f64)]; 1] = [&fx];
    let inp = PairInput {
        first_min: 777,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &[&ox],
        wx: &[1.0],
        fund_y: &fy,
        fund_x: &fxs,
    };
    let a = same_as_single(&inp, params(BetaMode::Ols, LegKind::Perp, LegKind::Perp));
    let b = same_as_single(
        &inp,
        Params {
            refresh: 97,
            ..params(BetaMode::Fixed(1.0), LegKind::Perp, LegKind::Spot)
        },
    );
    assert!(a >= 100 && b >= 100, "тест пуст: сделок {a} / {b}");

    // корзина из трёх компонент
    let base = walk(N, &mut r, 1e-3, 3.0);
    let comps: Vec<(Vec<f64>, Vec<f64>)> = (0..3)
        .map(|i| {
            let ln: Vec<f64> = base
                .iter()
                .map(|b| b * (0.7 + 0.2 * i as f64) + i as f64 + 3e-4 * r.g())
                .collect();
            with_gaps(&ln, &mut r, None)
        })
        .collect();
    let w = [0.5, 0.3, 0.2];
    let lxb: Vec<f64> = (0..N)
        .map(|t| {
            if comps.iter().all(|c| c.1[t].is_finite()) {
                comps.iter().zip(w).map(|(c, wi)| wi * c.1[t].ln()).sum()
            } else {
                f64::NAN
            }
        })
        .collect();
    let fill: Vec<f64> = lxb
        .iter()
        .map(|v| if v.is_finite() { *v } else { 0.0 })
        .collect();
    let yb = ou_on(&fill, 1.1, &mut r, 2e-3);
    let (oyb, cyb) = with_gaps(&yb, &mut r, None);
    let lyb = lns(&cyb);
    let oxb: Vec<&[f64]> = comps.iter().map(|c| c.0.as_slice()).collect();
    let inp = PairInput {
        first_min: 0,
        ly: &lyb,
        lx: &lxb,
        oy: &oyb,
        ox: &oxb,
        wx: &w,
        fund_y: &[],
        fund_x: &[],
    };
    let c = same_as_single(&inp, params(BetaMode::Ols, LegKind::Perp, LegKind::Perp));
    assert!(c >= 100, "тест пуст: сделок {c}");
}

#[test]
fn multi_rejects_mixed_groups() {
    let (ly, lx, oy, ox) = (vec![0.0; 10], vec![0.0; 10], vec![1.0; 10], vec![1.0; 10]);
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
    let a = params(BetaMode::Ols, LegKind::Perp, LegKind::Perp);
    let b = Params {
        window: a.window + 1,
        ..a
    };
    assert!(run_pair_multi(&inp, &[a, b], &mut [(), ()]).is_err());
    assert!(run_pair_multi(&inp, &[], &mut [(); 0]).is_err());
    assert!(run_pair_multi(&inp, &[a], &mut [(), ()]).is_err());
}
