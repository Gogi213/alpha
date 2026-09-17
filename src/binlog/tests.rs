/// Заголовок — такие же данные с диска, как и поле длины кадра, и потолок,
/// выведенный только из него, испорченным заголовком обходится. Проверяется
/// то, что второй потолок существует и связывает: `u32::MAX` записей дали бы
/// тридцать с лишним гигабайт, а обязаны упереться в границу из Decision 23.
#[test]
fn a_corrupt_header_cannot_raise_the_ceiling_past_the_day_budget() {
    let honest = super::max_frame_payload_bytes(1_000);
    assert_eq!(
        honest,
        1_000 * super::MIN_RECORD_LEN + super::FRAME_EPOCH_LEN,
        "на честном значении потолок обязан считаться ровно по записям"
    );

    let corrupt = super::max_frame_payload_bytes(u32::MAX);
    assert_eq!(
        corrupt,
        super::HARD_PAYLOAD_CEILING,
        "испорченный заголовок обязан упираться в границу, а не в своё произведение"
    );
    assert!(
        (u32::MAX as usize) * super::MIN_RECORD_LEN > corrupt,
        "иначе тест не проверяет ничего: произведение обязано быть больше границы"
    );
}

use super::*;
use crate::alloc_count;

const TICK_E9: i64 = 100_000; // 0.0001, как в тестах book.rs
const STEP_E9: i64 = 1_000_000; // 0.001

/// Потолок заголовка по умолчанию для тестов, которым сам потолок не
/// важен — round trip, границы суток, усечение и т.п. Не значение,
/// которое использует рекордер (то назначает вызывающий при создании
/// файла, не этот модуль), а запас с большим отступом над самым
/// крупным кадром, который где-либо в этом наборе тестов пишется
/// одним вызовом `write_frame` через общий `header()` — крупнейший тут
/// `PER_FRAME = 20_000` в `round_trips_one_million_events` и в тестах
/// аллокаций ниже. Тесты про сам потолок (`..._exceeds_the_header_ceiling`,
/// `..._at_exactly_the_maximum`, `writer_refuses_frame_exceeding_...`)
/// собирают свой `Header` с маленьким явным значением, а не берут этот.
const DEFAULT_TEST_MAX_RECORDS_PER_FRAME: u32 = 1_000_000;

fn header() -> Header {
    Header {
        tick_e9: TICK_E9,
        step_e9: STEP_E9,
        max_records_per_frame: DEFAULT_TEST_MAX_RECORDS_PER_FRAME,
    }
}

/// Флаг снапшота бид-уровня, взятый у крейта, а не выдуманный: доказывает
/// «поля — надмножество `Event`» на конкретном значении, которое реально
/// использует экспортёр (шаг 6.1), а не произвольное число теста.
fn ev_snapshot_bid() -> u64 {
    hftbacktest::types::LOCAL_BID_DEPTH_SNAPSHOT_EVENT
}
fn ev_delta_ask() -> u64 {
    hftbacktest::types::LOCAL_ASK_DEPTH_EVENT
}
fn ev_trade_buy() -> u64 {
    hftbacktest::types::LOCAL_BUY_TRADE_EVENT
}

fn rec(ev: u64, exch_ts_ns: i64, local_ts_ns: i64, price_ticks: i64, qty_lots: i64) -> Record {
    Record {
        ev,
        exch_ts_ns,
        local_ts_ns,
        price_ticks,
        qty_lots,
        block: false,
        rpi: false,
    }
}

fn write_all(hdr: Header, frames: &[Vec<Record>]) -> Vec<u8> {
    let mut w = Writer::create(Vec::new(), hdr, zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
    for frame in frames {
        w.write_frame(frame).unwrap();
    }
    w.into_inner()
}

fn read_all(bytes: &[u8]) -> (Header, Vec<Vec<Record>>) {
    let mut r = Reader::open(bytes).unwrap();
    let hdr = r.header();
    let mut frames = Vec::new();
    while let Some(f) = r.read_frame().unwrap() {
        frames.push(f);
    }
    (hdr, frames)
}

// -----------------------------------------------------------------
// Базовый round trip.
// -----------------------------------------------------------------

#[test]
fn header_round_trips() {
    let bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 10, 20, 1, 1)]]);
    let mut r = Reader::open(&bytes[..]).unwrap();
    assert_eq!(r.header(), header());
    assert!(r.read_frame().unwrap().is_some());
    assert!(r.read_frame().unwrap().is_none());
}

#[test]
fn records_round_trip_across_several_frames() {
    let frame0 = vec![
        rec(ev_snapshot_bid(), 1_000, 1_500, 100, 5),
        rec(ev_snapshot_bid(), 1_000, 1_600, 99, 3),
        rec(ev_delta_ask(), 1_000, 1_700, 101, 4),
    ];
    let frame1 = vec![
        rec(ev_delta_ask(), 5_000, 5_100, 105, 0),
        rec(ev_trade_buy(), 5_050, 5_200, 100, 2),
    ];
    let bytes = write_all(header(), &[frame0.clone(), frame1.clone()]);
    let (hdr, frames) = read_all(&bytes);
    assert_eq!(hdr, header());
    assert_eq!(frames, vec![frame0, frame1]);
}

/// Второй кадр не должен зависеть от состояния первого: дельта внутри
/// него считается заново от нуля. Если бы состояние переносилось между
/// кадрами, эта запись расшифровалась бы в другую цену.
#[test]
fn delta_state_resets_at_frame_boundary() {
    let frame0 = vec![rec(ev_snapshot_bid(), 0, 0, 1_000_000, 500)];
    let frame1 = vec![rec(ev_delta_ask(), 1, 1, 7, 2)];
    let bytes = write_all(header(), &[frame0, frame1]);
    let (_, frames) = read_all(&bytes);
    assert_eq!(frames[1][0].price_ticks, 7);
    assert_eq!(frames[1][0].qty_lots, 2);
}

