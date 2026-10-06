use super::*;
use crate::book::Side;
use crate::commands::lob::levels::{run_levels, LevelsArgs};
use crate::commands::lob::replay::ReplayDay;
use crate::commands::lob::replay_symbol;
use crate::commands::lob::test_support::{three_level_frames, touch_frames, write_day};
use crate::commands::lob::H3ModeArg;
use crate::lob::excursion::SecondMids;
use crate::lob::levels::{ApproachEnd, ApproachRecord, LevelsConfig, TouchRecord};
use crate::lob::r1::{ArmR1, FLOW_N, FLOW_NAMES, LEVEL_N, R1_UNDEF};
use crate::lob::sigma::SigmaSeries;

fn touches_args(root: &std::path::Path) -> TouchesArgs {
    TouchesArgs {
        root: root.to_path_buf(),
        symbol: "SOLUSDT".to_string(),
        h3: H3Args {
            h3_mode: H3ModeArg::Percentile,
            h3_lots: Some(5),
            h3_usd: None,
            h3_strength_pct: None,
            h3_strength_window_bps: None,
        },
        h3_k: None,
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        out: None,
        approach_bps: Vec::new(),
        approach_min_age_secs: 0,
        moves: None,
        moves_window_ms: None,
        moves_bin_ms: None,
        numbers: None,
        allow_unverified: true,
        carry_age: false,
        emit_day: None,
        levels_out: None,
        minute_flow: None,
        r1_cols: false,
    }
}

fn read_rows(path: &std::path::Path) -> (Vec<String>, Vec<Vec<String>>) {
    let mut r = csv::Reader::from_path(path).unwrap();
    let header: Vec<String> = r.headers().unwrap().iter().map(str::to_string).collect();
    let rows = r
        .records()
        .map(|rec| rec.unwrap().iter().map(str::to_string).collect())
        .collect();
    (header, rows)
}

fn col<'a>(header: &[String], row: &'a [String], name: &str) -> &'a str {
    let i = header.iter().position(|h| h == name).unwrap();
    &row[i]
}

/// Критерий приёмки таска 35: `lob touches` на фикстуре пишет
/// `touches-<SYMBOL>.csv` с ожидаемыми колонками, по строке на кончившееся
/// касание, с признаками из записи и производными.
#[test]
fn touches_fixture_writes_touch_rows_with_expected_columns() {
    let dir = tempfile::tempdir().unwrap();
    write_day(dir.path(), "SOLUSDT", "2026-09-08", &touch_frames());
    let summary = run_touches(&touches_args(dir.path())).unwrap();
    assert_eq!(summary.days, 1);
    assert_eq!(summary.touches, 3);
    assert_eq!(
        summary.out,
        dir.path().join("touches-SOLUSDT.csv"),
        "имя артефакта — touches-<SYMBOL>.csv"
    );
    let (header, rows) = read_rows(&summary.out);
    assert_eq!(header, TOUCHES_COLUMNS.map(str::to_string).to_vec());
    assert_eq!(rows.len(), 3);
    assert!(
        rows.iter().all(|r| col(&header, r, "price_tick") == "99"),
        "родившиеся лучшей ценой 100/101/105 касаний не дают (В-43)"
    );
    for r in &rows {
        assert_eq!(r.len(), TOUCHES_COLUMNS.len());
    }

    let find = |idx: &str| {
        rows.iter()
            .find(|r| col(&header, r, "touch_index") == idx)
            .unwrap_or_else(|| panic!("нет строки касания {idx}"))
    };
    let t0 = find("0");
    assert_eq!(col(&header, t0, "day_utc"), "2026-09-08");
    assert_eq!(col(&header, t0, "side"), "bid");
    assert_eq!(col(&header, t0, "start_ms"), "1000");
    assert_eq!(col(&header, t0, "end_ms"), "2000");
    assert_eq!(col(&header, t0, "duration_ms"), "1000");
    assert_eq!(col(&header, t0, "birth_ms"), "0");
    assert_eq!(col(&header, t0, "age_ms"), "1000");
    assert_eq!(col(&header, t0, "size_at_touch"), "10");
    assert_eq!(col(&header, t0, "size_max_before"), "10");
    assert_eq!(col(&header, t0, "traded_during"), "0");
    // Кадры раз в секунду: фронтран за секунду до касания (В-45) — те же 10
    // лотов бида 100, что и сметённые последним шагом.
    assert_eq!(col(&header, t0, "frontrun_lots"), "10");
    assert_eq!(col(&header, t0, "swept_lots"), "10");
    assert_eq!(col(&header, t0, "round_zeros"), "0");
    assert_eq!(col(&header, t0, "ended_by_death"), "false");
    assert_eq!(
        col(&header, t0, "stack_levels"),
        "1",
        "окно 25 bps от 99 тиков — 0 тиков: только сам уровень (В-45)"
    );
    // Касание длилось 1000 мс: горизонты 100 мс и 1 с — внутри касания
    // (граница включительна), 10 с и 60 с — нет (В-45 (2)).
    assert_eq!(col(&header, t0, "within_touch_100ms"), "true");
    assert_eq!(col(&header, t0, "within_touch_1s"), "true");
    assert_eq!(col(&header, t0, "within_touch_10s"), "false");
    assert_eq!(col(&header, t0, "within_touch_60s"), "false");
    // Расстояние 99 до середины 102.5 при рождении — |99 − 102.5| / 102.5.
    let dist: f64 = col(&header, t0, "dist_bps").parse().unwrap();
    assert!((dist - 341.463_414).abs() < 1e-3, "dist_bps = {dist}");
    // База — срез как есть на 1000 мс (середина уже шагнула на 99: 204/2);
    // через 100 мс сдвига нет — ноль, не −1 тик; через 1 с середина 205/2 —
    // выше: бид-касание, «в сторону отскока» — плюс.
    assert_eq!(col(&header, t0, "m_100ms"), "0.000000");
    let m_1s: f64 = col(&header, t0, "m_1s").parse().unwrap();
    assert!(m_1s > 0.0, "m_1s = {m_1s}");
    // Подход: за секунду до касания середина падала на бид (205 → 204) — плюс.
    let ap_1s: f64 = col(&header, t0, "approach_1s").parse().unwrap();
    assert!(ap_1s > 0.0, "approach_1s = {ap_1s}");
    assert_eq!(
        col(&header, t0, "approach_10s"),
        "",
        "десяти секунд до касания в записи нет"
    );

    let t1 = find("1");
    assert_eq!(col(&header, t1, "start_ms"), "3000");
    assert_eq!(col(&header, t1, "end_ms"), "4000");
    assert_eq!(col(&header, t1, "frontrun_lots"), "10");
    assert_eq!(col(&header, t1, "ended_by_death"), "false");

    // Касание 2 кончилось смертью: сделка внутри — 4 лота, фронтран — 10
    // лотов бида 101 с кадра 4000.
    let t2 = find("2");
    assert_eq!(col(&header, t2, "start_ms"), "5000");
    assert_eq!(col(&header, t2, "end_ms"), "6000");
    assert_eq!(col(&header, t2, "traded_during"), "4");
    assert_eq!(col(&header, t2, "frontrun_lots"), "10");
    assert_eq!(col(&header, t2, "ended_by_death"), "true");
    // T2 (Г-07): на кадре старта (5000) бид 99 стал лучшим, книга бида —
    // {99: 10, 98: 10} (100 и 101 сняты раньше) — позади 99 только 98 (10).
    assert_eq!(col(&header, t2, "depth_behind_lots"), "10");
}

