//! Проверка пауз шарда сделок по REST (G-022, 16.11): была ли на бирже сделка внутри паузы.
//!
//! У Bybit номера сделки нет (UUID), дозапроса нет, а пауза шарда сделок краснила строку страницы. Разреженный шард (80 символов, 7–24 сделки/с, порог 3 с) молчит из-за тишины рынка примерно раз
//! в час: четыре паузы 16.11 сверены по REST `recent-trade` и свечам 1 м — сделок биржи внутри нет, записи полны (`docs/findings/collect-followups-deploy-2026-11-16.md`).
//! Здесь та же сверка делается сама, в отдельном потоке (не в событийном цикле), для каждой закончившейся паузы шарда сделок:
//! - запрос уходит из `shard::end_gap`; поток по символам шарда берёт из REST время биржи последних 1 000 сделок (`Exchange::trade_times`);
//! - окно проверки `[приём последнего кадра до паузы, приём первого кадра после − p99 лага сделок шарда]` (концы — по приёму кадров, не по тику надзора; p99 измерен на обнаружении паузы, нет — `LAG_FALLBACK_MS`):
//!   доставленная запись несёт время биржи на лаг прихода раньше приёма, поэтому последняя сделка до паузы и первая после неё (лаг ≤ p99) в окно не входят — они доставлены, не потеря; пустое окно — `Unverified`;
//! - есть сделка биржи в окне — `Loss` (потеря: красит строку); все символы проверены и сделок нет — `Quiet` (тишина рынка, не красит); REST не ответил, окно не покрыто ответом, пауза старше `MAX_AGE_MS` — `Unverified` (красит:
//!   тишина не доказана);
//! - отказ по частоте (HTTP 429/418, `retCode` 10006/10018, HTTP 403 у Bybit — бан IP на 10 минут) ставит на паузу весь поток, попытка не тратится; запросы идут не чаще раза в `MIN_CALL_GAP`.
//!
//! Паузы шардов книги и mark сюда не попадают — они потеря, как были.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, AtomicI64, AtomicU64, Ordering::Relaxed};
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use super::exchange::{Exchange, RestError};

/// Лаг прихода сделок, мс, когда p99 шарда измерить нечем (нет записей в окне 60 с на обнаружении паузы); измеренный p99 `trade01` Bybit 16.11 — 133 мс; назначаемое (Судье).
pub const LAG_FALLBACK_MS: i64 = 500;
/// Не чаще одного запроса к REST за это время: 6,7 запроса/с на IP при лимите Bybit 600 за 5 с (общем со всеми запросами с адреса).
pub const MIN_CALL_GAP: Duration = Duration::from_millis(150);
/// Пауза старше этого не проверяется: `recent-trade` отдаёт 1 000 последних сделок символа, окно паузы из них уходит; назначаемое (Судье).
pub const MAX_AGE_MS: i64 = 600_000;
/// Повторы символа после сбоя REST (не отказа по частоте) и ожидание перед ними.
pub const RETRY_DELAYS: [Duration; 2] = [Duration::from_secs(5), Duration::from_secs(20)];
/// Не проверенные паузы в очереди: больше — считаются `Unverified` без запроса (не рост памяти при штормовом потоке пауз).
pub const MAX_QUEUE: usize = 64;
/// Журнал вердиктов: хранится столько, сколько нужно часовым счётчикам и ряду минут.
const LOG_KEEP_MS: i64 = 3 * 3_600_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Verdict {
    /// На бирже в окне паузы сделок нет — тишина рынка.
    Quiet,
    /// В окне паузы на бирже были сделки — потеря.
    Loss,
    /// Тишина не доказана (сбой REST, окно не покрыто, слишком старая пауза) — как потеря.
    Unverified,
}

impl Verdict {
    pub fn name(self) -> &'static str {
        match self {
            Self::Quiet => "quiet",
            Self::Loss => "loss",
            Self::Unverified => "unverified",
        }
    }

    /// Красит ли строку и столбик.
    pub fn is_bad(self) -> bool {
        self != Self::Quiet
    }
}

/// Куда писать итог проверки: событие шарда (имя, поля).
pub type Emit = Box<dyn Fn(&str, Value) + Send>;

/// Пауза, которую надо проверить: шард, его потоки на момент конца паузы, границы (мс UTC, приём кадров), куда писать итог.
pub struct PauseReq {
    pub shard: String,
    pub streams: Vec<String>,
    /// Приём последнего кадра до паузы.
    pub start_ms: i64,
    /// Приём первого кадра после паузы; при `end_exact == false` (пауза закончилась обрывом соединений) — оценка `start + наибольшее молчание на тике`, раньше настоящего конца.
    pub end_ms: i64,
    pub end_exact: bool,
    /// p99 лага прихода сделок шарда на обнаружении паузы (мс вверх); `None` — `LAG_FALLBACK_MS`.
    pub lag_p99_ms: Option<i64>,
    pub emit: Emit,
}

