//! TK-048 К-4а (`ALPHA_HOLD_INDEX`, заготовка): индекс суток для быстрого пути удержания. Один проход по локальным
//! строкам ленты вместо шести (по кругу на форму): изменения лучших `bid/ask`, история объёма по тику и сделки по
//! тику/стороне. Круг читает значения подписи `hold_input_sig` из индекса и прыгает к следующей значимой строке.
//! Здесь — построение и запросы; встраивание в `fast_hold_scan` — следующий шаг. Сейчас индекс не строится, если в
//! суточной ленте есть строки очистки глубины или метки локальной стороны идут не по возрастанию (`None` — путь
//! прежний).

use super::fast_depth::FastMarketDepth;
use super::fast_hold::{apply_local, finish_handoff, FastHandoff};
use super::{round_half_away, DepthSnapshot, Event};
use hftbacktest::depth::{L2MarketDepth, MarketDepth};
use hftbacktest::types::EXCH_EVENT;
use hftbacktest::types::{
    EXCH_BUY_TRADE_EVENT, EXCH_SELL_TRADE_EVENT, LOCAL_ASK_DEPTH_CLEAR_EVENT,
    LOCAL_ASK_DEPTH_EVENT, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_CLEAR_EVENT,
    LOCAL_BID_DEPTH_EVENT, LOCAL_BID_DEPTH_SNAPSHOT_EVENT, LOCAL_DEPTH_CLEAR_EVENT, LOCAL_EVENT,
    LOCAL_TRADE_EVENT,
};

/// Строк локальной стороны между контрольными снимками книги (назначаемое число; подбор замером на d15 —
/// кандидаты 1 024 / 4 096 / 16 384, TK-048 К-4а §3).
pub const CKPT_ROWS: usize = 4096;

/// Таймеры построения (нс; на итог счёта не влияют): [0] строк ленты в проходе, [1] локальных строк, [2] применение
/// к книге, [3] запись объёма тика, [4] сделки, [5] лучшие цены и учёт строк (последние три — выборка каждой
/// `SAMPLE`-й локальной строки, масштабированная), [6] контрольные снимки книги, [7] сортировки по тику.
pub static BUILD_STAGES: [std::sync::atomic::AtomicU64; 8] =
    [const { std::sync::atomic::AtomicU64::new(0) }; 8];
const SAMPLE: usize = 128;

fn stage_add(i: usize, ns: u128, mul: u64) {
    BUILD_STAGES[i].fetch_add(
        u64::try_from(ns).unwrap_or(u64::MAX).saturating_mul(mul),
        std::sync::atomic::Ordering::Relaxed,
    );
}

/// Устойчивая сортировка по тику подсчётом (CSR): вход уже в порядке строк, поэтому ключ `(tick, row)` сохраняется.
/// Разброс тиков больше линейного от длины — обычная устойчивая сортировка.
fn sort_by_tick<T: Copy>(v: &mut Vec<T>, tick: impl Fn(&T) -> i64) {
    if v.len() < 2 {
        return;
    }
    let (lo, hi) = v.iter().fold((i64::MAX, i64::MIN), |(a, b), x| {
        (a.min(tick(x)), b.max(tick(x)))
    });
    let range = usize::try_from(hi - lo)
        .unwrap_or(usize::MAX)
        .saturating_add(2);
    if range > 4 * v.len() + 65_536 {
        v.sort_by_key(|x| tick(x));
        return;
    }
    let mut start = vec![0u32; range];
    for x in v.iter() {
        start[usize::try_from(tick(x) - lo).unwrap_or(0) + 1] += 1;
    }
    for i in 1..range {
        start[i] += start[i - 1];
    }
    let mut out = v.clone();
    for x in v.iter() {
        let k = usize::try_from(tick(x) - lo).unwrap_or(0);
        out[start[k] as usize] = *x;
        start[k] += 1;
    }
    *v = out;
}

/// Сторона книги / сделки в ключе тика: 0 — bid (покупатели), 1 — ask.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Debug)]
pub enum BookSide {
    Bid = 0,
    Ask = 1,
}

/// Лучшие цены после строки `row` (пишется только при смене любой из двух).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct BestChange {
    pub local_ts: i64,
    pub row: usize,
    pub bid_tick: i64,
    pub ask_tick: i64,
}

/// Обновление объёма тика: `qty` с момента `local_ts`.
#[derive(Clone, Copy, Debug)]
struct QtyAt {
    tick: i64,
    row: usize,
    local_ts: i64,
    qty: f64,
}

