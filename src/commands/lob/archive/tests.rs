//! Тесты команды `lob archive` (T46): что именно происходит с файлами,
//! порядок шагов и эквивалентность артефактов на архиве и на оригинале.

use super::*;
use crate::commands::lob::test_support;

/// Каталог с настоящим суточным файлом v3 (та же фикстура, что у `levels`).
fn day_dir(symbol: &str, day: &str) -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
    test_support::write_day(dir.path(), symbol, day, &test_support::three_level_frames());
    dir
}

fn args_of(path: &Path) -> ArchiveArgs {
    ArchiveArgs {
        path: path.to_path_buf(),
        level: archive::DEFAULT_LEVEL,
        out: None,
        delete_original: false,
    }
}

fn orig_path(dir: &Path, symbol: &str, day: &str) -> PathBuf {
    crate::commands::record::day_file_path(dir, symbol, day, 1)
}

/// Основной путь команды: контейнер ложится рядом под именем `<файл>.zst`,
/// временный файл не остаётся, сводка несёт кадры/записи/размеры, а оригинал
/// на месте (флага удаления не было).
#[test]
fn archive_writes_container_next_to_the_original_and_keeps_it() {
    let dir = day_dir("SOLUSDT", "2026-09-08");
    let src = orig_path(dir.path(), "SOLUSDT", "2026-09-08");
    let summary = run_archive(&args_of(&src)).unwrap();
    assert_eq!(summary.src, src);
    assert_eq!(
        summary.out,
        dir.path().join("SOLUSDT-2026-09-08.binlog.zst")
    );
    assert!(summary.out.is_file());
    assert!(!tmp_path(&summary.out).exists(), "временный файл убран");
    assert!(src.is_file(), "оригинал без --delete-original не трогается");
    assert!(!summary.original_deleted);
    assert_eq!(summary.source_bytes, std::fs::metadata(&src).unwrap().len());
    assert_eq!(
        summary.archive_bytes,
        std::fs::metadata(&summary.out).unwrap().len()
    );
    assert_eq!(summary.level, archive::DEFAULT_LEVEL);
    // Кадры и записи — из обязательной сверки: она и есть доказательство, что
    // в контейнере те же сутки.
    assert!(summary.frames > 0 && summary.records > 0);
    // Строки отчёта: размеры, проценты, времена и судьба оригинала.
    let lines = summary_lines(&summary);
    assert!(lines[0].contains("уровень zstd 19"), "{:?}", lines[0]);
    assert!(lines[1].contains("round-trip сошёлся"), "{:?}", lines[1]);
    assert!(lines[3].contains("оригинал оставлен"), "{:?}", lines[3]);
}

/// Архив читается тем же `Reader`, что обычный файл: уровень виден, заголовок
/// тот же, кадров столько же.
#[test]
fn container_is_readable_by_the_plain_reader() {
    let dir = day_dir("SOLUSDT", "2026-09-08");
    let src = orig_path(dir.path(), "SOLUSDT", "2026-09-08");
    let summary = run_archive(&args_of(&src)).unwrap();
    let mut plain = crate::binlog::Reader::open(std::fs::File::open(&src).unwrap()).unwrap();
    let mut packed =
        crate::binlog::Reader::open(std::fs::File::open(&summary.out).unwrap()).unwrap();
    assert_eq!(packed.archive_level(), Some(archive::DEFAULT_LEVEL as u8));
    assert_eq!(packed.version(), crate::binlog::VERSION);
    assert_eq!(packed.header(), plain.header());
    let mut frames = 0;
    while let Some(f) = packed.read_frame().unwrap() {
        let p = plain.read_frame().unwrap().expect("кадров не меньше");
        assert_eq!(f, p);
        frames += 1;
    }
    assert!(plain.read_frame().unwrap().is_none());
    assert_eq!(frames as u64, summary.frames);
}

