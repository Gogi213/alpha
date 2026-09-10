//! `lob record --symbol <S> --root data/bybit` — непрерывная запись (шаг 0.3).
//!
//! Ротация по суткам UTC, `gaps.csv`, заголовок с `tickSize`/`qtyStep`,
//! двойной детектор смены шагов (Decision 7; шаг 0.3 `PLAN.md`).
//!
//! # Два детектора смены шагов, и оба нужны
//!
//! Горячий путь (`check_level_step`, вызывается из `stage_book_update` и
//! `stage_trade` до любого изменения книги и файла): одна целочисленная
//! операция `%` на уже разобранном поле. Ловит первое же затронутое событие —
//! без него цены в дельтах тихо поехали бы по масштабу в невосстановимых
//! данных. Холодный путь (часовой таймер в `run_record`, тот же, что ходит за
//! свободным местом): перечитывает `/v5/market/instruments-info` и даёт
//! авторитетные значения для заголовка следующего файла. Без него ротация не
//! сработала бы никогда — горячий детектор знает, что шаг не тот, но не знает,
//! какой тот.
//!
//! # Раскладка файлов под `--root`
//!
//! ```text
//! <root>/<SYMBOL>-<YYYY-MM-DD>.binlog      первая часть суток
//! <root>/<SYMBOL>-<YYYY-MM-DD>-p<N>.binlog  N-я часть тех же суток (ротация
//!                                          по смене шагов посреди суток)
//! <root>/gaps.csv                          общий журнал разрывов, шапка +
//!                                          ноль строк в норме
//! <root>/verify.csv                        сверка книги с REST по `u` раз в 5 минут
//!                                          (шаг 0.8): пишет сайдкар `bybit::verify_sidecar`,
//!                                          цикл записи только шлёт клон книги в канал
//! <root>/instruments.csv                   вход: пишет `lob pick`, читает
//!                                          `load_steps_for_symbol`
//! ```
//!
//! Каждый `.binlog` самодостаточен (Decision 7): заголовок несёт `tickSize` и
//! `qtyStep`, кадр 0 — синтетический полный снапшот книги на момент открытия
//! файла. Кадр — `u32` длина + один zstd-кадр, записи — дельты в тиках/varint
//! (Decision 23); всё это уже реализовано в `crate::binlog` — этот модуль его
//! потребитель и формата не касается.
//!
//! # Где ноль аллокаций, а где нет
//!
//! Горячий путь — `stage_book_update`/`stage_trade` от уже разобранного
//! `Update`/`Trade` (граница `bybit::ws`): применение к книге, проверка шагов,
//! перевод в тики/лоты, постановка в переиспользуемый батч, запись кадра.
//! После прогрева (батч при ёмкости, книга при ёмкости, `Writer` прогрет
//! первым кадром) он не аллоцирует — см. тест `steady_events_allocate_nothing`.
//! Разбор JSON (`serde_json::Value`) и транспорт (`tungstenite::Vec` на кадр)
//! в этот замер не входят: первый аллоцирует по построению и принадлежит
//! строке «транспорт ≤ 1 на кадр» гейта GC, второй — известная принятая цена
//! (`PLAN.md`, Out of scope). Замер идёт от границы `ws.rs`, как `steady_frames`
//! в `src/lob/levels.rs` меряет от границы кадров, а не от сокета.

use std::fs::{File, OpenOptions};
use std::io;
use std::path::{Path, PathBuf};
use std::time::Duration;

use clap::Args;

use crate::binlog::{Header, Record};
use crate::book::{Book, Side, Update};
use crate::bybit::conn::{
    BackoffConfig, BybitPublicLinearConnector, Clock, ConnConfig, ConnEvent, Connection,
    SystemClock,
};
use crate::bybit::rest::{
    fetch_all_linear_instruments, BybitPublicRest, PublicRest, BYBIT_MAINNET_URL,
};
use crate::bybit::verify_sidecar::{offer_verify_update, spawn_verify_sidecar, VerifyMsg};
use crate::bybit::ws::{Event, Trade};

// ---------------------------------------------------------------------------
// Константы. Каждое число — из `PLAN.md`, кроме явно помеченных
// эксплуатационных выборов этого файла.
// ---------------------------------------------------------------------------

/// Связывающий бюджет Decision 23: ≤ 150 МБ на символ-сутки после zstd.
/// Именно его потребляет диск и именно он питает правило свободного места.
pub const DAY_BUDGET_BYTES: u64 = 150 * 1024 * 1024;

/// Старт запрещён при свободном месте меньше `МБ_в_сутки × 14` (строка
/// «Свободное место» гейта GC). Абсолютный порог: у открытой записи нет
/// горизонта, запас обязан покрывать недели без присмотра.
pub const START_FREE_BYTES_REQUIRED: u64 = DAY_BUDGET_BYTES * 14;

/// Падение ниже `МБ_в_сутки × 2` — остановка с записью в `gaps.csv`.
pub const STOP_FREE_BYTES_REQUIRED: u64 = DAY_BUDGET_BYTES * 2;

/// Потолок записей в одном кадре — он же `max_records_per_frame` заголовка
/// (Decision 23, ревизия 10: потолок берётся из файла, а выбирает и хранит
/// его вызывающий, то есть этот рекордер). Значение — эксплуатационный выбор
/// батчинга, не свойство рынка: на порядки выше любого кадра, который даёт
/// `FRAME_TARGET_RECORDS` ниже, и на порядки ниже границы дня.
pub const MAX_RECORDS_PER_FRAME: u32 = 20_000;

/// Сколько записей копим перед записью кадра. Эксплуатационный выбор: кадр
/// раз в ~10 секунд живого потока (порядка сотни записей уровней в секунду)
/// — компромисс между зря потерянными секундами при крахе и степенью сжатия
/// мелких кадров. Байты на запись измеряются на пилоте 3.1 и дальше работают
/// как регрессия, поэтому это число обязано быть именованным и видимым, а не
/// литералом в теле цикла.
pub const FRAME_TARGET_RECORDS: usize = 1_000;

/// Уровень zstd суточных файлов. Не изобретённое число: дефолт библиотеки до
/// пилота 3.1, который назначает его измерением байта на запись (см. doc
/// `binlog::Writer::create` — уровень там параметр именно поэтому).
pub const ZSTD_LEVEL: i32 = zstd::DEFAULT_COMPRESSION_LEVEL;

/// Период часового таймера: свободное место, `clock.csv` (шаг 0.5, хук — см.
/// `run_session`) и перечитывание `instruments-info`. Один таймер на всё —
/// три часовых будильника дрейфовали бы друг относительно друга и будили
/// процесс трижды в час вместо одного раза.
pub const HOURLY_REFRESH_SECS: u64 = 3_600;

/// Пинг WS: Bybit рвёт молчащее соединение по своему таймауту (факт протокола,
/// не число `PLAN.md` — поэтому константа здесь, рядом с соединением, как
/// `MEASUREMENT_PING_INTERVAL` в `lob.rs` рядом со своим).
const RECORD_PING_INTERVAL: Duration = Duration::from_secs(20);

/// Бэкофф переподключения записи — эксплуатационный выбор, не число плана
/// (тот же смысл, что `MEASUREMENT_BACKOFF` в `lob.rs`).
const RECORD_BACKOFF: BackoffConfig = BackoffConfig {
    initial: Duration::from_millis(500),
    max: Duration::from_secs(30),
    multiplier: 2,
};

/// Ёмкость канала «сокет → запись» (A3: ввод-вывод отдельно от потока
/// решений). Задержки записи измеряются миллисекундами, а не секундами,
/// поэтому 4096 кадров — это часы запаса против всплесков, а не минуты.
const CHANNEL_CAPACITY: usize = 4_096;

// ---------------------------------------------------------------------------
// Ошибки. Один тип на весь модуль: и сбой ввода-вывода, и отторгнутое событие.
// ---------------------------------------------------------------------------

/// Отказ рекордера. Ни один вариант не паникует: процесс рассчитан на недели
/// без присмотра, и вырожденный вход обязан вернуться ошибкой с причиной.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RecordError {
    /// Файл не открылся / не записался / не закрылся.
    Io(String),
    /// `gaps.csv` / `instruments.csv` не разобрались как CSV.
    Csv(String),
    /// `instruments.csv`: нет файла, нет символа, не число, неположительный шаг.
    Steps(String),
    /// Шаги из кода/REST, а не из файла: нулевой или отрицательный масштаб —
    /// дельты в тиках с таким масштабом молча неверны, поэтому отказ, а не
    /// запись (та же дисциплина, что `validate_header` в `binlog`, но здесь
    /// аргумент приходит из кода вызывающего, а не с диска — см. её doc).
    BadSteps { tick_e9: i64, step_e9: i64 },
    /// Предстартовый гейт: свободного места меньше `START_FREE_BYTES_REQUIRED`.
    SpaceDenied {
        free_bytes: u64,
        required_bytes: u64,
    },
    /// Кадр не записался / не сжался.
    Binlog(String),
    /// Живое событие до первого снапшота файла. Ошибка программирования
    /// вызывающего (порядок «снапшот первым» — контракт `Recorder`), а не
    /// порча данных: событие отбрасывается loudly, файл остаётся валидным
    /// (заголовок + ноль кадров), и следующий читатель скажет `MissingSnapshot`,
    /// а не прочитает обрезанные сутки как полные.
    NoSnapshot,
    /// Горячий детектор: цена не кратна сохранённому тику или размер — шагу.
    /// Первое же затронутое событие; ни книга, ни файл не тронуты.
    Step {
        price_e9: i64,
        qty_e9: i64,
        tick_e9: i64,
        step_e9: i64,
    },
    /// Разрыв `u` последовательности Bybit. Книга больше не доверена;
    /// соединение уже шлёт ресинк-подписку само (`bybit::conn`), рекордер
    /// только фиксирует строку в `gaps.csv` и ждёт свежий снапшот.
    SequenceGap { expected: u64, got: u64 },
    /// Книга пересеклась после применения. Та же реакция, что на разрыв:
    /// строка в `gaps.csv`, ожидание снапшота, без ротации файла.
    Crossed {
        best_bid_tick: i64,
        best_ask_tick: i64,
    },
    /// Исчерпаны номера частей суток. Практически недостижимо (часть — это
    /// ротация по смене шагов внутри одних суток), но молча перезаписать
    /// часть 1 было бы потерей данных, поэтому явная ошибка.
    TooManyParts { day: String },
    /// Строка суток — не `YYYY-MM-DD`.
    BadDay { day: String },
    /// Метка времени вне диапазона календаря при форматировании дня/`ts_utc`.
    BadTimestamp { ts_ns: i64 },
}

impl std::fmt::Display for RecordError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RecordError::Io(e) => write!(f, "ввод-вывод: {e}"),
            RecordError::Csv(e) => write!(f, "CSV: {e}"),
            RecordError::Steps(e) => write!(f, "шаги инструмента: {e}"),
            RecordError::BadSteps { tick_e9, step_e9 } => {
                write!(
                    f,
                    "шаги неположительны: tick_e9={tick_e9}, step_e9={step_e9}"
                )
            }
            RecordError::SpaceDenied {
                free_bytes,
                required_bytes,
            } => write!(
                f,
                "старт запрещён: свободно {free_bytes} байт, нужно {required_bytes}"
            ),
            RecordError::Binlog(e) => write!(f, "бинлог: {e}"),
            RecordError::NoSnapshot => {
                write!(f, "живое событие до первого снапшота файла")
            }
            RecordError::Step {
                price_e9,
                qty_e9,
                tick_e9,
                step_e9,
            } => write!(
                f,
                "цена {price_e9} не на тике {tick_e9} или размер {qty_e9} не на шаге {step_e9}"
            ),
            RecordError::SequenceGap { expected, got } => {
                write!(f, "разрыв u: ждали {expected}, пришло {got}")
            }
            RecordError::Crossed {
                best_bid_tick,
                best_ask_tick,
            } => write!(
                f,
                "книга пересеклась: бид {best_bid_tick} >= аск {best_ask_tick}"
            ),
            RecordError::TooManyParts { day } => {
                write!(f, "исчерпаны номера частей суток {day}")
            }
            RecordError::BadDay { day } => {
                write!(f, "сутки не разобрались как YYYY-MM-DD: {day}")
            }
            RecordError::BadTimestamp { ts_ns } => {
                write!(f, "метка {ts_ns} нс вне диапазона календаря")
            }
        }
    }
}

impl std::error::Error for RecordError {}

impl From<io::Error> for RecordError {
    fn from(e: io::Error) -> Self {
        RecordError::Io(e.to_string())
    }
}

impl From<csv::Error> for RecordError {
    fn from(e: csv::Error) -> Self {
        RecordError::Csv(e.to_string())
    }
}

impl From<crate::binlog::BinlogError> for RecordError {
    fn from(e: crate::binlog::BinlogError) -> Self {
        RecordError::Binlog(e.to_string())
    }
}

