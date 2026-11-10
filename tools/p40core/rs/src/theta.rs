//! TK-526 (П-41 §2–3, В-128): переменный порог входа θ_t = max(Φ_t/(1−q), S_t) — вектор рядом с рядом `ds`, считанный только по прошлому:
//! S_t берётся по событиям с `tu[j] ≤ t_i − 1 с` (раз в секунду, как `level_grid`), событие `i` и будущее в θ_t[i] не входят.
//! Φ_t = max(Φ, K_t), K_t = C_fee + (spr_A + spr_B)/2 (bps, L1 на t). Мусор (П-41 §3): спред любой ноги ≥ X_s — тик не входит в окно и θ_t = NaN (эпизод не открывается).
//! NaN = «нормы нет»: нет истории окна (`warm`), пустое окно, мусорный тик — сравнения с NaN ложны, эпизод не открывается и «спокойствие» не копится.
//! Мусор по возрасту (П-41 §3 п. 2–3, пороги `smax`/`u` выводит G0 до чтения net, здесь — параметры, умолчание ∞ = флага нет): нога стоит дольше S_max, либо дольше u при сдвиге mid другой ноги ≥ 1 bps с её последней смены.
//! Без `P40_RULE` ядро считает постоянный θ как прежде (байт в байт).
use crate::Leg;
use std::collections::VecDeque;

/// Масштаб «нормального» разрыва S_t.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Stat {
    /// A0: только пол Φ_t/(1−q)
    Floor,
    /// A2: квантиль |d| окна, nearest-rank: k-й (с 0) по порядку, k = ⌈p·n⌉ − 1
    Quantile(f64),
    /// A3: med(d) + k·1,4826·MAD(d); MAD — k-й (с 0, k = n/2) по порядку |d − med|
    MadK(f64),
    /// A4: EWMA-квантиль |d| (стохастическая аппроксимация, O(1) на событие): q += α·sc·(p − 1{x ≤ q}), sc — EWMA |x − q|, α = 1 − 2^(−Δt/hl), Δt ≥ 1 мс
    Ewma(f64),
    /// A5: A3 по режиму — то же окно, события разнесены по терцилям 5-минутной подвижности пары (`vol5`), S_t — по терцилю текущего момента; границы терцилей — по окну до t − 1 с, терциль события фиксируется при входе в окно
    MadReg(f64),
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Rule {
    pub phi: f64,
    pub q: f64,
    pub c_fee: f64,
    pub stat: Stat,
    pub w_ms: i64,
    /// S_t определён, когда история ряда покрывает не меньше `warm_ms` (умолчание = окно)
    pub warm_ms: i64,
    /// порог мусора по спреду ноги, bps (умолчание ∞ = флага нет)
    pub xs: f64,
    /// S_max: возраст лучших цен ноги, мс (умолчание i64::MAX = флага нет)
    pub smax_ms: i64,
    /// u: возраст ноги при сдвиге mid другой ноги ≥ 1 bps, мс (умолчание i64::MAX = флага нет)
    pub u_ms: i64,
    /// полураспад A4, мс
    pub hl_ms: i64,
    /// начало истории для `warm` (мс UTC): прогрев считается от него, а не от первого события куска — окно S_t питается историей сквозь границы кусков (`orch.py --pre-days`, `P40_WARM_FROM_MS`); `i64::MIN` = первое событие ряда
    pub anchor_ms: i64,
}

impl Rule {
    /// `P40_RULE=a0|a2|a3|a4|a5,phi=20,w=21600,p=0.95,k=3,hl=21600,fee=8.82,xs=50,smax=120,u=30,warm=21600` (w, hl, warm, smax, u — секунды; q — отдельно `P40_Q`; warm по умолчанию = w, у a4 = hl).
    pub fn parse(s: &str, q: f64) -> Rule {
        let mut it = s.split(',').map(str::trim);
        let kind = it.next().unwrap();
        let (mut phi, mut w, mut p, mut k, mut fee, mut xs, mut warm) = (f64::NAN, 21600.0, f64::NAN, f64::NAN, 8.82, f64::INFINITY, f64::NAN);
        let (mut hl, mut smax, mut u) = (f64::NAN, f64::INFINITY, f64::INFINITY);
        for kv in it {
            let (key, v) = kv.split_once('=').unwrap_or_else(|| panic!("P40_RULE: ожидалось ключ=значение, получено {kv}"));
            let v: f64 = v.parse().unwrap_or_else(|_| panic!("P40_RULE: число {kv}"));
            match key {
                "phi" => phi = v,
                "w" => w = v,
                "p" => p = v,
                "k" => k = v,
                "fee" => fee = v,
                "xs" => xs = v,
                "warm" => warm = v,
                "hl" => hl = v,
                "smax" => smax = v,
                "u" => u = v,
                o => panic!("P40_RULE: неизвестный ключ {o}"),
            }
        }
        assert!(phi.is_finite() && (0.0..1.0).contains(&q), "P40_RULE: нужен phi, q в [0;1)");
        let stat = match kind {
            "a0" => Stat::Floor,
            "a2" => {
                assert!(p > 0.0 && p < 1.0, "P40_RULE a2: нужен p в (0;1)");
                Stat::Quantile(p)
            }
            "a3" => {
                assert!(k.is_finite() && k > 0.0, "P40_RULE a3: нужен k > 0");
                Stat::MadK(k)
            }
            "a5" => {
                assert!(k.is_finite() && k > 0.0, "P40_RULE a5: нужен k > 0");
                Stat::MadReg(k)
            }
            "a4" => {
                assert!(p > 0.0 && p < 1.0 && hl > 0.0, "P40_RULE a4: нужны p в (0;1) и hl > 0");
                Stat::Ewma(p)
            }
            o => panic!("P40_RULE: ожидалось a0|a2|a3|a4|a5, получено {o}"),
        };
        let ms = |x: f64| if x.is_finite() { (x * 1000.0) as i64 } else { i64::MAX };
        let (w_ms, hl_ms) = (ms(w), if hl.is_nan() { 0 } else { ms(hl) });
        let warm_ms = if !warm.is_nan() { ms(warm) } else if matches!(stat, Stat::Ewma(_)) { hl_ms } else { w_ms };
        Rule { phi, q, c_fee: fee, stat, w_ms, warm_ms, xs, smax_ms: ms(smax), u_ms: ms(u), hl_ms, anchor_ms: i64::MIN }
    }
    /// момент, от которого считается прогрев `warm`
    fn start(&self, tu: &[i64]) -> i64 {
        if self.anchor_ms == i64::MIN { tu[0] } else { self.anchor_ms }
    }
}

