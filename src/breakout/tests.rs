//! Тесты П-42: пивот без заглядывания, вход до пробоя, порядок внутри минутки (худший), тейкер/мейкер «строго
//! через», фандинг, суточная переоценка. Синтетические минутки на сетке кэша (минута 0 = 2026-09-01 00:00 UTC).

use super::data::{parse_dec, Cols, Fund, Sym, HALF_DAYS, START_DAY, START_MIN};
use super::levels::{build_levels, build_levels_tracked};
use super::params::Params;
use super::portfolio::settle;
use super::signals::{scan, Sig, SigCtr};
use super::trade::{simulate, ExitKind, Leg, TradeOut, NOTIONAL_U};
use super::Side;

pub(super) fn px(x: f64) -> i64 {
    (x * 1e8).round() as i64
}

pub(super) fn flat(upto: usize, price: f64) -> Cols {
    let mut c = Cols::new();
    for m in 0..upto {
        c.o[m] = px(price);
        c.h[m] = px(price);
        c.l[m] = px(price);
        c.c[m] = px(price);
    }
    c
}

pub(super) fn set(c: &mut Cols, m: usize, o: f64, h: f64, l: f64, cl: f64) {
    c.o[m] = px(o);
    c.h[m] = px(h);
    c.l[m] = px(l);
    c.c[m] = px(cl);
}

pub(super) fn sym(c: Cols) -> Sym {
    Sym::from_cols("TEST", c, Vec::new())
}

#[test]
fn decimal_parse_exact() {
    assert_eq!(parse_dec(b"83576.80", 8), Some(8_357_680_000_000));
    assert_eq!(parse_dec(b"0.00001234", 8), Some(1234));
    assert_eq!(parse_dec(b"-0.00008482", 8), Some(-8482));
    assert_eq!(parse_dec(b"13835811.75180", 5), Some(1_383_581_175_180));
    assert_eq!(parse_dec(b"1790812800000", 0), Some(1_790_812_800_000));
}

/// Мир с уровнем: часовые бары `HB + i` с максимумами H, пивоты на барах 1 и 5 (ширина k = 1), уровень
/// 100,025, рождён на закрытии бара 6 → минута `(HB + 7) * 60`.
pub(super) const HB: usize = 400;
pub(super) const BORN: usize = (HB + 7) * 60;
const _: () = assert!(BORN > START_MIN);

pub(super) fn level_world() -> Cols {
    let mut c = flat(HB * 60 + 3000, 90.0);
    let hs = [99.0, 100.0, 99.0, 98.0, 99.0, 100.05, 99.0, 98.5];
    for (i, h) in hs.iter().enumerate() {
        let b = (HB + i) * 60;
        let close = h - 0.4;
        for m in b..b + 60 {
            set(&mut c, m, close, close, close, close);
        }
        set(&mut c, b, close, *h, h - 0.5, close);
    }
    c
}

pub(super) fn lv_params() -> Params {
    let mut p = Params::base();
    p.sig.lv.k = 1;
    p.sig.lv.d_pm = 300;
    p.sig.natr_min_pm = 0;
    p.sig.t1h_u = 0;
    p
}

#[test]
fn pivot_level_no_lookahead() {
    let p = lv_params();
    let s = sym(level_world());
    let lv = build_levels(&s, &p.sig.lv, Side::Long);
    assert_eq!(lv.len(), 1, "один уровень из двух пивотов");
    assert_eq!(
        lv[0].born as usize, BORN,
        "известен только после закрытия бара k=1 справа от 2-го пивота"
    );
    assert_eq!(
        lv[0].segs[0].1,
        px(100.025),
        "цена уровня — среднее пивотов"
    );
    // всё после минуты рождения заменено мусором: рождение и цена не меняются (будущее не читается)
    let mut c = level_world();
    for m in BORN..BORN + 2000 {
        set(&mut c, m, 50.0, 200.0, 10.0, 77.0);
    }
    let lv2 = build_levels(&sym(c), &p.sig.lv, Side::Long);
    assert_eq!(lv2[0].born, lv[0].born);
    assert_eq!(lv2[0].segs[0], lv[0].segs[0]);
    // без бара справа от второго пивота (дыра) уровня нет
    let mut c = level_world();
    for m in (HB + 6) * 60..(HB + 7) * 60 {
        c.o[m] = 0;
        c.h[m] = 0;
        c.l[m] = 0;
        c.c[m] = 0;
    }
    assert!(build_levels(&sym(c), &p.sig.lv, Side::Long).is_empty());
}

