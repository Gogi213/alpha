//! Дозапрос сделок, потерянных при сбросе соединений (G-022, `docs/findings/health-red-review-2026-11-16.md` A.6 / п. 4).
//!
//! Binance 15.11 14:15: биржа сама оборвала оба соединения шарда `trade02`, замены подписались заново и получили только новые сделки — хвост из 8 081 сделки в разорванных
//! соединениях не пришёл. Номер сделки у Binance, OKX, BingX и Gate сплошной по символу, поэтому потеря видна по номерам, а не по паузам:
//! - `IdTracker` (в писателе шарда): помнит старший номер символа; номер выше следующего открывает «возможный пропуск»; пришедший позже (другая линия, замена) его закрывает;
//!   пропуск, не закрытый за `CONFIRM_MS`, — подтверждённый, запрос уходит в поток дозапроса; у пропуска — время сделки до него и после (запрос по времени у Gate) и время приёма сделки до него (минута потери в ряду);
//! - поток дозапроса (`spawn`): забирает недостающие номера из REST страницами (не в событийном цикле, не чаще раза в `MIN_CALL_GAP`) и пишет их в **отдельный** файл
//!   `<корень>/<день>/backfill-<биржа>-<запуск>.tsv.zst` — в файлы ws они не попадают никогда;
//! - `BackfillStats`: счётчики для health; потеря = подтверждённые номера, которые не удалось получить; номера, которых у биржи нет в странице REST, покрывающей их с обеих сторон, — «нет на бирже», не потеря;
//! - отказ по частоте (HTTP 429/418, OKX `50011`) ставит на паузу весь поток дозапроса, не одну задачу.
//!
//! Строки файла дозапроса (поля через табуляцию, значения биржи строками как пришли):
//! `rh <мс> <биржа> <запуск> <версия> <пометка>` — заголовок файла;
//! `rt <мс получения> <символ> <номер> <время сделки, мс> <цена> <размер> <f> <l> <m 0/1> <пропуск от> <пропуск до>` — сделка из REST;
//! `rg <мс> <символ> <от> <до> <найден, мс> <получено номеров> <статус> <попыток> <шард> <нет на бирже номеров> <время пропуска, мс>` — итог по пропуску (`ok`, `absent` — часть номеров у биржи отсутствует,
//! `partial`, `failed`, `too_large`, `unsupported`); «время пропуска» — приём последней сделки перед пропуском, в его минуте пишется потеря в поминутный ряд.

use std::collections::{HashMap, VecDeque};
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicI64, AtomicU64, Ordering::Relaxed};
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use super::exchange::{Exchange, PageReq, RestError, RestTrade, TradePage, TradeSeq};
use super::writer::day_string;

/// Пропуск номеров подтверждается, если за это время номера не пришли (другая линия и замена при перекрытии приносят их за секунды); назначаемое (Судье). Перестановки линий по записанным данным закрываются
/// за: Binance макс. 0,26 с, BingX 0,56 с, Gate 0,28 с, OKX 0,03 с (> 5 с — 0 случаев; проверка 16.11); у BingX запрос видит только последние 1 000 сделок — чем короче окно, тем меньше теряется.
pub const CONFIRM_MS: i64 = 10_000;
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

/// Источник страниц дозапроса: запрос → страница (`Exchange::trade_page`); в проверках подменяется.
pub type FetchFn<'a> = &'a dyn Fn(&PageReq<'_>) -> Option<Result<TradePage, RestError>>;

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

/// Подтверждённый пропуск: номера `lo..=hi` символа не пришли.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct GapReq {
    pub shard: String,
    pub symbol: String,
    pub lo: i64,
    pub hi: i64,
    /// Когда пропуск замечен — пришёл номер выше пропущенных (мс UTC, приём).
    pub found_ms: i64,
    /// Приём последней сделки символа перед пропуском (мс UTC): в минуте этого времени потеря попадает в поминутный ряд — рядом с паузой, а не с итогом дозапроса.
    pub gap_ms: i64,
    /// Время сделки биржи перед пропуском и после него, мс: границы запроса по времени (Gate).
    pub t_before_ms: i64,
    pub t_after_ms: i64,
}

