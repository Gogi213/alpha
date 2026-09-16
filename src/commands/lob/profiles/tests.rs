use super::*;
use std::path::Path;

use crate::commands::lob::H3ModeArg;
use crate::lob::costs::FillObservation;
use crate::lob::markout::{base_before, markouts_for_level, MidSample};
use crate::lob::shortlist::{DISTANCE_LABELS, LIFETIME_LABELS, SIZE_LABELS};

use super::accumulate::apply_observation;
use super::axes::{LIFETIME_BOUNDS_MS, SIZE_BOUNDS};

/// Метки числовых границ, введённых этим файлом (`SIZE_BOUNDS`,
/// `LIFETIME_BOUNDS_MS`), обязаны совпасть по количеству и порядку с
/// `shortlist::SIZE_LABELS`/`LIFETIME_LABELS`: у этих двух осей нет
/// собственной парной числовой таблицы в `shortlist.rs` (в отличие от
/// `DISTANCE_BOUNDS_BPS`), и дрейф между числом здесь и меткой там иначе
/// не поймать компилятором.
#[test]
fn size_and_lifetime_bounds_match_shortlist_labels() {
    assert_eq!(SIZE_BOUNDS.len(), SIZE_LABELS.len());
    assert_eq!(LIFETIME_BOUNDS_MS.len(), LIFETIME_LABELS.len());
    assert_eq!(size_bucket(1.0), Some(SIZE_LABELS[0]));
    assert_eq!(size_bucket(1.999), Some(SIZE_LABELS[0]));
    assert_eq!(size_bucket(2.0), Some(SIZE_LABELS[1]));
    assert_eq!(size_bucket(4.0), Some(SIZE_LABELS[2]));
    assert_eq!(size_bucket(0.5), None, "ниже порога H3 — вне сетки размера");
    assert_eq!(lifetime_bucket(0), Some(LIFETIME_LABELS[0]));
    assert_eq!(lifetime_bucket(999), Some(LIFETIME_LABELS[0]));
    assert_eq!(lifetime_bucket(1_000), Some(LIFETIME_LABELS[1]));
    assert_eq!(lifetime_bucket(10_000), Some(LIFETIME_LABELS[2]));
}

#[test]
fn distance_bucket_matches_shortlist_bounds() {
    assert_eq!(distance_bucket(0.0), Some(DISTANCE_LABELS[0]));
    assert_eq!(distance_bucket(0.999), Some(DISTANCE_LABELS[0]));
    assert_eq!(distance_bucket(1.0), Some(DISTANCE_LABELS[1]));
    assert_eq!(distance_bucket(24.999), Some(DISTANCE_LABELS[4]));
    assert_eq!(distance_bucket(25.0), None, "за пределами сетки расстояния");
}

fn write_instruments_csv(root: &Path, symbols: &[(&str, i64)]) {
    let mut text =
        String::from("symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n");
    for (symbol, h3_lots) in symbols {
        text.push_str(&format!("{symbol},0.01,0.1,0.1,5,{h3_lots}\n"));
    }
    std::fs::write(crate::commands::record::instruments_csv_path(root), text).unwrap();
}

fn write_candidates_csv(path: &Path, rows: &[(&str, f64)]) {
    let mut text = String::from("symbol,coverage_top50_bps\n");
    for (symbol, coverage) in rows {
        text.push_str(&format!("{symbol},{coverage}\n"));
    }
    std::fs::write(path, text).unwrap();
}

fn write_session_dir(
    root: &Path,
    session_id: &str,
    symbol: &str,
    started_utc: &str,
    start_hour_utc: u32,
    verified: bool,
    frames: &[Vec<crate::binlog::Record>],
) {
    let dir = root.join(session_id);
    std::fs::create_dir_all(&dir).unwrap();
    let json = format!(
        "{{\"started_utc\":\"{started_utc}\",\"start_hour_utc\":{start_hour_utc},\
         \"instruments\":[\"{symbol}\"]}}"
    );
    std::fs::write(dir.join("session.json"), json).unwrap();
    let header = crate::binlog::Header {
        tick_e9: super::super::test_support::FIX_TICK_E9,
        step_e9: 1_000_000,
        max_records_per_frame: 4096,
    };
    let mut w = crate::binlog::Writer::create(Vec::new(), header, 1).unwrap();
    for f in frames {
        w.write_frame(f).unwrap();
    }
    w.flush().unwrap();
    // Таск 19: `lob session` пишет `<SYMBOL>-<день>.binlog`, не
    // `<SYMBOL>.binlog` — фикстура следует той же раскладке, которую
    // теперь ждёт `session_binlog_for`.
    let day = &started_utc[..10];
    std::fs::write(dir.join(format!("{symbol}-{day}.binlog")), w.into_inner()).unwrap();
    if verified {
        std::fs::write(dir.join(format!("verify-{symbol}.status")), "ok").unwrap();
    }
}

/// Окно «сейчас» (ticket 21) для тестов, которым сам механизм окна
/// безразличен: широкий диапазон, заведомо накрывающий любую дату
/// фикстур этого файла (2000..2099), написан один раз — так тесты не
/// обязаны держать по двое суток ради `split_calendar`. Не переписывает
/// файл, если он уже существует: тесты самого окна пишут свой, более
/// узкий, файл до вызова `base_args`.
fn write_wide_preregistration(root: &Path) -> PathBuf {
    let path = root.join("preregistration.md");
    if !path.is_file() {
        std::fs::write(&path, "exploratory: 2000-01-01\nconfirmatory: 2099-12-31\n").unwrap();
    }
    path
}

