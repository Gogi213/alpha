//! TK-515: кодировщик L1 — сырые строки книги (et, fu, price, qty, flags) -> L1-файл ядра (те же 33 Б, flags: бит0 снимок, бит1 бид,
//! бит2 «уровень вышел из L1, но не удалён из книги»). Книга и группы — как в rs/src/book.rs (Book, Rows::group_end, snap_start).
//!
//! Правила: после конца группы (строки подряд с одинаковыми (snap, fu, et), снимочные подряд — одна группа) читается лучшая пара книги;
//! есть обе стороны и bid < ask — состояние L1 = (bid, bq, ask, aq), иначе публикации нет (L1 остаётся прежним). Запись только при изменении:
//! по стороне — смена цены: строка старой цены с qty=0 (бит2=1, если уровень ещё в полной книге) и строка новой цены; та же цена, другая qty — одна строка.
//! Сначала бид, потом аск; et/fu — последней строки группы, бит0 сброшен. Начало часа: снимок текущего L1 (две строки fu=-1, бит0; бид|бит1),
//! et = et первой строки часа; нет L1 к началу часа — снимок нового L1 при первой публикации часа.
use crate::push_row;
use std::collections::HashMap;
use std::hash::{BuildHasherDefault, Hasher};

/// Хеш цены (биты f64): умножение + свёртка старших бит вниз (круглые цены имеют нулевой хвост мантиссы).
#[derive(Default)]
pub struct Fx(u64);

impl Hasher for Fx {
    fn finish(&self) -> u64 {
        self.0
    }
    fn write(&mut self, b: &[u8]) {
        for &x in b {
            self.write_u64(x as u64);
        }
    }
    fn write_u64(&mut self, v: u64) {
        let x = (self.0 ^ v).wrapping_mul(0x9E37_79B9_7F4A_7C15);
        self.0 = x ^ (x >> 32);
    }
}

/// Сторона книги: хеш «биты цены -> qty» (все уровни, правка за O(1)) + `top` — лучшие уровни по порядку (лучший первым). `top` — всегда точный префикс
/// стороны (лучшие `top.len()` уровней), пуст только при пустой стороне; глубокие правки (хуже `top.last()`) идут в хеш и не двигают массив.
/// Опустел `top` при живых уровнях — добор из хеша (отбор TOP лучших). Порядок: у бидов лучше больший ключ (цены > 0: порядок бит = порядок цен), у асков меньший.
const TOP: usize = 32;

struct Side {
    bid: bool,
    map: HashMap<u64, f64, BuildHasherDefault<Fx>>,
    top: Vec<u64>,
}

impl Side {
    fn new(bid: bool) -> Self {
        Side { bid, map: HashMap::default(), top: Vec::with_capacity(TOP + 1) }
    }

    /// Порядок: меньше = лучше.
    fn ord(&self, k: u64) -> u64 {
        if self.bid { !k } else { k }
    }

    fn clear(&mut self) {
        self.map.clear();
        self.top.clear();
    }

    fn set(&mut self, key: u64, q: f64) {
        if q == 0.0 {
            if self.map.remove(&key).is_some() {
                let (o, bid) = (self.ord(key), self.bid);
                if let Ok(i) = self.top.binary_search_by_key(&o, |&k| if bid { !k } else { k }) {
                    self.top.remove(i);
                    if self.top.is_empty() && !self.map.is_empty() {
                        self.refill();
                    }
                }
            }
            return;
        }
        if self.map.insert(key, q).is_some() {
            return;
        }
        let Some(&last) = self.top.last() else {
            self.top.push(key);
            return;
        };
        let o = self.ord(key);
        if o < self.ord(last) {
            let bid = self.bid;
            let pos = self.top.partition_point(|&k| (if bid { !k } else { k }) < o);
            self.top.insert(pos, key);
            self.top.truncate(TOP);
        } else if self.top.len() + 1 == self.map.len() && self.top.len() < TOP {
            self.top.push(key);
        }
    }

    /// `top` := TOP лучших уровней хеша по порядку.
    fn refill(&mut self) {
        let bid = self.bid;
        let o = move |k: &u64| if bid { !*k } else { *k };
        self.top.clear();
        self.top.extend(self.map.keys().copied());
        if self.top.len() > TOP {
            self.top.select_nth_unstable_by_key(TOP - 1, o);
            self.top.truncate(TOP);
        }
        self.top.sort_unstable_by_key(o);
    }