/// Обратимость CSV → `TouchRecord` (`read_touches_csv`): записи трекера из
/// реплея фикстуры и записи, прочитанные из `touches-SOLUSDT.csv`, равны
/// поле в поле, сутки — из `day_utc`. На этом стоит `lob bounce-grid
/// --touches-from` (касания из ночного H3 вместо реплея книги).
#[test]
fn touches_csv_reads_back_the_same_records() {
    let dir = tempfile::tempdir().unwrap();
    write_day(dir.path(), "SOLUSDT", "2026-09-08", &touch_frames());
    let args = touches_args(dir.path());
    let summary = run_touches(&args).unwrap();
    let rows = read_touches_csv(&summary.out).unwrap();
    assert_eq!(rows.len(), 3);

    let cfg = LevelsConfig {
        mode: crate::lob::levels::H3Mode::Percentile { h3_lots: 5 },
        warmup_ms: args.warmup_ms,
        repeat_window_ms: args.repeat_window_ms,
        approach_bps: None,
        approach_min_age_ms: 0,
    };
    let replay = replay_symbol(dir.path(), "SOLUSDT", cfg).unwrap();
    let expected: Vec<TouchRow> = replay
        .days
        .iter()
        .flat_map(|d| {
            d.touches.iter().map(move |t| TouchRow {
                day: d.day.clone(),
                touch: *t,
                ret_bps: Some(std::array::from_fn(|k| {
                    pre_touch_return_bps_csv(&d.mids, t.start_ms, PRE_TOUCH_MS[k])
                })),
            })
        })
        .collect();
    assert_eq!(rows, expected, "CSV обязан читаться в те же записи");
    // Пустые поля читаются как «нет»: у фикстуры нет соседей в окнах силы.
    assert!(rows.iter().all(|r| r.touch.strength_held_e2[3] == -1));
}

