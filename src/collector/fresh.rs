//! Свежесть записей (мера приёмки TK-532): сколько записей пришло с лагом rx − E больше `LATE_MS` и каков максимальный лаг —
//! по видам, за 1 ч и 24 ч. Лаг считается по каждой записанной записи (после снятия дублей), не выборкой.
//!
//! Хранение — минутными корзинами (число поздних, максимум): границы часа и суток — с точностью до минуты в сторону
//! большего окна; память не растёт с числом поздних записей.

use std::collections::VecDeque;

pub const LATE_MS: i64 = 2000;
/// Первые секунды после подписки не считаются: начальные снимки несут устаревший ts, это не задержка доставки.
pub const GRACE_S: f64 = 5.0;
const BUCKET_MS: i64 = 60_000;
const HOUR_MS: i64 = 3_600_000;
const DAY_MS: i64 = 24 * HOUR_MS;

/// Виды учёта: book, trade, mark и raw (кадры данных, которые адаптер не разобрал, лаг — по времени кадра).
pub const KIND_NAMES: [&str; 4] = ["book", "trade", "mark", "raw"];
pub const RAW: usize = 3;

#[derive(Clone, Copy, Debug)]
struct Bucket {
    minute: i64,
    late: u64,
    max_lag: i64,
}

#[derive(Debug, Default)]
pub struct Fresh {
    done: [VecDeque<Bucket>; 4],
    cur: [Option<Bucket>; 4],
}

/// Вклад вида: поздние и максимальный лаг за час и сутки.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct KindFresh {
    pub late_1h: u64,
    pub late_24h: u64,
    pub max_1h_ms: Option<i64>,
    pub max_24h_ms: Option<i64>,
}

impl KindFresh {
    pub fn merge(&mut self, o: &KindFresh) {
        self.late_1h += o.late_1h;
        self.late_24h += o.late_24h;
        self.max_1h_ms = self.max_1h_ms.max(o.max_1h_ms);
        self.max_24h_ms = self.max_24h_ms.max(o.max_24h_ms);
    }
}

impl Fresh {
    /// Запись вида `kind` (0..4), принятая в `rx_ms`, с лагом `lag_ms`.
    pub fn add(&mut self, kind: usize, rx_ms: i64, lag_ms: i64) {
        let minute = rx_ms.div_euclid(BUCKET_MS);
        let late = u64::from(lag_ms > LATE_MS);
        let (Some(cur), Some(done)) = (self.cur.get_mut(kind), self.done.get_mut(kind)) else { return };
        match cur {
            Some(b) if b.minute == minute => {
                b.late += late;
                b.max_lag = b.max_lag.max(lag_ms);
            }
            Some(b) => {
                done.push_back(*b);
                *b = Bucket { minute, late, max_lag: lag_ms };
            }
            None => *cur = Some(Bucket { minute, late, max_lag: lag_ms }),
        }
    }

    /// Вклад вида на момент `now_ms`; устаревшее за сутки выбрасывается. `None` — записей этого вида не было.
    pub fn fragment(&mut self, kind: usize, now_ms: i64) -> Option<KindFresh> {
        let (hour, day) = (now_ms - HOUR_MS, now_ms - DAY_MS);
        let done = self.done.get_mut(kind)?;
        while done.front().is_some_and(|b| (b.minute + 1) * BUCKET_MS <= day) {
            done.pop_front();
        }
        let cur = self.cur.get(kind).copied().flatten();
        let all = done.iter().copied().chain(cur);
        let mut out = KindFresh::default();
        let mut any = false;
        for b in all {
            any = true;
            let end = (b.minute + 1) * BUCKET_MS;
            if end > day {
                out.late_24h += b.late;
                out.max_24h_ms = out.max_24h_ms.max(Some(b.max_lag));
            }
            if end > hour {
                out.late_1h += b.late;
                out.max_1h_ms = out.max_1h_ms.max(Some(b.max_lag));
            }
        }
        any.then_some(out)
    }
}
