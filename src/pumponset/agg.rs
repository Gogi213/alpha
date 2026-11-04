//! Потоковая агрегация прогона `run` и запись таблиц (спека: docs/findings/pumponset-run-spec-2026-11-05.md).
//!
//! Все накопители плотные (индекс по ключу арифметикой, без `HashMap`). Детерминизм при любом числе потоков:
//! суммы целочисленные (деньги — фиксированная точка `FIX`, не `f64`), значения для квантилей лежат списками
//! по ключу и сортируются при записи, слияние потоков — поэлементное сложение/конкатенация, так что порядок
//! слияния на итог не влияет; строки пишутся в порядке ключей.
//!
//! Страты (спека, `lags`): `all` (без FLAG_WICK и FLAG_OUTLIER), `market`/`nonmarket` (разбиение `all` по
//! FLAG_MARKET), `outlier` (FLAG_OUTLIER без FLAG_WICK), `wick`. Эпизод входит в одну («особую») страту и, если
//! он «чистый», ещё в `all`. Квантили — по отсортированным значениям, метод нижнего ранга `floor(q·(n−1))`.

#![allow(
    // индексы ограничены размерами плотных массивов, заданных `Layout`; бюджет линтов (TK-211) не растим
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_precision_loss
)]

use super::types::{Episode, Rule, XdTable, FLAG_MARKET, FLAG_OUTLIER, FLAG_WICK};
use anyhow::{Context, Result};
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::Path;

/// Фиксированная точка денег: 1e-9 bps. Сумма целых не зависит от порядка слагаемых.
pub const FIX: f64 = 1.0e9;

pub fn fix(x: f64) -> i64 {
    (x * FIX).round() as i64
}

pub fn unfix(v: i64) -> f64 {
    v as f64 / FIX
}

/// Число страт и их имена в порядке вывода.
pub const NS: usize = 5;
pub const STRATA: [&str; NS] = ["all", "market", "nonmarket", "outlier", "wick"];
pub const KINDS: [&str; 2] = ["ep", "c2b"];
/// Одиночные семейства для таблицы `first` (fam 0..=8; составные 9, 10 не участвуют).
pub const NSINGLE: usize = 9;
/// «Нет лага» в буфере лагов правил.
pub const NO_LAG: i32 = i32::MIN;

/// Страты эпизода по флагам: `(список, сколько)`; последняя в списке — «особая» (`market`/`nonmarket`/`outlier`/`wick`).
pub fn strata_of(flags: u8) -> ([usize; 2], usize) {
    if flags & FLAG_WICK != 0 {
        ([4, 0], 1)
    } else if flags & FLAG_OUTLIER != 0 {
        ([3, 0], 1)
    } else if flags & FLAG_MARKET != 0 {
        ([0, 1], 2)
    } else {
        ([0, 2], 2)
    }
}

/// Размеры осей накопителей.
#[derive(Debug, Clone, Copy)]
pub struct Dims {
    /// Правил в прогоне.
    pub nr: usize,
    /// Классов длительности `episodes.D_min`.
    pub nd: usize,
    /// Месяцев в периоде стадии.
    pub nm: usize,
    /// Суток в периоде стадии.
    pub ndays: usize,
    /// Значений `label_forward.H_min`.
    pub nh: usize,
    /// Окон и порогов (`features.windows_min`, `features.theta`).
    pub nw: usize,
    pub nt: usize,
}

/// Раскладка прогона: оси, правила, порядок вывода.
#[derive(Debug, Clone)]
pub struct Layout {
    pub stage: &'static str,
    pub dims: Dims,
    pub rules: Vec<Rule>,
    /// Позиции правил, отсортированные по `(fam, wi, ti)`.
    pub order: Vec<usize>,
    /// Первые сутки периода (дни от 1970-01-01) и месяц `yyyymm` каждых суток.
    pub day0: i64,
    pub day_month: Vec<i32>,
    /// Месяцы периода `yyyymm` по возрастанию.
    pub months: Vec<i32>,
    pub h_min: Vec<u32>,
    /// `first_map[wi · nt + ti]` — `(fam 0..=8, позиция правила)` по возрастанию `fam`.
    pub first_map: Vec<Vec<(u8, usize)>>,
}

