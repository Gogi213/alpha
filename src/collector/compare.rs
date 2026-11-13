//! Сравнение двух каталогов вывода коллектора (G6 «бок о бок», collector-perf): мультимножества записей без `rx` и распределение `rx_b − rx_a`.
//!
//! Личность записи — (вид, символ, личность биржи: book `u`, trade `a`, mark `E`); у совпавших личностей сравнивается содержимое (хеш полей данных, без `rx`).
//! Края окна (по 10 с с обеих сторон пересечения времён двух выводов) не считаются: процессы стартуют и останавливаются не в одну секунду.
//! Читает и незакрытые файлы суток (чтение до конца дописанного).

// Диагностический инструмент (не горячий путь): отношения и проценты в f64 для печати.
#![allow(clippy::cast_precision_loss)]

use std::collections::HashMap;
use std::hash::Hasher as _;
use std::io::{BufRead as _, BufReader};
use std::path::{Path, PathBuf};

use clap::Args;

use super::codec::{Decoded, Decoder};
use super::hash::Fnv;

#[derive(Debug, Args)]
pub struct CompareArgs {
    /// Каталог вывода A (корень данных: `<день>/<шард>-<запуск>.tsv.zst`).
    #[arg(long)]
    pub a: PathBuf,
    /// Каталог вывода B.
    #[arg(long)]
    pub b: PathBuf,
}

type Key = (u8, String, i64);
type Map = HashMap<Key, (i64, u64)>;

fn files(root: &Path) -> Vec<PathBuf> {
    let mut v = Vec::new();
    if let Ok(days) = std::fs::read_dir(root) {
        for d in days.flatten() {
            if let Ok(fs) = std::fs::read_dir(d.path()) {
                v.extend(
                    fs.flatten()
                        .map(|f| f.path())
                        .filter(|p| p.to_string_lossy().ends_with(".tsv.zst")),
                );
            }
        }
    }
    v.sort();
    v
}

fn h(parts: &[&str]) -> u64 {
    let mut f = Fnv::default();
    for p in parts {
        f.write(p.as_bytes());
        f.write(b"\t");
    }
    f.finish()
}

fn load(root: &Path) -> (Map, [u64; 3], i64, i64) {
    let mut m: Map = HashMap::new();
    let mut rows = [0u64; 3];
    let (mut t0, mut t1) = (i64::MAX, i64::MIN);
    for f in files(root) {
        let Ok(file) = std::fs::File::open(&f) else {
            continue;
        };
        let Ok(dec) = zstd::stream::read::Decoder::new(file) else {
            continue;
        };
        let mut rd = BufReader::new(dec);
        let mut d = Decoder::new();
        let mut line = Vec::new();
        loop {
            line.clear();
            match rd.read_until(b'\n', &mut line) {
                Ok(0) | Err(_) => break,
                Ok(_) => {}
            }
            if line.last() != Some(&b'\n') {
                break; // недописанная последняя строка
            }
            let Ok(text) = std::str::from_utf8(line.get(..line.len() - 1).unwrap_or(&[])) else {
                continue;
            };
            let Ok(rec) = d.line(text) else { continue };
            let (key, rx, ph) = match rec {
                Decoded::Book {
                    rx_ns,
                    e,
                    t,
                    sym,
                    u,
                    bid,
                    bsz,
                    ask,
                    asz,
                    bn,
                    an,
                } => {
                    rows[0] += 1;
                    (
                        (b'b', sym, u),
                        rx_ns,
                        h(&[
                            &e.to_string(),
                            &t.to_string(),
                            &bid,
                            &bsz,
                            &ask,
                            &asz,
                            &bn,
                            &an,
                        ]),
                    )
                }
                Decoded::Trade {
                    rx_ns,
                    e,
                    t,
                    sym,
                    a,
                    p,
                    q,
                    f,
                    l,
                    m,
                    nq,
                    x,
                    i,
                } => {
                    rows[1] += 1;
                    (
                        (b't', sym, a),
                        rx_ns,
                        h(&[
                            &e.to_string(),
                            &t.to_string(),
                            &p,
                            &q,
                            &f.to_string(),
                            &l.to_string(),
                            &m.to_string(),
                            &nq,
                            &x,
                            &i,
                        ]),
                    )
                }
                Decoded::Mark {
                    rx_ns,
                    e,
                    sym,
                    p,
                    ap,
                    pp,
                    i,
                    r,
                    t,
                } => {
                    rows[2] += 1;
                    (
                        (b'm', sym, e),
                        rx_ns,
                        h(&[&p, &ap, &pp, &i, &r, &t.to_string()]),
                    )
                }
                _ => continue,
            };
            t0 = t0.min(rx);
            t1 = t1.max(rx);
            let e = m.entry(key).or_insert((rx, ph));
            e.0 = e.0.min(rx);
        }
    }
    (m, rows, t0, t1)
}

pub fn run(a: &CompareArgs) {
    let (ma, ra, a0, a1) = load(&a.a);
    let (mb, rb, b0, b1) = load(&a.b);
    let trim = 10_000_000_000i64;
    let (lo, hi) = (a0.max(b0) + trim, a1.min(b1) - trim);
    println!(
        "A: {} личностей (book/trade/mark строк {:?}), B: {} ({:?}); окно сравнения {:.0} с",
        ma.len(),
        ra,
        mb.len(),
        rb,
        (hi - lo) as f64 / 1e9
    );
    let inside = |rx: i64| rx >= lo && rx <= hi;
    let (mut common, mut only_a, mut only_b, mut mism) = (0u64, 0u64, 0u64, 0u64);
    let mut diffs: Vec<i64> = Vec::new();
    for (k, &(rxa, pha)) in &ma {
        if !inside(rxa) {
            continue;
        }
        match mb.get(k) {
            Some(&(rxb, phb)) => {
                common += 1;
                if pha != phb {
                    mism += 1;
                }
                diffs.push(rxb - rxa);
            }
            None => only_a += 1,
        }
    }
    for (k, &(rxb, _)) in &mb {
        if inside(rxb) && !ma.contains_key(k) {
            only_b += 1;
        }
    }
    diffs.sort_unstable();
    let pct = |q: f64| {
        diffs
            .get(((diffs.len() as f64 * q) as usize).min(diffs.len().saturating_sub(1)))
            .copied()
            .unwrap_or(0) as f64
            / 1000.0
    };
    println!("общих {common}, только A {only_a}, только B {only_b}, различное содержимое среди общих {mism}");
    println!(
        "rx_b − rx_a, мкс: p0.1 {:.0} p1 {:.0} p10 {:.0} p50 {:.0} p90 {:.0} p99 {:.0} p99.9 {:.0}; хвост > +1 мс: {} ({:.3} %), < −1 мс: {}",
        pct(0.001),
        pct(0.01),
        pct(0.1),
        pct(0.5),
        pct(0.9),
        pct(0.99),
        pct(0.999),
        diffs.iter().filter(|&&d| d > 1_000_000).count(),
        100.0 * diffs.iter().filter(|&&d| d > 1_000_000).count() as f64 / diffs.len().max(1) as f64,
        diffs.iter().filter(|&&d| d < -1_000_000).count()
    );
}
