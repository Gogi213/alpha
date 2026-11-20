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
    START_MIN,
};
use super::levels::{build_levels_far, Level};
use super::params::{ExitCfg, LevelCfg, PortCfg, SigCfg};
use super::portfolio::Settled;
use super::store::{DayHead, DayTrades, QtyRaw, SrcMeta, TickStore};
use super::trade::{frac, ExitKind, NOTIONAL_U};
use super::Side;
use crate::lob::costs::leg_fee_bps;
use anyhow::{bail, Context, Result};
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::fs;
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

/// `a·b/d` с усечением к нулю — как прежнее `i128`-выражение; быстрый путь в `i64`, при переполнении (и делителе 0, `MIN/−1`) — `i128`.
#[inline]
pub fn mul_div(a: i64, b: i64, d: i64) -> i128 {
    if let Some(q) = a.checked_mul(b).and_then(|x| x.checked_div(d)) {
        return i128::from(q);
    }
    i128::from(a) * i128::from(b) / i128::from(d)
}

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

/// Неотрицательная десятичная строка (в том числе `1.5E-4`) → `(мантисса, знаков после точки)`: значение = мантисса / 10^знаков,
/// хвостовые нули дробной части убраны; `None` — не число или не помещается в `u64`/`u8`.
pub fn dec_parts(b: &[u8]) -> Option<(u64, u8)> {
    let (m, e) = match b.iter().position(|&c| c == b'E' || c == b'e') {
        Some(k) => (&b[..k], Some(&b[k + 1..])),
        None => (b, None),
    };
    let mut exp = 0i32;
    if let Some(e) = e {
        let (neg, e) = match e.first()? {
            b'-' => (true, &e[1..]),
            b'+' => (false, &e[1..]),
            _ => (false, e),
        };
        if e.is_empty() || e.len() > 3 {
            return None;
        }
        for &c in e {
            let d = c.wrapping_sub(b'0');
            if d > 9 {
                return None;
            }
            exp = exp * 10 + i32::from(d);
        }
        if neg {
            exp = -exp;
        }
    }
    let (mut mant, mut dec, mut dot, mut any) = (0u64, 0i32, false, false);
    for &c in m {
        if c == b'.' && !dot {
            dot = true;
            continue;
        }
        let d = c.wrapping_sub(b'0');
        if d > 9 {
            return None;
        }
        mant = mant.checked_mul(10)?.checked_add(u64::from(d))?;
        dec += i32::from(dot);
        any = true;
    }
    if !any {
        return None;
    }
    dec -= exp;
    while dec > 0 && mant % 10 == 0 {
        mant /= 10;
        dec -= 1;
    }
    if dec < 0 {
        mant = mant.checked_mul(10u64.checked_pow(dec.unsigned_abs())?)?;
        dec = 0;
    }
    u8::try_from(dec).ok().map(|d| (mant, d))
}

/// CSV суток (`agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker`) → сделки.
pub fn parse_trades(rd: impl BufRead, day: usize, st: &mut LoadStat) -> Result<Vec<Tick>> {
    parse_rows(rd, day, st, None)
}

/// То же и количество (TK-609): сделки те же, что у `parse_trades`, плюс количество по строкам и флаги точности.
pub fn parse_trades_full(rd: impl BufRead, day: usize, mut st: LoadStat) -> Result<DayTrades> {
    let mut raw = QtyRaw::default();
    let ticks = parse_rows(rd, day, &mut st, Some(&mut raw))?;
    Ok(DayTrades::new(ticks, raw, st))
}

fn parse_rows(
    mut rd: impl BufRead,
    day: usize,
    st: &mut LoadStat,
    mut col: Option<&mut QtyRaw>,
) -> Result<Vec<Tick>> {
    let base = super::data::t0_ms() + day as i64 * DAY_MS;
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
        let (_, Some(p), q, _, _, Some(t), Some(b)) = (
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
        if let Some(c) = col.as_deref_mut() {
            c.push(p, px, q);
        }
        st.rows += 1;
    }
    if unsorted {
        st.unsorted_files += 1;
        match col {
            Some(c) => c.sort_with(&mut v),
            None => v.sort_by_key(|x| x.off),
        }
    }
    Ok(v)
}

struct Lru {
    map: HashMap<(String, usize), Option<Arc<Vec<Tick>>>>,
    order: VecDeque<(String, usize)>,
    bytes: usize,
}

/// Итог `ZipSrc::fold_day`: нет файла zip / склад не дал сутки (промах, отступления, повреждение) / сутки свёрнуты.
pub enum Fold {
    NoZip,
    Miss,
    Done(DayHead),
}

