//! Стенд разжатия gzip-кадров BingX (TK-580): декодеры на записанных настоящих кадрах биржи (`tools/collector-perf/capture_gz.py`).
//!
//! Мера — наносекунды на кадр (медиана и хвост) при трёх режимах: горячий (кадры подряд), «загрязнённый» (между кадрами трогается `--pollute-kb` памяти:
//! так кэши выглядят в реакторе, где между кадрами идут TLS, разбор, ядро) и под фоновой нагрузкой (`--bg-threads` потоков на соседних ядрах гоняют память
//! и арифметику, как чужие счётные задачи на хосте). Все декодеры обязаны давать те же байты (хеш всех выходов).

// Диагностический инструмент (не горячий путь): счётчики и время в f64 для печати.
#![allow(clippy::cast_precision_loss)]

use std::hash::Hasher as _;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
use std::sync::Arc;
use std::time::Instant;

use clap::Args;

use super::gzip::Gunzip;
use super::hash::Fnv;
use super::inflate::Tiny;

#[derive(Debug, Args)]
pub struct GzBenchArgs {
    /// Файл кадров `capture_gz.py` (запись: u32 длина, u8 вид, f64 время, кадр).
    #[arg(long)]
    pub file: PathBuf,
    /// Проходов по набору на декодер (первый — прогрев и сверка выходов, не в счёте).
    #[arg(long, default_value_t = 4)]
    pub rounds: usize,
    /// Память (КБ), которую трогает поток между кадрами: вытеснение кэшей работой реактора вокруг разжатия.
    #[arg(long, default_value_t = 0)]
    pub pollute_kb: usize,
    /// Ядро стенда (по умолчанию не закреплять); фоновые потоки идут на следующие ядра.
    #[arg(long)]
    pub cpu: Option<usize>,
    /// Скважность как у реактора: после каждых `duty` мкс работы поток спит `sleep` мкс (0 — без сна).
    #[arg(long, default_value_t = 0)]
    pub duty_us: u64,
    #[arg(long, default_value_t = 6000)]
    pub sleep_us: u64,
    /// Фоновые потоки нагрузки на других ядрах (память + арифметика).
    #[arg(long, default_value_t = 0)]
    pub bg_threads: usize,
    /// Декодеры через запятую: libdeflate, miniz, tiny, gunzip (боевой: tiny, затем libdeflate).
    #[arg(long, default_value = "libdeflate,miniz,tiny,gunzip")]
    pub decoders: String,
}

/// Кадр из файла: вид (0 book, 1 trade, 2 mark) и байты как пришли.
struct Frame {
    kind: u8,
    bytes: Vec<u8>,
}

fn load(path: &PathBuf) -> std::io::Result<Vec<Frame>> {
    let raw = std::fs::read(path)?;
    let mut out = Vec::new();
    let mut at = 0usize;
    while let Some(h) = raw.get(at..at + 13) {
        let len = h
            .get(..4)
            .and_then(|b| b.try_into().ok())
            .map_or(0, u32::from_le_bytes) as usize;
        let kind = h.get(4).copied().unwrap_or(3);
        let Some(body) = raw.get(at + 13..at + 13 + len) else {
            break;
        };
        out.push(Frame {
            kind,
            bytes: body.to_vec(),
        });
        at += 13 + len;
    }
    Ok(out)
}

/// Разжатие одного кадра: выход живёт в буфере декодера до следующего вызова.
trait Dec {
    fn name(&self) -> &'static str;
    fn unpack(&mut self, frame: &[u8]) -> Option<&[u8]>;
}

struct LibDeflate(Gunzip);

impl Dec for LibDeflate {
    fn name(&self) -> &'static str {
        "libdeflate"
    }

    fn unpack(&mut self, frame: &[u8]) -> Option<&[u8]> {
        self.0.unpack(frame)
    }
}

/// Только быстрый путь (кадр, который он не берёт, — ошибка стенда).
struct TinyOnly {
    t: Box<Tiny>,
    buf: Vec<u8>,
}

impl Dec for TinyOnly {
    fn name(&self) -> &'static str {
        "tiny"
    }

    fn unpack(&mut self, frame: &[u8]) -> Option<&[u8]> {
        let n = self.t.unpack(frame, &mut self.buf)?;
        self.buf.get(..n)
    }
}

