//! `lob validate` — проверка данных внутри часа (TK-038, дизайн
//! `docs/findings/intra-hour-validation-design-2026-10-03.md`). Только чтение, один проход на файл.
//!
//! Срез 1: время (T1–T4, A1) — события по минутам, тишины, откаты `exch_ts`, задержка
//! `local − exch`, аномалии темпа. Срез 2: книга после каждого обновления в семантике
//! `verify --keep-going` (B1/B2, тот же код) и сделки против книги (X1) — в том же проходе.
//! Раздатчик: `--threads N` (читатель-префетч + N обработчиков). C1 (дубль группы), C2 (снимки), C3 (дрейф по снимку). D1 — `--carry`.

use std::io::Write;
use std::path::{Path, PathBuf};

use clap::Args;

use super::gaps::{collect_paths, symbol_day_of, GapsArgs};
use crate::binlog::Reader;
use crate::bybit::verify::{FileReplayer, KeepGoingReport, Verifier};

const NS_PER_SEC: i64 = 1_000_000_000;
const NS_PER_MS: i64 = 1_000_000;
const SECS_PER_DAY: i64 = 86_400;
const GAP_1M_NS: i64 = 60 * NS_PER_SEC;
const GAP_10M_NS: i64 = 600 * NS_PER_SEC;
/// Корзины log2 задержки в мс: 0, 1, 2–3, 4–7, …
const LAT_BUCKETS: usize = 40;

/// Аргументы `lob validate`.
#[derive(Debug, Args)]
pub struct ValidateArgs {
    /// Файлы бинлога или каталоги.
    pub paths: Vec<PathBuf>,
    /// Файл со списком путей (по одному на строку).
    #[arg(long)]
    pub list: Option<PathBuf>,
    /// Каталог вывода: `hours.csv`, `minutes.csv`, `files.csv`.
    #[arg(long)]
    pub out_dir: PathBuf,
    /// Ожидаемое число ложных тревог «минута без событий» на монето-сутки (решение В-##).
    #[arg(long, default_value_t = 0.01)]
    pub eps: f64,
    /// Обработчиков (1 читатель-префетч + N обработчиков; вывод в порядке входа).
    #[arg(long, default_value_t = 1)]
    pub threads: usize,
    /// На сколько файлов читатель может опережать обработчиков (страничный кэш).
    #[arg(long, default_value_t = 16)]
    pub lead: usize,
    /// D1: цепочки дней одной монеты (файлы подряд, по порядку дней) — перенос книги D→D+1.
    #[arg(long)]
    pub carry: bool,
}

#[derive(Clone, Copy)]
struct Minute {
    events: u32,
    d_min_ns: i64,
    d_max_ns: i64,
    backward: u32,
}

impl Minute {
    const EMPTY: Minute = Minute {
        events: 0,
        d_min_ns: i64::MAX,
        d_max_ns: i64::MIN,
        backward: 0,
    };
}

#[derive(Clone, Copy)]
struct Hour {
    longest_gap_ns: i64,
    gaps_1m: u32,
    gaps_10m: u32,
    backward: u32,
    max_backward_ns: i64,
    neg_latency: u32,
    lat: [u32; LAT_BUCKETS],
}

impl Hour {
    const EMPTY: Hour = Hour {
        longest_gap_ns: 0,
        gaps_1m: 0,
        gaps_10m: 0,
        backward: 0,
        max_backward_ns: 0,
        neg_latency: 0,
        lat: [0; LAT_BUCKETS],
    };
}

#[derive(Clone, Copy, Default)]
struct BookHour {
    updates: u32,
    broken_updates: u32,
    kinds: [u32; 6],
    resyncs: u32,
    broken_ms: i64,
    trades: u32,
    trades_out_of_range: u32,
    trades_violations: u32,
    trades_indeterminate: u32,
    trades_while_broken: u32,
    snapshots: u32,
    snap_drift: u32,
    snap_drift_levels: u32,
    dup_groups: u32,
}

