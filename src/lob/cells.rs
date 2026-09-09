//! Ячейки C1 и C2, дизъюнктный контраст и гейт G2 (план, §5.2).
//!
//! C1 — только исход (Decision 16): `pulled` на стороне бида. Дистанция 1–5
//! тиков и горизонт 10 с — свойства входного потока, а не предиката: вызывающий
//! подаёт сюда только уровни с измеренным markout на 10 с на дистанции 1–5
//! тиков, модуль лишь делит их на ячейки. C2 — исход и история: C1 плюс время
//! жизни строго ниже медианы и повторяемость на цене не меньше двух
//! за скользящий час.
//!
//! Медиана `lifetime_ms` считается внутри популяции C1 по первым суткам UTC
//! подтверждающей выборки и дальше не пересчитывается — то же правило, что для
//! терцилей слоёв в 5.2. Сюда она либо приходит готовой (`run_cells_with_median`,
//! как счётчик `watch` её только читает), либо считается один раз из первых
//! суток (`run_cells`).
//!
//! Вклад истории печатается двумя строками: основная — дизъюнктный контраст
//! `C2` против `C1 \ C2` со своим бутстрап-интервалом, вспомогательная —
//! вложенная разность `C2 − C1` вместе с долей `w` ячейки C2 внутри C1.
//! Дизъюнктный, а не вложенный: `C2` есть подмножество `C1`, поэтому `C2 − C1`
//! механически занижено ровно в `(1 − w)` раз, и читатель принял бы заниженное
//! число за вклад истории. Интервал контраста — wild cluster bootstrap
//! по тем же суткам-кластерам и тем же весам Уэбба, что тесты ячеек.
//!
//! Что переиспользуется, а не дублируется:
//!
//! - `watch::is_c1` / `watch::is_c2` — принадлежность к ячейкам. Своих
//!   предикатов здесь нет.
//! - `watch::day_eligible` — единственный предикат годности суток на весь
//!   документ (Decision 21). Своей копии здесь нет.
//! - `watch::TRIGGER_N_C2` / `watch::TRIGGER_G` — пороги выборки гейта.
//! - `stats::wild_cluster_bootstrap_t` — p-значения обеих ячеек по Decision 9
//!   (веса Уэбба, 9999 реплик, `G-1` степеней). Своей t-статистики здесь нет;
//!   требование конечности `t_obs` (`is_finite`, отказ `DegenerateVariance`
//!   вместо ложного зелёного на константных данных) наследуется вызовом.
//! - `stats::webb_p_grid_resolution` / `stats::GATE_ALPHA` — разрешение сетки
//!   и α = 0.005 на ячейку (Бонферрони от 0.01, Decision 16). Своих чисел
//!   здесь нет.
//!
//! Разведочная таблица (слои дистанции, терцили волатильности, терцили
//! `lifetime_ms`/`repeat_count`) в гейты не входит: её границы фиксируются до
//! прогона, а вердикт читает только итоги ячеек. Помощники слоёв ниже —
//! только разметка разведки, не вход гейта.

use crate::lob::levels::LevelRecord;
use crate::lob::watch::{day_eligible, is_c1, is_c2, DayTally, TRIGGER_G, TRIGGER_N_C2};
use crate::stats::{
    webb_p_grid_resolution, wild_cluster_bootstrap_t, BootstrapError, SplitMix64, GATE_ALPHA,
};

// ---------------------------------------------------------------------------
// Константы шага 5.2. Каждое число — из плана.
// ---------------------------------------------------------------------------

/// Seed кластерного бутстрапа контраста и значение по умолчанию для тестов
/// ячеек. Заморожен до данных, как всё предрегистрированное: один и тот же
/// вход даёт один и тот же отчёт (A2). p-тесты ячеек принимают seed параметром
/// и вызываются с тем же значением — реплики контраста и тестов идут из
/// одного источника.
pub const CELLS_SEED: u64 = 2026_0502;

/// Нижний квантиль двустороннего интервала контраста: 0.25%.
pub const CONTRAST_CI_LOW_Q: f64 = 0.0025;

/// Верхний квантиль двустороннего интервала контраста: 99.75%.
/// Вместе с нижним даёт двусторонний интервал уровня 99.5% — на той же α,
/// что вердикты ячеек (Decision 16).
pub const CONTRAST_CI_HIGH_Q: f64 = 0.9975;

/// Пометка разведочной таблицы: границы слоёв фиксируются до прогона
/// (дистанция 1–5 / 6–20 / 21+ тиков; терцили — по первым суткам, дальше не
/// пересчитываются), в гейты она не входит. Печатается рядом с вердиктом,
/// чтобы читатель не принял разведку за третью проверяемую ячейку.
pub const EXPLORATION_NOT_IN_GATES: &str =
    "разведка (слои дистанции/волатильности/lifetime/repeat) в гейты не входит";

// ---------------------------------------------------------------------------
// Детерминированный источник весов Уэбба для интервала контраста.
// ---------------------------------------------------------------------------

/// Шесть точек распределения Уэбба (Decision 9): `-√1.5, -1, -√0.5, √0.5, 1,
/// √1.5`. Те же значения, что использует `stats`: интервал контраста обязан
/// идти по тем же кластерным репликам, что p-тесты ячеек, поэтому источник
/// реплик — общий `crate::stats::SplitMix64` (вторая копия удалена: детектор
/// копипасты её не видел, потому что обёртки назывались по-разному).
/// Таблица точек при этом своя, `WEBB_POINTS`: `stats` отдаёт только функцию
/// `webb_weight`, а порядок индексации задаёт вызывающий. Побитовое совпадение
/// потоков держит тест `webb_points_match_stats_bit_for_bit`.
const WEBB_POINTS: [f64; 6] = [
    -1.224_744_871_391_589,
    -1.0,
    -std::f64::consts::FRAC_1_SQRT_2,
    std::f64::consts::FRAC_1_SQRT_2,
    1.0,
    1.224_744_871_391_589,
];

/// Очередной вес Уэбба из общего генератора. Порядок `next_u64 % 6` — тот же,
/// что `SplitMix64::next_webb_weight` в `stats`, поэтому при том же seed поток
/// совпадает пореплико (A2).
fn next_webb(rng: &mut SplitMix64) -> f64 {
    WEBB_POINTS[(rng.next_u64() % WEBB_POINTS.len() as u64) as usize]
}

// ---------------------------------------------------------------------------
// Ошибки. Один тип на весь модуль, в стиле `watch.rs`/`markup.rs`.
// ---------------------------------------------------------------------------

/// Отказ шага 5.2. Ни один вариант не паникует.
#[derive(Debug, Clone, PartialEq)]
pub enum CellsError {
    /// Суток не подано вовсе: медиану считать не из чего.
    NoEligibleDays,
    /// Негодные сутки во входе подтверждающей выборки: флаг по построению
    /// содержит только годные, расхождение есть дефект счётчика, не исход.
    DayNotEligible { day: String },
    /// Число markout не совпало с числом записей этих суток.
    MismatchedLengths {
        day: String,
        records: usize,
        markouts: usize,
    },
    /// Не конечный markout (NaN/inf): молча выкидывать его значило бы
    /// пересчитывать выборку задним числом.
    NonFiniteMarkout { day: String, index: usize },
    /// В первые сутки нет ни одного C1: популяция медианы пуста.
    NoC1InFirstDay { day: String },
}

impl std::fmt::Display for CellsError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CellsError::NoEligibleDays => write!(f, "нет годных суток: медиану считать не из чего"),
            CellsError::DayNotEligible { day } => {
                write!(f, "сутки {day} негодны: дефект счётчика, не исход")
            }
            CellsError::MismatchedLengths {
                day,
                records,
                markouts,
            } => write!(f, "сутки {day}: записей {records}, а markout {markouts}"),
            CellsError::NonFiniteMarkout { day, index } => {
                write!(f, "сутки {day}: markout {index} не конечен")
            }
            CellsError::NoC1InFirstDay { day } => {
                write!(f, "первые сутки {day} без C1: популяция медианы пуста")
            }
        }
    }
}

impl std::error::Error for CellsError {}

// ---------------------------------------------------------------------------
// Вход: сутки подтверждающей выборки с размеченными уровнями и markout.
// ---------------------------------------------------------------------------

