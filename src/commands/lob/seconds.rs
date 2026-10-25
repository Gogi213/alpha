//! `lob seconds` — посекундный ряд монето-суток (G-015 / TK-358, протокол
//! `docs/research/g015-event-scan-spec-2026-10-22.md` §2): середина, спред, поток
//! сделок и дисбаланс книги по секундам UTC. Один проход книги на сутки — классы
//! событий и пороги считает питон поверх ряда, в Rust их нет.
//!
//! Книга и сделки читаются тем же кодом, что реплей (`Reader` → `FileReplayer` →
//! `Book::apply`, сделки — `trade_hit_from_record`); срез книги берётся после
//! каждого обновления, как `MidSample` реплея. Все значения целые (А1).
//!
//! Строка суток всегда 86 400; секунда `t_s` = `[t_s·1000, (t_s+1)·1000)` мс.
//! «Состояние на метку T» — последнее применённое обновление с `cts_ms <= T`
//! (ровно `sample_asof`): `mid2` — на `(t_s+1)·1000`, `mid2_100` — на
//! `t_s·1000 + 100`. `spr_max` — максимум спреда по состоянию на начало секунды
//! и обновлениям в `(t_s·1000, (t_s+1)·1000]`; `gap = 1`, если обновлений книги
//! с двумя сторонами в этом полуинтервале не было (значения — прежнее состояние).
//! До первого среза суток `mid2 = mid2_100 = spr = … = 0`, `gap = 1`.
//!
//! Сделки (без блочных, RPI и непозитивных — как `minute_flow.trade_lots`)
//! кладутся в секунду по `exch_ts` (`trade_hit_from_record`); сделки вне суток —
//! в счётчик `dropped`. Номинал сделки — `price_tick·tick_e9·lots·step_e9`
//! (10⁻¹⁸ $) точно; в строку идёт разность `floor` накопленной за сутки суммы,
//! так что `Σ tb_usd = floor(Σ точных номиналов)` без дрейфа округления.
//!
//! Части суток (`-pN`) читаются подряд как один поток; К1 — маркер сверки
//! `verify-<SYMBOL>.status == ok`, как у `trades` (`--allow-unverified` снимает).
//!
//! `--bbo-events` (TK-399, G-016) — вместо посекундного ряда событийный BBO: строка
//! `t_ms,bid,ask,bq,aq` на каждое изменение лучших бид/аск или их размеров (тики и
//! лоты, целые). Состояние на метку T — последняя строка с `t_ms <= T` (`searchsorted`
//! по `t_ms`, любая задержка 0/100/250/500/1000 мс без потери). Одна мс — одна строка
//! (последнее состояние мс); `t_ms` не убывает и лежит в `[начало суток, +86 400 000)`:
//! обновления до полуночи схлопываются в её начало (состояние на начало суток), после
//! конца суток — отбрасываются. Нет одной из сторон → у неё `0,0` (до первого
//! обновления строк нет). Сделки в этом режиме не читаются в строки.

use std::io::Write;
use std::path::{Path, PathBuf};

use clap::Args;

use crate::binlog::Reader;
use crate::book::{Book, Side};
use crate::bybit::verify::FileReplayer;
use crate::lob::levels::TradeHit;
use crate::lob::markout::mid_double_tick;

use super::parts::{day_of_filename, read_frame_soft};
use super::replay::trade_hit_from_record;

#[cfg(test)]
mod tests;

const DAY_SECS: usize = 86_400;
/// 10¹⁸: номинал в единицах 10⁻¹⁸ $ → доллары.
const SCALE: i128 = 1_000_000_000_000_000_000;

pub(crate) const SECONDS_COLUMNS: [&str; 13] = [
    "t_s", "mid2", "mid2_100", "spr", "spr_max", "tb_usd", "ts_usd", "nb", "ns", "obi5", "obi20",
    "d_usd5", "gap",
];

