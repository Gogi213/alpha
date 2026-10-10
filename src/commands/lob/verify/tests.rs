use super::*;
use crate::commands::lob::test_support::{snap_frame, three_level_frames, write_day_part};

/// Критерий приёмки таска 26: каталог из фикстуры → `lob verify` →
/// `verify-<S>.status` ровно `ok` (без перевода строки — читатель
/// `profiles.rs` сравнивает `trim() == "ok"`, `watch.rs` — так же).
/// Ожидание — из фикстуры: `three_level_frames` не пересекает книгу
/// и торгует только по удерживаемым тикам (doc `test_support`).
#[test]
fn verify_writes_ok_marker_for_a_clean_session_directory() {
    let dir = tempfile::tempdir().unwrap();
    write_day_part(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        1,
        &three_level_frames(),
    );

    let report =
        verify_and_mark(dir.path(), dir.path(), "SOLUSDT").expect("фикстура обязана сверяться");

    assert_eq!(report.status, VerifyStatus::Ok);
    assert_eq!(report.parts.len(), 1);
    assert_eq!(report.parts[0].day_utc, "2026-09-08");
    assert_eq!(report.total.files, 1);
    let marker = std::fs::read_to_string(dir.path().join("verify-SOLUSDT.status")).unwrap();
    assert_eq!(marker, "ok");
}

/// Критерий приёмки таска 26: части за несколько суток — сверка по
/// всем частям символа (одна запись отчёта на часть с её сутками), и
/// одна битая часть (пересечённая книга в снапшоте: бид 105 ≥ аск 100 —
/// инвариант шага 0.6) роняет маркер всего каталога в `fail`.
#[test]
fn verify_reports_every_part_with_its_day_and_a_crossed_part_fails_the_marker() {
    let dir = tempfile::tempdir().unwrap();
    write_day_part(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        1,
        &three_level_frames(),
    );
    write_day_part(
        dir.path(),
        "SOLUSDT",
        "2026-09-09",
        1,
        &[snap_frame(0, &[(105, 10)], &[(100, 10)])],
    );

    let report = verify_and_mark(dir.path(), dir.path(), "SOLUSDT")
        .expect("битая часть — вердикт, не ошибка чтения");

    let days: Vec<&str> = report.parts.iter().map(|p| p.day_utc.as_str()).collect();
    assert_eq!(
        days,
        ["2026-09-08", "2026-09-09"],
        "по части на сутки, по порядку"
    );
    assert_eq!(VerifyStatus::of(&report.parts[0].summary), VerifyStatus::Ok);
    assert_eq!(
        VerifyStatus::of(&report.parts[1].summary),
        VerifyStatus::Fail
    );
    assert_eq!(report.total.files, 2);
    assert_eq!(report.status, VerifyStatus::Fail);
    let marker = std::fs::read_to_string(dir.path().join("verify-SOLUSDT.status")).unwrap();
    assert_eq!(marker, "fail");
}

/// Значение колонки `name` в строке `profile_id == id` таблицы
/// `profiles-<дата>.csv` (шапка `#` пропускается).
fn profiles_cell(text: &str, id: &str, name: &str) -> String {
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_reader(text.as_bytes());
    let idx = r
        .headers()
        .unwrap()
        .iter()
        .position(|h| h == name)
        .unwrap_or_else(|| panic!("колонки {name} нет"));
    r.records()
        .map(|row| row.unwrap())
        .find(|row| row.get(0) == Some(id))
        .unwrap_or_else(|| panic!("строки {id} нет:\n{text}"))
        .get(idx)
        .unwrap()
        .to_string()
}

/// В-56: тест 3 — это **доля** сделок, а не счётчик. Порог строгий:
/// ровно 0.1 % (10 из 10 000) — уже `fail`, 0.09 % — `ok`.
#[test]
fn trades_rule_is_a_share_and_the_limit_itself_fails() {
    let at_limit = VerifySummary {
        trades_total: 10_000,
        trades_violations: 10,
        ..Default::default()
    };
    assert_eq!(
        VerifyStatus::of(&at_limit),
        VerifyStatus::Fail,
        "ровно 0.1 % — это порог, а не «внутри порога»"
    );

    let below = VerifySummary {
        trades_total: 10_000,
        trades_violations: 9,
        ..Default::default()
    };
    assert_eq!(
        VerifyStatus::of(&below),
        VerifyStatus::Ok,
        "0.09 % — внутри"
    );

    // Боевые числа 09-15 из §12e: ASTR (1 из 76 524) и FIL (2 из 130 251)
    // прошли бы, BTW (749 из 48 797 = 1.535 %) — нет.
    let astr = VerifySummary {
        trades_total: 76_524,
        trades_violations: 1,
        ..Default::default()
    };
    assert_eq!(VerifyStatus::of(&astr), VerifyStatus::Ok);
    let fil = VerifySummary {
        trades_total: 130_251,
        trades_violations: 2,
        ..Default::default()
    };
    assert_eq!(
        VerifyStatus::of(&fil),
        VerifyStatus::Ok,
        "0.0015 % — внутри"
    );
    let btw = VerifySummary {
        trades_total: 48_797,
        trades_violations: 749,
        ..Default::default()
    };
    assert_eq!(VerifyStatus::of(&btw), VerifyStatus::Fail);
}