#[derive(Debug, Default)]
pub struct Outcome {
    pub verdict: Option<Verdict>,
    pub symbols: usize,
    pub checked: usize,
    pub unknown: usize,
    pub in_window: usize,
    pub why: &'static str,
    /// Применённый лаг (мс) и окно `[lo, hi]` (мс UTC).
    pub lag_used_ms: i64,
    pub window: (i64, i64),
}

#[derive(Clone, Copy, Debug)]
struct Judged {
    start_ms: i64,
    ms: i64,
    verdict: Verdict,
}

/// Счётчики проверки для health и страницы.
#[derive(Debug, Default)]
pub struct PauseStats {
    pub requested: AtomicU64,
    pub quiet: AtomicU64,
    pub loss: AtomicU64,
    pub unverified: AtomicU64,
    pub rest_requests: AtomicU64,
    pub rest_errors: AtomicU64,
    /// Отказы по частоте: каждый ставит на паузу весь поток; пауза до (мс UTC, 0 — нет) и суммарная, с.
    pub rate_limited: AtomicU64,
    pause_until_ms: AtomicI64,
    pub paused_s_total: AtomicU64,
    last_error: Mutex<Option<String>>,
    log: Mutex<VecDeque<Judged>>,
}

impl PauseStats {
    fn error(&self, e: &str) {
        self.rest_errors.fetch_add(1, Relaxed);
        *self
            .last_error
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) =
            Some(e.chars().take(200).collect());
    }

    fn limited(&self, now_ms: i64, d: Duration) {
        self.rate_limited.fetch_add(1, Relaxed);
        self.paused_s_total.fetch_add(d.as_secs(), Relaxed);
        self.pause_until_ms.store(
            now_ms + i64::try_from(d.as_millis()).unwrap_or(i64::MAX / 4),
            Relaxed,
        );
    }

    pub fn paused_s(&self, now_ms: i64) -> u64 {
        u64::try_from((self.pause_until_ms.load(Relaxed) - now_ms).max(0))
            .unwrap_or(0)
            .div_ceil(1000)
    }

    /// Вердикт по паузе, начавшейся в `start_ms`, длиной `ms`.
    pub fn judged(&self, start_ms: i64, ms: i64, v: Verdict) {
        {
            let mut l = self
                .log
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            l.push_back(Judged {
                start_ms,
                ms,
                verdict: v,
            });
            while l
                .front()
                .is_some_and(|j| start_ms - j.start_ms > LOG_KEEP_MS)
            {
                l.pop_front();
            }
        }
        // счётчик — после журнала: ряд минут по росту `bad_events` пересчитывает минуты и должен увидеть запись
        match v {
            Verdict::Quiet => &self.quiet,
            Verdict::Loss => &self.loss,
            Verdict::Unverified => &self.unverified,
        }
        .fetch_add(1, Relaxed);
    }

    /// Сколько проверок ещё не закончено (в очереди и в работе).
    pub fn pending(&self) -> u64 {
        self.requested.load(Relaxed).saturating_sub(self.decided())
    }

    fn decided(&self) -> u64 {
        self.quiet.load(Relaxed) + self.loss.load(Relaxed) + self.unverified.load(Relaxed)
    }

    /// Паузы, красящие строку (потеря и «не проверено»), по числу — ряд минут пересчитывается, когда оно растёт.
    pub fn bad_events(&self) -> u64 {
        self.loss.load(Relaxed) + self.unverified.load(Relaxed)
    }

    /// Миллисекунды пауз с началом в `[from_ms, to_ms)`, красящих строку, — строка минуты ряда.
    pub fn bad_ms_between(&self, from_ms: i64, to_ms: i64) -> i64 {
        self.log
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .iter()
            .filter(|j| j.verdict.is_bad() && (from_ms..to_ms).contains(&j.start_ms))
            .map(|j| j.ms)
            .sum()
    }

    /// Раздел `pause_check` health. `ongoing_s` — самая долгая пауза шарда сделок, идущая сейчас (0 — нет).
    pub fn json(&self, now_ms: i64, ongoing_s: u64) -> Value {
        let cut = now_ms - 3_600_000;
        let (mut q, mut l, mut u) = (0u64, 0u64, 0u64);
        for j in self
            .log
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .iter()
            .filter(|j| j.start_ms > cut)
        {
            match j.verdict {
                Verdict::Quiet => q += 1,
                Verdict::Loss => l += 1,
                Verdict::Unverified => u += 1,
            }
        }
        json!({
            "enabled": true, "quiet_1h": q, "loss_1h": l, "unverified_1h": u, "pending": self.pending(), "ongoing_s": ongoing_s,
            "quiet": self.quiet.load(Relaxed), "loss": self.loss.load(Relaxed), "unverified": self.unverified.load(Relaxed),
            "rest_requests": self.rest_requests.load(Relaxed), "rest_errors": self.rest_errors.load(Relaxed),
            "rate_limited": self.rate_limited.load(Relaxed), "paused_s": self.paused_s(now_ms), "paused_s_total": self.paused_s_total.load(Relaxed),
            "last_error": self.last_error.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone(),
        })
    }
}