    fn qty(&self, key: u64) -> f64 {
        self.map.get(&key).copied().unwrap_or(0.0)
    }

    fn best(&self) -> Option<(u64, f64)> {
        let &k = self.top.first()?;
        Some((k, self.map[&k]))
    }

    /// Накопленные строки (порядок прихода) -> в сторону: последняя строка ключа побеждает, qty=0 удаляет. Пустая сторона — сортировка и разовая сборка, иначе по одной.
    fn merge(&mut self, p: &mut Vec<(u64, f64)>) {
        if !self.map.is_empty() {
            for &(k, q) in p.iter() {
                self.set(k, q);
            }
            p.clear();
        } else if !p.is_empty() {
            p.sort_by(|a, b| a.0.cmp(&b.0));
            let mut w = 0;
            let mut i = 0;
            while i < p.len() {
                let mut j = i;
                while j + 1 < p.len() && p[j + 1].0 == p[i].0 {
                    j += 1;
                }
                if p[j].1 != 0.0 {
                    p[w] = p[j];
                    w += 1;
                }
                i = j + 1;
            }
            p.truncate(w);
            self.map.extend(p.iter().copied());
            self.top.clear();
            if self.bid {
                self.top.extend(p.iter().rev().take(TOP).map(|x| x.0));
            } else {
                self.top.extend(p.iter().take(TOP).map(|x| x.0));
            }
            p.clear();
        }
    }
}

/// Книга как rs/src/book.rs: абсолютные количества, qty=0 удаляет уровень.
pub struct Book {
    bids: Side,
    asks: Side,
    /// После `clear` (начало снимка) строки копятся в порядке прихода, книга собирается разом при первом чтении или правке не из снимка.
    loading: bool,
    pb: Vec<(u64, f64)>,
    pa: Vec<(u64, f64)>,
}

impl Default for Book {
    fn default() -> Self {
        Book { bids: Side::new(true), asks: Side::new(false), loading: false, pb: Vec::new(), pa: Vec::new() }
    }
}

impl Book {
    pub fn clear(&mut self) {
        self.bids.clear();
        self.asks.clear();
        self.pb.clear();
        self.pa.clear();
        self.loading = true;
    }

    pub fn set(&mut self, bid: bool, price: f64, q: f64) {
        let key = price.to_bits();
        if self.loading {
            if bid { self.pb.push((key, q)) } else { self.pa.push((key, q)) }
            return;
        }
        if bid { self.bids.set(key, q) } else { self.asks.set(key, q) }
    }

    /// Закончить накопление снимка: собрать книгу.
    pub fn flush(&mut self) {
        if self.loading {
            self.bids.merge(&mut self.pb);
            self.asks.merge(&mut self.pa);
            self.loading = false;
        }
    }

    pub fn qty(&mut self, bid: bool, price: f64) -> f64 {
        self.flush();
        (if bid { &self.bids } else { &self.asks }).qty(price.to_bits())
    }

    /// Лучшая пара `(бид, кол-во, аск, кол-во)`.
    pub fn best(&mut self) -> Option<(f64, f64, f64, f64)> {
        self.flush();
        let (kb, bq) = self.bids.best()?;
        let (ka, aq) = self.asks.best()?;
        Some((f64::from_bits(kb), bq, f64::from_bits(ka), aq))
    }
}

#[derive(Clone, Copy, PartialEq, Debug)]
struct L1 {
    bid: f64,
    bq: f64,
    ask: f64,
    aq: f64,
}

/// Строка после `i` — конец группы (Rows::group_end при известной следующей строке): `(snap, fu, et)` текущей и следующей.
/// `seq` (TK-522, Hyperliquid): подряд идущие снимки с разными `(fu, et)` — разные группы, каждый заменяет книгу целиком.
fn group_end(cur: (bool, i64, i64), next: (bool, i64, i64), seq: bool) -> bool {
    if cur == next {
        return false;
    }
    seq || !(cur.0 && next.0)
}

pub struct L1Enc {
    book: Book,
    last: Option<L1>,
    /// Предыдущая строка часа `(et, fu, snap)`; `None` — следующая строка первая в часе.
    prev: Option<(i64, i64, bool)>,
    hour_et: i64,
    need_snap: bool,
    /// TK-522: первый снимок суток метится временем публикации, а не первой строки часа (источник без снимка в начале: книга набирается позже).
    pub_stamp: bool,
    /// TK-522: источник шлёт полные снимки подряд (Hyperliquid, 20+20 уровней раз в 2–5 с): каждый снимок очищает книгу (`P40_L1_SNAPSEQ=1`).
    snap_seq: bool,
}

