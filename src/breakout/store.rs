//! TK-607 (L1): бинарный склад суточных сделок. Первая загрузка `(монета, дата)` из zip пишет компактный файл
//! `<склад>/<МОНЕТА>/<МОНЕТА>-<дата>.tk`, следующие читают его вместо `unzip -p` + разбора CSV.
//!
//! Содержимое файла — чистая функция байтов исходного zip: сделки суток сразу после `parse_trades` (отсев строк вне суток и
//! стабильная сортировка при `unsorted` уже применены; `off` — мс от полуночи календарной даты файла, поэтому окна с разным
//! `P42_T0` делят одни файлы) и счётчики `LoadStat`. `Vec<Tick>` со склада равен прежнему байт в байт.
//!
//! Формат v2 (little-endian): заголовок `HEAD_LEN` байт — магия, версия, размер и mtime zip, число строк, счётчики, длины
//! блоков, флаги точности, масштаб и шаг количества; блок A — zstd (с контрольной суммой) от «первая цена `i64`, НОД шагов
//! цены `u64`, затем на сделку два varint: зигзаг `(цена − прежняя) / НОД` и `(off − прежний) << 1 | bm`»; блок Q (количество,
//! TK-609) — zstd от varint `количество / шаг` на сделку, в порядке блока A. Путь движка читает только заголовок и блок A (`read`),
//! потребители признаков — оба (`read_day`). Запись — во временный файл и `rename`.
//!
//! Количество — целое в единицах `10^-qty_dec` (`qty_dec` — максимум знаков после точки в файле без хвостовых нулей, ≤ 18);
//! `qty / 10^qty_dec` как `f64` равно разбору десятичной строки (`pandas`/`strtod`) при `flags == 0`: оба числа точны в `f64`, одно
//! деление. Флаги: `F_PX_INEXACT` — цена с > 8 знаками (отброшены `parse_px`) или > 2^53 в ×1e8; `F_QTY_BAD` — количество не
//! разобрано / > 18 знаков / переполнение (записан 0); `F_QTY_F64` — количество > 2^53 (двойное округление).
//!
//! Предохранители: потолок объёма (`cap_bytes`, считается при открытии по каталогу и растёт с записями этого процесса;
//! параллельные процессы видят потолок приблизительно) и страж `df`: при свободном месте < `min_free_bytes` запись
//! прекращается, чтение продолжается.

use super::tick::{LoadStat, Tick, DAY_MS};
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::UNIX_EPOCH;

mod fold;
pub use fold::DayHead;

/// Версия формата склада (входит в ключ кэша сигналов L2).
pub const STORE_VERSION: u32 = 2;
const MAGIC: [u8; 4] = *b"TK42";
const HEAD_LEN: usize = 4 + 4 + 8 + 8 + 4 + 4 + 4 * 8 + 4 + 4 + 4 + 4 + 8 + 4 + 4;
const ZSTD_LEVEL: i32 = 3;
/// Проверка `df` — раз в столько попыток записи.
const DF_EVERY: u64 = 128;

/// Размер и mtime исходного zip: склад устаревает, когда они изменились.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SrcMeta {
    pub size: u64,
    pub mtime_s: i64,
    pub mtime_ns: u32,
}

impl SrcMeta {
    pub fn of(m: &fs::Metadata) -> Option<SrcMeta> {
        let d = m.modified().ok()?.duration_since(UNIX_EPOCH).ok()?;
        Some(SrcMeta {
            size: m.len(),
            mtime_s: i64::try_from(d.as_secs()).ok()?,
            mtime_ns: d.subsec_nanos(),
        })
    }
}

/// Параметры склада из командной строки.
#[derive(Debug, Clone)]
pub struct StoreOpts {
    pub dir: PathBuf,
    pub cap_gb: u64,
    pub min_free_gb: u64,
}

