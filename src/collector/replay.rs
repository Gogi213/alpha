//! Повтор записанных кадров (G-022 collector-perf): мерка «одинаковые условия» для пути писателя.
//!
//! Вход — `capture-<шард>.tsv` (`ALPHA_COLLECT_CAPTURE=1`): строка `<сессия>\t<приём нс>\t<кадр>` на кадр шарда до снятия дублей.
//! Кадры идут в настоящую очередь → `ShardWriter` (разбор, дубли, кодировщик) → настоящий поток диска (zstd, файл суток, `.ok`);
//! часы писателя — по приёму последнего положенного кадра, блок режется только по размеру (`flush` = час): два прогона одного входа
//! дают байт в байт одни и те же файлы. Мера — CPU потоков писателя и диска на миллион кадров (`getrusage` потока, не стена) и хеш
//! разжатого содержимого (для сравнения старого и нового бинаря). Копия кадра делается в потоке подачи (как выделение в потоке приёма),
//! освобождается в писателе — как в бою.

// Диагностический инструмент (не горячий путь): счётчики и время в f64 для печати.
#![allow(clippy::cast_precision_loss)]

use std::hash::Hasher as _;
use std::io::{Read as _, Write as _};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicI64, Ordering::Relaxed};
use std::sync::Arc;
use std::time::{Duration, Instant};

use bytes::Bytes;
use clap::Args;
use flate2::{write::GzEncoder, Compression};

use crate::alloc_count;

use super::codec::Rec;
use super::events::now_ms;
use super::exchange::{Exchange, Parsed, Recs};
use super::gzip::Gunzip;
use super::hash::Fnv;
use super::metrics::ShardMetrics;
use super::net::{gettid, thread_cpu_s};
use super::ring::{ring, Item, Payload};
use super::runner::{exchange_of, ExchangeName};
use super::sink::{run_sink, sink_channel};
use super::writer::{ShardWriter, WriterCfg};

static VCLOCK: AtomicI64 = AtomicI64::new(0);

fn vclock() -> i64 {
    VCLOCK.load(Relaxed)
}

#[derive(Debug, Args)]
pub struct ReplayArgs {
    #[arg(long, value_enum)]
    pub exchange: ExchangeName,
    /// `capture-<шард>.tsv`.
    #[arg(long)]
    pub capture: PathBuf,
    /// Корень вывода: проход k пишет в `<out>/pass<k>/`.
    #[arg(long)]
    pub out: PathBuf,
    #[arg(long, default_value_t = 3)]
    pub repeat: usize,
    #[arg(long, default_value_t = 1)]
    pub run_id: i64,
    #[arg(long, default_value_t = 1 << 15)]
    pub ring_cap: usize,
    /// Раз в проход замерить и один разбор (один поток, без дублей, кодировщика, zstd).
    #[arg(long)]
    pub parse_only: bool,
    /// Замер разжатия: каждый кадр сжимается gzip (уровень 6), время — `Gunzip::unpack`.
    #[arg(long)]
    pub gunzip: bool,
}

/// Кадры в одном буфере: (сессия, приём нс, начало, длина).
struct Capture {
    arena: Vec<u8>,
    idx: Vec<(u32, i64, usize, usize)>,
}

impl Capture {
    fn load(path: &Path) -> anyhow::Result<Self> {
        let buf = std::fs::read(path)?;
        let mut idx = Vec::new();
        let mut arena = Vec::with_capacity(buf.len());
        for line in buf.split(|&b| b == b'\n') {
            let mut it = line.splitn(3, |&b| b == b'\t');
            let (Some(s), Some(r), Some(f)) = (it.next(), it.next(), it.next()) else {
                continue;
            };
            let (Ok(s), Ok(r)) = (
                std::str::from_utf8(s)?.parse::<u32>(),
                std::str::from_utf8(r)?.parse::<i64>(),
            ) else {
                continue;
            };
            idx.push((s, r, arena.len(), f.len()));
            arena.extend_from_slice(f);
        }
        Ok(Self { arena, idx })
    }

    fn frame(&self, i: usize) -> Option<(u32, i64, &[u8])> {
        let &(s, r, o, l) = self.idx.get(i)?;
        Some((s, r, self.arena.get(o..o + l)?))
    }

