//! Открытая запись и правило остановки (план, §4.1, Decision 21).
//!
//! Ежесуточный счётчик, который знает только размер выборки: считает `n` и `G`
//! для ячейки C2 и выставляет `ready.flag`, когда `n(C2) >= 100` и `G(C2) >= 12`.
//! Попутно печатает те же две величины для C1 в `progress.csv` справочно.
//!
//! Три запрета, каждый из которых проверяется тестом, а не обещанием:
//!
//! - Модуль не вычисляет markout ни в каком виде: видит только счётчики
//!   (`LevelRecord` читается лишь предикатами принадлежности к C1/C2).
//! - Предикат годности суток один на весь документ: его читают и `watch`
//!   (шаг 4.1), и G2 (шаг 5.2). Двух реализаций быть не должно — см.
//!   `day_eligible`.
//! - Момент анализа выбирается только размером выборки (Decision 21):
//!   подтверждающая выборка — ровно сутки из `ready.flag`; запись после флага
//!   продолжается, но эти сутки в анализ не входят и остаются отложенными.
//!
//! Правила состава выборки:
//!
//! - H8: разрыв записи больше 6 часов или сутки, не прошедшие verify,
//!   выбрасываются целиком и кластером не считаются.
//! - H10: подтверждающая запись идёт по одному символу; чужой символ
//!   отвергается ошибкой, а не смешивается в одни сутки.
//! - C2 есть строгое подмножество C1, поэтому из условий C2 следуют условия C1:
//!   годные сутки с наблюдением C2 суть годные сутки с наблюдением C1.
//!
//! Поток вызовов за сутки: `tally_day` собирает `DayTally` из записей,
//! `WatchState::push_tally` копит, `write_progress_csv` печатает строку на сутки,
//! `write_ready_flag_if_due` выставляет флаг ровно один раз. Метку времени
//! строкой передаёт вызывающий: часы здесь не читаются, и это свойство
//! воспроизводимости, а не стиль, — иначе реплей и живой прогон дали бы
//! разные флаги на тех же данных.

use std::collections::BTreeMap;
use std::fs::{File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

use crate::book::Side;
use crate::lob::levels::{LevelRecord, Outcome};

// ---------------------------------------------------------------------------
// Константы Decision 16/21 и done 4.1. Каждое число — из плана.
// ---------------------------------------------------------------------------

/// Триггер Decision 21: ячейка C2 набрала не меньше сотни наблюдений.
pub const TRIGGER_N_C2: u64 = 100;

/// Триггер Decision 21: не меньше двенадцати суток G(C2).
pub const TRIGGER_G: u64 = 12;

/// Порог теста 1 из done 4.1: доля расхождений строго меньше 0.01%,
/// то есть числитель/знаменатель `< 1/10000`. Граница строгая: ровно 0.01%
/// уже не годно.
pub const VERIFY_TEST1_MAX_NUM: u64 = 1;
pub const VERIFY_TEST1_MAX_DEN: u64 = 10_000;

/// Порог done 4.1 для доли суток с разрывом: строго меньше 1%.
/// Считается в миллионных долях целыми, без дробных чисел:
/// 1% — это 10 000 ppm.
pub const GAP_SHARE_MAX_PPM: u64 = 10_000;

/// C2 требует повторяемости на цене не меньше двух за скользящий час
/// (Decision 16).
pub const C2_MIN_REPEAT: u32 = 2;

// ---------------------------------------------------------------------------
// Ошибки. Один тип на весь модуль, в стиле `record.rs`: отказ возвращается
// с причиной, паники нет.
// ---------------------------------------------------------------------------

/// Отказ счётчика. Ни один вариант не паникует: процесс открытой записи
/// рассчитан на недели без присмотра.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WatchError {
    /// Файл не открылся / не записался / не прочитался.
    Io(String),
    /// `progress.csv` не разобрался как CSV.
    Csv(String),
    /// Чужой символ при состоянии на один символ (H10).
    SymbolMismatch { expected: String, got: String },
    /// Те же сутки поданы дважды: строка на сутки обязана быть одна.
    DuplicateDay { day: String },
    /// Нарушен инвариант C2⊂C1: наблюдений C2 больше, чем C1.
    SubsetViolation { day: String, n_c1: u64, n_c2: u64 },
    /// Строка суток — не `YYYY-MM-DD`.
    BadDay { day: String },
    /// `ready.flag` отсутствует: будущий `markout --confirmatory`
    /// читает это как запрет старта, а не как пустую выборку.
    MissingFlag { path: String },
    /// `ready.flag` есть, но не разбирается как флаг этого модуля.
    BadFlag { reason: String },
}