/// `--out` в другой каталог и свой уровень: контейнер ложится туда, где
/// сказано, и объявляет запрошенный уровень.
#[test]
fn explicit_out_and_level_are_honoured() {
    let dir = day_dir("SOLUSDT", "2026-09-08");
    let src = orig_path(dir.path(), "SOLUSDT", "2026-09-08");
    let out_dir = tempfile::tempdir().unwrap();
    let out = out_dir.path().join("packed.zst");
    let mut args = args_of(&src);
    args.level = 3;
    args.out = Some(out.clone());
    let summary = run_archive(&args).unwrap();
    assert_eq!(summary.out, out);
    assert_eq!(summary.level, 3);
    assert!(out.is_file());
    assert!(!dir.path().join("SOLUSDT-2026-09-08.binlog.zst").exists());
}

/// Отказы до всякой записи: архивировать архив, положить контейнер поверх
/// оригинала, затереть существующий файл. Оригинал при этом не тронут.
#[test]
fn archive_refuses_archive_input_same_out_and_existing_out() {
    let dir = day_dir("SOLUSDT", "2026-09-08");
    let src = orig_path(dir.path(), "SOLUSDT", "2026-09-08");
    let summary = run_archive(&args_of(&src)).unwrap();

    let again = args_of(&summary.out);
    let err = run_archive(&again).unwrap_err().to_string();
    assert!(err.contains("уже контейнер"), "{err}");
    let mut same = args_of(&src);
    same.out = Some(src.clone());
    let err = run_archive(&same).unwrap_err().to_string();
    assert!(err.contains("не может лечь вместо оригинала"), "{err}");

    // Существующий контейнер не затирается даже при явном --out.
    let mut existing = args_of(&src);
    existing.out = Some(summary.out.clone());
    let err = run_archive(&existing).unwrap_err().to_string();
    assert!(err.contains("уже существует"), "{err}");
    assert!(src.is_file());
}

/// Не-v3 файл: архивация отказывается и **не оставляет** ничего лишнего в
/// каталоге (в частности временного контейнера).
#[test]
fn non_v3_file_is_refused_and_leaves_nothing_behind() {
    let dir = tempfile::tempdir().unwrap();
    let src = dir.path().join("SOLUSDT-2026-09-08.binlog");
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&crate::binlog::MAGIC);
    bytes.push(crate::binlog::VERSION_V2);
    bytes.extend_from_slice(&1_000_000i64.to_le_bytes());
    bytes.extend_from_slice(&1_000_000i64.to_le_bytes());
    bytes.extend_from_slice(&1_000u32.to_le_bytes());
    std::fs::write(&src, &bytes).unwrap();
    let err = run_archive(&args_of(&src)).unwrap_err().to_string();
    assert!(err.contains("версия 2"), "{err}");
    assert_eq!(std::fs::read(&src).unwrap(), bytes, "оригинал не изменён");
    assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 1);
}

/// Имя не по раскладке записи — команда отказывается: маркер сверки и
/// резолверы ищут `<SYMBOL>-<день>...`, а не произвольный файл.
#[test]
fn file_without_a_day_in_the_name_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let src = dir.path().join("SOLUSDT.binlog");
    std::fs::write(&src, b"not a binlog").unwrap();
    let err = run_archive(&args_of(&src)).unwrap_err().to_string();
    assert!(err.contains("не разбирается"), "{err}");
}

/// Пропавший файл — понятная ошибка, а не паника.
#[test]
fn missing_file_is_an_error() {
    let dir = tempfile::tempdir().unwrap();
    let src = dir.path().join("SOLUSDT-2026-09-08.binlog");
    let err = run_archive(&args_of(&src)).unwrap_err().to_string();
    assert!(err.contains("не найден"), "{err}");
}

/// `--delete-original` без маркера сверки: отказ, оригинал на месте, архив
/// (уже собранный и сверенный) остаётся — оператору есть что выгружать.
#[test]
fn delete_original_without_marker_is_refused() {
    let dir = day_dir("SOLUSDT", "2026-09-08");
    let src = orig_path(dir.path(), "SOLUSDT", "2026-09-08");
    let mut args = args_of(&src);
    args.delete_original = true;
    let err = run_archive(&args).unwrap_err().to_string();
    assert!(err.contains("нет маркера"), "{err}");
    assert!(src.is_file(), "оригинал обязан остаться");
    assert!(dir.path().join("SOLUSDT-2026-09-08.binlog.zst").is_file());
}

