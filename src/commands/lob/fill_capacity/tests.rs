use super::*;
use crate::commands::lob::test_support::{delta_frame, snap_frame, trade_frame, write_day};
use crate::commands::lob::touches::{run_touches, TouchesArgs};
use crate::commands::lob::{H3Args, H3ModeArg};

/// Фикстура `touches/tests.rs`: три касания бида 99; сделка продавца на 100 за
/// полсекунды до первого касания, на 99 — внутри третьего. Касания пишутся
/// вместе с записями подхода (`--approach-bps 700`: аск 105/106 против
/// бид-стены 99 — 606/707 bps, взвод на кадре 1000) — кэш F2 тот же.
const LEAD_S: i64 = 70;

fn frames() -> Vec<Vec<crate::binlog::Record>> {
    let lead = LEAD_S * 1_000;
    let mut frames = vec![snap_frame(
        0,
        &[(98, 10), (99, 10), (100, 10)],
        &[(105, 10)],
    )];
    for s in 1..=LEAD_S {
        let ask = if s % 2 == 0 { 105 } else { 106 };
        frames.push(delta_frame(
            s * 1_000,
            &[],
            &[(ask, 10), (105 + 106 - ask, 0)],
        ));
    }
    frames.extend([
        trade_frame(lead + 500, 100, 3),
        delta_frame(lead + 1000, &[(100, 0)], &[]),
        delta_frame(lead + 2000, &[(100, 10)], &[]),
        delta_frame(lead + 3000, &[(100, 0)], &[]),
        delta_frame(lead + 4000, &[(101, 10)], &[]),
        delta_frame(lead + 5000, &[(101, 0)], &[]),
        trade_frame(lead + 5500, 99, 4),
        delta_frame(lead + 6000, &[(99, 1)], &[]),
    ]);
    frames
}

fn h3() -> H3Args {
    H3Args {
        h3_mode: H3ModeArg::Floor,
        h3_lots: None,
        h3_usd: None,
        h3_strength_pct: None,
        h3_strength_window_bps: None,
    }
}

/// Корень с сутками, маркером и кэшем касаний ночного вида.
fn fixture(dir: &std::path::Path) -> PathBuf {
    std::fs::write(
        crate::commands::record::instruments_csv_path(dir),
        "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n\
         SOLUSDT,0.01,0.1,0.1,5,5\n",
    )
    .unwrap();
    write_day(dir, "SOLUSDT", "2026-09-08", &frames());
    std::fs::write(
        dir.join("session.json"),
        "{\"started_utc\":\"2026-09-08T00:00:00Z\",\"start_hour_utc\":0,\"instruments\":[\"SOLUSDT\"]}",
    )
    .unwrap();
    std::fs::write(dir.join("verify-SOLUSDT.status"), "ok").unwrap();
    let cache = dir.join("touches");
    run_touches(&TouchesArgs {
        root: dir.to_path_buf(),
        symbol: "SOLUSDT".to_string(),
        h3: h3(),
        h3_k: None,
        warmup_ms: crate::commands::lob::DEFAULT_WARMUP_MS,
        repeat_window_ms: crate::commands::lob::DEFAULT_REPEAT_WINDOW_MS,
        out: Some(cache.join("2026-09-08").join("touches-SOLUSDT.csv")),
        approach_bps: vec![700],
        approach_min_age_secs: 0,
        moves: None,
        moves_window_ms: None,
        moves_bin_ms: None,
        numbers: None,
        allow_unverified: false,
    })
    .unwrap();
    cache
}

fn args(root: &std::path::Path, cache: &std::path::Path, sets: &[&str]) -> FillCapacityArgs {
    FillCapacityArgs {
        root: root.to_path_buf(),
        symbols: vec!["SOLUSDT".to_string()],
        days: Vec::new(),
        touches_from: cache.to_path_buf(),
        targets: TargetSource::Touches,
        sets: sets.iter().map(|s| s.to_string()).collect(),
        regime_from: None,
        h3: h3(),
        h3_k: None,
        band_bps: 300,
        pre_secs: vec![60, 1],
        post_secs: vec![10],
        out_dir: root.join("cap"),
        allow_unverified: false,
    }
}

