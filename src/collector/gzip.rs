//! Разжатие кадров gzip (BingX: данные, ответы на подписку и `Ping` приходят двоичными gzip-кадрами, подготовка BingX п. 6.1).
//!
//! Состояние инфлятора и буфер живут на сессию и переиспользуются: кадр ≈ 100–300 байт, поэтому состояние на кадр (≈ 40 КБ у `GzDecoder`)
//! стоило бы дороже самого разжатия. Заголовок gzip разбирается здесь (RFC 1952), тело — «сырой» deflate, хвост (CRC32 и длина) проверяется:
//! кадр, прошедший разжатие, восстановлен точно, иначе — `None` (адаптер и писатель получают кадр как пришёл, ядро пишет событие).

use flate2::{Crc, Decompress, FlushDecompress, Status};

/// Потолок разжатого кадра: защита от «бомбы»; кадры бирж — килобайты.
const MAX_OUT: usize = 16 << 20;
const MIN_BUF: usize = 4096;

pub struct Gunzip {
    d: Decompress,
    buf: Vec<u8>,
}

impl Default for Gunzip {
    fn default() -> Self {
        Self::new()
    }
}

/// Длина заголовка gzip (с необязательными полями); `None` — не gzip или зарезервированные флаги.
fn header_len(f: &[u8]) -> Option<usize> {
    if f.get(..3)? != [0x1f, 0x8b, 8] {
        return None;
    }
    let flg = *f.get(3)?;
    if flg & 0xe0 != 0 {
        return None;
    }
    let mut i = 10usize;
    if flg & 0x04 != 0 {
        let xlen = u16::from_le_bytes([*f.get(i)?, *f.get(i.checked_add(1)?)?]);
        i = i.checked_add(2 + usize::from(xlen))?;
    }
    for bit in [0x08u8, 0x10] {
        if flg & bit != 0 {
            i = i.checked_add(f.get(i..)?.iter().position(|&c| c == 0)? + 1)?;
        }
    }
    if flg & 0x02 != 0 {
        i = i.checked_add(2)?;
    }
    Some(i)
}

impl Gunzip {
    pub fn new() -> Self {
        Self {
            d: Decompress::new(false),
            buf: Vec::with_capacity(MIN_BUF),
        }
    }

    /// Разжатый кадр (срез буфера до следующего вызова); `None` — не gzip, обрыв, хвост или длина/CRC не сошлись.
    pub fn unpack(&mut self, frame: &[u8]) -> Option<&[u8]> {
        let start = header_len(frame)?;
        let end = frame.len().checked_sub(8)?;
        let body = frame.get(start..end)?;
        let trailer = frame.get(end..)?;
        let crc = u32::from_le_bytes(trailer.get(..4)?.try_into().ok()?);
        let size = usize::try_from(u32::from_le_bytes(trailer.get(4..8)?.try_into().ok()?)).ok()?;
        if size > MAX_OUT {
            return None;
        }
        self.d.reset(false);
        self.buf.clear();
        // длина известна из хвоста: место сразу на весь кадр (с запасом на конец потока) — обычно один вызов
        self.buf.reserve(size + 64);
        loop {
            let used = usize::try_from(self.d.total_in()).ok()?;
            let out0 = self.buf.len();
            // `Finish` у miniz_oxide требует места под весь результат уже в первом вызове, поэтому поток разжимается как есть (`None`)
            let status = self
                .d
                .decompress_vec(body.get(used..)?, &mut self.buf, FlushDecompress::None)
                .ok()?;
            if status == Status::StreamEnd {
                break;
            }
            if self.buf.len() > size {
                return None; // длина в хвосте не сходится с потоком
            }
            if self.buf.len() == self.buf.capacity() {
                self.buf.reserve(self.buf.capacity().max(MIN_BUF));
            } else if usize::try_from(self.d.total_in()).ok()? == used && self.buf.len() == out0 {
                return None; // место в буфере есть, а хода нет: вход оборван
            }
        }
        if usize::try_from(self.d.total_in()).ok()? != body.len() {
            return None; // за концом потока deflate — лишние байты
        }
        let mut c = Crc::new();
        c.update(&self.buf);
        (c.sum() == crc && self.buf.len() == size).then_some(self.buf.as_slice())
    }
}
