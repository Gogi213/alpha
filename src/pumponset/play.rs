//! Выборка «монеты в игре» для `alpha pumponset run` (TK-492, слова владельца 05.11): убрать откупы после обвала и
//! тяжей без высокой волатильности, оставить первую фазу монеты «в игре». Всё за флагами `run`; без флагов прогон и
//! его csv прежние байт в байт.
//!
//! Слова владельца (дословно): «такие лонги не должны быть в выборке. это не пампы а откупы после события какого то»;
//! «убери мусор из монет типа crv, ldo, btc, eth, ltc и все прочее у чего не было высокой волатильности, или что
//! является тяжем. я думаю что то что мы ищем - мы ищем не предпамп - а первую фазу монеты инплей»; «для меня нижний
//! порог высокой волатильности это натр за 30 1м периодов >=1». Числа порога и окна NATR задаёт запуск
//! (`--hv-natr-pct`, `--hv-n`), в коде их нет; единственное число из ТЗ — класс `D = 360` для откупа ([`REVERSAL_D_MIN`]).
//!
//! # Определения (ТЗ CEO 05.11)
//! 1. **`NATR1_n`** (определение проекта, `tools/compute/tk435-vol.py`): `TR_m = max(h − l, |h − c_{m−1}|, |l − c_{m−1}|) /
//!    c_m · 100` для минуты `m` со сделками; `c_{m−1}` — предыдущий `close_ff` (последний известный close, а не close
//!    предыдущей минуты сетки: в `tk435-vol.py` после минуты без сделок берётся `h − l`, здесь гэп считается от
//!    последнего close); пока close не было вовсе — `h − l`. `NATR_n(m)` — среднее `TR` по минутам со сделками в окне
//!    `[m − n + 1, m]` (включая `m`, только данные `≤ m`), если таких минут не меньше `max(1, n / 2)` (целочисленное
//!    деление, как `k >= max(1,(b−a)//2)`); окно должно целиком лежать в сетке контракта, иначе `NaN`.
//! 2. **`hv_state[m] = NATR_n(m) ≥ порога`**, `NaN` → ложь. Без `--hv-natr-pct` условие отсутствует (всегда истина).
//! 3. **Откуп** (`--no-reversal`): `X_360` — порог хода класса `D = 360` из `XdTable`, `L_pre = onset.L_pre_min`.
//!    `ok_long[m]` ложь, если за `[m − L_pre, m]` цена падала от максимума `close_ff` на величину `≥ X_360` вниз
//!    (`ln(max / c_m) ≥ X_360`); `ok_short[m]` ложь, если `ln(c_m / min) ≥ X_360` вверх (зеркало: шорт после ралли).
//!    Окно, не лежащее целиком в данных (`m − L_pre` раньше первого close), — нет свидетельства, `ok = истина`.
//!    Лонг — сигнал со знаком `r_w > 0`, шорт — `< 0`; знак 0 условием откупа не режется.
//! 4. **Первая фаза** (`--hv-first-phase`): `hv_state` не было ни в одной минуте `[m − L_pre, m − 1]`, а в `m` — есть:
//!    ровно минута перехода в игру (дословно по ТЗ). Следствие: на сетке срабатываний в 5 минут условие держится
//!    только у срабатываний, попавших точно на минуту перехода, — срабатывание позже внутри того же «игрового» отрезка
//!    уже не первая фаза. Сколько срабатываний ушло — `triggers_dropped_first_phase` в `run-meta.json`.
//!
//! # Где применяется (`run.rs`)
//! (а) первые срабатывания: минута `t_f` обязана иметь `hv_state`, `ok_dir(знак r_w)` и (с флагом) первую фазу — там же,
//! где период стадии и вселенная (`Fast::build_gated`); (б) контроли C2a (маска `cube.r`, как `mask_r`) и C2b (условие на
//! кандидата в пуле, `control::c2b_with_cand`, знак — знак эпизода): момент берётся из той же «игровой» популяции;
//! (в) эпизод — член выборки, если `hv_state` выполнено хотя бы в одной минуте `[t0, t_end]` **по факту** (конец эпизода
//! известен задним числом — единственное условие, не подчинённое правилу «только данные `≤ t`») и `ok_dir(знак)` в `t0`;
//! (г) выходы (`--exits`) и выгрузка (`--dump-triggers`) берут те же срабатывания. `X_D` считаются как прежде по
//! вселенной фита без фильтра (фильтр их не меняет).
//!
//! Порядок учёта потерь в счётчиках: волатильность, затем откуп, затем первая фаза — срабатывание/эпизод считается
//! по первому не выполненному условию.