/// S2 плана по сторонам: три колонки хода до касания в конце шапки; на
/// фикстуре (6 с записи) все три пусты — окна 10 мин и длиннее упираются в
/// начало; на ряду, где окно есть, ход считается от среза за окно до базы
/// касания, знак абсолютный. Рядом с касаниями — минутный ряд `mids1m-*.csv`
/// с минутами подряд и закрытием минуты (фикстура — 6 с, одна минута).
#[test]
fn touches_write_pre_touch_returns_and_minute_mids() {
    let dir = tempfile::tempdir().unwrap();
    write_day(dir.path(), "SOLUSDT", "2026-09-08", &touch_frames());
    let summary = run_touches(&touches_args(dir.path())).unwrap();
    let (header, rows) = read_rows(&summary.out);
    assert_eq!(
        &header[header.len() - 4..],
        [
            "ret_10m_bps",
            "ret_1h_bps",
            "ret_4h_bps",
            "depth_behind_lots"
        ],
        "T2 (П-02) добавил depth_behind_lots аддитивно, в самый конец — после ret_4h_bps"
    );
    for r in &rows {
        for c in ["ret_10m_bps", "ret_1h_bps", "ret_4h_bps"] {
            assert_eq!(col(&header, r, c), "", "окно длиннее записи — пусто");
        }
    }
    let mids = [
        crate::lob::markout::MidSample {
            ts_ms: 0,
            bid_tick: 99,
            ask_tick: 101,
        },
        crate::lob::markout::MidSample {
            ts_ms: 500_000,
            bid_tick: 104,
            ask_tick: 106,
        },
        crate::lob::markout::MidSample {
            ts_ms: 700_000,
            bid_tick: 109,
            ask_tick: 111,
        },
    ];
    // База касания на 700_000 — 110 (2x = 220); срез за 10 мин (≤ 100_000) — первый, 100 (2x = 200).
    let r = pre_touch_return_bps(&mids, 700_000, PRE_TOUCH_MS[0]).unwrap();
    assert!((r - 1000.0).abs() < 1e-9, "+10 % = +1000 bps, получено {r}");
    assert_eq!(pre_touch_return_bps(&mids, 700_000, PRE_TOUCH_MS[1]), None);

    let m = mids1m_path(&summary.out, "SOLUSDT");
    assert_eq!(m, dir.path().join("mids1m-SOLUSDT.csv"));
    let (mh, mrows) = read_rows(&m);
    assert_eq!(mh, ["minute_ms", "mid2x"]);
    assert!(!mrows.is_empty(), "минимум одна минута: {mrows:?}");
    let minutes: Vec<i64> = mrows.iter().map(|r| r[0].parse().unwrap()).collect();
    assert!(
        minutes.windows(2).all(|w| w[1] == w[0] + 60_000),
        "минуты подряд: {minutes:?}"
    );
    assert!(mrows.iter().all(|r| r[1].parse::<i64>().unwrap() > 0));
}

/// F1 этапа F: `--approach-bps` пишет `approaches-<SYMBOL>.csv` рядом с
/// касаниями, а `read_approaches_csv` читает те же записи поле в поле.
/// Полоса 700 bps — от фикстуры: аск 105 против бид-стены 99 это 606 bps;
/// взвод на кадре 2000 (стена не лучшая: вернулся бид 100), снятие касанием
/// на 3000 (100 снят — стена стала лучшей). Повторного взвода нет: после
/// снятия касанием цена за `2 × D` не уходила.
#[test]
fn approaches_csv_reads_back_the_same_records() {
    let dir = tempfile::tempdir().unwrap();
    write_day(dir.path(), "SOLUSDT", "2026-09-08", &touch_frames());
    let mut args = touches_args(dir.path());
    args.approach_bps = vec![700];
    let summary = run_touches(&args).unwrap();
    assert_eq!(summary.approaches, 1);
    let path = summary
        .approaches_out
        .first()
        .cloned()
        .expect("файл подходов");
    assert_eq!(path, dir.path().join("approaches-SOLUSDT.csv"));
    let (header, rows) = read_rows(&path);
    assert_eq!(header, APPROACHES_COLUMNS.map(str::to_string).to_vec());
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].len(), APPROACHES_COLUMNS.len());
    let r = &rows[0];
    assert_eq!(col(&header, r, "day_utc"), "2026-09-08");
    assert_eq!(col(&header, r, "side"), "bid");
    assert_eq!(col(&header, r, "price_tick"), "99");
    assert_eq!(col(&header, r, "approach_index"), "0");
    assert_eq!(col(&header, r, "arm_ms"), "2000");
    assert_eq!(col(&header, r, "age_ms"), "2000");
    assert_eq!(col(&header, r, "arm_dist_bps"), "606");
    assert_eq!(col(&header, r, "best_opp_tick"), "105");
    assert_eq!(col(&header, r, "best_own_tick"), "100");
    assert_eq!(col(&header, r, "disarm_ms"), "3000");
    assert_eq!(col(&header, r, "duration_ms"), "1000");
    assert_eq!(col(&header, r, "touch_start_ms"), "3000");
    assert_eq!(col(&header, r, "disarm_reason"), "touch");
    // Обратимость: CSV читается в те же записи, что отдаёт трекер.
    let read = read_approaches_csv(&path).unwrap();
    let cfg = LevelsConfig {
        mode: crate::lob::levels::H3Mode::Percentile { h3_lots: 5 },
        warmup_ms: args.warmup_ms,
        repeat_window_ms: args.repeat_window_ms,
        approach_bps: Some(700),
        approach_min_age_ms: 0,
    };
    let replay = replay_symbol(dir.path(), "SOLUSDT", cfg).unwrap();
    let expected: Vec<ApproachRow> = replay
        .days
        .iter()
        .flat_map(|d| {
            d.approaches.iter().map(move |a| ApproachRow {
                day: d.day.clone(),
                approach: *a,
            })
        })
        .collect();
    assert_eq!(read, expected, "CSV обязан читаться в те же записи");
}

