//! Тесты контейнера архива (T46) и чтения его тем же `Reader`, что читает
//! обычный суточный файл: round-trip побайтово, отказы, эквивалентность
//! записей.

use super::*;
use crate::binlog::{is_binlog_file_name, Header, Record, Writer};
use hftbacktest::types::{
    LOCAL_ASK_DEPTH_EVENT, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_EVENT,
    LOCAL_BID_DEPTH_SNAPSHOT_EVENT, LOCAL_BUY_TRADE_EVENT,
};
use std::path::{Path, PathBuf};

const TICK_E9: i64 = 1_000_000;
const STEP_E9: i64 = 1_000_000;

fn header() -> Header {
    Header {
        tick_e9: TICK_E9,
        step_e9: STEP_E9,
        max_records_per_frame: 1_000_000,
    }
}

fn rec(ev: u64, exch_ts_ns: i64, price_ticks: i64, qty_lots: i64) -> Record {
    Record {
        ev,
        exch_ts_ns,
        local_ts_ns: exch_ts_ns,
        price_ticks,
        qty_lots,
        block: false,
        rpi: false,
    }
}

/// Суточный файл v3 из готовых кадров — тем же `Writer`, что пишет запись:
/// фикстура обязана быть ровно тем форматом, который архивируется.
fn plain_file(frames: &[Vec<Record>]) -> Vec<u8> {
    let mut w = Writer::create(Vec::new(), header(), 0).unwrap();
    for f in frames {
        w.write_frame(f).unwrap();
    }
    w.into_inner()
}

/// Кадры фикстуры: снапшот (открывает сутки), дельта, сделка.
fn sample_frames() -> Vec<Vec<Record>> {
    vec![
        vec![
            rec(LOCAL_BID_DEPTH_SNAPSHOT_EVENT, 0, 100, 5),
            rec(LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, 0, 110, 7),
        ],
        vec![
            rec(LOCAL_BID_DEPTH_EVENT, 1_000_000, 100, 6),
            rec(LOCAL_ASK_DEPTH_EVENT, 1_000_000, 110, 8),
        ],
        vec![rec(LOCAL_BUY_TRADE_EVENT, 2_000_000, 100, 1)],
    ]
}

fn archive(plain: &[u8], level: i32) -> Vec<u8> {
    let mut src = Reader::open(plain).unwrap();
    let mut out = Vec::new();
    write_container(&mut src, level, &mut out).unwrap();
    out
}

/// Основной критерий T46: контейнер читается тем же `Reader`, кадры совпадают
/// **побайтово**, записи — покадрово, заголовок — тот же. Проверяется на
/// фикстуре из трёх кадров и на каждом уровне, который назван в замере.
#[test]
fn archive_round_trips_frames_and_records_byte_for_byte() {
    let plain = plain_file(&sample_frames());
    for level in [1, 9, DEFAULT_LEVEL, max_level()] {
        let packed = archive(&plain, level);
        let mut src = Reader::open(&plain[..]).unwrap();
        let mut dst = Reader::open(&packed[..]).unwrap();
        assert!(
            dst.archive_level().is_some(),
            "уровень {level}: контейнер обязан опознаваться читателем"
        );
        assert_eq!(dst.archive_level(), Some(level as u8));
        assert_eq!(dst.version(), crate::binlog::VERSION);
        let report = verify_round_trip(&mut src, &mut dst).unwrap();
        assert_eq!(report.frames, 3);
        assert_eq!(report.records, 5);
    }
}

/// Контейнер — это zstd-поток, и он **меньше** исходника: на синтетике
/// выигрыш невелик (кадры фикстуры и так крошечные), но направление то же,
/// и главное — что контейнер не «то же самое плюс шапка».
#[test]
fn archive_shrinks_the_plain_file_on_repetitive_data() {
    // Много одинаковых кадров: сжимается всем, кроме уже сжатых тел.
    let frame = vec![
        rec(LOCAL_BID_DEPTH_EVENT, 1_000_000, 100, 5),
        rec(LOCAL_ASK_DEPTH_EVENT, 1_000_000, 110, 7),
    ];
    let frames: Vec<Vec<Record>> = std::iter::once(vec![
        rec(LOCAL_BID_DEPTH_SNAPSHOT_EVENT, 0, 100, 5),
        rec(LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, 0, 110, 7),
    ])
    .chain(std::iter::repeat_n(frame, 500))
    .collect();
    let plain = plain_file(&frames);
    let packed = archive(&plain, DEFAULT_LEVEL);
    assert!(
        packed.len() < plain.len(),
        "контейнер обязан быть меньше файла: {} против {}",
        packed.len(),
        plain.len()
    );
}

