//! `alpha pumponset run` — проход по контрактам кэша, агрегация в таблицы (спека: docs/findings/pumponset-run-spec-2026-11-05.md).
//!
//! Порядок: (1) `X_D` — из фита по вселенной (два прохода `XdAcc`) либо из `--xd`; (2) эпизоды `BTCUSDT` (для страты
//! «рыночный»), затем эпизоды всех контрактов и медианы длины фазы по стратам (нужны псевдо-моментам C2b до основного
//! прохода); (3) основной проход: куб → срабатывания и «следующее срабатывание» по правилам → C2a/метки/сделки →
//! лаги эпизодов → C2b; (4) запись таблиц.
//!
//! # Допущения (то, чего протокол и спека не называют)
//! 1. **Стадия `fit`** читает только минуты фита: контракт усекается по концу фита (`clip_contract`) до всех расчётов
//!    (эпизоды, куб, метки вперёд, сделки). Окна меток/сделок у края получают «нет данных», как у конца ряда.
//!    Стадия `test` читает контракт целиком (история для норм нужна), но срабатывания и эпизоды берёт только из
//!    периода теста; метки вперёд и сделки могут уходить за его конец (в данные после последнего месяца теста).
//! 2. **Вселенная** (П-34 §1): сутки `d` контракта входят, если сумма `qvol` за сутки `d − turnover_lag_days` ≥ порога.
//!    Первые сутки данных (суток-предшественника нет) не входят. Срабатывание — по суткам `t_f`, эпизод — по суткам
//!    `t0`; момент C2a — по суткам слота (выбор кандидатов режется маской через `cube.r = NaN` вне вселенной и
//!    периода, а `σ`-децили остаются по всему ряду); псевдо-момент C2b вне вселенной отбрасывается (его место не
//!    добирается — выборка чуть меньше, смещения нет). Контракты `is_leverage` и без строк OI пропущены (счётчики в
//!    `run-meta.json`). Отчёт о вселенной (П-34 §1, блок `universe` в `run-meta.json`): число снятых контрактов
//!    (подходящие, у которых последняя свеча раньше суток конца данных; конец данных — максимум по всем прочитанным
//!    контрактам, до усечения стадии `fit`) и эпизоды периода: всего, по правилу `d − lag`, по обороту суток `t0`
//!    (только счётчик), теряемые правилом (в вселенной суток `t0`, но не по правилу) и добавляемые им (наоборот).
//! 3. **X_D** считается по ходам концов `j` из фита в сутки вселенной (маска `XdAcc::add_masked`); для `test` без
//!    `--xd` пересчитывается тем же кодом по фиту (контракты усекаются по концу фита), а не по тесту.
//! 4. **Страты** эпизодов — `agg::strata_of`; `L_post'` C2b для страты = нижняя медиана `t_end − t0` эпизодов страты
//!    (период стадии, вселенная), общая для всех контрактов. Псевдо-моменты берёт `control::c2b_with` отдельным вызовом
//!    на страту (от `l_post` зависит пул); лаги правил считаются здесь по тем же окнам (`Fast::fill_lags`).
//! 5. **Месяц** строки лагов — месяц `t0` эпизода (у псевдо-момента — тот же: пул берётся в месяце эпизода).
//! 6. `first` считается среди правил, которые есть в прогоне (для `test` — среди замороженных K).
//! 7. Знак срабатывания 0 (`r_w` нулевая или не определена) — строки `daycells` нет (учтено в `triggers_no_sign`).
//! 8. **Наблюдаемость правила** (П-34 §1: минуты без признаков исключаются из выборки). Пара (эпизод | псевдо-момент
//!    C2b, правило) наблюдаема, если в слотах окна лага (`[t0 − L_pre, t_end]` / `[t' − L_pre, t' + L_post']`)
//!    есть хотя бы один слот, где уровень правила определён (все компоненты не `NaN`): нет нормы первые сутки/недели
//!    после листинга, дыра OI. Ненаблюдаемая пара не растит `n_ep` в `lags` и не входит в `first`/`progress` (сработать
//!    не могла); без этого такие пары шли бы как «не сработало» и занижали полноту, особенно на свежих листингах.
//!    Число пар по `kind` и страте — блок `lag_observability` в `run-meta.json`. Эпизод в таблицу эпизодов и в
//!    счётчик `episodes` входит по-прежнему (он есть в данных; не наблюдаемо только правило).
//! 9. **Рынок:** эпизоды самого `ref_symbol` (BTCUSDT) помечены `FLAG_MARKET` — по определению §2 они «рыночные».
//! 10. **Покрытие фандинга** (`trade.rs`, допущение 3): сделка с ценами, но без покрытия файлом ставок окна удержания
//!     в `nret` не входит; число таких сделок (всего, по H) и контрактов — блок `funding_coverage` в `run-meta.json`.

#![allow(
    // индексы ограничены размерами плотных массивов, заданных `Layout`; бюджет линтов (TK-211) не растим
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_precision_loss,
    clippy::unwrap_used,
    clippy::expect_used
)]

use super::agg::{
    fix, has_lag, strata_of, write_daycells, write_episodes, write_first, write_lags,
    write_progress, write_xd, Agg, Counters, Dims, EpRow, Layout, KINDS, NOT_OBS, NO_LAG, NS,
    PH_CONTROLS, PH_CUBE, PH_EPISODES, PH_PARSE, STRATA,
};
use super::cache::{Contract, SLOT_MIN};
use super::cli::{load_contract, sorted, sym_of};
use super::control::{self, yyyymm, Input, Label, TriggerRow};
use super::dump;
use super::episodes::{is_leverage, mark_market, Finder, XdAcc};
use super::exits::{self, ExitAgg};
use super::feat;
use super::params::Params;
use super::trade::{self, Skip};
use super::types::{Cube, Episode, Rule, XdTable, FLAG_MARKET, NF};
use crate::statarb::panel::{GRID0_MS, MINUTE_MS};
use anyhow::{bail, ensure, Context, Result};
use chrono::NaiveDate;
use clap::{Args, ValueEnum};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::Instant;

/// Минут в сутках и перевод доли в bps — единицы измерения, не параметры.
const DAY_MIN: i64 = 24 * 60;
const BPS: f64 = 1.0e4;
/// Минута сетки панелей, с которой начинается GRID0 (от начала эпохи Unix).
const MIN0: i64 = GRID0_MS / MINUTE_MS;
/// «Нет срабатывания» в таблице следующего срабатывания.
const NONE: u32 = u32::MAX;
/// Псевдо-правило для `control::c2b_with`, когда нужны только моменты (лаги считаются здесь).
const DUMMY: [Rule; 1] = [Rule {
    fam: 0,
    wi: 0,
    ti: 0,
}];

#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
pub enum Stage {
    Fit,
    Test,
}

impl Stage {
    pub fn name(self) -> &'static str {
        match self {
            Stage::Fit => "fit",
            Stage::Test => "test",
        }
    }
}

