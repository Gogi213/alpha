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

use crate::binlog::{parse_calendar_day, Header, Record, Writer};
use crate::book::Side;
use crate::bybit::ws::{parse_e9, parse_message, Event};
use crate::commands::record::{
    day_file_path, FRAME_TARGET_RECORDS, MAX_RECORDS_PER_FRAME, ZSTD_LEVEL,
};

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
    /// Шаги цены и количества суток — по первому снимку суток (НОД цен и размеров, не крупнее
    /// шага пула): Bybit менял шаг в течение года, а усечение по одному шагу пула слепляет цены
    /// (TK-035). Нет флага — шаги пула, как раньше. Любое значение вне сетки — отказ.
    #[arg(long)]
    pub steps_from_snapshot: bool,
    /// Как `--steps-from-snapshot`, но НОД берётся по всем уровням суток (снимки и дельты): нужен
    /// там, где Bybit сменил шаг внутри суток (TK-037, 112 монето-суток). Требует `--ob` файлом.
    #[arg(long)]
    pub steps_from_day: bool,
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
    /// Цены/размеры стакана, не кратные шагам файла (усечение их исказило бы).
    pub off_grid: u64,
    /// Сделки с ценой/размером мельче сетки стакана (улучшение цены RPI) — усекаются, как раньше.
    pub trade_off_grid: u64,
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
fn gcd(a: i64, b: i64) -> i64 {
    let (mut a, mut b) = (a.abs(), b.abs());
    while b != 0 {
        (a, b) = (b, a % b);
    }
    a
}

type Lines = Box<dyn Iterator<Item = std::io::Result<String>>>;

/// Шаги суток по первому снимку: НОД шага пула и НОД цен/размеров снимка — не крупнее пула, поэтому
/// там, где шаг архива равен или крупнее шага пула, результат тот же, что у прежнего импорта.
/// Прочитанные строки возвращаются, чтобы разбор пошёл с начала.
fn steps_from_snapshot(
    lines: &mut Lines,
    pool_tick_e9: i64,
    pool_step_e9: i64,
) -> anyhow::Result<(i64, i64, Vec<String>)> {
    let mut seen = Vec::new();
    for line in lines.by_ref() {
        let line = line?;
        let events = if line.trim().is_empty() {
            Vec::new()
        } else {
            parse_message(&zero_negative_seq(&line))
                .map_err(|e| anyhow::anyhow!("сообщение стакана: {e:?}"))?
        };
        seen.push(line);
        for ev in events {
            let Event::Book(u) = ev else { continue };
            if !u.is_snapshot {
                continue;
            }
            let (mut gp, mut gq) = (0, 0);
            for &(p, q) in u.bids.iter().chain(u.asks.iter()) {
                gp = gcd(gp, p);
                gq = gcd(gq, q);
            }
            return Ok((gcd(pool_tick_e9, gp), gcd(pool_step_e9, gq), seen));
        }
    }
    Ok((pool_tick_e9, pool_step_e9, seen))
}

/// Шаги суток по всем уровням после первого снимка до конца суток: одна сетка на файл, пригодная и
/// при смене шага биржей внутри суток. Второе число пары — НОД только снимков (для сравнения).
fn steps_from_day(
    path: &Path,
    day_end: i64,
    pool_tick_e9: i64,
    pool_step_e9: i64,
) -> anyhow::Result<((i64, i64), (i64, i64))> {
    let rdr = BufReader::with_capacity(1 << 20, std::fs::File::open(path)?);
    let (mut gp, mut gq, mut sp, mut sq) = (0, 0, 0, 0);
    let mut has_snapshot = false;
    for line in rdr.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let ts = serde_json::from_str::<PublishTs>(&line)
            .map_err(|e| anyhow::anyhow!("строка стакана без ts: {e}"))?
            .ts;
        if ts >= day_end {
            continue;
        }
        let events = parse_message(&zero_negative_seq(&line))
            .map_err(|e| anyhow::anyhow!("сообщение стакана: {e:?}"))?;
        for ev in events {
            let Event::Book(u) = ev else { continue };
            has_snapshot |= u.is_snapshot;
            if !has_snapshot {
                continue;
            }
            for &(p, q) in u.bids.iter().chain(u.asks.iter()) {
                gp = gcd(gp, p);
                gq = gcd(gq, q);
                if u.is_snapshot {
                    sp = gcd(sp, p);
                    sq = gcd(sq, q);
                }
            }
        }
    }
    Ok((
        (gcd(pool_tick_e9, gp), gcd(pool_step_e9, gq)),
        (gcd(pool_tick_e9, sp), gcd(pool_step_e9, sq)),
    ))
}

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