/// Запись без событий — no-op: в потоке не появляется ни одного байта,
/// а не кадр с нулём записей.
#[test]
fn writing_an_empty_slice_writes_nothing() {
    let mut w = Writer::create(Vec::new(), header(), zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
    w.write_frame(&[]).unwrap();
    let bytes = w.into_inner();
    assert_eq!(bytes.len(), HEADER_LEN);
}

// -----------------------------------------------------------------
// Требование 1: round trip на 10^6 событий (done-condition шага 0.2).
// -----------------------------------------------------------------

#[test]
fn round_trips_one_million_events() {
    const TOTAL: usize = 1_000_000;
    const PER_FRAME: usize = 20_000;

    let mut w = Writer::create(Vec::new(), header(), 1).unwrap();
    let mut written = 0usize;
    let mut expected: Vec<Record> = Vec::with_capacity(TOTAL);
    while written < TOTAL {
        let n = PER_FRAME.min(TOTAL - written);
        let mut frame = Vec::with_capacity(n);
        for i in 0..n {
            let idx = (written + i) as i64;
            let ev = if idx % 2 == 0 {
                ev_delta_ask()
            } else {
                ev_trade_buy()
            };
            // Знакопеременный шаг: проверяет, что дельты уверенно берут
            // и рост, и падение цены/размера внутри одного кадра.
            let r = rec(
                ev,
                1_700_000_000_000_000_000 + idx * 1_000,
                1_700_000_000_000_500_000 + idx * 1_000,
                1_000_000 + if idx % 3 == 0 { idx } else { -idx },
                100 + (idx % 97),
            );
            frame.push(r);
            expected.push(r);
        }
        w.write_frame(&frame).unwrap();
        written += n;
    }
    let bytes = w.into_inner();

    let (hdr, frames) = read_all(&bytes);
    assert_eq!(hdr, header());
    let got: Vec<Record> = frames.into_iter().flatten().collect();
    assert_eq!(got.len(), TOTAL);
    assert_eq!(got, expected);
}

// -----------------------------------------------------------------
// Требование архитектуры (A2 через план): один и тот же бинлог,
// прочитанный дважды, даёт побайтово одинаковый вывод. Тест ловит
// недетерминизм, прокрашивающийся в тракт декодирования (порядок обхода
// хеш-таблиц, время, RNG): читает ОДНИ И ТЕ ЖЕ байты двумя независимыми
// Reader и сравнивает покадрово, а не только итогом.
// -----------------------------------------------------------------

#[test]
fn same_bytes_twice_give_byte_identical_frames() {
    let day = write_all(
        header(),
        &[
            vec![
                rec(ev_snapshot_bid(), 0, 1, 10, 10),
                rec(ev_snapshot_bid(), 0, 1, 9, 3),
            ],
            vec![
                rec(ev_delta_ask(), 1_000, 1_001, 11, 9),
                rec(ev_trade_buy(), 1_000, 1_001, 11, 1),
                rec(ev_delta_ask(), 1_000, 1_001, 21, 19),
            ],
        ],
    );
    let (_, first) = read_all(&day);
    let (_, second) = read_all(&day);
    assert_eq!(first.len(), second.len(), "число кадров обязано совпасть");
    for (a, b) in first.iter().zip(second.iter()) {
        assert_eq!(a, b, "кадры обязаны совпасть побайтово-полностью");
    }
}

/// Сид-корпус фаззера (`fuzz/corpus/reader`) гоняется и в стабильном
/// наборе: ни один вход не вправе ронять читатель — ни паникой, ни
/// бесконечностью. Потолок кадров тот же, что в фазз-таргете.
#[test]
fn fuzz_seed_corpus_never_panics_nor_hangs() {
    let mut names: Vec<_> = std::fs::read_dir("fuzz/corpus/reader")
        .expect("сид-корпус обязан существовать")
        .map(|e| e.unwrap().path())
        .collect();
    names.sort();
    assert!(!names.is_empty(), "пустой корпус ничего не проверяет");
    for path in names {
        let data = std::fs::read(&path).unwrap();
        let Ok(mut reader) = Reader::open(&data[..]) else {
            continue;
        };
        let _ = reader.header();
        let mut frames = 0usize;
        while let Ok(Some(_)) = reader.read_frame() {
            frames += 1;
            assert!(frames < 4096, "вход {:?} не заканчивается", path);
        }
    }
}

// -----------------------------------------------------------------
// Требование 2: сутки читаются с первого байта без предыдущих суток.
// -----------------------------------------------------------------

/// Два «дня» с **разными** `tickSize` (биржа сменила шаг между ними,
/// как и предупреждает Decision 7) кодируются независимо. Открытие
/// второго не видит байт первого вообще — они лежат в отдельных `Vec`.
#[test]
fn each_day_file_decodes_standing_alone() {
    let day1_header = Header {
        tick_e9: 100_000,
        step_e9: 1_000_000,
        max_records_per_frame: DEFAULT_TEST_MAX_RECORDS_PER_FRAME,
    };
    let day2_header = Header {
        tick_e9: 50_000, // другой шаг цены — как после смены на бирже
        step_e9: 2_000_000,
        max_records_per_frame: DEFAULT_TEST_MAX_RECORDS_PER_FRAME,
    };
    let day1 = write_all(day1_header, &[vec![rec(ev_snapshot_bid(), 0, 1, 10, 10)]]);
    let day2 = write_all(day2_header, &[vec![rec(ev_snapshot_bid(), 0, 1, 20, 20)]]);

    // День 2 декодируется из собственного среза, день 1 в эту функцию
    // не передаётся вообще — не только логически, а буквально.
    let (hdr2, frames2) = read_all(&day2);
    assert_eq!(hdr2, day2_header);
    assert_eq!(frames2[0][0].price_ticks, 20);

    let (hdr1, frames1) = read_all(&day1);
    assert_eq!(hdr1, day1_header);
    assert_eq!(frames1[0][0].price_ticks, 10);
}

// -----------------------------------------------------------------
// Требование 3: усечённый последний кадр — короткое чтение.
// -----------------------------------------------------------------

#[test]
fn truncated_final_frame_is_a_short_read_not_corruption() {
    let good = vec![rec(ev_snapshot_bid(), 0, 1, 10, 10)];
    let mut w = Writer::create(Vec::new(), header(), zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
    w.write_frame(&good).unwrap();
    w.write_frame(&[rec(ev_delta_ask(), 5, 6, 11, 9)]).unwrap();
    let mut bytes = w.into_inner();

    // Обрезаем файл на середине второго кадра: первый читается штатно,
    // на втором должен быть именно `ShortRead`, не паника и не пустой
    // результат, выданный за нормальный конец файла.
    bytes.truncate(bytes.len() - 3);

    let mut r = Reader::open(&bytes[..]).unwrap();
    assert!(r.read_frame().unwrap().is_some(), "первый кадр цел");
    let err = r.read_frame().unwrap_err();
    assert!(
        matches!(err, BinlogError::ShortRead { .. }),
        "ожидался ShortRead, получено {err:?}"
    );
}

/// A4 (2026-09-17): тот же обрезанный хвост, но **мягким** чтением — это конец
/// прочитанного с флагом `truncated_tail`, а не отказ команды. Живой корень
/// читают, пока коллектор пишет (`COMMANDS.md`), а запись кладёт кадр не одним
/// `write`; строгий `read_frame` при этом обязан остаться строгим — мягкость
/// свойство вызывающего, а не чтения.
#[test]
fn soft_read_stops_at_a_truncated_tail_and_flags_it() {
    let good = vec![rec(ev_snapshot_bid(), 0, 1, 10, 10)];
    let mut w = Writer::create(Vec::new(), header(), zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
    w.write_frame(&good).unwrap();
    w.write_frame(&[rec(ev_delta_ask(), 5, 6, 11, 9)]).unwrap();
    let mut bytes = w.into_inner();
    bytes.truncate(bytes.len() - 3);

    let mut soft = Reader::open(&bytes[..]).unwrap();
    assert!(!soft.truncated_tail(), "до чтения флага нет");
    assert!(
        soft.read_frame_soft().unwrap().is_some(),
        "целый кадр читается"
    );
    assert!(!soft.truncated_tail(), "целый кадр флага не поднимает");
    assert!(
        soft.read_frame_soft().unwrap().is_none(),
        "обрезанный хвост — конец прочитанного, не ошибка"
    );
    assert!(soft.truncated_tail(), "и об этом сказано флагом");

    let mut strict = Reader::open(&bytes[..]).unwrap();
    assert!(strict.read_frame().unwrap().is_some());
    assert!(
        matches!(strict.read_frame(), Err(BinlogError::ShortRead { .. })),
        "`read_frame` остаётся строгим: мягкость выбирает вызывающий"
    );
}

// -----------------------------------------------------------------
// Требование 4: точное сохранение цены/размера на значениях, которые
// f64 не может держать точно.
// -----------------------------------------------------------------

#[test]
fn deltas_survive_values_f64_cannot_hold_exactly() {
    // 2^53 + 1 — первое целое, которое f64 не представляет точно.
    const BEYOND_F64: i64 = 9_007_199_254_740_993;
    let frame = vec![
        rec(ev_snapshot_bid(), 0, 0, i64::MAX, i64::MAX),
        // Соседняя запись на противоположном краю диапазона: обычное
        // `i64::MIN - i64::MAX` переполняет i64 и в debug-сборке
        // запаниковало бы — здесь дельта берётся `wrapping_sub`.
        rec(ev_delta_ask(), 1, 1, i64::MIN, i64::MIN),
        rec(ev_delta_ask(), 2, 2, BEYOND_F64, -BEYOND_F64),
        rec(ev_delta_ask(), 3, 3, 0, 0),
    ];
    let bytes = write_all(header(), std::slice::from_ref(&frame));
    let (_, frames) = read_all(&bytes);
    assert_eq!(frames[0], frame, "цена и размер обязаны совпасть побитово");

    // f64 не смог бы: явная демонстрация того, зачем эта проверка нужна.
    assert_ne!(
        BEYOND_F64 as f64 as i64, BEYOND_F64,
        "иначе тест не проверяет то, что заявлено"
    );
}

/// Тест выше гоняет в края `i64` только `price_ticks`/`qty_lots` —
/// `exch_ts_ns`/`local_ts_ns` там остаются маленькими. У времени своя
/// дельта (от эпохи кадра, а не от предыдущей записи, см. доку модуля),
/// и `local_ts_ns` никак не связан с `exch_ts_ns` (может разъехаться с
/// ним произвольно), так что оба поля отдельно должны пережить край
/// `i64` — именно там, где `exch_ts_ns.wrapping_sub(st.epoch_ns)` /
/// `local_ts_ns.wrapping_sub(st.epoch_ns)` обязаны остаться честной
/// биекцией, а не обычным `-`, который в debug-сборке запаниковал бы.
#[test]
fn timestamp_deltas_survive_epoch_at_i64_extremes() {
    let frame = vec![
        // Эпоха кадра = exch_ts_ns первой записи (`Writer::write_frame`).
        rec(ev_snapshot_bid(), i64::MIN, i64::MAX, 1, 1),
        rec(ev_delta_ask(), i64::MAX, i64::MIN, 2, 2),
        rec(ev_trade_buy(), 0, 0, 3, 3),
    ];
    let bytes = write_all(header(), std::slice::from_ref(&frame));
    let (_, frames) = read_all(&bytes);
    assert_eq!(
        frames[0], frame,
        "exch_ts_ns и local_ts_ns обязаны совпасть побитово даже на краях i64"
    );
}

// -----------------------------------------------------------------
// Требование 4б: кодек обязан правильно трогать не только цену, размер и
// время. В v3 у записи остались `ev`, обе метки, цена, размер и `block`
// (бит `attrs` группы), а группа обязана нести флаги и метки **своего**
// сообщения. `zigzag(0)` и `uvarint(0)` — один и тот же байт 0x00, так что
// перепутанные местами `write_zigzag`/`write_uvarint` на одних нулях дали бы
// зелёный round trip: поэтому здесь есть ненулевые `ev`, обе метки, `block`
// и границы групп внутри одного кадра.
// -----------------------------------------------------------------

/// Та же запись, но блочная — единственное поле v3, которого нет у `rec()`.
fn rec_block(
    ev: u64,
    exch_ts_ns: i64,
    local_ts_ns: i64,
    price_ticks: i64,
    qty_lots: i64,
) -> Record {
    Record {
        block: true,
        ..rec(ev, exch_ts_ns, local_ts_ns, price_ticks, qty_lots)
    }
}

#[test]
fn groups_keep_their_own_flags_stamps_and_block_bit() {
    // Три группы в одном кадре: уровни одного сообщения книги (три записи с
    // общими метками), однозаписная группа-сделка с `block = true`, и уровень
    // другого сообщения — с другой меткой приёма.
    let frame = vec![
        rec(ev_snapshot_bid(), 2_000, 2_050, 100, 5),
        rec(ev_snapshot_bid(), 2_000, 2_050, 99, 3),
        rec(ev_snapshot_bid(), 2_000, 2_050, 98, 1),
        rec_block(ev_trade_buy(), 2_100, 2_150, 101, 7),
        rec(ev_delta_ask(), 2_100, 2_150, 102, 4),
    ];
    let bytes = write_all(header(), std::slice::from_ref(&frame));
    let (_, frames) = read_all(&bytes);
    assert_eq!(
        frames[0], frame,
        "флаги, обе метки, `block` и дельты цены/размера обязаны совпасть"
    );
}

#[test]
fn records_of_one_message_share_the_group_stamps() {
    // Одно сообщение биржи — один `local_ts` на все его уровни: в этом и
    // смысл переноса метки приёма из записи в заголовок группы, и метка
    // обязана восстанавливаться у каждой записи группы, а не только у первой.
    let frame = vec![
        rec(ev_snapshot_bid(), 10, 20, 5, 1),
        rec(ev_snapshot_bid(), 10, 20, 6, 2),
    ];
    let bytes = write_all(header(), std::slice::from_ref(&frame));
    let (_, frames) = read_all(&bytes);
    for r in &frames[0] {
        assert_eq!((r.exch_ts_ns, r.local_ts_ns), (10, 20));
    }
}

/// Собирает тело кадра v3 из готовой группы: эпоха плюс байты группы как есть.
fn v3_payload_of(group: &[u8]) -> Vec<u8> {
    let mut payload = Vec::new();
    payload.extend_from_slice(&0i64.to_le_bytes());
    payload.extend_from_slice(group);
    payload
}

#[test]
fn unknown_attrs_bit_is_corruption_not_a_silent_ignore() {
    // ev=1, exch=0, local=0, attrs=0x04 (бит 2 — неизвестный: бит 0 занят
    // блочностью, бит 1 — RPI), count=1, цена, размер.
    let payload = v3_payload_of(&[1, 0, 0, 0x04, 1, 1, 1]);
    let err = decode_frame_payload_v3(&payload)
        .expect_err("неизвестный бит `attrs` обязан быть ошибкой, а не тишиной");
    assert!(matches!(err, BinlogError::Corrupt(_)), "{err:?}");
}

/// Флаг RPI лежит в `attrs` **группы**, но обязан остаться поштучным: смена
/// флага рвёт группу (иначе две сделки одного сообщения с разными `RPI`
/// слились бы в одну и флаг потерялся бы), а round-trip возвращает значения,
/// записанные на диск.
#[test]
fn rpi_flag_survives_round_trip_and_splits_the_group() {
    let ts = 1_700_000_000_000_000_000;
    let plain = rec(ev_trade_buy(), ts, ts + 1, 100, 5);
    let mut rpi = rec(ev_trade_buy(), ts, ts + 1, 101, 7);
    rpi.rpi = true;

    assert!(
        !same_message(&plain, &rpi),
        "смена RPI обязана начинать новую группу: иначе флаг не поштучный"
    );
    assert!(
        same_message(&plain, &plain),
        "иначе тест не проверяет границу: одинаковые записи — одна группа"
    );

    let bytes = write_all(header(), &[vec![plain, rpi]]);
    let (_, frames) = read_all(&bytes);
    assert_eq!(frames.len(), 1, "один кадр — одна группа на каждую запись");
    assert_eq!(
        frames[0],
        vec![plain, rpi],
        "значения и порядок — как на диске"
    );
    assert!(!frames[0][0].rpi);
    assert!(frames[0][1].rpi);
}

/// Файл, записанный до появления флага (бит 1 `attrs` нулевой), читается как
/// «не размечено», а не как ошибка раскладки: обратная совместимость по
/// чтению — часть контракта, а не побочный эффект.
#[test]
fn file_written_before_the_rpi_bit_reads_as_not_flagged() {
    let ts = 1_700_000_000_000_000_000;
    let plain = rec(ev_trade_buy(), ts, ts + 1, 100, 5);
    let bytes = write_all(header(), &[vec![plain, plain]]);
    let (_, frames) = read_all(&bytes);
    assert_eq!(frames[0].len(), 2);
    assert!(
        frames[0].iter().all(|r| !r.rpi),
        "записи без бита RPI обязаны читаться как `false`"
    );
}

#[test]
fn group_count_larger_than_the_frame_is_corruption_not_a_loop() {
    // `count` = 127 при двух оставшихся байтах: отказ обязан случиться **до**
    // чтения записей, иначе счётчик с диска крутил бы цикл и аллоцировал
    // записи, которых в кадре нет.
    let payload = v3_payload_of(&[1, 0, 0, 0, 0x7f, 0, 0]);
    let err =
        decode_frame_payload_v3(&payload).expect_err("группа длиннее кадра обязана быть ошибкой");
    assert!(matches!(err, BinlogError::Corrupt(_)), "{err:?}");
}

#[test]
fn frame_shorter_than_the_epoch_is_corruption() {
    let err = decode_frame_payload_v3(&[0u8; FRAME_EPOCH_LEN - 1])
        .expect_err("кадр короче эпохи обязан быть ошибкой");
    assert!(matches!(err, BinlogError::Corrupt(_)), "{err:?}");
}

/// Вариант «`ev` таблицей на кадр» (замер M1e тикета 44, в формат не входит):
/// round trip обязан быть побитовым, а значения сверх потолка таблицы — уходить
/// экранированными, а не теряться.
#[test]
fn ev_table_variant_round_trips_and_escapes_beyond_the_table() {
    let mut frame = vec![
        rec(0x5000_0001, 10, 10, 100, 5),
        rec(0x5000_0001, 10, 10, 99, 3),
        rec(0x6000_0001, 11, 11, 101, 2),
        rec_block(0x5000_0002, 12, 12, 102, 1),
    ];
    // Больше `EV_TABLE_MAX` различных значений: хвост обязан уехать
    // экранированным, иначе такой поток молча потерял бы флаги.
    for i in 0..(EV_TABLE_MAX as u64 + 5) {
        frame.push(rec(0x7000_0000 + i, 100 + i as i64, 100 + i as i64, 100, 1));
    }
    let mut raw = Vec::new();
    encode_frame_payload_v3_ev_table(&frame, &mut raw);
    let back = decode_frame_payload_v3_ev_table(&raw)
        .expect("вариант «ev таблицей» обязан читаться обратно");
    assert_eq!(back, frame, "round-trip варианта — побитовый");
}

// -----------------------------------------------------------------
// Требование 5: аллокации на запись не растут с числом событий.
// -----------------------------------------------------------------

/// Бюджет GC (`PLAN.md` шаг 0.2; SETTLED.md B2) — буквально ноль
/// аллокаций на событие после прогрева, не просто «не растёт кратно».
/// Раньше эта проверка допускала рост вплоть до `small * 2 + 4` —
/// свойство, которое зелёный тест давал бы и при паре аллокаций на
/// кадр (так и было: `zstd::stream::copy_encode` заводил новый
/// контекст сжатия на каждый вызов). `Writer` теперь держит
/// переиспользуемый `zstd::bulk::Compressor`, и бюджет проверяется
/// как заявлено в плане.
#[test]
fn write_path_allocates_nothing_after_warmup() {
    const FRAMES: usize = 4;

    fn synth(n: usize, seed: i64) -> Vec<Record> {
        (0..n)
            .map(|i| {
                let idx = seed + i as i64;
                rec(
                    ev_delta_ask(),
                    1_000_000 + idx,
                    1_000_100 + idx,
                    500 + (idx % 13),
                    10 + (idx % 7),
                )
            })
            .collect()
    }

    fn allocations_for(events_per_frame: usize) -> u64 {
        // `io::sink()`, не `Vec::new()`: сток теста не должен участвовать
        // в замере. Растущий `Vec<u8>`, копящий байты всех кадров подряд,
        // сам периодически перевыделяется (амортизированное удвоение
        // ёмкости) — это аллокации стока теста, а не `write_frame`, и
        // с `Vec::new()` они попадали бы в тот же счётчик, маскируя
        // настоящий бюджет под ложным «не совсем ноль».
        let mut w = Writer::create(io::sink(), header(), zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();

        // Прогрев: кадр той же формы вне замера, чтобы `scratch`,
        // `compressed` и `compressor` выросли до итоговой ёмкости
        // заранее — гейт GC требует «ноль после прогрева», а не
        // «ноль с первого кадра» (тот же принцип, что в
        // `alloc_count::tests`).
        w.write_frame(&synth(events_per_frame, 0)).unwrap();

        // Кадры строятся заранее и вне замера, чтобы считать только
        // аллокации самого `write_frame`, а не тестовых данных. Сдвиг
        // `seed` — кратное 91 (= НОК(13, 7), периодов `idx % 13` и
        // `idx % 7` выше): фаза обоих циклов совпадает с прогревом
        // (`seed = 0`), так что каждый кадр кодируется в те же самые
        // байты, что и прогревочный, — иначе кадр со сдвинутой фазой
        // мог бы дать дельту на один байт длиннее и вызвать рост
        // `scratch`/`compressed`, не имеющий отношения к бюджету GC.
        let batches: Vec<Vec<Record>> = (0..FRAMES)
            .map(|k| synth(events_per_frame, (k as i64 + 1) * 91))
            .collect();

        let (_, counts) = alloc_count::measure(|| {
            for batch in &batches {
                w.write_frame(batch).unwrap();
            }
        });
        counts.allocations
    }

    for events_per_frame in [1_000usize, 10_000] {
        let allocations = allocations_for(events_per_frame);
        assert_eq!(
            allocations, 0,
            "путь разбор-и-запись обязан быть нулевым после прогрева \
             ({events_per_frame} записей/кадр, {FRAMES} кадра): было {allocations}"
        );
    }
}

/// То же самое, но на масштабе, который SETTLED.md B2 называет
/// буквально: «измеряется проигрыванием 10^6 событий из заранее
/// заполненного буфера» — не 40 тысяч, как в тесте выше.
///
/// Дельты здесь ограничены по модулю (как в тесте выше), а не растут
/// без края с `idx`, как в `round_trips_one_million_events`: та форма
/// нарочно нужна для проверки корректности на больших скачках, но
/// делает байтовую длину кадра зависимой от того, насколько далеко
/// зашёл прогон, — здесь же кадр под замером обязан кодироваться
/// ровно в те же байты, что и прогревочный, на любом из миллиона
/// событий, иначе замер ловил бы рост данных, а не аллокатор.
#[test]
fn write_path_allocates_nothing_replaying_one_million_events() {
    const TOTAL: usize = 1_000_000;
    const PER_FRAME: usize = 20_000;

    fn synth(n: usize, seed: i64) -> Vec<Record> {
        (0..n)
            .map(|i| {
                let idx = seed + i as i64;
                rec(
                    ev_delta_ask(),
                    1_000_000 + idx,
                    1_000_100 + idx,
                    500 + (idx % 13),
                    10 + (idx % 7),
                )
            })
            .collect()
    }

    // `io::sink()`, не `Vec::new()` — см. комментарий в тесте выше.
    let mut w = Writer::create(io::sink(), header(), 1).unwrap();

    // Прогрев вне замера, как и в тесте выше.
    w.write_frame(&synth(PER_FRAME, 0)).unwrap();

    // Оставшиеся 980 000 событий, кадрами заранее — под замером
    // остаётся только сам `write_frame`. Сдвиг `seed` кратен 91
    // (см. комментарий в тесте выше), чтобы фаза `idx % 13`/`idx % 7`
    // всегда совпадала с прогревом.
    let batches: Vec<Vec<Record>> = (1..TOTAL / PER_FRAME)
        .map(|k| synth(PER_FRAME, (k as i64) * 91))
        .collect();

    let (_, counts) = alloc_count::measure(|| {
        for batch in &batches {
            w.write_frame(batch).unwrap();
        }
    });
    assert_eq!(
        counts.allocations, 0,
        "путь разбор-и-запись обязан быть нулевым после прогрева на \
         {TOTAL} событиях: было {}",
        counts.allocations
    );
}

// -----------------------------------------------------------------
// Требование 6: вырожденный ввод — ошибка, никогда не паника и не
// молчаливый пустой результат.
// -----------------------------------------------------------------

#[test]
fn empty_file_is_an_error() {
    let err = Reader::open(&b""[..]).unwrap_err();
    assert_eq!(
        err,
        BinlogError::TruncatedHeader {
            got: 0,
            want: HEADER_LEN
        }
    );
}

#[test]
fn header_only_file_is_an_error_not_an_empty_result() {
    // Валидный заголовок, ни одного кадра — в сутках нет даже
    // синтетического снапшота, которого требует Decision 7.
    let w = Writer::create(Vec::new(), header(), zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
    let bytes = w.into_inner();
    assert_eq!(bytes.len(), HEADER_LEN);

    let mut r = Reader::open(&bytes[..]).unwrap();
    let err = r.read_frame().unwrap_err();
    assert_eq!(err, BinlogError::MissingSnapshot);
}

#[test]
fn frame_claiming_more_bytes_than_remain_is_an_error() {
    let mut w = Writer::create(Vec::new(), header(), zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
    w.write_frame(&[rec(ev_snapshot_bid(), 0, 1, 10, 10)])
        .unwrap();
    let mut bytes = w.into_inner();

    // Читаем реальную длину первого кадра и завышаем её, оставляя тело
    // прежним: заявленная длина теперь превышает то, что реально есть.
    let len_at = HEADER_LEN;
    let real_len = u32::from_le_bytes(bytes[len_at..len_at + 4].try_into().unwrap());
    let inflated = real_len + 10_000;
    bytes[len_at..len_at + 4].copy_from_slice(&inflated.to_le_bytes());

    let mut r = Reader::open(&bytes[..]).unwrap();
    let err = r.read_frame().unwrap_err();
    assert!(
        matches!(err, BinlogError::ShortRead { .. }),
        "ожидался ShortRead, получено {err:?}"
    );
}

/// Как тест выше, но с длиной, завышенной не на 10 000 байт, а на
/// сотни мегабайт — там, где `vec![0u8; len]` до проверки остатка
/// потока попытался бы выделить эти мегабайты впрок. Неудачная
/// аллокация такого масштаба — это `abort` процесса (не перехватываемая
/// паника), что противоречит «никогда не паника» из шапки модуля;
/// `Reader::read_frame` обязан читать кусками и остановиться на
/// `ShortRead`, не аллоцируя больше, чем реально пришло.
#[test]
fn corrupt_length_prefix_does_not_pre_allocate_ahead_of_the_stream() {
    const HUGE: u32 = 200 * 1024 * 1024; // 200 МиБ — заведомо больше, чем есть на "диске"
    let mut w = Writer::create(Vec::new(), header(), zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
    w.write_frame(&[rec(ev_snapshot_bid(), 0, 1, 10, 10)])
        .unwrap();
    let mut bytes = w.into_inner();

    let len_at = HEADER_LEN;
    bytes[len_at..len_at + 4].copy_from_slice(&HUGE.to_le_bytes());

    let mut r = Reader::open(&bytes[..]).unwrap();
    let (result, counts) = alloc_count::measure(|| r.read_frame());
    let err = result.unwrap_err();
    assert!(
        matches!(err, BinlogError::ShortRead { .. }),
        "ожидался ShortRead, получено {err:?}"
    );
    assert!(
        counts.bytes < 4 * 1024 * 1024,
        "испорченная длина ({HUGE} байт) не должна аллоцировать вперёд \
         объявленного объёма — реально выделено {} байт",
        counts.bytes
    );
}

#[test]
fn truncated_header_prefix_is_an_error() {
    // Короче даже magic+version (`MAGIC_VERSION_LEN`) — усечение обязано
    // ловиться на первом шаге чтения заголовка, до того как код вообще
    // пытается истолковать эти байты как magic (см. доку `Reader::open`
    // про два шага чтения).
    let bytes = [0u8; MAGIC_VERSION_LEN - 1];
    let err = Reader::open(&bytes[..]).unwrap_err();
    assert_eq!(
        err,
        BinlogError::TruncatedHeader {
            got: MAGIC_VERSION_LEN - 1,
            want: HEADER_LEN,
        }
    );
}

#[test]
fn truncated_header_tail_is_an_error() {
    // Magic и версия целы и валидны, хвост (tick_e9/step_e9/
    // max_records_per_frame) обрезан на один байт — усечение ловится на
    // втором шаге чтения, не читается как валидные, но сдвинутые байты.
    let mut bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 0, 1, 1, 1)]]);
    bytes.truncate(HEADER_LEN - 1);
    let err = Reader::open(&bytes[..]).unwrap_err();
    assert_eq!(
        err,
        BinlogError::TruncatedHeader {
            got: HEADER_LEN - 1,
            want: HEADER_LEN,
        }
    );
}

#[test]
fn bad_magic_is_rejected() {
    let mut bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 0, 1, 1, 1)]]);
    bytes[0] = b'X';
    let err = Reader::open(&bytes[..]).unwrap_err();
    assert!(matches!(err, BinlogError::BadMagic { .. }));
}

