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

/// Правило входа и выхода.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Rule {
    /// По z-скору спреда: `entry_z`, `exit_z`, `stop_z`.
    Z,
    /// D3, по уровню ставки ноги Y: трейлинг-среднее `k` последних выплат (выплаты с минутой ≤ закрытию
    /// сигнальной минуты) × ожидаемое удержание `hold_payments` (в выплатах) в bps номинала Y против круга
    /// издержек (`round_trip_cost_bps`). Вход, когда доход выше круга; сторона — та, где нога Y получает
    /// фандинг (ставка > 0 → шорт Y). Выход, когда доход позиции перестал быть выше круга. `entry_z`, `exit_z`
    /// и `stop_z` не используются; `max_hold` действует. Нужны `k ≥ 1` и фандинг ноги Y (`fund_y`).
    Funding { k: usize, hold_payments: usize },
}

/// Причина выхода (TK-493 w3, F-02): правило возврата по z (`Rule`), стоп по z (`Stop`) либо срок `max_hold` (`Timeout`).
/// Для `Rule::Funding` стопа нет: выход по уровню ставки — `Rule`. Позиция, не закрытая к концу данных, сделкой не
/// становится и причины не имеет (`Counters::open_at_end`, в сводках — `open_end`). Решается на сигнальной минуте:
/// стоп важнее возврата, возврат важнее срока.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExitReason {
    Rule,
    Stop,
    Timeout,
}

impl ExitReason {
    pub fn as_str(self) -> &'static str {
        match self {
            ExitReason::Rule => "rule",
            ExitReason::Stop => "stop",
            ExitReason::Timeout => "timeout",
        }
    }
}

/// Круг издержек, bps номинала Y: комиссии входа и выхода по обеим ногам при цене выхода = цене входа;
/// номинал X = `|β|·Σw` (замораживается при входе). Ставки — только `lob::costs` через `LegKind::fee_bps`.
pub fn round_trip_cost_bps(p: &Params, beta: f64, wsum: f64) -> f64 {
    p.y_kind.fee_bps(p.taker_in)
        + p.y_kind.fee_bps(p.taker_out)
        + beta.abs() * wsum * (p.x_kind.fee_bps(p.taker_in) + p.x_kind.fee_bps(p.taker_out))
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
    /// Задержка входа ног X после исполнения ноги Y, минут (C1); 0 — обе ноги на одном open `t+1`. Нога X идёт
    /// по open минуты `fill_in + delay_x`, а если там нет open — ждёт ближайшей минуты с open у всех компонент
    /// (`Counters::x_deferred`). Позиция (выход, `max_hold`) считается набранной с входа ноги X; фандинг
    /// ноги Y платится с её собственного входа. На выходе обе ноги по одному open, как раньше.
    pub delay_x: usize,
    pub rule: Rule,
    /// F-02 (TK-493 w3, по умолчанию выкл. — иначе повторный вход в ту же сторону идёт сразу): после выхода по стопу или
    /// по сроку вход в ту же сторону разрешён, только когда z вернулся внутрь полосы входа (`|z| < entry_z`; нет данных —
    /// нет возврата). Другая сторона разрешена сразу. Выход по правилу ничего не блокирует. Только для `Rule::Z`:
    /// при `Rule::Funding` полосы z нет, флаг не действует (в `statarb run` / `bench` такое сочетание — ошибка).
    pub rearm: bool,
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
    /// Исполнение ноги Y.
    pub fill_in: i64,
    /// Исполнение ноги X: `fill_in + delay_x`, а при пропуске open — позже; без задержки равно `fill_in`.
    pub fill_x: i64,
    pub sig_out: i64,
    pub fill_out: i64,
    pub gross_bps: f64,
    pub fee_bps: f64,
    pub funding_bps: f64,
    pub net_bps: f64,
    /// β на сигнале входа, по которой заморожены номиналы ног X (F-03): круг издержек и валовый номинал сделки —
    /// `1 + |β|·Σw`. Только для новых выводов; хеши и старые столбцы его не используют.
    pub beta: f64,
    /// Причина выхода (F-02); только для новых выводов.
    pub reason: ExitReason,
}

/// Позиция, оставшаяся открытой на конце данных (TK-493, `--mtm-open`): состояние ядра на последней минуте.
/// Минуты — абсолютные (индекс сетки панелей), как в `Trade`.
#[derive(Debug, Clone, Copy)]
pub struct OpenPos<'a> {
    pub dir: i8,
    pub sig_in: i64,
    /// Исполнение ноги Y.
    pub fill_in: i64,
    /// Исполнение ног X; `None` — нога X ещё не вошла (`delay_x`: срок не наступил или нет open), открыта одна нога Y.
    pub fill_x: Option<i64>,
    /// Количества по ногам (0 = Y, дальше компоненты X) и цены входа; для ног X при `fill_x == None` значения не определены.
    pub qty: &'a [f64],
    pub pin: &'a [f64],
    /// Накопленный фандинг (доля номинала Y, до умножения на 1e4).
    pub fund: f64,
}