impl std::fmt::Display for WatchError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            WatchError::Io(e) => write!(f, "ввод-вывод: {e}"),
            WatchError::Csv(e) => write!(f, "CSV: {e}"),
            WatchError::SymbolMismatch { expected, got } => {
                write!(f, "чужой символ: состояние на {expected}, подали {got}")
            }
            WatchError::DuplicateDay { day } => write!(f, "сутки {day} уже учтены"),
            WatchError::SubsetViolation { day, n_c1, n_c2 } => {
                write!(f, "сутки {day}: n_c2={n_c2} больше n_c1={n_c1}")
            }
            WatchError::BadDay { day } => {
                write!(f, "сутки не разобрались как YYYY-MM-DD: {day}")
            }
            WatchError::MissingFlag { path } => write!(f, "нет ready.flag: {path}"),
            WatchError::BadFlag { reason } => write!(f, "ready.flag не разобрался: {reason}"),
        }
    }
}

impl std::error::Error for WatchError {}

impl From<io::Error> for WatchError {
    fn from(e: io::Error) -> Self {
        WatchError::Io(e.to_string())
    }
}

impl From<csv::Error> for WatchError {
    fn from(e: csv::Error) -> Self {
        WatchError::Csv(e.to_string())
    }
}

// ---------------------------------------------------------------------------
// C1/C2 как предикаты над LevelRecord. Markout не считается.
// ---------------------------------------------------------------------------

/// Принадлежность к C1 (Decision 16): исход `pulled` на стороне бида.
///
/// Дистанция 1–5 тиков и горизонт 10 с — свойства входного потока, а не этого
/// предиката: разметка подаёт сюда только уровни с измеренным горизонтом 10 с
/// на дистанции 1–5 тиков, а модуль лишь делит их на ячейки.
/// Ноль аллокаций: два сравнения целых.
pub fn is_c1(rec: &LevelRecord) -> bool {
    rec.outcome() == Outcome::Pulled && rec.side == Side::Bid
}

/// Принадлежность к C2 (Decision 16): C1 плюс время жизни строго ниже медианы
/// и повторяемость на цене не меньше двух за скользящий час.
///
/// Медиана `lifetime_ms` считается внутри популяции C1 по первым суткам UTC
/// подтверждающей выборки и дальше не пересчитывается — то же правило, что для
/// терцилей слоёв в 5.2. Сюда медиана приходит готовым числом: счётчик её
/// только читает. По построению C2 влечёт C1, и это свойство зафиксировано
/// тестом на общей фикстуре с шагом 5.2.
pub fn is_c2(rec: &LevelRecord, median_lifetime_ms: i64) -> bool {
    is_c1(rec) && rec.lifetime_ms < median_lifetime_ms && rec.repeat_count >= C2_MIN_REPEAT
}

// ---------------------------------------------------------------------------
// DayTally и общий предикат годности суток.
// ---------------------------------------------------------------------------

/// Счётчики одних суток UTC — единственное, что `watch` знает о сутках.
/// Единица наблюдений — `LevelRecord` из `levels.rs`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DayTally {
    /// Символ подтверждающей записи (H10: один на всё состояние).
    pub symbol: String,
    /// Сутки UTC как `YYYY-MM-DD`.
    pub day_utc: String,
    /// Наблюдений C1 за сутки.
    pub n_c1: u64,
    /// Наблюдений C2 за сутки. Инвариант C2⊂C1: `n_c2 <= n_c1`.
    pub n_c2: u64,
    /// Разрыв записи больше 6 часов внутри суток (H8).
    pub has_gap_over_6h: bool,
    /// Расхождения теста 1 из `verify.csv` за сутки (числитель доли).
    pub verify_test1_violations: u64,
    /// Число проверок теста 1 за сутки (знаменатель доли).
    pub verify_basis_points: u64,
}

/// Общий предикат годности суток на весь документ (Decision 21, H8).
///
/// Единственная реализация: её читают и `watch` (шаг 4.1), и G2 (шаг 5.2).
/// Шаг 5.2 обязан переиспользовать эту функцию, а не писать свою.
/// Годность — только качество суток, без требования наблюдений: `G`
/// Decision 21 считает годные сутки, содержащие наблюдение ячейки,
/// и это пересечение собирается вызывающим (`WatchState::g_c2`),
/// а не здесь, — иначе флаг считал бы годные сутки без наблюдений
/// (дефект, закрытый в Decision 21).
///
/// Условия: нет разрыва больше 6 часов (H8) и доля расхождений теста 1
/// строго меньше 0.01% (done 4.1). Ноль проверок — не годно: доказательств
/// чистоты нет, и отсутствие данных не есть чистые данные.
pub fn day_eligible(t: &DayTally) -> bool {
    if t.has_gap_over_6h {
        return false;
    }
    if t.verify_basis_points == 0 {
        return false;
    }
    (t.verify_test1_violations as u128) * (VERIFY_TEST1_MAX_DEN as u128)
        < (t.verify_basis_points as u128) * (VERIFY_TEST1_MAX_NUM as u128)
}

