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
//!
//! тело кадра v3:
//!   epoch_exch_ns : i64 LE (8)     ← метка биржи первой записи кадра
//!   группа* (одно сообщение биржи):
//!     ev : uvarint                 ← флаги события, служебных бит нет
//!     exch_dt : zigzag             ← exch_ts − epoch
//!     local_dt : zigzag            ← local_ts − epoch (одна метка на сообщение)
//!     attrs : uvarint              ← бит 0: блочная сделка; бит 1: RPI-сделка;
//!                                     остальные биты 0
//!     count : uvarint              ← записей в группе (≥ 1)
//!     count × (price_dt : zigzag, qty_dt : zigzag)  ← дельты от предыдущей
//!                                                     записи кадра
//! ```
//!
//! Версия 2 (её пишет живой коллектор до перезапуска) читается тем же
//! читателем: восемь полей на запись (`ev`, `exch_ts`, `local_ts`, цена,
//! размер, `order_id`, `ival`, `fval`). Три последних в живых данных
//! **нулевые во всех записях** (`docs/findings/binlog-format-2026-09-13.md`,
//! 17 млн записей трёх монет), поэтому v3 их не хранит вовсе; счётчики этих
//! полей по v2-файлу остаются доступны (`Reader::legacy_dead_fields`), чтобы
//! доказательство «0 %» можно было перепроверить на любом старом файле.
//! Блочность сделки (`ival != 0` в v2) — не мёртвое поле, а бит `attrs`:
//! `lob/levels.rs` пропускает блочные сделки, и семантика сохранена без
//! `i64`-варианта в каждой записи. Бит 1 (`RPI`, 2026-09-16) добавлен так же:
//! RPI-сделка исполнена об невидимую заявку маркет-мейкера и видимую
//! ликвидность уровня не потребляет. Файлы, записанные до этого дня, читаются
//! как `rpi = false` — «не размечено»; обратная совместимость односторонняя:
//! старый читатель новые файлы отвергает по неизвестному биту, и это его
//! штатное поведение (fail-closed), а не регресс.
//!
//! # Контейнер архива (T46, `binlog::archive`)
//!
//! Закрытые сверенные сутки складываются в `*.binlog.zst`: **один** zstd-поток
//! над
//!
//! ```text
//! [маркер ABLA(4) | версия контейнера(1) | уровень zstd(1)]
//! [заголовок v3 (25 Б, тот же) ]
//! [кадр 0: u32 длина LE | тело кадра БЕЗ сжатия]
//! [кадр 1: ...]*
//! ```
//!
//! Замер `docs/findings/archive-compression-2026-09-15.md`: покадровое сжатие
//! уровня 1 уже даёт 24 %, но контексту негде расти, и пересжатие кадров
//! уровнем 19 добавляет всего 1.6 %; одним потоком поверх тех же тел кадров
//! zstd-19 даёт −7.7 % **к сегодняшнему размеру на диске** (92.3 %), а
//! разжимается на порядок быстрее xz. Формат v3 при этом не меняется
//! (В-49): архив — обёртка над теми же кадрами, живой путь записи о ней не
//! знает вовсе.
//!
//! `Reader` опознаёт контейнер по магии zstd в первых четырёх байтах и читает
//! его тем же `read_frame`: различие только в теле кадра — у контейнера оно
//! уже лежит несжатым, у обычного файла разжимается покадрово. Заголовок
//! суток внутри контейнера — тот же v3, поэтому все читатели (`levels`,
//! `markout`, `verify`, `binlog-stats`, `dashboard`) получают архив даром:
//! их код не меняется, меняется только имя файла в каталоге.
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
//! Запись — это одно **сырое событие** (надмножество полей `Event` крейта
//! `hftbacktest` по данным, но не по носителю: `ev`, `exch_ts`, `local_ts`,
//! цена, размер, блочность; поля подтверждены по
//! `hftbacktest-0.9.4/src/types.rs`, Decision 17), а не реконструированное
//! состояние книги: снапшот в начале суток — это много обычных записей
//! подряд (по одной на уровень), никакого отдельного «состояния» модуль не
//! знает и не хранит. `px`/`qty` крейта — `f64` для границы с бэктестом; в
//! логе на их месте целые `price_ticks`/`qty_lots` (A1). Перевод в `f64` для
//! `Event` — дело экспортёра (шаг 6.1), не этого модуля.
//!
//! Записи одного **сообщения биржи** идут в теле кадра одной группой: у
//! уровня книги нет ничего своего, кроме цены и размера, а флаги, метка
//! биржи и метка приёма у всего сообщения одни и те же. Группа — это
//! `ev` + `exch_dt` + `local_dt` + `count` + сами пары (цена, размер).
//! Границу группы кодировщик видит по смене `(ev, exch_ts_ns, local_ts_ns,
//! block)` у соседних записей среза: вызывающий (`record.rs`,
//! `session::sink`) кладёт записи одного сообщения подряд, поэтому границы
//! групп совпадают с сообщениями без правки горячего пути. Кадр при этом
//! остаётся **пачкой** сообщений (`FRAME_TARGET_RECORDS`): сообщение книги —
//! это 1–5 изменённых уровней, и кадр на сообщение почти не сжимается
//! (измерено таском 25, `docs/findings/collector-2026-09-12.md`).
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

pub mod archive;

pub use archive::{
    default_out_path as archive_out_path, file_bytes as file_bytes_on_disk, is_archive_path,
    max_level as archive_max_level, read_stats as archive_stats,
    verify_round_trip as archive_verify_round_trip, write_container as archive_write, ArchiveRead,
    ArchiveVerify, ArchiveWrite, ARCHIVE_MAGIC, ARCHIVE_VERSION,
    DEFAULT_LEVEL as ARCHIVE_DEFAULT_LEVEL,
};

/// Кодеки, которые существуют только ради замера (тикеты 43/44, M1i/M1e) и
/// совместимости v2 — не часть настоящего формата, поэтому не в `pub` API
/// этого модуля (ремонт W3, ревью 23.09). `pub(crate)`, а не `pub`: видит
/// только этот бинарник (`lob binlog-stats`), не внешний потребитель крейта.
pub(crate) mod experiments;
pub(crate) use experiments::{
    decode_frame_payload_v3_ev_table, encode_frame_payload_v2, encode_frame_payload_v3_ev_table,
    encode_frame_payload_v3_index_simulated,
};

/// Суффикс обычного суточного файла: `<SYMBOL>-<день>[-pN].binlog`.
pub const BINLOG_SUFFIX: &str = ".binlog";

/// Суффикс контейнера архива (T46): `<SYMBOL>-<день>[-pN].binlog.zst`.
/// Отрезается **раньше** обычного суффикса: `strip_binlog_suffix` снял бы
/// `.binlog` первым и оставил `.zst` в хвосте имени, то есть сутки перестали
/// бы разбираться.
pub const BINLOG_ARCHIVE_SUFFIX: &str = ".binlog.zst";

/// Отрезает суффикс суточного файла (архивный или обычный) — единственное
/// место, где это правило записано. Живёт в `binlog`, а не в `commands`, чтобы
/// им могли пользоваться оба слоя: `commands::lob` (резолверы) и
/// `bybit::verify` (своя копия резолвера — `bybit` не зависит от `commands`,
/// граница слоёв). `None` — не имя суточного файла.
pub fn strip_binlog_suffix(name: &str) -> Option<&str> {
    name.strip_suffix(BINLOG_ARCHIVE_SUFFIX)
        .or_else(|| name.strip_suffix(BINLOG_SUFFIX))
}

