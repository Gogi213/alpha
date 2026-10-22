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

use std::path::{Path, PathBuf};

use clap::Args;

use crate::binlog::Reader;
use crate::book::{Book, Side};
use crate::bybit::verify::FileReplayer;
use crate::lob::levels::TradeHit;
use crate::lob::levels::{H3Mode, LevelsConfig};
use crate::lob::markout::{mid_double_tick, sample_asof};

use super::parts::{day_of_filename, read_frame_soft};
use super::replay::{replay_symbol_over_configs_keep, trade_hit_from_record, ReplayKeep};

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
    /// Гейт (б) протокола: после записи сверить `mid2`/`mid2_100` с `sample_asof` по срезам
    /// реплея (второй проход книги; ограничь `--root` одними сутками). Расхождение — отказ.
    #[arg(long, default_value_t = false)]
    pub check_mids: bool,
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
    /// Расхождений `mid2`/`mid2_100` с `sample_asof` (0 без `--check-mids`).
    pub mid_mismatch: u64,
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

fn day_start_ms(day: &str) -> anyhow::Result<i64> {
    let d = crate::binlog::parse_calendar_day(day)
        .ok_or_else(|| anyhow::anyhow!("сутки {day:?} не разбираются"))?;
    d.and_hms_opt(0, 0, 0)
        .map(|dt| dt.and_utc().timestamp_millis())
        .ok_or_else(|| anyhow::anyhow!("сутки {day:?}: полночь не строится"))
}

/// Один файл-часть суток в накопитель (реплей: новая книга на файл; разрыв
/// последовательности останавливает файл, как в `replay`/`verify`).
fn feed_file(path: &Path, acc: &mut DayAcc) -> anyhow::Result<()> {
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
                if let Some(s) = snap_of(&book, tick_e9, step_e9) {
                    acc.on_snap(up.cts_ms, s);
                }
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
            if let Some(s) = snap_of(&book, tick_e9, step_e9) {
                acc.on_snap(up.cts_ms, s);
            }
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
    let out_dir = args
        .out
        .clone()
        .unwrap_or_else(|| args.root.join("seconds"));
    std::fs::create_dir_all(&out_dir)?;
    let (mut trades, mut dropped, mut gap_secs, mut mid_mismatch) = (0u64, 0u64, 0u64, 0u64);
    let replay = if args.check_mids {
        let cfg = LevelsConfig {
            mode: H3Mode::Percentile { h3_lots: 5 },
            warmup_ms: 0,
            repeat_window_ms: 3_600_000,
            approach_bps: None,
            approach_min_age_ms: 0,
        };
        let keep = ReplayKeep {
            records: false,
            ..ReplayKeep::ALL
        };
        replay_symbol_over_configs_keep(&args.root, &args.symbol, &[cfg], keep)?.pop()
    } else {
        None
    };
    for (day, parts) in &by_day {
        let mut acc = DayAcc::new(day_start_ms(day)?);
        for p in parts {
            feed_file(p, &mut acc)?;
        }
        trades += acc.trades;
        dropped += acc.dropped;
        let rows = acc.finish();
        gap_secs += rows.iter().filter(|r| r[12] == 1).count() as u64;
        if let Some(stats) = &replay {
            let mids = &stats
                .days
                .iter()
                .find(|d| &d.day == day)
                .ok_or_else(|| anyhow::anyhow!("--check-mids: реплей без суток {day}"))?
                .mids;
            let want = |t: i64| sample_asof(mids, t, 0).map_or(0, |s| s.bid_tick + s.ask_tick);
            mid_mismatch += rows
                .iter()
                .map(|r| {
                    u64::from(r[1] != want((r[0] + 1) * 1000))
                        + u64::from(r[2] != want(r[0] * 1000 + 100))
                })
                .sum::<u64>();
        }
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
        mid_mismatch,
        out_dir,
    })
}