/// Нарушение шагов одной пары цена/размер — то, что возвращает горячий
/// детектор до упаковки в `RecordError`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct StepViolation {
    pub price_e9: i64,
    pub qty_e9: i64,
    pub tick_e9: i64,
    pub step_e9: i64,
}

impl From<StepViolation> for RecordError {
    fn from(v: StepViolation) -> Self {
        RecordError::Step {
            price_e9: v.price_e9,
            qty_e9: v.qty_e9,
            tick_e9: v.tick_e9,
            step_e9: v.step_e9,
        }
    }
}

fn validate_steps(tick_e9: i64, step_e9: i64) -> Result<(), RecordError> {
    if tick_e9 <= 0 || step_e9 <= 0 {
        return Err(RecordError::BadSteps { tick_e9, step_e9 });
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Имена файлов.
// ---------------------------------------------------------------------------

/// Путь суточного файла. Часть 1 — каноническое имя без суффикса; ротации по
/// смене шагов внутри тех же суток получают `-p2`, `-p3`, … — перезаписать
/// часть 1 новым заголовком было бы потерей уже записанных суток.
pub fn day_file_path(root: &Path, symbol: &str, day: &str, part: u32) -> PathBuf {
    if part <= 1 {
        root.join(format!("{symbol}-{day}.binlog"))
    } else {
        root.join(format!("{symbol}-{day}-p{part}.binlog"))
    }
}

/// `gaps.csv` — один на корень, на все символы и сутки: десятки строк, читает
/// человек (Decision 23: CSV только для метаданных-обочин).
pub fn gaps_csv_path(root: &Path) -> PathBuf {
    root.join("gaps.csv")
}

/// `instruments.csv` — пишет `lob pick`, читает запись (шаги для заголовка).
pub fn instruments_csv_path(root: &Path) -> PathBuf {
    root.join("instruments.csv")
}

// ---------------------------------------------------------------------------
// gaps.csv: шапка всегда, строки только на разрывах.
// ---------------------------------------------------------------------------

/// Причина строки в `gaps.csv`. Сериализуется snake_case и читается назад тем
/// же именем — переименование варианта без `serde(rename)` молча разойдётся
/// со старыми файлами, поэтому имена зафиксированы тестом `gap_row_round_trips`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GapKind {
    /// Смена `tickSize`/`qtyStep`: файл закрыт, открыт следующий, заголовок
    /// нового несёт новые шаги.
    StepChange,
    /// Разрыв `u`: событие пропущено, книга ждёт снапшота.
    SequenceGap,
    /// Пересечение книги или немасштабное нарушение инварианта.
    BookInvariant,
    /// Кадр транспорта не разобрался: событие потеряно до книги.
    ParseError,
    /// Свободное место упало ниже `STOP_FREE_BYTES_REQUIRED`: остановка.
    LowSpace,
}

/// Одна строка `gaps.csv`. Колонки: момент (UTC, RFC 3339), символ, причина,
/// человекочитаемая деталь (какое поле, какие значения, какие пороги).
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct GapRow {
    pub ts_utc: String,
    pub symbol: String,
    pub kind: GapKind,
    pub detail: String,
}

/// Шапка `gaps.csv` — те же имена и в том же порядке, что поля `GapRow`.
/// Ручная запись, а не `serialize` пустой строки: `csv` пишет шапку только на
/// первом `serialize`, а файл с нулём строк обязан шапку уже нести
/// (done-condition 0.3: «`gaps.csv` с нулём строк» — это шапка без данных,
/// а не отсутствующий файл). Дрейф имён ловит `gap_row_round_trips`.
const GAPS_HEADER: [&str; 4] = ["ts_utc", "symbol", "kind", "detail"];

/// Создаёт `gaps.csv` с шапкой, если его нет или он пуст. Существующий
/// непустой файл не трогает — часовой замер не имеет права терять уже
/// записанные разрывы (тот же приём, что `clock::append_row`).
pub fn ensure_gaps_csv(path: &Path) -> Result<(), RecordError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let needs_header = std::fs::metadata(path)
        .map(|m| m.len() == 0)
        .unwrap_or(true);
    if needs_header {
        // `truncate(false)` явно: файл здесь либо отсутствует, либо нулевой
        // длины (проверено выше) — усекать нечего, и это зафиксировано
        // вызовом, а не умолчанием `OpenOptions`.
        let file = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(false)
            .open(path)?;
        let mut w = csv::WriterBuilder::new()
            .has_headers(false)
            .from_writer(file);
        w.write_record(GAPS_HEADER)?;
        w.flush()?;
    }
    Ok(())
}

/// Дописывает строку. Шапка пишется тем же вызовом, если файла не было, —
/// вызывающему не нужно помнить про `ensure_gaps_csv` отдельно.
pub fn append_gap_row(path: &Path, row: &GapRow) -> Result<(), RecordError> {
    ensure_gaps_csv(path)?;
    let file = OpenOptions::new().create(true).append(true).open(path)?;
    let mut w = csv::WriterBuilder::new()
        .has_headers(false)
        .from_writer(file);
    w.serialize(row)?;
    w.flush()?;
    Ok(())
}

/// Читает все строки. Пустого файла (ноль байт, без шапки) здесь быть не
/// должно после `ensure_gaps_csv`, но если он подсунут напрямую — это ноль
/// строк, а не ошибка разбора: отсутствие данных не есть повреждённые данные.
pub fn read_gap_rows(path: &Path) -> Result<Vec<GapRow>, RecordError> {
    if std::fs::metadata(path)
        .map(|m| m.len() == 0)
        .unwrap_or(true)
    {
        return Ok(Vec::new());
    }
    let mut r = csv::Reader::from_path(path)?;
    r.deserialize::<GapRow>()
        .collect::<Result<Vec<_>, _>>()
        .map_err(RecordError::from)
}

// ---------------------------------------------------------------------------
// Горячий детектор смены шагов: одна целочисленная операция на поле.
// ---------------------------------------------------------------------------

/// Проверяет, что цена кратна сохранённому тику, а размер — сохранённому шагу.
/// Вызывается на уже разобранных `i64` до изменения книги и файла — первое же
/// затронутое событие даёт `Err`, а не тихую запись по неверному масштабу.
/// Ноль аллокаций: только `%` и сравнение.
pub fn check_level_step(
    price_e9: i64,
    qty_e9: i64,
    tick_e9: i64,
    step_e9: i64,
) -> Result<(), StepViolation> {
    if price_e9 % tick_e9 != 0 || qty_e9 % step_e9 != 0 {
        return Err(StepViolation {
            price_e9,
            qty_e9,
            tick_e9,
            step_e9,
        });
    }
    Ok(())
}

/// Флаг события книги по стороне и виду кадра — те же биты, что использует
/// экспортёр (шаг 6.1) и тесты `binlog`: снапшот несёт бит `DEPTH_SNAPSHOT`,
/// дельта — `DEPTH`, сторона — `BUY` (бид) / `SELL` (аск), всё — `LOCAL`.
fn depth_ev(side: Side, snapshot: bool) -> u64 {
    use hftbacktest::types::{
        LOCAL_ASK_DEPTH_EVENT, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_EVENT,
        LOCAL_BID_DEPTH_SNAPSHOT_EVENT,
    };
    match (side, snapshot) {
        (Side::Bid, false) => LOCAL_BID_DEPTH_EVENT,
        (Side::Ask, false) => LOCAL_ASK_DEPTH_EVENT,
        (Side::Bid, true) => LOCAL_BID_DEPTH_SNAPSHOT_EVENT,
        (Side::Ask, true) => LOCAL_ASK_DEPTH_SNAPSHOT_EVENT,
    }
}

// ---------------------------------------------------------------------------
// Recorder: файл суток, батч кадров, gaps.csv. Книгой не владеет.
// ---------------------------------------------------------------------------

/// Пишет одни сутки (одну часть суток): заголовок один раз при открытии,
/// дальше кадры из переиспользуемого батча. Книга живёт у вызывающего
/// (`run_record`): ей нужен `tokio` и сокет рядом, а этому — только файл;
/// граница та же, что `ARCHITECTURE.md` A3 проводит между потоком решений и
/// вводом-выводом, только книга здесь передаётся параметром, а не каналом.
pub struct Recorder {
    root: PathBuf,
    symbol: String,
    tick_e9: i64,
    step_e9: i64,
    day: String,
    /// Индекс `day` для горячего пути (см. `day_index_of_day_str`).
    day_index: i64,
    part: u32,
    gaps: PathBuf,
    writer: crate::binlog::Writer<File>,
    /// Переиспользуемый батч: ёмкость `FRAME_TARGET_RECORDS` выделяется один
    /// раз при открытии, дальше только `push`/`clear` — это и есть половина
    /// бюджета «ноль после прогрева» со стороны рекордера (вторая — сам
    /// `Writer`, см. его doc).
    batch: Vec<Record>,
    frames_in_file: u64,
    has_snapshot: bool,
    records_in_file: u64,
    records_total: u64,
}

fn open_day_writer(
    path: &Path,
    tick_e9: i64,
    step_e9: i64,
) -> Result<crate::binlog::Writer<File>, RecordError> {
    let file = File::create(path)?;
    let header = Header {
        tick_e9,
        step_e9,
        max_records_per_frame: MAX_RECORDS_PER_FRAME,
    };
    crate::binlog::Writer::create(file, header, ZSTD_LEVEL).map_err(RecordError::from)
}

/// Первая свободная часть суток начиная с `from`: часть 1, если файла нет
/// (обычный случай), иначе `-p2`, `-p3`, … — перезапись занятого имени была
/// бы потерей данных.
fn claim_part(
    root: &Path,
    symbol: &str,
    day: &str,
    from: u32,
    tick_e9: i64,
    step_e9: i64,
) -> Result<(crate::binlog::Writer<File>, u32), RecordError> {
    let mut part = from.max(1);
    loop {
        let path = day_file_path(root, symbol, day, part);
        if !path.exists() {
            return Ok((open_day_writer(&path, tick_e9, step_e9)?, part));
        }
        part = part.checked_add(1).ok_or(RecordError::TooManyParts {
            day: day.to_string(),
        })?;
    }
}

impl Recorder {
    /// Открывает запись части суток: каталог, `gaps.csv` с шапкой, файл с
    /// заголовком (`tick_e9`/`step_e9` — те же значения, что `Book` и разбор
    /// `bybit::ws` уже используют, A1). Первый кадр обязан быть снапшотом
    /// (`on_snapshot`) — до него любое staged-событие это `NoSnapshot`.
    pub fn open(
        root: &Path,
        symbol: &str,
        tick_e9: i64,
        step_e9: i64,
        day: &str,
    ) -> Result<Self, RecordError> {
        validate_steps(tick_e9, step_e9)?;
        std::fs::create_dir_all(root)?;
        let gaps = gaps_csv_path(root);
        ensure_gaps_csv(&gaps)?;
        let (writer, part) = claim_part(root, symbol, day, 1, tick_e9, step_e9)?;
        let day_index = day_index_of_day_str(day)?;
        Ok(Self {
            root: root.to_path_buf(),
            symbol: symbol.to_string(),
            tick_e9,
            step_e9,
            day: day.to_string(),
            day_index,
            part,
            gaps,
            writer,
            batch: Vec::with_capacity(FRAME_TARGET_RECORDS),
            frames_in_file: 0,
            has_snapshot: false,
            records_in_file: 0,
            records_total: 0,
        })
    }

    pub fn symbol(&self) -> &str {
        &self.symbol
    }
    pub fn day(&self) -> &str {
        &self.day
    }
    pub fn day_index(&self) -> i64 {
        self.day_index
    }
    pub fn part(&self) -> u32 {
        self.part
    }
    pub fn tick_e9(&self) -> i64 {
        self.tick_e9
    }
    pub fn step_e9(&self) -> i64 {
        self.step_e9
    }
    pub fn gaps_path(&self) -> &Path {
        &self.gaps
    }
    pub fn current_file(&self) -> PathBuf {
        day_file_path(&self.root, &self.symbol, &self.day, self.part)
    }
    pub fn frames_in_file(&self) -> u64 {
        self.frames_in_file
    }
    pub fn records_in_file(&self) -> u64 {
        self.records_in_file
    }
    pub fn records_total(&self) -> u64 {
        self.records_total
    }

    /// Пишет накопленный батч кадром. Пустой батч — no-op (нулевая запись не
    /// несёт события и не порождает кадр — тот же контракт, что у
    /// `binlog::Writer::write_frame`).
    fn flush_frame(&mut self) -> Result<(), RecordError> {
        if self.batch.is_empty() {
            return Ok(());
        }
        if !self.has_snapshot {
            return Err(RecordError::NoSnapshot);
        }
        self.writer.write_frame(&self.batch)?;
        self.frames_in_file += 1;
        let n = self.batch.len() as u64;
        self.records_in_file += n;
        self.records_total += n;
        self.batch.clear();
        Ok(())
    }

