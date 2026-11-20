//! Тесты шага B П-42 (§13.2): сигнал по сделкам без заглядывания, вход до пробоя, вход по агрессивной сделке стороны,
//! тейки только строго через цену, стоп по цене сделки, фандинг и суточная переоценка по сделкам.

use super::data::{Cols, Fund, Sym, MIN_MS, START_DAY};
use super::levels::{build_levels, Level};
use super::params::Params;
use super::tests::{flat, level_world, lv_params, mirror, px, run_scan, set, sym, BORN};
use super::tick::{
    candidates, entry_decision, exit_natr, first_signal, fresh_ok, minute_range, parse_px,
    parse_trades, run_tick, settle_tick, signals_for_coin, sim_fill, sim_stream, tp_lots,
    tp_mult_m, EntryDec, EntryRej, LoadStat, TLeg, TSig, Tick, TickCtr, TickOut, TradeSrc, DAY_MS,
};
use super::trade::{ExitKind, NOTIONAL_U};
use super::Side;
use std::collections::HashMap;
use std::io::Cursor;
use std::sync::Arc;

fn tk(m: usize, ms: u32, price: f64, bm: bool) -> Tick {
    Tick {
        px: px(price),
        off: (m % 1440) as u32 * 60_000 + ms,
        bm,
    }
}

const E_E8: i64 = 300_000; // 0,3 %

#[test]
fn csv_parse_header_fields_and_out_of_day() {
    let day = START_DAY;
    let base = super::data::T0_MS + day as i64 * DAY_MS;
    let csv = format!(
        "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n\
         1,78549.6,0.025,10,11,{},false\n\
         2,78549.5,0.016,12,16,{},true\n\
         3,78549.0,0.003,17,17,{},true\n\
         4,1.0,1,18,18,{},false\n",
        base + 5,
        base + 3, // время назад — файл будет отсортирован
        base + 20,
        base + DAY_MS + 1, // вне суток — отброшена
    );
    let mut st = LoadStat::default();
    let v = parse_trades(Cursor::new(csv.as_bytes()), day, &mut st).expect("разбор");
    assert_eq!(v.len(), 3);
    assert_eq!(st.out_of_day, 1);
    assert_eq!(
        st.unsorted_files, 1,
        "убывание времени замечено и сделки отсортированы"
    );
    assert_eq!(v.iter().map(|t| t.off).collect::<Vec<_>>(), vec![3, 5, 20]);
    assert_eq!(v[0].px, px(78549.5));
    assert!(v[0].bm && !v[1].bm);
}

#[test]
fn csv_parse_java_exponent_prices() {
    // Binance пишет цены < 0,001 как `9.16E-4`: раньше такие строки отбрасывались как «плохие»
    assert_eq!(parse_px(b"9.16E-4"), Some(91_600));
    assert_eq!(parse_px(b"5.996E-5"), Some(5_996));
    assert_eq!(parse_px(b"1.3E-4"), Some(13_000));
    assert_eq!(parse_px(b"1E-5"), Some(1_000));
    assert_eq!(parse_px(b"7.0E-5"), Some(7_000));
    assert_eq!(parse_px(b"1.2345E+2"), Some(12_345_000_000));
    assert_eq!(parse_px(b"78549.6"), Some(px(78549.6)));
    assert_eq!(parse_px(b"0.00091600"), Some(91_600));
    assert_eq!(parse_px(b"9.16E"), None);
    assert_eq!(parse_px(b"abc"), None);
    let day = START_DAY;
    let base = super::data::T0_MS + day as i64 * DAY_MS;
    let csv = format!(
        "1,9.16E-4,5,1,1,{},false\n2,0.000917,5,2,2,{},true\n3,xx,5,3,3,{},true\n",
        base + 1,
        base + 2,
        base + 3
    );
    let mut st = LoadStat::default();
    let v = parse_trades(Cursor::new(csv.as_bytes()), day, &mut st).expect("разбор");
    assert_eq!(
        v.iter().map(|t| t.px).collect::<Vec<_>>(),
        vec![91_600, 91_700]
    );
    assert_eq!(st.bad_rows, 1);
}

#[test]
fn signal_on_approach_before_the_break_in_the_crossing_minute() {
    // 1м-версия: минутка пересекла уровень → сделки нет; по сделкам сигнал стоит на подходе, до пересечения
    let p = lv_params();
    let t1 = BORN + 100;
    let mut c = level_world();
    set(&mut c, t1, 99.0, 100.10, 98.9, 100.00);
    let (sigs, _) = run_scan(c.clone(), &p);
    assert!(
        sigs.iter().all(|s| s.t as usize != t1),
        "1м: минутка с пересечением сигнала не даёт"
    );
    let s = sym(c);
    let lv = build_levels(&s, &p.sig.lv, Side::Long);
    let mut cs = Vec::new();
    candidates(&s, &lv, &p.sig, Side::Long, &mut cs);
    let c1 = cs
        .iter()
        .find(|c| c.t as usize == t1)
        .expect("кандидат в минуте пересечения");
    let ticks = vec![
        tk(t1, 0, 99.0, false),
        tk(t1, 10_000, 99.5, false), // дальше 0,3 %
        tk(t1, 20_000, 99.8, false), // 0,22 % ниже уровня, выше открытия минуты → сигнал
        tk(t1, 30_000, 100.10, false),
        tk(t1, 40_000, 99.9, true),
    ];
    let (from, to) = minute_range(&ticks, t1);
    assert_eq!((from, to), (0, 5));
    let r = first_signal(&ticks, from, to, 1, c1.lp, E_E8, true).expect("сигнал");
    assert_eq!(r.0, 2);
}

#[test]
fn no_signal_after_level_reached_in_same_minute() {
    let lp = px(100.025);
    let ticks = vec![
        tk(10, 0, 99.0, false),
        tk(10, 5_000, 100.025, false), // дошла до уровня (касание = дошла)
        tk(10, 9_000, 99.9, false),
    ];
    assert!(first_signal(&ticks, 0, 3, 1, lp, E_E8, true).is_none());
}

#[test]
fn colour_uses_forming_minute_and_signal_ignores_later_trades() {
    let lp = px(100.025);
    let mut ticks = vec![
        tk(10, 0, 99.9, false), // первая сделка минуты: открытие, сигналом быть не может
        tk(10, 1_000, 99.85, true), // ниже открытия — красная
        tk(10, 2_000, 99.95, false), // выше открытия — зелёная, в зоне → сигнал
    ];
    let a = first_signal(&ticks, 0, 3, 1, lp, E_E8, true).expect("сигнал");
    assert_eq!(a.0, 2);
    // без цвета сигнал стоит на сделке, подошедшей к зоне (99,9 — первая в зоне, но она первая: открытие, не сигнал по цвету; без цвета — сигнал)
    assert_eq!(
        first_signal(&ticks, 0, 3, 1, lp, E_E8, false).map(|x| x.0),
        Some(0)
    );
    // заглядывания вперёд нет: сделки после сигнала сигнал не меняют
    ticks.push(tk(10, 3_000, 150.0, false));
    ticks.push(tk(10, 4_000, 1.0, true));
    assert_eq!(
        first_signal(&ticks, 0, 5, 1, lp, E_E8, true).map(|x| x.0),
        Some(2)
    );
    assert_eq!(
        first_signal(&ticks, 0, 3, 1, lp, E_E8, true).map(|x| x.0),
        Some(2)
    );
}

#[test]
fn short_side_mirrors_signal() {
    // шорт: уровень-пол 100, цена подходит сверху; в длинной раме цены с обратным знаком
    let lp = -px(100.0);
    let ticks = vec![
        tk(10, 0, 101.0, true),
        tk(10, 1_000, 100.20, true), // 0,2 % над полом, ниже открытия минуты (красная для шорта = зелёная в раме) → сигнал
        tk(10, 2_000, 99.9, true),
    ];
    let r = first_signal(&ticks, 0, 3, -1, lp, E_E8, true).expect("сигнал шорта");
    assert_eq!(r.0, 1);
}

#[test]
fn candidates_use_closed_minutes_for_pool() {
    // оборот минуты t1 в оборот часа на сигнале минуты t1 не входит (только закрытые минуты ≤ t1−1); с t1+1 — входит
    let mut p = lv_params();
    p.sig.t1h_u = 1;
    let t1 = BORN + 100;
    let mut c = level_world();
    c.qv[t1] = 1_000_000_000_000;
    set(&mut c, t1, 99.85, 99.95, 99.84, 99.90);
    set(&mut c, t1 + 1, 99.85, 99.95, 99.84, 99.90);
    let (sigs, _) = run_scan(c.clone(), &p);
    assert_eq!(
        sigs.iter().map(|s| s.t as usize).collect::<Vec<_>>(),
        vec![t1, t1 + 1],
        "1м: пул по закрытию t"
    );
    let s = sym(c);
    let lv = build_levels(&s, &p.sig.lv, Side::Long);
    let mut cs = Vec::new();
    candidates(&s, &lv, &p.sig, Side::Long, &mut cs);
    assert_eq!(
        cs.iter().map(|c| c.t as usize).collect::<Vec<_>>(),
        vec![t1 + 1]
    );
}

#[test]
fn false_breakout_ban_applies_from_next_minute() {
    let p = lv_params();
    let mut c = level_world();
    set(&mut c, BORN + 50, 100.00, 100.20, 99.80, 99.85); // пересечение и возврат глубже f: запрет с закрытия минуты
    set(&mut c, BORN + 51, 99.85, 99.95, 99.84, 99.90);
    let s = sym(c);
    let lv = build_levels(&s, &p.sig.lv, Side::Long);
    let mut cs = Vec::new();
    candidates(&s, &lv, &p.sig, Side::Long, &mut cs);
    assert_eq!(
        cs.iter().map(|c| c.t as usize).collect::<Vec<_>>(),
        vec![BORN + 50]
    );
}

fn ex_test() -> super::params::ExitCfg {
    let mut e = Params::base().exit;
    e.slip_bps = 0;
    e.n_tp = 2;
    e.t_low_m = 1000;
    e.t_high_m = 2000;
    e.q_pct = 50;
    e.be = true;
    e.a_m = 1_000_000; // трейл не взводится
    e
}

const NATR: i64 = 500_000; // 0,5 %: 1 NATR = 0,5 при входе 100

fn stream(ticks: Vec<(i64, f64, bool)>) -> impl FnMut() -> Option<(i64, i64, bool)> {
    let mut it = ticks.into_iter();
    move || it.next().map(|(ms, p, bm)| (ms, px(p), bm))
}

#[test]
fn entry_is_next_aggressor_trade_of_side_with_slippage_and_latency() {
    let mut ex = ex_test();
    ex.slip_bps = 1;
    let ticks = vec![
        (900, 100.0, false),  // до сигнала
        (1000, 99.0, true),   // продавец-тейкер: лонгу не подходит
        (1200, 100.0, false), // покупатель-тейкер → вход
        (1300, 100.0, false),
    ];
    let out = sim_stream(&mut stream(ticks.clone()), 1000, Side::Long, NATR, &ex).expect("исход");
    assert_eq!(out.entry_ms, 1200);
    assert_eq!(out.entry_px, 10_001_000_000, "100,0 + 1 bp");
    // задержка: сделки раньше 1500 мс не берутся
    ex.lat_ms = 500;
    let ticks2 = vec![
        (1200, 100.0, false),
        (1600, 101.0, false),
        (1700, 101.0, false),
    ];
    let out = sim_stream(&mut stream(ticks2), 1000, Side::Long, NATR, &ex).expect("исход");
    assert_eq!(out.entry_ms, 1600);
    // шорт: продавец-тейкер, цена входа ниже на 1 bp; сделка в момент сигнала подходит (≥)
    ex.lat_ms = 0;
    let out = sim_stream(&mut stream(ticks), 1000, Side::Short, NATR, &ex).expect("исход");
    assert_eq!(out.entry_ms, 1000);
    assert_eq!(out.entry_px, 9_899_010_000, "99,0 − 1 bp");
    // нет подходящей сделки — нет исхода
    assert!(sim_stream(
        &mut stream(vec![(1100, 100.0, true)]),
        1000,
        Side::Long,
        NATR,
        &ex
    )
    .is_none());
}

#[test]
fn take_fills_only_when_trade_goes_strictly_through() {
    let ex = ex_test();
    let touch = vec![(10, 100.0, false), (20, 100.5, false), (30, 100.2, true)];
    let out = sim_stream(&mut stream(touch), 0, Side::Long, NATR, &ex).expect("исход");
    assert_eq!(
        out.takes, 0,
        "сделка по цене тейка (100,5) не исполняет его"
    );
    let through = vec![
        (10, 100.0, false),
        (20, 100.5 + 1e-8, false),
        (30, 100.2, true),
    ];
    let out = sim_stream(&mut stream(through), 0, Side::Long, NATR, &ex).expect("исход");
    assert_eq!(out.takes, 1);
    assert_eq!(
        out.legs[0].px,
        px(100.5),
        "тейк исполнен по своей цене, мейкер"
    );
    assert!(out.legs[0].maker);
    assert_eq!(out.legs[0].ms, 20);
}

#[test]
fn stop_fills_at_trade_price_that_broke_it() {
    let ex = ex_test();
    // касание уровня стопа (95,0) — срабатывает, цена сделки = 95
    let t = vec![
        (10, 100.0, false),
        (20, 96.0, true),
        (30, 95.0, true),
        (40, 90.0, true),
    ];
    let out = sim_stream(&mut stream(t), 0, Side::Long, NATR, &ex).expect("исход");
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 30));
    assert_eq!(out.legs.last().map(|l| l.px), Some(px(95.0)));
    // гэп: следующая сделка 94,0 → исполнение по 94,0, а не по стопу
    let g = vec![(10, 100.0, false), (20, 94.0, true)];
    let out = sim_stream(&mut stream(g), 0, Side::Long, NATR, &ex).expect("исход");
    assert_eq!(out.legs.last().map(|l| l.px), Some(px(94.0)));
    // тейкер: проскальзывание хуже на 1 bp
    let mut ex1 = ex_test();
    ex1.slip_bps = 1;
    let g = vec![(10, 100.0, false), (20, 94.0, true)];
    let out = sim_stream(&mut stream(g), 0, Side::Long, NATR, &ex1).expect("исход");
    assert_eq!(
        out.legs.last().map(|l| l.px),
        Some(px(94.0) - px(94.0) / 10_000)
    );
}

#[test]
fn exit_latency_fills_stop_on_first_trade_after_delay_and_takes_stay_limits() {
    // §13.6: задержка выхода — стоп/трейл/БУ исполняются по первой сделке не раньше срабатывания + задержка; тейки — лимитки
    let mut ex = ex_test();
    let ticks = vec![
        (10, 100.0, false),  // вход
        (100, 94.0, true),   // стоп 5 % сработал
        (120, 101.5, false), // за время задержки цена прошла оба тейка (100,5 и 101,0)
        (160, 93.0, true),   // первая сделка ≥ 100 + 50 → исполнение выхода по её цене
        (200, 90.0, true),
    ];
    let out = sim_stream(&mut stream(ticks.clone()), 0, Side::Long, NATR, &ex).expect("исход");
    assert_eq!(
        (out.kind, out.exit_ms, out.takes),
        (ExitKind::HardStop, 100, 0)
    );
    assert_eq!(out.legs.last().map(|l| l.px), Some(px(94.0)));
    ex.lat_exit_ms = 50;
    let out = sim_stream(&mut stream(ticks), 0, Side::Long, NATR, &ex).expect("исход");
    assert_eq!(
        (out.kind, out.exit_ms, out.takes),
        (ExitKind::HardStop, 160, 2)
    );
    assert_eq!(out.legs.last().map(|l| l.px), Some(px(93.0)));
    assert!(out.legs[0].maker && out.legs[1].maker && !out.legs[2].maker);
}

#[test]
fn breakeven_after_first_take_then_exit_on_next_trade_at_entry() {
    let ex = ex_test();
    let t = vec![
        (10, 100.0, false),
        (20, 100.6, false), // тейк 1 (100,5) исполнен → стоп переезжает на цену входа
        (30, 100.0, true),  // сделка по цене входа → безубыток
        (40, 90.0, true),
    ];
    let out = sim_stream(&mut stream(t), 0, Side::Long, NATR, &ex).expect("исход");
    assert_eq!(
        (out.kind, out.exit_ms, out.takes),
        (ExitKind::BreakEven, 30, 1)
    );
}

#[test]
fn trail_arms_on_trade_prices_and_exits_on_trade() {
    let mut ex = ex_test();
    ex.a_m = 1000; // взвод при +1 NATR (0,5)
    ex.r_m = 1000; // откат 1 NATR
    ex.be = false;
    ex.n_tp = 2;
    ex.t_low_m = 4000;
    ex.t_high_m = 5000; // тейки далеко
    let t = vec![
        (10, 100.0, false),
        (20, 100.6, false), // максимум 100,6 ≥ вход + 0,5 → трейл взведён: 100,6 − 0,5 = 100,1
        (30, 100.3, true),  // выше трейла
        (40, 100.1, true),  // ≤ 100,1 → выход по трейлу по цене сделки
    ];
    let out = sim_stream(&mut stream(t), 0, Side::Long, NATR, &ex).expect("исход");
    assert_eq!((out.kind, out.exit_ms), (ExitKind::Trail, 40));
}

fn out_manual(entry_ms: i64, exit_ms: i64, exit_px: f64, marks: Vec<(usize, i64)>) -> TickOut {
    TickOut {
        entry_ms,
        entry_px: px(100.0),
        legs: vec![TLeg {
            ms: exit_ms,
            px: px(exit_px),
            u: NOTIONAL_U,
            maker: false,
        }],
        exit_ms,
        kind: ExitKind::Forced,
        takes: 0,
        marks,
        tl: None,
    }
}

fn fee_taker(n: f64) -> f64 {
    n * 3.5 * 0.9 / 1e4
}

#[test]
fn funding_strictly_between_entry_and_exit_and_daily_mtm_sums() {
    let day_end = (START_DAY as i64 + 1) * DAY_MS;
    let fund = |ms: i64| Fund {
        min: (ms / MIN_MS) as u32,
        rate_e8: 10_000,
        mark: px(100.0),
    };
    let s = Sym::from_cols(
        "T",
        flat(START_DAY * 1440 + 4000, 100.0),
        vec![fund(day_end)],
    );
    let tr = out_manual(
        day_end - 3_600_000,
        day_end + 3_600_000,
        102.0,
        vec![(START_DAY, px(101.0))],
    );
    let st = settle_tick(&s, Side::Long, &tr);
    assert!(
        (st.funding - (-0.05)).abs() < 1e-9,
        "лонг платит 0,05: {}",
        st.funding
    );
    let total = 10.0 - fee_taker(500.0) - fee_taker(500.0 * 1.02) - 0.05;
    assert_eq!(st.daily.len(), 2);
    assert_eq!((st.daily[0].0, st.daily[1].0), (0, 1));
    assert!(
        (st.daily[0].1 - (5.0 - fee_taker(500.0))).abs() < 1e-6,
        "день 0: переоценка по последней сделке дня"
    );
    assert!(
        (st.daily[0].1 + st.daily[1].1 - total).abs() < 1e-9,
        "сумма по суткам = итог сделки"
    );
    // выплата в момент входа или выхода — не «строго через»
    let at_entry = settle_tick(
        &s,
        Side::Long,
        &out_manual(day_end, day_end + 3_600_000, 100.0, vec![]),
    );
    assert!(at_entry.funding.abs() < 1e-12);
    let at_exit = settle_tick(
        &s,
        Side::Long,
        &out_manual(day_end - 3_600_000, day_end, 100.0, vec![]),
    );
    assert!(at_exit.funding.abs() < 1e-12);
    // шорт получает
    let sh = settle_tick(&s, Side::Short, &tr);
    assert!((sh.funding - 0.05).abs() < 1e-9);
}

struct MemSrc(HashMap<(String, usize), Arc<Vec<Tick>>>);

impl TradeSrc for MemSrc {
    fn day(&self, sym: &str, day: usize) -> Option<Arc<Vec<Tick>>> {
        self.0.get(&(sym.to_string(), day)).cloned()
    }
}

#[test]
fn end_to_end_trade_entered_before_the_break_inside_the_crossing_minute() {
    let p = lv_params();
    let t1 = BORN + 100;
    let mut c = level_world();
    for m in t1 - 5..t1 {
        set(&mut c, m, 99.5, 99.6, 99.4, 99.5); // волатильность до сигнала: NATR выхода > 0
    }
    set(&mut c, t1, 99.0, 100.10, 98.9, 100.00);
    let (sigs, _) = run_scan(c.clone(), &p);
    assert!(
        sigs.iter().all(|s| s.t as usize != t1),
        "1м-версия в этой минутке не входит"
    );
    let s = Sym::from_cols("TEST", c, Vec::new());
    let ticks = vec![
        tk(t1, 0, 99.0, false),
        tk(t1, 10_000, 99.5, false),
        tk(t1, 20_000, 99.8, false),   // сигнал
        tk(t1, 30_000, 100.10, false), // следующая сделка покупателя-тейкера → вход, уже выше уровня
        tk(t1, 40_000, 99.9, true),
        tk(t1 + 1, 0, 90.0, true), // ниже стопа 5 % → выход
    ];
    let mut m = HashMap::new();
    m.insert(("TEST".to_string(), t1 / 1440), Arc::new(ticks));
    let src = MemSrc(m);
    let jobs = vec![(p.sig.clone(), Side::Long)];
    let mut outs = vec![Vec::new()];
    let mut ctrs = vec![TickCtr::default()];
    signals_for_coin(&s, 0, &jobs, &src, &mut outs, &mut ctrs);
    assert_eq!(outs[0].len(), 1);
    assert_eq!(outs[0][0].t as usize, t1);
    let syms = [s];
    let r = run_tick(&syms, &outs[0], Side::Long, &p.exit, &p.port, &src);
    assert_eq!(r.trades.len(), 1, "отказы: {:?}", r.rej);
    let tr = &r.trades[0];
    assert_eq!(tr.entry_ms % DAY_MS, (t1 as i64 % 1440) * MIN_MS + 30_000);
    assert_eq!(tr.kind, ExitKind::HardStop);
    assert!(tr.net < 0.0);
}

