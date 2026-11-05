//! `alpha pumponset run --dump-triggers` — выгрузка первых срабатываний правил прогона со всеми атрибутами момента и
//! исходами в `triggers-<stage>.csv` (TK-492; схема — docs/findings/pumponset-run-spec-2026-11-05.md, «triggers»).
//!
//! Строка = первое срабатывание (правило из прогона, сторона `sign ≠ 0`) в периоде стадии и вселенной — те же
//! срабатывания, что кладёт в `daycells` `run::sink_trigger` (их источник — тот же `control::c2a_moments_with`).
//! Число строк по (правило, сторона) поэтому не меньше `Σ n` по `daycells` для любого `hi` и равно ему для `hi`, где
//! метка срабатывания определена (метка `NoData` у края данных в `n` не входит, строка в выгрузке остаётся).
//!
//! # Допущения (то, чего ТЗ не называет)
//! 1. **Окно «предыдущие w минут»** — последние `w` минут, кончающиеся минутой `t_f` включительно (close минуты `t_f`
//!    известен в `t_f`): `rv_w` — корень из суммы квадратов `w` минутных ln-доходностей `close_ff` с индексами
//!    `t_f−w+1…t_f`; `rng1440` и `qv1440` — по минутам `t_f−1439…t_f`; `drop360` — максимум по `t_f−359…t_f` (включает
//!    `c[t_f]`, поэтому `≤ 0`). Окно, не лежащее целиком в данных контракта (начало раньше первого `close` для цен,
//!    раньше первой минуты для объёма), даёт `NaN`.
//! 2. **Исходы** считаются от входа `e = t_f + 1` (`c[e]`, как `trade.rs`) по `close_ff`; `gross_h` — по `c[e+h]`, `NaN`
//!    если минуты нет в данных. Путь `mfe/mae` — минуты `e…e+1440` включительно (в `gross_1440` та же последняя минута,
//!    поэтому `mfe ≥ gross_h ≥ mae` для всех `h`); если путь целиком не лежит в данных — `mfe_*`, `mae_*` `NaN` (усечённый
//!    путь занижал бы `mfe` у края данных, как «нет данных» у меток вперёд). Минута экстремума — первая из равных.
//! 3. **Круглые уровни**: цена входа `p = c[e]`; уровень — `m·10^k` с `m` из набора (`d_dec` — {1}, `d_half` — {1, 5},
//!    `d_125` — {1, 2, 5}), `k` целое (в том числе отрицательное); берётся ближайший по `|ln(L/p)|`, при ничьей — нижний.
//!    `pk_d_*` — то же для цены в точке `mfe` (зависит от стороны: у шорта это минимум).
//! 4. **Отметки фандинга** — 00:00/08:00/16:00 UTC: `min_since_fund8 = (минуты UTC от эпохи) mod 480` (в отметку — 0),
//!    `min_to_fund8 = 480 − min_since_fund8` (в отметку — 480: «будущая» отметка строго позже `t_f`).
//! 5. **BTC** — `close_ff` контракта `ref_symbol` из того же каталога кэша, усечённый как контракты стадии; нет файла
//!    или минут — `NaN`. `btc_next360_bps` — контекст исхода (смотрит вперёд), не признак.
//! 6. **`ep_running`** — `TriggerRow::in_episode_running`: эпизод того же знака с `t0 < t_f ≤ t_end` (концы эпизодов
//!    находятся задним числом) — единственный столбец атрибутов момента, не подчинённый правилу «только данные ≤ t_f».
//! 7. **База `b_gross_*`** — среднее `sign_m·ln(c[m+1+H]/c[m+1])·1e4` по моментам C2a этого срабатывания (их выбирает
//!    `control::c2a_moments_with` — те же моменты, что в `bn/bhit`), где `sign_m` — знак `r_w` окна правила в моменте
//!    `m` (как метка момента); моменты без цен входа/выхода в среднее не входят, нет ни одного — `NaN`. Моменты лежат
//!    в том же месяце и дециле σ, но могут быть и позже `t_f`: это база исхода, а не признак момента.
//!
//! Детерминизм: поток накапливает срабатывания контракта, сортирует по `(t_f, fam, wi, ti, sign)` и пишет текст
//! в память; `write` склеивает контракты по алфавиту имён — байты не зависят от числа потоков.

