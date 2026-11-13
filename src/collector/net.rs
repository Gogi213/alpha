//! Сеть: TCP connect с замером RTT рукопожатия, TLS (rustls, ring) и WebSocket-клиент поверх нашего сокета.
//!
//! Зачем свой путь, а не `connect_async`: RTT рукопожатия (SYN→SYN-ACK по `TCP_INFO` ядра) нужен до TLS — плохой путь
//! отбраковывается за один RTT; порт источника задаёт класс пути, новый connect даёт другой порт (замер TK-527). Сокет
//! остаётся нашим: таймаут чтения будит поток для пинга и остановки, `FIONREAD` показывает очередь ядра (Recv-Q).
//! Единственное `unsafe` — системные вызовы ниже (`getsockopt(TCP_INFO)`, `gettid`, `ioctl(FIONREAD)`, `recvmsg` с меткой ядра, `setsockopt(SO_TIMESTAMPNS)`; у каждого описание безопасности).

use std::io::{self, Read, Write};
use std::net::{IpAddr, SocketAddr, TcpStream, ToSocketAddrs};
use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};

use tungstenite::client::IntoClientRequest;
use tungstenite::handshake::client::Response;
use tungstenite::{HandshakeError, WebSocket};

use super::exchange::{Entrance, Family};

pub const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
pub const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(10);

/// Состояние TCP сокета: поля `struct tcp_info` Linux (смещения как в Python-ядре `dial.tcp_state`).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct TcpState {
    pub retransmits: u8,
    pub backoff: u8,
    pub rto_ms: u32,
    pub unacked: u32,
    pub lost: u32,
    pub retrans: u32,
    pub last_data_recv_ms: u32,
    pub rtt_ms: u32,
    pub total_retrans: u32,
}

#[cfg(target_os = "linux")]
mod sys {
    use std::os::fd::AsRawFd;

    /// `getsockopt(TCP_INFO)`: первые 104 байта `struct tcp_info`; ядро вернуло меньше (старое ядро) — `None`, а не нули в полях
    /// (RTT 0 выглядел бы «быстрым путём»).
    pub fn tcp_info(s: &impl AsRawFd) -> Option<[u8; 104]> {
        let mut buf = [0u8; 104];
        let mut len: libc::socklen_t = 104;
        // SAFETY: fd живой (заимствован у `s`), буфер — 104 записываемых байта, `len` — его размер; ядро пишет не больше `len`.
        let rc = unsafe {
            libc::getsockopt(
                s.as_raw_fd(),
                libc::IPPROTO_TCP,
                libc::TCP_INFO,
                buf.as_mut_ptr().cast(),
                &mut len,
            )
        };
        (rc == 0 && len >= 104).then_some(buf)
    }

    /// Номер текущего потока ядра (tid).
    pub fn gettid() -> u32 {
        // SAFETY: системный вызов без аргументов и побочных эффектов.
        let t = unsafe { libc::syscall(libc::SYS_gettid) };
        u32::try_from(t).unwrap_or(0)
    }

    /// Включить ядерные метки приёма (`SO_TIMESTAMPNS`): `recvmsg` вернёт `SCM_TIMESTAMPNS` — время вхождения самого нового пакета чтения.
    pub fn enable_rx_timestamps(s: &impl AsRawFd) -> bool {
        let one: libc::c_int = 1;
        // SAFETY: fd живой (заимствован у `s`), значение — один c_int, длина 4.
        let rc = unsafe {
            libc::setsockopt(
                s.as_raw_fd(),
                libc::SOL_SOCKET,
                libc::SO_TIMESTAMPNS,
                std::ptr::from_ref(&one).cast(),
                4,
            )
        };
        rc == 0
    }

