//! Сравнение двух каталогов вывода коллектора (G6 «бок о бок», collector-perf): мультимножества записей без `rx` и распределение `rx_b − rx_a`.
//!
//! Личность записи — (вид, символ, личность биржи: book `u`, trade `a`, mark `E`); у совпавших личностей сравнивается содержимое (хеш полей данных, без `rx`).
//! Края окна (по 10 с с обеих сторон пересечения времён двух выводов) не считаются: процессы стартуют и останавливаются не в одну секунду.
//! Читает и незакрытые файлы суток (чтение до конца дописанного).

// Диагностический инструмент (не горячий путь): отношения и проценты в f64 для печати.
#![allow(clippy::cast_precision_loss)]

use std::collections::{HashMap, HashSet};
use std::io::{BufRead as _, BufReader};
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

use clap::Args;

use super::codec::{Decoded, Decoder};

#[derive(Debug, Args)]
pub struct CompareArgs {
    /// Каталог вывода A (корень данных: `<день>/<шард>-<запуск>.tsv.zst`).
    #[arg(long)]
    pub a: PathBuf,
    /// Каталог вывода B.
    #[arg(long)]
    pub b: PathBuf,
    /// Читать только записи с `rx` не раньше этого (нс от эпохи); файлы, закрытые раньше, не открываются. 0 — без границы.
    #[arg(long, default_value_t = 0)]
    pub rx_from_ns: i64,
    /// Читать только записи с `rx` не позже этого (нс от эпохи).
    #[arg(long, default_value_t = i64::MAX)]
    pub rx_to_ns: i64,
    /// Из A брать только (вид, символ), которые есть в B — боевой каталог шире копии по шардам.
    #[arg(long)]
    pub only_b_symbols: bool,
    /// Личность записи как у BingX между соединениями (книга — (символ, `T`), сделка — id, mark — `E`; `u` книги — счётчик соединения) и счёт расхождений по полям.
    #[arg(long)]
    pub bingx_ids: bool,
}

type Key = (u8, String, i64);
type Map = HashMap<Key, (i64, String)>;

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

fn h(parts: &[&str]) -> String {
    parts.join("\t")
}

/// Окно чтения `rx` (нс от эпохи) и отбор (вид, символ) для одной стороны сравнения.
struct Scope<'a> {
    from: i64,
    to: i64,
    /// Символы по видам (книга, сделка, марк); `None` — все.
    only: Option<&'a [HashSet<String>; 3]>,
}

const fn kind_ix(kind: u8) -> usize {
    match kind {
        b'b' => 0,
        b't' => 1,
        _ => 2,
    }
}

fn closed_before(f: &Path, from: i64) -> bool {
    from > 0
        && std::fs::metadata(f)
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
            .is_some_and(|d| i64::try_from(d.as_nanos()).unwrap_or(i64::MAX) < from)
}

/// Читает все записи каталога по порядку файлов (незакрытые тоже, до конца дописанного), файлы, закрытые раньше `from`, пропускает.
fn for_each(root: &Path, from: i64, mut f: impl FnMut(Decoded)) {
    for path in files(root) {
        if closed_before(&path, from) {
            continue;
        }
        let Ok(file) = std::fs::File::open(&path) else {
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
            if let Ok(rec) = d.line(text) {
                f(rec);
            }
        }
    }
}

fn load(root: &Path, sc: &Scope) -> (Map, [u64; 3], i64, i64) {
    let mut m: Map = HashMap::new();
    let mut rows = [0u64; 3];
    let (mut t0, mut t1) = (i64::MAX, i64::MIN);
    for_each(root, sc.from, |rec| {
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
            } => (
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
            ),
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
            } => (
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
            ),
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
            } => (
                (b'm', sym, e),
                rx_ns,
                h(&[&p, &ap, &pp, &i, &r, &t.to_string()]),
            ),
            _ => return,
        };
        let ix = kind_ix(key.0);
        if rx < sc.from
            || rx > sc.to
            || sc
                .only
                .is_some_and(|o| o.get(ix).is_none_or(|set| !set.contains(&key.1)))
        {
            return;
        }
        if let Some(r) = rows.get_mut(ix) {
            *r += 1;
        }
        t0 = t0.min(rx);
        t1 = t1.max(rx);
        let e = m.entry(key).or_insert((rx, ph));
        e.0 = e.0.min(rx);
    });
    (m, rows, t0, t1)
}

/// Поля содержимого по видам (книга, сделка, mark) для счёта расхождений по полям в режиме `--bingx-ids`.
const FIELDS: [&[&str]; 3] = [
    &["e", "bid", "bsz", "ask", "asz"],
    &["e", "t", "p", "q", "f", "l", "m", "nq", "x", "i"],
    &["p", "ap", "pp", "i", "r", "t"],
];

