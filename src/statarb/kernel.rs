//! Онлайн-ядро стат-арбитража (TK-479): скользящая оценка соотношения ног, спред, z-скор, сигнал,
//! позиция и net-PnL по ногам. O(1) на минуту: скользящие суммы относительно якоря плюс точный
//! пересчёт окна раз в `refresh` минут (амортизированно O(1)).
//!
//! Нога Y против ноги X (`K` компонент с весами). Спред `y − β·x − α` в логарифмах закрытий;
//! `β` — скользящая МНК (`BetaMode::Ols`) либо фиксированная (кросс-биржа: 1). Размер: номинал
//! ноги Y = 1, номинал компоненты X = `β·w_i` (замораживается при входе), PnL в штуках, не в
//! логарифмах; результат — bps номинала Y.

use crate::lob::costs::{leg_fee_bps, spot_leg_fee_bps};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LegKind {
    Perp,
    Spot,
}

impl LegKind {
    /// Комиссия ноги после возврата, bps (В-88).
    pub fn fee_bps(self, taker: bool) -> f64 {
        match self {
            LegKind::Perp => leg_fee_bps(taker),
            LegKind::Spot => spot_leg_fee_bps(taker),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum BetaMode {
    Ols,
    Fixed(f64),
}

/// Параметры прогона. Значений по умолчанию нет: пороги и окна — параметры бэктеста.
#[derive(Debug, Clone, Copy)]
pub struct Params {
    pub window: usize,
    /// Минимум совместных наблюдений в окне для сигнала (не меньше 2).
    pub min_obs: usize,
    pub beta: BetaMode,
    pub entry_z: f64,
    /// Выход, когда `dir·z ≥ −exit_z` (возврат к среднему).
    pub exit_z: f64,
    /// Стоп, когда `dir·z ≤ −stop_z`; `INFINITY` выключает.
    pub stop_z: f64,
    /// Выход по времени в минутах от входа; 0 выключает.
    pub max_hold: usize,
    /// Точный пересчёт окна раз в столько минут; 0 = `window`.
    pub refresh: usize,
    pub y_kind: LegKind,
    pub x_kind: LegKind,
    pub taker_in: bool,
    pub taker_out: bool,
}

/// Вход для одной пары: ряды выровнены по общей сетке, индекс `i` ↔ минута `first_min + i`.
pub struct PairInput<'a> {
    pub first_min: i64,
    /// ln(close) ноги Y и ноги X (для корзины — геометрический индекс `Σ w·ln P`).
    pub ly: &'a [f64],
    pub lx: &'a [f64],
    /// Open ноги Y и компонент X (цены исполнения).
    pub oy: &'a [f64],
    pub ox: &'a [&'a [f64]],
    pub wx: &'a [f64],
    /// Фандинг ноги Y и компонент X: `(минута сетки, ставка)` по возрастанию. Пусто — нет фандинга.
    pub fund_y: &'a [(i64, f64)],
    pub fund_x: &'a [&'a [(i64, f64)]],
}

impl PairInput<'_> {
    pub fn check(&self) -> Result<(), &'static str> {
        let n = self.ly.len();
        if self.lx.len() != n || self.oy.len() != n {
            return Err("ряды разной длины");
        }
        if self.ox.is_empty() || self.ox.len() != self.wx.len() {
            return Err("компоненты X и веса не совпадают");
        }
        if self.ox.iter().any(|o| o.len() != n) {
            return Err("open компоненты X другой длины");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Trade {
    pub dir: i8,
    pub sig_in: i64,
    pub fill_in: i64,
    pub sig_out: i64,
    pub fill_out: i64,
    pub gross_bps: f64,
    pub fee_bps: f64,
    pub funding_bps: f64,
    pub net_bps: f64,
}

pub trait Sink {
    fn trade(&mut self, t: &Trade);
}

impl Sink for () {
    fn trade(&mut self, _: &Trade) {}
}

impl Sink for Vec<Trade> {
    fn trade(&mut self, t: &Trade) {
        self.push(*t);
    }
}

/// Счётчики правила пропусков и сделок: видны в каждом прогоне, не выводятся задним числом.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct Counters {
    /// Минуты спана, где есть свеча у обеих ног.
    pub obs_minutes: u64,
    /// Минуты спана, где нет свечи хотя бы у одной ноги.
    pub gap_minutes: u64,
    pub entries: u64,
    /// Сигнал входа, а в следующую минуту нет open у какой-то ноги.
    pub entry_deferred: u64,
    /// Минуты ожидания выхода из-за нет open.
    pub exit_deferred: u64,
    pub trades: u64,
    pub minutes_in_pos: u64,
    /// Позиция, оставшаяся открытой на конце данных (не закрывается и не считается).
    pub open_at_end: u64,
    pub sum_net_bps: f64,
}

