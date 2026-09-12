use super::*;
use std::cell::Cell;

// ---- MonotonicClock -------------------------------------------------

/// Требуемое свойство (таск 15): последовательные метки одного экземпляра
/// не убывают — то, что откат `SystemClock` при коррекции NTP не может
/// гарантировать (см. doc `MonotonicClock`), и то, что стадии `lob react`
/// обязаны получить, иначе длительность стадии может выйти отрицательной.
#[test]
fn monotonic_clock_never_goes_backwards_across_many_reads() {
    let clock = MonotonicClock::start();
    let mut prev = clock.now_ns();
    for _ in 0..10_000 {
        let now = clock.now_ns();
        assert!(now >= prev, "метка обязана не убывать: {now} < {prev}");
        prev = now;
    }
}

/// Метка растёт вместе с настоящим временем (не заморожена, не случайна):
/// сон между двумя чтениями обязан дать положительную разницу порядка
/// длины сна, а не ноль и не что попало.
#[test]
fn monotonic_clock_advances_by_roughly_the_elapsed_sleep() {
    let clock = MonotonicClock::start();
    let before = clock.now_ns();
    std::thread::sleep(Duration::from_millis(20));
    let after = clock.now_ns();
    let delta_ns = after - before;
    assert!(
        delta_ns >= 10_000_000,
        "20мс сна обязаны отразиться в метке, получили {delta_ns}нс"
    );
}
use std::collections::VecDeque;

fn rt(local_send_ns: i64, remote_ns: i64, local_recv_ns: i64) -> RoundTrip {
    RoundTrip {
        local_send_ns,
        remote_ns,
        local_recv_ns,
    }
}

// ---- estimate_offset ----------------------------------------------

/// Требуемый тест: оценка корректирует сетевую задержку. `send=1000,
/// remote=1150, recv=1200` (RTT=200) даёт середину 1100 и скорректированное
/// смещение `50`. Обе наивные альтернативы — `remote - recv = -50` и
/// `remote - send = 150` — не совпадают со скорректированной оценкой и
/// друг с другом: это и есть расхождение, которое требует зафиксировать
/// задание, и корректная оценка — та, что пиннуется тестом.
#[test]
fn estimate_offset_corrects_for_round_trip_delay_disagreeing_with_naive_differences() {
    let round_trip = rt(1_000, 1_150, 1_200);

    let corrected = estimate_offset(&round_trip).unwrap();

    let naive_from_recv = round_trip.remote_ns - round_trip.local_recv_ns;
    let naive_from_send = round_trip.remote_ns - round_trip.local_send_ns;
    assert_eq!(
        corrected.offset_ns, 50,
        "скорректированная оценка пиннуется"
    );
    assert_eq!(corrected.rtt_ns, 200);
    assert_ne!(corrected.offset_ns as i128, naive_from_recv as i128);
    assert_ne!(corrected.offset_ns as i128, naive_from_send as i128);
}

/// Вырожденный вход: эталон сообщил время, которое уже наступило после
/// того, как локальные часы получили ответ (`remote_ns > local_recv_ns`).
/// Физически это означало бы отрицательную задержку в один конец, но
/// формула не делает такого допущения нигде — она обязана посчитать
/// число и не отказать, потому что источник большого положительного
/// смещения (часы хоста сильно отстают) выглядит снаружи ровно так же.
#[test]
fn estimate_offset_handles_a_remote_timestamp_ahead_of_local_receipt() {
    let round_trip = rt(1_000, 5_000, 1_200);

    let sample = estimate_offset(&round_trip).unwrap();

    assert_eq!(sample.rtt_ns, 200);
    assert_eq!(
        sample.offset_ns,
        5_000 - 1_100,
        "середина = 1000 + 200/2 = 1100"
    );
}

/// Вырожденный вход: получение раньше отправки по локальным часам —
/// сами часы шагнули назад между двумя своими же чтениями внутри
/// одного раунда. `Err`, а не число, посчитанное на противоречии.
#[test]
fn estimate_offset_rejects_a_round_trip_where_receipt_precedes_send() {
    let round_trip = rt(1_000, 1_000, 900);

    let err = estimate_offset(&round_trip).unwrap_err();

    assert_eq!(
        err,
        ClockError::NonMonotonicRoundTrip {
            local_send_ns: 1_000,
            local_recv_ns: 900,
        }
    );
}