/// Механика D-оси (владелец 20.09: в предрегистрацию идут несколько полос):
/// `--approach-bps` списком — один реплей, файл на полосу. Первая полоса пишет
/// привычное имя `approaches-<SYMBOL>.csv` и обязана совпасть с одиночным
/// прогоном той же полосы (байт в байт: один и тот же трекер на той же книге),
/// остальные — `approaches-<SYMBOL>-D<d>.csv`; касания одинаковы у всех полос.
/// Фикстура: аск 105 против бид-стены 99 — 606 bps, поэтому `D = 300` не
/// взводится вовсе, `700` и `1500` дают по одной записи.
#[test]
fn approach_bands_write_one_file_per_band() {
    let dir = tempfile::tempdir().unwrap();
    write_day(dir.path(), "SOLUSDT", "2026-09-08", &touch_frames());
    let mut single = touches_args(dir.path());
    single.approach_bps = vec![700];
    single.out = Some(dir.path().join("one").join("touches-SOLUSDT.csv"));
    let one = run_touches(&single).unwrap();
    assert_eq!(one.approaches, 1);
    let one_ap = one
        .approaches_out
        .first()
        .cloned()
        .expect("файл полосы 700");

    let mut multi = touches_args(dir.path());
    multi.approach_bps = vec![700, 300, 1500];
    multi.out = Some(dir.path().join("many").join("touches-SOLUSDT.csv"));
    let many = run_touches(&multi).unwrap();
    assert_eq!(many.approaches_out.len(), 3, "{:?}", many.approaches_out);
    assert!(many.approaches_out[0].ends_with("approaches-SOLUSDT.csv"));
    assert!(many.approaches_out[1].ends_with("approaches-SOLUSDT-D300.csv"));
    assert!(many.approaches_out[2].ends_with("approaches-SOLUSDT-D1500.csv"));
    assert_eq!(
        many.touches, one.touches,
        "сигнал подхода не меняет касания"
    );
    // Первая полоса списка — те же байты, что одиночный прогон.
    assert_eq!(
        std::fs::read(&many.approaches_out[0]).unwrap(),
        std::fs::read(&one_ap).unwrap(),
        "полоса 700 в списке и одиночно обязана дать тот же файл"
    );
    let (_, wide) = read_rows(&many.approaches_out[2]);
    assert_eq!(wide.len(), 1, "D = 1500 ловит тот же подход (606 bps)");
    let (_, narrow) = read_rows(&many.approaches_out[1]);
    assert!(
        narrow.is_empty(),
        "D = 300 не взводится: 606 bps вне полосы"
    );
    // Явная ошибка на повторы и отрицательные полосы.
    let mut bad = touches_args(dir.path());
    bad.approach_bps = vec![10, 20, 10];
    assert!(run_touches(&bad).is_err(), "повтор полосы — отказ");
    let mut bad = touches_args(dir.path());
    bad.approach_bps = vec![0];
    assert!(run_touches(&bad).is_err(), "нулевая полоса — отказ");
}

fn minimal_touch() -> TouchRecord {
    TouchRecord {
        side: Side::Bid,
        price_tick: 1000,
        touch_index: 0,
        start_ms: 0,
        end_ms: 500,
        duration_ms: 500,
        level_birth_ms: 0,
        size_at_touch: 200,
        size_max_before: 200,
        traded_during: 0,
        frontrun_lots: 0,
        frontrun_tick: None,
        swept_lots: 0,
        round_zeros: 3,
        ended_by_death: false,
        stack_levels: 1,
        stack_next_tick: None,
        traded_first_s: [0; 3],
        flow_1h_lots: 0,
        strength_e2: [-1, -1, -1],
        strength_held_e2: [-1, -1, -1, -1],
        repeat_count: 0,
        depth_behind_lots: 0,
    }
}

fn minimal_approach() -> ApproachRecord {
    ApproachRecord {
        side: Side::Bid,
        price_tick: 1000,
        approach_index: 0,
        arm_ms: 0,
        arm_dist_bps: 10,
        level_birth_ms: 0,
        size_at_arm: 100,
        best_own_tick: 999,
        best_opp_tick: 1010,
        flow_1h_lots: 0,
        strength_e2: [-1, -1, -1],
        depth_behind_lots: 0,
        stack_levels_at_arm: 0,
        frontrun_lots_at_arm: 0,
        p08: Some(Default::default()),
        r1: None,
        touch_start_ms: None,
        disarm_ms: 100,
        disarm_reason: ApproachEnd::PriceLeft,
    }
}