#[derive(Debug, Args)]
pub struct SecondsArgs {
    /// Корень записи: суточные файлы `<SYMBOL>-*.binlog`.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Символ, например `SOLUSDT`.
    #[arg(long)]
    pub symbol: String,
    /// Сутки UTC `YYYY-MM-DD` (повторяемый флаг); пусто — все сутки записи.
    #[arg(long = "day")]
    pub days: Vec<String>,
    /// Каталог результата: `<out>/<SYMBOL>-<день>.csv` (по умолчанию `<root>/seconds`).
    #[arg(long)]
    pub out: Option<PathBuf>,
    /// Снять требование маркера сверки (отладочные данные).
    #[arg(long, default_value_t = false)]
    pub allow_unverified: bool,
    /// Событийный BBO (`t_ms,bid,ask,bq,aq`) вместо посекундного ряда; по умолчанию
    /// каталог результата — `<root>/bbo`.
    #[arg(long, default_value_t = false)]
    pub bbo_events: bool,
}

#[derive(Debug)]
pub struct SecondsSummary {
    pub days: usize,
    /// Сделок, попавших в строки.
    pub trades: u64,
    /// Сделок вне суток файла (по `exch_ts`) — в ряд не вошли.
    pub dropped: u64,
    /// Секунд с `gap = 1`.
    pub gap_secs: u64,
    /// Строк событийного BBO (`--bbo-events`), иначе 0.
    pub bbo_rows: u64,
    pub out_dir: PathBuf,
}

/// Срез книги на конец секунды.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub(crate) struct Snap {
    pub mid2: i64,
    pub spr: i64,
    pub obi5: i64,
    pub obi20: i64,
    pub d_usd5: i64,
}

/// `10⁶·(b−a)/(b+a)`, ppm, деление к нулю; пустая книга — 0.
fn obi_ppm(b: i128, a: i128) -> i64 {
    let tot = b + a;
    if tot <= 0 {
        return 0;
    }
    i64::try_from(1_000_000 * (b - a) / tot).unwrap_or(0)
}

/// Срез по книге; `None`, если нет одной из сторон (как у `MidSample`).
pub(crate) fn snap_of(book: &Book, tick_e9: i64, step_e9: i64) -> Option<Snap> {
    let bid = book.best_bid_tick_opt()?;
    let ask = book.best_ask_tick_opt()?;
    let unit = tick_e9 as i128 * step_e9 as i128;
    let mut n5 = [0i128; 2];
    let mut n20 = [0i128; 2];
    for (s, side) in [Side::Bid, Side::Ask].into_iter().enumerate() {
        for (i, (tick, lots)) in book.levels(side).take(20).enumerate() {
            let v = tick as i128 * lots as i128 * unit;
            n20[s] += v;
            if i < 5 {
                n5[s] += v;
            }
        }
    }
    Some(Snap {
        mid2: mid_double_tick(bid, ask),
        spr: ask - bid,
        obi5: obi_ppm(n5[0], n5[1]),
        obi20: obi_ppm(n20[0], n20[1]),
        d_usd5: i64::try_from((n5[0] + n5[1]) / SCALE).unwrap_or(i64::MAX),
    })
}

#[derive(Debug, Clone, Copy, Default)]
struct SecRow {
    snap: Snap,
    mid2_100: i64,
    spr_max: i64,
    tb: i128,
    ts: i128,
    nb: u64,
    ns: u64,
    gap: bool,
}

/// Строка CSV: 13 целых в порядке `SECONDS_COLUMNS`.
pub(crate) type OutRow = [i64; 13];

/// Накопитель одних суток.
pub(crate) struct DayAcc {
    start_ms: i64,
    rows: Vec<SecRow>,
    cur: Option<Snap>,
    run_max: i64,
    seen: bool,
    next_end: usize,
    next_100: usize,
    pub trades: u64,
    pub dropped: u64,
}

impl DayAcc {
    pub(crate) fn new(start_ms: i64) -> Self {
        Self {
            start_ms,
            rows: vec![SecRow::default(); DAY_SECS],
            cur: None,
            run_max: 0,
            seen: false,
            next_end: 0,
            next_100: 0,
            trades: 0,
            dropped: 0,
        }
    }

