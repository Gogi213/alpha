//! Бинарный лог: дельты в varint, кадры с длиной, самодостаточные сутки
//! (Decision 7, 17, 23 `PLAN.md`; шаг 0.2).
//!
//! # Раскладка суточного файла
//!
//! ```text
//! [заголовок: magic(4) | version(1) | tick_e9(8 LE) | step_e9(8 LE)
//!             | max_records_per_frame(4 LE)]
//! [кадр 0: u32 длина LE | zstd-кадр]   ← синтетический полный снапшот
//! [кадр 1: u32 длина LE | zstd-кадр]   ← дальше живые дельты потока
//! [кадр 2: ...]
//! ...
//! ```
//!
//! Заголовок несёт `tickSize` и `qtyStep`, потому что записи внутри кадров
//! хранят цену в тиках и размер в шагах количества (`ARCHITECTURE.md` A1) —
//! без масштаба в том же файле дельты нечем перевести обратно в цену, а
//! ротация происходит по UTC-полуночи посреди потока, и без синтетического
//! снапшота **в этом же файле** каждые следующие сутки были бы нечитаемы без
//! состояния предыдущих: ровно то, что Decision 7 объявляет невосстановимым.
//!
//! `max_records_per_frame` — добавка ревизии 10 к Decision 23. `Reader`
//! разжимает тело кадра через bounded zstd API (`bulk::Decompressor::
//! decompress` с явной ёмкостью, не `stream::decode_all`, который растил бы
//! буфер до тех пор, пока не кончится вход): ёмкость — это потолок,
//! вычисленный из `max_records_per_frame` этого же заголовка и минимального
//! размера закодированной записи (`max_frame_payload_bytes` ниже), и если
//! распаковка потребовала бы больше, zstd возвращает ошибку **до** того,
//! как эти байты выделены, а не после. Без потолка испорченный (или
//! специально подобранный) кадр — маленький на диске, но сильно сжимаемый —
//! разжался бы в сколь угодно большой буфер: то самое «неограниченная
//! память» из процесса, который по плану работает неделями без присмотра.
//! Число берётся из заголовка, а не назначается константой этого модуля —
//! иначе оно было бы изобретённым числом, что план запрещает везде (Decision
//! 23). `Writer::write_frame`, симметрично, отказывается писать кадр
//! длиннее этого потолка — программной ошибкой (`assert!`), а не
//! результатом: число записей в кадре и число в заголовке выбирает один и
//! тот же вызывающий код, значит расхождение между ними — его баг батчинга,
//! а не порча данных, которая приходит с диска или из сети.
//!
//! Кадр — это `u32` длина сжатого блока, а не многокадровый zstd-поток:
//! чтение — «прочитать `u32`, прочитать столько байт, разжать один кадр».
//! Это осознанно снимает зависимость от многокадровой семантики декодера
//! (Decision 7) и превращает усечённый хвост файла в короткое чтение внутри
//! ровно одного кадра, а не в повреждение, которое пришлось бы диагностировать
//! по содержимому.
//!
//! Внутри кадра дельта-кодирование идёт **против предыдущей записи в этом же
//! кадре**, не против записи из предыдущего кадра: у каждого кадра своя точка
//! отсчёта (цена и размер стартуют с нуля, время — с эпохи кадра, которой
//! выбрана метка первой записи). Это то же самое решение, что и с длиной —
//! кадр читается независимо от соседних, поэтому усечение или повреждение
//! одного кадра не портит декодирование остальных.
//!
//! Запись — это одно **сырое событие**, надмножество полей `Event` крейта
//! `hftbacktest` (`ev`, `exch_ts`, `local_ts`, `px`, `qty`, `order_id`, `ival`,
//! `fval`; поля подтверждены по `hftbacktest-0.9.4/src/types.rs`, Decision 17),
//! а не реконструированное состояние книги: снапшот в начале суток — это
//! много обычных записей подряд (по одной на уровень), никакого отдельного
//! «состояния» модуль не знает и не хранит. `px`/`qty` крейта — `f64` для
//! границы с бэктестом; в логе на их месте целые `price_ticks`/`qty_lots`
//! (A1) — это и есть смысл слова «надмножество» в Decision 7: тот же набор
//! данных, но без потери точности, которую f64 внёс бы на самом горячем поле.
//! Перевод в `f64` для `Event` — дело экспортёра (шаг 6.1), не этого модуля.
//!
//! Дельты кодируются `wrapping_sub`/`wrapping_add` (по модулю 2^64), а не
//! проверяемой арифметикой: это честная биекция на всём диапазоне `i64` вне
//! зависимости от того, насколько разъехались соседние значения, и она не
//! может ни переполниться, ни запаниковать — в отличие от обычного `-`,
//! который в debug-сборке (`cargo test`) паникует на переполнении. Тест
//! `deltas_survive_values_f64_cannot_hold_exactly` проверяет это на паре
//! значений, между которыми обычное вычитание переполнилось бы.

use std::fmt;
use std::io::{self, Read, Write};

/// Магия формата. Проверяется при открытии, чтобы чужой или пустой файл
/// не читался молча как валидный лог с нулевым содержимым.
pub const MAGIC: [u8; 4] = *b"ABLG";