#[test]
fn unsupported_version_is_rejected() {
    let mut bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 0, 1, 1, 1)]]);
    bytes[4] = VERSION + 1;
    let err = Reader::open(&bytes[..]).unwrap_err();
    assert_eq!(err, BinlogError::UnsupportedVersion { got: VERSION + 1 });
}

#[test]
fn non_positive_tick_or_step_is_rejected_on_write_and_read() {
    assert!(Writer::create(
        Vec::new(),
        Header {
            tick_e9: 0,
            step_e9: 1,
            max_records_per_frame: DEFAULT_TEST_MAX_RECORDS_PER_FRAME,
        },
        zstd::DEFAULT_COMPRESSION_LEVEL
    )
    .is_err());

    let mut bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 0, 1, 1, 1)]]);
    // Затираем tick_e9 заголовка нулём напрямую в байтах.
    bytes[5..13].copy_from_slice(&0i64.to_le_bytes());
    let err = Reader::open(&bytes[..]).unwrap_err();
    assert!(matches!(err, BinlogError::InvalidHeader { .. }));
}

/// `max_records_per_frame = 0` — тот же класс ошибки, что нулевой/
/// отрицательный `tick_e9`/`step_e9` выше: заголовок, а не программный
/// аргумент, поэтому и здесь `Result`, а не паника (`validate_header`
/// общая на запись и чтение).
#[test]
fn zero_max_records_per_frame_is_rejected_on_write_and_read() {
    assert!(Writer::create(
        Vec::new(),
        Header {
            tick_e9: TICK_E9,
            step_e9: STEP_E9,
            max_records_per_frame: 0,
        },
        zstd::DEFAULT_COMPRESSION_LEVEL
    )
    .is_err());

    let mut bytes = write_all(header(), &[vec![rec(ev_snapshot_bid(), 0, 1, 1, 1)]]);
    // Затираем max_records_per_frame заголовка нулём напрямую в байтах
    // (смещение 21..25, см. `Writer::create`).
    bytes[21..25].copy_from_slice(&0u32.to_le_bytes());
    let err = Reader::open(&bytes[..]).unwrap_err();
    assert!(matches!(err, BinlogError::InvalidHeader { .. }));
}

