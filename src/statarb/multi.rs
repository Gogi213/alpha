//! Многоклеточный проход (TK-486, В-90 п.3): одна пара, много клеток П-32 за один проход по минутам.
//!
//! Скользящие суммы, β, z и ставки фандинга не зависят от порогов, стопа, срока и комиссий, поэтому
//! считаются один раз на минуту; на клетку остаётся автомат позиции. Клетки группы обязаны совпадать
//! по `window`, `min_obs`, `beta`, `refresh` — остальное свободно. Сделки и счётчики каждой клетки
//! совпадают с `kernel::run_pair` побитно (тест `multi_equals_single_cell_passes`); `run_pair` остаётся
//! эталоном и умолчанием, пока гейт против прежнего бинарника на calc не пройден.

use super::kernel::{
    beta_z, price_at, rates_at, Counters, PairInput, Params, Pend, Sink, Sums, Trade,
};

struct Cell {
    pend: Pend,
    dir: i8,
    sig_in: usize,
    fill_in: usize,
    qty: Vec<f64>,
    pin: Vec<f64>,
    fund: f64,
    c: Counters,
}

impl Cell {
    fn new(k: usize) -> Cell {
        Cell {
            pend: Pend::None,
            dir: 0,
            sig_in: 0,
            fill_in: 0,
            qty: vec![0.0; k + 1],
            pin: vec![0.0; k + 1],
            fund: 0.0,
            c: Counters::default(),
        }
    }

    /// Начало минуты `t`: исполнение отложенного ордера, выплата фандинга, минуты в позиции.
    #[allow(clippy::too_many_arguments)]
    fn open_minute<S: Sink>(
        &mut self,
        inp: &PairInput,
        p: &Params,
        sink: &mut S,
        t: usize,
        opens_ok: bool,
        ry: f64,
        rx: &[f64],
    ) {
        let k = inp.ox.len();
        let ft = inp.first_min + t as i64;
        let mut filled_now = false;
        match self.pend {
            Pend::Enter(d, beta, sig) => {
                self.pend = Pend::None;
                if opens_ok {
                    self.dir = d;
                    self.sig_in = sig;
                    self.fill_in = t;
                    self.qty[0] = f64::from(d) / inp.oy[t];
                    self.pin[0] = inp.oy[t];
                    for i in 0..k {
                        self.pin[i + 1] = inp.ox[i][t];
                        self.qty[i + 1] = -f64::from(d) * beta * inp.wx[i] / inp.ox[i][t];
                    }
                    self.fund = 0.0;
                    self.c.entries += 1;
                    filled_now = true;
                } else {
                    self.c.entry_deferred += 1;
                }
            }
            Pend::Exit(sig) => {
                if opens_ok {
                    let (qty, pin) = (&self.qty, &self.pin);
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
                    let (gross_bps, funding_bps) = (gross * 1e4, self.fund * 1e4);
                    let net = gross_bps - fee + funding_bps;
                    sink.trade(&Trade {
                        dir: self.dir,
                        sig_in: inp.first_min + self.sig_in as i64,
                        fill_in: inp.first_min + self.fill_in as i64,
                        sig_out: inp.first_min + sig as i64,
                        fill_out: ft,
                        gross_bps,
                        fee_bps: fee,
                        funding_bps,
                        net_bps: net,
                    });
                    self.c.trades += 1;
                    self.c.sum_net_bps += net;
                    self.dir = 0;
                    self.pend = Pend::None;
                } else {
                    self.c.exit_deferred += 1;
                }
            }
            Pend::None => {}
        }
        // Фандинг: позиция, державшаяся строго через момент выплаты.
        let hold = self.dir != 0 && !filled_now;
        if hold && ry != 0.0 {
            self.fund -= self.qty[0] * price_at(inp.oy, t) * ry;
        }
        for (i, &r) in rx.iter().enumerate() {
            if hold && r != 0.0 {
                self.fund -= self.qty[i + 1] * price_at(inp.ox[i], t) * r;
            }
        }
        if self.dir != 0 {
            self.c.minutes_in_pos += 1;
        }
    }

