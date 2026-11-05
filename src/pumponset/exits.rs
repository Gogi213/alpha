//! Выходы в прибыль после раннего сигнала (П-35, `docs/research/P-35-exits.md`, TK-492): для каждого первого
//! срабатывания правила — симуляция всех 84 конфигураций выхода за ОДИН проход по минутам пути сделки.
//!
//! # Модель (П-35 «Что считаем»)
//! Вход — по `close_ff` минуты `m_in = t_f + 1` в сторону `sign` (тейкер). Минуты пути нумеруются от входа:
//! `j = k − m_in`, `pnl_j = sign·ln(c[m_in + j]/c[m_in])·1e4` bps, `j ≥ 1` (первая минута решения `k = t_f + 2`).
//! Решение принимается по close минуты `j`, исполнение — по `c[j + 1]` (задержка в одну минуту, внутриминутных
//! касаний нет); временной предел исполняется по `c[cap_j]` в запланированную минуту.
//!
//! # Допущения (то, чего протокол не называет)
//! 1. **Минута предела.** `cap` считается так же, как `H` в П-34: выход на минуте `t_f + cap`, то есть `cap_j = cap − 1`
//!    минут после входа. Так конфигурации «только `cap`» совпадают с `H = cap` из `daycells` до бита (проверяется
//!    тестом), а «выход через 6 ч» П-35 = `H = 360` П-34. Решение на минуте `j ≤ cap_j − 1` исполняется не позже
//!    `cap_j`; решение на минуте `cap_j` уже не принимается — предел приоритетнее (причина `cap`).
//! 2. **Набор сделок по `cap`.** Сделка входит в конфигурации с данным `cap` тогда и только тогда, когда её берёт
//!    `trade::round_trip(…, H = cap)`: есть цены входа и минуты предела, файл фандинга покрывает окно до `t_f + cap`
//!    (допущение 3 `trade.rs`). Покрытие до предела покрывает и ранний выход, так что внутри `cap` у всех конфигураций
//!    один и тот же набор сделок — они сравнимы между собой, а `n` базовой линии совпадает с `nret` из `daycells`.
//!    Из-за этого «по последней доступной цене» П-35 здесь не достижимо: цена минуты предела есть всегда.
//! 3. **Порядок при ничьей.** Первой минутой выхода выигрывает более раннее решение; в одну минуту стоп приоритетнее
//!    тейка и трейлинга (по close тейк и стоп в одну минуту невозможны — одновременно могут сработать стоп и трейлинг).
//! 4. **Трейлинг.** Активен с первой минуты, где `pnl_j ≥ R` (то есть `pnl_max ≥ R`), выход при `pnl_j ≤ pnl_max − R`;
//!    `pnl_max` — максимум по минутам `j ≥ 1` (вход `pnl = 0` при `R > 0` ничего не меняет).
//! 5. **Потолок задним числом (`mfe`).** `mfe_bps` — максимум `pnl_j` по `j ≥ 1` в окне самого длинного `cap`
//!    (`j ≤ 1439`, до конца данных, если ряд короче), `mfe_min` — минута максимума от входа (первая при равных);
//!    `mfe_cap_bps` — то же в окне собственного `cap` ячейки. Это не стратегия, а верхняя граница «сколько хода было».
//! 6. **Скорость.** Пороги сравниваются в пространстве цен (`v = sign·c`, пороги `v_in·e^{±уровень/1e4}`), а не через
//!    `ln` на каждой минуте; `ln` берётся только для цен исполнения (не более 15 на сделку) и максимума. Отличие от
//!    наивного `ln`-сравнения — лишь на границе в пределах точности `f64` (сверено тестом на случайных путях).
//!    Пороги первого касания считаются один раз на путь: у уровней тейка/стопа/трейлинга свои «первые минуты», а
//!    конфигурация берёт минимум по своим уровням и сроку `cap` (стоп и тейк разных уровней не пересчитываются).
//! 7. **Фиксированная точка** денег и общая детерминированность — как `agg.rs`: суммы целые (`FIX`), слияние потоков —
//!    поэлементное сложение, порядок слияния на итог не влияет.