#[derive(Debug, Args)]
pub struct RunArgs {
    /// Каталог `<SYM>.pmc` (`alpha pumponset cache`).
    #[arg(long)]
    pub cache: PathBuf,
    /// `P-34-params.json`.
    #[arg(long)]
    pub params: PathBuf,
    #[arg(long)]
    pub out: PathBuf,
    #[arg(long, value_enum)]
    pub stage: Stage,
    /// `k.json` (`[{fam,wi,ti,...}]`): для `test` обязателен; для `fit` без него — все правила `feat::rules`.
    #[arg(long)]
    pub rules: Option<PathBuf>,
    /// Зерно контролей C2a/C2b.
    #[arg(long)]
    pub seed: u64,
    /// Сдвиг OI в слотах (Binance: 1, docs/findings/pumponset-step0-oi-timestamp-2026-11-05.md).
    #[arg(long)]
    pub oi_lag: u32,
    #[arg(long)]
    pub threads: usize,
    /// `xd.csv` фита; без него порог пересчитывается по фиту.
    #[arg(long)]
    pub xd: Option<PathBuf>,
    /// Первые N контрактов по алфавиту (замеры); пишется в `run-meta.json`.
    #[arg(long)]
    pub limit: Option<usize>,
    /// П-35: кроме основных таблиц — выходы в прибыль: для каждого первого срабатывания правил `--rules` и каждой
    /// стороны симулируются 84 конфигурации выхода (`exits.rs`) → `exits-<stage>.csv`, `exits-grid.csv`, блок `exits`
    /// в `run-meta.json`. Основные таблицы от флага не зависят. Память: на поток ≈ 3,4 КБ × правил × 2 × суток периода.
    #[arg(long)]
    pub exits: bool,
    /// Выгрузить первые срабатывания правил со всеми атрибутами момента и исходами в `triggers-<stage>.csv`
    /// (`dump.rs`); без флага прогон и его таблицы прежние.
    #[arg(long)]
    pub dump_triggers: bool,
}

// ---------- время, вселенная, период ----------

/// Сутки UTC от 1970-01-01 минуты `m` (от `GRID0_MS`).
pub fn day_of_min(m: i64) -> i64 {
    (MIN0 + m).div_euclid(DAY_MIN)
}

/// Сутки вселенной контракта (П-34 §1, допущение 2).
#[derive(Debug, Clone, Default)]
pub struct Universe {
    day0: i64,
    ok: Vec<bool>,
}

impl Universe {
    pub fn build(c: &Contract, min_turnover: f64, lag_days: u32) -> Universe {
        let n = c.close.len();
        if n == 0 {
            return Universe::default();
        }
        let d_first = day_of_min(c.first_min);
        let d_last = day_of_min(c.first_min + n as i64 - 1);
        let nd = (d_last - d_first + 1) as usize;
        let mut sums = vec![0.0f64; nd];
        for (i, &q) in c.qvol.iter().enumerate() {
            sums[(day_of_min(c.first_min + i as i64) - d_first) as usize] += f64::from(q);
        }
        let lag = i64::from(lag_days);
        let ok = (0..nd as i64)
            .map(|d| d >= lag && sums[(d - lag) as usize] >= min_turnover)
            .collect();
        Universe { day0: d_first, ok }
    }

    pub fn day_ok(&self, day: i64) -> bool {
        let k = day - self.day0;
        k >= 0 && (k as usize) < self.ok.len() && self.ok[k as usize]
    }

    pub fn min_ok(&self, m: i64) -> bool {
        self.day_ok(day_of_min(m))
    }

    /// Суток во вселенной среди суток периода `[lo_min, hi_min)`.
    pub fn days_in(&self, lo_min: i64, hi_min: i64) -> u64 {
        (day_of_min(lo_min)..=day_of_min(hi_min - 1))
            .filter(|&d| self.day_ok(d))
            .count() as u64
    }
}

/// Усечение контракта: остаются только минуты `< end_min` (и OI-слоты, фандинг до этого момента).
pub fn clip_contract(c: &mut Contract, end_min: i64) {
    let keep = (end_min - c.first_min).clamp(0, c.close.len() as i64) as usize;
    c.close.truncate(keep);
    c.vol.truncate(keep);
    c.qvol.truncate(keep);
    c.trades.truncate(keep);
    let slots =
        (end_min.div_euclid(SLOT_MIN) - c.oi_first_slot).clamp(0, c.oi[0].len() as i64) as usize;
    for col in &mut c.oi {
        col.truncate(slots);
    }
    c.funding.retain(|&(m, _)| m < end_min);
}

fn ym_parse(s: &str) -> Result<(i32, u32)> {
    let (y, m) = s
        .split_once('-')
        .with_context(|| format!("месяц {s:?}: нужен формат YYYY-MM"))?;
    let (y, m): (i32, u32) = (
        y.trim().parse().with_context(|| format!("месяц {s:?}"))?,
        m.trim().parse().with_context(|| format!("месяц {s:?}"))?,
    );
    ensure!((1..=12).contains(&m), "месяц {s:?}: номер месяца 1..12");
    Ok((y, m))
}

fn month_start_min(y: i32, m: u32) -> Result<i64> {
    let d = NaiveDate::from_ymd_opt(y, m, 1).with_context(|| format!("дата {y}-{m}"))?;
    let epoch = NaiveDate::from_ymd_opt(1970, 1, 1).context("эпоха")?;
    Ok(d.signed_duration_since(epoch).num_days() * DAY_MIN - MIN0)
}

/// Период `["YYYY-MM", "YYYY-MM"]` (оба месяца включительно) → `[от, до)` в минутах и список `yyyymm`.
pub fn period(r: &[String; 2]) -> Result<([i64; 2], Vec<i32>)> {
    let ((y0, m0), (y1, m1)) = (ym_parse(&r[0])?, ym_parse(&r[1])?);
    ensure!((y0, m0) <= (y1, m1), "период {r:?}: начало позже конца");
    let start = month_start_min(y0, m0)?;
    let end = if m1 == 12 {
        month_start_min(y1 + 1, 1)?
    } else {
        month_start_min(y1, m1 + 1)?
    };
    let mut months = Vec::new();
    let (mut y, mut m) = (y0, m0);
    while (y, m) <= (y1, m1) {
        months.push(y * 100 + m as i32);
        if m == 12 {
            y += 1;
            m = 1;
        } else {
            m += 1;
        }
    }
    Ok(([start, end], months))
}

// ---------- быстрые правила: «следующее срабатывание» ----------

/// Признаки только вверх (`z ≥ θ`): F1–F4, F9; у остальных `|z| ≥ θ` (как `feat::UP_ONLY`, сверено тестом).
const UP_ONLY: [bool; NF] = [
    true, true, true, true, false, false, false, false, true, false,
];

/// Уровень правила `(fam, wi)` по слотам куба: срабатывание при пороге θ ⇔ `f64::from(level) >= θ`. Составное правило —
/// минимум компонент, `NaN` у любой компоненты — `NaN` (не срабатывает). Равносильно `feat::fires`.
fn fill_level(cube: &Cube, fam: usize, wi: usize, out: &mut [f32]) {
    let (nw, n) = (cube.windows.len(), cube.n);
    out.fill(f32::INFINITY);
    for &f in feat::FAMILIES[fam] {
        let zs = &cube.z[(f * nw + wi) * n..(f * nw + wi + 1) * n];
        let up = UP_ONLY[f];
        for (o, &z) in out.iter_mut().zip(zs) {
            let v = if up { z } else { z.abs() };
            *o = if o.is_nan() || v.is_nan() {
                f32::NAN
            } else {
                o.min(v)
            };
        }
    }
}

fn ceil_div(a: i64, b: i64) -> i64 {
    -((-a).div_euclid(b))
}

