//! Шорт-лист и подтверждение (план, §5.4; контракт Decision 27).
//!
//! Разведочная 60% / подтверждающая 40% по календарю **до анализа**;
//! шорт-лист строится только на разведочной и замораживается дескриптором,
//! проверяемым перед обращением к подтверждающей; подтверждающая считается
//! только по шорт-листу; каждый посчитанный профиль идёт в `runs.csv`;
//! DSR дефлирует по фактическому числу; вердикт выносит подтверждающая.
//!
//! Сетка профилей — Decision 26: маргиналы по каждой оси (для пула из десяти
//! это 29: семь осей, повторяемость трёхзначна — ticket 06) плюс единственный
//! предрегистрированный крест инструмент × исход × расстояние (150 при
//! полной пригодности). Полный крест по всем семи осям разбирался и отменён
//! ревью R-C: семь осей R17 — оси маргиналов и признаки профиля, а не полный
//! крест (`SETTLED.md` В-18, Decision 26/26а, `PLAN.md` D-ИСПЫТАНИЯ).
//! Пригодность корзин — Decision 26а: пара инструмент × корзина существует,
//! только если корзина целиком внутри покрытия топ-50 инструмента;
//! непригодная корзина не печатается ни строкой, ни нулём — её нет, и в
//! число испытаний она не входит. Тест на зависимость от часа суток (ниже)
//! — тоже испытание и тоже строка `runs.csv`: спека «Профиль» считает число
//! испытаний как «пригодные пары плюс маргиналы плюс тесты на час суток».
//!
//! Откуда берутся входы (модуль ничего не меряет сам):
//!
//! - календарь — сутки из счётчика `watch` (там же единый предикат годности);
//! - отбор C1/C2 и статистики — `cells` (`run_cells`);
//! - издержки `net` — `costs`;
//! - число испытаний для DSR/PBO — журнал `runs` (пишутся все профили);
//! - DSR/PBO/CPCV — `final_metrics` (сюда отдаётся фактическое число).
//!
//! Заморозка коммитом буквально: прод-вызывающий передаёт хеш коммита с
//! шорт-листом в `freeze_shortlist`; дескриптор печатается в `shortlist-*.md`
//! рядом с отпечатком списка, а внешняя проверка «коммит предшествует по
//! журналу первому чтению подтверждающей» остаётся обязанностью оператора
//! и CI (в песочнице журнала нет, поэтому механическая часть — отказ
//! подтверждающей без заморозки и отказ профиля вне списка).
//!
//! Выходы как формат (писатель + тест формата, живые данные вне песочницы):
//! `docs/findings/profiles-<дата>.csv` — полная таблица по всем пригодным
//! профилям без отсева; `docs/findings/shortlist-<дата>.md` — шорт-лист с
//! подтверждающими числами, фактическим числом испытаний и поправкой.
//! Синтетика вместо недель: всё ниже считается на сконструированных входах.

use std::fs::File;
use std::io::{self, Write};
use std::path::Path;

use crate::lob::costs::GREEN_NET_BPS;
use crate::lob::runs::{append_run_row, RunKind, RunRow};
use crate::stats::{self, BootstrapError, GATE_ALPHA, G_MIN};

// ---------------------------------------------------------------------------
// Доли деления и пороги. Каждое число — из плана.
// ---------------------------------------------------------------------------

/// Числитель доли разведочной выборки: ранние 60% суток по календарю.
pub const EXPLORATORY_NUMER: usize = 3;

/// Знаменатель доли: 3/5 разведочная, остаток 2/5 подтверждающая.
pub const EXPLORATORY_DENOM: usize = 5;

/// Минимум суток для деления: по одним суткам на каждую половину.
pub const MIN_DAYS_FOR_SPLIT: usize = 2;

/// Порог шорт-листа на разведочной: `n >= 100`. G-порог на разведочной не
/// проверяется: правило §7 (`G >= G_MIN`) задано для подтверждающей, а
/// разведочный фрагмент короче полного периода по построению.
pub const SHORTLIST_MIN_N_EXPL: u64 = 100;

/// Порог зачёта профиля на подтверждающей (§7): `n >= 100`.
pub const CONFIRM_MIN_N: u64 = 100;

// ---------------------------------------------------------------------------
// Сетка профилей (Decision 26): границы назначены здесь, до данных.
// ---------------------------------------------------------------------------

/// Корзины расстояния до середины, bps (привязка к круговым издержкам, а не
/// к тику). Метки и границы идут парой: порядок фиксирован.
pub const DISTANCE_LABELS: [&str; 5] = ["[0,1)", "[1,2.5)", "[2.5,5)", "[5,10)", "[10,25)"];

/// Границы тех же корзин: `(включительно, исключительно)`, bps.
pub const DISTANCE_BOUNDS_BPS: [(f64, f64); 5] = [
    (0.0, 1.0),
    (1.0, 2.5),
    (2.5, 5.0),
    (5.0, 10.0),
    (10.0, 25.0),
];

/// Корзины размера: кратность порога H3.
pub const SIZE_LABELS: [&str; 3] = ["[1,2)", "[2,4)", "[4,inf)"];

/// Корзины времени жизни.
pub const LIFETIME_LABELS: [&str; 3] = ["[0,1s)", "[1s,10s)", "[10s,inf)"];

/// Стороны.
pub const SIDE_LABELS: [&str; 2] = ["bid", "ask"];

/// Исходы уровня.
pub const OUTCOME_LABELS: [&str; 3] = ["eaten", "pulled", "mixed"];

/// Корзины повторяемости на цене за скользящее окно (бриф §3, ticket 06):
/// ровно один, ровно два, три и более. `LevelRecord::repeat_count`
/// (`lob/levels`) считает прошлые рождения на этой цене и стороне; граница
/// назначена здесь, до данных, и не пересматривается (R61).
pub const REPEAT_LABELS: [&str; 3] = ["1", "2", ">=3"];

/// Классифицирует счётчик повторов в корзину `REPEAT_LABELS`. Чистое
/// огрубление: `repeat_count` приходит уже посчитанным `lob/levels`, здесь
/// он только раскладывается по корзине.
pub fn repeat_bucket(repeat_count: u32) -> &'static str {
    // `repeat_count` (`lob/levels.rs`) считает прошлые рождения 0-based:
    // первое появление на цене — 0, второе — 1, и так далее. Поэтому
    // «ровно один раз» — `repeat_count == 0`, не `<= 1`.
    match repeat_count {
        0 => REPEAT_LABELS[0],
        1 => REPEAT_LABELS[1],
        _ => REPEAT_LABELS[2],
    }
}

// ---------------------------------------------------------------------------
// Ошибки. Один тип на модуль, паники нет.
// ---------------------------------------------------------------------------

/// Отказ шага 5.4.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ShortlistError {
    /// Суток меньше двух: делить 60/40 нечего.
    TooFewDays { days: usize },
    /// Те же сутки поданы дважды.
    DuplicateDay { day: String },
    /// Сутки не `YYYY-MM-DD`.
    BadDay { day: String },
    /// Пул инструментов пуст: сетку строить не из чего.
    EmptyPool,
    /// Покрытие инструмента не конечно или отрицательно.
    BadCoverage { symbol: String },
    /// Обращение к подтверждающей без заморозки.
    NotFrozen,
    /// Профиль вне замороженного шорт-листа.
    OutOfShortlist { id: String },
    /// Число испытаний для DSR не совпало с фактическим.
    TrialsMismatch { expected: usize, got: usize },
    /// Файл не открылся / не записался.
    Io(String),
    /// Строка таблицы профилей не разобралась как CSV.
    Csv(String),
}

impl std::fmt::Display for ShortlistError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ShortlistError::TooFewDays { days } => {
                write!(f, "суток {days}: деление 60/40 требует минимум две даты")
            }
            ShortlistError::DuplicateDay { day } => write!(f, "сутки {day} поданы дважды"),
            ShortlistError::BadDay { day } => write!(f, "сутки не YYYY-MM-DD: {day}"),
            ShortlistError::EmptyPool => write!(f, "пул инструментов пуст"),
            ShortlistError::BadCoverage { symbol } => {
                write!(f, "покрытие {symbol} не конечно или отрицательно")
            }
            ShortlistError::NotFrozen => {
                write!(f, "подтверждающая до заморозки: сначала freeze_shortlist")
            }
            ShortlistError::OutOfShortlist { id } => {
                write!(f, "профиль {id} вне замороженного шорт-листа")
            }
            ShortlistError::TrialsMismatch { expected, got } => {
                write!(f, "испытаний для DSR {got}, а посчитано {expected}")
            }
            ShortlistError::Io(e) => write!(f, "ввод-вывод: {e}"),
            ShortlistError::Csv(e) => write!(f, "CSV: {e}"),
        }
    }
}

