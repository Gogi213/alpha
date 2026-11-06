//! Тесты правок ядра по слепому аудиту (TK-493 волна 3): F-02 `Params::rearm` и причина выхода, F-01 счётчики
//! окна и z, F-03 β на входе. Замкнутые сценарии: X плоская (100), β = 1 фиксирована, окно 24, `min_obs` 24,
//! точный пересчёт окна каждую минуту (`refresh = 1`: у плоского окна дисперсия ровно ноль, z не определён).
//! Ожидаемые числа получены независимой моделью (шаги правила выписаны заново, окно пересчитывается с нуля, без кода ядра)
//! и сверены с ядром, многоклеточным проходом и референсом.

use super::kernel::{
    run_pair, BetaMode, Counters, ExitReason, LegKind, PairInput, Params, Rule, Trade,
};
use super::multi::run_pair_multi;
use super::reference;
use super::tests::{both, lns, ou_on, params, walk, with_gaps, Lcg, N};

/// Y: 100 до минуты 29, дальше событие — падение со ступеньками и возврат к 100 (z на минуте 30 = −√23 ≈ −4,8).
pub(super) const EVENT: [f64; 18] = [
    99.0, 98.0, 96.0, 94.0, 93.0, 93.0, 93.0, 93.0, 93.0, 93.0, 95.0, 97.0, 99.0, 100.0, 100.0,
    100.0, 100.0, 100.0,
];

/// Данные одного сценария: ряд Y по закрытиям; open минуты = close прежней, на дыре open нет; X плоская.
struct Case {
    ly: Vec<f64>,
    lx: Vec<f64>,
    oy: Vec<f64>,
    cx: Vec<f64>,
}

impl Case {
    fn new(cy: &[f64]) -> Case {
        let n = cy.len();
        let mut oy: Vec<f64> = (0..n).map(|t| cy[t.saturating_sub(1)]).collect();
        for t in 0..n {
            if !cy[t].is_finite() {
                oy[t] = f64::NAN;
            }
        }
        let cx = vec![100.0; n];
        Case {
            ly: lns(cy),
            lx: lns(&cx),
            oy,
            cx,
        }
    }

    /// Вход пары (минута сетки 0 — индекс ряда) со ставками ноги Y; вызов `f` на нём.
    fn with<R>(&self, fund_y: &[(i64, f64)], f: impl FnOnce(&PairInput) -> R) -> R {
        let ox: [&[f64]; 1] = [&self.cx];
        f(&PairInput {
            first_min: 0,
            ly: &self.ly,
            lx: &self.lx,
            oy: &self.oy,
            ox: &ox,
            wx: &[1.0],
            fund_y,
            fund_x: &[],
        })
    }
}

fn cell(
    entry_z: f64,
    exit_z: f64,
    stop_z: f64,
    max_hold: usize,
    min_obs: usize,
    rearm: bool,
) -> Params {
    Params {
        window: 24,
        min_obs,
        beta: BetaMode::Fixed(1.0),
        entry_z,
        exit_z,
        stop_z,
        max_hold,
        refresh: 1,
        y_kind: LegKind::Perp,
        x_kind: LegKind::Perp,
        taker_in: true,
        taker_out: true,
        delay_x: 0,
        rearm,
        entry_from: None,
        rule: Rule::Z,
    }
}

type Key = (i8, i64, i64, i64, i64, ExitReason);

fn keys(tr: &[Trade]) -> Vec<Key> {
    tr.iter()
        .map(|t| (t.dir, t.sig_in, t.fill_in, t.sig_out, t.fill_out, t.reason))
        .collect()
}

