//! Урезанная лента суток (TK-049, В-185): строки глубины, которые круг не прочтёт, выпадают.
//!
//! Круг видит книгу только через строки ленты. Строка `i` на тике `p` нужна, если `p` лежит в полосе
//! `band` тиков от лучшей своей стороны хоть в одном из состояний книги, пока её `qty` актуален:
//! `s_i` (до строки) … `s_k` (до следующей строки на том же тике `k`, либо конец суток). Сделки и всё,
//! что не обновление глубины, и последняя строка суток остаются всегда. Лента — подпоследовательность
//! исходной, метки те же, окна и снимки строятся по полной ленте. Тики вне полосы, которые круг читает
//! по своим живым заявкам, ловит детектор круга (пересчёт на полной ленте).

use std::collections::HashMap;

use hftbacktest::depth::INVALID_MAX;
use hftbacktest::types::{LOCAL_ASK_DEPTH_EVENT, LOCAL_BID_DEPTH_EVENT};

use super::window_depth::round_half_away;
use super::{EventRows, WindowDepth};
use hftbacktest::types::Event;

/// Минимум на отрезке состояний — итеративное дерево отрезков.
struct MinTree {
    n: usize,
    t: Vec<i64>,
}

impl MinTree {
    fn new(vals: &[i64]) -> Self {
        let n = vals.len();
        let mut t = vec![i64::MAX; 2 * n];
        t[n..].copy_from_slice(vals);
        for i in (1..n).rev() {
            t[i] = t[2 * i].min(t[2 * i + 1]);
        }
        Self { n, t }
    }

    /// Минимум на `[l, r]` включительно.
    fn min(&self, l: usize, r: usize) -> i64 {
        let (mut lo, mut hi) = (l + self.n, r + self.n + 1);
        let mut m = i64::MAX;
        while lo < hi {
            if lo & 1 == 1 {
                m = m.min(self.t[lo]);
                lo += 1;
            }
            if hi & 1 == 1 {
                hi -= 1;
                m = m.min(self.t[hi]);
            }
            lo >>= 1;
            hi >>= 1;
        }
        m
    }
}

const BID: u8 = 1;
const ASK: u8 = 2;

/// Индексы строк, которые остаются в ленте (по возрастанию).
pub fn kept_rows<R: EventRows + ?Sized>(
    events: &R,
    tick_size: f64,
    lot_size: f64,
    band: i64,
) -> Vec<u32> {
    let n = events.len();
    let mut book = WindowDepth::new(tick_size, lot_size);
    let mut side = vec![0u8; n];
    let mut tick = vec![0i64; n];
    // Состояния s_0..s_n: лучший бид как есть; лучший аск с обратным знаком (минимум = максимум аска).
    let mut bid_states = Vec::with_capacity(n + 1);
    let mut neg_ask_states = Vec::with_capacity(n + 1);
    let neg = |a: i64| if a == INVALID_MAX { i64::MIN } else { -a };
    for j in 0..n {
        let (b, a) = book.best_ticks();
        bid_states.push(b);
        neg_ask_states.push(neg(a));
        let ev: Event = events.row(j);
        if ev.is(LOCAL_BID_DEPTH_EVENT) {
            side[j] = BID;
            tick[j] = round_half_away(ev.px / tick_size) as i64;
            book.update_bid_depth(ev.px, ev.qty);
        } else if ev.is(LOCAL_ASK_DEPTH_EVENT) {
            side[j] = ASK;
            tick[j] = round_half_away(ev.px / tick_size) as i64;
            book.update_ask_depth(ev.px, ev.qty);
        }
    }
    let (b, a) = book.best_ticks();
    bid_states.push(b);
    neg_ask_states.push(neg(a));
    let bid_tree = MinTree::new(&bid_states);
    let ask_tree = MinTree::new(&neg_ask_states);
    drop((bid_states, neg_ask_states));

    let mut next_at: HashMap<(u8, i64), usize> = HashMap::new();
    let mut keep = vec![false; n];
    for i in (0..n).rev() {
        let s = side[i];
        if s == 0 {
            keep[i] = true;
            continue;
        }
        let p = tick[i];
        let k = next_at.insert((s, p), i).unwrap_or(n);
        if i + 1 == n {
            keep[i] = true;
            continue;
        }
        keep[i] = if s == BID {
            bid_tree.min(i, k) <= p.saturating_add(band)
        } else {
            let best_ask_max = ask_tree.min(i, k);
            best_ask_max == i64::MIN || -best_ask_max >= p.saturating_sub(band)
        };
    }
    keep.iter()
        .enumerate()
        .filter(|(_, k)| **k)
        .map(|(i, _)| i as u32)
        .collect()
}

/// Вид на урезанную ленту: `EventRows` над исходными строками и индексом оставленных.
pub struct TrimRows<'a, R: EventRows + ?Sized> {
    inner: &'a R,
    kept: &'a [u32],
}

impl<'a, R: EventRows + ?Sized> TrimRows<'a, R> {
    pub fn new(inner: &'a R, kept: &'a [u32]) -> Self {
        Self { inner, kept }
    }
}

impl<R: EventRows + ?Sized> EventRows for TrimRows<'_, R> {
    fn len(&self) -> usize {
        self.kept.len()
    }
    fn row(&self, i: usize) -> Event {
        self.inner.row(self.kept[i] as usize)
    }
    fn row_local_ts(&self, i: usize) -> i64 {
        self.inner.row_local_ts(self.kept[i] as usize)
    }
    fn row_exch_ts(&self, i: usize) -> i64 {
        self.inner.row_exch_ts(self.kept[i] as usize)
    }
    fn skip_to(&self, orig_start: usize) -> usize {
        self.kept.partition_point(|&k| (k as usize) < orig_start)
    }
}

#[cfg(test)]
mod tests;
