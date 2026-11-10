//! TK-500 П-40 §3.1–3.2: ядро эпизодов схождения (v0): книга двух бирж -> уровень -> эпизоды. Определения — в шапке tools/p40core/ref_episodes.py;
//! вход — плоские бинарники tools/p40core/to_bin.py (<dirA>/<ЧЧ>.bin, <dirB>/<ЧЧ>.bin), выход — csv построчно как у эталона.
//! Запуск: p40core <out.csv> <dirA> <dirB> [<trA> <trB> <lotA> <lotB> [<L_ms=500> [<stepA> <stepB>]]] — с сделками дополнительно <out.csv>.fills.csv (заявки входа §3.3, проход 2).
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
    /// шаг количества инструмента для `is_filled` (в hftbacktest — `depth.lot_size()`, не размер заявки); умолчание ≈0 = «полное исполнение при front < 0» эталона TK-501
    step: [f64; 2],
    /// у ноги нет файлов сделок (Bybit ob200, Gate): П-40 §2 / §16.2 — очередь по убыли объёма (`drain`, верхняя граница), по проходу цены сквозь уровень (`through`, нижняя) либо вне мейкер-анализа (`maker_off`, умолчание), `P40_NOTRADE=drain|through|skip`
    no_tr: [bool; 2],
    nt: NoTrade,
    l_ms: i64,
    /// жизнь заявки входа/страховки (мс); умолчание `T_LIFE_MS`, список вариантов — `P40_VARIANTS`
    t_life: i64,
    lev: &'a [f64],
    s0: i64,
    valid: &'a Valid,
}

/// Усечение измерения (П-40 §2.2): конец данных и начала негодных интервалов книги (дыры G0). Выключено (конец = ∞, дыр нет), пока не задан `P40_TRUNC`.
struct Valid {
    on: bool,
    end: i64,
    /// негодные интервалы [начало, конец), мс, по возрастанию
    holes: Vec<(i64, i64)>,
}

impl Ctx<'_> {
    /// Мейкер-анализ выключен: у одной из ног нет сделок и выбран режим `skip` — заявки на такой ноге не ставятся, исход эпизода `off_maker` (net не считается).
    fn maker_off(&self) -> bool {
        self.nt == NoTrade::Skip && (self.no_tr[0] || self.no_tr[1])
    }
    /// Режим `убыль объёма` для ноги `i`.
    fn drain_leg(&self, i: usize) -> bool {
        self.nt == NoTrade::Drain && self.no_tr[i]
    }
    /// Режим `проход цены сквозь уровень` для ноги `i`.
    fn through_leg(&self, i: usize) -> bool {
        self.nt == NoTrade::Through && self.no_tr[i]
    }
    /// Момент вступления отмены заявок входа эпизода; при усечении (`P40_TRUNC`) — не позже последней годной мс его интервала (`vend − 1`, §2.2):
    /// исполнение входа на негодных данных (дыра, после конца данных) — не исполнение.
    fn entry_cancel(&self, e: &Ep) -> i64 {
        let tc = cancel_at(e, self.l_ms, self.t_life);
        if self.valid.on {
            tc.min(self.valid.end_after(e.t0).0 - 1)
        } else {
            tc
        }
    }
}

/// `P40_THETAS=5.04,12.6` — список порогов θ за проход (умолчание — `THETAS`).
fn parse_thetas(v: Option<&str>) -> Vec<f64> {
    v.map_or(THETAS.to_vec(), |v| v.split(',').map(|x| x.trim().parse().unwrap()).collect())
}

/// `P40_VARIANTS=500:60000,2000:60000` — варианты (задержка L, жизнь заявки T_life) в мс за проход; умолчание — одна пара (L из аргумента, `T_LIFE_MS`).
fn parse_variants(v: Option<&str>, base_l: i64) -> Vec<(i64, i64)> {
    v.map_or(vec![(base_l, T_LIFE_MS)], |v| {
        v.split(',')
            .map(|x| {
                let (l, t) = x.split_once(':').expect("P40_VARIANTS: L:T_life");
                (l.trim().parse().unwrap(), t.trim().parse().unwrap())
            })
            .collect()
    })
}

/// Исполнение мейкера на ноге без сделок (П-40 §2, §16.2).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum NoTrade {
    /// заявки на ноге не ставятся, исход эпизода `off_maker`
    Skip,
    /// убыль объёма на уровне заявки = сделка (верхняя граница: отмены тоже «исполняют»)
    Drain,
    /// исполнение только при проходе цены сквозь уровень заявки (нижняя граница)
    Through,
}

/// `P40_NOTRADE`: `drain` / `through` / `skip` (не задан = `skip`), иное — отказ.
fn parse_notrade(v: Option<&str>) -> NoTrade {
    match v {
        None | Some("skip") => NoTrade::Skip,
        Some("drain") => NoTrade::Drain,
        Some("through") => NoTrade::Through,
        Some(o) => panic!("P40_NOTRADE: ожидалось skip|drain|through, получено {o}"),
    }
}

impl Valid {
    /// Конец годного интервала, содержащего `t`: (момент, причина: 1 — конец данных, 2 — дыра книги).
    fn end_after(&self, t: i64) -> (i64, u8) {
        match self.holes.iter().find(|h| h.0 > t) {
            Some(h) if h.0 < self.end => (h.0, 2),
            _ => (self.end, 1),
        }
    }
    /// Эпизоды создаются только в годном времени: до конца данных и вне негодных интервалов.
    fn usable(&self, t: i64) -> bool {
        !(self.on && t >= self.end) && !self.holes.iter().any(|h| h.0 <= t && t < h.1)
    }
}

