//! Правило остановки подтверждающей выборки (план, §4.1, Decision 21) и
//! счётчик `n`/`G` на сессиях (таск 07).
//!
//! Гейт по ячейкам исхода-и-истории и его собственный триггер — сколько
//! суток копить, прежде чем выставить `ready.flag` — сняты таском 17: это
//! была машинерия непрерывной односимвольной записи `lob record`
//! (`interfaces.md`, «Что построено под отменённый дизайн»), и ничего в
//! `lob watch` (`commands/lob/watch.rs`) её больше не вызывает — счётчик там
//! идёт на сессиях (`SessionTally`/`WatchSample` ниже). Из старой машинерии
//! остались только те части, что до сих пор читает живой гейт G1
//! (`markup::run_confirmatory`, `commands/lob/markout.rs --confirmatory`):
//! `DayTally`/`tally_day` — суточный счётчик качества verify (без счёта
//! ячеек — считать их больше некому); `day_eligible` — общий предикат
//! годности суток; `ReadyFlag`/`require_ready_flag`/`write_ready_flag` —
//! замороженный снимок дней выборки, который G1 требует перед стартом
//! (сейчас его пишет не сам код — файл готовится заранее, тем же приёмом,
//! что и раньше, до сноса триггера).
//!
//! Правила состава выборки G1:
//!
//! - Сутки, не прошедшие verify (доля расхождений теста 1 не строго меньше
//!   0.01%), выбрасываются целиком и кластером не считаются.
//! - Момент анализа выбирается только флагом (Decision 21): подтверждающая
//!   выборка — ровно сутки из `ready.flag`; запись после флага продолжается,
//!   но эти сутки в анализ не входят и остаются отложенными.
//!
//! Метку времени для флага строкой передаёт вызывающий: часы здесь не
//! читаются, и это свойство воспроизводимости, а не стиль, — иначе реплей и
//! живой прогон дали бы разные флаги на тех же данных.

use std::collections::BTreeMap;
use std::fs::{File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

// ---------------------------------------------------------------------------
// Константы done 4.1. Каждое число — из плана.
// ---------------------------------------------------------------------------

/// Порог теста 1 из done 4.1: доля расхождений строго меньше 0.01%,
/// то есть числитель/знаменатель `< 1/10000`. Граница строгая: ровно 0.01%
/// уже не годно.
pub const VERIFY_TEST1_MAX_NUM: u64 = 1;
pub const VERIFY_TEST1_MAX_DEN: u64 = 10_000;

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
    /// `progress-*.csv` не разобрался как CSV.
    Csv(String),
    /// Чужой символ при состоянии на один символ — `WatchSample` держит
    /// ровно одного кандидата пула.
    SymbolMismatch { expected: String, got: String },
    /// Таск 07: та же сессия (каталог) подана дважды.
    DuplicateSession { session_id: String },
    /// Строка суток — не `YYYY-MM-DD`.
    BadDay { day: String },
    /// `ready.flag` отсутствует: `markout --confirmatory`
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
            WatchError::DuplicateSession { session_id } => {
                write!(f, "сессия {session_id} уже учтена")
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
// DayTally и общий предикат годности суток — вход гейта G1
// (`markup::run_confirmatory`). Счёт по ячейкам исхода-и-истории снят
// таском 17 вместе с их предикатами: G1 их не читал, они копились только
// ради снятого триггера (см. докстрока модуля).
// ---------------------------------------------------------------------------

/// Счётчики качества verify одних суток UTC — единственное, что читает G1.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DayTally {
    /// Символ подтверждающей записи — один на кандидата пула.
    pub symbol: String,
    /// Сутки UTC как `YYYY-MM-DD`.
    pub day_utc: String,
    /// Расхождения теста 1 из `verify.csv` за сутки (числитель доли).
    pub verify_test1_violations: u64,
    /// Число проверок теста 1 за сутки (знаменатель доли).
    pub verify_basis_points: u64,
}

/// Общий предикат годности суток на весь документ (Decision 21): доля
/// расхождений теста 1 строго меньше 0.01% (done 4.1). Ноль проверок —
/// не годно: доказательств чистоты нет, и отсутствие данных не есть чистые
/// данные.
pub fn day_eligible(t: &DayTally) -> bool {
    if t.verify_basis_points == 0 {
        return false;
    }
    (t.verify_test1_violations as u128) * (VERIFY_TEST1_MAX_DEN as u128)
        < (t.verify_basis_points as u128) * (VERIFY_TEST1_MAX_NUM as u128)
}

/// Собирает `DayTally` из счётчиков качества verify суток.
pub fn tally_day(
    symbol: &str,
    day_utc: &str,
    verify_test1_violations: u64,
    verify_basis_points: u64,
) -> DayTally {
    DayTally {
        symbol: symbol.to_string(),
        day_utc: day_utc.to_string(),
        verify_test1_violations,
        verify_basis_points,
    }
}

/// `ready.flag` — один на корень записи.
pub fn ready_flag_path(root: &Path) -> PathBuf {
    root.join("ready.flag")
}

// ---------------------------------------------------------------------------
// ready.flag: n, G, время, список суток выборки.
// ---------------------------------------------------------------------------

/// Замороженный снимок момента анализа: выборка — ровно эти сутки
/// (Decision 21). Числа дальше не пересчитываются: сутки после флага
/// остаются отложенной выборкой и сюда не входят.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReadyFlag {
    /// Символ подтверждающей записи — тот же кандидат, что и в `DayTally`.
    pub symbol: String,
    /// Сумма наблюдений по годным суткам выборки.
    pub n: u64,
    /// Число годных суток с наблюдением.
    pub g: u64,
    /// Момент выставления флага строкой UTC. Строкой, а не часами этого
    /// модуля: время передаёт вызывающий.
    pub ready_at_utc: String,
    /// Сутки выборки по возрастанию.
    pub days: Vec<String>,
}

