//! Разжатие кадров gzip (BingX: данные, ответы на подписку и `Ping` приходят двоичными gzip-кадрами, подготовка BingX п. 6.1).
//!
//! libdeflate (`libdeflater`): разжатие одним вызовом в буфер известного размера — длина берётся из хвоста gzip (ISIZE), заголовок RFC 1952 (имя, комментарий,
//! дополнительное поле, CRC заголовка), CRC32 и длина проверяются самой библиотекой. Состояние и буфер живут на сессию и переиспользуются. Замер collector-perf
//! (13.11, повтор 16 захватов): `miniz_oxide` — ≈ 8,6 мкс на кадр BingX (`init_tree` 34 % времени rx-потока в бою), libdeflate — см. отчёт.
//! Кадр, не прошедший проверку (не gzip, обрыв, CRC, длина, лишние байты), — `None`: ядро пишет событие, адаптер и писатель получают кадр как пришёл.

use libdeflater::Decompressor;

/// Потолок разжатого кадра: защита от «бомбы»; кадры бирж — килобайты.
const MAX_OUT: usize = 16 << 20;

pub struct Gunzip {
    d: Decompressor,
    buf: Vec<u8>,
}

impl Default for Gunzip {
    fn default() -> Self {
        Self::new()
    }
}

impl Gunzip {
    pub fn new() -> Self {
        Self {
            d: Decompressor::new(),
            buf: Vec::with_capacity(4096),
        }
    }

    /// Разжатый кадр (срез буфера до следующего вызова); `None` — не gzip, обрыв, хвост или длина/CRC не сошлись.
    pub fn unpack(&mut self, frame: &[u8]) -> Option<&[u8]> {
        let end = frame.len().checked_sub(8)?;
        let size =
            usize::try_from(u32::from_le_bytes(frame.get(end + 4..)?.try_into().ok()?)).ok()?;
        if size > MAX_OUT {
            return None;
        }
        self.buf.clear();
        self.buf.resize(size, 0);
        let n = self.d.gzip_decompress(frame, &mut self.buf).ok()?;
        (n == size).then(|| self.buf.get(..n)).flatten()
    }
}