    /// Одно неблокирующее `recvmsg` в `buf`; `(прочитано, ядерная метка нс UTC или 0)`; ошибки — как у `recv`.
    pub fn recv_stamped(s: &impl AsRawFd, buf: &mut [u8]) -> std::io::Result<(usize, i64)> {
        let mut iov = libc::iovec {
            iov_base: buf.as_mut_ptr().cast(),
            iov_len: buf.len(),
        };
        let mut ctl = [0u64; 8]; // 64 байта с выравниванием под cmsghdr
                                 // SAFETY: нулевая msghdr — допустимое начальное значение.
        let mut msg: libc::msghdr = unsafe { std::mem::zeroed() };
        msg.msg_iov = &mut iov;
        msg.msg_iovlen = 1;
        msg.msg_control = ctl.as_mut_ptr().cast();
        msg.msg_controllen = std::mem::size_of_val(&ctl) as _;
        // SAFETY: fd живой; `msg` ссылается на живые `iov` (буфер `buf` и его длина) и `ctl` (64 записываемых байта); ядро пишет не больше этих длин.
        let n = unsafe { libc::recvmsg(s.as_raw_fd(), &mut msg, libc::MSG_DONTWAIT) };
        if n < 0 {
            return Err(std::io::Error::last_os_error());
        }
        let mut stamp = 0i64;
        // SAFETY: обход cmsg по правилам libc над буфером `ctl`, заполненным ядром (`msg_controllen` обновлено ядром).
        unsafe {
            let mut c = libc::CMSG_FIRSTHDR(&msg);
            while !c.is_null() {
                if (*c).cmsg_level == libc::SOL_SOCKET && (*c).cmsg_type == libc::SCM_TIMESTAMPNS {
                    let ts: libc::timespec = std::ptr::read_unaligned(libc::CMSG_DATA(c).cast());
                    stamp = i64::from(ts.tv_sec) * 1_000_000_000 + i64::from(ts.tv_nsec);
                }
                c = libc::CMSG_NXTHDR(&msg, c);
            }
        }
        Ok((usize::try_from(n).unwrap_or(0), stamp))
    }

    /// Байт в очереди приёма сокета (Recv-Q).
    pub fn fionread(s: &impl AsRawFd) -> Option<u64> {
        let mut n: libc::c_int = 0;
        // SAFETY: fd живой; FIONREAD пишет один c_int в `n`.
        let rc = unsafe { libc::ioctl(s.as_raw_fd(), libc::FIONREAD, &mut n) };
        (rc == 0).then(|| u64::try_from(n).unwrap_or(0))
    }
}

#[cfg(not(target_os = "linux"))]
mod sys {
    pub fn gettid() -> u32 {
        0
    }

    pub fn tcp_info<T>(_: &T) -> Option<[u8; 104]> {
        None
    }

    pub fn fionread<T>(_: &T) -> Option<u64> {
        None
    }

    pub fn enable_rx_timestamps<T>(_: &T) -> bool {
        false
    }

    pub fn recv_stamped<T>(_: &T, _: &mut [u8]) -> std::io::Result<(usize, i64)> {
        Err(std::io::ErrorKind::Unsupported.into())
    }
}

/// Номер текущего потока ядра (для учёта CPU потоков шарда).
pub fn gettid() -> u32 {
    sys::gettid()
}

/// CPU потока (utime + stime), секунды; `None` — поток ушёл или не Linux.
pub fn thread_cpu_s(tid: u32) -> Option<f64> {
    let stat = std::fs::read_to_string(format!("/proc/self/task/{tid}/stat")).ok()?;
    let rest = stat.get(stat.rfind(')')? + 2..)?;
    let f: Vec<&str> = rest.split_whitespace().collect();
    let (u, s) = (
        f.get(11)?.parse::<f64>().ok()?,
        f.get(12)?.parse::<f64>().ok()?,
    );
    Some((u + s) / 100.0) // CLK_TCK Linux
}

fn u32_at(b: &[u8; 104], off: usize) -> u32 {
    b.get(off..off + 4)
        .and_then(|s| s.try_into().ok())
        .map_or(0, u32::from_ne_bytes)
}

pub fn tcp_state(s: &TcpStream) -> Option<TcpState> {
    let b = sys::tcp_info(s)?;
    Some(TcpState {
        retransmits: b.get(2).copied().unwrap_or(0),
        backoff: b.get(4).copied().unwrap_or(0),
        rto_ms: u32_at(&b, 8) / 1000,
        unacked: u32_at(&b, 24),
        lost: u32_at(&b, 32),
        retrans: u32_at(&b, 36),
        last_data_recv_ms: u32_at(&b, 52),
        rtt_ms: u32_at(&b, 68) / 1000,
        total_retrans: u32_at(&b, 100),
    })
}

/// Байт в очереди ядра на чтение (не Linux — `None`).
pub fn recv_q(s: &TcpStream) -> Option<u64> {
    sys::fionread(s)
}

static ALLOW_IPV6: AtomicBool = AtomicBool::new(false);

