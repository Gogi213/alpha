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
use crate::lob::final_metrics::{required_sharpe_for_dsr, DSR_TARGET};
use crate::lob::runs::{append_run_row, log_trials, RunKind, RunRow, PROFILE_TRIAL_PREFIX};
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

/// Последняя граница корзин расстояния целым числом bps — окно «завала»
/// касания (В-45: уровни ≥ `H3` той же стороны не дальше 25 bps от цены
/// уровня, `levels::stack_window_ticks`). Целое, потому что трекер уровней
/// считает без приближённых чисел; равенство с `DISTANCE_BOUNDS_BPS`
/// проверяется на компиляции — второго числа нет.
pub const DISTANCE_MAX_BPS: i64 = 25;
const _: () = assert!(
    DISTANCE_BOUNDS_BPS[DISTANCE_BOUNDS_BPS.len() - 1].1 == DISTANCE_MAX_BPS as f64,
    "DISTANCE_MAX_BPS обязана равняться последней границе DISTANCE_BOUNDS_BPS"
);

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
// Окно «сейчас» (R57, ticket 21): слепая приёмка поймала `profiles`/
// `shortlist` на чтении всех подкаталогов `root` без окна — «сейчас»
// читалось как скользящее окно фиксированной длины, длина нигде не
// назначалась до данных и не печаталась. Решение: «сейчас» — интервал
// `[window_start, window_end]` по суткам UTC, выведенный из уже
// зафиксированной пары разведочная/подтверждающая (та же граница, тот же
// файл, тот же приём write-once, что `CalendarSplit`/В-29) — не новое число,
// а прямое следствие уже решённой границы. `window_start` — первая дата
// `exploratory:`, `window_end` — последняя дата `confirmatory:`; файл с
// одной границей без конца (`confirmatory:` пуста или отсутствует) требует
// `--window-end` вызывающего и дописывает его один раз, тем же приёмом, что
// сама граница.
// ---------------------------------------------------------------------------

/// Окно «сейчас»: границы по суткам UTC плюс исходная пара разведочная/
/// подтверждающая, из которой они выведены (нужна шапке артефакта —
/// `exploratory=d1..d2 confirmatory=d3..d4`).
#[derive(Debug, Clone, PartialEq)]
pub struct PreregisteredWindow {
    /// Первая дата разведочной части — начало окна.
    pub start: String,
    /// Последняя дата подтверждающей части (или `--window-end`) — конец окна.
    pub end: String,
    /// Разведочные сутки, по возрастанию.
    pub exploratory: Vec<String>,
    /// Подтверждающие сутки, по возрастанию (может быть пуст только сразу
    /// после дозаписи `--window-end` в файл с одной границей).
    pub confirmatory: Vec<String>,
}

impl PreregisteredWindow {
    /// Сутки внутри окна — датное сравнение по границам, не членство в
    /// дискретном списке: сессия суток, для которых `lob pick`/`session`
    /// ничего не писали в файл предрегистрации (её там просто нет), но
    /// которые лежат между `start` и `end`, всё равно внутри окна.
    pub fn contains_day(&self, day: &str) -> bool {
        day >= self.start.as_str() && day <= self.end.as_str()
    }
}

fn parse_ymd(day: &str) -> Result<chrono::NaiveDate, ShortlistError> {
    chrono::NaiveDate::parse_from_str(day, "%Y-%m-%d").map_err(|_| ShortlistError::BadDay {
        day: day.to_string(),
    })
}

/// Длина окна в календарных сутках, включительно — из границ файла, не из
/// числа сессий, которые под них попали (критерий приёмки ticket 21: длина
/// не меняется от данных).
pub fn window_length_days(window: &PreregisteredWindow) -> Result<i64, ShortlistError> {
    let start = parse_ymd(&window.start)?;
    let end = parse_ymd(&window.end)?;
    Ok((end - start).num_days() + 1)
}

fn split_csv_list_window(s: &str) -> Vec<String> {
    s.split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .collect()
}