impl std::error::Error for ShortlistError {}

impl From<io::Error> for ShortlistError {
    fn from(e: io::Error) -> Self {
        ShortlistError::Io(e.to_string())
    }
}

impl From<csv::Error> for ShortlistError {
    fn from(e: csv::Error) -> Self {
        ShortlistError::Csv(e.to_string())
    }
}

// ---------------------------------------------------------------------------
// Календарное деление 60/40 до анализа.
// ---------------------------------------------------------------------------

/// Деление выборки: ранние 60% суток — разведочная, поздние 40% — подтв.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CalendarSplit {
    /// Ранние сутки по возрастанию.
    pub exploratory: Vec<String>,
    /// Поздние сутки по возрастанию.
    pub confirmatory: Vec<String>,
}

fn day_format_ok(day: &str) -> bool {
    let b = day.as_bytes();
    // Все обращения через `get`: длина проверяется здесь же, а не выше.
    b.len() == 10
        && b.get(4) == Some(&b'-')
        && b.get(7) == Some(&b'-')
        && b.get(..4).is_some_and(|s| s.iter().all(u8::is_ascii_digit))
        && b.get(5..7)
            .is_some_and(|s| s.iter().all(u8::is_ascii_digit))
        && b.get(8..10)
            .is_some_and(|s| s.iter().all(u8::is_ascii_digit))
}

/// Делит сутки по календарю 60/40 до анализа. Вход может идти в любом
/// порядке: сортировка здесь, граница — до первого чтения метрик.
/// Дубликаты и неформатные сутки — отказ, а не молчаливый сдвиг границы.
pub fn split_calendar(days: &[String]) -> Result<CalendarSplit, ShortlistError> {
    if days.len() < MIN_DAYS_FOR_SPLIT {
        return Err(ShortlistError::TooFewDays { days: days.len() });
    }
    for d in days {
        if !day_format_ok(d) {
            return Err(ShortlistError::BadDay { day: d.clone() });
        }
    }
    let mut sorted = days.to_vec();
    sorted.sort();
    // Окно из двух сравнивается паттерном, не индексом: `windows(2)` всегда
    // даёт ровно два элемента, и матчинг это выражает без паникующего синтаксиса.
    if sorted.windows(2).any(|w| matches!(w, [a, b] if a == b)) {
        let dup = sorted
            .windows(2)
            .find_map(|w| match w {
                [a, b] if a == b => Some(a.clone()),
                _ => None,
            })
            .unwrap_or_else(|| "?".to_string());
        return Err(ShortlistError::DuplicateDay { day: dup });
    }
    let expl_len = sorted.len() * EXPLORATORY_NUMER / EXPLORATORY_DENOM;
    let expl_len = expl_len.clamp(1, sorted.len() - 1);
    // `split_at` вместо срезов: граница доказана clamp выше (1..len).
    let (exploratory, confirmatory) = sorted.split_at(expl_len);
    Ok(CalendarSplit {
        confirmatory: confirmatory.to_vec(),
        exploratory: exploratory.to_vec(),
    })
}

// ---------------------------------------------------------------------------
// Сетка профилей и пригодность (Decision 26, 26а).
// ---------------------------------------------------------------------------

/// Покрытие инструмента: ширина видимой книги топ-50 в bps на шаге отбора.
/// Пишется колонкой `candidates.csv`; сюда приходит готовым числом.
#[derive(Debug, Clone, PartialEq)]
pub struct InstrumentCoverage {
    /// Символ пула.
    pub symbol: String,
    /// Покрытие топ-50, bps. Конечное, неотрицательное.
    pub coverage_bps: f64,
}

/// Пригодность корзины расстояния для инструмента (Decision 26а): корзина
/// существует, только если целиком внутри покрытия (`hi <= coverage`).
/// Нестрогое сравнение — граница включительно: корзина, ровно доставшая до
/// края книги, наблюдаема.
pub fn distance_bucket_eligible(coverage_bps: f64, lo_bps: f64, hi_bps: f64) -> bool {
    if !coverage_bps.is_finite() || !lo_bps.is_finite() || !hi_bps.is_finite() {
        return false;
    }
    if coverage_bps < 0.0 || lo_bps < 0.0 {
        return false;
    }
    if lo_bps.partial_cmp(&hi_bps) != Some(std::cmp::Ordering::Less) {
        return false;
    }
    hi_bps <= coverage_bps
}

fn marginal_ids(symbols: &[String]) -> Vec<String> {
    let mut out = Vec::new();
    for s in symbols {
        out.push(format!("marginal:instrument={s}"));
    }
    for s in SIDE_LABELS {
        out.push(format!("marginal:side={s}"));
    }
    for s in SIZE_LABELS {
        out.push(format!("marginal:size={s}"));
    }
    for s in DISTANCE_LABELS {
        out.push(format!("marginal:dist={s}"));
    }
    for s in LIFETIME_LABELS {
        out.push(format!("marginal:life={s}"));
    }
    for s in REPEAT_LABELS {
        out.push(format!("marginal:repeat={s}"));
    }
    for s in OUTCOME_LABELS {
        out.push(format!("marginal:outcome={s}"));
    }
    out
}

/// Строит сетку профилей: маргиналы по каждой оси плюс единственный
/// предрегистрированный крест инструмент × исход × расстояние на пригодных
/// корзинах расстояния (`SETTLED.md` В-18, Decision 26/26а, `PLAN.md`
/// D-ИСПЫТАНИЯ — семь осей R17 суть оси маргиналов и признаки профиля, а не
/// полный крест: R-C ревью таска 06). Непригодные пары отсутствуют в выходе
/// вовсе (не нули): вызывающий не посчитает их, не запишет в `runs.csv` и не
/// подставит в DSR. Порядок выхода — по возрастанию строки (детерминирован).
pub fn build_profile_grid(coverages: &[InstrumentCoverage]) -> Result<Vec<String>, ShortlistError> {
    if coverages.is_empty() {
        return Err(ShortlistError::EmptyPool);
    }
    for c in coverages {
        if !c.coverage_bps.is_finite() || c.coverage_bps < 0.0 {
            return Err(ShortlistError::BadCoverage {
                symbol: c.symbol.clone(),
            });
        }
    }
    let mut symbols: Vec<String> = coverages.iter().map(|c| c.symbol.clone()).collect();
    symbols.sort();
    let mut out = marginal_ids(&symbols);
    for c in coverages {
        for outcome in OUTCOME_LABELS {
            for (label, (lo, hi)) in DISTANCE_LABELS.iter().zip(DISTANCE_BOUNDS_BPS.iter()) {
                if distance_bucket_eligible(c.coverage_bps, *lo, *hi) {
                    out.push(format!("cross:{}|{outcome}|{label}", c.symbol));
                }
            }
        }
    }
    out.sort();
    out.dedup();
    Ok(out)
}

/// Номинальный размер сетки при полной пригодности всех корзин расстояния:
/// маргиналы (по одной на инструмент плюс по одной на корзину каждой из
/// остальных шести осей — повторяемость теперь трёхзначна) плюс крест
/// инструмент × исход × расстояние на каждый инструмент. Формула считается
/// от размеров самих массивов корзин, а не отдельным числом: для пула из
/// десяти при полной пригодности — 29 + 150 = 179.
pub fn nominal_grid_size(n_instruments: usize) -> usize {
    let marginals = n_instruments
        + SIDE_LABELS.len()
        + SIZE_LABELS.len()
        + DISTANCE_LABELS.len()
        + LIFETIME_LABELS.len()
        + REPEAT_LABELS.len()
        + OUTCOME_LABELS.len();
    let cross_per_instrument = OUTCOME_LABELS.len() * DISTANCE_LABELS.len();
    marginals + n_instruments * cross_per_instrument
}

/// Фактическое число испытаний — длина построенной сетки (Decision 26а:
/// только пригодные). Именно это число идёт в `runs.csv` и в DSR.
pub fn actual_trials(grid: &[String]) -> usize {
    grid.len()
}

