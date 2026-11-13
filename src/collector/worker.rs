//! Рабочий процесса: один поток на все шарды — раз в такт забирает накопленное из очередей шардов и прогоняет через писателей.
//!
//! Зачем (collector-perf, замер 13.11): по писателю на шард, просыпающемуся на каждый кадр, давало ≈ 1,07 `futex` и ≈ 0,6 переключения контекста
//! на кадр, а на PTI/VERW-госте каждый вход в ядро дорог. Теперь потоки приёма ничего не будят: кладут кадр в очередь шарда под коротким замком,
//! рабочий просыпается по таймеру (`tick`, 1 мс по умолчанию — `--worker-tick-us`), забирает очереди всех шардов и обрабатывает пачкой.
//! Порядок и содержимое записей прежние (тот же `ShardWriter`, тот же порядок в очереди шарда); меняется только то, кто и когда его запускает.

use std::os::fd::{AsRawFd, OwnedFd};
use std::sync::mpsc::{channel, sync_channel, Receiver, Sender, SyncSender, TryRecvError};
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use super::ring::Item;
use super::writer::ShardWriter;

pub(crate) struct Reg {
    writer: Box<ShardWriter>,
    done: SyncSender<()>,
}

/// Ручка рабочего: регистрация писателей шардов и остановка.
#[derive(Clone)]
pub struct WorkerHandle {
    tx: Sender<Msg>,
    /// Рабочий живёт внутри реактора: после команды реактор будится (его ожидание в `epoll_wait` может быть долгим).
    wake: Option<Arc<OwnedFd>>,
}

pub(crate) enum Msg {
    Add(Reg),
    Quit,
}

impl std::fmt::Debug for WorkerHandle {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("WorkerHandle")
    }
}

impl WorkerHandle {
    /// Канал команд рабочего и его ручка; `wake` — eventfd реактора (рабочий внутри реактора) или `None` (свой поток).
    pub(crate) fn channel(wake: Option<Arc<OwnedFd>>) -> (Self, Receiver<Msg>) {
        let (tx, rx) = channel();
        (Self { tx, wake }, rx)
    }

    fn poke(&self) {
        if let Some(w) = &self.wake {
            let one = 1u64.to_ne_bytes();
            // SAFETY: fd живой (держит `Arc<OwnedFd>`), буфер — 8 байт; запись в неблокирующий eventfd, ошибка безвредна.
            unsafe {
                libc::write(w.as_raw_fd(), one.as_ptr().cast(), 8);
            }
        }
    }

    /// Передать писателя шарда рабочему; сигнал на `Receiver` приходит, когда писатель закончил (файл закрыт, диск остановлен).
    pub fn add(&self, writer: ShardWriter) -> Receiver<()> {
        let (done, rx) = sync_channel(1);
        let _ = self.tx.send(Msg::Add(Reg {
            writer: Box::new(writer),
            done,
        }));
        self.poke();
        rx
    }

    /// Остановка рабочего (после того как все писатели закончили).
    pub fn quit(&self) {
        let _ = self.tx.send(Msg::Quit);
        self.poke();
    }
}

/// Состояние рабочего: писатели шардов и буфер пачки; такт делает поток рабочего или реактор (рабочий внутри реактора).
pub(crate) struct WorkerCore {
    writers: Vec<Reg>,
    scratch: Vec<Item>,
    rx: Receiver<Msg>,
    quit: bool,
}

impl WorkerCore {
    pub(crate) fn new(rx: Receiver<Msg>) -> Self {
        Self {
            writers: Vec::new(),
            scratch: Vec::with_capacity(1024),
            rx,
            quit: false,
        }
    }

    /// Один такт: подхватить команды и прогнать писателей. Внешний `None` — рабочий закончил (команда остановки получена, писателей не осталось);
    /// внутренний — через сколько срок ближайшего блока (`None` — блоков нет).
    pub(crate) fn tick(&mut self) -> Option<Option<Duration>> {
        loop {
            match self.rx.try_recv() {
                Ok(Msg::Add(r)) => self.writers.push(r),
                Ok(Msg::Quit) => self.quit = true,
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => {
                    self.quit = true;
                    break;
                }
            }
        }
        if self.quit && self.writers.is_empty() {
            return None;
        }
        let mut earliest: Option<Duration> = None;
        let scratch = &mut self.scratch;
        self.writers.retain_mut(|r| {
            if r.writer.step(scratch) {
                let _ = r.done.try_send(());
                false
            } else {
                if let Some(d) = r.writer.flush_in() {
                    earliest = Some(earliest.map_or(d, |e| e.min(d)));
                }
                true
            }
        });
        Some(earliest)
    }

    pub(crate) fn is_idle(&self) -> bool {
        self.writers.is_empty()
    }
}

/// Поток рабочего. `tick` — период пробуждений.
pub fn spawn_worker(tick: Duration) -> (WorkerHandle, JoinHandle<()>) {
    let (handle, rx) = WorkerHandle::channel(None);
    let join = thread::Builder::new()
        .name("wr-proc".to_string())
        .stack_size(1 << 20)
        .spawn(move || run(WorkerCore::new(rx), tick))
        .unwrap_or_else(|_| thread::spawn(|| {}));
    (handle, join)
}

fn run(mut core: WorkerCore, tick: Duration) {
    let mut next = Instant::now();
    loop {
        let Some(flush_in) = core.tick() else {
            return;
        };
        // абсолютный график тактов: обработка пачки не сдвигает следующий такт; опоздали — следующий отсчитывается от «сейчас»
        next += flush_in.map_or(tick, |d| d.min(tick));
        let now = Instant::now();
        if next < now {
            next = now;
        }
        thread::sleep(next - now);
    }
}