#![allow(
    // индексы ограничены размерами плотных массивов, заданных сеткой; бюджет линтов (TK-211) не растим
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_precision_loss
)]

use super::agg::{fix, unfix, Layout};
use super::control::{sign_of, slot_t};
use super::run::day_of_min;
use super::trade::{self, Funding, Skip};
use super::types::Cube;
use anyhow::{Context, Result};
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::Path;

/// Перевод доли в bps — единица измерения, не параметр.
const BPS: f64 = 1.0e4;
/// «Минуты не было» в первых касаниях и «уровня нет» в конфигурации.
const NONE: u32 = u32::MAX;
const NONE8: u8 = u8::MAX;

// ---------- сетка П-35 (единственные числа сетки; расширений после просмотра нет) ----------

/// Временной предел, мин (как `H` П-34: выход на минуте `t_f + cap`).
pub const CAPS: [u32; 2] = [360, 1440];
/// Тейк-профит `G`, bps (семейство A).
pub const TP_LEVELS: [u32; 5] = [100, 200, 300, 500, 1000];
/// Стоп `S`, bps против позиции; объединение стопов семейств A и B.
pub const SL_LEVELS: [u32; 4] = [100, 200, 300, 500];
/// Трейлинг `R`, bps (семейство B); равен первым четырём уровням тейка — активация трейлинга = первая минута
/// касания тейка того же уровня.
pub const TRAIL_LEVELS: [u32; 4] = [100, 200, 300, 500];
pub const NCAP: usize = CAPS.len();
pub const NTP: usize = TP_LEVELS.len();
pub const NSL: usize = SL_LEVELS.len();
pub const NTR: usize = TRAIL_LEVELS.len();
/// «Нет стопа» (индекс в `SL_LEVELS`).
pub const NO_STOP: u8 = NONE8;
/// Стопы семейства A: 100, 200, 300, 500, нет.
const STOPS_A: [u8; 5] = [0, 1, 2, 3, NO_STOP];
/// Стопы семейства B: 100, 300, 500, нет.
const STOPS_B: [u8; 4] = [0, 2, 3, NO_STOP];
/// Конфигураций: базовая линия (по одной на `cap`) + A (`G × S × cap`) + B (`R × S × cap`) = 2 + 50 + 32.
pub const NCFG: usize = NCAP + NCAP * NTP * STOPS_A.len() + NCAP * NTR * STOPS_B.len();
/// Слотов результата выхода: по одному на уровень тейка, стопа, трейлинга и на каждый `cap`.
const NSLOT: usize = NTP + NSL + NTR + NCAP;

const _: () = {
    assert!(NCFG == 84);
    let mut i = 0;
    while i < NTR {
        assert!(TRAIL_LEVELS[i] == TP_LEVELS[i]);
        i += 1;
    }
};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Family {
    /// Только `cap`.
    Base,
    /// Тейк-профит `G` и стоп `S`.
    Tp,
    /// Трейлинг `R` и стоп `S`.
    Trail,
}

impl Family {
    pub fn name(self) -> &'static str {
        match self {
            Family::Base => "base",
            Family::Tp => "tp",
            Family::Trail => "trail",
        }
    }
}

/// Одна конфигурация выхода: индексы уровней (`NONE8` — уровня нет / не применим) и индекс `cap`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Cfg {
    pub family: Family,
    /// Индекс в `TP_LEVELS` (только семейство A).
    pub tp: u8,
    /// Индекс в `TRAIL_LEVELS` (только семейство B).
    pub trail: u8,
    /// Индекс в `SL_LEVELS`; `NO_STOP` — стопа нет.
    pub sl: u8,
    /// Индекс в `CAPS`.
    pub ci: u8,
}

