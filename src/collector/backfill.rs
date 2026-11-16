//! Дозапрос сделок, потерянных при сбросе соединений (G-022, `docs/findings/health-red-review-2026-11-16.md` A.6 / п. 4).
//!
//! Binance 15.11 14:15: биржа сама оборвала оба соединения шарда `trade02`, замены подписались заново и получили только новые сделки — хвост из 8 081 сделки в разорванных
//! соединениях не пришёл. Номер сделки у Binance, OKX, BingX и Gate сплошной по символу, поэтому потеря видна по номерам, а не по паузам:
//! - `IdTracker` (в писателе шарда): помнит старший номер символа; номер выше следующего открывает «возможный пропуск»; пришедший позже (другая линия, замена) его закрывает;
//!   пропуск, не закрытый за `CONFIRM_MS`, — подтверждённая потеря, запрос уходит в поток дозапроса;
//! - поток дозапроса (`spawn`): забирает недостающие номера из REST страницами (не в событийном цикле, не чаще раза в `MIN_CALL_GAP`) и пишет их в **отдельный** файл
//!   `<корень>/<день>/backfill-<биржа>-<запуск>.tsv.zst` — в файлы ws они не попадают никогда;
//! - `BackfillStats`: счётчики для health; потеря = подтверждённые номера, которые не удалось получить.
//!
//! Строки файла дозапроса (поля через табуляцию, значения биржи строками как пришли):
//! `rh <мс> <биржа> <запуск> <версия> <пометка>` — заголовок файла;
//! `rt <мс получения> <символ> <номер> <время сделки, мс> <цена> <размер> <f> <l> <m 0/1> <пропуск от> <пропуск до>` — сделка из REST;
//! `rg <мс> <символ> <от> <до> <найден, мс> <получено номеров> <статус> <попыток> <шард>` — итог по пропуску (`ok`, `partial`, `failed`, `too_large`, `unsupported`).

use std::collections::{HashMap, VecDeque};
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering::Relaxed};
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use super::exchange::{Exchange, RestTrade, TradePage, TradeSeq};
use super::writer::day_string;

/// Пропуск номеров подтверждается, если за это время номера не пришли (другая линия и замена при перекрытии приносят их за секунды); назначаемое (Судье).
pub const CONFIRM_MS: i64 = 30_000;
/// Открытых пропусков на символ: больше — считаем и отбрасываем (не рост памяти при штормовом потоке).
pub const MAX_PENDING_PER_SYMBOL: usize = 64;
/// Скачок номера выше этого — разрыв нумерации (перелистинг контракта), а не потеря: пропуском не считается.
pub const MAX_JUMP: i64 = 5_000_000;
/// Пропуск крупнее не дозапрашивается (≈ 100 запросов по 1 000): потеря с отметкой `too_large`.
pub const MAX_GAP_IDS: i64 = 100_000;
/// Не чаще одного запроса к REST за это время (Binance `aggTrades` — вес 20 из 2 400 в минуту на IP, общий с опросом пула; 0,67 запроса/с — 800 в минуту).
pub const MIN_CALL_GAP: Duration = Duration::from_millis(1500);
/// Попыток на пропуск и ожидание перед следующей (после сбоя REST).
pub const RETRY_DELAYS: [Duration; 3] = [
    Duration::from_secs(20),
    Duration::from_secs(60),
    Duration::from_secs(300),
];

/// Источник страниц дозапроса: `(символ, от, до)` → страница (`Exchange::trade_page`); в проверках подменяется.
pub type FetchFn<'a> = &'a dyn Fn(&str, i64, i64) -> Option<Result<TradePage, String>>;

/// Темп и повторы потока дозапроса (в проверках сокращаются).
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

/// Подтверждённая потеря: номера `lo..=hi` символа не пришли.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct GapReq {
    pub shard: String,
    pub symbol: String,
    pub lo: i64,
    pub hi: i64,
    /// Когда пропуск замечен (мс UTC).
    pub found_ms: i64,
}

impl GapReq {
    pub fn ids(&self) -> i64 {
        self.hi - self.lo + 1
    }
}

#[derive(Debug)]
struct Range {
    lo: i64,
    hi: i64,
    since_ms: i64,
}

#[derive(Debug)]
struct SymIds {
    sym: Box<[u8]>,
    hi: i64,
    pending: Vec<Range>,
}

/// Поиск пропусков номеров сделок по символам одного писателя. Видит только записанные (не дубли) сделки в порядке записи.
#[derive(Debug, Default)]
pub struct IdTracker {
    syms: HashMap<u32, SymIds>,
    pending: usize,
    next_scan_ms: i64,
    /// Счётчики для отчёта: закрытые без дозапроса (пришли позже), отброшенные при переполнении, разрывы нумерации.
    pub reordered: u64,
    pub overflow: u64,
    pub jumps: u64,
}

