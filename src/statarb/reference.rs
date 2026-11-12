//! Простая референсная реализация ядра (TK-479): окно пересчитывается заново на каждой минуте
//! (O(W), два прохода, без скользящих сумм), фандинг — постфактум по списку событий. Правила те же,
//! код другой: сверка `kernel::run_pair` с ней — тест и вариант `naive` в таблице замера.

use super::kernel::{
    round_trip_cost_bps, BetaMode, Counters, PairInput, Params, Rule, Sink, Trade,
};

/// Нога для фандинга: события, open, количество.
type Leg<'a> = (&'a [(i64, f64)], &'a [f64], f64);

/// Значение ряда в минуте `t`. Ряды не короче `ly` (`PairInput::assert_aligned`): `NaN` недостижим.
fn at(s: &[f64], t: usize) -> f64 {
    s.get(t).copied().unwrap_or(f64::NAN)
}

/// z-скор и β на каждой минуте окна `[t−W+1, t]` (двухпроходная формула).
pub fn z_series(inp: &PairInput, p: &Params) -> Vec<(f64, f64)> {
    inp.assert_aligned();
    let n = inp.ly.len();
    let mut out = vec![(f64::NAN, f64::NAN); n];
    // пары (x, y) по минутам: окно берётся срезом по обоим рядам сразу
    let xy: Vec<(f64, f64)> = inp.lx.iter().zip(inp.ly).map(|(&x, &y)| (x, y)).collect();
    for (t, (&(xt, yt), slot)) in xy.iter().zip(out.iter_mut()).enumerate() {
        if !(xt.is_finite() && yt.is_finite()) {
            continue;
        }
        let lo = (t + 1).saturating_sub(p.window);
        let pts: Vec<(f64, f64)> = xy
            .get(lo..=t)
            .unwrap_or_default()
            .iter()
            .copied()
            .filter(|&(x, y)| x.is_finite() && y.is_finite())
            .collect();
        if pts.len() < p.min_obs.max(2) {
            continue;
        }
        #[allow(clippy::cast_precision_loss)] // точек в окне ≪ 2^53
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
            *slot = (beta, f64::NAN);
            continue;
        }
        *slot = (beta, (yt - beta * xt - mr) / (ssr / m).sqrt());
    }
    out
}

struct Open {
    dir: i8,
    sig_in: usize,
    fill_in: usize,
    /// Минута входа ноги X; `None` — нога X ещё ждёт срока или open.
    fill_x: Option<usize>,
    beta: f64,
    /// Количество и цена входа ноги Y; ноги X — `qty_x`, `pin_x` (пусты, пока нога X не вошла).
    qty_y: f64,
    pin_y: f64,
    qty_x: Vec<f64>,
    pin_x: Vec<f64>,
}

/// Последний конечный open не позже минуты `t`; позиция открыта по конечному open: `NaN` недостижим.
fn last_open(o: &[f64], t: usize) -> f64 {
    o.get(..=t)
        .and_then(|h| h.iter().rev().copied().find(|v| v.is_finite()))
        .unwrap_or(f64::NAN)
}

/// Трейлинг-среднее `k` последних выплат с минутой ≤ `ft`; `NaN`, если выплат меньше `k`.
fn trailing_mean(ev: &[(i64, f64)], ft: i64, k: usize) -> f64 {
    let cnt = ev.partition_point(|e| e.0 <= ft);
    if cnt < k {
        return f64::NAN;
    }
    let mut acc = 0.0;
    for e in ev.iter().take(cnt).skip(cnt - k) {
        acc += e.1;
    }
    #[allow(clippy::cast_precision_loss)] // число выплат в среднем мало
    let kf = k as f64;
    acc / kf
}

