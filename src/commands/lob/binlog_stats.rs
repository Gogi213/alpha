//! `lob binlog-stats` — что лежит в бинлоге и за что платятся байты.
//!
//! Задача (владелец 2026-09-13: «оптимизировать коллектор сильно: формат
//! файлов, формат записи, тип записи, скорость, объём») начинается с замера:
//! формат нельзя чинить, не зная, какие поля вообще несут информацию, сколько
//! записей на кадр и какие типы событий доминируют по числу.
//!
//! Команда **только читает** файл. Два режима:
//!
//! - обычный — счётчики: версия формата, записи, кадры, группы (сообщения
//!   биржи), типы событий, блочные сделки, доля `local_ts ≠ exch_ts`; для файла
//!   версии 2 — ещё и счётчики мёртвых полей (`order_id`/`ival`/`fval`),
//!   которыми доказано «0 % ненулевых» и на которых стоит формат v3;
//! - `--reencode` — замер A/B тикета 43
//!   (`.autopilot/2026-09-11-lob-density-ed3/tickets/43-binlog-v3.md`): те же
//!   записи кодируются кодеком v2 и v3 в памяти, сжимаются тем же zstd-1, что
//!   и коллектор, и сравниваются байт-в-байт по объёму, времени и round-trip.
//!   Это ответ на «чего не говорит первый замер» (`docs/findings/
//!   docs/findings/binlog-format-2026-09-13.md`): сколько **байтов** приносит каждое поле,
//!   видно только на перекодировке, а не по счётчикам записей.
//!
//! Скорость декодирования на живом пути меряется `lob react`/`--times`, а не
//! разовым проходом по диску; здесь время нужно только чтобы сравнить два
//! кодека между собой на одних и тех же записях (метрика M3 тикета 43).

use std::path::{Path, PathBuf};
use std::time::Instant;

use clap::Args;

use crate::binlog::{
    decode_frame_payload_v3, decode_frame_payload_v3_ev_table, encode_frame_payload_v2,
    encode_frame_payload_v3, encode_frame_payload_v3_ev_table,
    encode_frame_payload_v3_index_simulated, same_message, FieldBytes, LegacyDeadFields, Reader,
    Record, Writer, LEN_PREFIX, VERSION_V2,
};
use crate::commands::record::ZSTD_LEVEL;

/// Уровни zstd — кандидаты M1z тикета 44. Числа не изобретены: те же уровни
/// мерил T25 на живых файлах (`docs/findings/collector-2026-09-12.md`).
const ZSTD_LEVEL_CANDIDATE_3: i32 = 3;
const ZSTD_LEVEL_CANDIDATE_6: i32 = 6;

/// Аргументы `lob binlog-stats`.
#[derive(Debug, Args)]
pub struct BinlogStatsArgs {
    /// Файл бинлога (`<SYMBOL>-<день>.binlog`).
    #[arg(long)]
    pub path: PathBuf,
    /// Замер A/B формата: перекодировать те же записи кодеком v3 и сравнить
    /// объём, время и round-trip (метрики M1/M1b/M1i/M2/M3/M4 тикета 43 плюс
    /// M1e/M1z тикета 44).
    #[arg(long, default_value_t = false)]
    pub reencode: bool,
    /// Переписать файл версии 2 в версию 3 (тот же заголовок, те же границы
    /// кадров) — ворота M5c тикета 44: на переписанном файле читатели обязаны
    /// дать те же артефакты, что на исходном.
    #[arg(long)]
    pub rewrite_out: Option<PathBuf>,
}