#![allow(
    // индексы ограничены длиной сетки контракта (`len`), проверяемой до обращения; бюджет линтов (TK-211) не растим
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_precision_loss
)]

use super::cache::Contract;
use super::params::Params;
use super::types::XdTable;
use anyhow::{bail, ensure, Context, Result};
use clap::Args;
use std::collections::VecDeque;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::Path;

/// Класс `D` (мин) для откупа — из ТЗ владельца 05.11; `X_360` берётся из `XdTable` по `episodes.D_min`.
pub const REVERSAL_D_MIN: u32 = 360;
/// Доля → проценты (единица измерения NATR, не параметр).
const PCT: f64 = 100.0;

/// Флаги `run` выборки «в игре» (группа аргументов; без флагов — выборка прежняя).
#[derive(Debug, Clone, Default, Args)]
pub struct PlayArgs {
    /// Порог высокой волатильности: `NATR_n ≥ порога`, % (только вместе с `--hv-n`).
    #[arg(long, requires = "hv_n")]
    pub hv_natr_pct: Option<f64>,
    /// Окно NATR, 1 м свечей (только вместе с `--hv-natr-pct`).
    #[arg(long, requires = "hv_natr_pct")]
    pub hv_n: Option<usize>,
    /// Убрать откупы: нет падения (лонг) / роста (шорт) класса `D = 360` за `L_pre` до срабатывания.
    #[arg(long)]
    pub no_reversal: bool,
    /// Только первая фаза: до минуты срабатывания `hv_state` не было `L_pre` минут (требует `--hv-natr-pct`).
    #[arg(long, requires = "hv_natr_pct")]
    pub hv_first_phase: bool,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct HvCfg {
    pub natr_pct: f64,
    pub n: usize,
}

/// Проверенная конфигурация выборки; есть, только если включён хотя бы один фильтр.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PlayCfg {
    pub hv: Option<HvCfg>,
    pub no_reversal: bool,
    pub first_phase: bool,
}

impl PlayArgs {
    /// `None` — фильтра нет (прогон прежний). Сочетания флагов проверяются здесь, а не только в `clap`.
    pub fn cfg(&self) -> Result<Option<PlayCfg>> {
        let hv = match (self.hv_natr_pct, self.hv_n) {
            (None, None) => None,
            (Some(natr_pct), Some(n)) => {
                ensure!(
                    natr_pct.is_finite() && natr_pct > 0.0,
                    "--hv-natr-pct: нужно конечное число > 0, получено {natr_pct}"
                );
                ensure!(n > 0, "--hv-n: окно NATR не короче одной свечи");
                Some(HvCfg { natr_pct, n })
            }
            _ => bail!("--hv-natr-pct и --hv-n задаются только вместе"),
        };
        ensure!(
            !self.hv_first_phase || hv.is_some(),
            "--hv-first-phase требует --hv-natr-pct"
        );
        Ok((hv.is_some() || self.no_reversal).then_some(PlayCfg {
            hv,
            no_reversal: self.no_reversal,
            first_phase: self.hv_first_phase,
        }))
    }
}

