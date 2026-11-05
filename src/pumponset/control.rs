//! Контроли и лаги раннего старта пампов (TK-492, П-34 §4, §5): лаги правил к эпизодам, метка вперёд,
//! контроль C2a (база точности по срабатываниям) и C2b (нулевая линия «заливки до» по эпизодам).
//! C1 отдельно не считается: это дополнение точности, агрегат по `TriggerRow::label` (П-34 §5).
//!
//! Время — как в `types.rs`: минута `m` и слот `s` (5 мин) от `GRID0_MS`; слот куба `i` ↔ минута
//! `5·(slot0 + i)`; `t0`/`t_end` эпизодов — абсолютные минуты (не кратны 5); `cf` (`close_ff`) индексируется
//! от `first_min` контракта.
//!
//! Детерминизм: зерно потока — `seed_for(зерно запуска, символ, метка)`, дальше поток на (правило, `t_f`) для
//! C2a и на (`t0`, `di`, знак эпизода) для C2b. Порядок контрактов, число потоков и список правил на
//! выбор моментов не влияют (проверено в `control_tests.rs`).
//!
//! # Допущения (то, чего протокол не называет)
//! 1. Дециль σ 1 м считается внутри монеты по её собственным значениям `cube.sigma` за весь ряд (в том числе
//!    «будущим» — это признак страты, а не сигнал): порезы `v[j·m/10]`, `j = 1..9`, по отсортированным
//!    конечным значениям, дециль = число порезов ≤ σ. `NaN` в σ — момент без дециля, в контроль не идёт.
//! 2. Месяц — календарный месяц UTC (`yyyymm`) минуты момента (для C2b — минуты `t0` эпизода и `t'`
//!    псевдо-момента, не `t − L_pre`).
//! 3. Выбор моментов без повторов (алгоритм Флойда); кандидатов не больше требуемого числа — берутся все.
//!    Срабатывание-источник (C2a) из кандидатов не исключается: контроль — «случайный момент режима».
//! 4. Кандидат C2a годен, если в его слоте есть дециль σ, `r_w` правила конечна и ненулевая (знак момента),
//!    и все признаки правила (`rule_features`) не `NaN` (дыра OI — «минуты исключаются», `types.rs`).
//!    Срабатывание со знаком 0 (`r_w` нулевая или `NaN`) остаётся строкой: все метки `NoData`, моментов нет.
//! 5. Метка вперёд `NoData`, если окно наибольшего `D ≤ H` не уместилось в данные (`t_f + D > последняя
//!    минута`), `c[t_f+1]` ещё `NaN` или знак 0. Решение только по индексам, не по ценам — иначе в хвосте
//!    данных остались бы одни попадания. Попадание считается лишь по полностью лежащим в данных окнам.
//!    `c2a_n` считает моменты с меткой `Hit`/`Miss` (`NoData` не входит), `c2a_drawn` — выбранные моменты.
//! 6. `X_D` в `XdTable` — положительные величины хода в `ln` для обоих знаков (для дампа — модуль).
//! 7. «Эпизод уже идёт в t_f» (`in_episode_running`) — эпизод (любой `D`) того же знака с `t0 < t_f ≤ t_end`.
//! 8. C2b: окно исключения `[t + a, t + b]` берётся из `controls.C2b.exclude_same_sign_episode_window_min`
//!    как есть (в протоколе `[-360, 1440]` = `[t − L_pre, t + 1440]`), по `t0` эпизодов того же знака из
//!    переданного списка (передавать полный, а не прореженный); знак псевдо-момента — знак эпизода; окно лага
//!    `[t' − L_pre, t' + L_post']` должно целиком лежать в кубе (`t' + L_post' ≤` последний слот);
//!    дециль эпизода и псевдо-момента — σ в слоте `⌊(t − L_pre)/5⌋`.
//! 9. Лаги не зависят от знака правила и эпизода (П-34 §4: «первый момент, где правило сработало»); окно
//!    лага за краем куба обрезается; в слоте с `NaN`-признаками правило не срабатывает (решает `feat::fires`).
//! 10. Порядок строк: `lags` — эпизод, затем правило; `c2a` — правило (как в списке), затем срабатывание;
//!     `c2b` — эпизод, затем момент в порядке выбора, затем правило.
//! 11. Цель `play-onset` (`c2a_moments_custom`, `play.rs`): метка вперёд — своя функция, а не ход `X_D`; из корзины
//!     (месяц, дециль σ) исключаются кандидаты зоны срабатывания `|m − t_f| ≤ zone` (`zone` = `L_quiet + max H`, задаёт
//!     вызывающий). Корзина отсортирована по слотам, поэтому зона — один отрезок, а выбор идёт по номерам оставшихся
//!     кандидатов тем же потоком `(правило, t_f)`; без зоны выбор и зерно те же, что в `c2a_moments_with`. Без зоны
//!     срабатывание-источник из своей корзины не исключается (допущение 3), с зоной уходит вместе с окрестностью: корзина
//!     может опустеть (`c2a_drawn == 0`, строка остаётся с меткой, моментов нет) или стать короче
//!     `moments_per_first_trigger`. Сколько таких срабатываний — считает вызывающий по `c2a_drawn`
//!     (`play::FilterCnt::controls`, `run-meta.json` → `play_onset`).

#![allow(
    // численное ядро: индексы ограничены проверенными длинами куба и корзин; бюджет линтов (TK-211) не растим
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_precision_loss
)]

