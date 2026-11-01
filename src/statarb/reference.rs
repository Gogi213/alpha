//! Простая референсная реализация ядра (TK-479): окно пересчитывается заново на каждой минуте
//! (O(W), два прохода, без скользящих сумм), фандинг — постфактум по списку событий. Правила те же,
//! код другой: сверка `kernel::run_pair` с ней — тест и вариант `naive` в таблице замера.

use super::kernel::{BetaMode, Counters, PairInput, Params, Sink, Trade};

/// Нога для фандинга: события, open, количество.
type Leg<'a> = (&'a [(i64, f64)], &'a [f64], f64);

/// z-скор и β на каждой минуте окна `[t−W+1, t]` (двухпроходная формула).
#[allow(clippy::needless_range_loop)] // наивный эталон: индекс t нужен по всем рядам сразу
pub fn z_series(inp: &PairInput, p: &Params) -> Vec<(f64, f64)> {
    let n = inp.ly.len();
    let mut out = vec![(f64::NAN, f64::NAN); n];
    for t in 0..n {
        let (xt, yt) = (inp.lx[t], inp.ly[t]);
        if !(xt.is_finite() && yt.is_finite()) {
            continue;
        }
        let lo = (t + 1).saturating_sub(p.window);
        let pts: Vec<(f64, f64)> = (lo..=t)
            .filter(|&i| inp.lx[i].is_finite() && inp.ly[i].is_finite())
            .map(|i| (inp.lx[i], inp.ly[i]))
            .collect();
        if pts.len() < p.min_obs.max(2) {
            continue;
        }
        let m = pts.len() as f64;
        let mx = pts.iter().map(|q| q.0).sum::<f64>() / m;
        let my = pts.iter().map(|q| q.1).sum::<f64>() / m;
        let beta = match p.beta {
            BetaMode::Ols => {
                let cxx: f64 = pts.iter().map(|q| (q.0 - mx).powi(2)).sum();
                let cxy: f64 = pts.iter().map(|q| (q.0 - mx) * (q.1 - my)).sum();
                if cxx <= 0.0 {
                    continue;
                }
                cxy / cxx
            }
            BetaMode::Fixed(b) => b,
        };
        let res: Vec<f64> = pts.iter().map(|q| q.1 - beta * q.0).collect();
        let mr = res.iter().sum::<f64>() / m;
        let ssr: f64 = res.iter().map(|r| (r - mr).powi(2)).sum();
        if ssr <= 0.0 {
            out[t] = (beta, f64::NAN);
            continue;
        }
        out[t] = (beta, (yt - beta * xt - mr) / (ssr / m).sqrt());
    }
    out
}

struct Open {
    dir: i8,
    sig_in: usize,
    fill_in: usize,
    qty: Vec<f64>,
    pin: Vec<f64>,
}

fn last_open(o: &[f64], mut t: usize) -> f64 {
    while !o[t].is_finite() {
        t -= 1;
    }
    o[t]
}

pub fn run_pair<S: Sink>(inp: &PairInput, p: &Params, sink: &mut S) -> Counters {
    let n = inp.ly.len();
    let k = inp.ox.len();
    let zs = z_series(inp, p);
    let mut c = Counters::default();
    let mut pos: Option<Open> = None;
    let mut want_enter: Option<(i8, f64, usize)> = None;
    let mut want_exit: Option<usize> = None;
    for t in 0..n {
        let ok = inp.oy[t].is_finite() && inp.ox.iter().all(|o| o[t].is_finite());
        if let Some((d, beta, sig)) = want_enter.take() {
            if ok {
                let mut qty = vec![f64::from(d) / inp.oy[t]];
                let mut pin = vec![inp.oy[t]];
                for i in 0..k {
                    qty.push(-f64::from(d) * beta * inp.wx[i] / inp.ox[i][t]);
                    pin.push(inp.ox[i][t]);
                }
                pos = Some(Open {
                    dir: d,
                    sig_in: sig,
                    fill_in: t,
                    qty,
                    pin,
                });
                c.entries += 1;
            } else {
                c.entry_deferred += 1;
            }
        }
        if let Some(sig) = want_exit {
            if ok {
                let o = pos.take().expect("выход без позиции");
                let outs: Vec<f64> = std::iter::once(inp.oy[t])
                    .chain(inp.ox.iter().map(|x| x[t]))
                    .collect();
                let mut gross = 0.0;
                let mut fee = 0.0;
                for (i, q) in o.qty.iter().enumerate() {
                    let kind = if i == 0 { p.y_kind } else { p.x_kind };
                    gross += q * (outs[i] - o.pin[i]);
                    fee += q.abs()
                        * (o.pin[i] * kind.fee_bps(p.taker_in)
                            + outs[i] * kind.fee_bps(p.taker_out));
                }
                let mut fund = 0.0;
                for m in (o.fill_in + 1)..t {
                    let ft = inp.first_min + m as i64;
                    let legs: Vec<Leg> = std::iter::once((inp.fund_y, inp.oy, o.qty[0]))
                        .chain((0..k).map(|i| {
                            (
                                inp.fund_x.get(i).copied().unwrap_or(&[]),
                                inp.ox[i],
                                o.qty[i + 1],
                            )
                        }))
                        .collect();
                    for (ev, op, q) in legs {
                        for &(fm, r) in ev {
                            if fm == ft {
                                fund -= q * last_open(op, m) * r;
                            }
                        }
                    }
                }
                let (gross_bps, funding_bps) = (gross * 1e4, fund * 1e4);
                let net = gross_bps - fee + funding_bps;
                sink.trade(&Trade {
                    dir: o.dir,
                    sig_in: inp.first_min + o.sig_in as i64,
                    fill_in: inp.first_min + o.fill_in as i64,
                    sig_out: inp.first_min + sig as i64,
                    fill_out: inp.first_min + t as i64,
                    gross_bps,
                    fee_bps: fee,
                    funding_bps,
                    net_bps: net,
                });
                c.trades += 1;
                c.sum_net_bps += net;
                want_exit = None;
            } else {
                c.exit_deferred += 1;
            }
        }
        if pos.is_some() {
            c.minutes_in_pos += 1;
        }
        if inp.lx[t].is_finite() && inp.ly[t].is_finite() {
            c.obs_minutes += 1;
        } else {
            c.gap_minutes += 1;
        }
        let (beta, z) = zs[t];
        match &pos {
            None => {
                if want_enter.is_none() && z.is_finite() && beta.is_finite() {
                    if z <= -p.entry_z {
                        want_enter = Some((1, beta, t));
                    } else if z >= p.entry_z {
                        want_enter = Some((-1, beta, t));
                    }
                }
            }
            Some(o) => {
                if want_exit.is_none() {
                    let dz = f64::from(o.dir) * z;
                    let timed = p.max_hold > 0 && t - o.fill_in >= p.max_hold;
                    if (z.is_finite() && (dz >= -p.exit_z || dz <= -p.stop_z)) || timed {
                        want_exit = Some(t);
                    }
                }
            }
        }
    }
    if pos.is_some() {
        c.open_at_end = 1;
    }
    c
}