    fn frames(&self) -> impl Iterator<Item = (u32, i64, &[u8])> {
        (0..self.idx.len()).filter_map(|i| self.frame(i))
    }
}

struct Pass {
    wall: Duration,
    wr_cpu: f64,
    sk_cpu: f64,
    wr_allocs: u64,
    sk_allocs: u64,
    records: u64,
    dups: u64,
    raw: u64,
    rows: u64,
    bytes_out: u64,
    content: (u64, u64),
}

/// Хеш и длина разжатого содержимого всех файлов суток шарда (в порядке имён).
fn content_hash(root: &Path) -> (u64, u64) {
    let mut files: Vec<PathBuf> = Vec::new();
    if let Ok(days) = std::fs::read_dir(root) {
        for d in days.flatten() {
            if let Ok(fs) = std::fs::read_dir(d.path()) {
                files.extend(
                    fs.flatten()
                        .map(|f| f.path())
                        .filter(|p| p.to_string_lossy().ends_with(".tsv.zst")),
                );
            }
        }
    }
    files.sort();
    let mut h = Fnv::default();
    let mut total = 0u64;
    let mut buf = vec![0u8; 1 << 20];
    for f in files {
        let Ok(file) = std::fs::File::open(&f) else {
            continue;
        };
        let Ok(mut dec) = zstd::stream::read::Decoder::new(file) else {
            continue;
        };
        while let Ok(n) = dec.read(&mut buf) {
            if n == 0 {
                break;
            }
            if let Some(chunk) = buf.get(..n) {
                h.write(chunk);
            }
            total += u64::try_from(n).unwrap_or(0);
        }
    }
    (h.finish(), total)
}

fn pass(exch: &Arc<dyn Exchange>, cap: &Capture, shard: &str, root: &Path, a: &ReplayArgs) -> Pass {
    let metrics = ShardMetrics::new(shard, now_ms(), a.ring_cap, 64);
    let (rtx, rrx) = ring(&metrics.ring);
    let (stx, srx) = sink_channel(&metrics.sink_q);
    let cfg = WriterCfg {
        root: root.to_path_buf(),
        shard: shard.to_string(),
        run_id: a.run_id,
        flush: Duration::from_secs(3600),
        block_max: 256 * 1024,
        clock: vclock,
    };
    let writer = ShardWriter::new(cfg, Arc::clone(exch), Arc::clone(&metrics), stx, rrx);
    let (sk_depth, sk_m) = (Arc::clone(&metrics.sink_q), Arc::clone(&metrics));
    let sk = std::thread::spawn(move || {
        let ((), c) = alloc_count::measure(|| run_sink(&srx, &sk_depth, &sk_m));
        (thread_cpu_s(gettid()).unwrap_or(0.0), c.allocations)
    });
    let wr = std::thread::spawn(move || {
        let ((), c) = alloc_count::measure(|| writer.run());
        (thread_cpu_s(gettid()).unwrap_or(0.0), c.allocations)
    });
    let t0 = Instant::now();
    for (sender, rx_ns, data) in cap.frames() {
        VCLOCK.store(rx_ns, Relaxed);
        let _ = rtx.push(Item {
            rx_ns,
            sender,
            settled: true,
            payload: Payload::Frame(Bytes::copy_from_slice(data)),
        });
    }
    let _ = rtx.push(Item {
        rx_ns: cap.idx.last().map_or(0, |x| x.1),
        sender: 0,
        settled: true,
        payload: Payload::Shutdown,
    });
    let (wr_cpu, wr_allocs) = wr.join().unwrap_or((0.0, 0));
    let (sk_cpu, sk_allocs) = sk.join().unwrap_or((0.0, 0));
    let wall = t0.elapsed();
    let w = metrics.writer();
    let (records, dups, raw, rows) = (w.records, w.dups, w.raw, w.rows);
    drop(w);
    let bytes_out = metrics.sink().bytes_out;
    Pass {
        wall,
        wr_cpu,
        sk_cpu,
        wr_allocs,
        sk_allocs,
        records,
        dups,
        raw,
        rows,
        bytes_out,
        content: content_hash(root),
    }
}

