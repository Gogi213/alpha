//! Реактор приёма: один поток процесса читает все соединения (collector-perf, 13.11).
//!
//! Зачем: поток на соединение просыпается на каждый кадр — ≈ 0,6 recvfrom и ≈ 1,2 переключения контекста на кадр, а на PTI/VERW-госте каждый вход в ядро
//! стоит десятки микросекунд. Здесь: неблокирующие сокеты в `epoll` (по уровню), после обработки пачки реактор ждёт окно `window` — просыпается не чаще
//! 1/`window` раз в секунду, за окно на сокете копится несколько пакетов, и на готовый сокет приходится ОДНО `recvmsg` за такт (буфер `RxSock`), а ACK
//! уходит на пачку, а не на пакет. Ядерная метка приёма (`SO_TIMESTAMPNS`) — на чтение.
//! Соединение устанавливает поток сессии (бюджет, dial, TLS, upgrade, подписка — как раньше) и передаёт готовое `WebSocket` реактору; поток сессии ждёт
//! итог (`Outcome`) и дальше ведёт себя как прежде (переподключение, смена входа решает шард). Рукопожатия TLS в реакторе не идут.
//! Разбор, дубли, кодировщик — не здесь: кадр идёт в очередь шарда (рабочий процесса забирает её по таймеру).

use std::os::fd::{FromRawFd, OwnedFd, RawFd};
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};
use std::sync::mpsc::{channel, sync_channel, Receiver, Sender, SyncSender, TryRecvError};
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use tungstenite::{Message, WebSocket};

use super::net::Conn;
use super::session::{wall_ns, Outcome, Rx, Shared};
use super::worker::{WorkerCore, WorkerHandle};

/// Период обхода всех соединений (пинг, сторож pong, окно проверки пути, остановка, досылка записи).
const PASS: Duration = Duration::from_millis(200);
const MAX_EVENTS: usize = 512;
const MAX_EVENTS_C: i32 = 512;
const _: () = assert!(MAX_EVENTS == MAX_EVENTS_C as usize);
const WAKE: u64 = u64::MAX;
/// Корзин гистограммы «пользователь − ядро» (степени двойки мкс).
const HB: usize = 40;

/// Счётчики реактора для health.
pub struct ReactorStats {
    loops: AtomicU64,
    ready: AtomicU64,
    fills: AtomicU64,
    read_syscalls: AtomicU64,
    frames: AtomicU64,
    loop_max_us: AtomicU64,
    beat_ms: AtomicU64,
    kdelta: [AtomicU64; HB],
    /// Длительность оборота реактора (обработка сокетов + рабочий), мкс, и отдельно такта рабочего внутри него.
    loop_us: [AtomicU64; HB],
    tick_us: [AtomicU64; HB],
    stamped: AtomicU64,
    /// Обороты без сна окна: на каком-то сокете чтение заполнило буфер (разбор залпа, например снимков после подписки).
    catchup: AtomicU64,
}

impl Default for ReactorStats {
    fn default() -> Self {
        Self {
            loops: AtomicU64::new(0),
            ready: AtomicU64::new(0),
            fills: AtomicU64::new(0),
            read_syscalls: AtomicU64::new(0),
            frames: AtomicU64::new(0),
            loop_max_us: AtomicU64::new(0),
            beat_ms: AtomicU64::new(0),
            kdelta: std::array::from_fn(|_| AtomicU64::new(0)),
            loop_us: std::array::from_fn(|_| AtomicU64::new(0)),
            tick_us: std::array::from_fn(|_| AtomicU64::new(0)),
            stamped: AtomicU64::new(0),
            catchup: AtomicU64::new(0),
        }
    }
}

fn log2_bucket(us: u64) -> usize {
    usize::try_from(64 - (us | 1).leading_zeros())
        .unwrap_or(HB)
        .min(HB - 1)
}

impl ReactorStats {
    fn hist_add(h: &[AtomicU64; HB], us: u64) {
        if let Some(b) = h.get(log2_bucket(us)) {
            b.fetch_add(1, Relaxed);
        }
    }