/// Итог разбора — для печати диспетчером и для тестов.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct BinlogStats {
    /// Версия формата из заголовка: `VERSION` (текущая) или `VERSION_V2`.
    pub version: u8,
    /// Уровень zstd, если файл — контейнер архива (T46), `None` у обычных
    /// суток. Счётчики ниже от него не зависят: контейнер читается тем же
    /// `Reader`, и «что лежит внутри» одинаково у архива и у оригинала.
    pub archive_level: Option<u8>,
    pub records: u64,
    /// Групп (сообщений биржи): записи одного сообщения несут одни флаги и
    /// метки и считаются здесь по той же границе, по которой их режет кодек
    /// (`binlog::same_message`).
    pub groups: u64,
    pub frames: u64,
    pub bytes_on_disk: u64,
    /// Записей по коду типа события (`ev`), отсортировано по убыванию числа.
    pub by_ev: Vec<(u64, u64)>,
    /// Записи с `block` (блочная сделка: `BT` у Bybit, в v2 `ival != 0`).
    pub block_trades: u64,
    /// Записи с `rpi` (сделка об RPI-заявку: `RPI` у Bybit, 2026-09-16).
    /// На файлах до этого дня — нули по построению: бита в них нет.
    pub rpi_trades: u64,
    /// `local_ts != exch_ts` — сколько записей несут собственную метку приёма.
    pub local_ts_differs: u64,
    /// Мёртвые поля v2 (`order_id`/`ival`/`fval`). На файле v3 нули по
    /// построению: этих полей в формате нет.
    pub legacy_dead: LegacyDeadFields,
    pub min_records_in_frame: u64,
    pub max_records_in_frame: u64,
}

/// Хвост живого файла (A4, 2026-09-17): одна строка предупреждения, если
/// читатель остановился на обрезанном кадре. `binlog-stats` — команда чтения,
/// и падать на файле, который прямо сейчас дописывает коллектор, ей незачем;
/// порча (`Corrupt` и прочее) по-прежнему отказ — см. `Reader::read_frame_soft`.
fn warn_truncated<R: std::io::Read>(reader: &Reader<R>, path: &std::path::Path) {
    if reader.truncated_tail() {
        eprintln!(
            "{}: хвостовой кадр обрезан — файл дописывается, считаю прочитанное",
            path.display()
        );
    }
}

/// Разбирает файл целиком и считает счётчики. `Err` — только ввод-вывод и
/// порча формата: счётчики на испорченном файле не печатаются частично.
pub fn run_binlog_stats(args: &BinlogStatsArgs) -> anyhow::Result<BinlogStats> {
    let file = std::fs::File::open(&args.path)?;
    let bytes_on_disk = file.metadata()?.len();
    let mut reader = Reader::open(file)?;
    let mut stats = BinlogStats {
        version: reader.version(),
        archive_level: reader.archive_level(),
        bytes_on_disk,
        min_records_in_frame: u64::MAX,
        ..Default::default()
    };
    let mut by_ev: std::collections::BTreeMap<u64, u64> = std::collections::BTreeMap::new();
    while let Some(frame) = reader.read_frame_soft()? {
        stats.frames += 1;
        let n = frame.len() as u64;
        stats.records += n;
        stats.groups += count_groups(&frame);
        stats.min_records_in_frame = stats.min_records_in_frame.min(n);
        stats.max_records_in_frame = stats.max_records_in_frame.max(n);
        for r in &frame {
            *by_ev.entry(r.ev).or_insert(0) += 1;
            if r.block {
                stats.block_trades += 1;
            }
            if r.rpi {
                stats.rpi_trades += 1;
            }
            if r.local_ts_ns != r.exch_ts_ns {
                stats.local_ts_differs += 1;
            }
        }
    }
    if stats.frames == 0 {
        stats.min_records_in_frame = 0;
    }
    warn_truncated(&reader, &args.path);
    stats.legacy_dead = reader.legacy_dead_fields();
    let mut by_ev: Vec<(u64, u64)> = by_ev.into_iter().collect();
    by_ev.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    stats.by_ev = by_ev;
    Ok(stats)
}

/// Число групп в кадре: новая группа начинается там, где запись перестала быть
/// «тем же сообщением биржи» (см. `binlog::same_message`).
fn count_groups(frame: &[Record]) -> u64 {
    let mut groups = 0;
    let mut prev: Option<&Record> = None;
    for r in frame {
        if !prev.is_some_and(|p| same_message(p, r)) {
            groups += 1;
        }
        prev = Some(r);
    }
    groups
}