impl IdTracker {
    pub fn pending(&self) -> usize {
        self.pending
    }

    /// Запись покрывает номера `lo..=hi` символа `sid`, приём `rx_ms`.
    pub fn on_span(&mut self, sid: u32, sym: &[u8], lo: i64, hi: i64, rx_ms: i64) {
        let s = self.syms.entry(sid).or_insert_with(|| SymIds {
            sym: sym.into(),
            hi: i64::MIN,
            pending: Vec::new(),
        });
        if s.hi == i64::MIN {
            s.hi = hi;
            return;
        }
        if !s.pending.is_empty() && lo <= s.hi {
            let before = s.pending.len();
            if fill(&mut s.pending, lo, hi.min(s.hi)) {
                self.reordered += 1;
            }
            self.pending = self.pending.saturating_sub(before) + s.pending.len();
        }
        if lo > s.hi.saturating_add(1) {
            if lo - s.hi - 1 > MAX_JUMP {
                self.jumps += 1;
            } else if s.pending.len() < MAX_PENDING_PER_SYMBOL {
                s.pending.push(Range {
                    lo: s.hi + 1,
                    hi: lo - 1,
                    since_ms: rx_ms,
                });
                self.pending += 1;
            } else {
                self.overflow += 1;
            }
        }
        if hi > s.hi {
            s.hi = hi;
        }
    }

    /// Раз в секунду (только пока есть открытые пропуски): пропуски старше `CONFIRM_MS` — подтверждённые потери.
    pub fn scan(&mut self, now_ms: i64, shard: &str) -> Vec<GapReq> {
        if self.pending == 0 || now_ms < self.next_scan_ms {
            return Vec::new();
        }
        self.next_scan_ms = now_ms + 1000;
        let mut out = Vec::new();
        for s in self.syms.values_mut() {
            if s.pending.is_empty() {
                continue;
            }
            let sym = String::from_utf8_lossy(&s.sym).into_owned();
            s.pending.retain(|r| {
                if now_ms - r.since_ms >= CONFIRM_MS {
                    out.push(GapReq {
                        shard: shard.to_string(),
                        symbol: sym.clone(),
                        lo: r.lo,
                        hi: r.hi,
                        found_ms: r.since_ms,
                    });
                    false
                } else {
                    true
                }
            });
        }
        self.pending = self.pending.saturating_sub(out.len());
        out.sort_by(|a, b| a.symbol.cmp(&b.symbol).then(a.lo.cmp(&b.lo)));
        out
    }
}

/// Вычесть `lo..=hi` из открытых пропусков; `true` — что-то вычтено.
fn fill(pending: &mut Vec<Range>, lo: i64, hi: i64) -> bool {
    let mut out = Vec::with_capacity(pending.len() + 1);
    let mut hit = false;
    for r in pending.drain(..) {
        if hi < r.lo || lo > r.hi {
            out.push(r);
            continue;
        }
        hit = true;
        if lo > r.lo {
            out.push(Range {
                lo: r.lo,
                hi: lo - 1,
                since_ms: r.since_ms,
            });
        }
        if hi < r.hi {
            out.push(Range {
                lo: hi + 1,
                hi: r.hi,
                since_ms: r.since_ms,
            });
        }
    }
    *pending = out;
    hit
}

/// Счётчики дозапроса для health и страницы: подтверждённые номера — получены или потеряны.
#[derive(Debug, Default)]
pub struct BackfillStats {
    pub gaps_confirmed: AtomicU64,
    pub ids_confirmed: AtomicU64,
    pub ids_backfilled: AtomicU64,
    /// Окончательная потеря: не удалось получить (сбои REST, нет данных у биржи, слишком крупный пропуск, у биржи нет запроса).
    pub ids_lost: AtomicU64,
    pub rest_requests: AtomicU64,
    pub rest_errors: AtomicU64,
    pub trades_written: AtomicU64,
    last_error: Mutex<Option<String>>,
    /// Окончательные потери по времени: (мс, номеров) — поминутный ряд.
    lost_log: Mutex<VecDeque<(i64, u64)>>,
}

impl BackfillStats {
    fn error(&self, e: &str) {
        self.rest_errors.fetch_add(1, Relaxed);
        *self
            .last_error
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = Some(e.chars().take(200).collect());
    }

    pub fn lost(&self, now_ms: i64, ids: u64) {
        self.ids_lost.fetch_add(ids, Relaxed);
        let mut l = self
            .lost_log
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        l.push_back((now_ms, ids));
        while l.len() > 100_000 {
            l.pop_front();
        }
    }

