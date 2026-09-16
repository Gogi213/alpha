//! Черновой замер: сколько даёт архив закрытых суток.
//!
//! Живой путь сжимает каждый кадр отдельно на уровне 1 — там процессор в дефиците.
//! У закрытых суток ограничение другое: процессор можно тратить. Поэтому мерим не
//! «сжать файл ещё раз» (внутри уже zstd-кадры, повторно не жмётся вообще), а
//! **распакованные тела кадров, сжатые заново одним потоком**: именно так выглядит
//! архив, из которого потом восстанавливается v3.
//!
//! Сравнение — с размером исходного файла на диске (то, что архив должен заменить).
//!
//! Запуск: `cargo run --release --example archive_probe -- <файл.binlog>`

#![allow(clippy::cast_precision_loss)]

use std::env;
use std::fs::File;

use alpha::binlog::{encode_frame_payload_v3, Reader};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = env::args()
        .nth(1)
        .expect("usage: archive_probe <file.binlog> [plain-dump]");
    let dump = env::args().nth(2);
    let orig = std::fs::metadata(&path)?.len() as usize;

    let mut reader = Reader::open(File::open(&path)?)?;
    let mut plain: Vec<u8> = Vec::new();
    let mut frames = 0usize;
    let mut records = 0usize;
    while let Some(frame) = reader.read_frame()? {
        if frame.is_empty() {
            continue;
        }
        frames += 1;
        records += frame.len();
        let mut buf = Vec::new();
        encode_frame_payload_v3(&frame, &mut buf);
        plain.extend_from_slice(&(buf.len() as u32).to_le_bytes());
        plain.extend_from_slice(&buf);
    }
    println!(
        "файл: {path} — {:.2} МБ на диске, {frames} кадров, {records} записей",
        orig as f64 / 1e6
    );
    println!(
        "распакованные тела кадров: {:.1} МБ ({:.1}× от файла) — это то, что сейчас даёт zstd-1 по кадрам",
        plain.len() as f64 / 1e6,
        plain.len() as f64 / orig as f64
    );
    if let Some(out) = dump {
        std::fs::write(&out, &plain)?;
        println!(
            "распакованный поток выгружен в {out} ({:.1} МБ)",
            plain.len() as f64 / 1e6
        );
    }

    for level in [1, 3, 9, 19] {
        let t = std::time::Instant::now();
        let packed = zstd::bulk::compress(&plain, level)?;
        let secs = t.elapsed().as_secs_f64();
        println!(
            "архив одним потоком, уровень {level:>2}: {:.2} МБ — {:.1} % от файла ({:.2}× меньше), {secs:.1} с",
            packed.len() as f64 / 1e6,
            100.0 * packed.len() as f64 / orig as f64,
            orig as f64 / packed.len() as f64
        );
        if level == 19 {
            let back = zstd::bulk::decompress(&packed, plain.len())?;
            println!(
                "   round-trip: {}",
                if back == plain {
                    "восстановлено байт-в-байт"
                } else {
                    "РАСХОЖДЕНИЕ"
                }
            );
        }
    }

    let mut comp = zstd::bulk::Compressor::new(19)?;
    if comp
        .set_parameter(zstd::zstd_safe::CParameter::WindowLog(27))
        .is_ok()
    {
        let t = std::time::Instant::now();
        let packed = comp.compress(&plain)?;
        println!(
            "архив одним потоком, 19 + окно 128 МБ: {:.2} МБ — {:.1} % от файла ({:.2}× меньше), {:.1} с",
            packed.len() as f64 / 1e6,
            100.0 * packed.len() as f64 / orig as f64,
            orig as f64 / packed.len() as f64,
            t.elapsed().as_secs_f64()
        );
    }
    // Вариант без изменения читателей: файл остаётся валидным v3, но кадры
    // пересжаты сильнее. Сравниваем с тем, что лежит на диске сейчас.
    for level in [9, 19] {
        let mut per = zstd::bulk::Compressor::new(level)?;
        let mut total = 0usize;
        let t = std::time::Instant::now();
        let mut reader = Reader::open(File::open(&path)?)?;
        while let Some(frame) = reader.read_frame()? {
            if frame.is_empty() {
                continue;
            }
            let mut buf = Vec::new();
            encode_frame_payload_v3(&frame, &mut buf);
            total += per.compress(&buf)?.len() + 4;
        }
        println!(
            "по кадрам, уровень {level:>2} (файл остаётся v3): {:.2} МБ — {:.1} % от файла, {:.1} с",
            total as f64 / 1e6,
            100.0 * total as f64 / orig as f64,
            t.elapsed().as_secs_f64()
        );
    }
    Ok(())
}
