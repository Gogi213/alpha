//! Минутки Binance → компактный кэш `<SYM>.b42` (один раз) и загрузка в память.
//!
//! Цены — `i64` в единицах 1e-8 (десятичная строка биржи разбирается точно, без `f64`); оборот в котируемой
//! валюте — `i64` в единицах 1e-5. Минута без строки (дыра) — `open == 0`; нулевой объём — обычная минутка.
//! Сетка — минуты с 2026-09-01 00:00 UTC (`T0_MS`) по 2026-11-13 23:59 (`N_MIN` минут).

use super::par_map;
use anyhow::{bail, Context, Result};
use std::fs;
use std::path::{Path, PathBuf};

pub const T0_MS: i64 = 1_788_220_800_000;
pub const MIN_MS: i64 = 60_000;
pub const DAYS_TOTAL: usize = 74;
pub const N_MIN: usize = DAYS_TOTAL * 1440;
/// Первый торгуемый день (15.09) — до него прогрев.
pub const START_DAY: usize = 14;
pub const START_MIN: usize = START_DAY * 1440;
pub const COUNT_DAYS: usize = 60;
pub const HALF_DAYS: usize = 30;
pub const SCALE: i64 = 100_000_000;
pub const NATR_PERIODS: [usize; 3] = [15, 30, 60];
pub const TF_MINUTES: [usize; 3] = [30, 60, 240];
const MAGIC: [u8; 4] = *b"B42\x01";

/// Десятичная строка → целое в единицах `10^-digits` (лишние знаки отбрасываются).
pub fn parse_dec(b: &[u8], digits: usize) -> Option<i64> {
    let (neg, b) = match b.first()? {
        b'-' => (true, &b[1..]),
        _ => (false, b),
    };
    let mut ip: i64 = 0;
    let mut i = 0;
    while i < b.len() && b[i] != b'.' {
        let d = b[i].wrapping_sub(b'0');
        if d > 9 {
            return None;
        }
        ip = ip.checked_mul(10)?.checked_add(i64::from(d))?;
        i += 1;
    }
    let mut frac: i64 = 0;
    let mut nd = 0;
    if i < b.len() {
        i += 1;
        while i < b.len() {
            let d = b[i].wrapping_sub(b'0');
            if d > 9 {
                return None;
            }
            if nd < digits {
                frac = frac * 10 + i64::from(d);
                nd += 1;
            }
            i += 1;
        }
    }
    while nd < digits {
        frac *= 10;
        nd += 1;
    }
    let v = ip
        .checked_mul(10i64.pow(digits as u32))?
        .checked_add(frac)?;
    Some(if neg { -v } else { v })
}

#[derive(Debug, Clone)]
pub struct Cols {
    pub o: Vec<i64>,
    pub h: Vec<i64>,
    pub l: Vec<i64>,
    pub c: Vec<i64>,
    pub qv: Vec<i64>,
}

impl Default for Cols {
    fn default() -> Self {
        Self::new()
    }
}

impl Cols {
    pub fn new() -> Self {
        Cols {
            o: vec![0; N_MIN],
            h: vec![0; N_MIN],
            l: vec![0; N_MIN],
            c: vec![0; N_MIN],
            qv: vec![0; N_MIN],
        }
    }
}

/// Строки дневного файла → столбцы. Возвращает число разобранных строк.
pub fn parse_day(buf: &[u8], cols: &mut Cols) -> Result<usize> {
    let mut rows = 0;
    for line in buf.split(|&c| c == b'\n') {
        let line = line.strip_suffix(b"\r").unwrap_or(line);
        if line.is_empty() || !line[0].is_ascii_digit() {
            continue;
        }
        let mut f = line.split(|&c| c == b',');
        let (Some(ot), Some(o), Some(h), Some(l), Some(c), Some(_), Some(_), Some(qv)) = (
            f.next(),
            f.next(),
            f.next(),
            f.next(),
            f.next(),
            f.next(),
            f.next(),
            f.next(),
        ) else {
            bail!("строка не 8 полей: {}", String::from_utf8_lossy(line));
        };
        let ot = parse_dec(ot, 0).context("open_time")?;
        let idx = (ot - T0_MS) / MIN_MS;
        if ot < T0_MS || idx as usize >= N_MIN {
            continue;
        }
        let i = idx as usize;
        cols.o[i] = parse_dec(o, 8).context("open")?;
        cols.h[i] = parse_dec(h, 8).context("high")?;
        cols.l[i] = parse_dec(l, 8).context("low")?;
        cols.c[i] = parse_dec(c, 8).context("close")?;
        cols.qv[i] = parse_dec(qv, 5).context("quote_volume")?;
        rows += 1;
    }
    Ok(rows)
}