fn flag_text(flag: &ReadyFlag) -> String {
    format!(
        "symbol={}\nn={}\ng={}\nready_at_utc={}\ndays={}\n",
        flag.symbol,
        flag.n,
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
    let (k1, n_s) = kv(l1)?;
    let (k2, g_s) = kv(l2)?;
    let (k3, ready_at) = kv(l3)?;
    let (k4, days_s) = kv(l4)?;
    if (k0, k1, k2, k3, k4) != ("symbol", "n", "g", "ready_at_utc", "days") {
        return Err(bad_flag(
            "ключи обязаны идти порядком symbol,n,g,ready_at_utc,days".to_string(),
        ));
    }
    if symbol.is_empty() {
        return Err(bad_flag("пустой symbol".to_string()));
    }
    let n: u64 = n_s
        .parse()
        .map_err(|_| bad_flag(format!("n не число: {n_s}")))?;
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
        n,
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
// WatchState (копил `DayTally` по суткам, решал, когда выставить `ready.flag`
// по счёту ячеек исхода-и-истории) снят таском 17 вместе с ним самим: ничего
// в `lob watch` (`commands/lob/watch.rs`) его больше не зовёт — счётчик там
// на сессиях (`WatchSample` ниже). `ready.flag`, который он писал, сегодня
// готовится заранее (тем же форматом, что `write_ready_flag`/`ReadyFlag`
// выше) — снят только код, решавший, когда его выставить.
// ---------------------------------------------------------------------------

// =============================================================================
// Счётчик n/G на сессиях (таск 07) — то, что реально исполняет `lob watch`
// (`commands/lob/watch.rs`): параметризован вердиктным профилем
// (`shortlist::build_profile_grid`, id `marginal:...`/`cross:...`), копит
// наблюдения через **сессии** (`lob session`, таск 04), а не один
// непрерывный поток. Годность суток — «состоялась хотя бы одна сессия и
// прошла сверку целиком», не «разрыва не было».
// =============================================================================

use crate::lob::shortlist::CONFIRM_MIN_N;
use crate::stats::G_MIN;

/// Один сеанс записи (`lob session`) одного символа. Вызывающий
/// (`commands/lob/watch.rs`) уже решил, прошла ли сессия сверку целиком
/// (`lob verify`), и посчитал `n` — сколько наблюдений вердиктного профиля
/// она дала; какой именно профиль и как классифицировать `LevelRecord` —
/// дело вызывающего, не этого счётчика (сетка — `shortlist::build_profile_grid`,
/// полный перебор по всем осям — таски 10/12). Этот тип не знает про
/// `LevelRecord`, бинлог, часы или сеть — только считает переданные числа.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionTally {
    /// Имя каталога сессии — для `progress.csv`/отладки, в логику не входит.
    pub session_id: String,
    /// Календарные сутки старта сессии, UTC, `YYYY-MM-DD`. Сутки — единица
    /// кластера `G` (бриф: «в сутках теперь одна-две сессии»); сессия —
    /// единица записи.
    pub day_utc: String,
    /// Час старта сессии UTC (история 8, R41) — печатается в `progress.csv`.
    pub start_hour_utc: u32,
    /// Символ кандидата пула. `WatchSample` держит ровно одного кандидата —
    /// тот же принцип изоляции состояния, что у `WatchState` выше.
    pub symbol: String,
    /// Сессия целиком прошла сверку (`lob verify`) по этому символу.
    /// `false` — сессия выбрасывается целиком из `n`: даже ненулевой `n`,
    /// переданный ниже, в счёт не идёт (критерий приёмки таска 07).
    pub verified: bool,
    /// Наблюдений вердиктного профиля в этой сессии. Игнорируется, если
    /// `verified == false`.
    pub n: u64,
}

/// Сутки как кластер сессий — их может быть одна или две (бриф). Годность —
/// только качество, без требования наблюдений: `G` считает пересечение с
/// `n >= 1` отдельно, тем же приёмом, что старый `DayTally`/`day_eligible`
/// выше, но предикат другой (`session_day_eligible`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionDay {
    pub day_utc: String,
    pub symbol: String,
    pub sessions: Vec<SessionTally>,
}