/// Собирает `DayTally` из записей суток: считает C1/C2 предикатами выше.
/// Markout не читается и не считается: счётчик видит только принадлежность.
#[allow(clippy::too_many_arguments)]
pub fn tally_day(
    symbol: &str,
    day_utc: &str,
    records: &[LevelRecord],
    median_lifetime_ms: i64,
    has_gap_over_6h: bool,
    verify_test1_violations: u64,
    verify_basis_points: u64,
) -> DayTally {
    let mut n_c1: u64 = 0;
    let mut n_c2: u64 = 0;
    for rec in records {
        if is_c1(rec) {
            n_c1 = n_c1.saturating_add(1);
            if rec.lifetime_ms < median_lifetime_ms && rec.repeat_count >= C2_MIN_REPEAT {
                n_c2 = n_c2.saturating_add(1);
            }
        }
    }
    DayTally {
        symbol: symbol.to_string(),
        day_utc: day_utc.to_string(),
        n_c1,
        n_c2,
        has_gap_over_6h,
        verify_test1_violations,
        verify_basis_points,
    }
}

// ---------------------------------------------------------------------------
// progress.csv: строка на сутки, стиль строк — как `gaps.csv` в record.rs.
// ---------------------------------------------------------------------------

/// Одна строка `progress.csv`: счётчики суток плюс вычисленная годность.
/// Человек читает файл целиком, поэтому годность материализована колонкой,
/// а не восстанавливается взглядом по двум соседним.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ProgressRow {
    pub day_utc: String,
    pub symbol: String,
    pub n_c1: u64,
    pub n_c2: u64,
    pub eligible: bool,
    pub has_gap_over_6h: bool,
    pub verify_test1_violations: u64,
    pub verify_basis_points: u64,
}

/// Шапка `progress.csv` — те же имена и в том же порядке, что поля
/// `ProgressRow`. Пишется вручную тем же приёмом, что `GAPS_HEADER`:
/// файл с нулём строк обязан шапку уже нести. Дрейф имён ловит тест
/// кругового прохода ниже.
const PROGRESS_HEADER: [&str; 8] = [
    "day_utc",
    "symbol",
    "n_c1",
    "n_c2",
    "eligible",
    "has_gap_over_6h",
    "verify_test1_violations",
    "verify_basis_points",
];

/// `progress.csv` — один на корень, на все сутки: десятки строк, читает
/// человек (Decision 23: CSV только для метаданных-обочин).
pub fn progress_csv_path(root: &Path) -> PathBuf {
    root.join("progress.csv")
}

/// `ready.flag` — один на корень, рядом с `progress.csv`.
pub fn ready_flag_path(root: &Path) -> PathBuf {
    root.join("ready.flag")
}

/// Перезаписывает `progress.csv` целиком из строк состояния: строка на сутки,
/// порядок — по возрастанию суток. Перезапись, а не дописывание, держит
/// инвариант «одна строка на сутки» без отдельного дедупликатора.
pub fn write_progress_csv(path: &Path, rows: &[ProgressRow]) -> Result<(), WatchError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let file = File::create(path)?;
    let mut w = csv::WriterBuilder::new()
        .has_headers(false)
        .from_writer(file);
    w.write_record(PROGRESS_HEADER)?;
    for row in rows {
        w.serialize(row)?;
    }
    w.flush()?;
    Ok(())
}

/// Читает все строки. Отсутствующий или пустой файл — это ноль строк,
/// а не ошибка разбора: отсутствие данных не есть повреждённые данные.
/// Тот же приём, что `read_gap_rows` в `record.rs`.
pub fn read_progress_rows(path: &Path) -> Result<Vec<ProgressRow>, WatchError> {
    if std::fs::metadata(path)
        .map(|m| m.len() == 0)
        .unwrap_or(true)
    {
        return Ok(Vec::new());
    }
    let mut r = csv::Reader::from_path(path)?;
    r.deserialize::<ProgressRow>()
        .collect::<Result<Vec<_>, _>>()
        .map_err(WatchError::from)
}

// ---------------------------------------------------------------------------
// ready.flag: n, G, время, список суток выборки.
// ---------------------------------------------------------------------------

/// Замороженный снимок момента анализа: выборка — ровно эти сутки
/// (Decision 21). Числа дальше не пересчитываются: сутки после флага
/// остаются отложенной выборкой и сюда не входят.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReadyFlag {
    /// Символ подтверждающей записи (H10).
    pub symbol: String,
    /// Сумма `n_c2` по годным суткам выборки. Не меньше `TRIGGER_N_C2`.
    pub n_c2: u64,
    /// Число годных суток с наблюдением C2. Не меньше `TRIGGER_G`.
    pub g: u64,
    /// Момент выставления флага строкой UTC. Строкой, а не часами этого
    /// модуля: время передаёт вызывающий.
    pub ready_at_utc: String,
    /// Сутки выборки по возрастанию.
    pub days: Vec<String>,
}

fn flag_text(flag: &ReadyFlag) -> String {
    format!(
        "symbol={}\nn_c2={}\ng={}\nready_at_utc={}\ndays={}\n",
        flag.symbol,
        flag.n_c2,
        flag.g,
        flag.ready_at_utc,
        flag.days.join(",")
    )
}

