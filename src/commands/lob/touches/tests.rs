use super::*;
use crate::book::Side;
use crate::commands::lob::levels::{run_levels, LevelsArgs};
use crate::commands::lob::replay::ReplayDay;
use crate::commands::lob::replay_symbol;
use crate::commands::lob::test_support::{three_level_frames, touch_frames, write_day};
use crate::commands::lob::H3ModeArg;
use crate::lob::excursion::SecondMids;
use crate::lob::levels::{ApproachEnd, ApproachRecord, LevelsConfig, TouchRecord};
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