/// Вырожденный вход: метки на границах `i64`. Обычное `i64`-вычитание
/// здесь либо паникует, либо заворачивается в противоположный знак —
/// оба исхода недопустимы (см. doc `estimate_offset`). Ошибка, не паника.
#[test]
fn estimate_offset_reports_overflow_instead_of_wrapping_on_i64_extremes() {
    let round_trip = rt(i64::MAX, i64::MIN, i64::MAX);

    let err = estimate_offset(&round_trip).unwrap_err();

    assert_eq!(err, ClockError::Overflow);
}

/// Свойство, не разовое число: аллокаций нет ни на одном вызове, и их
/// не прибавляется с числом вызовов — если бы `estimate_offset` где-то
/// незаметно завела `Vec`/`String` на горячем (для этого модуля —
/// часовом) пути, тысяча вызовов показала бы рост, а один — нет.
#[test]
fn estimate_offset_allocates_nothing_regardless_of_call_count() {
    let round_trip = rt(1_000, 1_150, 1_200);
    let (_, once) = crate::alloc_count::measure(|| estimate_offset(&round_trip).unwrap());
    let (_, thousand) = crate::alloc_count::measure(|| {
        for _ in 0..1_000 {
            estimate_offset(&round_trip).unwrap();
        }
    });
    assert_eq!(once.allocations, 0);
    assert_eq!(
        thousand.allocations, 0,
        "аллокации не должны расти с числом вызовов"
    );
}

// ---- check_series / check_rows -------------------------------------

fn points(offsets: &[i64]) -> Vec<SeriesPoint> {
    offsets
        .iter()
        .enumerate()
        .map(|(i, &offset_ns)| SeriesPoint {
            sample_index: i as u64,
            offset_ns: Some(offset_ns),
        })
        .collect()
}

/// Требуемый тест: стабильное смещение внутри порога, без скачков —
/// ноль нарушений.
#[test]
fn a_steady_offset_inside_the_threshold_passes() {
    let series = points(&[2_000_000, 2_100_000, 1_900_000, 2_050_000]);
    assert_eq!(check_series("ntp", &series), Vec::new());
}

/// Требуемый тест: один скачок > 1 мс между соседними замерами
/// обнаруживается, и строка, вызвавшая его, узнаваема — `from_index`/
/// `to_index` указывают именно на пару (2, 3), а не на весь ряд.
#[test]
fn a_single_jump_above_the_threshold_is_detected_and_the_offending_pair_is_identifiable() {
    let series = points(&[1_000_000, 1_000_000, 1_000_000, 3_000_000]);

    let violations = check_series("ntp", &series);

    assert_eq!(
        violations,
        vec![Violation::Jump {
            source: "ntp",
            from_index: 2,
            to_index: 3,
            delta_ns: 2_000_000,
        }]
    );
}

/// Требуемый тест: смещение медленно дрейфует за 5 мс маленькими шагами
/// (каждый ≤ 1 мс, скачков нет) — нарушение обязано всплыть на первой
/// точке, где `|offset| >= 5 мс` (индекс 4, значение 5.1 мс), а не на
/// следующей (индекс 5, 5.4 мс). «Не на шаг позже» — то, что фиксирует
/// `first_violation_index == 4` ниже.
#[test]
fn a_slow_drift_past_the_threshold_is_caught_at_the_crossing_sample_not_one_late() {
    let series = points(&[
        4_000_000, 4_300_000, 4_600_000, 4_900_000, 5_100_000, 5_400_000,
    ]);

    let violations = check_series("ntp", &series);

    assert!(
        violations
            .iter()
            .all(|v| matches!(v, Violation::OffsetOutOfBounds { .. })),
        "шаги по 300 мкс — скачков быть не должно: {violations:?}"
    );
    let first_violation_index = violations
        .iter()
        .filter_map(|v| match v {
            Violation::OffsetOutOfBounds { sample_index, .. } => Some(*sample_index),
            Violation::Jump { .. } => None,
        })
        .min()
        .expect("нарушение обязано найтись");
    assert_eq!(
        first_violation_index, 4,
        "не на шаг позже пересечения порога"
    );
}

