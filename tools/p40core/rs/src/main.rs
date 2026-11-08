//! TK-500 П-40 §3.1–3.2: ядро эпизодов схождения (v0): книга двух бирж -> уровень -> эпизоды. Определения — в шапке tools/p40core/ref_episodes.py;
//! вход — плоские бинарники tools/p40core/to_bin.py (<dirA>/<ЧЧ>.bin, <dirB>/<ЧЧ>.bin), выход — csv построчно как у эталона.
//! Запуск: p40core <out.csv> <dirA> <dirB> [<trA> <trB> <lotA> <lotB> [<L_ms=500>]] — с сделками дополнительно <out.csv>.fills.csv (заявки входа §3.3, проход 2).
mod book;
mod feats;
mod fills;
mod queue;
use book::{load_trades, Book, Rows};
use std::io::Write;
use std::time::Instant;

const THETAS: [f64; 5] = [5.04, 12.6, 25.2, 50.0, 100.0];
const L_S: usize = 21600;
const GAP_S: usize = 60;
const W_MS: i64 = 60000;
const T_LIFE_MS: i64 = 60000;

/// Общее для проходов 2–3: ряды ног (A, B), книги/сделки/лоты ног, ряд d, задержка `L_ms`.
struct Ctx<'a> {
    leg: [&'a Leg; 2],
    tu: &'a [i64],
    ds: &'a [f64],
    dirs: [&'a str; 2],
    tr: [Vec<book::Tr>; 2],
    lot: [f64; 2],
    l_ms: i64,
    lev: &'a [f64],
    s0: i64,
}

struct Leg {
    t: Vec<i64>,
    b: Vec<f64>,
    a: Vec<f64>,
    crossed: u64,
    nonmono: u64,
}

impl Leg {
    /// Лучшие (бид, аск) после всех событий со временем ≤ `t`.
    fn bbo(&self, t: i64) -> Option<(f64, f64)> {
        let k = self.t.partition_point(|&u| u <= t);
        (k > 0).then(|| (self.b[k - 1], self.a[k - 1]))
    }
}

fn leg_series(dir: &str) -> Leg {
    let mut book = Book::default();
    let mut leg = Leg { t: vec![], b: vec![], a: vec![], crossed: 0, nonmono: 0 };
    let mut last_t = -1i64;
    for h in 0..24 {
        let Some(rows) = Rows::load(dir, h) else { continue };
        for i in 0..rows.len() {
            if rows.snap_start(i) {
                book.clear();
            }
            book.set(rows.is_bid(i), rows.px(i), rows.qt(i));
            if !rows.group_end(i) {
                continue;
            }
            if let Some((bb, aa)) = book.best() {
                if bb >= aa {
                    leg.crossed += 1;
                    continue;
                }
                let mut te = rows.et(i);
                if te < last_t {
                    leg.nonmono += 1;
                    te = last_t;
                }
                last_t = te;
                if leg.t.last() == Some(&te) {
                    *leg.b.last_mut().unwrap() = bb;
                    *leg.a.last_mut().unwrap() = aa;
                } else {
                    leg.t.push(te);
                    leg.b.push(bb);
                    leg.a.push(aa);
                }
            }
        }
    }
    leg
}

fn x_series(a: &Leg, b: &Leg) -> (Vec<i64>, Vec<f64>) {
    let (mut i, mut j) = (0usize, 0usize);
    let (mut tu, mut x) = (vec![], vec![]);
    while i < a.t.len() || j < b.t.len() {
        let t = match (a.t.get(i), b.t.get(j)) {
            (Some(&u), Some(&v)) => u.min(v),
            (Some(&u), None) => u,
            (None, Some(&v)) => v,
            _ => unreachable!(),
        };
        while i < a.t.len() && a.t[i] == t {
            i += 1;
        }
        while j < b.t.len() && b.t[j] == t {
            j += 1;
        }
        if i == 0 || j == 0 {
            continue;
        }
        let ma = (a.b[i - 1] + a.a[i - 1]) / 2.0;
        let mb = (b.b[j - 1] + b.a[j - 1]) / 2.0;
        tu.push(t);
        x.push((ma / mb).ln());
    }
    (tu, x)
}

fn level_grid(tu: &[i64], x: &[f64], t0: i64, t1: i64) -> Vec<f64> {
    let ns = (t1 - t0 + 1) as usize;
    let mut g = vec![f64::NAN; ns];
    let mut p = 0usize;
    for (j, gj) in g.iter_mut().enumerate() {
        let ts = (t0 + j as i64) * 1000;
        while p < tu.len() && tu[p] <= ts {
            p += 1;
        }
        if p > 0 {
            *gj = x[p - 1];
        }
    }
    let mut lev = vec![f64::NAN; ns];
    let mut win: Vec<f64> = Vec::with_capacity(L_S);
    let mut nnan = 0i64;
    for j in 0..ns {
        if j >= GAP_S {
            let v = g[j - GAP_S];
            if v == v {
                let k = win.partition_point(|&w| w < v);
                win.insert(k, v);
            } else {
                nnan += 1;
            }
        }
        if j > L_S {
            let v = g[j - L_S - 1];
            if v == v {
                let k = win.partition_point(|&w| w < v);
                win.remove(k);
            } else {
                nnan -= 1;
            }
        }
        if j >= L_S && nnan == 0 && win.len() == L_S - GAP_S + 1 {
            let m = win.len();
            lev[j] = if m % 2 == 1 { win[m / 2] } else { (win[m / 2 - 1] + win[m / 2]) / 2.0 };
        }
    }
    lev
}

struct Ep {
    theta: f64,
    t0: i64,
    dir: i64,
    d0: f64,
    peak: f64,
    t_peak: i64,
    t_half: i64,
    t_th2: i64,
    t_q: i64,
    t_end: i64,
}

/// Ряд d (bps) на моментах `tu`: x минус уровень на секунде момента; NaN — уровень не определён.
fn d_series(tu: &[i64], x: &[f64], s0: i64, lev: &[f64]) -> Vec<f64> {
    tu.iter()
        .zip(x)
        .map(|(&t, &xi)| {
            let js = t / 1000 - s0;
            let lv = if js >= 0 && (js as usize) < lev.len() { lev[js as usize] } else { f64::NAN };
            1e4 * (xi - lv)
        })
        .collect()
}

fn episodes(tu: &[i64], ds: &[f64], theta: f64) -> Vec<Ep> {
    let mut out = vec![];
    let mut calm: Option<i64> = None;
    let mut prev: Option<f64> = None;
    let mut open: Option<Ep> = None;
    let th2 = theta / 2.0;
    for i in 0..tu.len() {
        let di = ds[i];
        if di != di {
            calm = None;
            prev = None;
            continue;
        }
        let ad = di.abs();
        let t = tu[i];
        if let Some(e) = open.as_mut() {
            let s = e.dir as f64 * di;
            if s > e.peak {
                e.peak = s;
                e.t_peak = t;
            }
            if e.t_th2 < 0 && s <= th2 {
                e.t_th2 = t;
            }
            if e.t_half < 0 && s <= 0.5 * e.d0 {
                e.t_half = t;
            }
            if e.t_q < 0 && s <= 0.25 * e.d0 {
                e.t_q = t;
            }
            if s <= 0.0 {
                e.t_end = t;
                out.push(open.take().unwrap());
            }
        } else if let (Some(pa), Some(c)) = (prev, calm) {
            if pa < th2 && ad >= theta && t - c >= W_MS {
                open = Some(Ep { theta, t0: t, dir: if di > 0.0 { 1 } else { -1 }, d0: ad, peak: ad, t_peak: t, t_half: -1, t_th2: -1, t_q: -1, t_end: -1 });
            }
        }
        if open.is_none() {
            if ad < th2 {
                if calm.is_none() || prev.map_or(true, |p| p >= th2) {
                    calm = Some(t);
                }
            } else {
                calm = None;
            }
        }
        prev = Some(ad);
    }
    if let Some(e) = open {
        out.push(e);
    }
    out
}

/// Момент вступления отмены заявок входа: решение min(θ/2, t0+T_life) + задержка отмены `L_ms` (как у постановки).
fn cancel_at(e: &Ep, l_ms: i64) -> i64 {
    let dec = if e.t_th2 >= 0 { e.t_th2.min(e.t0 + T_LIFE_MS) } else { e.t0 + T_LIFE_MS };
    dec + l_ms
}

/// Проход 2 (аргументы: trA trB lotA lotB): заявки входа обеих ног каждого эпизода; таблица `<out>.fills.csv`.
fn fills_pass(out: &str, c: &Ctx, eps: &[Ep]) {
    let (la, lb, l_ms, dirs) = (c.leg[0], c.leg[1], c.l_ms, c.dirs);
    let (lot_a, lot_b) = (c.lot[0], c.lot[1]);
    // индексы заданий: ноги A и B; продаёт дорогая нога (dir=+1 — A), покупает дешёвая
    let mut ja: Vec<fills::Job> = vec![];
    let mut jb: Vec<fills::Job> = vec![];
    // цена — лучшая своей стороны в момент решения t0 (состояние после всех событий ≤ t0); заявка приходит в t0+L, снимается решением (θ/2 или T_life от t0) + L
    let at = |leg: &Leg, buy: bool, t0: i64, tp: i64, tc: i64| {
        let k = leg.t.partition_point(|&t| t <= t0);
        let price = if k == 0 { f64::NAN } else if buy { leg.b[k - 1] } else { leg.a[k - 1] };
        (price, tc > tp && price.is_finite())
    };
    let mut slot: Vec<[Option<usize>; 2]> = vec![];
    for e in eps {
        let tp = e.t0 + l_ms;
        let tc = cancel_at(e, l_ms);
        let (buy_a, buy_b) = (e.dir < 0, e.dir > 0);
        let (pa, oka) = at(la, buy_a, e.t0, tp, tc);
        let (pb, okb) = at(lb, buy_b, e.t0, tp, tc);
        let mut s = [None, None];
        if oka {
            s[0] = Some(ja.len());
            ja.push(fills::Job::new(buy_a, tp, tc, pa, lot_a));
        }
        if okb {
            s[1] = Some(jb.len());
            jb.push(fills::Job::new(buy_b, tp, tc, pb, lot_b));
        }
        slot.push(s);
    }
    fills::run(dirs[0], &c.tr[0], &mut ja);
    fills::run(dirs[1], &c.tr[1], &mut jb);
    let mut o = String::from("theta,t0_ms,dir,t_th2_ms,tc_ms,sell_leg,front_sell,fill_sell_ms,imm_sell,front_buy,fill_buy_ms,imm_buy
");
    for (e, s) in eps.iter().zip(&slot) {
        let rel = |v: i64| if v >= 0 { v - e.t0 } else { -1 };
        let (sell, buy) = if e.dir > 0 { (s[0].map(|k| &ja[k]), s[1].map(|k| &jb[k])) } else { (s[1].map(|k| &jb[k]), s[0].map(|k| &ja[k])) };
        let f = |j: Option<&fills::Job>| j.map_or((-1.0, -1, 0), |j| (j.front0, rel(j.fill), j.imm as u8));
        let (fs, ts, is) = f(sell);
        let (fb, tb, ib) = f(buy);
        o += &format!("{:?},{},{},{},{},{},{},{},{},{},{},{}
", e.theta, e.t0, e.dir, rel(e.t_th2), cancel_at(e, l_ms) - e.t0, if e.dir > 0 { "A" } else { "B" }, fs, ts, is, fb, tb, ib);
    }
    std::fs::File::create(format!("{out}.fills.csv")).unwrap().write_all(o.as_bytes()).unwrap();
    closing(out, c, eps, [ja, jb], &slot);
}

const NEVER: i64 = i64::MAX / 2;
const MAKER_BPS: f64 = 1.26;
const TAKER_BPS: f64 = 3.15;

/// Что делаем с эпизодом после входа (§3.3): обе ноги — выход мейкером по сигналу, одна — страховка, ни одной — ничего.
enum Plan {
    Skip,
    NoSignal,
    /// выход: заявки на ногах (индексы в `x[нога]`), продажа-нога выкупается по `xs`, купленная продаётся по `xb`
    Exit { js: usize, jb: usize, xs: f64, xb: f64 },
    /// страховка: закрывается исполненная нога (`sell_done` — проданная), `t_c` — момент решения, `j` — индекс в `x[нога]`
    Insure { sell_done: bool, t_c: i64, xp: f64, j: usize },
}

/// Проход 3: выход при обеих ногах (заявки на лучшей цене своей стороны при `dir·d ≤ d0/2`, без снятия) и страховка при одной
/// (закрытие мейкером `T_life`, затем тейкер). Один эпизод — независимая симуляция. Лоты выхода — те же, что у входа (константы аргументов).
/// Таблица `<out>.exits.csv`: theta,t0_ms,outcome,exit_ms,exit_kind,pnl_bps,fees_bps (pnl — в bps номинала ноги, комиссии мейкер 1,26 / тейкер 3,15 bps, §8).
fn closing(out: &str, c: &Ctx, eps: &[Ep], jobs: [Vec<fills::Job>; 2], slot: &[[Option<usize>; 2]]) {
    let mut x: [Vec<fills::Job>; 2] = [vec![], vec![]];
    let mut plans: Vec<Plan> = vec![];
    for (e, s) in eps.iter().zip(slot) {
        // продаёт дорогая нога: dir=+1 — A (0), иначе B (1)
        let (si, bi) = if e.dir > 0 { (0, 1) } else { (1, 0) };
        let sj = s[si].map(|k| &jobs[si][k]);
        let bj = s[bi].map(|k| &jobs[bi][k]);
        let (fs, fb) = (sj.map_or(-1, |j| j.fill), bj.map_or(-1, |j| j.fill));
        if fs < 0 && fb < 0 {
            plans.push(Plan::Skip);
        } else if fs >= 0 && fb >= 0 {
            let t_both = fs.max(fb);
            let k0 = c.tu.partition_point(|&t| t <= t_both);
            let hit = (k0..c.tu.len()).find(|&k| c.ds[k].is_finite() && e.dir as f64 * c.ds[k] <= 0.5 * e.d0);
            let Some(k) = hit else {
                plans.push(Plan::NoSignal);
                continue;
            };
            let t_x = c.tu[k];
            let xs = c.leg[si].bbo(t_x).unwrap().0;
            let xb = c.leg[bi].bbo(t_x).unwrap().1;
            x[si].push(fills::Job::new(true, t_x + c.l_ms, NEVER, xs, c.lot[si]));
            x[bi].push(fills::Job::new(false, t_x + c.l_ms, NEVER, xb, c.lot[bi]));
            plans.push(Plan::Exit { js: x[si].len() - 1, jb: x[bi].len() - 1, xs, xb });
        } else {
            let sell_done = fs >= 0;
            let t_c = if sell_done { fs } else { fb }.max(cancel_at(e, c.l_ms));
            let (li, buy) = if sell_done { (si, true) } else { (bi, false) };
            let (b, a) = c.leg[li].bbo(t_c).unwrap();
            let xp = if sell_done { b } else { a };
            x[li].push(fills::Job::new(buy, t_c + c.l_ms, t_c + T_LIFE_MS + c.l_ms, xp, c.lot[li]));
            plans.push(Plan::Insure { sell_done, t_c, xp, j: x[li].len() - 1 });
        }
    }
    fills::run(c.dirs[0], &c.tr[0], &mut x[0]);
    fills::run(c.dirs[1], &c.tr[1], &mut x[1]);
    let mut o = String::from("theta,t0_ms,outcome,exit_ms,exit_kind,pnl_bps,fees_bps\n");
    for ((e, s), plan) in eps.iter().zip(slot).zip(&plans) {
        let (si, bi) = if e.dir > 0 { (0, 1) } else { (1, 0) };
        let sj = s[si].map(|k| &jobs[si][k]);
        let bj = s[bi].map(|k| &jobs[bi][k]);
        let (ps, pb) = (sj.map_or(f64::NAN, |j| j.price), bj.map_or(f64::NAN, |j| j.price));
        let (outcome, exit_t, kind, pnl, fees) = match *plan {
            Plan::Skip => ("none", -1, "", f64::NAN, f64::NAN),
            Plan::NoSignal => ("both", -1, "no_exit_signal", f64::NAN, f64::NAN),
            Plan::Exit { js, jb, xs, xb } => {
                let (rs, rb) = (x[si][js].fill, x[bi][jb].fill);
                if rs >= 0 && rb >= 0 {
                    let t = rs.max(rb);
                    let kind = if e.t_end > 0 && t > e.t_end { "maker_after_mid" } else { "maker" };
                    let pnl = ((ps - xs) / ps + (xb - pb) / pb) * 1e4;
                    ("both", t, kind, pnl - 4.0 * MAKER_BPS, 4.0 * MAKER_BPS)
                } else {
                    ("both", -1, "exit_unfilled_censored", f64::NAN, f64::NAN)
                }
            }
            Plan::Insure { sell_done, t_c, xp, j } => {
                let li = if sell_done { si } else { bi };
                let r = x[li][j].fill;
                let (t, kind, fee, xp) = if r >= 0 {
                    (r, "insurance_maker", 2.0 * MAKER_BPS, xp)
                } else {
                    let t_t = t_c + c.l_ms + T_LIFE_MS + c.l_ms;
                    let (b2, a2) = c.leg[li].bbo(t_t).unwrap();
                    (t_t, "insurance_taker", MAKER_BPS + TAKER_BPS, if sell_done { a2 } else { b2 })
                };
                let pnl = if sell_done { (ps - xp) / ps } else { (xp - pb) / pb } * 1e4;
                ("one", t, kind, pnl - fee, fee)
            }
        };
        o += &format!("{:?},{},{},{},{},{},{}\n", e.theta, e.t0, outcome, exit_t, kind, pnl, fees);
    }
    std::fs::File::create(format!("{out}.exits.csv")).unwrap().write_all(o.as_bytes()).unwrap();
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let t = Instant::now();
    let la = leg_series(&a[2]);
    eprintln!("A {} состояний crossed {} nonmono {} {:.1}s", la.t.len(), la.crossed, la.nonmono, t.elapsed().as_secs_f64());
    let lb = leg_series(&a[3]);
    eprintln!("B {} состояний crossed {} nonmono {} {:.1}s", lb.t.len(), lb.crossed, lb.nonmono, t.elapsed().as_secs_f64());
    let (tu, x) = x_series(&la, &lb);
    eprintln!("объединённых моментов {} {:.1}s", tu.len(), t.elapsed().as_secs_f64());
    let (t0, t1) = (tu[0] / 1000, tu[tu.len() - 1] / 1000);
    let lev = level_grid(&tu, &x, t0, t1);
    eprintln!("уровень: определён на {} из {} с {:.1}s", lev.iter().filter(|v| v.is_finite()).count(), lev.len(), t.elapsed().as_secs_f64());
    let ds = d_series(&tu, &x, t0, &lev);
    let mut o = String::from("theta,t0_ms,dir,d0_bps,peak_bps,t_peak_ms,mae_mid_bps,t_half_ms,t_quarter_ms,t_conv_ms,censored\n");
    let mut all: Vec<Ep> = vec![];
    for th in THETAS {
        for e in episodes(&tu, &ds, th) {
            let r = |v: i64| if v >= 0 { v - e.t0 } else { -1 };
            o += &format!(
                "{:?},{},{},{:.4},{:.4},{},{:.4},{},{},{},{}\n",
                e.theta, e.t0, e.dir, e.d0, e.peak, e.t_peak - e.t0, e.peak - e.d0, r(e.t_half), r(e.t_q), r(e.t_end), if e.t_end >= 0 { 0 } else { 1 }
            );
            all.push(e);
        }
    }
    std::fs::File::create(&a[1]).unwrap().write_all(o.as_bytes()).unwrap();
    if a.len() >= 8 {
        fills::NO_CROSS.store(std::env::var_os("P40_NO_CROSS").is_some(), std::sync::atomic::Ordering::Relaxed);
        let c = Ctx {
            leg: [&la, &lb],
            tu: &tu,
            ds: &ds,
            dirs: [&a[2], &a[3]],
            tr: [load_trades(&a[4]), load_trades(&a[5])],
            lot: [a[6].parse().unwrap(), a[7].parse().unwrap()],
            l_ms: a.get(8).map_or(500, |v| v.parse().unwrap()),
            lev: &lev,
            s0: t0,
        };
        fills_pass(&a[1], &c, &all);
        feats::run(&a[1], &c, &all.iter().map(|e| (e.theta, e.t0, e.dir, e.d0)).collect::<Vec<_>>());
    }
    eprintln!("готово {:.2}s", t.elapsed().as_secs_f64());
}