#[derive(Debug, Default, Clone, Copy)]
pub struct StoreCounters {
    pub hit: u64,
    pub miss: u64,
    pub written: u64,
    pub skipped: u64,
    pub bad: u64,
    pub write_err: u64,
    pub used_bytes: u64,
    /// Сжатый блок количества и строки по записанным этим процессом суткам (для оценки доли количества в объёме).
    pub qty_bytes: u64,
    pub rows: u64,
}

pub struct TickStore {
    dir: PathBuf,
    cap_bytes: u64,
    min_free_bytes: u64,
    used: AtomicU64,
    /// Потолок достигнут или `df` ниже порога: только чтение.
    read_only: AtomicBool,
    attempts: AtomicU64,
    hit: AtomicU64,
    miss: AtomicU64,
    written: AtomicU64,
    qty_bytes: AtomicU64,
    rows: AtomicU64,
    skipped: AtomicU64,
    bad: AtomicU64,
    write_err: AtomicU64,
}

fn put(out: &mut Vec<u8>, mut v: u64) {
    while v >= 0x80 {
        out.push((v as u8 & 0x7f) | 0x80);
        v >>= 7;
    }
    out.push(v as u8);
}

/// Одно число varint. Значения в один и два байта (почти все в потоке сделок) — прямо в месте вызова, без ветвления по длине
/// (курсор остаётся в регистре); остальное (три байта и больше, последний байт буфера) — `get_multi`.
/// Результат и ошибки (`None`: обрыв, больше 10 байт) те же, что у цикла по байтам.
#[inline(always)]
pub(super) fn get(b: &[u8], i: &mut usize) -> Option<u64> {
    let p = *i;
    if let Some(&[c0, c1]) = b.get(p..p + 2) {
        let one = c0 < 0x80;
        if one || c1 < 0x80 {
            *i = p + if one { 1 } else { 2 };
            return Some(u64::from(c0 & 0x7f) | if one { 0 } else { u64::from(c1) << 7 });
        }
    }
    get_multi(b, i)
}

#[inline(never)]
fn get_multi(b: &[u8], i: &mut usize) -> Option<u64> {
    let (mut r, mut s) = (0u64, 0u32);
    loop {
        let c = *b.get(*i)?;
        *i += 1;
        if s > 63 {
            return None;
        }
        r |= u64::from(c & 0x7f) << s;
        if c < 0x80 {
            return Some(r);
        }
        s += 7;
    }
}

fn gcd(mut a: u64, mut b: u64) -> u64 {
    while b != 0 {
        (a, b) = (b, a % b);
    }
    a
}

pub const F_PX_INEXACT: u32 = 1;
pub const F_QTY_BAD: u32 = 2;
pub const F_QTY_F64: u32 = 4;
/// Наибольшее целое, точное в `f64`.
const F64_EXACT: u64 = 1 << 53;
/// Больше знаков после точки в количестве не храним (`10^18` ещё в `u64`, `10^22` точно в `f64`).
const MAX_QTY_DEC: u8 = 18;
const BAD_DEC: u8 = u8::MAX;
const POW10: [f64; MAX_QTY_DEC as usize + 1] = [
    1e0, 1e1, 1e2, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9, 1e10, 1e11, 1e12, 1e13, 1e14, 1e15, 1e16,
    1e17, 1e18,
];

/// Количество сделок «как разобрано»: на строку мантисса и число знаков (`BAD_DEC` — не разобрано). Заполняется при разборе
/// CSV вместе с `Vec<Tick>` и переставляется с ним при сортировке.
#[derive(Default)]
pub struct QtyRaw {
    mant: Vec<u64>,
    dec: Vec<u8>,
    px_inexact: bool,
}