/// Версия формата — байт в заголовке (раздел «Contracts touched» `PLAN.md`:
/// «формат бинарного лога версионируется байтом в заголовке»). Меняется при
/// любой несовместимой правке раскладки, а не при добавлении новых значений
/// существующих полей.
///
/// `2`, не `1`: ревизия 10 Decision 23 добавила поле `max_records_per_frame`
/// в хвост заголовка, а старый читатель этого не ждёт — несовместимая
/// правка раскладки обязана поднять версию, иначе файл версии 1 читался бы
/// байт в байт как версия 2 и хвост заголовка ушёл бы не туда (см. тест
/// `old_version_file_is_rejected_not_misread`).
pub const VERSION: u8 = 2;

/// magic(4) + version(1) — этого достаточно, чтобы решить, версия ли это,
/// которую понимает остальной код. Читается отдельно от хвоста заголовка
/// (`HEADER_TAIL_LEN`) и до него: у более старой версии хвост другой длины
/// и другого смысла, и разбирать его тем же способом значило бы читать
/// чужие байты как свои вместо понятной ошибки версии.
const MAGIC_VERSION_LEN: usize = 4 + 1;

/// tick_e9(8) + step_e9(8) + max_records_per_frame(4) — хвост заголовка
/// ровно текущей версии.
const HEADER_TAIL_LEN: usize = 8 + 8 + 4;

/// Полная длина заголовка текущей версии.
const HEADER_LEN: usize = MAGIC_VERSION_LEN + HEADER_TAIL_LEN;

/// Длина префикса кадра — `u32`, как назначено Decision 7.
const LEN_PREFIX: usize = 4;

/// Заголовок суточного файла: шаг цены и шаг количества, в единицах 1e-9
/// (тот же масштаб, что `book::Book` и разбор `bybit::ws` уже используют) —
/// значения одни и те же по всему конвейеру, а не два независимых масштаба,
/// которые могли бы разойтись при переносе константы.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Header {
    pub tick_e9: i64,
    pub step_e9: i64,
    /// Максимум записей в одном кадре (Decision 23, ревизия 10). Число
    /// выбирает и хранит в заголовке вызывающий (рекордер, шаг 0.3) —
    /// этот модуль не назначает ему значение по умолчанию: «потолок
    /// берётся из файла, а не назначается», иначе это было бы изобретённым
    /// числом. `Writer::write_frame` отказывается писать кадр длиннее
    /// этого значения; `Reader` использует его, чтобы вычислить потолок
    /// разжатого тела кадра (`max_frame_payload_bytes`) и не дать
    /// bounded zstd API выделить память сверх него.
    pub max_records_per_frame: u32,
}

fn validate_header(h: Header) -> Result<(), BinlogError> {
    // Не `assert!`, как в `Book::new`: там аргументы приходят из кода,
    // здесь заголовок может прийти прямо с диска (`Reader::open`), и файл —
    // недоверенный ввод. Панике здесь взяться неоткуда ни при записи, ни
    // при чтении: обе стороны проверяются одной и той же функцией.
    if h.tick_e9 <= 0 || h.step_e9 <= 0 || h.max_records_per_frame == 0 {
        return Err(BinlogError::InvalidHeader {
            tick_e9: h.tick_e9,
            step_e9: h.step_e9,
            max_records_per_frame: h.max_records_per_frame,
        });
    }
    Ok(())
}

/// Одна запись — сырое событие. Поля надмножество `Event` крейта
/// `hftbacktest` (см. доку модуля): `price_ticks`/`qty_lots` вместо `px`/`qty`
/// крейта, остальные шесть — один в один.
///
/// `PartialEq`, не `Eq`: единственное нецелое поле, `fval`, резерв под `f64`
/// крейта и в этом логе не участвует в сравнении уровней (A1 касается
/// цены и размера книги, не этого сквозного поля). Ловушка на будущее:
/// derived `PartialEq` сравнивает `fval` через `==`, так что `NaN != NaN` —
/// тест на round trip с `fval = NaN` обязан сравнивать `.to_bits()`, а не
/// сами `Record` через `assert_eq!`, иначе побитово верный round trip
/// выглядел бы как провал.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Record {
    /// Флаги события — те же биты, что `hftbacktest::types::{DEPTH_EVENT,
    /// TRADE_EVENT, BUY_EVENT, ...}`. Модуль их не толкует, только хранит:
    /// смысл бит — дело пишущего (рекордер, шаг 0.3), не формата хранения.
    pub ev: u64,
    /// Наносекунды от эпохи Unix — тот же масштаб, что `local_ts_ns`
    /// в `bybit::conn::ConnEvent` (H12): `exch_ts` биржи приходит в мс
    /// (`cts`/`T`) и переводится в нс на той же границе, что и сравнение
    /// `exch_ts < local_ts`, а не заново здесь другим способом.
    pub exch_ts_ns: i64,
    pub local_ts_ns: i64,
    /// Цена как целое число тиков (A1). Тик восстанавливается из `tick_e9`
    /// заголовка: `price_e9 = price_ticks * tick_e9`.
    pub price_ticks: i64,
    /// Размер как целое число шагов количества (A1), аналогично `step_e9`.
    pub qty_lots: i64,
    pub order_id: u64,
    pub ival: i64,
    pub fval: f64,
}

