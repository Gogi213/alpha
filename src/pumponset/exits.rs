//! Выходы в прибыль после раннего сигнала (П-35, `docs/research/P-35-exits.md`, TK-492): для каждого первого
//! срабатывания правила — симуляция всех 140 конфигураций выхода (84 в bps + 56 в единицах ATR-прокси) за ОДИН
//! проход по минутам пути сделки.
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
//! 8. **Единица A — ATR-прокси, не настоящий ATR.** В кэше нет high/low, поэтому «ATR» здесь — close-to-close прокси:
//!    `A = sigma_h_bps` = σ минутных ln-доходностей `close_ff` за `controls.sigma_1m_window_days` (7) суток слота `t_f`
//!    (`cube.sigma`, тот же расчёт, что `dump.rs` для `sigma_h_bps`) `× √60 × 1e4`, bps за час. Пороги семейств C и D —
//!    `множитель·A` bps, сравнение с `pnl_j` в bps; правила исполнения (решение по close `j`, исполнение по `c[j + 1]`,
//!    предел по `c[cap_j]`, в одну минуту стоп приоритетнее тейка и трейлинга) те же, что у семейств A и B.
//! 9. **A не определена** (`NaN`, не положительна или пороги не конечны) на `t_f` — сделка не участвует в семействах C и
//!    D, но остаётся в базовой линии и в семействах A и B. Число таких сделок — `atr_undefined` в блоке `exits`
//!    `run-meta.json`. Поэтому у ячеек C и D `n` (и суммы `mfe` — по тому же подмножеству сделок) могут быть меньше, чем
//!    у остальных конфигураций того же `cap`: это отражено в колонке `n`, а сравнение «в bps против в ATR» идёт на
//!    разных наборах сделок (подмножество «A определена» — это сделки не раньше чем через окно σ после первой свечи).
//! 10. **Скорость.** Один проход по минутам на все 140 конфигураций, повторного обхода пути нет. Указатели единиц A (тейк,
//!     стоп, трейлинг; пороги — 10 `exp` на сделку) заведены рядом с прежними, но проверка идёт по слитым порогам: на
//!     минуту четыре сравнения (новый максимум, ближайший ждущий тейк из двух наборов, ближайший ждущий стоп, порог
//!     трейлинга), порог трейлинга пересчитывается лишь при смене максимума или указателей, единицы A разбираются только
//!     на минутах касания. Сравнивали: (а) второй обход пути под единицы A — отвергнут (удваивает чтение пути);
//!     (б) указатели A рядом с прежними с проверкой каждого на каждой минуте — +≈60 % к работе прохода; (в) слитые
//!     пороги — выбран: проход не дороже прежнего, а решения те же (сверено с прежним проходом и с прямым). Остаток
//!     прироста — разбор 56 конфигураций и до 10 лишних исполнений (`ln` и сумма фандинга) на сделку.

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
/// «Нет стопа» (индекс в `SL_LEVELS` / `SLA_MULT`).
pub const NO_STOP: u8 = NONE8;
/// Стопы семейства A: 100, 200, 300, 500, нет.
const STOPS_A: [u8; 5] = [0, 1, 2, 3, NO_STOP];
/// Стопы семейства B: 100, 300, 500, нет.
const STOPS_B: [u8; 4] = [0, 2, 3, NO_STOP];

// ---------- сетка в единицах ATR-прокси (допущение 8; числа назначены CEO-сессией, П-35 «Семейства в единицах ATR-прокси») ----------

/// Тейк-профит семейства C: множители `A` (порог `G·A` bps).
pub const TPA_MULT: [f64; 4] = [1.0, 2.0, 3.0, 5.0];
/// Стоп семейств C и D: множители `A` (порог `S·A` bps против позиции).
pub const SLA_MULT: [f64; 3] = [1.0, 2.0, 3.0];
/// Трейлинг семейства D: множители `A`; равен первым трём множителям тейка — активация трейлинга = первая минута
/// касания тейка того же множителя (как у семейства B).
pub const TRA_MULT: [f64; 3] = [1.0, 2.0, 3.0];
pub const NTPA: usize = TPA_MULT.len();
pub const NSLA: usize = SLA_MULT.len();
pub const NTRA: usize = TRA_MULT.len();
/// Стопы семейств C и D: 1·A, 2·A, 3·A, нет.
const STOPS_ATR: [u8; 4] = [0, 1, 2, NO_STOP];