impl QtyRaw {
    /// `p`, `q` — поля цены и количества строки; `px` — цена ×1e8, как её разобрал движок.
    pub fn push(&mut self, p: &[u8], px: i64, q: Option<&[u8]>) {
        match q.and_then(super::tick::dec_parts) {
            Some((m, d)) => {
                self.mant.push(m);
                self.dec.push(d);
            }
            None => {
                self.mant.push(0);
                self.dec.push(BAD_DEC);
            }
        }
        if !self.px_inexact
            && (px.unsigned_abs() > F64_EXACT
                || super::tick::dec_parts(p).is_none_or(|(_, d)| d > 8))
        {
            self.px_inexact = true;
        }
    }

    /// Та же устойчивая сортировка по `off`, что у `Vec<Tick>` без количества.
    pub fn sort_with(&mut self, v: &mut Vec<Tick>) {
        let mut idx: Vec<u32> = (0..v.len() as u32).collect();
        idx.sort_by_key(|&i| v[i as usize].off);
        *v = idx.iter().map(|&i| v[i as usize]).collect();
        self.mant = idx.iter().map(|&i| self.mant[i as usize]).collect();
        self.dec = idx.iter().map(|&i| self.dec[i as usize]).collect();
    }

    /// Единый масштаб файла: `(qty_dec, количество целым в единицах 10^-qty_dec, флаги)`.
    fn finish(self) -> (u8, Vec<u64>, u32) {
        let d = self
            .dec
            .iter()
            .copied()
            .filter(|&d| d <= MAX_QTY_DEC)
            .max()
            .unwrap_or(0);
        let mut flags = if self.px_inexact { F_PX_INEXACT } else { 0 };
        let q = self
            .mant
            .iter()
            .zip(&self.dec)
            .map(|(&m, &rd)| {
                let v = (rd <= MAX_QTY_DEC)
                    .then(|| 10u64.checked_pow(u32::from(d - rd)))
                    .flatten()
                    .and_then(|k| m.checked_mul(k));
                match v {
                    Some(v) => {
                        if v > F64_EXACT {
                            flags |= F_QTY_F64;
                        }
                        v
                    }
                    None => {
                        flags |= F_QTY_BAD;
                        0
                    }
                }
            })
            .collect();
        (d, q, flags)
    }
}

/// Сутки сделок со склада или из zip: путь движка берёт `ticks`, признаки — ещё `qty`.
#[derive(Debug, Default, Clone)]
pub struct DayTrades {
    pub ticks: Vec<Tick>,
    /// Количество целым в единицах `10^-qty_dec`, по сделке параллельно `ticks`; пусто, если читали без количества.
    pub qty: Vec<u64>,
    pub qty_dec: u8,
    /// `F_*`; 0 — цена и количество переводятся в `f64` точно как десятичные строки.
    pub flags: u32,
    pub stat: LoadStat,
}

impl DayTrades {
    pub fn new(ticks: Vec<Tick>, raw: QtyRaw, stat: LoadStat) -> DayTrades {
        let (qty_dec, qty, flags) = raw.finish();
        DayTrades {
            ticks,
            qty,
            qty_dec,
            flags,
            stat,
        }
    }

    /// Цена сделки `i` как `f64` (точна при `flags & F_PX_INEXACT == 0`).
    pub fn px_f64(&self, i: usize) -> f64 {
        self.ticks[i].px as f64 / 1e8
    }

    /// Количество сделки `i` как `f64` (точно при `flags & (F_QTY_BAD | F_QTY_F64) == 0`).
    pub fn qty_f64(&self, i: usize) -> f64 {
        self.qty[i] as f64 / POW10[usize::from(self.qty_dec)]
    }
}

/// Количество → `(шаг, нагрузка)`: шаг — НОД ненулевых значений, на сделку varint `значение / шаг`.
fn encode_qty(q: &[u64]) -> (u64, Vec<u8>) {
    let g = q.iter().fold(0u64, |g, &x| gcd(g, x)).max(1);
    let mut out = Vec::with_capacity(q.len() * 2 + 8);
    for &x in q {
        put(&mut out, x / g);
    }
    (g, out)
}