/// Сделка локальной стороны на тике: порядок строк ленты сохранён (сумма за шаг считается в этом порядке, как
/// `observe_wall_trades`, — иначе `f64` разойдётся побитно).
#[derive(Clone, Copy, Debug)]
pub struct TradeAt {
    pub tick: i64,
    pub row: usize,
    pub local_ts: i64,
    pub qty: f64,
}

pub struct HoldIdx {
    base: FastMarketDepth,
    best: Vec<BestChange>,
    /// Обновления объёма по сторонам; внутри стороны — по `(tick, row)`.
    qty: [Vec<QtyAt>; 2],
    /// Сделки: [0] — продажи (едят bid), [1] — покупки (едят ask); по `(tick, row)`.
    sells: Vec<TradeAt>,
    buys: Vec<TradeAt>,
    /// Первая строка ленты после последней применённой локальной (конец прохода).
    pub end_row: usize,
    /// Локальные строки: (номер в ленте, `local_ts`) и префиксный максимум `exch_ts` по ним.
    local_rows: Vec<(usize, i64)>,
    pmax: Vec<i64>,
    /// Номера строк, делающих ленту «нечистой» (локальная без биржевой половины, биржевая без локальной).
    unclean: Vec<usize>,
    /// Все локальные сделки: (номер строки, `local_ts`), по порядку ленты.
    trade_rows: Vec<(usize, i64)>,
    /// `ckpts[j]` — книга после первых `j · CKPT_ROWS` локальных строк (`ckpts[0]` — база).
    ckpts: Vec<DepthSnapshot>,
}

impl HoldIdx {
    /// Проход по `rows[base_row..]`, `base_book` — локальная книга до `rows[base_row]`. `None` — очистка глубины
    /// в ленте или `local_ts` убывает.
    pub fn build(
        rows: &[Event],
        base_row: usize,
        base_book: &DepthSnapshot,
        tick: f64,
        lot: f64,
    ) -> Option<Self> {
        let mut book = base_book.build(tick, lot);
        let base = base_book.build(tick, lot);
        let mut best = Vec::new();
        let mut qty: [Vec<QtyAt>; 2] = [Vec::new(), Vec::new()];
        let (mut sells, mut buys) = (Vec::new(), Vec::new());
        let mut prev = i64::MIN;
        let (mut local_rows, mut pmax, mut unclean) = (Vec::new(), Vec::new(), Vec::new());
        let mut trade_rows = Vec::new();
        let mut ckpts = vec![base_book.clone()];
        let n_rows = rows.len().saturating_sub(base_row);
        local_rows.reserve(n_rows);
        pmax.reserve(n_rows);
        for q in &mut qty {
            q.reserve(n_rows / 3);
        }
        let mut last = (book.best_bid_tick(), book.best_ask_tick());
        for (i, ev) in rows.iter().enumerate().skip(base_row) {
            if !ev.is(LOCAL_EVENT) {
                if ev.is(EXCH_EVENT) {
                    unclean.push(i);
                }
                continue;
            }
            if ev.local_ts < prev {
                return None;
            }
            prev = ev.local_ts;
            if !ev.is(EXCH_EVENT) {
                unclean.push(i);
            }
            let sample = local_rows.len() % SAMPLE == 0;
            local_rows.push((i, ev.local_ts));
            pmax.push(
                pmax.last()
                    .map_or(ev.exch_ts, |m: &i64| (*m).max(ev.exch_ts)),
            );
            if ev.is(LOCAL_BID_DEPTH_CLEAR_EVENT)
                || ev.is(LOCAL_ASK_DEPTH_CLEAR_EVENT)
                || ev.is(LOCAL_DEPTH_CLEAR_EVENT)
            {
                return None;
            }
            #[allow(clippy::cast_possible_truncation)]
            let t = round_half_away(ev.px / tick) as i64;
            let t0 = sample.then(std::time::Instant::now);
            let side = if ev.is(LOCAL_BID_DEPTH_EVENT) || ev.is(LOCAL_BID_DEPTH_SNAPSHOT_EVENT) {
                book.update_bid_depth(ev.px, ev.qty, ev.local_ts);
                Some(0)
            } else if ev.is(LOCAL_ASK_DEPTH_EVENT) || ev.is(LOCAL_ASK_DEPTH_SNAPSHOT_EVENT) {
                book.update_ask_depth(ev.px, ev.qty, ev.local_ts);
                Some(1)
            } else {
                None
            };
            let t1 = sample.then(std::time::Instant::now);
            if let Some(sd) = side {
                qty[sd].push(QtyAt {
                    tick: t,
                    row: i,
                    local_ts: ev.local_ts,
                    qty: if sd == 0 {
                        book.bid_qty_at_tick(t)
                    } else {
                        book.ask_qty_at_tick(t)
                    },
                });
            }
            let t2 = sample.then(std::time::Instant::now);
            if ev.is(LOCAL_TRADE_EVENT) {
                trade_rows.push((i, ev.local_ts));
                let tr = TradeAt {
                    tick: t,
                    row: i,
                    local_ts: ev.local_ts,
                    qty: ev.qty,
                };
                if ev.ev & EXCH_SELL_TRADE_EVENT != 0 {
                    sells.push(tr);
                }
                if ev.ev & EXCH_BUY_TRADE_EVENT != 0 {
                    buys.push(tr);
                }
            }
            let t3 = sample.then(std::time::Instant::now);
            if local_rows.len() % CKPT_ROWS == 0 {
                let tc = std::time::Instant::now();
                ckpts.push(DepthSnapshot::of(&book));
                stage_add(6, tc.elapsed().as_nanos(), 1);
            }
            let now = (book.best_bid_tick(), book.best_ask_tick());
            if now != last {
                best.push(BestChange {
                    local_ts: ev.local_ts,
                    row: i,
                    bid_tick: now.0,
                    ask_tick: now.1,
                });
                last = now;
            }
            if let (Some(t0), Some(t1), Some(t2), Some(t3)) = (t0, t1, t2, t3) {
                let m = SAMPLE as u64;
                stage_add(2, (t1 - t0).as_nanos(), m);
                stage_add(3, (t2 - t1).as_nanos(), m);
                stage_add(4, (t3 - t2).as_nanos(), m);
                stage_add(5, t3.elapsed().as_nanos(), m);
            }
        }
        stage_add(0, rows.len().saturating_sub(base_row) as u128, 1);
        stage_add(1, local_rows.len() as u128, 1);
        let ts = std::time::Instant::now();
        for v in &mut qty {
            sort_by_tick(v, |q| q.tick);
        }
        sort_by_tick(&mut sells, |x| x.tick);
        sort_by_tick(&mut buys, |x| x.tick);
        stage_add(7, ts.elapsed().as_nanos(), 1);
        Some(Self {
            base,
            best,
            qty,
            sells,
            buys,
            end_row: rows.len(),
            local_rows,
            pmax,
            unclean,
            trade_rows,
            ckpts,
        })
    }

