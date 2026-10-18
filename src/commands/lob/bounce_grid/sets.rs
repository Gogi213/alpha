//! Наборы фильтров касаний (`--set`, `FilterSet`), их артефакты (`SetPaths`),
//! контекст касания по семи осям (`CTX_AXES`, `Range`, `TouchContext`,
//! `touch_contexts`), режим по минутам (`RegimeDay`, `read_regime_day`) и
//! фильтр касаний в момент касания (`TouchFilter`) — общий для сигналов
//! сетки и замера ёмкости (`lob fill-capacity`). Вынесено из `bounce_grid`
//! при разрезке B3 (ревью 23.09), поведение не менялось.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use crate::book::Side;
use crate::lob::levels::{ApproachRecord, TouchRecord};
use crate::lob::r1::{ArmR1, FLOW_N, R1_UNDEF};

use super::args::{BounceGridArgs, SideArg};
use super::cache::eaten_pct;
use super::drive::DayParams;

/// Артефакты одного набора фильтров.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SetPaths {
    pub name: String,
    pub rounds_path: PathBuf,
    pub forms_path: PathBuf,
}

/// Набор фильтров касаний одной сетки (`--set`, либо флаги команды).
#[derive(Debug, Clone, PartialEq)]
pub struct FilterSet {
    /// Имя подкаталога; пустое — артефакты в `<out-dir>` (без `--set`).
    pub name: String,
    pub frontrun_only: bool,
    pub min_age_secs: Option<i64>,
    /// Г-87: возраст плотности строго меньше (`agemax=<сек>`); `None` — без верхней границы.
    pub max_age_secs: Option<i64>,
    pub min_flow_pct: Option<f64>,
    pub side: Option<SideArg>,
    /// Состояние стены при касании (S1 плана по сторонам, [D 33:57] «не
    /// заходить в разъедания»): доля съедания к касанию `100 × (1 −
    /// size_at_touch / size_max_before)` не больше этого процента. Только
    /// ключ набора `eaten=<%>`, флага у команды нет.
    pub eaten_max_pct: Option<f64>,
    /// T4 (П-02, Г-86, P-90 «огромный завал уже проедается»): доля съедания к
    /// касанию не меньше этого процента — зеркало `eaten_max_pct` (там «не
    /// больше», здесь «не меньше»), для условия входа рынком при сильном
    /// разъедании. Только ключ набора `eaten_min=<%>`.
    pub eaten_min_pct: Option<f64>,
    /// T-35 (Г-85, В-122): фронтран касания не меньше стольких лотов (`frontrun_min=<лоты>`) — сумма лотов той
    /// же стороны строго лучше уровня (`TouchRecord::frontrun_lots`, В-45); порог вместо «да/нет» `frontrun`.
    /// Только ключ набора.
    pub frontrun_min_lots: Option<i64>,
    /// М-04 (В-228, TK-206): множитель размера позиции на первом подходе к уровню (`touch_index == 0`) — `lot_first=<k>`,
    /// k конечное > 0; на остальных подходах 1. Без ключа размер прежний (в шапку не попадает). Только ключ набора.
    pub lot_first: Option<f64>,
    /// Г-98 (П-17): лот под риск — `size_risk=<R$>`, R конечное > 0; лот = min(лот, R·10⁴ ÷ `stack_span_bps_at_arm`), нужны
    /// подходы. Без ключа размер прежний. Только ключ набора.
    pub size_risk: Option<f64>,
    /// Г-81 (П-17): ворота входа — `entry_gate=cxl<W>q<Q>` (W с > 0, Q > 0): на `sent + W` неисполненные ноги лестницы
    /// снимаются, если отмены у стены за `W` ÷ размер стены ≥ Q (мера `cxl<W>q<Q>` Г-116, нужен `--tape-log` ≥ W, сборка r2).
    /// Без ключа вход прежний. Только ключ набора.
    pub entry_gate: Option<(u32, f64)>,
    /// Номинал стены при касании не меньше стольких долларов (`usd_min=`):
    /// ось «размер стены» поверх пола `--h3-usd` (стены ≥ пола — надмножество,
    /// так что ключ — подмножество тех же касаний).
    pub usd_min: Option<f64>,
    /// Г-07 (TK-012): глубина позади стены не меньше стольких процентов её размера (`behind_min=<%>`, целое ≥ 0):
    /// `depth_behind_lots × 100 ≥ pct × size_at_touch` (у подхода — на взводе). Только ключ набора.
    pub behind_min_pct: Option<i64>,
    /// Г-07 (TK-012): уровней той же стороны в стопке не меньше `n` (`stack_min=<n>`, n ≥ 1; `stack_levels`,
    /// у подхода — `stack_levels_at_arm`). Только ключ набора.
    pub stack_min: Option<u32>,
    /// Г-136/Г-137 (П-17): прежних рождений на уровне в окне `repeat_window_ms` не меньше `k` (`repeat_min=<k>`, k ≥ 0;
    /// `TouchRecord::repeat_count`). `repeat_min=0` — контроль Г-136 (пропускает всё, но идентичность набора своя).
    pub repeat_min: Option<u32>,
    /// Г-135 (П-17): прочтение А «закола» — снесённый до касания объём не меньше `q` бп размера стены
    /// (`swept_min=<бп>`, целое ≥ 0): `swept_lots × 10⁴ ≥ q × size_at_touch`. Только ключ набора.
    pub swept_min_bp: Option<i64>,
    /// Г-101 (П-17): шаг лестницы входа ×f на касаниях «сильной» половины внутри допуска B1 (`btc4h ≤ T`):
    /// ключи пары `lreg_t=<bps>` + `lreg_f=<бп>` (f = бп/10⁴ от шага: ¼ = 2500). Без пары — прежний шаг.
    pub ladder_regime: Option<Regime>,
    /// Г-101 (П-17): то же для ширины стопа (`sreg_t`, `sreg_f`; стоп `pct<x>`/`s<a>` умножается на f).
    pub stop_regime: Option<Regime>,
    /// TK-025 (R1): границы колонок пакета R1 на подходе (`r1_<колонка>_min|_max=<целое>`), по
    /// порядку колонок `ArmR1::names()` — порядок в записи `--set` на набор не влияет. Только
    /// ключи набора и только `--signal approach`.
    pub r1: Vec<R1Bound>,
    /// Контекст касания (S4): границы в bps по осям `CTX_AXES` — ход монеты
    /// до касания за 10 мин / 1 ч / 4 ч (`ret10m`, `ret1h`, `ret4h`; знак
    /// абсолютный), медиана пула за 1 ч / 4 ч (`pool1h`, `pool4h`) и биток
    /// (`btc1h`, `btc4h`, промежуточные `btc2h`, `btc3h` — E26); ключи
    /// `<ось>_min=` / `<ось>_max=`. Касание без
    /// значения оси при заданной границе выбывает.
    pub ctx: [Range; CTX_AXES.len()],
}

/// Режим Г-101: на касании с `btc4h ≤ t_bps` параметр формы умножается на `f_bp / 10⁴`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Regime {
    pub t_bps: f64,
    pub f_bp: u32,
}

impl Regime {
    pub(crate) fn factor(self) -> f64 {
        f64::from(self.f_bp) / 10_000.0
    }
}

