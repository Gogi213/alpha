//! Состояние монеты для колонок R1 (TK-025): лента, книга, поток заявок.
//!
//! Определения колонок — `docs/findings/tk025-design-2026-10-02.md` (раздел «Колонки состояния монеты»),
//! порядок и имена — `r1::FLOW_NAMES`. Всё целое, ёмкость фиксирована в `new()`, на событие аллокаций нет;
//! время — только из аргументов (`exch_ms` сделки, `ms` кадра, `t0_ms` взвода).
//!
//! Окна — назад от `t0`, по слотам: секундное кольцо `(s0−W, s0]`, минутное `(m0−M, m0]`, где слот события
//! `exch_ms/1000` (`/60000`). Слот держит номер секунды (минуты), поэтому слот старше окна не просачивается
//! в сумму, даже если ещё не перезаписан. Окно определено, только когда монета наблюдается не меньше его длины
//! (`t0 − start_ms ≥ W`, `start_ms` — первый кадр); иначе колонка — [`R1_UNDEF`].

use crate::book::Side;
use crate::lob::levels::TradeHit;
use crate::lob::r1::{FLOW_N, R1_UNDEF};

/// Глубина снимка книги, которую R1 читает (`obi50_bp`).
pub const BOOK_DEPTH: usize = 50;

/// Слотов в секундном и минутном кольцах (наибольшее окно: 60 с и 60 мин).
const RING: usize = 60;
/// Корзин log₂-гистограммы размера сделки: `k = ⌊log₂ lots⌋ ≤ 62` для `lots ≤ i64::MAX`.
const SIZE_BUCKETS: usize = 64;
/// Закрытых объёмных баров в окне vpin.
const VPIN_BARS: usize = 50;
/// Секундные окна, которые читает `fill`; индекс в [`R1Flow::sec_sums`].
const WINS_S: [i64; 4] = [10, 15, 30, 60];
const W10: usize = 0;
const W15: usize = 1;
const W30: usize = 2;
const W60: usize = 3;
/// Прогрев часовых окон и vpin, мс.
const HOUR_MS: i64 = 3_600_000;
/// Окно размеров сделок, минут.
const SIZE_WINDOW_MIN: i64 = 15;

// Начало групп в `FLOW_NAMES` (тест `layout_matches_names` сверяет их с именами).
const I_TAPE: usize = 0;
const I_60M: usize = 18;
const I_BURST: usize = 20;
const I_AVG: usize = 24;
const I_SIGN: usize = 26;
const I_VPIN: usize = 28;
const I_SIZE: usize = 29;
const I_OBI: usize = 31;
const I_MICRO: usize = 35;
const I_OFI: usize = 36;
const I_FLIPS: usize = 38;

/// Номер слота, которого ещё не было.
const NO_SLOT: i64 = i64::MIN;

/// Кадр книги для R1: уровни `(тик, лоты)` каждой стороны, лучшая цена первой, не больше [`BOOK_DEPTH`].
#[derive(Debug, Clone, Copy)]
pub struct BookView<'a> {
    pub bids: &'a [(i64, i64)],
    pub asks: &'a [(i64, i64)],
}

/// Одна секунда: сделки по стороне агрессора (индекс 0 — продавец, 1 — покупатель), пары знаков, OFI,
/// смены лучшей цены (индекс 0 — бид, 1 — аск).
#[derive(Debug, Clone, Copy)]
struct SecSlot {
    id: i64,
    lots: [i64; 2],
    n: [i64; 2],
    ac_sum: i64,
    ac_n: i64,
    ofi: i64,
    flips: [i64; 2],
}

impl SecSlot {
    const fn new(id: i64) -> Self {
        Self {
            id,
            lots: [0; 2],
            n: [0; 2],
            ac_sum: 0,
            ac_n: 0,
            ofi: 0,
            flips: [0; 2],
        }
    }

    fn add(&mut self, o: &SecSlot) {
        self.lots[0] = self.lots[0].saturating_add(o.lots[0]);
        self.lots[1] = self.lots[1].saturating_add(o.lots[1]);
        self.n[0] += o.n[0];
        self.n[1] += o.n[1];
        self.flips[0] += o.flips[0];
        self.flips[1] += o.flips[1];
        self.ac_sum += o.ac_sum;
        self.ac_n += o.ac_n;
        self.ofi = self.ofi.saturating_add(o.ofi);
    }
}

