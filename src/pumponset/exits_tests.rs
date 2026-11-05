//! Тесты выходов П-35 (`exits`, `verdict --stage exits-fit|exits-test`): ручные пути цен с известным ответом
//! (тейк, стоп, трейлинг, предел, задержка исполнения, ничья стоп/трейлинг, потолок и его минута, фандинг в удержании),
//! быстрое == наивное на случайных путях, отсутствие утечки, совпадение набора сделок с `daycells`, детерминизм по
//! числу потоков, вердикт на синтетике с заведомым победителем.

use super::agg::{fix, unfix};
use super::exits::{
    exits_head, grid, grid_csv, scan, Cfg, ExitAgg, Fac, Family, Px, CAPS, NCAP, NCFG,
};
use super::play::PlayArgs;
use super::run::{run, RunArgs, Stage};
use super::run_tests::{
    lnp, params_json, pump_contract, put, rules_file, table, xd_file, Row, SEED, T0, XD,
};
use super::trade::{round_trip, Funding};
use super::verdict::{verdict, Stage as VStage, VerdictArgs};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

const PARAMS: &str = include_str!("../../docs/research/P-34-params.json");
const BPS: f64 = 1.0e4;

fn approx(a: f64, b: f64, tol: f64) {
    assert!((a - b).abs() <= tol, "{a} против {b} (допуск {tol})");
}

// ---------- сетка ----------

#[test]
fn grid_has_84_configs_in_stable_numbering() {
    let g = grid();
    assert_eq!(g.len(), NCFG);
    assert_eq!(NCFG, 84);
    let count = |f: Family| g.iter().filter(|c| c.family == f).count();
    assert_eq!(
        (count(Family::Base), count(Family::Tp), count(Family::Trail)),
        (2, 50, 32)
    );
    // базовая линия: cap = 360 («выход через 6 ч» П-34) и 1440
    assert_eq!((g[0].cap(), g[1].cap()), (360, 1440));
    assert!(g[..2].iter().all(|c| c.family == Family::Base));
    // семейство A: циклы G → S → cap
    let csv = grid_csv();
    let lines: Vec<&str> = csv.lines().collect();
    assert_eq!(lines.len(), NCFG + 1);
    assert_eq!(lines[0], "cfg,family,G,S,R,cap");
    assert_eq!(lines[1], "0,base,,,,360");
    assert_eq!(lines[2], "1,base,,,,1440");
    assert_eq!(lines[3], "2,tp,100,100,,360");
    assert_eq!(lines[4], "3,tp,100,100,,1440");
    assert_eq!(lines[3 + 8], "10,tp,100,none,,360");
    assert_eq!(lines[3 + 9], "11,tp,100,none,,1440");
    assert_eq!(lines[3 + 10], "12,tp,200,100,,360");
    assert_eq!(lines[3 + 49], "51,tp,1000,none,,1440");
    // семейство B: циклы R → S (100, 300, 500, нет) → cap
    assert_eq!(lines[3 + 50], "52,trail,,100,100,360");
    assert_eq!(lines[3 + 52], "54,trail,,300,100,360");
    assert_eq!(lines[3 + 56], "58,trail,,none,100,360");
    assert_eq!(lines[3 + 81], "83,trail,,none,500,1440");
    // стоп семейства B только 100/300/500/нет; 200 нет
    assert!(g
        .iter()
        .filter(|c| c.family == Family::Trail)
        .all(|c| c.s_bps() != Some(200)));
    // уникальность параметров
    let uniq: std::collections::HashSet<String> = csv
        .lines()
        .skip(1)
        .map(|l| l.split_once(',').unwrap().1.to_string())
        .collect();
    assert_eq!(uniq.len(), NCFG);
}

// ---------- пути с известным ответом ----------

/// Путь: `cf[0] = cf[1] = 100` (вход на минуте 1, `t_f = 0`, `first_min = 0`), дальше `cf[1 + j] = 100·exp(s·pnl(j)/1e4)`
/// — позиция стороны `sign` в минуте `j` имеет ровно `pnl(j)` bps. Всего `n` минут после входа.
fn path(sign: i8, n: usize, pnl: impl Fn(usize) -> f64) -> Vec<f64> {
    let s = f64::from(sign);
    let mut cf = vec![100.0, 100.0];
    for j in 1..=n {
        cf.push(100.0 * (s * pnl(j) / BPS).exp());
    }
    cf
}

/// Фандинг, покрывающий окна: выплата `1e-4` каждые 480 минут от −960 (все сделки тестов входят).
fn rates_wide() -> Vec<(i64, f64)> {
    (-2..40).map(|k| (480 * i64::from(k), 1.0e-4)).collect()
}

fn id(fam: Family, g: Option<u32>, r: Option<u32>, s: Option<u32>, cap: u32) -> usize {
    grid()
        .iter()
        .position(|c| {
            c.family == fam && c.g_bps() == g && c.r_bps() == r && c.s_bps() == s && c.cap() == cap
        })
        .unwrap()
}

fn tp(g: u32, s: Option<u32>, cap: u32) -> usize {
    id(Family::Tp, Some(g), None, s, cap)
}

fn trail(r: u32, s: Option<u32>, cap: u32) -> usize {
    id(Family::Trail, None, Some(r), s, cap)
}

/// Одна сделка в пустой накопитель (1 правило, 1 сутки).
fn one(cf: &[f64], rates: &[(i64, f64)], sign: i8) -> ExitAgg {
    let mut ex = ExitAgg::new(1, 1);
    let px = Px {
        cf,
        first_min: 0,
        funding: Funding::new(rates),
    };
    let g = ex.group(0, usize::from(sign < 0), 0);
    ex.add_trade(g, &px, 0, sign);
    ex
}

/// Итог единственной сделки по конфигурации: `(gross bps, fund bps, удержание, причина)`.
fn res(ex: &ExitAgg, sign: i8, cfg: usize) -> (f64, f64, i64, &'static str) {
    let g = ex.group(0, usize::from(sign < 0), 0);
    let c = ex.cfg_cell(g, cfg);
    let reason = match (c.n_tp, c.n_sl, c.n_trail) {
        (1, 0, 0) => "tp",
        (0, 1, 0) => "sl",
        (0, 0, 1) => "trail",
        (0, 0, 0) => "cap",
        other => panic!("несколько причин у одной сделки: {other:?}"),
    };
    (unfix(c.gross), unfix(c.fund), c.hold, reason)
}

#[test]
fn take_profit_executes_next_minute_close_after_decision() {
    // решение на минуте 5 (130 ≥ 100), исполнение по цене минуты 6 (90): задержка в одну минуту, не по 130
    let pnl = |j: usize| match j {
        1..=4 => 0.0,
        5 => 130.0,
        6 => 90.0,
        _ => 400.0,
    };
    for sign in [1i8, -1] {
        let ex = one(&path(sign, 1500, pnl), &[(-1000, 0.0), (5000, 0.0)], sign);
        for s in [None, Some(100), Some(300)] {
            let (g, _, hold, why) = res(&ex, sign, tp(100, s, 360));
            approx(g, 90.0, 1e-6);
            assert_eq!((hold, why), (6, "tp"), "sign {sign} S {s:?}");
        }
        // тейк 200 и 300 — на минуте 7 (400), исполнение на минуте 8 (400)
        for g_lvl in [200, 300] {
            let (g, _, hold, why) = res(&ex, sign, tp(g_lvl, None, 360));
            approx(g, 400.0, 1e-6);
            assert_eq!((hold, why), (8, "tp"));
        }
        // 500 и 1000 не достигнуты: выход по пределу, цена минуты предела (j = 359) = 400
        for g_lvl in [500, 1000] {
            let (g, _, hold, why) = res(&ex, sign, tp(g_lvl, None, 360));
            approx(g, 400.0, 1e-6);
            assert_eq!((hold, why), (359, "cap"));
        }
        // базовая линия
        let (g, _, hold, why) = res(&ex, sign, 0);
        approx(g, 400.0, 1e-6);
        assert_eq!((hold, why), (359, "cap"));
        let (_, _, hold, why) = res(&ex, sign, 1);
        assert_eq!((hold, why), (1439, "cap"));
    }
}