/// Границы одной колонки R1 (`r1_<колонка>_min|_max`); `col` — номер в `ArmR1::names()`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct R1Bound {
    pub col: usize,
    pub min: Option<i64>,
    pub max: Option<i64>,
}

impl R1Bound {
    /// Значение колонки внутри границ. Нет записи R1 или `R1_UNDEF` — не проходит: «не
    /// определено» не может удовлетворить порогу (как ось контекста без значения).
    pub fn holds(&self, r1: Option<&ArmR1>) -> bool {
        let Some(r1) = r1 else { return false };
        let v = if self.col < FLOW_N {
            r1.flow[self.col]
        } else {
            r1.level[self.col - FLOW_N]
        };
        v != R1_UNDEF && self.min.is_none_or(|m| v >= m) && self.max.is_none_or(|m| v <= m)
    }
}

/// Группа колонок файла режима: `Base` обязательна всегда, `Mid`/`Long` — только если
/// наборам нужны оси группы (в прежних файлах `regime.py` колонок нет).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RegimeGroup {
    Base,
    Mid,
    Long,
}

/// Откуда берётся значение оси контекста.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum CtxSrc {
    /// Ход монеты до касания: слот в `TouchRow::ret_bps` (кэш касаний или реплей).
    Coin(usize),
    /// Колонка файла режима `<сутки>.csv` (`regime.py`); значение — в слоте `RegimeDay`
    /// по порядку осей режима в реестре.
    Regime {
        col: &'static str,
        group: RegimeGroup,
    },
    /// Минутные свечи **монеты** (`--klines-from`, Г-57/Г-08): ось считает `KlineAxis`.
    Klines(KlineAxis),
    /// Ход BTC за последние `lag` секунд до касания (`--btc-seconds`, Г-72), bps: close последней
    /// **закрытой** секунды против close на `lag` секунд раньше.
    BtcSeconds(u32),
    /// Расстояние цены уровня до ближайшего круглого числа — кратного `ROUND_TICKS` тиков
    /// (`round_zeros >= 2`, прецедент P-02 H4), тики: 0…`ROUND_TICKS`/2. Свечи не нужны.
    RoundDist,
}

/// Круглое число — кратное 10² тиков цены (`lob::levels::round_zeros`, j = 2).
const ROUND_TICKS: i64 = 100;

/// Ось по свечам монеты. Окно — только закрытые минуты до минуты касания (без взгляда вперёд).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum KlineAxis {
    /// Диапазон часа (`max high − min low` по 60 минутам до касания) к закрытию последней, bps.
    Rng1h,
    /// Расстояние от цены уровня до минимума суток (UTC) на момент касания, тики (В-131).
    DayLowDist,
}

/// Ось контекста касания: имя в ключах `--set` (`<имя>_min|_max`) и источник значения.
#[derive(Debug, Clone, Copy)]
pub(crate) struct CtxAxisSpec {
    pub name: &'static str,
    pub src: CtxSrc,
}

const fn coin(name: &'static str, slot: usize) -> CtxAxisSpec {
    CtxAxisSpec {
        name,
        src: CtxSrc::Coin(slot),
    }
}

const fn regime(name: &'static str, col: &'static str, group: RegimeGroup) -> CtxAxisSpec {
    CtxAxisSpec {
        name,
        src: CtxSrc::Regime { col, group },
    }
}

const fn btc_lag(name: &'static str, lag_s: u32) -> CtxAxisSpec {
    CtxAxisSpec {
        name,
        src: CtxSrc::BtcSeconds(lag_s),
    }
}

const fn klines(name: &'static str, axis: KlineAxis) -> CtxAxisSpec {
    CtxAxisSpec {
        name,
        src: CtxSrc::Klines(axis),
    }
}

/// Реестр осей контекста — единственное место: порядок общий для `FilterSet::ctx`,
/// `TouchContext` и `RegimeDay`. Оси хода монеты — первые `COIN_AXES`, оси режима —
/// следом, затем оси по свечам монеты и по цене уровня; новая ось дописывается **в конец реестра**
/// (режимные идут перед свечами в раскладке `TouchContext`): индексы прежних осей не сдвигаются.
/// `btc2h`/`btc3h` (E26) и `btc6h`…`btc24h` (Г-10, TK-189) добавлены так же; `btc_lag_78s|91s|104s` — F2 П-15 Г-72 (TK-303:
/// промежутки лага 64…118 с по шагу F1/4, целые секунды).
pub(crate) const CTX_SPECS: [CtxAxisSpec; 27] = [
    coin("ret10m", 0),
    coin("ret1h", 1),
    coin("ret4h", 2),
    regime("pool1h", "pool_ret_1h_bps", RegimeGroup::Base),
    regime("pool4h", "pool_ret_4h_bps", RegimeGroup::Base),
    regime("btc1h", "btc_ret_1h_bps", RegimeGroup::Base),
    regime("btc4h", "btc_ret_4h_bps", RegimeGroup::Base),
    regime("btc2h", "btc_ret_2h_bps", RegimeGroup::Mid),
    regime("btc3h", "btc_ret_3h_bps", RegimeGroup::Mid),
    regime("btc6h", "btc_ret_6h_bps", RegimeGroup::Long),
    regime("btc8h", "btc_ret_8h_bps", RegimeGroup::Long),
    regime("btc12h", "btc_ret_12h_bps", RegimeGroup::Long),
    regime("btc24h", "btc_ret_24h_bps", RegimeGroup::Long),
    klines("rng1h", KlineAxis::Rng1h),
    klines("dlow", KlineAxis::DayLowDist),
    CtxAxisSpec {
        name: "drnd",
        src: CtxSrc::RoundDist,
    },
    btc_lag("btc_lag_1s", 1),
    btc_lag("btc_lag_2s", 2),
    btc_lag("btc_lag_4s", 4),
    btc_lag("btc_lag_8s", 8),
    btc_lag("btc_lag_16s", 16),
    btc_lag("btc_lag_32s", 32),
    btc_lag("btc_lag_64s", 64),
    btc_lag("btc_lag_118s", 118),
    btc_lag("btc_lag_78s", 78),
    btc_lag("btc_lag_91s", 91),
    btc_lag("btc_lag_104s", 104),
];

/// Индекс оси `btc4h` в реестре (Г-101: «сильная» половина режима).
#[allow(clippy::indexing_slicing)] // const: i < len — условие цикла, n[..5] — после n.len() == 5; ошибка была бы на сборке
pub(crate) const BTC4H_AXIS: usize = {
    let mut i = 0;
    while i < CTX_SPECS.len() {
        let n = CTX_SPECS[i].name.as_bytes();
        if n.len() == 5
            && n[0] == b'b'
            && n[1] == b't'
            && n[2] == b'c'
            && n[3] == b'4'
            && n[4] == b'h'
        {
            break;
        }
        i += 1;
    }
    i
};