/// Ошибка формата. `Io`/`Corrupt` несут текст, а не исходный `io::Error`:
/// он не реализует `PartialEq`, а структурные варианты ниже сравниваются
/// в тестах через `assert_eq!`, как `ApplyError` в `book/mod.rs`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BinlogError {
    /// Файла не хватило даже на заголовок. Пустой файл — частный случай
    /// с `got: 0`.
    TruncatedHeader {
        got: usize,
        want: usize,
    },
    /// Первые четыре байта — не `MAGIC`: не наш формат, а не сутки с нулём
    /// кадров.
    BadMagic {
        got: [u8; 4],
    },
    UnsupportedVersion {
        got: u8,
    },
    /// `tick_e9`/`step_e9` не положительны, либо `max_records_per_frame`
    /// нулевой — заголовок сфабрикован или повреждён; без этой проверки
    /// дельты в тиках молча приобрели бы нулевой или отрицательный
    /// масштаб, а нулевой потолок сделал бы каждый непустой кадр
    /// отвергнутым независимо от содержимого.
    InvalidHeader {
        tick_e9: i64,
        step_e9: i64,
        max_records_per_frame: u32,
    },
    /// Кадр объявил длину, для которой на диске не хватило байт: это
    /// усечённый хвост, а не повреждённое содержимое, и Decision 7 требует
    /// различать эти два случая — усечение диагностируется без разбора
    /// самого кадра.
    ShortRead {
        context: &'static str,
        want: usize,
        got: usize,
    },
    /// EOF на самом первом кадре: в файле нет даже синтетического снапшота,
    /// который Decision 7 требует от каждых суток. Заголовок-без-кадров —
    /// это отдельная, более узкая поломка, чем «файл кончился между
    /// кадрами», и молчаливый пустой результат здесь замаскировал бы
    /// потерю целых суток.
    MissingSnapshot,
    /// Кадр целиком прочитан, но не разобрался: битый zstd-поток или
    /// оборванный varint внутри уже разжатого содержимого.
    Corrupt(String),
    /// Сжатый кадр не влезает в `u32`-префикс. На практике недостижимо при
    /// разумном размере кадра, но `as u32` тихо обрезал бы длину, а это и
    /// есть silent corruption, которую этот модуль обязан не производить.
    FrameTooLarge {
        len: usize,
    },
    /// Разжатое тело кадра не поместилось бы в потолок, вычисленный из
    /// `max_records_per_frame` заголовка (Decision 23, ревизия 10):
    /// заявленная длина сжатого кадра на диске правдоподобна, но
    /// распаковка потребовала бы больше байт, чем потолок разрешает — либо
    /// кадр честно превышает потолок, либо испорчен так, что разжимаемый
    /// объём не сходится. Оба случая читаются здесь одинаково: bounded
    /// zstd API (`Reader::read_frame`) отказывается выделять память сверх
    /// `ceiling_bytes`, поэтому различить их без превышения самого потолка
    /// нечем, а превышать его ради диагностики — обходить весь смысл
    /// проверки.
    FrameExceedsHeaderCeiling {
        max_records_per_frame: u32,
        ceiling_bytes: usize,
    },
    Io(String),
}

impl fmt::Display for BinlogError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            BinlogError::TruncatedHeader { got, want } => {
                write!(f, "заголовок обрезан: {got} байт из {want}")
            }
            BinlogError::BadMagic { got } => write!(f, "неверная магия формата: {got:02x?}"),
            BinlogError::UnsupportedVersion { got } => {
                write!(f, "неподдерживаемая версия формата: {got}")
            }
            BinlogError::InvalidHeader {
                tick_e9,
                step_e9,
                max_records_per_frame,
            } => write!(
                f,
                "заголовок неисправен: tick_e9={tick_e9}, step_e9={step_e9}, \
                 max_records_per_frame={max_records_per_frame}"
            ),
            BinlogError::ShortRead { context, want, got } => {
                write!(f, "короткое чтение ({context}): {got} байт из {want}")
            }
            BinlogError::MissingSnapshot => {
                write!(
                    f,
                    "в файле нет ни одного кадра — нет синтетического снапшота"
                )
            }
            BinlogError::Corrupt(msg) => write!(f, "повреждён кадр: {msg}"),
            BinlogError::FrameTooLarge { len } => {
                write!(f, "сжатый кадр {len} байт не влезает в u32-префикс")
            }
            BinlogError::FrameExceedsHeaderCeiling {
                max_records_per_frame,
                ceiling_bytes,
            } => write!(
                f,
                "разжатый кадр превысил бы потолок заголовка \
                 (max_records_per_frame={max_records_per_frame}, {ceiling_bytes} байт) \
                 либо испорчен так, что разжимаемый объём не сходится"
            ),
            BinlogError::Io(msg) => write!(f, "ошибка ввода-вывода: {msg}"),
        }
    }
}

impl std::error::Error for BinlogError {}

impl From<io::Error> for BinlogError {
    fn from(e: io::Error) -> Self {
        BinlogError::Io(e.to_string())
    }
}

// ---------------------------------------------------------------------
// Varint: без знака (LEB128) и зигзаг для знаковых дельт.
// ---------------------------------------------------------------------

/// LEB128 без знака. `Vec::push` — единственная операция роста, поэтому
/// после прогрева (`Vec` уже нужной ёмкости) вызов не аллоцирует.
fn write_uvarint(buf: &mut Vec<u8>, mut v: u64) {
    loop {
        let byte = (v & 0x7f) as u8;
        v >>= 7;
        if v == 0 {
            buf.push(byte);
            return;
        }
        buf.push(byte | 0x80);
    }
}

/// Зигзаг переставляет знаковые значения так, чтобы малые по модулю (в т.ч.
/// отрицательные) давали короткий varint — ровно то, что нужно дельтам,
/// которые в среднем малы и колеблются вокруг нуля в обе стороны.
fn zigzag_encode(v: i64) -> u64 {
    ((v << 1) ^ (v >> 63)) as u64
}

