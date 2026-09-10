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
const DAY_BUDGET_BYTES: usize = 150 * 1024 * 1024;
const MAX_DECOMPRESSION_RATIO: usize = 10;
const HARD_PAYLOAD_CEILING: usize = DAY_BUDGET_BYTES * MAX_DECOMPRESSION_RATIO;

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
mod tests {

    /// Заголовок — такие же данные с диска, как и поле длины кадра, и потолок,
    /// выведенный только из него, испорченным заголовком обходится. Проверяется
    /// то, что второй потолок существует и связывает: `u32::MAX` записей дали бы
    /// тридцать с лишним гигабайт, а обязаны упереться в границу из Decision 23.
    #[test]
    fn a_corrupt_header_cannot_raise_the_ceiling_past_the_day_budget() {
        let honest = super::max_frame_payload_bytes(1_000);
        assert_eq!(
            honest,
            1_000 * super::MIN_RECORD_LEN + super::FRAME_EPOCH_LEN,
            "на честном значении потолок обязан считаться ровно по записям"
        );

        let corrupt = super::max_frame_payload_bytes(u32::MAX);
        assert_eq!(
            corrupt,
            super::HARD_PAYLOAD_CEILING,
            "испорченный заголовок обязан упираться в границу, а не в своё произведение"
        );
        assert!(
            (u32::MAX as usize) * super::MIN_RECORD_LEN > corrupt,
            "иначе тест не проверяет ничего: произведение обязано быть больше границы"
        );
    }

    use super::*;
    use crate::alloc_count;

    const TICK_E9: i64 = 100_000; // 0.0001, как в тестах book.rs
    const STEP_E9: i64 = 1_000_000; // 0.001

    /// Потолок заголовка по умолчанию для тестов, которым сам потолок не
    /// важен — round trip, границы суток, усечение и т.п. Не значение,
    /// которое использует рекордер (то назначает вызывающий при создании
    /// файла, не этот модуль), а запас с большим отступом над самым
    /// крупным кадром, который где-либо в этом наборе тестов пишется
    /// одним вызовом `write_frame` через общий `header()` — крупнейший тут
    /// `PER_FRAME = 20_000` в `round_trips_one_million_events` и в тестах
    /// аллокаций ниже. Тесты про сам потолок (`..._exceeds_the_header_ceiling`,
    /// `..._at_exactly_the_maximum`, `writer_refuses_frame_exceeding_...`)
    /// собирают свой `Header` с маленьким явным значением, а не берут этот.
    const DEFAULT_TEST_MAX_RECORDS_PER_FRAME: u32 = 1_000_000;

    fn header() -> Header {
        Header {
            tick_e9: TICK_E9,
            step_e9: STEP_E9,
            max_records_per_frame: DEFAULT_TEST_MAX_RECORDS_PER_FRAME,
        }
    }

    /// Флаг снапшота бид-уровня, взятый у крейта, а не выдуманный: доказывает
    /// «поля — надмножество `Event`» на конкретном значении, которое реально
    /// использует экспортёр (шаг 6.1), а не произвольное число теста.
    fn ev_snapshot_bid() -> u64 {
        hftbacktest::types::LOCAL_BID_DEPTH_SNAPSHOT_EVENT
    }
    fn ev_delta_ask() -> u64 {
        hftbacktest::types::LOCAL_ASK_DEPTH_EVENT
    }
    fn ev_trade_buy() -> u64 {
        hftbacktest::types::LOCAL_BUY_TRADE_EVENT
    }

    fn rec(ev: u64, exch_ts_ns: i64, local_ts_ns: i64, price_ticks: i64, qty_lots: i64) -> Record {
        Record {
            ev,
            exch_ts_ns,
            local_ts_ns,
            price_ticks,
            qty_lots,
            order_id: 0,
            ival: 0,
            fval: 0.0,
        }
    }

