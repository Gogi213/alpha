//! TK-500 П-40 §3.3: проход 2 — заявки входа (мейкер, лучшая цена, очередь `prob:3`) по потоку книги и сделок одной ноги.
//! Заявка ставится в `tp` (книга — все события строго до `tp`), снимается в `tc`; исполнение — `QueueOrder` (сделки, обновления уровня, пересечение).
//! События с одним временем: сделка раньше строки книги (иначе `cum_trade` не вычитается из изменения объёма).
use crate::book::{Book, Rows, Tr};
use crate::queue::QueueOrder;
use std::sync::atomic::{AtomicBool, Ordering};

/// Отладочный режим сверки с эталоном TK-501: не исполнять заявки, когда противоположная лучшая цена дошла до цены заявки уже после постановки
/// (в hftbacktest это исполнение есть, в `sim.py` эталона — нет). Умолчание — выкл. (как hftbacktest).
pub static NO_CROSS: AtomicBool = AtomicBool::new(false);

/// `P40_L1_STAT=1` (TK-515): помечать `Job::left_l1` и на полной книге (цена заявки перестала быть лучшей, уровень ещё есть) — для доли «ушла из L1» в сверке полная книга против L1.
pub static L1_STAT: AtomicBool = AtomicBool::new(false);

pub struct Job {
    pub buy: bool,
    pub tp: i64,
    pub tc: i64,
    pub price: f64,
    pub lot: f64,
    /// объём впереди в момент постановки (-1 — не ставилась)
    pub front0: f64,
    /// время исполнения (мс) или -1
    pub fill: i64,
    /// исполнена сразу при постановке: противоположная лучшая цена уже дошла до цены заявки (цена выбрана в момент решения, заявка пришла на `L_ms` позже)
    pub imm: bool,
    /// L1-файл (TK-515 §2.3): последний видимый размер уровня заявки, пока цена вне L1 (бит2) — очередь заморожена до возврата цены; `None` — цена в L1
    froz: Option<f64>,
    /// цена заявки хоть раз выходила из L1 за время жизни заявки (статистика `P40_L1_STAT`)
    pub left_l1: bool,
    q: Option<QueueOrder>,
}

impl Job {
    pub fn new(buy: bool, tp: i64, tc: i64, price: f64, lot: f64) -> Job {
        Job { buy, tp, tc, price, lot, front0: -1.0, fill: -1, imm: false, froz: None, left_l1: false, q: None }
    }
}

struct Sim<'a> {
    book: Book,
    /// нога без сделок (Bybit ob200, Gate): убыль объёма на уровне заявки считается сделкой агрессора (верхняя граница — отмены тоже «исполняют»)
    drain: bool,
    jobs: &'a mut [Job],
    ord: Vec<usize>,
    next: usize,
    active: Vec<usize>,
}

