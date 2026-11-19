//! Диск шарда: сжатие zstd и запись файла суток в отдельном потоке, чтобы остановка диска не останавливала разбор.
//!
//! Принимает от писателя шарда сообщения по порядку: `Open` (новый файл суток), `Block` (строки формата), `Close` (кадр
//! zstd закрыт, рядом `.ok` с числом строк), `Quit`. После каждого блока — `flush` zstd (блок целиком в файле): запись
//! на диске не позже `flush_ms` после приёма. Лаг «приём → диск» (`lag_out`) считается здесь по меткам приёма блока.
//! Сбой записи не рвёт кадр: кодировщик zstd помнит, что уже ушло на диск, блок продолжается с того же места, счёт строк
//! для `.ok` не сбрасывается (файл не открывается заново — новый кадр после оборванного был бы нечитаем).

use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::Ordering::Relaxed;
use std::sync::mpsc::{sync_channel, Receiver, SyncSender, TrySendError};
use std::sync::Arc;
use std::time::{Duration, Instant};

use super::metrics::{Depth, ShardMetrics};

pub const ZSTD_LEVEL: i32 = 1;
/// Сколько раз (по паузе) повторять запись блока при сбое, прежде чем отказаться от блока — только если кодировщик не принял из него ни байта
/// (принятую часть блока не вернуть: пока диск не вернётся, блок дописывается, очередь и приём копят данные).
const BLOCK_RETRIES: u32 = 60;
const RETRY_PAUSE: Duration = Duration::from_secs(1);
pub type Dest = Box<dyn Write + Send>;
type Enc = zstd::stream::write::Encoder<'static, CountW<Dest>>;

pub enum SinkMsg {
    Open {
        path: PathBuf,
    },
    Block {
        data: Vec<u8>,
        rows: u64,
        samples: Vec<i64>,
    },
    Close,
    Quit,
}

#[derive(Clone)]
pub struct SinkTx {
    tx: SyncSender<SinkMsg>,
    depth: Arc<Depth>,
}

pub fn sink_channel(depth: &Arc<Depth>) -> (SinkTx, Receiver<SinkMsg>) {
    let (tx, rx) = sync_channel(depth.cap);
    (
        SinkTx {
            tx,
            depth: Arc::clone(depth),
        },
        rx,
    )
}

impl SinkTx {
    /// Ждёт места, если диск не успевает (обратное давление на писателя и дальше на приём; считается в `Depth`).
    /// `false` — поток диска ушёл.
    pub fn send(&self, msg: SinkMsg) -> bool {
        self.depth.inc();
        match self.tx.try_send(msg) {
            Ok(()) => true,
            Err(TrySendError::Full(msg)) => {
                self.depth.full.fetch_add(1, Relaxed);
                let t0 = Instant::now();
                let ok = self.tx.send(msg).is_ok();
                self.depth.blocked_ns.fetch_add(
                    u64::try_from(t0.elapsed().as_nanos()).unwrap_or(u64::MAX),
                    Relaxed,
                );
                if !ok {
                    self.depth.dec();
                }
                ok
            }
            Err(TrySendError::Disconnected(_)) => {
                self.depth.dec();
                false
            }
        }
    }
}

struct CountW<W> {
    inner: W,
    n: u64,
}

impl<W: Write> Write for CountW<W> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let k = self.inner.write(buf)?;
        self.n += u64::try_from(k).unwrap_or(0);
        Ok(k)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.inner.flush()
    }
}

struct OpenFile {
    enc: Enc,
    path: PathBuf,
    rows: u64,
    /// Сколько байт уже учтено в `bytes_out`.
    counted: u64,
}

fn ok_path(path: &Path) -> PathBuf {
    let mut s = path.as_os_str().to_owned();
    s.push(".ok");
    PathBuf::from(s)
}

fn open_file(path: &Path) -> io::Result<Dest> {
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir)?;
    }
    let f: File = OpenOptions::new().create(true).append(true).open(path)?;
    Ok(Box::new(f))
}

fn wrap(w: Dest, path: &Path) -> io::Result<OpenFile> {
    let enc = zstd::stream::write::Encoder::new(CountW { inner: w, n: 0 }, ZSTD_LEVEL)?;
    Ok(OpenFile {
        enc,
        path: path.to_path_buf(),
        rows: 0,
        counted: 0,
    })
}