impl SessionDay {
    /// Сумма `n` по верифицированным сессиям суток; сессия без сверки —
    /// нулевой вклад, даже если у неё было наблюдение (выброшена целиком).
    pub fn n(&self) -> u64 {
        self.sessions
            .iter()
            .filter(|s| s.verified)
            .map(|s| s.n)
            .fold(0, u64::saturating_add)
    }

    /// Часы старта всех сессий суток по возрастанию — колонка `progress.csv`.
    pub fn start_hours_utc(&self) -> Vec<u32> {
        let mut hours: Vec<u32> = self.sessions.iter().map(|s| s.start_hour_utc).collect();
        hours.sort_unstable();
        hours
    }
}

/// Предикат годности суток для сессионной модели (таск 07, спека история 35,
/// «сутки остаются единицей кластера»): сутки годны, если состоялась хотя бы
/// одна сессия и она прошла сверку целиком. Сессия без сверки не годна сама
/// по себе; если она была единственной за сутки — сутки не считаются
/// кластером вовсе (критерий приёмки таска 07). Разрыв записи внутри одной
/// сессии сюда не входит: это забота самой сессии (её `gaps.csv`/verify),
/// не календарного кластера.
///
/// Единственная реализация: и `progress.csv` (`WatchSample::progress_rows`),
/// и триггер флага (`WatchSample::is_due`) читают ровно эту функцию — тест
/// `session_gate_and_progress_share_one_predicate_and_counter` ниже проверяет
/// на общей фикстуре, что оба пути дают одинаковые `n`/`G`.
pub fn session_day_eligible(day: &SessionDay) -> bool {
    day.sessions.iter().any(|s| s.verified)
}

/// Строка `progress.csv` таска 07: сутки, профиль, часы старта сессий,
/// число сессий, `n`, годность. Один файл на пару (символ, профиль) —
/// имя несёт оба (`session_progress_csv_path`), иначе разные профили
/// одного символа затирали бы друг друга.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct SessionProgressRow {
    pub day_utc: String,
    pub symbol: String,
    pub profile_id: String,
    /// Часы старта сессий суток по возрастанию, через запятую, `HH` (история 8).
    pub session_start_hours_utc: String,
    pub sessions: u64,
    pub n: u64,
    pub eligible: bool,
}

const SESSION_PROGRESS_HEADER: [&str; 7] = [
    "day_utc",
    "symbol",
    "profile_id",
    "session_start_hours_utc",
    "sessions",
    "n",
    "eligible",
];

/// Символы, безопасные для имени файла: буквы, цифры, `-`, `_`. Остальное
/// (`:`, `|`, `=`, `,` — из id профиля вроде `cross:{sym}|{outcome}|{dist}`)
/// заменяется на `_`, чтобы `progress-*.csv`/`ready-*.flag` не пытались
/// создать поддиректорию или запятую в имени файла.
fn filename_slug(s: &str) -> String {
    s.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect()
}

/// `progress-<символ>-<профиль>.csv` — один на пару (символ, профиль).
pub fn session_progress_csv_path(root: &Path, symbol: &str, profile_id: &str) -> PathBuf {
    root.join(format!(
        "progress-{}-{}.csv",
        filename_slug(symbol),
        filename_slug(profile_id)
    ))
}

/// `ready-<символ>-<профиль>.flag` — один на пару (символ, профиль).
pub fn session_ready_flag_path(root: &Path, symbol: &str, profile_id: &str) -> PathBuf {
    root.join(format!(
        "ready-{}-{}.flag",
        filename_slug(symbol),
        filename_slug(profile_id)
    ))
}