/// Файл `P40_HOLES` (строки `начало_мс,конец_мс`) -> негодные интервалы по возрастанию.
fn read_holes(f: Option<String>) -> Vec<(i64, i64)> {
    f.map_or(vec![], |f| {
        let mut h: Vec<(i64, i64)> = std::fs::read_to_string(f)
            .unwrap()
            .lines()
            .filter(|l| !l.trim().is_empty())
            .map(|l| {
                let (s, e) = l.split_once(',').unwrap();
                (s.trim().parse().unwrap(), e.trim().parse().unwrap())
            })
            .collect();
        h.sort_unstable();
        h
    })
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

/// П-40 §2.3/§16.5 (TK-522): годность часов ноги по L1 — той же книге, что считается (§16.1). По каждому часовому файлу `<ЧЧ>.l1.bin` каталога: число состояний (конец группы с лучшими ценами),
/// из них кроссированных (`bid ≥ ask`, исключаются как в `leg_series`), возраст лучших цен — интервалы между сменами `(bid, ask)` по `event_time`: p99 и максимум, мс. Часа без файла в выводе нет.
fn l1_validity(dir: &str) -> Vec<(usize, usize, usize, i64, i64)> {
    let mut out = vec![];
    for h in 0..crate::book::hours() {
        let Some(rows) = Rows::load(dir, h) else { continue };
        let mut book = Book::default();
        let (mut n, mut crossed) = (0usize, 0usize);
        let (mut last_px, mut last_t) = ((f64::NAN, f64::NAN), -1i64);
        let mut ages: Vec<i64> = vec![];
        for i in 0..rows.len() {
            if rows.snap_start(i) {
                book.clear();
            }
            book.set(rows.is_bid(i), rows.px(i), rows.qt(i));
            if !rows.group_end(i) {
                continue;
            }
            if let Some((bb, aa)) = book.best() {
                n += 1;
                if bb >= aa {
                    crossed += 1;
                    continue;
                }
                if (bb, aa) != last_px {
                    let te = rows.et(i);
                    if last_t >= 0 && te >= last_t {
                        ages.push(te - last_t);
                    }
                    last_px = (bb, aa);
                    last_t = te;
                }
            }
        }
        ages.sort_unstable();
        let p99 = ages.get(((ages.len() as f64) * 0.99) as usize).or(ages.last()).copied().unwrap_or(0);
        out.push((h, n, crossed, p99, ages.last().copied().unwrap_or(0)));
    }
    out
}

fn leg_series(dir: &str) -> Leg {
    let mut book = Book::default();
    let mut leg = Leg { t: vec![], b: vec![], a: vec![], crossed: 0, nonmono: 0 };
    let mut last_t = -1i64;
    for h in 0..crate::book::hours() {
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

fn level_grid(tu: &[i64], x: &[f64], t0: i64, t1: i64, holes: &[(i64, i64)]) -> Vec<f64> {
    let ns = (t1 - t0 + 1) as usize;
    let mut g = vec![f64::NAN; ns];
    let (mut p, mut hi) = (0usize, 0usize);
    for (j, gj) in g.iter_mut().enumerate() {
        let ts = (t0 + j as i64) * 1000;
        while p < tu.len() && tu[p] <= ts {
            p += 1;
        }
        while hi < holes.len() && holes[hi].0 <= ts {
            hi += 1;
        }
        if p > 0 {
            // прогрев после дыры (TK-522, П-40 §2.2): в негодном интервале и до первого события после его конца ряд не определён -> уровень L считается заново, пока 6-часовое окно не покрыто годными данными целиком
            let stale = hi > 0 && tu[p - 1] < holes[hi - 1].1;
            if !stale {
                *gj = x[p - 1];
            }
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
    /// гипотетический вход на старте мейкером (продажа на аске дорогой ноги, покупка на бид дешёвой), оценка по исполнимой стороне (закрытие шорта — аск, лонга — бид), bps номинала ноги, без комиссий
    ea: f64,
    eb: f64,
    mpu: f64,
    t_mpu: i64,
    best: f64,
    pnl_end: f64,
    /// контроль K2: случайный вход, заявки входа не снимаются по θ/2 (Р-8) — `t_th2` не заполняется
    random: bool,
}

impl Ep {
    fn new(theta: f64, t: i64, di: f64, (ea, eb): (f64, f64), random: bool) -> Ep {
        let ad = di.abs();
        Ep { theta, t0: t, dir: if di > 0.0 { 1 } else { -1 }, d0: ad, peak: ad, t_peak: t, t_half: -1, t_th2: -1, t_q: -1, t_end: -1, ea, eb, mpu: 0.0, t_mpu: t, best: 0.0, pnl_end: f64::NAN, random }
    }

    /// Один момент `t` открытого эпизода с лучшими ценами ног (ba, aa — нога A; bb, ab — нога B); true — сошёлся (`dir·d ≤ 0`).
    fn step(&mut self, t: i64, di: f64, (ba, aa, bb, ab): (f64, f64, f64, f64)) -> bool {
        let e = self;
        let pnl = 1e4 * if e.dir > 0 { (e.ea - aa) / e.ea + (bb - e.eb) / e.eb } else { (e.ea - ab) / e.ea + (ba - e.eb) / e.eb };
        if pnl < e.mpu {
            e.mpu = pnl;
            e.t_mpu = t;
        }
        if pnl > e.best {
            e.best = pnl;
        }
        let s = e.dir as f64 * di;
        if s > e.peak {
            e.peak = s;
            e.t_peak = t;
        }
        if !e.random && e.t_th2 < 0 && s <= e.theta / 2.0 {
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
            e.pnl_end = pnl;
            return true;
        }
        false
    }
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

fn episodes(tu: &[i64], ds: &[f64], theta: f64, la: &Leg, lb: &Leg) -> Vec<Ep> {
    let mut out = vec![];
    let mut calm: Option<i64> = None;
    let mut prev: Option<f64> = None;
    let mut open: Option<Ep> = None;
    let th2 = theta / 2.0;
    let (mut ka, mut kb) = (0usize, 0usize);
    for i in 0..tu.len() {
        let t = tu[i];
        while ka < la.t.len() && la.t[ka] <= t {
            ka += 1;
        }
        while kb < lb.t.len() && lb.t[kb] <= t {
            kb += 1;
        }
        let di = ds[i];
        if di != di {
            calm = None;
            prev = None;
            continue;
        }
        let ad = di.abs();
        if ka == 0 || kb == 0 {
            continue;
        }
        let (ba, aa, bb, ab) = (la.b[ka - 1], la.a[ka - 1], lb.b[kb - 1], lb.a[kb - 1]);
        if let Some(e) = open.as_mut() {
            if e.step(t, di, (ba, aa, bb, ab)) {
                out.push(open.take().unwrap());
            }
        } else if let (Some(pa), Some(c)) = (prev, calm) {
            if pa < th2 && ad >= theta && t - c >= W_MS {
                let (ea, eb) = if di > 0.0 { (aa, bb) } else { (ab, ba) };
                open = Some(Ep::new(theta, t, di, (ea, eb), false));
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

/// splitmix64 (фиксированный seed контроля K2; зависимостей нет).
struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }
}

/// Контроль K2 (П-40 §4.3): по одному случайному моменту на настоящий эпизод `theta` — равномерно по времени с определённым `d`, не ближе `min_gap_ms` к настоящему `t0` (`real` отсортирован),
/// направление по знаку `d`; эпизод ведётся как настоящий, но заявки входа не снимаются по θ/2 (Р-8). Каждый момент — независимая симуляция (эпизоды могут перекрываться).
fn k2_episodes(tu: &[i64], ds: &[f64], theta: f64, la: &Leg, lb: &Leg, real: &[i64], valid: &Valid, min_gap_ms: i64, seed: u64) -> Vec<Ep> {
    let ok = |k: usize| ds[k].is_finite() && ds[k] != 0.0;
    let (Some(k_lo), Some(k_hi)) = ((0..tu.len()).find(|&k| ok(k)), (0..tu.len()).rev().find(|&k| ok(k))) else { return vec![] };
    let (t_lo, t_hi) = (tu[k_lo], tu[k_hi]);
    let mut rng = Rng(seed);
    let mut picked: Vec<usize> = vec![];
    let mut tries = 0usize;
    while picked.len() < real.len() && tries < 10_000 * (real.len() + 1) {
        tries += 1;
        let t = t_lo + (rng.next() % ((t_hi - t_lo + 1) as u64)) as i64;
        let k = tu.partition_point(|&u| u <= t) - 1;
        let p = real.partition_point(|&r| r < tu[k]);
        let near = (p < real.len() && real[p] - tu[k] < min_gap_ms) || (p > 0 && tu[k] - real[p - 1] < min_gap_ms);
        if ok(k) && !near && !picked.contains(&k) && valid.usable(tu[k]) {
            picked.push(k);
        }
    }
    if picked.len() < real.len() {
        eprintln!("K2 θ={theta}: найдено {} из {} моментов", picked.len(), real.len());
    }
    picked.sort_unstable();
    let bbo = |t: i64| {
        let (ba, aa) = la.bbo(t)?;
        let (bb, ab) = lb.bbo(t)?;
        Some((ba, aa, bb, ab))
    };
    let mut out = vec![];
    for k0 in picked {
        let (t, di) = (tu[k0], ds[k0]);
        let Some((ba, aa, bb, ab)) = bbo(t) else { continue };
        let mut e = Ep::new(theta, t, di, if di > 0.0 { (aa, bb) } else { (ab, ba) }, true);
        for k in k0 + 1..tu.len() {
            if ds[k].is_finite() {
                if let Some(q) = bbo(tu[k]) {
                    if e.step(tu[k], ds[k], q) {
                        break;
                    }
                }
            }
        }
        out.push(e);
    }
    out
}

/// Момент вступления отмены заявок входа: решение min(θ/2, t0+T_life) + задержка отмены `L_ms` (как у постановки).
fn cancel_at(e: &Ep, l_ms: i64, t_life: i64) -> i64 {
    let dec = if e.t_th2 >= 0 { e.t_th2.min(e.t0 + t_life) } else { e.t0 + t_life };
    dec + l_ms
}

/// Проход 2 (аргументы: trA trB lotA lotB): заявки входа обеих ног каждого эпизода; таблица `<out>.fills.csv`.
fn fills_pass(out: &str, c: &Ctx, eps: &[Ep]) {
    let (la, lb, l_ms, dirs) = (c.leg[0], c.leg[1], c.l_ms, c.dirs);
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
        let tc = c.entry_cancel(e);
        let (buy_a, buy_b) = (e.dir < 0, e.dir > 0);
        let (pa, oka) = at(la, buy_a, e.t0, tp, tc);
        let (pb, okb) = at(lb, buy_b, e.t0, tp, tc);
        let (oka, okb) = (oka && !(c.maker_off() && c.no_tr[0]), okb && !(c.maker_off() && c.no_tr[1]));
        let mut s = [None, None];
        if oka {
            s[0] = Some(ja.len());
            ja.push(fills::Job::new(buy_a, tp, tc, pa, c.step[0]));
        }
        if okb {
            s[1] = Some(jb.len());
            jb.push(fills::Job::new(buy_b, tp, tc, pb, c.step[1]));
        }
        slot.push(s);
    }
    fills::run_mode(dirs[0], &c.tr[0], c.drain_leg(0), c.through_leg(0), &mut ja);
    fills::run_mode(dirs[1], &c.tr[1], c.drain_leg(1), c.through_leg(1), &mut jb);
    let mut o = String::from("theta,t0_ms,dir,t_th2_ms,tc_ms,sell_leg,front_sell,fill_sell_ms,imm_sell,front_buy,fill_buy_ms,imm_buy
");
    for (e, s) in eps.iter().zip(&slot) {
        let rel = |v: i64| if v >= 0 { v - e.t0 } else { -1 };
        let (sell, buy) = if e.dir > 0 { (s[0].map(|k| &ja[k]), s[1].map(|k| &jb[k])) } else { (s[1].map(|k| &jb[k]), s[0].map(|k| &ja[k])) };
        let f = |j: Option<&fills::Job>| j.map_or((-1.0, -1, 0), |j| (j.front0, rel(j.fill), j.imm as u8));
        let (fs, ts, is) = f(sell);
        let (fb, tb, ib) = f(buy);
        o += &format!("{:?},{},{},{},{},{},{},{},{},{},{},{}
", e.theta, e.t0, e.dir, rel(e.t_th2), c.entry_cancel(e) - e.t0, if e.dir > 0 { "A" } else { "B" }, fs, ts, is, fb, tb, ib);
    }
    std::fs::File::create(format!("{out}.fills.csv")).unwrap().write_all(o.as_bytes()).unwrap();
    if fills::L1_STAT.load(std::sync::atomic::Ordering::Relaxed) {
        // TK-515: цена заявки входа уходила из L1 за время жизни (1 — да, -1 — заявки нет); по строкам `fills.csv`
        let mut l = String::from("theta,t0_ms,dir,left_sell,left_buy
");
        for (e, s) in eps.iter().zip(&slot) {
            let (sell, buy) = if e.dir > 0 { (s[0].map(|k| &ja[k]), s[1].map(|k| &jb[k])) } else { (s[1].map(|k| &jb[k]), s[0].map(|k| &ja[k])) };
            let f = |j: Option<&fills::Job>| j.map_or(-1, |j| j.left_l1 as i32);
            l += &format!("{:?},{},{},{},{}
", e.theta, e.t0, e.dir, f(sell), f(buy));
        }
        std::fs::File::create(format!("{out}.l1leave.csv")).unwrap().write_all(l.as_bytes()).unwrap();
    }
    closing(out, c, eps, [ja, jb], &slot);
}

const NEVER: i64 = i64::MAX / 2;
const MAKER_BPS: f64 = 1.26;
const TAKER_BPS: f64 = 3.15;

/// Что делаем с эпизодом после входа (§3.3): обе ноги — выход мейкером по сигналу, одна — страховка, ни одной — ничего.
enum Plan {
    Skip,
    /// нога без сделок в режиме `skip`: эпизод в таблице, исход мейкером не считается
    OffMaker,
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
        if c.maker_off() {
            plans.push(Plan::OffMaker);
            continue;
        }
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
            let vend = c.valid.end_after(e.t0).0;
            let hit = (k0..c.tu.len()).take_while(|&k| c.tu[k] < vend).find(|&k| c.ds[k].is_finite() && e.dir as f64 * c.ds[k] <= 0.5 * e.d0);
            let Some(k) = hit else {
                plans.push(Plan::NoSignal);
                continue;
            };
            let t_x = c.tu[k];
            let xs = c.leg[si].bbo(t_x).unwrap().0;
            let xb = c.leg[bi].bbo(t_x).unwrap().1;
            x[si].push(fills::Job::new(true, t_x + c.l_ms, NEVER, xs, c.step[si]));
            x[bi].push(fills::Job::new(false, t_x + c.l_ms, NEVER, xb, c.step[bi]));
            plans.push(Plan::Exit { js: x[si].len() - 1, jb: x[bi].len() - 1, xs, xb });
        } else {
            let sell_done = fs >= 0;
            let t_c = if sell_done { fs } else { fb }.max(c.entry_cancel(e));
            let (li, buy) = if sell_done { (si, true) } else { (bi, false) };
            let (b, a) = c.leg[li].bbo(t_c).unwrap();
            let xp = if sell_done { b } else { a };
            x[li].push(fills::Job::new(buy, t_c + c.l_ms, t_c + c.t_life + c.l_ms, xp, c.step[li]));
            plans.push(Plan::Insure { sell_done, t_c, xp, j: x[li].len() - 1 });
        }
    }
    fills::run_mode(c.dirs[0], &c.tr[0], c.drain_leg(0), c.through_leg(0), &mut x[0]);
    fills::run_mode(c.dirs[1], &c.tr[1], c.drain_leg(1), c.through_leg(1), &mut x[1]);
    let mut o = String::from("theta,t0_ms,outcome,exit_ms,exit_kind,pnl_bps,fees_bps");
    o += if c.valid.on { ",trunc\n" } else { "\n" };
    let stat = fills::L1_STAT.load(std::sync::atomic::Ordering::Relaxed);
    let mut lv = String::from("theta,t0_ms,left_exit\n");
    for ((e, s), plan) in eps.iter().zip(slot).zip(&plans) {
        let (si, bi) = if e.dir > 0 { (0, 1) } else { (1, 0) };
        let sj = s[si].map(|k| &jobs[si][k]);
        let bj = s[bi].map(|k| &jobs[bi][k]);
        let (ps, pb) = (sj.map_or(f64::NAN, |j| j.price), bj.map_or(f64::NAN, |j| j.price));
        // усечение §2.2: годный интервал книги, содержащий старт эпизода; закрытие тейкером по исполнимой стороне последней годной книги
        let t_fill = sj.map_or(-1, |j| j.fill).max(bj.map_or(-1, |j| j.fill));
        let (vend, why) = if c.valid.on && t_fill >= 0 { c.valid.end_after(e.t0) } else { (i64::MAX, 0) };
        let trunc_close = |fee: f64| {
            let a_s = c.leg[si].bbo(vend - 1).unwrap().1;
            let b_b = c.leg[bi].bbo(vend - 1).unwrap().0;
            ("both", vend, "trunc_taker", ((ps - a_s) / ps + (b_b - pb) / pb) * 1e4 - fee, fee, why)
        };
        let (outcome, exit_t, kind, pnl, fees, tr) = match *plan {
            // окно входа обрезано концом годного интервала (`entry_cancel` < срока по T_life/θ/2): без позиции, исход неизвестен — причина усечения, P&L нет
            Plan::Skip if c.valid.on && cancel_at(e, c.l_ms, c.t_life) >= c.valid.end_after(e.t0).0 => ("none", -1, "trunc_no_entry", f64::NAN, f64::NAN, c.valid.end_after(e.t0).1),
            Plan::Skip => ("none", -1, "", f64::NAN, f64::NAN, 0),
            Plan::OffMaker => ("off_maker", -1, "no_trades_leg", f64::NAN, f64::NAN, 0),
            Plan::NoSignal if why > 0 => trunc_close(2.0 * MAKER_BPS + 2.0 * TAKER_BPS),
            Plan::NoSignal => ("both", -1, "no_exit_signal", f64::NAN, f64::NAN, 0),
            Plan::Exit { js, jb, xs, xb } => {
                let (rs, rb) = (x[si][js].fill, x[bi][jb].fill);
                if rs >= 0 && rb >= 0 && rs.max(rb) < vend {
                    let t = rs.max(rb);
                    let kind = if e.t_end > 0 && t > e.t_end { "maker_after_mid" } else { "maker" };
                    let pnl = ((ps - xs) / ps + (xb - pb) / pb) * 1e4;
                    ("both", t, kind, pnl - 4.0 * MAKER_BPS, 4.0 * MAKER_BPS, 0)
                } else if why > 0 {
                    trunc_close(2.0 * MAKER_BPS + 2.0 * TAKER_BPS)
                } else {
                    ("both", -1, "exit_unfilled_censored", f64::NAN, f64::NAN, 0)
                }
            }
            Plan::Insure { sell_done, t_c, xp, j } => {
                let li = if sell_done { si } else { bi };
                let r = x[li][j].fill;
                let t_t = t_c + c.l_ms + c.t_life + c.l_ms;
                let pnl_at = |xp: f64| if sell_done { (ps - xp) / ps } else { (xp - pb) / pb } * 1e4;
                if r >= 0 && r < vend {
                    ("one", r, "insurance_maker", pnl_at(xp) - 2.0 * MAKER_BPS, 2.0 * MAKER_BPS, 0)
                } else if t_t < vend {
                    let (b2, a2) = c.leg[li].bbo(t_t).unwrap();
                    let fee = MAKER_BPS + TAKER_BPS;
                    ("one", t_t, "insurance_taker", pnl_at(if sell_done { a2 } else { b2 }) - fee, fee, 0)
                } else {
                    let (b2, a2) = c.leg[li].bbo(vend - 1).unwrap();
                    let fee = MAKER_BPS + TAKER_BPS;
                    ("one", vend, "trunc_taker", pnl_at(if sell_done { a2 } else { b2 }) - fee, fee, why)
                }
            }
        };
        o += &format!("{:?},{},{},{},{},{},{}", e.theta, e.t0, outcome, exit_t, kind, pnl, fees);
        if c.valid.on {
            o += &format!(",{tr}");
        }
        o.push('\n');
        if stat {
            // TK-515: заявка выхода/страховки уходила из L1 (1/0), -1 — выхода нет
            let lx = match *plan {
                Plan::Exit { js, jb, .. } => (x[si][js].left_l1 || x[bi][jb].left_l1) as i32,
                Plan::Insure { sell_done, j, .. } => x[if sell_done { si } else { bi }][j].left_l1 as i32,
                _ => -1,
            };
            lv += &format!("{:?},{},{}\n", e.theta, e.t0, lx);
        }
    }
    std::fs::File::create(format!("{out}.exits.csv")).unwrap().write_all(o.as_bytes()).unwrap();
    if stat {
        std::fs::File::create(format!("{out}.l1leave_exits.csv")).unwrap().write_all(lv.as_bytes()).unwrap();
    }
}

/// Таблицы эпизодов: `<out>` (описание пути) и `<out>.path.csv` (МПУ).
fn write_tables(out: &str, eps: &[Ep]) {
    let mut o = String::from("theta,t0_ms,dir,d0_bps,peak_bps,t_peak_ms,mae_mid_bps,t_half_ms,t_quarter_ms,t_conv_ms,censored\n");
    let mut path = String::from("theta,t0_ms,mpu_bps,t_mpu_ms,best_bps,pnl_end_bps\n");
    for e in eps {
        path += &format!("{:?},{},{:.4},{},{:.4},{:.4}\n", e.theta, e.t0, e.mpu, e.t_mpu - e.t0, e.best, e.pnl_end);
        let r = |v: i64| if v >= 0 { v - e.t0 } else { -1 };
        o += &format!(
            "{:?},{},{},{:.4},{:.4},{},{:.4},{},{},{},{}\n",
            e.theta, e.t0, e.dir, e.d0, e.peak, e.t_peak - e.t0, e.peak - e.d0, r(e.t_half), r(e.t_q), r(e.t_end), if e.t_end >= 0 { 0 } else { 1 }
        );
    }
    std::fs::File::create(out).unwrap().write_all(o.as_bytes()).unwrap();
    std::fs::File::create(format!("{out}.path.csv")).unwrap().write_all(path.as_bytes()).unwrap();
}

/// Проходы 2–4 (заявки входа, выход/страховка/усечение, признаки) над таблицей эпизодов `eps`.
fn post_passes(out: &str, c: &mut Ctx, eps: &[Ep], variants: &[(i64, i64)]) {
    // варианты (L, T_life) — за один проход по сырью: заявки/выходы считаются на каждый, эпизоды и признаки от них не зависят; один вариант — имена без суффикса
    for &(l, tl) in variants {
        c.l_ms = l;
        c.t_life = tl;
        if variants.len() > 1 {
            fills_pass(&format!("{out}.v{l}_{tl}"), c, eps);
        } else {
            fills_pass(out, c, eps);
        }
    }
    feats::run(out, c, &eps.iter().map(|e| (e.theta, e.t0, e.dir, e.d0)).collect::<Vec<_>>());
}

/// Контроли и усечение — только по флагам среды (умолчание = прежнее поведение):
/// `P40_SHIFT_B_MS=<мс>` — K1, сдвиг времени ноги B (книга и сделки); `P40_K2=1` — K2, таблицы `<out>.k2*` (seed `P40_SEED`, умолчание 40; зазор `P40_K2_GAP_MS`, умолчание 600000 = П-40 §4.3);
/// `P40_NOTRADE=drain|through|skip` — нога без файлов сделок (П-40 §2, §16.2): `drain` — убыль объёма на уровне заявки = сделка (верхняя граница исполнений), `through` — исполнение только при проходе цены сквозь уровень (нижняя), `skip` (умолчание) — заявки на ноге не ставятся, исход `off_maker`;
/// `P40_TRUNC=1` — усечение §2.2 (+ колонка `trunc` в exits: 1 — конец данных, 2 — дыра), конец данных `P40_END_MS` (умолчание — последний момент рядов), дыры — файл `P40_HOLES` со строками `начало_мс,конец_мс`.
fn main() {
    let a: Vec<String> = std::env::args().collect();
    // G-L1a (TK-515): ряд L1 каталога — печать `(t, bid, bq, ask, aq)`; полная книга и её L1-файл дают одинаковый вывод (sha256 в гейте)
    if a.get(1).map(String::as_str) == Some("l1series") {
        let mut o = std::io::BufWriter::new(std::io::stdout().lock());
        for (t, b, bq, ak, aq) in book::l1_series(&a[2]) {
            writeln!(o, "{t} {b:?} {bq:?} {ak:?} {aq:?}").unwrap();
        }
        return;
    }
    // validity по L1 (TK-522, П-40 §16.5): `p40core validity <dir>` — строки `час состояний кроссированных age_p99_мс age_max_мс`
    if a.get(1).map(String::as_str) == Some("validity") {
        let mut o = std::io::BufWriter::new(std::io::stdout().lock());
        for (h, n, c, p99, mx) in l1_validity(&a[2]) {
            writeln!(o, "{h} {n} {c} {p99} {mx}").unwrap();
        }
        return;
    }
    let t = Instant::now();
    let env = |k: &str| std::env::var(k).ok();
    if let Some(v) = env("P40_SHIFT_B_MS") {
        let sh: i64 = v.parse().unwrap();
        let mut g = book::SHIFTS.lock().unwrap();
        g.push((a[3].clone(), sh));
        if a.len() > 5 {
            g.push((a[5].clone(), sh));
        }
        eprintln!("K1: сдвиг ноги B {sh} мс");
    }
    let la = leg_series(&a[2]);
    eprintln!("A {} состояний crossed {} nonmono {} {:.1}s", la.t.len(), la.crossed, la.nonmono, t.elapsed().as_secs_f64());
    let lb = leg_series(&a[3]);
    eprintln!("B {} состояний crossed {} nonmono {} {:.1}s", lb.t.len(), lb.crossed, lb.nonmono, t.elapsed().as_secs_f64());
    let (tu, x) = x_series(&la, &lb);
    eprintln!("объединённых моментов {} {:.1}s", tu.len(), t.elapsed().as_secs_f64());
    let (t0, t1) = (tu[0] / 1000, tu[tu.len() - 1] / 1000);
    let holes_in = if env("P40_TRUNC").is_some() { read_holes(env("P40_HOLES")) } else { vec![] };
    let lev = level_grid(&tu, &x, t0, t1, if env("P40_HOLE_WARM").is_some() { &holes_in } else { &[] });
    eprintln!("уровень: определён на {} из {} с {:.1}s", lev.iter().filter(|v| v.is_finite()).count(), lev.len(), t.elapsed().as_secs_f64());
    let ds = d_series(&tu, &x, t0, &lev);
    let valid = if env("P40_TRUNC").is_some() {
        let holes = holes_in;
        Valid { on: true, end: env("P40_END_MS").map_or(tu[tu.len() - 1] + 1, |v| v.parse().unwrap()), holes }
    } else {
        Valid { on: false, end: i64::MAX, holes: vec![] }
    };
    let mut all: Vec<Ep> = vec![];
    let thetas = parse_thetas(env("P40_THETAS").as_deref());
    for &th in &thetas {
        all.extend(episodes(&tu, &ds, th, &la, &lb).into_iter().filter(|e| valid.usable(e.t0)));
    }
    write_tables(&a[1], &all);
    let base_l: i64 = a.get(8).map_or(500, |v| v.parse().unwrap());
    let variants = parse_variants(env("P40_VARIANTS").as_deref(), base_l);
    let mut ctx = (a.len() >= 8).then(|| {
        fills::NO_CROSS.store(std::env::var_os("P40_NO_CROSS").is_some(), std::sync::atomic::Ordering::Relaxed);
        fills::L1_STAT.store(std::env::var_os("P40_L1_STAT").is_some(), std::sync::atomic::Ordering::Relaxed);
        let tr = [load_trades(&a[4]), load_trades(&a[5])];
        let no_tr = [tr[0].is_empty(), tr[1].is_empty()];
        let nt = parse_notrade(env("P40_NOTRADE").as_deref());
        if no_tr[0] || no_tr[1] {
            let mode = match nt {
                NoTrade::Drain => "убыль объёма",
                NoTrade::Through => "проход цены сквозь уровень",
                NoTrade::Skip => "вне мейкер-анализа",
            };
            eprintln!("нога без сделок A={} B={}: режим {mode}", no_tr[0], no_tr[1]);
        }
        Ctx {
            leg: [&la, &lb],
            tu: &tu,
            ds: &ds,
            dirs: [&a[2], &a[3]],
            tr,
            no_tr,
            nt,
            lot: [a[6].parse().unwrap(), a[7].parse().unwrap()],
            step: [a.get(9).map_or(1e-9, |v| v.parse().unwrap()), a.get(10).map_or(1e-9, |v| v.parse().unwrap())],
            l_ms: base_l,
            t_life: T_LIFE_MS,
            lev: &lev,
            s0: t0,
            valid: &valid,
        }
    });
    if let Some(c) = ctx.as_mut() {
        post_passes(&a[1], c, &all, &variants);
    }
    if env("P40_K2").is_some() {
        let seed: u64 = env("P40_SEED").map_or(40, |v| v.parse().unwrap());
        let gap: i64 = env("P40_K2_GAP_MS").map_or(600_000, |v| v.parse().unwrap());
        let mut k2: Vec<Ep> = vec![];
        for (n, th) in thetas.iter().enumerate() {
            let real: Vec<i64> = all.iter().filter(|e| e.theta == *th).map(|e| e.t0).collect();
            // seed пары-суток: базовый seed, первый момент рядов и номер θ (воспроизводимо, печатается)
            let s = seed ^ (tu[0] as u64).wrapping_mul(0x9E37_79B9_7F4A_7C15) ^ n as u64;
            eprintln!("K2 θ={th}: настоящих {}, seed {s}", real.len());
            k2.extend(k2_episodes(&tu, &ds, *th, &la, &lb, &real, &valid, gap, s));
        }
        let k2out = format!("{}.k2", a[1]);
        write_tables(&k2out, &k2);
        if let Some(c) = ctx.as_mut() {
            post_passes(&k2out, c, &k2, &variants);
        }
    }
    eprintln!("готово {:.2}s", t.elapsed().as_secs_f64());
}

#[cfg(test)]
mod tests {
    use super::*;

    fn leg(t: &[i64], b: &[f64], a: &[f64]) -> Leg {
        Leg { t: t.to_vec(), b: b.to_vec(), a: a.to_vec(), crossed: 0, nonmono: 0 }
    }

    #[test]
    fn thetas_and_variants_lists() {
        assert_eq!(parse_thetas(None), THETAS.to_vec());
        assert_eq!(parse_thetas(Some("5.04, 12.6")), vec![5.04, 12.6]);
        assert_eq!(parse_variants(None, 500), vec![(500, 60000)]);
        assert_eq!(parse_variants(Some("500:60000,2000:30000"), 7), vec![(500, 60000), (2000, 30000)]);
    }

    #[test]
    fn notrade_mode_and_maker_off() {
        assert!(parse_notrade(None) == NoTrade::Skip && parse_notrade(Some("skip")) == NoTrade::Skip);
        assert!(parse_notrade(Some("drain")) == NoTrade::Drain && parse_notrade(Some("through")) == NoTrade::Through);
        let (la, valid) = (leg(&[0], &[1.0], &[2.0]), Valid { on: false, end: i64::MAX, holes: vec![] });
        let mk = |no_tr: [bool; 2], nt: NoTrade| Ctx { leg: [&la, &la], tu: &[], ds: &[], dirs: ["", ""], tr: [vec![], vec![]], lot: [1.0; 2], step: [1.0; 2], no_tr, nt, l_ms: 0, t_life: T_LIFE_MS, lev: &[], s0: 0, valid: &valid };
        assert!(mk([false, true], NoTrade::Skip).maker_off() && mk([true, false], NoTrade::Skip).maker_off());
        assert!(!mk([false, false], NoTrade::Skip).maker_off(), "обе ноги со сделками — прежнее поведение");
        let d = mk([false, true], NoTrade::Drain);
        assert!(!d.maker_off() && d.drain_leg(1) && !d.drain_leg(0) && !d.through_leg(1));
        let t = mk([false, true], NoTrade::Through);
        assert!(!t.maker_off() && t.through_leg(1) && !t.through_leg(0) && !t.drain_leg(1));
    }

    #[test]
    fn level_rewarms_after_hole() {
        // события каждую секунду, x = 1; дыра [22000 с, 22100 с): без флага уровень определён, с дырой — NaN до конца дыры + окно 6 ч
        let ns = 50_000usize;
        let tu: Vec<i64> = (0..ns as i64).map(|i| i * 1000).collect();
        let x = vec![1.0; ns];
        let base = level_grid(&tu, &x, 0, ns as i64 - 1, &[]);
        let warm = level_grid(&tu, &x, 0, ns as i64 - 1, &[(22_000_000, 22_100_000)]);
        assert!(base[23_000].is_finite() && base[30_000].is_finite());
        assert!(warm[21_900].is_finite(), "до дыры уровень прежний");
        assert!(warm[22_059].is_finite(), "уровень отстаёт на GAP_S: первые 60 с дыры ещё по годным данным");
        assert!(warm[22_060].is_nan() && warm[23_000].is_nan() && warm[22_100 + L_S - 10].is_nan());
        assert!(warm[22_100 + L_S + 10].is_finite(), "окно покрыто годными данными целиком");
        assert_eq!(warm[21_700], base[21_700]);
        assert_eq!(warm[49_000], base[49_000]);
    }

    #[test]
    fn valid_end_hole_and_data_end() {
        let v = Valid { on: true, end: 10_000, holes: vec![(3_000, 4_000), (6_000, 7_000)] };
        assert_eq!(v.end_after(1_000), (3_000, 2));
        assert_eq!(v.end_after(4_500), (6_000, 2));
        assert_eq!(v.end_after(8_000), (10_000, 1));
        assert!(!v.usable(3_000) && !v.usable(3_999) && v.usable(4_000) && !v.usable(10_000));
        // дыра за концом данных не считается
        let w = Valid { on: true, end: 5_000, holes: vec![(6_000, 7_000)] };
        assert_eq!(w.end_after(1_000), (5_000, 1));
    }

    #[test]
    fn k2_is_deterministic_apart_and_independent_of_theta_stop() {
        // d: 0 -> растёт до 20 (сошлось не раньше конца), ряд 1 момент в 1 с
        let tu: Vec<i64> = (0..4000).map(|i| i * 1000).collect();
        let ds: Vec<f64> = (0..4000).map(|i| if i < 100 { f64::NAN } else { 10.0 + (i % 7) as f64 }).collect();
        let b: Vec<f64> = vec![100.0; 4000];
        let a: Vec<f64> = vec![100.1; 4000];
        let (la, lb) = (leg(&tu, &b, &a), leg(&tu, &b, &a));
        let valid = Valid { on: true, end: i64::MAX, holes: vec![] };
        let real = vec![1_000_000i64, 3_000_000];
        let r1 = k2_episodes(&tu, &ds, 12.6, &la, &lb, &real, &valid, 600_000, 7);
        let r2 = k2_episodes(&tu, &ds, 12.6, &la, &lb, &real, &valid, 600_000, 7);
        assert_eq!(r1.len(), 2);
        assert_eq!(r1.iter().map(|e| e.t0).collect::<Vec<_>>(), r2.iter().map(|e| e.t0).collect::<Vec<_>>());
        for e in &r1 {
            assert!(e.random && e.t_th2 < 0, "K2 не снимается по θ/2 (Р-8)");
            assert!(real.iter().all(|&t| (e.t0 - t).abs() >= 600_000), "не ближе 600 с к настоящему");
            assert!(e.t0 >= 100_000 && e.dir == 1);
        }
        // другой seed — другие моменты
        let r3 = k2_episodes(&tu, &ds, 12.6, &la, &lb, &real, &valid, 600_000, 8);
        assert_ne!(r1.iter().map(|e| e.t0).collect::<Vec<_>>(), r3.iter().map(|e| e.t0).collect::<Vec<_>>());
    }

    /// Каталог ноги с одним снимком в t=0 (бид 10 ×100, аск 11 ×50): строки `00.bin` формата `to_bin` (33 Б).
    fn flat_book_dir(tag: &str) -> String {
        let dir = std::env::temp_dir().join(format!("p40core-trunc-{tag}-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let mut raw = vec![];
        for (px, qt, fl) in [(10.0f64, 100.0f64, 3u8), (11.0, 50.0, 1)] {
            raw.extend_from_slice(&0i64.to_le_bytes());
            raw.extend_from_slice(&1i64.to_le_bytes());
            raw.extend_from_slice(&px.to_le_bytes());
            raw.extend_from_slice(&qt.to_le_bytes());
            raw.push(fl);
        }
        std::fs::write(dir.join("00.bin"), raw).unwrap();
        dir.to_str().unwrap().to_string()
    }

    /// Строка `<out>.exits.csv` единственного эпизода: (outcome, exit_kind, pnl_bps, trunc) при `valid` и сделке продажи на ноге A в `t_trade`.
    fn exits_row(tag: &str, valid: &Valid, t_trade: i64) -> (String, String, f64, String) {
        let (da, db) = (flat_book_dir(&format!("{tag}a")), flat_book_dir(&format!("{tag}b")));
        let la = leg(&[0], &[10.0], &[11.0]);
        let tr = [
            vec![book::Tr { t: t_trade, px: 11.0, qty: 51.0, buyer_maker: false }],
            vec![book::Tr { t: 100, px: 10.5, qty: 0.001, buyer_maker: true }],
        ];
        let c = Ctx { leg: [&la, &la], tu: &[], ds: &[], dirs: [&da, &db], tr, lot: [1.0; 2], step: [1e-9; 2], no_tr: [false; 2], nt: NoTrade::Skip, l_ms: 500, t_life: T_LIFE_MS, lev: &[], s0: 0, valid };
        // A дороже: продаёт A (аск 11), покупает B (бид 10); заявки встают в t0+L = 1500, живут до 61 500
        let eps = [Ep::new(5.04, 1_000, 12.0, (11.0, 10.0), false)];
        let out = std::env::temp_dir().join(format!("p40core-trunc-out-{tag}-{}", std::process::id())).to_str().unwrap().to_string();
        fills_pass(&out, &c, &eps);
        let txt = std::fs::read_to_string(format!("{out}.exits.csv")).unwrap();
        let f: Vec<&str> = txt.lines().nth(1).unwrap().split(',').collect();
        (f[2].to_string(), f[4].to_string(), f[5].parse().unwrap(), f.get(7).map_or(String::new(), |s| s.to_string()))
    }

    #[test]
    fn entry_filled_after_hole_start_is_no_position() {
        let hole = Valid { on: true, end: i64::MAX, holes: vec![(20_000, 25_000)] };
        // продажа исполняется сделкой в 30 000 — уже внутри/после дыры, начавшейся в 20 000 (после постановки в 1 500): позиции нет, P&L нет
        let (outcome, kind, pnl, trunc) = exits_row("h1", &hole, 30_000);
        assert_eq!((outcome.as_str(), kind.as_str(), trunc.as_str()), ("none", "trunc_no_entry", "2"));
        assert!(pnl.is_nan());
        // конец данных — то же, причина 1
        let end = Valid { on: true, end: 20_000, holes: vec![] };
        let (outcome, kind, pnl, trunc) = exits_row("h2", &end, 30_000);
        assert_eq!((outcome.as_str(), kind.as_str(), trunc.as_str()), ("none", "trunc_no_entry", "1"));
        assert!(pnl.is_nan());
        // вход исполнен до дыры (10 000): позиция есть, закрыта на последней годной книге
        let (outcome, kind, pnl, trunc) = exits_row("h3", &hole, 10_000);
        assert_eq!((outcome.as_str(), kind.as_str(), trunc.as_str()), ("one", "trunc_taker", "2"));
        assert!(pnl.is_finite());
        // без P40_TRUNC прежнее поведение: сделка в 30 000 исполняет вход (позиция на одной ноге, страховка), колонки trunc нет
        let off = Valid { on: false, end: i64::MAX, holes: vec![] };
        let (outcome, kind, _, trunc) = exits_row("h4", &off, 30_000);
        assert_eq!((outcome.as_str(), trunc.as_str()), ("one", ""));
        assert_ne!(kind, "trunc_no_entry");
    }

    #[test]
    fn k2_skips_hole() {
        let tu: Vec<i64> = (0..2000).map(|i| i * 1000).collect();
        let ds: Vec<f64> = vec![15.0; 2000];
        let (b, a) = (vec![100.0; 2000], vec![100.1; 2000]);
        let (la, lb) = (leg(&tu, &b, &a), leg(&tu, &b, &a));
        let valid = Valid { on: true, end: i64::MAX, holes: vec![(0, 1_900_000)] };
        let out = k2_episodes(&tu, &ds, 12.6, &la, &lb, &[10_000_000], &valid, 600_000, 1);
        assert!(out.iter().all(|e| e.t0 >= 1_900_000));
    }
}
