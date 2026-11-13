//! Счётчики и окна шарда: гистограммы лагов, глубины очередей, свежесть, списки событий.
//!
//! Делает: `Hist`/`Rolling` (перцентили по ВСЕМ записям окна, а не выборке: логарифмические корзины ≈ 3 %), `Depth`
//! (глубина очереди и сигнал переполнения), `ShardMetrics` (общее состояние шарда для потоков приёма, писателя, диска и
//! health). Не знает биржу и формат файлов; пишут в него потоки шарда, читает только health и строка статистики.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicI64, AtomicU64, Ordering::Relaxed};
use std::sync::{Arc, Mutex, MutexGuard};

use super::fresh::Fresh;

pub const WINDOW_S: i64 = 60;
pub const STATS_PERIOD_S: u64 = 10;
const SLOT_MS: i64 = 5_000;
const SLOTS: usize = 12;
const BUCKETS: usize = 1344;

/// Гистограмма неотрицательных целых (мкс): до 63 — точно, дальше 32 корзины на степень двойки (≤ 3 % ширины).
#[derive(Clone, Debug)]
pub struct Hist {
    c: Vec<u32>,
    n: u64,
}

impl Default for Hist {
    fn default() -> Self {
        Self::new()
    }
}

fn bucket(v: u64) -> usize {
    if v < 64 {
        return usize::try_from(v).unwrap_or(0);
    }
    let b = 63 - v.leading_zeros();
    let sub = (v >> (b - 5)) & 31;
    let idx = 64 + 32 * usize::try_from(b - 6).unwrap_or(0) + usize::try_from(sub).unwrap_or(0);
    idx.min(BUCKETS - 1)
}

/// Середина корзины.
fn bucket_mid(i: usize) -> u64 {
    if i < 64 {
        return u64::try_from(i).unwrap_or(0);
    }
    let g = (i - 64) / 32;
    let sub = u64::try_from((i - 64) % 32).unwrap_or(0);
    let shift = u32::try_from(g + 1).unwrap_or(1);
    ((32 + sub) << shift) + (1u64 << shift) / 2
}

impl Hist {
    pub fn new() -> Self {
        Self {
            c: vec![0; BUCKETS],
            n: 0,
        }
    }

    pub fn add(&mut self, v: u64) {
        if let Some(c) = self.c.get_mut(bucket(v)) {
            *c += 1;
            self.n += 1;
        }
    }

    pub fn count(&self) -> u64 {
        self.n
    }

    pub fn clear(&mut self) {
        self.c.iter_mut().for_each(|c| *c = 0);
        self.n = 0;
    }

    pub fn merge(&mut self, o: &Hist) {
        for (a, b) in self.c.iter_mut().zip(&o.c) {
            *a += b;
        }
        self.n += o.n;
    }

    /// Значение (мкс) на позиции `floor(n·p)` по возрастанию (как `percentile` Python-ядра); `None` — пусто.
    pub fn percentile(&self, p: f64) -> Option<u64> {
        if self.n == 0 {
            return None;
        }
        #[allow(
            clippy::cast_precision_loss,
            clippy::cast_possible_truncation,
            clippy::cast_sign_loss
        )]
        let rank = ((self.n as f64 * p) as u64).min(self.n - 1);
        let mut seen = 0u64;
        for (i, &c) in self.c.iter().enumerate() {
            seen += u64::from(c);
            if seen > rank {
                return Some(bucket_mid(i));
            }
        }
        None
    }
}

/// Скользящее окно `WINDOW_S` из пятисекундных гистограмм.
#[derive(Clone, Debug)]
pub struct Rolling {
    slots: Vec<Hist>,
    epoch: Vec<i64>,
}

impl Default for Rolling {
    fn default() -> Self {
        Self {
            slots: vec![Hist::new(); SLOTS],
            epoch: vec![i64::MIN; SLOTS],
        }
    }
}

