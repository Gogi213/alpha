//! G-022 collector-perf: проба «SO_TIMESTAMPNS через io_uring multishot recvmsg с кольцом буферов» (ядро ≥ 6.0).
//! Подключается к источнику (`serve.py`: сообщения по 200 Б), принимает multishot RECVMSG с provided buffers, разбирает cmsg `SCM_TIMESTAMPNS`,
//! печатает: пришла ли метка, сообщений на CQE, распределение «пользователь − ядро» (мкс).
//! usage: uring_ts_probe <host:port> <секунд> [wait_us]  — wait_us > 0: ждать CQE пачкой с таймаутом (окно W), иначе по одному.

use io_uring::{cqueue, opcode, types, IoUring};
use std::net::TcpStream;
use std::os::fd::AsRawFd;
use std::time::{Duration, Instant};

const BGID: u16 = 7;
const NBUF: u16 = 256;
const BUFSZ: usize = 4096;
const SO_TIMESTAMPNS: i32 = 35;
const MSG: usize = 200;

fn now_ns() -> i64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: валидный указатель на timespec.
    unsafe { libc::clock_gettime(libc::CLOCK_REALTIME, &mut ts) };
    i64::from(ts.tv_sec) * 1_000_000_000 + i64::from(ts.tv_nsec)
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let addr = args.get(1).map_or("127.0.0.1:9100", String::as_str);
    let secs: u64 = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(5);
    let wait_us: u32 = args.get(3).and_then(|s| s.parse().ok()).unwrap_or(0);
    let s = TcpStream::connect(addr).expect("connect");
    let fd = s.as_raw_fd();
    let one: libc::c_int = 1;
    // SAFETY: fd живой, значение — один c_int.
    let rc = unsafe {
        libc::setsockopt(
            fd,
            libc::SOL_SOCKET,
            SO_TIMESTAMPNS,
            std::ptr::from_ref(&one).cast(),
            4,
        )
    };
    assert_eq!(rc, 0, "setsockopt SO_TIMESTAMPNS");
    let mut ring = IoUring::new(256).expect("io_uring_setup");
    let mut bufs = vec![0u8; usize::from(NBUF) * BUFSZ];
    let base = bufs.as_mut_ptr();
    let prov = opcode::ProvideBuffers::new(base, BUFSZ as i32, NBUF, BGID, 0)
        .build()
        .user_data(1);
    // SAFETY: буферы живут до конца программы.
    unsafe { ring.submission().push(&prov).expect("sq") };
    ring.submit_and_wait(1).expect("submit");
    let c = ring.completion().next().expect("cqe");
    assert!(c.result() >= 0, "provide buffers: {}", c.result());
    // SAFETY: нулевая msghdr — допустимое начальное значение.
    let mut msg: libc::msghdr = unsafe { std::mem::zeroed() };
    msg.msg_controllen = 64;
    let submit_multishot = |ring: &mut IoUring| {
        let e = opcode::RecvMsgMulti::new(types::Fd(fd), &msg, BGID)
            .build()
            .user_data(2);
        // SAFETY: msg живёт всё время работы.
        unsafe { ring.submission().push(&e).expect("sq") };
    };
    submit_multishot(&mut ring);
    ring.submit().expect("submit");
    let (mut cqes, mut msgs, mut bytes, mut stamps, mut no_stamp, mut rearm) =
        (0u64, 0u64, 0u64, 0u64, 0u64, 0u64);
    let mut per_cqe = std::collections::BTreeMap::<u64, u64>::new();
    let mut deltas: Vec<i64> = Vec::new();
    let mut errs = 0u64;
    let end = Instant::now() + Duration::from_secs(secs);
    let mut reprovide: Vec<u16> = Vec::new();
    while Instant::now() < end {
        for bid in reprovide.drain(..) {
            let p = opcode::ProvideBuffers::new(
                // SAFETY: bid < NBUF, внутри буфера.
                unsafe { base.add(usize::from(bid) * BUFSZ) },
                BUFSZ as i32,
                1,
                BGID,
                bid,
            )
            .build()
            .user_data(3);
            // SAFETY: см. выше.
            unsafe { ring.submission().push(&p).expect("sq") };
        }
        if wait_us > 0 {
            let ts = types::Timespec::new().nsec(wait_us * 1000);
            let a = types::SubmitArgs::new().timespec(&ts);
            let _ = ring.submitter().submit_with_args(1, &a);
        } else {
            let ts = types::Timespec::new().sec(1);
            let a = types::SubmitArgs::new().timespec(&ts);
            let _ = ring.submitter().submit_with_args(1, &a);
        }
        let t_user = now_ns();
        let mut cq: Vec<(i32, u32, u64)> = Vec::new();
        for c in ring.completion() {
            cq.push((c.result(), c.flags(), c.user_data()));
        }
        for (res, flags, ud) in cq {
            if ud != 2 {
                continue;
            }
            if res < 0 {
                errs += 1;
                if !cqueue::more(flags) {
                    submit_multishot(&mut ring);
                    rearm += 1;
                }
                continue;
            }
            cqes += 1;
            let Some(bid) = cqueue::buffer_select(flags) else {
                continue;
            };
            let off = usize::from(bid) * BUFSZ;
            let buf = &bufs[off..off + res as usize];
            if let Ok(out) = types::RecvMsgOut::parse(buf, &msg) {
                let n = (out.payload_data().len() / MSG) as u64;
                msgs += n;
                bytes += out.payload_data().len() as u64;
                *per_cqe.entry(n.min(20)).or_insert(0) += 1;
                let ctl = out.control_data();
                let mut got = false;
                let mut p = 0usize;
                while p + 16 <= ctl.len() {
                    let len = usize::from_ne_bytes(ctl[p..p + 8].try_into().unwrap_or([0; 8]));
                    let lvl = i32::from_ne_bytes(ctl[p + 8..p + 12].try_into().unwrap_or([0; 4]));
                    let typ = i32::from_ne_bytes(ctl[p + 12..p + 16].try_into().unwrap_or([0; 4]));
                    if len < 16 {
                        break;
                    }
                    if lvl == libc::SOL_SOCKET && typ == SO_TIMESTAMPNS && p + 32 <= ctl.len() {
                        let sec = i64::from_ne_bytes(ctl[p + 16..p + 24].try_into().unwrap_or([0; 8]));
                        let ns = i64::from_ne_bytes(ctl[p + 24..p + 32].try_into().unwrap_or([0; 8]));
                        deltas.push((t_user - (sec * 1_000_000_000 + ns)) / 1000);
                        got = true;
                    }
                    p += (len + 7) & !7;
                }
                if got {
                    stamps += 1;
                } else {
                    no_stamp += 1;
                }
            }
            reprovide.push(bid);
            if !cqueue::more(flags) {
                submit_multishot(&mut ring);
                rearm += 1;
            }
        }
    }
    deltas.sort_unstable();
    let pct = |q: f64| deltas.get(((deltas.len() as f64 * q) as usize).min(deltas.len().saturating_sub(1))).copied().unwrap_or(0);
    println!("wait_us={wait_us}: CQE {cqes}, сообщений {msgs} ({:.2} на CQE), байт {bytes}, с меткой {stamps}, без метки {no_stamp}, ошибок {errs}, перевзвод multishot {rearm}", msgs as f64 / cqes.max(1) as f64);
    println!(
        "пользователь − ядро, мкс: p50 {} p90 {} p99 {} p99.9 {} max {}",
        pct(0.5),
        pct(0.9),
        pct(0.99),
        pct(0.999),
        deltas.last().copied().unwrap_or(0)
    );
    println!("сообщений на CQE (1..20+): {per_cqe:?}");
}