/// W5г: `TOUCHES_COLUMNS` (заголовок) и `touch_row_pairs` (значения) были
/// два независимых списка сверенных только по длине — переставить два
/// соседних имени в одном, забыв про другой, компилировалось молча. Теперь
/// имя и значение — одна пара на позицию; здесь сверяем порядок имён,
/// вынутых из пар, с заголовком напрямую (не через `debug_assert_eq!`
/// внутри `row::touch_row`, который не выполняется в `--release` —
/// см. `run_touches`, W5б).
#[test]
fn touch_row_pair_names_match_the_written_header() {
    let day = ReplayDay {
        day: "2026-01-01".to_string(),
        records: Vec::new(),
        touches: Vec::new(),
        approaches: Vec::new(),
        mids: Vec::new(),
    };
    let sigma = SigmaSeries::from_mids(&[]);
    let second_mids = SecondMids::from_mids(&[]);
    let names = super::row::touch_row_pair_names(&day, &minimal_touch(), &sigma, &second_mids);
    assert_eq!(
        names, TOUCHES_COLUMNS,
        "порядок имён из пар обязан совпадать с заголовком CSV"
    );
}

/// То же для `approaches-<SYMBOL>.csv` (W5г).
#[test]
fn approach_row_pair_names_match_the_written_header() {
    let names = super::row::approach_row_pair_names("2026-01-01", &minimal_approach());
    assert_eq!(
        names, APPROACHES_COLUMNS,
        "порядок имён из пар обязан совпадать с заголовком CSV"
    );
}

/// Файл подходов так, как его пишет `run_touches`: шапка и строки, с колонками R1 или без.
fn write_approach_csv(path: &std::path::Path, rows: &[ApproachRecord], with_r1: bool) {
    let mut w = csv::Writer::from_path(path).unwrap();
    if with_r1 {
        w.write_record(APPROACHES_COLUMNS.iter().copied().chain(ArmR1::names()))
            .unwrap();
    } else {
        w.write_record(APPROACHES_COLUMNS).unwrap();
    }
    for a in rows {
        let row = super::row::approach_row("2026-09-08", a);
        if with_r1 {
            w.write_record(row.into_iter().chain(super::row::r1_cells(a.r1.as_ref())))
                .unwrap();
        } else {
            w.write_record(row).unwrap();
        }
    }
    w.flush().unwrap();
}

/// Записи R1 с границами `i64`, нулём и «не определено» вперемешку.
fn varied_r1() -> ArmR1 {
    let mut r1 = ArmR1::undefined();
    r1.flow[0] = 0;
    r1.flow[1] = 12_345;
    r1.flow[FLOW_N - 1] = -700;
    r1.level[0] = i64::MAX;
    r1.level[1] = i64::MIN + 1;
    r1
}

/// TK-025: клетки R1 — пусто только для «нет записи» и `R1_UNDEF`; ноль и границы `i64` пишутся числом.
#[test]
fn r1_cells_are_empty_for_none_and_undef_only() {
    let cells = super::row::r1_cells(Some(&varied_r1()));
    assert_eq!(cells.len(), FLOW_N + LEVEL_N);
    assert_eq!(cells.len(), ArmR1::names().count());
    assert_eq!(cells[0], "0");
    assert_eq!(cells[1], "12345");
    assert!(cells[2..FLOW_N - 1].iter().all(String::is_empty));
    assert_eq!(cells[FLOW_N - 1], "-700");
    assert_eq!(cells[FLOW_N], i64::MAX.to_string());
    assert_eq!(cells[FLOW_N + 1], (i64::MIN + 1).to_string());
    assert!(cells[FLOW_N + 2..].iter().all(String::is_empty));
    let none = super::row::r1_cells(None);
    assert_eq!(none.len(), FLOW_N + LEVEL_N);
    assert!(none.iter().all(String::is_empty));
}