/// Одна минута: лоты по стороне агрессора и log₂-гистограмма размеров сделок.
#[derive(Debug, Clone, Copy)]
struct MinSlot {
    id: i64,
    lots: [i64; 2],
    hist: [u32; SIZE_BUCKETS],
}

impl MinSlot {
    const fn new(id: i64) -> Self {
        Self {
            id,
            lots: [0; 2],
            hist: [0; SIZE_BUCKETS],
        }
    }
}

/// Кольца ленты, потока заявок и снимок книги одной монеты за сутки. Ёмкость фиксирована при создании.
#[derive(Debug)]
pub struct R1Flow {
    /// Первый кадр монеты: от него считается прогрев.
    start_ms: Option<i64>,
    sec: [SecSlot; RING],
    min: [MinSlot; RING],
    /// Знак предыдущей сделки монеты (+1 покупатель, −1 продавец, 0 — сделок ещё не было).
    prev_sign: i64,
    /// Лучшие бид/аск предыдущего кадра `(тик, лоты)` — для OFI и смен цены.
    prev_bid: Option<(i64, i64)>,
    prev_ask: Option<(i64, i64)>,
    /// Последний кадр книги.
    bids: [(i64, i64); BOOK_DEPTH],
    asks: [(i64, i64); BOOK_DEPTH],
    n_bids: usize,
    n_asks: usize,
    /// Открытый объёмный бар vpin: размер (0 — бара нет), набрано, лоты по стороне агрессора.
    bar_size: i64,
    bar_fill: i64,
    bar_vol: [i64; 2],
    /// Закрытые бары (лоты по стороне агрессора), кольцо на `VPIN_BARS`.
    bars: [[i64; 2]; VPIN_BARS],
    bars_pos: usize,
    bars_len: usize,
}

impl Default for R1Flow {
    fn default() -> Self {
        Self::blank()
    }
}

impl R1Flow {
    fn blank() -> Self {
        Self {
            start_ms: None,
            sec: [SecSlot::new(NO_SLOT); RING],
            min: [MinSlot::new(NO_SLOT); RING],
            prev_sign: 0,
            prev_bid: None,
            prev_ask: None,
            bids: [(0, 0); BOOK_DEPTH],
            asks: [(0, 0); BOOK_DEPTH],
            n_bids: 0,
            n_asks: 0,
            bar_size: 0,
            bar_fill: 0,
            bar_vol: [0; 2],
            bars: [[0; 2]; VPIN_BARS],
            bars_pos: 0,
            bars_len: 0,
        }
    }

    /// Создаётся один раз на монету (выделение допустимо только здесь).
    pub fn new() -> Box<Self> {
        Box::new(Self::blank())
    }

    /// Секундный слот; `None` — секунда старше той, что уже занимает слот (запоздалое событие).
    fn sec_slot(&mut self, sec: i64) -> Option<&mut SecSlot> {
        let slot = &mut self.sec[sec.rem_euclid(RING as i64) as usize];
        if slot.id > sec {
            return None;
        }
        if slot.id < sec {
            *slot = SecSlot::new(sec);
        }
        Some(slot)
    }

    fn min_slot(&mut self, minute: i64) -> Option<&mut MinSlot> {
        let slot = &mut self.min[minute.rem_euclid(RING as i64) as usize];
        if slot.id > minute {
            return None;
        }
        if slot.id < minute {
            *slot = MinSlot::new(minute);
        }
        Some(slot)
    }

    /// Сделка ленты; вызывается из `LevelTracker::observe_trade` для каждой не-блочной сделки.
    /// Отбор тот же, что у кольца `flow_ring` трекера: не `block`, `lots > 0`, RPI-сделки входят,
    /// время — `exch_ms`.
    pub fn on_trade(&mut self, hit: &TradeHit) {
        if hit.block || hit.lots <= 0 {
            return;
        }
        let buy = hit.aggressor_is_buy;
        let a = usize::from(buy);
        let sign: i64 = if buy { 1 } else { -1 };
        // Предыдущий знак глобален по монете и не зависит от того, принял ли кольцо запоздалое событие.
        let prev = std::mem::replace(&mut self.prev_sign, sign);
        if let Some(sl) = self.sec_slot(hit.exch_ms.div_euclid(1_000)) {
            sl.lots[a] = sl.lots[a].saturating_add(hit.lots);
            sl.n[a] += 1;
            if prev != 0 {
                sl.ac_sum += sign * prev;
                sl.ac_n += 1;
            }
        }
        let k = 63 - hit.lots.leading_zeros() as usize;
        let Some(ms) = self.min_slot(hit.exch_ms.div_euclid(60_000)) else {
            return;
        };
        ms.lots[a] = ms.lots[a].saturating_add(hit.lots);
        ms.hist[k] = ms.hist[k].saturating_add(1);
        self.vpin_trade(hit.exch_ms, a, hit.lots);
    }

