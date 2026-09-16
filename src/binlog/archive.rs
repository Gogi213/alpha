//! Контейнер архива закрытых суток (T46): **один** zstd-поток над
//! `[маркер ABLA | версия | уровень]` + `[заголовок v3 (25 Б)]` +
//! `[u32 длина | тело кадра без сжатия]*`.
//!
//! # Зачем отдельный контейнер, а не «пересжать кадры»
//!
//! Живой путь сжимает каждый кадр отдельно уровнем 1: это 24 % экономии
//! бесплатно, но контексту негде расти — кадр ~5 КБ. Пересжатие кадров тем же
//! zstd-19 добавляет всего **−1.6 %** (`docs/findings/
//! archive-compression-2026-09-15.md`), а один поток на весь файл —
//! **−7.7 %** от сегодняшнего размера (92.3 %): контекст теперь общий на
//! сутки, и дельты цены/размера из соседних кадров видит один и тот же
//! энкодер. Формат v3 при этом не меняется (В-49): `binlog` по-прежнему
//! хранит кадры, меняется только то, что у контейнера они лежат **несжатыми**
//! внутри общего потока.
//!
//! # Что здесь есть и чего нет
//!
//! - `write_container` — архивация уже открытого читателя в приёмник;
//! - `verify_round_trip` — **обязательная** сверка: тела кадров побайтово и
//!   записи покадрово, иначе архив не считается годным;
//! - `read_stats` — проход по архиву (кадры, записи, байты): им меряется
//!   время чтения, и он же проверяет, что контейнер читается тем же
//!   `Reader`, что и обычный файл.
//!
//! Удаления оригинала здесь нет вовсе: это решение оператора, и живёт оно в
//! команде (`commands::lob::archive`), которая ещё и требует маркер сверки
//! `verify-<SYMBOL>.status == ok` в том же каталоге.
//!
//! `xz` не поддерживается осознанно (решение владельца, T46): он требует
//! новой зависимости (liblzma), а по замеру выигрывает у zstd-19 всего 2.8
//! процентных пункта при разжатии в 20–40 раз медленнее — при том, что анализ
//! по архиву читает гигабайты на каждый прогон.

use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::time::Instant;

use super::{
    strip_binlog_suffix, BinlogError, Header, Reader, BINLOG_ARCHIVE_SUFFIX, MAGIC, VERSION,
};

/// Магия контейнера: `ABLA` — «alpha binlog archive». Лежит **внутри**
/// zstd-потока, первыми его байтами: снаружи контейнер опознаётся магией
/// самого zstd (`ZSTD_MAGIC`), а эта — уже распакованным читателем, и
/// отличает «архив» от «файл v3, который начали читать не тем кодом».
/// Внутренний заголовок v3 идёт следом и одинаков у архива и у обычного
/// файла, поэтому `Reader` разбирает его одним и тем же кодом.
pub const ARCHIVE_MAGIC: [u8; 4] = *b"ABLA";

/// Версия контейнера. Меняется при несовместимой правке его раскладки
/// (сейчас это маркер, версия, уровень, дальше заголовок суток); версия
/// **формата** кадров живёт в заголовке суток и не дублируется.
pub const ARCHIVE_VERSION: u8 = 1;

/// Первые четыре байта любого zstd-потока (little-endian `0xFD2FB528`).
/// Единственное, чем контейнер отличается от суточного файла снаружи: файл
/// начинается с `MAGIC` (`ABLG`), архив — с этого числа. Перепутать нельзя, и
/// это позволяет опознать архив, не глядя на расширение: `Reader` работает и
/// с потоком из памяти, у которого имени нет вовсе.
pub const ZSTD_MAGIC: [u8; 4] = [0x28, 0xB5, 0x2F, 0xFD];

/// Уровень zstd по умолчанию: 19 — замер `docs/findings/
/// archive-compression-2026-09-15.md` (92.3 % от файла на диске против
/// 95.6 % у уровня 9). Число не изобретено — оно измерено на боевом файле.
pub const DEFAULT_LEVEL: i32 = 19;