    fn kdelta_add(&self, us: i64) {
        if let Some(b) = self
            .kdelta
            .get(log2_bucket(u64::try_from(us.max(0)).unwrap_or(0)))
        {
            b.fetch_add(1, Relaxed);
        }
    }

    /// Процентиль «пользователь − ядро», мкс (верхняя граница корзины степени двойки).
    fn kdelta_pct(&self, p: f64) -> Option<u64> {
        Self::pct(&self.kdelta, p)
    }

    fn pct(h: &[AtomicU64; HB], p: f64) -> Option<u64> {
        let v: Vec<u64> = h.iter().map(|b| b.load(Relaxed)).collect();
        let n: u64 = v.iter().sum();
        if n == 0 {
            return None;
        }
        #[allow(
            clippy::cast_precision_loss,
            clippy::cast_possible_truncation,
            clippy::cast_sign_loss
        )]
        let rank = ((n as f64 * p) as u64).min(n - 1);
        let mut seen = 0u64;
        for (i, c) in v.iter().enumerate() {
            seen += c;
            if seen > rank {
                return Some(1u64 << i);
            }
        }
        None
    }

    /// Раздел health `reactor`.
    pub fn json(&self) -> serde_json::Value {
        let now = super::shard::mono_ms();
        serde_json::json!({
            "loops": self.loops.load(Relaxed), "ready_events": self.ready.load(Relaxed), "reads": self.fills.load(Relaxed), "read_syscalls": self.read_syscalls.load(Relaxed),
            "frames": self.frames.load(Relaxed), "loop_max_us": self.loop_max_us.swap(0, Relaxed),
            "heartbeat_age_ms": now.saturating_sub(self.beat_ms.load(Relaxed)),
            "kernel_stamps": self.stamped.load(Relaxed),
            "catchup_loops": self.catchup.load(Relaxed),
            "loop_us": {"p50": Self::pct(&self.loop_us, 0.5), "p99": Self::pct(&self.loop_us, 0.99), "p99_9": Self::pct(&self.loop_us, 0.999)},
            "worker_tick_us": {"p50": Self::pct(&self.tick_us, 0.5), "p99": Self::pct(&self.tick_us, 0.99), "p99_9": Self::pct(&self.tick_us, 0.999)},
            "user_minus_kernel_us": {"p50": self.kdelta_pct(0.5), "p99": self.kdelta_pct(0.99), "p99_9": self.kdelta_pct(0.999)},
        })
    }
}

/// Параметры реактора.
#[derive(Clone, Copy, Debug)]
pub struct ReactorCfg {
    /// Окно пакетирования: реактор просыпается не чаще раза за окно.
    pub window: Duration,
    /// В `rx` файла — ядерная метка приёма (если есть), иначе пользовательская.
    pub kernel_stamp: bool,
    /// Подписка идёт из реактора (сообщения с паузой биржи в его цикле), а не блокирующим потоком сессии: соединение читается с первого кадра.
    pub pace_sub: bool,
}

enum Cmd {
    Add(Box<Entry>),
    Quit,
}

/// Ручка реактора: передача соединений, остановка, счётчики.
#[derive(Clone)]
pub struct ReactorHandle {
    /// Читать TLS-сокеты по записям с меткой у записи (`RxSock` точной метки): нужен при `kernel_stamp`.
    exact: bool,
    pace_sub: bool,
    tx: Sender<Cmd>,
    wake: Arc<OwnedFd>,
    pub stats: Arc<ReactorStats>,
}

impl std::fmt::Debug for ReactorHandle {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("ReactorHandle")
    }
}

struct Entry {
    ws: WebSocket<Conn>,
    rx: Rx,
    sh: Arc<Shared>,
    seq: u32,
    cancel: Arc<std::sync::atomic::AtomicBool>,
    done: SyncSender<Outcome>,
    fd: RawFd,
    last_fills: u64,
    last_sys: u64,
    out_interest: bool,
    outcome: Option<Outcome>,
}