/// Книга и сделки по часам и минутам. Нарушения считает `KeepGoingReport::on_update` —
/// тот же код, что `lob verify --keep-going`; здесь только раскладка по часам.
pub struct BookAcc {
    hours: [BookHour; 24],
    min_broken: Vec<u32>,
    min_flags: Vec<u8>,
    prev_hash: u64,
    prev_ms: i64,
    tick: i64,
    step: i64,
    carry_in: Option<CarryState>,
    pub carry_report: Option<CarryReport>,
    seen_first: bool,
    last_ms: i64,
}

const FLAG_DUP: u8 = 1;
const FLAG_DRIFT: u8 = 2;

fn mix(h: u64, x: u64) -> u64 {
    (h ^ x).wrapping_mul(0x9E37_79B9_7F4A_7C15).rotate_left(29)
}

/// Хеш группы (тип, цены и размеры, без метки: одинаковые метки реплеер сливает в одну группу) — для C1.
fn group_hash(up: &crate::book::Update) -> u64 {
    let mut h = mix(0, u64::from(up.is_snapshot));
    for &(p, q) in &up.bids {
        h = mix(mix(h, p as u64), q as u64);
    }
    h = mix(h, 0xA5A5);
    for &(p, q) in &up.asks {
        h = mix(mix(h, p as u64), q as u64);
    }
    h
}

/// Верх книги (до `n` лучших уровней стороны), от лучшей цены: (тик, размер в шагах).
fn book_top(verifier: &Verifier, side: crate::book::Side, n: usize) -> Vec<(i64, i64)> {
    let mut top: Vec<(i64, i64)> = verifier.book().levels(side).collect();
    if side == crate::book::Side::Bid {
        top.sort_unstable_by(|a, b| b.0.cmp(&a.0));
    } else {
        top.sort_unstable_by_key(|l| l.0);
    }
    top.truncate(n);
    top
}

/// Число тиков объединения верха книги и снимка с разным размером.
fn diff_top(mut top: Vec<(i64, i64)>, snap: &[(i64, i64)], tick: i64, step: i64) -> u32 {
    let mut sn: Vec<(i64, i64)> = snap.iter().map(|&(p, q)| (p / tick, q / step)).collect();
    sn.sort_unstable();
    top.sort_unstable();
    let (mut i, mut j, mut n) = (0, 0, 0u32);
    while i < top.len() || j < sn.len() {
        match (top.get(i), sn.get(j)) {
            (Some(a), Some(b)) if a.0 == b.0 => {
                n += u32::from(a.1 != b.1);
                i += 1;
                j += 1;
            }
            (Some(a), Some(b)) if a.0 < b.0 => {
                let _ = a;
                n += 1;
                i += 1;
            }
            (Some(_), None) => {
                n += 1;
                i += 1;
            }
            _ => {
                n += 1;
                j += 1;
            }
        }
    }
    n
}

/// C3: расхождение верха прежней книги со снимком.
fn drift_levels(verifier: &Verifier, up: &crate::book::Update, tick: i64, step: i64) -> u32 {
    use crate::book::Side;
    let mut n = 0;
    for (side, snap) in [(Side::Bid, &up.bids), (Side::Ask, &up.asks)] {
        if !snap.is_empty() {
            n += diff_top(book_top(verifier, side, snap.len()), snap, tick, step);
        }
    }
    n
}

/// D1: состояние книги на конце файла — верх до 50 уровней и последняя метка.
#[derive(Clone, Default)]
pub struct CarryState {
    bids: Vec<(i64, i64)>,
    asks: Vec<(i64, i64)>,
    last_ms: i64,
    tick: i64,
    step: i64,
    broken: bool,
}

/// D1 по началу файла: что пришло первым, расхождение со снимком, разрыв по времени.
#[derive(Clone, Default)]
pub struct CarryReport {
    pub first_kind: &'static str,
    pub mismatch: Option<u32>,
    pub gap_s: Option<f64>,
}

impl Default for BookAcc {
    fn default() -> Self {
        Self {
            hours: [BookHour::default(); 24],
            min_broken: vec![0; 1440],
            min_flags: vec![0; 1440],
            prev_hash: 0,
            prev_ms: i64::MIN,
            tick: 1,
            step: 1,
            carry_in: None,
            carry_report: None,
            seen_first: false,
            last_ms: 0,
        }
    }
}