/// Провал раунда источника (`offset_ns = None`) не создаёт ложный скачок
/// «к нулю и обратно»: пропуск исключается из проверки, и соседними для
/// скачка остаются две успешные точки по разные стороны от него.
#[test]
fn a_missing_sample_does_not_create_a_false_jump_around_the_gap() {
    let series = vec![
        SeriesPoint {
            sample_index: 0,
            offset_ns: Some(1_000_000),
        },
        SeriesPoint {
            sample_index: 1,
            offset_ns: None,
        },
        SeriesPoint {
            sample_index: 2,
            offset_ns: Some(1_000_500),
        },
    ];

    let violations = check_series("ntp", &series);

    assert_eq!(
        violations,
        Vec::new(),
        "0→2 отличаются на 500 нс, пропуск ни при чём"
    );
}

/// Вырожденный вход: пустая серия — ни одного замера ещё не снято.
/// Отсутствие нарушений здесь не значит "дисциплина подтверждена",
/// это значит "нечего проверять" — вызывающий код (гейт GC) обязан
/// требовать ненулевую серию отдельно, эта функция — не источник такой
/// гарантии, и не должна притворяться им, падая или выдумывая нарушение.
#[test]
fn zero_samples_produce_no_violations_and_no_panic() {
    assert_eq!(check_series("ntp", &[]), Vec::new());
    assert_eq!(check_rows(&[]), Vec::new());
}

/// Вырожденный вход: ровно один замер — порог проверяется, скачок не
/// определён по построению (не с чем сравнивать) и не проверяется.
#[test]
fn a_single_sample_checks_the_threshold_but_cannot_check_for_a_jump() {
    let inside = points(&[1_000_000]);
    assert_eq!(check_series("ntp", &inside), Vec::new());

    let outside = points(&[9_000_000]);
    assert_eq!(
        check_series("ntp", &outside),
        vec![Violation::OffsetOutOfBounds {
            source: "ntp",
            sample_index: 0,
            offset_ns: 9_000_000,
        }]
    );
}

/// Вырожденный вход: метки на границах `i64` в самой серии (не в
/// `estimate_offset` — здесь смещения уже посчитаны и переданы как
/// есть). Обычное `i64`-вычитание в разнице заворачивалось бы; проверка
/// обязана и увидеть нарушение, и не запаниковать на нём.
#[test]
fn check_series_flags_a_jump_between_i64_extremes_without_panicking() {
    let series = vec![
        SeriesPoint {
            sample_index: 0,
            offset_ns: Some(i64::MIN),
        },
        SeriesPoint {
            sample_index: 1,
            offset_ns: Some(i64::MAX),
        },
    ];

    let violations = check_series("ntp", &series);

    assert_eq!(
        violations.len(),
        3,
        "порог нарушен на обеих точках (|i64::MIN|, |i64::MAX| >= порога), плюс скачок между ними"
    );
    assert!(violations.iter().any(|v| matches!(
        v,
        Violation::Jump {
            from_index: 0,
            to_index: 1,
            delta_ns: i64::MAX,
            ..
        }
    )));
}

/// `check_rows` разводит два источника: нарушение только у `bybit` не
/// должно всплыть как нарушение `ntp`, и наоборот.
#[test]
fn check_rows_attributes_violations_to_the_correct_source() {
    let rows = vec![
        ClockRow {
            sample_index: 0,
            local_ts_ns: 0,
            ntp_offset_ns: Some(1_000_000),
            ntp_rtt_ns: Some(100),
            ntp_error: None,
            bybit_offset_ns: Some(1_000_000),
            bybit_rtt_ns: Some(100),
            bybit_error: None,
        },
        ClockRow {
            sample_index: 1,
            local_ts_ns: 1,
            ntp_offset_ns: Some(9_000_000), // нарушение порога
            ntp_rtt_ns: Some(100),
            ntp_error: None,
            bybit_offset_ns: Some(1_000_100), // в пределах
            bybit_rtt_ns: Some(100),
            bybit_error: None,
        },
    ];

    let violations = check_rows(&rows);

    assert!(violations.iter().any(|v| matches!(
        v,
        Violation::OffsetOutOfBounds {
            source: "ntp",
            sample_index: 1,
            ..
        }
    )));
    assert!(!violations.iter().any(|v| match v {
        Violation::OffsetOutOfBounds { source, .. } | Violation::Jump { source, .. } =>
            *source == "bybit",
    }));
}