/// Осей хода монеты (всегда в начале реестра), осей по свечам монеты (всегда в конце) и осей
/// режима (между ними).
pub(crate) const COIN_AXES: usize = 3;
pub(crate) const KLINE_AXES: usize = 2;
/// Осей по цене уровня (`RoundDist`) — в реестре после осей по свечам.
pub(crate) const LEVEL_AXES: usize = 1;
/// Осей по секундному ряду BTC (`BtcSeconds`, Г-72) — последние в реестре.
pub(crate) const SEC_AXES: usize = 8;
pub(crate) const REGIME_AXES: usize =
    CTX_SPECS.len() - COIN_AXES - KLINE_AXES - LEVEL_AXES - SEC_AXES;

/// Имена осей в порядке реестра.
#[allow(clippy::indexing_slicing)] // i < CTX_SPECS.len() — условие цикла
pub const CTX_AXES: [&str; CTX_SPECS.len()] = {
    let mut names = [""; CTX_SPECS.len()];
    let mut i = 0;
    while i < CTX_SPECS.len() {
        names[i] = CTX_SPECS[i].name;
        i += 1;
    }
    names
};

/// Границы одной оси контекста (bps; `dlow`/`drnd` — тики); `None` — не задана.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct Range {
    pub min: Option<f64>,
    pub max: Option<f64>,
}

impl Range {
    pub(crate) fn is_set(self) -> bool {
        self.min.is_some() || self.max.is_some()
    }

    pub(super) fn holds(self, v: Option<f64>) -> bool {
        if !self.is_set() {
            return true;
        }
        match v {
            None => false,
            Some(x) => self.min.is_none_or(|m| x >= m) && self.max.is_none_or(|m| x <= m),
        }
    }
}

/// Контекст одного касания в порядке `CTX_AXES`; `None` — значения нет
/// (окно за началом записи, нет режима на минуту).
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct TouchContext {
    pub axes: [Option<f64>; CTX_AXES.len()],
}

/// Режим суток по минутам из `regime.py`:
/// `minute_ms →` значения осей режима в порядке реестра `CTX_SPECS` (после осей хода монеты).
pub(crate) type RegimeDay = BTreeMap<i64, [Option<f64>; REGIME_AXES]>;

/// Колонки `btc_ret_2h_bps`/`btc_ret_3h_bps` пишет только `regime.py` после E26: в
/// прежних файлах их нет, и значения — `None`. Если оси нужны наборам (`need_mid`),
/// нет колонки — отказ (иначе все касания молча выбыли бы из набора).
#[allow(clippy::indexing_slicing)] // slot < REGIME_AXES (число осей режима в реестре), k — индекс по cols
pub(crate) fn read_regime_day(
    dir: &Path,
    day: &str,
    need_mid: bool,
    need_long: bool,
) -> anyhow::Result<RegimeDay> {
    let path = dir.join(format!("{day}.csv"));
    let mut r = csv::ReaderBuilder::new()
        .from_path(&path)
        .map_err(|e| anyhow::anyhow!("--regime-from: {}: {e}", path.display()))?;
    let header = r.headers()?.clone();
    let col = |name: &str| header.iter().position(|h| h == name);
    let minute_col = col("minute_ms")
        .ok_or_else(|| anyhow::anyhow!("{}: нет колонки minute_ms", path.display()))?;
    let mut cols = [None; REGIME_AXES];
    let regime_specs = CTX_SPECS.iter().filter_map(|a| match a.src {
        CtxSrc::Regime { col, group } => Some((col, group)),
        CtxSrc::Coin(_) | CtxSrc::Klines(_) | CtxSrc::RoundDist | CtxSrc::BtcSeconds(_) => None,
    });
    for (slot, (name, group)) in regime_specs.enumerate() {
        let need = match group {
            RegimeGroup::Base => true,
            RegimeGroup::Mid => need_mid,
            RegimeGroup::Long => need_long,
        };
        cols[slot] = match col(name) {
            Some(i) => Some(i),
            None if need && group == RegimeGroup::Base => {
                anyhow::bail!("{}: нет колонки {name}", path.display())
            }
            None if need => anyhow::bail!(
                "{}: нет колонки {name} — оси btc2h…btc24h требуют режим, пересчитанный regime.py",
                path.display()
            ),
            None => None,
        };
    }
    let mut out = RegimeDay::new();
    for rec in r.records() {
        let rec = rec?;
        let minute: i64 = rec
            .get(minute_col)
            .ok_or_else(|| anyhow::anyhow!("{}: короткая строка", path.display()))?
            .parse()?;
        let mut vals = [None; REGIME_AXES];
        for (k, c) in cols.iter().enumerate() {
            let Some(c) = c else { continue };
            let v = rec.get(*c).unwrap_or("");
            vals[k] = if v.is_empty() {
                None
            } else {
                Some(v.parse::<f64>()?)
            };
        }
        out.insert(minute, vals);
    }
    Ok(out)
}

/// Тики от цены уровня до ближайшего кратного `ROUND_TICKS`.
#[allow(clippy::cast_precision_loss)] // значение < ROUND_TICKS
pub(crate) fn round_dist(price_tick: i64) -> f64 {
    let r = price_tick.rem_euclid(ROUND_TICKS);
    r.min(ROUND_TICKS - r) as f64
}

/// Минутные свечи монеты (`ref-<СИМВОЛ>-1m.csv` по месяцам): `minute_ms → (high, low, close)`.
#[derive(Debug, Default)]
pub(crate) struct KlineSeries {
    bars: BTreeMap<i64, (f64, f64, f64)>,
    months: std::collections::BTreeSet<usize>,
}

const MONTH_ABBR: [&str; 12] = [
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
];

impl KlineSeries {
    /// Подгружает месяц суток `day` (`ГГГГ-ММ-ДД`) и предыдущий (окно часа в начале месяца);
    /// файл месяца `<dir>/<абр>/ref-<символ>-1m.csv`. Нет файла текущего месяца — отказ,
    /// предыдущего — окно у границы просто неполное (`None`).
    pub(crate) fn ensure_day(&mut self, dir: &Path, symbol: &str, day: &str) -> anyhow::Result<()> {
        let month: usize = day
            .get(5..7)
            .and_then(|m| m.parse().ok())
            .filter(|m| (1..=12).contains(m))
            .ok_or_else(|| anyhow::anyhow!("--klines-from: сутки {day} не ГГГГ-ММ-ДД"))?;
        for (m, must) in [(month, true), ((month + 10) % 12 + 1, false)] {
            if self.months.contains(&m) {
                continue;
            }
            let Some(abbr) = MONTH_ABBR.get(m - 1) else {
                continue;
            };
            let path = dir.join(abbr).join(format!("ref-{symbol}-1m.csv"));
            if !path.is_file() {
                anyhow::ensure!(!must, "--klines-from: нет {}", path.display());
                continue;
            }
            self.read_file(&path)?;
            self.months.insert(m);
        }
        Ok(())
    }

    fn read_file(&mut self, path: &Path) -> anyhow::Result<()> {
        let mut r = csv::ReaderBuilder::new().from_path(path)?;
        let header = r.headers()?.clone();
        let col = |name: &str| {
            header
                .iter()
                .position(|h| h == name)
                .ok_or_else(|| anyhow::anyhow!("{}: нет колонки {name}", path.display()))
        };
        let (cm, ch, cl, cc) = (col("minute_ms")?, col("high")?, col("low")?, col("close")?);
        for rec in r.records() {
            let rec = rec?;
            let f = |c: usize| rec.get(c).unwrap_or("").parse::<f64>();
            self.bars
                .insert(rec.get(cm).unwrap_or("").parse()?, (f(ch)?, f(cl)?, f(cc)?));
        }
        Ok(())
    }