/// Заголовок контейнера — тот же заголовок суток: шаги цены/размера и потолок
/// кадра переживают архивацию, иначе все читатели молча считали бы цену в
/// другом масштабе.
#[test]
fn archive_keeps_the_day_header() {
    let plain = plain_file(&sample_frames());
    let packed = archive(&plain, 3);
    let src = Reader::open(&plain[..]).unwrap();
    let dst = Reader::open(&packed[..]).unwrap();
    assert_eq!(src.header(), dst.header());
    assert_eq!(dst.header(), header());
}

/// Однокадровый файл: минимальные сутки (синтетический снапшот и больше
/// ничего) архивируются и читаются так же.
#[test]
fn single_frame_file_round_trips() {
    let one = plain_file(&[vec![rec(LOCAL_BID_DEPTH_SNAPSHOT_EVENT, 0, 100, 5)]]);
    let packed = archive(&one, DEFAULT_LEVEL);
    let mut src = Reader::open(&one[..]).unwrap();
    let mut dst = Reader::open(&packed[..]).unwrap();
    let report = verify_round_trip(&mut src, &mut dst).unwrap();
    assert_eq!((report.frames, report.records), (1, 1));
    // И то же самое, но через обычное чтение кадров: архив обязан читаться
    // ровно как файл.
    let mut reader = Reader::open(&packed[..]).unwrap();
    let frame = reader.read_frame().unwrap().unwrap();
    assert_eq!(frame.len(), 1);
    assert_eq!(reader.read_frame().unwrap(), None);
}

/// Файл из одного заголовка (ноль кадров) — не сутки: и поток, и архив
/// обязаны отказать одинаково (`MissingSnapshot`), а не записать пустой
/// контейнер, который потом нечем отличить от «архив без данных».
#[test]
fn header_only_source_is_refused_as_a_day_without_snapshot() {
    let empty = plain_file(&[]);
    // Читатель открывает такой файл (заголовок целый) и спотыкается на
    // первом же кадре — ровно как на живом файле без снапшота.
    let mut plain_reader = Reader::open(&empty[..]).unwrap();
    assert_eq!(
        plain_reader.read_body().unwrap_err(),
        BinlogError::MissingSnapshot
    );
    let mut reader = Reader::open(&empty[..]).unwrap();
    let mut out = Vec::new();
    let err = write_container(&mut reader, DEFAULT_LEVEL, &mut out).unwrap_err();
    assert_eq!(err, BinlogError::MissingSnapshot);
    assert!(out.is_empty(), "недописанный контейнер не остаётся никому");
}

/// Пустой файл (ноль байт) — не наш формат вовсе: отказ до всякой архивации,
/// и это `TruncatedHeader` с нулём прочитанных байт, как у обычного читателя.
#[test]
fn empty_file_is_refused_as_truncated_header() {
    let err = Reader::open(&b""[..]).unwrap_err();
    assert_eq!(err, BinlogError::TruncatedHeader { got: 0, want: 25 });
}

/// Файл версии 2 не архивируется: тело v2 устроено иначе, и «архив» из него
/// читался бы v3-декодером как мусор. Отказ называет версию источника.
#[test]
fn version_two_file_is_refused_not_misarchived() {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&crate::binlog::MAGIC);
    bytes.push(crate::binlog::VERSION_V2);
    bytes.extend_from_slice(&TICK_E9.to_le_bytes());
    bytes.extend_from_slice(&STEP_E9.to_le_bytes());
    bytes.extend_from_slice(&1_000u32.to_le_bytes());
    let mut src = Reader::open(&bytes[..]).unwrap();
    assert_eq!(src.version(), crate::binlog::VERSION_V2);
    let mut out = Vec::new();
    let err = write_container(&mut src, DEFAULT_LEVEL, &mut out).unwrap_err();
    assert_eq!(
        err,
        BinlogError::UnsupportedVersion {
            got: crate::binlog::VERSION_V2
        }
    );
    assert!(out.is_empty());
}

/// Уровень вне `1..=MAX_LEVEL` отвергается до создания энкодера: иначе
/// «не поддержано» сказал бы zstd, а не наш CLI.
#[test]
fn out_of_range_level_is_refused_before_any_output() {
    let plain = plain_file(&sample_frames());
    for level in [0, -1, max_level() + 1] {
        let mut src = Reader::open(&plain[..]).unwrap();
        let mut out = Vec::new();
        assert!(
            write_container(&mut src, level, &mut out).is_err(),
            "уровень {level} обязан быть отвергнут"
        );
        assert!(
            out.is_empty(),
            "уровень {level}: мусор в приёмник не пишется"
        );
    }
}

/// Испорченный контейнер: байты валидного zstd-потока, но внутри не наш
/// маркер — читатель обязан сказать «это не контейнер», а не молча отдать
/// пустые сутки.
#[test]
fn zstd_stream_without_our_marker_is_refused() {
    let junk = zstd::stream::encode_all(&b"not a binlog archive at all"[..], 1).unwrap();
    let err = Reader::open(&junk[..]).unwrap_err();
    assert!(
        matches!(err, BinlogError::Corrupt(ref m) if m.contains("ABLA")),
        "ожидалась ошибка про магию контейнера, получено {err:?}"
    );
}