impl Rolling {
    pub fn add(&mut self, now_ms: i64, v_us: u64) {
        let e = now_ms.div_euclid(SLOT_MS);
        let i = usize::try_from(e.rem_euclid(i64::try_from(SLOTS).unwrap_or(12))).unwrap_or(0);
        if let (Some(ep), Some(h)) = (self.epoch.get_mut(i), self.slots.get_mut(i)) {
            if *ep != e {
                *ep = e;
                h.clear();
            }
            h.add(v_us);
        }
    }

    pub fn snapshot(&self, now_ms: i64) -> Hist {
        let cur = now_ms.div_euclid(SLOT_MS);
        let mut out = Hist::new();
        for (ep, h) in self.epoch.iter().zip(&self.slots) {
            if *ep > cur - i64::try_from(SLOTS).unwrap_or(12) && *ep <= cur {
                out.merge(h);
            }
        }
        out
    }
}

/// Мкс → мс с десятыми для health.
#[allow(clippy::cast_precision_loss)]
pub fn us_to_ms(us: u64) -> f64 {
    (us as f64 / 100.0).round() / 10.0
}

/// Глубина очереди, переполнение и ожидание producer'а.
#[derive(Debug)]
pub struct Depth {
    pub cap: usize,
    pub cur: AtomicI64,
    /// Максимум глубины за окно статистики (сбрасывается чтением `take_max`).
    pub max: AtomicI64,
    /// Сколько раз producer нашёл очередь полной.
    pub full: AtomicU64,
    /// Сколько нс producer прождал освобождения места.
    pub blocked_ns: AtomicU64,
}

impl Depth {
    pub fn new(cap: usize) -> Arc<Self> {
        Arc::new(Self {
            cap,
            cur: AtomicI64::new(0),
            max: AtomicI64::new(0),
            full: AtomicU64::new(0),
            blocked_ns: AtomicU64::new(0),
        })
    }

    pub fn inc(&self) {
        let d = self.cur.fetch_add(1, Relaxed) + 1;
        self.max.fetch_max(d, Relaxed);
    }

    pub fn dec(&self) {
        self.cur.fetch_sub(1, Relaxed);
    }

    pub fn take_max(&self) -> i64 {
        self.max.swap(self.cur.load(Relaxed), Relaxed)
    }
}

/// События и пинги шарда (метки мс; чистятся по возрасту).
#[derive(Debug, Default)]
pub struct Lists {
    /// Все таймауты (ping без pong ≥ 10 с); из них «канал молчит» — `timeouts_silent`, остальные — pong не пришёл при живых данных.
    pub timeouts: VecDeque<i64>,
    /// Таймауты, при которых за те же 10 с не пришло и данных (канал молчит в обе стороны).
    pub timeouts_silent: VecDeque<i64>,
    pub gaps: VecDeque<i64>,
    /// Завершённые паузы данных шарда: (начало, мс); длительность — от обнаружения до первого кадра после (шаг надзора `GAP_TICK`).
    pub gap_spans: VecDeque<(i64, i64)>,
    pub reconnects: VecDeque<i64>,
    pub path_switches: VecDeque<i64>,
    pub entrance_switches: VecDeque<i64>,
    /// (мс, rtt мс)
    pub pings: VecDeque<(i64, f64)>,
}

const DAY_MS: i64 = 24 * 3600 * 1000;

impl Lists {
    pub fn trim(&mut self, now_ms: i64) {
        let cut = now_ms - DAY_MS;
        for dq in [
            &mut self.timeouts,
            &mut self.timeouts_silent,
            &mut self.gaps,
            &mut self.reconnects,
            &mut self.path_switches,
            &mut self.entrance_switches,
        ] {
            while dq.front().is_some_and(|&t| t < cut) {
                dq.pop_front();
            }
        }
        while self.gap_spans.front().is_some_and(|&(t, _)| t < cut) {
            self.gap_spans.pop_front();
        }
        while self
            .pings
            .front()
            .is_some_and(|&(t, _)| now_ms - t > WINDOW_S * 1000)
        {
            self.pings.pop_front();
        }
    }
}