/// Индекс класса `D = 360` в `episodes.D_min`; нет такого — ошибка (откуп без `X_360` не определён).
pub fn reversal_di(p: &Params) -> Result<usize> {
    p.episodes
        .d_min
        .iter()
        .position(|&d| d == REVERSAL_D_MIN)
        .with_context(|| {
            format!(
                "--no-reversal: в episodes.D_min {:?} нет D = {REVERSAL_D_MIN} мин (нужен X_{REVERSAL_D_MIN})",
                p.episodes.d_min
            )
        })
}

/// `[X_360 вверх, X_360 вниз]` (положительные ходы в `ln`) из таблицы порогов.
pub fn x_reversal(p: &Params, xd: &XdTable) -> Result<[f64; 2]> {
    let di = reversal_di(p)?;
    xd.x.get(di)
        .copied()
        .with_context(|| format!("XdTable: нет строки для D = {REVERSAL_D_MIN} (индекс {di})"))
}

// ---------- NATR ----------

/// `NATR1_n` на сетке минут контракта (определение — в заголовке модуля, п. 1); `NaN` — не определено.
pub fn natr_series(c: &Contract, n: usize) -> Vec<f32> {
    let len = c.close.len();
    let mut out = vec![f32::NAN; len];
    if n == 0 || len == 0 {
        return out;
    }
    // префиксные суммы TR (%) и число минут с определённым TR; плавающая сумма — f64, одинаковый порядок сложения
    let mut sum = vec![0.0f64; len + 1];
    let mut cnt = vec![0u32; len + 1];
    let mut prev = f64::NAN;
    for m in 0..len {
        let cm = c.close[m];
        let (mut s, mut k) = (sum[m], cnt[m]);
        if cm.is_finite() {
            let h = f64::from(c.high.get(m).copied().unwrap_or(f32::NAN));
            let l = f64::from(c.low.get(m).copied().unwrap_or(f32::NAN));
            if h.is_finite() && l.is_finite() {
                let mut tr = h - l;
                if prev.is_finite() {
                    tr = tr.max((h - prev).abs()).max((l - prev).abs());
                }
                let v = tr / cm * PCT;
                if v.is_finite() {
                    s += v;
                    k += 1;
                }
            }
            prev = cm;
        }
        sum[m + 1] = s;
        cnt[m + 1] = k;
    }
    let need = u32::try_from((n / 2).max(1)).unwrap_or(u32::MAX);
    for m in n.saturating_sub(1)..len {
        let a = m + 1 - n;
        let k = cnt[m + 1] - cnt[a];
        if k >= need {
            out[m] = ((sum[m + 1] - sum[a]) / f64::from(k)) as f32;
        }
    }
    out
}

/// `hv_state` по ряду NATR: `natr ≥ порога`, `NaN` → ложь.
pub fn hv_mask(natr: &[f32], thr: f64) -> Vec<bool> {
    natr.iter().map(|&x| f64::from(x) >= thr).collect()
}

/// Первая фаза (п. 4 заголовка): в минуте `m` `hv_state` есть, а в `[m − l_pre, m − 1]` не было ни разу.
pub fn first_phase_mask(hv: &[bool], l_pre: usize) -> Vec<bool> {
    let mut out = vec![false; hv.len()];
    let mut last: Option<usize> = None;
    for (m, &h) in hv.iter().enumerate() {
        if h {
            out[m] = last.is_none_or(|j| j + l_pre < m);
            last = Some(m);
        }
    }
    out
}