impl Layout {
    pub fn new(
        stage: &'static str,
        dims: Dims,
        rules: Vec<Rule>,
        day0: i64,
        day_month: Vec<i32>,
        months: Vec<i32>,
        h_min: Vec<u32>,
    ) -> Layout {
        let mut order: Vec<usize> = (0..rules.len()).collect();
        order.sort_by_key(|&k| (rules[k].fam, rules[k].wi, rules[k].ti));
        let mut first_map: Vec<Vec<(u8, usize)>> = vec![Vec::new(); dims.nw * dims.nt];
        for &k in &order {
            let r = rules[k];
            if (r.fam as usize) < NSINGLE {
                first_map[r.wi as usize * dims.nt + r.ti as usize].push((r.fam, k));
            }
        }
        Layout {
            stage,
            dims,
            rules,
            order,
            day0,
            day_month,
            months,
            h_min,
            first_map,
        }
    }
}

/// Суточная ячейка (правило, сторона, H, сутки).
#[derive(Debug, Clone, Copy, Default)]
pub struct Cell {
    pub n: u32,
    pub hit: u32,
    pub bn: u32,
    pub bhit: u32,
    pub nret: u32,
    /// Суммы в `FIX`-единицах.
    pub gross: i64,
    pub fund: i64,
}

/// Строка таблицы эпизодов.
#[derive(Debug, Clone, Copy)]
pub struct EpRow {
    pub cidx: u32,
    pub di: u8,
    pub sign: i8,
    pub t0: i64,
    pub t_end: i64,
    pub mv: f64,
    pub flags: u8,
}

impl EpRow {
    pub fn new(cidx: usize, e: &Episode) -> EpRow {
        EpRow {
            cidx: cidx as u32,
            di: e.di,
            sign: e.sign,
            t0: e.t0,
            t_end: e.t_end,
            mv: e.mv,
            flags: e.flags,
        }
    }
}

/// Счётчики прогона (целые — входят в детерминированные проверки; `secs` — нет).
#[derive(Debug, Clone, Default)]
pub struct Counters {
    pub contracts: u64,
    pub symbol_days: u64,
    pub episodes: u64,
    pub triggers: u64,
    pub triggers_no_sign: u64,
    pub c2b_moments: u64,
    /// Секунды по фазам (сумма по потокам): разбор, эпизоды, куб, контроли.
    pub secs: [f64; 4],
}

pub const PH_PARSE: usize = 0;
pub const PH_EPISODES: usize = 1;
pub const PH_CUBE: usize = 2;
pub const PH_CONTROLS: usize = 3;

impl Counters {
    fn add(&mut self, o: &Counters) {
        self.contracts += o.contracts;
        self.symbol_days += o.symbol_days;
        self.episodes += o.episodes;
        self.triggers += o.triggers;
        self.triggers_no_sign += o.triggers_no_sign;
        self.c2b_moments += o.c2b_moments;
        for (a, b) in self.secs.iter_mut().zip(o.secs) {
            *a += b;
        }
    }
}

/// Накопители одного потока.
pub struct Agg {
    pub dims: Dims,
    /// `day[((ri · 2 + si) · nh + hi) · ndays + сутки]`, `si` = 0 памп / 1 дамп.
    pub day: Vec<Cell>,
    /// Группа лагов `g = (((kind · NS + страта) · 2 + si) · nd + di) · nm + месяц`; `lag_n[g]` — эпизодов (моментов).
    pub lag_n: Vec<u32>,
    /// Лаги сработавших, мин: `lag_v[g · nr + ri]`.
    pub lag_v: Vec<Vec<i16>>,
    /// Группа «первого»: `gf = ((((kind · NS + страта) · nw + wi) · nt + ti) · 2 + si) · nd + di`.
    pub first_n: Vec<u32>,
    pub first_c: Vec<u32>,
    /// Прогресс хода по эпизодам: `prog[g · nr + ri]` для `kind = 0` (три списка: done, left_share, to_peak).
    pub prog: Vec<[Vec<f32>; 3]>,
    pub eps: Vec<EpRow>,
    pub cnt: Counters,
}

