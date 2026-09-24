use super::*;
use crate::commands::lob::test_support::{
    delta_frame, snap_frame, trade_frame, write_day_part,
    write_session_json as write_session_json_value,
};

/// Двойник прежней сигнатуры `build_dashboard` (до W7, ревью 23.09): та
/// собирала `CoinChart` каждой монеты в `Vec` и отдавала его вызывающему
/// вместе со сводкой — теперь `build_dashboard` отдаёт график через
/// `on_chart` сразу после расчёта (в бою — пишет на диск, `render_once`), а
/// тесты по-прежнему проверяют числа без ввода-вывода: здесь `on_chart`
/// просто копит графики в `Vec`, как раньше.
fn build_dashboard_collecting(args: &DashboardArgs) -> anyhow::Result<(Dashboard, Vec<CoinChart>)> {
    let mut charts = Vec::new();
    let dashboard = build_dashboard(args, |c| {
        charts.push(c);
        Ok(())
    })?;
    Ok((dashboard, charts))
}

fn write_instruments_csv(root: &Path, symbols: &[&str]) {
    let mut s = String::from(
        "symbol,turnover_usd_e9,tick_e9,step_e9,h3_lots,k,median_trade_lots,\
         window_start_utc_ms,window_secs,final_rank,selected_for_pilot\n",
    );
    for (i, sym) in symbols.iter().enumerate() {
        s.push_str(&format!(
            "{sym},1,10000000,1000000,5,1.0,5,0,3600,{},true\n",
            i + 1
        ));
    }
    std::fs::write(root.join("instruments.csv"), s).unwrap();
}

fn write_session_json(root: &Path, symbols: &[&str], started: &str, closed: bool) {
    let json = serde_json::json!({
        "started_utc": started,
        "start_hour_utc": 12,
        "duration_s": 3600u64,
        "instruments": symbols,
        "records_total": 1000u64,
        "gaps": 0u64,
        "clock_samples": 1u64,
        "parse_p99_ns": 50_000i64,
        "queue_p99_ns": 300_000i64,
        "cpu_pct_avg": 1.9,
        "cpu_pct_max": 3.5,
        "rss_bytes_start": 5_000_000u64,
        "rss_bytes_end": 6_000_000u64,
        "out": root.display().to_string(),
        "debug": false,
        "pilot": false,
        "pilot_minutes": serde_json::Value::Null,
        "always_on": true,
        "reconnects": 0u64,
        "resyncs": 0u64,
        "frames_failed": 0u64,
        "bytes_written": 4096u64,
        "updated_utc": started,
        "closed": closed,
        "samples": [{"ts_utc": started, "rss_bytes": 6_000_000u64, "cpu_pct": 2.0}],
        "binlog_files": [
            {"symbol": symbols[0], "part": 1, "started_utc": started},
        ],
    });
    write_session_json_value(root, &json);
}

/// Книга 100.00/100.01 (тик 0.01), порог 5 лотов. Уровень на 10000
/// (бид, 10 лотов, 2×H3) живёт с 0 до 20 с и проедается сделкой в 8
/// лотов; уровень на 9999 (бид, 10 лотов) живёт с 0 и снимается на 30 с;
/// уровень на 10002 (аск, 30 лотов, 6×H3) рождается на 25 с и **жив** до
/// конца записи (60 с), как и аск 10001 (10 лотов) с нуля.
fn frames() -> Vec<Vec<crate::commands::lob::Record>> {
    vec![
        snap_frame(0, &[(9998, 1), (9999, 10), (10000, 10)], &[(10001, 10)]),
        trade_frame(10_000, 10000, 8),
        delta_frame(20_000, &[(10000, 1)], &[]),
        delta_frame(25_000, &[], &[(10002, 30)]),
        delta_frame(30_000, &[(9999, 1)], &[]),
        delta_frame(60_000, &[(9998, 2)], &[]),
    ]
}