#[derive(Debug, Clone, Copy)]
pub struct Fund {
    pub min: u32,
    pub rate_e8: i64,
    pub mark: i64,
}

fn parse_funding(buf: &[u8]) -> Result<Vec<Fund>> {
    let mut v = Vec::new();
    for line in buf.split(|&c| c == b'\n') {
        let line = line.strip_suffix(b"\r").unwrap_or(line);
        if line.is_empty() || !line[0].is_ascii_digit() {
            continue;
        }
        let mut f = line.split(|&c| c == b',');
        let (Some(t), Some(r), Some(m)) = (f.next(), f.next(), f.next()) else {
            bail!("funding: мало полей");
        };
        let t = parse_dec(t, 0).context("funding_time")?;
        if t < T0_MS {
            continue;
        }
        let min = (t - T0_MS) / MIN_MS;
        if min as usize >= N_MIN {
            continue;
        }
        v.push(Fund {
            min: min as u32,
            rate_e8: parse_dec(r, 8).context("funding_rate")?,
            mark: parse_dec(m, 8).context("mark_price")?,
        });
    }
    v.sort_by_key(|f| f.min);
    Ok(v)
}

fn day_string(d: usize) -> String {
    chrono::NaiveDate::from_ymd_opt(2026, 9, 1)
        .and_then(|b| b.checked_add_days(chrono::Days::new(d as u64)))
        .map(|x| x.format("%Y-%m-%d").to_string())
        .unwrap_or_default()
}

#[derive(Debug, Default, Clone)]
pub struct ConvertStat {
    pub name: String,
    pub rows: usize,
    pub holes_a: usize,
    pub holes_b: usize,
    pub holes_warm: usize,
    pub funding_rows: usize,
}

fn write_cache(path: &Path, cols: &Cols, fund: &[Fund]) -> Result<()> {
    let mut out = Vec::with_capacity(8 + 5 * 8 * N_MIN + 4 + fund.len() * 24);
    out.extend_from_slice(&MAGIC);
    out.extend_from_slice(&(N_MIN as u32).to_le_bytes());
    for col in [&cols.o, &cols.h, &cols.l, &cols.c, &cols.qv] {
        for v in col.iter() {
            out.extend_from_slice(&v.to_le_bytes());
        }
    }
    out.extend_from_slice(&(fund.len() as u32).to_le_bytes());
    for f in fund {
        out.extend_from_slice(&i64::from(f.min).to_le_bytes());
        out.extend_from_slice(&f.rate_e8.to_le_bytes());
        out.extend_from_slice(&f.mark.to_le_bytes());
    }
    fs::write(path, out).with_context(|| format!("запись {}", path.display()))
}

fn convert_one(src: &Path, out: &Path, name: &str) -> Result<ConvertStat> {
    let dir = src.join(name);
    let mut cols = Cols::new();
    let mut rows = 0;
    for d in 0..DAYS_TOTAL {
        let p = dir.join(format!("{}.csv", day_string(d)));
        if let Ok(buf) = fs::read(&p) {
            rows += parse_day(&buf, &mut cols).with_context(|| p.display().to_string())?;
        }
    }
    let fund = match fs::read(dir.join("funding.csv")) {
        Ok(b) => parse_funding(&b)?,
        Err(_) => Vec::new(),
    };
    write_cache(&out.join(format!("{name}.b42")), &cols, &fund)?;
    // дыры — только внутри листинга (между первой и последней минуткой монеты), по частям окна
    let first = cols.o.iter().position(|&x| x != 0);
    let last = cols.o.iter().rposition(|&x| x != 0);
    let mut st = ConvertStat {
        name: name.to_string(),
        rows,
        funding_rows: fund.len(),
        ..Default::default()
    };
    if let (Some(a), Some(b)) = (first, last) {
        for m in a..=b {
            if cols.o[m] == 0 {
                match m / 1440 {
                    d if d < START_DAY => st.holes_warm += 1,
                    d if d < START_DAY + HALF_DAYS => st.holes_a += 1,
                    _ => st.holes_b += 1,
                }
            }
        }
    }
    Ok(st)
}