impl Agg {
    pub fn new(d: Dims) -> Agg {
        let groups = 2 * NS * 2 * d.nd * d.nm;
        let nfirst = 2 * NS * d.nw * d.nt * 2 * d.nd;
        Agg {
            dims: d,
            day: vec![Cell::default(); d.nr * 2 * d.nh * d.ndays],
            lag_n: vec![0; groups],
            lag_v: vec![Vec::new(); groups * d.nr],
            first_n: vec![0; nfirst],
            first_c: vec![0; nfirst * NSINGLE],
            prog: (0..groups / 2 * d.nr)
                .map(|_| [Vec::new(), Vec::new(), Vec::new()])
                .collect(),
            eps: Vec::new(),
            cnt: Counters::default(),
        }
    }

    pub fn cell_mut(&mut self, ri: usize, si: usize, hi: usize, doff: usize) -> &mut Cell {
        let d = self.dims;
        &mut self.day[((ri * 2 + si) * d.nh + hi) * d.ndays + doff]
    }

    fn group(&self, kind: usize, stratum: usize, si: usize, di: usize, mi: usize) -> usize {
        let d = self.dims;
        (((kind * NS + stratum) * 2 + si) * d.nd + di) * d.nm + mi
    }

    /// Записать лаги всех правил одного эпизода/псевдо-момента в страту (`lags` — `NO_LAG` или лаг, мин) и
    /// «какое семейство первым» (спека `first`).
    #[allow(clippy::too_many_arguments)]
    pub fn record_lags(
        &mut self,
        lay: &Layout,
        kind: usize,
        stratum: usize,
        si: usize,
        di: usize,
        mi: usize,
        lags: &[i32],
    ) {
        let d = self.dims;
        let g = self.group(kind, stratum, si, di, mi);
        self.lag_n[g] += 1;
        for (ri, &l) in lags.iter().enumerate() {
            if l != NO_LAG {
                self.lag_v[g * d.nr + ri].push(l as i16);
            }
        }
        for wi in 0..d.nw {
            for ti in 0..d.nt {
                let mut best: Option<(i32, u8)> = None;
                for &(fam, ri) in &lay.first_map[wi * d.nt + ti] {
                    let l = lags[ri];
                    if l != NO_LAG && best.is_none_or(|(b, _)| l < b) {
                        best = Some((l, fam));
                    }
                }
                if let Some((_, fam)) = best {
                    let gf =
                        ((((kind * NS + stratum) * d.nw + wi) * d.nt + ti) * 2 + si) * d.nd + di;
                    self.first_n[gf] += 1;
                    self.first_c[gf * NSINGLE + fam as usize] += 1;
                }
            }
        }
    }

    pub fn push_progress(
        &mut self,
        stratum: usize,
        si: usize,
        di: usize,
        mi: usize,
        ri: usize,
        vals: [f32; 3],
    ) {
        let d = self.dims;
        let g = self.group(0, stratum, si, di, mi);
        let slot = &mut self.prog[g * d.nr + ri];
        for (v, x) in slot.iter_mut().zip(vals) {
            v.push(x);
        }
    }

    /// Слить накопитель другого потока (результат от порядка слияния не зависит).
    pub fn merge(&mut self, o: Agg) {
        for (a, b) in self.day.iter_mut().zip(&o.day) {
            a.n += b.n;
            a.hit += b.hit;
            a.bn += b.bn;
            a.bhit += b.bhit;
            a.nret += b.nret;
            a.gross += b.gross;
            a.fund += b.fund;
        }
        for (a, b) in self.lag_n.iter_mut().zip(&o.lag_n) {
            *a += b;
        }
        for (a, b) in self.lag_v.iter_mut().zip(o.lag_v) {
            if a.is_empty() {
                *a = b;
            } else {
                a.extend(b);
            }
        }
        for (a, b) in self.first_n.iter_mut().zip(&o.first_n) {
            *a += b;
        }
        for (a, b) in self.first_c.iter_mut().zip(&o.first_c) {
            *a += b;
        }
        for (a, b) in self.prog.iter_mut().zip(o.prog) {
            for (x, y) in a.iter_mut().zip(b) {
                if x.is_empty() {
                    *x = y;
                } else {
                    x.extend(y);
                }
            }
        }
        self.eps.extend(o.eps);
        self.cnt.add(&o.cnt);
    }
}

