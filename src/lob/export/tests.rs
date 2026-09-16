use super::*;
use hftbacktest::types::{
    LOCAL_ASK_DEPTH_EVENT, LOCAL_BID_DEPTH_SNAPSHOT_EVENT, LOCAL_BUY_TRADE_EVENT,
};

use crate::binlog::{Header, Writer};

/// Контракт `Contracts touched`: `Event` внешний, `repr(C, align(64))`,
/// восемь полей ровно на 64 байта, последнее `fval`. Смена версии крейта
/// ловится здесь, а не в бэктесте.
#[test]
fn event_layout_matches_crate_contract() {
    use hftbacktest::backtest::data::NpyDTyped;
    use std::mem::{align_of, size_of};

    assert_eq!(size_of::<Event>(), 64, "восемь полей по 8 байт");
    assert_eq!(align_of::<Event>(), 64, "выравнивание крейта");

    let descr = Event::descr();
    assert_eq!(descr.len(), 8, "полей обязано быть восемь");
    let names: Vec<&str> = descr.iter().map(|f| f.name.as_str()).collect();
    assert_eq!(
        names,
        ["ev", "exch_ts", "local_ts", "px", "qty", "order_id", "ival", "fval"],
        "имена, типы и порядок — контракт чтения крейта"
    );
    assert_eq!(descr[7].name, "fval", "последнее поле — fval");
    for (field, suffix) in descr
        .iter()
        .zip(["u8", "i8", "i8", "f8", "f8", "u8", "i8", "f8"])
    {
        assert!(
            field.ty.ends_with(suffix),
            "поле {} сменило тип: {}",
            field.name,
            field.ty
        );
    }

    let ev = Event {
        ev: 1,
        exch_ts: 2,
        local_ts: 3,
        px: 4.0,
        qty: 5.0,
        order_id: 6,
        ival: 7,
        fval: 8.0,
    };
    let base = std::ptr::addr_of!(ev) as usize;
    let offsets = [
        (std::ptr::addr_of!(ev.ev) as usize - base),
        (std::ptr::addr_of!(ev.exch_ts) as usize - base),
        (std::ptr::addr_of!(ev.local_ts) as usize - base),
        (std::ptr::addr_of!(ev.px) as usize - base),
        (std::ptr::addr_of!(ev.qty) as usize - base),
        (std::ptr::addr_of!(ev.order_id) as usize - base),
        (std::ptr::addr_of!(ev.ival) as usize - base),
        (std::ptr::addr_of!(ev.fval) as usize - base),
    ];
    assert_eq!(
        offsets,
        [0, 8, 16, 24, 32, 40, 48, 56],
        "порядок полей в памяти обязан совпадать с порядком заголовка npy"
    );
}

/// Ценатики и лоты возвращаются в масштаб крейта через шаги заголовка;
/// метки и блочность — один в один (`ival` крейта из `block` записи).
#[test]
fn record_to_event_restores_crate_scale() {
    let r = Record {
        ev: LOCAL_BUY_TRADE_EVENT,
        exch_ts_ns: 1_000_000_000,
        local_ts_ns: 1_000_000_500,
        price_ticks: 150,
        qty_lots: 5,
        block: true,
        rpi: false,
    };
    // Шаг цены и шаг размера по целому: 150 тиков по 1.0 и 5 лотов по 1.0.
    let ev = record_to_event(&r, 1_000_000_000, 1_000_000_000);
    assert_eq!(ev.ev, LOCAL_BUY_TRADE_EVENT);
    assert_eq!(ev.exch_ts, 1_000_000_000);
    assert_eq!(ev.local_ts, 1_000_000_500);
    assert_eq!(ev.px, 150.0);
    assert_eq!(ev.qty, 5.0);
    assert_eq!(ev.order_id, 0);
    assert_eq!(ev.ival, 1);
    assert_eq!(ev.fval, 0.0);
}

/// Дробный масштаб без потери смысла: тик 1e-5, цена 65.4321.
#[test]
fn record_to_event_keeps_fractional_scale() {
    let r = Record {
        ev: LOCAL_ASK_DEPTH_EVENT,
        exch_ts_ns: 10,
        local_ts_ns: 20,
        price_ticks: 6_543_210,
        qty_lots: 250,
        block: false,
        rpi: false,
    };
    let ev = record_to_event(&r, 10_000, 1_000_000);
    assert!((ev.px - 65.4321).abs() < 1e-9, "px = {}", ev.px);
    assert_eq!(ev.qty, 0.25, "250 лотов по 0.001");
}

