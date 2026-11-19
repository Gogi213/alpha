//! Общий для всех шардов процесса бюджет подключений: не больше `limit` TCP-подключений к бирже за скользящее `window` — по каждому
//! правилу из набора (Binance: 300 за 5 мин на IP; OKX: ещё и 3 в секунду — второе правило тем же механизмом).
//!
//! Ровно один `acquire` перед каждым TCP connect; граница из кода — подключений в любом окне каждого правила ≤ его лимита, что бы ни
//! делали отбраковка пути, замены и переподключения. Лимиты бирж и запас — назначаемое.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering::Relaxed};
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const WINDOW: Duration = Duration::from_secs(300);
/// Назначаемое (Судье): 2/3 лимита биржи 300; запас 100 — на неучтённое (другие клиенты с этого IP, граница окна, ручные пробы).
pub const LIMIT: usize = 200;
/// Окно правила, с которого тревога по заполнению (90 %) осмысленна: у секундных правил (OKX и BingX 2/с, Gate 5/с) два подключения в одну секунду — уже 100 %, и ожидание третьего — норма
/// (старт OKX ≈ 5 с, пачка сбросов BingX из 5–17 подключений ≈ 8,5 с); там тревога — подключение ждёт места дольше `SEC_RULE_WAIT_ALARM_S`.
pub const FILL_ALARM_MIN_WINDOW_S: u64 = 60;
pub const SEC_RULE_WAIT_ALARM_S: u64 = 30;

struct Rule {
    limit: usize,
    window: Duration,
    taken: VecDeque<Instant>,
}

pub struct Budget {
    rules: Mutex<Vec<Rule>>,
    /// Места основного правила, которые отбраковка пути не трогает: на подключения и замены (сколько соединений в процессе ×2).
    reserve: AtomicUsize,
    /// Сколько подключений сейчас ждут места, а их шард без единого соединения: пока такие есть, остальные уступают (см. `acquire`).
    urgent: AtomicUsize,
    /// Места каждого правила, которые занимают только подключения шардов без единого соединения (замены, вторые линии и отбраковки их не трогают):
    /// после обрыва связи шарды переподключаются сразу, а не ждут, пока окно освободит место (Binance 15.11 18:12, `docs/findings/collect-gap-audit-2026-11-16.md` §3.2).
    urgent_reserve: AtomicUsize,
    /// Счётчики ожидания для тревоги «бюджет исчерпан»: подключений, которым не хватило места сразу; сколько ждут сейчас; суммарное ожидание, мс.
    waits: AtomicU64,
    waiting: AtomicUsize,
    wait_ms: AtomicU64,
    /// Начала идущих сейчас ожиданий: самое давнее — «ждёт дольше» тревоги секундных правил.
    wait_starts: Mutex<Vec<Instant>>,
}

/// Состояние бюджета для health (`connect_budget`).
#[derive(Clone, Debug, PartialEq)]
pub struct BudgetSnap {
    pub limit: usize,
    pub window_s: u64,
    pub used: usize,
    pub urgent_reserve: usize,
    pub waiting: usize,
    /// Сколько секунд ждёт самое давнее из ждущих сейчас подключений.
    pub max_wait_s: u64,
    pub waits_total: u64,
    pub wait_s_total: u64,
}

impl BudgetSnap {
    /// Тревога. Правило с окном от `FILL_ALARM_MIN_WINDOW_S` (Binance 280 и Bybit 200 за 5 мин): занято не меньше 90 % лимита или есть подключения, ждущие места.
    /// Секундное правило: подключение ждёт места не меньше `SEC_RULE_WAIT_ALARM_S` (заполнение и короткое ожидание у него обычны).
    pub fn alarm(&self) -> bool {
        if self.window_s >= FILL_ALARM_MIN_WINDOW_S {
            self.waiting > 0 || self.used * 10 >= self.limit * 9
        } else {
            self.max_wait_s >= SEC_RULE_WAIT_ALARM_S
        }
    }