fn decode_qty(b: &[u8], n: usize, step: u64) -> Option<Vec<u64>> {
    let (mut v, mut i) = (Vec::with_capacity(n), 0usize);
    for _ in 0..n {
        v.push(get(b, &mut i)?.checked_mul(step)?);
    }
    (i == b.len()).then_some(v)
}

/// Сделки → несжатая нагрузка. `None` — сделки вне допущений кодека (не по порядку, переполнение шага цены): склад не пишется.
pub fn encode(t: &[Tick]) -> Option<Vec<u8>> {
    let first = t.first().map_or(0, |x| x.px);
    let mut g = 0u64;
    let mut prev = first;
    let mut poff = 0u32;
    for x in t {
        if x.off < poff || i64::from(x.off) >= DAY_MS {
            return None;
        }
        poff = x.off;
        if g != 1 {
            g = gcd(g, x.px.checked_sub(prev)?.unsigned_abs());
        }
        prev = x.px;
    }
    if g == 0 {
        g = 1;
    }
    let mut out = Vec::with_capacity(t.len() * 3 + 16);
    out.extend_from_slice(&first.to_le_bytes());
    out.extend_from_slice(&g.to_le_bytes());
    let (mut ppx, mut poff) = (first, 0u32);
    for x in t {
        let d = (x.px - ppx) / g as i64;
        put(&mut out, ((d << 1) ^ (d >> 63)) as u64);
        put(&mut out, (u64::from(x.off - poff) << 1) | u64::from(x.bm));
        ppx = x.px;
        poff = x.off;
    }
    Some(out)
}

/// Нагрузка → `n` сделок; `None` — повреждение (обрыв, лишние байты, `off` вне суток).
pub fn decode(b: &[u8], n: usize) -> Option<Vec<Tick>> {
    let mut px = i64::from_le_bytes(b.get(0..8)?.try_into().ok()?);
    let g = i64::try_from(u64::from_le_bytes(b.get(8..16)?.try_into().ok()?)).ok()?;
    let mut v = Vec::with_capacity(n);
    let (mut off, mut i) = (0u64, 16usize);
    for _ in 0..n {
        let z = get(b, &mut i)?;
        px = px.wrapping_add(((z >> 1) as i64 ^ -((z & 1) as i64)).wrapping_mul(g));
        let w = get(b, &mut i)?;
        off += w >> 1;
        if off >= DAY_MS as u64 {
            return None;
        }
        v.push(Tick {
            px,
            off: off as u32,
            bm: w & 1 == 1,
        });
    }
    (i == b.len()).then_some(v)
}

pub(super) fn compress(raw: &[u8]) -> Option<Vec<u8>> {
    let mut enc =
        zstd::stream::write::Encoder::new(Vec::with_capacity(raw.len() / 2 + 64), ZSTD_LEVEL)
            .ok()?;
    enc.include_checksum(true).ok()?;
    enc.write_all(raw).ok()?;
    enc.finish().ok()
}

/// Разобранный заголовок файла.
struct Head {
    n: usize,
    st: LoadStat,
    raw_a: usize,
    comp_a: usize,
    flags: u32,
    qty_dec: u8,
    qty_step: u64,
    raw_q: usize,
    comp_q: usize,
}