/// Что шард получает для постановки паузы на проверку.
#[derive(Clone, Debug)]
pub struct PauseCheck {
    pub tx: Sender<PauseReq>,
    pub stats: Arc<PauseStats>,
    queued: Arc<AtomicU64>,
}

impl PauseCheck {
    /// Поставить закончившуюся паузу на проверку. Очередь полна — вердикт `Unverified` сразу, без запроса.
    pub fn submit(&self, req: PauseReq) {
        self.stats.requested.fetch_add(1, Relaxed);
        if self.queued.load(Relaxed) >= MAX_QUEUE as u64 {
            let ms = (req.end_ms - req.start_ms).max(0);
            self.stats.judged(req.start_ms, ms, Verdict::Unverified);
            (req.emit)(
                "pause_check",
                json!({"verdict": "unverified", "why": "queue_full", "pause_start_ms": req.start_ms, "pause_end_ms": req.end_ms, "end_exact": req.end_exact}),
            );
            return;
        }
        self.queued.fetch_add(1, Relaxed);
        if let Err(e) = self.tx.send(req) {
            // поток остановлен: проверка не состоится — не оставлять паузу «проверяется»
            self.queued.fetch_sub(1, Relaxed);
            let r = e.0;
            self.stats.judged(
                r.start_ms,
                (r.end_ms - r.start_ms).max(0),
                Verdict::Unverified,
            );
        }
    }
}

/// Источник: поток шарда → время биржи (мс) последних сделок символа (`Exchange::trade_times`); в проверках подменяется.
pub type FetchFn<'a> = &'a dyn Fn(&str) -> Option<Result<Vec<i64>, RestError>>;

/// Темп и повторы (в проверках сокращаются).
#[derive(Clone, Debug)]
pub struct WorkCfg {
    pub min_call_gap: Duration,
    pub retry: Vec<Duration>,
}

impl Default for WorkCfg {
    fn default() -> Self {
        Self {
            min_call_gap: MIN_CALL_GAP,
            retry: RETRY_DELAYS.to_vec(),
        }
    }
}

fn wall_ms() -> i64 {
    super::events::now_ms()
}

/// Поток проверки; `None` — у биржи такой проверки нет (есть дозапрос по номерам).
pub fn spawn(
    exch: Arc<dyn Exchange>,
    stop: Arc<AtomicBool>,
) -> Option<(PauseCheck, JoinHandle<()>)> {
    if !exch.pause_check() {
        return None;
    }
    let (tx, rx) = channel::<PauseReq>();
    let stats = Arc::new(PauseStats::default());
    let queued = Arc::new(AtomicU64::new(0));
    let (st, q) = (Arc::clone(&stats), Arc::clone(&queued));
    let join = std::thread::Builder::new()
        .name("pausecheck".into())
        .spawn(move || {
            let fetch = |s: &str| exch.trade_times(s);
            work(&fetch, &WorkCfg::default(), &rx, &st, &q, &stop);
        })
        .ok()?;
    Some((PauseCheck { tx, stats, queued }, join))
}