/// Счётчики по рангам значений (Fenwick): добавление/удаление и k-я порядковая статистика за O(log n).
struct Fen {
    t: Vec<i32>,
    top: usize,
}

impl Fen {
    fn new(n: usize) -> Fen {
        Fen { t: vec![0; n + 1], top: if n == 0 { 0 } else { 1usize << (usize::BITS - 1 - n.leading_zeros()) } }
    }
    fn add(&mut self, r: usize, d: i32) {
        let mut i = r + 1;
        while i < self.t.len() {
            self.t[i] += d;
            i += i & i.wrapping_neg();
        }
    }
    /// число элементов окна с рангом < `k`
    fn prefix(&self, mut k: usize) -> i32 {
        let mut s = 0;
        while k > 0 {
            s += self.t[k];
            k &= k - 1;
        }
        s
    }
    /// ранг k-го (с 0) по порядку элемента окна
    fn kth(&self, mut k: i32) -> usize {
        let (mut pos, mut pw) = (0usize, self.top);
        while pw > 0 {
            if pos + pw < self.t.len() && self.t[pos + pw] <= k {
                pos += pw;
                k -= self.t[pos];
            }
            pw >>= 1;
        }
        pos
    }
}

/// Окно значений (A2/A3): отсортированные уникальные значения `vals` + счётчики по рангам.
struct Win<'a> {
    vals: &'a [f64],
    fen: Fen,
    n: i32,
}

impl Win<'_> {
    fn nth(&self, k: i32) -> f64 {
        self.vals[self.fen.kth(k)]
    }
    fn median(&self) -> f64 {
        let m = self.n;
        if m % 2 == 1 { self.nth(m / 2) } else { (self.nth(m / 2 - 1) + self.nth(m / 2)) / 2.0 }
    }
    fn quantile(&self, p: f64) -> f64 {
        self.nth((((p * self.n as f64).ceil() as i32) - 1).clamp(0, self.n - 1))
    }
    /// число элементов окна в отрезке [lo; hi]
    fn count_in(&self, lo: f64, hi: f64) -> i32 {
        self.fen.prefix(self.vals.partition_point(|&v| v <= hi)) - self.fen.prefix(self.vals.partition_point(|&v| v < lo))
    }
    /// MAD относительно `med`: наименьшее r, при котором в [med−r; med+r] не меньше n/2 + 1 элементов (бисекция по значению, 60 шагов)
    fn mad(&self, med: f64) -> f64 {
        let need = self.n / 2 + 1;
        let (mut lo, mut hi) = (0.0f64, (self.nth(self.n - 1) - med).max(med - self.nth(0)).max(0.0));
        for _ in 0..60 {
            let mid = (lo + hi) / 2.0;
            if self.count_in(med - mid, med + mid) >= need { hi = mid } else { lo = mid }
        }
        hi
    }
}

fn spr_bps(b: f64, a: f64) -> f64 {
    2e4 * (a - b) / (a + b)
}

/// Пространство рангов: отсортированные уникальные конечные значения `f(i)` и ранг каждого `i` (u32::MAX — значения нет).
fn rank_space(n: usize, f: impl Fn(usize) -> f64) -> (Vec<f64>, Vec<u32>) {
    let mut vals: Vec<f64> = (0..n).map(&f).filter(|x| x.is_finite()).collect();
    vals.sort_unstable_by(|a, b| a.partial_cmp(b).unwrap());
    vals.dedup();
    let mut rk = vec![u32::MAX; n];
    let mut prev = (f64::NAN, u32::MAX);
    for (i, r) in rk.iter_mut().enumerate() {
        let x = f(i);
        if x.is_finite() {
            if x != prev.0 {
                prev = (x, vals.partition_point(|&w| w < x) as u32);
            }
            *r = prev.1;
        }
    }
    (vals, rk)
}