fn base_args(root: &Path, candidates_csv: PathBuf, out: PathBuf) -> ProfilesArgs {
    ProfilesArgs {
        root: root.to_path_buf(),
        candidates_csv,
        h3: H3Args {
            h3_mode: H3ModeArg::Floor,
            h3_lots: None,
        },
        warmup_ms: 0,
        repeat_window_ms: super::super::DEFAULT_REPEAT_WINDOW_MS,
        allow_unverified: false,
        out: Some(out),
        now_utc: Some("2026-09-08T00:00:00Z".to_string()),
        runs_out: root.join("runs.csv"),
        execution: ExecutionArgs {
            median_rtt_ns: None,
            p95_rtt_ns: None,
            order_qty_e9: None,
        },
        preregistration: Some(write_wide_preregistration(root)),
        window_end: None,
    }
}

/// Метка `YYYY-MM-DDTHH:MM:SSZ` в миллисекундах от эпохи. Час рождения
/// уровня (В-36) читается из `birth_ms` как настоящий час UTC, поэтому
/// фикстуры часа обязаны нести настоящие метки, а не смещение от нуля.
fn epoch_ms(rfc3339: &str) -> i64 {
    chrono::DateTime::parse_from_rfc3339(rfc3339)
        .unwrap()
        .timestamp_millis()
}

/// Один уровень, рождённый в названный час UTC: снапшот (бид 100@10,
/// аск 200@10) — рождение; через 1 с бид падает до 1 лота, `5*1 < 10`
/// (`below_fraction`) роняет его немедленно (`DeathKind::BelowFraction`),
/// база markout — срез в момент рождения; в 9 с (≤ горизонта 10 с от
/// базы) аск подтягивается на `shift` тиков — `future_asof` подхватывает
/// его до цели `10_000` мс, и `m_10s` растёт вместе с `shift`.
fn hour_frames(at_utc: &str, shift: i64) -> Vec<Vec<crate::binlog::Record>> {
    let base = epoch_ms(at_utc);
    vec![
        super::super::test_support::snap_frame(base, &[(100, 10)], &[(200, 10)]),
        super::super::test_support::delta_frame(base + 1_000, &[(100, 1)], &[]),
        super::super::test_support::delta_frame(base + 9_000, &[], &[(200 - shift, 10)]),
    ]
}

/// Критерий приёмки таска 30 (аудит 2026-09-12, «Сессии → олвейс-он»
/// п. 1; В-36): олвейс-он каталог — **одна часть на сутки**, час старта
/// части один и тот же (полночь UTC), а уровни рождаются в разных часах
/// внутри суток. На прежней оси (час старта части) ось часа была
/// константой `0`: кросс-произведение `(час − средний час)` — ноль на
/// каждом наблюдении, `hour_dependence_test` вырождался
/// (`DegenerateVariance`) и строки `hour_test` в `runs.csv` не было —
/// «зависимости от часа нет» при круглосуточном покрытии. На оси часа
/// рождения уровня те же данные дают разброс часов (по два часа на
/// сутки, `d` и `d+12`), тест доходит до `Ok` и пишет испытание.
/// `session_start_hours_utc` при этом остаётся информацией о записи и
/// печатает ту самую константу.
#[test]
fn always_on_day_gives_the_hour_axis_the_hours_of_level_birth_not_the_part_start() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_instruments_csv(root, &[("SOLUSDT", 5)]);
    let candidates_csv = root.join("candidates.csv");
    write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);

    let session = root.join("always-on");
    std::fs::create_dir_all(&session).unwrap();
    let mut parts: Vec<String> = Vec::new();
    for d in 1..=7i64 {
        let day = format!("2026-05-0{d}");
        let mut frames: Vec<Vec<crate::binlog::Record>> = Vec::new();
        for hour in [d, d + 12] {
            frames.extend(hour_frames(&format!("{day}T{hour:02}:00:00Z"), hour));
        }
        write_part_binlog(&session, "SOLUSDT", &day, 1, &frames);
        parts.push(format!(
            "{{\"symbol\":\"SOLUSDT\",\"part\":1,\"started_utc\":\"{day}T00:00:00Z\"}}"
        ));
    }
    let json = format!(
        "{{\"started_utc\":\"2026-05-07T00:00:00Z\",\"start_hour_utc\":0,\
         \"instruments\":[\"SOLUSDT\"],\"binlog_files\":[{}]}}",
        parts.join(",")
    );
    std::fs::write(session.join("session.json"), json).unwrap();
    std::fs::write(session.join("verify-SOLUSDT.status"), "ok").unwrap();

    let args = base_args(root, candidates_csv, root.join("profiles.csv"));
    run_profiles(&args).expect("прогон по олвейс-он каталогу");
    let text = std::fs::read_to_string(args.out.clone().unwrap()).unwrap();
    let ids = column(&text, "profile_id");
    let i = ids
        .iter()
        .position(|x| x == "marginal:instrument=SOLUSDT")
        .expect("маргинал по инструменту всегда есть в сетке");
    assert_eq!(
        column(&text, "session_start_hours_utc")[i],
        "0",
        "час старта части под олвейс-оном — константа, и это \
         информация о записи, а не ось:\n{text}"
    );
    assert_eq!(
        column(&text, "level_hours_utc")[i],
        "1,2,3,4,5,6,7,13,14,15,16,17,18,19",
        "ось часа — часы рождения уровней (по два на сутки), не час \
         старта части:\n{text}"
    );

    let rows = crate::lob::runs::read_run_rows(&args.runs_out).unwrap();
    assert!(
        rows.iter().any(|r| r
            .detail
            .starts_with("hour_test marginal:instrument=SOLUSDT ")),
        "разброс часов рождения обязан довести hour_dependence_test до \
         Ok и дать строку hour_test в runs.csv: {rows:?}"
    );
}

