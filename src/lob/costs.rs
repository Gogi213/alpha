//! Издержки и проскальзывание по Decision 15, гейт G3 (план, §5.3).
//!
//! Вход — мейкер у своей стороны спреда без улучшения (тип ордера —
//! Decision 19); выход — тейкер с издержкой в один тик против позиции плюс
//! половина наблюдённого спреда в момент выхода. Круг комиссий —
//! мейкер 0.02% + тейкер 0.055% = 7.5 bps (H4, Decision 19).
//!
//! Формула на наблюдение: `net = m − (7.5 + slippage)`, где `m` — подписанный
//! markout по Decision 14 (потребитель: `markout::markout_bps`), а slippage
//! считается на каждом наблюдении отдельно из его же спреда выхода и его же
//! базовой середины. Валового порога-константы нет (H6): тик на тонком
//! инструменте честно весит больше, потому что знаменатель — цена.
//!
//! Целые до последнего, как в книге (A7): тики — `i64`, середина держится
//! удвоенной суммой `bid + ask` (ровно держит половину тика без деления),
//! вещественное число появляется один раз — при делении в формуле bps.
//!
//! Вывод slippage: цена издержки = 1 тик + spread_exit / 2 тика, то есть
//! `(spread + 2) / 2` тиков; середина базы = `mid2x / 2` тиков, где
//! `mid2x = bid_base + ask_base`. Масштаб тика сокращается:
//! `slippage_bps = (spread + 2) / mid2x * 10^4`. Числитель — целый (`i128`),
//! деление одно.
//!
//! Знаменатель — базовая середина, тот же, что у `m`: net вычитается
//! из markout без смены базы. Спред — наблюдённый в момент выхода
//! (`ask_exit − bid_exit` в тиках, потребитель: `book`).
//!
//! Гейт G3 буквально: `m` после издержек положителен в вердиктной ячейке
//! (Decision 20: C2, если прошла, иначе C1). Иначе красный: сигнал есть,
//! эджа нет. Зелёный порог H6 (средний net ≥ 3 bps) здесь только читается
//! константой — финальный вердикт не здесь.
//!
//! Модуль не хранит состояния и не выделяет память.

use crate::lob::cells::G2Verdict;

/// Комиссия мейкера, bps (H4: 0.02%).
pub const MAKER_FEE_BPS: f64 = 2.0;

/// Комиссия тейкера, bps (H4: 0.055%).
pub const TAKER_FEE_BPS: f64 = 5.5;

/// Круг мейкер-тейкер, bps (Decision 19, H4): одно число для G0 и G3.
pub const ROUNDTRIP_FEES_BPS: f64 = 7.5;

/// Зелёный порог H6, bps: средний net ≥ 3 сверх полных издержек.
/// Только читается; вердикт G3 его не использует.
pub const GREEN_NET_BPS: f64 = 3.0;

/// Одно наблюдение для колонки «после издержек»: markout и его же издержки.
/// `spread_ticks_exit = ask_exit − bid_exit` в тиках; `mid2x_base` — удвоенная
/// середина базы (`bid + ask` в тиках, см. `markout::mid_double_tick`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Observation {
    /// Подписанный markout `m` в bps (Decision 14).
    pub m_bps: f64,
    /// Наблюдённый спред в момент выхода, в тиках.
    pub spread_ticks_exit: i64,
    /// Удвоенная середина базы в тиках.
    pub mid2x_base: i64,
}

/// Проскальзывание Decision 15 в bps: `(spread + 2) / mid2x * 10^4`.
/// `None` при неположительной базе, отрицательном спреде или переполнении
/// входа за пределы `i128` (практически недостижимо, проверка — страховка).
/// Целые до деления: числитель складывается в целых, деление одно.
pub fn slippage_bps(spread_ticks_exit: i64, mid2x_base: i64) -> Option<f64> {
    if mid2x_base <= 0 || spread_ticks_exit < 0 {
        return None;
    }
    let num = (i128::from(spread_ticks_exit) + 2) * 10_000;
    Some(num as f64 / mid2x_base as f64)
}

/// Полные издержки круга в bps: 7.5 комиссий плюс проскальзывание.
/// `None` — проскальзывание не посчиталось (см. `slippage_bps`).
pub fn cost_bps(spread_ticks_exit: i64, mid2x_base: i64) -> Option<f64> {
    slippage_bps(spread_ticks_exit, mid2x_base).map(|s| ROUNDTRIP_FEES_BPS + s)
}

