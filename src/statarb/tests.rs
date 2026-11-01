use super::kernel::*;
use super::panel::*;
use super::reference;
use crate::lob::costs::{leg_fee_bps, spot_leg_fee_bps};

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() <= 1e-9 * a.abs().max(b.abs()).max(1.0)
}

/// Детерминированный генератор: u ∈ (−1, 1), сумма трёх — грубая «гауссиана».
struct Lcg(u64);
impl Lcg {
    fn u(&mut self) -> f64 {
        self.0 = self
            .0
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        ((self.0 >> 11) as f64 / (1u64 << 53) as f64) * 2.0 - 1.0
    }
    fn g(&mut self) -> f64 {
        (self.u() + self.u() + self.u()) / 1.7
    }
}

/// Ряд цен `exp(ln)` с пропусками (`NaN` в open и close), open = прежнее закрытие с шумом.
fn with_gaps(ln: &[f64], rng: &mut Lcg, burst: Option<(usize, usize)>) -> (Vec<f64>, Vec<f64>) {
    let n = ln.len();
    let mut o = vec![f64::NAN; n];
    let mut c = vec![f64::NAN; n];
    for t in 0..n {
        let gap = rng.u() > 0.96 || burst.is_some_and(|(a, b)| (a..b).contains(&t));
        if !gap {
            c[t] = ln[t].exp();
            o[t] = (if t > 0 { ln[t - 1] } else { ln[t] } + 1e-4 * rng.g()).exp();
        }
    }
    (o, c)
}

fn lns(c: &[f64]) -> Vec<f64> {
    c.iter().map(|v| v.ln()).collect()
}

fn walk(n: usize, rng: &mut Lcg, step: f64, start: f64) -> Vec<f64> {
    let mut v = Vec::with_capacity(n);
    let mut x = start;
    for _ in 0..n {
        x += step * rng.g();
        v.push(x);
    }
    v
}

fn ou_on(base: &[f64], beta: f64, rng: &mut Lcg, sig: f64) -> Vec<f64> {
    let mut o = 0.0;
    base.iter()
        .map(|x| {
            o = 0.97 * o + sig * rng.g();
            beta * x + o + 1.0
        })
        .collect()
}

fn params(beta: BetaMode, yk: LegKind, xk: LegKind) -> Params {
    Params {
        window: 240,
        min_obs: 120,
        beta,
        entry_z: 2.0,
        exit_z: 0.2,
        stop_z: 4.0,
        max_hold: 500,
        refresh: 0,
        y_kind: yk,
        x_kind: xk,
        taker_in: true,
        taker_out: false,
    }
}

fn both(inp: &PairInput, p: &Params) -> (Vec<Trade>, Counters) {
    inp.check().unwrap();
    let mut a: Vec<Trade> = Vec::new();
    let mut b: Vec<Trade> = Vec::new();
    let ca = run_pair(inp, p, &mut a);
    let cb = reference::run_pair(inp, p, &mut b);
    assert_eq!(a.len(), b.len(), "число сделок");
    assert!(a.len() >= 5, "тест пуст: сделок {}", a.len());
    for (x, y) in a.iter().zip(&b) {
        // Целые — побайтно.
        assert_eq!(
            (x.dir, x.sig_in, x.fill_in, x.sig_out, x.fill_out),
            (y.dir, y.sig_in, y.fill_in, y.sig_out, y.fill_out)
        );
        for (u, v) in [
            (x.gross_bps, y.gross_bps),
            (x.fee_bps, y.fee_bps),
            (x.funding_bps, y.funding_bps),
            (x.net_bps, y.net_bps),
        ] {
            assert!(close(u, v), "{u} против {v}");
        }
    }
    assert!(close(ca.sum_net_bps, cb.sum_net_bps));
    let (mut ca0, mut cb0) = (ca, cb);
    ca0.sum_net_bps = 0.0;
    cb0.sum_net_bps = 0.0;
    assert_eq!(ca0, cb0, "счётчики");
    (a, ca)
}

fn funding_events(first: i64, n: usize, every: i64, off: i64, sign: f64) -> Vec<(i64, f64)> {
    (0..n as i64)
        .filter(|i| i % every == off)
        .map(|i| (first + i, sign * 1e-4 * (1.0 + (i % 3) as f64)))
        .collect()
}

const N: usize = 8000;

#[test]
fn pair_kernel_matches_reference_with_gaps() {
    let mut r = Lcg(1);
    let x = walk(N, &mut r, 1e-3, 4.0);
    let y = ou_on(&x, 0.8, &mut r, 2e-3);
    let (ox, cx) = with_gaps(&x, &mut r, None);
    let (oy, cy) = with_gaps(&y, &mut r, Some((3000, 3040)));
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
    let p = params(BetaMode::Ols, LegKind::Perp, LegKind::Perp);
    let (_, c) = both(&inp, &p);
    assert!(c.gap_minutes > 0 && c.obs_minutes + c.gap_minutes == N as u64);
    // Частота точного пересчёта окна не меняет сделок.
    for refresh in [7, 1000] {
        let mut a: Vec<Trade> = Vec::new();
        let mut b: Vec<Trade> = Vec::new();
        run_pair(&inp, &p, &mut a);
        run_pair(&inp, &Params { refresh, ..p }, &mut b);
        assert_eq!(a.len(), b.len());
        assert!(a.iter().zip(&b).all(|(u, v)| u.fill_in == v.fill_in
            && u.fill_out == v.fill_out
            && close(u.net_bps, v.net_bps)));
    }
}

