//! Проверки collector-perf: сокет такта (`RxSock`), сортировка пачки по приёму в писателе (минимум ядерной метки по линиям), рабочий процесса.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::Arc;
use std::time::Duration;

use bytes::Bytes;

use super::binance::Binance;
use super::codec::{Decoded, Decoder};
use super::exchange::Exchange;
use super::metrics::ShardMetrics;
use super::net::RxSock;
use super::ring::{ring, Item, Payload};
use super::sink::{run_sink, sink_channel};
use super::worker::spawn_worker;
use super::writer::{ShardWriter, WriterCfg};

/// Ядро включает метки приёма отложенно (общий ключ netstamp, рабочая очередь): первые пакеты после включения могут прийти без метки.
/// Прогрев: по байту, пока метка не появится; прочитанные байты отбрасываются.
#[cfg(target_os = "linux")]
fn warm_kernel_stamps(peer: &mut TcpStream, s: &mut RxSock) {
    let mut b = [0u8; 8];
    for _ in 0..200 {
        peer.write_all(b"w").unwrap();
        std::thread::sleep(Duration::from_millis(10));
        s.arm();
        assert_eq!(s.read(&mut b).unwrap(), 1);
        if s.stamp_ns() > 0 {
            return;
        }
    }
    panic!("ядро не отдаёт метки приёма");
}

#[test]
fn rxsock_tick_mode_reads_once_per_arm_and_reports_kernel_stamp() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let mut peer = TcpStream::connect(addr).unwrap();
    let (srv, _) = listener.accept().unwrap();
    let mut s = RxSock::new(srv);
    s.into_tick_mode(false).unwrap();
    #[cfg(target_os = "linux")]
    warm_kernel_stamps(&mut peer, &mut s);
    let base = s.fills();
    let mut buf = [0u8; 16];
    // не взведён — вызова ядра нет, WouldBlock
    assert_eq!(
        s.read(&mut buf).unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
    peer.write_all(b"hello").unwrap();
    std::thread::sleep(Duration::from_millis(50));
    s.arm();
    let n = s.read(&mut buf).unwrap();
    assert_eq!(&buf[..n], b"hello");
    assert_eq!(s.fills(), base + 1);
    #[cfg(target_os = "linux")]
    assert!(s.stamp_ns() > 0, "ядерная метка приёма пришла");
    // буфер пуст, повторное чтение без нового arm — без вызова ядра
    peer.write_all(b"more").unwrap();
    std::thread::sleep(Duration::from_millis(50));
    assert_eq!(
        s.read(&mut buf).unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
    assert_eq!(s.fills(), base + 1, "без arm ядро не трогали");
    s.arm();
    let n = s.read(&mut buf).unwrap();
    assert_eq!(&buf[..n], b"more");
    assert_eq!(s.fills(), base + 2);
    // чтение маленькими кусками из одного буфера — одно обращение к ядру
    peer.write_all(b"abcdef").unwrap();
    std::thread::sleep(Duration::from_millis(50));
    s.arm();
    let mut two = [0u8; 2];
    for want in [b"ab", b"cd", b"ef"] {
        assert_eq!(s.read(&mut two).unwrap(), 2);
        assert_eq!(&two, want);
    }
    assert_eq!(s.fills(), base + 3);
    // закрытие пира — EOF после взведения
    drop(peer);
    std::thread::sleep(Duration::from_millis(50));
    s.arm();
    assert_eq!(s.read(&mut buf).unwrap(), 0);
}

#[test]
fn rxsock_reports_saturated_read_when_queue_is_deeper_than_buffer() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let mut peer = TcpStream::connect(addr).unwrap();
    let (srv, _) = listener.accept().unwrap();
    let mut s = RxSock::new(srv);
    s.into_tick_mode(false).unwrap();
    assert!(!s.saturated());
    let total = super::net::RX_BUF * 2 + 100;
    peer.write_all(&vec![7u8; total]).unwrap();
    std::thread::sleep(Duration::from_millis(100));
    let mut sink = vec![0u8; super::net::RX_BUF];
    let mut got = 0usize;
    let mut saturated_reads = 0usize;
    while got < total {
        s.arm();
        loop {
            match s.read(&mut sink) {
                Ok(n) if n > 0 => got += n,
                _ => break,
            }
        }
        if s.saturated() {
            saturated_reads += 1;
        }
    }
    assert_eq!(got, total);
    assert!(
        saturated_reads >= 1,
        "полный буфер помечен насыщенным: {saturated_reads}"
    );
    assert!(!s.saturated(), "последнее чтение неполное — признак снят");
}

