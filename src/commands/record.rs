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
//! данных. Холодный путь (часовой таймер в `run_record`): перечитывает
//! `/v5/market/instruments-info` и даёт
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

/// Окно потери при крахе, секунды — то самое «кадр раз в ~10 секунд живого
/// потока» из doc `FRAME_TARGET_RECORDS`, названное константой (таск 25):
/// `lob session` сбрасывает накопленный кадр по тику таймера рантайма не
/// реже этого окна, поэтому тихий инструмент (или весь пул при молчании
/// сети) держит в памяти не больше окна, а не до часового таймера. Число не
/// новое — оно уже было обоснованием порога записей; здесь оно становится
/// границей по времени для того же компромисса «потерянные секунды при
/// крахе против степени сжатия мелких кадров».
pub const FRAME_LOSS_WINDOW_SECS: u64 = 10;

/// Уровень zstd суточных файлов. Назначен измерением (таск 25, бенч
/// `tests/collector_bench.rs::zstd_level_bytes_per_record_and_cpu_on_a_real_
/// binlog` на `data/collector/20260912T011109Z-after`, 8 файлов, 182 641
/// запись): уровень 1 — 7.245 Б/запись при 170 нс/запись; 3 (прежний
/// дефолт библиотеки) — 7.307 при 215; 6 — 6.908 при 475; 9 — 6.839 при
/// 824. Уровень 1 строго лучше прежнего по обеим осям; 6/9 покупают
/// −5…6 % байт за 2.8–4.8× CPU сжатия и большие контексты на инструмент —
/// при дисковом бюджете без потолка (В-32) и коллекторе «супер экономном»
/// по CPU/RSS выбран 1. Числа — в `docs/findings/collector-2026-09-12.md`.
pub const ZSTD_LEVEL: i32 = 1;

/// Период часового таймера: `clock.csv` (шаг 0.5, хук — см. `run_session`) и
/// перечитывание `instruments-info`. Один таймер на оба — два часовых
/// будильника дрейфовали бы друг относительно друга и будили процесс дважды
/// в час вместо одного раза.
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
    /// Кадр не записался на диск (таск 25): потерян целый батч — до
    /// `FRAME_TARGET_RECORDS` записей, молчать нельзя.
    WriteFailed,
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

/// Первая свободная часть суток начиная с `from`: часть 1, если файла нет
/// (обычный случай), иначе `-p2`, `-p3`, … — перезапись занятого имени была
/// бы потерей данных. `pub(crate)`: таск 22 переиспользует её из
/// `commands::lob::session` — второй сессии в те же сутки нужен тот же поиск
/// свободного номера, что уже применяет `lob record`, а не свой расчёт.
pub(crate) fn claim_part(
    root: &Path,
    symbol: &str,
    day: &str,
    from: u32,
    tick_e9: i64,
    step_e9: i64,
) -> Result<(crate::binlog::Writer<File>, u32), RecordError> {
    claim_part_with(root, symbol, day, from, tick_e9, step_e9, |file| file)
}

/// То же, что `claim_part`, но приёмник файла выбирает вызывающий (таск 25):
/// `lob session` заворачивает `File` в свой покадровый буфер, `lob record`
/// пишет в голый `File` — один цикл поиска свободного номера части на
/// обоих, а не две копии.
pub(crate) fn claim_part_with<W: std::io::Write>(
    root: &Path,
    symbol: &str,
    day: &str,
    from: u32,
    tick_e9: i64,
    step_e9: i64,
    wrap: impl FnOnce(File) -> W,
) -> Result<(crate::binlog::Writer<W>, u32), RecordError> {
    let mut part = from.max(1);
    loop {
        let path = day_file_path(root, symbol, day, part);
        if !path.exists() {
            let file = File::create(&path)?;
            let header = Header {
                tick_e9,
                step_e9,
                max_records_per_frame: MAX_RECORDS_PER_FRAME,
            };
            let writer = crate::binlog::Writer::create(wrap(file), header, ZSTD_LEVEL)?;
            return Ok((writer, part));
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
    // Единственный читатель `instruments.csv` (дозапрос по ревью таска 08,
    // ось Craft) — терпит метку `debug` первой строкой, голый
    // `csv::Reader::from_path` читал бы её как заголовок.
    let mut r = crate::commands::lob::pick::instruments_csv_reader(instruments_csv)?;
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
pub(crate) fn event_exch_ms(event: &Event) -> Option<i64> {
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
                    ConnEvent::Message {
                        local_ts_ns, event, ..
                    } => {
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
                    // Таск 28: у одно-символьного рекордера сокет несёт
                    // один инструмент, поэтому веер `first_of_socket`
                    // здесь всегда `true` и на учёт не влияет; `Unrouted`
                    // не встречается вовсе — подписки сокета и его
                    // единственный символ совпадают.
                    ConnEvent::Unrouted { .. } => {}
                    ConnEvent::Disconnected { .. } => {
                        synced = false;
                        rec.log_gap(GapKind::SequenceGap, &ts_utc_of_ns(SystemClock.now_ns()), "транспорт переподключился — шов покрытия")?;
                    }
                }
            }
            _ = hourly.tick() => {                // Часовой тик: место, итог подавления, flush. Шаги приходят
                // сами из ОС-потока авторитета — тик их только забирает
                // (шаг 0.7, Decision 24), сеть здесь не ждётся никогда.
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
mod tests;
