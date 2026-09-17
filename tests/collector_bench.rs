//! Офлайн-бенч разбора коллектора (таск 24, критерий «замер до/после»).
//!
//! Не гейт — печатает числа, которые `docs/archive/findings/collector-2026-09-12.md`
//! кладёт в таблицу «до → после». Запуск руками, не из `cargo test`:
//!
//! ```bash
//! cargo test --release --test collector_bench -- --ignored --nocapture
//! ```
//!
//! Фикстуры — тексты по формату публичного потока Bybit v5 (порядок полей,
//! компактный JSON без пробелов, строковые цена/размер, `L`/`i`/`BT` у
//! сделки — как в `src/bybit/ws.rs` и документации площадки): снапшот
//! `orderbook.50` на 50+50 уровней, дельта на 1–5 уровней на сторону,
//! `publicTrade` на 1–10 сделок. Для каждого вида — медиана и p99
//! наносекунд на сообщение (перцентиль «ближайший ранг», тот же, что
//! `bybit::probe`) и аллокаций на сообщение по `alloc_count::measure`
//! (среднее по прогретой серии — счётчик детерминирован, среднее нужно
//! только потому, что варианты фикстуры отличаются числом уровней).
//!
//! Разрешение `Instant` на Windows — 100 нс (QueryPerformanceCounter,
//! 10 МГц): у дельт порядка микросекунды это ~10% на одиночный замер,
//! медиана по сотням тысяч замеров от этого не страдает, p99 — не хуже
//! разрешения. Сам бенч живёт в интеграционных тестах, а не в `ws.rs`,
//! чтобы один и тот же файл без правок запускался на коде `HEAD` («до») и
//! на коде таска («после») — иначе замеры «до» и «после» шли бы разным
//! кодом бенча.

use std::time::Instant;

use alpha::alloc_count;
use alpha::bybit::ws::{parse_message, parse_message_into, Event};

/// Детерминированный генератор (SplitMix64) — чтобы «до» и «после» получили
/// байт в байт одинаковые тексты.
struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }
    fn below(&mut self, n: u64) -> u64 {
        self.next() % n
    }
}

/// Цена в сотых (два знака после точки, как у SOLUSDT), размер — до трёх
/// знаков; обе — строки, как отдаёт площадка.
fn price(cents: u64) -> String {
    format!("{}.{:02}", cents / 100, cents % 100)
}

fn size(rng: &mut Rng) -> String {
    let milli = 1 + rng.below(50_000);
    format!("{}.{:03}", milli / 1000, milli % 1000)
}

fn levels(rng: &mut Rng, start_cents: u64, n: usize, ascending: bool) -> String {
    let mut out = String::from("[");
    for i in 0..n {
        if i > 0 {
            out.push(',');
        }
        let cents = if ascending {
            start_cents + i as u64
        } else {
            start_cents - i as u64
        };
        out.push_str(&format!("[\"{}\",\"{}\"]", price(cents), size(rng)));
    }
    out.push(']');
    out
}

fn snapshot(rng: &mut Rng, u: u64) -> String {
    let ts = 1_757_600_000_000u64 + u;
    format!(
        "{{\"topic\":\"orderbook.50.SOLUSDT\",\"type\":\"snapshot\",\"ts\":{ts},\
         \"data\":{{\"s\":\"SOLUSDT\",\"b\":{},\"a\":{},\"u\":{u},\"seq\":{}}},\"cts\":{}}}",
        levels(rng, 15_000, 50, false),
        levels(rng, 15_001, 50, true),
        u * 7,
        ts - 2
    )
}

fn delta(rng: &mut Rng, u: u64) -> String {
    let ts = 1_757_600_000_000u64 + u;
    let nb = 1 + rng.below(5) as usize;
    let na = 1 + rng.below(5) as usize;
    let bid_start = 15_000 - rng.below(20);
    let ask_start = 15_001 + rng.below(20);
    let bids = levels(rng, bid_start, nb, false);
    let asks = levels(rng, ask_start, na, true);
    format!(
        "{{\"topic\":\"orderbook.50.SOLUSDT\",\"type\":\"delta\",\"ts\":{ts},\
         \"data\":{{\"s\":\"SOLUSDT\",\"b\":{bids},\"a\":{asks},\"u\":{u},\"seq\":{}}},\"cts\":{}}}",
        u * 7,
        ts - 1
    )
}