#[test]
fn reentry_modes_consume_level_per_protocol() {
    // §13.5: без `reentry` любой выход расходует уровень; с `reentry` — только жёсткий стоп
    let mut p = lv_params();
    let t1 = BORN + 100;
    let (t2, t3) = (t1 + 60, t1 + 120);
    let mut c = level_world();
    for t in [t1, t2, t3] {
        for m in t - 5..t {
            set(&mut c, m, 99.5, 99.6, 99.4, 99.5); // NATR выхода > 0
        }
    }
    let s = Sym::from_cols("TEST", c, Vec::new());
    let mut ticks = Vec::new();
    let mut sigs = Vec::new();
    for (t, good) in [(t1, true), (t2, false), (t3, false)] {
        sigs.push(TSig {
            ms: (t as i64 / 1440) * DAY_MS + (t as i64 % 1440) * MIN_MS + 10_000,
            sym: 0,
            level: 0,
            dist: 0,
            t: t as u32,
            day: (t / 1440) as u16,
            idx: ticks.len() as u32 + 1,
            lp: px(100.0),
            far: px(100.0),
            ws: false,
        });
        ticks.push(tk(t, 0, 99.6, false));
        ticks.push(tk(t, 10_000, 99.8, false)); // сигнал
        ticks.push(tk(t, 20_000, 100.0, false)); // вход
        if good {
            ticks.push(tk(t, 30_000, 105.0, false)); // тейки, трейл взведён
            ticks.push(tk(t, 40_000, 104.0, true)); // выход по трейлу
        } else {
            ticks.push(tk(t, 30_000, 90.0, true)); // жёсткий стоп 5 %
        }
    }
    let mut m = HashMap::new();
    m.insert(("TEST".to_string(), t1 / 1440), Arc::new(ticks));
    let src = MemSrc(m);
    let syms = [s];
    let kinds = |r: &super::tick::TickResult| r.trades.iter().map(|t| t.kind).collect::<Vec<_>>();
    let r = run_tick(&syms, &sigs, Side::Long, &p.exit, &p.port, &src);
    assert_eq!(kinds(&r), vec![ExitKind::Trail], "отказы: {:?}", r.rej);
    assert_eq!(
        r.rej[0], 2,
        "второй и третий сигналы — уровень израсходован"
    );
    p.port.reentry = 1;
    let r = run_tick(&syms, &sigs, Side::Long, &p.exit, &p.port, &src);
    assert_eq!(
        kinds(&r),
        vec![ExitKind::Trail, ExitKind::HardStop],
        "отказы: {:?}",
        r.rej
    );
    assert_eq!(r.rej[0], 1, "после жёсткого стопа уровень расходуется");
    p.port.reentry = 2;
    let r = run_tick(&syms, &sigs, Side::Long, &p.exit, &p.port, &src);
    assert_eq!(
        kinds(&r),
        vec![ExitKind::Trail, ExitKind::HardStop, ExitKind::HardStop],
        "отказы: {:?}",
        r.rej
    );
    assert_eq!(r.rej[0], 0, "reentry=2: выходы уровень не расходуют");
    p.port.reentry = 3;
    assert!(p.validate().is_err());
}

fn cand_minutes(c: Cols, p: &Params, side: Side) -> Vec<usize> {
    let s = sym(c);
    let lv = build_levels(&s, &p.sig.lv, side);
    let mut cs = Vec::new();
    candidates(&s, &lv, &p.sig, side, &mut cs);
    cs.iter().map(|c| c.t as usize).collect()
}

#[test]
fn pk_minute_pierce_kills_level_from_next_minute() {
    // минута BORN+50: high 100,30 (> предела 100,225), закрытие 100,10 выше границы запрета ложного пробоя (99,925);
    // BORN+51 — кандидат, если уровень жив. Минута прокола остаётся кандидатом (сигнал до прокола возможен), следующая — нет.
    let mut c = level_world();
    set(&mut c, BORN + 50, 99.85, 100.30, 99.84, 100.10);
    set(&mut c, BORN + 51, 99.85, 99.95, 99.84, 99.90);
    let off = lv_params();
    let mut on = lv_params();
    on.sig.lv.pk = true;
    assert_eq!(
        cand_minutes(c.clone(), &off, Side::Long),
        vec![BORN + 50, BORN + 51],
        "без pk прокол без возврата глубже f уровня не убивает"
    );
    assert_eq!(
        cand_minutes(c.clone(), &on, Side::Long),
        vec![BORN + 50],
        "pk: прокол 0,27 % > τ убивает со следующей минуты"
    );
    // шорт — зеркально
    let m = mirror(&c, 200.0);
    assert_eq!(
        cand_minutes(m.clone(), &off, Side::Short),
        vec![BORN + 50, BORN + 51]
    );
    assert_eq!(cand_minutes(m, &on, Side::Short), vec![BORN + 50]);
    // прокол в пределах τ (100,20 < 100,225) — уровень жив и с pk
    let mut c = level_world();
    set(&mut c, BORN + 50, 99.85, 100.20, 99.84, 100.10);
    set(&mut c, BORN + 51, 99.85, 99.95, 99.84, 99.90);
    assert_eq!(cand_minutes(c, &on, Side::Long), vec![BORN + 50, BORN + 51]);
}

#[test]
fn pk_death_is_permanent() {
    // после прокола уровень не оживает, даже если цена вернулась и стоит в зоне входа много минут подряд
    let mut c = level_world();
    set(&mut c, BORN + 50, 99.85, 100.30, 99.84, 100.10);
    for m in BORN + 51..BORN + 70 {
        set(&mut c, m, 99.85, 99.95, 99.84, 99.95);
    }
    let mut on = lv_params();
    on.sig.lv.pk = true;
    on.sig.ban = false;
    assert_eq!(cand_minutes(c, &on, Side::Long), vec![BORN + 50]);
}

// ───────────── §13.13: `ap` (подход с правильной стороны) и `pb` (прокол убивает уровень на сделке) ─────────────

/// Сигналы монеты TEST стороны `side` по сделкам `days` = [(сутки, сделки)]; без `ap`/`pb` — как прежде.
fn ap_signals(
    c: Cols,
    p: &Params,
    side: Side,
    days: Vec<(usize, Vec<Tick>)>,
) -> (Vec<TSig>, TickCtr) {
    let s = sym(c);
    let mut m = HashMap::new();
    for (d, v) in days {
        m.insert(("TEST".to_string(), d), Arc::new(v));
    }
    let src = MemSrc(m);
    let jobs = vec![(p.sig.clone(), side)];
    let mut outs = vec![Vec::new()];
    let mut ctrs = vec![TickCtr::default()];
    signals_for_coin(&s, 0, &jobs, &src, &mut outs, &mut ctrs);
    (outs.remove(0), ctrs.remove(0))
}

fn sig_minutes(v: &[TSig]) -> Vec<usize> {
    v.iter().map(|s| s.t as usize).collect()
}

/// Мир на двух минутах: `t1` — цена у уровня 100,025 (high 100,30 → дошла до уровня), `t2 = t1 + 1` — зона входа
/// (99,7249 … 100,025). `flip` зеркалит мир для шорта (цена → 200 − цена); вторая часть — конструктор сделок мира.
fn ap_world(t1: usize, flip: bool) -> (Cols, impl Fn(usize, u32, f64, bool) -> Tick) {
    let mut c = level_world();
    set(&mut c, t1, 99.0, 100.30, 98.9, 99.90); // закрытие минуты ниже уровня: часовой бар не закрывается за ним
    set(&mut c, t1 + 1, 99.90, 99.95, 99.85, 99.95);
    let f = move |m: usize, ms: u32, price: f64, bm: bool| -> Tick {
        tk(m, ms, if flip { 200.0 - price } else { price }, bm)
    };
    (if flip { mirror(&c, 200.0) } else { c }, f)
}

fn ap_params(ap: u8) -> Params {
    let mut p = lv_params();
    p.sig.ban = false; // ложный пробой по закрытиям минут не мешает: проверяется только `ap`
    p.sig.ap = ap;
    p
}

#[test]
fn ap_rejects_signal_when_price_came_into_the_zone_from_above() {
    for (flip, side) in [(false, Side::Long), (true, Side::Short)] {
        let t1 = BORN + 100;
        let t2 = t1 + 1;
        let day = t1 / 1440;
        assert_eq!(day, t2 / 1440);
        let (c, f) = ap_world(t1, flip);
        let above = vec![
            f(t1, 0, 99.0, false),
            f(t1, 10_000, 100.30, false), // дошла до уровня
            f(t1, 20_000, 100.10, true),
            f(t2, 0, 99.90, true), // вернулась в зону сверху
            f(t2, 10_000, 99.85, true),
            f(t2, 20_000, 99.95, false), // сигнал (зелёная, в зоне)
        ];
        let (s0, c0) = ap_signals(c.clone(), &ap_params(0), side, vec![(day, above.clone())]);
        assert_eq!(sig_minutes(&s0), vec![t2], "ap=0: сигнал есть, как прежде");
        assert!(!s0[0].ws);
        assert_eq!(c0.ap_wrong, 0);
        let (s1, c1) = ap_signals(c.clone(), &ap_params(1), side, vec![(day, above.clone())]);
        assert!(
            s1.is_empty(),
            "ap=1: цена пришла в зону сверху — сигнал отвергнут"
        );
        assert_eq!(c1.ap_wrong, 1);
        assert_eq!(c1.signals, [0, 0]);
        let (s2, c2) = ap_signals(c, &ap_params(2), side, vec![(day, above)]);
        assert_eq!(sig_minutes(&s2), vec![t2], "ap=2: не отвергает");
        assert!(s2[0].ws, "ap=2: метка «пришла сверху»");
        assert_eq!(c2.ap_wrong, 1);
    }
}

#[test]
fn ap_accepts_approach_from_below_and_recovery_below_the_zone() {
    for (flip, side) in [(false, Side::Long), (true, Side::Short)] {
        let t1 = BORN + 100;
        let t2 = t1 + 1;
        let day = t1 / 1440;
        let (c, f) = ap_world(t1, flip);
        let tail = |v: &mut Vec<Tick>| {
            v.push(f(t2, 0, 99.90, true));
            v.push(f(t2, 10_000, 99.85, true));
            v.push(f(t2, 20_000, 99.95, false));
        };
        // подход снизу: последняя сделка вне зоны — ниже зоны
        let mut below = vec![f(t1, 0, 99.0, false), f(t1, 10_000, 99.3, false)];
        tail(&mut below);
        let (s, cn) = ap_signals(c.clone(), &ap_params(1), side, vec![(day, below)]);
        assert_eq!(sig_minutes(&s), vec![t2]);
        assert_eq!(cn.ap_wrong, 0);
        // сверху → ушла ниже зоны → снова подошла снизу: решает последнее внезонное состояние — «ниже зоны»
        let mut again = vec![
            f(t1, 0, 99.0, false),
            f(t1, 10_000, 100.30, false),
            f(t1, 20_000, 99.2, true),
        ];
        tail(&mut again);
        let (s, cn) = ap_signals(c.clone(), &ap_params(1), side, vec![(day, again)]);
        assert_eq!(sig_minutes(&s), vec![t2]);
        assert_eq!(cn.ap_wrong, 0);
        // решающей сделки нет (все предыдущие — в зоне, прошлых суток нет) → принимается
        let mut none = vec![f(t1, 0, 99.9, false), f(t1, 10_000, 99.95, false)];
        tail(&mut none);
        let (s, _) = ap_signals(c, &ap_params(1), side, vec![(day, none)]);
        assert_eq!(
            sig_minutes(&s),
            vec![t1, t2],
            "сигнал есть и в t1 (99,95 выше открытия минуты)"
        );
    }
}

#[test]
fn ap_looks_back_across_the_day_boundary() {
    for (flip, side) in [(false, Side::Long), (true, Side::Short)] {
        let t2 = 17 * 1440; // первая минута суток
        let t1 = t2 - 1; // последняя минута прошлых суток
        assert!(t1 > BORN);
        let (c, f) = ap_world(t1, flip);
        let today = vec![
            f(t2, 0, 99.90, true),
            f(t2, 10_000, 99.85, true),
            f(t2, 20_000, 99.95, false),
        ];
        let prev_above = vec![
            f(t1, 0, 99.0, false),
            f(t1, 10_000, 100.30, false),
            f(t1, 50_000, 100.10, true),
        ];
        let days = |prev: Vec<Tick>| vec![(16, prev), (17, today.clone())];
        let (s, cn) = ap_signals(c.clone(), &ap_params(1), side, days(prev_above.clone()));
        assert!(s.is_empty(), "до полуночи цена дошла до уровня → сверху");
        assert_eq!(cn.ap_wrong, 1);
        let (s, _) = ap_signals(c.clone(), &ap_params(0), side, days(prev_above));
        assert_eq!(sig_minutes(&s), vec![t2]);
        let prev_below = vec![
            f(t1, 0, 100.30, false),
            f(t1, 10_000, 99.0, false),
            f(t1, 50_000, 99.1, true),
        ];
        let (s, _) = ap_signals(c.clone(), &ap_params(1), side, days(prev_below));
        assert_eq!(
            sig_minutes(&s),
            vec![t2],
            "до полуночи последним внезонным было «ниже зоны»"
        );
        // прошлых суток нет вовсе — решающей сделки нет → принимается
        let (s, _) = ap_signals(c, &ap_params(1), side, vec![(17, today)]);
        assert_eq!(sig_minutes(&s), vec![t2]);
    }
}

#[test]
fn pb_trade_at_or_beyond_level_kills_it_from_next_minute() {
    // минута BORN+50: high на 0,005 % выше уровня (без `pb` уровень жив: прокол ≪ τ, запрет ложного пробоя выключен);
    // минута прокола остаётся кандидатом (сигнал до прокола возможен), следующая — нет; смерть необратима
    let mut c = level_world();
    set(&mut c, BORN + 50, 99.85, 100.030, 99.84, 99.95);
    for m in BORN + 51..BORN + 70 {
        set(&mut c, m, 99.85, 99.95, 99.84, 99.95);
    }
    let mut off = lv_params();
    off.sig.ban = false;
    let mut on = off.clone();
    on.sig.pb = true;
    let all: Vec<usize> = (BORN + 50..BORN + 70).collect();
    assert_eq!(cand_minutes(c.clone(), &off, Side::Long), all);
    assert_eq!(cand_minutes(c.clone(), &on, Side::Long), vec![BORN + 50]);
    // касание ровно уровня (100,025) — тоже «дошла»
    let mut t = level_world();
    set(&mut t, BORN + 50, 99.85, 100.025, 99.84, 99.95);
    set(&mut t, BORN + 51, 99.85, 99.95, 99.84, 99.95);
    assert_eq!(
        cand_minutes(t.clone(), &off, Side::Long),
        vec![BORN + 50, BORN + 51]
    );
    assert_eq!(cand_minutes(t, &on, Side::Long), vec![BORN + 50]);
    // на 0,001 ниже уровня — жив
    let mut u = level_world();
    set(&mut u, BORN + 50, 99.85, 100.024, 99.84, 99.95);
    set(&mut u, BORN + 51, 99.85, 99.95, 99.84, 99.95);
    assert_eq!(cand_minutes(u, &on, Side::Long), vec![BORN + 50, BORN + 51]);
    // шорт — зеркально
    let m = mirror(&c, 200.0);
    assert_eq!(cand_minutes(m.clone(), &off, Side::Short), all);
    assert_eq!(cand_minutes(m, &on, Side::Short), vec![BORN + 50]);
}

#[test]
fn ap_and_pb_keys_parse_validate_and_default_off() {
    let base = Params::base();
    assert_eq!(base.sig.ap, 0);
    assert!(!base.sig.pb);
    let (_, p) = super::params::parse_variant("v ap=1 pb=1").expect("разбор");
    assert_eq!(p.sig.ap, 1);
    assert!(p.sig.pb);
    assert!(super::params::parse_variant("v ap=3").is_err());
    // ключи — часть SigCfg: задания сигналов с разными ap/pb различаются
    let (_, q) = super::params::parse_variant("v").expect("разбор");
    assert_ne!(p.sig, q.sig);
}

// ───────────── §13.12: сетка тейков — перекос цены γ, перекос объёма κ, источник NATR ns ─────────────

#[test]
fn take_grid_defaults_keep_the_old_ladder_and_lots() {
    let ex = Params::base().exit;
    assert_eq!((ex.tp_gamma_m, ex.tp_kappa_m, ex.natr_src), (1000, 0, 0));
    assert!(!ex.take_grid_opts());
    for i in 0..8 {
        assert_eq!(tp_mult_m(&ex, i, 8), 1000 + 4000 * i / 7, "ступень {i}");
    }
    assert_eq!(tp_lots(&ex), vec![NOTIONAL_U * 50 / (100 * 8); 8]);
    assert_eq!(tp_lots(&ex).iter().sum::<i64>(), NOTIONAL_U / 2);
}

#[test]
fn take_grid_gamma_moves_inner_steps_not_the_ends() {
    let mut ex = Params::base().exit;
    ex.n_tp = 5;
    ex.tp_gamma_m = 2000;
    // (i/4)^2 · 4000 = 0, 250, 1000, 2250, 4000
    let sq: Vec<i64> = (0..5).map(|i| tp_mult_m(&ex, i, 5)).collect();
    assert_eq!(sq, vec![1000, 1250, 2000, 3250, 5000]);
    ex.n_tp = 8;
    for g in [500, 2000, 3000] {
        ex.tp_gamma_m = g;
        let v: Vec<i64> = (0..8).map(|i| tp_mult_m(&ex, i, 8)).collect();
        assert_eq!((v[0], v[7]), (1000, 5000), "концы не двигаются, γ={g}");
        assert!(v.windows(2).all(|w| w[0] < w[1]), "возрастание, γ={g}");
        for (i, x) in v.iter().enumerate().take(7).skip(1) {
            let lin = 1000 + 4000 * i as i64 / 7;
            if g < 1000 {
                assert!(*x > lin, "γ<1 — к дальнему краю: ступень {i}");
            } else {
                assert!(*x < lin, "γ>1 — к ближнему краю: ступень {i}");
            }
        }
    }
}

#[test]
fn take_grid_kappa_weights_sum_to_q_and_tilt_by_sign() {
    let mut ex = Params::base().exit;
    for k in [-750, 1000, 3000] {
        ex.tp_kappa_m = k;
        let l = tp_lots(&ex);
        assert_eq!(l.len(), 8);
        assert_eq!(l.iter().sum::<i64>(), NOTIONAL_U / 2, "сумма = q, κ={k}");
        let w = |i: usize| 1.0 + f64::from(k as i32) / 1000.0 * i as f64 / 7.0;
        for i in 1..8 {
            let r = l[i] as f64 / l[0] as f64;
            assert!((r - w(i) / w(0)).abs() < 1e-6, "вес ступени {i}, κ={k}");
        }
        if k < 0 {
            assert!(l[0] > l[7], "κ<0 — больше на ближних");
        } else {
            assert!(l[0] < l[7], "κ>0 — больше на дальних");
        }
    }
    // q = 25 %: сумма долей — q, не 50 %
    ex.q_pct = 25;
    ex.tp_kappa_m = 1000;
    assert_eq!(tp_lots(&ex).iter().sum::<i64>(), NOTIONAL_U / 4);
    // допустимость значений
    for bad in ["tpk=-1", "tpg=0", "tpg=-1", "ns=2"] {
        assert!(
            super::params::parse_variant(&format!("x {bad}")).is_err(),
            "{bad}"
        );
    }
    let (_, p) = super::params::parse_variant("x tpg=0.5 tpk=-0.75 ns=1").expect("разбор");
    assert_eq!(
        (p.exit.tp_gamma_m, p.exit.tp_kappa_m, p.exit.natr_src),
        (500, -750, 1)
    );
    assert!(p.exit.take_grid_opts());
}

#[test]
fn take_grid_gamma_and_kappa_drive_the_simulated_takes() {
    // γ: n = 3, 1 NATR = 0,5 → тейки 100,5 / 100,75 / 101,0 при γ = 1 и 100,5 / 100,625 / 101,0 при γ = 2
    let mut ex = ex_test();
    ex.n_tp = 3;
    let t = vec![(10, 100.0, false), (20, 100.7, false), (30, 100.0, true)];
    let out = sim_stream(&mut stream(t.clone()), 0, Side::Long, NATR, &ex).expect("исход");
    assert_eq!(
        out.takes, 1,
        "γ=1: сделка 100,7 проходит только первый тейк"
    );
    ex.tp_gamma_m = 2000;
    let out = sim_stream(&mut stream(t), 0, Side::Long, NATR, &ex).expect("исход");
    assert_eq!(out.takes, 2, "γ=2: 100,5 и 100,625 пройдены");
    assert_eq!(out.legs[1].px, px(100.625));
    // κ = 1, n = 2: доли 1 : 2 от q = 50 % → 83 333 333 и 166 666 667 мкУЕ; остаток 50 % уходит выходом
    let mut ex = ex_test();
    ex.tp_kappa_m = 1000;
    let t = vec![(10, 100.0, false), (20, 101.5, false), (30, 100.0, true)];
    let out = sim_stream(&mut stream(t), 0, Side::Long, NATR, &ex).expect("исход");
    assert_eq!(out.takes, 2);
    let u: Vec<i64> = out.legs.iter().map(|l| l.u).collect();
    assert_eq!(u, vec![83_333_333, 166_666_667, NOTIONAL_U / 2]);
    assert_eq!(u.iter().sum::<i64>(), NOTIONAL_U);
}