#[test]
fn tracked_levels_log_members_without_changing_levels() {
    let p = lv_params();
    let s = sym(level_world());
    let plain = build_levels(&s, &p.sig.lv, Side::Long);
    let tr = build_levels_tracked(&s, &p.sig.lv, Side::Long, true);
    assert_eq!(plain.len(), tr.len());
    for (a, b) in plain.iter().zip(&tr) {
        assert_eq!((a.born, a.end, a.first_bar), (b.born, b.end, b.first_bar));
        assert_eq!(a.segs, b.segs);
        assert!(a.mem.is_empty());
        assert_eq!(b.mem.len(), b.segs.len(), "журнал членов — по версии цены");
        for (seg, m) in b.segs.iter().zip(&b.mem) {
            let mean = m.iter().map(|x| x.1).sum::<i64>() / m.len() as i64;
            assert_eq!(seg.1, mean, "цена уровня — среднее членов");
        }
    }
    let mut m0 = tr[0].mem[0].clone();
    m0.sort_unstable();
    assert_eq!(
        m0,
        vec![(HB as u32 + 1, px(100.0)), (HB as u32 + 5, px(100.05))]
    );
}

#[test]
fn level_killed_by_hourly_close_beyond() {
    let p = lv_params();
    let mut c = level_world();
    // часовой бар закрылся выше уровня: истинный пробой — уровень израсходован с минуты после закрытия бара
    let b = (HB + 9) * 60;
    for m in b..b + 60 {
        set(&mut c, m, 101.0, 101.0, 101.0, 101.0);
    }
    let lv = build_levels(&sym(c), &p.sig.lv, Side::Long);
    assert_eq!(lv[0].end as usize, (HB + 10) * 60);
}

pub(super) fn run_scan(c: Cols, p: &Params) -> (Vec<Sig>, SigCtr) {
    let s = sym(c);
    let lv = build_levels(&s, &p.sig.lv, Side::Long);
    let mut out = Vec::new();
    let mut ctr = SigCtr::default();
    scan(&s, 0, &lv, &p.sig, Side::Long, &mut out, &mut ctr);
    (out, ctr)
}

#[test]
fn entry_before_break_only() {
    let mut p = lv_params();
    p.sig.r_min = 0; // после пересечения в тесте цена уходит на 90 (плоские минутки) — это не ложный пробой
    let mut c = level_world();
    let t1 = BORN + 100;
    set(&mut c, t1, 99.85, 99.95, 99.84, 99.90); // зелёная, внутри 0,3 %, ниже уровня → сигнал
    set(&mut c, t1 + 10, 99.90, 100.10, 99.90, 100.00); // пересекла уровень в этой минутке → сделки нет
    set(&mut c, t1 + 40, 99.95, 99.96, 99.80, 99.85); // красная → нет
    set(&mut c, t1 + 50, 99.40, 99.60, 99.40, 99.55); // зелёная, но дальше 0,3 % (0,47 %) → нет
    set(&mut c, t1 + 60, 99.80, 100.02, 99.80, 100.01); // high ниже уровня 100,025, зелёная, 0,015 % → сигнал
    let (sigs, ctr) = run_scan(c, &p);
    let ts: Vec<u32> = sigs.iter().map(|s| s.t).collect();
    assert_eq!(ts, vec![t1 as u32, (t1 + 60) as u32]);
    assert_eq!(
        ctr.levels_crossed,
        [1, 0],
        "пересечение засчитано, пропуска нет: сигнал до него был"
    );
    assert_eq!(ctr.levels_skipped, [0, 0]);
}

#[test]
fn skip_counter_when_crossed_without_signal() {
    let p = lv_params();
    let mut c = level_world();
    set(&mut c, BORN + 50, 99.90, 100.10, 99.90, 100.00);
    let (sigs, ctr) = run_scan(c, &p);
    assert!(sigs.is_empty());
    assert_eq!(ctr.levels_crossed, [1, 0]);
    assert_eq!(ctr.levels_skipped, [1, 0]);
}