fn bad_flag(reason: String) -> WatchError {
    WatchError::BadFlag { reason }
}

/// Строка суток обязана быть `YYYY-MM-DD`: десять знаков, дефисы на местах,
/// остальное — цифры. Календарная проверка (тридцать февралей) не нужна:
/// сутки приходят из ротации по UTC, а не из рук пользователя.
fn day_format_ok(day: &str) -> bool {
    let b = day.as_bytes();
    b.len() == 10
        && b.get(4) == Some(&b'-')
        && b.get(7) == Some(&b'-')
        && b.get(..4).is_some_and(|s| s.iter().all(u8::is_ascii_digit))
        && b.get(5..7)
            .is_some_and(|s| s.iter().all(u8::is_ascii_digit))
        && b.get(8..10)
            .is_some_and(|s| s.iter().all(u8::is_ascii_digit))
}

fn kv(line: &str) -> Result<(&str, &str), WatchError> {
    line.split_once('=')
        .ok_or_else(|| bad_flag(format!("строка без знака равенства: {line}")))
}

fn parse_ready_flag(text: &str) -> Result<ReadyFlag, WatchError> {
    let lines: Vec<&str> = text.lines().collect();
    // Пять строк ровно: паттерн разбирает и проверяет длину одним движением,
    // дальше — именованные переменные вместо индексов.
    let [l0, l1, l2, l3, l4] = match lines.as_slice() {
        [a, b, c, d, e] => [*a, *b, *c, *d, *e],
        _ => return Err(bad_flag(format!("жду 5 строк, вижу {}", lines.len()))),
    };
    let (k0, symbol) = kv(l0)?;
    let (k1, n_c2_s) = kv(l1)?;
    let (k2, g_s) = kv(l2)?;
    let (k3, ready_at) = kv(l3)?;
    let (k4, days_s) = kv(l4)?;
    if (k0, k1, k2, k3, k4) != ("symbol", "n_c2", "g", "ready_at_utc", "days") {
        return Err(bad_flag(
            "ключи обязаны идти порядком symbol,n_c2,g,ready_at_utc,days".to_string(),
        ));
    }
    if symbol.is_empty() {
        return Err(bad_flag("пустой symbol".to_string()));
    }
    let n_c2: u64 = n_c2_s
        .parse()
        .map_err(|_| bad_flag(format!("n_c2 не число: {n_c2_s}")))?;
    let g: u64 = g_s
        .parse()
        .map_err(|_| bad_flag(format!("g не число: {g_s}")))?;
    if ready_at.is_empty() {
        return Err(bad_flag("пустая метка времени".to_string()));
    }
    let days: Vec<String> = days_s.split(',').map(str::to_string).collect();
    if days.iter().any(|d| !day_format_ok(d)) {
        return Err(bad_flag("список суток содержит не YYYY-MM-DD".to_string()));
    }
    if days.windows(2).any(|w| matches!(w, [a, b] if a >= b)) {
        return Err(bad_flag(
            "сутки обязаны идти строго по возрастанию".to_string(),
        ));
    }
    Ok(ReadyFlag {
        symbol: symbol.to_string(),
        n_c2,
        g,
        ready_at_utc: ready_at.to_string(),
        days,
    })
}

/// Пишет флаг созданием файла в режиме «только если его нет»: повторная запись
/// невозможна на уровне вызова ОС, а не договорённости. Существующий файл —
/// ошибка вызывающего, молча перезаписывать его запрещено.
pub fn write_ready_flag(path: &Path, flag: &ReadyFlag) -> Result<(), WatchError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut f = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| {
            if e.kind() == io::ErrorKind::AlreadyExists {
                bad_flag(format!("файл уже выставлен: {}", path.display()))
            } else {
                WatchError::Io(e.to_string())
            }
        })?;
    f.write_all(flag_text(flag).as_bytes())?;
    f.flush()?;
    Ok(())
}

/// Заглушка для будущего `markout --confirmatory`: без флага — `Err`
/// (вызывающий завершается ненулевым кодом), с флагом — сам флаг.
/// Сам markout пишется позже; здесь только механическая защита от
/// optional stopping, а не вычисление.
pub fn require_ready_flag(path: &Path) -> Result<ReadyFlag, WatchError> {
    let text = std::fs::read_to_string(path).map_err(|e| {
        if e.kind() == io::ErrorKind::NotFound {
            WatchError::MissingFlag {
                path: path.display().to_string(),
            }
        } else {
            WatchError::Io(e.to_string())
        }
    })?;
    parse_ready_flag(&text)
}

// ---------------------------------------------------------------------------
// Доля суток с разрывом: gaps < 1% по годным суткам считается здесь.
// ---------------------------------------------------------------------------