impl Default for L1Enc {
    fn default() -> Self {
        Self::new()
    }
}

impl L1Enc {
    pub fn new() -> Self {
        L1Enc { book: Book::default(), last: None, prev: None, hour_et: 0, need_snap: true, pub_stamp: false, snap_seq: false }
    }

    /// Энкодер с полными снимками подряд: каждый новый снимок (смена `(fu, et)`) начинает книгу заново.
    pub fn snap_seq(mut self) -> Self {
        self.snap_seq = true;
        self
    }

    /// Энкодер суток с переходом книги через часы (`P40_L1_CARRY=1`): `new` + метка первого снимка по публикации.
    pub fn carry() -> Self {
        L1Enc { pub_stamp: true, ..Self::new() }
    }

    /// Следующая строка источника в порядке файла; `fl`: бит0 снимок, бит1 бид (как в 33-байтном формате ядра).
    pub fn row(&mut self, out: &mut Vec<u8>, et: i64, fu: i64, px: f64, qt: f64, fl: u8) {
        let snap = fl & 1 != 0;
        let snap_start = match self.prev {
            Some((pe, pf, ps)) => {
                if group_end((ps, pf, pe), (snap, fu, et), self.snap_seq) {
                    self.publish(out, pe, pf);
                }
                snap && (!ps || (self.snap_seq && (pe, pf) != (et, fu)))
            }
            None => {
                self.hour_et = et;
                match self.last {
                    Some(l) => {
                        write_snapshot(out, et, l);
                        self.need_snap = false;
                    }
                    None => self.need_snap = true,
                }
                snap
            }
        };
        if snap_start {
            self.book.clear();
        }
        self.book.set(fl & 2 != 0, px, qt);
        self.prev = Some((et, fu, snap));
    }

    /// Конец часа: последняя строка закрывает группу; следующий час начинается снимком текущего L1.
    pub fn end_hour(&mut self, out: &mut Vec<u8>) {
        if let Some((pe, pf, _)) = self.prev.take() {
            self.publish(out, pe, pf);
        }
    }

    fn publish(&mut self, out: &mut Vec<u8>, et: i64, fu: i64) {
        let Some((bb, bq, aa, aq)) = self.book.best() else { return };
        if bb >= aa {
            return;
        }
        let cur = L1 { bid: bb, bq, ask: aa, aq };
        let old = match (self.need_snap, self.last) {
            (false, Some(o)) => o,
            _ => {
                write_snapshot(out, if self.pub_stamp { et } else { self.hour_et }, cur);
                self.need_snap = false;
                self.last = Some(cur);
                return;
            }
        };
        if cur == old {
            return;
        }
        for (bid, op, oq, np, nq) in [(true, old.bid, old.bq, cur.bid, cur.bq), (false, old.ask, old.aq, cur.ask, cur.aq)] {
            let side = (bid as u8) << 1;
            if np != op {
                let alive = (self.book.qty(bid, op) > 0.0) as u8;
                push_row(out, et, fu, op, 0.0, side | alive << 2);
                push_row(out, et, fu, np, nq, side);
            } else if nq != oq {
                push_row(out, et, fu, np, nq, side);
            }
        }
        self.last = Some(cur);
    }
}

fn write_snapshot(out: &mut Vec<u8>, et: i64, l: L1) {
    push_row(out, et, -1, l.bid, l.bq, 3);
    push_row(out, et, -1, l.ask, l.aq, 1);
}

#[cfg(test)]
mod tests {
    use super::*;

    type R = (i64, i64, f64, f64, u8);

    fn rows(b: &[u8]) -> Vec<R> {
        b.chunks_exact(33)
            .map(|r| {
                (
                    i64::from_le_bytes(r[0..8].try_into().unwrap()),
                    i64::from_le_bytes(r[8..16].try_into().unwrap()),
                    f64::from_le_bytes(r[16..24].try_into().unwrap()),
                    f64::from_le_bytes(r[24..32].try_into().unwrap()),
                    r[32],
                )
            })
            .collect()
    }