/// Перезаписывает `progress-*.csv` целиком: строка на сутки, по возрастанию.
pub fn write_session_progress_csv(
    path: &Path,
    rows: &[SessionProgressRow],
) -> Result<(), WatchError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let file = File::create(path)?;
    let mut w = csv::WriterBuilder::new()
        .has_headers(false)
        .from_writer(file);
    w.write_record(SESSION_PROGRESS_HEADER)?;
    for row in rows {
        w.serialize(row)?;
    }
    w.flush()?;
    Ok(())
}

/// Читает строки `progress-*.csv`. Отсутствующий/пустой файл — ноль строк.
pub fn read_session_progress_rows(path: &Path) -> Result<Vec<SessionProgressRow>, WatchError> {
    if std::fs::metadata(path)
        .map(|m| m.len() == 0)
        .unwrap_or(true)
    {
        return Ok(Vec::new());
    }
    let mut r = csv::Reader::from_path(path)?;
    r.deserialize::<SessionProgressRow>()
        .collect::<Result<Vec<_>, _>>()
        .map_err(WatchError::from)
}

/// Замороженный снимок момента анализа таска 07: символ, профиль, `n`, `G`,
/// момент и список суток выборки — ровно эти сутки (Decision 21, тот же
/// принцип, что у `ReadyFlag` выше, но с профилем вместо неявной ячейки C2).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionReadyFlag {
    pub symbol: String,
    pub profile_id: String,
    /// Сумма `n` по годным суткам выборки. Не меньше `CONFIRM_MIN_N`
    /// (`shortlist::CONFIRM_MIN_N` — тот же порог, что у шорт-листа, спека
    /// история 37/R49: `n >= 100`; не изобретён здесь).
    pub n: u64,
    /// Число годных суток с наблюдением профиля. Не меньше `G_MIN`
    /// (`crate::stats::G_MIN = 7`).
    pub g: u64,
    pub ready_at_utc: String,
    pub days: Vec<String>,
}

fn session_flag_text(flag: &SessionReadyFlag) -> String {
    format!(
        "symbol={}\nprofile_id={}\nn={}\ng={}\nready_at_utc={}\ndays={}\n",
        flag.symbol,
        flag.profile_id,
        flag.n,
        flag.g,
        flag.ready_at_utc,
        flag.days.join(",")
    )
}

fn parse_session_ready_flag(text: &str) -> Result<SessionReadyFlag, WatchError> {
    let lines: Vec<&str> = text.lines().collect();
    let [l0, l1, l2, l3, l4, l5] = match lines.as_slice() {
        [a, b, c, d, e, f] => [*a, *b, *c, *d, *e, *f],
        _ => return Err(bad_flag(format!("жду 6 строк, вижу {}", lines.len()))),
    };
    let (k0, symbol) = kv(l0)?;
    let (k1, profile_id) = kv(l1)?;
    let (k2, n_s) = kv(l2)?;
    let (k3, g_s) = kv(l3)?;
    let (k4, ready_at) = kv(l4)?;
    let (k5, days_s) = kv(l5)?;
    if (k0, k1, k2, k3, k4, k5) != ("symbol", "profile_id", "n", "g", "ready_at_utc", "days") {
        return Err(bad_flag(
            "ключи обязаны идти порядком symbol,profile_id,n,g,ready_at_utc,days".to_string(),
        ));
    }
    if symbol.is_empty() {
        return Err(bad_flag("пустой symbol".to_string()));
    }
    if profile_id.is_empty() {
        return Err(bad_flag("пустой profile_id".to_string()));
    }
    let n: u64 = n_s
        .parse()
        .map_err(|_| bad_flag(format!("n не число: {n_s}")))?;
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
    Ok(SessionReadyFlag {
        symbol: symbol.to_string(),
        profile_id: profile_id.to_string(),
        n,
        g,
        ready_at_utc: ready_at.to_string(),
        days,
    })
}

/// Пишет флаг созданием файла в режиме «только если его нет» — та же
/// защита от перезаписи, что у `write_ready_flag` выше.
pub fn write_session_ready_flag(path: &Path, flag: &SessionReadyFlag) -> Result<(), WatchError> {
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
    f.write_all(session_flag_text(flag).as_bytes())?;
    f.flush()?;
    Ok(())
}