/// Ядро, референс и многоклеточный проход: те же сделки (минуты, причина) и счётчики; возвращает сделки и счётчики ядра.
fn all_three(c: &Case, p: &Params) -> (Vec<Trade>, Counters) {
    c.with(&[], |inp| {
        let mut k: Vec<Trade> = Vec::new();
        let ck = run_pair(inp, p, &mut k);
        let mut r: Vec<Trade> = Vec::new();
        let cr = reference::run_pair(inp, p, &mut r);
        let mut m = [Vec::<Trade>::new()];
        let cm = run_pair_multi(inp, std::slice::from_ref(p), &mut m).unwrap();
        assert_eq!(keys(&k), keys(&r), "ядро против референса");
        assert_eq!(k, m[0], "ядро против многоклеточного прохода");
        assert_eq!(ck, cm[0], "счётчики: ядро против многоклеточного прохода");
        let (mut a, mut b) = (ck, cr);
        a.sum_net_bps = 0.0;
        b.sum_net_bps = 0.0;
        assert_eq!(a, b, "счётчики: ядро против референса");
        (k, ck)
    })
}

/// Событие дважды: минуты 30…47 и 68…85 (между ними флэт 100), всего 86 минут.
pub(super) fn two_events() -> Vec<f64> {
    let mut cy = vec![100.0; 30];
    cy.extend(EVENT);
    cy.extend([100.0; 20]);
    cy.extend(EVENT);
    cy
}

/// Ряд с дырами: флэт 100, дыра на 40, рампа −0,3 в минуту на 64…83 и флэт на ней до 99, вторая дыра на 70 (позиция
/// уже открыта). 100 минут.
pub(super) fn hole_case() -> Vec<f64> {
    let mut cy = vec![100.0; 100];
    for (i, v) in cy.iter_mut().enumerate().take(84).skip(64) {
        *v = 100.0 - 0.3 * (i as f64 - 63.0);
    }
    let last = cy[83];
    for v in &mut cy[84..] {
        *v = last;
    }
    cy[40] = f64::NAN;
    cy[70] = f64::NAN;
    cy
}

#[test]
fn stop_without_rearm_reenters_at_once_with_rearm_waits_for_the_band() {
    let cy = two_events();
    let c = Case::new(&cy);
    // стоп 3,5, вход 2, выход 0,2: на событии z = −4,8 / −4,3 / −4,2 / −3,8 / −3,2 / −2,6 / −2,2 / −1,9
    let off = cell(2.0, 0.2, 3.5, 0, 24, false);
    let (tr, cnt) = all_three(&c, &off);
    use ExitReason::{Rule as R, Stop as S};
    // без rearm: стоп на сигнале 31 → выход 32, и тут же вход снова (z всё ещё −4,2), так три сделки на событие
    assert_eq!(
        keys(&tr),
        vec![
            (1, 30, 31, 31, 32, S),
            (1, 32, 33, 33, 34, S),
            (1, 34, 35, 41, 42, R),
            (1, 68, 69, 69, 70, S),
            (1, 70, 71, 71, 72, S),
            (1, 72, 73, 79, 80, R),
        ]
    );
    assert_eq!((cnt.trades, cnt.entries, cnt.rearm_blocked), (6, 6, 0));

    // с rearm: после стопа вход в ту же сторону закрыт, пока |z| ≥ 2 (минуты 32…36); z вернулся внутрь полосы на
    // минуте 37 (−1,92) — блок снят, входа нет, потому что z уже выше −2; второе событие (минуты 70…74) — так же
    let on = Params { rearm: true, ..off };
    let (tr, cnt) = all_three(&c, &on);
    assert_eq!(
        keys(&tr),
        vec![(1, 30, 31, 31, 32, S), (1, 68, 69, 69, 70, S)]
    );
    // подавлено сигналов: по пять на событие (минуты 32…36 и 70…74)
    assert_eq!(cnt.rearm_blocked, 10);
    assert_eq!((cnt.trades, cnt.entries), (2, 2));
}

#[test]
fn mirrored_short_side_is_blocked_the_same_way() {
    // Y' = 1e4 / Y: z меняет знак, вход в шорт; блок на стороне −1
    let cy: Vec<f64> = two_events().iter().map(|v| 1e4 / v).collect();
    let c = Case::new(&cy);
    let off = cell(2.0, 0.2, 3.5, 0, 24, false);
    let on = Params { rearm: true, ..off };
    use ExitReason::Stop as S;
    let (tr_off, _) = all_three(&c, &off);
    assert_eq!(tr_off.len(), 6);
    assert!(tr_off.iter().all(|t| t.dir == -1));
    let (tr, cnt) = all_three(&c, &on);
    assert_eq!(
        keys(&tr),
        vec![(-1, 30, 31, 31, 32, S), (-1, 68, 69, 69, 70, S)]
    );
    assert_eq!(cnt.rearm_blocked, 10);
}