/// Размах mid ноги за (t − 300 с; t] (монотонные очереди), bps от текущего mid.
struct Rng {
    mx: VecDeque<(i64, f64)>,
    mn: VecDeque<(i64, f64)>,
    k: usize,
    last: f64,
}

impl Rng {
    fn new() -> Rng {
        Rng { mx: VecDeque::new(), mn: VecDeque::new(), k: 0, last: f64::NAN }
    }
    fn at(&mut self, l: &Leg, t: i64) -> f64 {
        while self.k < l.t.len() && l.t[self.k] <= t {
            let (te, m) = (l.t[self.k], (l.b[self.k] + l.a[self.k]) / 2.0);
            while self.mx.back().is_some_and(|x| x.1 <= m) {
                self.mx.pop_back();
            }
            self.mx.push_back((te, m));
            while self.mn.back().is_some_and(|x| x.1 >= m) {
                self.mn.pop_back();
            }
            self.mn.push_back((te, m));
            (self.last, self.k) = (m, self.k + 1);
        }
        while self.mx.front().is_some_and(|x| x.0 <= t - 300_000) {
            self.mx.pop_front();
        }
        while self.mn.front().is_some_and(|x| x.0 <= t - 300_000) {
            self.mn.pop_front();
        }
        match (self.mx.front(), self.mn.front()) {
            _ if self.k == 0 => f64::NAN,
            (Some(h), Some(l)) => 1e4 * (h.1 - l.1) / self.last,
            _ => 0.0, // нога не обновлялась 5 мин — подвижности нет
        }
    }
}

/// 5-минутная подвижность пары на момент каждого события: большая из двух ног («подвижная нога»); NaN, пока нет событий обеих.
fn vol5(tu: &[i64], la: &Leg, lb: &Leg) -> Vec<f64> {
    let (mut ra, mut rb) = (Rng::new(), Rng::new());
    tu.iter().map(|&t| ra.at(la, t).max(rb.at(lb, t))).collect()
}

/// A5: A3 по терцилю подвижности (см. `Stat::MadReg`). Три окна по рангам d (по одному на терциль) + окно рангов подвижности для границ терцилей.
#[allow(clippy::too_many_arguments)]
fn a5_theta(tu: &[i64], ds: &[f64], la: &Leg, lb: &Leg, bad: &[bool], floor: &[f64], k: f64, rule: &Rule) -> Vec<f64> {
    let n = tu.len();
    let v5 = vol5(tu, la, lb);
    let ok = |i: usize| !bad[i] && ds[i].is_finite() && v5[i].is_finite();
    let (vals_d, rk_d) = rank_space(n, |i| if ok(i) { ds[i] } else { f64::NAN });
    let (vals_v, rk_v) = rank_space(n, |i| if ok(i) { v5[i] } else { f64::NAN });
    let mut wins = [0; 3].map(|_| Win { vals: &vals_d, fen: Fen::new(vals_d.len()), n: 0 });
    let mut wv = Win { vals: &vals_v, fen: Fen::new(vals_v.len()), n: 0 };
    let mut reg = vec![u8::MAX; n];
    let mut th = vec![f64::NAN; n];
    let (mut pin, mut pout) = (0usize, 0usize);
    let (mut cur_s, mut cur, mut last_v5) = (i64::MIN, f64::NAN, f64::NAN);
    for i in 0..n {
        if bad[i] || floor[i].is_nan() {
            continue;
        }
        let s = tu[i].div_euclid(1000);
        if s != cur_s {
            cur_s = s;
            let hi = s * 1000 - 1000;
            let lo = hi - rule.w_ms;
            let expire = |pout: &mut usize, pin: usize, wins: &mut [Win; 3], wv: &mut Win, reg: &[u8]| {
                while *pout < pin && tu[*pout] <= lo {
                    let j = *pout;
                    if reg[j] != u8::MAX {
                        wins[reg[j] as usize].fen.add(rk_d[j] as usize, -1);
                        wins[reg[j] as usize].n -= 1;
                        wv.fen.add(rk_v[j] as usize, -1);
                        wv.n -= 1;
                    }
                    *pout += 1;
                }
            };
            expire(&mut pout, pin, &mut wins, &mut wv, &reg);
            // границы терцилей — по окну до этой секунды (прошлое)
            let cut = (wv.n >= 3).then(|| (wv.quantile(1.0 / 3.0), wv.quantile(2.0 / 3.0)));
            let rg = |x: f64| cut.map_or(0u8, |(c1, c2)| if x <= c1 { 0 } else if x <= c2 { 1 } else { 2 });
            while pin < n && tu[pin] <= hi {
                if v5[pin].is_finite() {
                    last_v5 = v5[pin];
                }
                if ok(pin) {
                    let r = rg(v5[pin]);
                    reg[pin] = r;
                    wins[r as usize].fen.add(rk_d[pin] as usize, 1);
                    wins[r as usize].n += 1;
                    wv.fen.add(rk_v[pin] as usize, 1);
                    wv.n += 1;
                }
                pin += 1;
            }
            expire(&mut pout, pin, &mut wins, &mut wv, &reg);
            let w = &wins[rg(last_v5) as usize];
            cur = if last_v5.is_nan() || w.n == 0 || hi - rule.start(tu) < rule.warm_ms {
                f64::NAN
            } else {
                let med = w.median();
                med + k * 1.4826 * w.mad(med)
            };
        }
        if !cur.is_nan() {
            th[i] = floor[i].max(cur);
        }
    }
    th
}

