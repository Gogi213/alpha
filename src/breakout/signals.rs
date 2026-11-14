//! Минутный проход по уровням монеты: ложный пробой, зона входа, цвет минутки, пул (П-42 §5.1).
//! Сигнал зависит только от траектории цены и `SigCfg` — не от портфеля, поэтому один список сигналов
//! общий для всех вариантов с тем же `SigCfg` (варианты выхода и портфеля делят его).
//!
//! Сигнал на закрытии минутки `t` (лонг; шорт зеркально): уровень жив и не запрещён, `high_t < уровня`
//! (цена не пересекла уровень в этой минутке), расстояние `(уровень − close_t)/уровень ≤ E` и `≤ D`,
//! минутка зелёная (если включено), пул (NATR, оборот за час/24 ч) проходит.
//! Ложный пробой: `high > уровня` — пересечение; если в течение `r` минут закрытие минутки ушло за уровень
//! глубже `f` % — уровень запрещён навсегда (если запрет включён). `D ≥ E` на всей сетке, поэтому слежение `D`
//! входит в условие, но на сигналы не влияет (в отчёте).

use super::data::{Sym, N_MIN, SCALE, START_DAY, START_MIN};
use super::levels::Level;
use super::params::SigCfg;
use super::Side;

#[derive(Debug, Clone, Copy)]
pub struct Sig {
    pub t: u32,
    pub sym: u16,
    pub level: u32,
    /// Расстояние до уровня в долях ×1e8 (порядок при нескольких сигналах в минуту — по возрастанию).
    pub dist: u32,
}

/// Счётчики по половинам окна: 0 — A, 1 — B.
#[derive(Debug, Default, Clone)]
pub struct SigCtr {
    pub signals: [u64; 2],
    pub levels_crossed: [u64; 2],
    /// Первое пересечение уровня без единого предшествующего сигнала по нему («вошла в зону и пересекла»).
    pub levels_skipped: [u64; 2],
    /// То же, но «был ли сигнал» — по геометрии (зона входа и цвет), без фильтра пула: чистая доля пропусков
    /// «вошла в зону и пересекла в одной минутке».
    pub levels_skipped_geo: [u64; 2],
    pub levels_total: u64,
}

impl SigCtr {
    pub fn add(&mut self, o: &SigCtr) {
        for h in 0..2 {
            self.signals[h] += o.signals[h];
            self.levels_crossed[h] += o.levels_crossed[h];
            self.levels_skipped[h] += o.levels_skipped[h];
            self.levels_skipped_geo[h] += o.levels_skipped_geo[h];
        }
        self.levels_total += o.levels_total;
    }
}

fn half(t: usize) -> usize {
    usize::from(t / 1440 >= START_DAY + super::data::HALF_DAYS)
}

pub fn scan(
    sym: &Sym,
    si: u16,
    levels: &[Level],
    cfg: &SigCfg,
    side: Side,
    out: &mut Vec<Sig>,
    ctr: &mut SigCtr,
) {
    let s = side.s();
    let Some(natr) = sym.natr_for(cfg.natr_p) else {
        return;
    };
    let e_e8 = cfg.e_pm * 1000;
    let track_e8 = cfg.track_pm * 1000;
    let natr_min = cfg.natr_min_pm * 1000;
    for (li, lv) in levels.iter().enumerate() {
        ctr.levels_total += 1;
        let end = (lv.end as usize).min(N_MIN);
        let mut seg = 0;
        let mut price = lv.segs[0].1;
        let mut pend: Option<usize> = None;
        let mut forbidden = false;
        let mut crossed = false;
        let mut had = false;
        let mut had_geo = false;
        for t in lv.born as usize..end {
            while seg + 1 < lv.segs.len() && lv.segs[seg + 1].0 as usize <= t {
                seg += 1;
                price = lv.segs[seg].1;
            }
            if sym.o[t] == 0 {
                continue;
            }
            let lp = s * price;
            let hi = if s > 0 { sym.h[t] } else { -sym.l[t] };
            let cl = s * sym.c[t];
            let op = s * sym.o[t];
            if let Some(tc) = pend {
                if t - tc > cfg.r_min {
                    pend = None;
                }
            }
            if hi > lp {
                if !crossed {
                    crossed = true;
                    if t >= START_MIN {
                        let h = half(t);
                        ctr.levels_crossed[h] += 1;
                        if !had {
                            ctr.levels_skipped[h] += 1;
                        }
                        if !had_geo {
                            ctr.levels_skipped_geo[h] += 1;
                        }
                    }
                }
                if pend.is_none() {
                    pend = Some(t);
                }
            }
            if cfg.ban && pend.is_some() {
                let back = lp - (i128::from(lp.abs()) * i128::from(cfg.f_pm) / 100_000) as i64;
                if cl < back {
                    forbidden = true;
                }
            }
            if t < START_MIN || forbidden || t + 1 >= N_MIN || sym.o[t + 1] == 0 || hi >= lp {
                continue;
            }
            let dist = (i128::from(lp - cl) * i128::from(SCALE) / i128::from(lp.abs())) as i64;
            if dist > e_e8 || dist > track_e8 {
                continue;
            }
            if cfg.color && cl <= op {
                continue;
            }
            had_geo = true;
            let nv = i64::from(natr[t]);
            if nv < 0 || nv < natr_min {
                continue;
            }
            if sym.pq[t + 1] - sym.pq[t + 1 - 60] < cfg.t1h_u {
                continue;
            }
            if cfg.t24_u > 0 && sym.pq[t + 1] - sym.pq[t + 1 - 1440] < cfg.t24_u {
                continue;
            }
            out.push(Sig {
                t: t as u32,
                sym: si,
                level: li as u32,
                dist: dist as u32,
            });
            had = true;
            ctr.signals[half(t)] += 1;
        }
    }
}

/// Порядок обработки внутри минуты (П-42 §5.1): время, расстояние до уровня, символ (индекс = алфавит), уровень.
pub fn sort_sigs(v: &mut [Sig]) {
    v.sort_unstable_by_key(|s| (s.t, s.dist, s.sym, s.level));
}