    /// Кадр на диск и файл на диск. Граница потери при крахе — последний
    /// `flush`: hourly-таймер и ротации вызывают его всегда.
    pub fn flush(&mut self) -> Result<(), RecordError> {
        self.flush_frame()?;
        self.writer.flush()?;
        Ok(())
    }

    fn push_record(&mut self, r: Record) -> Result<(), RecordError> {
        if self.batch.len() >= FRAME_TARGET_RECORDS {
            self.flush_frame()?;
        }
        self.batch.push(r);
        Ok(())
    }

    /// Первый кадр файла — синтетический полный снапшот (Decision 7): по одной
    /// записи на уровень книги после применения снапшотного `Update`.
    /// Серединные снапшоты (`u = 1`, рестарт сервиса) идут тем же путём:
    /// висящий батч дельт закрывается кадром первым, затем кадром — снапшот.
    pub fn on_snapshot(
        &mut self,
        book: &Book,
        exch_ts_ns: i64,
        local_ts_ns: i64,
    ) -> Result<(), RecordError> {
        self.flush_frame()?;
        // Батч пуст (только что сброшен или ещё ничего не staged — staged до
        // снапшота запрещён), уровней не больше 2 × CAPACITY книги (128 × 2 =
        // 256 < FRAME_TARGET_RECORDS): кадр снапшота пишется целиком, одним
        // вызовом ниже, без промежуточных сбросов и без роста батча.
        for side in [Side::Bid, Side::Ask] {
            for (tick, lots) in book.levels(side) {
                self.batch.push(Record {
                    ev: depth_ev(side, true),
                    exch_ts_ns,
                    local_ts_ns,
                    price_ticks: tick,
                    qty_lots: lots,
                    order_id: 0,
                    ival: 0,
                    fval: 0.0,
                });
            }
        }
        self.has_snapshot = true;
        self.flush_frame()?;
        Ok(())
    }

    /// Одна запись на изменённый уровень (Decision 23), не одно WS-сообщение.
    /// Порядок жёсткий: сначала горячий детектор на всех уровнях (нарушение
    /// не трогает ни книгу, ни файл), затем `Book::apply` (разрыв и
    /// пересечение — в `gaps.csv` у вызывающего, без ротации), затем staging.
    /// Возвращает число поставленных в батч записей.
    pub fn stage_book_update(
        &mut self,
        book: &mut Book,
        update: &Update,
        exch_ts_ns: i64,
        local_ts_ns: i64,
    ) -> Result<usize, RecordError> {
        if !self.has_snapshot {
            return Err(RecordError::NoSnapshot);
        }
        for &(price_e9, qty_e9) in update.bids.iter().chain(update.asks.iter()) {
            check_level_step(price_e9, qty_e9, self.tick_e9, self.step_e9)
                .map_err(RecordError::from)?;
        }
        book.apply(update).map_err(|e| match e {
            // Недостижимо при текущем порядке проверок (шаги уже проверены
            // выше теми же значениями), но обрабатывается той же веткой, что
            // и прямой детектор, — а не `unreachable!`, который лёг бы паникой
            // в процесс, рассчитанный на недели без присмотра.
            crate::book::ApplyError::PriceNotOnTick { price_e9, tick_e9 } => RecordError::Step {
                price_e9,
                qty_e9: 0,
                tick_e9,
                step_e9: self.step_e9,
            },
            crate::book::ApplyError::QtyNotOnStep { qty_e9, step_e9 } => RecordError::Step {
                price_e9: 0,
                qty_e9,
                tick_e9: self.tick_e9,
                step_e9,
            },
            crate::book::ApplyError::SequenceGap { expected, got } => {
                RecordError::SequenceGap { expected, got }
            }
            crate::book::ApplyError::Crossed {
                best_bid_tick,
                best_ask_tick,
            } => RecordError::Crossed {
                best_bid_tick,
                best_ask_tick,
            },
        })?;
        let mut n = 0;
        for &(price_e9, qty_e9) in &update.bids {
            self.push_record(Record {
                ev: depth_ev(Side::Bid, false),
                exch_ts_ns,
                local_ts_ns,
                price_ticks: price_e9 / self.tick_e9,
                qty_lots: qty_e9 / self.step_e9,
                order_id: 0,
                ival: 0,
                fval: 0.0,
            })?;
            n += 1;
        }
        for &(price_e9, qty_e9) in &update.asks {
            self.push_record(Record {
                ev: depth_ev(Side::Ask, false),
                exch_ts_ns,
                local_ts_ns,
                price_ticks: price_e9 / self.tick_e9,
                qty_lots: qty_e9 / self.step_e9,
                order_id: 0,
                ival: 0,
                fval: 0.0,
            })?;
            n += 1;
        }
        Ok(n)
    }

    /// Сделка ленты — одна запись. `ival = 1` помечает блочную (`BT`):
    /// такие не потребляют видимую ликвидность (Decision 5), но пишутся как
    /// сырые события (Decision 7) — разметка отфильтрует их сама по `ival`.
    /// `order_id = 0`: у публичного потока нет числового идентификатора
    /// заявки (`i` — строка), и выдумывать его из хеша значило бы писать
    /// в лог значение, которого биржа не сообщала.
    pub fn stage_trade(&mut self, trade: &Trade, local_ts_ns: i64) -> Result<(), RecordError> {
        if !self.has_snapshot {
            return Err(RecordError::NoSnapshot);
        }
        check_level_step(trade.price_e9, trade.qty_e9, self.tick_e9, self.step_e9)
            .map_err(RecordError::from)?;
        let ev = if trade.aggressor_is_buy {
            hftbacktest::types::LOCAL_BUY_TRADE_EVENT
        } else {
            hftbacktest::types::LOCAL_SELL_TRADE_EVENT
        };
        // `saturating_mul`: миллисекунды биржи в наносекунды лога. Реальные
        // значения далеки от переполнения, но метка приходит из сети, а паника
        // в горячем пути гасит недели записи ради одного кривого сообщения.
        let exch_ts_ns = trade.exch_ms.saturating_mul(1_000_000);
        self.push_record(Record {
            ev,
            exch_ts_ns,
            local_ts_ns,
            price_ticks: trade.price_e9 / self.tick_e9,
            qty_lots: trade.qty_e9 / self.step_e9,
            order_id: 0,
            ival: i64::from(trade.block),
            fval: 0.0,
        })?;
        Ok(())
    }

    /// Ротация по смене шагов: недописанный батч — кадром в старый файл,
    /// строка `step_change` в `gaps.csv`, новый файл (та же дата, следующая
    /// часть) с новым заголовком. Старый файл остаётся валидным: он
    /// заканчивается между кадрами, и читается с первого байта без нового.
    /// Снапшот нового файла — следующим `on_snapshot` от вызывающего.
    pub fn rotate_on_step_change(
        &mut self,
        new_tick_e9: i64,
        new_step_e9: i64,
        ts_utc: &str,
        detail: &str,
    ) -> Result<(), RecordError> {
        validate_steps(new_tick_e9, new_step_e9)?;
        self.flush()?;
        append_gap_row(
            &self.gaps,
            &GapRow {
                ts_utc: ts_utc.to_string(),
                symbol: self.symbol.clone(),
                kind: GapKind::StepChange,
                detail: detail.to_string(),
            },
        )?;
        let (writer, part) = claim_part(
            &self.root,
            &self.symbol,
            &self.day,
            self.part + 1,
            new_tick_e9,
            new_step_e9,
        )?;
        self.writer = writer;
        self.part = part;
        self.tick_e9 = new_tick_e9;
        self.step_e9 = new_step_e9;
        self.batch = Vec::with_capacity(FRAME_TARGET_RECORDS);
        self.frames_in_file = 0;
        self.records_in_file = 0;
        self.has_snapshot = false;
        Ok(())
    }

    /// Ротация по суткам UTC: недописанный батч — кадром в старые сутки,
    /// новый файл с тем же заголовком (шаги не менялись — это не
    /// `rotate_on_step_change`, и строки в `gaps.csv` не пишется: полночь —
    /// не разрыв). Снапшот новых суток — следующим `on_snapshot`.
    pub fn ensure_day(&mut self, day: &str) -> Result<bool, RecordError> {
        if day == self.day {
            return Ok(false);
        }
        self.flush()?;
        let (writer, part) =
            claim_part(&self.root, &self.symbol, day, 1, self.tick_e9, self.step_e9)?;
        self.writer = writer;
        self.day = day.to_string();
        self.day_index = day_index_of_day_str(day)?;
        self.part = part;
        self.batch = Vec::with_capacity(FRAME_TARGET_RECORDS);
        self.frames_in_file = 0;
        self.records_in_file = 0;
        self.has_snapshot = false;
        Ok(true)
    }

    /// Строка в `gaps.csv` от имени текущей записи: символ подставляется сам,
    /// вызывающему остаётся момент, причина и деталь. Для разрывов
    /// последовательности, пересечений книги и остановки по месту —
    /// везде, где файл не ротируется, но молчать нельзя.
    pub fn log_gap(&self, kind: GapKind, ts_utc: &str, detail: &str) -> Result<(), RecordError> {
        append_gap_row(
            &self.gaps,
            &GapRow {
                ts_utc: ts_utc.to_string(),
                symbol: self.symbol.clone(),
                kind,
                detail: detail.to_string(),
            },
        )
    }
}

// ---------------------------------------------------------------------------
// Шаги из instruments.csv (пишет `lob pick`, шаг 0.4).
// ---------------------------------------------------------------------------

/// Строка `instruments.csv` — только колонки, нужные записи. Остальные
/// (`min_order_qty`, `min_notional_value`, …) игнорируются разбором, но их
/// наличие в файле обязательно: файл читается по именам заголовков, и
/// перепутанные колонки дали бы чужие шаги молча — от этого страхует тест
/// `steps_come_from_the_right_columns`, где все четыре числа различны.
#[derive(serde::Deserialize)]
struct InstrumentStepsRow {
    symbol: String,
    tick_size: String,
    qty_step: String,
}

/// `(tick_e9, step_e9)` символа из `instruments.csv`. Тот же масштаб 1e-9 и
/// тот же разбор `parse_e9`, что книга и живой поток (A1) — три независимых
/// масштаба здесь разошлись бы при переносе константы.
pub fn load_steps_for_symbol(
    instruments_csv: &Path,
    symbol: &str,
) -> Result<(i64, i64), RecordError> {
    let mut r = csv::Reader::from_path(instruments_csv)?;
    for row in r.deserialize::<InstrumentStepsRow>() {
        let row: InstrumentStepsRow = row?;
        if row.symbol != symbol {
            continue;
        }
        let tick_e9 = crate::bybit::ws::parse_e9(row.tick_size.trim()).ok_or_else(|| {
            RecordError::Steps(format!("{symbol}: tick_size не разобрался как число"))
        })?;
        let step_e9 = crate::bybit::ws::parse_e9(row.qty_step.trim()).ok_or_else(|| {
            RecordError::Steps(format!("{symbol}: qty_step не разобрался как число"))
        })?;
        validate_steps(tick_e9, step_e9)?;
        return Ok((tick_e9, step_e9));
    }
    Err(RecordError::Steps(format!(
        "{symbol}: нет в {} — сначала `lob pick`",
        instruments_csv.display()
    )))
}

// ---------------------------------------------------------------------------
// Свободное место: предстартовый запрет и остановка с записью в gaps.csv.
// ---------------------------------------------------------------------------

/// Предстартовый гейт GC: свободно меньше `START_FREE_BYTES_REQUIRED` —
/// старт запрещён явной ошибкой, а не надеждой, что места хватит.
pub fn check_start_free_space(free_bytes: u64) -> Result<(), RecordError> {
    if free_bytes < START_FREE_BYTES_REQUIRED {
        return Err(RecordError::SpaceDenied {
            free_bytes,
            required_bytes: START_FREE_BYTES_REQUIRED,
        });
    }
    Ok(())
}

/// Часовой сэмпл: свободно ниже `STOP_FREE_BYTES_REQUIRED` — остановка с
/// записью `low_space` в `gaps.csv`. Строго ниже: ровно на границе запись
/// ещё продолжается.
pub fn should_stop_on_free_space(free_bytes: u64) -> bool {
    free_bytes < STOP_FREE_BYTES_REQUIRED
}

/// Источник свободного места. Трейт, а не прямой вызов ОС — по той же причине,
/// что `Clock` в `bybit::conn`: гейты проверяются сценарными числами без
/// настоящего диска (см. тесты ниже), а ОС вызывается только в бою.
pub trait FreeSpaceCheck {
    fn free_bytes(&self, dir: &Path) -> io::Result<u64>;
}

/// Свободное место от ОС, без новых зависимостей: прямых FFI-вызовов хватает —
/// `GetDiskFreeSpaceExW` на Windows, `statvfs` на Linux (единственная боевая
/// платформа по H12 — VPS; dev-платформа — Windows). Другим Unix — честная
/// ошибка «не поддерживается», а не выдуманное число.
pub struct OsFreeSpaceCheck;

#[cfg(windows)]
mod os_space {
    use super::Path;
    use std::io;
    use std::os::windows::ffi::OsStrExt;