fn raw_fd(w: &WebSocket<Conn>) -> RawFd {
    use std::os::fd::AsRawFd;
    w.get_ref().tcp().as_raw_fd()
}

impl ReactorHandle {
    /// Подписку сессии ведёт реактор (`ReactorCfg::pace_sub`).
    pub fn paces_subscription(&self) -> bool {
        self.pace_sub
    }

    fn poke(&self) {
        use std::os::fd::AsRawFd;
        let one = 1u64.to_ne_bytes();
        // SAFETY: fd живой (держит `Arc<OwnedFd>`), буфер — 8 байт; запись в eventfd не блокирует (EFD_NONBLOCK), ошибка (счётчик полон) безвредна.
        unsafe {
            libc::write(self.wake.as_raw_fd(), one.as_ptr().cast(), 8);
        }
    }

    /// Передать установленное соединение реактору и ждать итога (поток сессии). `Failed`, если реактор ушёл.
    pub fn run_conn(
        &self,
        sh: &Arc<Shared>,
        mut ws: WebSocket<Conn>,
        seq: u32,
        rx: Rx,
        cancel: Arc<std::sync::atomic::AtomicBool>,
    ) -> Outcome {
        let exact = self.exact && matches!(ws.get_ref(), Conn::Tls(_));
        if let Err(e) = ws.get_mut().rx_mut().into_tick_mode(exact) {
            return Outcome::Failed(format!("режим такта: {e}"));
        }
        let (done, done_rx) = sync_channel(1);
        let fd = raw_fd(&ws);
        let e = Box::new(Entry {
            ws,
            rx,
            sh: Arc::clone(sh),
            seq,
            cancel,
            done,
            fd,
            last_fills: 0,
            last_sys: 0,
            out_interest: false,
            outcome: None,
        });
        if self.tx.send(Cmd::Add(e)).is_err() {
            return Outcome::Failed("реактор ушёл".to_string());
        }
        self.poke();
        done_rx
            .recv()
            .unwrap_or_else(|_| Outcome::Failed("реактор ушёл".to_string()))
    }

    /// Остановка реактора (после того как все сессии закончились).
    pub fn quit(&self) {
        let _ = self.tx.send(Cmd::Quit);
        self.poke();
    }
}

/// Запуск реактора: ручка и поток.
pub fn spawn_reactor(cfg: ReactorCfg) -> Option<(ReactorHandle, JoinHandle<()>)> {
    spawn(cfg, false).map(|(h, _, j)| (h, j))
}

/// Запуск реактора с рабочим внутри: разбор, дубли и кодировщик идут в потоке реактора после обработки сокетов такта (нет отдельного потока и передачи между ними).
pub fn spawn_reactor_inline(
    cfg: ReactorCfg,
) -> Option<(ReactorHandle, WorkerHandle, JoinHandle<()>)> {
    spawn(cfg, true).and_then(|(h, w, j)| w.map(|w| (h, w, j)))
}

fn spawn(
    cfg: ReactorCfg,
    inline: bool,
) -> Option<(ReactorHandle, Option<WorkerHandle>, JoinHandle<()>)> {
    // SAFETY: eventfd без указателей.
    let efd = unsafe { libc::eventfd(0, libc::EFD_NONBLOCK | libc::EFD_CLOEXEC) };
    if efd < 0 {
        return None;
    }
    // SAFETY: `efd` только что создан и принадлежит нам.
    let wake = Arc::new(unsafe { OwnedFd::from_raw_fd(efd) });
    let (tx, rx) = channel();
    let stats = Arc::new(ReactorStats::default());
    let handle = ReactorHandle {
        exact: cfg.kernel_stamp,
        pace_sub: cfg.pace_sub,
        tx,
        wake: Arc::clone(&wake),
        stats: Arc::clone(&stats),
    };
    let (worker_handle, core) = if inline {
        let (wh, wrx) = WorkerHandle::channel(Some(Arc::clone(&wake)));
        (Some(wh), Some(WorkerCore::new(wrx)))
    } else {
        (None, None)
    };
    let join = thread::Builder::new()
        .name("rx-reactor".to_string())
        .stack_size(1 << 20)
        .spawn(move || run(&rx, &wake, &stats, cfg, core))
        .ok()?;
    Some((handle, worker_handle, join))
}