/// Разбирает `exploratory:`/`confirmatory:` из текста файла предрегистрации
/// — тот же формат, что пишет `commands::lob::shortlist::load_or_write_boundary`
/// (тексты обоих файлов взаимно читаемы: какая бы команда ни написала файл
/// первой, вторая прочитает его без изменений).
fn parse_boundary_lines_window(text: &str) -> (Vec<String>, Vec<String>) {
    let mut exploratory = Vec::new();
    let mut confirmatory = Vec::new();
    for line in text.lines() {
        if let Some(rest) = line.strip_prefix("exploratory: ") {
            exploratory = split_csv_list_window(rest);
        } else if let Some(rest) = line.strip_prefix("confirmatory: ") {
            confirmatory = split_csv_list_window(rest);
        }
    }
    (exploratory, confirmatory)
}

/// Загружает окно «сейчас» из файла предрегистрации, либо пишет его впервые
/// (write-once, тот же приём, что граница разведочная/подтверждающая,
/// В-29): 60/40 по суткам, видимым под `root` на момент первого прогона.
/// Файл, уже существующий, только читается — новые сутки, появившиеся под
/// `root` позже, не двигают уже зафиксированное окно.
///
/// Файл с одной границей без конца (`confirmatory:` пуста или отсутствует —
/// не бывает из-под `split_calendar`, только из хендкрафченного/более
/// раннего файла) требует `window_end`: без него — отказ «окно не
/// определено», не тихое «все сессии»; с ним — конец дописывается в файл
/// один раз (`confirmatory: <window_end>`) и дальше читается как обычно.
pub fn load_or_write_window(
    path: &Path,
    days_now: &[String],
    window_end: Option<&str>,
) -> Result<PreregisteredWindow, ShortlistError> {
    if path.is_file() {
        let text = std::fs::read_to_string(path)?;
        let (exploratory, confirmatory) = parse_boundary_lines_window(&text);
        if exploratory.is_empty() {
            return Err(ShortlistError::Io(format!(
                "{}: файл предрегистрации не разобрался (нет строки exploratory:)",
                path.display()
            )));
        }
        let start = exploratory.first().cloned().unwrap_or_default();
        let (end, confirmatory) = if let Some(last) = confirmatory.last() {
            (last.clone(), confirmatory)
        } else {
            let we = window_end.ok_or_else(|| {
                ShortlistError::Io(format!(
                    "{}: файл предрегистрации несёт только границу без конца — нужен \
                     --window-end (окно «сейчас» не определено)",
                    path.display()
                ))
            })?;
            let mut f = std::fs::OpenOptions::new().append(true).open(path)?;
            writeln!(f, "confirmatory: {we}")?;
            (we.to_string(), vec![we.to_string()])
        };
        return Ok(PreregisteredWindow {
            start,
            end,
            exploratory,
            confirmatory,
        });
    }
    let split = split_calendar(days_now)?;
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let text = format!(
        "# lob profiles/shortlist: окно «сейчас» — граница разведочная/\n\
         # подтверждающая по календарю, назначена до анализа и заморожена\n\
         # (R57, SETTLED.md В-29). Файл не переписывается, пока не удалён\n\
         # вручную — это и есть заморозка окна.\n\
         exploratory: {}\n\
         confirmatory: {}\n",
        split.exploratory.join(","),
        split.confirmatory.join(","),
    );
    std::fs::write(path, &text)?;
    let start = split.exploratory.first().cloned().unwrap_or_default();
    let end = split.confirmatory.last().cloned().unwrap_or_default();
    Ok(PreregisteredWindow {
        start,
        end,
        exploratory: split.exploratory,
        confirmatory: split.confirmatory,
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
    /// Наблюдаемый Шарп (среднее/стандартное отклонение) круговых net этого
    /// профиля на подтверждающей, в единицах на наблюдение — тот же смысл,
    /// что `final_metrics::sharpe_ratio`. `None`, пока источник (реальная
    /// модель исполнения поверх `lob::backtest`, а не `NoFillModel`) не
    /// измерил круги: поправку на множественность не из чего считать, и
    /// профиль не подтверждается по построению `decide_profile` — то же
    /// правило, что раньше держало `not_measured`.
    pub observed_sharpe: Option<f64>,
    /// Размер ордера пула в долларах (`instruments.csv`/`candidates.csv`,
    /// Decision 22) — источник двух справочных долларовых колонок таблицы
    /// (R54: «доллары — справочными колонками, ни в один гейт не входят»).
    /// `None` для профилей, не привязанных к одному инструменту (маргиналы
    /// `side`/`outcome`/`size`/`life`/`repeat` — доллар не определён без
    /// единственного размера ордера).
    pub order_size_usd: Option<f64>,
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

/// Решает статус одного профиля по §7 плюс поправка на множественность
/// (R47, R51–R53, `PLAN.md` гейт G3-в): сначала зачёт по `n`/`G`, затем знак
/// низа интервала (необходимое условие, как раньше), и только затем —
/// поправка DSR по фактическому числу испытаний `total_trials` (таск 05:
/// `final_metrics::required_sharpe_for_dsr`). «Нижняя граница выше нуля»
/// сама по себе больше не вердикт: тест `confirm_status_requires_dsr_
/// correction_not_just_positive_lower_bound` ловит именно это — критерий
/// приёмки таска 13 буквально.
///
/// `observed_sharpe` — наблюдаемый Шарп круговых net на подтверждающей
/// (`ConfProfile::observed_sharpe`); `None` (модель исполнения не измеряла
/// круги — сегодня `NoFillModel`/бэктест не подключён к этому вызову) даёт
/// `Unconfirmed`, а не `Confirmed`: отсутствие измерения не может подтвердить
/// профиль (тот же принцип, что раньше отверг `net_fill_lower = None`).
pub fn decide_profile(
    n: u64,
    g: u64,
    net_fill_lower: Option<f64>,
    observed_sharpe: Option<f64>,
    total_trials: usize,
) -> ConfirmStatus {
    if n < CONFIRM_MIN_N || g < G_MIN as u64 {
        return ConfirmStatus::InsufficientData;
    }
    let lower_positive = matches!(net_fill_lower, Some(v) if v.is_finite() && v > 0.0);
    if !lower_positive {
        return ConfirmStatus::Unconfirmed;
    }
    let Some(sr) = observed_sharpe else {
        return ConfirmStatus::Unconfirmed;
    };
    if !sr.is_finite() {
        return ConfirmStatus::Unconfirmed;
    }
    match required_sharpe_for_dsr(total_trials, n as usize, DSR_TARGET) {
        Some(required) if sr >= required => ConfirmStatus::Confirmed,
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
    /// Размер ордера в долларах — см. `ConfProfile::order_size_usd`.
    pub order_size_usd: Option<f64>,
}

impl ConfirmRow {
    /// Подтверждён.
    pub fn is_confirmed(&self) -> bool {
        self.status == ConfirmStatus::Confirmed
    }
    /// `net_fill` в долларах на круг ордера пула (R54: справочная величина,
    /// ни в один гейт не входит) — `None`, когда не измерен `net_fill` или
    /// профиль не привязан к одному инструменту.
    pub fn net_fill_usd(&self) -> Option<f64> {
        match (self.net_fill, self.order_size_usd) {
            (Some(nf), Some(sz)) if nf.is_finite() && sz.is_finite() => Some(nf * sz / 10_000.0),
            _ => None,
        }
    }
    /// Порог `GREEN_NET_BPS` (H6) в долларах на тот же размер ордера —
    /// справочная величина рядом с `net_fill_usd` для сравнения на глаз.
    pub fn green_threshold_usd(&self) -> Option<f64> {
        self.order_size_usd
            .filter(|sz| sz.is_finite())
            .map(|sz| GREEN_NET_BPS * sz / 10_000.0)
    }
    /// Строка таблицы отчёта.
    pub fn format_line(&self) -> String {
        format!(
            "{}: n={} G={} net_fill={} lower={} status={} net_fill_usd={} green_threshold_usd={}",
            self.id,
            self.n,
            self.g,
            fmt_opt(self.net_fill),
            fmt_opt(self.net_fill_lower),
            self.status,
            fmt_opt(self.net_fill_usd()),
            fmt_opt(self.green_threshold_usd()),
        )
    }
}

fn fmt_opt(v: Option<f64>) -> String {
    match v {
        Some(x) => format!("{x:.4}"),
        None => "none".to_string(),
    }
}

/// Печать числа, отсутствие которого обязано быть объяснено (таск 29):
/// число — как везде, названная причина — `n/a (<причина>)`, и только
/// безымянное отсутствие — прежний `none`. Три разных исхода, а не два:
/// `none` у PBO/CPCV сегодня означал бы «не измеряли», и именно эту
/// неотличимость таск снимает.
fn fmt_measured(v: Option<f64>, na: Option<&str>) -> String {
    match (v, na) {
        (Some(x), _) => format!("{x:.4}"),
        (None, Some(reason)) => format!("n/a ({reason})"),
        (None, None) => "none".to_string(),
    }
}

/// Прогоняет подтверждающую только по шорт-листу. Первым делом требует
/// заморозку (`None` — отказ); затем проверяет, что каждый поданный профиль
/// входит в список (чужой — отказ); затем печатает строку на каждый профиль
/// списка, включая отсутствующие в подаче (они идут с нулями как
/// `insufficient`, а не исчезают). `total_trials` — фактическое число
/// испытаний из `runs.csv` (R47): тот же `N`, которым `decide_profile`
/// двигает порог для каждой строки.
pub fn confirmatory_table(
    frozen: Option<&FrozenShortlist>,
    conf: &[ConfProfile],
    total_trials: usize,
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
                status: decide_profile(p.n, p.g, p.net_fill_lower, p.observed_sharpe, total_trials),
                order_size_usd: p.order_size_usd,
            }),
            None => rows.push(ConfirmRow {
                id: id.clone(),
                n: 0,
                g: 0,
                net_fill: None,
                net_fill_lower: None,
                status: ConfirmStatus::InsufficientData,
                order_size_usd: None,
            }),
        }
    }
    Ok(rows)
}