/// Заглушка для подтверждающего прогона (флаг `--confirmatory` соседней
/// подкоманды): без флага — `Err` (вызывающий завершается ненулевым кодом),
/// с флагом — сам флаг. Та же механическая защита от optional stopping, что
/// `require_ready_flag` выше, но на сессионном флаге.
pub fn require_session_ready_flag(path: &Path) -> Result<SessionReadyFlag, WatchError> {
    let text = std::fs::read_to_string(path).map_err(|e| {
        if e.kind() == io::ErrorKind::NotFound {
            WatchError::MissingFlag {
                path: path.display().to_string(),
            }
        } else {
            WatchError::Io(e.to_string())
        }
    })?;
    parse_session_ready_flag(&text)
}

/// Исход суточного шага таска 07: прогресс переписан всегда, флаг — только
/// в момент первого срабатывания триггера.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionObserveOutcome {
    pub progress_rows: usize,
    pub flag: Option<SessionReadyFlag>,
}

/// Счётчик n/G на пару (символ, вердиктный профиль) — таск 07. По инстансу
/// на каждую пару, не общее сразу на несколько (тот же принцип, что у
/// `WatchState` выше). Копит сессии по суткам; `n`/`G` — только по годным
/// суткам (`session_day_eligible`): мусор (сессия без сверки) в зачёт не
/// идёт, а если это была единственная сессия суток — отодвигает флаг целыми
/// сутками, не только своим наблюдением.
#[derive(Debug)]
pub struct WatchSample {
    symbol: String,
    profile_id: String,
    days: BTreeMap<String, SessionDay>,
    seen_sessions: std::collections::BTreeSet<String>,
    flag_written: bool,
}

impl WatchSample {
    /// Новое состояние на пару (символ, профиль).
    pub fn new(symbol: &str, profile_id: &str) -> Self {
        Self {
            symbol: symbol.to_string(),
            profile_id: profile_id.to_string(),
            days: BTreeMap::new(),
            seen_sessions: std::collections::BTreeSet::new(),
            flag_written: false,
        }
    }

    pub fn symbol(&self) -> &str {
        &self.symbol
    }

    pub fn profile_id(&self) -> &str {
        &self.profile_id
    }

    pub fn is_empty(&self) -> bool {
        self.days.is_empty()
    }

    /// Сколько суток учтено, включая негодные.
    pub fn days_len(&self) -> usize {
        self.days.len()
    }

    fn eligible_days(&self) -> impl Iterator<Item = &SessionDay> {
        self.days.values().filter(|d| session_day_eligible(d))
    }

    /// `n` вердиктного профиля через все годные сутки, через все сессии.
    pub fn n_total(&self) -> u64 {
        self.eligible_days()
            .map(SessionDay::n)
            .fold(0, u64::saturating_add)
    }

    /// `G` — годные сутки, у которых есть хотя бы одно наблюдение профиля.
    pub fn g(&self) -> usize {
        self.eligible_days().filter(|d| d.n() >= 1).count()
    }

    /// Сутки выборки по возрастанию: годные сутки с наблюдением профиля.
    pub fn sample_days(&self) -> Vec<String> {
        let mut days: Vec<String> = self
            .eligible_days()
            .filter(|d| d.n() >= 1)
            .map(|d| d.day_utc.clone())
            .collect();
        days.sort();
        days
    }

    /// Гейт таска 07 (спека история 37, R49): `n >= CONFIRM_MIN_N` и
    /// `G >= G_MIN`, и флаг ещё не стоял. Тот же метод — единственный код,
    /// применяющий гейт: `write_ready_flag_if_due` вызывает ровно его, не
    /// повторяет условие своей копией (критерий приёмки таска 07).
    pub fn is_due(&self) -> bool {
        !self.flag_written && self.n_total() >= CONFIRM_MIN_N && self.g() >= G_MIN
    }

    /// Принимает сессию: символ обязан совпасть, сутки — формат
    /// `YYYY-MM-DD`, сессия — не дублировать уже поданную по `session_id`.
    pub fn push_session(&mut self, tally: SessionTally) -> Result<(), WatchError> {
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
        if !self.seen_sessions.insert(tally.session_id.clone()) {
            return Err(WatchError::DuplicateSession {
                session_id: tally.session_id,
            });
        }
        self.days
            .entry(tally.day_utc.clone())
            .or_insert_with(|| SessionDay {
                day_utc: tally.day_utc.clone(),
                symbol: tally.symbol.clone(),
                sessions: Vec::new(),
            })
            .sessions
            .push(tally);
        Ok(())
    }