/// Касания (таск 36). Бид 10000 родился лучшей ценой — не касание (В-43)
/// — и снят на 5 с; бид 9999 (10 лотов, с нуля) стал лучшим — касание 0
/// (фронтран — 10 лотов бида 10000 с прошлого кадра, доля 1.0). На 8 с
/// перед ним встали 3 лота на 10000 (не уровень, < H3) — касание кончилось
/// уходом с лучшей цены: **отскок**, 3 с. На 12 с 10000 снят — касание 1 у
/// 9999 (фронтран 3 лота, доля 0.3); продавец бьёт 8 лотов, на 13 с 9999
/// упал до 1 лота — смерть уровня: **проели на касании**, 1 с. Бид 9998 —
/// 1 лот, не уровень, касаний не даёт; аск 10001 жив до конца (60 с), где
/// 9999 убран и середина ушла на полтика вниз.
fn touch_frames() -> Vec<Vec<crate::commands::lob::Record>> {
    vec![
        snap_frame(0, &[(9998, 1), (9999, 10), (10000, 10)], &[(10001, 10)]),
        delta_frame(5_000, &[(10000, 0)], &[]),
        delta_frame(8_000, &[(10000, 3)], &[]),
        delta_frame(12_000, &[(10000, 0)], &[]),
        trade_frame(12_500, 9999, 8),
        delta_frame(13_000, &[(9999, 1)], &[]),
        delta_frame(60_000, &[(9999, 0), (9998, 2)], &[]),
    ]
}

/// Ни один уровень не становился лучшей ценой после рождения — касаний нет.
fn no_touch_frames() -> Vec<Vec<crate::commands::lob::Record>> {
    vec![
        snap_frame(0, &[(10000, 10)], &[(10001, 10)]),
        delta_frame(60_000, &[], &[(10002, 10)]),
    ]
}

fn fixture(dir: &Path) -> DashboardArgs {
    fixture_with(dir, &frames())
}

fn fixture_with(dir: &Path, frames: &[Vec<crate::commands::lob::Record>]) -> DashboardArgs {
    write_instruments_csv(dir, &["SOLUSDT"]);
    write_session_json(dir, &["SOLUSDT"], "2026-09-12T12:00:00Z", false);
    write_day_part(dir, "SOLUSDT", "2026-09-12", 1, frames);
    DashboardArgs {
        root: dir.to_path_buf(),
        out: dir.join("out"),
        h3_mode: H3ModeArg::Floor,
        h3_lots: None,
        h3_k: None,
        watch: None,
    }
}

/// Живой уровень на последнем кадре попадает в «плотности сейчас» с
/// расстоянием, размером и возрастом от последней середины; умершие —
/// в исходы; полоски картины несут все три.
#[test]
fn coin_page_shows_live_levels_outcomes_and_bars() {
    let dir = tempfile::tempdir().unwrap();
    let args = fixture(dir.path());
    let (d, charts) = build_dashboard_collecting(&args).expect("расчёт обязан пройти на фикстуре");
    assert_eq!(d.coins.len(), 1);
    let c = &d.coins[0];
    assert!(
        c.error.is_none(),
        "реплей фикстуры обязан пройти: {:?}",
        c.error
    );
    assert_eq!(c.h3_lots, 5);
    assert_eq!(c.price_decimals, 2, "тик 0.01 — два знака");
    assert_eq!(
        c.bid,
        Some(100.0),
        "лучший бид на последнем кадре — 10000 тиков"
    );
    assert_eq!(c.ask, Some(100.01));

    // Живы два аска: 10001 (10 лотов, с нуля) и 10002 (30 лотов, с 25 с);
    // самый крупный — первый.
    assert_eq!(c.levels_now.len(), 2, "{:?}", c.levels_now);
    assert_eq!(c.levels_now[1].size_lots, 10);
    let now = &c.levels_now[0];
    assert_eq!(now.side, "ask");
    assert_eq!(now.size_lots, 30);
    assert!((now.size_x_h3 - 6.0).abs() < 1e-9);
    assert!(now.distance_bps.unwrap() > 0.0);
    assert_eq!(now.age_secs, 35, "родился на 25 с, последний кадр 60 с");

    assert_eq!(c.levels_total, 2, "два умерших уровня");
    let by = |l: &str| c.outcomes.iter().find(|r| r.label == l).unwrap().n;
    assert_eq!(by("eaten"), 1);
    assert_eq!(by("pulled"), 1);
    assert_eq!(c.all.n, 2);

    let ch = &charts[0];
    let alive = ch.bars.iter().filter(|b| b.o == ALIVE_CODE).count();
    assert_eq!(alive, 2);
    assert_eq!(ch.bars.len(), 4, "два умерших и два живых");
    assert_eq!(c.chart_bars, 4);
    assert_eq!(c.chart_file, "coin-SOLUSDT.json");
    assert_eq!(ch.to_ms, 60_000);
    assert!(!ch.mid.is_empty());
}

fn touch_row<'a>(rows: &'a [TouchRow], label: &str) -> &'a TouchRow {
    rows.iter()
        .find(|r| r.label == label)
        .unwrap_or_else(|| panic!("нет строки {label}"))
}