/// Окно минут `[t_lo, t_hi]` → индексы слотов куба `[lo, hi]` (границы включены, за краем куба обрезается).
pub fn slot_range(slot0: i64, n: usize, t_lo: i64, t_hi: i64) -> Option<(usize, usize)> {
    let lo = (ceil_div(t_lo, SLOT_MIN) - slot0).max(0);
    let hi = (t_hi.div_euclid(SLOT_MIN) - slot0).min(n as i64 - 1);
    (lo <= hi).then_some((lo as usize, hi as usize))
}

/// Правила одной пары (семейство, окно): `(индекс порога, позиция правила)`.
struct Pair {
    fam: usize,
    wi: usize,
    tis: Vec<(usize, usize)>,
}

/// Для каждого правила: первые срабатывания (`trig`, отфильтрованные `keep(t_f)`) и таблица следующего срабатывания
/// `nxt[слот · nr + правило]` — наименьший слот `≥ слот` со срабатыванием (`NONE` — нет). Первое срабатывание лага
/// в окне — одно чтение. Буферы переиспользуются между контрактами.
pub struct Fast {
    pub nr: usize,
    pub n: usize,
    pub nxt: Vec<u32>,
    pub trig: Vec<Vec<usize>>,
    lvl: Vec<f32>,
    /// Пара (семейство, окно) каждого правила и префиксные суммы числа слотов с определённым уровнем по парам:
    /// `obs[пара · (n + 1) + i]` — слотов `< i` с уровнем не `NaN` (наблюдаемость правила в окне лага).
    pair_of: Vec<u32>,
    obs: Vec<u32>,
}

impl Default for Fast {
    fn default() -> Self {
        Fast::new()
    }
}

impl Fast {
    pub fn new() -> Fast {
        Fast {
            nr: 0,
            n: 0,
            nxt: Vec::new(),
            trig: Vec::new(),
            lvl: Vec::new(),
            pair_of: Vec::new(),
            obs: Vec::new(),
        }
    }

    /// `quiet` — слотов тишины перед первым срабатыванием (`L_pre / 5`).
    pub fn build(
        &mut self,
        cube: &Cube,
        p: &Params,
        rules: &[Rule],
        quiet: usize,
        keep: &dyn Fn(i64) -> bool,
    ) {
        let (n, nr) = (cube.n, rules.len());
        self.n = n;
        self.nr = nr;
        self.nxt.clear();
        self.nxt.resize((n + 1) * nr, NONE);
        self.lvl.clear();
        self.lvl.resize(n, 0.0);
        for t in &mut self.trig {
            t.clear();
        }
        self.trig.resize_with(nr, Vec::new);
        // правила по парам (семейство, окно): уровень считается один раз на пару
        let mut pairs: Vec<Pair> = Vec::new();
        self.pair_of.clear();
        for (ri, r) in rules.iter().enumerate() {
            let (fam, wi, ti) = (r.fam as usize, r.wi as usize, r.ti as usize);
            let pi = match pairs.iter().position(|q| q.fam == fam && q.wi == wi) {
                Some(pi) => {
                    pairs[pi].tis.push((ti, ri));
                    pi
                }
                None => {
                    pairs.push(Pair {
                        fam,
                        wi,
                        tis: vec![(ti, ri)],
                    });
                    pairs.len() - 1
                }
            };
            self.pair_of.push(pi as u32);
        }
        self.obs.clear();
        self.obs.resize(pairs.len() * (n + 1), 0);
        for (pi, Pair { fam, wi, tis }) in pairs.iter().enumerate() {
            fill_level(cube, *fam, *wi, &mut self.lvl);
            let pre = &mut self.obs[pi * (n + 1)..(pi + 1) * (n + 1)];
            let mut c = 0u32;
            for (o, &v) in pre.iter_mut().zip(&self.lvl) {
                *o = c;
                c += u32::from(!v.is_nan());
            }
            pre[n] = c;
            let thr: Vec<f64> = tis.iter().map(|&(ti, _)| p.features.theta[ti]).collect();
            let mut last: Vec<Option<usize>> = vec![None; tis.len()];
            for (i, &v) in self.lvl.iter().enumerate() {
                if v.is_nan() {
                    continue;
                }
                let vf = f64::from(v);
                for (k, &(_, ri)) in tis.iter().enumerate() {
                    if vf >= thr[k] {
                        if last[k].is_none_or(|l| l + quiet < i)
                            && keep(SLOT_MIN * (cube.slot0 + i as i64))
                        {
                            self.trig[ri].push(i);
                        }
                        last[k] = Some(i);
                    }
                }
            }
            let mut nx = vec![NONE; tis.len()];
            for i in (0..n).rev() {
                let vf = f64::from(self.lvl[i]);
                let row = &mut self.nxt[i * nr..(i + 1) * nr];
                for (k, &(_, ri)) in tis.iter().enumerate() {
                    if vf >= thr[k] {
                        nx[k] = i as u32;
                    }
                    row[ri] = nx[k];
                }
            }
        }
    }

    /// Пометить `NOT_OBS` правила, у которых в слотах окна минут `[t_lo, t_hi]` нет ни одного определённого уровня
    /// (те же слоты, что берёт `fill_lags`; такое правило сработать не могло, поэтому в `out` у него уже `NO_LAG`).
    pub fn mark_unobserved(&self, slot0: i64, t_lo: i64, t_hi: i64, out: &mut [i32]) {
        match slot_range(slot0, self.n, t_lo, t_hi) {
            None => out.fill(NOT_OBS),
            Some((lo, hi)) => {
                let stride = self.n + 1;
                for (o, &pi) in out.iter_mut().zip(&self.pair_of) {
                    let pre = &self.obs[pi as usize * stride..(pi as usize + 1) * stride];
                    if pre[hi + 1] == pre[lo] {
                        *o = NOT_OBS;
                    }
                }
            }
        }
    }

    /// Лаги всех правил для окна минут `[t_lo, t_hi]` относительно якоря `t_anchor`: `t_f − t_anchor` первого
    /// срабатывания в окне или `NO_LAG`.
    pub fn fill_lags(&self, slot0: i64, t_anchor: i64, t_lo: i64, t_hi: i64, out: &mut [i32]) {
        match slot_range(slot0, self.n, t_lo, t_hi) {
            None => out.fill(NO_LAG),
            Some((lo, hi)) => {
                let row = &self.nxt[lo * self.nr..(lo + 1) * self.nr];
                for (o, &j) in out.iter_mut().zip(row) {
                    *o = if j != NONE && (j as usize) <= hi {
                        (SLOT_MIN * (slot0 + i64::from(j)) - t_anchor) as i32
                    } else {
                        NO_LAG
                    };
                }
            }
        }
    }
}

// ---------- параллельный проход ----------