fn bn_book(u: i64) -> String {
    format!(
        r#"{{"stream":"btcusdt@bookTicker","data":{{"e":"bookTicker","u":{u},"s":"BTCUSDT","ps":"BTCUSDT","b":"1.0","B":"2","a":"1.1","A":"3","T":1000,"E":1000,"st":1}}}}"#
    )
}

fn item(rx_ns: i64, sender: u32, f: &str) -> Item {
    Item {
        rx_ns,
        sender,
        lane: 0,
        settled: true,
        payload: Payload::Frame(Bytes::from(f.to_string())),
    }
}

fn test_clock() -> i64 {
    20_000 * 86_400 * 1_000_000_000 + 80_000_000_000
}

/// Книга: строки из файла суток единственного шарда в `dir`.
fn rows(dir: &std::path::Path) -> Vec<Decoded> {
    let mut out = Vec::new();
    for d in std::fs::read_dir(dir).unwrap().flatten() {
        if !d.path().is_dir() {
            continue;
        }
        for f in std::fs::read_dir(d.path()).unwrap().flatten() {
            if f.path().extension().is_some_and(|e| e == "zst") {
                let text = String::from_utf8(
                    zstd::decode_all(std::fs::File::open(f.path()).unwrap()).unwrap(),
                )
                .unwrap();
                let mut dec = Decoder::new();
                out.extend(text.lines().map(|l| dec.line(l).unwrap()));
            }
        }
    }
    out
}

fn run_batch(sort: bool, items: Vec<Item>) -> Vec<Decoded> {
    let dir = tempfile::tempdir().unwrap();
    let metrics = ShardMetrics::new("book00", 0, 1024, 16);
    let (tx, rx) = ring(&metrics.ring);
    let (stx, srx) = sink_channel(&metrics.sink_q);
    let cfg = WriterCfg {
        root: dir.path().to_path_buf(),
        shard: "book00".into(),
        run_id: 1_794_000_000,
        flush: Duration::from_secs(3600),
        block_max: 64 * 1024,
        clock: test_clock,
    };
    let exch: Arc<dyn Exchange> = Arc::new(Binance);
    let mut w = ShardWriter::new(cfg, exch, Arc::clone(&metrics), stx, rx);
    w.set_sort_batch(sort);
    let (m2, d2) = (Arc::clone(&metrics), Arc::clone(&metrics.sink_q));
    let sink = std::thread::spawn(move || run_sink(&srx, &d2, &m2));
    for it in items {
        tx.push(it).unwrap();
    }
    tx.push(Item {
        rx_ns: 0,
        sender: 0,
        lane: 0,
        settled: true,
        payload: Payload::Shutdown,
    })
    .unwrap();
    // всё в одной пачке: один такт забирает все элементы
    let mut scratch = Vec::new();
    assert!(
        w.step(&mut scratch),
        "Shutdown в той же пачке — писатель закончил за один такт"
    );
    sink.join().unwrap();
    rows(dir.path())
}