use super::cache::SLOT_MIN;
use super::feat;
use super::params::Params;
use super::types::{Cube, Episode, LagRow, Rule, XdTable, NF};
use crate::statarb::panel::{GRID0_MS, MINUTE_MS};
use anyhow::{bail, Result};
use chrono::{DateTime, Datelike};

// ---------- генератор и зёрна ----------

const GAMMA: u64 = 0x9E37_79B9_7F4A_7C15;
const FNV_OFFSET: u64 = 0xCBF2_9CE4_8422_2325;
const FNV_PRIME: u64 = 0x100_0000_01B3;

/// Метки областей зерна (чтобы потоки C2a и C2b одного символа не совпадали) — структурные, не параметры.
pub(super) const TAG_C2A: u64 = 1;
pub(super) const TAG_C2B: u64 = 2;

fn fin(z: u64) -> u64 {
    let z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    let z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

/// Один шаг SplitMix64 от состояния `x` без хранения состояния — перемешивание ключей.
pub(super) fn mix64(x: u64) -> u64 {
    fin(x.wrapping_add(GAMMA))
}

/// SplitMix64: состояние 64 бита, период 2^64; одинаковое зерно — один и тот же поток.
#[derive(Debug, Clone)]
pub struct SplitMix64 {
    state: u64,
}

impl SplitMix64 {
    pub fn new(seed: u64) -> Self {
        Self { state: seed }
    }

    pub fn next_u64(&mut self) -> u64 {
        self.state = self.state.wrapping_add(GAMMA);
        fin(self.state)
    }

    /// Равномерно в `0..n` без смещения (Лемир); `n == 0` → 0.
    pub fn below(&mut self, n: u64) -> u64 {
        if n == 0 {
            return 0;
        }
        let mut m = u128::from(self.next_u64()) * u128::from(n);
        if (m as u64) < n {
            let t = n.wrapping_neg() % n;
            while (m as u64) < t {
                m = u128::from(self.next_u64()) * u128::from(n);
            }
        }
        (m >> 64) as u64
    }
}

/// FNV-1a 64 по байтам имени.
pub(super) fn fnv1a(s: &str) -> u64 {
    let mut h = FNV_OFFSET;
    for &b in s.as_bytes() {
        h ^= u64::from(b);
        h = h.wrapping_mul(FNV_PRIME);
    }
    h
}

/// Зерно контракта/правила/семейства контроля из (зерно запуска, символ, метка): от порядка обработки
/// контрактов и числа потоков не зависит.
pub fn seed_for(seed: u64, sym: &str, tag: u64) -> u64 {
    mix64(mix64(mix64(seed) ^ fnv1a(sym)) ^ tag)
}

/// Метка правила в зерне: область + (семейство, окно, порог).
pub(super) fn rule_tag(kind: u64, r: Rule) -> u64 {
    (kind << 32) | (u64::from(r.fam) << 16) | (u64::from(r.wi) << 8) | u64::from(r.ti)
}

/// Поток на ключ внутри зерна (момент срабатывания, эпизод).
pub(super) fn stream(seed: u64, key: u64) -> SplitMix64 {
    SplitMix64::new(mix64(seed ^ mix64(key)))
}

pub(super) fn ep_key(e: &Episode) -> u64 {
    mix64(e.t0 as u64) ^ mix64((u64::from(e.di) << 1) | u64::from(e.sign < 0))
}

/// `k` различных индексов из `0..n` (Флойд), в порядке выбора; `k ≥ n` — все `0..n` по порядку.
/// Квадратичная проверка повтора — `k` порядка десятка (`controls.*.moments_*`).
pub(super) fn pick(rng: &mut SplitMix64, n: usize, k: usize, out: &mut Vec<u32>) {
    out.clear();
    if k >= n {
        out.extend(0..(n as u32));
        return;
    }
    for j in (n - k)..n {
        let t = rng.below(j as u64 + 1) as u32;
        if out.contains(&t) {
            out.push(j as u32);
        } else {
            out.push(t);
        }
    }
}

// ---------- время, знак, страты ----------

/// Календарный месяц UTC минуты `min` (от `GRID0_MS`) в виде `yyyymm`.
pub fn yyyymm(min: i64) -> i32 {
    DateTime::from_timestamp_millis(GRID0_MS + min * MINUTE_MS)
        .map_or(0, |d| d.year() * 100 + d.month() as i32)
}

pub(super) fn slot_t(cube: &Cube, i: usize) -> i64 {
    SLOT_MIN * (cube.slot0 + i as i64)
}

pub(super) fn sign_of(r: f32) -> i8 {
    if r > 0.0 {
        1
    } else if r < 0.0 {
        -1
    } else {
        0
    }
}

fn ceil_div(a: i64, b: i64) -> i64 {
    -((-a).div_euclid(b))
}

/// Число страт σ — определение «дециль», не параметр.
pub(super) const DECILES: usize = 10;
pub(super) const NONE8: u8 = u8::MAX;
/// Число семейств правил (`Rule::fam`, состав — `feat::FAMILIES`).
pub(super) const NFAM: usize = feat::FAMILIES.len();

/// Признаки, нужные правилу семейства `fam` (`feat::FAMILIES`; неизвестное семейство — пусто).
pub(super) fn rule_features(fam: u8) -> &'static [usize] {
    feat::FAMILIES.get(fam as usize).copied().unwrap_or(&[])
}