#[test]
fn false_breakout_forbids_level_forever() {
    let mut p = lv_params();
    let mk = || {
        let mut c = level_world();
        // пересечение и возврат с закрытием глубже f = 0,1 % (ниже 99,925) → уровень запрещён
        set(&mut c, BORN + 50, 100.00, 100.20, 99.80, 99.85);
        // зелёная минутка в зоне спустя 3 часа — вне окна r, но запрет вечный
        set(&mut c, BORN + 200, 99.85, 99.95, 99.84, 99.90);
        c
    };
    assert!(run_scan(mk(), &p).0.is_empty());
    p.sig.ban = false;
    assert_eq!(
        run_scan(mk(), &p).0.len(),
        1,
        "без запрета тот же уровень торгуется"
    );
}

fn ex_nos() -> super::params::ExitCfg {
    let mut e = Params::base().exit;
    e.slip_bps = 0;
    e
}

const T: usize = 24_500;
const NATR: i64 = 500_000; // 0,5 % → 1 NATR = 0,5 при входе 100

fn trade_world() -> Cols {
    flat(30_000, 100.0)
}

fn sim(c: Cols, side: Side, ex: &super::params::ExitCfg) -> TradeOut {
    let s = sym(c);
    simulate(&s, side, T, NATR, ex, None).expect("исход")
}

#[test]
fn entry_minute_stop_first_beats_take() {
    let mut c = trade_world();
    set(&mut c, T + 1, 100.0, 101.2, 94.0, 100.0); // и тейк 1 (100,5), и стоп 95 — в минутке входа стоп первым
    let tr = sim(c, Side::Long, &ex_nos());
    assert_eq!(tr.kind, ExitKind::HardStop);
    assert_eq!(tr.legs.len(), 1);
    assert_eq!(tr.legs[0].px, px(95.0));
    assert_eq!(tr.takes, 0);
}

#[test]
fn take_is_strictly_through_and_stop_touches() {
    let mut c = trade_world();
    set(&mut c, T + 1, 100.0, 100.5, 99.9, 100.1); // high == цена тейка → не исполнен
    set(&mut c, T + 2, 100.1, 100.5 + 1e-8, 100.05, 100.1); // на единицу выше → исполнен
    set(&mut c, T + 3, 100.1, 100.2, 100.0, 100.05); // low == стоп (безубыток уже включён → стоп на 100): касание → выход
    let tr = sim(c, Side::Long, &ex_nos());
    assert_eq!(tr.takes, 1);
    assert_eq!(tr.legs[0].min as usize, T + 2);
    assert_eq!(tr.legs[0].px, px(100.5));
    assert!(tr.legs[0].maker);
    assert_eq!(tr.exit_min as usize, T + 3);
    assert_eq!(tr.kind, ExitKind::BreakEven);
}

#[test]
fn take_and_breakeven_same_minute_breakeven_wins() {
    let mut c = trade_world();
    set(&mut c, T + 1, 100.0, 100.2, 99.9, 100.1);
    set(&mut c, T + 2, 100.2, 100.6, 99.9, 100.0); // первый тейк (100,5) и касание входа в одной минутке
    let tr = sim(c, Side::Long, &ex_nos());
    assert_eq!(tr.takes, 1, "тейк исполнен");
    assert_eq!(
        tr.kind,
        ExitKind::BreakEven,
        "остаток ушёл по безубытку в той же минутке"
    );
    assert_eq!(tr.exit_min as usize, T + 2);
    assert_eq!(tr.legs.last().map(|l| l.px), Some(px(100.0)));
}

#[test]
fn breakeven_uses_previous_minute_state() {
    let mut c = trade_world();
    set(&mut c, T + 1, 100.0, 100.2, 99.9, 100.1);
    set(&mut c, T + 2, 100.3, 100.6, 100.2, 100.5); // первый тейк, вход не тронут
    set(&mut c, T + 3, 100.1, 100.2, 99.5, 99.8); // стоп = вход (состояние прошлой минутки), цена прошла вниз
    let tr = sim(c, Side::Long, &ex_nos());
    assert_eq!(tr.takes, 1);
    assert_eq!(
        (tr.kind, tr.exit_min as usize),
        (ExitKind::BreakEven, T + 3)
    );
    assert_eq!(tr.legs.last().map(|l| l.px), Some(px(100.0)));
}