#![allow(
    // индексы ограничены проверенными длинами рядов контракта; бюджет линтов (TK-211) не растим
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_precision_loss,
    clippy::cast_sign_loss
)]

use super::cache::{Contract, SLOT_MIN};
use super::cli::load_contract;
use super::control::{sign_of, slot_t, yyyymm, TriggerRow};
use super::play::Play;
use super::run::{clip_contract, day_of_min};
use super::types::{Cube, Rule};
use crate::statarb::panel::{GRID0_MS, MINUTE_MS};
use anyhow::{Context, Result};
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::Path;

/// Единицы измерения (не параметры): перевод доли в bps, минуты в сутках, период отметок фандинга, минута GRID0.
const BPS: f64 = 1.0e4;
const DAY_MIN: i64 = 24 * 60;
const FUND_MIN: i64 = 8 * 60;
const MIN0: i64 = GRID0_MS / MINUTE_MS;
/// Горизонты исходов `gross_h`, мин (из ТЗ), и горизонты базы `b_gross_*`.
const HS: [usize; 9] = [5, 15, 30, 60, 120, 240, 360, 720, 1440];
const BASE_HS: [usize; 3] = [60, 360, 1440];
/// Окна атрибутов, мин (из ТЗ) и длина пути `mfe/mae` после входа.
const W_RV_SHORT: usize = 60;
const W_DAY: usize = 1440;
const W_DROP: usize = 360;
const W_RET_SHORT: i64 = 60;
const W_RET_LONG: i64 = 360;
const W_BTC_PREV: i64 = 60;
const W_BTC_NEXT: i64 = 360;
const PATH_MIN: usize = 1440;

/// Наборы мантисс круглых уровней: каждый набор включает 1 и верхнюю границу декады 10.
pub const DEC: [f64; 2] = [1.0, 10.0];
pub const HALF: [f64; 3] = [1.0, 5.0, 10.0];
pub const M125: [f64; 4] = [1.0, 2.0, 5.0, 10.0];

/// Заголовок `triggers-<stage>.csv` (порядок столбцов = порядок записи в `Collector::write_row`).
pub const HEADER: &str = "sym,fam,wi,ti,sign,t_f,day,month,\
hour_utc,minute_of_hour,dow,min_since_day,min_to_fund8,min_since_fund8,\
d_dec,d_half,d_125,pk_d_dec,pk_d_half,pk_d_125,\
rv60_bps,rv1440_bps,rng1440_bps,sigma_h_bps,\
ret_prev60_bps,ret_prev360_bps,drop360_bps,ep_running,age_days,qv1440_usd,btc_prev60_bps,btc_next360_bps,\
gross_5,gross_15,gross_30,gross_60,gross_120,gross_240,gross_360,gross_720,gross_1440,\
mfe_bps,mfe_min,mae_bps,mae_min,mfe_price,\
b_gross_60,b_gross_360,b_gross_1440";

/// Столбцы выборки «монеты в игре» (`play.rs`): добавляются в конец строки, только если фильтр включён.
/// `natr_pct` — NATR в `t_f` (`NaN`, если `--hv-natr-pct` не задан); `ok_long`/`ok_short` — откуп не мешает лонгу/шорту
/// (1, если `--no-reversal` не задан); `first_phase` — минута перехода в игру (1, если нет `--hv-natr-pct`).
pub const PLAY_COLS: &str = "natr_pct,ok_long,ok_short,first_phase";

// ---------- время ----------

/// Календарные столбцы минуты `t_f` (от `GRID0_MS`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TimeCols {
    /// Сутки UTC от 1970-01-01 и календарный месяц `yyyymm`.
    pub day: i64,
    pub month: i32,
    pub hour: i64,
    pub minute: i64,
    /// 0 = понедельник … 6 = воскресенье.
    pub dow: i64,
    pub since_day: i64,
    pub to_fund: i64,
    pub since_fund: i64,
}

