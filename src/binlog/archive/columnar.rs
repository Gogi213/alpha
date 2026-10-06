//! Колоночный контейнер (TK-048, В-185): тела кадров v3 разложены по семи
//! varint-потокам на весь файл (`ev`, `exch_dt`, `local_dt`, `attrs`, `count`,
//! `price_dt`, `qty_dt`) и сжаты одним zstd с длинным окном. Замер chain62:
//! 66–75 % от размера обычного файла против 88 % у контейнера версии 1
//! (уровень 9). `Reader` собирает из колонок те же тела кадров — побайтово
//! равные исходным.
//!
//! ```text
//! [ABLA | 2 | уровень] [заголовок суток v3/v4]
//! uvarint nframes | nframes × uvarint групп в кадре
//! nframes × 8 Б эпох кадров
//! 7 × (uvarint длина | байты колонки)
//! ```

use std::io::{self, Read, Write};
use std::time::Instant;

use super::{
    header_bytes, max_level, ArchiveWrite, BinlogError, CountingWriter, Reader, ARCHIVE_MAGIC,
    HEADER_LEN, HEADER_LEVEL_AT, VERSION, VERSION_V4,
};

pub const ARCHIVE_VERSION_COLUMNAR: u8 = 2;

/// Окно zstd (log2): 2^27 — потолок окна декодера по умолчанию, замер chain62
/// шёл с `--long=27`.
const WINDOW_LOG: u32 = 27;

const COLUMN_COUNT: usize = 7;
/// Колонок на группу до пар цена/размер: ev, exch_dt, local_dt, attrs, count.
const GROUP_COLUMNS: usize = 5;

fn varint_end(buf: &[u8], pos: usize) -> Result<usize, BinlogError> {
    let mut j = pos;
    loop {
        let b = *buf
            .get(j)
            .ok_or_else(|| BinlogError::Corrupt("varint колонки обрезан".into()))?;
        j += 1;
        if b < 0x80 {
            return Ok(j);
        }
        if j - pos >= 10 {
            return Err(BinlogError::Corrupt("varint колонки длиннее 64 бит".into()));
        }
    }
}

fn put_uvarint<W: Write>(out: &mut W, v: u64) -> io::Result<()> {
    let mut buf = Vec::with_capacity(10);
    super::super::write_uvarint(&mut buf, v);
    out.write_all(&buf)
}

/// Раскладывает тело кадра по колонкам; возвращает число групп. Тело короче
/// эпохи или с оборванным varint — ошибка.
fn split_body(
    body: &[u8],
    epochs: &mut Vec<u8>,
    cols: &mut [Vec<u8>; COLUMN_COUNT],
) -> Result<u64, BinlogError> {
    if body.len() < 8 {
        return Err(BinlogError::Corrupt("тело кадра короче эпохи".into()));
    }
    epochs.extend_from_slice(&body[..8]);
    let mut i = 8;
    let mut groups = 0u64;
    while i < body.len() {
        let mut count_at = i;
        for (c, col) in cols.iter_mut().enumerate().take(GROUP_COLUMNS) {
            let j = varint_end(body, i)?;
            col.extend_from_slice(&body[i..j]);
            if c == GROUP_COLUMNS - 1 {
                count_at = i;
            }
            i = j;
        }
        let count = super::super::read_uvarint(body, &mut count_at)?;
        for _ in 0..count {
            for col in &mut cols[GROUP_COLUMNS..] {
                let j = varint_end(body, i)?;
                col.extend_from_slice(&body[i..j]);
                i = j;
            }
        }
        groups += 1;
    }
    Ok(groups)
}

