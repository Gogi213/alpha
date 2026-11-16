//! Режим «стоянка всей биржи» (G-022, проверка пул-фикса 16.11, A.5): когда у большинства шардов биржи одновременно испорчены линии или стоят данные, новые линии на том же пути
//! так же плохи, как старые (`path_rejected` p50 6–8 с), а замены съедают бюджет подключений (Binance: 94–261 подключение за эпизод при лимите 300 за 5 мин). Пока режим включён,
//! испорченные линии не заменяются (кроме одной пробной на шард за `PROBE_PERIOD`), идущие замены прекращаются, старые соединения несут поток. Шард без единого соединения
//! переподключается как обычно (резерв бюджета, `Budget::set_urgent_reserve`). Не горячий путь: оценка раз в 200 мс из основного цикла процесса.

use std::sync::atomic::{AtomicBool, AtomicI64, AtomicU64, AtomicUsize, Ordering::Relaxed};
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use super::budget::Cap;
use super::metrics::ShardMetrics;

/// Меньше шардов — «большинство» ничего не значит (у Gate 9, у остальных 12–27).
pub const MIN_SHARDS: usize = 4;
/// Режим выключается, когда испорченных шардов не больше половины столько мс подряд (назначаемое, Судье).
pub const CLEAR_MS: i64 = 60_000;
/// Одна пробная замена на шард за это время в режиме стоянки (назначаемое, Судье).
pub const PROBE_PERIOD: Duration = Duration::from_secs(300);

#[derive(Debug, Default)]
pub struct Storm {
    on: AtomicBool,
    since_ms: AtomicI64,
    last_over_ms: AtomicI64,
    episodes: AtomicU64,
    holds: AtomicU64,
    aborts: AtomicU64,
    impaired: AtomicUsize,
    total: AtomicUsize,
}

/// Шард испорчен сейчас: данные стоят (пауза) или ни одна его линия не несёт поток (нет открытой неиспорченной сессии). Испорченная линия при здоровом партнёре шард не портит: партнёр
/// несёт поток, а замена испорченной линии в режиме держится — иначе режим сам себя держал бы (проверка 16.11, Б1: испорченная линия не заменяется → шард «испорчен» → режим не выключается).
pub fn impaired(m: &ShardMetrics) -> bool {
    m.in_gap.load(Relaxed) || !m.lane_carry.iter().any(|b| b.load(Relaxed))
}

impl Storm {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    pub fn is_on(&self) -> bool {
        self.on.load(Relaxed)
    }

    /// Оценка: `impaired` испорченных шардов из `total` на момент `now_ms`. Включается, когда их большинство (≥ `MIN_SHARDS` шардов, больше половины), выключается через `CLEAR_MS`
    /// после последнего момента, когда их было больше половины.
    pub fn evaluate(&self, impaired: usize, total: usize, now_ms: i64) {
        self.impaired.store(impaired, Relaxed);
        self.total.store(total, Relaxed);
        let over = total >= MIN_SHARDS && impaired * 2 > total;
        if over {
            self.last_over_ms.store(now_ms, Relaxed);
            if !self.on.swap(true, Relaxed) {
                self.since_ms.store(now_ms, Relaxed);
                self.episodes.fetch_add(1, Relaxed);
            }
        } else if self.is_on() && now_ms - self.last_over_ms.load(Relaxed) >= CLEAR_MS {
            self.on.store(false, Relaxed);
        }
    }

    /// Замена не открыта: режим держит старую линию.
    pub fn note_hold(&self) {
        self.holds.fetch_add(1, Relaxed);
    }

    /// Идущая замена прекращена: режим включился, пока она искала путь.
    pub fn note_abort(&self) {
        self.aborts.fetch_add(1, Relaxed);
    }

    pub fn json(&self, now_ms: i64) -> Value {
        let on = self.is_on();
        json!({
            "on": on, "since_s": on.then(|| (now_ms - self.since_ms.load(Relaxed)).max(0) / 1000),
            "impaired": self.impaired.load(Relaxed), "shards": self.total.load(Relaxed),
            "episodes": self.episodes.load(Relaxed), "holds": self.holds.load(Relaxed), "aborts": self.aborts.load(Relaxed),
        })
    }
}

/// Решение для линии, которой нужна замена пути (не возрастная пересменка).
#[derive(Debug, PartialEq, Eq)]
pub enum StormGate {
    /// Режим выключен — открывать как обычно; замена, начатая до включения режима, при включении прекращается (`storm_abort`).
    Open,
    /// Режим включён, это пробная замена шарда за период: открывать и не прекращать по режиму — пробы нужны, чтобы линии менялись, пока режим включён (Б1: прежде она прекращалась на первой же итерации).
    Probe,
    /// Режим включён и пробная замена шарда за период уже была — держать старую линию.
    Hold,
}

/// `probe` — потолок «одна пробная замена на шард за `PROBE_PERIOD`» (по одному на шард).
pub fn gate(storm: Option<&Storm>, probe: &Cap, now: Instant) -> StormGate {
    match storm {
        Some(s) if s.is_on() => {
            if probe.take(now) {
                StormGate::Probe
            } else {
                StormGate::Hold
            }
        }
        _ => StormGate::Open,
    }
}

/// Решение для линии, которой нужна замена пути, с учётом потолка замен.
#[derive(Debug, PartialEq, Eq)]
pub enum Admit {
    /// Замену открывать; `Probe` — пробная замена режима «стоянка».
    Go(StormGate),
    /// Режим держит старую линию.
    Hold,
    /// Потолок замен пути шарда исчерпан.
    Capped,
}

/// Допуск замены: возрастная пересменка идёт всегда; остальные проходят режим «стоянка» (`storm`, пробная — `probe`) и потолок замен (`replace`).
/// Пробная замена, не прошедшая потолок замен, возвращается в `probe`: потолок не сжигает её на `PROBE_PERIOD` (проверка 16.11).
pub fn admit(old: bool, storm: Option<&Storm>, probe: &Cap, replace: &Cap, now: Instant) -> Admit {
    if old {
        return Admit::Go(StormGate::Open);
    }
    let gate = gate(storm, probe, now);
    if gate == StormGate::Hold {
        return Admit::Hold;
    }
    if !replace.take(now) {
        if gate == StormGate::Probe {
            probe.refund(now);
        }
        return Admit::Capped;
    }
    Admit::Go(gate)
}
