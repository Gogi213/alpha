//! Очередь от потоков приёма к писателю шарда: ограниченная, пачками, с глубиной и сигналом переполнения.
//!
//! Потоки приёма кладут кадры и служебные записи под коротким замком (две-три линии на шард — столкновения редки); писатель забирает
//! накопленное целиком раз в такт (`take`), никто никого не будит на каждый кадр (замер collector-perf 13.11: передача кадра
//! через `sync_channel` стоила ≈ 1,07 `futex` и ≈ 0,6 переключения контекста на кадр). Данные не теряются: при полной очереди приём
//! ждёт места (обратное давление), и каждое ожидание считается (`Depth.full`, `blocked_ns`).
//! Размер очереди — секунды запаса при пиковой нагрузке; по `ring_depth_max` в health видно, что запас не тронут.

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering::Relaxed};
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use bytes::Bytes;

use super::metrics::Depth;

pub enum Payload {
    /// Кадр биржи как пришёл.
    Frame(Bytes),
    /// Служебная запись (`connected` / `subscribed` / `closed`).
    Control { what: &'static str, detail: String },
    /// Пинг WS: время до pong, мкс.
    Ping(i64),
    /// Конец: все приёмники закончили, писатель дочитывает очередь и закрывает файл.
    Shutdown,
}

pub struct Item {
    /// Приём, нс UTC (сразу после возврата из чтения).
    pub rx_ns: i64,
    /// Номер сессии-источника (снятие копий сырых кадров между линиями).
    pub sender: u32,
    /// Кадр пришёл не в первые секунды после подписки: идёт в свежесть.
    pub settled: bool,
    pub payload: Payload,
}

/// Писатель шарда ушёл: класть некуда.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Closed;

struct Inner {
    q: Mutex<Vec<Item>>,
    space: Condvar,
    /// Писатель ушёл (`RingRx` снят).
    closed: AtomicBool,
    /// Живых `RingTx`: ноль и пустая очередь — писателю больше ждать нечего.
    senders: AtomicUsize,
}

pub struct RingTx {
    inner: Arc<Inner>,
    depth: Arc<Depth>,
}

pub struct RingRx {
    inner: Arc<Inner>,
    depth: Arc<Depth>,
}

pub fn ring(depth: &Arc<Depth>) -> (RingTx, RingRx) {
    let inner = Arc::new(Inner {
        q: Mutex::new(Vec::with_capacity(1024)),
        space: Condvar::new(),
        closed: AtomicBool::new(false),
        senders: AtomicUsize::new(1),
    });
    (
        RingTx {
            inner: Arc::clone(&inner),
            depth: Arc::clone(depth),
        },
        RingRx {
            inner,
            depth: Arc::clone(depth),
        },
    )
}

impl Clone for RingTx {
    fn clone(&self) -> Self {
        self.inner.senders.fetch_add(1, Relaxed);
        Self {
            inner: Arc::clone(&self.inner),
            depth: Arc::clone(&self.depth),
        }
    }
}

impl Drop for RingTx {
    fn drop(&mut self) {
        self.inner.senders.fetch_sub(1, Relaxed);
    }
}

impl Drop for RingRx {
    fn drop(&mut self) {
        self.inner.closed.store(true, Relaxed);
        self.inner.space.notify_all();
    }
}

fn lock(m: &Mutex<Vec<Item>>) -> MutexGuard<'_, Vec<Item>> {
    m.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

impl RingTx {
    /// Положить; при полной очереди — ждать места (и посчитать). `Err` — писатель ушёл.
    pub fn push(&self, item: Item) -> Result<(), Closed> {
        let mut q = lock(&self.inner.q);
        if q.len() >= self.depth.cap {
            self.depth.full.fetch_add(1, Relaxed);
            let t0 = Instant::now();
            while q.len() >= self.depth.cap {
                if self.inner.closed.load(Relaxed) {
                    return Err(Closed);
                }
                q = self
                    .inner
                    .space
                    .wait_timeout(q, Duration::from_millis(100))
                    .unwrap_or_else(std::sync::PoisonError::into_inner)
                    .0;
            }
            self.depth.blocked_ns.fetch_add(
                u64::try_from(t0.elapsed().as_nanos()).unwrap_or(u64::MAX),
                Relaxed,
            );
        }
        if self.inner.closed.load(Relaxed) {
            return Err(Closed);
        }
        q.push(item);
        drop(q);
        self.depth.inc();
        Ok(())
    }
}

impl RingRx {
    /// Забрать накопленное целиком в `out` (он должен быть пуст; его ёмкость переиспользуется). `false` — все отправители ушли и очередь пуста.
    pub fn take(&self, out: &mut Vec<Item>) -> bool {
        let mut q = lock(&self.inner.q);
        if q.is_empty() {
            return self.inner.senders.load(Relaxed) > 0;
        }
        std::mem::swap(&mut *q, out);
        drop(q);
        self.depth.dec_n(out.len());
        self.inner.space.notify_all();
        true
    }
}