/// Одни сутки подтверждающей выборки: счётчик качества (`watch`), записи
/// уровней (`levels`) и markout на 10 с (`markout`, горизонт ячейки C1/C2)
/// параллельно записям: `markouts_10s[i]` — значение для `records[i]`.
/// `None` — будущего на 10 с нет: наблюдение не входит в ячейки (это не ноль),
/// а считается в `CellsReport::markout_missing` — свидетельством, что цепочка
/// дошла до конца, а не доказательством нулевого сдвига.
pub struct CellDay<'a> {
    /// Счётчик суток: символ, дата, качество verify/разрывов.
    pub tally: &'a DayTally,
    /// Записи уровней этих суток.
    pub records: &'a [LevelRecord],
    /// Markout на 10 с параллельно записям.
    pub markouts_10s: &'a [Option<f64>],
}

// ---------------------------------------------------------------------------
// Итоги ячеек и контраста.
// ---------------------------------------------------------------------------

/// Итог одной ячейки — done-condition 5.2 буквально: n, среднее, медиана
/// и p по Decision 9. Среднее и медиана — markout на 10 с в bps; `None` —
/// наблюдений нет.
#[derive(Debug, Clone, PartialEq)]
pub struct CellStats {
    /// Наблюдений ячейки с измеренным markout (годные сутки).
    pub n: u64,
    /// Суток `G` в смысле Decision 21: годные сутки с наблюдением ячейки.
    pub g: u64,
    /// Среднее markout в bps.
    pub mean: Option<f64>,
    /// Медиана markout в bps (при чётном n — среднее двух средних).
    pub median: Option<f64>,
    /// Односторонний wild cluster bootstrap-t, H1: среднее > 0.
    /// `Err(DegenerateVariance)` на константных данных — отказ, а не
    /// ложный зелёный: наследуется из `stats` вызовом, не копией.
    pub p: Result<f64, BootstrapError>,
    /// Достижимое разрешение сетки p при фактическом `G` (печатается
    /// перед вердиктом по Decision 9).
    pub resolution: f64,
}

impl CellStats {
    /// Строка ячейки для отчёта: n, G, среднее, медиана, p и разрешение.
    pub fn format_line(&self, name: &str) -> String {
        format!(
            "{name}: n={} G={} mean={} median={} p={} grid={:.3e}",
            self.n,
            self.g,
            fmt_opt(self.mean),
            fmt_opt(self.median),
            match self.p {
                Ok(v) => format!("{v:.6}"),
                Err(e) => format!("ERR({e:?})"),
            },
            self.resolution
        )
    }
}

/// Основная строка вклада истории: дизъюнктный контраст `C2` против
/// `C1 \ C2` со своим бутстрап-интервалом. Точечная оценка — разность
/// пуловых средних, интервал — процентили wild cluster bootstrap
/// разности на весах Уэбба (тем же seed, что p-тесты).
#[derive(Debug, Clone, PartialEq)]
pub struct DisjointContrast {
    /// Наблюдений C2.
    pub n_c2: u64,
    /// Наблюдений контроля `C1 \ C2` (тот же исход, без условия по истории).
    pub n_rest: u64,
    /// Среднее C2 в bps.
    pub mean_c2: f64,
    /// Среднее контроля в bps.
    pub mean_rest: f64,
    /// Разность `mean_c2 − mean_rest` в bps.
    pub diff: f64,
    /// Нижняя граница интервала (квантиль 0.25%).
    pub ci_low: f64,
    /// Верхняя граница интервала (квантиль 99.75%).
    pub ci_high: f64,
    /// Число реплик интервала.
    pub replications: u32,
    /// Seed реплик.
    pub seed: u64,
}

impl DisjointContrast {
    /// Основная строка: разность с интервалом, не вложенная.
    pub fn format_headline(&self) -> String {
        format!(
            "history(C2 vs C1\\C2): diff={:.4} CI=[{:.4},{:.4}] (99.5%, B={} seed={}) n_c2={} n_rest={}",
            self.diff,
            self.ci_low,
            self.ci_high,
            self.replications,
            self.seed,
            self.n_c2,
            self.n_rest
        )
    }
}

/// Вспомогательная строка: вложенная разность `C2 − C1` вместе с долей `w`
/// ячейки C2 внутри C1. Только для чтения рядом с основной: вердикт её
/// не читает (см. `decide_g2` — вложенной разности нет в аргументах).
#[derive(Debug, Clone, PartialEq)]
pub struct NestedAux {
    /// Наблюдений C1 (включая C2).
    pub n_c1: u64,
    /// Наблюдений C2.
    pub n_c2: u64,
    /// Среднее C1 в bps.
    pub mean_c1: f64,
    /// Среднее C2 в bps.
    pub mean_c2: f64,
    /// Вложенная разность `mean_c2 − mean_c1` в bps (занижена в `(1−w)` раз).
    pub nested_diff: f64,
    /// Доля `n_c2 / n_c1`.
    pub w: f64,
}

impl NestedAux {
    /// Вспомогательная строка: вложенная разность и доля.
    pub fn format_aux(&self) -> String {
        format!(
            "aux nested(C2-C1): diff={:.4} w={:.4} (занижено в (1-w) раз, не вклад истории)",
            self.nested_diff, self.w
        )
    }
}

// ---------------------------------------------------------------------------
// Вердикты: ячейка и гейт G2 дословно по формулировке плана.
// ---------------------------------------------------------------------------

/// Почему ячейка не прошла методически (вердикт не выносится), а не рыночно.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum MethodReason {
    /// Разрешение сетки p при фактическом `G` грубее α: тест не выносится.
    GridTooCoarse { g: u64, resolution: f64 },
    /// Недобор (`n < 100` или `G < 12`): флаг выставлен неверно, это дефект
    /// `lob watch`, а не исход — вердикт не выносится до починки счётчика.
    SampleDefect { n: u64, g: u64 },
    /// Вырожденная дисперсия (константные данные): t не считается вовсе.
    DegenerateVariance { clusters: usize },
    /// Кластеров меньше минимума (оборонительный вариант: недобор выше
    /// уже пойман как `SampleDefect`).
    TooFewClusters { clusters: usize },
}

impl std::fmt::Display for MethodReason {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            MethodReason::GridTooCoarse { g, resolution } => {
                write!(f, "методика: сетка p при G={g} грубее α ({resolution:.3e})")
            }
            MethodReason::SampleDefect { n, g } => {
                write!(f, "методика: недобор n={n} G={g} — дефект счётчика watch")
            }
            MethodReason::DegenerateVariance { clusters } => {
                write!(f, "методика: вырожденная дисперсия на {clusters} кластерах")
            }
            MethodReason::TooFewClusters { clusters } => {
                write!(f, "методика: кластеров {clusters} меньше минимума")
            }
        }
    }
}

/// Вердикт одной ячейки: `n ≥ 100` и `G ≥ 12`, среднее на 10 с положительно,
/// односторонний bootstrap-t даёт p ≤ 0.005.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum CellVerdict {
    /// Все четыре условия выполнены.
    Pass,
    /// Данные против ячейки (среднее неположительно или p > α).
    MarketRed,
    /// Вердикт не выносится: методический красный, не рыночный.
    MethodRed(MethodReason),
}

impl CellVerdict {
    /// Проход — условие гейта для этой ячейки.
    pub fn is_pass(self) -> bool {
        matches!(self, CellVerdict::Pass)
    }

    /// Методический красный (вердикт не вынесен).
    pub fn is_method_red(self) -> bool {
        matches!(self, CellVerdict::MethodRed(_))
    }
}

impl std::fmt::Display for CellVerdict {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CellVerdict::Pass => write!(f, "pass: n,G,mean>0,p<=0.005"),
            CellVerdict::MarketRed => write!(f, "RED: данные против ячейки"),
            CellVerdict::MethodRed(r) => write!(f, "RED ({r})"),
        }
    }
}

/// Вердикт гейта G2. Хотя бы одна из C1, C2 прошла — проход; обе не прошли
/// по данным — красный; хотя бы одна не вынесена методически и ни одна
/// не прошла — методический красный (вердикт не выносится), а не рыночный:
/// гейт обязан скорее недосчитать сигнал, чем переучесть его.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum G2Verdict {
    /// Хотя бы одна ячейка прошла; флаги — какая именно.
    Pass {
        /// C1 прошла.
        c1_passed: bool,
        /// C2 прошла.
        c2_passed: bool,
    },
    /// Обе не прошли по данным.
    RedMarket,
    /// Вердикт не выносится. Причина — ячейки C2, если она методическая
    /// (вердиктная ячейка по Decision 20), иначе ячейки C1.
    RedMethodology(MethodReason),
}