/// Каталог `<src>/<SYM>/<день>.csv` + `funding.csv` → `<out>/<SYM>.b42`.
pub fn convert(src: &Path, out: &Path, threads: usize) -> Result<Vec<ConvertStat>> {
    fs::create_dir_all(out)?;
    let mut names: Vec<String> = Vec::new();
    for e in fs::read_dir(src)? {
        let e = e?;
        if e.file_type()?.is_dir() {
            if let Some(n) = e.file_name().to_str() {
                names.push(n.to_string());
            }
        }
    }
    names.sort();
    names.retain(|n| {
        src.join(n).join("2026-09-15.csv").exists() || src.join(n).join("2026-10-01.csv").exists()
    });
    let res = par_map(&names, threads, |n| convert_one(src, out, n));
    res.into_iter().collect()
}

pub struct Bars {
    pub o: Vec<i64>,
    pub h: Vec<i64>,
    pub l: Vec<i64>,
    pub c: Vec<i64>,
    pub ok: Vec<bool>,
}

fn build_bars(cols: &Cols, tfm: usize) -> Bars {
    let nb = N_MIN / tfm;
    let mut b = Bars {
        o: vec![0; nb],
        h: vec![0; nb],
        l: vec![0; nb],
        c: vec![0; nb],
        ok: vec![false; nb],
    };
    for i in 0..nb {
        let mut first = true;
        for m in i * tfm..(i + 1) * tfm {
            if cols.o[m] == 0 {
                continue;
            }
            if first {
                b.o[i] = cols.o[m];
                b.h[i] = cols.h[m];
                b.l[i] = cols.l[m];
                first = false;
            } else {
                b.h[i] = b.h[i].max(cols.h[m]);
                b.l[i] = b.l[i].min(cols.l[m]);
            }
            b.c[i] = cols.c[m];
            b.ok[i] = true;
        }
    }
    b
}

/// NATR (ATR/close ×1e8) по `p` последним минутам сетки; простое среднее истинных диапазонов, закрытая минута
/// включена. `-1` — нет значения (мало истории, дыра).
pub fn natr_series(cols: &Cols, p: usize) -> Vec<i32> {
    let n = N_MIN;
    let mut trp = vec![0i64; n + 1];
    let mut cnt = vec![0u32; n + 1];
    let mut prevc = 0i64;
    for t in 0..n {
        let mut tr = 0;
        let mut c = 0;
        if cols.o[t] != 0 {
            tr = cols.h[t] - cols.l[t];
            if prevc != 0 {
                tr = tr
                    .max((cols.h[t] - prevc).abs())
                    .max((cols.l[t] - prevc).abs());
            }
            prevc = cols.c[t];
            c = 1;
        }
        trp[t + 1] = trp[t] + tr;
        cnt[t + 1] = cnt[t] + c;
    }
    let mut out = vec![-1i32; n];
    for t in p - 1..n {
        if cols.o[t] == 0 {
            continue;
        }
        let k = cnt[t + 1] - cnt[t + 1 - p];
        if (k as usize) * 2 < p {
            continue;
        }
        let sum = i128::from(trp[t + 1] - trp[t + 1 - p]);
        let v = sum * i128::from(SCALE) / (i128::from(k) * i128::from(cols.c[t]));
        out[t] = v.min(i128::from(i32::MAX)) as i32;
    }
    out
}

pub struct Sym {
    pub name: String,
    pub o: Vec<i64>,
    pub h: Vec<i64>,
    pub l: Vec<i64>,
    pub c: Vec<i64>,
    pub qv: Vec<i64>,
    /// Префиксные суммы оборота: оборот минут `[a, b)` = `pq[b] - pq[a]`.
    pub pq: Vec<i64>,
    pub natr: [Vec<i32>; 3],
    pub bars: [Bars; 3],
    pub fund: Vec<Fund>,
}