/// Вердикт шага 5.4 по подтверждающей (спека «Решение», R51–R53): три
/// предрегистрированных исхода. Красный расщеплён на два по R68/РВ-М —
/// «красный по нехватке мощности печатается иначе, чем красный про рынок»:
/// `RedInsufficientPower` — ни одна строка не набрала `n`/`G` даже на то,
/// чтобы её измерить; `RedNoEdge` — измерение состоялось (хотя бы одна строка
/// прошла `n`/`G`), но эджа нет. Не различать их значило бы одинаково
/// печатать «данных не хватило» и «идея проверена и не работает» — а это
/// разные результаты для владельца (ticket 13, критерий приёмки).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ShortlistVerdict {
    /// Ни одна строка не набрала `n >= 100` и `G >= G_MIN`: измерения не было.
    RedInsufficientPower,
    /// Измерение состоялось, но ни один профиль не подтверждён.
    RedNoEdge,
    /// Эдж есть, работу не окупает.
    GreenThin,
    /// Прошедший профиль с `net_fill >= H6`.
    Green,
}

impl ShortlistVerdict {
    /// `true` — красный (оба варианта): используется, где различие причины
    /// не нужно (например, счётчик прохода в CLI).
    pub fn is_red(self) -> bool {
        matches!(
            self,
            ShortlistVerdict::RedInsufficientPower | ShortlistVerdict::RedNoEdge
        )
    }
}