/// Верхняя граница `--level`: столько позволяет сам zstd (`zstd_safe::
/// max_c_level`; уровень 22 — «ultra» в CLI, но библиотеке он доступен и без
/// отдельного ключа). Проверяется до создания энкодера, чтобы отказ был
/// сказан числом вызывающего, а не ошибкой внутри zstd.
pub fn max_level() -> i32 {
    zstd::zstd_safe::max_c_level()
}

/// Шапка контейнера: магия(4) + версия(1) + уровень(1).
pub const HEADER_LEN: usize = 4 + 1 + 1;

/// Смещение байта уровня в шапке контейнера — им пользуется `Reader`, чтобы
/// не собирать вторую копию раскладки.
pub(super) const HEADER_LEVEL_AT: usize = 5;

/// Итог архивации: сколько кадров ушло в контейнер, сколько он занял и
/// сколько это заняло времени. Записей здесь нет намеренно: их считает
/// `verify_round_trip`, которая всё равно декодирует все кадры, — второй
/// проход декодера на архивации был бы работой ради числа, которое уже
/// считается обязательным шагом.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct ArchiveWrite {
    pub frames: u64,
    /// Байт в контейнере (то, что реально ушло в приёмник).
    pub archive_bytes: u64,
    /// Уровень zstd, которым контейнер собран.
    pub level: i32,
    /// Длительность записи контейнера, мс.
    pub write_ms: u128,
}

/// Итог сверки round-trip.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct ArchiveVerify {
    pub frames: u64,
    pub records: u64,
    pub millis: u128,
}

/// Итог прохода по контейнеру (чтение архива тем же `Reader`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct ArchiveRead {
    pub frames: u64,
    pub records: u64,
    pub bytes: u64,
    pub level: i32,
    pub millis: u128,
}

/// Проверяет шапку контейнера: магия и версия. Зовётся `Reader`-ом на
/// распакованном потоке: «это контейнер» снаружи уже решено магией zstd, а
/// здесь проверяется, что внутри действительно наш архив, а не что-то ещё,
/// что тем же zstd сжали.
pub(super) fn validate_container_header(bytes: &[u8]) -> Result<(), BinlogError> {
    let magic: [u8; 4] = bytes
        .get(..4)
        .and_then(|b| b.try_into().ok())
        .ok_or_else(|| BinlogError::Corrupt("шапка контейнера короче магии".into()))?;
    if magic != ARCHIVE_MAGIC {
        return Err(BinlogError::Corrupt(format!(
            "магия контейнера не ABLA, а {magic:?}"
        )));
    }
    let version = bytes[4];
    if version != ARCHIVE_VERSION {
        return Err(BinlogError::Corrupt(format!(
            "версия контейнера {version}, а этот код знает {ARCHIVE_VERSION}"
        )));
    }
    Ok(())
}

/// Приёмник, считающий записанные байты: размер контейнера нужен отчёту, а
/// `Write` его не сообщает — `metadata` есть у файла, но не у любого
/// приёмника, и требовать его от `W` значило бы сузить шов до `File`.
struct CountingWriter<W: Write> {
    inner: W,
    bytes: u64,
}

impl<W: Write> Write for CountingWriter<W> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let n = self.inner.write(buf)?;
        self.bytes += n as u64;
        Ok(n)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.inner.flush()
    }
}