/// Строгое `<`: равенство меток — уже дефект, не граница нормы.
#[test]
fn strict_inequality_marks_equal_stamps_defective() {
    let mk = |exch_ts: i64, local_ts: i64| Event {
        ev: LOCAL_ASK_DEPTH_EVENT,
        exch_ts,
        local_ts,
        px: 1.0,
        qty: 1.0,
        order_id: 0,
        ival: 0,
        fval: 0.0,
    };
    assert!(!is_defective(&mk(10, 11)));
    assert!(is_defective(&mk(11, 11)), "равенство — дефект");
    assert!(is_defective(&mk(12, 11)), "инверсия — дефект");
}

fn write_binlog(path: &Path, tick_e9: i64, step_e9: i64, frames: &[Vec<Record>]) {
    let file = File::create(path).unwrap();
    let mut w = Writer::create(
        file,
        Header {
            tick_e9,
            step_e9,
            max_records_per_frame: 10_000,
        },
        1,
    )
    .unwrap();
    for f in frames {
        w.write_frame(f).unwrap();
    }
    w.flush().unwrap();
}

fn sample_record(ev: u64, exch_ts_ns: i64, local_ts_ns: i64) -> Record {
    Record {
        ev,
        exch_ts_ns,
        local_ts_ns,
        price_ticks: 100,
        qty_lots: 2,
        block: false,
        rpi: false,
    }
}

/// Разбор написанного `npy` без читателя крейта. Его `read_npy_file`
/// на этом хосте непригоден: он зовёт `sync_all` на хэндле, открытом
/// только для чтения, что Win32 отвергает `PermissionDenied` (код 5,
/// проверено отдельной пробой вне репозитория). Писатель `write_npy`
/// тем же дефектом не страдает, поэтому круг замыкается так: пишем
/// крейтом, сверяем побайтово с ожидаемым заголовком и полями — ровно
/// запасной вариант, который тикет разрешает буквально.
fn read_npy_parts(path: &Path) -> (String, Vec<u8>) {
    let bytes = std::fs::read(path).unwrap();
    assert!(bytes.len() >= 10, "файл короче префикса npy");
    assert_eq!(&bytes[0..6], b"\x93NUMPY", "магия npy");
    assert_eq!(&bytes[6..8], b"\x01\x00", "крейт пишет версию 1.0");
    let hlen = u16::from_le_bytes(bytes[8..10].try_into().unwrap()) as usize;
    assert_eq!(
        (10 + hlen) % 64,
        0,
        "смещение данных выровнено на кэш-линию, как требует читатель"
    );
    let header = String::from_utf8(bytes[10..10 + hlen].to_vec()).unwrap();
    let payload = bytes[10 + hlen..].to_vec();
    (header, payload)
}

/// Заголовок несёт дескриптор всех восьми полей в порядке контракта
/// и форму `(n, )`. Порядок dtype — little-endian хоста сборки.
fn assert_header_shape_and_fields(header: &str, n: usize) {
    for tuple in [
        "('ev', '<u8')",
        "('exch_ts', '<i8')",
        "('local_ts', '<i8')",
        "('px', '<f8')",
        "('qty', '<f8')",
        "('order_id', '<u8')",
        "('ival', '<i8')",
        "('fval', '<f8')",
    ] {
        assert!(
            header.contains(tuple),
            "заголовок без поля {tuple}: {header}"
        );
    }
    let mut pos = 0;
    for name in [
        "ev", "exch_ts", "local_ts", "px", "qty", "order_id", "ival", "fval",
    ] {
        let next = header[pos..]
            .find(&format!("('{name}',"))
            .unwrap_or_else(|| panic!("поле {name} потеряно: {header}"));
        pos += next + 1;
    }
    assert!(
        header.contains("'fortran_order': False"),
        "порядок C, не фортрановский: {header}"
    );
    assert!(
        header.contains(&format!("'shape': ({n}, )")),
        "форма обязана быть ({n}, ): {header}"
    );
}

/// Байты одного `Event` как их положил `write_npy`: плоское
/// представление структуры ровно на 64 байта.
fn event_bytes(ev: &Event) -> Vec<u8> {
    assert_eq!(std::mem::size_of::<Event>(), 64);
    unsafe { std::slice::from_raw_parts(ev as *const Event as *const u8, 64).to_vec() }
}

/// Независимая расшифровка чанка: флаг, обе метки и цена — без
/// предположений о выравнивании буфера в памяти.
fn decoded_head(chunk: &[u8]) -> (u64, i64, i64, f64) {
    assert_eq!(chunk.len(), 64);
    let ev = u64::from_le_bytes(chunk[0..8].try_into().unwrap());
    let exch = i64::from_le_bytes(chunk[8..16].try_into().unwrap());
    let local = i64::from_le_bytes(chunk[16..24].try_into().unwrap());
    let px = f64::from_le_bytes(chunk[24..32].try_into().unwrap());
    (ev, exch, local, px)
}