/// Строки отчёта — одна на факт, чтобы диспетчер не считал сам.
pub fn summary_lines(path: &Path, s: &BinlogStats) -> Vec<String> {
    let per_record = if s.records > 0 {
        s.bytes_on_disk as f64 / s.records as f64
    } else {
        0.0
    };
    let frame_avg = if s.frames > 0 {
        s.records as f64 / s.frames as f64
    } else {
        0.0
    };
    let group_avg = if s.groups > 0 {
        s.records as f64 / s.groups as f64
    } else {
        0.0
    };
    let share = |v: u64| {
        if s.records > 0 {
            100.0 * v as f64 / s.records as f64
        } else {
            0.0
        }
    };
    let mut lines = vec![
        format!(
            "binlog-stats: {} · формат v{} ({}) · {:.1} МБ · записей {} · кадров {} ({:.0} записей на кадр, от {} до {}) · {:.2} Б/запись",
            path.display(),
            s.version,
            if s.version == VERSION_V2 {
                "legacy, её пишет живой коллектор"
            } else {
                "текущая"
            },
            s.bytes_on_disk as f64 / 1e6,
            s.records,
            s.frames,
            frame_avg,
            s.min_records_in_frame,
            s.max_records_in_frame,
            per_record
        ),
        format!(
            "binlog-stats: групп (сообщений биржи) {} — {:.1} записей на сообщение · local_ts ≠ exch_ts {:.1} % · блочных сделок {} ({:.2} %) · RPI-сделок {} ({:.2} %)",
            s.groups,
            group_avg,
            share(s.local_ts_differs),
            s.block_trades,
            share(s.block_trades),
            s.rpi_trades,
            share(s.rpi_trades)
        ),
    ];
    if let Some(level) = s.archive_level {
        lines.push(format!(
            "binlog-stats: источник — контейнер архива (zstd-{level}, T46): один поток на \
             сутки, тела кадров внутри несжаты"
        ));
    }
    if s.version == VERSION_V2 {
        lines.push(format!(
            "binlog-stats: мёртвые поля v2 — order_id {} ({:.2} %) · ival {} ({:.2} %) · fval {} ({:.2} %)",
            s.legacy_dead.nonzero_order_id,
            share(s.legacy_dead.nonzero_order_id),
            s.legacy_dead.nonzero_ival,
            share(s.legacy_dead.nonzero_ival),
            s.legacy_dead.nonzero_fval,
            share(s.legacy_dead.nonzero_fval)
        ));
    } else {
        lines.push(
            "binlog-stats: мёртвых полей (order_id/ival/fval) в формате v3 нет по построению"
                .to_string(),
        );
    }
    for (ev, n) in s.by_ev.iter().take(8) {
        lines.push(format!(
            "binlog-stats: ev={ev:#x} — {n} записей ({:.1} %)",
            share(*n)
        ));
    }
    lines
}

// ---------------------------------------------------------------------
// `--reencode`: замер A/B формата (тикет 43).
// ---------------------------------------------------------------------

/// Итог A/B-замера: байты, время и расхождения round-trip.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct ReencodeReport {
    pub records: u64,
    pub frames: u64,
    pub bytes_on_disk: u64,
    /// Перекодировка тем же кодеком v2: совпала ли длина каждого кадра с тем,
    /// что лежит на диске (M4б — проверка, что замер сравнивает то же самое).
    pub v2_frames_matching_disk: u64,
    pub v2_bytes: u64,
    pub v3_bytes: u64,
    /// v3 с кадром на сообщение — «local_ts буквально в кадре» (M1b).
    pub v3_per_message_bytes: u64,
    pub v3_per_message_frames: u64,
    /// Симуляция «уровень индексом» (M1i).
    pub v3_index_bytes: u64,
    /// Вариант «`ev` таблицей на кадр» (M1e).
    pub v3_ev_table_bytes: u64,
    /// Тот же v3-payload, сжатый уровнями 3 и 6 (M1z).
    pub v3_level3_bytes: u64,
    pub v3_level6_bytes: u64,
    /// Только сжатие (без кодирования): уровень 1 — базовая линия M1z,
    /// уровень 6 — кандидат. Без разделения нельзя честно сказать, что
    /// уровень 6 стоит столько-то CPU: `v3_encode_ns` включает и кодирование.
    pub v3_compress_ns: u128,
    pub v3_level6_encode_ns: u128,
    /// Расхождения round-trip варианта «`ev` таблицей» (M4e).
    pub ev_table_mismatches: u64,
    pub v2_field_bytes: FieldBytes,
    pub v3_field_bytes: FieldBytes,
    pub v2_encode_ns: u128,
    pub v3_encode_ns: u128,
    pub v2_decode_ns: u128,
    pub v3_decode_ns: u128,
    pub round_trip_mismatches: u64,
}