impl Sym {
    pub fn from_cols(name: &str, cols: Cols, fund: Vec<Fund>) -> Sym {
        let mut pq = vec![0i64; N_MIN + 1];
        for t in 0..N_MIN {
            pq[t + 1] = pq[t] + cols.qv[t];
        }
        let natr = [
            natr_series(&cols, NATR_PERIODS[0]),
            natr_series(&cols, NATR_PERIODS[1]),
            natr_series(&cols, NATR_PERIODS[2]),
        ];
        let bars = [
            build_bars(&cols, TF_MINUTES[0]),
            build_bars(&cols, TF_MINUTES[1]),
            build_bars(&cols, TF_MINUTES[2]),
        ];
        Sym {
            name: name.to_string(),
            o: cols.o,
            h: cols.h,
            l: cols.l,
            c: cols.c,
            qv: cols.qv,
            pq,
            natr,
            bars,
            fund,
        }
    }

    pub fn valid(&self, t: usize) -> bool {
        self.o[t] != 0
    }

    pub fn natr_for(&self, period: usize) -> Option<&[i32]> {
        NATR_PERIODS
            .iter()
            .position(|&p| p == period)
            .map(|i| self.natr[i].as_slice())
    }

    pub fn bars_for(&self, tf: usize) -> Option<&Bars> {
        TF_MINUTES
            .iter()
            .position(|&p| p == tf)
            .map(|i| &self.bars[i])
    }
}

fn read_cache(path: &Path) -> Result<(Cols, Vec<Fund>)> {
    let buf = fs::read(path).with_context(|| path.display().to_string())?;
    if buf.len() < 8 || buf[..4] != MAGIC {
        bail!("{}: не .b42", path.display());
    }
    let n = u32::from_le_bytes([buf[4], buf[5], buf[6], buf[7]]) as usize;
    if n != N_MIN {
        bail!("{}: n = {n}, ожидалось {N_MIN}", path.display());
    }
    let mut pos = 8;
    let mut rd = |count: usize| -> Vec<i64> {
        let v: Vec<i64> = buf[pos..pos + count * 8]
            .chunks_exact(8)
            .map(|c| i64::from_le_bytes([c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7]]))
            .collect();
        pos += count * 8;
        v
    };
    let cols = Cols {
        o: rd(n),
        h: rd(n),
        l: rd(n),
        c: rd(n),
        qv: rd(n),
    };
    let nf = u32::from_le_bytes([buf[pos], buf[pos + 1], buf[pos + 2], buf[pos + 3]]) as usize;
    pos += 4;
    let mut fund = Vec::with_capacity(nf);
    for _ in 0..nf {
        let r = |o: usize| {
            i64::from_le_bytes([
                buf[pos + o],
                buf[pos + o + 1],
                buf[pos + o + 2],
                buf[pos + o + 3],
                buf[pos + o + 4],
                buf[pos + o + 5],
                buf[pos + o + 6],
                buf[pos + o + 7],
            ])
        };
        fund.push(Fund {
            min: r(0) as u32,
            rate_e8: r(8),
            mark: r(16),
        });
        pos += 24;
    }
    Ok((cols, fund))
}

/// Все `.b42` каталога → символы по алфавиту (индекс символа = порядок тай-брейка П-42).
pub fn load_all(cache: &Path, threads: usize, only: Option<&[String]>) -> Result<Vec<Sym>> {
    let mut files: Vec<(String, PathBuf)> = Vec::new();
    for e in fs::read_dir(cache)? {
        let p = e?.path();
        if p.extension().and_then(|x| x.to_str()) == Some("b42") {
            if let Some(n) = p.file_stem().and_then(|x| x.to_str()) {
                if only.is_none_or(|o| o.iter().any(|x| x == n)) {
                    files.push((n.to_string(), p.clone()));
                }
            }
        }
    }
    files.sort();
    let res = par_map(&files, threads, |(n, p)| -> Result<Sym> {
        let (cols, fund) = read_cache(p)?;
        Ok(Sym::from_cols(n, cols, fund))
    });
    res.into_iter().collect()
}
