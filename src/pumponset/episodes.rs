//! Эпизоды трендов П-34 §2 — порт определения v11 (`tools/compute/tk435-windows.py`) на Rust.
//!
//! Ход по концу `j` и длительности-классу `D`: старт `i ∈ [j − D, j − MIN_DUR]` (окно обрезано началом ряда),
//! вверх — от минимума close окна, вниз — от максимума; отрицательный ход → 0; ход считается по `close_ff`
//! (П-34 §1; питон v11 NaN-минуты не протягивал, у ликвидных контрактов разницы нет). Ход меряется в `ln`
//! (`ln c[j] − min ln c`, `max ln c − ln c[j]`, как в `types.rs`): монотонная замена арифметического
//! `c[j]/min − 1` и `1 − c[j]/max` v11 — те же старты, те же эпизоды, порог `X_D` в тех же единицах.
//! При равных экстремумах старт — самый ранний (как `argmin`/`argmax` питона). Событие на `(D, знак)` — жадно по
//! убыванию хода (при равных меньший `j`), непересекающиеся отрезки `[старт, конец]`; кластеры v11 склеивали
//! события разных рынков одной монеты — у контракта рынок один, кластер = событие. E1 — имя с плечом (пусто),
//! E4 — фитиль (флаг), E2/E3 не применяются (П-34 §2); «рыночный» — `mark_market`.
//!
//! Порог `X_D` — точный квантиль хода по `(D, знак)` за период фита по всем контрактам, метод `numpy.quantile`
//! `linear` (виртуальный индекс `(n − 1)·q`, `_lerp` numpy ≥ 1.22 как в `tk442-forward.py`); `XdAcc` считает его
//! потоково за два прохода без хранения ≈ 3·10⁹ значений.

use super::cache::Contract;
use super::params::Params;
use super::types::{Episode, XdTable, FLAG_MARKET, FLAG_OUTLIER, FLAG_WICK};
use anyhow::{bail, Result};

/// Минимальная длительность хода, мин: константа определения v11 (tk435: тренд ≥ 1,5 ч, В-57), старт окна
/// не ближе к концу. Не новое число протокола.
pub const MIN_DUR: usize = 90;

/// Сдвиг f32-представления неотрицательного хода в номер корзины: знак (0) + порядок (8) + 8 старших бит
/// мантиссы — корзины шириной ≈ 0,4 % значения, `f32`-ключ монотонен по `f64`-ходу.
const BIN_SHIFT: u32 = 15;
const BINS: usize = 1 << (31 - BIN_SHIFT);

/// `LEV` из `tk435-windows.py`: `(\d+[LS]|BULL|BEAR|UP|DOWN)(_?USDT?|_?USD)?$` по части имени до первого `-`
/// (поиск без привязки слева: имя оканчивается на `UP`/`DOWN`/`BULL`/`BEAR` или `<цифры>L|S`, затем
/// необязательно `USDT`, `USD`, `_USDT`, `_USD`).
pub fn is_leverage(sym: &str) -> bool {
    let base = sym.split('-').next().unwrap_or("");
    ["", "USDT", "USD", "_USDT", "_USD"]
        .iter()
        .any(|suf| base.strip_suffix(suf).is_some_and(ends_with_lev))
}

fn ends_with_lev(s: &str) -> bool {
    if ["BULL", "BEAR", "UP", "DOWN"]
        .iter()
        .any(|w| s.ends_with(w))
    {
        return true;
    }
    let b = s.as_bytes();
    b.len() >= 2 && matches!(b[b.len() - 1], b'L' | b'S') && b[b.len() - 2].is_ascii_digit()
}

/// Буферы на контракт, переиспользуются между контрактами (после прогрева ни одной аллокации на минуту).
#[derive(Default)]
struct Scratch {
    /// `close_ff`: close с переносом вперёд, до первого close — `NaN`.
    ff: Vec<f64>,
    /// `ln` от `ff`.
    lc: Vec<f64>,
    /// Монотонные очереди индексов (минимум / максимум окна старта); каждый индекс кладётся один раз.
    dmin: Vec<u32>,
    dmax: Vec<u32>,
}