#[test]
fn writer_sorted_batch_keeps_the_minimum_receive_stamp_of_a_duplicate() {
    let t = 20_000 * 86_400 * 1_000_000_000_i64;
    // линия 2 обработана первой, но линия 1 приняла ту же запись раньше (меньшая метка)
    let items = || {
        vec![
            item(t + 2_000_000, 2, &bn_book(7)),
            item(t + 1_000_000, 1, &bn_book(7)),
            item(t + 3_000_000, 1, &bn_book(8)),
        ]
    };
    let unsorted = run_batch(false, items());
    let sorted = run_batch(true, items());
    let rx_of = |rows: &[Decoded], want_u: i64| {
        rows.iter().find_map(|d| match d {
            Decoded::Book { u, rx_ns, .. } if *u == want_u => Some(*rx_ns),
            _ => None,
        })
    };
    assert_eq!(
        rx_of(&unsorted, 7),
        Some(t + 2_000_000),
        "без сортировки — метка обработанного первым"
    );
    assert_eq!(
        rx_of(&sorted, 7),
        Some(t + 1_000_000),
        "с сортировкой — минимум"
    );
    assert_eq!(rx_of(&sorted, 8), Some(t + 3_000_000));
    let books = sorted
        .iter()
        .filter(|d| matches!(d, Decoded::Book { .. }))
        .count();
    assert_eq!(books, 2, "дубль снят");
}

static HOLD_NOW: std::sync::atomic::AtomicI64 = std::sync::atomic::AtomicI64::new(0);

fn hold_clock() -> i64 {
    HOLD_NOW.load(std::sync::atomic::Ordering::Relaxed)
}

/// Три записи двумя тактами: в первом приходит запись с меткой +30 мс (сокет, прочитанный в обороте позже), во втором — записи с метками +10 и +20 мс (сокет, прочитанный раньше,
/// но доставивший их в следующем такте). Возвращает (метки `rx` в файле, сколько записей поднято до метки предыдущей).
fn run_two_ticks(hold_ms: u64) -> (Vec<i64>, u64) {
    use std::sync::atomic::Ordering::Relaxed;
    let t = 20_000 * 86_400 * 1_000_000_000_i64;
    let dir = tempfile::tempdir().unwrap();
    let metrics = ShardMetrics::new("book00", 0, 1024, 16);
    let (tx, rx) = ring(&metrics.ring);
    let (stx, srx) = sink_channel(&metrics.sink_q);
    let cfg = WriterCfg {
        root: dir.path().to_path_buf(),
        shard: "book00".into(),
        run_id: 1_794_000_000,
        flush: Duration::from_secs(3600),
        block_max: 64 * 1024,
        clock: hold_clock,
    };
    let exch: Arc<dyn Exchange> = Arc::new(Binance);
    let mut w = ShardWriter::new(cfg, exch, Arc::clone(&metrics), stx, rx);
    w.set_sort_batch(true);
    w.set_hold(Duration::from_millis(hold_ms));
    let (m2, d2) = (Arc::clone(&metrics), Arc::clone(&metrics.sink_q));
    let sink = std::thread::spawn(move || run_sink(&srx, &d2, &m2));
    let mut scratch = Vec::new();
    HOLD_NOW.store(t + 40_000_000, Relaxed);
    tx.push(item(t + 30_000_000, 1, &bn_book(1))).unwrap();
    assert!(!w.step(&mut scratch));
    HOLD_NOW.store(t + 45_000_000, Relaxed);
    tx.push(item(t + 10_000_000, 2, &bn_book(2))).unwrap();
    tx.push(item(t + 20_000_000, 2, &bn_book(3))).unwrap();
    assert!(!w.step(&mut scratch));
    HOLD_NOW.store(t + 500_000_000, Relaxed);
    tx.push(Item {
        rx_ns: 0,
        sender: 0,
        lane: 0,
        settled: true,
        payload: Payload::Shutdown,
    })
    .unwrap();
    assert!(
        w.step(&mut scratch),
        "Shutdown выпускает всё и закрывает писателя"
    );
    sink.join().unwrap();
    let stamps = rows(dir.path())
        .iter()
        .filter_map(|d| match d {
            Decoded::Book { rx_ns, .. } => Some(*rx_ns - t),
            _ => None,
        })
        .collect();
    let clamped = metrics.writer().clamped;
    (stamps, clamped)
}

#[test]
fn writer_hold_puts_late_arrivals_in_order_instead_of_clamping_rx() {
    let (off, clamped_off) = run_two_ticks(0);
    assert_eq!(clamped_off, 2, "без выдержки обе поздние записи подняты");
    assert_eq!(off, vec![30_000_000, 30_000_000, 30_000_000]);
    let (on, clamped_on) = run_two_ticks(50);
    assert_eq!(clamped_on, 0, "с выдержкой подъёма нет");
    assert_eq!(
        on,
        vec![10_000_000, 20_000_000, 30_000_000],
        "настоящие метки по порядку"
    );
}