    const SB: u8 = 3; // снимок, бид
    const SA: u8 = 1; // снимок, аск
    const B: u8 = 2;
    const A: u8 = 0;

    /// Снимок 100/101 + последующие события; возвращает энкодер и вывод после снимка.
    fn start() -> (L1Enc, Vec<u8>) {
        let mut e = L1Enc::new();
        let mut o = Vec::new();
        e.row(&mut o, 10, -1, 100.0, 5.0, SB);
        e.row(&mut o, 10, -1, 99.0, 7.0, SB);
        e.row(&mut o, 10, -1, 101.0, 4.0, SA);
        e.row(&mut o, 10, -1, 102.0, 8.0, SA);
        (e, o)
    }

    #[test]
    fn hour_start_snapshot_on_first_publication() {
        let (mut e, mut o) = start();
        e.row(&mut o, 20, 1, 100.0, 6.0, B); // закрывает группу снимка
        assert_eq!(rows(&o), vec![(10, -1, 100.0, 5.0, 3), (10, -1, 101.0, 4.0, 1)]);
        e.end_hour(&mut o); // qty бида 5 -> 6: изменение
        assert_eq!(rows(&o)[2..], [(20, 1, 100.0, 6.0, B)]);
    }

    #[test]
    fn same_price_other_qty_one_row() {
        let (mut e, mut o) = start();
        e.row(&mut o, 20, 1, 101.0, 9.0, A);
        e.end_hour(&mut o);
        assert_eq!(rows(&o)[2..], [(20, 1, 101.0, 9.0, A)]);
    }

    #[test]
    fn new_better_price_old_level_alive_bit2() {
        let (mut e, mut o) = start();
        e.row(&mut o, 20, 1, 100.5, 3.0, B); // новый лучший бид выше, 100.0 остаётся в книге
        e.end_hour(&mut o);
        assert_eq!(rows(&o)[2..], [(20, 1, 100.0, 0.0, B | 4), (20, 1, 100.5, 3.0, B)]);
    }

    #[test]
    fn best_removed_bit2_zero() {
        let (mut e, mut o) = start();
        e.row(&mut o, 20, 1, 100.0, 0.0, B); // лучший бид удалён, следующий 99
        e.end_hour(&mut o);
        assert_eq!(rows(&o)[2..], [(20, 1, 100.0, 0.0, B), (20, 1, 99.0, 7.0, B)]);
    }

    #[test]
    fn bid_first_then_ask_one_group() {
        let (mut e, mut o) = start();
        // одна группа (одинаковые fu, et): бид и аск меняются вместе; порядок записи — бид, потом аск
        e.row(&mut o, 20, 1, 101.0, 0.0, A);
        e.row(&mut o, 20, 1, 100.0, 1.0, B);
        e.end_hour(&mut o);
        assert_eq!(
            rows(&o)[2..],
            [(20, 1, 100.0, 1.0, B), (20, 1, 101.0, 0.0, A), (20, 1, 102.0, 8.0, A)]
        );
    }

    #[test]
    fn crossed_not_published_and_unchanged_not_written() {
        let (mut e, mut o) = start();
        e.row(&mut o, 20, 1, 101.0, 2.0, B); // бид 101 >= аск 101: пересечение
        e.row(&mut o, 30, 2, 101.0, 0.0, B); // убрали; состояние как прежнее -> записи нет
        e.row(&mut o, 40, 3, 99.0, 8.0, B); // не лучший уровень: L1 тот же
        e.end_hour(&mut o);
        assert_eq!(rows(&o).len(), 2);
    }

    #[test]
    fn crossed_then_resolved_writes_change() {
        let (mut e, mut o) = start();
        e.row(&mut o, 20, 1, 101.0, 2.0, B); // пересечение
        e.row(&mut o, 30, 2, 101.0, 0.0, A); // аск 101 удалён: бид 101 < аск 102 -> новый L1
        e.end_hour(&mut o);
        let r = rows(&o);
        assert_eq!(r[2..], [(30, 2, 100.0, 0.0, B | 4), (30, 2, 101.0, 2.0, B), (30, 2, 101.0, 0.0, A), (30, 2, 102.0, 8.0, A)]);
    }

    #[test]
    fn no_side_not_published() {
        let mut e = L1Enc::new();
        let mut o = Vec::new();
        e.row(&mut o, 10, -1, 100.0, 5.0, SB);
        e.end_hour(&mut o);
        assert!(o.is_empty());
    }