    /// Книга перед строкой `row` ленты `all` (локальная сторона): ближайший контрольный снимок + повтор строк.
    pub fn book_before(
        &self,
        all: &[Event],
        row: usize,
        tick_size: f64,
        lot_size: f64,
    ) -> DepthSnapshot {
        let k = self.local_rows.partition_point(|&(r, _)| r < row);
        let c = (k / CKPT_ROWS).min(self.ckpts.len() - 1);
        let mut book = self.ckpts[c].build(tick_size, lot_size);
        for &(r, _) in &self.local_rows[c * CKPT_ROWS..k] {
            apply_local(&mut book, &all[r]);
        }
        DepthSnapshot::of(&book)
    }

    /// Состояние для нового движка на `t` для круга, начавшегося с курсора `cur` (первая строка ленты, которую круг
    /// ещё не видел): то же, что `HoldTracker::handoff` после `advance_to(t)`, но книга берётся из ближайшего
    /// контрольного снимка + повтор ≤ `CKPT_ROWS` строк. `None` — как у трекера (нечистая лента на `[cur, курсор)` или
    /// биржа отстаёт). Индекс — в нумерации ленты `all`; строки круга `rows` — её срез, начинающийся с `all[off]`
    /// (хвост или буфер), `cur` — номер в `rows` (не раньше базы индекса); в `FastHandoff` номера — в `rows`.
    #[allow(clippy::too_many_arguments)]
    pub fn handoff(
        &self,
        all: &[Event],
        rows: &[Event],
        off: usize,
        cur: usize,
        t: i64,
        tick_size: f64,
        lot_size: f64,
    ) -> Option<FastHandoff> {
        let k = self.local_rows.partition_point(|&(_, ts)| ts <= t);
        let lcur = self.local_rows.get(k).map_or(all.len(), |&(r, _)| r);
        let lcur_rows = lcur.checked_sub(off).filter(|&l| l <= rows.len())?;
        let lo = self.unclean.partition_point(|&r| r < cur + off);
        if self.unclean.get(lo).is_some_and(|&r| r < lcur) {
            return None;
        }
        if k > 0 && self.pmax[k - 1] > t {
            return None;
        }
        let c = (k / CKPT_ROWS).min(self.ckpts.len() - 1);
        let mut book = self.ckpts[c].build(tick_size, lot_size);
        for &(r, _) in &self.local_rows[c * CKPT_ROWS..k] {
            apply_local(&mut book, &all[r]);
        }
        Some(finish_handoff(
            rows,
            lcur_rows,
            DepthSnapshot::of(&book),
            t,
            tick_size,
            lot_size,
        ))
    }