/// G5: остановка диска на 30 с (поток диска засыпает), приём идёт 40 с по 3 000 кадров/с: писатель и очередь не блокируются, ничего не потеряно.
/// Запуск вручную (≈ 50 с): `cargo test --release g5_disk_pause -- --ignored --nocapture`.
#[test]
#[ignore = "G5: 30 с паузы диска, ≈ 50 с; запускать вручную на машине сборки"]
fn g5_disk_pause_30s_loses_nothing_and_never_blocks_the_writer() {
    use std::sync::atomic::Ordering::Relaxed;
    let dir = tempfile::tempdir().unwrap();
    let metrics = ShardMetrics::new("book00", 0, 1 << 15, 600);
    let (tx, rx) = ring(&metrics.ring);
    let (stx, srx) = sink_channel(&metrics.sink_q);
    let cfg = WriterCfg {
        root: dir.path().to_path_buf(),
        shard: "book00".into(),
        run_id: 1_794_000_000,
        flush: Duration::from_millis(100),
        block_max: 256 * 1024,
        clock: super::session::wall_ns,
    };
    let exch: Arc<dyn Exchange> = Arc::new(Binance);
    let mut w = ShardWriter::new(cfg, exch, Arc::clone(&metrics), stx, rx);
    w.set_sort_batch(true);
    let writer = std::thread::spawn(move || w.run());
    let (m2, d2) = (Arc::clone(&metrics), Arc::clone(&metrics.sink_q));
    let sink = std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(30));
        run_sink(&srx, &d2, &m2);
    });
    let t0 = std::time::Instant::now();
    let mut pushed = 0i64;
    let mut worst_push = Duration::ZERO;
    while t0.elapsed() < Duration::from_secs(40) {
        for _ in 0..3 {
            pushed += 1;
            let s = std::time::Instant::now();
            tx.push(item(super::session::wall_ns(), 1, &bn_book(pushed)))
                .unwrap();
            worst_push = worst_push.max(s.elapsed());
        }
        std::thread::sleep(Duration::from_millis(1));
    }
    tx.push(Item {
        rx_ns: 0,
        sender: 0,
        lane: 0,
        settled: true,
        payload: Payload::Shutdown,
    })
    .unwrap();
    writer.join().unwrap();
    let depth_max = metrics.sink_q.max.load(Relaxed);
    let (ring_full, sink_full) = (
        metrics.ring.full.load(Relaxed),
        metrics.sink_q.full.load(Relaxed),
    );
    sink.join().unwrap();
    let books = rows(dir.path())
        .iter()
        .filter(|d| matches!(d, Decoded::Book { .. }))
        .count();
    let line = format!("G5: кадров {pushed}, записей {books}, глубина очереди диска макс {depth_max} из 600, полных очередей ring {ring_full} sink {sink_full}, худший push {worst_push:?}");
    eprintln!("{line}");
    let _ = std::fs::write(std::env::temp_dir().join("g5-result.txt"), &line);
    assert_eq!(i64::try_from(books).unwrap(), pushed, "ни одной потери");
    assert_eq!(sink_full, 0, "очередь диска не переполнялась");
    assert_eq!(ring_full, 0);
    assert!(depth_max < 600, "запас очереди диска остался: {depth_max}");
    assert!(
        worst_push < Duration::from_millis(50),
        "приём не блокировался: {worst_push:?}"
    );
}

