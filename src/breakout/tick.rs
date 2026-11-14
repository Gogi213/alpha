//! П-42 шаг B (протокол §13.2): сигнал, вход и выходы по сделкам Binance aggTrades вместо минутного приближения.
//!
//! Что не меняется: уровни (часовые бары из минуток), пул и NATR (по закрытым минуткам), запрет ложного пробоя
//! (по закрытиям минуток), портфель, издержки, фандинг. Что меняется: (1) сигнал — первая сделка минуты `t`,
//! подошедшая к уровню на ≤ E % снизу, пока ни одна сделка минуты не дошла до уровня; (2) вход — следующая
//! агрессивная сделка стороны входа (± проскальзывание); (3) тейки/стоп/трейл — по ценам сделок: тейк — только
//! строго через цену, стоп — по цене сделки, пробившей его.
//!
//! Фаза 1 (`signals_for_coin`): кандидаты (уровень, минута) по минуткам → проверка по сделкам суток монеты.
//! Фаза 2 (`run_tick`): портфель (как `portfolio::run`, но в мс) и исход сделки по сделкам (`simulate_tick`).

use super::data::{
    day_string, parse_dec, Sym, COUNT_DAYS, DAYS_TOTAL, HALF_DAYS, MIN_MS, N_MIN, SCALE, START_DAY,
    START_MIN, T0_MS,
};
use super::levels::{build_levels, Level};
use super::params::{ExitCfg, LevelCfg, PortCfg, SigCfg};
use super::portfolio::Settled;
use super::trade::{frac, ExitKind, NOTIONAL_U};
use super::Side;
use crate::lob::costs::leg_fee_bps;
use anyhow::{bail, Context, Result};
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

/// Счётчик опции `pk` (§13.11, диагностика): уровни, убитые минутным проколом > τ, которых запрет ложного пробоя к этой минуте не убил.
pub static PK_MINUTE_KILLED: AtomicU64 = AtomicU64::new(0);

pub const DAY_MS: i64 = 86_400_000;

/// Одна агрегированная сделка: цена ×1e8, время — мс от начала суток файла, `bm` = `is_buyer_maker`
/// (агрессор — продавец).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Tick {
    pub px: i64,
    pub off: u32,
    pub bm: bool,
}

/// Источник сделок: сутки монеты (0 = 01.09) по времени; `None` — файла нет.
pub trait TradeSrc: Sync {
    fn day(&self, sym: &str, day: usize) -> Option<Arc<Vec<Tick>>>;
}

#[derive(Debug, Default, Clone)]
pub struct LoadStat {
    pub files: u64,
    pub rows: u64,
    pub unsorted_files: u64,
    pub out_of_day: u64,
    pub bad_rows: u64,
    pub errors: u64,
}

impl LoadStat {
    fn add(&mut self, o: &LoadStat) {
        self.files += o.files;
        self.rows += o.rows;
        self.unsorted_files += o.unsorted_files;
        self.out_of_day += o.out_of_day;
        self.bad_rows += o.bad_rows;
        self.errors += o.errors;
    }
}

/// Цена aggTrades ×1e8: обычная десятичная строка или запись Java `9.16E-4` (так Binance пишет цены < 0,001).
pub fn parse_px(b: &[u8]) -> Option<i64> {
    let Some(k) = b.iter().position(|&c| c == b'E' || c == b'e') else {
        return parse_dec(b, 8);
    };
    let (m, e) = (&b[..k], &b[k + 1..]);
    let (eneg, e) = match e.first()? {
        b'-' => (true, &e[1..]),
        b'+' => (false, &e[1..]),
        _ => (false, e),
    };
    if e.is_empty() || e.len() > 3 {
        return None;
    }
    let mut exp = 0i32;
    for &c in e {
        let d = c.wrapping_sub(b'0');
        if d > 9 {
            return None;
        }
        exp = exp * 10 + i32::from(d);
    }
    if eneg {
        exp = -exp;
    }
    let (mut mant, mut fd, mut dot) = (0i64, 0i32, false);
    for &c in m {
        if c == b'.' && !dot {
            dot = true;
            continue;
        }
        let d = c.wrapping_sub(b'0');
        if d > 9 {
            return None;
        }
        mant = mant.checked_mul(10)?.checked_add(i64::from(d))?;
        fd += i32::from(dot);
    }
    let shift = exp - fd + 8;
    if shift >= 0 {
        mant.checked_mul(10i64.checked_pow(shift as u32)?)
    } else {
        Some(10i64.checked_pow((-shift) as u32).map_or(0, |p| mant / p))
    }
}