impl Cfg {
    pub fn cap(&self) -> u32 {
        CAPS[usize::from(self.ci)]
    }
    pub fn g_bps(&self) -> Option<u32> {
        TP_LEVELS.get(usize::from(self.tp)).copied()
    }
    pub fn r_bps(&self) -> Option<u32> {
        TRAIL_LEVELS.get(usize::from(self.trail)).copied()
    }
    pub fn s_bps(&self) -> Option<u32> {
        SL_LEVELS.get(usize::from(self.sl)).copied()
    }
}

/// Сетка конфигураций в стабильной нумерации (номер = индекс): 0–1 базовая линия по `cap`; затем семейство A
/// циклами `G → S → cap`; затем семейство B циклами `R → S → cap`.
pub fn grid() -> Vec<Cfg> {
    let mut v = Vec::with_capacity(NCFG);
    for ci in 0..NCAP as u8 {
        v.push(Cfg {
            family: Family::Base,
            tp: NONE8,
            trail: NONE8,
            sl: NO_STOP,
            ci,
        });
    }
    for gi in 0..NTP as u8 {
        for &sl in &STOPS_A {
            for ci in 0..NCAP as u8 {
                v.push(Cfg {
                    family: Family::Tp,
                    tp: gi,
                    trail: NONE8,
                    sl,
                    ci,
                });
            }
        }
    }
    for ri in 0..NTR as u8 {
        for &sl in &STOPS_B {
            for ci in 0..NCAP as u8 {
                v.push(Cfg {
                    family: Family::Trail,
                    tp: NONE8,
                    trail: ri,
                    sl,
                    ci,
                });
            }
        }
    }
    v
}

/// `exits-grid.csv`: номер конфигурации и её параметры (`S = none` — стопа нет; `G`/`R` пусты там, где не применимы).
pub fn grid_csv() -> String {
    let opt = |x: Option<u32>| x.map_or(String::new(), |v| v.to_string());
    let mut s = String::from("cfg,family,G,S,R,cap\n");
    for (k, c) in grid().iter().enumerate() {
        let stop = match (c.family, c.s_bps()) {
            (Family::Base, _) => String::new(),
            (_, Some(v)) => v.to_string(),
            (_, None) => "none".to_string(),
        };
        s.push_str(&format!(
            "{},{},{},{},{},{}\n",
            k,
            c.family.name(),
            opt(c.g_bps()),
            stop,
            opt(c.r_bps()),
            c.cap()
        ));
    }
    s
}

// ---------- проход по минутам пути ----------

/// Множители порогов для стороны: `e^{±уровень/1e4}` по уровням тейка, стопа, трейлинга.
#[derive(Debug, Clone, Copy)]
pub struct Fac {
    tp: [f64; NTP],
    sl: [f64; NSL],
    tr: [f64; NTR],
}

impl Fac {
    /// `s` = ±1. Тейк: `v ≥ v_in·e^{+s·G}`; стоп: `v ≤ v_in·e^{−s·S}`; трейлинг: `v ≤ v_best·e^{−s·R}`,
    /// где `v = s·c` (знак переводит обе стороны в «больше — лучше»; умножение на ±1 точное).
    pub fn new(s: f64) -> Fac {
        let e = |lvl: u32, k: f64| (k * f64::from(lvl) / BPS).exp();
        Fac {
            tp: TP_LEVELS.map(|l| e(l, s)),
            sl: SL_LEVELS.map(|l| e(l, -s)),
            tr: TRAIL_LEVELS.map(|l| e(l, -s)),
        }
    }
}

/// Результат прохода: первые минуты решения по уровням (от входа, `NONE` — не было) и потолки.
#[derive(Debug, Clone, Copy)]
pub struct Scan {
    pub tp: [u32; NTP],
    pub sl: [u32; NSL],
    pub tr: [u32; NTR],
    /// Максимум `pnl` (bps) и его минута в окне каждого `cap` (`j ≤ cap − 1`); `NaN`, если окно не пройдено.
    pub mfe_cap: [(f64, u32); NCAP],
    /// То же по всему пройденному окну.
    pub mfe: (f64, u32),
}

