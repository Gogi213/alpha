use super::args::{
    ensure_entry_conditions_args, parse_early_exits, parse_entry_ttls, parse_exit_forms, DriverArg,
    SideArg,
};
use super::cache::eaten_pct;
use super::carry::carry_events;
use super::forms::{grid_forms_with_axes, grid_forms_with_early, grid_forms_with_entry_ttl};
use super::outputs::{forms_row, sum_net_bps, FORMS_HEADER, FORMS_HEADER_CARRY_LEN};
use super::sets::{Range, CTX_AXES};
use super::*;
use crate::book::Side;
use crate::commands::lob::backtest::{EntryForm, EntryTtl, PoolLot, StopForm, TakeForm};
use crate::commands::lob::bounce_verdict::{parse_form, DEADLINE_SECS};
use crate::commands::lob::test_support::{delta_frame, snap_frame, trade_frame, write_day};
use crate::commands::lob::{H3Args, H3ModeArg};
use crate::lob::backtest::{BounceRun, QueueModelKind};
use crate::lob::levels::TouchRecord;
use crate::lob::strategy::WallStopMode;

/// Фикстура `touches/tests.rs` (три касания бида 99 за 6 секунд), сдвинутая
/// на `LEAD_S` секунд «тихой» книги впереди: ряд `σ` (В-62) должен покрывать
/// окно 60 с к первому касанию, иначе ни у одной формы нет сигналов.
const LEAD_S: i64 = 70;

