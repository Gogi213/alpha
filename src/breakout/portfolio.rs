//! Портфельный проход одной стороны (П-42 §5.1): сигналы всех монет по минутам → потолок позиций P, одна
//! позиция на монету, одна сделка на уровень, паузы по монете и общая; исход сделки считает `trade::simulate`
//! в момент принятия сигнала (он не зависит от других монет), закрытия обрабатываются по времени выхода.
//! Детерминирован: порядок сигналов — (минута, расстояние до уровня, символ по алфавиту, уровень).
//!
//! Уточнения: «стоп» для пауз — стоп-лосс от входа (`HardStop`; безубыток и трейл стопами не считаются, любой
//! другой выход обнуляет серию подряд); пауза по монете `c` — от минуты выхода; общий счётчик стопов
//! обнуляется при срабатывании паузы.

use super::data::{Sym, COUNT_DAYS, HALF_DAYS, START_DAY};
use super::params::{ExitCfg, PortCfg};
use super::signals::Sig;
use super::trade::{simulate, ExitKind, TradeOut, NOTIONAL_U};
use super::Side;
use crate::lob::costs::leg_fee_bps;
use std::collections::{HashSet, VecDeque};

#[derive(Debug, Clone)]
pub struct TradeRec {
    pub sym: u16,
    pub level: u32,
    pub sig_min: u32,
    pub entry_min: u32,
    pub exit_min: u32,
    pub entry_px: i64,
    pub kind: ExitKind,
    pub takes: u32,
    pub gross: f64,
    pub fees: f64,
    pub funding: f64,
    pub net: f64,
}

#[derive(Debug, Clone)]
pub struct PortResult {
    pub trades: Vec<TradeRec>,
    /// $ за сутки, 60 суток с 15.09; сделка, пересекающая границу A/B, целиком в половине входа.
    pub daily: [f64; COUNT_DAYS],
    /// $ по монете и суткам (для проверки «без 2 лучших монет» и деления до/после первого дня ≥ $100 млн).
    pub coinday: std::collections::BTreeMap<(u16, usize), f64>,
    /// Отказы по причинам: уровень израсходован, монета занята, пауза монеты, общая пауза, потолок P, нет исхода.
    pub rej: [u64; 6],
}

pub struct Settled {
    pub gross: f64,
    pub fees: f64,
    pub funding: f64,
    pub daily: Vec<(usize, f64)>,
}

/// Деньги сделки: валовая, комиссии, фандинг, и суточные приращения (переоценка на 00:00 UTC по закрытию
/// последней минутки суток).
pub fn settle(sym: &Sym, side: Side, tr: &TradeOut) -> Settled {
    let s = side.s() as f64;
    let e = tr.entry_px as f64;
    let n0 = NOTIONAL_U as f64 / 1e6;
    let mut flows: Vec<(usize, f64)> = Vec::new();
    let entry_fee = n0 * leg_fee_bps(true) / 1e4;
    flows.push((tr.entry_min as usize, -entry_fee));
    let mut gross = 0.0;
    let mut fees = entry_fee;
    for leg in &tr.legs {
        let u = leg.u as f64 / 1e6;
        let g = u * s * (leg.px - tr.entry_px) as f64 / e;
        let fee = u * (leg.px as f64 / e) * leg_fee_bps(!leg.maker) / 1e4;
        gross += g;
        fees += fee;
        flows.push((leg.min as usize, g - fee));
    }
    // фандинг: позиция открыта строго через момент выплаты (вход раньше минуты выплаты; выход после её начала)
    let mut funding = 0.0;
    for f in &sym.fund {
        let fm = f.min as usize;
        let held = fm > tr.entry_min as usize
            && (fm < tr.exit_min as usize || (fm == tr.exit_min as usize && !tr.at_open));
        if !held {
            continue;
        }
        let closed: i64 = tr
            .legs
            .iter()
            .filter(|l| l.maker && (l.min as usize) < fm)
            .map(|l| l.u)
            .sum();
        let rem = (NOTIONAL_U - closed) as f64 / 1e6;
        let pay = -s * (f.rate_e8 as f64 / 1e8) * rem * (f.mark as f64 / e);
        funding += pay;
        flows.push((fm, pay));
    }
    // суточные приращения
    let g0 = tr.entry_min as usize / 1440;
    let g1 = tr.exit_min as usize / 1440;
    let d_entry = g0 - START_DAY;
    let mut prev = 0.0;
    let mut daily = Vec::new();
    for g in g0..=g1 {
        let b = (g + 1) * 1440 - 1;
        let realized: f64 = flows.iter().filter(|(m, _)| *m <= b).map(|(_, v)| v).sum();
        let unreal = if (tr.exit_min as usize) <= b {
            0.0
        } else {
            let closed: i64 = tr
                .legs
                .iter()
                .filter(|l| l.maker && (l.min as usize) <= b)
                .map(|l| l.u)
                .sum();
            let rem = (NOTIONAL_U - closed) as f64 / 1e6;
            let mut mk = tr.entry_px;
            let mut m = b;
            while m > tr.entry_min as usize {
                if sym.o[m] != 0 {
                    mk = sym.c[m];
                    break;
                }
                m -= 1;
            }
            rem * s * (mk - tr.entry_px) as f64 / e
        };
        let cum = realized + unreal;
        let d = g - START_DAY;
        let attr = if d_entry < HALF_DAYS {
            d.min(HALF_DAYS - 1)
        } else {
            d
        };
        daily.push((attr, cum - prev));
        prev = cum;
    }
    Settled {
        gross,
        fees,
        funding,
        daily,
    }
}