/// Таск 17, пункт 5г: `hour_dependence_test` отказывает («сутки < G_MIN»)
/// на фикстурах остального файла — они держат один-два дня. Здесь семь
/// **разных** суток одного профиля (`marginal:instrument=SOLUSDT`) с
/// разным часом сессии (`start_hour_utc = d`, и вся сессия внутри этого
/// часа) и разным движением цены (ask сдвигается на `2*d` тиков внутри
/// горизонта 10 с той же сессии) — день и наблюдение линейно связаны тем
/// же приёмом, что `shortlist::hour_dependence_test_rejects_strong_known_trend`,
/// только через настоящий бинлог, а не готовый `HourDayObservation`.
/// Критерий — не число `p`, а сам факт: цепочка дошла до `Ok`, и
/// `log_hour_test` дописал строку `runs.csv`.
///
/// Он же — тест совместимости оси часа (таск 30, В-36): короткая сессия
/// 5–15 мин целиком лежит в одном часе, поэтому час рождения уровня и
/// час старта части совпадают, и переход на новую ось не меняет ни
/// результат теста, ни печатаемые часы.
#[test]
fn hour_dependence_test_reaches_ok_with_seven_days_and_logs_a_runs_csv_row() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_instruments_csv(root, &[("SOLUSDT", 5)]);
    let candidates_csv = root.join("candidates.csv");
    write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);

    for d in 1..=7i64 {
        let started_utc = format!("2026-09-0{d}T{d:02}:00:00Z");
        let frames = hour_frames(&started_utc, 2 * d);
        write_session_dir(
            root,
            &format!("day-{d}"),
            "SOLUSDT",
            &started_utc,
            d as u32,
            true,
            &frames,
        );
    }

    let out = root.join("profiles.csv");
    let args = base_args(root, candidates_csv, out.clone());
    run_profiles(&args).expect("боевой прогон на семи сутках");

    let rows = crate::lob::runs::read_run_rows(&args.runs_out).unwrap();
    let hour_rows: Vec<_> = rows
        .iter()
        .filter(|r| {
            r.detail
                .starts_with("hour_test marginal:instrument=SOLUSDT ")
        })
        .collect();
    assert!(
        !hour_rows.is_empty(),
        "семь суток с разными часами и разным движением обязаны довести \
         hour_dependence_test до Ok и дать строку hour_test в runs.csv: {rows:?}"
    );

    let text = std::fs::read_to_string(out).unwrap();
    let ids = column(&text, "profile_id");
    let i = ids
        .iter()
        .position(|x| x == "marginal:instrument=SOLUSDT")
        .expect("маргинал по инструменту всегда есть в сетке");
    let level_hours = column(&text, "level_hours_utc");
    let start_hours = column(&text, "session_start_hours_utc");
    assert_eq!(
        level_hours[i], start_hours[i],
        "сессия внутри одного часа: час рождения уровня совпадает с \
         часом старта части, ось часа не изменилась\n{text}"
    );
    assert_eq!(level_hours[i], "1,2,3,4,5,6,7", "часы семи сессий\n{text}");
}