#[test]
fn basket_kernel_matches_reference() {
    let mut r = Lcg(2);
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
    let lx: Vec<f64> = (0..N)
        .map(|t| {
            if comps.iter().all(|c| c.1[t].is_finite()) {
                comps.iter().zip(w).map(|(c, wi)| wi * c.1[t].ln()).sum()
            } else {
                f64::NAN
            }
        })
        .collect();
    let lx_fill: Vec<f64> = lx
        .iter()
        .map(|v| if v.is_finite() { *v } else { 0.0 })
        .collect();
    let y = ou_on(&lx_fill, 1.1, &mut r, 2e-3);
    let (oy, cy) = with_gaps(&y, &mut r, None);
    let ly = lns(&cy);
    let ox: Vec<&[f64]> = comps.iter().map(|c| c.0.as_slice()).collect();
    let inp = PairInput {
        first_min: 0,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &ox,
        wx: &w,
        fund_y: &[],
        fund_x: &[],
    };
    both(&inp, &params(BetaMode::Ols, LegKind::Perp, LegKind::Perp));
}

#[test]
fn cross_exchange_fixed_beta_matches_reference() {
    let mut r = Lcg(3);
    let x = walk(N, &mut r, 1e-3, 5.0);
    let y: Vec<f64> = ou_on(&x, 1.0, &mut r, 3e-4)
        .iter()
        .map(|v| v - 1.0)
        .collect();
    let (ox, cx) = with_gaps(&x, &mut r, None);
    let (oy, cy) = with_gaps(&y, &mut r, None);
    let (lx, ly) = (lns(&cx), lns(&cy));
    let inp = PairInput {
        first_min: 5,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &[&ox],
        wx: &[1.0],
        fund_y: &[],
        fund_x: &[],
    };
    both(
        &inp,
        &params(BetaMode::Fixed(1.0), LegKind::Perp, LegKind::Perp),
    );
}

#[test]
fn funding_perp_spot_and_perp_perp_matches_reference() {
    let mut r = Lcg(4);
    let x = walk(N, &mut r, 1e-3, 4.0);
    let y = ou_on(&x, 1.0, &mut r, 5e-4);
    let (ox, cx) = with_gaps(&x, &mut r, None);
    let (oy, cy) = with_gaps(&y, &mut r, None);
    let (lx, ly) = (lns(&cx), lns(&cy));
    let fy = funding_events(100, N, 480, 3, 1.0);
    let fx = funding_events(100, N, 480, 3, -1.0);
    let fxs: [&[(i64, f64)]; 1] = [&fx];
    let inp = PairInput {
        first_min: 100,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &[&ox],
        wx: &[1.0],
        fund_y: &fy,
        fund_x: &fxs,
    };
    // Перп против перпа: фандинг обеих ног.
    let (t_pp, _) = both(
        &inp,
        &params(BetaMode::Fixed(1.0), LegKind::Perp, LegKind::Perp),
    );
    assert!(
        t_pp.iter().any(|t| t.funding_bps != 0.0),
        "фандинг не выплачен"
    );
    // Перп против спота: фандинг только у ноги-перпа Y.
    let inp2 = PairInput { fund_x: &[], ..inp };
    let (t_ps, _) = both(
        &inp2,
        &params(BetaMode::Fixed(1.0), LegKind::Perp, LegKind::Spot),
    );
    assert!(t_ps.iter().any(|t| t.funding_bps != 0.0));
}

fn flat_x_params() -> Params {
    Params {
        window: 4,
        min_obs: 3,
        beta: BetaMode::Fixed(1.0),
        entry_z: 1.5,
        exit_z: 0.5,
        stop_z: f64::INFINITY,
        max_hold: 0,
        refresh: 0,
        y_kind: LegKind::Perp,
        x_kind: LegKind::Spot,
        taker_in: true,
        taker_out: true,
    }
}

