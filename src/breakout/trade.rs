//! Выход одной сделки по минуткам (П-42 §5.1). Исход зависит только от траектории цены и параметров выхода.
//!
//! Длинная рама: для шорта цены с обратным знаком, потолок/пол — один код. Порядок внутри минутки (худший —
//! основной): в минутке входа сначала стоп; дальше стоп/трейл/безубыток — по состоянию на конец прошлой минутки
//! (гэп через стоп — по открытию минутки); затем тейки (мейкер, только строго через цену); если в этой минутке
//! исполнился первый тейк и безубыток включён, а минимум дошёл до входа — остаток уходит по безубытку; затем
//! обновляется трейл (действует со следующей минутки). Любой стоп закрывает весь остаток и снимает тейки.
//! Тейки и трейл считаются от NATR выхода на закрытии сигнальной минутки; цена входа — открытие `t+1` плюс
//! проскальзывание; цены без округления до тика (тик-сайз не загружался).

use super::data::{Sym, N_MIN, SCALE};
use super::params::ExitCfg;
use super::Side;

/// Размер позиции $500 в миллионных долях доллара.
pub const NOTIONAL_U: i64 = 500_000_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExitKind {
    HardStop,
    BreakEven,
    Trail,
    Forced,
}

impl ExitKind {
    pub fn name(self) -> &'static str {
        match self {
            ExitKind::HardStop => "stop",
            ExitKind::BreakEven => "be",
            ExitKind::Trail => "trail",
            ExitKind::Forced => "forced",
        }
    }
}

#[derive(Debug, Clone)]
pub struct Leg {
    pub min: u32,
    /// Настоящая цена исполнения (не рама).
    pub px: i64,
    pub u: i64,
    pub maker: bool,
}

#[derive(Debug, Clone)]
pub struct TradeOut {
    pub entry_min: u32,
    pub entry_px: i64,
    pub legs: Vec<Leg>,
    pub exit_min: u32,
    pub kind: ExitKind,
    /// Выход гэпом по открытию минутки.
    pub at_open: bool,
    pub takes: u32,
}

fn frac(x: i64, num: i64, den: i64) -> i64 {
    (i128::from(x) * i128::from(num) / i128::from(den)) as i64
}

