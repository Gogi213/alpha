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
        ws: false,
    };
    let syms = [s];
    let r = run_tick(&syms, &[sg], Side::Long, &ex, &p.port, &src);
    assert!(r.trades.is_empty());
    assert_eq!(r.entry_rej, vec![(0, 0, sg.ms, EntryRej::Beyond)]);
    assert_eq!(r.rej[5], 1, "в счётчике «нет исхода»");
    assert_eq!(r.rej[0], 0, "уровень не расходуется");
}