/// Доля суток с флагом разрыва больше 6 часов среди всех учтённых суток,
/// в миллионных долях целыми: 1% — это 10 000 ppm. `None` — суток нет.
/// Сутки с разрывом годными не являются (H8), поэтому эта доля — верхняя
/// оценка потерь записи; порог done 4.1 — строго меньше 1%.
/// Разрывы короче 6 часов внутри годных суток эта доля не покрывает:
/// их суммарное время читается по `gaps.csv` напрямую.
pub fn gap_day_share_ppm(tallies: &[DayTally]) -> Option<u64> {
    if tallies.is_empty() {
        return None;
    }
    let gap = tallies.iter().filter(|t| t.has_gap_over_6h).count() as u128;
    let total = tallies.len() as u128;
    // Итоговый каст точен: частное — доля в миллионных (≤ 1e6).
    #[allow(clippy::cast_possible_truncation)]
    let ppm = (gap * 1_000_000 / total) as u64;
    Some(ppm)
}

/// Укладывается ли доля в бюджет done 4.1: строго меньше `GAP_SHARE_MAX_PPM`.
pub fn gap_share_within_budget(share_ppm: u64) -> bool {
    share_ppm < GAP_SHARE_MAX_PPM
}

// ---------------------------------------------------------------------------
// WatchState: копит tally по суткам, печатает прогресс, выставляет флаг раз.
// ---------------------------------------------------------------------------

/// Исход суточного шага: прогресс переписан всегда, флаг — только в момент
/// первого срабатывания триггера.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ObserveOutcome {
    /// Сколько строк теперь несёт `progress.csv`.
    pub progress_rows: usize,
    /// Флаг, выставленный этим вызовом, если триггер сработал впервые.
    pub flag: Option<ReadyFlag>,
}

/// Состояние открытой записи на один символ (H10). Суммы и `G` считаются
/// только по годным суткам: мусор в зачёт не идёт, а отодвигает флаг.
#[derive(Debug)]
pub struct WatchState {
    symbol: String,
    median_lifetime_ms: i64,
    tallies: BTreeMap<String, DayTally>,
    flag_written: bool,
}

impl WatchState {
    /// Новое состояние на символ. Медиана времени жизни приходит готовой
    /// (Decision 16: считается по первым суткам и дальше не пересчитывается) —
    /// счётчик её только читает.
    pub fn new(symbol: &str, median_lifetime_ms: i64) -> Self {
        Self {
            symbol: symbol.to_string(),
            median_lifetime_ms,
            tallies: BTreeMap::new(),
            flag_written: false,
        }
    }

    /// Символ подтверждающей записи.
    pub fn symbol(&self) -> &str {
        &self.symbol
    }

    /// Медиана, которой считается C2. Нужна вызывающему для печати состава.
    pub fn median_lifetime_ms(&self) -> i64 {
        self.median_lifetime_ms
    }

    /// Сколько суток учтено, включая выброшенные.
    pub fn len(&self) -> usize {
        self.tallies.len()
    }

    /// Суток пока нет.
    pub fn is_empty(&self) -> bool {
        self.tallies.is_empty()
    }

    /// Годных суток талонов: только качество, без требования наблюдений.
    fn eligible(&self) -> impl Iterator<Item = &DayTally> {
        self.tallies.values().filter(|t| day_eligible(t))
    }

    /// Сумма `n_c2` по годным суткам — то `n`, что попадёт во флаг.
    pub fn n_c2_total(&self) -> u64 {
        self.eligible().map(|t| t.n_c2).fold(0, u64::saturating_add)
    }

    /// Сумма `n_c1` по годным суткам — справочно для `progress.csv`.
    pub fn n_c1_total(&self) -> u64 {
        self.eligible().map(|t| t.n_c1).fold(0, u64::saturating_add)
    }

    /// `G(C2)`: годные сутки с наблюдением C2. Определение `G` одно на весь
    /// документ: так же его читает G2 в 5.2.
    pub fn g_c2(&self) -> u64 {
        self.eligible().filter(|t| t.n_c2 >= 1).count() as u64
    }

    /// `G(C1)` справочно: из C2⊂C1 следует `G(C1) >= G(C2)`, и триггер по C2
    /// выполняет условия выборки для обеих ячеек сам.
    pub fn g_c1(&self) -> u64 {
        self.eligible().filter(|t| t.n_c1 >= 1).count() as u64
    }

    /// Сутки выборки по возрастанию: годные сутки с наблюдением C2.
    /// Подтверждающая выборка — ровно этот список (Decision 21).
    pub fn sample_days(&self) -> Vec<String> {
        self.eligible()
            .filter(|t| t.n_c2 >= 1)
            .map(|t| t.day_utc.clone())
            .collect()
    }

    /// Триггер Decision 21: `n(C2) >= 100` и `G(C2) >= 12`, и флаг ещё не стоял.
    pub fn is_due(&self) -> bool {
        !self.flag_written && self.n_c2_total() >= TRIGGER_N_C2 && self.g_c2() >= TRIGGER_G
    }