#[test]
fn stop_loss_and_order_of_events_by_minute() {
    // минута 3: −110 (стоп 100 коснулся), минута 4: −150 (исполнение), далее взлёт: тейк 100 — на минуте 5
    let pnl = |j: usize| match j {
        1..=2 => -20.0,
        3 => -110.0,
        4 => -150.0,
        _ => 500.0,
    };
    for sign in [1i8, -1] {
        let ex = one(&path(sign, 1500, pnl), &[(-1000, 0.0), (5000, 0.0)], sign);
        // стоп 100 раньше тейка 100: SL, исполнение по минуте 4 = −150
        let (g, _, hold, why) = res(&ex, sign, tp(100, Some(100), 360));
        approx(g, -150.0, 1e-6);
        assert_eq!((hold, why), (4, "sl"));
        // стоп 200 не касается, тейк 100 — на минуте 5, исполнение на минуте 6 (500)
        let (g, _, hold, why) = res(&ex, sign, tp(100, Some(200), 360));
        approx(g, 500.0, 1e-6);
        assert_eq!((hold, why), (6, "tp"));
        // без стопа: тот же тейк
        let (_, _, hold, why) = res(&ex, sign, tp(100, None, 1440));
        assert_eq!((hold, why), (6, "tp"));
    }
    // тейк раньше стопа: +150 на минуте 2, затем −300 на минуте 4 → TP (исполнение на минуте 3 = 160)
    let pnl = |j: usize| match j {
        1 => 0.0,
        2 => 150.0,
        3 => 160.0,
        _ => -300.0,
    };
    let ex = one(&path(1, 1500, pnl), &[(-1000, 0.0), (5000, 0.0)], 1);
    let (g, _, hold, why) = res(&ex, 1, tp(100, Some(100), 360));
    approx(g, 160.0, 1e-6);
    assert_eq!((hold, why), (3, "tp"));
}

#[test]
fn trailing_and_simultaneous_stop_and_trail() {
    // минута 3: +150 (трейлинг 100 активен), минута 4: 150, минута 5: −120 — в одну минуту и просадка ≥ R (150−120 ≥ 100…
    // 150 − (−120) = 270), и стоп 100; исполнение на минуте 6 = −130; тейк 100 коснулся раньше, на минуте 3
    let pnl = |j: usize| match j {
        1..=2 => 0.0,
        3..=4 => 150.0,
        5 => -120.0,
        _ => -130.0,
    };
    for sign in [1i8, -1] {
        let ex = one(&path(sign, 1500, pnl), &[(-1000, 0.0), (5000, 0.0)], sign);
        // стоп и трейлинг в одну минуту: приоритет стопа
        let (g, _, hold, why) = res(&ex, sign, trail(100, Some(100), 360));
        approx(g, -130.0, 1e-6);
        assert_eq!((hold, why), (6, "sl"));
        // без стопа та же минута — причина трейлинг
        let (g, _, hold, why) = res(&ex, sign, trail(100, None, 360));
        approx(g, -130.0, 1e-6);
        assert_eq!((hold, why), (6, "trail"));
        // стоп 300 не касается — трейлинг
        let (_, _, _, why) = res(&ex, sign, trail(100, Some(300), 360));
        assert_eq!(why, "trail");
        // трейлинг 200 не активен (максимум 150 < 200): стоп 100 на минуте 5, без стопа — предел
        let (_, _, hold, why) = res(&ex, sign, trail(200, Some(100), 360));
        assert_eq!((hold, why), (6, "sl"));
        let (_, _, hold, why) = res(&ex, sign, trail(200, None, 360));
        assert_eq!((hold, why), (359, "cap"));
        // тейк 100 касается на минуте 3, стоп — на минуте 5: тейк раньше (исполнение на минуте 4 = 150)
        let (g, _, hold, why) = res(&ex, sign, tp(100, Some(100), 360));
        approx(g, 150.0, 1e-6);
        assert_eq!((hold, why), (4, "tp"));
    }
    // трейлинг на откате без стопа: максимум 400 на минуте 10, откат на 100 → минута 12 (299 ≤ 300), исполнение на 13
    let pnl = |j: usize| match j {
        1..=9 => 50.0,
        10 => 400.0,
        11 => 350.0,
        12 => 299.0,
        13 => 280.0,
        _ => 100.0,
    };
    for sign in [1i8, -1] {
        let ex = one(&path(sign, 1500, pnl), &[(-1000, 0.0), (5000, 0.0)], sign);
        let (g, _, hold, why) = res(&ex, sign, trail(100, None, 360));
        approx(g, 280.0, 1e-6);
        assert_eq!((hold, why), (13, "trail"));
        // R = 200: откат от 400 до ≤ 200 — на минуте 14 (100), исполнение на 15 (100)
        let (g, _, hold, why) = res(&ex, sign, trail(200, None, 360));
        approx(g, 100.0, 1e-6);
        assert_eq!((hold, why), (15, "trail"));
        // R = 500: максимум 400 < 500 — трейлинг не активен, предел
        let (_, _, hold, why) = res(&ex, sign, trail(500, None, 360));
        assert_eq!((hold, why), (359, "cap"));
    }
}

#[test]
fn cap_executes_at_scheduled_minute_and_beats_late_decision() {
    // ничего не происходит: предел 360 — по цене минуты 359, предел 1440 — по цене минуты 1439
    let ex = one(&path(1, 1500, |_| 10.0), &rates_wide(), 1);
    let (g, _, hold, why) = res(&ex, 1, tp(100, Some(100), 360));
    approx(g, 10.0, 1e-6);
    assert_eq!((hold, why), (359, "cap"));
    let (_, _, hold, why) = res(&ex, 1, trail(100, None, 1440));
    assert_eq!((hold, why), (1439, "cap"));

    // решение на минуте 358 = cap_j − 1 исполняется на минуте 359 (тейк успевает), решение на 359 — уже предел
    let late = |j: usize| match j {
        358 => 150.0,
        359 => 140.0,
        _ => 0.0,
    };
    let ex = one(&path(1, 1500, late), &rates_wide(), 1);
    let (g, _, hold, why) = res(&ex, 1, tp(100, None, 360));
    approx(g, 140.0, 1e-6);
    assert_eq!((hold, why), (359, "tp"));
    let later = |j: usize| if j == 359 { 150.0 } else { 0.0 };
    let ex = one(&path(1, 1500, later), &rates_wide(), 1);
    let (g, _, hold, why) = res(&ex, 1, tp(100, None, 360));
    approx(g, 150.0, 1e-6);
    assert_eq!((hold, why), (359, "cap"));
    // у предела 1440 то же касание на минуте 359 — обычный тейк, исполнение на 360
    let (_, _, hold, why) = res(&ex, 1, tp(100, None, 1440));
    assert_eq!((hold, why), (360, "tp"));
}