/// Один проход по `cf[b + 1 ..= b + jmax]`: `cf[b]` — цена входа. Все пороги и максимум за один обход; тейки,
/// стопы и трейлинги упорядочены по уровню, поэтому «ждущий» уровень один (указатель) — на минуту в среднем два
/// сравнения. Подробности — шапка модуля, допущения 3–6.
pub fn scan(cf: &[f64], b: usize, jmax: usize, sign: i8, fac: &Fac) -> Scan {
    let s = f64::from(sign);
    let v_in = s * cf[b];
    let mut tp_thr = [f64::INFINITY; NTP];
    let mut sl_thr = [f64::NEG_INFINITY; NSL];
    for (t, f) in tp_thr.iter_mut().zip(&fac.tp) {
        *t = v_in * f;
    }
    for (t, f) in sl_thr.iter_mut().zip(&fac.sl) {
        *t = v_in * f;
    }
    let pnl = |v: f64| s * (v / v_in).ln() * BPS;
    let mut out = Scan {
        tp: [NONE; NTP],
        sl: [NONE; NSL],
        tr: [NONE; NTR],
        mfe_cap: [(f64::NAN, 0); NCAP],
        mfe: (f64::NAN, 0),
    };
    let (mut vbest, mut jbest) = (f64::NEG_INFINITY, 0u32);
    let (mut tpn, mut sln, mut trn) = (0usize, 0usize, 0usize);
    let mut j = 1usize;
    for (ci, &cap) in CAPS.iter().enumerate() {
        let end = (cap as usize - 1).min(jmax);
        if j <= end {
            for (d, &c) in cf[b + j..=b + end].iter().enumerate() {
                let jj = (j + d) as u32;
                let v = s * c;
                if v > vbest {
                    vbest = v;
                    jbest = jj;
                }
                while tpn < NTP && v >= tp_thr[tpn] {
                    out.tp[tpn] = jj;
                    tpn += 1;
                }
                while sln < NSL && v <= sl_thr[sln] {
                    out.sl[sln] = jj;
                    sln += 1;
                }
                // трейлинг уровня активен, когда тейк того же уровня уже коснулся; следующий по уровню не может
                // сработать раньше предыдущего (его просадка строже), поэтому ждёт один указатель
                while trn < tpn.min(NTR) && v <= vbest * fac.tr[trn] {
                    out.tr[trn] = jj;
                    trn += 1;
                }
            }
            j = end + 1;
        }
        if jmax + 1 >= cap as usize {
            out.mfe_cap[ci] = (pnl(vbest), jbest);
        }
    }
    out.mfe = (pnl(vbest), jbest);
    out
}

// ---------- накопители ----------

/// Сумма по ячейке (правило, сторона, сутки) для одной конфигурации; деньги — в `FIX`-единицах.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct CfgCell {
    pub gross: i64,
    pub fund: i64,
    /// Минут удержания от входа до выхода.
    pub hold: i64,
    pub n_tp: u32,
    pub n_sl: u32,
    pub n_trail: u32,
}

/// Сумма по ячейке и `cap`: число сделок (общее для всех конфигураций с этим `cap`) и потолки.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct CapCell {
    pub n: u32,
    pub mfe: i64,
    pub mfe_min: u64,
    pub mfe_cap: i64,
    pub mfe_cap_min: u64,
}

/// Сделки по `cap` для `run-meta.json`: взяты / нет покрытия фандинга / нет цен.
#[derive(Debug, Clone, Copy, Default)]
pub struct CapCnt {
    pub trades: u64,
    pub uncovered: u64,
    pub nodata: u64,
}

#[derive(Debug, Clone, Default)]
pub struct ExitCounters {
    /// Первые срабатывания с направлением (знак ≠ 0), прошедшие в симуляцию.
    pub triggers: u64,
    pub caps: [CapCnt; NCAP],
    /// Секунды симуляции (сумма по потокам).
    pub secs: f64,
}

/// Цены и фандинг контракта.
#[derive(Clone, Copy)]
pub struct Px<'a> {
    pub cf: &'a [f64],
    pub first_min: i64,
    pub funding: Funding<'a>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Reason {
    Tp,
    Sl,
    Trail,
    Cap,
}

