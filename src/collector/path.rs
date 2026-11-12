//! Качество пути до биржи: окно проверки нового пути и сторож лага живых соединений.
//!
//! `PathProbe` — вердикт «чистый путь» по лагам первых `PROBE_S` с после подписки; `PathWatch` — сессия с p50 лага выше
//! `LAG_LIMIT_MS` подряд окон становится `degraded`. Не знает соединений: решение «открыть замену» принимает шард.
//! Все пороги — назначаемые, из замеров 11–12.11 (В-140: потери пути NL→Токио; у Bybit путь короче, пороги те же).

use std::collections::{HashMap, HashSet};
use std::time::Instant;

pub const PROBE_S: f64 = 15.0;
/// Снапшот подписки несёт старый E.
pub const PROBE_SKIP_S: f64 = 4.0;
pub const PROBE_P50_MS: f64 = 350.0;
pub const PROBE_P99_MS: f64 = 1500.0;
pub const PROBE_MIN_SAMPLES: usize = 5;
/// Подряд отбракованных путей (RTT рукопожатия, затем лаг); дальше соединение принимается (сторож лага остаётся).
pub const PROBE_MAX_REJECTS: u32 = 12;
pub const LAG_LIMIT_MS: f64 = 2000.0;
pub const LAG_BAD_WINDOWS: u32 = 2;
pub const LAG_SKIP_WINDOWS: u32 = 2;
/// Выборок на окно проверки не больше (защита памяти при плотном потоке).
const PROBE_MAX_SAMPLES: usize = 50_000;

/// Значение на позиции `floor(n·p)` по возрастанию (как `percentile` Python-ядра); пусто — `None`.
pub fn percentile(xs: &mut [f64], p: f64) -> Option<f64> {
    if xs.is_empty() {
        return None;
    }
    xs.sort_by(f64::total_cmp);
    #[allow(
        clippy::cast_precision_loss,
        clippy::cast_possible_truncation,
        clippy::cast_sign_loss
    )]
    let i = ((xs.len() as f64 * p) as usize).min(xs.len() - 1);
    xs.get(i).copied()
}

/// Чистый ли путь по лагам окна проверки: мало выборок — не отбраковываем.
pub fn path_verdict(lags: &mut [f64]) -> bool {
    if lags.len() < PROBE_MIN_SAMPLES {
        return true;
    }
    percentile(lags, 0.5).is_some_and(|v| v <= PROBE_P50_MS)
        && percentile(lags, 0.99).is_some_and(|v| v <= PROBE_P99_MS)
}

pub struct PathProbe {
    t0: Instant,
    skip_s: f64,
    total_s: f64,
    lags: Vec<f64>,
}

impl PathProbe {
    pub fn new(t0: Instant) -> Self {
        Self::with_window(t0, PROBE_SKIP_S, PROBE_S)
    }

    /// Окно проверки `skip_s`…`total_s` секунд от `t0` (в тестах — короче боевого).
    pub fn with_window(t0: Instant, skip_s: f64, total_s: f64) -> Self {
        Self {
            t0,
            skip_s,
            total_s,
            lags: Vec::new(),
        }
    }

    pub fn collecting(&self, now: Instant) -> bool {
        now.duration_since(self.t0).as_secs_f64() >= self.skip_s
    }

    pub fn add(&mut self, lag: Option<f64>) {
        if let Some(l) = lag {
            if self.lags.len() < PROBE_MAX_SAMPLES {
                self.lags.push(l);
            }
        }
    }

    pub fn finished(&self, now: Instant) -> bool {
        now.duration_since(self.t0).as_secs_f64() >= self.total_s
    }

    pub fn clean(&mut self) -> bool {
        path_verdict(&mut self.lags)
    }

    /// (число выборок, p50, p99) для события.
    pub fn summary(&mut self) -> (usize, Option<f64>, Option<f64>) {
        let n = self.lags.len();
        (
            n,
            percentile(&mut self.lags, 0.5),
            percentile(&mut self.lags, 0.99),
        )
    }
}

/// Сторож лага по сессиям шарда: выборки окна, возраст в окнах, подряд плохих окон; `degraded` — сессии с испорченным
/// путём (и без pong, см. session).
#[derive(Default)]
pub struct PathWatch {
    lags: HashMap<u32, Vec<f64>>,
    age: HashMap<u32, u32>,
    bad: HashMap<u32, u32>,
    degraded: HashSet<u32>,
    /// Сессии, принятые без проверки пути (шард был без соединения) при медленном рукопожатии: путь улучшается позже заменой.
    slow: HashSet<u32>,
}

impl PathWatch {
    pub fn open(&mut self, seq: u32) {
        self.lags.insert(seq, Vec::new());
        self.age.insert(seq, 0);
        self.bad.insert(seq, 0);
    }

    pub fn close(&mut self, seq: u32) {
        self.lags.remove(&seq);
        self.age.remove(&seq);
        self.bad.remove(&seq);
        self.degraded.remove(&seq);
        self.slow.remove(&seq);
    }

    pub fn sample(&mut self, seq: u32, lag: f64) {
        self.lags.entry(seq).or_default().push(lag);
    }

    pub fn mark_degraded(&mut self, seq: u32) {
        self.degraded.insert(seq);
    }

    pub fn mark_slow(&mut self, seq: u32) {
        self.slow.insert(seq);
    }

    pub fn is_slow(&self, seq: u32) -> bool {
        self.slow.contains(&seq)
    }

    pub fn is_degraded(&self, seq: u32) -> bool {
        self.degraded.contains(&seq)
    }

    /// Конец окна: сессия с p50 лага выше `LAG_LIMIT_MS` `LAG_BAD_WINDOWS` окон подряд — в `degraded`; выборки сбрасываются.
    pub fn judge(&mut self) {
        for (seq, xs) in &mut self.lags {
            let p50 = percentile(xs, 0.5);
            let age = self.age.entry(*seq).or_insert(0);
            *age += 1;
            let bad = self.bad.entry(*seq).or_insert(0);
            if *age > LAG_SKIP_WINDOWS && p50.is_some_and(|v| v > LAG_LIMIT_MS) {
                *bad += 1;
            } else {
                *bad = 0;
            }
            if *bad >= LAG_BAD_WINDOWS {
                self.degraded.insert(*seq);
            }
            xs.clear();
        }
    }
}