#[test]
fn mfe_and_minute_of_maximum() {
    // максимум окна 360 — 275 на минуте 40; в окне 1440 — 900 на минуте 700
    let pnl = |j: usize| match j {
        40 => 275.0,
        700 => 900.0,
        1..=39 => 10.0,
        _ => -20.0,
    };
    let ex = one(&path(1, 1500, pnl), &rates_wide(), 1);
    let g = ex.group(0, 0, 0);
    let (c360, c1440) = (ex.cap_cell(g, 0), ex.cap_cell(g, 1));
    assert_eq!((c360.n, c1440.n), (1, 1));
    // потолок «за 1440» один на сделку; потолок собственного cap — по своему окну
    approx(unfix(c360.mfe), 900.0, 1e-6);
    assert_eq!(c360.mfe_min, 700);
    approx(unfix(c360.mfe_cap), 275.0, 1e-6);
    assert_eq!(c360.mfe_cap_min, 40);
    approx(unfix(c1440.mfe), 900.0, 1e-6);
    assert_eq!((c1440.mfe_min, c1440.mfe_cap_min), (700, 700));
    approx(unfix(c1440.mfe_cap), 900.0, 1e-6);

    // сделка в минус целиком: потолок — максимум по j ≥ 1 (вход pnl = 0 в него не входит), а не 0
    let down = |j: usize| -50.0 - (j as f64) * 0.1;
    let ex = one(&path(-1, 1500, down), &rates_wide(), -1);
    let g = ex.group(0, 1, 0);
    let c = ex.cap_cell(g, 0);
    approx(unfix(c.mfe_cap), -50.1, 1e-6);
    assert_eq!(c.mfe_cap_min, 1);

    // равные максимумы — первая минута
    let flat = |j: usize| if j == 5 || j == 9 { 300.0 } else { 0.0 };
    let ex = one(&path(1, 1500, flat), &rates_wide(), 1);
    assert_eq!(ex.cap_cell(ex.group(0, 0, 0), 0).mfe_cap_min, 5);
}

#[test]
fn funding_is_paid_over_actual_holding_window() {
    // вход на минуте 1; выплаты: на 1 (минута входа — позицию не застаёт), 6, 8, 9 (минута выхода — застаёт), 10 (после)
    let mut rates = vec![(-500, 1.0e-4), (1, 1.0e-4)];
    rates.extend([(6, 2.0e-4), (8, 4.0e-4), (9, 8.0e-4), (10, 16.0e-4)]);
    rates.extend([
        (400, 1.0e-4),
        (800, 1.0e-4),
        (1200, 1.0e-4),
        (1600, 1.0e-4),
        (3000, 1.0e-4),
    ]);
    // тейк 100: решение на минуте 7 (150), исполнение на минуте 8 (160) = минута 9 → выплаты 6, 8, 9
    let pnl = |j: usize| match j {
        7 => 150.0,
        8 => 160.0,
        _ => 0.0,
    };
    for sign in [1i8, -1] {
        let ex = one(&path(sign, 1500, pnl), &rates, sign);
        let (g, f, hold, why) = res(&ex, sign, tp(100, None, 360));
        approx(g, 160.0, 1e-6);
        assert_eq!((hold, why), (8, "tp"));
        // лонг платит, шорт получает: Σ ставок = 14e-4 → 14 bps со знаком стороны
        approx(f, f64::from(sign) * 14.0, 1e-9);
        // предел 360: выплаты 6, 8, 9, 10 (минуты ≤ 360), 400 уже после
        let (_, f, _, why) = res(&ex, sign, 0);
        assert_eq!(why, "cap");
        approx(f, f64::from(sign) * 30.0, 1e-9);
    }
}

#[test]
fn trade_without_funding_coverage_does_not_enter() {
    // первая выплата файла (минута 5) позже минуты входа (1): выплаты до неё неизвестны — как в trade.rs, сделки нет
    let late = vec![
        (5, 1.0e-4),
        (485, 1.0e-4),
        (965, 1.0e-4),
        (1445, 1.0e-4),
        (1925, 1.0e-4),
    ];
    let cf = path(1, 1500, |_| 10.0);
    let ex = one(&cf, &late, 1);
    let g = ex.group(0, 0, 0);
    assert!(ex.cap_cell(g, 0).n == 0 && ex.cap_cell(g, 1).n == 0);
    for k in 0..NCFG {
        assert_eq!(*ex.cfg_cell(g, k), Default::default());
    }
    assert_eq!(ex.cnt.caps[0].uncovered, 1);
    assert_eq!(ex.cnt.caps[1].uncovered, 1);
    assert_eq!(ex.cnt.caps[0].trades + ex.cnt.caps[1].trades, 0);

    // файл кончается раньше предела 1440 (последняя выплата 480): пара cap 1440 без покрытия, cap 360 — в сделках
    let short = vec![(-480, 1.0e-4), (0, 1.0e-4), (480, 1.0e-4)];
    let ex = one(&cf, &short, 1);
    let g = ex.group(0, 0, 0);
    assert_eq!((ex.cap_cell(g, 0).n, ex.cap_cell(g, 1).n), (1, 0));
    assert_eq!((ex.cnt.caps[0].trades, ex.cnt.caps[1].uncovered), (1, 1));
    // конфигурации cap 1440 пусты, cap 360 заполнены
    assert_eq!(*ex.cfg_cell(g, 1), Default::default());
    assert_eq!(ex.cfg_cell(g, 0).hold, 359);
}

#[test]
fn short_series_enters_only_caps_it_reaches() {
    // после входа 400 минут: cap 360 (минута 359) достижим, cap 1440 — нет
    let cf = path(1, 400, |j| if j == 300 { 250.0 } else { 0.0 });
    let ex = one(&cf, &rates_wide(), 1);
    let g = ex.group(0, 0, 0);
    assert_eq!((ex.cap_cell(g, 0).n, ex.cap_cell(g, 1).n), (1, 0));
    assert_eq!(ex.cnt.caps[1].nodata, 1);
    // потолок «за 1440» — по доступному окну (до минуты 400), максимум 250 на минуте 300
    approx(unfix(ex.cap_cell(g, 0).mfe), 250.0, 1e-6);
    assert_eq!(ex.cap_cell(g, 0).mfe_min, 300);
    // решение на j = 300 (250 ≥ 100) исполняется на 301 ≤ cap_j (359): тейк успевает, цена минуты 301 = 0
    let (g, _, hold, why) = res(&ex, 1, tp(100, None, 360));
    approx(g, 0.0, 1e-6);
    assert_eq!((hold, why), (301, "tp"));
}

// ---------- быстрое == наивное, утечка ----------

struct Xs(u64);

impl Xs {
    fn unit(&mut self) -> f64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        (x >> 11) as f64 / (1u64 << 53) as f64
    }
}

/// Случайный путь: шаги равномерны на `±step` bps; всего `n` минут.
fn rw(seed: u64, sign: i8, n: usize, step: f64) -> Vec<f64> {
    let mut r = Xs(seed | 1);
    let mut x = 0.0;
    let mut v = vec![0.0];
    for _ in 1..=n {
        x += (r.unit() - 0.5) * 2.0 * step;
        v.push(x);
    }
    path(sign, n, |j| v[j])
}

fn rates_var() -> Vec<(i64, f64)> {
    (-2i32..40)
        .map(|k| (480 * i64::from(k), 1.0e-4 * f64::from(k.rem_euclid(4) + 1)))
        .collect()
}

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
enum Why {
    Tp,
    Sl,
    Trail,
    Cap,
}