    /// Номеров, окончательно потерянных в `[from_ms, to_ms)` — строка минуты ряда.
    pub fn lost_between(&self, from_ms: i64, to_ms: i64) -> u64 {
        self.lost_log
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .iter()
            .filter(|(t, _)| (from_ms..to_ms).contains(t))
            .map(|(_, n)| *n)
            .sum()
    }

    /// Раздел `backfill` health. `open_ids` — подтверждены, ещё не получены и не потеряны (в очереди или в повторе).
    pub fn json(&self, enabled: bool, now_ms: i64) -> Value {
        let (c, b, l) = (
            self.ids_confirmed.load(Relaxed),
            self.ids_backfilled.load(Relaxed),
            self.ids_lost.load(Relaxed),
        );
        let lost_1h: u64 = self.lost_between(now_ms - 3_600_000, now_ms + 1);
        json!({
            "enabled": enabled, "gaps_confirmed": self.gaps_confirmed.load(Relaxed), "ids_confirmed": c, "ids_backfilled": b, "ids_lost": l,
            "ids_lost_1h": lost_1h, "open_ids": c.saturating_sub(b + l), "rest_requests": self.rest_requests.load(Relaxed),
            "rest_errors": self.rest_errors.load(Relaxed), "trades_written": self.trades_written.load(Relaxed),
            "last_error": self.last_error.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone(),
        })
    }
}

/// Что писатель шарда получает для поиска пропусков и отправки запросов.
#[derive(Clone, Debug)]
pub struct Backfill {
    pub tx: Sender<GapReq>,
    pub stats: Arc<BackfillStats>,
    pub seq: TradeSeq,
}

struct Job {
    req: GapReq,
    attempts: usize,
    not_before: Instant,
    /// Ещё не запрошенная часть диапазона и сколько номеров уже получено.
    rest: Option<(i64, i64)>,
    got: i64,
}

/// Файл дозапроса: сутки (UTC) — свой каталог, как у файлов ws; кадры zstd дописываются по пачке (читать с `read_across_frames`).
pub(crate) struct OutFile {
    root: PathBuf,
    exchange: &'static str,
    run_id: i64,
    day: Option<i64>,
    file: Option<std::fs::File>,
}

impl OutFile {
    pub(crate) fn new(root: PathBuf, exchange: &'static str, run_id: i64) -> Self {
        Self {
            root,
            exchange,
            run_id,
            day: None,
            file: None,
        }
    }

    fn write(&mut self, now_ms: i64, lines: &str) {
        let day = now_ms.div_euclid(86_400_000);
        let mut text = String::new();
        if self.day != Some(day) {
            let dir = self.root.join(day_string(day));
            let _ = std::fs::create_dir_all(&dir);
            self.file = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(dir.join(format!("backfill-{}-{}.tsv.zst", self.exchange, self.run_id)))
                .ok();
            self.day = Some(day);
            text.push_str(&format!(
                "rh\t{now_ms}\t{}\t{}\t{}\tREST backfill of trades lost on the websocket; not websocket data, not part of the ws files\n",
                self.exchange,
                self.run_id,
                super::VERSION
            ));
        }
        text.push_str(lines);
        if let (Some(f), Ok(z)) = (self.file.as_mut(), zstd::bulk::compress(text.as_bytes(), 3)) {
            let _ = f.write_all(&z);
            let _ = f.flush();
        }
    }
}

fn status_of(got: i64, ids: i64, failed: bool) -> &'static str {
    if failed {
        "failed"
    } else if got >= ids {
        "ok"
    } else {
        "partial"
    }
}

/// Поток дозапроса: очередь пропусков, страницы REST, файл. Остановка — `stop`; остаток очереди при остановке остаётся открытым (health покажет `open_ids`).
pub fn spawn(
    exch: Arc<dyn Exchange>,
    root: PathBuf,
    run_id: i64,
    stop: Arc<AtomicBool>,
) -> Option<(Backfill, JoinHandle<()>)> {
    let seq = exch.trade_seq()?;
    let (tx, rx) = channel::<GapReq>();
    let stats = Arc::new(BackfillStats::default());
    let st = Arc::clone(&stats);
    let name = exch.name();
    let join = std::thread::Builder::new()
        .name("backfill".into())
        .spawn(move || {
            let mut out = OutFile::new(root, name, run_id);
            let fetch = |s: &str, lo: i64, hi: i64| exch.trade_page(s, lo, hi);
            work(&fetch, &WorkCfg::default(), &rx, &st, &mut out, &stop);
        })
        .ok()?;
    Some((Backfill { tx, stats, seq }, join))
}