/// CSV суток (`agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker`) → сделки.
pub fn parse_trades(mut rd: impl BufRead, day: usize, st: &mut LoadStat) -> Result<Vec<Tick>> {
    let base = T0_MS + day as i64 * DAY_MS;
    let mut v: Vec<Tick> = Vec::new();
    let mut line: Vec<u8> = Vec::with_capacity(128);
    let mut prev = 0u32;
    let mut unsorted = false;
    loop {
        line.clear();
        if rd.read_until(b'\n', &mut line)? == 0 {
            break;
        }
        if !line.first().is_some_and(u8::is_ascii_digit) {
            continue;
        }
        let mut f = line.split(|&c| c == b',');
        let (_, Some(p), _, _, _, Some(t), Some(b)) = (
            f.next(),
            f.next(),
            f.next(),
            f.next(),
            f.next(),
            f.next(),
            f.next(),
        ) else {
            st.bad_rows += 1;
            if st.bad_rows == 1 {
                eprintln!(
                    "плохая строка (день {day}): {}",
                    String::from_utf8_lossy(&line).trim_end()
                );
            }
            continue;
        };
        let (Some(px), Some(ts)) = (parse_px(p), parse_dec(t, 0)) else {
            st.bad_rows += 1;
            if st.bad_rows == 1 {
                eprintln!(
                    "плохая строка (день {day}): {}",
                    String::from_utf8_lossy(&line).trim_end()
                );
            }
            continue;
        };
        let off = ts - base;
        if !(0..DAY_MS).contains(&off) {
            st.out_of_day += 1;
            continue;
        }
        let off = off as u32;
        if off < prev {
            unsorted = true;
        }
        prev = off;
        v.push(Tick {
            px,
            off,
            bm: b.first() == Some(&b't'),
        });
        st.rows += 1;
    }
    if unsorted {
        st.unsorted_files += 1;
        v.sort_by_key(|x| x.off);
    }
    Ok(v)
}

struct Lru {
    map: HashMap<(String, usize), Option<Arc<Vec<Tick>>>>,
    order: VecDeque<(String, usize)>,
    bytes: usize,
}

/// Сутки из `<dir>/<МОНЕТА>/<МОНЕТА>-aggTrades-<день>.zip` (распаковка внешним `unzip -p`), с ограничением памяти.
pub struct ZipSrc {
    dir: PathBuf,
    cap_bytes: usize,
    lru: Mutex<Lru>,
    pub stat: Mutex<LoadStat>,
}

impl ZipSrc {
    pub fn new(dir: PathBuf, cap_bytes: usize) -> ZipSrc {
        ZipSrc {
            dir,
            cap_bytes,
            lru: Mutex::new(Lru {
                map: HashMap::new(),
                order: VecDeque::new(),
                bytes: 0,
            }),
            stat: Mutex::new(LoadStat::default()),
        }
    }

    fn load(&self, name: &str, day: usize) -> Result<Option<Vec<Tick>>> {
        let p = self
            .dir
            .join(name)
            .join(format!("{name}-aggTrades-{}.zip", day_string(day)));
        if !p.exists() {
            return Ok(None);
        }
        let mut child = Command::new("unzip")
            .arg("-p")
            .arg(&p)
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .with_context(|| format!("unzip {}", p.display()))?;
        let out = child.stdout.take().context("unzip: нет stdout")?;
        let mut st = LoadStat::default();
        let v = parse_trades(BufReader::with_capacity(1 << 20, out), day, &mut st);
        let status = child.wait()?;
        let v = v?;
        if !status.success() {
            bail!("unzip {}: {status}", p.display());
        }
        st.files = 1;
        if let Ok(mut g) = self.stat.lock() {
            g.add(&st);
        }
        Ok(Some(v))
    }
}