#[test]
fn exit_natr_is_the_closed_minute_before_the_signal_unless_ns() {
    let mut c = flat(3000, 100.0);
    let t = 2000;
    set(&mut c, t, 100.0, 101.0, 99.0, 100.0); // диапазон 2 только в сигнальной минуте
    let s = sym(c);
    let mut ex = Params::base().exit;
    assert_eq!(
        exit_natr(&s, &ex, t),
        0,
        "ns=0: 30 баров до сигнальной минуты — плоские"
    );
    ex.natr_src = 1;
    let v = s.natr_for(30).expect("кэш 30")[t];
    assert!(v > 0);
    assert_eq!(
        exit_natr(&s, &ex, t),
        i64::from(v),
        "ns=1: включая минуту t"
    );
}

#[test]
fn take_grid_opts_flag_marks_non_default_keys() {
    for line in ["x tpg=2", "x tpk=1", "x ns=1"] {
        let (_, p) = super::params::parse_variant(line).expect("разбор");
        assert!(p.exit.take_grid_opts(), "{line}");
    }
}

// ───────────── §13.14: «свежий уровень» — касание в пределах H или проторговка ─────────────

const FT: usize = 3000; // минута сигнала в мире `fresh_world`

/// Плоский мир (все цены 100,0), уровень 101,0 с первым пивотом на баре 10 (минута 600), версия цены с минуты 1000.
fn fresh_world() -> (Sym, Level, i64) {
    let c = flat(5000, 100.0);
    let lv = Level {
        born: 1000,
        end: u32::MAX,
        segs: vec![(1000, px(101.0))],
        first_bar: 10,
        mem: Vec::new(),
    };
    (sym(c), lv, px(101.0))
}

fn fcfg(fh_min: usize, k: usize, share: usize, b_pm: i64) -> super::params::SigCfg {
    let mut c = Params::base().sig;
    c.fr_h_min = fh_min;
    c.fr_k = k;
    c.fr_share = share;
    c.fr_b_pm = b_pm;
    c
}

fn fresh_cols(f: impl Fn(&mut Cols)) -> Sym {
    let mut c = flat(5000, 100.0);
    f(&mut c);
    sym(c)
}

#[test]
fn fresh_off_always_allows() {
    let (s, lv, lp) = fresh_world();
    assert!(!fcfg(0, 120, 0, 500).fresh_on());
    assert!(fresh_ok(&s, &fcfg(0, 120, 0, 500), &lv, 1, lp, FT));
    assert!(fcfg(60, 120, 0, 500).fresh_on() && fcfg(0, 120, 50, 500).fresh_on());
}

#[test]
fn fresh_touch_age_boundary_and_band_edge() {
    let lp = px(101.0);
    let lv = fresh_world().1;
    // касание в минуте FT−100 (high 101,0): возраст 100 — допускается при H = 100, не при H = 99
    let s = fresh_cols(|c| set(c, FT - 100, 100.0, 101.0, 100.0, 100.0));
    assert!(fresh_ok(&s, &fcfg(100, 120, 0, 500), &lv, 1, lp, FT));
    assert!(!fresh_ok(&s, &fcfg(99, 120, 0, 500), &lv, 1, lp, FT));
    // граница полосы: уровень − 0,2 % = 100,798 — касание (≥), на 1e-8 ниже — нет
    let s = fresh_cols(|c| set(c, FT - 1, 100.0, 100.798, 100.0, 100.0));
    assert!(fresh_ok(&s, &fcfg(5, 120, 0, 500), &lv, 1, lp, FT));
    let s = fresh_cols(|c| set(c, FT - 1, 100.0, 100.797_999_99, 100.0, 100.0));
    assert!(!fresh_ok(&s, &fcfg(5, 120, 0, 500), &lv, 1, lp, FT));
    // прокол (high выше уровня) — тоже касание
    let s = fresh_cols(|c| set(c, FT - 2, 100.0, 101.5, 100.0, 100.0));
    assert!(fresh_ok(&s, &fcfg(2, 120, 0, 500), &lv, 1, lp, FT));
    // минутка t (сигнальная) в счёт не идёт: касание в t не освежает уровень
    let s = fresh_cols(|c| set(c, FT, 100.0, 101.0, 100.0, 100.0));
    assert!(!fresh_ok(&s, &fcfg(120, 120, 0, 500), &lv, 1, lp, FT));
}

#[test]
fn fresh_touch_run_counts_from_its_last_minute_and_holes_are_ignored() {
    let lp = px(101.0);
    let lv = fresh_world().1;
    // подряд минутки FT−5…FT−3 — одно касание, возраст от последней (3)
    let s = fresh_cols(|c| {
        for m in FT - 5..=FT - 3 {
            set(c, m, 100.0, 100.9, 100.0, 100.0);
        }
    });
    assert!(fresh_ok(&s, &fcfg(3, 120, 0, 500), &lv, 1, lp, FT));
    assert!(!fresh_ok(&s, &fcfg(2, 120, 0, 500), &lv, 1, lp, FT));
    // минутка без данных (o = 0) касанием не считается
    let s = fresh_cols(|c| {
        set(c, FT - 1, 100.0, 100.9, 100.0, 100.0);
        c.o[FT - 1] = 0;
    });
    assert!(!fresh_ok(&s, &fcfg(10, 120, 0, 500), &lv, 1, lp, FT));
}

#[test]
fn fresh_touch_window_starts_at_first_pivot_bar() {
    let lp = px(101.0);
    let lv = fresh_world().1; // first_bar = 10 → окно с минуты 600
    let before = fresh_cols(|c| set(c, 599, 100.0, 101.0, 100.0, 100.0));
    assert!(!fresh_ok(
        &before,
        &fcfg(10_000, 120, 0, 500),
        &lv,
        1,
        lp,
        FT
    ));
    let inside = fresh_cols(|c| set(c, 600, 100.0, 101.0, 100.0, 100.0));
    assert!(fresh_ok(
        &inside,
        &fcfg(10_000, 120, 0, 500),
        &lv,
        1,
        lp,
        FT
    ));
}

#[test]
fn fresh_consolidation_counts_closes_in_band_and_forbids_closes_at_or_beyond() {
    let lp = px(101.0);
    let lv = fresh_world().1;
    // закрытие 100,7: 0,297 % ниже уровня, high 100,7 < полосы касания — в полосе b = 0,5 %, не касание
    let n_in = |n: usize| {
        fresh_cols(move |c| {
            for m in FT - n..FT {
                set(c, m, 100.7, 100.7, 100.7, 100.7);
            }
        })
    };
    // K = 60, share 50 % → M = 30
    assert!(fresh_ok(&n_in(30), &fcfg(0, 60, 50, 500), &lv, 1, lp, FT));
    assert!(!fresh_ok(&n_in(29), &fcfg(0, 60, 50, 500), &lv, 1, lp, FT));
    // K = 120: M = 60 — 40 минут мало, 60 хватает; окно K = 60 тех же 40 минут пропускает
    assert!(!fresh_ok(&n_in(40), &fcfg(0, 120, 50, 500), &lv, 1, lp, FT));
    assert!(fresh_ok(&n_in(60), &fcfg(0, 120, 50, 500), &lv, 1, lp, FT));
    assert!(fresh_ok(&n_in(40), &fcfg(0, 60, 50, 500), &lv, 1, lp, FT));
    // доля 25 % и 75 % при K = 60: M = 15 и 45
    assert!(fresh_ok(&n_in(15), &fcfg(0, 60, 25, 500), &lv, 1, lp, FT));
    assert!(!fresh_ok(&n_in(14), &fcfg(0, 60, 25, 500), &lv, 1, lp, FT));
    assert!(!fresh_ok(&n_in(44), &fcfg(0, 60, 75, 500), &lv, 1, lp, FT));
    assert!(fresh_ok(&n_in(45), &fcfg(0, 60, 75, 500), &lv, 1, lp, FT));
    // одно закрытие на уровне (101,0) — отказ; вне окна K — не мешает
    let beyond = |m: usize| {
        fresh_cols(move |c| {
            for x in FT - 60..FT {
                set(c, x, 100.7, 100.7, 100.7, 100.7);
            }
            set(c, m, 100.9, 101.2, 100.9, 101.0);
        })
    };
    assert!(!fresh_ok(
        &beyond(FT - 7),
        &fcfg(0, 60, 50, 500),
        &lv,
        1,
        lp,
        FT
    ));
    assert!(fresh_ok(
        &beyond(FT - 61),
        &fcfg(0, 60, 50, 500),
        &lv,
        1,
        lp,
        FT
    ));
    // тень выше уровня при закрытии ниже — не нарушение
    let wick = fresh_cols(|c| {
        for x in FT - 60..FT {
            set(c, x, 100.7, 100.7, 100.7, 100.7);
        }
        set(c, FT - 7, 100.7, 101.5, 100.7, 100.7);
    });
    assert!(fresh_ok(&wick, &fcfg(0, 60, 50, 500), &lv, 1, lp, FT));
    // минутки без данных пропускаются: 30 минут в полосе и 30 дыр — достаточно при M = 30
    let holes = fresh_cols(|c| {
        for x in FT - 60..FT {
            if x % 2 == 0 {
                set(c, x, 100.7, 100.7, 100.7, 100.7);
            } else {
                set(c, x, 100.7, 100.7, 100.7, 100.7);
                c.o[x] = 0;
            }
        }
    });
    assert!(fresh_ok(&holes, &fcfg(0, 60, 50, 500), &lv, 1, lp, FT));
}

#[test]
fn fresh_consolidation_band_width_edge() {
    let lp = px(101.0);
    let lv = fresh_world().1;
    // b = 0,5 %: 101,0 − 0,505 = 100,495 — в полосе (включительно), 100,494 999 99 — нет
    let at = |cl: f64| {
        fresh_cols(move |c| {
            for x in FT - 60..FT {
                set(c, x, cl, cl, cl, cl);
            }
        })
    };
    assert!(fresh_ok(
        &at(100.495),
        &fcfg(0, 60, 100, 500),
        &lv,
        1,
        lp,
        FT
    ));
    assert!(!fresh_ok(
        &at(100.494_999_99),
        &fcfg(0, 60, 100, 500),
        &lv,
        1,
        lp,
        FT
    ));
    // b = 1 % и 0,3 %
    assert!(fresh_ok(
        &at(100.0),
        &fcfg(0, 60, 100, 1000),
        &lv,
        1,
        lp,
        FT
    ));
    assert!(!fresh_ok(
        &at(100.0),
        &fcfg(0, 60, 100, 300),
        &lv,
        1,
        lp,
        FT
    ));
}

#[test]
fn fresh_branches_combine_with_or_and_each_can_be_off() {
    let lp = px(101.0);
    let lv = fresh_world().1;
    let old_touch = |c: &mut Cols| set(c, FT - 1000, 100.0, 101.0, 100.0, 100.0); // касание давно
    let consolidation = |c: &mut Cols| {
        for x in FT - 60..FT {
            set(c, x, 100.7, 100.7, 100.7, 100.7);
        }
    };
    let recent = |c: &mut Cols| set(c, FT - 5, 100.0, 100.9, 100.0, 100.0);
    let a = fcfg(240, 60, 0, 500); // только (а), H = 4 ч
    let b = fcfg(0, 60, 50, 500); // только (б)
    let ab = fcfg(240, 60, 50, 500);
    // давнее касание, нет проторговки: везде отказ
    let s = fresh_cols(old_touch);
    assert!(!fresh_ok(&s, &a, &lv, 1, lp, FT));
    assert!(!fresh_ok(&s, &b, &lv, 1, lp, FT));
    assert!(!fresh_ok(&s, &ab, &lv, 1, lp, FT));
    // давнее касание, проторговка: (б) и «или» — да; (а) — нет
    let s = fresh_cols(|c| {
        old_touch(c);
        consolidation(c);
    });
    assert!(!fresh_ok(&s, &a, &lv, 1, lp, FT));
    assert!(fresh_ok(&s, &b, &lv, 1, lp, FT));
    assert!(fresh_ok(&s, &ab, &lv, 1, lp, FT));
    // свежее касание, нет проторговки: (а) и «или» — да; (б) — нет
    let s = fresh_cols(recent);
    assert!(fresh_ok(&s, &a, &lv, 1, lp, FT));
    assert!(!fresh_ok(&s, &b, &lv, 1, lp, FT));
    assert!(fresh_ok(&s, &ab, &lv, 1, lp, FT));
}

#[test]
fn fresh_short_mirrors_long() {
    // шорт: цены в длинной раме с обратным знаком; мир 100,0 / уровень 99,0 под ценой (зеркало мира лонга около 200)
    let lv = fresh_world().1;
    let lp = -px(99.0);
    let a = fcfg(100, 120, 0, 500);
    let b = fcfg(0, 60, 50, 500);
    // касание: low не выше 99,0 + 0,198 = 99,198
    let s = fresh_cols(|c| set(c, FT - 100, 100.0, 100.0, 99.0, 100.0));
    assert!(fresh_ok(&s, &a, &lv, -1, lp, FT));
    assert!(!fresh_ok(&s, &fcfg(99, 120, 0, 500), &lv, -1, lp, FT));
    // проторговка выше уровня: закрытия 99,3 (0,3 % над уровнем); закрытие на уровне или ниже — отказ
    let cons = |bad: Option<f64>| {
        fresh_cols(move |c| {
            for x in FT - 60..FT {
                set(c, x, 99.3, 99.3, 99.3, 99.3);
            }
            if let Some(v) = bad {
                set(c, FT - 3, v, v, v, v);
            }
        })
    };
    assert!(fresh_ok(&cons(None), &b, &lv, -1, lp, FT));
    assert!(!fresh_ok(&cons(Some(99.0)), &b, &lv, -1, lp, FT));
    assert!(!fresh_ok(&cons(Some(98.5)), &b, &lv, -1, lp, FT));
}

#[test]
fn fresh_rejection_does_not_consume_the_level_and_next_minute_is_rechecked() {
    // мир уровня 100,025: последнее касание — пивот-бар 5 на минуте BORN−120 (возраст на BORN+50 — 170 минут)
    let mut c = level_world();
    set(&mut c, BORN + 50, 99.85, 99.95, 99.84, 99.90); // кандидат; high в полосе (≥ 99,825) — касание для следующей минуты
    set(&mut c, BORN + 51, 99.85, 99.95, 99.84, 99.90);
    let off = lv_params();
    let mut on = lv_params();
    on.sig.fr_h_min = 120;
    assert_eq!(
        cand_minutes(c.clone(), &off, Side::Long),
        vec![BORN + 50, BORN + 51]
    );
    assert_eq!(
        cand_minutes(c.clone(), &on, Side::Long),
        vec![BORN + 51],
        "BORN+50: возраст 170 > 120 — отказ; BORN+51: касание в минуте BORN+50 — допуск"
    );
    on.sig.fr_h_min = 170;
    assert_eq!(
        cand_minutes(c.clone(), &on, Side::Long),
        vec![BORN + 50, BORN + 51],
        "возраст ровно 170 допускается"
    );
    // шорт — зеркально (мир, отражённый около 200)
    let m = mirror(&c, 200.0);
    on.sig.fr_h_min = 120;
    assert_eq!(cand_minutes(m, &on, Side::Short), vec![BORN + 51]);
}

#[test]
fn fresh_keys_parse_and_validate_and_minute_engine_flag() {
    let (_, p) = super::params::parse_variant("x fh=4 fk=60 fm=25 fb=0.3").expect("разбор");
    assert_eq!(
        (p.sig.fr_h_min, p.sig.fr_k, p.sig.fr_share, p.sig.fr_b_pm),
        (240, 60, 25, 300)
    );
    assert!(p.sig.fresh_on());
    let (_, p) = super::params::parse_variant("x fh=0 fm=0").expect("разбор");
    assert!(!p.sig.fresh_on());
    let (_, base) = super::params::parse_variant("x").expect("разбор");
    assert_eq!(
        (
            base.sig.fr_h_min,
            base.sig.fr_k,
            base.sig.fr_share,
            base.sig.fr_b_pm
        ),
        (0, 120, 0, 500)
    );
    for bad in ["fm=101", "fk=0", "fk=1441"] {
        assert!(
            super::params::parse_variant(&format!("x {bad}")).is_err(),
            "{bad}"
        );
    }
}

// ───────────────────────────── §13.16: вход по ленте «как на момент сигнала + lat» ─────────────────────────────

const EDAY: usize = 20;

fn et(off: u32, price: f64, bm: bool) -> Tick {
    Tick {
        px: px(price),
        off,
        bm,
    }
}

fn esig(k: usize, lp: f64) -> TSig {
    TSig {
        ms: EDAY as i64 * DAY_MS + 1000,
        sym: 0,
        level: 0,
        dist: 0,
        t: 0,
        day: EDAY as u16,
        idx: k as u32,
        lp: px(lp),
        far: px(lp),
        ws: false,
    }
}

fn esrc(ticks: Vec<Tick>) -> MemSrc {
    let mut m = HashMap::new();
    m.insert(("TEST".to_string(), EDAY), Arc::new(ticks));
    MemSrc(m)
}

fn etape_cfg(w: i64, x: i64) -> super::params::ExitCfg {
    let mut ex = ex_test();
    ex.lat_ms = 4;
    ex.ent_w_ms = w;
    ex.ent_x_bp = x;
    ex
}

fn esym() -> Sym {
    Sym::from_cols("TEST", flat(10, 100.0), Vec::new())
}

#[test]
fn tape_entry_keys_parse_and_default_to_old_entry() {
    let (_, p) = super::params::parse_variant("x ew=50 ex=2").expect("разбор");
    assert_eq!((p.exit.ent_w_ms, p.exit.ent_x_bp), (50, 2));
    assert!(p.exit.tape_entry());
    let (_, p) = super::params::parse_variant("x lat=4").expect("разбор");
    assert!(!p.exit.tape_entry());
    assert!(super::params::parse_variant("x ew=-1").is_err());
    assert!(super::params::parse_variant("x ex=-1").is_err());
    let src = esrc(vec![et(1000, 99.0, false)]);
    let dec = entry_decision(&src, &esym(), Side::Long, &esig(0, 100.0), &etape_cfg(0, 0));
    assert_eq!(dec, EntryDec::Next, "ew = 0 — прежний вход");
}

#[test]
fn tape_entry_proxy_is_last_trade_of_side_at_or_before_t() {
    // сигнал в 1000 мс (идёт сделка 0), T = 1004
    let ticks = vec![
        et(1000, 99.00, false), // сигнальная: покупатель-тейкер
        et(1002, 98.90, true),  // продавец-тейкер: лонгу не подходит
        et(1004, 99.10, false), // ровно T — берётся
        et(1005, 99.50, false), // после T — не берётся
    ];
    let src = esrc(ticks);
    let sg = esig(0, 100.0);
    let t = EDAY as i64 * DAY_MS + 1004;
    assert_eq!(
        entry_decision(&src, &esym(), Side::Long, &sg, &etape_cfg(100, 0)),
        EntryDec::Fill {
            ms: t,
            p0: px(99.10)
        }
    );
    // шорт: продавец-тейкер ≤ T; цена в раме отрицательна, уровень шорта 98,0 (цена 98,90 выше уровня — до пробоя)
    let mut shg = esig(0, 98.0);
    shg.lp = -px(98.0);
    let dec = entry_decision(&src, &esym(), Side::Short, &shg, &etape_cfg(100, 0));
    assert_eq!(
        dec,
        EntryDec::Fill {
            ms: t,
            p0: -px(98.90)
        }
    );
}

#[test]
fn tape_entry_age_limit_is_inclusive_and_missing_side_is_stale() {
    let ticks = vec![et(500, 99.0, false), et(1000, 98.0, true)];
    let src = esrc(ticks);
    let sg = esig(1, 100.0);
    // последняя сделка покупателя — 500 мс; T = 1004 → возраст 504
    let d = |w| entry_decision(&src, &esym(), Side::Long, &sg, &etape_cfg(w, 0));
    assert_eq!(
        d(504),
        EntryDec::Fill {
            ms: EDAY as i64 * DAY_MS + 1004,
            p0: px(99.0)
        },
        "ровно W — допускается"
    );
    assert_eq!(
        d(503),
        EntryDec::Reject(EntryRej::Stale),
        "W − 1 — нет исхода"
    );
    // сделок стороны вообще нет
    let src = esrc(vec![et(1000, 98.0, true)]);
    assert_eq!(
        entry_decision(
            &src,
            &esym(),
            Side::Long,
            &esig(0, 100.0),
            &etape_cfg(5000, 0)
        ),
        EntryDec::Reject(EntryRej::Stale)
    );
}

#[test]
fn tape_entry_looks_back_into_previous_day_within_age() {
    let mut m = HashMap::new();
    m.insert(
        ("TEST".to_string(), EDAY - 1),
        Arc::new(vec![et(DAY_MS as u32 - 10, 99.0, false)]),
    );
    m.insert(
        ("TEST".to_string(), EDAY),
        Arc::new(vec![et(1, 98.0, true)]),
    );
    let src = MemSrc(m);
    let mut sg = esig(0, 100.0);
    sg.ms = EDAY as i64 * DAY_MS + 1;
    // T = день + 5; сделка предыдущих суток за 15 мс до T
    let dec = entry_decision(&src, &esym(), Side::Long, &sg, &etape_cfg(15, 0));
    assert_eq!(
        dec,
        EntryDec::Fill {
            ms: EDAY as i64 * DAY_MS + 5,
            p0: px(99.0)
        }
    );
    assert_eq!(
        entry_decision(&src, &esym(), Side::Long, &sg, &etape_cfg(14, 0)),
        EntryDec::Reject(EntryRej::Stale)
    );
}

