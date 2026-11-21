//! Часовые бары → пивоты → уровни (П-42 §5.1). Только закрытые бары прошлого, без заглядывания.
//!
//! Работаем в «длинной раме»: для шорта цены берутся с обратным знаком (`s = -1`), тогда потолок и пол —
//! один код. Пивот-максимум ширины `k`: максимум бара `h` строго больше максимумов `k` баров слева и справа;
//! он известен только после закрытия бара `h + k`. Глубина `d`: после пивота какой-то закрытый бар ушёл от него
//! на ≥ `d` %, прежде чем закрытие бара оказалось за пивотом (иначе пивот отброшен). Отскок «засчитан» на
//! закрытии бара `max(h + k, бар глубины)`.
//!
//! Уровень: ≥ `N` засчитанных пивотов одного типа в окне `L` суток, все в пределах `τ` % от своего среднего;
//! цена уровня — среднее. Группа строится от нового пивота: ближайшие по цене добавляются, пока условие `τ` держится.
//! Новый пивот присоединяется к живому уровню, если условие держится с ним; члены старше `L` выпадают, при
//! `< N` членов уровень кончается. Уровень жив, пока ни один закрытый бар (после первого пивота) не закрылся за
//! ним; такой бар — истинный пробой, уровень израсходован.

use super::data::{Bars, Sym};
use super::params::LevelCfg;
use super::Side;
use std::sync::atomic::{AtomicU64, Ordering};

/// Счётчики опции `pk` (§13.11, диагностика; считаются только в обычной сборке уровней, не в журнале членов): группы,
/// не родившиеся из-за прокола/закрытия за ценой с первого пивота, и уровни, убитые перепроверкой при смене версии.
pub static PK_BIRTH_BLOCKED: AtomicU64 = AtomicU64::new(0);
pub static PK_RETRO_KILLED: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone)]
pub struct Level {
    /// Первая минута, когда уровень известен (после закрытия бара, давшего N-й отскок).
    pub born: u32,
    /// Первая минута, когда уровня уже нет (`u32::MAX` — до конца данных).
    pub end: u32,
    /// Версии цены: (с какой минуты, цена уровня); цена меняется, когда присоединяется пивот или выпадает член.
    pub segs: Vec<(u32, i64)>,
    pub first_bar: u32,
    /// Только при `build_levels_tracked(.., true)`: для каждой версии цены — члены уровня (бар, цена ×1e8); иначе пусто.
    pub mem: Vec<Vec<(u32, i64)>>,
    /// Источник уровня (TK-668): 0 — пивоты; иначе значение `LevelCfg::lx` (1 `D`, 2 `W`, 3 `P`). Уровень источника «свежий» по построению: `fresh_ok` его пропускает.
    pub src: u8,
}

#[derive(Clone, Copy)]
struct Pv {
    bar: u32,
    /// Значение в длинной раме (шорт — с обратным знаком).
    p: i64,
    active: u32,
}

struct Frame<'a> {
    b: &'a Bars,
    s: i64,
}

impl Frame<'_> {
    fn hi(&self, i: usize) -> i64 {
        if self.s > 0 {
            self.b.h[i]
        } else {
            -self.b.l[i]
        }
    }
    fn lo(&self, i: usize) -> i64 {
        if self.s > 0 {
            self.b.l[i]
        } else {
            -self.b.h[i]
        }
    }
    fn cl(&self, i: usize) -> i64 {
        self.s * self.b.c[i]
    }
}

/// (все члены в пределах τ от среднего, среднее в длинной раме).
fn group_ok(ps: &[i64], tau_pm: i64) -> (bool, i64) {
    let n = ps.len() as i128;
    let sum: i128 = ps.iter().map(|&x| i128::from(x)).sum();
    let mean = (sum / n) as i64;
    let ok = ps.iter().all(|&x| {
        i128::from((x - mean).abs()) * 100_000 <= i128::from(tau_pm) * i128::from(mean.abs())
    });
    (ok, mean)
}

/// `pk` (§13.11): среди закрытых баров `from..=to` есть бар с high за ценой `mean` (длинная рама) больше чем на τ или —
/// после бара `from` — с закрытием за ценой `mean`. Часовой high — максимум минутных, поэтому равно поминутной проверке.
fn pk_violated(f: &Frame, bars: &Bars, from: usize, to: usize, mean: i64, tau_pm: i64) -> bool {
    let lim = mean + (i128::from(mean.abs()) * i128::from(tau_pm) / 100_000) as i64;
    (from..=to).any(|bb| bars.ok[bb] && (f.hi(bb) > lim || (bb > from && f.cl(bb) > mean)))
}