/// Критерий приёмки таска 36: одно касание-отскок и одно касание-проезд →
/// `touches.total = 2`, доли, строки осей В-44 с `n`, крест исход × возраст
/// 2 × 3, метки на картине с числами наведения.
#[test]
fn touches_block_counts_a_bounce_and_a_death_with_axes_cross_and_marks() {
    let dir = tempfile::tempdir().unwrap();
    let args = fixture_with(dir.path(), &touch_frames());
    let (d, charts) = build_dashboard_collecting(&args).expect("расчёт обязан пройти на фикстуре");
    let c = &d.coins[0];
    assert!(c.error.is_none(), "{:?}", c.error);
    let t = &c.touches;

    // Итог.
    assert_eq!(t.total, 2, "два касания бида 9999");
    assert_eq!(t.bounced, 1);
    assert_eq!(t.eaten, 1);
    assert_eq!(t.share_bounced, Some(0.5));
    assert_eq!(t.per_hour, Some(120.0), "2 касания за минуту записи");
    assert_eq!(
        t.outcomes
            .iter()
            .map(|r| r.label.as_str())
            .collect::<Vec<_>>(),
        TOUCH_OUTCOME_LABELS.to_vec()
    );
    assert_eq!(touch_row(&t.outcomes, "bounced").n, 1);
    assert_eq!(touch_row(&t.outcomes, "eaten").n, 1);
    assert_eq!(touch_row(&t.outcomes, "bounced").share_bounced, Some(1.0));
    assert_eq!(touch_row(&t.outcomes, "eaten").share_bounced, Some(0.0));
    assert_eq!(t.all.n, 2);
    assert_eq!(t.all.share, Some(1.0));
    // База — середина на момент касания (В-43): через 100 мс и 10 с
    // середина та же (9999 остался лучшим бидом и после смерти уровня —
    // 1 лот на цене) — ноль; на 60 с 9999 убран, середина ушла на полтика
    // вниз — против отскока бида: минус.
    // Касания длились 3 с и 1 с: горизонты 100 мс и 1 с — внутри касания,
    // в средние не входят (В-45 (2)); 10 с и 60 с — снаружи.
    assert_eq!(t.within_touch, [2, 2, 0, 0]);
    assert_eq!(t.all.m_bps[0], None, "оба касания длиннее 100 мс");
    assert_eq!(t.all.m_bps[1], None, "оба касания не короче 1 с");
    assert_eq!(t.all.m_bps[H10S], Some(0.0));
    assert!(t.all.m_bps[3].unwrap() < 0.0, "{:?}", t.all.m_bps);
    assert_eq!(t.all.m10s_n, 2);
    assert_eq!(t.all.m10s_bounced_n, 1);
    assert_eq!(
        t.stack_median,
        Some(1.0),
        "на касании жив один уровень ≥ H3 в окне 25 bps"
    );
    assert!(t.stack_shown);
    assert_eq!(t.stack_window_bps, 25);
    assert!(
        t.k_stub,
        "k = 1.0 в instruments.csv фикстуры — заглушка (В-30)"
    );
    assert_eq!(t.approach_ms, APPROACH_MS);

    // Оси В-44: у каждой все метки в порядке `touch_axes`, сумма `n` — 2.
    let labels = |rows: &[TouchRow]| rows.iter().map(|r| r.label.clone()).collect::<Vec<_>>();
    assert_eq!(labels(&t.by_age), AGE_LABELS.to_vec());
    assert_eq!(labels(&t.by_frontrun), FRONTRUN_LABELS.to_vec());
    assert_eq!(labels(&t.by_round), ROUND_LABELS.to_vec());
    assert_eq!(labels(&t.by_index), TOUCH_INDEX_LABELS.to_vec());
    assert_eq!(labels(&t.by_approach), APPROACH_LABELS.to_vec());
    assert_eq!(labels(&t.by_duration), LIFETIME_LABELS.to_vec());
    assert_eq!(labels(&t.by_side), SIDE_LABELS.to_vec());
    assert_eq!(labels(&t.by_size), SIZE_LABELS.to_vec());
    assert_eq!(touch_row(&t.by_age, "[0,10m)").n, 2, "возраст 5 с и 12 с");
    assert_eq!(
        touch_row(&t.by_frontrun, "[0.5,inf)").n,
        1,
        "10 лотов из 10"
    );
    assert_eq!(touch_row(&t.by_frontrun, "(0,0.5)").n, 1, "3 лота из 10");
    assert_eq!(touch_row(&t.by_frontrun, "0").n, 0);
    assert_eq!(touch_row(&t.by_round, "0").n, 2, "9999 — нулей нет");
    assert_eq!(touch_row(&t.by_index, "1").n, 1);
    assert_eq!(touch_row(&t.by_index, "2-3").n, 1);
    assert_eq!(
        touch_row(&t.by_approach, "[0,1)").n,
        2,
        "за секунду до касания середина шла на бид на полтика: {:?}",
        t.by_approach
    );
    assert_eq!(t.approach_missing, 0);
    assert_eq!(touch_row(&t.by_duration, "[1s,10s)").n, 2, "3 с и 1 с");
    assert_eq!(touch_row(&t.by_side, "bid").n, 2);
    assert_eq!(touch_row(&t.by_size, "[2,4)").n, 2, "10 лотов при пороге 5");
    assert_eq!(t.size_below_h3, 0);
    let bounced_age = touch_row(&t.by_age, "[0,10m)");
    assert_eq!(bounced_age.share_bounced, Some(0.5));
    assert_eq!(bounced_age.m10s_bounced_n, 1);

    // Крест исход × возраст: 2 × 3 клеток, по одному касанию в первом столбце.
    assert_eq!(t.cross_outcome_age.len(), 6);
    let cell = |o: &str, a: &str| {
        t.cross_outcome_age
            .iter()
            .find(|c| c.outcome == o && c.age == a)
            .unwrap()
    };
    assert_eq!(cell("bounced", "[0,10m)").n, 1);
    assert_eq!(cell("bounced", "[0,10m)").share, Some(0.5));
    assert_eq!(cell("eaten", "[0,10m)").n, 1);
    assert_eq!(cell("eaten", "[1h,inf)").n, 0);
    assert_eq!(cell("bounced", "[10m,1h)").m10s_n, 0);

    // Метки на графике: обе в файле, с числами наведения; `t0 = 0` —
    // смещения равны меткам.
    let p = &charts[0];
    assert_eq!(p.t0, 0);
    assert_eq!(p.touches.len(), 2);
    assert_eq!(c.chart_touches, 2);
    let bounce = p
        .touches
        .iter()
        .find(|m| m.o == touch_outcome_code(false))
        .unwrap();
    assert_eq!(bounce.t, 5_000);
    assert_eq!(bounce.s, side_code(Side::Bid));
    assert_eq!(bounce.p, 99.99);
    assert_eq!(bounce.i, 0);
    assert_eq!(bounce.a, 5_000);
    assert_eq!(bounce.d, 3_000);
    assert_eq!(bounce.fr, Some(1.0));
    assert_eq!(
        bounce.sw,
        Some(1.0),
        "кадры реже секунды: сметено = фронтран"
    );
    assert!(bounce.ap.unwrap() > 0.0);
    assert_eq!(bounce.m, Some(0.0));
    assert!(
        bounce.d < HORIZONS_MS[H10S],
        "3 с < 10 с — горизонт снаружи касания"
    );
    assert!((bounce.x - 2.0).abs() < 1e-9);
    let death = p
        .touches
        .iter()
        .find(|m| m.o == touch_outcome_code(true))
        .unwrap();
    assert_eq!(death.t, 12_000);
    assert_eq!(death.i, 1);
    assert_eq!(death.d, 1_000);
    assert!((death.fr.unwrap() - 0.3).abs() < 1e-9);
    assert!((death.sw.unwrap() - 0.3).abs() < 1e-9);

    // Уровни при этом — как прежде: 10000 сняли, 9999 проели, аск жив.
    assert_eq!(c.levels_total, 2);
    assert_eq!(c.levels_now.len(), 1);
}