    #[link(name = "kernel32")]
    extern "system" {
        fn GetDiskFreeSpaceExW(
            dir: *const u16,
            free_to_caller: *mut u64,
            total: *mut u64,
            total_free: *mut u64,
        ) -> i32;
    }

    pub(super) fn free_bytes(dir: &Path) -> io::Result<u64> {
        let wide: Vec<u16> = dir
            .as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();
        let mut free: u64 = 0;
        // SAFETY: `wide` — NUL-терминированный UTF-16, живёт до конца вызова;
        // `&mut free` — валидный указатель на запись; остальные out-параметры
        // не нужны и передаются NULL, что API разрешает явно.
        let ok = unsafe {
            GetDiskFreeSpaceExW(
                wide.as_ptr(),
                &mut free,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
            )
        };
        if ok == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(free)
    }
}

#[cfg(target_os = "linux")]
mod os_space {
    use super::Path;
    use std::ffi::CString;
    use std::io;
    use std::os::raw::c_char;
    use std::os::unix::ffi::OsStrExt;

    /// Подмножество `struct statvfs` glibc, достаточное для свободного места.
    /// Порядок и ширина первых пяти полей — часть стабильного ABI Linux.
    #[repr(C)]
    struct Statvfs {
        f_bsize: u64,
        f_frsize: u64,
        f_blocks: u64,
        f_bfree: u64,
        f_bavail: u64,
    }

    extern "C" {
        fn statvfs(path: *const c_char, buf: *mut Statvfs) -> i32;
    }

    pub(super) fn free_bytes(dir: &Path) -> io::Result<u64> {
        let c = CString::new(dir.as_os_str().as_bytes())
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "путь содержит NUL-байт"))?;
        let mut st = std::mem::MaybeUninit::<Statvfs>::uninit();
        // SAFETY: `c` — валидная C-строка до конца вызова; `st` — выделенная,
        // но неинициализированная память ровно под `Statvfs`, которую `statvfs`
        // целиком записывает при возврате 0. Читаем её только в этом случае.
        let rc = unsafe { statvfs(c.as_ptr(), st.as_mut_ptr()) };
        if rc != 0 {
            return Err(io::Error::last_os_error());
        }
        let st = unsafe { st.assume_init() };
        Ok(st.f_bavail.saturating_mul(st.f_frsize))
    }
}

#[cfg(not(any(windows, target_os = "linux")))]
mod os_space {
    use super::Path;
    use std::io;

    pub(super) fn free_bytes(dir: &Path) -> io::Result<u64> {
        Err(io::Error::new(
            io::ErrorKind::Unsupported,
            format!(
                "свободное место не измеряется на этой платформе: {}",
                dir.display()
            ),
        ))
    }
}

impl FreeSpaceCheck for OsFreeSpaceCheck {
    fn free_bytes(&self, dir: &Path) -> io::Result<u64> {
        os_space::free_bytes(dir)
    }
}

// ---------------------------------------------------------------------------
// Живой контур: сокет → книга → файл. Тонкая оболочка (как `run_pick` в
// `lob.rs`): вся проверяемая логика выше — в чистых функциях и `Recorder`,
// здесь только сеть, часы и ожидание. Без сети не тестируется по той же
// причине, по которой её нельзя устроить в CI.
// ---------------------------------------------------------------------------

/// Аргументы `lob record`. Символ обязан пройти `lob pick` заранее: шаги для
/// заголовка берутся из `<root>/instruments.csv` (зависимость 0.3 от 0.4).
#[derive(Debug, Args)]
pub struct RecordArgs {
    /// Инструмент, например `SOLUSDT`.
    #[arg(long)]
    pub symbol: String,
    /// Корень записи: суточные файлы, `gaps.csv`, `instruments.csv`.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// REST-хост Bybit v5 для часового перечитывания шагов.
    #[arg(long, default_value = BYBIT_MAINNET_URL)]
    pub base_url: String,
}

/// Причина остановки записи — печатается в итоге и уходит в `runs.csv` как
/// долг/факт (шаг ведёт учёт остановок с причиной).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StopReason {
    /// Свободное место ниже `STOP_FREE_BYTES_REQUIRED`, строка `low_space`
    /// уже в `gaps.csv`.
    LowSpace,
    /// `Ctrl-C`: файлы сброшены, сутки читаемы (заканчиваются между кадрами).
    Interrupted,
    /// Канал от сокета закрылся — `Connection::run` сам не возвращается,
    /// поэтому это всегда дефект, а не норма.
    ChannelClosed,
}

/// Итог `lob record`: что записано, чем закончилось.
pub struct RecordSummary {
    pub symbol: String,
    pub records_total: u64,
    pub gaps: Vec<GapRow>,
    pub files: Vec<PathBuf>,
    pub stop_reason: StopReason,
}

/// Чем закончилась одна сессия сокета. `StepChange` несёт авторитетные шаги:
/// вызывающий перезапускает соединение с новым `ConnConfig` и свежей книгой,
/// файл уже повёрнут самим `Recorder`.
enum SessionEnd {
    StepChange { tick_e9: i64, step_e9: i64 },
    Stop { reason: StopReason },
}

/// Авторитетные шаги из `instruments-info` (холодный детектор). Ошибка сети
/// или отсутствие символа — это `Steps`, а не паника: часовой авторитет
/// переживает её и пробует снова через час.
fn refresh_steps<R: PublicRest>(rest: &mut R, symbol: &str) -> Result<(i64, i64), RecordError> {
    let instruments = fetch_all_linear_instruments(rest)
        .map_err(|e| RecordError::Steps(format!("instruments-info: {e}")))?;
    let inst = instruments
        .iter()
        .find(|i| i.symbol == symbol)
        .ok_or_else(|| RecordError::Steps(format!("{symbol}: нет в instruments-info")))?;
    validate_steps(inst.tick_e9, inst.qty_step_e9)?;
    Ok((inst.tick_e9, inst.qty_step_e9))
}

/// Горячий путь будит авторитет, не дожидаясь HTTP (ремонт 0.7 Р1).
/// `try_send` на канале ёмкостью 1: полный канал — это уже pending
/// пробуждение, второе не нужно; закрытый — авторитет умер, ждать некого.
/// Вызов не блокируется никогда — это и держит «ноль HTTP в событийном пути».
fn request_steps_refresh(wake_tx: &std::sync::mpsc::SyncSender<()>) {
    let _ = wake_tx.try_send(());
}

/// Один цикл авторитета: fetch → `mpsc`, затем ожидание до часа или
/// пробуждения. Общая часть боевого и тестового спавна (ремонт 0.7 Р2) —
/// источник шагов за трейтом, а не жёсткий `BybitPublicRest` в теле цикла.
fn run_steps_authority_loop<R: PublicRest>(
    rest: &mut R,
    symbol: &str,
    steps_tx: std::sync::mpsc::Sender<(i64, i64)>,
    wake_rx: std::sync::mpsc::Receiver<()>,
) {
    loop {
        match refresh_steps(rest, symbol) {
            Ok(steps) => {
                if steps_tx.send(steps).is_err() {
                    break;
                }
            }
            Err(e) => {
                eprintln!("record: instruments-info недоступен ({e}), повтор через час");
            }
        }
        match wake_rx.recv_timeout(Duration::from_secs(HOURLY_REFRESH_SECS)) {
            Ok(()) => while wake_rx.try_recv().is_ok() {},
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }
}

/// Часовой авторитет шагов в ОС-потоке (шаг 0.7, Decision 24).
/// Отдельный `std::thread` (НЕ tokio-задача) со своим `BybitPublicRest`:
/// цикл fetch → `mpsc` → `recv_timeout` 1h/пробуждение. Внутри ОС-потока
/// `block_on` легален — чужого рантайма там нет, поэтому вложенный рантайм
/// невозможен. Цикл записи никогда не ждёт HTTP: он только толкает
/// `try_send` в канал-будильник и дренирует готовое из канала шагов.
fn spawn_steps_authority(
    base_url: String,
    symbol: String,
    wake_rx: std::sync::mpsc::Receiver<()>,
) -> std::sync::mpsc::Receiver<(i64, i64)> {
    let (tx, rx) = std::sync::mpsc::channel::<(i64, i64)>();
    let spawned = std::thread::Builder::new()
        .name("steps-authority".to_string())
        .spawn(move || {
            let mut rest = match BybitPublicRest::new(base_url) {
                Ok(r) => r,
                Err(e) => {
                    eprintln!("record: авторитет шагов не создался ({e})");
                    return;
                }
            };
            run_steps_authority_loop(&mut rest, &symbol, tx, wake_rx);
        });
    if let Err(e) = spawned {
        eprintln!("record: авторитет шагов не запустился ({e})");
    }
    rx
}

/// Тестовый спавн того же цикла с фейковым источником шагов (ремонт 0.7 Р2).
/// Боевой код его не зовёт — только тесты стыка «подозрение → авторитет →
/// ротация» без сети.
#[cfg(test)]
fn spawn_steps_authority_with_rest<R: PublicRest + Send + 'static>(
    rest: R,
    symbol: String,
    wake_rx: std::sync::mpsc::Receiver<()>,
) -> std::sync::mpsc::Receiver<(i64, i64)> {
    let (tx, rx) = std::sync::mpsc::channel::<(i64, i64)>();
    let spawned = std::thread::Builder::new()
        .name("steps-authority-test".to_string())
        .spawn(move || {
            let mut rest = rest;
            run_steps_authority_loop(&mut rest, &symbol, tx, wake_rx);
        });
    if let Err(e) = spawned {
        eprintln!("record: тестовый авторитет не запустился ({e})");
    }
    rx
}

/// Решение по горячему подозрению на последнем известном авторитете
/// (ремонт 0.7 Р2): свежие шаги отличаются — ротация файла со строкой
/// `step_change`, совпадают или свежести нет — строка подавления
/// `book_invariant` (первая на часть файла, дальше счётчик у вызывающего).
/// Возвращает `Some` новых шагов при ротации, `None` при подавлении.
/// Тот же код зовёт и цикл записи, и тесты стыка — шов один, а не два.
fn resolve_suspicion(
    rec: &mut Recorder,
    latest: Option<(i64, i64)>,
    ts_utc: &str,
    detail: &str,
    logged: &mut bool,
    suppressed: &mut u64,
) -> Result<Option<(i64, i64)>, RecordError> {
    if let Some((new_tick, new_step)) = latest {
        if new_tick != rec.tick_e9() || new_step != rec.step_e9() {
            rec.rotate_on_step_change(new_tick, new_step, ts_utc, detail)?;
            return Ok(Some((new_tick, new_step)));
        }
    }
    if !*logged {
        rec.log_gap(GapKind::BookInvariant, ts_utc, detail)?;
        *logged = true;
    }
    *suppressed = suppressed.saturating_add(1);
    Ok(None)
}

/// Дрен канала авторитета: забирает всё, возвращает только последнее.
/// `None` — свежести нет (авторитет ещё не ответил или недоступен):
/// вызывающий идёт в ветку подавления, а не ждёт сеть.
fn drain_latest_steps(rx: &mut std::sync::mpsc::Receiver<(i64, i64)>) -> Option<(i64, i64)> {
    let mut latest = None;
    while let Ok(v) = rx.try_recv() {
        latest = Some(v);
    }
    latest
}

/// Время матчинга события в миллисекундах — ось ротации по суткам UTC и ключ
/// склейки с лентой (Decision 5: `cts` стакана против `T` трейдов). `Other`
/// времени не несёт и сутки не двигает.
fn event_exch_ms(event: &Event) -> Option<i64> {
    match event {
        Event::Book(u) => Some(u.cts_ms),
        Event::Trade(t) => Some(t.exch_ms),
        Event::Other => None,
    }
}