    /// Оборот монеты за 60 минут `(m−60, m]` на минуту `now_ms` — то же окно, что у `flow_1h_lots` трекера.
    fn flow_1h_lots(&self, now_ms: i64) -> i64 {
        let now_min = now_ms.div_euclid(60_000);
        let mut sum: i64 = 0;
        for m in &self.min {
            let age = now_min.saturating_sub(m.id);
            if (0..RING as i64).contains(&age) {
                sum = sum.saturating_add(m.lots[0]).saturating_add(m.lots[1]);
            }
        }
        sum
    }

    /// Объёмные бары vpin. Бары строятся только после прогрева 60 мин (до него размер бара
    /// занижен неполным часом оборота). Размер бара — `flow_1h/50` (≥ 1 лота) на открытии бара, по
    /// обороту с учётом всей текущей сделки; сделка делится между барами. `flow_1h ≥ lots`, поэтому
    /// баров на сделку не больше ≈ 50.
    fn vpin_trade(&mut self, ms: i64, a: usize, lots: i64) {
        let Some(start) = self.start_ms else {
            return;
        };
        if ms.saturating_sub(start) < HOUR_MS {
            return;
        }
        let mut rest = lots;
        while rest > 0 {
            if self.bar_size == 0 {
                self.bar_size = (self.flow_1h_lots(ms) / VPIN_BARS as i64).max(1);
                self.bar_fill = 0;
                self.bar_vol = [0; 2];
            }
            let take = rest.min(self.bar_size - self.bar_fill);
            self.bar_vol[a] += take;
            self.bar_fill += take;
            rest -= take;
            if self.bar_fill == self.bar_size {
                self.bars[self.bars_pos] = self.bar_vol;
                self.bars_pos = (self.bars_pos + 1) % VPIN_BARS;
                self.bars_len = (self.bars_len + 1).min(VPIN_BARS);
                self.bar_size = 0;
            }
        }
    }

    /// Кадр книги в момент `ms`; вызывается из `LevelTracker::begin_frame`.
    /// Повторный кадр с той же книгой вкладов не даёт (OFI и смены цены — разности соседних кадров).
    pub fn on_frame(&mut self, ms: i64, book: BookView<'_>) {
        if self.start_ms.is_none() {
            self.start_ms = Some(ms);
        }
        let nb = book.bids.len().min(BOOK_DEPTH);
        let na = book.asks.len().min(BOOK_DEPTH);
        self.bids[..nb].copy_from_slice(&book.bids[..nb]);
        self.asks[..na].copy_from_slice(&book.asks[..na]);
        self.n_bids = nb;
        self.n_asks = na;

        let bid = book.bids.first().copied();
        let ask = book.asks.first().copied();
        let flip = |prev: Option<(i64, i64)>, cur: Option<(i64, i64)>| -> i64 {
            match (prev, cur) {
                (Some((p0, _)), Some((p1, _))) => i64::from(p0 != p1),
                _ => 0,
            }
        };
        let flips = [flip(self.prev_bid, bid), flip(self.prev_ask, ask)];
        // OFI — только когда лучшие уровни обеих сторон есть в обоих кадрах.
        let ofi = match (self.prev_bid, self.prev_ask, bid, ask) {
            (Some(b0), Some(a0), Some(b1), Some(a1)) => ofi_step(b0, a0, b1, a1),
            _ => 0,
        };
        self.prev_bid = bid;
        self.prev_ask = ask;
        if ofi != 0 || flips != [0; 2] {
            if let Some(sl) = self.sec_slot(ms.div_euclid(1_000)) {
                sl.ofi = sl.ofi.saturating_add(ofi);
                sl.flips[0] += flips[0];
                sl.flips[1] += flips[1];
            }
        }
    }