pub fn simulate(
    sym: &Sym,
    side: Side,
    t: usize,
    natr_e8: i64,
    ex: &ExitCfg,
    mut trace: Option<&mut Vec<String>>,
) -> Option<TradeOut> {
    let s = side.s();
    let me = t + 1;
    if me >= N_MIN || sym.o[me] == 0 {
        return None;
    }
    let fr = |m: usize| -> (i64, i64, i64, i64) {
        if s > 0 {
            (sym.o[m], sym.h[m], sym.l[m], sym.c[m])
        } else {
            (-sym.o[m], -sym.l[m], -sym.h[m], -sym.c[m])
        }
    };
    let (o0, _, _, _) = fr(me);
    let e = o0 + frac(o0.abs(), ex.slip_bps, 10_000);
    let ea = e.abs();
    let nat = |x_m: i64| -> i64 {
        (i128::from(ea) * i128::from(natr_e8) * i128::from(x_m) / (i128::from(SCALE) * 1000)) as i64
    };
    let hard = e - frac(ea, ex.s_pm, 100_000);
    let n = ex.n_tp as i64;
    let tps: Vec<i64> = (0..n)
        .map(|i| e + nat(ex.t_low_m + (ex.t_high_m - ex.t_low_m) * i / (n - 1)))
        .collect();
    let lot = NOTIONAL_U * ex.q_pct / (100 * n);
    let a_dist = nat(ex.a_m);
    let r_dist = nat(ex.r_m);
    let tracing = trace.is_some();
    let mut log = |line: String| {
        if let Some(v) = trace.as_deref_mut() {
            v.push(line);
        }
    };
    if tracing {
        log(format!(
        "вход: сигнал t={t}, исполнение m={me}, open={} → цена входа {} (проскальзывание {} bp), NATR_exit={} (e8), стоп {}, тейки {:?}, лот {} мкУЕ, трейл A={a_dist} R={r_dist} (расстояния в ценах)",
        s * o0,
        s * e,
        ex.slip_bps,
        natr_e8,
        s * hard,
        tps.iter().map(|x| s * x).collect::<Vec<_>>(),
        lot
    ));
    }

    let mut rem = NOTIONAL_U;
    let mut legs: Vec<Leg> = Vec::new();
    let mut stop_base = hard;
    let mut be_on = false;
    let mut armed = false;
    let mut trail = i64::MIN;
    let mut maxh = i64::MIN;
    let mut next_tp = 0usize;
    let mut last_valid = me;
    let mut exit: Option<(usize, i64, ExitKind, bool)> = None;
    for m in me..N_MIN {
        if sym.o[m] == 0 {
            if tracing {
                log(format!("m={m}: дыра"));
            }
            continue;
        }
        last_valid = m;
        let (o, h, l, c) = fr(m);
        let trail_lvl = if armed { trail } else { i64::MIN };
        let sp = stop_base.max(trail_lvl);
        let sp_kind = if trail_lvl > stop_base {
            ExitKind::Trail
        } else if be_on {
            ExitKind::BreakEven
        } else {
            ExitKind::HardStop
        };
        let mut first_take_now = false;
        let stop_check = |exit: &mut Option<(usize, i64, ExitKind, bool)>| {
            if o <= sp {
                *exit = Some((m, o, sp_kind, true));
            } else if l <= sp {
                *exit = Some((m, sp, sp_kind, false));
            }
        };
        if !ex.best_order {
            stop_check(&mut exit);
        }
        if exit.is_none() {
            while next_tp < tps.len() && h > tps[next_tp] {
                legs.push(Leg {
                    min: m as u32,
                    px: s * tps[next_tp],
                    u: lot,
                    maker: true,
                });
                rem -= lot;
                if next_tp == 0 && ex.be {
                    be_on = true;
                    stop_base = stop_base.max(e);
                    first_take_now = true;
                }
                next_tp += 1;
            }
            if ex.best_order {
                stop_check(&mut exit);
            } else if first_take_now && l <= e {
                exit = Some((m, e, ExitKind::BreakEven, false));
            }
        }
        if tracing {
            log(format!(
            "m={m}: o={} h={} l={} c={} | стоп={} трейл={} взведён={armed} тейков={next_tp} безубыток={be_on}{}",
            s * o,
            s * if s > 0 { h } else { l },
            s * if s > 0 { l } else { h },
            s * c,
            s * sp,
            if trail_lvl == i64::MIN { "—".to_string() } else { (s * trail_lvl).to_string() },
            match &exit {
                Some((_, px, k, ao)) => format!(" → ВЫХОД {} по {} (гэп по открытию: {ao})", k.name(), s * px),
                None => String::new(),
            }
            ));
        }
        if exit.is_some() {
            break;
        }
        if h > maxh {
            maxh = h;
        }
        if !armed && maxh - e >= a_dist {
            armed = true;
        }
        if armed {
            trail = trail.max(maxh - r_dist);
        }
    }
    let (xm, xpx, kind, at_open) = match exit {
        Some(x) => x,
        None => {
            let (_, _, _, c) = fr(last_valid);
            (last_valid, c, ExitKind::Forced, false)
        }
    };
    // стоп — тейкер: цена хуже на проскальзывание (в длинной раме — ниже)
    let px_frame = xpx - frac(xpx.abs(), ex.slip_bps, 10_000);
    legs.push(Leg {
        min: xm as u32,
        px: s * px_frame,
        u: rem,
        maker: false,
    });
    if tracing {
        log(format!(
            "выход: m={xm}, {}, цена {} (после проскальзывания), остаток {rem} мкУЕ",
            kind.name(),
            s * px_frame
        ));
    }
    Some(TradeOut {
        entry_min: me as u32,
        entry_px: s * e,
        legs,
        exit_min: xm as u32,
        kind,
        at_open,
        takes: next_tp as u32,
    })
}