impl Scratch {
    fn load(&mut self, c: &Contract) {
        let n = c.close.len();
        self.ff.clear();
        self.lc.clear();
        self.ff.reserve(n);
        self.lc.reserve(n);
        let (mut last, mut last_ln) = (f64::NAN, f64::NAN);
        for &x in &c.close {
            if !x.is_nan() {
                last = x;
                last_ln = x.ln();
            }
            self.ff.push(last);
            self.lc.push(last_ln);
        }
        self.dmin.resize(n, 0);
        self.dmax.resize(n, 0);
    }
}

/// Ходы по концам `j < to` (колбэк зовётся при `j ≥ from`): `f(j, up, i_up, dn, i_dn)`, `NaN`, где конец без
/// close или в окне старта нет ни одного close. Монотонные очереди за O(n); при равных значениях в очереди
/// остаётся самый ранний индекс.
fn scan_moves(
    lc: &[f64],
    w: usize,
    dmin: &mut [u32],
    dmax: &mut [u32],
    from: usize,
    to: usize,
    mut f: impl FnMut(usize, f64, usize, f64, usize),
) {
    let (mut mh, mut mt, mut xh, mut xt) = (0usize, 0usize, 0usize, 0usize);
    for j in 0..to {
        if j >= MIN_DUR {
            let e = j - MIN_DUR;
            let v = lc[e];
            if !v.is_nan() {
                while mt > mh && lc[dmin[mt - 1] as usize] > v {
                    mt -= 1;
                }
                dmin[mt] = e as u32;
                mt += 1;
                while xt > xh && lc[dmax[xt - 1] as usize] < v {
                    xt -= 1;
                }
                dmax[xt] = e as u32;
                xt += 1;
            }
        }
        let lo = j.saturating_sub(w);
        while mt > mh && (dmin[mh] as usize) < lo {
            mh += 1;
        }
        while xt > xh && (dmax[xh] as usize) < lo {
            xh += 1;
        }
        if j < from {
            continue;
        }
        if mt > mh && !lc[j].is_nan() {
            let (im, ix) = (dmin[mh] as usize, dmax[xh] as usize);
            let (up, dn) = (lc[j] - lc[im], lc[ix] - lc[j]);
            f(
                j,
                if up > 0.0 { up } else { 0.0 },
                im,
                if dn > 0.0 { dn } else { 0.0 },
                ix,
            );
        } else {
            f(j, f64::NAN, 0, f64::NAN, 0);
        }
    }
}

/// Кандидат в событие: конец `j`, старт `i`, ход `v ≥ X_D`.
#[derive(Clone, Copy)]
struct Cand {
    j: u32,
    i: u32,
    v: f64,
}

/// Фиксированные для контракта и D значения выбора эпизодов.
struct Pick<'a> {
    ff: &'a [f64],
    first_min: i64,
    di: u8,
    share: f64,
    wick_min: usize,
    tail_lt: usize,
}

/// Жадный отбор: по убыванию хода (при равных меньший `j`), отрезок `[i, j]` не пересекает принятые; флаги E4 и
/// выброса. `cand` — по возрастанию `j`.
fn pick(
    cx: &Pick,
    cand: &[Cand],
    sign: i8,
    order: &mut Vec<u32>,
    taken: &mut Vec<(u32, u32)>,
    out: &mut Vec<Episode>,
) {
    order.clear();
    order.extend(0..cand.len() as u32);
    order.sort_unstable_by(|&a, &b| {
        cand[b as usize]
            .v
            .total_cmp(&cand[a as usize].v)
            .then(a.cmp(&b))
    });
    taken.clear();
    let n = cx.ff.len();
    for &pos in order.iter() {
        let pos = pos as usize;
        let cd = cand[pos];
        let at = taken.partition_point(|&(s, _)| s <= cd.j);
        if at > 0 && taken[at - 1].1 >= cd.i {
            continue;
        }
        taken.insert(at, (cd.i, cd.j));
        let (i, j) = (cd.i as usize, cd.j as usize);
        // E4: после пика цена вернулась хотя бы на долю хода (по цене, как в v11).
        let (cj, ci) = (cx.ff[j], cx.ff[i]);
        let ret = cj - (cj - ci) * cx.share;
        let post = &cx.ff[(j + 1).min(n)..(j + 1 + cx.wick_min).min(n)];
        let wick = if sign > 0 {
            post.iter().any(|&x| x <= ret)
        } else {
            post.iter().any(|&x| x >= ret)
        };
        // Хвост: серия подряд идущих концов с ходом ≥ X_D, содержащая пик.
        let (mut a, mut b) = (pos, pos);
        while a > 0 && cand[a - 1].j + 1 == cand[a].j {
            a -= 1;
        }
        while b + 1 < cand.len() && cand[b + 1].j == cand[b].j + 1 {
            b += 1;
        }
        let mut flags = 0;
        if wick {
            flags |= FLAG_WICK;
        }
        if b - a + 1 < cx.tail_lt {
            flags |= FLAG_OUTLIER;
        }
        out.push(Episode {
            di: cx.di,
            sign,
            t0: cx.first_min + i as i64,
            t_end: cx.first_min + j as i64,
            mv: cd.v,
            flags,
        });
    }
}

