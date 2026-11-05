//! Выходы в прибыль после раннего сигнала (П-35, `docs/research/P-35-exits.md`, TK-492): для каждого первого
//! срабатывания правила — симуляция всех конфигураций выхода за ОДИН проход по минутам пути сделки: 140 по умолчанию
//! (84 в bps + 56 в единицах ATR-прокси) и 510 с `run --exits --exits-units` (ещё 370 в единицах ТЕКУЩЕЙ волатильности
//! — `rv60` и `NATR`, допущения 11–17).
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
//!    `ln` на каждой минуте; `ln` берётся только для цен исполнения и максимума. Отличие от наивного `ln`-сравнения —
//!    лишь на границе в пределах точности `f64` (сверено тестом на случайных путях). Пороги первого касания считаются
//!    один раз на путь: у уровней тейка/стопа/трейлинга свои «первые минуты», а конфигурация берёт минимум по своим
//!    уровням и сроку `cap` (стоп и тейк разных уровней не пересчитываются).
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
//! 10. **Скорость единиц A.** Один проход по минутам на все конфигурации, повторного обхода пути нет. Проверка идёт по
//!     слитым порогам: на минуту четыре сравнения (новый максимум, ближайший ждущий тейк по всем единицам, ближайший
//!     ждущий стоп, порог трейлинга), порог трейлинга пересчитывается лишь при смене максимума или указателей, единицы
//!     разбираются только на минутах касания. Сравнивали: (а) второй обход пути под единицы A — отвергнут (удваивает
//!     чтение пути); (б) указатели A рядом с прежними с проверкой каждого на каждой минуте — +≈60 % к работе прохода;
//!     (в) слитые пороги — выбран: проход не дороже прежнего, а решения те же (сверено с прежним проходом и с прямым).
//! 11. **Единицы текущей волатильности (флаг `--exits-units`).** Семейства E–H считаются в единицах `U`, взятых на
//!     минуте срабатывания `t_f` по данным `≤ t_f`: `U_rv = rv60_bps` — корень из суммы квадратов 60 минутных
//!     ln-доходностей `close_ff`, оканчивающихся в `t_f`, `× 1e4` (формула и окно `W_RV_SHORT` — те же, что `rv60_bps`
//!     в `dump.rs`, бит в бит); `U_nt = NATR_n(t_f)·100` — NATR из `play::natr_series` в процентах, переведённый в bps
//!     (1,30 % → 130 bps), `n` задаёт запуск (`--exits-natr-n`, в коде числа нет). Пороги — `множитель·U` bps; единица A
//!     в этих семействах не участвует. `U_rv` — реальная часовая волатильность, `U_nt` — размах свечи за минуту (не
//!     за час): множители относятся к своей единице, сетка общая, это сетка, а не рекомендация.
//! 12. **Сетка E–H.** На единицу `U ∈ {rv, nt}`: семейство «tp» (тейк `g·U`, стоп `s·U`) `g ∈ {0.5, 1, 2, 3, 5}`,
//!     `s ∈ {0.5, 1, 2, 3, нет}` и семейство «trail» (активация при `pnl ≥ R·U`, выход при откате `R·U` от максимума)
//!     `R ∈ {0.5, 1, 2, 3}`, `s ∈ {1, 2, нет}`; каждое × `cap ∈ {30, 60, 180, 360, 1440}` → 125 + 60 = 185 на
//!     единицу, всего 370 (cfg 140–509: единица rv, затем nt; внутри — tp, затем trail; циклы по возрастанию
//!     параметров, `cap` внутренний). Трейлинг активируется касанием тейка того же множителя (как в семействах B и D).
//! 13. **Предел E–H.** Пределы 30, 60, 180 минут короче `CAPS`: набор сделок по ним — по тому же правилу, что и по
//!     `cap` (допущение 2, `round_trip(…, H = cap)`), поэтому `n` семейств E–H разных `cap` может отличаться, а ячейки
//!     `caps_rv`/`caps_nt` хранят свои `n` и потолки по каждому из пяти `cap`. Потолок `mfe_bps` — по-прежнему максимум
//!     в окне `j ≤ 1439` (самый длинный `cap`), `mfe_cap` — в окне собственного `cap` (29, 59, 179, 359, 1439).
//! 14. **Единица не определена** (`NaN`, не положительна или пороги не представимы) — сделка выпадает из ЭТОЙ единицы и
//!     остаётся в остальных (счётчики `rv_undefined`, `nt_undefined`, по `cap` — `…_by_cap`, в блоке `exits`).
//! 15. **Без флага** прогон прежний байт в байт: 140 конфигураций, `exits-grid.csv` на 140 строк, ячейки `caps_rv` и
//!     `caps_nt` не заводятся; проход один и тот же (единицы rv и nt в нём просто не заводятся).
//! 16. **Ранний выход прохода.** Когда взяты все первые касания всех единиц (число ждущих уровней дошло до нуля),
//!     проверка порогов прекращается и остаётся только слежение за максимумом: он нужен потолку `mfe`, а `cap = 1440`
//!     базовой линии всё равно ждёт минуты 1439 (сама цена предела берётся из `round_trip`, а не из прохода).
//! 17. **Память и время.** Ячейка конфигурации — 40 байт; на группу (правило × сторона × сутки) при 510 конфигурациях
//!     ≈ 20,4 КБ вместо 5,6 КБ. Время — оценка по коду, не замер (В-182): проход на минуту прежний (четыре сравнения,
//!     пороги ещё 26 `exp` на сделку), разбор — 510 конфигураций вместо 140 по уже найденным касаниям и до 26 лишних
//!     исполнений (`ln` и сумма фандинга) на сделку, итого на сделку ≈ ×2 к прежнему.

#![allow(
    // индексы ограничены размерами плотных массивов, заданных сеткой; бюджет линтов (TK-211) не растим
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_precision_loss
)]

use super::agg::{fix, unfix, Layout};
use super::control::{sign_of, slot_t};
use super::dump::W_RV_SHORT;
use super::run::day_of_min;
use super::trade::{self, Funding, Skip};
use super::types::Cube;
use anyhow::{ensure, Context, Result};
use clap::Args;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::Path;

/// Перевод доли в bps — единица измерения, не параметр.
const BPS: f64 = 1.0e4;
/// Проценты NATR → bps (1 % = 100 bps) — единица измерения, не параметр.
const PCT_TO_BPS: f64 = 100.0;
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
/// «Нет стопа» (индекс в `SL_LEVELS` / `SLA_MULT` / `SLU_MULT`).
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

// ---------- сетка в единицах текущей волатильности (допущения 11–14; ТЗ CEO 05.11, числа назначены им) ----------

/// Временные пределы семейств E–H (единицы rv и nt), мин.
pub const CAPS_U: [u32; 5] = [30, 60, 180, 360, 1440];
pub const NCAPU: usize = CAPS_U.len();
/// Позиция `CAPS[0]` в `CAPS_U`: `CAPS` — хвост `CAPS_U` (проверяется на этапе компиляции).
const OLD_AT: usize = NCAPU - NCAP;
/// Тейк-профит семейств E и G: множители `U` (порог `g·U` bps).
pub const TPU_MULT: [f64; 5] = [0.5, 1.0, 2.0, 3.0, 5.0];
/// Стоп семейств E–H: множители `U` (порог `s·U` bps против позиции).
pub const SLU_MULT: [f64; 4] = [0.5, 1.0, 2.0, 3.0];
/// Трейлинг семейств F и H: множители `U`; равен первым четырём множителям тейка (активация = касание тейка).
pub const TRU_MULT: [f64; 4] = [0.5, 1.0, 2.0, 3.0];
pub const NTPU: usize = TPU_MULT.len();
pub const NSLU: usize = SLU_MULT.len();
pub const NTRU: usize = TRU_MULT.len();
/// Стопы семейств tp: 0.5·U, 1·U, 2·U, 3·U, нет.
const STOPS_TPU: [u8; 5] = [0, 1, 2, 3, NO_STOP];
/// Стопы семейств trail: 1·U, 2·U, нет.
const STOPS_TRU: [u8; 3] = [1, 2, NO_STOP];

