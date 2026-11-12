//! Общий для всех шардов процесса бюджет подключений: не больше `limit` TCP-подключений к бирже за скользящие `window`.
//!
//! Ровно один `acquire` перед каждым TCP connect; граница из кода — подключений в любом окне ≤ `limit`, что бы ни делали
//! отбраковка пути, замены и переподключения. Лимит биржи (Binance USD-M: 300 за 5 мин на IP) и запас — назначаемое.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const WINDOW: Duration = Duration::from_secs(300);
/// Назначаемое (Судье): 2/3 лимита биржи 300; запас 100 — на неучтённое (другие клиенты с этого IP, граница окна, ручные пробы).
pub const LIMIT: usize = 200;

pub struct Budget {
    limit: usize,
    window: Duration,
    taken: Mutex<VecDeque<Instant>>,
}

impl Budget {
    pub fn new(limit: usize, window: Duration) -> Self {
        Self { limit: limit.max(1), window, taken: Mutex::new(VecDeque::new()) }
    }

    /// `None` — место занято (отметка «сейчас» записана); иначе — через сколько освободится самое старое место.
    pub fn try_take(&self, now: Instant) -> Option<Duration> {
        let mut q = self.taken.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        while q.front().is_some_and(|t| now.saturating_duration_since(*t) >= self.window) {
            q.pop_front();
        }
        if q.len() < self.limit {
            q.push_back(now);
            None
        } else {
            q.front().map(|t| (*t + self.window).saturating_duration_since(now))
        }
    }

    /// Занято мест в окне сейчас.
    pub fn used(&self, now: Instant) -> usize {
        let q = self.taken.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        q.iter().filter(|t| now.saturating_duration_since(**t) < self.window).count()
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
                Some(w) => std::thread::sleep(w.min(Duration::from_millis(500)) + Duration::from_millis(10)),
            }
        }
    }
}