fn zigzag_decode(v: u64) -> i64 {
    ((v >> 1) as i64) ^ -((v & 1) as i64)
}

fn write_zigzag(buf: &mut Vec<u8>, v: i64) {
    write_uvarint(buf, zigzag_encode(v));
}

/// Читает varint из `buf`, начиная с `*pos`, и продвигает `*pos`. Ошибка —
/// не паника: `buf.get` вместо индексации, явный предел на число байт,
/// потому что оборванный кадр обязан дать `Corrupt`, а не читать за границей.
fn read_uvarint(buf: &[u8], pos: &mut usize) -> Result<u64, BinlogError> {
    let mut result: u64 = 0;
    let mut shift = 0u32;
    loop {
        let byte = *buf
            .get(*pos)
            .ok_or_else(|| BinlogError::Corrupt("varint обрезан".to_string()))?;
        *pos += 1;
        if shift >= 64 {
            return Err(BinlogError::Corrupt("varint длиннее 64 бит".to_string()));
        }
        result |= ((byte & 0x7f) as u64) << shift;
        if byte & 0x80 == 0 {
            return Ok(result);
        }
        shift += 7;
    }
}

fn read_zigzag(buf: &[u8], pos: &mut usize) -> Result<i64, BinlogError> {
    read_uvarint(buf, pos).map(zigzag_decode)
}

// ---------------------------------------------------------------------
// Кодирование одной записи внутри кадра.
// ---------------------------------------------------------------------

/// Состояние дельты, которое живёт внутри одного кадра и обнуляется на
/// каждом новом (см. доку модуля: кадры читаются независимо друг от друга).
struct DeltaState {
    epoch_ns: i64,
    prev_price_ticks: i64,
    prev_qty_lots: i64,
}

fn encode_record(buf: &mut Vec<u8>, r: &Record, st: &mut DeltaState) {
    write_uvarint(buf, r.ev);
    // `wrapping_sub`, не `-`: обоснование — в доке модуля. Время — дельта
    // от эпохи кадра (Decision 23), а не от предыдущей записи; цена и
    // размер — дельта от предыдущей записи этого же кадра.
    write_zigzag(buf, r.exch_ts_ns.wrapping_sub(st.epoch_ns));
    write_zigzag(buf, r.local_ts_ns.wrapping_sub(st.epoch_ns));
    write_zigzag(buf, r.price_ticks.wrapping_sub(st.prev_price_ticks));
    write_zigzag(buf, r.qty_lots.wrapping_sub(st.prev_qty_lots));
    write_uvarint(buf, r.order_id);
    write_zigzag(buf, r.ival);
    // Битовый паттерн `f64`, не значение через арифметику: это резервное
    // поле крейта, для наших событий почти всегда 0.0 (бит-паттерн 0 —
    // один байт), а для любого другого значения даёт точный обратный
    // перевод без вопроса о том, что значит «дельта» для плавающей точки.
    write_uvarint(buf, r.fval.to_bits());

    st.prev_price_ticks = r.price_ticks;
    st.prev_qty_lots = r.qty_lots;
}

fn decode_record(buf: &[u8], pos: &mut usize, st: &mut DeltaState) -> Result<Record, BinlogError> {
    let ev = read_uvarint(buf, pos)?;
    let exch_delta = read_zigzag(buf, pos)?;
    let local_delta = read_zigzag(buf, pos)?;
    let price_delta = read_zigzag(buf, pos)?;
    let qty_delta = read_zigzag(buf, pos)?;
    let order_id = read_uvarint(buf, pos)?;
    let ival = read_zigzag(buf, pos)?;
    let fval_bits = read_uvarint(buf, pos)?;

    let price_ticks = st.prev_price_ticks.wrapping_add(price_delta);
    let qty_lots = st.prev_qty_lots.wrapping_add(qty_delta);
    let record = Record {
        ev,
        exch_ts_ns: st.epoch_ns.wrapping_add(exch_delta),
        local_ts_ns: st.epoch_ns.wrapping_add(local_delta),
        price_ticks,
        qty_lots,
        order_id,
        ival,
        fval: f64::from_bits(fval_bits),
    };

    st.prev_price_ticks = price_ticks;
    st.prev_qty_lots = qty_lots;
    Ok(record)
}

/// Длина префикса эпохи кадра — `i64`-метка времени первой записи
/// (`Writer::write_frame`), которой мерятся дельты времени внутри кадра.
const FRAME_EPOCH_LEN: usize = 8;

/// Разбирает уже разжатое содержимое кадра целиком — читает записи, пока
/// не кончится срез. Число записей нигде не хранится отдельно: конец среза
/// и есть конец кадра, ещё одно поле было бы источником рассогласования.
/// Срез эпохи доказан guard выше (`len < FRAME_EPOCH_LEN` возвращается).
#[allow(clippy::indexing_slicing)]
fn decode_frame_payload(payload: &[u8]) -> Result<Vec<Record>, BinlogError> {
    if payload.len() < FRAME_EPOCH_LEN {
        return Err(BinlogError::Corrupt(format!(
            "кадр короче эпохи ({FRAME_EPOCH_LEN} байт)"
        )));
    }
    let epoch_ns = i64::from_le_bytes(
        payload[0..FRAME_EPOCH_LEN]
            .try_into()
            .map_err(|_| BinlogError::Corrupt("эпоха кадра не легла в i64".into()))?,
    );
    let mut st = DeltaState {
        epoch_ns,
        prev_price_ticks: 0,
        prev_qty_lots: 0,
    };
    let mut pos = FRAME_EPOCH_LEN;
    let mut out = Vec::new();
    while pos < payload.len() {
        out.push(decode_record(payload, &mut pos, &mut st)?);
    }
    Ok(out)
}