fn sod_ms(ms: i64) -> usize {
    (ms / 1000).rem_euclid(SECS_PER_DAY) as usize
}

impl BookAcc {
    pub fn on_updates(
        &mut self,
        kg: &mut KeepGoingReport,
        verifier: &mut Verifier,
        ups: &mut [crate::book::Update],
    ) {
        for up in ups.iter_mut() {
            let s = sod_ms(up.cts_ms);
            let (counts, broken, resyncs, ms) =
                (kg.counts, kg.broken_updates, kg.resyncs, kg.broken_ms);
            if !self.seen_first {
                self.seen_first = true;
                if let Some(c) = &self.carry_in {
                    let same_grid = c.tick == self.tick && c.step == self.step;
                    let mismatch = if up.is_snapshot && same_grid && !c.broken {
                        let mut n = 0;
                        for (top, snap) in [(&c.bids, &up.bids), (&c.asks, &up.asks)] {
                            if !snap.is_empty() {
                                let k = snap.len().min(top.len());
                                n += diff_top(top[..k].to_vec(), snap, self.tick, self.step);
                            }
                        }
                        Some(n)
                    } else {
                        None
                    };
                    self.carry_report = Some(CarryReport {
                        first_kind: if up.is_snapshot { "snapshot" } else { "delta" },
                        mismatch,
                        gap_s: Some((up.cts_ms - c.last_ms) as f64 / 1000.0),
                    });
                }
            }
            self.last_ms = up.cts_ms;
            let hash = group_hash(up);
            let h = &mut self.hours[s / 3600];
            if hash == self.prev_hash && !up.is_snapshot && up.cts_ms - self.prev_ms <= 1000 {
                h.dup_groups += 1;
                self.min_flags[s / 60] |= FLAG_DUP;
            }
            self.prev_hash = hash;
            self.prev_ms = up.cts_ms;
            if up.is_snapshot {
                h.snapshots += 1;
                if verifier.book().is_synced() && !kg.broken {
                    let d = drift_levels(verifier, up, self.tick, self.step);
                    if d > 0 {
                        h.snap_drift += 1;
                        h.snap_drift_levels += d;
                        self.min_flags[s / 60] |= FLAG_DRIFT;
                    }
                }
            }
            kg.on_update(verifier, up);
            let h = &mut self.hours[s / 3600];
            h.updates += 1;
            for (k, (now, was)) in h.kinds.iter_mut().zip(kg.counts.iter().zip(&counts)) {
                *k += (now - was) as u32;
            }
            let db = (kg.broken_updates - broken) as u32;
            h.broken_updates += db;
            self.min_broken[s / 60] += db;
            h.resyncs += (kg.resyncs - resyncs) as u32;
            h.broken_ms += kg.broken_ms - ms;
        }
    }

    pub fn carry_out(&self, verifier: &Verifier, kg: &KeepGoingReport) -> CarryState {
        use crate::book::Side;
        CarryState {
            bids: book_top(verifier, Side::Bid, 50),
            asks: book_top(verifier, Side::Ask, 50),
            last_ms: self.last_ms,
            tick: self.tick,
            step: self.step,
            broken: kg.broken,
        }
    }

    /// Сделки кадра: приращения счётчиков `Verifier` относятся к часу первой сделки кадра.
    pub fn on_trades(
        &mut self,
        kg: &mut KeepGoingReport,
        verifier: &mut Verifier,
        trades: &[crate::bybit::verify::TradePoint],
    ) {
        let Some(first) = trades.first() else { return };
        let before = verifier.stats();
        for t in trades {
            verifier.observe_trade(t.tick, t.exch_ms, t.block, t.rpi, t.aggressor_is_buy);
        }
        let after = verifier.stats();
        let h = &mut self.hours[sod_ms(first.exch_ms) / 3600];
        h.trades += trades.len() as u32;
        h.trades_out_of_range += (after.trades_out_of_range - before.trades_out_of_range) as u32;
        h.trades_violations += (after.trades_violations - before.trades_violations) as u32;
        h.trades_indeterminate += (after.trades_indeterminate - before.trades_indeterminate) as u32;
        if kg.broken {
            kg.trades_while_broken += trades.len() as u64;
            h.trades_while_broken += trades.len() as u32;
        }
    }
}

