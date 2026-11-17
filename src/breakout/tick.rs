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
use super::levels::{build_levels_far, Level};
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

/// Счётчик правила §13.14 (диагностика): кандидаты (уровень, минута), прошедшие прежние условия и отвергнутые правилом «свежий уровень».
pub static FR_REJECTED: AtomicU64 = AtomicU64::new(0);

/// Счётчик опции `pb` (§13.13, диагностика): уровни, убитые сделкой на уровне или за ним, которых запрет ложного пробоя к этой минуте не убил.
pub static PB_KILLED: AtomicU64 = AtomicU64::new(0);

/// Опция `ap` (§13.13): сколько суток назад искать решающую сделку.
pub const AP_LOOKBACK_DAYS: usize = 3;

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
    /// §13.19: дальний край зоны версии цены (длинная рама); без журнала членов — равен `lp`.
    pub far: i64,
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
    /// §13.16: цена версии уровня в минуте сигнала, длинная рама (`s·цена`, ×1e8).
    pub lp: i64,
    /// §13.19: дальний край зоны версии уровня (минимум цен пивотов в длинной раме), для стопа от уровня (`ls`, `bm = 3`).
    pub far: i64,
    /// §13.13 (`ap = 2`): цена пришла в зону сверху (сигнал помечен, не отвергнут).
    pub ws: bool,
}

#[derive(Debug, Default, Clone)]
pub struct TickCtr {
    pub cands: u64,
    pub signals: [u64; 2],
    pub missing_days: u64,
    /// §13.13: сигналов, пришедших в зону сверху (при `ap = 1` отвергнуты, при `ap = 2` помечены).
    pub ap_wrong: u64,
}

impl TickCtr {
    pub fn add(&mut self, o: &TickCtr) {
        self.cands += o.cands;
        self.missing_days += o.missing_days;
        self.ap_wrong += o.ap_wrong;
        for h in 0..2 {
            self.signals[h] += o.signals[h];
        }
    }
}

fn half_of_day(day: usize) -> usize {
    usize::from(day >= START_DAY + HALF_DAYS)
}