/// Имя суточного файла — обычного или контейнера архива. Оба читаются одним
/// `Reader`, поэтому и резолверы обязаны видеть оба (T46).
pub fn is_binlog_file_name(name: &str) -> bool {
    strip_binlog_suffix(name).is_some()
}

/// Разбор календарных суток `YYYY-MM-DD` — общая точка для всех мест, где день приходит строкой
/// с CLI или из CSV: `commands::record::paths::day_index_of_day_str`,
/// `commands::lob::import_archive::run_import_archive`, `commands::lob::replay::is_next_day`,
/// `lob::shortlist::parse_ymd` (W9 ревью 23.09). Живёт в `binlog`, а не в `commands` или `lob`,
/// чтобы обоим слоям было можно — `lob` не имеет пути до `commands` (граница модулей,
/// `ARCHITECTURE.md`). `None` — не разобралось как `YYYY-MM-DD` целиком (включая календарно
/// невозможные дни вроде 30 февраля); вызывающий сам решает, какой ошибкой это обернуть.
/// `bounce_grid/carry.rs` — намеренно отдельная копия (другая дорожка правок).
pub fn parse_calendar_day(day: &str) -> Option<chrono::NaiveDate> {
    chrono::NaiveDate::parse_from_str(day, "%Y-%m-%d").ok()
}

/// Хронологический ключ суточного файла: сутки UTC, затем часть суток
/// (`-p2` после смены шагов). Голая лексикография врёт: `-` (0x2D) меньше
/// `.` (0x2E) в ASCII, и `SOLUSDT-2026-09-08-p2.binlog` как строка встал бы
/// **раньше** `SOLUSDT-2026-09-08.binlog`, то есть хвост суток — раньше их
/// начала, а данные читателям нужны по времени. Имя после префикса символа —
/// либо `день`, либо `день-pN`; всё нераспознанное считается частью 1 того же
/// имени.
///
/// Живёт здесь, а не в `commands`, по той же причине, что и снятие суффикса:
/// правило нужно двум слоям (`commands::lob` — резолверы, `bybit::verify` —
/// своя копия резолвера; `bybit` не зависит от `commands`, граница слоёв). До
/// T46 оно было второй копией в каждом из них; с двумя суффиксами копий
/// стало бы столько же — второй способ придумать то же правило перестал быть
/// дешевле общего.
pub fn binlog_file_order_key(prefix: &str, name: &str) -> (String, u32) {
    let rest = name.strip_prefix(prefix).unwrap_or(name);
    let rest = strip_binlog_suffix(rest).unwrap_or(rest);
    if let Some(tail) = rest.get(10..) {
        if let Some(num) = tail.strip_prefix("-p") {
            if let Ok(part) = num.parse::<u32>() {
                return (rest[..10].to_string(), part);
            }
        }
    }
    (rest.to_string(), 1)
}

/// Приводит список файлов одного символа к «одна часть суток — один файл»:
/// сортирует хронологически ([`binlog_file_order_key`]) и выбрасывает
/// дубликаты по `(сутки, часть)`.
///
/// Дубликат — ровно случай «оригинал и его архив лежат рядом» (T46): сутки
/// те же (архив собран из этого файла и сверен round-trip), и вернуть оба
/// значило бы проиграть сутки дважды, причём в порядке `read_dir`, то есть
/// недетерминированно. Побеждает **обычный** файл: он не требует кодека и
/// читается всеми командами ровно как до T46, а архив читается, когда
/// оригинала уже нет, — то есть в том порядке, который описывает ранбук
/// (архивировать → сверить → удалить оригинал).
pub fn dedupe_same_day_part(prefix: &str, files: &mut Vec<std::path::PathBuf>) {
    let key_of = |p: &std::path::PathBuf| {
        let name = p
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        binlog_file_order_key(prefix, &name)
    };
    let is_archive = |p: &std::path::PathBuf| p.to_string_lossy().ends_with(BINLOG_ARCHIVE_SUFFIX);
    // Порядок по ключу; при равном ключе обычный файл раньше архива.
    files.sort_by(|a, b| {
        key_of(a)
            .cmp(&key_of(b))
            .then_with(|| is_archive(a).cmp(&is_archive(b)))
    });
    files.dedup_by(|a, b| key_of(a) == key_of(b));
}

/// Магия формата. Проверяется при открытии, чтобы чужой или пустой файл
/// не читался молча как валидный лог с нулевым содержимым.
pub const MAGIC: [u8; 4] = *b"ABLG";

/// Версия формата, которую **пишет** этот код, — байт в заголовке (раздел
/// «Contracts touched» `PLAN.md`: «формат бинарного лога версионируется
/// байтом в заголовке»). Меняется при любой несовместимой правке раскладки,
/// а не при добавлении новых значений существующих полей.
///
/// `3`, не `2`: тело кадра стало списком групп (сообщений биржи), у записи
/// больше нет `order_id`/`ival`/`fval`, а `local_ts` переехал из записи в
/// заголовок группы. Старый читатель такого тела не ждёт — несовместимая
/// правка раскладки обязана поднять версию, иначе файл версии 3 читался бы
/// байт в байт как версия 2 и первая же группа ушла бы не туда (см. тесты
/// `old_version_file_is_rejected_not_misread` и
/// `version_two_file_is_read_as_legacy_not_misread`).
pub const VERSION: u8 = 3;

/// Версия 2 — та, которую пишет **живой коллектор** до перезапуска на новый
/// бинарник (В-41: процесс живёт с копии `data/always-on/alpha-collector.exe`
/// и в этой правке не трогается). Читатель обязан знать обе версии, иначе
/// вся идущая запись перестала бы читаться; писателя версии 2 в коде нет —
/// её форму держит только legacy-декодер `decode_frame_payload_v2`.
pub const VERSION_V2: u8 = 2;

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

/// Длина префикса кадра — `u32`, как назначено Decision 7. Публичная, потому
/// что замер формата (`lob binlog-stats --reencode`) собирает кадры в памяти и
/// обязан считать их длину той же формулой, а не второй копией числа 4.
pub const LEN_PREFIX: usize = 4;

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