fn head(meta: &SrcMeta, d: &DayTrades, step: u64, lens: [usize; 4]) -> Option<Vec<u8>> {
    let mut h = Vec::with_capacity(HEAD_LEN);
    h.extend_from_slice(&MAGIC);
    h.extend_from_slice(&STORE_VERSION.to_le_bytes());
    h.extend_from_slice(&meta.size.to_le_bytes());
    h.extend_from_slice(&meta.mtime_s.to_le_bytes());
    h.extend_from_slice(&meta.mtime_ns.to_le_bytes());
    h.extend_from_slice(&u32::try_from(d.ticks.len()).ok()?.to_le_bytes());
    let st = &d.stat;
    for c in [st.rows, st.unsorted_files, st.out_of_day, st.bad_rows] {
        h.extend_from_slice(&c.to_le_bytes());
    }
    let [raw_a, comp_a, raw_q, comp_q] = lens.map(u32::try_from);
    h.extend_from_slice(&raw_a.ok()?.to_le_bytes());
    h.extend_from_slice(&comp_a.ok()?.to_le_bytes());
    h.extend_from_slice(&d.flags.to_le_bytes());
    h.extend_from_slice(&u32::from(d.qty_dec).to_le_bytes());
    h.extend_from_slice(&step.to_le_bytes());
    h.extend_from_slice(&raw_q.ok()?.to_le_bytes());
    h.extend_from_slice(&comp_q.ok()?.to_le_bytes());
    Some(h)
}

/// Заголовок файла. `Err(true)` — повреждён, `Err(false)` — устарел или иной формат.
fn parse_head(buf: &[u8; HEAD_LEN], meta: &SrcMeta) -> Result<Head, bool> {
    if buf[..4] != MAGIC {
        return Err(true);
    }
    let u32_at = |o: usize| u32::from_le_bytes([buf[o], buf[o + 1], buf[o + 2], buf[o + 3]]);
    let u64_at = |o: usize| {
        let mut a = [0u8; 8];
        a.copy_from_slice(&buf[o..o + 8]);
        u64::from_le_bytes(a)
    };
    if u32_at(4) != STORE_VERSION {
        return Err(false);
    }
    let src = SrcMeta {
        size: u64_at(8),
        mtime_s: u64_at(16) as i64,
        mtime_ns: u32_at(24),
    };
    if src != *meta {
        return Err(false);
    }
    Ok(Head {
        n: u32_at(28) as usize,
        st: LoadStat {
            files: 1,
            rows: u64_at(32),
            unsorted_files: u64_at(40),
            out_of_day: u64_at(48),
            bad_rows: u64_at(56),
            errors: 0,
        },
        raw_a: u32_at(64) as usize,
        comp_a: u32_at(68) as usize,
        flags: u32_at(72),
        qty_dec: u8::try_from(u32_at(76)).map_err(|_| true)?,
        qty_step: u64_at(80),
        raw_q: u32_at(88) as usize,
        comp_q: u32_at(92) as usize,
    })
}

/// Файл склада → сутки (`full` — с количеством). `Err(true)` — файл повреждён, `Err(false)` — нет, устарел или иной формат.
fn read_file(p: &Path, meta: &SrcMeta, full: bool) -> Result<DayTrades, bool> {
    let mut f = fs::File::open(p).map_err(|_| false)?;
    let mut hb = [0u8; HEAD_LEN];
    f.read_exact(&mut hb)
        .map_err(|e| e.kind() == std::io::ErrorKind::UnexpectedEof)?;
    let h = parse_head(&hb, meta)?;
    let total = (HEAD_LEN + h.comp_a + h.comp_q) as u64;
    if f.metadata().map_err(|_| false)?.len() != total {
        return Err(true);
    }
    let mut body = vec![0u8; h.comp_a];
    f.read_exact(&mut body).map_err(|_| true)?;
    let raw = zstd::bulk::decompress(&body, h.raw_a).map_err(|_| true)?;
    if raw.len() != h.raw_a {
        return Err(true);
    }
    let ticks = decode(&raw, h.n).ok_or(true)?;
    let mut qty = Vec::new();
    if full {
        let mut qb = vec![0u8; h.comp_q];
        f.read_exact(&mut qb).map_err(|_| true)?;
        let qraw = zstd::bulk::decompress(&qb, h.raw_q).map_err(|_| true)?;
        if qraw.len() != h.raw_q {
            return Err(true);
        }
        qty = decode_qty(&qraw, h.n, h.qty_step).ok_or(true)?;
    }
    Ok(DayTrades {
        ticks,
        qty,
        qty_dec: h.qty_dec,
        flags: h.flags,
        stat: h.st,
    })
}