/// Наивная реализация П-35 «в лоб»: пошагово, `ln` на каждой минуте, один конфиг за раз.
/// Возвращает `(причина, удержание, gross bps, fund bps)`.
fn naive(cf: &[f64], sign: i8, cfg: &Cfg, rates: &[(i64, f64)]) -> (Why, u32, f64, f64) {
    let (b, s, c_in) = (1usize, f64::from(sign), cf[1]);
    let cap_j = cfg.cap() as usize - 1;
    let mut pmax = f64::NEG_INFINITY;
    let (mut why, mut j_exec) = (Why::Cap, cap_j);
    for j in 1..cap_j {
        let pnl = s * (cf[b + j] / c_in).ln() * BPS;
        pmax = pmax.max(pnl);
        let sl = cfg.s_bps().is_some_and(|x| pnl <= -f64::from(x));
        let tp = cfg.g_bps().is_some_and(|x| pnl >= f64::from(x));
        let tr = cfg
            .r_bps()
            .is_some_and(|r| pmax >= f64::from(r) && pnl <= pmax - f64::from(r));
        let hit = if sl {
            Some(Why::Sl)
        } else if tp {
            Some(Why::Tp)
        } else if tr {
            Some(Why::Trail)
        } else {
            None
        };
        if let Some(h) = hit {
            (why, j_exec) = (h, j + 1);
            break;
        }
    }
    let gross = s * (cf[b + j_exec] / c_in).ln() * BPS;
    let mut sum = 0.0;
    for &(m, r) in rates {
        if m > b as i64 && m <= (b + j_exec) as i64 {
            sum += r;
        }
    }
    (why, j_exec as u32, gross, s * sum * BPS)
}

fn assert_matches_naive(cf: &[f64], rates: &[(i64, f64)], sign: i8, tag: &str) {
    let ex = one(cf, rates, sign);
    let g = ex.group(0, usize::from(sign < 0), 0);
    for (k, cfg) in grid().iter().enumerate() {
        let (why, hold, gross, fund) = naive(cf, sign, cfg, rates);
        let c = ex.cfg_cell(g, k);
        assert_eq!(c.gross, fix(gross), "{tag} cfg {k} gross");
        assert_eq!(c.fund, fix(fund), "{tag} cfg {k} fund");
        assert_eq!(c.hold, i64::from(hold), "{tag} cfg {k} hold");
        let got = match (c.n_tp, c.n_sl, c.n_trail) {
            (1, 0, 0) => Why::Tp,
            (0, 1, 0) => Why::Sl,
            (0, 0, 1) => Why::Trail,
            _ => Why::Cap,
        };
        assert_eq!(got, why, "{tag} cfg {k} причина");
    }
}

#[test]
fn fast_scan_equals_naive_on_random_paths() {
    let rates = rates_var();
    let mut reasons = BTreeMap::<&str, usize>::new();
    for seed in 1..=40u64 {
        let step = [2.0, 6.0, 15.0][(seed % 3) as usize];
        for sign in [1i8, -1] {
            let cf = rw(seed * 7919, sign, 1500, step);
            assert_matches_naive(&cf, &rates, sign, &format!("seed {seed} sign {sign}"));
            let ex = one(&cf, &rates, sign);
            let g = ex.group(0, usize::from(sign < 0), 0);
            for k in 0..NCFG {
                let c = ex.cfg_cell(g, k);
                *reasons.entry("tp").or_default() += c.n_tp as usize;
                *reasons.entry("sl").or_default() += c.n_sl as usize;
                *reasons.entry("trail").or_default() += c.n_trail as usize;
            }
        }
    }
    // случайные пути задевают все причины выхода (иначе сравнение было бы вырожденным)
    for why in ["tp", "sl", "trail"] {
        assert!(reasons[why] > 200, "{why}: {reasons:?}");
    }
}

#[test]
fn scan_maximum_matches_naive_maximum() {
    let fac = [Fac::new(1.0), Fac::new(-1.0)];
    for seed in 1..=20u64 {
        for sign in [1i8, -1] {
            let cf = rw(seed * 104_729, sign, 1500, 9.0);
            let sc = scan(&cf, 1, 1439, sign, &fac[usize::from(sign < 0)]);
            let s = f64::from(sign);
            let pnl: Vec<f64> = (1..=1439)
                .map(|j| s * (cf[1 + j] / cf[1]).ln() * BPS)
                .collect();
            let argmax = |hi: usize| {
                let (mut best, mut at) = (f64::NEG_INFINITY, 0usize);
                for (i, &x) in pnl[..hi].iter().enumerate() {
                    if x > best {
                        (best, at) = (x, i + 1);
                    }
                }
                (best, at as u32)
            };
            assert_eq!(sc.mfe, argmax(1439), "seed {seed} sign {sign}");
            assert_eq!(sc.mfe_cap[0], argmax(359), "seed {seed} sign {sign}");
            assert_eq!(sc.mfe_cap[1], sc.mfe);
        }
    }
    assert_eq!(NCAP, CAPS.len());
}

#[test]
fn result_does_not_depend_on_prices_after_exit() {
    // изменение цен после минуты исполнения не меняет результат этой конфигурации (в том числе предела)
    let rates = rates_var();
    for seed in 1..=12u64 {
        for sign in [1i8, -1] {
            let cf = rw(seed * 15_485_863, sign, 1500, 8.0);
            let ex = one(&cf, &rates, sign);
            let g = ex.group(0, usize::from(sign < 0), 0);
            for k in 0..NCFG {
                let base = *ex.cfg_cell(g, k);
                let after = 1 + usize::try_from(base.hold).unwrap() + 1;
                let mut cf2 = cf.clone();
                for x in &mut cf2[after..] {
                    *x *= 3.0;
                }
                let ex2 = one(&cf2, &rates, sign);
                assert_eq!(
                    *ex2.cfg_cell(g, k),
                    base,
                    "seed {seed} sign {sign} cfg {k}: цены после выхода изменили сделку"
                );
            }
        }
    }
}

#[test]
fn baseline_equals_round_trip_of_p34() {
    // конфигурации «только cap» = H = cap из trade.rs, побитово
    let rates = rates_var();
    for seed in 1..=10u64 {
        for sign in [1i8, -1] {
            let cf = rw(seed * 31, sign, 1500, 7.0);
            let ex = one(&cf, &rates, sign);
            let g = ex.group(0, usize::from(sign < 0), 0);
            for (ci, &cap) in CAPS.iter().enumerate() {
                let t = round_trip(&cf, 0, &Funding::new(&rates), 0, cap, sign).unwrap();
                let c = ex.cfg_cell(g, ci);
                assert_eq!((c.gross, c.fund), (fix(t.gross_bps), fix(t.fund_bps)));
                assert_eq!(c.hold, i64::from(cap) - 1);
            }
        }
    }
}

// ---------- run: набор сделок как в daycells, детерминизм ----------

fn args_run(
    cache: &Path,
    params: &Path,
    out: &Path,
    rules: PathBuf,
    xd: PathBuf,
    threads: usize,
    exits: bool,
) -> RunArgs {
    RunArgs {
        cache: cache.to_path_buf(),
        params: params.to_path_buf(),
        out: out.to_path_buf(),
        stage: Stage::Test,
        rules: Some(rules),
        seed: SEED,
        oi_lag: 1,
        threads,
        xd: Some(xd),
        limit: None,
        exits,
        dump_triggers: false,
        play: PlayArgs::default(),
    }
}