/// Одна запись — сырое событие внутри группы (сообщения биржи). Поля, которые
/// есть у `Event` крейта `hftbacktest`, но информации в живых данных не несут
/// (`order_id`, `fval` — 0 % ненулевых на 17 млн записей, замер
/// `docs/findings/binlog-format-2026-09-13.md`), у `Record` нет вовсе: экспортёр
/// ставит на их место `0`/`0.0` (поток L2 Bybit числового id заявки не
/// сообщает). `ival` заменён на `block` — семантику блочной сделки, ради
/// которой он и отличался от нуля (`lob/levels.rs` такие сделки пропускает).
///
/// `Eq`, не только `PartialEq` (в v2 здесь был `f64` `fval`, и `NaN != NaN`
/// делал `Eq` невозможным): после переноса мёртвых полей вон в записи не
/// осталось ни одного нецелого поля, и сравнение записей стало полным.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Record {
    /// Флаги события — те же биты, что `hftbacktest::types::{DEPTH_EVENT,
    /// TRADE_EVENT, BUY_EVENT, ...}`. Модуль их не толкует, только хранит:
    /// смысл бит — дело пишущего (рекордер, шаг 0.3), не формата хранения.
    pub ev: u64,
    /// Наносекунды от эпохи Unix — тот же масштаб, что `local_ts_ns`
    /// в `bybit::conn::ConnEvent` (H12): `exch_ts` биржи приходит в мс
    /// (`cts`/`T`) и переводится в нс на той же границе, что и сравнение
    /// `exch_ts < local_ts`, а не заново здесь другим способом.
    ///
    /// В формате v3 обе метки хранятся **один раз на группу**: внутри
    /// сообщения биржи они одни и те же у всех его записей (sink передаёт
    /// одну метку приёма на сообщение), поэтому перенос не теряет ничего, а
    /// экономит вторую метку в каждой записи.
    pub exch_ts_ns: i64,
    pub local_ts_ns: i64,
    /// Цена как целое число тиков (A1). Тик восстанавливается из `tick_e9`
    /// заголовка: `price_e9 = price_ticks * tick_e9`.
    pub price_ticks: i64,
    /// Размер как целое число шагов количества (A1), аналогично `step_e9`.
    pub qty_lots: i64,
    /// Блочная сделка (`BT` у Bybit, в v2 — `ival != 0`): такие сделки не
    /// потребляют видимую ликвидность (Decision 5), разметка их пропускает
    /// (`lob/levels.rs`). В формате v3 — бит 0 `attrs` группы.
    pub block: bool,
    /// Сделка об RPI-заявку (`RPI` у Bybit). В формате v3 — бит 1 `attrs`
    /// группы; файлы до 2026-09-16 читаются как `false` («не размечено»).
    pub rpi: bool,
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
// Кодек кадра: v3 (текущий, пишется) и v2 (только чтение живых файлов).
// ---------------------------------------------------------------------

/// Длина префикса эпохи кадра — `i64`-метка времени первой записи
/// (`Writer::write_frame`), которой мерятся дельты времени внутри кадра.
const FRAME_EPOCH_LEN: usize = 8;

/// Бит 0 `attrs` группы — блочная сделка (`BT` у Bybit, в v2 `ival != 0`).
/// Остальные биты обязаны быть нулевыми: неизвестный бит означает раскладку,
/// которой этот читатель не знает, и молча его проглотить значило бы прочитать
/// чужие байты как свои (та же дисциплина, что у версии в заголовке).
const ATTRS_BLOCK: u64 = 1 << 0;

/// Бит 1 `attrs` группы — RPI-сделка (`RPI` у Bybit, 2026-09-16): исполнена об
/// невидимую заявку маркет-мейкера, видимую ликвидность уровня не потребляет.
/// Группа рвётся по смене флага (`same_message`), поэтому флаг остаётся
/// поштучным, хотя живёт в заголовке группы.
const ATTRS_RPI: u64 = 1 << 1;

/// Все биты `attrs`, которые знает эта версия читателя.
const ATTRS_KNOWN: u64 = ATTRS_BLOCK | ATTRS_RPI;

/// `attrs` группы из флагов записи.
fn attrs_of(r: &Record) -> u64 {
    u64::from(r.block) | (u64::from(r.rpi) << 1)
}

/// Полевой бюджет кадра — сколько байт **сырого** тела пришлось на каждое
/// поле. В формат не входит: копит кодировщик, читает замер
/// (`lob binlog-stats --reencode`), чтобы вопрос «за что платятся байты» имел
/// числовой ответ, а не оценку на глаз (`docs/findings/binlog-format-2026-09-13.md`,
/// «Чего этот замер не говорит»).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct FieldBytes {
    /// Эпоха кадра — одна на кадр, в обеих версиях.
    pub epoch: usize,
    pub ev: usize,
    /// `exch_ts`/`local_ts`: в v3 — на группу (сообщение), в v2 — на запись.
    pub exch_ts: usize,
    pub local_ts: usize,
    pub price: usize,
    pub qty: usize,
    /// `attrs` и `count` — заголовок группы, не привязанный к отдельной записи
    /// (в v3).
    pub group_overhead: usize,
    /// Только v2: байты `order_id`, `ival`, `fval` — тех полей, которых в v3
    /// нет.
    pub dead_fields: usize,
}

impl FieldBytes {
    /// Складывает бюджеты: замер идёт по всем кадрам файла, а кодировщик
    /// считает кадр за кадром.
    pub fn add(&mut self, other: Self) {
        self.epoch += other.epoch;
        self.ev += other.ev;
        self.exch_ts += other.exch_ts;
        self.local_ts += other.local_ts;
        self.price += other.price;
        self.qty += other.qty;
        self.group_overhead += other.group_overhead;
        self.dead_fields += other.dead_fields;
    }
}

/// Состояние дельты, которое живёт внутри одного кадра и обнуляется на
/// каждом новом (см. доку модуля: кадры читаются независимо друг от друга).
/// Эпоха кадра здесь не хранится: её знает и кодировщик, и декодер, а метки
/// считаются от неё напрямую — держать её ещё и тут значило бы заводить
/// вторую копию одного значения без нужды.
struct DeltaState {
    prev_price_ticks: i64,
    prev_qty_lots: i64,
}

impl DeltaState {
    fn new() -> Self {
        Self {
            prev_price_ticks: 0,
            prev_qty_lots: 0,
        }
    }
}

/// Сколько байт записал один вызов кодека: `buf.len()` до и после.
fn took(buf: &[u8], before: usize) -> usize {
    buf.len() - before
}

/// Пишет одну запись v3: цена и размер — дельты от предыдущей записи кадра.
/// `indexed` — только для симуляции M1i (`PriceMode::IndexSimulated`): вместо
/// дельты цены один байт, как у настоящего индекса уровня.
fn encode_entry_v3(
    buf: &mut Vec<u8>,
    r: &Record,
    st: &mut DeltaState,
    fb: &mut FieldBytes,
    indexed: bool,
) {
    if indexed {
        // Индекс уровня стороны: у книги ≤ 50 уровней на сторону, значит
        // индекс < 128 и uvarint занимает ровно один байт. Значение — младшие
        // биты цены, а не ноль: у замены должна быть та же длина и
        // сопоставимая энтропия, иначе zstd сжал бы одинаковые нули в ничто и
        // завысил бы экономию варианта.
        buf.push((r.price_ticks as u64 & 0x7f) as u8);
        fb.price += 1;
    } else {
        let before = buf.len();
        write_zigzag(buf, r.price_ticks.wrapping_sub(st.prev_price_ticks));
        fb.price += took(buf, before);
    }
    let before = buf.len();
    write_zigzag(buf, r.qty_lots.wrapping_sub(st.prev_qty_lots));
    fb.qty += took(buf, before);
    st.prev_price_ticks = r.price_ticks;
    st.prev_qty_lots = r.qty_lots;
}