/// Пишет колоночный контейнер из открытого читателя v3/v4.
pub fn write_container_columnar<R: Read, W: Write>(
    src: &mut Reader<R>,
    level: i32,
    out: W,
) -> Result<ArchiveWrite, BinlogError> {
    if src.version() != VERSION && src.version() != VERSION_V4 {
        return Err(BinlogError::UnsupportedVersion { got: src.version() });
    }
    if !(1..=max_level()).contains(&level) {
        return Err(BinlogError::Corrupt(format!(
            "уровень zstd {level} вне 1..={}: такое значение zstd не примет",
            max_level()
        )));
    }
    let started = Instant::now();
    let mut epochs = Vec::new();
    let mut cols: [Vec<u8>; COLUMN_COUNT] = Default::default();
    let mut groups = Vec::new();
    while let Some(body) = src.read_body()? {
        groups.push(split_body(&body, &mut epochs, &mut cols)?);
    }
    let counted = CountingWriter {
        inner: out,
        bytes: 0,
    };
    let mut encoder = zstd::stream::write::Encoder::new(counted, level)?;
    encoder.long_distance_matching(true)?;
    encoder.window_log(WINDOW_LOG)?;
    let level_byte = u8::try_from(level)
        .map_err(|_| BinlogError::Corrupt(format!("уровень {level} не влезает в байт")))?;
    let mut container = [0u8; HEADER_LEN];
    container[..4].copy_from_slice(&ARCHIVE_MAGIC);
    container[4] = ARCHIVE_VERSION_COLUMNAR;
    container[HEADER_LEVEL_AT] = level_byte;
    encoder.write_all(&container)?;
    encoder.write_all(&header_bytes(&src.header(), src.step_schedule()))?;
    put_uvarint(&mut encoder, groups.len() as u64)?;
    for g in &groups {
        put_uvarint(&mut encoder, *g)?;
    }
    encoder.write_all(&epochs)?;
    for col in &cols {
        put_uvarint(&mut encoder, col.len() as u64)?;
        encoder.write_all(col)?;
    }
    let counted = encoder.finish()?;
    Ok(ArchiveWrite {
        frames: groups.len() as u64,
        archive_bytes: counted.bytes,
        level,
        write_ms: started.elapsed().as_millis(),
    })
}

fn take_slice<'a>(blob: &'a [u8], pos: &mut usize, n: usize) -> Result<&'a [u8], BinlogError> {
    let end = pos
        .checked_add(n)
        .filter(|e| *e <= blob.len())
        .ok_or_else(|| BinlogError::Corrupt("колоночный контейнер обрезан".into()))?;
    let s = &blob[*pos..end];
    *pos = end;
    Ok(s)
}

fn copy_varint(col: &[u8], cur: &mut usize, out: &mut Vec<u8>) -> Result<(), BinlogError> {
    let j = varint_end(col, *cur)?;
    out.extend_from_slice(&col[*cur..j]);
    *cur = j;
    Ok(())
}

/// Собирает из тела колоночного контейнера (всё после заголовка суток) поток
/// `[u32 длина | тело кадра]*` — то, что лежит в контейнере версии 1.
pub(in crate::binlog) fn columnar_to_stream(blob: &[u8]) -> Result<Vec<u8>, BinlogError> {
    let mut pos = 0usize;
    let nframes = super::super::read_uvarint(blob, &mut pos)? as usize;
    if nframes > blob.len() {
        return Err(BinlogError::Corrupt(
            "число кадров больше тела контейнера".into(),
        ));
    }
    let mut groups = Vec::with_capacity(nframes);
    for _ in 0..nframes {
        groups.push(super::super::read_uvarint(blob, &mut pos)?);
    }
    let epochs = take_slice(blob, &mut pos, nframes * 8)?;
    let mut cols: Vec<&[u8]> = Vec::with_capacity(COLUMN_COUNT);
    for _ in 0..COLUMN_COUNT {
        let n = super::super::read_uvarint(blob, &mut pos)? as usize;
        cols.push(take_slice(blob, &mut pos, n)?);
    }
    let mut cur = [0usize; COLUMN_COUNT];
    let mut out = Vec::with_capacity(blob.len() + blob.len() / 4);
    let mut body = Vec::new();
    for (f, g) in groups.iter().enumerate() {
        body.clear();
        body.extend_from_slice(&epochs[f * 8..f * 8 + 8]);
        for _ in 0..*g {
            let mut count_at = body.len();
            for c in 0..GROUP_COLUMNS {
                if c == GROUP_COLUMNS - 1 {
                    count_at = body.len();
                }
                copy_varint(cols[c], &mut cur[c], &mut body)?;
            }
            let count = super::super::read_uvarint(&body, &mut count_at)?;
            for _ in 0..count {
                for c in GROUP_COLUMNS..COLUMN_COUNT {
                    copy_varint(cols[c], &mut cur[c], &mut body)?;
                }
            }
        }
        let len = u32::try_from(body.len())
            .map_err(|_| BinlogError::FrameTooLarge { len: body.len() })?;
        out.extend_from_slice(&len.to_le_bytes());
        out.extend_from_slice(&body);
    }
    if cur.iter().zip(&cols).any(|(c, col)| *c != col.len()) {
        return Err(BinlogError::Corrupt(
            "в колонках остались неразобранные байты".into(),
        ));
    }
    Ok(out)
}
