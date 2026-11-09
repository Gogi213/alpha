//! TK-500: книга одной ноги из плоского бинарника (tools/p40core/to_bin.py, 33 Б: event_time i64, final_update_id i64, price f64, qty f64, flags u8 бит0=снимок бит1=бид)
//! и чтение сделок (to_bin_trades.py, 25 Б). Общие части прохода 1 (серия лучших цен) и прохода 2 (заявки с очередью).
use std::cmp::Reverse;
use std::collections::{BinaryHeap, HashMap};

/// Сдвиг времени каталога (контроль K1, П-40 §4: сдвиг ноги B на ±1/10/60 с, ±1 ч): `(каталог, мс)`, задаётся до первого чтения.
pub static SHIFTS: std::sync::Mutex<Vec<(String, i64)>> = std::sync::Mutex::new(Vec::new());

pub fn shift_of(dir: &str) -> i64 {
    SHIFTS.lock().unwrap().iter().find(|(d, _)| d == dir).map_or(0, |s| s.1)
}

/// Строки часа и сдвиг времени каталога (прибавляется к `et`).
pub struct Rows(pub Vec<u8>, pub i64);

impl Rows {
    pub fn load(dir: &str, h: usize) -> Option<Rows> {
        let sh = shift_of(dir);
        // L1-файл несжатый (`pq2bin l1`, P40_L1_RAW=1) лежит как `<ЧЧ>.l1.bin`; обычный `<ЧЧ>.bin` приоритетнее
        std::fs::read(format!("{dir}/{h:02}.bin")).or_else(|_| std::fs::read(format!("{dir}/{h:02}.l1.bin"))).ok().map(|b| Rows(b, sh))
    }
    pub fn len(&self) -> usize {
        self.0.len() / 33
    }
    pub fn et(&self, i: usize) -> i64 {
        i64::from_le_bytes(self.0[i * 33..i * 33 + 8].try_into().unwrap()) + self.1
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
    /// Бит2 (L1-файл, TK-515): уровень вышел из L1, но не удалён (qty = 0 в строке — «не виден», а не «снят»).
    pub fn leaves_l1(&self, i: usize) -> bool {
        self.fl(i) & 4 != 0
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
    /// Последний видимый размер уровней, вышедших из L1 (бит2): цена -> размер; только для заморозки очереди заявок (fills.rs).
    hid_b: HashMap<u64, f64>,
    hid_a: HashMap<u64, f64>,
}

impl Book {
    pub fn clear(&mut self) {
        self.bids.clear();
        self.asks.clear();
        self.hb.clear();
        self.ha.clear();
        self.hid_b.clear();
        self.hid_a.clear();
    }

    /// Уровень вышел из L1 (бит2) с последним видимым размером `q0` (0 — не было видно: не запоминать).
    pub fn hide(&mut self, bid: bool, price: f64, q0: f64) {
        if q0 > 0.0 {
            if bid { &mut self.hid_b } else { &mut self.hid_a }.insert(price.to_bits(), q0);
        }
    }

    /// Последний видимый размер уровня, ушедшего из L1 и не вернувшегося.
    pub fn hidden_qty(&self, bid: bool, price: f64) -> Option<f64> {
        if bid { self.hid_b.get(&price.to_bits()) } else { self.hid_a.get(&price.to_bits()) }.copied()
    }

    /// Уровень вернулся в L1 (или реально удалён): забыть скрытый размер.
    pub fn unhide(&mut self, bid: bool, price: f64) {
        if bid { &mut self.hid_b } else { &mut self.hid_a }.remove(&price.to_bits());
    }

    /// Лучшие цены с размерами: (bid, bq, ask, aq).
    pub fn best_q(&mut self) -> Option<(f64, f64, f64, f64)> {
        let (b, a) = self.best()?;
        Some((b, self.qty(true, b), a, self.qty(false, a)))
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

/// Ряд L1 каталога `dir` (G-L1a, TK-515): состояние `(t, bid, bq, ask, aq)` в конце каждой группы по правилам ядра (внутри снимка не публикуется, `bid ≥ ask` не публикуется),
/// запись только при изменении `(bid, bq, ask, aq)`. Полная книга и её L1-файл дают один ряд.
pub fn l1_series(dir: &str) -> Vec<(i64, f64, f64, f64, f64)> {
    let mut book = Book::default();
    let mut out: Vec<(i64, f64, f64, f64, f64)> = vec![];
    for h in 0..hours() {
        let Some(rows) = Rows::load(dir, h) else { continue };
        for i in 0..rows.len() {
            if rows.snap_start(i) {
                book.clear();
            }
            book.set(rows.is_bid(i), rows.px(i), rows.qt(i));
            if !rows.group_end(i) {
                continue;
            }
            if let Some((b, bq, a, aq)) = book.best_q() {
                if b < a && out.last().map_or(true, |l| (l.1, l.2, l.3, l.4) != (b, bq, a, aq)) {
                    out.push((rows.et(i), b, bq, a, aq));
                }
            }
        }
    }
    out
}

#[derive(Clone, Copy)]
pub struct Tr {
    pub t: i64,
    pub px: f64,
    pub qty: f64,
    pub buyer_maker: bool,
}

/// Сделки суток из `<dir>/<ЧЧ>.bin` (25 Б: trade_time i64, price f64, qty f64, flags u8 бит0=is_buyer_maker), по порядку часов.
/// Число часовых файлов `<ЧЧ>.bin` каталога ноги: `P40_HOURS` (умолчание 24 = сутки; для чанка из K суток — 24·K, файлы 00..23, 24.. подряд, `orch.py --chunk`).
pub fn hours() -> usize {
    std::env::var("P40_HOURS").ok().map_or(24, |v| v.parse().unwrap())
}

pub fn load_trades(dir: &str) -> Vec<Tr> {
    let sh = shift_of(dir);
    let mut out = vec![];
    for h in 0..hours() {
        let Ok(raw) = std::fs::read(format!("{dir}/{h:02}.bin")) else { continue };
        for r in raw.chunks_exact(25) {
            out.push(Tr {
                t: i64::from_le_bytes(r[0..8].try_into().unwrap()) + sh,
                px: f64::from_le_bytes(r[8..16].try_into().unwrap()),
                qty: f64::from_le_bytes(r[16..24].try_into().unwrap()),
                buyer_maker: r[24] & 1 != 0,
            });
        }
    }
    out
}