/// Маркер `fail` — тот же отказ: «данные не сверены» и «сверены с ошибкой»
/// для необратимого удаления неразличимы (fail-closed, R44).
#[test]
fn delete_original_with_failed_marker_is_refused() {
    let dir = day_dir("SOLUSDT", "2026-09-08");
    let src = orig_path(dir.path(), "SOLUSDT", "2026-09-08");
    std::fs::write(dir.path().join("verify-SOLUSDT.status"), "fail").unwrap();
    let mut args = args_of(&src);
    args.delete_original = true;
    assert!(run_archive(&args).is_err());
    assert!(src.is_file());
}

/// `ok`-маркер и явный флаг — единственная комбинация, при которой оригинал
/// уходит; в сводке это названо, и архив на месте.
#[test]
fn delete_original_needs_the_flag_and_the_ok_marker() {
    let dir = day_dir("SOLUSDT", "2026-09-08");
    let src = orig_path(dir.path(), "SOLUSDT", "2026-09-08");
    std::fs::write(dir.path().join("verify-SOLUSDT.status"), "ok").unwrap();
    let mut args = args_of(&src);
    args.delete_original = true;
    let summary = run_archive(&args).unwrap();
    assert!(summary.original_deleted);
    assert!(!src.exists(), "оригинал удалён по флагу и маркеру");
    assert!(summary.out.is_file());
    assert!(summary
        .marker
        .as_ref()
        .is_some_and(|p| p.ends_with("verify-SOLUSDT.status")));
    assert!(summary_lines(&summary)[3].contains("оригинал удалён"));
}

/// Резолверы видят архив как те же сутки: `session_binlog_for`,
/// `session_parts_for`, `session_days_in_dir` и `list_symbol_binlogs` — с
/// одним файлом, с архивом и с обоими (тогда сутки читаются **один** раз,
/// побеждает обычный файл).
#[test]
fn resolvers_see_the_archive_as_the_same_day() {
    use crate::commands::lob::{session_binlog_for, session_days_in_dir, session_parts_for};
    let dir = day_dir("SOLUSDT", "2026-09-08");
    let src = orig_path(dir.path(), "SOLUSDT", "2026-09-08");
    let summary = run_archive(&args_of(&src)).unwrap();
    let packed = summary.out.clone();

    // `session_parts_for` читает каталог только с `session.json` (это
    // признак сессии, а не просто папки с файлами) — фикстуре он нужен.
    std::fs::write(
        dir.path().join("session.json"),
        r#"{"start_hour_utc":0,"binlog_files":[]}"#,
    )
    .unwrap();

    // Оба файла в каталоге: сутки — один файл, и это оригинал.
    assert_eq!(
        session_binlog_for(dir.path(), "SOLUSDT").unwrap(),
        vec![src.clone()],
        "оригинал и архив одной части — одни сутки, читаются один раз"
    );
    let parts = session_parts_for(dir.path(), "SOLUSDT").unwrap();
    assert_eq!(parts.len(), 1);
    assert_eq!(parts[0].part, 1);
    assert_eq!(parts[0].day_utc, "2026-09-08");
    assert_eq!(session_days_in_dir(dir.path()).len(), 1);

    // Только архив (штатный сценарий: оригинал удалён после сверки).
    std::fs::remove_file(&src).unwrap();
    assert_eq!(
        session_binlog_for(dir.path(), "SOLUSDT").unwrap(),
        vec![packed.clone()]
    );
    let parts = session_parts_for(dir.path(), "SOLUSDT").unwrap();
    assert_eq!(parts.len(), 1);
    assert_eq!(parts[0].day_utc, "2026-09-08");
    assert_eq!(
        session_days_in_dir(dir.path()),
        ["2026-09-08".to_string()].into()
    );
}