/// Заголовок группы: флаги события (или код таблицы — `EvMode`), метка биржи,
/// метка приёма (одна на сообщение биржи), `attrs` и число записей.
fn encode_group_header(
    buf: &mut Vec<u8>,
    r: &Record,
    epoch_ns: i64,
    count: usize,
    ev_mode: &EvMode<'_>,
    fb: &mut FieldBytes,
) {
    let before = buf.len();
    match ev_mode {
        // Код группы: индекс в таблице кадра, а для значения, не попавшего в
        // таблицу (различных значений больше `EV_TABLE_MAX`), — `ev_count` как
        // escape и полный `ev` следом: молча терять значение нельзя.
        EvMode::Table(table) => match table.iter().position(|&e| e == r.ev) {
            Some(code) => write_uvarint(buf, code as u64),
            None => {
                write_uvarint(buf, table.len() as u64);
                write_uvarint(buf, r.ev);
            }
        },
        EvMode::Inline => write_uvarint(buf, r.ev),
    }
    fb.ev += took(buf, before);
    // `wrapping_sub`, не `-`: обоснование — в доке модуля. Метки времени —
    // дельты от эпохи кадра (Decision 23), а не от предыдущей записи: эпоха
    // одна на кадр, и метка сообщения одна на все его уровни.
    let before = buf.len();
    write_zigzag(buf, r.exch_ts_ns.wrapping_sub(epoch_ns));
    fb.exch_ts += took(buf, before);
    let before = buf.len();
    write_zigzag(buf, r.local_ts_ns.wrapping_sub(epoch_ns));
    fb.local_ts += took(buf, before);
    let before = buf.len();
    write_uvarint(buf, attrs_of(r));
    write_uvarint(buf, count as u64);
    fb.group_overhead += took(buf, before);
}

/// Одна ли это группа: у сообщения биржи флаги, обе метки и блочность одни и
/// те же на все его записи. Смена любого из пяти — начало следующего
/// сообщения. Функция публичная, потому что по этой же границе считает группы
/// замер (`lob binlog-stats`): граница групп — часть формата, и второй её
/// редакции в командах быть не должно.
pub fn same_message(a: &Record, b: &Record) -> bool {
    a.ev == b.ev
        && a.exch_ts_ns == b.exch_ts_ns
        && a.local_ts_ns == b.local_ts_ns
        && a.block == b.block
        && a.rpi == b.rpi
}

/// Как кодировать цену — параметр **замера**, в формат входит только `Delta`.
enum PriceMode<'a> {
    /// Дельта от предыдущей записи кадра — формат v3.
    Delta,
    /// Симуляция «уровень индексом» для M1i тикета 43. В формат не входит и
    /// писателем не используется: чтобы индекс уровня стал настоящим, читателю
    /// нужна книга инструмента, а это ломает «кадр читается независимо»
    /// (Decision 7) и вдобавок требует хранить `u` — последовательность
    /// обновлений, без которой `Book::apply` не работает, а в логе её нет.
    IndexSimulated(&'a [bool]),
}

/// Как кодировать флаги `ev` — параметр **замера** (M1e тикета 44). В формат
/// входит только `Inline`.
enum EvMode<'a> {
    /// `ev` в заголовке каждой группы — формат v3.
    Inline,
    /// Таблица `ev` на кадр, в группе — код. Различных значений в живых файлах
    /// 4–6 при пятибайтовом варинте, поэтому таблица (~21 байт на кадр) может
    /// оказаться дешевле, чем полный `ev` в каждой группе; решает замер, а не
    /// очевидность.
    Table(&'a [u64]),
}

/// Максимум значений `ev` в таблице кадра варианта M1e. Больше — значение
/// уходит в группу экранированным (код `ev_count` плюс полный `ev`), поэтому
/// вариант не ломается на незнакомом потоке, а не теряет значение молча.
const EV_TABLE_MAX: usize = 32;

/// Тело кадра v3: эпоха, затем группы сообщений до конца среза. Число групп
/// нигде не хранится отдельно — конец среза и есть конец кадра; `count`
/// группы считается просмотром вперёд по срезу.
fn encode_frame_payload(
    records: &[Record],
    mode: PriceMode<'_>,
    ev_mode: EvMode<'_>,
    out: &mut Vec<u8>,
) -> FieldBytes {
    let mut fb = FieldBytes::default();
    let Some(first) = records.first() else {
        return fb;
    };
    // Эпоха кадра — метка первой записи (см. доку модуля): не отдельный
    // параметр звонка, чтобы вызывающему не приходилось поддерживать вторую
    // копию того же значения.
    let epoch_ns = first.exch_ts_ns;
    out.extend_from_slice(&epoch_ns.to_le_bytes());
    fb.epoch += FRAME_EPOCH_LEN;

    if let EvMode::Table(table) = &ev_mode {
        let before = out.len();
        write_uvarint(out, table.len() as u64);
        for ev in *table {
            write_uvarint(out, *ev);
        }
        fb.ev += took(out, before);
    }

    let mut st = DeltaState::new();
    let mut i = 0;
    while let Some(head) = records.get(i) {
        let mut end = i + 1;
        while records.get(end).is_some_and(|r| same_message(head, r)) {
            end += 1;
        }
        encode_group_header(out, head, epoch_ns, end - i, &ev_mode, &mut fb);
        for (k, r) in records[i..end].iter().enumerate() {
            let indexed = matches!(mode, PriceMode::IndexSimulated(m) if m.get(i + k).copied().unwrap_or(false));
            encode_entry_v3(out, r, &mut st, &mut fb, indexed);
        }
        i = end;
    }
    fb
}

/// Тело кадра v3 — то, что пишет `Writer::write_frame`.
pub fn encode_frame_payload_v3(records: &[Record], out: &mut Vec<u8>) -> FieldBytes {
    encode_frame_payload(records, PriceMode::Delta, EvMode::Inline, out)
}

// Варианты `PriceMode::IndexSimulated`/`EvMode::Table` (M1i/M1e тикетов
// 43/44) и перекодировщик v2 живут в `experiments` (ремонт W3, ревью 23.09):
// `encode_frame_payload_v3_index_simulated`, `encode_frame_payload_v3_ev_table`,
// `encode_frame_payload_v2` — эти три и парный им `decode_frame_payload_v3_ev_table`
// ниже. Ни один формат-кадр их не использует — только замер `lob binlog-stats`
// (в т.ч. `--reencode`), и pub API этого модуля (реального писателя/читателя)
// им нести незачем; `experiments` — дочерний модуль, поэтому видит
// `encode_frame_payload`/`PriceMode`/`EvMode`/`EV_TABLE_MAX` этого файла как есть.

/// Сколько записей v2-файла несли ненулевое значение в поле, которого в v3
/// больше нет. Ненулевое `ival` — это и есть блочность (в `Record` такие
/// записи приходят с `block: true`), но считается она здесь по исходной форме:
/// счётчики отвечают на вопрос про **байты формата**, а не про смысл поля.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct LegacyDeadFields {
    pub nonzero_order_id: u64,
    pub nonzero_ival: u64,
    pub nonzero_fval: u64,
}

/// Читает эпоху кадра — общий префикс обеих версий.
fn read_frame_epoch(payload: &[u8]) -> Result<i64, BinlogError> {
    let raw: [u8; FRAME_EPOCH_LEN] = payload
        .get(..FRAME_EPOCH_LEN)
        .and_then(|s| s.try_into().ok())
        .ok_or_else(|| {
            BinlogError::Corrupt(format!("кадр короче эпохи ({FRAME_EPOCH_LEN} байт)"))
        })?;
    Ok(i64::from_le_bytes(raw))
}