    /// Строки `progress-*.csv`: по одной на сутки, по возрастанию суток.
    pub fn progress_rows(&self) -> Vec<SessionProgressRow> {
        self.days
            .values()
            .map(|d| SessionProgressRow {
                day_utc: d.day_utc.clone(),
                symbol: d.symbol.clone(),
                profile_id: self.profile_id.clone(),
                session_start_hours_utc: d
                    .start_hours_utc()
                    .iter()
                    .map(|h| format!("{h:02}"))
                    .collect::<Vec<_>>()
                    .join(","),
                sessions: d.sessions.len() as u64,
                n: d.n(),
                eligible: session_day_eligible(d),
            })
            .collect()
    }

    /// Сессия целиком: принять, переписать прогресс, при срабатывании
    /// триггера выставить флаг — ровно один раз.
    pub fn observe_session(
        &mut self,
        root: &Path,
        tally: SessionTally,
        now_utc: &str,
    ) -> Result<SessionObserveOutcome, WatchError> {
        self.push_session(tally)?;
        write_session_progress_csv(
            &session_progress_csv_path(root, &self.symbol, &self.profile_id),
            &self.progress_rows(),
        )?;
        let flag = self.write_ready_flag_if_due(root, now_utc)?;
        Ok(SessionObserveOutcome {
            progress_rows: self.days.len(),
            flag,
        })
    }

