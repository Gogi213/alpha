//! Тесты `lob binlog-stats`: счётчики считаются по факту содержимого файла,
//! а замер `--reencode` — по перекодировке тех же записей.

use super::*;
use crate::binlog::{Header, Writer};

fn record(ev: u64, block: bool, local_eq_exch: bool) -> Record {
    Record {
        ev,
        exch_ts_ns: 1_000,
        local_ts_ns: if local_eq_exch { 1_000 } else { 1_500 },
        price_ticks: 100,
        qty_lots: 1,
        block,
        rpi: false,
    }
}

fn args(path: &Path) -> BinlogStatsArgs {
    BinlogStatsArgs {
        path: path.to_path_buf(),
        reencode: false,
        rewrite_out: None,
    }
}

fn write_v3(path: &Path, header: Header, frames: &[Vec<Record>]) {
    let file = std::fs::File::create(path).unwrap();
    let mut writer = Writer::create(file, header, 3).unwrap();
    for frame in frames {
        writer.write_frame(frame).unwrap();
    }
    writer.flush().unwrap();
}

/// Файл версии 2 — та форма, что лежит у живого коллектора. Собирается вручную:
/// `Writer` пишет только текущую версию, а замер A/B обязан считаться по v2.
fn write_v2(path: &Path, header: Header, frames: &[Vec<Record>]) {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&crate::binlog::MAGIC);
    bytes.push(VERSION_V2);
    bytes.extend_from_slice(&header.tick_e9.to_le_bytes());
    bytes.extend_from_slice(&header.step_e9.to_le_bytes());
    bytes.extend_from_slice(&header.max_records_per_frame.to_le_bytes());
    for frame in frames {
        let mut raw = Vec::new();
        encode_frame_payload_v2(frame, &mut raw);
        let compressed = zstd::bulk::compress(&raw, ZSTD_LEVEL).unwrap();
        let len = u32::try_from(compressed.len()).unwrap();
        bytes.extend_from_slice(&len.to_le_bytes());
        bytes.extend_from_slice(&compressed);
    }
    std::fs::write(path, bytes).unwrap();
}

#[test]
fn stats_count_frames_records_groups_and_live_fields() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("X-2026-09-13.binlog");
    let header = Header {
        tick_e9: 10_000_000,
        step_e9: 100_000_000,
        max_records_per_frame: 16,
    };
    write_v3(
        &path,
        header,
        &[
            vec![
                // Одно сообщение — две записи с общими флагами и метками.
                record(0x1, false, true),
                record(0x1, false, true),
                // Второе сообщение: своя метка приёма.
                record(0x1, false, false),
                // Третье: блочная сделка.
                record(0x2, true, true),
            ],
            vec![record(0x2, false, true)],
        ],
    );

    let stats = run_binlog_stats(&args(&path)).unwrap();
    assert_eq!(stats.version, crate::binlog::VERSION);
    assert_eq!(stats.records, 5);
    assert_eq!(stats.frames, 2);
    assert_eq!(
        stats.groups, 4,
        "три сообщения в первом кадре и одно во втором"
    );
    assert_eq!(stats.min_records_in_frame, 1);
    assert_eq!(stats.max_records_in_frame, 4);
    assert_eq!(stats.by_ev, vec![(0x1, 3), (0x2, 2)]);
    assert_eq!(stats.block_trades, 1);
    assert_eq!(
        stats.local_ts_differs, 1,
        "одна запись с собственной меткой"
    );
    assert_eq!(stats.legacy_dead, LegacyDeadFields::default());
    assert!(stats.bytes_on_disk > 0);

    let lines = summary_lines(&path, &stats);
    assert!(lines[0].contains("записей 5"), "{}", lines[0]);
    assert!(lines[0].contains("формат v3"), "{}", lines[0]);
    assert!(
        lines[1].contains("групп (сообщений биржи) 4"),
        "{}",
        lines[1]
    );
    assert!(lines[2].contains("мёртвых полей"), "{}", lines[2]);
}

#[test]
fn stats_read_legacy_v2_file_and_count_dead_fields() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("Y-2026-09-13.binlog");
    let header = Header {
        tick_e9: 10_000_000,
        step_e9: 100_000_000,
        max_records_per_frame: 16,
    };
    write_v2(
        &path,
        header,
        &[vec![record(0x1, false, true), record(0x2, true, false)]],
    );

    let stats = run_binlog_stats(&args(&path)).unwrap();
    assert_eq!(stats.version, VERSION_V2);
    assert_eq!(stats.records, 2);
    assert_eq!(stats.block_trades, 1, "`ival != 0` — это `block`");
    // Живой v2-кодек пишет в `order_id`/`fval` нули (так их и несла живая
    // запись), а `ival` — из блочности: именно эти три числа и доказывают,
    // что формат v3 ничего не теряет, выбрасывая поля.
    assert_eq!(stats.legacy_dead.nonzero_order_id, 0);
    assert_eq!(stats.legacy_dead.nonzero_fval, 0);
    assert_eq!(
        stats.legacy_dead.nonzero_ival, 1,
        "единственная блочная сделка — единственный ненулевой `ival`"
    );
    let lines = summary_lines(&path, &stats);
    assert!(lines[0].contains("legacy"), "{}", lines[0]);
    assert!(lines[2].contains("order_id 0"), "{}", lines[2]);
}