impl TradeSrc for ZipSrc {
    fn day(&self, sym: &str, day: usize) -> Option<Arc<Vec<Tick>>> {
        let key = (sym.to_string(), day);
        if let Ok(g) = self.lru.lock() {
            if let Some(v) = g.map.get(&key) {
                return v.clone();
            }
        }
        let loaded = match self.load(sym, day) {
            Ok(v) => v.map(Arc::new),
            Err(e) => {
                eprintln!("ОШИБКА загрузки {sym} день {day}: {e:#}");
                if let Ok(mut g) = self.stat.lock() {
                    g.errors += 1;
                }
                None
            }
        };
        if let Ok(mut g) = self.lru.lock() {
            let sz = loaded
                .as_ref()
                .map_or(0, |a| a.len() * std::mem::size_of::<Tick>());
            if g.map.insert(key.clone(), loaded.clone()).is_none() {
                g.order.push_back(key);
                g.bytes += sz;
            }
            while g.bytes > self.cap_bytes && g.order.len() > 1 {
                if let Some(k) = g.order.pop_front() {
                    if let Some(Some(a)) = g.map.remove(&k) {
                        g.bytes = g
                            .bytes
                            .saturating_sub(a.len() * std::mem::size_of::<Tick>());
                    }
                }
            }
        }
        loaded
    }
}

// ───────────────────────────── фаза 1: сигналы ─────────────────────────────

/// Кандидат: уровень `level` жив в минуте `t`, цена уровня `lp` в длинной раме, минутка по диапазону может дать сигнал.
#[derive(Debug, Clone, Copy)]
pub struct Cand {
    pub t: u32,
    pub level: u32,
    pub lp: i64,
}

/// Сигнал по сделке: `ms` — время сделки от 01.09 00:00 UTC, `day`/`idx` — её место в файле суток.
#[derive(Debug, Clone, Copy)]
pub struct TSig {
    pub ms: i64,
    pub sym: u16,
    pub level: u32,
    pub dist: u32,
    pub t: u32,
    pub day: u16,
    pub idx: u32,
}

#[derive(Debug, Default, Clone)]
pub struct TickCtr {
    pub cands: u64,
    pub signals: [u64; 2],
    pub missing_days: u64,
}

impl TickCtr {
    pub fn add(&mut self, o: &TickCtr) {
        self.cands += o.cands;
        self.missing_days += o.missing_days;
        for h in 0..2 {
            self.signals[h] += o.signals[h];
        }
    }
}

fn half_of_day(day: usize) -> usize {
    usize::from(day >= START_DAY + HALF_DAYS)
}

/// Кандидаты (§13.2 п. 1): состояние уровня (запрет, ложный пробой) — по закрытиям минут ≤ `t−1`; пул — по
/// закрытым минутам ≤ `t−1`; минутка по диапазону может дать сделку в зоне входа под уровнем. Затем состояние
/// обновляется минуткой `t` (как в `signals::scan`).
#[allow(clippy::needless_range_loop)] // t индексирует сразу несколько рядов монеты
pub fn candidates(sym: &Sym, levels: &[Level], cfg: &SigCfg, side: Side, out: &mut Vec<Cand>) {
    let s = side.s();
    let Some(natr) = sym.natr_for(cfg.natr_p) else {
        return;
    };
    let dmax = (cfg.e_pm * 1000).min(cfg.track_pm * 1000);
    let natr_min = cfg.natr_min_pm * 1000;
    for (li, lv) in levels.iter().enumerate() {
        let end = (lv.end as usize).min(N_MIN);
        let mut seg = 0;
        let mut price = lv.segs[0].1;
        let mut pend: Option<usize> = None;
        let mut forbidden = false;
        let mut pk_dead = false;
        for t in lv.born as usize..end {
            while seg + 1 < lv.segs.len() && lv.segs[seg + 1].0 as usize <= t {
                seg += 1;
                price = lv.segs[seg].1;
            }
            if sym.o[t] == 0 {
                continue;
            }
            let lp = s * price;
            let (hi, lo) = if s > 0 {
                (sym.h[t], sym.l[t])
            } else {
                (-sym.l[t], -sym.h[t])
            };
            if t >= START_MIN && !forbidden && lo < lp {
                let zone_lo = lp
                    - (i128::from(lp.abs()) * i128::from(dmax + 1) / i128::from(SCALE)) as i64
                    - 1;
                let nv = i64::from(natr[t - 1]);
                if hi >= zone_lo
                    && nv >= 0
                    && nv >= natr_min
                    && sym.pq[t] - sym.pq[t - 60] >= cfg.t1h_u
                    && (cfg.t24_u == 0 || sym.pq[t] - sym.pq[t - 1440] >= cfg.t24_u)
                {
                    out.push(Cand {
                        t: t as u32,
                        level: li as u32,
                        lp,
                    });
                }
            }
            if let Some(tc) = pend {
                if t - tc > cfg.r_min {
                    pend = None;
                }
            }
            if hi > lp && pend.is_none() {
                pend = Some(t);
            }
            if cfg.ban && pend.is_some() {
                let back = lp - (i128::from(lp.abs()) * i128::from(cfg.f_pm) / 100_000) as i64;
                if s * sym.c[t] < back {
                    forbidden = true;
                }
            }
            // §13.11 (`pk`): прокол цены версии больше чем на τ убивает уровень навсегда, со следующей минуты
            if cfg.lv.pk
                && !pk_dead
                && hi > lp + (i128::from(lp.abs()) * i128::from(cfg.lv.tau_pm) / 100_000) as i64
            {
                pk_dead = true;
                if !forbidden {
                    PK_MINUTE_KILLED.fetch_add(1, Ordering::Relaxed);
                }
                forbidden = true;
            }
        }
    }
}