/// Поиск эпизодов с переиспользуемыми буферами (один `Finder` на поток).
#[derive(Default)]
pub struct Finder {
    sc: Scratch,
    cand: [Vec<Cand>; 2],
    order: Vec<u32>,
    taken: Vec<(u32, u32)>,
}

impl Finder {
    /// См. `find_episodes`.
    pub fn find(&mut self, c: &Contract, p: &Params, xd: &XdTable, sym: &str) -> Vec<Episode> {
        let mut out = Vec::new();
        if is_leverage(sym) {
            return out;
        }
        assert_eq!(
            xd.x.len(),
            p.episodes.d_min.len(),
            "XdTable: число D другое, чем в params"
        );
        let n = c.close.len();
        if n <= MIN_DUR {
            return out;
        }
        let Finder {
            sc,
            cand,
            order,
            taken,
        } = self;
        sc.load(c);
        let e = &p.episodes;
        for (di, &d) in e.d_min.iter().enumerate() {
            let w = d as usize;
            assert!(w >= MIN_DUR, "episodes.D_min: D меньше MIN_DUR = {MIN_DUR}");
            let [xu, xdn] = xd.x[di];
            let [cu, cdn] = &mut *cand;
            cu.clear();
            cdn.clear();
            scan_moves(
                &sc.lc,
                w,
                &mut sc.dmin,
                &mut sc.dmax,
                0,
                n,
                |j, up, iu, dn, id| {
                    if up >= xu {
                        cu.push(Cand {
                            j: j as u32,
                            i: iu as u32,
                            v: up,
                        });
                    }
                    if dn >= xdn {
                        cdn.push(Cand {
                            j: j as u32,
                            i: id as u32,
                            v: dn,
                        });
                    }
                },
            );
            let cx = Pick {
                ff: &sc.ff,
                first_min: c.first_min,
                di: di as u8,
                share: e.e4_wick.return_share,
                wick_min: e.e4_wick.within_min as usize,
                tail_lt: e.outlier_tail_candles_lt as usize,
            };
            pick(&cx, cu, 1, order, taken, &mut out);
            pick(&cx, cdn, -1, order, taken, &mut out);
        }
        out.sort_by_key(|x| (x.t0, x.di, x.sign));
        out
    }
}

/// Эпизоды контракта (П-34 §2): E1 — имя с плечом → пусто; порог из `xd` (`xd.x[di] = [вверх, вниз]`, `NaN` —
/// нет порога, эпизодов нет); `t0` — минута старта хода, `t_end` — минута пика; флаги `FLAG_WICK` (E4) и
/// `FLAG_OUTLIER` (хвост хода — серия концов с ходом ≥ X_D, содержащая пик, короче `outlier_tail_candles_lt`
/// свечей). Эпизоды помечаются, не удаляются. Порядок — `(t0, di, sign)`.
pub fn find_episodes(c: &Contract, p: &Params, xd: &XdTable, sym: &str) -> Vec<Episode> {
    Finder::default().find(c, p, xd, sym)
}