// -----------------------------------------------------------------
// Требование 7 (ревизия 10, Decision 23): потолок разжатого кадра,
// вычисленный из `max_records_per_frame` заголовка.
// -----------------------------------------------------------------

/// Кодирует `n` заведомо одинаковых нулевых записей напрямую через кодировщик
/// v3 — не через `Writer::write_frame`, который сам отказался бы писать кадр
/// длиннее заголовочного потолка (см. тест ниже про эту самую проверку).
/// Записи одинаковы, поэтому ложатся одной группой: эпоха, заголовок группы и
/// по цене-размеру на запись. Такая нагрузка сжимается в исчезающе малый кадр
/// на диске, разжимаясь в огромный, — ровно форма, которую и обязан отвергать
/// потолок заголовка.
fn raw_frame_payload_all_zero(n: usize) -> Vec<u8> {
    let records: Vec<Record> = (0..n).map(|_| rec(0, 0, 0, 0, 0)).collect();
    let mut scratch = Vec::new();
    encode_frame_payload_v3(&records, &mut scratch);
    scratch
}

/// Заворачивает уже готовую (разжатую) полезную нагрузку в кадр
/// формата этого файла: `u32` длина сжатого блока LE, затем сам блок —
/// то же самое, что пишет `Writer::write_frame` после кодирования, но
/// здесь собрано вручную в обход его проверки потолка (см. доку
/// `raw_frame_payload_all_zero`).
fn frame_bytes_from_payload(payload: &[u8]) -> Vec<u8> {
    let compressed = zstd::bulk::compress(payload, zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
    let len = u32::try_from(compressed.len()).unwrap();
    let mut out = Vec::with_capacity(LEN_PREFIX + compressed.len());
    out.extend_from_slice(&len.to_le_bytes());
    out.extend_from_slice(&compressed);
    out
}

#[test]
#[should_panic(expected = "превышает потолок заголовка max_records_per_frame")]
fn writer_refuses_frame_exceeding_max_records_per_frame() {
    let max = 2u32;
    let hdr = Header {
        tick_e9: TICK_E9,
        step_e9: STEP_E9,
        max_records_per_frame: max,
    };
    let mut w = Writer::create(Vec::new(), hdr, zstd::DEFAULT_COMPRESSION_LEVEL).unwrap();
    let over_limit: Vec<Record> = (0..(max as i64 + 1))
        .map(|i| rec(ev_delta_ask(), i, i, i, i))
        .collect();
    // Программная ошибка вызывающего (см. доку `Writer::write_frame`) —
    // тест проверяет, что это паника, а не `Result::Err`.
    let _ = w.write_frame(&over_limit);
}

/// Кадр ровно на потолке (`records.len() == max_records_per_frame`) —
/// граничное значение, а не «за» ним: `Writer` обязан согласиться его
/// написать (`<=`, не `<`), и `Reader` обязан прочитать его штатно.
#[test]
fn frame_at_exactly_the_maximum_still_reads() {
    let max = 3u32;
    let hdr = Header {
        tick_e9: TICK_E9,
        step_e9: STEP_E9,
        max_records_per_frame: max,
    };
    // Малые значения (все поля кодируются одним байтом: `ev`/`order_id`
    // < 128, дельты в [-64, 63], `fval = 0.0`) — записи занимают ровно
    // `MIN_RECORD_LEN` = 8 байт каждая, и разжатое тело кадра совпадает
    // с потолком (`8 + 3*8 = 32`) байт в байт, не с запасом. Тест
    // проверяет именно эту границу: `<=`, а не `<`, у сравнения внутри
    // bounded zstd API. Кадр с тем же числом записей, но с типичными
    // для потока полями (большие флаги `ev`, дельты времени в наносекундах
    // между записями одного кадра) занял бы больше 8 байт на запись —
    // это не противоречие: `max_records_per_frame` в заголовке обязан
    // выбираться вызывающим (рекордером) с запасом над реальным
    // байтовым размером его собственных кадров, а не равняться
    // количеству записей, которое он фактически туда кладёт.
    let frame: Vec<Record> = (0..max as i64).map(|i| rec(i as u64, i, i, i, i)).collect();
    let bytes = write_all(hdr, std::slice::from_ref(&frame));
    let (read_hdr, frames) = read_all(&bytes);
    assert_eq!(read_hdr, hdr);
    assert_eq!(
        frames,
        vec![frame],
        "кадр ровно на потолке обязан читаться штатно"
    );
}

/// Файл версии 1 (до ревизии 10 Decision 23): целый и полный заголовок
/// этой версии — magic(4) + version(1) + tick_e9(8) + step_e9(8) = 21
/// байт, **без** `max_records_per_frame` и без единого лишнего байта
/// сверх. Собран напрямую, потому что ни один писатель этого кода версию 1
/// не пишет.
///
/// Длина нарочно ровно 21, не 25 (`HEADER_LEN` версий 2 и 3): если бы
/// `Reader::open` по-прежнему читал единым куском фиксированные
/// `HEADER_LEN` байт (одним чтением на весь заголовок, как до этой
/// правки), этих 21 не хватило бы на затребованные 25, и код вернул бы
/// `TruncatedHeader` — правдоподобную, но **вводящую в заблуждение**
/// ошибку: файл не обрезан, он просто другой, более старой версии.
/// Два раздельных чтения (`MAGIC_VERSION_LEN`, затем `HEADER_TAIL_LEN`)
/// обязаны поймать несовпадение версии на первом шаге, пятью байтами,
/// раньше, чем код вообще спросит про хвост — вот что здесь проверяется.
#[test]
fn version_one_file_is_rejected_not_misread() {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&MAGIC);
    bytes.push(1);
    bytes.extend_from_slice(&TICK_E9.to_le_bytes());
    bytes.extend_from_slice(&STEP_E9.to_le_bytes());
    assert_eq!(
        bytes.len(),
        21,
        "ровно заголовок версии 1, ни байтом больше"
    );

    let err = Reader::open(&bytes[..]).unwrap_err();
    assert_eq!(
        err,
        BinlogError::UnsupportedVersion { got: 1 },
        "старая версия обязана называться прямо, а не маскироваться под усечение"
    );
}

