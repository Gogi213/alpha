use super::*;
use crate::commands::lob::test_support::{delta_frame, snap_frame, trade_frame, write_day_part};

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
    std::fs::write(
        root.join("session.json"),
        serde_json::to_string_pretty(&json).unwrap(),
    )
    .unwrap();
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
    let d = build_dashboard(&args).expect("расчёт обязан пройти на фикстуре");
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

    let alive = c.picture.bars.iter().filter(|b| b.o == "alive").count();
    assert_eq!(alive, 2);
    assert_eq!(c.picture.bars.len(), 4, "два умерших и два живых");
    assert_eq!(c.picture.to_ms, 60_000);
    assert!(!c.picture.mid.is_empty());
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
    let d = build_dashboard(&args).expect("расчёт обязан пройти на фикстуре");
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
    assert_eq!(t.all.m_bps[0], Some(0.0));
    assert_eq!(t.all.m_bps[H10S], Some(0.0));
    assert!(t.all.m_bps[3].unwrap() < 0.0, "{:?}", t.all.m_bps);
    assert_eq!(t.all.m10s_n, 2);
    assert_eq!(t.all.m10s_bounced_n, 1);
    assert_eq!(
        t.stack_median,
        Some(1.0),
        "на касании жив один уровень ≥ H3"
    );
    assert!(t.stack_shown);
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

    // Метки на картине: обе в окне, с числами наведения.
    let p = &c.picture;
    assert_eq!(p.marks_total, 2);
    assert_eq!(p.marks.len(), 2);
    assert_eq!(p.marks_clipped, 0);
    let bounce = p.marks.iter().find(|m| m.o == "bounced").unwrap();
    assert_eq!(bounce.t, 5_000);
    assert_eq!(bounce.s, "bid");
    assert_eq!(bounce.p, 99.99);
    assert_eq!(bounce.i, 0);
    assert_eq!(bounce.a, 5_000);
    assert_eq!(bounce.d, 3_000);
    assert_eq!(bounce.fr, Some(1.0));
    assert!(bounce.ap.unwrap() > 0.0);
    assert_eq!(bounce.m, Some(0.0));
    assert!((bounce.x - 2.0).abs() < 1e-9);
    let death = p.marks.iter().find(|m| m.o == "eaten").unwrap();
    assert_eq!(death.t, 12_000);
    assert_eq!(death.i, 1);
    assert_eq!(death.d, 1_000);
    assert!((death.fr.unwrap() - 0.3).abs() < 1e-9);

    // Уровни при этом — как прежде: 10000 сняли, 9999 проели, аск жив.
    assert_eq!(c.levels_total, 2);
    assert_eq!(c.levels_now.len(), 1);
}

/// Без касаний — блок с нулями и всеми метками, не ошибка; меток на картине нет.
#[test]
fn a_coin_without_touches_gets_a_zero_block_not_an_error() {
    let dir = tempfile::tempdir().unwrap();
    let args = fixture_with(dir.path(), &no_touch_frames());
    let d = build_dashboard(&args).unwrap();
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
    assert!(c.picture.marks.is_empty());
    assert_eq!(c.picture.marks_total, 0);
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
    assert_eq!(d.assumed_rtt_ms, ASSUMED_RTT_MS);
    assert_eq!(d.roundtrip_fees_bps, ROUNDTRIP_FEES_BPS);
    assert_eq!(s.coins, 1);
    // Касания едут в `data.json` теми же типами: у `frames()` касаний нет
    // (10000 после проедания остался в книге одним лотом — лучшей ценой,
    // 9999 лучшим не становился), блок с нулями и всеми метками.
    assert_eq!(d.coins[0].touches.total, 0);
    assert_eq!(d.coins[0].touches.by_approach.len(), APPROACH_LABELS.len());
    assert!(d.coins[0].picture.marks.is_empty());
    assert!(d.glossary.iter().any(|g| g.title.starts_with("Касание")));
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
    let d = build_dashboard(&args).unwrap();
    assert_eq!(d.coins.len(), 2);
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
    for needle in [
        "touchesHtml",
        "co.touches",
        "P.marks",
        "f_touch",
        "Касания: цена дошла до плотности",
        "cross_outcome_age",
    ] {
        assert!(html.contains(needle), "в шаблоне нет {needle}");
    }
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