#[test]
fn tape_entry_beyond_level_rule_x_bp_and_short_mirror() {
    // лонг: уровень 100,0
    let long = |p: f64, x: i64| {
        let src = esrc(vec![et(1000, p, false)]);
        entry_decision(
            &src,
            &esym(),
            Side::Long,
            &esig(0, 100.0),
            &etape_cfg(100, x),
        )
    };
    assert!(
        matches!(long(100.0, 0), EntryDec::Fill { .. }),
        "на уровне при X = 0 — допускается"
    );
    assert_eq!(
        long(100.01, 0),
        EntryDec::Reject(EntryRej::Beyond),
        "выше уровня — отказ"
    );
    assert!(
        matches!(long(100.01, 1), EntryDec::Fill { .. }),
        "1 бп = 0,01: допускается при X = 1"
    );
    assert_eq!(long(100.02, 1), EntryDec::Reject(EntryRej::Beyond));
    // шорт: уровень 100,0 в раме −100,0; сделка продавца-тейкера
    let short = |p: f64, x: i64| {
        let src = esrc(vec![et(1000, p, true)]);
        let mut sg = esig(0, 100.0);
        sg.lp = -px(100.0);
        entry_decision(&src, &esym(), Side::Short, &sg, &etape_cfg(100, x))
    };
    assert!(matches!(short(100.0, 0), EntryDec::Fill { .. }));
    assert_eq!(
        short(99.99, 0),
        EntryDec::Reject(EntryRej::Beyond),
        "ниже уровня шорта — отказ"
    );
    assert!(matches!(short(99.99, 1), EntryDec::Fill { .. }));
}

#[test]
fn sim_fill_enters_at_t_and_exits_only_on_trades_after_t() {
    let ex = etape_cfg(100, 0);
    // вход в 1004 по 100,0; сделки до T и ровно в T выходами не бывают; ниже стопа 5 % после T — выход
    let ticks = vec![(1000, 80.0, false), (1004, 80.0, true), (1010, 90.0, true)];
    let out = sim_fill(&mut stream(ticks), 1004, px(100.0), Side::Long, NATR, &ex);
    assert_eq!(out.entry_ms, 1004);
    assert_eq!(out.entry_px, px(100.0), "slip = 0 в ex_test");
    assert_eq!(out.exit_ms, 1010);
    assert_eq!(out.kind, ExitKind::HardStop);
}

#[test]
fn tape_entry_rejection_does_not_consume_level_and_is_listed() {
    // цена-заменитель за уровнем → отказ с причиной; уровень не расходуется; в счётчике «нет исхода»
    let p = lv_params();
    let mut ex = p.exit.clone();
    ex.lat_ms = 4;
    ex.ent_w_ms = 100;
    ex.ent_x_bp = 0;
    let t1 = BORN + 100;
    let mut c = level_world();
    for m in t1 - 5..t1 + 2 {
        set(&mut c, m, 99.5, 99.6, 99.4, 99.5); // NATR выхода > 0
    }
    let s = Sym::from_cols("TEST", c, Vec::new());
    let base = (t1 as i64 % 1440) * MIN_MS;
    let mk = |off: i64, price: f64, bm: bool| Tick {
        px: px(price),
        off: (base + off) as u32,
        bm,
    };
    let ticks = vec![
        mk(0, 99.6, false),
        mk(10_000, 100.20, false), // за уровнем 100,05
        mk(10_010, 100.30, false),
        mk(20_000, 90.0, true),
    ];
    let mut m = HashMap::new();
    m.insert(("TEST".to_string(), t1 / 1440), Arc::new(ticks));
    let src = MemSrc(m);
    let sg = TSig {
        ms: (t1 as i64 / 1440) * DAY_MS + base + 10_000,
        sym: 0,
        level: 0,
        dist: 0,
        t: t1 as u32,
        day: (t1 / 1440) as u16,
        idx: 1,
        lp: px(100.05),
        far: px(100.05),
        ws: false,
    };
    let syms = [s];
    let r = run_tick(&syms, &[sg], Side::Long, &ex, &p.port, &src);
    assert!(r.trades.is_empty());
    assert_eq!(r.entry_rej, vec![(0, 0, sg.ms, EntryRej::Beyond)]);
    assert_eq!(r.rej[5], 1, "в счётчике «нет исхода»");
    assert_eq!(r.rej[0], 0, "уровень не расходуется");
}

// ───────────────────────── §13.19: стоп и выходы от уровня, безубыток, два входа ─────────────────────────

use super::levels::build_levels_far;
use super::tick::{lot2_decision, sim_fill_lc, LevelCtx, Lot2Dec};

/// Выход лота по потоку `ticks` после входа в 1000 мс по цене 100,0 (рама `s`); `lp`/`far` — настоящие цены уровня и дальнего пивота.
fn run_lc(
    ticks: Vec<(i64, f64, bool)>,
    side: Side,
    ex: &super::params::ExitCfg,
    lp: f64,
    far: f64,
) -> TickOut {
    let s = side.s();
    let lc = LevelCtx {
        lp: s * px(lp),
        far: s * px(far),
        rng: None,
    };
    sim_fill_lc(
        &mut stream(ticks),
        1000,
        s * px(100.0),
        side,
        NATR,
        ex,
        Some(lc),
        NOTIONAL_U,
    )
}

#[test]
fn level_options_keys_parse_validate_and_default_off() {
    let (_, p) = super::params::parse_variant("x ew=5 e2=0.1 ls=0.5 tt=5 tb=0.3 rf=0.1 rs=2 bm=3")
        .expect("разбор");
    let e = &p.exit;
    assert_eq!(
        (e.e2_pm, e.ls_m, e.tx_min, e.tx_x_pm, e.rt_f_pm, e.rt_s, e.be_mode),
        (100, 500, 5, 300, 100, 2, 3)
    );
    assert!(e.lvl_exit_opts() && e.level_opts());
    let (_, p) = super::params::parse_variant("x lat=4").expect("разбор");
    assert!(!p.exit.lvl_exit_opts(), "по умолчанию всё выключено");
    assert!(
        super::params::parse_variant("x e2=0.1").is_err(),
        "e2 требует ew"
    );
    assert!(super::params::parse_variant("x bm=4").is_err());
    assert!(super::params::parse_variant("x ls=-1").is_err());
    assert!(super::params::parse_variant("x tt=-1").is_err());
    assert!(super::params::parse_variant("x rf=-0.1").is_err());
}

#[test]
fn far_edge_is_lowest_pivot_for_long_and_highest_for_short() {
    let p = lv_params();
    let lv = build_levels_far(&sym(level_world()), &p.sig.lv, Side::Long);
    assert_eq!(
        lv[0].far_edge(0, 1),
        Some(px(100.0)),
        "пивоты 100,00 и 100,05 — край 100,00"
    );
    let m = mirror(&level_world(), 200.0);
    let lv = build_levels_far(&sym(m), &p.sig.lv, Side::Short);
    // шорт: пивоты-минимумы 100,00 и 99,95 — самый высокий 100,00, в длинной раме −100,00
    assert_eq!(lv[0].far_edge(0, -1), Some(-px(100.0)));
    // без журнала членов дальнего края нет
    let plain = build_levels(&sym(level_world()), &p.sig.lv, Side::Long);
    assert_eq!(plain[0].far_edge(0, 1), None);
}

#[test]
fn ls_stop_is_beyond_far_edge_plus_k_natr_capped_by_hard_stop() {
    let mut ex = ex_test();
    ex.ls_m = 500; // 0,5 NATR = 0,25 при входе 100
                   // дальний пивот ниже входа: стоп 99,90 − 0,25 = 99,65
    let t = vec![
        (1100, 100.2, false),
        (1200, 99.70, true),
        (1300, 99.65, true),
        (1400, 90.0, true),
    ];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 1300));
    assert_eq!(out.legs.last().map(|l| l.px), Some(px(99.65)));
    // дальний пивот выше входа: от входа — 100,00 − 0,25 = 99,75
    let t = vec![(1100, 99.76, true), (1200, 99.75, true)];
    let out = run_lc(t, Side::Long, &ex, 100.4, 100.2);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 1200));
    // шорт зеркально: пивот-минимум 99,80 (самый высокий), стоп от входа 100,00 + 0,25 = 100,25
    let t = vec![
        (1100, 99.9, true),
        (1200, 100.24, false),
        (1300, 100.25, false),
    ];
    let out = run_lc(t, Side::Short, &ex, 99.7, 99.8);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 1300));
    assert_eq!(out.legs.last().map(|l| l.px), Some(px(100.25)));
    // не дальше жёсткого стопа 5 %: k = 20 NATR → 10 % → 95
    ex.ls_m = 20_000;
    let t = vec![(1100, 95.01, true), (1200, 95.0, true)];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 1200));
    // выключен — стоп 5 %, как раньше
    ex.ls_m = 0;
    let t = vec![(1100, 99.0, true), (1200, 95.0, true)];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!(out.exit_ms, 1200);
}

#[test]
fn time_exit_counts_from_first_touch_and_breakout_cancels() {
    let mut ex = ex_test();
    ex.tx_min = 5;
    ex.tx_x_pm = 300; // «пробой» глубиной 0,3 % → 100,6009
                      // касание в 2000; до дедлайна 302000 выхода нет; на первой сделке не раньше — выход «время»
    let t = vec![
        (1100, 100.0, false),
        (2000, 100.3, false),
        (2500, 100.35, false),
        (301_999, 100.4, false),
        (302_000, 100.4, false),
        (303_000, 100.4, false),
    ];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::TimeExit, 302_000));
    assert_eq!(out.kind.name(), "tx");
    // «пробой» до дедлайна отменяет выход (тейк 1 взят на 100,61)
    let t = vec![
        (2000, 100.3, false),
        (3000, 100.61, false),
        (400_000, 100.2, false),
        (500_000, 100.1, false),
    ];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!(out.kind, ExitKind::Forced);
    // касания не было — таймер не идёт
    let t = vec![(1100, 100.0, false), (400_000, 100.1, false)];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!(out.kind, ExitKind::Forced);
    // выключено — выхода нет
    ex.tx_min = 0;
    let t = vec![(2000, 100.3, false), (400_000, 100.4, false)];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!(out.kind, ExitKind::Forced);
}

#[test]
fn return_exit_needs_cross_depth_and_duration_and_resets() {
    let mut ex = ex_test();
    ex.rt_f_pm = 200; // 0,2 % от 100,3 → порог 100,0994
    ex.rt_s = 2;
    // до касания возврата нет, хотя цена ниже порога
    let t = vec![
        (1100, 99.95, false),
        (1200, 100.05, false),
        (2000, 100.3, false),
        (3000, 100.05, false),
        (4000, 100.0, false),
        (5000, 100.0, false),
        (6000, 100.0, false),
    ];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::Return, 5000));
    assert_eq!(out.kind.name(), "rt");
    assert_eq!(out.legs.last().map(|l| l.px), Some(px(100.0)));
    // сделка выше порога сбрасывает отсчёт
    let t = vec![
        (2000, 100.3, false),
        (3000, 100.05, false),
        (4000, 100.2, false),
        (4500, 100.05, false),
        (5000, 100.05, false),
    ];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!(out.kind, ExitKind::Forced);
    // t = 0: первая сделка глубже f
    ex.rt_s = 0;
    let t = vec![
        (2000, 100.3, false),
        (3000, 100.05, false),
        (4000, 100.0, false),
    ];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::Return, 3000));
    // шорт зеркально: пол 99,7, возврат — цена выше 99,7 + 0,2 % = 99,8994
    let t = vec![(2000, 99.7, true), (3000, 99.95, true), (3500, 99.95, true)];
    let out = run_lc(t, Side::Short, &ex, 99.7, 99.8);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::Return, 3000));
}

#[test]
fn be_modes_move_the_stop_as_registered() {
    let base = |bm: u8| {
        let mut ex = ex_test();
        ex.be_mode = bm;
        ex
    };
    // 0: стоп в цену входа после 1-го тейка (100,5)
    let t = vec![
        (1100, 100.6, false),
        (1200, 99.9, true),
        (1300, 101.1, false),
    ];
    let out = run_lc(t, Side::Long, &base(0), 100.3, 99.9);
    assert_eq!(
        (out.kind, out.exit_ms, out.takes),
        (ExitKind::BreakEven, 1200, 1)
    );
    // 1: выкл — стоп не двигается
    let t4 = vec![
        (1100, 100.6, false),
        (1200, 99.9, true),
        (1300, 101.1, false),
        (1400, 99.9, true),
    ];
    let out = run_lc(t4.clone(), Side::Long, &base(1), 100.3, 99.9);
    assert_eq!((out.kind, out.takes), (ExitKind::Forced, 2));
    // 2: после 2-го тейка (101,0)
    let out = run_lc(t4, Side::Long, &base(2), 100.3, 99.9);
    assert_eq!(
        (out.kind, out.exit_ms, out.takes),
        (ExitKind::BreakEven, 1400, 2)
    );
    // 3: за уровнем — дальний пивот 99,90 − 0,25 = 99,65 (k = 0,5 при ls = 0)
    let t = vec![(1100, 100.6, false), (1200, 99.9, true), (1250, 99.6, true)];
    let out = run_lc(t, Side::Long, &base(3), 100.3, 99.9);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::BreakEven, 1250));
    // 3 + ls = 1: стоп k = 1 → 99,90 − 0,5 = 99,40
    let mut ex = base(3);
    ex.ls_m = 1000;
    let t = vec![(1100, 100.6, false), (1200, 99.5, true), (1250, 99.4, true)];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::BreakEven, 1250));
}

/// Мир для проверки двух входов: сигнал на минуте `t1`, цена уровня 100,0, дальний пивот 99,9, второй вход ближе 0,1 % (99,9).
struct E2World {
    syms: Vec<Sym>,
    base: i64,
    t1: usize,
    ex: super::params::ExitCfg,
    port: super::params::PortCfg,
}

fn e2_world() -> E2World {
    let p = lv_params();
    let t1 = BORN + 100;
    let mut c = level_world();
    for m in t1 - 5..t1 + 2 {
        set(&mut c, m, 99.5, 99.6, 99.4, 99.5); // NATR выхода > 0
    }
    let mut ex = ex_test();
    ex.lat_ms = 4;
    ex.ent_w_ms = 100;
    ex.ent_x_bp = 0;
    ex.e2_pm = 100; // 0,1 %
    E2World {
        syms: vec![Sym::from_cols("TEST", c, Vec::new())],
        base: (t1 as i64 % 1440) * MIN_MS,
        t1,
        ex,
        port: p.port,
    }
}

impl E2World {
    fn run(&self, ticks: &[(i64, f64, bool)]) -> super::tick::TickResult {
        let ticks: Vec<Tick> = ticks
            .iter()
            .map(|&(off, price, bm)| Tick {
                px: px(price),
                off: (self.base + off) as u32,
                bm,
            })
            .collect();
        let mut m = HashMap::new();
        m.insert(("TEST".to_string(), self.t1 / 1440), Arc::new(ticks));
        let src = MemSrc(m);
        let sg = TSig {
            ms: (self.t1 as i64 / 1440) * DAY_MS + self.base + 10_000,
            sym: 0,
            level: 0,
            dist: 0,
            t: self.t1 as u32,
            day: (self.t1 / 1440) as u16,
            idx: 1,
            lp: px(100.0),
            far: px(99.9),
            ws: false,
        };
        run_tick(&self.syms, &[sg], Side::Long, &self.ex, &self.port, &src)
    }

    fn abs(&self, off: i64) -> i64 {
        (self.t1 as i64 / 1440) * DAY_MS + self.base + off
    }
}

#[test]
fn e2_second_lot_enters_when_price_comes_within_d_and_both_exit_independently() {
    let w = e2_world();
    let r = w.run(&[
        (0, 99.6, false),
        (10_000, 99.7, false), // сигнал
        (20_000, 99.85, false),
        (30_000, 99.95, false), // подошла ближе 0,1 % и ниже уровня → лот 2
        (40_000, 85.0, true),
    ]);
    assert_eq!(r.trades.len(), 1);
    assert_eq!(r.lot2, [0, 1, 0, 0]);
    let t = &r.trades[0];
    let l2 = t.lot2.as_ref().expect("второй лот");
    assert_eq!(t.entry_ms, w.abs(10_004));
    assert_eq!(l2.entry_ms, w.abs(30_004));
    assert_eq!(
        l2.entry_px,
        px(99.95),
        "цена-заменитель: последняя сделка стороны ≤ T2"
    );
    assert_eq!((t.exit_ms, l2.exit_ms), (w.abs(40_000), w.abs(40_000)));
    assert_eq!(t.kind, ExitKind::HardStop);
    // 50/50: каждая нога выхода — по $250
    assert_eq!(t.legs.last().map(|l| l.u), Some(NOTIONAL_U / 2));
    assert_eq!(l2.legs.last().map(|l| l.u), Some(NOTIONAL_U / 2));
    // брутто двух лотов: (85 − 99,7)/99,7·250 + (85 − 99,95)/99,95·250
    let want = 250.0 * (85.0 - 99.7) / 99.7 + 250.0 * (85.0 - 99.95) / 99.95;
    assert!(
        (t.gross - want).abs() < 0.01,
        "брутто {} против {}",
        t.gross,
        want
    );
    // комиссия: две комиссии входа по $250 = одна по $500; выходы — по $250 каждый, тейкер
    let fee = crate::lob::costs::leg_fee_bps(true) / 1e4;
    let want_fees = 500.0 * fee + 250.0 * (85.0 / 99.7) * fee + 250.0 * (85.0 / 99.95) * fee;
    assert!((t.fees - want_fees).abs() < 0.01, "комиссии {}", t.fees);
}

#[test]
fn e2_immediate_when_lot1_price_already_within_d() {
    let w = e2_world();
    let r = w.run(&[
        (0, 99.6, false),
        (10_000, 99.95, false), // сигнал: цена лота 1 уже в пределах 0,1 %
        (40_000, 85.0, true),
    ]);
    assert_eq!(r.lot2, [1, 0, 0, 0]);
    let t = &r.trades[0];
    let l2 = t.lot2.as_ref().expect("второй лот");
    assert_eq!(
        (l2.entry_ms, l2.entry_px),
        (t.entry_ms, t.entry_px),
        "тот же момент и цена"
    );
}

#[test]
fn e2_cancelled_by_cross_by_lot1_exit_and_rejected_when_tape_is_stale() {
    let w = e2_world();
    // пересекла уровень раньше, чем зашла в зону d → лота 2 нет
    let r = w.run(&[
        (0, 99.6, false),
        (10_000, 99.7, false),
        (20_000, 100.2, false),
        (30_000, 99.95, false),
        (40_000, 85.0, true),
    ]);
    assert_eq!(r.lot2, [0, 0, 1, 0]);
    assert!(r.trades[0].lot2.is_none());
    // лот 1 закрыт стопом до подхода → лота 2 нет
    let r = w.run(&[
        (0, 99.6, false),
        (10_000, 99.7, false),
        (20_000, 85.0, true),
        (30_000, 99.95, false),
    ]);
    assert_eq!(r.lot2, [0, 0, 1, 0]);
    assert!(r.trades[0].lot2.is_none());
    // сделка-триггер есть, но сделки стороны (покупатель-тейкер) старше W → отказ, лот 1 остаётся полусделкой
    let r = w.run(&[
        (0, 99.6, false),
        (10_000, 99.7, false),
        (30_000, 99.95, true),
        (40_000, 85.0, true),
    ]);
    assert_eq!(r.lot2, [0, 0, 0, 1]);
    assert!(r.trades[0].lot2.is_none());
    assert_eq!(r.trades.len(), 1);
}

#[test]
fn lot2_decision_does_not_enter_after_lot1_exit_time() {
    let w = e2_world();
    let ticks = vec![
        et(0, 99.6, false),
        et(10_000, 99.7, false),
        et(30_000, 99.95, false),
    ];
    let mut m = HashMap::new();
    m.insert(("TEST".to_string(), w.t1 / 1440), Arc::new(ticks));
    let src = MemSrc(m);
    let sg = TSig {
        ms: 0,
        sym: 0,
        level: 0,
        dist: 0,
        t: 0,
        day: (w.t1 / 1440) as u16,
        idx: 1,
        lp: px(100.0),
        far: px(99.9),
        ws: false,
    };
    let d0 = (w.t1 / 1440) as i64 * DAY_MS;
    // лот 1 вошёл в 10 004 и закрыт в 30 004: T2 = 30 004 — не раньше выхода → нет второго лота
    let dec = lot2_decision(
        &src,
        &w.syms[0],
        Side::Long,
        &sg,
        &w.ex,
        (d0 + 10_004, px(99.7)),
        d0 + 30_004,
    );
    assert_eq!(dec, Lot2Dec::Cancel);
    // закрыт на 1 мс позже — второй лот входит (T2 = 30 004 < 30 005)
    let dec = lot2_decision(
        &src,
        &w.syms[0],
        Side::Long,
        &sg,
        &w.ex,
        (d0 + 10_004, px(99.7)),
        d0 + 30_005,
    );
    assert_eq!(
        dec,
        Lot2Dec::Fill {
            ms: d0 + 30_004,
            p0: px(99.95),
            immediate: false
        }
    );
}

// ───────────────────────── §13.21: NATR-стоп (`sn/sg/sb/sr/sd`), шаг тейков (`tj`), фильтр признаков (`fa/fo/fv`) ─────────────────────────

use super::ftab::{filter_sigs, FeatTab};

/// Расстояние NATR-стопа без множителя `g`: `|e|·NATR·k0` (формула движка, цена ×1e8).
fn natr_d(e: i64, natr: i64, k0_m: i64) -> i64 {
    (i128::from(e) * i128::from(natr) * i128::from(k0_m) / (100_000_000i128 * 1000)) as i64
}

/// ex_test + NATR-стоп: 1 NATR = 0,5 при входе 100; тейки 100,5 / 101,0; безубыток выключен (`bm = 1`), чтобы j-семейства были осмысленны.
fn nex(sn: f64, sg: u8) -> super::params::ExitCfg {
    let mut e = ex_test();
    e.sn_m = (sn * 1000.0).round() as i64;
    e.sg = sg;
    e.be_mode = 1;
    e
}

/// Исход по потоку: вход по первой сделке покупателя-тейкера (лонг) / продавца (шорт) в момент ≥ 0.
fn sim(ticks: Vec<(i64, f64, bool)>, side: Side, ex: &super::params::ExitCfg) -> TickOut {
    sim_stream(&mut stream(ticks), 0, side, NATR, ex).expect("исход")
}

