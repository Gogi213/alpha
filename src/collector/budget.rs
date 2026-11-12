//! Общий для всех шардов процесса бюджет подключений: не больше `limit` TCP-подключений к бирже за скользящее `window` — по каждому
//! правилу из набора (Binance: 300 за 5 мин на IP; OKX: ещё и 3 в секунду — второе правило тем же механизмом).
//!
//! Ровно один `acquire` перед каждым TCP connect; граница из кода — подключений в любом окне каждого правила ≤ его лимита, что бы ни
//! делали отбраковка пути, замены и переподключения. Лимиты бирж и запас — назначаемое.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const WINDOW: Duration = Duration::from_secs(300);
/// Назначаемое (Судье): 2/3 лимита биржи 300; запас 100 — на неучтённое (другие клиенты с этого IP, граница окна, ручные пробы).
pub const LIMIT: usize = 200;

struct Rule {
    limit: usize,
    window: Duration,
    taken: VecDeque<Instant>,
}

pub struct Budget {
    rules: Mutex<Vec<Rule>>,
    /// RTT рукопожатия последних подключений процесса, мс: «как сейчас до биржи» (порог отбраковки пути — относительно соседей).
    rtts: Mutex<VecDeque<f64>>,
}

/// Сколько последних RTT помнить и сколько нужно для медианы.
const RTT_KEEP: usize = 64;
const RTT_MIN_SAMPLES: usize = 8;

impl Budget {
    pub fn new(limit: usize, window: Duration) -> Self {
        Self::with_rules(&[(limit, window)])
    }

    /// Несколько окон сразу: подключение разрешено, если место есть во всех.
    pub fn with_rules(rules: &[(usize, Duration)]) -> Self {
        let rules = rules
            .iter()
            .map(|&(limit, window)| Rule {
                limit: limit.max(1),
                window,
                taken: VecDeque::new(),
            })
            .collect();
        Self {
            rules: Mutex::new(rules),
            rtts: Mutex::new(VecDeque::new()),
        }
    }

    /// `None` — место занято (отметка «сейчас» записана во всех правилах); иначе — через сколько можно пробовать снова
    /// (по самому долгому из правил, где места нет).
    pub fn try_take(&self, now: Instant) -> Option<Duration> {
        let mut rules = self
            .rules
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let mut wait: Option<Duration> = None;
        for r in rules.iter_mut() {
            while r
                .taken
                .front()
                .is_some_and(|t| now.saturating_duration_since(*t) >= r.window)
            {
                r.taken.pop_front();
            }
            if r.taken.len() >= r.limit {
                let w = r.taken.front().map_or(Duration::ZERO, |t| {
                    (*t + r.window).saturating_duration_since(now)
                });
                wait = Some(wait.map_or(w, |x| x.max(w)));
            }
        }
        if wait.is_none() {
            for r in rules.iter_mut() {
                r.taken.push_back(now);
            }
        }
        wait
    }

    /// Записать RTT рукопожатия очередного подключения.
    pub fn note_rtt(&self, rtt_ms: f64) {
        let mut q = self
            .rtts
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if q.len() >= RTT_KEEP {
            q.pop_front();
        }
        q.push_back(rtt_ms);
    }

    /// Медиана RTT последних подключений; `None` — мало данных.
    pub fn rtt_median(&self) -> Option<f64> {
        let q = self
            .rtts
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if q.len() < RTT_MIN_SAMPLES {
            return None;
        }
        let mut v: Vec<f64> = q.iter().copied().collect();
        v.sort_by(f64::total_cmp);
        v.get(v.len() / 2).copied()
    }

    /// Занято мест в окне первого (основного) правила сейчас.
    pub fn used(&self, now: Instant) -> usize {
        let rules = self
            .rules
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        rules.first().map_or(0, |r| {
            r.taken
                .iter()
                .filter(|t| now.saturating_duration_since(**t) < r.window)
                .count()
        })
    }

    /// Ждёт места и занимает его; возвращает, сколько ждали. `None` — остановка (`stop`) пришла раньше.
    pub fn acquire(&self, stop: &AtomicBool) -> Option<Duration> {
        let t0 = Instant::now();
        loop {
            if stop.load(Relaxed) {
                return None;
            }
            match self.try_take(Instant::now()) {
                None => return Some(t0.elapsed()),
                Some(w) => std::thread::sleep(
                    w.min(Duration::from_millis(500)) + Duration::from_millis(10),
                ),
            }
        }
    }
}