/// Разбирает уже разжатое тело кадра v3: эпоха, затем группы сообщений до
/// конца среза. Число записей группы проверяется против остатка кадра **до**
/// чтения: каждая запись — минимум два байта, поэтому группа, объявившая
/// записей больше, чем в кадре осталось байт, — порча, а не повод крутить
/// цикл по счётчику, который пришёл с диска.
pub fn decode_frame_payload_v3(payload: &[u8]) -> Result<Vec<Record>, BinlogError> {
    let epoch_ns = read_frame_epoch(payload)?;
    let mut st = DeltaState::new();
    let mut pos = FRAME_EPOCH_LEN;
    let mut out = Vec::new();
    while pos < payload.len() {
        let ev = read_uvarint(payload, &mut pos)?;
        let exch_delta = read_zigzag(payload, &mut pos)?;
        let local_delta = read_zigzag(payload, &mut pos)?;
        let attrs = read_uvarint(payload, &mut pos)?;
        if attrs & !ATTRS_KNOWN != 0 {
            return Err(BinlogError::Corrupt(format!(
                "неизвестный бит attrs группы: {attrs:#x}"
            )));
        }
        let count = read_uvarint(payload, &mut pos)?;
        let remaining = (payload.len() - pos) as u64;
        if count > remaining {
            return Err(BinlogError::Corrupt(format!(
                "группа объявила {count} записей, а в кадре осталось {remaining} байт"
            )));
        }
        let exch_ts_ns = epoch_ns.wrapping_add(exch_delta);
        let local_ts_ns = epoch_ns.wrapping_add(local_delta);
        let block = attrs & ATTRS_BLOCK != 0;
        let rpi = attrs & ATTRS_RPI != 0;
        for _ in 0..count {
            let price_delta = read_zigzag(payload, &mut pos)?;
            let qty_delta = read_zigzag(payload, &mut pos)?;
            let price_ticks = st.prev_price_ticks.wrapping_add(price_delta);
            let qty_lots = st.prev_qty_lots.wrapping_add(qty_delta);
            st.prev_price_ticks = price_ticks;
            st.prev_qty_lots = qty_lots;
            out.push(Record {
                ev,
                exch_ts_ns,
                local_ts_ns,
                price_ticks,
                qty_lots,
                block,
                rpi,
            });
        }
    }
    Ok(out)
}

// `decode_frame_payload_v3_ev_table` (парный читатель варианта замера выше)
// — тоже в `experiments`, тем же обоснованием.

/// Разбирает тело кадра v2 — форму, которую пишет живой коллектор до
/// перезапуска. `order_id`/`ival`/`fval` читаются, потому что лежат в потоке,
/// но в `Record` их больше нет: `ival` становится `block`, а `order_id`/`fval`
/// только считаются в `dead` — эти счётчики и есть воспроизводимое на любом
/// старом файле доказательство «0 % ненулевых».
fn decode_frame_payload_v2(
    payload: &[u8],
    dead: &mut LegacyDeadFields,
) -> Result<Vec<Record>, BinlogError> {
    let epoch_ns = read_frame_epoch(payload)?;
    let mut st = DeltaState::new();
    let mut pos = FRAME_EPOCH_LEN;
    let mut out = Vec::new();
    while pos < payload.len() {
        let ev = read_uvarint(payload, &mut pos)?;
        let exch_delta = read_zigzag(payload, &mut pos)?;
        let local_delta = read_zigzag(payload, &mut pos)?;
        let price_delta = read_zigzag(payload, &mut pos)?;
        let qty_delta = read_zigzag(payload, &mut pos)?;
        let order_id = read_uvarint(payload, &mut pos)?;
        let ival = read_zigzag(payload, &mut pos)?;
        let fval_bits = read_uvarint(payload, &mut pos)?;
        if order_id != 0 {
            dead.nonzero_order_id += 1;
        }
        if ival != 0 {
            dead.nonzero_ival += 1;
        }
        if fval_bits != 0 {
            dead.nonzero_fval += 1;
        }
        let price_ticks = st.prev_price_ticks.wrapping_add(price_delta);
        let qty_lots = st.prev_qty_lots.wrapping_add(qty_delta);
        st.prev_price_ticks = price_ticks;
        st.prev_qty_lots = qty_lots;
        out.push(Record {
            ev,
            exch_ts_ns: epoch_ns.wrapping_add(exch_delta),
            local_ts_ns: epoch_ns.wrapping_add(local_delta),
            price_ticks,
            qty_lots,
            block: ival != 0,
            // v2 поля RPI не несёт: файлы той версии — «не размечено».
            rpi: false,
        });
    }
    Ok(out)
}

/// Минимальная длина, которую запись занимает в теле кадра, — по **обеим**
/// формам сразу, потому что потолок кадра один на читателя, а читатель знает
/// и v2, и v3. v2: восемь полей, каждое — варинт минимум в байт (LEB128 нуля
/// — ровно один байт `0x00`, и зигзаг сводится к тому же `uvarint` после
/// перестановки знака), то есть 8 байт на запись. v3: 5 байт заголовка группы
/// (`ev`, две дельты времени, `attrs`, `count`) плюс 2 байта записи (цена и
/// размер) — 7 байт на запись в худшем случае, когда каждая запись оказалась
/// своей группой. Берётся максимум: меньшая граница сделала бы потолок ниже
/// настоящего кадра v2 и отвергла бы законный файл.
const MIN_RECORD_LEN: usize = 8;

/// Максимальная длина, которую запись занимает в теле кадра, — тоже по обеим
/// формам: потолок одного поля — LEB128-варинт `u64` не длиннее десяти байт
/// (64 бита по семь на байт, `ceil(64 / 7) = 10`, свойство кодека, не данных).
/// v2 — восемь полей, 80 байт; v3 — пять полей заголовка группы плюс цена и
/// размер, 70 байт. Из максимума считается `max_frame_bytes_on_disk`.
const MAX_RECORD_LEN: usize = 80;

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
/// (`Reader::header`), а `MIN_RECORD_LEN` — из форм `decode_frame_payload_v2`
/// и `decode_frame_payload_v3` в этом же файле (берётся максимум по версиям,
/// иначе потолок отверг бы законный кадр v2).
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

