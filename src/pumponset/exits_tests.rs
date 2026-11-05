//! Тесты выходов П-35 (`exits`, `verdict --stage exits-fit|exits-test`): ручные пути цен с известным ответом
//! (тейк, стоп, трейлинг, предел, задержка исполнения, ничья стоп/трейлинг, потолок и его минута, фандинг в удержании),
//! быстрое == наивное на случайных путях, отсутствие утечки, совпадение набора сделок с `daycells`, детерминизм по
//! числу потоков, вердикт на синтетике с заведомым победителем. Семейства в единицах ATR-прокси (C `tpa`, D `tra`):
//! ручные пути при заданной A (в том числе на границе порога), A не определена, устойчивость нумерации первых 84
//! конфигураций, совпадение с семействами в bps при A = 100, лучшая в семействе (fixed против atr) в вердикте.

use super::agg::{fix, unfix, Dims, Layout};
use super::exits::{
    atr_proxy_bps, exits_head, grid, grid_csv, scan, scan_a, Cfg, ExitAgg, Fac, FacA, Family, Px,
    CAPS, NCAP, NCFG, NCFG_FIXED, SLA_MULT, SL_LEVELS, TPA_MULT, TP_LEVELS, TRAIL_LEVELS, TRA_MULT,
};
use super::play::PlayArgs;
use super::run::{day_of_min, run, RunArgs, Stage};
use super::run_tests::{
    lnp, params_json, pump_contract, put, rules_file, table, xd_file, Row, SEED, T0, XD,
};
use super::trade::{round_trip, Funding};
use super::types::{Cube, Rule, NF};
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
fn grid_has_140_configs_and_first_84_are_stable() {
    let g = grid();
    assert_eq!(g.len(), NCFG);
    assert_eq!((NCFG, NCFG_FIXED), (140, 84));
    let count = |f: Family| g.iter().filter(|c| c.family == f).count();
    assert_eq!(
        (
            count(Family::Base),
            count(Family::Tp),
            count(Family::Trail),
            count(Family::TpA),
            count(Family::TrA)
        ),
        (2, 50, 32, 32, 24)
    );
    // базовая линия: cap = 360 («выход через 6 ч» П-34) и 1440
    assert_eq!((g[0].cap(), g[1].cap()), (360, 1440));
    assert!(g[..2].iter().all(|c| c.family == Family::Base));
    let csv = grid_csv();
    let lines: Vec<&str> = csv.lines().collect();
    assert_eq!(lines.len(), NCFG + 1);
    // новая колонка unit — последняя, у прежних строк — bps
    assert_eq!(lines[0], "cfg,family,G,S,R,cap,unit");
    assert_eq!(lines[1], "0,base,,,,360,bps");
    assert_eq!(lines[2], "1,base,,,,1440,bps");
    // семейство A: циклы G → S → cap
    assert_eq!(lines[3], "2,tp,100,100,,360,bps");
    assert_eq!(lines[4], "3,tp,100,100,,1440,bps");
    assert_eq!(lines[3 + 8], "10,tp,100,none,,360,bps");
    assert_eq!(lines[3 + 9], "11,tp,100,none,,1440,bps");
    assert_eq!(lines[3 + 10], "12,tp,200,100,,360,bps");
    assert_eq!(lines[3 + 49], "51,tp,1000,none,,1440,bps");
    // семейство B: циклы R → S (100, 300, 500, нет) → cap
    assert_eq!(lines[3 + 50], "52,trail,,100,100,360,bps");
    assert_eq!(lines[3 + 52], "54,trail,,300,100,360,bps");
    assert_eq!(lines[3 + 56], "58,trail,,none,100,360,bps");
    assert_eq!(lines[3 + 81], "83,trail,,none,500,1440,bps");
    // семейство C «tpa»: циклы G·A → S·A (1, 2, 3, нет) → cap, множители с точкой
    assert_eq!(lines[1 + 84], "84,tpa,1.0,1.0,,360,atr");
    assert_eq!(lines[1 + 85], "85,tpa,1.0,1.0,,1440,atr");
    assert_eq!(lines[1 + 90], "90,tpa,1.0,none,,360,atr");
    assert_eq!(lines[1 + 92], "92,tpa,2.0,1.0,,360,atr");
    assert_eq!(lines[1 + 108], "108,tpa,5.0,1.0,,360,atr");
    assert_eq!(lines[1 + 115], "115,tpa,5.0,none,,1440,atr");
    // семейство D «tra»: циклы R·A → S·A → cap
    assert_eq!(lines[1 + 116], "116,tra,,1.0,1.0,360,atr");
    assert_eq!(lines[1 + 117], "117,tra,,1.0,1.0,1440,atr");
    assert_eq!(lines[1 + 122], "122,tra,,none,1.0,360,atr");
    assert_eq!(lines[1 + 124], "124,tra,,1.0,2.0,360,atr");
    assert_eq!(lines[1 + 139], "139,tra,,none,3.0,1440,atr");
    // стоп семейства B только 100/300/500/нет; 200 нет
    assert!(g
        .iter()
        .filter(|c| c.family == Family::Trail)
        .all(|c| c.s_bps() != Some(200)));
    // параметры в единицах A не просачиваются в bps и обратно
    assert!(g
        .iter()
        .filter(|c| c.family.is_atr())
        .all(|c| c.g_bps().is_none() && c.s_bps().is_none() && c.r_bps().is_none()));
    assert!(g[..NCFG_FIXED]
        .iter()
        .all(|c| c.g_mult().is_none() && c.s_mult().is_none() && c.r_mult().is_none()));
    // уникальность параметров (с семейством и единицей)
    let uniq: std::collections::HashSet<String> = csv
        .lines()
        .skip(1)
        .map(|l| l.split_once(',').unwrap().1.to_string())
        .collect();
    assert_eq!(uniq.len(), NCFG);
}

/// Прежняя сетка из 84 конфигураций, выписанная независимо от кода (до добавления семейств в единицах A): нумерация
/// первых 84 не должна измениться — сверка с текстом файла `exits-grid.csv` до и после (без колонки unit).
fn old_grid_lines() -> Vec<String> {
    let mut v = Vec::new();
    for cap in [360, 1440] {
        v.push(format!("{},base,,,,{cap}", v.len()));
    }
    for g in [100, 200, 300, 500, 1000] {
        for s in ["100", "200", "300", "500", "none"] {
            for cap in [360, 1440] {
                v.push(format!("{},tp,{g},{s},,{cap}", v.len()));
            }
        }
    }
    for r in [100, 200, 300, 500] {
        for s in ["100", "300", "500", "none"] {
            for cap in [360, 1440] {
                v.push(format!("{},trail,,{s},{r},{cap}", v.len()));
            }
        }
    }
    v
}

