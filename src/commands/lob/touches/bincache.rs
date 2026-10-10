//! Каркас двоичного кэша `<csv>.abin` (TK-048, КТ-5): колонки · delta · zigzag-varint · zstd, один на подходы
//! (`abin`) и касания (`tbin`). CSV — источник правды: файл годен, только если размер и mtime CSV совпали; при
//! записи проверяется «двоичное → записи == CSV → записи». Кэш включён, когда задан `ALPHA_APPROACH_BIN_DIR`
//! (зеркало пути CSV под ним; входное дерево не трогаем); без каталога — чтение CSV.

use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

pub(super) const HEAD: usize = 4 + 8 + 8 + 4 + 1;
const DEFAULT_LEVEL: i32 = 3;

/// Путь `.abin` в зеркале пути CSV под `ALPHA_APPROACH_BIN_DIR`; `None` — каталог не задан, кэша нет.
pub(super) fn bin_path(csv: &Path) -> Option<PathBuf> {
    let dir = std::env::var_os("ALPHA_APPROACH_BIN_DIR")?;
    let rel: PathBuf = csv
        .components()
        .filter(|c| matches!(c, std::path::Component::Normal(_)))
        .collect();
    let mut s = PathBuf::from(dir).join(rel).into_os_string();
    s.push(".abin");
    Some(PathBuf::from(s))
}

/// Штамп файла «размер + mtime в нс» — общий для кэшей `.abin` и сайдкаров счёта событий.
pub(crate) fn stamp(csv: &Path) -> Option<(u64, u64)> {
    let m = std::fs::metadata(csv).ok()?;
    let t = m.modified().ok()?.duration_since(UNIX_EPOCH).ok()?;
    Some((
        m.len(),
        t.as_secs() * 1_000_000_000 + u64::from(t.subsec_nanos()),
    ))
}

/// Чтение CSV через кэш: годный `.abin` — из него, иначе CSV и запись `.abin` (tmp + rename).
pub(super) fn read_cached<T: PartialEq>(
    csv: &Path,
    tag: &str,
    read: fn(&Path) -> anyhow::Result<Vec<T>>,
    encode: fn(&[T], u64, u64) -> Option<Vec<u8>>,
    decode: fn(&[u8], u64, u64) -> Option<Vec<T>>,
) -> anyhow::Result<Vec<T>> {
    let (Some(bin), Some((len, mtime))) = (bin_path(csv), stamp(csv)) else {
        return read(csv);
    };
    if let Ok(bytes) = std::fs::read(&bin) {
        if let Some(rows) = decode(&bytes, len, mtime) {
            return Ok(rows);
        }
    }
    let rows = read(csv)?;
    if let Some(bytes) = encode(&rows, len, mtime) {
        if decode(&bytes, len, mtime).as_deref() == Some(&rows[..]) {
            if let Some(parent) = bin.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            let tmp = bin.with_extension(format!("abin.tmp{}", std::process::id()));
            if std::fs::write(&tmp, &bytes).is_ok() && std::fs::rename(&tmp, &bin).is_err() {
                let _ = std::fs::remove_file(&tmp);
            }
        } else {
            eprintln!(
                "{tag}: {} — сверка не сошлась, кэш не записан",
                csv.display()
            );
        }
    }
    Ok(rows)
}

pub(super) fn put(out: &mut Vec<u8>, v: i64) {
    let mut z = ((v << 1) ^ (v >> 63)) as u64;
    while z >= 0x80 {
        out.push((z as u8) | 0x80);
        z >>= 7;
    }
    out.push(z as u8);
}

/// День файла — префикс полезной нагрузки: длина varint + байты.
pub(super) fn put_day(p: &mut Vec<u8>, day: &str) {
    put(p, day.len() as i64);
    p.extend_from_slice(day.as_bytes());
}

pub(super) fn put_plain(p: &mut Vec<u8>, it: impl Iterator<Item = i64>) {
    it.for_each(|v| put(p, v));
}

pub(super) fn put_delta(p: &mut Vec<u8>, it: impl Iterator<Item = i64>) {
    let mut prev = 0i64;
    for v in it {
        put(p, v.wrapping_sub(prev));
        prev = v;
    }
}

