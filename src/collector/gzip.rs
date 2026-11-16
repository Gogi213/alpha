//! Разжатие кадров gzip (BingX: данные, ответы на подписку и `Ping` приходят двоичными gzip-кадрами, подготовка BingX п. 6.1).
//!
//! libdeflate (`libdeflater`): разжатие одним вызовом в буфер известного размера — длина берётся из хвоста gzip (ISIZE), заголовок RFC 1952 (имя, комментарий,
//! дополнительное поле, CRC заголовка), CRC32 и длина проверяются самой библиотекой. Буфер живёт на сессию, состояние libdeflate — на поток. Замер collector-perf
//! (13.11, повтор 16 захватов): `miniz_oxide` — ≈ 8,6 мкс на кадр BingX (`init_tree` 34 % времени rx-потока в бою), libdeflate — см. отчёт.
//! Кадр, не прошедший проверку (не gzip, обрыв, CRC, длина, лишние байты), — `None`: ядро пишет событие, адаптер и писатель получают кадр как пришёл.

use std::cell::RefCell;
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};

use libdeflater::Decompressor;

use super::inflate::Tiny;

// Состояние libdeflate (таблицы декодирования, ≈ 10 КБ) — одно на поток, а не на соединение: кадры BingX крошечные (медиана 149 Б сжатого → 184 Б, 70 % с динамическим деревом,
// своим у каждого кадра — 499 разных деревьев на 4 360 кадров), так что на кадр таблицы строятся заново, и главная цена — холодные кэши: 30 соединений × свои таблицы не помещаются
// в L2, а одно общее состояние остаётся горячим (в бою 23 мкс на кадр против 2,5 мкс на повторе).
thread_local! {
    static DEC: RefCell<Decompressor> = RefCell::new(Decompressor::new());
    // Быстрый путь мелких кадров (`inflate.rs`): таблицы в сотни байт, одно состояние на поток, по той же причине.
    static TINY: RefCell<Box<Tiny>> = RefCell::new(Tiny::new());
}

/// Кадры, разжатые быстрым путём мелких кадров, и кадры, которые он не взял (ушли в libdeflate): счётчики процесса для health (`gzip`).
static FAST: AtomicU64 = AtomicU64::new(0);
static FALLBACK: AtomicU64 = AtomicU64::new(0);

/// Раздел health `gzip`: сколько кадров разжал быстрый путь и сколько ушло в libdeflate (необычный заголовок, длинный код, кадр больше потолка быстрого пути, битый кадр).
pub fn stats_json() -> serde_json::Value {
    serde_json::json!({"fast": FAST.load(Relaxed), "fallback": FALLBACK.load(Relaxed)})
}

/// Потолок разжатого кадра: защита от «бомбы»; кадры бирж — килобайты.
const MAX_OUT: usize = 16 << 20;

pub struct Gunzip {
    buf: Vec<u8>,
    /// Сначала быстрый путь мелких кадров (`inflate.rs`), libdeflate — для всего, что он не взял.
    fast: bool,
}

impl Default for Gunzip {
    fn default() -> Self {
        Self::new()
    }
}

impl Gunzip {
    pub fn new() -> Self {
        Self {
            buf: Vec::with_capacity(4096),
            fast: true,
        }
    }

    /// Только libdeflate, без быстрого пути: сверка выходов и стенд.
    pub fn libdeflate_only() -> Self {
        Self {
            fast: false,
            ..Self::new()
        }
    }

    /// Разжатый кадр (срез буфера до следующего вызова); `None` — не gzip, обрыв, хвост или длина/CRC не сошлись.
    pub fn unpack(&mut self, frame: &[u8]) -> Option<&[u8]> {
        if self.fast {
            let buf = &mut self.buf;
            if let Some(n) = TINY.with(|t| t.borrow_mut().unpack(frame, buf)) {
                FAST.fetch_add(1, Relaxed);
                return self.buf.get(..n);
            }
            FALLBACK.fetch_add(1, Relaxed);
        }
        let end = frame.len().checked_sub(8)?;
        let size =
            usize::try_from(u32::from_le_bytes(frame.get(end + 4..)?.try_into().ok()?)).ok()?;
        if size > MAX_OUT {
            return None;
        }
        self.buf.clear();
        self.buf.resize(size, 0);
        let buf = &mut self.buf;
        let n = DEC
            .with(|d| d.borrow_mut().gzip_decompress(frame, buf))
            .ok()?;
        (n == size).then(|| self.buf.get(..n)).flatten()
    }
}