/// Конфигураций в bps: базовая линия (по одной на `cap`) + A (`G × S × cap`) + B (`R × S × cap`) = 2 + 50 + 32.
pub const NCFG_FIXED: usize = NCAP + NCAP * NTP * STOPS_A.len() + NCAP * NTR * STOPS_B.len();
/// Семейство C «tpa»: `G·A × S·A × cap` = 4 · 4 · 2.
pub const NCFG_TPA: usize = NCAP * NTPA * STOPS_ATR.len();
/// Семейство D «tra»: `R·A × S·A × cap` = 3 · 4 · 2.
pub const NCFG_TRA: usize = NCAP * NTRA * STOPS_ATR.len();
/// Конфигураций в единицах A: C + D = 32 + 24.
pub const NCFG_ATR: usize = NCFG_TPA + NCFG_TRA;
/// Конфигураций по умолчанию: 84 в bps (нумерация 0–83 не менялась) + 56 в единицах A (84–139).
pub const NCFG: usize = NCFG_FIXED + NCFG_ATR;
/// Семейство tp одной единицы `U`: `g × s × cap` = 5 · 5 · 5.
pub const NCFG_TPU: usize = NCAPU * NTPU * STOPS_TPU.len();
/// Семейство trail одной единицы `U`: `R × s × cap` = 4 · 3 · 5.
pub const NCFG_TRU: usize = NCAPU * NTRU * STOPS_TRU.len();
/// Конфигураций одной единицы `U`: 125 + 60.
pub const NCFG_UNIT: usize = NCFG_TPU + NCFG_TRU;
/// Конфигураций в единицах rv и nt вместе: 370.
pub const NCFG_U: usize = 2 * NCFG_UNIT;
/// Все конфигурации с `--exits-units`: 140 + 370 (нумерация 0–139 не менялась).
pub const NCFG_ALL: usize = NCFG + NCFG_U;

/// Число единиц прохода: bps, A, rv, nt.
const NU: usize = 4;
/// Наибольшее число уровней единицы: тейк, стоп, трейлинг.
const MAXTP: usize = 5;
const MAXSL: usize = 4;
const MAXTR: usize = 4;
/// Уровней по единицам (bps, A, rv, nt): тейк, стоп, трейлинг.
const LV: [[usize; 3]; NU] = [
    [NTP, NSL, NTR],
    [NTPA, NSLA, NTRA],
    [NTPU, NSLU, NTRU],
    [NTPU, NSLU, NTRU],
];

/// Первый слот результата по (единица, тип уровня): по одному слоту на уровень тейка, стопа, трейлинга каждой единицы
/// и на каждый предел `CAPS_U` (слоты хранят исполнение, общее для конфигураций с одним и тем же событием).
const fn slot_offsets() -> [[usize; 3]; NU] {
    let mut off = [[0usize; 3]; NU];
    let mut acc = 0;
    let mut u = 0;
    while u < NU {
        let mut k = 0;
        while k < 3 {
            off[u][k] = acc;
            acc += LV[u][k];
            k += 1;
        }
        u += 1;
    }
    off
}
const OFF: [[usize; 3]; NU] = slot_offsets();
const S_CAP: usize = OFF[NU - 1][2] + LV[NU - 1][2];
const NSLOT: usize = S_CAP + NCAPU;

const _: () = {
    assert!(NCFG_FIXED == 84);
    assert!(NCFG_TPA == 32 && NCFG_TRA == 24);
    assert!(NCFG == 140);
    assert!(NCFG_TPU == 125 && NCFG_TRU == 60 && NCFG_UNIT == 185);
    assert!(NCFG_ALL == 510);
    assert!(NTP <= MAXTP && NTPA <= MAXTP && NTPU <= MAXTP);
    assert!(NSL <= MAXSL && NSLA <= MAXSL && NSLU <= MAXSL);
    assert!(NTR <= MAXTR && NTRA <= MAXTR && NTRU <= MAXTR);
    let mut i = 0;
    while i < NTR {
        assert!(TRAIL_LEVELS[i] == TP_LEVELS[i]);
        i += 1;
    }
    assert!(NTRA <= NTPA);
    assert!(NTRU <= NTPU);
    // `CAPS` — хвост `CAPS_U`: пределы прежних семейств совпадают с пределами единиц по значению
    let mut c = 0;
    while c < NCAP {
        assert!(CAPS_U[OLD_AT + c] == CAPS[c]);
        c += 1;
    }
};

/// Флаги `run` для единиц текущей волатильности в выходах (допущения 11–17): без них выходы прежние (140
/// конфигураций), с ними — 510. Числа сетки в коде; окно NATR задаёт запуск.
#[derive(Debug, Clone, Default, Args)]
pub struct UnitsArgs {
    /// Добавить к выходам 370 конфигураций в единицах `rv60` и NATR на минуте срабатывания (cfg 140–509, `exits.rs`).
    #[arg(long, requires = "exits")]
    pub exits_units: bool,
    /// Окно NATR единицы nt, 1 м свечей (без него берётся окно `--hv-n`; нет ни того, ни другого — отказ).
    #[arg(long, requires = "exits_units")]
    pub exits_natr_n: Option<usize>,
}

impl UnitsArgs {
    /// Окно NATR единицы nt; `None` — единиц rv и nt нет. `exits` — флаг `--exits`, `hv_n` — окно NATR фильтра выборки.
    pub fn natr_n(&self, exits: bool, hv_n: Option<usize>) -> Result<Option<usize>> {
        if !self.exits_units {
            return Ok(None);
        }
        ensure!(exits, "--exits-units требует --exits");
        let n = self.exits_natr_n.or(hv_n).context(
            "--exits-units требует --exits-natr-n (окно NATR единицы nt, 1 м свечей) или --hv-n",
        )?;
        ensure!(n > 0, "--exits-natr-n: окно NATR не короче одной свечи");
        Ok(Some(n))
    }
}

/// Единица порогов конфигурации: bps, ATR-прокси `A`, реальная часовая волатильность `rv60`, NATR. Порядок —
/// порядок предпочтения при ничьей в `verdict` (bps < atr < rv < nt) и номер единицы в проходе.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Unit {
    Bps,
    Atr,
    Rv,
    Nt,
}

impl Unit {
    /// Имя в `exits-grid.csv` (колонка `unit`).
    pub fn name(self) -> &'static str {
        match self {
            Unit::Bps => "bps",
            Unit::Atr => "atr",
            Unit::Rv => "rv",
            Unit::Nt => "nt",
        }
    }
}

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
    /// Тейк `g·U_rv` и стоп `s·U_rv` (`U_rv` — `rv60_bps` на `t_f`).
    TpR,
    /// Трейлинг `R·U_rv` и стоп `s·U_rv`.
    TrR,
    /// Тейк `g·U_nt` и стоп `s·U_nt` (`U_nt` — NATR на `t_f`, bps).
    TpN,
    /// Трейлинг `R·U_nt` и стоп `s·U_nt`.
    TrN,
}