    #[test]
    fn next_hour_starts_with_snapshot_of_carried_l1() {
        let (mut e, mut o) = start();
        e.row(&mut o, 20, 1, 100.0, 6.0, B);
        e.end_hour(&mut o);
        let mut o2 = Vec::new();
        e.row(&mut o2, 5000, 2, 101.0, 4.5, A); // первая строка нового часа: снимок прежнего L1, затем изменение
        e.end_hour(&mut o2);
        assert_eq!(
            rows(&o2),
            vec![(5000, -1, 100.0, 6.0, 3), (5000, -1, 101.0, 4.0, 1), (5000, 2, 101.0, 4.5, A)]
        );
    }

    #[test]
    fn midhour_snapshot_series_is_ordinary_change() {
        let (mut e, mut o) = start();
        e.row(&mut o, 20, 1, 100.0, 6.0, B);
        // снимок посреди часа: новая книга 98/103 (старый бид 100 исчез из книги целиком)
        e.row(&mut o, 30, -1, 98.0, 2.0, SB);
        e.row(&mut o, 30, -1, 103.0, 3.0, SA);
        e.row(&mut o, 40, 5, 103.0, 3.5, A);
        e.end_hour(&mut o);
        let r = rows(&o);
        assert_eq!(r[2], (20, 1, 100.0, 6.0, B));
        assert_eq!(
            r[3..],
            [
                (30, -1, 100.0, 0.0, B), // реально исчез: бит2=0
                (30, -1, 98.0, 2.0, B),
                (30, -1, 101.0, 0.0, A),
                (30, -1, 103.0, 3.0, A),
                (40, 5, 103.0, 3.5, A),
            ]
        );
        assert!(r[3..].iter().all(|x| x.4 & 1 == 0));
    }

    /// TK-522 (Hyperliquid): снимки подряд с разными (fu, et) — каждый заменяет книгу; без флага стоящий уровень прежнего снимка даёт crossed.
    #[test]
    fn snapshot_series_with_snap_seq_replaces_book() {
        let feed = |e: &mut L1Enc, o: &mut Vec<u8>| {
            // снимок 1: бид 100, аск 101; снимок 2 (другой et/fu): бид 97, аск 98 (рынок ушёл вниз)
            e.row(o, 10, 1, 100.0, 5.0, SB);
            e.row(o, 10, 1, 101.0, 4.0, SA);
            e.row(o, 20, 2, 97.0, 3.0, SB);
            e.row(o, 20, 2, 98.0, 2.0, SA);
            e.end_hour(o);
        };
        let (mut e, mut o) = (L1Enc::new().snap_seq(), Vec::new());
        feed(&mut e, &mut o);
        let r = rows(&o);
        assert_eq!(r[..2], [(10, -1, 100.0, 5.0, 3), (10, -1, 101.0, 4.0, 1)]);
        // второй снимок: 100/101 заменены 97/98 (старые убраны, qty=0), новая пара пришла
        assert!(r.iter().any(|x| x.2 == 97.0 && x.3 == 3.0 && x.4 & 2 != 0));
        assert!(r.iter().any(|x| x.2 == 98.0 && x.3 == 2.0 && x.4 & 2 == 0));
        // без флага снимки склеены в одну группу: книга 100/97 бид, 98/101 аск -> crossed (100 >= 98), второго L1 нет
        let (mut e, mut o) = (L1Enc::new(), Vec::new());
        feed(&mut e, &mut o);
        assert_eq!(rows(&o).len(), 0);
    }

    /// E-21 (OKX): час без снимка (только дельты) после посева книги часом прошлых суток начинается её L1, а не набором тронутых уровней.
    #[test]
    fn carry_seed_then_delta_only_hour_starts_from_seeded_book() {
        let mut e = L1Enc::carry();
        let mut sink = Vec::new();
        for (px, q, fl) in [(100.0, 5.0, SB), (99.0, 7.0, SB), (101.0, 4.0, SA), (102.0, 8.0, SA)] {
            e.row(&mut sink, 10, -1, px, q, fl);
        }
        e.end_hour(&mut sink); // посев: конец прошлого часа
        let mut o = Vec::new();
        e.row(&mut o, 50, 7, 100.0, 0.0, B); // час без снимка: лучший бид убран -> следующий 99, а не «лучший из тронутых»
        e.row(&mut o, 60, 8, 103.0, 1.0, A); // дальний аск: L1 прежний
        e.end_hour(&mut o);
        assert_eq!(rows(&o), vec![(50, -1, 100.0, 5.0, 3), (50, -1, 101.0, 4.0, 1), (50, 7, 100.0, 0.0, B), (50, 7, 99.0, 7.0, B)]);
        // без посева тот же час: книга из двух тронутых уровней, лучший аск 103 при пустом бида -> L1 нет вовсе (артефакт E-21)
        let (mut e, mut o) = (L1Enc::carry(), Vec::new());
        e.row(&mut o, 50, 7, 100.0, 0.0, B);
        e.row(&mut o, 60, 8, 103.0, 1.0, A);
        e.end_hour(&mut o);
        assert!(rows(&o).is_empty());
    }

