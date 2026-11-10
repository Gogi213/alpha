//! TK-526 (П-41 §2–3, В-128): переменный порог входа θ_t = max(Φ_t/(1−q), S_t) — вектор рядом с рядом `ds`, считанный только по прошлому:
//! S_t берётся по событиям с `tu[j] ≤ t_i − 1 с` (раз в секунду, как `level_grid`), событие `i` и будущее в θ_t[i] не входят.
//! Φ_t = max(Φ, K_t), K_t = C_fee + (spr_A + spr_B)/2 (bps, L1 на t). Мусор (П-41 §3): спред любой ноги ≥ X_s — тик не входит в окно и θ_t = NaN (эпизод не открывается).
//! NaN = «нормы нет»: нет истории окна (`warm`), пустое окно, мусорный тик — сравнения с NaN ложны, эпизод не открывается и «спокойствие» не копится.
//! Без `P40_RULE` ядро считает постоянный θ как прежде (байт в байт).
use crate::Leg;

/// Масштаб «нормального» разрыва S_t.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Stat {
    /// A0: только пол Φ_t/(1−q)
    Floor,
    /// A2: квантиль |d| окна, nearest-rank: k-й (с 0) по порядку, k = ⌈p·n⌉ − 1
    Quantile(f64),
    /// A3: med(d) + k·1,4826·MAD(d); MAD — k-й (с 0, k = n/2) по порядку |d − med|
    MadK(f64),
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
}

impl Rule {
    /// `P40_RULE=a0|a2|a3,phi=20,w=21600,p=0.95,k=3,fee=8.82,xs=50,warm=21600` (w, warm — секунды; q — отдельно `P40_Q`).
    pub fn parse(s: &str, q: f64) -> Rule {
        let mut it = s.split(',').map(str::trim);
        let kind = it.next().unwrap();
        let (mut phi, mut w, mut p, mut k, mut fee, mut xs, mut warm) = (f64::NAN, 21600.0, f64::NAN, f64::NAN, 8.82, f64::INFINITY, f64::NAN);
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
            o => panic!("P40_RULE: ожидалось a0|a2|a3, получено {o}"),
        };
        let w_ms = (w * 1000.0) as i64;
        Rule { phi, q, c_fee: fee, stat, w_ms, warm_ms: if warm.is_nan() { w_ms } else { (warm * 1000.0) as i64 }, xs }
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

/// θ_t[i] для моментов `tu` (см. шапку). Длина = `tu.len()`.
pub fn theta_series(tu: &[i64], ds: &[f64], la: &Leg, lb: &Leg, rule: &Rule) -> Vec<f64> {
    let n = tu.len();
    let (mut floor, mut bad) = (vec![f64::NAN; n], vec![false; n]);
    let (mut ka, mut kb) = (0usize, 0usize);
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
        bad[i] = sa >= rule.xs || sb >= rule.xs;
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
    };
    // значение тика в окне; мусор и неопределённый d в окно не входят
    let v = |i: usize| if bad[i] || !ds[i].is_finite() { f64::NAN } else if signed { ds[i] } else { ds[i].abs() };
    let mut vals: Vec<f64> = (0..n).map(v).filter(|x| x.is_finite()).collect();
    vals.sort_unstable_by(|a, b| a.partial_cmp(b).unwrap());
    vals.dedup();
    let mut rk = vec![u32::MAX; n];
    let mut prev = (f64::NAN, u32::MAX);
    for (i, r) in rk.iter_mut().enumerate() {
        let x = v(i);
        if x.is_finite() {
            if x != prev.0 {
                prev = (x, vals.partition_point(|&w| w < x) as u32);
            }
            *r = prev.1;
        }
    }
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
            cur = if win.n == 0 || hi - tu[0] < rule.warm_ms {
                f64::NAN
            } else {
                match rule.stat {
                    Stat::Quantile(p) => win.quantile(p),
                    Stat::MadK(k) => {
                        let med = win.median();
                        med + k * 1.4826 * win.mad(med)
                    }
                    Stat::Floor => unreachable!(),
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
            Stat::Floor => unreachable!(),
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
        for stat in ["a2,phi=1,w=300,p=0.9,warm=0", "a3,phi=1,w=300,k=3,warm=0"] {
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