/// Сделок не было — нарушать нечего: тест 3 пройден (иначе каталог без
/// ленты не читался бы вовсе).
#[test]
fn trades_rule_passes_when_there_were_no_trades() {
    let no_trades = VerifySummary {
        trades_total: 0,
        trades_violations: 0,
        ..Default::default()
    };
    assert_eq!(VerifyStatus::of(&no_trades), VerifyStatus::Ok);
}

/// Целостность остаётся ровно нулевой и порогом не размывается: и разрыв
/// `u`, и поломка книги роняют вердикт даже при нулевых нарушениях теста 3.
#[test]
fn integrity_counters_stay_strict_zero() {
    let gap = VerifySummary {
        trades_total: 1_000_000,
        sequence_gaps: 1,
        ..Default::default()
    };
    assert_eq!(VerifyStatus::of(&gap), VerifyStatus::Fail, "разрыв `u`");

    let crossed = VerifySummary {
        trades_total: 1_000_000,
        invariant_violations: 1,
        ..Default::default()
    };
    assert_eq!(
        VerifyStatus::of(&crossed),
        VerifyStatus::Fail,
        "инвариант книги"
    );
}

/// V4 → В-59 (2026-09-17): разрыв `u` в бинлоге не виден — поля `u` там нет,
/// — поэтому вердикт обязан смотреть `gaps.csv` того же каталога, но **по
/// смыслу**. Шов покрытия (`sequence_gap` — переподключение) считается и
/// сутки не роняет: по обе стороны шва запись честная. Потеря целостности
/// (`write_failed`) роняет: дальше по файлу книга не та. Строка чужого
/// символа вердикт не трогает: маркер — про этот символ.
#[test]
fn a_seam_row_keeps_ok_a_loss_row_fails_the_marker() {
    use crate::commands::record::{append_gap_row, GapKind, GapRow};

    let row = |symbol: &str, kind: GapKind, detail: &str| GapRow {
        ts_utc: "2026-09-08T10:00:00Z".to_string(),
        symbol: symbol.to_string(),
        kind,
        detail: detail.to_string(),
    };

    let dir = tempfile::tempdir().unwrap();
    write_day_part(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        1,
        &three_level_frames(),
    );
    let gaps = dir.path().join("gaps.csv");
    append_gap_row(
        &gaps,
        &row("XRPUSDT", GapKind::WriteFailed, "потеря другого символа"),
    )
    .unwrap();
    let report = verify_and_mark(dir.path(), dir.path(), "SOLUSDT").unwrap();
    assert_eq!(report.gaps, GapTally::default());
    assert_eq!(report.status, VerifyStatus::Ok, "чужая потеря не моя");

    append_gap_row(
        &gaps,
        &row(
            "SOLUSDT",
            GapKind::SequenceGap,
            "транспорт переподключился — шов покрытия",
        ),
    )
    .unwrap();
    let report = verify_and_mark(dir.path(), dir.path(), "SOLUSDT").unwrap();
    assert_eq!((report.gaps.seams, report.gaps.losses), (1, 0));
    assert_eq!(
        report.status,
        VerifyStatus::Ok,
        "шов покрытия сутки не роняет"
    );
    assert_eq!(
        std::fs::read_to_string(dir.path().join("verify-SOLUSDT.status")).unwrap(),
        "ok"
    );

    append_gap_row(
        &gaps,
        &row("SOLUSDT", GapKind::WriteFailed, "кадр не записался"),
    )
    .unwrap();
    let report = verify_and_mark(dir.path(), dir.path(), "SOLUSDT").unwrap();
    assert_eq!((report.gaps.seams, report.gaps.losses), (1, 1));
    assert_eq!(report.gaps.rows(), 2);
    assert_eq!(
        report.status,
        VerifyStatus::Fail,
        "потеря целостности — не ok"
    );
    assert_eq!(
        std::fs::read_to_string(dir.path().join("verify-SOLUSDT.status")).unwrap(),
        "fail"
    );
}