type FMap = HashMap<Key, (i64, [u64; 10])>;

fn fnv(s: &str) -> u64 {
    s.bytes().fold(0xcbf2_9ce4_8422_2325, |h, b| {
        (h ^ u64::from(b)).wrapping_mul(0x0100_0000_01b3)
    })
}

fn field_hashes(parts: &[String]) -> [u64; 10] {
    let mut out = [0u64; 10];
    for (o, p) in out.iter_mut().zip(parts) {
        *o = fnv(p);
    }
    out
}

/// Личность записи BingX между соединениями, `rx` и поля содержимого в порядке `FIELDS`.
fn ids_of(rec: Decoded) -> Option<(Key, i64, Vec<String>)> {
    Some(match rec {
        Decoded::Book {
            rx_ns,
            e,
            t,
            sym,
            bid,
            bsz,
            ask,
            asz,
            ..
        } => (
            (b'b', sym, t),
            rx_ns,
            vec![e.to_string(), bid, bsz, ask, asz],
        ),
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
        } => (
            (b't', sym, a),
            rx_ns,
            vec![
                e.to_string(),
                t.to_string(),
                p,
                q,
                f.to_string(),
                l.to_string(),
                m.to_string(),
                nq,
                x,
                i,
            ],
        ),
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
        } => ((b'm', sym, e), rx_ns, vec![p, ap, pp, i, r, t.to_string()]),
        _ => return None,
    })
}

fn load_ids(root: &Path, sc: &Scope) -> (FMap, [u64; 3], i64, i64) {
    let mut m: FMap = HashMap::new();
    let mut rows = [0u64; 3];
    let (mut t0, mut t1) = (i64::MAX, i64::MIN);
    for_each(root, sc.from, |rec| {
        let Some((key, rx, fields)) = ids_of(rec) else {
            return;
        };
        let ix = kind_ix(key.0);
        if rx < sc.from
            || rx > sc.to
            || sc
                .only
                .is_some_and(|o| o.get(ix).is_none_or(|set| !set.contains(&key.1)))
        {
            return;
        }
        if let Some(r) = rows.get_mut(ix) {
            *r += 1;
        }
        t0 = t0.min(rx);
        t1 = t1.max(rx);
        let e = m.entry(key).or_insert((rx, field_hashes(&fields)));
        e.0 = e.0.min(rx);
    });
    (m, rows, t0, t1)
}

/// Значения полей первых вхождений заданных личностей (для печати примеров расхождений).
fn raw_of(root: &Path, from: i64, keys: &HashSet<Key>) -> HashMap<Key, (i64, Vec<String>)> {
    let mut out: HashMap<Key, (i64, Vec<String>)> = HashMap::new();
    for_each(root, from, |rec| {
        if let Some((key, rx, fields)) = ids_of(rec) {
            if keys.contains(&key) {
                out.entry(key).or_insert((rx, fields));
            }
        }
    });
    out
}

fn hhmmss(rx_ns: i64) -> String {
    let s = rx_ns.div_euclid(1_000_000_000).rem_euclid(86_400);
    format!("{:02}:{:02}:{:02}", s / 3600, s % 3600 / 60, s % 60)
}

/// Когда лежат записи, которых нет у другой стороны: границы и три самые плотные десятисекундки.
fn when(rxs: &[i64]) -> String {
    let (Some(lo), Some(hi)) = (rxs.iter().min(), rxs.iter().max()) else {
        return "—".to_string();
    };
    let mut b: HashMap<i64, u64> = HashMap::new();
    for r in rxs {
        *b.entry(r.div_euclid(10_000_000_000)).or_insert(0) += 1;
    }
    let mut top: Vec<(i64, u64)> = b.into_iter().collect();
    top.sort_by_key(|&(k, c)| (std::cmp::Reverse(c), k));
    let tops: Vec<String> = top
        .iter()
        .take(3)
        .map(|&(k, c)| format!("{} — {c}", hhmmss(k * 10_000_000_000)))
        .collect();
    format!(
        "{}…{} UTC, {} десятисекундок; плотнейшие: {}",
        hhmmss(*lo),
        hhmmss(*hi),
        rxs.iter()
            .map(|r| r.div_euclid(10_000_000_000))
            .collect::<HashSet<_>>()
            .len(),
        tops.join(", ")
    )
}

