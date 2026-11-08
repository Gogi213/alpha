//! TK-500 П-40 §3.5: признаки эпизода из книги и сделок (проход 4): depth_slip, spread, vol5m, plevel, flow1m, bookimb, leader, hour, d_in.
//! Не сделано: oi5m, liq5m, Φ_t (нужны ряды mark/OI/ликвидаций, конвертеров нет). Терцили — стадия статистики (границы D1 фиксируются для D2).
//! Определения и знаки — как в эталоне TK-501 (tools/p40ref/features.py): книга на t0 — последнее состояние ≤ t0; сделки, средняя цена, вол — строго до t0.
use crate::book::{Book, Rows, Tr};
use crate::{Ctx, Leg};
use std::io::Write;

const NOTIONAL: f64 = 1000.0;

#[derive(Clone, Copy)]
pub struct Snap {
    bid: f64,
    ask: f64,
    vw_bid: f64,
    vw_ask: f64,
    t5_bid: f64,
    t5_ask: f64,
}

/// VWAP набора `NOTIONAL` USDT вглубь от лучшей цены; -1 — глубины не хватает.
fn sweep(lv: &[(f64, f64)]) -> f64 {
    let (mut need, mut sq, mut sv) = (NOTIONAL, 0.0, 0.0);
    for &(p, q) in lv {
        let take = q.min(need / p);
        sq += take;
        sv += take * p;
        need -= take * p;
        if need <= 1e-9 {
            return sv / sq;
        }
    }
    -1.0
}

fn top5(lv: &[(f64, f64)]) -> f64 {
    lv.iter().take(5).map(|&(p, q)| p * q).sum()
}

fn snap(book: &mut Book, mult: f64) -> Option<Snap> {
    let (bid, ask) = book.best()?;
    let sc = |v: Vec<(f64, f64)>| v.into_iter().map(|(p, q)| (p, q * mult)).collect::<Vec<_>>();
    let (b, a) = (sc(book.levels(true)), sc(book.levels(false)));
    Some(Snap { bid, ask, vw_bid: sweep(&b), vw_ask: sweep(&a), t5_bid: top5(&b), t5_ask: top5(&a) })
}

/// Состояние книги ноги в моменты `q` (по возрастанию): последнее состояние со временем ≤ q (группа с временем > q ещё не применена).
fn probes(dir: &str, q: &[i64], mult: f64) -> Vec<Option<Snap>> {
    let mut book = Book::default();
    let mut out = vec![None; q.len()];
    let (mut qi, mut fresh) = (0usize, true);
    for h in 0..24 {
        let Some(rows) = Rows::load(dir, h) else { continue };
        for i in 0..rows.len() {
            if fresh {
                while qi < q.len() && q[qi] < rows.et(i) {
                    out[qi] = snap(&mut book, mult);
                    qi += 1;
                }
            }
            if rows.snap_start(i) {
                book.clear();
            }
            book.set(rows.is_bid(i), rows.px(i), rows.qt(i));
            fresh = rows.group_end(i);
        }
    }
    while qi < q.len() {
        out[qi] = snap(&mut book, mult);
        qi += 1;
    }
    out
}

/// Реализованная вол mid за прошлые 5 мин: sd приращений ln mid на сетке 1 с (ddof=0), bps.
fn vol5m(l: &Leg, t: i64) -> f64 {
    let mut m = vec![];
    let mut g = t - 300_000;
    while g < t {
        if g > l.t[0] {
            let k = l.t.partition_point(|&u| u <= g);
            m.push(((l.b[k - 1] + l.a[k - 1]) / 2.0).ln());
        }
        g += 1000;
    }
    if m.len() < 10 {
        return f64::NAN;
    }
    let d: Vec<f64> = m.windows(2).map(|w| w[1] - w[0]).collect();
    let mu = d.iter().sum::<f64>() / d.len() as f64;
    (d.iter().map(|x| (x - mu).powi(2)).sum::<f64>() / d.len() as f64).sqrt() * 1e4
}

fn mid_before(l: &Leg, t: i64) -> f64 {
    let k = l.t.partition_point(|&u| u < t);
    if k == 0 { f64::NAN } else { (l.b[k - 1] + l.a[k - 1]) / 2.0 }
}