fn find_pivots(f: &Frame, cfg: &LevelCfg) -> Vec<Pv> {
    let nb = f.b.ok.len();
    let k = cfg.k;
    let mut out = Vec::new();
    if nb < 2 * k + 1 {
        return out;
    }
    'outer: for h in k..nb - k {
        for j in h - k..=h + k {
            if !f.b.ok[j] {
                continue 'outer;
            }
        }
        let ph = f.hi(h);
        for j in 1..=k {
            if f.hi(h - j) >= ph || f.hi(h + j) >= ph {
                continue 'outer;
            }
        }
        let thr = ph - (i128::from(ph.abs()) * i128::from(cfg.d_pm) / 100_000) as i64;
        let mut j = h + 1;
        while j < nb {
            if f.b.ok[j] {
                if f.cl(j) > ph {
                    break;
                }
                if f.lo(j) <= thr {
                    out.push(Pv {
                        bar: h as u32,
                        p: ph,
                        active: j.max(h + k) as u32,
                    });
                    break;
                }
            }
            j += 1;
        }
    }
    out.sort_by_key(|p| (p.active, p.bar));
    out
}

struct Live {
    id: usize,
    members: Vec<Pv>,
}

fn prices(m: &[Pv]) -> Vec<i64> {
    m.iter().map(|p| p.p).collect()
}

pub fn build_levels(sym: &Sym, cfg: &LevelCfg, side: Side) -> Vec<Level> {
    build_levels_tracked(sym, cfg, side, false)
}

/// Как `build_levels`, но с журналом членов (`Level::mem`) и со счётчиками `pk` (§13.19: дальний край зоны `P_far` для стопа от уровня).
pub fn build_levels_far(sym: &Sym, cfg: &LevelCfg, side: Side) -> Vec<Level> {
    build_levels_impl(sym, cfg, side, true, true)
}

impl Level {
    /// Дальний край зоны версии цены `seg` в длинной раме: минимум цен пивотов (лонг — самый низкий, шорт — минус самый высокий).
    /// Без журнала членов — `None`.
    pub fn far_edge(&self, seg: usize, s: i64) -> Option<i64> {
        let m = self.mem.get(seg)?;
        if s > 0 {
            m.iter().map(|x| x.1).min()
        } else {
            m.iter().map(|x| -x.1).min()
        }
    }
}

fn members(m: &[Pv]) -> Vec<(u32, i64)> {
    m.iter().map(|p| (p.bar, p.p.abs())).collect()
}

/// `track` — вести журнал членов уровня (`Level::mem`); цены, рождение и конец уровней от него не зависят.
pub fn build_levels_tracked(sym: &Sym, cfg: &LevelCfg, side: Side, track: bool) -> Vec<Level> {
    build_levels_impl(sym, cfg, side, track, !track)
}

