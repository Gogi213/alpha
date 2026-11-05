//! Тесты выходов П-35 (`exits`, `verdict --stage exits-fit|exits-test`): ручные пути цен с известным ответом
//! (тейк, стоп, трейлинг, предел, задержка исполнения, ничья стоп/трейлинг, потолок и его минута, фандинг в удержании),
//! быстрое == наивное на случайных путях, отсутствие утечки, совпадение набора сделок с `daycells`, детерминизм по
//! числу потоков, вердикт на синтетике с заведомым победителем. Семейства в единицах ATR-прокси (C `tpa`, D `tra`):
//! ручные пути при заданной A (в том числе на границе порога), A не определена, устойчивость нумерации первых 84
//! конфигураций, совпадение с семействами в bps при A = 100, лучшая в семействе (fixed против atr) в вердикте.
//! Единицы текущей волатильности (E–H, `--exits-units`, сетка из 510): нумерация 140–509 выписана независимо от кода,
//! ручные пути tpr/trr/tpn/trn при заданных U (граница включена, стоп приоритетнее, пределы 30/60/180), единица не
//! определена — сделка выпадает только из неё, старые 140 ячеек не меняются, быстрый проход против наивного по всем
//! 510, ранний выход прохода, отсутствие утечки в rv60 и NATR, детерминизм по потокам, вердикт на синтетике с
//! заведомым победителем в новой единице и устойчивость выбора.
//! Результат сделки — ПРОСТАЯ доходность стороны `sign·(c/c_in − 1)`, не `ln` (допущение 18 `exits.rs`): ручные пути
//! строятся на `100·(1 + sign·pnl/1e4)`, а границы тейка, стопа и трейлинга (лонг и шорт) проверены на ценах, где
//! простая и логарифмическая формулы расходятся: шорт ±50 %, тейк шорта 10 % (90,0 / 89,99 / 90,01 / 90,4), стоп 5 %,
//! трейлинг 5 % от максимума, `mfe`, лонг + шорт = 0 точно, недостижимые пороги (≥ 1e4 bps).

use super::agg::{fix, unfix, Dims, Layout};
use super::cache::Contract;
use super::exits::{
    atr_proxy_bps, exits_head, grid, grid_all, grid_csv, grid_csv_all, nt_bps, rv60_bps, scan,
    scan_a, scan_x, Cfg, ExitAgg, Fac, FacA, FacU, Facs, Family, Px, Unit, Units, UnitsArgs, CAPS,
    CAPS_U, NCAP, NCAPU, NCFG, NCFG_ALL, NCFG_FIXED, NCFG_U, NCFG_UNIT, NO_STOP, SLA_MULT,
    SLU_MULT, SL_LEVELS, TPA_MULT, TPU_MULT, TP_LEVELS, TRAIL_LEVELS, TRA_MULT, TRU_MULT,
};
use super::play::{natr_series, PlayArgs};
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