fn ep_ctl(ep: RawFd, op: libc::c_int, fd: RawFd, events: u32, data: u64) {
    let mut ev = libc::epoll_event { events, u64: data };
    // SAFETY: `ev` — живая структура на время вызова; ошибки (fd уже закрыт) безвредны — запись всё равно удаляется.
    unsafe {
        libc::epoll_ctl(ep, op, fd, &mut ev);
    }
}

struct Slab {
    v: Vec<Option<Box<Entry>>>,
    gen: Vec<u32>,
    free: Vec<usize>,
}

impl Slab {
    fn insert(&mut self, e: Box<Entry>) -> u64 {
        let i = if let Some(i) = self.free.pop() {
            if let Some(slot) = self.v.get_mut(i) {
                *slot = Some(e);
            }
            i
        } else {
            self.v.push(Some(e));
            self.gen.push(0);
            self.v.len() - 1
        };
        self.key(i)
    }

    fn key(&self, i: usize) -> u64 {
        (u64::from(self.gen.get(i).copied().unwrap_or(0)) << 32) | i as u64
    }

    /// Освободить место `i`: поколение растёт, старые ключи перестают совпадать.
    fn release(&mut self, i: usize) {
        if let Some(slot) = self.v.get_mut(i) {
            *slot = None;
        }
        if let Some(g) = self.gen.get_mut(i) {
            *g = g.wrapping_add(1);
        }
        self.free.push(i);
    }

    fn get(&mut self, key: u64) -> Option<&mut Box<Entry>> {
        let i = usize::try_from(key & 0xffff_ffff).ok()?;
        if self.gen.get(i).copied() != u32::try_from(key >> 32).ok() {
            return None;
        }
        self.v.get_mut(i)?.as_mut()
    }
}