/// Накопитель суток: фиксированные массивы, аллокаций на запись нет.
pub struct TimeAcc {
    pub book: BookAcc,
    minutes: Vec<Minute>,
    hours: [Hour; 24],
    records: u64,
    first_exch: i64,
    max_exch: i64,
}

impl Default for TimeAcc {
    fn default() -> Self {
        Self {
            book: BookAcc::default(),
            minutes: vec![Minute::EMPTY; 1440],
            hours: [Hour::EMPTY; 24],
            records: 0,
            first_exch: 0,
            max_exch: 0,
        }
    }
}

fn sod(ns: i64) -> usize {
    (ns / NS_PER_SEC).rem_euclid(SECS_PER_DAY) as usize
}

impl TimeAcc {
    pub fn push(&mut self, exch: i64, local: i64) {
        if self.records == 0 {
            self.first_exch = exch;
            self.max_exch = exch;
        }
        let s = sod(exch);
        let (h, m) = (s / 3600, s / 60);
        if exch < self.max_exch {
            let back = self.max_exch - exch;
            let hr = &mut self.hours[h];
            hr.backward += 1;
            hr.max_backward_ns = hr.max_backward_ns.max(back);
            self.minutes[m].backward += 1;
        } else if exch > self.max_exch {
            let gap = exch - self.max_exch;
            let hr = &mut self.hours[sod(self.max_exch) / 3600];
            hr.longest_gap_ns = hr.longest_gap_ns.max(gap);
            hr.gaps_1m += u32::from(gap > GAP_1M_NS);
            hr.gaps_10m += u32::from(gap > GAP_10M_NS);
            self.max_exch = exch;
        }
        let d = local - exch;
        let min = &mut self.minutes[m];
        min.events += 1;
        min.d_min_ns = min.d_min_ns.min(d);
        min.d_max_ns = min.d_max_ns.max(d);
        let hr = &mut self.hours[h];
        if d < 0 {
            hr.neg_latency += 1;
        } else {
            let ms = (d / NS_PER_MS) as u64;
            let b = (64 - ms.leading_zeros()) as usize;
            hr.lat[b.min(LAT_BUCKETS - 1)] += 1;
        }
        self.records += 1;
    }

    fn span_minutes(&self) -> (usize, usize) {
        (sod(self.first_exch) / 60, sod(self.max_exch) / 60)
    }

    /// Медиана событий/мин по минутам внутри размаха файла.
    pub fn median_rate(&self) -> f64 {
        if self.records == 0 {
            return 0.0;
        }
        let (a, b) = self.span_minutes();
        if b < a {
            return 0.0;
        }
        let mut v: Vec<u32> = self.minutes[a..=b].iter().map(|m| m.events).collect();
        v.sort_unstable();
        f64::from(v[v.len() / 2])
    }

    /// Минута без событий — аномалия при обычной активности: `1440·exp(−λ) < eps`.
    pub fn silent_is_anomaly(&self, eps: f64) -> bool {
        1440.0 * (-self.median_rate()).exp() < eps
    }