/// IPv6 для всех входов всех линий разрешён только флагом `--allow-ipv6` (13.11: таймауты на IPv6-путях, без запасного IPv6 по умолчанию).
pub fn set_allow_ipv6(on: bool) {
    ALLOW_IPV6.store(on, Relaxed);
}

pub fn allow_ipv6() -> bool {
    ALLOW_IPV6.load(Relaxed)
}

/// Адреса для connect: без `allow_v6` — только IPv4 (вход `V6` и закреплённый IPv6-адрес остаются без адресов), порядок разрешения имени сохраняется.
pub fn select_addrs(addrs: Vec<SocketAddr>, family: Family, allow_v6: bool) -> Vec<SocketAddr> {
    addrs
        .into_iter()
        .filter(|a| match family {
            Family::V4 => a.is_ipv4(),
            Family::V6 => allow_v6 && a.is_ipv6(),
            Family::Any => allow_v6 || a.is_ipv4(),
        })
        .collect()
}

/// TCP connect: адреса имени (IPv4; IPv6 — только при `--allow-ipv6`) по порядку, первый, что ответил; RTT рукопожатия, мс.
pub fn dial(entrance: &Entrance, port: u16) -> io::Result<(TcpStream, f64, IpAddr)> {
    let allow_v6 = allow_ipv6();
    let addrs = match entrance.ip {
        Some(ip) => select_addrs(vec![SocketAddr::new(ip, port)], entrance.family, allow_v6),
        None => select_addrs(
            (entrance.host.as_str(), port).to_socket_addrs()?.collect(),
            entrance.family,
            allow_v6,
        ),
    };
    let mut last: Option<io::Error> = None;
    for addr in addrs {
        let t0 = Instant::now();
        match TcpStream::connect_timeout(&addr, CONNECT_TIMEOUT) {
            Ok(s) => {
                s.set_nodelay(true)?;
                let rtt = tcp_state(&s)
                    .map(|t| f64::from(t.rtt_ms))
                    .filter(|r| *r > 0.0)
                    .unwrap_or_else(|| t0.elapsed().as_secs_f64() * 1000.0);
                return Ok((s, rtt, addr.ip()));
            }
            Err(e) => last = Some(e),
        }
    }
    Err(last.unwrap_or_else(|| {
        io::Error::new(
            io::ErrorKind::NotFound,
            format!("нет адресов для {} (IPv6 выключен)", entrance.host),
        )
    }))
}

/// Размер буфера приёма реактора на соединение: одно `recvmsg` за такт читает до него.
pub const RX_BUF: usize = 64 * 1024;

/// Сокет для rustls/tungstenite: блокирующий (потоки приёма) — чтение сразу из сокета; такт (реактор) — одно `recvmsg` в свой буфер на такт
/// (`arm`), дальше читатели получают данные из буфера, а когда он пуст — `WouldBlock` без вызова ядра. Так на готовый сокет за такт приходится
/// одно чтение (collector-perf: «вычитать до EAGAIN» стоило бы второго входа в ядро), а ядерная метка приёма — на чтение.
pub struct RxSock {
    sock: TcpStream,
    tick: bool,
    buf: Vec<u8>,
    pos: usize,
    len: usize,
    armed: bool,
    eof: bool,
    stamp_ns: i64,
    fills: u64,
}

impl RxSock {
    pub fn new(sock: TcpStream) -> Self {
        Self {
            sock,
            tick: false,
            buf: Vec::new(),
            pos: 0,
            len: 0,
            armed: false,
            eof: false,
            stamp_ns: 0,
            fills: 0,
        }
    }

    /// Перейти на режим такта: сокет неблокирующий, ядерные метки включены, буфер выделен.
    pub fn into_tick_mode(&mut self) -> io::Result<()> {
        self.sock.set_nonblocking(true)?;
        sys::enable_rx_timestamps(&self.sock);
        self.buf = vec![0u8; RX_BUF];
        self.tick = true;
        Ok(())
    }

    /// Разрешить одно чтение из ядра на этот такт.
    pub fn arm(&mut self) {
        self.armed = true;
    }

    /// Ядерная метка последнего чтения (нс UTC; 0 — нет).
    pub fn stamp_ns(&self) -> i64 {
        self.stamp_ns
    }

    /// Сколько чтений из ядра сделано.
    pub fn fills(&self) -> u64 {
        self.fills
    }

    /// Сколько байт последнего чтения ещё не передано читателям.
    pub fn buffered(&self) -> usize {
        self.len - self.pos
    }