pub(super) fn free_bytes(dir: &Path) -> Option<u64> {
    let out = Command::new("df")
        .args(["--output=avail", "-B1"])
        .arg(dir)
        .output()
        .ok()?;
    String::from_utf8_lossy(&out.stdout)
        .lines()
        .nth(1)?
        .trim()
        .parse()
        .ok()
}

fn dir_bytes(dir: &Path) -> u64 {
    let mut total = 0;
    let Ok(rd) = fs::read_dir(dir) else { return 0 };
    for coin in rd.flatten() {
        let Ok(files) = fs::read_dir(coin.path()) else {
            continue;
        };
        for f in files.flatten() {
            total += f.metadata().map_or(0, |m| m.len());
        }
    }
    total
}

impl TickStore {
    pub fn open(o: &StoreOpts) -> std::io::Result<TickStore> {
        fs::create_dir_all(&o.dir)?;
        let used = dir_bytes(&o.dir);
        let s = TickStore {
            dir: o.dir.clone(),
            cap_bytes: o.cap_gb << 30,
            min_free_bytes: o.min_free_gb << 30,
            used: AtomicU64::new(used),
            read_only: AtomicBool::new(false),
            attempts: AtomicU64::new(0),
            hit: AtomicU64::new(0),
            miss: AtomicU64::new(0),
            written: AtomicU64::new(0),
            qty_bytes: AtomicU64::new(0),
            rows: AtomicU64::new(0),
            skipped: AtomicU64::new(0),
            bad: AtomicU64::new(0),
            write_err: AtomicU64::new(0),
        };
        s.check_limits();
        Ok(s)
    }

    fn check_limits(&self) {
        let low = free_bytes(&self.dir).is_some_and(|f| f < self.min_free_bytes);
        if low || self.used.load(Ordering::Relaxed) >= self.cap_bytes {
            self.read_only.store(true, Ordering::Relaxed);
        }
    }

    pub fn path(&self, name: &str, date: &str) -> PathBuf {
        self.dir.join(name).join(format!("{name}-{date}.tk"))
    }

    /// Сутки со склада для движка (сделки без количества); `None` — нет файла, устарел, повреждён (сосчитано) — вызывающий читает zip.
    pub fn read(&self, name: &str, date: &str, meta: &SrcMeta) -> Option<(Vec<Tick>, LoadStat)> {
        self.read_with(name, date, meta, false)
            .map(|d| (d.ticks, d.stat))
    }

    /// Сутки со склада целиком (сделки и количество) для признаков; `None` — как у `read`.
    pub fn read_day(&self, name: &str, date: &str, meta: &SrcMeta) -> Option<DayTrades> {
        self.read_with(name, date, meta, true)
    }

    fn read_with(&self, name: &str, date: &str, meta: &SrcMeta, full: bool) -> Option<DayTrades> {
        let p = self.path(name, date);
        match read_file(&p, meta, full) {
            Ok(x) => {
                self.hit.fetch_add(1, Ordering::Relaxed);
                Some(x)
            }
            Err(bad) => {
                self.miss.fetch_add(1, Ordering::Relaxed);
                if bad {
                    self.bad.fetch_add(1, Ordering::Relaxed);
                    eprintln!("склад: повреждён {}", p.display());
                }
                None
            }
        }
    }