/// Дециль σ по слотам (допущение 1); `NONE8` — σ не конечна.
fn deciles(sigma: &[f32]) -> Vec<u8> {
    let mut v: Vec<f32> = sigma.iter().copied().filter(|x| x.is_finite()).collect();
    v.sort_by(f32::total_cmp);
    let m = v.len();
    if m == 0 {
        return vec![NONE8; sigma.len()];
    }
    let cuts: Vec<f32> = (1..DECILES).map(|j| v[j * m / DECILES]).collect();
    sigma
        .iter()
        .map(|&x| {
            if x.is_finite() {
                cuts.partition_point(|&c| c <= x) as u8
            } else {
                NONE8
            }
        })
        .collect()
}

/// Страты слотов куба: плотный номер месяца (по возрастанию `yyyymm`) и дециль σ.
struct Strata {
    months: Vec<i32>,
    month_of: Vec<u16>,
    dec: Vec<u8>,
}

impl Strata {
    fn new(cube: &Cube) -> Strata {
        let mut months: Vec<i32> = Vec::new();
        let mut month_of = Vec::with_capacity(cube.n);
        for i in 0..cube.n {
            let ym = yyyymm(slot_t(cube, i));
            if months.last() != Some(&ym) {
                months.push(ym);
            }
            month_of.push((months.len() - 1) as u16);
        }
        Strata {
            months,
            month_of,
            dec: deciles(&cube.sigma),
        }
    }

    fn month_id(&self, ym: i32) -> Option<usize> {
        self.months.binary_search(&ym).ok()
    }

    /// Номер корзины (месяц, дециль) слота `i`; `None` — дециля нет.
    fn key(&self, i: usize) -> Option<usize> {
        let d = self.dec[i];
        (d != NONE8).then(|| self.month_of[i] as usize * DECILES + d as usize)
    }
}

/// Корзины слотов (CSR): `items[start[k]..start[k+1]]` — слоты корзины `k` по возрастанию.
struct Pool {
    start: Vec<u32>,
    items: Vec<u32>,
}

impl Pool {
    fn build(nb: usize, n: usize, key: impl Fn(usize) -> Option<usize>) -> Pool {
        let keys: Vec<u32> = (0..n)
            .map(|j| key(j).map_or(u32::MAX, |k| k as u32))
            .collect();
        let mut start = vec![0u32; nb + 1];
        for &k in &keys {
            if k != u32::MAX {
                start[k as usize + 1] += 1;
            }
        }
        let mut acc = 0u32;
        for s in &mut start {
            acc += *s;
            *s = acc;
        }
        let mut fill = start.clone();
        let mut items = vec![0u32; acc as usize];
        for (j, &k) in keys.iter().enumerate() {
            if k != u32::MAX {
                let f = &mut fill[k as usize];
                items[*f as usize] = j as u32;
                *f += 1;
            }
        }
        Pool { start, items }
    }

    fn bucket(&self, k: usize) -> &[u32] {
        &self.items[self.start[k] as usize..self.start[k + 1] as usize]
    }
}

// ---------- метка вперёд (§5, Н-3) ----------

/// Метка срабатывания/момента: попадание, промах или «нет данных» (допущение 5).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Label {
    Hit,
    Miss,
    NoData,
}

/// Максимум значений `H_min`: метки хранятся маской из двух бит (знак) на каждое `H` в `u64`.
const MAX_H: usize = 32;

fn di_of(p: &Params, d: u32) -> Option<usize> {
    p.episodes.d_min.iter().position(|&x| x == d)
}

/// Проверка параметров метки до счёта: каждое `D` метки есть в `episodes.D_min` (там задан `X_D`), у каждого
/// `H` есть хотя бы одно `D ≤ H`, число `H` помещается в маску.
pub fn check_label_d(p: &Params) -> Result<()> {
    let lf = &p.label_forward;
    if lf.h_min.len() > MAX_H {
        bail!("label_forward.H_min: не больше {MAX_H} значений");
    }
    for &d in &lf.d_min {
        if di_of(p, d).is_none() {
            bail!(
                "label_forward.D_min: {d} мин нет в episodes.D_min {:?} — X_D не задан",
                p.episodes.d_min
            );
        }
    }
    for &h in &lf.h_min {
        if !lf.d_min.iter().any(|&d| d <= h) {
            bail!(
                "label_forward.H_min: {h} мин меньше любого D из label_forward.D_min {:?}",
                lf.d_min
            );
        }
    }
    Ok(())
}

/// Метка одного момента по определению П-34 §5 (Н-3): существует `D ∈ label_forward.D_min`, `D ≤ h`, что
/// `max_{s ∈ [t_f+1, t_f+D]} знак·ln(c[s]/c[t_f+1]) ≥ X_D` (`X_D` по знаку). Одиночный вызов — `O(ΣD)`;
/// на всех моментах контракта — `Labels`. Параметры проверять `check_label_d` (без проверки `D` без `X_D`
/// пропускается).
pub fn label(
    cf: &[f64],
    first_min: i64,
    t_f: i64,
    sign: i8,
    xd: &XdTable,
    p: &Params,
    h: u32,
) -> Label {
    if sign == 0 {
        return Label::NoData;
    }
    let lf = &p.label_forward;
    let Some(dmax) = lf.d_min.iter().copied().filter(|&d| d <= h).max() else {
        return Label::NoData;
    };
    let b = t_f + 1 - first_min;
    if b < 0 || b as usize + dmax as usize > cf.len() || cf[b as usize].is_nan() {
        return Label::NoData;
    }
    let b = b as usize;
    let c_b = cf[b];
    for &d in lf.d_min.iter().filter(|&&d| d <= h) {
        let Some(di) = di_of(p, d) else { continue };
        let Some(&[x_up, x_dn]) = xd.x.get(di) else {
            continue;
        };
        let w = &cf[b..b + d as usize];
        let hit = if sign > 0 {
            (w.iter().copied().fold(f64::NEG_INFINITY, f64::max) / c_b).ln() >= x_up
        } else {
            (w.iter().copied().fold(f64::INFINITY, f64::min) / c_b).ln() <= -x_dn
        };
        if hit {
            return Label::Hit;
        }
    }
    Label::Miss
}