/// Архив части 1 и обычная часть 2 одних суток: порядок по номеру части, а не
/// по расширению (иначе хвост суток читался бы раньше их начала).
#[test]
fn archive_of_part_one_sorts_before_plain_part_two() {
    let dir = day_dir("SOLUSDT", "2026-09-08");
    let p1 = orig_path(dir.path(), "SOLUSDT", "2026-09-08");
    let summary = run_archive(&args_of(&p1)).unwrap();
    std::fs::remove_file(&p1).unwrap();
    let p2 = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-08", 2);
    std::fs::copy(&summary.out, &p2).unwrap();
    let day2_plain = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-09", 1);
    std::fs::write(&day2_plain, b"stub").unwrap();
    let paths = crate::commands::lob::session_binlog_for(dir.path(), "SOLUSDT").unwrap();
    assert_eq!(
        paths,
        vec![summary.out, p2, day2_plain],
        "сутки D (часть 1 архивом, часть 2), затем D+1"
    );
}

/// `levels` на архиве даёт **тот же CSV побайтово**, что на оригинале:
/// критерий приёмки T46 «артефакты совпадают». Каталоги разные и в каждом
/// ровно один файл символа — так проверяется именно чтение архива, а не
/// склейка списка.
#[test]
fn levels_csv_is_byte_identical_on_archive_and_original() {
    use crate::commands::lob::{run_levels, H3Args, H3ModeArg, LevelsArgs};
    let dir = day_dir("SOLUSDT", "2026-09-08");
    let src = orig_path(dir.path(), "SOLUSDT", "2026-09-08");
    let packed = run_archive(&args_of(&src)).unwrap().out;

    let root_plain = tempfile::tempdir().unwrap();
    std::fs::copy(&src, root_plain.path().join(src.file_name().unwrap())).unwrap();
    let root_archive = tempfile::tempdir().unwrap();
    std::fs::copy(
        &packed,
        root_archive.path().join(packed.file_name().unwrap()),
    )
    .unwrap();

    let args = |root: &Path| LevelsArgs {
        root: root.to_path_buf(),
        symbol: "SOLUSDT".to_string(),
        h3: H3Args {
            h3_mode: H3ModeArg::Percentile,
            h3_lots: Some(5),
        },
        h3_k: None,
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        out: None,
    };
    let a = run_levels(&args(root_plain.path())).unwrap();
    let b = run_levels(&args(root_archive.path())).unwrap();
    assert_eq!(a.levels, b.levels, "уровней столько же");
    assert_eq!(
        std::fs::read(&a.out).unwrap(),
        std::fs::read(&b.out).unwrap(),
        "CSV уровней обязан совпасть побайтово"
    );
}

/// `verify` на архиве даёт ту же сводку и тот же вердикт, что на оригинале.
#[test]
fn verify_summary_and_marker_match_between_archive_and_original() {
    let dir = day_dir("SOLUSDT", "2026-09-08");
    let src = orig_path(dir.path(), "SOLUSDT", "2026-09-08");
    let packed = run_archive(&args_of(&src)).unwrap().out;

    let root_plain = tempfile::tempdir().unwrap();
    std::fs::copy(&src, root_plain.path().join(src.file_name().unwrap())).unwrap();
    let root_archive = tempfile::tempdir().unwrap();
    std::fs::copy(
        &packed,
        root_archive.path().join(packed.file_name().unwrap()),
    )
    .unwrap();

    let a = super::super::verify::verify_and_mark(root_plain.path(), root_plain.path(), "SOLUSDT")
        .unwrap();
    let b =
        super::super::verify::verify_and_mark(root_archive.path(), root_archive.path(), "SOLUSDT")
            .unwrap();
    assert_eq!(a.total, b.total, "сводка сверки обязана совпасть");
    assert_eq!(a.status, b.status);
    assert_eq!(
        std::fs::read_to_string(&a.marker).unwrap(),
        std::fs::read_to_string(&b.marker).unwrap()
    );
}