    /// Выставляет флаг, если гейт (`is_due`) сработал впервые.
    pub fn write_ready_flag_if_due(
        &mut self,
        root: &Path,
        now_utc: &str,
    ) -> Result<Option<SessionReadyFlag>, WatchError> {
        if self.flag_written {
            return Ok(None);
        }
        if now_utc.is_empty() {
            return Err(bad_flag("пустая метка времени".to_string()));
        }
        if !self.is_due() {
            return Ok(None);
        }
        let flag = SessionReadyFlag {
            symbol: self.symbol.clone(),
            profile_id: self.profile_id.clone(),
            n: self.n_total(),
            g: self.g() as u64,
            ready_at_utc: now_utc.to_string(),
            days: self.sample_days(),
        };
        let path = session_ready_flag_path(root, &self.symbol, &self.profile_id);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(mut f) => {
                f.write_all(session_flag_text(&flag).as_bytes())?;
                f.flush()?;
            }
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {
                let existing = require_session_ready_flag(&path)?;
                if existing.symbol != self.symbol || existing.profile_id != self.profile_id {
                    return Err(WatchError::SymbolMismatch {
                        expected: format!("{}/{}", self.symbol, self.profile_id),
                        got: format!("{}/{}", existing.symbol, existing.profile_id),
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

    fn tally(violations: u64, basis: u64) -> DayTally {
        DayTally {
            symbol: "TST".to_string(),
            day_utc: "2026-01-01".to_string(),
            verify_test1_violations: violations,
            verify_basis_points: basis,
        }
    }

    #[test]
    fn tally_day_builds_from_verify_counts_and_feeds_eligibility() {
        let t = tally_day("TST", "2026-01-01", 0, 50_000);
        assert_eq!(t.symbol, "TST");
        assert_eq!(t.day_utc, "2026-01-01");
        assert!(day_eligible(&t));
    }

    #[test]
    fn eligibility_encodes_the_test1_threshold() {
        assert!(day_eligible(&tally(0, 50_000)), "чистые сутки годны");
        assert!(
            !day_eligible(&tally(1, 10_000)),
            "ровно 0.01% — уже не годно, граница строгая"
        );
        assert!(day_eligible(&tally(1, 10_001)), "ниже порога — годно");
        assert!(
            !day_eligible(&tally(0, 0)),
            "проверок не было — годности нет"
        );
        assert!(!day_eligible(&tally(10, 10_000)), "провал verify целиком");
        assert!(day_eligible(&tally(5, 100_000)), "0.005% — годно");
    }

    #[test]
    fn confirmatory_without_flag_is_err() {
        let dir = tempfile::tempdir().expect("песочница");
        let path = ready_flag_path(dir.path());
        assert!(
            require_ready_flag(&path).is_err(),
            "подтверждающий прогон без флага обязан завершиться ненулевым кодом"
        );
        let flag = ReadyFlag {
            symbol: "TST".to_string(),
            n: 108,
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

    // -------------------------------------------------------------------
    // Таск 07 — сессии и вердиктный профиль вместо ячейки C1/C2.
    // -------------------------------------------------------------------

    fn session(id: &str, day: &str, hour: u32, verified: bool, n: u64) -> SessionTally {
        SessionTally {
            session_id: id.to_string(),
            day_utc: day.to_string(),
            start_hour_utc: hour,
            symbol: "SOLUSDT".to_string(),
            verified,
            n,
        }
    }

    fn day_of(sessions: Vec<SessionTally>) -> SessionDay {
        SessionDay {
            day_utc: sessions[0].day_utc.clone(),
            symbol: sessions[0].symbol.clone(),
            sessions,
        }
    }

    #[test]
    fn session_day_eligible_requires_at_least_one_verified_session() {
        let empty = SessionDay {
            day_utc: "2026-03-01".to_string(),
            symbol: "SOLUSDT".to_string(),
            sessions: Vec::new(),
        };
        assert!(!session_day_eligible(&empty), "суток без сессий вовсе нет");

        let lone_bad = day_of(vec![session("s1", "2026-03-01", 2, false, 50)]);
        assert!(
            !session_day_eligible(&lone_bad),
            "единственная сессия без сверки — сутки не кластер"
        );
        assert_eq!(lone_bad.n(), 0, "не прошедшая сверку сессия не даёт n");

        let lone_good = day_of(vec![session("s1", "2026-03-01", 2, true, 0)]);
        assert!(
            session_day_eligible(&lone_good),
            "верифицированная сессия годна, даже если наблюдений нет"
        );

        let mixed = day_of(vec![
            session("s1", "2026-03-01", 2, false, 999),
            session("s2", "2026-03-01", 14, true, 10),
        ]);
        assert!(
            session_day_eligible(&mixed),
            "вторая сессия суток верифицирована — сутки годны"
        );
        assert_eq!(
            mixed.n(),
            10,
            "непрошедшая сверку сессия выброшена целиком, её n не подмешалось"
        );
        assert_eq!(mixed.start_hours_utc(), vec![2, 14]);
    }

    #[test]
    fn push_session_rejects_foreign_symbol_bad_day_and_duplicate_session() {
        let mut sample = WatchSample::new("SOLUSDT", "marginal:outcome=pulled");
        let foreign = SessionTally {
            symbol: "NEARUSDT".to_string(),
            ..session("s1", "2026-03-01", 2, true, 10)
        };
        assert!(
            sample.push_session(foreign).is_err(),
            "чужой символ отвергается"
        );
        let bad_day = SessionTally {
            day_utc: "01.03.2026".to_string(),
            ..session("s1", "2026-03-01", 2, true, 10)
        };
        assert!(
            sample.push_session(bad_day).is_err(),
            "формат суток строгий"
        );
        sample
            .push_session(session("s1", "2026-03-01", 2, true, 10))
            .expect("первая сессия");
        assert!(
            sample
                .push_session(session("s1", "2026-03-02", 5, true, 10))
                .is_err(),
            "тот же session_id — дубликат, даже на другие сутки"
        );
        assert_eq!(sample.days_len(), 1);
    }

    /// Критерий приёмки таска 07: предикат годности суток и счётчик `n` у
    /// `lob watch` — тот же код, что применяет гейт. Пересчёт из
    /// `progress_rows()` (то, что видит человек/следующий таск в CSV) тем
    /// же путём, что и `is_due()`/флаг, обязан дать те же числа — иначе
    /// прогресс и гейт могли бы разойтись.
    #[test]
    fn session_gate_and_progress_share_one_predicate_and_counter() {
        let dir = tempfile::tempdir().expect("песочница");
        let root = dir.path();
        let mut sample = WatchSample::new("SOLUSDT", "marginal:outcome=pulled");
        let now = "2026-04-01T00:00:00Z";
        let mut fired = None;
        // Семь годных суток по одной верифицированной сессии, 15
        // наблюдений каждая: G=7 (порог), n=105 (>= CONFIRM_MIN_N=100).
        for d in 1..=7u32 {
            let day = format!("2026-03-{d:02}");
            let tally = session(&format!("s{d}"), &day, 2, true, 15);
            let outcome = sample.observe_session(root, tally, now).expect("сессия");
            if let Some(f) = outcome.flag {
                fired = Some(f);
            }
        }
        // Восьмые сутки: единственная сессия, сверку не прошла — выброшена
        // целиком, несмотря на большой n, и флаг уже стоит.
        let junk = session("junk", "2026-03-08", 5, false, 999);
        let outcome = sample.observe_session(root, junk, now).expect("сессия");
        assert!(
            outcome.flag.is_none(),
            "флаг уже выставлен седьмыми сутками, повторно не выставляется"
        );

        let rows = sample.progress_rows();
        assert_eq!(rows.len(), 8, "восемь суток, включая негодные");
        let recomputed_n: u64 = rows.iter().filter(|r| r.eligible).map(|r| r.n).sum();
        let recomputed_g = rows.iter().filter(|r| r.eligible && r.n >= 1).count();
        assert_eq!(
            recomputed_n,
            sample.n_total(),
            "n из progress.csv обязан совпасть с n гейта"
        );
        assert_eq!(
            recomputed_g,
            sample.g(),
            "G из progress.csv обязан совпасть с G гейта"
        );
        assert_eq!(sample.n_total(), 105, "n не меняется от негодных суток");
        assert_eq!(sample.g(), 7, "G не меняется от негодных суток");
        assert!(
            !sample.is_due(),
            "флаг уже стоит — is_due больше не взводится повторно"
        );
        let flag = fired.expect("седьмые годные сутки обязаны выставить флаг");
        assert_eq!(flag.symbol, "SOLUSDT");
        assert_eq!(flag.profile_id, "marginal:outcome=pulled");
        assert_eq!(flag.n, recomputed_n);
        assert_eq!(flag.g, recomputed_g as u64);
        assert_eq!(flag.days.len(), 7);

        let junk_row = rows
            .iter()
            .find(|r| r.day_utc == "2026-03-08")
            .expect("строка на восьмые сутки есть");
        assert!(
            !junk_row.eligible,
            "единственная непрошедшая сверку сессия — сутки негодны"
        );
        assert_eq!(junk_row.n, 0);
        assert_eq!(junk_row.session_start_hours_utc, "05");

        let first_row = rows
            .iter()
            .find(|r| r.day_utc == "2026-03-01")
            .expect("строка на первые сутки есть");
        assert_eq!(first_row.session_start_hours_utc, "02");
        assert_eq!(first_row.n, 15);
        assert!(first_row.eligible);

        // Флаг лежит на диске под именем, несущим и символ, и профиль.
        let flag_path = session_ready_flag_path(root, "SOLUSDT", "marginal:outcome=pulled");
        assert!(flag_path.exists());
        let back = require_session_ready_flag(&flag_path).expect("флаг читается");
        assert_eq!(back, flag);
    }

    #[test]
    fn require_session_ready_flag_without_file_is_err() {
        let dir = tempfile::tempdir().expect("песочница");
        let path = session_ready_flag_path(dir.path(), "SOLUSDT", "marginal:side=bid");
        assert!(
            require_session_ready_flag(&path).is_err(),
            "будущий подтверждающий прогон без флага обязан завершиться ненулевым кодом"
        );
        let flag = SessionReadyFlag {
            symbol: "SOLUSDT".to_string(),
            profile_id: "marginal:side=bid".to_string(),
            n: 108,
            g: 7,
            ready_at_utc: "2026-04-01T00:00:00Z".to_string(),
            days: vec!["2026-03-01".to_string()],
        };
        write_session_ready_flag(&path, &flag).expect("флаг пишется раз");
        assert_eq!(
            require_session_ready_flag(&path).expect("с флагом — пропуск"),
            flag
        );
        assert!(
            write_session_ready_flag(&path, &flag).is_err(),
            "второй флаг — отказ"
        );
    }

    #[test]
    fn session_progress_csv_round_trips_with_header() {
        let dir = tempfile::tempdir().expect("песочница");
        let path = session_progress_csv_path(dir.path(), "SOLUSDT", "cross:SOLUSDT|pulled|[0,1)");
        let rows = vec![SessionProgressRow {
            day_utc: "2026-03-01".to_string(),
            symbol: "SOLUSDT".to_string(),
            profile_id: "cross:SOLUSDT|pulled|[0,1)".to_string(),
            session_start_hours_utc: "02,14".to_string(),
            sessions: 2,
            n: 15,
            eligible: true,
        }];
        write_session_progress_csv(&path, &rows).expect("прогресс пишется");
        let raw = std::fs::read_to_string(&path).expect("прогресс читается");
        let header = raw.lines().next().expect("шапка обязана быть");
        assert_eq!(
            header,
            "day_utc,symbol,profile_id,session_start_hours_utc,sessions,n,eligible"
        );
        assert_eq!(
            read_session_progress_rows(&path).expect("круговой проход"),
            rows
        );
        assert_eq!(
            read_session_progress_rows(&dir.path().join("нет.csv")),
            Ok(Vec::new())
        );
        // Имя файла несёт слаг профиля: `:`/`|`/`,`/`[`/`)` не проезжают как есть.
        assert!(path
            .file_name()
            .unwrap()
            .to_string_lossy()
            .starts_with("progress-SOLUSDT-cross_SOLUSDT_pulled_"));
    }
}