    /// Значение оси на минуту касания `minute` (начало минуты, мс); `price_tick` — цена уровня
    /// в тиках, `tick` — шаг цены монеты.
    /// Нет полного окна (60 минут) или минут суток до касания — `None`.
    #[allow(clippy::cast_precision_loss, clippy::cast_possible_truncation)] // тики < 2^53
    pub(crate) fn axis(
        &self,
        axis: KlineAxis,
        minute: i64,
        price_tick: i64,
        tick: f64,
    ) -> Option<f64> {
        const MIN: i64 = 60_000;
        match axis {
            KlineAxis::Rng1h => {
                let (mut hi, mut lo, mut n, mut close) = (f64::MIN, f64::MAX, 0, 0.0);
                for (_, &(h, l, c)) in self.bars.range(minute - 60 * MIN..minute) {
                    hi = hi.max(h);
                    lo = lo.min(l);
                    close = c;
                    n += 1;
                }
                (n == 60 && close > 0.0).then(|| (hi - lo) / close * 1e4)
            }
            KlineAxis::DayLowDist => {
                let day_start = minute - minute.rem_euclid(86_400_000);
                let low = self
                    .bars
                    .range(day_start..minute)
                    .map(|(_, &(_, l, _))| l)
                    .reduce(f64::min)?;
                // цена на бирже кратна шагу (В-172): минимум — в целых тиках, без дроби f64
                (tick > 0.0).then(|| (price_tick - (low / tick).round() as i64) as f64)
            }
        }
    }
}

/// Секундный ряд BTC (`--btc-seconds <каталог>`, файлы `<сутки>.csv`: `second_ms,close_ticks`,
/// 86 400 строк): `начало суток (мс) → close секунды в тиках`. Окно у границы суток берёт и
/// предыдущие сутки; нет их — окно неполное (`None`).
#[derive(Debug, Default)]
pub(crate) struct BtcSeconds {
    days: BTreeMap<i64, Vec<i64>>,
}

const DAY_MS: i64 = 86_400_000;

impl BtcSeconds {
    /// Подгружает сутки `day` (обязательно) и предыдущие (если файл есть).
    pub(crate) fn ensure_day(&mut self, dir: &Path, day: &str) -> anyhow::Result<()> {
        let start = super::carry::day_start_ns(day)?.div_euclid(1_000_000);
        self.days.retain(|&k, _| k >= start - DAY_MS);
        for (ms, must) in [(start, true), (start - DAY_MS, false)] {
            if self.days.contains_key(&ms) {
                continue;
            }
            let name = day_name(ms)?;
            let path = dir.join(format!("{name}.csv"));
            if !path.is_file() {
                anyhow::ensure!(!must, "--btc-seconds: нет {}", path.display());
                continue;
            }
            let v = Self::read_file(&path, ms)?;
            self.days.insert(ms, v);
        }
        Ok(())
    }

    fn read_file(path: &Path, day_ms: i64) -> anyhow::Result<Vec<i64>> {
        let text = std::fs::read_to_string(path)?;
        let mut v = Vec::with_capacity(86_400);
        for (n, line) in text.lines().enumerate().skip(1) {
            let (ms, c) = line.split_once(',').ok_or_else(|| {
                anyhow::anyhow!("{}: строка {}: нет запятой", path.display(), n + 1)
            })?;
            let ms: i64 = ms.trim().parse()?;
            anyhow::ensure!(
                ms == day_ms + 1000 * i64::try_from(v.len())?,
                "{}: строка {}: секунда {ms} не по порядку",
                path.display(),
                n + 1
            );
            v.push(c.trim().parse()?);
        }
        anyhow::ensure!(
            v.len() == 86_400,
            "{}: {} строк, нужно 86400",
            path.display(),
            v.len()
        );
        Ok(v)
    }

    fn close_at(&self, sec_ms: i64) -> Option<i64> {
        let day = sec_ms - sec_ms.rem_euclid(DAY_MS);
        let i = usize::try_from((sec_ms - day) / 1000).ok()?;
        self.days.get(&day)?.get(i).copied().filter(|&c| c > 0)
    }

    /// Ход BTC за `lag_s` секунд до последней закрытой секунды перед касанием `start_ms`, bps.
    #[allow(clippy::cast_precision_loss)] // тики BTC < 2^53
    pub(crate) fn lag_bps(&self, lag_s: u32, start_ms: i64) -> Option<f64> {
        let end = start_ms - start_ms.rem_euclid(1000) - 1000;
        let (c1, c0) = (
            self.close_at(end)?,
            self.close_at(end - 1000 * i64::from(lag_s))?,
        );
        Some((c1 - c0) as f64 / c0 as f64 * 1e4)
    }
}

/// `ГГГГ-ММ-ДД` по началу суток UTC в мс.
fn day_name(day_ms: i64) -> anyhow::Result<String> {
    let dt = chrono::DateTime::from_timestamp_millis(day_ms)
        .ok_or_else(|| anyhow::anyhow!("сутки {day_ms} мс вне диапазона"))?;
    Ok(dt.format("%Y-%m-%d").to_string())
}

/// Контекст касаний суток: ход монеты — из кэша (`TouchRow::ret_bps`) или из
/// реплея (`pre_touch_return_bps_csv` — те же шесть знаков), режим — по
/// минуте `start_ms` из `RegimeDay` (нет режима — `None`).
#[allow(clippy::indexing_slicing)] // slot < COIN_AXES (реестр), срез COIN_AXES..+REGIME_AXES лежит в CTX_SPECS.len()
pub(crate) fn touch_contexts(
    rets: &[[Option<f64>; 3]],
    touches: &[TouchRecord],
    regime: Option<&RegimeDay>,
    klines: Option<(&KlineSeries, f64)>,
    btc_sec: Option<&BtcSeconds>,
) -> Vec<TouchContext> {
    debug_assert_eq!(rets.len(), touches.len());
    touches
        .iter()
        .zip(rets)
        .map(|(t, r)| {
            let minute = t.start_ms - t.start_ms.rem_euclid(60_000);
            let m = regime
                .and_then(|g| g.get(&minute))
                .copied()
                .unwrap_or([None; REGIME_AXES]);
            let mut axes = [None; CTX_SPECS.len()];
            for (a, spec) in axes.iter_mut().zip(&CTX_SPECS) {
                *a = match spec.src {
                    CtxSrc::Coin(slot) => r[slot],
                    CtxSrc::Regime { .. } => None,
                    CtxSrc::Klines(axis) => {
                        klines.and_then(|(k, tick)| k.axis(axis, minute, t.price_tick, tick))
                    }
                    CtxSrc::RoundDist => Some(round_dist(t.price_tick)),
                    CtxSrc::BtcSeconds(lag) => btc_sec.and_then(|b| b.lag_bps(lag, t.start_ms)),
                };
            }
            axes[COIN_AXES..COIN_AXES + REGIME_AXES].copy_from_slice(&m);
            TouchContext { axes }
        })
        .collect()
}