#[test]
fn gap_through_stop_fills_at_open() {
    let mut c = trade_world();
    set(&mut c, T + 1, 100.0, 100.1, 99.9, 100.0);
    set(&mut c, T + 2, 94.0, 94.5, 93.0, 93.5); // гэп ниже стопа 95 → по открытию
    let tr = sim(c, Side::Long, &ex_nos());
    assert_eq!(tr.kind, ExitKind::HardStop);
    assert!(tr.at_open);
    assert_eq!(tr.legs.last().map(|l| l.px), Some(px(94.0)));
}

#[test]
fn trail_arms_then_exits_on_next_minute() {
    let mut c = trade_world();
    set(&mut c, T + 1, 100.0, 100.2, 99.9, 100.1);
    set(&mut c, T + 2, 100.1, 101.2, 100.05, 101.0); // 2 NATR = 1,0 пройдено → трейл 101,2 − 1,0 = 100,2; тейк 1 исполнен
    set(&mut c, T + 3, 101.0, 101.0, 100.1, 100.3); // low ниже трейла 100,2 → выход по трейлу
    let tr = sim(c, Side::Long, &ex_nos());
    assert_eq!((tr.kind, tr.exit_min as usize), (ExitKind::Trail, T + 3));
    assert_eq!(tr.legs.last().map(|l| l.px), Some(px(100.2)));
}

#[test]
fn taker_slippage_and_short_mirror() {
    let mut c = trade_world();
    set(&mut c, T + 1, 100.0, 100.1, 99.9, 100.0);
    set(&mut c, T + 2, 100.0, 105.5, 99.9, 105.4); // шорт: стоп 5 % = 105,0 (+ проскальзывание на входе)
    let ex = Params::base().exit; // slip 1 bp
    let tr = sim(c, Side::Short, &ex);
    // вход шорта: продажа по open 100 с проскальзыванием 1 bp вниз = 99,99
    assert_eq!(tr.entry_px, px(99.99));
    assert_eq!(tr.kind, ExitKind::HardStop);
    // стоп = 99,99 · 1,05 = 104,9895; тейкер покупает дороже на 1 bp: 104,9895 · 1,0001
    let stop = (px(99.99) as i128 * 105 / 100) as i64;
    let fill = stop + (stop as i128 / 10_000) as i64;
    assert_eq!(tr.legs.last().map(|l| l.px), Some(fill));
}

fn mk_trade(entry: usize, exit: usize, exit_px: f64, at_open: bool) -> TradeOut {
    TradeOut {
        entry_min: entry as u32,
        entry_px: px(100.0),
        legs: vec![Leg {
            min: exit as u32,
            px: px(exit_px),
            u: NOTIONAL_U,
            maker: false,
        }],
        exit_min: exit as u32,
        kind: ExitKind::Forced,
        at_open,
        takes: 0,
    }
}

#[test]
fn funding_only_when_held_strictly_through() {
    let base = START_MIN + 100;
    let mut c = flat(START_MIN + 3000, 100.0);
    c.c[base + 49] = px(100.0);
    let fund = |min: usize| Fund {
        min: min as u32,
        rate_e8: 10_000, // 0,0001
        mark: px(100.0),
    };
    let s = Sym::from_cols("T", c, vec![fund(base), fund(base + 50), fund(base + 100)]);
    let tr = mk_trade(base, base + 100, 100.0, false);
    // выплата в минуте входа — не «строго через»; в минуте выхода (не по гэпу) — платится
    let long = settle(&s, Side::Long, &tr);
    assert!(
        (long.funding - (-0.05 * 2.0)).abs() < 1e-9,
        "лонг платит 2 выплаты по 0,05: {}",
        long.funding
    );
    let short = settle(&s, Side::Short, &tr);
    assert!((short.funding - 0.10).abs() < 1e-9, "шорт получает");
    let gap = settle(&s, Side::Long, &mk_trade(base, base + 100, 100.0, true));
    assert!(
        (gap.funding - (-0.05)).abs() < 1e-9,
        "выход гэпом по открытию минуты выплаты — выплаты нет"
    );
}