fn mid_at(l: &Leg, t: i64) -> Option<f64> {
    l.bbo(t).map(|(b, a)| (b + a) / 2.0)
}

/// Нога стоит (П-41 §3 п. 2–3): возраст `x_age` её лучших цен > S_max, либо > u при том, что mid другой ноги `y` с момента последней смены этой ноги сдвинулся ≥ 1 bps.
fn stale(x_age: i64, y: &Leg, t: i64, rule: &Rule) -> bool {
    if x_age > rule.smax_ms {
        return true;
    }
    if x_age > rule.u_ms {
        if let (Some(m0), Some(m1)) = (mid_at(y, t - x_age), mid_at(y, t)) {
            return 1e4 * (m1 - m0).abs() / m0 >= 1.0;
        }
    }
    false
}

/// A4: тот же ход по секундам, что у окна (события ≤ t − 1 с), но S_t — EWMA-квантиль `|d|` без хранения окна. Мусор и неопределённый d в оценку не входят.
fn ewma_theta(tu: &[i64], ds: &[f64], bad: &[bool], floor: &[f64], p: f64, rule: &Rule) -> Vec<f64> {
    let n = tu.len();
    let mut th = vec![f64::NAN; n];
    let (mut q, mut sc, mut last, mut init) = (0.0f64, 0.0f64, 0i64, false);
    let (mut pin, mut cur_s, mut cur) = (0usize, i64::MIN, f64::NAN);
    for i in 0..n {
        if bad[i] || floor[i].is_nan() {
            continue;
        }
        let s = tu[i].div_euclid(1000);
        if s != cur_s {
            cur_s = s;
            let hi = s * 1000 - 1000;
            while pin < n && tu[pin] <= hi {
                if !bad[pin] && ds[pin].is_finite() {
                    let x = ds[pin].abs();
                    if init {
                        let a = -(-std::f64::consts::LN_2 * ((tu[pin] - last).max(1) as f64) / rule.hl_ms as f64).exp_m1();
                        sc += a * ((x - q).abs() - sc);
                        q += a * sc * (p - if x <= q { 1.0 } else { 0.0 });
                    } else {
                        (q, sc, init) = (x, x, true);
                    }
                    last = tu[pin];
                }
                pin += 1;
            }
            cur = if !init || hi - rule.start(tu) < rule.warm_ms { f64::NAN } else { q };
        }
        if !cur.is_nan() {
            th[i] = floor[i].max(cur);
        }
    }
    th
}

/// θ_t[i] для моментов `tu` (см. шапку). Длина = `tu.len()`.
pub fn theta_series(tu: &[i64], ds: &[f64], la: &Leg, lb: &Leg, rule: &Rule) -> Vec<f64> {
    let n = tu.len();
    let (mut floor, mut bad) = (vec![f64::NAN; n], vec![false; n]);
    let (mut ka, mut kb) = (0usize, 0usize);
    let aged = rule.smax_ms != i64::MAX || rule.u_ms != i64::MAX;
    let (age_a, age_b) = if aged { (la.age_at(tu), lb.age_at(tu)) } else { (vec![], vec![]) };
    for i in 0..n {
        while ka < la.t.len() && la.t[ka] <= tu[i] {
            ka += 1;
        }
        while kb < lb.t.len() && lb.t[kb] <= tu[i] {
            kb += 1;
        }
        if ka == 0 || kb == 0 {
            continue;
        }
        let (sa, sb) = (spr_bps(la.b[ka - 1], la.a[ka - 1]), spr_bps(lb.b[kb - 1], lb.a[kb - 1]));
        bad[i] = sa >= rule.xs || sb >= rule.xs || (aged && (stale(age_a[i], lb, tu[i], rule) || stale(age_b[i], la, tu[i], rule)));
        floor[i] = rule.phi.max(rule.c_fee + (sa + sb) / 2.0) / (1.0 - rule.q);
    }
    let mut th = vec![f64::NAN; n];
    let signed = match rule.stat {
        Stat::Floor => {
            for i in 0..n {
                if !bad[i] {
                    th[i] = floor[i];
                }
            }
            return th;
        }
        Stat::Quantile(_) => false,
        Stat::MadK(_) => true,
        Stat::MadReg(k) => return a5_theta(tu, ds, la, lb, &bad, &floor, k, rule),
        Stat::Ewma(p) => return ewma_theta(tu, ds, &bad, &floor, p, rule),
    };
    // значение тика в окне; мусор и неопределённый d в окно не входят
    let v = |i: usize| if bad[i] || !ds[i].is_finite() { f64::NAN } else if signed { ds[i] } else { ds[i].abs() };
    let (vals, rk) = rank_space(n, v);
    let mut win = Win { vals: &vals, fen: Fen::new(vals.len()), n: 0 };
    let (mut pin, mut pout) = (0usize, 0usize);
    let (mut cur_s, mut cur) = (i64::MIN, f64::NAN);
    for i in 0..n {
        if bad[i] || floor[i].is_nan() {
            continue;
        }
        let s = tu[i].div_euclid(1000);
        if s != cur_s {
            cur_s = s;
            let hi = s * 1000 - 1000;
            while pin < n && tu[pin] <= hi {
                if rk[pin] != u32::MAX {
                    win.fen.add(rk[pin] as usize, 1);
                    win.n += 1;
                }
                pin += 1;
            }
            let lo = hi - rule.w_ms;
            while pout < pin && tu[pout] <= lo {
                if rk[pout] != u32::MAX {
                    win.fen.add(rk[pout] as usize, -1);
                    win.n -= 1;
                }
                pout += 1;
            }
            cur = if win.n == 0 || hi - rule.start(tu) < rule.warm_ms {
                f64::NAN
            } else {
                match rule.stat {
                    Stat::Quantile(p) => win.quantile(p),
                    Stat::MadK(k) => {
                        let med = win.median();
                        med + k * 1.4826 * win.mad(med)
                    }
                    Stat::Floor | Stat::Ewma(_) | Stat::MadReg(_) => unreachable!(),
                }
            };
        }
        if !cur.is_nan() {
            th[i] = floor[i].max(cur);
        }
    }
    th
}