/// Первая сделка среди `ticks[from..to]` (сделки одной минуты), дающая сигнал: ни одна сделка минуты до неё
/// включительно не дошла до уровня (длинная рама: цена < `lp`), расстояние `(lp − цена)/lp ≤ dmax`, цвет (если
/// включён) — цена выше цены первой сделки минуты. Возвращает (индекс в `ticks`, расстояние ×1e8).
pub fn first_signal(
    ticks: &[Tick],
    from: usize,
    to: usize,
    s: i64,
    lp: i64,
    dmax: i64,
    color: bool,
) -> Option<(usize, u32)> {
    let mut hi = i64::MIN;
    let mut op = 0i64;
    for (k, tk) in ticks.iter().enumerate().take(to).skip(from) {
        let p = s * tk.px;
        if k == from {
            op = p;
        }
        hi = hi.max(p);
        if hi >= lp {
            return None;
        }
        let dist = (i128::from(lp - p) * i128::from(SCALE) / i128::from(lp.abs())) as i64;
        if dist > dmax || (color && p <= op) {
            continue;
        }
        return Some((k, dist as u32));
    }
    None
}

/// Границы сделок минуты `t` в `ticks` суток (по времени).
pub fn minute_range(ticks: &[Tick], t: usize) -> (usize, usize) {
    let start = ((t % 1440) as i64 * MIN_MS) as u32;
    let end = start + MIN_MS as u32;
    (
        ticks.partition_point(|x| x.off < start),
        ticks.partition_point(|x| x.off < end),
    )
}

/// Сигналы одной монеты для всех заданий `(SigCfg, сторона)`: сделки каждых суток читаются один раз.
pub fn signals_for_coin(
    sym: &Sym,
    si: u16,
    jobs: &[(SigCfg, Side)],
    src: &dyn TradeSrc,
    outs: &mut [Vec<TSig>],
    ctrs: &mut [TickCtr],
) {
    let mut lev: HashMap<(LevelCfg, Side), Vec<Level>> = HashMap::new();
    let mut by_day: BTreeMap<usize, Vec<(usize, Cand)>> = BTreeMap::new();
    for (j, (cfg, side)) in jobs.iter().enumerate() {
        let levels = lev
            .entry((cfg.lv.clone(), *side))
            .or_insert_with(|| build_levels(sym, &cfg.lv, *side));
        let mut cs = Vec::new();
        candidates(sym, levels, cfg, *side, &mut cs);
        ctrs[j].cands += cs.len() as u64;
        for c in cs {
            by_day.entry(c.t as usize / 1440).or_default().push((j, c));
        }
    }
    for (day, list) in by_day {
        let Some(ticks) = src.day(&sym.name, day) else {
            for (j, _) in &list {
                ctrs[*j].missing_days += 1;
            }
            continue;
        };
        for (j, c) in list {
            let (cfg, side) = &jobs[j];
            let (from, to) = minute_range(&ticks, c.t as usize);
            let dmax = (cfg.e_pm * 1000).min(cfg.track_pm * 1000);
            if let Some((k, dist)) = first_signal(&ticks, from, to, side.s(), c.lp, dmax, cfg.color)
            {
                outs[j].push(TSig {
                    ms: day as i64 * DAY_MS + i64::from(ticks[k].off),
                    sym: si,
                    level: c.level,
                    dist,
                    t: c.t,
                    day: day as u16,
                    idx: k as u32,
                });
                ctrs[j].signals[half_of_day(day)] += 1;
            }
        }
    }
}