/// Результат выхода в одной минуте исполнения (считается один раз на сделку и слот).
#[derive(Clone, Copy, Default)]
struct Slot {
    done: bool,
    gross: i64,
    fund: i64,
    hold: u32,
}

/// Накопитель одного потока. Раскладка: группа `g = (ri·2 + si)·ndays + сутки`; внутри группы 84 конфигурации
/// подряд (запись одной сделки идёт по соседней памяти).
pub struct ExitAgg {
    nr: usize,
    ndays: usize,
    grid: Vec<Cfg>,
    fac: [Fac; 2],
    cfgs: Vec<CfgCell>,
    caps: Vec<CapCell>,
    pub cnt: ExitCounters,
}

/// Контекст одной сделки для расчёта цены и фандинга исполнения.
struct Fill<'a> {
    px: &'a Px<'a>,
    b: usize,
    m_in: i64,
    c_in: f64,
    side: f64,
}

impl Fill<'_> {
    /// Результат выхода через `j` минут после входа: `(gross, fund)` в `FIX`-единицах — те же выражения, что в
    /// `trade::round_trip` (побитово равны при равном `j`).
    fn at(&self, j: u32) -> (i64, i64) {
        let c = self.px.cf[self.b + j as usize];
        let gross = self.side * (c / self.c_in).ln() * BPS;
        let mut sum = 0.0;
        for &(_, r) in self.px.funding.window(self.m_in, self.m_in + i64::from(j)) {
            sum += r;
        }
        (fix(gross), fix(self.side * sum * BPS))
    }
}

impl ExitAgg {
    pub fn new(nr: usize, ndays: usize) -> ExitAgg {
        let groups = nr * 2 * ndays;
        ExitAgg {
            nr,
            ndays,
            grid: grid(),
            fac: [Fac::new(1.0), Fac::new(-1.0)],
            cfgs: vec![CfgCell::default(); groups * NCFG],
            caps: vec![CapCell::default(); groups * NCAP],
            cnt: ExitCounters::default(),
        }
    }

    /// Группа ячеек (правило `ri`, сторона `si` = 0 памп / 1 дамп, сутки `doff` от начала периода стадии).
    pub fn group(&self, ri: usize, si: usize, doff: usize) -> usize {
        (ri * 2 + si) * self.ndays + doff
    }

    pub fn cfg_cell(&self, g: usize, cfg: usize) -> &CfgCell {
        &self.cfgs[g * NCFG + cfg]
    }

    pub fn cap_cell(&self, g: usize, ci: usize) -> &CapCell {
        &self.caps[g * NCAP + ci]
    }

    /// Все первые срабатывания контракта: `trig[ri]` — слоты куба правила с позицией `ri` в `lay.rules`. Знак — знак
    /// `r_w` окна правила в `t_f` (как в основном проходе); без направления сделки нет.
    pub fn add_triggers(&mut self, lay: &Layout, cube: &Cube, trig: &[Vec<usize>], px: &Px) {
        for (ri, slots) in trig.iter().enumerate() {
            let wi = lay.rules[ri].wi as usize;
            for &i in slots {
                let sign = sign_of(cube.rat(wi, i));
                if sign == 0 {
                    continue;
                }
                let t_f = slot_t(cube, i);
                let doff = (day_of_min(t_f) - lay.day0) as usize;
                let g = self.group(ri, usize::from(sign < 0), doff);
                self.add_trade(g, px, t_f, sign);
            }
        }
    }

