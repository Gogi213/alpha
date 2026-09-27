//! Минутный поток заявок по всей видимой книге (TK-012, П-08 §12, Г-140).
//!
//! Чистая логика: кадр стороны книги (тики и лоты, лучшая цена первой)
//! сравнивается с прошлым кадром той же стороны, сделки копятся между
//! кадрами. Правила кадра:
//! - цена есть в обоих кадрах: рост размера — `add`, падение — сначала
//!   сделки на этой цене со времени прошлого кадра, остаток — `cancel`
//!   (не меньше нуля);
//! - цена появилась — `add` её размера, если она не глубже худшей видимой
//!   цены **прошлого** кадра (глубже — просто вошла в окно видимости);
//! - цена исчезла — падение до нуля по тому же правилу, если она не глубже
//!   худшей видимой цены **текущего** кадра (глубже — просто вышла из окна,
//!   это не отмена);
//! - первый кадр, снапшот (`reset`) и пустая сторона — только база, без
//!   потока.
//!
//! `trade_lots` — все сделки монеты за минуту, кроме блочных и RPI (та же
//! отсечка, что у `levels::LevelTracker::observe_trade` для `traded`).
//! Минута — `floor(ts / 60 000) × 60 000` метки кадра или сделки; строка — на
//! каждую минуту, где был хотя бы один кадр (нули допустимы), по времени.
//! Событие с меткой раньше текущей минуты идёт в текущую (назад строки не
//! переписываются).
//!
//! Горячий путь: массивы фиксированной ёмкости `MAX_LEVELS` на сторону, без
//! карт и `f64`; куча — только у выходного вектора строк (строка на минуту).

/// Ёмкость стороны кадра: столько же держит книга (`book::CAPACITY`);
/// более глубокие уровни отбрасываются, худшей видимой считается последняя
/// взятая.
pub const MAX_LEVELS: usize = 256;

/// Ёмкость копилки сделок одной стороны между кадрами — различных цен.
/// Переполнение считается (`pending_overflow`), такие сделки в разбор
/// падения не идут (падение уходит в `cancel`), в `trade_lots` — идут.
pub const MAX_PENDING: usize = 256;

use crate::lob::levels::TradeHit;

const MINUTE_MS: i64 = 60_000;

/// Строка минутного ряда.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MinuteRow {
    pub minute_ms: i64,
    pub add_lots: i64,
    pub cancel_lots: i64,
    pub trade_lots: i64,
}

/// Кадр одной стороны: ключ «лучше — меньше» (`тик` у аска, `−тик` у бида),
/// по возрастанию ключа.
#[derive(Clone)]
struct SideFrame {
    keys: [i64; MAX_LEVELS],
    lots: [i64; MAX_LEVELS],
    n: usize,
}

impl SideFrame {
    const fn new() -> Self {
        Self {
            keys: [0; MAX_LEVELS],
            lots: [0; MAX_LEVELS],
            n: 0,
        }
    }
}

/// Сделки одной стороны книги со времени прошлого кадра, по ключу цены.
#[derive(Clone)]
struct Pending {
    keys: [i64; MAX_PENDING],
    lots: [i64; MAX_PENDING],
    n: usize,
}

impl Pending {
    const fn new() -> Self {
        Self {
            keys: [0; MAX_PENDING],
            lots: [0; MAX_PENDING],
            n: 0,
        }
    }

    fn at(&self, key: i64) -> i64 {
        self.keys
            .iter()
            .zip(self.lots.iter())
            .take(self.n)
            .find(|(k, _)| **k == key)
            .map_or(0, |(_, l)| *l)
    }

    /// `false` — переполнение (цена новая, места нет).
    fn add(&mut self, key: i64, lots: i64) -> bool {
        let n = self.n;
        for (k, l) in self.keys.iter().zip(self.lots.iter_mut()).take(n) {
            if *k == key {
                *l = l.saturating_add(lots);
                return true;
            }
        }
        match (self.keys.get_mut(n), self.lots.get_mut(n)) {
            (Some(k), Some(l)) => {
                *k = key;
                *l = lots;
                self.n = n + 1;
                true
            }
            _ => false,
        }
    }
}

/// Состояние стороны: прошлый кадр, рабочий буфер и копилка сделок.
#[derive(Clone)]
struct SideState {
    prev: SideFrame,
    cur: SideFrame,
    /// Есть ли база (прошлый кадр после сброса).
    has_prev: bool,
    pending: Pending,
}

impl SideState {
    fn new() -> Self {
        Self {
            prev: SideFrame::new(),
            cur: SideFrame::new(),
            has_prev: false,
            pending: Pending::new(),
        }
    }
}

/// Итог сравнения кадров стороны.
#[derive(Debug, Default, Clone, Copy)]
struct Flow {
    add: i64,
    cancel: i64,
}