/// Проверяет вход DSR: длина среза пробных Sharpe обязана равняться числу
/// посчитанных профилей. Несовпадение — отказ, а не молчаливое занижение.
pub fn require_dsr_trials(trial_sharpes_len: usize, grid_len: usize) -> Result<(), ShortlistError> {
    if trial_sharpes_len != grid_len {
        return Err(ShortlistError::TrialsMismatch {
            expected: grid_len,
            got: trial_sharpes_len,
        });
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Заморозка шорт-листа.
// ---------------------------------------------------------------------------

/// Замороженный шорт-лист: отсортированный список, коммит и отпечаток.
/// Отпечаток — FNV-1a 64 по списку (шестнадцатеричная печать в отчёте).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FrozenShortlist {
    /// Профили-кандидаты, по возрастанию.
    ids: Vec<String>,
    /// Хеш коммита с шорт-листом (журнальная проверка — вне модуля).
    commit: String,
    /// Число испытаний, из которых отобрано (длина сетки на момент отбора).
    trials: usize,
    /// Отпечаток списка.
    fingerprint: u64,
}

fn fnv1a64(ids: &[String]) -> u64 {
    const OFFSET: u64 = 14_695_981_039_346_656_037;
    const PRIME: u64 = 1_099_511_628_211;
    let mut h = OFFSET;
    for id in ids {
        for b in id.as_bytes() {
            h ^= u64::from(*b);
            h = h.wrapping_mul(PRIME);
        }
        h ^= 0xFF;
        h = h.wrapping_mul(PRIME);
    }
    h
}

impl FrozenShortlist {
    /// Профили-кандидаты.
    pub fn ids(&self) -> &[String] {
        &self.ids
    }
    /// Коммит заморозки.
    pub fn commit(&self) -> &str {
        &self.commit
    }
    /// Число испытаний, из которых отобрано.
    pub fn trials(&self) -> usize {
        self.trials
    }
    /// Отпечаток списка.
    pub fn fingerprint(&self) -> u64 {
        self.fingerprint
    }
    /// Отпечаток шестнадцатеричной строкой для отчёта.
    pub fn fingerprint_hex(&self) -> String {
        format!("{:016x}", self.fingerprint)
    }
    /// Входит ли профиль в список.
    pub fn contains(&self, id: &str) -> bool {
        self.ids.iter().any(|x| x == id)
    }
}

/// Замораживает шорт-лист: сортирует, снимает дубликаты, считает отпечаток.
/// `commit` — хеш коммита с шорт-листом; `trials` — длина сетки, из которой
/// отбирали (печатается в отчёте рядом с поправкой).
pub fn freeze_shortlist(ids: &[String], commit: &str, trials: usize) -> FrozenShortlist {
    let mut sorted = ids.to_vec();
    sorted.sort();
    sorted.dedup();
    let fingerprint = fnv1a64(&sorted);
    FrozenShortlist {
        ids: sorted,
        commit: commit.to_string(),
        trials,
        fingerprint,
    }
}

/// Требует заморозку перед обращением к подтверждающей: `None` — отказ.
pub fn require_frozen(
    frozen: Option<&FrozenShortlist>,
) -> Result<&FrozenShortlist, ShortlistError> {
    frozen.ok_or(ShortlistError::NotFrozen)
}

/// Требует членство профиля в замороженном списке: чужой — отказ
/// (эквивалент ненулевого кода вызывающей команды).
pub fn require_member(frozen: &FrozenShortlist, id: &str) -> Result<(), ShortlistError> {
    if frozen.contains(id) {
        Ok(())
    } else {
        Err(ShortlistError::OutOfShortlist { id: id.to_string() })
    }
}

// ---------------------------------------------------------------------------
// Отбор только на разведочной.
// ---------------------------------------------------------------------------

/// Профиль на разведочной: идентификатор сетки плюс зачётные счётчики.
/// Числа приходят от `cells`/`costs` вызывающего; модуль их не меряет.
#[derive(Debug, Clone, PartialEq)]
pub struct ExplProfile {
    /// Идентификатор из `build_profile_grid`.
    pub id: String,
    /// Наблюдений на разведочной.
    pub n: u64,
    /// Годных суток с наблюдением на разведочной.
    pub g: u64,
}

/// Отбирает шорт-лист только на разведочной: `n >= 100`. Подтверждающие
/// числа сюда не входят даже аргументами: красивый только на подтверждающей
/// профиль попасть в список не может по построению.
pub fn select_shortlist(expl: &[ExplProfile]) -> Vec<String> {
    let mut out: Vec<String> = expl
        .iter()
        .filter(|p| p.n >= SHORTLIST_MIN_N_EXPL)
        .map(|p| p.id.clone())
        .collect();
    out.sort();
    out.dedup();
    out
}

// ---------------------------------------------------------------------------
// Подтверждающая только по шорт-листу; вердикт — она.
// ---------------------------------------------------------------------------

/// Профиль на подтверждающей. Интервал `net_fill` приходит от вызывающего
/// (бутстрап `cells` + издержки `costs`); модуль читает только его низ.
#[derive(Debug, Clone, PartialEq)]
pub struct ConfProfile {
    /// Идентификатор из замороженного списка.
    pub id: String,
    /// Наблюдений на подтверждающей.
    pub n: u64,
    /// Годных суток с наблюдением на подтверждающей.
    pub g: u64,
    /// Точечный `net_fill`, bps.
    pub net_fill: Option<f64>,
    /// Нижняя граница интервала `net_fill`, bps.
    pub net_fill_lower: Option<f64>,
}

/// Статус профиля на подтверждающей. Печатаются все три: красивый на
/// разведочной и пустой на подтверждающей — неподтверждённый, а не исчезнувший.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConfirmStatus {
    /// Достаточно данных и низ интервала строго выше нуля.
    Confirmed,
    /// Достаточно данных, но эджа нет (низ неположителен или отсутствует).
    Unconfirmed,
    /// Недобор (`n < 100` или `G < G_MIN`, включая отсутствие профиля вовсе).
    InsufficientData,
}

impl std::fmt::Display for ConfirmStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ConfirmStatus::Confirmed => write!(f, "confirmed"),
            ConfirmStatus::Unconfirmed => write!(f, "unconfirmed"),
            ConfirmStatus::InsufficientData => write!(f, "insufficient"),
        }
    }
}

/// Решает статус одного профиля строго по §7: сначала зачёт по `n`/`G`,
/// затем знак низа интервала (конечный и строго положительный).
pub fn decide_profile(n: u64, g: u64, net_fill_lower: Option<f64>) -> ConfirmStatus {
    if n < CONFIRM_MIN_N || g < G_MIN as u64 {
        return ConfirmStatus::InsufficientData;
    }
    match net_fill_lower {
        Some(v) if v.is_finite() && v > 0.0 => ConfirmStatus::Confirmed,
        _ => ConfirmStatus::Unconfirmed,
    }
}

/// Строка подтверждающей таблицы: профиль замороженного списка с его
/// подтверждающими числами и статусом.
#[derive(Debug, Clone, PartialEq)]
pub struct ConfirmRow {
    /// Профиль.
    pub id: String,
    /// Наблюдений на подтверждающей (0 — профиль не пришёл вовсе).
    pub n: u64,
    /// Годных суток (0 — профиль не пришёл вовсе).
    pub g: u64,
    /// Точечный `net_fill`, bps.
    pub net_fill: Option<f64>,
    /// Низ интервала, bps.
    pub net_fill_lower: Option<f64>,
    /// Статус.
    pub status: ConfirmStatus,
}

impl ConfirmRow {
    /// Подтверждён.
    pub fn is_confirmed(&self) -> bool {
        self.status == ConfirmStatus::Confirmed
    }
    /// Строка таблицы отчёта.
    pub fn format_line(&self) -> String {
        format!(
            "{}: n={} G={} net_fill={} lower={} status={}",
            self.id,
            self.n,
            self.g,
            fmt_opt(self.net_fill),
            fmt_opt(self.net_fill_lower),
            self.status
        )
    }
}

fn fmt_opt(v: Option<f64>) -> String {
    match v {
        Some(x) => format!("{x:.4}"),
        None => "none".to_string(),
    }
}