/// Откуп (п. 3 заголовка): `(ok_long, ok_short)` по `close_ff`, скользящие максимум и минимум за `[m − l_pre, m]`
/// монотонными очередями (`O(n)`). Окно не целиком в данных (раньше первого close) — `ok = истина`.
pub fn reversal_masks(cf: &[f64], l_pre: usize, x_up: f64, x_dn: f64) -> (Vec<bool>, Vec<bool>) {
    let n = cf.len();
    let (mut ok_long, mut ok_short) = (vec![true; n], vec![true; n]);
    let Some(v0) = cf.iter().position(|x| x.is_finite() && *x > 0.0) else {
        return (ok_long, ok_short);
    };
    let (mut qmax, mut qmin): (VecDeque<usize>, VecDeque<usize>) =
        (VecDeque::new(), VecDeque::new());
    for i in v0..n {
        let c = cf[i];
        if !(c.is_finite() && c > 0.0) {
            continue;
        }
        while qmax.back().is_some_and(|&j| cf[j] <= c) {
            qmax.pop_back();
        }
        qmax.push_back(i);
        while qmin.back().is_some_and(|&j| cf[j] >= c) {
            qmin.pop_back();
        }
        qmin.push_back(i);
        while qmax.front().is_some_and(|&j| j + l_pre < i) {
            qmax.pop_front();
        }
        while qmin.front().is_some_and(|&j| j + l_pre < i) {
            qmin.pop_front();
        }
        if i < v0 + l_pre {
            continue;
        }
        if let (Some(&a), Some(&b)) = (qmax.front(), qmin.front()) {
            // `NaN` (нет оценки) — не свидетельство: допустимо
            let (drawdown, runup) = ((cf[a] / c).ln(), (c / cf[b]).ln());
            ok_long[i] = drawdown.is_nan() || drawdown < x_dn;
            ok_short[i] = runup.is_nan() || runup < x_up;
        }
    }
    (ok_long, ok_short)
}

// ---------- маски контракта ----------

/// Итог проверки момента: оставлен либо первое невыполненное условие (порядок — в заголовке модуля).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Gate {
    Keep,
    /// Вне периода стадии или вселенной (в счётчиках выборки не участвует) — решает вызывающий (`run.rs`).
    Out,
    Hv,
    Reversal,
    FirstPhase,
}

/// Маски одного контракта по минутам его сетки (индекс 0 ↔ `first_min`). Все маски в минуте `m` используют данные
/// `≤ m` (окна кончаются в `m`); вне сетки `hv_state` ложь, остальные условия истинны.
#[derive(Debug, Clone)]
pub struct Play {
    cfg: PlayCfg,
    first_min: i64,
    len: usize,
    /// Пусты, если `hv` не задан.
    natr: Vec<f32>,
    hv: Vec<bool>,
    hv_cum: Vec<u32>,
    first: Vec<bool>,
    /// Пусты, если откуп не проверяется.
    ok_long: Vec<bool>,
    ok_short: Vec<bool>,
}

impl Play {
    /// `cf` — `c.close_ff()`; `x360` — `[X_360 вверх, вниз]` (нужен только с `no_reversal`); `l_pre` — `onset.L_pre_min`.
    pub fn build(c: &Contract, cf: &[f64], cfg: PlayCfg, l_pre: i64, x360: [f64; 2]) -> Play {
        let len = c.close.len();
        let l = usize::try_from(l_pre).unwrap_or(0);
        let mut pl = Play {
            cfg,
            first_min: c.first_min,
            len,
            natr: Vec::new(),
            hv: Vec::new(),
            hv_cum: Vec::new(),
            first: Vec::new(),
            ok_long: Vec::new(),
            ok_short: Vec::new(),
        };
        if let Some(hv) = cfg.hv {
            pl.natr = natr_series(c, hv.n);
            pl.hv = hv_mask(&pl.natr, hv.natr_pct);
            pl.hv_cum = Vec::with_capacity(len + 1);
            let mut acc = 0u32;
            pl.hv_cum.push(0);
            for &h in &pl.hv {
                acc += u32::from(h);
                pl.hv_cum.push(acc);
            }
            pl.first = first_phase_mask(&pl.hv, l);
        }
        if cfg.no_reversal {
            let (ol, os) = reversal_masks(cf, l, x360[0], x360[1]);
            pl.ok_long = ol;
            pl.ok_short = os;
        }
        pl
    }