impl FilterSet {
    /// Хоть один ключ контекста задан.
    pub(super) fn uses_ctx(&self) -> bool {
        self.ctx.iter().any(|r| r.is_set())
    }

    /// Хоть один ключ пакета R1 (`r1_*`) задан — нужен кэш подходов с колонками R1.
    pub(crate) fn uses_r1(&self) -> bool {
        !self.r1.is_empty()
    }

    /// Задана граница хоть одной оси режима: `group = None` — любой группы, иначе только этой.
    fn uses_group(&self, group: Option<RegimeGroup>) -> bool {
        CTX_SPECS.iter().zip(&self.ctx).any(|(a, r)| {
            r.is_set()
                && matches!(a.src, CtxSrc::Regime { group: g, .. } if group.is_none_or(|w| w == g))
        })
    }

    /// Хоть один ключ режима (`pool*`/`btc*`) задан — нужен `--regime-from`.
    pub(crate) fn uses_regime(&self) -> bool {
        self.uses_group(None)
    }

    /// Хоть один ключ промежуточных осей битка (`btc2h`/`btc3h`) задан — в файлах
    /// режима нужны их колонки (`read_regime_day`, `need_mid`).
    pub(crate) fn uses_btc_mid(&self) -> bool {
        self.uses_group(Some(RegimeGroup::Mid))
    }

    /// Хоть один ключ длинных осей битка (`btc6h`…`btc24h`, Г-10) задан — нужны их колонки.
    pub(crate) fn uses_btc_long(&self) -> bool {
        self.uses_group(Some(RegimeGroup::Long))
    }

    /// Хоть один ключ по свечам монеты (`rng1h`/`dlow`) задан — нужен `--klines-from`.
    pub(crate) fn uses_klines(&self) -> bool {
        CTX_SPECS
            .iter()
            .zip(&self.ctx)
            .any(|(a, r)| r.is_set() && matches!(a.src, CtxSrc::Klines(_)))
    }

    /// Хоть один ключ секундного ряда BTC (`btc_lag_<s>s`, Г-72) задан — нужен `--btc-seconds`.
    pub(crate) fn uses_btc_seconds(&self) -> bool {
        CTX_SPECS
            .iter()
            .zip(&self.ctx)
            .any(|(a, r)| r.is_set() && matches!(a.src, CtxSrc::BtcSeconds(_)))
    }

    /// Хоть один ключ хода **монеты** до сигнала (`ret10m`/`ret1h`/`ret4h`)
    /// задан — их несёт только кэш касаний (`TouchRow::ret_bps`).
    pub(crate) fn uses_ret(&self) -> bool {
        self.ctx[..COIN_AXES].iter().any(|r| r.is_set())
    }

    /// Ключи контекста набора для шапки: `ret1h_min=… pool4h_max=…`.
    pub(super) fn ctx_label(&self) -> String {
        let mut parts = Vec::new();
        for (name, r) in CTX_AXES.iter().zip(&self.ctx) {
            if let Some(v) = r.min {
                parts.push(format!("{name}_min={v}"));
            }
            if let Some(v) = r.max {
                parts.push(format!("{name}_max={v}"));
            }
        }
        if parts.is_empty() {
            "none".to_string()
        } else {
            parts.join(",")
        }
    }

    pub(super) fn from_args(args: &BounceGridArgs) -> Self {
        Self {
            name: String::new(),
            frontrun_only: args.frontrun_only,
            min_age_secs: args.min_age_secs,
            max_age_secs: None,
            min_flow_pct: args.min_flow_pct,
            side: args.side,
            eaten_max_pct: None,
            eaten_min_pct: None,
            frontrun_min_lots: None,
            lot_first: None,
            size_risk: None,
            entry_gate: None,
            usd_min: None,
            behind_min_pct: None,
            stack_min: None,
            repeat_min: None,
            swept_min_bp: None,
            ladder_regime: None,
            stop_regime: None,
            r1: Vec::new(),
            ctx: [Range::default(); CTX_AXES.len()],
        }
    }