    /// Принимает сутки: один символ (H10), формат суток, одна строка на сутки,
    /// инвариант C2⊂C1. Нарушение любого — ошибка, молчаливого пути нет.
    pub fn push_tally(&mut self, tally: DayTally) -> Result<(), WatchError> {
        if tally.symbol != self.symbol {
            return Err(WatchError::SymbolMismatch {
                expected: self.symbol.clone(),
                got: tally.symbol.clone(),
            });
        }
        if !day_format_ok(&tally.day_utc) {
            return Err(WatchError::BadDay {
                day: tally.day_utc.clone(),
            });
        }
        if self.tallies.contains_key(&tally.day_utc) {
            return Err(WatchError::DuplicateDay {
                day: tally.day_utc.clone(),
            });
        }
        if tally.n_c2 > tally.n_c1 {
            return Err(WatchError::SubsetViolation {
                day: tally.day_utc.clone(),
                n_c1: tally.n_c1,
                n_c2: tally.n_c2,
            });
        }
        self.tallies.insert(tally.day_utc.clone(), tally);
        Ok(())
    }

    /// Строки `progress.csv`: по одной на сутки, по возрастанию суток.
    pub fn progress_rows(&self) -> Vec<ProgressRow> {
        self.tallies
            .values()
            .map(|t| ProgressRow {
                day_utc: t.day_utc.clone(),
                symbol: t.symbol.clone(),
                n_c1: t.n_c1,
                n_c2: t.n_c2,
                eligible: day_eligible(t),
                has_gap_over_6h: t.has_gap_over_6h,
                verify_test1_violations: t.verify_test1_violations,
                verify_basis_points: t.verify_basis_points,
            })
            .collect()
    }

    /// Суточный шаг целиком: принять сутки, переписать прогресс, при
    /// срабатывании триггера выставить флаг. После флага запись
    /// продолжается тем же вызовом: новые сутки идут в прогресс,
    /// но во флаг уже не входят.
    pub fn observe_day(
        &mut self,
        root: &Path,
        tally: DayTally,
        now_utc: &str,
    ) -> Result<ObserveOutcome, WatchError> {
        self.push_tally(tally)?;
        write_progress_csv(&progress_csv_path(root), &self.progress_rows())?;
        let flag = self.write_ready_flag_if_due(root, now_utc)?;
        Ok(ObserveOutcome {
            progress_rows: self.tallies.len(),
            flag,
        })
    }

    /// Выставляет флаг, если триггер сработал впервые. Ровно один раз:
    /// повторный вызов возвращает `None`, файл не перезаписывается.
    /// Файл, уже лежащий на диске (перезапуск после флага), тоже даёт `None`
    /// без перезаписи; чужой символ в нём — ошибка, порча — ошибка.
    pub fn write_ready_flag_if_due(
        &mut self,
        root: &Path,
        now_utc: &str,
    ) -> Result<Option<ReadyFlag>, WatchError> {
        if self.flag_written {
            return Ok(None);
        }
        if now_utc.is_empty() {
            return Err(bad_flag("пустая метка времени".to_string()));
        }
        let n_c2 = self.n_c2_total();
        let g = self.g_c2();
        if n_c2 < TRIGGER_N_C2 || g < TRIGGER_G {
            return Ok(None);
        }
        let flag = ReadyFlag {
            symbol: self.symbol.clone(),
            n_c2,
            g,
            ready_at_utc: now_utc.to_string(),
            days: self.sample_days(),
        };
        let path = ready_flag_path(root);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(mut f) => {
                f.write_all(flag_text(&flag).as_bytes())?;
                f.flush()?;
            }
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {
                let existing = require_ready_flag(&path)?;
                if existing.symbol != self.symbol {
                    return Err(WatchError::SymbolMismatch {
                        expected: self.symbol.clone(),
                        got: existing.symbol,
                    });
                }
                self.flag_written = true;
                return Ok(None);
            }
            Err(e) => return Err(WatchError::Io(e.to_string())),
        }
        self.flag_written = true;
        Ok(Some(flag))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lob::levels::DeathKind;

    const MEDIAN_MS: i64 = 10_000;

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

    // SHARED WITH 5.2: общая фикстура C1/C2. Шаг 5.2 обязан пересчитать эти же
    // восемь записей и получить те же числа: n_c1 = 5, n_c2 = 2 при медиане
    // 10 000 мс. Состав: два чистых C2; два C1 без C2 (долгая жизнь; повтор
    // меньше двух); один C1 ровно на медиане (строгая граница — не C2);
    // съеденный бид, снятый аск и смешанный бид — вне C1.
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

    fn tally(gap: bool, violations: u64, basis: u64) -> DayTally {
        DayTally {
            symbol: "TST".to_string(),
            day_utc: "2026-01-01".to_string(),
            n_c1: 5,
            n_c2: 2,
            has_gap_over_6h: gap,
            verify_test1_violations: violations,
            verify_basis_points: basis,
        }
    }