    fn at(&self, m: i64) -> Option<usize> {
        usize::try_from(m - self.first_min)
            .ok()
            .filter(|&i| i < self.len)
    }

    /// NATR в минуте `m`; `NaN`, если `hv` не задан или не определён.
    pub fn natr_at(&self, m: i64) -> f32 {
        self.at(m)
            .and_then(|i| self.natr.get(i).copied())
            .unwrap_or(f32::NAN)
    }

    /// `hv_state` в минуте `m`; без `hv` — истина, вне сетки — ложь.
    pub fn hv_at(&self, m: i64) -> bool {
        self.cfg.hv.is_none() || self.at(m).is_some_and(|i| self.hv[i])
    }

    /// Минута перехода в игру (п. 4 заголовка); без `hv` — истина (условия нет), вне сетки — ложь.
    pub fn first_at(&self, m: i64) -> bool {
        self.cfg.hv.is_none() || self.at(m).is_some_and(|i| self.first[i])
    }

    /// Откуп не мешает входу в сторону `sign` (+1 лонг, −1 шорт) в минуте `m`; знак 0, вне сетки и без проверки — истина.
    pub fn ok_at(&self, m: i64, sign: i8) -> bool {
        if !self.cfg.no_reversal || sign == 0 {
            return true;
        }
        self.at(m).is_none_or(|i| {
            if sign > 0 {
                self.ok_long[i]
            } else {
                self.ok_short[i]
            }
        })
    }

    /// `hv_state` хотя бы в одной минуте `[a, b]` (по факту: эпизод, конец известен задним числом); без `hv` — истина.
    pub fn hv_any(&self, a: i64, b: i64) -> bool {
        if self.cfg.hv.is_none() {
            return true;
        }
        let lo = (a - self.first_min).max(0);
        let hi = (b - self.first_min).min(self.len as i64 - 1);
        lo <= hi && self.hv_cum[hi as usize + 1] > self.hv_cum[lo as usize]
    }

    /// Проверка момента `m` стороны `sign` по порядку: волатильность, откуп, первая фаза.
    pub fn gate(&self, m: i64, sign: i8) -> Gate {
        if !self.hv_at(m) {
            Gate::Hv
        } else if !self.ok_at(m, sign) {
            Gate::Reversal
        } else if self.cfg.first_phase && !self.first_at(m) {
            Gate::FirstPhase
        } else {
            Gate::Keep
        }
    }

    /// Момент годен как срабатывание/кандидат контроля.
    pub fn moment_ok(&self, m: i64, sign: i8) -> bool {
        self.gate(m, sign) == Gate::Keep
    }

    /// Минут сетки с `keep(m)` и из них с `hv_state` (без `hv` — все).
    pub fn minutes(&self, keep: impl Fn(i64) -> bool) -> (u64, u64) {
        let (mut all, mut hv) = (0u64, 0u64);
        for i in 0..self.len {
            let m = self.first_min + i as i64;
            if keep(m) {
                all += 1;
                hv += u64::from(self.cfg.hv.is_none() || self.hv[i]);
            }
        }
        (all, hv)
    }
}

// ---------- счётчики и отчёт о составе ----------

/// Счётчики выборки (суммируются по контрактам и потокам; порядок слияния на результат не влияет).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct FilterCnt {
    pub triggers_before: u64,
    pub triggers_dropped_hv: u64,
    pub triggers_dropped_reversal: u64,
    pub triggers_dropped_first_phase: u64,
    pub episodes_before: u64,
    pub episodes_dropped_hv: u64,
    pub episodes_dropped_reversal: u64,
    pub contracts_with_hv: u64,
    pub minutes_universe: u64,
    pub minutes_hv: u64,
}