/// `binlog-stats` на архиве: все **счётчики** те же, что на оригинале
/// (записи, кадры, группы, типы событий, блочные сделки, `local_ts`), а
/// отличается только размер файла — он и есть смысл архива. Плюс строка о
/// том, что источник — контейнер.
#[test]
fn binlog_stats_counters_match_except_sizes() {
    use crate::commands::lob::binlog_stats::{run_binlog_stats, summary_lines, BinlogStatsArgs};
    let dir = day_dir("SOLUSDT", "2026-09-08");
    let src = orig_path(dir.path(), "SOLUSDT", "2026-09-08");
    let packed = run_archive(&args_of(&src)).unwrap().out;

    let stats_of = |path: &Path| {
        run_binlog_stats(&BinlogStatsArgs {
            path: path.to_path_buf(),
            reencode: false,
            rewrite_out: None,
        })
        .unwrap()
    };
    let a = stats_of(&src);
    let b = stats_of(&packed);
    assert_eq!(a.version, b.version);
    assert_eq!(a.records, b.records);
    assert_eq!(a.frames, b.frames);
    assert_eq!(a.groups, b.groups);
    assert_eq!(a.by_ev, b.by_ev);
    assert_eq!(a.block_trades, b.block_trades);
    assert_eq!(a.local_ts_differs, b.local_ts_differs);
    assert_eq!(a.min_records_in_frame, b.min_records_in_frame);
    assert_eq!(a.max_records_in_frame, b.max_records_in_frame);
    assert_eq!(a.archive_level, None);
    assert_eq!(b.archive_level, Some(archive::DEFAULT_LEVEL as u8));
    assert!(b.bytes_on_disk < a.bytes_on_disk, "архив меньше оригинала");

    // Строки отчёта: всё, кроме размера и объёма на запись, совпадает; у
    // архива добавляется строка про контейнер, и только она.
    let la = summary_lines(&src, &a);
    let lb = summary_lines(&packed, &b);
    let lb_counters: Vec<&String> = lb[1..]
        .iter()
        .filter(|l| !l.contains("контейнер архива"))
        .collect();
    let la_counters: Vec<&String> = la[1..].iter().collect();
    assert_eq!(la_counters, lb_counters, "счётчики — те же строки");
    assert!(lb.iter().any(|l| l.contains("контейнер архива")));
    assert!(!la.iter().any(|l| l.contains("контейнер архива")));
    assert_ne!(
        la[0], lb[0],
        "размер и имя источника — разные по определению"
    );
}

/// Настоящий суточный файл с несколькими кадрами и сделками архивируется и
/// читается: проверка на фикстуре `test_support` (снапшоты, дельты, сделки).
#[test]
fn multi_frame_day_round_trips_through_the_command() {
    let dir = tempfile::tempdir().unwrap();
    test_support::write_day(
        dir.path(),
        "NEARUSDT",
        "2026-09-10",
        &test_support::three_level_frames(),
    );
    let src = orig_path(dir.path(), "NEARUSDT", "2026-09-10");
    let summary = run_archive(&args_of(&src)).unwrap();
    assert!(summary.frames >= 2, "кадров {}", summary.frames);
    let stats = archive::read_stats(&summary.out).unwrap();
    assert_eq!(stats.frames, summary.frames);
    assert_eq!(stats.records, summary.records);
}

/// Символ для маркера берётся из имени файла, а не из каталога: `-p2.binlog`
/// и `-p2.binlog.zst` дают тот же символ (иначе `--delete-original` искал бы
/// маркер не там).
#[test]
fn symbol_is_taken_from_the_file_name() {
    assert_eq!(
        super::symbol_of(Path::new("/x/SOLUSDT-2026-09-08.binlog")).unwrap(),
        "SOLUSDT"
    );
    assert_eq!(
        super::symbol_of(Path::new("/x/SOLUSDT-2026-09-08-p2.binlog.zst")).unwrap(),
        "SOLUSDT"
    );
    assert!(super::symbol_of(Path::new("/x/SOLUSDT.binlog")).is_err());
    assert!(super::symbol_of(Path::new("/x/2026-09-08.binlog")).is_err());
}