#[cfg(test)]
mod tests {
    use super::*;

    fn leg(t: &[i64], b: f64, a: f64) -> Leg {
        Leg { t: t.to_vec(), b: vec![b; t.len()], a: vec![a; t.len()], bq: vec![], aq: vec![], crossed: 0, nonmono: 0 }
    }

    /// псевдослучайный ряд d (bps): 1 событие на ~0,3 с, всплески
    fn series(n: usize, seed: u64) -> (Vec<i64>, Vec<f64>) {
        let mut st = seed;
        let mut rnd = move || {
            st ^= st << 13;
            st ^= st >> 7;
            st ^= st << 17;
            st
        };
        let (mut t, mut tu, mut ds) = (0i64, vec![], vec![]);
        for _ in 0..n {
            t += 100 + (rnd() % 500) as i64;
            tu.push(t);
            let r = rnd();
            ds.push(if r % 50 == 0 { f64::NAN } else { ((r >> 8) % 4000) as f64 / 100.0 - 20.0 + if r % 97 == 0 { 40.0 } else { 0.0 } });
        }
        (tu, ds)
    }

    /// эталон: статистика окна по отсортированному Vec на каждом событии (O(n·W log W))
    fn ref_stat(tu: &[i64], ds: &[f64], i: usize, rule: &Rule) -> f64 {
        let hi = tu[i].div_euclid(1000) * 1000 - 1000;
        let lo = hi - rule.w_ms;
        if hi - tu[0] < rule.warm_ms {
            return f64::NAN;
        }
        let signed = matches!(rule.stat, Stat::MadK(_));
        let mut w: Vec<f64> = (0..tu.len()).filter(|&j| tu[j] <= hi && tu[j] > lo && ds[j].is_finite()).map(|j| if signed { ds[j] } else { ds[j].abs() }).collect();
        if w.is_empty() {
            return f64::NAN;
        }
        w.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let m = w.len();
        match rule.stat {
            Stat::Quantile(p) => w[(((p * m as f64).ceil() as usize).max(1) - 1).min(m - 1)],
            Stat::MadK(k) => {
                let med = if m % 2 == 1 { w[m / 2] } else { (w[m / 2 - 1] + w[m / 2]) / 2.0 };
                let mut dev: Vec<f64> = w.iter().map(|x| (x - med).abs()).collect();
                dev.sort_by(|a, b| a.partial_cmp(b).unwrap());
                med + k * 1.4826 * dev[m / 2]
            }
            Stat::Floor | Stat::Ewma(_) | Stat::MadReg(_) => unreachable!(),
        }
    }

    /// Судья 11.11 п. 2: кусок, читающий предысторию ≥ W до своего начала, даёт θ_t[i] как непрерывный прогон (прогрев — от общего начала данных `anchor_ms`, не от первого события куска)
    #[test]
    fn chunk_with_prehistory_equals_continuous_run() {
        let (tu, ds) = series(9000, 0x9E3779B97F4A7C15);
        let lg = leg(&tu, 100.0, 100.01);
        let c = tu.partition_point(|&t| t < tu[tu.len() - 1] / 2);   // начало куска — середина ряда
        let j0 = tu.partition_point(|&t| t < tu[c] - 700_000);        // предыстория 700 с ≥ W = 600 с
        let (tc, dc) = (&tu[j0..], &ds[j0..]);
        let lc = leg(tc, 100.0, 100.01);
        for (stat, exact) in [("a2,phi=1,w=600,p=0.95,warm=600", true), ("a3,phi=1,w=600,k=3,warm=600", true), ("a5,phi=1,w=600,k=3,warm=600", true), ("a4,phi=1,hl=60,p=0.9,warm=600", false)] {
            let mut rule = Rule::parse(stat, 0.5);
            rule.anchor_ms = tu[0];
            let (full, cut) = (theta_series(&tu, &ds, &lg, &lg, &rule), theta_series(tc, dc, &lc, &lc, &rule));
            let (mut def, mut tail_nan) = (0, 0);
            for i in c..tu.len() {
                let (a, b) = (full[i], cut[i - j0]);
                if a.is_nan() || b.is_nan() {
                    assert!(a.is_nan() && b.is_nan(), "{stat} i={i}: {a} против {b}");
                    tail_nan += 1;
                } else {
                    def += 1;
                    assert!(if exact { a.to_bits() == b.to_bits() } else { (a - b).abs() <= 0.02 * a.abs().max(1.0) }, "{stat} i={i}: непрерывный {a} кусок {b}");
                }
            }
            assert!(def > 1000, "{stat}: определённых моментов {def} (NaN {tail_nan})");
        }
    }