/// Одна сессия сокета: читает канал, ведёт книгу, пишет файл. Возвращается
/// только на ротацию шагов (перезапуск соединения снаружи), остановку или
/// Ctrl-C — сама по себе запись открыта и бесконечна (Decision 21).
/// REST здесь нет: авторитет шагов приходит готовым по `steps_rx` из
/// ОС-потока (шаг 0.7, Decision 24) — событийный путь HTTP не ждёт никогда.
/// Та же дисциплина у сверки (шаг 0.8, ремонт Р1/Р2): сессия форвардит КАЖДОЕ
/// обработанное `Update` в `verify_tx` через `try_send` (дроп+счётчик при
/// переполнении), а REST-снапшот и `verify.csv` — дело ОС-потока сайдкара
/// `bybit::verify_sidecar` со своим соединением. Тикера здесь нет: тикает один
/// сайдкар сном 300с в своём потоке.
#[allow(clippy::too_many_lines, clippy::too_many_arguments)]
async fn run_session(
    rec: &mut Recorder,
    steps_rx: &mut std::sync::mpsc::Receiver<(i64, i64)>,
    wake_tx: std::sync::mpsc::SyncSender<()>,
    verify_tx: &std::sync::mpsc::SyncSender<VerifyMsg>,
    free_check: &OsFreeSpaceCheck,
    symbol: &str,
    tick_e9: i64,
    step_e9: i64,
) -> Result<SessionEnd, RecordError> {
    let mut book = Book::new(tick_e9, step_e9);
    let mut synced = false;
    // Подавление шторма: расхождение с неизменённым авторитетом пишется
    // первой строкой на часть файла, дальше считается молча до часового тика,
    // который дописывает итог. Иначе каждое событие outage давало бы строку и
    // запись в файл на событие — ровно та нагрузка, от которой gaps.csv
    // защищает оговорка «десятки строк, читает человек».
    let mut off_step_logged = false;
    let mut off_step_suppressed: u64 = 0;
    // Счётчик дропов verify-форварда (шаг 0.8, ремонт): переполнение канала —
    // дроп нового обновления, цикл не ждёт никогда.
    let mut verify_skipped: u64 = 0;
    // Новая эпоха для реплики сайдкара (смена шагов инвалидирует её кольцо и
    // базу). Неблокирующе: полный канал означает, что сайдкар и так отстаёт.
    offer_verify_update(
        verify_tx,
        VerifyMsg::Reset { tick_e9, step_e9 },
        &mut verify_skipped,
    );

    let cfg = ConnConfig {
        symbol: symbol.to_string(),
        tick_e9,
        step_e9,
        ping_interval: RECORD_PING_INTERVAL,
        backoff: RECORD_BACKOFF,
    };
    let (tx, mut rx) = tokio::sync::mpsc::channel::<ConnEvent>(CHANNEL_CAPACITY);
    let conn = Connection::new(BybitPublicLinearConnector, cfg);
    let conn_task = tokio::spawn(conn.run(SystemClock, tx));

    let mut hourly = tokio::time::interval(Duration::from_secs(HOURLY_REFRESH_SECS));
    hourly.tick().await;

    // Горячий путь не форматирует дату на событие: индекс дня — целочисленное
    // деление, строка — только на ротации (раз в сутки, не 50 раз в секунду).
    let day_index = |ts_ns: i64| ts_ns.div_euclid(NS_PER_DAY);

    macro_rules! rotate_day_if_needed {
        ($exch_ts_ns:expr) => {
            if day_index($exch_ts_ns) != rec.day_index() {
                let day = day_string_of_ns($exch_ts_ns)?;
                if rec.ensure_day(&day)? && synced {
                    // Книга доверена — новые сутки получают свой снапшот
                    // сразу, а не ждут следующего от биржи.
                    rec.on_snapshot(&book, $exch_ts_ns, SystemClock.now_ns())?;
                }
            }
        };
    }

    // Подтверждение горячего подозрения холодным авторитетом из ОС-потока
    // (шаг 0.7, Decision 24 + ремонт Р1/Р2): сначала `try_send` в
    // канал-будильник без ожидания сети, затем дрен готового без ожидания.
    // Авторитет новее и отличается — ротация; нет свежести или совпадает —
    // ветка подавления с gaps.csv (шторм считается молча).
    macro_rules! confirm_step_change {
        ($ts_utc:expr, $detail:expr) => {{
            request_steps_refresh(&wake_tx);
            let latest = drain_latest_steps(&mut *steps_rx);
            if let Some((new_tick, new_step)) = resolve_suspicion(
                rec,
                latest,
                &$ts_utc,
                &$detail,
                &mut off_step_logged,
                &mut off_step_suppressed,
            )? {
                conn_task.abort();
                return Ok(SessionEnd::StepChange {
                    tick_e9: new_tick,
                    step_e9: new_step,
                });
            }
        }};
    }

    loop {
        tokio::select! {
            msg = rx.recv() => {
                let Some(conn_event) = msg else {
                    conn_task.abort();
                    return Ok(SessionEnd::Stop { reason: StopReason::ChannelClosed });
                };
                match conn_event {
                    ConnEvent::Message { local_ts_ns, event } => {
                        if let Some(exch_ms) = event_exch_ms(&event) {
                            let exch_ts_ns = exch_ms.saturating_mul(1_000_000);
                            rotate_day_if_needed!(exch_ts_ns);
                        }
                        match event {
                            Event::Book(update) if update.is_snapshot || update.u == 1 => {
                                match book.apply(&update) {
                                    Ok(()) => {
                                        synced = true;
                                        let exch_ts_ns = update.cts_ms.saturating_mul(1_000_000);
                                        rec.on_snapshot(&book, exch_ts_ns, local_ts_ns)?;
                                    }
                                    Err(e) => {
                                        synced = false;
                                        let ts_utc = ts_utc_of_ns(local_ts_ns);
                                        match &e {
                                            crate::book::ApplyError::PriceNotOnTick { .. }
                                            | crate::book::ApplyError::QtyNotOnStep { .. } => {
                                                let detail = format!("снапшот вне шагов: {e:?}");
                                                confirm_step_change!(ts_utc, detail);
                                            }
                                            _ => {
                                                rec.log_gap(GapKind::BookInvariant, &ts_utc, &format!("снапшот отвергнут: {e:?}"))?;
                                            }
                                        }
                                    }
                                }
                                // Непрерывная реплика сайдкара (0.8 Р1): каждое
                                // обработанное обновление — в канал без ожидания.
                                if !offer_verify_update(
                                    verify_tx,
                                    VerifyMsg::Update(update),
                                    &mut verify_skipped,
                                ) {
                                    eprintln!(
                                        "record: verify-сайдкар не успевает (пропусков: {verify_skipped})"
                                    );
                                }
                            }
                            Event::Book(update) => {
                                let exch_ts_ns = update.cts_ms.saturating_mul(1_000_000);
                                match rec.stage_book_update(&mut book, &update, exch_ts_ns, local_ts_ns) {
                                    Ok(_) => {}
                                    Err(RecordError::Step { price_e9, qty_e9, tick_e9, step_e9 }) => {
                                        let ts_utc = ts_utc_of_ns(local_ts_ns);
                                        let detail = format!(
                                            "price {price_e9} / qty {qty_e9} не на шагах {tick_e9}/{step_e9} (u={})",
                                            update.u
                                        );
                                        confirm_step_change!(ts_utc, detail);
                                    }
                                    Err(RecordError::SequenceGap { expected, got }) => {
                                        synced = false;
                                        rec.log_gap(GapKind::SequenceGap, &ts_utc_of_ns(local_ts_ns), &format!("ждали u={expected}, пришло u={got}"))?;
                                    }
                                    Err(RecordError::Crossed { best_bid_tick, best_ask_tick }) => {
                                        synced = false;
                                        rec.log_gap(GapKind::BookInvariant, &ts_utc_of_ns(local_ts_ns), &format!("книга пересеклась: бид {best_bid_tick} >= аск {best_ask_tick}"))?;
                                    }
                                    Err(RecordError::NoSnapshot) => {
                                        // Файл ждёт снапшота (полночь или ротация
                                        // в разрыве): сброс нормален, не разрыв.
                                    }
                                    Err(e) => return Err(e),
                                }
                                if !offer_verify_update(
                                    verify_tx,
                                    VerifyMsg::Update(update),
                                    &mut verify_skipped,
                                ) {
                                    eprintln!(
                                        "record: verify-сайдкар не успевает (пропусков: {verify_skipped})"
                                    );
                                }
                            }
                            Event::Trade(trade) => {
                                match rec.stage_trade(&trade, local_ts_ns) {
                                    Ok(()) => {}
                                    Err(RecordError::Step { price_e9, qty_e9, tick_e9, step_e9 }) => {
                                        let ts_utc = ts_utc_of_ns(local_ts_ns);
                                        let detail = format!(
                                            "трейд price {price_e9} / qty {qty_e9} не на шагах {tick_e9}/{step_e9}"
                                        );
                                        confirm_step_change!(ts_utc, detail);
                                    }
                                    Err(RecordError::NoSnapshot) => {}
                                    Err(e) => return Err(e),
                                }
                            }
                            Event::Other => {}
                        }
                    }
                    ConnEvent::ParseFailed { local_ts_ns, err } => {
                        // Потеряно неизвестное число событий одного кадра —
                        // шов покрытия, а не тишина.
                        rec.log_gap(GapKind::ParseError, &ts_utc_of_ns(local_ts_ns), &format!("кадр не разобрался: {err:?}"))?;
                    }
                    ConnEvent::SequenceGap { .. } => {
                        // Книга сокета (оракул ресинка, не книга записи)
                        // ушла на ресинк сама; книга записи отметит тот же
                        // разрыв своей строкой на следующей дельте — здесь
                        // только флаг несинхронности, без второй строки.
                        synced = false;
                    }
                    ConnEvent::BookInvariantViolated { .. } => {
                        // То же: решение сокета, не записи. Нарушение шагов
                        // рекордер поймает сам на том же событии (или уже
                        // поймал), пересечение — своей веткой выше.
                        synced = false;
                    }
                    ConnEvent::Disconnected => {
                        synced = false;
                        rec.log_gap(GapKind::SequenceGap, &ts_utc_of_ns(SystemClock.now_ns()), "транспорт переподключился — шов покрытия")?;
                    }
                }
            }
            _ = hourly.tick() => {                // Часовой тик: место, итог подавления, flush. Шаги приходят
                // сами из ОС-потока авторитета — тик их только забирает
                // (шаг 0.7, Decision 24), сеть здесь не ждётся никогда.
                match free_check.free_bytes(&rec.root) {
                    Ok(free) if should_stop_on_free_space(free) => {
                        let ts_utc = ts_utc_of_ns(SystemClock.now_ns());
                        rec.log_gap(GapKind::LowSpace, &ts_utc, &format!("свободно {free} байт — остановка"))?;
                        rec.flush()?;
                        conn_task.abort();
                        return Ok(SessionEnd::Stop { reason: StopReason::LowSpace });
                    }
                    Ok(_) => {}
                    Err(e) => eprintln!("record: место не измерилось ({e}), продолжаем"),
                }
                if off_step_suppressed > 0 {
                    let ts_utc = ts_utc_of_ns(SystemClock.now_ns());
                    rec.log_gap(GapKind::BookInvariant, &ts_utc, &format!("подавлено немасштабных событий за час: {}", off_step_suppressed))?;
                    off_step_suppressed = 0;
                    off_step_logged = false;
                }
                if let Some((new_tick, new_step)) = drain_latest_steps(&mut *steps_rx) {
                    if new_tick != rec.tick_e9() || new_step != rec.step_e9() {
                        let ts_utc = ts_utc_of_ns(SystemClock.now_ns());
                        rec.rotate_on_step_change(new_tick, new_step, &ts_utc, &format!("часовой instruments-info: тик {new_tick}, шаг {new_step}"))?;
                        conn_task.abort();
                        return Ok(SessionEnd::StepChange { tick_e9: new_tick, step_e9: new_step });
                    }
                }
                if let Err(e) = rec.flush() {
                    conn_task.abort();
                    return Err(e);
                }
            }
            _ = tokio::signal::ctrl_c() => {
                rec.flush()?;
                conn_task.abort();
                return Ok(SessionEnd::Stop { reason: StopReason::Interrupted });
            }
        }
    }
}

/// Точка входа `lob record`. Синхронная сигнатура по образцу `run_pick`:
/// свой многопоточный рантайм внутри — до `CHANNEL_CAPACITY` живых чтений
/// сокета и часовой таймер не укладываются в один поток опроса.
pub fn run_record(args: &RecordArgs) -> anyhow::Result<RecordSummary> {
    let runtime = tokio::runtime::Runtime::new()?;
    runtime.block_on(run_record_async(args))
}

