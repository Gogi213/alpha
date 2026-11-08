//! TK-500: книга одной ноги из плоского бинарника (tools/p40core/to_bin.py, 33 Б: event_time i64, final_update_id i64, price f64, qty f64, flags u8 бит0=снимок бит1=бид)
//! и чтение сделок (to_bin_trades.py, 25 Б). Общие части прохода 1 (серия лучших цен) и прохода 2 (заявки с очередью).
use std::cmp::Reverse;
use std::collections::{BinaryHeap, HashMap};

pub struct Rows(pub Vec<u8>);

impl Rows {
    pub fn load(dir: &str, h: usize) -> Option<Rows> {
        std::fs::read(format!("{dir}/{h:02}.bin")).ok().map(Rows)
    }
    pub fn len(&self) -> usize {
        self.0.len() / 33
    }
    pub fn et(&self, i: usize) -> i64 {
        i64::from_le_bytes(self.0[i * 33..i * 33 + 8].try_into().unwrap())
    }
    pub fn fu(&self, i: usize) -> i64 {
        i64::from_le_bytes(self.0[i * 33 + 8..i * 33 + 16].try_into().unwrap())
    }
    pub fn px(&self, i: usize) -> f64 {
        f64::from_le_bytes(self.0[i * 33 + 16..i * 33 + 24].try_into().unwrap())
    }
    pub fn qt(&self, i: usize) -> f64 {
        f64::from_le_bytes(self.0[i * 33 + 24..i * 33 + 32].try_into().unwrap())
    }
    pub fn fl(&self, i: usize) -> u8 {
        self.0[i * 33 + 32]
    }
    pub fn snap(&self, i: usize) -> bool {
        self.fl(i) & 1 != 0
    }
    pub fn is_bid(&self, i: usize) -> bool {
        self.fl(i) & 2 != 0
    }
    /// Начало нового снимка (первая строка снимка после обычных строк): книга очищается.
    pub fn snap_start(&self, i: usize) -> bool {
        self.snap(i) && (i == 0 || !self.snap(i - 1))
    }
    /// После строки `i` состояние книги считается законченным (конец группы одного события); иначе строка продолжает группу.
    pub fn group_end(&self, i: usize) -> bool {
        if i + 1 >= self.len() {
            return true;
        }
        let (s, ns) = (self.snap(i), self.snap(i + 1));
        if s == ns && self.fu(i) == self.fu(i + 1) && self.et(i) == self.et(i + 1) {
            return false;
        }
        !(s && ns)
    }
}

#[derive(Default)]
pub struct Book {
    bids: HashMap<u64, f64>,
    asks: HashMap<u64, f64>,
    hb: BinaryHeap<u64>,
    ha: BinaryHeap<Reverse<u64>>,
}

impl Book {
    pub fn clear(&mut self) {
        self.bids.clear();
        self.asks.clear();
        self.hb.clear();
        self.ha.clear();
    }

    /// Абсолютное количество на уровне (0 — убрать). Возвращает прежнее количество (0, если уровня не было).
    pub fn set(&mut self, bid: bool, price: f64, q: f64) -> f64 {
        let key = price.to_bits();
        let prev = if bid { self.bids.get(&key) } else { self.asks.get(&key) }.copied().unwrap_or(0.0);
        if q == 0.0 {
            if bid {
                self.bids.remove(&key);
            } else {
                self.asks.remove(&key);
            }
        } else {
            if prev == 0.0 {
                if bid {
                    self.hb.push(key);
                } else {
                    self.ha.push(Reverse(key));
                }
            }
            if bid {
                self.bids.insert(key, q);
            } else {
                self.asks.insert(key, q);
            }
        }
        prev
    }

    pub fn qty(&self, bid: bool, price: f64) -> f64 {
        let key = price.to_bits();
        if bid { self.bids.get(&key) } else { self.asks.get(&key) }.copied().unwrap_or(0.0)
    }

    /// Уровни стороны от лучшего вглубь: (цена, количество).
    pub fn levels(&self, bid: bool) -> Vec<(f64, f64)> {
        let m = if bid { &self.bids } else { &self.asks };
        let mut v: Vec<(f64, f64)> = m.iter().map(|(&k, &q)| (f64::from_bits(k), q)).collect();
        v.sort_by(|a, b| if bid { b.0.partial_cmp(&a.0).unwrap() } else { a.0.partial_cmp(&b.0).unwrap() });
        v
    }

    pub fn best(&mut self) -> Option<(f64, f64)> {
        while let Some(&k) = self.hb.peek() {
            if self.bids.contains_key(&k) {
                break;
            }
            self.hb.pop();
        }
        while let Some(&Reverse(k)) = self.ha.peek() {
            if self.asks.contains_key(&k) {
                break;
            }
            self.ha.pop();
        }
        match (self.hb.peek(), self.ha.peek()) {
            (Some(&kb), Some(&Reverse(ka))) => Some((f64::from_bits(kb), f64::from_bits(ka))),
            _ => None,
        }
    }
}

#[derive(Clone, Copy)]
pub struct Tr {
    pub t: i64,
    pub px: f64,
    pub qty: f64,
    pub buyer_maker: bool,
}

/// Сделки суток из `<dir>/<ЧЧ>.bin` (25 Б: trade_time i64, price f64, qty f64, flags u8 бит0=is_buyer_maker), по порядку часов.
pub fn load_trades(dir: &str) -> Vec<Tr> {
    let mut out = vec![];
    for h in 0..24 {
        let Ok(raw) = std::fs::read(format!("{dir}/{h:02}.bin")) else { continue };
        for r in raw.chunks_exact(25) {
            out.push(Tr {
                t: i64::from_le_bytes(r[0..8].try_into().unwrap()),
                px: f64::from_le_bytes(r[8..16].try_into().unwrap()),
                qty: f64::from_le_bytes(r[16..24].try_into().unwrap()),
                buyer_maker: r[24] & 1 != 0,
            });
        }
    }
    out
}