    fn write_all(hdr: Header, frames: &[Vec<Record>]) -> Vec<u8> {
        let mut w = Writer::create(Vec::new(), hdr, zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
        for frame in frames {
            w.write_frame(frame).unwrap();
        }
        w.into_inner()
    }

    fn read_all(bytes: &[u8]) -> (Header, Vec<Vec<Record>>) {
        let mut r = Reader::open(bytes).unwrap();
        let hdr = r.header();
        let mut frames = Vec::new();
        while let Some(f) = r.read_frame().unwrap() {
            frames.push(f);
        }
        (hdr, frames)
    }

    // -----------------------------------------------------------------
    // Базовый round trip.
    // -----------------------------------------------------------------

    #[test]
    fn header_round_trips() {
        let bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 10, 20, 1, 1)]]);
        let mut r = Reader::open(&bytes[..]).unwrap();
        assert_eq!(r.header(), header());
        assert!(r.read_frame().unwrap().is_some());
        assert!(r.read_frame().unwrap().is_none());
    }

    #[test]
    fn records_round_trip_across_several_frames() {
        let frame0 = vec![
            rec(ev_snapshot_bid(), 1_000, 1_500, 100, 5),
            rec(ev_snapshot_bid(), 1_000, 1_600, 99, 3),
            rec(ev_delta_ask(), 1_000, 1_700, 101, 4),
        ];
        let frame1 = vec![
            rec(ev_delta_ask(), 5_000, 5_100, 105, 0),
            rec(ev_trade_buy(), 5_050, 5_200, 100, 2),
        ];
        let bytes = write_all(header(), &[frame0.clone(), frame1.clone()]);
        let (hdr, frames) = read_all(&bytes);
        assert_eq!(hdr, header());
        assert_eq!(frames, vec![frame0, frame1]);
    }

    /// Второй кадр не должен зависеть от состояния первого: дельта внутри
    /// него считается заново от нуля. Если бы состояние переносилось между
    /// кадрами, эта запись расшифровалась бы в другую цену.
    #[test]
    fn delta_state_resets_at_frame_boundary() {
        let frame0 = vec![rec(ev_snapshot_bid(), 0, 0, 1_000_000, 500)];
        let frame1 = vec![rec(ev_delta_ask(), 1, 1, 7, 2)];
        let bytes = write_all(header(), &[frame0, frame1]);
        let (_, frames) = read_all(&bytes);
        assert_eq!(frames[1][0].price_ticks, 7);
        assert_eq!(frames[1][0].qty_lots, 2);
    }

    /// Запись без событий — no-op: в потоке не появляется ни одного байта,
    /// а не кадр с нулём записей.
    #[test]
    fn writing_an_empty_slice_writes_nothing() {
        let mut w = Writer::create(Vec::new(), header(), zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
        w.write_frame(&[]).unwrap();
        let bytes = w.into_inner();
        assert_eq!(bytes.len(), HEADER_LEN);
    }

    // -----------------------------------------------------------------
    // Требование 1: round trip на 10^6 событий (done-condition шага 0.2).
    // -----------------------------------------------------------------

    #[test]
    fn round_trips_one_million_events() {
        const TOTAL: usize = 1_000_000;
        const PER_FRAME: usize = 20_000;

        let mut w = Writer::create(Vec::new(), header(), 1).unwrap();
        let mut written = 0usize;
        let mut expected: Vec<Record> = Vec::with_capacity(TOTAL);
        while written < TOTAL {
            let n = PER_FRAME.min(TOTAL - written);
            let mut frame = Vec::with_capacity(n);
            for i in 0..n {
                let idx = (written + i) as i64;
                let ev = if idx % 2 == 0 {
                    ev_delta_ask()
                } else {
                    ev_trade_buy()
                };
                // Знакопеременный шаг: проверяет, что дельты уверенно берут
                // и рост, и падение цены/размера внутри одного кадра.
                let r = rec(
                    ev,
                    1_700_000_000_000_000_000 + idx * 1_000,
                    1_700_000_000_000_500_000 + idx * 1_000,
                    1_000_000 + if idx % 3 == 0 { idx } else { -idx },
                    100 + (idx % 97),
                );
                frame.push(r);
                expected.push(r);
            }
            w.write_frame(&frame).unwrap();
            written += n;
        }
        let bytes = w.into_inner();

        let (hdr, frames) = read_all(&bytes);
        assert_eq!(hdr, header());
        let got: Vec<Record> = frames.into_iter().flatten().collect();
        assert_eq!(got.len(), TOTAL);
        assert_eq!(got, expected);
    }

    // -----------------------------------------------------------------
    // Требование архитектуры (A2 через план): один и тот же бинлог,
    // прочитанный дважды, даёт побайтово одинаковый вывод. Тест ловит
    // недетерминизм, прокрашивающийся в тракт декодирования (порядок обхода
    // хеш-таблиц, время, RNG): читает ОДНИ И ТЕ ЖЕ байты двумя независимыми
    // Reader и сравнивает покадрово, а не только итогом.
    // -----------------------------------------------------------------

    #[test]
    fn same_bytes_twice_give_byte_identical_frames() {
        let day = write_all(
            header(),
            &[
                vec![
                    rec(ev_snapshot_bid(), 0, 1, 10, 10),
                    rec(ev_snapshot_bid(), 0, 1, 9, 3),
                ],
                vec![
                    rec(ev_delta_ask(), 1_000, 1_001, 11, 9),
                    rec(ev_trade_buy(), 1_000, 1_001, 11, 1),
                    rec(ev_delta_ask(), 1_000, 1_001, 21, 19),
                ],
            ],
        );
        let (_, first) = read_all(&day);
        let (_, second) = read_all(&day);
        assert_eq!(first.len(), second.len(), "число кадров обязано совпасть");
        for (a, b) in first.iter().zip(second.iter()) {
            assert_eq!(a, b, "кадры обязаны совпасть побайтово-полностью");
        }
    }

    /// Сид-корпус фаззера (`fuzz/corpus/reader`) гоняется и в стабильном
    /// наборе: ни один вход не вправе ронять читатель — ни паникой, ни
    /// бесконечностью. Потолок кадров тот же, что в фазз-таргете.
    #[test]
    fn fuzz_seed_corpus_never_panics_nor_hangs() {
        let mut names: Vec<_> = std::fs::read_dir("fuzz/corpus/reader")
            .expect("сид-корпус обязан существовать")
            .map(|e| e.unwrap().path())
            .collect();
        names.sort();
        assert!(!names.is_empty(), "пустой корпус ничего не проверяет");
        for path in names {
            let data = std::fs::read(&path).unwrap();
            let Ok(mut reader) = Reader::open(&data[..]) else {
                continue;
            };
            let _ = reader.header();
            let mut frames = 0usize;
            while let Ok(Some(_)) = reader.read_frame() {
                frames += 1;
                assert!(frames < 4096, "вход {:?} не заканчивается", path);
            }
        }
    }

    // -----------------------------------------------------------------
    // Требование 2: сутки читаются с первого байта без предыдущих суток.
    // -----------------------------------------------------------------

    /// Два «дня» с **разными** `tickSize` (биржа сменила шаг между ними,
    /// как и предупреждает Decision 7) кодируются независимо. Открытие
    /// второго не видит байт первого вообще — они лежат в отдельных `Vec`.
    #[test]
    fn each_day_file_decodes_standing_alone() {
        let day1_header = Header {
            tick_e9: 100_000,
            step_e9: 1_000_000,
            max_records_per_frame: DEFAULT_TEST_MAX_RECORDS_PER_FRAME,
        };
        let day2_header = Header {
            tick_e9: 50_000, // другой шаг цены — как после смены на бирже
            step_e9: 2_000_000,
            max_records_per_frame: DEFAULT_TEST_MAX_RECORDS_PER_FRAME,
        };
        let day1 = write_all(day1_header, &[vec![rec(ev_snapshot_bid(), 0, 1, 10, 10)]]);
        let day2 = write_all(day2_header, &[vec![rec(ev_snapshot_bid(), 0, 1, 20, 20)]]);

        // День 2 декодируется из собственного среза, день 1 в эту функцию
        // не передаётся вообще — не только логически, а буквально.
        let (hdr2, frames2) = read_all(&day2);
        assert_eq!(hdr2, day2_header);
        assert_eq!(frames2[0][0].price_ticks, 20);

        let (hdr1, frames1) = read_all(&day1);
        assert_eq!(hdr1, day1_header);
        assert_eq!(frames1[0][0].price_ticks, 10);
    }

    // -----------------------------------------------------------------
    // Требование 3: усечённый последний кадр — короткое чтение.
    // -----------------------------------------------------------------

    #[test]
    fn truncated_final_frame_is_a_short_read_not_corruption() {
        let good = vec![rec(ev_snapshot_bid(), 0, 1, 10, 10)];
        let mut w = Writer::create(Vec::new(), header(), zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
        w.write_frame(&good).unwrap();
        w.write_frame(&[rec(ev_delta_ask(), 5, 6, 11, 9)]).unwrap();
        let mut bytes = w.into_inner();

        // Обрезаем файл на середине второго кадра: первый читается штатно,
        // на втором должен быть именно `ShortRead`, не паника и не пустой
        // результат, выданный за нормальный конец файла.
        bytes.truncate(bytes.len() - 3);

        let mut r = Reader::open(&bytes[..]).unwrap();
        assert!(r.read_frame().unwrap().is_some(), "первый кадр цел");
        let err = r.read_frame().unwrap_err();
        assert!(
            matches!(err, BinlogError::ShortRead { .. }),
            "ожидался ShortRead, получено {err:?}"
        );
    }

    // -----------------------------------------------------------------
    // Требование 4: точное сохранение цены/размера на значениях, которые
    // f64 не может держать точно.
    // -----------------------------------------------------------------

    #[test]
    fn deltas_survive_values_f64_cannot_hold_exactly() {
        // 2^53 + 1 — первое целое, которое f64 не представляет точно.
        const BEYOND_F64: i64 = 9_007_199_254_740_993;
        let frame = vec![
            rec(ev_snapshot_bid(), 0, 0, i64::MAX, i64::MAX),
            // Соседняя запись на противоположном краю диапазона: обычное
            // `i64::MIN - i64::MAX` переполняет i64 и в debug-сборке
            // запаниковало бы — здесь дельта берётся `wrapping_sub`.
            rec(ev_delta_ask(), 1, 1, i64::MIN, i64::MIN),
            rec(ev_delta_ask(), 2, 2, BEYOND_F64, -BEYOND_F64),
            rec(ev_delta_ask(), 3, 3, 0, 0),
        ];
        let bytes = write_all(header(), std::slice::from_ref(&frame));
        let (_, frames) = read_all(&bytes);
        assert_eq!(frames[0], frame, "цена и размер обязаны совпасть побитово");

        // f64 не смог бы: явная демонстрация того, зачем эта проверка нужна.
        assert_ne!(
            BEYOND_F64 as f64 as i64, BEYOND_F64,
            "иначе тест не проверяет то, что заявлено"
        );
    }

    /// Тест выше гоняет в края `i64` только `price_ticks`/`qty_lots` —
    /// `exch_ts_ns`/`local_ts_ns` там остаются маленькими. У времени своя
    /// дельта (от эпохи кадра, а не от предыдущей записи, см. доку модуля),
    /// и `local_ts_ns` никак не связан с `exch_ts_ns` (может разъехаться с
    /// ним произвольно), так что оба поля отдельно должны пережить край
    /// `i64` — именно там, где `exch_ts_ns.wrapping_sub(st.epoch_ns)` /
    /// `local_ts_ns.wrapping_sub(st.epoch_ns)` обязаны остаться честной
    /// биекцией, а не обычным `-`, который в debug-сборке запаниковал бы.
    #[test]
    fn timestamp_deltas_survive_epoch_at_i64_extremes() {
        let frame = vec![
            // Эпоха кадра = exch_ts_ns первой записи (`Writer::write_frame`).
            rec(ev_snapshot_bid(), i64::MIN, i64::MAX, 1, 1),
            rec(ev_delta_ask(), i64::MAX, i64::MIN, 2, 2),
            rec(ev_trade_buy(), 0, 0, 3, 3),
        ];
        let bytes = write_all(header(), std::slice::from_ref(&frame));
        let (_, frames) = read_all(&bytes);
        assert_eq!(
            frames[0], frame,
            "exch_ts_ns и local_ts_ns обязаны совпасть побитово даже на краях i64"
        );
    }

    // -----------------------------------------------------------------
    // Требование 4б: `order_id`/`ival`/`fval` — тоже часть записи, не
    // только цена/размер/время. `rec()` выше кладёт все три в ноль, и
    // ни один тест до этого добавления не проверял, что кодек вообще
    // трогает эти поля правильно: `zigzag(0)` и `uvarint(0)` — один и
    // тот же байт 0x00, так что перепутанные местами `write_zigzag`/
    // `write_uvarint` на `ival` тоже дали бы зелёный набор.
    // -----------------------------------------------------------------

    // Восемь аргументов — один в один поля `Record` (см. `clippy::
    // too_many_arguments`); тестовый конструктор, не публичное API,
    // группировать их незачем.
    #[allow(clippy::too_many_arguments)]
    fn rec_full(
        ev: u64,
        exch_ts_ns: i64,
        local_ts_ns: i64,
        price_ticks: i64,
        qty_lots: i64,
        order_id: u64,
        ival: i64,
        fval: f64,
    ) -> Record {
        Record {
            ev,
            exch_ts_ns,
            local_ts_ns,
            price_ticks,
            qty_lots,
            order_id,
            ival,
            fval,
        }
    }

    #[test]
    fn order_id_ival_and_fval_round_trip_nonzero_values() {
        // `order_id` не помещается в младшие 32 бита; `ival` знакопеременный
        // (крейт хранит его как `i64`, кодируется зигзагом, не как беззнаковый
        // varint); `fval` — ненулевой битовый паттерн, но не NaN (`Record`
        // сравнивается через derived `PartialEq`, а `NaN != NaN` сломал бы
        // `assert_eq!` независимо от того, что кодек сохранил бит-в-бит).
        let frame = vec![
            rec_full(ev_snapshot_bid(), 0, 1, 10, 10, 0, 0, 0.0),
            rec_full(
                ev_delta_ask(),
                2,
                3,
                11,
                9,
                u64::MAX,
                i64::MIN,
                f64::MIN_POSITIVE,
            ),
            rec_full(
                ev_trade_buy(),
                4,
                5,
                9,
                11,
                1,
                i64::MAX,
                -std::f64::consts::PI,
            ),
            rec_full(ev_delta_ask(), 6, 7, 12, 8, 123_456_789, -1, 1.0),
        ];
        let bytes = write_all(header(), std::slice::from_ref(&frame));
        let (_, frames) = read_all(&bytes);
        assert_eq!(
            frames[0], frame,
            "order_id/ival/fval обязаны совпасть побитово, а не только price/qty/время"
        );
    }

    // -----------------------------------------------------------------
    // Требование 5: аллокации на запись не растут с числом событий.
    // -----------------------------------------------------------------

    /// Бюджет GC (`PLAN.md` шаг 0.2; SETTLED.md B2) — буквально ноль
    /// аллокаций на событие после прогрева, не просто «не растёт кратно».
    /// Раньше эта проверка допускала рост вплоть до `small * 2 + 4` —
    /// свойство, которое зелёный тест давал бы и при паре аллокаций на
    /// кадр (так и было: `zstd::stream::copy_encode` заводил новый
    /// контекст сжатия на каждый вызов). `Writer` теперь держит
    /// переиспользуемый `zstd::bulk::Compressor`, и бюджет проверяется
    /// как заявлено в плане.
    #[test]
    fn write_path_allocates_nothing_after_warmup() {
        const FRAMES: usize = 4;

        fn synth(n: usize, seed: i64) -> Vec<Record> {
            (0..n)
                .map(|i| {
                    let idx = seed + i as i64;
                    rec(
                        ev_delta_ask(),
                        1_000_000 + idx,
                        1_000_100 + idx,
                        500 + (idx % 13),
                        10 + (idx % 7),
                    )
                })
                .collect()
        }

        fn allocations_for(events_per_frame: usize) -> u64 {
            // `io::sink()`, не `Vec::new()`: сток теста не должен участвовать
            // в замере. Растущий `Vec<u8>`, копящий байты всех кадров подряд,
            // сам периодически перевыделяется (амортизированное удвоение
            // ёмкости) — это аллокации стока теста, а не `write_frame`, и
            // с `Vec::new()` они попадали бы в тот же счётчик, маскируя
            // настоящий бюджет под ложным «не совсем ноль».
            let mut w =
                Writer::create(io::sink(), header(), zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();

            // Прогрев: кадр той же формы вне замера, чтобы `scratch`,
            // `compressed` и `compressor` выросли до итоговой ёмкости
            // заранее — гейт GC требует «ноль после прогрева», а не
            // «ноль с первого кадра» (тот же принцип, что в
            // `alloc_count::tests`).
            w.write_frame(&synth(events_per_frame, 0)).unwrap();

            // Кадры строятся заранее и вне замера, чтобы считать только
            // аллокации самого `write_frame`, а не тестовых данных. Сдвиг
            // `seed` — кратное 91 (= НОК(13, 7), периодов `idx % 13` и
            // `idx % 7` выше): фаза обоих циклов совпадает с прогревом
            // (`seed = 0`), так что каждый кадр кодируется в те же самые
            // байты, что и прогревочный, — иначе кадр со сдвинутой фазой
            // мог бы дать дельту на один байт длиннее и вызвать рост
            // `scratch`/`compressed`, не имеющий отношения к бюджету GC.
            let batches: Vec<Vec<Record>> = (0..FRAMES)
                .map(|k| synth(events_per_frame, (k as i64 + 1) * 91))
                .collect();

            let (_, counts) = alloc_count::measure(|| {
                for batch in &batches {
                    w.write_frame(batch).unwrap();
                }
            });
            counts.allocations
        }

        for events_per_frame in [1_000usize, 10_000] {
            let allocations = allocations_for(events_per_frame);
            assert_eq!(
                allocations, 0,
                "путь разбор-и-запись обязан быть нулевым после прогрева \
                 ({events_per_frame} записей/кадр, {FRAMES} кадра): было {allocations}"
            );
        }
    }

    /// То же самое, но на масштабе, который SETTLED.md B2 называет
    /// буквально: «измеряется проигрыванием 10^6 событий из заранее
    /// заполненного буфера» — не 40 тысяч, как в тесте выше.
    ///
    /// Дельты здесь ограничены по модулю (как в тесте выше), а не растут
    /// без края с `idx`, как в `round_trips_one_million_events`: та форма
    /// нарочно нужна для проверки корректности на больших скачках, но
    /// делает байтовую длину кадра зависимой от того, насколько далеко
    /// зашёл прогон, — здесь же кадр под замером обязан кодироваться
    /// ровно в те же байты, что и прогревочный, на любом из миллиона
    /// событий, иначе замер ловил бы рост данных, а не аллокатор.
    #[test]
    fn write_path_allocates_nothing_replaying_one_million_events() {
        const TOTAL: usize = 1_000_000;
        const PER_FRAME: usize = 20_000;

        fn synth(n: usize, seed: i64) -> Vec<Record> {
            (0..n)
                .map(|i| {
                    let idx = seed + i as i64;
                    rec(
                        ev_delta_ask(),
                        1_000_000 + idx,
                        1_000_100 + idx,
                        500 + (idx % 13),
                        10 + (idx % 7),
                    )
                })
                .collect()
        }

        // `io::sink()`, не `Vec::new()` — см. комментарий в тесте выше.
        let mut w = Writer::create(io::sink(), header(), 1).unwrap();

        // Прогрев вне замера, как и в тесте выше.
        w.write_frame(&synth(PER_FRAME, 0)).unwrap();

        // Оставшиеся 980 000 событий, кадрами заранее — под замером
        // остаётся только сам `write_frame`. Сдвиг `seed` кратен 91
        // (см. комментарий в тесте выше), чтобы фаза `idx % 13`/`idx % 7`
        // всегда совпадала с прогревом.
        let batches: Vec<Vec<Record>> = (1..TOTAL / PER_FRAME)
            .map(|k| synth(PER_FRAME, (k as i64) * 91))
            .collect();

        let (_, counts) = alloc_count::measure(|| {
            for batch in &batches {
                w.write_frame(batch).unwrap();
            }
        });
        assert_eq!(
            counts.allocations, 0,
            "путь разбор-и-запись обязан быть нулевым после прогрева на \
             {TOTAL} событиях: было {}",
            counts.allocations
        );
    }

    // -----------------------------------------------------------------
    // Требование 6: вырожденный ввод — ошибка, никогда не паника и не
    // молчаливый пустой результат.
    // -----------------------------------------------------------------

    #[test]
    fn empty_file_is_an_error() {
        let err = Reader::open(&b""[..]).unwrap_err();
        assert_eq!(
            err,
            BinlogError::TruncatedHeader {
                got: 0,
                want: HEADER_LEN
            }
        );
    }

    #[test]
    fn header_only_file_is_an_error_not_an_empty_result() {
        // Валидный заголовок, ни одного кадра — в сутках нет даже
        // синтетического снапшота, которого требует Decision 7.
        let w = Writer::create(Vec::new(), header(), zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
        let bytes = w.into_inner();
        assert_eq!(bytes.len(), HEADER_LEN);

        let mut r = Reader::open(&bytes[..]).unwrap();
        let err = r.read_frame().unwrap_err();
        assert_eq!(err, BinlogError::MissingSnapshot);
    }

    #[test]
    fn frame_claiming_more_bytes_than_remain_is_an_error() {
        let mut w = Writer::create(Vec::new(), header(), zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
        w.write_frame(&[rec(ev_snapshot_bid(), 0, 1, 10, 10)])
            .unwrap();
        let mut bytes = w.into_inner();

        // Читаем реальную длину первого кадра и завышаем её, оставляя тело
        // прежним: заявленная длина теперь превышает то, что реально есть.
        let len_at = HEADER_LEN;
        let real_len = u32::from_le_bytes(bytes[len_at..len_at + 4].try_into().unwrap());
        let inflated = real_len + 10_000;
        bytes[len_at..len_at + 4].copy_from_slice(&inflated.to_le_bytes());

        let mut r = Reader::open(&bytes[..]).unwrap();
        let err = r.read_frame().unwrap_err();
        assert!(
            matches!(err, BinlogError::ShortRead { .. }),
            "ожидался ShortRead, получено {err:?}"
        );
    }

    /// Как тест выше, но с длиной, завышенной не на 10 000 байт, а на
    /// сотни мегабайт — там, где `vec![0u8; len]` до проверки остатка
    /// потока попытался бы выделить эти мегабайты впрок. Неудачная
    /// аллокация такого масштаба — это `abort` процесса (не перехватываемая
    /// паника), что противоречит «никогда не паника» из шапки модуля;
    /// `Reader::read_frame` обязан читать кусками и остановиться на
    /// `ShortRead`, не аллоцируя больше, чем реально пришло.
    #[test]
    fn corrupt_length_prefix_does_not_pre_allocate_ahead_of_the_stream() {
        const HUGE: u32 = 200 * 1024 * 1024; // 200 МиБ — заведомо больше, чем есть на "диске"
        let mut w = Writer::create(Vec::new(), header(), zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
        w.write_frame(&[rec(ev_snapshot_bid(), 0, 1, 10, 10)])
            .unwrap();
        let mut bytes = w.into_inner();

        let len_at = HEADER_LEN;
        bytes[len_at..len_at + 4].copy_from_slice(&HUGE.to_le_bytes());

        let mut r = Reader::open(&bytes[..]).unwrap();
        let (result, counts) = alloc_count::measure(|| r.read_frame());
        let err = result.unwrap_err();
        assert!(
            matches!(err, BinlogError::ShortRead { .. }),
            "ожидался ShortRead, получено {err:?}"
        );
        assert!(
            counts.bytes < 4 * 1024 * 1024,
            "испорченная длина ({HUGE} байт) не должна аллоцировать вперёд \
             объявленного объёма — реально выделено {} байт",
            counts.bytes
        );
    }

    #[test]
    fn truncated_header_prefix_is_an_error() {
        // Короче даже magic+version (`MAGIC_VERSION_LEN`) — усечение обязано
        // ловиться на первом шаге чтения заголовка, до того как код вообще
        // пытается истолковать эти байты как magic (см. доку `Reader::open`
        // про два шага чтения).
        let bytes = [0u8; MAGIC_VERSION_LEN - 1];
        let err = Reader::open(&bytes[..]).unwrap_err();
        assert_eq!(
            err,
            BinlogError::TruncatedHeader {
                got: MAGIC_VERSION_LEN - 1,
                want: HEADER_LEN,
            }
        );
    }

    #[test]
    fn truncated_header_tail_is_an_error() {
        // Magic и версия целы и валидны, хвост (tick_e9/step_e9/
        // max_records_per_frame) обрезан на один байт — усечение ловится на
        // втором шаге чтения, не читается как валидные, но сдвинутые байты.
        let mut bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 0, 1, 1, 1)]]);
        bytes.truncate(HEADER_LEN - 1);
        let err = Reader::open(&bytes[..]).unwrap_err();
        assert_eq!(
            err,
            BinlogError::TruncatedHeader {
                got: HEADER_LEN - 1,
                want: HEADER_LEN,
            }
        );
    }

    #[test]
    fn bad_magic_is_rejected() {
        let mut bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 0, 1, 1, 1)]]);
        bytes[0] = b'X';
        let err = Reader::open(&bytes[..]).unwrap_err();
        assert!(matches!(err, BinlogError::BadMagic { .. }));
    }

    #[test]
    fn unsupported_version_is_rejected() {
        let mut bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 0, 1, 1, 1)]]);
        bytes[4] = VERSION + 1;
        let err = Reader::open(&bytes[..]).unwrap_err();
        assert_eq!(err, BinlogError::UnsupportedVersion { got: VERSION + 1 });
    }

    #[test]
    fn non_positive_tick_or_step_is_rejected_on_write_and_read() {
        assert!(Writer::create(
            Vec::new(),
            Header {
                tick_e9: 0,
                step_e9: 1,
                max_records_per_frame: DEFAULT_TEST_MAX_RECORDS_PER_FRAME,
            },
            zstd::DEFAULT_COMPRESSION_LEVEL
        )
        .is_err());

        let mut bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 0, 1, 1, 1)]]);
        // Затираем tick_e9 заголовка нулём напрямую в байтах.
        bytes[5..13].copy_from_slice(&0i64.to_le_bytes());
        let err = Reader::open(&bytes[..]).unwrap_err();
        assert!(matches!(err, BinlogError::InvalidHeader { .. }));
    }

    /// `max_records_per_frame = 0` — тот же класс ошибки, что нулевой/
    /// отрицательный `tick_e9`/`step_e9` выше: заголовок, а не программный
    /// аргумент, поэтому и здесь `Result`, а не паника (`validate_header`
    /// общая на запись и чтение).
    #[test]
    fn zero_max_records_per_frame_is_rejected_on_write_and_read() {
        assert!(Writer::create(
            Vec::new(),
            Header {
                tick_e9: TICK_E9,
                step_e9: STEP_E9,
                max_records_per_frame: 0,
            },
            zstd::DEFAULT_COMPRESSION_LEVEL
        )
        .is_err());

        let mut bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 0, 1, 1, 1)]]);
        // Затираем max_records_per_frame заголовка нулём напрямую в байтах
        // (смещение 21..25, см. `Writer::create`).
        bytes[21..25].copy_from_slice(&0u32.to_le_bytes());
        let err = Reader::open(&bytes[..]).unwrap_err();
        assert!(matches!(err, BinlogError::InvalidHeader { .. }));
    }

    // -----------------------------------------------------------------
    // Требование 7 (ревизия 10, Decision 23): потолок разжатого кадра,
    // вычисленный из `max_records_per_frame` заголовка.
    // -----------------------------------------------------------------

    /// Кодирует `n` заведомо нулевых записей напрямую через `encode_record`
    /// — не через `Writer::write_frame`, который сам отказался бы писать
    /// кадр длиннее заголовочного потолка (см. тест ниже про эту самую
    /// проверку). Нулевые поля и нулевые дельты дают ровно минимальный
    /// размер записи (`MIN_RECORD_LEN` = 8 байт: каждое из восьми полей —
    /// однобайтовый varint нуля), и при этом чрезвычайно легко сжимаются:
    /// маленький кадр на диске, огромный после распаковки — ровно форма,
    /// которую и обязан отвергать потолок.
    fn raw_frame_payload_all_zero(n: usize) -> Vec<u8> {
        let mut scratch = Vec::new();
        let epoch_ns = 0i64;
        scratch.extend_from_slice(&epoch_ns.to_le_bytes());
        let mut st = DeltaState {
            epoch_ns,
            prev_price_ticks: 0,
            prev_qty_lots: 0,
        };
        let zero = rec(0, 0, 0, 0, 0);
        for _ in 0..n {
            encode_record(&mut scratch, &zero, &mut st);
        }
        scratch
    }

    /// Заворачивает уже готовую (разжатую) полезную нагрузку в кадр
    /// формата этого файла: `u32` длина сжатого блока LE, затем сам блок —
    /// то же самое, что пишет `Writer::write_frame` после кодирования, но
    /// здесь собрано вручную в обход его проверки потолка (см. доку
    /// `raw_frame_payload_all_zero`).
    fn frame_bytes_from_payload(payload: &[u8]) -> Vec<u8> {
        let compressed = zstd::bulk::compress(payload, zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
        let len = u32::try_from(compressed.len()).unwrap();
        let mut out = Vec::with_capacity(LEN_PREFIX + compressed.len());
        out.extend_from_slice(&len.to_le_bytes());
        out.extend_from_slice(&compressed);
        out
    }

    #[test]
    #[should_panic(expected = "превышает потолок заголовка max_records_per_frame")]
    fn writer_refuses_frame_exceeding_max_records_per_frame() {
        let max = 2u32;
        let hdr = Header {
            tick_e9: TICK_E9,
            step_e9: STEP_E9,
            max_records_per_frame: max,
        };
        let mut w = Writer::create(Vec::new(), hdr, zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
        let over_limit: Vec<Record> = (0..(max as i64 + 1))
            .map(|i| rec(ev_delta_ask(), i, i, i, i))
            .collect();
        // Программная ошибка вызывающего (см. доку `Writer::write_frame`) —
        // тест проверяет, что это паника, а не `Result::Err`.
        let _ = w.write_frame(&over_limit);
    }

    /// Кадр ровно на потолке (`records.len() == max_records_per_frame`) —
    /// граничное значение, а не «за» ним: `Writer` обязан согласиться его
    /// написать (`<=`, не `<`), и `Reader` обязан прочитать его штатно.
    #[test]
    fn frame_at_exactly_the_maximum_still_reads() {
        let max = 3u32;
        let hdr = Header {
            tick_e9: TICK_E9,
            step_e9: STEP_E9,
            max_records_per_frame: max,
        };
        // Малые значения (все поля кодируются одним байтом: `ev`/`order_id`
        // < 128, дельты в [-64, 63], `fval = 0.0`) — записи занимают ровно
        // `MIN_RECORD_LEN` = 8 байт каждая, и разжатое тело кадра совпадает
        // с потолком (`8 + 3*8 = 32`) байт в байт, не с запасом. Тест
        // проверяет именно эту границу: `<=`, а не `<`, у сравнения внутри
        // bounded zstd API. Кадр с тем же числом записей, но с типичными
        // для потока полями (большие флаги `ev`, дельты времени в наносекундах
        // между записями одного кадра) занял бы больше 8 байт на запись —
        // это не противоречие: `max_records_per_frame` в заголовке обязан
        // выбираться вызывающим (рекордером) с запасом над реальным
        // байтовым размером его собственных кадров, а не равняться
        // количеству записей, которое он фактически туда кладёт.
        let frame: Vec<Record> = (0..max as i64).map(|i| rec(i as u64, i, i, i, i)).collect();
        let bytes = write_all(hdr, std::slice::from_ref(&frame));
        let (read_hdr, frames) = read_all(&bytes);
        assert_eq!(read_hdr, hdr);
        assert_eq!(
            frames,
            vec![frame],
            "кадр ровно на потолке обязан читаться штатно"
        );
    }

    /// Файл версии 1 (до ревизии 10 Decision 23): целый и полный заголовок
    /// этой версии — magic(4) + version(1) + tick_e9(8) + step_e9(8) = 21
    /// байт, **без** `max_records_per_frame` и без единого лишнего байта
    /// сверх. Собран напрямую, потому что текущий `Writer` умеет писать
    /// только текущую версию.
    ///
    /// Длина нарочно ровно 21, не 25 (`HEADER_LEN` версии 2): если бы
    /// `Reader::open` по-прежнему читал единым куском фиксированные
    /// `HEADER_LEN` байт (одним чтением на весь заголовок, как до этой
    /// правки), этих 21 не хватило бы на затребованные 25, и код вернул бы
    /// `TruncatedHeader` — правдоподобную, но **вводящую в заблуждение**
    /// ошибку: файл не обрезан, он просто другой, более старой версии.
    /// Два раздельных чтения (`MAGIC_VERSION_LEN`, затем `HEADER_TAIL_LEN`)
    /// обязаны поймать несовпадение версии на первом шаге, пятью байтами,
    /// раньше, чем код вообще спросит про хвост — вот что здесь проверяется.
    #[test]
    fn old_version_file_is_rejected_not_misread() {
        let mut bytes = Vec::new();
        bytes.extend_from_slice(&MAGIC);
        bytes.push(VERSION - 1);
        bytes.extend_from_slice(&TICK_E9.to_le_bytes());
        bytes.extend_from_slice(&STEP_E9.to_le_bytes());
        assert_eq!(
            bytes.len(),
            21,
            "ровно заголовок версии 1, ни байтом больше"
        );

        let err = Reader::open(&bytes[..]).unwrap_err();
        assert_eq!(
            err,
            BinlogError::UnsupportedVersion { got: VERSION - 1 },
            "старая версия обязана называться прямо, а не маскироваться под усечение"
        );
    }

    /// Кадр, чья заявленная (сжатая) длина на диске правдоподобна, но
    /// распаковка которого превышает потолок заголовка: отвергается как
    /// `Err`, не паника, и — проверено через `alloc_count` — не после того,
    /// как память под весь разжатый объём уже выделена. Потолок читателя
    /// (`max_frame_payload_bytes`) сам по себе не бесполезен только если
    /// он ограничивает именно **аллокацию**, а не служит числом, которое
    /// код печатает уже после того, как разжал кадр целиком, — это и есть
    /// разница между bounded API и «разжать, потом проверить».
    #[test]
    fn frame_exceeding_the_header_ceiling_is_rejected_without_full_allocation() {
        let max = 10u32;
        let hdr = Header {
            tick_e9: TICK_E9,
            step_e9: STEP_E9,
            max_records_per_frame: max,
        };
        let expected_ceiling = max_frame_payload_bytes(max);
        // 8 (эпоха) + 10 записей * 8 байт (минимум) = 88 — записано числом
        // здесь исключительно для читаемости остальных чисел теста, само
        // значение проверено равенством `max_frame_payload_bytes(max)` выше.
        assert_eq!(expected_ceiling, 88);

        let mut file = Writer::create(Vec::new(), hdr, zstd::DEFAULT_COMPRESSION_LEVEL)
            .unwrap()
            .into_inner();

        // Сильно за потолком, не впритык: два миллиона одинаковых нулевых
        // записей дают разжатый объём 8 + 2_000_000*8 = 16_000_008 байт —
        // примерно в 180 000 раз больше 88-байтового потолка — и при этом
        // сжимаются в исчезающе малый кадр на диске (проверено ниже).
        const N: usize = 2_000_000;
        let payload = raw_frame_payload_all_zero(N);
        assert_eq!(payload.len(), FRAME_EPOCH_LEN + N * MIN_RECORD_LEN);
        let frame_on_disk = frame_bytes_from_payload(&payload);
        // Проверка теста на себе: «заявленная длина правдоподобна» значит
        // сжатый кадр на диске обязан быть на порядки меньше того, во что
        // он разжимается (не впритык к потолку — потолок сам по себе
        // маленький, 88 байт, и сжатый кадр здесь его не меньше), иначе
        // это тест на что-то другое, не на bounded decompression.
        assert!(
            frame_on_disk.len() * 100 < payload.len(),
            "проверка теста на себе: сжатый кадр ({} байт) обязан быть на порядки \
             меньше разжатого объёма ({} байт), иначе это не «легко сжимаемая \
             полезная нагрузка», о которой говорит тест",
            frame_on_disk.len(),
            payload.len()
        );
        file.extend_from_slice(&frame_on_disk);

        let mut r = Reader::open(&file[..]).unwrap();
        let (result, counts) = alloc_count::measure(|| r.read_frame());
        let err = result.expect_err(
            "кадр, чья распаковка превышает потолок заголовка, обязан быть ошибкой, не Ok",
        );
        assert_eq!(
            err,
            BinlogError::FrameExceedsHeaderCeiling {
                max_records_per_frame: max,
                ceiling_bytes: expected_ceiling,
            }
        );
        assert!(
            counts.bytes < 50_000,
            "аллокация обязана остаться в пределах потолка заголовка ({expected_ceiling} байт \
             в этом тесте), а не расти пропорционально разжатому объёму (16 000 008 байт) — \
             реально выделено {} байт",
            counts.bytes
        );
    }

    // -----------------------------------------------------------------
    // Varint/зигзаг — сами примитивы, границы диапазона.
    // -----------------------------------------------------------------

    #[test]
    fn uvarint_round_trips_boundary_values() {
        for v in [0u64, 1, 127, 128, 300, u32::MAX as u64, u64::MAX] {
            let mut buf = Vec::new();
            write_uvarint(&mut buf, v);
            let mut pos = 0;
            assert_eq!(read_uvarint(&buf, &mut pos).unwrap(), v);
            assert_eq!(pos, buf.len());
        }
    }

    #[test]
    fn zigzag_round_trips_boundary_values() {
        for v in [0i64, 1, -1, 63, -64, i64::MAX, i64::MIN] {
            let mut buf = Vec::new();
            write_zigzag(&mut buf, v);
            let mut pos = 0;
            assert_eq!(read_zigzag(&buf, &mut pos).unwrap(), v);
        }
    }

    #[test]
    fn truncated_varint_is_an_error_not_a_panic() {
        let buf = [0x80u8]; // продолжение обещано, следующего байта нет
        let mut pos = 0;
        assert!(read_uvarint(&buf, &mut pos).is_err());
    }
}