/// Кэш из пампов трёх монет и «дампа» (цена перевёрнута: `c' = 10⁴/c` — сторона −1, путь pnl тот же).
fn cache_dir(dir: &Path) -> PathBuf {
    let cache = dir.join("cache");
    fs::create_dir_all(&cache).unwrap();
    put(
        &cache,
        "ABCUSDT",
        &pump_contract(17_280, &[4_000, 9_000, T0]),
    );
    put(&cache, "DEFUSDT", &pump_contract(17_280, &[6_000, 12_000]));
    put(&cache, "GHIUSDT", &pump_contract(17_280, &[T0]));
    let mut d = pump_contract(17_280, &[5_000, T0]);
    for x in &mut d.close {
        *x = 10_000.0 / *x;
    }
    put(&cache, "DMPUSDT", &d);
    cache
}

fn params_360_1440(dir: &Path) -> PathBuf {
    let p = dir.join("params.json");
    let text = params_json("2019-12", "2020-01", "[90]").replace("[90,180]", "[360,1440]");
    assert!(text.contains("[360,1440]"));
    fs::write(&p, text).unwrap();
    p
}

const RULES: [(u8, u8, u8); 4] = [(0, 0, 0), (0, 1, 1), (1, 0, 0), (9, 0, 0)];

fn key(r: &Row, cols: &[&str]) -> String {
    cols.iter()
        .map(|c| r[*c].clone())
        .collect::<Vec<_>>()
        .join("|")
}

#[test]
fn exits_trades_equal_daycells_trades_and_baseline_is_p34_h() {
    let dir = tempfile::tempdir().unwrap();
    let cache = cache_dir(dir.path());
    let pj = params_360_1440(dir.path());
    let (rf, xf) = (rules_file(dir.path(), &RULES), xd_file(dir.path(), XD));
    let out = dir.path().join("out");
    run(args_run(&cache, &pj, &out, rf, xf, 2, true)).unwrap();

    let dc = table(&out.join("daycells-test.csv"));
    let ex = table(&out.join("exits-test.csv"));
    assert!(!ex.is_empty());
    // daycells (H = 360 → hi 0, 1440 → hi 1) по ключу (правило, сторона, сутки)
    let mut by_dc: BTreeMap<(String, usize), &Row> = BTreeMap::new();
    for r in &dc {
        let hi: usize = r["hi"].parse().unwrap();
        if hi < 2 {
            by_dc.insert((key(r, &["fam", "wi", "ti", "sign", "day"]), hi), r);
        }
    }
    let mut seen = 0usize;
    let mut signs = std::collections::BTreeSet::new();
    for r in &ex {
        let cfg: usize = r["cfg"].parse().unwrap();
        let k = key(r, &["fam", "wi", "ti", "sign", "day"]);
        signs.insert(r["sign"].clone());
        // n одинаков у всех конфигураций с тем же cap и равен nret из daycells для H = cap
        let hi = usize::from(grid()[cfg].cap() == 1440);
        let d = by_dc
            .get(&(k.clone(), hi))
            .unwrap_or_else(|| panic!("в daycells нет {k} hi {hi}"));
        assert_eq!(r["n"], d["nret"], "{k} cfg {cfg}");
        if cfg < 2 {
            // «только cap» == H = cap из daycells: gross, фандинг, удержание
            assert_eq!(r["gross_bps_sum"], d["gross_bps"], "{k} cfg {cfg}");
            assert_eq!(r["fund_bps_sum"], d["fund_bps"], "{k} cfg {cfg}");
            let n: i64 = r["n"].parse().unwrap();
            assert_eq!(
                r["hold_min_sum"],
                (n * (i64::from(CAPS[cfg]) - 1)).to_string()
            );
            assert_eq!(r["n_cap"], r["n"]);
            seen += 1;
        }
        // причины выхода дают n
        let parts: u64 = ["n_tp", "n_sl", "n_trail", "n_cap"]
            .iter()
            .map(|c| r[*c].parse::<u64>().unwrap())
            .sum();
        assert_eq!(parts.to_string(), r["n"]);
    }
    // и в обратную сторону: каждая ячейка daycells с nret > 0 есть среди базовых строк exits
    for r in &dc {
        let hi: usize = r["hi"].parse().unwrap();
        let nret: u64 = r["nret"].parse().unwrap();
        if hi < 2 && nret > 0 {
            let k = key(r, &["fam", "wi", "ti", "sign", "day"]);
            let found = ex.iter().any(|e| {
                e["cfg"] == hi.to_string() && key(e, &["fam", "wi", "ti", "sign", "day"]) == k
            });
            assert!(found, "в exits нет базовой строки для {k} hi {hi}");
        }
    }
    assert!(seen >= 6, "слишком мало сделок для сравнения: {seen}");
    assert_eq!(signs.len(), 2, "в данных есть и памп, и дамп: {signs:?}");

    // сетка и мета
    assert_eq!(
        fs::read_to_string(out.join("exits-grid.csv")).unwrap(),
        grid_csv()
    );
    let meta: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out.join("run-meta.json")).unwrap()).unwrap();
    assert_eq!(meta["exits"]["configs"], 84);
    // число сделок в мете = Σ n по строкам базовой линии cap 360, и оно не больше числа срабатываний с направлением
    let n360: u64 = ex
        .iter()
        .filter(|r| r["cfg"] == "0")
        .map(|r| r["n"].parse::<u64>().unwrap())
        .sum();
    assert_eq!(
        meta["exits"]["trades_by_cap"]["360"].as_u64().unwrap(),
        n360
    );
    assert!(n360 <= meta["exits"]["triggers"].as_u64().unwrap());
}

#[test]
fn exits_flag_does_not_change_main_tables_and_default_has_no_exits() {
    let dir = tempfile::tempdir().unwrap();
    let cache = cache_dir(dir.path());
    let pj = params_360_1440(dir.path());
    let (rf, xf) = (rules_file(dir.path(), &RULES), xd_file(dir.path(), XD));
    let (o0, o1) = (dir.path().join("o0"), dir.path().join("o1"));
    run(args_run(&cache, &pj, &o0, rf.clone(), xf.clone(), 2, false)).unwrap();
    run(args_run(&cache, &pj, &o1, rf, xf, 2, true)).unwrap();
    assert!(!o0.join("exits-test.csv").exists() && !o0.join("exits-grid.csv").exists());
    assert!(o1.join("exits-test.csv").exists());
    for f in [
        "daycells-test.csv",
        "lags-test.csv",
        "first-test.csv",
        "progress-test.csv",
        "episodes-test.csv",
        "xd.csv",
    ] {
        assert_eq!(
            fs::read(o0.join(f)).unwrap(),
            fs::read(o1.join(f)).unwrap(),
            "{f} изменился из-за --exits"
        );
    }
    let m0: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(o0.join("run-meta.json")).unwrap()).unwrap();
    assert!(m0.get("exits").is_none());
}

#[test]
fn exits_csv_is_byte_identical_for_1_and_4_threads() {
    let dir = tempfile::tempdir().unwrap();
    let cache = cache_dir(dir.path());
    let pj = params_360_1440(dir.path());
    let (rf, xf) = (rules_file(dir.path(), &RULES), xd_file(dir.path(), XD));
    let (o1, o4) = (dir.path().join("t1"), dir.path().join("t4"));
    run(args_run(&cache, &pj, &o1, rf.clone(), xf.clone(), 1, true)).unwrap();
    run(args_run(&cache, &pj, &o4, rf, xf, 4, true)).unwrap();
    let a = fs::read(o1.join("exits-test.csv")).unwrap();
    assert!(a.len() > 1000);
    assert_eq!(a, fs::read(o4.join("exits-test.csv")).unwrap());
    assert_eq!(
        fs::read(o1.join("exits-grid.csv")).unwrap(),
        fs::read(o4.join("exits-grid.csv")).unwrap()
    );
}