    /// Свойство: воспроизведение L1-файла (qty=0 — убрать, бит2 игнорируется) на концах групп даёт то же L1, что полная книга.
    #[test]
    fn replay_equals_full_book_random() {
        let mut s = 12345u64;
        let mut rnd = |n: u64| {
            s = s.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            (s >> 33) % n
        };
        let mut e = L1Enc::new();
        let mut o = Vec::new();
        let mut full = Book::default();
        let mut exp: Vec<(i64, [f64; 4])> = Vec::new(); // ожидаемое L1 на концах групп (только изменения)
        let mut lastl1: Option<[f64; 4]> = None;
        for i in 0..4000i64 {
            let n = 1 + rnd(3);
            for _ in 0..n {
                let bid = rnd(2) == 0;
                let px = (if bid { 95 + rnd(8) } else { 98 + rnd(8) }) as f64;
                let q = if rnd(3) == 0 { 0.0 } else { 1.0 + rnd(5) as f64 };
                full.set(bid, px, q);
                e.row(&mut o, i, i, px, q, (bid as u8) << 1);
            }
            if let Some((b, bq, a, aq)) = full.best() {
                if b < a {
                    let l = [b, bq, a, aq];
                    if lastl1 != Some(l) {
                        exp.push((i, l));
                        lastl1 = Some(l);
                    }
                }
            }
        }
        e.end_hour(&mut o);
        // воспроизведение
        let mut rb = Book::default();
        let mut got: Vec<(i64, [f64; 4])> = Vec::new();
        let r = rows(&o);
        let mut k = 0;
        while k < r.len() {
            let et = r[k].0;
            while k < r.len() && r[k].0 == et {
                rb.set(r[k].4 & 2 != 0, r[k].2, r[k].3);
                k += 1;
            }
            let (b, bq, a, aq) = rb.best().unwrap();
            got.push((et, [b, bq, a, aq]));
        }
        assert!(!exp.is_empty());
        // первая запись — снимок часа: et первой строки часа (0), далее время концов групп
        assert_eq!(got.iter().map(|x| x.1).collect::<Vec<_>>(), exp.iter().map(|x| x.1).collect::<Vec<_>>());
        assert_eq!(got[0].0, 0);
        assert_eq!(got[1..].iter().map(|x| x.0).collect::<Vec<_>>(), exp[1..].iter().map(|x| x.0).collect::<Vec<_>>());
    }

    /// Массив уровней == эталон (карта + перебор): best и qty после случайных set, включая удаление отсутствующего, пустую сторону и clear.
    #[test]
    fn book_matches_map_reference() {
        use std::collections::HashMap;
        let mut s = 777u64;
        let mut rnd = |n: u64| {
            s = s.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            (s >> 33) % n
        };
        let mut b = Book::default();
        let (mut rb, mut ra): (HashMap<u64, f64>, HashMap<u64, f64>) = (HashMap::new(), HashMap::new());
        for i in 0..200_000u32 {
            if i % 50_000 == 49_999 {
                b.clear();
                rb.clear();
                ra.clear();
            }
            let bid = rnd(2) == 0;
            let px = 0.5 + rnd(300) as f64 * 0.001; // плотная сетка: много попаданий в существующие уровни
            let q = if rnd(3) == 0 { 0.0 } else { 0.25 + rnd(9) as f64 };
            b.set(bid, px, q);
            let m = if bid { &mut rb } else { &mut ra };
            if q == 0.0 {
                m.remove(&px.to_bits());
            } else {
                m.insert(px.to_bits(), q);
            }
            let probe = 0.5 + rnd(300) as f64 * 0.001;
            assert_eq!(b.qty(true, probe), rb.get(&probe.to_bits()).copied().unwrap_or(0.0));
            assert_eq!(b.qty(false, probe), ra.get(&probe.to_bits()).copied().unwrap_or(0.0));
            let want = match (rb.keys().max(), ra.keys().min()) {
                (Some(&kb), Some(&ka)) => Some((f64::from_bits(kb), rb[&kb], f64::from_bits(ka), ra[&ka])),
                _ => None,
            };
            assert_eq!(b.best(), want);
        }
    }