/// Замер A/B на одном файле: те же записи, тот же уровень zstd (`ZSTD_LEVEL`,
/// им пишет коллектор), разные кодеки. Файл читается в память целиком: замер
/// времени не должен мерить системные вызовы, а файл суток — десятки мегабайт.
///
/// Считается только по файлу версии 2: сравнивать объём формата с самим собой
/// бессмысленно, а именно v2 лежит на диске у живого коллектора (В-41).
pub fn measure_reencode(args: &BinlogStatsArgs) -> anyhow::Result<ReencodeReport> {
    let data = std::fs::read(&args.path)?;
    let bytes_on_disk = data.len() as u64;
    let mut reader = Reader::open(&data[..])?;
    let version = reader.version();
    if version != VERSION_V2 {
        anyhow::bail!(
            "{} — формат v{version}: замер A/B считается по файлу версии 2, \
             её и пишет живой коллектор",
            args.path.display()
        );
    }
    let mut compressor = zstd::bulk::Compressor::new(ZSTD_LEVEL)?;
    let mut decompressor = zstd::bulk::Decompressor::new()?;
    let mut report = ReencodeReport {
        bytes_on_disk,
        ..Default::default()
    };

    let mut v2_raw = Vec::new();
    let mut v3_raw = Vec::new();
    let mut msg_raw = Vec::new();
    let mut index_raw = Vec::new();
    let mut ev_raw = Vec::new();
    let mut compressed = Vec::new();
    let mut index_flags: Vec<bool> = Vec::new();
    // Кандидаты M1z — из замера T25 (`docs/findings/collector-2026-09-12.md`):
    // уровень 1 (текущий, 170 нс/запись), 3, 6 (475 нс/запись, −4.7 % байт) и 9.
    // Здесь берутся 3 и 6: 9 дороже вчетверо за 1 % байт, а память zstd
    // ограничена размером входа (наши кадры — десятки килобайт).
    let mut level3 = zstd::bulk::Compressor::new(ZSTD_LEVEL_CANDIDATE_3)?;
    let mut level6 = zstd::bulk::Compressor::new(ZSTD_LEVEL_CANDIDATE_6)?;

    loop {
        let t_decode = Instant::now();
        let frame = reader.read_frame_soft()?;
        let v2_decode_ns = t_decode.elapsed().as_nanos();
        let Some(frame) = frame else {
            // Замер на живом файле: обрезанный хвост — конец прочитанного
            // (A4), а не отказ замера.
            warn_truncated(&reader, &args.path);
            break;
        };
        report.frames += 1;
        report.v2_decode_ns += v2_decode_ns;
        report.records += frame.len() as u64;

        // v2: перекодировка тем же кодеком. Длина кадра обязана совпасть с
        // диском — иначе замер сравнивает не то, что лежит в файле.
        let t_encode = Instant::now();
        v2_raw.clear();
        report
            .v2_field_bytes
            .add(encode_frame_payload_v2(&frame, &mut v2_raw));
        compress_into(&mut compressor, &v2_raw, &mut compressed)?;
        report.v2_encode_ns += t_encode.elapsed().as_nanos();
        report.v2_bytes += (LEN_PREFIX + compressed.len()) as u64;
        if LEN_PREFIX + compressed.len() == reader.last_frame_bytes() {
            report.v2_frames_matching_disk += 1;
        }

        // v3: кадр — пачка сообщений (текущий формат).
        let t_encode = Instant::now();
        v3_raw.clear();
        report
            .v3_field_bytes
            .add(encode_frame_payload_v3(&frame, &mut v3_raw));
        report.v3_encode_ns += t_encode.elapsed().as_nanos();
        let t_compress = Instant::now();
        compress_into(&mut compressor, &v3_raw, &mut compressed)?;
        report.v3_compress_ns += t_compress.elapsed().as_nanos();
        report.v3_bytes += (LEN_PREFIX + compressed.len()) as u64;

        // M4а: v2 → v3 → v2, сравнение поле в поле.
        match decompress_exact(&mut decompressor, &compressed, v3_raw.len()) {
            Ok(payload) => {
                let t_decode = Instant::now();
                let back = decode_frame_payload_v3(&payload);
                report.v3_decode_ns += t_decode.elapsed().as_nanos();
                match back {
                    Ok(back) => {
                        if back.len() != frame.len() {
                            report.round_trip_mismatches +=
                                (back.len() as i64 - frame.len() as i64).unsigned_abs();
                        }
                        report.round_trip_mismatches += back
                            .iter()
                            .zip(frame.iter())
                            .filter(|(a, b)| a != b)
                            .count() as u64;
                    }
                    Err(_) => report.round_trip_mismatches += frame.len() as u64,
                }
            }
            Err(e) => return Err(anyhow::anyhow!("v3-кадр не разжался обратно: {e}")),
        }

        // M1b: кадр на сообщение — то, что буквально просит «local_ts в кадр».
        // Свой буфер: `v3_raw` ещё нужен целиком для M1z ниже, и затирание его
        // телом последнего сообщения уже давало неверный замер (0.11 Б/запись).
        for message in split_messages(&frame) {
            msg_raw.clear();
            encode_frame_payload_v3(message, &mut msg_raw);
            compress_into(&mut compressor, &msg_raw, &mut compressed)?;
            report.v3_per_message_bytes += (LEN_PREFIX + compressed.len()) as u64;
            report.v3_per_message_frames += 1;
        }

        // M1i: симуляция «уровень индексом» (в формат не входит, см.
        // `PriceMode::IndexSimulated` в `binlog`).
        index_flags.clear();
        index_flags.extend(frame.iter().map(is_indexable_depth));
        index_raw.clear();
        encode_frame_payload_v3_index_simulated(&frame, &index_flags, &mut index_raw);
        compress_into(&mut compressor, &index_raw, &mut compressed)?;
        report.v3_index_bytes += (LEN_PREFIX + compressed.len()) as u64;

        // M1e/M4e: «ev таблицей» — реальный кодек варианта плюс его round-trip
        // (без обратного чтения экономия проверялась бы на слово).
        ev_raw.clear();
        encode_frame_payload_v3_ev_table(&frame, &mut ev_raw);
        compress_into(&mut compressor, &ev_raw, &mut compressed)?;
        report.v3_ev_table_bytes += (LEN_PREFIX + compressed.len()) as u64;
        match decode_frame_payload_v3_ev_table(&ev_raw) {
            Ok(back) => {
                if back.len() != frame.len() {
                    report.ev_table_mismatches +=
                        (back.len() as i64 - frame.len() as i64).unsigned_abs();
                }
                report.ev_table_mismatches += back
                    .iter()
                    .zip(frame.iter())
                    .filter(|(a, b)| a != b)
                    .count() as u64;
            }
            Err(_) => report.ev_table_mismatches += frame.len() as u64,
        }

        // M1z: тот же v3-payload уровнями 3 и 6 (уровень 1 — базовая линия).
        // Время меряется на уровне 6: он дороже всех, и его цена — CPU.
        compress_into(&mut level3, &v3_raw, &mut compressed)?;
        report.v3_level3_bytes += (LEN_PREFIX + compressed.len()) as u64;
        let t_encode = Instant::now();
        compress_into(&mut level6, &v3_raw, &mut compressed)?;
        report.v3_level6_encode_ns += t_encode.elapsed().as_nanos();
        report.v3_level6_bytes += (LEN_PREFIX + compressed.len()) as u64;
    }

    Ok(report)
}