fn read_csv(path: &std::path::Path) -> (Vec<String>, Vec<Vec<String>>) {
    let text = std::fs::read_to_string(path).unwrap();
    let mut r = csv::Reader::from_reader(text.as_bytes());
    let header: Vec<String> = r.headers().unwrap().iter().map(str::to_string).collect();
    let rows = r
        .records()
        .map(|rec| rec.unwrap().iter().map(str::to_string).collect())
        .collect();
    (header, rows)
}

fn col<'a>(header: &[String], row: &'a [String], name: &str) -> &'a str {
    let i = header
        .iter()
        .position(|h| h == name)
        .unwrap_or_else(|| panic!("колонки {name} нет: {header:?}"));
    &row[i]
}

#[test]
fn writes_band_rows_per_touch_with_queue_and_sold_by_slot() {
    let dir = tempfile::tempdir().unwrap();
    let cache = fixture(dir.path());
    let a = args(
        dir.path(),
        &cache,
        &["all:", "bid:side=bid", "ask:side=ask"],
    );
    let s = run_fill_capacity(&a).unwrap();
    assert_eq!(s.symbols_done, 1);
    assert_eq!(s.symbol_days, 1);
    assert!(s.touches >= 3, "касаний фикстуры: {}", s.touches);
    // Полоса 300 bps от 99 тиков — ⌊2.97⌋ = 2 тика: три строки на касание.
    assert_eq!(s.rows, s.touches * 3);
    let (touches_h, touches) = read_csv(&cache.join("2026-09-08").join("touches-SOLUSDT.csv"));
    let n_bid_touches = touches
        .iter()
        .filter(|r| col(&touches_h, r, "side") == "bid")
        .count() as u64;
    // `all:` и `bid` — одни и те же касания (аск-касаний в фикстуре нет), `ask` — файла нет.
    let all = a.out_dir.join("all").join("capacity-SOLUSDT.csv");
    let bid = a.out_dir.join("bid").join("capacity-SOLUSDT.csv");
    assert!(!a.out_dir.join("ask").join("capacity-SOLUSDT.csv").exists());
    assert_eq!(
        std::fs::read_to_string(&all).unwrap(),
        std::fs::read_to_string(&bid).unwrap()
    );
    let (h, rows) = read_csv(&all);
    assert_eq!(rows.len() as u64, n_bid_touches * 3);
    assert_eq!(
        h.iter().skip(h.len() - 13).cloned().collect::<Vec<_>>(),
        [
            "best_pre1",
            "opp_pre1",
            "q_pre1",
            "sold_pre1",
            "best_pre60",
            "opp_pre60",
            "q_pre60",
            "sold_pre60",
            "q_post10",
            "sold_post10",
            "clear_ms",
            "best_after_clear",
            "opp_after_clear"
        ]
    );
    let lead = LEAD_S * 1_000;
    for r in &rows {
        assert_eq!(col(&h, r, "symbol"), "SOLUSDT");
        assert_eq!(col(&h, r, "side"), "bid");
        assert_eq!(col(&h, r, "price_tick"), "99");
        assert_eq!(col(&h, r, "tick_px"), "0.01");
        assert_eq!(col(&h, r, "lot_qty"), "0.001");
        assert_eq!(col(&h, r, "band"), "2");
        let off: i64 = col(&h, r, "off").parse().unwrap();
        assert_eq!(col(&h, r, "tick").parse::<i64>().unwrap(), 99 + off);
        // Стена в кадре старта: очередь на тике 0 в `t0` — размер касания.
        if off == 0 {
            assert_eq!(col(&h, r, "q_t0"), col(&h, r, "size_at_touch"));
        }
        // Лучшие цены в `t0`: наш бид — стена, аск — 105/106.
        assert_eq!(col(&h, r, "best_t0"), "99");
        assert!(matches!(col(&h, r, "opp_t0"), "105" | "106"));
        // Окно 60 с до первого касания (lead + 1 с − 60 с = 11 с) — книга есть.
        assert_ne!(col(&h, r, "q_pre60"), "-1");
    }
    // Первое касание: старт lead + 1000 (100 снят). За секунду до — на 100 стояло
    // 10, сделка продавца на 100 (3 лота) — внутри окна 1 с и 60 с; в `t0` тика
    // 100 в книге нет.
    let first: Vec<&Vec<String>> = rows
        .iter()
        .filter(|r| col(&h, r, "start_ms") == (lead + 1000).to_string())
        .collect();
    assert_eq!(first.len(), 3);
    let at = |off: &str, name: &str| -> String {
        let r = first
            .iter()
            .find(|r| col(&h, r, "off") == off)
            .expect("строка тика");
        col(&h, r, name).to_string()
    };
    assert_eq!(at("1", "q_pre1"), "10");
    assert_eq!(at("1", "sold_pre1"), "3");
    assert_eq!(at("1", "sold_pre60"), "3");
    assert_eq!(at("1", "q_t0"), "0");
    assert_eq!(at("1", "sold_touch"), "0");
    // Слот post 10 с: очередь — как в `t0`; за 10 с после старта первого касания
    // одна сделка против нас — на стене в lead + 5500 (4 лота).
    assert_eq!(at("1", "q_post10"), "0");
    assert_eq!(at("0", "q_post10"), at("0", "q_t0"));
    assert_eq!(at("0", "sold_post10"), "4");
    // Очередь на стене (10 лотов в t0) за 10 с не выбрана — сделка 4 лота; на 10 001
    // очередь 0, но сделок там после t0 нет.
    assert_eq!(at("0", "clear_ms"), "-1");
    assert_eq!(at("1", "clear_ms"), "-1");
    assert_eq!(at("0", "best_after_clear"), "-1");
    assert_eq!(at("0", "q_pre1"), "10");
    assert_eq!(at("0", "sold_pre1"), "0");
    assert_eq!(at("2", "q_pre1"), "0");
    assert_eq!(at("0", "dist_bps"), "0.000");
    // 1 тик от 99 — 101.010 bps.
    assert_eq!(at("1", "dist_bps"), "101.010");
    // Касание со сделкой продавца на стене (lead + 5500, 4 лота): `sold_touch` на тике 0.
    let hit = rows
        .iter()
        .filter(|r| col(&h, r, "off") == "0")
        .map(|r| col(&h, r, "sold_touch").parse::<i64>().unwrap())
        .sum::<i64>();
    assert_eq!(hit, 4);
    let manifest = std::fs::read_to_string(a.out_dir.join("manifest.txt")).unwrap();
    assert!(manifest.contains("band_bps=300"));
    assert!(manifest.contains("set=bid:side=bid"));
}