    /// Суммы секундного кольца по окнам [`WINS_S`]: слот входит, если `0 ≤ s0 − id < W`.
    fn sec_sums(&self, s0: i64) -> [SecSlot; 4] {
        let mut sums = [SecSlot::new(0); 4];
        for sl in &self.sec {
            let age = s0.saturating_sub(sl.id);
            if !(0..RING as i64).contains(&age) {
                continue;
            }
            for (sum, w) in sums.iter_mut().zip(WINS_S) {
                if age < w {
                    sum.add(sl);
                }
            }
        }
        sums
    }

    /// Значения [`r1::FLOW_NAMES`] на момент `t0_ms` для стены стороны `wall_side`.
    /// Направленные величины ориентированы «от стены»: у аск-стены знак зеркалится.
    pub fn fill(&self, wall_side: Side, t0_ms: i64, out: &mut [i64; FLOW_N]) {
        out.fill(R1_UNDEF);
        let Some(start) = self.start_ms else {
            return;
        };
        let elapsed = t0_ms.saturating_sub(start);
        let ask_wall = wall_side == Side::Ask;
        // Агрессоры против стены: у бид-стены продают (индекс 0), у аск-стены покупают (индекс 1).
        let press = usize::from(ask_wall);
        let with = 1 - press;
        let sums = self.sec_sums(t0_ms.div_euclid(1_000));
        let warm = |w_s: i64| elapsed >= w_s * 1_000;

        // Минутное кольцо: лоты за 60 мин и гистограмма за 15 мин.
        let m0 = t0_ms.div_euclid(60_000);
        let mut lots_60m = [0i64; 2];
        let mut hist = [0u64; SIZE_BUCKETS];
        for m in &self.min {
            let age = m0.saturating_sub(m.id);
            if !(0..RING as i64).contains(&age) {
                continue;
            }
            for (acc, v) in lots_60m.iter_mut().zip(m.lots) {
                *acc = acc.saturating_add(v);
            }
            if age < SIZE_WINDOW_MIN {
                for (acc, c) in hist.iter_mut().zip(m.hist) {
                    *acc += u64::from(c);
                }
            }
        }

        // Лента 15/30/60 с.
        for (g, wi) in [W15, W30, W60].into_iter().enumerate() {
            if !warm(WINS_S[wi]) {
                continue;
            }
            let s = &sums[wi];
            let b = I_TAPE + g * 6;
            out[b] = s.lots[press];
            out[b + 1] = s.lots[with];
            out[b + 2] = s.lots[press].saturating_add(s.lots[with]);
            out[b + 3] = s.n[press];
            out[b + 4] = s.n[with];
            out[b + 5] = s.n[press] + s.n[with];
        }
        let warm_hour = elapsed >= HOUR_MS;
        if warm_hour {
            out[I_60M] = lots_60m[press];
            out[I_60M + 1] = lots_60m[with];
            // Всплеск: темп окна к среднему темпу за 60 мин (10⁴ — норма).
            for (g, wi) in [W15, W30].into_iter().enumerate() {
                let w = i128::from(WINS_S[wi]);
                for (j, side) in [press, with].into_iter().enumerate() {
                    out[I_BURST + 2 * g + j] = ratio(
                        i128::from(sums[wi].lots[side]) * 36_000_000,
                        w * i128::from(lots_60m[side]),
                    );
                }
            }
        }
        // Средняя сделка окна 30 с.
        if warm(WINS_S[W30]) {
            for (j, side) in [press, with].into_iter().enumerate() {
                out[I_AVG + j] = ratio(
                    i128::from(sums[W30].lots[side]) * 100,
                    i128::from(sums[W30].n[side]),
                );
            }
        }
        // Автокорреляция знаков: не зависит от стороны стены.
        for (j, wi) in [W15, W60].into_iter().enumerate() {
            if warm(WINS_S[wi]) {
                out[I_SIGN + j] = ratio(
                    i128::from(sums[wi].ac_sum) * 10_000,
                    i128::from(sums[wi].ac_n),
                );
            }
        }
        if warm_hour && self.bars_len == VPIN_BARS {
            let (mut imb, mut vol) = (0i128, 0i128);
            for b in &self.bars {
                imb += i128::from((b[1] - b[0]).abs());
                vol += i128::from(b[0] + b[1]);
            }
            out[I_VPIN] = ratio(imb * 10_000, vol);
        }
        if elapsed >= SIZE_WINDOW_MIN * 60_000 {
            let n: u64 = hist.iter().sum();
            out[I_SIZE] = size_percentile(&hist, n, 50);
            out[I_SIZE + 1] = size_percentile(&hist, n, 90);
        }

        // Книга: последний кадр.
        let (own, opp) = if ask_wall {
            (&self.asks[..self.n_asks], &self.bids[..self.n_bids])
        } else {
            (&self.bids[..self.n_bids], &self.asks[..self.n_asks])
        };
        for (j, n) in [1usize, 5, 10, 50].into_iter().enumerate() {
            out[I_OBI + j] = obi(own, opp, n);
        }
        if let (Some(&(pb, qb)), Some(&(pa, qa))) = (
            self.bids[..self.n_bids].first(),
            self.asks[..self.n_asks].first(),
        ) {
            // micro − mid относительно mid: (ask−bid)(qb−qa) / ((qb+qa)(bid+ask)), ×10⁶ (цены в тиках).
            let num = 1_000_000 * i128::from(pa - pb) * i128::from(qb - qa);
            let den = i128::from(qb + qa) * i128::from(pb + pa);
            out[I_MICRO] = orient(ratio(num, den), ask_wall);
        }

        // Поток заявок.
        for (j, wi) in [W10, W60].into_iter().enumerate() {
            if warm(WINS_S[wi]) {
                out[I_OFI + j] = orient(sums[wi].ofi, ask_wall);
            }
        }
        for (j, wi) in [W15, W60].into_iter().enumerate() {
            if warm(WINS_S[wi]) {
                out[I_FLIPS + j] = sums[wi].flips[press];
            }
        }
    }
}