struct Open {
    exit_min: usize,
    sym: usize,
    kind: ExitKind,
}

pub fn run(syms: &[Sym], sigs: &[Sig], side: Side, ex: &ExitCfg, pc: &PortCfg) -> PortResult {
    let ns = syms.len();
    let mut res = PortResult {
        trades: Vec::new(),
        daily: [0.0; COUNT_DAYS],
        coinday: std::collections::BTreeMap::new(),
        rej: [0; 6],
    };
    let mut busy = vec![false; ns];
    let mut cool_until = vec![0usize; ns];
    let mut cpause_until = vec![0usize; ns];
    let mut streak = vec![0usize; ns];
    let mut gstops: VecDeque<usize> = VecDeque::new();
    let mut gpause_until = 0usize;
    let mut consumed: HashSet<u64> = HashSet::new();
    let mut open: Vec<Open> = Vec::new();
    for sg in sigs {
        let t = sg.t as usize;
        while !open.is_empty() && open[0].exit_min <= t {
            let o = open.remove(0);
            busy[o.sym] = false;
            cool_until[o.sym] = o.exit_min + pc.cool_min;
            if o.kind == ExitKind::HardStop {
                streak[o.sym] += 1;
                if streak[o.sym] >= pc.m_stops {
                    cpause_until[o.sym] = o.exit_min + pc.mm_min;
                    streak[o.sym] = 0;
                }
                gstops.push_back(o.exit_min);
                while let Some(&f) = gstops.front() {
                    if o.exit_min >= f + pc.w_min {
                        gstops.pop_front();
                    } else {
                        break;
                    }
                }
                if gstops.len() >= pc.s_glob {
                    if pc.ww_min > 0 {
                        gpause_until = o.exit_min + pc.ww_min;
                    }
                    gstops.clear();
                }
            } else {
                streak[o.sym] = 0;
            }
        }
        let si = sg.sym as usize;
        let key = (u64::from(sg.sym) << 32) | u64::from(sg.level);
        if consumed.contains(&key) {
            res.rej[0] += 1;
            continue;
        }
        if busy[si] {
            res.rej[1] += 1;
            continue;
        }
        if t < cool_until[si] || t < cpause_until[si] {
            res.rej[2] += 1;
            continue;
        }
        if t < gpause_until {
            res.rej[3] += 1;
            continue;
        }
        if open.len() >= pc.p {
            res.rej[4] += 1;
            continue;
        }
        let sym = &syms[si];
        let natr = sym.natr_for(ex.natr_p).map_or(-1, |v| i64::from(v[t]));
        let tr = if natr > 0 {
            simulate(sym, side, t, natr, ex, None)
        } else {
            None
        };
        let Some(tr) = tr else {
            res.rej[5] += 1;
            continue;
        };
        consumed.insert(key);
        busy[si] = true;
        open.push(Open {
            exit_min: tr.exit_min as usize,
            sym: si,
            kind: tr.kind,
        });
        open.sort_by_key(|o| o.exit_min);
        let st = settle(sym, side, &tr);
        for (d, v) in &st.daily {
            res.daily[*d] += v;
            *res.coinday.entry((sg.sym, *d)).or_insert(0.0) += v;
        }
        res.trades.push(TradeRec {
            sym: sg.sym,
            level: sg.level,
            sig_min: sg.t,
            entry_min: tr.entry_min,
            exit_min: tr.exit_min,
            entry_px: tr.entry_px,
            kind: tr.kind,
            takes: tr.takes,
            gross: st.gross,
            fees: st.fees,
            funding: st.funding,
            net: st.gross - st.fees + st.funding,
        });
    }
    res
}