async fn run_record_async(args: &RecordArgs) -> anyhow::Result<RecordSummary> {
    let root = args.root.clone();
    std::fs::create_dir_all(&root)?;
    let (tick_e9, step_e9) = load_steps_for_symbol(&instruments_csv_path(&root), &args.symbol)
        .map_err(|e| anyhow::anyhow!("{e}"))?;

    // Предстартовый гейт GC. Неизмеримое место — предупреждение, а не старт
    // вслепую молча и не запрет без причины: на поддерживаемых платформах
    // (Windows/Linux) измерение обязано работать, и его отказ виден в логе.
    let free_check = OsFreeSpaceCheck;
    match free_check.free_bytes(&root) {
        Ok(free) => check_start_free_space(free).map_err(|e| anyhow::anyhow!("{e}"))?,
        Err(e) => eprintln!("record: предстартовое место не измерилось ({e}), продолжаем"),
    }

    let day = day_string_of_ns(SystemClock.now_ns()).map_err(|e| anyhow::anyhow!("{e}"))?;
    let mut rec = Recorder::open(&root, &args.symbol, tick_e9, step_e9, &day)
        .map_err(|e| anyhow::anyhow!("{e}"))?;
    // Шаг 0.7, Decision 24 + ремонт Р1: часовой авторитет живёт в ОС-потоке
    // со своим соединением и каналом-будильником; событийный путь толкает
    // `try_send` и забирает готовое из канала шагов, HTTP не ждёт никогда.
    let (wake_tx, wake_rx) = std::sync::mpsc::sync_channel::<()>(1);
    let mut steps_rx = spawn_steps_authority(args.base_url.clone(), args.symbol.clone(), wake_rx);
    // Шаг 0.8, Decision 24 + ремонт Р2: verify-сайдкар — ОС-поток со своим
    // соединением (как авторитет шагов); сессия форвардит ему каждое Update в
    // `verify_tx` через `try_send` и HTTP не ждёт. Остановка потока — закрытием
    // канала (все отправители дропнуты), abort невозможен и не нужен.
    let verify_tx = spawn_verify_sidecar(
        args.base_url.clone(),
        args.symbol.clone(),
        root.clone(),
        tick_e9,
        step_e9,
    );

    let (mut tick, mut step) = (tick_e9, step_e9);
    let stop_reason = loop {
        match run_session(
            &mut rec,
            &mut steps_rx,
            wake_tx.clone(),
            &verify_tx,
            &free_check,
            &args.symbol,
            tick,
            step,
        )
        .await
        {
            Ok(SessionEnd::StepChange {
                tick_e9: t,
                step_e9: s,
            }) => {
                tick = t;
                step = s;
            }
            Ok(SessionEnd::Stop { reason }) => break reason,
            Err(e) => {
                let _ = rec.flush();
                return Err(anyhow::anyhow!("{e}"));
            }
        }
    };

    let _ = rec.flush();
    let gaps = read_gap_rows(rec.gaps_path()).unwrap_or_default();
    let mut files: Vec<PathBuf> = std::fs::read_dir(&root)?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| {
            p.file_name().and_then(|n| n.to_str()).is_some_and(|n| {
                n.starts_with(&format!("{}-", args.symbol)) && n.ends_with(".binlog")
            })
        })
        .collect();
    files.sort();
    Ok(RecordSummary {
        symbol: args.symbol.clone(),
        records_total: rec.records_total(),
        gaps,
        files,
        stop_reason,
    })
}

/// Наносекунд в сутках UTC. Деление целочисленное — горячий путь сравнивает
/// дни этим же делителем без форматирования строк (см. `run_record`).
pub const NS_PER_DAY: i64 = 86_400 * 1_000_000_000;

/// Календарная дата UTC (`YYYY-MM-DD`) метки в наносекундах. Ошибка вместо
/// паники на внедиапазонной метке: метка приходит из сети/часов, а паника
/// гасит недели записи.
pub fn day_string_of_ns(ts_ns: i64) -> Result<String, RecordError> {
    let secs = ts_ns.div_euclid(1_000_000_000);
    // Остаток доказуемо < 1e9 < u32::MAX по построению `rem_euclid`.
    #[allow(clippy::cast_possible_truncation)]
    let nanos = ts_ns.rem_euclid(1_000_000_000) as u32;
    let dt = chrono::DateTime::<chrono::Utc>::from_timestamp(secs, nanos)
        .ok_or(RecordError::BadTimestamp { ts_ns })?;
    Ok(dt.format("%Y-%m-%d").to_string())
}

/// Момент UTC для `ts_utc` в `gaps.csv` (RFC 3339). Тотальная: на
/// внедиапазонной метке пишет сырые наносекунды с префиксом, а не падает, —
/// строка-деталь не имеет права ронять запись разрыва, которую оформляет.
pub fn ts_utc_of_ns(ts_ns: i64) -> String {
    let secs = ts_ns.div_euclid(1_000_000_000);
    // Тот же доказанный остаток, что в `day_string_of_ns` выше.
    #[allow(clippy::cast_possible_truncation)]
    let nanos = ts_ns.rem_euclid(1_000_000_000) as u32;
    chrono::DateTime::<chrono::Utc>::from_timestamp(secs, nanos)
        .map(|dt| dt.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true))
        .unwrap_or_else(|| format!("nanos:{ts_ns}"))
}

/// Индекс суток UTC (целые дни от эпохи) строки `YYYY-MM-DD`. Нужен горячему
/// пути: сравнение индексов — целочисленное деление без форматирования строк
/// на событие; строка форматируется только на ротации.
pub fn day_index_of_day_str(day: &str) -> Result<i64, RecordError> {
    let date =
        chrono::NaiveDate::parse_from_str(day, "%Y-%m-%d").map_err(|_| RecordError::BadDay {
            day: day.to_string(),
        })?;
    // Дни от эпохи через публичную арифметику дат, а не через приватный
    // счётчик эры: 1970-01-01 даёт ровно 0, что проверяет тест. `expect`
    // здесь невозможен по режиму линтов, поэтому невероятная ветвь
    // (календарь без 1970-01-01) возвращается той же ошибкой, что и кривой
    // вход, — с фиксированной строкой вместо пользовательского ввода.
    let epoch = chrono::NaiveDate::from_ymd_opt(1970, 1, 1).ok_or_else(|| RecordError::BadDay {
        day: "1970-01-01".to_string(),
    })?;
    Ok(date.signed_duration_since(epoch).num_days())
}

#[cfg(test)]
mod tests {
    use super::{
        append_gap_row, check_level_step, day_file_path, drain_latest_steps, ensure_gaps_csv,
        gaps_csv_path, read_gap_rows, request_steps_refresh, resolve_suspicion,
        spawn_steps_authority_with_rest, GapKind, GapRow, Recorder,
    };

    const TICK_E9: i64 = 10_000_000; // 0.01
    const STEP_E9: i64 = 1_000_000; // 0.001
    const DAY: &str = "2026-09-08";

    fn snapshot_update() -> crate::book::Update {
        crate::book::Update {
            is_snapshot: true,
            u: 1,
            seq: 1,
            cts_ms: 1_757_800_000_000,
            bids: vec![(150_000_000_000, 2_500_000_000)],
            asks: vec![(150_010_000_000, 3_000_000_000)],
        }
    }

    fn open_recorder(dir: &tempfile::TempDir) -> Recorder {
        Recorder::open(dir.path(), "SOLUSDT", TICK_E9, STEP_E9, DAY).unwrap()
    }

    fn snapshot_book() -> crate::book::Book {
        let mut book = crate::book::Book::new(TICK_E9, STEP_E9);
        book.apply(&snapshot_update()).unwrap();
        book
    }

    /// Требуемый тест шага 0.3 (`PLAN.md`): цена, не кратная сохранённому
    /// шагу, даёт ротацию и строку в `gaps.csv`, а не молчаливое продолжение.
    #[test]
    fn off_tick_price_rotates_and_leaves_a_gap_row_instead_of_continuing_silently() {
        let dir = tempfile::tempdir().unwrap();
        let mut rec = open_recorder(&dir);
        let mut book = snapshot_book();
        rec.on_snapshot(&book, 1_757_800_000_000_000_000, 1_757_800_000_001_000_000)
            .unwrap();

        // Цена 150.005 не кратна тику 0.01 — горячий детектор обязан отказать,
        // а не записать дельту молча.
        let bad = crate::book::Update {
            is_snapshot: false,
            u: 2,
            seq: 2,
            cts_ms: 1_757_800_000_020,
            bids: vec![(150_005_000_000, 1_000_000_000)],
            asks: vec![],
        };
        assert!(
            rec.stage_book_update(
                &mut book,
                &bad,
                1_757_800_000_020_000_000,
                1_757_800_000_021_000_000
            )
            .is_err(),
            "цена не на тике обязана отвергаться, а не писаться молча"
        );

        // Авторитетное значение из часового перечитывания instruments-info:
        // биржа уполовинила тик, и 150.005 ему кратен.
        rec.rotate_on_step_change(
            5_000_000,
            STEP_E9,
            "2026-09-08T00:00:20Z",
            "price 150.005 not multiple of tick 0.01",
        )
        .unwrap();

        assert_eq!(rec.tick_e9(), 5_000_000, "новый файл несёт новый шаг цены");
        let gaps = read_gap_rows(&gaps_csv_path(dir.path())).unwrap();
        assert_eq!(gaps.len(), 1, "одна ротация — одна строка в gaps.csv");
        assert_eq!(gaps[0].kind, GapKind::StepChange);
    }

    /// Парный тест: валидная дельта пишется молча в ХОРОШЕМ смысле — без
    /// ротации и без строк в gaps.csv. Без него предыдущий тест не отличил бы
    /// «отвергает некратное» от «отвергает всё подряд».
    #[test]
    fn on_tick_delta_is_staged_without_rotation_and_without_gap_rows() {
        let dir = tempfile::tempdir().unwrap();
        let mut rec = open_recorder(&dir);
        let mut book = snapshot_book();
        rec.on_snapshot(&book, 1_757_800_000_000_000_000, 1_757_800_000_001_000_000)
            .unwrap();

        let delta = crate::book::Update {
            is_snapshot: false,
            u: 2,
            seq: 2,
            cts_ms: 1_757_800_000_020,
            bids: vec![(150_000_000_000, 3_000_000_000)],
            asks: vec![],
        };
        let n = rec
            .stage_book_update(
                &mut book,
                &delta,
                1_757_800_000_020_000_000,
                1_757_800_000_021_000_000,
            )
            .unwrap();
        assert_eq!(n, 1, "одна запись на один изменённый уровень (Decision 23)");
        assert_eq!(rec.part(), 1, "ротации не было");
        let gaps = read_gap_rows(&gaps_csv_path(dir.path())).unwrap();
        assert!(
            gaps.is_empty(),
            "нормальная запись не оставляет строк в gaps.csv"
        );
    }

    /// Горячий детектор — чистая функция: ноль и отрицательные значения тоже
    /// обязаны проверяться тем же `%`, а не особым путём.
    #[test]
    fn step_detector_accepts_multiples_and_rejects_everything_else() {
        assert!(check_level_step(150_000_000_000, 1_000_000_000, TICK_E9, STEP_E9).is_ok());
        // Удаление уровня (размер 0) кратно любому шагу — оно обязано проходить.
        assert!(check_level_step(150_000_000_000, 0, TICK_E9, STEP_E9).is_ok());
        let v = check_level_step(150_005_000_000, 1_000_000_000, TICK_E9, STEP_E9).unwrap_err();
        assert_eq!(v.price_e9, 150_005_000_000);
        assert_eq!(v.tick_e9, TICK_E9);
        let v = check_level_step(150_000_000_000, 1_500_000, TICK_E9, STEP_E9).unwrap_err();
        assert_eq!(v.qty_e9, 1_500_000);
    }

    /// Имя части 1 — каноническое, без суффикса; следующие — с `-pN`.
    #[test]
    fn day_file_names_are_canonical_for_part_one_and_suffixed_after() {
        let root = std::path::Path::new("data/bybit");
        assert_eq!(
            day_file_path(root, "SOLUSDT", DAY, 1),
            root.join("SOLUSDT-2026-09-08.binlog")
        );
        assert_eq!(
            day_file_path(root, "SOLUSDT", DAY, 2),
            root.join("SOLUSDT-2026-09-08-p2.binlog")
        );
    }

    /// `gaps.csv` создаётся с шапкой и нулём строк; повторный вызов ничего не
    /// дописывает; строка дописывается и читается назад теми же значениями
    /// (это же фиксирует имена колонок и `GapKind`, от которых зависит чтение
    /// старых файлов).
    #[test]
    fn gap_row_round_trips_and_header_is_stable() {
        let dir = tempfile::tempdir().unwrap();
        let path = gaps_csv_path(dir.path());
        ensure_gaps_csv(&path).unwrap();
        ensure_gaps_csv(&path).unwrap();
        let header = std::fs::read_to_string(&path).unwrap();
        assert_eq!(
            header.trim_end(),
            "ts_utc,symbol,kind,detail",
            "шапка gaps.csv — контракт, не оформление"
        );
        assert!(
            read_gap_rows(&path).unwrap().is_empty(),
            "шапка без данных — ноль строк"
        );

        let row = GapRow {
            ts_utc: "2026-09-08T00:00:20Z".to_string(),
            symbol: "SOLUSDT".to_string(),
            kind: GapKind::StepChange,
            detail: "tick 0.01 -> 0.005".to_string(),
        };
        append_gap_row(&path, &row).unwrap();
        assert_eq!(read_gap_rows(&path).unwrap(), vec![row]);
    }

    /// Дата считается по UTC, а не по локальному часовому поясу хоста:
    /// якоря на эпохе и на отрицательной метке, плюс живая дата из других
    /// тестов этого файла (1_757_800_000 секунд — целое число суток).
    #[test]
    fn day_string_of_ns_uses_utc_not_the_hosts_timezone() {
        assert_eq!(super::day_string_of_ns(0).unwrap(), "1970-01-01");
        assert_eq!(
            super::day_string_of_ns(86_400_000_000_000 - 1).unwrap(),
            "1970-01-01"
        );
        assert_eq!(
            super::day_string_of_ns(86_400_000_000_000).unwrap(),
            "1970-01-02"
        );
        assert_eq!(super::day_string_of_ns(-1).unwrap(), "1969-12-31");
        assert_eq!(
            super::day_string_of_ns(1_757_800_000_000_000_000).unwrap(),
            "2025-09-13"
        );
    }