/// `count` — считать счётчики `pk` (диагностика): обычная сборка — да, журнал для графиков — нет (не удваиваем).
fn build_levels_impl(
    sym: &Sym,
    cfg: &LevelCfg,
    side: Side,
    track: bool,
    count: bool,
) -> Vec<Level> {
    if cfg.lx != 0 {
        return build_source_levels(sym, cfg, side);
    }
    let Some(bars) = sym.bars_for(cfg.tf_min) else {
        return Vec::new();
    };
    let f = Frame {
        b: bars,
        s: side.s(),
    };
    let pivs = find_pivots(&f, cfg);
    let nb = bars.ok.len();
    let tfm = cfg.tf_min as i64;
    let lwin = cfg.l_days as i64 * 1440;
    let mut levels: Vec<Level> = Vec::new();
    let mut live: Vec<Live> = Vec::new();
    let mut free: Vec<Pv> = Vec::new();
    let mut pi = 0;
    for b in 0..nb {
        let now = (b as i64 + 1) * tfm;
        free.retain(|p| i64::from(p.bar) * tfm >= now - lwin);
        let mut li = 0;
        while li < live.len() {
            let before = live[li].members.len();
            live[li]
                .members
                .retain(|p| i64::from(p.bar) * tfm >= now - lwin);
            let id = live[li].id;
            if live[li].members.len() < cfg.n {
                levels[id].end = now as u32;
                live.remove(li);
                continue;
            }
            let (_, mean) = group_ok(&prices(&live[li].members), cfg.tau_pm);
            if live[li].members.len() != before {
                levels[id].segs.push((now as u32, mean.abs()));
                if track {
                    levels[id].mem.push(members(&live[li].members));
                }
                if cfg.pk {
                    let from = live[li].members.iter().map(|p| p.bar as usize).min();
                    if from.is_some_and(|from| pk_violated(&f, bars, from, b, mean, cfg.tau_pm)) {
                        if count {
                            PK_RETRO_KILLED.fetch_add(1, Ordering::Relaxed);
                        }
                        levels[id].end = now as u32;
                        live.remove(li);
                        continue;
                    }
                }
            }
            if bars.ok[b] && f.cl(b) > mean {
                levels[id].end = now as u32;
                live.remove(li);
                continue;
            }
            li += 1;
        }
        while pi < pivs.len() && pivs[pi].active as usize == b {
            let pv = pivs[pi];
            pi += 1;
            let mut joined = false;
            for li in 0..live.len() {
                let mut cand = prices(&live[li].members);
                cand.push(pv.p);
                let (ok, mean) = group_ok(&cand, cfg.tau_pm);
                if ok {
                    let id = live[li].id;
                    live[li].members.push(pv);
                    levels[id].segs.push((now as u32, mean.abs()));
                    if track {
                        levels[id].mem.push(members(&live[li].members));
                    }
                    joined = true;
                    if cfg.pk {
                        let from = live[li].members.iter().map(|p| p.bar as usize).min();
                        if from.is_some_and(|from| pk_violated(&f, bars, from, b, mean, cfg.tau_pm))
                        {
                            if count {
                                PK_RETRO_KILLED.fetch_add(1, Ordering::Relaxed);
                            }
                            levels[id].end = now as u32;
                            live.remove(li);
                        }
                    }
                    break;
                }
            }
            if joined {
                continue;
            }
            free.push(pv);
            let mut order: Vec<Pv> = free
                .iter()
                .copied()
                .filter(|q| !(q.bar == pv.bar && q.p == pv.p))
                .collect();
            order.sort_by_key(|q| ((q.p - pv.p).abs(), std::cmp::Reverse(q.bar)));
            let mut grp = vec![pv];
            for q in order {
                let mut cand = prices(&grp);
                cand.push(q.p);
                if group_ok(&cand, cfg.tau_pm).0 {
                    grp.push(q);
                } else {
                    break;
                }
            }
            if grp.len() < cfg.n {
                continue;
            }
            let (_, mean) = group_ok(&prices(&grp), cfg.tau_pm);
            let first_bar = grp.iter().map(|p| p.bar).min().unwrap_or(pv.bar);
            free.retain(|p| !grp.iter().any(|q| q.bar == p.bar && q.p == p.p));
            let dead = if cfg.pk {
                let d = pk_violated(&f, bars, first_bar as usize, b, mean, cfg.tau_pm);
                if d && count
                    && !(first_bar as usize + 1..=b).any(|bb| bars.ok[bb] && f.cl(bb) > mean)
                {
                    PK_BIRTH_BLOCKED.fetch_add(1, Ordering::Relaxed);
                }
                d
            } else {
                (first_bar as usize + 1..=b).any(|bb| bars.ok[bb] && f.cl(bb) > mean)
            };
            if !dead {
                levels.push(Level {
                    born: now as u32,
                    end: u32::MAX,
                    segs: vec![(now as u32, mean.abs())],
                    first_bar,
                    mem: if track {
                        vec![members(&grp)]
                    } else {
                        Vec::new()
                    },
                    src: 0,
                });
                live.push(Live {
                    id: levels.len() - 1,
                    members: grp,
                });
            }
        }
    }
    levels
}

// ───────────── TK-668 (`lx`): уровни из источника вместо пивотов ─────────────

/// Источник `lx` читает только закрытые часовые бары (`bars_for(60)`); минутки — лишь профиль `lx = 3`.
const SRC_TF_MIN: usize = 60;
const DAY_BARS: usize = 24;
const WEEK_BARS: usize = 168;
/// `lx = 3`: минимум минут суток с данными, при меньшем числе профиля нет.
const PROFILE_MIN_MINUTES: usize = 1200;
/// `lx = 3`: ширина корзины — `средняя tp суток / PROFILE_BIN_DIV` (0,10 %).
const PROFILE_BIN_DIV: i64 = 1000;