/// Строки отчёта A/B — тонкая обёртка над замером, чтобы диспетчер не считал
/// ничего сам, а тесты читали числа, а не текст.
pub fn run_reencode(args: &BinlogStatsArgs) -> anyhow::Result<Vec<String>> {
    let report = measure_reencode(args)?;
    Ok(report_lines(&args.path, &report))
}

/// Книжная неснапшотная запись: только у таких цена — уровень книги, у
/// которого есть индекс; снимок кадра 0 несёт абсолютные цены и без книги
/// (Decision 7) не индексируется. Торговые события в индекс не входят: у
/// сделки цена не «уровень».
fn is_indexable_depth(r: &Record) -> bool {
    use hftbacktest::types::{DEPTH_EVENT, DEPTH_SNAPSHOT_EVENT};
    r.ev & DEPTH_EVENT != 0 && r.ev & DEPTH_SNAPSHOT_EVENT == 0
}

/// Режет кадр на сообщения биржи — по той же границе, что кодек.
fn split_messages(frame: &[Record]) -> Vec<&[Record]> {
    let mut messages = Vec::new();
    let mut start = 0;
    for i in 1..=frame.len() {
        let boundary = i == frame.len() || !same_message(&frame[i - 1], &frame[i]);
        if boundary {
            messages.push(&frame[start..i]);
            start = i;
        }
    }
    messages
}

