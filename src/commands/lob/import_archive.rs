//! `lob import-archive` — сутки публичного архива Bybit → суточный бинлог v3 (эпоха «история»,
//! владелец 2026-09-22: «данные исторические за текущий месяц, кроме уже заколлекченных»).
//!
//! Архив стакана `quote-saver.bycsi.com/orderbook/linear/<SYM>/<день>_<SYM>_ob200.data.zip` — это
//! поток WS `orderbook.200` как есть (снимок в 00:00, дельты, снимок в 00:00 следующих суток;
//! сверка M21 на 21.09: номера `u` подряд, середина и касания совпадают с нашей записью). Поэтому
//! строка архива разбирается **тем же** `bybit::ws::parse_message`, что и живое сообщение, а записи
//! строятся так же, как у коллектора (`session::sink::write_market_event`): цена — в тиках, размер —
//! в шагах количества, `exch_ts` — время матчинга `cts`. Сделки — CSV `public.bybit.com/trading`
//! (`timestamp,symbol,side,size,price,…,RPI`): агрессор — `side`, RPI — последняя колонка, блочных
//! сделок в выгрузке нет (`block = false`).
//!
//! Чего в архиве нет и чем это заменено (всё названо, не спрятано):
//! - **времени приёма** (`local_ts`): у стакана берётся время публикации биржи `ts`, у сделки —
//!   время исполнения `T`; сеть до нас (единицы мс) не добавляется — шаг потока `.200` 100 мс;
//! - **быстрого потока `.50`**: основной файл эпохи несёт `.200` — шаг 100 мс вместо 20 мс (M21).
//!
//! Файл пишется в `<root>/<SYMBOL>-<день>.binlog`; существующий файл **не перезаписывается** —
//! иначе импорт мог бы затереть нашу собственную запись тех же суток.

use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};

use clap::Args;
use hftbacktest::types::{
    LOCAL_ASK_DEPTH_EVENT, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_EVENT,
    LOCAL_BID_DEPTH_SNAPSHOT_EVENT, LOCAL_BUY_TRADE_EVENT, LOCAL_SELL_TRADE_EVENT,
};

use crate::binlog::{Header, Record, Writer};
use crate::book::Side;
use crate::bybit::ws::{parse_e9, parse_message, Event};
use crate::commands::record::{FRAME_TARGET_RECORDS, MAX_RECORDS_PER_FRAME, ZSTD_LEVEL};

const DAY_MS: i64 = 86_400_000;

/// Аргументы `lob import-archive`.
#[derive(Debug, Args)]
pub struct ImportArchiveArgs {
    /// Инструмент, например `WIFUSDT`.
    #[arg(long)]
    pub symbol: String,
    /// Сутки UTC `YYYY-MM-DD`.
    #[arg(long)]
    pub day: String,
    /// Поток стакана — распакованный `<день>_<SYM>_ob200.data` (строка — сообщение WS); `-` — stdin.
    #[arg(long)]
    pub ob: PathBuf,
    /// Сделки — распакованный CSV `public.bybit.com/trading/<SYM>/<SYM><день>.csv.gz`.
    #[arg(long)]
    pub trades: PathBuf,
    /// Пул с шагами цены и количества (`instruments.csv`: `symbol,tick_size,…,qty_step,…`).
    #[arg(long)]
    pub instruments: PathBuf,
    /// Корень эпохи: файл `<root>/<SYMBOL>-<день>.binlog`.
    #[arg(long)]
    pub root: PathBuf,
}

/// Итог импорта суток.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ImportSummary {
    pub out: PathBuf,
    pub messages: u64,
    pub snapshots: u64,
    /// Разрывы `u` в дельтах (у полного потока — ноль).
    pub u_gaps: u64,
    pub trades: u64,
    /// Сделки до первого снимка суток — не пишутся (как у коллектора: файл начинается снимком).
    pub trades_before_snapshot: u64,
    /// Сообщения стакана за концом суток (снимок 00:00 следующих суток) — не пишутся.
    pub dropped_after_day: u64,
    pub records: u64,
}