pub fn run_pair<S: Sink>(inp: &PairInput, p: &Params, sink: &mut S) -> Counters {
    inp.assert_aligned();
    let n = inp.ly.len();
    let zs = z_series(inp, p);
    let wsum = inp.wx.iter().sum::<f64>();
    #[allow(clippy::cast_precision_loss)] // число выплат ≪ 2^53
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
    let mut want_exit: Option<usize> = None;
    for t in 0..n {
        let oy_t = at(inp.oy, t);
        let ok = oy_t.is_finite() && inp.ox.iter().all(|o| at(o, t).is_finite());
        if let Some((d, beta, sig)) = want_enter.take() {
            // Без задержки нужны open всех ног в эту минуту; с задержкой — только у Y.
            if (p.delay_x == 0 && ok) || (p.delay_x > 0 && oy_t.is_finite()) {
                pos = Some(Open {
                    dir: d,
                    sig_in: sig,
                    fill_in: t,
                    fill_x: None,
                    beta,
                    qty_y: f64::from(d) / oy_t,
                    pin_y: oy_t,
                    qty_x: Vec::new(),
                    pin_x: Vec::new(),
                });
                c.entries += 1;
            } else {
                c.entry_deferred += 1;
            }
        }
        // Нога X: срок `fill_in + delay_x`, дальше — первая минута с open у всех компонент.
        if let Some(o) = pos.as_mut() {
            if o.fill_x.is_none() && t >= o.fill_in.saturating_add(p.delay_x) {
                if inp.ox.iter().all(|x| at(x, t).is_finite()) {
                    for (x, &w) in inp.ox.iter().zip(inp.wx) {
                        o.qty_x.push(-f64::from(o.dir) * o.beta * w / at(x, t));
                        o.pin_x.push(at(x, t));
                    }
                    o.fill_x = Some(t);
                } else {
                    c.x_deferred += 1;
                }
            }
        }
        if let Some(sig) = want_exit {
            if ok {
                #[allow(clippy::expect_used)]
                // инвариант: выход запрашивают только по открытой позиции
                let o = pos.take().expect("выход без позиции");
                #[allow(clippy::expect_used)]
                // инвариант: выход запрашивают, когда нога X уже вошла
                let fx = o.fill_x.expect("выход без ноги X");
                // Ноги: (вид, количество, цена входа, цена выхода); первая — Y.
                let pnl_legs = std::iter::once((p.y_kind, o.qty_y, o.pin_y, oy_t)).chain(
                    o.qty_x
                        .iter()
                        .zip(&o.pin_x)
                        .zip(inp.ox)
                        .map(|((&q, &pin), x)| (p.x_kind, q, pin, at(x, t))),
                );
                let mut gross = 0.0;
                let mut fee = 0.0;
                for (kind, q, pin, out) in pnl_legs {
                    gross += q * (out - pin);
                    fee += q.abs()
                        * (pin * kind.fee_bps(p.taker_in) + out * kind.fee_bps(p.taker_out));
                }
                // Фандинг постфактум: каждая нога — за метки строго между её входом и минутой выхода.
                let mut fund = 0.0;
                for m in (o.fill_in + 1)..t {
                    let ft = inp.first_min + m as i64;
                    let y_leg: (Leg, usize) = ((inp.fund_y, inp.oy, o.qty_y), o.fill_in);
                    let x_legs = inp
                        .ox
                        .iter()
                        .zip(&o.qty_x)
                        .enumerate()
                        .map(|(i, (&x, &q))| {
                            let ev = inp.fund_x.get(i).copied().unwrap_or(&[]);
                            let leg: Leg = (ev, x, q);
                            (leg, fx)
                        });
                    for ((ev, op, q), start) in std::iter::once(y_leg).chain(x_legs) {
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
        if at(inp.lx, t).is_finite() && at(inp.ly, t).is_finite() {
            c.obs_minutes += 1;
        } else {
            c.gap_minutes += 1;
        }
        let (beta, z) = zs.get(t).copied().unwrap_or((f64::NAN, f64::NAN));
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
                                if z <= -p.entry_z {
                                    want_enter = Some((1, beta, t));
                                } else if z >= p.entry_z {
                                    want_enter = Some((-1, beta, t));
                                }
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
                                want_enter = Some((if r_mean > 0.0 { -1 } else { 1 }, b, t));
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
                            want_exit = Some(t);
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