/// Блок в кадр zstd с продолжением после сбоя: принятая кодировщиком часть (`done`) не повторяется, `flush` повторяется до конца
/// (кодировщик zstd помнит непереданный выход). Файл и кадр прежние, оборванных кадров нет.
fn write_block(enc: &mut Enc, data: &[u8], done: &mut usize) -> io::Result<()> {
    while let Some(rest) = data.get(*done..).filter(|r| !r.is_empty()) {
        let n = enc.write(rest)?;
        if n == 0 {
            return Err(io::ErrorKind::WriteZero.into());
        }
        *done += n;
    }
    enc.flush()
}

/// Конец файла: кадр zstd закрыт, `.ok` = число строк (закрытые сутки неизменяемы).
fn close_file(f: OpenFile) -> io::Result<()> {
    let OpenFile {
        enc, path, rows, ..
    } = f;
    let mut w = enc.finish()?;
    w.flush()?;
    fs::write(ok_path(&path), format!("{rows}\n"))
}

fn now_ns() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_nanos()).unwrap_or(i64::MAX))
}

/// Поток диска шарда: до `Quit` или закрытия канала.
pub fn run_sink(rx: &Receiver<SinkMsg>, depth: &Depth, m: &ShardMetrics) {
    run_sink_with(rx, depth, m, &open_file, RETRY_PAUSE);
}

/// То же с подменой открытия файла и паузы повтора (проверки сбоев записи).
pub fn run_sink_with(
    rx: &Receiver<SinkMsg>,
    depth: &Depth,
    m: &ShardMetrics,
    open: &dyn Fn(&Path) -> io::Result<Dest>,
    pause: Duration,
) {
    let mut cur: Option<OpenFile> = None;
    let mut retry: Option<PathBuf> = None; // файл не открылся: блоки повторяют открытие (после открытия кадр новый, строк 0)
    let opened = |path: &Path| open(path).and_then(|w| wrap(w, path));
    while let Ok(msg) = rx.recv() {
        depth.dec();
        match msg {
            SinkMsg::Open { path } => {
                finish(&mut cur, m);
                retry = None;
                match opened(&path) {
                    Ok(f) => cur = Some(f),
                    Err(_) => {
                        m.sink().errors += 1;
                        retry = Some(path);
                    }
                }
            }
            SinkMsg::Block {
                data,
                rows,
                samples,
            } => {
                // сбой записи (диск, место): тот же блок продолжается с места сбоя раз в `pause`; отказ от блока — только если из него ничего
                // не принято, и считается (`blocks_dropped`)
                let (mut attempts, mut done) = (0u32, 0usize);
                loop {
                    if cur.is_none() {
                        if let Some(p) = retry.as_ref() {
                            cur = opened(p).ok();
                        }
                    }
                    let failed = match cur.as_mut() {
                        Some(f) => {
                            let t0 = Instant::now();
                            let res = write_block(&mut f.enc, &data, &mut done);
                            let dt_us = u64::try_from(t0.elapsed().as_micros()).unwrap_or(u64::MAX);
                            if res.is_ok() {
                                f.rows += rows;
                                let now = now_ns();
                                let now_ms = now.div_euclid(1_000_000);
                                let total = f.enc.get_ref().n;
                                let wrote = total - f.counted;
                                f.counted = total;
                                let mut s = m.sink();
                                s.flushes += 1;
                                s.bytes_out += wrote;
                                s.write_max_us = s.write_max_us.max(dt_us);
                                s.last_write_ms = now_ms;
                                for rx in &samples {
                                    s.lag_out.add(
                                        now_ms,
                                        u64::try_from((now - rx).max(0) / 1000).unwrap_or(0),
                                    );
                                }
                                break;
                            }
                            true
                        }
                        None => true,
                    };
                    if failed {
                        m.sink().errors += 1;
                    }
                    attempts += 1;
                    if attempts >= BLOCK_RETRIES && done == 0 {
                        m.sink().blocks_dropped += 1;
                        break;
                    }
                    std::thread::sleep(pause);
                }
            }
            SinkMsg::Close => finish(&mut cur, m),
            SinkMsg::Quit => break,
        }
    }
    finish(&mut cur, m);
}

fn finish(cur: &mut Option<OpenFile>, m: &ShardMetrics) {
    if let Some(f) = cur.take() {
        let mut s = m.sink();
        match close_file(f) {
            Ok(()) => s.files_closed += 1,
            Err(_) => s.errors += 1,
        }
    }
}