// ---------- запись таблиц ----------

fn create(path: &Path) -> Result<BufWriter<File>> {
    Ok(BufWriter::with_capacity(
        1 << 20,
        File::create(path).with_context(|| path.display().to_string())?,
    ))
}

/// Индекс квантиля `pct %` среди `n ≥ 1` отсортированных значений: `floor(pct · (n − 1) / 100)`.
pub fn q_index(n: usize, pct: usize) -> usize {
    pct * (n - 1) / 100
}

fn sgn(si: usize) -> i8 {
    if si == 0 {
        1
    } else {
        -1
    }
}

/// `daycells-<stage>.csv`.
pub fn write_daycells(path: &Path, lay: &Layout, agg: &Agg) -> Result<()> {
    let d = lay.dims;
    let mut w = create(path)?;
    writeln!(
        w,
        "fam,wi,ti,sign,hi,day,month,n,hit,bn,bhit,nret,gross_bps,fund_bps"
    )?;
    for &ri in &lay.order {
        let r = lay.rules[ri];
        for si in 0..2 {
            for hi in 0..d.nh {
                for doff in 0..d.ndays {
                    let c = &agg.day[((ri * 2 + si) * d.nh + hi) * d.ndays + doff];
                    if c.n == 0 && c.nret == 0 {
                        continue;
                    }
                    writeln!(
                        w,
                        "{},{},{},{},{},{},{},{},{},{},{},{},{},{}",
                        r.fam,
                        r.wi,
                        r.ti,
                        sgn(si),
                        hi,
                        lay.day0 + doff as i64,
                        lay.day_month[doff],
                        c.n,
                        c.hit,
                        c.bn,
                        c.bhit,
                        c.nret,
                        unfix(c.gross),
                        unfix(c.fund)
                    )?;
                }
            }
        }
    }
    w.flush()?;
    Ok(())
}

/// `lags-<stage>.csv` (сортирует списки лагов на месте).
pub fn write_lags(path: &Path, lay: &Layout, agg: &mut Agg) -> Result<()> {
    let d = lay.dims;
    let mut w = create(path)?;
    writeln!(
        w,
        "kind,stratum,fam,wi,ti,sign,di,month,n_ep,n_fired,n_lt0,n_le0,n_le30,n_le60,p10,p50,p90"
    )?;
    for (kind, kname) in KINDS.iter().enumerate() {
        for (s, sname) in STRATA.iter().enumerate() {
            for &ri in &lay.order {
                let r = lay.rules[ri];
                for si in 0..2 {
                    for di in 0..d.nd {
                        for mi in 0..d.nm {
                            let g = agg.group(kind, s, si, di, mi);
                            let n_ep = agg.lag_n[g];
                            if n_ep == 0 {
                                continue;
                            }
                            let v = &mut agg.lag_v[g * d.nr + ri];
                            v.sort_unstable();
                            let cnt = |x: i16| v.partition_point(|&l| l <= x);
                            let n = v.len();
                            let (lt0, le0, le30, le60) =
                                (v.partition_point(|&l| l < 0), cnt(0), cnt(30), cnt(60));
                            let q = |pct: usize| {
                                if n == 0 {
                                    String::new()
                                } else {
                                    v[q_index(n, pct)].to_string()
                                }
                            };
                            writeln!(
                                w,
                                "{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{}",
                                kname,
                                sname,
                                r.fam,
                                r.wi,
                                r.ti,
                                sgn(si),
                                di,
                                lay.months[mi],
                                n_ep,
                                n,
                                lt0,
                                le0,
                                le30,
                                le60,
                                q(10),
                                q(50),
                                q(90)
                            )?;
                        }
                    }
                }
            }
        }
    }
    w.flush()?;
    Ok(())
}