    /// Записать сутки (после успешного разбора zip). Сбой записи — предупреждение: склад не влияет на результат счёта.
    pub fn write(&self, name: &str, date: &str, meta: &SrcMeta, d: &DayTrades) {
        if self.attempts.fetch_add(1, Ordering::Relaxed) % DF_EVERY == DF_EVERY - 1 {
            self.check_limits();
        }
        if self.read_only.load(Ordering::Relaxed) || d.qty.len() != d.ticks.len() {
            self.skipped.fetch_add(1, Ordering::Relaxed);
            return;
        }
        let Some((file, qlen)) = encode(&d.ticks).and_then(|raw| {
            let ca = compress(&raw)?;
            let (step, qraw) = encode_qty(&d.qty);
            let cq = compress(&qraw)?;
            let mut f = head(meta, d, step, [raw.len(), ca.len(), qraw.len(), cq.len()])?;
            f.extend_from_slice(&ca);
            f.extend_from_slice(&cq);
            Some((f, cq.len()))
        }) else {
            self.skipped.fetch_add(1, Ordering::Relaxed);
            return;
        };
        let p = self.path(name, date);
        let tmp = p.with_extension(format!(
            "tmp.{}.{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
        let res = p
            .parent()
            .map_or(Ok(()), fs::create_dir_all)
            .and_then(|()| fs::write(&tmp, &file))
            .and_then(|()| fs::rename(&tmp, &p));
        match res {
            Ok(()) => {
                self.written.fetch_add(1, Ordering::Relaxed);
                self.qty_bytes.fetch_add(qlen as u64, Ordering::Relaxed);
                self.rows.fetch_add(d.ticks.len() as u64, Ordering::Relaxed);
                if self.used.fetch_add(file.len() as u64, Ordering::Relaxed) + file.len() as u64
                    >= self.cap_bytes
                {
                    self.read_only.store(true, Ordering::Relaxed);
                }
            }
            Err(e) => {
                let _ = fs::remove_file(&tmp);
                self.write_err.fetch_add(1, Ordering::Relaxed);
                if self.write_err.load(Ordering::Relaxed) == 1 {
                    eprintln!("склад: запись {} не удалась: {e}", p.display());
                }
                self.read_only.store(true, Ordering::Relaxed);
            }
        }
    }

    /// Число строк файла, если он лежит, ключ (размер и mtime zip) тот же и длина сходится с заголовком — без разбора блоков
    /// (быстрый пропуск лежащих файлов в store-fill).
    pub fn valid_rows(&self, name: &str, date: &str, meta: &SrcMeta) -> Option<u64> {
        let mut f = fs::File::open(self.path(name, date)).ok()?;
        let mut hb = [0u8; HEAD_LEN];
        f.read_exact(&mut hb).ok()?;
        let h = parse_head(&hb, meta).ok()?;
        let total = (HEAD_LEN + h.comp_a + h.comp_q) as u64;
        (f.metadata().ok()?.len() == total).then_some(h.n as u64)
    }

    /// Потолок достигнут, `df` ниже порога или запись не удалась: склад только читается.
    pub fn is_read_only(&self) -> bool {
        self.read_only.load(Ordering::Relaxed)
    }

    pub fn counters(&self) -> StoreCounters {
        StoreCounters {
            hit: self.hit.load(Ordering::Relaxed),
            miss: self.miss.load(Ordering::Relaxed),
            written: self.written.load(Ordering::Relaxed),
            skipped: self.skipped.load(Ordering::Relaxed),
            bad: self.bad.load(Ordering::Relaxed),
            write_err: self.write_err.load(Ordering::Relaxed),
            used_bytes: self.used.load(Ordering::Relaxed),
            qty_bytes: self.qty_bytes.load(Ordering::Relaxed),
            rows: self.rows.load(Ordering::Relaxed),
        }
    }

    pub fn summary(&self) -> String {
        let c = self.counters();
        format!(
            "tick-store: hit {} miss {} written {} skipped {} bad {} write_err {}, объём {:.2} ГБ, из записанного: блок количества {:.2} МБ, строк {}{}",
            c.hit,
            c.miss,
            c.written,
            c.skipped,
            c.bad,
            c.write_err,
            c.used_bytes as f64 / f64::from(1u32 << 30),
            c.qty_bytes as f64 / f64::from(1u32 << 20),
            c.rows,
            if self.read_only.load(Ordering::Relaxed) {
                " (только чтение)"
            } else {
                ""
            }
        )
    }
}