/// Верхняя граница длины **закодированного** тела кадра v3 для
/// `max_records_per_frame` записей: эпоха плюс записи по максимальной длине
/// (`MAX_RECORD_LEN`). Не оценка, а потолок: `count` записей в группе — не
/// меньше байта на группу, и ни одно поле не длиннее своей LEB128-формы.
///
/// Нужна читателю контейнера архива (T46): там тело кадра лежит **несжатым**,
/// и потолок разжатия zstd (`max_frame_payload_bytes` — граница снизу, по
/// минимальной длине записи) его не ограничивает. Без этой проверки
/// испорченная длина кадра в контейнере дала бы вверх по стеку буфер
/// размером с файл вместо «кадра такого размера в формате быть не может».
fn max_frame_record_bytes(max_records_per_frame: u32) -> usize {
    (max_records_per_frame as usize)
        .saturating_mul(MAX_RECORD_LEN)
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

/// Хвост заголовка (`HEADER_TAIL_LEN` байт) из уже опознанного потока:
/// общий код обычного файла и контейнера архива — заголовок суток у них
/// один и тот же (T46). `before` — сколько байт заголовка уже прочитано
/// (0 у обычного файла, `archive::HEADER_LEN` у контейнера), чтобы
/// `TruncatedHeader` называл смещение в распакованном потоке, а не в
/// формате, которого у контейнера снаружи нет.
fn read_header_tail<R: Read>(body: &mut Body<R>, before: usize) -> Result<Header, BinlogError> {
    let mut tail = [0u8; HEADER_TAIL_LEN];
    match body.read_upto(&mut tail)? {
        ReadStatus::Full => {}
        ReadStatus::Partial(got) => {
            return Err(BinlogError::TruncatedHeader {
                got: before + MAGIC_VERSION_LEN + got,
                want: before + HEADER_LEN,
            })
        }
        ReadStatus::Eof => {
            return Err(BinlogError::TruncatedHeader {
                got: before + MAGIC_VERSION_LEN,
                want: before + HEADER_LEN,
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
    Ok(header)
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
fn read_upto<R: Read>(r: &mut R, buf: &mut [u8]) -> io::Result<ReadStatus> {
    if buf.is_empty() {
        return Ok(ReadStatus::Full);
    }
    let mut filled = 0;
    while filled < buf.len() {
        match r.read(&mut buf[filled..]) {
            Ok(0) => break,
            // `.min(buf.len() - filled)` (ремонт W3, ревью 23.09): `Read::
            // read` обязан вернуть `n` не больше длины среза, который ему
            // дали, но контракт — не гарантия компилятора, а обещание
            // реализации. Источники здесь — обычный файл, и (T46) поток
            // `zstd::stream::read::Decoder`, разжимающий архив на лету: у
            // обоих `n` без проверки уже был бы доверенным чужим числом,
            // и нарушивший контракт `Read` увёл бы `filled` за `buf.len()`,
            // а следующая итерация — `&mut buf[filled..]` — запаниковала бы
            // на срезе с началом за концом (Decision 7: усечение обязано
            // быть видно как ошибка, не как паника).
            Ok(n) => filled += n.min(buf.len() - filled),
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
        if records.is_empty() {
            return Ok(());
        }

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
        // Тело кадра целиком — один вызов кодека v3: эпоха, группы сообщений
        // (флаги и обе метки один раз на сообщение), дельты цены и размера.
        // Полевой бюджет кодировщик считает всегда, но здесь он не нужен: его
        // читает только замер (`binlog-stats --reencode`), и отбрасывается он
        // бесплатно — это `Copy`-структура из `usize`, не аллокация.
        encode_frame_payload_v3(records, &mut self.scratch);

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

    /// Тот же приёмник, изменяемо: им закрывают часть на ходу (A8.1 —
    /// инструмент убран из пула: `FrameSink::close` отпускает дескриптор,
    /// счётчики приёмника остаются). Кадров через `Writer` после этого не
    /// пишут; сам формат не задет.
    pub fn get_mut(&mut self) -> &mut W {
        &mut self.inner
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
/// `version` — версия из заголовка: `VERSION` (текущая) или `VERSION_V2`
/// (то, что пишет живой коллектор до перезапуска). Обе версии читаются одним
/// и тем же `read_frame`; различие — в декодере тела кадра, и другого
/// различия нет: заголовок у версий 2 и 3 общий. Версия хранится в читателе,
/// потому что без неё нельзя решить, каким декодером разбирать кадр, а
/// угадывать по содержимому — это ровно то тихое неверное чтение, от которого
/// версия в заголовке и защищает.
///
/// `decompressor` переиспользуется между кадрами по той же причине, что и
/// `compressor` у `Writer` (см. его доку): держит один `DCtx` вместо того,
/// чтобы заводить новый на каждый вызов. Не заявлено как часть бюджета GC
/// «ноль на событие» — тот бюджет про путь разбор-и-запись рекордера, а
/// чтение обслуживает `verify`/`export`/`markout`, но переиспользование
/// не стоит ничего и держит `Reader` симметричным `Writer`.
pub struct Reader<R: Read> {
    inner: Body<R>,
    header: Header,
    version: u8,
    frames_read: u64,
    /// Счётчики мёртвых полей версии 2 (у v3 таких полей нет вовсе). Копятся
    /// по ходу чтения, потому что иначе их неоткуда взять: в `Record` этих
    /// полей нет, а доказательство «0 % ненулевых» должно оставаться
    /// проверяемым на любом старом файле.
    legacy_dead: LegacyDeadFields,
    /// Полная длина последнего прочитанного кадра **на диске** (префикс +
    /// тело): замеру M1 тикета 43 нужна базовая линия «сколько файл занимает
    /// сейчас», и взять её из самого читателя честнее, чем считать позицию
    /// файла снаружи. У контейнера архива покадровых байт на диске нет
    /// (сжатие общее на весь файл) — там это длина **тела** кадра в
    /// распакованном потоке, и её же кладут на диск при архивации.
    last_frame_bytes: usize,
    /// Уровень zstd контейнера архива, `None` у обычного файла (T46). Нужен
    /// печати (`binlog-stats` именует источник архива) и тестам.
    archive_level: Option<u8>,
    decompressor: zstd::bulk::Decompressor<'static>,
    /// Был ли остановлен на обрезанном **хвостовом** кадре (`read_frame_soft`,
    /// A4, 2026-09-17): файл живой записи читатель может застать между
    /// `write` и полным кадром, и это не порча — см. `ShortRead` выше.
    truncated_tail: bool,
}

/// Откуда `Reader` берёт байты после опознания формата: обычный суточный файл
/// или контейнер архива. Заголовок и кадры читаются одинаково, различие — в
/// теле кадра (T46).
enum Body<R: Read> {
    /// Обычный файл (v2 или v3): кадры сжаты покадрово. Первые
    /// `MAGIC_VERSION_LEN` байт сюда **не** входят — они уже прочитаны и
    /// разобраны при опознании формата (`Reader::open`), и второй раз их
    /// отдавать значило бы сдвинуть весь хвост заголовка на пять байт.
    Plain(R),
    /// Контейнер архива: **один** zstd-поток на весь файл. Распаковывается
    /// по мере чтения (`Decoder`), а не целиком в память: суточный файл
    /// разжимается в единицы-десятки раз больше, чем занимает на диске, и
    /// держать его целиком в памяти у команды, которая может идти по сотне
    /// символов, нельзя. Внутри — тот же заголовок v3 и те же тела кадров,
    /// только без покадрового сжатия.
    ///
    /// `Chain` — потому что первые байты (магия zstd) already прочитаны при
    /// опознании формата, а декодер обязан увидеть поток с самого начала:
    /// zstd читает свой заголовок именно там.
    Archive(
        zstd::stream::read::Decoder<
            'static,
            io::BufReader<io::Chain<io::Cursor<[u8; MAGIC_VERSION_LEN]>, R>>,
        >,
    ),
}

impl<R: Read> Body<R> {
    /// То же `read_upto`, что и у обычного файла, но по телу: у контейнера
    /// это разжимающийся на лету поток.
    fn read_upto(&mut self, buf: &mut [u8]) -> io::Result<ReadStatus> {
        match self {
            Self::Plain(inner) => read_upto(inner, buf),
            Self::Archive(decoder) => read_upto(decoder, buf),
        }
    }
}

impl<R: Read> fmt::Debug for Reader<R> {
    /// Ручная реализация, не `#[derive]`: `zstd::bulk::Decompressor` не
    /// реализует `Debug` (см. ту же причину у `Writer` выше).
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Reader")
            .field("header", &self.header)
            .field("version", &self.version)
            .field("frames_read", &self.frames_read)
            .field("archive_level", &self.archive_level)
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
    ///
    /// Версии 2 и 3 различаются **только телом кадра**, поэтому хвост
    /// заголовка у них общий и читается одинаково; различие уходит в
    /// `read_frame` через сохранённую `version`.
    ///
    /// **Контейнер архива (T46) опознаётся по магии zstd в первых четырёх
    /// байтах** (`archive::ZSTD_MAGIC`): суточный файл начинается с `MAGIC`
    /// (`ABLG`), и перепутать их нельзя — ни один из них не начинается с
    /// четырёх байт другого. Дальше контейнер читается тем же кодом: внутри
    /// него лежат тот же заголовок v3 и те же тела кадров, отличается только
    /// способ их получения (разжимается весь поток, а не каждый кадр).
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
        // Прочитанные байты возвращаются в поток только контейнеру: zstd
        // обязан увидеть свой поток с первого байта. Обычному пути они уже
        // не нужны — magic и версия из них прочитаны ниже, а хвост заголовка
        // идёт следом за ними.
        if prefix[0..4] == archive::ZSTD_MAGIC {
            let decoder = zstd::stream::read::Decoder::new(io::Cursor::new(prefix).chain(inner))?;
            return Self::open_container(Body::Archive(decoder));
        }
        if prefix[0..4] != MAGIC {
            return Err(BinlogError::BadMagic {
                got: [prefix[0], prefix[1], prefix[2], prefix[3]],
            });
        }
        let version = prefix[4];
        if version != VERSION && version != VERSION_V2 {
            return Err(BinlogError::UnsupportedVersion { got: version });
        }
        let mut body = Body::Plain(inner);
        let header = read_header_tail(&mut body, 0)?;
        Ok(Self {
            inner: body,
            header,
            version,
            frames_read: 0,
            legacy_dead: LegacyDeadFields::default(),
            last_frame_bytes: 0,
            archive_level: None,
            decompressor: zstd::bulk::Decompressor::new()?,
            truncated_tail: false,
        })
    }

    /// Дочитывает контейнер архива: свой маркер, а за ним — обычный заголовок
    /// v3. Только версия 3: контейнер собирается из v3-файлов (v2 в него не
    /// кладут — `archive::write_container` отказывается), и версия внутри
    /// контейнера проверяется ровно так же строго, как в файле. Ошибки
    /// `TruncatedHeader` считают `got`/`want` в байтах **распакованного**
    /// контейнера: снаружи у него нет ни «начала файла», ни «конца суток»,
    /// по которым можно было бы назвать смещение.
    fn open_container(mut body: Body<R>) -> Result<Self, BinlogError> {
        let mut container = [0u8; archive::HEADER_LEN];
        match body.read_upto(&mut container)? {
            ReadStatus::Full => {}
            ReadStatus::Partial(got) => {
                return Err(BinlogError::TruncatedHeader {
                    got,
                    want: archive::HEADER_LEN,
                })
            }
            ReadStatus::Eof => {
                return Err(BinlogError::TruncatedHeader {
                    got: 0,
                    want: archive::HEADER_LEN,
                })
            }
        }
        archive::validate_container_header(&container)?;
        let level = container[archive::HEADER_LEVEL_AT];
        let mut magic_version = [0u8; MAGIC_VERSION_LEN];
        match body.read_upto(&mut magic_version)? {
            ReadStatus::Full => {}
            ReadStatus::Partial(got) => {
                return Err(BinlogError::TruncatedHeader {
                    got: archive::HEADER_LEN + got,
                    want: archive::HEADER_LEN + HEADER_LEN,
                })
            }
            ReadStatus::Eof => {
                return Err(BinlogError::TruncatedHeader {
                    got: archive::HEADER_LEN,
                    want: archive::HEADER_LEN + HEADER_LEN,
                })
            }
        }
        if magic_version[0..4] != MAGIC {
            return Err(BinlogError::BadMagic {
                got: [
                    magic_version[0],
                    magic_version[1],
                    magic_version[2],
                    magic_version[3],
                ],
            });
        }
        if magic_version[4] != VERSION {
            return Err(BinlogError::UnsupportedVersion {
                got: magic_version[4],
            });
        }
        let header = read_header_tail(&mut body, archive::HEADER_LEN)?;
        Ok(Self {
            inner: body,
            header,
            version: VERSION,
            frames_read: 0,
            legacy_dead: LegacyDeadFields::default(),
            last_frame_bytes: 0,
            archive_level: Some(level),
            decompressor: zstd::bulk::Decompressor::new()?,
            truncated_tail: false,
        })
    }

    pub fn header(&self) -> Header {
        self.header
    }

    /// Версия формата из заголовка: `VERSION` (текущая, её пишет `Writer`) или
    /// `VERSION_V2` (живой коллектор до перезапуска).
    pub fn version(&self) -> u8 {
        self.version
    }

    /// Уровень zstd контейнера архива (T46), `None` у обычного суточного
    /// файла. Не влияет на разбор: уровень — запись о том, чем сутки сжаты,
    /// и нужен печати (`binlog-stats`) и тестам.
    pub fn archive_level(&self) -> Option<u8> {
        self.archive_level
    }

    /// Счётчики мёртвых полей, накопленные на прочитанных кадрах версии 2.
    /// На файле версии 3 они нулевые по построению: таких полей в формате нет.
    pub fn legacy_dead_fields(&self) -> LegacyDeadFields {
        self.legacy_dead
    }

    /// Полная длина последнего прочитанного кадра на диске (префикс + тело).
    pub fn last_frame_bytes(&self) -> usize {
        self.last_frame_bytes
    }

    /// Следующее **тело** кадра — как оно записано в источнике: у обычного
    /// файла это тело, разжатое покадрово zstd, у контейнера архива — байты,
    /// которые лежат в нём как есть (T46). Отдельно от `read_frame`, потому
    /// что архивация обязана сравнивать тела кадров **побайтово**: из
    /// `Vec<Record>` байтовое равенство кадра не следует — раскладка того же
    /// набора записей может отличаться группами.
    ///
    /// `None` на чистом конце потока **после** хотя бы одного прочитанного
    /// кадра. Любая нехватка байт внутри объявленной длины — `ShortRead`,
    /// никогда не `Ok(None)` и никогда не паника (Decision 7: усечение
    /// обязано быть видно как короткое чтение, а не как молчаливый пустой
    /// хвост). `frames_read` растёт здесь, а не в `read_frame`: кадр
    /// прочитан из потока в тот момент, когда прочитано его тело.
    ///
    /// Срезы чанка ниже доказаны: `want ≤ READ_CHUNK = chunk.len()` через
    /// `min`, `n` из `Partial(n)` не превышает запрошенного по контракту
    /// `read_upto`; проверка через `get` в цикле ввода-вывода — мёртвый код.
    pub fn read_body(&mut self) -> Result<Option<Vec<u8>>, BinlogError> {
        let mut len_buf = [0u8; LEN_PREFIX];
        match self.inner.read_upto(&mut len_buf)? {
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
        self.last_frame_bytes = LEN_PREFIX + len;

        // Ремонт W3 (ревью 23.09): раньше потолок заголовка проверялся только
        // ПОСЛЕ того, как эти `len` байт уже прочитаны — у обычного файла это
        // ещё сжатые байты, но у контейнера архива (`Body::Archive`) тело
        // лежит несжатым и читается через разжимающийся на лету поток
        // (`zstd::stream::read::Decoder`), то есть «прочитаны» там уже значит
        // «разжаты». Испорченный префикс длины (один перевёрнутый бит, до
        // `u32::MAX`) тянул в память гигабайты ДО единственной проверки —
        // ровно то «декомпрессия сначала, проверка после», от которого
        // Decision 23 (ревизия 10) требует уйти, только на шаг раньше в этой
        // же функции. Потолок — тот же самый, что раньше стоял после чтения
        // (`max_frame_record_bytes` для уже разжатого архива,
        // `max_frame_bytes_on_disk` для ещё сжатого обычного файла — то же
        // выражение, которым сам `Writer` резервирует буфер под кадр, поэтому
        // настоящий кадр этого писателя в него гарантированно укладывается),
        // сравнивается с длиной сразу, до единого байта чтения тела.
        let len_ceiling = match self.inner {
            Body::Archive(_) => max_frame_record_bytes(self.header.max_records_per_frame),
            Body::Plain(_) => max_frame_bytes_on_disk(self.header.max_records_per_frame as usize),
        };
        if len > len_ceiling {
            return Err(BinlogError::FrameExceedsHeaderCeiling {
                max_records_per_frame: self.header.max_records_per_frame,
                ceiling_bytes: len_ceiling,
            });
        }

        // `len` прошла потолок заголовка, но заголовок — те же данные с
        // диска, что и поле длины (см. проверку выше и её обоснование):
        // остаётся читать кусками, а не аллоцировать `len` байт заранее —
        // буфер растёт только на то, что реально пришло, и испорченная
        // длина (в границах потолка, но всё ещё больше настоящего файла)
        // обрывается на `ShortRead` первого недостающего куска, а не на
        // попытке выделить впрок то, чего на диске нет.
        const READ_CHUNK: usize = 64 * 1024;
        let mut stored = Vec::with_capacity(len.min(READ_CHUNK));
        let mut got = 0usize;
        let mut chunk = [0u8; READ_CHUNK];
        while got < len {
            let want = (len - got).min(READ_CHUNK);
            match self.inner.read_upto(&mut chunk[..want])? {
                ReadStatus::Full => {
                    stored.extend_from_slice(&chunk[..want]);
                    got += want;
                }
                ReadStatus::Partial(n) => {
                    stored.extend_from_slice(&chunk[..n]);
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
        self.frames_read += 1;

        if matches!(self.inner, Body::Archive(_)) {
            // Тело контейнера лежит несжатым: это и есть тело кадра v3.
            // Потолок уже проверен выше, до чтения (`len_ceiling`) — `stored.
            // len() == len` (цикл выше не выходит иначе) не может превысить
            // его повторно; `debug_assert!` — граница инварианта, а не
            // рабочая проверка (она и не имеет права сработать в релизе, раз
            // выше `len > len_ceiling` уже вернула ошибку).
            debug_assert!(
                stored.len() <= max_frame_record_bytes(self.header.max_records_per_frame),
                "len_ceiling выше обязан был отвергнуть этот кадр раньше"
            );
            return Ok(Some(stored));
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
        //
        // Второй, более тесный потолок — от содержимого, не только от
        // заголовка (ремонт W3, ревью 23.09), и только когда первый уже
        // насыщен об `HARD_PAYLOAD_CEILING`: насыщение — признак вероятно
        // испорченного `max_records_per_frame` (ни один вызывающий в этом
        // дереве, включая тесты с намеренно завышенным потолком, не просит
        // больше нескольких миллионов записей на кадр, а `HARD_PAYLOAD_
        // CEILING` — щедрый бюджет **суток**, не одного кадра), и тогда 4
        // испорченных байта заголовка всё ещё требовали бы до 1.5 ГБ
        // аллокации под кадр, чьё сжатое тело на диске — считаные байты.
        // `stored.len() * MAX_DECOMPRESSION_RATIO` — тот же коэффициент
        // распаковки, которым уже обоснован сам `HARD_PAYLOAD_CEILING`,
        // только от РЕАЛЬНОГО размера этого кадра на диске. Условие на
        // насыщении, а не безусловный `min()`, — намеренно: честный
        // (ненасыщенный) потолок заголовка, каким бы большим он ни был
        // назначен вызывающим (`DEFAULT_TEST_MAX_RECORDS_PER_FRAME` тестов
        // этого файла в том числе), этим ремонтом не тронут вовсе — тесно
        // сравнивать с реальным сжатием годится только тогда, когда сам
        // заголовок уже не выглядит правдоподобным.
        let header_ceiling = max_frame_payload_bytes(self.header.max_records_per_frame);
        let ceiling_bytes = if header_ceiling == HARD_PAYLOAD_CEILING {
            header_ceiling.min(stored.len().saturating_mul(MAX_DECOMPRESSION_RATIO))
        } else {
            header_ceiling
        };
        let payload = self
            .decompressor
            .decompress(&stored, ceiling_bytes)
            .map_err(|_| BinlogError::FrameExceedsHeaderCeiling {
                max_records_per_frame: self.header.max_records_per_frame,
                ceiling_bytes,
            })?;
        Ok(Some(payload))
    }

    /// Возвращает следующий кадр как список записей — `read_body` плюс
    /// декодирование по версии источника.
    pub fn read_frame(&mut self) -> Result<Option<Vec<Record>>, BinlogError> {
        let Some(payload) = self.read_body()? else {
            return Ok(None);
        };
        // Версию выбирает заголовок, а не содержимое кадра: угадывание по
        // байтам тела — ровно то тихое неверное чтение, от которого версия
        // в заголовке и защищает (`Reader::open`).
        let records = if self.version == VERSION_V2 {
            let mut dead = self.legacy_dead;
            let records = decode_frame_payload_v2(&payload, &mut dead)?;
            self.legacy_dead = dead;
            records
        } else {
            decode_frame_payload_v3(&payload)?
        };
        Ok(Some(records))
    }

    /// Кадр живого файла (A4, 2026-09-17): обрезанный **хвостовой** кадр —
    /// это не порча, а «файл ещё пишется»: читатель может застать момент
    /// между записью префикса и дописыванием тела. Отличие от `read_frame`
    /// ровно одно: `ShortRead` здесь означает конец прочитанного (`Ok(None)`)
    /// и поднимает флаг `truncated_tail()`, который вызывающий печатает
    /// предупреждением; всё остальное (`Corrupt`, `MissingSnapshot`,
    /// `TruncatedHeader`) остаётся ошибкой — эти состояния об обрыве хвоста не
    /// говорят.
    pub fn read_frame_soft(&mut self) -> Result<Option<Vec<Record>>, BinlogError> {
        match self.read_frame() {
            Ok(frame) => Ok(frame),
            Err(BinlogError::ShortRead { .. }) => {
                self.truncated_tail = true;
                Ok(None)
            }
            Err(e) => Err(e),
        }
    }

    /// Остановился ли последний `read_frame_soft` на обрезанном хвосте.
    pub fn truncated_tail(&self) -> bool {
        self.truncated_tail
    }
}

#[cfg(test)]
mod tests;