/// В-59: потеря целостности роняет вердикт даже при идеальной сводке; швы и
/// смена шага — нет; без строк решает сводка — порог В-56 остаётся в силе.
#[test]
fn losses_override_a_clean_summary_seams_do_not() {
    let clean = VerifySummary {
        trades_total: 1_000,
        ..Default::default()
    };
    let seams = GapTally {
        seams: 3,
        losses: 0,
        step_changes: 1,
    };
    let loss = GapTally {
        seams: 0,
        losses: 1,
        step_changes: 0,
    };
    assert_eq!(
        VerifyStatus::of_with_gaps(&clean, GapTally::default()),
        VerifyStatus::Ok
    );
    assert_eq!(
        VerifyStatus::of_with_gaps(&clean, seams),
        VerifyStatus::Ok,
        "швы и смена шага — не брак"
    );
    assert_eq!(
        VerifyStatus::of_with_gaps(&clean, loss),
        VerifyStatus::Fail,
        "одна потеря целостности важнее доли нарушений"
    );
}

/// В-59: раскладка строк журнала по смыслу — каждая причина ровно в одной
/// корзине; переименование варианта без правки здесь провалит этот тест.
#[test]
fn gap_tally_sorts_every_kind_into_one_bucket() {
    use crate::commands::record::{GapKind, GapRow};

    let row = |kind: GapKind| GapRow {
        ts_utc: "2026-09-17T01:51:19Z".to_string(),
        symbol: "HYPEUSDT".to_string(),
        kind,
        detail: String::new(),
    };
    let rows = [
        row(GapKind::StepChange),
        row(GapKind::SequenceGap),
        row(GapKind::BookInvariant),
        row(GapKind::ParseError),
        row(GapKind::WriteFailed),
        row(GapKind::ConnectFailed),
        // A8.3: отказ подписки — шов покрытия, как `connect_failed`.
        row(GapKind::SubscribeFailed),
    ];
    let tally = GapTally::of_rows(rows.iter());
    assert_eq!(
        tally,
        GapTally {
            seams: 4,
            losses: 2,
            step_changes: 1,
        }
    );
    assert_eq!(tally.rows(), rows.len());
}

/// A4 (2026-09-17): `lob verify` читает и живой корень — обрезанный хвостовой
/// кадр (коллектор дописывает файл прямо сейчас) не роняет команду, а
/// становится концом прочитанного; посчитанное при этом остаётся посчитанным.
#[test]
fn verify_tolerates_a_truncated_tail_frame() {
    let dir = tempfile::tempdir().unwrap();
    write_day_part(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        1,
        &three_level_frames(),
    );
    let path = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-08", 1);
    let mut bytes = std::fs::read(&path).unwrap();
    // Обрыв внутри последнего кадра: префикс длины обещает больше байт, чем
    // осталось в файле, — ровно то, что видит читатель живого файла.
    bytes.truncate(bytes.len() - 3);
    std::fs::write(&path, &bytes).unwrap();

    let report = verify_and_mark(dir.path(), dir.path(), "SOLUSDT")
        .expect("обрезанный хвост — это «файл дописывается», а не отказ");
    assert_eq!(report.status, VerifyStatus::Ok);
    assert!(
        report.total.files > 0,
        "прочитанное обязано быть посчитано, а не выброшено"
    );
}

/// TK-034: нарушение в начале суток. Без флага файл обрывается на нём
/// (прежнее поведение), с `--keep-going` — идёт дальше, считает вид и время
/// в битом состоянии, а следующий снапшот возвращает чистую книгу.
#[test]
fn keep_going_continues_past_an_early_crossed_book_and_counts_the_kind() {
    use crate::commands::lob::test_support::delta_frame;
    let dir = tempfile::tempdir().unwrap();
    write_day_part(
        dir.path(),
        "SOLUSDT",
        "2026-09-08",
        1,
        &[
            snap_frame(0, &[(98, 10), (100, 10)], &[(105, 10)]),
            delta_frame(1000, &[(106, 5)], &[]),
            delta_frame(2000, &[(106, 0)], &[]),
            delta_frame(3000, &[(99, 4)], &[]),
            snap_frame(4000, &[(98, 10)], &[(105, 10)]),
            delta_frame(5000, &[(97, 1)], &[]),
        ],
    );
    let path = crate::commands::record::day_file_path(dir.path(), "SOLUSDT", "2026-09-08", 1);

    let plain = verify_file(&path).unwrap();
    assert_eq!(plain.invariant_violations, 1);
    assert_eq!(plain.updates_applied, 1, "прежний режим встаёт на ошибке");

    let (summary, report) = verify_file_keep_going(&path).unwrap();
    assert_eq!(summary.updates_applied, 5, "всё, кроме упавшего обновления");
    assert_eq!(report.updates, 6);
    assert_eq!(report.counts[0], 1, "одно пересечение книги: {report:?}");
    assert_eq!(report.broken_updates, 1);
    assert_eq!(report.first_broken_ms, Some(1000));
    assert_eq!(report.broken_ms, 1000);
    assert_eq!(report.broken_share_ppm(), Some(200_000));
    assert_eq!(report.examples.len(), 1);
    assert_eq!(report.examples[0].update_no, 2);
    assert_eq!(report.examples[0].exch_ms, 1000);
}