/// Done-condition буквально, насколько позволяет хост: вышедший `npy`
/// несёт заголовок крейта на восемь полей и тело из тех же событий
/// побитово, а `exch_ts < local_ts` держится на 100% записей тела.
#[test]
fn exported_npy_matches_header_and_payload_byte_for_byte() {
    let dir = tempfile::tempdir().unwrap();
    let symbol = "TEST";
    let snap = sample_record(LOCAL_BID_DEPTH_SNAPSHOT_EVENT, 1_000, 1_500);
    let delta = sample_record(LOCAL_ASK_DEPTH_EVENT, 2_000, 2_100);
    let trade = Record {
        ev: LOCAL_BUY_TRADE_EVENT,
        exch_ts_ns: 3_000,
        local_ts_ns: 3_050,
        price_ticks: 101,
        qty_lots: 3,
        block: true,
        rpi: false,
    };
    write_binlog(
        &dir.path().join(format!("{symbol}-2026-01-01.binlog")),
        1_000_000_000,
        1_000_000_000,
        &[vec![snap], vec![delta, trade]],
    );
    let out = dir.path().join("day.npy");
    let summary = run_export(&ExportArgs {
        root: dir.path().to_path_buf(),
        symbol: symbol.to_string(),
        out: out.clone(),
    })
    .unwrap();

    assert_eq!(summary.files, 1);
    assert_eq!(summary.events, 3);
    assert_eq!(summary.defective, 0);
    assert_eq!(summary.out, out);

    let (header, payload) = read_npy_parts(&out);
    assert_header_shape_and_fields(&header, 3);
    assert_eq!(payload.len(), 3 * 64, "тело — три события без хвостов");

    let expected = [
        record_to_event(&snap, 1_000_000_000, 1_000_000_000),
        record_to_event(&delta, 1_000_000_000, 1_000_000_000),
        record_to_event(&trade, 1_000_000_000, 1_000_000_000),
    ];
    for (i, want) in expected.iter().enumerate() {
        let chunk = &payload[i * 64..(i + 1) * 64];
        assert_eq!(
            chunk,
            event_bytes(want).as_slice(),
            "событие {i} легло побитово тем же"
        );
        let (ev, exch, local, px) = decoded_head(chunk);
        assert!(exch < local, "событие {i}: инвариант на 100% записей");
        assert_eq!(ev, want.ev);
        assert_eq!(px, want.px);
    }
    assert_eq!(decoded_head(&payload[0..64]).3, 100.0);
    assert_eq!(decoded_head(&payload[128..192]).0, LOCAL_BUY_TRADE_EVENT);
    assert_eq!(decoded_head(&payload[128..192]).3, 101.0);
    assert_eq!(
        i64::from_le_bytes(payload[128 + 48..128 + 56].try_into().unwrap()),
        1,
        "флаг блочной сделки едет сквозняком"
    );
}

/// Круг через читателя самого крейта — там, где хост это позволяет.
/// Игнорируется на Windows: `read_npy_file` зовёт `sync_all` на
/// read-only хэндле (см. `read_npy_parts`), и это ограничение крейта,
/// а не написанного файла. На Linux обязан быть зелёным.
#[ignore]
#[test]
fn crate_reader_round_trip_where_platform_allows() {
    let dir = tempfile::tempdir().unwrap();
    let symbol = "TEST";
    write_binlog(
        &dir.path().join(format!("{symbol}-2026-01-04.binlog")),
        1_000_000_000,
        1_000_000_000,
        &[vec![sample_record(LOCAL_ASK_DEPTH_EVENT, 1_000, 1_100)]],
    );
    let out = dir.path().join("day.npy");
    run_export(&ExportArgs {
        root: dir.path().to_path_buf(),
        symbol: symbol.to_string(),
        out: out.clone(),
    })
    .unwrap();

    let data = hftbacktest::backtest::data::read_npy_file::<Event>(out.to_str().unwrap()).unwrap();
    assert_eq!(data.len(), 1);
    assert!(data[0].exch_ts < data[0].local_ts);
    assert_eq!(data[0].px, 100.0);
}

