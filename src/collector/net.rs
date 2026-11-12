//! Сеть: TCP connect с замером RTT рукопожатия, TLS (rustls, ring) и WebSocket-клиент поверх нашего сокета.
//!
//! Зачем свой путь, а не `connect_async`: RTT рукопожатия (SYN→SYN-ACK по `TCP_INFO` ядра) нужен до TLS — плохой путь
//! отбраковывается за один RTT; порт источника задаёт класс пути, новый connect даёт другой порт (замер TK-527). Сокет
//! остаётся нашим: таймаут чтения будит поток для пинга и остановки, `FIONREAD` показывает очередь ядра (Recv-Q).
//! Единственное `unsafe` — три системных вызова ниже (`getsockopt(TCP_INFO)`, `gettid`, `ioctl(FIONREAD)`; у каждого описание безопасности).

use std::io::{self, Read, Write};
use std::net::{IpAddr, SocketAddr, TcpStream, ToSocketAddrs};
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

/// TCP connect: адреса имени (только IPv4, если вход так задан) по порядку, первый, что ответил; RTT рукопожатия, мс.
pub fn dial(entrance: &Entrance, port: u16) -> io::Result<(TcpStream, f64, IpAddr)> {
    let addrs: Vec<SocketAddr> = match entrance.ip {
        Some(ip) => vec![SocketAddr::new(ip, port)],
        None => (entrance.host.as_str(), port)
            .to_socket_addrs()?
            .filter(|a| match entrance.family {
                Family::Any => true,
                Family::V4 => a.is_ipv4(),
                Family::V6 => a.is_ipv6(),
            })
            .collect(),
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
            format!("нет адресов для {}", entrance.host),
        )
    }))
}

type TlsStream = rustls::StreamOwned<rustls::ClientConnection, TcpStream>;

/// Соединение: TLS (боевое) или открытый TCP (локальные проверки `ws://`).
pub enum Conn {
    Plain(TcpStream),
    Tls(Box<TlsStream>),
}

impl Conn {
    pub fn tcp(&self) -> &TcpStream {
        match self {
            Conn::Plain(s) => s,
            Conn::Tls(t) => &t.sock,
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
        Conn::Tls(Box::new(rustls::StreamOwned::new(cc, tcp)))
    } else {
        Conn::Plain(tcp)
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