fn one_row(ex: &[Row], cfg: usize) -> &Row {
    let v: Vec<&Row> = ex.iter().filter(|r| r["cfg"] == cfg.to_string()).collect();
    assert_eq!(v.len(), 1, "cfg {cfg}");
    v[0]
}

#[test]
fn exits_on_single_pump_match_hand_calculation() {
    // памп с минуты T0: pnl после входа на t_f = T0 растёт на (ε + R) = 10,1 bps в минуту до минуты 89 после входа
    let dir = tempfile::tempdir().unwrap();
    let cache = dir.path().join("cache");
    fs::create_dir_all(&cache).unwrap();
    put(&cache, "ABCUSDT", &pump_contract(17_280, &[T0]));
    let pj = params_360_1440(dir.path());
    let rf = rules_file(dir.path(), &[(1, 0, 0)]);
    let out = dir.path().join("out");
    run(args_run(
        &cache,
        &pj,
        &out,
        rf,
        xd_file(dir.path(), XD),
        1,
        true,
    ))
    .unwrap();
    let ex = table(&out.join("exits-test.csv"));
    let t_in = T0 + 1;
    let pnl = |j: usize| (lnp(t_in + j, &[T0]) - lnp(t_in, &[T0])) * BPS;
    let row = |cfg: usize| one_row(&ex, cfg);
    // тейк G: касание на первой минуте j с pnl_j ≥ G (10,1·j: 100 → 10, 200 → 20, 300 → 30, 500 → 50), исполнение на j + 1;
    // до 1000 bps путь в окне cap 360 не доходит (максимум 898,9 на минуте 89) — выход по пределу
    for (g_lvl, want) in [
        (100u32, Some(10usize)),
        (200, Some(20)),
        (300, Some(30)),
        (500, Some(50)),
        (1000, None),
    ] {
        assert_eq!((1..=358).find(|&j| pnl(j) >= f64::from(g_lvl)), want);
        let r = row(tp(g_lvl, None, 360));
        let (j_exec, tp_n, cap_n) = match want {
            Some(j) => (j + 1, "1", "0"),
            None => (359, "0", "1"),
        };
        approx(r["gross_bps_sum"].parse().unwrap(), pnl(j_exec), 1e-6);
        assert_eq!(r["hold_min_sum"], j_exec.to_string());
        assert_eq!(
            (r["n"].as_str(), r["n_tp"].as_str(), r["n_cap"].as_str()),
            ("1", tp_n, cap_n)
        );
        // удержание короче интервала фандинга (ближайшие выплаты 14980 и 15460): выплат в окне нет
        approx(r["fund_bps_sum"].parse().unwrap(), 0.0, 1e-12);
    }
    approx(pnl(2), 20.2, 1e-9);
    // потолок: максимум на минуте 89 после входа (дальше только дрейф ε вверх → максимум на последней минуте окна)
    let r = row(0);
    assert_eq!(r["n"], "1");
    let (c0, c1) = (row(0), row(1));
    assert_eq!(c0["hold_min_sum"], "359");
    assert_eq!(c1["hold_min_sum"], "1439");
    // путь монотонен вверх: потолок окна 1440 — на минуте 1439, окна cap 360 — на 359
    assert_eq!(c0["mfe_min_sum"], "1439");
    assert_eq!(c0["mfe_cap_min_sum"], "359");
    approx(c0["mfe_bps_sum"].parse().unwrap(), pnl(1439), 1e-6);
    approx(c0["mfe_cap_bps_sum"].parse().unwrap(), pnl(359), 1e-6);
    // трейлинг на монотонном пути не срабатывает: причина cap
    let r = row(trail(100, None, 360));
    assert_eq!((r["n_trail"].as_str(), r["n_cap"].as_str()), ("0", "1"));
}

// ---------- verdict ----------

fn xrow(
    rule: (u8, u8, u8),
    sign: i8,
    cfg: usize,
    day: i64,
    month: i32,
    n: u32,
    gross_per_trade: f64,
) -> String {
    let nn = f64::from(n);
    // причины выхода: все по пределу; суммы удержания и потолка — произвольные, но согласованные
    format!(
        "{},{},{},{},{},{},{},{},{},0,{},0,0,0,{},{},{},{},{}",
        rule.0,
        rule.1,
        rule.2,
        sign,
        cfg,
        day,
        month,
        n,
        nn * gross_per_trade,
        u64::from(n) * 100,
        n,
        nn * 90.0,
        u64::from(n) * 7,
        nn * 80.0,
        u64::from(n) * 5,
    )
}

fn write_exits_input(
    dir: &Path,
    stage: &str,
    fill: impl Fn(&mut Vec<String>),
    grid_text: &str,
) -> PathBuf {
    let d = dir.join(format!("in-{stage}"));
    fs::create_dir_all(&d).unwrap();
    let mut lines = vec![exits_head().to_string()];
    fill(&mut lines);
    fs::write(
        d.join(format!("exits-{stage}.csv")),
        lines.join("\n") + "\n",
    )
    .unwrap();
    fs::write(d.join("exits-grid.csv"), grid_text).unwrap();
    d
}

fn params_file(dir: &Path) -> PathBuf {
    let p = dir.join("params.json");
    fs::write(&p, PARAMS).unwrap();
    p
}

fn vargs(
    stage: VStage,
    input: &Path,
    params: &Path,
    out: &Path,
    k: &Path,
    threads: usize,
) -> VerdictArgs {
    VerdictArgs {
        stage,
        input: input.to_path_buf(),
        in_fit: None,
        params: params.to_path_buf(),
        out: out.to_path_buf(),
        k: Some(k.to_path_buf()),
        threads,
    }
}

fn csv_rows(path: &Path) -> Vec<Row> {
    table(path)
}

/// Колебание net по суткам (детерминированное), чтобы интервал не вырождался.
fn wiggle(i: usize) -> f64 {
    [-3.0, 1.0, 2.0, -1.0, 3.0, -2.0][i % 6]
}

const FIT_DAY0: i64 = 20_148; // 2025-03-01
const TEST_DAY0: i64 = 20_454; // 2026-01-01