fn trades(rng: &mut Rng, u: u64) -> String {
    let ts = 1_757_600_000_000u64 + u;
    let n = 1 + rng.below(10) as usize;
    let mut items = String::new();
    for i in 0..n {
        if i > 0 {
            items.push(',');
        }
        let side = if rng.below(2) == 0 { "Buy" } else { "Sell" };
        let tick = if side == "Buy" {
            "PlusTick"
        } else {
            "MinusTick"
        };
        items.push_str(&format!(
            "{{\"T\":{},\"s\":\"SOLUSDT\",\"S\":\"{side}\",\"v\":\"{}\",\"p\":\"{}\",\
             \"L\":\"{tick}\",\"i\":\"{:08x}-{:04x}-5b31-9112-a178eb6023af\",\"BT\":false}}",
            ts - 3,
            size(rng),
            price(15_000 + rng.below(10)),
            rng.next() as u32,
            rng.next() as u16
        ));
    }
    format!(
        "{{\"topic\":\"publicTrade.SOLUSDT\",\"type\":\"snapshot\",\"ts\":{ts},\"data\":[{items}]}}"
    )
}

/// Перцентиль «ближайший ранг» на отсортированном ряду — `rank = ceil(p/100 · n)`,
/// 1-based; тот же метод, что `bybit::probe::percentile_of_sorted`.
fn percentile(sorted: &[u64], p: u64) -> u64 {
    let n = sorted.len() as u64;
    let rank = (p * n).div_ceil(100).max(1);
    sorted[(rank - 1) as usize]
}

struct BenchRow {
    kind: &'static str,
    n: usize,
    median_ns: u64,
    p99_ns: u64,
    allocs_per_msg: f64,
    bytes_per_msg: f64,
    msg_bytes_avg: f64,
}

/// `Fresh` — `parse_message` (свежий `Vec<Event>` на вызов, единственный
/// вход до таска 24); `Reuse` — `parse_message_into` с одним буфером на всю
/// серию, как в `bybit::conn::Connection::run` после таска 24.
#[derive(Clone, Copy)]
enum Mode {
    Fresh,
    Reuse,
}

fn bench_kind(kind: &'static str, msgs: &[String], iters: usize, mode: Mode) -> BenchRow {
    let mut out: Vec<Event> = Vec::new();
    let mut parse_once = |m: &str| match mode {
        Mode::Fresh => {
            let evs = parse_message(m).unwrap();
            std::hint::black_box(&evs);
        }
        Mode::Reuse => {
            parse_message_into(m, &mut out).unwrap();
            std::hint::black_box(&out);
        }
    };
    // Прогрев: аллокатор и кэши инструкций — те же условия, что у живого
    // сокета после первых секунд.
    for m in msgs.iter().take(1_000) {
        parse_once(m);
    }
    let mut ns = Vec::with_capacity(iters);
    for i in 0..iters {
        let m = &msgs[i % msgs.len()];
        let t0 = Instant::now();
        parse_once(m);
        ns.push(t0.elapsed().as_nanos() as u64);
    }
    ns.sort_unstable();

    let mut allocs = 0u64;
    let mut bytes = 0u64;
    let probe = msgs.len().min(4_096);
    for m in msgs.iter().take(probe) {
        let (_, counts) = alloc_count::measure(|| parse_once(m));
        allocs += counts.allocations;
        bytes += counts.bytes;
    }
    assert!(!out.is_empty() || matches!(mode, Mode::Fresh));
    let msg_bytes: usize = msgs.iter().map(String::len).sum();
    BenchRow {
        kind,
        n: iters,
        median_ns: percentile(&ns, 50),
        p99_ns: percentile(&ns, 99),
        allocs_per_msg: allocs as f64 / probe as f64,
        bytes_per_msg: bytes as f64 / probe as f64,
        msg_bytes_avg: msg_bytes as f64 / msgs.len() as f64,
    }
}