/// §13.14 «свежий уровень»: допуск кандидата (уровень `lv`, минута `t`), цена версии `lp` в длинной раме (`s` — сторона). По закрытым
/// минутам ≤ `t−1`. (а) последнее касание (high/low не ниже `lp − |lp|·τ`, минутки без данных не в счёт) не старше H минут,
/// окно — от бара первого пивота; (б) в минутах `t−K … t−1` не меньше `K·share/100` закрытий строго ниже `lp` и не дальше `b` %
/// от него, и ни одного закрытия на уровне или за ним. Допуск — (а) или (б); правило выключено — всегда `true`.
pub fn fresh_ok(sym: &Sym, cfg: &SigCfg, lv: &Level, s: i64, lp: i64, t: usize) -> bool {
    if !cfg.fresh_on() {
        return true;
    }
    if cfg.fr_h_min > 0 {
        let tau = (i128::from(lp.abs()) * i128::from(cfg.lv.tau_pm) / 100_000) as i64;
        let band = lp - tau;
        let lo = (lv.first_bar as usize * cfg.lv.tf_min).max(t.saturating_sub(cfg.fr_h_min));
        let touch = |m: usize| sym.o[m] != 0 && (if s > 0 { sym.h[m] } else { -sym.l[m] }) >= band;
        if (lo..t).any(touch) {
            return true;
        }
    }
    if cfg.fr_share > 0 {
        let lim = (i128::from(lp.abs()) * i128::from(cfg.fr_b_pm) / 100_000) as i64;
        let need = cfg.fr_k * cfg.fr_share / 100;
        let mut n_in = 0usize;
        let mut beyond = false;
        for m in t.saturating_sub(cfg.fr_k)..t {
            if sym.o[m] == 0 {
                continue;
            }
            let c = s * sym.c[m];
            if c >= lp {
                beyond = true;
                break;
            }
            if lp - c <= lim {
                n_in += 1;
            }
        }
        if !beyond && n_in >= need {
            return true;
        }
    }
    false
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
        let mut far = lv.far_edge(0, s);
        let mut pend: Option<usize> = None;
        let mut forbidden = false;
        let mut pk_dead = false;
        for t in lv.born as usize..end {
            while seg + 1 < lv.segs.len() && lv.segs[seg + 1].0 as usize <= t {
                seg += 1;
                price = lv.segs[seg].1;
                far = lv.far_edge(seg, s);
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
                    if fresh_ok(sym, cfg, lv, s, lp, t) {
                        out.push(Cand {
                            t: t as u32,
                            level: li as u32,
                            lp,
                            far: far.unwrap_or(lp),
                        });
                    } else {
                        FR_REJECTED.fetch_add(1, Ordering::Relaxed);
                    }
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
            // §13.13 (`pb`): сделка минуты на уровне или за ним (high ≥ цены версии) убивает уровень со следующей минуты
            if cfg.pb && hi >= lp {
                if !forbidden {
                    PB_KILLED.fetch_add(1, Ordering::Relaxed);
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
            .or_insert_with(|| build_levels_far(sym, &cfg.lv, *side));
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
                let mut ws = false;
                if cfg.ap != 0
                    && came_from_above(src, &sym.name, day, &ticks, k, side.s(), (c.lp, dmax))
                {
                    ctrs[j].ap_wrong += 1;
                    if cfg.ap == 1 {
                        continue;
                    }
                    ws = true;
                }
                outs[j].push(TSig {
                    ms: day as i64 * DAY_MS + i64::from(ticks[k].off),
                    sym: si,
                    level: c.level,
                    dist,
                    t: c.t,
                    day: day as u16,
                    idx: k as u32,
                    lp: c.lp,
                    far: c.far,
                    ws,
                });
                ctrs[j].signals[half_of_day(day)] += 1;
            }
        }
    }
}

/// §13.13 (`ap`): сигнал по сделке `k` суток `day` пришёл в зону сверху? Назад по сделкам монеты (не дальше
/// `AP_LOOKBACK_DAYS` суток): решает первая сделка, которая дошла до уровня (`p ≥ lp` — цена пришла сверху → `true`)
/// или стоит ниже зоны (`dist > dmax` — подход снизу → `false`); сделки внутри зоны не решают; решающей нет → `false`.
/// `zone` = (цена уровня в длинной раме, `dmax` ×1e8).
pub fn came_from_above(
    src: &dyn TradeSrc,
    name: &str,
    day: usize,
    ticks: &[Tick],
    k: usize,
    s: i64,
    zone: (i64, i64),
) -> bool {
    let (lp, dmax) = zone;
    let decide = |tk: &Tick| -> Option<bool> {
        let p = s * tk.px;
        if p >= lp {
            return Some(true);
        }
        let dist = (i128::from(lp - p) * i128::from(SCALE) / i128::from(lp.abs())) as i64;
        (dist > dmax).then_some(false)
    };
    if let Some(r) = ticks[..k].iter().rev().find_map(decide) {
        return r;
    }
    for back in 1..=AP_LOOKBACK_DAYS {
        let Some(d) = day.checked_sub(back) else {
            break;
        };
        if let Some(v) = src.day(name, d) {
            if let Some(r) = v.iter().rev().find_map(decide) {
                return r;
            }
        }
    }
    false
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

/// Множитель ступени `i` лестницы тейков в тысячных NATR (§13.12): `t_low + (t_high − t_low)·(i/(n−1))^γ`; γ = 1 — прежняя
/// целая арифметика, концы лестницы не двигаются при любом γ.
pub fn tp_mult_m(ex: &ExitCfg, i: i64, n: i64) -> i64 {
    if ex.tp_gamma_m == 1000 || i == 0 || i == n - 1 {
        return ex.t_low_m + (ex.t_high_m - ex.t_low_m) * i / (n - 1);
    }
    let x = (i as f64 / (n - 1) as f64).powf(ex.tp_gamma_m as f64 / 1000.0);
    ex.t_low_m + ((ex.t_high_m - ex.t_low_m) as f64 * x).round() as i64
}

/// Доли ступеней в мкУЕ (§13.12): вес ∝ `1 + κ·i/(n−1)`, сумма — `q` от позиции, остаток целого деления — последней ступени;
/// κ = 0 — прежний равный лот.
pub fn tp_lots(ex: &ExitCfg) -> Vec<i64> {
    tp_lots_n(ex, NOTIONAL_U)
}

/// То же для лота размера `notional` (мкУЕ): §13.19 — два входа по 50 %.
pub fn tp_lots_n(ex: &ExitCfg, notional: i64) -> Vec<i64> {
    let n = ex.n_tp as i64;
    if ex.tp_kappa_m == 0 {
        return vec![notional * ex.q_pct / (100 * n); ex.n_tp];
    }
    let total = notional * ex.q_pct / 100;
    let w: Vec<i128> = (0..n)
        .map(|i| i128::from(1000 * (n - 1) + ex.tp_kappa_m * i))
        .collect();
    let sw: i128 = w.iter().sum();
    let mut lots: Vec<i64> = w
        .iter()
        .map(|&x| (i128::from(total) * x / sw) as i64)
        .collect();
    let done: i64 = lots.iter().sum();
    if let Some(last) = lots.last_mut() {
        *last += total - done;
    }
    lots
}

/// NATR выхода (e8) для сигнала на минуте `t` (§13.2 п. 3; `ns`, §13.12): закрытая минутка `t−1` или, при `ns = 1`, `t`.
pub fn exit_natr(sym: &Sym, ex: &ExitCfg, t: usize) -> i64 {
    sym.natr_for(ex.natr_p)
        .map_or(-1, |v| i64::from(v[t - 1 + usize::from(ex.natr_src)]))
}

/// Цена версии уровня на сигнале и дальний край зоны (длинная рама) — для выходов от уровня (§13.19).
#[derive(Debug, Clone, Copy)]
pub struct LevelCtx {
    pub lp: i64,
    pub far: i64,
}

const NONE_MS: i64 = i64::MIN;

/// Состояние позиции в длинной раме; сделки подаются по одной.
struct Pos {
    e: i64,
    tps: Vec<i64>,
    lots: Vec<i64>,
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
    /// §13.19: безубыток (режим `bm`) и его стоп «за уровнем».
    be_mode: u8,
    be_lvl: Option<i64>,
    /// §13.19: правила от уровня — цена уровня (рама), выход по времени (мс; 0 — нет) и глубина «пробоя», возврат (глубина, мс; 0 — нет).
    rules: bool,
    lp: i64,
    tx_ms: i64,
    tx_x: i64,
    rt_d: i64,
    rt_ms: i64,
    touched_ms: i64,
    broke: bool,
    below_since: i64,
}

impl Pos {
    fn new(
        s: i64,
        e: i64,
        p0: i64,
        natr_e8: i64,
        ex: &ExitCfg,
        lc: Option<LevelCtx>,
        notional: i64,
    ) -> Pos {
        let ea = e.abs();
        let nat = |x_m: i64| -> i64 {
            (i128::from(ea) * i128::from(natr_e8) * i128::from(x_m) / (i128::from(SCALE) * 1000))
                as i64
        };
        let n = ex.n_tp as i64;
        let mut stop_base = e - frac(ea, ex.s_pm, 100_000);
        let mut be_lvl = None;
        if let Some(l) = lc {
            if ex.ls_m > 0 {
                stop_base = stop_base.max(e.min(l.far) - nat(ex.ls_m));
            }
            if ex.be_mode == 3 {
                let kb = if ex.ls_m > 0 { ex.ls_m } else { 500 };
                be_lvl = Some(e.min(l.far) - nat(kb));
            }
        }
        let rules = lc.is_some() && (ex.tx_min > 0 || ex.rt_f_pm > 0);
        let lp = lc.map_or(0, |l| l.lp);
        let dist = |x_pm: i64| (i128::from(lp.abs()) * i128::from(x_pm) / 100_000) as i64;
        Pos {
            e,
            tps: (0..n).map(|i| e + nat(tp_mult_m(ex, i, n))).collect(),
            lots: tp_lots_n(ex, notional),
            a_dist: nat(ex.a_m),
            r_dist: nat(ex.r_m),
            be_cfg: ex.be,
            rem: notional,
            stop_base,
            be_on: false,
            armed: false,
            trail: i64::MIN,
            maxp: p0,
            next_tp: 0,
            legs: Vec::new(),
            s,
            be_mode: ex.be_mode,
            be_lvl,
            rules,
            lp,
            tx_ms: ex.tx_min * 60_000,
            tx_x: dist(ex.tx_x_pm),
            rt_d: dist(ex.rt_f_pm),
            rt_ms: ex.rt_s * 1000,
            touched_ms: NONE_MS,
            broke: false,
            below_since: NONE_MS,
        }
    }

    /// Тейки-лимитки, пройденные сделкой `p` строго насквозь.
    fn fill_takes(&mut self, ms: i64, p: i64) {
        while self.next_tp < self.tps.len() && p > self.tps[self.next_tp] {
            let lot = self.lots[self.next_tp];
            self.legs.push(TLeg {
                ms,
                px: self.s * self.tps[self.next_tp],
                u: lot,
                maker: true,
            });
            self.rem -= lot;
            if self.be_cfg {
                // §13.19 `bm`: 0 — стоп в цену входа после 1-го тейка; 1 — выкл; 2 — после 2-го; 3 — за уровнем
                let to = match (self.be_mode, self.next_tp) {
                    (0 | 3, 0) | (2, 1) => Some(self.be_lvl.unwrap_or(self.e)),
                    _ => None,
                };
                if let Some(lvl) = to {
                    self.be_on = true;
                    self.stop_base = self.stop_base.max(lvl);
                }
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
        if self.rules {
            return self.level_exit(ms, p);
        }
        None
    }

    /// §13.19: выход по времени (`tt/tb`) и при возврате за уровень (`rf/rs`); «пересекли» — сделка `p ≥ L`.
    fn level_exit(&mut self, ms: i64, p: i64) -> Option<ExitKind> {
        if self.touched_ms == NONE_MS && p >= self.lp {
            self.touched_ms = ms;
        }
        if self.touched_ms == NONE_MS {
            return None;
        }
        if !self.broke && p >= self.lp + self.tx_x {
            self.broke = true;
        }
        if self.tx_ms > 0 && !self.broke && ms >= self.touched_ms + self.tx_ms {
            return Some(ExitKind::TimeExit);
        }
        if self.rt_d > 0 {
            if p <= self.lp - self.rt_d {
                if self.below_since == NONE_MS {
                    self.below_since = ms;
                }
                if ms - self.below_since >= self.rt_ms {
                    return Some(ExitKind::Return);
                }
            } else {
                self.below_since = NONE_MS;
            }
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
    Some(sim_tail(
        next, entry_ms, p0, side, natr_e8, ex, None, NOTIONAL_U,
    ))
}

/// §13.16: вход заполнен в момент `entry_ms` по цене `p0` (рама); выходы — по сделкам со временем строго после `entry_ms`.
pub fn sim_fill(
    next: &mut dyn FnMut() -> Option<(i64, i64, bool)>,
    entry_ms: i64,
    p0: i64,
    side: Side,
    natr_e8: i64,
    ex: &ExitCfg,
) -> TickOut {
    sim_fill_lc(next, entry_ms, p0, side, natr_e8, ex, None, NOTIONAL_U)
}

/// `sim_fill` с контекстом уровня (§13.19: стоп/выходы от уровня) и размером лота `notional` (мкУЕ).
#[allow(clippy::too_many_arguments)]
pub fn sim_fill_lc(
    next: &mut dyn FnMut() -> Option<(i64, i64, bool)>,
    entry_ms: i64,
    p0: i64,
    side: Side,
    natr_e8: i64,
    ex: &ExitCfg,
    lc: Option<LevelCtx>,
    notional: i64,
) -> TickOut {
    let mut after = || -> Option<(i64, i64, bool)> {
        loop {
            let t = next()?;
            if t.0 > entry_ms {
                return Some(t);
            }
        }
    };
    sim_tail(&mut after, entry_ms, p0, side, natr_e8, ex, lc, notional)
}

/// Позиция после входа: цена входа `p0` (рама) ± проскальзывание, тейки, стоп, трейл по потоку `next` (сделки после входа).
#[allow(clippy::too_many_arguments)]
fn sim_tail(
    next: &mut dyn FnMut() -> Option<(i64, i64, bool)>,
    entry_ms: i64,
    p0: i64,
    side: Side,
    natr_e8: i64,
    ex: &ExitCfg,
    lc: Option<LevelCtx>,
    notional: i64,
) -> TickOut {
    let s = side.s();
    let e = p0 + frac(p0.abs(), ex.slip_bps, 10_000);
    let mut pos = Pos::new(s, e, p0, natr_e8, ex, lc, notional);
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
    TickOut {
        entry_ms,
        entry_px: s * e,
        legs,
        exit_ms: xm,
        kind,
        takes: pos.next_tp as u32,
        marks,
    }
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
    let taker_bm = side == Side::Short;
    let mut next = || cur.next_tick();
    let (entry_ms, p0) = loop {
        let (ms, px, bm) = next()?;
        if ms >= sig.ms + ex.lat_ms && bm == taker_bm {
            break (ms, side.s() * px);
        }
    };
    let lc = LevelCtx {
        lp: sig.lp,
        far: sig.far,
    };
    Some(sim_tail(
        &mut next,
        entry_ms,
        p0,
        side,
        natr_e8,
        ex,
        Some(lc),
        NOTIONAL_U,
    ))
}

/// Причина отказа входа по ленте (§13.16).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EntryRej {
    /// Цена-заменитель уже за уровнем больше чем на X бп.
    Beyond,
    /// Нет сделки стороны входа со временем ≤ `сигнал + lat` не старше W мс.
    Stale,
}

/// Решение о входе для сигнала (§13.16).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EntryDec {
    /// `ew = 0`: прежний вход — следующая сделка стороны (§13.2 п. 2).
    Next,
    /// Вход в момент `ms` по цене `p0` (рама).
    Fill {
        ms: i64,
        p0: i64,
    },
    Reject(EntryRej),
}

/// Последняя сделка стороны входа (лонг — покупатель-тейкер, `bm = false`; шорт — `bm = true`) со временем ≤ `t_abs` (мс от
/// 01.09) не старше `cap_ms`: (цена ×1e8, возраст мс). `ticks` — сутки `day`; при исчерпании суток в пределах возраста — предыдущие.
pub fn tape_last_side(
    src: &dyn TradeSrc,
    name: &str,
    day: usize,
    ticks: &[Tick],
    t_abs: i64,
    taker_bm: bool,
    cap_ms: i64,
) -> Option<(i64, i64)> {
    let t_off = t_abs - day as i64 * DAY_MS;
    let hi = ticks.partition_point(|x| i64::from(x.off) <= t_off);
    for tk in ticks[..hi].iter().rev() {
        let age = t_off - i64::from(tk.off);
        if age > cap_ms {
            return None;
        }
        if tk.bm == taker_bm {
            return Some((tk.px, age));
        }
    }
    let v = src.day(name, day.checked_sub(1)?)?;
    for tk in v.iter().rev() {
        let age = t_off + DAY_MS - i64::from(tk.off);
        if age > cap_ms {
            return None;
        }
        if tk.bm == taker_bm {
            return Some((tk.px, age));
        }
    }
    None
}

/// §13.16: вход по ленте «как на момент сигнала + `lat`»: цена-заменитель — последняя сделка стороны ≤ `T` не старше W; за уровнем
/// больше чем на X бп — отказ.
pub fn entry_decision(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    sig: &TSig,
    ex: &ExitCfg,
) -> EntryDec {
    if !ex.tape_entry() {
        return EntryDec::Next;
    }
    entry_at(
        src,
        sym,
        side,
        usize::from(sig.day),
        sig.ms + ex.lat_ms,
        sig.lp,
        ex,
    )
}

/// Вход по ленте на момент `t` (мс от 01.09; сутки файла — `day`) при цене уровня `lp` (рама): цена-заменитель — последняя сделка
/// стороны ≤ `t` не старше W; за уровнем больше чем на X бп — отказ.
pub fn entry_at(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    day: usize,
    t: i64,
    lp: i64,
    ex: &ExitCfg,
) -> EntryDec {
    let Some(ticks) = src.day(&sym.name, day) else {
        return EntryDec::Reject(EntryRej::Stale);
    };
    let Some((px, _)) = tape_last_side(
        src,
        &sym.name,
        day,
        &ticks,
        t,
        side == Side::Short,
        ex.ent_w_ms,
    ) else {
        return EntryDec::Reject(EntryRej::Stale);
    };
    let p = side.s() * px;
    if i128::from(p - lp) * 10_000 > i128::from(ex.ent_x_bp) * i128::from(lp.abs()) {
        return EntryDec::Reject(EntryRej::Beyond);
    }
    EntryDec::Fill { ms: t, p0: p }
}

/// Решение о втором лоте (§13.19, `e2`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Lot2Dec {
    /// Лот 2 входит в `ms` по цене `p0` (рама); `immediate` — цена лота 1 уже в пределах d, вход в тот же момент по той же цене.
    Fill { ms: i64, p0: i64, immediate: bool },
    /// Лот 1 закрыт к моменту входа / цена пересекла уровень раньше, чем подошла на d.
    Cancel,
    /// Цена-заменитель лота 2 за уровнем или ленты нет — отказ (как у лота 1).
    Reject,
}

/// Второй лот (§13.19): рыночный вход, когда цена подошла к уровню ближе d (`p ≥ L − d·|L|`) и ещё не пересекла (`p < L`), пока лот 1 открыт.
/// `lot1` — (время входа, цена без проскальзывания) лота 1, `lot1_exit_ms` — время его выхода.
pub fn lot2_decision(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    sig: &TSig,
    ex: &ExitCfg,
    lot1: (i64, i64),
    lot1_exit_ms: i64,
) -> Lot2Dec {
    let d = (i128::from(sig.lp.abs()) * i128::from(ex.e2_pm) / 100_000) as i64;
    let (ms1, p1) = lot1;
    if p1 >= sig.lp - d {
        return Lot2Dec::Fill {
            ms: ms1,
            p0: p1,
            immediate: true,
        };
    }
    let s = side.s();
    let mut cur = Cursor::new(src, &sym.name, usize::from(sig.day), sig.idx as usize + 1);
    while let Some((ms, px, _)) = cur.next_tick() {
        if ms <= ms1 {
            continue;
        }
        if ms >= lot1_exit_ms {
            return Lot2Dec::Cancel;
        }
        let p = s * px;
        if p >= sig.lp {
            return Lot2Dec::Cancel;
        }
        if p >= sig.lp - d {
            let t2 = ms + ex.lat_ms;
            if t2 >= lot1_exit_ms {
                return Lot2Dec::Cancel;
            }
            return match entry_at(src, sym, side, (t2 / DAY_MS) as usize, t2, sig.lp, ex) {
                EntryDec::Fill { ms, p0 } => Lot2Dec::Fill {
                    ms,
                    p0,
                    immediate: false,
                },
                _ => Lot2Dec::Reject,
            };
        }
    }
    Lot2Dec::Cancel
}

/// Вход прежней модели для сигнала (§13.2 п. 2): (время, цена ×1e8 без проскальзывания) первой сделки стороны со временем
/// ≥ `сигнал + lat` после сигнальной; справочно для сравнения с §13.16.
pub fn old_entry(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    sig: &TSig,
    ex: &ExitCfg,
) -> Option<(i64, i64)> {
    let mut cur = Cursor::new(src, &sym.name, usize::from(sig.day), sig.idx as usize + 1);
    let taker_bm = side == Side::Short;
    while let Some((ms, px, bm)) = cur.next_tick() {
        if ms >= sig.ms + ex.lat_ms && bm == taker_bm {
            return Some((ms, px));
        }
    }
    None
}

/// Цена входа прежней модели с проскальзыванием (`px` — настоящая цена сделки, ×1e8).
fn with_slip(side: Side, px: i64, slip_bps: i64) -> i64 {
    let p0 = side.s() * px;
    side.s() * (p0 + frac(p0.abs(), slip_bps, 10_000))
}

/// Деньги сделки (как `portfolio::settle`, но по времени сделок): валовая, комиссии, фандинг, суточные приращения.
pub fn settle_tick(sym: &Sym, side: Side, tr: &TickOut) -> Settled {
    settle_tick_n(sym, side, tr, NOTIONAL_U)
}

/// `settle_tick` для лота размера `notional` (мкУЕ): §13.19 — два входа по 50 %.
pub fn settle_tick_n(sym: &Sym, side: Side, tr: &TickOut, notional: i64) -> Settled {
    let s = side.s() as f64;
    let e = tr.entry_px as f64;
    let n0 = notional as f64 / 1e6;
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
        let rem = (notional - closed) as f64 / 1e6;
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
            let rem = (notional - closed) as f64 / 1e6;
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
    /// §13.13: сигнал помечен `ap = 2` (цена пришла в зону сверху).
    pub ws: bool,
    /// §13.16 (только при `ew > 0`): вход прежней модели для этого сигнала — (время мс от 01.09, цена ×1e8 с проскальзыванием).
    pub old_entry: Option<(i64, i64)>,
    /// §13.19 (`e2`): второй лот (свой вход, ноги, выход); `entry_ms`, `entry_px`, `legs` выше — лот 1, `exit_ms` — последний выход.
    pub lot2: Option<TickOut>,
    /// §13.19: вид выхода лота 1 (при `lot2` поле `kind` — вид сделки целиком).
    pub kind1: ExitKind,
}

#[derive(Debug, Clone)]
pub struct TickResult {
    pub trades: Vec<TickTrade>,
    /// §13.16: отвергнутые входом сигналы (монета, уровень, `sig_ms`, причина).
    pub entry_rej: Vec<(u16, u32, i64, EntryRej)>,
    pub daily: [f64; COUNT_DAYS],
    pub coinday: BTreeMap<(u16, usize), f64>,
    /// Отказы: уровень израсходован, монета занята, пауза монеты, общая пауза, потолок P, нет исхода.
    pub rej: [u64; 6],
    /// §13.19 (`e2`): лот 2 — вошёл сразу (цена лота 1 уже в пределах d), вошёл позже, отменён (лот 1 закрыт / цена пересекла уровень), отказ (за уровнем / нет ленты).
    pub lot2: [u64; 4],
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
        entry_rej: Vec::new(),
        daily: [0.0; COUNT_DAYS],
        coinday: BTreeMap::new(),
        rej: [0; 6],
        lot2: [0; 4],
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
        let natr = exit_natr(sym, ex, sg.t as usize);
        let mut dec = EntryDec::Next;
        let mut tr2: Option<TickOut> = None;
        let lc = LevelCtx {
            lp: sg.lp,
            far: sg.far,
        };
        let split = ex.e2_pm > 0;
        let lot_n = if split { NOTIONAL_U / 2 } else { NOTIONAL_U };
        let tr = if natr > 0 {
            dec = entry_decision(src, sym, side, sg, ex);
            match dec {
                EntryDec::Next => simulate_tick(src, sym, side, sg, natr, ex),
                EntryDec::Fill { ms, p0 } => {
                    let mut cur =
                        Cursor::new(src, &sym.name, usize::from(sg.day), sg.idx as usize + 1);
                    let t1 = sim_fill_lc(
                        &mut || cur.next_tick(),
                        ms,
                        p0,
                        side,
                        natr,
                        ex,
                        Some(lc),
                        lot_n,
                    );
                    if split {
                        match lot2_decision(src, sym, side, sg, ex, (ms, p0), t1.exit_ms) {
                            Lot2Dec::Fill {
                                ms: m2,
                                p0: q0,
                                immediate,
                            } => {
                                res.lot2[usize::from(!immediate)] += 1;
                                let mut cur2 = Cursor::new(
                                    src,
                                    &sym.name,
                                    usize::from(sg.day),
                                    sg.idx as usize + 1,
                                );
                                tr2 = Some(sim_fill_lc(
                                    &mut || cur2.next_tick(),
                                    m2,
                                    q0,
                                    side,
                                    natr,
                                    ex,
                                    Some(lc),
                                    lot_n,
                                ));
                            }
                            Lot2Dec::Cancel => res.lot2[2] += 1,
                            Lot2Dec::Reject => res.lot2[3] += 1,
                        }
                    }
                    Some(t1)
                }
                EntryDec::Reject(why) => {
                    res.entry_rej.push((sg.sym, sg.level, sg.ms, why));
                    None
                }
            }
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
        // §13.19: позиция из двух лотов — выход последнего лота; «стоп», если хоть один лот закрыт стопом
        let (exit_all, kind_all) = match &tr2 {
            Some(t2) => (
                tr.exit_ms.max(t2.exit_ms),
                if tr.kind == ExitKind::HardStop || t2.kind == ExitKind::HardStop {
                    ExitKind::HardStop
                } else if t2.exit_ms > tr.exit_ms {
                    t2.kind
                } else {
                    tr.kind
                },
            ),
            None => (tr.exit_ms, tr.kind),
        };
        open.push(Open {
            exit_ms: exit_all,
            sym: si,
            key,
            kind: kind_all,
        });
        open.sort_by_key(|o| o.exit_ms);
        let mut st = settle_tick_n(sym, side, &tr, lot_n);
        if let Some(t2) = &tr2 {
            let s2 = settle_tick_n(sym, side, t2, lot_n);
            st.gross += s2.gross;
            st.fees += s2.fees;
            st.funding += s2.funding;
            st.daily.extend(s2.daily);
        }
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
            exit_ms: exit_all,
            entry_px: tr.entry_px,
            kind: kind_all,
            takes: tr.takes + tr2.as_ref().map_or(0, |t| t.takes),
            gross: st.gross,
            fees: st.fees,
            funding: st.funding,
            net: st.gross - st.fees + st.funding,
            legs: tr.legs.clone(),
            natr_e8: natr,
            dist: sg.dist,
            ws: sg.ws,
            old_entry: if matches!(dec, EntryDec::Fill { .. }) {
                old_entry(src, sym, side, sg, ex)
                    .map(|(ms, px)| (ms, with_slip(side, px, ex.slip_bps)))
            } else {
                None
            },
            kind1: tr.kind,
            lot2: tr2,
        });
    }
    res
}