    /// `<имя>:<k=v,…>` — см. `BounceGridArgs::sets`. Повтор ключа и чужой
    /// ключ — отказ: набор пишется один раз и читается людьми.
    pub fn parse(spec: &str) -> anyhow::Result<Self> {
        let (name, rest) = spec
            .split_once(':')
            .ok_or_else(|| anyhow::anyhow!("--set {spec:?}: ожидается <имя>:<k=v,…>"))?;
        anyhow::ensure!(
            !name.is_empty()
                && name
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
                && name != "."
                && name != "..",
            "--set {spec:?}: имя набора — буквы, цифры, `.`, `_`, `-`"
        );
        let mut set = Self {
            name: name.to_string(),
            frontrun_only: false,
            min_age_secs: None,
            max_age_secs: None,
            min_flow_pct: None,
            side: None,
            eaten_max_pct: None,
            eaten_min_pct: None,
            frontrun_min_lots: None,
            lot_first: None,
            size_risk: None,
            entry_gate: None,
            usd_min: None,
            behind_min_pct: None,
            stack_min: None,
            repeat_min: None,
            swept_min_bp: None,
            ladder_regime: None,
            stop_regime: None,
            r1: Vec::new(),
            ctx: [Range::default(); CTX_AXES.len()],
        };
        let mut seen = std::collections::BTreeSet::new();
        let (mut reg_t, mut reg_f) = ([None::<f64>; 2], [None::<u32>; 2]);
        for kv in rest.split(',').filter(|s| !s.is_empty()) {
            let (k, v) = kv.split_once('=').unwrap_or((kv, ""));
            anyhow::ensure!(
                seen.insert(k.to_string()),
                "--set {spec:?}: ключ {k} повторяется"
            );
            match k {
                "age" => {
                    set.min_age_secs = Some(
                        v.parse()
                            .map_err(|e| anyhow::anyhow!("--set {spec:?}: age={v:?}: {e}"))?,
                    );
                }
                "agemax" => {
                    set.max_age_secs = Some(
                        v.parse()
                            .map_err(|e| anyhow::anyhow!("--set {spec:?}: agemax={v:?}: {e}"))?,
                    );
                }
                "flow" => {
                    set.min_flow_pct = Some(
                        v.parse()
                            .map_err(|e| anyhow::anyhow!("--set {spec:?}: flow={v:?}: {e}"))?,
                    );
                }
                "side" => {
                    set.side = Some(match v {
                        "bid" => SideArg::Bid,
                        "ask" => SideArg::Ask,
                        _ => anyhow::bail!("--set {spec:?}: side={v:?}, ожидается bid|ask"),
                    });
                }
                "usd_min" => {
                    let usd: f64 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: usd_min={v:?}: {e}"))?;
                    anyhow::ensure!(
                        usd.is_finite() && usd >= 0.0,
                        "--set {spec:?}: usd_min={v:?} — доллары обязаны быть неотрицательным числом"
                    );
                    set.usd_min = Some(usd);
                }
                "behind_min" => {
                    let pct: i64 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: behind_min={v:?}: {e}"))?;
                    anyhow::ensure!(
                        pct >= 0,
                        "--set {spec:?}: behind_min={v:?} — целый процент ≥ 0"
                    );
                    set.behind_min_pct = Some(pct);
                }
                "stack_min" => {
                    let n: u32 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: stack_min={v:?}: {e}"))?;
                    anyhow::ensure!(
                        n >= 1,
                        "--set {spec:?}: stack_min={v:?} — целое число уровней ≥ 1"
                    );
                    set.stack_min = Some(n);
                }
                "repeat_min" => {
                    set.repeat_min =
                        Some(v.parse().map_err(|e| {
                            anyhow::anyhow!("--set {spec:?}: repeat_min={v:?}: {e}")
                        })?);
                }
                "swept_min" => {
                    let q: i64 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: swept_min={v:?}: {e}"))?;
                    anyhow::ensure!(q >= 0, "--set {spec:?}: swept_min={v:?} — целые бп ≥ 0");
                    set.swept_min_bp = Some(q);
                }
                "lreg_t" | "sreg_t" => {
                    let t: f64 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: {k}={v:?}: {e}"))?;
                    anyhow::ensure!(
                        t.is_finite(),
                        "--set {spec:?}: {k}={v:?} — порог bps обязан быть числом"
                    );
                    if k == "lreg_t" {
                        reg_t[0] = Some(t)
                    } else {
                        reg_t[1] = Some(t)
                    }
                }
                "lreg_f" | "sreg_f" => {
                    let f: u32 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: {k}={v:?}: {e}"))?;
                    anyhow::ensure!(
                        f >= 1,
                        "--set {spec:?}: {k}={v:?} — множитель в бп ≥ 1 (10000 = ×1, это B1)"
                    );
                    if k == "lreg_f" {
                        reg_f[0] = Some(f)
                    } else {
                        reg_f[1] = Some(f)
                    }
                }
                "eaten" => {
                    let pct: f64 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: eaten={v:?}: {e}"))?;
                    anyhow::ensure!(
                        pct.is_finite(),
                        "--set {spec:?}: eaten={v:?} — процент обязан быть числом"
                    );
                    set.eaten_max_pct = Some(pct);
                }
                "eaten_min" => {
                    let pct: f64 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: eaten_min={v:?}: {e}"))?;
                    anyhow::ensure!(
                        pct.is_finite(),
                        "--set {spec:?}: eaten_min={v:?} — процент обязан быть числом"
                    );
                    set.eaten_min_pct = Some(pct);
                }
                "frontrun_min" => {
                    let n: i64 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: frontrun_min={v:?}: {e}"))?;
                    anyhow::ensure!(
                        n >= 1,
                        "--set {spec:?}: frontrun_min={v:?} — целое число лотов ≥ 1"
                    );
                    set.frontrun_min_lots = Some(n);
                }
                "lot_first" => {
                    let k: f64 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: lot_first={v:?}: {e}"))?;
                    anyhow::ensure!(
                        k.is_finite() && k > 0.0,
                        "--set {spec:?}: lot_first={v:?} — множитель размера: конечное число > 0"
                    );
                    set.lot_first = Some(k);
                }
                "size_risk" => {
                    let r: f64 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: size_risk={v:?}: {e}"))?;
                    anyhow::ensure!(
                        r.is_finite() && r > 0.0,
                        "--set {spec:?}: size_risk={v:?} — риск на сделку, $: конечное число > 0"
                    );
                    set.size_risk = Some(r);
                }
                "entry_gate" => {
                    let bad = || {
                        anyhow::anyhow!(
                            "--set {spec:?}: entry_gate={v:?} — ожидается cxl<W>q<Q>: окно W > 0 с, порог Q > 0"
                        )
                    };
                    let (ws, qs) = v
                        .strip_prefix("cxl")
                        .and_then(|r| r.split_once('q'))
                        .ok_or_else(bad)?;
                    let w: u32 = ws.parse().map_err(|_| bad())?;
                    // `qprobe` — проба порогов: порог недостижим (ворота не закрываются, ход B1 прежний), мера пишется
                    // в `gate-probe.csv`; в идентичности набора — `qprobe`.
                    let q: f64 = if qs == "probe" {
                        f64::INFINITY
                    } else {
                        let q: f64 = qs.parse().map_err(|_| bad())?;
                        anyhow::ensure!(q.is_finite() && q > 0.0, "{}", bad());
                        q
                    };
                    anyhow::ensure!(w > 0, "{}", bad());
                    set.entry_gate = Some((w, q));
                }
                "frontrun" => {
                    anyhow::ensure!(
                        v.is_empty() || v == "1" || v == "true",
                        "--set {spec:?}: frontrun без значения (или =1)"
                    );
                    set.frontrun_only = true;
                }
                // TK-025: `r1_<колонка>_min|_max=<целое>`; раньше `_`, иначе ключ ушёл бы в оси контекста.
                _ if k.starts_with("r1_") => set.r1_key(spec, k, v)?,
                _ => {
                    let (axis, bound) = k
                        .rsplit_once('_')
                        .ok_or_else(|| anyhow::anyhow!("--set {spec:?}: неизвестный ключ {k:?} (age|flow|side|frontrun|frontrun_min|repeat_min|swept_min|lreg_t|lreg_f|sreg_t|sreg_f|size_risk|entry_gate|lot_first|eaten|eaten_min|usd_min|behind_min|stack_min|<ось>_min|<ось>_max)"))?;
                    let i = CTX_AXES.iter().position(|a| *a == axis).ok_or_else(|| {
                        anyhow::anyhow!(
                            "--set {spec:?}: неизвестная ось {axis:?} ({})",
                            CTX_AXES.join("|")
                        )
                    })?;
                    let v: f64 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: {k}={v:?}: {e}"))?;
                    anyhow::ensure!(
                        v.is_finite(),
                        "--set {spec:?}: {k}={v:?} — bps обязаны быть числом"
                    );
                    match bound {
                        "min" => set.ctx[i].min = Some(v),
                        "max" => set.ctx[i].max = Some(v),
                        _ => anyhow::bail!(
                            "--set {spec:?}: {k:?} — ожидается <ось>_min или <ось>_max"
                        ),
                    }
                }
            }
        }
        set.r1.sort_unstable_by_key(|b| b.col);
        // Г-101: порог и множитель — парой; режим живёт внутри допуска B1 (ось btc4h в наборе обязана быть задана,
        // иначе контекст не грузится и «сильная половина» не определена).
        for (slot, name) in ["lreg", "sreg"].into_iter().enumerate() {
            #[allow(clippy::indexing_slicing)] // slot ∈ {0, 1}, reg_t/reg_f — массивы по 2
            let one = match (reg_t[slot], reg_f[slot]) {
                (None, None) => None,
                (Some(t_bps), Some(f_bp)) => Some(Regime { t_bps, f_bp }),
                _ => anyhow::bail!("--set {spec:?}: {name}_t и {name}_f — только парой"),
            };
            if one.is_some() {
                anyhow::ensure!(
                    set.ctx.get(BTC4H_AXIS).is_some_and(|r| r.is_set()),
                    "--set {spec:?}: {name}_t/{name}_f работают внутри допуска B1 — задай btc4h_min/btc4h_max"
                );
            }
            if slot == 0 {
                set.ladder_regime = one
            } else {
                set.stop_regime = one
            }
        }
        Ok(set)
    }