    pub fn hours_csv_header() -> &'static str {
        "symbol,day,hour,events,silent_minutes,longest_gap_s,gaps_gt_1m,gaps_gt_10m,\
         backward_steps,max_backward_ms,neg_latency,lat_p50_ms_ub,lat_p99_ms_ub,lat_min_ms,\
         lat_max_ms,status,book_updates,book_broken_updates,book_broken_ms,book_resyncs,crossed,\
         nonpositive_size,unordered_levels,price_not_on_tick,qty_not_on_step,delta_before_snapshot,\
         trades,trades_out_of_range,trades_violations,trades_indeterminate,trades_while_broken,\
         snapshots,snapshot_drift,snapshot_drift_levels,dup_groups"
    }

    fn hour_line(&self, sym: &str, day: &str, h: usize, status: &str) -> Option<String> {
        let ms = &self.minutes[h * 60..h * 60 + 60];
        let events: u64 = ms.iter().map(|m| u64::from(m.events)).sum();
        let hr = &self.hours[h];
        if events == 0 && hr.longest_gap_ns == 0 {
            return None;
        }
        let (a, b) = self.span_minutes();
        let silent = (h * 60..h * 60 + 60)
            .filter(|&i| i >= a && i <= b && self.minutes[i].events == 0)
            .count();
        let total: u32 = hr.lat.iter().sum();
        let q = |p: u64| -> u64 {
            if total == 0 {
                return 0;
            }
            let need = (u64::from(total) * p).div_ceil(100);
            let mut acc = 0u64;
            for (i, c) in hr.lat.iter().enumerate() {
                acc += u64::from(*c);
                if acc >= need {
                    return if i == 0 { 0 } else { 1u64 << i };
                }
            }
            0
        };
        let (dmin, dmax) = if events == 0 {
            (0, 0)
        } else {
            (
                ms.iter().map(|m| m.d_min_ns).min().unwrap_or(0),
                ms.iter().map(|m| m.d_max_ns).max().unwrap_or(0),
            )
        };
        let b = &self.book.hours[h];
        Some(format!(
            "{sym},{day},{h},{events},{silent},{:.3},{},{},{},{:.3},{},{},{},{:.3},{:.3},{status},\
             {},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{}",
            hr.longest_gap_ns as f64 / NS_PER_SEC as f64,
            hr.gaps_1m,
            hr.gaps_10m,
            hr.backward,
            hr.max_backward_ns as f64 / NS_PER_MS as f64,
            hr.neg_latency,
            q(50),
            q(99),
            dmin as f64 / NS_PER_MS as f64,
            dmax as f64 / NS_PER_MS as f64,
            b.updates,
            b.broken_updates,
            b.broken_ms,
            b.resyncs,
            b.kinds[0],
            b.kinds[1],
            b.kinds[2],
            b.kinds[3],
            b.kinds[4],
            b.kinds[5],
            b.trades,
            b.trades_out_of_range,
            b.trades_violations,
            b.trades_indeterminate,
            b.trades_while_broken,
            b.snapshots,
            b.snap_drift,
            b.snap_drift_levels,
            b.dup_groups,
        ))
    }

    pub fn hour_lines(&self, sym: &str, day: &str, status: &str) -> Vec<String> {
        (0..24)
            .filter_map(|h| self.hour_line(sym, day, h, status))
            .collect()
    }

    pub fn minutes_csv_header() -> &'static str {
        "symbol,day,hour,minute,kinds,events,d_min_ms,d_max_ms,broken_updates"
    }

    /// Минуты-исключения: пустые внутри размаха, аномалия темпа, откат `exch_ts`.
    pub fn minute_lines(&self, sym: &str, day: &str, eps: f64) -> Vec<String> {
        let mut out = Vec::new();
        if self.records == 0 {
            return out;
        }
        let (a, b) = self.span_minutes();
        let anomaly = self.silent_is_anomaly(eps);
        for i in a..=b.min(1439) {
            let m = &self.minutes[i];
            let mut kinds = Vec::new();
            if m.events == 0 {
                kinds.push("silent");
                if anomaly {
                    kinds.push("rate_anomaly");
                }
            }
            if m.backward > 0 {
                kinds.push("ts_backward");
            }
            let broken = self.book.min_broken[i];
            if broken > 0 {
                kinds.push("book_broken");
            }
            let fl = self.book.min_flags[i];
            if fl & FLAG_DUP != 0 {
                kinds.push("dup_group");
            }
            if fl & FLAG_DRIFT != 0 {
                kinds.push("snap_drift");
            }
            if kinds.is_empty() {
                continue;
            }
            let (lo, hi) = if m.events == 0 {
                (0.0, 0.0)
            } else {
                (
                    m.d_min_ns as f64 / NS_PER_MS as f64,
                    m.d_max_ns as f64 / NS_PER_MS as f64,
                )
            };
            out.push(format!(
                "{sym},{day},{},{},{},{},{lo:.3},{hi:.3},{broken}",
                i / 60,
                i % 60,
                kinds.join("|"),
                m.events
            ));
        }
        out
    }
}