/// Тело кадра v2 с явными мёртвыми полями: восемь полей на запись, как их
/// писала живая запись (`ival` — из блочности, `order_id`/`fval` — нули).
/// Ненулевые значения подставляет тест: без них счётчики
/// `Reader::legacy_dead_fields` — то самое воспроизводимое доказательство
/// «0 % ненулевых» на старых файлах — нечем проверить.
fn v2_payload_with_dead_fields(records: &[Record], dead: &[(u64, i64, u64)]) -> Vec<u8> {
    let mut out = Vec::new();
    let epoch_ns = records.first().map_or(0, |r| r.exch_ts_ns);
    out.extend_from_slice(&epoch_ns.to_le_bytes());
    let mut prev_price = 0i64;
    let mut prev_qty = 0i64;
    for (r, (order_id, ival, fval_bits)) in records.iter().zip(dead) {
        write_uvarint(&mut out, r.ev);
        write_zigzag(&mut out, r.exch_ts_ns.wrapping_sub(epoch_ns));
        write_zigzag(&mut out, r.local_ts_ns.wrapping_sub(epoch_ns));
        write_zigzag(&mut out, r.price_ticks.wrapping_sub(prev_price));
        write_zigzag(&mut out, r.qty_lots.wrapping_sub(prev_qty));
        write_uvarint(&mut out, *order_id);
        write_zigzag(&mut out, *ival);
        write_uvarint(&mut out, *fval_bits);
        prev_price = r.price_ticks;
        prev_qty = r.qty_lots;
    }
    out
}