    pub fn tcp(&self) -> &TcpStream {
        &self.sock
    }
}

impl Read for RxSock {
    fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
        if !self.tick {
            return self.sock.read(out);
        }
        if self.pos == self.len {
            if self.eof {
                return Ok(0);
            }
            if !self.armed {
                return Err(io::ErrorKind::WouldBlock.into());
            }
            self.armed = false;
            let (n, stamp) = sys::recv_stamped(&self.sock, &mut self.buf)?;
            self.fills += 1;
            if n == 0 {
                self.eof = true;
                return Ok(0);
            }
            (self.pos, self.len, self.stamp_ns) = (0, n, stamp);
        }
        let k = out.len().min(self.len - self.pos);
        if let (Some(dst), Some(src)) = (out.get_mut(..k), self.buf.get(self.pos..self.pos + k)) {
            dst.copy_from_slice(src);
        }
        self.pos += k;
        Ok(k)
    }
}

impl Write for RxSock {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.sock.write(buf)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.sock.flush()
    }
}

type TlsStream = rustls::StreamOwned<rustls::ClientConnection, RxSock>;

/// Соединение: TLS (боевое) или открытый TCP (локальные проверки `ws://`).
pub enum Conn {
    Plain(RxSock),
    Tls(Box<TlsStream>),
}

impl Conn {
    pub fn tcp(&self) -> &TcpStream {
        match self {
            Conn::Plain(s) => s.tcp(),
            Conn::Tls(t) => t.sock.tcp(),
        }
    }

    pub fn rx(&self) -> &RxSock {
        match self {
            Conn::Plain(s) => s,
            Conn::Tls(t) => &t.sock,
        }
    }

    pub fn rx_mut(&mut self) -> &mut RxSock {
        match self {
            Conn::Plain(s) => s,
            Conn::Tls(t) => &mut t.sock,
        }
    }
}

impl Read for Conn {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        match self {
            Conn::Plain(s) => s.read(buf),
            Conn::Tls(t) => t.read(buf),
        }
    }
}

impl Write for Conn {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        match self {
            Conn::Plain(s) => s.write(buf),
            Conn::Tls(t) => t.write(buf),
        }
    }

    fn flush(&mut self) -> io::Result<()> {
        match self {
            Conn::Plain(s) => s.flush(),
            Conn::Tls(t) => t.flush(),
        }
    }
}

fn tls_config() -> Result<Arc<rustls::ClientConfig>, String> {
    static CFG: OnceLock<Result<Arc<rustls::ClientConfig>, String>> = OnceLock::new();
    CFG.get_or_init(|| {
        let roots =
            rustls::RootCertStore::from_iter(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
        let provider = Arc::new(rustls::crypto::ring::default_provider());
        let cfg = rustls::ClientConfig::builder_with_provider(provider)
            .with_safe_default_protocol_versions()
            .map_err(|e| format!("rustls: {e}"))?
            .with_root_certificates(roots)
            .with_no_client_auth();
        Ok(Arc::new(cfg))
    })
    .clone()
}

/// WebSocket поверх уже открытого сокета: TLS (для `wss`), upgrade; таймауты рукопожатия на сокете.
pub fn ws_connect(
    tcp: TcpStream,
    url: &str,
    host: &str,
    tls: bool,
) -> Result<(WebSocket<Conn>, Response), String> {
    tcp.set_read_timeout(Some(HANDSHAKE_TIMEOUT))
        .map_err(|e| e.to_string())?;
    tcp.set_write_timeout(Some(HANDSHAKE_TIMEOUT))
        .map_err(|e| e.to_string())?;
    let conn = if tls {
        let name = rustls::pki_types::ServerName::try_from(host.to_string())
            .map_err(|e| format!("имя {host}: {e}"))?;
        let cc =
            rustls::ClientConnection::new(tls_config()?, name).map_err(|e| format!("tls: {e}"))?;
        Conn::Tls(Box::new(rustls::StreamOwned::new(cc, RxSock::new(tcp))))
    } else {
        Conn::Plain(RxSock::new(tcp))
    };
    let req = url
        .into_client_request()
        .map_err(|e| format!("запрос {url}: {e}"))?;
    match tungstenite::client(req, conn) {
        Ok(ok) => Ok(ok),
        Err(HandshakeError::Failure(e)) => Err(format!("рукопожатие: {e}")),
        Err(HandshakeError::Interrupted(_)) => Err("рукопожатие: таймаут".to_string()),
    }
}
