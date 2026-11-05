//! Бинарный кэш контракта `.pmc` (TK-492): разбор csv.gz один раз, дальше прогоны идут с диска/памяти.
//!
//! Заголовок 40 байт: магия `PMC2`, `u32` резерв, `i64` первая минута, `u64` число минут, `i64` первый слот
//! OI (5 мин от `GRID0_MS`), `u64` число слотов. Затем столбцы LE подряд: минутные `close` (`f64`), `vol`,
//! `qvol`, `trades`, `high`, `low` (`f32`); слотовые `OI_COLS` (`f64`); число ставок фандинга `u64` и пары
//! `(i64, f64)`. Версия 2 добавила `high`/`low` (нужны NATR, `play.rs`); файлы `PMC1` читаются с ошибкой
//! «кэш старого формата, пересоберите» и считаются устаревшими (`is_stale`) — `alpha pumponset cache` их заменяет.

use crate::statarb::panel::{bad, price, GRID0_MS, MINUTE_MS};
use std::fs::File;
use std::io::{self, BufRead, Read, Write};
use std::path::Path;

const MAGIC: [u8; 4] = *b"PMC2";
/// Магия прежнего формата (без `high`/`low`): читать нельзя, пересобрать.
const MAGIC_V1: [u8; 4] = *b"PMC1";
pub const SLOT_MIN: i64 = 5;
/// Колонки OI в порядке хранения; имена — как в csv (без учёта регистра).
pub const OI_COLS: [&str; 6] = [
    "open_interest",
    "open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
];
const OI_TIME: [&str; 4] = ["t_ms", "time_ms", "timestamp", "time"];

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Contract {
    pub first_min: i64,
    pub close: Vec<f64>,
    pub vol: Vec<f32>,
    pub qvol: Vec<f32>,
    pub trades: Vec<f32>,
    /// `high` / `low` минуты (свечи `h`, `l`); `NaN` в минутах без сделок (v = 0) и в дырах.
    pub high: Vec<f32>,
    pub low: Vec<f32>,
    pub oi_first_slot: i64,
    /// `oi[k][i]` — колонка `OI_COLS[k]` на слоте `oi_first_slot + i`; `NaN` — дыра.
    pub oi: [Vec<f64>; 6],
    /// `(минута сетки выплаты, ставка)`.
    pub funding: Vec<(i64, f64)>,
}

impl Contract {
    /// `close` с переносом вперёд (нет свечи / v = 0 → прежний close, как TK-442); до первого close — `NaN`.
    pub fn close_ff(&self) -> Vec<f64> {
        let mut last = f64::NAN;
        self.close
            .iter()
            .map(|&x| {
                if !x.is_nan() {
                    last = x;
                }
                last
            })
            .collect()
    }
}

/// Счётчики разбора свечей: строки и строки без сделок (v = 0 → close NaN).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct CandleStats {
    pub rows: u64,
    pub zero_volume: u64,
}

fn num(f: &str, line: usize, what: &str) -> io::Result<f64> {
    let v: f64 = f
        .trim()
        .parse()
        .map_err(|_| bad(format!("строка {line}: {what} не число: {f:?}")))?;
    if v.is_finite() && v >= 0.0 {
        Ok(v)
    } else {
        Err(bad(format!(
            "строка {line}: {what} отрицательно или не конечно: {v}"
        )))
    }
}

/// Свечи `t_open_ms,o,h,l,c,v,quote_v,n_trades` (заголовок есть/нет) → колонки минут. Строго по возрастанию,
/// каждая на границе минуты; нарушение — ошибка. Дыры и v = 0 → `close`, `high`, `low` = `NaN`, объёмы 0 (цены `h`, `l`
/// проверяются как `o`, `c`: положительные, конечные). Пустой csv → `None`.
pub fn parse_candles<R: BufRead>(csv: R) -> io::Result<(Option<Contract>, CandleStats)> {
    parse_candles_with(csv, false)
}

