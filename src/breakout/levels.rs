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

#[derive(Debug, Clone)]
pub struct Level {
    /// Первая минута, когда уровень известен (после закрытия бара, давшего N-й отскок).
    pub born: u32,
    /// Первая минута, когда уровня уже нет (`u32::MAX` — до конца данных).
    pub end: u32,
    /// Версии цены: (с какой минуты, цена уровня); цена меняется, когда присоединяется пивот или выпадает член.
    pub segs: Vec<(u32, i64)>,
    pub first_bar: u32,
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
            for lv in live.iter_mut() {
                let mut cand = prices(&lv.members);
                cand.push(pv.p);
                let (ok, mean) = group_ok(&cand, cfg.tau_pm);
                if ok {
                    lv.members.push(pv);
                    levels[lv.id].segs.push((now as u32, mean.abs()));
                    joined = true;
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
            let dead = (first_bar as usize + 1..=b).any(|bb| bars.ok[bb] && f.cl(bb) > mean);
            if !dead {
                levels.push(Level {
                    born: now as u32,
                    end: u32::MAX,
                    segs: vec![(now as u32, mean.abs())],
                    first_bar,
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