    /// Ключ `r1_<колонка>_min|_max=<целое>`: колонка — из `ArmR1::names()`, граница — целое, не
    /// `R1_UNDEF`; `min > max` пустил бы набор молча — отказ.
    fn r1_key(&mut self, spec: &str, k: &str, v: &str) -> anyhow::Result<()> {
        let rest = k.strip_prefix("r1_").unwrap_or(k);
        let (name, bound) = rest.rsplit_once('_').unwrap_or((rest, ""));
        anyhow::ensure!(
            matches!(bound, "min" | "max"),
            "--set {spec:?}: {k:?} — ожидается r1_<колонка>_min или r1_<колонка>_max"
        );
        let col = ArmR1::names().position(|n| n == name).ok_or_else(|| {
            anyhow::anyhow!(
                "--set {spec:?}: неизвестная колонка R1 {name:?} в {k:?} (колонки: {})",
                ArmR1::names().collect::<Vec<_>>().join("|")
            )
        })?;
        let n: i64 = v
            .parse()
            .map_err(|e| anyhow::anyhow!("--set {spec:?}: {k}={v:?}: {e}"))?;
        anyhow::ensure!(
            n != R1_UNDEF,
            "--set {spec:?}: {k}={v:?} — «не определено» порогом быть не может"
        );
        let at = match self.r1.iter().position(|b| b.col == col) {
            Some(i) => i,
            None => {
                self.r1.push(R1Bound {
                    col,
                    min: None,
                    max: None,
                });
                self.r1.len() - 1
            }
        };
        let b = &mut self.r1[at];
        if bound == "min" {
            b.min = Some(n);
        } else {
            b.max = Some(n);
        }
        anyhow::ensure!(
            b.min.zip(b.max).is_none_or(|(lo, hi)| lo <= hi),
            "--set {spec:?}: r1_{name}_min > r1_{name}_max — набор был бы пуст"
        );
        Ok(())
    }
}

/// Фильтр касаний набора в момент касания — один и тот же для сигналов сетки
/// (`signals_for`) и для замера ёмкости (`lob fill-capacity`, S10): порог
/// плотности, фронтран, возраст, сила «×поток», сторона, съедание, номинал,
/// контекст. Касание, не прошедшее фильтр, — «пропуск» у вызывающего.
pub(crate) struct TouchFilter<'a> {
    pub(crate) tick: f64,
    pub(crate) lot: f64,
    pub(crate) spec: FilterSpec<'a>,
    /// `holds_at_touch(t) == Some(true)` по касаниям суток, посчитано один раз на все наборы.
    pub(crate) holds: Option<&'a [bool]>,
    /// Скаляры касаний суток для `admits` без чтения `TouchRecord` (нужен `holds`).
    pub(crate) rows: Option<&'a [AdmitRow]>,
}

/// Условия набора `FilterSet` в виде, который читает горячий путь: единицы приведены (секунды → мс,
/// `SideArg` → `Side`), границы R1 и контекст заимствованы. Одна структура на `DayParams` и `TouchFilter`
/// (раньше поля копировались в каждую из трёх).
#[derive(Debug, Clone, Copy)]
pub(crate) struct FilterSpec<'a> {
    pub(crate) frontrun_only: bool,
    pub(crate) min_age_ms: Option<i64>,
    pub(crate) max_age_ms: Option<i64>,
    pub(crate) min_flow_pct: Option<f64>,
    pub(crate) side: Option<Side>,
    pub(crate) eaten_max_pct: Option<f64>,
    pub(crate) eaten_min_pct: Option<f64>,
    pub(crate) frontrun_min_lots: Option<i64>,
    pub(crate) usd_min: Option<f64>,
    pub(crate) behind_min_pct: Option<i64>,
    pub(crate) stack_min: Option<u32>,
    pub(crate) repeat_min: Option<u32>,
    pub(crate) swept_min_bp: Option<i64>,
    pub(crate) ladder_regime: Option<Regime>,
    pub(crate) stop_regime: Option<Regime>,
    /// Г-81: ворота входа `(W, Q)` — в план сигнала (`PyramidCfg`), не фильтр касаний.
    pub(crate) entry_gate: Option<(u32, f64)>,
    /// TK-025: границы колонок R1 набора; проверяются `admits_r1` на записи подхода.
    pub(crate) r1: &'a [R1Bound],
    pub(crate) ctx: Option<&'a [TouchContext]>,
    pub(crate) ctx_ranges: [Range; CTX_AXES.len()],
}

impl<'a> FilterSpec<'a> {
    /// Г-101: множитель режима `r` на касании `ti`, если его `btc4h ≤ T` («сильная» половина); иначе `None`.
    pub(crate) fn regime_factor(&self, r: Option<Regime>, ti: usize) -> Option<f64> {
        let r = r?;
        let v = self.ctx?.get(ti)?.axes.get(BTC4H_AXIS).copied().flatten()?;
        (v <= r.t_bps).then(|| r.factor())
    }

    /// Контекст подаётся, только если у набора есть ключи контекста.
    pub(crate) fn of_set(set: &'a FilterSet, ctx: &'a [TouchContext]) -> Self {
        Self {
            frontrun_only: set.frontrun_only,
            min_age_ms: set.min_age_secs.map(|s| s.saturating_mul(1_000)),
            max_age_ms: set.max_age_secs.map(|s| s.saturating_mul(1_000)),
            min_flow_pct: set.min_flow_pct,
            side: set.side.map(Side::from),
            eaten_max_pct: set.eaten_max_pct,
            eaten_min_pct: set.eaten_min_pct,
            frontrun_min_lots: set.frontrun_min_lots,
            usd_min: set.usd_min,
            behind_min_pct: set.behind_min_pct,
            stack_min: set.stack_min,
            repeat_min: set.repeat_min,
            swept_min_bp: set.swept_min_bp,
            ladder_regime: set.ladder_regime,
            stop_regime: set.stop_regime,
            entry_gate: set.entry_gate,
            r1: &set.r1,
            ctx: if set.uses_ctx() { Some(ctx) } else { None },
            ctx_ranges: set.ctx,
        }
    }
}

/// Поля `TouchRecord`, нужные `TouchFilter::admits`, плотной строкой (~72 Б против ≈300 Б записи).
#[derive(Debug, Clone, Copy)]
pub(crate) struct AdmitRow {
    frontrun_some: bool,
    side: Side,
    frontrun_lots: i64,
    age_ms: i64,
    flow_1h_lots: i64,
    size_at_touch: i64,
    price_tick: i64,
    depth_behind_lots: i64,
    stack_levels: u32,
    repeat_count: u32,
    swept_lots: i64,
    eaten_pct: f64,
}

impl AdmitRow {
    pub(crate) fn of(t: &TouchRecord) -> Self {
        Self {
            frontrun_some: t.frontrun_tick.is_some(),
            side: t.side,
            frontrun_lots: t.frontrun_lots,
            age_ms: t.age_ms(),
            flow_1h_lots: t.flow_1h_lots,
            size_at_touch: t.size_at_touch,
            price_tick: t.price_tick,
            depth_behind_lots: t.depth_behind_lots,
            stack_levels: t.stack_levels,
            repeat_count: t.repeat_count,
            swept_lots: t.swept_lots,
            eaten_pct: eaten_pct(t),
        }
    }
}