/// Боевой `Gunzip`: быстрый путь, затем libdeflate.
struct Both(Gunzip);

impl Dec for Both {
    fn name(&self) -> &'static str {
        "gunzip"
    }

    fn unpack(&mut self, frame: &[u8]) -> Option<&[u8]> {
        self.0.unpack(frame)
    }
}

/// Тело deflate и (crc, длина) из gzip-кадра без необязательных полей и с ними (RFC 1952).
fn gzip_body(frame: &[u8]) -> Option<(&[u8], u32)> {
    let flg = *frame.get(3)?;
    let mut pos = 10usize;
    if flg & 4 != 0 {
        let xlen = usize::from(u16::from_le_bytes(
            frame.get(pos..pos + 2)?.try_into().ok()?,
        ));
        pos += 2 + xlen;
    }
    for bit in [8u8, 16] {
        if flg & bit != 0 {
            pos += frame.get(pos..)?.iter().position(|&b| b == 0)? + 1;
        }
    }
    if flg & 2 != 0 {
        pos += 2;
    }
    let end = frame.len().checked_sub(8)?;
    let size = u32::from_le_bytes(frame.get(end + 4..)?.try_into().ok()?);
    Some((frame.get(pos..end)?, size))
}

struct Miniz {
    d: flate2::Decompress,
    buf: Vec<u8>,
}

impl Dec for Miniz {
    fn name(&self) -> &'static str {
        "miniz"
    }

    fn unpack(&mut self, frame: &[u8]) -> Option<&[u8]> {
        let (body, size) = gzip_body(frame)?;
        let size = size as usize;
        self.d.reset(false);
        self.buf.clear();
        self.buf.resize(size.max(1), 0);
        let st = self
            .d
            .decompress(body, &mut self.buf, flate2::FlushDecompress::Finish)
            .ok()?;
        (st == flate2::Status::StreamEnd && usize::try_from(self.d.total_out()).ok() == Some(size))
            .then(|| self.buf.get(..size))
            .flatten()
    }
}

/// Процентиль в промилле (500 — медиана).
fn pct(v: &[u32], permille: usize) -> u32 {
    let i = (v.len() * permille / 1000).min(v.len().saturating_sub(1));
    v.get(i).copied().unwrap_or(0)
}

/// Фоновая нагрузка: каждый поток — случайные чтения по 64 МБ и арифметика (как чужой счёт на соседнем ядре).
fn spawn_bg(n: usize, base: usize, stop: &Arc<AtomicBool>) -> Vec<std::thread::JoinHandle<()>> {
    (0..n)
        .map(|i| {
            let stop = Arc::clone(stop);
            std::thread::spawn(move || {
                pin(base + i + 1);
                let mut mem = vec![1u64; 8 << 20];
                let (mut x, mut acc) = (0x9e37_79b9_7f4a_7c15u64 ^ i as u64, 0u64);
                while !stop.load(Relaxed) {
                    for _ in 0..4096 {
                        x ^= x << 13;
                        x ^= x >> 7;
                        x ^= x << 17;
                        let j = usize::try_from(x % (mem.len() as u64)).unwrap_or(0);
                        if let Some(c) = mem.get_mut(j) {
                            acc = acc.wrapping_add(*c).rotate_left(5);
                            *c = acc;
                        }
                    }
                }
                std::hint::black_box(acc);
            })
        })
        .collect()
}

fn pin(cpu: usize) {
    // SAFETY: `set` — локальная нулевая маска; `sched_setaffinity(0, …)` относится к текущему потоку, ошибка (нет такого ядра) безвредна.
    unsafe {
        let mut set: libc::cpu_set_t = std::mem::zeroed();
        libc::CPU_SET(cpu, &mut set);
        libc::sched_setaffinity(0, std::mem::size_of::<libc::cpu_set_t>(), &set);
    }
}