/// Шаг OFI Конт–Куканова–Стоянова по лучшим уровням: `(тик, лоты)` бида и аска, предыдущий и текущий кадр.
fn ofi_step(b0: (i64, i64), a0: (i64, i64), b1: (i64, i64), a1: (i64, i64)) -> i64 {
    let mut e: i64 = 0;
    if b1.0 >= b0.0 {
        e = e.saturating_add(b1.1);
    }
    if b1.0 <= b0.0 {
        e = e.saturating_sub(b0.1);
    }
    if a1.0 <= a0.0 {
        e = e.saturating_sub(a1.1);
    }
    if a1.0 >= a0.0 {
        e = e.saturating_add(a0.1);
    }
    e
}

/// `(own − opp)/(own + opp) · 10⁴` по `n` лучшим уровням каждой стороны (короче — сколько есть).
fn obi(own: &[(i64, i64)], opp: &[(i64, i64)], n: usize) -> i64 {
    let sum = |side: &[(i64, i64)]| side.iter().take(n).map(|l| i128::from(l.1)).sum::<i128>();
    let (a, b) = (sum(own), sum(opp));
    ratio((a - b) * 10_000, a + b)
}

/// Процентиль размера сделки: нижняя граница `2^k` первой корзины, где накопленное ≥ `⌈p·n/100⌉`.
fn size_percentile(hist: &[u64; SIZE_BUCKETS], n: u64, p: u64) -> i64 {
    if n == 0 {
        return R1_UNDEF;
    }
    let target = (p * n).div_ceil(100);
    let mut cum = 0u64;
    for (k, c) in hist.iter().enumerate() {
        cum += c;
        if cum >= target {
            return 1i64 << k.min(62);
        }
    }
    R1_UNDEF
}

/// Деление с усечением к нулю; нулевой знаменатель — «не определено»; результат не равен `R1_UNDEF`.
fn ratio(num: i128, den: i128) -> i64 {
    if den == 0 {
        return R1_UNDEF;
    }
    (num / den).clamp(i128::from(i64::MIN) + 1, i128::from(i64::MAX)) as i64
}

/// Ориентация «от стены»: у аск-стены знак меняется, «не определено» остаётся им.
fn orient(v: i64, ask_wall: bool) -> i64 {
    if ask_wall && v != R1_UNDEF {
        -v
    } else {
        v
    }
}

#[cfg(test)]
mod tests;