pub trait Sink {
    fn trade(&mut self, t: &Trade);
    /// z-скор минуты `t` (`NaN` — оценки нет); по умолчанию не нужен и вычёркивается при мономорфизации.
    #[inline]
    fn z(&mut self, _t: usize, _z: f64) {}
    /// Вызывается один раз после последней минуты, если позиция осталась открытой (`Counters::open_at_end == 1`).
    /// По умолчанию не нужен: бинарные хеши, `Vec<Trade>` и `()` его не принимают.
    #[inline]
    fn open_end(&mut self, _pos: &OpenPos) {}
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
    /// Минуты ожидания входа ноги X после срока `delay_x` из-за нет open у какой-то компоненты.
    pub x_deferred: u64,
    pub trades: u64,
    pub minutes_in_pos: u64,
    /// Позиция, оставшаяся открытой на конце данных (не закрывается и не считается).
    pub open_at_end: u64,
    pub sum_net_bps: f64,
    /// F-01 (TK-493 w3), знаменатель: минуты спана, где окно оценки уже могло набраться (`t + 1 ≥ window`), с пропусками;
    /// разогрев первых `window − 1` минут спана в счётчик не входит (там окно неполное по построению).
    pub eval_minutes: u64,
    /// Из них минуты с определённым z. Доля `z_def_minutes / eval_minutes`.
    pub z_def_minutes: u64,
    /// Минуты спана, где позиция удержана (как в `minutes_in_pos`) и z не определён: правило выхода и стоп по z слепы,
    /// остаётся только `max_hold`. Считается по всему спану, как `minutes_in_pos`.
    pub pos_z_nan_minutes: u64,
    /// Минуты без права входа из-за неполного окна: нет позиции и ордера, у минуты есть свеча, окно уже могло набраться
    /// (`t + 1 ≥ window`), но совместных наблюдений в нём меньше `max(min_obs, 2)` (дыры). Только правило z.
    pub noentry_window_minutes: u64,
    /// F-02: сигналов входа, подавленных `Params::rearm` (z всё ещё за порогом после стопа или срока в ту же сторону).
    pub rearm_blocked: u64,
}

#[derive(Clone, Copy, Default)]
pub(crate) struct Sums {
    anchored: bool,
    ax: f64,
    ay: f64,
    /// Совместных наблюдений в окне.
    pub(crate) n: u32,
    sx: f64,
    sy: f64,
    sxx: f64,
    sxy: f64,
    syy: f64,
}