/// Заголовок + zstd: `magic · len · mtime · n · флаг · zstd(p)`; уровень — `ALPHA_APPROACH_BIN_LEVEL`.
pub(super) fn wrap(
    magic: &[u8; 4],
    (len, mtime): (u64, u64),
    n: usize,
    flag: bool,
    p: &[u8],
) -> Option<Vec<u8>> {
    let level = std::env::var("ALPHA_APPROACH_BIN_LEVEL")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(DEFAULT_LEVEL);
    let z = zstd::encode_all(p, level).ok()?;
    let mut out = Vec::with_capacity(HEAD + z.len());
    out.extend_from_slice(magic);
    out.extend_from_slice(&len.to_le_bytes());
    out.extend_from_slice(&mtime.to_le_bytes());
    out.extend_from_slice(&u32::try_from(n).ok()?.to_le_bytes());
    out.push(u8::from(flag));
    out.extend_from_slice(&z);
    Some(out)
}

/// Обратное к `wrap`: `(n, флаг, распакованная нагрузка)`; чужой magic/штамп — `None`.
pub(super) fn unwrap(
    b: &[u8],
    magic: &[u8; 4],
    (len, mtime): (u64, u64),
) -> Option<(usize, bool, Vec<u8>)> {
    if b.len() < HEAD || &b[..4] != magic {
        return None;
    }
    if u64::from_le_bytes(b[4..12].try_into().ok()?) != len
        || u64::from_le_bytes(b[12..20].try_into().ok()?) != mtime
    {
        return None;
    }
    let n = u32::from_le_bytes(b[20..24].try_into().ok()?) as usize;
    Some((n, b[24] == 1, zstd::decode_all(&b[HEAD..]).ok()?))
}

/// Курсор чтения колонок; `n` — число строк файла.
pub(super) struct Rd<'a> {
    pub(super) raw: &'a [u8],
    pub(super) pos: usize,
    pub(super) n: usize,
}

impl<'a> Rd<'a> {
    pub(super) fn new(raw: &'a [u8], n: usize) -> Self {
        Rd { raw, pos: 0, n }
    }

    pub(super) fn get(&mut self) -> Option<i64> {
        let mut z = 0u64;
        let mut shift = 0;
        loop {
            let b = *self.raw.get(self.pos)?;
            self.pos += 1;
            z |= u64::from(b & 0x7f).checked_shl(shift)?;
            if b < 0x80 {
                break;
            }
            shift += 7;
        }
        Some(((z >> 1) as i64) ^ -((z & 1) as i64))
    }

    pub(super) fn day(&mut self) -> Option<String> {
        let dl = usize::try_from(self.get()?).ok()?;
        let day = std::str::from_utf8(self.raw.get(self.pos..self.pos + dl)?).ok()?;
        self.pos += dl;
        Some(day.to_string())
    }

    pub(super) fn bytes(&mut self) -> Option<Vec<u8>> {
        let v = self.raw.get(self.pos..self.pos + self.n)?.to_vec();
        self.pos += self.n;
        Some(v)
    }

    pub(super) fn plain(&mut self) -> Option<Vec<i64>> {
        (0..self.n).map(|_| self.get()).collect()
    }

    pub(super) fn delta(&mut self) -> Option<Vec<i64>> {
        let mut prev = 0i64;
        (0..self.n)
            .map(|_| {
                prev = prev.wrapping_add(self.get()?);
                Some(prev)
            })
            .collect()
    }

    /// Колонка `Option<i64>`: флаги-байты, затем значения только у `1`.
    pub(super) fn opt(&mut self) -> Option<Vec<Option<i64>>> {
        let flags = self.bytes()?;
        flags
            .into_iter()
            .map(|f| {
                if f == 1 {
                    self.get().map(Some)
                } else {
                    Some(None)
                }
            })
            .collect()
    }

    /// Нагрузка прочитана ровно до конца.
    pub(super) fn done(&self) -> bool {
        self.pos == self.raw.len()
    }
}