/// Элементы `0..n` раздаются потокам счётчиком; у потока своё состояние `T` (из `init`), состояния возвращаются в
/// порядке потоков. Первая ошибка по номеру элемента останавливает остальных.
fn par_map<T, I, F>(threads: usize, n: usize, init: I, f: F) -> Result<Vec<T>>
where
    T: Send,
    I: Fn(usize) -> T + Sync,
    F: Fn(&mut T, usize) -> Result<()> + Sync,
{
    let next = AtomicUsize::new(0);
    let failed = AtomicBool::new(false);
    let errs: Mutex<Vec<(usize, anyhow::Error)>> = Mutex::new(Vec::new());
    let mut outs: Vec<T> = Vec::new();
    let mut panicked = false;
    std::thread::scope(|s| {
        let hs: Vec<_> = (0..threads.max(1))
            .map(|w| {
                let (next, failed, errs, init, f) = (&next, &failed, &errs, &init, &f);
                s.spawn(move || {
                    let mut st = init(w);
                    loop {
                        if failed.load(Ordering::Relaxed) {
                            break;
                        }
                        let i = next.fetch_add(1, Ordering::Relaxed);
                        if i >= n {
                            break;
                        }
                        if let Err(e) = f(&mut st, i) {
                            failed.store(true, Ordering::Relaxed);
                            if let Ok(mut g) = errs.lock() {
                                g.push((i, e));
                            }
                            break;
                        }
                    }
                    st
                })
            })
            .collect();
        for h in hs {
            match h.join() {
                Ok(st) => outs.push(st),
                Err(_) => panicked = true,
            }
        }
    });
    ensure!(!panicked, "поток прохода завершился паникой");
    let mut errs = errs.into_inner().unwrap_or_default();
    errs.sort_by_key(|e| e.0);
    if let Some((_, e)) = errs.into_iter().next() {
        return Err(e);
    }
    Ok(outs)
}

// ---------- контекст прогона ----------

struct Run<'a> {
    p: &'a Params,
    min_turnover: f64,
    lag_days: u32,
    stage: Stage,
    /// Период стадии и период фита, `[от, до)` в минутах.
    pmin: [i64; 2],
    fit: [i64; 2],
    /// Усечение контрактов (только стадия `fit`).
    clip: Option<i64>,
    lay: Layout,
    xd: XdTable,
    seed: u64,
    oi_lag: u32,
    /// Слотов тишины перед первым срабатыванием и `L_pre`, мин.
    quiet: usize,
    l_pre: i64,
    /// `L_post'` C2b по стратам (нижняя медиана длины фазы).
    l_post: [i64; NS],
    ref_sym: String,
    /// `(fam · nw + wi) · nt + ti` → позиция правила (`u32::MAX` — правила нет в прогоне).
    rpos: Vec<u32>,
    /// Считать выходы П-35 (`--exits`).
    exits: bool,
    /// Эталон `btc_*` выгрузки срабатываний (`--dump-triggers`); `None` — выгрузки нет.
    dump: Option<dump::Shared>,
}

impl Run<'_> {
    fn in_stage(&self, m: i64) -> bool {
        m >= self.pmin[0] && m < self.pmin[1]
    }

    fn is_member(&self, e: &Episode, u: &Universe) -> bool {
        self.in_stage(e.t0) && u.min_ok(e.t0)
    }

    fn month_idx(&self, m: i64) -> Option<usize> {
        self.lay.months.binary_search(&yyyymm(m)).ok()
    }

    fn ridx(&self, r: Rule) -> usize {
        let d = self.lay.dims;
        self.rpos[(r.fam as usize * d.nw + r.wi as usize) * d.nt + r.ti as usize] as usize
    }
}

/// Страта `s` входит в маску страт.
fn has(mask: u8, s: usize) -> bool {
    (mask >> s) & 1 == 1
}

enum Elig {
    Ok,
    Leverage,
    Empty,
    NoOi,
}

fn eligible(sym: &str, c: &Contract) -> Elig {
    if is_leverage(sym) {
        Elig::Leverage
    } else if c.close.is_empty() {
        Elig::Empty
    } else if c.oi[0].is_empty() {
        Elig::NoOi
    } else {
        Elig::Ok
    }
}

#[derive(Debug, Clone, Copy, Default)]
struct Skips {
    leverage: u64,
    empty: u64,
    no_oi: u64,
    no_funding: u64,
}

/// Контракт стадии и минута после его последней свечи (до усечения стадии `fit`).
fn load_end(rn: &Run, path: &Path) -> Result<(Contract, i64)> {
    let mut c = load_contract(path)?;
    let end = c.first_min + c.close.len() as i64;
    if let Some(e) = rn.clip {
        clip_contract(&mut c, e);
    }
    Ok((c, end))
}

fn load(rn: &Run, path: &Path) -> Result<Contract> {
    Ok(load_end(rn, path)?.0)
}

/// Отчёт о вселенной (П-34 §1): снятые контракты и эпизоды, теряемые правилом `d − lag`. Суммы по потокам не зависят
/// от раздачи контрактов потокам (счётчики и максимум).
#[derive(Debug, Clone, Default)]
struct Selection {
    /// Минута после последней свечи каждого подходящего контракта.
    ends: Vec<i64>,
    /// Максимум такой минуты по всем прочитанным контрактам (в том числе пропущенным).
    data_end: i64,
    /// Эпизоды подходящих контрактов с `t0` в периоде стадии: всего, по правилу прогона, по обороту суток `t0`.
    eps_period: u64,
    eps_rule: u64,
    eps_same_day: u64,
    /// В вселенной суток `t0`, но не по правилу / по правилу, но не в вселенной суток `t0`.
    eps_lost: u64,
    eps_gained: u64,
}

impl Selection {
    fn add_episode(&mut self, by_rule: bool, same_day: bool) {
        self.eps_period += 1;
        self.eps_rule += u64::from(by_rule);
        self.eps_same_day += u64::from(same_day);
        self.eps_lost += u64::from(same_day && !by_rule);
        self.eps_gained += u64::from(by_rule && !same_day);
    }

    fn merge(&mut self, o: Selection) {
        self.ends.extend(o.ends);
        self.data_end = self.data_end.max(o.data_end);
        self.eps_period += o.eps_period;
        self.eps_rule += o.eps_rule;
        self.eps_same_day += o.eps_same_day;
        self.eps_lost += o.eps_lost;
        self.eps_gained += o.eps_gained;
    }

    /// Сутки последней свечи данных и число подходящих контрактов, чья последняя свеча в более ранние сутки.
    fn delisted(&self) -> (i64, u64) {
        let last = day_of_min(self.data_end - 1);
        let n = self
            .ends
            .iter()
            .filter(|&&e| day_of_min(e - 1) < last)
            .count();
        (last, n as u64)
    }
}

// ---------- X_D ----------

fn xd_passes(
    rn: &Run,
    files: &[PathBuf],
    syms: &[String],
    threads: usize,
) -> Result<(XdTable, Vec<[u64; 2]>)> {
    let feed = |acc: &mut XdAcc, i: usize| -> Result<()> {
        let mut c = load_contract(&files[i])?;
        clip_contract(&mut c, rn.fit[1]);
        if !matches!(eligible(&syms[i], &c), Elig::Ok) {
            return Ok(());
        }
        let u = Universe::build(&c, rn.min_turnover, rn.lag_days);
        acc.add_masked(&c, rn.p, rn.fit, |m| u.min_ok(m));
        Ok(())
    };
    let mut master = XdAcc::new(rn.p);
    for a in par_map(threads, files.len(), |_| XdAcc::new(rn.p), feed)? {
        master.merge(a);
    }
    let counts = master.counts();
    master.begin_pass2();
    for a in par_map(threads, files.len(), |_| master.pass2_worker(), feed)? {
        master.merge(a);
    }
    Ok((master.finish()?, counts))
}