impl FilterCnt {
    /// Учесть исход проверки срабатывания (`Out` в счётчиках не участвует).
    pub fn trigger(&mut self, g: Gate) {
        if g == Gate::Out {
            return;
        }
        self.triggers_before += 1;
        match g {
            Gate::Hv => self.triggers_dropped_hv += 1,
            Gate::Reversal => self.triggers_dropped_reversal += 1,
            Gate::FirstPhase => self.triggers_dropped_first_phase += 1,
            Gate::Keep | Gate::Out => {}
        }
    }

    pub fn merge(&mut self, o: &FilterCnt) {
        self.triggers_before += o.triggers_before;
        self.triggers_dropped_hv += o.triggers_dropped_hv;
        self.triggers_dropped_reversal += o.triggers_dropped_reversal;
        self.triggers_dropped_first_phase += o.triggers_dropped_first_phase;
        self.episodes_before += o.episodes_before;
        self.episodes_dropped_hv += o.episodes_dropped_hv;
        self.episodes_dropped_reversal += o.episodes_dropped_reversal;
        self.contracts_with_hv += o.contracts_with_hv;
        self.minutes_universe += o.minutes_universe;
        self.minutes_hv += o.minutes_hv;
    }
}

/// Блок `sample_filter` в `run-meta.json`. `x360` — `[вверх, вниз]` или `NaN`, если откуп не проверяется.
pub fn meta(
    cfg: &PlayCfg,
    cnt: &FilterCnt,
    contracts_eligible: usize,
    x360: [f64; 2],
) -> serde_json::Value {
    // без `--hv-natr-pct` условия волатильности нет: доля и число монет «с hv» не определены
    let share = (cfg.hv.is_some() && cnt.minutes_universe > 0)
        .then(|| cnt.minutes_hv as f64 / cnt.minutes_universe as f64);
    let x = |v: f64| cfg.no_reversal.then_some(v);
    serde_json::json!({
        "hv_natr_pct": cfg.hv.map(|h| h.natr_pct),
        "hv_n": cfg.hv.map(|h| h.n),
        "no_reversal": cfg.no_reversal,
        "first_phase": cfg.first_phase,
        "reversal_d_min": cfg.no_reversal.then_some(REVERSAL_D_MIN),
        "x360_up": x(x360[0]),
        "x360_down": x(x360[1]),
        "x_d_scope": "по вселенной фита без фильтра выборки (фильтр порогов не меняет)",
        "episode_hv": "по факту: хотя бы одна минута [t0, t_end]",
        "contracts_eligible": contracts_eligible,
        "contracts_with_hv": cfg.hv.map(|_| cnt.contracts_with_hv),
        "hv_minutes_share": share,
        "episodes_before_filter": cnt.episodes_before,
        "episodes_dropped_hv": cnt.episodes_dropped_hv,
        "episodes_dropped_reversal": cnt.episodes_dropped_reversal,
        "triggers_before": cnt.triggers_before,
        "triggers_dropped_hv": cnt.triggers_dropped_hv,
        "triggers_dropped_reversal": cnt.triggers_dropped_reversal,
        "triggers_dropped_first_phase": cnt.triggers_dropped_first_phase,
    })
}

/// Строка `sample-coins-<stage>.csv`: что осталось от монеты после всех условий прогона.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CoinRow {
    pub sym: String,
    pub minutes_in_universe: u64,
    pub minutes_hv: u64,
    pub triggers_kept: u64,
    pub episodes_kept: u64,
}

pub const COINS_HEADER: &str = "sym,minutes_in_universe,minutes_hv,triggers_kept,episodes_kept";

pub fn write_coins(path: &Path, rows: &[CoinRow]) -> Result<()> {
    let mut w = BufWriter::new(File::create(path).with_context(|| path.display().to_string())?);
    writeln!(w, "{COINS_HEADER}")?;
    for r in rows {
        writeln!(
            w,
            "{},{},{},{},{}",
            r.sym, r.minutes_in_universe, r.minutes_hv, r.triggers_kept, r.episodes_kept
        )?;
    }
    w.flush()?;
    Ok(())
}