/// Режим `--bingx-ids`: по каждому виду — общие, только A, только B, различия по полям (время `E`/`T` сделки BingX своё у каждого соединения, TK-535).
fn run_ids(a: &CompareArgs) {
    let sc = |only| Scope {
        from: a.rx_from_ns,
        to: a.rx_to_ns,
        only,
    };
    let (mb, rb, b0, b1) = load_ids(&a.b, &sc(None));
    let mut syms: [HashSet<String>; 3] = Default::default();
    for k in mb.keys() {
        if let Some(set) = syms.get_mut(kind_ix(k.0)) {
            set.insert(k.1.clone());
        }
    }
    let (ma, ra, a0, a1) = load_ids(&a.a, &sc(a.only_b_symbols.then_some(&syms)));
    let trim = 10_000_000_000i64;
    let (lo, hi) = (a0.max(b0) + trim, a1.min(b1) - trim);
    println!(
        "A: {} личностей (book/trade/mark строк {:?}), B: {} ({:?}); окно сравнения {:.0} с; личность — как у BingX между соединениями",
        ma.len(),
        ra,
        mb.len(),
        rb,
        (hi - lo) as f64 / 1e9
    );
    let inside = |rx: i64| rx >= lo && rx <= hi;
    let mut common = [0u64; 3];
    let mut only_a = [0u64; 3];
    let mut only_b = [0u64; 3];
    let mut by_field = [[0u64; 10]; 3];
    let mut whole = [0u64; 3];
    let mut when_a: [Vec<i64>; 3] = Default::default();
    let mut when_b: [Vec<i64>; 3] = Default::default();
    let mut ex: [Vec<(Key, bool)>; 3] = Default::default();
    let timing = |ix: usize, f: usize| (ix == 0 && f == 0) || (ix == 1 && f < 2);
    for (k, (rxa, ha)) in &ma {
        if !inside(*rxa) {
            continue;
        }
        let ix = kind_ix(k.0);
        match mb.get(k) {
            Some((_, hb)) => {
                bump(&mut common, ix);
                let mut any = false;
                let mut other = false;
                for (f, (x, y)) in ha.iter().zip(hb).enumerate() {
                    if x != y {
                        any = true;
                        other |= !timing(ix, f);
                        if let Some(c) = by_field.get_mut(ix).and_then(|r| r.get_mut(f)) {
                            *c += 1;
                        }
                    }
                }
                if any {
                    bump(&mut whole, ix);
                    if let Some(v) = ex.get_mut(ix) {
                        let have = v.iter().filter(|(_, o)| *o == other).count();
                        if have < if other { 4 } else { 1 } {
                            v.push((k.clone(), other));
                        }
                    }
                }
            }
            None => {
                bump(&mut only_a, ix);
                if let Some(w) = when_a.get_mut(ix) {
                    w.push(*rxa);
                }
            }
        }
    }
    for (k, (rxb, _)) in &mb {
        if inside(*rxb) && !ma.contains_key(k) {
            bump(&mut only_b, kind_ix(k.0));
            if let Some(w) = when_b.get_mut(kind_ix(k.0)) {
                w.push(*rxb);
            }
        }
    }
    for (ix, name) in ["book", "trade", "mark"].iter().enumerate() {
        let diffs: Vec<String> = FIELDS
            .get(ix)
            .into_iter()
            .flat_map(|fs| fs.iter().enumerate())
            .filter_map(|(f, fname)| {
                let c = by_field
                    .get(ix)
                    .and_then(|r| r.get(f))
                    .copied()
                    .unwrap_or(0);
                (c > 0).then(|| format!("{fname} {c}"))
            })
            .collect();
        println!(
            "  {name}: общих {}, только A {}, только B {}, с различием содержимого {} (по полям: {})",
            common.get(ix).copied().unwrap_or(0),
            only_a.get(ix).copied().unwrap_or(0),
            only_b.get(ix).copied().unwrap_or(0),
            whole.get(ix).copied().unwrap_or(0),
            if diffs.is_empty() { "—".to_string() } else { diffs.join(", ") }
        );
    }
    for (ix, name) in ["book", "trade", "mark"].iter().enumerate() {
        for (lab, w) in [("только A", &when_a), ("только B", &when_b)] {
            if let Some(v) = w.get(ix).filter(|v| !v.is_empty()) {
                println!("    {name} {lab} — когда: {}", when(v));
            }
        }
    }
    let keys: HashSet<Key> = ex.iter().flatten().map(|(k, _)| k.clone()).collect();
    if !keys.is_empty() {
        let (ra_, rb_) = (
            raw_of(&a.a, a.rx_from_ns, &keys),
            raw_of(&a.b, a.rx_from_ns, &keys),
        );
        for (k, other) in ex.iter().flatten() {
            let fs = FIELDS.get(kind_ix(k.0)).copied().unwrap_or(&[]);
            let (Some((xa, fa)), Some((xb, fb))) = (ra_.get(k), rb_.get(k)) else {
                continue;
            };
            let diff: Vec<String> = fs
                .iter()
                .zip(fa.iter().zip(fb))
                .filter(|(_, (x, y))| x != y)
                .map(|(n, (x, y))| format!("{n}: A {x} / B {y}"))
                .collect();
            println!(
                "    пример ({}): {}:{}:{} rx_A {} rx_B {}; {}",
                if *other {
                    "не время"
                } else {
                    "время"
                },
                k.0 as char,
                k.1,
                k.2,
                hhmmss(*xa),
                hhmmss(*xb),
                diff.join("; ")
            );
        }
    }
}