#[derive(Clone, Copy, Default)]
pub(crate) struct Sums {
    anchored: bool,
    ax: f64,
    ay: f64,
    n: u32,
    sx: f64,
    sy: f64,
    sxx: f64,
    sxy: f64,
    syy: f64,
}

impl Sums {
    #[inline]
    fn add(&mut self, x: f64, y: f64) {
        if !self.anchored {
            self.ax = x;
            self.ay = y;
            self.anchored = true;
        }
        let (x, y) = (x - self.ax, y - self.ay);
        self.n += 1;
        self.sx += x;
        self.sy += y;
        self.sxx += x * x;
        self.sxy += x * y;
        self.syy += y * y;
    }

    #[inline]
    fn sub(&mut self, x: f64, y: f64) {
        let (x, y) = (x - self.ax, y - self.ay);
        self.n -= 1;
        self.sx -= x;
        self.sy -= y;
        self.sxx -= x * x;
        self.sxy -= x * y;
        self.syy -= y * y;
    }

    /// Точный пересчёт по окну `[lo, hi]` с новым якорем.
    fn rebuild(lx: &[f64], ly: &[f64], lo: usize, hi: usize) -> Sums {
        let mut s = Sums::default();
        for i in lo..=hi {
            if lx[i].is_finite() && ly[i].is_finite() {
                s.add(lx[i], ly[i]);
            }
        }
        s
    }
}

/// `(β, z)` на текущей точке по суммам окна; `NaN` — оценки нет.
#[inline]
pub(crate) fn beta_z(s: &Sums, mode: BetaMode, x: f64, y: f64, min_obs: usize) -> (f64, f64) {
    if (s.n as usize) < min_obs.max(2) {
        return (f64::NAN, f64::NAN);
    }
    let n = f64::from(s.n);
    let cxx = s.sxx - s.sx * s.sx / n;
    let cxy = s.sxy - s.sx * s.sy / n;
    let cyy = s.syy - s.sy * s.sy / n;
    let beta = match mode {
        BetaMode::Ols => {
            if cxx <= 0.0 {
                return (f64::NAN, f64::NAN);
            }
            cxy / cxx
        }
        BetaMode::Fixed(b) => b,
    };
    let ssr = cyy - 2.0 * beta * cxy + beta * beta * cxx;
    let scale = cyy + beta * beta * cxx;
    if !(ssr > 8.0 * f64::EPSILON * scale) {
        return (beta, f64::NAN);
    }
    let sigma = (ssr / n).sqrt();
    let r = (y - s.ay) - beta * (x - s.ax) - (s.sy - beta * s.sx) / n;
    (beta, r / sigma)
}

#[derive(Clone, Copy)]
enum Pend {
    None,
    Enter(i8, f64, usize),
    Exit(usize),
}

/// Цена для выплаты фандинга: open минуты, а если там нет сделок — последний известный open.
fn price_at(open: &[f64], t: usize) -> f64 {
    let mut i = t;
    loop {
        if open[i].is_finite() {
            return open[i];
        }
        i -= 1; // позиция открыта по конечному open, поиск назад кончается на входе
    }
}

/// Ставки с минутой ровно `ft`; курсор проходит и более ранние (они не относятся к держанию).
#[inline]
fn rates_at(ev: &[(i64, f64)], cur: &mut usize, ft: i64) -> f64 {
    let mut r = 0.0;
    while *cur < ev.len() && ev[*cur].0 <= ft {
        if ev[*cur].0 == ft {
            r += ev[*cur].1;
        }
        *cur += 1;
    }
    r
}