    fn close_second(&mut self) {
        let k = self.next_end;
        let s = self.cur.unwrap_or_default();
        if let Some(r) = self.rows.get_mut(k) {
            r.snap = s;
            r.spr_max = if self.cur.is_some() {
                self.run_max.max(s.spr)
            } else {
                0
            };
            r.gap = !self.seen;
        }
        self.seen = false;
        self.run_max = s.spr;
        self.next_end += 1;
    }

    /// Применённое обновление книги с двумя сторонами на `ts_ms`.
    pub(crate) fn on_snap(&mut self, ts_ms: i64, snap: Snap) {
        let rel = ts_ms - self.start_ms;
        let mid_now = self.cur.map_or(0, |s| s.mid2);
        while self.next_100 < DAY_SECS && (self.next_100 as i64) * 1000 + 100 < rel {
            self.rows[self.next_100].mid2_100 = mid_now;
            self.next_100 += 1;
        }
        while self.next_end < DAY_SECS && (self.next_end as i64 + 1) * 1000 < rel {
            self.close_second();
        }
        self.cur = Some(snap);
        self.run_max = self.run_max.max(snap.spr);
        if self.next_end < DAY_SECS && rel > (self.next_end as i64) * 1000 {
            self.seen = true;
        }
    }

    pub(crate) fn on_trade(&mut self, hit: TradeHit, tick_e9: i64, step_e9: i64) {
        if hit.block || hit.rpi || hit.lots <= 0 {
            return;
        }
        let idx = (hit.exch_ms - self.start_ms).div_euclid(1000);
        let Some(row) = usize::try_from(idx).ok().and_then(|i| self.rows.get_mut(i)) else {
            self.dropped += 1;
            return;
        };
        let v = hit.tick as i128 * tick_e9 as i128 * hit.lots as i128 * step_e9 as i128;
        if hit.aggressor_is_buy {
            row.tb += v;
            row.nb += 1;
        } else {
            row.ts += v;
            row.ns += 1;
        }
        self.trades += 1;
    }

    /// Закрывает сутки: хвост секунд — прежнее состояние; строки CSV.
    pub(crate) fn finish(mut self) -> Vec<OutRow> {
        let mid_now = self.cur.map_or(0, |s| s.mid2);
        while self.next_100 < DAY_SECS {
            self.rows[self.next_100].mid2_100 = mid_now;
            self.next_100 += 1;
        }
        while self.next_end < DAY_SECS {
            self.close_second();
        }
        let day_s = self.start_ms.div_euclid(1000);
        let (mut cum_b, mut cum_s, mut prev_b, mut prev_s) = (0i128, 0i128, 0i128, 0i128);
        let mut out = Vec::with_capacity(DAY_SECS);
        for (k, r) in self.rows.iter().enumerate() {
            cum_b += r.tb;
            cum_s += r.ts;
            let (fb, fs) = (cum_b / SCALE, cum_s / SCALE);
            let (tb, ts) = (fb - prev_b, fs - prev_s);
            (prev_b, prev_s) = (fb, fs);
            out.push([
                day_s + k as i64,
                r.snap.mid2,
                r.mid2_100,
                r.snap.spr,
                r.spr_max,
                i64::try_from(tb).unwrap_or(i64::MAX),
                i64::try_from(ts).unwrap_or(i64::MAX),
                r.nb as i64,
                r.ns as i64,
                r.snap.obi5,
                r.snap.obi20,
                r.snap.d_usd5,
                i64::from(r.gap),
            ]);
        }
        out
    }
}

/// Приёмник событий потока: срез книги после обновления и сделка.
pub(crate) trait Feed {
    fn on_book(
        &mut self,
        ts_ms: i64,
        book: &Book,
        tick_e9: i64,
        step_e9: i64,
    ) -> anyhow::Result<()>;
    fn on_trade(&mut self, hit: TradeHit, tick_e9: i64, step_e9: i64);
}

impl Feed for DayAcc {
    fn on_book(
        &mut self,
        ts_ms: i64,
        book: &Book,
        tick_e9: i64,
        step_e9: i64,
    ) -> anyhow::Result<()> {
        if let Some(s) = snap_of(book, tick_e9, step_e9) {
            self.on_snap(ts_ms, s);
        }
        Ok(())
    }