#[test]
fn timeout_without_rearm_reenters_at_once_with_rearm_waits() {
    // рампа −0,3 в минуту 40 минут: z держится около −1,68; вход 1,5, срок 10 минут, стопа нет
    let mut cy = vec![100.0; 30];
    cy.extend((0..40).map(|i| 100.0 - 0.3 * f64::from(i + 1)));
    cy.extend([88.0; 20]);
    let c = Case::new(&cy);
    let off = cell(1.5, 0.0, f64::INFINITY, 10, 24, false);
    let on = Params { rearm: true, ..off };
    use ExitReason::Timeout as T;
    let (tr, cnt) = all_three(&c, &off);
    assert_eq!(
        keys(&tr),
        vec![
            (1, 30, 31, 41, 42, T),
            (1, 42, 43, 53, 54, T),
            (1, 54, 55, 65, 66, T),
            (1, 66, 67, 77, 78, T),
        ]
    );
    assert_eq!(cnt.rearm_blocked, 0);
    let (tr, cnt) = all_three(&c, &on);
    assert_eq!(keys(&tr), vec![(1, 30, 31, 41, 42, T)]);
    assert_eq!((cnt.entries, cnt.rearm_blocked), (1, 29));
}

#[test]
fn rule_exit_blocks_nothing() {
    // выход по возврату (rule) не закрывает сторону: те же три сделки с флагом и без, когда стопа нет
    let cy = two_events();
    let c = Case::new(&cy);
    let off = cell(2.0, 0.2, f64::INFINITY, 0, 24, false);
    let on = Params { rearm: true, ..off };
    let (a, _) = all_three(&c, &off);
    let (b, cb) = all_three(&c, &on);
    assert_eq!(keys(&a), keys(&b));
    assert_eq!(cb.rearm_blocked, 0);
    assert!(a.iter().all(|t| t.reason == ExitReason::Rule));
}

#[test]
fn rearm_is_ignored_with_funding_rule() {
    // правило funding: полосы z нет, флаг не действует (ядро, run_pair выбирает мономорфизацию без REARM)
    let cy = two_events();
    let c = Case::new(&cy);
    let fund: Vec<(i64, f64)> = (0..cy.len() as i64)
        .filter(|m| m % 8 == 3)
        .map(|m| (m, 1e-3))
        .collect();
    let mk = |rearm: bool| Params {
        rule: Rule::Funding {
            k: 2,
            hold_payments: 3,
        },
        rearm,
        ..cell(2.0, 0.2, 3.5, 20, 24, false)
    };
    c.with(&fund, |inp| {
        let (mut a, mut b): (Vec<Trade>, Vec<Trade>) = (Vec::new(), Vec::new());
        let ca = run_pair(inp, &mk(false), &mut a);
        let cb = run_pair(inp, &mk(true), &mut b);
        assert!(!a.is_empty(), "тест пуст");
        assert_eq!(a, b);
        assert_eq!(ca, cb);
        assert_eq!(cb.rearm_blocked, 0);
    });
}