    #[test]
    fn window_matches_sorted_vec_reference() {
        let (tu, ds) = series(6000, 0x9E3779B97F4A7C15);
        let lg = leg(&tu, 100.0, 100.01);
        for stat in ["a2,phi=1,w=600,p=0.95,warm=0", "a2,phi=1,w=120,p=0.5,warm=300", "a3,phi=1,w=600,k=3,warm=0", "a3,phi=1,w=60,k=5,warm=0"] {
            let rule = Rule::parse(stat, 0.5);
            let th = theta_series(&tu, &ds, &lg, &lg, &rule);
            let fl = rule.phi.max(rule.c_fee + spr_bps(100.0, 100.01)) / 0.5;
            let mut def = 0;
            for i in 0..tu.len() {
                let s = ref_stat(&tu, &ds, i, &rule);
                if s.is_nan() {
                    assert!(th[i].is_nan(), "{stat} i={i}");
                } else {
                    def += 1;
                    let want = fl.max(s);
                    assert!((th[i] - want).abs() < 1e-9, "{stat} i={i}: {} против {want}", th[i]);
                }
            }
            assert!(def > 3000, "{stat}: определённых θ_t {def}");
        }
    }

    #[test]
    fn theta_uses_only_past() {
        let (tu, ds) = series(4000, 12345);
        let lg = leg(&tu, 100.0, 100.01);
        let k = 2500;
        for stat in ["a2,phi=1,w=300,p=0.9,warm=0", "a3,phi=1,w=300,k=3,warm=0", "a4,phi=1,hl=120,p=0.9,warm=0"] {
            let rule = Rule::parse(stat, 0.5);
            let a = theta_series(&tu, &ds, &lg, &lg, &rule);
            // будущее со `k`-го события — другое (и ноги тоже не нужны: спред постоянный)
            let mut ds2 = ds.clone();
            for x in ds2.iter_mut().skip(k) {
                *x = if x.is_finite() { *x * 3.0 + 17.0 } else { 5.0 };
            }
            let b = theta_series(&tu, &ds2, &lg, &lg, &rule);
            let mut same = 0;
            for i in 0..tu.len() {
                if tu[i] < tu[k] + 1000 {
                    assert_eq!(a[i].to_bits(), b[i].to_bits(), "{stat}: θ_t[{i}] зависит от будущего (tu {} < {})", tu[i], tu[k] + 1000);
                    same += 1;
                }
            }
            assert!(same > 2000);
            assert!(a.iter().zip(&b).skip(k + 600).any(|(x, y)| x != y), "{stat}: будущее должно менять поздние θ_t");
        }
    }

    #[test]
    fn floor_and_garbage_flag() {
        let tu = vec![0i64, 1000, 2000];
        let ds = vec![1.0; 3];
        // спред ноги A 2 bps, B 4 bps: K = 8,82 + 3 = 11,82 < Φ = 20 -> пол 20/(1−½) = 40; при Φ = 5: 11,82/0,5 = 23,64
        let (la, lb) = (leg(&tu, 100.0, 100.02), leg(&tu, 100.0, 100.04));
        let t = theta_series(&tu, &ds, &la, &lb, &Rule::parse("a0,phi=20", 0.5));
        assert!(t.iter().all(|&x| (x - 40.0).abs() < 1e-3), "{t:?}");
        let t = theta_series(&tu, &ds, &la, &lb, &Rule::parse("a0,phi=5", 0.5));
        assert!(t.iter().all(|&x| (x - 2.0 * (8.82 + 3.0)).abs() < 1e-3), "{t:?}");
        let t = theta_series(&tu, &ds, &la, &lb, &Rule::parse("a0,phi=20", 0.0));
        assert!(t.iter().all(|&x| (x - 20.0).abs() < 1e-3));
        // мусор: спред ноги B ≥ 3 bps -> θ_t = NaN (A0 и окно)
        let t = theta_series(&tu, &ds, &la, &lb, &Rule::parse("a0,phi=20,xs=3", 0.5));
        assert!(t.iter().all(|x| x.is_nan()));
        let t = theta_series(&tu, &ds, &la, &lb, &Rule::parse("a2,phi=20,p=0.9,xs=3,warm=0", 0.5));
        assert!(t.iter().all(|x| x.is_nan()));
    }