/// Критерий приёмки таска 10, буквально: снести файл, прогнать команду
/// на тех же входах, получить те же числа. Заодно проверяет: таблица
/// полная (профиль без наблюдений остаётся строкой с `n=0`, а не
/// исчезает), непригодная корзина расстояния отсутствует (ZEC с
/// покрытием 4 bps не даёт `cross:ZECUSDT|...|[10,25)`), шапка несёт
/// длину окна и seed, `unreachable_*` печатает `not_measured`.
#[test]
fn profiles_csv_is_complete_and_deterministic() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_instruments_csv(root, &[("SOLUSDT", 5), ("ZECUSDT", 5)]);
    let candidates_csv = root.join("candidates.csv");
    write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0), ("ZECUSDT", 4.0)]);

    let frames = super::super::test_support::three_level_frames();
    write_session_dir(
        root,
        "2026-09-08T020000Z",
        "SOLUSDT",
        "2026-09-08T02:00:00Z",
        2,
        true,
        &frames,
    );
    write_session_dir(
        root,
        "2026-09-08T020000Z",
        "ZECUSDT",
        "2026-09-08T02:00:00Z",
        2,
        true,
        &frames,
    );
    // Не верифицированная сессия того же символа — обязана быть
    // пропущена в боевом режиме (verified=false, --allow-unverified не
    // передан), иначе детерминизм и полнота были бы случайностью.
    write_session_dir(
        root,
        "2026-09-09T050000Z",
        "SOLUSDT",
        "2026-09-09T05:00:00Z",
        5,
        false,
        &frames,
    );

    let out = root.join("profiles.csv");
    let args = base_args(root, candidates_csv, out.clone());

    let first = run_profiles(&args).expect("первый прогон");
    let bytes_first = std::fs::read(&out).unwrap();
    std::fs::remove_file(&out).unwrap();
    let second = run_profiles(&args).expect("второй прогон после сноса файла");
    let bytes_second = std::fs::read(&out).unwrap();

    assert_eq!(
        bytes_first, bytes_second,
        "снести файл и прогнать снова — те же числа"
    );
    assert_eq!(first.rows, second.rows);
    assert!(!first.debug);

    let text = String::from_utf8(bytes_second).unwrap();
    let mut lines = text.lines();
    let header_comment = lines.next().unwrap();
    assert!(
        header_comment.starts_with("# lob profiles:"),
        "{header_comment}"
    );
    assert!(
        header_comment.contains("repeat_window_ms="),
        "{header_comment}"
    );
    assert!(header_comment.contains("seed="), "{header_comment}");
    assert!(
        header_comment.contains("fill_model=none"),
        "без модели исполнения шапка обязана назвать источник: {header_comment}"
    );
    assert!(
        !header_comment.contains("debug"),
        "боевой прогон не несёт debug"
    );

    // Полнота: маргинал инструмента ZEC с нулём наблюдений (сессия
    // недоступна отдельно от SOL в этой фикстуре — здесь просто
    // проверяем, что строка вообще есть, а не отфильтрована).
    assert!(
        text.contains("marginal:instrument=ZECUSDT,"),
        "профиль без выигрыша обязан остаться строкой"
    );
    // Непригодная корзина ZEC (покрытие 4 bps: [10,25) требует бы 25 bps
    // видимой книги) обязана отсутствовать вовсе, а не печататься нулём;
    // ближняя [0,1) при том же покрытии пригодна и обязана присутствовать.
    assert!(
        !text.contains("cross:ZECUSDT|eaten|[10,25)"),
        "непригодная корзина расстояния обязана отсутствовать, а не быть строкой с нулём"
    );
    assert!(
        text.contains("cross:ZECUSDT|eaten|[0,1)"),
        "ближняя корзина пригодна даже при узком покрытии ZEC"
    );
    assert!(
        text.contains("cross:SOLUSDT|eaten|[10,25)"),
        "у SOL широкое покрытие — дальний крест обязан присутствовать"
    );
    assert!(
        text.contains("not_measured"),
        "флаг unreachable без живого отчёта G-LAT печатает not_measured"
    );

    // Боевой режим пишет журнал на каждый прогон (append, не перезапись —
    // `runs::append_run_row`), поэтому после двух прогонов строк вдвое
    // больше, чем строк сетки; по модулю числа прогонов — ровно по
    // строке на id сетки.
    let rows = crate::lob::runs::read_run_rows(&args.runs_out).unwrap();
    assert!(!rows.is_empty(), "боевой режим обязан писать runs.csv");
    assert_eq!(
        rows.len(),
        2 * first.rows,
        "по строке журнала на каждый id сетки, на каждый из двух прогонов"
    );
}

/// `--allow-unverified`: сессия без маркера сверки всё равно считается,
/// шапка несёт `debug`, `runs.csv` не пишется.
#[test]
fn allow_unverified_marks_debug_and_skips_runs_csv() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_instruments_csv(root, &[("SOLUSDT", 5)]);
    let candidates_csv = root.join("candidates.csv");
    write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);
    let frames = super::super::test_support::three_level_frames();
    write_session_dir(
        root,
        "2026-09-08T020000Z",
        "SOLUSDT",
        "2026-09-08T02:00:00Z",
        2,
        false,
        &frames,
    );

    let out = root.join("profiles.csv");
    let mut args = base_args(root, candidates_csv, out.clone());
    args.allow_unverified = true;

    let summary = run_profiles(&args).expect("отладочный прогон");
    assert!(summary.debug);
    let text = std::fs::read_to_string(&out).unwrap();
    let header_comment = text.lines().next().unwrap();
    assert!(header_comment.ends_with(" debug"), "{header_comment}");
    assert!(
        text.contains("marginal:instrument=SOLUSDT,4,"),
        "неверифицированная сессия обязана засчитаться под --allow-unverified: {text}"
    );
    assert!(
        !args.runs_out.exists(),
        "отладочный режим не создаёт и не трогает runs.csv"
    );
}