pub fn time_cols(t_f: i64) -> TimeCols {
    let u = MIN0 + t_f;
    let day = day_of_min(t_f);
    let md = u.rem_euclid(DAY_MIN);
    let since_fund = u.rem_euclid(FUND_MIN);
    TimeCols {
        day,
        month: yyyymm(t_f),
        hour: md / 60,
        minute: md % 60,
        // 1970-01-01 — четверг (3 при отсчёте от понедельника)
        dow: (day + 3).rem_euclid(7),
        since_day: md,
        to_fund: FUND_MIN - since_fund,
        since_fund,
    }
}

// ---------- круглые числа ----------

/// `10^n` умножением: точно до `n = 22`, одинаково на всех платформах.
fn pow10(n: u32) -> f64 {
    let mut v = 1.0;
    for _ in 0..n {
        v *= 10.0;
    }
    v
}

/// Уровень `m·10^k`: для `k < 0` делением на точную степень (`1/1000` — ближайшее к 0,001 число, а не 0,001·(1+ε)).
fn level(m: f64, k: i32) -> f64 {
    if k >= 0 {
        m * pow10(k as u32)
    } else {
        m / pow10(k.unsigned_abs())
    }
}

/// Расстояние со знаком, bps, от цены `p` до ближайшего по `|ln(L/p)|` уровня `m·10^k`, `m` из `mants` (по возрастанию,
/// первая 1, последняя 10); положительное — уровень выше цены. Ничья — нижний уровень. `NaN` для неположительной или
/// неконечной цены.
pub fn nearest_level_bps(p: f64, mants: &[f64]) -> f64 {
    if !(p.is_finite() && p > 0.0) {
        return f64::NAN;
    }
    let mut k = p.log10().floor() as i32;
    // log10 может ошибиться на границе декады: приводим к 10^k ≤ p < 10^(k+1)
    for _ in 0..4 {
        if level(1.0, k) > p {
            k -= 1;
        } else if level(1.0, k + 1) <= p {
            k += 1;
        }
    }
    let (mut lo, mut hi) = (level(mants[0], k), level(10.0, k));
    for &m in mants {
        let l = level(m, k);
        if l <= p {
            lo = l;
        } else {
            hi = l;
            break;
        }
    }
    if !(lo > 0.0 && lo <= p && hi > p && hi.is_finite()) {
        return f64::NAN;
    }
    let (d_lo, d_hi) = ((lo / p).ln(), (hi / p).ln());
    (if -d_lo <= d_hi { d_lo } else { d_hi }) * BPS
}

// ---------- ряды ----------

/// Цена `close_ff` минуты `m` или `NaN` (нет минуты, до первого close, неположительная).
fn price_at(cf: &[f64], first_min: i64, m: i64) -> f64 {
    usize::try_from(m - first_min)
        .ok()
        .and_then(|k| cf.get(k).copied())
        .filter(|x| x.is_finite() && *x > 0.0)
        .unwrap_or(f64::NAN)
}

/// `ln(a/b)·1e4` или `NaN`, если любая из цен не определена.
fn ln_bps(a: f64, b: f64) -> f64 {
    if a.is_finite() && a > 0.0 && b.is_finite() && b > 0.0 {
        (a / b).ln() * BPS
    } else {
        f64::NAN
    }
}

/// Знаковая доходность `side·ln(c[e+h]/c[e])·1e4` от минуты входа `e_min`.
fn gross(cf: &[f64], first_min: i64, e_min: i64, h: usize, side: f64) -> f64 {
    side * ln_bps(
        price_at(cf, first_min, e_min + h as i64),
        price_at(cf, first_min, e_min),
    )
}

/// Эталон для `btc_*`: `close_ff` `ref_symbol`, усечённый так же, как контракты стадии.
#[derive(Debug, Default)]
pub struct Shared {
    first_min: i64,
    cf: Vec<f64>,
}

impl Shared {
    /// Нет файла — пустой эталон (`btc_*` = `NaN`).
    pub fn load(path: &Path, clip: Option<i64>) -> Result<Shared> {
        if !path.exists() {
            return Ok(Shared::default());
        }
        let mut c = load_contract(path)?;
        if let Some(e) = clip {
            clip_contract(&mut c, e);
        }
        Ok(Shared {
            first_min: c.first_min,
            cf: c.close_ff(),
        })
    }