/// Сравнение `prev → cur` одной стороны (оба по возрастанию ключа).
fn diff_side(prev: &SideFrame, cur: &SideFrame, pending: &Pending) -> Flow {
    let mut f = Flow::default();
    let pk = prev.keys.get(..prev.n).unwrap_or(&[]);
    let pl = prev.lots.get(..prev.n).unwrap_or(&[]);
    let ck = cur.keys.get(..cur.n).unwrap_or(&[]);
    let cl = cur.lots.get(..cur.n).unwrap_or(&[]);
    let (Some(&prev_worst), Some(&cur_worst)) = (pk.last(), ck.last()) else {
        return f;
    };
    let decrease = |f: &mut Flow, key: i64, lots: i64| {
        let traded = pending.at(key).max(0);
        f.cancel = f.cancel.saturating_add((lots - traded).max(0));
    };
    let (mut i, mut j) = (0usize, 0usize);
    loop {
        match (pk.get(i).zip(pl.get(i)), ck.get(j).zip(cl.get(j))) {
            (None, None) => break,
            (Some((&k, &l)), None) => {
                if k <= cur_worst {
                    decrease(&mut f, k, l);
                }
                i += 1;
            }
            (None, Some((&k, &l))) => {
                if k <= prev_worst {
                    f.add = f.add.saturating_add(l);
                }
                j += 1;
            }
            (Some((&kp, &lp)), Some((&kc, &lc))) => {
                if kp == kc {
                    if lc > lp {
                        f.add = f.add.saturating_add(lc - lp);
                    } else if lc < lp {
                        decrease(&mut f, kp, lp - lc);
                    }
                    i += 1;
                    j += 1;
                } else if kp < kc {
                    if kp <= cur_worst {
                        decrease(&mut f, kp, lp);
                    }
                    i += 1;
                } else {
                    if kc <= prev_worst {
                        f.add = f.add.saturating_add(lc);
                    }
                    j += 1;
                }
            }
        }
    }
    f
}

/// Минутный поток заявок одной монеты.
pub struct MinuteFlow {
    bid: SideState,
    ask: SideState,
    minute: Option<i64>,
    has_frame: bool,
    acc: MinuteRow,
    /// Сделок, не уместившихся в копилку (`MAX_PENDING`), за всё время.
    pub pending_overflow: u64,
}

impl Default for MinuteFlow {
    fn default() -> Self {
        Self::new()
    }
}

impl MinuteFlow {
    pub fn new() -> Self {
        Self {
            bid: SideState::new(),
            ask: SideState::new(),
            minute: None,
            has_frame: false,
            acc: MinuteRow {
                minute_ms: 0,
                add_lots: 0,
                cancel_lots: 0,
                trade_lots: 0,
            },
            pending_overflow: 0,
        }
    }

    /// Переход к минуте события; закрытая минута с кадром уходит в `out`.
    fn roll(&mut self, ts_ms: i64, out: &mut Vec<MinuteRow>) {
        let m = ts_ms.div_euclid(MINUTE_MS).saturating_mul(MINUTE_MS);
        match self.minute {
            Some(cur) if m <= cur => {}
            _ => {
                self.flush(out);
                self.minute = Some(m);
                self.acc = MinuteRow {
                    minute_ms: m,
                    add_lots: 0,
                    cancel_lots: 0,
                    trade_lots: 0,
                };
            }
        }
    }

    fn flush(&mut self, out: &mut Vec<MinuteRow>) {
        if self.minute.is_some() && self.has_frame {
            out.push(self.acc);
        }
        self.has_frame = false;
    }

    /// Сделка (`aggressor_is_buy` — ест аск). Блочные, RPI и непозитивные — мимо.
    pub fn observe_trade(&mut self, tr: TradeHit, out: &mut Vec<MinuteRow>) {
        if tr.block || tr.rpi || tr.lots <= 0 {
            return;
        }
        let (ts_ms, tick, lots) = (tr.exch_ms, tr.tick, tr.lots);
        let aggressor_is_buy = tr.aggressor_is_buy;
        self.roll(ts_ms, out);
        self.acc.trade_lots = self.acc.trade_lots.saturating_add(lots);
        let (side, key) = if aggressor_is_buy {
            (&mut self.ask, tick)
        } else {
            (&mut self.bid, tick.saturating_neg())
        };
        if !side.pending.add(key, lots) {
            self.pending_overflow += 1;
        }
    }

    /// Кадр книги: стороны лучшей ценой первой (`Book::levels`). `reset` —
    /// снапшот или новый файл: кадр становится базой без потока.
    pub fn observe_frame<B, A>(
        &mut self,
        ts_ms: i64,
        bids: B,
        asks: A,
        reset: bool,
        out: &mut Vec<MinuteRow>,
    ) where
        B: IntoIterator<Item = (i64, i64)>,
        A: IntoIterator<Item = (i64, i64)>,
    {
        self.roll(ts_ms, out);
        self.has_frame = true;
        let fb = Self::side_frame(&mut self.bid, bids, true, reset);
        let fa = Self::side_frame(&mut self.ask, asks, false, reset);
        self.acc.add_lots = self.acc.add_lots.saturating_add(fb.add + fa.add);
        self.acc.cancel_lots = self.acc.cancel_lots.saturating_add(fb.cancel + fa.cancel);
    }

    fn side_frame<I: IntoIterator<Item = (i64, i64)>>(
        s: &mut SideState,
        levels: I,
        is_bid: bool,
        reset: bool,
    ) -> Flow {
        let mut n = 0usize;
        for ((tick, lots), (k, l)) in levels
            .into_iter()
            .zip(s.cur.keys.iter_mut().zip(s.cur.lots.iter_mut()))
        {
            *k = if is_bid { tick.saturating_neg() } else { tick };
            *l = lots;
            n += 1;
        }
        s.cur.n = n;
        let f = if s.has_prev && !reset {
            diff_side(&s.prev, &s.cur, &s.pending)
        } else {
            Flow::default()
        };
        std::mem::swap(&mut s.prev, &mut s.cur);
        s.has_prev = n > 0;
        s.pending.n = 0;
        f
    }

    /// Конец ряда: последняя минута с кадром уходит в `out`.
    pub fn finish(&mut self, out: &mut Vec<MinuteRow>) {
        self.flush(out);
        self.minute = None;
    }

    /// Сброс баз обеих сторон (новый файл): следующий кадр — база.
    pub fn reset_book(&mut self) {
        for s in [&mut self.bid, &mut self.ask] {
            s.has_prev = false;
            s.pending.n = 0;
        }
    }
}

#[cfg(test)]
mod tests;