    pub fn json(&self) -> serde_json::Value {
        serde_json::json!({
            "limit": self.limit, "window_s": self.window_s, "used": self.used, "free": self.limit.saturating_sub(self.used),
            "urgent_reserve": self.urgent_reserve, "waiting": self.waiting, "max_wait_s": self.max_wait_s, "waits_total": self.waits_total, "wait_s_total": self.wait_s_total,
            "alarm": self.alarm(),
        })
    }
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
            urgent: AtomicUsize::new(0),
            urgent_reserve: AtomicUsize::new(0),
            waits: AtomicU64::new(0),
            waiting: AtomicUsize::new(0),
            wait_ms: AtomicU64::new(0),
            wait_starts: Mutex::new(Vec::new()),
        }
    }

    /// Лимит, занятость и ожидание основного правила — в health.
    pub fn snapshot(&self, now: Instant) -> BudgetSnap {
        let (limit, window) = self
            .rules
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .first()
            .map_or((0, Duration::ZERO), |r| (r.limit, r.window));
        BudgetSnap {
            limit,
            window_s: window.as_secs(),
            used: self.used(now),
            urgent_reserve: self.urgent_reserve(),
            waiting: self.waiting.load(Relaxed),
            max_wait_s: self
                .wait_starts
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .iter()
                .map(|t| now.saturating_duration_since(*t).as_secs())
                .max()
                .unwrap_or(0),
            waits_total: self.waits.load(Relaxed),
            wait_s_total: self.wait_ms.load(Relaxed) / 1000,
        }
    }

    /// `None` — место занято (отметка «сейчас» записана во всех правилах); иначе — через сколько можно пробовать снова
    /// (по самому долгому из правил, где места нет).
    pub fn try_take(&self, now: Instant) -> Option<Duration> {
        self.try_take_keeping(now, 0)
    }

    /// Как `try_take`, но `keep` мест каждого правила остаются тем, кто берёт без остатка (шарды без соединения); у правила остаётся не меньше одного места.
    pub fn try_take_keeping(&self, now: Instant, keep: usize) -> Option<Duration> {
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
            let limit = r.limit.saturating_sub(keep).max(1);
            if r.taken.len() >= limit {
                // место освободится, когда уйдёт старейшая отметка, из-за которой окно заполнено (при остатке — не первая, а `taken.len() - limit + 1`-я)
                let idx = r.taken.len() - limit;
                let w = r.taken.get(idx).map_or(Duration::ZERO, |t| {
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

    /// Резерв мест для шардов без соединения (см. `urgent_reserve`); в правилах с коротким окном (секунды) резерв не нужен — вызывающий ставит 0.
    pub fn set_urgent_reserve(&self, n: usize) {
        self.urgent_reserve.store(n, Relaxed);
    }

    pub fn urgent_reserve(&self) -> usize {
        self.urgent_reserve.load(Relaxed)
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

    /// Новый лимит основного правила (передача между процессами: сумма лимитов двух процессов на одном IP не больше лимита биржи).
    /// Уже занятые места не снимаются: подключения ждут, пока окно не освободит место под новый лимит.
    pub fn set_limit(&self, limit: usize) {
        if let Some(r) = self
            .rules
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .first_mut()
        {
            r.limit = limit.max(1);
        }
    }

    /// Ждёт места и занимает его; возвращает, сколько ждали. `None` — остановка (любой флаг из `stops`) пришла раньше.
    /// `uncovered` — у шарда сейчас нет ни одного соединения (проверяется на каждом круге): такие подключения идут первыми,
    /// остальные (вторая линия, замена, уже покрытый шард) не берут место, пока хоть одно из первых ждёт.
    pub fn acquire(&self, stops: &[&AtomicBool], uncovered: &dyn Fn() -> bool) -> Option<Duration> {
        let t0 = Instant::now();
        let mut counted = false;
        let mut waited = false;
        let out = loop {
            if stops.iter().any(|s| s.load(Relaxed)) {
                break None;
            }
            let first = uncovered();
            if first != counted {
                if first {
                    self.urgent.fetch_add(1, Relaxed);
                } else {
                    self.urgent.fetch_sub(1, Relaxed);
                }
                counted = first;
            }
            let wait = if !first && self.urgent.load(Relaxed) > 0 {
                Some(Duration::from_millis(40))
            } else {
                self.try_take_keeping(
                    Instant::now(),
                    if first { 0 } else { self.urgent_reserve() },
                )
            };
            match wait {
                None => break Some(t0.elapsed()),
                Some(w) => {
                    if !waited {
                        waited = true;
                        self.waits.fetch_add(1, Relaxed);
                        self.waiting.fetch_add(1, Relaxed);
                        self.wait_starts
                            .lock()
                            .unwrap_or_else(std::sync::PoisonError::into_inner)
                            .push(t0);
                    }
                    std::thread::sleep(
                        w.min(Duration::from_millis(500)) + Duration::from_millis(10),
                    );
                }
            }
        };
        if waited {
            self.waiting.fetch_sub(1, Relaxed);
            let mut starts = self
                .wait_starts
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            if let Some(i) = starts.iter().position(|t| *t == t0) {
                starts.swap_remove(i);
            }
            drop(starts);
            self.wait_ms.fetch_add(
                u64::try_from(t0.elapsed().as_millis()).unwrap_or(u64::MAX),
                Relaxed,
            );
        }
        if counted {
            self.urgent.fetch_sub(1, Relaxed);
        }
        out
    }
}

/// Потолок событий на шард за скользящее окно: замены пути и отбраковки не растут без предела при общей деградации сети (Binance 15.11 18:12:
/// 261 подключение за 3 мин на 25 шардов, бюджет 280 исчерпан, у 8 шардов 123–139 с без соединения).
pub struct Cap {
    max: usize,
    window: Duration,
    stamps: Mutex<VecDeque<Instant>>,
}

impl Cap {
    pub fn new(max: usize, window: Duration) -> Self {
        Self {
            max: max.max(1),
            window,
            stamps: Mutex::new(VecDeque::new()),
        }
    }

    fn pruned(&self, now: Instant) -> std::sync::MutexGuard<'_, VecDeque<Instant>> {
        let mut g = self
            .stamps
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        while g
            .front()
            .is_some_and(|t| now.saturating_duration_since(*t) >= self.window)
        {
            g.pop_front();
        }
        g
    }

    /// Занять место, если оно есть; `false` — потолок.
    pub fn take(&self, now: Instant) -> bool {
        let mut g = self.pruned(now);
        if g.len() >= self.max {
            return false;
        }
        g.push_back(now);
        true
    }

    /// Вернуть место, занятое `take` в момент `now`, если событие не состоялось (пробная замена режима «стоянка», упёршаяся в потолок замен): окно не тратится впустую.
    pub fn refund(&self, now: Instant) {
        let mut g = self.pruned(now);
        if let Some(i) = g.iter().rposition(|t| *t == now) {
            g.remove(i);
        }
    }

    /// Записать событие, уже случившееся (отбраковка пути): потолок не отказывает, но следующее событие подождёт `wait`.
    pub fn note(&self, now: Instant) {
        self.pruned(now).push_back(now);
    }

    /// Через сколько можно следующее событие (`ZERO` — сразу).
    pub fn wait(&self, now: Instant) -> Duration {
        let g = self.pruned(now);
        if g.len() < self.max {
            return Duration::ZERO;
        }
        g.get(g.len() - self.max).map_or(Duration::ZERO, |t| {
            (*t + self.window).saturating_duration_since(now)
        })
    }
}