fn read_xd(path: &Path, nd: usize) -> Result<(XdTable, Vec<[u64; 2]>)> {
    let text = fs::read_to_string(path).with_context(|| path.display().to_string())?;
    let mut x = vec![[f64::NAN; 2]; nd];
    let mut cnt = vec![[0u64; 2]; nd];
    let mut seen = vec![[false; 2]; nd];
    for (k, line) in text.lines().enumerate().skip(1) {
        if line.trim().is_empty() {
            continue;
        }
        let f: Vec<&str> = line.split(',').map(str::trim).collect();
        ensure!(
            f.len() >= 4,
            "{}: строка {}: меньше 4 полей",
            path.display(),
            k + 1
        );
        let di: usize = f[0]
            .parse()
            .with_context(|| format!("xd строка {}", k + 1))?;
        let sign: i32 = f[1]
            .parse()
            .with_context(|| format!("xd строка {}", k + 1))?;
        ensure!(
            di < nd && (sign == 1 || sign == -1),
            "{}: строка {}: di/sign вне диапазона",
            path.display(),
            k + 1
        );
        let si = usize::from(sign < 0);
        x[di][si] = f[2]
            .parse()
            .with_context(|| format!("xd строка {}", k + 1))?;
        cnt[di][si] = f[3]
            .parse()
            .with_context(|| format!("xd строка {}", k + 1))?;
        seen[di][si] = true;
    }
    ensure!(
        seen.iter().all(|s| s[0] && s[1]),
        "{}: нет строки для каждой пары (di, знак)",
        path.display()
    );
    Ok((XdTable { x }, cnt))
}

// ---------- эпизоды и медианы ----------

/// Подготовка контракта: сутки вселенной, эпизоды с флагами и маска страт членов (эпизоды вселенной и периода).
struct Pre {
    univ: Universe,
    eps: Vec<Episode>,
    mask: Vec<u8>,
}

struct EAcc {
    finder: Finder,
    pre: Vec<(usize, Pre)>,
    /// Длины фаз членов по «особым» стратам: market, nonmarket, outlier, wick.
    lens: [Vec<i64>; 4],
    sk: Skips,
    sel: Selection,
    secs: [f64; 2],
}

fn btc_episodes(rn: &Run, dir: &Path) -> Result<Option<Vec<Episode>>> {
    let path = dir.join(format!("{}.pmc", rn.ref_sym));
    if !path.exists() {
        return Ok(None);
    }
    let c = load(rn, &path)?;
    Ok(Some(Finder::default().find(&c, rn.p, &rn.xd, &rn.ref_sym)))
}

fn prepare(
    rn: &Run,
    btc: &[Episode],
    acc: &mut EAcc,
    (idx, path, sym): (usize, &Path, &str),
) -> Result<()> {
    let t = Instant::now();
    let (c, end) = load_end(rn, path)?;
    acc.secs[0] += t.elapsed().as_secs_f64();
    acc.sel.data_end = acc.sel.data_end.max(end);
    match eligible(sym, &c) {
        Elig::Ok => {}
        Elig::Leverage => {
            acc.sk.leverage += 1;
            return Ok(());
        }
        Elig::Empty => {
            acc.sk.empty += 1;
            return Ok(());
        }
        Elig::NoOi => {
            acc.sk.no_oi += 1;
            return Ok(());
        }
    }
    if c.funding.is_empty() {
        acc.sk.no_funding += 1;
    }
    acc.sel.ends.push(end);
    let t = Instant::now();
    let univ = Universe::build(&c, rn.min_turnover, rn.lag_days);
    // только счётчик «что было бы по обороту суток t0» (П-34 §1); в выборку не входит
    let same_day = Universe::build(&c, rn.min_turnover, 0);
    let mut eps = acc.finder.find(&c, rn.p, &rn.xd, sym);
    if sym == rn.ref_sym {
        // эпизоды самого эталона «рыночные» по определению §2 (они же эталон для остальных)
        for e in &mut eps {
            e.flags |= FLAG_MARKET;
        }
    } else {
        mark_market(
            &mut eps,
            btc,
            rn.p.episodes.market_stratum.min_overlap_share,
        );
    }
    let mut mask = vec![0u8; eps.len()];
    for (k, e) in eps.iter().enumerate() {
        if rn.in_stage(e.t0) {
            acc.sel
                .add_episode(univ.min_ok(e.t0), same_day.min_ok(e.t0));
        }
        if !rn.is_member(e, &univ) {
            continue;
        }
        let (st, ns) = strata_of(e.flags);
        for &s in &st[..ns] {
            mask[k] |= 1 << s;
        }
        acc.lens[st[ns - 1] - 1].push(e.t_end - e.t0);
    }
    acc.secs[1] += t.elapsed().as_secs_f64();
    acc.pre.push((idx, Pre { univ, eps, mask }));
    Ok(())
}

/// Нижняя медиана (`floor(0.5·(n−1))`-й по порядку) отсортированных длин; пусто → 0 (страта не используется).
fn lower_median(v: &[i64]) -> i64 {
    if v.is_empty() {
        0
    } else {
        v[(v.len() - 1) / 2]
    }
}

// ---------- основной проход ----------

struct Work {
    agg: Agg,
    fast: Fast,
    lagbuf: Vec<i32>,
    /// Накопитель выходов П-35; `None` без `--exits`.
    exits: Option<ExitAgg>,
    /// Выгрузка срабатываний: текст строк контрактов потока `(индекс файла, байты)`.
    dumped: Vec<(usize, Vec<u8>)>,
}

/// Цены и фандинг контракта для сделок и прогресса.
struct Prices<'a> {
    cf: &'a [f64],
    first_min: i64,
    funding: trade::Funding<'a>,
}

fn mask_r(cube: &mut Cube, rn: &Run, univ: &Universe) {
    let (n, nw) = (cube.n, cube.windows.len());
    for i in 0..n {
        let t = SLOT_MIN * (cube.slot0 + i as i64);
        if !(rn.in_stage(t) && univ.min_ok(t)) {
            for wi in 0..nw {
                cube.r[wi * n + i] = f32::NAN;
            }
        }
    }
}

fn sink_trigger(rn: &Run, agg: &mut Agg, px: &Prices, row: TriggerRow) {
    agg.cnt.triggers += 1;
    if row.sign == 0 {
        agg.cnt.triggers_no_sign += 1;
        return;
    }
    let ri = rn.ridx(row.rule);
    let si = usize::from(row.sign < 0);
    let doff = (day_of_min(row.t_f) - rn.lay.day0) as usize;
    for (hi, &h) in rn.lay.h_min.iter().enumerate() {
        let tr = trade::round_trip(px.cf, px.first_min, &px.funding, row.t_f, h, row.sign);
        let cell = agg.cell_mut(ri, si, hi, doff);
        match row.label[hi] {
            Label::Hit => {
                cell.n += 1;
                cell.hit += 1;
            }
            Label::Miss => cell.n += 1,
            Label::NoData => {}
        }
        cell.bn += u32::from(row.c2a_n[hi]);
        cell.bhit += u32::from(row.c2a_hit[hi]);
        match tr {
            Ok(t) => {
                cell.nret += 1;
                cell.gross += fix(t.gross_bps);
                cell.fund += fix(t.fund_bps);
                agg.cnt.trades += 1;
            }
            Err(Skip::Funding) => {
                agg.cnt.trades_funding_gap += 1;
                agg.gap_h[hi] += 1;
            }
            Err(Skip::NoData) => {}
        }
    }
}