/// Пишет контейнер архива из уже открытого читателя в приёмник.
///
/// Источник обязан быть версии 3 (`VERSION`): v2-файл живого коллектора
/// архивировать нечем — его тело устроено иначе, и «архив» из него читался бы
/// v3-декодером как мусор. Требование сформулировано ошибкой, а не молчанием.
///
/// Кадры берутся `Reader::read_body` — **телами**, а не записями: сверка
/// round-trip обязана быть побайтовой, а из `Vec<Record>` байтовое равенство
/// кадра не следует (раскладка того же набора записей по группам может
/// отличаться при том же содержимом).
pub fn write_container<R: Read, W: Write>(
    src: &mut Reader<R>,
    level: i32,
    out: W,
) -> Result<ArchiveWrite, BinlogError> {
    if src.version() != VERSION {
        return Err(BinlogError::UnsupportedVersion { got: src.version() });
    }
    if !(1..=max_level()).contains(&level) {
        return Err(BinlogError::Corrupt(format!(
            "уровень zstd {level} вне 1..={}: такое значение zstd не примет",
            max_level()
        )));
    }
    let started = Instant::now();
    let counted = CountingWriter {
        inner: out,
        bytes: 0,
    };
    let mut encoder = zstd::stream::write::Encoder::new(counted, level)?;

    let level_byte = u8::try_from(level)
        .map_err(|_| BinlogError::Corrupt(format!("уровень {level} не влезает в байт")))?;
    let mut container = [0u8; HEADER_LEN];
    container[..4].copy_from_slice(&ARCHIVE_MAGIC);
    container[4] = ARCHIVE_VERSION;
    container[HEADER_LEVEL_AT] = level_byte;
    encoder.write_all(&container)?;
    encoder.write_all(&header_bytes(&src.header()))?;

    let mut frames = 0u64;
    let mut len_buf = [0u8; 4];
    while let Some(body) = src.read_body()? {
        let len = u32::try_from(body.len())
            .map_err(|_| BinlogError::FrameTooLarge { len: body.len() })?;
        len_buf.copy_from_slice(&len.to_le_bytes());
        encoder.write_all(&len_buf)?;
        encoder.write_all(&body)?;
        frames += 1;
    }
    let counted = encoder.finish()?;
    Ok(ArchiveWrite {
        frames,
        archive_bytes: counted.bytes,
        level,
        write_ms: started.elapsed().as_millis(),
    })
}

/// Заголовок суток как 25 байт: та же раскладка, что пишет `Writer::create`.
/// Собирается из `Header`, а не копируется байтами из источника: «архив
/// хранит тот же заголовок» — свойство, которое здесь и берётся из одного
/// места с обычной записью (`binlog`), а не из второго литерала.
fn header_bytes(header: &Header) -> [u8; super::HEADER_LEN] {
    let mut buf = [0u8; super::HEADER_LEN];
    buf[0..4].copy_from_slice(&MAGIC);
    buf[4] = VERSION;
    buf[5..13].copy_from_slice(&header.tick_e9.to_le_bytes());
    buf[13..21].copy_from_slice(&header.step_e9.to_le_bytes());
    buf[21..25].copy_from_slice(&header.max_records_per_frame.to_le_bytes());
    buf
}