#[test]
fn v21_keys_parse_validate_default_off_and_minute_engine_rejects() {
    let (_, p) = super::params::parse_variant("x sn=3 sg=3 sd=0.5 bm=1 tj=0.25").expect("разбор");
    let e = &p.exit;
    assert_eq!((e.sn_m, e.sg, e.sd_m, e.tj_m), (3000, 3, 500, 250));
    assert!(e.natr_stop_opts());
    let (_, p) = super::params::parse_variant("x sn=3 sg=1 sb=1").expect("разбор");
    assert_eq!((p.exit.sg, p.exit.sb_m), (1, 1000));
    let (_, p) = super::params::parse_variant("x sn=3 sg=2 sr=1.5").expect("разбор");
    assert_eq!((p.exit.sg, p.exit.sr_m), (2, 1500));
    // выключено по умолчанию; явные нули = база
    let (_, base) = super::params::parse_variant("x lat=4").expect("разбор");
    assert!(!base.exit.natr_stop_opts() && !base.filt.on());
    let (_, zero) =
        super::params::parse_variant("x lat=4 sn=0 sg=0 sb=0 sr=0 sd=0 tj=0").expect("разбор");
    assert_eq!(zero.exit, base.exit);
    assert_eq!(zero.filt, base.filt);
    for bad in [
        "sn=-1",
        "tj=-0.1",
        "sg=4 sn=3",
        "sg=1 sb=1",             // семейство без sn
        "sn=3 sg=1",             // sg=1 без β
        "sn=3 sg=2",             // sg=2 без ρ
        "sn=3 sg=3",             // sg=3 без δ
        "sn=3 sb=1",             // β при sg=0
        "sn=3 sg=1 sb=1 sd=0.5", // чужой параметр
        "sn=3 ns=1",             // NATR стопа — минута t−1
    ] {
        assert!(
            super::params::parse_variant(&format!("x {bad}")).is_err(),
            "{bad}"
        );
    }
    // фильтр признаков
    let (_, p) = super::params::parse_variant("x fa=tfv300 fo=hi fv=-1.5").expect("разбор");
    assert!(p.filt.on());
    assert_eq!(
        (p.filt.name.as_str(), p.filt.hi, p.filt.v),
        ("tfv300", Some(true), Some(-1.5))
    );
    let (_, p) = super::params::parse_variant("x fa=f fo=lo fv=0").expect("разбор");
    assert_eq!(p.filt.hi, Some(false));
    for bad in [
        "fa=f fo=lo",        // нет порога
        "fa=f fv=1",         // нет направления
        "fo=lo fv=1",        // без имени
        "fa=f fo=mid fv=1",  // направление
        "fa=f fo=lo fv=nan", // не конечное
        "fa=f-g fo=lo fv=1", // имя
        "fv=1",
    ] {
        assert!(
            super::params::parse_variant(&format!("x {bad}")).is_err(),
            "{bad}"
        );
    }
    // минутный движок отвергает новые ключи, а не игнорирует
    for line in [
        "x sn=3",
        "x sn=3 sg=1 sb=1",
        "x tj=0.25",
        "x fa=f fo=lo fv=1",
    ] {
        let (_, p) = super::params::parse_variant(line).expect("разбор");
        assert!(
            super::cli::reject_tick_only(&p, line).is_err(),
            "минутный движок принял {line}"
        );
    }
    let (_, p) = super::params::parse_variant("x").expect("разбор");
    assert!(super::cli::reject_tick_only(&p, "x").is_ok());
}

#[test]
fn natr_stop_const_level_is_entry_minus_k0_natr_and_fills_at_or_through() {
    // k0 = 3 → 3·0,5 = 1,5 → стоп 98,5; касание цены стопа срабатывает, исполнение — по цене сделки
    let ex = nex(3.0, 0);
    let t = vec![
        (10, 100.0, false),
        (20, 98.6, true),
        (30, 98.5, true),
        (40, 90.0, true),
    ];
    let out = sim(t, Side::Long, &ex);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 30));
    assert_eq!(out.kind.name(), "stop", "вид выхода — «стоп»");
    assert_eq!(out.legs.last().map(|l| l.px), Some(px(98.5)));
    // гэп: следующая сделка 97,0 → по её цене
    let g = vec![(10, 100.0, false), (20, 98.6, true), (30, 97.0, true)];
    let out = sim(g.clone(), Side::Long, &ex);
    assert_eq!(
        (out.exit_ms, out.legs.last().map(|l| l.px)),
        (30, Some(px(97.0)))
    );
    // выключен — стоп 5 % (95), сделки 98,5 и 97,0 его не трогают
    let out = sim(g, Side::Long, &nex(0.0, 0));
    assert_eq!(out.kind, ExitKind::Forced);
}

#[test]
fn natr_stop_distance_is_clamped_to_half_percent_and_hard_stop() {
    // нижний зажим 0,5 %: k0 = 0,2 → 0,1 → зажато в 0,5 (стоп 99,5)
    let t = vec![(10, 100.0, false), (20, 99.6, true), (30, 99.5, true)];
    let out = sim(t, Side::Long, &nex(0.2, 0));
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 30));
    // верхний зажим S = 5 %: k0 = 20 → 10 % → стоп 95
    let t = vec![(10, 100.0, false), (20, 95.01, true), (30, 95.0, true)];
    let out = sim(t, Side::Long, &nex(20.0, 0));
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 30));
    // S = 2 %: k0 = 5 → 2,5 % → зажато в 2 % (стоп 98,0); k0 = 3 → 1,5 % — не зажато
    let mut ex = nex(5.0, 0);
    ex.s_pm = 2000;
    let t = vec![(10, 100.0, false), (20, 98.01, true), (30, 98.0, true)];
    assert_eq!(sim(t, Side::Long, &ex).exit_ms, 30);
    ex.sn_m = 3000;
    let t = vec![(10, 100.0, false), (20, 98.51, true), (30, 98.5, true)];
    assert_eq!(sim(t, Side::Long, &ex).exit_ms, 30);
    // S < 0,5 %: верхняя граница главнее — стоп = жёсткий (99,7), а не 99,5
    ex.s_pm = 300;
    let t = vec![(10, 100.0, false), (20, 99.71, true), (30, 99.7, true)];
    assert_eq!(sim(t, Side::Long, &ex).exit_ms, 30);
}

#[test]
fn natr_stop_short_mirrors_long() {
    // шорт: вход продавцом-тейкером по 100,0; стоп 100 + 1,5 = 101,5
    let ex = nex(3.0, 0);
    let t = vec![
        (10, 100.0, true),
        (20, 101.4, false),
        (30, 101.5, false),
        (40, 110.0, false),
    ];
    let out = sim(t, Side::Short, &ex);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 30));
    assert_eq!(out.legs.last().map(|l| l.px), Some(px(101.5)));
    // зажимы зеркально: k0 = 0,2 → 0,5 % → 100,5; k0 = 20 → S = 5 % → 105
    let t = vec![(10, 100.0, true), (20, 100.4, false), (30, 100.5, false)];
    assert_eq!(sim(t, Side::Short, &nex(0.2, 0)).exit_ms, 30);
    let t = vec![(10, 100.0, true), (20, 104.99, false), (30, 105.0, false)];
    assert_eq!(sim(t, Side::Short, &nex(20.0, 0)).exit_ms, 30);
    // j-семейство зеркально: шаг δ = 0,5 → после 1-го тейка (99,5) стоп 100 + 2,25 = 102,25
    let mut ex = nex(3.0, 3);
    ex.sd_m = 500;
    let t = vec![
        (10, 100.0, true),
        (20, 99.4, true),
        (30, 102.0, false),
        (40, 102.25, false),
    ];
    let out = sim(t, Side::Short, &ex);
    assert_eq!(
        (out.kind, out.exit_ms, out.takes),
        (ExitKind::HardStop, 40, 1)
    );
}

#[test]
fn natr_stop_linear_in_hold_time_is_recomputed_on_every_trade() {
    // β = 1/ч: d = 1,5·(1 + Δt_ч), Δt от входа (10 мс)
    let mut ex = nex(3.0, 1);
    ex.sb_m = 1000;
    // через 0,5 ч стоп 97,75, через 1 ч — 97,0: сделки выше уровня на своё время не выбивают
    let t = vec![
        (10, 100.0, false),
        (1_800_010, 97.76, true),
        (1_801_010, 97.75, true),
        (3_600_009, 97.01, true),
        (3_600_010, 97.0, true),
        (3_600_011, 90.0, true),
    ];
    let out = sim(t, Side::Long, &ex);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 3_600_010));
    assert_eq!(out.legs.last().map(|l| l.px), Some(px(97.0)));
    // та же цена 97,0 на 1 мс позже уже выше уровня (он ушёл ниже: 96,99999996) — не выбивает; следующая 96,99 — выбивает
    let t = vec![
        (10, 100.0, false),
        (3_600_011, 97.0, true),
        (3_600_012, 96.99, true),
    ];
    let out = sim(t, Side::Long, &ex);
    assert_eq!(out.exit_ms, 3_600_012);
    assert_eq!(out.legs.last().map(|l| l.px), Some(px(96.99)));
    // константа (sg = 0): тот же поток выбивает уже на первой сделке ниже 98,5
    let t = vec![(10, 100.0, false), (1_800_010, 97.76, true)];
    assert_eq!(sim(t, Side::Long, &nex(3.0, 0)).exit_ms, 1_800_010);
    // верхний зажим растущего стопа: через 10 ч g = 11 → 16,5 → S = 5 % (95)
    let t = vec![
        (10, 100.0, false),
        (36_000_010, 95.01, true),
        (36_000_011, 95.0, true),
    ];
    assert_eq!(sim(t, Side::Long, &ex).exit_ms, 36_000_011);
}

#[test]
fn natr_stop_geometric_and_step_follow_filled_takes_from_the_next_trade() {
    let mut geo = nex(3.0, 2);
    geo.sr_m = 1500; // d = 1,5·1,5^j
    let mut step = nex(3.0, 3);
    step.sd_m = 500; // d = 1,5·(1 + 0,5·j)
                     // j = 1 (тейк 100,5): стоп 97,75 — сделка 98,0 уже не выбивает (константа 98,5 выбила бы)
    let t = vec![
        (10, 100.0, false),
        (20, 100.6, false),
        (30, 98.0, true),
        (40, 97.75, true),
    ];
    for ex in [&geo, &step] {
        let out = sim(t.clone(), Side::Long, ex);
        assert_eq!(
            (out.kind, out.exit_ms, out.takes),
            (ExitKind::HardStop, 40, 1)
        );
        assert_eq!(out.legs.last().map(|l| l.px), Some(px(97.75)));
    }
    let out = sim(t, Side::Long, &nex(3.0, 0));
    assert_eq!(
        (out.exit_ms, out.takes),
        (30, 1),
        "sg = 0: стоп не двигается"
    );
    // j = 2 (оба тейка одной сделкой 101,1): geo 96,625; step 97,0
    let t = vec![
        (10, 100.0, false),
        (20, 101.1, false),
        (30, 96.9, true),
        (40, 96.62, true),
    ];
    let out = sim(t.clone(), Side::Long, &geo);
    assert_eq!((out.exit_ms, out.takes), (40, 2), "geo: 96,625");
    let out = sim(t, Side::Long, &step);
    assert_eq!((out.exit_ms, out.takes), (30, 2), "step: 97,0 — 96,9 ниже");
    // j = 0 до тейка: оба семейства — как константа (g = 1)
    let t = vec![(10, 100.0, false), (20, 98.5, true)];
    assert_eq!(sim(t.clone(), Side::Long, &geo).exit_ms, 20);
    assert_eq!(sim(t, Side::Long, &step).exit_ms, 20);
}

fn ex3(tj: f64) -> super::params::ExitCfg {
    let mut e = ex_test();
    e.n_tp = 3;
    e.t_low_m = 1000;
    e.t_high_m = 3000; // ступени 1, 2, 3 NATR → 100,5 / 101,0 / 101,5
    e.tj_m = (tj * 1000.0).round() as i64;
    e
}

#[test]
fn tj_recomputes_remaining_ladder_after_each_take_and_acts_from_the_next_trade() {
    // δ = 1: после j-го тейка оставшиеся ступени — исходные × (1 + j) (не накопительно)
    let t = vec![
        (10, 100.0, false),
        (20, 100.6, false), // тейк 1 (100,5); ступени 2, 3 → ×2: 102,0 / 103,0
        (30, 101.9, false), // прежняя ступень 2 (101,0) исполнилась бы — новая нет
        (40, 102.1, false), // тейк 2 по 102,0; ступень 3 → ×3: 104,5
        (50, 103.2, false), // накопительно было бы 109, исходная 101,5 — здесь не исполняется
        (60, 104.6, false), // тейк 3 по 104,5
    ];
    let out = sim(t, Side::Long, &ex3(1.0));
    assert_eq!(out.takes, 3);
    let v: Vec<(i64, i64)> = out.legs.iter().take(3).map(|l| (l.ms, l.px)).collect();
    assert_eq!(v, vec![(20, px(100.5)), (40, px(102.0)), (60, px(104.5))]);
    assert!(out.legs.iter().take(3).all(|l| l.maker));
    // сделка, прошедшая две ступени сразу, исполняет обе по прежним ценам; пересчёт — со следующей сделки
    let t = vec![
        (10, 100.0, false),
        (20, 101.2, false), // тейки 1 и 2 по 100,5 и 101,0 (лестница на начало сделки)
        (30, 101.6, false), // ступень 3 → ×3: 104,5 — 101,6 не исполняет
        (40, 104.6, false),
    ];
    let out = sim(t.clone(), Side::Long, &ex3(1.0));
    let v: Vec<(i64, i64)> = out.legs.iter().take(3).map(|l| (l.ms, l.px)).collect();
    assert_eq!(v, vec![(20, px(100.5)), (20, px(101.0)), (40, px(104.5))]);
    // выключено (tj = 0): ступень 3 — прежняя 101,5, исполняется на сделке 101,6
    let out = sim(t, Side::Long, &ex3(0.0));
    assert_eq!(out.legs.get(2).map(|l| (l.ms, l.px)), Some((30, px(101.5))));
}

#[test]
fn natr_stop_coexists_with_other_stops_tightest_wins() {
    // (а) безубыток: после 1-го тейка стоп → цена входа (100,0) плотнее NATR-стопа 98,5 → «безубыток»
    let mut ex = nex(3.0, 0);
    ex.be_mode = 0;
    let t = vec![(10, 100.0, false), (20, 100.6, false), (30, 99.9, true)];
    let out = sim(t, Side::Long, &ex);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::BreakEven, 30));
    // (б) трейл плотнее: взвод +1 NATR, откат 1 NATR → 100,1 выше 98,5
    let mut ex = nex(3.0, 0);
    ex.a_m = 1000;
    ex.r_m = 1000;
    ex.t_low_m = 4000;
    ex.t_high_m = 5000;
    let t = vec![(10, 100.0, false), (20, 100.6, false), (30, 100.1, true)];
    let out = sim(t, Side::Long, &ex);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::Trail, 30));
    // (в) NATR-стоп плотнее трейла: откат 5 NATR → трейл 98,1 ниже 98,5 → выбивает NATR-стоп, вид «стоп»
    ex.r_m = 5000;
    let t = vec![
        (10, 100.0, false),
        (20, 100.6, false),
        (30, 98.5, true),
        (40, 98.1, true),
    ];
    let out = sim(t.clone(), Side::Long, &ex);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 30));
    ex.sn_m = 0; // без NATR-стопа выбивает трейл на 98,1
    let out = sim(t, Side::Long, &ex);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::Trail, 40));
    // (г) стоп за краем зоны (`ls`) и NATR-стоп: побеждает ближайший к цене, дальний не берётся
    let mut ex = nex(1.0, 0); // d = max(0,5, 0,5 %) = 0,5 → 99,5
    ex.ls_m = 500; // 0,25
    let t = vec![(1100, 99.7, true), (1200, 99.65, true), (1300, 90.0, true)];
    // дальний пивот 99,9 → ls-уровень 99,65 плотнее 99,5
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 1200));
    assert_eq!(out.legs.last().map(|l| l.px), Some(px(99.65)));
    // дальний пивот 99,0 → ls-уровень 98,75 дальше NATR-стопа 99,5 → выбивает NATR-стоп
    let t = vec![(1100, 99.6, true), (1200, 99.5, true), (1300, 90.0, true)];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.0);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 1200));
    assert_eq!(out.legs.last().map(|l| l.px), Some(px(99.5)));
    // (д) жёсткий стоп S: NATR-стоп зажат в [0,5 %, S], поэтому никогда не дальше жёсткого — при k0 = 20 это ровно 95
    let t = vec![(10, 100.0, false), (20, 95.0, true)];
    assert_eq!(sim(t, Side::Long, &nex(20.0, 0)).kind, ExitKind::HardStop);
}

#[test]
fn natr_stop_executes_with_exit_latency_and_one_bp_slippage_and_gap() {
    // slip 1 бп: вход 100,0 → 100,01; стоп 100,01 − 3·0,5 % · 100,01 = 98,50985; latx = 4 мс
    let mut ex = nex(3.0, 0);
    ex.slip_bps = 1;
    ex.lat_exit_ms = 4;
    let t = vec![
        (10, 100.0, false),
        (20, 98.51, true),    // выше стопа
        (30, 98.50985, true), // ровно на стопе → срабатывание; исполнение не раньше 34
        (33, 98.0, true),     // раньше срока — не исполняется
        (40, 97.0, true),     // первая сделка ≥ 34: гэп → по её цене, минус 1 бп
        (50, 90.0, true),
    ];
    let out = sim(t, Side::Long, &ex);
    assert_eq!(out.entry_px, px(100.0) + px(100.0) / 10_000);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 40));
    assert_eq!(
        out.legs.last().map(|l| l.px),
        Some(px(97.0) - px(97.0) / 10_000)
    );
    assert!(!out.legs.last().is_some_and(|l| l.maker), "стоп — тейкер");
    // уровень — от цены входа лота с проскальзыванием: 98,51 его не трогает
    let t = vec![(10, 100.0, false), (20, 98.51, true)];
    assert_eq!(sim(t, Side::Long, &ex).kind, ExitKind::Forced);
}

/// Мир для NATR-стопа через портфель: сигнал на минуте `t1`; диапазон минут до неё 0,2; при `t1_range` у сигнальной минуты диапазон 3,0.
fn natr_world(t1_range: bool) -> E2World {
    let mut w = e2_world();
    let mut c = flat(w.t1 + 10, 100.0);
    for m in w.t1 - 5..w.t1 + 2 {
        set(&mut c, m, 99.5, 99.6, 99.4, 99.5);
    }
    if t1_range {
        set(&mut c, w.t1, 99.5, 101.0, 98.0, 99.5);
    }
    w.syms = vec![Sym::from_cols("TEST", c, Vec::new())];
    w.ex.e2_pm = 0;
    w.ex.sn_m = 30_000;
    w.ex.t_low_m = 100_000; // тейки далеко: выходы — только стопы
    w.ex.t_high_m = 200_000;
    w
}

#[test]
fn natr_stop_takes_natr_from_the_closed_minute_before_the_signal_not_the_signal_minute() {
    let w = natr_world(true);
    let nat = w.syms[0].natr_for(30).expect("кэш 30");
    let (n_prev, n_sig) = (i64::from(nat[w.t1 - 1]), i64::from(nat[w.t1]));
    assert!(n_prev > 0 && n_sig > n_prev, "{n_prev} {n_sig}");
    let e = px(99.7);
    let (d_prev, d_sig) = (natr_d(e, n_prev, 30_000), natr_d(e, n_sig, 30_000));
    assert!(
        e / 200 < d_prev && d_prev < d_sig && d_sig < e / 20,
        "дистанции внутри зажима: {d_prev} {d_sig}"
    );
    let lvl = e - d_prev;
    let f = |x: i64| x as f64 / 1e8;
    let ticks = [
        (0, 99.6, false),
        (10_000, 99.7, false),
        (20_000, f(lvl + 1), true),
        (30_000, f(lvl), true),
        (40_000, 80.0, true),
    ];
    let r = w.run(&ticks);
    assert_eq!(r.trades.len(), 1);
    let t = &r.trades[0];
    assert_eq!(t.natr_e8, n_prev, "NATR выхода — минута t−1");
    assert_eq!(
        (t.kind, t.exit_ms),
        (ExitKind::HardStop, w.abs(30_000)),
        "стоп по NATR минуты t−1, а не сигнальной (та дала бы уровень на {} ниже)",
        d_sig - d_prev
    );
    assert_eq!(t.legs.last().map(|l| l.px), Some(lvl));
    assert_eq!(t.entry_px, e);
    // та же лента без диапазона в сигнальной минуте — тот же стоп (NATR t−1 от неё не зависит)
    let w2 = natr_world(false);
    let r2 = w2.run(&ticks);
    assert_eq!(r2.trades[0].exit_ms, w2.abs(30_000));
    // вид выхода «стоп» — HardStop: паузы и серии стопов портфеля считают его как жёсткий
    assert_eq!(r2.trades[0].kind, ExitKind::HardStop);
}

#[test]
fn natr_stop_each_lot_of_e2_has_its_own_stop_from_its_own_entry() {
    let mut w = natr_world(false);
    w.ex.e2_pm = 100; // 0,1 %: лот 2 входит по 99,95
    let n = i64::from(w.syms[0].natr_for(30).expect("кэш 30")[w.t1 - 1]);
    let (e1, e2) = (px(99.7), px(99.95));
    let (l1, l2) = (e1 - natr_d(e1, n, 30_000), e2 - natr_d(e2, n, 30_000));
    assert!(l1 < l2);
    let f = |x: i64| x as f64 / 1e8;
    let r = w.run(&[
        (0, 99.6, false),
        (10_000, 99.7, false), // сигнал, лот 1
        (20_000, 99.85, false),
        (30_000, 99.95, false), // лот 2
        (40_000, f(l2), true),  // стоп лота 2; уровень лота 1 ниже
        (50_000, f(l1), true),
    ]);
    assert_eq!(r.lot2, [0, 1, 0, 0]);
    let t = &r.trades[0];
    let l2t = t.lot2.as_ref().expect("второй лот");
    assert_eq!((l2t.kind, l2t.exit_ms), (ExitKind::HardStop, w.abs(40_000)));
    assert_eq!((t.kind1, t.exit_ms), (ExitKind::HardStop, w.abs(50_000)));
    assert_eq!(l2t.legs.last().map(|l| l.px), Some(l2));
    assert_eq!(t.legs.last().map(|l| l.px), Some(l1));
}