impl G2Verdict {
    /// Проход гейта — зелёный свет дальше по подтверждающему прогону.
    pub fn is_pass(self) -> bool {
        matches!(self, G2Verdict::Pass { .. })
    }
}

impl std::fmt::Display for G2Verdict {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            G2Verdict::Pass {
                c1_passed,
                c2_passed,
            } => write!(
                f,
                "G2 pass: C1={c1_passed} C2={c2_passed} (α=0.005 на ячейку)"
            ),
            G2Verdict::RedMarket => write!(f, "G2 RED: обе ячейки не прошли"),
            G2Verdict::RedMethodology(r) => write!(f, "G2 RED ({r})"),
        }
    }
}

// ---------------------------------------------------------------------------
// Разведочная таблица: разметка вне гейтов.
// ---------------------------------------------------------------------------

/// Слой дистанции из 5.2: границы фиксируются до прогона.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DistanceBucket {
    /// 1–5 тиков (полоса ячеек C1/C2).
    Near,
    /// 6–20 тиков.
    Mid,
    /// 21+ тиков.
    Far,
}

/// Слой дистанции по числу тиков. Ноль тиков на практике не встречается
/// (уровень на своей цене уже не «дистанция») и относится к ближнему слою.
pub fn distance_bucket(dist_ticks: u32) -> DistanceBucket {
    if dist_ticks <= 5 {
        DistanceBucket::Near
    } else if dist_ticks <= 20 {
        DistanceBucket::Mid
    } else {
        DistanceBucket::Far
    }
}

/// Метка терциля разведки по замороженным границам: ниже `b1` — 0, ниже `b2`
/// — 1, иначе 2. Границы приходят готовыми (посчитаны по первым суткам и
/// дальше не пересчитываются); порядок `b1 <= b2` — предусловие вызывающего.
pub fn tercile_label(value: i64, b1: i64, b2: i64) -> u8 {
    debug_assert!(b1 <= b2, "границы терцилей обязаны идти по возрастанию");
    if value < b1 {
        0
    } else if value < b2 {
        1
    } else {
        2
    }
}

// ---------------------------------------------------------------------------
// Чистые помощники: медианы, среднее, квантиль.
// ---------------------------------------------------------------------------

/// Медиана целых lifetimes: по возрастанию, при чётном n — верхняя середина
/// (`sorted[n/2]`). Та же договорённость, что счётчик подразумевает молча:
/// зафиксирована здесь явно, потому что решает состав C2.
fn median_i64_upper(sorted: &mut [i64]) -> Option<i64> {
    if sorted.is_empty() {
        return None;
    }
    sorted.sort_unstable();
    Some(sorted[sorted.len() / 2])
}

/// Медиана markout: при чётном n — среднее двух средних.
fn median_f64(values: &[f64]) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    let mut sorted = values.to_vec();
    sorted.sort_by(|a, b| a.total_cmp(b));
    let n = sorted.len();
    if n % 2 == 1 {
        Some(sorted[n / 2])
    } else {
        Some((sorted[n / 2 - 1] + sorted[n / 2]) / 2.0)
    }
}

/// Среднее. `None` — значений нет. Конечность входа проверена вызывающим.
fn mean_f64(values: &[f64]) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    Some(values.iter().sum::<f64>() / values.len() as f64)
}

/// Квантиль уровня `q` на отсортированном по возрастанию срезе, линейная
/// интерполяция между соседями. Вызывающий гарантирует непустоту и `q`
/// внутри [0, 1].
fn quantile_sorted(sorted: &[f64], q: f64) -> f64 {
    debug_assert!(!sorted.is_empty(), "квантиль пустого распределения");
    debug_assert!((0.0..=1.0).contains(&q), "уровень квантиля вне [0,1]");
    let pos = q * (sorted.len() - 1) as f64;
    let lo = pos.floor() as usize;
    let hi = pos.ceil() as usize;
    if lo == hi {
        sorted[lo]
    } else {
        sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo as f64)
    }
}

/// Печать опционального числа: `none`, если наблюдений нет.
fn fmt_opt(v: Option<f64>) -> String {
    match v {
        Some(x) => format!("{x:.4}"),
        None => "none".to_string(),
    }
}

// ---------------------------------------------------------------------------
// Медиана C1 по первым суткам: считается один раз, не пересчитывается.
// ---------------------------------------------------------------------------