#[test]
fn fit_selects_known_winner_with_tie_breaks_and_is_thread_independent() {
    let dir = tempfile::tempdir().unwrap();
    let a = (0u8, 0u8, 0u8);
    let b = (1u8, 0u8, 0u8);
    // победитель (A, +1): три идентичные конфигурации — tp G300 S100 cap1440, cap360 и S none cap360 → ничья:
    // меньший cap, затем меньший S → tp G300 S100 cap360
    let w_a_plus = [
        tp(300, Some(100), 1440),
        tp(300, Some(100), 360),
        tp(300, None, 360),
    ];
    // (A, −1): единственный победитель trail R200 S300 cap1440
    let w_a_minus = trail(200, Some(300), 1440);
    // (B, +1): два идентичных по cap 1440 — G200 S200 и G200 S300 → меньший S (200)
    let w_b_plus = [tp(200, Some(200), 1440), tp(200, Some(300), 1440)];
    let grid_text = grid_csv();
    let inp = write_exits_input(
        dir.path(),
        "fit",
        |lines| {
            for (rule, sign) in [(a, 1i8), (a, -1), (b, 1)] {
                for cfg in 0..NCFG {
                    for d in 0..12usize {
                        let day = FIT_DAY0 + d as i64;
                        let mu = if (rule, sign) == (a, 1) && w_a_plus.contains(&cfg) {
                            40.0
                        } else if (rule, sign) == (a, -1) && cfg == w_a_minus {
                            25.0
                        } else if (rule, sign) == (b, 1) && w_b_plus.contains(&cfg) {
                            30.0
                        } else {
                            -10.0 + (cfg % 5) as f64
                        };
                        lines.push(xrow(rule, sign, cfg, day, 202_503, 10, mu + wiggle(d)));
                    }
                }
            }
        },
        &grid_text,
    );
    let pj = params_file(dir.path());
    let kf = rules_file(dir.path(), &[a, b]);
    let (o1, o4) = (dir.path().join("o1"), dir.path().join("o4"));
    verdict(vargs(VStage::ExitsFit, &inp, &pj, &o1, &kf, 1)).unwrap();
    verdict(vargs(VStage::ExitsFit, &inp, &pj, &o4, &kf, 4)).unwrap();
    for f in [
        "exits-k.json",
        "exits-verdict-fit.csv",
        "exits-journal-line.txt",
    ] {
        assert_eq!(
            fs::read(o1.join(f)).unwrap(),
            fs::read(o4.join(f)).unwrap(),
            "{f}: зависит от числа потоков"
        );
    }
    let k: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(o1.join("exits-k.json")).unwrap()).unwrap();
    let pick = |rule: (u8, u8, u8), sign: i64| -> u64 {
        let v: Vec<&serde_json::Value> = k
            .as_array()
            .unwrap()
            .iter()
            .filter(|e| {
                e["fam"] == rule.0 && e["wi"] == rule.1 && e["ti"] == rule.2 && e["sign"] == sign
            })
            .collect();
        assert_eq!(v.len(), 1, "{rule:?} {sign}");
        v[0]["cfg"].as_u64().unwrap()
    };
    assert_eq!(pick(a, 1) as usize, tp(300, Some(100), 360));
    assert_eq!(pick(a, -1) as usize, w_a_minus);
    assert_eq!(pick(b, 1) as usize, tp(200, Some(200), 1440));
    // пара (B, −1) без данных в выбор не идёт
    assert_eq!(k.as_array().unwrap().len(), 3);
    // таблица: все ячейки, ровно одна выбранная на пару
    let rows = csv_rows(&o1.join("exits-verdict-fit.csv"));
    assert_eq!(rows.len(), 2 * 2 * NCFG);
    assert_eq!(rows.iter().filter(|r| r["chosen"] == "1").count(), 3);
    let win = rows
        .iter()
        .find(|r| r["fam"] == "0" && r["sign"] == "1" && r["cfg"] == pick(a, 1).to_string())
        .unwrap();
    assert_eq!(win["family"], "tp");
    assert!(win["net_lo"].parse::<f64>().unwrap() > 20.0);
    let line = fs::read_to_string(o1.join("exits-journal-line.txt")).unwrap();
    assert!(
        line.contains("П-35") && line.contains("без данных 1"),
        "{line}"
    );
}

#[test]
fn fit_rejects_test_months_grid_mismatch_and_missing_k() {
    let dir = tempfile::tempdir().unwrap();
    let a = (0u8, 0u8, 0u8);
    let kf = rules_file(dir.path(), &[a]);
    let pj = params_file(dir.path());
    let good = |lines: &mut Vec<String>| {
        for cfg in 0..NCFG {
            lines.push(xrow(a, 1, cfg, FIT_DAY0, 202_503, 5, 10.0));
        }
    };
    // месяц теста в данных фита — отказ (защита от утечки теста в выбор)
    let leak = write_exits_input(
        dir.path(),
        "fit",
        |l| {
            good(l);
            l.push(xrow(a, 1, 0, TEST_DAY0, 202_601, 5, 10.0));
        },
        &grid_csv(),
    );
    let e = verdict(vargs(
        VStage::ExitsFit,
        &leak,
        &pj,
        &dir.path().join("o"),
        &kf,
        1,
    ))
    .unwrap_err();
    assert!(format!("{e:#}").contains("вне split.fit"), "{e:#}");
    // сетка файла не совпала с сеткой кода
    let bad_grid = grid_csv().replace("0,base,,,,360", "0,base,,,,720");
    let g = write_exits_input(dir.path(), "fit", good, &bad_grid);
    let e = verdict(vargs(
        VStage::ExitsFit,
        &g,
        &pj,
        &dir.path().join("o"),
        &kf,
        1,
    ))
    .unwrap_err();
    assert!(format!("{e:#}").contains("сетка"), "{e:#}");
    // без --k
    let ok = write_exits_input(dir.path(), "fit", good, &grid_csv());
    let mut no_k = vargs(VStage::ExitsFit, &ok, &pj, &dir.path().join("o"), &kf, 1);
    no_k.k = None;
    assert!(verdict(no_k).is_err());
    // причины выхода не дают n — отказ
    let broken = write_exits_input(
        dir.path(),
        "fit",
        |l| l.push(xrow(a, 1, 0, FIT_DAY0, 202_503, 5, 10.0).replacen(",5,", ",6,", 1)),
        &grid_csv(),
    );
    assert!(verdict(vargs(
        VStage::ExitsFit,
        &broken,
        &pj,
        &dir.path().join("o"),
        &kf,
        1
    ))
    .is_err());
}

/// exits-k.json из списка (правило, сторона, cfg).
fn exits_k_file(dir: &Path, pairs: &[((u8, u8, u8), i8, usize)]) -> PathBuf {
    let g = grid();
    let items: Vec<serde_json::Value> = pairs
        .iter()
        .map(|(r, sign, cfg)| {
            serde_json::json!({
                "fam": r.0, "wi": r.1, "ti": r.2, "sign": sign, "cfg": cfg,
                "exit_family": g[*cfg].family.name(), "cap": g[*cfg].cap()
            })
        })
        .collect();
    let p = dir.join("exits-k.json");
    fs::write(&p, serde_json::to_string(&items).unwrap()).unwrap();
    p
}

/// Тест-таблица: пара P1 — устойчивый плюс во всех 10 месяцах; P2 — плюс в 5 месяцах из 10, остальные минус;
/// P3 — минус. Остальные конфигурации — минус.
fn gen_test_rows(lines: &mut Vec<String>, c1: usize, c2: usize, c3: usize) {
    let p1 = ((0u8, 0u8, 0u8), 1i8);
    let p2 = ((0u8, 0u8, 0u8), -1i8);
    let p3 = ((1u8, 0u8, 0u8), 1i8);
    for (rule, sign) in [p1, p2, p3] {
        for cfg in 0..NCFG {
            for m in 0..10usize {
                for d in 0..3usize {
                    let day = TEST_DAY0 + (m * 31 + d) as i64;
                    let month = 202_601 + m as i32;
                    let mu = if (rule, sign) == p1 && cfg == c1 {
                        30.0
                    } else if (rule, sign) == p2 && cfg == c2 {
                        if m < 5 {
                            70.0
                        } else {
                            -15.0
                        }
                    } else if (rule, sign) == p3 && cfg == c3 {
                        -20.0
                    } else {
                        -12.0 + (cfg % 4) as f64
                    };
                    lines.push(xrow(rule, sign, cfg, day, month, 8, mu + wiggle(m + d)));
                }
            }
        }
    }
}

