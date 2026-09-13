use super::*;
use crate::book::Side;
use crate::lob::levels::DeathKind;
use crate::lob::watch::write_ready_flag;

fn level_rec(traded_lots: i64, size_max: i64, death_ms: i64) -> LevelRecord {
    LevelRecord {
        side: Side::Bid,
        price_tick: 1000,
        birth_ms: 0,
        death_ms,
        lifetime_ms: death_ms,
        size_max,
        time_to_max_ms: 0,
        size_monotonic: true,
        repeat_count: 0,
        repriced: false,
        death: DeathKind::BelowFraction,
        traded_lots,
    }
}

/// Границы правила 70/20 берутся включительно, как в шаге 1.2:
/// ровно 70% — eaten, ровно 20% — pulled.
fn eaten_rec() -> LevelRecord {
    level_rec(70, 100, 5_000)
}

fn pulled_rec() -> LevelRecord {
    level_rec(20, 100, 5_000)
}

fn mixed_rec() -> LevelRecord {
    level_rec(50, 100, 5_000)
}

fn mid(ts_ms: i64, mid2x: i64) -> MidSample {
    MidSample {
        ts_ms,
        bid_tick: mid2x / 2,
        ask_tick: mid2x - mid2x / 2,
    }
}

/// Будущее на 10 с есть: база в 0, срез ровно на `0 + 10 000`.
fn mids_with_future() -> Vec<MidSample> {
    vec![mid(0, 20_000), mid(10_000, 20_200)]
}

/// Базы нет, значит нет и markout: единственный срез ровно в момент
/// смерти — не база по строгому неравенству Decision 11.
fn mids_without_future() -> Vec<MidSample> {
    vec![mid(5_000, 20_200)]
}

fn eligible_tally(symbol: &str, day: &str) -> DayTally {
    DayTally {
        symbol: symbol.to_string(),
        day_utc: day.to_string(),
        verify_test1_violations: 0,
        verify_basis_points: 50_000,
    }
}

fn write_flag(dir: &Path, symbol: &str, days: &[&str]) -> std::path::PathBuf {
    let path = dir.join("ready.flag");
    let flag = ReadyFlag {
        symbol: symbol.to_string(),
        n: 100,
        g: days.len() as u64,
        ready_at_utc: "2026-01-01T00:00:00Z".to_string(),
        days: days.iter().map(|s| s.to_string()).collect(),
    };
    write_ready_flag(&path, &flag).expect("флаг пишется раз");
    path
}

#[test]
fn distribution_prints_counts_and_shares() {
    let mut recs = Vec::new();
    recs.extend(std::iter::repeat_with(eaten_rec).take(2));
    recs.extend(std::iter::repeat_with(pulled_rec).take(16));
    recs.extend(std::iter::repeat_with(mixed_rec).take(2));
    assert_eq!(eaten_rec().outcome(), Outcome::Eaten);
    assert_eq!(pulled_rec().outcome(), Outcome::Pulled);
    assert_eq!(mixed_rec().outcome(), Outcome::Mixed);
    let counts = ClassCounts::from_records(&recs);
    assert_eq!(
        counts,
        ClassCounts {
            total: 20,
            eaten: 2,
            pulled: 16,
            mixed: 2,
        }
    );
    assert_eq!(counts.eaten_share_ppm(), Some(100_000));
    assert_eq!(counts.pulled_share_ppm(), Some(800_000));
    assert_eq!(counts.mixed_share_ppm(), Some(100_000));
    let line = counts.format_distribution();
    assert!(line.contains("total=20"), "напечатано: {line}");
    assert!(line.contains("eaten=2"), "напечатано: {line}");
    assert!(line.contains("pulled=16"), "напечатано: {line}");
    assert!(line.contains("mixed=2"), "напечатано: {line}");
    assert_eq!(format!("{counts}"), line);
}

#[test]
fn g1_passes_on_the_exact_five_percent_boundary() {
    let mut recs = Vec::new();
    recs.push(eaten_rec());
    recs.push(pulled_rec());
    recs.extend(std::iter::repeat_with(mixed_rec).take(18));
    let counts = ClassCounts::from_records(&recs);
    assert_eq!(counts.total, 20);
    assert_eq!(decide_g1(&counts), G1Verdict::Pass);
    assert!(decide_g1(&counts).is_pass());
}

#[test]
fn g1_is_red_when_either_share_is_below_five_percent() {
    let mut low_eaten = vec![pulled_rec(); 50];
    low_eaten.extend(std::iter::repeat_with(eaten_rec).take(4));
    low_eaten.extend(std::iter::repeat_with(mixed_rec).take(46));
    let counts = ClassCounts::from_records(&low_eaten);
    assert_eq!(counts.total, 100);
    assert_eq!(decide_g1(&counts), G1Verdict::RedMethodology);

    let mut low_pulled = vec![eaten_rec(); 50];
    low_pulled.extend(std::iter::repeat_with(pulled_rec).take(4));
    low_pulled.extend(std::iter::repeat_with(mixed_rec).take(46));
    let counts = ClassCounts::from_records(&low_pulled);
    assert_eq!(decide_g1(&counts), G1Verdict::RedMethodology);
    assert!(!decide_g1(&counts).is_pass());
}