    /// Одна сделка: проход по минутам, затем результат каждой из 84 конфигураций в группу `g`.
    pub fn add_trade(&mut self, g: usize, px: &Px, t_f: i64, sign: i8) {
        self.cnt.triggers += 1;
        let m_in = t_f + 1;
        // набор сделок по `cap` — как у `daycells` (допущение 2)
        let mut caps: [Option<trade::Trade>; NCAP] = [None; NCAP];
        let mut any = false;
        for (ci, &cap) in CAPS.iter().enumerate() {
            match trade::round_trip(px.cf, px.first_min, &px.funding, t_f, cap, sign) {
                Ok(t) => {
                    caps[ci] = Some(t);
                    self.cnt.caps[ci].trades += 1;
                    any = true;
                }
                Err(Skip::Funding) => self.cnt.caps[ci].uncovered += 1,
                Err(Skip::NoData) => self.cnt.caps[ci].nodata += 1,
            }
        }
        if !any {
            return;
        }
        // `round_trip` вернул сделку: минута входа внутри ряда, цена входа конечна и положительна, ряд не короче `cap`
        let b = (m_in - px.first_min) as usize;
        let jmax = (px.cf.len() - 1 - b).min(CAPS[NCAP - 1] as usize - 1);
        let sc = scan(px.cf, b, jmax, sign, &self.fac[usize::from(sign < 0)]);
        let fill = Fill {
            px,
            b,
            m_in,
            c_in: px.cf[b],
            side: f64::from(sign),
        };
        let mut slots = [Slot::default(); NSLOT];
        for (ci, t) in caps.iter().enumerate() {
            if let Some(t) = t {
                slots[NSLOT - NCAP + ci] = Slot {
                    done: true,
                    gross: fix(t.gross_bps),
                    fund: fix(t.fund_bps),
                    hold: CAPS[ci] - 1,
                };
            }
        }
        let base = g * NCFG;
        for (k, cfg) in self.grid.iter().enumerate() {
            let ci = usize::from(cfg.ci);
            if caps[ci].is_none() {
                continue;
            }
            // последняя минута решения: решение на `j` исполняется на `j + 1 ≤ cap_j`
            let last = CAPS[ci] - 2;
            let (mut t_ev, mut slot, mut why) = (NONE, NSLOT - NCAP + ci, Reason::Cap);
            if cfg.sl != NO_STOP {
                let t = sc.sl[usize::from(cfg.sl)];
                if t <= last {
                    (t_ev, slot, why) = (t, NTP + usize::from(cfg.sl), Reason::Sl);
                }
            }
            if cfg.tp != NONE8 {
                let t = sc.tp[usize::from(cfg.tp)];
                if t <= last && t < t_ev {
                    (t_ev, slot, why) = (t, usize::from(cfg.tp), Reason::Tp);
                }
            }
            if cfg.trail != NONE8 {
                let t = sc.tr[usize::from(cfg.trail)];
                if t <= last && t < t_ev {
                    (t_ev, slot, why) = (t, NTP + NSL + usize::from(cfg.trail), Reason::Trail);
                }
            }
            if !slots[slot].done {
                let (gross, fund) = fill.at(t_ev + 1);
                slots[slot] = Slot {
                    done: true,
                    gross,
                    fund,
                    hold: t_ev + 1,
                };
            }
            let s = slots[slot];
            let cell = &mut self.cfgs[base + k];
            cell.gross += s.gross;
            cell.fund += s.fund;
            cell.hold += i64::from(s.hold);
            match why {
                Reason::Tp => cell.n_tp += 1,
                Reason::Sl => cell.n_sl += 1,
                Reason::Trail => cell.n_trail += 1,
                Reason::Cap => {}
            }
        }
        for (ci, t) in caps.iter().enumerate() {
            if t.is_none() {
                continue;
            }
            debug_assert!(sc.mfe_cap[ci].0.is_finite() && sc.mfe.0.is_finite());
            let c = &mut self.caps[g * NCAP + ci];
            c.n += 1;
            c.mfe += fix(sc.mfe.0);
            c.mfe_min += u64::from(sc.mfe.1);
            c.mfe_cap += fix(sc.mfe_cap[ci].0);
            c.mfe_cap_min += u64::from(sc.mfe_cap[ci].1);
        }
    }