/// Критерий приёмки тикета 35b (В-45): касание без фронтрана — бид 9999
/// родился лучшим (не касание), на 5 с перед ним встали 3 лота на 10000, на
/// 5,2 с ушли — касание, за секунду до которого впереди не было ничего:
/// строка корзины `0` с `n = 1`, а сметено последним шагом 0.3 размера — в
/// наведении на метку. Касание длилось 12,8 с — горизонт 10 с внутри него:
/// `m 10 с` метки не печатается, в среднее не входит.
#[test]
fn a_touch_without_frontrun_lands_in_the_zero_bucket_and_swept_is_shown_separately() {
    let dir = tempfile::tempdir().unwrap();
    let frames = vec![
        snap_frame(0, &[(9999, 10)], &[(10001, 10)]),
        delta_frame(5_000, &[(10000, 3)], &[]),
        delta_frame(5_200, &[(10000, 0)], &[]),
        delta_frame(18_000, &[(10000, 3)], &[]),
        delta_frame(60_000, &[(10000, 0)], &[]),
    ];
    let args = fixture_with(dir.path(), &frames);
    let (d, charts) = build_dashboard_collecting(&args).expect("расчёт обязан пройти на фикстуре");
    let c = &d.coins[0];
    assert!(c.error.is_none(), "{:?}", c.error);
    let t = &c.touches;
    assert_eq!(t.total, 1);
    assert_eq!(touch_row(&t.by_frontrun, "0").n, 1, "{:?}", t.by_frontrun);
    assert_eq!(touch_row(&t.by_frontrun, "(0,0.5)").n, 0);
    assert_eq!(t.within_touch, [1, 1, 1, 0]);
    assert_eq!(t.all.m_bps[H10S], None, "10 с внутри касания в 12,8 с");
    assert_eq!(t.all.m10s_n, 0);
    let m = &charts[0].touches[0];
    assert_eq!(m.fr, Some(0.0));
    assert!((m.sw.unwrap() - 0.3).abs() < 1e-9, "{:?}", m.sw);
    assert!(
        m.d >= HORIZONS_MS[H10S],
        "10 с внутри касания — страница печатает «внутри касания» по d"
    );
    assert_eq!(m.m, None);
}