fn compress_into(
    compressor: &mut zstd::bulk::Compressor<'static>,
    raw: &[u8],
    out: &mut Vec<u8>,
) -> anyhow::Result<()> {
    out.clear();
    let bound = zstd::zstd_safe::compress_bound(raw.len());
    if out.capacity() < bound {
        out.reserve(bound - out.len());
    }
    compressor.compress_to_buffer(raw, out)?;
    Ok(())
}

fn decompress_exact(
    decompressor: &mut zstd::bulk::Decompressor<'static>,
    compressed: &[u8],
    want: usize,
) -> anyhow::Result<Vec<u8>> {
    Ok(decompressor.decompress(compressed, want)?)
}

/// Строки отчёта A/B: те же величины, что в пререгистрации тикета 43, чтобы
/// решение принималось по числу, а не по впечатлению.
fn report_lines(path: &Path, r: &ReencodeReport) -> Vec<String> {
    let per = |bytes: u64| {
        if r.records > 0 {
            bytes as f64 / r.records as f64
        } else {
            0.0
        }
    };
    let per_record_disk = per(r.bytes_on_disk);
    let per_record_v3 = per(r.v3_bytes);
    let ratio = |bytes: u64| {
        if r.bytes_on_disk > 0 {
            100.0 * bytes as f64 / r.bytes_on_disk as f64
        } else {
            0.0
        }
    };
    let ns_per_record = |ns: u128| {
        if r.records > 0 {
            ns as f64 / r.records as f64
        } else {
            0.0
        }
    };
    let gb = |bytes: u64| bytes as f64 / 1e9;
    let mut lines = vec![
        format!(
            "reencode M1: {} — записей {} · кадров {} · на диске {:.1} МБ ({:.2} Б/запись)",
            path.display(),
            r.records,
            r.frames,
            gb(r.bytes_on_disk) * 1000.0,
            per_record_disk
        ),
        format!(
            "reencode M1: v3 (кадр = пачка сообщений) — {:.1} МБ ({:.2} Б/запись, {:.1} % от v2)",
            gb(r.v3_bytes) * 1000.0,
            per_record_v3,
            ratio(r.v3_bytes)
        ),
        format!(
            "reencode M1b: v3 (кадр = сообщение) — {} кадров, {:.1} МБ ({:.2} Б/запись, {:.1} % от v2)",
            r.v3_per_message_frames,
            gb(r.v3_per_message_bytes) * 1000.0,
            per(r.v3_per_message_bytes),
            ratio(r.v3_per_message_bytes)
        ),
        format!(
            "reencode M1i: симуляция «уровень индексом» — {:.2} Б/запись, {:.1} % от v3-пачки (внедрять только при экономии ≥ 10 % M1i-порога тикета 43)",
            per(r.v3_index_bytes),
            100.0 * r.v3_index_bytes as f64 / r.v3_bytes.max(1) as f64
        ),
        format!(
            "reencode M1e: «ev таблицей на кадр» — {:.2} Б/запись, {:.1} % от v3-пачки, round-trip расхождений {} (порог ≥ 5 % экономии, тикет 44)",
            per(r.v3_ev_table_bytes),
            100.0 * r.v3_ev_table_bytes as f64 / r.v3_bytes.max(1) as f64,
            r.ev_table_mismatches
        ),
        format!(
            "reencode M1z: уровень zstd 3 — {:.2} Б/запись ({:.1} % от уровня 1); уровень 6 — {:.2} Б/запись ({:.1} % от уровня 1), сжатие {:.0} нс/запись против {:.0} на уровне 1",
            per(r.v3_level3_bytes),
            100.0 * r.v3_level3_bytes as f64 / r.v3_bytes.max(1) as f64,
            per(r.v3_level6_bytes),
            100.0 * r.v3_level6_bytes as f64 / r.v3_bytes.max(1) as f64,
            ns_per_record(r.v3_level6_encode_ns),
            ns_per_record(r.v3_compress_ns)
        ),
        format!(
            "reencode M2 (экстраполяция ×10 к топ-100, не замер): v2 {:.1} ГБ/сутки, v3 {:.1} ГБ/сутки",
            gb(r.bytes_on_disk) * 10.0,
            gb(r.v3_bytes) * 10.0
        ),
        format!(
            "reencode M3 (нс/запись): v2 кодирование+zstd {:.0}, декодирование {:.0}; v3 кодирование+zstd {:.0}, декодирование {:.0}",
            ns_per_record(r.v2_encode_ns),
            ns_per_record(r.v2_decode_ns),
            ns_per_record(r.v3_encode_ns),
            ns_per_record(r.v3_decode_ns)
        ),
        format!(
            "reencode M4: round-trip v2→v3→v2 — расхождений {} записей; перекодировка v2 совпала с диском на {}/{} кадрах",
            r.round_trip_mismatches, r.v2_frames_matching_disk, r.frames
        ),
        format!(
            "reencode поля v2 (сырые байты на запись): ev {:.2} · exch_ts {:.2} · local_ts {:.2} · цена {:.2} · размер {:.2} · мёртвые {:.2}",
            per(r.v2_field_bytes.ev as u64),
            per((r.v2_field_bytes.exch_ts + r.v2_field_bytes.epoch) as u64),
            per(r.v2_field_bytes.local_ts as u64),
            per(r.v2_field_bytes.price as u64),
            per(r.v2_field_bytes.qty as u64),
            per(r.v2_field_bytes.dead_fields as u64)
        ),
        format!(
            "reencode поля v3 (сырые байты на запись): ev {:.2} · exch_ts {:.2} · local_ts {:.2} · цена {:.2} · размер {:.2} · заголовок группы {:.2}",
            per(r.v3_field_bytes.ev as u64),
            per((r.v3_field_bytes.exch_ts + r.v3_field_bytes.epoch) as u64),
            per(r.v3_field_bytes.local_ts as u64),
            per(r.v3_field_bytes.price as u64),
            per(r.v3_field_bytes.qty as u64),
            per(r.v3_field_bytes.group_overhead as u64)
        ),
    ];
    lines.push(
        "reencode: числа — один и тот же файл, те же записи, тот же zstd-1; пороги решения — в тикетах 43 и 44"
            .to_string(),
    );
    lines
}