impl Sim<'_> {
    /// Сдвиг часов до `t` (события в момент `t` ещё не применены): снять просроченные (tc < t), поставить пришедшие (tp < t).
    fn advance(&mut self, t: i64) {
        if !self.active.is_empty() {
            let jobs = &mut *self.jobs;
            self.active.retain(|&j| {
                if jobs[j].tc < t {
                    jobs[j].q = None;
                    false
                } else {
                    true
                }
            });
        }
        // заявка встаёт после всех событий со временем ≤ tp (как BBO/уровень «на момент прихода»); события в момент tc ей ещё видны
        while self.next < self.ord.len() && self.jobs[self.ord[self.next]].tp < t {
            let j = self.ord[self.next];
            self.next += 1;
            let job = &mut self.jobs[j];
            if let Some((b, a)) = self.book.best() {
                if (job.buy && a <= job.price) || (!job.buy && b >= job.price) {
                    job.fill = job.tp;
                    job.imm = true;
                    continue;
                }
            }
            if job.tc < t {
                continue;
            }
            let mut lq = self.book.qty(job.buy, job.price);
            // цена заявки к моменту прихода вне L1 (бит2): очередь — последний видимый размер, заморожена до возврата цены в L1
            if lq == 0.0 {
                if let Some(h) = self.book.hidden_qty(job.buy, job.price) {
                    lq = h;
                    job.froz = Some(h);
                    job.left_l1 = true;
                }
            }
            job.front0 = lq;
            job.q = Some(QueueOrder::new(job.buy, job.price, lq, job.lot));
            self.active.push(j);
        }
    }

    fn fill(&mut self, k: usize, t: i64) {
        let j = self.active[k];
        self.jobs[j].fill = t;
        self.jobs[j].q = None;
        self.active.swap_remove(k);
    }

    fn trade(&mut self, tr: &Tr) {
        self.advance(tr.t);
        let mut k = 0;
        while k < self.active.len() {
            let j = self.active[k];
            if self.jobs[j].q.as_mut().unwrap().on_trade(tr.px, tr.qty, tr.buyer_maker) {
                self.fill(k, tr.t);
            } else {
                k += 1;
            }
        }
    }

    fn row(&mut self, rows: &Rows, i: usize) {
        let t = rows.et(i);
        self.advance(t);
        if rows.snap_start(i) {
            self.book.clear();
        }
        let (bid, px) = (rows.is_bid(i), rows.px(i));
        let left = rows.leaves_l1(i);
        let prev = self.book.set(bid, px, rows.qt(i));
        if left {
            self.book.hide(bid, px, prev);
        } else if rows.qt(i) > 0.0 {
            self.book.unhide(bid, px);
        }
        let mut k = 0;
        while k < self.active.len() {
            let job = &mut self.jobs[self.active[k]];
            if job.buy == bid && job.price == px {
                // L1 (бит2, TK-515 §2.3): уход цены из L1 очередь не двигает и не исполняет; возврат — по видимому размеру: q1 < q0 -> on_depth(q0, q1) без on_trade (и в drain), q1 ≥ q0 — без изменения
                if left {
                    job.froz.get_or_insert(prev);
                    job.left_l1 = true;
                    k += 1;
                    continue;
                }
                if let Some(q0) = job.froz.take() {
                    if rows.qt(i) < q0 {
                        job.q.as_mut().unwrap().on_depth(q0, rows.qt(i));
                    }
                    k += 1;
                    continue;
                }
                let q = job.q.as_mut().unwrap();
                // убыль по цене заявки = сделка встречной стороны на объём убыли (бид исполняют продающие агрессоры -> buyer_maker)
                if self.drain && rows.qt(i) < prev && q.on_trade(px, prev - rows.qt(i), bid) {
                    self.fill(k, t);
                    continue;
                }
                q.on_depth(prev, rows.qt(i));
            }
            k += 1;
        }
        if L1_STAT.load(Ordering::Relaxed) && !rows.snap(i) && rows.group_end(i) && !self.active.is_empty() {
            if let Some((b, a)) = self.book.best() {
                for &j in &self.active {
                    let job = &mut self.jobs[j];
                    let off = if job.buy { b > job.price } else { a < job.price };
                    if off && self.book.qty(job.buy, job.price) > 0.0 {
                        job.left_l1 = true;
                    }
                }
            }
        }
        if !NO_CROSS.load(Ordering::Relaxed) && !rows.snap(i) && rows.group_end(i) && !self.active.is_empty() {
            if let Some((b, a)) = self.book.best() {
                if b < a {
                    let mut k = 0;
                    while k < self.active.len() {
                        let job = &self.jobs[self.active[k]];
                        let opp = if job.buy { a } else { b };
                        if job.q.as_ref().unwrap().on_cross(opp) {
                            self.fill(k, t);
                        } else {
                            k += 1;
                        }
                    }
                }
            }
        }
    }
}

