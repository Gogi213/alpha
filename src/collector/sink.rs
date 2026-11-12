//! Диск шарда: сжатие zstd и запись файла суток в отдельном потоке, чтобы остановка диска не останавливала разбор.
//!
//! Принимает от писателя шарда сообщения по порядку: `Open` (новый файл суток), `Block` (строки формата), `Close` (кадр
//! zstd закрыт, рядом `.ok` с числом строк), `Quit`. После каждого блока — `flush` zstd (блок целиком в файле): запись
//! на диске не позже `flush_ms` после приёма. Лаг «приём → диск» (`lag_out`) считается здесь по меткам приёма блока.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::Ordering::Relaxed;
use std::sync::mpsc::{sync_channel, Receiver, SyncSender, TrySendError};
use std::sync::Arc;
use std::time::Instant;

use super::metrics::{Depth, ShardMetrics};

pub const ZSTD_LEVEL: i32 = 1;

pub enum SinkMsg {
    Open { path: PathBuf },
    Block { data: Vec<u8>, rows: u64, samples: Vec<i64> },
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
    (SinkTx { tx, depth: Arc::clone(depth) }, rx)
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
                self.depth.blocked_ns.fetch_add(u64::try_from(t0.elapsed().as_nanos()).unwrap_or(u64::MAX), Relaxed);
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
    enc: zstd::stream::write::Encoder<'static, CountW<File>>,
    path: PathBuf,
    rows: u64,
}

fn ok_path(path: &Path) -> PathBuf {
    let mut s = path.as_os_str().to_owned();
    s.push(".ok");
    PathBuf::from(s)
}

fn open_file(path: &Path) -> io::Result<OpenFile> {
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir)?;
    }
    let f = OpenOptions::new().create(true).append(true).open(path)?;
    let enc = zstd::stream::write::Encoder::new(CountW { inner: f, n: 0 }, ZSTD_LEVEL)?;
    Ok(OpenFile { enc, path: path.to_path_buf(), rows: 0 })
}

/// Конец файла: кадр zstd закрыт, `.ok` = число строк (закрытые сутки неизменяемы).
fn close_file(f: OpenFile) -> io::Result<()> {
    let OpenFile { enc, path, rows } = f;
    let mut w = enc.finish()?;
    w.flush()?;
    fs::write(ok_path(&path), format!("{rows}\n"))
}

fn now_ns() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| i64::try_from(d.as_nanos()).unwrap_or(i64::MAX))
}

/// Поток диска шарда: до `Quit` или закрытия канала.
pub fn run_sink(rx: &Receiver<SinkMsg>, depth: &Depth, m: &ShardMetrics) {
    let mut cur: Option<OpenFile> = None;
    let mut retry: Option<PathBuf> = None; // после ошибки записи: открыть файл заново (новый кадр zstd после оборванного)
    while let Ok(msg) = rx.recv() {
        depth.dec();
        match msg {
            SinkMsg::Open { path } => {
                finish(&mut cur, m);
                retry = None;
                match open_file(&path) {
                    Ok(f) => cur = Some(f),
                    Err(_) => {
                        m.sink().errors += 1;
                        retry = Some(path);
                    }
                }
            }
            SinkMsg::Block { data, rows, samples } => {
                if cur.is_none() {
                    if let Some(p) = retry.take() {
                        match open_file(&p) {
                            Ok(f) => cur = Some(f),
                            Err(_) => {
                                m.sink().errors += 1;
                                retry = Some(p);
                            }
                        }
                    }
                }
                let Some(f) = cur.as_mut() else { continue };
                let t0 = Instant::now();
                let before = f.enc.get_ref().n;
                let res = f.enc.write_all(&data).and_then(|()| f.enc.flush());
                let dt_us = u64::try_from(t0.elapsed().as_micros()).unwrap_or(u64::MAX);
                match res {
                    Ok(()) => {
                        f.rows += rows;
                        let now = now_ns();
                        let now_ms = now.div_euclid(1_000_000);
                        let wrote = f.enc.get_ref().n - before;
                        let mut s = m.sink();
                        s.flushes += 1;
                        s.bytes_out += wrote;
                        s.write_max_us = s.write_max_us.max(dt_us);
                        s.last_write_ms = now_ms;
                        for rx in samples {
                            s.lag_out.add(now_ms, u64::try_from((now - rx).max(0) / 1000).unwrap_or(0));
                        }
                    }
                    Err(_) => {
                        m.sink().errors += 1;
                        retry = cur.take().map(|f| f.path);
                    }
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