    fn on_trade(&mut self, hit: TradeHit, tick_e9: i64, step_e9: i64) {
        DayAcc::on_trade(self, hit, tick_e9, step_e9);
    }
}

const DAY_MS: i64 = DAY_SECS as i64 * 1000;

pub(crate) const BBO_COLUMNS: [&str; 5] = ["t_ms", "bid", "ask", "bq", "aq"];

/// Лучшие бид/аск (тики) и их размеры (лоты); сторона без уровней — `0, 0`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Bbo {
    bid: i64,
    ask: i64,
    bq: i64,
    aq: i64,
}

impl Bbo {
    fn of(book: &Book) -> Self {
        let bid = book.best_bid_tick_opt();
        let ask = book.best_ask_tick_opt();
        Self {
            bid: bid.unwrap_or(0),
            ask: ask.unwrap_or(0),
            bq: bid.map_or(0, |t| book.qty_lots_at(Side::Bid, t)),
            aq: ask.map_or(0, |t| book.qty_lots_at(Side::Ask, t)),
        }
    }
}

/// Событийный BBO одних суток (`--bbo-events`): строки пишутся потоком, в памяти — одна.
pub(crate) struct BboAcc<W: Write> {
    start_ms: i64,
    w: csv::Writer<W>,
    pending: Option<(i64, Bbo)>,
    last: Option<Bbo>,
    pub rows: u64,
}

impl<W: Write> BboAcc<W> {
    pub(crate) fn new(start_ms: i64, w: W) -> anyhow::Result<Self> {
        let mut w = csv::Writer::from_writer(w);
        w.write_record(BBO_COLUMNS)?;
        Ok(Self {
            start_ms,
            w,
            pending: None,
            last: None,
            rows: 0,
        })
    }

    /// Состояние мс закрыто: строка, если оно отличается от последней записанной.
    fn flush_pending(&mut self) -> anyhow::Result<()> {
        if let Some((t, s)) = self.pending.take() {
            if self.last != Some(s) {
                self.w
                    .write_record([t, s.bid, s.ask, s.bq, s.aq].map(|v| v.to_string()))?;
                self.last = Some(s);
                self.rows += 1;
            }
        }
        Ok(())
    }

    pub(crate) fn finish(mut self) -> anyhow::Result<u64> {
        self.flush_pending()?;
        self.w.flush()?;
        Ok(self.rows)
    }
}

impl<W: Write> Feed for BboAcc<W> {
    fn on_book(
        &mut self,
        ts_ms: i64,
        book: &Book,
        _tick_e9: i64,
        _step_e9: i64,
    ) -> anyhow::Result<()> {
        if ts_ms - self.start_ms >= DAY_MS {
            return Ok(());
        }
        let t = ts_ms
            .max(self.start_ms)
            .max(self.pending.map_or(0, |p| p.0));
        let s = Bbo::of(book);
        match &mut self.pending {
            Some((pt, ps)) if *pt == t => *ps = s,
            _ => {
                self.flush_pending()?;
                self.pending = Some((t, s));
            }
        }
        Ok(())
    }

    fn on_trade(&mut self, _hit: TradeHit, _tick_e9: i64, _step_e9: i64) {}
}

fn day_start_ms(day: &str) -> anyhow::Result<i64> {
    let d = crate::binlog::parse_calendar_day(day)
        .ok_or_else(|| anyhow::anyhow!("сутки {day:?} не разбираются"))?;
    d.and_hms_opt(0, 0, 0)
        .map(|dt| dt.and_utc().timestamp_millis())
        .ok_or_else(|| anyhow::anyhow!("сутки {day:?}: полночь не строится"))
}