#[test]
fn unverified_symbol_is_skipped_unless_allowed() {
    let dir = tempfile::tempdir().unwrap();
    let cache = fixture(dir.path());
    std::fs::write(dir.path().join("verify-SOLUSDT.status"), "fail").unwrap();
    let a = args(dir.path(), &cache, &["all:"]);
    let s = run_fill_capacity(&a).unwrap();
    assert_eq!(s.symbols_skipped_unverified, 1);
    assert_eq!(s.rows, 0);
    let mut a = args(dir.path(), &cache, &["all:"]);
    a.allow_unverified = true;
    let s = run_fill_capacity(&a).unwrap();
    assert_eq!(s.symbols_done, 1);
    assert!(s.rows > 0);
}

#[test]
fn rejects_bad_flags() {
    let dir = tempfile::tempdir().unwrap();
    let cache = fixture(dir.path());
    let mut a = args(dir.path(), &cache, &["all:"]);
    a.band_bps = 0;
    assert!(run_fill_capacity(&a).is_err());
    let mut a = args(dir.path(), &cache, &["all:"]);
    a.pre_secs = vec![0];
    assert!(run_fill_capacity(&a).is_err());
    let mut a = args(dir.path(), &cache, &["all:"]);
    a.post_secs = vec![10, 10];
    assert!(run_fill_capacity(&a).is_err());
    let mut a = args(dir.path(), &cache, &["all:", "all:side=bid"]);
    a.pre_secs = vec![1];
    assert!(run_fill_capacity(&a).is_err(), "имена наборов повторяются");
    let a = args(dir.path(), &cache, &["x:pool4h_max=0"]);
    assert!(run_fill_capacity(&a).is_err(), "режим без --regime-from");
    let mut a = args(dir.path(), &cache, &["all:"]);
    a.touches_from = dir.path().join("нет-такого");
    assert!(run_fill_capacity(&a).is_err());
}