impl std::fmt::Display for ShortlistVerdict {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ShortlistVerdict::RedInsufficientPower => {
                write!(
                    f,
                    "RED (insufficient power): n/G/N ниже порога — измерения не было"
                )
            }
            ShortlistVerdict::RedNoEdge => {
                write!(f, "RED (market): измерено, ни один профиль не подтверждён")
            }
            ShortlistVerdict::GreenThin => write!(f, "GREEN-thin: эдж есть, ёмкости нет"),
            ShortlistVerdict::Green => write!(f, "GREEN: net_fill>=H6"),
        }
    }
}

/// Лучший `net_fill` среди подтверждённых строк — то же число, которое несёт
/// вердикт (`decide_verdict`) и шапка отчёта (`VerdictHeader::value_bps`);
/// вынесено отдельно, чтобы вызывающий CLI не пересчитывал вердикт заново
/// ради одного числа.
pub fn best_confirmed_net_fill(rows: &[ConfirmRow]) -> Option<f64> {
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
    best
}

/// Выносит вердикт только по подтверждающим строкам. Разведочные числа сюда
/// не входят даже аргументами. Различение красного (R68) смотрит на статусы
/// строк: если хотя бы одна дошла до измерения (`Unconfirmed` или
/// `Confirmed` — обе значат «n/G набраны, DSR-проверка состоялась»), красный
/// — про рынок; если все строки `InsufficientData` (включая пустую таблицу),
/// красный — про нехватку мощности.
pub fn decide_verdict(rows: &[ConfirmRow]) -> ShortlistVerdict {
    let best = best_confirmed_net_fill(rows);
    let any_measured = rows
        .iter()
        .any(|r| r.status != ConfirmStatus::InsufficientData);
    match best {
        Some(v) if v >= GREEN_NET_BPS => ShortlistVerdict::Green,
        Some(_) => ShortlistVerdict::GreenThin,
        None if any_measured => ShortlistVerdict::RedNoEdge,
        None => ShortlistVerdict::RedInsufficientPower,
    }
}