/// Один файл-часть суток в накопитель (реплей: новая книга на файл; разрыв
/// последовательности останавливает файл, как в `replay`/`verify`).
fn feed_file<F: Feed>(path: &Path, acc: &mut F) -> anyhow::Result<()> {
    let data = std::fs::read(path)
        .map_err(|e| anyhow::anyhow!("файл {} не читается: {e}", path.display()))?;
    let mut reader = Reader::open(&data[..])
        .map_err(|e| anyhow::anyhow!("заголовок {}: {e:?}", path.display()))?;
    let header = reader.header();
    let (tick_e9, step_e9) = (header.tick_e9, header.step_e9);
    let mut book = Book::new(tick_e9, step_e9);
    let mut replayer = FileReplayer::new();
    let (mut ups, mut tps) = (Vec::new(), Vec::new());
    let mut ok = true;
    'frames: while let Some(records) = read_frame_soft(&mut reader, path)? {
        for rec in &records {
            ups.clear();
            tps.clear();
            replayer.push_frame(
                std::slice::from_ref(rec),
                tick_e9,
                step_e9,
                &mut ups,
                &mut tps,
            );
            for up in &ups {
                if book.apply(up).is_err() {
                    ok = false;
                    break 'frames;
                }
                acc.on_book(up.cts_ms, &book, tick_e9, step_e9)?;
            }
            if let Some(h) = trade_hit_from_record(rec) {
                acc.on_trade(h, tick_e9, step_e9);
            }
        }
    }
    if ok {
        let mut tail = Vec::new();
        replayer.finish(&mut tail);
        for up in &tail {
            if book.apply(up).is_err() {
                break;
            }
            acc.on_book(up.cts_ms, &book, tick_e9, step_e9)?;
        }
    }
    Ok(())
}

pub fn run_seconds(args: &SecondsArgs) -> anyhow::Result<SecondsSummary> {
    super::require_verified(&args.root, &args.symbol, args.allow_unverified)?;
    let paths = super::session_binlog_for(&args.root, &args.symbol)?;
    anyhow::ensure!(
        !paths.is_empty(),
        "{}: нет суточных файлов {}",
        args.root.display(),
        args.symbol
    );
    let prefix = format!("{}-", args.symbol);
    let mut by_day: Vec<(String, Vec<PathBuf>)> = Vec::new();
    for path in paths {
        let name = path
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        let day = day_of_filename(&prefix, &name)
            .ok_or_else(|| anyhow::anyhow!("имя {name} не разбирается как сутки"))?;
        if !args.days.is_empty() && !args.days.iter().any(|d| d == &day) {
            continue;
        }
        match by_day.last_mut() {
            Some((d, v)) if *d == day => v.push(path),
            _ => by_day.push((day, vec![path])),
        }
    }
    anyhow::ensure!(!by_day.is_empty(), "нет суток под --day");
    let out_dir = args.out.clone().unwrap_or_else(|| {
        args.root
            .join(if args.bbo_events { "bbo" } else { "seconds" })
    });
    std::fs::create_dir_all(&out_dir)?;
    let (mut trades, mut dropped, mut gap_secs, mut bbo_rows) = (0u64, 0u64, 0u64, 0u64);
    for (day, parts) in &by_day {
        if args.bbo_events {
            let path = out_dir.join(format!("{}-{day}.csv", args.symbol));
            let file = std::io::BufWriter::new(std::fs::File::create(&path)?);
            let mut acc = BboAcc::new(day_start_ms(day)?, file)?;
            for p in parts {
                feed_file(p, &mut acc)?;
            }
            bbo_rows += acc.finish()?;
            continue;
        }
        let mut acc = DayAcc::new(day_start_ms(day)?);
        for p in parts {
            feed_file(p, &mut acc)?;
        }
        trades += acc.trades;
        dropped += acc.dropped;
        let rows = acc.finish();
        gap_secs += rows.iter().filter(|r| r[12] == 1).count() as u64;
        let path = out_dir.join(format!("{}-{day}.csv", args.symbol));
        let mut w = csv::Writer::from_path(&path)?;
        w.write_record(SECONDS_COLUMNS)?;
        for r in &rows {
            w.write_record(r.map(|v| v.to_string()))?;
        }
        w.flush()?;
    }
    Ok(SecondsSummary {
        days: by_day.len(),
        trades,
        dropped,
        gap_secs,
        bbo_rows,
        out_dir,
    })
}
