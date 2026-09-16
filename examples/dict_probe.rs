//! Черновой офлайн-замер: даёт ли словарь zstd меньше байт на кадр, чем чистое zstd-1.
//!
//! Только чтение. Схема замера:
//!
//! * кадры настоящего бинлога кодируются штатным v3 и берутся как записи для сжатия;
//! * **базовая линия** — `zstd-1` без словаря по тестовым кадрам;
//! * словарь обучается `zstd::dict::from_samples` на обучающих кадрах (размеры 8, 32, 112 КБ)
//!   и применяется к тестовым;
//! * если обучающий и тестовый файлы — один и тот же, обучение идёт по первой половине кадров,
//!   а замер по второй (иначе словарь «видел» бы то, на чём его проверяют, и завысил экономию);
//! * размер словаря печатается отдельно: в бою словарь отгружается один раз, а не на кадр.
//!
//! Запуск: `cargo run --release --example dict_probe -- <обучающий.binlog> <тестовый.binlog>`

#![allow(clippy::indexing_slicing, clippy::cast_precision_loss)]

use std::env;
use std::fs::File;

use alpha::binlog::{encode_frame_payload_v3, Reader, Record};

const LEVEL: i32 = 1;
const DICT_SIZES: [usize; 3] = [8 * 1024, 32 * 1024, 112 * 1024];

fn payloads(path: &str) -> Result<Vec<Vec<u8>>, Box<dyn std::error::Error>> {
    let mut reader = Reader::open(File::open(path)?)?;
    let mut out = Vec::new();
    let mut buf = Vec::with_capacity(64 * 1024);
    while let Some(frame) = reader.read_frame()? {
        if frame.is_empty() {
            continue;
        }
        buf.clear();
        encode_frame_payload_v3(&frame, &mut buf);
        out.push(buf.clone());
    }
    Ok(out)
}

fn plain_bytes(frames: &[Vec<u8>]) -> usize {
    frames
        .iter()
        .map(|f| zstd::bulk::compress(f, LEVEL).map_or(0, |c| c.len()))
        .sum()
}

fn dict_bytes(compressor: &mut zstd::bulk::Compressor<'static>, frames: &[Vec<u8>]) -> usize {
    frames
        .iter()
        .map(|f| compressor.compress(f).map_or(0, |c| c.len()))
        .sum()
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().skip(1).collect();
    let (train_path, test_path) = (
        args.first()
            .expect("usage: dict_probe <train.binlog> <test.binlog>"),
        args.get(1)
            .expect("usage: dict_probe <train.binlog> <test.binlog>"),
    );
    let same_file = train_path == test_path;

    let all_train = payloads(train_path)?;
    let (train, test) = if same_file {
        let half = all_train.len() / 2;
        (all_train[..half].to_vec(), all_train[half..].to_vec())
    } else {
        (all_train, payloads(test_path)?)
    };

    let test_records: usize = if same_file {
        count_records(test_path, test.len(), true)?
    } else {
        count_records(test_path, test.len(), false)?
    };

    let base = plain_bytes(&test);
    let per = |b: usize| b as f64 / test_records.max(1) as f64;
    println!(
        "train={train_path} ({} кадров)  test={test_path} ({} кадров, {} записей)",
        train.len(),
        test.len(),
        test_records
    );
    println!(
        "базовая линия zstd-1 без словаря : {:.3} Б/запись ({:.2} МБ)",
        per(base),
        base as f64 / 1e6
    );

    for size in DICT_SIZES {
        let dict = zstd::dict::from_samples(&train, size)?;
        let mut comp = zstd::bulk::Compressor::with_dictionary(LEVEL, &dict)?;
        let bytes = dict_bytes(&mut comp, &test);
        println!(
            "словарь {:>3} КБ (обучен на {} КБ) : {:.3} Б/запись, {:.1} % от базы (+{:.0} КБ на словарь)",
            size / 1024,
            train.iter().map(Vec::len).sum::<usize>() / 1024,
            per(bytes),
            100.0 * bytes as f64 / base as f64,
            dict.len() as f64 / 1024.0
        );
    }

    // Верхняя граница: словарь обучен на самих тестовых кадрах (в бою так нельзя,
    // показывает потолок метода).
    let dict = zstd::dict::from_samples(&test, 112 * 1024)?;
    let mut comp = zstd::bulk::Compressor::with_dictionary(LEVEL, &dict)?;
    let bytes = dict_bytes(&mut comp, &test);
    println!(
        "потолок (словарь из тестовых кадров): {:.3} Б/запись, {:.1} % от базы",
        per(bytes),
        100.0 * bytes as f64 / base as f64
    );
    Ok(())
}

/// Число записей в тестовом наборе: полный проход по файлу с отбрасыванием первой
/// половины кадров, когда обучение и замер — один и тот же файл.
fn count_records(
    path: &str,
    _frames: usize,
    skip_first_half: bool,
) -> Result<usize, Box<dyn std::error::Error>> {
    let mut reader = Reader::open(File::open(path)?)?;
    let mut frames: Vec<Vec<Record>> = Vec::new();
    while let Some(frame) = reader.read_frame()? {
        if !frame.is_empty() {
            frames.push(frame);
        }
    }
    let half = frames.len() / 2;
    let slice = if skip_first_half {
        &frames[half..]
    } else {
        &frames[..]
    };
    Ok(slice.iter().map(Vec::len).sum())
}