/// Прогоняет подтверждающую только по шорт-листу. Первым делом требует
/// заморозку (`None` — отказ); затем проверяет, что каждый поданный профиль
/// входит в список (чужой — отказ); затем печатает строку на каждый профиль
/// списка, включая отсутствующие в подаче (они идут с нулями как
/// `insufficient`, а не исчезают).
pub fn confirmatory_table(
    frozen: Option<&FrozenShortlist>,
    conf: &[ConfProfile],
) -> Result<Vec<ConfirmRow>, ShortlistError> {
    let frozen = require_frozen(frozen)?;
    for p in conf {
        require_member(frozen, &p.id)?;
    }
    let mut by_id = std::collections::BTreeMap::new();
    for p in conf {
        by_id.insert(p.id.clone(), p);
    }
    let mut rows = Vec::with_capacity(frozen.ids.len());
    for id in &frozen.ids {
        match by_id.get(id) {
            Some(p) => rows.push(ConfirmRow {
                id: id.clone(),
                n: p.n,
                g: p.g,
                net_fill: p.net_fill,
                net_fill_lower: p.net_fill_lower,
                status: decide_profile(p.n, p.g, p.net_fill_lower),
            }),
            None => rows.push(ConfirmRow {
                id: id.clone(),
                n: 0,
                g: 0,
                net_fill: None,
                net_fill_lower: None,
                status: ConfirmStatus::InsufficientData,
            }),
        }
    }
    Ok(rows)
}

/// Вердикт шага 5.4 по подтверждающей (шкала Goal): красный — ни один профиль
/// не подтверждён; зелёный без ёмкости — подтверждённые есть, но лучший
/// `net_fill` ниже H6 (3 bps); зелёный — лучший не ниже H6.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ShortlistVerdict {
    /// Ни один профиль не подтверждён.
    RedNoConfirm,
    /// Эдж есть, работу не окупает.
    GreenThin,
    /// Прошедший профиль с `net_fill >= H6`.
    Green,
}

impl std::fmt::Display for ShortlistVerdict {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ShortlistVerdict::RedNoConfirm => write!(f, "RED: ни один профиль не подтверждён"),
            ShortlistVerdict::GreenThin => write!(f, "GREEN-thin: эдж есть, ёмкости нет"),
            ShortlistVerdict::Green => write!(f, "GREEN: net_fill>=H6"),
        }
    }
}

/// Выносит вердикт только по подтверждающим строкам. Разведочные числа сюда
/// не входят даже аргументами.
pub fn decide_verdict(rows: &[ConfirmRow]) -> ShortlistVerdict {
    let mut best: Option<f64> = None;
    for r in rows {
        if r.is_confirmed() {
            if let Some(v) = r.net_fill {
                if v.is_finite() {
                    best = Some(best.map_or(v, |b: f64| b.max(v)));
                }
            }
        }
    }
    match best {
        None => ShortlistVerdict::RedNoConfirm,
        Some(v) if v >= GREEN_NET_BPS => ShortlistVerdict::Green,
        Some(_) => ShortlistVerdict::GreenThin,
    }
}

// ---------------------------------------------------------------------------
// Журнал испытаний: каждый посчитанный профиль — строка runs.csv.
// ---------------------------------------------------------------------------

/// Пишет в журнал по строке на каждый посчитанный профиль (все пригодные, не
/// только шорт-лист). Вид строки — подтверждающий прогон: каждая строка
/// входит в число испытаний DSR. Метку времени строкой передаёт вызывающий.
pub fn log_profile_trials(path: &Path, ts_utc: &str, ids: &[String]) -> Result<(), ShortlistError> {
    for id in ids {
        append_run_row(
            path,
            &RunRow {
                ts_utc: ts_utc.to_string(),
                symbol: String::new(),
                kind: RunKind::Confirmatory,
                detail: format!("profile {id}"),
            },
        )
        .map_err(|e| match e {
            crate::lob::runs::RunsError::Io(s) => ShortlistError::Io(s),
            crate::lob::runs::RunsError::Csv(s) => ShortlistError::Csv(s),
        })?;
    }
    Ok(())
}

/// Число испытаний для DSR/PBO из журнала (тонкая обёртка: правило «что
/// считается испытанием» живёт в `runs`). `None` — журнал битый.
pub fn trials_from_runs_csv(path: &Path) -> Option<usize> {
    crate::lob::runs::trials_from_runs_csv(path)
}

/// Число испытаний, которое ведёт модуль: пригодная сетка плюс тесты на
/// час — ровно формула спеки «Профиль» («пригодные пары плюс маргиналы плюс
/// тесты на час суток»). Печатается рядом с шорт-листом; тест сверяет его со
/// строками `runs.csv` на одной фикстуре.
pub fn total_trials(grid_len: usize, hour_tests: usize) -> usize {
    grid_len + hour_tests
}

// ---------------------------------------------------------------------------
// Тест на зависимость от часа суток (спека «Профиль»; бриф §6а: «час старта
// каждой сессии печатается, и разведка обязана показать, зависит ли профиль
// от часа»). Статистика, нуль и альфа объявляются здесь (ticket 06) и оттуда
// идут в предрегистрацию этапа 1 (`SETTLED.md` В-29, `PLAN.md` §9).
// ---------------------------------------------------------------------------

/// Одна сутки — один кластер (Decision 9 сохраняет кластеризацию по суткам
/// при переходе на короткие сессии: `2026-09-11-brief.md` §6а). `hour_utc` —
/// час старта для этого профиля в эти сутки (при нескольких сессиях за
/// сутки — среднее их часов; усредняет вызывающий, модуль ничего не меряет
/// само). `value` — headline-наблюдаемая профиля в эти сутки, тот же знак и
/// те же единицы (bps), что markout, идущий в гейт G2.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct HourDayObservation {
    /// Сутки как кластер: целое, не с плавающей точкой (A1), как кластер
    /// `stats::wild_cluster_bootstrap_t`.
    pub day: i64,
    /// Час старта UTC за эти сутки, `[0, 24)`.
    pub hour_utc: f64,
    /// Наблюдаемая профиля за эти сутки, bps.
    pub value: f64,
}

/// Отказ теста на час: тот же методический смысл, что `BootstrapError` гейта
/// G2 — «сетка учёта не даёт вынести число», а не рыночный ноль.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum HourTestError {
    /// Суток меньше `G_MIN`.
    TooFewDays { days: usize, minimum: usize },
    /// Достижимое разрешение сетки Уэбба грубее альфы.
    GridTooCoarse {
        days: usize,
        resolution: f64,
        alpha: f64,
    },
    /// Наблюдаемая не варьируется: знаменатель теста — ноль.
    DegenerateVariance { days: usize },
}

impl std::fmt::Display for HourTestError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            HourTestError::TooFewDays { days, minimum } => {
                write!(f, "суток {days}, для теста на час нужно минимум {minimum}")
            }
            HourTestError::GridTooCoarse {
                days,
                resolution,
                alpha,
            } => write!(
                f,
                "суток {days}: разрешение сетки {resolution} грубее альфы {alpha}"
            ),
            HourTestError::DegenerateVariance { days } => {
                write!(f, "суток {days}: наблюдаемая не варьируется по кластерам")
            }
        }
    }
}

impl std::error::Error for HourTestError {}

fn map_bootstrap_error(e: BootstrapError) -> HourTestError {
    match e {
        BootstrapError::TooFewClusters { clusters, minimum } => HourTestError::TooFewDays {
            days: clusters,
            minimum,
        },
        BootstrapError::GridTooCoarse {
            clusters,
            resolution,
            alpha,
        } => HourTestError::GridTooCoarse {
            days: clusters,
            resolution,
            alpha,
        },
        BootstrapError::DegenerateVariance { clusters } => {
            HourTestError::DegenerateVariance { days: clusters }
        }
    }
}