    /// Ближайшая локальная строка строго после `t`.
    pub fn next_row_ts(&self, t: i64) -> Option<i64> {
        self.local_rows
            .get(self.local_rows.partition_point(|&(_, ts)| ts <= t))
            .map(|&(_, ts)| ts)
    }

    /// Ближайшая строка после `t`, меняющая вход подписи удержания: лучшие цены, объём тика уровня своей стороны
    /// (лонг — bid) или сделка на тике уровня (лонг — продажи).
    pub fn next_sig_ts(&self, long: bool, level_tick: i64, t: i64) -> Option<i64> {
        let side = if long { BookSide::Bid } else { BookSide::Ask };
        [
            self.next_best_change(t),
            self.next_qty_change(side, level_tick, t),
            self.next_trade(long, level_tick, t),
        ]
        .into_iter()
        .flatten()
        .min()
    }

    /// Локальные сделки с `t0 < local_ts ≤ t1` (все тики), в порядке ленты — вход `observe_wall_trades`.
    pub fn trade_events(&self, all: &[Event], t0: i64, t1: i64, out: &mut Vec<Event>) {
        let a = self.trade_rows.partition_point(|&(_, ts)| ts <= t0);
        let b = self.trade_rows.partition_point(|&(_, ts)| ts <= t1);
        out.extend(self.trade_rows[a..b].iter().map(|&(r, _)| all[r].clone()));
    }

    /// Лучшие `(bid, ask)` на метке `t`: состояние после последней локальной строки с `local_ts ≤ t`.
    pub fn best_at(&self, t: i64) -> (i64, i64) {
        let n = self.best.partition_point(|c| c.local_ts <= t);
        match n.checked_sub(1) {
            Some(i) => (self.best[i].bid_tick, self.best[i].ask_tick),
            None => (self.base.best_bid_tick(), self.base.best_ask_tick()),
        }
    }

    /// Следующая смена лучших цен строго после `t` (метка локальной стороны).
    pub fn next_best_change(&self, t: i64) -> Option<i64> {
        self.best
            .get(self.best.partition_point(|c| c.local_ts <= t))
            .map(|c| c.local_ts)
    }

    /// Объём тика на метке `t` (последнее обновление с `local_ts ≤ t`; нет обновлений — объём базовой книги).
    pub fn qty_at(&self, side: BookSide, tick: i64, t: i64) -> f64 {
        let v = &self.qty[side as usize];
        let lo = v.partition_point(|q| q.tick < tick);
        let hi = v.partition_point(|q| q.tick <= tick);
        // Внутри тика строки идут по номеру, а метки не убывают ⇒ `local_ts ≤ t` — префикс.
        let n = v[lo..hi].partition_point(|q| q.local_ts <= t);
        match n.checked_sub(1) {
            Some(i) => v[lo + i].qty,
            None => match side {
                BookSide::Bid => self.base.bid_qty_at_tick(tick),
                BookSide::Ask => self.base.ask_qty_at_tick(tick),
            },
        }
    }

    /// Ближайшая строка с изменением объёма тика строго после `t` (метка локальной стороны).
    pub fn next_qty_change(&self, side: BookSide, tick: i64, t: i64) -> Option<i64> {
        let v = &self.qty[side as usize];
        let lo = v.partition_point(|q| q.tick < tick);
        let hi = v.partition_point(|q| q.tick <= tick);
        let n = v[lo..hi].partition_point(|q| q.local_ts <= t);
        v[lo..hi].get(n).map(|q| q.local_ts)
    }

    /// Сделки на тике (продажи — `want_sell`) с `t0 < local_ts ≤ t1`, в порядке ленты.
    pub fn trades_in(&self, want_sell: bool, tick: i64, t0: i64, t1: i64) -> &[TradeAt] {
        let v = if want_sell { &self.sells } else { &self.buys };
        let lo = v.partition_point(|x| x.tick < tick);
        let hi = v.partition_point(|x| x.tick <= tick);
        let s = &v[lo..hi];
        let a = s.partition_point(|x| x.local_ts <= t0);
        let b = s.partition_point(|x| x.local_ts <= t1);
        &s[a..b]
    }

    /// Ближайшая сделка на тике строго после `t`.
    pub fn next_trade(&self, want_sell: bool, tick: i64, t: i64) -> Option<i64> {
        self.trades_in(want_sell, tick, t, i64::MAX)
            .first()
            .map(|x| x.local_ts)
    }
}