/// Итог по файлу: строки часов и минут-исключений, статус.
pub struct FileValidation {
    pub symbol: String,
    pub day: String,
    pub records: u64,
    pub status: String,
    pub hours: Vec<String>,
    pub minutes: Vec<String>,
    pub carry: Option<CarryReport>,
    pub carry_out: Option<CarryState>,
}

pub fn validate_file(path: &Path, eps: f64, carry_in: Option<CarryState>) -> FileValidation {
    let (symbol, day) = symbol_day_of(path);
    let mut acc = TimeAcc::default();
    let mut status = "ok".to_string();
    acc.book.carry_in = carry_in;
    let mut carry_out = None;
    let res: anyhow::Result<()> = (|| {
        let file = std::fs::File::open(path)?;
        let mut reader = Reader::open(std::io::BufReader::with_capacity(1 << 20, file))?;
        let (tick, step) = (reader.header().tick_e9, reader.header().step_e9);
        acc.book.tick = tick;
        acc.book.step = step;
        let mut verifier = Verifier::new(tick, step);
        let mut kg = KeepGoingReport::default();
        let mut replayer = FileReplayer::new();
        let (mut updates, mut trades) = (Vec::new(), Vec::new());
        while let Some(frame) = reader.read_frame_soft()? {
            for r in &frame {
                acc.push(r.exch_ts_ns, r.local_ts_ns);
            }
            updates.clear();
            trades.clear();
            replayer.push_frame(&frame, tick, step, &mut updates, &mut trades);
            acc.book.on_updates(&mut kg, &mut verifier, &mut updates);
            acc.book.on_trades(&mut kg, &mut verifier, &trades);
        }
        let mut tail = Vec::new();
        replayer.finish(&mut tail);
        acc.book.on_updates(&mut kg, &mut verifier, &mut tail);
        carry_out = Some(acc.book.carry_out(&verifier, &kg));
        if reader.truncated_tail() {
            status = "truncated_tail".to_string();
        }
        Ok(())
    })();
    if let Err(e) = res {
        status = format!("error: {e}").replace(['\n', '\r', ','], " ");
    }
    FileValidation {
        hours: acc.hour_lines(&symbol, &day, &status),
        minutes: acc.minute_lines(&symbol, &day, eps),
        records: acc.records,
        carry: acc.book.carry_report.take(),
        carry_out,
        symbol,
        day,
        status,
    }
}

/// Единицы работы: файл или (при `carry`) цепочка подряд идущих файлов одной монеты — один
/// обработчик ведёт её по порядку, перенося книгу с конца дня D на начало D+1.
fn work_items(paths: &[PathBuf], carry: bool) -> Vec<Vec<usize>> {
    let mut items: Vec<Vec<usize>> = Vec::new();
    let mut last_sym = String::new();
    for (i, p) in paths.iter().enumerate() {
        let sym = symbol_day_of(p).0;
        if carry && !items.is_empty() && sym == last_sym {
            items.last_mut().unwrap().push(i);
        } else {
            items.push(vec![i]);
        }
        last_sym = sym;
    }
    items
}

fn run_item(
    paths: &[PathBuf],
    item: &[usize],
    eps: f64,
    carry: bool,
) -> Vec<(usize, FileValidation)> {
    let mut state: Option<CarryState> = None;
    let mut out = Vec::with_capacity(item.len());
    for &i in item {
        let mut v = validate_file(&paths[i], eps, if carry { state.take() } else { None });
        state = v.carry_out.take();
        out.push((i, v));
    }
    out
}