    /// Слить накопитель другого потока (результат от порядка слияния не зависит).
    pub fn merge(&mut self, o: ExitAgg) {
        for (a, b) in self.cfgs.iter_mut().zip(&o.cfgs) {
            a.gross += b.gross;
            a.fund += b.fund;
            a.hold += b.hold;
            a.n_tp += b.n_tp;
            a.n_sl += b.n_sl;
            a.n_trail += b.n_trail;
        }
        for (a, b) in self.caps.iter_mut().zip(&o.caps) {
            a.n += b.n;
            a.mfe += b.mfe;
            a.mfe_min += b.mfe_min;
            a.mfe_cap += b.mfe_cap;
            a.mfe_cap_min += b.mfe_cap_min;
        }
        self.cnt.triggers += o.cnt.triggers;
        for (a, b) in self.cnt.caps.iter_mut().zip(o.cnt.caps) {
            a.trades += b.trades;
            a.uncovered += b.uncovered;
            a.nodata += b.nodata;
        }
        self.cnt.secs += o.cnt.secs;
    }

    /// Блок `exits` для `run-meta.json`.
    pub fn meta(&self) -> serde_json::Value {
        let by_cap = |f: fn(&CapCnt) -> u64| -> serde_json::Map<String, serde_json::Value> {
            CAPS.iter()
                .zip(&self.cnt.caps)
                .map(|(c, n)| (c.to_string(), serde_json::Value::from(f(n))))
                .collect()
        };
        serde_json::json!({
            "configs": NCFG,
            "caps": CAPS,
            "rules": self.nr,
            "triggers": self.cnt.triggers,
            "trades_by_cap": by_cap(|n| n.trades),
            "uncovered_by_cap": by_cap(|n| n.uncovered),
            "nodata_by_cap": by_cap(|n| n.nodata),
            "secs": self.cnt.secs,
        })
    }
}

// ---------- запись ----------

const EXITS_HEAD: &str = "fam,wi,ti,sign,cfg,day,month,n,gross_bps_sum,fund_bps_sum,hold_min_sum,n_tp,n_sl,n_trail,n_cap,mfe_bps_sum,mfe_min_sum,mfe_cap_bps_sum,mfe_cap_min_sum";

/// Заголовок `exits-<stage>.csv` (читает `verdict`).
pub fn exits_head() -> &'static str {
    EXITS_HEAD
}

/// `exits-<stage>.csv`: суточные ячейки на (правило, сторона, конфигурация, сутки) с `n > 0`; строки по ключам
/// `(fam, wi, ti)`, сторона (+1 затем −1), конфигурация, сутки — вывод детерминирован.
pub fn write_exits(path: &Path, lay: &Layout, ex: &ExitAgg) -> Result<()> {
    let mut w = BufWriter::with_capacity(
        1 << 20,
        File::create(path).with_context(|| path.display().to_string())?,
    );
    writeln!(w, "{EXITS_HEAD}")?;
    for &ri in &lay.order {
        let r = lay.rules[ri];
        for si in 0..2 {
            for (k, cfg) in ex.grid.iter().enumerate() {
                for doff in 0..ex.ndays {
                    let g = ex.group(ri, si, doff);
                    let cap = ex.cap_cell(g, usize::from(cfg.ci));
                    if cap.n == 0 {
                        continue;
                    }
                    let c = ex.cfg_cell(g, k);
                    writeln!(
                        w,
                        "{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{}",
                        r.fam,
                        r.wi,
                        r.ti,
                        if si == 0 { 1 } else { -1 },
                        k,
                        lay.day0 + doff as i64,
                        lay.day_month[doff],
                        cap.n,
                        unfix(c.gross),
                        unfix(c.fund),
                        c.hold,
                        c.n_tp,
                        c.n_sl,
                        c.n_trail,
                        cap.n - c.n_tp - c.n_sl - c.n_trail,
                        unfix(cap.mfe),
                        cap.mfe_min,
                        unfix(cap.mfe_cap),
                        cap.mfe_cap_min
                    )?;
                }
            }
        }
    }
    w.flush()?;
    Ok(())
}

/// `exits-grid.csv`.
pub fn write_grid(path: &Path) -> Result<()> {
    std::fs::write(path, grid_csv()).with_context(|| path.display().to_string())
}