/// Ремонт по ревью (BLOCKING, ось Манифест): без модели исполнения
/// `fill`/`net_fill`/`net_fill_lower` обязаны печатать литерал
/// `not_measured` **в каждой строке**, а не правдоподобное число из
/// плейсхолдера `filled = true` — числовой `fill=1.0000`/`net_fill ==
/// net` без модели исполнения есть выдуманный факт.
#[test]
fn fill_columns_print_not_measured_for_every_row_without_a_fill_model() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_instruments_csv(root, &[("SOLUSDT", 5)]);
    let candidates_csv = root.join("candidates.csv");
    write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);
    let frames = super::super::test_support::three_level_frames();
    write_session_dir(
        root,
        "2026-09-08T020000Z",
        "SOLUSDT",
        "2026-09-08T02:00:00Z",
        2,
        true,
        &frames,
    );
    let out = root.join("profiles.csv");
    let args = base_args(root, candidates_csv, out.clone());
    run_profiles(&args).expect("прогон без модели исполнения");

    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_path(&out)
        .expect("файл читается");
    let mut rows = 0usize;
    for row in r.records() {
        let row = row.expect("строка читается");
        rows += 1;
        assert_eq!(row.get(22), Some("not_measured"), "fill: {row:?}");
        assert_eq!(row.get(23), Some("not_measured"), "net_fill: {row:?}");
        assert_eq!(row.get(24), Some("not_measured"), "net_fill_lower: {row:?}");
    }
    assert!(rows > 0, "таблица не пуста");
}

/// Тело CSV без `#`-комментариев шапки — сравнение строк без учёта
/// изменчивых счётчиков окна (`sessions=`/`sessions_outside_window=`).
fn csv_body_without_header_comments(text: &str) -> String {
    text.lines()
        .filter(|l| !l.starts_with('#'))
        .collect::<Vec<_>>()
        .join("\n")
}

/// Критерий приёмки ticket 21 (R57), буквально: добавление сессии за
/// пределами окна не меняет ни одной строки таблицы. Окно — узкая
/// граница `[2026-05-01, 2026-05-02]` из файла предрегистрации; первая
/// сессия внутри окна, вторая (добавленная между прогонами) — на
/// 2026-06-15, далеко снаружи. `sessions_outside_window` в шапке
/// поднимается с 0 до 1, но ни одна строка данных не сдвигается.
#[test]
fn session_outside_window_does_not_change_any_row() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_instruments_csv(root, &[("SOLUSDT", 5)]);
    let candidates_csv = root.join("candidates.csv");
    write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);
    let preregistration = root.join("preregistration.md");
    std::fs::write(
        &preregistration,
        "exploratory: 2026-05-01\nconfirmatory: 2026-05-02\n",
    )
    .unwrap();

    let frames = super::super::test_support::three_level_frames();
    write_session_dir(
        root,
        "2026-05-01T020000Z",
        "SOLUSDT",
        "2026-05-01T02:00:00Z",
        2,
        true,
        &frames,
    );

    let mut args = base_args(root, candidates_csv, root.join("profiles.csv"));
    args.preregistration = Some(preregistration.clone());
    args.now_utc = Some("2026-06-20T00:00:00Z".to_string());

    run_profiles(&args).expect("первый прогон, окно уже заморожено файлом");
    let text_before = std::fs::read_to_string(args.out.clone().unwrap()).unwrap();
    let header_before = text_before.lines().find(|l| l.starts_with("# window:"));
    assert!(
        header_before.is_some_and(|l| l.contains("sessions_outside_window=0")),
        "{text_before}"
    );

    // Сессия далеко за пределами окна, добавленная между прогонами.
    write_session_dir(
        root,
        "2026-06-15T020000Z",
        "SOLUSDT",
        "2026-06-15T02:00:00Z",
        3,
        true,
        &frames,
    );
    run_profiles(&args).expect("второй прогон, окно то же самое");
    let text_after = std::fs::read_to_string(args.out.clone().unwrap()).unwrap();
    let header_after = text_after.lines().find(|l| l.starts_with("# window:"));
    assert!(
        header_after.is_some_and(|l| l.contains("sessions_outside_window=1")),
        "сессия вне окна обязана быть учтена в счётчике, не прочитана: {text_after}"
    );

    assert_eq!(
        csv_body_without_header_comments(&text_before),
        csv_body_without_header_comments(&text_after),
        "сессия за пределами окна не обязана менять ни одну строку таблицы"
    );
}

/// Датированная часть в уже существующий каталог сессии (таск 22/23):
/// `<SYMBOL>-<день>[-pN].binlog` рядом с прежними, `session.json` не
/// трогается — его пишет вызывающий.
fn write_part_binlog(
    dir: &Path,
    symbol: &str,
    day: &str,
    part: u32,
    frames: &[Vec<crate::binlog::Record>],
) {
    let header = crate::binlog::Header {
        tick_e9: super::super::test_support::FIX_TICK_E9,
        step_e9: 1_000_000,
        max_records_per_frame: 4096,
    };
    let mut w = crate::binlog::Writer::create(Vec::new(), header, 1).unwrap();
    for f in frames {
        w.write_frame(f).unwrap();
    }
    w.flush().unwrap();
    let path = crate::commands::record::day_file_path(dir, symbol, day, part);
    std::fs::write(path, w.into_inner()).unwrap();
}