    fn at(&self, m: i64) -> f64 {
        price_at(&self.cf, self.first_min, m)
    }
}

/// Максимум и минимум цены на пути входа с минутами (от входа) первых достижений.
#[derive(Debug, Clone, Copy)]
struct Extremes {
    hi: f64,
    hi_k: usize,
    lo: f64,
    lo_k: usize,
}

/// Один проход по минутам `e…e+PATH_MIN` включительно; `None` — путь не лежит целиком в данных или цена не определена.
fn scan_extremes(cf: &[f64], e: usize) -> Option<Extremes> {
    let w = cf.get(e..=e + PATH_MIN)?;
    let p = w[0];
    if !(p.is_finite() && p > 0.0) {
        return None;
    }
    let mut x = Extremes {
        hi: p,
        hi_k: 0,
        lo: p,
        lo_k: 0,
    };
    for (k, &v) in w.iter().enumerate().skip(1) {
        if !(v.is_finite() && v > 0.0) {
            return None;
        }
        if v > x.hi {
            x.hi = v;
            x.hi_k = k;
        }
        if v < x.lo {
            x.lo = v;
            x.lo_k = k;
        }
    }
    Some(x)
}

// ---------- сбор ----------

/// Срабатывание, отложенное до сортировки и записи.
struct Item {
    t_f: i64,
    rule: Rule,
    sign: i8,
    running: bool,
    base: [f64; 3],
}

/// Атрибуты момента `t_f`, не зависящие от стороны и правила (считаются один раз на `t_f`).
struct Common {
    tc: TimeCols,
    p: f64,
    rv60: f64,
    rv1440: f64,
    rng1440: f64,
    sigma_h: f64,
    ret60: f64,
    ret360: f64,
    drop360: f64,
    age_days: f64,
    qv1440: f64,
    btc_prev60: f64,
    btc_next360: f64,
    ext: Option<Extremes>,
}

/// Накопитель срабатываний одного контракта.
pub struct Collector<'a> {
    sh: &'a Shared,
    sym: &'a str,
    c: &'a Contract,
    cf: &'a [f64],
    cube: &'a Cube,
    items: Vec<Item>,
    /// Маски выборки «в игре»: есть — к строке добавляются `PLAY_COLS`.
    play: Option<&'a Play>,
}