    #[test]
    fn shared_fixture_gives_frozen_c1_c2_counts() {
        let recs = shared_c1_c2_fixture();
        let n_c1 = recs.iter().filter(|r| is_c1(r)).count();
        let n_c2 = recs.iter().filter(|r| is_c2(r, MEDIAN_MS)).count();
        assert_eq!(n_c1, 5, "C1: записи 1,2,3,7,8");
        assert_eq!(n_c2, 2, "C2: записи 1,8");
        assert!(
            recs.iter().filter(|r| is_c2(r, MEDIAN_MS)).all(is_c1),
            "C2 строго внутри C1: из условий C2 следуют условия C1"
        );
        let t = tally_day("TST", "2026-01-01", &recs, MEDIAN_MS, false, 0, 50_000);
        assert_eq!(t.n_c1, 5);
        assert_eq!(t.n_c2, 2);
        assert!(day_eligible(&t));
    }

    #[test]
    fn eligibility_encodes_h8_and_test1_threshold() {
        assert!(day_eligible(&tally(false, 0, 50_000)), "чистые сутки годны");
        assert!(
            !day_eligible(&tally(true, 0, 50_000)),
            "H8: разрыв больше 6 часов"
        );
        assert!(
            !day_eligible(&tally(false, 1, 10_000)),
            "ровно 0.01% — уже не годно, граница строгая"
        );
        assert!(
            day_eligible(&tally(false, 1, 10_001)),
            "ниже порога — годно"
        );
        assert!(
            !day_eligible(&tally(false, 0, 0)),
            "проверок не было — годности нет"
        );
        assert!(
            !day_eligible(&tally(false, 10, 10_000)),
            "провал verify целиком"
        );
        assert!(day_eligible(&tally(false, 5, 100_000)), "0.005% — годно");
    }

    #[test]
    fn twelve_good_days_raise_flag_once_with_frozen_numbers() {
        let dir = tempfile::tempdir().expect("песочница");
        let root = dir.path();
        let mut st = WatchState::new("TST", MEDIAN_MS);
        assert!(st.is_empty());
        // Двое суток-мусора: разрыв и провал verify. Даже с большим n_c2
        // в зачёт не идут, а отодвигают флаг.
        let junk_gap = DayTally {
            day_utc: "2026-01-01".to_string(),
            n_c1: 60,
            n_c2: 50,
            has_gap_over_6h: true,
            ..tally(false, 0, 50_000)
        };
        let junk_verify = DayTally {
            day_utc: "2026-01-02".to_string(),
            n_c1: 60,
            n_c2: 50,
            verify_test1_violations: 10,
            verify_basis_points: 10_000,
            ..tally(false, 0, 50_000)
        };
        st.observe_day(root, junk_gap, "2026-01-03T00:00:00Z")
            .expect("мусор тоже пишется в прогресс");
        st.observe_day(root, junk_verify, "2026-01-03T00:00:00Z")
            .expect("мусор тоже пишется в прогресс");
        assert!(!st.is_due());
        assert_eq!(st.g_c2(), 0);
        for d in 3..=14u32 {
            let day = format!("2026-01-{d:02}");
            let t = DayTally {
                day_utc: day,
                n_c1: 12,
                n_c2: 9,
                ..tally(false, 0, 50_000)
            };
            let out = st
                .observe_day(root, t, "2026-02-01T00:00:00Z")
                .expect("сутки копятся");
            if d < 14 {
                assert!(out.flag.is_none(), "флаг раньше двенадцатых годных суток");
            } else {
                let flag = out.flag.expect("двенадцатые годные сутки выставляют флаг");
                assert_eq!(flag.n_c2, 108, "мусорные 100 наблюдений в сумму не вошли");
                assert_eq!(flag.g, 12);
                assert_eq!(flag.days.len(), 12);
                assert_eq!(flag.days[0], "2026-01-03");
            }
        }
        assert_eq!(st.len(), 14);
        assert_eq!(st.n_c2_total(), 108);
        // Монотонность Decision 21: условия C1 выполнены сами.
        assert!(st.n_c1_total() >= TRIGGER_N_C2);
        assert!(st.g_c1() >= TRIGGER_G);
        // Ровно один раз: повтор не перезаписывает файл.
        let raw_before = std::fs::read(ready_flag_path(root)).expect("флаг лежит");
        assert!(st
            .write_ready_flag_if_due(root, "2026-02-02T00:00:00Z")
            .expect("повтор — не ошибка")
            .is_none());
        let raw_after = std::fs::read(ready_flag_path(root)).expect("флаг лежит");
        assert_eq!(raw_before, raw_after, "флаг не перезаписывается");
        let back = require_ready_flag(&ready_flag_path(root)).expect("флаг читается");
        assert_eq!(back.n_c2, 108);
        assert_eq!(back.g, 12);
        assert_eq!(back.days.len(), 12);
        // progress.csv — строка на сутки, мусор помечен негодным.
        let rows = read_progress_rows(&progress_csv_path(root)).expect("прогресс читается");
        assert_eq!(rows.len(), 14, "строка на сутки");
        assert_eq!(rows.iter().filter(|r| r.eligible).count(), 12);
        assert_eq!(rows[0].day_utc, "2026-01-01");
        assert!(!rows[0].eligible);
    }