/// Сутки из `<dir>/<МОНЕТА>/<МОНЕТА>-aggTrades-<день>.zip` (распаковка внешним `unzip -p`), с ограничением памяти.
pub struct ZipSrc {
    dir: PathBuf,
    cap_bytes: usize,
    lru: Mutex<Lru>,
    pub stat: Mutex<LoadStat>,
    /// TK-607 (L1): склад разобранных суток; без него — прежний путь (`unzip` + CSV).
    store: Option<Arc<TickStore>>,
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
            store: None,
        }
    }

    pub fn with_store(mut self, store: Arc<TickStore>) -> ZipSrc {
        self.store = Some(store);
        self
    }

    /// Сутки монеты со склада или из zip. `full` — вместе с количеством (TK-609, `read_day`); без него — путь движка: со склада
    /// читается только блок сделок, а количество при первой загрузке разбирается лишь ради записи в склад и отбрасывается.
    fn load_day(&self, name: &str, day: usize, full: bool) -> Result<Option<DayTrades>> {
        let date = day_string(day);
        let p = self
            .dir
            .join(name)
            .join(format!("{name}-aggTrades-{date}.zip"));
        let Ok(md) = fs::metadata(&p) else {
            return Ok(None);
        };
        let meta = SrcMeta::of(&md);
        if let (Some(store), Some(meta)) = (&self.store, &meta) {
            let hit = if full {
                store.read_day(name, &date, meta)
            } else {
                store
                    .read(name, &date, meta)
                    .map(|(ticks, stat)| DayTrades {
                        ticks,
                        stat,
                        ..DayTrades::default()
                    })
            };
            if let Some(d) = hit {
                if let Ok(mut g) = self.stat.lock() {
                    g.add(&d.stat);
                }
                return Ok(Some(d));
            }
        }
        let mut child = Command::new("unzip")
            .arg("-p")
            .arg(&p)
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .with_context(|| format!("unzip {}", p.display()))?;
        let out = child.stdout.take().context("unzip: нет stdout")?;
        let rd = BufReader::with_capacity(1 << 20, out);
        let parsed = if full || self.store.is_some() {
            parse_trades_full(rd, day, LoadStat::default())
        } else {
            let mut st = LoadStat::default();
            parse_trades(rd, day, &mut st).map(|ticks| DayTrades {
                ticks,
                stat: st,
                ..DayTrades::default()
            })
        };
        let status = child.wait()?;
        let mut d = parsed?;
        if !status.success() {
            bail!("unzip {}: {status}", p.display());
        }
        d.stat.files = 1;
        if let (Some(store), Some(meta)) = (&self.store, &meta) {
            store.write(name, &date, meta, &d);
        }
        if let Ok(mut g) = self.stat.lock() {
            g.add(&d.stat);
        }
        if !full {
            d.qty = Vec::new();
        }
        Ok(Some(d))
    }

    /// Сутки с количеством, без кэша (потребитель решает сам, что держать в памяти): со склада или из zip с записью в склад.
    pub fn read_day(&self, name: &str, day: usize) -> Result<Option<DayTrades>> {
        self.load_day(name, day, true)
    }

    /// TK-609 (v6): сутки со склада свёрткой (`TickStore::fold_day`) — без массивов и без записи в склад.
    pub fn fold_day<F: FnMut(u32, i64, bool, u64)>(
        &self,
        name: &str,
        day: usize,
        start: impl FnOnce(&DayHead) -> Option<F>,
    ) -> Fold {
        let date = day_string(day);
        let p = self
            .dir
            .join(name)
            .join(format!("{name}-aggTrades-{date}.zip"));
        let Ok(md) = fs::metadata(&p) else {
            return Fold::NoZip;
        };
        match (&self.store, SrcMeta::of(&md)) {
            (Some(store), Some(meta)) => store
                .fold_day(name, &date, &meta, start)
                .map_or(Fold::Miss, Fold::Done),
            _ => Fold::Miss,
        }
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
        let loaded = match self.load_day(sym, day, false) {
            Ok(v) => v.map(|d| Arc::new(d.ticks)),
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
        let tau = mul_div(lp.abs(), cfg.lv.tau_pm, 100_000) as i64;
        let band = lp - tau;
        let lo = (lv.first_bar as usize * cfg.lv.tf_min).max(t.saturating_sub(cfg.fr_h_min));
        let touch = |m: usize| sym.o[m] != 0 && (if s > 0 { sym.h[m] } else { -sym.l[m] }) >= band;
        if (lo..t).any(touch) {
            return true;
        }
    }
    if cfg.fr_share > 0 {
        let lim = mul_div(lp.abs(), cfg.fr_b_pm, 100_000) as i64;
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
                let zone_lo = lp - mul_div(lp.abs(), dmax + 1, SCALE) as i64 - 1;
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
                let back = lp - mul_div(lp.abs(), cfg.f_pm, 100_000) as i64;
                if s * sym.c[t] < back {
                    forbidden = true;
                }
            }
            // §13.11 (`pk`): прокол цены версии больше чем на τ убивает уровень навсегда, со следующей минуты
            if cfg.lv.pk && !pk_dead && hi > lp + mul_div(lp.abs(), cfg.lv.tau_pm, 100_000) as i64 {
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
        let dist = mul_div(lp - p, SCALE, lp.abs()) as i64;
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
        let dist = mul_div(lp - p, SCALE, lp.abs()) as i64;
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
    /// v22 (`tl`): выставленный лимитный приказ трейла — (мс срабатывания, настоящая цена ×1e8); `None` — приказа не было (`tl = 0` или трейл не срабатывал).
    pub tl: Option<(i64, i64)>,
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
    /// §13.31 (`cw`): диапазон (минимум low, максимум high; длинная рама) закрытых минут `t−W … t−1` до сигнальной; `None` — не включено или данных меньше половины окна.
    pub rng: Option<(i64, i64)>,
}

/// §13.31: диапазон проторговки — минимум low и максимум high (длинная рама, `s` — сторона) закрытых минут `t−w … t−1` с данными; минуты без данных пропускаются;
/// диапазона нет (`None`), если минут с данными меньше `⌈w/2⌉` или `w = 0`. Причинно: сигнальная минута `t` и позже не читаются.
pub fn cons_range(sym: &Sym, s: i64, t: usize, w: usize) -> Option<(i64, i64)> {
    if w == 0 {
        return None;
    }
    let (mut lo, mut hi, mut n) = (i64::MAX, i64::MIN, 0usize);
    for m in t.saturating_sub(w)..t {
        if sym.o[m] == 0 {
            continue;
        }
        let (h, l) = if s > 0 {
            (sym.h[m], sym.l[m])
        } else {
            (-sym.l[m], -sym.h[m])
        };
        hi = hi.max(h);
        lo = lo.min(l);
        n += 1;
    }
    (n > 0 && 2 * n >= w).then_some((lo, hi))
}

/// Контекст уровня сигнала для выходов от уровня (§13.19, §13.31).
pub fn level_ctx(sym: &Sym, side: Side, sg: &TSig, ex: &ExitCfg) -> LevelCtx {
    LevelCtx {
        lp: sg.lp,
        far: sg.far,
        rng: cons_range(sym, side.s(), sg.t as usize, ex.cs_w as usize),
    }
}

const NONE_MS: i64 = i64::MIN;

/// §13.21 (`sn`): NATR-стоп лота. Расстояние `d = clamp(k0·NATR·g, 0,5 %, S_жёсткий)` от цены входа; NATR — закрытая минутка `t−1`
/// (`exit_natr` при `ns = 0`), заморожен на входе. Уровень стопа пересчитывается на каждой сделке (`g` зависит от времени и числа тейков `j`).
struct NatrStop {
    k0_m: i64,
    sg: u8,
    sb_m: i64,
    sr_m: i64,
    sd_m: i64,
    /// `|e|·NATR` (e8): расстояние в NATR → цена = `unit·x/(SCALE·1000)` при `x` в тысячных NATR.
    unit: i128,
    /// Зажим дистанции: 0,5 % и жёсткий стоп S от цены входа (цена ×1e8).
    lo: i64,
    hi: i64,
    entry_ms: i64,
}

/// Доля NATR-стопа в 0,5 % цены входа (нижний зажим), тысячные процента.
const NATR_STOP_MIN_PM: i64 = 500;
const HOUR_MS: i128 = 3_600_000;

impl NatrStop {
    /// Расстояние стопа от цены входа (цена ×1e8) на сделке в `ms`, когда исполнено `j` тейков.
    fn dist(&self, ms: i64, j: usize) -> i64 {
        let nat = self.unit * i128::from(self.k0_m) / (i128::from(SCALE) * 1000);
        let d = match self.sg {
            1 => {
                let dt = i128::from((ms - self.entry_ms).max(0));
                nat * (1000 * HOUR_MS + i128::from(self.sb_m) * dt) / (1000 * HOUR_MS)
            }
            2 => {
                let g = (self.sr_m as f64 / 1000.0).powi(j.min(i32::MAX as usize) as i32);
                (nat as f64 * g) as i128
            }
            3 => nat * (1000 + i128::from(self.sd_m) * j as i128) / 1000,
            _ => nat,
        };
        // верхняя граница главнее нижней: при S < 0,5 % жёсткий стоп и так ближе
        d.max(i128::from(self.lo)).min(i128::from(self.hi)) as i64
    }
}

/// §13.21 (`tj`): пересчёт оставшихся ступеней лестницы тейков после исполнения тейков.
struct StepLadder {
    delta_m: i64,
    /// Исходные множители ступеней, тысячные NATR (`tp_mult_m`).
    mults: Vec<i64>,
    unit: i128,
}

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
    /// §13.31 (`nx`): до касания цена ≤ этого порога (рама) — выход «сценарий отменён»; `i64::MIN` — выкл.
    nx_lvl: i64,
    /// TK-649 (`tu`): срок касания — `вход + T` (мс); `i64::MAX` — выкл.
    tu_due: i64,
    /// §13.21: NATR-стоп (`sn`) и шаг лестницы тейков (`tj`); `None` — выкл.
    natr_stop: Option<NatrStop>,
    step: Option<StepLadder>,
    /// v22 (`tl`): лимитный приказ трейла стоит — тейки сняты, трейл заморожен, остаются рыночные стопы и выходы от уровня.
    lim: bool,
}