/// TK-025: запись → чтение. Значения и «не определено» возвращаются как были; запись без R1 (`None`)
/// у кэша с колонками читается как «всё не определено» (`Some`, пустые клетки) — так `r1.is_some()`
/// значит «кэш несёт R1»; кэш без колонок — `None`; наполовину записанный — отказ с названием колонки.
#[test]
fn r1_columns_round_trip_through_the_approaches_csv() {
    let dir = tempfile::tempdir().unwrap();
    let mut with = minimal_approach();
    with.r1 = Some(varied_r1());
    let mut undef = minimal_approach();
    undef.approach_index = 1;
    undef.r1 = Some(ArmR1::undefined());
    let mut none = minimal_approach();
    none.approach_index = 2;
    none.r1 = None;
    let rows = [with, undef, none];

    let path = dir.path().join("with-r1.csv");
    write_approach_csv(&path, &rows, true);
    let (header, raw) = read_rows(&path);
    assert_eq!(header.len(), APPROACHES_COLUMNS.len() + FLOW_N + LEVEL_N);
    assert!(raw.iter().all(|r| r.len() == header.len()));
    let read = read_approaches_csv(&path).unwrap();
    assert_eq!(read.len(), 3);
    assert_eq!(
        read[0].approach, with,
        "значения и UNDEF возвращаются как были"
    );
    assert_eq!(
        read[0].approach.r1.unwrap().get("since_far_ms"),
        Some(R1_UNDEF)
    );
    assert_eq!(read[1].approach.r1, Some(ArmR1::undefined()));
    assert_eq!(
        read[2].approach.r1,
        Some(ArmR1::undefined()),
        "None у кэша с колонками читается как «не определено»"
    );

    // Кэш без колонок R1 — прежний файл: r1 = None, остальное то же.
    let old = dir.path().join("without-r1.csv");
    write_approach_csv(&old, &rows, false);
    let (old_header, _) = read_rows(&old);
    assert_eq!(old_header, APPROACHES_COLUMNS.map(str::to_string).to_vec());
    let read_old = read_approaches_csv(&old).unwrap();
    assert!(read_old.iter().all(|r| r.approach.r1.is_none()));
    assert_eq!(read_old[0].approach, ApproachRecord { r1: None, ..with });

    // Часть колонок — отказ: наполовину записанный кэш не читается как «без R1».
    let (header, raw) = read_rows(&path);
    let partial = dir.path().join("partial.csv");
    let mut w = csv::Writer::from_path(&partial).unwrap();
    w.write_record(&header[..header.len() - 1]).unwrap();
    for r in &raw {
        w.write_record(&r[..r.len() - 1]).unwrap();
    }
    w.flush().unwrap();
    let err = format!("{:#}", read_approaches_csv(&partial).unwrap_err());
    assert!(
        err.contains(&format!("{} из {}", FLOW_N + LEVEL_N - 1, FLOW_N + LEVEL_N)),
        "{err}"
    );
    assert!(
        err.contains("since_far_ms"),
        "названа недостающая колонка: {err}"
    );

    // Не целое в клетке R1 — отказ с названием колонки.
    let bad = dir.path().join("bad-cell.csv");
    let mut w = csv::Writer::from_path(&bad).unwrap();
    w.write_record(&header).unwrap();
    let mut row = raw[0].clone();
    let at = APPROACHES_COLUMNS.len() + 5;
    row[at] = "1.5".to_string();
    w.write_record(&row).unwrap();
    w.flush().unwrap();
    let err = format!("{:#}", read_approaches_csv(&bad).unwrap_err());
    assert!(err.contains(FLOW_NAMES[5]), "{err}");
}

/// TK-025: `--r1-cols` дописывает 61 колонку (FLOW_N + LEVEL_N) в КОНЕЦ шапки после колонок П-08, а без флага шапка и
/// строки — прежние байты (файл касаний флаг не трогает вовсе).
#[test]
fn r1_cols_flag_appends_the_61_columns_and_keeps_the_old_bytes() {
    let dir = tempfile::tempdir().unwrap();
    write_day(dir.path(), "SOLUSDT", "2026-09-08", &touch_frames());
    let run = |sub: &str, r1_cols: bool| {
        let mut a = touches_args(dir.path());
        a.approach_bps = vec![700];
        a.r1_cols = r1_cols;
        a.out = Some(dir.path().join(sub).join("touches-SOLUSDT.csv"));
        run_touches(&a).unwrap()
    };
    let off = run("off", false);
    let on = run("on", true);
    assert_eq!(off.approaches, 1);
    assert_eq!(on.approaches, 1);
    assert_eq!(
        std::fs::read(&off.out).unwrap(),
        std::fs::read(&on.out).unwrap(),
        "файл касаний флаг не меняет"
    );

    // Без флага — ровно шапка и строки подхода, без единой колонки R1.
    let cfg = LevelsConfig {
        mode: crate::lob::levels::H3Mode::Percentile { h3_lots: 5 },
        warmup_ms: 0,
        repeat_window_ms: 3_600_000,
        approach_bps: Some(700),
        approach_min_age_ms: 0,
    };
    let replay = replay_symbol(dir.path(), "SOLUSDT", cfg).unwrap();
    let records: Vec<ApproachRecord> = replay
        .days
        .iter()
        .flat_map(|d| d.approaches.iter().copied())
        .collect();
    let expected = dir.path().join("expected-old.csv");
    write_approach_csv(&expected, &records, false);
    assert_eq!(
        std::fs::read(&off.approaches_out[0]).unwrap(),
        std::fs::read(&expected).unwrap(),
        "без --r1-cols файл подходов — прежние байты"
    );

    let (h_off, r_off) = read_rows(&off.approaches_out[0]);
    let (h_on, r_on) = read_rows(&on.approaches_out[0]);
    let old = APPROACHES_COLUMNS.len();
    assert_eq!(h_off, APPROACHES_COLUMNS.map(str::to_string).to_vec());
    assert_eq!(h_on.len(), old + FLOW_N + LEVEL_N);
    assert_eq!(h_on[..old], h_off[..], "прежние колонки на прежних местах");
    let names: Vec<String> = ArmR1::names().map(str::to_string).collect();
    assert_eq!(
        h_on[old..],
        names[..],
        "61 колонка R1 в конце, в порядке ArmR1::names()"
    );
    assert_eq!(r_on.len(), r_off.len());
    for (a, b) in r_on.iter().zip(&r_off) {
        assert_eq!(a.len(), old + FLOW_N + LEVEL_N);
        assert_eq!(a[..old], b[..]);
    }

    // Чтение: с колонками — запись R1 у каждой строки, без — None.
    assert!(read_approaches_csv(&on.approaches_out[0])
        .unwrap()
        .iter()
        .all(|r| r.approach.r1.is_some()));
    assert!(read_approaches_csv(&off.approaches_out[0])
        .unwrap()
        .iter()
        .all(|r| r.approach.r1.is_none()));
}