/// `check_rows` не обязан заводить промежуточные `Vec<SeriesPoint>` для
/// `ntp` и `bybit`: `check_series`/`check_points` обходят точки
/// последовательно, одним `prev`, и `check_rows` может отдать им
/// `map`-итератор по строкам напрямую. На чистой серии (ноль нарушений,
/// `violations` внутри `check_points` ни разу не растёт) это значит ноль
/// аллокаций целиком — до правки здесь стояли два `.collect()`.
#[test]
fn check_rows_allocates_nothing_when_the_series_has_no_violations() {
    let rows = vec![
        ClockRow {
            sample_index: 0,
            local_ts_ns: 0,
            ntp_offset_ns: Some(1_000_000),
            ntp_rtt_ns: Some(100),
            ntp_error: None,
            bybit_offset_ns: Some(1_000_000),
            bybit_rtt_ns: Some(100),
            bybit_error: None,
        },
        ClockRow {
            sample_index: 1,
            local_ts_ns: 1,
            ntp_offset_ns: Some(1_000_100),
            ntp_rtt_ns: Some(100),
            ntp_error: None,
            bybit_offset_ns: Some(1_000_100),
            bybit_rtt_ns: Some(100),
            bybit_error: None,
        },
    ];

    let (violations, counts) = crate::alloc_count::measure(|| check_rows(&rows));

    assert_eq!(violations, Vec::new());
    assert_eq!(
        counts.allocations, 0,
        "check_rows не обязан аллоцировать промежуточные Vec<SeriesPoint>"
    );
}

// ---- ClockRow / CSV --------------------------------------------------

/// Требуемый тест: строки переживают запись и чтение CSV, включая
/// строку с провалом одного источника — `sample_index` и `*_error`
/// остаются на месте, так что упавшая строка узнаваема после чтения
/// с диска, а не только в памяти процесса, который её записал.
#[test]
fn clock_rows_round_trip_through_csv_bytes_including_a_failed_source() {
    let rows = vec![
        ClockRow {
            sample_index: 0,
            local_ts_ns: 1_700_000_000_000_000_000,
            ntp_offset_ns: Some(1_200_000),
            ntp_rtt_ns: Some(15_000_000),
            ntp_error: None,
            bybit_offset_ns: Some(900_000),
            bybit_rtt_ns: Some(20_000_000),
            bybit_error: None,
        },
        ClockRow {
            sample_index: 1,
            local_ts_ns: 1_700_000_003_600_000_000_i64,
            ntp_offset_ns: None,
            ntp_rtt_ns: None,
            ntp_error: Some("Transport(\"timed out\")".to_string()),
            bybit_offset_ns: Some(950_000),
            bybit_rtt_ns: Some(19_000_000),
            bybit_error: None,
        },
    ];

    let mut writer = csv::Writer::from_writer(Vec::new());
    for row in &rows {
        writer.serialize(row).unwrap();
    }
    let bytes = writer.into_inner().unwrap();

    let mut reader = csv::Reader::from_reader(bytes.as_slice());
    let read_back: Vec<ClockRow> = reader.deserialize().collect::<Result<_, _>>().unwrap();

    assert_eq!(read_back, rows);
    let failed = read_back.iter().find(|r| r.ntp_error.is_some()).unwrap();
    assert_eq!(
        failed.sample_index, 1,
        "упавшая строка узнаваема по sample_index"
    );
}