    #[test]
    fn confirmatory_without_flag_is_err() {
        let dir = tempfile::tempdir().expect("песочница");
        let path = ready_flag_path(dir.path());
        assert!(
            require_ready_flag(&path).is_err(),
            "будущий markout --confirmatory без флага обязан завершиться ненулевым кодом"
        );
        let flag = ReadyFlag {
            symbol: "TST".to_string(),
            n_c2: 108,
            g: 12,
            ready_at_utc: "2026-02-01T00:00:00Z".to_string(),
            days: vec!["2026-01-03".to_string()],
        };
        write_ready_flag(&path, &flag).expect("флаг пишется раз");
        assert_eq!(require_ready_flag(&path).expect("с флагом — пропуск"), flag);
        assert!(
            write_ready_flag(&path, &flag).is_err(),
            "второй флаг — отказ"
        );
    }

    #[test]
    fn push_rejects_bad_tallies() {
        let mut st = WatchState::new("TST", MEDIAN_MS);
        let foreign = DayTally {
            symbol: "OTHER".to_string(),
            ..tally(false, 0, 50_000)
        };
        assert!(st.push_tally(foreign).is_err(), "H10: чужой символ");
        let bad_day = DayTally {
            day_utc: "01.01.2026".to_string(),
            ..tally(false, 0, 50_000)
        };
        assert!(st.push_tally(bad_day).is_err(), "формат суток строгий");
        let subset = DayTally {
            n_c1: 2,
            n_c2: 5,
            ..tally(false, 0, 50_000)
        };
        assert!(st.push_tally(subset).is_err(), "C2 не больше C1");
        st.push_tally(tally(false, 0, 50_000))
            .expect("первые сутки");
        assert!(
            st.push_tally(tally(false, 0, 50_000)).is_err(),
            "дубликат суток"
        );
        assert_eq!(st.len(), 1);
    }

    #[test]
    fn gap_share_ppm_counts_gap_days() {
        assert_eq!(gap_day_share_ppm(&[]), None, "суток нет — доли нет");
        let clean: Vec<DayTally> = (0..8).map(|_| tally(false, 0, 50_000)).collect();
        assert_eq!(gap_day_share_ppm(&clean), Some(0));
        let mut mixed = clean.clone();
        mixed[0].has_gap_over_6h = true;
        mixed[1].has_gap_over_6h = true;
        assert_eq!(gap_day_share_ppm(&mixed), Some(250_000), "2 из 8 — 25%");
        assert!(gap_share_within_budget(9_999));
        assert!(
            !gap_share_within_budget(10_000),
            "ровно 1% — уже сверх порога"
        );
    }

    #[test]
    fn progress_csv_round_trips_with_header() {
        let dir = tempfile::tempdir().expect("песочница");
        let path = progress_csv_path(dir.path());
        let rows = vec![
            ProgressRow {
                day_utc: "2026-01-01".to_string(),
                symbol: "TST".to_string(),
                n_c1: 5,
                n_c2: 2,
                eligible: true,
                has_gap_over_6h: false,
                verify_test1_violations: 0,
                verify_basis_points: 50_000,
            },
            ProgressRow {
                day_utc: "2026-01-02".to_string(),
                symbol: "TST".to_string(),
                n_c1: 0,
                n_c2: 0,
                eligible: false,
                has_gap_over_6h: true,
                verify_test1_violations: 0,
                verify_basis_points: 50_000,
            },
        ];
        write_progress_csv(&path, &rows).expect("прогресс пишется");
        let raw = std::fs::read_to_string(&path).expect("прогресс читается как текст");
        let header = raw.lines().next().expect("шапка обязана быть");
        assert_eq!(
            header,
            "day_utc,symbol,n_c1,n_c2,eligible,has_gap_over_6h,verify_test1_violations,verify_basis_points"
        );
        assert_eq!(read_progress_rows(&path).expect("круговой проход"), rows);
        assert_eq!(
            read_progress_rows(&dir.path().join("нет.csv")),
            Ok(Vec::new())
        );
    }

    /// Граница модулей в духе шага 1.1: счётчик не знает про транспорт,
    /// часы и дробные числа. Проверка — грепом по собственному исходнику.
    /// Дробных чисел здесь нет сознательно: доли считаются целыми
    /// (порог verify — перекрёстным умножением, доля разрывов — в ppm).
    ///
    /// Запрещённые фрагменты собраны из частей: литерал целиком триггерил бы
    /// эту же проверку сам на себя.
    #[test]
    fn module_stays_detached_from_transport_clocks_and_approx_numbers() {
        const SRC: &str = include_str!("watch.rs");
        let banned = [
            concat!("by", "bit"),
            concat!("tok", "io"),
            concat!("Inst", "ant"),
            concat!("System", "Time"),
            concat!("f", "64"),
            concat!("std::", "time"),
        ];
        for b in banned {
            assert!(!SRC.contains(b), "исходник тянет запрещённое: {b}");
        }
    }
}