fn touch_frames() -> Vec<Vec<crate::binlog::Record>> {
    let lead = LEAD_S * 1_000;
    let mut frames = vec![snap_frame(
        0,
        &[(98, 10), (99, 10), (100, 10)],
        &[(105, 10)],
    )];
    for s in 1..=LEAD_S {
        // Лёгкое дыхание аска: середина двигается, σ не ноль.
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

fn fixture_root(dir: &std::path::Path, verified: bool) {
    std::fs::write(
        crate::commands::record::instruments_csv_path(dir),
        "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n\
         SOLUSDT,0.01,0.1,0.1,5,5\n",
    )
    .unwrap();
    write_day(dir, "SOLUSDT", "2026-09-08", &touch_frames());
    std::fs::write(
        dir.join("session.json"),
        "{\"started_utc\":\"2026-09-08T00:00:00Z\",\"start_hour_utc\":0,\"instruments\":[\"SOLUSDT\"]}",
    )
    .unwrap();
    if verified {
        std::fs::write(dir.join("verify-SOLUSDT.status"), "ok").unwrap();
    }
}

/// Фикстура F6 (В-73): цена **подходит** к бид-стене 99.00 (тик 9900) за 2 с
/// до касания. Аск стоит далеко (130.00 — 3130 bps от стены, полоса не
/// взведена), на `lead+3500` входит в полосу 750 bps (106.00 против 99.00 —
/// 707 bps), на `lead+4000` кадр стороны бида взводит подход, на `lead+5500`
/// убирается бид 100.00: стена становится лучшей — **касание**, подход
/// снимается касанием. Свип 99.00 на `lead+5000` (за 0,5 с **до** касания)
/// исполняет ноги входа, поставленные на взводе, а касание закрывает круг
/// стопом «в стену».
fn approach_frames() -> Vec<Vec<crate::binlog::Record>> {
    let lead = LEAD_S * 1_000;
    let mut frames = vec![snap_frame(
        0,
        &[(9_800, 10), (9_900, 10), (10_000, 10)],
        &[(13_000, 10)],
    )];
    for s in 1..=LEAD_S {
        // Аск дышит далеко от стены: полоса взвода не задета.
        let ask = if s % 2 == 0 { 13_000 } else { 13_100 };
        frames.push(delta_frame(s * 1_000, &[], &[(ask, 10), (26_100 - ask, 0)]));
    }
    frames.extend([
        // Подход: аск 106.00 — 707 bps от стены 99.00 (полоса 750).
        delta_frame(lead + 3_500, &[], &[(10_600, 10), (13_000, 0), (13_100, 0)]),
        // Кадр стороны бида: чужая цена (106.00) уже известна, стена ещё не
        // лучшая (впереди 100.00) — взвод подхода здесь, а не на касании.
        delta_frame(lead + 4_000, &[(9_800, 20)], &[]),
        // Свип в стену до касания: сделка по 99.00 исполняет ноги входа.
        trade_frame(lead + 5_000, 9_900, 4),
        // Касание: 100.00 снят — стена стала лучшей ценой, подход снят.
        delta_frame(lead + 5_500, &[(10_000, 0)], &[]),
        // Книга стоит: круг закрывается стопом «в стену» (99.00).
        delta_frame(lead + 5_700, &[(9_900, 10)], &[(10_600, 10)]),
        delta_frame(lead + 5_900, &[(9_900, 10)], &[(10_600, 10)]),
        // Второй свип: сигнал **касания** (t0 = lead+5500) ставит ногу 99.01
        // позже и исполняется здесь — на тех же данных видно разницу сигналов.
        trade_frame(lead + 6_000, 9_900, 4),
        delta_frame(lead + 6_200, &[(9_900, 10)], &[(10_600, 10)]),
        // 100.00 вернулся: касание стены заканчивается и попадает в CSV
        // касаний (открытое касание в конце записи не эмитится).
        delta_frame(lead + 6_400, &[(10_000, 10)], &[]),
    ]);
    frames
}

fn fixture_root_approach(dir: &std::path::Path) {
    std::fs::write(
        crate::commands::record::instruments_csv_path(dir),
        "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n\
         SOLUSDT,0.01,0.1,0.1,5,5\n",
    )
    .unwrap();
    write_day(dir, "SOLUSDT", "2026-09-08", &approach_frames());
    std::fs::write(
        dir.join("session.json"),
        "{\"started_utc\":\"2026-09-08T00:00:00Z\",\"start_hour_utc\":0,\"instruments\":[\"SOLUSDT\"]}",
    )
    .unwrap();
    std::fs::write(dir.join("verify-SOLUSDT.status"), "ok").unwrap();
}

fn args(root: &std::path::Path, allow_unverified: bool) -> BounceGridArgs {
    BounceGridArgs {
        root: root.to_path_buf(),
        symbols: vec!["SOLUSDT".to_string()],
        median_rtt_ns: crate::lob::backtest::ExecLatency::uniform(20_000_000),
        p95_rtt_ns: crate::lob::backtest::ExecLatency::uniform(20_000_000),
        queue_model: QueueModelKind::RiskAdverse,
        order_qty_e9: Some(100_000_000),
        order_qty_mult: 1,
        order_qty_from_pool: false,
        order_usd: None,
        // Оба флага сняты — умолчание команды: вход пост-онли (В-72).
        post_only: false,
        no_post_only: false,
        stop_form: vec!["s1".to_string(), "s2".to_string()],
        take_form: vec!["t1".to_string()],
        take_floor_fees: Some(1.0),
        // F5 (В-74): по умолчанию — прежний режим `touch`, полоса не задана.
        entry_ttl_secs: Vec::new(),
        band_exit_bps: None,
        frontrun_only: false,
        min_age_secs: None,
        min_flow_pct: None,
        side: None,
        touches_from: None,
        // F6 (В-73): прежний сигнал (касание) и прежний вход (`single@fr`) —
        // на них стоит гейт «те же круги»; подход и лестница — своими тестами.
        signal: SignalArg::Touch,
        entry_form: Vec::new(),
        sets: Vec::new(),
        cells: None,
        sigma_from: None,
        ctx_shift_secs: 0,
        hold_step: "poll".to_string(),
        level_control: None,
        exit_group: "off".to_string(),
        p08_cols: false,
        r1_cols: false,
        tape_log: None,
        tape_log_ws: vec![],
        touch_col: false,
        emit_events: false,
        regime_from: None,
        klines_from: None,
        btc_seconds: None,
        deadline_secs: Vec::new(),
        h3: H3Args {
            h3_mode: H3ModeArg::Percentile,
            h3_lots: Some(5),
            h3_usd: None,
            h3_strength_pct: None,
            h3_strength_window_bps: None,
        },
        h3_k: None,
        warmup_ms: Some(0),
        repeat_window_ms: Some(3_600_000),
        days: Vec::new(),
        threads: Some(3),
        driver: DriverArg::Setups,
        round_memo: "on".to_string(),
        busy_skip: "on".to_string(),
        // К3: сверка окон с книгой крейта на всех сутках тестовых сеток.
        windows_check: true,
        events: "compact".to_string(),
        out_dir: root.join("grid"),
        allow_unverified,
        verdict_csv: None,
        // F7/F8: форма выхода — по умолчанию `none` (гейт).
        exit_form: Vec::new(),
        btc_minutes: Vec::new(),
        early_exit_secs: Vec::new(),
        carry_age: false,
        touches_cache_only: false,
        carry_root: None,
        extra_runs: None,
    }
}

fn read_csv(path: &std::path::Path) -> (Vec<String>, Vec<Vec<String>>) {
    let text = std::fs::read_to_string(path).unwrap();
    read_csv_from(text.as_bytes())
}

/// То же, но из байтов «золотого» файла (`include_str!`).
fn read_csv_from(bytes: &[u8]) -> (Vec<String>, Vec<Vec<String>>) {
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_reader(bytes);
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

mod admit;
mod carry_cases;
mod control;
mod ctx_shift;
mod form_cases;
mod grid;
mod keys;
mod mechanics;