/// Шапка контейнера с чужой версией — тоже отказ, а не «прочитаем как
/// получится»: раскладка контейнера версионируется так же, как формат.
#[test]
fn container_with_unknown_version_is_refused() {
    let plain = plain_file(&sample_frames());
    let packed = archive(&plain, 3);
    let inner = zstd::stream::decode_all(&packed[..]).unwrap();
    assert_eq!(&inner[..4], &ARCHIVE_MAGIC);
    let mut bad = inner.clone();
    bad[4] = ARCHIVE_VERSION + 1;
    let junk = zstd::stream::encode_all(&bad[..], 1).unwrap();
    let err = Reader::open(&junk[..]).unwrap_err();
    assert!(
        matches!(err, BinlogError::Corrupt(ref m) if m.contains("версия контейнера")),
        "ожидалась ошибка про версию контейнера, получено {err:?}"
    );
}

/// Урезанный контейнер: поток обрывается посреди шапки или посреди кадра —
/// это `TruncatedHeader`/`ShortRead`, а не паника и не «пустые сутки».
#[test]
fn truncated_container_reports_short_read_not_panic() {
    let plain = plain_file(&sample_frames());
    let packed = archive(&plain, 1);
    let inner = zstd::stream::decode_all(&packed[..]).unwrap();
    // Обрезаем распакованное на середине последнего кадра и сжимаем снова:
    // сам zstd-поток при этом целый, «усечение» — внутри формата.
    let cut = inner.len() - 3;
    let junk = zstd::stream::encode_all(&inner[..cut], 1).unwrap();
    let mut reader = Reader::open(&junk[..]).unwrap();
    let mut frames = 0u64;
    let err = loop {
        match reader.read_frame() {
            Ok(Some(_)) => frames += 1,
            Ok(None) => panic!("усечённый контейнер не имеет права кончиться чисто"),
            Err(e) => break e,
        }
    };
    assert_eq!(frames, 2, "два целых кадра до обрыва");
    assert!(
        matches!(err, BinlogError::ShortRead { .. }),
        "обрыв внутри кадра — ShortRead, получено {err:?}"
    );
}

/// Проход `read_stats` по контейнеру: те же кадры и записи, что в исходнике,
/// и размер контейнера — размер файла на диске.
#[test]
fn read_stats_sees_the_same_frames_and_records_as_the_source() {
    let plain = plain_file(&sample_frames());
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("X-2026-09-15.binlog.zst");
    std::fs::write(&path, archive(&plain, DEFAULT_LEVEL)).unwrap();
    let stats = read_stats(&path).unwrap();
    assert_eq!(stats.frames, 3);
    assert_eq!(stats.records, 5);
    assert_eq!(stats.level, DEFAULT_LEVEL);
    assert_eq!(stats.bytes, file_bytes(&path).unwrap());
    assert_eq!(stats.bytes, std::fs::metadata(&path).unwrap().len());
}

/// Имя контейнера по умолчанию дописывает суффикс, а не заменяет расширение:
/// иначе в каталоге не осталось бы ни одного файла с именем суток, и
/// резолверы (`session_binlog_for`) перестали бы их находить.
#[test]
fn default_out_path_appends_the_archive_suffix() {
    assert_eq!(
        default_out_path(Path::new("data/SOLUSDT-2026-09-15.binlog")),
        PathBuf::from("data/SOLUSDT-2026-09-15.binlog.zst")
    );
    assert!(is_archive_path(Path::new(
        "data/SOLUSDT-2026-09-15.binlog.zst"
    )));
    assert!(!is_archive_path(Path::new(
        "data/SOLUSDT-2026-09-15.binlog"
    )));
}

/// Имена суточных файлов: обычное и архивное имя разбираются одинаково, и
/// суффикс архива отрезается раньше обычного (иначе `-p2.binlog.zst` дал бы
/// часть 1 с `.zst` в хвосте имени суток).
#[test]
fn file_name_helpers_know_both_suffixes() {
    assert_eq!(
        strip_binlog_suffix("SYM-2026-09-15.binlog"),
        Some("SYM-2026-09-15")
    );
    assert_eq!(
        strip_binlog_suffix("SYM-2026-09-15.binlog.zst"),
        Some("SYM-2026-09-15")
    );
    assert_eq!(strip_binlog_suffix("SYM-2026-09-15.csv"), None);
    assert!(is_binlog_file_name("SYM-2026-09-15-p2.binlog"));
    assert!(is_binlog_file_name("SYM-2026-09-15-p2.binlog.zst"));
    assert!(!is_binlog_file_name("SYM-2026-09-15.zst"));
    assert!(!is_binlog_file_name("SYM-2026-09-15.binlog.gz"));
}