/// A1/V8: часть, которую сессия ещё пишет, не архивируется. `session.json` с
/// `closed = false` называет её последней частью символа — `unlink` открытого
/// файла потерял бы всё, что допишется после, а отказ обязан случиться **до**
/// записи контейнера, а не после часа работы.
#[test]
fn part_the_session_is_still_writing_is_not_archived() {
    let dir = day_dir("SOLUSDT", "2026-09-08");
    let src = orig_path(dir.path(), "SOLUSDT", "2026-09-08");
    std::fs::write(
        dir.path().join("session.json"),
        "{\"started_utc\":\"2026-09-08T00:00:00Z\",\"start_hour_utc\":0,\
         \"instruments\":[\"SOLUSDT\"],\
         \"binlog_files\":[{\"symbol\":\"SOLUSDT\",\"part\":1,\
         \"started_utc\":\"2026-09-08T00:00:00Z\"}]}",
    )
    .unwrap();

    let err = run_archive(&args_of(&src)).unwrap_err().to_string();
    assert!(err.contains("не архивируется"), "{err}");
    assert!(
        !dir.path().join("SOLUSDT-2026-09-08.binlog.zst").exists(),
        "контейнер не создан: отказ раньше работы"
    );
    assert!(src.is_file(), "оригинал открытой части не тронут");
}

/// A1: закрытая сессия архивируется как раньше, и сутки, которых открытая
/// сессия не пишет, тоже — иначе правило «открытая часть» заблокировало бы
/// выгрузку вчерашних суток, ради чего сторож и заведён.
#[test]
fn closed_session_and_other_days_do_not_block_archiving() {
    let closed = day_dir("SOLUSDT", "2026-09-08");
    let src = orig_path(closed.path(), "SOLUSDT", "2026-09-08");
    std::fs::write(
        closed.path().join("session.json"),
        "{\"started_utc\":\"2026-09-08T00:00:00Z\",\"start_hour_utc\":0,\
         \"closed\":true,\"instruments\":[\"SOLUSDT\"],\
         \"binlog_files\":[{\"symbol\":\"SOLUSDT\",\"part\":1,\
         \"started_utc\":\"2026-09-08T00:00:00Z\"}]}",
    )
    .unwrap();
    run_archive(&args_of(&src)).expect("закрытая сессия — архив разрешён");

    // Вчерашние сутки при открытой сегодняшней: последняя часть символа —
    // другая (другой день), значит вчерашний файл не открыт.
    let yesterday = day_dir("SOLUSDT", "2026-09-08");
    let old = orig_path(yesterday.path(), "SOLUSDT", "2026-09-08");
    let today =
        crate::commands::record::day_file_path(yesterday.path(), "SOLUSDT", "2026-09-09", 1);
    std::fs::write(&today, "часть сегодняшних суток, содержимое не важно").unwrap();
    std::fs::write(
        yesterday.path().join("session.json"),
        "{\"started_utc\":\"2026-09-09T00:00:00Z\",\"start_hour_utc\":0,\
         \"instruments\":[\"SOLUSDT\"],\
         \"binlog_files\":[{\"symbol\":\"SOLUSDT\",\"part\":1,\
         \"started_utc\":\"2026-09-09T00:00:00Z\"}]}",
    )
    .unwrap();
    run_archive(&args_of(&old)).expect("вчерашние сутки не открыты сегодняшней сессией");
}

/// A1: `session.json` есть, но не разбирается — отказ (fail-closed): «не знаю,
/// что пишется сейчас» не даёт права удалять живой файл.
#[test]
fn unreadable_session_json_is_refused() {
    let dir = day_dir("SOLUSDT", "2026-09-08");
    let src = orig_path(dir.path(), "SOLUSDT", "2026-09-08");
    std::fs::write(dir.path().join("session.json"), "{не json").unwrap();

    let err = run_archive(&args_of(&src)).unwrap_err().to_string();
    assert!(err.contains("не разбирается"), "{err}");
    assert!(src.is_file());
}