/// Порядок обработки (§13.2): время сделки, расстояние до уровня, монета по алфавиту, уровень.
pub fn sort_tsigs(v: &mut [TSig]) {
    v.sort_unstable_by_key(|s| (s.ms, s.dist, s.sym, s.level));
}

// ───────────────────────────── фаза 2: исход сделки ─────────────────────────────

#[derive(Debug, Clone)]
pub struct TLeg {
    pub ms: i64,
    /// Настоящая цена исполнения (не рама).
    pub px: i64,
    pub u: i64,
    pub maker: bool,
}

#[derive(Debug, Clone)]
pub struct TickOut {
    pub entry_ms: i64,
    pub entry_px: i64,
    pub legs: Vec<TLeg>,
    pub exit_ms: i64,
    pub kind: ExitKind,
    pub takes: u32,
    /// Цена последней сделки до 00:00 UTC конца суток `g` (настоящая цена), для суток входа … суток перед выходом.
    pub marks: Vec<(usize, i64)>,
}

/// Состояние позиции в длинной раме; сделки подаются по одной.
struct Pos {
    e: i64,
    tps: Vec<i64>,
    lot: i64,
    a_dist: i64,
    r_dist: i64,
    be_cfg: bool,
    rem: i64,
    stop_base: i64,
    be_on: bool,
    armed: bool,
    trail: i64,
    maxp: i64,
    next_tp: usize,
    legs: Vec<TLeg>,
    s: i64,
}

impl Pos {
    fn new(s: i64, e: i64, p0: i64, natr_e8: i64, ex: &ExitCfg) -> Pos {
        let ea = e.abs();
        let nat = |x_m: i64| -> i64 {
            (i128::from(ea) * i128::from(natr_e8) * i128::from(x_m) / (i128::from(SCALE) * 1000))
                as i64
        };
        let n = ex.n_tp as i64;
        Pos {
            e,
            tps: (0..n)
                .map(|i| e + nat(ex.t_low_m + (ex.t_high_m - ex.t_low_m) * i / (n - 1)))
                .collect(),
            lot: NOTIONAL_U * ex.q_pct / (100 * n),
            a_dist: nat(ex.a_m),
            r_dist: nat(ex.r_m),
            be_cfg: ex.be,
            rem: NOTIONAL_U,
            stop_base: e - frac(ea, ex.s_pm, 100_000),
            be_on: false,
            armed: false,
            trail: i64::MIN,
            maxp: p0,
            next_tp: 0,
            legs: Vec::new(),
            s,
        }
    }

    /// Тейки-лимитки, пройденные сделкой `p` строго насквозь.
    fn fill_takes(&mut self, ms: i64, p: i64) {
        while self.next_tp < self.tps.len() && p > self.tps[self.next_tp] {
            self.legs.push(TLeg {
                ms,
                px: self.s * self.tps[self.next_tp],
                u: self.lot,
                maker: true,
            });
            self.rem -= self.lot;
            if self.next_tp == 0 && self.be_cfg {
                self.be_on = true;
                self.stop_base = self.stop_base.max(self.e);
            }
            self.next_tp += 1;
        }
    }

    /// Сделка с ценой `p` (рама) в момент `ms`. `Some(вид)` — стоп сработал на этой сделке (исполнение по `p`).
    fn on_trade(&mut self, ms: i64, p: i64) -> Option<ExitKind> {
        let trail_lvl = if self.armed { self.trail } else { i64::MIN };
        let sp = self.stop_base.max(trail_lvl);
        if p <= sp {
            return Some(if trail_lvl > self.stop_base {
                ExitKind::Trail
            } else if self.be_on {
                ExitKind::BreakEven
            } else {
                ExitKind::HardStop
            });
        }
        self.fill_takes(ms, p);
        self.maxp = self.maxp.max(p);
        if !self.armed && self.maxp - self.e >= self.a_dist {
            self.armed = true;
        }
        if self.armed {
            self.trail = self.trail.max(self.maxp - self.r_dist);
        }
        None
    }
}

