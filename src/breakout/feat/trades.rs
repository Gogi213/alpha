//! Сутки сделок монеты для признаков: `(t мс, v = price·quantity, покупка тейкера)` — как `tk594_feat.read_trades_day`.
//!
//! Источник — `trait DaySrc`; сейчас `ZipDaySrc` (внешний `unzip -p`, как `tick::ZipSrc` движка), склад TK-607 подключается
//! отдельной реализацией без правок признаков. Порядок записей — как в файле; при убывании времени — устойчивая сортировка
//! (Python: `np.argsort(kind="stable")`). `price` и `quantity` — `f64` прямо из десятичной записи (pandas-конвертер), а не ×1e8.

use super::pdcompat::{date_str, parse_f64, parse_i64};
use anyhow::{bail, Context, Result};
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

#[derive(Debug, Default, Clone, PartialEq)]
pub struct DayTrades {
    /// Время сделки, мс от 1970-01-01 (`transact_time`).
    pub t: Vec<i64>,
    /// Оборот записи `price · quantity` (одно умножение `f64`).
    pub v: Vec<f64>,
    /// Агрессор — покупатель (`is_buyer_maker` ≠ true).
    pub buy: Vec<bool>,
}

/// Источник суточных файлов сделок: `None` — файла нет.
pub trait DaySrc: Sync {
    fn day(&self, sym: &str, abs_day: i64) -> Result<Option<DayTrades>>;
}

/// `<корень>/<SYM>/<SYM>-aggTrades-<день>.zip`, корни — по порядку, берётся первый существующий файл.
pub struct ZipDaySrc {
    pub roots: Vec<PathBuf>,
}

impl DaySrc for ZipDaySrc {
    fn day(&self, sym: &str, abs_day: i64) -> Result<Option<DayTrades>> {
        let name = format!("{sym}-aggTrades-{}", date_str(abs_day));
        for root in &self.roots {
            let path = root.join(sym).join(format!("{name}.zip"));
            if !path.exists() {
                continue;
            }
            let mut child = Command::new("unzip")
                .arg("-p")
                .arg(&path)
                .arg(format!("{name}.csv"))
                .stdout(Stdio::piped())
                .stderr(Stdio::null())
                .spawn()
                .with_context(|| format!("unzip {}", path.display()))?;
            let out = child.stdout.take().context("unzip: нет stdout")?;
            let parsed = parse_trades_csv(BufReader::with_capacity(1 << 20, out));
            let status = child.wait()?;
            let d = parsed.with_context(|| path.display().to_string())?;
            if !status.success() {
                bail!("unzip {}: {status}", path.display());
            }
            return Ok(Some(d));
        }
        Ok(None)
    }
}

fn find_col(head: &[&[u8]], name: &str) -> Result<usize> {
    head.iter()
        .position(|h| *h == name.as_bytes())
        .with_context(|| format!("нет столбца {name}"))
}

/// `is_buyer_maker`: pandas даёт bool для `true/True/TRUE` и объектный столбец иначе; итог один — `lower() == "true"`.
fn is_true(b: &[u8]) -> bool {
    b.eq_ignore_ascii_case(b"true")
}

/// CSV суток (`agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker`) → сделки.
pub fn parse_trades_csv(mut rd: impl BufRead) -> Result<DayTrades> {
    let mut line = Vec::with_capacity(256);
    if rd.read_until(b'\n', &mut line)? == 0 {
        return Ok(DayTrades::default());
    }
    let head: Vec<&[u8]> = trim_eol(&line).split(|&c| c == b',').collect();
    let (ip, iq, it, ib) = (
        find_col(&head, "price")?,
        find_col(&head, "quantity")?,
        find_col(&head, "transact_time")?,
        find_col(&head, "is_buyer_maker")?,
    );
    let need = ip.max(iq).max(it).max(ib) + 1;
    let mut d = DayTrades::default();
    loop {
        line.clear();
        if rd.read_until(b'\n', &mut line)? == 0 {
            break;
        }
        let l = trim_eol(&line);
        if l.is_empty() {
            continue;
        }
        let (mut p, mut q, mut t, mut b) = (&l[..0], &l[..0], &l[..0], &l[..0]);
        let mut cnt = 0;
        for (i, f) in l.split(|&c| c == b',').enumerate() {
            if i == ip {
                p = f;
            } else if i == iq {
                q = f;
            } else if i == it {
                t = f;
            } else if i == ib {
                b = f;
            }
            cnt = i + 1;
        }
        if cnt < need {
            bail!("строка с {cnt} полями: {:?}", String::from_utf8_lossy(l));
        }
        d.t.push(parse_i64(t)?);
        d.v.push(parse_f64(p)? * parse_f64(q)?);
        d.buy.push(!is_true(b));
    }
    if d.t.len() > 1 && d.t.windows(2).any(|w| w[1] < w[0]) {
        let mut ord: Vec<usize> = (0..d.t.len()).collect();
        ord.sort_by_key(|&i| d.t[i]);
        d.t = ord.iter().map(|&i| d.t[i]).collect();
        d.v = ord.iter().map(|&i| d.v[i]).collect();
        d.buy = ord.iter().map(|&i| d.buy[i]).collect();
    }
    Ok(d)
}

fn trim_eol(l: &[u8]) -> &[u8] {
    let mut e = l.len();
    while e > 0 && (l[e - 1] == b'\n' || l[e - 1] == b'\r') {
        e -= 1;
    }
    &l[..e]
}

/// Небольшой CSV из zip (`metrics`, `premium`, `fundingRate`): весь текст в памяти, столбцы — по именам.
pub struct Csv {
    buf: Vec<u8>,
}

impl Csv {
    /// `unzip -p <zip> <inner>`; ошибка — если файла/участника нет.
    pub fn from_zip(path: &Path, inner: &str) -> Result<Csv> {
        let mut child = Command::new("unzip")
            .arg("-p")
            .arg(path)
            .arg(inner)
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .with_context(|| format!("unzip {}", path.display()))?;
        let mut buf = Vec::new();
        child.stdout.take().context("unzip: нет stdout")?.read_to_end(&mut buf)?;
        let status = child.wait()?;
        if !status.success() {
            bail!("unzip {} {inner}: {status}", path.display());
        }
        Ok(Csv { buf })
    }

    pub fn from_file(path: &Path) -> Result<Csv> {
        Ok(Csv { buf: std::fs::read(path).with_context(|| path.display().to_string())? })
    }

    #[cfg(test)]
    pub fn from_bytes(b: &[u8]) -> Csv {
        Csv { buf: b.to_vec() }
    }

    /// Строки как срезы полей нужных столбцов (в порядке `names`).
    pub fn columns<'a>(&'a self, names: &[&str]) -> Result<Vec<Vec<&'a [u8]>>> {
        let mut lines = self.buf.split(|&c| c == b'\n').map(trim_eol);
        let Some(h) = lines.next() else {
            return Ok(names.iter().map(|_| Vec::new()).collect());
        };
        let head: Vec<&[u8]> = h.split(|&c| c == b',').collect();
        let idx: Vec<usize> = names.iter().map(|n| find_col(&head, n)).collect::<Result<_>>()?;
        let mut out: Vec<Vec<&[u8]>> = names.iter().map(|_| Vec::new()).collect();
        for l in lines {
            if l.is_empty() {
                continue;
            }
            let f: Vec<&[u8]> = l.split(|&c| c == b',').collect();
            for (o, &i) in out.iter_mut().zip(&idx) {
                o.push(*f.get(i).with_context(|| format!("короткая строка: {:?}", String::from_utf8_lossy(l)))?);
            }
        }
        Ok(out)
    }
}