#[test]
fn grid_numbering_of_old_84_is_unchanged_after_adding_atr_families() {
    let old = old_grid_lines();
    assert_eq!(old.len(), NCFG_FIXED);
    let csv = grid_csv();
    let new_lines: Vec<&str> = csv.lines().skip(1).take(NCFG_FIXED).collect();
    for (k, (o, n)) in old.iter().zip(&new_lines).enumerate() {
        // новая строка = прежняя + ",bps"
        assert_eq!(format!("{o},bps"), *n, "cfg {k}");
    }
    // новые конфигурации — только после прежних и в порядке C, затем D
    let g = grid();
    assert!(g[..NCFG_FIXED].iter().all(|c| !c.family.is_atr()));
    assert!(g[NCFG_FIXED..NCFG_FIXED + 32]
        .iter()
        .all(|c| c.family == Family::TpA));
    assert!(g[NCFG_FIXED + 32..].iter().all(|c| c.family == Family::TrA));
    // внутри семейства — вложенные циклы по возрастанию параметров
    let tpa: Vec<(f64, f64, u32)> = g[NCFG_FIXED..NCFG_FIXED + 32]
        .iter()
        .map(|c| {
            (
                c.g_mult().unwrap(),
                c.s_mult().unwrap_or(f64::INFINITY),
                c.cap(),
            )
        })
        .collect();
    let mut sorted = tpa.clone();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
    assert_eq!(tpa, sorted);
    assert_eq!(TPA_MULT, [1.0, 2.0, 3.0, 5.0]);
    assert_eq!(SLA_MULT, [1.0, 2.0, 3.0]);
    assert_eq!(TRA_MULT, [1.0, 2.0, 3.0]);
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

/// Номер конфигурации семейства C (множители A; `s = None` — стопа нет).
fn tpa(g: f64, s: Option<f64>, cap: u32) -> usize {
    grid()
        .iter()
        .position(|c| {
            c.family == Family::TpA && c.g_mult() == Some(g) && c.s_mult() == s && c.cap() == cap
        })
        .unwrap()
}

/// Номер конфигурации семейства D.
fn tra(r: f64, s: Option<f64>, cap: u32) -> usize {
    grid()
        .iter()
        .position(|c| {
            c.family == Family::TrA && c.r_mult() == Some(r) && c.s_mult() == s && c.cap() == cap
        })
        .unwrap()
}

/// A по умолчанию в тестах семейств A и B: определена, чтобы утечка и наивное сравнение охватывали и C, D.
const A_DEF: f64 = 30.0;

/// Одна сделка в пустой накопитель (1 правило, 1 сутки), A сделки — `A_DEF`.
fn one(cf: &[f64], rates: &[(i64, f64)], sign: i8) -> ExitAgg {
    one_a(cf, rates, sign, A_DEF)
}

/// То же с заданной A (bps; `NaN` — не определена).
fn one_a(cf: &[f64], rates: &[(i64, f64)], sign: i8, a: f64) -> ExitAgg {
    let mut ex = ExitAgg::new(1, 1);
    let px = Px {
        cf,
        first_min: 0,
        funding: Funding::new(rates),
    };
    let g = ex.group(0, usize::from(sign < 0), 0);
    ex.add_trade(g, &px, 0, sign, a);
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

/// Наивная реализация П-35 «в лоб»: пошагово, `ln` на каждой минуте, один конфиг за раз. Пороги семейств C и D —
/// `множитель·a` bps (`a` — A сделки). Возвращает `(причина, удержание, gross bps, fund bps)`.
fn naive(cf: &[f64], sign: i8, cfg: &Cfg, rates: &[(i64, f64)], a: f64) -> (Why, u32, f64, f64) {
    let (b, s, c_in) = (1usize, f64::from(sign), cf[1]);
    let cap_j = cfg.cap() as usize - 1;
    // пороги в bps: тейк, стоп, трейлинг
    let (g_thr, s_thr, r_thr) = if cfg.family.is_atr() {
        (
            cfg.g_mult().map(|m| m * a),
            cfg.s_mult().map(|m| m * a),
            cfg.r_mult().map(|m| m * a),
        )
    } else {
        (
            cfg.g_bps().map(f64::from),
            cfg.s_bps().map(f64::from),
            cfg.r_bps().map(f64::from),
        )
    };
    let mut pmax = f64::NEG_INFINITY;
    let (mut why, mut j_exec) = (Why::Cap, cap_j);
    for j in 1..cap_j {
        let pnl = s * (cf[b + j] / c_in).ln() * BPS;
        pmax = pmax.max(pnl);
        let sl = s_thr.is_some_and(|x| pnl <= -x);
        let tp = g_thr.is_some_and(|x| pnl >= x);
        let tr = r_thr.is_some_and(|r| pmax >= r && pnl <= pmax - r);
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

fn assert_matches_naive(cf: &[f64], rates: &[(i64, f64)], sign: i8, a: f64, tag: &str) {
    let ex = one_a(cf, rates, sign, a);
    let g = ex.group(0, usize::from(sign < 0), 0);
    for (k, cfg) in grid().iter().enumerate() {
        if cfg.family.is_atr() && !(a.is_finite() && a > 0.0) {
            // A не определена: семейств C и D нет
            assert_eq!(*ex.cfg_cell(g, k), Default::default(), "{tag} cfg {k}");
            continue;
        }
        let (why, hold, gross, fund) = naive(cf, sign, cfg, rates, a);
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
        // A сопоставима с ходом пути (σ минутных доходностей ≈ 0,58·step; за час ×√60), чтобы пороги задевались;
        // каждая шестая сделка — без A
        let a = [0.7 * step, 4.5 * step, 11.0 * step][(seed % 3) as usize];
        let a = if seed % 6 == 0 { f64::NAN } else { a };
        for sign in [1i8, -1] {
            let cf = rw(seed * 7919, sign, 1500, step);
            assert_matches_naive(&cf, &rates, sign, a, &format!("seed {seed} sign {sign}"));
            let ex = one_a(&cf, &rates, sign, a);
            let g = ex.group(0, usize::from(sign < 0), 0);
            for k in 0..NCFG {
                let c = ex.cfg_cell(g, k);
                let (tp_k, sl_k, tr_k) = if k < NCFG_FIXED {
                    ("tp", "sl", "trail")
                } else {
                    ("atr_tp", "atr_sl", "atr_trail")
                };
                *reasons.entry(tp_k).or_default() += c.n_tp as usize;
                *reasons.entry(sl_k).or_default() += c.n_sl as usize;
                *reasons.entry(tr_k).or_default() += c.n_trail as usize;
            }
        }
    }
    // случайные пути задевают все причины выхода (иначе сравнение было бы вырожденным)
    for why in ["tp", "sl", "trail", "atr_tp", "atr_sl", "atr_trail"] {
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
        if cfg < NCFG_FIXED {
            assert_eq!(r["n"], d["nret"], "{k} cfg {cfg}");
        } else {
            // семейства C и D считаются только по сделкам с определённой A
            let (n, nret): (u64, u64) = (r["n"].parse().unwrap(), d["nret"].parse().unwrap());
            assert!(n <= nret, "{k} cfg {cfg}: n {n} больше nret {nret}");
        }
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
    assert_eq!(meta["exits"]["configs"], 140);
    assert_eq!(meta["exits"]["configs_bps"], 84);
    assert_eq!(meta["exits"]["configs_atr"], 56);
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

// ---------- семейства в единицах ATR-прокси (C `tpa`, D `tra`) ----------

/// Фандинг без выплат, покрывающий окна всех тестов с ручными путями.
const RATES0: [(i64, f64); 2] = [(-1000, 0.0), (5000, 0.0)];

/// Как `path`, но вход на минуте `t_in`: `cf[0..=t_in] = 100`, дальше `cf[t_in + j] = 100·exp(s·pnl(j)/1e4)`.
fn path_from(sign: i8, t_in: usize, n: usize, pnl: impl Fn(usize) -> f64) -> Vec<f64> {
    let s = f64::from(sign);
    let mut cf = vec![100.0; t_in + 1];
    for j in 1..=n {
        cf.push(100.0 * (s * pnl(j) / BPS).exp());
    }
    cf
}

#[test]
fn atr_proxy_is_sigma_times_sqrt60_in_bps() {
    let s = 0.0125f32;
    assert_eq!(atr_proxy_bps(s), f64::from(s) * 60f64.sqrt() * BPS);
    assert!(atr_proxy_bps(f32::NAN).is_nan());
    // σ = 1 % за минуту ≈ 77,5 bps за час
    approx(atr_proxy_bps(0.01), 774.596_669, 1e-3);
}

#[test]
fn atr_take_profit_threshold_is_multiple_of_a_and_executes_next_minute() {
    // решение на минуте 5 (130), исполнение по минуте 6 (90); затем 400
    let pnl = |j: usize| match j {
        1..=4 => 0.0,
        5 => 130.0,
        6 => 90.0,
        _ => 400.0,
    };
    for sign in [1i8, -1] {
        let cf = path(sign, 1500, pnl);
        // A = 50: пороги 50, 100, 150, 250 → G1 и G2 касаются на минуте 5 (исполнение по 90), G3 и G5 — на минуте 7
        // (400), исполнение на минуте 8; стоп (1A, 3A) недостижим — pnl не бывает отрицательным
        let ex = one_a(&cf, &RATES0, sign, 50.0);
        for (m, gross, hold) in [
            (1.0, 90.0, 6),
            (2.0, 90.0, 6),
            (3.0, 400.0, 8),
            (5.0, 400.0, 8),
        ] {
            for s in [None, Some(1.0), Some(3.0)] {
                for cap in [360, 1440] {
                    let (g, _, h, why) = res(&ex, sign, tpa(m, s, cap));
                    approx(g, gross, 1e-6);
                    assert_eq!(
                        (h, why),
                        (hold, "tp"),
                        "sign {sign} A50 G{m} S{s:?} cap {cap}"
                    );
                }
            }
        }
        // A = 100: пороги 100, 200, 300, 500 → G1 на минуте 5 (90); G2 и G3 на минуте 7 (400, исполнение на 8);
        // G5 (500) не достигнут — выход по пределу (цена минуты предела = 400)
        let ex = one_a(&cf, &RATES0, sign, 100.0);
        for (m, gross, hold, why) in [
            (1.0, 90.0, 6, "tp"),
            (2.0, 400.0, 8, "tp"),
            (3.0, 400.0, 8, "tp"),
            (5.0, 400.0, 359, "cap"),
        ] {
            let (g, _, h, w) = res(&ex, sign, tpa(m, None, 360));
            approx(g, gross, 1e-6);
            assert_eq!((h, w), (hold, why), "sign {sign} A100 G{m}");
        }
        let (_, _, h, w) = res(&ex, sign, tpa(5.0, None, 1440));
        assert_eq!((h, w), (1439, "cap"));
        // A = 400: G1 = 400 достигнут ровно (pnl 400 ≥ 400) на минуте 7, остальные — нет
        let ex = one_a(&cf, &RATES0, sign, 400.0);
        let (_, _, h, w) = res(&ex, sign, tpa(1.0, None, 360));
        assert_eq!((h, w), (8, "tp"));
        let (_, _, h, w) = res(&ex, sign, tpa(2.0, None, 360));
        assert_eq!((h, w), (359, "cap"));
    }
}

#[test]
fn atr_stop_and_take_profit_order_by_minute() {
    // минута 3: −110, минута 4: −150, далее +500. A = 50: стоп 1A = 50 коснулся на минуте 3 (исполнение на 4 = −150);
    // стоп 3A = 150 — на минуте 4 (−150 ≤ −150, граница), исполнение на 5 (500); тейк 1A — на минуте 5, раньше стопа не бывает
    let pnl = |j: usize| match j {
        1..=2 => -20.0,
        3 => -110.0,
        4 => -150.0,
        _ => 500.0,
    };
    for sign in [1i8, -1] {
        let cf = path(sign, 1500, pnl);
        let ex = one_a(&cf, &RATES0, sign, 50.0);
        let (g, _, hold, why) = res(&ex, sign, tpa(1.0, Some(1.0), 360));
        approx(g, -150.0, 1e-6);
        assert_eq!((hold, why), (4, "sl"));
        // стоп 2A = 100: на минуте 3 (−110 ≤ −100), исполнение на 4
        let (g, _, hold, why) = res(&ex, sign, tpa(1.0, Some(2.0), 360));
        approx(g, -150.0, 1e-6);
        assert_eq!((hold, why), (4, "sl"));
        // стоп 3A = 150: на минуте 4 ровно (граница, pnl = −150 ≤ −150), исполнение на минуте 5 (500)
        let (g, _, hold, why) = res(&ex, sign, tpa(1.0, Some(3.0), 360));
        approx(g, 500.0, 1e-6);
        assert_eq!((hold, why), (5, "sl"));
        // без стопа тейк 1A коснётся на минуте 5, исполнение на 6
        let (_, _, hold, why) = res(&ex, sign, tpa(1.0, None, 1440));
        assert_eq!((hold, why), (6, "tp"));
    }
    // тейк раньше стопа: +150 на минуте 2, затем −300 на минуте 4; A = 100 → тейк 1A на минуте 2 → TP (исполнение на 3)
    let pnl = |j: usize| match j {
        1 => 0.0,
        2 => 150.0,
        3 => 160.0,
        _ => -300.0,
    };
    let ex = one_a(&path(1, 1500, pnl), &RATES0, 1, 100.0);
    let (g, _, hold, why) = res(&ex, 1, tpa(1.0, Some(1.0), 360));
    approx(g, 160.0, 1e-6);
    assert_eq!((hold, why), (3, "tp"));
}

#[test]
fn atr_trailing_activation_drawdown_and_stop_priority() {
    // минута 3: +150 (максимум), минута 5: −120 — и просадка от максимума, и стоп в одну минуту; исполнение на 6 = −130
    let pnl = |j: usize| match j {
        1..=2 => 0.0,
        3..=4 => 150.0,
        5 => -120.0,
        _ => -130.0,
    };
    for sign in [1i8, -1] {
        let cf = path(sign, 1500, pnl);
        let ex = one_a(&cf, &RATES0, sign, 50.0);
        // стоп 1A = 50 и трейлинг R = 1A в минуту 5: приоритет стопа
        let (g, _, hold, why) = res(&ex, sign, tra(1.0, Some(1.0), 360));
        approx(g, -130.0, 1e-6);
        assert_eq!((hold, why), (6, "sl"));
        // без стопа — трейлинг
        let (g, _, hold, why) = res(&ex, sign, tra(1.0, None, 360));
        approx(g, -130.0, 1e-6);
        assert_eq!((hold, why), (6, "trail"));
        // стоп 3A = 150 не касается (−120 > −150): трейлинг
        let (_, _, hold, why) = res(&ex, sign, tra(2.0, Some(3.0), 360));
        assert_eq!((hold, why), (6, "trail"));
        // R = 3A = 150: активация ровно на 150 (граница), откат до ≤ 0 — минута 5
        let (_, _, hold, why) = res(&ex, sign, tra(3.0, None, 360));
        assert_eq!((hold, why), (6, "trail"));
        // A = 80: R = 2A = 160 не активен (максимум 150): стоп 1A = 80 — минута 5, без стопа — предел (цена −130)
        let ex = one_a(&cf, &RATES0, sign, 80.0);
        let (_, _, hold, why) = res(&ex, sign, tra(2.0, Some(1.0), 360));
        assert_eq!((hold, why), (6, "sl"));
        let (g, _, hold, why) = res(&ex, sign, tra(2.0, None, 360));
        approx(g, -130.0, 1e-6);
        assert_eq!((hold, why), (359, "cap"));
    }
    // откат от максимума без стопа; A = 50: пороги 50, 100, 150. Максимум 400 на минуте 10
    let pnl = |j: usize| match j {
        1..=9 => 60.0,
        10 => 400.0,
        11 => 340.0,
        12 => 299.0,
        13 => 280.0,
        _ => 100.0,
    };
    for sign in [1i8, -1] {
        let ex = one_a(&path(sign, 1500, pnl), &RATES0, sign, 50.0);
        // R = 50: активен с минуты 1; откат ≤ 350 — минута 11 (340), исполнение на 12 (299)
        let (g, _, hold, why) = res(&ex, sign, tra(1.0, None, 360));
        approx(g, 299.0, 1e-6);
        assert_eq!((hold, why), (12, "trail"));
        // R = 100: откат ≤ 300 — минута 12 (299), исполнение на 13 (280)
        let (g, _, hold, why) = res(&ex, sign, tra(2.0, None, 360));
        approx(g, 280.0, 1e-6);
        assert_eq!((hold, why), (13, "trail"));
        // R = 150: откат ≤ 250 — минута 14 (100), исполнение на 15 (100)
        let (g, _, hold, why) = res(&ex, sign, tra(3.0, None, 1440));
        approx(g, 100.0, 1e-6);
        assert_eq!((hold, why), (15, "trail"));
    }
}

#[test]
fn atr_threshold_on_the_boundary_is_inclusive() {
    // A = 37,5 (точно представима): тейк 2A = 75, стоп 1A = 37,5
    let a = 37.5;
    for sign in [1i8, -1] {
        let tp_path = |p5: f64| {
            path(sign, 1500, move |j| match j {
                5 => p5,
                6 => 10.0,
                7 => 100.0,
                8 => 60.0,
                _ => 0.0,
            })
        };
        // pnl ровно 2A на минуте 5 — касание, исполнение по минуте 6 (10)
        let ex = one_a(&tp_path(75.0), &RATES0, sign, a);
        let (g, _, hold, why) = res(&ex, sign, tpa(2.0, None, 360));
        approx(g, 10.0, 1e-6);
        assert_eq!((hold, why), (6, "tp"), "sign {sign}");
        // чуть ниже порога — минута 5 не в счёт, касание на минуте 7 (100), исполнение на 8 (60)
        let ex = one_a(&tp_path(74.999), &RATES0, sign, a);
        let (g, _, hold, why) = res(&ex, sign, tpa(2.0, None, 360));
        approx(g, 60.0, 1e-6);
        assert_eq!((hold, why), (8, "tp"), "sign {sign}");
        // тейк 1A = 37,5 при 74,999 касается уже на минуте 5
        let (_, _, hold, _) = res(&ex, sign, tpa(1.0, None, 360));
        assert_eq!(hold, 6);

        let sl_path = |p5: f64| {
            path(sign, 1500, move |j| match j {
                5 => p5,
                6 => -5.0,
                _ => 0.0,
            })
        };
        // pnl ровно −1A: стоп коснулся на минуте 5, исполнение по минуте 6 (−5)
        let ex = one_a(&sl_path(-37.5), &RATES0, sign, a);
        let (g, _, hold, why) = res(&ex, sign, tpa(5.0, Some(1.0), 360));
        approx(g, -5.0, 1e-6);
        assert_eq!((hold, why), (6, "sl"), "sign {sign}");
        // чуть выше — стопа нет: предел
        let ex = one_a(&sl_path(-37.4), &RATES0, sign, a);
        let (_, _, hold, why) = res(&ex, sign, tpa(5.0, Some(1.0), 360));
        assert_eq!((hold, why), (359, "cap"), "sign {sign}");
    }
}

#[test]
fn undefined_a_excludes_trade_from_atr_families_only() {
    let rates = rates_var();
    for sign in [1i8, -1] {
        let cf = rw(77, sign, 1500, 6.0);
        let base = one_a(&cf, &rates, sign, 40.0);
        let g = base.group(0, usize::from(sign < 0), 0);
        assert_eq!(base.cnt.atr_undefined, 0);
        assert_eq!(base.cnt.atr_undefined_by_cap, [0, 0]);
        for ci in 0..NCAP {
            // A определена: n семейств C и D совпадает с n остальных
            assert_eq!(base.cap_cell(g, ci), base.cap_cell_atr(g, ci));
        }
        assert!((NCFG_FIXED..NCFG).any(|k| *base.cfg_cell(g, k) != Default::default()));
        for a in [f64::NAN, 0.0, -3.0, f64::INFINITY, f64::NEG_INFINITY] {
            let ex = one_a(&cf, &rates, sign, a);
            // прежние 84 конфигурации и их n — как при определённой A
            for k in 0..NCFG_FIXED {
                assert_eq!(ex.cfg_cell(g, k), base.cfg_cell(g, k), "A = {a} cfg {k}");
            }
            for ci in 0..NCAP {
                assert_eq!(ex.cap_cell(g, ci), base.cap_cell(g, ci), "A = {a}");
                // а у семейств C и D сделки нет
                assert_eq!(*ex.cap_cell_atr(g, ci), Default::default(), "A = {a}");
            }
            for k in NCFG_FIXED..NCFG {
                assert_eq!(*ex.cfg_cell(g, k), Default::default(), "A = {a} cfg {k}");
            }
            // сделку посчитали, и исключение записано по обоим cap
            assert_eq!((ex.cnt.triggers, ex.cnt.atr_undefined), (1, 1), "A = {a}");
            assert_eq!(ex.cnt.atr_undefined_by_cap, [1, 1], "A = {a}");
        }
    }
    // короткий ряд: достижим только cap 360 — исключение считается по cap, который сделка взяла
    let cf = path(1, 400, |j| if j == 300 { 250.0 } else { 0.0 });
    let ex = one_a(&cf, &rates_wide(), 1, f64::NAN);
    assert_eq!(ex.cnt.atr_undefined, 1);
    assert_eq!(ex.cnt.atr_undefined_by_cap, [1, 0]);
    // сделки без цен/фандинга не считаются и как «без A»: их в симуляцию не взяли
    let ex = one_a(&path(1, 1500, |_| 10.0), &[(5, 1.0e-4)], 1, f64::NAN);
    assert_eq!(
        (ex.cnt.atr_undefined, ex.cnt.atr_undefined_by_cap),
        (0, [0, 0])
    );
}

#[test]
fn atr_families_equal_bps_families_when_a_is_100() {
    // при A = 100 порог m·A совпадает с m·100 bps: одинаковые аргументы `exp` → одинаковые первые касания, значит
    // семейства C и D обязаны дать побитово те же ячейки, что A и B с теми же значениями
    let rates = rates_var();
    let tp_grid = [(1.0, 100u32), (2.0, 200), (3.0, 300), (5.0, 500)];
    let sl_a = [
        (Some(1.0), Some(100u32)),
        (Some(2.0), Some(200)),
        (Some(3.0), Some(300)),
        (None, None),
    ];
    let sl_b = [
        (Some(1.0), Some(100u32)),
        (Some(3.0), Some(300)),
        (None, None),
    ];
    let (mut compared, mut events) = (0usize, 0usize);
    for seed in 1..=30u64 {
        let step = [3.0, 9.0, 22.0][(seed % 3) as usize];
        for sign in [1i8, -1] {
            let cf = rw(seed * 2_654_435_761, sign, 1500, step);
            let ex = one_a(&cf, &rates, sign, 100.0);
            let g = ex.group(0, usize::from(sign < 0), 0);
            for cap in CAPS {
                for &(gm, gb) in &tp_grid {
                    for &(sm, sb) in &sl_a {
                        let (c, f) = (tpa(gm, sm, cap), tp(gb, sb, cap));
                        assert_eq!(
                            ex.cfg_cell(g, c),
                            ex.cfg_cell(g, f),
                            "seed {seed} sign {sign} tpa {c} / tp {f}"
                        );
                        let cell = ex.cfg_cell(g, c);
                        events += (cell.n_tp + cell.n_sl) as usize;
                        compared += 1;
                    }
                }
                for &(rm, rb) in &tp_grid[..3] {
                    for &(sm, sb) in &sl_b {
                        let (c, f) = (tra(rm, sm, cap), trail(rb, sb, cap));
                        assert_eq!(
                            ex.cfg_cell(g, c),
                            ex.cfg_cell(g, f),
                            "seed {seed} sign {sign} tra {c} / trail {f}"
                        );
                        let cell = ex.cfg_cell(g, c);
                        events += (cell.n_trail + cell.n_sl) as usize;
                        compared += 1;
                    }
                }
            }
        }
    }
    assert_eq!(compared, 30 * 2 * 2 * (16 + 9));
    assert!(events > 300, "сравнение вырождено: событий {events}");
}

#[test]
fn scan_with_atr_does_not_change_bps_part_and_orders_levels() {
    let fac = [Fac::new(1.0), Fac::new(-1.0)];
    for seed in 1..=15u64 {
        for sign in [1i8, -1] {
            let cf = rw(seed * 99_991, sign, 1500, 8.0);
            let f = &fac[usize::from(sign < 0)];
            let base = scan(&cf, 1, 1439, sign, f);
            let fa = FacA::new(f64::from(sign), 40.0).unwrap();
            let both = scan_a(&cf, 1, 1439, sign, f, Some(&fa));
            let none = scan_a(&cf, 1, 1439, sign, f, None);
            for sc in [&both, &none] {
                assert_eq!(sc.tp, base.tp);
                assert_eq!(sc.sl, base.sl);
                assert_eq!(sc.tr, base.tr);
                assert_eq!(sc.mfe_cap, base.mfe_cap);
                assert_eq!(sc.mfe, base.mfe);
            }
            // без A указателей C и D нет; с A — касаются, и более высокий порог не раньше низкого
            assert_eq!(none.tpa, [u32::MAX; 4]);
            assert_eq!(none.sla, [u32::MAX; 3]);
            assert_eq!(none.tra, [u32::MAX; 3]);
            assert!(both.tpa.iter().chain(&both.sla).any(|&t| t != u32::MAX));
            assert!(both.tpa.windows(2).all(|w| w[0] <= w[1]), "{:?}", both.tpa);
            assert!(both.sla.windows(2).all(|w| w[0] <= w[1]), "{:?}", both.sla);
        }
    }
    for a in [f64::NAN, 0.0, -1.0, f64::INFINITY] {
        assert!(FacA::new(1.0, a).is_none(), "A = {a}");
    }
    // слишком большая A: порог не представим — A считается не определённой
    assert!(FacA::new(1.0, 1.0e9).is_none());
}

/// Прежний проход (до семейств в единицах A), дословно: указатели по каждому уровню, сравнение на каждой минуте.
/// Эталон «байт в байт» для части в bps: новый проход обязан дать те же первые касания и потолки.
type PrevScan = ([u32; 5], [u32; 4], [u32; 4], [(u64, u32); 2], (u64, u32));

fn scan_prev(cf: &[f64], b: usize, jmax: usize, sign: i8) -> PrevScan {
    let s = f64::from(sign);
    let e = |lvl: u32, k: f64| (k * f64::from(lvl) / BPS).exp();
    let (ftp, fsl, ftr) = (
        TP_LEVELS.map(|l| e(l, s)),
        SL_LEVELS.map(|l| e(l, -s)),
        TRAIL_LEVELS.map(|l| e(l, -s)),
    );
    let v_in = s * cf[b];
    let mut tp_thr = [f64::INFINITY; 5];
    let mut sl_thr = [f64::NEG_INFINITY; 4];
    for (t, f) in tp_thr.iter_mut().zip(&ftp) {
        *t = v_in * f;
    }
    for (t, f) in sl_thr.iter_mut().zip(&fsl) {
        *t = v_in * f;
    }
    let pnl = |v: f64| s * (v / v_in).ln() * BPS;
    let (mut otp, mut osl, mut otr) = ([u32::MAX; 5], [u32::MAX; 4], [u32::MAX; 4]);
    let mut mfe_cap = [(f64::NAN, 0u32); 2];
    let (mut vbest, mut jbest) = (f64::NEG_INFINITY, 0u32);
    let (mut tpn, mut sln, mut trn) = (0usize, 0usize, 0usize);
    let mut j = 1usize;
    for (ci, &cap) in CAPS.iter().enumerate() {
        let end = (cap as usize - 1).min(jmax);
        if j <= end {
            for (d, &c) in cf[b + j..=b + end].iter().enumerate() {
                let jj = (j + d) as u32;
                let v = s * c;
                if v > vbest {
                    vbest = v;
                    jbest = jj;
                }
                while tpn < 5 && v >= tp_thr[tpn] {
                    otp[tpn] = jj;
                    tpn += 1;
                }
                while sln < 4 && v <= sl_thr[sln] {
                    osl[sln] = jj;
                    sln += 1;
                }
                while trn < tpn.min(4) && v <= vbest * ftr[trn] {
                    otr[trn] = jj;
                    trn += 1;
                }
            }
            j = end + 1;
        }
        if jmax + 1 >= cap as usize {
            mfe_cap[ci] = (pnl(vbest), jbest);
        }
    }
    let bits = |x: (f64, u32)| (x.0.to_bits(), x.1);
    (
        otp,
        osl,
        otr,
        [bits(mfe_cap[0]), bits(mfe_cap[1])],
        bits((pnl(vbest), jbest)),
    )
}

#[test]
fn new_scan_equals_previous_scan_for_bps_levels_byte_for_byte() {
    let fac = [Fac::new(1.0), Fac::new(-1.0)];
    let bits = |x: (f64, u32)| (x.0.to_bits(), x.1);
    let mut touched = 0usize;
    for seed in 1..=40u64 {
        let step = [4.0, 9.0, 20.0][(seed % 3) as usize];
        for sign in [1i8, -1] {
            let cf = rw(seed * 48_271, sign, 1500, step);
            let f = &fac[usize::from(sign < 0)];
            // окна разной длины: полный, до cap 360, обрезанные внутри окон
            for jmax in [1439usize, 700, 359, 358, 120, 1] {
                let old = scan_prev(&cf, 1, jmax, sign);
                let fa = FacA::new(f64::from(sign), 25.0);
                for sc in [
                    scan(&cf, 1, jmax, sign, f),
                    scan_a(&cf, 1, jmax, sign, f, fa.as_ref()),
                ] {
                    assert_eq!(sc.tp, old.0, "seed {seed} sign {sign} jmax {jmax}");
                    assert_eq!(sc.sl, old.1, "seed {seed} sign {sign} jmax {jmax}");
                    assert_eq!(sc.tr, old.2, "seed {seed} sign {sign} jmax {jmax}");
                    assert_eq!([bits(sc.mfe_cap[0]), bits(sc.mfe_cap[1])], old.3);
                    assert_eq!(bits(sc.mfe), old.4);
                }
                touched += old
                    .0
                    .iter()
                    .chain(&old.1)
                    .chain(&old.2)
                    .filter(|&&t| t != u32::MAX)
                    .count();
            }
        }
    }
    assert!(touched > 500, "сравнение вырождено: касаний {touched}");
}

/// Куб из одного окна 5 мин на `sigma.len()` слотов от слота 0; знак всех срабатываний — `sign` (`r`).
fn cube_with_sigma(sigma: Vec<f32>, sign: i8) -> Cube {
    let n = sigma.len();
    Cube {
        slot0: 0,
        n,
        windows: vec![5],
        z: vec![0.0; NF * n],
        r: vec![f32::from(sign); n],
        sigma,
    }
}

fn layout_one_rule(day0: i64) -> Layout {
    Layout::new(
        "test",
        Dims {
            nr: 1,
            nd: 1,
            nm: 1,
            ndays: 1,
            nh: 1,
            nw: 1,
            nt: 1,
        },
        vec![Rule {
            fam: 0,
            wi: 0,
            ti: 0,
        }],
        day0,
        vec![202_001],
        vec![202_001],
        vec![90],
    )
}

#[test]
fn add_triggers_takes_a_from_sigma_of_the_trigger_slot() {
    let sg = 0.004f32;
    let a = atr_proxy_bps(sg);
    let t_f = 5i64; // слот 1, вход на минуте 6
    let lay = layout_one_rule(day_of_min(t_f));
    let rates = rates_wide();
    for sign in [1i8, -1] {
        // 1,5A на минуте 3, 1,2A на минуте 4, затем 2,5A
        let cf = path_from(sign, 6, 1500, |j| match j {
            1..=2 => 0.0,
            3 => 1.5 * a,
            4 => 1.2 * a,
            _ => 2.5 * a,
        });
        let px = Px {
            cf: &cf,
            first_min: 0,
            funding: Funding::new(&rates),
        };
        // σ задана только в слоте срабатывания (слот 0 — NaN): A берётся оттуда
        let mut ex = ExitAgg::new(1, 1);
        ex.add_triggers(
            &lay,
            &cube_with_sigma(vec![f32::NAN, sg], sign),
            &[vec![1]],
            &px,
        );
        assert_eq!((ex.cnt.triggers, ex.cnt.atr_undefined), (1, 0));
        // 1A: касание на минуте 3, исполнение на 4 (1,2A); 2A: касание на 5, исполнение на 6 (2,5A); 3A недостижим → предел
        let (g, _, hold, why) = res(&ex, sign, tpa(1.0, None, 360));
        approx(g, 1.2 * a, 1e-6);
        assert_eq!((hold, why), (4, "tp"));
        let (g, _, hold, why) = res(&ex, sign, tpa(2.0, None, 360));
        approx(g, 2.5 * a, 1e-6);
        assert_eq!((hold, why), (6, "tp"));
        let (g, _, hold, why) = res(&ex, sign, tpa(3.0, None, 360));
        approx(g, 2.5 * a, 1e-6);
        assert_eq!((hold, why), (359, "cap"));
        // правило по знаку: сторона сделки — знак `r` окна правила
        let gi = ex.group(0, usize::from(sign < 0), 0);
        assert_eq!(ex.cap_cell_atr(gi, 0).n, 1);

        // σ слота срабатывания не определена, у соседнего слота есть — сделка без A, в C и D не входит
        let mut ex = ExitAgg::new(1, 1);
        ex.add_triggers(
            &lay,
            &cube_with_sigma(vec![sg, f32::NAN], sign),
            &[vec![1]],
            &px,
        );
        assert_eq!((ex.cnt.triggers, ex.cnt.atr_undefined), (1, 1));
        let gi = ex.group(0, usize::from(sign < 0), 0);
        assert_eq!((ex.cap_cell(gi, 0).n, ex.cap_cell_atr(gi, 0).n), (1, 0));
        assert_eq!(*ex.cfg_cell(gi, tpa(1.0, None, 360)), Default::default());
        assert_ne!(*ex.cfg_cell(gi, tp(100, None, 360)), Default::default());
        // слот вне ряда sigma — тоже не определена (без паники)
        let mut cube = cube_with_sigma(vec![sg, sg], sign);
        cube.sigma.truncate(1);
        let mut ex = ExitAgg::new(1, 1);
        ex.add_triggers(&lay, &cube, &[vec![1]], &px);
        assert_eq!((ex.cnt.triggers, ex.cnt.atr_undefined), (1, 1));
    }
}

/// Как `cache_dir`, но цены с мелким детерминированным шумом: σ минутных доходностей > 0, A определена.
fn noisy_cache_dir(dir: &Path) -> PathBuf {
    let cache = dir.join("cache");
    fs::create_dir_all(&cache).unwrap();
    let mut d = pump_contract(17_280, &[5_000, T0]);
    for x in &mut d.close {
        *x = 10_000.0 / *x;
    }
    let mut r = Xs(0x9E37_79B9_7F4A_7C15);
    for (sym, mut c) in [
        ("ABCUSDT", pump_contract(17_280, &[4_000, 9_000, T0])),
        ("DEFUSDT", pump_contract(17_280, &[6_000, 12_000])),
        ("GHIUSDT", pump_contract(17_280, &[T0])),
        ("DMPUSDT", d),
    ] {
        for x in &mut c.close {
            *x *= ((r.unit() - 0.5) * 2.0e-4).exp();
        }
        put(&cache, sym, &c);
    }
    cache
}

#[test]
fn atr_families_in_run_are_deterministic_and_consistent_with_base_rows() {
    let dir = tempfile::tempdir().unwrap();
    let cache = noisy_cache_dir(dir.path());
    let pj = params_360_1440(dir.path());
    let (rf, xf) = (rules_file(dir.path(), &RULES), xd_file(dir.path(), XD));
    let (o1, o4) = (dir.path().join("t1"), dir.path().join("t4"));
    run(args_run(&cache, &pj, &o1, rf.clone(), xf.clone(), 1, true)).unwrap();
    run(args_run(&cache, &pj, &o4, rf, xf, 4, true)).unwrap();
    // побайтно при любом числе потоков
    let a = fs::read(o1.join("exits-test.csv")).unwrap();
    assert_eq!(a, fs::read(o4.join("exits-test.csv")).unwrap());
    assert_eq!(
        fs::read(o1.join("exits-grid.csv")).unwrap(),
        fs::read(o4.join("exits-grid.csv")).unwrap()
    );
    assert_eq!(
        fs::read_to_string(o1.join("exits-grid.csv")).unwrap(),
        grid_csv()
    );

    let ex = table(&o1.join("exits-test.csv"));
    let atr: Vec<&Row> = ex
        .iter()
        .filter(|r| r["cfg"].parse::<usize>().unwrap() >= NCFG_FIXED)
        .collect();
    assert!(
        !atr.is_empty(),
        "A должна быть определена на данных с шумом"
    );
    let cols = ["fam", "wi", "ti", "sign", "day"];
    let base_n: BTreeMap<(String, usize), u64> = ex
        .iter()
        .filter(|r| r["cfg"] == "0" || r["cfg"] == "1")
        .map(|r| {
            (
                (key(r, &cols), r["cfg"].parse().unwrap()),
                r["n"].parse().unwrap(),
            )
        })
        .collect();
    let mut tp_n = 0u64;
    for r in &atr {
        let cfg: usize = r["cfg"].parse().unwrap();
        let c = grid()[cfg];
        let n: u64 = r["n"].parse().unwrap();
        let nb = base_n[&(key(r, &cols), usize::from(c.cap() == 1440))];
        assert!(
            (1..=nb).contains(&n),
            "cfg {cfg}: n {n} при базовой линии {nb}"
        );
        let part = |name: &str| r[name].parse::<u64>().unwrap();
        // причины дают n; у тейка нет трейлинга и наоборот
        assert_eq!(
            part("n_tp") + part("n_sl") + part("n_trail") + part("n_cap"),
            n,
            "cfg {cfg}"
        );
        match c.family {
            Family::TpA => assert_eq!(part("n_trail"), 0),
            Family::TrA => assert_eq!(part("n_tp"), 0),
            other => panic!("не семейство в единицах A: {other:?}"),
        }
        tp_n += part("n_tp");
    }
    // пампы данных задевают тейк в единицах A
    assert!(tp_n > 0, "тейков в семействе C нет: {tp_n}");

    // мета: сделки C и D по cap = сделки cap − исключённые; Σ n строк первой конфигурации C = атр-сделкам cap 360
    let meta: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(o1.join("run-meta.json")).unwrap()).unwrap();
    let m = &meta["exits"];
    assert_eq!(
        (m["configs"].as_u64(), m["configs_atr"].as_u64()),
        (Some(140), Some(56))
    );
    for cap in ["360", "1440"] {
        let (t, u, a) = (
            m["trades_by_cap"][cap].as_u64().unwrap(),
            m["atr_undefined_by_cap"][cap].as_u64().unwrap(),
            m["atr_trades_by_cap"][cap].as_u64().unwrap(),
        );
        assert_eq!(t - u, a, "cap {cap}");
    }
    let n_first: u64 = ex
        .iter()
        .filter(|r| r["cfg"] == NCFG_FIXED.to_string())
        .map(|r| r["n"].parse::<u64>().unwrap())
        .sum();
    assert_eq!(n_first, m["atr_trades_by_cap"]["360"].as_u64().unwrap());
    assert!(m["atr_undefined"].as_u64().unwrap() <= m["triggers"].as_u64().unwrap());
    assert!(m["atr_unit"].as_str().unwrap().contains("sigma_h_bps"));
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
    // `exits-k.json` без `best_by_group` (файл прежней версии): сводка «лучшая в семействе» не строится
    assert!(rep.contains("нет `best_by_group`"), "{rep}");
    assert_eq!(csv_rows(&o1.join("exits-verdict-test-groups.csv")).len(), 0);
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

// ---------- verdict: лучшая в семействе, fixed против atr ----------

fn read_json(path: &Path) -> serde_json::Value {
    serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap()
}

#[test]
fn fit_writes_best_per_family_group_and_unit_aware_k_json() {
    let dir = tempfile::tempdir().unwrap();
    let a = (0u8, 0u8, 0u8);
    // (A, +1): лучшая из всех 140 — в единицах A; лучшая в bps, лучший трейлинг и лучшая tra — каждая своя
    let win_atr = tpa(2.0, Some(1.0), 360);
    let win_fixed = tp(300, Some(100), 1440);
    let win_trail = trail(200, Some(300), 360);
    let win_tra = tra(1.0, None, 1440);
    // (A, −1): конфигурация в bps и в единицах A с одинаковыми данными — ничья, в bps раньше
    let tie_fixed = tp(100, Some(100), 360);
    let tie_atr = tpa(1.0, Some(1.0), 360);
    let inp = write_exits_input(
        dir.path(),
        "fit",
        |lines| {
            for sign in [1i8, -1] {
                for cfg in 0..NCFG {
                    for d in 0..12usize {
                        let mu = if sign == 1 && cfg == win_atr {
                            40.0
                        } else if sign == 1 && cfg == win_fixed {
                            25.0
                        } else if sign == 1 && cfg == win_tra {
                            10.0
                        } else if sign == 1 && cfg == win_trail {
                            5.0
                        } else if sign == -1 && (cfg == tie_fixed || cfg == tie_atr) {
                            30.0
                        } else {
                            -10.0 + (cfg % 5) as f64
                        };
                        let day = FIT_DAY0 + d as i64;
                        lines.push(xrow(a, sign, cfg, day, 202_503, 10, mu + wiggle(d)));
                    }
                }
            }
        },
        &grid_csv(),
    );
    let pj = params_file(dir.path());
    let kf = rules_file(dir.path(), &[a]);
    let out = dir.path().join("out");
    verdict(vargs(VStage::ExitsFit, &inp, &pj, &out, &kf, 2)).unwrap();

    let k = read_json(&out.join("exits-k.json"));
    let arr = k.as_array().unwrap();
    assert_eq!(arr.len(), 2);
    let plus = arr.iter().find(|e| e["sign"] == 1).unwrap();
    let minus = arr.iter().find(|e| e["sign"] == -1).unwrap();
    // замороженная пара — конфигурация в единицах A: множители с точкой, unit = atr, R пуст
    assert_eq!(plus["cfg"].as_u64(), Some(win_atr as u64));
    assert_eq!(plus["exit_family"], "tpa");
    assert_eq!(plus["unit"], "atr");
    assert_eq!(plus["G"].as_f64(), Some(2.0));
    assert_eq!(plus["S"].as_f64(), Some(1.0));
    assert!(plus["R"].is_null());
    assert_eq!(plus["cap"], 360);
    // лучшая в каждой группе — по фиту
    let want = [
        ("fixed", win_fixed),
        ("atr", win_atr),
        ("tp", win_fixed),
        ("trail", win_trail),
        ("tpa", win_atr),
        ("tra", win_tra),
    ];
    for (name, cfg) in want {
        assert_eq!(
            plus["best_by_group"][name].as_u64(),
            Some(cfg as u64),
            "{name}"
        );
    }
    // ничья между bps и A при равных данных и cap: выбрана конфигурация в bps
    assert_eq!(minus["cfg"].as_u64(), Some(tie_fixed as u64));
    assert_eq!(minus["unit"], "bps");
    assert_eq!(minus["G"].as_u64(), Some(100));
    assert_eq!(
        minus["best_by_group"]["fixed"].as_u64(),
        Some(tie_fixed as u64)
    );
    assert_eq!(minus["best_by_group"]["atr"].as_u64(), Some(tie_atr as u64));
    assert_eq!(
        minus["best_by_group"]["tp"].as_u64(),
        Some(tie_fixed as u64)
    );
    assert_eq!(minus["best_by_group"]["tpa"].as_u64(), Some(tie_atr as u64));

    // таблица фита: колонка unit, множители с точкой, 140 ячеек на пару и знак
    let rows = csv_rows(&out.join("exits-verdict-fit.csv"));
    assert_eq!(rows.len(), 2 * NCFG);
    let w = rows
        .iter()
        .find(|r| r["sign"] == "1" && r["cfg"] == win_atr.to_string())
        .unwrap();
    assert_eq!(
        (
            w["family"].as_str(),
            w["unit"].as_str(),
            w["G"].as_str(),
            w["S"].as_str(),
            w["R"].as_str()
        ),
        ("tpa", "atr", "2.0", "1.0", "")
    );
    assert_eq!(w["chosen"], "1");
    let b = rows
        .iter()
        .find(|r| r["sign"] == "1" && r["cfg"] == "2")
        .unwrap();
    assert_eq!(
        (b["family"].as_str(), b["unit"].as_str(), b["G"].as_str()),
        ("tp", "bps", "100")
    );
    // сводка по группам: по 6 строк на пару и знак; chosen = 1 только у групп с замороженной конфигурацией
    let grp = csv_rows(&out.join("exits-verdict-fit-groups.csv"));
    assert_eq!(grp.len(), 12);
    let chosen: Vec<(&str, &str)> = grp
        .iter()
        .filter(|r| r["chosen"] == "1")
        .map(|r| (r["sign"].as_str(), r["group"].as_str()))
        .collect();
    assert_eq!(
        chosen,
        [("1", "atr"), ("1", "tpa"), ("-1", "fixed"), ("-1", "tp")]
    );
    let g_atr = grp
        .iter()
        .find(|r| r["sign"] == "1" && r["group"] == "atr")
        .unwrap();
    assert_eq!(g_atr["cfg"], win_atr.to_string());
    assert!(g_atr["net_lo"].parse::<f64>().unwrap() > 20.0);
    let line = fs::read_to_string(out.join("exits-journal-line.txt")).unwrap();
    assert!(
        line.contains("exits-verdict-fit-groups.csv") && line.contains("140 конфигураций"),
        "{line}"
    );

    // ---- тест: замороженные пары и лучшие в группах оцениваются на тесте, ничего заново не выбирается
    let inp_t = write_exits_input(
        dir.path(),
        "test",
        |lines| {
            for sign in [1i8, -1] {
                for cfg in 0..NCFG {
                    for m in 0..10usize {
                        for d in 0..3usize {
                            let day = TEST_DAY0 + (m * 31 + d) as i64;
                            let mu = if cfg == win_atr || cfg == tie_atr {
                                30.0
                            } else if cfg == win_fixed || cfg == tie_fixed {
                                10.0
                            } else {
                                -12.0 + (cfg % 4) as f64
                            };
                            let month = 202_601 + m as i32;
                            lines.push(xrow(a, sign, cfg, day, month, 8, mu + wiggle(m + d)));
                        }
                    }
                }
            }
        },
        &grid_csv(),
    );
    let out_t = dir.path().join("out-test");
    verdict(vargs(
        VStage::ExitsTest,
        &inp_t,
        &pj,
        &out_t,
        &out.join("exits-k.json"),
        2,
    ))
    .unwrap();
    let tg = csv_rows(&out_t.join("exits-verdict-test-groups.csv"));
    assert_eq!(tg.len(), 12);
    fn cell<'a>(rows: &'a [Row], sign: &str, group: &str) -> &'a Row {
        rows.iter()
            .find(|r| r["sign"] == sign && r["group"] == group)
            .unwrap()
    }
    let fee = 2.0 * crate::lob::costs::leg_fee_bps(true);
    let atr_row = cell(&tg, "1", "atr");
    let fixed_row = cell(&tg, "1", "fixed");
    assert_eq!(atr_row["cfg"], win_atr.to_string());
    assert_eq!(fixed_row["cfg"], win_fixed.to_string());
    approx(
        atr_row["net_main"].parse().unwrap(),
        30.0 + wiggle_mean() - fee,
        0.5,
    );
    approx(
        fixed_row["net_main"].parse().unwrap(),
        10.0 + wiggle_mean() - fee,
        0.5,
    );
    assert_eq!(
        (atr_row["unit"].as_str(), fixed_row["unit"].as_str()),
        ("atr", "bps")
    );
    // у пары (A, −1): fixed и atr из ничьей оценены на тесте как есть
    assert_eq!(cell(&tg, "-1", "fixed")["cfg"], tie_fixed.to_string());
    assert_eq!(cell(&tg, "-1", "atr")["cfg"], tie_atr.to_string());
    let rep = fs::read_to_string(out_t.join("report-exits-tables.md")).unwrap();
    assert!(rep.contains("## 3. Лучшая в семействе"), "{rep}");
    assert!(rep.contains("atr − fixed: Δnet = 20.0"), "{rep}");
    assert!(rep.contains("tpa G2.0A S1.0A cap360"), "{rep}");
    assert!(rep.contains("140 конфигураций"), "{rep}");
    // описательная таблица теста — все 140 конфигураций на пару и знак
    assert_eq!(
        csv_rows(&out_t.join("exits-verdict-test-grid.csv")).len(),
        2 * NCFG
    );
    // тот же вход, потоки 1 и 4 — файлы побайтно равны
    let out_t4 = dir.path().join("out-test4");
    verdict(vargs(
        VStage::ExitsTest,
        &inp_t,
        &pj,
        &out_t4,
        &out.join("exits-k.json"),
        4,
    ))
    .unwrap();
    for f in [
        "exits-verdict-test.csv",
        "exits-verdict-test-grid.csv",
        "exits-verdict-test-groups.csv",
        "report-exits-tables.md",
    ] {
        assert_eq!(
            fs::read(out_t.join(f)).unwrap(),
            fs::read(out_t4.join(f)).unwrap(),
            "{f}: зависит от числа потоков"
        );
    }
}

#[test]
fn test_stage_checks_best_by_group_and_rejects_old_grid() {
    let dir = tempfile::tempdir().unwrap();
    let a = (0u8, 0u8, 0u8);
    let pj = params_file(dir.path());
    let kf = rules_file(dir.path(), &[a]);
    let ok_rows = |l: &mut Vec<String>| {
        for cfg in 0..NCFG {
            l.push(xrow(a, 1, cfg, TEST_DAY0, 202_601, 5, 10.0));
        }
    };
    let inp = write_exits_input(dir.path(), "test", ok_rows, &grid_csv());
    let o = dir.path().join("o");
    let c1 = tp(300, Some(100), 360);
    let k_with = |best: serde_json::Value| -> PathBuf {
        let g = grid();
        let p = dir.path().join("k-best.json");
        fs::write(
            &p,
            serde_json::to_string(&[serde_json::json!({
                "fam": 0, "wi": 0, "ti": 0, "sign": 1, "cfg": c1,
                "exit_family": g[c1].family.name(), "cap": g[c1].cap(),
                "best_by_group": best
            })])
            .unwrap(),
        )
        .unwrap();
        p
    };
    // конфигурация не из группы (atr-конфигурация в группе fixed) — отказ
    let wrong_group = k_with(serde_json::json!({ "fixed": tpa(1.0, None, 360) }));
    let e = verdict(vargs(VStage::ExitsTest, &inp, &pj, &o, &wrong_group, 1)).unwrap_err();
    assert!(format!("{e:#}").contains("не из этой группы"), "{e:#}");
    // номер вне сетки — отказ
    let outside = k_with(serde_json::json!({ "atr": 255 }));
    let e = verdict(vargs(VStage::ExitsTest, &inp, &pj, &o, &outside, 1)).unwrap_err();
    assert!(format!("{e:#}").contains("вне сетки"), "{e:#}");
    // корректный best_by_group с пустыми группами (null) — работает
    let partial = k_with(serde_json::json!({ "fixed": c1, "atr": null }));
    verdict(vargs(VStage::ExitsTest, &inp, &pj, &o, &partial, 1)).unwrap();
    let tg = csv_rows(&o.join("exits-verdict-test-groups.csv"));
    assert_eq!(tg.len(), 1);
    assert_eq!(
        (tg[0]["group"].as_str(), tg[0]["cfg"].as_str()),
        ("fixed", c1.to_string().as_str())
    );

    // сетка прежней версии (84 конфигурации, без unit) не принимается — таблицы надо пересчитать
    let old_grid = format!("cfg,family,G,S,R,cap\n{}\n", old_grid_lines().join("\n"));
    let inp_old = write_exits_input(
        dir.path(),
        "fit",
        |l| l.push(xrow(a, 1, 0, FIT_DAY0, 202_503, 5, 10.0)),
        &old_grid,
    );
    let e = verdict(vargs(VStage::ExitsFit, &inp_old, &pj, &o, &kf, 1)).unwrap_err();
    assert!(format!("{e:#}").contains("сетка"), "{e:#}");
}