/// Курсор по сделкам монеты от места `(day, idx)`; сутки без файла пропускаются.
pub struct Cursor<'a> {
    src: &'a dyn TradeSrc,
    name: &'a str,
    day: usize,
    idx: usize,
    cur: Option<Arc<Vec<Tick>>>,
}

impl<'a> Cursor<'a> {
    pub fn new(src: &'a dyn TradeSrc, name: &'a str, day: usize, idx: usize) -> Cursor<'a> {
        Cursor {
            src,
            name,
            day,
            idx,
            cur: None,
        }
    }

    /// (время от 01.09 в мс, цена, `is_buyer_maker`).
    pub fn next_tick(&mut self) -> Option<(i64, i64, bool)> {
        loop {
            if self.day >= DAYS_TOTAL {
                return None;
            }
            if self.cur.is_none() {
                self.cur = Some(
                    self.src
                        .day(self.name, self.day)
                        .unwrap_or_else(|| Arc::new(Vec::new())),
                );
            }
            if let Some(v) = &self.cur {
                if let Some(tk) = v.get(self.idx) {
                    self.idx += 1;
                    return Some((self.day as i64 * DAY_MS + i64::from(tk.off), tk.px, tk.bm));
                }
            }
            self.cur = None;
            self.day += 1;
            self.idx = 0;
        }
    }
}

/// Вход и выходы по потоку сделок `next` (после сигнала в момент `sig_ms`): §13.2 п. 2–3.
pub fn sim_stream(
    next: &mut dyn FnMut() -> Option<(i64, i64, bool)>,
    sig_ms: i64,
    side: Side,
    natr_e8: i64,
    ex: &ExitCfg,
) -> Option<TickOut> {
    let s = side.s();
    let taker_bm = side == Side::Short;
    let (entry_ms, p0) = loop {
        let (ms, px, bm) = next()?;
        if ms >= sig_ms + ex.lat_ms && bm == taker_bm {
            break (ms, s * px);
        }
    };
    let e = p0 + frac(p0.abs(), ex.slip_bps, 10_000);
    let mut pos = Pos::new(s, e, p0, natr_e8, ex);
    let mut cur_day = (entry_ms / DAY_MS) as usize;
    let mut marks: Vec<(usize, i64)> = Vec::new();
    let mut last = (entry_ms, p0);
    let mut exit: Option<(i64, i64, ExitKind)> = None;
    let mut pending: Option<(i64, ExitKind)> = None;
    while let Some((ms, px, _)) = next() {
        let d = (ms / DAY_MS) as usize;
        while cur_day < d {
            marks.push((cur_day, s * last.1));
            cur_day += 1;
        }
        let p = s * px;
        last = (ms, p);
        if let Some((due, kind)) = pending {
            if ms >= due {
                exit = Some((ms, p, kind));
                break;
            }
            pos.fill_takes(ms, p);
            continue;
        }
        if let Some(kind) = pos.on_trade(ms, p) {
            if ex.lat_exit_ms > 0 {
                pending = Some((ms + ex.lat_exit_ms, kind));
                continue;
            }
            exit = Some((ms, p, kind));
            break;
        }
    }
    let (xm, xp, kind) = exit.unwrap_or((last.0, last.1, ExitKind::Forced));
    let px_frame = xp - frac(xp.abs(), ex.slip_bps, 10_000);
    let mut legs = pos.legs;
    legs.push(TLeg {
        ms: xm,
        px: s * px_frame,
        u: pos.rem,
        maker: false,
    });
    Some(TickOut {
        entry_ms,
        entry_px: s * e,
        legs,
        exit_ms: xm,
        kind,
        takes: pos.next_tp as u32,
        marks,
    })
}