/// `append_row` на настоящем файле: заголовок пишется один раз, обе
/// строки дописываются последовательными вызовами (как это будет
/// происходить раз в час на боевой записи), и обе читаются обратно.
#[test]
fn append_row_writes_the_header_once_and_appends_subsequent_rows_to_the_same_file() {
    let tmp = tempfile::NamedTempFile::new().unwrap();
    let row0 = ClockRow {
        sample_index: 0,
        local_ts_ns: 0,
        ntp_offset_ns: Some(100),
        ntp_rtt_ns: Some(10),
        ntp_error: None,
        bybit_offset_ns: Some(200),
        bybit_rtt_ns: Some(20),
        bybit_error: None,
    };
    let row1 = ClockRow {
        sample_index: 1,
        local_ts_ns: 3_600_000_000_000,
        ntp_offset_ns: Some(150),
        ntp_rtt_ns: Some(11),
        ntp_error: None,
        bybit_offset_ns: Some(250),
        bybit_rtt_ns: Some(21),
        bybit_error: None,
    };

    append_row(tmp.path(), &row0).unwrap();
    append_row(tmp.path(), &row1).unwrap();

    let content = std::fs::read_to_string(tmp.path()).unwrap();
    assert_eq!(
        content.matches("sample_index").count(),
        1,
        "заголовок обязан встретиться ровно один раз"
    );

    let read_back = read_rows(tmp.path()).unwrap();
    assert_eq!(read_back, vec![row0, row1]);
}

// ---- sample / run ------------------------------------------------

struct ScriptedSource {
    script: VecDeque<Result<RoundTrip, ClockError>>,
}

impl ScriptedSource {
    fn new(script: Vec<Result<RoundTrip, ClockError>>) -> Self {
        Self {
            script: script.into(),
        }
    }
}

impl ReferenceClock for ScriptedSource {
    fn round_trip(&mut self) -> Result<RoundTrip, ClockError> {
        self.script
            .pop_front()
            .unwrap_or_else(|| panic!("тест не подготовил столько раундов"))
    }
}

/// Локальные часы для тестов: счётчик, не настоящее время — детерминизм
/// (`ARCHITECTURE.md` A2) и отсутствие ожидания.
struct CountingClock {
    next_ns: Cell<i64>,
}

impl Clock for CountingClock {
    fn now_ns(&self) -> i64 {
        let v = self.next_ns.get();
        self.next_ns.set(v + 1);
        v
    }
}

struct FakeTicker {
    remaining: usize,
}

impl Ticker for FakeTicker {
    fn next_tick(&mut self) -> bool {
        if self.remaining == 0 {
            false
        } else {
            self.remaining -= 1;
            true
        }
    }
}

#[test]
fn sample_records_a_failed_source_as_none_with_an_error_and_keeps_the_other_source() {
    let mut ntp = ScriptedSource::new(vec![Err(ClockError::Transport("timeout".to_string()))]);
    let mut bybit = ScriptedSource::new(vec![Ok(rt(10, 20, 30))]);
    let clock = CountingClock {
        next_ns: Cell::new(0),
    };

    let row = sample(0, &clock, &mut ntp, &mut bybit);

    assert_eq!(row.ntp_offset_ns, None);
    assert!(row.ntp_error.is_some());
    assert_eq!(
        row.bybit_offset_ns,
        Some(estimate_offset(&rt(10, 20, 30)).unwrap().offset_ns)
    );
    assert!(row.bybit_error.is_none());
}

#[test]
fn run_writes_one_row_per_tick_with_sequential_sample_indices_and_they_round_trip() {
    let tmp = tempfile::NamedTempFile::new().unwrap();
    let clock = CountingClock {
        next_ns: Cell::new(0),
    };
    let round_trip = rt(0, 5, 10);
    let mut ntp = ScriptedSource::new(vec![Ok(round_trip); 3]);
    let mut bybit = ScriptedSource::new(vec![Ok(round_trip); 3]);
    let mut ticker = FakeTicker { remaining: 3 };

    let rows = run(tmp.path(), &clock, &mut ntp, &mut bybit, &mut ticker).unwrap();

    assert_eq!(
        rows.iter().map(|r| r.sample_index).collect::<Vec<_>>(),
        vec![0, 1, 2]
    );
    assert_eq!(read_rows(tmp.path()).unwrap(), rows);
}