#[test]
fn hole_inside_position_and_incomplete_window_are_counted() {
    // Y = 100, дыра на минуте 40 (флэт), рампа с минуты 64 по 83 (−0,3 в минуту), дальше флэт; вторая дыра на 70,
    // когда позиция уже открыта (сигнал 64, исполнение 65). min_obs = окно = 24: после дыры 23 минуты z не определён.
    let cy = hole_case();
    let c = Case::new(&cy);
    // выход по возврату z ≥ 0 так и не наступает; срока нет: позиция открыта на конце
    let p = cell(1.5, 0.0, f64::INFINITY, 0, 24, false);
    let (tr, cnt) = all_three(&c, &p);
    assert!(tr.is_empty());
    assert_eq!(
        (cnt.entries, cnt.open_at_end, cnt.minutes_in_pos),
        (1, 1, 35)
    );
    // знаменатель: минуты с t + 1 ≥ 24 (77); z определён на 64…69 и 94…99 (12), остальное — флэт или окно с дырой
    assert_eq!((cnt.eval_minutes, cnt.z_def_minutes), (77, 12));
    // позиция удержана с z = NaN на 70…93 (24 минуты: дыра и 23 минуты неполного окна)
    assert_eq!(cnt.pos_z_nan_minutes, 24);
    // без права входа: свеча есть, окно с дырой 40, t = 41…63 (23 минуты), позиции и ордера нет
    assert_eq!(cnt.noentry_window_minutes, 23);
    assert_eq!(cnt.gap_minutes, 2);

    // min_obs 23 допускает одну дыру в окне: счётчики окна падают до самой дыры
    let p23 = cell(1.5, 0.0, f64::INFINITY, 0, 23, false);
    let (_, c23) = all_three(&c, &p23);
    assert_eq!(c23.noentry_window_minutes, 0);
    assert_eq!(c23.pos_z_nan_minutes, 1, "только минута самой дыры 70");
    assert_eq!(c23.z_def_minutes, 35);
}

#[test]
fn counters_agree_on_gappy_random_pairs_with_and_without_rearm() {
    let mut r = Lcg(5);
    let x = walk(N, &mut r, 1e-3, 4.0);
    let y = ou_on(&x, 0.8, &mut r, 2e-3);
    let (ox, cx) = with_gaps(&x, &mut r, None);
    let (oy, cy) = with_gaps(&y, &mut r, Some((3000, 3040)));
    let (lx, ly) = (lns(&cx), lns(&cy));
    let inp = PairInput {
        first_min: 777,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &[&ox],
        wx: &[1.0],
        fund_y: &[],
        fund_x: &[],
    };
    // min_obs близко к окну: ~4 % дыр по ряду, окно (240) часто неполное — счётчики окна не нули
    let base = Params {
        min_obs: 228,
        stop_z: 3.0,
        max_hold: 120,
        ..params(BetaMode::Ols, LegKind::Perp, LegKind::Perp)
    };
    let (t_off, c_off) = both(&inp, &base);
    let on = Params {
        rearm: true,
        ..base
    };
    let (t_on, c_on) = both(&inp, &on);
    // выходы по причинам: на этом ряде есть и стоп, и срок, и возврат
    for r in [ExitReason::Rule, ExitReason::Stop, ExitReason::Timeout] {
        assert!(t_off.iter().any(|t| t.reason == r), "нет выходов {r:?}");
    }
    assert_eq!(c_off.rearm_blocked, 0);
    assert!(c_on.rearm_blocked > 0);
    assert!(t_on.len() < t_off.len(), "rearm убирает повторные входы");
    // счётчики F-01 на ряде с пропусками: окно неполное, z бывает не определён внутри позиции
    for c in [&c_off, &c_on] {
        assert!(c.eval_minutes > 0 && c.z_def_minutes <= c.eval_minutes);
        assert!(
            c.noentry_window_minutes > 0,
            "дыры дают минуты без права входа"
        );
        assert!(c.pos_z_nan_minutes <= c.minutes_in_pos);
    }
    // β на входе у всех сделок конечна (Ols) и совпадает по знаку с номиналом ноги X: qty_x = −d·β·w/ox
    assert!(t_off.iter().chain(&t_on).all(|t| t.beta.is_finite()));

    // многоклеточный проход той же группы: клетки с rearm и без, побитно как одиночные проходы
    let cells = [
        base,
        on,
        Params { stop_z: 2.5, ..on },
        Params {
            entry_z: 1.5,
            ..base
        },
    ];
    let mut sinks: Vec<Vec<Trade>> = cells.iter().map(|_| Vec::new()).collect();
    let cs = run_pair_multi(&inp, &cells, &mut sinks).unwrap();
    for ((p, got), cm) in cells.iter().zip(&sinks).zip(&cs) {
        let mut want: Vec<Trade> = Vec::new();
        let cw = run_pair(&inp, p, &mut want);
        assert_eq!(*got, want, "сделки клетки {p:?}");
        assert_eq!(*cm, cw, "счётчики клетки {p:?}");
    }
}