/// TK-025: `--r1-cols` без `--approach-bps` — отказ (колонкам R1 некуда лечь), как и другие флаги подхода.
#[test]
fn r1_cols_without_approach_bps_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    write_day(dir.path(), "SOLUSDT", "2026-09-08", &touch_frames());
    let mut a = touches_args(dir.path());
    a.r1_cols = true;
    let err = run_touches(&a).unwrap_err().to_string();
    assert!(err.contains("--r1-cols"), "{err}");
}

/// Аргументы `lob levels` с теми же `--h3-*`/`--warmup-ms`/`--repeat-window-ms`, что у касаний:
/// эталон `--levels-out` (T-14).
fn levels_args_like(t: &TouchesArgs, out: std::path::PathBuf) -> LevelsArgs {
    LevelsArgs {
        root: t.root.clone(),
        symbol: t.symbol.clone(),
        h3: t.h3,
        h3_k: t.h3_k,
        warmup_ms: t.warmup_ms,
        repeat_window_ms: t.repeat_window_ms,
        out: Some(out),
    }
}

/// T-14 (план «один проход», условия Судьи 1 и 3): `lob touches --levels-out` пишет те же
/// байты, что `lob levels` при тех же флагах, — с полосой подхода и без, при окне
/// `repeat_count` длиннее прогона (строка `# debug` в шапке) и короче его (строки нет).
#[test]
fn levels_out_matches_lob_levels_bytes() {
    for (name, frames, min_levels) in [
        ("three", three_level_frames(), 4usize),
        ("touch", touch_frames(), 1),
    ] {
        let dir = tempfile::tempdir().unwrap();
        write_day(dir.path(), "SOLUSDT", "2026-09-08", &frames);
        for window_ms in [3_600_000i64, 1] {
            for bands in [Vec::new(), vec![700i64]] {
                let tag = format!("{name}-w{window_ms}-b{}", bands.len());
                let mut t = touches_args(dir.path());
                t.repeat_window_ms = window_ms;
                t.approach_bps = bands;
                t.out = Some(dir.path().join(&tag).join("touches-SOLUSDT.csv"));
                let via_touches = dir.path().join(&tag).join("levels-via-touches.csv");
                t.levels_out = Some(via_touches.clone());
                let ts = run_touches(&t).unwrap();
                let reference = dir.path().join(&tag).join("levels-reference.csv");
                let ls = run_levels(&levels_args_like(&t, reference.clone())).unwrap();
                let a = std::fs::read(&via_touches).unwrap();
                let b = std::fs::read(&reference).unwrap();
                assert_eq!(a, b, "{tag}: уровни из прохода касаний ≠ lob levels");
                assert_eq!(ts.levels_out, Some((via_touches, ls.levels)), "{tag}");
                assert!(
                    ls.levels >= min_levels,
                    "{tag}: фикстура без уровней — сравнение пустое"
                );
                let text = String::from_utf8(a).unwrap();
                assert_eq!(
                    text.starts_with("# lob levels: debug"),
                    window_ms == 3_600_000,
                    "{tag}: строка debug обязана стоять ровно при окне длиннее прогона"
                );
            }
        }
    }
}

/// T-14: без `--levels-out` файла уровней нет и итог его не называет — прежнее поведение.
#[test]
fn levels_out_absent_writes_no_levels_file() {
    let dir = tempfile::tempdir().unwrap();
    write_day(dir.path(), "SOLUSDT", "2026-09-08", &three_level_frames());
    let summary = run_touches(&touches_args(dir.path())).unwrap();
    assert_eq!(summary.levels_out, None);
    assert!(!dir.path().join("levels-SOLUSDT.csv").exists());
}