fn run(
    rx: &Receiver<Cmd>,
    wake: &Arc<OwnedFd>,
    stats: &Arc<ReactorStats>,
    cfg: ReactorCfg,
    mut worker: Option<WorkerCore>,
) {
    use std::os::fd::AsRawFd;
    // SAFETY: epoll_create1 без указателей.
    let ep = unsafe { libc::epoll_create1(libc::EPOLL_CLOEXEC) };
    if ep < 0 {
        return;
    }
    ep_ctl(
        ep,
        libc::EPOLL_CTL_ADD,
        wake.as_raw_fd(),
        libc::EPOLLIN as u32,
        WAKE,
    );
    let mut slab = Slab {
        v: Vec::new(),
        gen: Vec::new(),
        free: Vec::new(),
    };
    let mut evs = vec![libc::epoll_event { events: 0, u64: 0 }; MAX_EVENTS];
    let mut ready: Vec<u64> = Vec::with_capacity(MAX_EVENTS);
    let mut quit = false;
    let mut next_pass = Instant::now() + PASS;
    let mut live = 0usize;
    let mut worker_due: Option<Instant> = None;
    let mut sub_due: Option<Instant> = None;
    loop {
        // до срока обхода, срока блока рабочего или следующего сообщения подписки, миллисекунды вверх (иначе у срока менее 1 мс вышел бы холостой оборот)
        let deadline = [worker_due, sub_due]
            .into_iter()
            .flatten()
            .fold(next_pass, Instant::min);
        let timeout = i32::try_from(
            deadline
                .saturating_duration_since(Instant::now())
                .as_micros()
                .div_ceil(1000),
        )
        .unwrap_or(i32::MAX);
        // SAFETY: `evs` — живой буфер на MAX_EVENTS записей; ядро пишет не больше.
        let n = unsafe { libc::epoll_wait(ep, evs.as_mut_ptr(), MAX_EVENTS_C, timeout.max(0)) };
        let t0 = Instant::now();
        ready.clear();
        let mut woke = false;
        for ev in evs.iter().take(usize::try_from(n).unwrap_or(0)) {
            let d = ev.u64;
            if d == WAKE {
                woke = true;
                let mut buf = [0u8; 8];
                // SAFETY: fd живой, буфер — 8 байт; eventfd неблокирующий.
                unsafe {
                    libc::read(wake.as_raw_fd(), buf.as_mut_ptr().cast(), 8);
                }
            } else {
                ready.push(d);
            }
        }
        if woke || n <= 0 {
            loop {
                match rx.try_recv() {
                    Ok(Cmd::Add(e)) => {
                        let fd = e.fd;
                        let key = slab.insert(e);
                        ep_ctl(ep, libc::EPOLL_CTL_ADD, fd, libc::EPOLLIN as u32, key);
                        // данные, уже лежащие в буферах rustls/tungstenite после блокирующего рукопожатия, события не дадут — первый проход обязателен
                        ready.push(key);
                        live += 1;
                    }
                    Ok(Cmd::Quit) => quit = true,
                    Err(TryRecvError::Empty) => break,
                    Err(TryRecvError::Disconnected) => {
                        quit = true;
                        break;
                    }
                }
            }
        }
        stats.ready.fetch_add(ready.len() as u64, Relaxed);
        let mut more = false;
        for &key in &ready {
            if let Some(e) = slab.get(key) {
                more |= on_ready(e, stats, cfg);
            }
        }
        if more {
            stats.catchup.fetch_add(1, Relaxed);
        }
        let now = Instant::now();
        if now >= next_pass {
            next_pass = now + PASS;
            for i in 0..slab.v.len() {
                if let Some(Some(e)) = slab.v.get_mut(i) {
                    pass(e);
                }
            }
        }
        // рабочий внутри реактора: пачка кадров этого такта и срок ближайшего блока
        if let Some(core) = worker.as_mut() {
            let t_tick = Instant::now();
            let r = core.tick();
            ReactorStats::hist_add(
                &stats.tick_us,
                u64::try_from(t_tick.elapsed().as_micros()).unwrap_or(u64::MAX),
            );
            match r {
                Some(d) => worker_due = d.map(|d| Instant::now() + d),
                None => worker = None,
            }
        }
        // завершённые и обновление интереса к записи; сообщения подписки, чей срок пришёл
        sub_due = None;
        let t_sub = Instant::now();
        for i in 0..slab.v.len() {
            let key = slab.key(i);
            let Some(Some(e)) = slab.v.get_mut(i) else {
                continue;
            };
            if e.outcome.is_none() && e.rx.sub_due().is_some_and(|d| d <= t_sub) {
                if let Some(out) = e.rx.tick(&e.sh, &mut e.ws, e.seq, t_sub) {
                    e.outcome = Some(out);
                }
                if let Some(msg) = e.rx.flush(&mut e.ws).err() {
                    e.outcome = Some(Outcome::Failed(msg));
                }
            }
            if let Some(d) = e.rx.sub_due().filter(|_| e.outcome.is_none()) {
                sub_due = Some(sub_due.map_or(d, |x: Instant| x.min(d)));
            }
            if let Some(out) = e.outcome.take() {
                if !matches!(out, Outcome::Failed(_)) || !e.sh.stop.load(Relaxed) {
                    let _ = e.ws.close(None);
                }
                ep_ctl(ep, libc::EPOLL_CTL_DEL, e.fd, 0, 0);
                let done = e.done.clone();
                slab.release(i);
                let _ = done.send(out);
                live = live.saturating_sub(1);
            } else if e.rx.need_flush != e.out_interest {
                e.out_interest = e.rx.need_flush;
                let ev = libc::EPOLLIN as u32
                    | if e.out_interest {
                        libc::EPOLLOUT as u32
                    } else {
                        0
                    };
                ep_ctl(ep, libc::EPOLL_CTL_MOD, e.fd, ev, key);
            }
        }
        stats.loops.fetch_add(1, Relaxed);
        stats.beat_ms.store(super::shard::mono_ms(), Relaxed);
        let spent = t0.elapsed();
        ReactorStats::hist_add(
            &stats.loop_us,
            u64::try_from(spent.as_micros()).unwrap_or(u64::MAX),
        );
        stats.loop_max_us.fetch_max(
            u64::try_from(spent.as_micros()).unwrap_or(u64::MAX),
            Relaxed,
        );
        if quit && live == 0 && worker.as_ref().is_none_or(WorkerCore::is_idle) {
            // SAFETY: ep — наш дескриптор.
            unsafe {
                libc::close(ep);
            }
            return;
        }
        if spent < cfg.window && !more {
            thread::sleep(cfg.window - spent);
        }
    }
}