#[test]
fn g1_pulled_above_ninety_percent_does_not_close() {
    let mut recs = Vec::new();
    recs.extend(std::iter::repeat_with(pulled_rec).take(92));
    recs.extend(std::iter::repeat_with(eaten_rec).take(5));
    recs.extend(std::iter::repeat_with(mixed_rec).take(3));
    let counts = ClassCounts::from_records(&recs);
    assert!(pulled_dominant(&counts), "92% — ожидаемый мир снятия");
    assert_eq!(
        decide_g1(&counts),
        G1Verdict::Pass,
        "высокое pulled само по себе не красный"
    );
    assert!(!pulled_dominant(&ClassCounts::default()));
}

#[test]
fn g1_empty_sample_is_red_and_has_no_shares() {
    let counts = ClassCounts::from_records(&[]);
    assert_eq!(counts.total, 0);
    assert_eq!(counts.eaten_share_ppm(), None);
    assert_eq!(counts.pulled_share_ppm(), None);
    assert_eq!(decide_g1(&counts), G1Verdict::RedMethodology);
}

#[test]
fn confirmatory_without_flag_is_refusal() {
    let dir = tempfile::tempdir().expect("песочница");
    let missing = dir.path().join("ready.flag");
    let tally = eligible_tally("TST", "2026-01-01");
    let recs = vec![pulled_rec()];
    let mids = mids_with_future();
    let days = [ConfirmatoryDay {
        tally: &tally,
        records: &recs,
        mids: &mids,
    }];
    let err = run_confirmatory(&missing, &days).expect_err("без флага — отказ");
    assert!(
        matches!(err, MarkupError::Flag(WatchError::MissingFlag { .. })),
        "отказ тем же вариантом, что require_ready_flag: {err}"
    );

    std::fs::write(&missing, "мусор без знака равенства\n").expect("мусор пишется");
    let err = run_confirmatory(&missing, &days).expect_err("порча флага — отказ");
    assert!(
        matches!(err, MarkupError::Flag(WatchError::BadFlag { .. })),
        "порча флага — отказ: {err}"
    );
}

#[test]
fn confirmatory_uses_exactly_the_flag_days() {
    let dir = tempfile::tempdir().expect("песочница");
    let flag_path = write_flag(dir.path(), "TST", &["2026-01-01", "2026-01-02"]);
    let t1 = eligible_tally("TST", "2026-01-01");
    let t2 = eligible_tally("TST", "2026-01-02");
    let t3 = eligible_tally("TST", "2026-01-03");
    let r_pulled = vec![pulled_rec()];
    let r_eaten = vec![eaten_rec(); 3];
    let mids = mids_with_future();
    // Сутки после флага — отложенная выборка: молча не входят.
    let days = [
        ConfirmatoryDay {
            tally: &t1,
            records: &r_pulled,
            mids: &mids,
        },
        ConfirmatoryDay {
            tally: &t2,
            records: &r_pulled,
            mids: &mids,
        },
        ConfirmatoryDay {
            tally: &t3,
            records: &r_eaten,
            mids: &mids,
        },
    ];
    let report = run_confirmatory(&flag_path, &days).expect("флаг есть");
    assert_eq!(report.days_used, vec!["2026-01-01", "2026-01-02"]);
    assert_eq!(report.counts.pulled, 2);
    assert_eq!(report.counts.eaten, 0, "отложенные сутки не вошли");

    // Неполная выборка — ошибка, а не тихий недобор.
    let short = [
        ConfirmatoryDay {
            tally: &t1,
            records: &r_pulled,
            mids: &mids,
        },
        ConfirmatoryDay {
            tally: &t3,
            records: &r_eaten,
            mids: &mids,
        },
    ];
    let err = run_confirmatory(&flag_path, &short).expect_err("суток флага нет");
    assert_eq!(
        err,
        MarkupError::MissingDay {
            day: "2026-01-02".to_string(),
        }
    );
}