struct Trade {
    ms: i64,
    price_e9: i64,
    qty_e9: i64,
    buy: bool,
    rpi: bool,
}

fn depth_flags(side: Side, snapshot: bool) -> u64 {
    match (side, snapshot) {
        (Side::Bid, true) => LOCAL_BID_DEPTH_SNAPSHOT_EVENT,
        (Side::Bid, false) => LOCAL_BID_DEPTH_EVENT,
        (Side::Ask, true) => LOCAL_ASK_DEPTH_SNAPSHOT_EVENT,
        (Side::Ask, false) => LOCAL_ASK_DEPTH_EVENT,
    }
}

/// Шаги цены и количества символа из `instruments.csv`, в 1e-9.
fn steps(instruments: &Path, symbol: &str) -> anyhow::Result<(i64, i64)> {
    let mut rdr = csv::Reader::from_path(instruments)?;
    let head = rdr.headers()?.clone();
    let col = |name: &str| {
        head.iter()
            .position(|h| h == name)
            .ok_or_else(|| anyhow::anyhow!("{}: нет колонки {name}", instruments.display()))
    };
    let (i_sym, i_tick, i_step) = (col("symbol")?, col("tick_size")?, col("qty_step")?);
    for row in rdr.records() {
        let row = row?;
        if row.get(i_sym) == Some(symbol) {
            let tick = parse_e9(row.get(i_tick).unwrap_or(""))
                .ok_or_else(|| anyhow::anyhow!("{symbol}: tick_size не число"))?;
            let step = parse_e9(row.get(i_step).unwrap_or(""))
                .ok_or_else(|| anyhow::anyhow!("{symbol}: qty_step не число"))?;
            anyhow::ensure!(tick > 0 && step > 0, "{symbol}: шаги обязаны быть > 0");
            return Ok((tick, step));
        }
    }
    anyhow::bail!("{symbol}: нет в {}", instruments.display())
}

/// Время сделки выгрузки (`1789948800.4366` — секунды с долями) → мс, доли отбрасываются.
fn trade_ms(s: &str) -> Option<i64> {
    let (sec, frac) = s.split_once('.').unwrap_or((s, ""));
    let sec: i64 = sec.parse().ok()?;
    let mut ms = 0i64;
    for (i, c) in frac.chars().take(3).enumerate() {
        let d = i64::from(c.to_digit(10)?);
        ms += d * [100, 10, 1][i];
    }
    Some(sec.checked_mul(1000)? + ms)
}

/// Десятичное число выгрузки сделок → 1e-9 без `f64`. Выгрузка пишет крупные и мелкие величины в
/// экспоненциальной записи (`1.1283e+06`, замер 22.09 на AKE/DOGE/PUMPFUN 01.09), поэтому мантисса
/// разбирается `parse_e9`, а порядок применяется целочисленно; значение точнее 1e-9 — отказ.
fn parse_decimal_e9(s: &str) -> Option<i64> {
    let s = s.trim();
    let Some((mant, exp)) = s.split_once(['e', 'E']) else {
        return parse_e9(s);
    };
    let mut v = i128::from(parse_e9(mant)?);
    let exp: i32 = exp.trim_start_matches('+').parse().ok()?;
    if exp >= 0 {
        for _ in 0..exp {
            v = v.checked_mul(10)?;
        }
    } else {
        for _ in 0..exp.unsigned_abs() {
            if v % 10 != 0 {
                return None;
            }
            v /= 10;
        }
    }
    i64::try_from(v).ok()
}