/// Число полей одной записи, которые кодирует `encode_record`: `ev`,
/// четыре дельты (`exch_ts`, `local_ts`, `price`, `qty`), `order_id`,
/// `ival`, `fval` — ровно восемь. Если у `Record` появится девятое поле,
/// этой константе и `encode_record`/`decode_record` придётся обновиться
/// вместе, иначе `cargo test binlog` разойдётся с форматом.
const RECORD_FIELD_COUNT: usize = 8;

/// Минимальная длина закодированной записи в байтах. LEB128 `uvarint`
/// значения `0` — ровно один байт `0x00` (`write_uvarint`: цикл пишет байт
/// и останавливается уже на первой итерации, если `v == 0`), и зигзаг
/// сводится к тому же `uvarint` после перестановки знака, так что короче
/// байта варинт по построению кодека быть не может. Восемь полей — минимум
/// восемь байт на запись, независимо от того, какие значения несёт
/// настоящий поток: это структурная нижняя граница формата, а не свойство
/// типичных данных.
const MIN_RECORD_LEN: usize = RECORD_FIELD_COUNT;

/// Максимальная длина закодированной записи: восемь полей, каждое —
/// LEB128-варинт `u64` не длиннее десяти байт (64 бита по семь на байт —
/// `ceil(64 / 7) = 10`, свойство кодека, не данных). Верхняя граница
/// формата — из неё считается `max_frame_bytes_on_disk`.
const MAX_RECORD_LEN: usize = RECORD_FIELD_COUNT * 10;

/// Верхняя граница байт одного кадра **на диске** для `records` записей:
/// префикс длины плюс `compress_bound` zstd от эпохи и записей максимальной
/// длины. Вызывающий (таск 25, `commands::lob::session::FrameSink`)
/// резервирует по ней буфер один раз — ноль аллокаций на кадр после старта
/// вне зависимости от того, какой кадр окажется самым крупным.
pub fn max_frame_bytes_on_disk(records: usize) -> usize {
    let raw = FRAME_EPOCH_LEN.saturating_add(records.saturating_mul(MAX_RECORD_LEN));
    LEN_PREFIX.saturating_add(zstd::zstd_safe::compress_bound(raw))
}

/// Потолок для разжатого тела кадра (Decision 23, ревизия 10): столько байт
/// максимум может занять кадр из `max_records_per_frame` записей — эпоха
/// кадра плюс записи по их минимальному размеру. Оба множителя — не
/// изобретённые числа: `max_records_per_frame` читается из заголовка суток
/// (`Reader::header`), а `MIN_RECORD_LEN` — из формы `encode_record` в этом
/// же файле.
///
/// `saturating_*`, не обычная арифметика: `max_records_per_frame` приходит
/// с диска через `Reader::open` и теоретически может нести испорченное
/// значение, а переполнение при вычислении потолка обязано остаться
/// числом (насыщенным до `usize::MAX`), а не паникой — той же дисциплины
/// держится весь этот читатель (см. `corrupt_length_prefix_does_not_pre_allocate_ahead_of_the_stream`).
///
/// **Второй потолок, над первым.** Насыщения мало: заголовок — такие же
/// данные с диска, как и поле длины, и значение около `u32::MAX` дало бы
/// честно посчитанные тридцать с лишним гигабайт на одну аллокацию. То есть
/// защита от испорченной длины кадра не защищала от испорченного заголовка,
/// а процесс по плану работает неделями без присмотра.
///
/// Верхняя граница не назначается, а выводится из Decision 23: связывающий
/// бюджет там — **150 МБ на символ-сутки после сжатия**, и один кадр по
/// определению не может законно превысить целые сутки. Коэффициент
/// распаковки для этих данных не задан планом, поэтому берётся заведомо
/// щедрый десятикратный: получившиеся полтора гигабайта на порядки больше
/// любого настоящего кадра и на порядки меньше того, чем испорченное поле
/// способно исчерпать хост.
const COMPRESSED_DAY_CEILING_BYTES: usize = 150 * 1024 * 1024;
const MAX_DECOMPRESSION_RATIO: usize = 10;
const HARD_PAYLOAD_CEILING: usize = COMPRESSED_DAY_CEILING_BYTES * MAX_DECOMPRESSION_RATIO;

fn max_frame_payload_bytes(max_records_per_frame: u32) -> usize {
    (max_records_per_frame as usize)
        .saturating_mul(MIN_RECORD_LEN)
        .saturating_add(FRAME_EPOCH_LEN)
        .min(HARD_PAYLOAD_CEILING)
}

// ---------------------------------------------------------------------
// Чтение точно `n` байт с различением «чисто EOF» и «оборвано на середине».
// ---------------------------------------------------------------------

enum ReadStatus {
    Full,
    Partial(usize),
    Eof,
}