    #[test]
    fn garbage_tick_does_not_move_the_norm() {
        // тик с огромным d и мусорным спредом не входит в окно: норма та же, что без него
        let tu: Vec<i64> = (0..200).map(|i| i * 1000).collect();
        let mut ds = vec![2.0; 200];
        ds[100] = 5000.0;
        let (mut la, lb) = (leg(&tu, 100.0, 100.01), leg(&tu, 100.0, 100.01));
        la.a[100] = 100.9;
        let rule = Rule::parse("a2,phi=1,w=1000,p=0.999,xs=50,warm=0", 0.5);
        let t = theta_series(&tu, &ds, &la, &lb, &rule);
        assert!(t[100].is_nan(), "сам мусорный тик — нормы нет");
        assert!((t[150] - 2.0 * (8.82 + 1.0)).abs() < 1e-3 || t[150] >= 2.0, "норма после мусора не раздута: {}", t[150]);
        assert!(t[150] < 100.0, "мусор не раздул норму: {}", t[150]);
        // без флага тот же тик раздувает квантиль
        let t2 = theta_series(&tu, &ds, &la, &lb, &Rule::parse("a2,phi=1,w=1000,p=0.999,warm=0", 0.5));
        assert!(t2[150] > 1000.0, "{}", t2[150]);
    }

    /// A5: на постоянных ногах подвижность 0 во всех тиках — один терциль, результат = A3 побитово; при чередовании спокойных и подвижных блоков S_t подвижного режима на порядок выше спокойного и общей A3.
    #[test]
    fn regime_split_a5() {
        let (tu, ds) = series(4000, 777);
        let lg = leg(&tu, 100.0, 100.01);
        let a3 = theta_series(&tu, &ds, &lg, &lg, &Rule::parse("a3,phi=1,w=300,k=3,warm=0", 0.5));
        let a5 = theta_series(&tu, &ds, &lg, &lg, &Rule::parse("a5,phi=1,w=300,k=3,warm=0", 0.5));
        assert!(a3.iter().zip(&a5).all(|(x, y)| x.to_bits() == y.to_bits()), "без подвижности A5 ≡ A3");
        // цикл 2400 с: первые 1000 с mid ноги B прыгает на ±5 bps, d ∈ ±63; остальные 1400 с ноги стоят, d ∈ ±1,2 (подвижность за 5 мин падает до 0 через 300 с после последнего прыжка)
        let n = 7200usize;
        let tu: Vec<i64> = (0..n).map(|i| i as i64 * 1000).collect();
        let (mut ds, la, mut lb) = (vec![0.0; n], leg(&tu, 100.0, 100.0001), leg(&tu, 100.0, 100.0001));
        for i in 0..n {
            let busy = i % 2400 < 1000;
            let sgn = if i % 2 == 0 { 1.0 } else { -1.0 };
            ds[i] = (((i * 7919) % 1000) as f64 / 500.0 - 1.0) * if busy { 63.0 } else { 1.2 }; // равномерно на [−масштаб; масштаб), медиана ≈ 0
            if busy {
                lb.b[i] += sgn * 0.05;
                lb.a[i] += sgn * 0.05;
            }
        }
        let rule = |k: &str| Rule::parse(&format!("{k},phi=0.001,fee=0,w=3000,k=3,warm=1200"), 0.0);
        let (t3, t5) = (theta_series(&tu, &ds, &la, &lb, &rule("a3")), theta_series(&tu, &ds, &la, &lb, &rule("a5")));
        // спокойное окно 4500 (нули подвижности 3700..4800) и подвижное 5000 (прыжки с 4800): в окне W 40 % подвижных, поэтому MAD общей A3 — по спокойному кластеру и мала везде
        let (calm, busy) = (4500usize, 5000usize);
        assert!(t5[calm].is_finite() && t5[busy].is_finite() && t3[busy].is_finite());
        assert!(t5[busy] > 20.0 * t5[calm], "режимы не разделены: подвижный {} спокойный {}", t5[busy], t5[calm]);
        assert!(t5[busy] > 10.0 * t3[busy], "подвижный: A5 {} против A3 {}", t5[busy], t3[busy]);
    }

    /// A4: на стационарном ряду |d| ~ U[0;20) оценка 0,9-квантиля сходится к 18; мусорный тик с огромным d её не двигает (флаг xs), а без флага двигает.
    #[test]
    fn ewma_quantile_converges_and_skips_garbage() {
        let n = 60_000usize;
        let (mut st, mut tu, mut ds) = (0x2545F4914F6CDD1Du64, vec![], vec![]);
        for i in 0..n {
            st ^= st << 13;
            st ^= st >> 7;
            st ^= st << 17;
            tu.push(i as i64 * 100); // 10 событий/с
            ds.push(((st >> 11) % 2000) as f64 / 100.0 * if st & 1 == 0 { 1.0 } else { -1.0 });
        }
        let lg = leg(&tu, 100.0, 100.001);
        let t = theta_series(&tu, &ds, &lg, &lg, &Rule::parse("a4,phi=1,hl=600,p=0.9,warm=600", 0.5));
        let tail: Vec<f64> = t[n - 20_000..].iter().copied().filter(|x| x.is_finite()).collect();
        let mean = tail.iter().sum::<f64>() / tail.len() as f64;
        assert!((mean - 18.0).abs() < 1.0, "среднее оценки {mean} против 18");
        assert!(t[..5000].iter().all(|x| x.is_nan()), "до warm θ_t = NaN");
        let mut ds2 = ds.clone();
        let mut lb = leg(&tu, 100.0, 100.001);
        for i in (10_000..n).step_by(50) {
            ds2[i] = 9000.0;
            lb.a[i] = 100.9; // спред ≥ xs
        }
        let flagged = theta_series(&tu, &ds2, &lg, &lb, &Rule::parse("a4,phi=1,hl=600,p=0.9,warm=600,xs=50", 0.5));
        let tail: Vec<f64> = flagged[n - 20_000..].iter().copied().filter(|x| x.is_finite()).collect();
        let mean2 = tail.iter().sum::<f64>() / tail.len() as f64;
        assert!((mean2 - 18.0).abs() < 1.0, "мусор раздул норму: {mean2}");
    }