fn read_trades(path: &Path) -> anyhow::Result<Vec<Trade>> {
    let mut rdr = csv::Reader::from_path(path)?;
    let head = rdr.headers()?.clone();
    let col = |name: &str| head.iter().position(|h| h == name);
    let i_ts = col("timestamp").ok_or_else(|| anyhow::anyhow!("сделки: нет timestamp"))?;
    let i_side = col("side").ok_or_else(|| anyhow::anyhow!("сделки: нет side"))?;
    let i_size = col("size").ok_or_else(|| anyhow::anyhow!("сделки: нет size"))?;
    let i_price = col("price").ok_or_else(|| anyhow::anyhow!("сделки: нет price"))?;
    let i_rpi = col("RPI");
    let mut out = Vec::new();
    for (n, row) in rdr.records().enumerate() {
        let row = row?;
        let bad = || anyhow::anyhow!("{}: строка {} не разбирается", path.display(), n + 2);
        out.push(Trade {
            ms: trade_ms(row.get(i_ts).ok_or_else(bad)?).ok_or_else(bad)?,
            price_e9: parse_decimal_e9(row.get(i_price).ok_or_else(bad)?).ok_or_else(bad)?,
            qty_e9: parse_decimal_e9(row.get(i_size).ok_or_else(bad)?).ok_or_else(bad)?,
            buy: match row.get(i_side) {
                Some("Buy") => true,
                Some("Sell") => false,
                _ => return Err(bad()),
            },
            rpi: i_rpi
                .and_then(|i| row.get(i))
                .is_some_and(|v| v == "1" || v == "true"),
        });
    }
    // Выгрузка идёт по времени; устойчивая сортировка — страховка, порядок равных сохраняется.
    out.sort_by_key(|t| t.ms);
    Ok(out)
}

#[derive(serde::Deserialize)]
struct PublishTs {
    ts: i64,
}

struct Out<W: std::io::Write> {
    writer: Writer<W>,
    batch: Vec<Record>,
    records: u64,
}

impl<W: std::io::Write> Out<W> {
    fn flush(&mut self) -> anyhow::Result<()> {
        if !self.batch.is_empty() {
            self.writer.write_frame(&self.batch)?;
            self.records += self.batch.len() as u64;
            self.batch.clear();
        }
        Ok(())
    }
}