fn parse_only(exch: &Arc<dyn Exchange>, cap: &Capture) {
    let mut p = exch.new_parser();
    let (mut recs, mut raws, mut sink) = (0u64, 0u64, 0i64);
    let t = Instant::now();
    let ((), c) = alloc_count::measure(|| {
        for (_, _, data) in cap.frames() {
            let r = p.parse(
                data,
                &mut Recs(|r: &Rec<'_>| {
                    recs += 1;
                    sink = sink.wrapping_add(r.ident());
                }),
            );
            if r == Parsed::Raw {
                raws += 1;
            }
        }
    });
    let el = t.elapsed().as_secs_f64();
    let n = cap.idx.len().max(1) as f64;
    println!(
        "  parse only: {:.1} ns/frame ({:.2} Mframe/s per core), allocs {:.4}/frame, records {recs}, raw frames {raws} (check {sink})",
        el * 1e9 / n,
        n / el / 1e6,
        c.allocations as f64 / n
    );
}

fn gunzip_bench(cap: &Capture) {
    let gz: Vec<Vec<u8>> = cap
        .frames()
        .map(|(_, _, d)| {
            let mut e = GzEncoder::new(Vec::new(), Compression::new(6));
            let _ = e.write_all(d);
            e.finish().unwrap_or_default()
        })
        .collect();
    let (plain, packed): (usize, usize) = (
        cap.frames().map(|f| f.2.len()).sum(),
        gz.iter().map(Vec::len).sum(),
    );
    let mut g = Gunzip::new();
    let (mut ok, mut sink) = (0u64, 0usize);
    let t = Instant::now();
    for f in &gz {
        if let Some(out) = g.unpack(f) {
            ok += 1;
            sink = sink.wrapping_add(out.len());
        }
    }
    let el = t.elapsed().as_secs_f64();
    let n = gz.len().max(1) as f64;
    println!(
        "  gunzip (miniz_oxide via flate2): {:.0} ns/frame, {ok}/{} unpacked, plain {plain} B, gzip {packed} B (check {sink})",
        el * 1e9 / n,
        gz.len()
    );
}

pub fn run(a: &ReplayArgs) -> anyhow::Result<()> {
    let exch = exchange_of(a.exchange);
    let shard = a
        .capture
        .file_stem()
        .and_then(|s| s.to_str())
        .map_or("shard", |s| s.strip_prefix("capture-").unwrap_or(s))
        .to_string();
    let cap = Capture::load(&a.capture)?;
    let n = cap.idx.len();
    println!(
        "replay {} shard {shard}: {n} frames, {} MB, sessions {}",
        exch.name(),
        cap.arena.len() / 1_000_000,
        cap.idx
            .iter()
            .map(|x| x.0)
            .collect::<std::collections::BTreeSet<_>>()
            .len()
    );
    if a.parse_only {
        for _ in 0..a.repeat {
            parse_only(&exch, &cap);
        }
    }
    if a.gunzip {
        for _ in 0..a.repeat {
            gunzip_bench(&cap);
        }
    }
    let mut per_m = Vec::new();
    for k in 0..a.repeat {
        let root = a.out.join(format!("pass{k}"));
        let _ = std::fs::remove_dir_all(&root);
        let r = pass(&exch, &cap, &shard, &root, a);
        let cpu = r.wr_cpu + r.sk_cpu;
        let per = cpu / n.max(1) as f64 * 1e6;
        per_m.push(per);
        println!(
            "pass {k}: wall {:.2} s, CPU wr {:.2} s + sk {:.2} s = {:.2} s => {:.2} CPU-s per Mframe (wr {:.2}, sk {:.2}); records {} dups {} raw {} rows {}; allocs wr {:.3}/frame sk {:.4}/frame; zst {} B; decompressed {} B fnv {:016x}",
            r.wall.as_secs_f64(),
            r.wr_cpu,
            r.sk_cpu,
            cpu,
            per,
            r.wr_cpu / n.max(1) as f64 * 1e6,
            r.sk_cpu / n.max(1) as f64 * 1e6,
            r.records,
            r.dups,
            r.raw,
            r.rows,
            r.wr_allocs as f64 / n.max(1) as f64,
            r.sk_allocs as f64 / n.max(1) as f64,
            r.bytes_out,
            r.content.1,
            r.content.0
        );
    }
    per_m.sort_by(f64::total_cmp);
    if let Some(m) = per_m.get(per_m.len() / 2) {
        println!("median {m:.2} CPU-s per Mframe over {} passes", per_m.len());
    }
    Ok(())
}