/// `first-<stage>.csv`.
pub fn write_first(path: &Path, lay: &Layout, agg: &Agg) -> Result<()> {
    let d = lay.dims;
    let mut w = create(path)?;
    writeln!(w, "kind,stratum,wi,ti,sign,di,fam,n_ep,n_first")?;
    for (kind, kname) in KINDS.iter().enumerate() {
        for (s, sname) in STRATA.iter().enumerate() {
            for wi in 0..d.nw {
                for ti in 0..d.nt {
                    for si in 0..2 {
                        for di in 0..d.nd {
                            let gf =
                                ((((kind * NS + s) * d.nw + wi) * d.nt + ti) * 2 + si) * d.nd + di;
                            let n_ep = agg.first_n[gf];
                            if n_ep == 0 {
                                continue;
                            }
                            for fam in 0..NSINGLE {
                                writeln!(
                                    w,
                                    "{},{},{},{},{},{},{},{},{}",
                                    kname,
                                    sname,
                                    wi,
                                    ti,
                                    sgn(si),
                                    di,
                                    fam,
                                    n_ep,
                                    agg.first_c[gf * NSINGLE + fam]
                                )?;
                            }
                        }
                    }
                }
            }
        }
    }
    w.flush()?;
    Ok(())
}

/// `progress-<stage>.csv` (сортирует списки на месте).
pub fn write_progress(path: &Path, lay: &Layout, agg: &mut Agg) -> Result<()> {
    let d = lay.dims;
    let mut w = create(path)?;
    writeln!(
        w,
        "stratum,fam,wi,ti,sign,di,month,n_fired,done_bps_p10,done_bps_p50,done_bps_p90,left_share_p10,left_share_p50,left_share_p90,to_peak_bps_p10,to_peak_bps_p50,to_peak_bps_p90"
    )?;
    for (s, sname) in STRATA.iter().enumerate() {
        for &ri in &lay.order {
            let r = lay.rules[ri];
            for si in 0..2 {
                for di in 0..d.nd {
                    for mi in 0..d.nm {
                        let g = agg.group(0, s, si, di, mi);
                        let slot = &mut agg.prog[g * d.nr + ri];
                        let n = slot[0].len();
                        if n == 0 {
                            continue;
                        }
                        let mut line = format!(
                            "{},{},{},{},{},{},{},{}",
                            sname,
                            r.fam,
                            r.wi,
                            r.ti,
                            sgn(si),
                            di,
                            lay.months[mi],
                            n
                        );
                        for col in slot.iter_mut() {
                            col.sort_unstable_by(f32::total_cmp);
                            for pct in [10, 50, 90] {
                                line.push(',');
                                line.push_str(&col[q_index(n, pct)].to_string());
                            }
                        }
                        writeln!(w, "{line}")?;
                    }
                }
            }
        }
    }
    w.flush()?;
    Ok(())
}

/// `episodes-<stage>.csv`: эпизоды вселенной и периода; порядок — по контрактам (алфавит), затем `(t0, di, знак)`.
pub fn write_episodes(path: &Path, syms: &[String], xd: &XdTable, agg: &mut Agg) -> Result<()> {
    agg.eps
        .sort_unstable_by_key(|r| (r.cidx, r.t0, r.di, r.sign));
    let mut w = create(path)?;
    writeln!(w, "sym,di,sign,t0,t_end,mv,flags,x_d")?;
    for r in &agg.eps {
        let x = xd.x[r.di as usize][usize::from(r.sign < 0)];
        writeln!(
            w,
            "{},{},{},{},{},{},{},{}",
            syms[r.cidx as usize], r.di, r.sign, r.t0, r.t_end, r.mv, r.flags, x
        )?;
    }
    w.flush()?;
    Ok(())
}

/// `xd.csv`: порог хода `X_D` (в `ln`) и число окон, из которых он получен.
pub fn write_xd(path: &Path, xd: &XdTable, windows: &[[u64; 2]]) -> Result<()> {
    let mut w = create(path)?;
    writeln!(w, "di,sign,x_d,n_windows")?;
    for (di, x) in xd.x.iter().enumerate() {
        for (si, v) in x.iter().enumerate() {
            let n = windows.get(di).map_or(0, |c| c[si]);
            writeln!(w, "{},{},{},{}", di, sgn(si), v, n)?;
        }
    }
    w.flush()?;
    Ok(())
}