fn wall_ms() -> i64 {
    super::events::now_ms()
}

pub(crate) fn work(
    fetch: FetchFn<'_>,
    cfg: &WorkCfg,
    rx: &Receiver<GapReq>,
    st: &Arc<BackfillStats>,
    out: &mut OutFile,
    stop: &Arc<AtomicBool>,
) {
    let mut queue: VecDeque<Job> = VecDeque::new();
    let mut last_call = Instant::now()
        .checked_sub(cfg.min_call_gap)
        .unwrap_or_else(Instant::now);
    while !stop.load(Relaxed) {
        match rx.recv_timeout(Duration::from_millis(200)) {
            Ok(req) => queue.push_back(Job {
                rest: Some((req.lo, req.hi)),
                req,
                attempts: 0,
                not_before: Instant::now(),
                got: 0,
            }),
            Err(RecvTimeoutError::Timeout) => {}
            Err(RecvTimeoutError::Disconnected) => {
                if queue.is_empty() {
                    return;
                }
            }
        }
        while let Ok(req) = rx.try_recv() {
            queue.push_back(Job {
                rest: Some((req.lo, req.hi)),
                req,
                attempts: 0,
                not_before: Instant::now(),
                got: 0,
            });
        }
        let now = Instant::now();
        let Some(pos) = queue.iter().position(|j| j.not_before <= now) else {
            continue;
        };
        let Some(mut job) = queue.remove(pos) else {
            continue;
        };
        let ids = job.req.ids();
        if ids > MAX_GAP_IDS {
            finish(st, out, &job, "too_large", ids);
            continue;
        }
        let Some((lo, hi)) = job.rest else {
            finish(st, out, &job, status_of(job.got, ids, false), ids - job.got);
            continue;
        };
        if now.duration_since(last_call) < cfg.min_call_gap {
            std::thread::sleep(cfg.min_call_gap.saturating_sub(now.duration_since(last_call)));
        }
        last_call = Instant::now();
        st.rest_requests.fetch_add(1, Relaxed);
        match fetch(&job.req.symbol, lo, hi) {
            None => finish(st, out, &job, "unsupported", ids),
            Some(Ok(page)) => {
                job.attempts = 0;
                job.got += i64::try_from(page.trades.len()).unwrap_or(0);
                write_trades(st, out, &job.req, &page.trades);
                job.rest = page.rest.filter(|(a, b)| a <= b);
                if job.rest.is_none() {
                    finish(st, out, &job, status_of(job.got, ids, false), (ids - job.got).max(0));
                } else {
                    job.not_before = Instant::now();
                    queue.push_front(job);
                }
            }
            Some(Err(e)) => {
                st.error(&e);
                if let Some(d) = cfg.retry.get(job.attempts) {
                    job.attempts += 1;
                    job.not_before = Instant::now() + *d;
                    queue.push_back(job);
                } else {
                    finish(st, out, &job, "failed", (ids - job.got).max(0));
                }
            }
        }
    }
}

fn write_trades(st: &BackfillStats, out: &mut OutFile, req: &GapReq, trades: &[RestTrade]) {
    if trades.is_empty() {
        return;
    }
    let now = wall_ms();
    let mut s = String::new();
    for t in trades {
        s.push_str(&format!(
            "rt\t{now}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\n",
            req.symbol,
            t.id,
            t.t_ms,
            t.p,
            t.q,
            t.f,
            t.l,
            u8::from(t.m),
            req.lo,
            req.hi
        ));
    }
    st.trades_written
        .fetch_add(u64::try_from(trades.len()).unwrap_or(0), Relaxed);
    out.write(now, &s);
}

/// Итог по пропуску: строка `rg`, счётчики; `lost_ids` — сколько номеров осталось потерянными.
fn finish(st: &BackfillStats, out: &mut OutFile, job: &Job, status: &str, lost_ids: i64) {
    let now = wall_ms();
    let ids = job.req.ids();
    let lost = lost_ids.clamp(0, ids);
    let got = (ids - lost).max(0);
    st.ids_backfilled
        .fetch_add(u64::try_from(got).unwrap_or(0), Relaxed);
    if lost > 0 {
        st.lost(now, u64::try_from(lost).unwrap_or(0));
    }
    out.write(
        now,
        &format!(
            "rg\t{now}\t{}\t{}\t{}\t{}\t{got}\t{status}\t{}\t{}\n",
            job.req.symbol,
            job.req.lo,
            job.req.hi,
            job.req.found_ms,
            job.attempts + 1,
            job.req.shard
        ),
    );
}