// ---------------------------------------------------------------------------
// Журнал испытаний: каждый посчитанный профиль — строка runs.csv.
// ---------------------------------------------------------------------------

/// Пишет в журнал по строке на каждый посчитанный профиль (все пригодные, не
/// только шорт-лист). Вид строки — подтверждающий прогон: каждая строка
/// входит в число испытаний DSR. Метку времени строкой передаёт вызывающий.
/// Писатель — общий с профилями касаний (`runs::log_trials`, префикс
/// `PROFILE_TRIAL_PREFIX`).
pub fn log_profile_trials(path: &Path, ts_utc: &str, ids: &[String]) -> Result<(), ShortlistError> {
    log_trials(path, ts_utc, PROFILE_TRIAL_PREFIX, ids).map_err(|e| match e {
        crate::lob::runs::RunsError::Io(s) => ShortlistError::Io(s),
        crate::lob::runs::RunsError::Csv(s) => ShortlistError::Csv(s),
    })
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
// Выход shortlist-<дата>.md.
// ---------------------------------------------------------------------------
//
// Черновая `profiles-<дата>.csv` (`ProfileRow`, 15 колонок) сняты таском 17:
// боевую таблицу профилей пишет `commands::lob::profiles` (таск 10, 28
// колонок, `observed_sharpe` из таска 16) — этот черновик её не читал и не
// писал ни разу, только держал место до появления настоящей реализации.

/// Гейт вердикта в шапке шорт-листа (`PLAN.md` раздел 6): гейт G3-в на
/// подтверждающей. Числа для DSR/PBO/CPCV/`G`/`p`/джекнайфа собирает
/// вызывающий (`commands::lob::shortlist`) — этот модуль их не измеряет,
/// только печатает, включая честный `none`, когда измерения не было (тот же
/// принцип, что `not_measured` у `fill`/`net_fill`).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct VerdictHeader {
    /// `net_fill` профиля, который выносит вердикт (лучший подтверждённый,
    /// либо лучший измеренный, если подтверждённых нет) — `None`, если
    /// измерений не было вовсе.
    pub value_bps: Option<f64>,
    /// Deflated Sharpe Ratio (R47, `final_metrics::dsr`/`dsr_for_trial_count`).
    pub dsr: Option<f64>,
    /// Probability of Backtest Overfitting процедуры отбора.
    pub pbo: Option<f64>,
    /// Средний OOS Sharpe CPCV процедуры отбора.
    pub cpcv_oos_sharpe: Option<f64>,
    /// Почему `pbo` — не число (таск 29): `days=4 < 8`, `trials=1 < 2`.
    /// Заполнен — шапка печатает `pbo=n/a (<причина>)` вместо `none`:
    /// отсутствие числа обязано быть объяснено, а не молчаливо.
    pub pbo_na: Option<String>,
    /// Почему `cpcv_oos_sharpe` — не число (таск 29), в том же формате.
    pub cpcv_na: Option<String>,
    /// Правило отбора, которое проверила CPCV (§6.4 п. 4: проверяется
    /// процедура, а не профиль) — печатается рядом с числом: `(selection:
    /// …)`. Без числа не печатается: называть правило нечему.
    pub cpcv_selection: Option<String>,
    /// Готовая строка `pbo_matrix: …` — форма матрицы «испытания × периоды»
    /// и правило пустой ячейки; форматирует вызывающий
    /// (`commands::lob::shortlist`), как и `window`. Пусто — матрицу не
    /// строили, строки в шапке нет.
    pub pbo_matrix: String,
    /// Фактическое число годных суток, которым вынесен вердикт.
    pub g: Option<u64>,
    /// Достижимое разрешение сетки Уэбба `p` при этом `G`
    /// (`stats::webb_p_grid_resolution`).
    pub p_grid_resolution: Option<f64>,
    /// Джекнайф-по-суткам чувствительность (A03) — `None`, если суток для
    /// исключения меньше двух или измерения не было.
    pub jackknife: Option<crate::lob::final_metrics::JackknifeSensitivity>,
    /// Строка `window: …` (ticket 21, R57) — готовая, форматирует
    /// вызывающий (`commands::lob::profiles::format_window_line`, общий
    /// формат с `profiles-<дата>.csv`): `window: debug (all sessions)` в
    /// отладке, иначе границы, длина в сутках и пара разведочная/
    /// подтверждающая, из которой окно выведено.
    pub window: String,
}