impl Family {
    pub fn name(self) -> &'static str {
        match self {
            Family::Base => "base",
            Family::Tp => "tp",
            Family::Trail => "trail",
            Family::TpA => "tpa",
            Family::TrA => "tra",
            Family::TpR => "tpr",
            Family::TrR => "trr",
            Family::TpN => "tpn",
            Family::TrN => "trn",
        }
    }

    /// Единица порогов семейства.
    pub fn unit_kind(self) -> Unit {
        match self {
            Family::Base | Family::Tp | Family::Trail => Unit::Bps,
            Family::TpA | Family::TrA => Unit::Atr,
            Family::TpR | Family::TrR => Unit::Rv,
            Family::TpN | Family::TrN => Unit::Nt,
        }
    }

    /// Пороги в единицах A, а не в bps.
    pub fn is_atr(self) -> bool {
        self.unit_kind() == Unit::Atr
    }

    /// Пороги в единицах текущей волатильности (rv, nt): свои пределы `CAPS_U`.
    pub fn is_units(self) -> bool {
        matches!(self.unit_kind(), Unit::Rv | Unit::Nt)
    }

    /// Единица порогов в `exits-grid.csv`: `bps`, `atr`, `rv` или `nt`.
    pub fn unit(self) -> &'static str {
        self.unit_kind().name()
    }
}

/// Одна конфигурация выхода: индексы уровней (`NONE8` — уровня нет / не применим) и индекс `cap`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Cfg {
    pub family: Family,
    /// Индекс в `TP_LEVELS` (семейство A), `TPA_MULT` (C) или `TPU_MULT` (E, G).
    pub tp: u8,
    /// Индекс в `TRAIL_LEVELS` (семейство B), `TRA_MULT` (D) или `TRU_MULT` (F, H).
    pub trail: u8,
    /// Индекс в `SL_LEVELS` (A, B), `SLA_MULT` (C, D) или `SLU_MULT` (E–H); `NO_STOP` — стопа нет.
    pub sl: u8,
    /// Индекс в `CAPS` (семейства в bps и A) или в `CAPS_U` (семейства в единицах rv и nt).
    pub ci: u8,
}

impl Cfg {
    pub fn cap(&self) -> u32 {
        if self.family.is_units() {
            CAPS_U[usize::from(self.ci)]
        } else {
            CAPS[usize::from(self.ci)]
        }
    }
    /// Тейк, bps (только семейство A).
    pub fn g_bps(&self) -> Option<u32> {
        if self.family.unit_kind() != Unit::Bps {
            return None;
        }
        TP_LEVELS.get(usize::from(self.tp)).copied()
    }
    /// Трейлинг, bps (только семейство B).
    pub fn r_bps(&self) -> Option<u32> {
        if self.family.unit_kind() != Unit::Bps {
            return None;
        }
        TRAIL_LEVELS.get(usize::from(self.trail)).copied()
    }
    /// Стоп, bps (семейства A и B).
    pub fn s_bps(&self) -> Option<u32> {
        if self.family.unit_kind() != Unit::Bps {
            return None;
        }
        SL_LEVELS.get(usize::from(self.sl)).copied()
    }
    /// Тейк, множитель единицы (семейства C, E, G).
    pub fn g_mult(&self) -> Option<f64> {
        let i = usize::from(self.tp);
        match self.family {
            Family::TpA => TPA_MULT.get(i).copied(),
            Family::TpR | Family::TpN => TPU_MULT.get(i).copied(),
            _ => None,
        }
    }
    /// Трейлинг, множитель единицы (семейства D, F, H).
    pub fn r_mult(&self) -> Option<f64> {
        let i = usize::from(self.trail);
        match self.family {
            Family::TrA => TRA_MULT.get(i).copied(),
            Family::TrR | Family::TrN => TRU_MULT.get(i).copied(),
            _ => None,
        }
    }
    /// Стоп, множитель единицы (семейства C–H).
    pub fn s_mult(&self) -> Option<f64> {
        let i = usize::from(self.sl);
        match self.family {
            Family::TpA | Family::TrA => SLA_MULT.get(i).copied(),
            Family::TpR | Family::TrR | Family::TpN | Family::TrN => SLU_MULT.get(i).copied(),
            _ => None,
        }
    }