/// `parse_candles`; `lenient` — пустое поле `n_trades` (Bybit его не отдаёт) читается как 0 вместо ошибки.
// столбцы объёмов, числа сделок, `high`, `low` хранятся в `f32` (формат `.pmc`); бюджет линтов (TK-211) не растим
#[allow(clippy::cast_possible_truncation)]
pub fn parse_candles_with<R: BufRead>(
    csv: R,
    lenient: bool,
) -> io::Result<(Option<Contract>, CandleStats)> {
    let mut st = CandleStats::default();
    let mut c: Option<Contract> = None;
    let mut last = i64::MIN;
    for (i, line) in csv.lines().enumerate() {
        let line = line?;
        let n = i + 1;
        let f: Vec<&str> = line.split(',').collect();
        if f.len() < 8 {
            if line.trim().is_empty() {
                continue;
            }
            return Err(bad(format!("строка {n}: меньше 8 полей")));
        }
        let Ok(t_ms) = f[0].trim().parse::<i64>() else {
            if i == 0 {
                continue;
            }
            return Err(bad(format!("строка {n}: время не число: {:?}", f[0])));
        };
        let rel = t_ms - GRID0_MS;
        if rel < 0 || rel % MINUTE_MS != 0 {
            return Err(bad(format!("строка {n}: время {t_ms} не на минуте сетки")));
        }
        let idx = rel / MINUTE_MS;
        if idx <= last {
            return Err(bad(format!("строка {n}: минуты не по возрастанию")));
        }
        last = idx;
        st.rows += 1;
        let v = num(f[5], n, "v")?;
        let q = num(f[6], n, "quote_v")?;
        let tr = if lenient && f[7].trim().is_empty() {
            0.0
        } else {
            num(f[7], n, "n_trades")?
        };
        let (close, hi, lo) = if v == 0.0 {
            st.zero_volume += 1;
            (f64::NAN, f64::NAN, f64::NAN)
        } else {
            price(f[1], n)?;
            let h = price(f[2], n)?;
            let l = price(f[3], n)?;
            (price(f[4], n)?, h, l)
        };
        let k = c.get_or_insert_with(|| Contract {
            first_min: idx,
            ..Default::default()
        });
        let at = (idx - k.first_min) as usize;
        k.close.resize(at, f64::NAN);
        k.vol.resize(at, 0.0);
        k.qvol.resize(at, 0.0);
        k.trades.resize(at, 0.0);
        k.high.resize(at, f32::NAN);
        k.low.resize(at, f32::NAN);
        k.close.push(close);
        k.vol.push(v as f32);
        k.qvol.push(q as f32);
        k.trades.push(tr as f32);
        k.high.push(hi as f32);
        k.low.push(lo as f32);
    }
    Ok((c, st))
}

/// OI-статистика (заголовок обязателен: время и `OI_COLS` по имени, регистр не важен, лишние колонки
/// игнорируются) → колонки слотов. Пустое поле — `NaN`; не число/отрицательное — ошибка; слоты не по
/// возрастанию — ошибка. Строка с меткой не на границе 5 мин (секундный джиттер архива, 8 контрактов Binance)
/// пропускается как дыра (`NaN`, ничего не дорисовывается и не округляется) и считается отдельно. Пропущенные слоты — `NaN`.
pub fn parse_oi<R: BufRead>(csv: R, into: &mut Contract) -> io::Result<u64> {
    parse_oi_counted(csv, into).map(|(rows, _)| rows)
}

/// `parse_oi` с числом пропущенных строк не на сетке: `(принято строк, пропущено не на сетке)`.
pub fn parse_oi_counted<R: BufRead>(csv: R, into: &mut Contract) -> io::Result<(u64, u64)> {
    parse_oi_with(csv, into, false)
}