impl Sums {
    #[inline]
    pub(crate) fn add(&mut self, x: f64, y: f64) {
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
    pub(crate) fn sub(&mut self, x: f64, y: f64) {
        let (x, y) = (x - self.ax, y - self.ay);
        self.n -= 1;
        self.sx -= x;
        self.sy -= y;
        self.sxx -= x * x;
        self.sxy -= x * y;
        self.syy -= y * y;
    }

    /// Точный пересчёт по окну `[lo, hi]` с новым якорем.
    pub(crate) fn rebuild(lx: &[f64], ly: &[f64], lo: usize, hi: usize) -> Sums {
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
    if !matches!(
        ssr.partial_cmp(&(8.0 * f64::EPSILON * scale)),
        Some(std::cmp::Ordering::Greater)
    ) {
        return (beta, f64::NAN);
    }
    let sigma = (ssr / n).sqrt();
    let r = (y - s.ay) - beta * (x - s.ax) - (s.sy - beta * s.sx) / n;
    (beta, r / sigma)
}

/// `x_due` при отсутствии ожидающей ноги X.
const NO_DUE: usize = usize::MAX;

#[derive(Clone, Copy)]
pub(crate) enum Pend {
    None,
    Enter(i8, f64, usize),
    Exit(usize),
}

/// Цена для выплаты фандинга: open минуты, а если там нет сделок — последний известный open.
pub(crate) fn price_at(open: &[f64], t: usize) -> f64 {
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
pub(crate) fn rates_at(ev: &[(i64, f64)], cur: &mut usize, ft: i64) -> f64 {
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
/// Режимы `delay_x > 0` и `Rule::Funding` — отдельные мономорфизации цикла: умолчание (без задержки, правило z)
/// не несёт их состояния в регистрах горячего цикла (замер TK-482: общий цикл был на ~10 % медленнее прежнего).
pub fn run_pair<S: Sink>(inp: &PairInput, p: &Params, sink: &mut S) -> Counters {
    let funding = matches!(p.rule, Rule::Funding { .. });
    // `rearm` действует только с правилом z (при funding игнорируется); без флага код цикла тот же, что до F-02.
    match (p.delay_x > 0, funding, p.rearm) {
        (false, false, false) => run_pair_impl::<S, false, false, false>(inp, p, sink),
        (true, false, false) => run_pair_impl::<S, true, false, false>(inp, p, sink),
        (false, false, true) => run_pair_impl::<S, false, false, true>(inp, p, sink),
        (true, false, true) => run_pair_impl::<S, true, false, true>(inp, p, sink),
        (false, true, _) => run_pair_impl::<S, false, true, false>(inp, p, sink),
        (true, true, _) => run_pair_impl::<S, true, true, false>(inp, p, sink),
    }
}

/// `DELAY` — нога X входит позже ноги Y (`delay_x > 0`); `FUND` — правило `Rule::Funding`; `REARM` — `Params::rearm`
/// (только с правилом z).
fn run_pair_impl<S: Sink, const DELAY: bool, const FUND: bool, const REARM: bool>(
    inp: &PairInput,
    p: &Params,
    sink: &mut S,
) -> Counters {
    let n = inp.ly.len();
    let k = inp.ox.len();
    let refresh = if p.refresh == 0 { p.window } else { p.refresh };
    let mut c = Counters::default();
    let mut s = Sums::default();
    let mut pend = Pend::None;
    // Позиция: направление, шаг входа (сигнал, исполнение Y, исполнение X), количества и цены входа по ногам (0 = Y).
    let mut dir = 0i8;
    let mut sig_in = 0usize;
    let mut fill_in = 0usize;
    let mut fill_x = 0usize;
    // Нога X ждёт входа с минуты `x_due`; β заморожена на сигнале.
    let mut x_due = NO_DUE;
    let mut x_beta = 0.0f64;
    let mut qty = vec![0.0f64; k + 1];
    let mut pin = vec![0.0f64; k + 1];
    let mut fund = 0.0f64;
    let mut cur_y = 0usize;
    let mut cur_x = vec![0usize; k];
    let no_fund: &[(i64, f64)] = &[];
    // D3: кольцо `fk` последних выплат ноги Y, трейлинг-среднее, курсор по выплатам.
    let (fk, fh) = match p.rule {
        Rule::Funding { k, hold_payments } if FUND => (k, hold_payments as f64),
        _ => (0, 0.0),
    };
    assert!(!FUND || fk > 0, "Rule::Funding: нужно k ≥ 1");
    let mut ring = vec![0.0f64; fk];
    let (mut ring_len, mut ring_head, mut cur_k) = (0usize, 0usize, 0usize);
    let mut r_mean = f64::NAN;
    let wsum = inp.wx.iter().sum::<f64>();
    // F-02: причина решённого выхода; сторона, закрытая стопом или сроком (0 — нет; только `REARM`).
    let mut why = ExitReason::Rule;
    let mut block = 0i8;
    for t in 0..n {
        let ft = inp.first_min + t as i64;
        let opens_ok = inp.oy[t].is_finite() && inp.ox.iter().all(|o| o[t].is_finite());
        let mut filled_now = false;
        match pend {
            Pend::Enter(d, beta, sig) => {
                pend = Pend::None;
                // Без задержки нужны open всех ног в эту минуту; с задержкой — только у Y, нога X идёт в свой срок.
                if (!DELAY && opens_ok) || (DELAY && inp.oy[t].is_finite()) {
                    dir = d;
                    sig_in = sig;
                    fill_in = t;
                    qty[0] = f64::from(d) / inp.oy[t];
                    pin[0] = inp.oy[t];
                    x_beta = beta;
                    if DELAY {
                        x_due = t.saturating_add(p.delay_x);
                    } else {
                        for i in 0..k {
                            pin[i + 1] = inp.ox[i][t];
                            qty[i + 1] = -f64::from(d) * beta * inp.wx[i] / inp.ox[i][t];
                        }
                        fill_x = t;
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
                        fill_x: inp.first_min + fill_x as i64,
                        sig_out: inp.first_min + sig as i64,
                        fill_out: ft,
                        gross_bps,
                        fee_bps: fee,
                        funding_bps,
                        net_bps: net,
                        beta: x_beta,
                        reason: why,
                    });
                    c.trades += 1;
                    c.sum_net_bps += net;
                    if REARM {
                        block = if why == ExitReason::Rule { 0 } else { dir };
                    }
                    dir = 0;
                    pend = Pend::None;
                } else {
                    c.exit_deferred += 1;
                }
            }
            Pend::None => {}
        }
        // Нога X: вход в свой срок по open минуты, где он есть у всех компонент.
        // Без задержки нога X вошла вместе с Y (`x_filled_now == filled_now`).
        let mut x_filled_now = filled_now;
        if DELAY && x_due <= t && dir != 0 {
            if inp.ox.iter().all(|o| o[t].is_finite()) {
                for i in 0..k {
                    pin[i + 1] = inp.ox[i][t];
                    qty[i + 1] = -f64::from(dir) * x_beta * inp.wx[i] / inp.ox[i][t];
                }
                fill_x = t;
                x_due = NO_DUE;
                x_filled_now = true;
            } else {
                c.x_deferred += 1;
            }
        }
        // Фандинг: нога, державшаяся строго через момент выплаты (Y — с входа Y, X — с входа X).
        let hold = dir != 0 && !filled_now;
        let hold_x = dir != 0 && x_due == NO_DUE && !x_filled_now;
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
            if hold_x && rx != 0.0 {
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
        sink.z(t, z);
        if t + 1 >= p.window {
            c.eval_minutes += 1;
            if z.is_finite() {
                c.z_def_minutes += 1;
            }
        }
        if dir != 0 && !z.is_finite() {
            c.pos_z_nan_minutes += 1;
        }
        // D3: выплаты ноги Y с минутой ≤ t входят в трейлинг-среднее (известны на закрытии минуты t).
        if FUND {
            let ev = if inp.fund_y.is_empty() {
                no_fund
            } else {
                inp.fund_y
            };
            let mut got = false;
            while cur_k < ev.len() && ev[cur_k].0 <= ft {
                if ring_len < fk {
                    ring[ring_len] = ev[cur_k].1;
                    ring_len += 1;
                } else {
                    ring[ring_head] = ev[cur_k].1;
                    ring_head = (ring_head + 1) % fk;
                }
                cur_k += 1;
                got = true;
            }
            if got && ring_len == fk {
                let mut acc = 0.0;
                for j in 0..fk {
                    acc += ring[(ring_head + j) % fk];
                }
                r_mean = acc / fk as f64;
            }
        }
        // Сигнал по закрытию минуты t; исполнение — следующая минута. Выход — когда позиция набрана.
        if matches!(pend, Pend::None) {
            if dir == 0 {
                if !FUND {
                    if z.is_finite() && beta.is_finite() {
                        if REARM && z.abs() < p.entry_z {
                            block = 0;
                        }
                        if z <= -p.entry_z {
                            if REARM && block == 1 {
                                c.rearm_blocked += 1;
                            } else {
                                pend = Pend::Enter(1, beta, t);
                                if REARM {
                                    block = 0;
                                }
                            }
                        } else if z >= p.entry_z {
                            if REARM && block == -1 {
                                c.rearm_blocked += 1;
                            } else {
                                pend = Pend::Enter(-1, beta, t);
                                if REARM {
                                    block = 0;
                                }
                            }
                        }
                    } else if obs && t + 1 >= p.window && (s.n as usize) < p.min_obs.max(2) {
                        c.noentry_window_minutes += 1;
                    }
                } else {
                    let b = match p.beta {
                        BetaMode::Fixed(b) => b,
                        BetaMode::Ols => beta,
                    };
                    if r_mean.is_finite() && b.is_finite() {
                        let edge = r_mean.abs() * 1e4 * fh;
                        if edge > round_trip_cost_bps(p, b, wsum) {
                            pend = Pend::Enter(if r_mean > 0.0 { -1 } else { 1 }, b, t);
                        }
                    }
                }
            } else if !DELAY || x_due == NO_DUE {
                let timed = p.max_hold > 0 && t - fill_x >= p.max_hold;
                let by_rule = if !FUND {
                    let dz = f64::from(dir) * z;
                    z.is_finite() && (dz >= -p.exit_z || dz <= -p.stop_z)
                } else {
                    let edge = -f64::from(dir) * r_mean * 1e4 * fh;
                    // не «выше круга», в том числе когда ставки ещё нет (NaN)
                    edge.partial_cmp(&round_trip_cost_bps(p, x_beta, wsum))
                        != Some(std::cmp::Ordering::Greater)
                };
                if by_rule || timed {
                    why = if !by_rule {
                        ExitReason::Timeout
                    } else if !FUND && f64::from(dir) * z <= -p.stop_z {
                        ExitReason::Stop
                    } else {
                        ExitReason::Rule
                    };
                    pend = Pend::Exit(t);
                }
            }
        }
    }
    if dir != 0 {
        c.open_at_end = 1;
        sink.open_end(&OpenPos {
            dir,
            sig_in: inp.first_min + sig_in as i64,
            fill_in: inp.first_min + fill_in as i64,
            fill_x: if DELAY && x_due != NO_DUE {
                None
            } else {
                Some(inp.first_min + fill_x as i64)
            },
            qty: &qty,
            pin: &pin,
            fund,
        });
    }
    c
}