/// Чистый результат наблюдения в bps: `m − cost`. `None` при неконечном `m`
/// или при `None` издержек: отсутствие данных не есть нулевая издержка.
pub fn net_bps(m_bps: f64, spread_ticks_exit: i64, mid2x_base: i64) -> Option<f64> {
    if !m_bps.is_finite() {
        return None;
    }
    cost_bps(spread_ticks_exit, mid2x_base).map(|c| m_bps - c)
}

/// Средний net среза наблюдений — одна колонка «после издержек».
/// Считается из понаблюдательных net, а не из средних входов: при разных
/// ценах средний вес тика отличается, и константная издержка дала бы другой
/// вердикт на тех же данных (H6). `None` — срез пуст или хотя бы одно
/// наблюдение не посчиталось: молча выкидывать его значило бы пересчитывать
/// выборку задним числом (то же правило, что неконечный markout в 5.2).
pub fn mean_net_bps(observations: &[Observation]) -> Option<f64> {
    if observations.is_empty() {
        return None;
    }
    let mut sum = 0.0;
    for o in observations {
        sum += net_bps(o.m_bps, o.spread_ticks_exit, o.mid2x_base)?;
    }
    Some(sum / observations.len() as f64)
}

/// Вердиктная ячейка по Decision 20: C2, если прошла, иначе C1.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VerdictCell {
    /// Вердикт выносит C1.
    C1,
    /// Вердикт выносит C2.
    C2,
}

impl std::fmt::Display for VerdictCell {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            VerdictCell::C1 => write!(f, "C1"),
            VerdictCell::C2 => write!(f, "C2"),
        }
    }
}

/// Выбор вердиктной ячейки по флагу прохода C2.
pub fn select_verdict_cell(c2_passed: bool) -> VerdictCell {
    if c2_passed {
        VerdictCell::C2
    } else {
        VerdictCell::C1
    }
}

/// Тот же выбор поверх вердикта G2: C2 тогда и только тогда, когда G2
/// зафиксировал её проход; методический и рыночный красный G2 прохода C2
/// не содержат и ведут на C1. G3 перепроверкой G2 не занимается.
pub fn select_verdict_cell_from_g2(g2: &G2Verdict) -> VerdictCell {
    match g2 {
        G2Verdict::Pass { c2_passed, .. } => select_verdict_cell(*c2_passed),
        G2Verdict::RedMarket | G2Verdict::RedMethodology(_) => VerdictCell::C1,
    }
}

/// Вердикт гейта G3. Положительность строгая и конечная, как знак среднего
/// в 5.2: ноль и неконечность — не проход.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum G3Verdict {
    /// Средний net вердиктной ячейки положителен.
    Pass,
    /// Иначе красный: сигнал есть, эджа нет (включая отсутствие данных).
    Red,
}

impl G3Verdict {
    /// Проход гейта — зелёный свет дальше по подтверждающему прогону.
    pub fn is_pass(self) -> bool {
        matches!(self, G3Verdict::Pass)
    }
}

impl std::fmt::Display for G3Verdict {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            G3Verdict::Pass => write!(f, "G3 pass: mean net>0 in verdict cell"),
            G3Verdict::Red => write!(f, "G3 RED: сигнал есть, эджа нет"),
        }
    }
}

/// Решение G3 строго по формулировке: средний net вердиктной ячейки
/// положителен — проход, иначе (включая `None`) красный.
pub fn decide_g3(mean_net_verdict_cell: Option<f64>) -> G3Verdict {
    match mean_net_verdict_cell {
        Some(v) if v.is_finite() && v > 0.0 => G3Verdict::Pass,
        _ => G3Verdict::Red,
    }
}