/// T-14 (условие Судьи 2): там, где эталона `lob levels` нет, — отказ до реплея, и отказ
/// называет `--levels-out`.
#[test]
fn levels_out_refuses_without_reference() {
    let dir = tempfile::tempdir().unwrap();
    write_day(dir.path(), "SOLUSDT", "2026-09-08", &three_level_frames());
    let with_levels = || {
        let mut t = touches_args(dir.path());
        t.levels_out = Some(dir.path().join("levels-x.csv"));
        t
    };
    let mut carry = with_levels();
    carry.carry_age = true;
    let mut emit = with_levels();
    emit.emit_day = Some("2026-09-08".to_string());
    let mut bands = with_levels();
    bands.approach_bps = vec![700, 300];
    for (what, args) in [("carry", carry), ("emit", emit), ("bands", bands)] {
        let err = run_touches(&args).expect_err(what).to_string();
        assert!(err.contains("--levels-out"), "{what}: {err}");
    }
    assert!(
        !dir.path().join("levels-x.csv").exists(),
        "отказ — до реплея и записи"
    );
}

/// TK-012: `--minute-flow <каталог>` пишет `minute-flow-<SYMBOL>.csv` со строкой на минуту
/// с кадром, а касания — те же байты, что без флага.
#[test]
fn minute_flow_dir_writes_series_and_keeps_touch_bytes() {
    let dir = tempfile::tempdir().unwrap();
    write_day(dir.path(), "SOLUSDT", "2026-09-08", &touch_frames());
    let touches = dir.path().join("touches-SOLUSDT.csv");
    run_touches(&touches_args(dir.path())).unwrap();
    let before = std::fs::read(&touches).unwrap();
    let flow_dir = dir.path().join("flow");
    std::fs::create_dir_all(&flow_dir).unwrap();
    let mut a = touches_args(dir.path());
    a.minute_flow = Some(flow_dir.clone());
    run_touches(&a).unwrap();
    assert_eq!(std::fs::read(&touches).unwrap(), before);
    let (header, rows) = read_rows(&flow_dir.join("minute-flow-SOLUSDT.csv"));
    assert_eq!(
        header,
        [
            "minute_ms",
            "symbol",
            "add_lots",
            "cancel_lots",
            "trade_lots"
        ]
    );
    assert!(!rows.is_empty());
    let mut prev = i64::MIN;
    for r in &rows {
        let m: i64 = r[0].parse().unwrap();
        assert_eq!(m.rem_euclid(60_000), 0);
        assert!(m > prev);
        prev = m;
        assert_eq!(r[1], "SOLUSDT");
        for v in &r[2..] {
            assert!(v.parse::<i64>().unwrap() >= 0);
        }
    }
}

#[test]
fn abin_roundtrip_matches_records() {
    use crate::lob::levels::ArmP08;
    let mut rows = Vec::new();
    for i in 0..50_i64 {
        let mut a = minimal_approach();
        a.side = if i % 2 == 0 { Side::Bid } else { Side::Ask };
        a.price_tick = 100 + i;
        a.arm_ms = 1_000 + i * 37;
        a.level_birth_ms = 900 - i;
        a.strength_e2 = [i * 7 - 1, -1, 12_345];
        a.touch_start_ms = (i % 3 == 0).then_some(a.arm_ms + 5);
        a.disarm_reason = [
            ApproachEnd::Touch,
            ApproachEnd::LevelDeath,
            ApproachEnd::PriceLeft,
        ][(i % 3) as usize];
        a.depth_behind_lots = i * 1_000_003;
        a.frontrun_lots_at_arm = if i == 5 { -1 } else { i };
        a.p08 = (i % 2 == 0).then_some(ArmP08 {
            traded_lots: i,
            size_max: i * 3,
            size_monotonic: i % 4 == 0,
            eat_60s_lots: 0,
            size_max_60s_lots: 9,
            depth_behind50_lots: -2,
        });
        rows.push(ApproachRow {
            day: "2026-01-01".into(),
            approach: a,
        });
    }
    // p08 то есть то нет в файле — не кэшируется (формат либо у всех, либо ни у кого)
    assert!(abin::encode_file(&rows, 7, 8).is_none());
    for r in &mut rows {
        r.approach.p08 = Some(ArmP08::default());
    }
    let bytes = abin::encode_file(&rows, 7, 8).unwrap();
    assert_eq!(abin::decode_file(&bytes, 7, 8).unwrap(), rows);
    assert!(
        abin::decode_file(&bytes, 7, 9).is_none(),
        "другой mtime — кэш негоден"
    );
    assert!(
        abin::decode_file(&bytes, 6, 8).is_none(),
        "другой размер — кэш негоден"
    );
    for r in &mut rows {
        r.approach.p08 = None;
    }
    let bytes = abin::encode_file(&rows, 7, 8).unwrap();
    assert_eq!(abin::decode_file(&bytes, 7, 8).unwrap(), rows);
}