pub(crate) fn work(
    fetch: FetchFn<'_>,
    cfg: &WorkCfg,
    rx: &Receiver<PauseReq>,
    st: &Arc<PauseStats>,
    queued: &Arc<AtomicU64>,
    stop: &Arc<AtomicBool>,
) {
    let mut last_call = Instant::now()
        .checked_sub(cfg.min_call_gap)
        .unwrap_or_else(Instant::now);
    while !stop.load(Relaxed) {
        let req = match rx.recv_timeout(Duration::from_millis(200)) {
            Ok(r) => r,
            Err(RecvTimeoutError::Timeout) => continue,
            Err(RecvTimeoutError::Disconnected) => return,
        };
        let o = check(fetch, cfg, &req, st, stop, &mut last_call);
        queued.fetch_sub(1, Relaxed);
        let Some(v) = o.verdict else {
            continue; // остановка: итога нет
        };
        let ms = (req.end_ms - req.start_ms).max(0);
        st.judged(req.start_ms, ms, v);
        (req.emit)(
            "pause_check",
            json!({
                "verdict": v.name(), "why": o.why, "pause_start_ms": req.start_ms, "pause_end_ms": req.end_ms, "pause_ms": ms,
                "symbols": o.symbols, "checked": o.checked, "unknown": o.unknown, "trades_in_window": o.in_window,
                "end_exact": req.end_exact, "lag_p99_ms": req.lag_p99_ms, "lag_used_ms": o.lag_used_ms, "window_lo": o.window.0, "window_hi": o.window.1,
            }),
        );
    }
}

/// Спит `d`, но не дольше остановки; `false` — остановка.
fn sleep_stop(stop: &AtomicBool, d: Duration) -> bool {
    let end = Instant::now() + d;
    while Instant::now() < end {
        if stop.load(Relaxed) {
            return false;
        }
        std::thread::sleep(
            Duration::from_millis(50).min(end.saturating_duration_since(Instant::now())),
        );
    }
    !stop.load(Relaxed)
}

/// Проверка одной паузы. `verdict: None` — процесс останавливается.
pub(crate) fn check(
    fetch: FetchFn<'_>,
    cfg: &WorkCfg,
    req: &PauseReq,
    st: &PauseStats,
    stop: &AtomicBool,
    last_call: &mut Instant,
) -> Outcome {
    let mut o = Outcome {
        symbols: req.streams.len(),
        ..Outcome::default()
    };
    o.lag_used_ms = req.lag_p99_ms.unwrap_or(LAG_FALLBACK_MS);
    let (lo, hi) = (req.start_ms, req.end_ms - o.lag_used_ms);
    o.window = (lo, hi);
    if hi <= lo {
        // пауза не длиннее лага: окно пусто, тишина не доказана
        o.unknown = o.symbols;
        o.verdict = Some(Verdict::Unverified);
        o.why = "window_empty";
        return o;
    }
    for stream in &req.streams {
        let mut attempts = 0;
        loop {
            if stop.load(Relaxed) {
                return o;
            }
            if wall_ms() - req.end_ms > MAX_AGE_MS {
                o.unknown = o.symbols - o.checked;
                o.verdict = Some(Verdict::Unverified);
                o.why = "too_old";
                return o;
            }
            let since = last_call.elapsed();
            if since < cfg.min_call_gap && !sleep_stop(stop, cfg.min_call_gap - since) {
                return o;
            }
            *last_call = Instant::now();
            st.rest_requests.fetch_add(1, Relaxed);
            match fetch(stream) {
                None => {
                    o.unknown = o.symbols - o.checked;
                    o.verdict = Some(Verdict::Unverified);
                    o.why = "unsupported";
                    return o;
                }
                Some(Ok(times)) => {
                    o.checked += 1;
                    // ответ покрывает окно, если самая старая сделка в нём раньше начала окна (иначе старше ответа могли остаться сделки окна); нет сделок вовсе — покрывает
                    let covered = times.iter().min().is_none_or(|&t| t < lo);
                    let inside = times.iter().filter(|&&t| (lo..=hi).contains(&t)).count();
                    o.in_window += inside;
                    if !covered && inside == 0 {
                        o.unknown += 1;
                    }
                    break;
                }
                Some(Err(e)) => {
                    st.error(&e.msg);
                    if let Some(d) = e.limited {
                        // отказ по частоте: весь поток стоит, попытка символа не тратится
                        st.limited(wall_ms(), d);
                        if !sleep_stop(stop, d) {
                            return o;
                        }
                    } else if let Some(d) = cfg.retry.get(attempts) {
                        attempts += 1;
                        if !sleep_stop(stop, *d) {
                            return o;
                        }
                    } else {
                        o.unknown += 1;
                        break;
                    }
                }
            }
        }
        if o.in_window > 0 {
            // сделка биржи внутри паузы — потеря, остальные символы не нужны
            o.verdict = Some(Verdict::Loss);
            o.why = "trades_in_pause";
            return o;
        }
    }
    if o.unknown > 0 {
        o.verdict = Some(Verdict::Unverified);
        o.why = "not_proven";
    } else {
        o.verdict = Some(Verdict::Quiet);
        o.why = "no_trades_in_pause";
    }
    o
}