fn fee_taker(n: f64) -> f64 {
    n * 3.5 * 0.9 / 1e4
}

#[test]
fn daily_mtm_splits_and_sums_to_total() {
    // вход 15.09 23:00 UTC (день 0), выход 16.09 01:00 (день 1); отметка на 00:00 — закрытие 23:59
    let e = (START_DAY + 1) * 1440 - 60;
    let x = (START_DAY + 1) * 1440 + 60;
    let mut c = flat(START_MIN + 4000, 100.0);
    c.c[(START_DAY + 1) * 1440 - 1] = px(101.0);
    let s = sym(c);
    let tr = mk_trade(e, x, 102.0, false);
    let st = settle(&s, Side::Long, &tr);
    let entry_fee = fee_taker(500.0);
    let exit_fee = fee_taker(500.0 * 1.02);
    let total = 10.0 - entry_fee - exit_fee;
    assert_eq!(st.daily.len(), 2);
    assert!(
        (st.daily[0].1 - (5.0 - entry_fee)).abs() < 1e-6,
        "день 0: переоценка 500·1 % и комиссия входа"
    );
    assert!(
        (st.daily[0].1 + st.daily[1].1 - total).abs() < 1e-9,
        "сумма по суткам = итог сделки"
    );
    assert_eq!((st.daily[0].0, st.daily[1].0), (0, 1));
}

#[test]
fn trade_across_half_boundary_stays_in_entry_half() {
    let last_a = START_DAY + HALF_DAYS - 1;
    let e = (last_a + 1) * 1440 - 60;
    let x = (last_a + 1) * 1440 + 60;
    let c = flat((last_a + 3) * 1440, 100.0);
    let s = sym(c);
    let st = settle(&s, Side::Long, &mk_trade(e, x, 100.0, false));
    assert!(
        st.daily.iter().all(|(d, _)| *d == HALF_DAYS - 1),
        "всё в последних сутках A: {:?}",
        st.daily
    );
}

// ───────────────────────────── §13.11: опция `pk` ─────────────────────────────

/// Мир для `pk`: бары `HB + i` — (максимум, закрытие); минимум бара = максимум − 0,5; остальные бары — 90.
pub(super) fn bars_world(spec: &[(f64, f64)]) -> Cols {
    let mut c = flat(HB * 60 + 3000, 90.0);
    for (i, (h, close)) in spec.iter().enumerate() {
        let b = (HB + i) * 60;
        for m in b..b + 60 {
            set(&mut c, m, *close, *close, *close, *close);
        }
        set(&mut c, b, *close, *h, h - 0.5, *close);
    }
    c
}

pub(super) fn pk_params() -> Params {
    let mut p = lv_params();
    p.sig.lv.pk = true;
    p
}

/// Зеркало мира для шорта: цена → `k − цена`, high ↔ low; дыры остаются дырами.
pub(super) fn mirror(c: &Cols, k: f64) -> Cols {
    let mut m = Cols::new();
    for i in 0..c.o.len() {
        if c.o[i] == 0 {
            continue;
        }
        m.o[i] = px(k) - c.o[i];
        m.h[i] = px(k) - c.l[i];
        m.l[i] = px(k) - c.h[i];
        m.c[i] = px(k) - c.c[i];
        m.qv[i] = c.qv[i];
    }
    m
}

#[test]
fn pk_blocks_birth_when_pierced_between_first_pivot_and_birth() {
    // пивоты — бары 1 (100,00) и 7 (100,05), уровень 100,025, предел прокола 100,225; бары 3–4 — пара с равным
    // максимумом 100,30 (не пивот), закрытия ниже уровня: без `pk` проверка закрытий прокола не видит
    let spec = [
        (99.0, 98.6),
        (100.0, 99.6),
        (99.0, 98.6),
        (100.30, 99.9),
        (100.30, 99.9),
        (98.0, 97.6),
        (98.5, 98.1),
        (100.05, 99.65),
        (98.0, 97.6),
        (98.0, 97.6),
    ];
    let off = build_levels(&sym(bars_world(&spec)), &lv_params().sig.lv, Side::Long);
    assert_eq!(off.len(), 1, "без pk уровень рождается");
    assert_eq!(off[0].born as usize, (HB + 9) * 60);
    let on = build_levels(&sym(bars_world(&spec)), &pk_params().sig.lv, Side::Long);
    assert!(
        on.is_empty(),
        "pk: прокол 0,27 % > τ до рождения — уровня нет"
    );
    let mut ok = spec;
    ok[3].0 = 100.22; // 0,195 % < τ
    ok[4].0 = 100.22;
    let on = build_levels(&sym(bars_world(&ok)), &pk_params().sig.lv, Side::Long);
    assert_eq!(on.len(), 1, "прокол в пределах τ уровень не убивает");
    // шорт — зеркально
    let mc = mirror(&bars_world(&spec), 200.0);
    assert_eq!(
        build_levels(&sym(mc.clone()), &lv_params().sig.lv, Side::Short).len(),
        1
    );
    assert!(build_levels(&sym(mc), &pk_params().sig.lv, Side::Short).is_empty());
}