/// `FLAG_MARKET`: эпизод `BTCUSDT` того же знака и того же `di` перекрывает `[t0, t_end]` эпизода хотя бы на
/// долю `min_overlap_share` его длины (П-34 §2). Берётся один эпизод BTC с наибольшим перекрытием. Эпизоды
/// BTCUSDT с самим собой не сверять — вызывающий пропускает этот контракт.
pub fn mark_market(eps: &mut [Episode], btc: &[Episode], min_overlap_share: f64) {
    // По ключу `di * 2 + (0 вверх | 1 вниз)`, внутри — по `t0`, чтобы выходить рано.
    let mut by_key: Vec<Vec<(i64, i64)>> = Vec::new();
    for b in btc {
        let k = b.di as usize * 2 + usize::from(b.sign < 0);
        if by_key.len() <= k {
            by_key.resize_with(k + 1, Vec::new);
        }
        by_key[k].push((b.t0, b.t_end));
    }
    for g in &mut by_key {
        g.sort_unstable();
    }
    for e in eps.iter_mut() {
        let len = (e.t_end - e.t0) as f64;
        let k = e.di as usize * 2 + usize::from(e.sign < 0);
        let Some(g) = by_key.get(k) else { continue };
        let mut best = 0i64;
        for &(s, t) in g {
            if s > e.t_end {
                break;
            }
            best = best.max(e.t_end.min(t) - e.t0.max(s));
        }
        if len > 0.0 && best as f64 / len >= min_overlap_share {
            e.flags |= FLAG_MARKET;
        }
    }
}

/// Номер корзины неотрицательного хода (монотонно по значению).
fn bin_of(v: f64) -> usize {
    if v > 0.0 {
        ((v as f32).to_bits() >> BIN_SHIFT) as usize
    } else {
        0
    }
}

/// Ранги порядковых статистик `numpy.quantile(..., method="linear")`: `h = (n − 1)·q`, нижняя — `⌊h⌋`,
/// верхняя — следующая (за последним — тот же, `n ≥ 1`); `gamma = h − ⌊h⌋`.
fn ranks(n: u64, q: f64) -> (u64, u64, f64) {
    let h = (n - 1) as f64 * q;
    if h >= (n - 1) as f64 {
        (n - 1, n - 1, 0.0)
    } else {
        let l = h.floor();
        (l as u64, l as u64 + 1, h - l)
    }
}

/// Линейная интерполяция numpy `_lerp`.
fn lerp(a: f64, b: f64, t: f64) -> f64 {
    let d = b - a;
    if t >= 0.5 {
        b - d * (1.0 - t)
    } else {
        a + d * t
    }
}

/// Цель прохода 2 для одного `(D, знак)`: какие корзины собрать и какой порядковый номер выбрать.
#[derive(Clone, Debug, PartialEq)]
struct Target {
    /// Всего значений на проходе 1.
    n: u64,
    lo_rank: u64,
    hi_rank: u64,
    gamma: f64,
    /// Значений в корзинах ниже `bins.0`.
    base: u64,
    /// Собираются значения корзин `bins.0 ..= bins.1` (пусто, если `bins.0 > bins.1`).
    bins: (usize, usize),
    /// Сколько значений в этих корзинах по гистограмме (проверка прохода 2).
    want: u64,
    vals: Vec<f64>,
}

impl Target {
    fn from_hist(hs: &[u64], q: f64) -> Target {
        let n: u64 = hs.iter().sum();
        let mut t = Target {
            n,
            lo_rank: 0,
            hi_rank: 0,
            gamma: 0.0,
            base: 0,
            bins: (1, 0),
            want: 0,
            vals: Vec::new(),
        };
        if n == 0 {
            return t;
        }
        let (lo, hi, gamma) = ranks(n, q);
        let (b_lo, base) = bin_of_rank(hs, lo);
        let (b_hi, _) = bin_of_rank(hs, hi);
        t.lo_rank = lo;
        t.hi_rank = hi;
        t.gamma = gamma;
        t.base = base;
        t.bins = (b_lo, b_hi);
        t.want = hs[b_lo..=b_hi].iter().sum();
        t
    }

    fn take(&mut self, v: f64) {
        if (self.bins.0..=self.bins.1).contains(&bin_of(v)) {
            self.vals.push(v);
        }
    }

    fn same_goal(&self, o: &Target) -> bool {
        self.n == o.n
            && self.lo_rank == o.lo_rank
            && self.hi_rank == o.hi_rank
            && self.base == o.base
            && self.bins == o.bins
            && self.want == o.want
    }

    fn quantile(mut self) -> f64 {
        let l = (self.lo_rank - self.base) as usize;
        let (_, a, right) = self.vals.select_nth_unstable_by(l, f64::total_cmp);
        let a = *a;
        if self.hi_rank == self.lo_rank {
            return a;
        }
        // Верхняя статистика (следующий ранг) — минимум правой части.
        let b = right.iter().copied().fold(f64::INFINITY, f64::min);
        lerp(a, b, self.gamma)
    }
}