/// Один каталог `--root/<id>` с частями за двое суток — так выглядит
/// каталог, в который владелец гоняет `lob session` день за днём:
/// верхний `started_utc`/`start_hour_utc` — от последней сессии (D+1,
/// 14 ч), `binlog_files` перечисляет обе части с их `started_utc`.
fn write_two_day_session_dir(
    root: &Path,
    session_id: &str,
    symbol: &str,
    frames: &[Vec<crate::binlog::Record>],
) {
    let dir = root.join(session_id);
    std::fs::create_dir_all(&dir).unwrap();
    write_part_binlog(&dir, symbol, "2026-05-01", 1, frames);
    write_part_binlog(&dir, symbol, "2026-05-02", 1, frames);
    let json = format!(
        "{{\"started_utc\":\"2026-05-02T14:00:00Z\",\"start_hour_utc\":14,\
         \"instruments\":[\"{symbol}\"],\"binlog_files\":[\
         {{\"symbol\":\"{symbol}\",\"part\":1,\"started_utc\":\"2026-05-01T02:00:00Z\"}},\
         {{\"symbol\":\"{symbol}\",\"part\":1,\"started_utc\":\"2026-05-02T14:00:00Z\"}}]}}"
    );
    std::fs::write(dir.join("session.json"), json).unwrap();
    std::fs::write(dir.join(format!("verify-{symbol}.status")), "ok").unwrap();
}

fn column(text: &str, name: &str) -> Vec<String> {
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
        .map(|row| row.unwrap().get(idx).unwrap().to_string())
        .collect()
}

/// Критерий приёмки таска 23: каталог с частями за двое суток даёт
/// `G = 2` и оба часа старта — по части, не по каталогу (до таска 23
/// обе части шли за 2026-05-02 / 14 ч из верхнего `session.json`, и
/// `g` был бы 1).
#[test]
fn two_day_session_dir_yields_g_two_and_hours_per_part() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_instruments_csv(root, &[("SOLUSDT", 5)]);
    let candidates_csv = root.join("candidates.csv");
    write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);
    let frames = super::super::test_support::three_level_frames();
    write_two_day_session_dir(root, "collect", "SOLUSDT", &frames);

    let args = base_args(root, candidates_csv, root.join("profiles.csv"));
    run_profiles(&args).expect("прогон по каталогу с двумя сутками");
    let text = std::fs::read_to_string(args.out.clone().unwrap()).unwrap();
    let g = column(&text, "g");
    let n = column(&text, "n");
    let hours = column(&text, "session_start_hours_utc");
    assert!(!g.is_empty(), "таблица не пуста:\n{text}");
    // Строки, куда попали наблюдения (`n > 0`), видят обе части — по
    // одной на сутки — и оба часа старта.
    let populated: Vec<usize> = n
        .iter()
        .enumerate()
        .filter(|(_, n)| n.parse::<u64>().unwrap_or(0) > 0)
        .map(|(i, _)| i)
        .collect();
    assert!(!populated.is_empty(), "есть строки с наблюдениями:\n{text}");
    for i in populated {
        assert_eq!(g[i], "2", "g строки {i}: двое суток — два кластера\n{text}");
        assert_eq!(
            hours[i], "2,14",
            "часы старта строки {i} — по части, не 14 из каталога\n{text}"
        );
    }
}

/// Критерий приёмки таска 23: окно «сейчас» с границей между сутками
/// одного каталога читает только части нужных суток. Окно
/// `[2026-05-01, 2026-05-01]` на каталоге с частями за 01 и 02 мая даёт
/// ту же таблицу, что каталог только с частью за 01 мая; в шапке —
/// `sessions=1 sessions_outside_window=1`.
#[test]
fn now_window_boundary_inside_one_directory_reads_only_the_days_in_window() {
    let frames = super::super::test_support::three_level_frames();
    let preregistration_text = "exploratory: 2026-05-01\nconfirmatory: 2026-05-01\n";

    // Эталон: каталог только с частью за 01 мая.
    let one = tempfile::tempdir().unwrap();
    let one_root = one.path();
    write_instruments_csv(one_root, &[("SOLUSDT", 5)]);
    let one_candidates = one_root.join("candidates.csv");
    write_candidates_csv(&one_candidates, &[("SOLUSDT", 300.0)]);
    write_session_dir(
        one_root,
        "collect",
        "SOLUSDT",
        "2026-05-01T02:00:00Z",
        2,
        true,
        &frames,
    );
    let one_prereg = one_root.join("preregistration.md");
    std::fs::write(&one_prereg, preregistration_text).unwrap();
    let mut one_args = base_args(one_root, one_candidates, one_root.join("profiles.csv"));
    one_args.preregistration = Some(one_prereg);
    one_args.now_utc = Some("2026-06-20T00:00:00Z".to_string());
    run_profiles(&one_args).expect("эталонный прогон");
    let expected = std::fs::read_to_string(one_args.out.clone().unwrap()).unwrap();

    // Проверяемое: тот же каталог плюс часть за 02 мая, окно — только 01.
    let two = tempfile::tempdir().unwrap();
    let two_root = two.path();
    write_instruments_csv(two_root, &[("SOLUSDT", 5)]);
    let two_candidates = two_root.join("candidates.csv");
    write_candidates_csv(&two_candidates, &[("SOLUSDT", 300.0)]);
    write_two_day_session_dir(two_root, "collect", "SOLUSDT", &frames);
    let two_prereg = two_root.join("preregistration.md");
    std::fs::write(&two_prereg, preregistration_text).unwrap();
    let mut two_args = base_args(two_root, two_candidates, two_root.join("profiles.csv"));
    two_args.preregistration = Some(two_prereg);
    two_args.now_utc = Some("2026-06-20T00:00:00Z".to_string());
    run_profiles(&two_args).expect("прогон с границей окна внутри каталога");
    let actual = std::fs::read_to_string(two_args.out.clone().unwrap()).unwrap();

    let window_line = actual
        .lines()
        .find(|l| l.starts_with("# window:"))
        .expect(&actual);
    assert!(
        window_line.contains("sessions=1 sessions_outside_window=1"),
        "часть за 02 мая — сессионная единица вне окна: {window_line}"
    );
    assert_eq!(
        csv_body_without_header_comments(&actual),
        csv_body_without_header_comments(&expected),
        "часть за сутки вне окна не обязана менять ни одну строку таблицы"
    );
    assert!(
        column(&actual, "g").iter().all(|g| g == "1" || g == "0"),
        "внутри окна — одни сутки:\n{actual}"
    );
}