#[test]
fn worker_serves_two_shards_by_timer_and_closes_them() {
    let (worker, join) = spawn_worker(Duration::from_millis(1));
    let mut dirs = Vec::new();
    let mut handles = Vec::new();
    for name in ["book00", "book01"] {
        let dir = tempfile::tempdir().unwrap();
        let metrics = ShardMetrics::new(name, 0, 1024, 16);
        let (tx, rx) = ring(&metrics.ring);
        let (stx, srx) = sink_channel(&metrics.sink_q);
        let cfg = WriterCfg {
            root: dir.path().to_path_buf(),
            shard: name.into(),
            run_id: 1_794_000_000,
            flush: Duration::from_millis(20),
            block_max: 64 * 1024,
            clock: test_clock,
        };
        let exch: Arc<dyn Exchange> = Arc::new(Binance);
        let w = ShardWriter::new(cfg, exch, Arc::clone(&metrics), stx, rx);
        let (m2, d2) = (Arc::clone(&metrics), Arc::clone(&metrics.sink_q));
        let sink = std::thread::spawn(move || run_sink(&srx, &d2, &m2));
        let done = worker.add(w);
        let t = 20_000 * 86_400 * 1_000_000_000_i64;
        for u in 1..=50 {
            tx.push(item(t + u * 1_000_000, 1, &bn_book(u))).unwrap();
        }
        tx.push(Item {
            rx_ns: 0,
            sender: 0,
            lane: 0,
            settled: true,
            payload: Payload::Shutdown,
        })
        .unwrap();
        handles.push((done, sink, metrics, tx));
        dirs.push(dir);
    }
    for (done, sink, metrics, _tx) in handles {
        done.recv_timeout(Duration::from_secs(5))
            .expect("писатель закончил");
        sink.join().unwrap();
        assert_eq!(metrics.writer().records, 50);
    }
    for d in &dirs {
        let r = rows(d.path());
        assert_eq!(
            r.iter()
                .filter(|x| matches!(x, Decoded::Book { .. }))
                .count(),
            50
        );
    }
    worker.quit();
    join.join().unwrap();
}

// ---- G5 (облегчённый, открытый ws): границы кадров на неблокирующем сокете реактора ----

/// Кадр WS сервер→клиент без маски.
fn ws_frame(opcode: u8, fin: bool, payload: &[u8]) -> Vec<u8> {
    let mut f = vec![(if fin { 0x80 } else { 0 }) | opcode];
    let n = payload.len();
    if n < 126 {
        f.push(u8::try_from(n).unwrap());
    } else if n < 65_536 {
        f.push(126);
        f.extend_from_slice(&u16::try_from(n).unwrap().to_be_bytes());
    } else {
        f.push(127);
        f.extend_from_slice(&(n as u64).to_be_bytes());
    }
    f.extend_from_slice(payload);
    f
}

/// Источник «биржа» с ручной нарезкой байтов: после подписки отдаёт заранее заданные куски с паузами и держит соединение.
fn raw_source(listener: TcpListener, chunks: Vec<(Vec<u8>, u64)>) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        let (s, _) = listener.accept().unwrap();
        s.set_nodelay(true).unwrap();
        let mut ws = tungstenite::accept(s).unwrap();
        let _ = ws.read().unwrap(); // подписка
        for (bytes, pause_ms) in chunks {
            ws.get_mut().write_all(&bytes).unwrap();
            ws.get_mut().flush().unwrap();
            if pause_ms > 0 {
                std::thread::sleep(Duration::from_millis(pause_ms));
            }
        }
        // держим соединение, пока клиент не закроет
        ws.get_ref()
            .set_read_timeout(Some(Duration::from_millis(100)))
            .unwrap();
        for _ in 0..200 {
            match ws.read() {
                Ok(tungstenite::Message::Close(_)) | Err(tungstenite::Error::ConnectionClosed) => {
                    break
                }
                Err(tungstenite::Error::Io(e))
                    if matches!(
                        e.kind(),
                        std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                    ) => {}
                Err(_) => break,
                Ok(_) => {}
            }
        }
    })
}

#[test]
fn reactor_handles_frame_boundaries_on_a_nonblocking_socket() {
    frame_boundaries(false);
}

#[test]
fn reactor_with_inline_worker_handles_frame_boundaries() {
    frame_boundaries(true);
}