/// Прогон одной пары по всему спану. Память на минуту не выделяется: векторы размера `K` — до цикла.
pub fn run_pair<S: Sink>(inp: &PairInput, p: &Params, sink: &mut S) -> Counters {
    let n = inp.ly.len();
    let k = inp.ox.len();
    let refresh = if p.refresh == 0 { p.window } else { p.refresh };
    let mut c = Counters::default();
    let mut s = Sums::default();
    let mut pend = Pend::None;
    // Позиция: направление, β, шаг входа (сигнал, исполнение), количества и цены входа по ногам (0 = Y).
    let mut dir = 0i8;
    let mut sig_in = 0usize;
    let mut fill_in = 0usize;
    let mut qty = vec![0.0f64; k + 1];
    let mut pin = vec![0.0f64; k + 1];
    let mut fund = 0.0f64;
    let mut cur_y = 0usize;
    let mut cur_x = vec![0usize; k];
    let no_fund: &[(i64, f64)] = &[];
    for t in 0..n {
        let ft = inp.first_min + t as i64;
        let opens_ok = inp.oy[t].is_finite() && inp.ox.iter().all(|o| o[t].is_finite());
        let mut filled_now = false;
        match pend {
            Pend::Enter(d, beta, sig) => {
                pend = Pend::None;
                if opens_ok {
                    dir = d;
                    sig_in = sig;
                    fill_in = t;
                    qty[0] = f64::from(d) / inp.oy[t];
                    pin[0] = inp.oy[t];
                    for i in 0..k {
                        pin[i + 1] = inp.ox[i][t];
                        qty[i + 1] = -f64::from(d) * beta * inp.wx[i] / inp.ox[i][t];
                    }
                    fund = 0.0;
                    c.entries += 1;
                    filled_now = true;
                } else {
                    c.entry_deferred += 1;
                }
            }
            Pend::Exit(sig) => {
                if opens_ok {
                    let mut gross = qty[0] * (inp.oy[t] - pin[0]);
                    let mut fee = qty[0].abs()
                        * (pin[0] * p.y_kind.fee_bps(p.taker_in)
                            + inp.oy[t] * p.y_kind.fee_bps(p.taker_out));
                    for i in 0..k {
                        gross += qty[i + 1] * (inp.ox[i][t] - pin[i + 1]);
                        fee += qty[i + 1].abs()
                            * (pin[i + 1] * p.x_kind.fee_bps(p.taker_in)
                                + inp.ox[i][t] * p.x_kind.fee_bps(p.taker_out));
                    }
                    let (gross_bps, funding_bps) = (gross * 1e4, fund * 1e4);
                    let net = gross_bps - fee + funding_bps;
                    sink.trade(&Trade {
                        dir,
                        sig_in: inp.first_min + sig_in as i64,
                        fill_in: inp.first_min + fill_in as i64,
                        sig_out: inp.first_min + sig as i64,
                        fill_out: ft,
                        gross_bps,
                        fee_bps: fee,
                        funding_bps,
                        net_bps: net,
                    });
                    c.trades += 1;
                    c.sum_net_bps += net;
                    dir = 0;
                    pend = Pend::None;
                } else {
                    c.exit_deferred += 1;
                }
            }
            Pend::None => {}
        }
        // Фандинг: позиция, державшаяся строго через момент выплаты.
        let hold = dir != 0 && !filled_now;
        let ry = rates_at(
            if inp.fund_y.is_empty() {
                no_fund
            } else {
                inp.fund_y
            },
            &mut cur_y,
            ft,
        );
        if hold && ry != 0.0 {
            fund -= qty[0] * price_at(inp.oy, t) * ry;
        }
        for i in 0..k {
            let ev = inp.fund_x.get(i).copied().unwrap_or(no_fund);
            let rx = rates_at(ev, &mut cur_x[i], ft);
            if hold && rx != 0.0 {
                fund -= qty[i + 1] * price_at(inp.ox[i], t) * rx;
            }
        }
        if dir != 0 {
            c.minutes_in_pos += 1;
        }
        // Окно: добавить минуту t, убрать t−W, раз в `refresh` — точный пересчёт.
        let (xt, yt) = (inp.lx[t], inp.ly[t]);
        let obs = xt.is_finite() && yt.is_finite();
        if obs {
            s.add(xt, yt);
            c.obs_minutes += 1;
        } else {
            c.gap_minutes += 1;
        }
        if t >= p.window {
            let j = t - p.window;
            if inp.lx[j].is_finite() && inp.ly[j].is_finite() {
                s.sub(inp.lx[j], inp.ly[j]);
            }
        }
        if (t + 1) % refresh == 0 {
            let lo = (t + 1).saturating_sub(p.window);
            s = Sums::rebuild(inp.lx, inp.ly, lo, t);
        }
        let (beta, z) = if obs {
            beta_z(&s, p.beta, xt, yt, p.min_obs)
        } else {
            (f64::NAN, f64::NAN)
        };
        // Сигнал по закрытию минуты t; исполнение — следующая минута.
        if matches!(pend, Pend::None) {
            if dir == 0 {
                if z.is_finite() && beta.is_finite() {
                    if z <= -p.entry_z {
                        pend = Pend::Enter(1, beta, t);
                    } else if z >= p.entry_z {
                        pend = Pend::Enter(-1, beta, t);
                    }
                }
            } else {
                let dz = f64::from(dir) * z;
                let timed = p.max_hold > 0 && t - fill_in >= p.max_hold;
                if (z.is_finite() && (dz >= -p.exit_z || dz <= -p.stop_z)) || timed {
                    pend = Pend::Exit(t);
                }
            }
        }
    }
    if dir != 0 {
        c.open_at_end = 1;
    }
    c
}