impl GapReq {
    pub fn ids(&self) -> i64 {
        self.hi - self.lo + 1
    }
}

#[derive(Clone, Copy, Debug)]
struct Range {
    lo: i64,
    hi: i64,
    since_ms: i64,
    gap_ms: i64,
    t_before_ms: i64,
    t_after_ms: i64,
}

#[derive(Debug)]
struct SymIds {
    sym: Box<[u8]>,
    hi: i64,
    /// Приём и время биржи сделки с номером `hi`.
    last_rx_ms: i64,
    last_t_ms: i64,
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

    /// Запись покрывает номера `lo..=hi` символа `sid`, приём `rx_ms`, время сделки биржи `t_ms`.
    pub fn on_span(&mut self, sid: u32, sym: &[u8], lo: i64, hi: i64, rx_ms: i64, t_ms: i64) {
        let s = self.syms.entry(sid).or_insert_with(|| SymIds {
            sym: sym.into(),
            hi: i64::MIN,
            last_rx_ms: rx_ms,
            last_t_ms: t_ms,
            pending: Vec::new(),
        });
        if s.hi == i64::MIN {
            s.hi = hi;
            s.last_rx_ms = rx_ms;
            s.last_t_ms = t_ms;
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
                    gap_ms: s.last_rx_ms,
                    t_before_ms: s.last_t_ms,
                    t_after_ms: t_ms,
                });
                self.pending += 1;
            } else {
                self.overflow += 1;
            }
        }
        if hi > s.hi {
            s.hi = hi;
            s.last_rx_ms = rx_ms;
            s.last_t_ms = t_ms;
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
                        gap_ms: r.gap_ms,
                        t_before_ms: r.t_before_ms,
                        t_after_ms: r.t_after_ms,
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
        // остатки пропуска несут время и метки целого: запрос по времени шире нужного, лишние номера отсекаются по диапазону
        if lo > r.lo {
            out.push(Range { hi: lo - 1, ..r });
        }
        if hi < r.hi {
            out.push(Range { lo: hi + 1, ..r });
        }
    }
    *pending = out;
    hit
}

/// Счётчики дозапроса для health и страницы: подтверждённые номера — получены, отсутствуют у биржи или потеряны.
#[derive(Debug, Default)]
pub struct BackfillStats {
    pub gaps_confirmed: AtomicU64,
    pub ids_confirmed: AtomicU64,
    pub ids_backfilled: AtomicU64,
    /// Номера, которых нет в странице REST, покрывающей их с обеих сторон: у биржи их нет (Gate нумерует не подряд) — не потеря.
    pub ids_absent: AtomicU64,
    /// Окончательная потеря: не удалось получить (сбои REST, слишком крупный пропуск, старше окна ответа, у биржи нет запроса).
    pub ids_lost: AtomicU64,
    pub rest_requests: AtomicU64,
    pub rest_errors: AtomicU64,
    pub trades_written: AtomicU64,
    /// Отказы по частоте (HTTP 429/418, OKX `50011`): каждый ставит на паузу весь поток; пауза до (мс UTC, 0 — нет) и суммарная, с.
    pub rate_limited: AtomicU64,
    pause_until_ms: AtomicI64,
    pub paused_s_total: AtomicU64,
    last_error: Mutex<Option<String>>,
    /// Окончательные потери по времени пропуска: (мс приёма последней сделки перед пропуском, номеров) — поминутный ряд.
    lost_log: Mutex<VecDeque<(i64, u64)>>,
}