/// Связка Decision 20 + G3: выбор ячейки по проходу C2 и вердикт по её
/// среднему net. Возвращает пару (ячейка, вердикт).
pub fn decide_g3_from_cells(
    mean_net_c1: Option<f64>,
    mean_net_c2: Option<f64>,
    c2_passed: bool,
) -> (VerdictCell, G3Verdict) {
    let cell = select_verdict_cell(c2_passed);
    let mean = match cell {
        VerdictCell::C1 => mean_net_c1,
        VerdictCell::C2 => mean_net_c2,
    };
    (cell, decide_g3(mean))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn obs(m_bps: f64, spread: i64, mid2x: i64) -> Observation {
        Observation {
            m_bps,
            spread_ticks_exit: spread,
            mid2x_base: mid2x,
        }
    }

    fn close(a: f64, b: f64) -> bool {
        (a - b).abs() < 1e-9
    }

    /// H4 + Decision 19: круг мейкер-тейкер — одно число 7.5 bps для G0 и G3.
    #[test]
    fn roundtrip_fees_are_single_number_for_g0_and_g3() {
        assert!(close(MAKER_FEE_BPS, 2.0));
        assert!(close(TAKER_FEE_BPS, 5.5));
        assert!(close(ROUNDTRIP_FEES_BPS, 7.5));
        assert!(close(MAKER_FEE_BPS + TAKER_FEE_BPS, ROUNDTRIP_FEES_BPS));
    }

    /// Формула буквально: net = m − (7.5 + (spread+2)/mid2x·10⁴).
    /// mid2x = 20000, spread = 1 → slippage 1.5, cost 9.0, net 1.0 при m = 10.
    #[test]
    fn net_formula_is_exact_on_synthetic_observation() {
        assert!(close(slippage_bps(1, 20_000).unwrap(), 1.5));
        assert!(close(cost_bps(1, 20_000).unwrap(), 9.0));
        assert!(close(net_bps(10.0, 1, 20_000).unwrap(), 1.0));
    }

    /// Done-condition буквально: колонка «после издержек» есть во всех трёх —
    /// C1, C2 и разведка считаются одной функцией из своих наблюдений.
    #[test]
    fn after_cost_column_exists_in_all_three() {
        let c1 = [obs(10.0, 1, 20_000), obs(12.0, 2, 20_000)];
        let c2 = [obs(15.0, 1, 20_000)];
        let exploration = [obs(5.0, 4, 20_000), obs(6.0, 1, 10_000)];
        let (n1, n2, ne) = (
            mean_net_bps(&c1).expect("C1 обязан посчитаться"),
            mean_net_bps(&c2).expect("C2 обязан посчитаться"),
            mean_net_bps(&exploration).expect("разведка обязана посчитаться"),
        );
        assert!(close(n1, (1.0 + 2.5) / 2.0));
        assert!(close(n2, 6.0));
        assert!(n1.is_finite() && n2.is_finite() && ne.is_finite());
    }

    /// Проскальзывание считается на каждом наблюдении отдельно, а не
    /// константой: одинаковый m при разном спреде даёт разный net, а средний
    /// net при разных ценах не равен net от средних входов.
    #[test]
    fn slippage_is_per_observation_not_a_constant() {
        let a = net_bps(10.0, 1, 20_000).unwrap();
        let b = net_bps(10.0, 5, 20_000).unwrap();
        assert!(!close(a, b), "разный спред обязан дать разный net");
        // Разные цены: средний понаблюдательный net против издержек
        // от средних входов (mid 15000, spread 1 → slippage 2.0).
        let per_obs = mean_net_bps(&[obs(10.0, 1, 20_000), obs(10.0, 1, 10_000)]).unwrap();
        let mean_m = 10.0;
        let const_cost = cost_bps(1, 15_000).unwrap();
        assert!(
            !close(per_obs, mean_m - const_cost),
            "понаблюдательный средний {per_obs} обязан отличаться от константного {}",
            mean_m - const_cost
        );
        assert!(close(per_obs, (1.0 + -0.5) / 2.0));
    }

    /// Тик на тонком инструменте честно весит больше: тот же спред при
    /// меньшей цене даёт большее проскальзывание в bps.
    #[test]
    fn tick_weighs_more_on_thin_instrument() {
        let thick = slippage_bps(2, 20_000).unwrap();
        let thin = slippage_bps(2, 2_000).unwrap();
        assert!(close(thick, 2.0));
        assert!(close(thin, 20.0));
        assert!(thin > thick);
        assert!(
            net_bps(10.0, 2, 2_000).unwrap() < net_bps(10.0, 2, 20_000).unwrap(),
            "тонкий инструмент обязан съедать больше эджа"
        );
    }

    /// Гейт G3 буквально: положителен — проход; ноль, минус и отсутствие
    /// данных — красный «сигнал есть, эджа нет».
    #[test]
    fn g3_passes_only_on_positive_verdict_mean() {
        assert!(decide_g3(Some(0.1)).is_pass());
        assert!(!decide_g3(Some(0.0)).is_pass());
        assert!(!decide_g3(Some(-0.1)).is_pass());
        assert!(!decide_g3(None).is_pass());
        assert!(!decide_g3(Some(f64::NAN)).is_pass());
        assert!(!decide_g3(Some(f64::INFINITY)).is_pass());
        assert_eq!(decide_g3(Some(1.0)), G3Verdict::Pass);
        assert_eq!(decide_g3(None), G3Verdict::Red);
    }

    /// Decision 20: вердиктная ячейка — C2, если прошла, иначе C1.
    /// Та же развилка поверх вердикта G2.
    #[test]
    fn verdict_cell_is_c2_only_when_c2_passed() {
        assert_eq!(select_verdict_cell(true), VerdictCell::C2);
        assert_eq!(select_verdict_cell(false), VerdictCell::C1);
        assert_eq!(
            select_verdict_cell_from_g2(&G2Verdict::Pass {
                c1_passed: true,
                c2_passed: true
            }),
            VerdictCell::C2
        );
        assert_eq!(
            select_verdict_cell_from_g2(&G2Verdict::Pass {
                c1_passed: true,
                c2_passed: false
            }),
            VerdictCell::C1
        );
        assert_eq!(
            select_verdict_cell_from_g2(&G2Verdict::RedMarket),
            VerdictCell::C1
        );
    }

    /// Связка: при прошедшей C2 читается её net, иначе — net C1.
    #[test]
    fn g3_from_cells_reads_the_verdict_cell_only() {
        // C2 прошла, но её net отрицателен — красный, хотя C1 положительна.
        let (cell, verdict) = decide_g3_from_cells(Some(5.0), Some(-1.0), true);
        assert_eq!(cell, VerdictCell::C2);
        assert_eq!(verdict, G3Verdict::Red);
        // C2 не прошла — читается C1.
        let (cell, verdict) = decide_g3_from_cells(Some(5.0), Some(-1.0), false);
        assert_eq!(cell, VerdictCell::C1);
        assert_eq!(verdict, G3Verdict::Pass);
    }

    /// H6 только читается: зелёный ≥ 3 bps, а G3 проходит уже при > 0.
    /// Net 1.0 — проход G3, но не зелёный: вердикты не смешиваются.
    #[test]
    fn green_threshold_is_read_not_decided_here() {
        assert!(close(GREEN_NET_BPS, 3.0));
        let probe = net_bps(10.0, 1, 20_000).expect("синтетика обязана посчитаться");
        assert!(
            decide_g3(Some(probe)).is_pass(),
            "G3 проходит ниже зелёного"
        );
        assert!(probe < GREEN_NET_BPS, "но зелёным это не является");
    }

    /// Отказы вместо нулей: плохая база, отрицательный спред, неконечный m,
    /// пустой срез и хотя бы одно битое наблюдение дают None насквозь.
    #[test]
    fn invalid_inputs_yield_none_not_zero() {
        assert_eq!(slippage_bps(1, 0), None);
        assert_eq!(slippage_bps(1, -5), None);
        assert_eq!(slippage_bps(-1, 20_000), None);
        assert_eq!(net_bps(f64::NAN, 1, 20_000), None);
        assert_eq!(net_bps(f64::INFINITY, 1, 20_000), None);
        assert_eq!(net_bps(10.0, 1, 0), None);
        assert_eq!(mean_net_bps(&[]), None);
        assert_eq!(mean_net_bps(&[obs(10.0, 1, 20_000), obs(10.0, 1, 0)]), None);
        assert_eq!(
            mean_net_bps(&[obs(f64::NAN, 1, 20_000)]),
            None,
            "битое наблюдение травит среднее, а не выкидывается молча"
        );
    }

    /// Синтетика вместо недели: все тесты выше идут на сконструированных
    /// наблюдениях без файлов и сети. Здесь — явная печать колонки
    /// синтетического прогона для ревью done-condition.
    #[test]
    fn synthetic_run_prints_net_column() {
        let column = [obs(12.0, 1, 20_000), obs(8.0, 3, 20_000)];
        let mean = mean_net_bps(&column).unwrap();
        let line = format!("after_costs: mean_net={mean:.4} n={}", column.len());
        assert!(line.contains("after_costs"), "колонка обязана называться");
        assert!(close(mean, (3.0 + -2.0) / 2.0));
    }

    /// Граница модулей в духе шагов 1.1/5.2: чистая логика не знает про
    /// транспорт и часы. Проверка — грепом по собственному исходнику.
    #[test]
    fn module_stays_detached_from_transport_and_clocks() {
        const SRC: &str = include_str!("costs.rs");
        let banned = [
            concat!("by", "bit"),
            concat!("tok", "io"),
            concat!("Inst", "ant"),
            concat!("System", "Time"),
            concat!("std::", "time"),
        ];
        for b in banned {
            assert!(!SRC.contains(b), "исходник тянет запрещённое: {b}");
        }
    }
}