    /// Полночь UTC закрывает сутки и открывает новые тем же заголовком, без
    /// строки в gaps.csv; новые сутки читаются с первого байта: заголовок +
    /// снапшот первым кадром (Decision 7: сутки самодостаточны).
    #[test]
    fn utc_midnight_rotates_to_a_new_self_sufficient_file() {
        let dir = tempfile::tempdir().unwrap();
        let mut rec = open_recorder(&dir);
        let book = snapshot_book();
        rec.on_snapshot(&book, 1_757_800_000_000_000_000, 1_757_800_000_001_000_000)
            .unwrap();
        rec.flush().unwrap();
        let day_one_file = rec.current_file();

        assert!(!rec.ensure_day(DAY).unwrap(), "те же сутки — не ротация");
        assert_eq!(rec.current_file(), day_one_file);

        assert!(
            rec.ensure_day("2025-09-14").unwrap(),
            "следующие сутки UTC — ротация"
        );
        assert_eq!(rec.day(), "2025-09-14");
        // Снапшот новых суток пишется в новый файл первым кадром.
        rec.on_snapshot(&book, 1_757_808_000_000_000_000, 1_757_808_000_001_000_000)
            .unwrap();
        rec.flush().unwrap();

        let gaps = read_gap_rows(&gaps_csv_path(dir.path())).unwrap();
        assert!(
            gaps.is_empty(),
            "полуночная ротация — не разрыв, строк в gaps.csv нет"
        );

        // Новые сутки читаются с первого байта без старых: заголовок несёт
        // шаги, первый кадр — снапшот тех же уровней.
        let file = std::fs::File::open(rec.current_file()).unwrap();
        let mut reader = crate::binlog::Reader::open(file).unwrap();
        assert_eq!(reader.header().tick_e9, TICK_E9);
        assert_eq!(reader.header().step_e9, STEP_E9);
        assert_eq!(
            reader.header().max_records_per_frame,
            super::MAX_RECORDS_PER_FRAME
        );
        let frame = reader.read_frame().unwrap().expect("кадр 0 — снапшот");
        assert_eq!(frame.len(), 2, "два уровня снапшота");
        assert!(
            frame.iter().all(
                |r| r.ev == hftbacktest::types::LOCAL_BID_DEPTH_SNAPSHOT_EVENT
                    || r.ev == hftbacktest::types::LOCAL_ASK_DEPTH_SNAPSHOT_EVENT
            ),
            "кадр 0 — только снапшотные флаги"
        );

        // Старые сутки целы: заголовок и снапшот на месте.
        let old = std::fs::File::open(day_one_file).unwrap();
        let mut old_reader = crate::binlog::Reader::open(old).unwrap();
        assert_eq!(old_reader.header().tick_e9, TICK_E9);
        assert!(old_reader.read_frame().unwrap().is_some());
    }

    /// Заголовок суточного файла несёт шаг цены и шаг количества
    /// (Decision 7: дельты в тиках без масштаба в том же файле
    /// невосстановимы) — читается назад тем же `Reader`, что читает кадры.
    #[test]
    fn daily_file_header_carries_tick_size_and_qty_step() {
        let dir = tempfile::tempdir().unwrap();
        let mut rec = Recorder::open(dir.path(), "BTCUSDT", 100, 10, DAY).unwrap();
        let mut book = crate::book::Book::new(100, 10);
        book.apply(&crate::book::Update {
            is_snapshot: true,
            u: 1,
            seq: 1,
            cts_ms: 0,
            bids: vec![(1_000, 50)],
            asks: vec![(1_100, 60)],
        })
        .unwrap();
        rec.on_snapshot(&book, 0, 1).unwrap();
        rec.flush().unwrap();

        let file = std::fs::File::open(rec.current_file()).unwrap();
        let reader = crate::binlog::Reader::open(file).unwrap();
        assert_eq!(reader.header().tick_e9, 100);
        assert_eq!(reader.header().step_e9, 10);
    }

    /// Шаги для заголовка берутся из `instruments.csv` шага 0.4 — тем же
    /// разбором и в том же масштабе. Все четыре числа инструмента различны:
    /// перепутанные колонки `tick_size`/`qty_step`/`min_order_qty` прошли бы
    /// тест с совпадающими значениями незамеченными (тот же приём, что
    /// `instruments_csv_round_trips_and_is_nonempty` в `lob.rs`).
    #[test]
    fn steps_come_from_the_right_columns_of_instruments_csv() {
        use crate::bybit::rest::Instrument;
        let dir = tempfile::tempdir().unwrap();
        let path = super::instruments_csv_path(dir.path());
        crate::commands::lob::write_instruments_csv(
            &path,
            &[Instrument {
                symbol: "SOLUSDT".to_string(),
                base_coin: "SOL".to_string(),
                quote_coin: "USDT".to_string(),
                contract_type: "LinearPerpetual".to_string(),
                status: "Trading".to_string(),
                launch_time_ms: Some(1_600_000_000_000),
                tick_e9: 10_000_000,           // 0.01
                min_order_qty_e9: 500_000_000, // 0.5 — не шаг!
                qty_step_e9: 250_000_000,      // 0.25 — не минлот!
                min_notional_value_e9: 5_000_000_000,
            }],
        )
        .unwrap();

        assert_eq!(
            super::load_steps_for_symbol(&path, "SOLUSDT").unwrap(),
            (10_000_000, 250_000_000),
            "tick из tick_size, step из qty_step, а не из соседних колонок"
        );
        assert!(
            super::load_steps_for_symbol(&path, "NOSUCHUSDT").is_err(),
            "чужой символ — ошибка, а не шаги соседа"
        );
    }

    /// Предстартовый гейт: ниже `×14` — запрет с числами, ровно на границе и
    /// выше — старт. Граница включается: «меньше» запрещает, «равно» ещё нет.
    #[test]
    fn start_is_refused_below_fourteen_day_budgets_and_allowed_at_the_line() {
        assert_eq!(
            super::START_FREE_BYTES_REQUIRED,
            super::DAY_BUDGET_BYTES * 14
        );
        let denied =
            super::check_start_free_space(super::START_FREE_BYTES_REQUIRED - 1).unwrap_err();
        assert_eq!(
            denied,
            super::RecordError::SpaceDenied {
                free_bytes: super::START_FREE_BYTES_REQUIRED - 1,
                required_bytes: super::START_FREE_BYTES_REQUIRED,
            }
        );
        assert!(super::check_start_free_space(super::START_FREE_BYTES_REQUIRED).is_ok());
        assert!(super::check_start_free_space(u64::MAX).is_ok());
    }

    /// Часовой сэмпл: строго ниже `×2` — остановка, ровно на границе — ещё
    /// продолжение («падение ниже», не «до»).
    #[test]
    fn hourly_sample_stops_strictly_below_two_day_budgets() {
        assert_eq!(super::STOP_FREE_BYTES_REQUIRED, super::DAY_BUDGET_BYTES * 2);
        assert!(super::should_stop_on_free_space(
            super::STOP_FREE_BYTES_REQUIRED - 1
        ));
        assert!(!super::should_stop_on_free_space(
            super::STOP_FREE_BYTES_REQUIRED
        ));
        assert!(!super::should_stop_on_free_space(u64::MAX));
    }

    /// ОС действительно отдаёт свободное место живого каталога, а не ошибку
    /// обёртки. Только там, где измерение реализовано (Windows/Linux).
    #[cfg(any(windows, target_os = "linux"))]
    #[test]
    fn os_reports_free_space_for_a_live_directory() {
        use super::{FreeSpaceCheck, OsFreeSpaceCheck};
        let dir = tempfile::tempdir().unwrap();
        let free = OsFreeSpaceCheck.free_bytes(dir.path()).unwrap();
        assert!(free > 0, "у живого каталога обязано быть свободное место");
    }

    /// Индекс строки дня совпадает с целочисленным делением меток — иначе
    /// горячий путь ротировал бы не на той границе, что именует файлы.
    #[test]
    fn day_index_matches_ns_division() {
        for (ts_ns, day) in [
            (0i64, "1970-01-01"),
            (86_400_000_000_000 - 1, "1970-01-01"),
            (86_400_000_000_000, "1970-01-02"),
            (-1, "1969-12-31"),
            (1_757_800_000_000_000_000, "2025-09-13"),
        ] {
            assert_eq!(super::day_string_of_ns(ts_ns).unwrap(), day);
            assert_eq!(
                super::day_index_of_day_str(day).unwrap(),
                ts_ns.div_euclid(super::NS_PER_DAY),
                "строка и деление обязаны указывать на одни сутки"
            );
        }
        assert!(super::day_index_of_day_str("13.09.2025").is_err());
    }

    /// CLI-поверхность `lob record`: символ обязателен, корень и REST-хост —
    /// с дефолтами. Без сети: только разбор аргументов.
    #[test]
    fn record_cli_parses_symbol_and_defaults() {
        #[derive(clap::Parser)]
        struct TestCli {
            #[command(subcommand)]
            cmd: crate::commands::lob::LobCommand,
        }
        use clap::Parser as _;
        let cli = TestCli::try_parse_from(["t", "record", "--symbol", "SOLUSDT"]).unwrap();
        match cli.cmd {
            crate::commands::lob::LobCommand::Record(args) => {
                assert_eq!(args.symbol, "SOLUSDT");
                assert_eq!(args.root, std::path::PathBuf::from("data/bybit"));
            }
            crate::commands::lob::LobCommand::Pick(_) => {
                panic!("разобралась не та подкоманда")
            }
            crate::commands::lob::LobCommand::Verify(_) => {
                panic!("разобралась не та подкоманда")
            }
            crate::commands::lob::LobCommand::Export(_) => {
                panic!("разобралась не та подкоманда")
            }
            crate::commands::lob::LobCommand::Clock(_) => {
                panic!("разобралась не та подкоманда")
            }
            crate::commands::lob::LobCommand::Probe(_) => {
                panic!("разобралась не та подкоманда")
            }
            crate::commands::lob::LobCommand::Levels(_) => {
                panic!("разобралась не та подкоманда")
            }
            crate::commands::lob::LobCommand::Markout(_) => {
                panic!("разобралась не та подкоманда")
            }
            crate::commands::lob::LobCommand::Watch(_) => {
                panic!("разобралась не та подкоманда")
            }
            crate::commands::lob::LobCommand::Pilot(_) => {
                panic!("разобралась не та подкоманда")
            }
        }
    }

    /// Контракт порядка: живое событие до первого снапшота — громкий
    /// `NoSnapshot`, а не запись в файл без кадра 0 и не паника.
    #[test]
    fn staging_before_the_first_snapshot_is_a_loud_error() {
        let dir = tempfile::tempdir().unwrap();
        let mut rec = open_recorder(&dir);
        let mut book = crate::book::Book::new(TICK_E9, STEP_E9);
        let delta = crate::book::Update {
            is_snapshot: false,
            u: 2,
            seq: 2,
            cts_ms: 1_757_800_000_020,
            bids: vec![(150_000_000_000, 1_000_000_000)],
            asks: vec![],
        };
        assert_eq!(
            rec.stage_book_update(
                &mut book,
                &delta,
                1_757_800_000_020_000_000,
                1_757_800_000_021_000_000
            )
            .unwrap_err(),
            super::RecordError::NoSnapshot
        );
        let trade = crate::bybit::ws::Trade {
            exch_ms: 1_757_800_000_020,
            price_e9: 150_000_000_000,
            qty_e9: 1_000_000_000,
            aggressor_is_buy: true,
            block: false,
        };
        assert_eq!(
            rec.stage_trade(&trade, 1_757_800_000_021_000_000)
                .unwrap_err(),
            super::RecordError::NoSnapshot
        );
    }

    /// Разрыв `u` — это `SequenceGap` с числами (для строки gaps.csv), книга и
    /// файл не тронуты: пропущенное событие не пишется «как ни в чём не
    /// бывало», файл ждёт снапшота.
    #[test]
    fn sequence_gap_is_reported_loudly_and_stages_nothing() {
        let dir = tempfile::tempdir().unwrap();
        let mut rec = open_recorder(&dir);
        let mut book = snapshot_book();
        rec.on_snapshot(&book, 1_757_800_000_000_000_000, 1_757_800_000_001_000_000)
            .unwrap();
        let before = rec.records_total();

        let skipped = crate::book::Update {
            is_snapshot: false,
            u: 4, // ждали 2
            seq: 4,
            cts_ms: 1_757_800_000_040,
            bids: vec![(150_000_000_000, 9_000_000_000)],
            asks: vec![],
        };
        assert_eq!(
            rec.stage_book_update(
                &mut book,
                &skipped,
                1_757_800_000_040_000_000,
                1_757_800_000_041_000_000
            )
            .unwrap_err(),
            super::RecordError::SequenceGap {
                expected: 2,
                got: 4
            }
        );
        rec.log_gap(
            super::GapKind::SequenceGap,
            "2025-09-13T21:46:40Z",
            "ждали u=2, пришло u=4",
        )
        .unwrap();
        assert_eq!(rec.records_total(), before);
        let gaps = read_gap_rows(&gaps_csv_path(dir.path())).unwrap();
        assert_eq!(gaps.len(), 1);
        assert_eq!(gaps[0].kind, super::GapKind::SequenceGap);
    }