#[test]
fn confirmatory_rejects_bad_days() {
    let dir = tempfile::tempdir().expect("песочница");
    let flag_path = write_flag(dir.path(), "TST", &["2026-01-01"]);
    let recs = vec![pulled_rec()];
    let mids = mids_with_future();

    let mut not_eligible = eligible_tally("TST", "2026-01-01");
    not_eligible.verify_basis_points = 0;
    let days = [ConfirmatoryDay {
        tally: &not_eligible,
        records: &recs,
        mids: &mids,
    }];
    assert_eq!(
        run_confirmatory(&flag_path, &days).expect_err("ноль проверок verify — сутки не годны"),
        MarkupError::DayNotEligible {
            day: "2026-01-01".to_string(),
        }
    );

    let foreign = eligible_tally("OTHER", "2026-01-01");
    let foreign_tally = DayTally {
        day_utc: "2026-01-01".to_string(),
        ..foreign
    };
    let days = [ConfirmatoryDay {
        tally: &foreign_tally,
        records: &recs,
        mids: &mids,
    }];
    assert!(
        matches!(
            run_confirmatory(&flag_path, &days),
            Err(MarkupError::SymbolMismatch { .. })
        ),
        "чужой символ отвергается"
    );

    let tally = eligible_tally("TST", "2026-01-01");
    let dup = [
        ConfirmatoryDay {
            tally: &tally,
            records: &recs,
            mids: &mids,
        },
        ConfirmatoryDay {
            tally: &tally,
            records: &recs,
            mids: &mids,
        },
    ];
    assert_eq!(
        run_confirmatory(&flag_path, &dup).expect_err("дубликат"),
        MarkupError::DuplicateDay {
            day: "2026-01-01".to_string(),
        }
    );
}

#[test]
fn confirmatory_runs_levels_outcome_markout_and_decides_g1() {
    assert_eq!(HORIZONS_MS[2], 10_000, "индекс обязан смотреть на 10 с");
    let dir = tempfile::tempdir().expect("песочница");
    let flag_path = write_flag(dir.path(), "TST", &["2026-01-01"]);
    let tally = eligible_tally("TST", "2026-01-01");
    // Два pulled и два eaten: обе доли сильно выше 5% — проход.
    let recs = vec![pulled_rec(), pulled_rec(), eaten_rec(), eaten_rec()];
    // У первой пары будущее есть, у второй — нет: покрытие считается,
    // а вердикт от него не зависит.
    let with_future = mids_with_future();
    let report = run_confirmatory(
        &flag_path,
        &[ConfirmatoryDay {
            tally: &tally,
            records: &recs[..2],
            mids: &with_future,
        }],
    )
    .expect("прогон");
    assert_eq!(report.markout_10s_available, 2);
    assert_eq!(report.markout_10s_missing, 0);
    assert_eq!(report.verdict, G1Verdict::RedMethodology);
    assert!(
        report.summary_line().contains("RED"),
        "печать: {}",
        report.summary_line()
    );

    let without_future = mids_without_future();
    let report = run_confirmatory(
        &flag_path,
        &[ConfirmatoryDay {
            tally: &tally,
            records: &recs,
            mids: &without_future,
        }],
    )
    .expect("прогон");
    assert_eq!(report.markout_10s_available, 0);
    assert_eq!(report.markout_10s_missing, 4);
    assert_eq!(report.verdict, G1Verdict::Pass);
    let line = report.summary_line();
    assert!(line.contains("total=4"), "печать: {line}");
    assert!(line.contains("pass"), "печать: {line}");
}

#[test]
fn binlog_drain_round_trips_synthetic_frames() {
    use crate::binlog::{Header, Reader, Writer};
    let header = Header {
        tick_e9: 1,
        step_e9: 1,
        max_records_per_frame: 8,
    };
    let mut buf: Vec<u8> = Vec::new();
    {
        let mut w = Writer::create(&mut buf, header, 1).expect("заголовок годен");
        let recs: Vec<Record> = (0..3)
            .map(|i| Record {
                ev: 1,
                exch_ts_ns: 1_000 + i,
                local_ts_ns: 2_000 + i,
                price_ticks: 100 + i,
                qty_lots: 10 + i,
                block: false,
            })
            .collect();
        w.write_frame(&recs).expect("кадр пишется");
        w.write_frame(&recs[..1]).expect("второй кадр пишется");
        w.flush().expect("сброс");
    }
    let mut reader = Reader::open(buf.as_slice()).expect("заголовок читается");
    let got = drain_binlog_records(&mut reader).expect("слив");
    assert_eq!(got.len(), 4, "три записи плюс одна");
    assert_eq!(got[0].price_ticks, 100);
    assert_eq!(got[3].price_ticks, 100, "второй кадр — те же записи");
    assert!(!got[3].block, "блочность читается из `attrs` группы");
}

/// Граница модулей в духе шагов 1.1/4.1: разметка не знает про транспорт
/// и часы, целые не размениваются на приближённые числа. Проверка —
/// грепом по собственному исходнику. Доли считаются целыми ppm, поэтому
/// вещественных чисел здесь нет, как в `watch.rs`.
///
/// Запрещённые фрагменты собраны из частей: литерал целиком триггерил бы
/// эту же проверку сам на себя.
#[test]
fn module_stays_detached_from_transport_clocks_and_approx_numbers() {
    const SRC: &str = include_str!("../markup.rs");
    let banned = [
        concat!("by", "bit"),
        concat!("tok", "io"),
        concat!("Inst", "ant"),
        concat!("System", "Time"),
        concat!("f", "64"),
        concat!("std::", "time"),
    ];
    for b in banned {
        assert!(!SRC.contains(b), "исходник тянет запрещённое: {b}");
    }
}