/// Пишет шорт-лист: профили с подтверждающими числами, фактическое число
/// испытаний и поправка, с которой взят порог, плюс вердикт подтверждающей
/// в трёх предрегистрированных исходах (R50–R54, R68) с гейтом, значением,
/// порогом, `N`, DSR/PBO/CPCV, `G`, `p` и джекнайфом (A03) — отдельного
/// файла-отчёта нет (§5 задачи), всё это — шапка данного файла.
#[allow(clippy::too_many_arguments)]
pub fn write_shortlist_md(
    path: &Path,
    date: &str,
    frozen: &FrozenShortlist,
    rows: &[ConfirmRow],
    verdict: ShortlistVerdict,
    header: &VerdictHeader,
) -> Result<(), ShortlistError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut text = String::new();
    text.push_str(&format!("# Shortlist {date}\n"));
    text.push_str(&format!(
        "outcome: {verdict} gate=G3-в value_bps={} green_threshold_bps={GREEN_NET_BPS}\n",
        fmt_opt(header.value_bps),
    ));
    text.push_str(&format!("{}\n", header.window));
    text.push_str(&format!("trials: {}\n", frozen.trials()));
    text.push_str(&format!("freeze_commit: {}\n", frozen.commit()));
    text.push_str(&format!("fingerprint: {}\n", frozen.fingerprint_hex()));
    text.push_str(&format!(
        "threshold: n>={CONFIRM_MIN_N} G>={G_MIN} net_fill_lower>0 (DSR by actual trials)\n"
    ));
    text.push_str(&format!(
        "dsr_target: {DSR_TARGET} dsr={} pbo={} cpcv_oos_sharpe={}{}\n",
        fmt_opt(header.dsr),
        fmt_measured(header.pbo, header.pbo_na.as_deref()),
        fmt_measured(header.cpcv_oos_sharpe, header.cpcv_na.as_deref()),
        match (&header.cpcv_selection, header.cpcv_oos_sharpe) {
            (Some(rule), Some(_)) => format!(" (selection: {rule})"),
            _ => String::new(),
        },
    ));
    // Порог PBO: искали в задаче (§6.4), плане и `SETTLED.md` — не назначен
    // ни один. Число печатается, вердикт по нему не выносится: назначить
    // порог самому значило бы изобрести его (таск 29).
    text.push_str(
        "pbo_gate: none — порог PBO задачей и планом не назначен: \
         число печатается, вердикт по нему не выносится\n",
    );
    if !header.pbo_matrix.is_empty() {
        text.push_str(&format!("{}\n", header.pbo_matrix));
    }
    text.push_str(&format!(
        "G: {} p_grid_resolution: {}\n",
        header
            .g
            .map_or_else(|| "none".to_string(), |g| g.to_string()),
        fmt_opt(header.p_grid_resolution),
    ));
    match &header.jackknife {
        Some(j) => text.push_str(&format!(
            "jackknife_by_day: min={:.4} max={:.4} range={:.4} n={}\n",
            j.min,
            j.max,
            j.range,
            j.leave_one_out.len()
        )),
        None => text.push_str("jackknife_by_day: none\n"),
    }
    // Долларовые колонки — справочные (R54), в гейт не входят: вердикт и
    // `status` решены только по `net_fill`/`net_fill_lower` в bps выше.
    text.push_str(
        "| profile_id | n_conf | g_conf | net_fill | lower | status | net_fill_usd | green_threshold_usd |\n",
    );
    for r in rows {
        text.push_str(&format!(
            "| {} | {} | {} | {} | {} | {} | {} | {} |\n",
            r.id,
            r.n,
            r.g,
            fmt_opt(r.net_fill),
            fmt_opt(r.net_fill_lower),
            r.status,
            fmt_opt(r.net_fill_usd()),
            fmt_opt(r.green_threshold_usd()),
        ));
    }
    text.push_str(&format!("verdict: {verdict}\n"));
    let mut f = File::create(path)?;
    f.write_all(text.as_bytes())?;
    f.flush()?;
    Ok(())
}

#[cfg(test)]
mod tests;