/// Без касаний — блок с нулями и всеми метками, не ошибка; меток на картине нет.
#[test]
fn a_coin_without_touches_gets_a_zero_block_not_an_error() {
    let dir = tempfile::tempdir().unwrap();
    let args = fixture_with(dir.path(), &no_touch_frames());
    let (d, charts) = build_dashboard_collecting(&args).unwrap();
    let c = &d.coins[0];
    assert!(c.error.is_none(), "{:?}", c.error);
    let t = &c.touches;
    assert_eq!(t.total, 0);
    assert_eq!(t.bounced, 0);
    assert_eq!(t.share_bounced, None);
    assert_eq!(t.per_hour, Some(0.0));
    assert_eq!(t.all.n, 0);
    assert_eq!(t.all.m_bps, [None; 4]);
    assert_eq!(t.stack_median, None, "медианы пустого набора нет");
    assert_eq!(t.within_touch, [0; 4]);
    assert_eq!(t.outcomes.len(), 2);
    for rows in [
        &t.by_age,
        &t.by_frontrun,
        &t.by_round,
        &t.by_index,
        &t.by_approach,
        &t.by_duration,
        &t.by_side,
        &t.by_size,
    ] {
        assert!(!rows.is_empty());
        assert!(rows.iter().all(|r| r.n == 0 && r.share.is_none()));
    }
    assert_eq!(t.cross_outcome_age.len(), 6);
    assert!(t.cross_outcome_age.iter().all(|c| c.n == 0));
    assert!(charts[0].touches.is_empty());
    assert_eq!(c.chart_touches, 0);
}

/// Оба файла лежат на диске, JSON разбирается обратно в те же типы, а
/// временных файлов после записи не остаётся.
#[test]
fn renders_both_files_atomically_and_json_round_trips() {
    let dir = tempfile::tempdir().unwrap();
    let args = fixture(dir.path());
    let s = run_dashboard(&args).unwrap();
    assert!(s.json.exists() && s.html.exists());
    assert!(!args.out.join("data.tmp").exists());
    assert!(!args.out.join("index.tmp").exists());
    let raw = std::fs::read_to_string(&s.json).unwrap();
    let d: Dashboard = serde_json::from_str(&raw).unwrap();
    assert_eq!(d.coins[0].symbol, "SOLUSDT");
    assert_eq!(d.exec_latency, ExecLatencyMs::measured());
    assert_eq!(d.exec_latency.place_ms, 4.2);
    assert_eq!(d.exec_latency.source, "measured(lob latency, В-68)");
    assert_eq!(d.roundtrip_fees_bps, ROUNDTRIP_FEES_BPS);
    assert_eq!(s.coins, 1);
    // Касания едут в `data.json` теми же типами: у `frames()` касаний нет
    // (10000 после проедания остался в книге одним лотом — лучшей ценой,
    // 9999 лучшим не становился), блок с нулями и всеми метками.
    assert_eq!(d.coins[0].touches.total, 0);
    assert_eq!(d.coins[0].touches.by_approach.len(), APPROACH_LABELS.len());
    assert!(d.glossary.iter().any(|g| g.title.starts_with("Касание")));
    // Файл графика — тоже атомарно и теми же типами обратно.
    let chart_path = args.out.join(&d.coins[0].chart_file);
    assert!(chart_path.exists(), "{}", chart_path.display());
    assert!(!args.out.join("coin-SOLUSDT.tmp").exists());
    let ch: CoinChart =
        serde_json::from_str(&std::fs::read_to_string(&chart_path).unwrap()).unwrap();
    assert_eq!(ch.symbol, "SOLUSDT");
    assert_eq!(ch.bars.len(), d.coins[0].chart_bars);
    assert!(ch.touches.is_empty());
}