/// Попадание вперёд (`Label::Hit`); «нет данных» → `false` (отдельно — `label`).
pub fn hit(
    cf: &[f64],
    first_min: i64,
    t_f: i64,
    sign: i8,
    xd: &XdTable,
    p: &Params,
    h: u32,
) -> bool {
    label(cf, first_min, t_f, sign, xd, p, h) == Label::Hit
}

/// Монотонная очередь индексов (экстремум окна за `O(1)` амортизированно).
#[derive(Default)]
struct Mono {
    q: Vec<u32>,
    head: usize,
}

impl Mono {
    fn reset(&mut self) {
        self.q.clear();
        self.head = 0;
    }

    fn push(&mut self, c: &[f64], e: usize, is_max: bool) {
        while self.q.len() > self.head {
            let last = self.q[self.q.len() - 1] as usize;
            let covered = if is_max {
                c[last] <= c[e]
            } else {
                c[last] >= c[e]
            };
            if !covered {
                break;
            }
            self.q.pop();
        }
        self.q.push(e as u32);
    }

    /// Индекс экстремума в окне, начинающемся с `b` (`b ≤` последнего добавленного).
    fn front(&mut self, b: usize) -> usize {
        while (self.q[self.head] as usize) < b {
            self.head += 1;
        }
        self.q[self.head] as usize
    }
}

/// Метки всех моментов контракта сразу: для каждого `D` метки скользящие max/min окна `[b, b+D−1]` (монотонная
/// очередь, `O(n)` на `D`), `b` = индекс `c[t_f+1]`. Хранит бит попадания на (минута, `H`, знак).
pub struct Labels {
    first_min: i64,
    n: usize,
    /// Первая минута с конечным `close_ff`.
    valid_from: usize,
    /// Наибольшее `D ≤ H[hi]`: окно меток целиком в данных, иначе `NoData`.
    dmax: Vec<u32>,
    /// `hits[b]`: бит `2·hi + (знак < 0)` — попадание.
    hits: Vec<u64>,
}

impl Labels {
    /// `cf` — `close_ff` контракта (`NaN` только в начале); `X_D` — положительные для нужных `D`.
    pub fn new(cf: &[f64], first_min: i64, xd: &XdTable, p: &Params) -> Result<Labels> {
        check_label_d(p)?;
        let lf = &p.label_forward;
        let n = cf.len();
        let v0 = cf.iter().position(|x| !x.is_nan()).unwrap_or(n);
        if cf[v0..].iter().any(|x| !(x.is_finite() && *x > 0.0)) {
            bail!("cf: после первого close есть NaN или неположительное значение — нужен close_ff");
        }
        let mut dmax = Vec::with_capacity(lf.h_min.len());
        for &h in &lf.h_min {
            dmax.push(
                lf.d_min
                    .iter()
                    .copied()
                    .filter(|&d| d <= h)
                    .max()
                    .unwrap_or(0),
            );
        }
        let mut hits = vec![0u64; n];
        let (mut qmax, mut qmin) = (Mono::default(), Mono::default());
        for &d in &lf.d_min {
            let Some(di) = di_of(p, d) else { continue };
            let Some(&[x_up, x_dn]) = xd.x.get(di) else {
                bail!("XdTable: нет строки для D = {d} (индекс {di})");
            };
            if !(x_up.is_finite() && x_up > 0.0 && x_dn.is_finite() && x_dn > 0.0) {
                bail!("XdTable: X_D для D = {d} должен быть конечным и > 0");
            }
            let (mut mask_up, mut mask_dn) = (0u64, 0u64);
            for (hi, &h) in lf.h_min.iter().enumerate() {
                if d <= h {
                    mask_up |= 1u64 << (2 * hi);
                    mask_dn |= 1u64 << (2 * hi + 1);
                }
            }
            let dd = d as usize;
            qmax.reset();
            qmin.reset();
            for e in v0..n {
                qmax.push(cf, e, true);
                qmin.push(cf, e, false);
                if e + 1 < v0 + dd {
                    continue;
                }
                let b = e + 1 - dd;
                let c_b = cf[b];
                if (cf[qmax.front(b)] / c_b).ln() >= x_up {
                    hits[b] |= mask_up;
                }
                if (cf[qmin.front(b)] / c_b).ln() <= -x_dn {
                    hits[b] |= mask_dn;
                }
            }
        }
        Ok(Labels {
            first_min,
            n,
            valid_from: v0,
            dmax,
            hits,
        })
    }