/// Шапка `profiles-<дата>.csv` несёт `window: …` с границами, длиной в
/// сутках (из границ файла, не из числа сессий) и парой разведочная/
/// подтверждающая, из которой окно выведено.
#[test]
fn profiles_header_prints_window_line() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_instruments_csv(root, &[("SOLUSDT", 5)]);
    let candidates_csv = root.join("candidates.csv");
    write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);
    let preregistration = root.join("preregistration.md");
    std::fs::write(
        &preregistration,
        "exploratory: 2026-05-01,2026-05-02\nconfirmatory: 2026-05-03,2026-05-04\n",
    )
    .unwrap();
    let frames = super::super::test_support::three_level_frames();
    write_session_dir(
        root,
        "2026-05-01T020000Z",
        "SOLUSDT",
        "2026-05-01T02:00:00Z",
        2,
        true,
        &frames,
    );

    let mut args = base_args(root, candidates_csv, root.join("profiles.csv"));
    args.preregistration = Some(preregistration);
    run_profiles(&args).expect("боевой прогон с окном");

    let text = std::fs::read_to_string(args.out.clone().unwrap()).unwrap();
    let window_line = text
        .lines()
        .find(|l| l.starts_with("# window:"))
        .expect(&text);
    assert!(
        window_line.contains("window: 2026-05-01..2026-05-04 days=4"),
        "{window_line}"
    );
    assert!(
        window_line.contains("exploratory=2026-05-01..2026-05-02"),
        "{window_line}"
    );
    assert!(
        window_line.contains("confirmatory=2026-05-03..2026-05-04"),
        "{window_line}"
    );
}

/// Критерий приёмки ticket 21: без `--preregistration` и без
/// `--allow-unverified` окно не определено — громкая ошибка, а не тихое
/// «все сессии».
#[test]
fn missing_preregistration_without_allow_unverified_is_an_error() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    write_instruments_csv(root, &[("SOLUSDT", 5)]);
    let candidates_csv = root.join("candidates.csv");
    write_candidates_csv(&candidates_csv, &[("SOLUSDT", 300.0)]);

    let mut args = base_args(root, candidates_csv, root.join("profiles.csv"));
    args.preregistration = None;

    let err = run_profiles(&args).expect_err("окно не определено без файла предрегистрации");
    assert!(err.to_string().contains("--preregistration"), "{err}");
}

/// `PLAN.md` §11 п.5: зеркальная пара (бид/аск с противоположным
/// движением середины) даёт одинаковый `m` (полярность по стороне это и
/// обеспечивает), но противоположное по знаку сырое движение — «без
/// нормировки знаком» значит без применения `σ`, не «без знака вовсе».
/// Ремонт по ревью (BLOCKING, ось Данные): `.abs()` на `raw_vals` убран.
#[test]
fn mirrored_moves_give_equal_m_and_opposite_signed_raw() {
    fn level(side: crate::book::Side, death_ms: i64) -> LevelRecord {
        LevelRecord {
            side,
            price_tick: 1000,
            birth_ms: 0,
            death_ms,
            lifetime_ms: death_ms,
            size_max: 200,
            time_to_max_ms: 0,
            size_monotonic: true,
            repeat_count: 0,
            repriced: false,
            death: crate::lob::levels::DeathKind::BelowFraction,
            traded_lots: 0,
            rpi_lots: 0,
        }
    }
    fn sample(ts_ms: i64, mid2x: i64) -> MidSample {
        let bid_tick = mid2x / 2;
        let ask_tick = mid2x - bid_tick;
        MidSample {
            ts_ms,
            bid_tick,
            ask_tick,
        }
    }

    // База 20000 (удвоенная середина); бид уходит вниз на срезе 100 мс,
    // аск — на ту же величину вверх: зеркальная пара `markout.rs`.
    let bid_mids = vec![sample(0, 20_000), sample(100, 19_800)];
    let ask_mids = vec![sample(0, 20_000), sample(100, 20_200)];
    let bid_level = level(crate::book::Side::Bid, 50);
    let ask_level = level(crate::book::Side::Ask, 50);

    let m_bid = markouts_for_level(&bid_level, &bid_mids);
    let m_ask = markouts_for_level(&ask_level, &ask_mids);
    let base_bid = base_before(&bid_mids, bid_level.death_ms);
    let base_ask = base_before(&ask_mids, ask_level.death_ms);

    let mut agg_bid = ProfileAgg::default();
    let mut agg_ask = ProfileAgg::default();
    apply_observation(
        &mut agg_bid,
        bid_level.outcome(),
        m_bid,
        base_bid,
        &bid_mids,
        0,
        0,
        "SOLUSDT",
        &bid_level,
        &NoFillModel,
    );
    apply_observation(
        &mut agg_ask,
        ask_level.outcome(),
        m_ask,
        base_ask,
        &ask_mids,
        0,
        0,
        "SOLUSDT",
        &ask_level,
        &NoFillModel,
    );

    let m_bid_100 = agg_bid.horizons[0].m_obs[0].net_bps;
    let m_ask_100 = agg_ask.horizons[0].m_obs[0].net_bps;
    assert!(
        (m_bid_100 - m_ask_100).abs() < 1e-9,
        "зеркальная пара обязана дать одинаковый m: {m_bid_100} vs {m_ask_100}"
    );

    let raw_bid_100 = agg_bid.horizons[0].raw_vals[0];
    let raw_ask_100 = agg_ask.horizons[0].raw_vals[0];
    assert!(
        raw_bid_100 < 0.0 && raw_ask_100 > 0.0,
        "сырое движение обязано остаться знаковым: bid={raw_bid_100} ask={raw_ask_100}"
    );
    assert!(
        (raw_bid_100 + raw_ask_100).abs() < 1e-9,
        "величины противоположны и равны по модулю: {raw_bid_100} vs {raw_ask_100}"
    );
}

