//! Очередь от потоков приёма к писателю шарда: ограниченная, с глубиной и сигналом переполнения.
//!
//! Потоки приёма кладут кадры и служебные записи; писатель шарда — единственный потребитель. Данные не теряются:
//! при полной очереди приём ждёт места (это обратное давление), и каждое ожидание считается (`Depth.full`, `blocked_ns`).
//! Размер очереди — секунды запаса при пиковой нагрузке; по `ring_depth_max` в health видно, что запас не тронут.

use std::sync::atomic::Ordering::Relaxed;
use std::sync::mpsc::{
    sync_channel, Receiver, RecvTimeoutError, SyncSender, TryRecvError, TrySendError,
};
use std::sync::Arc;
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

#[derive(Clone)]
pub struct RingTx {
    tx: SyncSender<Item>,
    depth: Arc<Depth>,
}

pub struct RingRx {
    rx: Receiver<Item>,
    depth: Arc<Depth>,
}

pub fn ring(depth: &Arc<Depth>) -> (RingTx, RingRx) {
    let (tx, rx) = sync_channel(depth.cap);
    (
        RingTx {
            tx,
            depth: Arc::clone(depth),
        },
        RingRx {
            rx,
            depth: Arc::clone(depth),
        },
    )
}

impl RingTx {
    /// Положить; при полной очереди — ждать места (и посчитать). `Err` — писатель ушёл.
    pub fn push(&self, item: Item) -> Result<(), Closed> {
        self.depth.inc();
        match self.tx.try_send(item) {
            Ok(()) => Ok(()),
            Err(TrySendError::Full(item)) => {
                self.depth.full.fetch_add(1, Relaxed);
                let t0 = Instant::now();
                let r = self.tx.send(item);
                self.depth.blocked_ns.fetch_add(
                    u64::try_from(t0.elapsed().as_nanos()).unwrap_or(u64::MAX),
                    Relaxed,
                );
                if r.is_err() {
                    self.depth.dec();
                }
                r.map_err(|_| Closed)
            }
            Err(TrySendError::Disconnected(_)) => {
                self.depth.dec();
                Err(Closed)
            }
        }
    }
}

impl RingRx {
    pub fn recv_timeout(&self, d: Duration) -> Result<Item, RecvTimeoutError> {
        let r = self.rx.recv_timeout(d);
        if r.is_ok() {
            self.depth.dec();
        }
        r
    }

    pub fn try_recv(&self) -> Result<Item, TryRecvError> {
        let r = self.rx.try_recv();
        if r.is_ok() {
            self.depth.dec();
        }
        r
    }
}