impl Pos {
    #[allow(clippy::too_many_arguments)]
    fn new(
        s: i64,
        e: i64,
        p0: i64,
        entry_ms: i64,
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
            // §13.31: стоп за проторговкой (`cw`) — если ширина диапазона в допуске `[cl, ch]` NATR; иначе запасной резиновый стоп от уровня (`sl`)
            let mut cons_ok = false;
            if ex.cs_w > 0 {
                if let Some((rlo, rhi)) = l.rng {
                    let w = rhi - rlo;
                    if w >= nat(ex.cs_lo_m) && w <= nat(ex.cs_hi_m) {
                        stop_base = stop_base.max((rlo - nat(ex.cs_m_m)).min(e - 1));
                        cons_ok = true;
                    }
                }
            }
            if !cons_ok && ex.sl_m > 0 {
                stop_base = stop_base.max((l.lp - nat(ex.sl_m)).min(e - 1));
            }
            if ex.be_mode == 3 {
                let kb = if ex.ls_m > 0 { ex.ls_m } else { 500 };
                be_lvl = Some(e.min(l.far) - nat(kb));
            }
        }
        let rules =
            lc.is_some() && (ex.tx_min > 0 || ex.rt_f_pm > 0 || ex.nx_m > 0 || ex.tu_min > 0);
        let lp = lc.map_or(0, |l| l.lp);
        let dist = |x_pm: i64| (i128::from(lp.abs()) * i128::from(x_pm) / 100_000) as i64;
        let unit = i128::from(ea) * i128::from(natr_e8);
        let natr_stop = (ex.sn_m > 0).then(|| NatrStop {
            k0_m: ex.sn_m,
            sg: ex.sg,
            sb_m: ex.sb_m,
            sr_m: ex.sr_m,
            sd_m: ex.sd_m,
            unit,
            lo: frac(ea, NATR_STOP_MIN_PM, 100_000),
            hi: frac(ea, ex.s_pm, 100_000),
            entry_ms,
        });
        let step = (ex.tj_m > 0).then(|| StepLadder {
            delta_m: ex.tj_m,
            mults: (0..n).map(|i| tp_mult_m(ex, i, n)).collect(),
            unit,
        });
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
            nx_lvl: if ex.nx_m > 0 {
                (lp - nat(ex.nx_m)).min(e - 1)
            } else {
                i64::MIN
            },
            tu_due: if ex.tu_min > 0 {
                entry_ms + ex.tu_min * 60_000
            } else {
                i64::MAX
            },
            natr_stop,
            step,
            lim: false,
        }
    }

    /// Тейки-лимитки, пройденные сделкой `p` строго насквозь.
    fn fill_takes(&mut self, ms: i64, p: i64) {
        let filled0 = self.next_tp;
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
        // §13.21 (`tj`): лестница пересчитана после этой сделки — действует со следующей (внутри сделки ступени идут по прежним ценам)
        if self.next_tp > filled0 {
            if let Some(st) = &self.step {
                let f = 1000 + i128::from(st.delta_m) * self.next_tp as i128;
                let rest = self.tps[self.next_tp..]
                    .iter_mut()
                    .zip(&st.mults[self.next_tp..]);
                for (tp, &m) in rest {
                    *tp = self.e
                        + (st.unit * i128::from(m) * f / (i128::from(SCALE) * 1_000_000)) as i64;
                }
            }
        }
    }

    /// Сделка с ценой `p` (рама) в момент `ms`. `Some(вид)` — стоп сработал на этой сделке (исполнение по `p`).
    fn on_trade(&mut self, ms: i64, p: i64) -> Option<ExitKind> {
        let trail_lvl = if self.armed && !self.lim {
            self.trail
        } else {
            i64::MIN
        };
        // §13.21: NATR-стоп пересчитывается на каждой сделке; «самый плотный из активных» — уровень, ближайший к цене (в рамe — наибольший)
        let natr_lvl = self
            .natr_stop
            .as_ref()
            .map_or(i64::MIN, |n| self.e - n.dist(ms, self.next_tp));
        let sp = self.stop_base.max(trail_lvl).max(natr_lvl);
        if p <= sp {
            return Some(if trail_lvl > self.stop_base.max(natr_lvl) {
                ExitKind::Trail
            } else if natr_lvl > self.stop_base {
                ExitKind::HardStop
            } else if self.be_on {
                ExitKind::BreakEven
            } else {
                ExitKind::HardStop
            });
        }
        if self.lim {
            return if self.rules {
                self.level_exit(ms, p)
            } else {
                None
            };
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
            // §13.31 (`nx`): до касания цена ушла от уровня против позиции на X·NATR — сценарий отменён
            if p <= self.nx_lvl {
                return Some(ExitKind::NoTouch);
            }
            // TK-649 (`tu`): уровень не тронут за T минут от входа
            return (ms >= self.tu_due).then_some(ExitKind::TimeNoTouch);
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
    sim_fill_lc_e(
        next,
        entry_ms,
        p0,
        side,
        natr_e8,
        ex,
        lc,
        notional,
        ex.slip_bps,
    )
}

/// `sim_fill_lc` с проскальзыванием входа `entry_slip_bps` (§13.28: лимитный вход — 0).
#[allow(clippy::too_many_arguments)]
pub fn sim_fill_lc_e(
    next: &mut dyn FnMut() -> Option<(i64, i64, bool)>,
    entry_ms: i64,
    p0: i64,
    side: Side,
    natr_e8: i64,
    ex: &ExitCfg,
    lc: Option<LevelCtx>,
    notional: i64,
    entry_slip_bps: i64,
) -> TickOut {
    let mut after = || -> Option<(i64, i64, bool)> {
        loop {
            let t = next()?;
            if t.0 > entry_ms {
                return Some(t);
            }
        }
    };
    sim_tail_e(
        &mut after,
        entry_ms,
        p0,
        side,
        natr_e8,
        ex,
        lc,
        notional,
        entry_slip_bps,
    )
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
    sim_tail_e(
        next,
        entry_ms,
        p0,
        side,
        natr_e8,
        ex,
        lc,
        notional,
        ex.slip_bps,
    )
}

/// `sim_tail` с проскальзыванием входа `entry_slip_bps` (§13.28: у лимитного входа 0; проскальзывание выходов — `ex.slip_bps`).
#[allow(clippy::too_many_arguments)]
fn sim_tail_e(
    next: &mut dyn FnMut() -> Option<(i64, i64, bool)>,
    entry_ms: i64,
    p0: i64,
    side: Side,
    natr_e8: i64,
    ex: &ExitCfg,
    lc: Option<LevelCtx>,
    notional: i64,
    entry_slip_bps: i64,
) -> TickOut {
    let s = side.s();
    let e = p0 + frac(p0.abs(), entry_slip_bps, 10_000);
    let mut pos = Pos::new(s, e, p0, entry_ms, natr_e8, ex, lc, notional);
    let mut cur_day = (entry_ms / DAY_MS) as usize;
    let mut marks: Vec<(usize, i64)> = Vec::new();
    let mut last = (entry_ms, p0);
    let mut exit: Option<(i64, i64, ExitKind)> = None;
    let mut pending: Option<(i64, ExitKind)> = None;
    // v22 (`tl`): стоящий приказ трейла — (цена в рамe, момент, с которого он живёт, цена последней сделки до его прихода); `tl_info` — (мс срабатывания,
    // настоящая цена) для вывода
    let mut lim: Option<(i64, i64, i64)> = None;
    let mut tl_info: Option<(i64, i64)> = None;
    let mut maker_exit = false;
    while let Some((ms, px, _)) = next() {
        let d = (ms / DAY_MS) as usize;
        while cur_day < d {
            marks.push((cur_day, s * last.1));
            cur_day += 1;
        }
        let p = s * px;
        last = (ms, p);
        if let Some((lpx, live, arrival)) = lim {
            if ms < live {
                // приказ в пути (задержка выхода): как окно задержки рыночного выхода — сделки только исполняют тейки
                pos.fill_takes(ms, p);
                lim = Some((lpx, live, p));
                continue;
            }
            // `tl = 2`: к приходу приказа цена уже строго за ним — приказ маркетабелен и исполняется как рыночный выход v21 (тейкер по сделке, с проскальзыванием)
            if ex.tl == 2 && arrival > lpx {
                exit = Some((ms, p, ExitKind::Trail));
                break;
            }
            lim = Some((lpx, live, i64::MIN));
            pos.lim = true;
            if let Some((due, kind)) = pending {
                if ms >= due {
                    exit = Some((ms, p, kind));
                    break;
                }
            }
            if p >= lpx {
                exit = Some((ms, lpx, ExitKind::Trail));
                maker_exit = true;
                break;
            }
            if pending.is_none() {
                if let Some(kind) = pos.on_trade(ms, p) {
                    if ex.lat_exit_ms > 0 {
                        pending = Some((ms + ex.lat_exit_ms, kind));
                    } else {
                        exit = Some((ms, p, kind));
                        break;
                    }
                }
            }
            continue;
        }
        if let Some((due, kind)) = pending {
            if ms >= due {
                exit = Some((ms, p, kind));
                break;
            }
            pos.fill_takes(ms, p);
            continue;
        }
        if let Some(kind) = pos.on_trade(ms, p) {
            if ex.tl > 0 && kind == ExitKind::Trail {
                lim = Some((pos.trail, ms + ex.lat_exit_ms, p));
                tl_info = Some((ms, s * pos.trail));
                continue;
            }
            if ex.lat_exit_ms > 0 {
                pending = Some((ms + ex.lat_exit_ms, kind));
                continue;
            }
            exit = Some((ms, p, kind));
            break;
        }
    }
    let (xm, xp, kind) = exit.unwrap_or((last.0, last.1, ExitKind::Forced));
    // лимитный выход трейла — по цене приказа, мейкер, без проскальзывания; остальные выходы — тейкер с проскальзыванием
    let px_frame = if maker_exit {
        xp
    } else {
        xp - frac(xp.abs(), ex.slip_bps, 10_000)
    };
    let mut legs = pos.legs;
    legs.push(TLeg {
        ms: xm,
        px: s * px_frame,
        u: pos.rem,
        maker: maker_exit,
    });
    TickOut {
        entry_ms,
        entry_px: s * e,
        legs,
        exit_ms: xm,
        kind,
        takes: pos.next_tp as u32,
        marks,
        tl: tl_info,
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
    let lc = level_ctx(sym, side, sig, ex);
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
    /// §13.28 (`mk`): заявка мейкера не заполнилась до конца минуты.
    Unfilled,
    /// §13.28 (`mk = 1`): до заполнения сделка дошла до уровня — заявка снята.
    Touched,
    /// §13.28 (`mk`): тик монеты нигде не определён (нет окна из ≥ 100 сделок).
    NoTick,
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

// ───────────────────────────── §13.28: вход лимиткой (мейкер), опция `mk` ─────────────────────────────

/// §13.28: минимум сделок окна для определения тика.
pub const MK_MIN_TRADES: usize = 100;

/// §13.28: окно тика — столько календарных суток до суток `T` плюс сутки `T` до `T`.
pub const MK_TICK_DAYS: usize = 7;

fn gcd(mut a: i64, mut b: i64) -> i64 {
    while b != 0 {
        (a, b) = (b, a % b);
    }
    a
}

/// §13.28: цены набора сделок для тика — НОД разностей к первой цене, первая цена, число сделок (`n = 0` — набор пуст).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct PxGcd {
    pub g: i64,
    pub p0: i64,
    pub n: usize,
}

impl PxGcd {
    pub fn of(ticks: &[Tick]) -> PxGcd {
        let Some(first) = ticks.first() else {
            return PxGcd::default();
        };
        let mut g = 0;
        for t in ticks {
            if g != 1 {
                g = gcd(g, (t.px - first.px).abs());
            }
        }
        PxGcd {
            g,
            p0: first.px,
            n: ticks.len(),
        }
    }

    /// Объединение двух наборов: НОД всех разностей цен обоих наборов.
    pub fn merge(self, o: PxGcd) -> PxGcd {
        if self.n == 0 {
            return o;
        }
        if o.n == 0 {
            return self;
        }
        PxGcd {
            g: gcd(gcd(self.g, o.g), (o.p0 - self.p0).abs()),
            p0: self.p0,
            n: self.n + o.n,
        }
    }

    fn tick(self) -> Option<i64> {
        (self.n >= MK_MIN_TRADES && self.g > 0).then_some(self.g)
    }
}

/// §13.28: тик монеты на момент `t` — НОД разностей цен сделок строго до `t` за предыдущие `MK_TICK_DAYS` суток и сутки `t` до `t`;
/// окно меньше `MK_MIN_TRADES` сделок — последнее более раннее окно (то же правило, закончившееся сутками раньше), в котором тик определён.
/// Сделок позже `t` не читает. `cache` — НОД полных суток монеты (ключ — номер монеты и сутки).
pub fn tick_at(
    src: &dyn TradeSrc,
    name: &str,
    sym_i: u16,
    t: i64,
    cache: &mut HashMap<(u16, usize), PxGcd>,
) -> Option<i64> {
    let day = t.div_euclid(DAY_MS) as usize;
    let mut full = |d: usize| -> PxGcd {
        *cache
            .entry((sym_i, d))
            .or_insert_with(|| src.day(name, d).map_or(PxGcd::default(), |v| PxGcd::of(&v)))
    };
    let part = src.day(name, day).map_or(PxGcd::default(), |v| {
        let off = t - day as i64 * DAY_MS;
        PxGcd::of(&v[..v.partition_point(|x| i64::from(x.off) < off)])
    });
    let mut w = part;
    for d in day.saturating_sub(MK_TICK_DAYS)..day {
        w = full(d).merge(w);
    }
    if let Some(g) = w.tick() {
        return Some(g);
    }
    for e in (0..day).rev() {
        let mut w = PxGcd::default();
        for d in e.saturating_sub(MK_TICK_DAYS)..=e {
            w = w.merge(full(d));
        }
        if let Some(g) = w.tick() {
            return Some(g);
        }
    }
    None
}

/// Конец минуты, содержащей `t` (мс).
fn minute_end(t: i64) -> i64 {
    (t.div_euclid(MIN_MS) + 1) * MIN_MS
}

/// §13.28: заявка мейкера на вход, выставляемая в момент `t` (мс от 01.09) при цене уровня `lp` (рама); живёт до `until` (мс, не включая).
/// Допуск по ленте — как у тейкера (`entry_at`: `Stale` / `Beyond`). Цена-заменитель `p` — последняя сделка стороны ≤ `t`; тик — `tick_of(t)`.
/// `mode = 1` (mk1): лимит `p − тик` (рама); снимается сделкой `≥ lp` (`Touched`); заполняет первая сделка после `t` строго ниже лимита.
/// `mode = 2` (mk2): лимит — цена уровня, округлённая вниз на сетку заменителя (`p + ⌊(lp − p)/тик⌋·тик`); заявка рабочая после пробоя —
/// первой сделки после `t` с ценой `≥ lp` плюс `lat`; заполняет первая сделка строго после этого момента строго ниже лимита.
/// Заполнение — по цене лимита, время — время сквозной сделки. `from` — (сутки, индекс) первой сделки курсора после сигнальной.
#[allow(clippy::too_many_arguments)]
pub fn mk_order(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    from: (usize, usize),
    day: usize,
    t: i64,
    lp: i64,
    ex: &ExitCfg,
    mode: u8,
    until: i64,
    tick_of: &mut dyn FnMut(i64) -> Option<i64>,
) -> EntryDec {
    let p_sur = match entry_at(src, sym, side, day, t, lp, ex) {
        EntryDec::Fill { p0, .. } => p0,
        other => return other,
    };
    let Some(tick) = tick_of(t) else {
        return EntryDec::Reject(EntryRej::NoTick);
    };
    let s = side.s();
    let limit = if mode == 1 {
        p_sur - tick
    } else {
        p_sur + (lp - p_sur).div_euclid(tick) * tick
    };
    let mut live_from = if mode == 1 { t } else { NONE_MS };
    let mut cur = Cursor::new(src, &sym.name, from.0, from.1);
    while let Some((ms, px, _)) = cur.next_tick() {
        if ms <= t {
            continue;
        }
        if ms >= until {
            break;
        }
        let p = s * px;
        if mode == 1 {
            if p >= lp {
                return EntryDec::Reject(EntryRej::Touched);
            }
        } else if live_from == NONE_MS {
            if p >= lp {
                live_from = ms + ex.lat_ms;
            }
            continue;
        }
        if ms > live_from && p < limit {
            return EntryDec::Fill { ms, p0: limit };
        }
    }
    EntryDec::Reject(EntryRej::Unfilled)
}

/// §13.28: решение о входе лимиткой для сигнала (лот 1): заявка в `сигнал + lat`, жизнь — до конца минуты этого момента.
pub fn mk_decision(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    sig: &TSig,
    ex: &ExitCfg,
    mode: u8,
    tick_of: &mut dyn FnMut(i64) -> Option<i64>,
) -> EntryDec {
    let t = sig.ms + ex.lat_ms;
    mk_order(
        src,
        sym,
        side,
        (usize::from(sig.day), sig.idx as usize + 1),
        usize::from(sig.day),
        t,
        sig.lp,
        ex,
        mode,
        minute_end(t),
        tick_of,
    )
}

/// Второй лот лимиткой (§13.28): как `lot2_decision`, но заявка подхода (`t2 = ms + lat`) — мейкер mk1, живёт до конца минуты `t2` и не дольше
/// выхода лота 1; не заполнилась / уровень сорван — `Cancel`; у mk2 лот 2 заполняется тем же событием и по той же цене, что лот 1.
#[allow(clippy::too_many_arguments)]
pub fn lot2_decision_mk(
    src: &dyn TradeSrc,
    sym: &Sym,
    side: Side,
    sig: &TSig,
    ex: &ExitCfg,
    lot1: (i64, i64),
    lot1_exit_ms: i64,
    mode: u8,
    tick_of: &mut dyn FnMut(i64) -> Option<i64>,
) -> Lot2Dec {
    let (ms1, p1) = lot1;
    let d = (i128::from(sig.lp.abs()) * i128::from(ex.e2_pm) / 100_000) as i64;
    if mode == 2 || p1 >= sig.lp - d {
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
            let until = minute_end(t2).min(lot1_exit_ms);
            let dec = mk_order(
                src,
                sym,
                side,
                (usize::from(sig.day), sig.idx as usize + 1),
                (t2 / DAY_MS) as usize,
                t2,
                sig.lp,
                ex,
                1,
                until,
                tick_of,
            );
            return match dec {
                EntryDec::Fill { ms, p0 } => Lot2Dec::Fill {
                    ms,
                    p0,
                    immediate: false,
                },
                EntryDec::Reject(EntryRej::Unfilled | EntryRej::Touched) => Lot2Dec::Cancel,
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
    settle_tick_mk(sym, side, tr, notional, true)
}

/// `settle_tick_n` с видом комиссии входа: `entry_taker = false` — лимитный вход (§13.28, мейкер).
pub fn settle_tick_mk(
    sym: &Sym,
    side: Side,
    tr: &TickOut,
    notional: i64,
    entry_taker: bool,
) -> Settled {
    let s = side.s() as f64;
    let e = tr.entry_px as f64;
    let n0 = notional as f64 / 1e6;
    let mut flows: Vec<(i64, f64)> = Vec::new();
    let entry_fee = n0 * leg_fee_bps(entry_taker) / 1e4;
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
    /// v22 (`tl`): приказ трейла лота 1 — (мс срабатывания, настоящая цена ×1e8); `None` — приказа не было.
    pub tl: Option<(i64, i64)>,
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
    /// §13.28 (`mk > 0`): заявки лота 1 — заполнено, не заполнено, снято уровнем (число отказов `notick` — в `entry_rej`).
    pub mk: [u64; 3],
}

impl TickResult {
    /// Пустой результат (режим `P42_SIGSX_ONLY`, §13.18: только сигналы и дампы, без портфеля и без сделок).
    pub fn empty() -> TickResult {
        TickResult {
            trades: Vec::new(),
            entry_rej: Vec::new(),
            daily: [0.0; COUNT_DAYS],
            coinday: BTreeMap::new(),
            rej: [0; 6],
            lot2: [0; 4],
            mk: [0; 3],
        }
    }
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
    run_tick_mk(syms, sigs, side, ex, pc, src, 0)
}

/// `run_tick` с входом лимиткой (§13.28): `mk = 0` — прежний путь тейкера; 1 — mk1; 2 — mk2 (требует `ew > 0`).
pub fn run_tick_mk(
    syms: &[Sym],
    sigs: &[TSig],
    side: Side,
    ex: &ExitCfg,
    pc: &PortCfg,
    src: &dyn TradeSrc,
    mk: u8,
) -> TickResult {
    let ns = syms.len();
    let mut res = TickResult {
        trades: Vec::new(),
        entry_rej: Vec::new(),
        daily: [0.0; COUNT_DAYS],
        coinday: BTreeMap::new(),
        rej: [0; 6],
        lot2: [0; 4],
        mk: [0; 3],
    };
    let maker = mk > 0;
    let entry_slip = if maker { 0 } else { ex.slip_bps };
    let mut tick_cache: HashMap<(u16, usize), PxGcd> = HashMap::new();
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
        // §13.21 (`busy = 0`): монета с открытой позицией принимает сигналы других уровней
        if pc.busy && busy[si] {
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
        let lc = level_ctx(sym, side, sg, ex);
        let split = ex.e2_pm > 0;
        let lot_n = if split { NOTIONAL_U / 2 } else { NOTIONAL_U };
        let tr = if natr > 0 {
            dec = if maker {
                let mut tick_of = |t: i64| tick_at(src, &sym.name, sg.sym, t, &mut tick_cache);
                mk_decision(src, sym, side, sg, ex, mk, &mut tick_of)
            } else {
                entry_decision(src, sym, side, sg, ex)
            };
            match dec {
                EntryDec::Next => simulate_tick(src, sym, side, sg, natr, ex),
                EntryDec::Fill { ms, p0 } => {
                    let mut cur =
                        Cursor::new(src, &sym.name, usize::from(sg.day), sg.idx as usize + 1);
                    let t1 = sim_fill_lc_e(
                        &mut || cur.next_tick(),
                        ms,
                        p0,
                        side,
                        natr,
                        ex,
                        Some(lc),
                        lot_n,
                        entry_slip,
                    );
                    if maker {
                        res.mk[0] += 1;
                    }
                    if split {
                        let l2 = if maker {
                            let mut tick_of =
                                |t: i64| tick_at(src, &sym.name, sg.sym, t, &mut tick_cache);
                            lot2_decision_mk(
                                src,
                                sym,
                                side,
                                sg,
                                ex,
                                (ms, p0),
                                t1.exit_ms,
                                mk,
                                &mut tick_of,
                            )
                        } else {
                            lot2_decision(src, sym, side, sg, ex, (ms, p0), t1.exit_ms)
                        };
                        match l2 {
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
                                tr2 = Some(sim_fill_lc_e(
                                    &mut || cur2.next_tick(),
                                    m2,
                                    q0,
                                    side,
                                    natr,
                                    ex,
                                    Some(lc),
                                    lot_n,
                                    entry_slip,
                                ));
                            }
                            Lot2Dec::Cancel => res.lot2[2] += 1,
                            Lot2Dec::Reject => res.lot2[3] += 1,
                        }
                    }
                    Some(t1)
                }
                EntryDec::Reject(why) => {
                    match why {
                        EntryRej::Unfilled => res.mk[1] += 1,
                        EntryRej::Touched => res.mk[2] += 1,
                        _ => {}
                    }
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
        let mut st = settle_tick_mk(sym, side, &tr, lot_n, !maker);
        if let Some(t2) = &tr2 {
            let s2 = settle_tick_mk(sym, side, t2, lot_n, !maker);
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
            tl: tr.tl,
            lot2: tr2,
        });
    }
    res
}