impl<'a> Collector<'a> {
    pub fn new(
        sh: &'a Shared,
        sym: &'a str,
        c: &'a Contract,
        cf: &'a [f64],
        cube: &'a Cube,
    ) -> Collector<'a> {
        Collector {
            sh,
            sym,
            c,
            cf,
            cube,
            items: Vec::new(),
            play: None,
        }
    }

    /// Дописывать столбцы `PLAY_COLS` (`None` — схема прежняя).
    pub fn with_play(mut self, play: Option<&'a Play>) -> Collector<'a> {
        self.play = play;
        self
    }

    /// Принять срабатывание (строка `control::c2a_moments_with`) и слоты куба его моментов C2a; знак 0 — строки нет.
    pub fn add(&mut self, row: &TriggerRow, moments: &[u32]) {
        if row.sign == 0 {
            return;
        }
        let wi = row.rule.wi as usize;
        let mut base = [f64::NAN; 3];
        for (b, &h) in base.iter_mut().zip(&BASE_HS) {
            let (mut sum, mut n) = (0.0, 0u32);
            for &j in moments {
                let j = j as usize;
                let sj = sign_of(self.cube.rat(wi, j));
                if sj == 0 {
                    continue;
                }
                let tj = slot_t(self.cube, j);
                let g = gross(self.cf, self.c.first_min, tj + 1, h, f64::from(sj));
                if g.is_finite() {
                    sum += g;
                    n += 1;
                }
            }
            if n > 0 {
                *b = sum / f64::from(n);
            }
        }
        self.items.push(Item {
            t_f: row.t_f,
            rule: row.rule,
            sign: row.sign,
            running: row.in_episode_running,
            base,
        });
    }

    /// Атрибуты момента `t_f` по данным `≤ t_f` и путь исхода (`r2` — квадраты минутных ln-доходностей `close_ff`).
    fn common(&self, t_f: i64, r2: &[f64]) -> Common {
        let (cf, fm) = (self.cf, self.c.first_min);
        let i = usize::try_from(t_f - fm).ok().filter(|&i| i < cf.len());
        let e = t_f + 1;
        let tc = time_cols(t_f);
        let nan = f64::NAN;
        let (mut rv60, mut rv1440, mut rng1440, mut drop360, mut qv1440) =
            (nan, nan, nan, nan, nan);
        if let Some(i) = i {
            if i >= W_RV_SHORT {
                let s: f64 = r2[i + 1 - W_RV_SHORT..=i].iter().sum();
                rv60 = s.sqrt() * BPS;
            }
            if i >= W_DROP - 1 {
                let mut mx = f64::NEG_INFINITY;
                let mut ok = true;
                for &x in &cf[i + 1 - W_DROP..=i] {
                    ok &= x.is_finite() && x > 0.0;
                    mx = mx.max(x);
                }
                if ok {
                    drop360 = ln_bps(cf[i], mx);
                }
            }
            if i >= W_DAY - 1 {
                let (mut s2, mut qv) = (0.0, 0.0);
                let (mut mx, mut mn) = (f64::NEG_INFINITY, f64::INFINITY);
                let mut ok = true;
                let lo = i + 1 - W_DAY;
                let rows = r2[lo..=i].iter().zip(&self.c.qvol[lo..=i]).zip(&cf[lo..=i]);
                for ((&r, &q), &x) in rows {
                    s2 += r;
                    qv += f64::from(q);
                    ok &= x.is_finite() && x > 0.0;
                    mx = mx.max(x);
                    mn = mn.min(x);
                }
                rv1440 = s2.sqrt() * BPS;
                qv1440 = qv;
                if ok {
                    rng1440 = ln_bps(mx, mn);
                }
            }
        }
        let slot = t_f.div_euclid(SLOT_MIN) - self.cube.slot0;
        let sigma_h = usize::try_from(slot)
            .ok()
            .and_then(|s| self.cube.sigma.get(s).copied())
            .map_or(nan, |s| f64::from(s) * 60f64.sqrt() * BPS);
        let at = |m: i64| price_at(cf, fm, m);
        let ext = usize::try_from(e - fm)
            .ok()
            .and_then(|k| scan_extremes(cf, k));
        Common {
            tc,
            p: at(e),
            rv60,
            rv1440,
            rng1440,
            sigma_h,
            ret60: ln_bps(at(t_f), at(t_f - W_RET_SHORT)),
            ret360: ln_bps(at(t_f), at(t_f - W_RET_LONG)),
            drop360,
            age_days: (t_f - fm) as f64 / DAY_MIN as f64,
            qv1440,
            btc_prev60: ln_bps(self.sh.at(t_f), self.sh.at(t_f - W_BTC_PREV)),
            btc_next360: ln_bps(self.sh.at(e + W_BTC_NEXT), self.sh.at(e)),
            ext,
        }
    }

    /// Текст строк контракта (без заголовка), в порядке `(t_f, fam, wi, ti, sign)`.
    pub fn finish(mut self) -> Vec<u8> {
        if self.items.is_empty() {
            return Vec::new();
        }
        self.items
            .sort_unstable_by_key(|it| (it.t_f, it.rule.fam, it.rule.wi, it.rule.ti, it.sign));
        let cf = self.cf;
        let mut r2 = vec![f64::NAN; cf.len()];
        for (r, w) in r2.iter_mut().skip(1).zip(cf.windows(2)) {
            let ok = w.iter().all(|x| x.is_finite() && *x > 0.0);
            if ok {
                let l = (w[1] / w[0]).ln();
                *r = l * l;
            }
        }
        let mut out = Vec::with_capacity(self.items.len() * 900);
        let mut cur: Option<(i64, Common)> = None;
        for it in &self.items {
            if cur.as_ref().map(|c| c.0) != Some(it.t_f) {
                cur = Some((it.t_f, self.common(it.t_f, &r2)));
            }
            if let Some((_, cm)) = &cur {
                self.write_row(&mut out, it, cm);
            }
        }
        out
    }

    fn write_row(&self, out: &mut Vec<u8>, it: &Item, cm: &Common) {
        let (fm, p) = (self.c.first_min, cm.p);
        let side = f64::from(it.sign);
        let tc = &cm.tc;
        let _ = write!(
            out,
            "{},{},{},{},{},{},{},{},{},{},{},{},{},{}",
            self.sym,
            it.rule.fam,
            it.rule.wi,
            it.rule.ti,
            it.sign,
            it.t_f,
            tc.day,
            tc.month,
            tc.hour,
            tc.minute,
            tc.dow,
            tc.since_day,
            tc.to_fund,
            tc.since_fund
        );
        // путь исхода: у лонга экстремум пользы — максимум цены, у шорта — минимум
        let (mfe, mae) = match &cm.ext {
            Some(x) if it.sign > 0 => (Some((x.hi, x.hi_k)), Some((x.lo, x.lo_k))),
            Some(x) => (Some((x.lo, x.lo_k)), Some((x.hi, x.hi_k))),
            None => (None, None),
        };
        let mfe_price = mfe.map_or(f64::NAN, |m| m.0);
        for mants in [&DEC[..], &HALF[..], &M125[..]] {
            put_f(out, nearest_level_bps(p, mants));
        }
        for mants in [&DEC[..], &HALF[..], &M125[..]] {
            put_f(out, nearest_level_bps(mfe_price, mants));
        }
        for x in [
            cm.rv60, cm.rv1440, cm.rng1440, cm.sigma_h, cm.ret60, cm.ret360, cm.drop360,
        ] {
            put_f(out, x);
        }
        let _ = write!(out, ",{}", u8::from(it.running));
        for x in [cm.age_days, cm.qv1440, cm.btc_prev60, cm.btc_next360] {
            put_f(out, x);
        }
        for h in HS {
            put_f(out, gross(self.cf, fm, it.t_f + 1, h, side));
        }
        put_f(out, mfe.map_or(f64::NAN, |m| side * ln_bps(m.0, p)));
        put_k(out, mfe.map(|m| m.1));
        put_f(out, mae.map_or(f64::NAN, |m| side * ln_bps(m.0, p)));
        put_k(out, mae.map(|m| m.1));
        put_f(out, mfe_price);
        for b in it.base {
            put_f(out, b);
        }
        if let Some(pl) = self.play {
            let _ = write!(
                out,
                ",{},{},{},{}",
                pl.natr_at(it.t_f) + 0.0,
                u8::from(pl.ok_at(it.t_f, 1)),
                u8::from(pl.ok_at(it.t_f, -1)),
                u8::from(pl.first_at(it.t_f))
            );
        }
        out.push(b'\n');
    }
}