#[test]
fn single_trade_gross_fees_by_leg_closed_form() {
    // X плоская (100), Y: 100×6, 98, 99, 100…; окно 4. Сигнал входа t=6 (z=−√3), исполнение open[7]=c[6]=98;
    // выход по сигналу t=7, исполнение open[8]=c[7]=99; следующий вход — сигнал t=10, не закрыт.
    let mut cy = vec![100.0; 6];
    cy.extend([98.0, 99.0, 100.0, 100.0, 100.0, 100.0, 100.0]);
    let n = cy.len();
    let oy: Vec<f64> = (0..n).map(|t| cy[t.saturating_sub(1)]).collect();
    let cx = vec![100.0; n];
    let (lx, ly) = (lns(&cx), lns(&cy));
    let inp = PairInput {
        first_min: 40,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &[&cx],
        wx: &[1.0],
        fund_y: &[],
        fund_x: &[],
    };
    let p = Params {
        min_obs: 4,
        ..flat_x_params()
    };
    let mut tr: Vec<Trade> = Vec::new();
    let c = run_pair(&inp, &p, &mut tr);
    assert_eq!(tr.len(), 1);
    assert_eq!((c.trades, c.entries, c.open_at_end), (1, 2, 1));
    let t = tr[0];
    assert_eq!(
        (t.dir, t.sig_in, t.fill_in, t.sig_out, t.fill_out),
        (1, 46, 47, 47, 48)
    );
    assert!(close(t.gross_bps, 1e4 * (99.0 / 98.0 - 1.0)));
    let (fp, fs) = (leg_fee_bps(true), spot_leg_fee_bps(true));
    let fee = (1.0 + 99.0 / 98.0) * fp + 2.0 * fs;
    assert!(close(t.fee_bps, fee), "{} против {fee}", t.fee_bps);
    assert!(close(t.net_bps, t.gross_bps - fee));
}

#[test]
fn gap_minute_is_not_invented_and_blocks_fill() {
    // Сигнал входа на t=6, но open[7] отсутствует: вход не исполнен, счётчик отложенных растёт.
    let mut cy = vec![100.0; 6];
    cy.extend([98.0, f64::NAN, 100.0, 100.0, 100.0, 100.0]);
    let n = cy.len();
    let mut oy: Vec<f64> = (0..n).map(|t| cy[t.saturating_sub(1)]).collect();
    oy[7] = f64::NAN;
    let cx = vec![100.0; n];
    let (lx, ly) = (lns(&cx), lns(&cy));
    let inp = PairInput {
        first_min: 0,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &[&cx],
        wx: &[1.0],
        fund_y: &[],
        fund_x: &[],
    };
    let mut tr: Vec<Trade> = Vec::new();
    let c = run_pair(&inp, &flat_x_params(), &mut tr);
    assert_eq!(c.entry_deferred, 1);
    assert_eq!(c.gap_minutes, 1);
    assert!(tr.iter().all(|t| t.fill_in != 7));
}

#[test]
fn spot_and_perp_fees_after_rebate() {
    assert!(close(leg_fee_bps(false), 1.26) && close(leg_fee_bps(true), 3.15));
    assert!(close(spot_leg_fee_bps(false), 5.625) && close(spot_leg_fee_bps(true), 6.75));
}

#[test]
fn panel_roundtrip_and_csv_gap_rule() {
    let csv = format!(
        "t_open_ms,o,h,l,c,v,quote_v,n_trades\n{},1.5,2,1,1.75,3,4,5\n{},1.75,2,1,1.8,3,4,5\n{}, 2.5 ,3,1, 2.6,3,4,5\n",
        GRID0_MS + 10 * MINUTE_MS,
        GRID0_MS + 11 * MINUTE_MS,
        GRID0_MS + 14 * MINUTE_MS,
    );
    let s = convert_csv(csv.as_bytes()).unwrap().unwrap();
    assert_eq!((s.first_min, s.len(), s.last_min()), (10, 5, 14));
    assert!(s.close[2].is_nan() && s.close[3].is_nan() && s.open[2].is_nan());
    assert_eq!((s.open[4], s.close[4], s.close[1]), (2.5, 2.6, 1.8));
    let mut buf: Vec<u8> = Vec::new();
    write_series(&mut buf, &s).unwrap();
    let back = read_series(&mut buf.as_slice()).unwrap();
    let bits = |v: &[f64]| v.iter().map(|x| x.to_bits()).collect::<Vec<_>>();
    assert_eq!(back.first_min, s.first_min);
    assert_eq!(bits(&back.open), bits(&s.open));
    assert_eq!(bits(&back.close), bits(&s.close));
    // Только заголовок — нет ряда; время не на минуте и порядок не по возрастанию — отказ.
    assert!(convert_csv("t_open_ms,o,h,l,c\n".as_bytes())
        .unwrap()
        .is_none());
    assert!(convert_csv(format!("{},1,1,1,1\n", GRID0_MS + 7).as_bytes()).is_err());
    assert!(convert_csv(
        format!("{},1,1,1,1\n{},1,1,1,1\n", GRID0_MS + 60_000, GRID0_MS).as_bytes()
    )
    .is_err());
}

#[test]
fn overlap_of_spans() {
    let mk = |f: i64, n: usize| Series {
        first_min: f,
        open: vec![1.0; n],
        close: vec![1.0; n],
    };
    assert_eq!(mk(10, 10).overlap(&mk(15, 10)), Some((5, 0, 5)));
    assert_eq!(mk(10, 3).overlap(&mk(13, 3)), None);
}