    /// Колонки `G,S,R` для csv (общие для `exits-grid.csv` и таблиц вердикта): в bps — целые, в единицах A, rv и nt —
    /// числа с точкой (`2.0`, `0.5`); `S = none` — стопа нет, у базовой линии пусто; `G`/`R` пусты там, где не применимы.
    pub fn gsr_cols(&self) -> (String, String, String) {
        let bps = |x: Option<u32>| x.map_or(String::new(), |v| v.to_string());
        let mult = |x: Option<f64>| x.map_or(String::new(), |v| format!("{v:.1}"));
        let stop = match self.family {
            Family::Base => String::new(),
            Family::Tp | Family::Trail => {
                self.s_bps().map_or("none".to_string(), |v| v.to_string())
            }
            _ => self
                .s_mult()
                .map_or("none".to_string(), |v| format!("{v:.1}")),
        };
        if self.family.unit_kind() == Unit::Bps {
            (bps(self.g_bps()), stop, bps(self.r_bps()))
        } else {
            (mult(self.g_mult()), stop, mult(self.r_mult()))
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

/// Сетка с единицами текущей волатильности: `grid()` (0–139, не менялись), затем единица rv (140–324: tp циклами
/// `g → s → cap`, затем trail циклами `R → s → cap`), затем единица nt (325–509, тот же порядок).
pub fn grid_all() -> Vec<Cfg> {
    let mut v = grid();
    v.reserve(NCFG_U);
    for (tp_fam, tr_fam) in [(Family::TpR, Family::TrR), (Family::TpN, Family::TrN)] {
        for gi in 0..NTPU as u8 {
            for &sl in &STOPS_TPU {
                for ci in 0..NCAPU as u8 {
                    v.push(Cfg {
                        family: tp_fam,
                        tp: gi,
                        trail: NONE8,
                        sl,
                        ci,
                    });
                }
            }
        }
        for ri in 0..NTRU as u8 {
            for &sl in &STOPS_TRU {
                for ci in 0..NCAPU as u8 {
                    v.push(Cfg {
                        family: tr_fam,
                        tp: NONE8,
                        trail: ri,
                        sl,
                        ci,
                    });
                }
            }
        }
    }
    debug_assert_eq!(v.len(), NCFG_ALL);
    v
}

/// Заголовок `exits-grid.csv`: `unit` (`bps` | `atr` | `rv` | `nt`) — последняя колонка, у семейств A, B и базовой
/// линии — `bps`.
pub const GRID_HEAD: &str = "cfg,family,G,S,R,cap,unit";

fn csv_of(grid: &[Cfg]) -> String {
    let mut s = format!("{GRID_HEAD}\n");
    for (k, c) in grid.iter().enumerate() {
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

/// `exits-grid.csv` по умолчанию: номер конфигурации и её параметры (`S = none` — стопа нет; `G`/`R` пусты там, где не
/// применимы; в единицах A — множители с точкой).
pub fn grid_csv() -> String {
    csv_of(&grid())
}

/// `exits-grid.csv` с `--exits-units`: 510 строк (первые 140 совпадают с `grid_csv()`).
pub fn grid_csv_all() -> String {
    csv_of(&grid_all())
}

/// ATR-прокси `A` (bps за час) из σ минутных ln-доходностей слота (`cube.sigma`): `σ × √60 × 1e4` (допущение 8).
/// Единственное место формулы: её же берёт `dump.rs` для `sigma_h_bps`.
pub fn atr_proxy_bps(sigma: f32) -> f64 {
    f64::from(sigma) * 60f64.sqrt() * BPS
}

/// `rv60_bps` на минуте `t_f` по `close_ff` (допущение 11): корень из суммы квадратов 60 минутных ln-доходностей,
/// оканчивающихся в `t_f`, `× 1e4`. `NaN`, если `t_f` вне ряда, окно не помещается (`i < 60`, как в `dump.rs`) или в
/// окне есть неположительная/неконечная цена. Сумма — по возрастанию минут, как `dump.rs`.
pub fn rv60_bps(cf: &[f64], first_min: i64, t_f: i64) -> f64 {
    let Some(i) = usize::try_from(t_f - first_min)
        .ok()
        .filter(|&i| i < cf.len())
    else {
        return f64::NAN;
    };
    if i < W_RV_SHORT {
        return f64::NAN;
    }
    let mut sum = 0.0f64;
    for w in cf[i - W_RV_SHORT..=i].windows(2) {
        if !w.iter().all(|x| x.is_finite() && *x > 0.0) {
            return f64::NAN;
        }
        let l = (w[1] / w[0]).ln();
        sum += l * l;
    }
    sum.sqrt() * BPS
}

/// `NATR_n` на минуте `t_f` в bps (допущение 11): `natr_pct` — ряд `play::natr_series` по сетке контракта
/// (индекс 0 ↔ `first_min`), в процентах; `NaN`, если минуты нет в ряду или NATR не определён.
pub fn nt_bps(natr_pct: &[f32], first_min: i64, t_f: i64) -> f64 {
    usize::try_from(t_f - first_min)
        .ok()
        .and_then(|i| natr_pct.get(i))
        .map_or(f64::NAN, |&x| f64::from(x) * PCT_TO_BPS)
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

/// Множители порогов семейств E–H для одной сделки (единица `U` — `rv60_bps` или NATR в bps, допущение 11): те же
/// `e^{±порог/1e4}`, порог — `множитель·U` bps.
#[derive(Debug, Clone, Copy)]
pub struct FacU {
    tp: [f64; NTPU],
    sl: [f64; NSLU],
    tr: [f64; NTRU],
}

impl FacU {
    /// `s` = ±1, `u_bps` — единица сделки. `None`, если единица не определена (допущение 14): не конечна, не
    /// положительна или какой-то из порогов не представим. Выражение порога то же, что у `FacA::new`.
    pub fn new(s: f64, u_bps: f64) -> Option<FacU> {
        if !(u_bps.is_finite() && u_bps > 0.0) {
            return None;
        }
        let e = |m: f64, k: f64| (k * m * u_bps / BPS).exp();
        let f = FacU {
            tp: TPU_MULT.map(|m| e(m, s)),
            sl: SLU_MULT.map(|m| e(m, -s)),
            tr: TRU_MULT.map(|m| e(m, -s)),
        };
        let ok = |xs: &[f64]| xs.iter().all(|x| x.is_finite() && *x > 0.0);
        (ok(&f.tp) && ok(&f.sl) && ok(&f.tr)).then_some(f)
    }
}

/// Множители одной единицы в общем виде прохода: сколько уровней и сами множители (лишние места — заглушки).
#[derive(Clone, Copy)]
struct UFac {
    /// Число уровней: тейк, стоп, трейлинг; `[0, 0, 0]` — единицы нет (не определена или не заведена).
    n: [usize; 3],
    tp: [f64; MAXTP],
    sl: [f64; MAXSL],
    tr: [f64; MAXTR],
}

impl UFac {
    const IDLE: UFac = UFac {
        n: [0; 3],
        tp: [1.0; MAXTP],
        sl: [1.0; MAXSL],
        tr: [1.0; MAXTR],
    };

    fn pad<const N: usize, const M: usize>(xs: &[f64; N]) -> [f64; M] {
        std::array::from_fn(|i| xs.get(i).copied().unwrap_or(1.0))
    }

    fn from_fac(f: &Fac) -> UFac {
        UFac {
            n: [NTP, NSL, NTR],
            tp: Self::pad(&f.tp),
            sl: Self::pad(&f.sl),
            tr: Self::pad(&f.tr),
        }
    }

    fn from_a(f: &FacA) -> UFac {
        UFac {
            n: [NTPA, NSLA, NTRA],
            tp: Self::pad(&f.tp),
            sl: Self::pad(&f.sl),
            tr: Self::pad(&f.tr),
        }
    }

    fn from_u(f: &FacU) -> UFac {
        UFac {
            n: [NTPU, NSLU, NTRU],
            tp: Self::pad(&f.tp),
            sl: Self::pad(&f.sl),
            tr: Self::pad(&f.tr),
        }
    }
}

/// Единицы прохода: bps всегда; A, rv, nt — если определены для сделки (`None` — единицы нет).
#[derive(Debug, Clone, Copy)]
pub struct Facs<'a> {
    pub bps: &'a Fac,
    pub atr: Option<&'a FacA>,
    pub rv: Option<&'a FacU>,
    pub nt: Option<&'a FacU>,
}

/// Первые минуты решения по уровням одной единицы (от входа, `NONE` — не было).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Hits {
    pub tp: [u32; MAXTP],
    pub sl: [u32; MAXSL],
    pub tr: [u32; MAXTR],
}

impl Hits {
    const NONE: Hits = Hits {
        tp: [NONE; MAXTP],
        sl: [NONE; MAXSL],
        tr: [NONE; MAXTR],
    };
}

/// Результат прохода по всем единицам: первые касания по единицам (bps, A, rv, nt) и потолки по пределам `CAPS_U`.
#[derive(Debug, Clone, Copy)]
pub struct ScanX {
    pub hits: [Hits; NU],
    /// Максимум `pnl` (bps) и его минута в окне каждого `CAPS_U` (`j ≤ cap − 1`); `NaN`, если окно не пройдено.
    pub mfe_cap: [(f64, u32); NCAPU],
    /// То же по всему пройденному окну.
    pub mfe: (f64, u32),
}

/// Результат прохода по единицам bps и A: первые минуты решения по уровням (от входа, `NONE` — не было) и потолки.
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
    scan_a(cf, b, jmax, sign, fac, None)
}

/// `scan` и те же указатели для порогов в единицах A (`fa` — множители сделки; `None` — A не определена, указатели
/// семейств C и D не заводятся). Один обход пути на оба набора порогов.
pub fn scan_a(cf: &[f64], b: usize, jmax: usize, sign: i8, fac: &Fac, fa: Option<&FacA>) -> Scan {
    let x = scan_x(
        cf,
        b,
        jmax,
        sign,
        &Facs {
            bps: fac,
            atr: fa,
            rv: None,
            nt: None,
        },
    );
    let (h, a) = (&x.hits[0], &x.hits[1]);
    Scan {
        tp: std::array::from_fn(|i| h.tp[i]),
        sl: std::array::from_fn(|i| h.sl[i]),
        tr: std::array::from_fn(|i| h.tr[i]),
        tpa: std::array::from_fn(|i| a.tp[i]),
        sla: std::array::from_fn(|i| a.sl[i]),
        tra: std::array::from_fn(|i| a.tr[i]),
        mfe_cap: std::array::from_fn(|i| x.mfe_cap[OLD_AT + i]),
        mfe: x.mfe,
    }
}

/// Проход по всем единицам сделки сразу: bps, A, rv, nt (последние три — если определены). Один обход пути.
pub fn scan_x(cf: &[f64], b: usize, jmax: usize, sign: i8, f: &Facs) -> ScanX {
    let uf = [
        UFac::from_fac(f.bps),
        f.atr.map_or(UFac::IDLE, UFac::from_a),
        f.rv.map_or(UFac::IDLE, UFac::from_u),
        f.nt.map_or(UFac::IDLE, UFac::from_u),
    ];
    scan_core(cf, b, jmax, sign, &uf)
}

/// Ближайший ждущий порог тейка по всем единицам (наименьший: пороги каждой единицы упорядочены по уровню, ждущий — на
/// который указывает указатель); `+∞`, если ждущих нет.
fn next_up(uf: &[UFac; NU], thr: &[[f64; MAXTP]; NU], at: &[usize; NU]) -> f64 {
    uf.iter()
        .zip(thr)
        .zip(at)
        .filter(|((f, _), &i)| i < f.n[0])
        .map(|((_, t), &i)| t[i])
        .fold(f64::INFINITY, f64::min)
}

/// Ближайший ждущий порог стопа (наибольший); `−∞`, если ждущих нет.
fn next_dn(uf: &[UFac; NU], thr: &[[f64; MAXSL]; NU], at: &[usize; NU]) -> f64 {
    uf.iter()
        .zip(thr)
        .zip(at)
        .filter(|((f, _), &i)| i < f.n[1])
        .map(|((_, t), &i)| t[i])
        .fold(f64::NEG_INFINITY, f64::max)
}

/// Множитель порога трейлинга, который задаёт наибольший порог срабатывания `v_best·fac` среди активных уровней всех
/// единиц (уровень активен, когда тейк того же уровня уже коснулся; следующий по уровню не сработает раньше
/// предыдущего — его просадка строже). Знак `v_best` совпадает со стороной (`v = s·c`, `c > 0`): при `v_best > 0`
/// наибольший порог даёт наибольший множитель, при `v_best < 0` — наименьший (округление монотонно, поэтому
/// `v_best·fac_max` равно максимуму произведений бит в бит). Нет активных уровней — множитель, дающий `−∞`.
fn gate_fac(uf: &[UFac; NU], tpn: &[usize; NU], trn: &[usize; NU], up_side: bool) -> f64 {
    let facs = uf
        .iter()
        .zip(tpn)
        .zip(trn)
        .filter(|((f, &tp), &tr)| tr < tp.min(f.n[2]))
        .map(|((f, _), &tr)| f.tr[tr]);
    if up_side {
        facs.fold(f64::NEG_INFINITY, f64::max)
    } else {
        facs.fold(f64::INFINITY, f64::min)
    }
}

/// Тело прохода. На минуту — четыре сравнения: новый максимум, «ждущий» порог тейка, «ждущий» порог стопа, порог
/// трейлинга. Ждущие пороги всех единиц сведены в один (тейк — наименьший из ждущих, стоп — наибольший), поэтому
/// число единиц не растит работу на каждой минуте: единицы разбираются только на минутах, где порог действительно
/// коснулся. Порог трейлинга `v_best·fac` пересчитывается лишь при смене максимума (одно умножение) или указателей;
/// значения порогов — те же выражения, что при прямом сравнении, решения не меняются (сверено с прямым проходом).
/// Когда взяты все уровни всех единиц, проверка порогов прекращается — остаётся максимум (допущение 16).
fn scan_core(cf: &[f64], b: usize, jmax: usize, sign: i8, uf: &[UFac; NU]) -> ScanX {
    let s = f64::from(sign);
    let v_in = s * cf[b];
    let mut tp_thr = [[f64::INFINITY; MAXTP]; NU];
    let mut sl_thr = [[f64::NEG_INFINITY; MAXSL]; NU];
    // сколько уровней ещё ждёт касания (тейки, стопы и трейлинги всех единиц)
    let mut left = 0usize;
    for ((f, tp), sl) in uf.iter().zip(&mut tp_thr).zip(&mut sl_thr) {
        for (t, m) in tp.iter_mut().zip(&f.tp).take(f.n[0]) {
            *t = v_in * m;
        }
        for (t, m) in sl.iter_mut().zip(&f.sl).take(f.n[1]) {
            *t = v_in * m;
        }
        left += f.n.iter().sum::<usize>();
    }
    let pnl = |v: f64| s * (v / v_in).ln() * BPS;
    let mut out = ScanX {
        hits: [Hits::NONE; NU],
        mfe_cap: [(f64::NAN, 0); NCAPU],
        mfe: (f64::NAN, 0),
    };
    let up_side = s > 0.0;
    let (mut vbest, mut jbest) = (f64::NEG_INFINITY, 0u32);
    let (mut tpn, mut sln, mut trn) = ([0usize; NU], [0usize; NU], [0usize; NU]);
    let (mut up, mut dn) = (next_up(uf, &tp_thr, &tpn), next_dn(uf, &sl_thr, &sln));
    let mut gfac = gate_fac(uf, &tpn, &trn, up_side);
    let mut tr_gate = f64::NEG_INFINITY;
    let mut tail = left == 0;
    let mut j = 1usize;
    for (ci, &cap) in CAPS_U.iter().enumerate() {
        let end = (cap as usize - 1).min(jmax);
        if j <= end {
            let seg = &cf[b + j..=b + end];
            // `from` — с какой позиции сегмента идёт только слежение за максимумом
            let mut from = 0usize;
            if !tail {
                from = seg.len();
                for (d, &c) in seg.iter().enumerate() {
                    let jj = (j + d) as u32;
                    let v = s * c;
                    if v > vbest {
                        vbest = v;
                        jbest = jj;
                        tr_gate = vbest * gfac;
                    }
                    if v >= up {
                        for (u, f) in uf.iter().enumerate() {
                            while tpn[u] < f.n[0] && v >= tp_thr[u][tpn[u]] {
                                out.hits[u].tp[tpn[u]] = jj;
                                tpn[u] += 1;
                                left -= 1;
                            }
                        }
                        up = next_up(uf, &tp_thr, &tpn);
                        // тейк активирует трейлинг своего уровня
                        gfac = gate_fac(uf, &tpn, &trn, up_side);
                        tr_gate = vbest * gfac;
                        if left == 0 {
                            // все уровни взяты: дальше только максимум
                            (tail, from) = (true, d + 1);
                            break;
                        }
                    }
                    if v <= dn {
                        for (u, f) in uf.iter().enumerate() {
                            while sln[u] < f.n[1] && v <= sl_thr[u][sln[u]] {
                                out.hits[u].sl[sln[u]] = jj;
                                sln[u] += 1;
                                left -= 1;
                            }
                        }
                        dn = next_dn(uf, &sl_thr, &sln);
                        if left == 0 {
                            (tail, from) = (true, d + 1);
                            break;
                        }
                    }
                    if v <= tr_gate {
                        for (u, f) in uf.iter().enumerate() {
                            while trn[u] < tpn[u].min(f.n[2]) && v <= vbest * f.tr[trn[u]] {
                                out.hits[u].tr[trn[u]] = jj;
                                trn[u] += 1;
                                left -= 1;
                            }
                        }
                        gfac = gate_fac(uf, &tpn, &trn, up_side);
                        tr_gate = vbest * gfac;
                        if left == 0 {
                            (tail, from) = (true, d + 1);
                            break;
                        }
                    }
                }
            }
            for (d, &c) in seg.iter().enumerate().skip(from) {
                let v = s * c;
                if v > vbest {
                    vbest = v;
                    jbest = (j + d) as u32;
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
    /// То же по пределам `CAPS_U` (только с `--exits-units`; `caps` — их хвост).
    pub caps_u: [CapCnt; NCAPU],
    /// Сделки (взятые хотя бы по одному `cap`), у которых A на `t_f` не определена: в семействах C и D не участвуют.
    pub atr_undefined: u64,
    /// То же по `cap`: сделки `cap`, исключённые из семейств C и D.
    pub atr_undefined_by_cap: [u64; NCAP],
    /// Сделки, у которых `rv60` на `t_f` не определён (допущение 14), всего и по пределам `CAPS_U`.
    pub rv_undefined: u64,
    pub rv_undefined_by_cap: [u64; NCAPU],
    /// То же для NATR.
    pub nt_undefined: u64,
    pub nt_undefined_by_cap: [u64; NCAPU],
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

/// Единицы сделки в bps; `NaN` — не определена (допущения 9 и 14). `rv` и `nt` читаются только с `--exits-units`.
#[derive(Debug, Clone, Copy)]
pub struct Units {
    pub atr: f64,
    pub rv: f64,
    pub nt: f64,
}

impl Units {
    /// Только A (прежние семейства); rv и nt не определены.
    pub fn atr_only(a_bps: f64) -> Units {
        Units {
            atr: a_bps,
            rv: f64::NAN,
            nt: f64::NAN,
        }
    }
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

/// Разбор конфигурации для прохода по сетке (считается один раз при создании накопителя).
#[derive(Clone, Copy)]
struct Plan {
    /// Единица: 0 bps, 1 A, 2 rv, 3 nt.
    unit: u8,
    /// Индекс предела в `CAPS_U`.
    uc: u8,
    /// Последняя минута решения: решение на `j` исполняется на `j + 1 ≤ cap_j`.
    last: u32,
    sl: u8,
    tp: u8,
    tr: u8,
}

fn plan_of(c: &Cfg) -> Plan {
    let tp_fam = matches!(
        c.family,
        Family::Tp | Family::TpA | Family::TpR | Family::TpN
    );
    let tr_fam = matches!(
        c.family,
        Family::Trail | Family::TrA | Family::TrR | Family::TrN
    );
    let uc = if c.family.is_units() {
        usize::from(c.ci)
    } else {
        OLD_AT + usize::from(c.ci)
    };
    Plan {
        unit: c.family.unit_kind() as u8,
        uc: uc as u8,
        last: CAPS_U[uc] - 2,
        sl: c.sl,
        tp: if tp_fam { c.tp } else { NONE8 },
        tr: if tr_fam { c.trail } else { NONE8 },
    }
}

/// Накопитель одного потока. Раскладка: группа `g = (ri·2 + si)·ndays + сутки`; внутри группы все конфигурации
/// подряд (140 или 510; запись одной сделки идёт по соседней памяти).
pub struct ExitAgg {
    nr: usize,
    ndays: usize,
    /// Единицы текущей волатильности включены (`--exits-units`): 510 конфигураций вместо 140.
    ext: bool,
    /// Окно NATR, 1 м свечей, из `--exits-natr-n` (только для `run-meta.json`; 0 без `--exits-units`).
    natr_n: usize,
    grid: Vec<Cfg>,
    plans: Vec<Plan>,
    fac: [Fac; 2],
    cfgs: Vec<CfgCell>,
    /// По всем сделкам с данным `cap` (базовая линия, семейства A и B).
    caps: Vec<CapCell>,
    /// Только по сделкам с определённой A (семейства C и D; допущение 9).
    caps_a: Vec<CapCell>,
    /// Только по сделкам с определённым `rv60` / NATR и по пределам `CAPS_U` (семейства E–H; пусты без флага).
    caps_rv: Vec<CapCell>,
    caps_nt: Vec<CapCell>,
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

/// Добавить сделку в ячейку `cap`: число сделок, потолок окна 1440 мин и потолок окна собственного `cap`.
fn put_cap(c: &mut CapCell, mfe: (f64, u32), mfe_cap: (f64, u32)) {
    c.n += 1;
    c.mfe += fix(mfe.0);
    c.mfe_min += u64::from(mfe.1);
    c.mfe_cap += fix(mfe_cap.0);
    c.mfe_cap_min += u64::from(mfe_cap.1);
}

impl ExitAgg {
    /// 140 конфигураций (84 в bps + 56 в единицах A): прежнее поведение, единицы rv и nt не заводятся.
    pub fn new(nr: usize, ndays: usize) -> ExitAgg {
        ExitAgg::build(nr, ndays, false, 0)
    }

    /// 510 конфигураций: к прежним 140 добавлены 370 в единицах `rv60` и NATR (`--exits-units`); `natr_n` — окно
    /// NATR из `--exits-natr-n` (в `run-meta.json`).
    pub fn with_units(nr: usize, ndays: usize, natr_n: usize) -> ExitAgg {
        ExitAgg::build(nr, ndays, true, natr_n)
    }

    fn build(nr: usize, ndays: usize, ext: bool, natr_n: usize) -> ExitAgg {
        // активация трейлинга в единицах A = касание тейка того же множителя (указатель `tpn` в `scan_core`)
        assert!(
            TRA_MULT
                .iter()
                .zip(&TPA_MULT)
                .all(|(r, g)| r.to_bits() == g.to_bits()),
            "TRA_MULT должен совпадать с началом TPA_MULT"
        );
        assert!(
            TRU_MULT
                .iter()
                .zip(&TPU_MULT)
                .all(|(r, g)| r.to_bits() == g.to_bits()),
            "TRU_MULT должен совпадать с началом TPU_MULT"
        );
        let grid = if ext { grid_all() } else { grid() };
        let plans = grid.iter().map(plan_of).collect();
        let groups = nr * 2 * ndays;
        ExitAgg {
            nr,
            ndays,
            ext,
            natr_n,
            cfgs: vec![CfgCell::default(); groups * grid.len()],
            plans,
            grid,
            fac: [Fac::new(1.0), Fac::new(-1.0)],
            caps: vec![CapCell::default(); groups * NCAP],
            caps_a: vec![CapCell::default(); groups * NCAP],
            caps_rv: vec![CapCell::default(); if ext { groups * NCAPU } else { 0 }],
            caps_nt: vec![CapCell::default(); if ext { groups * NCAPU } else { 0 }],
            cnt: ExitCounters::default(),
        }
    }

    /// Единицы rv и nt включены (510 конфигураций).
    pub fn is_ext(&self) -> bool {
        self.ext
    }

    /// Сетка накопителя: 140 или 510 конфигураций.
    pub fn grid(&self) -> &[Cfg] {
        &self.grid
    }

    /// Группа ячеек (правило `ri`, сторона `si` = 0 памп / 1 дамп, сутки `doff` от начала периода стадии).
    pub fn group(&self, ri: usize, si: usize, doff: usize) -> usize {
        (ri * 2 + si) * self.ndays + doff
    }

    pub fn cfg_cell(&self, g: usize, cfg: usize) -> &CfgCell {
        &self.cfgs[g * self.grid.len() + cfg]
    }

    pub fn cap_cell(&self, g: usize, ci: usize) -> &CapCell {
        &self.caps[g * NCAP + ci]
    }

    /// Сумма по ячейке и `cap` только по сделкам с определённой A (`n` семейств C и D).
    pub fn cap_cell_atr(&self, g: usize, ci: usize) -> &CapCell {
        &self.caps_a[g * NCAP + ci]
    }

    /// Сумма по ячейке и пределу `CAPS_U[ui]` только по сделкам с определённым `rv60` (семейства E и F).
    pub fn cap_cell_rv(&self, g: usize, ui: usize) -> &CapCell {
        &self.caps_rv[g * NCAPU + ui]
    }

    /// То же для NATR (семейства G и H).
    pub fn cap_cell_nt(&self, g: usize, ui: usize) -> &CapCell {
        &self.caps_nt[g * NCAPU + ui]
    }

    /// Ячейка `cap` для строки `exits-<stage>.csv` конфигурации `cfg`: своя совокупность сделок у каждой единицы.
    pub fn cap_cell_for(&self, g: usize, cfg: &Cfg) -> &CapCell {
        let ci = usize::from(cfg.ci);
        match cfg.family.unit_kind() {
            Unit::Bps => self.cap_cell(g, ci),
            Unit::Atr => self.cap_cell_atr(g, ci),
            Unit::Rv => self.cap_cell_rv(g, ci),
            Unit::Nt => self.cap_cell_nt(g, ci),
        }
    }

    /// Все первые срабатывания контракта: `trig[ri]` — слоты куба правила с позицией `ri` в `lay.rules`. Знак — знак
    /// `r_w` окна правила в `t_f` (как в основном проходе); без направления сделок нет. Единицы rv и nt не заводятся
    /// (прежние 140 конфигураций); с ними — `add_triggers_u`.
    pub fn add_triggers(&mut self, lay: &Layout, cube: &Cube, trig: &[Vec<usize>], px: &Px) {
        self.add_triggers_u(lay, cube, trig, px, None);
    }

    /// `add_triggers` с единицами текущей волатильности: `natr` — ряд `play::natr_series` по сетке контракта (проценты,
    /// индекс 0 ↔ `first_min`); `None` — NATR нигде не определён. Читается, только если накопитель создан
    /// `with_units`; `rv60` берётся из `px.cf`.
    pub fn add_triggers_u(
        &mut self,
        lay: &Layout,
        cube: &Cube,
        trig: &[Vec<usize>],
        px: &Px,
        natr: Option<&[f32]>,
    ) {
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
                let units = if self.ext {
                    Units {
                        atr: a,
                        rv: rv60_bps(px.cf, px.first_min, t_f),
                        nt: natr.map_or(f64::NAN, |s| nt_bps(s, px.first_min, t_f)),
                    }
                } else {
                    Units::atr_only(a)
                };
                self.add_trade_u(g, px, t_f, sign, units);
            }
        }
    }

    /// Одна сделка, только A (единицы rv и nt не определены): см. `add_trade_u`.
    pub fn add_trade(&mut self, g: usize, px: &Px, t_f: i64, sign: i8, a_bps: f64) {
        self.add_trade_u(g, px, t_f, sign, Units::atr_only(a_bps));
    }

    /// Счётчик сделок по пределу `CAPS_U[ui]`: в `caps_u` (только с `--exits-units`) и, для `CAPS`, в `caps`.
    fn count(&mut self, ui: usize, f: impl Fn(&mut CapCnt)) {
        if self.ext {
            f(&mut self.cnt.caps_u[ui]);
        }
        if ui >= OLD_AT {
            f(&mut self.cnt.caps[ui - OLD_AT]);
        }
    }

    /// Одна сделка: проход по минутам, затем результат каждой из 140 (или 510) конфигураций в группу `g`. Единицы
    /// сделки — `units` (допущения 9 и 14): `NaN`/неположительная — единица не определена, сделка остаётся в
    /// остальных конфигурациях, но не входит в семейства этой единицы.
    pub fn add_trade_u(&mut self, g: usize, px: &Px, t_f: i64, sign: i8, units: Units) {
        self.cnt.triggers += 1;
        let m_in = t_f + 1;
        // набор сделок по `cap` — как у `daycells` (допущение 2); без единиц rv и nt нужны только `CAPS` (хвост `CAPS_U`)
        let mut caps: [Option<trade::Trade>; NCAPU] = [None; NCAPU];
        let mut any = false;
        let first = if self.ext { 0 } else { OLD_AT };
        for (ui, &cap) in CAPS_U.iter().enumerate().skip(first) {
            match trade::round_trip(px.cf, px.first_min, &px.funding, t_f, cap, sign) {
                Ok(t) => {
                    caps[ui] = Some(t);
                    any = true;
                    self.count(ui, |c| c.trades += 1);
                }
                Err(Skip::Funding) => self.count(ui, |c| c.uncovered += 1),
                Err(Skip::NoData) => self.count(ui, |c| c.nodata += 1),
            }
        }
        if !any {
            return;
        }
        // `round_trip` вернул сделку: минута входа внутри ряда, цена входа конечна и положительна, ряд не короче `cap`
        let b = (m_in - px.first_min) as usize;
        let jmax = (px.cf.len() - 1 - b).min(CAPS_U[NCAPU - 1] as usize - 1);
        let s = f64::from(sign);
        let fa = FacA::new(s, units.atr);
        let fr = self.ext.then(|| FacU::new(s, units.rv)).flatten();
        let fnn = self.ext.then(|| FacU::new(s, units.nt)).flatten();
        if fa.is_none() {
            // семейства C и D живут только на пределах `CAPS`: сделка, взятая лишь коротким пределом, в их совокупности не была
            let mut in_old = false;
            for (ci, t) in caps[OLD_AT..].iter().enumerate() {
                if t.is_some() {
                    in_old = true;
                    self.cnt.atr_undefined_by_cap[ci] += 1;
                }
            }
            self.cnt.atr_undefined += u64::from(in_old);
        }
        if self.ext && fr.is_none() {
            self.cnt.rv_undefined += 1;
            for (ui, t) in caps.iter().enumerate() {
                if t.is_some() {
                    self.cnt.rv_undefined_by_cap[ui] += 1;
                }
            }
        }
        if self.ext && fnn.is_none() {
            self.cnt.nt_undefined += 1;
            for (ui, t) in caps.iter().enumerate() {
                if t.is_some() {
                    self.cnt.nt_undefined_by_cap[ui] += 1;
                }
            }
        }
        let sc = scan_x(
            px.cf,
            b,
            jmax,
            sign,
            &Facs {
                bps: &self.fac[usize::from(sign < 0)],
                atr: fa.as_ref(),
                rv: fr.as_ref(),
                nt: fnn.as_ref(),
            },
        );
        let fill = Fill {
            px,
            b,
            m_in,
            c_in: px.cf[b],
            side: f64::from(sign),
        };
        let mut slots = [Slot::default(); NSLOT];
        for (ui, t) in caps.iter().enumerate() {
            if let Some(t) = t {
                slots[S_CAP + ui] = Slot {
                    done: true,
                    gross: fix(t.gross_bps),
                    fund: fix(t.fund_bps),
                    hold: CAPS_U[ui] - 1,
                };
            }
        }
        // единица определена для сделки: bps — всегда, A, rv, nt — по своим множителям
        let unit_ok = [true, fa.is_some(), fr.is_some(), fnn.is_some()];
        let base = g * self.grid.len();
        for (k, pl) in self.plans.iter().enumerate() {
            let (u, uc) = (usize::from(pl.unit), usize::from(pl.uc));
            if !unit_ok[u] || caps[uc].is_none() {
                continue;
            }
            let h = &sc.hits[u];
            let (mut t_ev, mut slot, mut why) = (NONE, S_CAP + uc, Reason::Cap);
            if pl.sl != NONE8 {
                let t = h.sl[usize::from(pl.sl)];
                if t <= pl.last {
                    (t_ev, slot, why) = (t, OFF[u][1] + usize::from(pl.sl), Reason::Sl);
                }
            }
            if pl.tp != NONE8 {
                let t = h.tp[usize::from(pl.tp)];
                if t <= pl.last && t < t_ev {
                    (t_ev, slot, why) = (t, OFF[u][0] + usize::from(pl.tp), Reason::Tp);
                }
            }
            if pl.tr != NONE8 {
                let t = h.tr[usize::from(pl.tr)];
                if t <= pl.last && t < t_ev {
                    (t_ev, slot, why) = (t, OFF[u][2] + usize::from(pl.tr), Reason::Trail);
                }
            }
            if !slots[slot].done {
                slots[slot] = fill.exit_at(t_ev + 1);
            }
            put(&mut self.cfgs[base + k], slots[slot], why);
        }
        for (ui, t) in caps.iter().enumerate() {
            if t.is_none() {
                continue;
            }
            debug_assert!(sc.mfe_cap[ui].0.is_finite() && sc.mfe.0.is_finite());
            if ui >= OLD_AT {
                let ci = ui - OLD_AT;
                put_cap(&mut self.caps[g * NCAP + ci], sc.mfe, sc.mfe_cap[ui]);
                if fa.is_some() {
                    put_cap(&mut self.caps_a[g * NCAP + ci], sc.mfe, sc.mfe_cap[ui]);
                }
            }
            if fr.is_some() {
                put_cap(&mut self.caps_rv[g * NCAPU + ui], sc.mfe, sc.mfe_cap[ui]);
            }
            if fnn.is_some() {
                put_cap(&mut self.caps_nt[g * NCAPU + ui], sc.mfe, sc.mfe_cap[ui]);
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
            .chain(self.caps_a.iter_mut().zip(&o.caps_a))
            .chain(self.caps_rv.iter_mut().zip(&o.caps_rv))
            .chain(self.caps_nt.iter_mut().zip(&o.caps_nt));
        for (a, b) in pairs {
            a.n += b.n;
            a.mfe += b.mfe;
            a.mfe_min += b.mfe_min;
            a.mfe_cap += b.mfe_cap;
            a.mfe_cap_min += b.mfe_cap_min;
        }
        self.cnt.triggers += o.cnt.triggers;
        self.cnt.atr_undefined += o.cnt.atr_undefined;
        self.cnt.rv_undefined += o.cnt.rv_undefined;
        self.cnt.nt_undefined += o.cnt.nt_undefined;
        for (a, b) in self
            .cnt
            .atr_undefined_by_cap
            .iter_mut()
            .zip(o.cnt.atr_undefined_by_cap)
        {
            *a += b;
        }
        for (a, b) in self
            .cnt
            .rv_undefined_by_cap
            .iter_mut()
            .zip(o.cnt.rv_undefined_by_cap)
        {
            *a += b;
        }
        for (a, b) in self
            .cnt
            .nt_undefined_by_cap
            .iter_mut()
            .zip(o.cnt.nt_undefined_by_cap)
        {
            *a += b;
        }
        for (a, b) in self.cnt.caps.iter_mut().zip(o.cnt.caps) {
            a.trades += b.trades;
            a.uncovered += b.uncovered;
            a.nodata += b.nodata;
        }
        for (a, b) in self.cnt.caps_u.iter_mut().zip(o.cnt.caps_u) {
            a.trades += b.trades;
            a.uncovered += b.uncovered;
            a.nodata += b.nodata;
        }
        self.cnt.secs += o.cnt.secs;
    }

    /// Блок `exits` для `run-meta.json`. Без `--exits-units` — прежний набор ключей; с ним — ещё ключи единиц rv и nt.
    pub fn meta(&self) -> serde_json::Value {
        use serde_json::{Map, Value};
        let by_cap = |f: fn(&CapCnt) -> u64| -> Map<String, Value> {
            CAPS.iter()
                .zip(&self.cnt.caps)
                .map(|(c, n)| (c.to_string(), Value::from(f(n))))
                .collect()
        };
        // сделки семейств C и D по `cap`: все сделки `cap` минус сделки без A
        let atr_by_cap: Map<String, Value> = CAPS
            .iter()
            .zip(&self.cnt.caps)
            .zip(&self.cnt.atr_undefined_by_cap)
            .map(|((c, n), u)| (c.to_string(), Value::from(n.trades - u)))
            .collect();
        let undefined_by_cap: Map<String, Value> = CAPS
            .iter()
            .zip(&self.cnt.atr_undefined_by_cap)
            .map(|(c, u)| (c.to_string(), Value::from(*u)))
            .collect();
        let mut m = serde_json::json!({
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
        });
        if !self.ext {
            return m;
        }
        // по пределам `CAPS_U`: ключ — предел в минутах
        let by_u = |f: &dyn Fn(usize) -> u64| -> Value {
            let map: Map<String, Value> = CAPS_U
                .iter()
                .enumerate()
                .map(|(i, c)| (c.to_string(), Value::from(f(i))))
                .collect();
            Value::from(map)
        };
        let c = &self.cnt;
        m["configs"] = Value::from(self.grid.len());
        m["configs_rv"] = Value::from(NCFG_UNIT);
        m["configs_nt"] = Value::from(NCFG_UNIT);
        m["caps_units"] = serde_json::json!(CAPS_U);
        m["trades_by_cap_units"] = by_u(&|i| c.caps_u[i].trades);
        m["uncovered_by_cap_units"] = by_u(&|i| c.caps_u[i].uncovered);
        m["nodata_by_cap_units"] = by_u(&|i| c.caps_u[i].nodata);
        m["rv_unit"] = Value::from(
            "rv60_bps: sqrt(sum of 60 squared 1m ln-returns of close_ff ending at t_f) * 1e4",
        );
        m["rv_undefined"] = Value::from(c.rv_undefined);
        m["rv_undefined_by_cap"] = by_u(&|i| c.rv_undefined_by_cap[i]);
        m["rv_trades_by_cap"] = by_u(&|i| c.caps_u[i].trades - c.rv_undefined_by_cap[i]);
        m["nt_unit"] = Value::from(
            "NATR_n at t_f from play::natr_series, percent * 100 (bps); window n = nt_n 1m candles",
        );
        m["nt_n"] = Value::from(self.natr_n);
        m["nt_undefined"] = Value::from(c.nt_undefined);
        m["nt_undefined_by_cap"] = by_u(&|i| c.nt_undefined_by_cap[i]);
        m["nt_trades_by_cap"] = by_u(&|i| c.caps_u[i].trades - c.nt_undefined_by_cap[i]);
        m
    }

    /// `exits-grid.csv` этого накопителя: 140 или 510 строк.
    pub fn write_grid(&self, path: &Path) -> Result<()> {
        std::fs::write(path, csv_of(&self.grid)).with_context(|| path.display().to_string())
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
                    // семейства C–H считаются только по сделкам со своей определённой единицей: своё `n` и свои потолки
                    let cap = ex.cap_cell_for(g, cfg);
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

/// `exits-grid.csv` по умолчанию (140 строк).
pub fn write_grid(path: &Path) -> Result<()> {
    std::fs::write(path, grid_csv()).with_context(|| path.display().to_string())
}