#[test]
fn run_writes_nothing_when_the_ticker_never_ticks() {
    let tmp = tempfile::NamedTempFile::new().unwrap();
    let clock = CountingClock {
        next_ns: Cell::new(0),
    };
    let mut ntp = ScriptedSource::new(vec![]);
    let mut bybit = ScriptedSource::new(vec![]);
    let mut ticker = FakeTicker { remaining: 0 };

    let rows = run(tmp.path(), &clock, &mut ntp, &mut bybit, &mut ticker).unwrap();

    assert_eq!(rows, Vec::new());
}

// ---- разбор протоколов, без сети ------------------------------------

/// Известный ответ NTP: секунды с 1900-го = `NTP_UNIX_EPOCH_DELTA_SECS +
/// 1000`, дробная часть ноль → unix-время 1000 с ровно.
#[test]
fn parse_ntp_response_decodes_a_known_transmit_timestamp() {
    let mut response = [0u8; 48];
    response[0] = 0b00_100_100; // LI=0, VN=4, Mode=4 (сервер)
    response[1] = 1; // stratum: первичный источник, не kiss-o'-death
    let secs = (NTP_UNIX_EPOCH_DELTA_SECS + 1_000) as u32;
    response[40..44].copy_from_slice(&secs.to_be_bytes());
    response[44..48].copy_from_slice(&0u32.to_be_bytes());

    let round_trip = parse_ntp_response(&response, 100, 200).unwrap();

    assert_eq!(round_trip.remote_ns, 1_000_000_000_000);
    assert_eq!(round_trip.local_send_ns, 100);
    assert_eq!(round_trip.local_recv_ns, 200);
}

#[test]
fn parse_ntp_response_rejects_an_unsynchronized_leap_indicator() {
    let mut response = [0u8; 48];
    response[0] = 0b11_100_100; // LI=3
    response[1] = 1;
    assert!(matches!(
        parse_ntp_response(&response, 0, 0),
        Err(ClockError::Decode(_))
    ));
}

#[test]
fn parse_ntp_response_rejects_a_kiss_of_death_reply() {
    let mut response = [0u8; 48];
    response[0] = 0b00_100_100;
    response[1] = 0; // stratum = 0
    assert!(matches!(
        parse_ntp_response(&response, 0, 0),
        Err(ClockError::Decode(_))
    ));
}

#[test]
fn parse_ntp_response_rejects_a_short_reply() {
    let response = [0u8; 20];
    assert!(matches!(
        parse_ntp_response(&response, 0, 0),
        Err(ClockError::Decode(_))
    ));
}

/// Форма ответа сверена живым запросом к `api.bybit.com/v5/market/time`
/// (см. doc `parse_bybit_server_time`) — эта фикстура и есть тот ответ,
/// с числами, заменёнными на маленькие для читаемости теста.
#[test]
fn parse_bybit_server_time_decodes_the_documented_envelope() {
    let body = r#"{"retCode":0,"retMsg":"OK","result":{"timeSecond":"1","timeNano":"1000000000"},"retExtInfo":{},"time":1000}"#;

    let round_trip = parse_bybit_server_time(body, 10, 20).unwrap();

    assert_eq!(round_trip.remote_ns, 1_000_000_000);
    assert_eq!(round_trip.local_send_ns, 10);
    assert_eq!(round_trip.local_recv_ns, 20);
}

#[test]
fn parse_bybit_server_time_reports_a_nonzero_ret_code_as_an_error() {
    let body =
        r#"{"retCode":10001,"retMsg":"boom","result":{"timeSecond":"1","timeNano":"1000000000"}}"#;
    assert!(matches!(
        parse_bybit_server_time(body, 0, 0),
        Err(ClockError::Decode(_))
    ));
}

#[test]
fn parse_bybit_server_time_rejects_malformed_json() {
    assert!(matches!(
        parse_bybit_server_time("not json", 0, 0),
        Err(ClockError::Decode(_))
    ));
}

#[test]
fn parse_bybit_server_time_rejects_a_non_numeric_time_nano() {
    let body = r#"{"retCode":0,"retMsg":"OK","result":{"timeSecond":"1","timeNano":"abc"}}"#;
    assert!(matches!(
        parse_bybit_server_time(body, 0, 0),
        Err(ClockError::Decode(_))
    ));
}