/// Корзина, где лежит значение ранга `r` (0-based), и число значений в корзинах ниже неё.
fn bin_of_rank(hs: &[u64], r: u64) -> (usize, u64) {
    let mut cum = 0u64;
    for (b, &c) in hs.iter().enumerate() {
        if cum + c > r {
            return (b, cum);
        }
        cum += c;
    }
    (hs.len() - 1, cum)
}

enum Phase {
    /// Проход 1: счётчики корзин, `hist[(di * 2 + знак) * BINS + корзина]`.
    Hist(Vec<u64>),
    /// Проход 2: цели по ключу `di * 2 + знак` (0 — вверх, 1 — вниз) и собранные значения.
    Collect(Vec<Target>),
}

/// Потоковый точный квантиль хода `X_D` по `(D, знак)` за период фита (П-34 §2), два прохода по одним и тем же
/// контрактам:
/// 1. `new` → `add` по каждому контракту (можно по потокам, затем `merge`) — гистограмма по старшим битам
///    `f32`-представления хода (точные счётчики);
/// 2. `begin_pass2` находит корзины с нужными рангами → `pass2_worker` на поток → `add` теми же контрактами →
///    `merge` → `finish` выбирает точные порядковые элементы только из этих корзин и интерполирует как numpy.
///
/// В выборку входят все концы `j` с определённым ходом (нулевые тоже) в `[fit_min[0], fit_min[1])` — минут от
/// `GRID0_MS`; окно старта может начинаться раньше периода. Контракты с плечевым именем (`is_leverage`) вызывающий
/// не передаёт (в v11 гистограмма строилась без них). Фильтра оборота по дням здесь нет.
pub struct XdAcc {
    nd: usize,
    q: f64,
    phase: Phase,
    sc: Scratch,
}

impl XdAcc {
    pub fn new(p: &Params) -> XdAcc {
        let nd = p.episodes.d_min.len();
        XdAcc {
            nd,
            q: p.episodes.quantile_x_d,
            phase: Phase::Hist(vec![0; nd * 2 * BINS]),
            sc: Scratch::default(),
        }
    }

    /// Добавить ходы контракта за `fit_min` (проход 1 — счётчики, проход 2 — значения нужных корзин).
    pub fn add(&mut self, c: &Contract, p: &Params, fit_min: [i64; 2]) {
        self.add_impl(c, p, fit_min, &|_| true);
    }

    /// `add` с маской вселенной: в выборку входят только концы `j`, чья минута (от `GRID0_MS`) проходит
    /// `in_universe` (слой `run`: сутки, где оборот предыдущих суток ≥ порога, П-34 §1). Окно старта маской не
    /// режется — маска решает, считать ли ход конца `j`. Тот же вызов в обоих проходах.
    pub fn add_masked(
        &mut self,
        c: &Contract,
        p: &Params,
        fit_min: [i64; 2],
        in_universe: impl Fn(i64) -> bool,
    ) {
        self.add_impl(c, p, fit_min, &in_universe);
    }

    fn add_impl(
        &mut self,
        c: &Contract,
        p: &Params,
        fit_min: [i64; 2],
        keep: &impl Fn(i64) -> bool,
    ) {
        assert_eq!(
            p.episodes.d_min.len(),
            self.nd,
            "XdAcc: число D другое, чем в new"
        );
        let n = c.close.len();
        let from = (fit_min[0] - c.first_min).clamp(0, n as i64) as usize;
        let to = (fit_min[1] - c.first_min).clamp(0, n as i64) as usize;
        if from >= to {
            return;
        }
        let first_min = c.first_min;
        self.sc.load(c);
        let Scratch { lc, dmin, dmax, .. } = &mut self.sc;
        for (di, &d) in p.episodes.d_min.iter().enumerate() {
            let w = d as usize;
            assert!(w >= MIN_DUR, "episodes.D_min: D меньше MIN_DUR = {MIN_DUR}");
            match &mut self.phase {
                Phase::Hist(h) => {
                    let (hu, hd) = h[di * 2 * BINS..(di * 2 + 2) * BINS].split_at_mut(BINS);
                    scan_moves(lc, w, dmin, dmax, from, to, |j, up, _, dn, _| {
                        if !up.is_nan() && keep(first_min + j as i64) {
                            hu[bin_of(up)] += 1;
                            hd[bin_of(dn)] += 1;
                        }
                    });
                }
                Phase::Collect(ts) => {
                    let (tu, td) = ts[di * 2..di * 2 + 2].split_at_mut(1);
                    let (tu, td) = (&mut tu[0], &mut td[0]);
                    scan_moves(lc, w, dmin, dmax, from, to, |j, up, _, dn, _| {
                        if !up.is_nan() && keep(first_min + j as i64) {
                            tu.take(up);
                            td.take(dn);
                        }
                    });
                }
            }
        }
    }