/// (покупки − продажи)/(сумма) по номиналу за прошлую 1 мин, taker_sell = is_buyer_maker.
fn flow_imb(tr: &[Tr], t: i64) -> f64 {
    let (a, b) = (tr.partition_point(|x| x.t < t - 60_000), tr.partition_point(|x| x.t < t));
    let (mut s, mut bu) = (0.0, 0.0);
    for x in &tr[a..b] {
        if x.buyer_maker { s += x.qty * x.px } else { bu += x.qty * x.px }
    }
    if b > a && bu + s > 0.0 { (bu - s) / (bu + s) } else { f64::NAN }
}

fn nanmean(v: &[f64]) -> f64 {
    let f: Vec<f64> = v.iter().copied().filter(|x| x.is_finite()).collect();
    if f.is_empty() { f64::NAN } else { f.iter().sum::<f64>() / f.len() as f64 }
}

fn nanmax(v: &[f64]) -> f64 {
    v.iter().copied().filter(|x| x.is_finite()).fold(f64::NAN, f64::max)
}

/// Таблица `<out>.features.csv`; строки в порядке `eps` = (theta, t0, dir, d0).
pub fn run(out: &str, c: &Ctx, eps: &[(f64, i64, i64, f64)]) {
    let mut idx: Vec<usize> = (0..eps.len()).collect();
    idx.sort_by_key(|&i| eps[i].1);
    let q: Vec<i64> = idx.iter().map(|&i| eps[i].1).collect();
    // единицы количества: A — базовый актив (лот A), B — контракты; база = лот A / лот ноги
    let (pa, pb) = (probes(c.dirs[0], &q, 1.0), probes(c.dirs[1], &q, c.lot[0] / c.lot[1]));
    let mut sa = vec![None; eps.len()];
    let mut sb = vec![None; eps.len()];
    for (k, &i) in idx.iter().enumerate() {
        sa[i] = pa[k];
        sb[i] = pb[k];
    }
    let mut o = String::from("theta,t_start,dir,d_in,depth_slip,spread,vol5m,plevel,flow1m,bookimb,leader,hour\n");
    for (i, &(theta, t, dr, d0)) in eps.iter().enumerate() {
        let (a, b) = (sa[i], sb[i]);
        let (mut slip, mut spread, mut bimb) = (f64::NAN, f64::NAN, f64::NAN);
        if let (Some(a), Some(b)) = (a, b) {
            let ((sell, buy), _) = if dr > 0 { ((a, b), 0) } else { ((b, a), 0) };
            let sl = |vw: f64, best: f64| if vw > 0.0 { (vw - best).abs() / best * 1e4 } else { f64::NAN };
            slip = nanmax(&[sl(sell.vw_bid, sell.bid), sl(buy.vw_ask, buy.ask)]);
            let sp = |s: &Snap| (s.ask - s.bid) / ((s.ask + s.bid) / 2.0) * 1e4;
            spread = sp(&a).max(sp(&b));
            let im = |s: &Snap| if s.t5_bid + s.t5_ask > 0.0 { (s.t5_bid - s.t5_ask) / (s.t5_bid + s.t5_ask) } else { f64::NAN };
            bimb = nanmean(&[im(&a) * dr as f64, im(&b) * -(dr as f64)]);
        }
        let vol = nanmean(&[vol5m(c.leg[0], t), vol5m(c.leg[1], t)]);
        let js = t / 1000 - c.s0;
        let plevel = if js >= 0 && (js as usize) < c.lev.len() { c.lev[js as usize] * 1e4 } else { f64::NAN };
        let flow = nanmean(&[flow_imb(&c.tr[0], t) * dr as f64, flow_imb(&c.tr[1], t) * -(dr as f64)]);
        let ret = |l: &Leg| (mid_before(l, t) / mid_before(l, t - 10_000)).ln();
        let (ra, rb) = (ret(c.leg[0]), ret(c.leg[1]));
        let leader = if ra.abs() == rb.abs() || !ra.is_finite() || !rb.is_finite() { -1 } else { (ra.abs() > rb.abs()) as i32 };
        let hour = ((t / 1000) % 86400) / 3600 / 6;
        o += &format!("{:?},{},{},{:.10},{:.10},{:.10},{:.10},{:.10},{:.10},{:.10},{},{}\n", theta, t, dr, d0, slip, spread, vol, plevel, flow, bimb, leader, hour);
    }
    std::fs::File::create(format!("{out}.features.csv")).unwrap().write_all(o.as_bytes()).unwrap();
}