/// Конфигураций в bps: базовая линия (по одной на `cap`) + A (`G × S × cap`) + B (`R × S × cap`) = 2 + 50 + 32.
pub const NCFG_FIXED: usize = NCAP + NCAP * NTP * STOPS_A.len() + NCAP * NTR * STOPS_B.len();
/// Семейство C «tpa»: `G·A × S·A × cap` = 4 · 4 · 2.
pub const NCFG_TPA: usize = NCAP * NTPA * STOPS_ATR.len();
/// Семейство D «tra»: `R·A × S·A × cap` = 3 · 4 · 2.
pub const NCFG_TRA: usize = NCAP * NTRA * STOPS_ATR.len();
/// Конфигураций в единицах A: C + D = 32 + 24.
pub const NCFG_ATR: usize = NCFG_TPA + NCFG_TRA;
/// Все конфигурации: 84 в bps (нумерация 0–83 не менялась) + 56 в единицах A (84–139).
pub const NCFG: usize = NCFG_FIXED + NCFG_ATR;

// Слоты результата выхода: по одному на уровень тейка, стопа, трейлинга (в bps и в единицах A) и на каждый `cap`.
const S_TP: usize = 0;
const S_SL: usize = S_TP + NTP;
const S_TR: usize = S_SL + NSL;
const S_TPA: usize = S_TR + NTR;
const S_SLA: usize = S_TPA + NTPA;
const S_TRA: usize = S_SLA + NSLA;
const S_CAP: usize = S_TRA + NTRA;
const NSLOT: usize = S_CAP + NCAP;

const _: () = {
    assert!(NCFG_FIXED == 84);
    assert!(NCFG_TPA == 32 && NCFG_TRA == 24);
    assert!(NCFG == 140);
    let mut i = 0;
    while i < NTR {
        assert!(TRAIL_LEVELS[i] == TP_LEVELS[i]);
        i += 1;
    }
    assert!(NTRA <= NTPA);
};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Family {
    /// Только `cap`.
    Base,
    /// Тейк-профит `G` и стоп `S`, bps.
    Tp,
    /// Трейлинг `R` и стоп `S`, bps.
    Trail,
    /// Тейк-профит `G·A` и стоп `S·A` (A — ATR-прокси `sigma_h_bps`).
    TpA,
    /// Трейлинг `R·A` и стоп `S·A`.
    TrA,
}

impl Family {
    pub fn name(self) -> &'static str {
        match self {
            Family::Base => "base",
            Family::Tp => "tp",
            Family::Trail => "trail",
            Family::TpA => "tpa",
            Family::TrA => "tra",
        }
    }

    /// Пороги в единицах A, а не в bps.
    pub fn is_atr(self) -> bool {
        matches!(self, Family::TpA | Family::TrA)
    }

    /// Единица порогов в `exits-grid.csv`: `bps` или `atr`.
    pub fn unit(self) -> &'static str {
        if self.is_atr() {
            "atr"
        } else {
            "bps"
        }
    }
}

/// Одна конфигурация выхода: индексы уровней (`NONE8` — уровня нет / не применим) и индекс `cap`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Cfg {
    pub family: Family,
    /// Индекс в `TP_LEVELS` (семейство A) или `TPA_MULT` (семейство C).
    pub tp: u8,
    /// Индекс в `TRAIL_LEVELS` (семейство B) или `TRA_MULT` (семейство D).
    pub trail: u8,
    /// Индекс в `SL_LEVELS` (A, B) или `SLA_MULT` (C, D); `NO_STOP` — стопа нет.
    pub sl: u8,
    /// Индекс в `CAPS`.
    pub ci: u8,
}

