//! Бинарная столбцовая панель минутных свечей `.sap` (TK-479): один файл на символ, столбцы
//! `open`, `close` как `f64` подряд в собственном спане символа; `NaN` — минута без сделок.
//!
//! Заголовок 24 байта: магия `SAP1`, `u32` резерв, `i64` первая минута (индекс от `GRID0_MS`),
//! `u64` число минут. Дальше `n` значений open, потом `n` значений close (LE). Плотный спан даёт
//! ядру прямую индексацию без слияния двух рядов и срезы без копий.

use std::io::{self, BufRead, Read, Write};

/// Начало общей сетки: 2020-01-01 00:00 UTC, мс.
pub const GRID0_MS: i64 = 1_577_836_800_000;
pub const MINUTE_MS: i64 = 60_000;
const MAGIC: [u8; 4] = *b"SAP1";

/// Ряд одного символа: минута `first_min + i` ↔ `open[i]`, `close[i]`; `NaN` — свечи нет.
#[derive(Debug, Clone, PartialEq)]
pub struct Series {
    pub first_min: i64,
    pub open: Vec<f64>,
    pub close: Vec<f64>,
}

impl Series {
    pub fn len(&self) -> usize {
        self.close.len()
    }

    pub fn is_empty(&self) -> bool {
        self.close.is_empty()
    }

    /// Последняя минута спана (включительно).
    pub fn last_min(&self) -> i64 {
        self.first_min + self.len() as i64 - 1
    }

    /// Индексы `[a, b)` общего перекрытия двух рядов в координатах каждого; `None` — нет перекрытия.
    pub fn overlap(&self, other: &Series) -> Option<(usize, usize, usize)> {
        let lo = self.first_min.max(other.first_min);
        let hi = self.last_min().min(other.last_min());
        if hi < lo {
            return None;
        }
        Some((
            (lo - self.first_min) as usize,
            (lo - other.first_min) as usize,
            (hi - lo + 1) as usize,
        ))
    }

    /// `ln(close)` без изменения `NaN`: считается один раз на символ, а не на пару.
    pub fn ln_close(&self) -> Vec<f64> {
        self.close.iter().map(|c| c.ln()).collect()
    }
}

pub fn write_series<W: Write>(w: &mut W, s: &Series) -> io::Result<()> {
    w.write_all(&MAGIC)?;
    w.write_all(&0u32.to_le_bytes())?;
    w.write_all(&s.first_min.to_le_bytes())?;
    w.write_all(&(s.len() as u64).to_le_bytes())?;
    for col in [&s.open, &s.close] {
        let mut buf = Vec::with_capacity(col.len().min(1 << 16) * 8);
        for chunk in col.chunks(1 << 16) {
            buf.clear();
            for v in chunk {
                buf.extend_from_slice(&v.to_le_bytes());
            }
            w.write_all(&buf)?;
        }
    }
    Ok(())
}

fn read_col<R: Read>(r: &mut R, n: usize) -> io::Result<Vec<f64>> {
    let mut out = Vec::with_capacity(n);
    let mut buf = vec![0u8; (1 << 16) * 8];
    let mut left = n;
    while left > 0 {
        let take = left.min(1 << 16);
        r.read_exact(&mut buf[..take * 8])?;
        out.extend(
            buf[..take * 8]
                .chunks_exact(8)
                .map(|b| f64::from_le_bytes(b.try_into().expect("8 байт"))),
        );
        left -= take;
    }
    Ok(out)
}

pub fn read_series<R: Read>(r: &mut R) -> io::Result<Series> {
    let mut h = [0u8; 24];
    r.read_exact(&mut h)?;
    if h[..4] != MAGIC {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "не панель SAP1"));
    }
    let first_min = i64::from_le_bytes(h[8..16].try_into().expect("8 байт"));
    let n = u64::from_le_bytes(h[16..24].try_into().expect("8 байт")) as usize;
    let open = read_col(r, n)?;
    let close = read_col(r, n)?;
    Ok(Series {
        first_min,
        open,
        close,
    })
}

fn bad(msg: String) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, msg)
}

fn price(f: &str, line: usize) -> io::Result<f64> {
    let v: f64 = f
        .trim()
        .parse()
        .map_err(|_| bad(format!("строка {line}: цена не число: {f:?}")))?;
    if v.is_finite() && v > 0.0 {
        Ok(v)
    } else {
        Err(bad(format!("строка {line}: цена не положительна: {v}")))
    }
}

/// Свечи csv (`t_open_ms,o,h,l,c,...`, с заголовком или без) → панель. Строки строго по возрастанию
/// минут, каждая — на границе минуты; нарушение — ошибка, не молчаливая правка. Пустой csv → `None`.
/// Дыры между строками — `NaN`, ничего не выдумывается.
pub fn convert_csv<R: BufRead>(csv: R) -> io::Result<Option<Series>> {
    let mut s: Option<Series> = None;
    let mut last_idx = i64::MIN;
    for (i, line) in csv.lines().enumerate() {
        let line = line?;
        let mut f = line.split(',');
        let (Some(t), Some(o), Some(_h), Some(_l), Some(c)) =
            (f.next(), f.next(), f.next(), f.next(), f.next())
        else {
            if line.trim().is_empty() {
                continue;
            }
            return Err(bad(format!("строка {}: меньше 5 полей", i + 1)));
        };
        let Ok(t_ms) = t.trim().parse::<i64>() else {
            if i == 0 {
                continue; // заголовок
            }
            return Err(bad(format!("строка {}: время не число: {t:?}", i + 1)));
        };
        let rel = t_ms - GRID0_MS;
        if rel < 0 || rel % MINUTE_MS != 0 {
            return Err(bad(format!(
                "строка {}: время {t_ms} не на минуте сетки",
                i + 1
            )));
        }
        let idx = rel / MINUTE_MS;
        if idx <= last_idx {
            return Err(bad(format!("строка {}: минуты не по возрастанию", i + 1)));
        }
        let (o, c) = (price(o, i + 1)?, price(c, i + 1)?);
        let ser = s.get_or_insert_with(|| Series {
            first_min: idx,
            open: Vec::new(),
            close: Vec::new(),
        });
        let at = (idx - ser.first_min) as usize;
        ser.open.resize(at, f64::NAN);
        ser.close.resize(at, f64::NAN);
        ser.open.push(o);
        ser.close.push(c);
        last_idx = idx;
    }
    Ok(s)
}