/// Источник уровней `lx ≠ 0` (§13.68): периоды подряд — сутки (`lx = 1, 3`) или календарные недели UTC (`lx = 2`, первый понедельник — на `lw` суток
/// от начала сетки). Уровень периода рождается готовым на первой минуте следующего периода и живёт один период, но не дольше закрытия первого
/// закрытого часового бара за ценой (в раме стороны, как у пивотных уровней: `end` — конец этого бара). Период без данных (бар `ok = false`; для
/// профиля — меньше `PROFILE_MIN_MINUTES` минут с данными) уровня не даёт. Журнала членов нет (`mem` пуст): дальний край зоны — `None`.
fn build_source_levels(sym: &Sym, cfg: &LevelCfg, side: Side) -> Vec<Level> {
    let Some(bars) = sym.bars_for(SRC_TF_MIN) else {
        return Vec::new();
    };
    let f = Frame {
        b: bars,
        s: side.s(),
    };
    let nb = bars.ok.len();
    let (step, first) = if cfg.lx == 2 {
        (WEEK_BARS, cfg.lw * DAY_BARS)
    } else {
        (DAY_BARS, 0)
    };
    let mut out = Vec::new();
    let mut a = first;
    while a + step < nb {
        let born_bar = a + step;
        let found = if cfg.lx == 3 {
            profile_poc(sym, &f, a, born_bar)
        } else {
            range_extreme(&f, a, born_bar)
        };
        if let Some((price, first_bar)) = found {
            let life_end = (born_bar + step).min(nb);
            let end_bar = (born_bar..life_end).find(|&b| bars.ok[b] && f.cl(b) > price);
            let born = (born_bar * SRC_TF_MIN) as u32;
            out.push(Level {
                born,
                end: end_bar.map_or(((born_bar + step) * SRC_TF_MIN) as u32, |b| {
                    ((b + 1) * SRC_TF_MIN) as u32
                }),
                segs: vec![(born, price.abs())],
                first_bar: first_bar as u32,
                mem: Vec::new(),
                src: cfg.lx,
            });
        }
        a += step;
    }
    out
}

/// `lx = 1, 2`: цена уровня периода — максимум `h` (лонг) / минимум `l` (шорт) баров `a..z` (в раме стороны — максимум `hi`) и первый бар с ним;
/// любой бар периода без данных — `None`.
fn range_extreme(f: &Frame, a: usize, z: usize) -> Option<(i64, usize)> {
    if !(a..z).all(|i| f.b.ok[i]) {
        return None;
    }
    let price = (a..z).map(|i| f.hi(i)).max()?;
    let first = (a..z).find(|&i| f.hi(i) == price)?;
    Some((price, first))
}

/// `lx = 3`: профиль объёма минуток баров `a..z` (одни сутки): `tp = (h + l + c) / 3`, корзины шириной 0,10 % средней `tp` суток (сетка корзин от нуля
/// цены: корзина — `tp / ширина`, целочисленно), вес — оборот `qv`. Цена — центр корзины с наибольшим оборотом (ничья — нижняя), в раме стороны;
/// бар — час минуты с наибольшим оборотом в корзине (ничья — ранняя минута). Меньше `PROFILE_MIN_MINUTES` минут с данными или нулевой оборот — `None`.
#[allow(clippy::needless_range_loop)] // m индексирует сразу несколько рядов монеты
fn profile_poc(sym: &Sym, f: &Frame, a: usize, z: usize) -> Option<(i64, usize)> {
    let (m0, m1) = (a * SRC_TF_MIN, z * SRC_TF_MIN);
    let mut mins: Vec<(i64, i64, usize)> = Vec::with_capacity(m1 - m0);
    let mut sum_tp: i128 = 0;
    for m in m0..m1 {
        if sym.o[m] == 0 {
            continue;
        }
        let tp = (sym.h[m] + sym.l[m] + sym.c[m]) / 3;
        sum_tp += i128::from(tp);
        mins.push((tp, sym.qv[m], m));
    }
    if mins.len() < PROFILE_MIN_MINUTES {
        return None;
    }
    let mean = (sum_tp / mins.len() as i128) as i64;
    let w = (mean / PROFILE_BIN_DIV).max(1);
    let mut cells: Vec<(i64, i64, usize)> = mins
        .iter()
        .map(|&(tp, qv, m)| (tp.div_euclid(w), qv, m))
        .collect();
    cells.sort_unstable_by_key(|&(bin, _, m)| (bin, m));
    let mut best: Option<(i128, i64, usize)> = None;
    for grp in cells.chunk_by(|x, y| x.0 == y.0) {
        let total: i128 = grp.iter().map(|x| i128::from(x.1)).sum();
        if best.is_none_or(|(t, _, _)| total > t) {
            let mut peak = grp[0];
            for &x in grp {
                if x.1 > peak.1 {
                    peak = x;
                }
            }
            best = Some((total, grp[0].0, peak.2));
        }
    }
    let (total, bin, peak_min) = best?;
    if total <= 0 {
        return None;
    }
    let poc = bin * w + w / 2;
    Some((f.s * poc, peak_min / SRC_TF_MIN))
}