impl Cfg {
    pub fn cap(&self) -> u32 {
        CAPS[usize::from(self.ci)]
    }
    /// Тейк, bps (только семейство A).
    pub fn g_bps(&self) -> Option<u32> {
        if self.family.is_atr() {
            return None;
        }
        TP_LEVELS.get(usize::from(self.tp)).copied()
    }
    /// Трейлинг, bps (только семейство B).
    pub fn r_bps(&self) -> Option<u32> {
        if self.family.is_atr() {
            return None;
        }
        TRAIL_LEVELS.get(usize::from(self.trail)).copied()
    }
    /// Стоп, bps (семейства A и B).
    pub fn s_bps(&self) -> Option<u32> {
        if self.family.is_atr() {
            return None;
        }
        SL_LEVELS.get(usize::from(self.sl)).copied()
    }
    /// Тейк, множитель A (только семейство C).
    pub fn g_mult(&self) -> Option<f64> {
        if self.family != Family::TpA {
            return None;
        }
        TPA_MULT.get(usize::from(self.tp)).copied()
    }
    /// Трейлинг, множитель A (только семейство D).
    pub fn r_mult(&self) -> Option<f64> {
        if self.family != Family::TrA {
            return None;
        }
        TRA_MULT.get(usize::from(self.trail)).copied()
    }
    /// Стоп, множитель A (семейства C и D).
    pub fn s_mult(&self) -> Option<f64> {
        if !self.family.is_atr() {
            return None;
        }
        SLA_MULT.get(usize::from(self.sl)).copied()
    }

    /// Колонки `G,S,R` для csv (общие для `exits-grid.csv` и таблиц вердикта): в bps — целые, в единицах A — числа с
    /// точкой (`2.0`); `S = none` — стопа нет, у базовой линии пусто; `G`/`R` пусты там, где не применимы.
    pub fn gsr_cols(&self) -> (String, String, String) {
        let bps = |x: Option<u32>| x.map_or(String::new(), |v| v.to_string());
        let mult = |x: Option<f64>| x.map_or(String::new(), |v| format!("{v:.1}"));
        let stop = match self.family {
            Family::Base => String::new(),
            Family::Tp | Family::Trail => {
                self.s_bps().map_or("none".to_string(), |v| v.to_string())
            }
            Family::TpA | Family::TrA => self
                .s_mult()
                .map_or("none".to_string(), |v| format!("{v:.1}")),
        };
        if self.family.is_atr() {
            (mult(self.g_mult()), stop, mult(self.r_mult()))
        } else {
            (bps(self.g_bps()), stop, bps(self.r_bps()))
        }
    }
}