/// Символ без файла в кэше касаний — пропуск со счётчиком, не отказ всего прогона.
#[test]
fn symbol_missing_from_cache_is_skipped() {
    let dir = tempfile::tempdir().unwrap();
    let cache = fixture(dir.path());
    let mut a = args(dir.path(), &cache, &["all:"]);
    a.touches_from = dir.path().join("cap-empty");
    std::fs::create_dir_all(&a.touches_from).unwrap();
    let s = run_fill_capacity(&a).unwrap();
    assert_eq!(s.symbols_without_cache, 1);
    assert_eq!(s.symbols_done, 0);
    assert_eq!(s.rows, 0);
}

/// F2 этапа F: `--targets approaches` берёт цели из записей подхода —
/// `start_ms` это `arm_ms`, `end_ms` — `disarm_ms`, `age_ms` и `size_at_touch`
/// с момента взвода, `frontrun_off` пуст (фронтрана на взводе нет). Колонки и
/// число строк на цель те же, что у касаний.
#[test]
fn approaches_targets_take_arm_and_disarm_windows() {
    let dir = tempfile::tempdir().unwrap();
    let cache = fixture(dir.path());
    // Фикстура: взвод бид-стены 99 на кадре 1000, снятие касанием на 71000.
    let (ap_h, ap) = read_csv(&cache.join("2026-09-08").join("approaches-SOLUSDT.csv"));
    assert_eq!(ap.len(), 1, "{ap:?}");
    assert_eq!(col(&ap_h, &ap[0], "arm_ms"), "1000");
    assert_eq!(col(&ap_h, &ap[0], "disarm_ms"), "71000");
    assert_eq!(col(&ap_h, &ap[0], "disarm_reason"), "touch");
    let mut a = args(dir.path(), &cache, &["all:"]);
    a.targets = TargetSource::Approaches;
    let s = run_fill_capacity(&a).unwrap();
    assert_eq!(s.symbols_done, 1);
    assert_eq!(s.symbol_days, 1);
    assert_eq!(s.touches, 1, "одна цель — один подход");
    assert_eq!(s.rows, 3, "полоса 300 bps от 99 тиков — три строки");
    let (h, rows) = read_csv(
        &dir.path()
            .join("cap")
            .join("all")
            .join("capacity-SOLUSDT.csv"),
    );
    // Колонки те же, что у замера по касаниям (читатель `leg-distance.py`).
    assert_eq!(col(&h, &rows[0], "side"), "bid");
    assert_eq!(col(&h, &rows[0], "price_tick"), "99");
    assert_eq!(
        col(&h, &rows[0], "start_ms"),
        "1000",
        "постановка на взводе"
    );
    assert_eq!(
        col(&h, &rows[0], "end_ms"),
        "71000",
        "жизнь до снятия взвода"
    );
    assert_eq!(col(&h, &rows[0], "age_ms"), "1000", "возраст на взводе");
    assert_eq!(col(&h, &rows[0], "size_at_touch"), "10", "размер на взводе");
    assert_eq!(
        col(&h, &rows[0], "frontrun_off"),
        "",
        "фронтрана на взводе не считаем"
    );
    assert!(
        rows.iter().all(|r| col(&h, r, "start_ms") == "1000"),
        "все тики полосы — та же цель"
    );
}

/// Ключи контекста (`ret*`/`pool*`/`btc*`) у подхода не определены: замер по
/// подходам с ними — отказ, а не молчаливый ноль строк.
#[test]
fn approaches_targets_refuse_context_keys() {
    let dir = tempfile::tempdir().unwrap();
    let cache = fixture(dir.path());
    let mut a = args(dir.path(), &cache, &["h1:ret1h_min=10"]);
    a.targets = TargetSource::Approaches;
    let err = run_fill_capacity(&a).unwrap_err().to_string();
    assert!(err.contains("контекста"), "{err}");
    // Кэша подходов нет (суточных `approaches-<SYMBOL>.csv` не писали) —
    // символ пропускается со счётчиком, не отказ всего прогона.
    let mut a = args(dir.path(), &cache, &["all:"]);
    a.targets = TargetSource::Approaches;
    a.touches_from = dir.path().join("cap-empty");
    std::fs::create_dir_all(&a.touches_from).unwrap();
    let s = run_fill_capacity(&a).unwrap();
    assert_eq!(s.symbols_without_cache, 1);
    assert_eq!(s.symbols_done, 0);
}