/// Готовый сокет: одно чтение за такт, все полные кадры из буфера, ответы, тик. `true` — чтение заполнило буфер, в очереди ядра может быть ещё: оборот без сна.
fn on_ready(e: &mut Box<Entry>, stats: &ReactorStats, cfg: ReactorCfg) -> bool {
    if e.outcome.is_some() {
        return false;
    }
    e.ws.get_mut().rx_mut().arm();
    loop {
        match e.ws.read() {
            Ok(msg) => {
                let user = wall_ns();
                let t_read = Instant::now();
                let (fills, stamp, sys) = {
                    let r = e.ws.get_ref().rx();
                    (r.fills(), r.stamp_ns(), r.syscalls())
                };
                if sys != e.last_sys {
                    stats.read_syscalls.fetch_add(sys - e.last_sys, Relaxed);
                    e.last_sys = sys;
                }
                if fills != e.last_fills {
                    e.last_fills = fills;
                    stats.fills.fetch_add(1, Relaxed);
                    if stamp > 0 {
                        stats.stamped.fetch_add(1, Relaxed);
                        stats.kdelta_add((user - stamp) / 1000);
                    }
                }
                if matches!(msg, Message::Text(_) | Message::Binary(_)) {
                    stats.frames.fetch_add(1, Relaxed);
                }
                // метка записи TLS у пакета, завершившего её (читаем по записям: `RxSock` точной метки), нет метки — пользовательская
                let rx_ns = if cfg.kernel_stamp && stamp > 0 {
                    stamp
                } else {
                    user
                };
                if let Some(out) = e.rx.message(&e.sh, &mut e.ws, e.seq, msg, rx_ns, t_read) {
                    e.outcome = Some(out);
                    return false;
                }
            }
            Err(err) => {
                match Rx::read_error(&err) {
                    None => {}
                    Some(out) => {
                        e.rx.report_end(&e.sh, &e.ws, e.seq, &err);
                        e.outcome = Some(out);
                    }
                }
                break;
            }
        }
    }
    if let Some(out) = e.rx.flush(&mut e.ws).err() {
        e.outcome = Some(Outcome::Failed(out));
        return false;
    }
    if e.outcome.is_none() {
        if let Some(out) = e.rx.tick(&e.sh, &mut e.ws, e.seq, Instant::now()) {
            e.outcome = Some(out);
        }
    }
    e.outcome.is_none() && e.ws.get_ref().rx().saturated()
}

/// Периодический обход: остановка/отмена, досылка записи, тик пинга и окна проверки пути.
fn pass(e: &mut Box<Entry>) {
    if e.outcome.is_some() {
        return;
    }
    if e.cancel.load(Relaxed) || e.sh.stop.load(Relaxed) {
        e.outcome = Some(Outcome::Closed);
        return;
    }
    if let Err(err) = e.rx.flush(&mut e.ws) {
        e.outcome = Some(Outcome::Failed(err));
        return;
    }
    if let Some(out) = e.rx.tick(&e.sh, &mut e.ws, e.seq, Instant::now()) {
        e.outcome = Some(out);
    }
}