/// Путь: `cf[0] = cf[1] = 100` (вход на минуте 1, `t_f = 0`, `first_min = 0`), дальше `cf[1 + j] = 100·(1 + s·pnl(j)/1e4)`
/// — позиция стороны `sign` в минуте `j` имеет ровно `pnl(j)` bps ПРОСТОЙ доходности (`s·(c/100 − 1)·1e4`). Всего `n`
/// минут после входа.
fn path(sign: i8, n: usize, pnl: impl Fn(usize) -> f64) -> Vec<f64> {
    let s = f64::from(sign);
    let mut cf = vec![100.0, 100.0];
    for j in 1..=n {
        cf.push(100.0 * (1.0 + s * pnl(j) / BPS));
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

/// Наивная реализация П-35 «в лоб»: пошагово, простая доходность на каждой минуте, один конфиг за раз. Пороги семейств C и D —
/// `множитель·a` bps (`a` — A сделки). Возвращает `(причина, удержание, gross bps, fund bps)`.
fn naive(cf: &[f64], sign: i8, cfg: &Cfg, rates: &[(i64, f64)], a: f64) -> (Why, u32, f64, f64) {
    let (b, s, c_in) = (1usize, f64::from(sign), cf[1]);
    let cap_j = cfg.cap() as usize - 1;
    // пороги в bps: тейк, стоп, трейлинг; `a` — единица семейства (A, rv или nt), у семейств в bps не используется
    let (g_thr, s_thr, r_thr) = if cfg.family.unit_kind() != Unit::Bps {
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
        let pnl = s * (cf[b + j] / c_in - 1.0) * BPS;
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
    let gross = s * (cf[b + j_exec] / c_in - 1.0) * BPS;
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
                .map(|j| s * (cf[1 + j] / cf[1] - 1.0) * BPS)
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
        exits_units: UnitsArgs::default(),
        dump_triggers: false,
        dump_features: false,
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
    // простая доходность лонга от входа: `exp(Δln) − 1`; `ln`-ход растёт на 10,1 bps в минуту (допущение 18 `exits.rs`)
    let pnl = |j: usize| ((lnp(t_in + j, &[T0]) - lnp(t_in, &[T0])).exp() - 1.0) * BPS;
    let row = |cfg: usize| one_row(&ex, cfg);
    // тейк G: касание на первой минуте j с pnl_j ≥ G (простая: 10 → 101,5; 20 → 204,1; 30 → 307,6; 49 → 507,3 — а не 50,
    // как было бы по `ln`: 10,1·49 = 494,9 < 500), исполнение на j + 1; до 1000 bps путь в окне cap 360 не доходит
    // (простая доходность на минуте 89 — 940,6, к минуте 358 с дрейфом ε — ≈ 970) — выход по пределу
    for (g_lvl, want) in [
        (100u32, Some(10usize)),
        (200, Some(20)),
        (300, Some(30)),
        (500, Some(49)),
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
    // простая доходность за 2 минуты: exp(0,00202) − 1 = 20,2204 bps, а не 20,2 (как было бы по ln)
    approx(pnl(2), ((0.00202f64).exp() - 1.0) * BPS, 1e-9);
    approx(pnl(2), 20.2204, 1e-3);
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

/// Как `path`, но вход на минуте `t_in`: `cf[0..=t_in] = 100`, дальше `cf[t_in + j] = 100·(1 + s·pnl(j)/1e4)`.
fn path_from(sign: i8, t_in: usize, n: usize, pnl: impl Fn(usize) -> f64) -> Vec<f64> {
    let s = f64::from(sign);
    let mut cf = vec![100.0; t_in + 1];
    for j in 1..=n {
        cf.push(100.0 * (1.0 + s * pnl(j) / BPS));
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
    // при A = 100 порог m·A совпадает с m·100 bps: одинаковые выражения множителей → одинаковые первые касания, значит
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
    // слишком большая A: порог не представим (не конечен) — A считается не определённой
    assert!(FacA::new(1.0, 1.0e308).is_none());
    // а порог ≥ 1e4 bps представим и просто недостижим: тейк шорта 5·A = 12 500 bps даёт неположительный множитель
    // (цена не бывает ≤ 0); цена падает до 1 % входа (+9900 bps шорта): множители 1, 2, 3·A взяты, 5·A — никогда
    let fa = FacA::new(-1.0, 2500.0).expect("порог 1e4 bps представим");
    let cf = path(-1, 50, |j| if j >= 3 { 9900.0 } else { 0.0 });
    let sc = scan_a(&cf, 1, 49, -1, &Fac::new(-1.0), Some(&fa));
    assert_eq!(sc.tpa, [3, 3, 3, u32::MAX]);
    // стоп лонга 3·A = 12 000 bps — то же: цена падает до 1 % входа (−9900 bps), стопы 1, 2·A взяты, 3·A — никогда
    let fa = FacA::new(1.0, 4000.0).expect("порог 1e4 bps представим");
    let cf = path(1, 50, |j| if j >= 3 { -9900.0 } else { 0.0 });
    let sc = scan_a(&cf, 1, 49, 1, &Fac::new(1.0), Some(&fa));
    assert_eq!(sc.sla, [3, 3, u32::MAX]);
}

/// Прежний проход (до семейств в единицах A): указатели по каждому уровню, сравнение на каждой минуте; пороги — те же
/// выражения простой доходности, что в `Fac::new` и `scan_core` (допущение 18 `exits.rs`). Эталон «байт в байт» для
/// части в bps: новый проход обязан дать те же первые касания и потолки.
type PrevScan = ([u32; 5], [u32; 4], [u32; 4], [(u64, u32); 2], (u64, u32));

fn scan_prev(cf: &[f64], b: usize, jmax: usize, sign: i8) -> PrevScan {
    let s = f64::from(sign);
    let m = |lvl: u32, k: f64| 1.0 + k * f64::from(lvl) / BPS;
    let (ftp, fsl, ftr) = (
        TP_LEVELS.map(|l| m(l, s)),
        SL_LEVELS.map(|l| m(l, -s)),
        TRAIL_LEVELS.map(|l| f64::from(l) / BPS),
    );
    let c_in = cf[b];
    let v_in = s * c_in;
    let mut tp_thr = [f64::INFINITY; 5];
    let mut sl_thr = [f64::NEG_INFINITY; 4];
    for (t, f) in tp_thr.iter_mut().zip(&ftp) {
        *t = v_in * f;
    }
    for (t, f) in sl_thr.iter_mut().zip(&fsl) {
        *t = v_in * f;
    }
    let pnl = |v: f64| s * (v / v_in - 1.0) * BPS;
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
                while trn < tpn.min(4) && v <= vbest - ftr[trn] * c_in {
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
    let g = grid_all();
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

// ---------- единицы текущей волатильности (rv, nt): сетка из 510 конфигураций ----------

/// Номер конфигурации семейств E и G (`fam` = `TpR` | `TpN`) в сетке из 510: множители `g·U`, `s·U`.
fn tpu(fam: Family, g: f64, s: Option<f64>, cap: u32) -> usize {
    grid_all()
        .iter()
        .position(|c| c.family == fam && c.g_mult() == Some(g) && c.s_mult() == s && c.cap() == cap)
        .unwrap()
}

/// Номер конфигурации семейств F и H (`fam` = `TrR` | `TrN`).
fn tru(fam: Family, r: f64, s: Option<f64>, cap: u32) -> usize {
    grid_all()
        .iter()
        .position(|c| c.family == fam && c.r_mult() == Some(r) && c.s_mult() == s && c.cap() == cap)
        .unwrap()
}

/// Единицы сделки: A — `A_DEF`, rv и nt — заданные.
fn units(rv: f64, nt: f64) -> Units {
    Units { atr: A_DEF, rv, nt }
}

/// Одна сделка в пустой накопитель с единицами rv и nt (1 правило, 1 сутки, `t_f = 0`).
fn ext_one(cf: &[f64], rates: &[(i64, f64)], sign: i8, u: Units) -> ExitAgg {
    let mut ex = ExitAgg::with_units(1, 1, 30);
    let px = Px {
        cf,
        first_min: 0,
        funding: Funding::new(rates),
    };
    let g = ex.group(0, usize::from(sign < 0), 0);
    ex.add_trade_u(g, &px, 0, sign, u);
    ex
}

const RATES_U: [(i64, f64); 2] = [(-1000, 0.0), (5000, 0.0)];

#[test]
fn grid_all_has_510_configs_and_first_140_are_stable() {
    let g = grid_all();
    assert_eq!(g.len(), NCFG_ALL);
    assert_eq!(
        (NCFG, NCFG_FIXED, NCFG_UNIT, NCFG_U, NCFG_ALL),
        (140, 84, 185, 370, 510)
    );
    // нумерация 0–139 не менялась: те же конфигурации и те же строки exits-grid.csv
    assert_eq!(g[..NCFG], grid()[..]);
    let (csv, old) = (grid_csv_all(), grid_csv());
    let lines: Vec<&str> = csv.lines().collect();
    assert_eq!(lines.len(), NCFG_ALL + 1);
    assert_eq!(lines[..=NCFG], old.lines().collect::<Vec<_>>()[..]);
    // новые 370 — выписаны независимо от кода: единица rv, затем nt; внутри tp, затем trail; циклы по возрастанию,
    // предел внутренний
    let caps = [30, 60, 180, 360, 1440];
    let mut want: Vec<String> = Vec::new();
    for (unit, tp_f, tr_f) in [("rv", "tpr", "trr"), ("nt", "tpn", "trn")] {
        for gm in ["0.5", "1.0", "2.0", "3.0", "5.0"] {
            for s in ["0.5", "1.0", "2.0", "3.0", "none"] {
                for cap in caps {
                    want.push(format!(
                        "{},{tp_f},{gm},{s},,{cap},{unit}",
                        NCFG + want.len()
                    ));
                }
            }
        }
        for r in ["0.5", "1.0", "2.0", "3.0"] {
            for s in ["1.0", "2.0", "none"] {
                for cap in caps {
                    want.push(format!(
                        "{},{tr_f},,{s},{r},{cap},{unit}",
                        NCFG + want.len()
                    ));
                }
            }
        }
    }
    assert_eq!(want.len(), NCFG_U);
    for (k, w) in want.iter().enumerate() {
        assert_eq!(lines[1 + NCFG + k], w, "cfg {}", NCFG + k);
    }
    // состав по семействам и единицам
    let count = |f: Family| g.iter().filter(|c| c.family == f).count();
    assert_eq!(
        (
            count(Family::TpR),
            count(Family::TrR),
            count(Family::TpN),
            count(Family::TrN)
        ),
        (125, 60, 125, 60)
    );
    assert_eq!(g[NCFG].family, Family::TpR);
    assert_eq!(g[NCFG + NCFG_UNIT].family, Family::TpN);
    assert_eq!(g[NCFG_ALL - 1].family, Family::TrN);
    // пределы новых семейств — свои; параметры в bps и A у них не определены
    for c in &g[NCFG..] {
        assert!(c.family.is_units() && !c.family.is_atr());
        assert!(CAPS_U.contains(&c.cap()));
        assert!(c.g_bps().is_none() && c.s_bps().is_none() && c.r_bps().is_none());
        assert!(c.s_mult().is_some() || c.sl == NO_STOP);
    }
    assert!(g[..NCFG]
        .iter()
        .all(|c| !c.family.is_units() && CAPS.contains(&c.cap())));
    // уникальность параметров (с семейством и единицей)
    let uniq: std::collections::HashSet<String> = csv
        .lines()
        .skip(1)
        .map(|l| l.split_once(',').unwrap().1.to_string())
        .collect();
    assert_eq!(uniq.len(), NCFG_ALL);
    assert_eq!(TPU_MULT, [0.5, 1.0, 2.0, 3.0, 5.0]);
    assert_eq!(SLU_MULT, [0.5, 1.0, 2.0, 3.0]);
    assert_eq!(TRU_MULT, [0.5, 1.0, 2.0, 3.0]);
    assert_eq!(CAPS_U, caps);
    assert_eq!(NCAPU, 5);
    // порядок единиц при ничьей: bps < atr < rv < nt
    assert!(Unit::Bps < Unit::Atr && Unit::Atr < Unit::Rv && Unit::Rv < Unit::Nt);
    assert_eq!(
        [Unit::Bps, Unit::Atr, Unit::Rv, Unit::Nt].map(Unit::name),
        ["bps", "atr", "rv", "nt"]
    );
}

#[test]
fn rv_and_nt_take_profit_thresholds_are_multiples_of_their_own_unit() {
    // решение на минуте 5 (130), исполнение по минуте 6 (90); затем 400
    let pnl = |j: usize| match j {
        1..=4 => 0.0,
        5 => 130.0,
        6 => 90.0,
        _ => 400.0,
    };
    for sign in [1i8, -1] {
        let cf = path(sign, 1500, pnl);
        // rv = 50, nt = 80: пороги rv — 25, 50, 100, 150, 250; пороги nt — 40, 80, 160, 240, 400 (400 — ровно на границе)
        let ex = ext_one(&cf, &RATES_U, sign, units(50.0, 80.0));
        for (fam, u) in [(Family::TpR, 50.0), (Family::TpN, 80.0)] {
            for (m, gross, hold) in [
                (0.5, 90.0, 6),
                (1.0, 90.0, 6),
                (
                    2.0,
                    if u < 60.0 { 90.0 } else { 400.0 },
                    if u < 60.0 { 6 } else { 8 },
                ),
                (3.0, 400.0, 8),
                (5.0, 400.0, 8),
            ] {
                for s in [None, Some(3.0)] {
                    for cap in CAPS_U {
                        let (g, _, h, why) = res(&ex, sign, tpu(fam, m, s, cap));
                        approx(g, gross, 1e-6);
                        assert_eq!(
                            (h, why),
                            (hold, "tp"),
                            "sign {sign} {fam:?} U{u} g{m} s{s:?} cap {cap}"
                        );
                    }
                }
            }
        }
        // rv = 400: порог 1·U = 400 достигнут ровно (pnl 400 ≥ 400) на минуте 7; 2·U — нет, предел
        let ex = ext_one(&cf, &RATES_U, sign, units(400.0, 400.0));
        for fam in [Family::TpR, Family::TpN] {
            let (_, _, h, w) = res(&ex, sign, tpu(fam, 1.0, None, 360));
            assert_eq!((h, w), (8, "tp"));
            let (_, _, h, w) = res(&ex, sign, tpu(fam, 2.0, None, 360));
            assert_eq!((h, w), (359, "cap"));
            // короткий предел 30: касание на минуте 7 раньше предела — тейк
            let (_, _, h, w) = res(&ex, sign, tpu(fam, 1.0, None, 30));
            assert_eq!((h, w), (8, "tp"));
        }
    }
}

#[test]
fn rv_and_nt_stop_priority_and_boundary_are_inclusive() {
    // минута 3: −110, минута 4: −150, далее +500; U = 50: стопы 25, 50, 100 касаются на минуте 3 (исполнение на 4 =
    // −150), стоп 3·U = 150 — на минуте 4 ровно (граница), исполнение на 5 (500); тейк 0.5·U = 25 — на минуте 5
    let pnl = |j: usize| match j {
        1..=2 => -20.0,
        3 => -110.0,
        4 => -150.0,
        _ => 500.0,
    };
    for sign in [1i8, -1] {
        let cf = path(sign, 1500, pnl);
        let ex = ext_one(&cf, &RATES_U, sign, units(50.0, 50.0));
        for fam in [Family::TpR, Family::TpN] {
            for cap in CAPS_U {
                for s in [0.5, 1.0, 2.0] {
                    let (g, _, hold, why) = res(&ex, sign, tpu(fam, 0.5, Some(s), cap));
                    approx(g, -150.0, 1e-6);
                    assert_eq!((hold, why), (4, "sl"), "{fam:?} s{s} cap {cap}");
                }
                let (g, _, hold, why) = res(&ex, sign, tpu(fam, 0.5, Some(3.0), cap));
                approx(g, 500.0, 1e-6);
                assert_eq!((hold, why), (5, "sl"), "{fam:?} s3 cap {cap}");
                // без стопа — тейк на минуте 5, исполнение на 6
                let (_, _, hold, why) = res(&ex, sign, tpu(fam, 0.5, None, cap));
                assert_eq!((hold, why), (6, "tp"));
            }
        }
    }
    // тейк раньше стопа: +150 на минуте 2, затем −300 на минуте 4; U = 100 → тейк 1·U на минуте 2, исполнение на 3
    let pnl = |j: usize| match j {
        1 => 0.0,
        2 => 150.0,
        3 => 160.0,
        _ => -300.0,
    };
    let ex = ext_one(&path(1, 1500, pnl), &RATES_U, 1, units(100.0, 100.0));
    for fam in [Family::TpR, Family::TpN] {
        let (g, _, hold, why) = res(&ex, 1, tpu(fam, 1.0, Some(1.0), 360));
        approx(g, 160.0, 1e-6);
        assert_eq!((hold, why), (3, "tp"));
    }
}

#[test]
fn rv_and_nt_trailing_activation_drawdown_and_stop_priority() {
    // минута 3: +150 (максимум), минута 5: −120 — и просадка от максимума, и стоп в одну минуту; исполнение на 6 = −130
    let pnl = |j: usize| match j {
        1..=2 => 0.0,
        3..=4 => 150.0,
        5 => -120.0,
        _ => -130.0,
    };
    for sign in [1i8, -1] {
        let cf = path(sign, 1500, pnl);
        // U = 60: R = 30, 60, 120, 180
        let ex = ext_one(&cf, &RATES_U, sign, units(60.0, 60.0));
        for fam in [Family::TrR, Family::TrN] {
            for cap in [30u32, 60, 1440] {
                // стоп 1·U = 60 и трейлинг R = 1·U в минуту 5: приоритет стопа
                let (g, _, hold, why) = res(&ex, sign, tru(fam, 1.0, Some(1.0), cap));
                approx(g, -130.0, 1e-6);
                assert_eq!((hold, why), (6, "sl"), "{fam:?} cap {cap}");
                // стоп 2·U = 120 ровно на границе (−120 ≤ −120): тоже стоп
                let (_, _, hold, why) = res(&ex, sign, tru(fam, 1.0, Some(2.0), cap));
                assert_eq!((hold, why), (6, "sl"));
                // без стопа — трейлинг
                let (g, _, hold, why) = res(&ex, sign, tru(fam, 1.0, None, cap));
                approx(g, -130.0, 1e-6);
                assert_eq!((hold, why), (6, "trail"));
                for r in [0.5, 2.0] {
                    let (_, _, hold, why) = res(&ex, sign, tru(fam, r, None, cap));
                    assert_eq!((hold, why), (6, "trail"), "R {r}");
                }
                // R = 3·U = 180 не активен (максимум 150): со стопом — стоп, без — предел (цена −130)
                let (_, _, hold, why) = res(&ex, sign, tru(fam, 3.0, Some(1.0), cap));
                assert_eq!((hold, why), (6, "sl"));
                let (g, _, hold, why) = res(&ex, sign, tru(fam, 3.0, None, cap));
                approx(g, -130.0, 1e-6);
                assert_eq!((hold, why), (i64::from(cap) - 1, "cap"), "cap {cap}");
            }
        }
    }
    // откат от максимума без стопа; U = 50: R = 25, 50, 100, 150. Максимум 400 на минуте 10
    let pnl = |j: usize| match j {
        1..=9 => 60.0,
        10 => 400.0,
        11 => 340.0,
        12 => 299.0,
        13 => 280.0,
        _ => 100.0,
    };
    for sign in [1i8, -1] {
        let ex = ext_one(&path(sign, 1500, pnl), &RATES_U, sign, units(50.0, 50.0));
        for fam in [Family::TrR, Family::TrN] {
            // R = 25 и 50: откат ≤ 375 и ≤ 350 — минута 11 (340), исполнение на 12 (299)
            for r in [0.5, 1.0] {
                let (g, _, hold, why) = res(&ex, sign, tru(fam, r, None, 360));
                approx(g, 299.0, 1e-6);
                assert_eq!((hold, why), (12, "trail"), "R {r}");
            }
            // R = 100: откат ≤ 300 — минута 12 (299), исполнение на 13 (280)
            let (g, _, hold, why) = res(&ex, sign, tru(fam, 2.0, None, 360));
            approx(g, 280.0, 1e-6);
            assert_eq!((hold, why), (13, "trail"));
            // R = 150: откат ≤ 250 — минута 14 (100), исполнение на 15 (100)
            let (g, _, hold, why) = res(&ex, sign, tru(fam, 3.0, None, 1440));
            approx(g, 100.0, 1e-6);
            assert_eq!((hold, why), (15, "trail"));
        }
    }
}

#[test]
fn short_caps_30_60_180_execute_at_their_own_scheduled_minute() {
    let ucaps = [30u32, 60, 180, 360];
    for sign in [1i8, -1] {
        for (i, &c) in ucaps.iter().enumerate() {
            let next = CAPS_U[i + 1];
            // касание на последней минуте решения c − 2 (150 ≥ 100), исполнение на c − 1 (140): тейк успевает
            let early = |j: usize| match j {
                j if j == c as usize - 2 => 150.0,
                j if j == c as usize - 1 => 140.0,
                _ => 0.0,
            };
            let ex = ext_one(
                &path(sign, 1500, early),
                &RATES_U,
                sign,
                units(100.0, 100.0),
            );
            for fam in [Family::TpR, Family::TpN] {
                let (g, _, hold, why) = res(&ex, sign, tpu(fam, 1.0, None, c));
                approx(g, 140.0, 1e-6);
                assert_eq!((hold, why), (i64::from(c) - 1, "tp"), "{fam:?} cap {c}");
            }
            // касание на минуте c − 1 — решение уже не принимается: предел по c[c − 1]; у следующего предела — тейк
            let late = |j: usize| if j == c as usize - 1 { 150.0 } else { 0.0 };
            let ex = ext_one(&path(sign, 1500, late), &RATES_U, sign, units(100.0, 100.0));
            for fam in [Family::TpR, Family::TpN] {
                let (g, _, hold, why) = res(&ex, sign, tpu(fam, 1.0, None, c));
                approx(g, 150.0, 1e-6);
                assert_eq!((hold, why), (i64::from(c) - 1, "cap"), "{fam:?} cap {c}");
                let (g, _, hold, why) = res(&ex, sign, tpu(fam, 1.0, None, next));
                approx(g, 0.0, 1e-6);
                assert_eq!((hold, why), (i64::from(c), "tp"), "{fam:?} cap {next}");
            }
        }
        // ничего не происходит: предел 30 — по цене минуты 29, 60 — по 59, 180 — по 179
        let ex = ext_one(
            &path(sign, 1500, |_| 10.0),
            &rates_wide(),
            sign,
            units(100.0, 100.0),
        );
        for (&c, &h) in CAPS_U.iter().zip(&[29i64, 59, 179, 359, 1439]) {
            let (g, _, hold, why) = res(&ex, sign, tru(Family::TrR, 1.0, None, c));
            approx(g, 10.0, 1e-6);
            assert_eq!((hold, why), (h, "cap"));
        }
    }
}

#[test]
fn undefined_unit_excludes_trade_from_its_own_unit_only() {
    let rates = rates_var();
    let (rv_range, nt_range) = (NCFG..NCFG + NCFG_UNIT, NCFG + NCFG_UNIT..NCFG_ALL);
    for sign in [1i8, -1] {
        let cf = rw(77, sign, 1500, 6.0);
        let base = ext_one(&cf, &rates, sign, units(40.0, 25.0));
        let g = base.group(0, usize::from(sign < 0), 0);
        assert_eq!(
            (base.cnt.rv_undefined, base.cnt.nt_undefined),
            (0, 0),
            "единицы определены"
        );
        assert!((NCFG..NCFG_ALL).any(|k| *base.cfg_cell(g, k) != Default::default()));
        for bad in [f64::NAN, 0.0, -3.0, f64::INFINITY, f64::NEG_INFINITY] {
            for rv_bad in [true, false] {
                let ex = ext_one(
                    &cf,
                    &rates,
                    sign,
                    if rv_bad {
                        units(bad, 25.0)
                    } else {
                        units(40.0, bad)
                    },
                );
                let (gone, kept) = if rv_bad {
                    (rv_range.clone(), nt_range.clone())
                } else {
                    (nt_range.clone(), rv_range.clone())
                };
                // прежние 140 конфигураций и их n — как при определённых единицах
                for k in 0..NCFG {
                    assert_eq!(ex.cfg_cell(g, k), base.cfg_cell(g, k), "bad {bad} cfg {k}");
                }
                for ci in 0..NCAP {
                    assert_eq!(ex.cap_cell(g, ci), base.cap_cell(g, ci));
                    assert_eq!(ex.cap_cell_atr(g, ci), base.cap_cell_atr(g, ci));
                }
                // другая единица — как при определённой; своей единицы у сделки нет
                for k in kept {
                    assert_eq!(ex.cfg_cell(g, k), base.cfg_cell(g, k), "bad {bad} cfg {k}");
                }
                for k in gone {
                    assert_eq!(*ex.cfg_cell(g, k), Default::default(), "bad {bad} cfg {k}");
                }
                for ui in 0..NCAPU {
                    let (mine, other) = if rv_bad {
                        (ex.cap_cell_rv(g, ui), ex.cap_cell_nt(g, ui))
                    } else {
                        (ex.cap_cell_nt(g, ui), ex.cap_cell_rv(g, ui))
                    };
                    assert_eq!(*mine, Default::default());
                    assert_eq!(other.n, 1);
                }
                let (u, v) = if rv_bad {
                    (ex.cnt.rv_undefined_by_cap, ex.cnt.nt_undefined_by_cap)
                } else {
                    (ex.cnt.nt_undefined_by_cap, ex.cnt.rv_undefined_by_cap)
                };
                assert_eq!((u, v), ([1; NCAPU], [0; NCAPU]), "bad {bad}");
                assert_eq!(ex.cnt.triggers, 1);
            }
        }
    }
    // короткий ряд: достижимы пределы до 360, предел 1440 — «нет цен»; исключение считается по взятым пределам
    let cf = path(1, 400, |j| if j == 300 { 250.0 } else { 0.0 });
    let ex = ext_one(&cf, &rates_wide(), 1, units(f64::NAN, 30.0));
    assert_eq!(ex.cnt.rv_undefined, 1);
    assert_eq!(ex.cnt.rv_undefined_by_cap, [1, 1, 1, 1, 0]);
    assert_eq!(ex.cnt.nt_undefined_by_cap, [0; NCAPU]);
    let trades: Vec<u64> = ex.cnt.caps_u.iter().map(|c| c.trades).collect();
    assert_eq!(trades, [1, 1, 1, 1, 0]);
    assert_eq!(ex.cnt.caps_u[4].nodata, 1);
    // прежние счётчики по CAPS — хвост счётчиков по CAPS_U
    assert_eq!((ex.cnt.caps[0].trades, ex.cnt.caps[1].nodata), (1, 1));
    // сделка без цен/фандинга в симуляцию не берётся и как «без единицы» не считается
    let ex = ext_one(
        &path(1, 1500, |_| 10.0),
        &[(5, 1.0e-4)],
        1,
        units(f64::NAN, f64::NAN),
    );
    assert_eq!((ex.cnt.rv_undefined, ex.cnt.nt_undefined), (0, 0));
}

#[test]
fn trade_sets_of_short_caps_follow_round_trip_and_funding_coverage() {
    // файл фандинга кончается на минуте 480: пределы 30, 60, 180, 360 покрыты (gap 480), 1440 — нет
    let short = [(-480i64, 1.0e-4), (0, 1.0e-4), (480, 1.0e-4)];
    let cf = path(1, 1500, |j| if j == 40 { 120.0 } else { 0.0 });
    let ex = ext_one(&cf, &short, 1, units(50.0, 50.0));
    let g = ex.group(0, 0, 0);
    let n: Vec<u32> = (0..NCAPU).map(|u| ex.cap_cell_rv(g, u).n).collect();
    assert_eq!(n, [1, 1, 1, 1, 0]);
    let n: Vec<u32> = (0..NCAPU).map(|u| ex.cap_cell_nt(g, u).n).collect();
    assert_eq!(n, [1, 1, 1, 1, 0]);
    // набор сделок = round_trip(H = cap) для каждого предела; пределы 360 и 1440 — те же счётчики, что у прежних cap
    for (ui, &cap) in CAPS_U.iter().enumerate() {
        let r = round_trip(&cf, 0, &Funding::new(&short), 0, cap, 1);
        assert_eq!(ex.cnt.caps_u[ui].trades, u64::from(r.is_ok()), "cap {cap}");
        assert_eq!(
            ex.cnt.caps_u[ui].uncovered,
            u64::from(r == Err(super::trade::Skip::Funding)),
            "cap {cap}"
        );
    }
    assert_eq!(ex.cnt.caps[0].trades, ex.cnt.caps_u[3].trades);
    assert_eq!(ex.cnt.caps[1].uncovered, ex.cnt.caps_u[4].uncovered);
    // потолок: максимум за окно самого длинного cap — 120 на минуте 40; за окно собственного cap — по его окну
    approx(unfix(ex.cap_cell_rv(g, 0).mfe), 120.0, 1e-6);
    assert_eq!(ex.cap_cell_rv(g, 0).mfe_min, 40);
    approx(unfix(ex.cap_cell_rv(g, 0).mfe_cap), 0.0, 1e-6); // окно cap 30: j ≤ 29, до минуты 40 pnl = 0
    approx(unfix(ex.cap_cell_rv(g, 1).mfe_cap), 120.0, 1e-6); // окно cap 60
    assert_eq!(ex.cap_cell_rv(g, 1).mfe_cap_min, 40);
    // первый конфиг предела 1440 пуст, предела 30 — заполнен (hold = 29)
    let c30 = tpu(Family::TpR, 5.0, None, 30);
    assert_eq!(ex.cfg_cell(g, c30).hold, 29);
    assert_eq!(
        *ex.cfg_cell(g, tpu(Family::TpR, 5.0, None, 1440)),
        Default::default()
    );

    // сделка, взятая лишь короткими пределами (ряд из 100 минут после входа): в совокупность семейств C и D (пределы
    // 360 и 1440) она не входила, поэтому «A не определена» её не считает; единицы rv и nt — на коротких пределах
    let cf = path(1, 100, |_| 0.0);
    let u = Units {
        atr: f64::NAN,
        rv: 30.0,
        nt: 30.0,
    };
    let ex = ext_one(&cf, &rates_wide(), 1, u);
    let g = ex.group(0, 0, 0);
    assert_eq!(
        (
            ex.cnt.triggers,
            ex.cnt.atr_undefined,
            ex.cnt.atr_undefined_by_cap
        ),
        (1, 0, [0, 0])
    );
    let trades: Vec<u64> = ex.cnt.caps_u.iter().map(|c| c.trades).collect();
    assert_eq!(trades, [1, 1, 0, 0, 0]);
    assert_eq!(ex.cnt.caps_u[2].nodata, 1);
    assert_eq!((ex.cap_cell_rv(g, 1).n, ex.cap_cell_rv(g, 2).n), (1, 0));
    assert_eq!((ex.cap_cell(g, 0).n, ex.cap_cell_atr(g, 0).n), (0, 0));
}

/// Единицы по семейству конфигурации для наивного сравнения: bps — не нужна, A, rv, nt — свои.
fn unit_of(cfg: &Cfg, u: &Units) -> f64 {
    match cfg.family.unit_kind() {
        Unit::Bps => f64::NAN,
        Unit::Atr => u.atr,
        Unit::Rv => u.rv,
        Unit::Nt => u.nt,
    }
}

fn defined(x: f64) -> bool {
    x.is_finite() && x > 0.0
}

#[test]
fn fast_scan_equals_naive_for_all_510_configs() {
    let rates = rates_var();
    let mut reasons = BTreeMap::<String, usize>::new();
    for seed in 1..=40u64 {
        let step = [2.0, 6.0, 15.0][(seed % 3) as usize];
        // единицы сопоставимы с ходом пути (σ минутных доходностей ≈ 0,58·step; за час ×√60), чтобы пороги задевались;
        // каждая пятая сделка без rv, каждая седьмая без nt
        let scale = [0.7, 4.5, 11.0][(seed % 3) as usize] * step;
        let u = Units {
            atr: scale,
            rv: if seed % 5 == 0 { f64::NAN } else { 1.3 * scale },
            nt: if seed % 7 == 0 { 0.0 } else { 0.8 * scale },
        };
        for sign in [1i8, -1] {
            let cf = rw(seed * 6007, sign, 1500, step);
            let ex = ext_one(&cf, &rates, sign, u);
            let g = ex.group(0, usize::from(sign < 0), 0);
            for (k, cfg) in grid_all().iter().enumerate() {
                let x = unit_of(cfg, &u);
                let c = ex.cfg_cell(g, k);
                if cfg.family.unit_kind() != Unit::Bps && !defined(x) {
                    assert_eq!(*c, Default::default(), "seed {seed} sign {sign} cfg {k}");
                    continue;
                }
                let (why, hold, gross, fund) = naive(&cf, sign, cfg, &rates, x);
                let tag = format!("seed {seed} sign {sign} cfg {k} {cfg:?}");
                assert_eq!(c.gross, fix(gross), "{tag} gross");
                assert_eq!(c.fund, fix(fund), "{tag} fund");
                assert_eq!(c.hold, i64::from(hold), "{tag} hold");
                let got = match (c.n_tp, c.n_sl, c.n_trail) {
                    (1, 0, 0) => Why::Tp,
                    (0, 1, 0) => Why::Sl,
                    (0, 0, 1) => Why::Trail,
                    _ => Why::Cap,
                };
                assert_eq!(got, why, "{tag} причина");
                if cfg.family.is_units() {
                    let key = format!(
                        "{}_{}",
                        cfg.family.unit(),
                        format!("{got:?}").to_lowercase()
                    );
                    *reasons.entry(key).or_default() += 1;
                }
            }
        }
    }
    // случайные пути задевают все причины выхода в обеих новых единицах (иначе сравнение было бы вырожденным)
    for unit in ["rv", "nt"] {
        for why in ["tp", "sl", "trail"] {
            let n = reasons[&format!("{unit}_{why}")];
            assert!(n > 200, "{unit}_{why}: {reasons:?}");
        }
    }
}

#[test]
fn units_do_not_change_the_old_140_cells_bit_for_bit() {
    let rates = rates_var();
    for seed in 1..=25u64 {
        let step = [3.0, 9.0, 22.0][(seed % 3) as usize];
        for sign in [1i8, -1] {
            let cf = rw(seed * 2_654_435_761, sign, 1500, step);
            let a = if seed % 4 == 0 { f64::NAN } else { 4.0 * step };
            let old = one_a(&cf, &rates, sign, a);
            assert!(!old.is_ext() && old.grid().len() == NCFG);
            let ext = ext_one(
                &cf,
                &rates,
                sign,
                Units {
                    atr: a,
                    rv: 5.0 * step,
                    nt: if seed % 3 == 0 { f64::NAN } else { 2.0 * step },
                },
            );
            assert!(ext.is_ext() && ext.grid().len() == NCFG_ALL);
            let g = old.group(0, usize::from(sign < 0), 0);
            for k in 0..NCFG {
                assert_eq!(
                    ext.cfg_cell(g, k),
                    old.cfg_cell(g, k),
                    "seed {seed} sign {sign} cfg {k}"
                );
            }
            for ci in 0..NCAP {
                assert_eq!(ext.cap_cell(g, ci), old.cap_cell(g, ci));
                assert_eq!(ext.cap_cell_atr(g, ci), old.cap_cell_atr(g, ci));
            }
            assert_eq!(ext.cnt.caps[0].trades, old.cnt.caps[0].trades);
            assert_eq!(ext.cnt.atr_undefined_by_cap, old.cnt.atr_undefined_by_cap);
            // у накопителя по умолчанию ячеек rv и nt нет вовсе
            assert_eq!(old.cnt.caps_u[0].trades, 0);
        }
    }
}

#[test]
fn scan_x_keeps_bps_and_atr_hits_and_orders_new_levels() {
    let fac = [Fac::new(1.0), Fac::new(-1.0)];
    for seed in 1..=15u64 {
        for sign in [1i8, -1] {
            let cf = rw(seed * 99_991, sign, 1500, 8.0);
            let f = &fac[usize::from(sign < 0)];
            let s = f64::from(sign);
            let fa = FacA::new(s, 40.0);
            let (fr, fnn) = (FacU::new(s, 60.0), FacU::new(s, 30.0));
            let base = scan_a(&cf, 1, 1439, sign, f, fa.as_ref());
            let x = scan_x(
                &cf,
                1,
                1439,
                sign,
                &Facs {
                    bps: f,
                    atr: fa.as_ref(),
                    rv: fr.as_ref(),
                    nt: fnn.as_ref(),
                },
            );
            // части bps и A, потолки — те же, что без единиц rv и nt
            assert_eq!(x.hits[0].tp, base.tp);
            assert_eq!(x.hits[0].sl[..], base.sl[..]);
            assert_eq!(x.hits[0].tr[..], base.tr[..]);
            assert_eq!(x.hits[1].tp[..4], base.tpa);
            assert_eq!(x.hits[1].sl[..3], base.sla);
            assert_eq!(x.hits[1].tr[..3], base.tra);
            assert_eq!(x.mfe, base.mfe);
            assert_eq!([x.mfe_cap[3], x.mfe_cap[4]], base.mfe_cap);
            // новые уровни: касаются, и более высокий порог не раньше низкого
            for h in &x.hits[2..] {
                assert!(h.tp.iter().chain(&h.sl).any(|&t| t != u32::MAX));
                assert!(h.tp.windows(2).all(|w| w[0] <= w[1]), "{:?}", h.tp);
                assert!(h.sl.windows(2).all(|w| w[0] <= w[1]), "{:?}", h.sl);
            }
            // единицы без множителей (None) указателей не заводят
            let none = scan_x(
                &cf,
                1,
                1439,
                sign,
                &Facs {
                    bps: f,
                    atr: None,
                    rv: None,
                    nt: None,
                },
            );
            assert_eq!(none.hits[0], x.hits[0]);
            for h in &none.hits[1..] {
                assert_eq!(h.tp, [u32::MAX; 5]);
                assert_eq!(h.sl, [u32::MAX; 4]);
                assert_eq!(h.tr, [u32::MAX; 4]);
            }
        }
    }
    for u in [f64::NAN, 0.0, -1.0, f64::INFINITY] {
        assert!(FacU::new(1.0, u).is_none(), "U = {u}");
    }
    // слишком большая единица: порог не представим (не конечен) — единица не определена
    assert!(FacU::new(1.0, 1.0e308).is_none());
}

#[test]
fn early_exit_keeps_maximum_and_caps_windows_after_all_levels_are_taken() {
    // всплеск вверх до +1500 (минута 5) и обвал до −1000 (минута 10) берут все уровни всех единиц; дальше проход
    // следит только за максимумом: ещё более высокий пик на минуте 700 (+2500) и его окно cap обязаны сохраниться
    let pnl = |j: usize| match j {
        1..=4 => 0.0,
        5 => 1500.0,
        6..=9 => 500.0,
        10..=699 => -1000.0,
        700 => 2500.0,
        _ => -1000.0,
    };
    let fac = [Fac::new(1.0), Fac::new(-1.0)];
    for sign in [1i8, -1] {
        let cf = path(sign, 1500, pnl);
        let s = f64::from(sign);
        let (fa, fr, fnn) = (FacA::new(s, 30.0), FacU::new(s, 20.0), FacU::new(s, 25.0));
        let x = scan_x(
            &cf,
            1,
            1439,
            sign,
            &Facs {
                bps: &fac[usize::from(sign < 0)],
                atr: fa.as_ref(),
                rv: fr.as_ref(),
                nt: fnn.as_ref(),
            },
        );
        // все уровни взяты до минуты 700
        for h in &x.hits {
            assert!(h.tp.iter().filter(|&&t| t != u32::MAX).all(|&t| t < 700));
        }
        assert!(x.hits[2].tr.iter().all(|&t| t < 700), "{:?}", x.hits[2].tr);
        assert!(x.hits[0].sl.iter().all(|&t| t < 700));
        // потолки: до минуты 700 максимум 1500 на минуте 5, окно 1440 — 2500 на минуте 700
        assert_eq!(x.mfe.1, 700);
        approx(x.mfe.0, 2500.0, 1e-6);
        for (ui, &(m, at)) in x.mfe_cap.iter().enumerate().take(4) {
            approx(m, 1500.0, 1e-6);
            assert_eq!(at, 5, "cap {}", CAPS_U[ui]);
        }
        assert_eq!(x.mfe_cap[4], x.mfe);
        // окна короче 700 минут тоже: прохода по сегментам хватает на любой jmax
        for jmax in [1439usize, 700, 699, 359, 29, 5, 1] {
            let y = scan_x(
                &cf,
                1,
                jmax,
                sign,
                &Facs {
                    bps: &fac[usize::from(sign < 0)],
                    atr: fa.as_ref(),
                    rv: fr.as_ref(),
                    nt: fnn.as_ref(),
                },
            );
            let s = f64::from(sign);
            let pn: Vec<f64> = (1..=jmax)
                .map(|j| s * (cf[1 + j] / cf[1] - 1.0) * BPS)
                .collect();
            let argmax = |hi: usize| {
                let (mut best, mut at) = (f64::NEG_INFINITY, 0usize);
                for (i, &v) in pn[..hi.min(pn.len())].iter().enumerate() {
                    if v > best {
                        (best, at) = (v, i + 1);
                    }
                }
                (best, at as u32)
            };
            assert_eq!(y.mfe, argmax(jmax), "jmax {jmax}");
            for (ui, &cap) in CAPS_U.iter().enumerate() {
                if jmax + 1 >= cap as usize {
                    assert_eq!(
                        y.mfe_cap[ui],
                        argmax(cap as usize - 1),
                        "jmax {jmax} cap {cap}"
                    );
                } else {
                    assert!(y.mfe_cap[ui].0.is_nan(), "jmax {jmax} cap {cap}");
                }
            }
        }
    }
}

#[test]
fn result_does_not_depend_on_prices_after_exit_in_any_unit() {
    let rates = rates_var();
    for seed in 1..=10u64 {
        for sign in [1i8, -1] {
            let cf = rw(seed * 15_485_863, sign, 1500, 8.0);
            let u = units(35.0, 20.0);
            let ex = ext_one(&cf, &rates, sign, u);
            let g = ex.group(0, usize::from(sign < 0), 0);
            for k in 0..NCFG_ALL {
                let base = *ex.cfg_cell(g, k);
                let after = 1 + usize::try_from(base.hold).unwrap() + 1;
                let mut cf2 = cf.clone();
                for x in &mut cf2[after..] {
                    *x *= 3.0;
                }
                let ex2 = ext_one(&cf2, &rates, sign, u);
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
fn rv60_follows_dump_formula_and_uses_only_data_up_to_t_f() {
    // произвольный путь с шумом: окно из 60 минутных ln-доходностей, оканчивающихся в t_f
    let mut r = Xs(0xA5A5_1234_9E37_79B9);
    let mut cf = vec![100.0];
    for _ in 1..300 {
        let last = *cf.last().unwrap();
        cf.push(last * ((r.unit() - 0.5) * 6.0e-3).exp());
    }
    let first_min = 1_000i64;
    for t_f in [
        first_min + 60,
        first_min + 61,
        first_min + 150,
        first_min + 299,
    ] {
        let i = (t_f - first_min) as usize;
        let mut sum = 0.0f64;
        for k in i - 59..=i {
            let l = (cf[k] / cf[k - 1]).ln();
            sum += l * l;
        }
        let want = sum.sqrt() * BPS;
        assert_eq!(rv60_bps(&cf, first_min, t_f).to_bits(), want.to_bits());
        // данные после t_f на значение не влияют
        let mut later = cf.clone();
        for x in &mut later[i + 1..] {
            *x *= 1.7;
        }
        assert_eq!(
            rv60_bps(&later, first_min, t_f).to_bits(),
            want.to_bits(),
            "t_f {t_f}"
        );
        // а данные в окне — влияют
        let mut inside = cf.clone();
        inside[i - 30] *= 1.01;
        assert_ne!(rv60_bps(&inside, first_min, t_f).to_bits(), want.to_bits());
    }
    // окно не помещается (нужны 61 цена), t_f вне ряда или до его начала — не определена
    assert!(rv60_bps(&cf, first_min, first_min + 59).is_nan());
    assert!(rv60_bps(&cf, first_min, first_min + 300).is_nan());
    assert!(rv60_bps(&cf, first_min, first_min - 5).is_nan());
    // нечестная цена в окне (NaN, 0, −1) — не определена; вне окна — не мешает
    for bad in [f64::NAN, 0.0, -1.0] {
        let mut c = cf.clone();
        c[100] = bad;
        assert!(rv60_bps(&c, first_min, first_min + 120).is_nan(), "{bad}");
        assert!(
            rv60_bps(&c, first_min, first_min + 161).is_finite(),
            "{bad}"
        );
    }
    // плоская цена: rv = 0 — единица не определена (FacU требует > 0)
    let flat = vec![100.0; 200];
    assert_eq!(rv60_bps(&flat, 0, 100), 0.0);
    assert!(FacU::new(1.0, rv60_bps(&flat, 0, 100)).is_none());
}

#[test]
fn nt_is_natr_percent_in_bps_and_uses_only_data_up_to_t_f() {
    // NATR 0,8 % на минуте 100 → 80 bps; вне ряда и NaN — не определено
    let mut natr = vec![f32::NAN; 200];
    natr[100] = 0.8;
    natr[101] = 1.3;
    assert_eq!(nt_bps(&natr, 0, 100), f64::from(0.8f32) * 100.0);
    assert_eq!(nt_bps(&natr, 50, 151), f64::from(1.3f32) * 100.0);
    assert!(nt_bps(&natr, 0, 99).is_nan());
    assert!(nt_bps(&natr, 0, 200).is_nan());
    assert!(nt_bps(&natr, 150, 100).is_nan());
    // NATR из play::natr_series на свечах с high/low: правка свечей ПОСЛЕ минуты не меняет значения в минуте
    let mut r = Xs(0xC0FF_EE00_1234_5678);
    let n = 400usize;
    let mut close = vec![100.0f64];
    for _ in 1..n {
        let last = *close.last().unwrap();
        close.push(last * ((r.unit() - 0.5) * 4.0e-3).exp());
    }
    let mk = |close: &[f64]| Contract {
        first_min: 0,
        close: close.to_vec(),
        high: close.iter().map(|&x| (x * 1.001) as f32).collect(),
        low: close.iter().map(|&x| (x * 0.999) as f32).collect(),
        ..Default::default()
    };
    let c = mk(&close);
    let s = natr_series(&c, 30);
    let t = 250usize;
    assert!(s[t].is_finite());
    // TR ≈ 0,2 % (гэп от прошлого close дополняет размах), поэтому NATR порядка десятков bps
    assert!(nt_bps(&s, 0, t as i64) > 10.0);
    let mut later = close.clone();
    for x in &mut later[t + 1..] {
        *x *= 1.3;
    }
    let s2 = natr_series(&mk(&later), 30);
    assert_eq!(s[t].to_bits(), s2[t].to_bits());
    assert_eq!(
        nt_bps(&s, 0, t as i64).to_bits(),
        nt_bps(&s2, 0, t as i64).to_bits()
    );
    // а правка внутри окна — меняет
    let mut inside = close.clone();
    inside[t - 10] *= 1.05;
    assert_ne!(natr_series(&mk(&inside), 30)[t].to_bits(), s[t].to_bits());
}

/// Путь с шумом до `t_in` включительно и ровно `100` на минуте входа: дальше `100·(1 + s·pnl(j)/1e4)`.
fn noisy_path_from(sign: i8, t_in: usize, n: usize, pnl: impl Fn(usize) -> f64) -> Vec<f64> {
    let s = f64::from(sign);
    let mut r = Xs(0x1357_9BDF_2468_ACE0);
    let mut cf: Vec<f64> = (0..t_in)
        .map(|_| 100.0 * ((r.unit() - 0.5) * 2.0e-3).exp())
        .collect();
    cf.push(100.0);
    for j in 1..=n {
        cf.push(100.0 * (1.0 + s * pnl(j) / BPS));
    }
    cf
}

#[test]
fn add_triggers_u_takes_rv_and_nt_from_the_trigger_minute() {
    let t_f = 100i64; // слот 20, вход на минуте 101
    let lay = layout_one_rule(day_of_min(t_f));
    let rates = rates_wide();
    let natr_pct = 0.8f32;
    let u_nt = f64::from(natr_pct) * 100.0;
    for sign in [1i8, -1] {
        // шумный путь до входа: rv60 на минуте t_f определён и положителен; независимое вычисление по формуле
        let probe = noisy_path_from(sign, 101, 1500, |_| 0.0);
        let u_rv = rv60_bps(&probe, 0, t_f);
        assert!(u_rv > 0.0 && u_rv.is_finite());
        let mut sum = 0.0f64;
        for k in 41..=100 {
            let l = (probe[k] / probe[k - 1]).ln();
            sum += l * l;
        }
        assert_eq!(u_rv.to_bits(), (sum.sqrt() * BPS).to_bits());
        let mut natr = vec![f32::NAN; probe.len()];
        natr[100] = natr_pct;
        // слот 20 ↔ минута 100; σ слота не определена (A не нужна)
        let mut sigma = vec![f32::NAN; 21];
        sigma[20] = 0.004;
        let mk = |u: f64| {
            // 1,5·U на минуте 3, 1,2·U на минуте 4, затем 2,5·U
            noisy_path_from(sign, 101, 1500, |j| match j {
                1..=2 => 0.0,
                3 => 1.5 * u,
                4 => 1.2 * u,
                _ => 2.5 * u,
            })
        };
        let run_one = |cf: &[f64], natr: Option<&[f32]>| {
            let px = Px {
                cf,
                first_min: 0,
                funding: Funding::new(&rates),
            };
            let mut ex = ExitAgg::with_units(1, 1, 30);
            ex.add_triggers_u(
                &lay,
                &cube_with_sigma(sigma.clone(), sign),
                &[vec![20]],
                &px,
                natr,
            );
            ex
        };
        // единица rv: 1·U — касание на минуте 3, исполнение на 4 (1,2·U); 2·U — на 5, исполнение на 6 (2,5·U);
        // 3·U недостижим → предел
        let ex = run_one(&mk(u_rv), Some(&natr[..]));
        assert_eq!(
            (ex.cnt.triggers, ex.cnt.rv_undefined, ex.cnt.nt_undefined),
            (1, 0, 0)
        );
        for (m, gross, hold, why) in [
            (1.0, 1.2 * u_rv, 4, "tp"),
            (2.0, 2.5 * u_rv, 6, "tp"),
            (3.0, 2.5 * u_rv, 29, "cap"),
        ] {
            let (g, _, h, w) = res(&ex, sign, tpu(Family::TpR, m, None, 30));
            approx(g, gross, 1e-6);
            assert_eq!((h, w), (hold, why), "rv g{m}");
        }
        // единица nt: тот же путь, построенный на U_nt = NATR·100 минуты срабатывания
        let ex = run_one(&mk(u_nt), Some(&natr[..]));
        for (m, gross, hold, why) in [
            (1.0, 1.2 * u_nt, 4, "tp"),
            (2.0, 2.5 * u_nt, 6, "tp"),
            (3.0, 2.5 * u_nt, 29, "cap"),
        ] {
            let (g, _, h, w) = res(&ex, sign, tpu(Family::TpN, m, None, 30));
            approx(g, gross, 1e-6);
            assert_eq!((h, w), (hold, why), "nt g{m}");
        }
        let gi = ex.group(0, usize::from(sign < 0), 0);
        assert_eq!((ex.cap_cell_nt(gi, 0).n, ex.cap_cell_rv(gi, 0).n), (1, 1));
        // ряда NATR нет вовсе — nt не определена, rv считается; прежние семейства на месте
        let ex = run_one(&mk(u_rv), None);
        assert_eq!((ex.cnt.rv_undefined, ex.cnt.nt_undefined), (0, 1));
        let gi = ex.group(0, usize::from(sign < 0), 0);
        assert_eq!((ex.cap_cell_rv(gi, 0).n, ex.cap_cell_nt(gi, 0).n), (1, 0));
        assert_ne!(
            *ex.cfg_cell(gi, tpu(Family::TpR, 1.0, None, 30)),
            Default::default()
        );
        assert_eq!(
            *ex.cfg_cell(gi, tpu(Family::TpN, 1.0, None, 30)),
            Default::default()
        );
        assert_ne!(*ex.cfg_cell(gi, tp(100, None, 360)), Default::default());
        // NATR в минуте срабатывания не определён (в соседней есть): nt не определена
        let mut other = vec![f32::NAN; probe.len()];
        other[101] = natr_pct;
        let ex = run_one(&mk(u_rv), Some(&other[..]));
        assert_eq!(ex.cnt.nt_undefined, 1);
        // срабатывание раньше окна rv60 (t_f < 60): rv не определена
        let lay_early = layout_one_rule(day_of_min(30));
        let cf = mk(u_rv);
        let px = Px {
            cf: &cf,
            first_min: 0,
            funding: Funding::new(&rates),
        };
        let mut ex = ExitAgg::with_units(1, 1, 30);
        let mut sg = vec![f32::NAN; 7];
        sg[6] = 0.004;
        ex.add_triggers_u(
            &lay_early,
            &cube_with_sigma(sg, sign),
            &[vec![6]],
            &px,
            Some(&natr[..]),
        );
        assert_eq!((ex.cnt.triggers, ex.cnt.rv_undefined), (1, 1));
    }
}

/// Как `noisy_cache_dir`, но свечи с `high`/`low` (±0,05 % цены): NATR определён, единицы rv и nt положительны.
fn noisy_hl_cache_dir(dir: &Path) -> PathBuf {
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
        c.high = c.close.iter().map(|&x| (x * 1.0005) as f32).collect();
        c.low = c.close.iter().map(|&x| (x * 0.9995) as f32).collect();
        put(&cache, sym, &c);
    }
    cache
}

fn args_units(
    cache: &Path,
    params: &Path,
    out: &Path,
    rules: PathBuf,
    xd: PathBuf,
    threads: usize,
    natr_n: Option<usize>,
) -> RunArgs {
    let mut a = args_run(cache, params, out, rules, xd, threads, true);
    a.exits_units = UnitsArgs {
        exits_units: true,
        exits_natr_n: natr_n,
    };
    a
}

#[test]
fn exits_units_run_is_deterministic_and_keeps_old_rows_byte_for_byte() {
    let dir = tempfile::tempdir().unwrap();
    let cache = noisy_hl_cache_dir(dir.path());
    let pj = params_360_1440(dir.path());
    let (rf, xf) = (rules_file(dir.path(), &RULES), xd_file(dir.path(), XD));
    let (o_def, o1, o4) = (
        dir.path().join("def"),
        dir.path().join("u1"),
        dir.path().join("u4"),
    );
    run(args_run(
        &cache,
        &pj,
        &o_def,
        rf.clone(),
        xf.clone(),
        2,
        true,
    ))
    .unwrap();
    run(args_units(
        &cache,
        &pj,
        &o1,
        rf.clone(),
        xf.clone(),
        1,
        Some(30),
    ))
    .unwrap();
    run(args_units(&cache, &pj, &o4, rf, xf, 4, Some(30))).unwrap();
    // побайтно при любом числе потоков; сетка файла — расширенная
    let a = fs::read(o1.join("exits-test.csv")).unwrap();
    assert_eq!(a, fs::read(o4.join("exits-test.csv")).unwrap());
    assert_eq!(
        fs::read_to_string(o1.join("exits-grid.csv")).unwrap(),
        grid_csv_all()
    );
    assert_eq!(
        fs::read_to_string(o_def.join("exits-grid.csv")).unwrap(),
        grid_csv(),
        "без флага сетка прежняя"
    );
    // строки прежних 140 конфигураций — те же байты, что без флага; основные таблицы от флага не зависят
    let text = String::from_utf8(a).unwrap();
    let old_rows: Vec<&str> = text
        .lines()
        .filter(|l| l.split(',').nth(4).and_then(|c| c.parse::<usize>().ok()) < Some(NCFG))
        .collect();
    let def_text = fs::read_to_string(o_def.join("exits-test.csv")).unwrap();
    assert_eq!(old_rows, def_text.lines().collect::<Vec<_>>());
    for f in ["daycells-test.csv", "lags-test.csv", "first-test.csv"] {
        assert_eq!(
            fs::read(o1.join(f)).unwrap(),
            fs::read(o_def.join(f)).unwrap(),
            "{f}"
        );
    }

    let ex = table(&o1.join("exits-test.csv"));
    let rows_of = |lo: usize, hi: usize| -> Vec<&Row> {
        ex.iter()
            .filter(|r| (lo..hi).contains(&r["cfg"].parse::<usize>().unwrap()))
            .collect()
    };
    let rv_rows = rows_of(NCFG, NCFG + NCFG_UNIT);
    let nt_rows = rows_of(NCFG + NCFG_UNIT, NCFG_ALL);
    assert!(!rv_rows.is_empty() && !nt_rows.is_empty());
    let g = grid_all();
    let mut tp_n = [0u64; 2];
    for (ui, rows) in [&rv_rows, &nt_rows].into_iter().enumerate() {
        for r in rows.iter() {
            let cfg: usize = r["cfg"].parse().unwrap();
            let c = g[cfg];
            let (n, part) = (r["n"].parse::<u64>().unwrap(), |name: &str| {
                r[name].parse::<u64>().unwrap()
            });
            assert_eq!(
                part("n_tp") + part("n_sl") + part("n_trail") + part("n_cap"),
                n,
                "cfg {cfg}"
            );
            match c.family {
                Family::TpR | Family::TpN => assert_eq!(part("n_trail"), 0),
                Family::TrR | Family::TrN => assert_eq!(part("n_tp"), 0),
                other => panic!("не семейство rv/nt: {other:?}"),
            }
            tp_n[ui] += part("n_tp");
        }
    }
    // пампы данных задевают тейк в обеих единицах
    assert!(tp_n[0] > 0 && tp_n[1] > 0, "тейков нет: {tp_n:?}");

    // сделок в ячейке предела не больше, чем у предела короче (покрытие фандинга и длины ряда монотонно по пределу)
    let cols = ["fam", "wi", "ti", "sign", "day"];
    for fam in [Family::TpR, Family::TpN] {
        let by_cap: Vec<BTreeMap<String, u64>> = CAPS_U
            .iter()
            .map(|&cap| {
                let cfg = tpu(fam, 1.0, None, cap);
                ex.iter()
                    .filter(|r| r["cfg"] == cfg.to_string())
                    .map(|r| (key(r, &cols), r["n"].parse().unwrap()))
                    .collect()
            })
            .collect();
        for w in by_cap.windows(2) {
            for (k, n_long) in &w[1] {
                assert!(w[0].get(k).copied().unwrap_or(0) >= *n_long, "{fam:?} {k}");
            }
        }
    }

    // мета: ключи единиц, сделки единицы по пределу = сделки предела − исключённые; Σ n первой конфигурации = ей
    let meta: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(o1.join("run-meta.json")).unwrap()).unwrap();
    let m = &meta["exits"];
    assert_eq!(
        (
            m["configs"].as_u64(),
            m["configs_atr"].as_u64(),
            m["configs_rv"].as_u64(),
            m["configs_nt"].as_u64(),
            m["nt_n"].as_u64()
        ),
        (Some(510), Some(56), Some(185), Some(185), Some(30))
    );
    assert_eq!(m["caps_units"], serde_json::json!(CAPS_U));
    for (ci, cap) in ["30", "60", "180", "360", "1440"].into_iter().enumerate() {
        for (unit, fam) in [("rv", Family::TpR), ("nt", Family::TpN)] {
            let (t, u, a) = (
                m["trades_by_cap_units"][cap].as_u64().unwrap(),
                m[format!("{unit}_undefined_by_cap")][cap].as_u64().unwrap(),
                m[format!("{unit}_trades_by_cap")][cap].as_u64().unwrap(),
            );
            assert_eq!(t - u, a, "{unit} cap {cap}");
            // первая конфигурация предела: тейк 0.5·U без стопа
            let cfg = tpu(fam, 0.5, None, CAPS_U[ci]);
            assert_eq!(g[cfg].cap().to_string(), cap);
            let n_first: u64 = ex
                .iter()
                .filter(|r| r["cfg"] == cfg.to_string())
                .map(|r| r["n"].parse::<u64>().unwrap())
                .sum();
            assert_eq!(n_first, a, "{unit} cap {cap}");
        }
    }
    // прежние ключи не менялись: сделки по 360 и 1440 — те же, что по пределам единиц
    for cap in ["360", "1440"] {
        assert_eq!(m["trades_by_cap"][cap], m["trades_by_cap_units"][cap]);
    }
    assert!(m["rv_unit"].as_str().unwrap().contains("rv60_bps"));
    assert!(m["nt_unit"].as_str().unwrap().contains("NATR"));
    assert!(m["nt_trades_by_cap"]["360"].as_u64().unwrap() > 0);
    // без флага блока единиц нет
    let meta_def: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(o_def.join("run-meta.json")).unwrap()).unwrap();
    assert!(meta_def["exits"].get("rv_undefined").is_none());
    assert_eq!(meta_def["exits"]["configs"].as_u64(), Some(140));
}

#[test]
fn nt_is_undefined_without_high_low_in_the_cache_and_rows_are_absent() {
    // `pump_contract` без high/low: NATR нигде не определён — единица nt пуста, rv и прежние семейства считаются
    let dir = tempfile::tempdir().unwrap();
    let cache = noisy_cache_dir(dir.path());
    let pj = params_360_1440(dir.path());
    let (rf, xf) = (rules_file(dir.path(), &RULES), xd_file(dir.path(), XD));
    let out = dir.path().join("o");
    run(args_units(&cache, &pj, &out, rf, xf, 2, Some(30))).unwrap();
    let ex = table(&out.join("exits-test.csv"));
    assert!(ex
        .iter()
        .all(|r| r["cfg"].parse::<usize>().unwrap() < NCFG + NCFG_UNIT));
    assert!(ex
        .iter()
        .any(|r| r["cfg"].parse::<usize>().unwrap() >= NCFG));
    let meta: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out.join("run-meta.json")).unwrap()).unwrap();
    let m = &meta["exits"];
    assert_eq!(m["nt_trades_by_cap"]["360"].as_u64(), Some(0));
    assert!(m["nt_undefined"].as_u64().unwrap() > 0);
}

#[derive(clap::Parser)]
struct Cli {
    #[command(flatten)]
    a: RunArgs,
}

#[test]
fn exits_units_flags_parse_validate_and_take_natr_n_from_the_filter() {
    use clap::Parser;
    let base = [
        "t",
        "--cache",
        "c",
        "--params",
        "p",
        "--out",
        "o",
        "--stage",
        "fit",
        "--seed",
        "1",
        "--oi-lag",
        "1",
        "--threads",
        "1",
    ];
    let with = |extra: &[&str]| {
        let args: Vec<&str> = base.iter().chain(extra.iter()).copied().collect();
        Cli::try_parse_from(args)
    };
    let a = with(&["--exits", "--exits-units", "--exits-natr-n", "30"])
        .unwrap()
        .a;
    assert!(a.exits && a.exits_units.exits_units);
    assert_eq!(a.exits_units.natr_n(a.exits, None).unwrap(), Some(30));
    // без флагов — единиц нет
    let a = with(&["--exits"]).unwrap().a;
    assert_eq!(a.exits_units.natr_n(a.exits, None).unwrap(), None);
    // зависимости на уровне разбора: единицы требуют --exits, окно — единицы
    assert!(with(&["--exits-units", "--exits-natr-n", "30"]).is_err());
    assert!(with(&["--exits", "--exits-natr-n", "30"]).is_err());
    // окно: явное побеждает окно фильтра выборки, без явного берётся оно; ни того ни другого — отказ
    let u = |n: Option<usize>| UnitsArgs {
        exits_units: true,
        exits_natr_n: n,
    };
    assert_eq!(u(Some(30)).natr_n(true, Some(45)).unwrap(), Some(30));
    assert_eq!(u(None).natr_n(true, Some(45)).unwrap(), Some(45));
    assert!(u(None).natr_n(true, None).is_err());
    assert!(u(Some(0)).natr_n(true, None).is_err());
    assert!(u(Some(30)).natr_n(false, None).is_err());
    assert_eq!(UnitsArgs::default().natr_n(false, Some(45)).unwrap(), None);
}

// ---------- verdict: сетка из 510 конфигураций ----------

#[test]
fn fit_and_test_stages_with_units_pick_by_unit_order_and_report_stability() {
    let dir = tempfile::tempdir().unwrap();
    let a = (0u8, 0u8, 0u8);
    let b = (1u8, 0u8, 0u8);
    // (A, +1): победитель в единицах NATR (номер > 255), лучшие в группах — каждая своя
    let win_nt = tpu(Family::TpN, 2.0, Some(1.0), 60);
    let win_rv = tru(Family::TrR, 1.0, Some(2.0), 180);
    let win_fixed = tp(300, Some(100), 1440);
    let win_atr = tpa(2.0, Some(1.0), 360);
    assert!(win_nt > 255);
    // (A, −1): четыре конфигурации с одинаковыми данными и cap 360 — ничья, выбирается bps; в группах — свои
    let tie = [
        tp(100, Some(100), 360),
        tpa(1.0, Some(1.0), 360),
        tpu(Family::TpR, 1.0, Some(1.0), 360),
        tpu(Family::TpN, 1.0, Some(1.0), 360),
    ];
    // (B, +1): rv и nt с одним cap и одинаковыми данными — rv раньше; rv с меньшим cap раньше большего
    let b_tie = [
        tpu(Family::TpR, 1.0, Some(0.5), 180),
        tpu(Family::TpR, 1.0, Some(0.5), 60),
        tpu(Family::TpN, 1.0, Some(0.5), 60),
    ];
    let grid_text = grid_csv_all();
    let inp = write_exits_input(
        dir.path(),
        "fit",
        |lines| {
            for (rule, sign) in [(a, 1i8), (a, -1), (b, 1)] {
                for cfg in 0..NCFG_ALL {
                    for d in 0..12usize {
                        let mu = if (rule, sign) == (a, 1) && cfg == win_nt {
                            40.0
                        } else if (rule, sign) == (a, 1) && cfg == win_rv {
                            30.0
                        } else if (rule, sign) == (a, 1) && cfg == win_atr {
                            20.0
                        } else if (rule, sign) == (a, 1) && cfg == win_fixed {
                            25.0
                        } else if (rule, sign) == (a, -1) && tie.contains(&cfg) {
                            30.0
                        } else if (rule, sign) == (b, 1) && b_tie.contains(&cfg) {
                            35.0
                        } else {
                            -10.0 + (cfg % 5) as f64
                        };
                        let day = FIT_DAY0 + d as i64;
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
        "exits-verdict-fit-groups.csv",
        "exits-journal-line.txt",
    ] {
        assert_eq!(
            fs::read(o1.join(f)).unwrap(),
            fs::read(o4.join(f)).unwrap(),
            "{f}: зависит от числа потоков"
        );
    }
    let k = read_json(&o1.join("exits-k.json"));
    let arr = k.as_array().unwrap();
    assert_eq!(arr.len(), 3);
    let pick = |rule: (u8, u8, u8), sign: i64| -> &serde_json::Value {
        arr.iter()
            .find(|e| e["fam"] == rule.0 && e["sign"] == sign)
            .unwrap()
    };
    // (A, +1): победитель — nt, номер больше 255 записан и прочитан без потерь
    let p = pick(a, 1);
    assert_eq!(p["cfg"].as_u64(), Some(win_nt as u64));
    assert_eq!(
        (
            p["exit_family"].as_str(),
            p["unit"].as_str(),
            p["cap"].as_u64()
        ),
        (Some("tpn"), Some("nt"), Some(60))
    );
    assert_eq!((p["G"].as_f64(), p["S"].as_f64()), (Some(2.0), Some(1.0)));
    for (name, cfg) in [
        ("nt", win_nt),
        ("rv", win_rv),
        ("fixed", win_fixed),
        ("atr", win_atr),
    ] {
        assert_eq!(
            p["best_by_group"][name].as_u64(),
            Some(cfg as u64),
            "{name}"
        );
    }
    // (A, −1): ничья в cap 360 → bps раньше atr, atr раньше rv, rv раньше nt; в группах — свой
    let p = pick(a, -1);
    assert_eq!(p["cfg"].as_u64(), Some(tie[0] as u64));
    assert_eq!(p["unit"], "bps");
    for (name, cfg) in [
        ("fixed", tie[0]),
        ("atr", tie[1]),
        ("rv", tie[2]),
        ("nt", tie[3]),
    ] {
        assert_eq!(
            p["best_by_group"][name].as_u64(),
            Some(cfg as u64),
            "{name}"
        );
    }
    // (B, +1): меньший cap раньше при равенстве, затем rv раньше nt
    let p = pick(b, 1);
    assert_eq!(p["cfg"].as_u64(), Some(b_tie[1] as u64), "{p}");
    assert_eq!(p["unit"], "rv");
    assert_eq!(p["best_by_group"]["nt"].as_u64(), Some(b_tie[2] as u64));
    // таблица фита: все 510 ячеек на пару и знак; колонка unit; ровно одна выбранная на пару
    let rows = csv_rows(&o1.join("exits-verdict-fit.csv"));
    assert_eq!(rows.len(), 2 * 2 * NCFG_ALL);
    assert_eq!(rows.iter().filter(|r| r["chosen"] == "1").count(), 3);
    let w = rows
        .iter()
        .find(|r| r["fam"] == "0" && r["sign"] == "1" && r["cfg"] == win_nt.to_string())
        .unwrap();
    assert_eq!(
        (
            w["family"].as_str(),
            w["unit"].as_str(),
            w["G"].as_str(),
            w["S"].as_str(),
            w["cap"].as_str()
        ),
        ("tpn", "nt", "2.0", "1.0", "60")
    );
    // сводка по группам включает rv и nt
    let grp = csv_rows(&o1.join("exits-verdict-fit-groups.csv"));
    for name in ["rv", "nt"] {
        assert!(grp.iter().any(|r| r["group"] == name), "{name}");
    }
    let line = fs::read_to_string(o1.join("exits-journal-line.txt")).unwrap();
    assert!(
        line.contains("510 конфигураций")
            && line.contains("Множественность подбора")
            && line.contains("bps < atr < rv < nt")
            && line.contains("exits-verdict-test-stability.csv"),
        "{line}"
    );

    // ---- тест: замороженные пары; в паре (A, −1) выбранная на фите bps-конфигурация не лучшая на тесте
    let inp_t = write_exits_input(
        dir.path(),
        "test",
        |lines| {
            for (rule, sign) in [(a, 1i8), (a, -1), (b, 1)] {
                for cfg in 0..NCFG_ALL {
                    for m in 0..10usize {
                        for d in 0..3usize {
                            let day = TEST_DAY0 + (m * 31 + d) as i64;
                            let mu = if (rule, sign) == (a, 1) && cfg == win_nt {
                                30.0
                            } else if (rule, sign) == (a, -1) && cfg == tie[2] {
                                50.0
                            } else if (rule, sign) == (a, -1) && cfg == tie[3] {
                                40.0
                            } else if ((rule, sign) == (a, -1) && (cfg == tie[0] || cfg == tie[1]))
                                || ((rule, sign) == (b, 1) && cfg == b_tie[1])
                            {
                                30.0
                            } else {
                                -12.0 + (cfg % 4) as f64
                            };
                            let month = 202_601 + m as i32;
                            lines.push(xrow(rule, sign, cfg, day, month, 8, mu + wiggle(m + d)));
                        }
                    }
                }
            }
        },
        &grid_text,
    );
    let (t1, t4) = (dir.path().join("t1"), dir.path().join("t4"));
    verdict(vargs(
        VStage::ExitsTest,
        &inp_t,
        &pj,
        &t1,
        &o1.join("exits-k.json"),
        1,
    ))
    .unwrap();
    verdict(vargs(
        VStage::ExitsTest,
        &inp_t,
        &pj,
        &t4,
        &o1.join("exits-k.json"),
        4,
    ))
    .unwrap();
    for f in [
        "exits-verdict-test.csv",
        "exits-verdict-test-grid.csv",
        "exits-verdict-test-groups.csv",
        "exits-verdict-test-stability.csv",
        "report-exits-tables.md",
    ] {
        assert_eq!(
            fs::read(t1.join(f)).unwrap(),
            fs::read(t4.join(f)).unwrap(),
            "{f}: зависит от числа потоков"
        );
    }
    assert_eq!(
        csv_rows(&t1.join("exits-verdict-test-grid.csv")).len(),
        3 * NCFG_ALL
    );
    let st = csv_rows(&t1.join("exits-verdict-test-stability.csv"));
    assert_eq!(st.len(), 3);
    let s_plus = by_pair(&st, "0", "1");
    assert_eq!(s_plus["cfg"], win_nt.to_string());
    assert_eq!(s_plus["rank_lo"], "1", "{s_plus:?}");
    assert_eq!(s_plus["top_cfg"], win_nt.to_string());
    assert_eq!(
        (s_plus["pos_net"].as_str(), s_plus["configs"].as_str()),
        ("1", "510")
    );
    let s_minus = by_pair(&st, "0", "-1");
    // две конфигурации лучше выбранной (tie[2] = 50, tie[3] = 40); равная ей tie[1] места не отнимает
    assert_eq!(s_minus["cfg"], tie[0].to_string());
    assert_eq!(s_minus["rank_lo"], "3", "{s_minus:?}");
    assert_eq!(s_minus["top_cfg"], tie[2].to_string());
    assert_eq!(s_minus["pos_net"], "4");
    approx(s_minus["share_pos"].parse().unwrap(), 4.0 / 510.0, 1e-6);
    let rep = fs::read_to_string(t1.join("report-exits-tables.md")).unwrap();
    for needle in [
        "## 3. Лучшая в семействе: bps против единиц ATR-прокси, rv60 и NATR",
        "## 4. Устойчивость выбора",
        "Множественность подбора",
        "510 конфигураций",
        "tpn G2.0nt S1.0nt cap60",
        "rv − fixed: Δnet",
        "nt − fixed: Δnet",
    ] {
        assert!(rep.contains(needle), "нет «{needle}» в отчёте:\n{rep}");
    }
    let tg = csv_rows(&t1.join("exits-verdict-test-groups.csv"));
    for name in ["rv", "nt"] {
        assert!(tg.iter().any(|r| r["group"] == name), "{name}");
    }
    // номер конфигурации вне сетки из 510 — отказ; сетка файла из 140 и таблицы из 510 строк не сочетаются
    let bad = write_exits_input(
        dir.path(),
        "fit",
        |l| l.push(xrow(a, 1, NCFG_ALL, FIT_DAY0, 202_503, 5, 10.0)),
        &grid_text,
    );
    let e = verdict(vargs(
        VStage::ExitsFit,
        &bad,
        &pj,
        &dir.path().join("x"),
        &kf,
        1,
    ))
    .unwrap_err();
    assert!(format!("{e:#}").contains("вне сетки"), "{e:#}");
    let old_grid = write_exits_input(
        dir.path(),
        "fit",
        |l| l.push(xrow(a, 1, 200, FIT_DAY0, 202_503, 5, 10.0)),
        &grid_csv(),
    );
    let e = verdict(vargs(
        VStage::ExitsFit,
        &old_grid,
        &pj,
        &dir.path().join("x"),
        &kf,
        1,
    ))
    .unwrap_err();
    assert!(format!("{e:#}").contains("вне сетки"), "{e:#}");
}

// ---------- простая доходность стороны, не ln (допущение 18 `exits.rs`) ----------

/// Путь с явными ценами: `cf[0] = cf[1] = 100` (вход на минуте 1, `t_f = 0`), `cf[1 + j] = price(j)`, `j = 1..=n`.
fn prices(n: usize, price: impl Fn(usize) -> f64) -> Vec<f64> {
    let mut cf = vec![100.0, 100.0];
    cf.extend((1..=n).map(price));
    cf
}

/// Итог единственной сделки стороны `sign` по конфигурации `cfg` на явном пути цен: `(gross, удержание, причина)`.
fn on_prices(cf: &[f64], sign: i8, cfg: usize) -> (f64, i64, &'static str) {
    let ex = one(cf, &RATES0, sign);
    let (g, _, hold, why) = res(&ex, sign, cfg);
    (g, hold, why)
}

#[test]
fn trade_result_of_a_side_is_simple_return_not_log() {
    let rates = rates_wide();
    let fund = Funding::new(&rates);
    // цена вверх на 50 % и вниз на 50 %: шорт −5000 и +5000 bps (по ln было бы −4054,65 и +6931,47), лонг наоборот
    for (c_out, long, short) in [(150.0, 5000.0, -5000.0), (50.0, -5000.0, 5000.0)] {
        let cf = [100.0, 100.0, c_out];
        let l = round_trip(&cf, 0, &fund, 0, 2, 1).unwrap();
        let s = round_trip(&cf, 0, &fund, 0, 2, -1).unwrap();
        assert_eq!((l.gross_bps, s.gross_bps), (long, short), "c_out {c_out}");
        let ln = (c_out / 100.0f64).ln() * BPS;
        assert!((s.gross_bps + ln).abs() > 900.0, "ln-значение {ln}");
        // базовая линия выходов (cap 360 → цена минуты 359) на пути, который остаётся на этой цене, — те же ±5000
        let cf = prices(1500, |j| if j >= 3 { c_out } else { 100.0 });
        for (sign, want) in [(1i8, long), (-1, short)] {
            let (g, hold, why) = on_prices(&cf, sign, 0);
            approx(g, want, 1e-6);
            assert_eq!((hold, why), (359, "cap"), "sign {sign} c_out {c_out}");
        }
    }
}

#[test]
fn long_plus_short_on_the_same_prices_is_exactly_zero() {
    let rates = rates_wide();
    let fund = Funding::new(&rates);
    for r in [0.37, 0.9, 0.9999, 1.0, 1.0001, 1.5, 1.9, 2.0, 7.3] {
        let cf = [100.0, 100.0, 100.0 * r];
        let l = round_trip(&cf, 0, &fund, 0, 2, 1).unwrap();
        let s = round_trip(&cf, 0, &fund, 0, 2, -1).unwrap();
        assert_eq!(l.gross_bps + s.gross_bps, 0.0, "r {r}");
        assert_eq!(l.gross_bps, -s.gross_bps, "r {r}");
    }
    // выходы: лонг и шорт на одном и том же случайном пути; базовая линия (цена предела) — сумма ровно 0
    let rates = rates_var();
    for seed in 1..=10u64 {
        let cf = rw(seed * 4_099, 1, 1500, 9.0);
        let (lo, sh) = (one(&cf, &rates, 1), one(&cf, &rates, -1));
        for cfg in [0usize, 1] {
            let (gl, gs) = (res(&lo, 1, cfg).0, res(&sh, -1, cfg).0);
            assert_eq!(gl + gs, 0.0, "seed {seed} cfg {cfg}");
        }
    }
}

#[test]
fn take_profit_ten_percent_fires_at_simple_threshold_not_at_log_equivalent() {
    // решение на минуте 5 по цене c5, исполнение на минуте 6 по цене 100 (результат 0); тейк 1000 bps (10 %), без стопа
    let fire = |sign: i8, c5: f64| -> (&'static str, i64) {
        let cf = prices(1500, |j| if j == 5 { c5 } else { 100.0 });
        let (g, hold, why) = on_prices(&cf, sign, tp(1000, None, 360));
        approx(g, 0.0, 1e-6);
        (why, hold)
    };
    // шорт: 10 % — это c ≤ 0,9·c0 = 90 (по ln-эквиваленту сработало бы уже при c ≤ 100·e^(−0,1) = 90,48)
    for (c5, fires) in [
        (90.0, true),   // 0,9·c0: граница включена
        (89.99, true),  // 0,8999·c0
        (90.01, false), // 0,9001·c0
        (90.4, false),  // простая доходность 960 bps (по ln было бы 1009 bps — тейк сработал бы)
        (91.0, false),
    ] {
        let want = if fires { ("tp", 6) } else { ("cap", 359) };
        assert_eq!(fire(-1, c5), want, "шорт c5 {c5}");
    }
    // лонг: 10 % — это c ≥ 1,1·c0 = 110 (по ln-эквиваленту — только при c ≥ 110,52)
    for (c5, fires) in [
        (110.01, true),
        (110.4, true), // простая доходность 1040 bps (по ln было бы 989 bps — тейка нет)
        (109.99, false),
        (109.0, false),
    ] {
        let want = if fires { ("tp", 6) } else { ("cap", 359) };
        assert_eq!(fire(1, c5), want, "лонг c5 {c5}");
    }
}

#[test]
fn stop_and_trailing_use_simple_return_for_both_sides() {
    // стоп 500 bps (5 %); тейк 1000 на этих путях недостижим
    let stop = |sign: i8, c5: f64| -> (&'static str, i64) {
        let cf = prices(1500, |j| if j == 5 { c5 } else { 100.0 });
        let (_, hold, why) = on_prices(&cf, sign, tp(1000, Some(500), 360));
        (why, hold)
    };
    // лонг: стоп — c ≤ 95 (по ln-эквиваленту — c ≤ 95,12)
    for (c5, fires) in [(95.0, true), (94.95, true), (95.05, false), (95.1, false)] {
        let want = if fires { ("sl", 6) } else { ("cap", 359) };
        assert_eq!(stop(1, c5), want, "лонг c5 {c5}");
    }
    // шорт: стоп — c ≥ 105 (по ln-эквиваленту — c ≥ 105,13)
    for (c5, fires) in [
        (105.0, true),
        (105.05, true),
        (104.95, false),
        (104.9, false),
    ] {
        let want = if fires { ("sl", 6) } else { ("cap", 359) };
        assert_eq!(stop(-1, c5), want, "шорт c5 {c5}");
    }

    // трейлинг R = 500 bps: активен после +500; выход при откате на 500 bps от максимума по простой доходности;
    // цена 100 → максимум `peak` на минуте 3 → `c4` с минуты 4 (решение на 4, исполнение на 5)
    let trail_run = |sign: i8, peak: f64, c4: f64| -> (&'static str, i64) {
        let cf = prices(1500, |j| match j {
            1..=2 => 100.0,
            3 => peak,
            _ => c4,
        });
        let (_, hold, why) = on_prices(&cf, sign, trail(500, None, 360));
        (why, hold)
    };
    // шорт: максимум 90 (+1000) → выход при c ≥ 95 (+500); по ln-эквиваленту — только при c ≥ 94,62
    assert_eq!(trail_run(-1, 90.0, 95.1), ("trail", 5));
    assert_eq!(trail_run(-1, 90.0, 94.9), ("cap", 359));
    // лонг: максимум 110 (+1000) → выход при c ≤ 105 (+500); по ln-эквиваленту — только при c ≤ 104,64
    assert_eq!(trail_run(1, 110.0, 104.8), ("trail", 5));
    assert_eq!(trail_run(1, 110.0, 105.2), ("cap", 359));
}

#[test]
fn mfe_is_simple_return_signed_by_side() {
    // лонг: максимум цены 110 на минуте 10 → +1000 bps (по ln 953,1); шорт: минимум 90 → +1000 bps (по ln 1053,6)
    for (sign, peak) in [(1i8, 110.0), (-1, 90.0)] {
        let cf = prices(1500, |j| if j == 10 { peak } else { 100.0 });
        let ex = one(&cf, &RATES0, sign);
        let c = ex.cap_cell(ex.group(0, usize::from(sign < 0), 0), 0);
        approx(unfix(c.mfe), 1000.0, 1e-6);
        approx(unfix(c.mfe_cap), 1000.0, 1e-6);
        assert_eq!((c.mfe_min, c.mfe_cap_min), (10, 10), "sign {sign}");
    }
    // потолок — максимум по j ≥ 1 со знаком стороны, а не 0; цена +20 %: шорт целиком в минусе, −2000 (по ln −1823,2),
    // лонг +2000 (по ln +1823,2)
    let cf = prices(1500, |_| 120.0);
    for (sign, want) in [(-1i8, -2000.0), (1, 2000.0)] {
        let ex = one(&cf, &RATES0, sign);
        let c = ex.cap_cell(ex.group(0, usize::from(sign < 0), 0), 0);
        approx(unfix(c.mfe), want, 1e-6);
        assert_eq!(c.mfe_min, 1, "sign {sign}");
    }
}

/// Случайный путь с мультипликативными шагами `c ← c·(1 + u·step/1e4)`, `u` равномерна на ±1: цена всегда положительна,
/// путь уходит на десятки процентов — там простая и логарифмическая доходности заметно расходятся.
fn rw_mult(seed: u64, n: usize, step: f64) -> Vec<f64> {
    let mut r = Xs(seed | 1);
    let mut cf = vec![100.0, 100.0];
    for _ in 1..=n {
        let last = *cf.last().unwrap();
        cf.push(last * (1.0 + (r.unit() - 0.5) * 2.0 * step / BPS));
    }
    cf
}

#[test]
fn fast_scan_equals_naive_on_wide_multiplicative_paths() {
    let rates = rates_var();
    let mut reasons = BTreeMap::<&str, usize>::new();
    for seed in 1..=30u64 {
        let step = [40.0, 90.0][(seed % 2) as usize];
        let a = [2.0, 6.0, 15.0][(seed % 3) as usize] * step;
        for sign in [1i8, -1] {
            let cf = rw_mult(seed * 7_001, 1500, step);
            assert_matches_naive(
                &cf,
                &rates,
                sign,
                a,
                &format!("wide seed {seed} sign {sign}"),
            );
            let ex = one_a(&cf, &rates, sign, a);
            let g = ex.group(0, usize::from(sign < 0), 0);
            for k in 0..NCFG {
                let c = ex.cfg_cell(g, k);
                *reasons.entry("tp").or_default() += c.n_tp as usize;
                *reasons.entry("sl").or_default() += c.n_sl as usize;
                *reasons.entry("trail").or_default() += c.n_trail as usize;
            }
        }
    }
    // широкие пути задевают все причины выхода (иначе сравнение было бы вырожденным)
    for why in ["tp", "sl", "trail"] {
        assert!(reasons[why] > 200, "{why}: {reasons:?}");
    }
}
