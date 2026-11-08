//! TK-500 П-40 §3.1–3.2: ядро эпизодов схождения (v0): книга двух бирж -> уровень -> эпизоды. Определения — в шапке tools/p40core/ref_episodes.py;
//! вход — плоские бинарники tools/p40core/to_bin.py (<dirA>/<ЧЧ>.bin, <dirB>/<ЧЧ>.bin), выход — csv построчно как у эталона.
#[allow(dead_code)]
mod queue;
use std::cmp::Reverse;
use std::collections::{BinaryHeap, HashMap};
use std::io::Write;
use std::time::Instant;

const THETAS: [f64; 5] = [5.04, 12.6, 25.2, 50.0, 100.0];
const L_S: usize = 21600;
const GAP_S: usize = 60;
const W_MS: i64 = 60000;

struct Leg {
    t: Vec<i64>,
    b: Vec<f64>,
    a: Vec<f64>,
    crossed: u64,
    nonmono: u64,
}

fn leg_series(dir: &str) -> Leg {
    let mut bids: HashMap<u64, f64> = HashMap::new();
    let mut asks: HashMap<u64, f64> = HashMap::new();
    let mut hb: BinaryHeap<u64> = BinaryHeap::new();
    let mut ha: BinaryHeap<Reverse<u64>> = BinaryHeap::new();
    let mut leg = Leg { t: vec![], b: vec![], a: vec![], crossed: 0, nonmono: 0 };
    let mut last_t = -1i64;
    for h in 0..24 {
        let path = format!("{dir}/{h:02}.bin");
        let Ok(raw) = std::fs::read(&path) else { continue };
        let n = raw.len() / 33;
        let et = |i: usize| i64::from_le_bytes(raw[i * 33..i * 33 + 8].try_into().unwrap());
        let fu = |i: usize| i64::from_le_bytes(raw[i * 33 + 8..i * 33 + 16].try_into().unwrap());
        let px = |i: usize| f64::from_le_bytes(raw[i * 33 + 16..i * 33 + 24].try_into().unwrap());
        let qt = |i: usize| f64::from_le_bytes(raw[i * 33 + 24..i * 33 + 32].try_into().unwrap());
        let fl = |i: usize| raw[i * 33 + 32];
        for i in 0..n {
            let snap = fl(i) & 1 != 0;
            if snap && (i == 0 || fl(i - 1) & 1 == 0) {
                bids.clear();
                asks.clear();
                hb.clear();
                ha.clear();
            }
            let key = px(i).to_bits();
            let q = qt(i);
            if fl(i) & 2 != 0 {
                if q == 0.0 {
                    bids.remove(&key);
                } else {
                    if !bids.contains_key(&key) {
                        hb.push(key);
                    }
                    bids.insert(key, q);
                }
            } else if q == 0.0 {
                asks.remove(&key);
            } else {
                if !asks.contains_key(&key) {
                    ha.push(Reverse(key));
                }
                asks.insert(key, q);
            }
            let last = i == n - 1;
            if !last {
                let nsnap = fl(i + 1) & 1 != 0;
                if snap == nsnap && fu(i) == fu(i + 1) && et(i) == et(i + 1) {
                    continue;
                }
                if snap && nsnap {
                    continue;
                }
            }
            while let Some(&k) = hb.peek() {
                if bids.contains_key(&k) {
                    break;
                }
                hb.pop();
            }
            while let Some(&Reverse(k)) = ha.peek() {
                if asks.contains_key(&k) {
                    break;
                }
                ha.pop();
            }
            if let (Some(&kb), Some(&Reverse(ka))) = (hb.peek(), ha.peek()) {
                let (bb, aa) = (f64::from_bits(kb), f64::from_bits(ka));
                if bb >= aa {
                    leg.crossed += 1;
                    continue;
                }
                let mut te = et(i);
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
    t_q: i64,
    t_end: i64,
}

fn episodes(tu: &[i64], x: &[f64], s0: i64, lev: &[f64], theta: f64) -> Vec<Ep> {
    let mut out = vec![];
    let mut calm: Option<i64> = None;
    let mut prev: Option<f64> = None;
    let mut open: Option<Ep> = None;
    let th2 = theta / 2.0;
    for i in 0..tu.len() {
        let js = tu[i] / 1000 - s0;
        let lv = if js >= 0 && (js as usize) < lev.len() { lev[js as usize] } else { f64::NAN };
        let di = 1e4 * (x[i] - lv);
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
                open = Some(Ep { theta, t0: t, dir: if di > 0.0 { 1 } else { -1 }, d0: ad, peak: ad, t_peak: t, t_half: -1, t_q: -1, t_end: -1 });
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
    let mut o = String::from("theta,t0_ms,dir,d0_bps,peak_bps,t_peak_ms,mae_mid_bps,t_half_ms,t_quarter_ms,t_conv_ms,censored\n");
    for th in THETAS {
        for e in episodes(&tu, &x, t0, &lev, th) {
            let r = |v: i64| if v >= 0 { v - e.t0 } else { -1 };
            o += &format!(
                "{:?},{},{},{:.4},{:.4},{},{:.4},{},{},{},{}\n",
                e.theta, e.t0, e.dir, e.d0, e.peak, e.t_peak - e.t0, e.peak - e.d0, r(e.t_half), r(e.t_q), r(e.t_end), if e.t_end >= 0 { 0 } else { 1 }
            );
        }
    }
    std::fs::File::create(&a[1]).unwrap().write_all(o.as_bytes()).unwrap();
    eprintln!("готово {:.2}s", t.elapsed().as_secs_f64());
}