/// Тест на зависимость профиля от часа суток.
///
/// **Статистика**: кластерный (по суткам) бутстрап-t на весах Уэбба —
/// та же машина, что гейт G2 (`stats::wild_cluster_bootstrap_t`, Decision 9),
/// применённая к другой наблюдаемой: кросс-произведению
/// `(час_суток − средний час) × (value − среднее value)` по суткам. Одна
/// сутки — одно значение кросс-произведения — один кластер: перебор знаков
/// Уэбба по суткам эквивалентен рандомизационному тесту нулевой линейной
/// связи часа и наблюдаемой (H0 ниже), не изобретённой заново статистике —
/// это стандартный приём (тест корреляции знаковой перестановкой), собранный
/// из уже объявленных частей.
///
/// **Нуль `H0`**: между часом старта суток и наблюдаемой профиля нет
/// линейной связи — среднее кросс-произведение по суткам равно нулю.
/// Двусторонний, в отличие от гейта G2: Decision 14 фиксирует знак markout
/// заранее, а для часа предсказанного направления нет. Двусторонний `p`
/// получается удвоением меньшего из двух односторонних `p`
/// (`wild_cluster_bootstrap_t` на прямом и на отрицательном рядах,
/// стандартный приём получения двустороннего `p` из одностороннего теста).
///
/// **Альфа**: `stats::GATE_ALPHA` — единственная объявленная альфа во всём
/// плане (см. документацию модуля `stats`); отдельной альфы для этого теста
/// нигде не назначено, а назначать вторую самому — вторая изобретённая
/// константа там, где годится уже названная (§9 задачи).
///
/// Возвращает `Err`, а не подделывает число, когда суток меньше `G_MIN`,
/// сетка Уэбба грубее альфы, или наблюдаемая не варьируется — три ровно те
/// же методические причины отказа, что и у гейта G2.
pub fn hour_dependence_test(
    observations: &[HourDayObservation],
    replications: u32,
    seed: u64,
) -> Result<f64, HourTestError> {
    if observations.is_empty() {
        return Err(HourTestError::TooFewDays {
            days: 0,
            minimum: G_MIN,
        });
    }
    let n = observations.len() as f64;
    let mean_hour: f64 = observations.iter().map(|o| o.hour_utc).sum::<f64>() / n;
    let mean_value: f64 = observations.iter().map(|o| o.value).sum::<f64>() / n;
    let products: Vec<(i64, f64)> = observations
        .iter()
        .map(|o| (o.day, (o.hour_utc - mean_hour) * (o.value - mean_value)))
        .collect();
    let negated: Vec<(i64, f64)> = products.iter().map(|&(d, v)| (d, -v)).collect();

    let p_pos = stats::wild_cluster_bootstrap_t(&products, replications, GATE_ALPHA, seed)
        .map_err(map_bootstrap_error)?;
    let p_neg = stats::wild_cluster_bootstrap_t(&negated, replications, GATE_ALPHA, seed)
        .map_err(map_bootstrap_error)?;
    Ok((2.0 * p_pos.min(p_neg)).min(1.0))
}

/// Пишет тест на час в журнал испытаний: испытание, как и любой посчитанный
/// профиль (спека «Профиль»: «плюс тесты на час суток» в числе для DSR).
/// `RunKind::Confirmatory` — тот же вид строки, что `log_profile_trials`,
/// потому что оба считаются испытанием одинаково (`RunKind::counts_as_trial`).
pub fn log_hour_test(
    path: &Path,
    ts_utc: &str,
    profile_id: &str,
    p_two_sided: f64,
) -> Result<(), ShortlistError> {
    append_run_row(
        path,
        &RunRow {
            ts_utc: ts_utc.to_string(),
            symbol: String::new(),
            kind: RunKind::Confirmatory,
            detail: format!("hour_test {profile_id} p={p_two_sided:.6} alpha={GATE_ALPHA}"),
        },
    )
    .map_err(|e| match e {
        crate::lob::runs::RunsError::Io(s) => ShortlistError::Io(s),
        crate::lob::runs::RunsError::Csv(s) => ShortlistError::Csv(s),
    })
}

// ---------------------------------------------------------------------------
// Выход profiles-<дата>.csv: полная таблица без отсева.
// ---------------------------------------------------------------------------

/// Строка таблицы профилей: разведочные числа §4 плюс флаг шорт-листа.
/// Числа приходят от вызывающего (`cells`/`costs`/очередь); модуль их только
/// печатает. Непригодных профилей здесь нет по построению входа.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ProfileRow {
    /// Идентификатор из `build_profile_grid`.
    pub profile_id: String,
    /// Наблюдений на разведочной.
    pub n_expl: u64,
    /// Годных суток на разведочной.
    pub g_expl: u64,
    /// Доля `eaten`.
    pub eaten_share: Option<f64>,
    /// Доля `pulled`.
    pub pulled_share: Option<f64>,
    /// Доля `mixed`.
    pub mixed_share: Option<f64>,
    /// Markout 100 мс, bps.
    pub m_100ms: Option<f64>,
    /// Markout 1 с, bps.
    pub m_1s: Option<f64>,
    /// Markout 10 с, bps (горизонт вердиктной ячейки).
    pub m_10s: Option<f64>,
    /// Markout 60 с, bps.
    pub m_60s: Option<f64>,
    /// Чистый результат после круговых издержек, bps.
    pub net_bps: Option<f64>,
    /// Доля исполнившихся входов за 2 с.
    pub fill: Option<f64>,
    /// `net`, взвешенный на `fill`.
    pub net_fill: Option<f64>,
    /// Нижняя граница интервала `net_fill`, bps.
    pub net_fill_lower: Option<f64>,
    /// Вошёл ли профиль в замороженный шорт-лист.
    pub in_shortlist: bool,
}

/// Шапка `profiles-<дата>.csv` — те же имена и в том же порядке, что поля
/// `ProfileRow`. Пишется вручную: дрейф ловит тест.
pub const PROFILES_HEADER: [&str; 15] = [
    "profile_id",
    "n_expl",
    "g_expl",
    "eaten_share",
    "pulled_share",
    "mixed_share",
    "m_100ms",
    "m_1s",
    "m_10s",
    "m_60s",
    "net_bps",
    "fill",
    "net_fill",
    "net_fill_lower",
    "in_shortlist",
];

/// Пишет полную таблицу профилей (строка на каждый пригодный профиль).
pub fn write_profiles_csv(path: &Path, rows: &[ProfileRow]) -> Result<(), ShortlistError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let file = File::create(path)?;
    let mut w = csv::WriterBuilder::new()
        .has_headers(false)
        .from_writer(file);
    w.write_record(PROFILES_HEADER)?;
    for row in rows {
        w.serialize(row)?;
    }
    w.flush()?;
    Ok(())
}

/// Читает таблицу профилей. Пустой или отсутствующий файл — ноль строк.
pub fn read_profiles_csv(path: &Path) -> Result<Vec<ProfileRow>, ShortlistError> {
    if std::fs::metadata(path)
        .map(|m| m.len() == 0)
        .unwrap_or(true)
    {
        return Ok(Vec::new());
    }
    let mut r = csv::Reader::from_path(path)?;
    r.deserialize::<ProfileRow>()
        .collect::<Result<Vec<_>, _>>()
        .map_err(ShortlistError::from)
}

// ---------------------------------------------------------------------------
// Выход shortlist-<дата>.md.
// ---------------------------------------------------------------------------