/// Прогресс хода по эпизоду для сработавших правил (спека `progress`): сколько хода уже прошло к входу, какая доля
/// осталась, ход до пика.
fn episode_progress(agg: &mut Agg, px: &Prices, e: &Episode, sm: u8, lags: &[i32], mi: usize) {
    let at = |m: i64| -> Option<f64> {
        usize::try_from(m - px.first_min)
            .ok()
            .and_then(|k| px.cf.get(k).copied())
            .filter(|x| x.is_finite() && *x > 0.0)
    };
    let (Some(c0), Some(ce)) = (at(e.t0), at(e.t_end)) else {
        return;
    };
    let sign = f64::from(e.sign);
    let den = sign * (ce / c0).ln();
    if !den.is_finite() || den.abs() <= 0.0 {
        return;
    }
    let (si, di) = (usize::from(e.sign < 0), e.di as usize);
    for (ri, &l) in lags.iter().enumerate() {
        if !has_lag(l) {
            continue;
        }
        let Some(cb) = at(e.t0 + i64::from(l) + 1) else {
            continue;
        };
        let num = sign * (ce / cb).ln();
        let vals = [
            (sign * (cb / c0).ln() * BPS) as f32,
            (num / den) as f32,
            (num * BPS) as f32,
        ];
        for s in 0..NS {
            if has(sm, s) {
                agg.push_progress(s, si, di, mi, ri, vals);
            }
        }
    }
}

fn process(
    rn: &Run,
    wk: &mut Work,
    (idx, path, sym): (usize, &Path, &str),
    pre: &Pre,
) -> Result<()> {
    let t = Instant::now();
    let c = load(rn, path)?;
    wk.agg.cnt.secs[PH_PARSE] += t.elapsed().as_secs_f64();

    let t = Instant::now();
    let mut cube = feat::build_cube(&c, rn.p, rn.oi_lag);
    if cube.n == 0 {
        return Ok(());
    }
    let cf = c.close_ff();
    let mut funding = c.funding.clone();
    funding.sort_unstable_by_key(|&(m, _)| m);
    let univ = &pre.univ;
    wk.fast.build(&cube, rn.p, &rn.lay.rules, rn.quiet, &|t_f| {
        rn.in_stage(t_f) && univ.min_ok(t_f)
    });
    mask_r(&mut cube, rn, univ);
    wk.agg.cnt.secs[PH_CUBE] += t.elapsed().as_secs_f64();

    let t = Instant::now();
    let Work {
        agg,
        fast,
        lagbuf,
        exits,
        dumped,
    } = wk;
    agg.cnt.contracts += 1;
    agg.cnt.symbol_days += univ.days_in(rn.pmin[0], rn.pmin[1]);
    let px = Prices {
        cf: &cf,
        first_min: c.first_min,
        funding: trade::Funding::new(&funding),
    };
    let inp = Input {
        sym,
        cube: &cube,
        cf: &cf,
        first_min: c.first_min,
        eps: &pre.eps,
    };
    // выходы П-35: те же первые срабатывания, что берёт основной проход (до того, как C2a их заберёт), без контролей
    if let Some(ex) = exits.as_mut() {
        let t = Instant::now();
        let xp = exits::Px {
            cf: px.cf,
            first_min: px.first_min,
            funding: px.funding,
        };
        ex.add_triggers(&rn.lay, &cube, &fast.trig, &xp);
        ex.cnt.secs += t.elapsed().as_secs_f64();
    }
    let gap0 = agg.cnt.trades_funding_gap;
    let mut dc = rn
        .dump
        .as_ref()
        .map(|sh| dump::Collector::new(sh, sym, &c, &cf, &cube));
    {
        let trig = &mut fast.trig;
        control::c2a_moments_with(
            &inp,
            &rn.lay.rules,
            &rn.xd,
            rn.p,
            rn.seed,
            dc.is_some(),
            |r| std::mem::take(&mut trig[rn.ridx(r)]),
            |row, moments| {
                if let Some(d) = dc.as_mut() {
                    d.add(&row, moments);
                }
                sink_trigger(rn, agg, &px, row);
            },
        )?;
    }
    if let Some(d) = dc {
        dumped.push((idx, d.finish()));
    }
    agg.cnt.contracts_funding_gap += u64::from(agg.cnt.trades_funding_gap > gap0);
    // лаги эпизодов (kind = ep)
    let slot0 = cube.slot0;
    lagbuf.clear();
    lagbuf.resize(rn.lay.dims.nr, NO_LAG);
    let mut present = 0u8;
    for (k, e) in pre.eps.iter().enumerate() {
        let sm = pre.mask[k];
        if sm == 0 {
            continue;
        }
        let Some(mi) = rn.month_idx(e.t0) else {
            continue;
        };
        present |= sm;
        agg.cnt.episodes += 1;
        agg.eps.push(EpRow::new(idx, e));
        fast.fill_lags(slot0, e.t0, e.t0 - rn.l_pre, e.t_end, lagbuf);
        fast.mark_unobserved(slot0, e.t0 - rn.l_pre, e.t_end, lagbuf);
        let (si, di) = (usize::from(e.sign < 0), e.di as usize);
        for s in 0..NS {
            if has(sm, s) {
                agg.record_lags(&rn.lay, 0, s, si, di, mi, lagbuf);
            }
        }
        episode_progress(agg, &px, e, sm, lagbuf, mi);
    }
    // нулевая линия C2b (kind = c2b): моменты берёт `control::c2b_with`, лаги — таблица следующего срабатывания
    for s in 0..NS {
        if !has(present, s) {
            continue;
        }
        let lp = rn.l_post[s];
        control::c2b_with(
            &inp,
            &DUMMY,
            rn.p,
            lp,
            rn.seed,
            |_, _| true,
            |row| {
                let k = row.episode_idx as usize;
                if !has(pre.mask[k], s) || !univ.min_ok(row.t_pseudo) {
                    return;
                }
                let e = &pre.eps[k];
                let Some(mi) = rn.month_idx(e.t0) else {
                    return;
                };
                agg.cnt.c2b_moments += 1;
                fast.fill_lags(
                    slot0,
                    row.t_pseudo,
                    row.t_pseudo - rn.l_pre,
                    row.t_pseudo + lp,
                    lagbuf,
                );
                fast.mark_unobserved(slot0, row.t_pseudo - rn.l_pre, row.t_pseudo + lp, lagbuf);
                agg.record_lags(
                    &rn.lay,
                    1,
                    s,
                    usize::from(e.sign < 0),
                    e.di as usize,
                    mi,
                    lagbuf,
                );
            },
        )?;
    }
    agg.cnt.secs[PH_CONTROLS] += t.elapsed().as_secs_f64();
    Ok(())
}

// ---------- правила, запуск ----------

#[derive(Deserialize)]
struct RuleJson {
    fam: u8,
    wi: u8,
    ti: u8,
}