/// Дефект считается отдельно и не молчит: два события с нарушенным
/// инвариантом отбракованы счётчиком, выход при этом чист на 100%.
#[test]
fn export_counts_and_skips_defective_keeping_output_clean() {
    let dir = tempfile::tempdir().unwrap();
    let symbol = "TEST";
    write_binlog(
        &dir.path().join(format!("{symbol}-2026-01-02.binlog")),
        1_000_000_000,
        1_000_000_000,
        &[vec![
            sample_record(LOCAL_ASK_DEPTH_EVENT, 1_000, 1_100),
            sample_record(LOCAL_ASK_DEPTH_EVENT, 2_000, 2_000),
            sample_record(LOCAL_ASK_DEPTH_EVENT, 3_000, 2_900),
        ]],
    );
    let out = dir.path().join("day.npy");
    let summary = run_export(&ExportArgs {
        root: dir.path().to_path_buf(),
        symbol: symbol.to_string(),
        out: out.clone(),
    })
    .unwrap();

    assert_eq!(summary.events, 1);
    assert_eq!(
        summary.defective, 2,
        "равенство и инверсия меток — два дефекта, не ноль и не тишина"
    );

    let (header, payload) = read_npy_parts(&out);
    assert_header_shape_and_fields(&header, 1);
    assert_eq!(payload.len(), 64);
    let (_, exch, local, px) = decoded_head(&payload);
    assert!(exch < local, "выход чист на 100%");
    assert_eq!(px, 100.0, "уцелело именно годное событие");
}

/// Части суток после смены шагов (`-p2`) сливаются одним вызовом, масштаб
/// каждой части — из её собственного заголовка.
#[test]
fn export_merges_parts_with_per_file_scale() {
    let dir = tempfile::tempdir().unwrap();
    let symbol = "TEST";
    write_binlog(
        &dir.path().join(format!("{symbol}-2026-01-03.binlog")),
        1_000_000_000,
        1_000_000_000,
        &[vec![sample_record(LOCAL_ASK_DEPTH_EVENT, 1_000, 1_100)]],
    );
    write_binlog(
        &dir.path().join(format!("{symbol}-2026-01-03-p2.binlog")),
        10_000_000,
        1_000_000_000,
        &[vec![sample_record(LOCAL_ASK_DEPTH_EVENT, 2_000, 2_100)]],
    );
    let out = dir.path().join("day.npy");
    let summary = run_export(&ExportArgs {
        root: dir.path().to_path_buf(),
        symbol: symbol.to_string(),
        out: out.clone(),
    })
    .unwrap();

    assert_eq!(summary.files, 2);
    assert_eq!(summary.events, 2);
    let (header, payload) = read_npy_parts(&out);
    assert_header_shape_and_fields(&header, 2);
    assert_eq!(payload.len(), 2 * 64);
    assert_eq!(
        decoded_head(&payload[0..64]).3,
        100.0,
        "первая часть: тик 1.0"
    );
    assert_eq!(
        decoded_head(&payload[64..128]).3,
        1.0,
        "вторая часть: тик 0.01 — свой масштаб"
    );
}

/// Часть 1 суток идёт раньше `-p2`: голая лексикография ставила хвост
/// суток раньше начала (`-` < `.`), ломая время в выходе.
#[test]
fn part_files_sort_day_then_part() {
    let prefix = "TEST-";
    let mut names = vec![
        "TEST-2026-01-03-p2.binlog",
        "TEST-2026-01-04.binlog",
        "TEST-2026-01-03.binlog",
    ];
    names.sort_by_key(|n| part_order_key(prefix, n));
    assert_eq!(
        names,
        vec![
            "TEST-2026-01-03.binlog",
            "TEST-2026-01-03-p2.binlog",
            "TEST-2026-01-04.binlog",
        ]
    );
}

/// Пустой корень — явная ошибка, а не молчаливый пустой `npy`.
#[test]
fn export_fails_without_source_files() {
    let dir = tempfile::tempdir().unwrap();
    let err = run_export(&ExportArgs {
        root: dir.path().to_path_buf(),
        symbol: "NOBODY".to_string(),
        out: dir.path().join("day.npy"),
    })
    .unwrap_err();
    assert!(
        err.to_string().contains("NOBODY"),
        "ошибка называет символ: {err}"
    );
}

/// Граница ядра как у разметки: чистому преобразованию нечего делать
/// в транспорте и часах. Список уже списка уровней: вещественное число
/// здесь разрешено масштабом крейта, поэтому `f64` вне запрета, а
/// coupling к модулю площадки ловится парой шаблонов с `::` — голое имя
/// каталога в значении флага по умолчанию под запрет не подпадает.
#[test]
fn module_stays_detached_from_transport_and_clocks() {
    const SRC: &str = include_str!("../export.rs");
    let banned = [
        concat!("by", "bit::"),
        concat!("crate::by", "bit"),
        concat!("tok", "io"),
        concat!("Inst", "ant"),
        concat!("System", "Time"),
        concat!("std::", "time"),
    ];
    for b in banned {
        assert!(!SRC.contains(b), "исходник тянет запрещённое: {b}");
    }
}