/// Жив/нет: первый расчёт — «не знаю», второй с выросшими бинлогами —
/// «пишет», без роста — «не пишет»; чужой `--root` в том же `--out` за
/// базу не берётся.
#[test]
fn liveness_comes_from_binlog_growth_between_two_renders() {
    let dir = tempfile::tempdir().unwrap();
    let args = fixture(dir.path());
    let first = run_dashboard(&args).unwrap();
    assert_eq!(first.alive, None);

    let second = run_dashboard(&args).unwrap();
    assert_eq!(second.alive, Some(false), "бинлоги не выросли");

    // Дописали часть — выросли.
    write_day_part(dir.path(), "SOLUSDT", "2026-09-12", 2, &frames());
    let third = run_dashboard(&args).unwrap();
    assert_eq!(third.alive, Some(true), "{}", third.alive_reason);

    // Другой каталог в тот же --out: базы нет.
    let other = tempfile::tempdir().unwrap();
    let mut args2 = fixture(other.path());
    args2.out = args.out.clone();
    let fourth = run_dashboard(&args2).unwrap();
    assert_eq!(fourth.alive, None, "чужой data.json не база");
}

/// `closed = true` — «остановлен», независимо от роста.
#[test]
fn closed_session_is_reported_as_stopped() {
    let dir = tempfile::tempdir().unwrap();
    let args = fixture(dir.path());
    write_session_json(dir.path(), &["SOLUSDT"], "2026-09-12T12:00:00Z", true);
    let s = run_dashboard(&args).unwrap();
    assert_eq!(s.alive, Some(false));
    assert!(s.alive_reason.contains("остановлен"), "{}", s.alive_reason);
}

/// Монета без бинлога не роняет страницу: строка с ошибкой, соседи целы.
#[test]
fn a_coin_without_binlog_is_reported_not_dropped() {
    let dir = tempfile::tempdir().unwrap();
    let mut args = fixture(dir.path());
    write_instruments_csv(dir.path(), &["SOLUSDT", "XRPUSDT"]);
    write_session_json(
        dir.path(),
        &["SOLUSDT", "XRPUSDT"],
        "2026-09-12T12:00:00Z",
        false,
    );
    args.h3_k = None;
    let (d, charts) = build_dashboard_collecting(&args).unwrap();
    assert_eq!(d.coins.len(), 2);
    assert_eq!(charts.len(), 2, "файл графика — и у непрочитанной монеты");
    assert!(charts[1].error.is_some());
    assert!(charts[1].mid.is_empty());
    assert!(d.coins[0].error.is_none());
    let e = d.coins[1]
        .error
        .as_deref()
        .expect("XRPUSDT без файла — ошибка");
    assert!(e.contains("XRPUSDT"), "{e}");
    assert_eq!(
        d.coins[1].touches.total, 0,
        "блок касаний с нулями, не паника"
    );
    assert_eq!(d.coins[1].touches.by_age.len(), AGE_LABELS.len());
}