/// Порядок суток по возрастанию `day_utc` (индексы входа). Первые сутки UTC —
/// минимальная дата среди годных, а не первые в срезе: порядок подачи
/// на медиану не влияет.
fn order_by_day(days: &[CellDay<'_>]) -> Vec<usize> {
    let mut order: Vec<usize> = (0..days.len()).collect();
    order.sort_by(|&a, &b| days[a].tally.day_utc.cmp(&days[b].tally.day_utc));
    order
}

/// Проверяет вход целиком: непустоту, попарные длины, годность каждых суток
/// общим предикатом и конечность каждого измеренного markout. Возвращает
/// порядок суток по возрастанию даты.
fn validate_days(days: &[CellDay<'_>]) -> Result<Vec<usize>, CellsError> {
    if days.is_empty() {
        return Err(CellsError::NoEligibleDays);
    }
    for day in days {
        let key = day.tally.day_utc.as_str();
        if day.records.len() != day.markouts_10s.len() {
            return Err(CellsError::MismatchedLengths {
                day: key.to_string(),
                records: day.records.len(),
                markouts: day.markouts_10s.len(),
            });
        }
        if !day_eligible(day.tally) {
            return Err(CellsError::DayNotEligible {
                day: key.to_string(),
            });
        }
        for (i, m) in day.markouts_10s.iter().enumerate() {
            if let Some(v) = m {
                if !v.is_finite() {
                    return Err(CellsError::NonFiniteMarkout {
                        day: key.to_string(),
                        index: i,
                    });
                }
            }
        }
    }
    Ok(order_by_day(days))
}

/// Медиана `lifetime_ms` внутри популяции C1 по первым суткам UTC
/// подтверждающей выборки. Популяция — все записи C1 первых суток
/// (наличие markout на медиану не влияет: время жизни есть всегда).
/// Возвращает пару (первые сутки, медиана).
pub fn median_lifetime_c1_first_day(days: &[CellDay<'_>]) -> Result<(String, i64), CellsError> {
    let order = validate_days(days)?;
    let first = &days[order[0]];
    let mut lifetimes: Vec<i64> = first
        .records
        .iter()
        .filter(|r| is_c1(r))
        .map(|r| r.lifetime_ms)
        .collect();
    median_i64_upper(&mut lifetimes).map_or_else(
        || {
            Err(CellsError::NoC1InFirstDay {
                day: first.tally.day_utc.clone(),
            })
        },
        |m| Ok((first.tally.day_utc.clone(), m)),
    )
}

// ---------------------------------------------------------------------------
// Решение ячейки и гейта — чистые функции поверх посчитанных статистик.
// ---------------------------------------------------------------------------

/// Решение одной ячейки строго по формулировке G2. Порядок проверок задан
/// планом: сначала выборка (дефект счётчика — не исход), затем разрешение
/// сетки (грубее α — тест не выносится), затем отказ вырожденной дисперсии
/// (наследуется из `stats`: сравнение с неконечным `t_obs` дало бы ложный
/// зелёный), затем знак среднего и только потом сравнение p с α.
pub fn decide_cell(
    n: u64,
    g: u64,
    mean: Option<f64>,
    p: Result<f64, BootstrapError>,
    resolution: f64,
) -> CellVerdict {
    if n < TRIGGER_N_C2 || g < TRIGGER_G {
        return CellVerdict::MethodRed(MethodReason::SampleDefect { n, g });
    }
    if resolution > GATE_ALPHA {
        return CellVerdict::MethodRed(MethodReason::GridTooCoarse { g, resolution });
    }
    match p {
        Err(BootstrapError::DegenerateVariance { clusters }) => {
            return CellVerdict::MethodRed(MethodReason::DegenerateVariance { clusters });
        }
        Err(BootstrapError::TooFewClusters { clusters, .. }) => {
            return CellVerdict::MethodRed(MethodReason::TooFewClusters { clusters });
        }
        Err(BootstrapError::GridTooCoarse { clusters, .. }) => {
            return CellVerdict::MethodRed(MethodReason::GridTooCoarse {
                g: clusters as u64,
                resolution,
            });
        }
        Ok(_) => {}
    }
    match mean {
        Some(m) if m.is_finite() && m > 0.0 => {}
        _ => return CellVerdict::MarketRed,
    }
    match p {
        Ok(v) if v.is_finite() && v <= GATE_ALPHA => CellVerdict::Pass,
        _ => CellVerdict::MarketRed,
    }
}

/// Вердикт гейта поверх вердиктов ячеек. Вложенная разность и доля `w` сюда
/// не входят даже аргументами: aux-строка вердикт не читает по построению.
/// Причина методического красного — ячейки C2, если она методическая
/// (вердиктная по Decision 20), иначе ячейки C1.
pub fn decide_g2(c1: CellVerdict, c2: CellVerdict) -> G2Verdict {
    match (c1, c2) {
        (CellVerdict::Pass, CellVerdict::Pass) => G2Verdict::Pass {
            c1_passed: true,
            c2_passed: true,
        },
        (CellVerdict::Pass, _) => G2Verdict::Pass {
            c1_passed: true,
            c2_passed: false,
        },
        (_, CellVerdict::Pass) => G2Verdict::Pass {
            c1_passed: false,
            c2_passed: true,
        },
        (CellVerdict::MethodRed(_), CellVerdict::MethodRed(r2)) => G2Verdict::RedMethodology(r2),
        (CellVerdict::MarketRed, CellVerdict::MethodRed(r2)) => G2Verdict::RedMethodology(r2),
        (CellVerdict::MethodRed(r1), CellVerdict::MarketRed) => G2Verdict::RedMethodology(r1),
        (CellVerdict::MarketRed, CellVerdict::MarketRed) => G2Verdict::RedMarket,
    }
}

// ---------------------------------------------------------------------------
// Прогон 5.2: отбор, статистики, контраст, вердикт.
// ---------------------------------------------------------------------------

/// Итог шага 5.2: обе ячейки с n, средним, медианой и p; дизъюнктный контраст
/// основной строкой; вложенная разность — вспомогательной; вердикт G2.
#[derive(Debug, Clone, PartialEq)]
pub struct CellsReport {
    /// Замороженная медиана `lifetime_ms` популяции C1 первых суток.
    pub median_lifetime_ms: i64,
    /// Первые сутки UTC, давшие медиану.
    pub first_day_utc: String,
    /// Итог C1.
    pub c1: CellStats,
    /// Итог C2.
    pub c2: CellStats,
    /// Вердикт C1.
    pub c1_verdict: CellVerdict,
    /// Вердикт C2.
    pub c2_verdict: CellVerdict,
    /// Вердикт гейта G2.
    pub verdict: G2Verdict,
    /// Дизъюнктный контраст. `None` — контроль `C1 \ C2` пуст (вся C1
    /// ушла в C2): headline-строки нет, вердикт от этого не зависит.
    pub contrast: Option<DisjointContrast>,
    /// Вспомогательная вложенная разность. `None` — ячейка C1 пуста.
    pub nested: Option<NestedAux>,
    /// Записей с пропущенным будущим на 10 с (в ячейки не вошли, не нули).
    pub markout_missing: u64,
}

impl CellsReport {
    /// Строки отчёта по done-condition: C1 и C2 отдельно, основная строка
    /// контраста, вспомогательная вложенная, пометка разведки, вердикт.
    pub fn summary_lines(&self) -> Vec<String> {
        let mut lines = Vec::with_capacity(7);
        lines.push(format!(
            "median_lifetime_ms={} from first_day={} (frozen, not recomputed)",
            self.median_lifetime_ms, self.first_day_utc
        ));
        lines.push(self.c1.format_line("C1"));
        lines.push(self.c2.format_line("C2"));
        match &self.contrast {
            Some(c) => lines.push(c.format_headline()),
            None => lines.push("history(C2 vs C1\\C2): none (control is empty, w=1)".to_string()),
        }
        match &self.nested {
            Some(a) => lines.push(a.format_aux()),
            None => lines.push("aux nested(C2-C1): none (C1 is empty)".to_string()),
        }
        lines.push(EXPLORATION_NOT_IN_GATES.to_string());
        lines.push(format!(
            "{} markout_10s_missing={} (not zeros)",
            self.verdict, self.markout_missing
        ));
        lines
    }
}

impl std::fmt::Display for CellsReport {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.summary_lines().join("\n"))
    }
}

/// Прогон с готовой медианой (счётчик `watch` её только читает).
/// `replications` — число реплик p-тестов и интервала (продакшн — 9999
/// по Decision 9, `stats::BOOTSTRAP_REPLICATIONS`); `seed` — seed реплик.
#[allow(clippy::cast_possible_wrap)]
pub fn run_cells_with_median(
    days: &[CellDay<'_>],
    median_lifetime_ms: i64,
    replications: u32,
    seed: u64,
) -> Result<CellsReport, CellsError> {
    let order = validate_days(days)?;

    // Отбор из размеченных уровней тем же кодом, что триггер: C2 влечёт C1.
    // Кластер — позиция суток в порядке возрастания даты: тот же порядок,
    // в котором `stats` раздаёт веса реплик (BTreeMap по ключу-кластеру).
    let mut obs_c1: Vec<(i64, f64)> = Vec::new();
    let mut obs_c2: Vec<(i64, f64)> = Vec::new();
    let mut obs_rest: Vec<(i64, f64)> = Vec::new();
    let mut day_sums: Vec<(f64, u64, f64, u64)> = Vec::with_capacity(order.len());
    let mut missing: u64 = 0;
    for (cluster, &di) in order.iter().enumerate() {
        let day = &days[di];
        let key = cluster as i64;
        let mut sum_c2 = 0.0;
        let mut cnt_c2: u64 = 0;
        let mut sum_rest = 0.0;
        let mut cnt_rest: u64 = 0;
        for (rec, m) in day.records.iter().zip(day.markouts_10s.iter()) {
            let Some(v) = m else {
                missing = missing.saturating_add(1);
                continue;
            };
            if !is_c1(rec) {
                continue;
            }
            obs_c1.push((key, *v));
            if is_c2(rec, median_lifetime_ms) {
                obs_c2.push((key, *v));
                sum_c2 += *v;
                cnt_c2 = cnt_c2.saturating_add(1);
            } else {
                obs_rest.push((key, *v));
                sum_rest += *v;
                cnt_rest = cnt_rest.saturating_add(1);
            }
        }
        day_sums.push((sum_c2, cnt_c2, sum_rest, cnt_rest));
    }

    let c1 = cell_stats(&obs_c1, replications, seed);
    let c2 = cell_stats(&obs_c2, replications, seed);
    let c1_verdict = decide_cell(c1.n, c1.g, c1.mean, c1.p, c1.resolution);
    let c2_verdict = decide_cell(c2.n, c2.g, c2.mean, c2.p, c2.resolution);
    let verdict = decide_g2(c1_verdict, c2_verdict);
    let contrast = disjoint_contrast(&day_sums, replications, seed);
    let nested = nested_aux(&obs_c1, &obs_c2);

    Ok(CellsReport {
        median_lifetime_ms,
        first_day_utc: days[order[0]].tally.day_utc.clone(),
        c1,
        c2,
        c1_verdict,
        c2_verdict,
        verdict,
        contrast,
        nested,
        markout_missing: missing,
    })
}

/// Прогон с медианой из первых суток: медиана считается один раз здесь
/// и дальше не пересчитывается — поздние сутки на неё не влияют.
pub fn run_cells(
    days: &[CellDay<'_>],
    replications: u32,
    seed: u64,
) -> Result<CellsReport, CellsError> {
    let (first_day, median) = median_lifetime_c1_first_day(days)?;
    let mut report = run_cells_with_median(days, median, replications, seed)?;
    // Медиана обязана прийти из первых суток, а не из порядка подачи.
    report.median_lifetime_ms = median;
    report.first_day_utc.clone_from(&first_day);
    Ok(report)
}

/// Статистики одной ячейки поверх отобранных (кластер, markout) пар.
/// p — вызовом `stats` по Decision 9; разрешение — той же сетки Уэбба.
fn cell_stats(observations: &[(i64, f64)], replications: u32, seed: u64) -> CellStats {
    let values: Vec<f64> = observations.iter().map(|(_, v)| *v).collect();
    let clusters: usize = {
        let mut seen = observations.iter().map(|(c, _)| *c).collect::<Vec<_>>();
        seen.sort_unstable();
        seen.dedup();
        seen.len()
    };
    let g = clusters as u64;
    CellStats {
        n: values.len() as u64,
        g,
        mean: mean_f64(&values),
        median: median_f64(&values),
        p: wild_cluster_bootstrap_t(observations, replications, GATE_ALPHA, seed),
        resolution: webb_p_grid_resolution(clusters as u32),
    }
}

/// Дизъюнктный контраст: точечная оценка — разность пуловых средних;
/// интервал — процентили wild cluster bootstrap разности на весах Уэбба.
/// `day_sums` — (сумма C2, число C2, сумма контроля, число контроля) по суткам
/// в фиксированном порядке: одна и та же реплика одним и тем же весом
/// взвешивает обе группы — «те же кластерные реплики» буквально.
/// `None` — пуста C2 или пуст контроль.
fn disjoint_contrast(
    day_sums: &[(f64, u64, f64, u64)],
    replications: u32,
    seed: u64,
) -> Option<DisjointContrast> {
    let (tot_c2, n_c2, tot_rest, n_rest) =
        day_sums
            .iter()
            .fold((0.0, 0u64, 0.0, 0u64), |(s2, c2, sr, cr), d| {
                (
                    s2 + d.0,
                    c2.saturating_add(d.1),
                    sr + d.2,
                    cr.saturating_add(d.3),
                )
            });
    if n_c2 == 0 || n_rest == 0 {
        return None;
    }
    let mean_c2 = tot_c2 / n_c2 as f64;
    let mean_rest = tot_rest / n_rest as f64;
    let diff = mean_c2 - mean_rest;
    let mut diffs = Vec::with_capacity(replications as usize);
    if replications == 0 {
        diffs.push(diff);
    } else {
        let mut rng = SplitMix64::new(seed);
        for _ in 0..replications {
            let mut boot_c2 = 0.0;
            let mut boot_rest = 0.0;
            for d in day_sums {
                let w = next_webb(&mut rng);
                boot_c2 += w * d.0;
                boot_rest += w * d.2;
            }
            let b = boot_c2 / n_c2 as f64 - boot_rest / n_rest as f64;
            // Веса и суммы конечны по построению входа: вырожденная реплика
            // здесь невозможна арифметически, проверка — страховка A2.
            debug_assert!(b.is_finite(), "бутстрап-разность обязана быть конечной");
            diffs.push(b);
        }
    }
    diffs.sort_by(|a, b| a.total_cmp(b));
    Some(DisjointContrast {
        n_c2,
        n_rest,
        mean_c2,
        mean_rest,
        diff,
        ci_low: quantile_sorted(&diffs, CONTRAST_CI_LOW_Q),
        ci_high: quantile_sorted(&diffs, CONTRAST_CI_HIGH_Q),
        replications,
        seed,
    })
}

/// Вспомогательная вложенная разность `mean_c2 − mean_c1` и доля `w`.
/// `None` — ячейки пусты.
fn nested_aux(obs_c1: &[(i64, f64)], obs_c2: &[(i64, f64)]) -> Option<NestedAux> {
    let v1: Vec<f64> = obs_c1.iter().map(|(_, v)| *v).collect();
    let v2: Vec<f64> = obs_c2.iter().map(|(_, v)| *v).collect();
    let (m1, m2) = (mean_f64(&v1), mean_f64(&v2));
    match (m1, m2) {
        (Some(mean_c1), Some(mean_c2)) => Some(NestedAux {
            n_c1: v1.len() as u64,
            n_c2: v2.len() as u64,
            mean_c1,
            mean_c2,
            nested_diff: mean_c2 - mean_c1,
            w: v2.len() as f64 / v1.len() as f64,
        }),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::book::Side;
    use crate::lob::levels::DeathKind;

    /// Реплик в тестах: сетка p от их числа не зависит (свойство перебора
    /// весовых векторов, см. `stats`), а прогон в десять раз быстрее.
    const TEST_REPLICATIONS: u32 = 999;

    fn rec(side: Side, traded: i64, max: i64, lifetime: i64, repeat: u32) -> LevelRecord {
        LevelRecord {
            side,
            price_tick: 1000,
            birth_ms: 0,
            death_ms: lifetime,
            lifetime_ms: lifetime,
            size_max: max,
            time_to_max_ms: 0,
            size_monotonic: true,
            repeat_count: repeat,
            repriced: false,
            death: DeathKind::BelowFraction,
            traded_lots: traded,
        }
    }

    fn pulled_bid(lifetime: i64, repeat: u32) -> LevelRecord {
        rec(Side::Bid, 10, 100, lifetime, repeat)
    }

    // SHARED WITH 5.2 (тот же код, что фикстура `watch`): те же восемь
    // записей, те же предикаты `is_c1`/`is_c2` без копий. При медиане
    // 10 000 мс: n_c1 = 5 (записи 1,2,3,7,8), n_c2 = 2 (записи 1,8).
    fn shared_c1_c2_fixture() -> Vec<LevelRecord> {
        vec![
            rec(Side::Bid, 20, 100, 5_000, 2),
            rec(Side::Bid, 10, 100, 15_000, 3),
            rec(Side::Bid, 5, 50, 5_000, 1),
            rec(Side::Bid, 80, 100, 3_000, 9),
            rec(Side::Ask, 10, 100, 1_000, 5),
            rec(Side::Bid, 50, 100, 1_000, 5),
            rec(Side::Bid, 20, 100, 10_000, 4),
            rec(Side::Bid, 1, 100, 100, 2),
        ]
    }

    fn eligible_tally(day: &str) -> DayTally {
        DayTally {
            symbol: "TST".to_string(),
            day_utc: day.to_string(),
            n_c1: 0,
            n_c2: 0,
            has_gap_over_6h: false,
            verify_test1_violations: 0,
            verify_basis_points: 50_000,
        }
    }

    fn close(a: f64, b: f64) -> bool {
        (a - b).abs() < 1e-9
    }

    #[test]
    fn shared_fixture_gives_frozen_c1_c2_counts() {
        let recs = shared_c1_c2_fixture();
        let n_c1 = recs.iter().filter(|r| is_c1(r)).count();
        let n_c2 = recs.iter().filter(|r| is_c2(r, 10_000)).count();
        assert_eq!(n_c1, 5, "C1: записи 1,2,3,7,8");
        assert_eq!(n_c2, 2, "C2: записи 1,8");
        assert!(
            recs.iter().filter(|r| is_c2(r, 10_000)).all(is_c1),
            "C2 строго внутри C1"
        );
        // Строгая граница медианы: запись 7 ровно на медиане — C1, но не C2.
        assert!(is_c1(&recs[6]));
        assert!(!is_c2(&recs[6], 10_000));
    }

    /// Медиана — из первых суток UTC и только из них: поздние сутки состав
    /// C2 не пересчитывают. Порядок подачи в срезе на медиану не влияет.
    #[test]
    fn median_comes_from_first_utc_day_and_is_frozen() {
        // День 1: C1 lifetimes [1000, 2000, 9000] → медиана 2000.
        let d1: Vec<LevelRecord> = [1000, 2000, 9000].map(|lt| pulled_bid(lt, 2)).to_vec();
        // День 2: lifetimes [1500, 1600, 1700] — пуловая медиана была бы 1700
        // и выкинула бы 1700 из C2; замороженная 2000 оставляет все три.
        let d2: Vec<LevelRecord> = [1500, 1600, 1700].map(|lt| pulled_bid(lt, 2)).to_vec();
        let m1 = vec![Some(1.0), Some(1.0), Some(1.0)];
        let t1 = eligible_tally("2026-04-01");
        let t2 = eligible_tally("2026-04-02");
        let fwd = [
            CellDay {
                tally: &t1,
                records: &d1,
                markouts_10s: &m1,
            },
            CellDay {
                tally: &t2,
                records: &d2,
                markouts_10s: &m1,
            },
        ];
        let (day, median) = median_lifetime_c1_first_day(&fwd).expect("медиана обязана найтись");
        assert_eq!(day, "2026-04-01");
        assert_eq!(median, 2000);
        let report = run_cells(&fwd, TEST_REPLICATIONS, CELLS_SEED).expect("прогон");
        assert_eq!(report.median_lifetime_ms, 2000);
        assert_eq!(report.first_day_utc, "2026-04-01");
        // C2: день 1 — только 1000; день 2 — все три (все < 2000).
        assert_eq!(report.c1.n, 6);
        assert_eq!(report.c2.n, 4, "пуловая медиана 1700 дала бы 3");
        // Тот же вход в обратном порядке — та же медиана первых суток UTC.
        let bwd = [
            CellDay {
                tally: &t2,
                records: &d2,
                markouts_10s: &m1,
            },
            CellDay {
                tally: &t1,
                records: &d1,
                markouts_10s: &m1,
            },
        ];
        let report = run_cells(&bwd, TEST_REPLICATIONS, CELLS_SEED).expect("прогон");
        assert_eq!(report.median_lifetime_ms, 2000);
        assert_eq!(report.c2.n, 4);
    }

    /// Done-condition буквально: C1 и C2 напечатаны отдельно, каждая с n,
    /// средним, медианой и p по Decision 9. p сверяется с прямым вызовом
    /// `stats` на тех же наблюдениях — переиспользование доказывается
    /// равенством, а не комментарием.
    #[test]
    fn cells_print_n_mean_median_p_per_decision9() {
        let recs = shared_c1_c2_fixture();
        // C2 (записи 1,8): 10.0, 12.0; контроль C1\C2 (2,3,7): 4.0, 6.0, 5.0.
        let mos = vec![
            Some(10.0),
            Some(4.0),
            Some(6.0),
            Some(7.0),
            Some(7.0),
            Some(7.0),
            Some(5.0),
            Some(12.0),
        ];
        let tally = eligible_tally("2026-04-01");
        let days = [CellDay {
            tally: &tally,
            records: &recs,
            markouts_10s: &mos,
        }];
        let report =
            run_cells_with_median(&days, 10_000, TEST_REPLICATIONS, CELLS_SEED).expect("прогон");
        assert_eq!(report.c1.n, 5);
        assert_eq!(report.c2.n, 2);
        assert!(close(report.c1.mean.unwrap(), 7.4));
        assert!(close(report.c1.median.unwrap(), 6.0));
        assert!(close(report.c2.mean.unwrap(), 11.0));
        assert!(close(report.c2.median.unwrap(), 11.0));
        // Тот же p тем же вызовом: один кластер — один и тот же ответ.
        let obs_c1 = vec![(0, 10.0), (0, 4.0), (0, 6.0), (0, 5.0), (0, 12.0)];
        let direct = wild_cluster_bootstrap_t(&obs_c1, TEST_REPLICATIONS, GATE_ALPHA, CELLS_SEED);
        assert_eq!(
            format!("{:?}", report.c1.p),
            format!("{direct:?}"),
            "p ячейки обязан совпасть с прямым вызовом stats"
        );
        let lines = report.summary_lines();
        let text = lines.join("\n");
        assert!(text.contains("C1: n=5"), "печать C1: {text}");
        assert!(text.contains("C2: n=2"), "печать C2: {text}");
        assert!(text.contains("mean="), "печать: {text}");
        assert!(text.contains("median="), "печать: {text}");
        assert!(text.contains("p="), "печать: {text}");
        assert!(text.contains("grid="), "печать: {text}");
    }

    /// Вклад истории — две разные строки: основная дизъюнктная (6.0),
    /// вспомогательная вложенная (3.6) с долей w = 0.4. Вердикт вложенную
    /// разность не читает: `decide_g2` её нет даже в аргументах, а отчётный
    /// вердикт равен чистому решению поверх вердиктов ячеек.
    #[test]
    fn disjoint_is_headline_nested_is_aux_only() {
        let recs = shared_c1_c2_fixture();
        let mos = vec![
            Some(10.0),
            Some(4.0),
            Some(6.0),
            Some(7.0),
            Some(7.0),
            Some(7.0),
            Some(5.0),
            Some(12.0),
        ];
        let tally = eligible_tally("2026-04-01");
        let days = [CellDay {
            tally: &tally,
            records: &recs,
            markouts_10s: &mos,
        }];
        let report =
            run_cells_with_median(&days, 10_000, TEST_REPLICATIONS, CELLS_SEED).expect("прогон");
        let c = report.contrast.as_ref().expect("контроль C1\\C2 непуст");
        assert_eq!((c.n_c2, c.n_rest), (2, 3));
        assert!(close(c.diff, 6.0), "headline: 11.0 − 5.0, а не вложенная");
        assert!(
            c.ci_low <= c.diff && c.diff <= c.ci_high,
            "точечная оценка внутри своего интервала"
        );
        let a = report.nested.as_ref().expect("C1 непуста");
        assert!(close(a.nested_diff, 3.6), "aux: 11.0 − 7.4");
        assert!(close(a.w, 0.4), "w = 2/5");
        assert!(
            !close(c.diff, a.nested_diff),
            "дизъюнктный и вложенный обязаны различаться"
        );
        assert_eq!(
            report.verdict,
            decide_g2(report.c1_verdict, report.c2_verdict)
        );
        let text = report.summary_lines().join("\n");
        assert!(text.contains("C2 vs C1\\C2"), "печать: {text}");
        assert!(text.contains("aux nested(C2-C1)"), "печать: {text}");
        assert!(text.contains("w=0.4000"), "печать: {text}");
    }

    /// Константные данные — вырожденная дисперсия, а не ложный зелёный:
    /// обе ячейки методически красные, гейт не пройден. Нули и ненулевая
    /// константа идут отдельно: у нулей и среднее нулевое, у 7.0 среднее
    /// положительно — и именно здесь до починки выходил минимальный p.
    #[test]
    fn constant_data_is_degenerate_variance_not_green() {
        for value in [0.0, 7.0] {
            let mut tallies = Vec::new();
            let mut recs: Vec<Vec<LevelRecord>> = Vec::new();
            let mut mos: Vec<Vec<Option<f64>>> = Vec::new();
            for d in 1..=12u32 {
                tallies.push(eligible_tally(&format!("2026-05-{d:02}")));
                recs.push(vec![pulled_bid(100, 5); 9]);
                mos.push(vec![Some(value); 9]);
            }
            let days: Vec<CellDay> = tallies
                .iter()
                .zip(recs.iter().zip(mos.iter()))
                .map(|(t, (r, m))| CellDay {
                    tally: t,
                    records: r.as_slice(),
                    markouts_10s: m.as_slice(),
                })
                .collect();
            let report = run_cells_with_median(&days, 10_000, TEST_REPLICATIONS, CELLS_SEED)
                .expect("прогон");
            assert_eq!(report.c1.n, 108);
            assert!(
                matches!(report.c1.p, Err(BootstrapError::DegenerateVariance { .. })),
                "значение {value}: p обязан быть отказом, а не числом"
            );
            assert!(
                matches!(
                    report.c1_verdict,
                    CellVerdict::MethodRed(MethodReason::DegenerateVariance { .. })
                ),
                "значение {value}: ячейка методически красная"
            );
            assert!(
                !report.verdict.is_pass(),
                "значение {value}: гейт не пройден"
            );
            assert!(
                matches!(report.verdict, G2Verdict::RedMethodology(_)),
                "значение {value}: методический красный, не рыночный"
            );
        }
    }

    /// Синтетика гейта: 12 суток по 18 наблюдений (9 C2 + 9 C1-only),
    /// средний markout 6.0 с посуточной качелей ±0.3 — сильный сигнал,
    /// межкластерная дисперсия ненулевая. Обе ячейки проходят.
    type GateInput = (Vec<DayTally>, Vec<Vec<LevelRecord>>, Vec<Vec<Option<f64>>>);

    fn gate_pass_input() -> GateInput {
        let mut tallies = Vec::new();
        let mut recs: Vec<Vec<LevelRecord>> = Vec::new();
        let mut mos: Vec<Vec<Option<f64>>> = Vec::new();
        for d in 0..12u32 {
            tallies.push(eligible_tally(&format!("2026-03-{:02}", d + 1)));
            let day_mean = 6.0 + 0.3 * (f64::from(d % 2) * 2.0 - 1.0);
            let mut day_recs = Vec::with_capacity(18);
            let mut day_mos = Vec::with_capacity(18);
            for i in 0..18 {
                // Первые 9 — C2 (жизнь ниже медианы первых суток, повтор ≥ 2),
                // остальные — C1 без истории (повтор < 2).
                let (lt, rep) = if i < 9 { (3000, 3) } else { (9000, 0) };
                day_recs.push(pulled_bid(lt, rep));
                let v = day_mean + (if i % 2 == 0 { 0.1 } else { -0.1 });
                day_mos.push(Some(v));
            }
            recs.push(day_recs);
            mos.push(day_mos);
        }
        (tallies, recs, mos)
    }

    fn bind_days<'a>(
        tallies: &'a [DayTally],
        recs: &'a [Vec<LevelRecord>],
        mos: &'a [Vec<Option<f64>>],
    ) -> Vec<CellDay<'a>> {
        tallies
            .iter()
            .zip(recs.iter().zip(mos.iter()))
            .map(|(t, (r, m))| CellDay {
                tally: t,
                records: r.as_slice(),
                markouts_10s: m.as_slice(),
            })
            .collect()
    }

    #[test]
    fn strong_signal_passes_gate_on_both_cells() {
        let (tallies, recs, mos) = gate_pass_input();
        let days = bind_days(&tallies, &recs, &mos);
        let report = run_cells(&days, TEST_REPLICATIONS, CELLS_SEED).expect("прогон");
        // Первые сутки: C1 lifetimes [3000×9, 9000×9] → верхняя середина 9000.
        assert_eq!(report.median_lifetime_ms, 9000);
        assert_eq!(report.c1.n, 216);
        assert_eq!(report.c2.n, 108);
        assert_eq!(report.c1.g, 12);
        assert_eq!(report.c2.g, 12);
        assert_eq!(report.c1_verdict, CellVerdict::Pass);
        assert_eq!(report.c2_verdict, CellVerdict::Pass);
        assert_eq!(
            report.verdict,
            G2Verdict::Pass {
                c1_passed: true,
                c2_passed: true
            }
        );
        assert!(report.verdict.is_pass());
        let text = report.summary_lines().join("\n");
        assert!(text.contains("G2 pass"), "печать: {text}");
    }

    /// Зеркальный сигнал (среднее −6.0): p одностороннего теста велик,
    /// вердикт рыночно красный — и это исход, а не дефект.
    #[test]
    fn negative_mean_is_market_red() {
        let (tallies, recs, mos) = gate_pass_input();
        let neg: Vec<Vec<Option<f64>>> = mos
            .iter()
            .map(|day| day.iter().map(|m| m.map(|v| -v)).collect())
            .collect();
        let days = bind_days(&tallies, &recs, &neg);
        let report = run_cells(&days, TEST_REPLICATIONS, CELLS_SEED).expect("прогон");
        assert_eq!(report.c1_verdict, CellVerdict::MarketRed);
        assert_eq!(report.c2_verdict, CellVerdict::MarketRed);
        assert_eq!(report.verdict, G2Verdict::RedMarket);
        assert!(!report.verdict.is_pass());
    }

    /// Положительное, но незначимое среднее (0.3 при посуточной качели
    /// ±2.0): знак верный, p груб — рыночный красный через p, не через знак.
    #[test]
    fn insignificant_positive_mean_is_market_red_through_p() {
        let mut tallies = Vec::new();
        let mut recs: Vec<Vec<LevelRecord>> = Vec::new();
        let mut mos: Vec<Vec<Option<f64>>> = Vec::new();
        for d in 0..12u32 {
            tallies.push(eligible_tally(&format!("2026-06-{:02}", d + 1)));
            let day_value = if d % 2 == 0 { 2.3 } else { -1.7 };
            let mut day_recs = Vec::with_capacity(18);
            let mut day_mos = Vec::with_capacity(18);
            for i in 0..18 {
                let (lt, rep) = if i < 9 { (3000, 3) } else { (9000, 0) };
                day_recs.push(pulled_bid(lt, rep));
                day_mos.push(Some(day_value + (if i % 2 == 0 { 0.1 } else { -0.1 })));
            }
            recs.push(day_recs);
            mos.push(day_mos);
        }
        let days = bind_days(&tallies, &recs, &mos);
        let report = run_cells(&days, TEST_REPLICATIONS, CELLS_SEED).expect("прогон");
        assert!(report.c1.mean.unwrap() > 0.0, "знак верный");
        assert!(report.c1.p.unwrap() > GATE_ALPHA, "а значимость нет");
        assert_eq!(report.c1_verdict, CellVerdict::MarketRed);
        assert_eq!(report.verdict, G2Verdict::RedMarket);
    }

    /// Недобор — дефект `lob watch`, а не исход: вердикт не выносится.
    #[test]
    fn underrun_is_watch_defect_not_outcome() {
        let recs = shared_c1_c2_fixture();
        let mos = vec![Some(6.0); 8];
        let tally = eligible_tally("2026-04-01");
        let days = [CellDay {
            tally: &tally,
            records: &recs,
            markouts_10s: &mos,
        }];
        let report =
            run_cells_with_median(&days, 10_000, TEST_REPLICATIONS, CELLS_SEED).expect("прогон");
        assert_eq!(report.c1.n, 5);
        assert!(matches!(
            report.c1_verdict,
            CellVerdict::MethodRed(MethodReason::SampleDefect { n: 5, g: 1 })
        ));
        assert!(matches!(
            report.verdict,
            G2Verdict::RedMethodology(MethodReason::SampleDefect { .. })
        ));
        assert!(!report.verdict.is_pass());
    }

    /// Чистые решения ячейки: сетка грубее α, отказ `stats`, знак, порог p.
    #[test]
    fn decide_cell_branches_cover_gate_wording() {
        let fine = webb_p_grid_resolution(12);
        assert_eq!(
            decide_cell(120, 12, Some(6.0), Ok(0.001), fine),
            CellVerdict::Pass
        );
        assert!(
            matches!(
                decide_cell(120, 12, Some(6.0), Ok(0.001), 1.0 / 36.0),
                CellVerdict::MethodRed(MethodReason::GridTooCoarse { .. })
            ),
            "сетка грубее α — тест не выносится"
        );
        assert!(matches!(
            decide_cell(50, 12, Some(6.0), Ok(0.001), fine),
            CellVerdict::MethodRed(MethodReason::SampleDefect { .. })
        ));
        assert!(matches!(
            decide_cell(120, 5, Some(6.0), Ok(0.001), fine),
            CellVerdict::MethodRed(MethodReason::SampleDefect { .. })
        ));
        assert_eq!(
            decide_cell(120, 12, Some(-1.0), Ok(0.001), fine),
            CellVerdict::MarketRed
        );
        assert_eq!(
            decide_cell(120, 12, None, Ok(0.001), fine),
            CellVerdict::MarketRed
        );
        assert_eq!(
            decide_cell(120, 12, Some(6.0), Ok(0.5), fine),
            CellVerdict::MarketRed
        );
        assert!(matches!(
            decide_cell(
                120,
                12,
                Some(6.0),
                Err(BootstrapError::DegenerateVariance { clusters: 12 }),
                fine
            ),
            CellVerdict::MethodRed(MethodReason::DegenerateVariance { .. })
        ));
        assert!(matches!(
            decide_cell(
                120,
                12,
                Some(6.0),
                Err(BootstrapError::TooFewClusters {
                    clusters: 12,
                    minimum: 12
                }),
                fine
            ),
            CellVerdict::MethodRed(MethodReason::TooFewClusters { .. })
        ));
    }

    /// Сводка решений гейта: проход хотя бы одной; методика бьёт рынок
    /// (невынесенный вердикт не превращается в рыночный задним числом);
    /// причина — вердиктной C2.
    #[test]
    fn decide_g2_prefers_pass_then_methodology() {
        let pass = CellVerdict::Pass;
        let market = CellVerdict::MarketRed;
        let m1 = CellVerdict::MethodRed(MethodReason::SampleDefect { n: 5, g: 1 });
        let m2 = CellVerdict::MethodRed(MethodReason::DegenerateVariance { clusters: 12 });
        assert_eq!(
            decide_g2(pass, market),
            G2Verdict::Pass {
                c1_passed: true,
                c2_passed: false
            }
        );
        assert_eq!(
            decide_g2(market, pass),
            G2Verdict::Pass {
                c1_passed: false,
                c2_passed: true
            }
        );
        assert_eq!(decide_g2(market, market), G2Verdict::RedMarket);
        assert!(
            matches!(
                decide_g2(market, m2),
                G2Verdict::RedMethodology(MethodReason::DegenerateVariance { .. })
            ),
            "причина вердиктной C2"
        );
        assert!(matches!(
            decide_g2(m1, market),
            G2Verdict::RedMethodology(MethodReason::SampleDefect { .. })
        ));
        assert!(
            matches!(
                decide_g2(m1, m2),
                G2Verdict::RedMethodology(MethodReason::DegenerateVariance { .. })
            ),
            "обе методические — причина C2"
        );
    }

    /// Пропущенное будущее — не ноль: в ячейки не входит, считается рядом.
    #[test]
    fn missing_markouts_are_excluded_and_counted() {
        let recs = shared_c1_c2_fixture();
        let mut mos = vec![Some(6.0); 8];
        mos[0] = None; // запись 1 (C2) без будущего
        let tally = eligible_tally("2026-04-01");
        let days = [CellDay {
            tally: &tally,
            records: &recs,
            markouts_10s: &mos,
        }];
        let report =
            run_cells_with_median(&days, 10_000, TEST_REPLICATIONS, CELLS_SEED).expect("прогон");
        assert_eq!(report.markout_missing, 1);
        assert_eq!(report.c1.n, 4);
        assert_eq!(report.c2.n, 1);
    }

    /// Входные ошибки: негодные сутки, разошедшиеся длины, неконечный markout,
    /// пустой вход и первые сутки без C1.
    #[test]
    fn bad_inputs_are_errors_not_verdicts() {
        let recs = shared_c1_c2_fixture();
        let mos = vec![Some(6.0); 8];
        let mut gapped = eligible_tally("2026-04-01");
        gapped.has_gap_over_6h = true;
        let days = [CellDay {
            tally: &gapped,
            records: &recs,
            markouts_10s: &mos,
        }];
        assert_eq!(
            run_cells_with_median(&days, 10_000, TEST_REPLICATIONS, CELLS_SEED)
                .expect_err("разрыв H8"),
            CellsError::DayNotEligible {
                day: "2026-04-01".to_string(),
            }
        );

        let tally = eligible_tally("2026-04-01");
        let short = vec![Some(6.0); 7];
        let days = [CellDay {
            tally: &tally,
            records: &recs,
            markouts_10s: &short,
        }];
        assert!(matches!(
            run_cells_with_median(&days, 10_000, TEST_REPLICATIONS, CELLS_SEED),
            Err(CellsError::MismatchedLengths { .. })
        ));

        let mut nonfinite = vec![Some(6.0); 8];
        nonfinite[2] = Some(f64::NAN);
        let days = [CellDay {
            tally: &tally,
            records: &recs,
            markouts_10s: &nonfinite,
        }];
        assert!(matches!(
            run_cells_with_median(&days, 10_000, TEST_REPLICATIONS, CELLS_SEED),
            Err(CellsError::NonFiniteMarkout { .. })
        ));

        let empty: [CellDay; 0] = [];
        assert_eq!(
            run_cells(&empty, TEST_REPLICATIONS, CELLS_SEED).expect_err("пусто"),
            CellsError::NoEligibleDays
        );

        // Первые сутки без C1: один съеденный бид.
        let no_c1 = vec![rec(Side::Bid, 80, 100, 3_000, 9)];
        let mos = vec![Some(6.0)];
        let days = [CellDay {
            tally: &tally,
            records: &no_c1,
            markouts_10s: &mos,
        }];
        assert!(matches!(
            run_cells(&days, TEST_REPLICATIONS, CELLS_SEED),
            Err(CellsError::NoC1InFirstDay { .. })
        ));
    }

    /// Разведка в гейты не входит: границы слоёв — по плану, а вердикт зависит
    /// только от признаков Decision 16 (сторона, исход, жизнь, повтор, markout).
    /// Записи, различающиеся лишь внегейтовыми полями, дают тот же вердикт.
    #[test]
    fn exploration_strata_do_not_enter_gates() {
        assert_eq!(distance_bucket(1), DistanceBucket::Near);
        assert_eq!(distance_bucket(5), DistanceBucket::Near);
        assert_eq!(distance_bucket(6), DistanceBucket::Mid);
        assert_eq!(distance_bucket(20), DistanceBucket::Mid);
        assert_eq!(distance_bucket(21), DistanceBucket::Far);
        assert_eq!(tercile_label(5, 10, 20), 0);
        assert_eq!(tercile_label(10, 10, 20), 1);
        assert_eq!(tercile_label(20, 10, 20), 2);

        let mut recs = shared_c1_c2_fixture();
        let mos = vec![Some(6.0); 8];
        let tally = eligible_tally("2026-04-01");
        let days = [CellDay {
            tally: &tally,
            records: &recs,
            markouts_10s: &mos,
        }];
        let base =
            run_cells_with_median(&days, 10_000, TEST_REPLICATIONS, CELLS_SEED).expect("прогон");
        // Внегейтовые поля: цена, монотонность, эвристика переставления.
        for r in &mut recs {
            r.price_tick += 500;
            r.size_monotonic = !r.size_monotonic;
            r.repriced = !r.repriced;
            r.time_to_max_ms += 7;
        }
        let days = [CellDay {
            tally: &tally,
            records: &recs,
            markouts_10s: &mos,
        }];
        let altered =
            run_cells_with_median(&days, 10_000, TEST_REPLICATIONS, CELLS_SEED).expect("прогон");
        assert_eq!(base.c1_verdict, altered.c1_verdict);
        assert_eq!(base.c2_verdict, altered.c2_verdict);
        assert_eq!(base.verdict, altered.verdict);
        let text = base.summary_lines().join("\n");
        assert!(text.contains(EXPLORATION_NOT_IN_GATES), "печать: {text}");
    }

    /// Пустой контроль (вся C1 ушла в C2): headline-строки нет, aux с w = 1,
    /// вердикт от отсутствия контроля не зависит.
    #[test]
    fn empty_control_has_no_headline_but_keeps_aux() {
        let recs = vec![pulled_bid(100, 5); 4];
        let mos = vec![Some(2.0), Some(4.0), Some(6.0), Some(8.0)];
        let tally = eligible_tally("2026-04-01");
        let days = [CellDay {
            tally: &tally,
            records: &recs,
            markouts_10s: &mos,
        }];
        let report =
            run_cells_with_median(&days, 10_000, TEST_REPLICATIONS, CELLS_SEED).expect("прогон");
        assert_eq!(report.c1.n, 4);
        assert_eq!(report.c2.n, 4);
        assert!(report.contrast.is_none(), "контроля нет — headline нет");
        let aux = report
            .nested
            .as_ref()
            .expect("aux есть всегда при непустой C1");
        assert!(close(aux.w, 1.0));
        assert!(close(aux.nested_diff, 0.0));
    }

    /// Граница модулей в духе шагов 1.1/4.1/5.1: анализ не знает про транспорт
    /// и часы. Вещественное число здесь разрешено формулой (bps, p, интервал),
    /// поэтому его нет среди запрещённых — в отличие от списка уровней.
    ///
    /// Запрещённые фрагменты собраны из частей: литерал целиком триггерил бы
    /// эту же проверку сам на себя.
    /// Веса Уэбба записаны здесь и в `stats` **разными выражениями**: там
    /// `(1.5).sqrt()` и `(0.5).sqrt()` считаются в рантайме, здесь стоят
    /// литерал и `FRAC_1_SQRT_2`. Комментарий к `WEBB_POINTS` утверждает, что
    /// поток весов совпадает с потоком `stats` пореплико, и это утверждение
    /// держится ровно на побитовом равенстве шести чисел.
    ///
    /// Сегодня оно верно — оба выражения дают одинаковые f64 по корректному
    /// округлению, — но верно по удаче, а не потому, что кто-то это проверяет.
    /// Правка одного литерала развела бы два потока молча, и ни один тест не
    /// покраснел бы. Сравнение идёт по `to_bits`, а не по `==`: равенство
    /// f64 здесь и есть предмет проверки, приблизительное сравнение проверяло
    /// бы не то.
    #[test]
    fn webb_points_match_stats_bit_for_bit() {
        assert_eq!(
            WEBB_POINTS.len() as u32,
            crate::stats::WEBB_WEIGHT_VALUES,
            "число весов разошлось с сеткой stats"
        );
        for i in 0..crate::stats::WEBB_WEIGHT_VALUES {
            assert_eq!(
                WEBB_POINTS[i as usize].to_bits(),
                crate::stats::webb_weight(i).to_bits(),
                "вес {i} разошёлся с stats::webb_weight побитово"
            );
        }
    }

    #[test]
    fn module_stays_detached_from_transport_and_clocks() {
        const SRC: &str = include_str!("cells.rs");
        let banned = [
            concat!("by", "bit"),
            concat!("tok", "io"),
            concat!("Inst", "ant"),
            concat!("System", "Time"),
            concat!("std::", "time"),
        ];
        for b in banned {
            assert!(!SRC.contains(b), "исходник тянет запрещённое: {b}");
        }
    }
}
