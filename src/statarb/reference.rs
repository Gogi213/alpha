//! Простая референсная реализация ядра (TK-479): окно пересчитывается заново на каждой минуте
//! (O(W), два прохода, без скользящих сумм), фандинг — постфактум по списку событий. Правила те же,
//! код другой: сверка `kernel::run_pair` с ней — тест и вариант `naive` в таблице замера.

use super::kernel::{
    entry_gate, round_trip_cost_bps, BetaMode, Counters, ExitReason, PairInput, Params, Rule, Sink,
    Trade,
};

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

/// Совместных наблюдений в окне `[t−W+1, t]` (для `Counters::noentry_window_minutes`).
fn window_obs(inp: &PairInput, p: &Params, t: usize) -> usize {
    let lo = (t + 1).saturating_sub(p.window);
    match (inp.lx.get(lo..=t), inp.ly.get(lo..=t)) {
        (Some(x), Some(y)) => x
            .iter()
            .zip(y)
            .filter(|(a, b)| a.is_finite() && b.is_finite())
            .count(),
        _ => 0,
    }
}

struct Open {
    dir: i8,
    sig_in: usize,
    fill_in: usize,
    /// Минута входа ноги X; `None` — нога X ещё ждёт срока или open.
    fill_x: Option<usize>,
    beta: f64,
    qty: Vec<f64>,
    pin: Vec<f64>,
}

fn last_open(o: &[f64], mut t: usize) -> f64 {
    while !o[t].is_finite() {
        t -= 1;
    }
    o[t]
}

/// Трейлинг-среднее `k` последних выплат с минутой ≤ `ft`; `NaN`, если выплат меньше `k`.
fn trailing_mean(ev: &[(i64, f64)], ft: i64, k: usize) -> f64 {
    let cnt = ev.partition_point(|e| e.0 <= ft);
    if cnt < k {
        return f64::NAN;
    }
    let mut acc = 0.0;
    for e in &ev[cnt - k..cnt] {
        acc += e.1;
    }
    acc / k as f64
}