/// Критерий приёмки: страница самодостаточна — ни одного внешнего URL,
/// и JSON не может закрыть тег `script`; блок касаний и метки на картине
/// есть в шаблоне (таск 36).
#[test]
fn page_is_self_contained_html_without_external_urls() {
    let html = render_html(r#"{"x":"</script><b>"}"#);
    for needle in ["http://", "https://", "src=\"//", "@import"] {
        assert!(
            !html.contains(needle),
            "внешняя ссылка на странице: {needle}"
        );
    }
    assert!(!html.contains("</script><b>"), "JSON закрыл script");
    assert!(html.contains("\\u003c/script>"));
    assert!(html.contains("<title>Монеты и плотности</title>"));
    // Сетка графиков (таск 41, владелец 2026-09-13: «кроме сетки ниче там
    // пока не оставлять»): canvas на плитку, сетка на всю ширину окна —
    // ни одного `max-width` на странице; ни таблиц, ни фильтров, ни
    // развёрнутого вида с таблицами касаний — на странице только сетка.
    for needle in [
        "<canvas",
        "display:grid",
        "grid-template-columns:repeat(var(--cols",
        "chart_file",
        "wheel",
        "dblclick",
    ] {
        assert!(html.contains(needle), "в шаблоне нет {needle}");
    }
    for needle in [
        "<table",
        "<select",
        "<input",
        "<details",
        "touchesHtml",
        "cross_outcome_age",
    ] {
        assert!(!html.contains(needle), "на странице лишнее: {needle}");
    }
    // Ни у `body`, ни у сетки нет `max-width` — плитки идут на всю ширину окна.
    for rule in [".grid{", "body{"] {
        let start = html
            .find(rule)
            .unwrap_or_else(|| panic!("в шаблоне нет правила {rule}"));
        let body = &html[start..start + html[start..].find('}').unwrap()];
        assert!(
            !body.contains("max-width"),
            "{rule} ограничен по ширине: {body}"
        );
    }
    assert!(
        !html.contains("<main"),
        "корневого контейнера с полями нет — сетка на всю ширину"
    );
    assert!(!html.contains("<svg"), "график — canvas, не SVG");
}

#[test]
fn decimals_follow_the_tick() {
    assert_eq!(decimals_of_e9(10_000_000), 2);
    assert_eq!(decimals_of_e9(1_000_000_000), 0);
    assert_eq!(decimals_of_e9(1), 9);
    assert_eq!(decimals_of_e9(0), 0);
}

#[test]
fn gaps_are_counted_from_the_live_csv_without_header() {
    let dir = tempfile::tempdir().unwrap();
    let p = dir.path().join("gaps.csv");
    assert_eq!(gaps_rows(&p), 0);
    std::fs::write(&p, "symbol,kind,ts\n").unwrap();
    assert_eq!(gaps_rows(&p), 0);
    std::fs::write(&p, "symbol,kind,ts\nSOLUSDT,seq,1\nSOLUSDT,seq,2\n").unwrap();
    assert_eq!(gaps_rows(&p), 2);
}

/// Критерий приёмки таска 41: две монеты → два файла графика рядом с
/// `data.json`, массивы компактной формы (`[b, d, p, s, o, x, m]`,
/// `[t, p, s, o, a, x, fr, sw, ap, i, d, m]`, `[мс, цена]`), середина —
/// по **всей** записи в две часа, не по последнему часу; полоска, умершая
/// на 5-й секунде, и касание на 5-й секунде — в файле (прежняя картина
/// часа их бы не увидела).
#[test]
fn two_coins_get_two_chart_files_over_the_whole_recording() {
    let dir = tempfile::tempdir().unwrap();
    // Бид 10000 снят на 5 с — 9999 стал лучшим: касание, отскок на 8 с;
    // дальше два часа тишины и один кадр в конце.
    let long = vec![
        snap_frame(0, &[(9998, 1), (9999, 10), (10000, 10)], &[(10001, 10)]),
        delta_frame(5_000, &[(10000, 0)], &[]),
        delta_frame(8_000, &[(10000, 3)], &[]),
        delta_frame(7_200_000, &[(9998, 2)], &[]),
    ];
    write_instruments_csv(dir.path(), &["SOLUSDT", "XRPUSDT"]);
    write_session_json(
        dir.path(),
        &["SOLUSDT", "XRPUSDT"],
        "2026-09-12T12:00:00Z",
        false,
    );
    write_day_part(dir.path(), "SOLUSDT", "2026-09-12", 1, &long);
    write_day_part(dir.path(), "XRPUSDT", "2026-09-12", 1, &frames());
    let args = DashboardArgs {
        root: dir.path().to_path_buf(),
        out: dir.path().join("out"),
        h3_mode: H3ModeArg::Floor,
        h3_lots: None,
        h3_k: None,
        watch: None,
    };
    let s = run_dashboard(&args).unwrap();
    assert_eq!(s.coins, 2);
    for sym in ["SOLUSDT", "XRPUSDT"] {
        assert!(args.out.join(format!("coin-{sym}.json")).exists());
        assert!(!args.out.join(format!("coin-{sym}.tmp")).exists());
    }

    let raw = std::fs::read_to_string(args.out.join("coin-SOLUSDT.json")).unwrap();
    let v: serde_json::Value = serde_json::from_str(&raw).unwrap();
    assert_eq!(v["symbol"], "SOLUSDT");
    assert_eq!(v["t0"], 0);
    assert_eq!(v["from_ms"], 0);
    assert_eq!(v["to_ms"], 7_200_000);
    assert_eq!(v["sides"], serde_json::json!(["bid", "ask"]));
    assert_eq!(
        v["outcomes"],
        serde_json::json!(["eaten", "pulled", "mixed", "alive"])
    );
    assert_eq!(v["touch_outcomes"], serde_json::json!(["bounced", "eaten"]));
    let mid = v["mid"].as_array().unwrap();
    assert_eq!(mid.len(), 4, "четыре кадра — четыре секунды со срезом");
    assert_eq!(mid[0].as_array().unwrap().len(), 2);
    assert_eq!(mid[0][0], 0);
    assert_eq!(mid[3][0], 7_200_000, "середина — до конца записи, не час");
    let bars = v["bars"].as_array().unwrap();
    assert!(bars.iter().all(|b| b.as_array().unwrap().len() == 7));
    // Снятый на 5 с бид 10000: `[0, 5000, 100.0, 0 (bid), 1 (pulled), 2.0, m]`.
    let pulled = bars
        .iter()
        .find(|b| b[1] == 5_000)
        .expect("полоска, умершая на 5-й секунде, в файле");
    assert_eq!(pulled[0], 0);
    assert_eq!(pulled[2], 100.0);
    assert_eq!(pulled[3], 0);
    assert_eq!(pulled[4], 1);
    assert_eq!(pulled[5], 2.0);
    // Живые до конца: `d = null`, исход 3.
    assert!(bars.iter().any(|b| b[1].is_null() && b[4] == 3));
    let touches = v["touches"].as_array().unwrap();
    assert_eq!(touches.len(), 1);
    let t = touches[0].as_array().unwrap();
    assert_eq!(t.len(), 12);
    assert_eq!(t[0], 5_000, "начало касания, мс от t0");
    assert_eq!(t[1], 99.99);
    assert_eq!(t[2], 0, "бид");
    assert_eq!(t[3], 0, "отскок");
    assert_eq!(t[10], 3_000, "длилось 3 с");
    assert!(v["levels"].as_array().unwrap().is_empty(), "слой T39 пуст");
    assert!(v["stabs"].as_array().unwrap().is_empty());

    // Сводка знает про файлы и их размер.
    let d: Dashboard = serde_json::from_str(&std::fs::read_to_string(&s.json).unwrap()).unwrap();
    assert_eq!(d.coins[0].chart_file, "coin-SOLUSDT.json");
    assert_eq!(d.coins[0].chart_bars, bars.len());
    assert_eq!(
        d.coins[0].chart_bars_total,
        bars.len(),
        "до потолка файла — все"
    );
    assert_eq!(d.coins[0].chart_touches, 1);
    assert_eq!(v["bars_total"], bars.len());
    assert_eq!(v["touches_total"], 1);
    // Середина — до знака полутика: у тика 0.01 — три знака, без хвоста f64.
    assert_eq!(mid[0][1], 100.005);
    assert_eq!(d.coins[1].chart_file, "coin-XRPUSDT.json");
    assert_eq!(d.chart_max_drawn, CHART_MAX_DRAWN);
    assert_eq!(d.chart_start_minutes, CHART_START_MINUTES);
}

/// Потолок файла (`CHART_FILE_MAX_ROWS`): остаются самые крупные, до
/// потолка — все как есть.
#[test]
fn keep_largest_keeps_the_biggest_rows_only_above_the_cap() {
    let mut v = vec![3.0, 9.0, 1.0, 7.0, 5.0];
    keep_largest(&mut v, 2, |x| *x);
    v.sort_by(f64::total_cmp);
    assert_eq!(v, vec![7.0, 9.0]);
    let mut w = vec![3.0, 9.0];
    keep_largest(&mut w, 2, |x| *x);
    assert_eq!(w, vec![3.0, 9.0]);
    assert_eq!(CHART_FILE_MAX_ROWS, CHART_MAX_DRAWN * 24);
}

/// Округление для файла: два и три знака, без `-0.0`.
#[test]
fn chart_rounding_drops_noise_and_negative_zero() {
    assert_eq!(round_to(1.2345, 100.0), 1.23);
    assert_eq!(round_to(0.3, 1000.0), 0.3);
    assert_eq!(round_to(-0.001, 100.0).to_string(), "0");
    assert_eq!(
        Bar::from(BarRow::from(Bar {
            b: 1,
            d: None,
            p: 2.5,
            s: 1,
            o: ALIVE_CODE,
            x: 3.0,
            m: None
        }))
        .d,
        None
    );
}