    /// Флаги возраста (П-41 §3 п. 2–3): нога A стоит, mid ноги B уходит на 1 bps за секунду.
    #[test]
    fn stale_leg_flags() {
        let tu: Vec<i64> = (0..200).map(|i| i * 1000).collect();
        let la = leg(&tu, 100.0, 100.01);
        let mut lb = leg(&tu, 100.0, 100.01);
        for i in 0..200 {
            lb.b[i] = 100.0 + 0.01 * i as f64;
            lb.a[i] = lb.b[i] + 0.01;
        }
        let t = theta_series(&tu, &vec![1.0; 200], &la, &lb, &Rule::parse("a0,phi=1,smax=50", 0.5));
        assert!(t[50].is_finite() && t[51].is_nan() && t[199].is_nan(), "S_max: возраст A > 50 с — с i = 51");
        let t = theta_series(&tu, &vec![1.0; 200], &la, &lb, &Rule::parse("a0,phi=1,u=10", 0.5));
        assert!(t[10].is_finite() && t[11].is_nan(), "u: возраст A > 10 с при сдвиге B");
        // обе ноги стоят: «одиночной котировки» нет (сдвига другой ноги нет), S_max не задан
        let t = theta_series(&tu, &vec![1.0; 200], &la, &la, &Rule::parse("a0,phi=1,u=10", 0.5));
        assert!(t.iter().all(|x| x.is_finite()));
        let t = theta_series(&tu, &vec![1.0; 200], &la, &la, &Rule::parse("a0,phi=1,smax=10", 0.5));
        assert!(t[10].is_finite() && t[11].is_nan());
    }

    /// Замер В-170 (`cargo test --release bench_window -- --ignored --nocapture`, на srv): сутки событий (~1,3 млн, ≈15 событий/с), статистика раз в секунду.
    /// Варианты: Fenwick по рангам (`theta_series`, A2 и A3) против отсортированного `Vec` со сдвигом (`insert`/`remove`, как прежний `window_levels` до TK-522); окно 1 ч и 24 ч.
    #[test]
    #[ignore]
    fn bench_window() {
        let n = 1_300_000usize;
        let (mut st, mut t, mut tu, mut ds) = (88172645463325252u64, 0i64, Vec::with_capacity(n), Vec::with_capacity(n));
        for _ in 0..n {
            st ^= st << 13;
            st ^= st >> 7;
            st ^= st << 17;
            t += 1 + (st % 130) as i64;
            tu.push(t);
            ds.push(((st >> 20) % 6000) as f64 / 100.0 - 30.0);
        }
        let lg = leg(&tu, 100.0, 100.01);
        for w in [3600.0f64, 86400.0] {
            for (name, kind) in [("a2", "a2,phi=1,p=0.95,warm=0"), ("a3", "a3,phi=1,k=3,warm=0")] {
                let rule = Rule::parse(&format!("{kind},w={w}"), 0.5);
                let t0 = std::time::Instant::now();
                let th = theta_series(&tu, &ds, &lg, &lg, &rule);
                println!("Fenwick {name} W={}ч: {:.2} с (θ определён на {})", w / 3600.0, t0.elapsed().as_secs_f64(), th.iter().filter(|x| x.is_finite()).count());
            }
            if w > 3600.0 {
                continue; // отсортированный Vec на 24 ч — ~10 МБ сдвига на событие, не ждём
            }
            let t0 = std::time::Instant::now();
            let (mut win, mut pin, mut pout, mut acc) = (Vec::<f64>::new(), 0usize, 0usize, 0.0f64);
            let mut s_prev = i64::MIN;
            for i in 0..n {
                let s = tu[i].div_euclid(1000);
                if s == s_prev {
                    continue;
                }
                s_prev = s;
                let hi = s * 1000 - 1000;
                while pin < n && tu[pin] <= hi {
                    let v = ds[pin].abs();
                    let k = win.partition_point(|&x| x < v);
                    win.insert(k, v);
                    pin += 1;
                }
                while pout < pin && tu[pout] <= hi - (w * 1000.0) as i64 {
                    let v = ds[pout].abs();
                    let k = win.partition_point(|&x| x < v);
                    win.remove(k);
                    pout += 1;
                }
                if !win.is_empty() {
                    acc += win[(((0.95 * win.len() as f64).ceil() as usize).max(1) - 1).min(win.len() - 1)];
                }
            }
            println!("sorted Vec a2 W=1ч: {:.2} с (контрольная сумма {acc:.1})", t0.elapsed().as_secs_f64());
        }
    }
}