pub fn run(args: &GzBenchArgs) -> anyhow::Result<()> {
    let frames = load(&args.file)?;
    let data: Vec<&Frame> = frames.iter().filter(|f| f.kind < 3).collect();
    println!(
        "кадров {} (book {}, trade {}, mark {}), сжато {:.0} Б в среднем",
        data.len(),
        data.iter().filter(|f| f.kind == 0).count(),
        data.iter().filter(|f| f.kind == 1).count(),
        data.iter().filter(|f| f.kind == 2).count(),
        data.iter().map(|f| f.bytes.len()).sum::<usize>() as f64 / data.len().max(1) as f64
    );
    println!(
        "режим: pollute {} КБ, фон {} потоков; ядро стенда {:?}",
        args.pollute_kb, args.bg_threads, args.cpu
    );
    if let Some(c) = args.cpu {
        pin(c);
    }
    let stop = Arc::new(AtomicBool::new(false));
    let bg = spawn_bg(args.bg_threads, args.cpu.unwrap_or(0), &stop);
    let mut dirty = vec![0u8; args.pollute_kb * 1024 + 64];
    let mut decs: Vec<Box<dyn Dec>> = Vec::new();
    for name in args.decoders.split(',') {
        decs.push(match name.trim() {
            "libdeflate" => Box::new(LibDeflate(Gunzip::libdeflate_only())),
            "tiny" => Box::new(TinyOnly {
                t: Tiny::new(),
                buf: Vec::new(),
            }),
            "gunzip" => Box::new(Both(Gunzip::new())),
            "miniz" => Box::new(Miniz {
                d: flate2::Decompress::new(false),
                buf: Vec::new(),
            }),
            other => anyhow::bail!("неизвестный декодер {other}"),
        });
    }
    // кадр за кадром все декодеры подряд (порядок сдвигается с каждым кадром): условия хоста у них в каждый момент одни и те же
    let nd = decs.len();
    let mut times: Vec<[Vec<u32>; 3]> = (0..nd).map(|_| Default::default()).collect();
    let mut hashes: Vec<Fnv> = (0..nd).map(|_| Fnv::default()).collect();
    let mut sink = 0u64;
    let mut worked_ns = 0u64;
    for round in 0..args.rounds.max(2) {
        for (fi, f) in data.iter().enumerate() {
            for step in 0..nd {
                let di = (fi + step) % nd;
                for b in dirty.iter_mut().step_by(64) {
                    *b = b.wrapping_add(1);
                }
                let Some(dec) = decs.get_mut(di) else {
                    continue;
                };
                let t = Instant::now();
                let out = dec.unpack(&f.bytes);
                let ns = u32::try_from(t.elapsed().as_nanos()).unwrap_or(u32::MAX);
                worked_ns += u64::from(ns);
                if args.duty_us > 0 && worked_ns >= args.duty_us * 1000 {
                    std::thread::sleep(std::time::Duration::from_micros(args.sleep_us));
                    worked_ns = 0;
                }
                let Some(out) = out else {
                    anyhow::bail!("{}: кадр не разжат", dec.name());
                };
                sink = sink.wrapping_add(out.len() as u64);
                if round == 0 {
                    if let Some(h) = hashes.get_mut(di) {
                        h.write(out);
                    }
                } else if let Some(v) = times
                    .get_mut(di)
                    .and_then(|t| t.get_mut(usize::from(f.kind)))
                {
                    v.push(ns);
                }
            }
        }
        if round == 0 {
            let first = hashes.first().map(Fnv::finish);
            if hashes.iter().any(|h| Some(h.finish()) != first) {
                anyhow::bail!("выходы декодеров различаются");
            }
        }
    }
    std::hint::black_box(sink);
    for (dec, tk) in decs.iter().zip(times.iter_mut()) {
        let mut all: Vec<u32> = tk.iter().flatten().copied().collect();
        all.sort_unstable();
        let mean = all.iter().map(|&x| f64::from(x)).sum::<f64>() / all.len().max(1) as f64;
        print!(
            "{:11} все: средн {:7.0} p50 {:6} p90 {:6} p99 {:6} нс",
            dec.name(),
            mean,
            pct(&all, 500),
            pct(&all, 900),
            pct(&all, 990)
        );
        for (k, label) in [(0usize, "book"), (1, "trade"), (2, "mark")] {
            if let Some(v) = tk.get_mut(k) {
                v.sort_unstable();
                print!(" | {label} p50 {:6}", pct(v, 500));
            }
        }
        println!();
    }
    stop.store(true, Relaxed);
    for j in bg {
        let _ = j.join();
    }
    Ok(())
}