    /// Глубокая книга (> TOP уровней): снятие лучших по одному до пустоты, вставка лучше/хуже границы `top` — best == эталон (добор из хеша).
    #[test]
    fn book_deep_sweep_matches_reference() {
        let mut b = Book::default();
        let n = 3 * TOP as u64 + 7;
        for i in 0..n {
            b.set(true, 1.0 + i as f64 * 0.01, 1.0 + i as f64);
            b.set(false, 100.0 + i as f64 * 0.01, 2.0 + i as f64);
        }
        for step in 0..n {
            // бид: лучший — самый дорогой из оставшихся; аск — самый дешёвый
            let (eb, ea) = (1.0 + (n - 1 - step) as f64 * 0.01, 100.0 + step as f64 * 0.01);
            let got = b.best().unwrap();
            assert_eq!((got.0, got.2), (eb, ea), "шаг {step}");
            assert_eq!((got.1, got.3), (n as f64 - step as f64, 2.0 + step as f64));
            b.set(true, eb, 0.0);
            b.set(false, ea, 0.0);
            if step % 5 == 0 {
                // вставка глубоко и вплотную к границе: не должна менять best
                b.set(true, 0.5 + step as f64 * 0.001, 9.0);
                b.set(false, 200.0 + step as f64 * 0.001, 9.0);
            }
        }
        // остались только глубокие вставки
        let got = b.best().unwrap();
        let smax = ((n - 1) / 5) * 5;
        assert_eq!((got.0, got.1, got.2, got.3), (0.5 + smax as f64 * 0.001, 9.0, 200.0, 9.0));
    }

    /// Пакетная загрузка после clear (без чтений между строками, с повторами ключа и qty=0) == построчное применение к эталону.
    #[test]
    fn book_batch_load_matches_reference() {
        use std::collections::HashMap;
        let mut s = 4242u64;
        let mut rnd = |n: u64| {
            s = s.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            (s >> 33) % n
        };
        let mut b = Book::default();
        for round in 0..2000u32 {
            b.clear();
            let (mut rb, mut ra): (HashMap<u64, f64>, HashMap<u64, f64>) = (HashMap::new(), HashMap::new());
            for _ in 0..rnd(60) {
                let bid = rnd(2) == 0;
                let px = 0.5 + rnd(40) as f64 * 0.001;
                let q = if rnd(4) == 0 { 0.0 } else { 0.25 + rnd(9) as f64 };
                b.set(bid, px, q);
                let m = if bid { &mut rb } else { &mut ra };
                if q == 0.0 {
                    m.remove(&px.to_bits());
                } else {
                    m.insert(px.to_bits(), q);
                }
            }
            if round % 3 == 0 {
                let want = match (rb.keys().max(), ra.keys().min()) {
                    (Some(&kb), Some(&ka)) => Some((f64::from_bits(kb), rb[&kb], f64::from_bits(ka), ra[&ka])),
                    _ => None,
                };
                assert_eq!(b.best(), want);
            }
            for k in 0..45 {
                let probe = 0.5 + k as f64 * 0.001;
                assert_eq!(b.qty(true, probe), rb.get(&probe.to_bits()).copied().unwrap_or(0.0));
                assert_eq!(b.qty(false, probe), ra.get(&probe.to_bits()).copied().unwrap_or(0.0));
            }
            // правка после загрузки идёт напрямую и сохраняет порядок
            b.set(true, 0.51, 7.0);
            rb.insert(0.51f64.to_bits(), 7.0);
            assert_eq!(b.qty(true, 0.51), 7.0);
            let want = match (rb.keys().max(), ra.keys().min()) {
                (Some(&kb), Some(&ka)) => Some((f64::from_bits(kb), rb[&kb], f64::from_bits(ka), ra[&ka])),
                _ => None,
            };
            assert_eq!(b.best(), want);
        }
    }
}