fn frame_boundaries(inline: bool) {
    use super::budget::Budget;
    use super::exchange::{Entrance, Family, ShardPlan};
    use super::reactor::{spawn_reactor, spawn_reactor_inline, ReactorCfg};
    use super::shard::{spawn_shard, ShardOpts};
    use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let mut chunks: Vec<(Vec<u8>, u64)> = Vec::new();
    // (a) 50 кадров одной записью, потом тишина: все должны быть выданы сразу
    let mut burst = Vec::new();
    for u in 1..=50 {
        burst.extend(ws_frame(1, true, bn_book(u).as_bytes()));
    }
    chunks.push((burst, 1500));
    // (b) один кадр по байту
    for b in ws_frame(1, true, bn_book(51).as_bytes()) {
        chunks.push((vec![b], 1));
    }
    // (c) большой кадр (сырой, 82 КБ) в ≈ 60 сегментах
    let big = format!(r#"{{"big":"{}"}}"#, "x".repeat(82_000));
    for part in ws_frame(1, true, big.as_bytes()).chunks(1400) {
        chunks.push((part.to_vec(), 0));
    }
    // (d) сообщение из двух фрагментов с пингом посередине
    let m = bn_book(52);
    let (m1, m2) = m.as_bytes().split_at(40);
    chunks.push((ws_frame(1, false, m1), 5));
    chunks.push((ws_frame(9, true, b"ping"), 5));
    chunks.push((ws_frame(0, true, m2), 5));
    // (e) заголовок кадра пополам: два байта и остаток
    let f = ws_frame(1, true, bn_book(53).as_bytes());
    chunks.push((f[..1].to_vec(), 5));
    chunks.push((f[1..].to_vec(), 300));
    let src = raw_source(listener, chunks);
    let dir = tempfile::tempdir().unwrap();
    let plan = ShardPlan {
        name: "book00".into(),
        endpoint: "public".into(),
        streams: vec!["btcusdt@bookTicker".into()],
    };
    let mut opts = ShardOpts::new(dir.path().to_path_buf(), 1_794_000_000);
    (opts.tls, opts.port, opts.flush) = (false, port, Duration::from_millis(30));
    let cfg = ReactorCfg {
        window: Duration::from_millis(1),
        kernel_stamp: true,
        pace_sub: true,
    };
    let (reactor, rj, inline_worker) = if inline {
        let (r, w, j) = spawn_reactor_inline(cfg).unwrap();
        opts.worker = Some(w.clone());
        (r, j, Some(w))
    } else {
        let (r, j) = spawn_reactor(cfg).unwrap();
        (r, j, None)
    };
    opts.reactor = Some(reactor.clone());
    opts.kernel_stamp = true;
    let exch: Arc<dyn Exchange> = Arc::new(Binance);
    let stop = Arc::new(AtomicBool::new(false));
    let budget = Arc::new(Budget::new(10, Duration::from_secs(300)));
    let ent = vec![vec![Entrance::new("127.0.0.1", Family::V4)]];
    let h = spawn_shard(&exch, &plan, &ent, &opts, &budget, &stop).unwrap();
    let metrics = Arc::clone(&h.metrics);
    // (a): 50 кадров дошли в пределах секунды, а источник ещё 1,5 с молчит
    let t0 = std::time::Instant::now();
    while metrics.writer().records < 50 && t0.elapsed() < Duration::from_secs(10) {
        std::thread::sleep(Duration::from_millis(10));
    }
    assert!(
        metrics.writer().records >= 50,
        "50 кадров одной записью выданы без новых событий сокета: {}",
        metrics.writer().records
    );
    assert!(
        t0.elapsed() < Duration::from_millis(1400),
        "выданы до конца тишины источника: {:?}",
        t0.elapsed()
    );
    let t1 = std::time::Instant::now();
    while metrics.writer().records < 53 && t1.elapsed() < Duration::from_secs(10) {
        std::thread::sleep(Duration::from_millis(20));
    }
    stop.store(true, Relaxed);
    h.join();
    if let Some(w) = inline_worker {
        w.quit();
    }
    reactor.quit();
    rj.join().unwrap();
    src.join().unwrap();
    let r = rows(dir.path());
    let us: Vec<i64> = r
        .iter()
        .filter_map(|d| match d {
            Decoded::Book { u, .. } => Some(*u),
            _ => None,
        })
        .collect();
    assert_eq!(
        us,
        (1..=53).collect::<Vec<i64>>(),
        "53 записи книги по порядку, без потерь и дублей"
    );
    let raws: Vec<&Decoded> = r
        .iter()
        .filter(|d| matches!(d, Decoded::Raw { .. }))
        .collect();
    assert!(
        raws.iter()
            .any(|d| matches!(d, Decoded::Raw { frame, .. } if frame == &big)),
        "большой кадр 82 КБ записан сырым целиком"
    );
    // ядерные метки: rx не убывает по файлу
    let rxs: Vec<i64> = r
        .iter()
        .map(|d| match d {
            Decoded::Book { rx_ns, .. }
            | Decoded::Trade { rx_ns, .. }
            | Decoded::Mark { rx_ns, .. }
            | Decoded::Ping { rx_ns, .. }
            | Decoded::Control { rx_ns, .. }
            | Decoded::Raw { rx_ns, .. } => *rx_ns,
        })
        .collect();
    assert!(rxs.windows(2).all(|w| w[0] <= w[1]), "rx не убывает");
}

fn tls_record(len: usize, fill: u8) -> Vec<u8> {
    let mut r = vec![
        0x17,
        0x03,
        0x03,
        u8::try_from(len >> 8).unwrap(),
        u8::try_from(len & 255).unwrap(),
    ];
    r.extend(std::iter::repeat_n(fill, len));
    r
}

#[test]
fn rxsock_exact_mode_hands_out_one_tls_record_per_read() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let mut peer = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
    peer.set_nodelay(true).unwrap();
    let (srv, _) = listener.accept().unwrap();
    let mut s = RxSock::new(srv);
    s.into_tick_mode(true).unwrap();
    // ключ меток ядра включается отложенно: прогреваем отдельным сокетом, живущим до конца проверки
    #[cfg(target_os = "linux")]
    let _warm = {
        let wl = TcpListener::bind("127.0.0.1:0").unwrap();
        let mut wpeer = TcpStream::connect(wl.local_addr().unwrap()).unwrap();
        let (wsrv, _) = wl.accept().unwrap();
        let mut ws = RxSock::new(wsrv);
        ws.into_tick_mode(false).unwrap();
        warm_kernel_stamps(&mut wpeer, &mut ws);
        (wpeer, ws)
    };
    // три записи в разных пакетах (паузы), затем неполная запись
    for (len, fill) in [(10usize, 1u8), (20, 2), (5, 3)] {
        peer.write_all(&tls_record(len, fill)).unwrap();
        std::thread::sleep(Duration::from_millis(8));
    }
    let partial = tls_record(30, 4);
    peer.write_all(&partial[..12]).unwrap();
    std::thread::sleep(Duration::from_millis(30));
    s.arm();
    let mut buf = [0u8; 200];
    let mut stamps = Vec::new();
    for (len, fill) in [(10usize, 1u8), (20, 2), (5, 3)] {
        let n = s.read(&mut buf).unwrap();
        assert_eq!(n, 5 + len, "ровно одна запись за вызов");
        assert_eq!(buf[5], fill);
        stamps.push(s.stamp_ns());
    }
    #[cfg(target_os = "linux")]
    {
        assert!(stamps.iter().all(|&t| t > 0), "метки пришли: {stamps:?}");
        // пакеты, накопившиеся в очереди нечитанными, ядро склеивает в один skb с меткой самого нового (`tcp_try_coalesce`): метки записей не убывают,
        // но у склеенных совпадают — точность метки ограничена этим (collector-perf, отчёт §4)
        assert!(
            stamps.windows(2).all(|w| w[0] <= w[1]),
            "метки не убывают: {stamps:?}"
        );
    }
    // неполная запись остаётся в ядре, до следующего такта
    assert_eq!(
        s.read(&mut buf).unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
    peer.write_all(&partial[12..]).unwrap();
    std::thread::sleep(Duration::from_millis(30));
    s.arm();
    let n = s.read(&mut buf).unwrap();
    assert_eq!(n, 35);
    assert_eq!(buf[5], 4);
    // одним чтением из ядра не считается: просмотр + снятие каждой записи
    assert!(s.syscalls() >= 6, "системных вызовов: {}", s.syscalls());
}