/// Собирает файл версии 1/2/3 вручную: заголовок с заданной версией и тела
/// кадров как есть. Нужен там, где пишется не текущая версия (`Writer` умеет
/// только её одну).
fn file_with_version(version: u8, hdr: Header, payloads: &[Vec<u8>]) -> Vec<u8> {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&MAGIC);
    bytes.push(version);
    bytes.extend_from_slice(&hdr.tick_e9.to_le_bytes());
    bytes.extend_from_slice(&hdr.step_e9.to_le_bytes());
    bytes.extend_from_slice(&hdr.max_records_per_frame.to_le_bytes());
    for payload in payloads {
        bytes.extend_from_slice(&frame_bytes_from_payload(payload));
    }
    bytes
}

/// Файл версии 2 — та форма, которую пишет живой коллектор до перезапуска на
/// новый бинарник (В-41): заголовок тот же, тело — восемь полей на запись.
/// Обязан читаться новым читателем, иначе вся идущая запись перестала бы
/// читаться; `order_id`/`ival`/`fval` из потока читаются, `ival` становится
/// `block`, а счётчики мёртвых полей остаются воспроизводимыми.
#[test]
fn version_two_file_is_read_as_legacy_with_dead_field_counters() {
    let frame = vec![
        rec(ev_snapshot_bid(), 10, 20, 100, 5),
        rec(ev_trade_buy(), 30, 40, 101, 2),
    ];
    let payload = v2_payload_with_dead_fields(&frame, &[(0, 0, 0), (u64::MAX, 1, 7)]);
    let bytes = file_with_version(VERSION_V2, header(), std::slice::from_ref(&payload));

    let mut r = Reader::open(&bytes[..]).unwrap();
    assert_eq!(r.version(), VERSION_V2);
    let got = r
        .read_frame()
        .unwrap()
        .expect("кадр версии 2 обязан читаться");
    assert!(r.read_frame().unwrap().is_none());
    assert_eq!(got.len(), 2);
    assert_eq!(got[0], frame[0]);
    assert_eq!(got[1].ev, frame[1].ev);
    assert_eq!(got[1].price_ticks, frame[1].price_ticks);
    assert!(got[1].block, "`ival != 0` — это `block`");
    let dead = r.legacy_dead_fields();
    assert_eq!(dead.nonzero_order_id, 1);
    assert_eq!(dead.nonzero_ival, 1);
    assert_eq!(dead.nonzero_fval, 1);
}