/// Сверяет контейнер с источником **покадрово**: заголовок, число кадров,
/// тела кадров побайтово и записи. Первое же расхождение — ошибка с номером
/// кадра: «архив совпал» без такой сверки означало бы доверие к декодеру,
/// который эту же сверку и должен подтвердить.
///
/// Записи сравниваются отдельно от тел, хотя тела уже равны: равенство тел
/// доказывает равенство записей только если декодер детерминирован, а
/// утверждение «архив эквивалентен исходнику» обязано быть проверенным, а не
/// выведенным. Стоит это копейки: архивация офлайн.
pub fn verify_round_trip<R1: Read, R2: Read>(
    src: &mut Reader<R1>,
    dst: &mut Reader<R2>,
) -> Result<ArchiveVerify, BinlogError> {
    let started = Instant::now();
    if src.header() != dst.header() {
        return Err(BinlogError::Corrupt(format!(
            "заголовок контейнера {:?} не равен исходному {:?}",
            dst.header(),
            src.header()
        )));
    }
    let mut frames = 0u64;
    let mut records = 0u64;
    loop {
        match (src.read_body()?, dst.read_body()?) {
            (None, None) => break,
            (Some(a), Some(b)) => {
                if a != b {
                    return Err(BinlogError::Corrupt(format!(
                        "тело кадра {frames} в контейнере не совпало с исходным \
                         ({} байт против {})",
                        b.len(),
                        a.len()
                    )));
                }
                let ra = super::decode_frame_payload_v3(&a)?;
                let rb = super::decode_frame_payload_v3(&b)?;
                if ra != rb {
                    return Err(BinlogError::Corrupt(format!(
                        "записи кадра {frames} в контейнере не совпали с исходными \
                         ({} против {})",
                        rb.len(),
                        ra.len()
                    )));
                }
                frames += 1;
                records += ra.len() as u64;
            }
            (None, Some(_)) => {
                return Err(BinlogError::Corrupt(
                    "в контейнере кадров больше, чем в исходном файле".into(),
                ))
            }
            (Some(_), None) => {
                return Err(BinlogError::Corrupt(
                    "в контейнере кадров меньше, чем в исходном файле".into(),
                ))
            }
        }
    }
    Ok(ArchiveVerify {
        frames,
        records,
        millis: started.elapsed().as_millis(),
    })
}

/// Проход по контейнеру: сколько в нём кадров и записей и сколько он занимает
/// на диске. Тем же `Reader`, что читает обычный файл, — то есть заодно
/// проверка, что архив открывается существующим кодом без правок.
pub fn read_stats(path: &Path) -> Result<ArchiveRead, BinlogError> {
    let started = Instant::now();
    let file = std::fs::File::open(path)?;
    let bytes = file.metadata()?.len();
    let mut reader = Reader::open(file)?;
    let level = reader.archive_level().map_or(0, i32::from);
    let mut frames = 0u64;
    let mut records = 0u64;
    while let Some(frame) = reader.read_frame()? {
        frames += 1;
        records += frame.len() as u64;
    }
    Ok(ArchiveRead {
        frames,
        records,
        bytes,
        level,
        millis: started.elapsed().as_millis(),
    })
}

/// Размер файла на диске — то, чем меряется «исходный размер» в отчёте
/// (а не суммой распакованных кадров).
pub fn file_bytes(path: &Path) -> io::Result<u64> {
    Ok(std::fs::File::open(path)?.metadata()?.len())
}

/// Имя контейнера по умолчанию для исходного файла: обычный суффикс
/// **заменяется** архивным, а не дописывается к нему
/// (`SOLUSDT-2026-09-15.binlog` → `SOLUSDT-2026-09-15.binlog.zst`, но не
/// `…binlog.binlog.zst`). Так имя суток сохраняется целиком, и резолверы
/// видят архив той же частью тех же суток. Файл без узнаваемого суффикса
/// получает архивный в конец — имя, которого резолверы всё равно не разберут
/// как сутки, но такой файл команда и не архивирует (`run_archive` требует
/// имя по раскладке записи).
pub fn default_out_path(src: &Path) -> PathBuf {
    let raw = src.as_os_str();
    match strip_binlog_suffix(&raw.to_string_lossy()) {
        Some(stem) => {
            let mut name = std::ffi::OsString::from(stem);
            name.push(BINLOG_ARCHIVE_SUFFIX);
            PathBuf::from(name)
        }
        None => {
            let mut name = raw.to_owned();
            name.push(BINLOG_ARCHIVE_SUFFIX);
            PathBuf::from(name)
        }
    }
}

/// Уже ли это контейнер: проверка по имени — команда в этом случае
/// отказывается архивировать архив повторно (двойная обёртка ничего не
/// сжимает, а имя перестаёт разбираться как сутки).
pub fn is_archive_path(path: &Path) -> bool {
    path.to_string_lossy().ends_with(BINLOG_ARCHIVE_SUFFIX)
}

#[cfg(test)]
mod tests;