/// Один читатель читает файлы подряд (последовательно, греет страничный кэш, опережая на `lead`
/// файлов; при `carry` не запускается — цепочки идут параллельно в разных местах диска),
/// `threads` обработчиков берут единицы работы по очереди; `sink` получает результаты строго
/// в порядке входа.
fn process_all<F>(
    paths: &[PathBuf],
    eps: f64,
    threads: usize,
    lead: usize,
    carry: bool,
    mut sink: F,
) -> std::io::Result<()>
where
    F: FnMut(usize, &FileValidation) -> std::io::Result<()>,
{
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::mpsc;
    let items = work_items(paths, carry);
    if threads <= 1 {
        for item in &items {
            for (i, v) in run_item(paths, item, eps, carry) {
                sink(i, &v)?;
            }
        }
        return Ok(());
    }
    let next = AtomicUsize::new(0);
    let taken = AtomicUsize::new(0);
    let (tx, rx) = mpsc::channel::<(usize, FileValidation)>();
    let mut err = Ok(());
    std::thread::scope(|sc| {
        if !carry {
            sc.spawn(|| {
                let mut sinkbuf = vec![0u8; 1 << 20];
                for (i, p) in paths.iter().enumerate() {
                    while i > taken.load(Ordering::Relaxed) + lead {
                        std::thread::sleep(std::time::Duration::from_millis(20));
                    }
                    if let Ok(mut f) = std::fs::File::open(p) {
                        use std::io::Read;
                        while matches!(f.read(&mut sinkbuf), Ok(k) if k > 0) {}
                    }
                }
            });
        }
        for _ in 0..threads {
            let tx = tx.clone();
            let (next, taken, items) = (&next, &taken, &items);
            sc.spawn(move || loop {
                let k = next.fetch_add(1, Ordering::SeqCst);
                let Some(item) = items.get(k) else { break };
                taken.fetch_max(item[0], Ordering::Relaxed);
                for r in run_item(paths, item, eps, carry) {
                    if tx.send(r).is_err() {
                        return;
                    }
                }
            });
        }
        drop(tx);
        let mut pending: std::collections::BTreeMap<usize, FileValidation> = Default::default();
        let mut want = 0usize;
        for (i, v) in rx {
            pending.insert(i, v);
            while let Some(v) = pending.remove(&want) {
                if err.is_ok() {
                    err = sink(want, &v);
                }
                want += 1;
            }
        }
    });
    err
}

pub fn run_validate(args: &ValidateArgs) -> anyhow::Result<()> {
    let gaps_args = GapsArgs {
        paths: args.paths.clone(),
        list: args.list.clone(),
        sha256: false,
        out: None,
    };
    let paths = collect_paths(&gaps_args)?;
    if paths.is_empty() {
        anyhow::bail!("lob validate: не задано ни одного файла (paths / --list)");
    }
    std::fs::create_dir_all(&args.out_dir)?;
    let open = |n: &str| -> anyhow::Result<_> {
        Ok(std::io::BufWriter::new(std::fs::File::create(
            args.out_dir.join(n),
        )?))
    };
    let (mut hours, mut minutes, mut files) =
        (open("hours.csv")?, open("minutes.csv")?, open("files.csv")?);
    writeln!(hours, "{}", TimeAcc::hours_csv_header())?;
    writeln!(minutes, "{}", TimeAcc::minutes_csv_header())?;
    writeln!(
        files,
        "symbol,day,path,records,status,carry_first_kind,carry_mismatch_levels,carry_gap_s"
    )?;
    let results = process_all(
        &paths,
        args.eps,
        args.threads.max(1),
        args.lead.max(1),
        args.carry,
        |i, v| {
            for l in &v.hours {
                writeln!(hours, "{l}")?;
            }
            for l in &v.minutes {
                writeln!(minutes, "{l}")?;
            }
            writeln!(
                files,
                "{},{},\"{}\",{},{},{},{},{}",
                v.symbol,
                v.day,
                paths[i].display().to_string().replace('"', "\"\""),
                v.records,
                v.status,
                v.carry.as_ref().map_or("", |c| c.first_kind),
                v.carry
                    .as_ref()
                    .and_then(|c| c.mismatch)
                    .map_or(String::new(), |m| m.to_string()),
                v.carry
                    .as_ref()
                    .and_then(|c| c.gap_s)
                    .map_or(String::new(), |g| format!("{g:.3}"))
            )?;
            files.flush()
        },
    );
    results?;
    hours.flush()?;
    minutes.flush()?;
    Ok(())
}

#[cfg(test)]
mod tests;