/// Таск 16: `observed_sharpe` — Шарп **только исполнившихся** наблюдений
/// (`filled == true`), посчитанный вручную по трём известным числам, не
/// вызовом `sharpe_ratio` (формула не подтверждает сама себя). Четвёртое
/// наблюдение (`filled == false`) обязано быть исключено.
#[test]
fn observed_sharpe_column_matches_hand_computed_sharpe_of_filled_observations() {
    let mut agg = ProfileAgg {
        eaten: 3,
        ..ProfileAgg::default()
    };
    agg.fill_obs = vec![
        FillObservation {
            day_cluster: 0,
            net_bps: 10.0,
            filled: true,
        },
        FillObservation {
            day_cluster: 0,
            net_bps: -2.0,
            filled: true,
        },
        FillObservation {
            day_cluster: 1,
            net_bps: 999.0,
            filled: false,
        },
        FillObservation {
            day_cluster: 1,
            net_bps: 6.0,
            filled: true,
        },
    ];
    let filled = [10.0_f64, -2.0, 6.0];
    let mean = filled.iter().sum::<f64>() / 3.0;
    let var = filled.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / 3.0;
    let expected_sharpe = mean / var.sqrt();

    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("row.csv");
    {
        let file = std::fs::File::create(&path).unwrap();
        let mut w = csv::WriterBuilder::new()
            .has_headers(false)
            .from_writer(file);
        write_row(&mut w, "marginal:test", &agg, true).unwrap();
        w.flush().unwrap();
    }
    let text = std::fs::read_to_string(&path).unwrap();
    let cols: Vec<&str> = text.trim_end().split(',').collect();
    let printed: f64 = cols
        .last()
        .expect("строка не пуста")
        .parse()
        .expect("observed_sharpe обязан быть числом при measured=true");
    assert!(
        (printed - expected_sharpe).abs() < 1e-6,
        "printed={printed} expected={expected_sharpe}"
    );
}

/// Без активной модели (`measured=false`) `observed_sharpe` печатает
/// `not_measured` буквально, тем же приёмом, что `fill`/`net_fill` —
/// критерий приёмки таска 16, а не молчаливый `none`.
#[test]
fn observed_sharpe_is_not_measured_without_a_fill_model() {
    let agg = ProfileAgg {
        eaten: 1,
        fill_obs: vec![FillObservation {
            day_cluster: 0,
            net_bps: 5.0,
            filled: true,
        }],
        ..ProfileAgg::default()
    };
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("row.csv");
    {
        let file = std::fs::File::create(&path).unwrap();
        let mut w = csv::WriterBuilder::new()
            .has_headers(false)
            .from_writer(file);
        write_row(&mut w, "marginal:test", &agg, false).unwrap();
        w.flush().unwrap();
    }
    let text = std::fs::read_to_string(&path).unwrap();
    assert!(text.trim_end().ends_with("not_measured"), "{text}");
}

/// `resolve_fill_model` (таск 16): тройка RTT/лота задаётся вся или не
/// задаётся вовсе — частичная тройка отказывает, а не молча берёт
/// `NoFillModel` или изобретает недостающее число.
#[test]
fn resolve_fill_model_requires_all_three_or_none() {
    assert_eq!(
        resolve_fill_model(None, None, None).unwrap().label(),
        "none"
    );
    assert_eq!(
        resolve_fill_model(Some(1_000_000), Some(2_000_000), Some(100_000_000))
            .unwrap()
            .label(),
        "backtest"
    );
    assert!(resolve_fill_model(Some(1_000_000), None, None).is_err());
    assert!(resolve_fill_model(None, Some(1_000_000), Some(1)).is_err());
}