#[test]
fn reencode_measures_v2_against_v3_on_the_same_records() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("Z-2026-09-13.binlog");
    let header = Header {
        tick_e9: 10_000_000,
        step_e9: 100_000_000,
        max_records_per_frame: 16,
    };
    let frames = vec![
        vec![
            record(0x1, false, true),
            record(0x1, false, true),
            record(0x1, false, false),
        ],
        vec![record(0x2, true, false)],
    ];
    write_v2(&path, header, &frames);

    let report = measure_reencode(&args(&path)).unwrap();
    assert_eq!(report.records, 4);
    assert_eq!(report.frames, 2);
    assert_eq!(
        report.v2_frames_matching_disk, 2,
        "перекодировка v2 обязана совпасть с диском — иначе замер сравнивает не то"
    );
    assert_eq!(report.round_trip_mismatches, 0, "v2 → v3 → v2 без потерь");
    assert!(report.v3_bytes > 0 && report.v2_bytes > 0);
    assert_eq!(
        report.v3_per_message_frames, 3,
        "три сообщения в двух кадрах"
    );
    assert_eq!(
        report.ev_table_mismatches, 0,
        "вариант «ev таблицей» обязан читаться обратно без расхождений"
    );
    assert!(report.v3_ev_table_bytes > 0 && report.v3_level3_bytes > 0);
    // Уровни 3 и 6 сжимают **то же** тело, что уровень 1, поэтому их размеры
    // обязаны быть рядом с `v3_bytes`. Регрессия, которую это ловит: буфер тела
    // кадра затирался телом последнего сообщения (M1b) и M1z мерил огрызок —
    // втрое-впятеро меньше настоящего.
    assert!(
        report.v3_level3_bytes * 2 >= report.v3_bytes && report.v3_level3_bytes <= report.v3_bytes,
        "уровень 3 обязан сжимать то же тело, что уровень 1: {} против {}",
        report.v3_level3_bytes,
        report.v3_bytes
    );

    let lines = report_lines(&path, &report);
    assert!(lines.iter().any(|l| l.contains("M1:")), "{lines:?}");
    assert!(lines.iter().any(|l| l.contains("M1e:")), "{lines:?}");
    assert!(lines.iter().any(|l| l.contains("M1z:")), "{lines:?}");
    assert!(
        lines.iter().any(|l| l.contains("расхождений 0")),
        "{lines:?}"
    );
}

/// Ворота M5c тикета 44: переписанный в v3 файл читается как v3 и несёт **те
/// же** записи и те же кадры; отказ на файле, который уже v3.
#[test]
fn rewrite_writes_a_v3_file_with_the_same_records() {
    let dir = tempfile::tempdir().unwrap();
    let src = dir.path().join("V-2026-09-13.binlog");
    let header = Header {
        tick_e9: 10_000_000,
        step_e9: 100_000_000,
        max_records_per_frame: 16,
    };
    let frames = vec![
        vec![
            record(0x1, false, true),
            record(0x1, false, true),
            record(0x1, false, false),
        ],
        vec![record(0x2, true, false)],
    ];
    write_v2(&src, header, &frames);

    let out = dir.path().join("v3/V-2026-09-13.binlog");
    let lines = rewrite_v2_to_v3(&src, &out).unwrap();
    assert!(lines[0].contains("v2 → v3"), "{lines:?}");

    let bytes = std::fs::read(&out).unwrap();
    let mut reader = Reader::open(&bytes[..]).unwrap();
    assert_eq!(reader.version(), crate::binlog::VERSION);
    assert_eq!(reader.header(), header, "шаги и потолок кадра те же");
    let mut got = Vec::new();
    while let Some(frame) = reader.read_frame().unwrap() {
        got.push(frame);
    }
    assert_eq!(got, frames, "границы кадров и записи те же");

    let err = rewrite_v2_to_v3(&out, &dir.path().join("again.binlog")).unwrap_err();
    assert!(err.to_string().contains("версии 2"), "{err}");
}

#[test]
fn reencode_refuses_a_current_format_file() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("W-2026-09-13.binlog");
    let header = Header {
        tick_e9: 10_000_000,
        step_e9: 100_000_000,
        max_records_per_frame: 16,
    };
    write_v3(&path, header, &[vec![record(0x1, false, true)]]);
    let err = measure_reencode(&args(&path)).unwrap_err();
    assert!(
        err.to_string().contains("версии 2"),
        "замер обязан отказаться от v3-файла, а не сравнивать формат с собой: {err}"
    );
}