/// Состояние писателя шарда: меняется потоком писателя пачками, читается health.
#[derive(Debug, Default)]
pub struct WriterStats {
    pub fresh: Fresh,
    /// лаг прихода записей после снятия дублей, по видам book/trade/mark (settled)
    pub lag_in: [Rolling; 3],
    /// сколько кадр прождал в очереди до писателя
    pub queue_lag: Rolling,
    /// символ (как в кадре) → (имя в нижнем регистре, мс последнего кадра)
    pub seen: std::collections::HashMap<Box<[u8]>, (String, i64)>,
    pub records: u64,
    pub dups: u64,
    pub raw: u64,
    pub raw_copies: u64,
    pub clamped: u64,
    pub rows: u64,
}

/// Состояние диска шарда.
#[derive(Debug, Default)]
pub struct SinkStats {
    /// rx → запись на диск (мкс)
    pub lag_out: Rolling,
    pub bytes_out: u64,
    pub flushes: u64,
    /// самая долгая запись блока на диск за окно статистики, мкс (сбрасывается чтением)
    pub write_max_us: u64,
    pub errors: u64,
    /// блоков, от которых отказались после `BLOCK_RETRIES` неудачных записей (данные потеряны)
    pub blocks_dropped: u64,
    pub last_write_ms: i64,
    pub files_closed: u64,
}

/// Итог последнего завершённого окна статистики (`STATS_PERIOD_S`): максимумы и приращения; читают строка статистики и health.
#[derive(Clone, Debug, Default)]
pub struct WindowSnap {
    pub ts_ms: i64,
    pub frames: u64,
    pub bytes: u64,
    pub max_gap_ms: u64,
    pub ring_depth_max: i64,
    pub ring_full: u64,
    pub ring_blocked_ms: u64,
    pub recvq_max: u64,
    pub recvq_samples: u64,
    pub recvq_behind: u64,
    pub busy_max_us: u64,
    pub handoff_max_us: u64,
    pub sink_depth_max: i64,
    pub write_max_us: u64,
}

/// Значения счётчиков на конец прошлого окна (для приращений).
#[derive(Debug, Default)]
struct Prev {
    frames: u64,
    bytes: u64,
    ring_full: u64,
    ring_blocked_ns: u64,
    recvq_samples: u64,
    recvq_behind: u64,
}

/// Общее состояние шарда: пишут потоки приёма (атомики), писателя, диска; читает health.
#[derive(Debug)]
pub struct ShardMetrics {
    pub name: String,
    pub started_ms: i64,
    pub frames: AtomicU64,
    pub bytes: AtomicU64,
    pub last_frame_ms: AtomicI64,
    pub last_frame_mono_ms: AtomicU64,
    pub connected: AtomicI64,
    /// Recv-Q ядра (FIONREAD) в моменты чтения: максимум за окно статистики, байт
    pub recvq_max: AtomicU64,
    pub recvq_samples: AtomicU64,
    /// сколько замеров показали непустую очередь сокета (читатель отстаёт от прихода)
    pub recvq_behind: AtomicU64,
    /// чтение кадра → кадр в очереди писателя: максимум за окно, мкс
    pub handoff_max_us: AtomicU64,
    /// самый долгий промежуток между возвратами из чтения (занятость читателя), мкс
    pub busy_max_us: AtomicU64,
    pub ring: Arc<Depth>,
    pub sink_q: Arc<Depth>,
    pub lists: Mutex<Lists>,
    pub writer: Mutex<WriterStats>,
    pub sink: Mutex<SinkStats>,
    pub window: Mutex<WindowSnap>,
    prev: Mutex<Prev>,
    /// Номера потоков ядра, работающих на шард (CPU шарда в строке статистики).
    tids: Mutex<Vec<u32>>,
}