/// Импорт одних суток. Читает поток стакана построчно и сливает с ним сделки по времени
/// (стакан — по `ts`, сделка — по `T`), так что порядок записей в файле — порядок поступления.
pub fn run_import_archive(args: &ImportArchiveArgs) -> anyhow::Result<ImportSummary> {
    let (tick_e9, step_e9) = steps(&args.instruments, &args.symbol)?;
    let day = chrono::NaiveDate::parse_from_str(&args.day, "%Y-%m-%d")
        .map_err(|_| anyhow::anyhow!("--day {}: ожидается YYYY-MM-DD", args.day))?;
    let day_start = day
        .and_hms_opt(0, 0, 0)
        .ok_or_else(|| anyhow::anyhow!("--day: полночь не строится"))?
        .and_utc()
        .timestamp_millis();
    let day_end = day_start + DAY_MS;
    std::fs::create_dir_all(&args.root)?;
    let out_path = args
        .root
        .join(format!("{}-{}.binlog", args.symbol, args.day));
    anyhow::ensure!(
        !out_path.exists(),
        "{}: файл уже есть — импорт не перезаписывает записанные сутки",
        out_path.display()
    );
    let trades = read_trades(&args.trades)?;
    let reader: Box<dyn BufRead> = if args.ob.as_os_str() == "-" {
        Box::new(BufReader::new(std::io::stdin().lock()))
    } else {
        Box::new(BufReader::with_capacity(
            1 << 20,
            std::fs::File::open(&args.ob)?,
        ))
    };
    let header = Header {
        tick_e9,
        step_e9,
        max_records_per_frame: MAX_RECORDS_PER_FRAME,
    };
    let tmp_path = out_path.with_extension("binlog.part");
    let file = std::io::BufWriter::new(std::fs::File::create(&tmp_path)?);
    let mut out = Out {
        writer: Writer::create(file, header, ZSTD_LEVEL)?,
        batch: Vec::with_capacity(FRAME_TARGET_RECORDS + 512),
        records: 0,
    };
    let mut sum = ImportSummary {
        out: out_path.clone(),
        messages: 0,
        snapshots: 0,
        u_gaps: 0,
        trades: 0,
        trades_before_snapshot: 0,
        dropped_after_day: 0,
        records: 0,
    };
    let mut has_snapshot = false;
    let mut last_u: Option<u64> = None;
    let mut ti = 0usize;
    let push_trade = |t: &Trade, out: &mut Out<_>| {
        let ts = t.ms.saturating_mul(1_000_000);
        out.batch.push(Record {
            ev: if t.buy {
                LOCAL_BUY_TRADE_EVENT
            } else {
                LOCAL_SELL_TRADE_EVENT
            },
            exch_ts_ns: ts,
            local_ts_ns: ts,
            price_ticks: t.price_e9 / tick_e9,
            qty_lots: t.qty_e9 / step_e9,
            block: false,
            rpi: t.rpi,
        });
    };
    for line in reader.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let ts = serde_json::from_str::<PublishTs>(&line)
            .map_err(|e| anyhow::anyhow!("строка стакана без ts: {e}"))?
            .ts;
        if ts < day_start {
            continue;
        }
        if ts >= day_end {
            sum.dropped_after_day += 1;
            continue;
        }
        // Сделки, исполненные раньше публикации этого сообщения, — перед ним.
        while ti < trades.len() && trades[ti].ms < ts {
            let t = &trades[ti];
            ti += 1;
            if t.ms < day_start || t.ms >= day_end {
                continue;
            }
            if has_snapshot {
                push_trade(t, &mut out);
                sum.trades += 1;
            } else {
                sum.trades_before_snapshot += 1;
            }
        }
        let events =
            parse_message(&line).map_err(|e| anyhow::anyhow!("сообщение стакана: {e:?}"))?;
        for ev in events {
            let Event::Book(update) = ev else { continue };
            sum.messages += 1;
            if update.is_snapshot {
                sum.snapshots += 1;
                has_snapshot = true;
                // Снимок — своим кадром, как у коллектора: накопленные записи уходят первыми.
                out.flush()?;
            } else {
                if !has_snapshot {
                    continue;
                }
                if last_u.is_some_and(|u| update.u != u + 1) {
                    sum.u_gaps += 1;
                }
            }
            last_u = Some(update.u);
            let exch_ts_ns = update.cts_ms.saturating_mul(1_000_000);
            let local_ts_ns = ts.saturating_mul(1_000_000);
            for (side, levels) in [(Side::Bid, &update.bids), (Side::Ask, &update.asks)] {
                for &(price_e9, qty_e9) in levels {
                    out.batch.push(Record {
                        ev: depth_flags(side, update.is_snapshot),
                        exch_ts_ns,
                        local_ts_ns,
                        price_ticks: price_e9 / tick_e9,
                        qty_lots: qty_e9 / step_e9,
                        block: false,
                        rpi: false,
                    });
                }
            }
            if update.is_snapshot || out.batch.len() >= FRAME_TARGET_RECORDS {
                out.flush()?;
            }
        }
    }
    // Хвост сделок суток после последнего сообщения стакана.
    while ti < trades.len() {
        let t = &trades[ti];
        ti += 1;
        if t.ms < day_start || t.ms >= day_end {
            continue;
        }
        if has_snapshot {
            push_trade(t, &mut out);
            sum.trades += 1;
        } else {
            sum.trades_before_snapshot += 1;
        }
    }
    out.flush()?;
    sum.records = out.records;
    let mut w = out.writer.into_inner();
    std::io::Write::flush(&mut w)?;
    drop(w);
    anyhow::ensure!(
        has_snapshot,
        "{}: в потоке стакана нет снимка суток",
        args.symbol
    );
    std::fs::rename(&tmp_path, &out_path)?;
    Ok(sum)
}

#[cfg(test)]
mod tests;