    /// Метка момента `t_f` (абсолютная минута) для знака и `H[hi]`.
    pub fn get(&self, t_f: i64, sign: i8, hi: usize) -> Label {
        if sign == 0 || hi >= self.dmax.len() {
            return Label::NoData;
        }
        let b = t_f + 1 - self.first_min;
        if b < 0 {
            return Label::NoData;
        }
        let b = b as usize;
        if b < self.valid_from || b + self.dmax[hi] as usize > self.n {
            return Label::NoData;
        }
        if (self.hits[b] >> (2 * hi + usize::from(sign < 0))) & 1 == 1 {
            Label::Hit
        } else {
            Label::Miss
        }
    }
}

// ---------- лаги (§4) ----------

/// Окно минут `[t_lo, t_hi]` → индексы слотов куба `[lo, hi]` (границы включены, за краем куба обрезаются).
fn slot_range(cube: &Cube, t_lo: i64, t_hi: i64) -> Option<(usize, usize)> {
    let lo = (ceil_div(t_lo, SLOT_MIN) - cube.slot0).max(0);
    let hi = (t_hi.div_euclid(SLOT_MIN) - cube.slot0).min(cube.n as i64 - 1);
    (lo <= hi).then_some((lo as usize, hi as usize))
}

/// Первое срабатывание в `[t0 − l_pre, t0 + l_post]` → `t_f − t0`.
fn first_lag(
    cube: &Cube,
    t0: i64,
    l_pre: i64,
    l_post: i64,
    mut fires: impl FnMut(usize) -> bool,
) -> Option<i64> {
    let (lo, hi) = slot_range(cube, t0 - l_pre, t0 + l_post)?;
    (lo..=hi).find(|&i| fires(i)).map(|i| slot_t(cube, i) - t0)
}

/// Лаги правил к эпизодам (П-34 §4): для каждого эпизода и правила `lag = t_f − t0` первого срабатывания в
/// `[t0 − L_pre, t0 + L_post]`, `L_post = t_end − t0` или `l_post_override` (псевдо-моменты C2b); `None` —
/// не сработало. Порядок: эпизод, затем правило.
pub fn lags(
    cube: &Cube,
    eps: &[Episode],
    rules: &[Rule],
    p: &Params,
    l_post_override: Option<i64>,
) -> Vec<LagRow> {
    lags_with(cube, eps, rules, p, l_post_override, |r, i| {
        feat::fires(cube, r, p, i)
    })
}

/// `lags` с заданным предикатом срабатывания `fires(правило, индекс слота куба)`.
pub fn lags_with<F>(
    cube: &Cube,
    eps: &[Episode],
    rules: &[Rule],
    p: &Params,
    l_post_override: Option<i64>,
    mut fires: F,
) -> Vec<LagRow>
where
    F: FnMut(Rule, usize) -> bool,
{
    let l_pre = i64::from(p.onset.l_pre_min);
    let mut out = Vec::with_capacity(eps.len() * rules.len());
    for (ei, e) in eps.iter().enumerate() {
        let l_post = l_post_override.unwrap_or(e.t_end - e.t0);
        for &rule in rules {
            let lag = first_lag(cube, e.t0, l_pre, l_post, |i| fires(rule, i));
            out.push(LagRow {
                rule,
                episode: ei as u32,
                lag,
            });
        }
    }
    out
}

// ---------- вход контракта, C2a ----------

/// Вход одного контракта для контролей.
#[derive(Debug, Clone, Copy)]
pub struct Input<'a> {
    pub sym: &'a str,
    pub cube: &'a Cube,
    /// `Contract::close_ff()`, индекс 0 ↔ минута `first_min`.
    pub cf: &'a [f64],
    pub first_min: i64,
    /// Эпизоды контракта (все `D`, без прореживания).
    pub eps: &'a [Episode],
}

/// Строка на первое срабатывание правила (C2a и метки для C1, П-34 §5).
#[derive(Debug, Clone, PartialEq)]
pub struct TriggerRow {
    pub rule: Rule,
    /// Минута срабатывания `5·(slot0 + i)`.
    pub t_f: i64,
    /// Календарный месяц UTC `yyyymm`.
    pub month: i32,
    /// Знак сигнала = знак `r_w` окна правила в `t_f` (0 — направления нет).
    pub sign: i8,
    /// Метка срабатывания по `H_min[hi]`.
    pub label: Vec<Label>,
    /// Попадания среди моментов C2a по `H_min[hi]` (среди `c2a_n`).
    pub c2a_hit: Vec<u16>,
    /// Моменты C2a с меткой `Hit`/`Miss` по `H_min[hi]`.
    pub c2a_n: Vec<u16>,
    /// Выбранные моменты C2a (≤ `moments_per_first_trigger`; меньше — не хватило кандидатов в страте).
    pub c2a_drawn: u16,
    /// Описательная колонка: эпизод того же знака уже идёт в `t_f`; в вердикт не входит.
    pub in_episode_running: bool,
}

fn check_cube(cube: &Cube) -> Result<()> {
    let nw = cube.windows.len();
    if cube.sigma.len() != cube.n || cube.r.len() != nw * cube.n || cube.z.len() != NF * nw * cube.n
    {
        bail!(
            "куб: n = {}, окон {nw}, но z = {}, r = {}, sigma = {}",
            cube.n,
            cube.z.len(),
            cube.r.len(),
            cube.sigma.len()
        );
    }
    Ok(())
}

fn check_rule(rule: Rule, nw: usize) -> Result<()> {
    if rule.fam as usize >= NFAM || rule.wi as usize >= nw {
        bail!("правило {rule:?} вне куба (семейств {NFAM}, окон {nw})");
    }
    Ok(())
}