/// У файла версии 3 счётчиков мёртвых полей нет по построению: таких полей в
/// формате не существует, и нули здесь — не «повезло с данными», а свойство
/// раскладки.
#[test]
fn version_three_file_reports_no_dead_fields() {
    let bytes = write_all(header(), &[vec![rec(ev_trade_buy(), 10, 20, 100, 5)]]);
    let mut r = Reader::open(&bytes[..]).unwrap();
    assert_eq!(r.version(), VERSION);
    assert!(r.read_frame().unwrap().is_some());
    assert_eq!(r.legacy_dead_fields(), LegacyDeadFields::default());
}

/// Кадр, чья заявленная (сжатая) длина на диске правдоподобна, но
/// распаковка которого превышает потолок заголовка: отвергается как
/// `Err`, не паника, и — проверено через `alloc_count` — не после того,
/// как память под весь разжатый объём уже выделена. Потолок читателя
/// (`max_frame_payload_bytes`) сам по себе не бесполезен только если
/// он ограничивает именно **аллокацию**, а не служит числом, которое
/// код печатает уже после того, как разжал кадр целиком, — это и есть
/// разница между bounded API и «разжать, потом проверить».
#[test]
fn frame_exceeding_the_header_ceiling_is_rejected_without_full_allocation() {
    let max = 10u32;
    let hdr = Header {
        tick_e9: TICK_E9,
        step_e9: STEP_E9,
        max_records_per_frame: max,
    };
    let expected_ceiling = max_frame_payload_bytes(max);
    // 8 (эпоха) + 10 записей * 8 байт (минимум) = 88 — записано числом
    // здесь исключительно для читаемости остальных чисел теста, само
    // значение проверено равенством `max_frame_payload_bytes(max)` выше.
    assert_eq!(expected_ceiling, 88);

    let mut file = Writer::create(Vec::new(), hdr, zstd::DEFAULT_COMPRESSION_LEVEL)
        .unwrap()
        .into_inner();

    // Сильно за потолком, не впритык: два миллиона одинаковых нулевых записей
    // дают разжатый объём 8 (эпоха) + 5 (заголовок группы) + 2 на запись —
    // около четырёх мегабайт, то есть в десятки тысяч раз больше 88-байтового
    // потолка этого теста, — и при этом сжимаются в исчезающе малый кадр на
    // диске (проверено ниже).
    const N: usize = 2_000_000;
    let payload = raw_frame_payload_all_zero(N);
    assert!(
        payload.len() > expected_ceiling,
        "проверка теста на себе: нагрузка обязана быть за потолком заголовка \
         ({expected_ceiling} байт), иначе отвергать было бы нечего"
    );
    let frame_on_disk = frame_bytes_from_payload(&payload);
    // Проверка теста на себе: «заявленная длина правдоподобна» значит
    // сжатый кадр на диске обязан быть на порядки меньше того, во что
    // он разжимается (не впритык к потолку — потолок сам по себе
    // маленький, 88 байт, и сжатый кадр здесь его не меньше), иначе
    // это тест на что-то другое, не на bounded decompression.
    assert!(
        frame_on_disk.len() * 100 < payload.len(),
        "проверка теста на себе: сжатый кадр ({} байт) обязан быть на порядки \
         меньше разжатого объёма ({} байт), иначе это не «легко сжимаемая \
         полезная нагрузка», о которой говорит тест",
        frame_on_disk.len(),
        payload.len()
    );
    file.extend_from_slice(&frame_on_disk);

    let mut r = Reader::open(&file[..]).unwrap();
    let (result, counts) = alloc_count::measure(|| r.read_frame());
    let err = result
        .expect_err("кадр, чья распаковка превышает потолок заголовка, обязан быть ошибкой, не Ok");
    assert_eq!(
        err,
        BinlogError::FrameExceedsHeaderCeiling {
            max_records_per_frame: max,
            ceiling_bytes: expected_ceiling,
        }
    );
    assert!(
        counts.bytes < 50_000,
        "аллокация обязана остаться в пределах потолка заголовка ({expected_ceiling} байт \
         в этом тесте), а не расти пропорционально разжатому объёму (16 000 008 байт) — \
         реально выделено {} байт",
        counts.bytes
    );
}

// -----------------------------------------------------------------
// Varint/зигзаг — сами примитивы, границы диапазона.
// -----------------------------------------------------------------

#[test]
fn uvarint_round_trips_boundary_values() {
    for v in [0u64, 1, 127, 128, 300, u32::MAX as u64, u64::MAX] {
        let mut buf = Vec::new();
        write_uvarint(&mut buf, v);
        let mut pos = 0;
        assert_eq!(read_uvarint(&buf, &mut pos).unwrap(), v);
        assert_eq!(pos, buf.len());
    }
}

#[test]
fn zigzag_round_trips_boundary_values() {
    for v in [0i64, 1, -1, 63, -64, i64::MAX, i64::MIN] {
        let mut buf = Vec::new();
        write_zigzag(&mut buf, v);
        let mut pos = 0;
        assert_eq!(read_zigzag(&buf, &mut pos).unwrap(), v);
    }
}

#[test]
fn truncated_varint_is_an_error_not_a_panic() {
    let buf = [0x80u8]; // продолжение обещано, следующего байта нет
    let mut pos = 0;
    assert!(read_uvarint(&buf, &mut pos).is_err());
}