impl BackfillStats {
    fn error(&self, e: &str) {
        self.rest_errors.fetch_add(1, Relaxed);
        *self
            .last_error
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) =
            Some(e.chars().take(200).collect());
    }

    /// Потеряно `ids` номеров; `gap_ms` — время пропуска (не время итога дозапроса: итог приходит после подтверждения, очереди и повторов).
    pub fn lost(&self, gap_ms: i64, ids: u64) {
        self.ids_lost.fetch_add(ids, Relaxed);
        let mut l = self
            .lost_log
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        l.push_back((gap_ms, ids));
        while l.len() > 100_000 {
            l.pop_front();
        }
    }

    /// Отказ по частоте: весь поток стоит `d` с `now_ms`.
    fn limited(&self, now_ms: i64, d: Duration) {
        self.rate_limited.fetch_add(1, Relaxed);
        self.paused_s_total.fetch_add(d.as_secs(), Relaxed);
        self.pause_until_ms.store(
            now_ms + i64::try_from(d.as_millis()).unwrap_or(i64::MAX / 4),
            Relaxed,
        );
    }

    /// Сколько секунд поток ещё стоит по отказу по частоте (0 — идёт).
    pub fn paused_s(&self, now_ms: i64) -> u64 {
        u64::try_from((self.pause_until_ms.load(Relaxed) - now_ms).max(0))
            .unwrap_or(0)
            .div_ceil(1000)
    }

    /// Номеров, окончательно потерянных с пропуском в `[from_ms, to_ms)` — строка минуты ряда.
    pub fn lost_between(&self, from_ms: i64, to_ms: i64) -> u64 {
        self.lost_log
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .iter()
            .filter(|(t, _)| (from_ms..to_ms).contains(t))
            .map(|(_, n)| *n)
            .sum()
    }

    /// Раздел `backfill` health. `open_ids` — подтверждены, ещё не получены, не отсутствуют и не потеряны (в очереди или в повторе).
    pub fn json(&self, enabled: bool, now_ms: i64) -> Value {
        let (c, b, a, l) = (
            self.ids_confirmed.load(Relaxed),
            self.ids_backfilled.load(Relaxed),
            self.ids_absent.load(Relaxed),
            self.ids_lost.load(Relaxed),
        );
        let lost_1h: u64 = self.lost_between(now_ms - 3_600_000, now_ms + 1);
        json!({
            "enabled": enabled, "gaps_confirmed": self.gaps_confirmed.load(Relaxed), "ids_confirmed": c, "ids_backfilled": b, "ids_absent": a, "ids_lost": l,
            "ids_lost_1h": lost_1h, "open_ids": c.saturating_sub(b + a + l), "rest_requests": self.rest_requests.load(Relaxed),
            "rest_errors": self.rest_errors.load(Relaxed), "trades_written": self.trades_written.load(Relaxed),
            "rate_limited": self.rate_limited.load(Relaxed), "paused_s": self.paused_s(now_ms), "paused_s_total": self.paused_s_total.load(Relaxed),
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
    /// Ещё не запрошенная часть диапазона, сколько номеров уже получено, номера, о которых страницы ответили целиком (`TradePage::covered`), и границы времени следующего запроса.
    rest: Option<(i64, i64)>,
    got: i64,
    cover: Option<(i64, i64)>,
    t_from_ms: i64,
    t_to_ms: i64,
}

impl Job {
    fn new(req: GapReq) -> Self {
        Self {
            rest: Some((req.lo, req.hi)),
            attempts: 0,
            not_before: Instant::now(),
            got: 0,
            cover: None,
            t_from_ms: req.t_before_ms,
            t_to_ms: req.t_after_ms,
            req,
        }
    }

    /// (получено, нет у биржи, потеряно) по номерам пропуска: нет у биржи — номера в покрытой страницами части, которых в них нет; остальное — потеря.
    fn tally(&self) -> (i64, i64, i64) {
        let ids = self.req.ids();
        let got = self.got.clamp(0, ids);
        let covered = self.cover.map_or(0, |(a, b)| {
            (b.min(self.req.hi) - a.max(self.req.lo) + 1).max(0)
        });
        let absent = (covered - got).clamp(0, ids - got);
        (got, absent, ids - got - absent)
    }
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
                .open(dir.join(format!(
                    "backfill-{}-{}.tsv.zst",
                    self.exchange, self.run_id
                )))
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
            let fetch = |q: &PageReq<'_>| exch.trade_page(q);
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
    // отказ по частоте: весь поток — не одна задача — стоит до этого момента (запросы не идут, новые пропуски копятся в очереди)
    let mut paused_until: Option<Instant> = None;
    while !stop.load(Relaxed) {
        match rx.recv_timeout(Duration::from_millis(200)) {
            Ok(req) => queue.push_back(Job::new(req)),
            Err(RecvTimeoutError::Timeout) => {}
            Err(RecvTimeoutError::Disconnected) => {
                if queue.is_empty() {
                    return;
                }
                std::thread::sleep(Duration::from_millis(200));
            }
        }
        while let Ok(req) = rx.try_recv() {
            queue.push_back(Job::new(req));
        }
        let now = Instant::now();
        if let Some(p) = paused_until {
            if now < p {
                continue;
            }
            paused_until = None;
        }
        let Some(pos) = queue.iter().position(|j| j.not_before <= now) else {
            continue;
        };
        let Some(mut job) = queue.remove(pos) else {
            continue;
        };
        let ids = job.req.ids();
        if ids > MAX_GAP_IDS {
            finish(st, out, &job, Some("too_large"));
            continue;
        }
        let Some((lo, hi)) = job.rest else {
            finish(st, out, &job, None);
            continue;
        };
        if now.duration_since(last_call) < cfg.min_call_gap {
            std::thread::sleep(
                cfg.min_call_gap
                    .saturating_sub(now.duration_since(last_call)),
            );
        }
        last_call = Instant::now();
        st.rest_requests.fetch_add(1, Relaxed);
        let q = PageReq {
            symbol: &job.req.symbol,
            lo,
            hi,
            t_from_ms: job.t_from_ms,
            t_to_ms: job.t_to_ms,
        };
        match fetch(&q) {
            None => finish(st, out, &job, Some("unsupported")),
            Some(Ok(page)) => {
                job.attempts = 0;
                job.got += i64::try_from(page.trades.len()).unwrap_or(0);
                if let Some((a, b)) = page.covered {
                    job.cover = Some(job.cover.map_or((a, b), |(x, y)| (x.min(a), y.max(b))));
                }
                if let Some(t) = page.next_to_ms {
                    job.t_to_ms = t;
                }
                write_trades(st, out, &job.req, &page.trades);
                job.rest = page.rest.filter(|(a, b)| a <= b);
                if job.rest.is_none() {
                    finish(st, out, &job, None);
                } else {
                    job.not_before = Instant::now();
                    queue.push_front(job);
                }
            }
            Some(Err(e)) => {
                st.error(&e.msg);
                if let Some(d) = e.limited {
                    // 429/418 (OKX 50011): ни этот запрос, ни другие не идут, пока не пройдёт срок; попытка задачи не тратится
                    st.limited(wall_ms(), d);
                    paused_until = Some(Instant::now() + d);
                    job.not_before = Instant::now() + d;
                    queue.push_front(job);
                } else if let Some(d) = cfg.retry.get(job.attempts) {
                    job.attempts += 1;
                    job.not_before = Instant::now() + *d;
                    queue.push_back(job);
                } else {
                    finish(st, out, &job, Some("failed"));
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

/// Итог по пропуску: строка `rg`, счётчики. `status` — принудительный (`too_large`, `unsupported`, `failed`); иначе `ok` / `absent` (часть номеров у биржи отсутствует) / `partial` (часть потеряна).
fn finish(st: &BackfillStats, out: &mut OutFile, job: &Job, status: Option<&str>) {
    let now = wall_ms();
    let (got, absent, lost) = job.tally();
    st.ids_backfilled
        .fetch_add(u64::try_from(got).unwrap_or(0), Relaxed);
    st.ids_absent
        .fetch_add(u64::try_from(absent).unwrap_or(0), Relaxed);
    if lost > 0 {
        st.lost(job.req.gap_ms, u64::try_from(lost).unwrap_or(0));
    }
    let status = status.unwrap_or(if lost > 0 {
        "partial"
    } else if absent > 0 {
        "absent"
    } else {
        "ok"
    });
    out.write(
        now,
        &format!(
            "rg\t{now}\t{}\t{}\t{}\t{}\t{got}\t{status}\t{}\t{}\t{absent}\t{}\n",
            job.req.symbol,
            job.req.lo,
            job.req.hi,
            job.req.found_ms,
            job.attempts + 1,
            job.req.shard,
            job.req.gap_ms
        ),
    );
}