/// Слот `j` годен кандидатом C2a для правила (допущение 4): корзина (месяц, дециль) или `None`.
fn cand_ok(cube: &Cube, st: &Strata, fam: u8, wi: usize, j: usize) -> Option<usize> {
    let key = st.key(j)?;
    if sign_of(cube.rat(wi, j)) == 0 {
        return None;
    }
    if rule_features(fam)
        .iter()
        .any(|&f| cube.zat(f, wi, j).is_nan())
    {
        return None;
    }
    Some(key)
}

/// Маска «эпизод того же знака уже идёт»: `t0 < 5·s ≤ t_end` по слотам куба, [памп, дамп].
pub(super) fn running_mask(cube: &Cube, eps: &[Episode]) -> [Vec<bool>; 2] {
    let mut m = [vec![false; cube.n], vec![false; cube.n]];
    for e in eps {
        if e.sign == 0 {
            continue;
        }
        let si = usize::from(e.sign < 0);
        let lo = (e.t0.div_euclid(SLOT_MIN) + 1 - cube.slot0).max(0);
        let hi = (e.t_end.div_euclid(SLOT_MIN) - cube.slot0).min(cube.n as i64 - 1);
        for s in lo..=hi {
            m[si][s as usize] = true;
        }
    }
    m
}

/// C2a (П-34 §5, Р-2): для каждого первого срабатывания каждого правила — до `moments_per_first_trigger`
/// случайных моментов той же монеты, месяца и дециля σ 1 м, без исключений по эпизодам; знак момента — знак
/// `r_w` окна правила; метка вперёд моментов по каждому `H_min`. `first_triggers` — `feat::first_triggers`.
pub fn c2a(
    inp: &Input,
    rules: &[Rule],
    xd: &XdTable,
    p: &Params,
    seed: u64,
) -> Result<Vec<TriggerRow>> {
    let mut out = Vec::new();
    c2a_with(
        inp,
        rules,
        xd,
        p,
        seed,
        |r| feat::first_triggers(inp.cube, r, p),
        |row| out.push(row),
    )?;
    Ok(out)
}

/// `c2a` с заданным источником первых срабатываний (индексы слотов куба) и приёмником строк (поток вместо
/// накопления — вызывающий агрегирует по ходу).
pub fn c2a_with<T, S>(
    inp: &Input,
    rules: &[Rule],
    xd: &XdTable,
    p: &Params,
    seed: u64,
    first_triggers: T,
    mut sink: S,
) -> Result<()>
where
    T: FnMut(Rule) -> Vec<usize>,
    S: FnMut(TriggerRow),
{
    c2a_moments_with(inp, rules, xd, p, seed, false, first_triggers, |row, _| {
        sink(row);
    })
}

/// `c2a_with`, у которого приёмник получает ещё слоты куба выбранных моментов C2a (`moments = true`; иначе срез
/// пуст — вызов обычного прогона ничего лишнего не строит). Строки, зерно и порядок те же, что у `c2a_with`.
#[allow(clippy::too_many_arguments)]
pub fn c2a_moments_with<T, S>(
    inp: &Input,
    rules: &[Rule],
    xd: &XdTable,
    p: &Params,
    seed: u64,
    moments: bool,
    first_triggers: T,
    sink: S,
) -> Result<()>
where
    T: FnMut(Rule) -> Vec<usize>,
    S: FnMut(TriggerRow, &[u32]),
{
    c2a_core(
        inp,
        rules,
        p,
        seed,
        moments,
        &LabelSrc::Move(xd),
        None,
        first_triggers,
        sink,
    )
    .map(|_| ())
}

/// `c2a_moments_with` со своей меткой `метка(минута, знак, индекс H)` вместо хода класса `D` (цель `play-onset`, `play.rs`)
/// и зоной исключения вокруг срабатывания: кандидат `m` пула годен только при `|m − t_f| > zone` (окно `[m − L, m + H]`
/// не пересекает окно срабатывания). Возвращает число кандидатов пула, попавших в зону (сумма по срабатываниям). Выбор
/// из оставшихся — тот же, что без зоны, с тем же зерном потока на `(правило, t_f)`.
#[allow(clippy::too_many_arguments)]
pub fn c2a_moments_custom<T, S>(
    inp: &Input,
    rules: &[Rule],
    p: &Params,
    seed: u64,
    moments: bool,
    zone: i64,
    label: &dyn Fn(i64, i8, usize) -> Label,
    first_triggers: T,
    sink: S,
) -> Result<u64>
where
    T: FnMut(Rule) -> Vec<usize>,
    S: FnMut(TriggerRow, &[u32]),
{
    c2a_core(
        inp,
        rules,
        p,
        seed,
        moments,
        &LabelSrc::Custom(label),
        Some(zone),
        first_triggers,
        sink,
    )
}

/// Откуда берётся метка вперёд момента в `c2a_core`.
enum LabelSrc<'a> {
    /// Ход класса `D` не меньше `X_D` по ценам контракта (`Labels`, П-34 §5).
    Move(&'a XdTable),
    /// Своя метка (`минута, знак, индекс H`).
    Custom(&'a dyn Fn(i64, i8, usize) -> Label),
}

/// Метки, готовые к чтению (`LabelSrc::Move` строит `Labels` по ценам один раз на контракт).
enum LabelGet<'a> {
    Move(Labels),
    Custom(&'a dyn Fn(i64, i8, usize) -> Label),
}

impl LabelGet<'_> {
    fn get(&self, t: i64, sign: i8, hi: usize) -> Label {
        match self {
            LabelGet::Move(l) => l.get(t, sign, hi),
            LabelGet::Custom(f) => f(t, sign, hi),
        }
    }
}