// ───────────────────────────── фильтр признаков ─────────────────────────────

fn fsig(ms: i64) -> TSig {
    TSig {
        ms,
        sym: 0,
        level: 0,
        dist: 0,
        t: 0,
        day: 0,
        idx: 0,
        lp: 0,
        far: 0,
        ws: false,
    }
}

fn ftab(text: &str, want: &[&str]) -> anyhow::Result<FeatTab> {
    let idx: HashMap<&str, u16> = HashMap::from([("TEST", 0), ("OTHER", 1)]);
    FeatTab::parse(text, &idx, want)
}

#[test]
fn feature_filter_semantics_lo_hi_missing_passes() {
    let text = "sym,sig_ms,f1,f2,note\r\n\
        TEST,1000,0.5,1,x\r\n\
        TEST,2000,1.5,,y\r\n\
        TEST,3000,nan,3,z\r\n\
        OTHER,1000,9,9,w\r\n\
        GONE,1000,9,9,v\r\n\
        TEST,4000,1.0,2,u\r\n";
    let t = ftab(text, &["f1", "f2"]).expect("разбор");
    assert_eq!(t.rows(), 5, "TEST×4 + OTHER; чужая монета (GONE) пропущена");
    let (c1, c2) = (t.col("f1").expect("f1"), t.col("f2").expect("f2"));
    assert_eq!(t.value(c1, 0, 1000), Some(0.5));
    assert_eq!(t.value(c1, 1, 1000), Some(9.0), "ключ — (монета, время)");
    assert_eq!(t.value(c1, 0, 5000), None);
    assert!(t.value(c1, 0, 3000).is_some_and(f64::is_nan));
    assert!(
        t.value(c2, 0, 2000).is_some_and(f64::is_nan),
        "пусто — нет значения"
    );
    let sigs: Vec<TSig> = [1000, 2000, 3000, 4000, 5000].map(fsig).to_vec();
    let ms = |v: &[TSig]| v.iter().map(|s| s.ms).collect::<Vec<_>>();
    // lo: отбросить value < 1,0 — 1000 (0,5); 1,0 не отбрасывается; NaN и нет строки — проходят
    let (kept, st) = filter_sigs(&sigs, &t, c1, false, 1.0);
    assert_eq!(ms(&kept), vec![2000, 3000, 4000, 5000]);
    assert_eq!((st.cands, st.dropped, st.no_key, st.no_value), (5, 1, 1, 1));
    // hi: отбросить value > 1,0 — 2000 (1,5)
    let (kept, st) = filter_sigs(&sigs, &t, c1, true, 1.0);
    assert_eq!(ms(&kept), vec![1000, 3000, 4000, 5000]);
    assert_eq!(st.dropped, 1);
    // пустое значение второго признака проходит при любом пороге
    let (kept, _) = filter_sigs(&sigs[1..2], &t, c2, false, 100.0);
    assert_eq!(ms(&kept), vec![2000]);
}

#[test]
fn feature_table_rejects_bad_input_and_skips_unwanted_columns() {
    assert!(ftab("", &["f"]).is_err());
    assert!(ftab("coin,sig_ms,f\n", &["f"]).is_err(), "заголовок");
    assert!(
        ftab("sym,sig_ms,g\nTEST,1,1\n", &["f"]).is_err(),
        "нет столбца"
    );
    assert!(
        ftab("sym,sig_ms,f\nTEST,1\n", &["f"]).is_err(),
        "мало полей"
    );
    assert!(
        ftab("sym,sig_ms,f\nTEST,1,abc\n", &["f"]).is_err(),
        "не число"
    );
    assert!(
        ftab("sym,sig_ms,f\nTEST,x,1\n", &["f"]).is_err(),
        "время не число"
    );
    // текст в НЕ нужном столбце не разбирается
    assert!(ftab("sym,sig_ms,f,note\nTEST,1,2,hello\n", &["f"]).is_ok());
}

/// Один сигнал в мире `natr_world`, лента для него и портфель; `keep` — фильтр по столбцу `f` со значением 5 у кандидата.
fn filtered_run(
    w: &E2World,
    ticks: &[(i64, f64, bool)],
    keep: Option<(bool, f64)>,
) -> super::tick::TickResult {
    let ticks: Vec<Tick> = ticks
        .iter()
        .map(|&(off, price, bm)| Tick {
            px: px(price),
            off: (w.base + off) as u32,
            bm,
        })
        .collect();
    let mut m = HashMap::new();
    m.insert(("TEST".to_string(), w.t1 / 1440), Arc::new(ticks));
    let src = MemSrc(m);
    let sg = TSig {
        ms: (w.t1 as i64 / 1440) * DAY_MS + w.base + 10_000,
        sym: 0,
        level: 0,
        dist: 0,
        t: w.t1 as u32,
        day: (w.t1 / 1440) as u16,
        idx: 1,
        lp: px(100.0),
        far: px(99.9),
        ws: false,
    };
    let mut sigs = vec![sg];
    if let Some((hi, fv)) = keep {
        let t = ftab(&format!("sym,sig_ms,f\nTEST,{},5\n", sg.ms), &["f"]).expect("таблица");
        sigs = filter_sigs(&sigs, &t, 0, hi, fv).0;
    }
    run_tick(&w.syms, &sigs, Side::Long, &w.ex, &w.port, &src)
}

#[test]
fn feature_filter_pass_all_is_identical_and_cut_all_gives_zero_trades() {
    let w = natr_world(false);
    let ticks = [
        (0, 99.6, false),
        (10_000, 99.7, false),
        (20_000, 100.6, false),
        (30_000, 80.0, true),
    ];
    let plain = filtered_run(&w, &ticks, None);
    assert_eq!(plain.trades.len(), 1);
    // значение 5: lo при пороге не выше 5 и hi при пороге не ниже 5 — пропускают всех
    for keep in [(false, -1e18), (true, 1e18), (false, 5.0), (true, 5.0)] {
        let r = filtered_run(&w, &ticks, Some(keep));
        assert_eq!(
            format!("{:?}", r.trades),
            format!("{:?}", plain.trades),
            "{keep:?}"
        );
        assert_eq!(r.daily, plain.daily);
        assert_eq!(r.coinday, plain.coinday);
        assert_eq!(r.rej, plain.rej);
    }
    // режет всех
    for keep in [(false, 1e18), (true, -1e18)] {
        let r = filtered_run(&w, &ticks, Some(keep));
        assert!(r.trades.is_empty(), "{keep:?}");
        assert_eq!(r.rej, [0; 6], "отброшенный кандидат не доходит до портфеля");
        assert!(r.daily.iter().all(|&d| d == 0.0));
    }
}

// ───────────────────────────── §13.21: `busy` и режим сигналов портфеля ─────────────────────────────

/// Портфель по сигналам одной монеты: `sigs` — (время сигнала в мс от начала минуты `t1`, номер сделки-триггера в ленте); уровень сигнала — его номер.
fn multi_run(
    w: &E2World,
    ticks: &[(i64, f64, bool)],
    sigs: &[(i64, u32)],
    port: &super::params::PortCfg,
) -> super::tick::TickResult {
    let ticks: Vec<Tick> = ticks
        .iter()
        .map(|&(off, price, bm)| Tick {
            px: px(price),
            off: (w.base + off) as u32,
            bm,
        })
        .collect();
    let mut m = HashMap::new();
    m.insert(("TEST".to_string(), w.t1 / 1440), Arc::new(ticks));
    let src = MemSrc(m);
    let sigs: Vec<TSig> = sigs
        .iter()
        .enumerate()
        .map(|(k, &(off, idx))| TSig {
            ms: (w.t1 as i64 / 1440) * DAY_MS + w.base + off,
            sym: 0,
            level: k as u32,
            dist: 0,
            t: w.t1 as u32,
            day: (w.t1 / 1440) as u16,
            idx,
            lp: px(100.0),
            far: px(99.9),
            ws: false,
        })
        .collect();
    run_tick(&w.syms, &sigs, Side::Long, &w.ex, port, &src)
}

/// Режим сигналов (§13.21): лимиты портфеля выключены значениями ключей; одна сделка на уровень остаётся.
fn sig_mode_port() -> super::params::PortCfg {
    let (_, p) =
        super::params::parse_variant("x p=100000 c=0 m=1000000 s_glob=1000000 ww=0 busy=0")
            .expect("разбор");
    p.port
}

fn port_with(line: &str) -> super::params::PortCfg {
    super::params::parse_variant(&format!("x {line}"))
        .expect("разбор")
        .1
        .port
}

fn sum_net(r: &super::tick::TickResult) -> f64 {
    r.trades.iter().map(|t| t.net).sum()
}

#[test]
fn busy_key_parses_defaults_to_one_and_minute_engine_rejects_it() {
    let (_, base) = super::params::parse_variant("x").expect("разбор");
    assert!(base.port.busy);
    for on in ["x busy=1", "x busy=2"] {
        assert!(
            super::params::parse_variant(on)
                .expect("разбор")
                .1
                .port
                .busy
        );
    }
    assert_eq!(
        super::params::parse_variant("x busy=1").expect("разбор").1,
        base,
        "явное busy=1 = база"
    );
    let (_, off) = super::params::parse_variant("x busy=0").expect("разбор");
    assert!(!off.port.busy);
    assert!(super::cli::reject_tick_only(&off, "x busy=0").is_err());
    assert!(super::cli::reject_tick_only(&base, "x busy=1").is_ok());
}

#[test]
fn busy_off_lets_a_busy_coin_take_a_second_level_with_independent_exits_and_sums_accounting() {
    let mut w = natr_world(false);
    w.ex.e2_pm = 0;
    w.ex.sn_m = 0;
    // сигнал A (лента 1) и сигнал B другого уровня (лента 3) на той же монете; жёсткий стоп 5 %: A (99,7) → 94,715; B (99,85) → 94,8575
    let ticks = [
        (0, 99.6, false),
        (10_000, 99.7, false), // сигнал A
        (20_000, 99.8, false),
        (30_000, 99.85, false), // сигнал B, позиция A ещё открыта
        (40_000, 94.8, true),   // ниже стопа B, выше стопа A
        (50_000, 94.7, true),   // ниже стопа A
    ];
    let sigs = [(10_000, 1), (30_000, 3)];
    // busy = 1 (по умолчанию и явно): B отвергнут как «занята», результат = только A
    let mut p1 = port_with("busy=1");
    p1.p = 3;
    let r1 = multi_run(&w, &ticks, &sigs, &p1);
    assert_eq!((r1.trades.len(), r1.rej), (1, [0, 1, 0, 0, 0, 0]));
    let r_def = multi_run(&w, &ticks, &sigs, &w.port);
    assert_eq!(
        (format!("{:?}", r_def.trades), r_def.rej, r_def.daily),
        (format!("{:?}", r1.trades), r1.rej, r1.daily),
        "лимиты по умолчанию и busy=1 — одно и то же"
    );
    // busy = 0: обе позиции открыты одновременно, выходы независимы
    let mut p0 = w.port.clone();
    p0.busy = false;
    let r0 = multi_run(&w, &ticks, &sigs, &p0);
    assert_eq!((r0.trades.len(), r0.rej), (2, [0; 6]));
    let (a, b) = (&r0.trades[0], &r0.trades[1]);
    assert_eq!(
        (a.level, a.exit_ms, a.kind),
        (0, w.abs(50_000), ExitKind::HardStop)
    );
    assert_eq!(
        (b.level, b.exit_ms, b.kind),
        (1, w.abs(40_000), ExitKind::HardStop)
    );
    assert!(
        b.entry_ms > a.entry_ms && b.entry_ms < a.exit_ms,
        "одновременно"
    );
    assert_eq!(a.legs.last().map(|l| l.px), Some(px(94.7)));
    assert_eq!(b.legs.last().map(|l| l.px), Some(px(94.8)));
    // сделка A при busy = 0 такая же, как при busy = 1
    assert_eq!(format!("{:?}", a), format!("{:?}", r1.trades[0]));
    // учёт: сумма по суткам и по монето-суткам = сумма сделок
    let tot = sum_net(&r0);
    assert!((r0.daily.iter().sum::<f64>() - tot).abs() < 1e-9);
    assert!((r0.coinday.values().sum::<f64>() - tot).abs() < 1e-9);
    assert!((tot - (sum_net(&r1) + b.net)).abs() < 1e-9);
}

#[test]
fn sig_mode_off_values_disable_every_portfolio_limit_and_each_one_alone_binds() {
    let mut w = natr_world(false);
    w.ex.sn_m = 0;
    // 6 сигналов одной монеты (разные уровни), каждый выбит жёстким стоп-ордером через 5 с, следующий — через 15 с
    let (mut ticks, mut sigs) = (vec![(0, 99.6, false)], Vec::new());
    for k in 0..6i64 {
        let b = 20_000 * k;
        sigs.push((b + 10_000, ticks.len() as u32));
        ticks.push((b + 10_000, 99.7, false));
        ticks.push((b + 15_000, 90.0, true));
    }
    let run = |port: &super::params::PortCfg| multi_run(&w, &ticks, &sigs, port);
    // по умолчанию: пауза после стопа (c = 5 мин) отвергает остальные
    let r = run(&w.port);
    assert_eq!((r.trades.len(), r.rej), (1, [0, 0, 5, 0, 0, 0]));
    // режим сигналов — все 6 сделок, ни одного отказа
    let r = run(&sig_mode_port());
    assert_eq!((r.trades.len(), r.rej), (6, [0; 6]));
    assert!(r.trades.iter().all(|t| t.kind == ExitKind::HardStop));
    // каждый ключ режима по отдельности (остальные — как в режиме сигналов) действует
    let r = run(&port_with(
        "p=100000 c=5 m=1000000 s_glob=1000000 ww=0 busy=0",
    ));
    assert_eq!((r.trades.len(), r.rej[2]), (1, 5), "c = 5");
    let r = run(&port_with(
        "p=100000 c=0 m=3 mm=60 s_glob=1000000 ww=0 busy=0",
    ));
    assert_eq!(
        (r.trades.len(), r.rej[2]),
        (3, 3),
        "m = 3: пауза монеты после 3-го стопа"
    );
    let r = run(&port_with(
        "p=100000 c=0 m=1000000 s_glob=4 w=10 ww=60 busy=0",
    ));
    assert_eq!(
        (r.trades.len(), r.rej[3]),
        (4, 2),
        "s_glob = 4, ww = 60: общая пауза"
    );
    let r = run(&port_with(
        "p=100000 c=0 m=1000000 s_glob=4 w=10 ww=0 busy=0",
    ));
    assert_eq!(
        (r.trades.len(), r.rej),
        (6, [0; 6]),
        "ww = 0 выключает общую паузу и при малом s_glob"
    );
    // потолок позиций p и «занята»: пять сигналов подряд, позиции живут до общего выбива
    let (mut t2, mut s2) = (vec![(0, 99.6, false)], Vec::new());
    for k in 0..5i64 {
        s2.push((10_000 + 1000 * k, t2.len() as u32));
        t2.push((10_000 + 1000 * k, 99.7, false));
    }
    t2.push((60_000, 90.0, true));
    let r = multi_run(&w, &t2, &s2, &sig_mode_port());
    assert_eq!(
        (r.trades.len(), r.rej),
        (5, [0; 6]),
        "пять позиций одной монеты разом"
    );
    let tot = sum_net(&r);
    assert!((r.daily.iter().sum::<f64>() - tot).abs() < 1e-9);
    assert!((r.coinday.values().sum::<f64>() - tot).abs() < 1e-9);
    let r = multi_run(
        &w,
        &t2,
        &s2,
        &port_with("p=3 c=0 m=1000000 s_glob=1000000 ww=0 busy=0"),
    );
    assert_eq!((r.trades.len(), r.rej[4]), (3, 2), "p = 3: потолок");
    let r = multi_run(
        &w,
        &t2,
        &s2,
        &port_with("p=100000 c=0 m=1000000 s_glob=1000000 ww=0 busy=1"),
    );
    assert_eq!((r.trades.len(), r.rej[1]), (1, 4), "busy = 1: «занята»");
}

// ───────────────────────── §13.28: вход лимиткой (`mk`) ─────────────────────────

use super::tick::{mk_decision, run_tick_mk, tick_at};

/// 100 сделок суток `EDAY` в первые 100 мс на сетке 0,01 (цены 98,00 / 98,01): тик 0,01 и ≥ 100 сделок до сигнала.
fn mk_fill() -> Vec<Tick> {
    (0..100u32)
        .map(|i| et(i, if i % 2 == 0 { 98.00 } else { 98.01 }, i % 3 == 0))
        .collect()
}

/// Решение мейкера для сигнала на сделке `extra[0]` (1000 мс), уровень `lp` (рама), заявка `mode`.
fn mk_run(extra: Vec<Tick>, side: Side, lp: i64, mode: u8) -> EntryDec {
    let mut v = mk_fill();
    let k = v.len();
    v.extend(extra);
    let src = esrc(v);
    let mut sg = esig(k, 0.0);
    sg.lp = lp;
    let ex = etape_cfg(100, 0);
    let sym = esym();
    let mut cache = HashMap::new();
    let mut tick_of = |t: i64| tick_at(&src, &sym.name, 0, t, &mut cache);
    mk_decision(&src, &sym, side, &sg, &ex, mode, &mut tick_of)
}

const MK_DAY: i64 = EDAY as i64 * DAY_MS;

fn fill(off: i64, price: f64) -> EntryDec {
    EntryDec::Fill {
        ms: MK_DAY + off,
        p0: px(price),
    }
}

fn rej(why: EntryRej) -> EntryDec {
    EntryDec::Reject(why)
}

#[test]
fn mk1_limit_is_ask_minus_tick_and_fills_only_on_a_trade_strictly_through() {
    let run = |tail: Vec<Tick>| {
        let mut e = vec![et(1000, 99.50, false)];
        e.extend(tail);
        mk_run(e, Side::Long, px(100.0), 1)
    };
    // лимит 99,49; сделка ровно на лимите не заполняет, 99,48 — заполняет по цене лимита в момент сделки
    assert_eq!(
        run(vec![et(1010, 99.49, true), et(1020, 99.50, false)]),
        rej(EntryRej::Unfilled)
    );
    assert_eq!(
        run(vec![et(1010, 99.49, true), et(1030, 99.48, true)]),
        fill(1030, 99.49)
    );
    // заявка встаёт в T = 1004: сделка в T и раньше не заполняет
    assert_eq!(run(vec![et(1004, 99.40, true)]), rej(EntryRej::Unfilled));
    // жизнь — до конца минуты, содержащей T
    assert_eq!(
        run(vec![et(59_999, 99.40, true)]),
        fill(59_999, 99.49),
        "последняя миллисекунда минуты"
    );
    assert_eq!(
        run(vec![et(60_000, 99.40, true)]),
        rej(EntryRej::Unfilled),
        "следующая минута — заявки нет"
    );
}

#[test]
fn mk1_is_cancelled_by_a_trade_at_or_beyond_the_level_but_only_before_the_fill() {
    let run = |tail: Vec<Tick>| {
        let mut e = vec![et(1000, 99.50, false)];
        e.extend(tail);
        mk_run(e, Side::Long, px(100.0), 1)
    };
    assert_eq!(
        run(vec![et(1010, 100.00, false), et(1020, 99.40, true)]),
        rej(EntryRej::Touched),
        "сделка на уровне снимает заявку"
    );
    assert_eq!(
        run(vec![et(1010, 100.01, false)]),
        rej(EntryRej::Touched),
        "за уровнем — тоже"
    );
    assert_eq!(
        run(vec![et(1010, 99.48, true), et(1020, 100.00, false)]),
        fill(1010, 99.49),
        "касание после заполнения заявку не трогает"
    );
}

#[test]
fn mk1_short_mirrors_the_long_in_the_frame() {
    // шорт: уровень 98,0 (рама −98,0); сигнальная — продавец-тейкер 98,90; лимит = bid + тик = 98,91 (рама −98,91)
    let run = |tail: Vec<Tick>| {
        let mut e = vec![et(1000, 98.90, true)];
        e.extend(tail);
        mk_run(e, Side::Short, -px(98.0), 1)
    };
    let short = |off: i64, price: f64| EntryDec::Fill {
        ms: MK_DAY + off,
        p0: -px(price),
    };
    assert_eq!(
        run(vec![et(1010, 98.91, false), et(1020, 98.92, false)]),
        short(1020, 98.91),
        "сквозь — цена выше лимита; ровно на лимите не заполняет"
    );
    assert_eq!(run(vec![et(1010, 98.91, false)]), rej(EntryRej::Unfilled));
    assert_eq!(
        run(vec![et(1010, 98.00, true), et(1020, 98.95, false)]),
        rej(EntryRej::Touched),
        "сделка на уровне (рама ≥ −98,0) снимает заявку"
    );
}