fn build_fixtures() -> (Vec<String>, Vec<String>, Vec<String>) {
    let mut rng = Rng(0x5EED_2026_0912);
    let snapshots: Vec<String> = (0..256).map(|i| snapshot(&mut rng, 1 + i)).collect();
    let deltas: Vec<String> = (0..4_096).map(|i| delta(&mut rng, 1_000 + i)).collect();
    let trades: Vec<String> = (0..4_096).map(|i| trades(&mut rng, 10_000 + i)).collect();
    (snapshots, deltas, trades)
}

#[test]
#[ignore = "бенч, не гейт: запускать руками с --ignored --nocapture"]
fn parse_message_bench() {
    let (snapshots, deltas, trades) = build_fixtures();
    let rows = [
        bench_kind("snapshot_50x50", &snapshots, 50_000, Mode::Fresh),
        bench_kind("delta_1..5", &deltas, 500_000, Mode::Fresh),
        bench_kind("trades_1..10", &trades, 200_000, Mode::Fresh),
        bench_kind("snapshot_50x50/into", &snapshots, 50_000, Mode::Reuse),
        bench_kind("delta_1..5/into", &deltas, 500_000, Mode::Reuse),
        bench_kind("trades_1..10/into", &trades, 200_000, Mode::Reuse),
    ];
    println!("collector_bench parse_message (Fresh) / parse_message_into (into)");
    println!("kind\tn\tmsg_bytes_avg\tmedian_ns\tp99_ns\tallocs_per_msg\talloc_bytes_per_msg");
    for r in &rows {
        println!(
            "{}\t{}\t{:.0}\t{}\t{}\t{:.2}\t{:.0}",
            r.kind, r.n, r.msg_bytes_avg, r.median_ns, r.p99_ns, r.allocs_per_msg, r.bytes_per_msg
        );
    }
}

/// Таск 25: уровень zstd — по замеру, не по умолчанию библиотеки. Одна и та
/// же реальная пятиминутная запись (`data/collector/*-after/`, таск 24)
/// перекодируется кадрами по `FRAME_TARGET_RECORDS` на уровнях 1/3/6/9:
/// байт на запись и наносекунд сжатия на запись. Без `data/` — пропуск с
/// сообщением (данные не коммитятся).
/// `cargo test --release --test collector_bench zstd_level -- --ignored --nocapture`
#[test]
#[ignore]
fn zstd_level_bytes_per_record_and_cpu_on_a_real_binlog() {
    use alpha::binlog::{Reader, Writer};
    use alpha::commands::record::FRAME_TARGET_RECORDS;
    let Some(dir) = std::fs::read_dir("data/collector")
        .ok()
        .into_iter()
        .flatten()
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .find(|p| p.to_string_lossy().ends_with("-after"))
    else {
        eprintln!("zstd bench: нет data/collector/*-after — пропуск");
        return;
    };
    let mut files: Vec<_> = std::fs::read_dir(&dir)
        .unwrap()
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|x| x == "binlog"))
        .collect();
    files.sort();
    let mut total_records = 0usize;
    let mut per_level: [(i32, usize, u128); 4] = [(1, 0, 0), (3, 0, 0), (6, 0, 0), (9, 0, 0)];
    for path in &files {
        let data = std::fs::read(path).unwrap();
        let mut reader = Reader::open(&data[..]).unwrap();
        let header = reader.header();
        let mut records = Vec::new();
        while let Some(frame) = reader.read_frame().unwrap() {
            records.extend(frame);
        }
        total_records += records.len();
        for slot in per_level.iter_mut() {
            let mut w = Writer::create(Vec::new(), header, slot.0).unwrap();
            let t0 = std::time::Instant::now();
            for chunk in records.chunks(FRAME_TARGET_RECORDS) {
                w.write_frame(chunk).unwrap();
            }
            slot.2 += t0.elapsed().as_nanos();
            slot.1 += w.into_inner().len();
        }
    }
    eprintln!(
        "zstd bench: {} файлов, {} записей из {}",
        files.len(),
        total_records,
        dir.display()
    );
    for (level, bytes, ns) in per_level {
        eprintln!(
            "zstd level {level}: {:.3} байт/запись, {:.1} нс/запись сжатия, {bytes} байт",
            bytes as f64 / total_records.max(1) as f64,
            ns as f64 / total_records.max(1) as f64
        );
    }
}