/// Переписывает файл версии 2 в версию 3: тот же заголовок (шаги и потолок
/// кадра), те же границы кадров, тот же порядок записей. Ворота M5c тикета 44:
/// на переписанном файле `verify`/`levels` обязаны дать **те же** артефакты,
/// что на исходном, — иначе раскатка v3 на сервер меняет результаты анализа.
///
/// Пишет только v3: версия 2 остаётся только для чтения (её пишет живой
/// коллектор старого бинарника, В-41/В-48).
pub fn rewrite_v2_to_v3(src: &Path, out: &Path) -> anyhow::Result<Vec<String>> {
    let data = std::fs::read(src)?;
    let mut reader = Reader::open(&data[..])?;
    let version = reader.version();
    if version != VERSION_V2 {
        anyhow::bail!(
            "{} — формат v{version}: переписывать в v3 можно только файл версии 2",
            src.display()
        );
    }
    let header = reader.header();
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let file = std::fs::File::create(out)?;
    let mut writer = Writer::create(file, header, ZSTD_LEVEL)?;
    let mut frames = 0u64;
    let mut records = 0u64;
    // Здесь `read_frame`, а не мягкий вариант (A4): `--rewrite-out` **пишет
    // файл-артефакт**, и молча потерянный хвостовой кадр сделал бы его
    // «полными сутками» на вид — отказ честнее (та же логика, что у ворот
    // M5c: артефакт обязан совпадать с оригиналом).
    while let Some(frame) = reader.read_frame()? {
        writer.write_frame(&frame)?;
        frames += 1;
        records += frame.len() as u64;
    }
    writer.flush()?;
    let bytes = std::fs::metadata(out)?.len();
    Ok(vec![
        format!(
            "rewrite: {} → {} · формат v{VERSION_V2} → v{} · кадров {} · записей {} · {:.1} МБ",
            src.display(),
            out.display(),
            crate::binlog::VERSION,
            frames,
            records,
            bytes as f64 / 1e6
        ),
        "rewrite: тот же заголовок и те же границы кадров — читатели обязаны дать те же артефакты (M5c тикета 44)".to_string(),
    ])
}

#[cfg(test)]
mod tests;