/// Общее тело `c2a_moments_with` и `c2a_moments_custom`; `zone = None` — прежний выбор из всей корзины.
#[allow(clippy::too_many_arguments)]
fn c2a_core<T, S>(
    inp: &Input,
    rules: &[Rule],
    p: &Params,
    seed: u64,
    moments: bool,
    src: &LabelSrc,
    zone: Option<i64>,
    mut first_triggers: T,
    mut sink: S,
) -> Result<u64>
where
    T: FnMut(Rule) -> Vec<usize>,
    S: FnMut(TriggerRow, &[u32]),
{
    let cube = inp.cube;
    check_cube(cube)?;
    let k_want = p.controls.c2a.moments_per_first_trigger as usize;
    if k_want > usize::from(u16::MAX) {
        bail!(
            "controls.C2a.moments_per_first_trigger: не больше {}",
            u16::MAX
        );
    }
    let labels = match src {
        LabelSrc::Move(xd) => LabelGet::Move(Labels::new(inp.cf, inp.first_min, xd, p)?),
        LabelSrc::Custom(f) => LabelGet::Custom(*f),
    };
    let mut dropped = 0u64;
    let nh = p.label_forward.h_min.len();
    let nw = cube.windows.len();
    let st = Strata::new(cube);
    let nb = st.months.len() * DECILES;
    let running = running_mask(cube, inp.eps);
    let mut pools: Vec<Option<Pool>> = (0..NFAM * nw).map(|_| None).collect();
    let mut buf: Vec<u32> = Vec::with_capacity(k_want);
    let mut moms: Vec<u32> = Vec::new();
    for &rule in rules {
        check_rule(rule, nw)?;
        let wi = rule.wi as usize;
        let trig = first_triggers(rule);
        if trig.is_empty() {
            continue;
        }
        let pool: &Pool = pools[rule.fam as usize * nw + wi].get_or_insert_with(|| {
            Pool::build(nb, cube.n, |j| cand_ok(cube, &st, rule.fam, wi, j))
        });
        let rseed = seed_for(seed, inp.sym, rule_tag(TAG_C2A, rule));
        for i in trig {
            if i >= cube.n {
                bail!("first_triggers: слот {i} за кубом (n = {})", cube.n);
            }
            let t_f = slot_t(cube, i);
            let sign = sign_of(cube.rat(wi, i));
            let mut n_hit = vec![0u16; nh];
            let mut n_all = vec![0u16; nh];
            let mut drawn = 0u16;
            moms.clear();
            if sign != 0 {
                if let Some(key) = st.key(i) {
                    let bucket = pool.bucket(key);
                    // зона срабатывания: корзина отсортирована по слотам, исключённое — один непрерывный отрезок
                    // `[cut_lo, cut_hi)`; кандидаты — `bucket[..cut_lo]` и `bucket[cut_hi..]` (без зоны оба — `len`)
                    let (cut_lo, cut_hi) = zone.map_or((bucket.len(), bucket.len()), |z| {
                        (
                            bucket.partition_point(|&j| slot_t(cube, j as usize) < t_f - z),
                            bucket.partition_point(|&j| slot_t(cube, j as usize) <= t_f + z),
                        )
                    });
                    dropped += (cut_hi - cut_lo) as u64;
                    let avail = cut_lo + (bucket.len() - cut_hi);
                    let k = avail.min(k_want);
                    pick(&mut stream(rseed, t_f as u64), avail, k, &mut buf);
                    drawn = k as u16;
                    for &q in &buf {
                        let q = q as usize;
                        let j = (if q < cut_lo {
                            bucket[q]
                        } else {
                            bucket[cut_hi + (q - cut_lo)]
                        }) as usize;
                        if moments {
                            moms.push(j as u32);
                        }
                        let (tj, sj) = (slot_t(cube, j), sign_of(cube.rat(wi, j)));
                        for hi in 0..nh {
                            match labels.get(tj, sj, hi) {
                                Label::Hit => {
                                    n_hit[hi] += 1;
                                    n_all[hi] += 1;
                                }
                                Label::Miss => n_all[hi] += 1,
                                Label::NoData => {}
                            }
                        }
                    }
                }
            }
            sink(
                TriggerRow {
                    rule,
                    t_f,
                    month: yyyymm(t_f),
                    sign,
                    label: (0..nh).map(|hi| labels.get(t_f, sign, hi)).collect(),
                    c2a_hit: n_hit,
                    c2a_n: n_all,
                    c2a_drawn: drawn,
                    in_episode_running: sign != 0 && running[usize::from(sign < 0)][i],
                },
                &moms,
            );
        }
    }
    Ok(dropped)
}

// ---------- C2b ----------

/// Строка C2b: лаг правила для псевдо-момента эпизода (нулевая линия §4, база для (а) §5).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct C2bRow {
    pub rule: Rule,
    /// Индекс эпизода в переданном списке.
    pub episode_idx: u32,
    /// Псевдо-момент: минута `5·(slot0 + j)`.
    pub t_pseudo: i64,
    /// `t_f − t'` первого срабатывания в `[t' − L_pre, t' + L_post']`; `None` — не сработало.
    pub lag: Option<i64>,
}