#[test]
fn mk2_rests_at_the_level_after_the_breakout_and_fills_on_the_retest_through_it() {
    let run = |tail: Vec<Tick>, lp: f64| {
        let mut e = vec![et(1000, 99.50, false)];
        e.extend(tail);
        mk_run(e, Side::Long, px(lp), 2)
    };
    // пробой (≥ 100,00) в 1010 → рабочая с 1014; сделка в 1012 ниже лимита ещё не заполняет, в 1020 — заполняет по 100,00
    assert_eq!(
        run(
            vec![
                et(1010, 100.00, false),
                et(1012, 99.99, true),
                et(1020, 99.99, true)
            ],
            100.0
        ),
        fill(1020, 100.00)
    );
    // ровно на лимите — нет
    assert_eq!(
        run(vec![et(1010, 100.01, false), et(1020, 100.00, true)], 100.0),
        rej(EntryRej::Unfilled)
    );
    // без пробоя — нет; пробой без ретеста — нет
    assert_eq!(
        run(vec![et(1010, 99.99, false), et(1020, 99.98, true)], 100.0),
        rej(EntryRej::Unfilled)
    );
    assert_eq!(
        run(vec![et(1010, 100.20, false), et(1020, 100.10, true)], 100.0),
        rej(EntryRej::Unfilled)
    );
    // жизнь — до конца минуты сигнала
    assert_eq!(
        run(
            vec![et(1010, 100.00, false), et(60_000, 99.99, true)],
            100.0
        ),
        rej(EntryRej::Unfilled)
    );
    // уровень 100,004 не на тике: лимит округлён вниз до 100,00; сделка на 100,00 — ещё не пробой (< 100,004)
    assert_eq!(
        run(
            vec![et(1010, 100.01, false), et(1020, 99.99, true)],
            100.004
        ),
        fill(1020, 100.00)
    );
    assert_eq!(
        run(
            vec![et(1010, 100.00, false), et(1020, 99.99, true)],
            100.004
        ),
        rej(EntryRej::Unfilled)
    );
    // шорт: уровень 98,004 (рама −98,004) → лимит продажи 98,01 (округлён вверх); пробой — сделка ≤ 98,004, ретест — выше 98,01
    let mut e = vec![et(1000, 98.90, true)];
    e.extend([et(1010, 98.00, true), et(1020, 98.02, false)]);
    assert_eq!(
        mk_run(e, Side::Short, -px(98.004), 2),
        EntryDec::Fill {
            ms: MK_DAY + 1020,
            p0: -px(98.01)
        }
    );
}

#[test]
fn tick_is_causal_windowed_and_falls_back_to_the_last_defined_one() {
    let t = MK_DAY + 1000;
    let on_grid = |n: u32, step: f64| -> Vec<Tick> {
        (0..n)
            .map(|i| et(i, 98.00 + step * f64::from(i % 3), false))
            .collect()
    };
    let tick = |today: Vec<Tick>, old: Vec<(usize, Vec<Tick>)>| {
        let mut m = HashMap::new();
        m.insert(("TEST".to_string(), EDAY), Arc::new(today));
        for (d, v) in old {
            m.insert(("TEST".to_string(), d), Arc::new(v));
        }
        tick_at(&MemSrc(m), "TEST", 0, t, &mut HashMap::new())
    };
    // сегодня 100 сделок на сетке 0,02 → 0,02; сделки после T тик не меняют
    let mut later = on_grid(100, 0.02);
    assert_eq!(tick(later.clone(), vec![]), Some(2_000_000));
    later.push(et(5000, 98.01, false));
    later.push(et(6000, 98.015, false));
    assert_eq!(
        tick(later, vec![]),
        Some(2_000_000),
        "сделки после T не читаются"
    );
    // окно: сутки EDAY−7 на сетке 0,03 входят (НОД 0,01), EDAY−8 — нет
    assert_eq!(
        tick(on_grid(100, 0.02), vec![(EDAY - 7, on_grid(10, 0.03))]),
        Some(1_000_000)
    );
    assert_eq!(
        tick(on_grid(100, 0.02), vec![(EDAY - 8, on_grid(10, 0.03))]),
        Some(2_000_000)
    );
    // меньше 100 сделок в окне → последний определённый тик (EDAY−12: сетка 0,05)
    assert_eq!(
        tick(on_grid(50, 0.02), vec![(EDAY - 12, on_grid(150, 0.05))]),
        Some(5_000_000)
    );
    // нигде нет 100 сделок → тика нет; мейкер отказывает `notick`, тейкер по тому же сигналу входит
    assert_eq!(tick(on_grid(50, 0.02), vec![]), None);
    let mut v = on_grid(50, 0.02);
    v.push(et(1000, 99.50, false));
    let src = esrc(v);
    let sg = esig(50, 100.0);
    let ex = etape_cfg(100, 0);
    let sym = esym();
    let mut cache = HashMap::new();
    let mut tick_of = |t: i64| tick_at(&src, &sym.name, 0, t, &mut cache);
    assert_eq!(
        mk_decision(&src, &sym, Side::Long, &sg, &ex, 1, &mut tick_of),
        rej(EntryRej::NoTick)
    );
    assert!(matches!(
        entry_decision(&src, &sym, Side::Long, &sg, &ex),
        EntryDec::Fill { .. }
    ));
}

#[test]
fn mk_key_parses_validates_and_keeps_the_params_debug_string_at_mk0() {
    use super::params::parse_variant;
    let (_, p0) = parse_variant("x ew=5").expect("разбор");
    assert_eq!(p0.mk, 0);
    for (line, mk) in [("x ew=5 mk=1", 1), ("x ew=5 mk=2", 2)] {
        assert_eq!(parse_variant(line).expect("разбор").1.mk, mk);
    }
    assert!(parse_variant("x mk=1").is_err(), "mk требует ew");
    assert!(parse_variant("x ew=5 mk=3").is_err());
    // при mk = 0 строка `params` в meta.json — как у производного Debug до §13.28
    let d = format!("{p0:?}");
    assert!(!d.contains("mk: "));
    assert_eq!(
        d,
        format!(
            "Params {{ sig: {:?}, exit: {:?}, port: {:?}, filt: {:?} }}",
            p0.sig, p0.exit, p0.port, p0.filt
        )
    );
    let (_, p1) = parse_variant("x ew=5 mk=1").expect("разбор");
    assert!(format!("{p1:?}").ends_with(", mk: 1 }"));
    assert!(super::cli::reject_tick_only(&p1, "x").is_err());
}

impl E2World {
    /// `run_tick_mk` для сигнала как в `run`, но с 100 заполняющими сделками на сетке 0,01 за 5 с до сценария (тик и окно ≥ 100 сделок).
    fn run_mk(
        &self,
        ex: &super::params::ExitCfg,
        ticks: &[(i64, f64, bool)],
        mk: u8,
    ) -> super::tick::TickResult {
        let mut v: Vec<Tick> = (0..100u32)
            .map(|i| Tick {
                px: px(if i % 2 == 0 { 99.40 } else { 99.41 }),
                off: (self.base - 5000) as u32 + i,
                bm: i % 3 == 0,
            })
            .collect();
        v.extend(ticks.iter().map(|&(off, price, bm)| Tick {
            px: px(price),
            off: (self.base + off) as u32,
            bm,
        }));
        let mut m = HashMap::new();
        m.insert(("TEST".to_string(), self.t1 / 1440), Arc::new(v));
        let src = MemSrc(m);
        let sg = TSig {
            ms: (self.t1 as i64 / 1440) * DAY_MS + self.base + 10_000,
            sym: 0,
            level: 0,
            dist: 0,
            t: self.t1 as u32,
            day: (self.t1 / 1440) as u16,
            idx: 101,
            lp: px(100.0),
            far: px(99.9),
            ws: false,
        };
        run_tick_mk(&self.syms, &[sg], Side::Long, ex, &self.port, &src, mk)
    }
}

#[test]
fn mk_entry_is_maker_without_slippage_and_exit_clocks_run_from_the_fill() {
    let w = e2_world();
    let mut ex = w.ex.clone();
    ex.e2_pm = 0;
    ex.slip_bps = 1;
    ex.tx_min = 1; // выход по времени: 1 мин от первой после заполнения сделки ≥ L
    ex.tx_x_pm = 500; // «пробой» — 100,5
    let scen = [
        (0, 99.60, false),
        (10_000, 99.70, false),  // сигнал
        (10_100, 100.00, false), // пробой до заполнения (mk2)
        (10_200, 99.99, true),   // ретест → заполнение по 100,00
        (10_300, 100.00, false), // первое «пересекли» после заполнения
        (70_100, 99.95, true),   // 1 мин от пробоя до заполнения — выхода быть не должно
        (70_300, 99.95, true),   // 1 мин от (10 300) → выход по времени
    ];
    let r = w.run_mk(&ex, &scen, 2);
    assert_eq!((r.trades.len(), r.mk), (1, [1, 0, 0]));
    let t = &r.trades[0];
    assert_eq!(
        (t.entry_ms, t.entry_px),
        (w.abs(10_200), px(100.00)),
        "цена лимита, проскальзывание входа 0"
    );
    assert_eq!((t.kind, t.exit_ms), (ExitKind::TimeExit, w.abs(70_300)));
    let fee = |taker| crate::lob::costs::leg_fee_bps(taker) / 1e4;
    let want = 500.0 * fee(false) + 500.0 * (99.95 * (1.0 - 1e-4) / 100.0) * fee(true);
    assert!(
        (t.fees - want).abs() < 1e-6,
        "комиссии {} против {}: вход мейкер, выход тейкер со своим проскальзыванием",
        t.fees,
        want
    );
    // тейкер на том же потоке: вход со своим проскальзыванием 1 бп
    let r0 = w.run_mk(&ex, &scen, 0);
    let t0 = &r0.trades[0];
    assert_eq!(t0.entry_px, px(99.70) + px(99.70) / 10_000, "99,70 + 1 бп");
    assert_eq!(r0.mk, [0, 0, 0]);
}

#[test]
fn mk1_unfilled_and_touched_orders_are_rejects_that_do_not_consume_the_level() {
    let w = e2_world();
    let mut ex = w.ex.clone();
    ex.e2_pm = 0;
    // не заполнилась: сделок ниже лимита 99,69 нет
    let r = w.run_mk(
        &ex,
        &[
            (0, 99.60, false),
            (10_000, 99.70, false),
            (20_000, 99.80, false),
        ],
        1,
    );
    assert!(r.trades.is_empty());
    assert_eq!((r.mk, r.rej[0], r.rej[5]), ([0, 1, 0], 0, 1));
    assert_eq!(r.entry_rej.len(), 1);
    assert_eq!(r.entry_rej[0].3, EntryRej::Unfilled);
    // сорван уровень
    let r = w.run_mk(
        &ex,
        &[
            (0, 99.60, false),
            (10_000, 99.70, false),
            (20_000, 100.00, false),
        ],
        1,
    );
    assert_eq!(r.mk, [0, 0, 1]);
    assert_eq!(r.entry_rej[0].3, EntryRej::Touched);
}

#[test]
fn mk_second_lot_is_immediate_late_or_cancelled_and_follows_mk2_with_lot1() {
    let w = e2_world();
    // сразу: заменитель 99,95 → лимит 99,94 ≥ L − d = 99,90; лот 2 — тем же событием и по той же цене
    let r = w.run_mk(
        &w.ex,
        &[
            (0, 99.60, false),
            (10_000, 99.95, false),
            (10_100, 99.93, true),
            (40_000, 85.0, true),
        ],
        1,
    );
    assert_eq!(r.lot2, [1, 0, 0, 0]);
    let t = &r.trades[0];
    let l2 = t.lot2.as_ref().expect("второй лот");
    assert_eq!((t.entry_ms, t.entry_px), (w.abs(10_100), px(99.94)));
    assert_eq!((l2.entry_ms, l2.entry_px), (t.entry_ms, t.entry_px));
    // позже: лимит лота 1 99,69 (заполнен в 10 100 по 99,69), подход к 99,92 в 20 000 → заявка 99,91, заполнена в 20 100
    let r = w.run_mk(
        &w.ex,
        &[
            (0, 99.60, false),
            (10_000, 99.70, false),
            (10_100, 99.68, true),
            (20_000, 99.92, false),
            (20_100, 99.90, true),
            (40_000, 85.0, true),
        ],
        1,
    );
    assert_eq!(r.lot2, [0, 1, 0, 0]);
    let l2 = r.trades[0].lot2.as_ref().expect("второй лот");
    assert_eq!((l2.entry_ms, l2.entry_px), (w.abs(20_100), px(99.91)));
    // отмена: уровень сорван до заполнения
    let touched = [
        (0, 99.60, false),
        (10_000, 99.70, false),
        (10_100, 99.68, true),
        (20_000, 99.92, false),
        (20_050, 100.00, false),
        (40_000, 85.0, true),
    ];
    assert_eq!(w.run_mk(&w.ex, &touched, 1).lot2, [0, 0, 1, 0]);
    // отмена: заполняющая сделка пришла на выходе лота 1 — заявка живёт не дольше его
    let late = [
        (0, 99.60, false),
        (10_000, 99.70, false),
        (10_100, 99.68, true),
        (20_000, 99.92, false),
        (40_000, 85.0, true),
    ];
    let r = w.run_mk(&w.ex, &late, 1);
    assert_eq!(r.lot2, [0, 0, 1, 0]);
    assert!(r.trades[0].lot2.is_none());
    // mk2: лот 2 заполняется тем же событием, что лот 1
    let r = w.run_mk(
        &w.ex,
        &[
            (0, 99.60, false),
            (10_000, 99.70, false),
            (10_100, 100.00, false),
            (10_200, 99.99, true),
            (40_000, 85.0, true),
        ],
        2,
    );
    assert_eq!(r.lot2, [1, 0, 0, 0]);
    let t = &r.trades[0];
    let l2 = t.lot2.as_ref().expect("второй лот");
    assert_eq!((l2.entry_ms, l2.entry_px), (t.entry_ms, px(100.00)));
}

// ───────────────────────────── v22 (`tl`): трейл лимиткой (TK-603) ─────────────────────────────

/// Трейл взводится при +0,5 (1 NATR) и держит откат 0,5: вход 100,0 → максимум 100,6 → уровень трейла 100,1; тейки далеко (102,0 / 102,5), безубыток выкл.
fn ex_tl(tl: u8) -> super::params::ExitCfg {
    let mut e = ex_test();
    e.a_m = 1000;
    e.r_m = 1000;
    e.be = false;
    e.t_low_m = 4000;
    e.t_high_m = 5000;
    e.tl = tl;
    e
}

/// Вход 100,0 → максимум 100,6 (трейл 100,1) → срабатывание на сделке в 40 мс по цене `trig`.
fn tl_head(trig: f64) -> Vec<(i64, f64, bool)> {
    vec![(10, 100.0, false), (20, 100.6, false), (40, trig, true)]
}

fn with_tail(mut v: Vec<(i64, f64, bool)>, tail: &[(i64, f64, bool)]) -> Vec<(i64, f64, bool)> {
    v.extend_from_slice(tail);
    v
}

#[test]
fn tl_key_parses_validates_default_off_and_minute_engine_rejects() {
    let (_, p) = super::params::parse_variant("x tl=1").expect("разбор");
    assert_eq!(p.exit.tl, 1);
    let (_, p2) = super::params::parse_variant("x tl=2").expect("разбор");
    assert_eq!(p2.exit.tl, 2);
    let (_, base) = super::params::parse_variant("x lat=4").expect("разбор");
    let (_, zero) = super::params::parse_variant("x lat=4 tl=0").expect("разбор");
    assert_eq!(base.exit.tl, 0);
    assert_eq!(zero.exit, base.exit);
    assert!(super::params::parse_variant("x tl=3").is_err());
    assert!(super::cli::reject_tick_only(&p, "x tl=1").is_err());
    assert!(super::cli::reject_tick_only(&base, "x").is_ok());
}

#[test]
fn tl_off_trail_is_taker_at_the_trade_price_that_broke_it() {
    let mut ex = ex_tl(0);
    ex.slip_bps = 1;
    let out = sim(tl_head(99.0), Side::Long, &ex);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::Trail, 40));
    let last = out.legs.last().expect("нога");
    assert!(!last.maker);
    assert_eq!(
        last.px,
        px(99.0) - px(99.0) / 10_000,
        "цена сделки − проскальзывание"
    );
    assert_eq!(out.tl, None);
}

#[test]
fn tl_limit_fills_at_its_price_when_a_later_trade_reaches_it_for_both_modes() {
    // срабатывание 100,05 ≤ 100,1; сделка по самой цене приказа 100,1; затем 100,2
    let ticks = with_tail(tl_head(100.05), &[(50, 100.1, false), (60, 100.2, false)]);
    for tl in [1, 2] {
        let out = sim(ticks.clone(), Side::Long, &ex_tl(tl));
        assert_eq!(
            (out.kind, out.exit_ms),
            (ExitKind::Trail, 50),
            "tl={tl}: дошла"
        );
        let last = out.legs.last().expect("нога");
        assert_eq!((last.px, last.maker, last.ms), (px(100.1), true, 50));
        assert_eq!(last.u, NOTIONAL_U, "приказ на весь остаток");
        assert_eq!(out.tl, Some((40, px(100.1))));
    }
    // прежнее поведение на тех же сделках: тейкер на сделке срабатывания
    let old = sim(tl_head(100.05), Side::Long, &ex_tl(0));
    assert_eq!((old.kind, old.exit_ms), (ExitKind::Trail, 40));
}

#[test]
fn tl_trigger_trade_never_fills_its_own_order() {
    // сделка срабатывания ровно по цене приказа (100,1 ≤ 100,1); дальше сделок нет — приказ не исполнен, конец потока
    let out = sim(tl_head(100.1), Side::Long, &ex_tl(1));
    assert_eq!(out.kind, ExitKind::Forced);
    assert!(!out.legs.last().expect("нога").maker);
    assert_eq!(out.tl, Some((40, px(100.1))));
}

#[test]
fn tl_gap_through_rests_and_fills_at_limit_price_when_price_comes_back() {
    // гэп: сделка срабатывания 99,0 (ниже приказа 100,1, выше стопа 95,0); потом цена вернулась выше приказа до 100,4
    let ticks = with_tail(
        tl_head(99.0),
        &[(50, 99.5, false), (60, 100.4, false), (70, 90.0, true)],
    );
    let out = sim(ticks, Side::Long, &ex_tl(1));
    assert_eq!((out.kind, out.exit_ms), (ExitKind::Trail, 60));
    let last = out.legs.last().expect("нога");
    assert_eq!(
        (last.px, last.maker),
        (px(100.1), true),
        "по цене приказа, не по сделке 100,4"
    );
    assert_eq!(
        out.tl,
        Some((40, px(100.1))),
        "трейл после срабатывания не двигается"
    );
}

#[test]
fn tl_never_filled_order_rests_until_market_stop_which_is_taker_with_slip() {
    let ticks = with_tail(
        tl_head(99.0),
        &[(50, 98.0, true), (60, 94.0, true), (70, 100.5, false)],
    );
    let out = sim(ticks.clone(), Side::Long, &ex_tl(1));
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 60));
    let last = out.legs.last().expect("нога");
    assert_eq!((last.px, last.maker), (px(94.0), false));
    assert_eq!(
        out.tl,
        Some((40, px(100.1))),
        "приказ был выставлен, но не исполнен"
    );
    let mut ex = ex_tl(1);
    ex.slip_bps = 1;
    let out = sim(ticks, Side::Long, &ex);
    assert_eq!(out.kind, ExitKind::HardStop);
    assert_eq!(
        out.legs.last().map(|l| l.px),
        Some(px(94.0) - px(94.0) / 10_000),
        "стоп — тейкер, проскальзывание хуже на 1 бп"
    );
}

#[test]
fn tl_stream_end_with_resting_order_is_forced_taker() {
    let out = sim(
        with_tail(tl_head(99.0), &[(50, 98.0, true)]),
        Side::Long,
        &ex_tl(1),
    );
    assert_eq!((out.kind, out.exit_ms), (ExitKind::Forced, 50));
    let last = out.legs.last().expect("нога");
    assert_eq!((last.px, last.maker), (px(98.0), false));
}

#[test]
fn tl_breakeven_stays_market_while_limit_rests_and_takes_stay_makers() {
    let mut ex = ex_tl(1);
    ex.be = true;
    ex.t_low_m = 1000;
    ex.t_high_m = 2000; // тейки 100,5 / 101,0
    let ticks = vec![
        (10, 100.0, false),
        (20, 100.6, false), // тейк 1 → стоп на цену входа 100,0; максимум 100,6 → трейл 100,1
        (30, 100.05, true), // ≤ 100,1: трейл главнее безубытка → приказ на остаток по 100,1
        (40, 99.9, true),   // ≤ 100,0 → безубыток, рынок
        (50, 100.5, false),
    ];
    let out = sim(ticks, Side::Long, &ex);
    assert_eq!(
        (out.kind, out.exit_ms, out.takes),
        (ExitKind::BreakEven, 40, 1)
    );
    assert!(out.legs[0].maker && out.legs[0].px == px(100.5));
    let last = out.legs.last().expect("нога");
    assert_eq!(
        (last.px, last.maker),
        (px(99.9), false),
        "безубыток — тейкер по цене сделки"
    );
    assert_eq!(out.tl, Some((30, px(100.1))));
}

#[test]
fn tl_limit_sells_only_the_remainder_after_takes() {
    let mut ex = ex_tl(1);
    ex.be = true;
    ex.t_low_m = 1000;
    ex.t_high_m = 2000;
    let ticks = vec![
        (10, 100.0, false),
        (20, 100.6, false),
        (30, 100.05, true),
        (40, 100.2, false), // ≥ 100,1 → приказ исполнен
    ];
    let out = sim(ticks, Side::Long, &ex);
    assert_eq!((out.kind, out.exit_ms, out.takes), (ExitKind::Trail, 40, 1));
    let last = out.legs.last().expect("нога");
    assert_eq!(
        (last.px, last.maker, last.u),
        (px(100.1), true, NOTIONAL_U - NOTIONAL_U / 4)
    );
}

