//! Рабочий процесса: один поток на все шарды — раз в такт забирает накопленное из очередей шардов и прогоняет через писателей.
//!
//! Зачем (collector-perf, замер 13.11): по писателю на шард, просыпающемуся на каждый кадр, давало ≈ 1,07 `futex` и ≈ 0,6 переключения контекста
//! на кадр, а на PTI/VERW-госте каждый вход в ядро дорог. Теперь потоки приёма ничего не будят: кладут кадр в очередь шарда под коротким замком,
//! рабочий просыпается по таймеру (`tick`, 1 мс по умолчанию — `--worker-tick-us`), забирает очереди всех шардов и обрабатывает пачкой.
//! Порядок и содержимое записей прежние (тот же `ShardWriter`, тот же порядок в очереди шарда); меняется только то, кто и когда его запускает.

use std::sync::mpsc::{channel, sync_channel, Receiver, Sender, SyncSender, TryRecvError};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use super::ring::Item;
use super::writer::ShardWriter;

struct Reg {
    writer: Box<ShardWriter>,
    done: SyncSender<()>,
}

/// Ручка рабочего: регистрация писателей шардов и остановка.
#[derive(Clone)]
pub struct WorkerHandle {
    tx: Sender<Msg>,
}

enum Msg {
    Add(Reg),
    Quit,
}

impl std::fmt::Debug for WorkerHandle {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("WorkerHandle")
    }
}

impl WorkerHandle {
    /// Передать писателя шарда рабочему; сигнал на `Receiver` приходит, когда писатель закончил (файл закрыт, диск остановлен).
    pub fn add(&self, writer: ShardWriter) -> Receiver<()> {
        let (done, rx) = sync_channel(1);
        let _ = self.tx.send(Msg::Add(Reg {
            writer: Box::new(writer),
            done,
        }));
        rx
    }

    /// Остановка рабочего (после того как все писатели закончили).
    pub fn quit(&self) {
        let _ = self.tx.send(Msg::Quit);
    }
}

/// Поток рабочего. `tick` — период пробуждений.
pub fn spawn_worker(tick: Duration) -> (WorkerHandle, JoinHandle<()>) {
    let (tx, rx) = channel();
    let join = thread::Builder::new()
        .name("wr-proc".to_string())
        .stack_size(1 << 20)
        .spawn(move || run(&rx, tick))
        .unwrap_or_else(|_| thread::spawn(|| {}));
    (WorkerHandle { tx }, join)
}

fn run(rx: &Receiver<Msg>, tick: Duration) {
    let mut writers: Vec<Reg> = Vec::new();
    let mut scratch: Vec<Item> = Vec::with_capacity(1024);
    let mut quit = false;
    let mut next = Instant::now();
    loop {
        loop {
            match rx.try_recv() {
                Ok(Msg::Add(r)) => writers.push(r),
                Ok(Msg::Quit) => quit = true,
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => {
                    quit = true;
                    break;
                }
            }
        }
        if quit && writers.is_empty() {
            return;
        }
        let mut earliest = tick;
        writers.retain_mut(|r| {
            if r.writer.step(&mut scratch) {
                let _ = r.done.try_send(());
                false
            } else {
                earliest = earliest.min(r.writer.next_wake());
                true
            }
        });
        // абсолютный график тактов: обработка пачки не сдвигает следующий такт; опоздали — следующий отсчитывается от «сейчас»
        next += earliest.min(tick);
        let now = Instant::now();
        if next < now {
            next = now;
        }
        thread::sleep(next - now);
    }
}