/// `parse_oi_counted`; `lenient` — отсутствующие в файле колонки, кроме `open_interest`, остаются `NaN`
/// (Bybit отдаёт только `open_interest`), а не ошибка.
pub fn parse_oi_with<R: BufRead>(
    csv: R,
    into: &mut Contract,
    lenient: bool,
) -> io::Result<(u64, u64)> {
    let mut lines = csv.lines();
    let head = lines
        .next()
        .ok_or_else(|| bad("OI: пустой файл".into()))??;
    let names: Vec<String> = head
        .split(',')
        .map(|s| s.trim().to_ascii_lowercase())
        .collect();
    let find = |keys: &[&str]| names.iter().position(|n| keys.contains(&n.as_str()));
    let tcol = find(&OI_TIME).ok_or_else(|| bad(format!("OI: нет колонки времени {OI_TIME:?}")))?;
    let mut cols = [usize::MAX; 6];
    for (k, name) in OI_COLS.iter().enumerate() {
        cols[k] = match find(&[name]) {
            Some(i) => i,
            None if lenient && k > 0 => usize::MAX,
            None => return Err(bad(format!("OI: нет колонки {name}"))),
        };
    }
    let need = cols
        .iter()
        .copied()
        .filter(|&c| c != usize::MAX)
        .chain([tcol])
        .max()
        .unwrap_or(0);
    let step = SLOT_MIN * MINUTE_MS;
    let (mut rows, mut off_grid, mut last) = (0u64, 0u64, i64::MIN);
    for (i, line) in lines.enumerate() {
        let line = line?;
        let n = i + 2;
        if line.trim().is_empty() {
            continue;
        }
        let f: Vec<&str> = line.split(',').collect();
        if f.len() <= need {
            return Err(bad(format!("строка {n}: меньше {} полей", need + 1)));
        }
        let t_ms: i64 = f[tcol]
            .trim()
            .parse()
            .map_err(|_| bad(format!("строка {n}: время не число: {:?}", f[tcol])))?;
        let rel = t_ms - GRID0_MS;
        if rel < 0 {
            return Err(bad(format!("строка {n}: время {t_ms} раньше начала сетки")));
        }
        if rel % step != 0 {
            off_grid += 1;
            continue;
        }
        let slot = rel / step;
        if slot <= last {
            return Err(bad(format!("строка {n}: слоты не по возрастанию")));
        }
        last = slot;
        if rows == 0 {
            into.oi_first_slot = slot;
        }
        let at = (slot - into.oi_first_slot) as usize;
        for (k, col) in into.oi.iter_mut().enumerate() {
            col.resize(at, f64::NAN);
            if cols[k] == usize::MAX {
                col.push(f64::NAN);
                continue;
            }
            let s = f[cols[k]].trim();
            col.push(if s.is_empty() {
                f64::NAN
            } else {
                num(s, n, OI_COLS[k])?
            });
        }
        rows += 1;
    }
    Ok((rows, off_grid))
}

fn w_all<W: Write, T: Copy, const N: usize>(
    w: &mut W,
    v: &[T],
    to: impl Fn(T) -> [u8; N],
) -> io::Result<()> {
    let mut buf = Vec::with_capacity(8192 * N);
    for chunk in v.chunks(8192) {
        buf.clear();
        for &x in chunk {
            buf.extend_from_slice(&to(x));
        }
        w.write_all(&buf)?;
    }
    Ok(())
}

/// Столбец ровно в `n` значений: короче — добивается `NaN` (контракт без `high`/`low`, собранный не из csv),
/// длиннее — обрезается; формат файла требует по `n` значений на каждый минутный столбец.
fn padded(v: &[f32], n: usize) -> Vec<f32> {
    let mut out: Vec<f32> = v.iter().copied().take(n).collect();
    out.resize(n, f32::NAN);
    out
}