impl ShardMetrics {
    pub fn new(name: &str, started_ms: i64, ring_cap: usize, sink_cap: usize) -> Arc<Self> {
        Arc::new(Self {
            name: name.to_string(),
            started_ms,
            frames: AtomicU64::new(0),
            bytes: AtomicU64::new(0),
            last_frame_ms: AtomicI64::new(0),
            last_frame_mono_ms: AtomicU64::new(0),
            connected: AtomicI64::new(0),
            recvq_max: AtomicU64::new(0),
            recvq_samples: AtomicU64::new(0),
            recvq_behind: AtomicU64::new(0),
            handoff_max_us: AtomicU64::new(0),
            busy_max_us: AtomicU64::new(0),
            ring: Depth::new(ring_cap),
            sink_q: Depth::new(sink_cap),
            lists: Mutex::new(Lists::default()),
            writer: Mutex::new(WriterStats::default()),
            sink: Mutex::new(SinkStats::default()),
            window: Mutex::new(WindowSnap::default()),
            prev: Mutex::new(Prev::default()),
            tids: Mutex::new(Vec::new()),
        })
    }

    /// Поток шарда о себе: его CPU идёт в `cpu_seconds`.
    pub fn register_thread(&self) {
        self.tids
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .push(super::net::gettid());
    }

    /// Суммарный CPU потоков шарда, секунды (ушедшие потоки учитывать нечем — их `cpu_seconds` уже не видит).
    pub fn cpu_seconds(&self) -> f64 {
        let mut t = self
            .tids
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        t.retain(|&id| super::net::thread_cpu_s(id).is_some());
        t.iter()
            .filter_map(|&id| super::net::thread_cpu_s(id))
            .sum()
    }

    /// Конец окна статистики: максимумы сбрасываются, приращения считаются от прошлого окна; итог доступен в `window`.
    /// Зовёт один поток (надзор шарда).
    pub fn take_window(&self, now_ms: i64, max_gap_ms: u64) -> WindowSnap {
        let mut p = self
            .prev
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let (frames, bytes) = (self.frames.load(Relaxed), self.bytes.load(Relaxed));
        let (full, blocked) = (
            self.ring.full.load(Relaxed),
            self.ring.blocked_ns.load(Relaxed),
        );
        let (qs, qb) = (
            self.recvq_samples.load(Relaxed),
            self.recvq_behind.load(Relaxed),
        );
        let write_max_us = std::mem::take(&mut self.sink().write_max_us);
        let w = WindowSnap {
            ts_ms: now_ms,
            frames: frames - p.frames,
            bytes: bytes - p.bytes,
            max_gap_ms,
            ring_depth_max: self.ring.take_max(),
            ring_full: full - p.ring_full,
            ring_blocked_ms: (blocked - p.ring_blocked_ns) / 1_000_000,
            recvq_max: self.recvq_max.swap(0, Relaxed),
            recvq_samples: qs - p.recvq_samples,
            recvq_behind: qb - p.recvq_behind,
            busy_max_us: self.busy_max_us.swap(0, Relaxed),
            handoff_max_us: self.handoff_max_us.swap(0, Relaxed),
            sink_depth_max: self.sink_q.take_max(),
            write_max_us,
        };
        *p = Prev {
            frames,
            bytes,
            ring_full: full,
            ring_blocked_ns: blocked,
            recvq_samples: qs,
            recvq_behind: qb,
        };
        *self
            .window
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = w.clone();
        w
    }

    pub fn lists(&self) -> MutexGuard<'_, Lists> {
        self.lists
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    pub fn writer(&self) -> MutexGuard<'_, WriterStats> {
        self.writer
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    pub fn sink(&self) -> MutexGuard<'_, SinkStats> {
        self.sink
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }
}
