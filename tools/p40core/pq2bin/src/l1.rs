//! TK-515: кодировщик L1 — сырые строки книги (et, fu, price, qty, flags) -> L1-файл ядра (те же 33 Б, flags: бит0 снимок, бит1 бид,
//! бит2 «уровень вышел из L1, но не удалён из книги»). Книга и группы — как в rs/src/book.rs (Book, Rows::group_end, snap_start).
//!
//! Правила: после конца группы (строки подряд с одинаковыми (snap, fu, et), снимочные подряд — одна группа) читается лучшая пара книги;
//! есть обе стороны и bid < ask — состояние L1 = (bid, bq, ask, aq), иначе публикации нет (L1 остаётся прежним). Запись только при изменении:
//! по стороне — смена цены: строка старой цены с qty=0 (бит2=1, если уровень ещё в полной книге) и строка новой цены; та же цена, другая qty — одна строка.
//! Сначала бид, потом аск; et/fu — последней строки группы, бит0 сброшен. Начало часа: снимок текущего L1 (две строки fu=-1, бит0; бид|бит1),
//! et = et первой строки часа; нет L1 к началу часа — снимок нового L1 при первой публикации часа.
use crate::push_row;
use std::collections::BTreeMap;
use std::hash::Hasher;

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

/// Книга как rs/src/book.rs: абсолютные количества, qty=0 удаляет уровень. Сторона — `BTreeMap<биты цены, qty>` (цены > 0: порядок бит = порядок цен):
/// лучший уровень за O(log n) без хешей и без сдвигов массива (отсортированный Vec давал memmove на глубоких правках, TK-516).
#[derive(Default)]
pub struct Book {
    bids: BTreeMap<u64, f64>,
    asks: BTreeMap<u64, f64>,
    /// После `clear` (начало снимка) строки копятся в порядке прихода, книга собирается разом при первом чтении или правке не из снимка.
    loading: bool,
    pb: Vec<(u64, f64)>,
    pa: Vec<(u64, f64)>,
}

fn set_side(m: &mut BTreeMap<u64, f64>, key: u64, q: f64) {
    if q == 0.0 {
        m.remove(&key);
    } else {
        m.insert(key, q);
    }
}

/// Накопленные строки стороны -> в книгу: последняя строка ключа побеждает, qty=0 удаляет. Пустая сторона — сортировка и разовая сборка, иначе по одной.
fn merge_side(m: &mut BTreeMap<u64, f64>, p: &mut Vec<(u64, f64)>) {
    if !m.is_empty() {
        for &(k, q) in p.iter() {
            set_side(m, k, q);
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
        *m = p.drain(..).collect();
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
        set_side(if bid { &mut self.bids } else { &mut self.asks }, key, q);
    }

    /// Закончить накопление снимка: собрать книгу.
    pub fn flush(&mut self) {
        if self.loading {
            merge_side(&mut self.bids, &mut self.pb);
            merge_side(&mut self.asks, &mut self.pa);
            self.loading = false;
        }
    }

    pub fn qty(&mut self, bid: bool, price: f64) -> f64 {
        self.flush();
        let m = if bid { &self.bids } else { &self.asks };
        m.get(&price.to_bits()).copied().unwrap_or(0.0)
    }

    /// Лучшая пара `(бид, кол-во, аск, кол-во)`.
    pub fn best(&mut self) -> Option<(f64, f64, f64, f64)> {
        self.flush();
        let (&kb, &bq) = self.bids.last_key_value()?;
        let (&ka, &aq) = self.asks.first_key_value()?;
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
fn group_end(cur: (bool, i64, i64), next: (bool, i64, i64)) -> bool {
    if cur == next {
        return false;
    }
    !(cur.0 && next.0)
}

pub struct L1Enc {
    book: Book,
    last: Option<L1>,
    /// Предыдущая строка часа `(et, fu, snap)`; `None` — следующая строка первая в часе.
    prev: Option<(i64, i64, bool)>,
    hour_et: i64,
    need_snap: bool,
}

impl Default for L1Enc {
    fn default() -> Self {
        Self::new()
    }
}

impl L1Enc {
    pub fn new() -> Self {
        L1Enc { book: Book::default(), last: None, prev: None, hour_et: 0, need_snap: true }
    }

    /// Следующая строка источника в порядке файла; `fl`: бит0 снимок, бит1 бид (как в 33-байтном формате ядра).
    pub fn row(&mut self, out: &mut Vec<u8>, et: i64, fu: i64, px: f64, qt: f64, fl: u8) {
        let snap = fl & 1 != 0;
        let snap_start = match self.prev {
            Some((pe, pf, ps)) => {
                if group_end((ps, pf, pe), (snap, fu, et)) {
                    self.publish(out, pe, pf);
                }
                snap && !ps
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
                write_snapshot(out, self.hour_et, cur);
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