#[test]
fn pk_rechecks_history_when_pivot_joins() {
    // пивоты 1 (100,00), 6 (100,10) → уровень 100,05 (предел 100,2501), пара баров 3–4 с максимумом 100,24 проходит;
    // 3-й пивот (бар 9, 99,85) присоединяется, цена уровня 99,983 (предел 100,183): прокол 100,24 — нарушение
    let spec = [
        (99.0, 98.6),
        (100.0, 99.6),
        (98.5, 98.1),
        (100.24, 99.9),
        (100.24, 99.9),
        (98.0, 97.6),
        (100.10, 99.7),
        (98.0, 97.6),
        (98.0, 97.6),
        (99.85, 99.45),
        (98.0, 97.6),
        (98.0, 97.6),
    ];
    // окно L = 60 сут.: до конца данных никто не выпадает
    let mut p0 = lv_params();
    p0.sig.lv.l_days = 60;
    let mut p1 = pk_params();
    p1.sig.lv.l_days = 60;
    let off = build_levels(&sym(bars_world(&spec)), &p0.sig.lv, Side::Long);
    assert_eq!(off.len(), 1);
    assert_eq!(
        off[0].segs.len(),
        2,
        "присоединение 3-го пивота: вторая версия цены"
    );
    assert_eq!(off[0].end, u32::MAX, "без pk уровень жив");
    let on = build_levels(&sym(bars_world(&spec)), &p1.sig.lv, Side::Long);
    assert_eq!(
        on.len(),
        1,
        "при рождении (цена 100,05) прокол 100,24 допустим"
    );
    assert_eq!(
        on[0].end as usize,
        (HB + 11) * 60,
        "присоединение пивота пересчитало предел по новой цене: уровень умер"
    );
}

#[test]
fn pk_rechecks_history_when_member_expires() {
    // окно L = 1 сут.; пивоты 1 (100,30), 6 (100,10), 13 (99,95): присоединение (цена 100,117, предел 100,317) прокол
    // 100,24 (пара баров 9–10) прощает; на баре 25 пивот 1 выпадает → цена 100,025 (предел 100,225): прокол — нарушение
    let mut spec = vec![(98.0, 97.6); 31];
    spec[0] = (99.0, 98.6);
    spec[1] = (100.30, 99.9);
    spec[2] = (98.5, 98.1);
    spec[6] = (100.10, 99.7);
    spec[9] = (100.24, 99.9);
    spec[10] = (100.24, 99.9);
    spec[13] = (99.95, 99.55);
    let mut p = lv_params();
    p.sig.lv.l_days = 1;
    let off = build_levels(&sym(bars_world(&spec)), &p.sig.lv, Side::Long);
    assert_eq!(off.len(), 1);
    assert_eq!(off[0].segs.len(), 3, "рождение, присоединение, выпадение");
    assert_eq!(
        off[0].end as usize,
        (HB + 31) * 60,
        "без pk уровень живёт до выпадения пивота 6 (остаётся 1 < N)"
    );
    p.sig.lv.pk = true;
    let on = build_levels(&sym(bars_world(&spec)), &p.sig.lv, Side::Long);
    assert_eq!(on.len(), 1);
    assert_eq!(on[0].segs.len(), 3);
    assert_eq!(
        on[0].end as usize,
        (HB + 26) * 60,
        "выпадение пивота 1 сдвинуло цену вниз: прокол 100,24 теперь нарушение"
    );
}