/// Проигрывает книгу ноги `dir` и сделки `trades` (по времени), проставляет `fill`/`front0` в `jobs`; `drain` — режим «убыль объёма» для ноги без сделок.
pub fn run(dir: &str, trades: &[Tr], drain: bool, jobs: &mut [Job]) {
    let mut ord: Vec<usize> = (0..jobs.len()).collect();
    ord.sort_by_key(|&j| jobs[j].tp);
    let mut s = Sim { book: Book::default(), drain, jobs, ord, next: 0, active: vec![] };
    let mut ti = 0usize;
    for h in 0..crate::book::hours() {
        let Some(rows) = Rows::load(dir, h) else { continue };
        for i in 0..rows.len() {
            let te = rows.et(i);
            while ti < trades.len() && trades[ti].t <= te {
                s.trade(&trades[ti]);
                ti += 1;
            }
            s.row(&rows, i);
        }
    }
    while ti < trades.len() {
        s.trade(&trades[ti]);
        ti += 1;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // строка книги: (event_time, final_update_id, price, qty, flags: бит0 снимок, бит1 бид)
    fn write_rows(tag: &str, rows: &[(i64, i64, f64, f64, u8)]) -> String {
        let dir = std::env::temp_dir().join(format!("p40core-fills-{tag}-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let mut raw = vec![];
        for r in rows {
            raw.extend_from_slice(&r.0.to_le_bytes());
            raw.extend_from_slice(&r.1.to_le_bytes());
            raw.extend_from_slice(&r.2.to_le_bytes());
            raw.extend_from_slice(&r.3.to_le_bytes());
            raw.push(r.4);
        }
        std::fs::write(dir.join("00.bin"), raw).unwrap();
        dir.to_str().unwrap().to_string()
    }

    // снимок в t=0: бид 10 (100), бид 9.9 (10), аск 11 (50)
    const SNAP: [(i64, i64, f64, f64, u8); 3] = [(0, 1, 10.0, 100.0, 3), (0, 1, 9.9, 10.0, 3), (0, 1, 11.0, 50.0, 1)];

    fn tr(t: i64, px: f64, qty: f64, bm: bool) -> Tr {
        Tr { t, px, qty, buyer_maker: bm }
    }

    #[test]
    fn buy_fills_after_queue_drained_by_trades() {
        let dir = write_rows("a", &SNAP);
        let mut jobs = [Job::new(true, 500, 10_000, 10.0, 1.0)];
        run(&dir, &[tr(1000, 10.0, 100.0, true), tr(2000, 10.0, 1.0, true)], false, &mut jobs);
        assert_eq!(jobs[0].front0, 100.0);
        assert_eq!(jobs[0].fill, 2000);
    }

    #[test]
    fn cancelled_before_fill_stays_unfilled() {
        let dir = write_rows("b", &SNAP);
        let mut jobs = [Job::new(true, 500, 1500, 10.0, 1.0)];
        run(&dir, &[tr(1000, 10.0, 100.0, true), tr(2000, 10.0, 1.0, true)], false, &mut jobs);
        assert_eq!(jobs[0].fill, -1);
    }

    #[test]
    fn opposite_best_crossing_down_fills_buy() {
        // t=3000: уровень бида 10 исчезает, аск 10 появляется (одно событие, fu=7): аск ≤ цена заявки при b=9.9 < a=10
        let mut rows = SNAP.to_vec();
        rows.push((3000, 7, 10.0, 0.0, 2));
        rows.push((3000, 7, 10.0, 5.0, 0));
        let dir = write_rows("c", &rows);
        let mut jobs = [Job::new(true, 500, 10_000, 10.0, 1.0)];
        run(&dir, &[], false, &mut jobs);
        assert_eq!(jobs[0].fill, 3000);
    }

    #[test]
    fn sell_not_placed_after_cancel_time_and_ahead_queue_counts() {
        let dir = write_rows("d", &SNAP);
        // tc ≤ tp: заявка не ставится; вторая — на аске 11: впереди 50
        let mut jobs = [Job::new(false, 800, 800, 11.0, 1.0), Job::new(false, 800, 5000, 11.0, 1.0)];
        run(&dir, &[tr(1000, 11.0, 49.0, false), tr(1500, 11.0, 2.0, false)], false, &mut jobs);
        assert_eq!((jobs[0].front0, jobs[0].fill), (-1.0, -1));
        assert_eq!((jobs[1].front0, jobs[1].fill), (50.0, 1500));
    }

    #[test]
    fn stale_price_fills_immediately_on_arrival() {
        // покупка по 11 (аск 11 уже достигнут) и продажа по 10 (бид 10 уже достигнут) исполняются в момент постановки
        let dir = write_rows("e", &SNAP);
        let mut jobs = [Job::new(true, 800, 5000, 11.0, 1.0), Job::new(false, 800, 5000, 10.0, 1.0), Job::new(true, 800, 5000, 10.0, 1.0)];
        run(&dir, &[tr(900, 10.5, 0.0001, true)], false, &mut jobs);
        assert_eq!((jobs[0].fill, jobs[0].imm), (800, true));
        assert_eq!((jobs[1].fill, jobs[1].imm), (800, true));
        assert_eq!((jobs[2].fill, jobs[2].imm), (-1, false));
    }

    #[test]
    fn drain_mode_fills_on_volume_decrease_only_when_enabled() {
        // бид 10: 100 -> 30 -> 0 (front 0: не исполнена) -> +5 -> 2 (убыль 3: front -3 -> исполнена)
        let mut rows = SNAP.to_vec();
        rows.extend([(1000, 2, 10.0, 30.0, 2), (2000, 3, 10.0, 0.0, 2), (3000, 4, 10.0, 5.0, 2), (4000, 5, 10.0, 2.0, 2)]);
        let dir = write_rows("f", &rows);
        let mut on = [Job::new(true, 500, 10_000, 10.0, 1.0)];
        run(&dir, &[], true, &mut on);
        assert_eq!((on[0].front0, on[0].fill), (100.0, 4000));
        let mut off = [Job::new(true, 500, 10_000, 10.0, 1.0)];
        run(&dir, &[], false, &mut off);
        assert_eq!(off[0].fill, -1);
        // продажа на аске: убыль объёма аска 11 (50 -> 0) двигает очередь, но не исполняет (front 0)
        let mut rows2 = SNAP.to_vec();
        rows2.extend([(1000, 2, 11.0, 0.0, 0), (2000, 3, 11.0, 4.0, 0), (3000, 4, 11.0, 1.0, 0)]);
        let dir2 = write_rows("g", &rows2);
        let mut sell = [Job::new(false, 500, 10_000, 11.0, 1.0)];
        run(&dir2, &[], true, &mut sell);
        assert_eq!((sell[0].front0, sell[0].fill), (50.0, 3000));
    }

    // --- L1-файл (TK-515 §2.3): бит2 (flags 4) = уровень вышел из L1, не удалён ---

    // L1-снимок: бид 10 (100), аск 11 (50); t=1000: бид 10.1 (5) вошёл, 10 вышел из L1 (бит2); t=2000: 10.1 ушёл, 10 вернулся с размером q1
    fn l1_rows(q1: f64) -> Vec<(i64, i64, f64, f64, u8)> {
        vec![(0, -1, 10.0, 100.0, 3), (0, -1, 11.0, 50.0, 1), (1000, 2, 10.0, 0.0, 2 | 4), (1000, 2, 10.1, 5.0, 2), (2000, 3, 10.1, 0.0, 2), (2000, 3, 10.0, q1, 2)]
    }

    #[test]
    fn l1_leave_freezes_queue_and_never_fills_even_in_drain() {
        for drain in [false, true] {
            let dir = write_rows(if drain { "l1a" } else { "l1b" }, &l1_rows(100.0));
            let mut jobs = [Job::new(true, 500, 10_000, 10.0, 1.0)];
            run(&dir, &[], drain, &mut jobs);
            assert_eq!((jobs[0].front0, jobs[0].fill, jobs[0].left_l1), (100.0, -1, true));
        }
    }

    #[test]
    fn l1_return_with_smaller_size_moves_queue_by_on_depth_only() {
        // q1 = 30 < q0 = 100: очередь 100 -> 30 (заявка последняя: back = 0); сделка 31 исполняет. Без возврата-on_depth очередь осталась бы 100
        let dir = write_rows("l1c", &l1_rows(30.0));
        let mut jobs = [Job::new(true, 500, 10_000, 10.0, 1.0)];
        run(&dir, &[tr(3000, 10.0, 31.0, true)], false, &mut jobs);
        assert_eq!(jobs[0].fill, 3000);
        // drain: возврат с q1 < q0 — не сделка, исполнения в момент возврата нет
        let mut d = [Job::new(true, 500, 10_000, 10.0, 1.0)];
        run(&dir, &[], true, &mut d);
        assert_eq!(d[0].fill, -1);
    }

    #[test]
    fn l1_return_with_larger_size_keeps_queue() {
        let dir = write_rows("l1d", &l1_rows(150.0));
        let mut jobs = [Job::new(true, 500, 10_000, 10.0, 1.0)];
        run(&dir, &[tr(3000, 10.0, 99.0, true)], false, &mut jobs);
        assert_eq!(jobs[0].fill, -1); // впереди 100 - 99 = 1
        let mut j2 = [Job::new(true, 500, 10_000, 10.0, 1.0)];
        run(&dir, &[tr(3000, 10.0, 101.0, true)], false, &mut j2);
        assert_eq!(j2[0].fill, 3000);
    }

    #[test]
    fn l1_placed_while_price_outside_l1_takes_last_visible_size() {
        // заявка приходит в t=1500, когда цена 10 уже вне L1: очередь = последний видимый размер 100, заморожена до возврата
        let dir = write_rows("l1e", &l1_rows(100.0));
        let mut jobs = [Job::new(true, 1500, 10_000, 10.0, 1.0)];
        run(&dir, &[], true, &mut jobs);
        assert_eq!((jobs[0].front0, jobs[0].fill, jobs[0].left_l1), (100.0, -1, true));
    }

    #[test]
    fn deleted_level_without_bit2_still_acts_as_before() {
        // тот же ряд без бита2: убыль 100 -> 0 в drain = сделка на 100 (front 0, не исполнена), возврат 30 после 0 — прирост; поведение прежнее, left_l1 не ставится
        let mut rows = l1_rows(30.0);
        rows[2].4 = 2;
        let dir = write_rows("l1f", &rows);
        let mut jobs = [Job::new(true, 500, 10_000, 10.0, 1.0)];
        run(&dir, &[], true, &mut jobs);
        assert!(!jobs[0].left_l1);
        assert_eq!(jobs[0].fill, -1);
    }
}