pub fn simulate_tick(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    sig: &TSig,
    natr_e8: i64,
    ex: &ExitCfg,
) -> Option<TickOut> {
    let mut cur = Cursor::new(src, &sym.name, usize::from(sig.day), sig.idx as usize + 1);
    sim_stream(&mut || cur.next_tick(), sig.ms, side, natr_e8, ex)
}

/// Деньги сделки (как `portfolio::settle`, но по времени сделок): валовая, комиссии, фандинг, суточные приращения.
pub fn settle_tick(sym: &Sym, side: Side, tr: &TickOut) -> Settled {
    let s = side.s() as f64;
    let e = tr.entry_px as f64;
    let n0 = NOTIONAL_U as f64 / 1e6;
    let mut flows: Vec<(i64, f64)> = Vec::new();
    let entry_fee = n0 * leg_fee_bps(true) / 1e4;
    flows.push((tr.entry_ms, -entry_fee));
    let mut gross = 0.0;
    let mut fees = entry_fee;
    for leg in &tr.legs {
        let u = leg.u as f64 / 1e6;
        let g = u * s * (leg.px - tr.entry_px) as f64 / e;
        let fee = u * (leg.px as f64 / e) * leg_fee_bps(!leg.maker) / 1e4;
        gross += g;
        fees += fee;
        flows.push((leg.ms, g - fee));
    }
    // фандинг: вход строго раньше момента выплаты, выход строго позже
    let mut funding = 0.0;
    for f in &sym.fund {
        let fms = i64::from(f.min) * MIN_MS;
        if !(fms > tr.entry_ms && fms < tr.exit_ms) {
            continue;
        }
        let closed: i64 = tr
            .legs
            .iter()
            .filter(|l| l.maker && l.ms < fms)
            .map(|l| l.u)
            .sum();
        let rem = (NOTIONAL_U - closed) as f64 / 1e6;
        let pay = -s * (f.rate_e8 as f64 / 1e8) * rem * (f.mark as f64 / e);
        funding += pay;
        flows.push((fms, pay));
    }
    let g0 = (tr.entry_ms / DAY_MS) as usize;
    let g1 = (tr.exit_ms / DAY_MS) as usize;
    let d_entry = g0 - START_DAY;
    let mut prev = 0.0;
    let mut daily = Vec::new();
    for g in g0..=g1 {
        let b = (g as i64 + 1) * DAY_MS;
        let realized: f64 = flows.iter().filter(|(m, _)| *m < b).map(|(_, v)| v).sum();
        let unreal = if tr.exit_ms < b {
            0.0
        } else {
            let closed: i64 = tr
                .legs
                .iter()
                .filter(|l| l.maker && l.ms < b)
                .map(|l| l.u)
                .sum();
            let rem = (NOTIONAL_U - closed) as f64 / 1e6;
            let mk = tr
                .marks
                .iter()
                .find(|(d, _)| *d == g)
                .map_or(tr.entry_px, |(_, p)| *p);
            rem * s * (mk - tr.entry_px) as f64 / e
        };
        let cum = realized + unreal;
        let d = g - START_DAY;
        let attr = if d_entry < HALF_DAYS {
            d.min(HALF_DAYS - 1)
        } else {
            d
        };
        daily.push((attr, cum - prev));
        prev = cum;
    }
    Settled {
        gross,
        fees,
        funding,
        daily,
    }
}

#[derive(Debug, Clone)]
pub struct TickTrade {
    pub sym: u16,
    pub level: u32,
    pub sig_ms: i64,
    pub sig_min: u32,
    pub entry_ms: i64,
    pub exit_ms: i64,
    pub entry_px: i64,
    pub kind: ExitKind,
    pub takes: u32,
    pub gross: f64,
    pub fees: f64,
    pub funding: f64,
    pub net: f64,
    /// Ноги сделки (тейки и остаток), NATR выхода (e8) и расстояние сигнала до уровня (×1e8) — для диагностики.
    pub legs: Vec<TLeg>,
    pub natr_e8: i64,
    pub dist: u32,
}

#[derive(Debug, Clone)]
pub struct TickResult {
    pub trades: Vec<TickTrade>,
    pub daily: [f64; COUNT_DAYS],
    pub coinday: BTreeMap<(u16, usize), f64>,
    /// Отказы: уровень израсходован, монета занята, пауза монеты, общая пауза, потолок P, нет исхода.
    pub rej: [u64; 6],
}