pub fn run_pair<S: Sink>(inp: &PairInput, p: &Params, sink: &mut S) -> Counters {
    let n = inp.ly.len();
    let k = inp.ox.len();
    let zs = z_series(inp, p);
    let wsum = inp.wx.iter().sum::<f64>();
    let (fk, fh) = match p.rule {
        Rule::Z => (0, 0.0),
        Rule::Funding { k, hold_payments } => (k, hold_payments as f64),
    };
    assert!(
        fk > 0 || matches!(p.rule, Rule::Z),
        "Rule::Funding: нужно k ≥ 1"
    );
    let mut c = Counters::default();
    let mut pos: Option<Open> = None;
    let mut want_enter: Option<(i8, f64, usize)> = None;
    let mut want_exit: Option<(usize, ExitReason)> = None;
    // `rearm` — сторона, закрытая стопом или сроком; действует только с правилом z.
    let rearm = p.rearm && matches!(p.rule, Rule::Z);
    let mut block = 0i8;
    // D5(ii): первая минута сетки, на которой допустимо исполнение входа (`Params::entry_from`).
    let enter_t0 = entry_gate(p, inp.first_min);
    for t in 0..n {
        let ok = inp.oy[t].is_finite() && inp.ox.iter().all(|o| o[t].is_finite());
        if let Some((d, beta, sig)) = want_enter.take() {
            // Без задержки нужны open всех ног в эту минуту; с задержкой — только у Y.
            if (p.delay_x == 0 && ok) || (p.delay_x > 0 && inp.oy[t].is_finite()) {
                pos = Some(Open {
                    dir: d,
                    sig_in: sig,
                    fill_in: t,
                    fill_x: None,
                    beta,
                    qty: vec![f64::from(d) / inp.oy[t]],
                    pin: vec![inp.oy[t]],
                });
                c.entries += 1;
            } else {
                c.entry_deferred += 1;
            }
        }
        // Нога X: срок `fill_in + delay_x`, дальше — первая минута с open у всех компонент.
        if let Some(o) = pos.as_mut() {
            if o.fill_x.is_none() && t >= o.fill_in.saturating_add(p.delay_x) {
                if inp.ox.iter().all(|x| x[t].is_finite()) {
                    for i in 0..k {
                        o.qty
                            .push(-f64::from(o.dir) * o.beta * inp.wx[i] / inp.ox[i][t]);
                        o.pin.push(inp.ox[i][t]);
                    }
                    o.fill_x = Some(t);
                } else {
                    c.x_deferred += 1;
                }
            }
        }
        if let Some((sig, reason)) = want_exit {
            if ok {
                let o = pos.take().expect("выход без позиции");
                let fx = o.fill_x.expect("выход без ноги X");
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
                // Фандинг постфактум: каждая нога — за метки строго между её входом и минутой выхода.
                let mut fund = 0.0;
                for m in (o.fill_in + 1)..t {
                    let ft = inp.first_min + m as i64;
                    let mut legs: Vec<(Leg, usize)> =
                        vec![((inp.fund_y, inp.oy, o.qty[0]), o.fill_in)];
                    for i in 0..k {
                        let ev = inp.fund_x.get(i).copied().unwrap_or(&[]);
                        legs.push(((ev, inp.ox[i], o.qty[i + 1]), fx));
                    }
                    for ((ev, op, q), start) in legs {
                        if m <= start {
                            continue;
                        }
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
                    fill_x: inp.first_min + fx as i64,
                    sig_out: inp.first_min + sig as i64,
                    fill_out: inp.first_min + t as i64,
                    gross_bps,
                    fee_bps: fee,
                    funding_bps,
                    net_bps: net,
                    beta: o.beta,
                    reason,
                });
                c.trades += 1;
                c.sum_net_bps += net;
                if rearm {
                    block = if reason == ExitReason::Rule { 0 } else { o.dir };
                }
                want_exit = None;
            } else {
                c.exit_deferred += 1;
            }
        }
        if pos.is_some() {
            c.minutes_in_pos += 1;
        }
        let obs = inp.lx[t].is_finite() && inp.ly[t].is_finite();
        if obs {
            c.obs_minutes += 1;
        } else {
            c.gap_minutes += 1;
        }
        let (beta, z) = zs[t];
        if t + 1 >= p.window {
            c.eval_minutes += 1;
            if z.is_finite() {
                c.z_def_minutes += 1;
            }
        }
        if pos.is_some() && !z.is_finite() {
            c.pos_z_nan_minutes += 1;
        }
        let r_mean = if fk > 0 {
            trailing_mean(inp.fund_y, inp.first_min + t as i64, fk)
        } else {
            f64::NAN
        };
        match &pos {
            None => {
                if want_enter.is_none() {
                    match p.rule {
                        Rule::Z => {
                            if z.is_finite() && beta.is_finite() {
                                if rearm && z.abs() < p.entry_z {
                                    block = 0;
                                }
                                let side = if z <= -p.entry_z {
                                    1
                                } else if z >= p.entry_z {
                                    -1
                                } else {
                                    0
                                };
                                if side != 0 && t + 1 < enter_t0 {
                                    c.warmup_blocked += 1;
                                } else if side != 0 && rearm && block == side {
                                    c.rearm_blocked += 1;
                                } else if side != 0 {
                                    want_enter = Some((side, beta, t));
                                    if rearm {
                                        block = 0;
                                    }
                                }
                            } else if obs
                                && t + 1 >= p.window
                                && window_obs(inp, p, t) < p.min_obs.max(2)
                            {
                                c.noentry_window_minutes += 1;
                            }
                        }
                        Rule::Funding { .. } => {
                            let b = match p.beta {
                                BetaMode::Fixed(b) => b,
                                BetaMode::Ols => beta,
                            };
                            if r_mean.is_finite()
                                && b.is_finite()
                                && r_mean.abs() * 1e4 * fh > round_trip_cost_bps(p, b, wsum)
                            {
                                if t + 1 < enter_t0 {
                                    c.warmup_blocked += 1;
                                } else {
                                    want_enter = Some((if r_mean > 0.0 { -1 } else { 1 }, b, t));
                                }
                            }
                        }
                    }
                }
            }
            Some(o) => {
                if want_exit.is_none() {
                    if let Some(fx) = o.fill_x {
                        let timed = p.max_hold > 0 && t - fx >= p.max_hold;
                        let by_rule = match p.rule {
                            Rule::Z => {
                                let dz = f64::from(o.dir) * z;
                                z.is_finite() && (dz >= -p.exit_z || dz <= -p.stop_z)
                            }
                            Rule::Funding { .. } => {
                                let edge = -f64::from(o.dir) * r_mean * 1e4 * fh;
                                // не «выше круга», в том числе когда ставки ещё нет (NaN)
                                edge.partial_cmp(&round_trip_cost_bps(p, o.beta, wsum))
                                    != Some(std::cmp::Ordering::Greater)
                            }
                        };
                        if by_rule || timed {
                            let reason = if !by_rule {
                                ExitReason::Timeout
                            } else if matches!(p.rule, Rule::Z) && f64::from(o.dir) * z <= -p.stop_z
                            {
                                ExitReason::Stop
                            } else {
                                ExitReason::Rule
                            };
                            want_exit = Some((t, reason));
                        }
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