    /// Слить результат другого накопителя той же фазы (проход 2 — с теми же целями). Другая фаза, число D или цели — ошибка.
    pub fn merge(&mut self, other: XdAcc) -> Result<()> {
        if self.nd != other.nd {
            bail!("XdAcc::merge: разное число D");
        }
        match (&mut self.phase, other.phase) {
            (Phase::Hist(a), Phase::Hist(b)) => {
                for (x, y) in a.iter_mut().zip(b) {
                    *x += y;
                }
            }
            (Phase::Collect(a), Phase::Collect(b)) => {
                for (x, y) in a.iter_mut().zip(b) {
                    if !x.same_goal(&y) {
                        bail!("XdAcc::merge: разные цели прохода 2");
                    }
                    x.vals.extend(y.vals);
                }
            }
            _ => bail!("XdAcc::merge: накопители в разных фазах"),
        }
        Ok(())
    }

    /// Число значений прохода 1 по `[di][вверх|вниз]` (после `begin_pass2` — те же числа).
    pub fn counts(&self) -> Vec<[u64; 2]> {
        match &self.phase {
            Phase::Hist(h) => (0..self.nd)
                .map(|di| {
                    let s = |k: usize| -> u64 {
                        h[(di * 2 + k) * BINS..(di * 2 + k + 1) * BINS].iter().sum()
                    };
                    [s(0), s(1)]
                })
                .collect(),
            Phase::Collect(ts) => ts.chunks(2).map(|t| [t[0].n, t[1].n]).collect(),
        }
    }

    /// Конец прохода 1: по гистограмме найти корзины рангов `⌊h⌋` и `⌊h⌋ + 1`; дальше `add` собирает значения.
    pub fn begin_pass2(&mut self) -> Result<()> {
        let Phase::Hist(h) = &self.phase else {
            bail!("XdAcc::begin_pass2: нужна фаза прохода 1");
        };
        let ts: Vec<Target> = (0..self.nd * 2)
            .map(|k| Target::from_hist(&h[k * BINS..(k + 1) * BINS], self.q))
            .collect();
        self.phase = Phase::Collect(ts);
        Ok(())
    }

    /// Пустой накопитель прохода 2 с теми же целями (на поток); слить обратно через `merge`. До `begin_pass2` выдаёт
    /// пустой накопитель прохода 1: `merge` в мастер вернёт ошибку фаз, молчаливого ответа нет.
    pub fn pass2_worker(&self) -> XdAcc {
        let phase = match &self.phase {
            Phase::Collect(ts) => Phase::Collect(
                ts.iter()
                    .map(|t| {
                        let mut t = t.clone();
                        t.vals.clear();
                        t
                    })
                    .collect(),
            ),
            Phase::Hist(h) => Phase::Hist(vec![0; h.len()]),
        };
        XdAcc {
            nd: self.nd,
            q: self.q,
            phase,
            sc: Scratch::default(),
        }
    }

    /// Таблица `X_D`; `NaN` там, где значений нет. Ошибка — проход 2 не по тем же данным (счётчики не сошлись)
    /// или проход 2 не начат.
    pub fn finish(self) -> Result<XdTable> {
        let Phase::Collect(ts) = self.phase else {
            bail!("XdAcc::finish: сначала begin_pass2 и проход 2");
        };
        let mut x = vec![[f64::NAN; 2]; self.nd];
        for (k, t) in ts.into_iter().enumerate() {
            if t.n == 0 {
                continue;
            }
            if t.vals.len() as u64 != t.want {
                bail!(
                    "XdAcc::finish: D #{} знак #{}: собрано {} значений, по гистограмме {} — проход 2 не по тем же данным",
                    k / 2,
                    k % 2,
                    t.vals.len(),
                    t.want
                );
            }
            x[k / 2][k % 2] = t.quantile();
        }
        Ok(XdTable { x })
    }
}