/// Пишет шорт-лист: профили с подтверждающими числами, фактическое число
/// испытаний и поправка, с которой взят порог, плюс вердикт подтверждающей.
#[allow(clippy::too_many_arguments)]
pub fn write_shortlist_md(
    path: &Path,
    date: &str,
    frozen: &FrozenShortlist,
    rows: &[ConfirmRow],
    verdict: ShortlistVerdict,
) -> Result<(), ShortlistError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut text = String::new();
    text.push_str(&format!("# Shortlist {date}\n"));
    text.push_str(&format!("trials: {}\n", frozen.trials()));
    text.push_str(&format!("freeze_commit: {}\n", frozen.commit()));
    text.push_str(&format!("fingerprint: {}\n", frozen.fingerprint_hex()));
    text.push_str("threshold: n>=100 G>=12 net_fill_lower>0 (DSR by actual trials)\n");
    text.push_str("| profile_id | n_conf | g_conf | net_fill | lower | status |\n");
    for r in rows {
        text.push_str(&format!(
            "| {} | {} | {} | {} | {} | {} |\n",
            r.id,
            r.n,
            r.g,
            fmt_opt(r.net_fill),
            fmt_opt(r.net_fill_lower),
            r.status
        ));
    }
    text.push_str(&format!("verdict: {verdict}\n"));
    let mut f = File::create(path)?;
    f.write_all(text.as_bytes())?;
    f.flush()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn days(prefix: &str, from: u32, to: u32) -> Vec<String> {
        (from..=to).map(|d| format!("{prefix}{d:02}")).collect()
    }

    fn ten_coverages_all_wide() -> Vec<InstrumentCoverage> {
        [
            "SOLUSDT",
            "ZECUSDT",
            "XRPUSDT",
            "HYPEUSDT",
            "NEARUSDT",
            "DOGEUSDT",
            "VVVUSDT",
            "PUMPFUNUSDT",
            "IOSTUSDT",
            "USELESSUSDT",
        ]
        .iter()
        .map(|s| InstrumentCoverage {
            symbol: (*s).to_string(),
            coverage_bps: 300.0,
        })
        .collect()
    }

    fn close(a: f64, b: f64) -> bool {
        (a - b).abs() < 1e-9
    }

    /// Деление 60/40 по календарю до анализа: десять суток дают 6/4, порядок
    /// подачи не влияет, граница — между шестыми и седьмыми сутками.
    #[test]
    fn split_is_calendar_60_40_before_any_analysis() {
        let mut input = days("2026-05-", 1, 10);
        input.reverse();
        let split = split_calendar(&input).expect("десять суток делятся");
        assert_eq!(split.exploratory.len(), 6);
        assert_eq!(split.confirmatory.len(), 4);
        assert_eq!(split.exploratory[0], "2026-05-01");
        assert_eq!(split.exploratory[5], "2026-05-06");
        assert_eq!(split.confirmatory[0], "2026-05-07");
        assert_eq!(split.confirmatory[3], "2026-05-10");
    }

    /// Мало суток, дубликаты и мусор — отказ, а не сдвиг границы.
    #[test]
    fn split_refuses_degenerate_input() {
        assert!(split_calendar(&[]).is_err());
        assert!(split_calendar(&["2026-05-01".to_string()]).is_err());
        let dup = vec!["2026-05-01".to_string(), "2026-05-01".to_string()];
        assert_eq!(
            split_calendar(&dup),
            Err(ShortlistError::DuplicateDay {
                day: "2026-05-01".to_string()
            })
        );
        let bad = vec!["2026-05-01".to_string(), "01.05.2026".to_string()];
        assert!(matches!(
            split_calendar(&bad),
            Err(ShortlistError::BadDay { .. })
        ));
        // Двое суток делятся 1/1: обе половины непусты.
        let two = vec!["2026-05-01".to_string(), "2026-05-02".to_string()];
        let split = split_calendar(&two).expect("двое суток делятся");
        assert_eq!(split.exploratory.len(), 1);
        assert_eq!(split.confirmatory.len(), 1);
    }

    /// R-C ревью таска 06: маргиналы по каждой оси (повторяемость теперь
    /// трёхзначна — 29 на десять инструментов) плюс единственный
    /// предрегистрированный крест инструмент × исход × расстояние (150 при
    /// полной пригодности) — не полный крест семи осей. Формула и
    /// построенная сетка обязаны совпасть.
    #[test]
    fn nominal_grid_is_29_plus_150_for_ten_instruments() {
        assert_eq!(nominal_grid_size(10), 179);
        let grid = build_profile_grid(&ten_coverages_all_wide()).expect("сетка");
        assert_eq!(
            grid.len(),
            179,
            "маргиналы 29 + крест инструмент×исход×расстояние 150"
        );
        assert_eq!(actual_trials(&grid), 179);
        assert!(grid.contains(&"marginal:side=bid".to_string()));
        assert!(grid.contains(&"marginal:dist=[0,1)".to_string()));
        assert!(grid.contains(&"marginal:repeat=2".to_string()));
        assert!(grid.contains(&"cross:SOLUSDT|pulled|[0,1)".to_string()));
    }

    /// Decision 26а буквально: непригодная корзина отсутствует, а не ноль.
    /// ZEC с покрытием 4 bps видит только корзины до 4 bps.
    #[test]
    fn ineligible_basket_absent_not_zero() {
        let coverages = vec![InstrumentCoverage {
            symbol: "ZECUSDT".to_string(),
            coverage_bps: 4.0,
        }];
        let grid = build_profile_grid(&coverages).expect("сетка");
        // Маргиналы для одного инструмента: 1 + 19 = 20 (повторяемость трёхзначна).
        // Крест: пригодны [0,1) и [1,2.5) × 3 исхода = 6.
        assert_eq!(grid.len(), 20 + 6, "дальние корзины отсутствуют: {grid:?}");
        for bad in ["[2.5,5)", "[5,10)", "[10,25)"] {
            assert!(
                !grid.contains(&format!("cross:ZECUSDT|pulled|{bad}")),
                "непригодная корзина {bad} обязана отсутствовать, а не быть нулём"
            );
        }
        assert!(
            !grid.contains(&"cross:ZECUSDT|pulled|[10,25)".to_string()),
            "непригодная корзина обязана отсутствовать, а не быть нулём"
        );
        assert!(grid.contains(&"cross:ZECUSDT|pulled|[0,1)".to_string()));
        // Пустой пул и плохое покрытие — отказ.
        assert_eq!(build_profile_grid(&[]), Err(ShortlistError::EmptyPool));
        assert_eq!(
            build_profile_grid(&[InstrumentCoverage {
                symbol: "X".to_string(),
                coverage_bps: f64::NAN,
            }]),
            Err(ShortlistError::BadCoverage {
                symbol: "X".to_string()
            })
        );
    }

    /// Повторяемость — трёхзначная (`1`, `2`, `>=3`), не двузначная: спека
    /// «Профиль» и бриф §3 буквально требуют три корзины. `repeat_count`
    /// считает прошлые рождения 0-based (`lob/levels.rs`), поэтому «ровно
    /// один раз» — `repeat_count == 0`, не `<= 1`.
    #[test]
    fn repeat_axis_has_three_buckets() {
        assert_eq!(REPEAT_LABELS, ["1", "2", ">=3"]);
        assert_eq!(repeat_bucket(0), "1");
        assert_eq!(repeat_bucket(1), "2");
        assert_eq!(repeat_bucket(2), ">=3");
        assert_eq!(repeat_bucket(100), ">=3");
    }

    /// Граница пригодности включительно: hi == coverage наблюдаемо.
    #[test]
    fn eligibility_boundary_is_inclusive() {
        assert!(distance_bucket_eligible(10.0, 5.0, 10.0));
        assert!(!distance_bucket_eligible(9.99, 5.0, 10.0));
        assert!(!distance_bucket_eligible(f64::NAN, 0.0, 1.0));
        assert!(!distance_bucket_eligible(300.0, 2.5, 2.5));
    }

    /// Шорт-лист только на разведочной: порог n>=100, сортировка, дубликаты
    /// сняты; красивый только на подтверждающей сюда не входит по построению
    /// (вход — лишь разведочные счётчики).
    #[test]
    fn shortlist_comes_from_exploratory_only() {
        let expl = vec![
            ExplProfile {
                id: "cross:A|pulled|[0,1)".to_string(),
                n: 150,
                g: 9,
            },
            ExplProfile {
                id: "cross:B|eaten|[1,2.5)".to_string(),
                n: 99,
                g: 20,
            },
            ExplProfile {
                id: "cross:A|pulled|[0,1)".to_string(),
                n: 150,
                g: 9,
            },
        ];
        let list = select_shortlist(&expl);
        assert_eq!(list, vec!["cross:A|pulled|[0,1)".to_string()]);
    }

    /// Обращение к подтверждающей до заморозки — отказ.
    #[test]
    fn confirmatory_before_freeze_is_refusal() {
        let conf = vec![ConfProfile {
            id: "x".to_string(),
            n: 150,
            g: 12,
            net_fill: Some(5.0),
            net_fill_lower: Some(1.0),
        }];
        assert_eq!(
            confirmatory_table(None, &conf),
            Err(ShortlistError::NotFrozen)
        );
        assert_eq!(require_frozen(None), Err(ShortlistError::NotFrozen));
    }

    /// Профиль вне шорт-листа на подтверждающей — отказ (механическая
    /// заморозка: эквивалент ненулевого кода команды).
    #[test]
    fn confirmatory_outside_shortlist_is_refusal() {
        let frozen = freeze_shortlist(&["a".to_string()], "commit-1", 178);
        assert!(require_member(&frozen, "a").is_ok());
        assert_eq!(
            require_member(&frozen, "b"),
            Err(ShortlistError::OutOfShortlist {
                id: "b".to_string()
            })
        );
        let conf = vec![ConfProfile {
            id: "b".to_string(),
            n: 200,
            g: 12,
            net_fill: Some(5.0),
            net_fill_lower: Some(1.0),
        }];
        assert_eq!(
            confirmatory_table(Some(&frozen), &conf),
            Err(ShortlistError::OutOfShortlist {
                id: "b".to_string()
            })
        );
    }

    /// Профиль, красивый на разведочной и пустой на подтверждающей,
    /// печатается как неподтверждённый, а не исчезает.
    #[test]
    fn beautiful_exploratory_empty_confirmatory_stays_unconfirmed() {
        let expl = vec![ExplProfile {
            id: "cross:SOLUSDT|pulled|[0,1)".to_string(),
            n: 500,
            g: 15,
        }];
        let ids = select_shortlist(&expl);
        let frozen = freeze_shortlist(&ids, "commit-beautiful", 178);
        // На подтверждающей профиля нет вовсе: подача пуста.
        let rows = confirmatory_table(Some(&frozen), &[]).expect("подтв");
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].id, "cross:SOLUSDT|pulled|[0,1)");
        assert_eq!(rows[0].n, 0);
        assert_eq!(rows[0].status, ConfirmStatus::InsufficientData);
        assert!(!rows[0].is_confirmed());
        let line = rows[0].format_line();
        assert!(line.contains("cross:SOLUSDT|pulled|[0,1)"), "{line}");
        // Вердикт по такой таблице — красный, а не тишина.
        assert_eq!(decide_verdict(&rows), ShortlistVerdict::RedNoConfirm);
    }

    /// Статусы §7: зачёт по n/G, затем знак низа интервала.
    #[test]
    fn confirm_status_reads_counts_then_lower_bound() {
        assert_eq!(decide_profile(100, 12, Some(0.1)), ConfirmStatus::Confirmed);
        assert_eq!(
            decide_profile(99, 12, Some(5.0)),
            ConfirmStatus::InsufficientData
        );
        assert_eq!(
            decide_profile(100, 6, Some(5.0)),
            ConfirmStatus::InsufficientData
        );
        assert_eq!(
            decide_profile(100, 12, Some(0.0)),
            ConfirmStatus::Unconfirmed
        );
        assert_eq!(decide_profile(100, 12, None), ConfirmStatus::Unconfirmed);
        assert_eq!(
            decide_profile(100, 12, Some(f64::NAN)),
            ConfirmStatus::Unconfirmed
        );
    }

    /// Ровно `G_MIN` (7, таск 01: `CONFIRM_MIN_G` сведён к этой константе,
    /// объявленной в `stats::G_MIN`) обязано зачитываться, а не отказывать —
    /// граница включает минимум. Прежний отдельный порог этого модуля (12)
    /// отказал бы ровно на семи годных сутках.
    #[test]
    fn confirm_status_accepts_exactly_g_min_good_days() {
        assert_eq!(G_MIN, 7);
        assert_eq!(
            decide_profile(100, G_MIN as u64, Some(0.1)),
            ConfirmStatus::Confirmed
        );
    }

    /// Вердикт выносит подтверждающая: красный без подтверждённых, тонкий
    /// зелёный ниже H6, полный зелёный не ниже H6.
    #[test]
    fn verdict_comes_from_confirmatory_only() {
        assert!(close(GREEN_NET_BPS, 3.0));
        let red = vec![ConfirmRow {
            id: "a".to_string(),
            n: 150,
            g: 12,
            net_fill: Some(-1.0),
            net_fill_lower: Some(-2.0),
            status: ConfirmStatus::Unconfirmed,
        }];
        assert_eq!(decide_verdict(&red), ShortlistVerdict::RedNoConfirm);
        assert_eq!(decide_verdict(&[]), ShortlistVerdict::RedNoConfirm);
        let thin = vec![ConfirmRow {
            id: "a".to_string(),
            n: 150,
            g: 12,
            net_fill: Some(2.9),
            net_fill_lower: Some(0.5),
            status: ConfirmStatus::Confirmed,
        }];
        assert_eq!(decide_verdict(&thin), ShortlistVerdict::GreenThin);
        let green = vec![ConfirmRow {
            id: "a".to_string(),
            n: 150,
            g: 12,
            net_fill: Some(3.0),
            net_fill_lower: Some(0.5),
            status: ConfirmStatus::Confirmed,
        }];
        assert_eq!(decide_verdict(&green), ShortlistVerdict::Green);
    }

    /// Все посчитанные профили идут в runs.csv, и число строк равно
    /// фактическому числу испытаний; DSR-вход проверяется на равенство.
    #[test]
    fn runs_csv_holds_every_counted_profile() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("runs.csv");
        let grid = build_profile_grid(&ten_coverages_all_wide()).expect("сетка");
        log_profile_trials(&path, "2026-05-20T00:00:00Z", &grid).expect("журнал");
        assert_eq!(trials_from_runs_csv(&path), Some(grid.len()));
        assert!(require_dsr_trials(grid.len(), grid.len()).is_ok());
        assert_eq!(
            require_dsr_trials(grid.len() - 28, grid.len()),
            Err(ShortlistError::TrialsMismatch {
                expected: grid.len(),
                got: grid.len() - 28
            })
        );
        // Урезанная сетка (непригодные исключены) даёт меньше строк.
        let narrow = vec![InstrumentCoverage {
            symbol: "ZECUSDT".to_string(),
            coverage_bps: 4.0,
        }];
        let grid2 = build_profile_grid(&narrow).expect("узкая сетка");
        let path2 = dir.path().join("runs2.csv");
        log_profile_trials(&path2, "2026-05-20T00:00:00Z", &grid2).expect("журнал");
        assert_eq!(trials_from_runs_csv(&path2), Some(grid2.len()));
        assert!(grid2.len() < grid.len());
    }

    /// Обязательный тест ticket 06: число испытаний, которое печатает модуль
    /// (`total_trials`: сетка плюс тесты на час), совпадает с числом строк
    /// `runs.csv` на общей фикстуре — и профили, и тесты на час идут в один
    /// журнал одной и той же строкой-испытанием.
    #[test]
    fn module_trial_count_matches_runs_csv_row_count() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("runs.csv");
        let narrow = vec![InstrumentCoverage {
            symbol: "ZECUSDT".to_string(),
            coverage_bps: 4.0,
        }];
        let grid = build_profile_grid(&narrow).expect("сетка");
        log_profile_trials(&path, "2026-05-20T00:00:00Z", &grid).expect("журнал профилей");
        let hour_test_ids = ["marginal:instrument=ZECUSDT", "cross:ZECUSDT|..."];
        for id in hour_test_ids {
            log_hour_test(&path, "2026-05-20T00:05:00Z", id, 0.5).expect("журнал часа");
        }
        let printed = total_trials(grid.len(), hour_test_ids.len());
        assert_eq!(trials_from_runs_csv(&path), Some(printed));
    }

    /// Известная величина, посчитанная вручную: `value = 10 × hour_utc` на
    /// семи сутках (`hour_utc = 1..7`) даёт кросс-произведение
    /// `10 × (hour − 4)²` по каждым суткам — `[90, 40, 10, 0, 10, 40, 90]`,
    /// строго неотрицательное и явно ненулевое в среднем. Сильная линейная
    /// связь обязана быть отвергнута на альфе гейта.
    #[test]
    fn hour_dependence_test_rejects_strong_known_trend() {
        let obs: Vec<HourDayObservation> = (1..=7)
            .map(|d| HourDayObservation {
                day: d,
                hour_utc: d as f64,
                value: 10.0 * d as f64,
            })
            .collect();
        let p = hour_dependence_test(&obs, 999, 7).expect("семь суток — минимум набран");
        assert!(
            p <= GATE_ALPHA,
            "сильный линейный тренд обязан пройти альфу гейта: p={p}"
        );
    }

    /// Известная величина: `value = (hour − 4)²` — чётная функция от
    /// отклонения часа, само отклонение часа — нечётная функция, поэтому
    /// сумма их произведений по семи суткам равна нулю ровно по симметрии
    /// (`Σ [-15,0,3,0,-3,0,15] = 0`), не по случайности данных. Наблюдаемая
    /// при этом варьируется (не вырождена), так что тест обязан вернуть
    /// число, а не отказ, и это число не должно проходить альфу.
    #[test]
    fn hour_dependence_test_does_not_reject_symmetric_no_trend() {
        let obs: Vec<HourDayObservation> = (1..=7)
            .map(|d| {
                let dev = d as f64 - 4.0;
                HourDayObservation {
                    day: d,
                    hour_utc: d as f64,
                    value: dev * dev,
                }
            })
            .collect();
        let p = hour_dependence_test(&obs, 999, 11).expect("наблюдаемая варьируется");
        assert!(
            p > GATE_ALPHA,
            "кросс-произведение по симметрии в среднем нулевое: p={p} обязан быть больше альфы"
        );
    }

    /// Наблюдаемая, тождественно равная по всем суткам, вырождает
    /// кросс-произведение в ноль на каждой сутки — знаменатель теста ноль,
    /// и это методический отказ (как `BootstrapError::DegenerateVariance` у
    /// гейта G2), а не подделанное число.
    #[test]
    fn hour_dependence_test_refuses_on_degenerate_variance() {
        let obs: Vec<HourDayObservation> = (1..=7)
            .map(|d| HourDayObservation {
                day: d,
                hour_utc: d as f64,
                value: 5.0,
            })
            .collect();
        assert_eq!(
            hour_dependence_test(&obs, 999, 1),
            Err(HourTestError::DegenerateVariance { days: 7 })
        );
    }

    /// Меньше `G_MIN` суток — тот же методический отказ, что у гейта G2, а
    /// не заниженное число.
    #[test]
    fn hour_dependence_test_refuses_below_g_min_days() {
        let obs: Vec<HourDayObservation> = (1..=6)
            .map(|d| HourDayObservation {
                day: d,
                hour_utc: d as f64,
                value: 10.0 * d as f64,
            })
            .collect();
        assert_eq!(
            hour_dependence_test(&obs, 999, 1),
            Err(HourTestError::TooFewDays {
                days: 6,
                minimum: G_MIN
            })
        );
        assert_eq!(
            hour_dependence_test(&[], 999, 1),
            Err(HourTestError::TooFewDays {
                days: 0,
                minimum: G_MIN
            })
        );
    }

    /// Формат журнала: тест на час пишется `RunKind::Confirmatory` — тем же
    /// видом строки, что и профиль, — и это испытание считается в
    /// `count_trials`.
    #[test]
    fn hour_test_log_line_counts_as_trial() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("runs.csv");
        log_hour_test(&path, "2026-05-20T00:00:00Z", "marginal:side=bid", 0.12).expect("журнал");
        let rows = crate::lob::runs::read_run_rows(&path).expect("чтение");
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].kind, RunKind::Confirmatory);
        assert!(rows[0].detail.contains("hour_test"));
        assert!(rows[0].detail.contains("marginal:side=bid"));
        assert_eq!(crate::lob::runs::count_trials(&rows), 1);
    }

    fn sample_profile_row(id: &str, in_shortlist: bool) -> ProfileRow {
        ProfileRow {
            profile_id: id.to_string(),
            n_expl: 150,
            g_expl: 9,
            eaten_share: Some(0.2),
            pulled_share: Some(0.7),
            mixed_share: Some(0.1),
            m_100ms: Some(1.0),
            m_1s: Some(2.0),
            m_10s: Some(4.0),
            m_60s: Some(3.0),
            net_bps: Some(1.5),
            fill: Some(0.5),
            net_fill: Some(0.75),
            net_fill_lower: Some(0.1),
            in_shortlist,
        }
    }

    /// Формат profiles CSV: шапка побайтово, круговой проход, пустой файл —
    /// ноль строк. Живых данных в песочнице нет: только синтетика.
    #[test]
    fn profiles_csv_format_round_trips_with_stable_header() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("profiles-2026-05-20.csv");
        let rows = vec![
            sample_profile_row("cross:A|pulled|[0,1)", true),
            sample_profile_row("marginal:side=bid", false),
        ];
        write_profiles_csv(&path, &rows).expect("писатель");
        let text = std::fs::read_to_string(&path).unwrap();
        let header = text.lines().next().expect("шапка обязана быть");
        assert_eq!(
            header,
            "profile_id,n_expl,g_expl,eaten_share,pulled_share,mixed_share,m_100ms,m_1s,m_10s,m_60s,net_bps,fill,net_fill,net_fill_lower,in_shortlist"
        );
        assert_eq!(read_profiles_csv(&path).expect("круговой проход"), rows);
        assert_eq!(
            read_profiles_csv(&dir.path().join("нет.csv")).expect("нет файла"),
            Vec::new()
        );
    }

    /// Формат shortlist MD: число испытаний, коммит, отпечаток, порог,
    /// таблица и вердикт; неподтверждённый профиль в таблице остаётся.
    #[test]
    fn shortlist_md_format_carries_trials_threshold_and_verdict() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("shortlist-2026-05-20.md");
        let frozen = freeze_shortlist(
            &[
                "cross:A|pulled|[0,1)".to_string(),
                "cross:B|eaten|[1,2.5)".to_string(),
            ],
            "abc123",
            178,
        );
        let rows = vec![
            ConfirmRow {
                id: "cross:A|pulled|[0,1)".to_string(),
                n: 150,
                g: 12,
                net_fill: Some(4.0),
                net_fill_lower: Some(1.0),
                status: ConfirmStatus::Confirmed,
            },
            ConfirmRow {
                id: "cross:B|eaten|[1,2.5)".to_string(),
                n: 0,
                g: 0,
                net_fill: None,
                net_fill_lower: None,
                status: ConfirmStatus::InsufficientData,
            },
        ];
        write_shortlist_md(&path, "2026-05-20", &frozen, &rows, ShortlistVerdict::Green)
            .expect("писатель");
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(text.contains("trials: 178"), "{text}");
        assert!(text.contains("freeze_commit: abc123"), "{text}");
        assert!(
            text.contains(&frozen.fingerprint_hex()),
            "отпечаток: {text}"
        );
        assert!(text.contains("net_fill_lower>0"), "{text}");
        assert!(text.contains("cross:A|pulled|[0,1)"), "{text}");
        assert!(
            text.contains("cross:B|eaten|[1,2.5)"),
            "неподтверждённый обязан остаться: {text}"
        );
        assert!(text.contains("verdict: GREEN"), "{text}");
    }

    /// Заморозка детерминирована: порядок подачи не влияет, дубликаты сняты,
    /// отпечаток стабилен.
    #[test]
    fn freeze_is_deterministic_over_input_order() {
        let a = freeze_shortlist(
            &["b".to_string(), "a".to_string(), "b".to_string()],
            "c",
            178,
        );
        let b = freeze_shortlist(&["a".to_string(), "b".to_string()], "c", 178);
        assert_eq!(a.ids(), &["a".to_string(), "b".to_string()]);
        assert_eq!(a.fingerprint(), b.fingerprint());
        assert_eq!(a.fingerprint_hex().len(), 16);
        assert_eq!(a.trials(), 178);
    }

    /// Синтетика вместо недель: весь модуль считается на сконструированных
    /// входах без файлов недели и без сети — явная печать связки для ревью.
    #[test]
    fn synthetic_drive_prints_split_and_verdict() {
        let split = split_calendar(&days("2026-06-", 1, 5)).expect("пять суток");
        assert_eq!(split.exploratory.len(), 3);
        assert_eq!(split.confirmatory.len(), 2);
        let frozen = freeze_shortlist(&["p1".to_string()], "synthetic", 25);
        let rows = confirmatory_table(
            Some(&frozen),
            &[ConfProfile {
                id: "p1".to_string(),
                n: 120,
                g: 12,
                net_fill: Some(1.0),
                net_fill_lower: Some(0.2),
            }],
        )
        .expect("подтв");
        let line = format!(
            "split 3/2 {} verdict={}",
            rows[0].format_line(),
            decide_verdict(&rows)
        );
        assert!(line.contains("p1"));
        assert!(line.contains("GREEN-thin"));
    }

    /// Граница модулей: чистая логика не знает про транспорт и часы.
    /// Проверка — грепом по собственному исходнику.
    #[test]
    fn module_stays_detached_from_transport_and_clocks() {
        const SRC: &str = include_str!("shortlist.rs");
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