#[test]
fn test_stage_finds_stable_profit_and_names_what_failed() {
    let dir = tempfile::tempdir().unwrap();
    let (c1, c2, c3) = (
        tp(300, Some(100), 360),
        trail(200, Some(300), 1440),
        tp(200, Some(200), 1440),
    );
    let inp = write_exits_input(
        dir.path(),
        "test",
        |l| {
            gen_test_rows(l, c1, c2, c3);
            // строки ПОСЛЕ конца периода теста отбрасываются; чужое правило не используется
            l.push(xrow((0, 0, 0), 1, c1, TEST_DAY0 + 400, 202_702, 8, 500.0));
            l.push(xrow((2, 0, 0), 1, 0, TEST_DAY0, 202_601, 8, 10.0));
        },
        &grid_csv(),
    );
    let pj = params_file(dir.path());
    let kf = exits_k_file(
        dir.path(),
        &[((0, 0, 0), 1, c1), ((0, 0, 0), -1, c2), ((1, 0, 0), 1, c3)],
    );
    let (o1, o4) = (dir.path().join("o1"), dir.path().join("o4"));
    verdict(vargs(VStage::ExitsTest, &inp, &pj, &o1, &kf, 1)).unwrap();
    verdict(vargs(VStage::ExitsTest, &inp, &pj, &o4, &kf, 4)).unwrap();
    for f in [
        "exits-verdict-test.csv",
        "exits-verdict-test-grid.csv",
        "report-exits-tables.md",
    ] {
        assert_eq!(
            fs::read(o1.join(f)).unwrap(),
            fs::read(o4.join(f)).unwrap(),
            "{f}: зависит от числа потоков"
        );
    }
    let rows = csv_rows(&o1.join("exits-verdict-test.csv"));
    assert_eq!(rows.len(), 3);
    let (r1, r2, r3) = (
        by_pair(&rows, "0", "1"),
        by_pair(&rows, "0", "-1"),
        by_pair(&rows, "1", "1"),
    );
    // P1: найден, net ≈ 30 − издержки, все условия
    let fee = 2.0 * crate::lob::costs::leg_fee_bps(true);
    assert_eq!(r1["found"], "1", "{r1:?}");
    approx(
        r1["net_main"].parse().unwrap(),
        30.0 + wiggle_mean() - fee,
        0.5,
    );
    assert!(r1["net_lo"].parse::<f64>().unwrap() > 20.0);
    assert!(r1["p_holm"].parse::<f64>().unwrap() < 0.05);
    assert_eq!(
        (r1["months_pos"].as_str(), r1["months_total"].as_str()),
        ("10", "10")
    );
    approx(
        r1["breakeven_slip"].parse().unwrap(),
        r1["net_main"].parse().unwrap(),
        1e-9,
    );
    assert_eq!(r1["cap"], "360");
    assert_eq!(r1["share_cap"], "1.000000");
    // P2: средний net > 0 и интервал выше нуля, но только 5 месяцев из 10 — «месяцы» не сошлись
    assert_eq!(r2["found"], "0", "{r2:?}");
    assert!(r2["net_main"].parse::<f64>().unwrap() > 0.0);
    assert_eq!(
        (r2["months_pos"].as_str(), r2["months_total"].as_str()),
        ("5", "10")
    );
    assert!(
        r2["fail"].contains("net > 0 менее чем в 2/3 месяцев"),
        "{}",
        r2["fail"]
    );
    // P3: убыточная пара
    assert_eq!(r3["found"], "0");
    assert!(
        r3["fail"].contains("нижняя граница net <= 0"),
        "{}",
        r3["fail"]
    );
    // описательная таблица: все 84 конфигурации по каждой из трёх пар, ровно по одной «frozen»
    let grid_rows = csv_rows(&o1.join("exits-verdict-test-grid.csv"));
    assert_eq!(grid_rows.len(), 3 * NCFG);
    assert_eq!(grid_rows.iter().filter(|r| r["frozen"] == "1").count(), 3);
    // отчёт: итог по сторонам
    let rep = fs::read_to_string(o1.join("report-exits-tables.md")).unwrap();
    assert!(
        rep.contains("Пампы (sign +1): выход в прибыль найден"),
        "{rep}"
    );
    assert!(
        rep.contains("Дампы (sign −1): выход в прибыль не доказан"),
        "{rep}"
    );
    assert!(rep.contains("вне периода теста отброшено: 1"), "{rep}");
}

fn by_pair<'a>(rows: &'a [Row], fam: &str, sign: &str) -> &'a Row {
    rows.iter()
        .find(|r| r["fam"] == fam && r["sign"] == sign)
        .unwrap()
}

fn wiggle_mean() -> f64 {
    // среднее колебания по тем же (m + d) для m = 0..10, d = 0..3
    let mut s = 0.0;
    for m in 0..10usize {
        for d in 0..3usize {
            s += wiggle(m + d);
        }
    }
    s / 30.0
}

#[test]
fn test_stage_input_errors() {
    let dir = tempfile::tempdir().unwrap();
    let c1 = tp(300, Some(100), 360);
    let pj = params_file(dir.path());
    let ok_rows = |l: &mut Vec<String>| {
        for cfg in 0..NCFG {
            l.push(xrow((0, 0, 0), 1, cfg, TEST_DAY0, 202_601, 5, 10.0));
        }
    };
    let inp = write_exits_input(dir.path(), "test", ok_rows, &grid_csv());
    let o = dir.path().join("o");
    // замороженная конфигурация не совпадает с сеткой кода
    let g = grid();
    let wrong = dir.path().join("wrong-k.json");
    fs::write(
        &wrong,
        serde_json::to_string(&[serde_json::json!({
            "fam": 0, "wi": 0, "ti": 0, "sign": 1, "cfg": c1, "exit_family": "base", "cap": g[c1].cap()
        })])
        .unwrap(),
    )
    .unwrap();
    assert!(verdict(vargs(VStage::ExitsTest, &inp, &pj, &o, &wrong, 1)).is_err());
    // месяц раньше начала теста — отказ
    let early = write_exits_input(
        dir.path(),
        "test",
        |l| {
            ok_rows(l);
            l.push(xrow((0, 0, 0), 1, 0, FIT_DAY0, 202_503, 5, 10.0));
        },
        &grid_csv(),
    );
    let kf = exits_k_file(dir.path(), &[((0, 0, 0), 1, c1)]);
    let e = verdict(vargs(VStage::ExitsTest, &early, &pj, &o, &kf, 1)).unwrap_err();
    assert!(format!("{e:#}").contains("вне split.test"), "{e:#}");
    // повтор пары и повтор строки
    let dup = dir.path().join("dup-k.json");
    let one = serde_json::json!({
        "fam": 0, "wi": 0, "ti": 0, "sign": 1, "cfg": c1,
        "exit_family": g[c1].family.name(), "cap": g[c1].cap()
    });
    fs::write(&dup, serde_json::to_string(&[one.clone(), one]).unwrap()).unwrap();
    assert!(verdict(vargs(VStage::ExitsTest, &inp, &pj, &o, &dup, 1)).is_err());
    let twice = write_exits_input(
        dir.path(),
        "test",
        |l| {
            ok_rows(l);
            l.push(xrow((0, 0, 0), 1, 0, TEST_DAY0, 202_601, 5, 10.0));
        },
        &grid_csv(),
    );
    assert!(verdict(vargs(VStage::ExitsTest, &twice, &pj, &o, &kf, 1)).is_err());
}

#[test]
fn funding_window_follows_trade_rs_bounds() {
    let rates = [(1, 1.0), (5, 2.0), (9, 3.0), (12, 4.0)];
    let f = Funding::new(&rates);
    // (after, upto]: выплата в `after` не входит, в `upto` входит
    assert_eq!(f.window(1, 9), &[(5, 2.0), (9, 3.0)]);
    assert_eq!(f.window(1, 8), &[(5, 2.0)]);
    assert_eq!(f.window(9, 9), &[]);
    assert_eq!(f.window(0, 100), &rates[..]);
    assert_eq!(f.window(20, 30), &[]);
}