/// `new` склеивает `{base_url}/v5/market/time` один раз и хранит готовую
/// строку в поле `url` — не в `base_url`, который `round_trip` потом
/// форматировал бы заново на каждый вызов. Без сети: конструктор её не
/// трогает, а поле читается тестом напрямую (тот же модуль, приватность
/// не мешает).
#[test]
fn bybit_server_time_source_precomputes_the_endpoint_url_once_in_new() {
    let source = BybitServerTimeSource::new("https://api.bybit.com").unwrap();

    assert_eq!(source.url, "https://api.bybit.com/v5/market/time");
}

// ---- живая сеть, не часть обычного прогона --------------------------

/// Живой смоук-тест обоих эталонов разом: доказывает, что оба клиента
/// протокола рабочие (пакет NTP собирается и разбирается, HTTP до
/// `api.bybit.com` проходит и `timeNano` разбирается) и дают конечную,
/// разумную оценку смещения — не то, что *эта* машина проходит GC.
///
/// Порог `MAX_ABS_OFFSET_NS` намеренно не проверяется здесь как
/// критерий провала теста: это гейт GC done-condition шага 0.5, и он
/// обязан выполняться на хосте `[ASSUMPTION H12]` (VPS в регионе входа
/// Bybit, дисциплинированном NTP), а не на произвольной машине, где
/// запущен `cargo test -- --ignored`. На недисциплинированном хосте
/// (песочница разработки, CI-контейнер без `ntpd`/`chrony`) реальное
/// смещение легко превышает 5 мс — это свойство хоста, а не дефект
/// разбора, и тест, падающий на этом, бесполезно шумел бы при каждом
/// прогоне не с `H12`. Оба измеренных смещения печатаются
/// (`--nocapture`), чтобы человек, гоняющий этот тест с `H12`, увидел
/// число и мог сверить его с порогом глазами; часовой прогон длиной в
/// неделю, который и есть настоящая проверка GC, выполняет `lob clock`.
#[test]
#[ignore = "живая сеть: NTP-пул и api.bybit.com; не часть обычного cargo test"]
fn live_round_trip_against_ntp_and_bybit_reports_a_finite_plausible_offset() {
    // Сутки с запасом: ловит грубый дефект разбора (не тот эпох, не та
    // единица) как провал теста, но не чувствителен к реальной
    // дисциплине часов конкретной машины — то, что проверяет тест ниже
    // на «отвечает и разбирается», а не «эта машина проходит GC»
    // (см. doc теста).
    const SANITY_BOUND_NS: i64 = 24 * 3_600 * 1_000_000_000;

    let mut ntp = UdpNtpSource::connect("pool.ntp.org:123", std::time::Duration::from_secs(5))
        .expect("подключение к NTP-пулу");
    let ntp_offset = estimate_offset(&ntp.round_trip().expect("раунд NTP")).unwrap();
    eprintln!(
        "NTP: offset={}нс rtt={}нс (порог GC |offset| < {}нс)",
        ntp_offset.offset_ns, ntp_offset.rtt_ns, MAX_ABS_OFFSET_NS
    );
    assert!(
        ntp_offset.offset_ns.abs() < SANITY_BOUND_NS,
        "NTP offset {} — похоже на дефект разбора, не на реальный рассинхрон часов",
        ntp_offset.offset_ns
    );

    let mut bybit = BybitServerTimeSource::new("https://api.bybit.com").expect("клиент serverTime");
    let bybit_offset = estimate_offset(&bybit.round_trip().expect("раунд serverTime")).unwrap();
    eprintln!(
        "Bybit: offset={}нс rtt={}нс (порог GC |offset| < {}нс)",
        bybit_offset.offset_ns, bybit_offset.rtt_ns, MAX_ABS_OFFSET_NS
    );
    assert!(
        bybit_offset.offset_ns.abs() < SANITY_BOUND_NS,
        "Bybit offset {} — похоже на дефект разбора, не на реальный рассинхрон часов",
        bybit_offset.offset_ns
    );
}