#[test]
fn tl_short_mirrors_long() {
    // шорт: вход 100,0 продавцом-тейкером; минимум 99,4 → трейл 99,9; срабатывание на росте до 99,95; приказ — откуп по 99,9
    let head = |trig: f64| vec![(10, 100.0, true), (20, 99.4, true), (40, trig, false)];
    let touch = with_tail(head(99.95), &[(50, 99.9, true), (60, 99.8, true)]);
    let out = sim(touch.clone(), Side::Short, &ex_tl(1));
    assert_eq!(
        (out.kind, out.exit_ms),
        (ExitKind::Trail, 50),
        "tl=1: дошла"
    );
    let last = out.legs.last().expect("нога");
    assert_eq!((last.px, last.maker), (px(99.9), true));
    assert_eq!(out.tl, Some((40, px(99.9))));
    let out = sim(touch, Side::Short, &ex_tl(2));
    assert_eq!(
        (out.kind, out.exit_ms),
        (ExitKind::Trail, 50),
        "tl=2: дошла"
    );
    // гэп вверх (стоп 105,0 не задет), затем падение цены ниже приказа: исполнение по 99,9, не по сделке 99,5
    let out = sim(
        with_tail(head(101.0), &[(50, 100.5, true), (60, 99.5, true)]),
        Side::Short,
        &ex_tl(1),
    );
    assert_eq!((out.kind, out.exit_ms), (ExitKind::Trail, 60));
    let last = out.legs.last().expect("нога");
    assert_eq!((last.px, last.maker), (px(99.9), true));
    // рыночный стоп шорта (цена ≥ 105,0) — тейкер по цене сделки
    let out = sim(
        with_tail(head(101.0), &[(50, 106.0, false)]),
        Side::Short,
        &ex_tl(1),
    );
    assert_eq!(out.kind, ExitKind::HardStop);
    let last = out.legs.last().expect("нога");
    assert_eq!((last.px, last.maker), (px(106.0), false));
}

#[test]
fn tl_order_goes_live_after_exit_latency_and_a_pending_stop_does_not_cancel_it_before_it_executes()
{
    let mut ex = ex_tl(1);
    ex.lat_exit_ms = 50; // приказ живёт с 40 + 50 = 90
                         // цена ≥ приказа до 90 мс — приказ ещё в пути, не исполняется; исполняется первой сделкой ≥ 90 мс
    let out = sim(
        with_tail(tl_head(100.05), &[(60, 100.2, false), (100, 100.2, false)]),
        Side::Long,
        &ex,
    );
    assert_eq!((out.kind, out.exit_ms), (ExitKind::Trail, 100));
    assert_eq!(
        out.legs.last().map(|l| (l.px, l.maker)),
        Some((px(100.1), true))
    );
    // стоп сработал на 100 мс (рыночный выход — с 150), до 150 цена вернулась на приказ → исполнен приказ
    let back = with_tail(
        tl_head(99.0),
        &[(100, 94.0, true), (120, 100.2, false), (160, 93.0, true)],
    );
    let out = sim(back, Side::Long, &ex);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::Trail, 120));
    assert_eq!(
        out.legs.last().map(|l| (l.px, l.maker)),
        Some((px(100.1), true))
    );
    // цена не вернулась: рыночный выход на первой сделке ≥ 150 мс — тейкер по её цене
    let down = with_tail(
        tl_head(99.0),
        &[(100, 94.0, true), (120, 94.5, true), (160, 93.0, true)],
    );
    let out = sim(down, Side::Long, &ex);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 160));
    assert_eq!(
        out.legs.last().map(|l| (l.px, l.maker)),
        Some((px(93.0), false))
    );
}

#[test]
fn tl2_order_that_arrives_when_price_is_already_beyond_it_is_a_v21_market_exit_while_tl1_fills_at_its_price(
) {
    let mut ex = ex_tl(1);
    ex.lat_exit_ms = 50; // приказ доходит на 90 мс; цена до этого вернулась выше приказа (100,2 > 100,1), а на 100 мс снова ниже
    let ticks = with_tail(
        tl_head(100.05),
        &[(60, 100.2, false), (100, 100.0, true), (110, 100.3, false)],
    );
    // tl=1 (буква приказа): на приход приказа сделка 100,0 < 100,1 — приказ стоит; исполнен сделкой 100,3 по цене приказа
    let out = sim(ticks.clone(), Side::Long, &ex);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::Trail, 110));
    assert_eq!(
        out.legs.last().map(|l| (l.px, l.maker)),
        Some((px(100.1), true))
    );
    // tl=2: к приходу цена уже выше приказа — приказ исполняется сразу как рыночный: первая сделка ≥ 90 мс, тейкер (как выход v21)
    ex.tl = 2;
    ex.slip_bps = 1;
    let out = sim(ticks.clone(), Side::Long, &ex);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::Trail, 100));
    let last = out.legs.last().expect("нога");
    assert!(!last.maker);
    assert_eq!(last.px, px(100.0) - px(100.0) / 10_000);
    assert_eq!(out.tl, Some((40, out.tl.expect("приказ").1)));
    // tl=2: к приходу цена ещё ниже приказа — приказ стоит и исполняется по своей цене (как tl=1)
    ex.slip_bps = 0;
    let rest = with_tail(
        tl_head(100.05),
        &[(60, 99.9, true), (100, 100.2, false), (110, 100.3, false)],
    );
    let out = sim(rest, Side::Long, &ex);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::Trail, 100));
    assert_eq!(
        out.legs.last().map(|l| (l.px, l.maker)),
        Some((px(100.1), true))
    );
    // без задержки приказ приходит сразу после срабатывания: цена срабатывания ≤ приказа — приказ стоит (tl=2 как tl=1)
    ex.lat_exit_ms = 0;
    let out = sim(
        with_tail(tl_head(99.0), &[(50, 99.5, false), (60, 100.4, false)]),
        Side::Long,
        &ex,
    );
    assert_eq!((out.kind, out.exit_ms), (ExitKind::Trail, 60));
    assert_eq!(
        out.legs.last().map(|l| (l.px, l.maker)),
        Some((px(100.1), true))
    );
}

#[test]
fn tl_trail_leg_pays_maker_fee_and_no_slippage_in_settle() {
    let base = START_DAY as i64 * DAY_MS;
    let s = Sym::from_cols("T", flat(START_DAY * 1440 + 4000, 100.0), vec![]);
    let ticks: Vec<(i64, f64, bool)> = tl_head(100.05)
        .into_iter()
        .chain([(50, 100.2, false)])
        .map(|(ms, p, bm)| (base + ms, p, bm))
        .collect();
    let out = sim_stream(&mut stream(ticks), base, Side::Long, NATR, &ex_tl(1)).expect("исход");
    assert_eq!(out.kind, ExitKind::Trail);
    let st = settle_tick(&s, Side::Long, &out);
    let maker = |n: f64| n * 1.4 * 0.9 / 1e4;
    assert!(
        (st.gross - 0.5).abs() < 1e-9,
        "500 $ · (100,1 − 100,0) / 100,0: {}",
        st.gross
    );
    let want = fee_taker(500.0) + maker(500.0 * 1.001);
    assert!(
        (st.fees - want).abs() < 1e-9,
        "вход тейкер, выход мейкер: {} против {want}",
        st.fees
    );
}

// ───────────────────────── §13.31: выход «сценарий отменён» (`nx`), стоп от уровня (`sl`), стоп за проторговкой (`cw`) ─────────────────────────

use super::tick::cons_range;

/// `run_lc` с диапазоном проторговки `rng` = (low, high) в настоящих ценах (рама — по стороне).
fn run_rng(
    ticks: Vec<(i64, f64, bool)>,
    side: Side,
    ex: &super::params::ExitCfg,
    lp: f64,
    rng: Option<(f64, f64)>,
) -> TickOut {
    let s = side.s();
    // рама: нижний край диапазона = s·(low для лонга | high для шорта)
    let rng = rng.map(|(lo, hi)| {
        if s > 0 {
            (px(lo), px(hi))
        } else {
            (-px(hi), -px(lo))
        }
    });
    let lc = LevelCtx {
        lp: s * px(lp),
        far: s * px(lp),
        rng,
    };
    sim_fill_lc(
        &mut stream(ticks),
        1000,
        s * px(100.0),
        side,
        NATR,
        ex,
        Some(lc),
        NOTIONAL_U,
    )
}

#[test]
fn stop_redesign_keys_parse_validate_default_off_and_keep_the_params_debug_string() {
    use super::params::parse_variant;
    let (_, p0) = parse_variant("x ew=5").expect("разбор");
    let e = &p0.exit;
    assert_eq!(
        (e.nx_m, e.sl_m, e.cs_w, e.cs_lo_m, e.cs_hi_m, e.cs_m_m),
        (0, 0, 0, 0, 0, 0)
    );
    assert!(!e.stop_redesign_opts() && !e.lvl_exit_opts());
    // выключено — `Debug` ExitCfg как у производного до §13.31 (meta.json байт в байт)
    assert_eq!(
        format!("{e:?}"),
        "ExitCfg { natr_p: 30, n_tp: 8, t_low_m: 1000, t_high_m: 5000, q_pct: 50, be: true, a_m: 2000, r_m: 2000, s_pm: 5000, slip_bps: 1, \
         best_order: false, lat_ms: 0, lat_exit_ms: 0, tp_gamma_m: 1000, tp_kappa_m: 0, natr_src: 0, ent_w_ms: 5, ent_x_bp: 0, ls_m: 0, \
         tx_min: 0, tx_x_pm: 0, rt_f_pm: 0, rt_s: 0, be_mode: 0, e2_pm: 0, sn_m: 0, sg: 0, sb_m: 0, sr_m: 0, sd_m: 0, tj_m: 0, tl: 0 }"
    );
    let (_, p) = parse_variant("x nx=1.5 sl=2 cw=60 cl=2 ch=6 cm=0.5").expect("разбор");
    let e = &p.exit;
    assert_eq!(
        (e.nx_m, e.sl_m, e.cs_w, e.cs_lo_m, e.cs_hi_m, e.cs_m_m),
        (1500, 2000, 60, 2000, 6000, 500)
    );
    assert!(e.stop_redesign_opts() && e.level_opts() && e.lvl_exit_opts());
    let d = format!("{e:?}");
    assert!(d.ends_with(
        "tl: 0, nx_m: 1500, sl_m: 2000, cs_w: 60, cs_lo_m: 2000, cs_hi_m: 6000, cs_m_m: 500 }"
    ));
    // валидация: ключи проторговки только вместе с cw, cl < ch, неотрицательность
    assert!(parse_variant("x cl=2 ch=6").is_err());
    assert!(parse_variant("x cm=1").is_err());
    assert!(parse_variant("x cw=60 cl=6 ch=2").is_err());
    assert!(parse_variant("x cw=60").is_err(), "ch > cl обязателен");
    assert!(parse_variant("x nx=-1").is_err());
    assert!(parse_variant("x sl=-1").is_err());
    // минутный движок отвергает
    for line in ["x nx=1", "x sl=1", "x cw=60 cl=1 ch=3"] {
        let (_, p) = parse_variant(line).expect("разбор");
        assert!(super::cli::reject_tick_only(&p, line).is_err(), "{line}");
    }
}

#[test]
fn nx_exits_before_touch_when_price_is_x_natr_below_the_level_and_not_after() {
    let mut ex = ex_test();
    ex.nx_m = 1000; // 1 NATR = 0,5 при входе 100; уровень 100,3 → порог 99,8
    let t = vec![
        (1100, 99.85, true),
        (1200, 99.81, true),
        (1300, 99.8, true),
        (1400, 90.0, true),
    ];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::NoTouch, 1300));
    assert_eq!(out.kind.name(), "nt");
    assert_eq!(out.legs.last().map(|l| l.px), Some(px(99.8)));
    // после касания (сделка ≥ уровня) правило не действует: падение до 99,0 — не NoTouch (жёсткий стоп 5 % = 95)
    let t = vec![
        (1100, 100.0, false),
        (1200, 100.3, false),
        (1300, 99.0, true),
        (1400, 98.0, true),
    ];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!(out.kind, ExitKind::Forced);
    // выключено — выхода нет
    ex.nx_m = 0;
    let t = vec![(1100, 99.0, true), (1200, 98.0, true)];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!(out.kind, ExitKind::Forced);
    // порог не выше входа: X = 0,25 NATR → 100,3 − 0,125 = 100,175 > e − 1 → порог = цена входа − 1 (e8)
    ex.nx_m = 250;
    let t = vec![(1100, 100.0, false), (1200, 99.99, true)];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::NoTouch, 1200));
    // шорт зеркально: уровень 99,7 (пол), вход 100,0; порог = 99,7 + 0,5 = 100,2
    ex.nx_m = 1000;
    let t = vec![
        (1100, 100.1, false),
        (1200, 100.2, false),
        (1300, 101.0, false),
    ];
    let out = run_lc(t, Side::Short, &ex, 99.7, 99.8);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::NoTouch, 1200));
}

#[test]
fn tu_key_parses_validates_defaults_off_and_keeps_the_params_debug_string() {
    use super::params::parse_variant;
    let (_, p0) = parse_variant("x ew=5").expect("разбор");
    assert_eq!(p0.exit.tu_min, 0);
    assert!(!p0.exit.level_opts() && !p0.exit.lvl_exit_opts());
    // выключено — `Debug` ExitCfg без поля `tu_min` (meta.json базы байт в байт)
    assert!(!format!("{:?}", p0.exit).contains("tu_min"));
    let (_, p) = parse_variant("x tu=30").expect("разбор");
    assert_eq!(p.exit.tu_min, 30);
    assert!(p.exit.level_opts() && p.exit.lvl_exit_opts());
    assert!(format!("{:?}", p.exit).ends_with("tl: 0, tu_min: 30 }"));
    let (_, p) = parse_variant("x nx=1 tu=15").expect("разбор");
    assert!(format!("{:?}", p.exit).ends_with("cs_m_m: 0, tu_min: 15 }"));
    assert!(parse_variant("x tu=-1").is_err());
    let (_, p) = parse_variant("x tu=60").expect("разбор");
    assert!(
        super::cli::reject_tick_only(&p, "x tu=60").is_err(),
        "минутный движок отвергает"
    );
}

#[test]
fn tu_exits_at_first_trade_t_minutes_after_entry_while_level_untouched_and_not_after_touch() {
    let mut ex = ex_test();
    ex.tu_min = 1; // вход 1000 мс → срок 61 000 мс; уровень 100,3
    let t = vec![
        (30_000, 100.1, true),
        (60_999, 100.2, true),
        (61_000, 100.05, true),
        (62_000, 90.0, true),
    ];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::TimeNoTouch, 61_000));
    assert_eq!(out.kind.name(), "tu");
    // первая сделка после срока (между сделками срок прошёл) — выход на ней
    let t = vec![(30_000, 100.1, true), (90_000, 100.0, true)];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::TimeNoTouch, 90_000));
    // касание до срока (сделка ≥ уровня) — правило не действует
    let t = vec![
        (30_000, 100.3, false),
        (61_000, 100.0, true),
        (120_000, 99.9, true),
    ];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!(out.kind, ExitKind::Forced);
    // касание сделкой ровно в срок — касание засчитано раньше проверки срока
    let t = vec![(61_000, 100.3, false), (62_000, 100.0, true)];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!(out.kind, ExitKind::Forced);
    // жёсткий стоп до срока срабатывает как раньше
    let t = vec![(30_000, 94.0, true), (61_000, 100.0, true)];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!(out.kind, ExitKind::HardStop);
    // выключено — выхода нет
    ex.tu_min = 0;
    let t = vec![(30_000, 100.1, true), (90_000, 100.0, true)];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!(out.kind, ExitKind::Forced);
    // шорт зеркально: уровень 99,7 (пол), вход 100,0
    ex.tu_min = 1;
    let t = vec![
        (30_000, 99.8, false),
        (61_000, 99.9, false),
        (62_000, 99.0, false),
    ];
    let out = run_lc(t, Side::Short, &ex, 99.7, 99.8);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::TimeNoTouch, 61_000));
    let t = vec![(30_000, 99.7, true), (61_000, 99.9, false)];
    let out = run_lc(t, Side::Short, &ex, 99.7, 99.8);
    assert_eq!(out.kind, ExitKind::Forced);
    // вместе с `nx`: что раньше — то и выход
    ex.nx_m = 1000; // порог 99,8
    let t = vec![(30_000, 99.8, true), (61_000, 100.0, true)];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::NoTouch, 30_000));
}

#[test]
fn sl_is_level_minus_k_natr_capped_by_hard_stop_and_works_after_touch() {
    let mut ex = ex_test();
    ex.sl_m = 1500; // 1,5 NATR = 0,75: уровень 100,3 → стоп 99,55
    let t = vec![
        (1100, 99.6, true),
        (1200, 99.56, true),
        (1300, 99.55, true),
        (1400, 90.0, true),
    ];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 1300));
    assert_eq!(out.legs.last().map(|l| l.px), Some(px(99.55)));
    // стоп действует и после касания уровня
    let t = vec![
        (1100, 100.0, false),
        (1200, 100.3, false),
        (1300, 99.6, true),
        (1400, 99.5, true),
    ];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 1400));
    // жёсткий стоп 5 % остаётся потолком: k = 40 NATR → 20 → стоп 80,3, но не ниже 95
    ex.sl_m = 40_000;
    let t = vec![(1100, 95.01, true), (1200, 95.0, true)];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 1200));
    // стоп не выше входа − 1: k = 0,25 → 100,3 − 0,125 = 100,175 → стоп = e − 1
    ex.sl_m = 250;
    let t = vec![(1100, 100.0, false), (1200, 99.99, true)];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 1200));
    // шорт зеркально: уровень 99,7, k = 1,5 → стоп 99,7 + 0,75 = 100,45
    ex.sl_m = 1500;
    let t = vec![(1100, 100.4, false), (1200, 100.45, false)];
    let out = run_lc(t, Side::Short, &ex, 99.7, 99.8);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 1200));
    assert_eq!(out.legs.last().map(|l| l.px), Some(px(100.45)));
    // выключен — жёсткий стоп 5 % (в тесте выход только по данным потока: Forced)
    ex.sl_m = 0;
    let t = vec![(1100, 99.0, true), (1200, 98.0, true)];
    let out = run_lc(t, Side::Long, &ex, 100.3, 99.9);
    assert_eq!(out.kind, ExitKind::Forced);
}

#[test]
fn cs_stop_is_beyond_range_low_when_width_in_band_else_falls_back_to_sl() {
    let mut ex = ex_test();
    ex.cs_w = 60;
    ex.cs_lo_m = 1000; // ширина ≥ 1 NATR = 0,5
    ex.cs_hi_m = 3000; // ≤ 3 NATR = 1,5
    ex.cs_m_m = 500; // отступ 0,5 NATR = 0,25
    ex.sl_m = 2000; // запасной стоп 100,3 − 1,0 = 99,3
                    // диапазон 99,4–100,0 (ширина 0,6 ∈ [0,5; 1,5]) → стоп 99,4 − 0,25 = 99,15
    let t = vec![(1100, 99.2, true), (1200, 99.16, true), (1300, 99.15, true)];
    let out = run_rng(t, Side::Long, &ex, 100.3, Some((99.4, 100.0)));
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 1300));
    assert_eq!(out.legs.last().map(|l| l.px), Some(px(99.15)));
    // узкий диапазон (ширина 0,2 < 0,5) → запасной стоп 99,3
    let t = vec![(1100, 99.31, true), (1200, 99.3, true)];
    let out = run_rng(t, Side::Long, &ex, 100.3, Some((99.8, 100.0)));
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 1200));
    // широкий диапазон (ширина 2,0 > 1,5) → запасной стоп
    let t = vec![(1100, 99.31, true), (1200, 99.3, true)];
    let out = run_rng(t, Side::Long, &ex, 100.3, Some((98.0, 100.0)));
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 1200));
    // диапазона нет (мало данных) → запасной стоп
    let t = vec![(1100, 99.31, true), (1200, 99.3, true)];
    let out = run_rng(t, Side::Long, &ex, 100.3, None);
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 1200));
    // границы допуска включены: ширина ровно 0,5 и ровно 1,5
    for (lo, hi) in [(99.5, 100.0), (98.5, 100.0)] {
        let stop = lo - 0.25;
        let t = vec![(1100, stop + 0.01, true), (1200, stop, true)];
        let out = run_rng(t, Side::Long, &ex, 100.3, Some((lo, hi)));
        assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 1200), "{lo}");
    }
    // стоп за краем не выше входа − 1: край выше входа (цена вышла из диапазона вниз) → стоп = e − 1
    let t = vec![(1100, 100.0, false), (1200, 99.99, true)];
    let out = run_rng(t, Side::Long, &ex, 100.3, Some((100.3, 100.8)));
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 1200));
    // шорт зеркально: диапазон 100,0–100,6 (рама: нижний край = −100,6), стоп = 100,6 + 0,25 = 100,85
    let t = vec![(1100, 100.8, false), (1200, 100.85, false)];
    let out = run_rng(t, Side::Short, &ex, 99.7, Some((100.0, 100.6)));
    assert_eq!((out.kind, out.exit_ms), (ExitKind::HardStop, 1200));
    assert_eq!(out.legs.last().map(|l| l.px), Some(px(100.85)));
}

#[test]
fn cons_range_is_causal_skips_holes_and_needs_half_the_window() {
    let mut c = flat(5000, 100.0);
    for m in 4000..4060 {
        set(&mut c, m, 100.0, 100.2, 99.8, 100.0);
    }
    set(&mut c, 4010, 100.0, 100.5, 99.5, 100.0);
    set(&mut c, 4060, 100.0, 120.0, 80.0, 100.0); // сигнальная минута — не читается
    let s = sym(c);
    let (lo, hi) = cons_range(&s, 1, 4060, 60).expect("диапазон");
    assert_eq!((lo, hi), (px(99.5), px(100.5)));
    // шорт: рама — отрицательные цены, lo = −max(high), hi = −min(low)
    let (lo, hi) = cons_range(&s, -1, 4060, 60).expect("диапазон");
    assert_eq!((lo, hi), (-px(100.5), -px(99.5)));
    // окно короче: минуты 4040…4059 — без выброса 4010
    let (lo, hi) = cons_range(&s, 1, 4060, 20).expect("диапазон");
    assert_eq!((lo, hi), (px(99.8), px(100.2)));
    // дыры: 29 из 60 минут с данными — диапазона нет; 30 — есть
    let mut c = Cols::new();
    for m in 4000..4029 {
        set(&mut c, m, 100.0, 100.2, 99.8, 100.0);
    }
    assert!(cons_range(&sym(c.clone()), 1, 4060, 60).is_none());
    set(&mut c, 4029, 100.0, 100.2, 99.8, 100.0);
    assert!(cons_range(&sym(c), 1, 4060, 60).is_some());
    // выключено
    assert!(cons_range(&s, 1, 4060, 0).is_none());
}
