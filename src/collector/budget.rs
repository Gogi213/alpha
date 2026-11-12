//! Общий для всех шардов процесса бюджет подключений: не больше `limit` TCP-подключений к бирже за скользящее `window` — по каждому
//! правилу из набора (Binance: 300 за 5 мин на IP; OKX: ещё и 3 в секунду — второе правило тем же механизмом).
//!
//! Ровно один `acquire` перед каждым TCP connect; граница из кода — подключений в любом окне каждого правила ≤ его лимита, что бы ни
//! делали отбраковка пути, замены и переподключения. Лимиты бирж и запас — назначаемое.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering::Relaxed};
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
    /// Места основного правила, которые отбраковка пути не трогает: на подключения и замены (сколько соединений в процессе ×2).
    reserve: AtomicUsize,
}

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
            reserve: AtomicUsize::new(0),
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

    /// Запас мест основного правила, недоступный отбраковке пути (см. `can_reject`).
    pub fn set_reserve(&self, n: usize) {
        self.reserve.store(n, Relaxed);
    }

    /// Можно ли потратить подключение на отбракованный путь: основное правило должно оставаться с запасом на подключения и замены.
    /// Если весь маршрут медленный, отбраковка съедает бюджет до запаса, после чего пути принимаются как есть (сторож лага остаётся).
    pub fn can_reject(&self, now: Instant) -> bool {
        let reserve = self.reserve.load(Relaxed);
        if reserve == 0 {
            return true; // как в Python: отбраковка не зависит от бюджета, нехватка места — ожидание в `acquire`
        }
        let limit = self
            .rules
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .first()
            .map_or(0, |r| r.limit);
        self.used(now) + reserve < limit
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