    /// Конец минуты `t`: сигнал по закрытию, исполнение — следующая минута.
    fn close_minute(&mut self, p: &Params, t: usize, beta: f64, z: f64) {
        if !matches!(self.pend, Pend::None) {
            return;
        }
        if self.dir == 0 {
            if z.is_finite() && beta.is_finite() {
                if z <= -p.entry_z {
                    self.pend = Pend::Enter(1, beta, t);
                } else if z >= p.entry_z {
                    self.pend = Pend::Enter(-1, beta, t);
                }
            }
        } else {
            let dz = f64::from(self.dir) * z;
            let timed = p.max_hold > 0 && t - self.fill_in >= p.max_hold;
            if (z.is_finite() && (dz >= -p.exit_z || dz <= -p.stop_z)) || timed {
                self.pend = Pend::Exit(t);
            }
        }
    }
}

/// Прогон одной пары по всему спану для всех клеток `cells` сразу; `sinks[i]` получает сделки и z
/// клетки `i`. Клетки группы должны совпадать по `window`, `min_obs`, `beta`, `refresh`.
pub fn run_pair_multi<S: Sink>(
    inp: &PairInput,
    cells: &[Params],
    sinks: &mut [S],
) -> Result<Vec<Counters>, &'static str> {
    let Some(p0) = cells.first() else {
        return Err("нет клеток");
    };
    if sinks.len() != cells.len() {
        return Err("приёмников не столько, сколько клеток");
    }
    if cells.iter().any(|p| {
        p.window != p0.window
            || p.min_obs != p0.min_obs
            || p.beta != p0.beta
            || p.refresh != p0.refresh
    }) {
        return Err("клетки группы различаются по window / min_obs / beta / refresh");
    }
    let n = inp.ly.len();
    let k = inp.ox.len();
    let refresh = if p0.refresh == 0 {
        p0.window
    } else {
        p0.refresh
    };
    let mut st: Vec<Cell> = cells.iter().map(|_| Cell::new(k)).collect();
    let (mut obs_minutes, mut gap_minutes) = (0u64, 0u64);
    let mut s = Sums::default();
    let mut cur_y = 0usize;
    let mut cur_x = vec![0usize; k];
    let mut rx = vec![0.0f64; k];
    let no_fund: &[(i64, f64)] = &[];
    for t in 0..n {
        let ft = inp.first_min + t as i64;
        let opens_ok = inp.oy[t].is_finite() && inp.ox.iter().all(|o| o[t].is_finite());
        let ry = rates_at(inp.fund_y, &mut cur_y, ft);
        for i in 0..k {
            rx[i] = rates_at(
                inp.fund_x.get(i).copied().unwrap_or(no_fund),
                &mut cur_x[i],
                ft,
            );
        }
        for ((cell, p), sink) in st.iter_mut().zip(cells).zip(sinks.iter_mut()) {
            cell.open_minute(inp, p, sink, t, opens_ok, ry, &rx);
        }
        // Окно (общее): добавить минуту t, убрать t−W, раз в `refresh` — точный пересчёт.
        let (xt, yt) = (inp.lx[t], inp.ly[t]);
        let obs = xt.is_finite() && yt.is_finite();
        if obs {
            s.add(xt, yt);
            obs_minutes += 1;
        } else {
            gap_minutes += 1;
        }
        if t >= p0.window {
            let j = t - p0.window;
            if inp.lx[j].is_finite() && inp.ly[j].is_finite() {
                s.sub(inp.lx[j], inp.ly[j]);
            }
        }
        if (t + 1) % refresh == 0 {
            let lo = (t + 1).saturating_sub(p0.window);
            s = Sums::rebuild(inp.lx, inp.ly, lo, t);
        }
        let (beta, z) = if obs {
            beta_z(&s, p0.beta, xt, yt, p0.min_obs)
        } else {
            (f64::NAN, f64::NAN)
        };
        for ((cell, p), sink) in st.iter_mut().zip(cells).zip(sinks.iter_mut()) {
            sink.z(t, z);
            cell.close_minute(p, t, beta, z);
        }
    }
    Ok(st
        .into_iter()
        .map(|mut cell| {
            cell.c.obs_minutes = obs_minutes;
            cell.c.gap_minutes = gap_minutes;
            if cell.dir != 0 {
                cell.c.open_at_end = 1;
            }
            cell.c
        })
        .collect())
}