/// `Read::read_exact` не годится: при ошибке он не сообщает, сколько байт
/// успел прочитать, а формату нужно различать «ровно ноль байт — конец
/// файла» и «часть кадра есть, но не вся — усечение» (Decision 7). Читает
/// через `read()` в цикле именно ради этого различия.
///
/// Срез `buf[filled..]` доказуемо в границах: условие цикла держит
/// `filled < buf.len()`. Развёртка в `get` невозможна без смены контракта
/// чтения (`Read::read` требует `&mut [u8]`), поэтому заглушка именная,
/// на функцию.
#[allow(clippy::indexing_slicing)]
fn read_upto<R: Read>(r: &mut R, buf: &mut [u8]) -> io::Result<ReadStatus> {
    if buf.is_empty() {
        return Ok(ReadStatus::Full);
    }
    let mut filled = 0;
    while filled < buf.len() {
        match r.read(&mut buf[filled..]) {
            Ok(0) => break,
            Ok(n) => filled += n,
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e),
        }
    }
    Ok(if filled == 0 {
        ReadStatus::Eof
    } else if filled == buf.len() {
        ReadStatus::Full
    } else {
        ReadStatus::Partial(filled)
    })
}

// ---------------------------------------------------------------------
// Writer
// ---------------------------------------------------------------------

/// Пишет один суточный файл: заголовок один раз при создании, дальше кадры
/// по вызову. `scratch`/`compressed` — переиспользуемые буферы, и `compressor`
/// — переиспользуемый контекст zstd: после прогрева (первый кадр вырастил
/// все три до нужной ёмкости) `write_frame` не аллоцирует ни на запись, ни
/// на кадр — это и есть путь «разбор и запись» гейта GC (бюджет «ноль»,
/// SETTLED.md B2; не тот же бюджет, что «≤ 1 на кадр» транспорта).
pub struct Writer<W: Write> {
    inner: W,
    header: Header,
    scratch: Vec<u8>,
    compressed: Vec<u8>,
    /// `zstd::bulk::Compressor`, не `zstd::stream::copy_encode`: последний
    /// создаёт новый контекст сжатия на каждый вызов (streaming `Encoder`
    /// поверх свежего `CCtx`), а `Compressor` держит один `CCtx` и переживает
    /// вызовы `write_frame` — ровно то переиспользование, которого требует
    /// бюджет «ноль после прогрева», а не только «мало и не растёт».
    compressor: zstd::bulk::Compressor<'static>,
}

impl<W: Write> fmt::Debug for Writer<W> {
    /// Ручная реализация, не `#[derive]`: `zstd::bulk::Compressor` не
    /// реализует `Debug`, а печатать `inner`/`scratch`/`compressed` целиком
    /// ради отладочного вывода не нужно — размеры буферов дают тот же сигнал.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Writer")
            .field("header", &self.header)
            .field("scratch_cap", &self.scratch.capacity())
            .field("compressed_cap", &self.compressed.capacity())
            .finish()
    }
}

impl<W: Write> Writer<W> {
    /// `level` — параметр, не константа модуля: число, которым Decision 23
    /// торгует размер против CPU, назначается пилотом 3.1 по измеренному
    /// байту на запись, а не изобретается здесь.
    pub fn create(mut inner: W, header: Header, level: i32) -> Result<Self, BinlogError> {
        validate_header(header)?;
        let mut buf = [0u8; HEADER_LEN];
        buf[0..4].copy_from_slice(&MAGIC);
        buf[4] = VERSION;
        buf[5..13].copy_from_slice(&header.tick_e9.to_le_bytes());
        buf[13..21].copy_from_slice(&header.step_e9.to_le_bytes());
        buf[21..25].copy_from_slice(&header.max_records_per_frame.to_le_bytes());
        inner.write_all(&buf)?;
        let compressor = zstd::bulk::Compressor::new(level)?;
        Ok(Self {
            inner,
            header,
            scratch: Vec::new(),
            compressed: Vec::new(),
            compressor,
        })
    }

    pub fn header(&self) -> Header {
        self.header
    }

    /// Пишет один кадр. Пустой срез — намеренный no-op: нулевая запись не
    /// несёт события и не порождает кадр, который на чтении пришлось бы
    /// отличать от настоящего (кадр с нулём записей был бы неотличим по
    /// смыслу от `MissingSnapshot`, если бы это оказался единственный кадр
    /// файла).
    pub fn write_frame(&mut self, records: &[Record]) -> Result<(), BinlogError> {
        let Some(first) = records.first() else {
            return Ok(());
        };

        // Программная ошибка вызывающего, не порча данных — `assert!`, не
        // `Result` (см. доку модуля и `validate_header`, тот же выбор по
        // тому же принципу: аргумент приходит из кода, а не с диска). Число
        // записей в `records` и `max_records_per_frame` заголовка выбирает
        // один и тот же вызывающий (рекордер, батчинг шага 0.3) — расхождение
        // между ними может быть только его багом. `debug_assert!` был бы не
        // тем выбором: рекордер собирается и работает неделями в `--release`,
        // где `debug_assert!` вырезается, и тогда именно тот кадр, который
        // должен был упасть здесь немедленно и громко, вместо этого ушёл бы
        // на диск — а отвергнет его уже `Reader` (`FrameExceedsHeaderCeiling`),
        // но только при следующем чтении, недели спустя, когда чинить нечего.
        assert!(
            records.len() <= self.header.max_records_per_frame as usize,
            "кадр из {} записей превышает потолок заголовка max_records_per_frame={}: \
             это баг батчинга вызывающего, а не повреждение данных",
            records.len(),
            self.header.max_records_per_frame
        );

        self.scratch.clear();
        // Эпоха кадра — метка первой записи (см. доку модуля): не отдельный
        // параметр звонка, чтобы вызывающему не приходилось поддерживать
        // вторую копию того же значения.
        let epoch_ns = first.exch_ts_ns;
        self.scratch.extend_from_slice(&epoch_ns.to_le_bytes());

        let mut st = DeltaState {
            epoch_ns,
            prev_price_ticks: 0,
            prev_qty_lots: 0,
        };
        for r in records {
            encode_record(&mut self.scratch, r, &mut st);
        }

        self.compressed.clear();
        // Резервируем по границе zstd (`compress_bound`), не по факту: если
        // бы `compressed` не хватило места, `compress_to_buffer` вернул бы
        // ошибку вместо того, чтобы сам вырасти (в отличие от `Vec::push`),
        // а недостаточная ёмкость на холодном или необычно сжимаемом кадре —
        // это не то же самое, что «Corrupt» на чтении.
        let bound = zstd::zstd_safe::compress_bound(self.scratch.len());
        if self.compressed.capacity() < bound {
            self.compressed.reserve(bound - self.compressed.len());
        }
        self.compressor
            .compress_to_buffer(&self.scratch[..], &mut self.compressed)?;

        let len = u32::try_from(self.compressed.len()).map_err(|_| BinlogError::FrameTooLarge {
            len: self.compressed.len(),
        })?;
        self.inner.write_all(&len.to_le_bytes())?;
        self.inner.write_all(&self.compressed)?;
        Ok(())
    }

