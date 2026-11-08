//! TK-500 П-40 §3.3: проход 2 — заявки входа (мейкер, лучшая цена, очередь `prob:3`) по потоку книги и сделок одной ноги.
//! Заявка ставится в `tp` (книга — все события строго до `tp`), снимается в `tc`; исполнение — `QueueOrder` (сделки, обновления уровня, пересечение).
//! События с одним временем: сделка раньше строки книги (иначе `cum_trade` не вычитается из изменения объёма).
use crate::book::{Book, Rows, Tr};
use crate::queue::QueueOrder;

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
    q: Option<QueueOrder>,
}

impl Job {
    pub fn new(buy: bool, tp: i64, tc: i64, price: f64, lot: f64) -> Job {
        Job { buy, tp, tc, price, lot, front0: -1.0, fill: -1, q: None }
    }
}

struct Sim<'a> {
    book: Book,
    jobs: &'a mut [Job],
    ord: Vec<usize>,
    next: usize,
    active: Vec<usize>,
}

impl Sim<'_> {
    /// Сдвиг часов до `t`: снять просроченные, поставить подошедшие.
    fn advance(&mut self, t: i64) {
        if !self.active.is_empty() {
            let jobs = &mut *self.jobs;
            self.active.retain(|&j| {
                if jobs[j].tc <= t {
                    jobs[j].q = None;
                    false
                } else {
                    true
                }
            });
        }
        while self.next < self.ord.len() && self.jobs[self.ord[self.next]].tp <= t {
            let j = self.ord[self.next];
            self.next += 1;
            let job = &mut self.jobs[j];
            if job.tc <= t {
                continue;
            }
            let lq = self.book.qty(job.buy, job.price);
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
        let prev = self.book.set(bid, px, rows.qt(i));
        for &j in &self.active {
            let job = &mut self.jobs[j];
            if job.buy == bid && job.price == px {
                job.q.as_mut().unwrap().on_depth(prev, rows.qt(i));
            }
        }
        if !rows.snap(i) && rows.group_end(i) && !self.active.is_empty() {
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

/// Проигрывает книгу ноги `dir` и сделки `trades` (по времени), проставляет `fill`/`front0` в `jobs`.
pub fn run(dir: &str, trades: &[Tr], jobs: &mut [Job]) {
    let mut ord: Vec<usize> = (0..jobs.len()).collect();
    ord.sort_by_key(|&j| jobs[j].tp);
    let mut s = Sim { book: Book::default(), jobs, ord, next: 0, active: vec![] };
    let mut ti = 0usize;
    for h in 0..24 {
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
        run(&dir, &[tr(1000, 10.0, 100.0, true), tr(2000, 10.0, 1.0, true)], &mut jobs);
        assert_eq!(jobs[0].front0, 100.0);
        assert_eq!(jobs[0].fill, 2000);
    }

    #[test]
    fn cancelled_before_fill_stays_unfilled() {
        let dir = write_rows("b", &SNAP);
        let mut jobs = [Job::new(true, 500, 1500, 10.0, 1.0)];
        run(&dir, &[tr(1000, 10.0, 100.0, true), tr(2000, 10.0, 1.0, true)], &mut jobs);
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
        run(&dir, &[], &mut jobs);
        assert_eq!(jobs[0].fill, 3000);
    }

    #[test]
    fn sell_not_placed_after_cancel_time_and_ahead_queue_counts() {
        let dir = write_rows("d", &SNAP);
        // tc ≤ tp: заявка не ставится; вторая — на аске 11: впереди 50
        let mut jobs = [Job::new(false, 800, 800, 11.0, 1.0), Job::new(false, 800, 5000, 11.0, 1.0)];
        run(&dir, &[tr(1000, 11.0, 49.0, false), tr(1500, 11.0, 2.0, false)], &mut jobs);
        assert_eq!((jobs[0].front0, jobs[0].fill), (-1.0, -1));
        assert_eq!((jobs[1].front0, jobs[1].fill), (50.0, 1500));
    }
}