impl<'a> TouchFilter<'a> {
    pub(super) fn from_day(p: &DayParams<'a>) -> Self {
        Self {
            tick: p.tick,
            lot: p.lot,
            spec: p.filter,
            holds: p.holds,
            rows: p.rows,
        }
    }

    /// Фильтр набора над касаниями суток с готовым контекстом (`touch_contexts`).
    pub(crate) fn from_set(
        set: &'a FilterSet,
        tick: f64,
        lot: f64,
        ctx: &'a [TouchContext],
    ) -> Self {
        Self {
            tick,
            lot,
            spec: FilterSpec::of_set(set, ctx),
            holds: None,
            rows: None,
        }
    }

    /// Границы R1 набора на записи подхода `a` (параллельна касаниям суток при
    /// `--signal approach`). Без ключей — всегда да; нет записи или R1 — нет.
    pub(crate) fn admits_r1(&self, a: Option<&ApproachRecord>) -> bool {
        let r1 = a.and_then(|a| a.r1.as_deref());
        self.spec.r1.iter().all(|b| b.holds(r1))
    }

    /// Проходит ли касание `ti` суток (индекс — в контекст, `holds` и `rows` суток заданы вызывающим).
    pub(crate) fn admits(&self, ti: usize) -> bool {
        let rows = self.rows.expect("admits: rows суток не заданы");
        self.admits_row(ti, &rows[ti])
    }

    /// Оракул `admits` для тестов: те же условия по полной `TouchRecord`, `holds` считается на месте.
    #[cfg(test)]
    #[allow(clippy::cast_precision_loss)]
    pub(crate) fn admits_oracle(
        &self,
        mode: crate::lob::levels::H3Mode,
        ti: usize,
        t: &TouchRecord,
    ) -> bool {
        if self.spec.frontrun_only && t.frontrun_tick.is_none() {
            return false;
        }
        if self
            .spec
            .frontrun_min_lots
            .is_some_and(|n| t.frontrun_lots < n)
        {
            return false;
        }
        // База E1 (В-66): плотность обязана держать порог при подходе цены,
        // а не только при рождении — иначе в сетку идут касания
        // «бывших» плотностей. Окно проверено при старте прогона.
        if mode.holds_at_touch(t) != Some(true) {
            return false;
        }
        if self.spec.min_age_ms.is_some_and(|n| t.age_ms() < n) {
            return false;
        }
        if self.spec.max_age_ms.is_some_and(|n| t.age_ms() >= n) {
            return false;
        }
        if let Some(s_min) = self.spec.min_flow_pct {
            let flow_ok = t.flow_1h_lots > 0
                && t.size_at_touch as f64 / t.flow_1h_lots as f64 * 100.0 >= s_min;
            if !flow_ok {
                return false;
            }
        }
        // Ось стороны: другая сторона выбывает до форм (как возраст и сила).
        if self.spec.side.is_some_and(|s| t.side != s) {
            return false;
        }
        // Состояние стены: съедена к касанию сильнее порога — не вход.
        if self.spec.eaten_max_pct.is_some_and(|m| eaten_pct(t) > m) {
            return false;
        }
        // T4 (П-02, Г-86): зеркало предыдущей проверки — съедено МЕНЬШЕ
        // порога не вход (нужен именно сильно проеденный завал).
        if self.spec.eaten_min_pct.is_some_and(|m| eaten_pct(t) < m) {
            return false;
        }
        // Размер стены: номинал при касании ниже порога набора — не вход.
        if self.spec.usd_min.is_some_and(|m| {
            t.price_tick as f64 * self.tick * t.size_at_touch as f64 * self.lot < m
        }) {
            return false;
        }
        // Г-07 (TK-012): позади стены меньше `pct` % её размера — не вход (целые лоты, без f64).
        if self.spec.behind_min_pct.is_some_and(|pct| {
            i128::from(t.depth_behind_lots) * 100 < i128::from(pct) * i128::from(t.size_at_touch)
        }) {
            return false;
        }
        if self.spec.stack_min.is_some_and(|n| t.stack_levels < n) {
            return false;
        }
        if self.spec.repeat_min.is_some_and(|k| t.repeat_count < k) {
            return false;
        }
        if self.spec.swept_min_bp.is_some_and(|q| {
            i128::from(t.swept_lots) * 10_000 < i128::from(q) * i128::from(t.size_at_touch)
        }) {
            return false;
        }
        // Контекст (S4): ход до касания и режим — вне границ набора или
        // без значения при заданной границе — не вход.
        if let Some(ctx) = self.spec.ctx {
            let c = &ctx[ti];
            if !self
                .spec
                .ctx_ranges
                .iter()
                .zip(&c.axes)
                .all(|(r, v)| r.holds(*v))
            {
                return false;
            }
        }
        true
    }

    /// То же, что `admits` при `holds`, по строке `AdmitRow` (порядок и арифметика те же, результат тот же).
    #[allow(clippy::cast_precision_loss)]
    fn admits_row(&self, ti: usize, r: &AdmitRow) -> bool {
        if self.spec.frontrun_only && !r.frontrun_some {
            return false;
        }
        if self
            .spec
            .frontrun_min_lots
            .is_some_and(|n| r.frontrun_lots < n)
        {
            return false;
        }
        if !self.holds.is_some_and(|h| h[ti]) {
            return false;
        }
        if self.spec.min_age_ms.is_some_and(|n| r.age_ms < n) {
            return false;
        }
        if self.spec.max_age_ms.is_some_and(|n| r.age_ms >= n) {
            return false;
        }
        if let Some(s_min) = self.spec.min_flow_pct {
            let flow_ok = r.flow_1h_lots > 0
                && r.size_at_touch as f64 / r.flow_1h_lots as f64 * 100.0 >= s_min;
            if !flow_ok {
                return false;
            }
        }
        if self.spec.side.is_some_and(|s| r.side != s) {
            return false;
        }
        if self.spec.eaten_max_pct.is_some_and(|m| r.eaten_pct > m) {
            return false;
        }
        if self.spec.eaten_min_pct.is_some_and(|m| r.eaten_pct < m) {
            return false;
        }
        if self.spec.usd_min.is_some_and(|m| {
            r.price_tick as f64 * self.tick * r.size_at_touch as f64 * self.lot < m
        }) {
            return false;
        }
        if self.spec.behind_min_pct.is_some_and(|pct| {
            i128::from(r.depth_behind_lots) * 100 < i128::from(pct) * i128::from(r.size_at_touch)
        }) {
            return false;
        }
        if self.spec.stack_min.is_some_and(|n| r.stack_levels < n) {
            return false;
        }
        if self.spec.repeat_min.is_some_and(|k| r.repeat_count < k) {
            return false;
        }
        if self.spec.swept_min_bp.is_some_and(|q| {
            i128::from(r.swept_lots) * 10_000 < i128::from(q) * i128::from(r.size_at_touch)
        }) {
            return false;
        }
        if let Some(ctx) = self.spec.ctx {
            let c = &ctx[ti];
            if !self
                .spec
                .ctx_ranges
                .iter()
                .zip(&c.axes)
                .all(|(rg, v)| rg.holds(*v))
            {
                return false;
            }
        }
        true
    }
}