fn excluded(starts: &[i64], lo: i64, hi: i64) -> bool {
    let k = starts.partition_point(|&x| x < lo);
    k < starts.len() && starts[k] <= hi
}

/// C2b (П-34 §5): на каждый эпизод до `moments_per_episode` псевдо-моментов той же монеты и месяца, дециль σ
/// «до» (`t − L_pre`) как у эпизода, без эпизода того же знака в `[t + a, t + b]`; для каждого — лаги всех
/// правил в окне `[t' − L_pre, t' + l_post]`, `l_post` — медиана длины фазы эпизодов (передаёт вызывающий).
pub fn c2b(inp: &Input, rules: &[Rule], p: &Params, l_post: i64, seed: u64) -> Result<Vec<C2bRow>> {
    let mut out = Vec::new();
    c2b_with(
        inp,
        rules,
        p,
        l_post,
        seed,
        |r, i| feat::fires(inp.cube, r, p, i),
        |row| out.push(row),
    )?;
    Ok(out)
}

/// `c2b` с заданным предикатом срабатывания и приёмником строк.
pub fn c2b_with<F, S>(
    inp: &Input,
    rules: &[Rule],
    p: &Params,
    l_post: i64,
    seed: u64,
    fires: F,
    sink: S,
) -> Result<()>
where
    F: FnMut(Rule, usize) -> bool,
    S: FnMut(C2bRow),
{
    c2b_with_cand(inp, rules, p, l_post, seed, |_, _| true, fires, sink)
}

/// `c2b_with` с дополнительным условием на кандидата: `cand(слот куба, знак эпизода)` — псевдо-момент берётся только
/// из слотов, где оно истинно (выборка «монет в игре», `play.rs`: контроль из той же популяции, что срабатывания).
/// Условие входит в состав пула до выбора, а не отбрасывает уже выбранные моменты; `|_, _| true` — прежний `c2b_with`.
#[allow(clippy::too_many_arguments)]
pub fn c2b_with_cand<C, F, S>(
    inp: &Input,
    rules: &[Rule],
    p: &Params,
    l_post: i64,
    seed: u64,
    cand: C,
    mut fires: F,
    mut sink: S,
) -> Result<()>
where
    C: Fn(usize, i8) -> bool,
    F: FnMut(Rule, usize) -> bool,
    S: FnMut(C2bRow),
{
    let cube = inp.cube;
    check_cube(cube)?;
    let nw = cube.windows.len();
    for &r in rules {
        check_rule(r, nw)?;
    }
    let l_pre = i64::from(p.onset.l_pre_min);
    let [wa, wb] = p.controls.c2b.exclude_same_sign_episode_window_min;
    let k_want = p.controls.c2b.moments_per_episode as usize;
    let st = Strata::new(cube);
    let nb = st.months.len() * DECILES;
    // слотов назад от момента до слота «до»: σ берётся в ⌊(t − L_pre)/5⌋ = слот − ceil(L_pre/5)
    let shift = ceil_div(l_pre, SLOT_MIN) as usize;
    let t_last = slot_t(cube, cube.n.saturating_sub(1));
    let mut starts: [Vec<i64>; 2] = [Vec::new(), Vec::new()];
    for e in inp.eps.iter().filter(|e| e.sign != 0) {
        starts[usize::from(e.sign < 0)].push(e.t0);
    }
    for s in &mut starts {
        s.sort_unstable();
    }
    let mut pools: [Option<Pool>; 2] = [None, None];
    let cseed = seed_for(seed, inp.sym, TAG_C2B);
    let mut buf: Vec<u32> = Vec::with_capacity(k_want);
    for (ei, e) in inp.eps.iter().enumerate() {
        if e.sign == 0 || cube.n == 0 {
            continue;
        }
        let si = usize::from(e.sign < 0);
        let Some(mid) = st.month_id(yyyymm(e.t0)) else {
            continue;
        };
        let r = (e.t0 - l_pre).div_euclid(SLOT_MIN) - cube.slot0;
        if !(0..cube.n as i64).contains(&r) {
            continue;
        }
        let d = st.dec[r as usize];
        if d == NONE8 {
            continue;
        }
        let pool: &Pool = pools[si].get_or_insert_with(|| {
            Pool::build(nb, cube.n, |j| {
                let ref_dec = st.dec.get(j.checked_sub(shift)?).copied()?;
                let t = slot_t(cube, j);
                if ref_dec == NONE8
                    || t + l_post > t_last
                    || excluded(&starts[si], t + wa, t + wb)
                    || !cand(j, if si == 0 { 1 } else { -1 })
                {
                    return None;
                }
                Some(st.month_of[j] as usize * DECILES + ref_dec as usize)
            })
        });
        let bucket = pool.bucket(mid * DECILES + d as usize);
        let k = bucket.len().min(k_want);
        pick(&mut stream(cseed, ep_key(e)), bucket.len(), k, &mut buf);
        for &q in &buf {
            let tp = slot_t(cube, bucket[q as usize] as usize);
            for &rule in rules {
                let lag = first_lag(cube, tp, l_pre, l_post, |i| fires(rule, i));
                sink(C2bRow {
                    rule,
                    episode_idx: ei as u32,
                    t_pseudo: tp,
                    lag,
                });
            }
        }
    }
    Ok(())
}