/// Сетка конфигураций в стабильной нумерации (номер = индекс): 0–1 базовая линия по `cap`; затем семейство A
/// циклами `G → S → cap`; затем семейство B циклами `R → S → cap` (0–83, не менялись); затем семейство C циклами
/// `G·A → S·A → cap` (84–115) и семейство D циклами `R·A → S·A → cap` (116–139).
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
    debug_assert_eq!(v.len(), NCFG_FIXED);
    for gi in 0..NTPA as u8 {
        for &sl in &STOPS_ATR {
            for ci in 0..NCAP as u8 {
                v.push(Cfg {
                    family: Family::TpA,
                    tp: gi,
                    trail: NONE8,
                    sl,
                    ci,
                });
            }
        }
    }
    for ri in 0..NTRA as u8 {
        for &sl in &STOPS_ATR {
            for ci in 0..NCAP as u8 {
                v.push(Cfg {
                    family: Family::TrA,
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

/// Заголовок `exits-grid.csv`: `unit` (`bps` | `atr`) — последняя колонка, у семейств A, B и базовой линии — `bps`.
pub const GRID_HEAD: &str = "cfg,family,G,S,R,cap,unit";

/// `exits-grid.csv`: номер конфигурации и её параметры (`S = none` — стопа нет; `G`/`R` пусты там, где не применимы;
/// в единицах A — множители с точкой).
pub fn grid_csv() -> String {
    let mut s = format!("{GRID_HEAD}\n");
    for (k, c) in grid().iter().enumerate() {
        let (g, st, r) = c.gsr_cols();
        s.push_str(&format!(
            "{},{},{},{},{},{},{}\n",
            k,
            c.family.name(),
            g,
            st,
            r,
            c.cap(),
            c.family.unit()
        ));
    }
    s
}

/// ATR-прокси `A` (bps за час) из σ минутных ln-доходностей слота (`cube.sigma`): `σ × √60 × 1e4` (допущение 8).
/// Единственное место формулы: её же берёт `dump.rs` для `sigma_h_bps`.
pub fn atr_proxy_bps(sigma: f32) -> f64 {
    f64::from(sigma) * 60f64.sqrt() * BPS
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

/// Множители порогов семейств C и D для одной сделки: те же `e^{±порог/1e4}`, но порог — `множитель·A` bps
/// (`A` — ATR-прокси сделки, допущение 8), поэтому считаются на сделку, а не на сторону.
#[derive(Debug, Clone, Copy)]
pub struct FacA {
    tp: [f64; NTPA],
    sl: [f64; NSLA],
    tr: [f64; NTRA],
}

impl FacA {
    /// Заглушка для прохода без семейств C и D (в вычислениях не участвует).
    const NEUTRAL: FacA = FacA {
        tp: [1.0; NTPA],
        sl: [1.0; NSLA],
        tr: [1.0; NTRA],
    };

    /// `s` = ±1, `a_bps` — A сделки. `None`, если A не определена: не конечна, не положительна или какой-то из порогов
    /// не представим (допущение 9). Выражение порога — `(k·множитель·A)/1e4`, как у `Fac::new` для уровня в bps:
    /// путь, построенный на `pnl = множитель·A` ровно, попадает на порог бит в бит.
    pub fn new(s: f64, a_bps: f64) -> Option<FacA> {
        if !(a_bps.is_finite() && a_bps > 0.0) {
            return None;
        }
        let e = |m: f64, k: f64| (k * m * a_bps / BPS).exp();
        let f = FacA {
            tp: TPA_MULT.map(|m| e(m, s)),
            sl: SLA_MULT.map(|m| e(m, -s)),
            tr: TRA_MULT.map(|m| e(m, -s)),
        };
        let ok = |xs: &[f64]| xs.iter().all(|x| x.is_finite() && *x > 0.0);
        (ok(&f.tp) && ok(&f.sl) && ok(&f.tr)).then_some(f)
    }
}

/// Результат прохода: первые минуты решения по уровням (от входа, `NONE` — не было) и потолки.
#[derive(Debug, Clone, Copy)]
pub struct Scan {
    pub tp: [u32; NTP],
    pub sl: [u32; NSL],
    pub tr: [u32; NTR],
    /// То же в единицах A (семейства C и D); `NONE`, если A не определена.
    pub tpa: [u32; NTPA],
    pub sla: [u32; NSLA],
    pub tra: [u32; NTRA],
    /// Максимум `pnl` (bps) и его минута в окне каждого `cap` (`j ≤ cap − 1`); `NaN`, если окно не пройдено.
    pub mfe_cap: [(f64, u32); NCAP],
    /// То же по всему пройденному окну.
    pub mfe: (f64, u32),
}

/// Один проход по `cf[b + 1 ..= b + jmax]`: `cf[b]` — цена входа. Все пороги и максимум за один обход; тейки,
/// стопы и трейлинги упорядочены по уровню, поэтому «ждущий» уровень один (указатель) — на минуту в среднем два
/// сравнения. Подробности — шапка модуля, допущения 3–6. Только пороги в bps; с единицами A — `scan_a`.
pub fn scan(cf: &[f64], b: usize, jmax: usize, sign: i8, fac: &Fac) -> Scan {
    scan_core::<false>(cf, b, jmax, sign, fac, &FacA::NEUTRAL)
}

/// `scan` и те же указатели для порогов в единицах A (`fa` — множители сделки; `None` — A не определена, указатели
/// семейств C и D не заводятся). Один обход пути на оба набора порогов.
pub fn scan_a(cf: &[f64], b: usize, jmax: usize, sign: i8, fac: &Fac, fa: Option<&FacA>) -> Scan {
    match fa {
        Some(fa) => scan_core::<true>(cf, b, jmax, sign, fac, fa),
        None => scan_core::<false>(cf, b, jmax, sign, fac, &FacA::NEUTRAL),
    }
}

/// Тело прохода. `A` — заводить ли указатели единиц A (константа времени компиляции: без них — только пороги в bps).
///
/// На минуту — четыре сравнения: новый максимум, «ждущий» порог тейка, «ждущий» порог стопа, порог трейлинга. Ждущие
/// пороги двух наборов (bps и A) сведены в один (тейк — наименьший из ждущих, стоп — наибольший), поэтому единицы A
/// не удваивают работу на каждой минуте, а разбираются только на минутах, где порог действительно коснулся. Порог
/// трейлинга `v_best·e^{−s·R}` пересчитывается лишь при смене максимума или указателей, а не на каждой минуте;
/// значения порогов — те же выражения, что при прямом сравнении, решения не меняются (сверено с прямым проходом).
fn scan_core<const A: bool>(
    cf: &[f64],
    b: usize,
    jmax: usize,
    sign: i8,
    fac: &Fac,
    fa: &FacA,
) -> Scan {
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
    let mut tpa_thr = [f64::INFINITY; NTPA];
    let mut sla_thr = [f64::NEG_INFINITY; NSLA];
    if A {
        for (t, f) in tpa_thr.iter_mut().zip(&fa.tp) {
            *t = v_in * f;
        }
        for (t, f) in sla_thr.iter_mut().zip(&fa.sl) {
            *t = v_in * f;
        }
    }
    // ближайший ждущий порог тейка (наименьший из двух наборов) и стопа (наибольший); у обоих наборов пороги упорядочены
    // по уровню, так что ждущий уровень набора — тот, на который указывает указатель
    let up_of = |tpn: usize, tan: usize| -> f64 {
        let f = if tpn < NTP {
            tp_thr[tpn]
        } else {
            f64::INFINITY
        };
        let a = if A && tan < NTPA {
            tpa_thr[tan]
        } else {
            f64::INFINITY
        };
        f.min(a)
    };
    let dn_of = |sln: usize, san: usize| -> f64 {
        let f = if sln < NSL {
            sl_thr[sln]
        } else {
            f64::NEG_INFINITY
        };
        let a = if A && san < NSLA {
            sla_thr[san]
        } else {
            f64::NEG_INFINITY
        };
        f.max(a)
    };
    // порог срабатывания трейлинга: наибольший из активных уровней двух наборов (уровень активен, когда тейк того же
    // уровня уже коснулся; следующий по уровню не сработает раньше предыдущего — его просадка строже)
    let tr_of = |vbest: f64, tpn: usize, trn: usize, tan: usize, tran: usize| -> f64 {
        let f = if trn < tpn.min(NTR) {
            vbest * fac.tr[trn]
        } else {
            f64::NEG_INFINITY
        };
        let a = if A && tran < tan.min(NTRA) {
            vbest * fa.tr[tran]
        } else {
            f64::NEG_INFINITY
        };
        f.max(a)
    };
    let pnl = |v: f64| s * (v / v_in).ln() * BPS;
    let mut out = Scan {
        tp: [NONE; NTP],
        sl: [NONE; NSL],
        tr: [NONE; NTR],
        tpa: [NONE; NTPA],
        sla: [NONE; NSLA],
        tra: [NONE; NTRA],
        mfe_cap: [(f64::NAN, 0); NCAP],
        mfe: (f64::NAN, 0),
    };
    let (mut vbest, mut jbest) = (f64::NEG_INFINITY, 0u32);
    let (mut tpn, mut sln, mut trn) = (0usize, 0usize, 0usize);
    let (mut tan, mut san, mut tran) = (0usize, 0usize, 0usize);
    let (mut up, mut dn, mut tr_gate) = (up_of(0, 0), dn_of(0, 0), f64::NEG_INFINITY);
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
                    tr_gate = tr_of(vbest, tpn, trn, tan, tran);
                }
                if v >= up {
                    while tpn < NTP && v >= tp_thr[tpn] {
                        out.tp[tpn] = jj;
                        tpn += 1;
                    }
                    if A {
                        while tan < NTPA && v >= tpa_thr[tan] {
                            out.tpa[tan] = jj;
                            tan += 1;
                        }
                    }
                    up = up_of(tpn, tan);
                    // тейк активирует трейлинг своего уровня
                    tr_gate = tr_of(vbest, tpn, trn, tan, tran);
                }
                if v <= dn {
                    while sln < NSL && v <= sl_thr[sln] {
                        out.sl[sln] = jj;
                        sln += 1;
                    }
                    if A {
                        while san < NSLA && v <= sla_thr[san] {
                            out.sla[san] = jj;
                            san += 1;
                        }
                    }
                    dn = dn_of(sln, san);
                }
                if v <= tr_gate {
                    while trn < tpn.min(NTR) && v <= vbest * fac.tr[trn] {
                        out.tr[trn] = jj;
                        trn += 1;
                    }
                    if A {
                        while tran < tan.min(NTRA) && v <= vbest * fa.tr[tran] {
                            out.tra[tran] = jj;
                            tran += 1;
                        }
                    }
                    tr_gate = tr_of(vbest, tpn, trn, tan, tran);
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
    /// Сделки (взятые хотя бы по одному `cap`), у которых A на `t_f` не определена: в семействах C и D не участвуют.
    pub atr_undefined: u64,
    /// То же по `cap`: сделки `cap`, исключённые из семейств C и D.
    pub atr_undefined_by_cap: [u64; NCAP],
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

/// Накопитель одного потока. Раскладка: группа `g = (ri·2 + si)·ndays + сутки`; внутри группы 140 конфигураций
/// подряд (запись одной сделки идёт по соседней памяти).
pub struct ExitAgg {
    nr: usize,
    ndays: usize,
    grid: Vec<Cfg>,
    fac: [Fac; 2],
    cfgs: Vec<CfgCell>,
    /// По всем сделкам с данным `cap` (базовая линия, семейства A и B).
    caps: Vec<CapCell>,
    /// Только по сделкам с определённой A (семейства C и D; допущение 9).
    caps_a: Vec<CapCell>,
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
    /// Слот результата выхода через `j` минут после входа.
    fn exit_at(&self, j: u32) -> Slot {
        let (gross, fund) = self.at(j);
        Slot {
            done: true,
            gross,
            fund,
            hold: j,
        }
    }

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

/// Записать результат слота в ячейку конфигурации.
fn put(cell: &mut CfgCell, s: Slot, why: Reason) {
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

/// Добавить сделку в ячейку `cap` (число сделок и потолки окна `ci`).
fn put_cap(c: &mut CapCell, sc: &Scan, ci: usize) {
    c.n += 1;
    c.mfe += fix(sc.mfe.0);
    c.mfe_min += u64::from(sc.mfe.1);
    c.mfe_cap += fix(sc.mfe_cap[ci].0);
    c.mfe_cap_min += u64::from(sc.mfe_cap[ci].1);
}

impl ExitAgg {
    pub fn new(nr: usize, ndays: usize) -> ExitAgg {
        // активация трейлинга в единицах A = касание тейка того же множителя (указатель `tan` в `scan_core`)
        assert!(
            TRA_MULT
                .iter()
                .zip(&TPA_MULT)
                .all(|(r, g)| r.to_bits() == g.to_bits()),
            "TRA_MULT должен совпадать с началом TPA_MULT"
        );
        let groups = nr * 2 * ndays;
        ExitAgg {
            nr,
            ndays,
            grid: grid(),
            fac: [Fac::new(1.0), Fac::new(-1.0)],
            cfgs: vec![CfgCell::default(); groups * NCFG],
            caps: vec![CapCell::default(); groups * NCAP],
            caps_a: vec![CapCell::default(); groups * NCAP],
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

    /// Сумма по ячейке и `cap` только по сделкам с определённой A (`n` семейств C и D).
    pub fn cap_cell_atr(&self, g: usize, ci: usize) -> &CapCell {
        &self.caps_a[g * NCAP + ci]
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
                // A сделки — σ слота `t_f` (допущение 8); вне куба — не определена
                let a = cube.sigma.get(i).map_or(f64::NAN, |&sg| atr_proxy_bps(sg));
                self.add_trade(g, px, t_f, sign, a);
            }
        }
    }

    /// Одна сделка: проход по минутам, затем результат каждой из 140 конфигураций в группу `g`. `a_bps` — A сделки
    /// (допущение 8); `NaN`/неположительная — A не определена: сделка остаётся в 84 конфигурациях bps, но не входит в
    /// семейства C и D (допущение 9).
    pub fn add_trade(&mut self, g: usize, px: &Px, t_f: i64, sign: i8, a_bps: f64) {
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
        let fa = FacA::new(f64::from(sign), a_bps);
        if fa.is_none() {
            self.cnt.atr_undefined += 1;
            for (ci, t) in caps.iter().enumerate() {
                if t.is_some() {
                    self.cnt.atr_undefined_by_cap[ci] += 1;
                }
            }
        }
        let sc = scan_a(
            px.cf,
            b,
            jmax,
            sign,
            &self.fac[usize::from(sign < 0)],
            fa.as_ref(),
        );
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
                slots[S_CAP + ci] = Slot {
                    done: true,
                    gross: fix(t.gross_bps),
                    fund: fix(t.fund_bps),
                    hold: CAPS[ci] - 1,
                };
            }
        }
        let base = g * NCFG;
        for (k, cfg) in self.grid.iter().enumerate().take(NCFG_FIXED) {
            let ci = usize::from(cfg.ci);
            if caps[ci].is_none() {
                continue;
            }
            // последняя минута решения: решение на `j` исполняется на `j + 1 ≤ cap_j`
            let last = CAPS[ci] - 2;
            let (mut t_ev, mut slot, mut why) = (NONE, S_CAP + ci, Reason::Cap);
            if cfg.sl != NO_STOP {
                let t = sc.sl[usize::from(cfg.sl)];
                if t <= last {
                    (t_ev, slot, why) = (t, S_SL + usize::from(cfg.sl), Reason::Sl);
                }
            }
            if cfg.tp != NONE8 {
                let t = sc.tp[usize::from(cfg.tp)];
                if t <= last && t < t_ev {
                    (t_ev, slot, why) = (t, S_TP + usize::from(cfg.tp), Reason::Tp);
                }
            }
            if cfg.trail != NONE8 {
                let t = sc.tr[usize::from(cfg.trail)];
                if t <= last && t < t_ev {
                    (t_ev, slot, why) = (t, S_TR + usize::from(cfg.trail), Reason::Trail);
                }
            }
            if !slots[slot].done {
                slots[slot] = fill.exit_at(t_ev + 1);
            }
            put(&mut self.cfgs[base + k], slots[slot], why);
        }
        // семейства C и D: тот же разбор по первым касаниям в единицах A; без A их нет (допущение 9)
        if fa.is_some() {
            for (k, cfg) in self.grid.iter().enumerate().skip(NCFG_FIXED) {
                let ci = usize::from(cfg.ci);
                if caps[ci].is_none() {
                    continue;
                }
                let last = CAPS[ci] - 2;
                let (mut t_ev, mut slot, mut why) = (NONE, S_CAP + ci, Reason::Cap);
                if cfg.sl != NO_STOP {
                    let t = sc.sla[usize::from(cfg.sl)];
                    if t <= last {
                        (t_ev, slot, why) = (t, S_SLA + usize::from(cfg.sl), Reason::Sl);
                    }
                }
                if cfg.family == Family::TpA {
                    let t = sc.tpa[usize::from(cfg.tp)];
                    if t <= last && t < t_ev {
                        (t_ev, slot, why) = (t, S_TPA + usize::from(cfg.tp), Reason::Tp);
                    }
                }
                if cfg.family == Family::TrA {
                    let t = sc.tra[usize::from(cfg.trail)];
                    if t <= last && t < t_ev {
                        (t_ev, slot, why) = (t, S_TRA + usize::from(cfg.trail), Reason::Trail);
                    }
                }
                if !slots[slot].done {
                    slots[slot] = fill.exit_at(t_ev + 1);
                }
                put(&mut self.cfgs[base + k], slots[slot], why);
            }
        }
        for (ci, t) in caps.iter().enumerate() {
            if t.is_none() {
                continue;
            }
            debug_assert!(sc.mfe_cap[ci].0.is_finite() && sc.mfe.0.is_finite());
            put_cap(&mut self.caps[g * NCAP + ci], &sc, ci);
            if fa.is_some() {
                put_cap(&mut self.caps_a[g * NCAP + ci], &sc, ci);
            }
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
        let pairs = self
            .caps
            .iter_mut()
            .zip(&o.caps)
            .chain(self.caps_a.iter_mut().zip(&o.caps_a));
        for (a, b) in pairs {
            a.n += b.n;
            a.mfe += b.mfe;
            a.mfe_min += b.mfe_min;
            a.mfe_cap += b.mfe_cap;
            a.mfe_cap_min += b.mfe_cap_min;
        }
        self.cnt.triggers += o.cnt.triggers;
        self.cnt.atr_undefined += o.cnt.atr_undefined;
        for (a, b) in self
            .cnt
            .atr_undefined_by_cap
            .iter_mut()
            .zip(o.cnt.atr_undefined_by_cap)
        {
            *a += b;
        }
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
        // сделки семейств C и D по `cap`: все сделки `cap` минус сделки без A
        let atr_by_cap: serde_json::Map<String, serde_json::Value> = CAPS
            .iter()
            .zip(&self.cnt.caps)
            .zip(&self.cnt.atr_undefined_by_cap)
            .map(|((c, n), u)| (c.to_string(), serde_json::Value::from(n.trades - u)))
            .collect();
        let undefined_by_cap: serde_json::Map<String, serde_json::Value> = CAPS
            .iter()
            .zip(&self.cnt.atr_undefined_by_cap)
            .map(|(c, u)| (c.to_string(), serde_json::Value::from(*u)))
            .collect();
        serde_json::json!({
            "configs": NCFG,
            "configs_bps": NCFG_FIXED,
            "configs_atr": NCFG_ATR,
            "caps": CAPS,
            "rules": self.nr,
            "triggers": self.cnt.triggers,
            "trades_by_cap": by_cap(|n| n.trades),
            "uncovered_by_cap": by_cap(|n| n.uncovered),
            "nodata_by_cap": by_cap(|n| n.nodata),
            "atr_unit": "sigma_h_bps: sigma 1m ln-returns close_ff (7 d) * sqrt(60) * 1e4; close-to-close proxy of ATR",
            "atr_undefined": self.cnt.atr_undefined,
            "atr_undefined_by_cap": undefined_by_cap,
            "atr_trades_by_cap": atr_by_cap,
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
                    // семейства C и D считаются только по сделкам с определённой A: своё `n` и свои потолки
                    let cap = if cfg.family.is_atr() {
                        ex.cap_cell_atr(g, usize::from(cfg.ci))
                    } else {
                        ex.cap_cell(g, usize::from(cfg.ci))
                    };
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