/// В день листинга первый (пустой) снимок несёт `"seq":-1` — `u64` его не разбирает (TK-021: 19
/// монето-суток, `BadShape("orderbook")`). `seq` в импорте не участвует (контроль — по `u`), поэтому
/// отрицательное значение заменяется нулём; правка только в импорте, живой разбор не затронут.
fn zero_negative_seq(line: &str) -> std::borrow::Cow<'_, str> {
    const KEY: &str = "\"seq\":-";
    let Some(at) = line.find(KEY) else {
        return std::borrow::Cow::Borrowed(line);
    };
    let digits = at + KEY.len();
    let end = line[digits..]
        .find(|c: char| !c.is_ascii_digit())
        .map_or(line.len(), |n| digits + n);
    std::borrow::Cow::Owned(format!("{}\"seq\":0{}", &line[..at], &line[end..]))
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
    let (pool_tick_e9, pool_step_e9) = steps(&args.instruments, &args.symbol)?;
    let day = parse_calendar_day(&args.day)
        .ok_or_else(|| anyhow::anyhow!("--day {}: ожидается YYYY-MM-DD", args.day))?;
    let day_start = day
        .and_hms_opt(0, 0, 0)
        .ok_or_else(|| anyhow::anyhow!("--day: полночь не строится"))?
        .and_utc()
        .timestamp_millis();
    let day_end = day_start + DAY_MS;
    std::fs::create_dir_all(&args.root)?;
    let out_path = day_file_path(&args.root, &args.symbol, &args.day, 1);
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
    let mut lines: Lines = Box::new(reader.lines());
    let (tick_e9, step_e9) = if args.steps_from_day {
        anyhow::ensure!(
            args.ob.as_os_str() != "-",
            "--steps-from-day: --ob должен быть файлом (нужен второй проход)"
        );
        let ((t, st), (snap_t, snap_st)) =
            steps_from_day(&args.ob, day_end, pool_tick_e9, pool_step_e9)?;
        eprintln!(
            "import-archive: {} {} — шаг суток по всем уровням: цена {t} e9, размер {st} e9; по снимкам: {snap_t}/{snap_st} (пул {pool_tick_e9}/{pool_step_e9})",
            args.symbol, args.day
        );
        (t, st)
    } else if args.steps_from_snapshot {
        let (t, st, seen) = steps_from_snapshot(&mut lines, pool_tick_e9, pool_step_e9)?;
        lines = Box::new(seen.into_iter().map(Ok).chain(lines));
        eprintln!(
            "import-archive: {} {} — шаг цены {t} e9 (пул {pool_tick_e9}), шаг размера {st} e9 (пул {pool_step_e9})",
            args.symbol, args.day
        );
        (t, st)
    } else {
        (pool_tick_e9, pool_step_e9)
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
        off_grid: 0,
        trade_off_grid: 0,
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
    for line in lines {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let ts = serde_json::from_str::<PublishTs>(&line)
            .map_err(|e| anyhow::anyhow!("строка стакана без ts: {e}"))?
            .ts;
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
                sum.trade_off_grid +=
                    u64::from(t.price_e9 % tick_e9 != 0 || t.qty_e9 % step_e9 != 0);
                sum.trades += 1;
            } else {
                sum.trades_before_snapshot += 1;
            }
        }
        let line = zero_negative_seq(&line);
        let events =
            parse_message(&line).map_err(|e| anyhow::anyhow!("сообщение стакана: {e:?}"))?;
        if ts < day_start {
            // Снимок начала суток архив иногда публикует на миллисекунды раньше полуночи
            // (TK-021: UNI, ATOM, BCH — 13 монето-суток), за ним идут дельты суток с тем же
            // потоком `u`: такой снимок — начало суток, а не «чужие» сообщения. Всё остальное до
            // полуночи пропускается, как прежде.
            let starts_day = events
                .iter()
                .any(|e| matches!(e, Event::Book(u) if u.is_snapshot));
            if !has_snapshot && !starts_day {
                continue;
            }
        }
        // Время получения записи не раньше начала суток: файл суток не выходит за них.
        let ts = ts.max(day_start);
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
                    sum.off_grid += u64::from(price_e9 % tick_e9 != 0 || qty_e9 % step_e9 != 0);
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
            sum.trade_off_grid += u64::from(t.price_e9 % tick_e9 != 0 || t.qty_e9 % step_e9 != 0);
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
    if (args.steps_from_snapshot || args.steps_from_day) && sum.off_grid > 0 {
        let _ = std::fs::remove_file(&tmp_path);
        anyhow::bail!(
            "{} {}: {} значений не кратны шагам суток (шаг цены {tick_e9} e9, размера {step_e9} e9) — файл не записан",
            args.symbol,
            args.day,
            sum.off_grid
        );
    }
    std::fs::rename(&tmp_path, &out_path)?;
    Ok(sum)
}

#[cfg(test)]
mod tests;