fn load_rules(path: &Path, p: &Params) -> Result<Vec<Rule>> {
    let text = fs::read_to_string(path).with_context(|| path.display().to_string())?;
    let v: Vec<RuleJson> = serde_json::from_str(&text)
        .with_context(|| format!("{}: ожидается [{{fam,wi,ti}},…]", path.display()))?;
    ensure!(!v.is_empty(), "{}: список правил пуст", path.display());
    let (nw, nt) = (p.features.windows_min.len(), p.features.theta.len());
    let mut out: Vec<Rule> = Vec::with_capacity(v.len());
    for r in v {
        ensure!(
            (r.fam as usize) < feat::FAMILIES.len() && (r.wi as usize) < nw && (r.ti as usize) < nt,
            "{}: правило ({}, {}, {}) вне сетки",
            path.display(),
            r.fam,
            r.wi,
            r.ti
        );
        let rule = Rule {
            fam: r.fam,
            wi: r.wi,
            ti: r.ti,
        };
        ensure!(
            !out.contains(&rule),
            "{}: правило {rule:?} повторено",
            path.display()
        );
        out.push(rule);
    }
    Ok(out)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Пик резидентной памяти, МБ (`VmHWM`); вне Linux — `None`.
fn peak_rss_mb() -> Option<f64> {
    let s = fs::read_to_string("/proc/self/status").ok()?;
    let kb: f64 = s
        .lines()
        .find(|l| l.starts_with("VmHWM:"))?
        .split_whitespace()
        .nth(1)?
        .parse()
        .ok()?;
    Some(kb / 1024.0)
}

pub fn run(a: RunArgs) -> Result<()> {
    let t_all = Instant::now();
    let text = fs::read_to_string(&a.params).with_context(|| a.params.display().to_string())?;
    let p = Params::from_json(&text)?;
    let uni = p.universe()?.clone();
    let doc: serde_json::Value = serde_json::from_str(&text).context("params.json")?;
    let ref_sym = doc
        .pointer("/episodes/market_stratum/ref_symbol")
        .and_then(|v| v.as_str())
        .context("params.json: нет episodes.market_stratum.ref_symbol")?
        .to_string();
    let mut sha = Sha256::new();
    sha.update(text.as_bytes());
    let params_sha256 = hex(&sha.finalize());
    control::check_label_d(&p)?;
    let threads = a.threads.max(1);
    let (nw, nt, nd) = (
        p.features.windows_min.len(),
        p.features.theta.len(),
        p.episodes.d_min.len(),
    );
    let l_pre = i64::from(p.onset.l_pre_min);
    let max_d = i64::from(p.episodes.d_min.iter().copied().max().unwrap_or(0));
    ensure!(
        l_pre + max_d < i64::from(i16::MAX),
        "L_pre + max(D) не помещается в i16 (хранение лагов)"
    );
    ensure!(
        l_pre % SLOT_MIN == 0,
        "onset.L_pre_min кратно слоту {SLOT_MIN} мин"
    );

    let (fit, _) = period(&p.split.fit)?;
    let (pmin, months) = match a.stage {
        Stage::Fit => period(&p.split.fit)?,
        Stage::Test => period(&p.split.test)?,
    };
    let rules = match (&a.rules, a.stage) {
        (Some(f), _) => load_rules(f, &p)?,
        (None, Stage::Fit) => feat::rules(&p),
        (None, Stage::Test) => bail!("--rules обязателен для стадии test (k.json фита)"),
    };
    let mut rpos = vec![u32::MAX; feat::FAMILIES.len() * nw * nt];
    for (k, r) in rules.iter().enumerate() {
        rpos[(r.fam as usize * nw + r.wi as usize) * nt + r.ti as usize] = k as u32;
    }

    let ndays = ((pmin[1] - pmin[0]) / DAY_MIN) as usize;
    let day_month: Vec<i32> = (0..ndays as i64)
        .map(|d| yyyymm(pmin[0] + d * DAY_MIN))
        .collect();
    let dims = Dims {
        nr: rules.len(),
        nd,
        nm: months.len(),
        ndays,
        nh: p.label_forward.h_min.len(),
        nw,
        nt,
    };
    let lay = Layout::new(
        a.stage.name(),
        dims,
        rules,
        day_of_min(pmin[0]),
        day_month,
        months,
        p.label_forward.h_min.clone(),
    );
    let mut rn = Run {
        p: &p,
        min_turnover: uni.min_quote_turnover_usd_per_day,
        lag_days: uni.turnover_lag_days,
        stage: a.stage,
        pmin,
        fit,
        clip: (a.stage == Stage::Fit).then_some(pmin[1]),
        lay,
        xd: XdTable { x: Vec::new() },
        seed: a.seed,
        oi_lag: a.oi_lag,
        quiet: (l_pre / SLOT_MIN) as usize,
        l_pre,
        l_post: [0; NS],
        ref_sym,
        rpos,
        exits: a.exits,
        dump: None,
    };

    let mut files = sorted(&a.cache, ".pmc")?;
    if let Some(n) = a.limit {
        files.truncate(n);
    }
    ensure!(!files.is_empty(), "в {} нет файлов .pmc", a.cache.display());
    let syms: Vec<String> = files.iter().map(|f| sym_of(f, ".pmc")).collect();
    fs::create_dir_all(&a.out)?;

    // 1. X_D
    let t = Instant::now();
    let (xd, windows, xd_source) = match &a.xd {
        Some(f) => {
            let (x, w) = read_xd(f, nd)?;
            (x, w, "file")
        }
        None => {
            let (x, w) = xd_passes(&rn, &files, &syms, threads)?;
            (x, w, "computed")
        }
    };
    let secs_xd = t.elapsed().as_secs_f64();
    ensure!(
        xd.x.iter()
            .all(|r| r.iter().all(|v| v.is_finite() && *v > 0.0)),
        "X_D не определён или не положителен ({xd:?}): выборка фита пуста?"
    );
    rn.xd = xd;
    if a.dump_triggers {
        let btc = a.cache.join(format!("{}.pmc", rn.ref_sym));
        rn.dump = Some(dump::Shared::load(&btc, rn.clip)?);
    }

    // 2. эпизоды: BTC первым, затем все контракты; медианы длины фазы по стратам
    let btc = btc_episodes(&rn, &a.cache)?;
    let btc_missing = btc.is_none();
    let btc = btc.unwrap_or_default();
    let eaccs = par_map(
        threads,
        files.len(),
        |_| EAcc {
            finder: Finder::default(),
            pre: Vec::new(),
            lens: Default::default(),
            sk: Skips::default(),
            sel: Selection::default(),
            secs: [0.0; 2],
        },
        {
            let (rn, btc, files, syms) = (&rn, &btc, &files, &syms);
            move |acc: &mut EAcc, i: usize| {
                prepare(rn, btc, acc, (i, files[i].as_path(), syms[i].as_str()))
            }
        },
    )?;
    let mut pres: Vec<Option<Pre>> = (0..files.len()).map(|_| None).collect();
    let mut lens: [Vec<i64>; 4] = Default::default();
    let (mut sk, mut e_secs) = (Skips::default(), [0.0f64; 2]);
    let mut sel = Selection::default();
    for acc in eaccs {
        sel.merge(acc.sel);
        for (i, pre) in acc.pre {
            pres[i] = Some(pre);
        }
        for (a_, b_) in lens.iter_mut().zip(acc.lens) {
            a_.extend(b_);
        }
        sk.leverage += acc.sk.leverage;
        sk.empty += acc.sk.empty;
        sk.no_oi += acc.sk.no_oi;
        sk.no_funding += acc.sk.no_funding;
        e_secs[0] += acc.secs[0];
        e_secs[1] += acc.secs[1];
    }
    for v in &mut lens {
        v.sort_unstable();
    }
    let mut all: Vec<i64> = lens[0].iter().chain(&lens[1]).copied().collect();
    all.sort_unstable();
    rn.l_post = [
        lower_median(&all),
        lower_median(&lens[0]),
        lower_median(&lens[1]),
        lower_median(&lens[2]),
        lower_median(&lens[3]),
    ];

    // 3. основной проход
    let works = {
        let (rn, files, syms, pres) = (&rn, &files, &syms, &pres);
        par_map(
            threads,
            files.len(),
            |_| Work {
                agg: Agg::new(dims),
                fast: Fast::new(),
                lagbuf: Vec::new(),
                exits: rn.exits.then(|| ExitAgg::new(dims.nr, dims.ndays)),
                dumped: Vec::new(),
            },
            move |wk: &mut Work, i: usize| match &pres[i] {
                Some(pre) => process(rn, wk, (i, files[i].as_path(), syms[i].as_str()), pre),
                None => Ok(()),
            },
        )?
    };
    let mut it = works.into_iter();
    let first = it.next().context("нет потоков")?;
    let mut agg = first.agg;
    let mut ex_agg = first.exits;
    let mut dumps = first.dumped;
    for w in it {
        agg.merge(w.agg);
        if let (Some(a), Some(b)) = (ex_agg.as_mut(), w.exits) {
            a.merge(b);
        }
        dumps.extend(w.dumped);
    }

    // 4. запись
    let t = Instant::now();
    let st = rn.stage.name();
    let out = &a.out;
    write_daycells(&out.join(format!("daycells-{st}.csv")), &rn.lay, &agg)?;
    write_lags(&out.join(format!("lags-{st}.csv")), &rn.lay, &mut agg)?;
    write_first(&out.join(format!("first-{st}.csv")), &rn.lay, &agg)?;
    write_progress(&out.join(format!("progress-{st}.csv")), &rn.lay, &mut agg)?;
    write_episodes(
        &out.join(format!("episodes-{st}.csv")),
        &syms,
        &rn.xd,
        &mut agg,
    )?;
    write_xd(&out.join("xd.csv"), &rn.xd, &windows)?;
    if let Some(ex) = &ex_agg {
        exits::write_exits(&out.join(format!("exits-{st}.csv")), &rn.lay, ex)?;
        exits::write_grid(&out.join("exits-grid.csv"))?;
    }
    if rn.dump.is_some() {
        dump::write(&out.join(format!("triggers-{st}.csv")), &syms, dumps)?;
    }
    let secs_write = t.elapsed().as_secs_f64();

    let cnt: &Counters = &agg.cnt;
    let l_post: serde_json::Map<String, serde_json::Value> = STRATA
        .iter()
        .zip(rn.l_post)
        .map(|(s, v)| ((*s).to_string(), serde_json::Value::from(v)))
        .collect();
    // пары (эпизод | момент C2b, правило), не вошедшие в `n_ep` лаг-таблицы: признаки правила в окне не определены
    let mut observability = serde_json::Map::new();
    for (k, kname) in KINDS.iter().enumerate() {
        let by_stratum: serde_json::Map<String, serde_json::Value> = STRATA
            .iter()
            .enumerate()
            .map(|(s, sname)| {
                let [pairs, unobserved] = agg.obs[k * NS + s];
                (
                    (*sname).to_string(),
                    serde_json::json!({"pairs": pairs, "unobserved": unobserved}),
                )
            })
            .collect();
        observability.insert((*kname).to_string(), by_stratum.into());
    }
    let gap_by_h: serde_json::Map<String, serde_json::Value> = rn
        .lay
        .h_min
        .iter()
        .zip(&agg.gap_h)
        .map(|(h, n)| (h.to_string(), serde_json::Value::from(*n)))
        .collect();
    let (data_end_day, delisted) = sel.delisted();
    let epoch = NaiveDate::from_ymd_opt(1970, 1, 1).context("эпоха")?;
    let mut meta = serde_json::json!({
        "stage": st,
        "params_sha256": params_sha256,
        "seed": a.seed,
        "oi_lag_slots": a.oi_lag,
        "contracts": cnt.contracts,
        "symbol_days": cnt.symbol_days,
        "episodes": cnt.episodes,
        "triggers": cnt.triggers,
        "secs_total": t_all.elapsed().as_secs_f64(),
        "secs_by_phase": {
            "xd": secs_xd,
            "parse": cnt.secs[PH_PARSE] + e_secs[0],
            "episodes": cnt.secs[PH_EPISODES] + e_secs[1],
            "cube": cnt.secs[PH_CUBE],
            "controls": cnt.secs[PH_CONTROLS],
            "write": secs_write,
        },
        "peak_rss_mb": peak_rss_mb(),
        "git_head": std::env::var("GIT_HEAD").ok(),
        "threads": threads,
        "limit": a.limit,
        "files": files.len(),
        "rules": rn.lay.rules.len(),
        "xd_source": xd_source,
        "skipped": {
            "leverage": sk.leverage,
            "no_candles": sk.empty,
            "no_oi": sk.no_oi,
        },
        "contracts_without_funding": sk.no_funding,
        "universe": {
            "min_quote_turnover_usd_per_day": rn.min_turnover,
            "turnover_lag_days": rn.lag_days,
            "data_end_day": epoch
                .checked_add_signed(chrono::Duration::days(data_end_day))
                .map(|d| d.to_string()),
            "contracts_eligible": sel.ends.len(),
            "contracts_delisted": delisted,
            "episodes_period": sel.eps_period,
            "episodes_member": sel.eps_rule,
            "episodes_member_same_day": sel.eps_same_day,
            "episodes_lost_by_lag": sel.eps_lost,
            "episodes_gained_by_lag": sel.eps_gained,
        },
        "lag_observability": observability,
        "funding_coverage": {
            "trades": cnt.trades,
            "trades_uncovered": cnt.trades_funding_gap,
            "trades_uncovered_by_h": gap_by_h,
            "contracts_with_uncovered": cnt.contracts_funding_gap,
        },
        "btc_missing": btc_missing,
        "triggers_no_sign": cnt.triggers_no_sign,
        "c2b_moments": cnt.c2b_moments,
        "l_post_min": l_post,
    });
    if let Some(ex) = &ex_agg {
        meta["exits"] = ex.meta();
    }
    fs::write(
        out.join("run-meta.json"),
        serde_json::to_string_pretty(&meta)? + "\n",
    )?;
    println!(
        "run {st}: контрактов {}, суток во вселенной {}, эпизодов {} (теряется из-за d − {}: {}), снятых контрактов {}, срабатываний {}, {:.1} с",
        cnt.contracts,
        cnt.symbol_days,
        cnt.episodes,
        rn.lag_days,
        sel.eps_lost,
        delisted,
        cnt.triggers,
        t_all.elapsed().as_secs_f64()
    );
    if let Some(ex) = &ex_agg {
        println!(
            "exits {st}: {} конфигураций, срабатываний с направлением {}, сделок по cap {:?}, {:.1} с (сумма по потокам)",
            exits::NCFG,
            ex.cnt.triggers,
            ex.cnt.caps.iter().map(|c| c.trades).collect::<Vec<_>>(),
            ex.cnt.secs
        );
    }
    Ok(())
}