    pub fn flush(&mut self) -> io::Result<()> {
        self.inner.flush()
    }

    /// Приёмник как есть — вызывающему нужны его счётчики (байты на диске,
    /// таск 25), формат не задет.
    pub fn get_ref(&self) -> &W {
        &self.inner
    }

    pub fn into_inner(self) -> W {
        self.inner
    }
}

// ---------------------------------------------------------------------
// Reader
// ---------------------------------------------------------------------

/// Читает один суточный файл кадр за кадром. `frames_read` различает
/// «файл кончился между кадрами» (нормально) и «файл кончился на первом
/// же кадре» (в сутках нет синтетического снапшота — ошибка, см.
/// `BinlogError::MissingSnapshot`).
///
/// `decompressor` переиспользуется между кадрами по той же причине, что и
/// `compressor` у `Writer` (см. его доку): держит один `DCtx` вместо того,
/// чтобы заводить новый на каждый вызов. Не заявлено как часть бюджета GC
/// «ноль на событие» — тот бюджет про путь разбор-и-запись рекордера, а
/// чтение обслуживает `verify`/`export`/`markout`, но переиспользование
/// не стоит ничего и держит `Reader` симметричным `Writer`.
pub struct Reader<R: Read> {
    inner: R,
    header: Header,
    frames_read: u64,
    decompressor: zstd::bulk::Decompressor<'static>,
}

impl<R: Read> fmt::Debug for Reader<R> {
    /// Ручная реализация, не `#[derive]`: `zstd::bulk::Decompressor` не
    /// реализует `Debug` (см. ту же причину у `Writer` выше).
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Reader")
            .field("header", &self.header)
            .field("frames_read", &self.frames_read)
            .finish()
    }
}

impl<R: Read> Reader<R> {
    /// Читает и проверяет заголовок. Ничего не помнит о других файлах —
    /// это и есть проверяемое свойство «сутки читаются с первого байта»:
    /// вся нужная для декодирования информация приходит из этого же потока.
    ///
    /// Заголовок читается в **два** шага, не один: сначала `magic` и
    /// `version` (`MAGIC_VERSION_LEN`), и версия проверяется **раньше**,
    /// чем читается хвост заголовка (`HEADER_TAIL_LEN`). У версии 1 не было
    /// поля `max_records_per_frame`, и её хвост на 4 байта короче — прочитать
    /// те же 20 байт из файла версии 1 значило бы принять первые 4 байта
    /// следующего поля (длину первого кадра) за часть заголовка версии 2:
    /// тихое неверное чтение вместо понятной ошибки версии (см. тест
    /// `old_version_file_is_rejected_not_misread`).
    pub fn open(mut inner: R) -> Result<Self, BinlogError> {
        let mut prefix = [0u8; MAGIC_VERSION_LEN];
        match read_upto(&mut inner, &mut prefix)? {
            ReadStatus::Full => {}
            ReadStatus::Partial(got) => {
                return Err(BinlogError::TruncatedHeader {
                    got,
                    want: HEADER_LEN,
                })
            }
            ReadStatus::Eof => {
                return Err(BinlogError::TruncatedHeader {
                    got: 0,
                    want: HEADER_LEN,
                })
            }
        }
        if prefix[0..4] != MAGIC {
            return Err(BinlogError::BadMagic {
                got: [prefix[0], prefix[1], prefix[2], prefix[3]],
            });
        }
        let version = prefix[4];
        if version != VERSION {
            return Err(BinlogError::UnsupportedVersion { got: version });
        }

        let mut tail = [0u8; HEADER_TAIL_LEN];
        match read_upto(&mut inner, &mut tail)? {
            ReadStatus::Full => {}
            ReadStatus::Partial(got) => {
                return Err(BinlogError::TruncatedHeader {
                    got: MAGIC_VERSION_LEN + got,
                    want: HEADER_LEN,
                })
            }
            ReadStatus::Eof => {
                return Err(BinlogError::TruncatedHeader {
                    got: MAGIC_VERSION_LEN,
                    want: HEADER_LEN,
                })
            }
        }
        let header = Header {
            tick_e9: i64::from_le_bytes(
                tail[0..8]
                    .try_into()
                    .map_err(|_| BinlogError::Corrupt("tick заголовка не лёг в i64".into()))?,
            ),
            step_e9: i64::from_le_bytes(
                tail[8..16]
                    .try_into()
                    .map_err(|_| BinlogError::Corrupt("step заголовка не лёг в i64".into()))?,
            ),
            max_records_per_frame: u32::from_le_bytes(
                tail[16..20]
                    .try_into()
                    .map_err(|_| BinlogError::Corrupt("потолок кадра не лёг в u32".into()))?,
            ),
        };
        validate_header(header)?;
        Ok(Self {
            inner,
            header,
            frames_read: 0,
            decompressor: zstd::bulk::Decompressor::new()?,
        })
    }