/// Число через запятую; `-0` пишется как `0`, `NaN` — как `NaN`.
fn put_f(out: &mut Vec<u8>, x: f64) {
    let _ = write!(out, ",{}", x + 0.0);
}

fn put_k(out: &mut Vec<u8>, k: Option<usize>) {
    match k {
        Some(k) => {
            let _ = write!(out, ",{k}");
        }
        None => out.extend_from_slice(b",NaN"),
    }
}

/// Записать `triggers-<stage>.csv`: заголовок и контракты по алфавиту имён (`syms[индекс файла]`).
pub fn write(path: &Path, syms: &[String], parts: Vec<(usize, Vec<u8>)>) -> Result<()> {
    write_with(path, syms, parts, false)
}

/// `write`; `play_cols` — заголовок с `PLAY_COLS` (строки собраны `Collector::with_play`).
pub fn write_with(
    path: &Path,
    syms: &[String],
    mut parts: Vec<(usize, Vec<u8>)>,
    play_cols: bool,
) -> Result<()> {
    parts.sort_by(|a, b| syms[a.0].cmp(&syms[b.0]).then(a.0.cmp(&b.0)));
    let mut w = BufWriter::with_capacity(
        1 << 20,
        File::create(path).with_context(|| path.display().to_string())?,
    );
    if play_cols {
        writeln!(w, "{HEADER},{PLAY_COLS}")?;
    } else {
        writeln!(w, "{HEADER}")?;
    }
    for (_, bytes) in &parts {
        w.write_all(bytes)?;
    }
    w.flush()?;
    Ok(())
}