pub fn write_contract<W: Write>(w: &mut W, c: &Contract) -> io::Result<()> {
    w.write_all(&MAGIC)?;
    w.write_all(&0u32.to_le_bytes())?;
    w.write_all(&c.first_min.to_le_bytes())?;
    w.write_all(&(c.close.len() as u64).to_le_bytes())?;
    w.write_all(&c.oi_first_slot.to_le_bytes())?;
    w.write_all(&(c.oi[0].len() as u64).to_le_bytes())?;
    w_all(w, &c.close, f64::to_le_bytes)?;
    for col in [&c.vol, &c.qvol, &c.trades] {
        w_all(w, col, f32::to_le_bytes)?;
    }
    for col in [&c.high, &c.low] {
        w_all(w, &padded(col, c.close.len()), f32::to_le_bytes)?;
    }
    for col in &c.oi {
        w_all(w, col, f64::to_le_bytes)?;
    }
    w.write_all(&(c.funding.len() as u64).to_le_bytes())?;
    for (m, r) in &c.funding {
        w.write_all(&m.to_le_bytes())?;
        w.write_all(&r.to_le_bytes())?;
    }
    Ok(())
}

fn r_all<R: Read, T, const N: usize>(
    r: &mut R,
    n: usize,
    from: impl Fn([u8; N]) -> T,
) -> io::Result<Vec<T>> {
    let mut out = Vec::with_capacity(n);
    let mut buf = vec![0u8; 8192 * N];
    let mut left = n;
    while left > 0 {
        let take = left.min(8192);
        r.read_exact(&mut buf[..take * N])?;
        out.extend(
            buf[..take * N]
                .chunks_exact(N)
                .map(|b| from(b.try_into().expect("N байт"))),
        );
        left -= take;
    }
    Ok(out)
}

fn r8<R: Read>(r: &mut R) -> io::Result<[u8; 8]> {
    let mut b = [0u8; 8];
    r.read_exact(&mut b)?;
    Ok(b)
}

pub fn read_contract<R: Read>(r: &mut R) -> io::Result<Contract> {
    let mut h = [0u8; 40];
    r.read_exact(&mut h)?;
    if h[..4] == MAGIC_V1 {
        return Err(bad(
            "кэш старого формата PMC1 (нет high/low), пересоберите: alpha pumponset cache".into(),
        ));
    }
    if h[..4] != MAGIC {
        return Err(bad("не кэш PMC2".into()));
    }
    let i64_at = |a: usize| i64::from_le_bytes(h[a..a + 8].try_into().expect("8 байт"));
    let (first_min, n, oi_first_slot, slots) = (
        i64_at(8),
        i64_at(16) as usize,
        i64_at(24),
        i64_at(32) as usize,
    );
    let mut c = Contract {
        first_min,
        oi_first_slot,
        close: r_all(r, n, f64::from_le_bytes)?,
        vol: r_all(r, n, f32::from_le_bytes)?,
        qvol: r_all(r, n, f32::from_le_bytes)?,
        trades: r_all(r, n, f32::from_le_bytes)?,
        high: r_all(r, n, f32::from_le_bytes)?,
        low: r_all(r, n, f32::from_le_bytes)?,
        ..Default::default()
    };
    for col in c.oi.iter_mut() {
        *col = r_all(r, slots, f64::from_le_bytes)?;
    }
    let nf = u64::from_le_bytes(r8(r)?) as usize;
    for _ in 0..nf {
        let m = i64::from_le_bytes(r8(r)?);
        let rate = f64::from_le_bytes(r8(r)?);
        c.funding.push((m, rate));
    }
    Ok(c)
}

/// Событийный сброс: кэш устарел, если его нет, он старого формата (магия не `PMC2`) или он старше любого из
/// источников.
pub fn is_stale(cache: &Path, sources: &[&Path]) -> bool {
    let mtime = |p: &Path| std::fs::metadata(p).and_then(|m| m.modified()).ok();
    let Some(cm) = mtime(cache) else {
        return true;
    };
    let mut magic = [0u8; 4];
    if File::open(cache)
        .and_then(|mut f| f.read_exact(&mut magic))
        .is_err()
        || magic != MAGIC
    {
        return true;
    }
    sources.iter().any(|s| mtime(s).is_some_and(|t| t > cm))
}