fn bump(c: &mut [u64; 3], ix: usize) {
    if let Some(x) = c.get_mut(ix) {
        *x += 1;
    }
}

pub fn run(a: &CompareArgs) {
    if a.bingx_ids {
        run_ids(a);
        return;
    }
    let (mb, rb, b0, b1) = load(
        &a.b,
        &Scope {
            from: a.rx_from_ns,
            to: a.rx_to_ns,
            only: None,
        },
    );
    let mut syms: [HashSet<String>; 3] = Default::default();
    for k in mb.keys() {
        if let Some(set) = syms.get_mut(kind_ix(k.0)) {
            set.insert(k.1.clone());
        }
    }
    let (ma, ra, a0, a1) = load(
        &a.a,
        &Scope {
            from: a.rx_from_ns,
            to: a.rx_to_ns,
            only: a.only_b_symbols.then_some(&syms),
        },
    );
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
    let mut mism_kind = [0u64; 3];
    let mut examples: Vec<String> = Vec::new();
    let mut diffs: Vec<i64> = Vec::new();
    for (k, (rxa, pha)) in &ma {
        let rxa = *rxa;
        if !inside(rxa) {
            continue;
        }
        match mb.get(k) {
            Some((rxb, phb)) => {
                common += 1;
                if pha != phb {
                    mism += 1;
                    if let Some(c) = mism_kind.get_mut(kind_ix(k.0)) {
                        *c += 1;
                    }
                    if examples.len() < 3 {
                        examples.push(format!("{}:{}:{} A[{pha}] B[{phb}]", k.0 as char, k.1, k.2));
                    }
                }
                diffs.push(*rxb - rxa);
            }
            None => only_a += 1,
        }
    }
    for (k, (rxb, _)) in &mb {
        let rxb = *rxb;
        if inside(rxb) && !ma.contains_key(k) {
            only_b += 1;
        }
    }
    diffs.sort_unstable();
    #[allow(clippy::cast_possible_truncation)] // q ∈ [0, 1]: индекс не выходит за длину
    let pct = |q: f64| {
        diffs
            .get(((diffs.len() as f64 * q) as usize).min(diffs.len().saturating_sub(1)))
            .copied()
            .unwrap_or(0) as f64
            / 1000.0
    };
    println!("общих {common}, только A {only_a}, только B {only_b}, различное содержимое среди общих {mism} (book/trade/mark {mism_kind:?})");
    for e in &examples {
        println!("  пример различия: {e}");
    }
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

#[derive(Debug, Args)]
pub struct StampErrArgs {
    /// Каталог вывода коллектора, питавшегося `tools/collector-perf/tlsws_replay.py` (в поле `B` книги — время отправки, мкс по модулю 10^10).
    #[arg(long)]
    pub dir: PathBuf,
}

/// Ошибка метки приёма: `rx − время отправки` по книге из локального источника (мкс): процентили. Время отправки зашито в `bsz` источником.
pub fn stamp_err(a: &StampErrArgs) {
    let mut errs: Vec<i64> = Vec::new();
    for f in files(&a.dir) {
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
                break;
            }
            let Ok(text) = std::str::from_utf8(line.get(..line.len() - 1).unwrap_or(&[])) else {
                continue;
            };
            let Ok(Decoded::Book { rx_ns, bsz, .. }) = d.line(text) else {
                continue;
            };
            if bsz.len() != 10 {
                continue;
            }
            let Ok(low) = bsz.parse::<i64>() else {
                continue;
            };
            let rx_us = rx_ns / 1000;
            let send_us = rx_us - (rx_us - low).rem_euclid(10_000_000_000);
            errs.push(rx_ns - send_us * 1000);
        }
    }
    errs.sort_unstable();
    #[allow(clippy::cast_possible_truncation)] // q ∈ [0, 1]: индекс не выходит за длину
    let pct = |q: f64| {
        errs.get(((errs.len() as f64 * q) as usize).min(errs.len().saturating_sub(1)))
            .copied()
            .unwrap_or(0) as f64
            / 1000.0
    };
    println!(
        "записей книги {}: rx − отправка, мкс: p0.1 {:.0} p1 {:.0} p10 {:.0} p50 {:.0} p90 {:.0} p99 {:.0} p99.9 {:.0} max {:.0}",
        errs.len(),
        pct(0.001),
        pct(0.01),
        pct(0.1),
        pct(0.5),
        pct(0.9),
        pct(0.99),
        pct(0.999),
        errs.last().copied().unwrap_or(0) as f64 / 1000.0
    );
}