    pub fn header(&self) -> Header {
        self.header
    }

    /// Возвращает следующий кадр как список записей, `None` на чистом конце
    /// файла **после** хотя бы одного прочитанного кадра. Любая нехватка
    /// байт внутри объявленной длины кадра — `ShortRead`, никогда не
    /// `Ok(None)` и никогда не паника (Decision 7: усечение обязано быть
    /// видно как короткое чтение, а не как молчаливый пустой хвост).
    ///
    /// Срезы чанка ниже доказаны: `want ≤ READ_CHUNK = chunk.len()` через
    /// `min`, `n` из `Partial(n)` не превышает запрошенного по контракту
    /// `read_upto`; проверка через `get` в цикле ввода-вывода — мёртвый код.
    #[allow(clippy::indexing_slicing)]
    pub fn read_frame(&mut self) -> Result<Option<Vec<Record>>, BinlogError> {
        let mut len_buf = [0u8; LEN_PREFIX];
        match read_upto(&mut self.inner, &mut len_buf)? {
            ReadStatus::Eof => {
                return if self.frames_read == 0 {
                    Err(BinlogError::MissingSnapshot)
                } else {
                    Ok(None)
                };
            }
            ReadStatus::Partial(got) => {
                return Err(BinlogError::ShortRead {
                    context: "длина кадра",
                    want: LEN_PREFIX,
                    got,
                })
            }
            ReadStatus::Full => {}
        }
        let len = u32::from_le_bytes(len_buf) as usize;

        // `len` пришла прямо с диска непроверенной и может быть любым
        // значением до `u32::MAX` (~4.3 ГиБ) из-за одного перевёрнутого
        // бита — испорченная длина не редкость именно для того потока
        // (крах посреди записи, битые сектора), для которого этот формат
        // и спроектирован. Аллоцировать `len` байт заранее значило бы
        // проверять, хватает ли на диске байт, уже потратив память под
        // это же чтение: неудачная аллокация такого размера — это abort
        // процесса (не перехватываемая паника), что прямо противоречит
        // «никогда не паника» из шапки модуля. Поэтому читаем кусками:
        // буфер растёт только на то, что реально пришло, и испорченная
        // длина обрывается на `ShortRead` первого недостающего куска, а не
        // на попытке выделить гигабайты впрок.
        const READ_CHUNK: usize = 64 * 1024;
        let mut compressed = Vec::with_capacity(len.min(READ_CHUNK));
        let mut got = 0usize;
        let mut chunk = [0u8; READ_CHUNK];
        while got < len {
            let want = (len - got).min(READ_CHUNK);
            match read_upto(&mut self.inner, &mut chunk[..want])? {
                ReadStatus::Full => {
                    compressed.extend_from_slice(&chunk[..want]);
                    got += want;
                }
                ReadStatus::Partial(n) => {
                    compressed.extend_from_slice(&chunk[..n]);
                    got += n;
                    return Err(BinlogError::ShortRead {
                        context: "тело кадра",
                        want: len,
                        got,
                    });
                }
                ReadStatus::Eof => {
                    // `len > got`, иначе цикл уже завершился бы выше.
                    // Значит объявленная длина требует байт, которых на
                    // диске больше нет.
                    return Err(BinlogError::ShortRead {
                        context: "тело кадра",
                        want: len,
                        got,
                    });
                }
            }
        }

        // `bulk::Decompressor::decompress` с явной ёмкостью, не
        // `stream::decode_all`: `decode_all` растит буфер по мере разжатия
        // и не отказывается сам ни при каком размере — ровно то
        // «декомпрессия сначала, проверка после» (уже после аллокации),
        // от которого Decision 23 (ревизия 10) требует уйти. Ёмкость здесь
        // — потолок из заголовка (`max_frame_payload_bytes`), и если
        // распаковка требует больше, zstd возвращает ошибку **до** того,
        // как эти байты выделены (см. `Decompressor::decompress`/`WriteBuf::
        // write_from` в крейте `zstd-safe`: буфер получает ровно
        // запрошенную ёмкость один раз и не растёт).
        let ceiling_bytes = max_frame_payload_bytes(self.header.max_records_per_frame);
        let payload = self
            .decompressor
            .decompress(&compressed, ceiling_bytes)
            .map_err(|_| BinlogError::FrameExceedsHeaderCeiling {
                max_records_per_frame: self.header.max_records_per_frame,
                ceiling_bytes,
            })?;
        let records = decode_frame_payload(&payload)?;
        self.frames_read += 1;
        Ok(Some(records))
    }
}

#[cfg(test)]
mod tests;