    /// Сделки пишутся по одной записи: сторона агрессора — флагом, блочная —
    /// `ival = 1`, цена/размер — в тиках/лотах. Сырые события пишутся все
    /// (Decision 7), фильтр `BT` — дело разметки по `ival`, не записи.
    #[test]
    fn trades_are_staged_with_side_block_flag_and_tick_lot_scale() {
        let dir = tempfile::tempdir().unwrap();
        let mut rec = open_recorder(&dir);
        let book = snapshot_book();
        rec.on_snapshot(&book, 1_757_800_000_000_000_000, 1_757_800_000_001_000_000)
            .unwrap();

        let buy = crate::bybit::ws::Trade {
            exch_ms: 1_757_800_000_100,
            price_e9: 150_000_000_000,
            qty_e9: 2_000_000_000,
            aggressor_is_buy: true,
            block: false,
        };
        let block_sell = crate::bybit::ws::Trade {
            exch_ms: 1_757_800_000_101,
            price_e9: 149_990_000_000,
            qty_e9: 5_000_000_000,
            aggressor_is_buy: false,
            block: true,
        };
        rec.stage_trade(&buy, 1_757_800_000_101_000_000).unwrap();
        rec.stage_trade(&block_sell, 1_757_800_000_102_000_000)
            .unwrap();
        rec.flush().unwrap();

        let file = std::fs::File::open(rec.current_file()).unwrap();
        let mut reader = crate::binlog::Reader::open(file).unwrap();
        reader.read_frame().unwrap().expect("кадр 0 — снапшот");
        let trades = reader.read_frame().unwrap().expect("кадр 1 — сделки");
        assert_eq!(trades.len(), 2);
        assert_eq!(trades[0].ev, hftbacktest::types::LOCAL_BUY_TRADE_EVENT);
        assert_eq!(trades[0].price_ticks, 150_000_000_000 / TICK_E9);
        assert_eq!(trades[0].qty_lots, 2_000_000_000 / STEP_E9);
        assert_eq!(trades[0].ival, 0);
        assert_eq!(
            trades[0].exch_ts_ns,
            1_757_800_000_100i64.saturating_mul(1_000_000)
        );
        assert_eq!(trades[1].ev, hftbacktest::types::LOCAL_SELL_TRADE_EVENT);
        assert_eq!(trades[1].ival, 1, "блочная сделка помечена, но записана");
    }

    /// Гейт GC для шага 0.3: установившийся поток (те же цены и размеры, что
    /// в прогреве) не аллоцирует на событие. Замер — тем же счётчиком на
    /// глобальном аллокаторе, что `steady_frames_allocate_nothing` в
    /// `src/lob/levels.rs`: от уже разобранных `Update`/`Trade` (граница
    /// `ws.rs`) через книгу и батч до кадра на диске.
    #[test]
    fn steady_events_allocate_nothing() {
        let dir = tempfile::tempdir().unwrap();
        let mut rec = open_recorder(&dir);
        let mut book = snapshot_book();
        rec.on_snapshot(&book, 1_757_800_000_000_000_000, 1_757_800_000_001_000_000)
            .unwrap();

        // Один шаблон дельты и одна сделка на все итерации: меняются только
        // монотонные счётчики (`u`), форма и magnitudes — нет, иначе замер
        // ловил бы рост буферов сжатия, а не аллокатор.
        let mut upd = crate::book::Update {
            is_snapshot: false,
            u: 2,
            seq: 2,
            cts_ms: 1_757_800_000_020,
            bids: vec![(150_000_000_000, 3_000_000_000)],
            asks: vec![(150_010_000_000, 4_000_000_000)],
        };
        let trade = crate::bybit::ws::Trade {
            exch_ms: 1_757_800_000_020,
            price_e9: 150_000_000_000,
            qty_e9: 1_000_000_000,
            aggressor_is_buy: true,
            block: false,
        };
        // Прогрев вне замера: книга, батч и Writer при рабочей ёмкости.
        for k in 0..2_000u64 {
            upd.u = 2 + k;
            upd.seq = 2 + k;
            rec.stage_book_update(
                &mut book,
                &upd,
                1_757_800_000_020_000_000,
                1_757_800_000_021_000_000,
            )
            .unwrap();
            rec.stage_trade(&trade, 1_757_800_000_021_000_000).unwrap();
        }
        let (_, counts) = crate::alloc_count::measure(|| {
            for k in 0..100_000u64 {
                upd.u = 2_002 + k;
                upd.seq = 2_002 + k;
                rec.stage_book_update(
                    &mut book,
                    &upd,
                    1_757_800_000_020_000_000,
                    1_757_800_000_021_000_000,
                )
                .unwrap();
                rec.stage_trade(&trade, 1_757_800_000_021_000_000).unwrap();
            }
        });
        assert_eq!(
            counts.allocations, 0,
            "горячий путь обязан не аллоцировать после прогрева"
        );
    }

    /// Шаг 0.7: дрен канала авторитета берёт только последнее, пустой канал —
    /// `None` (ветка подавления, а не ожидание сети).
    #[test]
    fn authority_drain_takes_only_the_latest_and_empty_is_none() {
        let (tx, mut rx) = std::sync::mpsc::channel::<(i64, i64)>();
        assert_eq!(drain_latest_steps(&mut rx), None);
        tx.send((10_000_000, 1_000_000)).unwrap();
        tx.send((5_000_000, 1_000_000)).unwrap();
        assert_eq!(drain_latest_steps(&mut rx), Some((5_000_000, 1_000_000)));
        assert_eq!(drain_latest_steps(&mut rx), None);
    }

    struct FakeStepsRest {
        responses: std::collections::VecDeque<Result<String, crate::bybit::rest::RestError>>,
    }

    impl crate::bybit::rest::PublicRest for FakeStepsRest {
        fn get(
            &mut self,
            _path: &str,
            _query: &[(&str, &str)],
        ) -> Result<String, crate::bybit::rest::RestError> {
            self.responses
                .pop_front()
                .expect("тест не подготовил столько ответов")
        }
    }

    fn fake_instruments_body(symbol: &str, tick: &str, step: &str) -> String {
        format!(
            r#"{{"retCode":0,"retMsg":"OK","result":{{"category":"linear","list":[{{"symbol":"{symbol}","contractType":"LinearPerpetual","status":"Trading","baseCoin":"SOL","quoteCoin":"USDT","priceFilter":{{"tickSize":"{tick}"}},"lotSizeFilter":{{"minOrderQty":"0.1","qtyStep":"{step}"}}}}],"nextPageCursor":""}}}}"#
        )
    }

    fn fake_steps_rest(bodies: Vec<String>) -> FakeStepsRest {
        FakeStepsRest {
            responses: bodies.into_iter().map(Ok).collect(),
        }
    }

    /// Ремонт 0.7 Р1 (В-6): подозрение будит авторитет, не блокируя цикл.
    /// Первая часть — горячий путь не ждёт: `try_send` на полном канале
    /// возвращается сразу, а не висит до приёма. Вторая — авторитет после
    /// пробуждения делает второй fetch за секунды, а не через час.
    #[test]
    fn suspicion_wakes_authority_without_blocking_hot_path() {
        let (wake_tx, wake_rx) = std::sync::mpsc::sync_channel::<()>(1);
        let old = fake_instruments_body("SOLUSDT", "0.01", "0.001");
        let new = fake_instruments_body("SOLUSDT", "0.005", "0.001");
        let steps_rx = spawn_steps_authority_with_rest(
            fake_steps_rest(vec![old, new]),
            "SOLUSDT".to_string(),
            wake_rx,
        );
        let first = steps_rx
            .recv_timeout(std::time::Duration::from_secs(5))
            .expect("первый fetch обязан прийти без пробуждения");
        assert_eq!(first, (TICK_E9, STEP_E9));

        let (full_tx, full_rx) = std::sync::mpsc::sync_channel::<()>(1);
        full_tx.try_send(()).unwrap();
        let probe_tx = full_tx.clone();
        let (done_tx, done_rx) = std::sync::mpsc::channel::<()>();
        std::thread::spawn(move || {
            request_steps_refresh(&probe_tx);
            let _ = done_tx.send(());
        });
        done_rx
            .recv_timeout(std::time::Duration::from_secs(2))
            .expect("горячий путь заблокировался на полном канале-будильнике");
        drop(full_rx);

        request_steps_refresh(&wake_tx);
        let second = steps_rx
            .recv_timeout(std::time::Duration::from_secs(5))
            .expect("авторитет обязан проснуться по подозрению, а не через час");
        assert_eq!(second, (5_000_000, STEP_E9));
    }

    /// Ремонт 0.7 Р2 (В-7): весь стык на фейке — подозрение, ответ авторитета
    /// с новыми шагами, ротация файла, строка `step_change` в `gaps.csv`.
    #[test]
    fn suspicion_with_new_steps_rotates_file_and_leaves_gap_row() {
        let dir = tempfile::tempdir().unwrap();
        let mut rec = open_recorder(&dir);
        let old = fake_instruments_body("SOLUSDT", "0.01", "0.001");
        let new = fake_instruments_body("SOLUSDT", "0.005", "0.001");
        let (wake_tx, wake_rx) = std::sync::mpsc::sync_channel::<()>(1);
        let mut steps_rx = spawn_steps_authority_with_rest(
            fake_steps_rest(vec![old, new]),
            "SOLUSDT".to_string(),
            wake_rx,
        );
        let first = steps_rx
            .recv_timeout(std::time::Duration::from_secs(5))
            .expect("первый fetch");
        assert_eq!(first, (TICK_E9, STEP_E9));
        drain_latest_steps(&mut steps_rx);

        request_steps_refresh(&wake_tx);
        let fresh = steps_rx
            .recv_timeout(std::time::Duration::from_secs(5))
            .expect("свежие шаги после пробуждения");
        assert_eq!(fresh, (5_000_000, STEP_E9));

        let mut logged = false;
        let mut suppressed = 0u64;
        let rotated = resolve_suspicion(
            &mut rec,
            Some(fresh),
            "2026-09-08T00:00:20Z",
            "price 150005000000 не на шаге 10000000",
            &mut logged,
            &mut suppressed,
        )
        .unwrap();
        assert_eq!(rotated, Some((5_000_000, STEP_E9)));
        assert_eq!(rec.tick_e9(), 5_000_000);
        assert_eq!(rec.part(), 2);
        let gaps = read_gap_rows(&gaps_csv_path(dir.path())).unwrap();
        assert_eq!(gaps.len(), 1);
        assert_eq!(gaps[0].kind, GapKind::StepChange);
    }

    /// Ремонт 0.7 Р2 (В-7), второй случай: авторитет ответил прежними шагами —
    /// ротации нет, а строка подавления в `gaps.csv` есть.
    #[test]
    fn suspicion_with_same_steps_suppresses_without_rotation_but_leaves_gap_row() {
        let dir = tempfile::tempdir().unwrap();
        let mut rec = open_recorder(&dir);
        let old_a = fake_instruments_body("SOLUSDT", "0.01", "0.001");
        let old_b = fake_instruments_body("SOLUSDT", "0.01", "0.001");
        let (wake_tx, wake_rx) = std::sync::mpsc::sync_channel::<()>(1);
        let mut steps_rx = spawn_steps_authority_with_rest(
            fake_steps_rest(vec![old_a, old_b]),
            "SOLUSDT".to_string(),
            wake_rx,
        );
        let first = steps_rx
            .recv_timeout(std::time::Duration::from_secs(5))
            .expect("первый fetch");
        assert_eq!(first, (TICK_E9, STEP_E9));
        drain_latest_steps(&mut steps_rx);

        request_steps_refresh(&wake_tx);
        let fresh = steps_rx
            .recv_timeout(std::time::Duration::from_secs(5))
            .expect("второй fetch после пробуждения");
        assert_eq!(fresh, (TICK_E9, STEP_E9));

        let mut logged = false;
        let mut suppressed = 0u64;
        let rotated = resolve_suspicion(
            &mut rec,
            Some(fresh),
            "2026-09-08T00:00:20Z",
            "price 150005000000 не на шаге 10000000",
            &mut logged,
            &mut suppressed,
        )
        .unwrap();
        assert_eq!(rotated, None);
        assert_eq!(rec.part(), 1, "прежние шаги — не ротация");
        assert_eq!(suppressed, 1);
        let gaps = read_gap_rows(&gaps_csv_path(dir.path())).unwrap();
        assert_eq!(gaps.len(), 1);
        assert_eq!(gaps[0].kind, GapKind::BookInvariant);
    }
}