struct Open {
    exit_ms: i64,
    sym: usize,
    key: u64,
    kind: ExitKind,
}

/// Портфель стороны по сигналам-сделкам (как `portfolio::run`; паузы — в мс).
pub fn run_tick(
    syms: &[Sym],
    sigs: &[TSig],
    side: Side,
    ex: &ExitCfg,
    pc: &PortCfg,
    src: &dyn TradeSrc,
) -> TickResult {
    let ns = syms.len();
    let mut res = TickResult {
        trades: Vec::new(),
        daily: [0.0; COUNT_DAYS],
        coinday: BTreeMap::new(),
        rej: [0; 6],
    };
    let mut busy = vec![false; ns];
    let mut cool_until = vec![0i64; ns];
    let mut cpause_until = vec![0i64; ns];
    let mut streak = vec![0usize; ns];
    let mut gstops: VecDeque<i64> = VecDeque::new();
    let mut gpause_until = 0i64;
    let mut consumed: HashSet<u64> = HashSet::new();
    let mut open: Vec<Open> = Vec::new();
    let min = |m: usize| m as i64 * MIN_MS;
    for sg in sigs {
        let t = sg.ms;
        while !open.is_empty() && open[0].exit_ms <= t {
            let o = open.remove(0);
            busy[o.sym] = false;
            cool_until[o.sym] = o.exit_ms + min(pc.cool_min);
            if o.kind == ExitKind::HardStop {
                if pc.reentry == 1 {
                    consumed.insert(o.key);
                }
                streak[o.sym] += 1;
                if streak[o.sym] >= pc.m_stops {
                    cpause_until[o.sym] = o.exit_ms + min(pc.mm_min);
                    streak[o.sym] = 0;
                }
                gstops.push_back(o.exit_ms);
                while let Some(&f) = gstops.front() {
                    if o.exit_ms >= f + min(pc.w_min) {
                        gstops.pop_front();
                    } else {
                        break;
                    }
                }
                if gstops.len() >= pc.s_glob {
                    if pc.ww_min > 0 {
                        gpause_until = o.exit_ms + min(pc.ww_min);
                    }
                    gstops.clear();
                }
            } else {
                streak[o.sym] = 0;
            }
        }
        let si = sg.sym as usize;
        let key = (u64::from(sg.sym) << 32) | u64::from(sg.level);
        if consumed.contains(&key) {
            res.rej[0] += 1;
            continue;
        }
        if busy[si] {
            res.rej[1] += 1;
            continue;
        }
        if t < cool_until[si] || t < cpause_until[si] {
            res.rej[2] += 1;
            continue;
        }
        if t < gpause_until {
            res.rej[3] += 1;
            continue;
        }
        if open.len() >= pc.p {
            res.rej[4] += 1;
            continue;
        }
        let sym = &syms[si];
        let natr = sym
            .natr_for(ex.natr_p)
            .map_or(-1, |v| i64::from(v[sg.t as usize - 1]));
        let tr = if natr > 0 {
            simulate_tick(src, sym, side, sg, natr, ex)
        } else {
            None
        };
        let Some(tr) = tr else {
            res.rej[5] += 1;
            continue;
        };
        if pc.reentry == 0 {
            consumed.insert(key);
        }
        busy[si] = true;
        open.push(Open {
            exit_ms: tr.exit_ms,
            sym: si,
            key,
            kind: tr.kind,
        });
        open.sort_by_key(|o| o.exit_ms);
        let st = settle_tick(sym, side, &tr);
        for (d, v) in &st.daily {
            res.daily[*d] += v;
            *res.coinday.entry((sg.sym, *d)).or_insert(0.0) += v;
        }
        res.trades.push(TickTrade {
            sym: sg.sym,
            level: sg.level,
            sig_ms: sg.ms,
            sig_min: sg.t,
            entry_ms: tr.entry_ms,
            exit_ms: tr.exit_ms,
            entry_px: tr.entry_px,
            kind: tr.kind,
            takes: tr.takes,
            gross: st.gross,
            fees: st.fees,
            funding: st.funding,
            net: st.gross - st.fees + st.funding,
            legs: tr.legs.clone(),
            natr_e8: natr,
            dist: sg.dist,
        });
    }
    res
}
